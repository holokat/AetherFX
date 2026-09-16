"""The Claude adapter, driven by a stub client - no network, no API key."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from aetherfx.ai.claude import API_KEY_ENV_VAR, DEFAULT_MODEL, MODEL_ENV_VAR, AnalyzerError, ClaudeAnalyzer
from aetherfx.ai.mock import canned_fire_aoe_ead, canned_render_evaluation
from aetherfx.ead import EffectAnalysisDocument, RenderEvaluation


class StubBlock:
    def __init__(self, text: str, kind: str = "text") -> None:
        self.type = kind
        self.text = text


class StubResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [StubBlock(text)] if text is not None else []
        self.stop_reason = stop_reason
        self.stop_details = None


class StubMessages:
    """Records every request and replays a scripted list of replies."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if not self.replies:  # pragma: no cover - a test script ran dry
            raise AssertionError("the stub ran out of scripted replies")
        reply = self.replies.pop(0)
        return reply if isinstance(reply, StubResponse) else StubResponse(reply)


class StubClient:
    def __init__(self, *replies: Any) -> None:
        self.messages = StubMessages(list(replies))


@pytest.fixture
def image(make_image: Callable[..., Path]) -> Path:
    return make_image("ref.png")


def ead_text() -> str:
    return json.dumps(canned_fire_aoe_ead("ignored.png").to_json_dict())


class TestConfiguration:
    def test_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
        assert ClaudeAnalyzer(client=StubClient()).model == DEFAULT_MODEL

    def test_model_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(MODEL_ENV_VAR, "claude-opus-5")
        assert ClaudeAnalyzer(client=StubClient()).model == "claude-opus-5"

    def test_constructor_model_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(MODEL_ENV_VAR, "claude-opus-5")
        assert ClaudeAnalyzer(model="claude-haiku-4-5", client=StubClient()).model == "claude-haiku-4-5"

    def test_missing_api_key_is_a_clear_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
        with pytest.raises(AnalyzerError, match=API_KEY_ENV_VAR):
            _ = ClaudeAnalyzer().client

    def test_supplied_client_needs_no_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
        stub = StubClient()
        assert ClaudeAnalyzer(client=stub).client is stub

    def test_package_imports_without_anthropic(self) -> None:
        """Importing the adapter must not import the SDK."""
        import subprocess
        import sys

        code = (
            "import sys; sys.modules['anthropic'] = None\n"
            "import aetherfx.ai.claude as m\n"
            "print(m.DEFAULT_MODEL)\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        assert out.stdout.strip() == DEFAULT_MODEL


class TestAnalyzeReference:
    def test_returns_a_validated_ead(self, image: Path) -> None:
        analyzer = ClaudeAnalyzer(client=StubClient(ead_text()))
        document = analyzer.analyze_reference(image, prompt="bigger")
        assert isinstance(document, EffectAnalysisDocument)
        assert document.source.path == str(image)
        assert document.source.prompt == "bigger"

    def test_sends_the_image_as_base64_with_the_right_media_type(self, image: Path) -> None:
        stub = StubClient(ead_text())
        ClaudeAnalyzer(client=stub).analyze_reference(image)
        content = stub.messages.requests[0]["messages"][0]["content"]
        block = content[0]
        assert block["type"] == "image"
        assert block["source"]["media_type"] == "image/png"
        assert base64.standard_b64decode(block["source"]["data"]) == image.read_bytes()

    def test_media_type_follows_the_extension(self, make_image: Callable[..., Path], tmp_path: Path) -> None:
        source = make_image("ref.png")
        jpeg = tmp_path / "ref.jpg"
        jpeg.write_bytes(source.read_bytes())
        stub = StubClient(ead_text())
        ClaudeAnalyzer(client=stub).analyze_reference(jpeg)
        assert stub.messages.requests[0]["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"

    def test_system_prompt_carries_the_schema_and_the_json_rule(self, image: Path) -> None:
        stub = StubClient(ead_text())
        ClaudeAnalyzer(client=stub).analyze_reference(image)
        system = stub.messages.requests[0]["system"]
        assert "Effect Analysis Document" in system
        assert "https://json-schema.org/draft/2020-12/schema" in system
        assert "single JSON object and nothing else" in system
        assert "particle_system" in system

    def test_prompt_is_passed_to_the_model(self, image: Path) -> None:
        stub = StubClient(ead_text())
        ClaudeAnalyzer(client=stub).analyze_reference(image, prompt="top-down ice AOE")
        text = stub.messages.requests[0]["messages"][0]["content"][-1]["text"]
        assert "top-down ice AOE" in text

    def test_markdown_fence_is_stripped(self, image: Path) -> None:
        fenced = f"```json\n{ead_text()}\n```"
        document = ClaudeAnalyzer(client=StubClient(fenced)).analyze_reference(image)
        assert document.global_analysis.effect_category == "fire_aoe"

    def test_surrounding_prose_is_stripped(self, image: Path) -> None:
        chatty = f"Here you go!\n{ead_text()}\nHope that helps."
        document = ClaudeAnalyzer(client=StubClient(chatty)).analyze_reference(image)
        assert document.global_analysis.effect_category == "fire_aoe"


class TestRetry:
    def test_invalid_json_is_repaired_once(self, image: Path) -> None:
        stub = StubClient("not json at all", ead_text())
        document = ClaudeAnalyzer(client=stub).analyze_reference(image)
        assert document.global_analysis.effect_category == "fire_aoe"
        assert len(stub.messages.requests) == 2

    def test_the_validation_error_is_fed_back(self, image: Path) -> None:
        broken = json.dumps({"source": {"kind": "image"}})
        stub = StubClient(broken, ead_text())
        ClaudeAnalyzer(client=stub).analyze_reference(image)
        repair = stub.messages.requests[1]["messages"]
        assert repair[-2]["role"] == "assistant"
        assert repair[-1]["role"] == "user"
        assert "did not validate" in repair[-1]["content"]
        assert "global" in repair[-1]["content"]

    def test_only_one_retry(self, image: Path) -> None:
        stub = StubClient("nope", "still nope")
        with pytest.raises(AnalyzerError, match="after one repair attempt"):
            ClaudeAnalyzer(client=stub).analyze_reference(image)
        assert len(stub.messages.requests) == 2

    def test_refusal_is_reported(self, image: Path) -> None:
        stub = StubClient(StubResponse("", stop_reason="refusal"))
        with pytest.raises(AnalyzerError, match="declined"):
            ClaudeAnalyzer(client=stub).analyze_reference(image)

    def test_empty_reply_is_reported(self, image: Path) -> None:
        stub = StubClient(StubResponse(""))
        with pytest.raises(AnalyzerError, match="no text content"):
            ClaudeAnalyzer(client=stub).analyze_reference(image)


class TestOtherMethods:
    def test_describe_effect_returns_prose(self, image: Path) -> None:
        stub = StubClient("A big ring of fire.")
        assert ClaudeAnalyzer(client=stub).describe_effect(image) == "A big ring of fire."
        assert "prose only" in stub.messages.requests[0]["system"]

    def test_segment_reference_returns_boxes(self, image: Path) -> None:
        segments = ClaudeAnalyzer(client=StubClient(ead_text())).segment_reference(image)
        assert segments and all(item.mask is None for item in segments)

    def test_evaluate_render_sends_both_images(self, make_image: Callable[..., Path]) -> None:
        reference = make_image("ref.png")
        render = make_image("render.png", centre=(160, 120))
        stub = StubClient(json.dumps(canned_render_evaluation().model_dump()))
        evaluation = ClaudeAnalyzer(client=stub).evaluate_render(reference, render)
        assert isinstance(evaluation, RenderEvaluation)
        content = stub.messages.requests[0]["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "image", "text"]
        assert base64.standard_b64decode(content[0]["source"]["data"]) == reference.read_bytes()
        assert base64.standard_b64decode(content[1]["source"]["data"]) == render.read_bytes()

    def test_evaluate_render_includes_the_ead_when_given(self, make_image: Callable[..., Path]) -> None:
        reference = make_image("ref.png")
        render = make_image("render.png")
        stub = StubClient(json.dumps(canned_render_evaluation().model_dump()))
        ClaudeAnalyzer(client=stub).evaluate_render(reference, render, ead=canned_fire_aoe_ead())
        text = stub.messages.requests[0]["messages"][0]["content"][-1]["text"]
        assert "outer flame wall" in text or "fire_aoe" in text
