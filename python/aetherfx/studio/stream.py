"""Live frame streaming for the studio's GPU viewer.

The viewer (three.js in the browser) renders what the deterministic engine
simulates. Frames travel over a WebSocket as one binary message each:

    [u32 little-endian header_length][header JSON utf-8][binary blob]

The header describes the blob with byte offsets so the client can create
typed-array views without copying. Numbers are little-endian; float arrays are
float32, index arrays uint32. Units: meters, Y up, right-handed, linear RGB.

Header (frame message)::

    {"type": "frame", "version": 1, "time": 1.25, "frame": 75, "fps": 60,
     "systems": [{"id": "flame_ps", "count": 1234, "render_mode": "billboard",
                  "blend": "additive", "material": "mat_fire", "sprite": "tex_puff",
                  "mesh": "", "mesh_variants": 1, "sprite_columns": 1, "sprite_rows": 1,
                  "sprite_fps": 0.0, "velocity_stretch": 0.08, "align_to_velocity": false,
                  "soft_particle_distance": 0.1, "sort": false,
                  "arrays": {"position": {"offset": 0, "dtype": "f32", "components": 3},
                             "velocity": {...}, "size": {"components": 1}, "rotation": {...},
                             "color": {"components": 4}, "emissive": {...}, "age_norm": {...},
                             "orientation": {"components": 4}   # mesh systems only (xyzw quaternion)
                             "scale3": {"components": 3},       # mesh systems only
                             "variant": {"dtype": "u32", "components": 1}}}],   # mesh systems only
     "lights": [{"id", "type", "position", "direction", "color", "intensity", "radius", "cone_angle"}],
     "decals": [{"id", "position", "normal", "size", "rotation_deg", "color", "opacity", "emissive",
                 "circle", "blend", "texture", "material"}],
     "mesh_instances": [{"id", "mesh", "material", "transform" (16, column-major), "color", "emissive", "visible"}],
     "volumes": [{"id", "mode", "shape", "transform" (16, column-major), "bounds_min", "bounds_max",
                  "radius", "height", "density", "emission", "color", "color_hot", "filament_scale",
                  "strands", "carve", "softness", "spiral_arms", "arm_sharpness", "twist", "spin",
                  "climb", "scatter", "march_steps", "seed", "time"}],   # plain JSON, nothing in the blob
     "beams": [{"id", "width", "color", "emissive", "blend", "material", "pulse_phase",
                "core_width", "glow_width",                     # cross-section, fractions of "width"
                # A bolt is hundreds of short paths, so the per-path metadata lives in the blob
                # too: one contiguous vertex run (5 f32 per vertex: xyz, width, intensity) plus
                # one record per path (4 f32: first vertex, vertex count, branch depth, fade).
                # "vertices"/"offset" are byte offsets, "total" vertices and "count" paths.
                "paths":  {"vertices", "total", "offset", "count"},
                "ghosts": {"vertices", "total", "offset", "count"},  # afterglow copies, same layout
                "flares": [{"position", "radius", "intensity"}]}],  # plain JSON, nothing in the blob
     "trails": [{"id", "blend", "material", "twist_deg",
                 "ribbons": [{"offset", "count"}]}],            # 12 f32 per vertex: pos3, width, age_norm, u, color4, opacity, emissive
     "camera": {"position", "target", "up", "fov"} | null,
     "post_effects": [{"id", "post_type", "intensity", "threshold", "radius", "frequency"}]}

Resources message (JSON text, sent once after "open" and on reload)::

    {"type": "resources", "effect": {"name", "duration", "fixed_dt", "seed"},
     "textures": {"tex_puff": {"url": "/api/stream/texture/<id>.png", "width", "height", "frames", "frame_width"}},
     "meshes": {"rock_mesh": {"variants": 8, "url": "/api/stream/mesh/<id>.json"}},   # json: {positions, normals, uvs, indices, variants:[{...}]}
     "materials": {"mat_fire": {...MaterialDesc fields...}},
     "render_settings": {...effect.metadata.render_settings...},
     "camera": {...} | null, "timeline": {...}}

Client -> server (JSON text): {"type":"open"} (re-open the active effect), {"type":"play","fps":60},
{"type":"pause"}, {"type":"seek","time":t}, {"type":"step","frames":1}, {"type":"resources"}.
"""

from __future__ import annotations

import json
import math
import struct
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

# 2: a beam's per-path metadata moved from the JSON header into the blob.  A
# lightning AOE is ~620 paths a frame, which was 40 KB of header (and 620
# short-lived objects on the client) every frame just to say where each one is.
STREAM_VERSION = 2

#: One beam path record in the blob: first vertex, vertex count, branch depth, fade.
BEAM_PATH_RECORD = 4

#: One beam vertex in the blob: xyz, width (m), intensity.
BEAM_VERTEX_STRIDE = 5


@dataclass
class ArrayRef:
    data: np.ndarray  # shape (N, components), float32 or uint32
    dtype: str = "f32"

    @property
    def components(self) -> int:
        return 1 if self.data.ndim == 1 else int(self.data.shape[1])


@dataclass
class SystemFrame:
    id: str
    render_mode: str = "billboard"
    blend: str = "additive"
    material: str = ""
    sprite: str = ""
    mesh: str = ""
    mesh_variants: int = 1
    sprite_columns: int = 1
    sprite_rows: int = 1
    sprite_fps: float = 0.0
    velocity_stretch: float = 0.0
    align_to_velocity: bool = False
    soft_particle_distance: float = 0.1
    sort: bool = False
    arrays: dict[str, ArrayRef] = field(default_factory=dict)

    @property
    def count(self) -> int:
        pos = self.arrays.get("position")
        return 0 if pos is None else int(pos.data.shape[0])


@dataclass
class Frame:
    time: float
    frame: int
    systems: list[SystemFrame] = field(default_factory=list)
    lights: list[dict[str, Any]] = field(default_factory=list)
    decals: list[dict[str, Any]] = field(default_factory=list)
    mesh_instances: list[dict[str, Any]] = field(default_factory=list)
    volumes: list[dict[str, Any]] = field(default_factory=list)        # procedural raymarched volumes (docs/VOLUMES.md)
    beams: list[dict[str, Any]] = field(default_factory=list)          # {..., "paths"/"ghosts": [{"vertices": (N,5), "depth", "fade"}]}
    trails: list[dict[str, Any]] = field(default_factory=list)         # {..., "ribbons": [np.ndarray (N,12)]}
    camera: dict[str, Any] | None = None
    post_effects: list[dict[str, Any]] = field(default_factory=list)


_NO_BEAM_VERTICES = np.zeros((0, BEAM_VERTEX_STRIDE), dtype=np.float32)


def beam_is_dark(beam: dict[str, Any]) -> bool:
    """True when a beam cannot put a single photon on the screen this frame.

    A bolt keeps re-rolling its fractal paths for the whole of its node window,
    including the dark gaps between flashes - on the built-in Lightning AOE that
    is **half of every frame's beam geometry**, several hundred kilobytes a
    second of paths whose pixels are guaranteed to be discarded. The renderers
    all shade a beam as ``blend(rgb * emissive * gain, alpha)``
    (docs/RUNTIME.md section 11), so two cases are exactly, not approximately,
    nothing:

    * ``color[3] == 0`` - every fragment discards, whatever the blend mode;
    * ``emissive == 0`` **and** additive blending - the colour term is zero and
      additive leaves the destination untouched. Under alpha blending a black
      beam would still darken what is behind it, so that case is left alone.

    ``width == 0`` zeroes every vertex width too, but a beam's ``impact_flare``
    has its own radius and still draws, so that one only empties the paths (see
    :func:`encode_frame`).
    """
    color = beam.get("color") or (1.0, 1.0, 1.0, 1.0)
    if len(color) > 3 and float(color[3]) == 0.0:
        return True
    return float(beam.get("emissive", 0.0)) == 0.0 and beam.get("blend") == "additive"


def encode_frame(frame: Frame, fps: float = 60.0) -> bytes:
    """Pack a Frame into one binary WebSocket message (see module docstring)."""
    chunks: list[bytes] = []
    offset = 0

    def put(arr: np.ndarray) -> int:
        nonlocal offset
        raw = np.ascontiguousarray(arr).tobytes()
        start = offset
        chunks.append(raw)
        offset += len(raw)
        # keep 4-byte alignment for typed-array views
        pad = (-offset) % 4
        if pad:
            chunks.append(b"\0" * pad)
            offset += pad
        return start

    systems_json = []
    for s in frame.systems:
        arrays_json = {}
        for name, ref in s.arrays.items():
            data = ref.data.astype(np.uint32 if ref.dtype == "u32" else np.float32, copy=False)
            arrays_json[name] = {"offset": put(data), "dtype": ref.dtype, "components": ref.components}
        systems_json.append({
            "id": s.id, "count": s.count, "render_mode": s.render_mode, "blend": s.blend,
            "material": s.material, "sprite": s.sprite, "mesh": s.mesh, "mesh_variants": s.mesh_variants,
            "sprite_columns": s.sprite_columns, "sprite_rows": s.sprite_rows, "sprite_fps": s.sprite_fps,
            "velocity_stretch": s.velocity_stretch, "align_to_velocity": s.align_to_velocity,
            "soft_particle_distance": s.soft_particle_distance, "sort": s.sort, "arrays": arrays_json,
        })
    beams_json = []
    for b in frame.beams:
        if beam_is_dark(b):
            continue
        # A zero-width bolt has zero-width vertices, so its ribbons are degenerate;
        # its impact flare has its own radius and still draws, so the entry stays.
        alive = float(b.get("width", 0.0)) != 0.0

        def group_of(key: str) -> dict[str, int]:
            """One beam's live paths or its ghosts, entirely in the blob.

            A fractal bolt is dozens of paths and every one of them used to cost
            a JSON object in the header; at 44 bolts a frame that was more header
            than the rest of the message put together. The vertices of a group go
            out as one contiguous run and the per-path records (first vertex,
            vertex count, branch depth, fade) as a second array beside them, so
            the client reads two typed-array views per beam and no objects.
            """
            paths = b.get(key, []) if alive else []
            blocks = [np.ascontiguousarray(p["vertices"], dtype=np.float32).reshape(-1, BEAM_VERTEX_STRIDE)
                      for p in paths]
            records = np.empty((len(blocks), BEAM_PATH_RECORD), dtype=np.float32)
            first = 0
            for i, block in enumerate(blocks):
                records[i] = (first, len(block), int(paths[i].get("depth", 0)), float(paths[i].get("fade", 1.0)))
                first += len(block)
            vertices = np.concatenate(blocks) if blocks else _NO_BEAM_VERTICES
            return {"vertices": put(vertices), "total": int(first),
                    "offset": put(records), "count": len(blocks)}
        rest = {k: v for k, v in b.items() if k not in ("paths", "ghosts")}
        beams_json.append({**rest, "paths": group_of("paths"), "ghosts": group_of("ghosts")})
    trails_json = []
    for t in frame.trails:
        ribbons = [{"offset": put(np.asarray(r, dtype=np.float32)), "count": int(len(r))} for r in t.get("ribbons", [])]
        trails_json.append({**{k: v for k, v in t.items() if k != "ribbons"}, "ribbons": ribbons})
    header = {
        "type": "frame", "version": STREAM_VERSION, "time": frame.time, "frame": frame.frame, "fps": fps,
        "systems": systems_json, "lights": frame.lights, "decals": frame.decals,
        "mesh_instances": frame.mesh_instances, "volumes": frame.volumes,
        "beams": beams_json, "trails": trails_json,
        "camera": frame.camera, "post_effects": frame.post_effects,
    }
    head = json.dumps(header, separators=(",", ":")).encode("utf-8")
    # Pad the header with JSON-insignificant spaces so the blob starts on a
    # 4-byte boundary: typed-array views need it, and the whole point of the
    # byte offsets is that the client does not have to copy.
    head += b" " * (-len(head) % 4)
    return struct.pack("<I", len(head)) + head + b"".join(chunks)


class FrameSource(Protocol):
    """A simulation the viewer can play. Implementations: MockFrameSource (synthetic),
    NativeFrameSource (libaetherfx via ctypes, the real engine)."""

    def open(self, effect_json: dict[str, Any]) -> None: ...
    def resources(self) -> dict[str, Any]: ...
    def duration(self) -> float: ...
    def fixed_dt(self) -> float: ...
    def frame_at(self, time: float) -> Frame: ...
    def close(self) -> None: ...


class MockFrameSource:
    """Synthetic swirling particle cloud for developing the viewer without the engine."""

    def __init__(self, count: int = 4000) -> None:
        self.count = count
        self._effect: dict[str, Any] = {"name": "Mock", "duration": 3.0}
        self._rng = np.random.default_rng(7)
        self._seeds = self._rng.random((count, 4)).astype(np.float32)
        self.lock = threading.Lock()

    def open(self, effect_json: dict[str, Any]) -> None:
        self._effect = {"name": effect_json.get("name", "Mock"), "duration": float(effect_json.get("duration", 3.0))}

    def resources(self) -> dict[str, Any]:
        return {"type": "resources", "effect": {"name": self._effect["name"], "duration": self.duration(),
                                                "fixed_dt": self.fixed_dt(), "seed": 1},
                "textures": {}, "meshes": {}, "materials": {"mock": {"blend": "additive", "shading": "unlit",
                                                                    "emissive_intensity": 2.0}},
                "render_settings": {}, "camera": {"position": [0, 1.5, 5], "target": [0, 1, 0], "up": [0, 1, 0], "fov": 45},
                "timeline": {"phases": []}}

    def duration(self) -> float:
        return self._effect["duration"]

    def fixed_dt(self) -> float:
        return 1.0 / 60.0

    def frame_at(self, time: float) -> Frame:
        t = float(time)
        s = self._seeds
        life = 1.5
        age = (t + s[:, 0] * life) % life
        u = age / life
        ang = s[:, 1] * math.tau + u * 4.0
        rad = 0.2 + 0.8 * u
        pos = np.stack([np.cos(ang) * rad, 0.2 + u * 2.2 + 0.2 * np.sin(ang * 3), np.sin(ang) * rad], axis=1).astype(np.float32)
        vel = np.stack([-np.sin(ang) * rad, np.full_like(u, 1.5), np.cos(ang) * rad], axis=1).astype(np.float32)
        size = (0.12 + 0.25 * np.sin(u * math.pi)).astype(np.float32)
        rot = (s[:, 2] * math.tau + t).astype(np.float32)
        color = np.stack([np.ones_like(u), 0.9 - 0.7 * u, 0.4 - 0.4 * u, 1.0 - u], axis=1).astype(np.float32)
        emissive = (3.0 * (1.0 - u)).astype(np.float32)
        system = SystemFrame(id="mock_ps", render_mode="billboard", blend="additive", material="mock",
                             arrays={"position": ArrayRef(pos), "velocity": ArrayRef(vel), "size": ArrayRef(size),
                                     "rotation": ArrayRef(rot), "color": ArrayRef(color),
                                     "emissive": ArrayRef(emissive), "age_norm": ArrayRef(u.astype(np.float32))})
        light = {"id": "glow", "type": "point", "position": [0.0, 1.2, 0.0], "direction": [0, -1, 0],
                 "color": [1.0, 0.6, 0.25, 1.0], "intensity": 6.0 + 2.0 * math.sin(t * 9.0), "radius": 5.0, "cone_angle": 45.0}
        return Frame(time=t, frame=int(round(t / self.fixed_dt())), systems=[system], lights=[light],
                     camera={"position": [0, 1.5, 5], "target": [0, 1, 0], "up": [0, 1, 0], "fov": 45})

    def close(self) -> None:
        pass


def decode_header(message: bytes) -> tuple[dict[str, Any], memoryview]:
    """Server-side helper for tests: split a frame message into (header, blob)."""
    (n,) = struct.unpack_from("<I", message, 0)
    header = json.loads(message[4:4 + n].decode("utf-8"))
    return header, memoryview(message)[4 + n:]
