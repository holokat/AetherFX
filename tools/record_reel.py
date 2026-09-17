#!/usr/bin/env python3
"""Record a demo reel of library effects from the real GPU viewer, frame by frame.

Drives a private headless Chrome over the DevTools protocol against a running studio, puts the
viewport in a clean full-window "reel mode", and for every effect steps through its timeline at the
output frame rate, capturing one screenshot per frame. Nothing is screen-recorded, so there are no
dropped frames and the footage is exactly what the viewer draws (HDR bloom, MSAA, volumes).
ffmpeg then assembles the clips with short fades plus intro and outro cards.

    python tools/record_reel.py --url http://127.0.0.1:8807/ --out out/reel \
        --effects "Lightning AOE,Fire AOE,Arcane AOE" --fps 60 --size 1920x1080

Needs: a studio you started yourself (never the user's live one), Google Chrome, ffmpeg, Pillow and
the `websockets` package (the studio venv has both).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request

CHROME = os.environ.get("AETHERFX_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

SETUP_JS = r"""
(() => {
  if (window.__reel) return 'ready';
  const style = document.createElement('style');
  style.textContent = `
    #viewport { position: fixed !important; inset: 0 !important; width: 100vw !important; height: 100vh !important;
                z-index: 9000 !important; background: #000 !important; border: 0 !important; border-radius: 0 !important; }
    #viewport canvas { width: 100% !important; height: 100% !important; }
    .gl-hud, #gl-stats, #gl-mode, #transport, .gl-note, .toast, #toasts, .toasts, #viewport-empty { display: none !important; }
    #reel-label { position: fixed; left: 56px; bottom: 48px; z-index: 9500; color: #fff; opacity: 0;
                  font: 600 34px/1.1 -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif; letter-spacing: -0.01em;
                  text-shadow: 0 2px 18px rgba(0,0,0,0.85), 0 0 2px rgba(0,0,0,0.9); }
    #reel-label small { display: block; margin-top: 8px; font: 500 15px/1.2 -apple-system, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
                        letter-spacing: 0.14em; text-transform: uppercase; color: rgba(255,255,255,0.62); }
    #reel-mark { position: fixed; right: 56px; bottom: 50px; z-index: 9500; color: rgba(255,255,255,0.5);
                 font: 500 15px/1 "SF Mono", Menlo, monospace; letter-spacing: 0.02em; text-shadow: 0 1px 10px rgba(0,0,0,0.9); }
  `;
  document.head.appendChild(style);
  const label = document.createElement('div'); label.id = 'reel-label'; document.body.appendChild(label);
  const mark = document.createElement('div'); mark.id = 'reel-mark'; document.body.appendChild(mark);
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  window.__reel = {
    base: null,
    async load(name, caption, markText) {
      document.querySelectorAll('#library-sections details').forEach(d => { d.open = true; });
      const rows = [...document.querySelectorAll('#library-sections li, #list-library li')];
      const row = rows.find(li => li.textContent.trim().toLowerCase().startsWith(name.toLowerCase()));
      if (row) {
        row.click();
      } else {
        // Community effects are not Library rows: open them by path through the page's own loader.
        const listing = await fetch('/api/effects').then(r => r.json()).catch(() => ({}));
        const pool = [].concat(listing.library || [], listing.community || []);
        const entry = pool.find(e => String(e.name || '').toLowerCase() === name.toLowerCase());
        if (!entry || typeof loadEffect !== 'function') return JSON.stringify({ error: 'no row for ' + name });
        loadEffect(entry.path);
      }
      const t0 = performance.now();
      while (performance.now() - t0 < 20000) {
        await new Promise(r => setTimeout(r, 250));
        const shown = (document.getElementById('effect-name') || {}).textContent || '';
        if (shown.trim().toLowerCase() === name.toLowerCase() && window.aetherViewer && window.aetherViewer.gl) break;
      }
      await new Promise(r => setTimeout(r, 2500));          // textures and meshes finish loading
      if (typeof S !== 'undefined' && S.playing) document.getElementById('btn-play').click();
      await new Promise(r => setTimeout(r, 300));
      const v = window.aetherViewer.gl;
      const p = v.camera.position, t = v.controls.target;
      const dx = p.x - t.x, dz = p.z - t.z;
      this.base = { radius: Math.hypot(dx, dz), azimuth: Math.atan2(dx, dz), y: p.y, tx: t.x, ty: t.y, tz: t.z };
      label.innerHTML = ''; label.appendChild(document.createTextNode(name));
      if (caption) { const s = document.createElement('small'); s.textContent = caption; label.appendChild(s); }
      mark.textContent = markText || '';
      const readout = (document.getElementById('time-readout') || {}).textContent || '';
      const m = readout.match(/\/\s*([0-9.]+)\s*s/);
      return JSON.stringify({ duration: m ? parseFloat(m[1]) : null, stats: (document.getElementById('gl-stats') || {}).textContent });
    },
    async frame(time, labelAlpha, sweepRad, progress) {
      const v = window.aetherViewer.gl, b = this.base;
      if (b && sweepRad) {
        const a = b.azimuth + sweepRad * (progress - 0.5);
        v.camera.position.set(b.tx + Math.sin(a) * b.radius, b.y, b.tz + Math.cos(a) * b.radius);
        v.controls.target.set(b.tx, b.ty, b.tz); v.controls.update();
      }
      label.style.opacity = String(labelAlpha);
      S.seekGuardUntil = 0;
      window.aetherViewer.seek(time);
      const t0 = performance.now();
      while (Math.abs((S.glTime || 0) - time) > 0.0085 && performance.now() - t0 < 4000) await raf();
      await raf(); await raf();
      return (S.glTime || 0).toFixed(4);
    }
  };
  return 'ready';
})()
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="", help="YOUR OWN studio instance, e.g. http://127.0.0.1:8807/ (never port 8770)")
    parser.add_argument("--out", required=True, help="output directory (frames/, clips/, reel.mp4)")
    parser.add_argument("--effects", default="", help="comma separated Library names, in reel order; 'Name|caption' adds a caption")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--sweep", type=float, default=14.0, help="camera orbit over a clip, in degrees (0 for projectiles is automatic)")
    parser.add_argument("--mark", default="", help="small text kept in the bottom right corner")
    parser.add_argument("--cdp-port", type=int, default=9370)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--tail", type=float, default=0.0, help="hold the last frame for this many seconds")
    parser.add_argument("--repeat-below", type=float, default=1.6, help="effects shorter than this many seconds play twice")
    parser.add_argument("--skip-existing", action="store_true", help="do not re-capture clips whose frames are complete")
    parser.add_argument("--no-intro", action="store_true", help="do not add the opening title card")
    parser.add_argument("--outro", action="store_true", help="add a closing card (off by default)")
    parser.add_argument("--assemble-only", action="store_true",
                        help="skip the browser entirely and rebuild the video from frames already under --out/frames")
    parser.add_argument("--name", default="aetherfx_reel", help="output file name without extension")
    args = parser.parse_args()
    if ":8770" in args.url:
        parser.error("refusing to drive the user's live studio on port 8770; start your own instance")
    if not args.assemble_only and not (args.url and args.effects):
        parser.error("--url and --effects are required unless --assemble-only is given")
    return args


async def record(args: argparse.Namespace) -> list[dict]:
    import websockets  # type: ignore

    width, height = (int(v) for v in args.size.lower().split("x"))
    scale = 2 if width >= 1600 else 1
    css_w, css_h = width // scale, height // scale
    profile = args.profile or os.path.join(args.out, "chrome-profile")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={args.cdp_port}", "--use-angle=metal", "--hide-scrollbars",
         "--no-first-run", "--disable-features=Translate", f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clips: list[dict] = []
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
            raise SystemExit("chrome did not expose a page")
        async with websockets.connect(ws_url, max_size=256 * 1024 * 1024) as ws:
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
                if result.get("exceptionDetails"):
                    raise RuntimeError(str(result["exceptionDetails"].get("exception", {}).get("description")))
                return str(result.get("result", {}).get("value"))

            await send("Emulation.setDeviceMetricsOverride",
                       {"width": css_w, "height": css_h, "deviceScaleFactor": scale, "mobile": False})
            await send("Page.enable")
            await send("Page.navigate", {"url": args.url})
            await asyncio.sleep(9)
            print("setup:", await evaluate(SETUP_JS))

            for index, spec in enumerate([e for e in args.effects.split(",") if e.strip()]):
                name, _, caption = spec.strip().partition("|")
                slug = f"{index:02d}_" + "".join(c if c.isalnum() else "_" for c in name.lower())
                frame_dir = os.path.join(args.out, "frames", slug)
                os.makedirs(frame_dir, exist_ok=True)
                info = json.loads(await evaluate(
                    f"window.__reel.load({json.dumps(name)}, {json.dumps(caption)}, {json.dumps(args.mark)})"))
                if info.get("error"):
                    print("SKIP", name, info["error"])
                    continue
                duration = float(info.get("duration") or 3.0)
                loop_frames = int(round(duration * args.fps))
                repeats = 2 if duration < args.repeat_below else 1          # very short effects play twice
                count = loop_frames * repeats
                hold = int(round(args.tail * args.fps))
                clip = {"name": name, "slug": slug, "dir": frame_dir, "frames": count + hold, "duration": duration * repeats}
                clips.append(clip)
                done = len([f for f in os.listdir(frame_dir) if f.endswith(".jpg")])
                if args.skip_existing and done >= count + hold:
                    print(f"{name}: {done} frames already captured")
                    continue
                projectile = any(k in name.lower() for k in ("bolt", "missile", "fireball", "shatter"))
                sweep = math.radians(0.0 if projectile else args.sweep)
                fade = max(1, int(0.3 * args.fps))
                started = time.time()
                for i in range(count + hold):
                    t = (min(i, count - 1) % loop_frames) / args.fps
                    alpha = min(1.0, (i + 1) / fade, (count + hold - i) / fade)
                    await evaluate(f"window.__reel.frame({t:.5f}, {alpha:.3f}, {sweep:.5f}, {min(1.0, i / max(1, count - 1)):.5f})")
                    shot = await send("Page.captureScreenshot", {"format": "jpeg", "quality": 95})
                    with open(os.path.join(frame_dir, f"{i:05d}.jpg"), "wb") as handle:
                        handle.write(base64.b64decode(shot["data"]))
                print(f"{name}: {count + hold} frames in {time.time() - started:.0f} s ({info.get('stats')})")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return clips


def make_card(path: str, size: tuple[int, int], title: str, lines: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    def font(px: int, bold: bool = False):
        for candidate in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                          "/System/Library/Fonts/Supplemental/Arial.ttf"):
            try:
                return ImageFont.truetype(candidate, px)
            except OSError:
                continue
        return ImageFont.load_default()

    image = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(image)
    w, h = size
    title_font, line_font = font(int(h * 0.105)), font(int(h * 0.034))
    tw = draw.textlength(title, font=title_font)
    y = h * 0.40
    draw.text(((w - tw) / 2, y), title, font=title_font, fill=(255, 255, 255))
    y += h * 0.15
    for line in lines:
        lw = draw.textlength(line, font=line_font)
        draw.text(((w - lw) / 2, y), line, font=line_font, fill=(176, 176, 190))
        y += h * 0.055
    image.save(path, quality=95)


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr[-2000:])
        raise SystemExit(f"command failed: {' '.join(cmd[:6])} ...")


def assemble(args: argparse.Namespace, clips: list[dict]) -> str:
    width, height = (int(v) for v in args.size.lower().split("x"))
    clip_dir = os.path.join(args.out, "clips")
    os.makedirs(clip_dir, exist_ok=True)
    fade = 0.22
    encode = ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(args.fps),
              "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    parts: list[str] = []

    def card(name: str, seconds: float, title: str, lines: list[str]) -> None:
        png = os.path.join(clip_dir, f"{name}.jpg")
        make_card(png, (width, height), title, lines)
        out = os.path.join(clip_dir, f"{name}.mp4")
        run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{seconds}", "-i", png,
             "-vf", f"scale=out_color_matrix=bt709:out_range=tv,fade=t=in:st=0:d=0.35,fade=t=out:st={seconds - 0.35:.2f}:d=0.35,format=yuv420p", *encode, out])
        parts.append(out)

    if not args.no_intro:
        card("000_intro", 2.0, "AetherFX", ["Game VFX authored by AI agents", "Open source  ·  MIT"])
    for clip in clips:
        seconds = clip["frames"] / args.fps
        out = os.path.join(clip_dir, f"{clip['slug']}.mp4")
        run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", os.path.join(clip["dir"], "%05d.jpg"),
             "-vf", f"scale={width}:{height}:flags=lanczos:in_color_matrix=bt601:out_color_matrix=bt709:in_range=full:out_range=tv,fade=t=in:st=0:d={fade},fade=t=out:st={max(0.0, seconds - fade):.3f}:d={fade},format=yuv420p",
             *encode, out])
        parts.append(out)
    if args.outro:
        card("zzz_outro", 3.0, "AetherFX", ["github.com/holokat/AetherFX", "Started by Gene  ·  @cogentgene1"])

    listing = os.path.join(clip_dir, "concat.txt")
    with open(listing, "w") as handle:
        for part in parts:
            handle.write(f"file '{os.path.abspath(part)}'\n")
    final = os.path.join(args.out, f"{args.name}.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
         "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-maxrate", "16M", "-bufsize", "32M", "-pix_fmt", "yuv420p",
         "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-r", str(args.fps), "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", final])
    return final


def existing_clips(args: argparse.Namespace) -> list[dict]:
    """Clips already captured under <out>/frames, in the order given by --effects (numbered folders)."""
    root = os.path.join(args.out, "frames")
    clips = []
    for slug in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        frame_dir = os.path.join(root, slug)
        count = len([f for f in os.listdir(frame_dir) if f.endswith(".jpg")]) if os.path.isdir(frame_dir) else 0
        if count:
            clips.append({"name": slug, "slug": slug, "dir": frame_dir, "frames": count, "duration": count / args.fps})
    return clips


def main() -> int:
    args = parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    os.makedirs(args.out, exist_ok=True)
    clips = existing_clips(args) if args.assemble_only else asyncio.run(record(args))
    if not clips:
        raise SystemExit("nothing was recorded")
    final = assemble(args, clips)
    seconds = sum(c["frames"] for c in clips) / args.fps + (0.0 if args.no_intro else 2.0) + (3.0 if args.outro else 0.0)
    print(f"reel: {final}  ({seconds:.0f} s, {os.path.getsize(final) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
