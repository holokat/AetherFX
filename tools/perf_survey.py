#!/usr/bin/env python3
"""Measure what every library effect costs, and rank them.

For each effect document the survey records

* engine side (``aetherfx.community`` measurements): peak live sprites, peak live mesh particles,
  seconds of procedural texture baking and bytes of baked texture (the load time of an effect), and
* GPU side (``tools/gl_capture.py --perf`` in a private studio): the GPU-synchronised cost of a frame
  in milliseconds (average, p95, worst), draw calls, triangles, and what the live stream carries.

    python tools/perf_survey.py --url http://127.0.0.1:8809/ --out out/perf/baseline

writes ``<out>.json`` and ``<out>.md``.  Pass ``--against out/perf/baseline.json`` to add a
before/after comparison.  Never point it at port 8770 (the user's studio): the capture tool refuses.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

GPU_KEYS = ("frame_ms_avg", "frame_ms_p95", "frame_ms_max", "overdraw_avg", "overdraw_peak", "particles_peak", "peak_draw_calls",
            "peak_triangles", "stream_kb_per_frame", "stream_fps", "render_fps", "long_tasks")
ENGINE_KEYS = ("peak_live_sprites", "peak_live_mesh_particles", "texture_bake_seconds", "texture_bytes",
               "texture_count", "node_count")


def effect_files(extra: list[str]) -> list[Path]:
    files = sorted((REPO / "examples" / "effects").glob("*.json")) + sorted((REPO / "community" / "effects").glob("*.json"))
    files += [Path(p) for p in extra]
    out = []
    for path in files:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        library = (doc.get("metadata") or {}).get("library") or {}
        if library.get("hidden"):
            continue
        out.append(path)
    return out


def engine_metrics(path: Path) -> dict:
    os.environ.setdefault("AETHERFX_BINARY", str(REPO / "build" / "bin" / "aetherfx"))
    os.environ.setdefault("AETHERFX_LIBRARY", str(REPO / "build" / "src" / "capi" / "libaetherfx.dylib"))
    from aetherfx import community  # noqa: WPS433 (needs the environment above)

    document = json.loads(path.read_text())
    _findings, metrics = community.engine_findings(path, document, community.Budgets())
    return {key: metrics.get(key) for key in ENGINE_KEYS} | {"duration": metrics.get("duration")}


def gpu_metrics(args: argparse.Namespace, name: str, seconds: float) -> dict:
    command = [sys.executable, str(REPO / "tools" / "gl_capture.py"), "--url", args.url, "--effect", name,
               "--times", "", "--out", str(Path(args.out).with_suffix("")) + "_probe", "--size", args.size,
               "--scale", str(args.scale),
               "--perf", f"{seconds:.2f}", "--cdp-port", str(args.cdp_port), "--profile", args.profile]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240)
    match = re.search(r"^perf: (\{.*\})\s*$", done.stdout, re.M)
    if not match:
        return {"error": (done.stdout + done.stderr).strip()[-300:]}
    return json.loads(match.group(1))


def table(rows: list[dict], against: dict[str, dict]) -> str:
    def delta(row: dict, key: str) -> str:
        value = row.get(key)
        if value is None:
            return "-"
        text = f"{value:g}" if isinstance(value, float) else str(value)
        before = (against.get(row["name"]) or {}).get(key)
        if isinstance(before, (int, float)) and before and isinstance(value, (int, float)) and before != value:
            text += f" ({(value - before) / before * 100:+.0f}%)"
        return text

    head = ["effect", "frame ms avg", "frame ms p95", "overdraw avg", "overdraw peak", "sprites peak", "mesh particles", "draws peak",
            "triangles peak", "stream KB/frame", "bake s", "texture MB"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for row in sorted(rows, key=lambda r: -(r.get("frame_ms_p95") or 0)):
        row = dict(row)
        row["texture_mb"] = round((row.get("texture_bytes") or 0) / 1048576, 1)
        lines.append("| " + " | ".join([row["name"], delta(row, "frame_ms_avg"), delta(row, "frame_ms_p95"),
                                        delta(row, "overdraw_avg"), delta(row, "overdraw_peak"),
                                        delta(row, "peak_live_sprites"), delta(row, "peak_live_mesh_particles"),
                                        delta(row, "peak_draw_calls"), delta(row, "peak_triangles"),
                                        delta(row, "stream_kb_per_frame"), delta(row, "texture_bake_seconds"),
                                        delta(row, "texture_mb")]) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="YOUR OWN studio, e.g. http://127.0.0.1:8809/")
    parser.add_argument("--out", required=True, help="output prefix; .json and .md are written")
    parser.add_argument("--effects", default="", help="comma-separated display names; default is every library effect")
    parser.add_argument("--extra", action="append", default=[], help="additional effect file to include")
    parser.add_argument("--against", default="", help="an earlier survey .json to compare with")
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--scale", type=float, default=2.0,
                        help="device pixel ratio; 2 matches a Retina display, where the viewer is fill-rate bound")
    parser.add_argument("--cdp-port", type=int, default=9341)
    parser.add_argument("--profile", default="/tmp/aetherfx-perf-survey-profile")
    parser.add_argument("--no-gpu", action="store_true", help="engine-side metrics only")
    args = parser.parse_args()

    wanted = {name.strip().lower() for name in args.effects.split(",") if name.strip()}
    against = {}
    if args.against:
        against = {row["name"]: row for row in json.loads(Path(args.against).read_text())["effects"]}

    rows = []
    for path in effect_files(args.extra):
        document = json.loads(path.read_text())
        name = str(document.get("name") or path.stem)
        if wanted and name.lower() not in wanted:
            continue
        started = time.time()
        row = {"name": name, "file": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)}
        try:
            row.update(engine_metrics(path))
        except Exception as error:  # noqa: BLE001 - one broken effect must not stop the survey
            row["engine_error"] = str(error)[:300]
        if not args.no_gpu:
            scale = float(document.get("time_scale") or 1.0) or 1.0
            wall = float(document.get("duration") or 3.0) / scale
            row.update(gpu_metrics(args, name, min(6.0, max(3.0, wall))))
        rows.append(row)
        print(f"{name:24s} frame {row.get('frame_ms_avg', '-')}/{row.get('frame_ms_p95', '-')} ms  overdraw "
              f"{row.get('overdraw_avg', '-')}/{row.get('overdraw_peak', '-')}  sprites "
              f"{row.get('peak_live_sprites', '-')}  meshes {row.get('peak_live_mesh_particles', '-')}  "
              f"draws {row.get('peak_draw_calls', '-')}  tris {row.get('peak_triangles', '-')}  "
              f"bake {row.get('texture_bake_seconds', '-')} s  ({time.time() - started:.0f} s)", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"size": args.size, "scale": args.scale, "effects": rows}, indent=1) + "\n")
    out.with_suffix(".md").write_text(table(rows, against))
    print(f"wrote {out.with_suffix('.json')} and {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
