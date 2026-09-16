"""The mock analyzer and the analyzer registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aetherfx.ai import MockAnalyzer, ReferenceAnalyzer, Segment, get_analyzer, media_type_for
from aetherfx.ai.base import read_image_bytes
from aetherfx.ai.registry import ANALYZER_ENV_VAR, available_analyzers
from aetherfx.ead import EffectAnalysisDocument, RenderEvaluation

from conftest import EXAMPLES_DIR


@pytest.fixture
def analyzer() -> MockAnalyzer:
    return MockAnalyzer()


class TestMockAnalyzer:
    def test_is_a_reference_analyzer(self, analyzer: MockAnalyzer) -> None:
        assert isinstance(analyzer, ReferenceAnalyzer)
        assert analyzer.name == "mock"

    def test_analyze_returns_a_valid_ead(self, analyzer: MockAnalyzer) -> None:
        document = analyzer.analyze_reference("refs/fire.png", prompt="bigger")
        assert isinstance(document, EffectAnalysisDocument)
        assert document.source.path == "refs/fire.png"
        assert document.source.prompt == "bigger"
        EffectAnalysisDocument.model_validate(document.to_json_dict())

    def test_is_deterministic(self, analyzer: MockAnalyzer) -> None:
        first = analyzer.analyze_reference("a.png").to_json_dict()
        second = MockAnalyzer().analyze_reference("a.png").to_json_dict()
        assert first == second

    def test_accepts_raw_bytes(self, analyzer: MockAnalyzer) -> None:
        document = analyzer.analyze_reference(b"\x89PNG\r\n\x1a\nnot really a png")
        assert document.source.path is None

    def test_layers_mirror_the_fire_aoe_example(self, analyzer: MockAnalyzer) -> None:
        example = json.loads((EXAMPLES_DIR / "fire_aoe.json").read_text(encoding="utf-8"))
        example_layers = [layer["id"] for layer in example["layers"]]
        document = analyzer.analyze_reference("fire_aoe.png")
        assert [layer.id for layer in document.layers] == example_layers
        assert [entry.layer for entry in document.implementation_plan] == example_layers

    def test_every_layer_has_an_implementation_plan_entry(self, analyzer: MockAnalyzer) -> None:
        document = analyzer.analyze_reference("fire_aoe.png")
        planned = {entry.layer for entry in document.implementation_plan}
        assert {layer.id for layer in document.layers} == planned

    def test_motion_and_timing_are_marked_inferred(self, analyzer: MockAnalyzer) -> None:
        document = analyzer.analyze_reference("fire_aoe.png")
        assert document.temporal_hypothesis.inferred is True
        for layer in document.layers:
            if layer.motion_hypothesis is not None:
                assert layer.motion_hypothesis.inferred is True

    def test_timeline_matches_the_probable_duration(self, analyzer: MockAnalyzer) -> None:
        document = analyzer.analyze_reference("fire_aoe.png")
        last = document.temporal_hypothesis.phases[-1]
        assert last.end == pytest.approx(document.global_analysis.probable_duration_s)

    def test_plan_uses_only_vocabulary_node_types(self, analyzer: MockAnalyzer) -> None:
        from aetherfx.ead import NODE_TYPES

        document = analyzer.analyze_reference("fire_aoe.png")
        for entry in document.implementation_plan:
            for node in entry.nodes:
                assert node.type in NODE_TYPES

    def test_segments_are_boxes_without_masks(self, analyzer: MockAnalyzer) -> None:
        segments = analyzer.segment_reference("fire_aoe.png")
        assert segments and all(isinstance(item, Segment) for item in segments)
        assert all(item.mask is None and item.has_mask() is False for item in segments)
        for item in segments:
            assert len(item.bbox) == 4

    def test_describe_effect_returns_prose(self, analyzer: MockAnalyzer) -> None:
        text = analyzer.describe_effect("fire_aoe.png")
        assert isinstance(text, str) and len(text) > 80 and not text.startswith("{")

    def test_evaluate_render_returns_actionable_suggestions(self, analyzer: MockAnalyzer) -> None:
        evaluation = analyzer.evaluate_render("ref.png", "render.png")
        assert isinstance(evaluation, RenderEvaluation)
        assert 0.0 <= evaluation.score <= 1.0
        assert evaluation.suggestions
        for suggestion in evaluation.suggestions:
            assert suggestion.parameter and suggestion.reason


class TestImageHelpers:
    def test_media_type_by_extension(self) -> None:
        assert media_type_for("a.png") == "image/png"
        assert media_type_for("a.JPG") == "image/jpeg"
        assert media_type_for("a.webp") == "image/webp"
        assert media_type_for("a.tga") == "image/png"

    def test_media_type_by_magic_number(self) -> None:
        assert media_type_for(b"\x89PNG\r\n\x1a\n rest") == "image/png"
        assert media_type_for(b"\xff\xd8\xff rest") == "image/jpeg"
        assert media_type_for(b"nonsense") == "image/png"

    def test_read_image_bytes_from_path_and_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "x.bin"
        path.write_bytes(b"hello")
        assert read_image_bytes(path) == b"hello"
        assert read_image_bytes(b"hello") == b"hello"


class TestRegistry:
    def test_default_is_the_mock_analyzer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ANALYZER_ENV_VAR, raising=False)
        assert isinstance(get_analyzer(), MockAnalyzer)

    def test_environment_variable_selects_the_analyzer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ANALYZER_ENV_VAR, "mock")
        assert get_analyzer().name == "mock"

    def test_explicit_name_wins_over_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ANALYZER_ENV_VAR, "claude")
        assert get_analyzer("mock").name == "mock"

    def test_claude_is_registered(self) -> None:
        assert available_analyzers() == ["claude", "mock"]

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown analyzer"):
            get_analyzer("telepathy")
