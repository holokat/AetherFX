"""Claude vision adapter: reference images in, EADs and render verdicts out.

Uses the official ``anthropic`` Python SDK, imported lazily so ``aetherfx``
installs and runs without it (``pip install 'aetherfx[claude]'`` adds it).

How it works:

* The image is sent as a base64 ``image`` content block, with the media type
  derived from the file extension (or sniffed from raw bytes).
* The system prompt carries the **generated** EAD JSON Schema
  (``aetherfx.ead.ead_json_schema``) plus the AetherFX node vocabulary, and
  demands a single JSON object and nothing else.
* The reply is parsed with the pydantic model.  If parsing or validation fails
  the adapter retries **once**, feeding the error text back so the model can
  repair its own output; a second failure raises :class:`AnalyzerError`.

Credentials: the API key comes from the constructor or the ``ANTHROPIC_API_KEY``
environment variable, and from nowhere else - no OAuth profile, no auth token,
no config file.  Pass a pre-built ``client`` to control that yourself.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, ClassVar, Sequence

from pydantic import BaseModel, ValidationError

from ..ead import EffectAnalysisDocument, RenderEvaluation, ead_json_schema
from .base import ImageInput, ReferenceAnalyzer, Segment, media_type_for, read_image_bytes

__all__ = ["DEFAULT_MODEL", "MODEL_ENV_VAR", "API_KEY_ENV_VAR", "AnalyzerError", "ClaudeAnalyzer"]

#: Default vision model.  Override per instance or with ``$AETHERFX_CLAUDE_MODEL``.
DEFAULT_MODEL = "claude-sonnet-5"

MODEL_ENV_VAR = "AETHERFX_CLAUDE_MODEL"
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"

#: Non-streaming default; an EAD for a busy effect is a few thousand tokens.
DEFAULT_MAX_TOKENS = 16000


class AnalyzerError(RuntimeError):
    """The model could not be reached, refused, or produced unusable output."""


_JSON_RULES = """
Answer with a single JSON object and nothing else. No prose, no explanation, no
markdown code fence. The object must validate against the JSON Schema below.
Use `null` for anything you cannot determine; never invent a field name that is
not in the schema.
""".strip()

_VOCABULARY_NOTE = """
`primitive` on a layer and `type` on a planned node must be one of the AetherFX
node types: emitter, particle_system, volume, force, field, mesh, curve, trail,
beam, light, decal, material, event, noise, collider, camera, post_effect,
texture. A planned node carries its parameters as members next to `type`, e.g.
{"type": "emitter", "shape": "ring", "radius": 3.0}.
""".strip()

_ANALYZE_SYSTEM = """
You are a senior real-time VFX artist analysing a reference image for AetherFX,
a node-graph VFX engine. Produce an Effect Analysis Document (EAD).

Rules:
- Separate the effect into layers by visual role, not by colour: telegraph,
  ignition, primary, secondary, interaction, aftermath.
- A still image shows no motion and no timing. Anything you say about movement
  or the timeline is a hypothesis: keep `inferred` true and keep `confidence`
  honest and low where you are guessing.
- Colours are linear RGB; emissive values may exceed 1.0.
- World sizes are metres, times are seconds, angles are degrees.
- Put anything you genuinely cannot tell in `open_questions` instead of
  inventing a confident answer.

{vocabulary}

{json_rules}

JSON Schema for your reply:
{schema}
""".strip()

_EVALUATE_SYSTEM = """
You are grading an AetherFX render against the reference image it was built
from. The FIRST image is the reference, the SECOND image is the render.

Rules:
- Judge silhouette, scale and placement first, then brightness and palette,
  then density and motion cues. Ignore differences that come from the reference
  being a photograph or painting rather than a real-time render.
- `score` is 0..1 where 1 means indistinguishable from the reference.
- Every suggestion must name a concrete parameter change the engine can apply:
  fill in `node` and `parameter` with names from the effect when you know them,
  and explain in `reason` what visual difference the change fixes.
- Prefer a few high-impact suggestions over many small ones.

{json_rules}

JSON Schema for your reply:
{schema}
""".strip()

_DESCRIBE_SYSTEM = """
You are a real-time VFX artist. Describe the effect in the image in plain
language for another artist: its overall form, its layers from telegraph to
aftermath, its palette and brightness, and how you think it moves. Two short
paragraphs at most. Reply with prose only - no JSON, no markdown headings.
""".strip()


class ClaudeAnalyzer(ReferenceAnalyzer):
    """A :class:`ReferenceAnalyzer` backed by a Claude vision model.

    Parameters
    ----------
    model:
        Model id.  Defaults to ``$AETHERFX_CLAUDE_MODEL`` then
        :data:`DEFAULT_MODEL`.
    api_key:
        API key.  Defaults to ``$ANTHROPIC_API_KEY``.  No other credential
        source is consulted.
    client:
        A pre-built ``anthropic.Anthropic`` (or any object exposing
        ``messages.create``).  When given, ``api_key`` is ignored and the
        ``anthropic`` package is never imported - which is what the tests use.
    max_tokens:
        Output cap for every request.
    timeout:
        Per-request timeout in seconds; ``None`` keeps the SDK default.
    """

    name: ClassVar[str] = "claude"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        client: Any | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float | None = None,
    ) -> None:
        self.model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = client
        self._api_key = api_key

    # -- client ------------------------------------------------------------

    @property
    def client(self) -> Any:
        """The Anthropic client, built on first use from the constructor key."""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        """Import ``anthropic`` lazily and construct a client with an explicit key."""
        try:
            import anthropic  # noqa: PLC0415 - deliberate lazy import
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise AnalyzerError(
                "the 'anthropic' package is required for ClaudeAnalyzer; "
                "install it with: pip install 'aetherfx[claude]'"
            ) from exc

        key = self._api_key or os.environ.get(API_KEY_ENV_VAR)
        if not key:
            raise AnalyzerError(
                f"no Anthropic API key: pass api_key= to ClaudeAnalyzer or set ${API_KEY_ENV_VAR}"
            )
        kwargs: dict[str, Any] = {"api_key": key}
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        return anthropic.Anthropic(**kwargs)

    # -- request plumbing --------------------------------------------------

    @staticmethod
    def _image_block(image: ImageInput) -> dict[str, Any]:
        """Build a base64 ``image`` content block for one reference or render."""
        data = read_image_bytes(image)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type_for(image),
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }

    def _create(self, system: str, messages: Sequence[dict[str, Any]]) -> str:
        """Send one request and return the concatenated text blocks of the reply."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=list(messages),
        )
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            raise AnalyzerError(f"the model declined to answer (stop_reason=refusal, details={details!r})")
        chunks: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                chunks.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
        text = "".join(chunks).strip()
        if not text:
            raise AnalyzerError(
                f"the model returned no text content (stop_reason={getattr(response, 'stop_reason', None)!r})"
            )
        return text

    @staticmethod
    def _extract_json(text: str) -> str:
        """Strip a markdown fence and trailing prose from an otherwise-JSON reply."""
        stripped = text.strip()
        if stripped.startswith("```"):
            body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
            if body.rstrip().endswith("```"):
                body = body.rstrip()[: -len("```")]
            stripped = body.strip()
        if stripped.startswith("{"):
            return stripped
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            return stripped[start : end + 1]
        return stripped

    def _parse_or_retry(
        self,
        system: str,
        messages: list[dict[str, Any]],
        model_cls: type[BaseModel],
    ) -> Any:
        """Ask, parse, and on failure re-ask once with the error fed back."""
        text = self._create(system, messages)
        try:
            return model_cls.model_validate_json(self._extract_json(text))
        except (ValidationError, ValueError) as first_error:
            repair = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "Your reply did not validate against the schema:\n\n"
                        f"{first_error}\n\n"
                        "Reply again with the corrected JSON object only. Fix exactly these problems and "
                        "keep everything else you already determined."
                    ),
                },
            ]
            retry_text = self._create(system, repair)
            try:
                return model_cls.model_validate_json(self._extract_json(retry_text))
            except (ValidationError, ValueError) as second_error:
                raise AnalyzerError(
                    f"{model_cls.__name__} did not validate after one repair attempt.\n"
                    f"First error: {first_error}\nSecond error: {second_error}\n"
                    f"Last reply: {retry_text[:2000]}"
                ) from second_error

    # -- ReferenceAnalyzer -------------------------------------------------

    def analyze_reference(self, image: ImageInput, prompt: str | None = None) -> EffectAnalysisDocument:
        """Analyse a reference image into an Effect Analysis Document."""
        system = _ANALYZE_SYSTEM.format(
            vocabulary=_VOCABULARY_NOTE,
            json_rules=_JSON_RULES,
            schema=json.dumps(ead_json_schema(), indent=2),
        )
        instruction = "Analyse this reference image and produce the Effect Analysis Document."
        if prompt:
            instruction += f"\n\nAdditional direction from the author: {prompt}"
        messages = [
            {"role": "user", "content": [self._image_block(image), {"type": "text", "text": instruction}]}
        ]
        document: EffectAnalysisDocument = self._parse_or_retry(system, messages, EffectAnalysisDocument)
        # The model does not know where the file lives; record it ourselves.
        if not isinstance(image, (bytes, bytearray)):
            document.source.path = str(image)
        if prompt:
            document.source.prompt = prompt
        return document

    def segment_reference(self, image: ImageInput) -> list[Segment]:
        """Return one :class:`Segment` per analysed primary form.

        A vision model returns boxes, not pixels, so every segment has
        ``mask=None``.  Use an actual segmentation model if masks are required.
        """
        document = self.analyze_reference(image)
        return [
            Segment(
                label=form.role,
                bbox=form.screen_bbox or (0.0, 0.0, 1.0, 1.0),
                mask=None,
                confidence=form.confidence,
            )
            for form in document.primary_forms
        ]

    def describe_effect(self, image: ImageInput) -> str:
        """Return a short plain-language description of the effect in the image."""
        messages = [
            {
                "role": "user",
                "content": [
                    self._image_block(image),
                    {"type": "text", "text": "Describe this effect."},
                ],
            }
        ]
        return self._create(_DESCRIBE_SYSTEM, messages)

    def evaluate_render(
        self,
        reference: ImageInput,
        render: ImageInput,
        ead: EffectAnalysisDocument | None = None,
    ) -> RenderEvaluation:
        """Compare a render against its reference and propose concrete changes."""
        system = _EVALUATE_SYSTEM.format(
            json_rules=_JSON_RULES,
            schema=json.dumps(RenderEvaluation.model_json_schema(), indent=2),
        )
        instruction = (
            "The first image is the reference, the second is the current render. "
            "Grade the render and propose parameter changes."
        )
        if ead is not None:
            instruction += (
                "\n\nThis is the Effect Analysis Document the render was built from; use its layer and "
                "node names in your suggestions:\n" + ead.to_json()
            )
        messages = [
            {
                "role": "user",
                "content": [
                    self._image_block(reference),
                    self._image_block(render),
                    {"type": "text", "text": instruction},
                ],
            }
        ]
        return self._parse_or_retry(system, messages, RenderEvaluation)
