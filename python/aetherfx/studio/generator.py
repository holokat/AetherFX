"""Effect generators for the studio's "Generate" box.

A generator turns a natural-language prompt into graph edits by driving the
engine's tool API through an AI agent, rendering previews so the agent can see
what it made, and iterating.  The studio only depends on this interface; the
concrete backends (Anthropic API tool-use loop, Claude Code Agent SDK) are
selected by :func:`get_generator` based on what credentials exist.

Event protocol (``on_event`` receives plain dicts, in order):
    {"kind": "status",      "text": "..."}                       progress line
    {"kind": "text",        "text": "..."}                       agent prose
    {"kind": "tool_call",   "name": "...", "args": {...}}
    {"kind": "tool_result", "name": "...", "ok": bool, "summary": "..."}
    {"kind": "image",       "path": "/abs/path.png", "caption": "..."}   a render the agent looked at
    {"kind": "error",       "text": "..."}
    {"kind": "done",        "effect_id": "fx_3" | None, "summary": "..."}
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..client import Client

EventSink = Callable[[dict[str, Any]], None]


@dataclass
class GenerationResult:
    effect_id: str | None
    summary: str
    tool_calls: int = 0
    renders: int = 0


class Generator(Protocol):
    """One backend that can generate/modify effects from a prompt."""

    name: str
    available: bool
    unavailable_reason: str | None

    def generate(
        self,
        prompt: str,
        *,
        mode: str,  # "new" (fresh effect) or "modify" (edit the active effect)
        on_event: EventSink,
        cancel: threading.Event,
    ) -> GenerationResult: ...


class NullGenerator:
    """Placeholder used when no AI backend is configured."""

    name = "none"
    available = False

    def __init__(self, reason: str) -> None:
        self.unavailable_reason = reason

    def generate(self, prompt: str, *, mode: str, on_event: EventSink, cancel: threading.Event) -> GenerationResult:
        on_event({"kind": "error", "text": self.unavailable_reason})
        on_event({"kind": "done", "effect_id": None, "summary": self.unavailable_reason})
        return GenerationResult(None, self.unavailable_reason)


def get_generator(
    client: Client,
    engine_lock: threading.Lock,
    backend: str | None = None,
    jobs_dir: "str | None" = None,
    output_dir: "str | None" = None,
) -> Generator:
    """Pick a generator backend.

    ``backend`` (or ``$AETHERFX_GENERATOR``) may be ``"api"`` (Anthropic API
    tool-use loop), ``"claude-code"`` (Claude Code Agent SDK), ``"session"``
    (job queue served by an attached interactive Claude session), ``"none"``,
    or ``None`` for auto-detection in that order. The session backend is
    returned as the last resort whenever ``jobs_dir`` is given, because its
    availability is dynamic (a worker may attach later). Backends are
    imported lazily so the studio starts even when their packages are missing.
    """
    import os  # noqa: PLC0415

    choice = (backend or os.environ.get("AETHERFX_GENERATOR") or "auto").lower()
    reasons: list[str] = []
    candidates = ["api", "claude-code", "session"] if choice == "auto" else [choice]
    session_fallback: Generator | None = None
    for name in candidates:
        if name == "none":
            break
        try:
            if name == "api":
                from .generate_api import ApiGenerator  # noqa: PLC0415

                gen: Generator = ApiGenerator(client, engine_lock)
            elif name == "claude-code":
                from .generate_claude_code import ClaudeCodeGenerator  # noqa: PLC0415

                gen = ClaudeCodeGenerator(client, engine_lock, output_dir=output_dir)
            elif name == "session":
                if not jobs_dir:
                    reasons.append("session: no jobs_dir configured")
                    continue
                from .generate_session import SessionGenerator  # noqa: PLC0415

                gen = SessionGenerator(client, engine_lock, jobs_dir)
                session_fallback = gen
            else:
                reasons.append(f"unknown generator backend '{name}'")
                continue
        except Exception as exc:  # noqa: BLE001 - report, keep the studio usable
            reasons.append(f"{name}: {exc}")
            continue
        if gen.available:
            return gen
        reasons.append(f"{name}: {gen.unavailable_reason}")
    if session_fallback is not None:
        return session_fallback
    return NullGenerator("; ".join(reasons) or "no generator backend configured")
