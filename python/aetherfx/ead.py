"""Effect Analysis Document (EAD) - pydantic v2 models mirroring docs/EAD.md.

An EAD is the output of reference analysis (Mode A) and the planning input for
semantic generation (Mode B).  Any :class:`~aetherfx.ai.base.ReferenceAnalyzer`
produces one; :func:`aetherfx.planner.ead_to_tool_calls` turns it into graph
tool calls.

Reading guide for an LLM:

* Everything inferred from a still image is *inferred*.  The document carries an
  explicit ``confidence`` everywhere and an ``inferred: true`` flag on the
  motion and temporal hypotheses.  Do not silently upgrade a guess to a fact.
* The JSON member is named ``global`` on the wire.  ``global`` is a Python
  keyword, so the field is ``global_analysis`` with alias ``global``; always
  dump with ``by_alias=True`` (``EffectAnalysisDocument.to_json_dict()`` does).
* Closed vocabularies are ``Literal`` types so an invalid value fails
  validation.  ``effect_category`` is deliberately open (docs/EAD.md ends its
  list with "..."); :data:`KNOWN_EFFECT_CATEGORIES` lists the documented ones.
* Every model forbids unknown members except :class:`PlannedNode`, whose extra
  keys are the free-form node parameters.
* ``schema/ead.schema.json`` is generated from these models by
  :func:`export_json_schema`; a test asserts the checked-in file is current.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

__all__ = [
    "EAD_VERSION",
    "JSON_SCHEMA_DIALECT",
    "JSON_SCHEMA_ID",
    "KNOWN_EFFECT_CATEGORIES",
    "NODE_TYPES",
    "NodeType",
    "SourceKind",
    "VisualStyle",
    "ContrastLevel",
    "EnergyLevel",
    "SymmetryKind",
    "CameraAngle",
    "FormKind",
    "SemanticRole",
    "DepthLayer",
    "Brightness",
    "Opacity",
    "MaterialBehavior",
    "SimulationType",
    "DominantColor",
    "Source",
    "GlobalAnalysis",
    "PrimaryForm",
    "WorldHypothesis",
    "MotionHypothesis",
    "LayerAnalysis",
    "TemporalPhase",
    "TemporalHypothesis",
    "OcclusionPair",
    "PlannedNode",
    "ImplementationPlanEntry",
    "ChangeSuggestion",
    "RenderEvaluation",
    "EffectAnalysisDocument",
    "ead_json_schema",
    "export_json_schema",
    "default_schema_path",
]

EAD_VERSION = "0.1.0"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
JSON_SCHEMA_ID = "https://aetherfx.dev/schema/ead.schema.json"

#: Node types of the AetherFX vocabulary (docs/VOCABULARY.md).  A layer's
#: ``primitive`` and a planned node's ``type`` must be one of these.
NODE_TYPES: tuple[str, ...] = (
    "emitter",
    "particle_system",
    "volume",
    "force",
    "field",
    "mesh",
    "curve",
    "trail",
    "beam",
    "light",
    "decal",
    "material",
    "event",
    "noise",
    "collider",
    "camera",
    "post_effect",
    "texture",
)

NodeType = Literal[
    "emitter",
    "particle_system",
    "volume",
    "force",
    "field",
    "mesh",
    "curve",
    "trail",
    "beam",
    "light",
    "decal",
    "material",
    "event",
    "noise",
    "collider",
    "camera",
    "post_effect",
    "texture",
]

#: The effect categories docs/EAD.md names explicitly.  The field is an open
#: string: analyzers may return other categories, and the planner only uses it
#: to name the effect.
KNOWN_EFFECT_CATEGORIES: tuple[str, ...] = (
    "fire_aoe",
    "projectile",
    "beam",
    "impact",
    "aura",
    "environmental",
)

SourceKind = Literal["image", "video", "text"]
VisualStyle = Literal["realistic", "stylized", "painterly", "anime", "hybrid"]
ContrastLevel = Literal["low", "medium", "high"]
EnergyLevel = Literal["calm", "moderate", "violent"]
SymmetryKind = Literal["radial", "bilateral", "none"]
CameraAngle = Literal["top_down", "three_quarter", "eye_level", "low"]
FormKind = Literal[
    "ring", "circle", "sphere", "column", "cone", "beam", "arc", "wave", "cloud", "spiral", "trail", "explosion"
]
SemanticRole = Literal["telegraph", "ignition", "primary", "secondary", "interaction", "aftermath"]
DepthLayer = Literal["foreground", "midground", "background"]
Brightness = Literal["emissive_high", "emissive", "lit", "dark"]
Opacity = Literal["opaque", "translucent", "additive_glow"]
MaterialBehavior = Literal["flame", "smoke", "sparks", "solid", "energy", "liquid", "distortion"]
SimulationType = Literal["particles", "analytic", "rigid", "volume"]

_STRICT = ConfigDict(extra="forbid", populate_by_name=True)


class DominantColor(BaseModel):
    """One entry of a colour palette: linear RGB plus the share of the image."""

    model_config = _STRICT

    rgb: tuple[float, float, float] = Field(description="Linear RGB; values may exceed 1 for emissive colours.")
    share: float = Field(ge=0.0, le=1.0, description="Fraction of the analysed pixels this colour covers.")
    role: str | None = Field(default=None, description="What the colour is, e.g. 'flame', 'smoke', 'rim'.")


class Source(BaseModel):
    """Where the analysis came from."""

    model_config = _STRICT

    kind: SourceKind
    path: str | None = Field(default=None, description="Reference image or video path, when there is a file.")
    prompt: str | None = Field(default=None, description="Text prompt, or extra instructions given with an image.")


class GlobalAnalysis(BaseModel):
    """Whole-image observations.  Serialised as the ``global`` member."""

    model_config = _STRICT

    effect_category: str = Field(
        description="Open vocabulary; documented values: " + ", ".join(KNOWN_EFFECT_CATEGORIES) + "."
    )
    estimated_scale_m: float | None = Field(default=None, gt=0.0, description="Rough world size in metres.")
    visual_style: VisualStyle = "stylized"
    dominant_colors: list[DominantColor] = Field(default_factory=list)
    contrast: ContrastLevel = "medium"
    energy: EnergyLevel = "moderate"
    symmetry: SymmetryKind = "none"
    camera_angle: CameraAngle = "three_quarter"
    probable_duration_s: float = Field(default=2.0, gt=0.0, description="Inferred effect duration in seconds.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class PrimaryForm(BaseModel):
    """A silhouette read off the reference, with where it sits on screen."""

    model_config = _STRICT

    form: FormKind
    screen_bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="[x0, y0, x1, y1] normalised to [0,1] with the origin at the top left."
    )
    role: str = Field(description="What this form is in the effect, e.g. 'outer flame wall'.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class WorldHypothesis(BaseModel):
    """Guessed world-space placement and size of a layer, in metres."""

    model_config = _STRICT

    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius_m: float | None = Field(default=None, ge=0.0)
    height_m: float | None = Field(default=None, ge=0.0)
    length_m: float | None = Field(default=None, ge=0.0)


class MotionHypothesis(BaseModel):
    """Guessed motion.  ``inferred`` stays true for anything read off a still."""

    model_config = _STRICT

    inferred: bool = True
    description: str = Field(description="Plain language, e.g. 'rising turbulent flames'.")
    speed_mps: float | None = Field(default=None, ge=0.0)
    direction: tuple[float, float, float] | None = None


class LayerAnalysis(BaseModel):
    """One visually separable element of the effect."""

    model_config = _STRICT

    id: str = Field(description="Stable identifier, e.g. 'outer_flame_wall'.")
    semantic_role: SemanticRole
    primitive: NodeType = Field(description="The vocabulary node type that best matches this layer.")
    shape_hint: str | None = Field(default=None, description="Free-form, e.g. 'ring', 'column', 'sheet'.")
    screen_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    world_hypothesis: WorldHypothesis | None = None
    depth: DepthLayer = "midground"
    inside_of: str | None = Field(default=None, description="Layer id that contains this one, if any.")
    color: tuple[float, float, float] = Field(default=(1.0, 1.0, 1.0), description="Representative linear RGB.")
    brightness: Brightness = "lit"
    opacity: Opacity = "translucent"
    material_behavior: MaterialBehavior = "energy"
    motion_hypothesis: MotionHypothesis | None = None
    simulation_type: SimulationType = "particles"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class TemporalPhase(BaseModel):
    """One absolute ``[start, end]`` window in seconds."""

    model_config = _STRICT

    name: str = Field(description="anticipation | activation | peak | sustain | decay | <custom>")
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    description: str | None = None

    @model_validator(mode="after")
    def _check_window(self) -> "TemporalPhase":
        """Reject empty or reversed windows (engine diagnostic E016)."""
        if self.end <= self.start:
            raise ValueError(f"phase {self.name!r}: end ({self.end}) must be greater than start ({self.start})")
        return self


class TemporalHypothesis(BaseModel):
    """The guessed timeline.  Always ``inferred`` when the source is a still."""

    model_config = _STRICT

    inferred: bool = True
    phases: list[TemporalPhase] = Field(default_factory=list)


class OcclusionPair(BaseModel):
    """``front`` draws over ``behind``; both are layer ids."""

    model_config = _STRICT

    front: str
    behind: str


class PlannedNode(BaseModel):
    """One node to create, as ``type`` plus free-form parameters.

    docs/EAD.md writes planned nodes flat - ``{"type": "emitter", "shape":
    "ring", "radius": 3.0}`` - so any member other than ``type``, ``id`` and
    ``parameters`` is folded into :attr:`parameters` on validation and written
    back flat on serialisation.  Parameter names and value encodings are not
    checked here; the engine validates them (E004/E005/E006/E007) and reports
    diagnostics on ``create_node``.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: NodeType
    id: str | None = Field(default=None, description="Optional node id; the planner derives one when absent.")
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _fold_flat_parameters(cls, data: Any) -> Any:
        """Move sibling members of ``type`` into ``parameters``."""
        if not isinstance(data, dict):
            return data
        reserved = {"type", "id", "parameters"}
        flat = {key: value for key, value in data.items() if key not in reserved}
        if not flat:
            return data
        merged: dict[str, Any] = dict(flat)
        nested = data.get("parameters")
        if isinstance(nested, dict):
            merged.update(nested)
        folded = {key: value for key, value in data.items() if key in reserved}
        folded["parameters"] = merged
        return folded

    @model_serializer(mode="plain")
    def _serialize_flat(self) -> dict[str, Any]:
        """Write ``type``/``id`` plus the parameters as siblings, as docs/EAD.md does."""
        out: dict[str, Any] = {"type": self.type}
        if self.id is not None:
            out["id"] = self.id
        out.update(self.parameters)
        return out


class ImplementationPlanEntry(BaseModel):
    """The nodes that realise one analysed layer."""

    model_config = _STRICT

    layer: str = Field(description="Layer id; should match a LayerAnalysis.id.")
    nodes: list[PlannedNode] = Field(default_factory=list)


class ChangeSuggestion(BaseModel):
    """A single proposed edit coming out of a render evaluation."""

    model_config = _STRICT

    layer: str | None = None
    node: str | None = None
    parameter: str | None = None
    current: Any | None = None
    proposed: Any | None = None
    reason: str


class RenderEvaluation(BaseModel):
    """The verdict of comparing a render against its reference.

    ``score`` uses the same 0..1 orientation as
    :func:`aetherfx.evaluate.compare_images` (1 = indistinguishable).
    ``metrics`` optionally carries that non-AI metric baseline so an agent can
    see both the measured numbers and the judgement.
    """

    model_config = _STRICT

    score: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    matches: list[str] = Field(default_factory=list, description="What the render already gets right.")
    mismatches: list[str] = Field(default_factory=list, description="What differs from the reference.")
    suggestions: list[ChangeSuggestion] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class EffectAnalysisDocument(BaseModel):
    """The whole EAD.  ``global_analysis`` is the ``global`` JSON member."""

    model_config = _STRICT

    ead_version: str = EAD_VERSION
    source: Source
    global_analysis: GlobalAnalysis = Field(alias="global")
    primary_forms: list[PrimaryForm] = Field(default_factory=list)
    layers: list[LayerAnalysis] = Field(default_factory=list)
    temporal_hypothesis: TemporalHypothesis = Field(default_factory=TemporalHypothesis)
    occlusion: list[OcclusionPair] = Field(default_factory=list)
    implementation_plan: list[ImplementationPlanEntry] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    def layer(self, layer_id: str) -> LayerAnalysis | None:
        """Return the :class:`LayerAnalysis` with this id, or ``None``."""
        for item in self.layers:
            if item.id == layer_id:
                return item
        return None

    def to_json_dict(self) -> dict[str, Any]:
        """Dump with wire names (``global``), ready for ``json.dumps``."""
        return self.model_dump(by_alias=True, mode="json")

    def to_json(self, indent: int | None = 2) -> str:
        """Serialise to a JSON string with wire names."""
        return json.dumps(self.to_json_dict(), indent=indent, ensure_ascii=False)


def ead_json_schema() -> dict[str, Any]:
    """Build the JSON Schema (draft 2020-12) for :class:`EffectAnalysisDocument`."""
    schema = EffectAnalysisDocument.model_json_schema(by_alias=True, mode="validation")
    ordered: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": JSON_SCHEMA_ID,
        "title": "Effect Analysis Document",
        "description": (
            "Output of AetherFX reference analysis (Mode A) and the planning input for semantic "
            "generation (Mode B). Generated from aetherfx.ead; see docs/EAD.md."
        ),
        "x-ead-version": EAD_VERSION,
    }
    for key, value in schema.items():
        if key in ("title", "description"):
            continue
        ordered[key] = value
    return ordered


def default_schema_path() -> Path:
    """Return ``<repo>/schema/ead.schema.json`` for this checkout."""
    return Path(__file__).resolve().parents[2] / "schema" / "ead.schema.json"


def export_json_schema(path: str | Path | None = None) -> dict[str, Any]:
    """Write the EAD JSON Schema to ``path`` and return it.

    ``path`` defaults to :func:`default_schema_path`.  The file is written with
    two-space indentation and a trailing newline so regenerating it is a no-op
    in version control.
    """
    target = Path(path) if path is not None else default_schema_path()
    schema = ead_json_schema()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return schema


if __name__ == "__main__":  # pragma: no cover - `python -m aetherfx.ead`
    written = default_schema_path()
    export_json_schema(written)
    print(f"wrote {written}")
