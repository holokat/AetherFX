#!/usr/bin/env python3
"""Generate the four Bandage effects (subtle healing while a character bandages a forearm).

    python tools/generators/bandage.py --out examples/effects
    python tools/generators/bandage.py --check examples/effects

"Small actions keep great adventures going": one or two thin, soft healing ribbons spiral along the
forearm being wrapped and lift gently off it, a few soft motes and small glints rise slowly off the arm,
and the bandage itself glows faintly as the energy flows through it. Nothing is drawn for the arm, the
bandage or the character, there is no ground ring and nothing overhead.

Attachment: the effect origin (the `anchor` node) is the CENTRE OF THE WRAPPED FOREARM SEGMENT with the
forearm along the anchor's local X axis, so a game attaches `anchor` to a forearm socket. The look is
authored for a limb of about 0.08 m radius over about 0.3 m; the Limb size control scales it for bigger
or smaller arms. In the preview the anchor sits ARM_Y above the ground, where a kneeling character holds
the forearm.

Every variant shares the same node ids, animation and controls and differs only by its palette.

Layers
------
* RIBBONS  three staggered looping thin ribbons (one or two lit at any moment), each a thin core trail
           over a soft wider glow trail with a soft cross-width texture, drawn by an invisible bead on a
           parent chain: anchor -> spin (keyframed rotation about the arm's X axis: the orbit angle) ->
           bead (keyframed local [along-arm, radius, 0]). A bead flies one 1.5 s "flight" per cycle: an
           open spring of about two turns that advances 0.12 m along the arm per turn (so it never closes
           onto itself), with an irregular radius from turn to turn, then its orbit slows near the top of
           the arm while its radius grows, so the tail lifts up and away while it dissolves. While
           invisible it jumps back and waits for its old vertices to age out (flight + trail life <=
           cycle, so no jump segment is ever lit). Trails lay at most one vertex per 60 Hz step, so the
           orbit is kept under about 10 degrees per step: the loops are smooth curves, not polygons.
* BANDAGE  the cloth interaction: small, dim soft dabs sitting on the surface of the wrapped segment
           (an ellipsoid stretched along the forearm), together a faint elongated band that breathes with
           the loop, and one faint point light at the arm.
* MOTES    10-30 small, low-opacity motes rising slowly (0.1-0.3 m/s) off the top of the arm, a few tiny
           sparks shed from the ribbon heads, and rare, very small asymmetric five-ray glints. No crosses.

Timing (the default preview, 8.95 s)
------------------------------------
start 0-0.5 s (the first ribbon curls onto the arm, the glow and the motes fade in), wrap 0.5-2.0 s (the
other two ribbons join), healing 2.0-6.75 s (the held loop: 3.0 s cycles), finish 6.75-7.55 s (the
bandage is secured: the ribbon in flight brightens briefly and unwinds, decelerating evenly along its own
path to the top of the arm and rising off it as it dissolves; the wrap glow swells; a small puff of motes
lifts off), complete 7.55-8.95 s (the last motes rise and fade).

`metadata.loop` is the hold: every keyframe track repeats exactly every 3.0 s from 2.0 s to the finish
and the emitters run at constant or periodic rates there, so a game wraps effect time from 5.0 s back to
2.0 s (the simulation keeps stepping) for as long as the bandage lasts. To finish, it stops wrapping and
lets time run on into the finish at 6.75 s; everything between 5.0 s and 6.75 s continues the same
periodic cycle, so a game that wants the finish sooner may jump from any hold time t to t + 3.0 s
without a visible change.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# palettes - every colour in the effect comes from one of these
# ---------------------------------------------------------------------------

NATURE = {"hot": [0.86, 1.00, 0.80], "mid": [0.44, 0.96, 0.42], "deep": [0.12, 0.62, 0.22]}
HOLY = {"hot": [1.00, 0.93, 0.76], "mid": [1.00, 0.70, 0.30], "deep": [0.86, 0.42, 0.10]}
ARCANE = {"hot": [0.76, 0.86, 1.00], "mid": [0.30, 0.52, 1.00], "deep": [0.10, 0.20, 0.86]}
VOID = {"hot": [0.90, 0.78, 1.00], "mid": [0.64, 0.36, 1.00], "deep": [0.36, 0.10, 0.80]}

BASE_TAGS = ["heal", "support", "bandage", "healing", "over_time", "target"]

VARIANTS: dict[str, dict[str, Any]] = {
    "bandage": {
        "name": "Bandage", "palette": NATURE, "seed": 81201, "theme": "nature",
        "flavour": "nature, soft fresh green",
        "tags": BASE_TAGS + ["nature", "green"],
    },
    "bandage_holy": {
        "name": "Bandage Holy", "palette": HOLY, "seed": 81202, "theme": "holy",
        "flavour": "holy, warm gold",
        "tags": BASE_TAGS + ["holy", "gold"],
    },
    "bandage_arcane": {
        "name": "Bandage Arcane", "palette": ARCANE, "seed": 81203, "theme": "arcane",
        "flavour": "arcane, blue",
        "tags": BASE_TAGS + ["arcane", "blue"],
    },
    "bandage_void": {
        "name": "Bandage Void", "palette": VOID, "seed": 81204, "theme": "void",
        "flavour": "void, violet",
        "tags": BASE_TAGS + ["void", "violet"],
    },
}

# ---------------------------------------------------------------------------
# geometry and timing
# ---------------------------------------------------------------------------

ARM_Y = 0.9                 # preview only: the forearm's height above the ground
LIMB_RADIUS = 0.08          # what the look is authored for
LIMB_LENGTH = 0.3

CYCLE = 3.0                 # the held healing loop
START_END = 0.5
LOOP_START = 2.0            # the third ribbon's first flight: every track is periodic from here
FINISH = LOOP_START + CYCLE + 1.75     # 6.75: the bandage is secured (one ribbon mid-wrap, one young)
FINISH_LEN = 0.8
MOTE_LIFE_MAX = 1.5
DURATION = round(FINISH + 0.3 + MOTE_LIFE_MAX + 0.4, 3)   # 8.95: the last motes are gone

TRAIL_LIFE = 1.45           # how long a stretch of ribbon stays visible behind the bead
FADE_OUT = 0.25
KEY_STEP = 0.04

# One entry per bead. radius (m), turns per flight, start along the arm x0 (m) and pitch (m per turn), flight
# time V (s), the final orbit angle where the tail lifts off (deg; 0 = top of the arm, + towards the viewer),
# lift (m), radius irregularity (fraction) and its phase, brightness gain, first flight start.
# V + TRAIL_LIFE + 0.02 <= CYCLE, so the jump back to the start pose never lights a segment; the angular
# speed stays under about 10 degrees per 60 Hz step, so the loops are smooth curves, not polygons.
STREAMS = [
    {"id": "a", "radius": 0.118, "turns": 2.0, "x0": -0.14, "pitch": 0.12, "v": 1.5, "final": -18.0,
     "lift": 0.09, "irregular": 0.09, "irr_phase": 0.3, "gain": 1.0, "offset_t": 0.0},
    {"id": "b", "radius": 0.132, "turns": 1.85, "x0": -0.11, "pitch": 0.125, "v": 1.5, "final": -40.0,
     "lift": 0.08, "irregular": 0.1, "irr_phase": 2.1, "gain": 0.75, "offset_t": 1.0},
    {"id": "c", "radius": 0.108, "turns": 1.9, "x0": -0.16, "pitch": 0.12, "v": 1.5, "final": 8.0,
     "lift": 0.07, "irregular": 0.08, "irr_phase": 4.0, "gain": 0.6, "offset_t": 2.0},
]

CORE_WIDTH = 0.0036
GLOW_WIDTH = 0.032
CORE_EMISSIVE = 0.9
GLOW_EMISSIVE = 0.32
SHED_RATE = 4.0

MOTE_RATE = 8.0
GLINT_RATE = 0.9
WRAP_RADIUS = 0.086
WRAP_RATE = 14.0
WRAP_STRETCH = 1.7         # the wrapped segment is about 0.3 m long

RIPPLE_CORE = [[0.0, 1.0], [0.3, 0.9], [0.55, 0.72], [0.8, 0.6], [0.93, 0.3], [1.0, 0.0]]
RIPPLE_GLOW = [[0.0, 1.0], [0.3, 0.85], [0.55, 0.7], [0.8, 0.55], [0.93, 0.28], [1.0, 0.0]]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def lum(rgb: list[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def gain(p: dict[str, Any], key: str = "mid") -> float:
    """Equalise stage brightness across palettes (blue and violet are far less luminous than green)."""
    return round((lum(NATURE[key]) / lum(p[key])) ** 0.6, 4)


def c(rgb: list[float], a: float = 1.0) -> list[float]:
    return [round(rgb[0], 4), round(rgb[1], 4), round(rgb[2], 4), a]


def tr(pairs: list[tuple[float, Any]]) -> dict[str, Any]:
    keys = [{"time": round(t, 4), "value": v} for t, v in pairs]
    return {"value": pairs[0][1], "track": keys}


def node(nid: str, ntype: str, params: dict[str, Any], inputs: dict[str, Any] | None = None,
         parent: str | None = None, layer: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        entry["layer"] = layer
    if parent:
        entry["parent"] = parent
    entry["parameters"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def op(nid: str, kind: str, params: dict[str, Any] | None = None,
       inputs: dict[str, str] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "op": kind}
    if params:
        entry["params"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def tex(nid: str, w: int, h: int, nodes: list[dict[str, Any]], output: str) -> dict[str, Any]:
    return node(nid, "texture", {"width": w, "height": h, "graph": {"nodes": nodes, "output": output}})


def flights(s: dict[str, Any]) -> list[float]:
    """Absolute start times of a stream's flights (none starts at or after the finish)."""
    out, t = [], s["offset_t"]
    while t < FINISH - 1e-6:
        out.append(round(t, 4))
        t += CYCLE
    return out


EASE_IN = 0.1
WRAP = 0.62                 # fraction of a flight spent hugging the arm; the rest is the lift-off


def progress(s: dict[str, Any], u: float) -> float:
    """Eased flight progress: the bead starts at rest and comes up to speed over EASE_IN, so a young
    ribbon stays short and dim while it fades in and no flat tail end ever shows."""
    def p(x: float) -> float:
        return x - EASE_IN * (1.0 - math.exp(-x / EASE_IN))
    return p(u * s["v"]) / p(s["v"])


ORBIT_END = 0.3            # angular speed left at the end of the lift-off (fraction of the wrap speed)


def orbit(u: float) -> float:
    """Normalised orbit progress: constant angular speed while wrapping, then slowing as the tail lifts
    off (never to a stop, so the head's vertices do not bunch into a bright knot)."""
    slow = 1.0 - ORBIT_END
    total = WRAP + (1.0 - WRAP) * (1.0 - 0.5 * slow)
    if u <= WRAP:
        return u / total
    w = (u - WRAP) / (1.0 - WRAP)
    return (WRAP + (1.0 - WRAP) * (w - 0.5 * slow * w * w)) / total


def pose(s: dict[str, Any], u: float) -> tuple[float, list[float]]:
    """(orbit angle about the arm in degrees, local [along-arm, radius, 0]) at flight fraction u.

    The bead advances `pitch` along the arm per turn, so the ribbon is an open spring that never closes
    onto itself, and its radius breathes irregularly from turn to turn (two incommensurate sines), so no
    loop is a geometric circle."""
    u = progress(s, u)
    k = orbit(u)
    turns = s["turns"] * k
    ang = s["final"] - 360.0 * s["turns"] * (1.0 - k)
    w = 0.0 if u <= WRAP else (u - WRAP) / (1.0 - WRAP)
    lift = w ** 1.7
    x = s["x0"] + s["pitch"] * turns
    irr = s["irregular"] * (0.65 * math.sin(2.0 * math.pi * 1.37 * turns + s["irr_phase"])
                            + 0.35 * math.sin(2.0 * math.pi * 0.53 * turns + 2.0 * s["irr_phase"]))
    r = s["radius"] * (1.0 + irr) + s["lift"] * lift
    return round(ang, 3), [round(x, 4), round(r, 4), 0.0]


def tangent(s: dict[str, Any], u: float) -> tuple[float, float, float]:
    """d(angle, x, radius)/dt at flight fraction u (per second), for C1 continuations."""
    h = 0.004
    a1, p1 = pose(s, u)
    a0, p0 = pose(s, u - h)
    dt = h * s["v"]
    return (a1 - a0) / dt, (p1[0] - p0[0]) / dt, (p1[1] - p0[1]) / dt


UNWIND = 1.0                # seconds of the finish unwind (at least)
UNWIND_RISE = 0.14          # how far the head lifts off the arm as it unwinds


def unwind_keys(s: dict[str, Any], t0: float, u: float) -> list[tuple[float, list[float], list[float]]]:
    """The finish: from the bead's pose at flight fraction u it keeps going along its own tangent (so
    there is no corner) and decelerates evenly until it reaches the top of the arm, while its radius
    swells smoothly: the spring unwinds and the head rises up off the arm as the ribbon dissolves."""
    a1, p1 = pose(s, u)
    da, dx, _ = tangent(s, u)
    to_top = (s["final"] - a1) % 360.0 or 360.0      # the next time the orbit passes the lift-off angle
    span = 2.0 * to_top / da                         # even deceleration from the current speed to rest
    out = []
    n = int(math.ceil(max(span, UNWIND) / KEY_STEP))
    for i in range(1, n + 1):
        dt = i * KEY_STEP
        q = min(1.0, dt / span)
        ease = 2.0 * q - q * q                       # starts with the flight's own speed, ends at rest
        e = q * q * (3.0 - 2.0 * q)
        out.append((t0 + dt, [round(a1 + to_top * ease, 3), 0.0, 0.0],
                    [round(p1[0] + dx * span * 0.5 * ease, 4), round(p1[1] + UNWIND_RISE * e, 4), 0.0]))
    return out


def flight_tracks(s: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rotation and position tracks for a stream's bead: flights, and jumps back while invisible."""
    rot: list[tuple[float, Any]] = []
    pos: list[tuple[float, Any]] = []
    starts = flights(s)
    a0, p0 = pose(s, 0.0)
    if starts[0] > 0.0:
        rot.append((0.0, [a0, 0.0, 0.0]))
        pos.append((0.0, p0))
    for t0 in starts:
        v = s["v"]
        n = max(2, int(math.ceil(v / KEY_STEP)))
        cut = FINISH if t0 < FINISH < t0 + v else None
        for i in range(n + 1):
            u = i / n
            t = t0 + v * u
            if cut is not None and t > cut - 1e-6:
                break
            a, p = pose(s, u)
            if rot and abs(rot[-1][0] - t) < 1e-6:
                continue
            rot.append((t, [a, 0.0, 0.0]))
            pos.append((t, p))
        if cut is not None:
            uf = (cut - t0) / v
            a, p = pose(s, uf)
            rot.append((cut, [a, 0.0, 0.0]))
            pos.append((cut, p))
            for t, r, q in unwind_keys(s, cut, uf):
                if t >= DURATION - 1e-6:
                    break
                rot.append((t, r))
                pos.append((t, q))
            break
        # invisible now: jump back to the start pose and park there while the jump's vertices age out
        rot.append((t0 + v + 0.02, [a0, 0.0, 0.0]))
        pos.append((t0 + v + 0.02, p0))
    rot.append((DURATION, list(rot[-1][1])))
    pos.append((DURATION, list(pos[-1][1])))
    return tr(rot), tr(pos)


def envelope(s: dict[str, Any], peak: float, ramp: str = "width", flare: float = 1.0) -> dict[str, Any]:
    """Width / emissive envelope of a stream's trails: in on the arm, dissolve as the tail lifts off,
    and for a flight still in the air at the finish, a brief flare and a graceful fade.

    The trail's width is global while its taper follows vertex age, so the width grows as
    (age / TRAIL_LIFE)^2 over the first TRAIL_LIFE seconds (no flat cut at a young ribbon's tail); the
    emissive comes up faster so the thin core is there from the start."""
    if ramp == "width":
        rise = [(TRAIL_LIFE * f, f * f) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    else:
        rise = [(0.0, 0.0), (0.12, 0.5), (0.35, 1.0)]
    keys: list[tuple[float, float]] = []
    starts = flights(s)
    if starts[0] > 0.0:
        keys.append((0.0, 0.0))
    for t0 in starts:
        t1 = t0 + s["v"]
        hold = t1 - FADE_OUT
        seg = [(t0 + dt, peak * f) for dt, f in rise if t0 + dt < hold - 1e-6]
        seg += [(hold, peak), (t1, 0.0)]
        if t1 > FINISH:
            here = [(t, v) for t, v in seg if t < FINISH]
            level = here[-1][1] if here else 0.0
            seg = here + [(FINISH, level), (FINISH + 0.2, level * flare), (FINISH + UNWIND - 0.1, 0.0)]
        keys.extend(seg)
    keys.append((DURATION, 0.0))
    out: list[tuple[float, float]] = []
    for t, v in keys:
        if out and abs(out[-1][0] - t) < 1e-6:
            continue
        out.append((t, round(v, 5)))
    return tr(out)


def shed_rate(s: dict[str, Any], peak: float) -> dict[str, Any]:
    """Tiny sparks shed from a ribbon's head while it flies (not while it is parked at the top)."""
    keys: list[tuple[float, float]] = [(0.0, 0.0)]
    for t0 in flights(s):
        t1 = min(t0 + s["v"], FINISH + 0.3)
        keys += [(t0 + 0.15, 0.0), (t0 + 0.35, peak), (t1 - 0.1, peak), (t1, 0.0)]
    keys.append((DURATION, 0.0))
    out: list[tuple[float, float]] = []
    for t, v in keys:
        if out and abs(out[-1][0] - t) < 1e-6:
            continue
        out.append((round(t, 4), v))
    return tr(out)


def colour_envelope(s: dict[str, Any], rgb: list[float]) -> dict[str, Any]:
    """The trail colour follows the emissive envelope down to black, so a renderer that draws a
    zero-width ribbon as a hairline (the CPU reference does) still shows nothing between flights."""
    keys = envelope(s, 1.0, "glow")["track"]
    return tr([(k["time"], c([x * min(1.0, k["value"]) for x in rgb])) for k in keys])


def breathe(base: float, depth: float, t0: float, t1: float, step: float = 0.2,
            phase: float = 0.0) -> list[tuple[float, float]]:
    """A slow breath with the loop's period (periodic in absolute time, so the wrap never pops)."""
    keys = []
    n = int(round((t1 - t0) / step))
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        keys.append((round(t, 4),
                     round(base * (1.0 + depth * math.sin(2.0 * math.pi * (t / CYCLE + phase))), 4)))
    return keys


def envelope_keys(start: list[tuple[float, float]], base: float, depth: float, finish: list[tuple[float, float]],
                  phase: float = 0.0) -> dict[str, Any]:
    """Start ramp, a breath from the end of the start to the finish, then the finish keys."""
    t_in = start[-1][0]
    keys = start[:-1] + breathe(base, depth, t_in, FINISH, phase=phase) + finish + [(DURATION, 0.0)]
    return tr(keys)


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------

def build(slug: str, spec: dict[str, Any]) -> dict[str, Any]:
    p = spec["palette"]
    hot, mid, deep = p["hot"], p["mid"], p["deep"]
    g = gain(p)
    nodes: list[dict[str, Any]] = []
    add = nodes.append

    add(node("cam", "camera", {"position": [0.24, 1.16, 0.42], "target": [0.0, 0.975, 0.0], "fov": 40.0}))

    # ---- textures: a soft cross-width profile for the ribbons, round glows, an asymmetric glint ----
    band = [op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
            op("gi", "invert", None, {"a": "g"}),
            op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"})]
    add(tex("tex_core", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.42},
                                          {"a": "t"})], "lv"))
    add(tex("tex_glow", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.62},
                                          {"a": "t"})], "lv"))
    add(tex("tex_mote", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"gamma": 0.8}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_soft", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"gamma": 1.25}, {"a": "r"}),
    ], "lv"))
    # a small asymmetric sparkle: five thin tapering rays of the star op over a faint round halo
    add(tex("tex_glint", 48, 48, [
        op("s", "star", {"points": 5, "width": 0.07, "outer_radius": 0.47, "core_radius": 0.1,
                         "softness": 0.035, "rotation": 8.0}),
        op("h", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("hl", "levels", {"out_high": 0.3, "gamma": 1.5}, {"a": "h"}),
        op("k", "math", {"mode": "max"}, {"a": "s", "b": "hl"}),
    ], "k"))

    add(node("mat_core", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(hot),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.02, "double_sided": True,
    }, {"base_texture": "tex_core"}))
    add(node("mat_glow", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(mid),
        "emissive_intensity": 0.2, "soft_particle": True, "depth_fade": 0.03, "double_sided": True,
    }, {"base_texture": "tex_glow"}))
    add(node("mat_mote", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(hot),
        "emissive_intensity": 0.25, "soft_particle": True, "depth_fade": 0.02,
    }))

    add(node("f_curl", "force", {"force_type": "curl_noise", "strength": 0.05, "frequency": 4.0,
                                 "speed": 0.25}))
    add(node("f_lift", "force", {"force_type": "directional", "direction": [0.0, 1.0, 0.0],
                                 "strength": 0.04}))

    # ---- the attach point: the centre of the wrapped forearm segment, forearm along local X ----
    add(node("anchor", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                "position": [0.0, ARM_Y, 0.0], "scale": [1.0, 1.0, 1.0]}))

    # ---- healing ribbons ----
    add(node("ps_shed", "particle_system", {
        "max_particles": 16, "lifetime": 0.7, "lifetime_variance": 0.2,
        "size": 0.014, "size_variance": 0.004,
        "size_over_life": [[0.0, 0.6], [0.25, 1.0], [1.0, 0.5]],
        "color": c(mid), "color_over_life": [[0.0, c(hot)], [0.4, [1.0, 1.0, 1.0, 1.0]]],
        "opacity": 0.45, "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.6, 0.6], [1.0, 0.0]],
        "emissive": round(1.3 * g, 3), "drag": 1.6, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.01,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_curl", "f_lift"]}))

    for s in STREAMS:
        sid = s["id"]
        rot, pos = flight_tracks(s)
        add(node(f"spin_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, "rotation": rot,
        }, parent="anchor", layer="ribbons"))
        add(node(f"bead_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, "position": pos,
        }, parent=f"spin_{sid}", layer="ribbons"))
        add(node(f"glow_{sid}", "trail", {
            "lifetime": TRAIL_LIFE, "max_segments": 200, "min_vertex_distance": 0.006,
            "taper": [[0.0, 0.0], [0.08, 0.75], [0.2, 1.0], [0.55, 0.85], [0.82, 0.5], [1.0, 0.0]],
            "opacity_over_life": RIPPLE_GLOW,
            "color": colour_envelope(s, mid), "blend": "additive", "noise_amplitude": 0.0,
            "width": envelope(s, round(GLOW_WIDTH * (0.6 + 0.4 * s["gain"]), 4)),
            "emissive": envelope(s, round(GLOW_EMISSIVE * g * s["gain"], 4), "glow", 1.3),
        }, {"source": f"bead_{sid}", "material": "mat_glow"}, layer="ribbons"))
        add(node(f"core_{sid}", "trail", {
            "lifetime": TRAIL_LIFE, "max_segments": 200, "min_vertex_distance": 0.006,
            "taper": [[0.0, 0.0], [0.07, 0.85], [0.18, 1.0], [0.55, 0.8], [0.82, 0.45], [1.0, 0.0]],
            "opacity_over_life": RIPPLE_CORE,
            "color": colour_envelope(s, hot), "blend": "additive", "noise_amplitude": 0.0,
            "width": envelope(s, round(CORE_WIDTH * (0.6 + 0.4 * s["gain"]), 5)),
            "emissive": envelope(s, round(CORE_EMISSIVE * g * s["gain"], 4), "glow", 1.3),
        }, {"source": f"bead_{sid}", "material": "mat_core"}, layer="ribbons"))
        add(node(f"e_shed_{sid}", "emitter", {
            "shape": "point", "direction": [0.0, 0.0, 0.0], "velocity": 0.03, "velocity_variance": 0.02,
            "inherit_velocity": 0.2, "rate": shed_rate(s, round(SHED_RATE * s["gain"], 3)),
        }, {"particle": "ps_shed"}, parent=f"bead_{sid}", layer="ribbons"))

    # the light in the cloth: soft glow dabs sitting on the bandage surface all round the wrap, overlapping
    # into a faint band (in a game the arm hides the far half, so the near half reads as the lit cloth)
    add(node("ps_wrap", "particle_system", {
        "max_particles": 20, "lifetime": 1.1, "lifetime_variance": 0.2,
        "size": 0.045, "size_variance": 0.01,
        "size_over_life": [[0.0, 0.7], [0.4, 1.0], [1.0, 0.85]],
        "color": c(mid), "color_over_life": [[0.0, c(hot)], [0.5, [1.0, 1.0, 1.0, 1.0]]],
        "opacity": envelope_keys([(0.0, 0.0), (START_END, 0.0)], 0.32, 0.3,
                                 [(FINISH + 0.1, 0.32), (FINISH + 0.3, 0.45), (FINISH + 0.9, 0.0)], phase=0.25),
        "opacity_over_life": [[0.0, 0.0], [0.35, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": round(1.0 * g, 3), "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.02,
    }, {"sprite": "tex_soft", "material": "mat_mote"}))
    # dabs on the surface of the wrapped segment: an ellipsoid stretched along the forearm (local X)
    add(node("e_wrap", "emitter", {
        "shape": "sphere", "radius": WRAP_RADIUS, "surface_only": True, "scale": [WRAP_STRETCH, 1.0, 1.0],
        "direction": [0.0, 0.0, 0.0], "velocity": 0.0,
        "rate": tr([(0.0, WRAP_RATE), (FINISH + 0.5, WRAP_RATE), (FINISH + 0.55, 0.0), (DURATION, 0.0)]),
        "burst_count": 8, "burst_times": [0.0],
    }, {"particle": "ps_wrap"}, parent="anchor", layer="bandage"))
    light_keys = envelope_keys([(0.0, 0.0), (0.6, 0.0)], round(0.5 * g, 3), 0.25,
                               [(FINISH + 0.2, round(0.9 * g, 3)), (FINISH + 0.9, 0.0)])
    add(node("l_glow", "light", {
        "light_type": "point", "position": [0.0, 0.03, 0.04], "color": c(mid), "radius": 0.9,
        "intensity": light_keys,
    }, parent="anchor", layer="bandage"))

    # ---- motes and glints ----
    add(node("ps_motes", "particle_system", {
        "max_particles": 24, "lifetime": 1.3, "lifetime_variance": 0.2,
        "size": 0.03, "size_variance": 0.01,
        "size_over_life": [[0.0, 0.6], [0.25, 1.0], [0.8, 0.9], [1.0, 0.5]],
        "color": c(mid), "color_over_life": [[0.0, c(hot)], [0.3, [1.0, 1.0, 1.0, 1.0]], [1.0, c(deep)]],
        "opacity": 0.38, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.5, 0.7], [0.75, 0.85], [1.0, 0.0]],
        "emissive": round(1.8 * g, 3), "drag": 0.4, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.02,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_curl", "f_lift"]}))
    add(node("e_motes", "emitter", {
        "shape": "hemisphere", "radius": 0.11, "surface_only": True, "scale": [1.6, 1.0, 1.0],
        "direction": [0.0, 1.0, 0.0], "spread": 18.0, "velocity": 0.13, "velocity_variance": 0.05,
        "rate": tr([(0.0, 0.0), (START_END, MOTE_RATE), (FINISH + 0.3, MOTE_RATE), (FINISH + 0.4, 0.0),
                    (DURATION, 0.0)]),
    }, {"particle": "ps_motes"}, parent="anchor", layer="motes"))
    add(node("e_finish", "emitter", {
        "shape": "hemisphere", "radius": 0.1, "surface_only": True, "scale": [1.5, 1.0, 1.0],
        "direction": [0.0, 1.0, 0.0], "spread": 25.0, "velocity": 0.26, "velocity_variance": 0.06,
        "rate": 0.0, "burst_count": 7, "burst_times": [0.12], "start_time": FINISH, "duration": 0.3,
    }, {"particle": "ps_motes"}, parent="anchor", layer="motes"))
    add(node("ps_glint", "particle_system", {
        "max_particles": 4, "lifetime": 0.7, "lifetime_variance": 0.15,
        "size": 0.02, "size_variance": 0.005, "rotation": 0.0, "rotation_variance": 180.0,
        "angular_velocity": 20.0, "angular_velocity_variance": 30.0,
        "size_over_life": [[0.0, 0.3], [0.3, 1.0], [0.6, 0.7], [1.0, 0.2]],
        "color": c(hot), "opacity": 0.32,
        "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.45, 0.35], [0.65, 0.9], [1.0, 0.0]],
        "emissive": round(1.8 * g, 3), "drag": 0.6, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.02,
    }, {"sprite": "tex_glint", "material": "mat_mote", "forces": ["f_lift"]}))
    add(node("e_glint", "emitter", {
        "shape": "hemisphere", "radius": 0.15, "surface_only": False, "scale": [1.4, 1.0, 1.0],
        "direction": [0.0, 1.0, 0.0], "spread": 20.0, "velocity": 0.1, "velocity_variance": 0.04,
        "rate": tr([(0.0, 0.0), (START_END, GLINT_RATE), (FINISH + 0.3, GLINT_RATE), (FINISH + 0.4, 0.0),
                    (DURATION, 0.0)]),
    }, {"particle": "ps_glint"}, parent="anchor", layer="motes"))

    # ---- controls ----
    colour_bindings = []
    for n in nodes:
        for parameter in ("color", "color_over_life", "emissive_color"):
            if parameter in n["parameters"]:
                colour_bindings.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit, "bindings": bindings}

    trails = [f"{k}_{s['id']}" for s in STREAMS for k in ("glow", "core")]
    wrap = trails
    sheds = [f"e_shed_{s['id']}" for s in STREAMS]
    sprites = ["ps_motes", "ps_shed", "ps_glint", "ps_wrap"]
    controls = [
        control("intensity", "Intensity", "Global", 0.0, 3.0,
                [mul(t, "emissive") for t in wrap] + [mul(ps, "emissive") for ps in sprites]
                + [mul("l_glow", "intensity")]),
        control("ribbon_brightness", "Ribbon brightness", "Healing ribbons", 0.0, 3.0,
                [mul(t, "emissive") for t in trails]),
        control("mote_amount", "Mote amount", "Motes", 0.0, 3.0,
                [mul("e_motes", "rate"), mul("e_finish", "burst_count"), mul("e_glint", "rate")]
                + [mul(e, "rate") for e in sheds]),
        control("limb_size", "Limb size", "Global", 0.5, 2.5,
                [mul("anchor", "scale")] + [mul(t, "width") for t in wrap]
                + [mul(ps, "size") for ps in sprites]
                + [mul("e_motes", "velocity"), mul("e_finish", "velocity"), mul("l_glow", "radius")]),
        control("hue", "Energy hue", "Global", -180.0, 180.0, colour_bindings, "deg", 0.0, 1.0),
        control("global_speed", "Speed", "Global", 0.25, 4.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}], step=0.05),
    ]

    loop_end = round(LOOP_START + CYCLE, 3)
    finish_end = round(FINISH + FINISH_LEN, 3)
    return {
        "schema_version": "0.1.0",
        "name": spec["name"],
        "description": f"Subtle healing while a character bandages a forearm ({spec['flavour']}): thin soft "
                       "ribbons spiral round the arm and lift off, faint motes rise and the wrap glows. Attach "
                       "at the forearm centre, arm along X; loops while the bandage lasts.",
        "duration": DURATION,
        "seed": spec["seed"],
        "timeline": {"phases": [
            {"name": "start", "start": 0.0, "end": START_END},
            {"name": "wrap", "start": START_END, "end": LOOP_START},
            {"name": "healing", "start": LOOP_START, "end": FINISH},
            {"name": "finish", "start": FINISH, "end": finish_end},
            {"name": "complete", "start": finish_end, "end": DURATION},
        ]},
        "layers": [
            {"id": "ribbons", "name": "Healing ribbons", "role": "primary"},
            {"id": "bandage", "name": "Bandage glow", "role": "secondary"},
            {"id": "motes", "name": "Motes", "role": "secondary"},
        ],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/bandage.py",
            "variant": slug,
            "theme": spec["theme"],
            "category": "support",
            "loop": {"start": LOOP_START, "end": loop_end, "release": FINISH,
                     "note": f"Hold the heal for as long as the bandage lasts by keeping effect time inside the "
                             f"healing phase: every keyframe track repeats exactly every {CYCLE:g} s there and "
                             f"the emitters run at constant or periodic rates, so a runtime wraps time from "
                             f"{loop_end:g} s back to {LOOP_START:g} s while the simulation keeps stepping. When "
                             f"the bandage is done, stop wrapping and let time run on into the finish at "
                             f"{FINISH:g} s (everything from {loop_end:g} s to {FINISH:g} s continues the same "
                             f"periodic cycle, so a runtime may also jump from any hold time t to t + {CYCLE:g} s "
                             f"to finish sooner); the effect ends by itself at {DURATION:g} s."},
            "attachment": {
                "origin": [0.0, 0.0, 0.0], "origin_node": "anchor", "axis": "x",
                "limb_radius": LIMB_RADIUS, "limb_length": LIMB_LENGTH, "preview_height": ARM_Y,
                "notes": "Attach the `anchor` node (the effect origin) to a forearm socket at the centre of the "
                         "wrapped segment, with the forearm along the anchor's local X axis and local +Y up, and "
                         "let it follow the arm. Nothing is drawn for the arm, the bandage or the character. "
                         "Scale for thicker or thinner limbs with the Limb size control.",
            },
            "analysis": f"start 0-{START_END:g} the first ribbon curls onto the forearm, the wrap glow and the "
                        f"motes fade in | wrap {START_END:g}-{LOOP_START:g} the other ribbons join | healing "
                        f"{LOOP_START:g}-{FINISH:g} (held loop, {CYCLE:g} s cycle) one or two thin ribbons wind "
                        "along the arm as an open spring of about two turns and lift gently off it, the wrap "
                        "glows faintly and breathes, faint motes rise slowly | finish "
                        f"{FINISH:g}-{finish_end:g} the ribbon in flight brightens briefly, unwinds and rises off "
                        "the arm as it dissolves, the wrap glow swells, a small puff of motes lifts off | "
                        "complete the last motes rise and fade",
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.05,
                                "bloom_intensity": 0.3, "bloom_radius": 0.05, "exposure": 1.0, "grid": False},
            "tags": spec["tags"],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write the four effect JSON files into")
    parser.add_argument("--only", default="", help="comma-separated slugs to write (default: all four)")
    parser.add_argument("--check", help="directory whose files must match the generator output byte for byte")
    args = parser.parse_args()
    only = {s for s in args.only.split(",") if s}
    if args.check:
        bad = 0
        for slug, spec in VARIANTS.items():
            path = Path(args.check) / f"{slug}.json"
            same = path.exists() and path.read_text(encoding="utf-8") == render(build(slug, spec))
            bad += 0 if same else 1
            print(f"{path}: {'identical' if same else 'DIFFERENT'}")
        return 1 if bad else 0
    out = Path(args.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    for slug, spec in VARIANTS.items():
        if only and slug not in only:
            continue
        effect = build(slug, spec)
        path = out / f"{slug}.json"
        path.write_text(render(effect), encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
