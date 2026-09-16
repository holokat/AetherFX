"""Generator backed by the Anthropic API (tool-use loop with the `anthropic` SDK).

Used when an API credential exists (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or
an `ant auth login` profile). Render tools return the frame as an image block
inside the tool result so the model can see what it made. Uses claude-opus-5
with adaptive thinking; server-side refusal fallbacks are enabled by default.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..client import Client
from .authoring_guide import (
    CURATED_TOOLS, build_authoring_guide, build_task_prompt, image_png_base64, render_image_paths,
    summarize_result, tool_fields,
)
from .generator import EventSink, GenerationResult

DEFAULT_MODEL = "claude-opus-5"
MAX_TOOL_TEXT = 60_000


class ApiGenerator:
    name = "api"

    def __init__(self, client: Client, engine_lock: threading.Lock, model: str | None = None,
                 max_iterations: int = 80) -> None:
        self.client = client
        self.lock = engine_lock
        self.model = model or os.environ.get("AETHERFX_API_MODEL") or DEFAULT_MODEL
        self.max_iterations = max_iterations
        self._guide_cache: str | None = None
        self.available, self.unavailable_reason = self._probe()

    @staticmethod
    def _probe() -> tuple[bool, str | None]:
        import importlib.util  # noqa: PLC0415

        if importlib.util.find_spec("anthropic") is None:
            return False, "the anthropic package is not installed"
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True, None
        if (Path.home() / ".config" / "anthropic").exists():
            return True, None  # `ant auth login` profile
        return False, "no Anthropic API credential (ANTHROPIC_API_KEY or `ant auth login`)"

    def _guide(self) -> str:
        if self._guide_cache is None:
            with self.lock:
                vocab = self.client.describe_vocabulary()
            self._guide_cache = build_authoring_guide(vocab)
        return self._guide_cache

    def _active_effect(self) -> dict[str, Any] | None:
        try:
            with self.lock:
                listing = self.client.list_effects()
        except Exception:  # noqa: BLE001
            return None
        for fx in listing.get("effects", []):
            if fx.get("active"):
                return {"effect_id": fx.get("effect_id"), "name": fx.get("name")}
        return None

    def _tool_definitions(self) -> list[dict[str, Any]]:
        with self.lock:
            specs = self.client.tools()
        defs = []
        for s in specs:
            name, description, schema = tool_fields(s)
            if name in CURATED_TOOLS:
                defs.append({"name": name, "description": description, "input_schema": schema})
        return defs

    def _execute(self, name: str, args: dict[str, Any], on_event: EventSink, counters: dict[str, int]) -> dict[str, Any]:
        counters["calls"] += 1
        on_event({"kind": "tool_call", "name": name, "args": args})
        try:
            with self.lock:
                result = self.client.call(name, **args)
        except Exception as exc:  # noqa: BLE001
            on_event({"kind": "tool_result", "name": name, "ok": False, "summary": str(exc)[:300]})
            return {"content": [{"type": "text", "text": json.dumps({"error": str(exc)})}], "is_error": True}
        text = json.dumps(result, separators=(",", ":"))
        if len(text) > MAX_TOOL_TEXT:
            text = text[:MAX_TOOL_TEXT] + "...(truncated)"
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for path, caption in render_image_paths(name, result):
            encoded = image_png_base64(path)
            if encoded:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded[0]}})
                counters["renders"] += 1
                on_event({"kind": "image", "path": path, "caption": caption})
        on_event({"kind": "tool_result", "name": name, "ok": True, "summary": summarize_result(name, result)})
        return {"content": content}

    def generate(self, prompt: str, *, mode: str, on_event: EventSink, cancel: threading.Event) -> GenerationResult:
        import anthropic  # noqa: PLC0415

        api = anthropic.Anthropic()
        tools = self._tool_definitions()
        system = [{"type": "text", "text": self._guide(), "cache_control": {"type": "ephemeral"}}]
        messages: list[dict[str, Any]] = [{"role": "user", "content": build_task_prompt(prompt, mode, self._active_effect())}]
        counters = {"calls": 0, "renders": 0}
        texts: list[str] = []
        error: str | None = None
        on_event({"kind": "status", "text": f"Anthropic API agent started ({self.model})"})
        for _ in range(self.max_iterations):
            if cancel.is_set():
                error = "cancelled"
                break
            try:
                response = api.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    thinking={"type": "adaptive"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    system=system,
                    tools=tools,
                    messages=messages,
                )
            except anthropic.APIError as exc:
                error = f"API error: {exc}"
                break
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    texts.append(block.text.strip())
                    on_event({"kind": "text", "text": block.text.strip()})
            if response.stop_reason == "refusal":
                error = "the model declined the request"
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                break
            results = []
            for use in tool_uses:
                outcome = self._execute(use.name, dict(use.input or {}), on_event, counters)
                results.append({"type": "tool_result", "tool_use_id": use.id,
                                "content": outcome["content"], "is_error": outcome.get("is_error", False)})
            messages.append({"role": "user", "content": results})
        else:
            error = "iteration limit reached"
        effect_id = (self._active_effect() or {}).get("effect_id")
        summary = error or (texts[-1] if texts else "done")
        if error:
            on_event({"kind": "error", "text": error})
        on_event({"kind": "done", "effect_id": effect_id, "summary": summary,
                  "tool_calls": counters["calls"], "renders": counters["renders"]})
        return GenerationResult(effect_id, summary, counters["calls"], counters["renders"])
