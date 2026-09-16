"""Optional AI adapters for AetherFX reference analysis and render evaluation.

Nothing in the engine depends on this package: the runtime is fully functional
without any AI (docs/ARCHITECTURE.md section 9).  Importing ``aetherfx.ai`` does
not import the ``anthropic`` SDK; only constructing
:class:`~aetherfx.ai.claude.ClaudeAnalyzer` does.
"""

from __future__ import annotations

from .base import ImageInput, ReferenceAnalyzer, Segment, media_type_for, read_image_bytes
from .mock import MockAnalyzer, canned_fire_aoe_ead, canned_render_evaluation
from .registry import ANALYZER_ENV_VAR, DEFAULT_ANALYZER, available_analyzers, get_analyzer

__all__ = [
    "ANALYZER_ENV_VAR",
    "DEFAULT_ANALYZER",
    "ImageInput",
    "MockAnalyzer",
    "ReferenceAnalyzer",
    "Segment",
    "available_analyzers",
    "canned_fire_aoe_ead",
    "canned_render_evaluation",
    "get_analyzer",
    "media_type_for",
    "read_image_bytes",
]
