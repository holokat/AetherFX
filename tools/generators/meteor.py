#!/usr/bin/env python3
"""Build examples/effects/meteor.json ("Meteor", a vertical projectile AOE).

    python tools/generators/meteor.py --out examples/effects

Layout (metres, +y up, ground y = 0, camera on +z):

* the target is the origin, the AOE radius is 3.5 m;
* the sky portal SKY sits 15 m up and 5.5 m to the left (and a little behind), so the
  descent is near vertical and crosses the frame on a slight diagonal;
* the meteor rides an invisible `rig` whose position is a sampled ease-in curve from SKY to
  the ground (slow push out of the clouds, 21 m/s at the ground); the rock is a cluster of dark
  crust boulders around a glowing lava core, because meshes cannot carry an emissive crack map.

Timeline (the concept sheet's): telegraph 0-0.6, summon_sky 0.6-1.2, meteor_appears 1.2-1.8,
descent 1.8-2.4, final_approach 2.4-2.8, impact 2.8-3.2, aftermath 3.2-4.0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DUR = 4.0
SEED = 60606
T_FORM = 1.15          # the rock starts to exist inside the glow
T_HIT = 2.8            # ground contact
AOE = 3.5              # telegraph / damage radius

SKY = (-5.2, 14.2, -1.2)          # centre of the sky portal
HIT = (0.0, 0.95, 0.0)            # rock centre at ground contact
CLOUD_TILT = -18.0                # the cloud disc leans its underside towards the camera

rng = random.Random(4242)


def r(v, n=4):
    return round(float(v), n)


def vec(v, n=4):
    return [r(c, n) for c in v]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def norm(a):
    length = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    return (a[0] / length, a[1] / length, a[2] / length)


FALL = norm(sub(HIT, SKY))         # direction of travel
BACK = mul(FALL, -1.0)             # up the path: where the flames stream
PATH_LEN = math.dist(SKY, HIT)


def path_s(t):
    """Fraction of the path covered at effect time t: a slow push, then a hard acceleration."""
    u = min(1.0, max(0.0, (t - T_FORM) / (T_HIT - T_FORM)))
    return 0.07 * u + 0.93 * u ** 2.5


def path_pos(t):
    return add(SKY, mul(sub(HIT, SKY), path_s(t)))


nodes: list[dict] = []


def node(nid, ntype, params=None, inputs=None, layer=None, parent=None, seed=None):
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
    nodes.append(n)
    return nid


def track(keys):
    """keys = [(t, value[, interp]), ...] -> {"value": first, "track": [...]}"""
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


def g(nid, op, params=None, inputs=None):
    d = {"id": nid, "op": op}
    if params:
        d["params"] = params
    if inputs:
        d["inputs"] = inputs
    return d


FIRE_RAMP = [
    [0.0, [0.10, 0.006, 0.0, 1.0]],
    [0.22, [0.62, 0.05, 0.004, 1.0]],
    [0.45, [1.0, 0.24, 0.02, 1.0]],
    [0.68, [1.0, 0.52, 0.10, 1.0]],
    [0.86, [1.0, 0.80, 0.36, 1.0]],
    [1.0, [1.0, 0.95, 0.78, 1.0]],
]

# =========================================================================
# camera
# =========================================================================
# The studio pulls the eye back 1.45x from this node, so the eye ends up near
# CAM_EYE: low, far, looking slightly up, sky in the upper third.
CAM_TARGET = (-2.0, 7.6, 0.0)
CAM_EYE = (-2.0, 6.5, 25.0)
cam_pos = add(CAM_TARGET, mul(sub(CAM_EYE, CAM_TARGET), 1.0 / 1.45))
node("cam", "camera", {"position": vec(cam_pos, 3), "target": vec(CAM_TARGET, 3), "fov": 46})

# =========================================================================
# textures
# =========================================================================
# One fire_sim flipbook shared by the meteor's flame body, the impact fireball and the
# aftermath ground fires.  The v mirror (half/vgr/byv/flip) puts the fuel bed at the bottom
# of the sprite, which is where both renderers need it (docs/BACKLOG.md "Flipbook orientation").
node("tex_fire", "texture", {"width": 128, "height": 192, "frames": 20, "graph": {"nodes": [
    g("sim", "fire_sim", {"seed": 11, "fuel": 1.3, "fuel_width": 0.85, "buoyancy": 2.6,
                          "turbulence": 1.9, "turbulence_scale": 2.4, "cooling": 1.28,
                          "detail": 4, "speed": 0.07, "substeps": 4, "loop": True,
                          "flicker": 0.45, "sharpness": 1.2}),
    g("half", "constant", {"color": [0.5, 0.5, 0.5, 1.0]}),
    g("vgr", "gradient_linear", {"angle": 90, "start": 1.0, "end": 0.0}),
    g("byv", "channel_pack", {"channel": "luminance"}, {"r": "half", "g": "vgr", "b": "half"}),
    g("flip", "distort", {"amount": 2.0}, {"a": "sim", "by": "byv"}),
    # soft side mask, and a root mask that dissolves the straight-edged fuel slab
    g("gu", "gradient_linear", {"angle": 0, "start": 0.0, "end": 1.0}),
    g("gi", "invert", None, {"a": "gu"}),
    g("par", "math", {"mode": "multiply"}, {"a": "gu", "b": "gi"}),
    g("hm", "levels", {"in_high": 0.16, "gamma": 0.8}, {"a": "par"}),
    g("m1", "math", {"mode": "multiply"}, {"a": "flip", "b": "hm"}),
    g("vr0", "gradient_linear", {"angle": 90, "start": 1.0, "end": 0.0}),
    g("vr", "levels", {"in_low": 0.10, "in_high": 0.50, "gamma": 0.85}, {"a": "vr0"}),
    g("m2", "math", {"mode": "multiply"}, {"a": "m1", "b": "vr"}),
    g("lv", "levels", {"in_low": 0.10, "in_high": 0.92}, {"a": "m2"}),
    # per-pixel temperature: deep red rags, orange body, yellow-white cores
    g("col", "colorize", {"gradient": [
        [0.0, [0.0, 0.0, 0.0, 0.0]],
        [0.2, [0.40, 0.02, 0.0, 1.0]],
        [0.5, [1.0, 0.15, 0.012, 1.0]],
        [0.8, [1.0, 0.42, 0.06, 1.0]],
        [1.0, [1.0, 0.82, 0.45, 1.0]],
    ]}, {"a": "lv"}),
], "output": "col"}})

# ragged animated puff: smoke, dust and the storm cloud sprites
node("tex_puff", "texture", {"width": 96, "height": 96, "frames": 8, "graph": {"nodes": [
    g("rd", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
    g("n", "fbm", {"frequency": 3.0, "octaves": 4, "seed": 3, "animate": 1.0}),
    g("m", "math", {"mode": "multiply"}, {"a": "rd", "b": "n"}),
    g("l", "levels", {"in_low": 0.05, "in_high": 0.55}, {"a": "m"}),
], "output": "l"}})

# soft round glow whose falloff fills the quad
node("tex_glow", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
    g("rd", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
], "output": "rd"}})

node("tex_spark", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
    g("rd", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
], "output": "rd"}})

# the glint as the meteor forms: a round hot core with many short uneven soft rays
# (9 + 13 rays of different lengths, broken by noise - never a four-armed star)
node("tex_glint", "texture", {"width": 192, "height": 192, "graph": {"nodes": [
    g("s1", "star", {"points": 9, "width": 0.06, "outer_radius": 0.38, "core_radius": 0.06,
                     "softness": 0.05, "rotation": 7.0}),
    g("s2", "star", {"points": 14, "width": 0.04, "outer_radius": 0.25, "core_radius": 0.05,
                     "softness": 0.04, "rotation": 23.0}),
    g("n", "fbm", {"frequency": 2.6, "octaves": 3, "seed": 71}),
    g("nl", "levels", {"in_low": 0.30, "in_high": 0.72, "out_low": 0.1, "out_high": 1.0}, {"a": "n"}),
    g("n2", "fbm", {"frequency": 3.4, "octaves": 3, "seed": 173}),
    g("n2l", "levels", {"in_low": 0.30, "in_high": 0.72, "out_low": 0.25, "out_high": 1.0}, {"a": "n2"}),
    g("s1n", "math", {"mode": "multiply"}, {"a": "s1", "b": "nl"}),
    g("s2n", "math", {"mode": "multiply"}, {"a": "s2", "b": "n2l"}),
    g("rays", "math", {"mode": "max"}, {"a": "s1n", "b": "s2n"}),
    g("fall", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
    g("raysf", "math", {"mode": "multiply"}, {"a": "rays", "b": "fall"}),
    g("rb", "blur", {"radius": 6.0}, {"a": "raysf"}),
    g("rbl", "levels", {"out_high": 0.62}, {"a": "rb"}),
    g("halo", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
    g("halol", "levels", {"out_high": 0.36}, {"a": "halo"}),
    g("core", "gradient_radial", {"radius": 0.16, "falloff": "quadratic"}),
    g("m1", "math", {"mode": "screen"}, {"a": "rbl", "b": "halol"}),
    g("m2", "math", {"mode": "max"}, {"a": "m1", "b": "core"}),
], "output": "m2"}})

# telegraph: concentric thin rings, two dash bands and a soft inner glow
node("tex_rune", "texture", {"width": 384, "height": 384, "graph": {"nodes": [
    g("r1", "ring", {"radius": 0.468, "thickness": 0.009, "softness": 0.005}),
    g("r2", "ring", {"radius": 0.405, "thickness": 0.0045, "softness": 0.004}),
    g("r3", "ring", {"radius": 0.318, "thickness": 0.007, "softness": 0.005}),
    g("r4", "ring", {"radius": 0.205, "thickness": 0.0045, "softness": 0.004}),
    g("r5", "ring", {"radius": 0.098, "thickness": 0.006, "softness": 0.005}),
    g("d1", "spokes", {"count": 40, "width": 0.011, "softness": 0.005,
                       "inner_radius": 0.418, "outer_radius": 0.452, "rotation": 4.0}),
    g("d2", "spokes", {"count": 22, "width": 0.014, "softness": 0.006,
                       "inner_radius": 0.334, "outer_radius": 0.388, "rotation": 11.0}),
    g("d3", "spokes", {"count": 12, "width": 0.010, "softness": 0.005,
                       "inner_radius": 0.222, "outer_radius": 0.300, "rotation": 17.0}),
    g("m1", "math", {"mode": "max"}, {"a": "r1", "b": "r2"}),
    g("m2", "math", {"mode": "max"}, {"a": "m1", "b": "r3"}),
    g("m3", "math", {"mode": "max"}, {"a": "m2", "b": "r4"}),
    g("m4", "math", {"mode": "max"}, {"a": "m3", "b": "r5"}),
    g("m5", "math", {"mode": "max"}, {"a": "m4", "b": "d1"}),
    g("m6", "math", {"mode": "max"}, {"a": "m5", "b": "d2"}),
    g("m7", "math", {"mode": "max"}, {"a": "m6", "b": "d3"}),
    # uneven brightness around the circle so it is not a mechanical stamp
    g("n", "fbm", {"frequency": 3.2, "octaves": 3, "seed": 21}),
    g("nl", "levels", {"in_low": 0.28, "in_high": 0.74, "out_low": 0.45, "out_high": 1.0}, {"a": "n"}),
    g("lines", "math", {"mode": "multiply"}, {"a": "m7", "b": "nl"}),
    g("hb", "blur", {"radius": 5.0}, {"a": "lines"}),
    g("hbl", "levels", {"in_high": 0.6, "out_high": 0.34}, {"a": "hb"}),
    g("glow", "gradient_radial", {"radius": 0.47, "falloff": "smooth"}),
    g("glowl", "levels", {"out_high": 0.16}, {"a": "glow"}),
    g("k1", "math", {"mode": "max"}, {"a": "lines", "b": "hbl"}),
    g("k2", "math", {"mode": "max"}, {"a": "k1", "b": "glowl"}),
    g("col", "colorize", {"gradient": [
        [0.0, [0.10, 0.003, 0.0, 0.0]],
        [0.16, [0.72, 0.035, 0.004, 0.26]],
        [0.45, [1.0, 0.13, 0.015, 0.62]],
        [0.78, [1.0, 0.36, 0.07, 0.9]],
        [1.0, [1.0, 0.78, 0.46, 1.0]],
    ]}, {"a": "k2"}),
], "output": "col"}})

# shockwave: a thin hot leading edge with a soft pressure band trailing inside it
node("tex_shock", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
    g("n1", "fbm", {"frequency": 2.4, "octaves": 4, "seed": 81}),
    g("n1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "n1"}),
    g("n2", "fbm", {"frequency": 2.4, "octaves": 4, "seed": 97}),
    g("n2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "n2"}),
    g("nv", "channel_pack", {"channel": "luminance"}, {"r": "n1l", "g": "n2l"}),
    g("edge", "ring", {"radius": 0.455, "thickness": 0.016, "softness": 0.012}),
    g("edged", "distort", {"amount": 0.018}, {"a": "edge", "by": "nv"}),
    g("band", "ring", {"radius": 0.40, "thickness": 0.11, "softness": 0.09}),
    g("bandd", "distort", {"amount": 0.04}, {"a": "band", "by": "nv"}),
    g("bandl", "levels", {"out_high": 0.42}, {"a": "bandd"}),
    g("mx", "math", {"mode": "max"}, {"a": "edged", "b": "bandl"}),
    g("brk", "fbm", {"frequency": 5.0, "octaves": 3, "seed": 103}),
    g("brkl", "levels", {"in_low": 0.25, "in_high": 0.75, "out_low": 0.55, "out_high": 1.0}, {"a": "brk"}),
    g("x", "math", {"mode": "multiply"}, {"a": "mx", "b": "brkl"}),
    g("col", "colorize", {"gradient": [
        [0.0, [0.06, 0.01, 0.0, 0.0]],
        [0.2, [0.75, 0.16, 0.02, 0.22]],
        [0.55, [1.0, 0.45, 0.09, 0.6]],
        [1.0, [1.0, 0.90, 0.62, 1.0]],
    ]}, {"a": "x"}),
], "output": "col"}})


def crater_texture(nid, s, size):
    """Radial crack web for the crater: three generations of warped spokes plus a cell network."""
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
    for i, (count, width, inner, out_high, warp, rot) in enumerate([
            (13, 0.0120, 0.03, 1.0, 0.070, 11.0),
            (21, 0.0068, 0.07, 0.85, 0.095, 4.0),
            (33, 0.0038, 0.17, 0.58, 0.120, 9.0)]):
        a, b, c, d = f"s{i}", f"s{i}d", f"s{i}e", f"s{i}l"
        gn.append(g(a, "spokes", {"count": count, "width": width, "softness": width * 0.45,
                                  "inner_radius": inner, "outer_radius": 0.5, "rotation": rot}))
        gn.append(g(b, "distort", {"amount": warp}, {"a": a, "by": "wv"}))
        gn.append(g(c, "distort", {"amount": r(warp * 0.31)}, {"a": b, "by": "fv"}))
        gn.append(g(d, "levels", {"out_high": out_high}, {"a": c}))
        if merged is None:
            merged = d
        else:
            gn.append(g(f"u{i}", "math", {"mode": "max"}, {"a": merged, "b": d}))
            merged = f"u{i}"
    gn += [
        g("cr", "cracks", {"density": 6.0, "width": 0.007, "seed": s + 8}),
        g("crd", "distort", {"amount": 0.03}, {"a": "cr", "by": "fv"}),
        g("crm", "gradient_radial", {"radius": 0.5, "inner_radius": 0.04, "falloff": "linear"}),
        g("crx", "math", {"mode": "multiply"}, {"a": "crd", "b": "crm"}),
        g("crl", "levels", {"out_high": 0.5}, {"a": "crx"}),
        g("uu", "math", {"mode": "max"}, {"a": merged, "b": "crl"}),
        g("fall", "gradient_radial", {"radius": 0.5, "inner_radius": 0.26, "falloff": "linear"}),
        g("web", "math", {"mode": "multiply"}, {"a": "uu", "b": "fall"}),
        g("hot", "gradient_radial", {"radius": 0.10, "falloff": "quadratic"}),
        g("k1", "math", {"mode": "max"}, {"a": "web", "b": "hot"}),
        g("blr", "blur", {"radius": 6.0}, {"a": "k1"}),
        g("blrl", "levels", {"in_high": 0.55, "out_high": 0.18}, {"a": "blr"}),
        g("k2", "math", {"mode": "max"}, {"a": "k1", "b": "blrl"}),
    ]
    return node(nid, "texture", {"width": size, "height": size,
                                 "graph": {"nodes": gn, "output": "k2"}})


crater_texture("tex_cracks", 980, 384)

node("tex_scorch", "texture", {"width": 128, "height": 128, "graph": {"nodes": [
    g("rd", "gradient_radial", {"radius": 0.5, "inner_radius": 0.12, "falloff": "smooth"}),
    g("n", "fbm", {"frequency": 5.0, "octaves": 4, "seed": 71}),
    g("m", "math", {"mode": "multiply"}, {"a": "rd", "b": "n"}),
    g("l", "levels", {"in_low": 0.06, "in_high": 0.5}, {"a": "m"}),
], "output": "l"}})

# soft streak for the flaming debris ribbons: bright thin centre, zero at both edges
node("tex_ribbon", "texture", {"width": 128, "height": 64, "graph": {"nodes": [
    g("gv", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
    g("gvi", "invert", None, {"a": "gv"}),
    g("band", "math", {"mode": "min"}, {"a": "gv", "b": "gvi"}),
    g("prof", "levels", {"in_low": 0.02, "in_high": 0.48, "gamma": 1.6}, {"a": "band"}),
    g("n", "fbm", {"frequency": 3.0, "octaves": 4, "seed": 611, "tile": True}),
    g("nl", "levels", {"in_low": 0.28, "in_high": 0.78, "out_low": 0.45, "out_high": 1.0}, {"a": "n"}),
    g("m", "math", {"mode": "multiply"}, {"a": "prof", "b": "nl"}),
    g("c", "colorize", {"gradient": [
        [0.0, [0.12, 0.006, 0.0, 0.0]],
        [0.3, [0.85, 0.13, 0.012, 0.4]],
        [0.65, [1.0, 0.42, 0.06, 0.8]],
        [1.0, [1.0, 0.86, 0.5, 1.0]],
    ]}, {"a": "m"}),
], "output": "c"}})

# =========================================================================
# materials
# =========================================================================
node("mat_fire", "material", {"blend": "additive", "shading": "unlit", "soft_particle": True,
                              "depth_fade": 0.5, "emissive_intensity": 0.55,
                              "dissolve": 0.42, "erosion": 0.28,
                              "temperature_gradient": FIRE_RAMP})
node("mat_glow", "material", {"blend": "additive", "shading": "unlit", "soft_particle": True,
                              "depth_fade": 0.4})
node("mat_smoke", "material", {"blend": "alpha", "shading": "lit",
                               "base_color": [0.115, 0.092, 0.082, 1.0],
                               "emissive_color": [1.0, 0.36, 0.08, 1.0],
                               "soft_particle": True, "depth_fade": 0.5,
                               "dissolve": 0.45, "erosion": 0.3})
node("mat_cloud", "material", {"blend": "alpha", "shading": "lit",
                               "base_color": [0.13, 0.10, 0.125, 1.0],
                               "emissive_color": [0.62, 0.50, 0.68, 1.0],
                               "soft_particle": True, "depth_fade": 0.5,
                               "dissolve": 0.42, "erosion": 0.3})
node("mat_dust", "material", {"blend": "alpha", "shading": "lit",
                              "base_color": [0.26, 0.19, 0.145, 1.0],
                              "emissive_color": [1.0, 0.4, 0.1, 1.0],
                              "soft_particle": True, "depth_fade": 0.45,
                              "dissolve": 0.42, "erosion": 0.3})
node("mat_rock", "material", {"blend": "alpha", "shading": "lit",
                              "base_color": [0.085, 0.060, 0.048, 1.0],
                              "emissive_color": [1.0, 0.30, 0.05, 1.0],
                              "emissive_intensity": 0.06})
node("mat_crust", "material", {"blend": "alpha", "shading": "lit",
                               "base_color": [0.050, 0.034, 0.028, 1.0],
                               "emissive_color": [1.0, 0.24, 0.04, 1.0],
                               "emissive_intensity": 0.0})
node("mat_lava", "material", {"blend": "alpha", "shading": "lit",
                              "base_color": [0.55, 0.20, 0.04, 1.0],
                              "emissive_color": [1.0, 0.46, 0.10, 1.0],
                              "emissive_intensity": 0.0})
node("mat_ribbon", "material", {"blend": "additive", "shading": "unlit", "soft_particle": True,
                                "depth_fade": 0.2, "emissive_color": [1.0, 0.5, 0.18, 1.0],
                                "emissive_intensity": 0.2}, {"base_texture": "tex_ribbon"})

# =========================================================================
# forces and colliders
# =========================================================================
node("f_gravity", "force", {"force_type": "gravity", "strength": 9.81})
node("f_gravity_rock", "force", {"force_type": "gravity", "strength": 24.0})
node("f_gravity_soft", "force", {"force_type": "gravity", "strength": 4.5})
node("f_fire_curl", "force", {"force_type": "curl_noise", "strength": 2.6, "frequency": 0.55,
                              "octaves": 2, "speed": 0.9})
node("f_fire_lift", "force", {"force_type": "buoyancy", "strength": 2.6, "temperature": 1.0})
node("f_smoke_lift", "force", {"force_type": "buoyancy", "strength": 1.5, "temperature": 1.0})
node("f_smoke_turb", "force", {"force_type": "turbulence", "strength": 1.5, "frequency": 0.45,
                               "octaves": 3, "speed": 0.6})
node("f_spark_turb", "force", {"force_type": "turbulence", "strength": 2.2, "frequency": 0.9,
                               "octaves": 2, "speed": 1.0})
node("f_ember_lift", "force", {"force_type": "buoyancy", "strength": 1.6, "temperature": 1.0})
tilt = math.radians(CLOUD_TILT)
CLOUD_AXIS = (0.0, math.cos(tilt), math.sin(tilt))
node("f_sky_vortex", "force", {"force_type": "vortex", "strength": 7.0, "direction": vec(CLOUD_AXIS),
                               "position": vec(SKY), "radius": 12.0, "falloff": "none"})
node("f_sky_pull", "force", {"force_type": "attractor", "strength": 8.0, "position": vec(SKY),
                             "radius": 0.0})
node("ground", "collider", {"collider_type": "plane", "normal": [0, 1, 0],
                            "bounce": 0.3, "friction": 0.6})

# =========================================================================
# LAYER telegraph (0.0 - 2.8)
# =========================================================================
node("tele_rune", "decal", {
    "shape": "circle", "position": [0.0, 0.02, 0.0], "size": [r(AOE * 2.14), r(AOE * 2.14)],
    "rotation": track([(0.0, [0.0, 0.0, 0.0]), (T_HIT, [0.0, 38.0, 0.0])]),
    "color": [1.0, 0.80, 0.62, 1.0], "blend": "additive",
    "emissive": track([(0.0, 0.0), (0.12, 2.4), (0.3, 1.5), (0.6, 1.25), (1.8, 1.1), (2.4, 1.5),
                       (2.72, 3.4), (T_HIT, 3.8), (2.86, 0.0)]),
    "opacity": track([(0.0, 0.0), (0.1, 1.0), (2.4, 0.9), (T_HIT, 1.0), (2.86, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "duration": 2.88,
}, {"texture": "tex_rune"}, layer="telegraph")

node("tele_glow", "decal", {
    "shape": "circle", "position": [0.0, 0.012, 0.0], "size": [r(AOE * 2.6), r(AOE * 2.6)],
    "color": [1.0, 0.16, 0.03, 1.0], "blend": "additive",
    "emissive": track([(0.0, 0.0), (0.2, 0.5), (1.8, 0.45), (2.4, 0.7), (T_HIT, 1.8), (2.9, 0.0)]),
    "opacity": track([(0.0, 0.0), (0.25, 0.42), (2.4, 0.42), (T_HIT, 0.75), (2.9, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "duration": 2.92,
}, {"texture": "tex_glow"}, layer="telegraph")

# the faint soft glow rising from the centre: wide stretched puffs, never a line
node("ps_column", "particle_system", {
    "max_particles": 110, "lifetime": 1.7, "lifetime_variance": 0.3,
    "size": 2.5, "size_variance": 0.7, "size_over_life": [[0.0, 0.8], [1.0, 1.15]],
    "color": [1.0, 0.17, 0.03, 1.0],
    "color_over_life": [[0.0, [1.0, 0.9, 0.7, 1.0]], [0.4, [1.0, 0.7, 0.55, 1.0]], [1.0, [0.8, 0.3, 0.3, 1.0]]],
    "opacity": 0.085, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.6, 0.6], [1.0, 0.0]],
    "emissive": 0.3, "drag": 0.35, "blend": "additive",
    "render_mode": "stretched_billboard", "velocity_stretch": 0.34,
}, {"sprite": "tex_glow", "material": "mat_glow"}, layer="telegraph")
node("e_column", "emitter", {
    "shape": "disc", "radius": 0.7, "position": [0.0, 0.6, 0.0],
    "rate": track([(0.0, 0.0), (0.08, 40.0), (0.9, 32.0), (1.5, 14.0), (2.2, 0.0)]),
    "velocity": 4.2, "velocity_variance": 1.0, "direction": [0, 1, 0], "spread": 4,
    "duration": 2.25,
}, {"particle": "ps_column"}, layer="telegraph")

node("ps_mote", "particle_system", {
    "max_particles": 220, "lifetime": 1.3, "lifetime_variance": 0.5,
    "size": 0.075, "size_variance": 0.035,
    "color": [1.0, 0.42, 0.08, 1.0],
    "color_over_life": [[0.0, [1.0, 0.9, 0.6, 1.0]], [0.5, [1.0, 0.45, 0.1, 1.0]], [1.0, [0.5, 0.05, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.7, 0.85], [1.0, 0.0]],
    "emissive": 4.0, "drag": 0.7, "blend": "additive",
    "render_mode": "stretched_billboard", "velocity_stretch": 0.12,
}, {"sprite": "tex_spark", "material": "mat_glow",
    "forces": ["f_ember_lift", "f_spark_turb"]}, layer="telegraph")
node("e_mote", "emitter", {
    "shape": "ring", "radius": r(AOE), "inner_radius": r(AOE - 0.5), "position": [0.0, 0.08, 0.0],
    "rate": track([(0.0, 0.0), (0.15, 90.0), (2.3, 70.0), (2.7, 150.0), (T_HIT, 0.0)]),
    "velocity": 1.1, "velocity_variance": 0.7, "direction": [0, 1, 0], "spread": 25,
    "duration": 2.82,
}, {"particle": "ps_mote"}, layer="telegraph")

node("tele_light", "light", {
    "light_type": "point", "position": [0.0, 1.1, 0.0], "color": [1.0, 0.22, 0.05, 1.0],
    "radius": 9.0,
    "intensity": track([(0.0, 0.0), (0.12, 4.5), (0.4, 3.0), (2.3, 2.6), (T_HIT, 5.0), (2.86, 0.0)]),
    "flicker_amplitude": 0.12, "flicker_frequency": 9.0, "duration": 2.88,
}, layer="telegraph")

# =========================================================================
# LAYER sky summon (0.6 - 3.2)
# =========================================================================
# The glowing, swirling inside of the storm: a raymarched nebula disc whose dense arms scatter
# the portal light.  Dark lit puffs (ps_cloud) draw over it, so the glow sits *inside* the cloud.
node("sky_cloud", "volume", {
    "mode": "procedural", "volume_type": "smoke", "shape": "ring",
    "position": vec(SKY), "rotation": [CLOUD_TILT, 0.0, 0.0],
    # a torus: `radius` is the centreline and height / 4 the tube radius, so the eye of the
    # storm opens as the radius grows
    "radius": track([(0.55, 2.2), (1.0, 3.6), (1.3, 4.3), (T_HIT, 4.7), (3.3, 5.2)]),
    "height": track([(0.55, 6.0), (1.2, 9.6), (3.3, 10.0)]),
    "density": track([(0.55, 0.0), (0.9, 1.2), (1.2, 1.8), (2.3, 1.6), (2.9, 0.7), (3.3, 0.0)]),
    "emission": 0.22,
    "color": [0.16, 0.10, 0.17, 1.0], "color_hot": [0.30, 0.22, 0.28, 1.0],
    "filament_scale": 0.7, "strands": 0.55, "carve": 0.46, "softness": 0.8,
    "spiral_arms": 4, "arm_sharpness": 1.7, "twist": 0.0, "spin": 0.22, "climb": 0.0,
    "scatter": 0.8, "march_steps": 24,
    "start_time": 0.55, "duration": 2.78,
}, layer="sky")

node("ps_cloud", "particle_system", {
    "max_particles": 220, "lifetime": 1.6, "lifetime_variance": 0.4,
    "size": 2.7, "size_variance": 0.9, "size_over_life": [[0.0, 0.45], [0.4, 1.0], [1.0, 1.3]],
    "color": [0.42, 0.36, 0.42, 1.0],
    "opacity": 0.7, "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.7, 0.8], [1.0, 0.0]],
    "emissive": 1.3, "rotation_variance": 180.0, "angular_velocity_variance": 24.0,
    "drag": 0.8, "blend": "alpha", "sort": True, "sprite_fps": 7.0,
}, {"sprite": "tex_puff", "material": "mat_cloud",
    "forces": ["f_sky_vortex", "f_sky_pull", "f_smoke_turb"]}, layer="sky")
node("e_cloud", "emitter", {
    "shape": "ring", "radius": 6.4, "inner_radius": 2.4,
    "position": vec(SKY), "rotation": [CLOUD_TILT, 0.0, 0.0],
    "rate": track([(0.6, 0.0), (0.72, 95.0), (1.2, 70.0), (2.2, 55.0), (2.7, 0.0)]),
    "velocity": 0.6, "velocity_variance": 0.4, "direction": [0, 0, 0], "spread": 40,
    "start_time": 0.6, "duration": 2.15,
}, {"particle": "ps_cloud"}, layer="sky")

# one long-lived sprite is the keyframed inner glow: its life curves are the animation
node("ps_sky_glow", "particle_system", {
    "max_particles": 4, "lifetime": 2.3, "size": 8.5,
    "size_over_life": [[0.0, 0.25], [0.22, 0.8], [0.27, 1.0], [0.5, 0.85], [1.0, 0.6]],
    "color": [1.0, 0.2, 0.035, 1.0],
    "color_over_life": [[0.0, [0.7, 0.5, 0.5, 1.0]], [0.2, [1.0, 0.8, 0.7, 1.0]],
                        [0.27, [1.0, 1.0, 1.0, 1.0]], [0.6, [0.9, 0.7, 0.6, 1.0]],
                        [1.0, [0.6, 0.3, 0.3, 1.0]]],
    "opacity": 0.55, "opacity_over_life": [[0.0, 0.0], [0.12, 0.45], [0.26, 1.0], [0.4, 0.7],
                                           [0.75, 0.45], [1.0, 0.0]],
    "emissive": 0.4, "blend": "additive",
}, {"sprite": "tex_glow", "material": "mat_glow"}, layer="sky")
node("e_sky_glow", "emitter", {
    "shape": "point", "position": vec(SKY), "rate": 0, "burst_count": 1, "burst_times": [0.0],
    "velocity": 0.0, "start_time": 0.62, "duration": 0.2,
}, {"particle": "ps_sky_glow"}, layer="sky")

node("ps_glint", "particle_system", {
    "max_particles": 4, "lifetime": 0.46, "size": 6.4,
    "size_over_life": [[0.0, 0.08], [0.25, 1.0], [0.55, 0.8], [1.0, 0.25]],
    "color": [1.0, 0.62, 0.30, 1.0],
    "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.5, [1.0, 0.85, 0.7, 1.0]], [1.0, [1.0, 0.5, 0.3, 1.0]]],
    "opacity": 0.9, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.6, 0.7], [1.0, 0.0]],
    "emissive": 1.2, "rotation": 12.0, "angular_velocity": 22.0, "blend": "additive",
}, {"sprite": "tex_glint", "material": "mat_glow"}, layer="sky")
node("e_glint", "emitter", {
    "shape": "point", "position": vec(add(SKY, (0.0, 0.0, 1.6))), "rate": 0, "burst_count": 1,
    "burst_times": [0.0], "velocity": 0.0, "start_time": 0.98, "duration": 0.2,
}, {"particle": "ps_glint"}, layer="sky")

node("ps_sky_ember", "particle_system", {
    "max_particles": 200, "lifetime": 1.5, "lifetime_variance": 0.5,
    "size": 0.10, "size_variance": 0.05,
    "color": [1.0, 0.5, 0.12, 1.0],
    "color_over_life": [[0.0, [1.0, 0.95, 0.7, 1.0]], [0.4, [1.0, 0.5, 0.12, 1.0]], [1.0, [0.45, 0.04, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.75, 0.9], [1.0, 0.0]],
    "emissive": 5.0, "drag": 0.6, "blend": "additive",
    "render_mode": "stretched_billboard", "velocity_stretch": 0.14,
}, {"sprite": "tex_spark", "material": "mat_glow",
    "forces": ["f_gravity_soft", "f_spark_turb"]}, layer="sky")
node("e_sky_ember", "emitter", {
    "shape": "disc", "radius": 2.8, "position": vec(SKY), "rotation": [CLOUD_TILT, 0.0, 0.0],
    "rate": track([(0.75, 0.0), (1.05, 70.0), (1.9, 60.0), (2.6, 0.0)]),
    "velocity": 1.6, "velocity_variance": 1.2, "direction": [0, -1, 0], "spread": 50,
    "start_time": 0.75, "duration": 1.9,
}, {"particle": "ps_sky_ember"}, layer="sky")

node("sky_light", "light", {
    "light_type": "point", "position": vec(add(SKY, (0.0, 0.4, -2.2))),
    "color": [1.0, 0.24, 0.05, 1.0], "radius": 9.5,
    "intensity": track([(0.6, 0.0), (0.9, 10.0), (1.1, 26.0), (1.2, 46.0), (1.36, 30.0),
                        (1.8, 20.0), (2.4, 10.0), (2.95, 0.0)]),
    "flicker_amplitude": 0.18, "flicker_frequency": 11.0,
    "start_time": 0.6, "duration": 2.4,
}, layer="sky")

# =========================================================================
# LAYER meteor (1.15 - 2.8)
# =========================================================================
path_keys = [(0.0, vec(SKY))]
t = T_FORM
while t < T_HIT - 1e-6:
    path_keys.append((t, vec(path_pos(t))))
    t += 0.05
path_keys.append((T_HIT, vec(HIT)))
path_keys.append((DUR, vec(HIT)))
node("rig", "mesh", {"primitive": "sphere", "radius": 0.02, "segments": 6, "visible": False,
                     "position": track(path_keys)}, layer="meteor")

FLIGHT = T_HIT - T_FORM
node("met_body", "mesh", {
    "primitive": "sphere", "radius": 0.02, "segments": 6, "visible": False,
    "rotation": track([(T_FORM, [0.0, 0.0, 0.0]), (T_HIT, [150.0, -110.0, 80.0])]),
    "scale": track([(T_FORM, [0.08, 0.08, 0.08]), (1.4, [0.62, 0.62, 0.62]),
                    (1.75, [1.0, 1.0, 1.0]), (T_HIT, [1.0, 1.0, 1.0])]),
}, layer="meteor", parent="rig")

node("met_lava", "mesh", {
    "primitive": "rock", "radius": 0.80, "segments": 16, "irregularity": 0.22,
    "color": [1.0, 0.72, 0.40, 1.0],
    "emissive": track([(T_FORM, 1.0), (1.8, 1.5), (2.4, 2.0), (T_HIT, 2.6)]),
    "start_time": T_FORM, "duration": r(FLIGHT),
}, {"material": "mat_lava"}, layer="meteor", parent="met_body", seed=9001)

# crust boulders on a jittered Fibonacci sphere: the gaps between them are the lava cracks
CHUNKS = 20
chunk_ids = []
golden = math.pi * (3.0 - math.sqrt(5.0))
for i in range(CHUNKS):
    y = 1.0 - 2.0 * (i + 0.5) / CHUNKS
    ring = math.sqrt(max(0.0, 1.0 - y * y))
    phi = i * golden + rng.uniform(-0.22, 0.22)
    direction = norm((math.cos(phi) * ring, y + rng.uniform(-0.08, 0.08), math.sin(phi) * ring))
    dist = rng.uniform(0.52, 0.64)
    rad = rng.uniform(0.36, 0.50)
    cid = f"met_crust_{i}"
    chunk_ids.append(cid)
    node(cid, "mesh", {
        "primitive": "rock", "radius": r(rad), "segments": 12, "irregularity": r(rng.uniform(0.55, 0.8)),
        "position": vec(mul(direction, dist)),
        "rotation": [r(rng.uniform(0, 360), 1), r(rng.uniform(0, 360), 1), r(rng.uniform(0, 360), 1)],
        "scale": [r(rng.uniform(0.95, 1.25)), r(rng.uniform(0.7, 0.95)), r(rng.uniform(0.9, 1.2))],
        "color": [r(rng.uniform(0.85, 1.15)), r(rng.uniform(0.8, 1.05)), r(rng.uniform(0.78, 1.0)), 1.0],
        "emissive": 0.0,
        "start_time": T_FORM, "duration": r(FLIGHT),
    }, {"material": "mat_crust"}, layer="meteor", parent="met_body", seed=9100 + i * 13)

# --- flame body: big fire_sim sprites born around the back of the rock and left behind,
# so the body is a long tapering tail and the leading face of the rock stays dark.
FLAME_OFFSET = add(mul(BACK, 0.35), (0.0, 0.0, -0.55))
node("ps_tail", "particle_system", {
    "max_particles": 260, "lifetime": 0.58, "lifetime_variance": 0.2,
    "size": 2.35, "size_variance": 0.7,
    "size_over_life": [[0.0, 0.5], [0.22, 1.0], [0.6, 0.78], [1.0, 0.3]],
    "color": [1.0, 0.88, 0.70, 1.0],
    "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.4, [1.0, 0.9, 0.8, 1.0]], [1.0, [0.9, 0.62, 0.5, 1.0]]],
    "opacity": 0.5, "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.55, 0.75], [1.0, 0.0]],
    "emissive": 0.3, "drag": 1.0, "blend": "additive", "soft_particle_distance": 0.6,
    "render_mode": "stretched_billboard", "velocity_stretch": 0.1, "sprite_fps": 18.0,
}, {"sprite": "tex_fire", "material": "mat_fire", "forces": ["f_fire_curl"]}, layer="meteor")
node("e_tail", "emitter", {
    "shape": "sphere", "radius": 0.7, "position": vec(FLAME_OFFSET),
    "rate": track([(T_FORM, 0.0), (1.3, 40.0), (1.8, 120.0), (2.4, 170.0), (2.78, 200.0), (T_HIT, 0.0)]),
    "velocity": 4.2, "velocity_variance": 2.0, "direction": vec(BACK), "spread": 13,
    "start_time": T_FORM, "duration": r(FLIGHT),
}, {"particle": "ps_tail"}, layer="meteor", parent="rig")

# thinner, longer-lived wisps: the far end of the tail
node("ps_tail_far", "particle_system", {
    "max_particles": 220, "lifetime": 1.05, "lifetime_variance": 0.3,
    "size": 1.55, "size_variance": 0.55,
    "size_over_life": [[0.0, 0.45], [0.2, 1.0], [0.6, 0.7], [1.0, 0.2]],
    "color": [1.0, 0.80, 0.60, 1.0],
    "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.5, [1.0, 0.8, 0.66, 1.0]], [1.0, [0.8, 0.45, 0.35, 1.0]]],
    "opacity": 0.42, "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.5, 0.65], [1.0, 0.0]],
    "emissive": 0.25, "drag": 0.9, "blend": "additive", "soft_particle_distance": 0.6,
    "render_mode": "stretched_billboard", "velocity_stretch": 0.16, "sprite_fps": 15.0,
}, {"sprite": "tex_fire", "material": "mat_fire", "forces": ["f_fire_curl"]}, layer="meteor")
node("e_tail_far", "emitter", {
    "shape": "sphere", "radius": 0.5, "position": vec(add(mul(BACK, 0.8), (0.0, 0.0, -0.4))),
    "rate": track([(T_FORM, 0.0), (1.4, 25.0), (1.8, 70.0), (2.4, 95.0), (2.78, 110.0), (T_HIT, 0.0)]),
    "velocity": 2.6, "velocity_variance": 1.4, "direction": vec(BACK), "spread": 10,
    "start_time": T_FORM, "duration": r(FLIGHT),
}, {"particle": "ps_tail_far"}, layer="meteor", parent="rig")

# hot leading face: a small glow that rides the bow of the rock
node("ps_bow", "particle_system", {
    "max_particles": 16, "lifetime": 0.12, "size": 1.9, "size_variance": 0.3,
    "color": [1.0, 0.5, 0.14, 1.0],
    "opacity": 0.32, "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [1.0, 0.0]],
    "emissive": 0.5, "blend": "additive",
}, {"sprite": "tex_glow", "material": "mat_glow"}, layer="meteor")
node("e_bow", "emitter", {
    "shape": "point", "position": vec(add(mul(FALL, 0.55), (0.0, 0.0, 0.7))),
    "rate": track([(T_FORM, 0.0), (1.5, 30.0), (T_HIT - 0.02, 50.0), (T_HIT, 0.0)]),
    "velocity": 0.0, "inherit_velocity": 1.0,
    "start_time": T_FORM, "duration": r(FLIGHT),
}, {"particle": "ps_bow"}, layer="meteor", parent="rig")

node("ps_trail_smoke", "particle_system", {
    "max_particles": 160, "lifetime": 1.35, "lifetime_variance": 0.4,
    "size": 1.7, "size_variance": 0.6, "size_over_life": [[0.0, 0.45], [0.4, 1.2], [1.0, 1.9]],
    "color": [0.8, 0.72, 0.68, 1.0],
    "opacity": 0.6, "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.65, 0.7], [1.0, 0.0]],
    "emissive": 0.55, "emissive_over_life": [[0.0, 1.6], [0.25, 0.55], [0.6, 0.12], [1.0, 0.0]],
    "rotation_variance": 180.0, "angular_velocity_variance": 30.0,
    "drag": 1.2, "blend": "alpha", "sort": True, "sprite_fps": 8.0,
}, {"sprite": "tex_puff", "material": "mat_smoke",
    "forces": ["f_smoke_lift", "f_smoke_turb"]}, layer="meteor")
node("e_trail_smoke", "emitter", {
    "shape": "sphere", "radius": 0.6, "position": vec(mul(BACK, 1.6)),
    "rate": track([(T_FORM, 0.0), (1.5, 18.0), (1.9, 40.0), (2.6, 52.0), (T_HIT, 0.0)]),
    "velocity": 1.6, "velocity_variance": 0.9, "direction": vec(BACK), "spread": 35,
    "start_time": T_FORM, "duration": r(FLIGHT),
}, {"particle": "ps_trail_smoke"}, layer="meteor", parent="rig")

node("ps_trail_spark", "particle_system", {
    "max_particles": 420, "lifetime": 0.85, "lifetime_variance": 0.4,
    "size": 0.085, "size_variance": 0.04,
    "color": [1.0, 0.6, 0.18, 1.0],
    "color_over_life": [[0.0, [1.0, 0.95, 0.7, 1.0]], [0.4, [1.0, 0.5, 0.12, 1.0]], [1.0, [0.5, 0.05, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 1.0], [0.7, 0.9], [1.0, 0.0]],
    "emissive": 5.5, "drag": 0.8, "blend": "additive",
    "render_mode": "stretched_billboard", "velocity_stretch": 0.11,
}, {"sprite": "tex_spark", "material": "mat_glow",
    "forces": ["f_gravity_soft", "f_spark_turb"]}, layer="meteor")
node("e_trail_spark", "emitter", {
    "shape": "sphere", "radius": 0.8,
    "rate": track([(T_FORM, 0.0), (1.4, 40.0), (1.9, 160.0), (2.6, 260.0), (T_HIT, 0.0)]),
    "velocity": 4.5, "velocity_variance": 3.0, "direction": vec(BACK), "spread": 55,
    "inherit_velocity": 0.15,
    "start_time": T_FORM, "duration": r(FLIGHT),
}, {"particle": "ps_trail_spark"}, layer="meteor", parent="rig")

# the light rides behind the rock (a light at the mesh would burn it white)
node("met_light", "light", {
    "light_type": "point", "position": vec(add(mul(BACK, 1.5), (0.0, 0.0, 0.8))),
    "color": [1.0, 0.48, 0.15, 1.0], "radius": 17.0,
    "intensity": track([(T_FORM, 0.0), (1.4, 3.0), (1.8, 7.0), (2.4, 12.0), (T_HIT, 20.0)]),
    "flicker_amplitude": 0.2, "flicker_frequency": 16.0,
    "start_time": T_FORM, "duration": r(FLIGHT),
}, layer="meteor", parent="rig")

# =========================================================================
# LAYER impact (2.8 - 3.2)
# =========================================================================
node("flash_light", "light", {
    "light_type": "point", "position": [0.0, 2.6, 0.6], "color": [1.0, 0.72, 0.4, 1.0],
    "radius": 18.0,
    "intensity": track([(T_HIT, 0.0), (2.83, 30.0), (2.95, 12.0), (3.12, 0.0)]),
    "start_time": T_HIT, "duration": 0.34,
}, layer="impact")

node("ps_flash", "particle_system", {
    "max_particles": 4, "lifetime": 0.3, "size": 13.0,
    "size_over_life": [[0.0, 0.3], [0.15, 1.0], [1.0, 1.2]],
    "color": [1.0, 0.55, 0.2, 1.0],
    "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.3, [1.0, 0.8, 0.6, 1.0]], [1.0, [0.9, 0.4, 0.25, 1.0]]],
    "opacity": 0.7, "opacity_over_life": [[0.0, 1.0], [0.25, 0.6], [1.0, 0.0]],
    "emissive": 0.6, "blend": "additive",
}, {"sprite": "tex_glow", "material": "mat_glow"}, layer="impact")
node("e_flash", "emitter", {
    "shape": "point", "position": [0.0, 1.2, 0.0], "rate": 0, "burst_count": 1, "burst_times": [0.0],
    "velocity": 0.0, "start_time": T_HIT, "duration": 0.1,
}, {"particle": "ps_flash"}, layer="impact")

node("ps_flash_core", "particle_system", {
    "max_particles": 4, "lifetime": 0.16, "size": 4.6,
    "size_over_life": [[0.0, 0.4], [0.3, 1.0], [1.0, 0.7]],
    "color": [1.0, 0.92, 0.78, 1.0],
    "opacity": 1.0, "opacity_over_life": [[0.0, 1.0], [0.4, 0.8], [1.0, 0.0]],
    "emissive": 1.6, "blend": "additive",
}, {"sprite": "tex_glow", "material": "mat_glow"}, layer="impact")
node("e_flash_core", "emitter", {
    "shape": "point", "position": [0.0, 1.0, 0.0], "rate": 0, "burst_count": 1, "burst_times": [0.0],
    "velocity": 0.0, "start_time": T_HIT, "duration": 0.1,
}, {"particle": "ps_flash_core"}, layer="impact")

# fireball: tongues thrown outward and up, rolled by drag and buoyancy
node("ps_fireball", "particle_system", {
    "max_particles": 420, "lifetime": 0.72, "lifetime_variance": 0.26,
    "size": 2.7, "size_variance": 0.9,
    "size_over_life": [[0.0, 0.35], [0.3, 1.0], [1.0, 1.1]],
    "color": [1.0, 0.88, 0.70, 1.0],
    "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.35, [1.0, 0.92, 0.84, 1.0]], [1.0, [0.9, 0.6, 0.45, 1.0]]],
    "opacity": 0.5, "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.55, 0.78], [1.0, 0.0]],
    "emissive": 0.3, "drag": 3.0, "blend": "additive", "soft_particle_distance": 0.7,
    "render_mode": "stretched_billboard", "velocity_stretch": 0.07, "sprite_fps": 18.0,
}, {"sprite": "tex_fire", "material": "mat_fire",
    "forces": ["f_fire_curl", "f_fire_lift"]}, layer="impact")
node("e_fireball", "emitter", {
    "shape": "hemisphere", "radius": 1.3, "position": [0.0, 0.7, 0.0],
    "rate": 0, "burst_count": 80, "burst_times": [0.0, 0.05],
    "velocity": 10.0, "velocity_variance": 4.5, "direction": [0, 0, 0], "spread": 12,
    "start_time": T_HIT, "duration": 0.2,
}, {"particle": "ps_fireball"}, layer="impact")
node("e_fire_column", "emitter", {
    "shape": "disc", "radius": 1.5, "position": [0.0, 1.0, 0.0],
    "rate": track([(2.84, 0.0), (2.9, 190.0), (3.1, 130.0), (3.4, 0.0)]),
    "velocity": 7.5, "velocity_variance": 2.5, "direction": [0, 1, 0], "spread": 26,
    "start_time": 2.84, "duration": 0.6,
}, {"particle": "ps_fireball"}, layer="impact")
node("e_fire_skirt", "emitter", {
    "shape": "ring", "radius": 2.0, "inner_radius": 0.8, "position": [0.0, 0.9, 0.0],
    "rate": 0, "burst_count": 60, "burst_times": [0.02, 0.08],
    "velocity": 2.0, "velocity_variance": 1.0, "direction": [0, 1, 0], "spread": 30,
    "radial_velocity": 11.0,
    "start_time": T_HIT, "duration": 0.2,
}, {"particle": "ps_fireball"}, layer="impact")

# shockwave ring racing outward along the ground
node("shock_ring", "decal", {
    "shape": "circle", "position": [0.0, 0.04, 0.0], "rotation": [0.0, 31.0, 0.0],
    "size": track([(T_HIT, [1.6, 1.6]), (2.88, [6.5, 6.5]), (2.98, [10.4, 10.4]),
                   (3.1, [13.6, 13.6]), (3.25, [16.2, 16.2]), (3.4, [17.6, 17.6])]),
    "color": track([(T_HIT, [1.0, 0.95, 0.85, 1.0]), (3.0, [1.0, 0.75, 0.45, 1.0]),
                    (3.4, [1.0, 0.4, 0.12, 1.0])]),
    "blend": "additive",
    "emissive": track([(T_HIT, 3.4), (2.95, 2.6), (3.15, 1.5), (3.4, 0.0)]),
    "opacity": track([(T_HIT, 1.0), (3.1, 0.9), (3.25, 0.5), (3.4, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": T_HIT, "duration": 0.62,
}, {"texture": "tex_shock"}, layer="impact")

node("ps_spark", "particle_system", {
    "max_particles": 700, "lifetime": 0.95, "lifetime_variance": 0.5,
    "size": 0.095, "size_variance": 0.05,
    "color": [1.0, 0.62, 0.2, 1.0],
    "color_over_life": [[0.0, [1.0, 0.96, 0.75, 1.0]], [0.4, [1.0, 0.5, 0.1, 1.0]], [1.0, [0.45, 0.05, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 1.0], [0.75, 0.95], [1.0, 0.0]],
    "emissive": 6.0, "emissive_over_life": [[0.0, 1.3], [0.5, 0.9], [1.0, 0.3]],
    "drag": 0.55, "bounce": 0.35, "blend": "additive",
    "render_mode": "stretched_billboard", "velocity_stretch": 0.075,
}, {"sprite": "tex_spark", "material": "mat_glow",
    "forces": ["f_gravity", "f_spark_turb"], "colliders": ["ground"]}, layer="impact")
node("e_spark", "emitter", {
    "shape": "hemisphere", "radius": 1.2, "position": [0.0, 0.3, 0.0],
    "rate": track([(T_HIT, 0.0), (2.84, 300.0), (3.1, 120.0), (3.4, 0.0)]),
    "burst_count": 440, "burst_times": [0.0],
    "velocity": 13.0, "velocity_variance": 8.0, "direction": [0, 1, 0], "spread": 72,
    "start_time": T_HIT, "duration": 0.62,
}, {"particle": "ps_spark"}, layer="impact")

# dust rolling along the ground behind the shock
node("ps_dust", "particle_system", {
    "max_particles": 260, "lifetime": 1.05, "lifetime_variance": 0.25,
    "size": 1.5, "size_variance": 0.6, "size_over_life": [[0.0, 0.4], [0.5, 1.4], [1.0, 2.0]],
    "color": [0.82, 0.76, 0.7, 1.0],
    "opacity": 0.5, "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.6, 0.7], [1.0, 0.0]],
    "emissive": 0.45, "emissive_over_life": [[0.0, 1.5], [0.3, 0.5], [1.0, 0.0]],
    "rotation_variance": 180.0, "angular_velocity_variance": 30.0,
    "drag": 2.5, "blend": "alpha", "sort": True, "sprite_fps": 8.0,
}, {"sprite": "tex_puff", "material": "mat_dust", "forces": ["f_smoke_turb"]}, layer="impact")
node("e_dust", "emitter", {
    "shape": "ring", "radius": 1.6, "inner_radius": 0.9, "position": [0.0, 0.55, 0.0],
    "rate": 0, "burst_count": 110, "burst_times": [0.0, 0.06],
    "velocity": 0.8, "velocity_variance": 0.6, "direction": [0, 1, 0], "spread": 40,
    "radial_velocity": 12.5,
    "start_time": T_HIT, "duration": 0.2,
}, {"particle": "ps_dust"}, layer="impact")

# =========================================================================
# LAYER debris (2.8 - 4.0)
# =========================================================================
node("rock_mesh", "mesh", {"primitive": "rock", "radius": 0.5, "segments": 12,
                           "irregularity": 0.75, "variants": 6, "visible": False})
node("chip_mesh", "mesh", {"primitive": "shard", "radius": 0.5, "height": 0.34,
                           "irregularity": 0.7, "variants": 4, "visible": False})


def debris(name, mesh, count, size, size_var, speed, speed_var, spread, radial, ring, life, spin,
           mesh_scale, mesh_var, mass):
    node(f"ps_{name}", "particle_system", {
        "max_particles": count + 4, "lifetime": life, "lifetime_variance": 0.06,
        "size": size, "size_variance": size_var,
        "size_over_life": [[0.0, 0.6], [0.04, 1.0], [0.86, 1.0], [1.0, 0.0]],
        "angular_velocity": spin, "angular_velocity_variance": r(spin * 0.8),
        "render_mode": "mesh", "blend": "alpha", "drag": 0.85, "mass": mass,
        "orientation": "tumble", "mesh_scale": mesh_scale, "mesh_scale_variance": mesh_var,
        "bounce": 0.32, "friction": 0.55,
    }, {"mesh": mesh, "material": "mat_rock",
        "forces": ["f_gravity_rock"], "colliders": ["ground"]}, layer="debris")
    node(f"e_{name}", "emitter", {
        "shape": "ring", "radius": ring, "inner_radius": r(ring * 0.35), "position": [0.0, 0.35, 0.0],
        "rate": 0, "burst_count": count, "burst_times": [0.0],
        "velocity": speed, "velocity_variance": speed_var, "direction": [0, 1, 0], "spread": spread,
        "radial_velocity": radial,
        "start_time": T_HIT, "duration": 0.1,
    }, {"particle": f"ps_{name}"}, layer="debris")


debris("rock_big", "rock_mesh", 11, 0.95, 0.35, 12.5, 3.0, 26, 4.2, 1.5, 1.16, 140.0,
       [1.1, 0.78, 1.0], [0.3, 0.25, 0.3], 4.0)
debris("rock_med", "rock_mesh", 36, 0.5, 0.2, 13.5, 4.5, 38, 5.5, 1.9, 1.14, 230.0,
       [1.05, 0.75, 1.0], [0.35, 0.3, 0.35], 2.0)
debris("rock_chip", "chip_mesh", 46, 0.3, 0.13, 14.0, 6.0, 52, 6.5, 2.2, 1.1, 340.0,
       [1.0, 0.9, 1.0], [0.35, 0.3, 0.35], 1.0)

# flaming streaks behind the bigger rocks
for name, width, life in (("rock_big", 0.34, 0.30), ("rock_med", 0.2, 0.22)):
    node(f"t_{name}", "trail", {
        "width": width, "lifetime": life, "blend": "additive",
        "color": [0.9, 0.3, 0.07, 1.0], "emissive": 0.35,
        "taper": [[0.0, 0.5], [0.2, 1.0], [0.6, 0.7], [1.0, 0.0]],
        "opacity_over_life": [[0.0, 0.0], [0.15, 0.5], [0.5, 0.3], [1.0, 0.0]],
        "min_vertex_distance": 0.12, "max_segments": 24, "uv_scroll": -1.5,
        "start_time": T_HIT, "duration": 0.75,
    }, {"source": f"ps_{name}", "material": "mat_ribbon"}, layer="debris")

# every landing kicks up a puff of dust: on_collision events fire an event-only emitter
node("ps_land_dust", "particle_system", {
    "max_particles": 160, "lifetime": 0.6, "lifetime_variance": 0.15,
    "size": 0.9, "size_variance": 0.35, "size_over_life": [[0.0, 0.35], [0.4, 1.0], [1.0, 1.5]],
    "color": [0.8, 0.72, 0.66, 1.0],
    "opacity": 0.42, "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.55, 0.6], [1.0, 0.0]],
    "rotation_variance": 180.0, "angular_velocity_variance": 40.0,
    "drag": 2.6, "blend": "alpha", "sort": True, "sprite_fps": 9.0,
}, {"sprite": "tex_puff", "material": "mat_dust", "forces": ["f_smoke_turb"]}, layer="debris")
node("e_land_dust", "emitter", {
    "shape": "sphere", "radius": 0.2, "rate": 0, "burst_count": 3, "burst_times": [],
    "velocity": 2.2, "velocity_variance": 1.2, "direction": [0, 1, 0], "spread": 75,
}, {"particle": "ps_land_dust"}, layer="debris")
node("ev_land_big", "event", {"trigger": "on_collision", "max_triggers": 26, "burst_count": 4,
                              "inherit_position": True},
     {"source": "ps_rock_big", "targets": ["e_land_dust"]}, layer="debris")
node("ev_land_med", "event", {"trigger": "on_collision", "probability": 0.55, "max_triggers": 30,
                              "burst_count": 2, "inherit_position": True},
     {"source": "ps_rock_med", "targets": ["e_land_dust"]}, layer="debris")

# =========================================================================
# LAYER aftermath (2.8 - 4.0)
# =========================================================================
node("scorch", "decal", {
    "shape": "circle", "position": [0.0, 0.006, 0.0], "size": [10.5, 10.5],
    "color": [0.016, 0.011, 0.008, 1.0], "blend": "alpha",
    "opacity": track([(T_HIT, 0.0), (2.9, 0.9), (3.7, 0.88), (DUR, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": T_HIT, "duration": r(DUR - T_HIT),
}, {"texture": "tex_scorch"}, layer="aftermath")

node("crater_cracks", "decal", {
    "shape": "circle", "position": [0.0, 0.03, 0.0], "rotation": [0.0, 17.0, 0.0],
    "size": track([(T_HIT, [3.0, 3.0]), (2.9, [8.2, 8.2]), (3.2, [9.0, 9.0]), (DUR, [9.2, 9.2])]),
    "color": track([(T_HIT, [1.0, 0.95, 0.75, 1.0]), (3.2, [1.0, 0.78, 0.36, 1.0]),
                    (3.5, [1.0, 0.45, 0.12, 1.0]), (3.8, [0.95, 0.2, 0.04, 1.0]),
                    (DUR, [0.6, 0.06, 0.01, 1.0])]),
    "blend": "additive",
    "emissive": track([(T_HIT, 0.0), (2.88, 3.2), (3.2, 2.2), (3.5, 1.5), (3.8, 0.8), (DUR, 0.0)]),
    "opacity": track([(T_HIT, 0.0), (2.86, 1.0), (3.6, 0.9), (3.85, 0.5), (DUR, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": T_HIT, "duration": r(DUR - T_HIT),
}, {"texture": "tex_cracks"}, layer="aftermath")

# ring of small ground fires around the impact radius: irregular clusters, one particle system
node("ps_groundfire", "particle_system", {
    "max_particles": 420, "lifetime": 0.62, "lifetime_variance": 0.22,
    "size": 1.25, "size_variance": 0.5,
    "size_over_life": [[0.0, 0.5], [0.3, 1.0], [1.0, 0.8]],
    "color": [1.0, 0.85, 0.66, 1.0],
    "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.4, [1.0, 0.9, 0.8, 1.0]], [1.0, [0.9, 0.62, 0.48, 1.0]]],
    "opacity": 0.4, "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.6, 0.78], [1.0, 0.0]],
    "emissive": 0.25, "drag": 2.0, "blend": "additive", "soft_particle_distance": 0.4,
    "render_mode": "stretched_billboard", "velocity_stretch": 0.2, "sprite_fps": 18.0,
}, {"sprite": "tex_fire", "material": "mat_fire",
    "forces": ["f_fire_curl", "f_fire_lift"]}, layer="aftermath")

FIRES = 12
fire_ids = []
for i in range(FIRES):
    ang = (i + rng.uniform(-0.3, 0.3)) * 2.0 * math.pi / FIRES
    rad = AOE + rng.uniform(-0.75, 0.45)
    strength = rng.uniform(0.55, 1.0)
    t0 = 2.88 + rng.uniform(0.0, 0.1)
    fid = f"e_groundfire_{i}"
    fire_ids.append(fid)
    node(fid, "emitter", {
        "shape": "disc", "radius": r(0.3 + 0.35 * strength),
        "position": [r(math.cos(ang) * rad), 0.42, r(math.sin(ang) * rad)],
        "rate": track([(t0, 0.0), (t0 + 0.1, r(34.0 * strength, 1)), (3.35, r(28.0 * strength, 1)),
                       (3.62, 0.0)]),
        "velocity": r(1.6 + 0.8 * strength), "velocity_variance": 0.8, "direction": [0, 1, 0], "spread": 16,
        "start_time": r(t0), "duration": r(3.64 - t0),
    }, {"particle": "ps_groundfire"}, layer="aftermath")

node("ps_smoke", "particle_system", {
    "max_particles": 260, "lifetime": 1.0, "lifetime_variance": 0.18,
    "size": 2.3, "size_variance": 0.8, "size_over_life": [[0.0, 0.4], [0.4, 1.1], [1.0, 1.8]],
    "color": [0.75, 0.68, 0.64, 1.0],
    "opacity": 0.66, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.6, 0.72], [1.0, 0.0]],
    "emissive": 0.6, "emissive_over_life": [[0.0, 1.6], [0.3, 0.5], [0.7, 0.1], [1.0, 0.0]],
    "rotation_variance": 180.0, "angular_velocity_variance": 26.0,
    "drag": 1.3, "blend": "alpha", "sort": True, "sprite_fps": 8.0,
}, {"sprite": "tex_puff", "material": "mat_smoke",
    "forces": ["f_smoke_lift", "f_smoke_turb"]}, layer="aftermath")
node("e_smoke", "emitter", {
    "shape": "disc", "radius": 1.7, "position": [0.0, 1.0, 0.0],
    "rate": track([(2.86, 0.0), (2.95, 120.0), (3.1, 70.0), (3.2, 0.0)]),
    "burst_count": 40, "burst_times": [0.0],
    "velocity": 5.5, "velocity_variance": 2.2, "direction": [0, 1, 0], "spread": 32,
    "start_time": 2.86, "duration": 0.36,
}, {"particle": "ps_smoke"}, layer="aftermath")

node("ps_ember", "particle_system", {
    "max_particles": 320, "lifetime": 0.9, "lifetime_variance": 0.3,
    "size": 0.08, "size_variance": 0.04,
    "color": [1.0, 0.5, 0.12, 1.0],
    "color_over_life": [[0.0, [1.0, 0.9, 0.6, 1.0]], [0.5, [1.0, 0.45, 0.08, 1.0]], [1.0, [0.4, 0.03, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.7, 0.85], [1.0, 0.0]],
    "emissive": 4.5, "drag": 0.8, "blend": "additive",
    "render_mode": "stretched_billboard", "velocity_stretch": 0.12,
}, {"sprite": "tex_spark", "material": "mat_glow",
    "forces": ["f_ember_lift", "f_spark_turb"]}, layer="aftermath")
node("e_ember", "emitter", {
    "shape": "disc", "radius": 4.2, "position": [0.0, 0.3, 0.0],
    "rate": track([(2.9, 0.0), (3.0, 260.0), (3.2, 200.0), (3.32, 0.0)]),
    "velocity": 2.2, "velocity_variance": 1.4, "direction": [0, 1, 0], "spread": 50,
    "start_time": 2.9, "duration": 0.45,
}, {"particle": "ps_ember"}, layer="aftermath")

node("ps_ash", "particle_system", {
    "max_particles": 160, "lifetime": 0.95, "lifetime_variance": 0.2,
    "size": 0.09, "size_variance": 0.04,
    "color": [0.5, 0.46, 0.44, 1.0],
    "opacity": 0.8, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.7, 0.8], [1.0, 0.0]],
    "rotation_variance": 180.0, "angular_velocity_variance": 120.0,
    "drag": 1.4, "blend": "alpha",
}, {"sprite": "tex_puff", "material": "mat_dust",
    "forces": ["f_gravity_soft", "f_spark_turb"]}, layer="aftermath")
node("e_ash", "emitter", {
    "shape": "box", "size": [9.0, 3.0, 6.0], "position": [0.0, 3.2, 0.0],
    "rate": track([(2.95, 0.0), (3.05, 140.0), (3.2, 120.0), (3.28, 0.0)]),
    "velocity": 0.6, "velocity_variance": 0.5, "direction": [0, -1, 0], "spread": 60,
    "start_time": 2.95, "duration": 0.35,
}, {"particle": "ps_ash"}, layer="aftermath")

node("after_light", "light", {
    "light_type": "point", "position": [0.0, 1.4, 0.8], "color": [1.0, 0.42, 0.11, 1.0],
    "radius": 13.0,
    "intensity": track([(2.9, 0.0), (3.0, 9.0), (3.3, 7.0), (3.7, 3.5), (DUR, 0.0)]),
    "flicker_amplitude": 0.25, "flicker_frequency": 12.0,
    "start_time": 2.9, "duration": r(DUR - 2.9),
}, layer="aftermath")

node("haze", "post_effect", {
    "post_type": "heat_haze", "frequency": 5.0,
    "intensity": track([(1.8, 0.0), (2.6, 0.22), (T_HIT, 0.3), (2.84, 0.85), (3.0, 0.45),
                        (3.4, 0.3), (3.9, 0.0)]),
    "start_time": 1.8, "duration": 2.15,
}, layer="impact")


# =========================================================================
# controls
# =========================================================================
def bind(nid, parameter, op="multiply"):
    return {"node": nid, "parameter": parameter, "op": op}


def control(cid, label, group, bindings, lo=0.0, hi=3.0, step=0.01, unit="x"):
    return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": 1.0,
            "value": 1.0, "step": step, "unit": unit, "bindings": bindings}


FIRE_SYSTEMS = ["ps_tail", "ps_tail_far", "ps_fireball", "ps_groundfire"]
controls = [
    control("meteor_size", "Meteor size", "Meteor",
            [bind("met_body", "scale"), bind("ps_tail", "size"), bind("ps_tail_far", "size"),
             bind("ps_bow", "size"), bind("e_tail", "radius"), bind("e_tail_far", "radius")],
            lo=0.5, hi=1.8),
    control("fire_intensity", "Fire intensity", "Meteor",
            [bind(ps, "color") for ps in FIRE_SYSTEMS] + [bind(ps, "emissive") for ps in FIRE_SYSTEMS] +
            [bind("met_lava", "emissive"), bind("met_light", "intensity"), bind("ps_bow", "color")],
            hi=2.5),
    control("sky_summon", "Sky summon", "Sky summon",
            [bind("sky_cloud", "density"), bind("sky_cloud", "emission"), bind("sky_light", "intensity"),
             bind("e_cloud", "rate"), bind("e_sky_ember", "rate"), bind("ps_sky_glow", "color"),
             bind("ps_glint", "color")], hi=2.5),
    control("debris_amount", "Debris amount", "Debris",
            [bind("e_rock_big", "burst_count"), bind("e_rock_med", "burst_count"),
             bind("e_rock_chip", "burst_count"), bind("ps_rock_big", "max_particles"),
             bind("ps_rock_med", "max_particles"), bind("ps_rock_chip", "max_particles")],
            hi=2.5),
    control("shockwave", "Shockwave", "Impact",
            [bind("shock_ring", "emissive"), bind("shock_ring", "opacity"), bind("e_dust", "burst_count"),
             bind("haze", "intensity")], hi=2.5),
    control("aftermath", "Aftermath", "Aftermath",
            [bind("scorch", "opacity"), bind("crater_cracks", "emissive"), bind("e_smoke", "rate"),
             bind("e_smoke", "burst_count"), bind("e_ember", "rate"), bind("e_ash", "rate"),
             bind("after_light", "intensity")] + [bind(fid, "rate") for fid in fire_ids], hi=2.5),
    {"id": "global_speed", "label": "Speed", "group": "Global", "min": 0.25, "max": 4.0,
     "default": 1.0, "value": 1.0, "step": 0.05, "unit": "x",
     "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]},
]

effect = {
    "schema_version": "0.1.0",
    "name": "Meteor",
    "description": "A sky portal of storm cloud tears open and a burning rock falls on the marked "
                   "ground: fireball, rock debris, a racing shockwave ring and a smouldering crater.",
    "duration": DUR,
    "seed": SEED,
    "timeline": {"phases": [
        {"name": "telegraph", "start": 0.0, "end": 0.6},
        {"name": "summon_sky", "start": 0.6, "end": 1.2},
        {"name": "meteor_appears", "start": 1.2, "end": 1.8},
        {"name": "descent", "start": 1.8, "end": 2.4},
        {"name": "final_approach", "start": 2.4, "end": 2.8},
        {"name": "impact", "start": 2.8, "end": 3.2},
        {"name": "aftermath", "start": 3.2, "end": 4.0},
    ]},
    "layers": [
        {"id": "telegraph", "name": "Telegraph", "role": "telegraph"},
        {"id": "sky", "name": "Sky summon", "role": "ignition"},
        {"id": "meteor", "name": "Meteor", "role": "primary"},
        {"id": "impact", "name": "Impact", "role": "interaction"},
        {"id": "debris", "name": "Debris", "role": "secondary"},
        {"id": "aftermath", "name": "Aftermath", "role": "aftermath"},
    ],
    "nodes": nodes,
    "controls": controls,
    "metadata": {
        "prompt": "lets create the meteor effect, just one basic one is good with impact debris, "
                  "shockwave ring and aftermath, make sure to include summon sky",
        "generator": "tools/generators/meteor.py",
        "analysis": "telegraph: ringed ground marker with a soft rising glow | summon_sky: a tilted storm "
                    "vortex (raymarched nebula + lit swirling puffs) lit from inside, embers fall, a "
                    "many-rayed glint | meteor: crust boulders around a lava core on a keyframed rig, "
                    "fire_sim flame body left behind as a tapering tail, smoke and sparks | impact: flash, "
                    "fireball, tumbling rock debris with flaming streaks, dust ring, shockwave ring decal | "
                    "aftermath: scorch, cooling crack web, ring of ground fires, smoke, embers and ash",
        "render_settings": {
            "ground_albedo": 0.11,
            "background": [0.0, 0.0, 0.0, 1.0],
            "bloom_intensity": 0.22,
            "bloom_radius": 0.04,
            "exposure": 0.95,
            "grid": False,
        },
        "layout": {"sky_portal": vec(SKY), "impact": [0.0, 0.0, 0.0], "aoe_radius": AOE},
        "tags": ["fire", "meteor", "aoe", "impact", "projectile", "summon", "debris"],
    },
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(REPO, "examples", "effects"),
                    help="directory to write meteor.json into")
    ap.add_argument("--name", default="meteor.json", help="output file name")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, args.name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(effect, fh, indent=1)
        fh.write("\n")
    print(f"wrote {path}  ({len(nodes)} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
