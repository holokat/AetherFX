"""Tests for the studio generator layer (no AI credentials needed)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from aetherfx.studio.authoring_guide import (
    CURATED_TOOLS, build_authoring_guide, build_task_prompt, render_image_paths, summarize_result,
)
from aetherfx.studio.generate_session import SessionGenerator
from aetherfx.studio.generator import NullGenerator, get_generator


class FakeClient:
    """Minimal stand-in for aetherfx.Client."""

    def list_effects(self):
        return {"active": "fx_1", "effects": [{"effect_id": "fx_1", "name": "Demo", "active": True}]}

    def describe_vocabulary(self):
        return {
            "node_types": {
                "emitter": {
                    "description": "spawns particles", "default_tier": 1, "spatial": True, "time_bound": True,
                    "parameters": {
                        "rate": {"type": "float", "default": 50.0, "min": 0, "animatable": True},
                        "shape": {"type": "enum", "default": "sphere", "enum": ["point", "sphere"]},
                    },
                    "inputs": {"particle": {"accepts": ["particle_system"], "required": True, "multi": False}},
                    "outputs": ["particles"],
                }
            },
            "texture_ops": [{"op": "fbm", "params": {"frequency": {"default": 4}}, "inputs": []}],
        }

    def tools(self):
        return []

    def close(self):
        pass


def test_authoring_guide_renders_vocabulary_and_recipes():
    guide = build_authoring_guide(FakeClient().describe_vocabulary())
    assert "## emitter (tier 1, spatial, time_bound)" in guide
    assert "rate: float=50 [0..] (A)" in guide
    assert "shape: enum{point|sphere}=sphere" in guide
    assert "particle<particle_system> required" in guide
    assert "fbm(frequency=4)" in guide
    assert "RECIPES" in guide and "WORKFLOW" in guide
    assert len(CURATED_TOOLS) == 28


def test_task_prompt_modes():
    new = build_task_prompt("a blue flame", "new", None)
    assert "create_effect" in new and "a blue flame" in new
    mod = build_task_prompt("make it bigger", "modify", {"effect_id": "fx_2", "name": "Fireball"})
    assert "Fireball" in mod and "inspect_graph" in mod


def test_summaries_and_render_paths(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert render_image_paths("render_frame", {"path": str(png)}) == [(str(png), "frame")]
    assert render_image_paths("render_preview", {"contact_sheet": str(tmp_path / "missing.png")}) == []
    assert render_image_paths("set_parameter", {"path": str(png)}) == []
    text = summarize_result("create_node", {"node": {"id": "flames", "type": "emitter"},
                                            "diagnostics": {"errors": 1, "warnings": 0,
                                                            "items": [{"code": "E010", "message": "missing particle"}]}})
    assert "flames" in text and "E010" in text


def test_get_generator_none_and_session_fallback(tmp_path: Path):
    client, lock = FakeClient(), threading.Lock()
    assert isinstance(get_generator(client, lock, backend="none"), NullGenerator)
    gen = get_generator(client, lock, backend="auto", jobs_dir=tmp_path / "jobs")
    assert gen.name == "claude-session"
    assert gen.available is False
    assert "worker" in (gen.unavailable_reason or "")
    (tmp_path / "jobs" / "worker.heartbeat").touch()
    assert gen.available is True


def _fake_worker(jobs_dir: Path, delay: float = 0.2):
    """Claim the pending job, emit events like a real worker, finish."""
    deadline = time.time() + 5
    while time.time() < deadline:
        pending = list((jobs_dir / "pending").glob("*.json"))
        if pending:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("no job appeared")
    job_file = pending[0]
    job = json.loads(job_file.read_text())
    running = jobs_dir / "running" / job_file.name
    job_file.rename(running)
    events = jobs_dir / f"{job['id']}.events.jsonl"
    with events.open("a") as f:
        f.write(json.dumps({"kind": "status", "text": "worker claimed"}) + "\n")
        f.write(json.dumps({"kind": "tool_call", "name": "create_effect", "args": {"name": job["prompt"]}}) + "\n")
        f.flush()
        time.sleep(delay)
        f.write(json.dumps({"kind": "done", "effect_id": "fx_9", "summary": "built", "tool_calls": 1, "renders": 0}) + "\n")
    running.rename(jobs_dir / "done" / job_file.name)


def test_session_generator_round_trip(tmp_path: Path):
    jobs = tmp_path / "jobs"
    gen = SessionGenerator(FakeClient(), threading.Lock(), jobs)
    (jobs / "worker.heartbeat").touch()
    events: list[dict] = []
    worker = threading.Thread(target=_fake_worker, args=(jobs,), daemon=True)
    worker.start()
    result = gen.generate("tiny spark", mode="new", on_event=events.append, cancel=threading.Event())
    worker.join(timeout=5)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "status" and kinds[-1] == "done"
    assert "tool_call" in kinds
    assert result.effect_id == "fx_9" and result.summary == "built" and result.tool_calls == 1
    assert list((jobs / "done").glob("*.json"))
    job = json.loads(next((jobs / "done").glob("*.json")).read_text())
    assert job["prompt"] == "tiny spark" and job["mode"] == "new" and job["active_effect"]["effect_id"] == "fx_1"


def test_session_generator_cancel(tmp_path: Path):
    jobs = tmp_path / "jobs"
    gen = SessionGenerator(FakeClient(), threading.Lock(), jobs)
    (jobs / "worker.heartbeat").touch()
    cancel = threading.Event()
    events: list[dict] = []
    threading.Timer(0.3, cancel.set).start()
    result = gen.generate("never served", mode="new", on_event=events.append, cancel=cancel)
    assert result.summary == "cancelled"
    assert events[-1]["kind"] == "done"
    assert list(jobs.glob("*.cancel"))


@pytest.mark.parametrize("backend", ["api", "claude-code"])
def test_optional_backends_report_reasons_without_credentials(backend, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    gen = get_generator(FakeClient(), threading.Lock(), backend=backend, jobs_dir=tmp_path)
    # Either the backend is genuinely available on this machine or it explains why not.
    assert gen.available or gen.unavailable_reason
