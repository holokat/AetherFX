"""Background generation jobs for AetherFX Studio.

One job runs at a time: a generator drives the *same* engine session as the UI
(docs/AGENT_API.md tools mutate the active effect), so two concurrent jobs would
interleave graph edits.  :class:`JobManager` enforces that, hands out ids and
keeps a short history so the browser can still read the log of a finished job.

A :class:`Job` owns three things the HTTP layer needs: an append-only event list
(the protocol in :mod:`aetherfx.studio.generator`), a cancellation
:class:`threading.Event` the generator polls, and the thread running it.  Every
mutation is guarded by a per-job lock because the generator thread appends while
the request threads read.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

__all__ = ["RUNNING", "DONE", "ERROR", "CANCELLED", "JobBusy", "Job", "JobManager"]

JsonDict = dict[str, Any]

#: Job statuses, as reported by ``GET /api/jobs/{job_id}``.
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"


class JobBusy(RuntimeError):
    """Raised by :meth:`JobManager.start` while another job is still running."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"a generation job is already running ({job_id})")
        self.job_id = job_id


class Job:
    """One generation run: its events, its status and its cancel flag."""

    def __init__(self, job_id: str, prompt: str, mode: str) -> None:
        self.job_id = job_id
        self.prompt = prompt
        self.mode = mode
        self.created = time.time()
        self.finished: float | None = None
        self.status = RUNNING
        self.effect_id: str | None = None
        self.summary: str | None = None
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self._events: list[JsonDict] = []
        self._lock = threading.Lock()

    # -- events ------------------------------------------------------------

    def add_event(self, event: JsonDict) -> int:
        """Append one generator event and return the new event count.

        Never raises: a generator must not die because its sink misbehaved.
        """
        item = dict(event) if isinstance(event, dict) else {"kind": "text", "text": str(event)}
        item.setdefault("kind", "text")
        item.setdefault("time", time.time())
        with self._lock:
            self._events.append(item)
            return len(self._events)

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.status == RUNNING

    def finish(self, status: str, effect_id: str | None = None, summary: str | None = None) -> None:
        """Mark the job finished.  The first terminal status wins."""
        with self._lock:
            if self.status != RUNNING:
                return
            self.status = status
            self.finished = time.time()
            if effect_id:
                self.effect_id = effect_id
            if summary:
                self.summary = summary

    def request_cancel(self) -> None:
        self.cancel.set()

    # -- reading -----------------------------------------------------------

    def snapshot(self, since: int = 0) -> JsonDict:
        """Return the job state plus every event from index ``since`` onwards."""
        with self._lock:
            total = len(self._events)
            start = max(0, min(since, total))
            return {
                "job_id": self.job_id,
                "status": self.status,
                "events": [dict(event) for event in self._events[start:]],
                "next": total,
                "total": total,
                "effect_id": self.effect_id,
                "summary": self.summary,
                "prompt": self.prompt,
                "mode": self.mode,
                "created": self.created,
                "finished": self.finished,
                "cancel_requested": self.cancel.is_set(),
            }


class JobManager:
    """Holds the jobs.  At most one may be ``running`` at any time."""

    def __init__(self, history: int = 20) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._history = max(1, history)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> Job | None:
        """The running job, or ``None``."""
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs[job_id]
                if job.running:
                    return job
        return None

    def start(self, prompt: str, mode: str, target: Callable[[Job], None]) -> Job:
        """Register a job and run ``target(job)`` on a daemon thread.

        Raises :class:`JobBusy` when another job is still running.
        """
        with self._lock:
            for job_id in reversed(self._order):
                if self._jobs[job_id].running:
                    raise JobBusy(job_id)
            job = Job(uuid.uuid4().hex[:12], prompt, mode)
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._prune_locked()

        def run() -> None:
            try:
                target(job)
            except BaseException as exc:  # noqa: BLE001 - a job must never kill the server
                job.add_event({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
                job.finish(ERROR, summary=str(exc))
            finally:
                job.finish(CANCELLED if job.cancel.is_set() else DONE)

        job.thread = threading.Thread(target=run, name=f"aetherfx-job-{job.job_id}", daemon=True)
        job.thread.start()
        return job

    def _prune_locked(self) -> None:
        while len(self._order) > self._history:
            oldest = self._order[0]
            if self._jobs[oldest].running:
                break
            self._order.pop(0)
            self._jobs.pop(oldest, None)
