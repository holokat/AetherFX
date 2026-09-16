"""Session-backed generator: queues a job on disk for an external worker.

The worker is a Claude agent attached from an interactive Claude Code session
(which carries the user's credentials). Protocol (all paths under jobs_dir):

    pending/<id>.json      job request, written atomically by the studio
    running/<id>.json      the worker moves the request here when it claims it
    done/<id>.json         moved here when finished
    <id>.events.jsonl      appended by the worker, one JSON event per line
                           (see generator.py for the event kinds); the last
                           line is {"kind": "done", ...}
    <id>.cancel            created by the studio when the user cancels
    worker.heartbeat       touched by the worker at least every 2 minutes

See worker_protocol.md for the worker's side.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..client import Client
from .generator import EventSink, GenerationResult

HEARTBEAT_MAX_AGE_S = 240.0
JOB_TIMEOUT_S = 40 * 60.0


class SessionGenerator:
    name = "claude-session"

    def __init__(self, client: Client, engine_lock: threading.Lock, jobs_dir: Path | str) -> None:
        self.client = client
        self.lock = engine_lock
        self.jobs_dir = Path(jobs_dir)
        for sub in ("pending", "running", "done"):
            (self.jobs_dir / sub).mkdir(parents=True, exist_ok=True)

    # availability is dynamic: it depends on a live worker heartbeat
    def _heartbeat_age(self) -> float:
        try:
            return time.time() - (self.jobs_dir / "worker.heartbeat").stat().st_mtime
        except FileNotFoundError:
            return float("inf")

    @property
    def available(self) -> bool:
        return self._heartbeat_age() < HEARTBEAT_MAX_AGE_S

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        return "no generation worker is attached (an interactive Claude Code session runs it)"

    def _active_effect(self) -> dict[str, Any] | None:
        try:
            with self.lock:
                listing = self.client.list_effects()
        except Exception:  # noqa: BLE001
            return None
        active = listing.get("active") if isinstance(listing, dict) else None
        if not active:
            return None
        for fx in listing.get("effects", []):
            if fx.get("effect_id") == active or fx.get("active"):
                return {"effect_id": fx.get("effect_id"), "name": fx.get("name")}
        return {"effect_id": active, "name": None}

    def generate(self, prompt: str, *, mode: str, on_event: EventSink, cancel: threading.Event) -> GenerationResult:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        job = {
            "id": job_id,
            "prompt": prompt,
            "mode": mode,
            "active_effect": self._active_effect(),
            "created": time.time(),
        }
        events_path = self.jobs_dir / f"{job_id}.events.jsonl"
        events_path.touch()
        tmp = self.jobs_dir / "pending" / f"{job_id}.json.tmp"
        tmp.write_text(json.dumps(job, indent=2))
        tmp.rename(self.jobs_dir / "pending" / f"{job_id}.json")
        on_event({"kind": "status", "text": f"job {job_id} queued for the attached Claude session"})

        started = last_activity = time.time()
        with events_path.open("r", encoding="utf-8") as stream:
            while True:
                line = stream.readline()
                if line:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    last_activity = time.time()
                    on_event(event)
                    if event.get("kind") == "done":
                        return GenerationResult(
                            event.get("effect_id"), event.get("summary", ""),
                            int(event.get("tool_calls", 0)), int(event.get("renders", 0)),
                        )
                    continue
                if cancel.is_set():
                    (self.jobs_dir / f"{job_id}.cancel").touch()
                    on_event({"kind": "error", "text": "cancelled"})
                    on_event({"kind": "done", "effect_id": None, "summary": "cancelled"})
                    return GenerationResult(None, "cancelled")
                now = time.time()
                stalled = (not self.available) and (now - last_activity > HEARTBEAT_MAX_AGE_S)
                if stalled or now - started > JOB_TIMEOUT_S:
                    text = "the generation worker stopped responding" if stalled else "generation timed out"
                    on_event({"kind": "error", "text": text})
                    on_event({"kind": "done", "effect_id": None, "summary": text})
                    return GenerationResult(None, text)
                time.sleep(0.3)
