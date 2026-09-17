"""The real engine in-process: a ctypes binding for ``libaetherfx``.

``src/capi/include/aetherfx/aetherfx.h`` is the contract - 80 C99 functions, a
handful of tag-only plain-data structs and library-owned memory.  This module
mirrors it field for field and then puts a small RAII layer on top so Python
code never sees a raw pointer:

    from aetherfx.native import Effect

    with Effect.from_file("examples/effects/fireball.json") as effect:
        compiled = effect.compile(fixed_dt=1.0 / 60.0)
        runtime = compiled.runtime()
        runtime.simulate_to(1.0)
        frame = runtime.frame()          # numpy copies, safe past the next step

Three rules from the header drive the design:

* **The library owns every pointer.**  Frame arrays die on the next
  ``step``/``simulate_to``/``reset`` of that runtime, so :meth:`Runtime.frame`
  hands back numpy *copies*, never views.  Resource arrays
  (:class:`Texture`, :class:`Mesh`) die with the :class:`Compiled` they came
  from and are copied for the same reason.
* **Failures are statuses, not exceptions.**  Every failing call is turned into
  a :class:`NativeError` carrying the status code and
  ``aetherfx_last_error()``.
* **One handle, one thread at a time.**  Handles are independent; this module
  adds no locking of its own (:class:`~aetherfx.studio.native_source.NativeFrameSource`
  does, because it owns one runtime shared by a socket).

The library is found through ``$AETHERFX_LIBRARY``, then the repository's
``build/src/capi`` and ``build-capi/src/capi`` directories, then the platform
loader.  When it is nowhere to be found, :func:`load_library` raises
:class:`NativeUnavailable` (an :class:`ImportError`) and :func:`is_available`
returns ``False``, so optional callers can probe without a try/except.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import sys
import threading
from ctypes import POINTER, byref, c_char_p, c_double, c_float, c_int, c_size_t, c_uint8, c_uint32, c_uint64, c_void_p
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator

import numpy as np

__all__ = [
    "ABI_VERSION",
    "BLEND_MODES",
    "Compiled",
    "Effect",
    "LIBRARY_ENV_VAR",
    "LIGHT_TYPES",
    "Mesh",
    "NativeError",
    "NativeUnavailable",
    "ParticleSystemFrame",
    "RENDER_MODES",
    "RuntimeFrame",
    "Runtime",
    "SHADING_MODES",
    "Texture",
    "find_library",
    "is_available",
    "library_candidates",
    "load_library",
    "version",
]

#: Environment variable consulted before any filesystem search.
LIBRARY_ENV_VAR = "AETHERFX_LIBRARY"

#: ``AETHERFX_ABI_VERSION`` in the header this module was written against.
ABI_VERSION = 1

#: Status code -> name, for error messages (``enum aetherfx_status``).
STATUS_NAMES: dict[int, str] = {
    0: "OK",
    -1: "INVALID_ARGUMENT",
    -2: "OUT_OF_RANGE",
    -3: "INVALID_JSON",
    -4: "VALIDATION",
    -5: "COMPILE",
    -6: "RUNTIME",
    -7: "IO",
    -8: "BUFFER_TOO_SMALL",
    -9: "UNSUPPORTED",
    -10: "INTERNAL",
}

OK = 0
ERROR_VALIDATION = -4
ERROR_BUFFER_TOO_SMALL = -8

#: ``enum aetherfx_blend_mode`` / ``render_mode`` / ``shading`` / ``light_type``.
BLEND_MODES: tuple[str, ...] = ("additive", "alpha", "premultiplied")
RENDER_MODES: tuple[str, ...] = ("billboard", "stretched_billboard", "mesh", "ribbon", "none")
SHADING_MODES: tuple[str, ...] = ("unlit", "lit")
LIGHT_TYPES: tuple[str, ...] = ("point", "spot", "area")

#: Render modes whose particles carry orientation / scale3 / variant.
MESH_RENDER_MODES = frozenset({"mesh"})


# =========================================================================
# errors
# =========================================================================


class NativeUnavailable(ImportError):
    """``libaetherfx`` could not be found or loaded.

    An :class:`ImportError` on purpose: callers treat the native engine as an
    optional import and fall back to something else.
    """


class NativeError(RuntimeError):
    """A failing C call: ``code`` is an ``aetherfx_status``, ``message`` is
    ``aetherfx_last_error()`` at the moment of the failure."""

    def __init__(self, code: int, message: str, operation: str = "") -> None:
        self.code = int(code)
        self.message = message or "(no message)"
        self.operation = operation
        self.status = STATUS_NAMES.get(self.code, f"STATUS({self.code})")
        where = f"{operation}: " if operation else ""
        super().__init__(f"{where}{self.status}: {self.message}")


# =========================================================================
# structs (mirror src/capi/include/aetherfx/aetherfx.h exactly)
# =========================================================================


class ControlInfo(ctypes.Structure):
    """``struct aetherfx_control_info``: one named numeric knob on an effect."""

    _fields_ = [
        ("id", c_char_p),
        ("label", c_char_p),
        ("group", c_char_p),
        ("unit", c_char_p),
        ("min", c_double),
        ("max", c_double),
        ("default_value", c_double),
        ("value", c_double),
        ("step", c_double),
        ("binding_count", c_int),
    ]


class TextureInfo(ctypes.Structure):
    """``struct aetherfx_texture_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("width", c_int),
        ("height", c_int),
        ("frames", c_int),
        ("frame_width", c_int),
        ("channels", c_int),
    ]


class MeshInfo(ctypes.Structure):
    """``struct aetherfx_mesh_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("vertex_count", c_size_t),
        ("index_count", c_size_t),
        ("has_normals", c_int),
        ("has_uvs", c_int),
    ]


class MaterialInfo(ctypes.Structure):
    """``struct aetherfx_material``."""

    _fields_ = [
        ("id", c_char_p),
        ("blend", c_int),
        ("shading", c_int),
        ("base_color", c_float * 4),
        ("opacity", c_float),
        ("emissive_color", c_float * 4),
        ("emissive_intensity", c_float),
        ("fresnel_power", c_float),
        ("dissolve", c_float),
        ("erosion", c_float),
        ("soft_particle", c_int),
        ("depth_fade", c_float),
        ("base_texture", c_char_p),
        ("noise_texture", c_char_p),
    ]


class ParticleSystemInfo(ctypes.Structure):
    """``struct aetherfx_particle_system_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("count", c_size_t),
        ("render_mode", c_int),
        ("blend", c_int),
        ("material_id", c_char_p),
        ("sprite_id", c_char_p),
        ("mesh_id", c_char_p),
        ("mesh_variants", c_int),
        ("sprite_columns", c_int),
        ("sprite_rows", c_int),
        ("sprite_fps", c_float),
        ("velocity_stretch", c_float),
        ("align_to_velocity", c_int),
        ("soft_particle_distance", c_float),
        ("sort", c_int),
    ]


class LightInfo(ctypes.Structure):
    """``struct aetherfx_light_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("type", c_int),
        ("position", c_float * 3),
        ("direction", c_float * 3),
        ("color", c_float * 4),
        ("intensity", c_float),
        ("radius", c_float),
        ("cone_angle_deg", c_float),
    ]


class DecalInfo(ctypes.Structure):
    """``struct aetherfx_decal_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("position", c_float * 3),
        ("normal", c_float * 3),
        ("size", c_float * 2),
        ("rotation_deg", c_float),
        ("color", c_float * 4),
        ("opacity", c_float),
        ("emissive", c_float),
        ("circle", c_int),
        ("blend", c_int),
        ("texture_id", c_char_p),
        ("material_id", c_char_p),
    ]


class MeshInstanceInfo(ctypes.Structure):
    """``struct aetherfx_mesh_instance_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("mesh_id", c_char_p),
        ("material_id", c_char_p),
        ("transform", c_float * 16),
        ("color", c_float * 4),
        ("emissive", c_float),
        ("visible", c_int),
    ]


class VolumeInfo(ctypes.Structure):
    """``struct aetherfx_volume_info`` - a `volume` node's resolved frame state.

    ``mode`` is ``"procedural"`` (a raymarched density field; every field below
    is meaningful, see docs/VOLUMES.md) or ``"simulation"`` (the V1 fluid stub:
    id, bounds, density and temperature only).
    """

    _fields_ = [
        ("id", c_char_p),
        ("mode", c_char_p),
        ("shape", c_char_p),
        ("volume_type", c_char_p),
        ("backend", c_char_p),
        ("transform", c_float * 16),
        ("bounds_min", c_float * 3),
        ("bounds_max", c_float * 3),
        ("radius", c_float),
        ("height", c_float),
        ("density", c_float),
        ("emission", c_float),
        ("color", c_float * 4),
        ("color_hot", c_float * 4),
        ("filament_scale", c_float),
        ("strands", c_float),
        ("carve", c_float),
        ("softness", c_float),
        ("spiral_arms", c_int),
        ("arm_sharpness", c_float),
        ("twist", c_float),
        ("spin", c_float),
        ("climb", c_float),
        ("scatter", c_float),
        ("march_steps", c_int),
        ("seed", c_uint32),
        ("time", c_float),
        ("temperature", c_float),
    ]


class BeamInfo(ctypes.Structure):
    """``struct aetherfx_beam_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("polyline_count", c_size_t),
        ("width", c_float),
        ("color", c_float * 4),
        ("emissive", c_float),
        ("blend", c_int),
        ("material_id", c_char_p),
        ("pulse_phase", c_float),
    ]


class BeamStyle(ctypes.Structure):
    """``struct aetherfx_beam_style`` - the strike detail added after ABI 1."""

    _fields_ = [
        ("core_width", c_float),
        ("glow_width", c_float),
        ("path_count", c_size_t),
        ("ghost_count", c_size_t),
        ("flare_count", c_size_t),
    ]


class BeamPath(ctypes.Structure):
    """``struct aetherfx_beam_path`` - one bolt or branch, width and intensity per vertex."""

    _fields_ = [
        ("vertex_count", c_size_t),
        ("depth", c_int),
        ("fade", c_float),
        ("position", POINTER(c_float)),
        ("width", POINTER(c_float)),
        ("intensity", POINTER(c_float)),
    ]


class BeamFlare(ctypes.Structure):
    """``struct aetherfx_beam_flare`` - the bright blob at one end of a beam."""

    _fields_ = [
        ("position", c_float * 3),
        ("radius", c_float),
        ("intensity", c_float),
    ]


class TrailVertex(ctypes.Structure):
    """``struct aetherfx_trail_vertex`` - 13 floats, tightly packed."""

    _fields_ = [
        ("position", c_float * 3),
        ("width", c_float),
        ("age", c_float),
        ("normalized_age", c_float),
        ("u", c_float),
        ("color", c_float * 4),
        ("opacity", c_float),
        ("emissive", c_float),
    ]


class TrailInfo(ctypes.Structure):
    """``struct aetherfx_trail_info``."""

    _fields_ = [
        ("id", c_char_p),
        ("ribbon_count", c_size_t),
        ("blend", c_int),
        ("material_id", c_char_p),
    ]


class CameraInfo(ctypes.Structure):
    """``struct aetherfx_camera``."""

    _fields_ = [
        ("position", c_float * 3),
        ("target", c_float * 3),
        ("up", c_float * 3),
        ("fov_deg", c_float),
        ("near_plane", c_float),
        ("far_plane", c_float),
        ("exposure", c_float),
    ]


#: Sizes the C compiler reports on a 64-bit LP64/LLP64 target.  A mismatch here
#: means the header moved under us, and reading a frame would be undefined
#: behaviour rather than a wrong number - so it is checked, once, at load time.
_EXPECTED_SIZES: tuple[tuple[type[ctypes.Structure], int], ...] = (
    (TextureInfo, 32),
    (MeshInfo, 32),
    (MaterialInfo, 96),
    (ParticleSystemInfo, 80),
    (LightInfo, 64),
    (DecalInfo, 96),
    (MeshInstanceInfo, 112),
    (VolumeInfo, 232),
    (BeamInfo, 64),
    (BeamStyle, 32),
    (BeamPath, 40),
    (BeamFlare, 20),
    (TrailVertex, 52),
    (TrailInfo, 32),
    (CameraInfo, 52),
)

#: Floats per ``struct aetherfx_trail_vertex``, and the 12 of them the stream
#: protocol wants (everything but ``age``, which ``normalized_age`` replaces).
TRAIL_VERTEX_FLOATS = 13
_RIBBON_COLUMNS = np.array([0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12], dtype=np.intp)


# =========================================================================
# library discovery and binding
# =========================================================================


def _repo_root() -> Path:
    """Repository root of this checkout: ``<repo>/python/aetherfx/native.py``."""
    return Path(__file__).resolve().parents[2]


def _library_names() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("libaetherfx.dylib", "libaetherfx.so")
    if os.name == "nt":
        return ("aetherfx.dll", "libaetherfx.dll")
    return ("libaetherfx.so", "libaetherfx.dylib")


def library_candidates() -> list[Path]:
    """Every path :func:`find_library` will try, in order (env var excluded)."""
    roots = [_repo_root(), Path.cwd(), Path.cwd().parent]
    seen: dict[Path, None] = {}
    for root in roots:
        for build in ("build", "build-capi"):
            for name in _library_names():
                seen.setdefault(root / build / "src" / "capi" / name, None)
    return list(seen)


def find_library(path: str | os.PathLike[str] | None = None) -> str | None:
    """Locate ``libaetherfx``, or return ``None``.

    Search order: the ``path`` argument, ``$AETHERFX_LIBRARY``,
    ``<repo>/build/src/capi/libaetherfx.{dylib,so,dll}`` (the canonical build
    tree), the same under ``build-capi/`` (the ``capi`` CMake preset), and
    finally :func:`ctypes.util.find_library`.
    """
    if path:
        return str(path)

    from_env = os.environ.get(LIBRARY_ENV_VAR)
    if from_env:
        return from_env

    for candidate in library_candidates():
        if candidate.is_file():
            return str(candidate)

    return ctypes.util.find_library("aetherfx")


_LOCK = threading.Lock()
_LIBRARY: ctypes.CDLL | None = None


def load_library(path: str | os.PathLike[str] | None = None) -> ctypes.CDLL:
    """Load ``libaetherfx`` once and bind every entry point.

    Raises :class:`NativeUnavailable` when the library cannot be found, cannot
    be loaded, or reports an ABI version this module was not written against.
    """
    global _LIBRARY
    with _LOCK:
        if _LIBRARY is not None and not path:
            return _LIBRARY

        resolved = find_library(path)
        if not resolved:
            tried = "\n  ".join(str(p) for p in library_candidates())
            raise NativeUnavailable(
                "libaetherfx was not found. Build it with\n"
                "  cmake --preset capi && cmake --build --preset capi\n"
                f"or point ${LIBRARY_ENV_VAR} at the shared library. Tried:\n  {tried}"
            )
        try:
            library = ctypes.CDLL(resolved)
        except OSError as exc:
            raise NativeUnavailable(f"libaetherfx at {resolved!r} could not be loaded: {exc}") from exc

        for struct, size in _EXPECTED_SIZES:
            actual = ctypes.sizeof(struct)
            if actual != size:
                raise NativeUnavailable(
                    f"{struct.__name__} is {actual} bytes here but the C ABI says {size}; "
                    "aetherfx.native does not match aetherfx/aetherfx.h"
                )

        _bind(library)
        abi = library.aetherfx_abi_version()
        if abi != ABI_VERSION:
            raise NativeUnavailable(
                f"libaetherfx at {resolved!r} has ABI version {abi}, this binding needs {ABI_VERSION}"
            )
        if not path:
            _LIBRARY = library
        return library


def is_available() -> bool:
    """``True`` when :func:`load_library` would succeed.  Never raises."""
    try:
        load_library()
    except Exception:  # noqa: BLE001 - "unavailable" covers every failure mode
        return False
    return True


def version() -> tuple[int, int, int]:
    """The loaded library's semantic version."""
    library = load_library()
    major, minor, patch = c_int(), c_int(), c_int()
    library.aetherfx_version(byref(major), byref(minor), byref(patch))
    return major.value, minor.value, patch.value


def version_string() -> str:
    """The loaded library's version string, e.g. ``"0.1.0"``."""
    return _text(load_library().aetherfx_version_string())


_FLOAT_P = POINTER(c_float)
_U32_P = POINTER(c_uint32)

#: name -> (restype, argtypes) for all 80 exported functions.
_SIGNATURES: dict[str, tuple[Any, tuple[Any, ...]]] = {
    # -- version and errors ------------------------------------------------
    "aetherfx_version": (None, (POINTER(c_int), POINTER(c_int), POINTER(c_int))),
    "aetherfx_abi_version": (c_int, ()),
    "aetherfx_version_string": (c_char_p, ()),
    "aetherfx_last_error": (c_char_p, ()),
    "aetherfx_free_string": (None, (c_void_p,)),
    # -- effects -----------------------------------------------------------
    "aetherfx_effect_load_json": (c_void_p, (c_char_p, c_size_t)),
    "aetherfx_effect_load_file": (c_void_p, (c_char_p,)),
    "aetherfx_effect_free": (None, (c_void_p,)),
    "aetherfx_effect_name": (c_char_p, (c_void_p,)),
    "aetherfx_effect_duration": (c_double, (c_void_p,)),
    "aetherfx_effect_validate": (c_int, (c_void_p, c_char_p, c_size_t)),
    "aetherfx_effect_set_parameter": (c_int, (c_void_p, c_char_p, c_char_p, c_char_p)),
    "aetherfx_effect_to_json": (c_void_p, (c_void_p, c_int)),
    # -- controls ----------------------------------------------------------
    "aetherfx_effect_control_count": (c_int, (c_void_p,)),
    "aetherfx_control_info": (c_int, (c_void_p, c_int, POINTER(ControlInfo))),
    "aetherfx_effect_control_index": (c_int, (c_void_p, c_char_p)),
    "aetherfx_effect_set_control": (c_int, (c_void_p, c_char_p, c_double)),
    # -- compilation -------------------------------------------------------
    "aetherfx_compile": (c_void_p, (c_void_p, c_double)),
    "aetherfx_compiled_free": (None, (c_void_p,)),
    "aetherfx_compiled_ok": (c_int, (c_void_p,)),
    "aetherfx_compiled_fixed_dt": (c_double, (c_void_p,)),
    "aetherfx_compiled_diagnostics_json": (c_void_p, (c_void_p,)),
    "aetherfx_compiled_plan_json": (c_void_p, (c_void_p,)),
    # -- baked textures ----------------------------------------------------
    "aetherfx_compiled_texture_count": (c_int, (c_void_p,)),
    "aetherfx_texture_info": (c_int, (c_void_p, c_int, POINTER(TextureInfo))),
    "aetherfx_texture_index": (c_int, (c_void_p, c_char_p)),
    "aetherfx_texture_pixels": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_texture_pixels_rgba8": (c_int, (c_void_p, c_int, POINTER(c_uint8), c_size_t, c_int)),
    # -- baked meshes ------------------------------------------------------
    "aetherfx_compiled_mesh_count": (c_int, (c_void_p,)),
    "aetherfx_mesh_info": (c_int, (c_void_p, c_int, POINTER(MeshInfo))),
    "aetherfx_mesh_index": (c_int, (c_void_p, c_char_p)),
    "aetherfx_mesh_positions": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_mesh_normals": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_mesh_uvs": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_mesh_indices": (_U32_P, (c_void_p, c_int)),
    # -- baked materials ---------------------------------------------------
    "aetherfx_compiled_material_count": (c_int, (c_void_p,)),
    "aetherfx_material_info": (c_int, (c_void_p, c_int, POINTER(MaterialInfo))),
    "aetherfx_material_index": (c_int, (c_void_p, c_char_p)),
    # -- runtime -----------------------------------------------------------
    "aetherfx_runtime_create": (c_void_p, (c_void_p,)),
    "aetherfx_runtime_free": (None, (c_void_p,)),
    "aetherfx_runtime_reset": (c_int, (c_void_p,)),
    "aetherfx_runtime_step": (c_int, (c_void_p,)),
    "aetherfx_runtime_simulate_to": (c_int, (c_void_p, c_double)),
    "aetherfx_runtime_time": (c_double, (c_void_p,)),
    "aetherfx_runtime_frame_index": (c_uint64, (c_void_p,)),
    "aetherfx_runtime_fixed_dt": (c_double, (c_void_p,)),
    "aetherfx_runtime_backend": (c_char_p, (c_void_p,)),
    "aetherfx_runtime_statistics_json": (c_void_p, (c_void_p,)),
    # -- frame state: particles -------------------------------------------
    "aetherfx_runtime_particle_system_count": (c_int, (c_void_p,)),
    "aetherfx_particle_system_info": (c_int, (c_void_p, c_int, POINTER(ParticleSystemInfo))),
    "aetherfx_particle_position": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_previous_position": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_velocity": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_age": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_lifetime": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_size": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_rotation": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_color": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_opacity": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_emissive": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_orientation": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_scale3": (_FLOAT_P, (c_void_p, c_int)),
    "aetherfx_particle_variant": (_U32_P, (c_void_p, c_int)),
    "aetherfx_particle_seed": (_U32_P, (c_void_p, c_int)),
    "aetherfx_particle_custom0": (_FLOAT_P, (c_void_p, c_int)),
    # -- frame state: everything else -------------------------------------
    "aetherfx_runtime_light_count": (c_int, (c_void_p,)),
    "aetherfx_light_info": (c_int, (c_void_p, c_int, POINTER(LightInfo))),
    "aetherfx_runtime_decal_count": (c_int, (c_void_p,)),
    "aetherfx_decal_info": (c_int, (c_void_p, c_int, POINTER(DecalInfo))),
    "aetherfx_runtime_mesh_instance_count": (c_int, (c_void_p,)),
    "aetherfx_mesh_instance_info": (c_int, (c_void_p, c_int, POINTER(MeshInstanceInfo))),
    "aetherfx_runtime_volume_count": (c_int, (c_void_p,)),
    "aetherfx_volume_info": (c_int, (c_void_p, c_int, POINTER(VolumeInfo))),
    "aetherfx_runtime_beam_count": (c_int, (c_void_p,)),
    "aetherfx_beam_info": (c_int, (c_void_p, c_int, POINTER(BeamInfo))),
    "aetherfx_beam_polyline": (c_int, (c_void_p, c_int, c_int, POINTER(_FLOAT_P), POINTER(c_size_t))),
    "aetherfx_beam_style": (c_int, (c_void_p, c_int, POINTER(BeamStyle))),
    "aetherfx_beam_path": (c_int, (c_void_p, c_int, c_int, POINTER(BeamPath))),
    "aetherfx_beam_ghost": (c_int, (c_void_p, c_int, c_int, POINTER(BeamPath))),
    "aetherfx_beam_flare": (c_int, (c_void_p, c_int, c_int, POINTER(BeamFlare))),
    "aetherfx_runtime_trail_count": (c_int, (c_void_p,)),
    "aetherfx_trail_info": (c_int, (c_void_p, c_int, POINTER(TrailInfo))),
    "aetherfx_trail_ribbon": (c_int, (c_void_p, c_int, c_int, POINTER(POINTER(TrailVertex)), POINTER(c_size_t))),
    "aetherfx_runtime_camera": (c_int, (c_void_p, POINTER(CameraInfo))),
}


def _bind(library: ctypes.CDLL) -> None:
    """Declare ``argtypes``/``restype`` for every entry point in the ABI."""
    missing = []
    for name, (restype, argtypes) in _SIGNATURES.items():
        try:
            function = getattr(library, name)
        except AttributeError:
            missing.append(name)
            continue
        function.restype = restype
        function.argtypes = list(argtypes)
    if missing:
        raise NativeUnavailable(
            "libaetherfx is missing " + ", ".join(missing) + "; it is older than this binding"
        )


# =========================================================================
# small helpers
# =========================================================================


def _text(raw: bytes | None) -> str:
    """A library-owned ``const char*`` as ``str`` (never ``None``)."""
    return "" if raw is None else raw.decode("utf-8", "replace")


def _last_error(library: ctypes.CDLL) -> str:
    return _text(library.aetherfx_last_error())


def _check(library: ctypes.CDLL, status: int, operation: str) -> int:
    """Raise :class:`NativeError` when ``status`` is a negative ``aetherfx_status``."""
    if status < 0:
        raise NativeError(status, _last_error(library), operation)
    return status


def _take_json(library: ctypes.CDLL, pointer: int | None, operation: str) -> Any:
    """Parse and release a ``char*`` from an ``aetherfx_*_json()`` function."""
    if not pointer:
        raise NativeError(-10, _last_error(library), operation)
    try:
        raw = ctypes.cast(pointer, c_char_p).value
    finally:
        library.aetherfx_free_string(c_void_p(pointer))
    try:
        return json.loads(_text(raw))
    except ValueError as exc:
        raise NativeError(-10, f"{operation} returned text that is not JSON: {exc}", operation) from exc


def _copy(pointer: Any, count: int, components: int, dtype: Any) -> np.ndarray:
    """A private numpy copy of ``count * components`` values behind ``pointer``.

    The C buffers are invalidated by the next step of the runtime (or by
    freeing the compiled effect), so every array that leaves this module owns
    its memory.  A NULL pointer with ``count == 0`` is the documented "no live
    particles" answer and yields an empty array of the right shape.
    """
    shape: tuple[int, ...] = (count,) if components == 1 else (count, components)
    if not pointer or count == 0:
        return np.zeros(shape, dtype=dtype)
    return np.ctypeslib.as_array(pointer, shape=shape).astype(dtype, copy=True)


def _enum(values: tuple[str, ...], index: int) -> str:
    return values[index] if 0 <= index < len(values) else str(index)


def _vec(values: Any) -> list[float]:
    return [float(v) for v in values]


# =========================================================================
# resources
# =========================================================================


@dataclass(frozen=True)
class Texture:
    """A baked texture: linear RGBA float pixels plus the flipbook layout.

    ``frames`` frames live side by side in one image; frame *i* covers columns
    ``[i * frame_width, (i + 1) * frame_width)``.
    """

    id: str
    width: int
    height: int
    frames: int
    frame_width: int
    channels: int
    _compiled: "Compiled" = field(repr=False, compare=False)
    _index: int = field(repr=False, compare=False)

    @property
    def pixels(self) -> np.ndarray:
        """Linear RGBA float32, shape ``(height, width, 4)``, origin top-left.

        A copy: the library's buffer dies with the :class:`Compiled`.
        """
        library, handle = self._compiled._use()
        pointer = library.aetherfx_texture_pixels(handle, self._index)
        if not pointer:
            raise NativeError(-10, _last_error(library), "aetherfx_texture_pixels")
        flat = _copy(pointer, self.width * self.height * self.channels, 1, np.float32)
        return flat.reshape(self.height, self.width, self.channels)

    def pixels_rgba8(self, srgb: bool = True) -> np.ndarray:
        """8-bit RGBA, shape ``(height, width, 4)``.

        With ``srgb`` the RGB channels are sRGB-encoded (what a GPU sampler
        expects from an sRGB format) and alpha stays linear.
        """
        library, handle = self._compiled._use()
        out = np.empty((self.height, self.width, 4), dtype=np.uint8)
        _check(
            library,
            library.aetherfx_texture_pixels_rgba8(
                handle, self._index, out.ctypes.data_as(POINTER(c_uint8)), c_size_t(out.nbytes), 1 if srgb else 0
            ),
            "aetherfx_texture_pixels_rgba8",
        )
        return out

    def png_bytes(self, srgb: bool = True) -> bytes:
        """The whole image (every frame, side by side) as PNG bytes."""
        from PIL import Image  # noqa: PLC0415 - keeps `import aetherfx.native` light

        buffer = BytesIO()
        Image.fromarray(self.pixels_rgba8(srgb=srgb), "RGBA").save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()


@dataclass(frozen=True)
class Mesh:
    """A baked mesh, or one seeded variant of one.

    Seeded primitives bake variants under ``"<id>"``, ``"<id>#1"``, ``"<id>#2"``
    ...; :attr:`variant_of` is the shared base id and :attr:`variant_index` the
    number a particle's ``variant`` array refers to.
    """

    id: str
    variant_of: str
    variant_index: int
    vertex_count: int
    index_count: int
    has_normals: bool
    has_uvs: bool
    _compiled: "Compiled" = field(repr=False, compare=False)
    _index: int = field(repr=False, compare=False)

    def _array(self, accessor: str, components: int, dtype: Any, count: int) -> np.ndarray:
        library, handle = self._compiled._use()
        pointer = getattr(library, accessor)(handle, self._index)
        return _copy(pointer, count, components, dtype)

    @property
    def positions(self) -> np.ndarray:
        """``(vertex_count, 3)`` float32, meters."""
        return self._array("aetherfx_mesh_positions", 3, np.float32, self.vertex_count)

    @property
    def normals(self) -> np.ndarray:
        """``(vertex_count, 3)`` float32, or ``(0, 3)`` when the mesh has none."""
        if not self.has_normals:
            return np.zeros((0, 3), dtype=np.float32)
        return self._array("aetherfx_mesh_normals", 3, np.float32, self.vertex_count)

    @property
    def uvs(self) -> np.ndarray:
        """``(vertex_count, 2)`` float32, or ``(0, 2)`` when the mesh has none."""
        if not self.has_uvs:
            return np.zeros((0, 2), dtype=np.float32)
        return self._array("aetherfx_mesh_uvs", 2, np.float32, self.vertex_count)

    @property
    def indices(self) -> np.ndarray:
        """``(index_count,)`` uint32 triangle list, counter-clockwise."""
        return self._array("aetherfx_mesh_indices", 1, np.uint32, self.index_count)


def _split_variant(mesh_id: str) -> tuple[str, int]:
    """``"rock#3"`` -> ``("rock", 3)``; a plain id is variant 0."""
    base, sep, suffix = mesh_id.rpartition("#")
    if not sep or not suffix.isdigit():
        return mesh_id, 0
    return base, int(suffix)


# =========================================================================
# effect
# =========================================================================


class _Handle:
    """Shared RAII plumbing: one owned C handle, freed once."""

    _free_function = ""

    def __init__(self, library: ctypes.CDLL, handle: int) -> None:
        self._library = library
        self._handle: int | None = handle

    def _use(self) -> tuple[ctypes.CDLL, c_void_p]:
        if self._handle is None:
            raise NativeError(-1, f"{type(self).__name__} has already been closed", "use after close")
        return self._library, c_void_p(self._handle)

    @property
    def closed(self) -> bool:
        return self._handle is None

    def close(self) -> None:
        """Release the handle.  Idempotent."""
        handle, self._handle = self._handle, None
        if handle is not None:
            getattr(self._library, self._free_function)(c_void_p(handle))

    def __enter__(self):  # noqa: ANN204 - returns Self
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - interpreter teardown must not raise
            pass


class Effect(_Handle):
    """An authored effect document: ``aetherfx_effect``."""

    _free_function = "aetherfx_effect_free"

    @classmethod
    def from_json(cls, text: str | bytes, library: ctypes.CDLL | None = None) -> "Effect":
        """Parse an effect document.  Older ``schema_version``\\ s migrate forward."""
        lib = library or load_library()
        raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        handle = lib.aetherfx_effect_load_json(raw, c_size_t(len(raw)))
        if not handle:
            raise NativeError(-3, _last_error(lib), "aetherfx_effect_load_json")
        return cls(lib, handle)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str], library: ctypes.CDLL | None = None) -> "Effect":
        """Read and parse an effect document from disk."""
        lib = library or load_library()
        handle = lib.aetherfx_effect_load_file(str(path).encode("utf-8"))
        if not handle:
            raise NativeError(-7, _last_error(lib), f"aetherfx_effect_load_file({path})")
        return cls(lib, handle)

    @property
    def name(self) -> str:
        library, handle = self._use()
        return _text(library.aetherfx_effect_name(handle))

    @property
    def duration(self) -> float:
        """Authored duration in seconds (a presentation hint, not a hard stop)."""
        library, handle = self._use()
        value = library.aetherfx_effect_duration(handle)
        if value < 0.0:
            raise NativeError(int(value), _last_error(library), "aetherfx_effect_duration")
        return float(value)

    def validate(self) -> dict[str, Any]:
        """Diagnostics as ``{"ok", "errors", "warnings", "items": [...]}``.

        Content problems are *data*, not exceptions: an effect with errors
        returns ``ok = False`` rather than raising.
        """
        library, handle = self._use()
        capacity = 1 << 14
        while capacity <= (1 << 22):
            buffer = ctypes.create_string_buffer(capacity)
            status = library.aetherfx_effect_validate(handle, buffer, c_size_t(capacity))
            if status in (OK, ERROR_VALIDATION):
                return json.loads(_text(buffer.value))
            if status != ERROR_BUFFER_TOO_SMALL:
                raise NativeError(status, _last_error(library), "aetherfx_effect_validate")
            capacity *= 4
        raise NativeError(ERROR_BUFFER_TOO_SMALL, _last_error(library), "aetherfx_effect_validate")

    def set_parameter(self, node_id: str, name: str, value: Any) -> None:
        """Override one parameter of one node for the *next* compile.

        ``value`` is encoded as JSON, so pass Python values: ``2.5``, ``True``,
        ``"sphere"``, ``[1, 0, 0]``, ``[[0, 1], [1, 0]]`` for a curve.  Setting a
        constant clears any keyframe track on that parameter.
        """
        library, handle = self._use()
        encoded = json.dumps(value)
        _check(
            library,
            library.aetherfx_effect_set_parameter(
                handle, node_id.encode("utf-8"), name.encode("utf-8"), encoded.encode("utf-8")
            ),
            f"aetherfx_effect_set_parameter({node_id}.{name})",
        )

    def controls(self) -> list[dict[str, Any]]:
        """The effect's named numeric knobs, in document order.

        Each entry is ``{id, label, group, unit, min, max, default, value,
        step, bindings}``.  Controls are non-destructive: moving one and
        compiling gives a weaker or stronger instance of the same effect
        without editing the graph (docs/CONTROLS.md).
        """
        library, handle = self._use()
        count = library.aetherfx_effect_control_count(handle)
        if count < 0:
            raise NativeError(count, _last_error(library), "aetherfx_effect_control_count")
        out: list[dict[str, Any]] = []
        for index in range(count):
            info = ControlInfo()
            _check(library, library.aetherfx_control_info(handle, c_int(index), ctypes.byref(info)),
                   f"aetherfx_control_info({index})")
            out.append({
                "id": _text(info.id),
                "label": _text(info.label),
                "group": _text(info.group),
                "unit": _text(info.unit),
                "min": float(info.min),
                "max": float(info.max),
                "default": float(info.default_value),
                "value": float(info.value),
                "step": float(info.step),
                "bindings": int(info.binding_count),
            })
        return out

    def set_control(self, control_id: str, value: float) -> None:
        """Move one control, for the *next* compile.

        ``value`` must lie inside the control's ``[min, max]``.  Nothing in the
        graph is rewritten; the compiler folds controls into its own copy.
        """
        library, handle = self._use()
        _check(
            library,
            library.aetherfx_effect_set_control(handle, control_id.encode("utf-8"), c_double(float(value))),
            f"aetherfx_effect_set_control({control_id})",
        )

    def to_json(self, indent: int = 2) -> str:
        """Serialise the effect back to a document (``indent < 0`` = compact)."""
        library, handle = self._use()
        pointer = library.aetherfx_effect_to_json(handle, c_int(indent))
        if not pointer:
            raise NativeError(-10, _last_error(library), "aetherfx_effect_to_json")
        try:
            return _text(ctypes.cast(pointer, c_char_p).value)
        finally:
            library.aetherfx_free_string(c_void_p(pointer))

    def to_dict(self) -> dict[str, Any]:
        """The effect document as a Python dict."""
        return json.loads(self.to_json(indent=-1))

    def compile(self, fixed_dt: float = 0.0) -> "Compiled":
        """Validate, bake and plan.  ``fixed_dt = 0`` means the default 1/60."""
        library, handle = self._use()
        compiled = library.aetherfx_compile(handle, c_double(float(fixed_dt)))
        if not compiled:
            raise NativeError(-5, _last_error(library), "aetherfx_compile")
        return Compiled(library, compiled)


# =========================================================================
# compiled
# =========================================================================


class Compiled(_Handle):
    """A validated effect plus its baked resources and plan: ``aetherfx_compiled``.

    Immutable once created, so it can be shared between threads and instances.
    Runtimes copy the plan they need and outlive it safely.
    """

    _free_function = "aetherfx_compiled_free"

    @property
    def ok(self) -> bool:
        """``True`` when the compile produced no error diagnostics."""
        library, handle = self._use()
        return _check(library, library.aetherfx_compiled_ok(handle), "aetherfx_compiled_ok") == 1

    @property
    def fixed_dt(self) -> float:
        library, handle = self._use()
        value = library.aetherfx_compiled_fixed_dt(handle)
        if value < 0.0:
            raise NativeError(int(value), _last_error(library), "aetherfx_compiled_fixed_dt")
        return float(value)

    @property
    def diagnostics(self) -> dict[str, Any]:
        """``{"ok", "errors", "warnings", "items": [...]}`` from the compile."""
        library, handle = self._use()
        return _take_json(library, library.aetherfx_compiled_diagnostics_json(handle), "aetherfx_compiled_diagnostics_json")

    @property
    def plan(self) -> dict[str, Any]:
        """The execution plan: ``{"nodes", "tiers", "resources", "diagnostics"}``."""
        library, handle = self._use()
        return _take_json(library, library.aetherfx_compiled_plan_json(handle), "aetherfx_compiled_plan_json")

    # -- textures ---------------------------------------------------------

    @property
    def textures(self) -> list[Texture]:
        """Every baked texture, in the library's stable (id-sorted) order."""
        library, handle = self._use()
        count = _check(library, library.aetherfx_compiled_texture_count(handle), "aetherfx_compiled_texture_count")
        out = []
        for index in range(count):
            info = TextureInfo()
            _check(library, library.aetherfx_texture_info(handle, index, byref(info)), "aetherfx_texture_info")
            out.append(
                Texture(
                    id=_text(info.id),
                    width=int(info.width),
                    height=int(info.height),
                    frames=int(info.frames),
                    frame_width=int(info.frame_width),
                    channels=int(info.channels),
                    _compiled=self,
                    _index=index,
                )
            )
        return out

    def texture(self, texture_id: str) -> Texture | None:
        """The texture with this id, or ``None``."""
        library, handle = self._use()
        index = library.aetherfx_texture_index(handle, texture_id.encode("utf-8"))
        if index < 0:
            return None
        return self.textures[index]

    # -- meshes -----------------------------------------------------------

    @property
    def meshes(self) -> list[Mesh]:
        """Every baked mesh, seeded variants included."""
        library, handle = self._use()
        count = _check(library, library.aetherfx_compiled_mesh_count(handle), "aetherfx_compiled_mesh_count")
        out = []
        for index in range(count):
            info = MeshInfo()
            _check(library, library.aetherfx_mesh_info(handle, index, byref(info)), "aetherfx_mesh_info")
            mesh_id = _text(info.id)
            base, variant = _split_variant(mesh_id)
            out.append(
                Mesh(
                    id=mesh_id,
                    variant_of=base,
                    variant_index=variant,
                    vertex_count=int(info.vertex_count),
                    index_count=int(info.index_count),
                    has_normals=bool(info.has_normals),
                    has_uvs=bool(info.has_uvs),
                    _compiled=self,
                    _index=index,
                )
            )
        return out

    def mesh(self, mesh_id: str) -> Mesh | None:
        """The mesh with this exact id (``"rock"``, ``"rock#2"``), or ``None``."""
        library, handle = self._use()
        index = library.aetherfx_mesh_index(handle, mesh_id.encode("utf-8"))
        if index < 0:
            return None
        return self.meshes[index]

    def mesh_variants(self, base_id: str) -> list[Mesh]:
        """Every variant of ``base_id``, ordered so ``variants[k]`` is variant *k*.

        This is the list a mesh particle system indexes with its ``variant``
        array; ``mesh_variants(base)[0]`` is the mesh stored under the plain id.
        """
        found = [mesh for mesh in self.meshes if mesh.variant_of == base_id]
        found.sort(key=lambda mesh: mesh.variant_index)
        return found

    def mesh_bases(self) -> list[str]:
        """The distinct base ids, in first-seen order."""
        seen: dict[str, None] = {}
        for mesh in self.meshes:
            seen.setdefault(mesh.variant_of, None)
        return list(seen)

    # -- materials --------------------------------------------------------

    @property
    def materials(self) -> list[dict[str, Any]]:
        """Every baked material as a JSON-serialisable dict.

        Enums are their vocabulary spellings (``blend``: ``additive`` /
        ``alpha`` / ``premultiplied``, ``shading``: ``unlit`` / ``lit``);
        colours are linear RGBA lists.
        """
        library, handle = self._use()
        count = _check(library, library.aetherfx_compiled_material_count(handle), "aetherfx_compiled_material_count")
        out = []
        for index in range(count):
            info = MaterialInfo()
            _check(library, library.aetherfx_material_info(handle, index, byref(info)), "aetherfx_material_info")
            out.append(
                {
                    "id": _text(info.id),
                    "blend": _enum(BLEND_MODES, int(info.blend)),
                    "shading": _enum(SHADING_MODES, int(info.shading)),
                    "base_color": _vec(info.base_color),
                    "opacity": float(info.opacity),
                    "emissive_color": _vec(info.emissive_color),
                    "emissive_intensity": float(info.emissive_intensity),
                    "fresnel_power": float(info.fresnel_power),
                    "dissolve": float(info.dissolve),
                    "erosion": float(info.erosion),
                    "soft_particle": bool(info.soft_particle),
                    "depth_fade": float(info.depth_fade),
                    "base_texture": _text(info.base_texture),
                    "noise_texture": _text(info.noise_texture),
                }
            )
        return out

    # -- runtimes ---------------------------------------------------------

    def runtime(self) -> "Runtime":
        """One playing instance.  Fails when :attr:`ok` is ``False``."""
        library, handle = self._use()
        runtime = library.aetherfx_runtime_create(handle)
        if not runtime:
            raise NativeError(-6, _last_error(library), "aetherfx_runtime_create")
        return Runtime(library, runtime)


# =========================================================================
# frame snapshot
# =========================================================================

#: Accessor -> (component count, dtype) for every per-particle array.
_PARTICLE_ARRAYS: tuple[tuple[str, str, int, Any], ...] = (
    ("position", "aetherfx_particle_position", 3, np.float32),
    ("previous_position", "aetherfx_particle_previous_position", 3, np.float32),
    ("velocity", "aetherfx_particle_velocity", 3, np.float32),
    ("age", "aetherfx_particle_age", 1, np.float32),
    ("lifetime", "aetherfx_particle_lifetime", 1, np.float32),
    ("size", "aetherfx_particle_size", 1, np.float32),
    ("rotation", "aetherfx_particle_rotation", 1, np.float32),
    ("color", "aetherfx_particle_color", 4, np.float32),
    ("opacity", "aetherfx_particle_opacity", 1, np.float32),
    ("emissive", "aetherfx_particle_emissive", 1, np.float32),
    ("orientation", "aetherfx_particle_orientation", 4, np.float32),
    ("scale3", "aetherfx_particle_scale3", 3, np.float32),
    ("variant", "aetherfx_particle_variant", 1, np.uint32),
    ("seed", "aetherfx_particle_seed", 1, np.uint32),
    ("custom0", "aetherfx_particle_custom0", 1, np.float32),
)


@dataclass
class ParticleSystemFrame:
    """One particle system in one frame: its draw setup plus owned numpy arrays.

    Every array has :attr:`count` entries.  ``position``/``velocity`` are
    ``(N, 3)``, ``color``/``orientation`` ``(N, 4)``, ``scale3`` ``(N, 3)`` and
    the rest ``(N,)``.  ``color``'s alpha channel is unused - the alpha to draw
    with is ``opacity``.
    """

    id: str
    count: int
    render_mode: str
    blend: str
    material_id: str
    sprite_id: str
    mesh_id: str
    mesh_variants: int
    sprite_columns: int
    sprite_rows: int
    sprite_fps: float
    velocity_stretch: float
    align_to_velocity: bool
    soft_particle_distance: float
    sort: bool
    arrays: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def is_mesh(self) -> bool:
        """``True`` when orientation / scale3 / variant carry real values."""
        return self.render_mode in MESH_RENDER_MODES

    def __getitem__(self, name: str) -> np.ndarray:
        return self.arrays[name]

    position = property(lambda self: self.arrays["position"])
    previous_position = property(lambda self: self.arrays["previous_position"])
    velocity = property(lambda self: self.arrays["velocity"])
    age = property(lambda self: self.arrays["age"])
    lifetime = property(lambda self: self.arrays["lifetime"])
    size = property(lambda self: self.arrays["size"])
    rotation = property(lambda self: self.arrays["rotation"])
    color = property(lambda self: self.arrays["color"])
    opacity = property(lambda self: self.arrays["opacity"])
    emissive = property(lambda self: self.arrays["emissive"])
    orientation = property(lambda self: self.arrays["orientation"])
    scale3 = property(lambda self: self.arrays["scale3"])
    variant = property(lambda self: self.arrays["variant"])
    seed = property(lambda self: self.arrays["seed"])
    custom0 = property(lambda self: self.arrays["custom0"])


@dataclass
class RuntimeFrame:
    """Everything one step produced, copied out of the library's buffers.

    Safe to keep: nothing here points into the runtime, so the next
    :meth:`Runtime.step` cannot invalidate it.  Dict keys mirror the C struct
    fields (``material_id``, ``texture_id``, ``cone_angle_deg`` ...).
    """

    time: float
    frame_index: int
    systems: list[ParticleSystemFrame] = field(default_factory=list)
    lights: list[dict[str, Any]] = field(default_factory=list)
    decals: list[dict[str, Any]] = field(default_factory=list)
    mesh_instances: list[dict[str, Any]] = field(default_factory=list)
    volumes: list[dict[str, Any]] = field(default_factory=list)
    beams: list[dict[str, Any]] = field(default_factory=list)   # {..., "polylines": [(N, 3) f32],
                                                                #  "paths"/"ghosts": [{"vertices": (N, 5) f32, "depth", "fade"}],
                                                                #  "flares": [{"position", "radius", "intensity"}]}
    trails: list[dict[str, Any]] = field(default_factory=list)  # {..., "ribbons": [(N, 12) f32]}
    camera: dict[str, Any] | None = None

    @property
    def particle_count(self) -> int:
        return sum(system.count for system in self.systems)

    def system(self, system_id: str) -> ParticleSystemFrame | None:
        return next((s for s in self.systems if s.id == system_id), None)


# =========================================================================
# runtime
# =========================================================================


class Runtime(_Handle):
    """One playing instance: ``aetherfx_runtime``.

    Deterministic and fixed-step - same document, seed, ``fixed_dt`` and step
    count gives bit-identical buffers.  A single runtime must be driven by one
    thread at a time; separate runtimes are fully independent.
    """

    _free_function = "aetherfx_runtime_free"

    # -- clock ------------------------------------------------------------

    def reset(self) -> None:
        """Rewind to t = 0, frame 0, with the analytic scene evaluated at 0."""
        library, handle = self._use()
        _check(library, library.aetherfx_runtime_reset(handle), "aetherfx_runtime_reset")

    def step(self) -> None:
        """Advance exactly one fixed timestep."""
        library, handle = self._use()
        _check(library, library.aetherfx_runtime_step(handle), "aetherfx_runtime_step")

    def simulate_to(self, time: float) -> None:
        """Step until the runtime reaches ``time``.

        Never interpolates and never steps backwards: a ``time`` behind the
        current one does nothing.  Call :meth:`reset` to replay.
        """
        library, handle = self._use()
        _check(library, library.aetherfx_runtime_simulate_to(handle, c_double(float(time))), "aetherfx_runtime_simulate_to")

    @property
    def time(self) -> float:
        """Current simulation time, always ``frame_index * fixed_dt``."""
        library, handle = self._use()
        value = library.aetherfx_runtime_time(handle)
        if value < 0.0:
            raise NativeError(int(value), _last_error(library), "aetherfx_runtime_time")
        return float(value)

    @property
    def frame_index(self) -> int:
        """Steps taken since the last reset."""
        library, handle = self._use()
        value = library.aetherfx_runtime_frame_index(handle)
        if value == 0xFFFFFFFFFFFFFFFF:
            raise NativeError(-10, _last_error(library), "aetherfx_runtime_frame_index")
        return int(value)

    @property
    def fixed_dt(self) -> float:
        library, handle = self._use()
        value = library.aetherfx_runtime_fixed_dt(handle)
        if value < 0.0:
            raise NativeError(int(value), _last_error(library), "aetherfx_runtime_fixed_dt")
        return float(value)

    @property
    def backend(self) -> str:
        """Name of the simulation backend, e.g. ``"cpu"``."""
        library, handle = self._use()
        return _text(library.aetherfx_runtime_backend(handle))

    def statistics(self) -> dict[str, Any]:
        """Per-system counters plus wall-clock timings (the only non-deterministic
        values in the API)."""
        library, handle = self._use()
        return _take_json(library, library.aetherfx_runtime_statistics_json(handle), "aetherfx_runtime_statistics_json")

    # -- frame ------------------------------------------------------------

    def frame(self) -> RuntimeFrame:
        """Copy the whole frame out of the library's buffers.

        Everything is copied because the C arrays are invalidated by the next
        step of this runtime; what comes back stays valid forever.
        """
        library, handle = self._use()
        return RuntimeFrame(
            time=self.time,
            frame_index=self.frame_index,
            systems=list(self._systems(library, handle)),
            lights=list(self._lights(library, handle)),
            decals=list(self._decals(library, handle)),
            mesh_instances=list(self._mesh_instances(library, handle)),
            volumes=list(self._volumes(library, handle)),
            beams=list(self._beams(library, handle)),
            trails=list(self._trails(library, handle)),
            camera=self.camera(),
        )

    def particle_systems(self) -> list[ParticleSystemFrame]:
        """Just the particle systems of the current frame."""
        library, handle = self._use()
        return list(self._systems(library, handle))

    def camera(self) -> dict[str, Any] | None:
        """The authored preview camera, or ``None`` when the effect has none."""
        library, handle = self._use()
        info = CameraInfo()
        status = library.aetherfx_runtime_camera(handle, byref(info))
        if status < 0:
            raise NativeError(status, _last_error(library), "aetherfx_runtime_camera")
        if status == 0:
            return None
        return {
            "position": _vec(info.position),
            "target": _vec(info.target),
            "up": _vec(info.up),
            "fov": float(info.fov_deg),
            "near": float(info.near_plane),
            "far": float(info.far_plane),
            "exposure": float(info.exposure),
        }

    # -- frame pieces -----------------------------------------------------

    def _systems(self, library: ctypes.CDLL, handle: c_void_p) -> Iterator[ParticleSystemFrame]:
        count = _check(
            library, library.aetherfx_runtime_particle_system_count(handle), "aetherfx_runtime_particle_system_count"
        )
        for index in range(count):
            info = ParticleSystemInfo()
            _check(library, library.aetherfx_particle_system_info(handle, index, byref(info)), "aetherfx_particle_system_info")
            alive = int(info.count)
            arrays = {
                name: _copy(getattr(library, accessor)(handle, index), alive, components, dtype)
                for name, accessor, components, dtype in _PARTICLE_ARRAYS
            }
            yield ParticleSystemFrame(
                id=_text(info.id),
                count=alive,
                render_mode=_enum(RENDER_MODES, int(info.render_mode)),
                blend=_enum(BLEND_MODES, int(info.blend)),
                material_id=_text(info.material_id),
                sprite_id=_text(info.sprite_id),
                mesh_id=_text(info.mesh_id),
                mesh_variants=int(info.mesh_variants),
                sprite_columns=int(info.sprite_columns),
                sprite_rows=int(info.sprite_rows),
                sprite_fps=float(info.sprite_fps),
                velocity_stretch=float(info.velocity_stretch),
                align_to_velocity=bool(info.align_to_velocity),
                soft_particle_distance=float(info.soft_particle_distance),
                sort=bool(info.sort),
                arrays=arrays,
            )

    def _lights(self, library: ctypes.CDLL, handle: c_void_p) -> Iterator[dict[str, Any]]:
        count = _check(library, library.aetherfx_runtime_light_count(handle), "aetherfx_runtime_light_count")
        for index in range(count):
            info = LightInfo()
            _check(library, library.aetherfx_light_info(handle, index, byref(info)), "aetherfx_light_info")
            yield {
                "id": _text(info.id),
                "type": _enum(LIGHT_TYPES, int(info.type)),
                "position": _vec(info.position),
                "direction": _vec(info.direction),
                "color": _vec(info.color),
                "intensity": float(info.intensity),
                "radius": float(info.radius),
                "cone_angle_deg": float(info.cone_angle_deg),
            }

    def _decals(self, library: ctypes.CDLL, handle: c_void_p) -> Iterator[dict[str, Any]]:
        count = _check(library, library.aetherfx_runtime_decal_count(handle), "aetherfx_runtime_decal_count")
        for index in range(count):
            info = DecalInfo()
            _check(library, library.aetherfx_decal_info(handle, index, byref(info)), "aetherfx_decal_info")
            yield {
                "id": _text(info.id),
                "position": _vec(info.position),
                "normal": _vec(info.normal),
                "size": _vec(info.size),
                "rotation_deg": float(info.rotation_deg),
                "color": _vec(info.color),
                "opacity": float(info.opacity),
                "emissive": float(info.emissive),
                "circle": bool(info.circle),
                "blend": _enum(BLEND_MODES, int(info.blend)),
                "texture_id": _text(info.texture_id),
                "material_id": _text(info.material_id),
            }

    def _mesh_instances(self, library: ctypes.CDLL, handle: c_void_p) -> Iterator[dict[str, Any]]:
        count = _check(
            library, library.aetherfx_runtime_mesh_instance_count(handle), "aetherfx_runtime_mesh_instance_count"
        )
        for index in range(count):
            info = MeshInstanceInfo()
            _check(library, library.aetherfx_mesh_instance_info(handle, index, byref(info)), "aetherfx_mesh_instance_info")
            yield {
                "id": _text(info.id),
                "mesh_id": _text(info.mesh_id),
                "material_id": _text(info.material_id),
                "transform": _vec(info.transform),
                "color": _vec(info.color),
                "emissive": float(info.emissive),
                "visible": bool(info.visible),
            }

    def _volumes(self, library: ctypes.CDLL, handle: c_void_p) -> Iterator[dict[str, Any]]:
        count = _check(library, library.aetherfx_runtime_volume_count(handle), "aetherfx_runtime_volume_count")
        for index in range(count):
            info = VolumeInfo()
            _check(library, library.aetherfx_volume_info(handle, index, byref(info)), "aetherfx_volume_info")
            yield {
                "id": _text(info.id),
                "mode": _text(info.mode),
                "shape": _text(info.shape),
                "volume_type": _text(info.volume_type),
                "backend": _text(info.backend),
                "transform": _vec(info.transform),
                "bounds_min": _vec(info.bounds_min),
                "bounds_max": _vec(info.bounds_max),
                "radius": float(info.radius),
                "height": float(info.height),
                "density": float(info.density),
                "emission": float(info.emission),
                "color": _vec(info.color),
                "color_hot": _vec(info.color_hot),
                "filament_scale": float(info.filament_scale),
                "strands": float(info.strands),
                "carve": float(info.carve),
                "softness": float(info.softness),
                "spiral_arms": int(info.spiral_arms),
                "arm_sharpness": float(info.arm_sharpness),
                "twist": float(info.twist),
                "spin": float(info.spin),
                "climb": float(info.climb),
                "scatter": float(info.scatter),
                "march_steps": int(info.march_steps),
                "seed": int(info.seed),
                "time": float(info.time),
                "temperature": float(info.temperature),
            }

    def _beams(self, library: ctypes.CDLL, handle: c_void_p) -> Iterator[dict[str, Any]]:
        count = _check(library, library.aetherfx_runtime_beam_count(handle), "aetherfx_runtime_beam_count")
        for index in range(count):
            info = BeamInfo()
            _check(library, library.aetherfx_beam_info(handle, index, byref(info)), "aetherfx_beam_info")
            polylines = []
            for polyline in range(int(info.polyline_count)):
                xyz = _FLOAT_P()
                points = c_size_t()
                _check(
                    library,
                    library.aetherfx_beam_polyline(handle, index, polyline, byref(xyz), byref(points)),
                    "aetherfx_beam_polyline",
                )
                polylines.append(_copy(xyz, int(points.value), 3, np.float32))
            style = BeamStyle()
            _check(library, library.aetherfx_beam_style(handle, index, byref(style)), "aetherfx_beam_style")
            yield {
                "id": _text(info.id),
                "width": float(info.width),
                "color": _vec(info.color),
                "emissive": float(info.emissive),
                "blend": _enum(BLEND_MODES, int(info.blend)),
                "material_id": _text(info.material_id),
                "pulse_phase": float(info.pulse_phase),
                "core_width": float(style.core_width),
                "glow_width": float(style.glow_width),
                "polylines": polylines,
                "paths": [
                    self._beam_path(library, handle, index, path, "aetherfx_beam_path")
                    for path in range(int(style.path_count))
                ],
                "ghosts": [
                    self._beam_path(library, handle, index, ghost, "aetherfx_beam_ghost")
                    for ghost in range(int(style.ghost_count))
                ],
                "flares": [
                    self._beam_flare(library, handle, index, flare)
                    for flare in range(int(style.flare_count))
                ],
            }

    @staticmethod
    def _beam_path(library: ctypes.CDLL, handle: c_void_p, beam: int, path: int, entry: str) -> dict[str, Any]:
        """One bolt / branch / ghost as ``(N, 5)`` float32: xyz, width, intensity."""
        info = BeamPath()
        _check(library, getattr(library, entry)(handle, beam, path, byref(info)), entry)
        count = int(info.vertex_count)
        vertices = np.empty((count, 5), dtype=np.float32)
        if count:
            vertices[:, 0:3] = _copy(info.position, count, 3, np.float32)
            vertices[:, 3] = _copy(info.width, count, 1, np.float32).reshape(count)
            vertices[:, 4] = _copy(info.intensity, count, 1, np.float32).reshape(count)
        return {"vertices": vertices, "depth": int(info.depth), "fade": float(info.fade)}

    @staticmethod
    def _beam_flare(library: ctypes.CDLL, handle: c_void_p, beam: int, index: int) -> dict[str, Any]:
        info = BeamFlare()
        _check(library, library.aetherfx_beam_flare(handle, beam, index, byref(info)), "aetherfx_beam_flare")
        return {"position": [float(v) for v in info.position], "radius": float(info.radius),
                "intensity": float(info.intensity)}

    def _trails(self, library: ctypes.CDLL, handle: c_void_p) -> Iterator[dict[str, Any]]:
        count = _check(library, library.aetherfx_runtime_trail_count(handle), "aetherfx_runtime_trail_count")
        for index in range(count):
            info = TrailInfo()
            _check(library, library.aetherfx_trail_info(handle, index, byref(info)), "aetherfx_trail_info")
            ribbons = []
            for ribbon in range(int(info.ribbon_count)):
                verts = POINTER(TrailVertex)()
                vertex_count = c_size_t()
                _check(
                    library,
                    library.aetherfx_trail_ribbon(handle, index, ribbon, byref(verts), byref(vertex_count)),
                    "aetherfx_trail_ribbon",
                )
                ribbons.append(_ribbon_array(verts, int(vertex_count.value)))
            yield {
                "id": _text(info.id),
                "blend": _enum(BLEND_MODES, int(info.blend)),
                "material_id": _text(info.material_id),
                "ribbons": ribbons,
            }


def _ribbon_array(verts: Any, count: int) -> np.ndarray:
    """``aetherfx_trail_vertex[count]`` -> the stream's ``(count, 12)`` layout.

    The C vertex is 13 tightly packed floats; the wire layout drops ``age``
    (column 4) and keeps ``pos3, width, age_norm, u, color4, opacity,
    emissive``.
    """
    if not verts or count == 0:
        return np.zeros((0, 12), dtype=np.float32)
    floats = ctypes.cast(verts, _FLOAT_P)
    packed = np.ctypeslib.as_array(floats, shape=(count, TRAIL_VERTEX_FLOATS))
    return np.ascontiguousarray(packed[:, _RIBBON_COLUMNS], dtype=np.float32)
