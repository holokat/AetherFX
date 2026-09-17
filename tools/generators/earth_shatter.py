#!/usr/bin/env python3
"""Build examples/effects/earth_shatter.json.

Layout: cast point at x = -4.0, target at x = +3.2, ground y = 0.
The shatter path is 8 overlapping crack decal segments that open in sequence
behind a travelling "crack head" (light + glow decal + grit/dust/rock emitters).
The target eruption is 9 hand-placed hero rock spikes (individual mesh nodes with
keyframed TRS, rising out of the ground) plus two mesh-particle crown rings.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "effects" / "earth_shatter.json"
DUR = 2.6
SEED = 8471

rng = random.Random(90210)


def r(v, n=4):
    return round(float(v), n)


# ---------------------------------------------------------------- path -----
PATH = [
    (-4.00, -0.18),
    (-3.12, 0.62),
    (-2.26, -0.52),
    (-1.36, 0.70),
    (-0.46, -0.44),
    (0.48, 0.76),
    (1.38, -0.30),
    (2.26, 0.52),
    (3.20, 0.02),
]
# short crack branches shooting off the path: (path index, side, length, angle off, t_delay)
BRANCH = [
    (1, +1, 1.30, 58.0, 0.06),
    (2, -1, 1.05, 47.0, 0.05),
    (4, +1, 1.45, 63.0, 0.05),
    (5, -1, 1.15, 52.0, 0.06),
    (6, +1, 0.95, 44.0, 0.04),
]
# time the crack head reaches PATH[i]
HEAD_T = [0.18, 0.34, 0.50, 0.60, 0.70, 0.82, 0.93, 1.03, 1.10]
TARGET = PATH[-1]

nodes: list[dict] = []


def node(nid, ntype, params=None, inputs=None, layer=None, parent=None, seed=None, meta=None):
    n = {"id": nid, "type": ntype}
    if layer:
        n["layer"] = layer
    if parent:
        n["parent"] = parent
    if seed is not None:
        n["seed"] = seed
    if params:
        n["parameters"] = params
    if inputs:
        n["inputs"] = inputs
    if meta:
        n["metadata"] = meta
    nodes.append(n)
    return nid


def track(keys, interp=None):
    """keys = [(t, value), ...] -> {"value": first, "track": [...]}"""
    out = []
    for t, v in keys:
        k = {"time": r(t, 4), "value": v}
        if interp:
            k["interp"] = interp
        out.append(k)
    return {"value": keys[0][1], "track": out}


def step_track(keys):
    """keys = [(t, value, interp?), ...]; keys that are not strictly later than the
    previous kept key are dropped, so a shared tail can be appended to a track whose
    head already runs past it."""
    out = []
    last = None
    for k in keys:
        t = r(k[0], 4)
        if last is not None and t <= last:
            continue
        last = t
        item = {"time": t, "value": k[1]}
        if len(k) > 2 and k[2]:
            item["interp"] = k[2]
        out.append(item)
    return {"value": out[0]["value"], "track": out}


# ------------------------------------------------------------ textures -----
def g(nid, op, params=None, inputs=None):
    d = {"id": nid, "op": op}
    if params:
        d["params"] = params
    if inputs:
        d["inputs"] = inputs
    return d


CRACK_RAMP = [
    [0.0, [0.02, 0.006, 0.002, 0.0]],
    [0.10, [0.26, 0.045, 0.006, 0.10]],
    [0.32, [0.92, 0.20, 0.018, 0.46]],
    [0.58, [1.0, 0.40, 0.055, 0.78]],
    [0.82, [1.0, 0.63, 0.19, 0.94]],
    [1.0, [1.0, 0.86, 0.52, 1.0]],
]


def crack_texture(nid, s, density, spine_lo, brk_lo, size=512):
    """A band of branching cracks running along +u with a meandering hot spine."""
    gn = [
        # two coarse fbms packed into rg = a 2D displacement field
        g("w1", "fbm", {"frequency": 2.0, "octaves": 4, "seed": s + 1}),
        g("w1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w1"}),
        g("w2", "fbm", {"frequency": 2.0, "octaves": 4, "seed": s + 2}),
        g("w2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w2"}),
        g("wv", "channel_pack", {"channel": "luminance"}, {"r": "w1l", "g": "w2l"}),
        g("f1", "fbm", {"frequency": 7.0, "octaves": 4, "seed": s + 3}),
        g("f1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "f1"}),
        g("f2", "fbm", {"frequency": 7.0, "octaves": 4, "seed": s + 4}),
        g("f2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "f2"}),
        g("fv", "channel_pack", {"channel": "luminance"}, {"r": "f1l", "g": "f2l"}),
        # v(1-v) parabola -> a band running along u, and a narrow spine inside it
        g("gv", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        g("gvi", "invert", None, {"a": "gv"}),
        g("bvr", "math", {"mode": "multiply"}, {"a": "gv", "b": "gvi"}),
        g("bv", "levels", {"in_low": 0.055, "in_high": 0.205}, {"a": "bvr"}),
        g("spr", "levels", {"in_low": spine_lo, "in_high": 0.2498}, {"a": "bvr"}),
        # the spine meanders and changes width: two displacement passes
        g("sp1", "distort", {"amount": 0.185}, {"a": "spr", "by": "wv"}),
        g("sp2", "distort", {"amount": 0.055}, {"a": "sp1", "by": "fv"}),
        # ... and breaks up along its length so it is never a constant-width line
        g("brk", "fbm", {"frequency": 3.4, "octaves": 3, "seed": s + 5}),
        g("brkl", "levels", {"in_low": brk_lo, "in_high": 0.78, "out_low": 0.30, "out_high": 1.0},
          {"a": "brk"}),
        g("spine", "math", {"mode": "multiply"}, {"a": "sp2", "b": "brkl"}),
        # branching crack network, two scales
        g("cr1", "cracks", {"density": density, "width": 0.0155, "seed": s + 6}),
        g("cr1d", "distort", {"amount": 0.042}, {"a": "cr1", "by": "fv"}),
        g("cr2", "cracks", {"density": density * 2.1, "width": 0.0062, "seed": s + 7}),
        g("cr2d", "distort", {"amount": 0.024}, {"a": "cr2", "by": "fv"}),
        g("cr2l", "levels", {"out_high": 0.52}, {"a": "cr2d"}),
        g("crk", "math", {"mode": "max"}, {"a": "cr1d", "b": "cr2l"}),
        g("crkm", "math", {"mode": "multiply"}, {"a": "crk", "b": "bv"}),
        g("u1", "math", {"mode": "max"}, {"a": "spine", "b": "crkm"}),
        # soft fade at both ends of the tile so overlapping segments blend
        g("gu", "gradient_linear", {"angle": 0.0, "start": 0.0, "end": 1.0}),
        g("gui", "invert", None, {"a": "gu"}),
        g("bur", "math", {"mode": "multiply"}, {"a": "gu", "b": "gui"}),
        g("bu", "levels", {"in_low": 0.0, "in_high": 0.155}, {"a": "bur"}),
        g("msk", "math", {"mode": "multiply"}, {"a": "u1", "b": "bu"}),
        # ember halo around the cracks
        g("blr", "blur", {"radius": 7.0}, {"a": "msk"}),
        g("blrl", "levels", {"in_high": 0.52, "out_high": 0.17}, {"a": "blr"}),
        g("k", "math", {"mode": "max"}, {"a": "msk", "b": "blrl"}),
        g("col", "colorize", {"gradient": CRACK_RAMP}, {"a": "k"}),
    ]
    return node(nid, "texture", {"width": size, "height": size,
                                 "graph": {"nodes": gn, "output": "col"}})


def web_texture(nid, s, counts, size=512):
    """Radial branching crack burst (cast rune cracks / target crater)."""
    gn = [
        g("w1", "fbm", {"frequency": 2.1, "octaves": 4, "seed": s + 1}),
        g("w1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w1"}),
        g("w2", "fbm", {"frequency": 2.1, "octaves": 4, "seed": s + 2}),
        g("w2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w2"}),
        g("wv", "channel_pack", {"channel": "luminance"}, {"r": "w1l", "g": "w2l"}),
        g("f1", "fbm", {"frequency": 6.5, "octaves": 4, "seed": s + 3}),
        g("f1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "f1"}),
        g("f2", "fbm", {"frequency": 6.5, "octaves": 4, "seed": s + 4}),
        g("f2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "f2"}),
        g("fv", "channel_pack", {"channel": "luminance"}, {"r": "f1l", "g": "f2l"}),
    ]
    merged = None
    for i, (count, width, inner, out_high, warp, rot) in enumerate(counts):
        a, b, c, d = f"s{i}", f"s{i}d", f"s{i}e", f"s{i}l"
        gn.append(g(a, "spokes", {"count": count, "width": width, "softness": width * 0.45,
                                  "inner_radius": inner, "outer_radius": 0.5, "rotation": rot}))
        gn.append(g(b, "distort", {"amount": warp}, {"a": a, "by": "wv"}))
        gn.append(g(c, "distort", {"amount": warp * 0.31}, {"a": b, "by": "fv"}))
        gn.append(g(d, "levels", {"out_high": out_high}, {"a": c}))
        if merged is None:
            merged = d
        else:
            gn.append(g(f"u{i}", "math", {"mode": "max"}, {"a": merged, "b": d}))
            merged = f"u{i}"
    gn += [
        g("cr", "cracks", {"density": 5.5, "width": 0.0055, "seed": s + 8}),
        g("crd", "distort", {"amount": 0.03}, {"a": "cr", "by": "fv"}),
        g("crm", "gradient_radial", {"radius": 0.5, "inner_radius": 0.04, "falloff": "linear"}),
        g("crx", "math", {"mode": "multiply"}, {"a": "crd", "b": "crm"}),
        g("crl", "levels", {"out_high": 0.44}, {"a": "crx"}),
        g("uu", "math", {"mode": "max"}, {"a": merged, "b": "crl"}),
        g("fall", "gradient_radial", {"radius": 0.5, "inner_radius": 0.30, "falloff": "linear"}),
        g("web", "math", {"mode": "multiply"}, {"a": "uu", "b": "fall"}),
        g("hot", "gradient_radial", {"radius": 0.06, "falloff": "quadratic"}),
        g("k1", "math", {"mode": "max"}, {"a": "web", "b": "hot"}),
        g("blr", "blur", {"radius": 8.0}, {"a": "k1"}),
        g("blrl", "levels", {"in_high": 0.55, "out_high": 0.15}, {"a": "blr"}),
        g("k2", "math", {"mode": "max"}, {"a": "k1", "b": "blrl"}),
        g("col", "colorize", {"gradient": CRACK_RAMP}, {"a": "k2"}),
    ]
    return node(nid, "texture", {"width": size, "height": size,
                                 "graph": {"nodes": gn, "output": "col"}})


# ---------------------------------------------------------------------------
node("cam", "camera", {"position": [0.35, 4.10, 12.4], "target": [0.35, 1.05, 0.0], "fov": 42})

crack_texture("tex_crack_a", 110, 4.6, 0.2395, 0.30)
crack_texture("tex_crack_b", 320, 5.6, 0.2425, 0.36)
crack_texture("tex_crack_c", 540, 4.0, 0.2370, 0.26)

web_texture("tex_castweb", 760, [(15, 0.0090, 0.02, 1.0, 0.150, 0.0),
                                 (23, 0.0052, 0.07, 0.86, 0.205, 7.0),
                                 (35, 0.0030, 0.18, 0.58, 0.245, 3.0)], 512)
web_texture("tex_crater", 980, [(17, 0.0105, 0.02, 1.0, 0.140, 11.0),
                                (27, 0.0058, 0.06, 0.88, 0.200, 4.0),
                                (41, 0.0031, 0.17, 0.60, 0.240, 9.0)], 768)

# dark scorched-earth band that sits under a crack segment
node("tex_scorch", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
    g("gv", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
    g("gvi", "invert", None, {"a": "gv"}),
    g("bvr", "math", {"mode": "multiply"}, {"a": "gv", "b": "gvi"}),
    g("bv", "levels", {"in_low": 0.012, "in_high": 0.175}, {"a": "bvr"}),
    g("gu", "gradient_linear", {"angle": 0.0, "start": 0.0, "end": 1.0}),
    g("gui", "invert", None, {"a": "gu"}),
    g("bur", "math", {"mode": "multiply"}, {"a": "gu", "b": "gui"}),
    g("bu", "levels", {"in_low": 0.0, "in_high": 0.15}, {"a": "bur"}),
    g("m", "math", {"mode": "multiply"}, {"a": "bv", "b": "bu"}),
    g("n", "fbm", {"frequency": 4.5, "octaves": 4, "seed": 61}),
    g("nl", "levels", {"in_low": 0.28, "in_high": 0.80, "out_low": 0.25, "out_high": 1.0}, {"a": "n"}),
    g("x", "math", {"mode": "multiply"}, {"a": "m", "b": "nl"}),
    g("e", "erosion", {"amount": 0.22, "frequency": 7.0, "seed": 62}, {"a": "x"}),
    g("l", "levels", {"in_low": 0.04, "in_high": 0.72}, {"a": "e"}),
], "output": "l"}})

# dry plated earth: voronoi plates with fine grain, faintly warmed by the effect
node("tex_plates", "texture", {"width": 512, "height": 512, "graph": {"nodes": [
    g("v1", "voronoi", {"cells": 9.0, "mode": "edges", "seed": 51}),
    g("v1i", "invert", None, {"a": "v1"}),
    g("v1l", "levels", {"in_low": 0.30, "in_high": 0.95, "out_high": 0.55}, {"a": "v1i"}),
    g("v2", "voronoi", {"cells": 19.0, "mode": "edges", "seed": 52}),
    g("v2i", "invert", None, {"a": "v2"}),
    g("v2l", "levels", {"in_low": 0.45, "in_high": 0.98, "out_high": 0.30}, {"a": "v2i"}),
    g("pl", "math", {"mode": "max"}, {"a": "v1l", "b": "v2l"}),
    g("gr", "fbm", {"frequency": 22.0, "octaves": 3, "seed": 53}),
    g("grl", "levels", {"in_low": 0.30, "in_high": 0.80, "out_low": 0.45, "out_high": 1.0},
      {"a": "gr"}),
    g("px", "math", {"mode": "multiply"}, {"a": "pl", "b": "grl"}),
    g("blo", "fbm", {"frequency": 2.4, "octaves": 4, "seed": 54}),
    g("blol", "levels", {"in_low": 0.28, "in_high": 0.78, "out_low": 0.18, "out_high": 1.0},
      {"a": "blo"}),
    g("py", "math", {"mode": "multiply"}, {"a": "px", "b": "blol"}),
    g("fo", "gradient_radial", {"radius": 0.5, "inner_radius": 0.12, "falloff": "smooth"}),
    g("out", "math", {"mode": "multiply"}, {"a": "py", "b": "fo"}),
], "output": "out"}})

# round scorch under the target crater
node("tex_crater_scorch", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
    g("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.10, "falloff": "smooth"}),
    g("n", "fbm", {"frequency": 5.0, "octaves": 4, "seed": 71}),
    g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "n"}),
    g("l", "levels", {"in_low": 0.07, "in_high": 0.52}, {"a": "m"}),
], "output": "l"}})

# cast rune: concentric rings + two dash bands + fine cracks (no cross, no star)
node("tex_rune", "texture", {"width": 512, "height": 512, "graph": {"nodes": [
    g("r1", "ring", {"radius": 0.468, "thickness": 0.0085, "softness": 0.004}),
    g("r2", "ring", {"radius": 0.412, "thickness": 0.0032, "softness": 0.003}),
    g("r3", "ring", {"radius": 0.318, "thickness": 0.0062, "softness": 0.004}),
    g("r4", "ring", {"radius": 0.176, "thickness": 0.0048, "softness": 0.003}),
    g("d1", "spokes", {"count": 27, "width": 0.0125, "softness": 0.004,
                       "inner_radius": 0.428, "outer_radius": 0.458, "rotation": 6.0}),
    g("d2", "spokes", {"count": 15, "width": 0.0175, "softness": 0.005,
                       "inner_radius": 0.334, "outer_radius": 0.398, "rotation": 12.0}),
    g("d3", "spokes", {"count": 9, "width": 0.0115, "softness": 0.005,
                       "inner_radius": 0.196, "outer_radius": 0.302, "rotation": 20.0}),
    g("gl", "cracks", {"density": 9.0, "width": 0.0042, "seed": 33}),
    g("glm", "gradient_radial", {"radius": 0.46, "inner_radius": 0.05, "falloff": "linear"}),
    g("glx", "math", {"mode": "multiply"}, {"a": "gl", "b": "glm"}),
    g("gll", "levels", {"out_high": 0.40}, {"a": "glx"}),
    g("hz", "gradient_radial", {"radius": 0.47, "falloff": "smooth"}),
    g("hzl", "levels", {"out_high": 0.11}, {"a": "hz"}),
    g("m1", "math", {"mode": "max"}, {"a": "r1", "b": "r2"}),
    g("m2", "math", {"mode": "max"}, {"a": "m1", "b": "r3"}),
    g("m3", "math", {"mode": "max"}, {"a": "m2", "b": "r4"}),
    g("m4", "math", {"mode": "max"}, {"a": "m3", "b": "d1"}),
    g("m5", "math", {"mode": "max"}, {"a": "m4", "b": "d2"}),
    g("m6", "math", {"mode": "max"}, {"a": "m5", "b": "d3"}),
    g("m7", "math", {"mode": "max"}, {"a": "m6", "b": "gll"}),
    g("m8", "math", {"mode": "max"}, {"a": "m7", "b": "hzl"}),
    g("col", "colorize", {"gradient": [
        [0.0, [0.06, 0.02, 0.004, 0.0]],
        [0.28, [0.85, 0.24, 0.03, 0.34]],
        [0.62, [1.0, 0.55, 0.12, 0.76]],
        [1.0, [1.0, 0.93, 0.72, 1.0]],
    ]}, {"a": "m8"}),
], "output": "col"}})

# expanding shock ring (soft, distorted -- never a crisp circle)
node("tex_ring", "texture", {"width": 512, "height": 512, "graph": {"nodes": [
    g("n1", "fbm", {"frequency": 2.6, "octaves": 4, "seed": 81}),
    g("n1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "n1"}),
    g("n2", "fbm", {"frequency": 2.6, "octaves": 4, "seed": 97}),
    g("n2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "n2"}),
    g("nv", "channel_pack", {"channel": "luminance"}, {"r": "n1l", "g": "n2l"}),
    g("rg", "ring", {"radius": 0.415, "thickness": 0.085, "softness": 0.075}),
    g("rd", "distort", {"amount": 0.055}, {"a": "rg", "by": "nv"}),
    g("rg2", "ring", {"radius": 0.44, "thickness": 0.022, "softness": 0.03}),
    g("rd2", "distort", {"amount": 0.048}, {"a": "rg2", "by": "nv"}),
    g("mx", "math", {"mode": "max"}, {"a": "rd", "b": "rd2"}),
    g("brk", "fbm", {"frequency": 4.0, "octaves": 3, "seed": 103}),
    g("brkl", "levels", {"in_low": 0.28, "in_high": 0.80, "out_low": 0.35, "out_high": 1.0}, {"a": "brk"}),
    g("x", "math", {"mode": "multiply"}, {"a": "mx", "b": "brkl"}),
    g("col", "colorize", {"gradient": [
        [0.0, [0.05, 0.015, 0.004, 0.0]],
        [0.25, [0.7, 0.20, 0.03, 0.22]],
        [0.6, [1.0, 0.48, 0.10, 0.62]],
        [1.0, [1.0, 0.88, 0.58, 0.95]],
    ]}, {"a": "x"}),
], "output": "col"}})

# soft warm glow for the travelling crack head
node("tex_head", "texture", {"width": 128, "height": 128, "graph": {"nodes": [
    g("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.02, "falloff": "quadratic"}),
    g("n", "fbm", {"frequency": 3.5, "octaves": 3, "seed": 121}),
    g("nl", "levels", {"in_low": 0.25, "in_high": 0.85, "out_low": 0.55, "out_high": 1.0}, {"a": "n"}),
    g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
    g("col", "colorize", {"gradient": [
        [0.0, [0.1, 0.02, 0.0, 0.0]],
        [0.35, [1.0, 0.36, 0.05, 0.35]],
        [0.75, [1.0, 0.70, 0.22, 0.8]],
        [1.0, [1.0, 0.95, 0.76, 1.0]],
    ]}, {"a": "m"}),
], "output": "col"}})

# dust / smoke puff (ragged, animated)
node("tex_puff", "texture", {"width": 128, "height": 128, "frames": 8, "graph": {"nodes": [
    g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
    g("n", "fbm", {"frequency": 3.2, "octaves": 4, "seed": 5, "animate": 1.0}),
    g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "n"}),
    g("l", "levels", {"in_low": 0.045, "in_high": 0.56}, {"a": "m"}),
], "output": "l"}})

node("tex_grit", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
    g("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
], "output": "r"}})

# soft vent glow that hugs the spike bases
node("tex_vent", "texture", {"width": 128, "height": 128, "graph": {"nodes": [
    g("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.02, "falloff": "smooth"}),
    g("n", "fbm", {"frequency": 5.5, "octaves": 5, "seed": 141}),
    g("nl", "levels", {"in_low": 0.34, "in_high": 0.72}, {"a": "n"}),
    g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
    g("n2", "fbm", {"frequency": 11.0, "octaves": 3, "seed": 143}),
    g("n2l", "levels", {"in_low": 0.38, "in_high": 0.80, "out_low": 0.4, "out_high": 1.0}, {"a": "n2"}),
    g("mx", "math", {"mode": "multiply"}, {"a": "m", "b": "n2l"}),
    g("e", "erosion", {"amount": 0.22, "frequency": 8.0, "seed": 145}, {"a": "mx"}),
    g("b", "blur", {"radius": 2.0}, {"a": "e"}),
    g("l", "levels", {"in_low": 0.10, "in_high": 0.68}, {"a": "b"}),
], "output": "l"}})

# ------------------------------------------------------------ materials ----
node("mat_stone", "material", {"blend": "alpha", "shading": "lit",
                               "base_color": [0.088, 0.060, 0.044, 1.0],
                               "emissive_color": [1.0, 0.36, 0.07, 1.0],
                               "emissive_intensity": 0.03})
node("mat_spike", "material", {"blend": "alpha", "shading": "lit",
                               "base_color": [0.086, 0.058, 0.042, 1.0],
                               "emissive_color": [1.0, 0.34, 0.06, 1.0],
                               "emissive_intensity": 0.055})
node("mat_rubble", "material", {"blend": "alpha", "shading": "lit",
                                "base_color": [0.096, 0.066, 0.049, 1.0],
                                "emissive_color": [1.0, 0.30, 0.05, 1.0],
                                "emissive_intensity": 0.05})
node("mat_dust", "material", {"blend": "alpha", "shading": "lit",
                              "base_color": [0.30, 0.225, 0.165, 1.0],
                              "soft_particle": True, "depth_fade": 0.28,
                              "dissolve": 0.42, "erosion": 0.30})
node("mat_glow", "material", {"blend": "additive", "shading": "unlit",
                              "soft_particle": True, "depth_fade": 0.12})

# --------------------------------------------------------------- meshes ----
node("spike_mesh", "mesh", {"primitive": "crystal", "radius": 0.34, "height": 1.0,
                            "segments": 7, "irregularity": 0.95, "variants": 8,
                            "visible": False})
node("chip_mesh", "mesh", {"primitive": "shard", "radius": 0.34, "height": 0.22,
                           "irregularity": 0.70, "variants": 6, "visible": False})
node("rock_mesh", "mesh", {"primitive": "rock", "radius": 0.5, "segments": 12,
                           "irregularity": 0.72, "variants": 8, "visible": False})

# --------------------------------------------------------------- forces ----
node("f_gravity", "force", {"force_type": "gravity", "strength": 9.81})
node("f_dust_lift", "force", {"force_type": "buoyancy", "strength": 0.55, "temperature": 1.0})
node("f_dust_turb", "force", {"force_type": "turbulence", "strength": 1.6,
                              "frequency": 1.15, "octaves": 3, "speed": 0.7})
node("f_dust_push", "force", {"force_type": "directional", "direction": [1.0, 0.05, 0.0],
                              "strength": step_track([(0.0, 0.0), (1.10, 0.0, "step"),
                                                      (1.16, 2.6), (1.55, 0.9),
                                                      (2.0, 0.25), (2.6, 0.0)])})
node("f_grit_turb", "force", {"force_type": "turbulence", "strength": 1.8,
                              "frequency": 1.7, "octaves": 2, "speed": 1.1})
node("f_vent_curl", "force", {"force_type": "curl_noise", "strength": 1.5,
                              "frequency": 1.8, "octaves": 2, "speed": 1.0})
node("f_ember_lift", "force", {"force_type": "buoyancy", "strength": 1.35, "temperature": 1.0})
node("f_rock_turb", "force", {"force_type": "turbulence", "strength": 1.4,
                              "frequency": 0.65, "octaves": 2, "speed": 0.55})
node("ground", "collider", {"collider_type": "plane", "normal": [0, 1, 0],
                            "bounce": 0.26, "friction": 0.62})

# ================================================== LAYER: shatter path ====
# per-segment scorch (dark earth) then the glowing crack decal on top
SEG_TEX = ["tex_crack_a", "tex_crack_b", "tex_crack_c",
           "tex_crack_b", "tex_crack_a", "tex_crack_c",
           "tex_crack_b", "tex_crack_a"]

# dry plated earth under the whole action so the ground is not a black void
for pi, (pcx, pcz, psz, prot, popa, pt0) in enumerate([
        (-3.4, 0.10, 6.6, 17.0, 0.48, 0.20), (-0.4, 0.05, 7.4, -24.0, 0.44, 0.62),
        (2.9, 0.05, 8.2, 41.0, 0.50, 1.02)]):
    node(f"plates_{pi}", "decal", {
        "shape": "circle", "position": [pcx, r(0.0015 + 0.0004 * pi), pcz],
        "rotation": [0.0, prot, 0.0], "size": [psz, psz],
        "color": [0.36, 0.235, 0.155, 1.0], "blend": "additive",
        "emissive": step_track([(0.0, 0.0), (pt0, 0.0, "step"), (pt0 + 0.16, 0.115),
                                (1.30, 0.20), (1.80, 0.185), (2.25, 0.125), (2.60, 0.075)]),
        "opacity": step_track([(0.0, 0.0), (pt0, 0.0, "step"), (pt0 + 0.18, r(popa)),
                               (2.18, r(popa)), (2.45, r(popa * 0.45)), (2.60, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": r(pt0), "duration": r(DUR - pt0),
    }, {"texture": "tex_plates"}, layer="path")

crack_ids: list[str] = []


def crack_segment(name, cx, cz, ang, length, width, t0, t1, tex, y, bright=1.0,
                  scorch=True, scorch_y=0.004):
    """One crack decal (plus the scorched earth under it) that opens between t0 and t1."""
    if scorch:
        node(f"scorch_{name}", "decal", {
            "shape": "rect",
            "position": [r(cx), r(scorch_y), r(cz)],
            "rotation": [0.0, r(ang), 0.0],
            "size": [r(length * 1.14), r(width * 1.3)],
            "color": [0.022, 0.015, 0.011, 1.0],
            "blend": "alpha",
            "opacity": step_track([(0.0, 0.0), (t0, 0.0, "step"), (t0 + 0.05, 0.52),
                                   (t1 + 0.12, 0.80), (2.20, 0.78), (2.45, 0.42), (2.60, 0.0)]),
            "fade_in": 0.0, "fade_out": 0.0, "start_time": r(t0), "duration": r(DUR - t0),
        }, {"texture": "tex_scorch"}, layer="path")

    nid = f"crack_{name}"
    crack_ids.append(nid)
    node(nid, "decal", {
        "shape": "rect",
        "position": [r(cx), r(y), r(cz)],
        "rotation": [0.0, r(ang), 0.0],
        "size": [r(length), r(width)],
        "color": step_track([
            (0.0, [1.0, 0.94, 0.84, 1.0]), (r(t1 + 0.03), [1.0, 1.0, 1.0, 1.0]),
            (r(t1 + 0.45), [1.0, 0.74, 0.44, 1.0]), (1.35, [1.0, 0.80, 0.50, 1.0]),
            (1.90, [1.0, 0.54, 0.22, 1.0]), (2.25, [1.0, 0.34, 0.09, 1.0]),
            (2.60, [0.75, 0.16, 0.02, 1.0])]),
        "blend": "additive",
        "emissive": step_track([
            (0.0, 0.0), (t0, 0.0, "step"), (t0 + 0.02, r(0.30 * bright)),
            (t1 + 0.03, r(1.25 * bright)), (t1 + 0.16, r(0.44 * bright)),
            (t1 + 0.42, r(0.26 * bright)),
            (1.28, r(0.34 * bright)), (1.60, r(0.22 * bright)),
            (1.92, r(0.14 * bright)), (2.20, r(0.07 * bright)),
            (2.44, r(0.03 * bright)), (2.60, 0.0)]),
        "opacity": step_track([
            (0.0, 0.0), (t0, 0.0, "step"), (t0 + 0.015, 0.42),
            (t0 + (t1 - t0) * 0.5, 0.90), (t1 + 0.03, 1.0), (t1 + 0.18, 0.80),
            (t1 + 0.45, 0.70), (1.32, 0.86), (1.72, 0.68),
            (2.05, 0.44), (2.35, 0.20), (2.56, 0.05), (2.60, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": r(t0), "duration": r(DUR - t0),
    }, {"texture": tex}, layer="path")


for i in range(len(PATH) - 1):
    (x0, z0), (x1, z1) = PATH[i], PATH[i + 1]
    seg_len = math.hypot(x1 - x0, z1 - z0)
    ang = math.degrees(math.atan2(z1 - z0, x1 - x0))
    flip = 180.0 if i % 2 else 0.0        # mirror alternate tiles so no two repeat
    crack_segment(str(i), (x0 + x1) * 0.5, (z0 + z1) * 0.5, ang + flip,
                  seg_len * 1.46, 0.95 + 0.26 * ((i * 5) % 3),
                  HEAD_T[i], HEAD_T[i + 1], SEG_TEX[i],
                  0.014 + 0.0016 * i, 1.0, True, 0.004 + 0.0008 * i)

for j, (idx, side, blen, boff, delay) in enumerate(BRANCH):
    (x0, z0), (x1, z1) = PATH[idx], PATH[idx + 1]
    ang = math.degrees(math.atan2(z1 - z0, x1 - x0))
    bang = ang + side * boff
    bx = x0 + (x1 - x0) * 0.35 + math.cos(math.radians(bang)) * blen * 0.5
    bz = z0 + (z1 - z0) * 0.35 + math.sin(math.radians(bang)) * blen * 0.5
    t0 = HEAD_T[idx] + delay
    crack_segment(f"b{j}", bx, bz, bang + (180.0 if j % 2 else 0.0),
                  blen, 0.78 + 0.18 * (j % 2), t0, t0 + 0.13,
                  SEG_TEX[(j * 2 + 1) % 3], 0.030 + 0.0012 * j, 0.62, False)

# radial crack burst around the cast point (phase 2)
node("cast_web", "decal", {
    "shape": "circle", "position": [r(PATH[0][0]), 0.012, r(PATH[0][1])],
    "rotation": [0.0, 24.0, 0.0], "size": {"value": [1.2, 1.2], "track": [
        {"time": 0.18, "value": [1.2, 1.2]}, {"time": 0.30, "value": [2.5, 2.5]},
        {"time": 0.52, "value": [2.9, 2.9]}, {"time": 2.6, "value": [3.05, 3.05]}]},
    "color": step_track([(0.0, [1.0, 0.96, 0.88, 1.0]), (0.60, [1.0, 0.80, 0.50, 1.0]),
                        (1.40, [1.0, 0.66, 0.32, 1.0]), (2.00, [1.0, 0.44, 0.14, 1.0]),
                        (2.60, [0.75, 0.18, 0.02, 1.0])]),
    "blend": "additive",
    "emissive": step_track([(0.0, 0.0), (0.18, 0.0, "step"), (0.24, 1.7), (0.42, 1.05),
                            (0.80, 0.72), (1.30, 0.80), (1.75, 0.55),
                            (2.20, 0.28), (2.60, 0.02)]),
    "opacity": step_track([(0.0, 0.0), (0.18, 0.0, "step"), (0.23, 0.85), (0.40, 1.0),
                           (1.20, 0.76), (1.80, 0.60), (2.25, 0.32), (2.56, 0.05), (2.60, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": 0.18, "duration": 2.42,
}, {"texture": "tex_castweb"}, layer="path")

# cast rune (phase 1) -- rings + dash bands, deliberately not a crosshair
node("rune", "decal", {
    "shape": "circle", "position": [r(PATH[0][0]), 0.020, r(PATH[0][1])],
    "size": {"value": [1.2, 1.2], "track": [
        {"time": 0.0, "value": [1.2, 1.2]}, {"time": 0.14, "value": [2.34, 2.34]},
        {"time": 0.34, "value": [2.54, 2.54]}, {"time": 1.2, "value": [2.64, 2.64]},
        {"time": 2.6, "value": [2.7, 2.7]}]},
    "rotation": {"value": [0.0, 0.0, 0.0], "track": [
        {"time": 0.0, "value": [0.0, 0.0, 0.0]}, {"time": 2.6, "value": [0.0, 46.0, 0.0]}]},
    "color": [1.0, 0.86, 0.62, 1.0], "blend": "additive",
    "emissive": step_track([(0.0, 0.8), (0.09, 3.4), (0.20, 2.3), (0.45, 1.3),
                            (1.0, 0.75), (1.30, 0.95), (1.9, 0.45), (2.6, 0.0)]),
    "opacity": step_track([(0.0, 0.0), (0.03, 0.35), (0.12, 1.0), (0.30, 0.72),
                           (0.70, 0.42), (1.35, 0.34), (2.0, 0.22), (2.6, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0,
}, {"texture": "tex_rune"}, layer="telegraph")

node("cast_light", "light", {
    "light_type": "point", "position": [r(PATH[0][0]), 1.05, r(PATH[0][1])],
    "color": [1.0, 0.62, 0.22, 1.0], "radius": 6.0,
    "intensity": step_track([(0.0, 0.0), (0.04, 2.4), (0.14, 1.5), (0.34, 0.9),
                             (0.62, 0.35), (0.85, 0.0)]),
    "flicker_amplitude": 0.25, "flicker_frequency": 15.0,
    "duration": 0.9,
}, layer="light")

# ---- the travelling crack head -------------------------------------------
head_pos = [(t, [r(p[0]), 0.0, r(p[1])]) for t, p in zip(HEAD_T, PATH)]
head_pos_y = [(t, [p[0], 0.028, p[2]]) for t, p in head_pos]

node("head_glow", "decal", {
    "shape": "circle",
    "position": {"value": head_pos_y[0][1], "track": (
        [{"time": 0.0, "value": head_pos_y[0][1], "interp": "step"}] +
        [{"time": r(t), "value": v, "interp": "linear"} for t, v in head_pos_y])},
    "size": {"value": [0.4, 0.4], "track": [
        {"time": 0.16, "value": [0.4, 0.4]}, {"time": 0.26, "value": [0.82, 0.62]},
        {"time": 0.80, "value": [0.90, 0.66]}, {"time": 1.06, "value": [1.12, 0.82]},
        {"time": 1.20, "value": [0.5, 0.5]}]},
    "color": [1.0, 0.72, 0.36, 1.0], "blend": "additive",
    "emissive": step_track([(0.0, 0.0), (0.16, 0.0, "step"), (0.22, 1.15), (0.55, 0.95),
                            (0.95, 1.25), (1.09, 1.6), (1.20, 0.0)]),
    "opacity": step_track([(0.0, 0.0), (0.16, 0.0, "step"), (0.21, 0.85), (0.95, 0.95),
                           (1.10, 0.8), (1.20, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": 0.16, "duration": 1.06,
}, {"texture": "tex_head"}, layer="path")

node("head_light", "light", {
    "light_type": "point", "color": [1.0, 0.52, 0.16, 1.0], "radius": 6.0,
    "position": {"value": [head_pos[0][1][0], 0.95, head_pos[0][1][2]], "track": (
        [{"time": 0.0, "value": [head_pos[0][1][0], 0.95, head_pos[0][1][2]], "interp": "step"}] +
        [{"time": r(t), "value": [v[0], 0.95, v[2]], "interp": "linear"} for t, v in head_pos])},
    "intensity": step_track([(0.0, 0.0), (0.16, 0.0, "step"), (0.24, 1.0), (0.50, 0.85),
                             (0.86, 1.2), (1.06, 1.6), (1.18, 0.4), (1.30, 0.0)]),
    "flicker_amplitude": 0.28, "flicker_frequency": 17.0,
    "start_time": 0.16, "duration": 1.16,
}, layer="light")

# grit thrown out of the opening crack
node("ps_pathgrit", "particle_system", {
    "max_particles": 420, "lifetime": 0.85, "lifetime_variance": 0.35,
    "size": 0.035, "size_variance": 0.02,
    "color": [1.0, 0.66, 0.22, 1.0],
    "color_over_life": [[0.0, [1.0, 0.92, 0.66, 1.0]], [0.45, [1.0, 0.42, 0.06, 1.0]],
                        [1.0, [0.35, 0.05, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 1.0], [0.7, 0.9], [1.0, 0.0]],
    "emissive": 5.0, "emissive_over_life": [[0.0, 1.3], [0.5, 0.9], [1.0, 0.3]],
    "drag": 0.5, "render_mode": "stretched_billboard", "velocity_stretch": 0.16,
    "blend": "additive",
}, {"sprite": "tex_grit", "material": "mat_glow",
    "forces": ["f_gravity", "f_grit_turb"], "colliders": ["ground"]}, layer="path")

node("e_pathgrit", "emitter", {
    "shape": "sphere", "radius": 0.22,
    "position": {"value": [head_pos[0][1][0], 0.08, head_pos[0][1][2]], "track": (
        [{"time": 0.0, "value": [head_pos[0][1][0], 0.08, head_pos[0][1][2]], "interp": "step"}] +
        [{"time": r(t), "value": [v[0], 0.08, v[2]], "interp": "linear"} for t, v in head_pos])},
    "rate": step_track([(0.0, 0.0), (0.16, 0.0, "step"), (0.22, 260.0), (0.55, 210.0),
                        (0.90, 300.0), (1.08, 340.0), (1.14, 0.0, "step"), (2.6, 0.0)]),
    "velocity": 3.4, "velocity_variance": 2.2, "direction": [0, 1, 0], "spread": 55,
    "start_time": 0.16, "duration": 1.0,
}, {"particle": "ps_pathgrit"}, layer="path")

node("e_cast_spark", "emitter", {
    "shape": "ring", "radius": 1.05, "inner_radius": 0.25,
    "position": [r(PATH[0][0]), 0.06, r(PATH[0][1])],
    "rate": step_track([(0.0, 90.0), (0.22, 60.0), (0.42, 0.0, "step"), (2.6, 0.0)]),
    "burst_count": 46, "burst_times": [0.02],
    "velocity": 2.6, "velocity_variance": 1.6, "direction": [0, 1, 0], "spread": 60,
    "duration": 0.45,
}, {"particle": "ps_pathgrit"}, layer="telegraph")

# dust kicked up along the path
node("ps_pathdust", "particle_system", {
    "max_particles": 420, "lifetime": 1.6, "lifetime_variance": 0.55,
    "size": 0.62, "size_variance": 0.30,
    "size_over_life": [[0.0, 0.28], [1.0, 2.0]],
    "color": [0.245, 0.178, 0.126, 1.0],
    "opacity": 0.40, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.65, 0.7], [1.0, 0.0]],
    "emissive": 0.50, "emissive_over_life": [[0.0, 1.5], [0.4, 1.0], [1.0, 0.35]],
    "rotation_variance": 180.0, "angular_velocity_variance": 30.0,
    "drag": 1.5, "blend": "alpha", "sort": True, "sprite_fps": 9.0,
}, {"sprite": "tex_puff", "material": "mat_dust",
    "forces": ["f_dust_lift", "f_dust_turb"]}, layer="dust")

node("e_pathdust", "emitter", {
    "shape": "sphere", "radius": 0.3,
    "position": {"value": [head_pos[0][1][0], 0.10, head_pos[0][1][2]], "track": (
        [{"time": 0.0, "value": [head_pos[0][1][0], 0.10, head_pos[0][1][2]], "interp": "step"}] +
        [{"time": r(t), "value": [v[0], 0.10, v[2]], "interp": "linear"} for t, v in head_pos])},
    "rate": step_track([(0.0, 0.0), (0.16, 0.0, "step"), (0.22, 120.0), (0.60, 105.0),
                        (0.95, 165.0), (1.08, 185.0), (1.14, 0.0, "step"), (2.6, 0.0)]),
    "velocity": 1.5, "velocity_variance": 0.9, "direction": [0, 1, 0], "spread": 62,
    "start_time": 0.16, "duration": 1.0,
}, {"particle": "ps_pathdust"}, layer="dust")

# small rocks heaving up along the path (phase 4 "approach" mostly)
node("ps_pathrock", "particle_system", {
    "max_particles": 42, "lifetime": 1.55, "lifetime_variance": 0.4,
    "size": 0.19, "size_variance": 0.10,
    "size_over_life": [[0.0, 0.55], [0.06, 1.0], [0.9, 1.0], [1.0, 0.72]],
    "angular_velocity": 150.0, "angular_velocity_variance": 140.0,
    "render_mode": "mesh", "blend": "alpha", "drag": 0.55, "mass": 2.0,
    "orientation": "tumble", "mesh_scale": [1.08, 0.74, 1.0],
    "mesh_scale_variance": [0.34, 0.3, 0.34],
    "physics": "rigid", "bounce": 0.26, "friction": 0.64,
}, {"mesh": "rock_mesh", "material": "mat_rubble",
    "forces": ["f_gravity", "f_rock_turb"], "colliders": ["ground"]}, layer="debris")

node("e_pathrock", "emitter", {
    "shape": "sphere", "radius": 0.26,
    "position": {"value": [head_pos[0][1][0], 0.06, head_pos[0][1][2]], "track": (
        [{"time": 0.0, "value": [head_pos[0][1][0], 0.06, head_pos[0][1][2]], "interp": "step"}] +
        [{"time": r(t), "value": [v[0], 0.06, v[2]], "interp": "linear"} for t, v in head_pos])},
    "rate": step_track([(0.0, 0.0), (0.30, 0.0, "step"), (0.36, 9.0), (0.62, 14.0),
                        (0.84, 34.0), (1.06, 46.0), (1.13, 0.0, "step"), (2.6, 0.0)]),
    "velocity": 3.3, "velocity_variance": 1.7, "direction": [0, 1, 0], "spread": 34,
    "start_time": 0.30, "duration": 0.85,
}, {"particle": "ps_pathrock"}, layer="debris")

# ==================================================== LAYER: eruption ======
TX, TZ = TARGET

# target crater crack burst
node("crater_scorch", "decal", {
    "shape": "circle", "position": [r(TX), 0.005, r(TZ)], "size": [5.6, 5.6],
    "color": [0.019, 0.013, 0.009, 1.0], "blend": "alpha",
    "opacity": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.20, 0.72), (1.6, 0.86),
                           (2.25, 0.84), (2.48, 0.42), (2.60, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": 1.10, "duration": 1.5,
}, {"texture": "tex_crater_scorch"}, layer="eruption")

node("crater_web", "decal", {
    "shape": "circle", "position": [r(TX), 0.030, r(TZ)],
    "rotation": {"value": [0.0, 13.0, 0.0], "track": [
        {"time": 0.0, "value": [0.0, 13.0, 0.0]}, {"time": 2.6, "value": [0.0, 6.0, 0.0]}]},
    "size": {"value": [1.6, 1.6], "track": [
        {"time": 1.08, "value": [1.6, 1.6]}, {"time": 1.19, "value": [4.4, 4.4]},
        {"time": 1.45, "value": [5.0, 5.0]}, {"time": 2.6, "value": [5.2, 5.2]}]},
    "color": step_track([(0.0, [1.0, 1.0, 1.0, 1.0]), (1.55, [1.0, 0.86, 0.58, 1.0]),
                        (1.95, [1.0, 0.60, 0.26, 1.0]), (2.30, [1.0, 0.38, 0.10, 1.0]),
                        (2.60, [0.78, 0.18, 0.02, 1.0])]),
    "blend": "additive",
    "emissive": step_track([(0.0, 0.0), (1.08, 0.0, "step"), (1.15, 1.55), (1.32, 0.95),
                            (1.58, 1.15), (1.85, 0.85), (2.15, 0.55),
                            (2.42, 0.24), (2.60, 0.02)]),
    "opacity": step_track([(0.0, 0.0), (1.08, 0.0, "step"), (1.14, 0.95), (1.40, 1.0),
                           (1.85, 0.88), (2.20, 0.62), (2.45, 0.28), (2.60, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": 1.08, "duration": 1.52,
}, {"texture": "tex_crater"}, layer="eruption")

# expanding ground shock ring
node("shock_ring", "decal", {
    "shape": "circle", "position": [r(TX), 0.038, r(TZ)],
    "rotation": [0.0, 31.0, 0.0],
    "size": {"value": [0.9, 0.9], "track": [
        {"time": 1.10, "value": [0.9, 0.9]}, {"time": 1.20, "value": [3.4, 3.4]},
        {"time": 1.34, "value": [5.6, 5.6]}, {"time": 1.52, "value": [7.4, 7.4]},
        {"time": 1.75, "value": [8.6, 8.6]}]},
    "color": [1.0, 0.72, 0.34, 1.0], "blend": "additive",
    "emissive": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.14, 2.6), (1.30, 1.5),
                            (1.52, 0.7), (1.75, 0.0)]),
    "opacity": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.13, 1.0), (1.36, 0.72),
                           (1.58, 0.34), (1.75, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": 1.10, "duration": 0.66,
}, {"texture": "tex_ring"}, layer="eruption")

# ---- hero spikes ---------------------------------------------------------
# (azimuth deg, radius, height, base radius, tilt deg, t_burst, irregularity)
HERO = [
    (0.0, 0.00, 3.90, 0.86, 4.0, 1.40, 0.88),     # central hero spike
    (22.0, 0.98, 2.60, 0.64, 22.0, 1.12, 0.92),
    (68.0, 1.22, 1.80, 0.54, 33.0, 1.20, 0.82),
    (121.0, 0.92, 2.88, 0.68, 18.0, 1.16, 0.95),
    (163.0, 1.38, 1.48, 0.48, 40.0, 1.30, 0.78),
    (201.0, 1.06, 2.32, 0.60, 28.0, 1.24, 0.88),
    (245.0, 1.47, 1.66, 0.50, 37.0, 1.34, 0.95),
    (294.0, 0.88, 3.00, 0.71, 16.0, 1.46, 0.84),
    (335.0, 1.26, 2.00, 0.54, 30.0, 1.27, 0.90),
    (146.0, 2.05, 1.12, 0.41, 46.0, 1.37, 0.86),
    (312.0, 2.15, 1.04, 0.39, 49.0, 1.42, 0.92),
]

for i, (az, rad, h, base_r, tilt, tb, irr) in enumerate(HERO):
    phi = math.radians(az)
    px = TX + rad * math.cos(phi)
    pz = TZ + rad * math.sin(phi)
    # crystal is centred: base at -h/2.  Resting centre keeps the base just under
    # the ground, and the lean pulls the apex outward.
    rest_y = h * 0.5 * math.cos(math.radians(tilt)) - 0.17
    down_y = rest_y - h * 1.08
    sink_y = rest_y - h * 0.40
    rot = [r(tilt), r(90.0 - az), 0.0]
    seg = 6 + (i % 3)
    # a slab cross-section, not a round needle: x and z are deliberately unequal
    sx = 0.92 + 0.42 * rng.random()
    sz = 0.46 + 0.30 * rng.random()
    node(f"spike_{i}", "mesh", {
        "primitive": "crystal", "radius": r(base_r), "height": r(h),
        "segments": seg, "irregularity": r(irr),
        "color": [r(0.88 + 0.16 * rng.random()), r(0.70 + 0.16 * rng.random()),
                  r(0.56 + 0.16 * rng.random()), 1.0],
        "rotation": rot,
        "position": step_track([
            (r(tb - 0.02), [r(px), r(down_y), r(pz)], "step"),
            (r(tb), [r(px), r(down_y), r(pz)], "smooth"),
            (r(tb + 0.13), [r(px), r(rest_y + h * 0.055), r(pz)], "smooth"),
            (r(tb + 0.21), [r(px), r(rest_y), r(pz)], "smooth"),
            (2.34, [r(px), r(rest_y), r(pz)], "smooth"),
            (2.58, [r(px), r(sink_y), r(pz)]),
        ]),
        "scale": step_track([
            (r(tb - 0.02), [r(sx * 0.62), 1.0, r(sz * 0.62)], "smooth"),
            (r(tb + 0.10), [r(sx * 1.14), 1.0, r(sz * 1.14)], "smooth"),
            (r(tb + 0.20), [r(sx), 1.0, r(sz)], "smooth"),
            (2.34, [r(sx), 1.0, r(sz)], "smooth"),
            (2.52, [r(sx * 0.88), 0.95, r(sz * 0.88)], "smooth"),
            (2.56, [r(sx * 0.42), 0.72, r(sz * 0.42)], "smooth"),
            (2.60, [r(sx * 0.04), 0.30, r(sz * 0.04)]),
        ]),
        "emissive": step_track([
            (r(tb - 0.02), 0.0, "step"), (r(tb + 0.05), 0.26), (r(tb + 0.22), 0.11),
            (1.62, 0.15), (1.90, 0.10), (2.20, 0.06), (2.45, 0.02), (2.59, 0.0)]),
        "start_time": r(max(0.0, tb - 0.03)), "duration": r(DUR - max(0.0, tb - 0.03)),
    }, {"material": "mat_stone"}, layer="eruption", seed=2200 + i * 37)

# ---- crown fillers (mesh particles) --------------------------------------
node("ps_crown", "particle_system", {
    "max_particles": 26, "lifetime": 1.26, "lifetime_variance": 0.07,
    "size": 1.38, "size_variance": 0.52,
    "size_over_life": [[0.0, 0.18], [0.07, 1.10], [0.13, 1.0], [0.82, 1.0],
                       [0.93, 0.62], [1.0, 0.0]],
    "render_mode": "mesh", "blend": "alpha", "drag": 8.5,
    "orientation": "upright", "tilt": 34.0, "rotation_variance": 180.0,
    "mesh_scale": [0.80, 1.48, 0.46], "mesh_scale_variance": [0.26, 0.58, 0.16],
}, {"mesh": "spike_mesh", "material": "mat_spike"}, layer="eruption")

node("e_crown", "emitter", {
    "shape": "ring", "radius": 2.20, "inner_radius": 0.95,
    "position": [r(TX), -0.62, r(TZ)],
    "rate": 0, "burst_count": 4, "burst_times": [0.0, 0.06, 0.13, 0.21, 0.30],
    "velocity": 5.4, "velocity_variance": 1.5, "direction": [0, 1, 0], "spread": 9,
    "start_time": 1.12, "duration": 0.45,
}, {"particle": "ps_crown"}, layer="eruption")

node("ps_crown_small", "particle_system", {
    "max_particles": 22, "lifetime": 1.24, "lifetime_variance": 0.07,
    "size": 0.74, "size_variance": 0.30,
    "size_over_life": [[0.0, 0.18], [0.07, 1.12], [0.13, 1.0], [0.80, 1.0],
                       [0.92, 0.60], [1.0, 0.0]],
    "render_mode": "mesh", "blend": "alpha", "drag": 9.0,
    "orientation": "upright", "tilt": 42.0, "rotation_variance": 180.0,
    "mesh_scale": [0.86, 1.28, 0.50], "mesh_scale_variance": [0.28, 0.46, 0.18],
}, {"mesh": "spike_mesh", "material": "mat_spike"}, layer="eruption")

node("e_crown_small", "emitter", {
    "shape": "ring", "radius": 2.60, "inner_radius": 1.70,
    "position": [r(TX), -0.42, r(TZ)],
    "rate": 0, "burst_count": 3, "burst_times": [0.0, 0.08, 0.17, 0.27],
    "velocity": 4.2, "velocity_variance": 1.3, "direction": [0, 1, 0], "spread": 12,
    "start_time": 1.14, "duration": 0.42,
}, {"particle": "ps_crown_small"}, layer="eruption")

# ---- heaved ground slabs: plates of earth tipped up around the crown ------
node("ps_slab", "particle_system", {
    "max_particles": 20, "lifetime": 1.28, "lifetime_variance": 0.08,
    "size": 0.95, "size_variance": 0.36,
    "size_over_life": [[0.0, 0.25], [0.08, 1.08], [0.15, 1.0], [0.84, 1.0],
                       [0.94, 0.66], [1.0, 0.0]],
    "render_mode": "mesh", "blend": "alpha", "drag": 9.5,
    "orientation": "upright", "tilt": 62.0, "rotation_variance": 180.0,
    "mesh_scale": [1.55, 0.30, 1.25], "mesh_scale_variance": [0.5, 0.12, 0.45],
}, {"mesh": "rock_mesh", "material": "mat_spike"}, layer="eruption")

node("e_slab", "emitter", {
    "shape": "ring", "radius": 3.0, "inner_radius": 1.4,
    "position": [r(TX), -0.30, r(TZ)],
    "rate": 0, "burst_count": 5, "burst_times": [0.0, 0.07, 0.16],
    "velocity": 2.6, "velocity_variance": 1.1, "direction": [0, 1, 0], "spread": 16,
    "start_time": 1.12, "duration": 0.3,
}, {"particle": "ps_slab"}, layer="eruption")

# rubble collar: chunks that settle around the spike bases and hide the ground join
node("ps_collar", "particle_system", {
    "max_particles": 34, "lifetime": 1.35, "lifetime_variance": 0.1,
    "size": 0.23, "size_variance": 0.13,
    "size_over_life": [[0.0, 0.3], [0.08, 1.05], [0.15, 1.0], [0.86, 1.0],
                       [0.95, 0.6], [1.0, 0.0]],
    "angular_velocity": 40.0, "angular_velocity_variance": 90.0,
    "render_mode": "mesh", "blend": "alpha", "drag": 7.0, "mass": 2.0,
    "orientation": "tumble", "mesh_scale": [1.1, 0.7, 1.05],
    "mesh_scale_variance": [0.36, 0.3, 0.36],
}, {"mesh": "rock_mesh", "material": "mat_spike"}, layer="eruption")

node("e_collar", "emitter", {
    "shape": "ring", "radius": 2.1, "inner_radius": 0.55,
    "position": [r(TX), -0.16, r(TZ)],
    "rate": 0, "burst_count": 8, "burst_times": [0.0, 0.08, 0.18, 0.28],
    "velocity": 1.6, "velocity_variance": 0.8, "direction": [0, 1, 0], "spread": 24,
    "start_time": 1.12, "duration": 0.4,
}, {"particle": "ps_collar"}, layer="eruption")

# ---- impact: flash, debris, grit, dust ------------------------------------
node("impact_flash", "light", {
    "light_type": "point", "position": [r(TX), 2.7, r(TZ + 0.3)],
    "color": [1.0, 0.68, 0.32, 1.0], "radius": 16.0,
    "intensity": step_track([(0.0, 0.0), (1.09, 0.0, "step"), (1.14, 13.0),
                             (1.26, 3.0), (1.40, 0.0)]),
    "start_time": 1.09, "duration": 0.35,
}, layer="light")

node("erupt_light_a", "light", {
    "light_type": "point", "position": [r(TX - 2.30), 1.55, r(TZ + 1.90)],
    "color": [1.0, 0.44, 0.11, 1.0], "radius": 11.0,
    "intensity": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.18, 2.4), (1.55, 2.9),
                             (1.85, 2.4), (2.15, 1.5), (2.45, 0.6), (2.60, 0.0)]),
    "flicker_amplitude": 0.20, "flicker_frequency": 9.0,
    "start_time": 1.10, "duration": 1.5,
}, layer="light")

node("erupt_light_b", "light", {
    "light_type": "point", "position": [r(TX + 2.40), 1.70, r(TZ - 1.50)],
    "color": [1.0, 0.52, 0.16, 1.0], "radius": 11.0,
    "intensity": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.20, 0.7), (1.58, 0.9),
                             (1.90, 0.7), (2.20, 0.45), (2.48, 0.16), (2.60, 0.0)]),
    "flicker_amplitude": 0.24, "flicker_frequency": 12.0,
    "start_time": 1.10, "duration": 1.5,
}, layer="light")

# high, wide warm fill so the dark rock separates from the background
node("erupt_light_c", "light", {
    "light_type": "point", "position": [r(TX - 0.6), 3.10, r(TZ + 2.20)],
    "color": [1.0, 0.58, 0.24, 1.0], "radius": 15.0,
    "intensity": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.20, 0.8), (1.60, 0.95),
                             (1.95, 0.75), (2.25, 0.45), (2.50, 0.16), (2.60, 0.0)]),
    "flicker_amplitude": 0.14, "flicker_frequency": 7.0,
    "start_time": 1.10, "duration": 1.5,
}, layer="light")

# the molten pool inside the crown: low, weak, and it up-lights the inner faces
node("erupt_core", "light", {
    "light_type": "point", "position": [r(TX), 0.45, r(TZ)],
    "color": [1.0, 0.36, 0.06, 1.0], "radius": 4.5,
    "intensity": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.16, 2.6), (1.55, 2.3),
                             (1.90, 1.8), (2.20, 1.05), (2.48, 0.32), (2.60, 0.0)]),
    "flicker_amplitude": 0.30, "flicker_frequency": 14.0,
    "start_time": 1.10, "duration": 1.5,
}, layer="light")

node("ps_debris", "particle_system", {
    "max_particles": 74, "lifetime": 1.55, "lifetime_variance": 0.45,
    "size": 0.27, "size_variance": 0.16,
    "size_over_life": [[0.0, 0.7], [0.05, 1.0], [0.92, 1.0], [1.0, 0.7]],
    "angular_velocity": 190.0, "angular_velocity_variance": 170.0,
    "render_mode": "mesh", "blend": "alpha", "drag": 0.85, "mass": 2.6,
    "orientation": "tumble", "mesh_scale": [1.06, 0.72, 1.02],
    "mesh_scale_variance": [0.36, 0.32, 0.36],
    "physics": "rigid", "bounce": 0.3, "friction": 0.58,
}, {"mesh": "rock_mesh", "material": "mat_rubble",
    "forces": ["f_gravity", "f_rock_turb"], "colliders": ["ground"]}, layer="debris")

node("e_debris", "emitter", {
    "shape": "disc", "radius": 1.5, "position": [r(TX), 0.20, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.16, 54.0), (1.45, 28.0),
                        (1.78, 0.0, "step"), (2.22, 0.0, "step"), (2.30, 26.0),
                        (2.52, 0.0, "step"), (2.6, 0.0)]),
    "burst_count": 40, "burst_times": [0.02],
    "velocity": 5.0, "velocity_variance": 2.4, "direction": [0, 1, 0], "spread": 40,
    "start_time": 1.10, "duration": 1.45,
}, {"particle": "ps_debris"}, layer="debris")

node("ps_chip", "particle_system", {
    "max_particles": 38, "lifetime": 1.35, "lifetime_variance": 0.4,
    "size": 0.17, "size_variance": 0.09,
    "size_over_life": [[0.0, 0.6], [0.05, 1.0], [0.9, 1.0], [1.0, 0.6]],
    "angular_velocity": 280.0, "angular_velocity_variance": 220.0,
    "render_mode": "mesh", "blend": "alpha", "drag": 0.9, "mass": 1.1,
    "orientation": "tumble", "mesh_scale": [1.0, 0.9, 1.0],
    "mesh_scale_variance": [0.38, 0.32, 0.38],
    "physics": "rigid", "bounce": 0.36, "friction": 0.5,
}, {"mesh": "chip_mesh", "material": "mat_rubble",
    "forces": ["f_gravity", "f_rock_turb"], "colliders": ["ground"]}, layer="debris")

node("e_chip", "emitter", {
    "shape": "disc", "radius": 2.0, "position": [r(TX), 0.15, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.16, 46.0), (1.55, 16.0),
                        (1.9, 0.0, "step"), (2.6, 0.0)]),
    "burst_count": 26, "burst_times": [0.02],
    "velocity": 5.8, "velocity_variance": 2.8, "direction": [0, 1, 0], "spread": 54,
    "start_time": 1.10, "duration": 0.85,
}, {"particle": "ps_chip"}, layer="debris")

node("ps_sparks", "particle_system", {
    "max_particles": 900, "lifetime": 1.0, "lifetime_variance": 0.5,
    "size": 0.04, "size_variance": 0.022,
    "color": [1.0, 0.66, 0.22, 1.0],
    "color_over_life": [[0.0, [1.0, 0.94, 0.70, 1.0]], [0.4, [1.0, 0.44, 0.07, 1.0]],
                        [1.0, [0.38, 0.05, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 1.0], [0.75, 0.95], [1.0, 0.0]],
    "emissive": 6.0, "emissive_over_life": [[0.0, 1.35], [0.5, 0.9], [1.0, 0.25]],
    "drag": 0.42, "render_mode": "stretched_billboard", "velocity_stretch": 0.18,
    "blend": "additive",
}, {"sprite": "tex_grit", "material": "mat_glow",
    "forces": ["f_gravity", "f_grit_turb"], "colliders": ["ground"]}, layer="dust")

node("e_sparks", "emitter", {
    "shape": "disc", "radius": 1.6, "position": [r(TX), 0.25, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.16, 540.0), (1.45, 280.0),
                        (1.85, 140.0), (2.20, 50.0), (2.55, 0.0)]),
    "burst_count": 330, "burst_times": [0.02],
    "velocity": 6.0, "velocity_variance": 3.4, "direction": [0, 1, 0], "spread": 46,
    "start_time": 1.10, "duration": 1.45,
}, {"particle": "ps_sparks"}, layer="dust")

# molten vent glow hugging the spike bases
node("ps_vent", "particle_system", {
    "max_particles": 560, "lifetime": 0.55, "lifetime_variance": 0.22,
    "size": 0.155, "size_variance": 0.095,
    "size_over_life": [[0.0, 0.5], [0.3, 1.0], [1.0, 0.45]],
    "color": [1.0, 0.40, 0.08, 1.0],
    "color_over_life": [[0.0, [1.0, 0.92, 0.66, 1.0]], [0.35, [1.0, 0.48, 0.09, 1.0]],
                        [1.0, [0.45, 0.07, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 0.0], [0.18, 1.0], [0.6, 0.8], [1.0, 0.0]],
    "emissive": 1.45, "opacity": 0.8,
    "emissive_over_life": [[0.0, 1.2], [0.4, 1.0], [1.0, 0.4]],
    "rotation_variance": 180.0, "angular_velocity_variance": 40.0,
    "drag": 2.4, "blend": "additive",
}, {"sprite": "tex_vent", "material": "mat_glow", "forces": ["f_vent_curl"]}, layer="eruption")

node("e_vent", "emitter", {
    "shape": "disc", "radius": 1.75, "position": [r(TX), 0.07, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.16, 640.0), (1.50, 520.0),
                        (1.85, 380.0), (2.15, 240.0), (2.42, 85.0), (2.58, 0.0)]),
    "burst_count": 140, "burst_times": [0.02],
    "velocity": 0.95, "velocity_variance": 0.6, "direction": [0, 1, 0], "spread": 34,
    "start_time": 1.10, "duration": 1.48,
}, {"particle": "ps_vent"}, layer="eruption")

node("e_vent_flank", "emitter", {
    "shape": "ring", "radius": 1.45, "inner_radius": 0.30,
    "position": [r(TX), 0.62, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.14, 0.0, "step"), (1.20, 260.0), (1.55, 200.0),
                        (1.90, 145.0), (2.18, 85.0), (2.44, 30.0), (2.58, 0.0)]),
    "velocity": 0.75, "velocity_variance": 0.5, "direction": [0, 1, 0], "spread": 55,
    "start_time": 1.14, "duration": 1.44,
}, {"particle": "ps_vent"}, layer="eruption")

node("ps_ember", "particle_system", {
    "max_particles": 360, "lifetime": 1.5, "lifetime_variance": 0.6,
    "size": 0.035, "size_variance": 0.018,
    "color": [1.0, 0.52, 0.12, 1.0],
    "color_over_life": [[0.0, [1.0, 0.82, 0.42, 1.0]], [0.5, [1.0, 0.38, 0.05, 1.0]],
                        [1.0, [0.3, 0.03, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.7, 0.85], [1.0, 0.0]],
    "emissive": 4.2, "drag": 0.8, "blend": "additive",
    "render_mode": "stretched_billboard", "velocity_stretch": 0.1,
}, {"sprite": "tex_grit", "material": "mat_glow",
    "forces": ["f_ember_lift", "f_grit_turb"]}, layer="dust")

node("e_ember", "emitter", {
    "shape": "disc", "radius": 2.1, "position": [r(TX), 0.3, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.12, 0.0, "step"), (1.20, 150.0), (1.60, 130.0),
                        (2.05, 95.0), (2.40, 40.0), (2.58, 0.0)]),
    "velocity": 1.9, "velocity_variance": 1.2, "direction": [0, 1, 0], "spread": 50,
    "start_time": 1.12, "duration": 1.46,
}, {"particle": "ps_ember"}, layer="dust")

# rolling brown dust from the impact
node("ps_dust", "particle_system", {
    "max_particles": 1100, "lifetime": 1.7, "lifetime_variance": 0.6,
    "size": 0.72, "size_variance": 0.34,
    "size_over_life": [[0.0, 0.3], [1.0, 2.1]],
    "color": [0.225, 0.163, 0.118, 1.0],
    "opacity": 0.33, "opacity_over_life": [[0.0, 0.0], [0.22, 1.0], [0.68, 0.72], [1.0, 0.0]],
    "emissive": 0.55, "emissive_over_life": [[0.0, 1.6], [0.4, 1.0], [1.0, 0.3]],
    "rotation_variance": 180.0, "angular_velocity": 14.0, "angular_velocity_variance": 26.0,
    "drag": 1.35, "blend": "alpha", "sort": True, "sprite_fps": 8.0,
}, {"sprite": "tex_puff", "material": "mat_dust",
    "forces": ["f_dust_lift", "f_dust_turb", "f_dust_push"]}, layer="dust")

node("e_dust_ring", "emitter", {
    "shape": "ring", "radius": 2.2, "inner_radius": 0.4, "position": [r(TX), 0.18, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.10, 0.0, "step"), (1.18, 480.0), (1.55, 340.0),
                        (1.95, 240.0), (2.30, 140.0), (2.58, 0.0)]),
    "burst_count": 190, "burst_times": [0.03],
    "velocity": 2.9, "velocity_variance": 1.6, "direction": [0, 0, 0], "spread": 30,
    "start_time": 1.10, "duration": 1.48,
}, {"particle": "ps_dust"}, layer="dust")

node("e_dust_plume", "emitter", {
    "shape": "disc", "radius": 1.3, "position": [r(TX), 0.9, r(TZ)],
    "rate": step_track([(0.0, 0.0), (1.12, 0.0, "step"), (1.22, 210.0), (1.62, 175.0),
                        (2.05, 120.0), (2.40, 60.0), (2.58, 0.0)]),
    "velocity": 2.3, "velocity_variance": 1.3, "direction": [0, 1, 0], "spread": 42,
    "start_time": 1.12, "duration": 1.46,
}, {"particle": "ps_dust"}, layer="dust")

# ---------------------------------------------------------------- effect ---
effect = {
    "schema_version": "0.1.0",
    "name": "Earth Shatter",
    "description": "A shatter path of glowing cracks races from the cast point to the target "
                   "and erupts into a crown of molten-seamed rock spikes.",
    "duration": DUR,
    "seed": SEED,
    "timeline": {"phases": [
        {"name": "cast", "start": 0.0, "end": 0.2},
        {"name": "cracks", "start": 0.2, "end": 0.5},
        {"name": "path", "start": 0.5, "end": 0.8},
        {"name": "approach", "start": 0.8, "end": 1.1},
        {"name": "impact", "start": 1.1, "end": 1.4},
        {"name": "eruption", "start": 1.4, "end": 1.8},
        {"name": "linger", "start": 1.8, "end": 2.2},
        {"name": "fade", "start": 2.2, "end": 2.6},
    ]},
    "layers": [
        {"id": "telegraph", "name": "Cast rune", "role": "telegraph"},
        {"id": "path", "name": "Shatter path", "role": "ignition"},
        {"id": "eruption", "name": "Spike eruption", "role": "primary"},
        {"id": "debris", "name": "Debris", "role": "secondary"},
        {"id": "dust", "name": "Dust and embers", "role": "secondary"},
        {"id": "light", "name": "Light", "role": "interaction"},
    ],
    "nodes": nodes,
    "controls": [
        {"id": "path_glow", "label": "Path glow", "group": "Shatter path",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": ([{"node": cid, "parameter": "emissive", "op": "multiply"}
                       for cid in crack_ids] +
                      [{"node": "cast_web", "parameter": "emissive", "op": "multiply"},
                       {"node": "crater_web", "parameter": "emissive", "op": "multiply"},
                       {"node": "head_glow", "parameter": "emissive", "op": "multiply"},
                       {"node": "head_light", "parameter": "intensity", "op": "multiply"},
                       {"node": "rune", "parameter": "emissive", "op": "multiply"},
                       {"node": "cast_light", "parameter": "intensity", "op": "multiply"}])},
        {"id": "spike_height", "label": "Spike height", "group": "Spike eruption",
         "min": 0.3, "max": 2.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": ([{"node": f"spike_{i}", "parameter": "height", "op": "multiply"}
                       for i in range(len(HERO))] +
                      [{"node": "spike_mesh", "parameter": "height", "op": "multiply"}])},
        {"id": "spike_density", "label": "Spike count", "group": "Spike eruption",
         "min": 0.0, "max": 2.5, "default": 1.0, "value": 1.0, "step": 0.05, "unit": "x",
         "bindings": [{"node": "e_crown", "parameter": "burst_count", "op": "multiply"},
                      {"node": "e_crown_small", "parameter": "burst_count", "op": "multiply"},
                      {"node": "e_slab", "parameter": "burst_count", "op": "multiply"},
                      {"node": "e_collar", "parameter": "burst_count", "op": "multiply"}]},
        {"id": "molten_glow", "label": "Molten glow", "group": "Spike eruption",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": [{"node": "ps_vent", "parameter": "emissive", "op": "multiply"},
                      {"node": "ps_vent", "parameter": "color", "op": "multiply"},
                      {"node": "ps_ember", "parameter": "emissive", "op": "multiply"},
                      {"node": "erupt_light_a", "parameter": "intensity", "op": "multiply"},
                      {"node": "erupt_light_b", "parameter": "intensity", "op": "multiply"},
                      {"node": "erupt_light_c", "parameter": "intensity", "op": "multiply"},
                      {"node": "erupt_core", "parameter": "intensity", "op": "multiply"},
                      {"node": "mat_stone", "parameter": "emissive_intensity", "op": "multiply"},
                      {"node": "mat_spike", "parameter": "emissive_intensity", "op": "multiply"}]},
        {"id": "debris_amount", "label": "Debris amount", "group": "Debris",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": [{"node": "e_debris", "parameter": "rate", "op": "multiply"},
                      {"node": "e_debris", "parameter": "burst_count", "op": "multiply"},
                      {"node": "e_chip", "parameter": "rate", "op": "multiply"},
                      {"node": "e_chip", "parameter": "burst_count", "op": "multiply"},
                      {"node": "e_pathrock", "parameter": "rate", "op": "multiply"}]},
        {"id": "dust_amount", "label": "Dust amount", "group": "Dust and embers",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": [{"node": "e_dust_ring", "parameter": "rate", "op": "multiply"},
                      {"node": "e_dust_ring", "parameter": "burst_count", "op": "multiply"},
                      {"node": "e_dust_plume", "parameter": "rate", "op": "multiply"},
                      {"node": "e_pathdust", "parameter": "rate", "op": "multiply"}]},
        {"id": "spark_amount", "label": "Spark amount", "group": "Dust and embers",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": [{"node": "e_sparks", "parameter": "rate", "op": "multiply"},
                      {"node": "e_sparks", "parameter": "burst_count", "op": "multiply"},
                      {"node": "e_ember", "parameter": "rate", "op": "multiply"},
                      {"node": "e_pathgrit", "parameter": "rate", "op": "multiply"},
                      {"node": "e_cast_spark", "parameter": "rate", "op": "multiply"},
                      {"node": "e_cast_spark", "parameter": "burst_count", "op": "multiply"}]},
        {"id": "impact_flash", "label": "Impact flash", "group": "Light",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": [{"node": "impact_flash", "parameter": "intensity", "op": "multiply"},
                      {"node": "shock_ring", "parameter": "emissive", "op": "multiply"}]},
    ],
    "metadata": {
        "reference": "out/references/earth_shatter_sheet.jpg",
        "prompt": "lets create the earth shatter effect, it should start off as a path and "
                  "erupt in the end (on the target)",
        "render_settings": {
            "ground_albedo": 0.13,
            "background": [0.0, 0.0, 0.0, 1.0],
            "bloom_intensity": 0.18,
            "bloom_radius": 0.05,
            "exposure": 0.92,
            "grid": False,
        },
        "layout": {"cast_point": [PATH[0][0], 0.0, PATH[0][1]],
                   "target": [TARGET[0], 0.0, TARGET[1]],
                   "path": [[p[0], 0.0, p[1]] for p in PATH]},
    },
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(effect, indent=1) + "\n")
print(f"wrote {OUT}  ({len(nodes)} nodes)")
