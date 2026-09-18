#!/usr/bin/env python3
"""Generate examples/effects/meteor_shower.json: a barrage of small meteors on a target area.

    python tools/generators/meteor_shower.py --out examples/effects
    python tools/generators/meteor_shower.py --check examples/effects/meteor_shower.json

The concept sheet: "A barrage of meteors streaks across the sky, showering the target area
with flaming rocks, creating multiple impacts in quick succession and leaving the ground
burning."  4.0 s, phases telegraph / meteors_appear / barrage / continuous_impacts /
final_strikes / aftermath.

LAYOUT (metres, +y up, ground y = 0, camera on +z and a little -x)
  The target area is a disc of radius AREA = 5 m about the origin.  Every meteor enters at
  y = ENTRY_H on a steep parallel diagonal - about 62-69 degrees from horizontal, travelling
  towards +x and slightly +z, so the whole barrage reads as one direction crossing the frame
  from upper left to lower right - and lands at a scattered point in (or just outside) the
  disc.  Two of the thirteen land outside for spill.

THE METEOR TABLE
  `METEORS` is the art direction: landing point, rock size, impact time, flight time, tilt
  from vertical and azimuth, one row per meteor.  Everything about timing, spread, size and
  stagger is read off that table, so the barrage is deterministic and hand-tuned: impacts are
  0.13-0.27 s apart, never in lockstep, and the last two are the biggest.

NODE ECONOMY (the reason this file looks the way it does)
  `aetherfx.community` caps an effect at 160 nodes, and 13 meteors x (hub + flight + fireball +
  flash + sparks + dust + debris + scorch) is far past that.  So:
  * a meteor in flight costs TWO nodes - `lane_i` (an invisible hub at the landing point, which
    is what the `area_radius` control multiplies) and `e_streak_i`, an emitter parented to that
    hub whose own `position` track flies it in from the entry offset.  One shared `ps_streak` /
    `ps_streak_big` particle system draws every flame, so the whole barrage is two draw calls.
  * an impact costs ZERO per-meteor nodes: every impact emitter has ONE `position` track with
    `step` interpolation that jumps to the next landing point while it is idle, and a
    `burst_times` list with all thirteen impact moments.  One node, thirteen impacts, thirteen
    places.  Two flash lights alternate (even and odd impacts) so a light can still be fading
    when the next one fires.
  Authored impact times were chosen over collider + `event` nodes: the sheet specifies the
  stagger, and a burst list is exact, cheaper and directly art-directable.

HOUSE STYLE
  All fire is the `fire_sim` flipbook coloured by the material's `temperature_gradient` (never
  the `flame` op), sprites overlap into a continuous body and the material carries
  dissolve + erosion for ragged edges.  A streak is a comet, not a rod: the tail is a dense
  train of flame sprites left behind in world space, tapering and fading over life, spread and
  curled by noise.  The impact flash is a soft halo with 11 + 17 irregular noise-broken rays -
  no crosses anywhere.  The telegraph is concentric rings and dash bands.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

SLUG = "meteor_shower"
NAME = "Meteor Shower"
DESCRIPTION = ("A barrage of flaming meteors streaks in on a steep diagonal and hammers the marked area with "
               "staggered impacts, leaving the ground cracked, burning and smoking.")

DUR = 4.0
SEED = 730941
AREA = 5.0            # target-disc radius
ENTRY_H = 12.6        # every meteor enters at this height

# phase boundaries from the sheet
T_TELE, T_APPEAR, T_BARRAGE, T_CONT, T_FINAL, T_AFTER = 0.0, 0.4, 0.7, 1.5, 2.8, 3.2

# landing x, landing z, rock size (m), impact time, flight time, tilt from vertical, azimuth
METEORS: tuple[tuple[float, float, float, float, float, float, float], ...] = (
    (-1.7, 1.2, 0.40, 0.78, 0.37, 24.0, 11.0),
    (2.4, -1.8, 0.32, 0.92, 0.34, 27.0, 6.0),
    (-3.2, -2.5, 0.50, 1.19, 0.39, 22.0, 16.0),
    (0.9, 3.3, 0.29, 1.36, 0.32, 26.0, 9.0),
    (3.9, 1.4, 0.44, 1.61, 0.36, 23.0, 14.0),
    (-2.3, -0.5, 0.35, 1.75, 0.33, 28.0, 7.0),
    (1.3, -5.3, 0.53, 2.02, 0.40, 24.0, 18.0),
    (-4.4, 2.1, 0.31, 2.16, 0.31, 25.0, 12.0),
    (2.8, 2.9, 0.41, 2.33, 0.35, 21.0, 5.0),
    (-0.8, -3.0, 0.47, 2.58, 0.38, 27.0, 15.0),
    (5.6, -0.9, 0.33, 2.71, 0.32, 23.0, 9.0),
    (-2.0, 4.0, 0.60, 2.90, 0.42, 22.0, 13.0),
    (1.1, 0.3, 0.70, 3.10, 0.45, 25.0, 8.0),
)
BIG = {2, 6, 9, 11, 12}        # heavier rocks: the bigger streak system, a second fireball burst
HEAVY = {6, 11, 12}            # the three that get the large fireball system

rng = random.Random(8812)
nodes: list[dict[str, Any]] = []


def r(v: float, n: int = 4) -> float:
    return round(float(v), n)


def vec(v, n: int = 4) -> list[float]:
    return [r(c, n) for c in v]


def node(nid: str, ntype: str, params=None, inputs=None, layer=None, parent=None, seed=None) -> str:
    n: dict[str, Any] = {"id": nid, "type": ntype}
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


def track(keys) -> dict[str, Any]:
    """keys = [(t, value[, interp]), ...] -> {"value": first, "track": [...]}"""
    out: list[dict[str, Any]] = []
    last: float | None = None
    for k in keys:
        t = r(k[0], 4)
        if last is not None and t <= last:
            continue
        last = t
        item: dict[str, Any] = {"time": t, "value": k[1]}
        if len(k) > 2 and k[2]:
            item["interp"] = k[2]
        out.append(item)
    return {"value": out[0]["value"], "track": out}


def g(nid: str, op: str, params=None, inputs=None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": nid, "op": op}
    if params:
        d["params"] = params
    if inputs:
        d["inputs"] = inputs
    return d


def entry_offset(tilt_deg: float, azimuth_deg: float) -> tuple[float, float, float]:
    """Where the meteor enters, relative to where it lands (y = ENTRY_H by construction)."""
    tilt, az = math.radians(tilt_deg), math.radians(azimuth_deg)
    run = ENTRY_H * math.tan(tilt)
    return (-run * math.cos(az), ENTRY_H, -run * math.sin(az))


def back_dir(tilt_deg: float, azimuth_deg: float) -> tuple[float, float, float]:
    """Unit vector up the flight path: where the flame streams."""
    off = entry_offset(tilt_deg, azimuth_deg)
    length = math.sqrt(sum(c * c for c in off))
    return (off[0] / length, off[1] / length, off[2] / length)


def stepped(times, points) -> dict[str, Any]:
    """A position track that jumps to the next place 0.06 s before it is used."""
    keys = [(0.0, list(points[0]), "step")]
    keys += [(max(0.02, t - 0.06), list(p), "step") for t, p in zip(times, points)]
    return track(keys)


FIRE_RAMP = [
    [0.0, [0.30, 0.02, 0.0, 1.0]],
    [0.2, [0.72, 0.06, 0.0, 1.0]],
    [0.42, [1.0, 0.27, 0.025, 1.0]],
    [0.64, [1.0, 0.56, 0.12, 1.0]],
    [0.84, [1.0, 0.84, 0.44, 1.0]],
    [1.0, [1.0, 0.97, 0.84, 1.0]],
]


# =========================================================================
# camera: low-ish wide three-quarter, the disc low and the entry band high
# =========================================================================
def build_camera() -> None:
    eye = (-5.0, 6.9, 23.5)
    target = (-0.4, 5.0, 0.0)
    pos = tuple(target[i] + (eye[i] - target[i]) / 1.45 for i in range(3))
    node("cam", "camera", {"position": vec(pos, 3), "target": vec(target, 3), "fov": 52})


# =========================================================================
# textures
# =========================================================================
def build_textures() -> None:
    # ONE fire_sim flipbook for every flame in the effect: streaks, fireballs, ground fires.
    # Greyscale heat, v-mirrored so the fuel bed sits at the bottom of the sprite; the colour
    # comes from mat_fire.temperature_gradient, which is what the `hue` control rotates.
    node("tex_fire", "texture", {"width": 128, "height": 192, "frames": 20, "graph": {"nodes": [
        g("sim", "fire_sim", {"seed": 17, "fuel": 1.2, "fuel_width": 0.62, "buoyancy": 2.5,
                              "turbulence": 1.7, "turbulence_scale": 3.0, "cooling": 1.6,
                              "detail": 4, "speed": 0.065, "substeps": 4, "loop": True,
                              "flicker": 0.5, "sharpness": 1.2}),
        g("half", "constant", {"color": [0.5, 0.5, 0.5, 1.0]}),
        g("vgr", "gradient_linear", {"angle": 90, "start": 1.0, "end": 0.0}),
        g("byv", "channel_pack", {"channel": "luminance"}, {"r": "half", "g": "vgr", "b": "half"}),
        g("flip", "distort", {"amount": 2.0}, {"a": "sim", "by": "byv"}),
        g("gu", "gradient_linear", {"angle": 0, "start": 0.0, "end": 1.0}),
        g("gi", "invert", None, {"a": "gu"}),
        g("par", "math", {"mode": "multiply"}, {"a": "gu", "b": "gi"}),
        g("hm", "levels", {"in_high": 0.2, "gamma": 0.8}, {"a": "par"}),
        g("m1", "math", {"mode": "multiply"}, {"a": "flip", "b": "hm"}),
        g("vr0", "gradient_linear", {"angle": 90, "start": 1.0, "end": 0.0}),
        g("vr", "levels", {"in_high": 0.28, "gamma": 0.82}, {"a": "vr0"}),
        g("m2", "math", {"mode": "multiply"}, {"a": "m1", "b": "vr"}),
        g("lv", "levels", {"in_low": 0.12, "in_high": 0.94}, {"a": "m2"}),
    ], "output": "lv"}})

    # ragged animated puff: smoke and dust
    node("tex_puff", "texture", {"width": 96, "height": 96, "frames": 8, "graph": {"nodes": [
        g("rd", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("n", "fbm", {"frequency": 3.0, "octaves": 4, "seed": 5, "animate": 1.0}),
        g("m", "math", {"mode": "multiply"}, {"a": "rd", "b": "n"}),
        g("l", "levels", {"in_low": 0.05, "in_high": 0.55}, {"a": "m"}),
    ], "output": "l"}})

    # dissolve / erosion noise for the fire and smoke materials
    node("tex_erosion", "texture", {"width": 128, "height": 128, "graph": {"nodes": [
        g("a", "fbm", {"frequency": 5.0, "octaves": 5, "gain": 0.55, "seed": 3301, "tile": True}),
        g("b", "fbm", {"frequency": 13.0, "octaves": 3, "gain": 0.5, "seed": 7717, "tile": True}),
        g("m", "math", {"mode": "lerp", "factor": 0.35}, {"a": "a", "b": "b"}),
        g("l", "levels", {"in_low": 0.27, "in_high": 0.76}, {"a": "m"}),
    ], "output": "l"}})

    node("tex_glow", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
        g("rd", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
    ], "output": "rd"}})

    node("tex_spark", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
        g("rd", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
    ], "output": "rd"}})

    # impact flash: a small hot core in a soft halo with 11 + 17 uneven rays broken by noise
    node("tex_flare", "texture", {"width": 128, "height": 128, "graph": {"nodes": [
        g("s1", "star", {"points": 11, "width": 0.055, "outer_radius": 0.40, "core_radius": 0.05,
                         "softness": 0.05, "rotation": 13.0}),
        g("s2", "star", {"points": 17, "width": 0.035, "outer_radius": 0.26, "core_radius": 0.04,
                         "softness": 0.04, "rotation": 31.0}),
        g("n", "fbm", {"frequency": 2.8, "octaves": 3, "seed": 43}),
        g("nl", "levels", {"in_low": 0.28, "in_high": 0.74, "out_low": 0.05, "out_high": 1.0}, {"a": "n"}),
        g("n2", "fbm", {"frequency": 3.6, "octaves": 3, "seed": 149}),
        g("n2l", "levels", {"in_low": 0.30, "in_high": 0.72, "out_low": 0.2, "out_high": 1.0}, {"a": "n2"}),
        g("s1n", "math", {"mode": "multiply"}, {"a": "s1", "b": "nl"}),
        g("s2n", "math", {"mode": "multiply"}, {"a": "s2", "b": "n2l"}),
        g("rays", "math", {"mode": "max"}, {"a": "s1n", "b": "s2n"}),
        g("fall", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("raysf", "math", {"mode": "multiply"}, {"a": "rays", "b": "fall"}),
        g("rb", "blur", {"radius": 4.0}, {"a": "raysf"}),
        g("rbl", "levels", {"out_high": 0.58}, {"a": "rb"}),
        g("halo", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        g("halol", "levels", {"out_high": 0.42}, {"a": "halo"}),
        g("core", "gradient_radial", {"radius": 0.13, "falloff": "quadratic"}),
        g("k1", "math", {"mode": "screen"}, {"a": "rbl", "b": "halol"}),
        g("k2", "math", {"mode": "max"}, {"a": "k1", "b": "core"}),
    ], "output": "k2"}})

    # telegraph: concentric rings, three dash bands, an uneven glow.  No crosses, no glyphs.
    node("tex_rune", "texture", {"width": 384, "height": 384, "graph": {"nodes": [
        g("r1", "ring", {"radius": 0.472, "thickness": 0.008, "softness": 0.005}),
        g("r2", "ring", {"radius": 0.414, "thickness": 0.0045, "softness": 0.004}),
        g("r3", "ring", {"radius": 0.330, "thickness": 0.007, "softness": 0.005}),
        g("r4", "ring", {"radius": 0.216, "thickness": 0.0045, "softness": 0.004}),
        g("r5", "ring", {"radius": 0.106, "thickness": 0.006, "softness": 0.005}),
        g("d1", "spokes", {"count": 36, "width": 0.010, "softness": 0.005,
                           "inner_radius": 0.428, "outer_radius": 0.460, "rotation": 5.0}),
        g("d2", "spokes", {"count": 20, "width": 0.013, "softness": 0.006,
                           "inner_radius": 0.346, "outer_radius": 0.398, "rotation": 13.0}),
        g("d3", "spokes", {"count": 11, "width": 0.009, "softness": 0.005,
                           "inner_radius": 0.232, "outer_radius": 0.312, "rotation": 21.0}),
        g("m1", "math", {"mode": "max"}, {"a": "r1", "b": "r2"}),
        g("m2", "math", {"mode": "max"}, {"a": "m1", "b": "r3"}),
        g("m3", "math", {"mode": "max"}, {"a": "m2", "b": "r4"}),
        g("m4", "math", {"mode": "max"}, {"a": "m3", "b": "r5"}),
        g("m5", "math", {"mode": "max"}, {"a": "m4", "b": "d1"}),
        g("m6", "math", {"mode": "max"}, {"a": "m5", "b": "d2"}),
        g("m7", "math", {"mode": "max"}, {"a": "m6", "b": "d3"}),
        g("n", "fbm", {"frequency": 3.4, "octaves": 3, "seed": 29}),
        g("nl", "levels", {"in_low": 0.26, "in_high": 0.76, "out_low": 0.42, "out_high": 1.0}, {"a": "n"}),
        g("lines", "math", {"mode": "multiply"}, {"a": "m7", "b": "nl"}),
        g("hb", "blur", {"radius": 5.0}, {"a": "lines"}),
        g("hbl", "levels", {"in_high": 0.6, "out_high": 0.32}, {"a": "hb"}),
        g("glow", "gradient_radial", {"radius": 0.475, "falloff": "smooth"}),
        g("glowl", "levels", {"out_high": 0.14}, {"a": "glow"}),
        g("k1", "math", {"mode": "max"}, {"a": "lines", "b": "hbl"}),
        g("k2", "math", {"mode": "max"}, {"a": "k1", "b": "glowl"}),
        g("col", "colorize", {"gradient": [
            [0.0, [0.10, 0.004, 0.0, 0.0]],
            [0.18, [0.70, 0.04, 0.005, 0.24]],
            [0.48, [1.0, 0.14, 0.018, 0.6]],
            [0.80, [1.0, 0.38, 0.08, 0.9]],
            [1.0, [1.0, 0.80, 0.48, 1.0]],
        ]}, {"a": "k2"}),
    ], "output": "col"}})

    # burning ground left by one impact: a scorched patch webbed with glowing cooling cracks
    node("tex_scorch", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
        g("w1", "fbm", {"frequency": 2.3, "octaves": 4, "seed": 211}),
        g("w1l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w1"}),
        g("w2", "fbm", {"frequency": 2.3, "octaves": 4, "seed": 307}),
        g("w2l", "levels", {"in_low": 0.36, "in_high": 0.64}, {"a": "w2"}),
        g("wv", "channel_pack", {"channel": "luminance"}, {"r": "w1l", "g": "w2l"}),
        g("s1", "spokes", {"count": 9, "width": 0.014, "softness": 0.008,
                           "inner_radius": 0.03, "outer_radius": 0.46, "rotation": 7.0}),
        g("s1d", "distort", {"amount": 0.11}, {"a": "s1", "by": "wv"}),
        g("s2", "spokes", {"count": 15, "width": 0.007, "softness": 0.005,
                           "inner_radius": 0.10, "outer_radius": 0.48, "rotation": 23.0}),
        g("s2d", "distort", {"amount": 0.14}, {"a": "s2", "by": "wv"}),
        g("s2l", "levels", {"out_high": 0.7}, {"a": "s2d"}),
        g("cr", "cracks", {"density": 5.0, "width": 0.008, "seed": 419}),
        g("crd", "distort", {"amount": 0.04}, {"a": "cr", "by": "wv"}),
        g("crl", "levels", {"out_high": 0.55}, {"a": "crd"}),
        g("u1", "math", {"mode": "max"}, {"a": "s1d", "b": "s2l"}),
        g("u2", "math", {"mode": "max"}, {"a": "u1", "b": "crl"}),
        g("fall", "gradient_radial", {"radius": 0.5, "inner_radius": 0.16, "falloff": "linear"}),
        g("web", "math", {"mode": "multiply"}, {"a": "u2", "b": "fall"}),
        g("bed0", "gradient_radial", {"radius": 0.5, "inner_radius": 0.05, "falloff": "smooth"}),
        g("bn", "fbm", {"frequency": 4.5, "octaves": 4, "seed": 523}),
        g("bed1", "math", {"mode": "multiply"}, {"a": "bed0", "b": "bn"}),
        g("bed", "levels", {"in_low": 0.06, "in_high": 0.5, "out_high": 0.30}, {"a": "bed1"}),
        g("hot", "gradient_radial", {"radius": 0.13, "falloff": "quadratic"}),
        g("k1", "math", {"mode": "max"}, {"a": "web", "b": "bed"}),
        g("k2", "math", {"mode": "max"}, {"a": "k1", "b": "hot"}),
        g("col", "colorize", {"gradient": [
            [0.0, [0.05, 0.004, 0.0, 0.0]],
            [0.22, [0.42, 0.03, 0.004, 0.32]],
            [0.55, [1.0, 0.18, 0.02, 0.72]],
            [0.82, [1.0, 0.48, 0.09, 0.92]],
            [1.0, [1.0, 0.88, 0.52, 1.0]],
        ]}, {"a": "k2"}),
    ], "output": "col"}})


# =========================================================================
# materials, forces, colliders, the shared debris mesh
# =========================================================================
def build_shared() -> None:
    node("mat_fire", "material", {"blend": "additive", "shading": "unlit", "soft_particle": True,
                                  "depth_fade": 0.5, "emissive_intensity": 0.7,
                                  "dissolve": 0.5, "erosion": 0.3,
                                  "temperature_gradient": FIRE_RAMP}, {"noise_texture": "tex_erosion"})
    node("mat_glow", "material", {"blend": "additive", "shading": "unlit", "soft_particle": True,
                                  "depth_fade": 0.4})
    node("mat_smoke", "material", {"blend": "alpha", "shading": "lit",
                                   "base_color": [0.30, 0.25, 0.23, 1.0],
                                   "emissive_color": [1.0, 0.34, 0.08, 1.0],
                                   "soft_particle": True, "depth_fade": 0.5,
                                   "dissolve": 0.45, "erosion": 0.3}, {"noise_texture": "tex_erosion"})
    node("mat_dust", "material", {"blend": "alpha", "shading": "lit",
                                  "base_color": [0.25, 0.19, 0.15, 1.0],
                                  "emissive_color": [1.0, 0.4, 0.1, 1.0],
                                  "soft_particle": True, "depth_fade": 0.45,
                                  "dissolve": 0.42, "erosion": 0.3}, {"noise_texture": "tex_erosion"})
    node("mat_rock", "material", {"blend": "alpha", "shading": "lit",
                                  "base_color": [0.14, 0.10, 0.08, 1.0],
                                  "emissive_color": [1.0, 0.24, 0.035, 1.0],
                                  "emissive_intensity": 0.08})

    node("rock_mesh", "mesh", {"primitive": "rock", "radius": 0.5, "segments": 10,
                               "irregularity": 0.72, "variants": 6, "visible": False})

    node("f_gravity", "force", {"force_type": "gravity", "strength": 9.81})
    node("f_gravity_rock", "force", {"force_type": "gravity", "strength": 24.0})
    node("f_fire_curl", "force", {"force_type": "curl_noise", "strength": 2.4, "frequency": 0.8,
                                  "octaves": 2, "speed": 1.0})
    node("f_fire_lift", "force", {"force_type": "buoyancy", "strength": 2.4, "temperature": 1.0})
    node("f_streak_lift", "force", {"force_type": "buoyancy", "strength": 1.1, "temperature": 1.0})
    node("f_smoke_lift", "force", {"force_type": "buoyancy", "strength": 1.3, "temperature": 1.0})
    node("f_smoke_turb", "force", {"force_type": "turbulence", "strength": 1.2, "frequency": 0.5,
                                   "octaves": 3, "speed": 0.6})
    node("f_spark_turb", "force", {"force_type": "turbulence", "strength": 2.0, "frequency": 0.9,
                                   "octaves": 2, "speed": 1.0})
    node("f_ember_lift", "force", {"force_type": "buoyancy", "strength": 1.5, "temperature": 1.0})
    node("ground", "collider", {"collider_type": "plane", "normal": [0, 1, 0],
                                "bounce": 0.32, "friction": 0.6})


# =========================================================================
# LAYER telegraph (0.0 - 0.4, held down through the barrage)
# =========================================================================
def build_telegraph() -> None:
    node("tele_rings", "decal", {
        "shape": "circle", "position": [0.0, 0.02, 0.0], "size": [r(AREA * 2.1), r(AREA * 2.1)],
        "rotation": track([(0.0, [0.0, 0.0, 0.0]), (DUR, [0.0, 26.0, 0.0])]),
        "color": [1.0, 0.72, 0.5, 1.0], "blend": "additive",
        "emissive": track([(0.0, 0.0), (0.14, 2.6), (0.4, 1.6), (0.9, 1.0), (2.2, 0.8),
                           (2.9, 0.55), (T_AFTER, 0.0)]),
        "opacity": track([(0.0, 0.0), (0.12, 1.0), (0.5, 0.9), (2.4, 0.6), (T_AFTER, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "duration": r(T_AFTER + 0.02),
    }, {"texture": "tex_rune"}, layer="telegraph")

    node("tele_glow", "decal", {
        "shape": "circle", "position": [0.0, 0.012, 0.0], "size": [r(AREA * 2.5), r(AREA * 2.5)],
        "color": [1.0, 0.15, 0.03, 1.0], "blend": "additive",
        "emissive": track([(0.0, 0.0), (0.2, 0.55), (0.6, 0.4), (2.4, 0.35), (T_AFTER, 0.0)]),
        "opacity": track([(0.0, 0.0), (0.24, 0.42), (2.4, 0.3), (T_AFTER, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "duration": r(T_AFTER + 0.02),
    }, {"texture": "tex_glow"}, layer="telegraph")

    # the red glow building in the sky, up the incoming path
    node("ps_skyglow", "particle_system", {
        "max_particles": 6, "lifetime": 3.5, "size": 11.0, "size_variance": 3.0,
        "size_over_life": [[0.0, 0.45], [0.18, 1.0], [0.75, 1.15], [1.0, 1.2]],
        "color": [1.0, 0.13, 0.03, 1.0],
        "color_over_life": [[0.0, [0.8, 0.5, 0.45, 1.0]], [0.16, [1.0, 0.62, 0.5, 1.0]],
                            [0.5, [1.0, 0.5, 0.42, 1.0]], [1.0, [0.7, 0.24, 0.22, 1.0]]],
        "opacity": 0.16, "opacity_over_life": [[0.0, 0.0], [0.11, 1.0], [0.62, 0.85], [1.0, 0.0]],
        "emissive": 0.25, "rotation_variance": 180.0, "angular_velocity_variance": 4.0,
        "blend": "additive",
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer="telegraph")
    node("e_skyglow", "emitter", {
        "shape": "sphere", "radius": 3.4, "position": [-5.2, 11.4, -4.2],
        "rate": 0, "burst_count": 3, "burst_times": [0.0],
        "velocity": 0.0, "start_time": 0.05, "duration": 0.2,
    }, {"particle": "ps_skyglow"}, layer="telegraph")

    # motes drifting off the marked ground; the aftermath re-uses this system for embers
    node("ps_mote", "particle_system", {
        "max_particles": 460, "lifetime": 1.5, "lifetime_variance": 0.6,
        "size": 0.085, "size_variance": 0.04,
        "color": [1.0, 0.44, 0.09, 1.0],
        "color_over_life": [[0.0, [1.0, 0.92, 0.66, 1.0]], [0.45, [1.0, 0.46, 0.1, 1.0]],
                            [1.0, [0.5, 0.05, 0.0, 1.0]]],
        "opacity_over_life": [[0.0, 0.0], [0.13, 1.0], [0.72, 0.85], [1.0, 0.0]],
        "emissive": 4.2, "drag": 0.7, "blend": "additive",
        "render_mode": "stretched_billboard", "velocity_stretch": 0.12,
    }, {"sprite": "tex_spark", "material": "mat_glow",
        "forces": ["f_ember_lift", "f_spark_turb"]}, layer="telegraph")
    node("e_mote", "emitter", {
        "shape": "ring", "radius": r(AREA), "inner_radius": r(AREA - 0.9), "position": [0.0, 0.1, 0.0],
        "rate": track([(0.0, 0.0), (0.16, 80.0), (0.6, 46.0), (2.6, 34.0), (3.05, 0.0)]),
        "velocity": 1.2, "velocity_variance": 0.8, "direction": [0, 1, 0], "spread": 26,
        "duration": 3.1,
    }, {"particle": "ps_mote"}, layer="telegraph")

    node("tele_light", "light", {
        "light_type": "point", "position": [0.0, 1.2, 0.0], "color": [1.0, 0.2, 0.05, 1.0],
        "radius": 10.0,
        "intensity": track([(0.0, 0.0), (0.14, 5.0), (0.45, 3.2), (2.4, 2.4), (T_AFTER, 0.0)]),
        "flicker_amplitude": 0.12, "flicker_frequency": 9.0, "duration": r(T_AFTER + 0.02),
    }, layer="telegraph")


# =========================================================================
# LAYER meteors: the streaks
# =========================================================================
def streak_system(nid: str, size: float, life: float) -> None:
    """A shared flame train.  The taper is size_over_life, so the head is the newest sprite:
    white-hot and full size, cooling and shrinking back down the tail as it is left behind."""
    node(nid, "particle_system", {
        "max_particles": 420, "lifetime": life, "lifetime_variance": r(life * 0.34),
        "size": size, "size_variance": r(size * 0.32),
        "size_over_life": [[0.0, 0.62], [0.09, 1.0], [0.34, 0.88], [0.72, 0.52], [1.0, 0.12]],
        "color": [1.0, 0.94, 0.82, 1.0],
        "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.14, [1.0, 0.96, 0.9, 1.0]],
                            [0.45, [1.0, 0.72, 0.5, 1.0]], [1.0, [0.72, 0.26, 0.12, 1.0]]],
        "opacity": 0.46, "opacity_over_life": [[0.0, 0.0], [0.07, 1.0], [0.42, 0.74], [0.8, 0.3], [1.0, 0.0]],
        "emissive": 0.5, "emissive_over_life": [[0.0, 1.5], [0.18, 1.0], [0.6, 0.6], [1.0, 0.3]],
        "drag": 1.4, "blend": "additive", "soft_particle_distance": 0.5,
        "rotation_variance": 180.0,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.16, "sprite_fps": 22.0,
    }, {"sprite": "tex_fire", "material": "mat_fire",
        "forces": ["f_fire_curl", "f_streak_lift"]}, layer="meteors")


def build_streaks() -> None:
    streak_system("ps_streak", 1.25, 0.30)
    streak_system("ps_streak_big", 2.25, 0.36)

    node("ps_trail_smoke", "particle_system", {
        "max_particles": 260, "lifetime": 1.25, "lifetime_variance": 0.45,
        "size": 1.5, "size_variance": 0.55, "size_over_life": [[0.0, 0.3], [0.4, 1.1], [1.0, 1.8]],
        "color": [0.72, 0.64, 0.6, 1.0],
        "opacity": 0.42, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.6, 0.65], [1.0, 0.0]],
        "emissive": 1.2, "emissive_over_life": [[0.0, 1.5], [0.22, 0.5], [0.6, 0.12], [1.0, 0.0]],
        "rotation_variance": 180.0, "angular_velocity_variance": 26.0,
        "drag": 1.3, "blend": "alpha", "sort": True, "sprite_fps": 8.0,
    }, {"sprite": "tex_puff", "material": "mat_smoke",
        "forces": ["f_smoke_lift", "f_smoke_turb"]}, layer="meteors")

    node("ps_trail_spark", "particle_system", {
        "max_particles": 640, "lifetime": 0.8, "lifetime_variance": 0.35,
        "size": 0.09, "size_variance": 0.04,
        "color": [1.0, 0.58, 0.16, 1.0],
        "color_over_life": [[0.0, [1.0, 0.95, 0.72, 1.0]], [0.4, [1.0, 0.48, 0.1, 1.0]],
                            [1.0, [0.5, 0.05, 0.0, 1.0]]],
        "opacity_over_life": [[0.0, 1.0], [0.7, 0.9], [1.0, 0.0]],
        "emissive": 5.0, "drag": 0.9, "blend": "additive",
        "render_mode": "stretched_billboard", "velocity_stretch": 0.1,
    }, {"sprite": "tex_spark", "material": "mat_glow",
        "forces": ["f_gravity", "f_spark_turb"]}, layer="meteors")

    for i, (lx, lz, size, t_hit, flight, tilt, az) in enumerate(METEORS):
        t_in = r(t_hit - flight)
        off = entry_offset(tilt, az)
        back = back_dir(tilt, az)
        big = i in BIG
        # the hub: everything that belongs to this meteor hangs off it, and `area_radius`
        # multiplies its (y = 0) position, so the whole barrage spreads or tightens.
        node(f"lane_{i}", "mesh", {"primitive": "sphere", "radius": 0.02, "segments": 6,
                                   "visible": False, "position": [r(lx), 0.0, r(lz)]},
             layer="meteors")

        flight_track = track([(t_in, vec(off)), (r(t_hit), [0.0, 0.0, 0.0]),
                              (DUR, [0.0, 0.0, 0.0])])
        speed = math.sqrt(sum(c * c for c in off)) / flight
        # one sprite every ~0.35 of a sprite width, so the train is a continuous body
        pitch = (2.25 if big else 1.25) * 0.36
        rate = r(min(190.0, speed / pitch), 1)
        node(f"e_streak_{i}", "emitter", {
            "shape": "sphere", "radius": r(size * 0.8), "position": flight_track,
            "rate": track([(t_in, 0.0), (r(t_in + 0.05), rate), (r(t_hit - 0.02), rate),
                           (r(t_hit), 0.0)]),
            "velocity": r(2.2 + 2.6 * size), "velocity_variance": 2.0,
            "direction": vec(back), "spread": 17,
            "start_time": t_in, "duration": r(flight),
        }, {"particle": "ps_streak_big" if big else "ps_streak"}, layer="meteors",
             parent=f"lane_{i}")

        node(f"e_tsmoke_{i}", "emitter", {
            "shape": "sphere", "radius": r(size * 1.1),
            "position": flight_track, "rate": r(9.0 + 22.0 * size, 1),
            "velocity": 1.4, "velocity_variance": 0.9, "direction": vec(back), "spread": 40,
            "start_time": r(t_in + 0.03), "duration": r(flight - 0.03),
        }, {"particle": "ps_trail_smoke"}, layer="meteors", parent=f"lane_{i}")

        node(f"e_tspark_{i}", "emitter", {
            "shape": "sphere", "radius": r(size * 0.9), "surface_only": True,
            "position": flight_track, "rate": r(30.0 + 130.0 * size, 1),
            "velocity": r(3.0 + 3.0 * size), "velocity_variance": 2.6,
            "direction": vec(back), "spread": 55,
            "start_time": t_in, "duration": r(flight),
        }, {"particle": "ps_trail_spark"}, layer="meteors", parent=f"lane_{i}")


# =========================================================================
# LAYER impacts: thirteen impacts out of eleven nodes
# =========================================================================
def hits(indices=None):
    picked = range(len(METEORS)) if indices is None else sorted(indices)
    times = [r(METEORS[i][3]) for i in picked]
    points = [[r(METEORS[i][0]), 0.0, r(METEORS[i][1])] for i in picked]
    return times, points


def build_impacts() -> None:
    all_t, all_p = hits()
    big_t, big_p = hits(BIG)
    heavy_t, heavy_p = hits(HEAVY)

    def fireball(nid: str, size: float, life: float) -> None:
        node(nid, "particle_system", {
            "max_particles": 260, "lifetime": life, "lifetime_variance": r(life * 0.35),
            "size": size, "size_variance": r(size * 0.34),
            "size_over_life": [[0.0, 0.32], [0.28, 1.0], [1.0, 1.12]],
            "color": [1.0, 0.92, 0.78, 1.0],
            "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.32, [1.0, 0.94, 0.84, 1.0]],
                                [1.0, [0.86, 0.5, 0.34, 1.0]]],
            "opacity": 0.40, "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.5, 0.72], [0.9, 0.0], [1.0, 0.0]],
            "emissive": 0.42, "drag": 3.2, "blend": "additive", "soft_particle_distance": 0.6,
            "rotation_variance": 180.0,
            "render_mode": "stretched_billboard", "velocity_stretch": 0.08, "sprite_fps": 20.0,
        }, {"sprite": "tex_fire", "material": "mat_fire",
            "forces": ["f_fire_curl", "f_fire_lift"]}, layer="impacts")

    fireball("ps_fireball", 1.55, 0.5)
    fireball("ps_fireball_big", 2.7, 0.62)

    node("e_fireball", "emitter", {
        "shape": "hemisphere", "radius": 0.55, "position": stepped(all_t, all_p),
        "rate": 0, "burst_count": 13, "burst_times": all_t,
        "velocity": 6.5, "velocity_variance": 3.0, "direction": [0, 0, 0], "spread": 14,
    }, {"particle": "ps_fireball"}, layer="impacts")
    node("e_fireball_mid", "emitter", {
        "shape": "ring", "radius": 1.1, "inner_radius": 0.35, "position": stepped(big_t, big_p),
        "rate": 0, "burst_count": 12, "burst_times": big_t,
        "velocity": 2.4, "velocity_variance": 1.4, "direction": [0, 1, 0], "spread": 34,
        "radial_velocity": 6.5,
    }, {"particle": "ps_fireball"}, layer="impacts")
    node("e_fireball_big", "emitter", {
        "shape": "hemisphere", "radius": 1.0, "position": stepped(heavy_t, heavy_p),
        "rate": 0, "burst_count": 24, "burst_times": heavy_t,
        "velocity": 8.5, "velocity_variance": 4.0, "direction": [0, 0, 0], "spread": 16,
    }, {"particle": "ps_fireball_big"}, layer="impacts")

    node("ps_flash", "particle_system", {
        "max_particles": 40, "lifetime": 0.22, "lifetime_variance": 0.05,
        "size": 2.6, "size_variance": 0.7,
        "size_over_life": [[0.0, 0.3], [0.22, 1.0], [1.0, 1.25]],
        "color": [1.0, 0.72, 0.42, 1.0],
        "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.3, [1.0, 0.86, 0.66, 1.0]],
                            [1.0, [1.0, 0.5, 0.26, 1.0]]],
        "opacity": 0.8, "opacity_over_life": [[0.0, 0.85], [0.18, 1.0], [1.0, 0.0]],
        "emissive": 1.3, "rotation_variance": 180.0, "angular_velocity_variance": 40.0,
        "blend": "additive",
    }, {"sprite": "tex_flare", "material": "mat_glow"}, layer="impacts")
    node("e_flash", "emitter", {
        "shape": "point", "position": stepped(all_t, [[p[0], 0.75, p[2]] for p in all_p]),
        "rate": 0, "burst_count": 1, "burst_times": all_t, "velocity": 0.0,
    }, {"particle": "ps_flash"}, layer="impacts")
    node("e_flash_big", "emitter", {
        "shape": "sphere", "radius": 0.5,
        "position": stepped(big_t, [[p[0], 1.1, p[2]] for p in big_p]),
        "rate": 0, "burst_count": 2, "burst_times": big_t, "velocity": 0.0,
    }, {"particle": "ps_flash"}, layer="impacts")

    node("ps_ispark", "particle_system", {
        "max_particles": 900, "lifetime": 0.85, "lifetime_variance": 0.45,
        "size": 0.1, "size_variance": 0.045,
        "color": [1.0, 0.6, 0.18, 1.0],
        "color_over_life": [[0.0, [1.0, 0.96, 0.76, 1.0]], [0.4, [1.0, 0.5, 0.1, 1.0]],
                            [1.0, [0.45, 0.05, 0.0, 1.0]]],
        "opacity_over_life": [[0.0, 1.0], [0.75, 0.95], [1.0, 0.0]],
        "emissive": 5.5, "emissive_over_life": [[0.0, 1.3], [0.5, 0.9], [1.0, 0.3]],
        "drag": 0.6, "bounce": 0.35, "blend": "additive",
        "render_mode": "stretched_billboard", "velocity_stretch": 0.07,
    }, {"sprite": "tex_spark", "material": "mat_glow",
        "forces": ["f_gravity", "f_spark_turb"], "colliders": ["ground"]}, layer="impacts")
    node("e_ispark", "emitter", {
        "shape": "hemisphere", "radius": 0.5, "position": stepped(all_t, all_p),
        "rate": 0, "burst_count": 30, "burst_times": all_t,
        "velocity": 8.5, "velocity_variance": 5.0, "direction": [0, 1, 0], "spread": 72,
    }, {"particle": "ps_ispark"}, layer="impacts")
    node("e_ispark_big", "emitter", {
        "shape": "hemisphere", "radius": 0.8, "position": stepped(big_t, big_p),
        "rate": 0, "burst_count": 40, "burst_times": big_t,
        "velocity": 12.0, "velocity_variance": 7.0, "direction": [0, 1, 0], "spread": 76,
    }, {"particle": "ps_ispark"}, layer="impacts")

    node("ps_dust", "particle_system", {
        "max_particles": 300, "lifetime": 0.95, "lifetime_variance": 0.25,
        "size": 1.25, "size_variance": 0.45, "size_over_life": [[0.0, 0.35], [0.5, 1.3], [1.0, 1.85]],
        "color": [0.8, 0.73, 0.67, 1.0],
        "opacity": 0.42, "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.6, 0.65], [1.0, 0.0]],
        "emissive": 0.5, "emissive_over_life": [[0.0, 1.4], [0.3, 0.45], [1.0, 0.0]],
        "rotation_variance": 180.0, "angular_velocity_variance": 30.0,
        "drag": 2.6, "blend": "alpha", "sort": True, "sprite_fps": 8.0,
    }, {"sprite": "tex_puff", "material": "mat_dust", "forces": ["f_smoke_turb"]}, layer="impacts")
    node("e_dust", "emitter", {
        "shape": "ring", "radius": 0.7, "inner_radius": 0.25,
        "position": stepped(all_t, [[p[0], 0.3, p[2]] for p in all_p]),
        "rate": 0, "burst_count": 7, "burst_times": all_t,
        "velocity": 0.7, "velocity_variance": 0.5, "direction": [0, 1, 0], "spread": 45,
        "radial_velocity": 8.0,
    }, {"particle": "ps_dust"}, layer="impacts")
    node("e_dust_big", "emitter", {
        "shape": "ring", "radius": 1.1, "inner_radius": 0.4,
        "position": stepped(big_t, [[p[0], 0.35, p[2]] for p in big_p]),
        "rate": 0, "burst_count": 9, "burst_times": big_t,
        "velocity": 0.9, "velocity_variance": 0.6, "direction": [0, 1, 0], "spread": 45,
        "radial_velocity": 11.0,
    }, {"particle": "ps_dust"}, layer="impacts")

    node("ps_debris", "particle_system", {
        "max_particles": 150, "lifetime": 1.15, "lifetime_variance": 0.3,
        "size": 0.34, "size_variance": 0.17,
        "size_over_life": [[0.0, 0.5], [0.05, 1.0], [0.85, 1.0], [1.0, 0.0]],
        "angular_velocity": 280.0, "angular_velocity_variance": 200.0,
        "render_mode": "mesh", "blend": "alpha", "drag": 0.8, "mass": 2.0,
        "orientation": "tumble", "mesh_scale": [1.05, 0.76, 1.0],
        "mesh_scale_variance": [0.35, 0.3, 0.35],
        "bounce": 0.34, "friction": 0.55,
    }, {"mesh": "rock_mesh", "material": "mat_rock",
        "forces": ["f_gravity_rock"], "colliders": ["ground"]}, layer="impacts")
    node("e_debris", "emitter", {
        "shape": "hemisphere", "radius": 0.4,
        "position": stepped(all_t, [[p[0], 0.2, p[2]] for p in all_p]),
        "rate": 0, "burst_count": 6, "burst_times": all_t,
        "velocity": 8.0, "velocity_variance": 4.0, "direction": [0, 1, 0], "spread": 46,
        "radial_velocity": 3.5,
    }, {"particle": "ps_debris"}, layer="impacts")
    node("e_debris_big", "emitter", {
        "shape": "hemisphere", "radius": 0.7,
        "position": stepped(big_t, [[p[0], 0.25, p[2]] for p in big_p]),
        "rate": 0, "burst_count": 8, "burst_times": big_t,
        "velocity": 11.0, "velocity_variance": 5.5, "direction": [0, 1, 0], "spread": 52,
        "radial_velocity": 5.0,
    }, {"particle": "ps_debris"}, layer="impacts")


def build_impact_lights() -> None:
    """Two lights cover thirteen flashes: even impacts on one, odd on the other, so one is
    still fading while the next fires.  Each jumps to its next place while it is dark."""
    for name, indices in (("l_flash_a", range(0, len(METEORS), 2)),
                          ("l_flash_b", range(1, len(METEORS), 2))):
        picked = list(indices)
        times, points = hits(picked)
        keys: list[tuple] = [(0.0, 0.0)]
        for i, t in zip(picked, times):
            heavy = i in HEAVY
            peak = 34.0 if heavy else (22.0 if i in BIG else 15.0)
            keys += [(r(t - 0.03), 0.0), (r(t + 0.025), peak), (r(t + 0.13), r(peak * 0.3)),
                     (r(t + 0.3), 0.0)]
        node(name, "light", {
            "light_type": "point",
            "position": stepped(times, [[p[0], 1.3, p[2]] for p in points]),
            "color": [1.0, 0.56, 0.22, 1.0], "radius": 11.0,
            "intensity": track(keys),
            "flicker_amplitude": 0.1, "flicker_frequency": 18.0,
        }, layer="impacts")


# =========================================================================
# LAYER aftermath: burning ground
# =========================================================================
def build_aftermath() -> None:
    for i, (lx, lz, size, t_hit, _flight, _tilt, _az) in enumerate(METEORS):
        span = r(1.3 + 2.7 * size)
        node(f"scorch_{i}", "decal", {
            "shape": "circle", "position": [r(lx), r(0.006 + 0.001 * i), r(lz)],
            "rotation": [0.0, r(rng.uniform(0.0, 360.0), 1), 0.0],
            "size": track([(r(t_hit), [r(span * 0.4), r(span * 0.4)]),
                           (r(t_hit + 0.12), [span, span]),
                           (DUR, [r(span * 1.06), r(span * 1.06)])]),
            "color": track([(r(t_hit), [1.0, 0.95, 0.72, 1.0]),
                            (r(t_hit + 0.3), [1.0, 0.62, 0.22, 1.0]),
                            (r(min(DUR - 0.4, t_hit + 0.9)), [1.0, 0.3, 0.06, 1.0]),
                            (DUR, [0.8, 0.1, 0.015, 1.0])]),
            "blend": "additive",
            "emissive": track([(r(t_hit), 0.0), (r(t_hit + 0.05), 3.4), (r(t_hit + 0.35), 1.9),
                               (3.5, 1.2), (3.8, 0.6), (DUR, 0.0)]),
            "opacity": track([(r(t_hit), 0.0), (r(t_hit + 0.05), 1.0), (3.6, 1.0), (DUR, 0.0)]),
            "fade_in": 0.0, "fade_out": 0.0,
            "start_time": r(t_hit), "duration": r(DUR - t_hit),
        }, {"texture": "tex_scorch"}, layer="aftermath")

    node("ps_groundfire", "particle_system", {
        "max_particles": 320, "lifetime": 0.5, "lifetime_variance": 0.16,
        "size": 1.05, "size_variance": 0.4,
        "size_over_life": [[0.0, 0.45], [0.3, 1.0], [1.0, 0.75]],
        "color": [1.0, 0.9, 0.76, 1.0],
        "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.4, [1.0, 0.9, 0.78, 1.0]],
                            [1.0, [0.88, 0.56, 0.4, 1.0]]],
        "opacity": 0.46, "opacity_over_life": [[0.0, 0.0], [0.16, 1.0], [0.55, 0.7], [0.9, 0.0], [1.0, 0.0]],
        "emissive": 0.3, "drag": 2.0, "blend": "additive", "soft_particle_distance": 0.4,
        "rotation_variance": 180.0,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.2, "sprite_fps": 19.0,
    }, {"sprite": "tex_fire", "material": "mat_fire",
        "forces": ["f_fire_curl", "f_fire_lift"]}, layer="aftermath")

    # a patch of fire lights up at each of the bigger craters, from the moment it is made
    for i in sorted(BIG):
        lx, lz, size, t_hit = METEORS[i][0], METEORS[i][1], METEORS[i][2], METEORS[i][3]
        peak = r(11.0 + 30.0 * size, 1)
        node(f"e_gfire_{i}", "emitter", {
            "shape": "disc", "radius": r(0.35 + 0.9 * size), "position": [r(lx), 0.35, r(lz)],
            "rate": track([(r(t_hit), 0.0), (r(t_hit + 0.12), peak), (3.45, r(peak * 0.85, 1)),
                           (3.75, 0.0)]),
            "velocity": r(1.5 + 1.4 * size), "velocity_variance": 0.8,
            "direction": [0, 1, 0], "spread": 18,
            "start_time": r(t_hit), "duration": r(3.78 - t_hit),
        }, {"particle": "ps_groundfire"}, layer="aftermath")

    node("ps_smoke", "particle_system", {
        "max_particles": 220, "lifetime": 1.6, "lifetime_variance": 0.4,
        "size": 2.1, "size_variance": 0.8, "size_over_life": [[0.0, 0.4], [0.4, 1.15], [1.0, 1.8]],
        "color": [0.7, 0.63, 0.6, 1.0],
        "opacity": 0.42, "opacity_over_life": [[0.0, 0.0], [0.22, 1.0], [0.62, 0.7], [1.0, 0.0]],
        "emissive": 1.1, "emissive_over_life": [[0.0, 1.3], [0.3, 0.45], [0.7, 0.1], [1.0, 0.0]],
        "rotation_variance": 180.0, "angular_velocity_variance": 22.0,
        "drag": 1.5, "blend": "alpha", "sort": True, "sprite_fps": 7.0,
    }, {"sprite": "tex_puff", "material": "mat_smoke",
        "forces": ["f_smoke_lift", "f_smoke_turb"]}, layer="aftermath")
    node("e_smoke", "emitter", {
        "shape": "disc", "radius": r(AREA * 0.92), "position": [0.0, 0.45, 0.0],
        "rate": track([(1.0, 0.0), (1.6, 22.0), (2.4, 42.0), (3.1, 58.0), (3.5, 26.0), (3.9, 0.0)]),
        "velocity": 1.5, "velocity_variance": 0.9, "direction": [0, 1, 0], "spread": 34,
        "start_time": 1.0, "duration": 2.95,
    }, {"particle": "ps_smoke"}, layer="aftermath")

    node("e_ember", "emitter", {
        "shape": "disc", "radius": r(AREA * 0.95), "position": [0.0, 0.25, 0.0],
        "rate": track([(1.1, 0.0), (1.6, 60.0), (2.6, 130.0), (3.2, 150.0), (3.6, 60.0), (3.9, 0.0)]),
        "velocity": 2.1, "velocity_variance": 1.4, "direction": [0, 1, 0], "spread": 48,
        "start_time": 1.1, "duration": 2.85,
    }, {"particle": "ps_mote"}, layer="aftermath")

    node("l_after", "light", {
        "light_type": "point", "position": [0.0, 1.9, 0.8], "color": [1.0, 0.4, 0.1, 1.0],
        "radius": 14.0,
        "intensity": track([(1.2, 0.0), (1.8, 6.0), (2.6, 11.0), (3.2, 14.0), (3.6, 9.0), (DUR, 0.0)]),
        "flicker_amplitude": 0.22, "flicker_frequency": 11.0,
        "start_time": 1.2, "duration": r(DUR - 1.2),
    }, layer="aftermath")

    node("haze", "post_effect", {
        "post_type": "heat_haze", "frequency": 5.0,
        "intensity": track([(0.7, 0.0), (1.2, 0.18), (2.2, 0.3), (3.0, 0.42), (3.4, 0.28),
                            (3.9, 0.0)]),
        "start_time": 0.7, "duration": 3.25,
    }, layer="aftermath")


# =========================================================================
# controls
# =========================================================================
def bind(nid: str, parameter: str, op: str = "multiply") -> dict[str, str]:
    return {"node": nid, "parameter": parameter, "op": op}


def control(cid, label, group, bindings, lo=0.0, hi=3.0, default=1.0, step=0.01, unit="x"):
    return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
            "value": default, "step": step, "unit": unit, "bindings": bindings}


FIRE_PS = ("ps_streak", "ps_streak_big", "ps_fireball", "ps_fireball_big", "ps_groundfire")
COLOUR_PS = FIRE_PS + ("ps_flash", "ps_mote", "ps_ispark", "ps_trail_spark", "ps_skyglow",
                       "ps_trail_smoke", "ps_smoke", "ps_dust")
LIGHTS = ("tele_light", "l_flash_a", "l_flash_b", "l_after")
IMPACT_EMITTERS = ("e_fireball", "e_fireball_mid", "e_fireball_big", "e_flash", "e_flash_big",
                   "e_ispark", "e_ispark_big", "e_dust", "e_dust_big", "e_debris", "e_debris_big")


def build_controls() -> list[dict[str, Any]]:
    lanes = [f"lane_{i}" for i in range(len(METEORS))]
    scorches = [f"scorch_{i}" for i in range(len(METEORS))]
    gfires = [f"e_gfire_{i}" for i in sorted(BIG)]

    area = [bind(n, "position") for n in lanes + scorches + list(IMPACT_EMITTERS)
            + ["l_flash_a", "l_flash_b"]]
    area += [bind(n, "position") for n in gfires]
    area += [bind("tele_rings", "size"), bind("tele_glow", "size"),
             bind("e_mote", "radius"), bind("e_mote", "inner_radius"),
             bind("e_smoke", "radius"), bind("e_ember", "radius")]

    size = [bind("ps_streak", "size"), bind("ps_streak_big", "size"),
            bind("ps_trail_smoke", "size"), bind("ps_debris", "size")]
    size += [bind(f"e_streak_{i}", "radius") for i in range(len(METEORS))]
    size += [bind(f"e_tsmoke_{i}", "radius") for i in range(len(METEORS))]
    size += [bind(f"e_tspark_{i}", "radius") for i in range(len(METEORS))]

    fire = [bind(ps, "color") for ps in FIRE_PS] + [bind(ps, "emissive") for ps in FIRE_PS]
    fire += [bind("mat_fire", "emissive_intensity"), bind("ps_flash", "color"),
             bind("ps_flash", "emissive"), bind("ps_trail_spark", "color"),
             bind("ps_ispark", "color")]
    fire += [bind(l, "intensity") for l in LIGHTS]

    burst = [bind(n, "burst_count") for n in IMPACT_EMITTERS]
    burst += [bind("ps_flash", "size"), bind("ps_fireball", "size"),
              bind("ps_fireball_big", "size"), bind("ps_dust", "size"),
              bind("l_flash_a", "intensity"), bind("l_flash_b", "intensity")]

    after = [bind(n, "emissive") for n in scorches] + [bind(n, "opacity") for n in scorches]
    after += [bind(n, "rate") for n in gfires]
    after += [bind("e_smoke", "rate"), bind("e_ember", "rate"), bind("l_after", "intensity"),
              bind("haze", "intensity")]

    hue = [bind("mat_fire", "temperature_gradient", "hue_shift"),
           bind("mat_smoke", "emissive_color", "hue_shift"),
           bind("mat_dust", "emissive_color", "hue_shift"),
           bind("mat_rock", "emissive_color", "hue_shift")]
    for ps in COLOUR_PS:
        hue += [bind(ps, "color", "hue_shift"), bind(ps, "color_over_life", "hue_shift")]
    hue += [bind(l, "color", "hue_shift") for l in LIGHTS]
    hue += [bind("tele_rings", "color", "hue_shift"), bind("tele_glow", "color", "hue_shift")]
    hue += [bind(n, "color", "hue_shift") for n in scorches]

    return [
        control("area_radius", "Area radius", "Global", area, lo=0.4, hi=1.8),
        control("meteor_size", "Meteor size", "Meteors", size, lo=0.4, hi=2.0),
        control("fire_intensity", "Fire intensity", "Global", fire, hi=2.5),
        control("impact_burst", "Impact burst", "Impacts", burst, hi=2.0),
        control("aftermath", "Burning ground", "Aftermath", after, hi=2.5),
        {"id": "hue", "label": "Hue", "group": "Global", "min": -180.0, "max": 180.0,
         "default": 0.0, "value": 0.0, "step": 1.0, "unit": "deg", "bindings": hue},
        {"id": "global_speed", "label": "Speed", "group": "Global", "min": 0.25, "max": 4.0,
         "default": 1.0, "value": 1.0, "step": 0.05, "unit": "x",
         "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]},
    ]


# =========================================================================
def build() -> dict[str, Any]:
    nodes.clear()
    build_camera()
    build_textures()
    build_shared()
    build_telegraph()
    build_streaks()
    build_impacts()
    build_impact_lights()
    build_aftermath()
    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": DESCRIPTION,
        "duration": DUR,
        "seed": SEED,
        "timeline": {"phases": [
            {"name": "telegraph", "start": T_TELE, "end": T_APPEAR},
            {"name": "meteors_appear", "start": T_APPEAR, "end": T_BARRAGE},
            {"name": "barrage", "start": T_BARRAGE, "end": T_CONT},
            {"name": "continuous_impacts", "start": T_CONT, "end": T_FINAL},
            {"name": "final_strikes", "start": T_FINAL, "end": T_AFTER},
            {"name": "aftermath", "start": T_AFTER, "end": DUR},
        ]},
        "layers": [
            {"id": "telegraph", "name": "Telegraph", "role": "telegraph"},
            {"id": "meteors", "name": "Meteors", "role": "primary"},
            {"id": "impacts", "name": "Impacts", "role": "interaction"},
            {"id": "aftermath", "name": "Burning ground", "role": "aftermath"},
        ],
        "nodes": nodes,
        "controls": build_controls(),
        "metadata": {
            "prompt": "lets create the meteor shower effect",
            "generator": f"tools/generators/{SLUG}.py",
            "analysis": (
                "telegraph: concentric ring decal on the target disc, a soft red ground glow, rising "
                "motes and a deep red glow building high up the incoming path | meteors_appear: the "
                "first streaks enter at 12.6 m on parallel 62-69 degree diagonals | barrage and "
                "continuous_impacts: thirteen meteors of 0.29-0.70 m land 0.13-0.27 s apart at "
                "scattered points, each a fire_sim flame train with smoke and sparks, each impact a "
                "fireball, a many-rayed flash, sparks, a dust ring, tumbling rock debris and a light "
                "flash | final_strikes: the last two are the largest | aftermath: scorch decals cool "
                "from yellow to deep red, ground fires burn at the bigger craters, a low smoke layer "
                "and drifting embers fade out by 4.0"
            ),
            "render_settings": {
                "ground_albedo": 0.1,
                "background": [0.0, 0.0, 0.0, 1.0],
                "bloom_intensity": 0.2,
                "bloom_radius": 0.04,
                "exposure": 0.95,
                "grid": False,
            },
            "layout": {"area_radius": AREA, "entry_height": ENTRY_H, "meteor_count": len(METEORS)},
            "tags": ["fire", "meteor", "shower", "aoe", "barrage", "impact", "debris", "aftermath"],
        },
    }


def render(effect: dict[str, Any]) -> str:
    return json.dumps(effect, indent=1) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help=f"directory to write {SLUG}.json into")
    ap.add_argument("--check", help="compare this file byte for byte with what the generator produces")
    args = ap.parse_args()
    text = render(build())
    if args.check:
        same = Path(args.check).read_text(encoding="utf-8") == text
        print(f"{args.check}: {'identical' if same else 'DIFFERS from the generator output'}")
        return 0 if same else 1
    if not args.out:
        ap.error("pass --out DIR or --check FILE")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{SLUG}.json"
    path.write_text(text, encoding="utf-8")
    print(f"{path}  ({len(nodes)} nodes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
