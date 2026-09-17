"""Generator backed by the Claude Code Agent SDK (claude-agent-sdk).

Runs a Claude Code agent in-process with the engine tools exposed as an SDK
MCP server, so the agent drives the studio's own engine session and sees the
frames it renders (render tools return image content blocks). Requires a
logged-in Claude Code CLI (the SDK bundles one; `claude auth login` once).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from ..client import Client
from .authoring_guide import (
    CURATED_TOOLS, build_authoring_guide, build_task_prompt, image_png_base64, render_image_paths,
    summarize_result, tool_fields,
)
from .generator import EventSink, GenerationResult

MAX_TOOL_TEXT = 60_000


class ClaudeCodeGenerator:
    name = "claude-code"

    def __init__(self, client: Client, engine_lock: threading.Lock, model: str | None = None,
                 max_turns: int = 80, output_dir: Path | str | None = None) -> None:
        self.client = client
        self.lock = engine_lock
        self.model = model or os.environ.get("AETHERFX_CLAUDE_MODEL") or None
        self.max_turns = max_turns
        self.output_dir = Path(output_dir) if output_dir else Path.cwd()
        self._guide_cache: str | None = None
        self.available, self.unavailable_reason = self._probe()

    # ------------------------------------------------------------------ probe
    @staticmethod
    def cli_path() -> str | None:
        try:
            import claude_agent_sdk  # noqa: PLC0415
        except ImportError:
            return None
        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        if bundled.is_file():
            return str(bundled)
        return shutil.which("claude")

    def _probe(self) -> tuple[bool, str | None]:
        import importlib.util  # noqa: PLC0415

        if importlib.util.find_spec("claude_agent_sdk") is None:
            return False, "claude-agent-sdk is not installed"
        cli = self.cli_path()
        if not cli:
            return False, "no Claude Code CLI found"
        try:
            proc = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=30)
            info = json.loads(proc.stdout or "{}")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            return False, f"could not read Claude Code auth status: {exc}"
        if not info.get("loggedIn"):
            return False, "Claude Code is not logged in on this machine"
        return True, None

    # ------------------------------------------------------------------ run
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

    def generate(self, prompt: str, *, mode: str, on_event: EventSink, cancel: threading.Event,
                 attachments: list[str] | None = None) -> GenerationResult:
        import anyio  # noqa: PLC0415

        return anyio.run(self._run, prompt, mode, on_event, cancel, list(attachments or []))

    def _make_tool(self, spec: dict[str, Any], on_event: EventSink, counters: dict[str, int]) -> Any:
        import anyio  # noqa: PLC0415
        from claude_agent_sdk import tool  # noqa: PLC0415

        name, description, schema = tool_fields(spec)

        def run_sync(args: dict[str, Any]) -> Any:
            with self.lock:
                return self.client.call(name, **args)

        @tool(name, description, schema)
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            counters["calls"] += 1
            on_event({"kind": "tool_call", "name": name, "args": args})
            try:
                result = await anyio.to_thread.run_sync(run_sync, dict(args))
            except Exception as exc:  # noqa: BLE001 - surfaced to the agent as a tool error
                on_event({"kind": "tool_result", "name": name, "ok": False, "summary": str(exc)[:300]})
                return {"content": [{"type": "text", "text": json.dumps({"error": str(exc)})}], "is_error": True}
            text = json.dumps(result, separators=(",", ":"))
            if len(text) > MAX_TOOL_TEXT:
                text = text[:MAX_TOOL_TEXT] + "...(truncated)"
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            for path, caption in render_image_paths(name, result):
                encoded = image_png_base64(path)
                if encoded:
                    content.append({"type": "image", "data": encoded[0], "mimeType": "image/png"})
                    counters["renders"] += 1
                    on_event({"kind": "image", "path": path, "caption": caption})
            on_event({"kind": "tool_result", "name": name, "ok": True, "summary": summarize_result(name, result)})
            return {"content": content}

        return handler

    async def _run(self, prompt: str, mode: str, on_event: EventSink, cancel: threading.Event,
                   attachments: list[str]) -> GenerationResult:
        from claude_agent_sdk import (  # noqa: PLC0415
            AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock, create_sdk_mcp_server,
        )

        counters = {"calls": 0, "renders": 0}
        with self.lock:
            specs = self.client.tools()
        sdk_tools = [self._make_tool(s, on_event, counters) for s in specs if tool_fields(s)[0] in CURATED_TOOLS]
        server = create_sdk_mcp_server("aetherfx", "0.1.0", sdk_tools)
        options = ClaudeAgentOptions(
            system_prompt=self._guide(),
            mcp_servers={"aetherfx": server},
            allowed_tools=[f"mcp__aetherfx__{n}" for n in CURATED_TOOLS] + (["Read"] if attachments else []),
            disallowed_tools=["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch",
                              "Task", "Agent", "Glob", "Grep"] + ([] if attachments else ["Read"]),
            permission_mode="default",
            max_turns=self.max_turns,
            setting_sources=[],
            cwd=str(self.output_dir),
            model=self.model,
            cli_path=self.cli_path(),
        )
        task = build_task_prompt(prompt, mode, self._active_effect(), attachments)
        if attachments:
            task += "\nView each reference image with the Read tool first."
        on_event({"kind": "status", "text": f"Claude Code agent started ({self.model or 'default model'})"})
        texts: list[str] = []
        error: str | None = None
        async with ClaudeSDKClient(options=options) as sdk:
            await sdk.query(task)
            async for message in sdk.receive_response():
                if cancel.is_set():
                    await sdk.interrupt()
                    error = "cancelled"
                    break
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            texts.append(block.text.strip())
                            on_event({"kind": "text", "text": block.text.strip()})
                elif isinstance(message, ResultMessage):
                    if message.is_error:
                        error = message.result or "the agent reported an error"
        effect_id = (self._active_effect() or {}).get("effect_id")
        summary = error or (texts[-1] if texts else "done")
        if error:
            on_event({"kind": "error", "text": error})
        on_event({"kind": "done", "effect_id": effect_id, "summary": summary,
                  "tool_calls": counters["calls"], "renders": counters["renders"]})
        return GenerationResult(effect_id, summary, counters["calls"], counters["renders"])
