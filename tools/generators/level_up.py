#!/usr/bin/env python3
"""Generate the Level Up effect (a short, grand celebration sequence) from one parametrised description.

    python tools/generators/level_up.py --out examples/effects
    python tools/generators/level_up.py --check examples/effects

"New horizons await": light, energy and runes converge from the ground to the character and culminate
in a radiant burst with a crystalline level core above the head, then settle and fade, without hiding
the character for long. The origin is the character's FEET at y = 0; the look is authored for a
character about 1.8 m tall with a body radius of about 0.45 m (the ground rune is twice that), and the
Character height control scales it for bigger or smaller ones. Holy gold with white-hot cores; every
colour comes from the PALETTE table, so the Energy hue control (a hue shift bound to every colour)
re-themes it in one move.

Beats (the sheet's row)
-----
1. GATHER    0-0.4 s     the ground rune draws in from outside and lights up; streaks of light run in
                         over the ground from the environment and curl up the body.
2. ASCEND    0.4-0.95 s  three energy ribbons spiral up round the body from the ankles (small glyph
                         plates shed off them), soft tapered light shafts rise out of the rune, and a
                         dozen stone fragments lift off the ground tumbling.
3. CULMINATE 0.95-1.35 s a soft column of light climbs through the body into the level core: an
                         elongated faceted diamond of light that snaps into being at about 2.75 m with a
                         soft radial glow and a spray of many short irregular rays (never four arms),
                         a flash of the chest light, the ribbons converging into the core.
4. EMPOWER   1.35-1.85 s the ribbons dissolve at the top, the column thins and withdraws upward, the core
                         rises a little and fades, the debris keeps drifting up and shrinks away.
5. COMPLETE  1.85-2.6 s  the ground rune expands and fades cleanly, the last motes drift up and fade.

Techniques: helical ribbons are trails behind invisible beads on keyframed parent chains
(tools/generators/power_up.py); the rune, the glyph plates and the core are `shape`/`ring`/`voronoi`
texture graphs (tools/generators/shield_variants.py); shafts and rays are stretched billboards of a
soft radial sprite, so they taper at both ends; debris is a seeded `rock` mesh with variants.
House style: no crosses, plus signs or four-armed stars anywhere; ribbons have a soft cross-width
profile and taper; shafts are soft spindles; the output is deterministic.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# palette - every colour in the effect comes from here
# ---------------------------------------------------------------------------

PALETTE = {
    "white": [1.00, 0.95, 0.84],   # white-hot cores (still warm, never pure white)
    "hot": [1.00, 0.84, 0.52],     # ribbon cores, sparks, the crystal
    "mid": [1.00, 0.60, 0.20],     # the signature gold: glows, the rune, shafts
    "deep": [0.80, 0.32, 0.06],    # falloff, dying motes
    "stone": [0.085, 0.07, 0.06],   # debris albedo
    # cool accents (the user's reference: an icy crystal, a blue-white column, blue shards among the gold)
    "frost": [0.84, 0.92, 1.00],   # blue-white: the column, the crystal's heart
    "ice": [0.50, 0.72, 1.00],     # the blue: crystal body, cool shafts, floating shards
}

SLUG = "level_up"
NAME = "Level Up"
SEED = 72201
TAGS = ["level_up", "celebration", "support", "buff", "holy", "gold", "target"]

DURATION = 2.6          # 2.2 s sequence plus the last motes fading
CORE_Y = 2.75           # the level core's height above the feet
RUNE_D = 1.9            # ground rune diameter (radius ~0.95 m = 2 x body radius)

# phase boundaries
GATHER, ASCEND, CULMINATE, EMPOWER, COMPLETE = 0.0, 0.4, 0.95, 1.35, 1.85

# ---------------------------------------------------------------------------
# ribbons: one entry per bead - base radius (m), turns, heights y0 -> y1, flight time, start, phase (deg),
# brightness gain, radius wobble, helix-axis lean (deg about x, z)
# ---------------------------------------------------------------------------

RIBBONS = [
    {"id": "a", "radius": 0.72, "turns": 1.45, "y0": 0.08, "y1": 2.55, "v": 0.9, "start": 0.26, "phase": 15.0,
     "gain": 1.0, "wobble": 0.05, "lean": [3.0, -2.0]},
    {"id": "b", "radius": 0.66, "turns": 1.35, "y0": 0.1, "y1": 2.5, "v": 0.92, "start": 0.33, "phase": 140.0,
     "gain": 0.8, "wobble": 0.06, "lean": [-2.5, 2.5]},
    {"id": "c", "radius": 0.78, "turns": 1.5, "y0": 0.08, "y1": 2.58, "v": 0.88, "start": 0.4, "phase": 262.0,
     "gain": 0.9, "wobble": 0.05, "lean": [2.0, 3.0]},
    # the fourth ribbon is born at the culmination and swirls round the level core like a halo
    {"id": "d", "radius": 0.46, "turns": 1.3, "y0": 2.35, "y1": 2.9, "v": 0.8, "start": 1.0, "phase": 60.0,
     "gain": 0.7, "wobble": 0.04, "lean": [32.0, -24.0], "narrow": 0.2},
]

TRAIL_LIFE = 0.62
FADE_OUT = 0.34
KEY_STEP = 0.03
EASE_IN = 0.16
CORE_WIDTH = 0.022
GLOW_WIDTH = 0.22
CORE_EMISSIVE = 1.9
GLOW_EMISSIVE = 0.75

RIPPLE_CORE = [[0.0, 1.0], [0.25, 0.9], [0.5, 0.55], [0.75, 0.8], [1.0, 0.0]]
RIPPLE_GLOW = [[0.0, 1.0], [0.25, 0.85], [0.5, 0.55], [0.75, 0.7], [1.0, 0.0]]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def c(rgb: list[float], a: float = 1.0) -> list[float]:
    return [round(rgb[0], 4), round(rgb[1], 4), round(rgb[2], 4), a]


def mix(a: list[float], b: list[float], t: float) -> list[float]:
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def tr(pairs: list[tuple[float, Any]]) -> dict[str, Any]:
    keys = []
    for t, v in pairs:
        if isinstance(v, float):
            v = round(v, 5)
        keys.append({"time": round(t, 4), "value": v})
    return {"value": keys[0]["value"], "track": keys}


def curve(pairs: list[tuple[float, float]]) -> list[list[float]]:
    return [[round(t, 4), round(v, 4)] for t, v in pairs]


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


def scaled(nid: str, src: str, k: float) -> dict[str, Any]:
    return op(nid, "levels", {"out_high": k}, {"a": src})


def fold(prefix: str, ids: list[str], mode: str = "max") -> tuple[list[dict[str, Any]], str]:
    parts: list[dict[str, Any]] = []
    cur = ids[0]
    for i, nxt in enumerate(ids[1:]):
        nid = f"{prefix}{i}"
        parts.append(op(nid, "math", {"mode": mode}, {"a": cur, "b": nxt}))
        cur = nid
    return parts, cur


def tex(nid: str, w: int, h: int, nodes: list[dict[str, Any]], output: str, frames: int = 1) -> dict[str, Any]:
    params: dict[str, Any] = {"width": w, "height": h}
    if frames > 1:
        params["frames"] = frames
    params["graph"] = {"nodes": nodes, "output": output}
    return node(nid, "texture", params)


# ---------------------------------------------------------------------------
# texture graphs
# ---------------------------------------------------------------------------

def rune_rings_graph() -> tuple[list[dict[str, Any]], str]:
    """The ground rune: concentric hairline rings, the main ring broken into seven arcs with a small
    hexagonal seal in each gap, dot markers, fine outer ticks and a soft wash under the feet."""
    parts = [
        op("r0", "ring", {"radius": 0.476, "thickness": 0.005, "softness": 0.005}),
        op("r1", "ring", {"radius": 0.448, "thickness": 0.011, "softness": 0.007}),
        op("r2", "ring", {"radius": 0.37, "thickness": 0.005, "softness": 0.005}),
        op("r3", "ring", {"radius": 0.30, "thickness": 0.013, "softness": 0.008}),
        op("r4", "ring", {"radius": 0.215, "thickness": 0.005, "softness": 0.005}),
        op("r5", "ring", {"radius": 0.12, "thickness": 0.007, "softness": 0.007}),
        op("wash", "ring", {"radius": 0.26, "thickness": 0.2, "softness": 0.16}),
        scaled("washd", "wash", 0.16),
        op("gaps", "spokes", {"count": 11, "width": 0.05, "softness": 0.02, "inner_radius": 0.40,
                              "outer_radius": 0.5, "rotation": 12.0}),
        op("gapsi", "invert", None, {"a": "gaps"}),
        op("r1c", "math", {"mode": "multiply"}, {"a": "r1", "b": "gapsi"}),
        op("gaps3", "spokes", {"count": 7, "width": 0.07, "softness": 0.014, "inner_radius": 0.25,
                               "outer_radius": 0.35}),
        op("gaps3i", "invert", None, {"a": "gaps3"}),
        op("r3c", "math", {"mode": "multiply"}, {"a": "r3", "b": "gaps3i"}),
        op("dots", "spokes", {"count": 15, "width": 0.016, "softness": 0.009, "inner_radius": 0.207,
                              "outer_radius": 0.224, "rotation": 8.0}),
        op("ticks", "spokes", {"count": 45, "width": 0.0055, "softness": 0.004, "inner_radius": 0.455,
                               "outer_radius": 0.472, "rotation": 5.0}),
        scaled("ticksd", "ticks", 0.7),
    ]
    ids = ["r0", "r1c", "r2", "r3c", "r4", "r5", "washd", "dots", "ticksd"]
    for i in range(7):
        ang = 2.0 * math.pi * i / 7.0
        cx = round(0.5 + 0.30 * math.cos(ang), 4)
        cy = round(0.5 + 0.30 * math.sin(ang), 4)
        rot = round(-math.degrees(ang) + 90.0, 2)
        parts += [
            op(f"seal{i}", "shape", {"shape": "hexagon", "mode": "outline", "radius": 0.024,
                                     "outline_width": 0.006, "softness": 0.004, "center": [cx, cy],
                                     "rotation": rot}),
            op(f"pip{i}", "shape", {"shape": "diamond", "radius": 0.009, "aspect": 0.7, "softness": 0.004,
                                    "center": [cx, cy], "rotation": rot}),
        ]
        ids += [f"seal{i}", f"pip{i}"]
    folds, out = fold("gm", ids, "max")
    return parts + folds, out


def rune_glyphs_graph(seed: int) -> tuple[list[dict[str, Any]], str]:
    """The counter-rotating glyph band of the ground rune: angular strokes clipped to two bands and
    splayed tick pairs between them."""
    parts = [
        op("vo", "voronoi", {"cells": 34, "mode": "edges", "seed": seed}),
        op("vol", "levels", {"in_low": 0.66, "in_high": 0.95}, {"a": "vo"}),
        op("band", "ring", {"radius": 0.408, "thickness": 0.046, "softness": 0.016}),
        op("ang", "math", {"mode": "multiply"}, {"a": "vol", "b": "band"}),
        op("vo2", "voronoi", {"cells": 24, "mode": "edges", "seed": seed + 19}),
        op("vo2l", "levels", {"in_low": 0.72, "in_high": 0.97}, {"a": "vo2"}),
        op("band2", "ring", {"radius": 0.258, "thickness": 0.036, "softness": 0.014}),
        op("ang2", "math", {"mode": "multiply"}, {"a": "vo2l", "b": "band2"}),
        scaled("ang2d", "ang2", 0.7),
        op("cv1", "spokes", {"count": 9, "width": 0.010, "softness": 0.007, "inner_radius": 0.325,
                             "outer_radius": 0.355, "rotation": 9.0}),
        op("cv2", "spokes", {"count": 9, "width": 0.010, "softness": 0.007, "inner_radius": 0.325,
                             "outer_radius": 0.355, "rotation": -9.0}),
        op("m0", "math", {"mode": "max"}, {"a": "ang", "b": "ang2d"}),
        op("m1", "math", {"mode": "max"}, {"a": "m0", "b": "cv1"}),
        op("m2", "math", {"mode": "max"}, {"a": "m1", "b": "cv2"}),
        op("soft", "blur", {"radius": 0.9}, {"a": "m2"}),
        op("lv", "levels", {"in_low": 0.02, "in_high": 0.86, "gamma": 0.9}, {"a": "soft"}),
    ]
    return parts, "lv"


def glyph_graph(seed: int) -> tuple[list[dict[str, Any]], str]:
    """A small rune plate for the glyphs that ride the ribbons: a hexagonal seal with angular strokes
    morphing inside it over the flipbook (the same rune language as the ground)."""
    parts = [
        op("w", "fbm", {"frequency": 2.6, "octaves": 2, "seed": seed + 5, "animate": 1.0}),
        op("vo", "voronoi", {"cells": 4, "mode": "edges", "seed": seed}),
        op("vl", "levels", {"in_low": 0.6, "in_high": 0.96}, {"a": "vo"}),
        op("d", "distort", {"amount": 0.15}, {"a": "vl", "by": "w"}),
        op("plate", "shape", {"shape": "hexagon", "radius": 0.33, "softness": 0.05}),
        op("m", "math", {"mode": "multiply"}, {"a": "d", "b": "plate"}),
        op("seal", "shape", {"shape": "hexagon", "mode": "outline", "radius": 0.43, "outline_width": 0.05,
                             "inset": 0.025, "softness": 0.03}),
        scaled("seald", "seal", 0.85),
        op("all", "math", {"mode": "max"}, {"a": "m", "b": "seald"}),
        op("b", "blur", {"radius": 0.8}, {"a": "all"}),
        op("lv", "levels", {"in_low": 0.05, "in_high": 0.6, "gamma": 0.85}, {"a": "b"}),
    ]
    return parts, "lv"


def crystal_graph() -> tuple[list[dict[str, Any]], str]:
    """The level core: an elongated faceted diamond of light, taller than wide. A bright rim, an inner
    glow hugging the rim, a lit left facet and a dimmer right one split by the vertical ridge (no
    horizontal line, so nothing reads as a cross), a concentric inner facet, a hot centre and a soft
    halo just outside the contour."""
    dia = {"shape": "diamond", "radius": 0.46, "aspect": 0.5}
    parts = [
        op("fill", "shape", {**dia, "mode": "fill", "softness": 0.008}),
        op("rim", "shape", {**dia, "mode": "outline", "outline_width": 0.02, "inset": 0.01, "softness": 0.008}),
        op("bev", "shape", {**dia, "mode": "bevel", "bevel": 0.1}),
        op("bevi", "invert", None, {"a": "bev"}),
        op("inner", "math", {"mode": "multiply"}, {"a": "bevi", "b": "fill"}),
        scaled("innerd", "inner", 0.75),
        # two facets: left lit, right in shade, a soft ridge between them
        op("split", "gradient_linear", {"angle": 0.0, "start": 0.0, "end": 1.0}),
        op("facet", "levels", {"in_low": 0.485, "in_high": 0.515, "out_low": 0.86, "out_high": 0.46},
           {"a": "split"}),
        op("vert", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("vertl", "levels", {"out_low": 1.0, "out_high": 0.7}, {"a": "vert"}),
        op("face0", "math", {"mode": "multiply"}, {"a": "facet", "b": "vertl"}),
        op("face", "math", {"mode": "multiply"}, {"a": "face0", "b": "fill"}),
        op("in2", "shape", {**dia, "mode": "outline", "outline_width": 0.012, "inset": 0.16, "softness": 0.01}),
        scaled("in2d", "in2", 0.55),
        op("hot", "gradient_radial", {"radius": 0.3, "falloff": "smooth"}),
        op("hotf", "math", {"mode": "multiply"}, {"a": "hot", "b": "fill"}),
        op("halo", "shape", {**dia, "mode": "bevel", "inset": -0.1, "bevel": 0.1}),
        op("haloo", "math", {"mode": "subtract"}, {"a": "halo", "b": "fill"}),
        scaled("halod", "haloo", 0.3),
    ]
    folds, out = fold("cm", ["rim", "innerd", "face", "in2d", "hotf", "halod"], "max")
    parts += folds
    parts.append(op("out", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 0.95}, {"a": out}))
    return parts, "out"


# ---------------------------------------------------------------------------
# ribbon animation (parent-chain beads, from power_up.py)
# ---------------------------------------------------------------------------

def progress(s: dict[str, Any], u: float) -> float:
    def p(x: float) -> float:
        return x - EASE_IN * (1.0 - math.exp(-x / EASE_IN))
    return p(u * s["v"]) / p(s["v"])


def pose(s: dict[str, Any], u: float) -> tuple[float, list[float]]:
    """(orbit angle in degrees, local [radius, height, 0]) at flight fraction u: the helix climbs from
    the ankles and narrows towards the top so the ribbons converge into the level core."""
    u = progress(s, u)
    ease = 0.85 * u + 0.15 * (u * u * (3.0 - 2.0 * u))
    y = s["y0"] + (s["y1"] - s["y0"]) * ease
    r = s["radius"] * (1.0 - s.get("narrow", 0.62) * ease ** 1.6) + s["wobble"] * math.sin(2.0 * math.pi * 1.2 * u + s["phase"] * 0.017)
    ang = s["phase"] + 360.0 * s["turns"] * u
    return round(ang, 3), [round(r, 4), round(y, 4), 0.0]


def flight_tracks(s: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rot: list[tuple[float, Any]] = []
    pos: list[tuple[float, Any]] = []
    a0, p0 = pose(s, 0.0)
    t0, v = s["start"], s["v"]
    rot.append((0.0, [0.0, a0, 0.0]))
    pos.append((0.0, p0))
    n = max(2, int(math.ceil(v / KEY_STEP)))
    for i in range(n + 1):
        u = i / n
        a, p = pose(s, u)
        rot.append((t0 + v * u, [0.0, a, 0.0]))
        pos.append((t0 + v * u, p))
    # after the flight the bead keeps circling slowly at the top while the ribbon dissolves
    a1, p1 = pose(s, 1.0)
    rot.append((t0 + v + 0.4, [0.0, round(a1 + 70.0, 3), 0.0]))
    pos.append((t0 + v + 0.4, [p1[0], round(p1[1] + 0.12, 4), 0.0]))
    rot.append((DURATION, [0.0, round(a1 + 90.0, 3), 0.0]))
    pos.append((DURATION, [p1[0], round(p1[1] + 0.15, 4), 0.0]))
    return tr(rot), tr(pos)


def envelope(s: dict[str, Any], peak: float, ramp: str = "width") -> dict[str, Any]:
    """Width / emissive envelope of a ribbon: grows in at the ankles, holds, dissolves at the top."""
    t0 = s["start"]
    t1 = t0 + s["v"]
    hold = t1 - FADE_OUT
    if ramp == "width":
        rise = [(0.8 * TRAIL_LIFE * f, f ** 1.5) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    else:
        rise = [(0.0, 0.0), (0.08, 0.5), (0.22, 1.0)]
    keys: list[tuple[float, float]] = [(0.0, 0.0)]
    keys += [(t0 + dt, peak * f) for dt, f in rise if t0 + dt < hold - 1e-6]
    keys += [(hold, peak), (t1 + 0.12, peak * 0.35), (t1 + 0.3, 0.0), (DURATION, 0.0)]
    out: list[tuple[float, float]] = []
    for t, v in keys:
        if out and abs(out[-1][0] - t) < 1e-6:
            continue
        out.append((t, round(v, 5)))
    return tr(out)


def colour_envelope(s: dict[str, Any], rgb: list[float]) -> dict[str, Any]:
    keys = envelope(s, 1.0, "glow")["track"]
    return tr([(k["time"], c([x * k["value"] for x in rgb])) for k in keys])


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------

def build() -> dict[str, Any]:
    p = PALETTE
    white, hot, mid, deep, stone = p["white"], p["hot"], p["mid"], p["deep"], p["stone"]
    frost, ice = p["frost"], p["ice"]
    nodes: list[dict[str, Any]] = []
    add = nodes.append

    add(node("cam", "camera", {"position": [1.75, 0.9, 3.45], "target": [0.0, 1.55, 0.0], "fov": 40.0}))

    # ---------------- textures ----------------
    band = [op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
            op("gi", "invert", None, {"a": "g"}),
            op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"})]
    add(tex("tex_ribbon_core", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.42},
                                                 {"a": "t"})], "lv"))
    add(tex("tex_ribbon_glow", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.7},
                                                 {"a": "t"})], "lv"))
    add(tex("tex_mote", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"gamma": 0.8}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_glint", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.02, "falloff": "quadratic"}),
        op("lv", "levels", {"gamma": 1.3}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_glow", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        op("lv", "levels", {"gamma": 1.1}, {"a": "r"}),
    ], "lv"))
    # a soft light-shaft spindle: stretched along the velocity it tapers at both ends; a little fbm
    # breaks the body so no two shafts are the same even strip
    add(tex("tex_shaft", 32, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 3.0, "octaves": 3, "seed": 11}),
        op("nl", "levels", {"in_low": 0.2, "in_high": 0.8, "out_low": 0.85, "out_high": 1.0}, {"a": "n"}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
        op("lv", "levels", {"in_low": 0.02, "in_high": 0.9, "gamma": 1.5}, {"a": "m"}),
    ], "lv"))
    rr_nodes, rr_out = rune_rings_graph()
    add(tex("tex_rune_rings", 512, 512, rr_nodes, rr_out))
    rg_nodes, rg_out = rune_glyphs_graph(SEED % 7919)
    add(tex("tex_rune_glyphs", 384, 384, rg_nodes, rg_out))
    add(tex("tex_pool", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"gamma": 1.6}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_wave", 256, 256, [
        op("r", "ring", {"radius": 0.44, "thickness": 0.02, "softness": 0.03}),
        op("r2", "ring", {"radius": 0.4, "thickness": 0.06, "softness": 0.07}),
        scaled("r2d", "r2", 0.35),
        op("m", "math", {"mode": "max"}, {"a": "r", "b": "r2d"}),
    ], "m"))
    gl_nodes, gl_out = glyph_graph((SEED + 41) % 7919)
    add(tex("tex_glyph", 64, 64, gl_nodes, gl_out, frames=6))
    cr_nodes, cr_out = crystal_graph()
    add(tex("tex_crystal", 256, 256, cr_nodes, cr_out))

    # ---------------- materials ----------------
    def additive(mid_: str, rgb: list[float], ei: float, depth_fade: float = 0.08, **extra: Any) -> None:
        params = {"blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(rgb),
                  "emissive_intensity": ei, "soft_particle": True, "depth_fade": depth_fade}
        params.update(extra)
        add(node(mid_, "material", params))

    add(node("mat_ribbon_core", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(hot),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.08, "double_sided": True,
    }, {"base_texture": "tex_ribbon_core"}))
    add(node("mat_ribbon_glow", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(mid),
        "emissive_intensity": 0.2, "soft_particle": True, "depth_fade": 0.12, "double_sided": True,
    }, {"base_texture": "tex_ribbon_glow"}))
    additive("mat_mote", hot, 0.25, 0.06)
    additive("mat_shaft", mid, 0.15, 0.2)
    additive("mat_glyph", hot, 0.25, 0.06)
    additive("mat_glow", mid, 0.15, 0.3)
    additive("mat_crystal", frost, 0.3, 0.05)
    additive("mat_shaft_cool", ice, 0.15, 0.2)
    add(node("mat_rune", "material", {"blend": "additive", "emissive_color": c(mid), "emissive_intensity": 0.2,
                                      "soft_particle": False}))
    add(node("mat_rock", "material", {"blend": "alpha", "shading": "lit", "base_color": c(stone),
                                      "emissive_color": c(mid), "emissive_intensity": 0.07}))

    # ---------------- forces ----------------
    add(node("f_curl", "force", {"force_type": "curl_noise", "strength": 0.35, "frequency": 1.1, "speed": 0.3}))
    add(node("f_lift", "force", {"force_type": "buoyancy", "strength": 0.6}))
    add(node("f_gather", "force", {"force_type": "attractor", "position": [0.0, 0.9, 0.0], "strength": 2.2,
                                   "radius": 3.0}))
    add(node("f_float", "force", {"force_type": "buoyancy", "strength": 0.35}))

    # ---------------- the attach point: the character's feet ----------------
    add(node("anchor", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                "scale": [1.0, 1.0, 1.0]}))

    # ================= ground rune (GATHER draws it in, COMPLETE expands and fades it) =================
    R = RUNE_D
    add(node("d_pool", "decal", {
        "shape": "circle", "size": tr([(0.0, [R * 0.6, R * 0.6]), (0.35, [R * 1.25, R * 1.25]),
                                       (1.0, [R * 1.4, R * 1.4]), (2.4, [R * 1.8, R * 1.8])]),
        "position": [0.0, 0.0, 0.0], "color": c(mid), "blend": "additive", "fade_in": 0.05, "fade_out": 0.2,
        "emissive": 0.4,
        "opacity": tr([(0.0, 0.0), (0.2, 0.3), (0.95, 0.32), (1.05, 0.45), (1.4, 0.26), (1.9, 0.14),
                       (2.35, 0.0)]),
    }, {"texture": "tex_pool", "material": "mat_rune"}, parent="anchor", layer="ground"))
    rune_size = tr([(0.0, [R * 1.7, R * 1.7]), (0.18, [R * 1.18, R * 1.18]), (0.38, [R, R]),
                    (1.85, [R, R]), (2.15, [R * 1.22, R * 1.22]), (2.45, [R * 1.42, R * 1.42])])
    rune_emissive = tr([(0.0, 0.3), (0.35, 1.1), (0.9, 0.9), (1.02, 1.8), (1.3, 1.0), (1.85, 0.9),
                        (2.2, 0.5), (2.45, 0.1)])
    add(node("d_rune", "decal", {
        "shape": "circle", "size": rune_size, "position": [0.0, 0.005, 0.0],
        "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (DURATION, [0.0, 40.0, 0.0])]),
        "color": c(mix(mid, hot, 0.35)), "blend": "additive", "fade_in": 0.04, "fade_out": 0.12,
        "emissive": rune_emissive,
        "opacity": tr([(0.0, 0.0), (0.12, 0.5), (0.35, 0.95), (1.85, 0.95), (2.15, 0.6), (2.45, 0.0)]),
    }, {"texture": "tex_rune_rings", "material": "mat_rune"}, parent="anchor", layer="ground"))
    add(node("d_glyphs", "decal", {
        "shape": "circle", "size": rune_size, "position": [0.0, 0.006, 0.0],
        "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (DURATION, [0.0, -75.0, 0.0])]),
        "color": c(hot), "blend": "additive", "fade_in": 0.05, "fade_out": 0.12,
        "emissive": rune_emissive,
        "opacity": tr([(0.0, 0.0), (0.1, 0.0), (0.4, 0.85), (1.85, 0.85), (2.1, 0.5), (2.4, 0.0)]),
    }, {"texture": "tex_rune_glyphs", "material": "mat_rune"}, parent="anchor", layer="ground"))

    add(node("d_wave", "decal", {
        "shape": "circle", "size": tr([(0.0, [R * 0.9, R * 0.9]), (0.97, [R * 0.9, R * 0.9]), (1.35, [R * 2.3, R * 2.3])]),
        "position": [0.0, 0.007, 0.0], "color": c(hot), "blend": "additive", "fade_in": 0.02, "fade_out": 0.1,
        "emissive": 1.2,
        "opacity": tr([(0.0, 0.0), (0.97, 0.0), (1.02, 0.9), (1.15, 0.55), (1.35, 0.0)]),
    }, {"texture": "tex_wave", "material": "mat_rune"}, parent="anchor", layer="ground"))

    # ================= GATHER: light runs in over the ground and curls up the body =================
    add(node("ps_gather", "particle_system", {
        "max_particles": 120, "lifetime": 0.6, "lifetime_variance": 0.15,
        "size": 0.05, "size_variance": 0.015,
        "size_over_life": curve([(0.0, 0.4), (0.25, 1.0), (0.8, 0.8), (1.0, 0.2)]),
        "color": c(hot), "color_over_life": [[0.0, c(mid)], [0.6, c(hot)], [1.0, c(white)]],
        "opacity": 0.9, "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (0.75, 0.9), (1.0, 0.0)]),
        "emissive": 2.8, "drag": 0.6, "render_mode": "stretched_billboard", "velocity_stretch": 0.9,
        "blend": "additive", "soft_particle_distance": 0.03,
    }, {"sprite": "tex_glint", "material": "mat_mote", "forces": ["f_gather", "f_lift", "f_curl"]}))
    add(node("e_gather", "emitter", {
        "shape": "ring", "radius": 2.1, "inner_radius": 1.3, "position": [0.0, 0.06, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 20.0, "velocity": 0.35, "velocity_variance": 0.2,
        "radial_velocity": -1.6,
        "rate": tr([(0.0, 90.0), (0.08, 230.0), (0.45, 210.0), (0.7, 40.0), (0.85, 0.0)]),
        "start_time": 0.0, "duration": 0.9,
    }, {"particle": "ps_gather"}, parent="anchor", layer="gather"))

    # ================= ASCEND: energy ribbons with riding glyphs =================
    add(node("ps_glyph", "particle_system", {
        "max_particles": 40, "lifetime": 0.55, "lifetime_variance": 0.12,
        "size": 0.085, "size_variance": 0.025,
        "size_over_life": curve([(0.0, 0.3), (0.2, 1.0), (0.8, 0.95), (1.0, 0.5)]),
        "color": c(hot), "color_over_life": [[0.0, c(white)], [0.5, c(hot)], [1.0, c(mid)]],
        "opacity": 0.9, "opacity_over_life": curve([(0.0, 0.0), (0.18, 1.0), (0.65, 0.8), (1.0, 0.0)]),
        "emissive": 1.7, "drag": 1.5, "render_mode": "billboard", "blend": "additive",
        "rotation_variance": 30.0, "sprite_fps": 8.0, "soft_particle_distance": 0.04,
    }, {"sprite": "tex_glyph", "material": "mat_glyph", "forces": ["f_lift"]}))
    add(node("ps_shed", "particle_system", {
        "max_particles": 60, "lifetime": 0.4, "lifetime_variance": 0.1,
        "size": 0.03, "size_variance": 0.01,
        "size_over_life": curve([(0.0, 0.6), (0.25, 1.0), (1.0, 0.4)]),
        "color": c(hot), "color_over_life": [[0.0, c(white)], [1.0, c(mid)]],
        "opacity": 0.9, "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.6, 0.7), (1.0, 0.0)]),
        "emissive": 2.2, "drag": 1.4, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.04,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_curl", "f_lift"]}))

    for s in RIBBONS:
        sid = s["id"]
        layer = "ribbons"
        add(node(f"tilt_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
            "rotation": [s["lean"][0], 0.0, s["lean"][1]],
        }, parent="anchor", layer=layer))
        rot, pos = flight_tracks(s)
        add(node(f"spin_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, "rotation": rot,
        }, parent=f"tilt_{sid}", layer=layer))
        add(node(f"bead_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, "position": pos,
        }, parent=f"spin_{sid}", layer=layer))
        add(node(f"glow_{sid}", "trail", {
            "lifetime": TRAIL_LIFE, "max_segments": 200, "min_vertex_distance": 0.025,
            "taper": curve([(0.0, 0.0), (0.12, 0.8), (0.3, 1.0), (0.55, 0.65), (0.8, 0.25), (1.0, 0.0)]),
            "opacity_over_life": RIPPLE_GLOW,
            "color": colour_envelope(s, mid), "blend": "additive", "noise_amplitude": 0.0,
            "width": envelope(s, round(GLOW_WIDTH * (0.7 + 0.3 * s["gain"]), 4)),
            "emissive": envelope(s, round(GLOW_EMISSIVE * s["gain"], 4), "glow"),
        }, {"source": f"bead_{sid}", "material": "mat_ribbon_glow"}, layer=layer))
        add(node(f"core_{sid}", "trail", {
            "lifetime": TRAIL_LIFE, "max_segments": 200, "min_vertex_distance": 0.025,
            "taper": curve([(0.0, 0.0), (0.1, 0.85), (0.26, 1.0), (0.5, 0.55), (0.78, 0.18), (1.0, 0.0)]),
            "opacity_over_life": RIPPLE_CORE,
            "color": colour_envelope(s, hot), "blend": "additive", "noise_amplitude": 0.0,
            "width": envelope(s, round(CORE_WIDTH * (0.7 + 0.3 * s["gain"]), 4)),
            "emissive": envelope(s, round(CORE_EMISSIVE * s["gain"], 4), "glow"),
        }, {"source": f"bead_{sid}", "material": "mat_ribbon_core"}, layer=layer))
        add(node(f"e_shed_{sid}", "emitter", {
            "shape": "point", "direction": [0.0, 0.0, 0.0], "velocity": 0.12, "velocity_variance": 0.06,
            "inherit_velocity": 0.25, "rate": envelope(s, round(22.0 * s["gain"], 3), "glow"),
        }, {"particle": "ps_shed"}, parent=f"bead_{sid}", layer=layer))
        add(node(f"e_glyph_{sid}", "emitter", {
            "shape": "point", "direction": [0.0, 0.0, 0.0], "velocity": 0.08, "velocity_variance": 0.04,
            "inherit_velocity": 0.15, "rate": envelope(s, round(9.0 * s["gain"], 3), "glow"),
        }, {"particle": "ps_glyph"}, parent=f"bead_{sid}", layer=layer))

    # ================= ASCEND: soft light shafts rising out of the rune =================
    add(node("ps_shaft", "particle_system", {
        "max_particles": 60, "lifetime": 0.55, "lifetime_variance": 0.15,
        "size": 0.12, "size_variance": 0.04,
        "size_over_life": curve([(0.0, 0.5), (0.3, 1.0), (1.0, 0.7)]),
        "color": c(hot), "color_over_life": [[0.0, c(white)], [0.5, c(hot)], [1.0, c(mix(mid, hot, 0.5))]],
        "opacity": 0.5, "opacity_over_life": curve([(0.0, 0.0), (0.25, 1.0), (0.6, 0.7), (1.0, 0.0)]),
        "emissive": 0.9, "drag": 0.9, "render_mode": "stretched_billboard", "velocity_stretch": 4.2,
        "blend": "additive", "soft_particle_distance": 0.1,
    }, {"sprite": "tex_shaft", "material": "mat_shaft"}))
    add(node("e_shaft", "emitter", {
        "shape": "ring", "radius": 0.92, "inner_radius": 0.3, "position": [0.0, 0.15, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 4.0, "velocity": 3.0, "velocity_variance": 0.9,
        "rate": tr([(0.0, 0.0), (0.12, 0.0), (0.2, 24.0), (0.5, 34.0), (1.05, 44.0), (1.4, 18.0), (1.7, 0.0)]),
        "start_time": 0.12, "duration": 1.63,
    }, {"particle": "ps_shaft"}, parent="anchor", layer="shafts"))

    add(node("ps_shaft_cool", "particle_system", {
        "max_particles": 30, "lifetime": 0.55, "lifetime_variance": 0.15,
        "size": 0.1, "size_variance": 0.04,
        "size_over_life": curve([(0.0, 0.5), (0.3, 1.0), (1.0, 0.7)]),
        "color": c(ice), "color_over_life": [[0.0, c(frost)], [0.5, c(mix(frost, ice, 0.5))], [1.0, c(ice)]],
        "opacity": 0.5, "opacity_over_life": curve([(0.0, 0.0), (0.25, 1.0), (0.6, 0.7), (1.0, 0.0)]),
        "emissive": 0.9, "drag": 0.9, "render_mode": "stretched_billboard", "velocity_stretch": 4.2,
        "blend": "additive", "soft_particle_distance": 0.1,
    }, {"sprite": "tex_shaft", "material": "mat_shaft_cool"}))
    add(node("e_shaft_cool", "emitter", {
        "shape": "ring", "radius": 0.8, "inner_radius": 0.25, "position": [0.0, 0.15, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 4.0, "velocity": 3.2, "velocity_variance": 0.9,
        "rate": tr([(0.0, 0.0), (0.12, 0.0), (0.25, 8.0), (0.5, 12.0), (1.05, 18.0), (1.4, 6.0), (1.7, 0.0)]),
        "start_time": 0.12, "duration": 1.63,
    }, {"particle": "ps_shaft_cool"}, parent="anchor", layer="shafts"))

    # ================= ASCEND: small blue crystal shards float up among the debris =================
    add(node("ps_shards", "particle_system", {
        "max_particles": 12, "lifetime": 1.5, "lifetime_variance": 0.2,
        "size": 0.13, "size_variance": 0.04,
        "size_over_life": curve([(0.0, 0.1), (0.12, 1.05), (0.2, 1.0), (0.8, 0.9), (1.0, 0.0)]),
        "color": c(ice), "color_over_life": [[0.0, c(frost)], [0.4, c(ice)], [1.0, c(ice)]],
        "opacity": 0.9, "opacity_over_life": curve([(0.0, 0.0), (0.1, 1.0), (0.75, 0.8), (1.0, 0.0)]),
        "emissive": 1.2, "drag": 1.4, "render_mode": "billboard", "blend": "additive",
        "rotation_variance": 12.0, "soft_particle_distance": 0.04,
    }, {"sprite": "tex_crystal", "material": "mat_crystal", "forces": ["f_float", "f_curl"]}))
    add(node("e_shards", "emitter", {
        "shape": "ring", "radius": 1.1, "inner_radius": 0.6, "position": [0.0, 0.5, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 25.0, "velocity": 1.6, "velocity_variance": 0.5,
        "rate": 0.0, "burst_count": 9, "burst_times": [0.5],
    }, {"particle": "ps_shards"}, parent="anchor", layer="core"))

    # ================= ASCEND: stone fragments lift off and float up =================
    add(node("rock_mesh", "mesh", {"primitive": "rock", "radius": 0.5, "segments": 12, "irregularity": 0.72,
                                   "variants": 8, "visible": False}))
    add(node("ps_debris", "particle_system", {
        "max_particles": 14, "lifetime": 1.75, "lifetime_variance": 0.2,
        "size": 0.2, "size_variance": 0.08,
        "size_over_life": curve([(0.0, 0.2), (0.08, 1.0), (0.7, 0.95), (0.9, 0.6), (1.0, 0.0)]),
        "angular_velocity": 70.0, "angular_velocity_variance": 60.0,
        "render_mode": "mesh", "blend": "alpha", "drag": 1.6, "orientation": "tumble",
        "mesh_scale": [1.05, 0.8, 1.0], "mesh_scale_variance": [0.35, 0.3, 0.35],
    }, {"mesh": "rock_mesh", "material": "mat_rock", "forces": ["f_float", "f_curl"]}))
    add(node("e_debris", "emitter", {
        "shape": "ring", "radius": 1.05, "inner_radius": 0.45, "position": [0.0, 0.04, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 18.0, "velocity": 2.4, "velocity_variance": 0.8,
        "rate": 0.0, "burst_count": 12, "burst_times": [0.45],
    }, {"particle": "ps_debris"}, parent="anchor", layer="debris"))
    add(node("ps_dust", "particle_system", {
        "max_particles": 40, "lifetime": 0.7, "lifetime_variance": 0.2,
        "size": 0.05, "size_variance": 0.02,
        "size_over_life": curve([(0.0, 0.5), (0.3, 1.0), (1.0, 0.3)]),
        "color": c(hot), "color_over_life": [[0.0, c(hot)], [1.0, c(deep)]],
        "opacity": 0.8, "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (1.0, 0.0)]),
        "emissive": 1.6, "drag": 1.2, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.03,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_lift", "f_curl"]}))
    add(node("e_dust", "emitter", {
        "shape": "ring", "radius": 1.05, "inner_radius": 0.45, "position": [0.0, 0.04, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 35.0, "velocity": 1.0, "velocity_variance": 0.5,
        "rate": 0.0, "burst_count": 30, "burst_times": [0.45],
    }, {"particle": "ps_dust"}, parent="anchor", layer="debris"))

    # ================= CULMINATE: a soft column of light up through the body =================
    # a soft, slightly irregular column: a few big stretched spindles rising fast out of the feet
    add(node("ps_column", "particle_system", {
        "max_particles": 40, "lifetime": 0.5, "lifetime_variance": 0.12,
        "size": 0.34, "size_variance": 0.1,
        "size_over_life": curve([(0.0, 0.5), (0.3, 1.0), (1.0, 0.75)]),
        "color": c(frost), "color_over_life": [[0.0, c(frost)], [0.5, c(mix(frost, ice, 0.4))], [1.0, c(ice)]],
        "opacity": 0.16, "opacity_over_life": curve([(0.0, 0.0), (0.25, 1.0), (0.6, 0.75), (1.0, 0.0)]),
        "emissive": 0.55, "drag": 0.4, "render_mode": "stretched_billboard", "velocity_stretch": 0.55,
        "blend": "additive", "soft_particle_distance": 0.2,
    }, {"sprite": "tex_shaft", "material": "mat_shaft"}))
    add(node("e_column", "emitter", {
        "shape": "disc", "radius": 0.12,
        "position": tr([(0.0, [0.0, 0.2, 0.0]), (1.12, [0.0, 0.2, 0.0]), (1.7, [0.0, 1.7, 0.0])]),
        "direction": [0.0, 1.0, 0.0], "spread": 2.0, "velocity": 4.2, "velocity_variance": 1.0,
        "rate": tr([(0.0, 0.0), (0.9, 0.0), (0.95, 60.0), (1.12, 42.0), (1.3, 10.0), (1.5, 0.0)]),
        "start_time": 0.9, "duration": 1.0,
    }, {"particle": "ps_column"}, parent="anchor", layer="column"))
    add(node("ps_stream", "particle_system", {
        "max_particles": 60, "lifetime": 0.4, "lifetime_variance": 0.1,
        "size": 0.05, "size_variance": 0.02,
        "size_over_life": curve([(0.0, 0.5), (0.3, 1.0), (1.0, 0.4)]),
        "color": c(frost), "color_over_life": [[0.0, c(white)], [0.4, c(frost)], [1.0, c(mix(frost, ice, 0.6))]],
        "opacity": 0.85, "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (0.7, 0.8), (1.0, 0.0)]),
        "emissive": 1.8, "drag": 0.2, "render_mode": "stretched_billboard", "velocity_stretch": 0.55,
        "blend": "additive", "soft_particle_distance": 0.05,
    }, {"sprite": "tex_shaft", "material": "mat_shaft"}))
    add(node("e_stream", "emitter", {
        "shape": "disc", "radius": 0.22, "position": [0.0, 0.2, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 3.0, "velocity": 6.5, "velocity_variance": 1.5,
        "rate": tr([(0.0, 0.0), (0.9, 0.0), (0.98, 110.0), (1.2, 60.0), (1.45, 12.0), (1.6, 0.0)]),
        "start_time": 0.9, "duration": 0.85,
    }, {"particle": "ps_stream"}, parent="anchor", layer="column"))

    # ================= CULMINATE: the level core =================
    core_pos = [0.0, CORE_Y, 0.0]
    add(node("ps_core_glow", "particle_system", {
        "max_particles": 2, "lifetime": 1.2, "size": 1.3,
        "size_over_life": curve([(0.0, 0.2), (0.08, 1.15), (0.2, 0.9), (0.6, 0.8), (1.0, 0.5)]),
        "color": c(mid), "opacity": 0.5,
        "opacity_over_life": curve([(0.0, 0.0), (0.06, 1.0), (0.25, 0.6), (0.7, 0.4), (1.0, 0.0)]),
        "emissive": 0.7, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.1,
    }, {"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_core_glow", "emitter", {
        "shape": "point", "position": core_pos, "direction": [0.0, 1.0, 0.0], "velocity": 0.12,
        "rate": 0.0, "burst_count": 1, "burst_times": [0.98],
    }, {"particle": "ps_core_glow"}, parent="anchor", layer="core"))
    add(node("ps_crystal", "particle_system", {
        "max_particles": 2, "lifetime": 1.1, "size": 0.62,
        "size_over_life": curve([(0.0, 0.05), (0.07, 1.12), (0.14, 1.0), (0.7, 0.95), (1.0, 0.55)]),
        "color": c(frost), "color_over_life": [[0.0, c(frost)], [0.3, c(mix(frost, ice, 0.45))], [1.0, c(ice)]],
        "opacity": 0.95, "opacity_over_life": curve([(0.0, 0.0), (0.05, 1.0), (0.6, 0.85), (1.0, 0.0)]),
        "emissive": 1.3, "emissive_over_life": curve([(0.0, 2.0), (0.15, 1.15), (1.0, 0.8)]),
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.05,
    }, {"sprite": "tex_crystal", "material": "mat_crystal"}))
    add(node("e_crystal", "emitter", {
        "shape": "point", "position": core_pos, "direction": [0.0, 1.0, 0.0], "velocity": 0.12,
        "rate": 0.0, "burst_count": 1, "burst_times": [1.0],
    }, {"particle": "ps_crystal"}, parent="anchor", layer="core"))
    # many short irregular rays: a swarm of stretched streaks from a small shell (never a star texture)
    add(node("ps_rays", "particle_system", {
        "max_particles": 60, "lifetime": 0.28, "lifetime_variance": 0.1,
        "size": 0.035, "size_variance": 0.015,
        "size_over_life": curve([(0.0, 0.6), (0.3, 1.0), (1.0, 0.3)]),
        "color": c(hot), "color_over_life": [[0.0, c(white)], [1.0, c(mid)]],
        "opacity": 0.85, "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (1.0, 0.0)]),
        "emissive": 2.2, "drag": 2.0, "render_mode": "stretched_billboard", "velocity_stretch": 2.6,
        "blend": "additive", "soft_particle_distance": 0.05,
    }, {"sprite": "tex_shaft", "material": "mat_shaft"}))
    add(node("e_rays", "emitter", {
        "shape": "sphere", "radius": 0.12, "surface_only": True, "position": core_pos,
        "direction": [0.0, 0.0, 0.0], "velocity": 2.1, "velocity_variance": 0.6,
        "rate": tr([(0.0, 0.0), (0.98, 0.0), (1.0, 220.0), (1.12, 90.0), (1.3, 20.0), (1.45, 0.0)]),
        "burst_count": 36, "burst_times": [0.02], "start_time": 0.98, "duration": 0.5,
    }, {"particle": "ps_rays"}, parent="anchor", layer="core"))
    add(node("ps_sparkle", "particle_system", {
        "max_particles": 60, "lifetime": 0.8, "lifetime_variance": 0.25,
        "size": 0.04, "size_variance": 0.015,
        "size_over_life": curve([(0.0, 0.4), (0.2, 1.0), (1.0, 0.3)]),
        "color": c(hot), "color_over_life": [[0.0, c(white)], [0.5, c(hot)], [1.0, c(deep)]],
        "opacity": 0.9, "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.6, 0.6), (1.0, 0.0)]),
        "emissive": 2.0, "drag": 1.8, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.03,
    }, {"sprite": "tex_glint", "material": "mat_mote", "forces": ["f_curl"]}))
    add(node("e_sparkle", "emitter", {
        "shape": "sphere", "radius": 0.3, "surface_only": True, "position": core_pos,
        "direction": [0.0, 0.0, 0.0], "velocity": 1.3, "velocity_variance": 0.6,
        "rate": tr([(0.0, 0.0), (0.98, 0.0), (1.02, 70.0), (1.5, 30.0), (1.85, 0.0)]),
        "start_time": 0.98, "duration": 0.9,
    }, {"particle": "ps_sparkle"}, parent="anchor", layer="core"))

    # ================= motes that rise through every beat and linger =================
    add(node("ps_motes", "particle_system", {
        "max_particles": 80, "lifetime": 1.0, "lifetime_variance": 0.3,
        "size": 0.045, "size_variance": 0.018,
        "size_over_life": curve([(0.0, 0.5), (0.2, 1.0), (0.8, 0.85), (1.0, 0.3)]),
        "color": c(hot), "color_over_life": [[0.0, c(white)], [0.5, c(hot)], [1.0, c(deep)]],
        "opacity": 0.85, "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.5, 0.65), (0.75, 0.85), (1.0, 0.0)]),
        "emissive": 1.8, "drag": 0.6, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.04,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_curl", "f_lift"]}))
    add(node("e_motes", "emitter", {
        "shape": "sphere", "radius": 0.55, "surface_only": True, "position": [0.0, 1.0, 0.0],
        "scale": [1.0, 1.7, 1.0], "direction": [0.0, 1.0, 0.0], "spread": 15.0,
        "velocity": 0.5, "velocity_variance": 0.2,
        "rate": tr([(0.0, 8.0), (0.35, 22.0), (1.0, 45.0), (1.6, 35.0), (2.1, 12.0), (2.3, 0.0), (DURATION, 0.0)]),
    }, {"particle": "ps_motes"}, parent="anchor", layer="motes"))

    # ================= light =================
    add(node("l_glow", "light", {
        "light_type": "point", "position": [0.0, 1.3, 0.0], "color": c(mid), "radius": 4.0,
        "intensity": tr([(0.0, 0.0), (0.4, 0.6), (0.95, 1.0), (1.02, 2.6), (1.25, 1.3), (1.8, 0.7),
                         (2.3, 0.0), (DURATION, 0.0)]),
    }, parent="anchor", layer="light"))

    add(node("l_core", "light", {
        "light_type": "point", "position": [0.0, CORE_Y, 0.0], "color": c(ice), "radius": 3.0,
        "intensity": tr([(0.0, 0.0), (0.98, 0.0), (1.02, 1.6), (1.3, 0.8), (1.9, 0.0), (DURATION, 0.0)]),
    }, parent="anchor", layer="light"))

    # ---------------- controls ----------------
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

    trails = [f"{k}_{s['id']}" for s in RIBBONS for k in ("glow", "core")]
    sprite_systems = ["ps_column", "ps_gather", "ps_glyph", "ps_shed", "ps_shaft", "ps_dust", "ps_stream", "ps_core_glow",
                      "ps_crystal", "ps_rays", "ps_sparkle", "ps_motes", "ps_shaft_cool", "ps_shards"]
    decals = ["d_pool", "d_rune", "d_glyphs", "d_wave"]
    controls = [
        control("intensity", "Intensity", "Global", 0.0, 3.0,
                [mul(t, "emissive") for t in trails]
                + [mul(ps, "emissive") for ps in sprite_systems]
                + [mul(d, "emissive") for d in decals] + [mul("l_glow", "intensity"), mul("l_core", "intensity")]),
        control("burst_size", "Burst size", "Level core", 0.3, 2.5,
                [mul("ps_crystal", "size"), mul("ps_core_glow", "size"), mul("ps_column", "size"),
                 mul("e_rays", "velocity"), mul("e_sparkle", "velocity"), mul("l_glow", "radius"), mul("l_core", "radius")]),
        control("ribbon_brightness", "Ribbon brightness", "Energy ribbons", 0.0, 3.0,
                [mul(t, "emissive") for t in trails] + [mul("ps_glyph", "emissive"), mul("ps_shed", "emissive")]),
        control("debris_amount", "Debris amount", "Floating debris", 0.0, 1.25,
                [mul("e_debris", "burst_count"), mul("e_dust", "burst_count"), mul("e_shards", "burst_count")]),
        control("rune_brightness", "Rune brightness", "Ground rune", 0.0, 3.0,
                [mul(d, "emissive") for d in decals]),
        control("character_height", "Character height", "Global", 0.5, 2.0,
                [mul("anchor", "scale")] + [mul(t, "width") for t in trails]
                + [mul(d, "size") for d in decals]
                + [mul(ps, "size") for ps in sprite_systems + ["ps_debris"]]
                + [mul(e, "velocity") for e in ("e_shaft", "e_shaft_cool", "e_stream", "e_debris", "e_dust", "e_motes", "e_shards")]
                + [mul("l_glow", "radius"), mul("l_core", "radius")]),
        control("hue", "Energy hue", "Global", -180.0, 180.0, colour_bindings, "deg", 0.0, 1.0),
        control("global_speed", "Speed", "Global", 0.25, 4.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}], step=0.05),
    ]

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": "A grand, short level-up celebration in holy gold with icy blue accents: light and runes gather from the ground, "
                       "energy ribbons and stone fragments rise, a crystalline level core bursts into being above "
                       "the head, then everything settles and the ground rune expands and fades. 2.2 s plus "
                       "lingering motes; origin at the character's feet (about 1.8 m tall).",
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [
            {"name": "gather", "start": GATHER, "end": ASCEND},
            {"name": "ascend", "start": ASCEND, "end": CULMINATE},
            {"name": "culminate", "start": CULMINATE, "end": EMPOWER},
            {"name": "empower", "start": EMPOWER, "end": COMPLETE},
            {"name": "complete", "start": COMPLETE, "end": DURATION},
        ]},
        "layers": [
            {"id": "ground", "name": "Ground rune", "role": "telegraph"},
            {"id": "gather", "name": "Gathering light", "role": "ignition"},
            {"id": "ribbons", "name": "Energy ribbons", "role": "primary"},
            {"id": "shafts", "name": "Light shafts", "role": "secondary"},
            {"id": "debris", "name": "Floating debris", "role": "secondary"},
            {"id": "column", "name": "Light column", "role": "primary"},
            {"id": "core", "name": "Level core", "role": "primary"},
            {"id": "motes", "name": "Motes", "role": "aftermath"},
            {"id": "light", "name": "Light", "role": "secondary"},
        ],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/level_up.py",
            "theme": "holy",
            "category": "support",
            "attachment": {
                "origin": [0.0, 0.0, 0.0], "origin_node": "anchor", "character_height": 1.8,
                "body_radius": 0.45,
                "notes": "Attach the `anchor` node (the effect origin) at the character's feet, y = 0. Nothing "
                         "is drawn for the character. The level core peaks about 2.75 m above the feet. Scale "
                         "for bigger or smaller characters with the Character height control.",
            },
            "analysis": "gather 0-0.4 the ground rune draws in and lights, streaks of light run in over the ground "
                        "and curl up | ascend 0.4-0.95 three ribbons spiral up the body shedding glyph plates, "
                        "soft shafts rise from the rune, stone fragments lift and tumble | culminate 0.95-1.35 a "
                        "soft column climbs through the body into the level core, a faceted diamond of light "
                        "with a radial glow and a spray of irregular rays, the chest light flashes | empower "
                        "1.35-1.85 ribbons dissolve at the top, the column thins upward, the core fades, debris "
                        "drifts and shrinks | complete 1.85-2.6 the ground rune expands and fades, motes linger",
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.05,
                                "bloom_intensity": 0.28, "bloom_radius": 0.05, "exposure": 0.95, "grid": False},
            "tags": TAGS,
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write level_up.json into")
    parser.add_argument("--check", help="directory whose level_up.json must match the generator output byte for byte")
    args = parser.parse_args()
    if args.check:
        path = Path(args.check) / f"{SLUG}.json"
        same = path.exists() and path.read_text(encoding="utf-8") == render(build())
        print(f"{path}: {'identical' if same else 'DIFFERENT'}")
        return 0 if same else 1
    out = Path(args.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    effect = build()
    path = out / f"{SLUG}.json"
    path.write_text(render(effect), encoding="utf-8")
    print(f"{path}  ({len(effect['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
