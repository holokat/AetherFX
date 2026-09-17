#!/usr/bin/env python3
"""Screenshot an effect in the AetherFX Studio GPU viewer with headless Chrome.

Drives a private headless Chrome over the DevTools protocol: opens a running
studio, clicks the Library row whose name matches ``--effect``, pauses, seeks
to each requested time and captures the viewport.

    python tools/gl_capture.py --url http://127.0.0.1:8781/ --effect "Fire AOE" \
        --times 0.5,1.2,2.4 --out out/work/fire/shots/fire --size 1280x800

Writes ``<out>_t0.50.png`` etc.  Needs the ``websockets`` package (the studio
venv has it) and Google Chrome.  Each parallel user should pass a distinct
``--cdp-port`` and ``--profile`` directory.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

CHROME = os.environ.get("AETHERFX_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

SELECT_SCRIPT = """
(async () => {
  const wanted = %(effect)s;
  const viewer = window.aetherViewer;
  if (!viewer || !viewer.available) return 'NO_GPU:' + (viewer ? (viewer.reason || 'unavailable') : 'viewer module not loaded (check /static/vendor/three/build/three.module.js)');
  const rows = [...document.querySelectorAll('#list-library li')];
  const row = rows.find(li => li.textContent.trim().toLowerCase().startsWith(wanted.toLowerCase()));
  if (!row) return 'NO_ROW:' + rows.map(li => li.textContent.trim()).join('|');
  // Always click: loading a row re-reads the JSON from disk, otherwise the
  // studio keeps serving its in-memory working copy of an earlier version.
  row.click();
  await new Promise(r => setTimeout(r, %(load_ms)d));
  if (typeof S !== 'undefined' && S.playing) document.getElementById('btn-play').click();
  await new Promise(r => setTimeout(r, 400));
  return 'OK ' + document.getElementById('time-readout').textContent;
})()
"""

SEEK_SCRIPT = """
(async () => {
  const s = document.getElementById('frame-slider');
  const max = parseInt(s.max, 10);
  const readout = document.getElementById('time-readout').textContent;
  const m = readout.match(/\\/\\s*([0-9.]+)\\s*s/);
  const duration = m ? parseFloat(m[1]) : %(fallback_duration)f;
  const frac = Math.max(0, Math.min(1, %(time)f / duration));
  s.value = String(Math.round(max * frac));
  s.dispatchEvent(new Event('input', {bubbles: true}));
  await new Promise(r => setTimeout(r, %(settle_ms)d));
  return document.getElementById('time-readout').textContent + ' | ' +
         (document.getElementById('gl-stats') || {textContent: ''}).textContent;
})()
"""


async def run(args: argparse.Namespace) -> int:
    import websockets  # type: ignore

    width, height = (int(v) for v in args.size.lower().split("x"))
    times = [float(t) for t in args.times.split(",") if t.strip()]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={args.cdp_port}", "--use-angle=metal",
         "--hide-scrollbars", "--no-first-run", "--disable-features=Translate",
         f"--user-data-dir={args.profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(80):
            try:
                pages = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{args.cdp_port}/json/list"))
                         if t["type"] == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ws_url:
            print("chrome did not expose a page", file=sys.stderr)
            return 2
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            ident = 0

            async def send(method: str, params: dict | None = None) -> dict:
                nonlocal ident
                ident += 1
                await ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == ident:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result", {})

            async def evaluate(expr: str) -> str:
                result = await send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
                return str(result.get("result", {}).get("value"))

            await send("Emulation.setDeviceMetricsOverride",
                       {"width": width, "height": height, "deviceScaleFactor": args.scale, "mobile": False})
            await send("Page.enable")
            await send("Page.navigate", {"url": args.url})
            await asyncio.sleep(args.page_wait)
            status = await evaluate(SELECT_SCRIPT % {"effect": json.dumps(args.effect), "load_ms": int(args.load_wait * 1000)})
            print("select:", status)
            if status.startswith("NO_ROW"):
                return 3
            if status.startswith("NO_GPU"):
                print("GPU viewer is not active in this page; refusing to capture the CPU fallback:", status, file=sys.stderr)
                return 4
            if args.camera:
                cam = json.loads(args.camera)
                expr = """(() => { const a = window.aetherViewer; const v = a && a.gl; if (!v || !v.camera) return 'no viewer';
                  const p = %s, t = %s; if (p) v.camera.position.set(p[0], p[1], p[2]);
                  if (t && v.controls) v.controls.target.set(t[0], t[1], t[2]);
                  if (v.controls) v.controls.update(); v.camera.updateProjectionMatrix(); return 'camera set'; })()""" % (
                    json.dumps(cam.get("position")), json.dumps(cam.get("target")))
                print("camera:", await evaluate(expr))
            for t in times:
                info = await evaluate(SEEK_SCRIPT % {"time": t, "settle_ms": int(args.settle * 1000), "fallback_duration": args.duration})
                shot = await send("Page.captureScreenshot", {"format": "png"})
                path = f"{args.out}_t{t:.2f}.png"
                with open(path, "wb") as handle:
                    handle.write(base64.b64decode(shot["data"]))
                print(f"{path}: {info}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8770/")
    parser.add_argument("--effect", required=True, help="Library display name prefix, e.g. 'Fire AOE'")
    parser.add_argument("--times", default="1.0", help="comma-separated effect times in seconds")
    parser.add_argument("--out", required=True, help="output path prefix; _t<time>.png is appended")
    parser.add_argument("--size", default="1280x800")
    parser.add_argument("--scale", type=float, default=1.0, help="device scale factor (2 for retina-like detail)")
    parser.add_argument("--cdp-port", type=int, default=9333)
    parser.add_argument("--profile", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "aetherfx-chrome-profile"))
    parser.add_argument("--page-wait", type=float, default=10.0, help="seconds to wait after navigation")
    parser.add_argument("--load-wait", type=float, default=8.0, help="seconds to wait after clicking the library row")
    parser.add_argument("--settle", type=float, default=2.0, help="seconds to wait after seeking before capture")
    parser.add_argument("--duration", type=float, default=3.0, help="fallback effect duration if the readout is unparsable")
    parser.add_argument("--camera", default=None, help='optional JSON {"position":[x,y,z],"target":[x,y,z]} applied to the three.js camera before capture')
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
