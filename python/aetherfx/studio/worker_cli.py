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


def cmd_tool(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"name": args.name, "args": json.loads(args.args) if args.args else {}}
    req = urllib.request.Request(
        args.studio.rstrip("/") + "/api/tool",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(body)
        return 1
    except urllib.error.URLError as exc:
        print(json.dumps({"error": {"code": "unreachable", "message": str(exc)}}))
        return 2
    print(body)
    return 0


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
    p.add_argument("name"); p.add_argument("args", nargs="?", default="")
    p.set_defaults(func=cmd_tool)
    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
