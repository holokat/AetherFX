"""Turn an Effect Analysis Document into a sequence of engine tool calls.

This is Mode B of docs/EAD.md: the analysis says *what* the effect is made of,
the planner says *which tools to call* to build it, and the engine executes
them.  The planner is pure - it never touches the network and never needs a
live engine - so a plan can be inspected, diffed and unit-tested before any
process is spawned.

Emission order (an agent can rely on it):

1. ``create_effect`` - always first; name from the source/category, duration
   from ``global.probable_duration_s``.
2. ``set_timeline_phase`` - one per ``temporal_hypothesis.phases`` entry.
3. Per ``implementation_plan`` entry, in document order:
   a. ``create_layer`` with the role of the matching ``LayerAnalysis``;
   b. ``create_node`` for each planned node, in plan order;
   c. ``connect_nodes`` for the conventional wiring inside that entry.

Every ``connect_nodes`` therefore references ids created earlier in the same
list, and every node references a layer created earlier.

Conventional wiring (docs/VOCABULARY.md port names):

===================  ==================  ==========
from (source)        to (consumer)       port
===================  ==================  ==========
``particle_system``  ``emitter``         ``particle``
``material``         ``particle_system`` ``material``
``force``            ``particle_system`` ``forces`` (multi)
``texture``          ``particle_system`` ``sprite``
``texture``          ``decal``           ``texture``
===================  ==================  ==========

Single ports pair by index (consumer *i* takes source ``min(i, n-1)``) so
adding a second emitter to an entry does not silently steal the first one's
particle system.  ``forces`` is a multi port, so every force in the entry is
attached to every particle system in the entry.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .client import Client
from .ead import EffectAnalysisDocument, ImplementationPlanEntry
from .jsonrpc import AetherError, JsonDict, TransportError

__all__ = [
    "SINGLE_PORT_WIRING",
    "MULTI_PORT_WIRING",
    "ToolCall",
    "PlanExecutionError",
    "ead_to_tool_calls",
    "apply_plan",
    "ead_to_effect",
]

#: ``(source type, consumer type) -> port`` for single-valued input ports.
SINGLE_PORT_WIRING: dict[tuple[str, str], str] = {
    ("particle_system", "emitter"): "particle",
    ("material", "particle_system"): "material",
    ("texture", "particle_system"): "sprite",
    ("texture", "decal"): "texture",
}

#: ``(source type, consumer type) -> port`` for multi-valued input ports.
MULTI_PORT_WIRING: dict[tuple[str, str], str] = {
    ("force", "particle_system"): "forces",
}

#: Order in which wiring rules are emitted, so plans are byte-stable.
_WIRING_ORDER: tuple[tuple[str, str], ...] = (
    ("particle_system", "emitter"),
    ("material", "particle_system"),
    ("force", "particle_system"),
    ("texture", "particle_system"),
    ("texture", "decal"),
)

_ID_RE = re.compile(r"[^a-z0-9_]+")


class ToolCall(BaseModel):
    """One engine tool invocation: a tool ``name`` and its argument object."""

    model_config = ConfigDict(extra="forbid")

    name: str
    args: dict[str, Any] = Field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        rendered = ", ".join(f"{key}={value!r}" for key, value in self.args.items())
        return f"ToolCall({self.name}({rendered}))"


class PlanExecutionError(RuntimeError):
    """A tool call in a plan failed; carries the call, its index and prior results."""

    def __init__(
        self,
        index: int,
        call: ToolCall,
        error: BaseException,
        results: Sequence[JsonDict],
    ) -> None:
        detail = f"call {index + 1} of the plan failed: {call.name}({call.args!r}) -> {error}"
        super().__init__(detail)
        self.index = index
        self.call = call
        self.error = error
        #: Results of the calls that did succeed, in order.
        self.results: list[JsonDict] = list(results)


def _sanitize_id(text: str, fallback: str = "node") -> str:
    """Coerce ``text`` into the engine's node/layer id form ``^[a-z][a-z0-9_]*$``."""
    lowered = _ID_RE.sub("_", text.strip().lower()).strip("_")
    if not lowered or not lowered[0].isalpha():
        lowered = f"{fallback}_{lowered}" if lowered else fallback
    return lowered


def _title(text: str) -> str:
    """Human-readable name from an id: ``outer_flame_wall`` -> ``Outer Flame Wall``."""
    return " ".join(part.capitalize() for part in text.replace("_", " ").split()) or text


def _effect_name(ead: EffectAnalysisDocument) -> str:
    """Derive the effect name from the source, falling back to the category.

    A prompt wins over a file name, a file name wins over the category, so
    ``{"kind": "image", "path": "refs/fire_aoe.png"}`` becomes ``Fire Aoe``.
    """
    prompt = (ead.source.prompt or "").strip()
    if prompt:
        first_line = prompt.splitlines()[0].strip()
        return (first_line[:60]).strip() or _title(ead.global_analysis.effect_category)
    path = (ead.source.path or "").strip()
    if path:
        stem = Path(path).stem
        if stem:
            return _title(stem)
    return _title(ead.global_analysis.effect_category)


class _IdAllocator:
    """Hands out unique node ids of the form ``{layer}_{type}``, then ``_2``, ``_3``."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def allocate(self, preferred: str) -> str:
        """Return ``preferred`` if free, else ``preferred_2``, ``preferred_3``, ..."""
        base = _sanitize_id(preferred)
        if base not in self._used:
            self._used.add(base)
            return base
        index = 2
        while f"{base}_{index}" in self._used:
            index += 1
        unique = f"{base}_{index}"
        self._used.add(unique)
        return unique


def _connect(source: str, target: str, port: str) -> ToolCall:
    """Build a ``connect_nodes`` call (``from`` is the wire name, not ``from_``)."""
    return ToolCall(name="connect_nodes", args={"from": source, "to": target, "port": port})


def _wire_entry(created: Mapping[str, list[str]]) -> list[ToolCall]:
    """Emit the conventional connections for one implementation-plan entry.

    ``created`` maps a node type to the ids created for it in this entry, in
    plan order.  Single ports pair by index; multi ports fan out.
    """
    calls: list[ToolCall] = []
    for pair in _WIRING_ORDER:
        source_type, consumer_type = pair
        sources = created.get(source_type, [])
        consumers = created.get(consumer_type, [])
        if not sources or not consumers:
            continue
        if pair in MULTI_PORT_WIRING:
            port = MULTI_PORT_WIRING[pair]
            for consumer in consumers:
                for source in sources:
                    calls.append(_connect(source, consumer, port))
        else:
            port = SINGLE_PORT_WIRING[pair]
            for index, consumer in enumerate(consumers):
                calls.append(_connect(sources[min(index, len(sources) - 1)], consumer, port))
    return calls


def _plan_entry_calls(
    entry: ImplementationPlanEntry,
    ead: EffectAnalysisDocument,
    allocator: _IdAllocator,
) -> list[ToolCall]:
    """Emit ``create_layer`` + ``create_node`` + ``connect_nodes`` for one entry."""
    layer_id = _sanitize_id(entry.layer, fallback="layer")
    analysis = ead.layer(entry.layer)
    role = analysis.semantic_role if analysis is not None else "custom"

    calls: list[ToolCall] = [
        ToolCall(name="create_layer", args={"id": layer_id, "name": _title(entry.layer), "role": role})
    ]

    created: dict[str, list[str]] = {}
    for planned in entry.nodes:
        node_id = allocator.allocate(planned.id or f"{layer_id}_{planned.type}")
        args: JsonDict = {"type": planned.type, "id": node_id, "layer": layer_id}
        if planned.parameters:
            args["parameters"] = dict(planned.parameters)
        calls.append(ToolCall(name="create_node", args=args))
        created.setdefault(planned.type, []).append(node_id)

    calls.extend(_wire_entry(created))
    return calls


def ead_to_tool_calls(ead: EffectAnalysisDocument) -> list[ToolCall]:
    """Translate an EAD into the tool calls that build the effect.

    The returned list is ordered so it can be replayed top to bottom against a
    fresh session: ``create_effect`` first, then the timeline, then each plan
    entry's layer, its nodes and its connections.  Nothing in the list refers to
    an id that an earlier entry did not create.

    Node ids are ``{layer}_{type}``, made unique with a numeric suffix
    (``primary_emitter``, ``primary_emitter_2``), unless the planned node
    carries its own ``id``.
    """
    allocator = _IdAllocator()
    calls: list[ToolCall] = [
        ToolCall(
            name="create_effect",
            args={
                "name": _effect_name(ead),
                "duration": float(ead.global_analysis.probable_duration_s),
            },
        )
    ]
    for phase in ead.temporal_hypothesis.phases:
        calls.append(
            ToolCall(
                name="set_timeline_phase",
                args={"name": phase.name, "start": float(phase.start), "end": float(phase.end)},
            )
        )
    for entry in ead.implementation_plan:
        calls.extend(_plan_entry_calls(entry, ead, allocator))
    return calls


def apply_plan(client: Client, calls: Iterable[ToolCall]) -> list[JsonDict]:
    """Execute a plan against a live engine, stopping at the first failure.

    Returns one result object per call, in order.  On failure raises
    :class:`PlanExecutionError`, which carries the failing call, its index and
    the results collected so far - so a caller can report exactly how far the
    build got and which argument the engine rejected.
    """
    results: list[JsonDict] = []
    for index, call in enumerate(calls):
        try:
            results.append(client.call_with(call.name, call.args))
        except (AetherError, TransportError) as exc:
            raise PlanExecutionError(index, call, exc, results) from exc
    return results


def ead_to_effect(client: Client, ead: EffectAnalysisDocument) -> list[JsonDict]:
    """Build the effect described by ``ead`` on ``client``.

    Convenience for ``apply_plan(client, ead_to_tool_calls(ead))``; this is the
    entry point docs/EAD.md refers to as ``aetherfx.planner.ead_to_effect``.
    """
    return apply_plan(client, ead_to_tool_calls(ead))
