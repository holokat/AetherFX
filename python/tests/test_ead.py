"""EAD model validation and JSON-Schema export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aetherfx.ead import (
    EAD_VERSION,
    JSON_SCHEMA_DIALECT,
    NODE_TYPES,
    EffectAnalysisDocument,
    PlannedNode,
    RenderEvaluation,
    TemporalPhase,
    ead_json_schema,
    export_json_schema,
)

from conftest import SCHEMA_PATH


def minimal_document() -> dict[str, Any]:
    """The smallest EAD that validates, written with wire names."""
    return {
        "ead_version": EAD_VERSION,
        "source": {"kind": "image", "path": "refs/fire.png"},
        "global": {"effect_category": "fire_aoe", "probable_duration_s": 4.5, "confidence": 0.7},
    }


class TestGoodDocuments:
    def test_minimal_document_validates(self) -> None:
        document = EffectAnalysisDocument.model_validate(minimal_document())
        assert document.global_analysis.effect_category == "fire_aoe"
        assert document.temporal_hypothesis.phases == []

    def test_global_is_the_wire_name(self) -> None:
        document = EffectAnalysisDocument.model_validate(minimal_document())
        dumped = document.to_json_dict()
        assert "global" in dumped
        assert "global_analysis" not in dumped

    def test_canned_document_round_trips_byte_for_byte(self) -> None:
        from aetherfx.ai.mock import canned_fire_aoe_ead

        original = canned_fire_aoe_ead("refs/fire_aoe.png", "make it bigger")
        dumped = original.to_json_dict()
        assert EffectAnalysisDocument.model_validate(dumped).to_json_dict() == dumped

    def test_layer_lookup(self) -> None:
        from aetherfx.ai.mock import canned_fire_aoe_ead

        document = canned_fire_aoe_ead()
        assert document.layer("primary") is not None
        assert document.layer("nope") is None

    def test_open_effect_category(self) -> None:
        payload = minimal_document()
        payload["global"]["effect_category"] = "ice_nova"
        assert EffectAnalysisDocument.model_validate(payload).global_analysis.effect_category == "ice_nova"


class TestPlannedNode:
    def test_flat_parameters_are_folded(self) -> None:
        node = PlannedNode.model_validate({"type": "emitter", "shape": "ring", "radius": 3.0})
        assert node.type == "emitter"
        assert node.parameters == {"shape": "ring", "radius": 3.0}

    def test_nested_parameters_still_work(self) -> None:
        node = PlannedNode.model_validate({"type": "force", "parameters": {"strength": 2.0}})
        assert node.parameters == {"strength": 2.0}

    def test_mixed_form_merges_with_nested_winning(self) -> None:
        node = PlannedNode.model_validate({"type": "force", "strength": 1.0, "parameters": {"strength": 9.0}})
        assert node.parameters == {"strength": 9.0}

    def test_serialisation_is_flat_like_the_docs(self) -> None:
        node = PlannedNode.model_validate({"type": "emitter", "id": "wall", "shape": "ring"})
        assert node.model_dump() == {"type": "emitter", "id": "wall", "shape": "ring"}

    def test_unknown_node_type_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlannedNode.model_validate({"type": "wormhole"})

    def test_every_vocabulary_type_is_accepted(self) -> None:
        for node_type in NODE_TYPES:
            assert PlannedNode.model_validate({"type": node_type}).type == node_type


class TestBadDocuments:
    def test_missing_source_is_rejected(self) -> None:
        payload = minimal_document()
        del payload["source"]
        with pytest.raises(ValidationError, match="source"):
            EffectAnalysisDocument.model_validate(payload)

    def test_missing_global_is_rejected(self) -> None:
        payload = minimal_document()
        del payload["global"]
        with pytest.raises(ValidationError):
            EffectAnalysisDocument.model_validate(payload)

    def test_unknown_member_is_rejected(self) -> None:
        payload = minimal_document()
        payload["vibes"] = "immaculate"
        with pytest.raises(ValidationError, match="vibes"):
            EffectAnalysisDocument.model_validate(payload)

    def test_invalid_source_kind_is_rejected(self) -> None:
        payload = minimal_document()
        payload["source"]["kind"] = "hologram"
        with pytest.raises(ValidationError):
            EffectAnalysisDocument.model_validate(payload)

    def test_confidence_out_of_range_is_rejected(self) -> None:
        payload = minimal_document()
        payload["global"]["confidence"] = 1.4
        with pytest.raises(ValidationError):
            EffectAnalysisDocument.model_validate(payload)

    def test_non_positive_duration_is_rejected(self) -> None:
        payload = minimal_document()
        payload["global"]["probable_duration_s"] = 0
        with pytest.raises(ValidationError):
            EffectAnalysisDocument.model_validate(payload)

    def test_invalid_layer_enum_is_rejected(self) -> None:
        payload = minimal_document()
        payload["layers"] = [{"id": "x", "semantic_role": "chorus", "primitive": "emitter"}]
        with pytest.raises(ValidationError):
            EffectAnalysisDocument.model_validate(payload)

    def test_layer_primitive_must_be_a_node_type(self) -> None:
        payload = minimal_document()
        payload["layers"] = [{"id": "x", "semantic_role": "primary", "primitive": "sparkle"}]
        with pytest.raises(ValidationError):
            EffectAnalysisDocument.model_validate(payload)

    def test_reversed_phase_window_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be greater than start"):
            TemporalPhase.model_validate({"name": "peak", "start": 2.0, "end": 1.0})

    def test_empty_phase_window_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemporalPhase.model_validate({"name": "peak", "start": 1.0, "end": 1.0})

    def test_render_evaluation_score_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            RenderEvaluation.model_validate({"score": 1.5})


class TestSchemaExport:
    def test_schema_declares_the_2020_12_dialect(self) -> None:
        schema = ead_json_schema()
        assert schema["$schema"] == JSON_SCHEMA_DIALECT
        assert schema["title"] == "Effect Analysis Document"
        assert schema["x-ead-version"] == EAD_VERSION

    def test_schema_uses_the_wire_name_for_global(self) -> None:
        schema = ead_json_schema()
        assert "global" in schema["properties"]
        assert "global_analysis" not in schema["properties"]

    def test_checked_in_schema_is_up_to_date(self, tmp_path: Path) -> None:
        """schema/ead.schema.json must equal a fresh export - regenerate with
        ``python -c 'from aetherfx.ead import export_json_schema; export_json_schema()'``."""
        assert SCHEMA_PATH.is_file(), f"{SCHEMA_PATH} is missing; generate it first"
        regenerated = tmp_path / "ead.schema.json"
        export_json_schema(regenerated)
        assert regenerated.read_text(encoding="utf-8") == SCHEMA_PATH.read_text(encoding="utf-8")

    def test_checked_in_schema_is_valid_json(self) -> None:
        assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$id"].endswith("ead.schema.json")
