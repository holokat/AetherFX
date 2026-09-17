#!/usr/bin/env python3
"""Build examples/effects/flame_wave.json.

    python tools/generators/flame_wave.py                 # writes examples/effects/flame_wave.json
    python tools/generators/flame_wave.py --out DIR       # writes DIR/flame_wave.json

Flame Wave is a travelling ground attack: a rune circle, a flame surge out of
it, then a wall of fire that races 10 m along +X, burns a strip behind itself,
scorches the ground and flares up three times where it reaches its targets.

Layout: ground y = 0, travel along +X from the cast circle at x = -4.5 to
x = +5.6, centred on z = 0.

How the travelling wall is built
--------------------------------
* `hub` is an invisible mesh whose position is keyframed along X (quadratic
  ease-in over the wave_forms phase, then constant speed).  Every wall emitter,
  the ground glow and the two lights that belong to the front are its children.
* The wall emitters are `line` emitters turned 90 degrees about Y, so the line
  spans the wall's WIDTH (z) and `length` is a plain float the width control can
  scale.  In that frame local +Z is world +X (forward) and `direction` is
  written as (0, up, forward).
* Emission rates follow hub speed (RATE_ENVELOPE), so the sprite spacing along
  the path is constant and the wall has no gaps at full speed.
* crest  - big, short-lived, forward-leaning sprites that inherit part of the
           hub velocity, so they lean into the direction of travel and swing
           upright as drag takes the forward speed away (the curl of the wave).
  body   - the mass of the wall, a little behind the crest.
  tongue - fast, long-lived tongues that tear off the top.
  lick   - small sprites thrown forward along the ground ahead of the front.
  strip  - low, longer-lived flames dropped behind the hub: the burning strip.
  All of them play ONE `fire_sim` flipbook (tex_flame), per particle, coloured
  over life by the material's temperature gradient.
* The scorch is a row of ground decals.  A decal cannot be wiped along its
  length, so every segment opens only once the hub is PAST its far end: it is
  born under the burning strip and is uncovered by the flames dying, which is
  what makes the reveal continuous - and it can never show ahead of the fire.

House style: the cast circle is rings, tick bands and script-like strokes only
(no crosses, no four-armed stars); the upward glow is a wide soft column of
puffs, never a rod; every texture is a procedural graph.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SLUG = "flame_wave"

# --------------------------------------------------------------------------
# timing and layout
# --------------------------------------------------------------------------
DUR = 2.5
SEED = 7141

PHASES = [
    ("cast", 0.0, 0.3),
    ("flame_surge", 0.3, 0.6),
    ("wave_forms", 0.6, 0.9),
    ("travel_forward", 0.9, 1.8),
    ("impact", 1.8, 2.1),
    ("fade", 2.1, 2.5),
]

X_CAST = -4.5          # centre of the cast circle
X_END = 5.6            # where the front dies
T_MOVE = 0.6           # the hub starts to move (wave_forms)
T_FULL = 0.9           # ... and is at full speed (travel_forward)
T_STOP = 2.0           # the hub reaches X_END
SPEED = (X_END - X_CAST) / (0.5 * (T_FULL - T_MOVE) + (T_STOP - T_FULL))   # ~8.1 m/s

WALL_WIDTH = 2.8       # metres across z

# three staggered flare-ups over the last stretch: (x, z)
FLARES = [(3.9, 0.35), (4.75, -0.45), (5.5, 0.2)]


def r(v: float, n: int = 4) -> float:
    return round(float(v), n)


def hub_x(t: float) -> float:
    if t <= T_MOVE:
        return X_CAST
    if t <= T_FULL:
        return X_CAST + 0.5 * SPEED / (T_FULL - T_MOVE) * (t - T_MOVE) ** 2
    if t <= T_STOP:
        return X_CAST + 0.5 * SPEED * (T_FULL - T_MOVE) + SPEED * (t - T_FULL)
    return X_END


def t_arrive(x: float) -> float:
    """Effect time at which the hub reaches x."""
    x_full = hub_x(T_FULL)
    if x <= X_CAST:
        return T_MOVE
    if x <= x_full:
        return T_MOVE + math.sqrt(2.0 * (x - X_CAST) * (T_FULL - T_MOVE) / SPEED)
    return min(T_STOP, T_FULL + (x - x_full) / SPEED)


# hub keys: the ease-in is sampled every 0.05 s, the cruise is one linear span
HUB_TIMES = [0.0, T_MOVE] + [r(T_MOVE + 0.05 * i, 2) for i in range(1, 7)] + [T_STOP, DUR]

# fraction of the full emission rate: a standing fire while the wave forms, then
# proportional to hub speed, and off as the front dies
RATE_ENVELOPE = [(0.0, 0.0), (0.5, 0.0), (0.62, 0.42), (0.75, 0.56), (0.9, 1.0), (1.93, 1.0), (T_STOP, 0.0)]

nodes: list[dict[str, Any]] = []


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def node(nid: str, ntype: str, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None,
         layer: str | None = None, parent: str | None = None) -> str:
    n: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        n["layer"] = layer
    if parent:
        n["parent"] = parent
    if params:
        n["parameters"] = params
    if inputs:
        n["inputs"] = inputs
    nodes.append(n)
    return nid


def track(keys: list[tuple], scale: float = 1.0) -> dict[str, Any]:
    """keys = [(t, value[, interp]), ...] -> {"value": first, "track": [...]}; scalar values are scaled."""
    out = []
    last = None
    for k in keys:
        t = r(k[0], 4)
        if last is not None and t <= last:
            continue
        last = t
        v = k[1]
        if isinstance(v, (int, float)):
            v = r(v * scale)
        item: dict[str, Any] = {"time": t, "value": v}
        if len(k) > 2 and k[2]:
            item["interp"] = k[2]
        out.append(item)
    return {"value": out[0]["value"], "track": out}


def envelope(peak: float, keys: list[tuple[float, float]] = RATE_ENVELOPE) -> dict[str, Any]:
    return track([(t, peak * f) for t, f in keys])


def hub_track(dx: float, y: float, z: float = 0.0) -> dict[str, Any]:
    """World-space position that follows the hub (for nodes that are not its children)."""
    return track([(t, [r(hub_x(t) + dx), r(y), r(z)]) for t in HUB_TIMES])


def g(nid: str, op: str, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": nid, "op": op}
    if params:
        d["params"] = params
    if inputs:
        d["inputs"] = inputs
    return d


def texture(nid: str, width: int, height: int, graph_nodes: list[dict[str, Any]], output: str, frames: int = 1) -> str:
    params: dict[str, Any] = {"width": width, "height": height}
    if frames > 1:
        params["frames"] = frames
    params["graph"] = {"nodes": graph_nodes, "output": output}
    return node(nid, "texture", params)


# --------------------------------------------------------------------------
# camera: raised three-quarter view from the front side. The wave runs left to
# right and slightly towards the eye; the studio pulls the eye back 1.45x.
# --------------------------------------------------------------------------
node("cam", "camera", {"position": [6.2, 5.0, 9.8], "target": [1.2, 0.5, 0.0], "fov": 45})

# --------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------
# The one fire flipbook. `fire_sim` bakes the root at image row 0 and both
# renderers draw row 0 at the TOP of a sprite, so the sheet is mirrored in V
# with a `distort` (the Fire AOE workaround). The same remap crops the bright
# fuel slab under the tongues (SLAB) so the flames fill the quad.
SLAB, TOP = 0.2, 0.99
_S0 = 0.5 + TOP / 2.0
_S1 = _S0 - (1.0 + TOP - SLAB) / 2.0
texture("tex_flame", 128, 160, [
    g("sim", "fire_sim", {"seed": 12, "fuel": 1.22, "fuel_width": 0.8, "buoyancy": 3.9, "turbulence": 2.3,
                          "turbulence_scale": 3.6, "cooling": 1.9, "detail": 4, "speed": 0.06, "substeps": 4,
                          "loop": True, "flicker": 0.6, "sharpness": 1.18}),
    g("half", "constant", {"color": [0.5, 0.5, 0.5, 1.0]}),
    g("vmap", "gradient_linear", {"angle": 90, "start": r(_S0), "end": r(_S1)}),
    g("byv", "channel_pack", {"channel": "luminance"}, {"r": "half", "g": "vmap", "b": "half"}),
    g("flip", "distort", {"amount": 2.0}, {"a": "sim", "by": "byv"}),
    # fade the sides, the root and the very top so no straight quad edge can show
    g("gu", "gradient_linear", {"angle": 0, "start": 0.0, "end": 1.0}),
    g("gui", "invert", None, {"a": "gu"}),
    g("par", "math", {"mode": "multiply"}, {"a": "gu", "b": "gui"}),
    g("side", "levels", {"in_high": 0.085, "gamma": 0.8}, {"a": "par"}),
    g("m1", "math", {"mode": "multiply"}, {"a": "flip", "b": "side"}),
    g("gv", "gradient_linear", {"angle": 90, "start": 1.0, "end": 0.0}),
    g("root", "levels", {"in_high": 0.16, "gamma": 0.8}, {"a": "gv"}),
    g("m2", "math", {"mode": "multiply"}, {"a": "m1", "b": "root"}),
    g("gvi", "invert", None, {"a": "gv"}),
    g("tip", "levels", {"in_high": 0.07}, {"a": "gvi"}),
    g("m3", "math", {"mode": "multiply"}, {"a": "m2", "b": "tip"}),
    g("lv", "levels", {"in_low": 0.12, "in_high": 0.94}, {"a": "m3"}),
], "lv", frames=24)

# soft living puff: smoke, the hot bed under the wall, the cast glow
texture("tex_puff", 96, 96, [
    g("rad", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
    g("n", "fbm", {"frequency": 3.0, "octaves": 4, "seed": 3, "animate": 1.0}),
    g("m", "math", {"mode": "multiply"}, {"a": "rad", "b": "n"}),
    g("l", "levels", {"in_low": 0.04, "in_high": 0.55}, {"a": "m"}),
], "l", frames=8)

texture("tex_spark", 32, 32, [
    g("rad", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
], "rad")

# wide soft ground glow (decals): light that looks like light, never a disc
texture("tex_glow", 96, 96, [
    g("rad", "gradient_radial", {"radius": 0.5, "inner_radius": 0.0, "falloff": "quadratic"}),
    g("n", "fbm", {"frequency": 2.6, "octaves": 3, "seed": 19}),
    g("nl", "levels", {"in_low": 0.2, "in_high": 0.85, "out_low": 0.6, "out_high": 1.0}, {"a": "n"}),
    g("m", "math", {"mode": "multiply"}, {"a": "rad", "b": "nl"}),
], "m")

# cast circle: concentric thin rings, two tick bands and a band of script-like
# strokes. No spokes through the centre, nothing with four (or two) arms.
texture("tex_rune", 384, 384, [
    g("r1", "ring", {"radius": 0.462, "thickness": 0.011, "softness": 0.006}),
    g("r2", "ring", {"radius": 0.425, "thickness": 0.005, "softness": 0.004}),
    g("r3", "ring", {"radius": 0.315, "thickness": 0.008, "softness": 0.005}),
    g("r4", "ring", {"radius": 0.205, "thickness": 0.005, "softness": 0.004}),
    g("r5", "ring", {"radius": 0.105, "thickness": 0.007, "softness": 0.005}),
    g("t1", "spokes", {"count": 36, "width": 0.011, "softness": 0.005,
                       "inner_radius": 0.431, "outer_radius": 0.455, "rotation": 4.0}),
    g("t2", "spokes", {"count": 11, "width": 0.014, "softness": 0.006,
                       "inner_radius": 0.215, "outer_radius": 0.262, "rotation": 13.0}),
    g("ck", "cracks", {"density": 24.0, "width": 0.0075, "seed": 27}),
    g("band", "ring", {"radius": 0.369, "thickness": 0.078, "softness": 0.022}),
    g("script", "math", {"mode": "multiply"}, {"a": "ck", "b": "band"}),
    g("ck2", "cracks", {"density": 17.0, "width": 0.0065, "seed": 44}),
    g("band2", "ring", {"radius": 0.157, "thickness": 0.05, "softness": 0.012}),
    g("script2", "math", {"mode": "multiply"}, {"a": "ck2", "b": "band2"}),
    g("script2l", "levels", {"out_high": 0.7}, {"a": "script2"}),
    g("hz", "gradient_radial", {"radius": 0.47, "falloff": "smooth"}),
    g("hzl", "levels", {"out_high": 0.1}, {"a": "hz"}),
    g("m1", "math", {"mode": "max"}, {"a": "r1", "b": "r2"}),
    g("m2", "math", {"mode": "max"}, {"a": "m1", "b": "r3"}),
    g("m3", "math", {"mode": "max"}, {"a": "m2", "b": "r4"}),
    g("m4", "math", {"mode": "max"}, {"a": "m3", "b": "r5"}),
    g("m5", "math", {"mode": "max"}, {"a": "m4", "b": "t1"}),
    g("m6", "math", {"mode": "max"}, {"a": "m5", "b": "t2"}),
    g("m7", "math", {"mode": "max"}, {"a": "m6", "b": "script"}),
    g("m8", "math", {"mode": "max"}, {"a": "m7", "b": "script2l"}),
    g("soft", "blur", {"radius": 0.9}, {"a": "m8"}),
    g("m9", "math", {"mode": "max"}, {"a": "soft", "b": "hzl"}),
], "m9")


def scorch_graph(seed: int) -> list[dict[str, Any]]:
    """A burnt band along u with torn edges and soft ends (one strip segment)."""
    return [
        g("gv", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        g("gvi", "invert", None, {"a": "gv"}),
        g("bvr", "math", {"mode": "multiply"}, {"a": "gv", "b": "gvi"}),
        g("bv", "levels", {"in_low": 0.015, "in_high": 0.19}, {"a": "bvr"}),
        g("gu", "gradient_linear", {"angle": 0.0, "start": 0.0, "end": 1.0}),
        g("gui", "invert", None, {"a": "gu"}),
        g("bur", "math", {"mode": "multiply"}, {"a": "gu", "b": "gui"}),
        g("bu", "levels", {"in_low": 0.0, "in_high": 0.13}, {"a": "bur"}),
        g("m", "math", {"mode": "multiply"}, {"a": "bv", "b": "bu"}),
        g("n", "fbm", {"frequency": 4.5, "octaves": 4, "seed": seed}),
        g("nl", "levels", {"in_low": 0.26, "in_high": 0.78, "out_low": 0.3, "out_high": 1.0}, {"a": "n"}),
        g("x", "math", {"mode": "multiply"}, {"a": "m", "b": "nl"}),
        g("e", "erosion", {"amount": 0.24, "frequency": 7.0, "seed": seed + 1}, {"a": "x"}),
        g("l", "levels", {"in_low": 0.05, "in_high": 0.7}, {"a": "e"}),
    ]


texture("tex_scorch", 256, 128, scorch_graph(61), "l")


def crack_graph(seed: int, density: float) -> list[dict[str, Any]]:
    """Glowing cracks in a band along u: a broken molten spine on the travel axis and
    a crack network either side of it, with an ember halo. Grayscale, so the decal's
    `color` (and the hue control) decides what it glows like."""
    return [
        g("w1", "fbm", {"frequency": 2.0, "octaves": 4, "seed": seed + 1}),
        g("w1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w1"}),
        g("w2", "fbm", {"frequency": 2.0, "octaves": 4, "seed": seed + 2}),
        g("w2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w2"}),
        g("wv", "channel_pack", {"channel": "luminance"}, {"r": "w1l", "g": "w2l"}),
        g("f1", "fbm", {"frequency": 7.0, "octaves": 4, "seed": seed + 3}),
        g("f1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "f1"}),
        g("f2", "fbm", {"frequency": 7.0, "octaves": 4, "seed": seed + 4}),
        g("f2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "f2"}),
        g("fv", "channel_pack", {"channel": "luminance"}, {"r": "f1l", "g": "f2l"}),
        # v(1-v): a band along u, and a narrow spine inside it
        g("gv", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        g("gvi", "invert", None, {"a": "gv"}),
        g("bvr", "math", {"mode": "multiply"}, {"a": "gv", "b": "gvi"}),
        g("bv", "levels", {"in_low": 0.04, "in_high": 0.23, "gamma": 0.85}, {"a": "bvr"}),
        g("spr", "levels", {"in_low": 0.2487, "in_high": 0.24995}, {"a": "bvr"}),
        g("sp1", "distort", {"amount": 0.15}, {"a": "spr", "by": "wv"}),
        g("sp2", "distort", {"amount": 0.05}, {"a": "sp1", "by": "fv"}),
        g("brk", "fbm", {"frequency": 3.4, "octaves": 3, "seed": seed + 5}),
        g("brkl", "levels", {"in_low": 0.34, "in_high": 0.74, "out_low": 0.12, "out_high": 1.0}, {"a": "brk"}),
        g("spine", "math", {"mode": "multiply"}, {"a": "sp2", "b": "brkl"}),
        # the crack network, two scales, warped so no line is ruler straight
        g("cr1", "cracks", {"density": density, "width": 0.02, "seed": seed + 6}),
        g("cr1d", "distort", {"amount": 0.045}, {"a": "cr1", "by": "fv"}),
        g("cr2", "cracks", {"density": density * 2.1, "width": 0.009, "seed": seed + 7}),
        g("cr2d", "distort", {"amount": 0.026}, {"a": "cr2", "by": "fv"}),
        g("cr2l", "levels", {"out_high": 0.55}, {"a": "cr2d"}),
        g("crk", "math", {"mode": "max"}, {"a": "cr1d", "b": "cr2l"}),
        g("crkm", "math", {"mode": "multiply"}, {"a": "crk", "b": "bv"}),
        # patches of the network stay dark: the ground did not crack evenly
        g("pat", "fbm", {"frequency": 2.6, "octaves": 3, "seed": seed + 8}),
        g("patl", "levels", {"in_low": 0.34, "in_high": 0.66, "out_low": 0.1, "out_high": 1.0}, {"a": "pat"}),
        g("crkp", "math", {"mode": "multiply"}, {"a": "crkm", "b": "patl"}),
        g("u1", "math", {"mode": "max"}, {"a": "spine", "b": "crkp"}),
        # soft ends so neighbouring segments blend
        g("gu", "gradient_linear", {"angle": 0.0, "start": 0.0, "end": 1.0}),
        g("gui", "invert", None, {"a": "gu"}),
        g("bur", "math", {"mode": "multiply"}, {"a": "gu", "b": "gui"}),
        g("bu", "levels", {"in_low": 0.0, "in_high": 0.13}, {"a": "bur"}),
        g("msk", "math", {"mode": "multiply"}, {"a": "u1", "b": "bu"}),
        # ember halo around the lines
        g("blr", "blur", {"radius": 5.0}, {"a": "msk"}),
        g("blrl", "levels", {"in_high": 0.5, "out_high": 0.42}, {"a": "blr"}),
        g("k", "math", {"mode": "max"}, {"a": "msk", "b": "blrl"}),
    ]


texture("tex_crack_a", 256, 256, crack_graph(110, 4.4), "k")
texture("tex_crack_b", 256, 256, crack_graph(320, 5.2), "k")

# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------
FIRE_RAMP = [
    [0.0, [0.3, 0.02, 0.0, 1.0]],
    [0.2, [0.72, 0.06, 0.0, 1.0]],
    [0.42, [1.0, 0.27, 0.025, 1.0]],
    [0.64, [1.0, 0.56, 0.12, 1.0]],
    [0.84, [1.0, 0.84, 0.44, 1.0]],
    [1.0, [1.0, 0.97, 0.84, 1.0]],
]
node("mat_fire", "material", {
    "blend": "additive", "emissive_intensity": 0.8, "soft_particle": True, "depth_fade": 0.15,
    "dissolve": 0.55, "erosion": 0.3, "temperature_gradient": FIRE_RAMP})
node("mat_glow", "material", {"blend": "additive", "shading": "unlit", "soft_particle": True, "depth_fade": 0.12})
node("mat_smoke", "material", {
    "blend": "alpha", "shading": "lit", "base_color": [0.13, 0.105, 0.098, 1.0],
    "soft_particle": True, "depth_fade": 0.3, "dissolve": 0.45, "erosion": 0.3})

# --------------------------------------------------------------------------
# forces, collider
# --------------------------------------------------------------------------
node("f_buoy_fire", "force", {"force_type": "buoyancy", "strength": 2.7})
node("f_curl_fire", "force", {"force_type": "curl_noise", "strength": 1.9, "frequency": 0.9, "octaves": 3, "speed": 1.4})
node("f_curl_fine", "force", {"force_type": "curl_noise", "strength": 1.2, "frequency": 1.8, "octaves": 2, "speed": 2.0})
node("f_surge_lean", "force", {
    "force_type": "directional", "direction": [1.0, 0.0, 0.0],
    "strength": track([(0.0, 0.0), (0.5, 0.0), (0.62, 15.0), (0.85, 9.0), (0.98, 0.0)])})
node("f_gravity", "force", {"force_type": "gravity", "strength": 6.0})
node("f_spark_turb", "force", {"force_type": "turbulence", "strength": 2.6, "frequency": 1.2, "octaves": 2})
node("f_ember_buoy", "force", {"force_type": "buoyancy", "strength": 1.3})
node("f_ember_turb", "force", {"force_type": "turbulence", "strength": 1.5, "frequency": 1.0, "octaves": 2})
node("f_smoke_buoy", "force", {"force_type": "buoyancy", "strength": 1.5})
node("f_smoke_turb", "force", {"force_type": "turbulence", "strength": 1.0, "frequency": 0.6, "octaves": 2})
node("ground", "collider", {"collider_type": "plane", "normal": [0, 1, 0], "bounce": 0.3, "friction": 0.5})

# --------------------------------------------------------------------------
# the hub every travelling node hangs off
# --------------------------------------------------------------------------
node("hub", "mesh", {"primitive": "sphere", "radius": 0.02, "segments": 6, "visible": False,
                     "position": track([(t, [r(hub_x(t)), 0.0, 0.0]) for t in HUB_TIMES])}, layer="wall")

# ============================================================ particles ====
FLAME_COLOR_LIFE = [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.3, [1.0, 0.95, 0.88, 1.0]],
                    [0.62, [1.0, 0.86, 0.72, 1.0]], [1.0, [0.92, 0.68, 0.52, 1.0]]]


def flame_system(nid: str, layer: str, forces: list[str], **over: Any) -> str:
    params: dict[str, Any] = {
        "max_particles": 400, "lifetime": 0.5, "lifetime_variance": 0.18,
        "size": 1.2, "size_variance": 0.4,
        "size_over_life": [[0.0, 0.6], [0.2, 1.0], [0.6, 1.0], [1.0, 0.6]],
        "color": [1.0, 0.55, 0.15, 1.0], "color_over_life": FLAME_COLOR_LIFE,
        "opacity": 0.32, "opacity_over_life": [[0.0, 0.0], [0.08, 1.0], [0.58, 0.8], [1.0, 0.0]],
        "emissive": 0.2, "drag": 2.2, "blend": "additive", "soft_particle_distance": 0.35,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.2, "sprite_fps": 22.0,
    }
    params.update(over)
    return node(nid, "particle_system", params,
                {"sprite": "tex_flame", "material": "mat_fire", "forces": forces}, layer=layer)


def wall_emitter(nid: str, system: str, layer: str, dx: float, y: float, length: float, rate: float,
                 up: float, forward: float, env: list[tuple[float, float]] | None = None, **over: Any) -> str:
    """A line emitter across the wall's width, riding the hub. `direction` is (0, up, forward)
    in the emitter's frame, which the 90 degree turn maps onto world (forward, up, 0)."""
    params: dict[str, Any] = {
        "shape": "line", "length": r(length), "position": [r(dx), r(y), 0.0], "rotation": [0.0, 90.0, 0.0],
        # the wall spreads out of the surge: narrow while the wave forms, full width at speed
        "scale": track([(0.0, [0.34, 1.0, 1.0]), (0.55, [0.34, 1.0, 1.0]), (0.9, [1.0, 1.0, 1.0])]),
        "rate": envelope(rate, env or RATE_ENVELOPE), "direction": [0.0, r(up), r(forward)], "start_time": 0.5,
    }
    params.update(over)
    return node(nid, "emitter", params, {"particle": system}, layer=layer, parent="hub")


# ---- crest: tall, bright, leaning into the direction of travel ------------
flame_system("ps_crest", "wall", ["f_curl_fire", "f_buoy_fire"],
             max_particles=420, lifetime=0.46, lifetime_variance=0.14, size=1.55, size_variance=0.45,
             size_over_life=[[0.0, 0.62], [0.18, 1.0], [0.6, 1.0], [1.0, 0.55]],
             color=[1.0, 0.6, 0.18, 1.0], opacity=0.34,
             opacity_over_life=[[0.0, 0.0], [0.07, 1.0], [0.55, 0.8], [1.0, 0.0]],
             emissive=0.22, drag=2.6, velocity_stretch=0.16, sprite_fps=24.0)
wall_emitter("e_crest", "ps_crest", "wall", 0.15, 1.0, WALL_WIDTH - 0.1, 170.0, 1.0, 0.18,
             velocity=4.4, velocity_variance=1.7, spread=15, inherit_velocity=0.42)
# a taller core in the middle of the wall so its height varies along the width
wall_emitter("e_crest_core", "ps_crest", "wall", 0.05, 1.45, WALL_WIDTH * 0.45, 60.0, 1.0, 0.15,
             velocity=5.2, velocity_variance=1.8, spread=13, inherit_velocity=0.42)

# ---- body: the mass of the wall ------------------------------------------
flame_system("ps_body", "wall", ["f_curl_fire", "f_buoy_fire"],
             max_particles=560, lifetime=0.6, lifetime_variance=0.22, size=1.25, size_variance=0.42,
             opacity=0.3, velocity_stretch=0.24, sprite_fps=21.0)
wall_emitter("e_body", "ps_body", "wall", -0.55, 0.62, WALL_WIDTH, 230.0, 1.0, 0.18,
             velocity=3.0, velocity_variance=1.3, spread=14, inherit_velocity=0.2)

# ---- tongues: long fast licks that tear off the top ----------------------
flame_system("ps_tongue", "wall", ["f_curl_fire", "f_buoy_fire"],
             max_particles=220, lifetime=0.85, lifetime_variance=0.38, size=1.35, size_variance=0.5,
             size_over_life=[[0.0, 0.4], [0.28, 1.1], [1.0, 0.7]],
             color=[1.0, 0.5, 0.12, 1.0],
             color_over_life=[[0.0, [1.0, 0.86, 0.62, 1.0]], [0.3, [1.0, 0.78, 0.5, 1.0]],
                              [0.66, [1.0, 0.62, 0.34, 1.0]], [1.0, [0.9, 0.45, 0.22, 1.0]]],
             opacity=0.34, opacity_over_life=[[0.0, 0.0], [0.12, 1.0], [0.55, 0.72], [1.0, 0.0]],
             emissive=0.18, drag=1.3, velocity_stretch=0.3, sprite_fps=18.0)
wall_emitter("e_tongue", "ps_tongue", "wall", -0.3, 1.0, WALL_WIDTH - 0.4, 62.0, 1.0, 0.1,
             velocity=5.6, velocity_variance=2.4, spread=16, inherit_velocity=0.3)

# ---- lickers: thrown forward along the ground ahead of the front ----------
flame_system("ps_lick", "wall", ["f_curl_fine"],
             max_particles=120, lifetime=0.26, lifetime_variance=0.08, size=0.62, size_variance=0.22,
             size_over_life=[[0.0, 0.5], [0.3, 1.0], [1.0, 0.45]],
             color=[1.0, 0.62, 0.2, 1.0], opacity=0.4,
             opacity_over_life=[[0.0, 0.0], [0.1, 1.0], [0.6, 0.8], [1.0, 0.0]],
             drag=3.5, velocity_stretch=0.12, sprite_fps=26.0)
wall_emitter("e_lick", "ps_lick", "wall", 0.75, 0.24, WALL_WIDTH - 0.5, 70.0, 0.22, 1.0,
             velocity=2.6, velocity_variance=1.2, spread=14, inherit_velocity=0.85)

# ---- hot bed: soft glow that welds the sprite roots into one base ---------
node("ps_base", "particle_system", {
    "max_particles": 260, "lifetime": 0.45, "lifetime_variance": 0.15, "size": 0.95, "size_variance": 0.35,
    "rotation_variance": 180.0, "angular_velocity": 25.0, "angular_velocity_variance": 40.0,
    "size_over_life": [[0.0, 0.5], [0.35, 1.05], [1.0, 0.7]],
    "color": [1.0, 0.75, 0.35, 1.0],
    "color_over_life": [[0.0, [1.0, 0.95, 0.78, 1.0]], [0.35, [1.0, 0.88, 0.6, 1.0]],
                        [0.75, [1.0, 0.78, 0.42, 1.0]], [1.0, [1.0, 0.66, 0.3, 1.0]]],
    "opacity": 0.13, "opacity_over_life": [[0.0, 0.0], [0.18, 1.0], [0.55, 0.55], [1.0, 0.0]],
    "emissive": 0.5, "drag": 2.4, "blend": "additive", "soft_particle_distance": 0.35,
    "render_mode": "billboard", "sprite_fps": 0.0,
}, {"sprite": "tex_puff", "material": "mat_fire", "forces": ["f_curl_fine", "f_buoy_fire"]}, layer="wall")
wall_emitter("e_base", "ps_base", "wall", -0.4, 0.32, WALL_WIDTH - 0.2, 120.0, 1.0, 0.1,
             velocity=1.2, velocity_variance=0.8, spread=40, inherit_velocity=0.15)

# ---- burning strip: lower, longer-lived flames dropped behind the front ---
STRIP_ENVELOPE = [(0.0, 0.0), (0.62, 0.0), (0.8, 0.5), (0.95, 1.0), (1.74, 1.0), (1.86, 0.0)]
flame_system("ps_strip", "strip", ["f_curl_fine", "f_buoy_fire"],
             max_particles=480, lifetime=0.7, lifetime_variance=0.2, size=1.0, size_variance=0.35,
             size_over_life=[[0.0, 0.5], [0.2, 1.0], [0.5, 0.9], [1.0, 0.35]],
             color=[1.0, 0.48, 0.11, 1.0], opacity=0.3,
             opacity_over_life=[[0.0, 0.0], [0.12, 1.0], [0.6, 0.75], [1.0, 0.0]],
             drag=2.0, velocity_stretch=0.3, sprite_fps=20.0)
wall_emitter("e_strip", "ps_strip", "strip", -1.0, 0.42, WALL_WIDTH - 0.3, 250.0, 1.0, 0.0,
             env=STRIP_ENVELOPE, velocity=2.3, velocity_variance=1.1, spread=24, start_time=0.62)

# ============================================================ sparks etc ===
node("ps_spark", "particle_system", {
    "max_particles": 900, "lifetime": 0.75, "lifetime_variance": 0.35, "size": 0.036, "size_variance": 0.018,
    "color": [1.0, 0.7, 0.25, 1.0],
    "color_over_life": [[0.0, [1.0, 0.92, 0.62, 1.0]], [0.4, [1.0, 0.5, 0.1, 1.0]], [1.0, [0.6, 0.08, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 1.0], [0.75, 1.0], [1.0, 0.0]],
    "emissive": 6.0, "drag": 0.5, "render_mode": "stretched_billboard", "velocity_stretch": 0.1,
    "blend": "additive",
}, {"sprite": "tex_spark", "material": "mat_glow", "forces": ["f_gravity", "f_spark_turb"],
    "colliders": ["ground"]}, layer="sparks")
wall_emitter("e_spark", "ps_spark", "sparks", 0.1, 0.5, WALL_WIDTH - 0.4, 260.0, 1.0, 0.55,
             velocity=4.5, velocity_variance=2.8, spread=38, inherit_velocity=0.55)

node("ps_ember", "particle_system", {
    "max_particles": 520, "lifetime": 1.3, "lifetime_variance": 0.55, "size": 0.03, "size_variance": 0.013,
    "color": [1.0, 0.45, 0.1, 1.0],
    "color_over_life": [[0.0, [1.0, 0.85, 0.5, 1.0]], [0.5, [1.0, 0.4, 0.06, 1.0]], [1.0, [0.45, 0.05, 0.0, 1.0]]],
    "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.75, 0.7], [1.0, 0.0]],
    "emissive": 4.2, "drag": 1.0, "blend": "additive",
}, {"sprite": "tex_spark", "material": "mat_glow", "forces": ["f_ember_buoy", "f_ember_turb"]}, layer="sparks")
wall_emitter("e_ember", "ps_ember", "sparks", -1.8, 0.5, WALL_WIDTH - 0.4, 95.0, 1.0, 0.0,
             velocity=1.2, velocity_variance=0.8, spread=45, inherit_velocity=0.1)
# embers keep lifting off the whole burnt strip while the fire dies
node("e_ember_fade", "emitter", {
    "shape": "box", "size": [8.6, 0.2, WALL_WIDTH - 0.6], "position": [0.9, 0.25, 0.0],
    "rate": track([(0.0, 0.0), (1.75, 0.0), (2.0, 170.0), (2.3, 120.0), (2.45, 0.0)]),
    "velocity": 0.9, "velocity_variance": 0.5, "direction": [0, 1, 0], "spread": 35, "start_time": 1.75,
}, {"particle": "ps_ember"}, layer="sparks")

node("ps_smoke", "particle_system", {
    "max_particles": 160, "lifetime": 1.25, "lifetime_variance": 0.4, "size": 0.95, "size_variance": 0.3,
    "size_over_life": [[0.0, 0.45], [1.0, 1.8]],
    "color": [0.11, 0.088, 0.082, 1.0], "opacity": 0.3,
    "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [1.0, 0.0]],
    "drag": 1.0, "blend": "alpha", "sort": True, "angular_velocity": 20.0, "angular_velocity_variance": 20.0,
}, {"sprite": "tex_puff", "material": "mat_smoke", "forces": ["f_smoke_buoy", "f_smoke_turb"]}, layer="sparks")
wall_emitter("e_smoke", "ps_smoke", "sparks", -2.6, 1.3, WALL_WIDTH - 0.8, 36.0, 1.0, 0.0,
             env=[(0.0, 0.0), (0.8, 0.0), (1.0, 1.0), (1.9, 1.0), (2.0, 0.0)],
             velocity=1.2, velocity_variance=0.5, spread=30, start_time=0.8)

# ================================================================= cast ====
node("rune", "decal", {
    "shape": "circle", "position": [X_CAST, 0.02, 0.0], "size": [2.7, 2.7],
    "rotation": track([(0.0, [0.0, 0.0, 0.0]), (1.0, [0.0, 38.0, 0.0])]),
    "color": [1.0, 0.5, 0.14, 1.0], "blend": "additive",
    "emissive": track([(0.0, 0.6), (0.1, 2.2), (0.3, 1.8), (0.36, 3.6), (0.6, 1.6), (0.95, 0.0)]),
    "opacity": track([(0.0, 0.0), (0.04, 0.4), (0.13, 1.0), (0.6, 0.9), (0.95, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "duration": 1.0,
}, {"texture": "tex_rune"}, layer="cast")
node("cast_glow", "decal", {
    "shape": "circle", "position": [X_CAST, 0.012, 0.0], "size": [3.6, 3.6],
    "color": [1.0, 0.42, 0.1, 1.0], "blend": "additive",
    "emissive": track([(0.0, 0.0), (0.2, 0.5), (0.36, 1.4), (0.7, 0.8), (1.05, 0.0)]),
    "opacity": track([(0.0, 0.0), (0.15, 0.7), (0.7, 0.7), (1.05, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "duration": 1.1,
}, {"texture": "tex_glow"}, layer="cast")
# the soft upward glow: a wide column of puffs, never a rod
node("e_cast_glow", "emitter", {
    "shape": "disc", "radius": 0.7, "position": [X_CAST, 0.3, 0.0],
    "rate": track([(0.0, 40.0), (0.1, 110.0), (0.3, 110.0), (0.42, 0.0)]),
    "velocity": 2.6, "velocity_variance": 0.9, "direction": [0, 1, 0], "spread": 7, "duration": 0.45,
}, {"particle": "ps_base"}, layer="cast")
# motes drawn in over the circle and lifted
node("e_cast_motes", "emitter", {
    "shape": "ring", "radius": 1.2, "inner_radius": 0.35, "position": [X_CAST, 0.06, 0.0],
    "rate": track([(0.0, 150.0), (0.28, 150.0), (0.36, 0.0)]),
    "velocity": 1.7, "velocity_variance": 0.9, "direction": [0, 1, 0], "spread": 18,
    "radial_velocity": -0.9, "duration": 0.4,
}, {"particle": "ps_ember"}, layer="cast")

# ================================================================ surge ====
flame_system("ps_surge", "surge", ["f_curl_fire", "f_buoy_fire", "f_surge_lean"],
             max_particles=420, lifetime=0.47, lifetime_variance=0.13, size=1.65, size_variance=0.55,
             size_over_life=[[0.0, 0.35], [0.25, 1.05], [1.0, 0.55]],
             color=[1.0, 0.58, 0.16, 1.0], opacity=0.34,
             opacity_over_life=[[0.0, 0.0], [0.1, 1.0], [0.58, 0.82], [1.0, 0.0]],
             emissive=0.24, drag=2.4, velocity_stretch=0.2, sprite_fps=22.0)
node("e_surge", "emitter", {
    "shape": "disc", "radius": 0.85, "position": [X_CAST, 0.55, 0.0],
    "rate": track([(0.0, 0.0), (0.3, 260.0), (0.55, 210.0), (0.7, 0.0)]),
    "burst_count": 36, "burst_times": [0.0],
    "velocity": 6.4, "velocity_variance": 2.3, "direction": [0, 1, 0], "spread": 13,
    "start_time": 0.3, "duration": 0.42,
}, {"particle": "ps_surge"}, layer="surge")
node("e_surge_spark", "emitter", {
    "shape": "disc", "radius": 0.8, "position": [X_CAST, 0.3, 0.0],
    "rate": track([(0.0, 0.0), (0.3, 260.0), (0.6, 160.0), (0.7, 0.0)]),
    "burst_count": 110, "burst_times": [0.0],
    "velocity": 6.5, "velocity_variance": 3.0, "direction": [0, 1, 0], "spread": 32,
    "start_time": 0.3, "duration": 0.42,
}, {"particle": "ps_spark"}, layer="surge")

# =============================================================== ground ====
# scorch: five dark segments, each opened once the hub is past its far end
SCORCH_LEN, SCORCH_STEP, SCORCH_WIDTH = 2.45, 2.05, 3.5
node("scorch_cast", "decal", {
    "shape": "circle", "position": [X_CAST, 0.003, 0.0], "size": [3.1, 3.1],
    "color": [0.02, 0.015, 0.01, 1.0], "blend": "alpha",
    "opacity": track([(0.0, 0.0), (0.32, 0.0), (0.6, 0.8), (2.2, 0.8), (DUR, 0.3)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": 0.3,
}, {"texture": "tex_glow"}, layer="ground")

scorch_ids: list[str] = []
crack_ids: list[str] = []
x = X_CAST + 0.35 + SCORCH_LEN / 2.0
i = 0
while x - SCORCH_LEN / 2.0 < X_END - 0.2:
    t0 = t_arrive(min(X_END, x + SCORCH_LEN / 2.0 + 0.55))
    scorch_ids.append(node(f"scorch_{i}", "decal", {
        "shape": "rect", "position": [r(x), r(0.004 + 0.0008 * i), 0.0],
        "rotation": [0.0, 180.0 if i % 2 else 0.0, 0.0], "size": [SCORCH_LEN, SCORCH_WIDTH],
        "color": [0.02, 0.015, 0.01, 1.0], "blend": "alpha",
        "opacity": track([(0.0, 0.0), (t0, 0.0), (t0 + 0.14, 0.82), (2.25, 0.82), (DUR, 0.3)]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": r(t0),
    }, {"texture": "tex_scorch"}, layer="ground"))
    x += SCORCH_STEP
    i += 1

# glowing cracks: shorter segments so the cooling runs along the strip
CRACK_LEN, CRACK_STEP, CRACK_WIDTH = 1.7, 1.32, 3.1
CRACK_HOT = [1.0, 0.62, 0.22, 1.0]
CRACK_WARM = [1.0, 0.36, 0.07, 1.0]
CRACK_DULL = [0.95, 0.16, 0.02, 1.0]


def lerp_color(a: list[float], b: list[float], f: float) -> list[float]:
    f = min(1.0, max(0.0, f))
    return [r(a[k] + (b[k] - a[k]) * f) for k in range(4)]


def crack_glow(age: float) -> float:
    """Emissive of a crack `age` seconds after it opened: white-hot, then a long ember tail."""
    for (a0, e0), (a1, e1) in zip(CRACK_COOLING, CRACK_COOLING[1:]):
        if age <= a1:
            return e0 + (e1 - e0) * (age - a0) / (a1 - a0)
    return CRACK_COOLING[-1][1]


CRACK_COOLING = [(0.0, 2.6), (0.3, 1.3), (0.75, 0.6), (1.6, 0.18)]


def crack_color(age: float) -> list[float]:
    if age <= 0.35:
        return lerp_color(CRACK_HOT, CRACK_WARM, age / 0.35)
    return lerp_color(CRACK_WARM, CRACK_DULL, (age - 0.35) / 0.6)


x = X_CAST + 0.5 + CRACK_LEN / 2.0
i = 0
while x - CRACK_LEN / 2.0 < X_END - 0.3:
    t0 = t_arrive(min(X_END, x + CRACK_LEN / 2.0 + 0.5))
    hot = t0 + 0.1
    # every key after the crack opens, clamped inside the effect; the glow also dies with the fade phase
    times = sorted({r(t) for t in (hot, hot + 0.15, hot + 0.3, hot + 0.75, 2.15, 2.35, DUR) if hot <= t <= DUR})
    end_fade = lambda t: 1.0 if t <= 2.15 else max(0.0, 1.0 - (t - 2.15) / (DUR - 2.15)) ** 1.5   # noqa: E731
    crack_ids.append(node(f"crack_{i}", "decal", {
        "shape": "rect", "position": [r(x), r(0.014 + 0.0012 * i), 0.0],
        "rotation": [0.0, 180.0 if i % 2 else 0.0, 0.0], "size": [CRACK_LEN, CRACK_WIDTH],
        "color": track([(0.0, CRACK_HOT)] + [(t, crack_color(t - hot)) for t in times]),
        "blend": "additive",
        "emissive": track([(0.0, 0.0), (t0, 0.0)] + [(t, crack_glow(t - hot) * (0.35 + 0.65 * end_fade(t))) for t in times]),
        "opacity": track([(0.0, 0.0), (t0, 0.0)] + [(t, end_fade(t)) for t in times]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": r(t0),
    }, {"texture": "tex_crack_a" if i % 2 == 0 else "tex_crack_b"}, layer="ground"))
    x += CRACK_STEP
    i += 1

# warm pool on the ground under the front
node("front_glow", "decal", {
    "shape": "circle", "position": hub_track(-0.5, 0.034), "size": [5.2, 4.2],
    "color": [1.0, 0.45, 0.1, 1.0], "blend": "additive",
    "emissive": track([(0.0, 0.0), (0.55, 0.0), (0.9, 0.9), (1.9, 0.9), (2.1, 0.0)]),
    "opacity": track([(0.0, 0.0), (0.55, 0.0), (0.85, 0.75), (1.9, 0.75), (2.1, 0.0)]),
    "fade_in": 0.0, "fade_out": 0.0, "start_time": 0.55, "duration": 1.6,
}, {"texture": "tex_glow"}, layer="ground")

# =============================================================== impact ====
flare_times = [max(1.8, t_arrive(fx)) for fx, _ in FLARES]
for i, ((fx, fz), ft) in enumerate(zip(FLARES, flare_times)):
    node(f"e_flare_{i}", "emitter", {
        "shape": "disc", "radius": 0.75, "position": [r(fx), 0.55, r(fz)],
        "rate": track([(0.0, 0.0), (ft, 330.0), (ft + 0.1, 0.0)]),
        "burst_count": 46, "burst_times": [0.0],
        "velocity": 7.6, "velocity_variance": 2.6, "direction": [0, 1, 0], "spread": 19,
        "start_time": r(ft), "duration": 0.12,
    }, {"particle": "ps_surge"}, layer="impact")
    node(f"e_flare_bed_{i}", "emitter", {
        "shape": "disc", "radius": 0.9, "position": [r(fx), 0.4, r(fz)],
        "rate": 0, "burst_count": 16, "burst_times": [0.0],
        "velocity": 2.2, "velocity_variance": 1.2, "direction": [0, 1, 0], "spread": 50,
        "start_time": r(ft), "duration": 0.1,
    }, {"particle": "ps_base"}, layer="impact")
    node(f"e_flare_spark_{i}", "emitter", {
        "shape": "disc", "radius": 0.6, "position": [r(fx), 0.35, r(fz)],
        "rate": 0, "burst_count": 120, "burst_times": [0.0],
        "velocity": 7.0, "velocity_variance": 3.4, "direction": [0.25, 1, 0], "spread": 52,
        "start_time": r(ft), "duration": 0.1,
    }, {"particle": "ps_spark"}, layer="impact")
    node(f"l_flare_{i}", "light", {
        "light_type": "point", "position": [r(fx - 0.2), 1.5, r(fz + 0.5)],
        "color": [1.0, 0.62, 0.26, 1.0], "radius": 11.0,
        "intensity": track([(0.0, 0.0), (ft - 0.01, 0.0), (ft + 0.05, 13.0), (ft + 0.16, 4.0), (ft + 0.32, 0.0)]),
        "start_time": r(ft - 0.01), "duration": 0.36,
    }, layer="impact")

# ================================================================ light ====
node("l_cast", "light", {
    "light_type": "point", "position": [X_CAST, 0.95, 0.3], "color": [1.0, 0.56, 0.2, 1.0], "radius": 8.0,
    "intensity": track([(0.0, 0.0), (0.12, 1.5), (0.3, 2.2), (0.36, 9.0), (0.6, 4.5), (1.0, 0.0)]),
    "flicker_amplitude": 0.2, "flicker_frequency": 14.0, "duration": 1.05,
}, layer="light")
node("l_front", "light", {
    "light_type": "point", "position": [-0.5, 1.35, 0.4], "color": [1.0, 0.55, 0.18, 1.0], "radius": 10.0,
    "intensity": track([(0.0, 0.0), (0.55, 0.0), (0.9, 5.5), (1.9, 6.0), (2.12, 0.0)]),
    "flicker_amplitude": 0.22, "flicker_frequency": 13.0, "start_time": 0.55, "duration": 1.6,
}, layer="light", parent="hub")
node("l_trail", "light", {
    "light_type": "point", "position": [-3.4, 0.9, 0.3], "color": [1.0, 0.42, 0.1, 1.0], "radius": 8.0,
    "intensity": track([(0.0, 0.0), (0.95, 0.0), (1.2, 2.4), (1.9, 2.4), (2.3, 0.0)]),
    "flicker_amplitude": 0.25, "flicker_frequency": 10.0, "start_time": 0.95, "duration": 1.4,
}, layer="light", parent="hub")
# a low ember light over the burnt strip so the cracks and the smoke still read in the fade
node("l_after", "light", {
    "light_type": "point", "position": [1.6, 1.3, 0.6], "color": [1.0, 0.4, 0.1, 1.0], "radius": 13.0,
    "intensity": track([(0.0, 0.0), (1.85, 0.0), (2.1, 1.8), (2.35, 1.0), (DUR, 0.3)]),
    "start_time": 1.85,
}, layer="light")
node("haze", "post_effect", {"post_type": "heat_haze", "intensity": 0.28, "frequency": 5.0,
                             "start_time": 0.35, "duration": 1.9}, layer="light")


# --------------------------------------------------------------------------
# controls
# --------------------------------------------------------------------------
def multiplier(cid: str, label: str, group: str, bindings: list[tuple[str, str]], hi: float = 3.0) -> dict[str, Any]:
    return {"id": cid, "label": label, "group": group, "min": 0.0, "max": hi, "default": 1.0, "value": 1.0,
            "step": 0.01, "unit": "x",
            "bindings": [{"node": n, "parameter": p, "op": "multiply"} for n, p in bindings]}


FLAME_SYSTEMS = ["ps_crest", "ps_body", "ps_tongue", "ps_lick", "ps_strip", "ps_surge"]
HEIGHT_EMITTERS = ["e_crest", "e_crest_core", "e_body", "e_tongue", "e_surge"] + [f"e_flare_{i}" for i in range(len(FLARES))]
LINE_EMITTERS = [n["id"] for n in nodes if n["type"] == "emitter" and n.get("parameters", {}).get("shape") == "line"]
SPARK_EMITTERS = [n["id"] for n in nodes if n["type"] == "emitter"
                  and n.get("inputs", {}).get("particle") in ("ps_spark", "ps_ember")]
LIGHTS = [n["id"] for n in nodes if n["type"] == "light"]

COLOR_PARAMS = ("color", "color_over_life", "temperature_gradient", "emissive_color")
hue_bindings = [{"node": n["id"], "parameter": p, "op": "hue_shift"}
                for n in nodes if n["type"] in ("particle_system", "material", "light", "decal")
                and n["id"] not in ("ps_smoke", "mat_smoke", "scorch_cast", *scorch_ids)
                for p in COLOR_PARAMS if p in n.get("parameters", {})]

controls = [
    multiplier("flame_height", "Flame height", "Flame wall",
               [(s, "size") for s in ("ps_crest", "ps_body", "ps_tongue", "ps_surge")] +
               [(e, "velocity") for e in HEIGHT_EMITTERS], hi=2.0),
    multiplier("wave_width", "Wave width", "Flame wall",
               [(e, "length") for e in LINE_EMITTERS] +
               [(d, "size") for d in (*scorch_ids, *crack_ids, "front_glow")], hi=2.0),
    multiplier("fire_intensity", "Fire intensity", "Global",
               [(s, "color") for s in (*FLAME_SYSTEMS, "ps_base")] + [(l, "intensity") for l in LIGHTS] +
               [("front_glow", "emissive"), ("cast_glow", "emissive")]),
    multiplier("embers", "Sparks and embers", "Sparks, embers, smoke",
               [(e, "rate") for e in SPARK_EMITTERS] +
               [(e, "burst_count") for e in SPARK_EMITTERS if e.startswith(("e_flare_spark", "e_surge_spark"))]),
    multiplier("scorch_trail", "Scorch trail", "Ground scorch",
               [(d, "opacity") for d in ("scorch_cast", *scorch_ids)] +
               [(d, "emissive") for d in crack_ids] + [(d, "opacity") for d in crack_ids], hi=2.5),
    {"id": "hue", "label": "Flame hue", "group": "Global", "min": -180.0, "max": 180.0, "default": 0.0,
     "value": 0.0, "step": 1.0, "unit": "deg", "bindings": hue_bindings},
    {"id": "global_speed", "label": "Speed", "group": "Global", "min": 0.25, "max": 4.0, "default": 1.0,
     "value": 1.0, "step": 0.05, "unit": "x",
     "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]},
]

effect = {
    "schema_version": "0.1.0",
    "name": "Flame Wave",
    "description": "A rune circle erupts and a searing wall of fire races ten metres along the ground, leaving a "
                   "burning strip and glowing scorched cracks, then flares up three times where it hits.",
    "duration": DUR,
    "seed": SEED,
    "timeline": {"phases": [{"name": n, "start": s, "end": e} for n, s, e in PHASES]},
    "layers": [
        {"id": "cast", "name": "Cast circle", "role": "telegraph"},
        {"id": "surge", "name": "Flame surge", "role": "ignition"},
        {"id": "wall", "name": "Flame wall", "role": "primary"},
        {"id": "strip", "name": "Burning strip", "role": "secondary"},
        {"id": "sparks", "name": "Sparks, embers, smoke", "role": "secondary"},
        {"id": "impact", "name": "Impact flare-ups", "role": "interaction"},
        {"id": "ground", "name": "Ground scorch", "role": "aftermath"},
        {"id": "light", "name": "Light", "role": "interaction"},
    ],
    "nodes": nodes,
    "controls": controls,
    "metadata": {
        "generator": "tools/generators/flame_wave.py",
        "analysis": "cast 0-0.3 rune circle with a soft upward glow | flame_surge 0.3-0.6 flames burst out of the "
                    "circle | wave_forms 0.6-0.9 the surge leans forward and spreads into a wall | travel_forward "
                    "0.9-1.8 the wall races along +X with a leaning crest, ground lickers, thrown sparks, a burning "
                    "strip and a scorch that opens behind it | impact 1.8-2.1 three staggered flare-ups | fade "
                    "2.1-2.5 embers, thin smoke, cooling cracks",
        "layout": {"cast_circle": [X_CAST, 0.0, 0.0], "end": [X_END, 0.0, 0.0], "speed": r(SPEED, 3),
                   "wall_width": WALL_WIDTH, "flare_ups": [[fx, 0.0, fz] for fx, fz in FLARES]},
        "render_settings": {
            "ground_albedo": 0.08,
            "background": [0.0, 0.0, 0.0, 1.0],
            "bloom_intensity": 0.2,
            "bloom_radius": 0.05,
            "exposure": 0.95,
            "grid": False,
        },
        "tags": ["fire", "wave", "ground", "traveling", "aoe", "line"],
    },
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO_ROOT / "examples" / "effects"),
                    help="directory to write flame_wave.json into (default: examples/effects)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{SLUG}.json"
    path.write_text(json.dumps(effect, indent=1) + "\n", encoding="utf-8")
    print(f"{path}  ({len(nodes)} nodes, {len(controls)} controls)")


if __name__ == "__main__":
    main()
