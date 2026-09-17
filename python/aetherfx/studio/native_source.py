"""The studio viewer's real-engine frame source.

:class:`NativeFrameSource` is the :class:`~aetherfx.studio.stream.FrameSource`
the viewer gets when ``libaetherfx`` is built: it compiles the studio's active
effect through :mod:`aetherfx.native` and plays the same deterministic
fixed-step simulation the C++ studio and a shipping game engine run.  When the
library is missing this module raises :class:`~aetherfx.native.NativeUnavailable`
at import, which is exactly what
:func:`~aetherfx.studio.stream_server.create_frame_source` probes for before
falling back to :class:`~aetherfx.studio.stream.MockFrameSource`.

The adapter is thin on purpose - :mod:`aetherfx.native` already copies every
buffer out of the library, so all that is left is renaming a few fields into the
wire vocabulary documented in :mod:`aetherfx.studio.stream`:

===========================  ==========================================
C / :mod:`aetherfx.native`   stream / viewer
===========================  ==========================================
``material_id``              ``material``
``texture_id``               ``texture``
``mesh_id``                  ``mesh``
``cone_angle_deg``           ``cone_angle``
``color`` + ``opacity``      ``color`` (rgb from colour, alpha from opacity)
``custom0``                  ``age_norm``
``age``                      ``age`` (seconds since birth)
===========================  ==========================================

Volumes are the one place the adapter drops something: a ``mode: simulation``
volume is the V1 fluid stub and carries no field, so it never reaches the wire
(docs/VOLUMES.md). Procedural volumes pass through as plain JSON.

One source owns one runtime, and a runtime must be driven by one thread at a
time, so every public method takes the source's lock: the server simulates in a
threadpool while the event loop keeps answering commands.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import numpy as np

from ..native import Compiled, Effect, NativeError, Runtime, load_library
from .stream import ArrayRef, Frame, SystemFrame

__all__ = ["NativeFrameSource", "effect_camera"]

# Raises NativeUnavailable (an ImportError) when libaetherfx cannot be loaded,
# so `import aetherfx.studio.native_source` is the availability probe.
load_library()

#: The studio and the viewer both run at 60 Hz.
DEFAULT_FIXED_DT = 1.0 / 60.0

#: Camera parameter defaults, from docs/VOCABULARY.md's ``camera`` node.
CAMERA_DEFAULTS: dict[str, Any] = {
    "position": [0.0, 1.5, 5.0],
    "target": [0.0, 1.0, 0.0],
    "up": [0.0, 1.0, 0.0],
    "fov": 45.0,
}


def _plain(value: Any) -> Any:
    """Unwrap an animatable parameter (``{"value": v, "track": [...]}``) to ``v``."""
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _floats(value: Any, fallback: list[float]) -> list[float]:
    value = _plain(value)
    if isinstance(value, (list, tuple)) and len(value) == len(fallback):
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return list(fallback)
    return list(fallback)


def _float(value: Any, fallback: float) -> float:
    try:
        return float(_plain(value))
    except (TypeError, ValueError):
        return fallback


def _flat(array: np.ndarray) -> list[float] | list[int]:
    """A JSON-serialisable flat list, the shape the mesh route wants."""
    return np.ascontiguousarray(array).ravel().tolist()


def effect_camera(effect_json: dict[str, Any]) -> dict[str, Any] | None:
    """The first enabled ``camera`` node's framing, or ``None``.

    Animated parameters are read at their constant ``value``; the viewer gets a
    starting framing, not a camera animation (the per-frame camera comes from
    the runtime).
    """
    for node in effect_json.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "camera":
            continue
        if node.get("enabled") is False:
            continue
        parameters = node.get("parameters") or {}
        return {
            "position": _floats(parameters.get("position"), CAMERA_DEFAULTS["position"]),
            "target": _floats(parameters.get("target"), CAMERA_DEFAULTS["target"]),
            "up": _floats(parameters.get("up"), CAMERA_DEFAULTS["up"]),
            "fov": _float(parameters.get("fov"), CAMERA_DEFAULTS["fov"]),
        }
    return None


class NativeFrameSource:
    """Plays an effect through ``libaetherfx`` for the studio's GPU viewer."""

    def __init__(self, fixed_dt: float = DEFAULT_FIXED_DT) -> None:
        load_library()
        self.lock = threading.RLock()
        self._requested_dt = float(fixed_dt) if fixed_dt > 0 else DEFAULT_FIXED_DT
        self._effect_json: dict[str, Any] = {}
        self._effect: Effect | None = None
        self._compiled: Compiled | None = None
        self._runtime: Runtime | None = None
        self._resources: dict[str, Any] | None = None
        self._duration = 0.0
        self._fixed_dt = self._requested_dt

    # -- lifecycle --------------------------------------------------------

    def open(self, effect_json: dict[str, Any]) -> None:
        """Load, compile and instantiate ``effect_json``.

        Raises :class:`~aetherfx.native.NativeError` when the document does not
        parse or the compile reports errors; the message carries the
        diagnostics, because "it did not compile" without them is useless in a
        studio log.
        """
        document = dict(effect_json or {})
        with self.lock:
            self.close()
            self._effect_json = document
            effect = Effect.from_json(json.dumps(document))
            try:
                compiled = effect.compile(self._requested_dt)
            except Exception:
                effect.close()
                raise
            try:
                if not compiled.ok:
                    raise NativeError(-5, _diagnostics_message(compiled.diagnostics), "compile")
                runtime = compiled.runtime()
            except Exception:
                compiled.close()
                effect.close()
                raise
            self._effect = effect
            self._compiled = compiled
            self._runtime = runtime
            self._duration = max(0.0, effect.duration)
            self._fixed_dt = runtime.fixed_dt or self._requested_dt
            self._resources = self._build_resources()

    def close(self) -> None:
        """Free the runtime, the compiled effect and the document.  Idempotent."""
        with self.lock:
            for name in ("_runtime", "_compiled", "_effect"):
                handle = getattr(self, name)
                setattr(self, name, None)
                if handle is not None:
                    handle.close()
            self._resources = None

    # -- description ------------------------------------------------------

    def duration(self) -> float:
        with self.lock:
            return self._duration

    def fixed_dt(self) -> float:
        with self.lock:
            return self._fixed_dt

    def resources(self) -> dict[str, Any]:
        """The baked resources, built once per :meth:`open`."""
        with self.lock:
            if self._resources is None:
                raise NativeError(-6, "no effect is open", "resources")
            return self._resources

    # -- playback ---------------------------------------------------------

    def frame_at(self, time: float) -> Frame:
        """Simulate to ``time`` and pack the result for the viewer.

        The runtime never steps backwards, so scrubbing back rewinds and
        replays - which is also what keeps the picture deterministic.
        """
        target = max(0.0, float(time))
        with self.lock:
            runtime = self._runtime
            if runtime is None:
                raise NativeError(-6, "no effect is open", "frame_at")
            if target < runtime.time - 1e-9:
                runtime.reset()
            runtime.simulate_to(target)
            snapshot = runtime.frame()
        return _to_frame(snapshot)

    # -- resources --------------------------------------------------------

    def _build_resources(self) -> dict[str, Any]:
        compiled = self._compiled
        effect = self._effect
        if compiled is None or effect is None:  # pragma: no cover - open() guarantees both
            raise NativeError(-6, "no effect is open", "resources")

        textures = {
            texture.id: {
                "width": texture.width,
                "height": texture.height,
                "frames": texture.frames,
                "frame_width": texture.frame_width,
                "png": texture.png_bytes(),
            }
            for texture in compiled.textures
        }

        meshes: dict[str, Any] = {}
        for base in compiled.mesh_bases():
            variants = [_mesh_block(mesh) for mesh in compiled.mesh_variants(base)]
            if not variants:  # pragma: no cover - mesh_bases() comes from the meshes
                continue
            meshes[base] = {
                # A list, because stream_server.mesh_payload() reads the per-variant
                # geometry from here; public_resources() turns it into the count the
                # viewer sees.  variants[k] is the mesh a particle's variant == k wants.
                "variants": variants,
                "variants_data": variants,
                "variant_count": len(variants),
                **variants[0],
            }

        return {
            "type": "resources",
            "effect": {
                "name": effect.name,
                "duration": self._duration,
                "fixed_dt": self._fixed_dt,
                "seed": int(self._effect_json.get("seed") or 0),
            },
            "textures": textures,
            "meshes": meshes,
            "materials": {material["id"]: material for material in compiled.materials},
            "render_settings": _render_settings(self._effect_json),
            "camera": effect_camera(self._effect_json),
            "timeline": self._effect_json.get("timeline") or {"phases": []},
        }

    # -- context manager --------------------------------------------------

    def __enter__(self) -> "NativeFrameSource":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# =========================================================================
# translation
# =========================================================================


def _diagnostics_message(diagnostics: dict[str, Any]) -> str:
    """"compile failed: E012 unknown node ..." - the errors, not just a count."""
    items = [item for item in (diagnostics.get("items") or []) if item.get("severity") == "error"]
    if not items:
        items = list(diagnostics.get("items") or [])
    detail = "; ".join(
        " ".join(str(part) for part in (item.get("code"), item.get("node"), item.get("message")) if part)
        for item in items[:8]
    )
    count = diagnostics.get("errors", len(items))
    return f"compile failed with {count} error(s): {detail}" if detail else f"compile failed with {count} error(s)"


def _render_settings(effect_json: dict[str, Any]) -> dict[str, Any]:
    metadata = effect_json.get("metadata")
    settings = metadata.get("render_settings") if isinstance(metadata, dict) else None
    return settings if isinstance(settings, dict) else {}


def _mesh_block(mesh: Any) -> dict[str, list[Any]]:
    """One baked mesh as flat JSON lists."""
    return {
        "positions": _flat(mesh.positions),
        "normals": _flat(mesh.normals),
        "uvs": _flat(mesh.uvs),
        "indices": _flat(mesh.indices),
    }


def _rgba(system: Any) -> np.ndarray:
    """``(N, 4)`` draw colour: rgb from ``color``, alpha from ``opacity``.

    The C ``color`` array's own alpha is unused (docs/ENGINE_INTEGRATION.md
    section 4); the alpha to draw with is the per-particle opacity.
    """
    color = system.arrays["color"]
    opacity = system.arrays["opacity"]
    out = np.empty((system.count, 4), dtype=np.float32)
    out[:, :3] = color[:, :3]
    out[:, 3] = opacity
    return out


def _to_frame(snapshot: Any) -> Frame:
    """A :class:`~aetherfx.native.RuntimeFrame` in the viewer's vocabulary."""
    systems = []
    for system in snapshot.systems:
        arrays = {
            "position": ArrayRef(system.arrays["position"]),
            "velocity": ArrayRef(system.arrays["velocity"]),
            "size": ArrayRef(system.arrays["size"]),
            "rotation": ArrayRef(system.arrays["rotation"]),
            "color": ArrayRef(_rgba(system)),
            "emissive": ArrayRef(system.arrays["emissive"]),
            "age_norm": ArrayRef(system.arrays["custom0"]),
        }
        if "age" in system.arrays:
            # seconds since birth: lets the viewer play sprite_fps flipbooks at the authored rate
            arrays["age"] = ArrayRef(system.arrays["age"])
        if system.is_mesh:
            arrays["orientation"] = ArrayRef(system.arrays["orientation"])
            arrays["scale3"] = ArrayRef(system.arrays["scale3"])
            arrays["variant"] = ArrayRef(system.arrays["variant"], dtype="u32")
        systems.append(
            SystemFrame(
                id=system.id,
                render_mode=system.render_mode,
                blend=system.blend,
                material=system.material_id,
                sprite=system.sprite_id,
                mesh=system.mesh_id,
                mesh_variants=system.mesh_variants,
                sprite_columns=system.sprite_columns,
                sprite_rows=system.sprite_rows,
                sprite_fps=system.sprite_fps,
                velocity_stretch=system.velocity_stretch,
                align_to_velocity=system.align_to_velocity,
                soft_particle_distance=system.soft_particle_distance,
                sort=system.sort,
                arrays=arrays,
            )
        )

    lights = [
        {
            "id": light["id"],
            "type": light["type"],
            "position": light["position"],
            "direction": light["direction"],
            "color": light["color"],
            "intensity": light["intensity"],
            "radius": light["radius"],
            "cone_angle": light["cone_angle_deg"],
        }
        for light in snapshot.lights
    ]
    decals = [
        {
            "id": decal["id"],
            "position": decal["position"],
            "normal": decal["normal"],
            "size": decal["size"],
            "rotation_deg": decal["rotation_deg"],
            "color": decal["color"],
            "opacity": decal["opacity"],
            "emissive": decal["emissive"],
            "circle": decal["circle"],
            "blend": decal["blend"],
            "texture": decal["texture_id"],
            "material": decal["material_id"],
        }
        for decal in snapshot.decals
    ]
    mesh_instances = [
        {
            "id": instance["id"],
            "mesh": instance["mesh_id"],
            "material": instance["material_id"],
            "transform": instance["transform"],
            "color": instance["color"],
            "emissive": instance["emissive"],
            "visible": instance["visible"],
        }
        for instance in snapshot.mesh_instances
    ]
    # Volumes travel as plain JSON: a procedural volume is ~25 scalars, so there is
    # nothing worth putting in the binary blob (docs/VOLUMES.md).
    volumes = [
        {
            "id": volume["id"],
            "mode": volume["mode"],
            "shape": volume["shape"],
            "transform": volume["transform"],
            "bounds_min": volume["bounds_min"],
            "bounds_max": volume["bounds_max"],
            "radius": volume["radius"],
            "height": volume["height"],
            "density": volume["density"],
            "emission": volume["emission"],
            "color": volume["color"],
            "color_hot": volume["color_hot"],
            "filament_scale": volume["filament_scale"],
            "strands": volume["strands"],
            "carve": volume["carve"],
            "softness": volume["softness"],
            "spiral_arms": volume["spiral_arms"],
            "arm_sharpness": volume["arm_sharpness"],
            "twist": volume["twist"],
            "spin": volume["spin"],
            "climb": volume["climb"],
            "scatter": volume["scatter"],
            "march_steps": volume["march_steps"],
            "seed": volume["seed"],
            "time": volume["time"],
        }
        for volume in snapshot.volumes
        if volume["mode"] != "simulation"
    ]
    beams = [
        {
            "id": beam["id"],
            "width": beam["width"],
            "color": beam["color"],
            "emissive": beam["emissive"],
            "blend": beam["blend"],
            "material": beam["material_id"],
            "pulse_phase": beam["pulse_phase"],
            "polylines": beam["polylines"],
        }
        for beam in snapshot.beams
    ]
    trails = [
        {
            "id": trail["id"],
            "blend": trail["blend"],
            "material": trail["material_id"],
            "twist_deg": 0.0,
            "ribbons": trail["ribbons"],
        }
        for trail in snapshot.trails
    ]
    camera = snapshot.camera
    if camera is not None:
        camera = {
            "position": camera["position"],
            "target": camera["target"],
            "up": camera["up"],
            "fov": camera["fov"],
        }
    return Frame(
        time=snapshot.time,
        frame=snapshot.frame_index,
        systems=systems,
        lights=lights,
        decals=decals,
        mesh_instances=mesh_instances,
        volumes=volumes,
        beams=beams,
        trails=trails,
        camera=camera,
        post_effects=[],
    )
