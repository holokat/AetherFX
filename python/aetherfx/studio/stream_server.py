"""WebSocket + resource routes that feed the studio's GPU viewer.

The protocol lives in :mod:`aetherfx.studio.stream` (frame message layout,
resources message, client commands).  This module is the transport: it owns one
:class:`~aetherfx.studio.stream.FrameSource` per WebSocket connection, paces
playback against the wall clock and serves the textures and meshes the viewer
asks for over plain HTTP.

Shape of things:

* ``WS /ws/stream`` - one connection, one source.  Sources are never shared, so
  two browser tabs scrub independently.  Simulation happens in the threadpool
  (:func:`~starlette.concurrency.run_in_threadpool`) so the event loop keeps
  answering commands while a heavy frame is being built.
* ``GET /api/stream/texture/{id}.png`` and ``GET /api/stream/mesh/{id}.json``
  read from a studio-level cache of the *last opened* resource set.  Per
  connection state would be awkward for a plain GET (the browser's ``<img>``
  and ``fetch`` do not carry the socket), and resources are a property of the
  effect, not of the viewer, so one cache per studio is the honest model.

Playback drops frames instead of queueing them: the target time is derived from
the wall clock, so a slow client or a slow frame simply skips ahead rather than
building a backlog the user would see as lag.

What a ``NativeFrameSource`` must put in ``resources()`` for the HTTP routes to
work (everything is optional; missing entries just 404):

    {"textures": {"<id>": {"png": b"\\x89PNG...",    # sRGB PNG bytes
                           "width": 256, "height": 64,
                           "frames": 4, "frame_width": 64}},
     "meshes":   {"<id>": {"positions": [...], "normals": [...], "uvs": [...],
                           "indices": [...],
                           "variants": [{"positions": ..., "normals": ...,
                                         "uvs": ..., "indices": ...}, ...]}}}

Arrays may be python lists, numpy arrays or anything ``numpy`` can flatten.  A
texture entry that already carries a ``url`` is passed through untouched (the
source may serve its own bytes from elsewhere).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import quote

import numpy as np
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from .stream import FrameSource, MockFrameSource, encode_frame

__all__ = ["create_frame_source", "ResourceCache", "stream_routes", "public_resources", "mesh_payload",
           "source_time_scale"]

LOGGER = logging.getLogger("aetherfx.studio.stream")

DEFAULT_FPS = 60.0
MAX_FPS = 240.0
#: Playback speed multiplier limits (1.0 = real time).
MIN_SPEED = 0.1
MAX_SPEED = 8.0
NO_STORE = {"Cache-Control": "no-store"}
#: Versioned (content-addressed) resource URLs never change their bytes.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
#: How many versioned blobs the cache keeps across effect switches and clients.
MAX_VERSIONED_BLOBS = 256


# =========================================================================
# source selection
# =========================================================================


def create_frame_source() -> FrameSource:
    """The real engine when it is available, the synthetic cloud otherwise.

    ``aetherfx.studio.native_source`` is written against the same
    :class:`FrameSource` protocol and loads the engine through a C library; it
    is optional, and the studio must stay usable (and the viewer developable)
    without it.
    """
    try:
        from . import native_source  # noqa: PLC0415 - optional, probed at runtime
    except Exception as exc:  # noqa: BLE001 - any import failure means "not available"
        LOGGER.debug("native frame source unavailable (%s); using the mock source", exc)
        return MockFrameSource()
    factory = getattr(native_source, "NativeFrameSource", None) or getattr(native_source, "create_frame_source", None)
    if factory is None:
        LOGGER.warning("aetherfx.studio.native_source has no NativeFrameSource; using the mock source")
        return MockFrameSource()
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 - a broken engine must not kill the viewer
        LOGGER.warning("NativeFrameSource() failed (%s); using the mock source", exc)
        return MockFrameSource()


# =========================================================================
# resources
# =========================================================================


def _flat_floats(value: Any) -> list[float]:
    if value is None:
        return []
    array = np.asarray(value, dtype=np.float32).ravel()
    return [float(v) for v in array]


def _flat_ints(value: Any) -> list[int]:
    if value is None:
        return []
    array = np.asarray(value).ravel().astype(np.uint32, copy=False)
    return [int(v) for v in array]


def _mesh_block(desc: Any) -> dict[str, list[Any]]:
    if not isinstance(desc, dict):
        return {"positions": [], "normals": [], "uvs": [], "indices": []}
    return {
        "positions": _flat_floats(desc.get("positions")),
        "normals": _flat_floats(desc.get("normals")),
        "uvs": _flat_floats(desc.get("uvs")),
        "indices": _flat_ints(desc.get("indices")),
    }


def mesh_payload(desc: Any) -> dict[str, Any]:
    """``{positions, normals, uvs, indices, variants:[{...}]}`` for the viewer."""
    payload = _mesh_block(desc)
    variants = desc.get("variants") if isinstance(desc, dict) else None
    payload["variants"] = [_mesh_block(v) for v in variants] if isinstance(variants, list) else []
    return payload


def _variant_count(desc: Any) -> int:
    if not isinstance(desc, dict):
        return 1
    variants = desc.get("variants")
    if isinstance(variants, list):
        return max(1, len(variants))
    try:
        return max(1, int(variants or 1))
    except (TypeError, ValueError):
        return 1



def _texture_bytes(desc: Any) -> bytes | None:
    if not isinstance(desc, dict):
        return None
    png = desc.get("png") or desc.get("data") or desc.get("bytes")
    return bytes(png) if isinstance(png, (bytes, bytearray, memoryview)) else None


def texture_digest(desc: Any) -> str | None:
    """Short content hash of a texture's PNG bytes (``None`` when there are no bytes)."""
    png = _texture_bytes(desc)
    return hashlib.sha1(png).hexdigest()[:16] if png else None


def mesh_digest(desc: Any) -> str | None:
    """Short content hash of a mesh's JSON payload (``None`` for an empty mesh)."""
    payload = mesh_payload(desc)
    if not payload.get("positions"):
        return None
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def versioned_url(url: str, digest: str | None) -> str:
    """``url?v=<digest>``: effects reuse ids such as ``tex_flame``, so the id alone is not an address.

    Without the version a browser (or a second client on the same studio) can be handed another
    effect's pixels for the same URL, and a flipbook sliced with the wrong frame count draws as
    hard-edged blades.
    """
    return f"{url}?v={digest}" if digest else url


def public_resources(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip the bytes out of a source's ``resources()`` and point at our routes."""
    out: dict[str, Any] = {k: v for k, v in (raw or {}).items() if k not in ("textures", "meshes")}
    out["type"] = "resources"

    textures: dict[str, Any] = {}
    for tid, desc in ((raw or {}).get("textures") or {}).items():
        desc = desc if isinstance(desc, dict) else {}
        meta = {k: v for k, v in desc.items() if k not in ("png", "data", "pixels", "bytes")}
        meta.setdefault("frames", 1)
        meta["url"] = desc.get("url") or versioned_url(
            f"/api/stream/texture/{quote(str(tid), safe='')}.png", texture_digest(desc))
        textures[str(tid)] = meta
    out["textures"] = textures

    meshes: dict[str, Any] = {}
    for mid, desc in ((raw or {}).get("meshes") or {}).items():
        desc = desc if isinstance(desc, dict) else {}
        meta = {k: v for k, v in desc.items() if k in ("name", "bounds", "triangles")}
        meta["variants"] = _variant_count(desc)
        meta["url"] = desc.get("url") or versioned_url(
            f"/api/stream/mesh/{quote(str(mid), safe='')}.json", mesh_digest(desc))
        meshes[str(mid)] = meta
    out["meshes"] = meshes
    return out



def clamp_speed(value: Any, default: float = 1.0) -> float:
    """Playback speed multiplier from a client command, clamped to a sane range."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return default
    if speed != speed or speed <= 0.0:  # NaN or non-positive
        return default
    return max(MIN_SPEED, min(MAX_SPEED, speed))


def source_time_scale(source: Any, default: float = 1.0) -> float:
    """The effect's own playback speed, or ``default`` for a source without one.

    ``time_scale`` is optional on :class:`FrameSource`, and a source that is
    between effects may raise rather than answer, so anything that is not a
    positive number means "plays at 1x".
    """
    getter = getattr(source, "time_scale", None)
    if getter is None:
        return default
    try:
        scale = float(getter())
    except Exception:  # noqa: BLE001 - a source without an effect open is normal
        return default
    if scale != scale or scale <= 0.0:  # NaN or non-positive
        return default
    return scale


def playback_target(origin_time: float, elapsed_wall: float, speed: float, time_scale: float = 1.0) -> float:
    """Effect time reached after ``elapsed_wall`` seconds of wall clock.

    Two multipliers, and they compose.  ``speed`` is the *viewer's* play-bar
    choice - how fast the person watching wants to see it, and not part of the
    effect.  ``time_scale`` is the *effect's* own speed, authored in the
    document and driven by its Speed control, so it is what a game would play
    the effect at too (docs/RUNTIME.md 11).
    """
    return origin_time + elapsed_wall * speed * time_scale


class ResourceCache:
    """The last opened resource set, so plain GETs can serve textures/meshes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._textures: dict[str, bytes] = {}
        self._meshes: dict[str, dict[str, Any]] = {}
        # (id, digest) -> bytes / payload, across effect switches and clients (bounded LRU)
        self._texture_versions: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._mesh_versions: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

    def update(self, raw: dict[str, Any]) -> None:
        textures: dict[str, bytes] = {}
        texture_versions: dict[tuple[str, str], bytes] = {}
        for tid, desc in ((raw or {}).get("textures") or {}).items():
            png = _texture_bytes(desc)
            if png is not None:
                textures[str(tid)] = png
                texture_versions[(str(tid), hashlib.sha1(png).hexdigest()[:16])] = png
        meshes: dict[str, dict[str, Any]] = {}
        mesh_versions: dict[tuple[str, str], dict[str, Any]] = {}
        for mid, desc in ((raw or {}).get("meshes") or {}).items():
            payload = mesh_payload(desc)
            if payload["positions"]:
                meshes[str(mid)] = payload
                digest = mesh_digest(desc)
                if digest:
                    mesh_versions[(str(mid), digest)] = payload
        with self._lock:
            self._textures = textures
            self._meshes = meshes
            for store, fresh in ((self._texture_versions, texture_versions), (self._mesh_versions, mesh_versions)):
                for key, value in fresh.items():
                    store[key] = value
                    store.move_to_end(key)
                while len(store) > MAX_VERSIONED_BLOBS:
                    store.popitem(last=False)

    def texture(self, tid: str, version: str | None = None) -> bytes | None:
        """The PNG for ``tid``; with ``version`` exactly that content or ``None``, never a sibling's."""
        with self._lock:
            if version:
                return self._texture_versions.get((tid, version))
            return self._textures.get(tid)

    def mesh(self, mid: str, version: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if version:
                return self._mesh_versions.get((mid, version))
            return self._meshes.get(mid)


def resource_cache(studio: Any) -> ResourceCache:
    """The studio's one cache, created on first use (no change to ``Studio``)."""
    cache = getattr(studio, "stream_resources", None)
    if not isinstance(cache, ResourceCache):
        cache = ResourceCache()
        studio.stream_resources = cache
    return cache


# =========================================================================
# one connection
# =========================================================================


class _Connection:
    """A viewer socket: one source, one optional playback task."""

    def __init__(self, studio: Any, websocket: WebSocket) -> None:
        self.studio = studio
        self.websocket = websocket
        self.source: FrameSource | None = None
        self.source_lock = asyncio.Lock()      # the source is not thread-safe
        self.play_task: asyncio.Task[None] | None = None
        self.time = 0.0
        self.fps = DEFAULT_FPS
        self.loop_playback = True
        self.speed = 1.0            # the play bar's multiplier (the viewer's choice)
        self.time_scale = 1.0       # the effect's own speed (the document's)
        self.duration = 1.0         # effect seconds
        self.opened = False
        self._resources: dict[str, Any] | None = None

    # -- lifecycle ------------------------------------------------------

    async def run(self) -> None:
        self.source = await run_in_threadpool(create_frame_source)
        LOGGER.info("stream client connected (%s)", type(self.source).__name__)
        await self.open()
        async for text in self.websocket.iter_text():
            try:
                command = json.loads(text)
            except ValueError:
                await self.send_error("bad_command", "command is not valid JSON")
                continue
            if not isinstance(command, dict):
                await self.send_error("bad_command", "command must be an object")
                continue
            await self.handle(command)

    async def aclose(self) -> None:
        await self.stop_playback()
        if self.source is not None:
            source, self.source = self.source, None
            try:
                await run_in_threadpool(source.close)
            except Exception:  # noqa: BLE001 - shutting down anyway
                LOGGER.debug("frame source close failed", exc_info=True)

    # -- commands -------------------------------------------------------

    async def handle(self, command: dict[str, Any]) -> None:
        kind = str(command.get("type") or "")
        if kind == "open":
            # Live editing: the frontend re-opens after every parameter change, so
            # a running playback has to survive it or the picture would stop the
            # moment the user touches a slider.
            resume = self.play_task is not None
            await self.stop_playback()
            await self.open()
            if resume and self.opened:
                self.play_task = asyncio.create_task(self._play_loop())
        elif kind == "resources":
            await self.send_resources()
        elif kind == "play":
            await self.start_playback(command)
        elif kind == "pause":
            await self.stop_playback()
            await self.send_state()
        elif kind == "seek":
            await self.stop_playback()
            self.time = self._clamp(_float(command.get("time"), 0.0))
            await self.send_frame(self.time)
        elif kind == "step":
            await self.stop_playback()
            frames = _float(command.get("frames"), 1.0)
            dt = await self._fixed_dt()
            self.time = self._clamp(self.time + frames * dt)
            await self.send_frame(self.time)
        else:
            await self.send_error("bad_command", f"unknown command {kind!r}")

    async def open(self) -> None:
        """Hand the studio's active effect to the source and publish resources."""
        source = self.source
        if source is None:
            return
        try:
            effect = await self.studio.acall("get_effect_json")
        except Exception as exc:  # noqa: BLE001 - no effect open is normal
            self.opened = False
            await self.send_error("no_effect", f"{type(exc).__name__}: {exc}")
            return
        async with self.source_lock:
            try:
                await run_in_threadpool(source.open, effect if isinstance(effect, dict) else {})
                raw = await run_in_threadpool(source.resources)
                self.duration = max(0.0, float(await run_in_threadpool(source.duration)))
                # Re-read on every open: the frontend re-opens after every
                # control change, so moving the Speed slider must re-pace
                # playback without restarting it.
                self.time_scale = await run_in_threadpool(source_time_scale, source)
            except Exception as exc:  # noqa: BLE001 - a bad effect must not kill the socket
                LOGGER.warning("frame source open failed: %s", exc, exc_info=True)
                self.opened = False
                await self.send_error("open_failed", f"{type(exc).__name__}: {exc}")
                return
        self.opened = True
        raw = raw if isinstance(raw, dict) else {}
        resource_cache(self.studio).update(raw)
        self._resources = public_resources(raw)
        self.time = self._clamp(self.time)
        # The resources message carries the effect block, including the speed a
        # control may just have changed, so the open handshake stays exactly
        # two messages: resources, then the frame at the current time.
        await self.send_json(self._resources)
        await self.send_frame(self.time)

    async def start_playback(self, command: dict[str, Any]) -> None:
        await self.stop_playback()
        if not self.opened:
            await self.open()
            if not self.opened:
                return
        self.fps = max(1.0, min(MAX_FPS, _float(command.get("fps"), self.fps)))
        if "loop" in command:
            self.loop_playback = bool(command.get("loop"))
        if "speed" in command:
            self.speed = clamp_speed(command.get("speed"), self.speed)
        if "time" in command:
            self.time = self._clamp(_float(command.get("time"), self.time))
        self.play_task = asyncio.create_task(self._play_loop())

    async def stop_playback(self) -> None:
        task, self.play_task = self.play_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - the task is going away either way
            LOGGER.debug("playback task ended with an error", exc_info=True)

    async def _play_loop(self) -> None:
        """Wall-clock paced playback; a slow frame skips ahead instead of queueing."""
        interval = 1.0 / self.fps
        origin_wall = time.monotonic()
        origin_time = self.time
        try:
            while True:
                started = time.monotonic()
                # Read both multipliers per frame: an `open` while playing (a
                # slider moved) swaps the effect's speed underneath us, and the
                # origin is fixed, so the rate changes without a time jump.
                target = playback_target(origin_time, started - origin_wall, self.speed, self.time_scale)
                if self.duration > 0.0 and target >= self.duration:
                    if self.loop_playback:
                        origin_wall = started
                        origin_time = 0.0
                        target = 0.0
                    else:
                        self.time = self.duration
                        await self.send_frame(self.duration)
                        await self.send_state(playing=False)
                        return
                self.time = max(0.0, target)
                await self.send_frame(self.time)
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
        except asyncio.CancelledError:
            raise
        except (WebSocketDisconnect, RuntimeError):
            return
        except Exception as exc:  # noqa: BLE001 - report and stop, never crash the app
            LOGGER.warning("playback stopped: %s", exc, exc_info=True)
            await self.send_error("frame_failed", f"{type(exc).__name__}: {exc}")

    # -- sending --------------------------------------------------------

    async def send_frame(self, when: float) -> None:
        source = self.source
        if source is None or not self.opened:
            return
        async with self.source_lock:
            if self.source is None:
                return
            message = await run_in_threadpool(self._encode, source, when)
        if self._connected():
            await self.websocket.send_bytes(message)

    @staticmethod
    def _encode(source: FrameSource, when: float) -> bytes:
        frame = source.frame_at(when)
        dt = source.fixed_dt() or (1.0 / 60.0)
        return encode_frame(frame, fps=1.0 / dt if dt > 0 else 60.0)

    async def send_resources(self) -> None:
        payload = self._resources
        if payload is None:
            await self.open()
        else:
            await self.send_json(payload)

    async def send_state(self, playing: bool | None = None) -> None:
        await self.send_json({
            "type": "state",
            "playing": bool(self.play_task is not None) if playing is None else playing,
            # `time` and `duration` are effect seconds - the timeline, the
            # keyframes and the phases all live there.  `wall_duration` is how
            # long the effect takes to play at its own `time_scale`.
            "time": self.time, "fps": self.fps, "loop": self.loop_playback, "duration": self.duration,
            "speed": self.speed,
            "time_scale": self.time_scale,
            "wall_duration": self.wall_duration(),
        })

    async def send_error(self, code: str, message: str) -> None:
        await self.send_json({"type": "error", "code": code, "message": message})

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self._connected():
            await self.websocket.send_text(json.dumps(payload, separators=(",", ":"), default=_json_default))

    def _connected(self) -> bool:
        return self.websocket.client_state == WebSocketState.CONNECTED

    # -- helpers --------------------------------------------------------

    async def _fixed_dt(self) -> float:
        source = self.source
        if source is None:
            return 1.0 / 60.0
        try:
            dt = float(await run_in_threadpool(source.fixed_dt))
        except Exception:  # noqa: BLE001
            return 1.0 / 60.0
        return dt if dt > 0 else 1.0 / 60.0

    def wall_duration(self) -> float:
        """Wall-clock seconds the effect lasts at its own speed (not the play bar's)."""
        return self.duration / self.time_scale if self.time_scale > 0.0 else self.duration

    def _clamp(self, when: float) -> float:
        if self.duration <= 0.0:
            return max(0.0, when)
        return max(0.0, min(when, self.duration))


def _float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out  # NaN -> default


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    return str(value)


# =========================================================================
# routes
# =========================================================================


def stream_routes(studio: Any) -> list[Route | WebSocketRoute]:
    """The viewer's WebSocket plus the two resource GETs, bound to ``studio``."""

    async def stream_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        connection = _Connection(studio, websocket)
        try:
            await connection.run()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - a broken socket must not take the app down
            LOGGER.exception("stream connection failed")
            try:
                await connection.send_error("internal_error", "stream failed; reload the page")
            except Exception:  # noqa: BLE001
                pass
        finally:
            await connection.aclose()
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.close()
                except Exception:  # noqa: BLE001
                    pass
            LOGGER.info("stream client disconnected")

    async def texture_endpoint(request: Request) -> Response:
        version = request.query_params.get("v") or None
        png = resource_cache(studio).texture(str(request.path_params["id"]), version)
        if png is None:
            return JSONResponse({"error": {"code": "not_found", "message": "unknown texture"}},
                                status_code=404, headers=NO_STORE)
        return Response(png, media_type="image/png", headers=IMMUTABLE if version else NO_STORE)

    async def mesh_endpoint(request: Request) -> Response:
        version = request.query_params.get("v") or None
        mesh = resource_cache(studio).mesh(str(request.path_params["id"]), version)
        if mesh is None:
            return JSONResponse({"error": {"code": "not_found", "message": "unknown mesh"}},
                                status_code=404, headers=NO_STORE)
        return JSONResponse(mesh, headers=IMMUTABLE if version else NO_STORE)

    return [
        WebSocketRoute("/ws/stream", stream_endpoint, name="stream"),
        Route("/api/stream/texture/{id}.png", texture_endpoint),
        Route("/api/stream/mesh/{id}.json", mesh_endpoint),
    ]
