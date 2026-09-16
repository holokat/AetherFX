"""Command-line helper for a studio generation worker (see worker_protocol.md).

An interactive Claude agent uses these subcommands so it never has to hand-roll
the job-file and event-file plumbing:

    python -m aetherfx.studio.worker_cli wait  --jobs DIR [--timeout S]   block until a job is claimed; prints the job JSON
    python -m aetherfx.studio.worker_cli tool  --studio URL NAME ['{json args}']   call an engine tool through the studio
    python -m aetherfx.studio.worker_cli event --jobs DIR JOB_ID '{json event}'    append one event
    python -m aetherfx.studio.worker_cli done  --jobs DIR JOB_ID '{json done event}'  final event + move job to done/
    python -m aetherfx.studio.worker_cli cancelled --jobs DIR JOB_ID                 exit code 0 if a cancel was requested
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HEARTBEAT_EVERY_S = 30.0


def _heartbeat(jobs: Path) -> None:
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "worker.heartbeat").touch()


def cmd_wait(args: argparse.Namespace) -> int:
    jobs = Path(args.jobs)
    for sub in ("pending", "running", "done"):
        (jobs / sub).mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.timeout
    last_beat = 0.0
    while True:
        now = time.time()
        if now - last_beat > HEARTBEAT_EVERY_S:
            _heartbeat(jobs)
            last_beat = now
        pending = sorted((jobs / "pending").glob("*.json"))
        if pending:
            job_file = pending[0]
            running = jobs / "running" / job_file.name
            try:
                job_file.rename(running)
            except OSError:
                time.sleep(0.2)
                continue
            job = json.loads(running.read_text())
            job["job_file"] = str(running)
            job["events_file"] = str(jobs / f"{job['id']}.events.jsonl")
            print(json.dumps(job, indent=2))
            return 0
        if now >= deadline:
            print(json.dumps({"timeout": True}))
            return 3
        time.sleep(0.5)


def cmd_event(args: argparse.Namespace) -> int:
    jobs = Path(args.jobs)
    event = json.loads(args.event)
    _heartbeat(jobs)
    with (jobs / f"{args.job_id}.events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    jobs = Path(args.jobs)
    event = json.loads(args.event)
    event["kind"] = "done"
    with (jobs / f"{args.job_id}.events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    running = jobs / "running" / f"{args.job_id}.json"
    if running.exists():
        running.rename(jobs / "done" / running.name)
    _heartbeat(jobs)
    return 0


def cmd_cancelled(args: argparse.Namespace) -> int:
    return 0 if (Path(args.jobs) / f"{args.job_id}.cancel").exists() else 1


def _append_event(jobs: Path, job_id: str, event: dict[str, Any]) -> None:
    with (jobs / f"{job_id}.events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def cmd_tool(args: argparse.Namespace) -> int:
    """Call an engine tool through the studio. With --jobs/--job the call is
    logged to the job's event file (tool_call, tool_result and image events)."""
    from .authoring_guide import render_image_paths, summarize_result  # noqa: PLC0415

    tool_args = json.loads(args.args) if args.args else {}
    jobs = Path(args.jobs) if args.jobs else None
    if jobs and args.job:
        _heartbeat(jobs)
        _append_event(jobs, args.job, {"kind": "tool_call", "name": args.name, "args": tool_args})
    payload: dict[str, Any] = {"name": args.name, "args": tool_args}
    req = urllib.request.Request(
        args.studio.rstrip("/") + "/api/tool",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    code, body = 0, ""
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body, code = exc.read().decode("utf-8", errors="replace"), 1
    except urllib.error.URLError as exc:
        body, code = json.dumps({"error": {"code": "unreachable", "message": str(exc)}}), 2
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        result = {"raw": body}
    if jobs and args.job:
        if code == 0:
            for path, caption in render_image_paths(args.name, result):
                _append_event(jobs, args.job, {"kind": "image", "path": path, "caption": caption})
            _append_event(jobs, args.job, {"kind": "tool_result", "name": args.name, "ok": True,
                                           "summary": summarize_result(args.name, result)})
        else:
            err = result.get("error", result) if isinstance(result, dict) else result
            _append_event(jobs, args.job, {"kind": "tool_result", "name": args.name, "ok": False,
                                           "summary": json.dumps(err)[:300]})
    if args.compact and isinstance(result, dict):
        # keep huge payloads readable for an agent: trim effect dumps and frame lists
        if "effect" in result and isinstance(result["effect"], dict) and len(body) > 4000:
            result["effect"] = {"name": result["effect"].get("name"), "nodes": len(result["effect"].get("nodes", [])), "(trimmed)": True}
        if isinstance(result.get("frames"), list) and len(result["frames"]) > 4:
            result["frames"] = result["frames"][:2] + [f"... {len(result['frames'])} frames"]
        body = json.dumps(result, indent=1)
    print(body)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aetherfx-worker")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("wait"); p.add_argument("--jobs", required=True); p.add_argument("--timeout", type=float, default=540.0)
    p.set_defaults(func=cmd_wait)
    p = sub.add_parser("event"); p.add_argument("--jobs", required=True); p.add_argument("job_id"); p.add_argument("event")
    p.set_defaults(func=cmd_event)
    p = sub.add_parser("done"); p.add_argument("--jobs", required=True); p.add_argument("job_id"); p.add_argument("event")
    p.set_defaults(func=cmd_done)
    p = sub.add_parser("cancelled"); p.add_argument("--jobs", required=True); p.add_argument("job_id")
    p.set_defaults(func=cmd_cancelled)
    p = sub.add_parser("tool"); p.add_argument("--studio", required=True); p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--jobs", default=None); p.add_argument("--job", default=None)
    p.add_argument("--compact", action="store_true", help="trim large payloads in the printed result")
    p.add_argument("name"); p.add_argument("args", nargs="?", default="")
    p.set_defaults(func=cmd_tool)
    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
