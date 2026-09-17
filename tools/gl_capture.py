#!/usr/bin/env python3
"""Screenshot an effect in the AetherFX Studio GPU viewer with headless Chrome.

Drives a private headless Chrome over the DevTools protocol: opens a running
studio, clicks the Library row whose name matches ``--effect``, pauses, seeks
to each requested time and captures the viewport.

    python tools/gl_capture.py --url http://127.0.0.1:8781/ --effect "Fire AOE" \
        --times 0.5,1.2,2.4 --out out/work/fire/shots/fire --size 1280x800

``--controls '{"primary_intensity": 2.0}'`` moves the effect's controls through
the studio API before the capture, which is how paired before/after shots of a
slider are taken at the same time stamp.

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
  // The Library is split into collapsible sections (Core / Mine) and the row label may
  // carry <mark> highlights from the live search: open every section, clear the filter,
  // and match on the label span's text.
  const studioTab = document.getElementById('nav-studio');
  if (studioTab) studioTab.click();
  const search = document.getElementById('lib-search');
  if (search && search.value) { search.value = ''; search.dispatchEvent(new Event('input', {bubbles: true})); }
  document.querySelectorAll('#library-sections details').forEach(d => { d.open = true; });
  await new Promise(r => setTimeout(r, 120));
  const rows = [...document.querySelectorAll('#library-sections li, #list-library li')];
  const label = li => ((li.querySelector('.row-label') || li).textContent || '').trim();
  const row = rows.find(li => label(li).toLowerCase().startsWith(wanted.toLowerCase()));
  if (!row) {
    // Contributed effects live in the Community view, not the Library: open the card there.
    const communityTab = document.getElementById('nav-community');
    if (communityTab) {
      communityTab.click();
      await new Promise(r => setTimeout(r, 1200));
      const comSearch = document.getElementById('com-search');
      if (comSearch && comSearch.value) { comSearch.value = ''; comSearch.dispatchEvent(new Event('input', {bubbles: true})); }
      await new Promise(r => setTimeout(r, 200));
      const cards = [...document.querySelectorAll('.community-card')];
      const card = cards.find(c => ((c.querySelector('.card-title') || {}).textContent || '')
        .trim().toLowerCase().startsWith(wanted.toLowerCase()));
      if (card) {
        card.querySelector('button').click();
        await new Promise(r => setTimeout(r, %(load_ms)d));
        if (typeof S !== 'undefined' && S.playing) document.getElementById('btn-play').click();
        await new Promise(r => setTimeout(r, 400));
        return 'OK ' + document.getElementById('time-readout').textContent;
      }
      if (studioTab) studioTab.click();
      return 'NO_ROW:' + rows.map(label).concat(cards.map(c => c.textContent.trim().split('\\n')[0])).join('|');
    }
    return 'NO_ROW:' + rows.map(label).join('|');
  }
  // Always click: loading a row re-reads the JSON from disk, otherwise the
  // studio keeps serving its in-memory working copy of an earlier version.
  row.click();
  await new Promise(r => setTimeout(r, %(load_ms)d));
  if (typeof S !== 'undefined' && S.playing) document.getElementById('btn-play').click();
  await new Promise(r => setTimeout(r, 400));
  return 'OK ' + document.getElementById('time-readout').textContent;
})()
"""

# Moves the effect's controls (docs/CONTROLS.md) through the studio API from
# inside the page, so the Style panel, the GPU stream and the capture all agree.
CONTROLS_SCRIPT = """
(async () => {
  const wanted = %(controls)s;
  const ids = Object.keys(wanted);
  for (const id of ids) {
    const res = await fetch('/api/controls/' + encodeURIComponent(id),
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value: wanted[id]})});
    if (!res.ok) return 'FAILED:' + id + ':' + res.status + ':' + (await res.text());
  }
  if (typeof refreshControls === 'function') await refreshControls();
  if (typeof invalidatePreview === 'function') invalidatePreview();
  await new Promise(r => setTimeout(r, %(wait_ms)d));
  return 'OK ' + ids.map(id => id + '=' + wanted[id]).join(' ');
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


PERF_SCRIPT = """
(async () => {
  const v = window.aetherViewer.gl;
  if (typeof S !== 'undefined' && !S.playing) document.getElementById('btn-play').click();
  await new Promise(r => setTimeout(r, 600));
  let raf = 0, frames = 0, bytes = 0, long = 0, longMax = 0, worstGap = 0, last = performance.now();
  const po = new PerformanceObserver(l => { for (const e of l.getEntries()) { long++; longMax = Math.max(longMax, e.duration); } });
  try { po.observe({ entryTypes: ['longtask'] }); } catch (err) {}
  const orig = v.client.onFrame;
  v.client.onFrame = function () { frames++; return orig.apply(this, arguments); };
  let calls = 0, tris = 0, samples = 0;
  const t0 = performance.now();
  await new Promise(res => { const tick = () => {
      const now = performance.now(); worstGap = Math.max(worstGap, now - last); last = now; raf++;
      calls += v.renderer.info.render.calls; tris += v.renderer.info.render.triangles; samples++;
      if (now - t0 < %(ms)d) requestAnimationFrame(tick); else res(); };
    requestAnimationFrame(tick); });
  v.client.onFrame = orig; po.disconnect();
  const secs = (performance.now() - t0) / 1000;
  if (typeof S !== 'undefined' && S.playing) document.getElementById('btn-play').click();
  return JSON.stringify({ render_fps: +(raf / secs).toFixed(1), stream_fps: +(frames / secs).toFixed(1),
    worst_frame_gap_ms: Math.round(worstGap), long_tasks: long, longest_task_ms: Math.round(longMax),
    avg_draw_calls: Math.round(calls / Math.max(1, samples)), avg_triangles: Math.round(tris / Math.max(1, samples)),
    geometries: v.renderer.info.memory.geometries, textures: v.renderer.info.memory.textures,
    canvas: [v.renderer.domElement.width, v.renderer.domElement.height] });
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
            if args.controls:
                status = await evaluate(CONTROLS_SCRIPT % {"controls": args.controls,
                                                           "wait_ms": int(args.settle * 1000)})
                print("controls:", status)
                if status.startswith("FAILED"):
                    return 5
            if args.hide_hud:
                await evaluate("(() => { const st = document.createElement('style'); st.textContent = '.gl-hud, #gl-stats, #gl-mode, .transport, #transport, .tp, .viewport-hud, .gl-note { visibility: hidden !important; }'; document.head.appendChild(st); return 1; })()")
            if args.camera:
                cam = json.loads(args.camera)
                expr = """(() => { const a = window.aetherViewer; const v = a && a.gl; if (!v || !v.camera) return 'no viewer';
                  const p = %s, t = %s; if (p) v.camera.position.set(p[0], p[1], p[2]);
                  if (t && v.controls) v.controls.target.set(t[0], t[1], t[2]);
                  if (v.controls) v.controls.update(); v.camera.updateProjectionMatrix(); return 'camera set'; })()""" % (
                    json.dumps(cam.get("position")), json.dumps(cam.get("target")))
                print("camera:", await evaluate(expr))
            if args.perf > 0:
                report = await evaluate(PERF_SCRIPT % {"ms": int(args.perf * 1000)})
                print("perf:", report)
            for t in times:
                info = await evaluate(SEEK_SCRIPT % {"time": t, "settle_ms": int(args.settle * 1000), "fallback_duration": args.duration})
                params = {"format": "png"}
                if args.clip_viewport:
                    rect = json.loads(await evaluate("(() => { const r = document.getElementById('viewport').getBoundingClientRect(); return JSON.stringify({x: r.left, y: r.top, width: r.width, height: r.height}); })()"))
                    params["clip"] = {**rect, "scale": 1}
                shot = await send("Page.captureScreenshot", params)
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
    parser.add_argument("--url", required=True,
                        help="studio URL of YOUR OWN instance, e.g. http://127.0.0.1:8781/ (port 8770 is the user's "
                             "live studio: capturing there would switch the effect under them)")
    parser.add_argument("--allow-user-studio", action="store_true",
                        help="permit --url on port 8770 (the user's live studio); off by default")
    parser.add_argument("--effect", required=True, help="Library display name prefix, e.g. 'Fire AOE'")
    parser.add_argument("--times", default="1.0", help="comma-separated effect times in seconds (empty = no captures)")
    parser.add_argument("--out", required=True, help="output path prefix; _t<time>.png is appended")
    parser.add_argument("--size", default="1280x800")
    parser.add_argument("--scale", type=float, default=1.0, help="device scale factor (2 for retina-like detail)")
    parser.add_argument("--cdp-port", type=int, default=9333)
    parser.add_argument("--profile", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "aetherfx-chrome-profile"))
    parser.add_argument("--page-wait", type=float, default=10.0, help="seconds to wait after navigation")
    parser.add_argument("--load-wait", type=float, default=8.0, help="seconds to wait after clicking the library row")
    parser.add_argument("--settle", type=float, default=2.0, help="seconds to wait after seeking before capture")
    parser.add_argument("--duration", type=float, default=3.0, help="fallback effect duration if the readout is unparsable")
    parser.add_argument("--clip-viewport", action="store_true",
                        help="capture only the effect viewport (no studio chrome), for galleries and docs")
    parser.add_argument("--hide-hud", action="store_true",
                        help="hide the stats, mode badge and play bar overlays before capturing")
    parser.add_argument("--perf", type=float, default=0.0,
                        help="play the effect for this many seconds first and print render fps, stream fps, "
                             "draw calls, triangles and long tasks as JSON")
    parser.add_argument("--camera", default=None, help='optional JSON {"position":[x,y,z],"target":[x,y,z]} applied to the three.js camera before capture')
    parser.add_argument("--controls", default=None,
                        help='optional JSON {"control_id": value} set through the studio API after loading the '
                             'effect and before capturing, e.g. \'{"primary_intensity": 2.0}\'')
    args = parser.parse_args()
    if ":8770" in args.url and not args.allow_user_studio:
        parser.error("refusing to drive the user's live studio on port 8770; start your own instance "
                     "and pass its --url (or --allow-user-studio if you really mean it)")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
