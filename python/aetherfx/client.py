"""Typed Python client for the AetherFX agent tool API.

Every operation an agent can perform is a tool in the C++ ``ToolRegistry``
(docs/AGENT_API.md).  :class:`Client` is a thin, strongly typed facade over the
JSON-RPC transport: there is no second code path and no client-side validation,
so whatever the engine accepts, this client accepts.

Reading guide for an LLM:

* ``client.call("create_node", type="emitter", id="fire")`` works for every
  tool, including ones added after this file was written.
* ``client.create_node(type="emitter", id="fire")`` is the same thing with a
  docstring and type hints.  Every tool in docs/AGENT_API.md has such a wrapper.
* ``client.some_new_tool(**args)`` is resolved dynamically through
  ``__getattr__``, so the client never blocks access to a tool it does not know.
* Optional arguments are dropped when they are ``None``; the engine then applies
  its own default.  ``effect_id`` is optional everywhere and means "the active
  effect" when omitted.
"""

from __future__ import annotations

import os
import shutil
from functools import partial
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .jsonrpc import AetherError, JsonDict, SubprocessTransport, Transport, TransportError

__all__ = ["BINARY_ENV_VAR", "ToolInfo", "find_binary", "Client"]

#: Environment variable consulted before any filesystem search.
BINARY_ENV_VAR = "AETHERFX_BINARY"

#: Name of the engine executable on PATH.
BINARY_NAME = "aetherfx"

JsonValue = Any


class ToolInfo(BaseModel):
    """One entry of the engine's ``tools/list`` reply.

    ``input_schema`` is a JSON Schema object describing the tool's arguments;
    ``mutating`` says whether calling it changes the document (and therefore
    pushes an undo entry); ``category`` is the docs/AGENT_API.md section
    (``effect``, ``graph``, ``parameters``, ``timeline``, ``simulate``,
    ``render``, ``inspect``, ``evaluate``, ``io``, ``history``).
    """

    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    input_schema: JsonDict = Field(default_factory=dict)
    mutating: bool = False
    category: str = ""


def _repo_root() -> Path:
    """Repository root of this checkout: ``<repo>/python/aetherfx/client.py``."""
    return Path(__file__).resolve().parents[2]


def find_binary(binary: str | os.PathLike[str] | None = None) -> str | None:
    """Locate the ``aetherfx`` executable, or return ``None``.

    Search order:

    1. the ``binary`` argument, if given;
    2. ``$AETHERFX_BINARY``;
    3. ``build/bin/aetherfx`` under the repository that contains this package
       (``<repo>/python/aetherfx/client.py`` -> ``<repo>/build/bin/aetherfx``),
       and the same path relative to the current working directory;
    4. ``aetherfx`` on ``PATH``.
    """
    if binary:
        return str(binary)

    from_env = os.environ.get(BINARY_ENV_VAR)
    if from_env:
        return from_env

    repo_root = _repo_root()
    candidates = [
        repo_root / "build" / "bin" / BINARY_NAME,
        Path.cwd() / "build" / "bin" / BINARY_NAME,
        Path.cwd().parent / "build" / "bin" / BINARY_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return shutil.which(BINARY_NAME)


class Client:
    """Talks to one ``aetherfx serve`` session.

    Parameters
    ----------
    binary:
        Path to the engine executable.  Defaults to :func:`find_binary`.
    transport:
        Use this transport instead of spawning a process.  Tests pass an
        :class:`~aetherfx.jsonrpc.InMemoryTransport` here.
    output_dir:
        Session output directory (``--output-dir``); rendered frames and saved
        effects land there.

    The subprocess is spawned lazily on the first call, so constructing a
    ``Client`` never fails just because the engine has not been built yet.
    """

    def __init__(
        self,
        binary: str | os.PathLike[str] | None = None,
        transport: Transport | None = None,
        output_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._explicit_transport = transport is not None
        self._transport: Transport | None = transport
        self.output_dir: str | None = str(output_dir) if output_dir is not None else None
        self.binary: str | None = None if transport is not None else find_binary(binary)
        self._requested_binary = str(binary) if binary else None
        self._tools_cache: list[ToolInfo] | None = None

    # -- transport ---------------------------------------------------------

    @property
    def transport(self) -> Transport:
        """The live transport, spawning ``aetherfx serve`` on first access."""
        if self._transport is None:
            if self.binary is None:
                hint = self._requested_binary or f"${BINARY_ENV_VAR} / build/bin/{BINARY_NAME} / PATH"
                raise TransportError(
                    f"the AetherFX engine binary was not found ({hint}). Build the C++ CLI or set "
                    f"{BINARY_ENV_VAR} to its path."
                )
            self._transport = SubprocessTransport(self.binary, output_dir=self.output_dir)
        return self._transport

    @property
    def connected(self) -> bool:
        """True when a transport already exists (no process is started to check)."""
        return self._transport is not None

    def available(self) -> bool:
        """Return True if the engine answers ``ping``.  Never raises."""
        try:
            return bool(self.ping().get("ok", False))
        except (AetherError, TransportError, OSError):
            return False

    def close(self) -> None:
        """Close the transport.  Externally supplied transports are closed too."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._tools_cache = None

    def __enter__(self) -> "Client":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- generic access ----------------------------------------------------

    @staticmethod
    def _args(**kwargs: Any) -> JsonDict:
        """Drop ``None`` values so the engine applies its own defaults."""
        return {key: value for key, value in kwargs.items() if value is not None}

    def call(self, tool_name: str, /, **args: Any) -> JsonDict:
        """Invoke tool ``tool_name`` with keyword arguments and return its result.

        The tool name is positional-only, so tools whose own arguments are
        called ``name`` (``create_effect``, ``create_layer``, ``set_parameter``,
        ``set_timeline_phase``, ...) pass straight through.

        Raises :class:`~aetherfx.jsonrpc.AetherError` for engine errors and
        :class:`~aetherfx.jsonrpc.TransportError` for transport failures.
        """
        return self.transport.request(tool_name, args)

    def call_with(self, tool_name: str, args: Mapping[str, Any], /) -> JsonDict:
        """Like :meth:`call` but takes the argument object as a mapping."""
        return self.transport.request(tool_name, dict(args))

    def __getattr__(self, name: str) -> Callable[..., JsonDict]:
        """Expose any engine tool as a method, including unknown future tools."""
        if name.startswith("_"):
            raise AttributeError(name)
        return partial(self.call, name)

    def __dir__(self) -> list[str]:  # pragma: no cover - interactive convenience
        names = set(super().__dir__())
        if self._tools_cache is not None:
            names.update(tool.name for tool in self._tools_cache)
        return sorted(names)

    # -- built-in protocol methods ----------------------------------------

    def ping(self) -> JsonDict:
        """Built-in ``ping`` -> ``{"ok": true}``.  Cheap liveness probe."""
        return self.transport.request("ping", {})

    def shutdown(self) -> JsonDict:
        """Built-in ``shutdown`` -> ``{"ok": true}``; the engine then exits."""
        return self.transport.request("shutdown", {})

    def tools(self, refresh: bool = False) -> list[ToolInfo]:
        """Return ``tools/list``, cached after the first successful call."""
        if self._tools_cache is None or refresh:
            result = self.transport.request("tools/list", {})
            self._tools_cache = [ToolInfo.model_validate(item) for item in result.get("tools", [])]
        return list(self._tools_cache)

    def tool_names(self, refresh: bool = False) -> list[str]:
        """Return just the tool names, in the order the engine lists them."""
        return [tool.name for tool in self.tools(refresh=refresh)]

    def tool(self, name: str, refresh: bool = False) -> ToolInfo | None:
        """Return one :class:`ToolInfo` by name, or ``None`` if unknown."""
        for info in self.tools(refresh=refresh):
            if info.name == name:
                return info
        return None

    # =====================================================================
    # effect
    # =====================================================================

    def create_effect(
        self,
        name: str,
        duration: float = 2.0,
        seed: int = 1,
        template: str | None = None,
    ) -> JsonDict:
        """Create an effect and make it active.

        ``template`` is ``"empty"`` or the name of an example in
        ``examples/effects``.  Returns ``{effect_id, effect}``.
        """
        return self.call("create_effect", **self._args(name=name, duration=duration, seed=seed, template=template))

    def delete_effect(self, effect_id: str) -> JsonDict:
        """Delete a loaded effect.  Returns ``{ok}``."""
        return self.call("delete_effect", effect_id=effect_id)

    def list_effects(self) -> JsonDict:
        """List loaded effects: ``{effects:[{effect_id,name,path,dirty,active}]}``."""
        return self.call("list_effects")

    def set_active_effect(self, effect_id: str) -> JsonDict:
        """Make ``effect_id`` the target of every tool that omits it.  ``{ok}``."""
        return self.call("set_active_effect", effect_id=effect_id)

    def set_effect_property(
        self,
        name: str | None = None,
        duration: float | None = None,
        seed: int | None = None,
        metadata: JsonDict | None = None,
    ) -> JsonDict:
        """Update effect-level properties.  Returns ``{effect}``."""
        return self.call(
            "set_effect_property", **self._args(name=name, duration=duration, seed=seed, metadata=metadata)
        )

    def describe_vocabulary(self, node_type: str | None = None) -> JsonDict:
        """Return the node vocabulary (all specs, or one) plus ``texture_ops``.

        This is the authoritative parameter list - prefer it over any memorised
        vocabulary when choosing parameter names.
        """
        return self.call("describe_vocabulary", **self._args(node_type=node_type))

    def get_effect_json(self, effect_id: str | None = None) -> JsonDict:
        """Return the full effect document as JSON."""
        return self.call("get_effect_json", **self._args(effect_id=effect_id))

    # =====================================================================
    # graph
    # =====================================================================

    def create_layer(self, name: str, role: str, id: str | None = None) -> JsonDict:
        """Create a semantic layer.  Returns ``{layer}``.

        ``role`` is one of ``telegraph``, ``ignition``, ``primary``,
        ``secondary``, ``interaction``, ``aftermath``, ``custom``.
        """
        return self.call("create_layer", **self._args(name=name, role=role, id=id))

    def delete_layer(self, layer_id: str, delete_nodes: bool = False) -> JsonDict:
        """Delete a layer.  Returns ``{ok, removed_nodes}``."""
        return self.call("delete_layer", layer_id=layer_id, delete_nodes=delete_nodes)

    def duplicate_layer(
        self, layer_id: str, new_id: str | None = None, suffix: str = "_copy"
    ) -> JsonDict:
        """Duplicate a layer and its nodes, remapping internal references."""
        return self.call("duplicate_layer", **self._args(layer_id=layer_id, new_id=new_id, suffix=suffix))

    def set_layer_property(
        self,
        layer_id: str,
        name: str | None = None,
        role: str | None = None,
        enabled: bool | None = None,
    ) -> JsonDict:
        """Update a layer.  Returns ``{layer}``."""
        return self.call(
            "set_layer_property", **self._args(layer_id=layer_id, name=name, role=role, enabled=enabled)
        )

    def create_node(
        self,
        type: str,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a node of any vocabulary ``type``.  Returns ``{node, diagnostics}``.

        ``parameters`` values use the JSON encoding of docs/VOCABULARY.md: a bare
        value, or ``{"value": v, "track": [{"time": s, "value": v, "interp": ...}]}``
        for a keyframed parameter.  ``inputs`` maps a port to a node id or a list
        of node ids for multi ports.
        """
        return self.call(
            "create_node",
            **self._args(
                type=type,
                id=id,
                layer=layer,
                parent=parent,
                parameters=dict(parameters) if parameters is not None else None,
                inputs=dict(inputs) if inputs is not None else None,
                metadata=metadata,
                seed=seed,
            ),
        )

    def _create_typed_node(self, tool: str, /, **kwargs: Any) -> JsonDict:
        """Shared body for the per-type ``create_*`` wrappers."""
        if kwargs.get("parameters") is not None:
            kwargs["parameters"] = dict(kwargs["parameters"])
        if kwargs.get("inputs") is not None:
            kwargs["inputs"] = dict(kwargs["inputs"])
        return self.call(tool, **self._args(**kwargs))

    def create_emitter(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create an ``emitter`` node (Tier 1 spawner; feeds one particle_system)."""
        return self._create_typed_node(
            "create_emitter", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_particle_system(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``particle_system`` node (what a particle is and how it renders)."""
        return self._create_typed_node(
            "create_particle_system", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_volume(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``volume`` node (Tier 3; the V1 backend reports statistics only)."""
        return self._create_typed_node(
            "create_volume", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_force(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``force`` node (gravity, curl_noise, attractor, buoyancy, ...)."""
        return self._create_typed_node(
            "create_force", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_field(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``field`` node (sampled data; V1 evaluates ``noise`` fields only)."""
        return self._create_typed_node(
            "create_field", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_mesh(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``mesh`` node (Tier 0 renderable, or an emitter shape source)."""
        return self._create_typed_node(
            "create_mesh", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_curve(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``curve`` node (a path for emitters, trails, beams or motion)."""
        return self._create_typed_node(
            "create_curve", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_trail(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``trail`` node (Tier 0 ribbon following a source node)."""
        return self._create_typed_node(
            "create_trail", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_beam(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``beam`` node (Tier 0 analytic lightning or laser)."""
        return self._create_typed_node(
            "create_beam", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_light(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``light`` node (point, spot or area; lights the ground plane)."""
        return self._create_typed_node(
            "create_light", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_decal(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``decal`` node (Tier 0 sprite projected on the ground)."""
        return self._create_typed_node(
            "create_decal", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_material(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``material`` node (blend mode, shading, soft particles, ...)."""
        return self._create_typed_node(
            "create_material", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_event(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create an ``event`` node (on_spawn / on_death / on_collision / on_time ...)."""
        return self._create_typed_node(
            "create_event", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_noise(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``noise`` node (parameter provider for forces and textures)."""
        return self._create_typed_node(
            "create_noise", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_collider(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``collider`` node (plane, sphere, box, capsule, mesh or sdf)."""
        return self._create_typed_node(
            "create_collider", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_camera(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``camera`` node; render tools use the effect's camera by default."""
        return self._create_typed_node(
            "create_camera", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_post_effect(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``post_effect`` node (bloom, heat_haze, distortion ...).  Use sparingly."""
        return self._create_typed_node(
            "create_post_effect", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def create_texture(
        self,
        id: str | None = None,
        layer: str | None = None,
        parent: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        inputs: Mapping[str, str | Sequence[str]] | None = None,
        metadata: JsonDict | None = None,
        seed: int | None = None,
    ) -> JsonDict:
        """Create a ``texture`` node (procedural texture graph or file; baked at compile)."""
        return self._create_typed_node(
            "create_texture", id=id, layer=layer, parent=parent, parameters=parameters,
            inputs=inputs, metadata=metadata, seed=seed,
        )

    def delete_node(self, node_id: str) -> JsonDict:
        """Delete a node and strip references to it.  Returns ``{ok, dangling}``."""
        return self.call("delete_node", node_id=node_id)

    def duplicate_node(self, node_id: str, new_id: str | None = None) -> JsonDict:
        """Duplicate a node.  Returns ``{node}``."""
        return self.call("duplicate_node", **self._args(node_id=node_id, new_id=new_id))

    def set_node_property(
        self,
        node_id: str,
        enabled: bool | None = None,
        parent: str | None = None,
        layer: str | None = None,
        seed: int | None = None,
        metadata: JsonDict | None = None,
    ) -> JsonDict:
        """Update non-parameter node fields.  Returns ``{node}``."""
        return self.call(
            "set_node_property",
            **self._args(node_id=node_id, enabled=enabled, parent=parent, layer=layer, seed=seed, metadata=metadata),
        )

    def connect_nodes(self, from_: str | None = None, to: str = "", port: str = "", **kwargs: Any) -> JsonDict:
        """Connect ``from_`` into ``to``'s input ``port``.

        ``from`` is a Python keyword, so the parameter is spelled ``from_``
        here and sent as ``from`` on the wire; ``connect_nodes(**{"from": ...})``
        works too.  Appends on multi ports, replaces on single ports.  Returns
        ``{node, diagnostics}``.
        """
        source = kwargs.pop("from", from_)
        if kwargs:
            raise TypeError(f"connect_nodes() got unexpected keyword arguments: {sorted(kwargs)}")
        if not source or not to or not port:
            raise TypeError("connect_nodes() requires 'from' (or from_), 'to' and 'port'")
        return self.call("connect_nodes", **{"from": source, "to": to, "port": port})

    def disconnect_nodes(self, to: str = "", port: str = "", from_: str | None = None, **kwargs: Any) -> JsonDict:
        """Remove one reference from ``to.port``, or clear the port entirely.

        Returns ``{node}``.  As with :meth:`connect_nodes`, ``from`` is spelled
        ``from_`` and ``**{"from": ...}`` also works.  Omitting the source
        clears the whole port.
        """
        source = kwargs.pop("from", from_)
        if kwargs:
            raise TypeError(f"disconnect_nodes() got unexpected keyword arguments: {sorted(kwargs)}")
        if not to or not port:
            raise TypeError("disconnect_nodes() requires 'to' and 'port'")
        args: JsonDict = {"to": to, "port": port}
        if source is not None:
            args["from"] = source
        return self.call_with("disconnect_nodes", args)

    # =====================================================================
    # parameters
    # =====================================================================

    def set_parameter(self, node_id: str, name: str, value: JsonValue) -> JsonDict:
        """Set one constant parameter, clearing any keyframe track on it.

        Returns ``{node_id, name, value, diagnostics}``.
        """
        return self.call("set_parameter", node_id=node_id, name=name, value=value)

    def set_parameters(self, node_id: str, parameters: Mapping[str, JsonValue]) -> JsonDict:
        """Set several parameters at once.  Returns ``{node, diagnostics}``."""
        return self.call("set_parameters", node_id=node_id, parameters=dict(parameters))

    def get_parameter(self, node_id: str, name: str, time: float | None = None) -> JsonDict:
        """Read a parameter.  Returns ``{value, default, animated, track, spec}``.

        ``time`` samples an animated parameter at that effect time.
        """
        return self.call("get_parameter", **self._args(node_id=node_id, name=name, time=time))

    def reset_parameter(self, node_id: str, name: str) -> JsonDict:
        """Reset a parameter to its spec default.  Returns ``{ok}``."""
        return self.call("reset_parameter", node_id=node_id, name=name)

    def set_keyframe(
        self, node_id: str, name: str, time: float, value: JsonValue, interp: str = "linear"
    ) -> JsonDict:
        """Add or replace a keyframe on a parameter's effect-time track.

        ``interp`` is ``linear``, ``step`` or ``smooth``.  Returns ``{track}``.
        Keyframe tracks are effect-time animation; ``*_over_life`` curves are a
        different thing (normalised particle age) and must not be conflated.
        """
        return self.call("set_keyframe", node_id=node_id, name=name, time=time, value=value, interp=interp)

    def remove_keyframe(self, node_id: str, name: str, time: float) -> JsonDict:
        """Remove the keyframe at ``time``.  Returns ``{track}``."""
        return self.call("remove_keyframe", node_id=node_id, name=name, time=time)

    def clear_track(self, node_id: str, name: str) -> JsonDict:
        """Remove a parameter's whole keyframe track.  Returns ``{ok}``."""
        return self.call("clear_track", node_id=node_id, name=name)

    # =====================================================================
    # timeline
    # =====================================================================

    def set_timeline_phase(self, name: str, start: float, end: float) -> JsonDict:
        """Create or update a timeline phase in seconds.  Returns ``{timeline}``.

        Conventional names: ``anticipation``, ``activation``, ``peak``,
        ``sustain``, ``decay``; custom names are allowed.
        """
        return self.call("set_timeline_phase", name=name, start=start, end=end)

    def remove_timeline_phase(self, name: str) -> JsonDict:
        """Remove a timeline phase.  Returns ``{timeline}``."""
        return self.call("remove_timeline_phase", name=name)

    def get_timeline(self) -> JsonDict:
        """Return ``{duration, phases, bound_nodes:{phase:[ids]}}``."""
        return self.call("get_timeline")

    # =====================================================================
    # simulate
    # =====================================================================

    def simulate(self, time: float | None = None, fixed_dt: float | None = None) -> JsonDict:
        """Simulate from 0 to ``time`` (default: the effect duration).

        Returns ``{statistics}``.  Fixed timestep, no interpolation.
        """
        return self.call("simulate", **self._args(time=time, fixed_dt=fixed_dt))

    def simulate_range(self, start: float, end: float, sample_every: float | None = None) -> JsonDict:
        """Simulate and sample.  Returns ``{samples:[{time,total_alive,per_system}], statistics}``."""
        return self.call("simulate_range", **self._args(start=start, end=end, sample_every=sample_every))

    def step_simulation(self, frames: int = 1) -> JsonDict:
        """Advance the runtime by ``frames`` fixed steps.  Returns ``{time, frame, statistics}``."""
        return self.call("step_simulation", frames=frames)

    def reset_simulation(self) -> JsonDict:
        """Reset the runtime to t=0.  Returns ``{ok}``."""
        return self.call("reset_simulation")

    # =====================================================================
    # render
    # =====================================================================

    def render_frame(
        self,
        time: float,
        width: int | None = None,
        height: int | None = None,
        camera: Mapping[str, JsonValue] | None = None,
        settings: Mapping[str, JsonValue] | None = None,
        path: str | os.PathLike[str] | None = None,
        format: str = "png",
    ) -> JsonDict:
        """Render one frame to a file.

        ``camera`` is ``{position, target, up?, fov?}``; ``settings`` is any
        subset of ``RenderSettings`` (``bloom``, ``ground_plane``,
        ``background``, ``exposure``, ...).  Returns
        ``{path, time, render_statistics, image_stats}``.
        """
        return self.call(
            "render_frame",
            **self._args(
                time=time,
                width=width,
                height=height,
                camera=dict(camera) if camera is not None else None,
                settings=dict(settings) if settings is not None else None,
                path=str(path) if path is not None else None,
                format=format,
            ),
        )

    def render_preview(
        self,
        fps: float = 24,
        start: float = 0,
        end: float | None = None,
        width: int | None = None,
        height: int | None = None,
        camera: Mapping[str, JsonValue] | None = None,
        settings: Mapping[str, JsonValue] | None = None,
        contact_sheet: bool = True,
        video: bool = False,
        out_dir: str | os.PathLike[str] | None = None,
    ) -> JsonDict:
        """Render a frame sequence.  Returns ``{frames, contact_sheet?, video?, statistics}``."""
        return self.call(
            "render_preview",
            **self._args(
                fps=fps,
                start=start,
                end=end,
                width=width,
                height=height,
                camera=dict(camera) if camera is not None else None,
                settings=dict(settings) if settings is not None else None,
                contact_sheet=contact_sheet,
                video=video,
                out_dir=str(out_dir) if out_dir is not None else None,
            ),
        )

    def render_turntable(
        self,
        time: float,
        frames: int = 8,
        distance: float | None = None,
        height: float | None = None,
        width: int | None = None,
        height_px: int | None = None,
    ) -> JsonDict:
        """Render an orbit around the effect at one time.  Returns ``{frames, contact_sheet}``."""
        return self.call(
            "render_turntable",
            **self._args(
                time=time, frames=frames, distance=distance, height=height, width=width, height_px=height_px
            ),
        )

    def inspect_render(self, path: str | os.PathLike[str]) -> JsonDict:
        """Measure an image file.

        Returns ``{width, height, mean_luminance, max_luminance, coverage, bbox,
        dominant_colors:[{rgb, share}], hash}`` - the same metrics as
        :func:`aetherfx.evaluate.image_stats`.
        """
        return self.call("inspect_render", path=str(path))

    # =====================================================================
    # inspect
    # =====================================================================

    def inspect_graph(self, effect_id: str | None = None, verbose: bool = False) -> JsonDict:
        """Return the whole graph: name, duration, seed, timeline, layers, nodes, diagnostics."""
        return self.call("inspect_graph", **self._args(effect_id=effect_id, verbose=verbose))

    def inspect_node(self, node_id: str) -> JsonDict:
        """Return ``{node, spec, effective_parameters, resolved_inputs, consumers, tier, window, diagnostics}``."""
        return self.call("inspect_node", node_id=node_id)

    def inspect_statistics(self) -> JsonDict:
        """Return the last known ``{simulation, render, plan}`` statistics."""
        return self.call("inspect_statistics")

    def inspect_plan(self) -> JsonDict:
        """Return the compiled plan (``CompiledEffect::plan_json``)."""
        return self.call("inspect_plan")

    def validate_effect(self, effect_id: str | None = None) -> JsonDict:
        """Validate without compiling.  Returns ``{diagnostics}``."""
        return self.call("validate_effect", **self._args(effect_id=effect_id))

    # =====================================================================
    # evaluate
    # =====================================================================

    def compare_reference(
        self,
        reference_path: str | os.PathLike[str],
        render_path: str | os.PathLike[str] | None = None,
        time: float | None = None,
    ) -> JsonDict:
        """Compare a render against a reference image.

        Pass ``render_path`` or a ``time`` to render first.  Returns
        ``{coverage_iou, palette_distance, luminance_histogram_distance,
        centroid_offset, radial_profile_distance, score, notes}`` - the same
        metric set as :func:`aetherfx.evaluate.compare_images`.
        """
        return self.call(
            "compare_reference",
            **self._args(
                reference_path=str(reference_path),
                render_path=str(render_path) if render_path is not None else None,
                time=time,
            ),
        )

    def evaluate_effect(self, time: float | None = None) -> JsonDict:
        """Return ``{statistics, budgets, render_statistics, diagnostics, score_hints}``."""
        return self.call("evaluate_effect", **self._args(time=time))

    # =====================================================================
    # io
    # =====================================================================

    def save_effect(self, path: str | os.PathLike[str] | None = None) -> JsonDict:
        """Save the active effect as JSON.  Returns ``{path}``."""
        return self.call("save_effect", **self._args(path=str(path) if path is not None else None))

    def load_effect(self, path: str | os.PathLike[str]) -> JsonDict:
        """Load an effect document and make it active.  Returns ``{effect_id, effect, diagnostics}``."""
        return self.call("load_effect", path=str(path))

    def export_effect(
        self,
        format: str,
        path: str | os.PathLike[str],
        options: Mapping[str, JsonValue] | None = None,
    ) -> JsonDict:
        """Export as ``json``, ``flipbook`` or ``frames``.  Returns ``{path, files, manifest}``.

        ``flipbook`` options: ``fps``, ``columns``, ``width``, ``height``,
        ``start``, ``end``, ``camera``, ``settings``.
        """
        return self.call(
            "export_effect",
            **self._args(format=format, path=str(path), options=dict(options) if options is not None else None),
        )

    # =====================================================================
    # history
    # =====================================================================

    def undo(self) -> JsonDict:
        """Undo the last mutating tool call.  Returns ``{ok, remaining}``."""
        return self.call("undo")

    def redo(self) -> JsonDict:
        """Redo the last undone call.  Returns ``{ok, remaining}``."""
        return self.call("redo")
