"""AetherFX Studio: a local web UI for previewing, editing and generating effects.

One process holds one engine session.  :class:`aetherfx.Client` speaks JSON-RPC
to a single ``aetherfx serve`` subprocess and is *not* thread-safe, so every
engine call in this module - including the ones a generation job makes from its
own thread - is serialised through one :class:`threading.Lock`
(``Studio.engine_lock``) and executed off the event loop with
:func:`starlette.concurrency.run_in_threadpool`.  UI requests stay responsive
while a job runs; they simply queue on the lock.

Layout:

* ``GET /`` and ``GET /static/*`` serve the vanilla HTML/CSS/JS frontend.
* ``/api/*`` is a thin, honest wrapper over docs/AGENT_API.md: nothing is
  reinterpreted, engine errors come back as ``{"error": {"code", "message"}}``
  and the server never dies because the engine said no.
* Rendered files are served only from inside the session output directory
  (resolve + prefix check), so the browser cannot walk the filesystem.

Run it with ``aetherfx-studio`` (see :func:`main`).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import mimetypes
import os
import random
import re
import sys
import threading
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..client import BINARY_ENV_VAR, Client
from ..jsonrpc import ERROR_METHOD_NOT_FOUND, AetherError, JsonDict, TransportError
from .generator import Generator, get_generator
from .jobs import CANCELLED, DONE, ERROR, JobBusy, JobManager

__all__ = ["StudioConfig", "StudioError", "Studio", "create_app", "build_parser", "main"]

LOGGER = logging.getLogger("aetherfx.studio")

STATIC_DIR = Path(__file__).resolve().parent / "static"
NO_STORE = {"Cache-Control": "no-store"}

#: Files the studio is willing to hand to the browser out of the output dir.
SERVABLE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".json", ".mp4", ".webm", ".txt"}


def _repo_root() -> Path:
    """``<repo>/python/aetherfx/studio/server.py`` -> ``<repo>``."""
    return Path(__file__).resolve().parents[3]


def slugify(name: str, fallback: str = "effect") -> str:
    """``"Blue Magic Missile"`` -> ``"blue_magic_missile"``; safe as a filename."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return slug or fallback


# =========================================================================
# configuration and errors
# =========================================================================


@dataclass
class StudioConfig:
    """Everything :func:`create_app` needs.  Tests build one directly."""

    output_dir: Path = field(default_factory=lambda: _repo_root() / "out" / "studio")
    examples_dir: Path = field(default_factory=lambda: _repo_root() / "examples" / "effects")
    binary: str | None = None
    generator: str | None = None
    host: str = "127.0.0.1"
    port: int = 8770
    #: Inject a ready-made client (a real or fake engine) instead of spawning one.
    client: Client | None = None
    #: Serve the MCP endpoint at /mcp on the same engine session (Claude Code / Desktop attach by URL).
    mcp: bool = True

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir).expanduser()
        self.examples_dir = Path(self.examples_dir).expanduser()


class StudioError(Exception):
    """An error the studio itself raises, carrying the HTTP status to use."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def error_response(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    payload: JsonDict = {"code": code, "message": message}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return JSONResponse({"error": payload}, status_code=status, headers=NO_STORE)


def endpoint(handler: Callable[[Request], Any]) -> Callable[[Request], Any]:
    """Turn engine and studio failures into ``{"error": {...}}`` responses.

    An engine that rejects a call is normal operation, not a server fault: the
    studio logs it and keeps running so the browser can fix and retry.
    """

    async def wrapper(request: Request) -> Response:
        try:
            result = await handler(request)
        except StudioError as exc:
            LOGGER.warning("%s %s -> %d %s: %s", request.method, request.url.path, exc.status, exc.code, exc.message)
            return error_response(exc.status, exc.code, exc.message, **exc.extra)
        except AetherError as exc:
            status = 404 if exc.code == ERROR_METHOD_NOT_FOUND else 400
            LOGGER.warning("%s %s -> engine error %s: %s", request.method, request.url.path, exc.aether_code, exc.message)
            return error_response(
                status, exc.aether_code or f"rpc{exc.code}", exc.message, node=exc.node, param=exc.param
            )
        except TransportError as exc:
            LOGGER.error("%s %s -> transport error: %s", request.method, request.url.path, exc)
            return error_response(500, "engine_unavailable", str(exc))
        except JobBusy as exc:
            return error_response(409, "job_running", str(exc), job_id=exc.job_id)
        except Exception as exc:  # noqa: BLE001 - the studio must stay up
            LOGGER.exception("%s %s -> unhandled error", request.method, request.url.path)
            return error_response(500, "internal_error", f"{type(exc).__name__}: {exc}")
        if isinstance(result, Response):
            return result
        return JSONResponse(result, headers=NO_STORE)

    wrapper.__name__ = getattr(handler, "__name__", "endpoint")
    return wrapper


# =========================================================================
# studio state
# =========================================================================


class Studio:
    """The engine session, the lock protecting it, the generator and the jobs."""

    def __init__(self, config: StudioConfig) -> None:
        self.config = config
        self.output_dir = Path(config.output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.examples_dir = Path(config.examples_dir)
        self.effects_dir = self.output_dir / "effects"
        self.frames_dir = self.output_dir / "studio_frames"
        self.preview_dir = self.output_dir / "preview"
        self.jobs_dir = self.output_dir / "jobs"
        for directory in (self.effects_dir, self.frames_dir, self.preview_dir, self.jobs_dir):
            directory.mkdir(parents=True, exist_ok=True)

        #: The one lock every engine call goes through, jobs included.
        self.engine_lock = threading.Lock()
        self.client = config.client or Client(binary=config.binary, output_dir=str(self.output_dir))
        self.generator: Generator = self._make_generator(config.generator)
        self.jobs = JobManager()
        self._vocabulary: JsonDict | None = None
        self._vocabulary_lock = threading.Lock()

    # -- generator ---------------------------------------------------------

    def _make_generator(self, backend: str | None) -> Generator:
        """Build the generator, tolerating older ``generator.py`` signatures."""
        attempts: list[JsonDict] = [
            {"jobs_dir": str(self.jobs_dir), "output_dir": str(self.output_dir)},
            {"jobs_dir": str(self.jobs_dir)},
            {},
        ]
        last: TypeError | None = None
        for extra in attempts:
            try:
                return get_generator(self.client, self.engine_lock, backend, **extra)
            except TypeError as exc:
                last = exc
        raise last or TypeError("get_generator refused every call signature")

    def generator_status(self) -> JsonDict:
        """Read the generator's availability *now* (a session backend changes it)."""
        try:
            available = bool(getattr(self.generator, "available", False))
            reason = getattr(self.generator, "unavailable_reason", None)
            name = str(getattr(self.generator, "name", "unknown"))
        except Exception as exc:  # noqa: BLE001 - a broken backend must not hide the UI
            return {"name": "unknown", "available": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {"name": name, "available": available, "reason": None if available else (reason or "unavailable")}

    @property
    def studio_url(self) -> str:
        host = self.config.host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        return f"http://{host}:{self.config.port}"

    # -- engine ------------------------------------------------------------

    #: Bumped after every mutating engine call from any path (UI, /api/tool, MCP,
    #: generators) so the frontend can follow changes made outside it.
    revision: int = 0
    _mutating_tools: set[str] | None = None

    def _is_mutating(self, tool: str) -> bool:
        if self._mutating_tools is None:
            try:
                infos = self.client.tools()
                self._mutating_tools = {getattr(i, "name", None) or i["name"] for i in infos
                                        if (getattr(i, "mutating", None) if not isinstance(i, dict) else i.get("mutating"))}
            except Exception:  # noqa: BLE001
                self._mutating_tools = set()
        if tool in self._mutating_tools:
            return True
        return tool.startswith(("create_", "set_", "delete_", "duplicate_", "connect_", "disconnect_", "remove_",
                                "load_", "undo", "redo", "clear_", "reset_parameter"))

    #: The studio's working document (a copy of a library entry or a new effect) and where it came from.
    working_id: str | None = None
    working_source: JsonDict | None = None
    #: studio.revision when the working document was loaded/created/saved; dirty = revision differs
    working_revision: int = 0

    def working_dirty(self) -> bool:
        return self.working_id is not None and self.revision != self.working_revision

    def decorate_active(self, active: JsonDict | None) -> JsonDict | None:
        """Replace the engine's 'dirty' flag (true for any new document) with the studio's."""
        if not active:
            return active
        out = dict(active)
        if out.get("effect_id") == self.working_id:
            out["dirty"] = self.working_dirty()
        return out

    def is_builtin_path(self, path: Path | str | None) -> bool:
        if not path:
            return False
        try:
            return Path(path).expanduser().resolve().is_relative_to(self.examples_dir.resolve())
        except (OSError, ValueError):
            return False

    async def library(self) -> list[JsonDict]:
        """Built-in effects (protected) followed by the user's own effects."""
        def listing(directory: Path, builtin: bool) -> list[JsonDict]:
            items: list[JsonDict] = []
            if not directory.is_dir():
                return items
            for path in sorted(directory.glob("*.json")):
                entry: JsonDict = {"name": path.stem, "path": str(path), "builtin": builtin}
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and raw.get("name"):
                        entry["name"] = str(raw["name"])
                    entry["duration"] = raw.get("duration") if isinstance(raw, dict) else None
                except (OSError, ValueError):
                    pass
                try:
                    entry["modified"] = path.stat().st_mtime
                except OSError:
                    entry["modified"] = None
                items.append(entry)
            return items

        builtin = await run_in_threadpool(listing, self.examples_dir, True)
        mine = await run_in_threadpool(listing, self.effects_dir, False)
        return builtin + mine

    async def open_working_copy(self, path: Path) -> JsonDict:
        """Open a library entry as a fresh working document (never the file itself)."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            name = str(raw.get("name") or path.stem) if isinstance(raw, dict) else path.stem
        except (OSError, ValueError):
            name = path.stem
        result = await self.acall("create_effect", name=name, template=str(path))
        await self._replace_working(result.get("effect_id"), {"path": str(path), "builtin": self.is_builtin_path(path), "name": name})
        return result

    async def _replace_working(self, new_id: str | None, source: JsonDict | None) -> None:
        previous = self.working_id
        self.working_id = new_id
        self.working_source = source
        self.working_revision = self.revision
        if previous and previous != new_id:
            try:
                await self.acall("delete_effect", effect_id=previous)
            except Exception:  # noqa: BLE001 - already gone
                pass

    def guard_tool(self, name: str, args: JsonDict | None) -> None:
        """Refuse tool calls that would overwrite a built-in library file."""
        if name not in ("save_effect", "export_effect"):
            return
        args = args or {}
        if name == "export_effect" and args.get("format") not in (None, "json"):
            return
        target = args.get("path")
        if not target and name == "save_effect":
            src = self.working_source or {}
            target = src.get("path") if src.get("builtin") else None
        if target and self.is_builtin_path(target):
            raise StudioError(403, "protected", "built-in library effects are protected; save under a new name in your own library")

    async def stage_defaults(self) -> JsonDict:
        """Per-effect render defaults stored in effect.metadata.render_settings (may be empty)."""
        try:
            doc = await self.acall("get_effect_json")
        except Exception:  # noqa: BLE001
            return {}
        meta = doc.get("metadata") if isinstance(doc, dict) else None
        rs = meta.get("render_settings") if isinstance(meta, dict) else None
        return _parse_settings(rs) if isinstance(rs, dict) else {}

    def note_call(self, tool: str) -> None:
        if self._is_mutating(tool):
            self.revision += 1

    def call(self, tool: str, /, **args: Any) -> JsonDict:
        """Blocking engine call, serialised on :attr:`engine_lock`."""
        with self.engine_lock:
            result = self.client.call(tool, **args)
            self.note_call(tool)
            return result

    def call_with_args(self, tool: str, args: JsonDict) -> JsonDict:
        """Blocking engine call taking the argument object as a mapping."""
        with self.engine_lock:
            result = self.client.call_with(tool, args)
            self.note_call(tool)
            return result

    async def acall(self, tool: str, /, **args: Any) -> JsonDict:
        """:meth:`call` off the event loop."""
        return await run_in_threadpool(lambda: self.call(tool, **args))

    async def acall_many(self, calls: Sequence[tuple[str, JsonDict]]) -> list[JsonDict]:
        """Run several tools under a single acquisition of the lock."""

        def run() -> list[JsonDict]:
            with self.engine_lock:
                out = [self.client.call_with(name, args) for name, args in calls]
                for name, _ in calls:
                    self.note_call(name)
                return out

        return await run_in_threadpool(run)

    def engine_status(self) -> JsonDict:
        """``ping`` the engine; never raises."""
        binary = getattr(self.client, "binary", None)
        try:
            with self.engine_lock:
                ok = bool(self.client.ping().get("ok", False))
        except (AetherError, TransportError, OSError) as exc:
            return {"ok": False, "error": str(exc), "binary": binary}
        return {"ok": ok, "error": None if ok else "engine did not answer ping", "binary": binary}

    async def active_effect(self) -> JsonDict | None:
        """``{effect_id, name, path, dirty}`` for the active effect, or ``None``."""
        listed = await self.acall("list_effects")
        for entry in listed.get("effects") or []:
            if entry.get("active"):
                return {
                    "effect_id": entry.get("effect_id"),
                    "name": entry.get("name"),
                    "path": entry.get("path"),
                    "dirty": bool(entry.get("dirty", False)),
                }
        return None

    async def require_active_effect(self) -> JsonDict:
        active = await self.active_effect()
        if active is None:
            raise StudioError(404, "no_active_effect", "no effect is open; load an example or create a new effect")
        return active

    def vocabulary(self) -> JsonDict:
        """``describe_vocabulary``, cached for the life of the process."""
        with self._vocabulary_lock:
            if self._vocabulary is None:
                self._vocabulary = self.call("describe_vocabulary")
            return self._vocabulary

    # -- paths -------------------------------------------------------------

    def resolve_inside_output(self, raw: str) -> Path:
        """Resolve ``raw`` and refuse anything outside the session output dir."""
        if not raw:
            raise StudioError(400, "bad_request", "path is required")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.output_dir / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise StudioError(400, "bad_request", f"cannot resolve path: {exc}") from exc
        if resolved != self.output_dir and self.output_dir not in resolved.parents:
            raise StudioError(403, "forbidden", "path is outside the session output directory")
        return resolved

    def file_url(self, path: str | os.PathLike[str] | None) -> str | None:
        """A ``/api/file`` URL for ``path`` when it lives inside the output dir."""
        if not path:
            return None
        try:
            resolved = Path(path).resolve()
        except OSError:
            return None
        if resolved != self.output_dir and self.output_dir not in resolved.parents:
            return None
        from urllib.parse import quote  # noqa: PLC0415

        return "/api/file?path=" + quote(str(resolved), safe="")

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001 - shutting down anyway
            LOGGER.debug("client close failed", exc_info=True)


# =========================================================================
# helpers
# =========================================================================


async def read_json(request: Request) -> JsonDict:
    """Parse the request body as a JSON object (an empty body means ``{}``)."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise StudioError(400, "bad_request", f"invalid JSON body: {exc}") from exc
    if not isinstance(data, dict):
        raise StudioError(400, "bad_request", "request body must be a JSON object")
    return data


def _number(data: JsonDict, key: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    value = data.get(key, default)
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StudioError(400, "bad_request", f"{key} must be a number") from exc
    if minimum is not None and number < minimum:
        raise StudioError(400, "bad_request", f"{key} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise StudioError(400, "bad_request", f"{key} must be <= {maximum}")
    return number


def _int(data: JsonDict, key: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    return int(_number(data, key, default, minimum, maximum))


def _string(data: JsonDict, key: str, default: str | None = None, required: bool = False) -> str:
    value = data.get(key, default)
    if value is None or value == "":
        if required:
            raise StudioError(400, "bad_request", f"{key} is required")
        return default or ""
    if not isinstance(value, str):
        raise StudioError(400, "bad_request", f"{key} must be a string")
    return value


def trim_statistics(statistics: Any) -> Any:
    """Drop the per-node plan dump; the UI only needs the summary numbers."""
    if not isinstance(statistics, dict):
        return statistics
    trimmed = dict(statistics)
    plan = trimmed.get("plan")
    if isinstance(plan, dict):
        small = {key: value for key, value in plan.items() if key not in ("nodes", "resources")}
        nodes = plan.get("nodes")
        if isinstance(nodes, list):
            small["node_count"] = len(nodes)
        trimmed["plan"] = small
    return trimmed


def effect_summary(effect: Any, effect_id: str | None, diagnostics: Any = None) -> JsonDict:
    """``{effect_id, name, duration, diagnostics}`` from a load/create result."""
    document = effect if isinstance(effect, dict) else {}
    return {
        "effect_id": effect_id,
        "name": document.get("name"),
        "duration": document.get("duration"),
        "diagnostics": diagnostics,
    }


# =========================================================================
# routes
# =========================================================================




# ---------------------------------------------------------------------------
# randomize: mutate parameter values for exploration
# ---------------------------------------------------------------------------

_RANDOMIZE_SKIP_TYPES = {"camera", "texture", "event", "field"}
_RANDOMIZE_SKIP_PARAMS = {
    "max_particles", "width", "height", "frames", "graph", "path", "segments", "sprite_columns", "sprite_rows",
    "sprite_fps", "points", "source", "primitive", "physics", "kill_on_collision", "sort", "surface_only",
    "cast_shadows", "double_sided", "soft_particle", "shading", "light_type", "collider_type", "trigger",
    "post_type", "volume_type", "field_type", "curve_type", "closed", "phase", "start_time", "duration", "time",
    "max_triggers", "burst_times", "projection_normal", "normal", "direction", "position", "rotation", "scale",
    "target", "up", "origin", "fov", "near", "far", "exposure", "offset", "bounds_min", "bounds_max",
    "resolution", "inherit_position", "visible", "noise_type", "force_type", "render_mode", "voxel_size",
    "collision_radius", "min_vertex_distance", "max_segments", "octaves",
}
_UNIT_RANGE_PARAMS = {"opacity", "friction", "bounce", "gain", "branch_probability", "branch_length",
                      "inherit_velocity", "probability", "flicker_amplitude", "dissolve", "erosion",
                      "ground_albedo", "bloom_intensity", "depth_fade"}
_ENUM_SWAPS = {"shape": {"point", "sphere", "hemisphere", "box", "disc", "ring", "cone", "line"},
               "blend": {"additive", "alpha", "premultiplied"},
               "falloff": {"none", "linear", "inverse_square", "smooth"}}


def _jitter_number(rng: "random.Random", value: float, amount: float, spec: JsonDict, name: str) -> float:
    lo, hi = spec.get("min"), spec.get("max")
    if name in _UNIT_RANGE_PARAMS or (lo == 0 and hi == 1):
        out = value + rng.uniform(-1.0, 1.0) * amount * 0.5
    else:
        base = value
        if base == 0 and isinstance(spec.get("default"), (int, float)) and spec["default"]:
            base = float(spec["default"])
        if base == 0:
            return value
        out = base * math.exp(rng.uniform(-1.0, 1.0) * amount * 1.2)
    if lo is not None:
        out = max(float(lo), out)
    if hi is not None:
        out = min(float(hi), out)
    return out


def _jitter_color(rng: "random.Random", color: list[float], amount: float) -> list[float]:
    import colorsys  # noqa: PLC0415

    rgb = [max(0.0, float(c)) for c in color[:3]]
    alpha = float(color[3]) if len(color) > 3 else 1.0
    peak = max(rgb) or 1.0
    h, s, v = colorsys.rgb_to_hsv(rgb[0] / peak, rgb[1] / peak, rgb[2] / peak)
    h = (h + rng.uniform(-1.0, 1.0) * amount * 0.25) % 1.0
    s = min(1.0, max(0.0, s + rng.uniform(-1.0, 1.0) * amount * 0.3))
    v = min(1.0, max(0.05, v + rng.uniform(-1.0, 1.0) * amount * 0.3))
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    scale = peak * math.exp(rng.uniform(-1.0, 1.0) * amount * 0.5)
    return [round(r * scale, 4), round(g * scale, 4), round(b * scale, 4), alpha]


def randomize_parameters(node_json: JsonDict, spec: JsonDict, effective: JsonDict, amount: float,
                         rng: "random.Random") -> JsonDict:
    """New values for one node. Never touches animated, structural or framing parameters."""
    changes: JsonDict = {}
    raw = node_json.get("parameters") or {}
    for name, pspec in (spec.get("parameters") or {}).items():
        if name in _RANDOMIZE_SKIP_PARAMS:
            continue
        current_raw = raw.get(name)
        if isinstance(current_raw, dict) and "track" in current_raw:
            continue  # keep keyframed envelopes
        value = effective.get(name)
        ptype = pspec.get("type")
        if ptype in ("float", "int") and isinstance(value, (int, float)) and not isinstance(value, bool):
            new = _jitter_number(rng, float(value), amount, pspec, name)
            if ptype == "int":
                new = int(round(new))
            else:
                new = round(new, 4)
            if new != value:
                changes[name] = new
        elif ptype == "color" and isinstance(value, list) and len(value) >= 3:
            changes[name] = _jitter_color(rng, value, amount)
        elif ptype in ("vec2", "vec3") and isinstance(value, list) and name in ("size", "area_size"):
            changes[name] = [round(float(v) * math.exp(rng.uniform(-1.0, 1.0) * amount * 0.8), 4) for v in value]
        elif ptype == "enum" and amount >= 0.6 and name in _ENUM_SWAPS and rng.random() < 0.35:
            options = [e for e in (pspec.get("enum") or []) if e in _ENUM_SWAPS[name] and e != value]
            if options:
                changes[name] = rng.choice(options)
    return changes


_STAGE_KEYS = {"background", "ground_plane", "grid", "ground_albedo", "bloom", "bloom_threshold", "bloom_intensity",
               "bloom_radius", "exposure", "soft_particles", "supersample"}


def _parse_settings(value: Any) -> JsonDict:
    """Render settings override from a JSON string (query) or object (body); unknown keys are dropped."""
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise StudioError(400, "bad_request", f"settings is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise StudioError(400, "bad_request", "settings must be an object")
    return {k: v for k, v in value.items() if k in _STAGE_KEYS}


def _parse_camera(value: Any) -> JsonDict | None:
    """Camera override from a JSON string (query) or object (body): position/target/up/fov."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise StudioError(400, "bad_request", f"camera is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise StudioError(400, "bad_request", "camera must be an object")
    out: JsonDict = {}
    for key in ("position", "target", "up"):
        vec = value.get(key)
        if isinstance(vec, list) and len(vec) == 3:
            out[key] = [float(v) for v in vec]
    if isinstance(value.get("fov"), (int, float)):
        out["fov"] = max(1.0, min(170.0, float(value["fov"])))
    return out or None


def create_app(config: StudioConfig | None = None) -> Starlette:
    """Build the studio application.  ``config.client`` may be a fake engine."""
    studio = Studio(config or StudioConfig())

    # -- static --------------------------------------------------------

    async def index(_request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_STORE)

    # -- status and effects --------------------------------------------

    @endpoint
    async def api_status(_request: Request) -> JsonDict:
        engine = await run_in_threadpool(studio.engine_status)
        active = studio.decorate_active(await studio.active_effect()) if engine.get("ok") else None
        job = studio.jobs.active()
        return {
            "engine": engine,
            "generator": studio.generator_status(),
            "output_dir": str(studio.output_dir),
            "examples_dir": str(studio.examples_dir),
            "studio_url": studio.studio_url,
            "active_effect": active,
            "active_job": job.job_id if job else None,
            "revision": studio.revision,
        }

    @endpoint
    async def api_effects(_request: Request) -> JsonDict:
        def listing(directory: Path, with_mtime: bool) -> list[JsonDict]:
            items: list[JsonDict] = []
            if not directory.is_dir():
                return items
            for path in sorted(directory.glob("*.json")):
                entry: JsonDict = {"name": path.stem, "path": str(path)}
                if with_mtime:
                    try:
                        entry["modified"] = path.stat().st_mtime
                    except OSError:
                        entry["modified"] = None
                items.append(entry)
            return items

        examples = await run_in_threadpool(listing, studio.examples_dir, False)
        saved = await run_in_threadpool(listing, studio.effects_dir, True)
        try:
            listed = await studio.acall("list_effects")
        except (AetherError, TransportError):
            listed = {"effects": []}
        open_effects = [
            {
                "effect_id": entry.get("effect_id"),
                "name": entry.get("name"),
                "active": bool(entry.get("active", False)),
                "dirty": bool(entry.get("dirty", False)),
                "path": entry.get("path"),
            }
            for entry in (listed.get("effects") or [])
        ]
        active = studio.decorate_active(await studio.active_effect())
        working = dict(active) if active else None
        if working is not None:
            working["source"] = studio.working_source
        return {"examples": examples, "saved": saved, "open": open_effects,
                "library": await studio.library(), "working": working}

    @endpoint
    async def api_effect_load(request: Request) -> JsonDict:
        data = await read_json(request)
        raw = _string(data, "path", required=True)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            for base in (studio.examples_dir, studio.effects_dir, studio.output_dir):
                candidate = base / raw
                if candidate.is_file():
                    path = candidate
                    break
        if not path.is_file():
            raise StudioError(404, "not_found", f"no effect file at {path}")
        result = await studio.open_working_copy(path)
        LOGGER.info("opened working copy %s of %s", result.get("effect_id"), path)
        summary = effect_summary(result.get("effect"), result.get("effect_id"), result.get("diagnostics"))
        summary["path"] = str(path)
        summary["builtin"] = studio.is_builtin_path(path)
        return summary

    @endpoint
    async def api_effect_new(request: Request) -> JsonDict:
        data = await read_json(request)
        name = _string(data, "name", "Untitled")
        duration = _number(data, "duration", 2.0, minimum=0.01)
        template = data.get("template") or None
        args: JsonDict = {"name": name, "duration": duration}
        if template and template != "empty":
            args["template"] = template
        result = await studio.acall("create_effect", **args)
        await studio._replace_working(result.get("effect_id"), None)  # noqa: SLF001
        LOGGER.info("created effect %s (%s)", result.get("effect_id"), name)
        return effect_summary(result.get("effect"), result.get("effect_id"), result.get("diagnostics"))

    @endpoint
    async def api_effect_activate(request: Request) -> JsonDict:
        data = await read_json(request)
        effect_id = _string(data, "effect_id", required=True)
        await studio.acall("set_active_effect", effect_id=effect_id)
        active = await studio.active_effect()
        return {"ok": True, "active_effect": active}

    @endpoint
    async def api_effect_save(request: Request) -> JsonDict:
        data = await read_json(request)
        active = await studio.require_active_effect()
        name = _string(data, "name", active.get("name") or "effect").strip() or "effect"
        raw = data.get("path")
        if raw:
            path = Path(str(raw)).expanduser()
            if not path.is_absolute():
                path = studio.effects_dir / path
        else:
            path = studio.effects_dir / f"{slugify(name)}.json"
        if studio.is_builtin_path(path):
            raise StudioError(403, "protected", "built-in library effects are protected; save under a new name")
        # never shadow a built-in name: "Fireball" saved from the library becomes "Fireball copy"
        while (studio.examples_dir / path.name).exists():
            name = name + " copy"
            path = studio.effects_dir / f"{slugify(name)}.json"
        await run_in_threadpool(lambda: path.parent.mkdir(parents=True, exist_ok=True))
        if name != active.get("name"):
            await studio.acall("set_effect_property", name=name)
        result = await studio.acall("save_effect", path=str(path))
        saved = result.get("path") or str(path)
        studio.working_source = {"path": saved, "builtin": False, "name": name}
        studio.working_revision = studio.revision
        LOGGER.info("saved effect %s as %s (%s)", active.get("effect_id"), name, saved)
        return {"path": saved, "effect_id": active.get("effect_id"), "name": name}

    @endpoint
    async def api_effect(_request: Request) -> JsonDict:
        active = await studio.require_active_effect()
        effect, graph, timeline, statistics = await studio.acall_many(
            [("get_effect_json", {}), ("inspect_graph", {}), ("get_timeline", {}), ("inspect_statistics", {})]
        )
        return {
            "active_effect": active,
            "working_source": studio.working_source,
            "stage_defaults": await studio.stage_defaults(),
            "effect": effect,
            "graph": graph,
            "timeline": timeline,
            "statistics": trim_statistics(statistics),
        }

    # -- nodes and parameters ------------------------------------------

    @endpoint
    async def api_node(request: Request) -> JsonDict:
        node_id = request.path_params["node_id"]
        return await studio.acall("inspect_node", node_id=node_id)

    @endpoint
    async def api_param(request: Request) -> JsonDict:
        data = await read_json(request)
        node_id = _string(data, "node_id", required=True)
        name = _string(data, "name", required=True)
        if "value" not in data:
            raise StudioError(400, "bad_request", "value is required")
        result = await studio.acall("set_parameter", node_id=node_id, name=name, value=data["value"])
        return {
            "ok": True,
            "node_id": result.get("node_id", node_id),
            "name": result.get("name", name),
            "value": result.get("value", data["value"]),
            "diagnostics": result.get("diagnostics"),
        }

    @endpoint
    async def api_node_property(request: Request) -> JsonDict:
        data = await read_json(request)
        node_id = _string(data, "node_id", required=True)
        args: JsonDict = {"node_id": node_id}
        if "enabled" in data and data["enabled"] is not None:
            args["enabled"] = bool(data["enabled"])
        for key in ("layer", "parent"):
            if key in data and data[key] is not None:
                args[key] = str(data[key])
        if len(args) == 1:
            raise StudioError(400, "bad_request", "one of enabled, layer or parent is required")
        result = await studio.acall("set_node_property", **args)
        return {"ok": True, "node": result.get("node"), "diagnostics": result.get("diagnostics")}

    @endpoint
    async def api_node_delete(request: Request) -> JsonDict:
        data = await read_json(request)
        node_id = _string(data, "node_id", required=True)
        result = await studio.acall("delete_node", node_id=node_id)
        LOGGER.info("deleted node %s", node_id)
        return {"ok": True, "dangling": result.get("dangling"), "diagnostics": result.get("diagnostics")}

    def history_endpoint(tool: str) -> Callable[[Request], Any]:
        @endpoint
        async def handler(_request: Request) -> JsonDict:
            result = await studio.acall(tool)
            return {
                "ok": bool(result.get("ok", True)),
                "remaining": result.get("remaining"),
                "redo_available": result.get("redo_available"),
                "diagnostics": result.get("diagnostics"),
            }

        handler.__name__ = f"api_{tool}"
        return handler

    # -- rendering ------------------------------------------------------

    @endpoint
    async def api_frame(request: Request) -> Response:
        await studio.require_active_effect()
        params = request.query_params
        try:
            time_s = float(params.get("time", 0.0))
            width = int(float(params.get("width", 384)))
            height = int(float(params.get("height", 384)))
        except ValueError as exc:
            raise StudioError(400, "bad_request", f"invalid frame parameters: {exc}") from exc
        width = max(16, min(width, 2048))
        height = max(16, min(height, 2048))
        camera = _parse_camera(params.get("camera"))
        settings = {**(await studio.stage_defaults()), **_parse_settings(params.get("settings"))}
        tag_src = json.dumps({"c": camera, "s": settings}, sort_keys=True)
        cam_tag = f"_{abs(hash(tag_src)) % 10**8:08d}" if (camera or settings) else ""
        time_s = max(0.0, time_s)
        path = studio.frames_dir / f"frame_{width}x{height}_{time_s:.3f}{cam_tag}.png"
        await run_in_threadpool(lambda: path.parent.mkdir(parents=True, exist_ok=True))
        result = await studio.acall(
            "render_frame", time=time_s, width=width, height=height, path=str(path), format="png",
            **({"camera": camera} if camera else {}), **({"settings": settings} if settings else {})
        )
        rendered = Path(result.get("path") or path)
        data = await run_in_threadpool(rendered.read_bytes)
        headers = dict(NO_STORE)
        stats = result.get("render_statistics")
        if isinstance(stats, dict):
            headers["X-Aether-Render"] = json.dumps(stats, separators=(",", ":"))
        headers["X-Aether-Time"] = f"{time_s:.3f}"
        return Response(data, media_type="image/png", headers=headers)

    @endpoint
    async def api_preview(request: Request) -> JsonDict:
        active = await studio.require_active_effect()
        data = await read_json(request)
        fps = _number(data, "fps", 24.0, minimum=1.0, maximum=120.0)
        preview_settings = {**(await studio.stage_defaults()), **_parse_settings(data.get("settings"))}
        width = _int(data, "width", 384, minimum=16, maximum=2048)
        height = _int(data, "height", 384, minimum=16, maximum=2048)
        start = _number(data, "start", 0.0, minimum=0.0)
        end = data.get("end")
        out_dir = studio.preview_dir / slugify(active.get("name") or "effect")
        await run_in_threadpool(lambda: out_dir.mkdir(parents=True, exist_ok=True))
        args: JsonDict = {
            "fps": fps,
            "start": start,
            "width": width,
            "height": height,
            "out_dir": str(out_dir),
            **({"camera": _parse_camera(data.get("camera"))} if data.get("camera") else {}),
            **({"settings": preview_settings} if preview_settings else {}),
            "contact_sheet": True,
        }
        if end is not None:
            args["end"] = _number(data, "end", 0.0, minimum=0.0)
        result = await studio.acall("render_preview", **args)
        frames = [studio.file_url(frame) for frame in (result.get("frames") or [])]
        frames = [url for url in frames if url]
        count = len(frames)
        LOGGER.info("rendered preview: %d frames at %g fps into %s", count, fps, out_dir)
        return {
            "frames": frames,
            "fps": fps,
            "count": count,
            "start": start,
            "duration": ((count - 1) / fps) if (fps and count > 1) else 0.0,
            "width": width,
            "height": height,
            "contact_sheet": studio.file_url(result.get("contact_sheet")),
            "out_dir": str(out_dir),
            "statistics": trim_statistics(result.get("statistics")),
            "notes": result.get("notes") or [],
        }

    @endpoint
    async def api_file(request: Request) -> Response:
        raw = request.query_params.get("path", "")
        path = studio.resolve_inside_output(raw)
        if not path.is_file():
            raise StudioError(404, "not_found", f"no file at {path}")
        if path.suffix.lower() not in SERVABLE_SUFFIXES:
            raise StudioError(403, "forbidden", f"refusing to serve {path.suffix or 'extension-less'} files")
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, headers=NO_STORE)

    @endpoint
    async def api_vocabulary(_request: Request) -> JsonDict:
        return await run_in_threadpool(studio.vocabulary)

    # -- raw tool access (used by out-of-process generation workers) ----

    @endpoint
    async def api_tools(_request: Request) -> JsonDict:
        return await studio.acall("tools/list")

    @endpoint
    async def api_tool(request: Request) -> JsonDict:
        data = await read_json(request)
        name = _string(data, "name", required=True)
        args = data.get("args") or {}
        studio.guard_tool(name, args)
        if not isinstance(args, dict):
            raise StudioError(400, "bad_request", "args must be a JSON object")
        LOGGER.info("tool %s(%s)", name, ", ".join(sorted(args)))
        return await run_in_threadpool(lambda: studio.call_with_args(name, args))

    # -- generation jobs ------------------------------------------------


    @endpoint
    async def api_randomize(request: Request) -> JsonDict:
        body = await read_json(request)
        amount = min(1.0, max(0.02, float(body.get("amount", 0.35))))
        target = body.get("node_id")
        seed = body.get("seed")
        rng = random.Random(seed)
        graph = await studio.acall("inspect_graph")
        nodes = [n for n in graph.get("nodes", []) if n.get("enabled", True)]
        if target:
            nodes = [n for n in nodes if n.get("id") == target]
            if not nodes:
                raise StudioError(404, "not_found", f"no node {target!r}")
        changed_nodes = 0
        changed_params = 0
        diagnostics: list[JsonDict] = []
        for entry in nodes:
            if entry.get("type") in _RANDOMIZE_SKIP_TYPES:
                continue
            info = await studio.acall("inspect_node", node_id=entry["id"])
            changes = randomize_parameters(info.get("node") or {}, info.get("spec") or {},
                                           info.get("effective_parameters") or {}, amount, rng)
            if not changes:
                continue
            result = await studio.acall("set_parameters", node_id=entry["id"], parameters=changes)
            changed_nodes += 1
            changed_params += len(changes)
            for item in (result.get("diagnostics") or {}).get("items", []):
                if item.get("severity") == "error":
                    diagnostics.append(item)
        new_seed = None
        if not target and rng.random() < amount + 0.3:
            new_seed = rng.randrange(1, 1_000_000)
            await studio.acall("set_effect_property", seed=new_seed)
        return {"changed_nodes": changed_nodes, "changed_params": changed_params, "seed": new_seed,
                "errors": diagnostics}

    @endpoint
    async def api_generate(request: Request) -> JsonDict:
        data = await read_json(request)
        prompt = _string(data, "prompt", required=True).strip()
        if not prompt:
            raise StudioError(400, "bad_request", "prompt is required")
        mode = _string(data, "mode", "new")
        if mode not in ("new", "modify"):
            raise StudioError(400, "bad_request", "mode must be 'new' or 'modify'")
        status = studio.generator_status()
        if not status.get("available"):
            raise StudioError(503, "generator_unavailable", status.get("reason") or "no generator backend configured")
        if mode == "modify":
            await studio.require_active_effect()

        generator = studio.generator

        def target(job: Any) -> None:
            def sink(event: JsonDict) -> None:
                item = dict(event) if isinstance(event, dict) else {"kind": "text", "text": str(event)}
                if item.get("kind") == "image":
                    url = studio.file_url(item.get("path"))
                    if url:
                        item["url"] = url
                job.add_event(item)

            job.add_event({"kind": "status", "text": f"{generator.name}: {mode} - {prompt}"})
            try:
                result = generator.generate(prompt, mode=mode, on_event=sink, cancel=job.cancel)
            except Exception as exc:  # noqa: BLE001 - reported to the browser, not fatal
                LOGGER.exception("generation job %s failed", job.job_id)
                job.add_event({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
                job.finish(ERROR, summary=str(exc))
                return
            effect_id = getattr(result, "effect_id", None)
            summary = getattr(result, "summary", "") or ""
            if job.cancel.is_set():
                job.finish(CANCELLED, effect_id, summary or "cancelled")
            else:
                job.finish(DONE, effect_id, summary)
            LOGGER.info("generation job %s finished: %s (%s)", job.job_id, job.status, effect_id)

        job = studio.jobs.start(prompt, mode, target)
        LOGGER.info("generation job %s started (%s, %s)", job.job_id, generator.name, mode)
        return {"job_id": job.job_id, "status": job.status, "generator": generator.name}

    @endpoint
    async def api_job(request: Request) -> JsonDict:
        job = studio.jobs.get(request.path_params["job_id"])
        if job is None:
            raise StudioError(404, "not_found", "unknown job id")
        try:
            since = int(request.query_params.get("since", 0))
        except ValueError:
            since = 0
        return job.snapshot(since)

    @endpoint
    async def api_job_cancel(request: Request) -> JsonDict:
        job = studio.jobs.get(request.path_params["job_id"])
        if job is None:
            raise StudioError(404, "not_found", "unknown job id")
        job.request_cancel()
        job.add_event({"kind": "status", "text": "cancellation requested"})
        LOGGER.info("generation job %s cancel requested", job.job_id)
        return {"ok": True, "status": job.status}

    routes = [
        Route("/", index),
        Route("/api/status", api_status),
        Route("/api/effects", api_effects),
        Route("/api/effects/load", api_effect_load, methods=["POST"]),
        Route("/api/effects/new", api_effect_new, methods=["POST"]),
        Route("/api/effects/activate", api_effect_activate, methods=["POST"]),
        Route("/api/effects/save", api_effect_save, methods=["POST"]),
        Route("/api/effect", api_effect),
        Route("/api/node/property", api_node_property, methods=["POST"]),
        Route("/api/node/delete", api_node_delete, methods=["POST"]),
        Route("/api/node/{node_id}", api_node),
        Route("/api/param", api_param, methods=["POST"]),
        Route("/api/undo", history_endpoint("undo"), methods=["POST"]),
        Route("/api/redo", history_endpoint("redo"), methods=["POST"]),
        Route("/api/frame", api_frame),
        Route("/api/preview", api_preview, methods=["POST"]),
        Route("/api/file", api_file),
        Route("/api/vocabulary", api_vocabulary),
        Route("/api/tools", api_tools),
        Route("/api/tool", api_tool, methods=["POST"]),
        Route("/api/randomize", api_randomize, methods=["POST"]),
        Route("/api/generate", api_generate, methods=["POST"]),
        Route("/api/jobs/{job_id}", api_job),
        Route("/api/jobs/{job_id}/cancel", api_job_cancel, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]

    mcp_manager = None
    if studio.config.mcp:
        try:
            from mcp.server.streamable_http_manager import StreamableHTTPSessionManager  # noqa: PLC0415

            from ..mcp_server import AetherMCPServer  # noqa: PLC0415
            from .locked_client import LockedClient  # noqa: PLC0415

            class _StudioClient(LockedClient):
                """Locked client that also bumps the studio revision (MCP-driven edits)."""

                @staticmethod
                def _guard(tool_name: str, args: dict[str, Any] | None) -> None:
                    try:
                        studio.guard_tool(tool_name, args)
                    except StudioError as exc:
                        raise AetherError(-32602, str(exc), aether_code="E403") from exc

                def call(self, tool_name: str, /, **args: Any) -> Any:
                    self._guard(tool_name, args)
                    result = super().call(tool_name, **args)
                    studio.note_call(tool_name)
                    return result

                def call_with(self, name: str, args: dict[str, Any] | None = None) -> Any:
                    self._guard(name, dict(args or {}))
                    result = super().call_with(name, args)
                    studio.note_call(name)
                    return result

            mcp_server = AetherMCPServer(_StudioClient(studio.client, studio.engine_lock))  # type: ignore[arg-type]
            mcp_manager = StreamableHTTPSessionManager(app=mcp_server.build_server())

            class _McpEndpoint:
                async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                    await mcp_manager.handle_request(scope, receive, send)

            routes.insert(0, Route("/mcp", _McpEndpoint(), methods=["GET", "POST", "DELETE"]))
        except Exception as exc:  # noqa: BLE001 - the studio still works without MCP
            LOGGER.warning("MCP endpoint disabled: %s", exc)
            mcp_manager = None

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        LOGGER.info("output dir: %s", studio.output_dir)
        LOGGER.info("examples dir: %s", studio.examples_dir)
        status = await run_in_threadpool(studio.engine_status)
        if status.get("ok"):
            LOGGER.info("engine ready: %s", status.get("binary"))
        else:
            LOGGER.warning("engine not ready: %s", status.get("error"))
        generator = studio.generator_status()
        if generator.get("available"):
            LOGGER.info("generator: %s", generator.get("name"))
        else:
            LOGGER.warning("generator unavailable: %s", generator.get("reason"))
        try:
            if mcp_manager is not None:
                LOGGER.info("MCP endpoint: http://%s:%s/mcp", studio.config.host, studio.config.port)
                async with mcp_manager.run():
                    yield
            else:
                yield
        finally:
            await run_in_threadpool(studio.close)

    app = Starlette(routes=routes, lifespan=lifespan)
    app.router.redirect_slashes = False
    app.state.studio = studio
    app.state.config = studio.config
    return app


# =========================================================================
# entry point
# =========================================================================


def build_parser() -> argparse.ArgumentParser:
    repo = _repo_root()
    parser = argparse.ArgumentParser(
        prog="aetherfx-studio",
        description="Local web UI for previewing, editing and generating AetherFX effects.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8770, help="port (default 8770).")
    parser.add_argument(
        "--binary",
        default=None,
        help=f"path to the engine binary (default: ${BINARY_ENV_VAR}, build/bin/aetherfx, then PATH).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(repo / "out" / "studio"),
        help="session output directory; renders and saved effects land here.",
    )
    parser.add_argument(
        "--examples-dir",
        default=str(repo / "examples" / "effects"),
        help="directory scanned for example effects.",
    )
    parser.add_argument(
        "--generator",
        default=None,
        choices=["auto", "api", "claude-code", "session", "none"],
        help="generator backend (default: auto, or $AETHERFX_GENERATOR).",
    )
    parser.add_argument("--no-open", action="store_true", help="do not open a browser window at startup.")
    parser.add_argument("--log-level", default="info", help="uvicorn log level (default info).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Start the studio.  Returns a process exit code."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config = StudioConfig(
        output_dir=Path(args.output_dir),
        examples_dir=Path(args.examples_dir),
        binary=args.binary,
        generator=args.generator,
        host=args.host,
        port=args.port,
    )
    app = create_app(config)
    url = app.state.studio.studio_url
    LOGGER.info("AetherFX Studio on %s", url)
    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    import uvicorn  # noqa: PLC0415 - keep import cost out of create_app

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level, access_log=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
