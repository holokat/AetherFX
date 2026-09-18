#!/usr/bin/env python3
"""Generate the Resurrect effect (a graceful holy revive played on a fallen ally) from one parametrised description.

    python tools/generators/resurrect.py --out examples/effects
    python tools/generators/resurrect.py --check examples/effects

"Life finds a way": light, feathers and spiritual energy come DOWN from the heavens onto the fallen ally,
lift the soul out of the body, return it, and restore the body with a gentle warm burst. The origin is the
ground at the centre of the fallen body, y = 0; the body lies along X (about 1.8 m long) and is never
drawn. The look is authored for a character radius of about 0.33 m (the beam is twice that), and the
Character size control scales it. Pearly ivory and white-gold with warm gold edges, no cool accents; every
colour comes from the PALETTE table, so the Energy hue control (a hue shift bound to every colour)
re-themes it in one move.

It is deliberately the opposite of Level Up (tools/generators/level_up.py): the light descends instead of
rising from the feet, halo rings high above replace the ground rune and the crystal core, drifting
feathers replace stone debris and shards, and a pearly soul that leaves and re-enters the body replaces
the energy ribbons around a standing character.

Beats (the sheet's row)
-----
1. CHANNEL     0-0.7 s    three thin halo rings (a sigil of concentric arcs, no cross) form about 3.2 m
                          above the body and turn slowly; light motes and the first feathers drift down;
                          a faint pool of light gathers on the ground under the body.
2. SOUL RISES  0.7-1.6 s  a soft beam descends from the top of the frame onto the body (radius ~0.65 m,
                          tapered, soft layered bands with a slow downward flow and falling light
                          streaks); a translucent human soul materialises out of the lying body, lifts
                          and turns upright, and hovers with its feet about 0.5 m off the ground, gently
                          floating and swaying: a glowing rim round a seamless human silhouette, a milky
                          inside with a brighter heart and head, legs dissolving into mist, shedding
                          motes and small rising wisps, a trail of mist streaming from its feet back down
                          to the body, wrapped by a slow soft spiral ribbon.
3. RECONVERGE  1.6-2.2 s  the soul sinks back down along the beam and lies back into the body; the halo
                          rings descend and widen after it; the beam's foot brightens as they rejoin.
4. RESTORE     2.2-2.6 s  a gentle warm burst of light at the body, a halo ring expands outward along the
                          ground, a flight of feathers is released upward.
5. COMPLETE    2.6-3.5 s  the beam lifts away upward and thins, the halos fade, feathers and motes drift
                          down and fade.

Techniques: halo arcs and soul ribbons are trails behind invisible beads on keyframed parent chains
(tools/generators/power_up.py: rotation is linear between keys, so arcs are exact circles); the beam is
three glow-only `beam` bands (no white core line) whose lower end is keyframed down from the sky and back
up; the soul's body is a `shape` texture graph (the union of head, neck, torso, arms and tapering legs, lit
by a rim hugging only its outer silhouette) drawn as a camera-facing sprite that is re-emitted every frame
from a keyframed rig: a `stretched_billboard` with zero stretch aligns its head with the emitter's rotated
+Y, so the figure lies, turns upright and lies back down with the rig (particles are world-space and cannot
be parented). A 3D figure of mesh primitives was tried first and rejected: every seam between parts shows
as a rim line and it read as a glass mannequin. The feather is a `shape` texture graph (asymmetric vane, soft central shaft). House style: no
crosses, plus signs or four-armed stars; nothing hard-edged; the output is deterministic.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# palette - every colour in the effect comes from here (warm only: no blue, that is Level Up's accent)
# ---------------------------------------------------------------------------

PALETTE = {
    "ivory": [1.00, 0.95, 0.86],   # pearly white-gold cores (warm, never pure white)
    "pearl": [0.96, 0.90, 0.84],   # the soul's body
    "gold": [1.00, 0.80, 0.50],    # the light: beam, halos
    "amber": [1.00, 0.62, 0.28],   # warm gold edges
    "deep": [0.74, 0.38, 0.12],    # falloff, dying motes
}

SLUG = "resurrect"
NAME = "Resurrect"
SEED = 30317
TAGS = ["resurrect", "revive", "support", "holy", "gold", "target", "soul", "feathers"]

DURATION = 3.5
CHANNEL, SOUL, RECONVERGE, RESTORE, COMPLETE = 0.0, 0.7, 1.6, 2.2, 2.6

BEAM_TOP = 9.0          # the beam's upper end, well above the frame
BEAM_W = 1.3            # beam width = 2 x a 0.65 m radius (character radius x 2)
HALO_Y = 3.25           # the sigil's height above the body

# soul path: (time, pelvis height, roll about z in degrees: 90 = lying along X, 0 = upright)
SOUL_KEYS = [
    (0.70, 0.22, 90.0), (0.9, 0.5, 62.0), (1.1, 0.95, 20.0), (1.35, 1.34, 2.0), (1.6, 1.42, 0.0),
    (1.8, 1.28, 3.0), (1.98, 0.84, 24.0), (2.12, 0.42, 68.0), (2.25, 0.22, 90.0),
]

# halo rings: radius, height offset, arcs, turn rate (rev/s), trail life (s), brightness
HALOS = [
    {"id": "a", "radius": 0.36, "dy": 0.08, "arcs": 2, "rate": 0.22, "life": 0.85, "gain": 1.0},
    {"id": "c", "radius": 1.0, "dy": -0.04, "arcs": 3, "rate": -0.1, "life": 1.1, "gain": 0.8},
]

# soul ribbons: phase (deg), radius, turn rate (rev/s), brightness
SOUL_RIBBONS = [
    # one ribbon climbs with the rising soul, the other spirals down with it on the way back: never two at once
    {"id": "a", "phase": 20.0, "radius": 0.6, "rate": 1.2, "gain": 1.0, "t0": 0.95, "t1": 1.8, "up": True},
    {"id": "b", "phase": 200.0, "radius": 0.6, "rate": -1.3, "gain": 0.55, "t0": 1.55, "t1": 2.2, "up": False},
]

KEY_STEP = 0.04


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
        elif isinstance(v, list):
            v = [round(x, 5) if isinstance(x, float) else x for x in v]
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


def smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def sample(keys: list[tuple[float, ...]], t: float, idx: int) -> float:
    """Smoothstep interpolation of column `idx` of a (time, ...) key table."""
    if t <= keys[0][0]:
        return keys[0][idx]
    for a, b in zip(keys, keys[1:]):
        if t <= b[0]:
            u = smooth((t - a[0]) / (b[0] - a[0]))
            return a[idx] + (b[idx] - a[idx]) * u
    return keys[-1][idx]


def times(t0: float, t1: float, step: float = KEY_STEP) -> list[float]:
    n = max(1, int(math.ceil((t1 - t0) / step)))
    return [t0 + (t1 - t0) * i / n for i in range(n + 1)]


# ---------------------------------------------------------------------------
# texture graphs
# ---------------------------------------------------------------------------

def feather_graph() -> tuple[list[dict[str, Any]], str]:
    """A small glowing feather, upright in the tile (tip at the top): an asymmetric pointed vane - the
    intersection of two large discs, a fuller leading edge on the left and a flatter trailing edge on the
    right - lit brightest along a slightly curved central shaft and fading softly to its edges, a small
    split in the leading vane, and the bare quill running out below. Never an ellipse or a leaf blob."""
    # a lens between y = 0.08 and 0.84: the left edge bulges 0.13, the right edge 0.065
    half, mid = 0.38, 0.46
    sl, sr = 0.13, 0.065
    rl = (half * half + sl * sl) / (2.0 * sl)
    rr = (half * half + sr * sr) / (2.0 * sr)
    left = {"shape": "circle", "radius": round(rl, 4), "center": [round(0.5 - sl + rl, 4), mid]}
    right = {"shape": "circle", "radius": round(rr, 4), "center": [round(0.5 + sr - rr, 4), mid]}
    parts = [
        op("dl", "shape", {**left, "mode": "fill", "softness": 0.012}),
        op("dr", "shape", {**right, "mode": "fill", "softness": 0.012}),
        op("lens", "math", {"mode": "min"}, {"a": "dl", "b": "dr"}),
        # the split in the leading vane
        op("notch", "shape", {"shape": "circle", "radius": 0.07, "aspect": 0.2, "center": [0.4, 0.58],
                              "rotation": 55.0, "softness": 0.01}),
        op("vane", "math", {"mode": "subtract"}, {"a": "lens", "b": "notch"}),
        # light: brightest inside, falling off to a soft edge
        op("bl", "shape", {**left, "mode": "bevel", "bevel": 0.07}),
        op("br", "shape", {**right, "mode": "bevel", "bevel": 0.05}),
        op("bev", "math", {"mode": "min"}, {"a": "bl", "b": "br"}),
        op("bevl", "levels", {"out_low": 0.35, "out_high": 1.0, "gamma": 0.8}, {"a": "bev"}),
        op("body0", "math", {"mode": "multiply"}, {"a": "vane", "b": "bevl"}),
        # fade towards the base of the vane
        op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("gl", "levels", {"in_low": 0.25, "in_high": 0.9, "out_low": 1.0, "out_high": 0.45}, {"a": "g"}),
        op("body1", "math", {"mode": "multiply"}, {"a": "body0", "b": "gl"}),
        scaled("body", "body1", 0.62),
        # the shaft: an arc of a large circle, a little curved, from the tip past the vane
        op("sh", "shape", {"shape": "circle", "radius": 1.3, "center": [1.785, 0.5], "mode": "outline",
                           "outline_width": 0.014, "softness": 0.01}),
        op("shv", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("shm0", "levels", {"in_low": 0.07, "in_high": 0.12, "out_low": 0.0, "out_high": 1.0}, {"a": "shv"}),
        op("shm1", "levels", {"in_low": 0.9, "in_high": 0.96, "out_low": 1.0, "out_high": 0.0}, {"a": "shv"}),
        op("shm", "math", {"mode": "min"}, {"a": "shm0", "b": "shm1"}),
        op("shaft0", "math", {"mode": "multiply"}, {"a": "sh", "b": "shm"}),
        scaled("shaft", "shaft0", 0.95),
        op("all", "math", {"mode": "max"}, {"a": "body", "b": "shaft"}),
        op("b", "blur", {"radius": 0.6}, {"a": "all"}),
        op("lv", "levels", {"in_low": 0.02, "in_high": 0.95}, {"a": "b"}),
    ]
    return parts, "lv"


def soul_figure_graph() -> tuple[list[dict[str, Any]], str]:
    """The soul's body as one seamless silhouette, upright in the tile (head at the top): the union of a
    head, neck, chest, waist, two arms hanging 20-30 degrees out with the forearms a little more open, and
    two long legs tapering to nothing. Its light is a soft rim hugging the OUTER silhouette only (the union
    minus a blurred copy of itself, so no seams between parts), a faint milky inside, a brighter heart and
    head, and legs that fade into mist below the knee."""
    def ellipse(nid: str, cx: float, cy: float, r: float, aspect: float, rot: float = 0.0) -> dict[str, Any]:
        return op(nid, "shape", {"shape": "circle", "radius": r, "aspect": aspect, "center": [cx, cy],
                                 "rotation": rot, "softness": 0.006})
    parts = [
        ellipse("head", 0.5, 0.105, 0.052, 0.84),
        op("neck", "shape", {"shape": "rounded_box", "radius": 0.04, "aspect": 0.55, "center": [0.5, 0.165],
                             "corner_radius": 0.015, "softness": 0.006}),
        ellipse("chest", 0.5, 0.272, 0.118, 0.8),
        ellipse("waist", 0.5, 0.4, 0.105, 0.66),
        ellipse("hips", 0.5, 0.465, 0.07, 1.0),
        # shoulders: a soft rounded yoke the arms hang from
        op("yoke", "shape", {"shape": "rounded_box", "radius": 0.028, "aspect": 3.3, "center": [0.5, 0.205],
                             "corner_radius": 0.026, "softness": 0.006}),
    ]
    ids = ["head", "neck", "chest", "waist", "hips", "yoke"]
    for side, sgn in (("r", 1.0), ("l", -1.0)):
        sx, sy = 0.5 + sgn * 0.078, 0.212
        au, af = math.radians(21.0), math.radians(29.0)
        lu, lf = 0.078, 0.072
        ex, ey = sx + sgn * 2 * lu * math.sin(au), sy + 2 * lu * math.cos(au)
        parts += [
            op(f"ua{side}", "shape", {"shape": "rounded_box", "radius": lu + 0.014, "aspect": 0.27,
                                      "center": [round(sx + sgn * lu * math.sin(au), 4), round(sy + lu * math.cos(au), 4)],
                                      "rotation": round(sgn * 21.0, 2), "corner_radius": 0.018, "softness": 0.006}),
            op(f"fa{side}", "shape", {"shape": "rounded_box", "radius": lf + 0.012, "aspect": 0.23,
                                      "center": [round(ex + sgn * lf * math.sin(af), 4), round(ey + lf * math.cos(af), 4)],
                                      "rotation": round(sgn * 29.0, 2), "corner_radius": 0.015, "softness": 0.006}),
            ellipse(f"lg{side}", 0.5 + sgn * 0.034, 0.7, 0.25, 0.14, sgn * -2.0),
        ]
        ids += [f"ua{side}", f"fa{side}", f"lg{side}"]
    folds, body = fold("u", ids, "max")
    parts += folds
    parts += [
        # rim: the silhouette minus a blurred copy of itself - bright on the outer contour only
        op("bl", "blur", {"radius": 3.0}, {"a": body}),
        op("bli", "invert", None, {"a": "bl"}),
        op("rim0", "math", {"mode": "multiply"}, {"a": body, "b": "bli"}),
        op("rim", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.9}, {"a": "rim0"}),
        op("wide", "blur", {"radius": 8.0}, {"a": body}),
        op("widei", "invert", None, {"a": "wide"}),
        op("inner0", "math", {"mode": "multiply"}, {"a": body, "b": "widei"}),
        scaled("inner", "inner0", 0.55),
        scaled("milk", body, 0.2),
        # a brighter heart and head
        op("heart", "gradient_radial", {"center": [0.5, 0.28], "radius": 0.14, "falloff": "smooth"}),
        op("heartm", "math", {"mode": "multiply"}, {"a": "heart", "b": body}),
        scaled("heartd", "heartm", 0.35),
        op("hd", "gradient_radial", {"center": [0.5, 0.105], "radius": 0.07, "falloff": "smooth"}),
        op("hdm", "math", {"mode": "multiply"}, {"a": "hd", "b": body}),
        scaled("hdd", "hdm", 0.3),
        op("s0", "math", {"mode": "add"}, {"a": "rim", "b": "inner"}),
        op("s1", "math", {"mode": "add"}, {"a": "s0", "b": "milk"}),
        op("s2", "math", {"mode": "add"}, {"a": "s1", "b": "heartd"}),
        op("s3", "math", {"mode": "add"}, {"a": "s2", "b": "hdd"}),
        # legs fade into mist below the knee, with a little noise so the fade is not a straight line
        op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("gl", "levels", {"in_low": 0.58, "in_high": 0.93, "out_low": 1.0, "out_high": 0.0}, {"a": "g"}),
        op("n", "fbm", {"frequency": 6.0, "octaves": 3, "seed": 31}),
        op("nl", "levels", {"in_low": 0.25, "in_high": 0.75, "out_low": 0.6, "out_high": 1.0}, {"a": "n"}),
        op("fade0", "math", {"mode": "multiply"}, {"a": "gl", "b": "nl"}),
        op("fade", "levels", {"gamma": 1.0}, {"a": "fade0"}),
        op("s4", "math", {"mode": "multiply"}, {"a": "s3", "b": "fade"}),
        op("soft", "blur", {"radius": 1.0}, {"a": "s4"}),
        op("out", "levels", {"in_low": 0.01, "in_high": 1.0}, {"a": "soft"}),
    ]
    return parts, "out"


def sigil_graph() -> tuple[list[dict[str, Any]], str]:
    """The faint halo sigil under the bright arcs: three hairline rings with a soft wash between them and
    a ring of small dots - rings, arcs and dots only (no spokes that could read as a cross)."""
    parts = [
        op("r0", "ring", {"radius": 0.47, "thickness": 0.006, "softness": 0.008}),
        op("r1", "ring", {"radius": 0.325, "thickness": 0.008, "softness": 0.01}),
        op("r2", "ring", {"radius": 0.17, "thickness": 0.006, "softness": 0.008}),
        op("wash", "ring", {"radius": 0.33, "thickness": 0.22, "softness": 0.2}),
        scaled("washd", "wash", 0.12),
        op("dots", "spokes", {"count": 24, "width": 0.012, "softness": 0.008, "inner_radius": 0.392,
                              "outer_radius": 0.408, "rotation": 7.5}),
        scaled("dotsd", "dots", 0.8),
    ]
    folds, out = fold("sm", ["r0", "r1", "r2", "washd", "dotsd"], "max")
    return parts + folds, out


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------

def soul_pos(t: float) -> tuple[float, float, float]:
    """The soul's pelvis: the key path plus a gentle float and sway while it is up."""
    up = smooth((t - 1.1) / 0.35) * smooth((1.95 - t) / 0.25)
    y = sample(SOUL_KEYS, t, 1) + 0.035 * math.sin(2.0 * math.pi * 0.55 * (t - 1.2)) * up
    sway = 0.03 * math.sin(2.0 * math.pi * 0.4 * (t - 0.9)) * up
    return sway, y, 0.0


def soul_roll(t: float) -> float:
    up = smooth((t - 1.1) / 0.35) * smooth((1.95 - t) / 0.25)
    return sample(SOUL_KEYS, t, 2) + 2.5 * math.sin(2.0 * math.pi * 0.4 * (t - 1.1)) * up


def build() -> dict[str, Any]:
    p = PALETTE
    ivory, pearl, gold, amber, deep = p["ivory"], p["pearl"], p["gold"], p["amber"], p["deep"]
    nodes: list[dict[str, Any]] = []
    add = nodes.append

    add(node("cam", "camera", {"position": [2.3, 1.2, 4.0], "target": [0.0, 1.75, 0.0], "fov": 42.0}))

    # ---------------- textures ----------------
    band = [op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
            op("gi", "invert", None, {"a": "g"}),
            op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"})]
    add(tex("tex_ribbon_core", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.42},
                                                 {"a": "t"})], "lv"))
    add(tex("tex_ribbon_glow", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.8},
                                                 {"a": "t"})], "lv"))
    add(tex("tex_mote", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"gamma": 0.8}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_glow", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        op("lv", "levels", {"gamma": 1.1}, {"a": "r"}),
    ], "lv"))
    # soft light streak / wisp spindle (tapers at both ends when stretched)
    add(tex("tex_streak", 32, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 3.0, "octaves": 3, "seed": 17}),
        op("nl", "levels", {"in_low": 0.2, "in_high": 0.8, "out_low": 0.8, "out_high": 1.0}, {"a": "n"}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
        op("lv", "levels", {"in_low": 0.02, "in_high": 0.9, "gamma": 1.5}, {"a": "m"}),
    ], "lv"))
    ft_nodes, ft_out = feather_graph()
    add(tex("tex_feather", 96, 96, ft_nodes, ft_out))
    sf_nodes, sf_out = soul_figure_graph()
    add(tex("tex_soul_figure", 256, 256, sf_nodes, sf_out))
    sg_nodes, sg_out = sigil_graph()
    add(tex("tex_sigil", 384, 384, sg_nodes, sg_out))
    add(tex("tex_pool", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"gamma": 1.5}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_wave", 256, 256, [
        op("r", "ring", {"radius": 0.44, "thickness": 0.018, "softness": 0.03}),
        op("r2", "ring", {"radius": 0.4, "thickness": 0.07, "softness": 0.08}),
        scaled("r2d", "r2", 0.35),
        op("m", "math", {"mode": "max"}, {"a": "r", "b": "r2d"}),
    ], "m"))
    # ---------------- materials ----------------
    def additive(mid_: str, rgb: list[float], ei: float, depth_fade: float = 0.08, **extra: Any) -> None:
        params = {"blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(rgb),
                  "emissive_intensity": ei, "soft_particle": True, "depth_fade": depth_fade}
        params.update(extra)
        add(node(mid_, "material", params))

    add(node("mat_ribbon_core", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(ivory),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.08, "double_sided": True,
    }, {"base_texture": "tex_ribbon_core"}))
    add(node("mat_ribbon_glow", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(gold),
        "emissive_intensity": 0.2, "soft_particle": True, "depth_fade": 0.12, "double_sided": True,
    }, {"base_texture": "tex_ribbon_glow"}))
    additive("mat_mote", ivory, 0.25, 0.06)
    additive("mat_streak", gold, 0.15, 0.2)
    additive("mat_glow", gold, 0.15, 0.3)
    additive("mat_feather", ivory, 0.25, 0.05)
    add(node("mat_beam", "material", {"blend": "additive", "emissive_color": c(gold), "emissive_intensity": 0.2,
                                      "soft_particle": True, "depth_fade": 0.3}))
    add(node("mat_decal", "material", {"blend": "additive", "emissive_color": c(gold), "emissive_intensity": 0.2,
                                       "soft_particle": False}))

    # ---------------- forces ----------------
    add(node("f_curl", "force", {"force_type": "curl_noise", "strength": 0.3, "frequency": 0.9, "speed": 0.25}))
    add(node("f_sink", "force", {"force_type": "gravity", "strength": 0.35, "direction": [0.0, -1.0, 0.0]}))
    add(node("f_feather", "force", {"force_type": "gravity", "strength": 0.55, "direction": [0.0, -1.0, 0.0]}))
    add(node("f_sway", "force", {"force_type": "curl_noise", "strength": 0.9, "frequency": 0.7, "speed": 0.35}))
    add(node("f_gather", "force", {"force_type": "attractor", "position": [0.0, 0.6, 0.0], "strength": 0.6,
                                   "radius": 4.0}))
    add(node("f_lift", "force", {"force_type": "buoyancy", "strength": 0.25}))
    add(node("ground", "collider", {"collider_type": "plane", "normal": [0.0, 1.0, 0.0], "position": [0.0, 0.0, 0.0],
                                    "kill_on_collision": True}))

    # ---------------- the attach point: the ground at the fallen body's centre ----------------
    add(node("anchor", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                "scale": [1.0, 1.0, 1.0]}))

    # ================= ground: the pool of light under the body =================
    add(node("d_pool", "decal", {
        "shape": "circle",
        "size": tr([(0.0, [1.6, 0.9]), (0.7, [2.5, 1.35]), (0.9, [2.9, 1.7]), (2.2, [3.1, 1.9]),
                    (2.6, [3.6, 2.3]), (3.1, [3.9, 2.5])]),
        "position": [0.0, 0.0, 0.0], "color": c(amber), "blend": "additive", "fade_in": 0.05, "fade_out": 0.2,
        "emissive": 0.5,
        "opacity": tr([(0.0, 0.0), (0.35, 0.12), (0.7, 0.22), (0.88, 0.42), (1.6, 0.4), (2.15, 0.55),
                       (2.3, 0.7), (2.7, 0.35), (3.15, 0.0)]),
    }, {"texture": "tex_pool", "material": "mat_decal"}, parent="anchor", layer="ground"))
    add(node("d_wave", "decal", {
        "shape": "circle", "size": tr([(0.0, [1.0, 1.0]), (2.22, [1.0, 1.0]), (2.95, [5.2, 5.2])]),
        "position": [0.0, 0.006, 0.0], "color": c(gold), "blend": "additive", "fade_in": 0.02, "fade_out": 0.1,
        "emissive": 1.0,
        "opacity": tr([(0.0, 0.0), (2.2, 0.0), (2.26, 0.85), (2.55, 0.5), (2.95, 0.0)]),
    }, {"texture": "tex_wave", "material": "mat_decal"}, parent="anchor", layer="restore"))

    # ================= CHANNEL: the halo sigil high above =================
    halo_keys = [(0.0, HALO_Y + 0.25), (0.6, HALO_Y), (1.75, HALO_Y - 0.05), (1.95, HALO_Y - 0.3),
                 (2.3, HALO_Y - 0.8), (2.8, HALO_Y - 1.05)]
    grow = [(0.0, 0.94), (0.6, 1.0), (1.75, 1.02), (2.3, 1.3), (2.8, 1.5)]

    def halo_scale(t: float) -> float:
        return sample(grow, t, 1)

    add(node("halo_rig", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                  "position": tr([(t, [0.0, round(sample(halo_keys, t, 1), 4), 0.0])
                                                  for t in times(0.0, 2.8, 0.1)])},
                  parent="anchor", layer="halos"))
    add(node("d_sigil", "decal", {
        "shape": "circle", "size": tr([(t, [2.3 * halo_scale(t), 2.3 * halo_scale(t)]) for t in
                                       (0.0, 0.3, 0.6, 1.75, 1.95, 2.3, 2.8)]),
        "position": [0.0, 0.0, 0.0],
        "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (DURATION, [0.0, 50.0, 0.0])]),
        "color": c(gold), "blend": "additive", "fade_in": 0.05, "fade_out": 0.1, "emissive": 0.8,
        "opacity": tr([(0.0, 0.0), (0.15, 0.2), (0.55, 0.6), (1.75, 0.6), (1.9, 0.7), (2.3, 0.5), (2.8, 0.0)]),
    }, {"texture": "tex_sigil", "material": "mat_decal"}, parent="halo_rig", layer="halos"))

    halo_trails: list[str] = []
    for h in HALOS:
        hid = h["id"]
        spin_deg = 360.0 * h["rate"] * DURATION
        add(node(f"halo_spin_{hid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
            "position": [0.0, h["dy"], 0.0],
            "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (DURATION, [0.0, round(spin_deg, 3), 0.0])]),
        }, parent="halo_rig", layer="halos"))
        for k in range(h["arcs"]):
            ang = 2.0 * math.pi * k / h["arcs"] + 0.4 * (ord(hid) - 97)
            ks = sorted(set([0.0, 0.3, 0.6, RECONVERGE, 1.75, 1.9, 2.1, 2.3, 2.55, 2.8, DURATION]))
            pos = tr([(t, [h["radius"] * halo_scale(t) * math.cos(ang), 0.0,
                           h["radius"] * halo_scale(t) * math.sin(ang)]) for t in ks])
            bid = f"halo_{hid}{k}"
            add(node(f"bead_{bid}", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4,
                                             "visible": False, "position": pos},
                     parent=f"halo_spin_{hid}", layer="halos"))
            g = h["gain"]
            env = [(0.0, 0.0), (0.12, 0.0), (0.55, 1.0), (1.5, 1.0), (1.72, 0.3), (1.85, 0.0), (DURATION, 0.0)]
            add(node(f"glow_{bid}", "trail", {
                "lifetime": h["life"], "max_segments": 160, "min_vertex_distance": 0.02,
                "taper": curve([(0.0, 0.0), (0.12, 0.85), (0.35, 1.0), (0.7, 0.7), (1.0, 0.0)]),
                "opacity_over_life": curve([(0.0, 1.0), (0.4, 0.9), (1.0, 0.0)]),
                "color": c(gold), "blend": "additive",
                "width": tr([(t, round(0.07 * v * (0.8 + 0.2 * g), 4)) for t, v in env]),
                "emissive": tr([(t, round(0.9 * g * v, 4)) for t, v in env]),
            }, {"source": f"bead_{bid}", "material": "mat_ribbon_glow"}, layer="halos"))
            if h["id"] == "c":
                halo_trails.append(f"glow_{bid}")
                continue
            add(node(f"core_{bid}", "trail", {
                "lifetime": h["life"], "max_segments": 160, "min_vertex_distance": 0.02,
                "taper": curve([(0.0, 0.0), (0.1, 0.9), (0.3, 1.0), (0.65, 0.6), (1.0, 0.0)]),
                "opacity_over_life": curve([(0.0, 1.0), (0.4, 0.9), (1.0, 0.0)]),
                "color": c(ivory), "blend": "additive",
                "width": tr([(t, round(0.014 * v, 4)) for t, v in env]),
                "emissive": tr([(t, round(1.6 * g * v, 4)) for t, v in env]),
            }, {"source": f"bead_{bid}", "material": "mat_ribbon_core"}, layer="halos"))
            halo_trails += [f"glow_{bid}", f"core_{bid}"]

    # a soft glow at the heart of the sigil
    add(node("ps_halo_glow", "particle_system", {
        "max_particles": 2, "lifetime": 2.6, "size": 1.1,
        "size_over_life": curve([(0.0, 0.3), (0.2, 1.0), (0.7, 1.05), (1.0, 1.2)]),
        "color": c(gold), "opacity": 0.28,
        "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.65, 0.8), (1.0, 0.0)]),
        "emissive": 0.6, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.1,
    }, {"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_halo_glow", "emitter", {
        "shape": "point", "position": [0.0, HALO_Y + 0.02, 0.0], "direction": [0.0, -1.0, 0.0], "velocity": 0.3,
        "rate": 0.0, "burst_count": 1, "burst_times": [0.1],
    }, {"particle": "ps_halo_glow"}, parent="anchor", layer="halos"))

    # ================= CHANNEL + throughout: light motes drifting down from the heavens =================
    add(node("ps_motes", "particle_system", {
        "max_particles": 260, "lifetime": 1.9, "lifetime_variance": 0.4,
        "size": 0.035, "size_variance": 0.015,
        "size_over_life": curve([(0.0, 0.4), (0.2, 1.0), (0.8, 0.85), (1.0, 0.3)]),
        "color": c(ivory), "color_over_life": [[0.0, c(ivory)], [0.5, c(gold)], [1.0, c(amber)]],
        "opacity": 0.85, "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.5, 0.65), (0.75, 0.85), (1.0, 0.0)]),
        "emissive": 1.6, "drag": 0.8, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.04,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_sink", "f_curl", "f_gather"],
        "colliders": ["ground"]}))
    add(node("e_motes", "emitter", {
        "shape": "disc", "radius": 1.5, "position": [0.0, 3.9, 0.0],
        "direction": [0.0, -1.0, 0.0], "spread": 20.0, "velocity": 0.55, "velocity_variance": 0.25,
        "rate": tr([(0.0, 30.0), (0.5, 70.0), (1.2, 90.0), (2.2, 70.0), (2.6, 25.0), (2.9, 0.0), (DURATION, 0.0)]),
    }, {"particle": "ps_motes"}, parent="anchor", layer="motes"))

    # ================= SOUL RISES: the beam descends from the sky =================
    land, rise0, rise1 = 0.86, 2.6, 3.0
    beam_origin = tr([(0.0, [0.0, BEAM_TOP, 0.0]), (0.42, [0.0, BEAM_TOP, 0.0]), (0.6, [0.0, 5.0, 0.0]),
                      (0.74, [0.0, 2.1, 0.0]), (land, [0.0, 0.0, 0.0]), (rise0, [0.0, 0.0, 0.0]),
                      (2.75, [0.0, 0.9, 0.0]), (2.9, [0.0, 2.3, 0.0]), (rise1, [0.0, 3.6, 0.0])])
    bands = [
        # id, width fraction, colour, alpha, emissive, noise offset, amplitude
        ("haze", 1.6, mix(amber, deep, 0.3), 0.35, 1.2, 2.1, 0.16),
        ("outer", 1.0, amber, 0.55, 2.0, 0.0, 0.11),
        ("mid", 0.6, gold, 0.45, 1.8, 0.7, 0.06),
        ("inner", 0.3, mix(gold, ivory, 0.5), 0.3, 1.4, 1.4, 0.04),
    ]
    beams: list[str] = []
    for bid, wf, col, alpha, em, off, amp in bands:
        width = tr([(0.0, BEAM_W * wf * 0.55), (0.42, BEAM_W * wf * 0.55), (land, BEAM_W * wf),
                    (2.05, BEAM_W * wf), (2.2, BEAM_W * wf * 1.12), (2.45, BEAM_W * wf), (rise0, BEAM_W * wf * 0.9),
                    (rise1, BEAM_W * wf * 0.3)])
        emis = tr([(0.0, 0.0), (0.42, 0.0), (0.5, em), (land, em * 1.15), (1.05, em * 0.8), (1.25, em * 0.6),
                   (1.75, em * 0.6), (2.05, em * 1.0), (2.2, em * 1.45), (2.45, em), (rise0, em * 0.8),
                   (2.8, em * 0.35), (rise1, 0.0)])
        add(node(f"b_{bid}", "beam", {
            "origin": beam_origin, "target": [0.0, BEAM_TOP, 0.0], "width": width,
            "segments": 40, "noise_amplitude": amp, "noise_frequency": 0.22, "jitter_rate": 0.0,
            "noise_scroll": -1.1, "noise_seed": 7, "noise_offset": off, "noise_taper": 0.1,
            "detail": 0, "width_profile": "taper_end", "core_width": 0.0, "glow_width": 1.0,
            "color": c(col, alpha), "emissive": emis, "blend": "additive",
            "start_time": 0.42, "duration": rise1 - 0.42,
        }, {"material": "mat_beam"}, layer="beam"))
        beams.append(f"b_{bid}")

    # the descending tip: a soft glow that leads the beam down
    add(node("beam_tip", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                  "position": beam_origin}, parent="anchor", layer="beam"))
    add(node("ps_tip", "particle_system", {
        "max_particles": 30, "lifetime": 0.06, "size": 1.0, "size_variance": 0.1,
        "size_over_life": curve([(0.0, 0.8), (1.0, 1.1)]),
        "color": c(gold), "color_over_life": [[0.0, c(ivory)], [1.0, c(gold)]],
        "opacity": 0.14, "opacity_over_life": curve([(0.0, 1.0), (1.0, 0.0)]),
        "emissive": 0.9, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.2,
    }, {"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_tip", "emitter", {
        "shape": "point", "direction": [0.0, -1.0, 0.0], "velocity": 0.0,
        "rate": tr([(0.0, 260.0), (land, 260.0), (land + 0.02, 0.0), (rise0, 0.0), (rise0 + 0.03, 200.0),
                    (2.8, 120.0), (rise1, 0.0)]),
        "start_time": 0.5, "duration": rise1 - 0.5,
    }, {"particle": "ps_tip"}, parent="beam_tip", layer="beam"))

    # falling light streaks inside the beam: the flow reads downward
    add(node("ps_streaks", "particle_system", {
        "max_particles": 90, "lifetime": 0.9, "lifetime_variance": 0.1,
        "size": 0.05, "size_variance": 0.02,
        "size_over_life": curve([(0.0, 0.6), (0.3, 1.0), (1.0, 0.8)]),
        "color": c(ivory), "color_over_life": [[0.0, c(ivory)], [0.6, c(gold)], [1.0, c(amber)]],
        "opacity": 0.35, "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.8, 0.8), (1.0, 0.0)]),
        "emissive": 1.2, "render_mode": "stretched_billboard", "velocity_stretch": 1.5,
        "blend": "additive", "soft_particle_distance": 0.1,
    }, {"sprite": "tex_streak", "material": "mat_streak", "colliders": ["ground"]}))
    add(node("e_streaks", "emitter", {
        "shape": "disc", "radius": 0.55, "position": [0.0, 6.5, 0.0],
        "direction": [0.0, -1.0, 0.0], "spread": 1.5, "velocity": 8.0, "velocity_variance": 1.5,
        "rate": tr([(0.0, 0.0), (0.5, 0.0), (0.6, 30.0), (1.6, 26.0), (2.2, 32.0), (2.4, 14.0), (2.55, 0.0)]),
        "start_time": 0.5, "duration": 2.15,
    }, {"particle": "ps_streaks"}, parent="anchor", layer="beam"))

    # where the beam meets the body: a soft ground glow that brightens as the soul rejoins
    add(node("ps_contact", "particle_system", {
        "max_particles": 40, "lifetime": 0.5, "lifetime_variance": 0.1, "size": 1.5, "size_variance": 0.2,
        "size_over_life": curve([(0.0, 0.7), (0.4, 1.0), (1.0, 1.1)]),
        "color": c(gold), "color_over_life": [[0.0, c(ivory)], [1.0, c(gold)]],
        "opacity": 0.12, "opacity_over_life": curve([(0.0, 0.0), (0.3, 1.0), (1.0, 0.0)]),
        "emissive": 0.7, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.3,
    }, {"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_contact", "emitter", {
        "shape": "box", "size": [1.4, 0.1, 0.4], "position": [0.0, 0.3, 0.0], "direction": [0.0, 1.0, 0.0],
        "velocity": 0.1,
        "rate": tr([(0.0, 0.0), (0.84, 0.0), (0.88, 30.0), (1.2, 12.0), (2.0, 12.0), (2.15, 36.0), (2.35, 30.0),
                    (2.6, 8.0), (2.9, 0.0)]),
        "start_time": 0.84, "duration": 2.1,
    }, {"particle": "ps_contact"}, parent="anchor", layer="beam"))

    # ================= SOUL: a translucent human soul rises out of the body, hovers, returns =================
    ts = times(SOUL, 2.3)
    add(node("soul", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                              "position": tr([(t, [round(x, 4) for x in soul_pos(t)]) for t in ts]),
                              "rotation": tr([(t, [0.0, 0.0, round(soul_roll(t), 3)]) for t in ts])},
             parent="anchor", layer="soul"))
    # materialise out of the lying body over ~0.3 s, dissolve back into it at the reconverge
    soul_env = [(0.0, 0.0), (SOUL, 0.0), (0.82, 0.45), (1.0, 1.0), (1.9, 1.0), (2.08, 0.55), (2.22, 0.0),
                (DURATION, 0.0)]

    # the seamless silhouette: a camera-facing figure whose up axis follows the rig (a stretched billboard
    # with no stretch aligns its head to the emitter's rotated +Y velocity), re-emitted every frame
    add(node("ps_soul_figure", "particle_system", {
        "max_particles": 16, "lifetime": 0.025, "size": 1.95,
        "color": c(mix(ivory, gold, 0.12)), "opacity": 0.3, "opacity_over_life": curve([(0.0, 1.0), (1.0, 1.0)]),
        "emissive": 1.2, "render_mode": "stretched_billboard", "velocity_stretch": 0.0, "blend": "additive",
        "soft_particle_distance": 0.05,
    }, {"sprite": "tex_soul_figure", "material": "mat_glow"}))
    add(node("e_soul_figure", "emitter", {
        "shape": "point", "position": [0.0, -0.04, 0.02], "direction": [0.0, 1.0, 0.0], "velocity": 0.12,
        "rate": tr([(t, round(150.0 * v, 3)) for t, v in soul_env]), "start_time": SOUL, "duration": 2.25 - SOUL,
    }, {"particle": "ps_soul_figure"}, parent="soul", layer="soul"))

    # a faint soft glow round the figure so it sits in the beam (the figure is the read, not the glow)
    add(node("v_soul", "volume", {
        "mode": "procedural", "volume_type": "magic", "shape": "nebula", "position": [0.0, -0.02, 0.0],
        "radius": 0.34, "height": 1.95,
        "density": tr([(t, round(0.55 * v, 4)) for t, v in soul_env]),
        "emission": 0.75, "color": c(mix(pearl, gold, 0.45)), "color_hot": c(mix(ivory, gold, 0.25)),
        "filament_scale": 3.2, "strands": 0.5, "carve": 0.38, "softness": 0.95,
        "twist": 1.2, "spin": 0.18, "climb": 0.7, "scatter": 0.15, "march_steps": 16,
        "start_time": SOUL, "duration": 2.25 - SOUL,
    }, parent="soul", layer="soul"))
    # head and heart a little brighter (one soft glow system, two emitters)
    add(node("ps_soul_glow", "particle_system", {
        "max_particles": 30, "lifetime": 0.1, "size": 0.48,
        "color": c(pearl), "color_over_life": [[0.0, c(ivory)], [1.0, c(mix(pearl, gold, 0.4))]], "opacity": 0.08,
        "opacity_over_life": curve([(0.0, 0.0), (0.3, 1.0), (1.0, 0.0)]),
        "emissive": 1.0, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.1,
    }, {"sprite": "tex_glow", "material": "mat_glow"}))
    for part, pos, rate in (("head", [0.0, 0.76, 0.02], 45.0), ("heart", [0.0, 0.36, 0.06], 30.0)):
        add(node(f"e_soul_{part}_glow", "emitter", {
            "shape": "point", "position": pos, "direction": [0.0, 1.0, 0.0], "velocity": 0.0,
            "rate": tr([(t, round(rate * v, 3)) for t, v in soul_env]), "start_time": SOUL, "duration": 2.25 - SOUL,
        }, {"particle": "ps_soul_glow"}, parent="soul", layer="soul"))

    # the body gently sheds its light: motes and short rising wisps from the surface of the torso, head
    # and arms (scaled sphere shells on each part), which also hide the seams between the parts
    add(node("ps_shed_motes", "particle_system", {
        "max_particles": 120, "lifetime": 0.9, "lifetime_variance": 0.25, "size": 0.026, "size_variance": 0.01,
        "size_over_life": curve([(0.0, 0.5), (0.2, 1.0), (1.0, 0.3)]),
        "color": c(ivory), "color_over_life": [[0.0, c(ivory)], [0.6, c(mix(ivory, gold, 0.5))], [1.0, c(gold)]],
        "opacity": 0.85, "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (1.0, 0.0)]),
        "emissive": 1.5, "drag": 1.4, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.03,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_lift", "f_curl"]}))
    add(node("ps_shed_wisps", "particle_system", {
        "max_particles": 120, "lifetime": 0.6, "lifetime_variance": 0.15, "size": 0.05, "size_variance": 0.015,
        "size_over_life": curve([(0.0, 0.4), (0.3, 1.0), (1.0, 0.6)]),
        "color": c(pearl), "color_over_life": [[0.0, c(ivory)], [0.6, c(mix(pearl, gold, 0.5))], [1.0, c(gold)]],
        "opacity": 0.3, "opacity_over_life": curve([(0.0, 0.0), (0.25, 1.0), (1.0, 0.0)]),
        "emissive": 1.1, "drag": 1.2, "render_mode": "stretched_billboard", "velocity_stretch": 2.4,
        "blend": "additive", "soft_particle_distance": 0.05,
    }, {"sprite": "tex_streak", "material": "mat_streak", "forces": ["f_lift", "f_curl"]}))
    sheds = [("chest", [0.0, 0.36, 0.0], [0.0, 0.0, 0.0], 0.15, [1.1, 1.5, 0.6], 26.0, 30.0),
             ("head", [0.0, 0.76, 0.0], [0.0, 0.0, 0.0], 0.1, [1.0, 1.15, 1.0], 10.0, 10.0),
             ("waist", [0.0, 0.05, 0.0], [0.0, 0.0, 0.0], 0.12, [1.0, 1.5, 0.7], 10.0, 12.0),
             ("arm_r", [0.25, 0.26, 0.0], [0.0, 0.0, 25.0], 0.04, [1.0, 6.5, 1.0], 12.0, 12.0),
             ("arm_l", [-0.25, 0.26, 0.0], [0.0, 0.0, -25.0], 0.04, [1.0, 6.5, 1.0], 12.0, 12.0)]
    for sid, ptrack, rtrack, rad, scl, mote_rate, wisp_rate in sheds:
        for kind, rate, ps in (("motes", mote_rate, "ps_shed_motes"), ("wisps", wisp_rate, "ps_shed_wisps")):
            add(node(f"e_shed_{kind}_{sid}", "emitter", {
                "shape": "sphere", "radius": rad, "surface_only": True, "position": ptrack, "rotation": rtrack,
                "scale": scl, "direction": [0.0, 0.0, 0.0], "velocity": 0.12, "velocity_variance": 0.06,
                "rate": tr([(t, round(rate * v, 3)) for t, v in soul_env]), "start_time": SOUL,
                "duration": 2.25 - SOUL,
            }, {"particle": ps}, parent="soul", layer="soul"))
    # below the feet: a soft trail of mist streaming back down to the body on the ground, plus wisps shed
    # from the legs that inherit the soul's motion and lag behind it as it rises
    add(node("ps_tether", "particle_system", {
        "max_particles": 60, "lifetime": 0.5, "lifetime_variance": 0.1, "size": 0.11, "size_variance": 0.03,
        "size_over_life": curve([(0.0, 0.6), (0.3, 1.0), (1.0, 1.4)]),
        "color": c(pearl), "color_over_life": [[0.0, c(mix(pearl, gold, 0.3))], [1.0, c(gold)]],
        "opacity": 0.2, "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (1.0, 0.0)]),
        "emissive": 1.0, "drag": 0.6, "render_mode": "stretched_billboard", "velocity_stretch": 2.0,
        "blend": "additive", "soft_particle_distance": 0.15,
    }, {"sprite": "tex_streak", "material": "mat_streak", "forces": ["f_curl"], "colliders": ["ground"]}))
    add(node("e_tether", "emitter", {
        "shape": "sphere", "radius": 0.08, "position": [0.0, -0.82, 0.0], "direction": [0.0, -1.0, 0.0],
        "spread": 12.0, "velocity": 1.1, "velocity_variance": 0.3,
        "rate": tr([(0.0, 0.0), (SOUL, 0.0), (0.95, 60.0), (1.85, 60.0), (2.05, 0.0), (DURATION, 0.0)]),
        "start_time": SOUL, "duration": 2.25 - SOUL,
    }, {"particle": "ps_tether"}, parent="soul", layer="soul"))
    add(node("ps_wisps", "particle_system", {
        "max_particles": 80, "lifetime": 0.5, "lifetime_variance": 0.12, "size": 0.13, "size_variance": 0.04,
        "size_over_life": curve([(0.0, 0.5), (0.3, 1.0), (1.0, 0.8)]),
        "color": c(pearl), "color_over_life": [[0.0, c(ivory)], [0.5, c(mix(pearl, gold, 0.5))], [1.0, c(gold)]],
        "opacity": 0.12, "opacity_over_life": curve([(0.0, 0.0), (0.25, 1.0), (1.0, 0.0)]),
        "emissive": 1.0, "drag": 3.0, "render_mode": "stretched_billboard", "velocity_stretch": 1.2,
        "blend": "additive", "soft_particle_distance": 0.15,
    }, {"sprite": "tex_streak", "material": "mat_streak", "forces": ["f_curl"]}))
    add(node("e_wisps", "emitter", {
        "shape": "sphere", "radius": 0.14, "scale": [1.2, 2.6, 1.0], "position": [0.0, -0.6, 0.0],
        "direction": [0.0, 0.0, 0.0], "velocity": 0.1, "velocity_variance": 0.05, "inherit_velocity": 0.9,
        "rate": tr([(t, round(60.0 * v, 3)) for t, v in soul_env]), "start_time": SOUL, "duration": 2.25 - SOUL,
    }, {"particle": "ps_wisps"}, parent="soul", layer="soul"))

    # soul ribbons: two slow soft spirals round the rising / returning soul
    rib_trails: list[str] = []
    for r in SOUL_RIBBONS:
        rid = r["id"]
        t0, t1 = r["t0"], r["t1"]
        rts = times(t0 - 0.05, t1 + 0.05)
        add(node(f"rib_rig_{rid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
            "position": tr([(t, [0.0, round(soul_pos(t)[1], 4), 0.0]) for t in rts]),
        }, parent="anchor", layer="soul_ribbons"))
        add(node(f"rib_spin_{rid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
            "rotation": tr([(t, [0.0, round(r["phase"] + 360.0 * r["rate"] * (t - t0), 3), 0.0])
                            for t in times(t0 - 0.05, t1 + 0.05, 0.1)]),
        }, parent=f"rib_rig_{rid}", layer="soul_ribbons"))

        def rib_local(t: float, r: dict[str, Any] = r) -> list[float]:
            u = smooth((t - r["t0"]) / (r["t1"] - r["t0"]))
            h = -0.45 + 1.25 * u if r["up"] else 0.8 - 1.2 * u
            rad = r["radius"] * (0.8 + 0.2 * math.sin(math.pi * u))
            return [round(rad, 4), round(h, 4), 0.0]

        add(node(f"bead_rib_{rid}", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4,
                                             "visible": False, "position": tr([(t, rib_local(t)) for t in rts])},
                 parent=f"rib_spin_{rid}", layer="soul_ribbons"))
        g = r["gain"]
        env = [(0.0, 0.0), (t0, 0.0), (t0 + 0.2, 1.0), (t1 - 0.28, 1.0), (t1, 0.0), (DURATION, 0.0)]
        add(node(f"glow_rib_{rid}", "trail", {
            "lifetime": 0.7, "max_segments": 160, "min_vertex_distance": 0.02,
            "taper": curve([(0.0, 0.0), (0.12, 0.8), (0.3, 1.0), (0.6, 0.6), (1.0, 0.0)]),
            "opacity_over_life": curve([(0.0, 1.0), (0.5, 0.8), (1.0, 0.0)]),
            "color": c(gold), "blend": "additive",
            "width": tr([(t, round(0.3 * v, 4)) for t, v in env]),
            "emissive": tr([(t, round(0.5 * g * v, 4)) for t, v in env]),
        }, {"source": f"bead_rib_{rid}", "material": "mat_ribbon_glow"}, layer="soul_ribbons"))
        add(node(f"core_rib_{rid}", "trail", {
            "lifetime": 0.7, "max_segments": 160, "min_vertex_distance": 0.02,
            "taper": curve([(0.0, 0.0), (0.1, 0.9), (0.26, 1.0), (0.55, 0.5), (1.0, 0.0)]),
            "opacity_over_life": curve([(0.0, 1.0), (0.5, 0.8), (1.0, 0.0)]),
            "color": c(ivory), "blend": "additive",
            "width": tr([(t, round(0.012 * v, 4)) for t, v in env]),
            "emissive": tr([(t, round(0.35 * g * v, 4)) for t, v in env]),
        }, {"source": f"bead_rib_{rid}", "material": "mat_ribbon_core"}, layer="soul_ribbons"))
        rib_trails += [f"glow_rib_{rid}", f"core_rib_{rid}"]

    # ================= feathers: a few drift down from above, a flight is released at the restore =================
    feather_ps = {
        "lifetime": 2.4, "lifetime_variance": 0.4, "size": 0.34, "size_variance": 0.08,
        "size_over_life": curve([(0.0, 0.3), (0.12, 1.0), (0.85, 0.95), (1.0, 0.6)]),
        "rotation_variance": 180.0, "angular_velocity": 0.0, "angular_velocity_variance": 70.0,
        "color": c(ivory), "color_over_life": [[0.0, c(ivory)], [0.6, c(mix(ivory, gold, 0.5))], [1.0, c(amber)]],
        "opacity": 0.9, "opacity_over_life": curve([(0.0, 0.0), (0.12, 1.0), (0.7, 0.8), (1.0, 0.0)]),
        "emissive": 1.3, "drag": 1.8, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.04,
    }
    add(node("ps_feathers", "particle_system", {"max_particles": 30, **feather_ps},
             {"sprite": "tex_feather", "material": "mat_feather", "forces": ["f_feather", "f_sway"],
              "colliders": ["ground"]}))
    add(node("e_feathers_fall", "emitter", {
        "shape": "disc", "radius": 1.6, "position": [0.0, 3.7, 0.0], "direction": [0.0, -1.0, 0.0],
        "spread": 30.0, "velocity": 0.3, "velocity_variance": 0.15,
        "rate": tr([(0.0, 5.0), (1.0, 7.0), (2.0, 5.0), (2.3, 0.0), (DURATION, 0.0)]),
    }, {"particle": "ps_feathers"}, parent="anchor", layer="feathers"))
    add(node("ps_feathers_burst", "particle_system", {"max_particles": 40, **feather_ps, "lifetime": 1.25,
                                                      "lifetime_variance": 0.15},
             {"sprite": "tex_feather", "material": "mat_feather", "forces": ["f_feather", "f_sway"],
              "colliders": ["ground"]}))
    add(node("e_feathers_burst", "emitter", {
        "shape": "box", "size": [1.6, 0.2, 0.6], "position": [0.0, 0.35, 0.0], "direction": [0.0, 1.0, 0.0],
        "spread": 62.0, "velocity": 2.7, "velocity_variance": 1.0,
        "rate": 0.0, "burst_count": 24, "burst_times": [RESTORE + 0.05],
    }, {"particle": "ps_feathers_burst"}, parent="anchor", layer="feathers"))

    # ================= RESTORE: a gentle warm burst at the body =================
    add(node("ps_burst_glow", "particle_system", {
        "max_particles": 2, "lifetime": 0.7, "size": 2.4,
        "size_over_life": curve([(0.0, 0.3), (0.15, 1.0), (1.0, 1.25)]),
        "color": c(gold), "color_over_life": [[0.0, c(ivory)], [1.0, c(gold)]], "opacity": 0.42,
        "opacity_over_life": curve([(0.0, 0.0), (0.1, 1.0), (0.45, 0.5), (1.0, 0.0)]),
        "emissive": 0.9, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.2,
    }, {"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_burst_glow", "emitter", {
        "shape": "point", "position": [0.0, 0.45, 0.0], "direction": [0.0, 1.0, 0.0], "velocity": 0.1,
        "rate": 0.0, "burst_count": 1, "burst_times": [RESTORE],
    }, {"particle": "ps_burst_glow"}, parent="anchor", layer="restore"))
    add(node("ps_burst_motes", "particle_system", {
        "max_particles": 90, "lifetime": 1.1, "lifetime_variance": 0.3, "size": 0.04, "size_variance": 0.015,
        "size_over_life": curve([(0.0, 0.5), (0.2, 1.0), (1.0, 0.3)]),
        "color": c(ivory), "color_over_life": [[0.0, c(ivory)], [0.5, c(gold)], [1.0, c(deep)]],
        "opacity": 0.85, "opacity_over_life": curve([(0.0, 0.0), (0.1, 1.0), (0.6, 0.6), (1.0, 0.0)]),
        "emissive": 1.8, "drag": 1.6, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.03,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_curl", "f_sink"]}))
    add(node("e_burst_motes", "emitter", {
        "shape": "box", "size": [1.6, 0.2, 0.6], "position": [0.0, 0.3, 0.0], "direction": [0.0, 1.0, 0.0],
        "spread": 60.0, "velocity": 2.2, "velocity_variance": 1.0,
        "rate": 0.0, "burst_count": 70, "burst_times": [RESTORE + 0.02],
    }, {"particle": "ps_burst_motes"}, parent="anchor", layer="restore"))

    # ================= light =================
    add(node("l_beam", "light", {
        # low, and dim while the soul is up: a point light near the soul's meshes leaves a specular hotspot
        "light_type": "point", "position": [0.0, 0.25, 0.0], "color": c(gold), "radius": 4.0,
        "intensity": tr([(0.0, 0.0), (0.5, 0.3), (land, 0.8), (1.0, 0.3), (1.9, 0.3), (2.1, 0.9), (RESTORE, 2.0),
                         (2.5, 1.0), (3.1, 0.0), (DURATION, 0.0)]),
    }, parent="anchor", layer="light"))
    add(node("l_halo", "light", {
        "light_type": "point", "position": [0.0, HALO_Y, 0.0], "color": c(gold), "radius": 3.0,
        "intensity": tr([(0.0, 0.0), (0.5, 0.5), (1.6, 0.5), (2.6, 0.0), (DURATION, 0.0)]),
    }, parent="anchor", layer="light"))

    # ---------------- controls ----------------
    # The user removed the floating soul figure ("looks weird"): drop the whole soul layer and its texture. The
    # soul ribbons stay: they climb the beam and come back down on their own, so energy still lifts and returns.
    dropped = {n["id"] for n in nodes if n.get("layer") == "soul"} | {"tex_soul_figure"}
    dropped |= {"ps_soul_figure", "ps_soul_glow", "ps_shed_motes", "ps_shed_wisps", "ps_tether", "ps_wisps", "f_lift"}
    nodes[:] = [n for n in nodes if n["id"] not in dropped]

    colour_bindings = []
    for n in nodes:
        for parameter in ("color", "color_over_life", "emissive_color", "color_hot"):
            if parameter in n["parameters"]:
                colour_bindings.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit, "bindings": bindings}

    soul_ps = [ps for ps in ("ps_soul_figure", "ps_soul_glow", "ps_shed_motes", "ps_shed_wisps", "ps_wisps",
                             "ps_tether") if ps not in dropped]
    sprite_systems = ["ps_motes", "ps_tip", "ps_streaks", "ps_contact", "ps_feathers", "ps_feathers_burst",
                      "ps_burst_glow", "ps_burst_motes", "ps_halo_glow"] + soul_ps
    decals = ["d_pool", "d_wave", "d_sigil"]
    soul_bright = [mul(ps, "emissive") for ps in soul_ps] + [mul(t, "emissive") for t in rib_trails]
    controls = [
        control("intensity", "Intensity", "Global", 0.0, 3.0,
                [mul(t, "emissive") for t in halo_trails + rib_trails + beams]
                + [mul(ps, "emissive") for ps in sprite_systems]
                + [mul(d, "emissive") for d in decals] + [mul("l_beam", "intensity"), mul("l_halo", "intensity")]),
        control("beam_width", "Beam width", "Beam of light", 0.3, 2.5,
                [mul(b, "width") for b in beams] + [mul("e_streaks", "radius"), mul("ps_tip", "size"),
                                                    mul("ps_contact", "size")]),
        control("soul_brightness", "Ribbon brightness", "Soul ribbons", 0.0, 3.0, soul_bright),
        control("feather_amount", "Feather amount", "Feathers", 0.0, 2.0,
                [mul("e_feathers_fall", "rate"), mul("e_feathers_burst", "burst_count")]),
        control("halo_brightness", "Halo brightness", "Halo rings", 0.0, 3.0,
                [mul(t, "emissive") for t in halo_trails] + [mul("d_sigil", "emissive"),
                                                             mul("ps_halo_glow", "emissive")]),
        control("character_size", "Character size", "Global", 0.5, 2.0,
                [mul("anchor", "scale")] + [mul(t, "width") for t in halo_trails + rib_trails + beams]
                + [mul(b, "origin") for b in beams] + [mul(b, "target") for b in beams]
                + [mul(d, "size") for d in decals]
                + [mul(ps, "size") for ps in sprite_systems]
                + [mul(e, "velocity") for e in ("e_streaks", "e_feathers_burst", "e_burst_motes", "e_motes")]
                + [mul("l_beam", "radius"), mul("l_halo", "radius")]),
        control("hue", "Energy hue", "Global", -180.0, 180.0, colour_bindings, "deg", 0.0, 1.0),
        control("global_speed", "Speed", "Global", 0.25, 4.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}], step=0.05),
    ]

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": "A graceful holy revive on a fallen ally in pearly ivory and white-gold: halo rings form high "
                       "above, a soft beam descends onto the body, soft ribbons of light spiral up the beam and back down, "
                       "then a gentle warm burst, a ground halo and a flight of feathers restore the body. 3.0 s plus "
                       "drifting feathers; origin on the ground at the body's centre, body lying along X.",
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [
            {"name": "channel", "start": CHANNEL, "end": SOUL},
            {"name": "soul_rises", "start": SOUL, "end": RECONVERGE},
            {"name": "reconverge", "start": RECONVERGE, "end": RESTORE},
            {"name": "restore", "start": RESTORE, "end": COMPLETE},
            {"name": "complete", "start": COMPLETE, "end": DURATION},
        ]},
        "layers": [
            {"id": "ground", "name": "Ground light", "role": "telegraph"},
            {"id": "halos", "name": "Halo rings", "role": "ignition"},
            {"id": "beam", "name": "Beam of light", "role": "primary"},
            {"id": "soul_ribbons", "name": "Soul ribbons", "role": "secondary"},
            {"id": "feathers", "name": "Feathers", "role": "secondary"},
            {"id": "restore", "name": "Restore burst", "role": "interaction"},
            {"id": "motes", "name": "Light motes", "role": "aftermath"},
            {"id": "light", "name": "Light", "role": "secondary"},
        ],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/resurrect.py",
            "theme": "holy",
            "category": "support",
            "attachment": {
                "origin": [0.0, 0.0, 0.0], "origin_node": "anchor", "character_height": 1.8,
                "body_radius": 0.33,
                "notes": "Attach the `anchor` node (the effect origin) on the ground at the centre of the fallen "
                         "ally's body, y = 0, with the body lying along X. Nothing is drawn for the ally or the "
                         "caster. The halo rings sit about 3.2 m above the ground and the beam comes down from well "
                         "above the frame. Scale for bigger or smaller characters with the Character size control.",
            },
            "analysis": "channel 0-0.7 halo rings form high above and turn, motes and feathers drift down, a pool "
                        "of light gathers under the body | soul_rises 0.7-1.6 a soft beam descends onto the body, "
                        "a slow ribbon spirals up out of the body | reconverge "
                        "1.6-2.2 a second ribbon spirals back down into the body, the halos descend and widen, the beam's foot "
                        "brightens | restore 2.2-2.6 a warm burst, a ground halo expands, feathers released | "
                        "complete 2.6-3.5 the beam lifts away, feathers and motes drift down and fade",
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.05,
                                "bloom_intensity": 0.26, "bloom_radius": 0.05, "exposure": 0.95, "grid": False},
            "tags": TAGS,
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write resurrect.json into")
    parser.add_argument("--check", help="directory whose resurrect.json must match the generator output byte for byte")
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
