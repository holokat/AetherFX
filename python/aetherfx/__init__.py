"""AetherFX - Python client, MCP server, AI adapters and evaluation metrics.

AetherFX is an AI-native authoring system for real-time game VFX.  The C++
engine owns the graph, the runtime and the renderer; this package is the agent's
side of the boundary (docs/ARCHITECTURE.md section 1):

* :mod:`aetherfx.jsonrpc` - newline-delimited JSON-RPC 2.0 over ``aetherfx serve``.
* :mod:`aetherfx.client` - a typed wrapper for every tool in docs/AGENT_API.md.
* :mod:`aetherfx.ead` - pydantic models for the Effect Analysis Document.
* :mod:`aetherfx.ai` - optional reference analyzers (mock, Claude).
* :mod:`aetherfx.evaluate` - the non-AI metric baseline (coverage, palette,
  histogram, centroid, radial profile).
* :mod:`aetherfx.planner` - EAD -> graph tool calls.
* :mod:`aetherfx.mcp_server` - the curated MCP surface.

Typical use::

    from aetherfx import Client

    with Client() as engine:
        engine.create_effect(name="Fire AOE", duration=4.5)
        engine.create_layer(id="primary", name="Primary", role="primary")
        engine.create_node(type="particle_system", id="wall_ps", layer="primary")
        print(engine.inspect_graph()["diagnostics"])
"""

from __future__ import annotations

from .client import BINARY_ENV_VAR, Client, ToolInfo, find_binary
from .ead import (
    ChangeSuggestion,
    EffectAnalysisDocument,
    GlobalAnalysis,
    ImplementationPlanEntry,
    LayerAnalysis,
    MotionHypothesis,
    OcclusionPair,
    PlannedNode,
    PrimaryForm,
    RenderEvaluation,
    Source,
    TemporalHypothesis,
    TemporalPhase,
    WorldHypothesis,
    ead_json_schema,
    export_json_schema,
)
from .evaluate import compare_images, image_stats
from .jsonrpc import AetherError, InMemoryTransport, SubprocessTransport, Transport, TransportError
from .planner import PlanExecutionError, ToolCall, apply_plan, ead_to_effect, ead_to_tool_calls

__version__ = "0.1.0"

__all__ = [
    "AetherError",
    "BINARY_ENV_VAR",
    "ChangeSuggestion",
    "Client",
    "EffectAnalysisDocument",
    "GlobalAnalysis",
    "ImplementationPlanEntry",
    "InMemoryTransport",
    "LayerAnalysis",
    "MotionHypothesis",
    "OcclusionPair",
    "PlanExecutionError",
    "PlannedNode",
    "PrimaryForm",
    "RenderEvaluation",
    "Source",
    "SubprocessTransport",
    "TemporalHypothesis",
    "TemporalPhase",
    "ToolCall",
    "ToolInfo",
    "Transport",
    "TransportError",
    "WorldHypothesis",
    "__version__",
    "apply_plan",
    "compare_images",
    "ead_json_schema",
    "ead_to_effect",
    "ead_to_tool_calls",
    "export_json_schema",
    "find_binary",
    "image_stats",
]
