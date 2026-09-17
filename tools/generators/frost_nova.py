#!/usr/bin/env python3
"""Generate examples/effects/frost_nova.json from a table of spikes and one wave front.

    python tools/generators/frost_nova.py --out examples/effects
    python tools/generators/frost_nova.py --check examples/effects/frost_nova.json

Frost Nova is an instant radial attack: a rune ring ignites under the caster, frost
is pulled inward, the centre bursts into a crown of ice spikes and a wave of ice
races outward along the ground to a 4.5 m rim, leaving a cracked frozen disc that
lingers and then sinks and shatters. There is no caster and no scenery: the origin
is where the caster stands and nothing is ever spawned inside 0.5 m of it.

Everything that travels is driven by ONE table, FRONT (time -> radius of the wave
front). A spike erupts when the front reaches its foot, a crack decal lights when
the front has crossed it, and the emitters that kick up mist, chips and glitter are
rings whose `scale` is keyframed along the same front, so the whole read - "it
travels from the centre to the rim" - stays in step when FRONT is retimed.

  spikes    mesh particles, `orientation: velocity`: a spike points where its emitter
            throws it, so the lean is authored, not random. The crystal's centre sits
            ON the ground (half of the mesh is buried under the opaque ground plane),
            so growing `size` reads as the spike stabbing out of the ground along its
            own axis and shrinking it sinks it back. The hero spikes (CROWN, RIM) are
            one point emitter each - azimuth, foot radius and lean per row - and the
            fillers between them are ring emitters (FILL). No two are alike: four
            size classes, four seeded crystal meshes, per-axis
            scale variance and a lean cone on every emitter.
  ground    one warped Voronoi seam texture drawn three times at nested sizes (each
            lights as the front crosses it: finer fractures near the centre), a decal of
            meandering radial fractures and a frost disc that SCALE with the front (a
            radial pattern scaled about its centre just extends), a torn frost shock ring.
  burst     a short flash with a small hot core and a swarm of ~60 soft streaks of
            uneven reach: a many-pointed burst, never a four-armed star.
  shards    elongated crystal shards and thin flakes tumbling out of the burst and off
            the rim, cheap sprite chips thrown up along the front, twinkling glitter.
  mist      lit, eroded flipbook puffs: creeping inward while the cast gathers, rolling
            outward with the front, drifting while the ice lingers.
  fade      the spikes sink, a last burst of flakes and glitter, the decals die.

Frames are 60 per second; bursts and keys sit on frames.

House style: no crosses or four-armed motifs (the rune is rings, a tick band and nine
pips; rays come in 11 + 7 + 17, warped off their spacing), no ruler-straight thin lines
(every line texture is warped and broken by noise; the cast glow is a soft wide column
of sprites, not a beam), hot cores stay small and short.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SLUG = "frost_nova"
NAME = "Frost Nova"
DESCRIPTION = ("A rune ring ignites, frost is drawn inward, then a crown of ice spikes bursts from the centre and a wave "
               "of spikes, glowing fractures and snow mist races out to a 4.5 m rim, lingers, and sinks into glitter.")
DURATION = 2.2
SEED = 27183
FPS = 60.0
RADIUS = 4.5

# The concept sheet's key frames.
PHASES = [
    ("cast_start", 0.0, 0.2),
    ("energy_build", 0.2, 0.4),
    ("explosion", 0.4, 0.6),
    ("shockwave", 0.6, 0.9),
    ("full_impact", 0.9, 1.2),
    ("linger", 1.2, 1.8),
    ("fade", 1.8, 2.2),
]

# ---------------------------------------------------------------------------
# the tables: the wave front and the spikes
# ---------------------------------------------------------------------------

#: (time s, radius m) of the ice wave: out of the burst at 0.43 s, at the rim by 0.9 s.
FRONT = [(0.43, 0.0), (0.47, 0.4), (0.6, 1.8), (0.7, 2.9), (0.8, 3.9), (0.9, 4.65)]

BURST_T = 0.43          # the explosion
SINK_T = 1.82           # spikes start to sink
DEAD_T = 2.13           # ... and the last one is gone (+ its lifetime variance, 0.04 s)


@dataclass(frozen=True)
class SpikeClass:
    """One mesh particle system. A class is a size AND a time slot: its over-life curves are in
    normalised life, so everything in it has to erupt within about 0.1 s to sink together."""
    id: str
    length: float       # visible length above the ground (m); the mesh is twice that, half of it buried
    variance: float
    girth: float        # visible width at the ground (m)
    mesh: str           # the glass material costs three draws per mesh variant in the viewer:
    tint: tuple[float, float, float]   # heroes get two variants, fillers one (scale variance does the rest)


CLASSES = {c.id: c for c in [
    SpikeClass("crown", 2.15, 0.5, 0.58, "mesh_spike_a", (0.62, 0.84, 1.0)),
    SpikeClass("rim", 2.55, 0.4, 0.72, "mesh_spike_b", (0.42, 0.7, 1.0)),
    SpikeClass("fill_in", 0.9, 0.4, 0.34, "mesh_spike_c", (0.5, 0.76, 1.0)),
    SpikeClass("fill_out", 1.25, 0.55, 0.42, "mesh_spike_d", (0.45, 0.72, 1.0)),
]}

#: Hero spikes, one point emitter each: (azimuth deg, foot radius m, lean deg from vertical, yaw off radial deg).
#: Azimuth 90 faces the camera. Splayed towards it, the steep tall ones at the back, so the default
#: view never looks straight down a spike.
CROWN = [
    (14, 0.64, 38, 6), (52, 0.8, 50, -9), (99, 0.7, 56, 12), (139, 0.84, 47, 8), (172, 0.66, 35, -5),
    (211, 0.76, 28, 7), (249, 0.6, 20, -8), (287, 0.8, 31, 3), (326, 0.72, 44, -6),
]
#: The rim: (azimuth, foot radius, lean, yaw, class). The tips reach about 4.9 m.
RIM = [
    (3, 3.2, 46, 4, "rim"), (29, 3.62, 57, -7, "fill_out"), (53, 3.12, 43, 6, "rim"),
    (80, 3.55, 54, -4, "fill_out"), (104, 3.3, 48, -8, "rim"), (127, 3.18, 41, 5, "rim"),
    (155, 3.66, 58, 9, "fill_out"), (181, 3.24, 47, -5, "rim"), (207, 3.58, 53, 6, "fill_out"),
    (231, 3.1, 42, -6, "rim"), (258, 3.64, 56, 8, "fill_out"), (284, 3.26, 49, -4, "rim"),
    (309, 3.5, 52, 7, "fill_out"), (336, 3.16, 44, -9, "rim"),
]


@dataclass(frozen=True)
class Fill:
    """A ring of filler spikes between the heroes: random azimuths, a lean cone around `lean`."""
    id: str
    inner: float
    outer: float
    count: int
    cls: str
    lean: float
    cone: float
    seed: int


FILL = [
    Fill("fill_a", 1.25, 1.75, 8, "fill_in", 31, 13, 11),
    Fill("fill_b", 1.95, 2.6, 10, "fill_in", 37, 14, 23),
    Fill("fill_c", 2.75, 3.3, 9, "fill_out", 43, 14, 37),
    Fill("fill_d", 3.85, 4.35, 14, "fill_out", 57, 12, 41),
]

# palette (linear): saturated ice blue, cyan-white only in the cores, deep blue in the shadows
WHITE = [0.84, 0.95, 1.0, 1.0]
CYAN = [0.42, 0.8, 1.0, 1.0]
ICE = [0.2, 0.5, 1.0, 1.0]
BLUE = [0.1, 0.3, 1.0, 1.0]
DEEP = [0.03, 0.1, 0.55, 1.0]

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def r4(v: float) -> float:
    return round(float(v), 4)


def fr(t: float) -> float:
    """Snap a time to the 60 fps frame grid."""
    return r4(round(t * FPS) / FPS)


def front_radius(t: float) -> float:
    if t <= FRONT[0][0]:
        return FRONT[0][1]
    for (t0, r0), (t1, r1) in zip(FRONT, FRONT[1:]):
        if t <= t1:
            return r0 + (r1 - r0) * (t - t0) / (t1 - t0)
    return FRONT[-1][1]


def front_time(radius: float) -> float:
    for (t0, r0), (t1, r1) in zip(FRONT, FRONT[1:]):
        if radius <= r1:
            return t0 + (t1 - t0) * (radius - r0) / (r1 - r0)
    return FRONT[-1][0]


def tint(rgba: list[float], k: float, alpha: float | None = None) -> list[float]:
    return [r4(rgba[0] * k), r4(rgba[1] * k), r4(rgba[2] * k), rgba[3] if alpha is None else alpha]


def track(keys: list[tuple], interp: str | None = None) -> dict[str, Any]:
    """keys = [(seconds, value)] -> {"value": first, "track": [...]}, times snapped to frames."""
    out = []
    for t, value in keys:
        if isinstance(value, (int, float)):
            value = r4(value)
        elif isinstance(value, (list, tuple)):
            value = [r4(x) for x in value]
        entry: dict[str, Any] = {"time": fr(t), "value": value}
        if interp:
            entry["interp"] = interp
        out.append(entry)
    return {"value": out[0]["value"], "track": out}


def front_track(uv_radius: float, lead: float = 0.0, floor: float = 0.2, scale: float = 1.0) -> dict[str, Any]:
    """A decal `size` (or an emitter `scale`) that follows the wave front: the feature drawn at
    `uv_radius` of the quad (1.0 for a unit ring emitter) sits `lead` metres ahead of the front."""
    keys = []
    for t, radius in FRONT:
        s = max(floor, (radius + lead) / uv_radius) * scale
        keys.append((t, [s, s, s] if uv_radius == 1.0 else [s, s]))
    return track(keys)


nodes: list[dict[str, Any]] = []


def node(nid: str, ntype: str, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None,
         layer: str | None = None, seed: int | None = None) -> str:
    n: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        n["layer"] = layer
    if seed is not None:
        n["seed"] = seed
    if params:
        n["parameters"] = params
    if inputs:
        n["inputs"] = inputs
    nodes.append(n)
    return nid


def g(nid: str, op: str, params: dict[str, Any] | None = None, inputs: dict[str, str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": nid, "op": op}
    if params:
        out["params"] = params
    if inputs:
        out["inputs"] = inputs
    return out


def warp_field(prefix: str, frequency: float, seed: int, animate: float = 0.0) -> list[dict[str, Any]]:
    """A packed two-channel fbm so `distort` displaces in x and y independently."""
    out = []
    for i, channel in enumerate(("a", "b")):
        params: dict[str, Any] = {"frequency": frequency, "octaves": 4, "seed": seed + 31 * i}
        if animate:
            params["animate"] = animate
        out.append(g(f"{prefix}{channel}", "fbm", params))
        out.append(g(f"{prefix}{channel}l", "levels", {"in_low": 0.34, "in_high": 0.66}, {"a": f"{prefix}{channel}"}))
    out.append(g(prefix, "channel_pack", {"channel": "luminance"}, {"r": f"{prefix}al", "g": f"{prefix}bl"}))
    return out


def fold(prefix: str, mode: str, ids: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Chain a binary math op over several inputs; returns (ops, id of the result)."""
    ops, acc = [], ids[0]
    for i, other in enumerate(ids[1:]):
        nid = f"{prefix}{i}"
        ops.append(g(nid, "math", {"mode": mode}, {"a": acc, "b": other}))
        acc = nid
    return ops, acc


# ---------------------------------------------------------------------------
# textures (all grayscale: the node that draws them brings the colour, so one
# hue control recolours the whole effect). 16 bytes a pixel: sizes are chosen so
# the set stays under 12 MB.
# ---------------------------------------------------------------------------


def build_textures() -> None:
    node("tex_glow", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("l", "levels", {"gamma": 0.7}, {"a": "r"}),
    ], "output": "l"}})

    node("tex_spark", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
    ], "output": "r"}})

    # A thin flake seen face on: a two-tone diamond, so a spinning billboard glints like a facet.
    node("tex_chip", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
        g("d", "shape", {"shape": "diamond", "radius": 0.47, "aspect": 0.42, "softness": 0.02}),
        g("split", "gradient_linear", {"angle": 0.0}),
        g("tone", "levels", {"in_low": 0.47, "in_high": 0.53, "out_low": 0.3, "out_high": 1.0}, {"a": "split"}),
        g("tip", "gradient_linear", {"angle": 90.0, "start": 1.0, "end": 0.55}),
        g("m", "math", {"mode": "multiply"}, {"a": "d", "b": "tone"}),
        g("k", "math", {"mode": "multiply"}, {"a": "m", "b": "tip"}),
    ], "output": "k"}})

    # Snow mist: a breathing fbm puff, eroded further by the material.
    node("tex_puff", "texture", {"width": 96, "height": 96, "frames": 8, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("n", "fbm", {"frequency": 3.6, "octaves": 4, "seed": 11, "animate": 0.8}),
        g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "n"}),
        g("l", "levels", {"in_low": 0.06, "in_high": 0.58}, {"a": "m"}),
    ], "output": "l"}})

    # Cast rune: three concentric rings, a band of 36 ticks and nine pips. No star, no cross.
    pips = []
    for i in range(9):
        a = math.radians(i * 40.0 + 20.0)
        pips.append(g(f"p{i}", "shape", {"shape": "diamond", "radius": 0.021, "aspect": 0.6, "softness": 0.004,
                                          "center": [r4(0.5 + 0.345 * math.cos(a)), r4(0.5 + 0.345 * math.sin(a))],
                                          "rotation": r4(90.0 - math.degrees(a))}))
    pip_ops, pip_out = fold("pm", "max", [p["id"] for p in pips])
    node("tex_rune", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
        g("r1", "ring", {"radius": 0.445, "thickness": 0.013, "softness": 0.006}),
        g("r2", "ring", {"radius": 0.4, "thickness": 0.006, "softness": 0.004}),
        g("r3", "ring", {"radius": 0.29, "thickness": 0.007, "softness": 0.004}),
        g("halo", "ring", {"radius": 0.445, "thickness": 0.05, "softness": 0.05}),
        g("halol", "levels", {"out_high": 0.3}, {"a": "halo"}),
        g("ticks", "spokes", {"count": 36, "width": 0.005, "softness": 0.003, "inner_radius": 0.408,
                              "outer_radius": 0.436}),
        g("dash", "spokes", {"count": 18, "width": 0.012, "softness": 0.004, "inner_radius": 0.3,
                             "outer_radius": 0.318, "rotation": 10.0}),
        *pips, *pip_ops,
        g("a", "math", {"mode": "max"}, {"a": "r1", "b": "r2"}),
        g("b", "math", {"mode": "max"}, {"a": "a", "b": "r3"}),
        g("c", "math", {"mode": "max"}, {"a": "b", "b": "halol"}),
        g("d", "math", {"mode": "max"}, {"a": "c", "b": "ticks"}),
        g("e", "math", {"mode": "max"}, {"a": "d", "b": "dash"}),
        g("f", "math", {"mode": "max"}, {"a": "e", "b": pip_out}),
        # the energy is uneven round the ring, so it shimmers as the decal turns
        g("n", "fbm", {"frequency": 2.4, "octaves": 3, "seed": 5}),
        g("nl", "levels", {"in_low": 0.3, "in_high": 0.7, "out_low": 0.55}, {"a": "n"}),
        g("k", "math", {"mode": "multiply"}, {"a": "f", "b": "nl"}),
    ], "output": "k"}})

    # Fractured ice: bright Voronoi seams inside a soft glow, finer hairlines between them, all
    # warped so no seam is ruler-straight, brightness wandering along the seams. Dimmer in the
    # middle because the three nested copies overlap there.
    node("tex_web", "texture", {"width": 512, "height": 512, "graph": {"nodes": [
        *warp_field("w", 3.2, 301),
        g("core", "cracks", {"density": 6.5, "width": 0.0055, "seed": 17}),
        g("glow", "voronoi", {"cells": 6.5, "mode": "edges", "seed": 17}),
        g("glowl", "levels", {"out_high": 0.26}, {"a": "glow"}),
        g("fine", "cracks", {"density": 17.0, "width": 0.0035, "seed": 29}),
        g("finel", "levels", {"out_high": 0.42}, {"a": "fine"}),
        g("u1", "math", {"mode": "max"}, {"a": "core", "b": "glowl"}),
        g("u2", "math", {"mode": "max"}, {"a": "u1", "b": "finel"}),
        g("wd", "distort", {"amount": 0.03}, {"a": "u2", "by": "w"}),
        g("n", "fbm", {"frequency": 3.0, "octaves": 3, "seed": 43}),
        g("nl", "levels", {"in_low": 0.3, "in_high": 0.68, "out_low": 0.3}, {"a": "n"}),
        g("e", "math", {"mode": "multiply"}, {"a": "wd", "b": "nl"}),
        g("rim", "gradient_radial", {"radius": 0.5, "inner_radius": 0.33, "falloff": "smooth"}),
        g("fill", "gradient_radial", {"radius": 0.26, "falloff": "smooth"}),
        g("midi", "invert", None, {"a": "fill"}),
        g("midl", "levels", {"out_low": 0.4}, {"a": "midi"}),
        g("mask", "math", {"mode": "multiply"}, {"a": "rim", "b": "midl"}),
        g("k", "math", {"mode": "multiply"}, {"a": "e", "b": "mask"}),
    ], "output": "k"}})

    # Radial fractures: 11 + 7 + 17 rays warped off their even spacing and broken along their
    # length. The decal scales with the wave front, which for a radial pattern reads as growth.
    node("tex_rays", "texture", {"width": 384, "height": 384, "graph": {"nodes": [
        *warp_field("w", 2.1, 517),
        g("s1", "spokes", {"count": 11, "width": 0.005, "softness": 0.004, "inner_radius": 0.03, "outer_radius": 0.5}),
        g("s1d", "distort", {"amount": 0.032}, {"a": "s1", "by": "w"}),
        g("s2", "spokes", {"count": 7, "width": 0.0075, "softness": 0.006, "inner_radius": 0.02, "outer_radius": 0.47,
                           "rotation": 13.0}),
        g("s2d", "distort", {"amount": 0.045}, {"a": "s2", "by": "w"}),
        g("s3", "spokes", {"count": 17, "width": 0.003, "softness": 0.0025, "inner_radius": 0.1, "outer_radius": 0.5,
                           "rotation": 7.0}),
        g("s3d", "distort", {"amount": 0.06}, {"a": "s3", "by": "w"}),
        g("s3l", "levels", {"out_high": 0.6}, {"a": "s3d"}),
        g("u1", "math", {"mode": "max"}, {"a": "s1d", "b": "s2d"}),
        g("u2", "math", {"mode": "max"}, {"a": "u1", "b": "s3l"}),
        g("n", "fbm", {"frequency": 5.0, "octaves": 3, "seed": 71}),
        g("nl", "levels", {"in_low": 0.36, "in_high": 0.62, "out_low": 0.08}, {"a": "n"}),
        g("b", "math", {"mode": "multiply"}, {"a": "u2", "b": "nl"}),
        g("fall", "gradient_radial", {"radius": 0.5, "inner_radius": 0.12, "falloff": "smooth"}),
        g("hub", "gradient_radial", {"radius": 0.13, "falloff": "smooth"}),
        g("hubi", "invert", None, {"a": "hub"}),
        g("hubl", "levels", {"out_low": 0.25}, {"a": "hubi"}),
        g("bf", "math", {"mode": "multiply"}, {"a": "b", "b": "fall"}),
        g("k", "math", {"mode": "multiply"}, {"a": "bf", "b": "hubl"}),
    ], "output": "k"}})

    # Frost shock ring: a bright torn front with a glow trailing behind it (towards the centre).
    node("tex_shock", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
        *warp_field("w", 3.0, 131),
        g("front", "ring", {"radius": 0.43, "thickness": 0.016, "softness": 0.02}),
        g("in", "gradient_radial", {"radius": 0.43, "falloff": "linear"}),
        g("ini", "invert", None, {"a": "in"}),
        g("disc", "shape", {"shape": "circle", "radius": 0.43, "softness": 0.012}),
        g("wake", "math", {"mode": "multiply"}, {"a": "ini", "b": "disc"}),
        g("wakel", "levels", {"in_low": 0.7, "in_high": 1.0, "out_high": 0.4, "gamma": 0.7}, {"a": "wake"}),
        g("u", "math", {"mode": "max"}, {"a": "front", "b": "wakel"}),
        g("n", "fbm", {"frequency": 4.0, "octaves": 4, "seed": 9}),
        g("nl", "levels", {"in_low": 0.36, "in_high": 0.64, "out_low": 0.06}, {"a": "n"}),
        g("t", "math", {"mode": "multiply"}, {"a": "u", "b": "nl"}),
        g("k", "distort", {"amount": 0.03}, {"a": "t", "by": "w"}),
    ], "output": "k"}})

    # Rime on the ground: feathery fbm against crystalline Worley cells, with a ragged edge.
    node("tex_frost", "texture", {"width": 192, "height": 192, "graph": {"nodes": [
        g("n", "fbm", {"frequency": 5.5, "octaves": 5, "seed": 13}),
        g("c", "worley", {"frequency": 11.0, "seed": 19}),
        g("ci", "invert", None, {"a": "c"}),
        g("cl", "levels", {"in_low": 0.2, "in_high": 0.95, "out_low": 0.45}, {"a": "ci"}),
        g("m", "math", {"mode": "multiply"}, {"a": "n", "b": "cl"}),
        g("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.3, "falloff": "smooth"}),
        g("e", "math", {"mode": "multiply"}, {"a": "m", "b": "r"}),
        g("k", "levels", {"in_low": 0.1, "in_high": 0.5}, {"a": "e"}),
    ], "output": "k"}})


# ---------------------------------------------------------------------------
# shared nodes: materials, meshes, forces, the ground
# ---------------------------------------------------------------------------


def build_shared() -> None:
    # Clean translucent ice. A light base colour selects the viewer's glass path (transmission and
    # tight specular highlights); the particle colour tints the body a saturated blue per class, the
    # emissive colour is the cyan-white the fresnel rim lights the grazing facets with, and an
    # opacity under 1 lets the glowing fractures show through. Single sided: the crystals are closed
    # and convex, and a double-sided glass mesh is drawn twice.
    node("mat_ice", "material", {"blend": "alpha", "shading": "lit", "base_color": [0.5, 0.74, 1.0, 1.0],
                                 "opacity": 0.88, "emissive_color": [0.3, 0.68, 1.0, 1.0], "emissive_intensity": 0.1,
                                 "fresnel_power": 4.2, "double_sided": False})
    node("mat_mist", "material", {"blend": "alpha", "shading": "lit", "base_color": [0.66, 0.82, 1.0, 1.0],
                                  "emissive_color": [0.42, 0.66, 1.0, 1.0], "emissive_intensity": 0.95,
                                  "soft_particle": True, "depth_fade": 0.45, "dissolve": 0.24, "erosion": 0.42})
    node("mat_glow", "material", {"blend": "additive", "shading": "unlit", "emissive_color": [0.5, 0.8, 1.0, 1.0],
                                  "emissive_intensity": 0.6, "soft_particle": True, "depth_fade": 0.2})

    for nid, facets, irregularity, variants, seed in (("mesh_spike_a", 6, 0.5, 2, 101), ("mesh_spike_b", 5, 0.62, 2, 202),
                                                      ("mesh_spike_c", 7, 0.7, 1, 303), ("mesh_spike_d", 6, 0.66, 1, 505)):
        node(nid, "mesh", {"primitive": "crystal", "radius": 0.3, "height": 1.0, "segments": facets,
                           "irregularity": irregularity, "variants": variants, "visible": False}, seed=seed)
    node("mesh_shard", "mesh", {"primitive": "crystal", "radius": 0.3, "height": 1.0, "segments": 6,
                                "irregularity": 0.45, "variants": 2, "visible": False}, seed=404)

    node("gravity", "force", {"force_type": "gravity", "strength": 9.81})
    node("chip_gravity", "force", {"force_type": "gravity", "strength": 5.5})
    node("drift", "force", {"force_type": "turbulence", "strength": 0.8, "frequency": 0.8, "octaves": 2})
    node("gather_pull", "force", {"force_type": "attractor", "strength": 22.0, "position": [0.0, 0.35, 0.0],
                                  "radius": 5.0, "falloff": "smooth", "start_time": 0.0, "duration": 0.45})
    node("ground", "collider", {"collider_type": "plane", "normal": [0, 1, 0], "bounce": 0.22, "friction": 0.55})


# ---------------------------------------------------------------------------
# cast_start / energy_build: rune ring, soft glow column, inward motes, creeping mist
# ---------------------------------------------------------------------------


def build_cast() -> None:
    layer = "telegraph"
    # The ring ignites, brightens through the build, flares with the burst and is gone.
    node("rune", "decal", {
        "shape": "circle", "size": track([(0.0, [2.5, 2.5]), (0.12, [2.95, 2.95]), (0.4, [2.95, 2.95]),
                                          (0.52, [3.5, 3.5])]),
        "rotation": track([(0.0, [0.0, 0.0, 0.0]), (0.55, [0.0, 38.0, 0.0])]),
        "color": tint(ICE, 1.0), "blend": "additive",
        "emissive": track([(0.0, 0.0), (0.1, 1.6), (0.2, 1.3), (0.4, 2.8), (0.45, 2.4), (0.52, 0.0)]),
        "opacity": track([(0.0, 0.0), (0.06, 1.0), (0.45, 1.0), (0.52, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": 0.0, "duration": 0.54,
    }, {"texture": "tex_rune"}, layer)

    # A soft cold glow on the ground under the caster: wide and dim, a falloff and never a plate.
    node("cast_glow", "decal", {
        "shape": "circle", "size": track([(0.0, [3.0, 3.0]), (0.4, [5.6, 5.6]), (0.5, [7.6, 7.6]), (1.2, [8.4, 8.4])]),
        "color": tint(BLUE, 1.0), "blend": "additive", "position": [0.0, 0.01, 0.0],
        "emissive": track([(0.0, 0.0), (0.2, 0.1), (0.4, 0.35), (0.46, 1.0), (0.62, 0.45), (1.2, 0.3), (1.8, 0.2),
                           (2.15, 0.0)]),
        "opacity": track([(0.0, 0.0), (0.15, 0.6), (0.46, 1.0), (1.8, 0.6), (2.15, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0,
    }, {"texture": "tex_glow"}, layer)

    # The rising cold glow, in place of the sheet's thin beam (house style: no ruler-straight light
    # rods): a dozen soft sprites stretched along their climb, each a 3 m ellipse of light with a
    # falloff on every side, overlapping into one wide shaft that tapers upward and never holds still.
    node("column_ps", "particle_system", {
        "max_particles": 40, "lifetime": 0.5, "lifetime_variance": 0.1, "size": 0.85, "size_variance": 0.25,
        "size_over_life": [[0.0, 1.0], [1.0, 0.6]],
        "color": tint(ICE, 1.0), "color_over_life": [[0.0, tint(CYAN, 1.0)], [0.45, tint(ICE, 1.0)], [1.0, tint(DEEP, 1.0)]],
        "opacity": 0.2, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.55, 0.6], [1.0, 0.0]],
        "emissive": 0.7, "drag": 0.6, "blend": "additive",
        "render_mode": "stretched_billboard", "velocity_stretch": 1.0,
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer)
    node("column", "emitter", {
        "shape": "disc", "radius": 0.22, "position": [0.0, 0.5, 0.0],
        "rate": track([(0.0, 0.0), (0.04, 26.0), (0.2, 30.0), (0.36, 40.0), (0.42, 0.0)]),
        "velocity": 2.6, "velocity_variance": 0.9, "direction": [0, 1, 0], "spread": 4.0,
        "start_time": 0.0, "duration": 0.44,
    }, {"particle": "column_ps"}, layer)

    # Motes and frost pulled inward: streaks thrown at the centre and accelerated into it.
    node("mote_ps", "particle_system", {
        "max_particles": 260, "lifetime": 0.4, "lifetime_variance": 0.08, "size": 0.06, "size_variance": 0.025,
        "color": tint(WHITE, 1.3), "color_over_life": [[0.0, tint(ICE, 1.0)], [0.6, tint(CYAN, 1.0)], [1.0, tint(WHITE, 1.0)]],
        "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.85, 1.0], [1.0, 0.0]],
        "emissive": 3.0, "drag": 0.2, "blend": "additive",
        "render_mode": "stretched_billboard", "velocity_stretch": 0.5,
    }, {"sprite": "tex_spark", "forces": ["gather_pull"]}, layer)
    node("motes", "emitter", {
        "shape": "ring", "radius": 3.6, "inner_radius": 2.0, "position": [0.0, 0.5, 0.0], "scale": [1.0, 1.0, 1.0],
        "rate": track([(0.0, 0.0), (0.04, 200.0), (0.2, 420.0), (0.33, 520.0), (0.36, 0.0)]),
        "velocity": 0.6, "velocity_variance": 0.4, "direction": [0, 1, 0], "spread": 70.0, "radial_velocity": -4.2,
        "start_time": 0.0, "duration": 0.37,
    }, {"particle": "mote_ps"}, layer)

    # Cold mist creeping over the rune and drawn towards the centre.
    node("creep_ps", "particle_system", {
        "max_particles": 60, "lifetime": 0.7, "lifetime_variance": 0.15, "size": 0.95, "size_variance": 0.3,
        "size_over_life": [[0.0, 0.6], [1.0, 1.35]], "rotation_variance": 180.0,
        "angular_velocity_variance": 25.0,
        "color": [0.85, 0.93, 1.0, 1.0], "opacity": 0.34, "opacity_over_life": [[0.0, 0.0], [0.35, 1.0], [1.0, 0.0]],
        "drag": 1.6, "blend": "alpha", "sort": True, "sprite_fps": 9.0,
    }, {"sprite": "tex_puff", "material": "mat_mist", "forces": ["gather_pull"]}, "mist")
    node("creep", "emitter", {
        "shape": "ring", "radius": 2.3, "inner_radius": 0.9, "position": [0.0, 0.22, 0.0], "scale": [1.0, 1.0, 1.0],
        "rate": track([(0.0, 40.0), (0.3, 46.0), (0.4, 0.0)]),
        "velocity": 0.2, "velocity_variance": 0.15, "direction": [0, 1, 0], "spread": 50.0,
        "start_time": 0.0, "duration": 0.42,
    }, {"particle": "creep_ps"}, "mist")


# ---------------------------------------------------------------------------
# explosion: flash, hot core, a many-pointed burst of soft streaks
# ---------------------------------------------------------------------------


def single(nid: str, layer: str, when: float, life: float, size: float, params: dict[str, Any],
           position: list[float], inputs: dict[str, Any]) -> None:
    """One billboard that lives `life` seconds from `when`."""
    node(f"{nid}_ps", "particle_system", {"max_particles": 2, "lifetime": r4(life), "size": size, **params}, inputs, layer)
    node(nid, "emitter", {"shape": "point", "position": position, "rate": 0, "burst_count": 1, "burst_times": [0.0],
                          "velocity": 0.0, "start_time": fr(when) - 0.001, "duration": 0.05},
         {"particle": f"{nid}_ps"}, layer)


def build_burst() -> None:
    layer = "burst"
    # Wide blue flash: soft, short, mostly colour. The white lives in the small core only.
    single("flash", layer, BURST_T, 0.24, 3.4, {
        "size_over_life": [[0.0, 0.35], [0.25, 1.0], [1.0, 1.25]],
        "color": tint(ICE, 1.0), "opacity": 0.45, "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.4, 0.4], [1.0, 0.0]],
        "emissive": 0.6, "blend": "additive"}, [0.0, 0.7, 0.0], {"sprite": "tex_glow", "material": "mat_glow"})
    single("flash_core", layer, BURST_T, 0.12, 0.7, {
        "size_over_life": [[0.0, 0.4], [0.3, 1.0], [1.0, 0.7]],
        "color": tint(WHITE, 1.25), "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [1.0, 0.0]],
        "emissive": 1.0, "blend": "additive"}, [0.0, 0.6, 0.0], {"sprite": "tex_glow", "material": "mat_glow"})

    # ~60 streaks of uneven speed thrown from a small sphere: a many-pointed irregular burst.
    node("ray_ps", "particle_system", {
        "max_particles": 90, "lifetime": 0.24, "lifetime_variance": 0.07, "size": 0.13, "size_variance": 0.05,
        "size_over_life": [[0.0, 1.0], [1.0, 0.5]],
        "color": tint(CYAN, 1.5), "color_over_life": [[0.0, tint(WHITE, 1.0)], [0.4, tint(CYAN, 1.0)], [1.0, tint(BLUE, 1.0)]],
        "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.55, 0.7], [1.0, 0.0]],
        "emissive": 1.0, "drag": 5.5, "blend": "additive",
        "render_mode": "stretched_billboard", "velocity_stretch": 1.05,
    }, {"sprite": "tex_spark"}, layer)
    node("rays", "emitter", {
        "shape": "hemisphere", "radius": 0.35, "surface_only": True, "position": [0.0, 0.35, 0.0],
        "rate": 0, "burst_count": 48, "burst_times": [0.0],
        "velocity": 12.0, "velocity_variance": 6.5, "direction": [0, 0, 0],
        "start_time": fr(BURST_T) - 0.001, "duration": 0.05,
    }, {"particle": "ray_ps"}, layer)


# ---------------------------------------------------------------------------
# the spikes
# ---------------------------------------------------------------------------


def spike_direction(azimuth: float, lean: float) -> list[float]:
    a, t = math.radians(azimuth), math.radians(lean)
    return [r4(math.sin(t) * math.cos(a)), r4(math.cos(t)), r4(math.sin(t) * math.sin(a))]


def spike_rows() -> list[dict[str, Any]]:
    """Every spike emitter as a row: id, class, when it erupts, and what kind it is."""
    rows = []
    for i, (azimuth, radius, lean, yaw) in enumerate(CROWN):
        # the crown stabs out over three frames, not all in one
        rows.append({"id": f"crown_{i}", "cls": "crown", "when": fr(BURST_T + (i % 3) / FPS),
                     "hero": (azimuth, radius, lean, yaw)})
    for i, (azimuth, radius, lean, yaw, cls) in enumerate(RIM):
        rows.append({"id": f"rim_{i}", "cls": cls, "when": fr(front_time(radius) + ((i * 5) % 3) / FPS),
                     "hero": (azimuth, radius, lean, yaw)})
    for f in FILL:
        rows.append({"id": f.id, "cls": f.cls, "when": fr(front_time(0.5 * (f.inner + f.outer))), "fill": f})
    return rows


def build_spike_systems(rows: list[dict[str, Any]]) -> None:
    for c in CLASSES.values():
        times = [row["when"] for row in rows if row["cls"] == c.id]
        first, last = min(times), max(times)
        life = DEAD_T - last            # the last one to erupt is gone at DEAD_T, the first a little sooner
        grow = 0.075 / life
        sink = (SINK_T - 0.5 * (first + last)) / life
        shape = c.girth / (0.3 * c.length)
        node(f"spike_{c.id}_ps", "particle_system", {
            "max_particles": 80, "lifetime": r4(life), "lifetime_variance": 0.04,
            "size": c.length, "size_variance": c.variance,
            # stab out of the ground, overshoot, settle; hold; sink back
            "size_over_life": [[0.0, 0.0], [r4(grow), 1.14], [r4(grow * 1.9), 0.97], [r4(grow * 3.2), 1.0],
                               [r4(sink), 1.0], [1.0, 0.0]],
            "color": [c.tint[0], c.tint[1], c.tint[2], 1.0],
            "color_over_life": [[0.0, [1.5, 1.5, 1.5, 1.0]], [0.16, [1.0, 1.0, 1.0, 1.0]],
                                [0.75, [0.82, 0.88, 1.0, 1.0]], [1.0, [0.5, 0.62, 0.95, 1.0]]],
            "opacity_over_life": [[0.0, 1.0], [1.0, 1.0]],
            "render_mode": "mesh", "blend": "alpha", "drag": 0.0,
            "orientation": "velocity", "tilt": 4.0,
            # a blade, not a cone: wide one way, thin the other, each axis with its own variance
            "mesh_scale": [r4(1.3 * shape), 2.0, r4(0.62 * shape)],
            "mesh_scale_variance": [r4(0.36 * shape), 0.22, r4(0.16 * shape)],
        }, {"mesh": c.mesh, "material": "mat_ice"}, "spikes")


def build_spikes() -> tuple[list[str], list[str]]:
    rows = spike_rows()
    build_spike_systems(rows)
    heroes, fills = [], []
    for row in rows:
        common = {"rate": 0, "burst_times": [0.0], "start_time": r4(row["when"] - 0.001), "duration": 0.05}
        if "hero" in row:
            azimuth, radius, lean, yaw = row["hero"]
            a = math.radians(azimuth)
            heroes.append(node(row["id"], "emitter", {
                "shape": "point", "position": [r4(radius * math.cos(a)), 0.0, r4(radius * math.sin(a))],
                "burst_count": 1,
                # a crawl along the axis: `orientation: velocity` needs a direction, and the spike keeps creeping
                "velocity": 0.075, "direction": spike_direction(azimuth + yaw, lean), "spread": 3.0, **common,
            }, {"particle": f"spike_{row['cls']}_ps"}, "spikes"))
        else:
            f = row["fill"]
            lean = math.radians(f.lean)
            fills.append(node(f.id, "emitter", {
                "shape": "ring", "radius": f.outer, "inner_radius": f.inner, "position": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "burst_count": f.count,
                "velocity": r4(0.075 * math.cos(lean)), "direction": [0, 1, 0], "spread": f.cone,
                "radial_velocity": r4(0.075 * math.sin(lean)), **common,
            }, {"particle": f"spike_{f.cls}_ps"}, "spikes", seed=f.seed))
    return heroes, fills


# ---------------------------------------------------------------------------
# frozen ground: nested seam decals, radial fractures, frost disc, shock ring
# ---------------------------------------------------------------------------


def build_ground() -> list[str]:
    layer = "ground"
    cracks = []
    # (id, size m, lights at, y) - the inner copy starts to glow during energy_build
    for nid, size, when, rise, y in (("web_inner", 3.3, 0.24, 0.2, 0.012), ("web_mid", 6.2, 0.58, 0.16, 0.014),
                                     ("web_outer", 9.7, 0.74, 0.18, 0.016)):
        early = nid == "web_inner"
        keys = [(when, 0.0), (when + rise, 0.55 if early else 1.0)]
        if early:
            keys += [(BURST_T, 0.6), (BURST_T + 0.05, 1.0)]
        peak = max(when + rise, BURST_T + 0.05)
        hold = 0.6 if early else 1.0      # the copies overlap in the middle: the inner one steps back
        keys += [(max(peak + 0.02, 0.95), hold), (1.25, 0.62 * hold), (SINK_T, 0.42 * hold), (2.16, 0.0)]
        cracks.append(node(nid, "decal", {
            "shape": "circle", "size": track([(when, [size * 0.9, size * 0.9]), (when + rise + 0.1, [size, size])]),
            "position": [0.0, y, 0.0], "rotation": [0.0, {"web_inner": 0.0, "web_mid": 73.0, "web_outer": 151.0}[nid], 0.0],
            "color": [0.16, 0.46, 1.0, 1.0], "emissive": 2.0, "blend": "additive", "opacity": track(keys),
            "fade_in": 0.0, "fade_out": 0.0, "start_time": fr(when),
        }, {"texture": "tex_web"}, layer))

    # radial fractures racing out with the front (the rays end at uv 0.5)
    cracks.append(node("fractures", "decal", {
        "shape": "circle", "size": front_track(0.47, lead=0.35, floor=0.6), "position": [0.0, 0.018, 0.0],
        "color": [0.3, 0.66, 1.0, 1.0], "emissive": 2.0, "blend": "additive",
        "opacity": track([(BURST_T, 0.0), (BURST_T + 0.04, 1.0), (1.0, 1.0), (1.3, 0.55), (SINK_T, 0.36), (2.16, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": fr(BURST_T),
    }, {"texture": "tex_rays"}, layer))

    # the frozen disc: rime spreading just behind the front, left behind until the fade
    node("frost", "decal", {
        "shape": "circle", "size": front_track(0.47, lead=-0.25, floor=0.6), "position": [0.0, 0.006, 0.0],
        "color": [0.3, 0.5, 0.95, 1.0], "blend": "alpha",
        "opacity": track([(BURST_T, 0.0), (BURST_T + 0.1, 0.4), (1.0, 0.42), (SINK_T, 0.34), (2.17, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": fr(BURST_T),
    }, {"texture": "tex_frost"}, layer)

    # the fast frost ring: the front itself
    node("shock", "decal", {
        "shape": "circle", "size": front_track(0.43, lead=0.1, floor=0.5), "position": [0.0, 0.02, 0.0],
        "color": tint(ICE, 1.0), "blend": "additive",
        "emissive": track([(BURST_T, 2.6), (0.8, 2.0), (0.96, 0.0)]),
        "opacity": track([(BURST_T, 0.0), (BURST_T + 0.04, 1.0), (0.82, 0.9), (0.97, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, "start_time": fr(BURST_T), "duration": 0.56,
    }, {"texture": "tex_shock"}, layer)
    return cracks


# ---------------------------------------------------------------------------
# shards, chips, glitter
# ---------------------------------------------------------------------------


def build_shards() -> tuple[list[str], list[str]]:
    layer = "shards"
    # elongated crystal shards: out of the burst, and off the rim when the wave lands
    node("shard_ps", "particle_system", {
        "max_particles": 70, "lifetime": 0.92, "lifetime_variance": 0.2, "size": 0.4, "size_variance": 0.16,
        "size_over_life": [[0.0, 0.3], [0.08, 1.0], [0.82, 1.0], [1.0, 0.0]],
        "color": [0.55, 0.82, 1.0, 1.0], "opacity_over_life": [[0.0, 1.0], [1.0, 1.0]],
        "angular_velocity": 280.0, "angular_velocity_variance": 170.0,
        "render_mode": "mesh", "blend": "alpha", "drag": 0.55, "mass": 1.0, "orientation": "tumble",
        "mesh_scale": [0.6, 1.6, 0.6], "mesh_scale_variance": [0.18, 0.5, 0.18], "bounce": 0.25, "friction": 0.5,
    }, {"mesh": "mesh_shard", "material": "mat_ice", "forces": ["gravity"], "colliders": ["ground"]}, layer)
    node("shards", "emitter", {
        "shape": "ring", "radius": 0.9, "inner_radius": 0.4, "position": [0.0, 0.4, 0.0],
        "rate": 0, "burst_count": 14, "burst_times": [0.0],
        "velocity": 6.2, "velocity_variance": 2.4, "direction": [0, 1, 0], "spread": 32.0, "radial_velocity": 5.2,
        "start_time": fr(BURST_T + 0.017) - 0.001, "duration": 0.05,
    }, {"particle": "shard_ps"}, layer, seed=7)
    node("shards_rim", "emitter", {
        "shape": "ring", "radius": 4.0, "inner_radius": 3.0, "position": [0.0, 0.5, 0.0], "scale": [1.0, 1.0, 1.0],
        "rate": 0, "burst_count": 12, "burst_times": [0.0],
        "velocity": 4.4, "velocity_variance": 1.6, "direction": [0, 1, 0], "spread": 30.0, "radial_velocity": 3.4,
        "start_time": fr(0.8) - 0.001, "duration": 0.05,
    }, {"particle": "shard_ps"}, layer, seed=9)

    # sprite chips: out of the burst, then kicked up along the front (a ring emitter scaled with it)
    node("chip_ps", "particle_system", {
        "max_particles": 520, "lifetime": 0.62, "lifetime_variance": 0.2, "size": 0.12, "size_variance": 0.06,
        "size_over_life": [[0.0, 0.5], [0.15, 1.0], [1.0, 0.35]], "rotation_variance": 180.0,
        "angular_velocity": 0.0, "angular_velocity_variance": 520.0,
        "color": tint(CYAN, 1.3), "color_over_life": [[0.0, tint(WHITE, 1.0)], [0.5, tint(CYAN, 1.0)], [1.0, tint(BLUE, 1.0)]],
        "opacity_over_life": [[0.0, 1.0], [0.75, 1.0], [1.0, 0.0]],
        "emissive": 1.1, "drag": 1.1, "blend": "additive",
    }, {"sprite": "tex_chip", "forces": ["chip_gravity"], "colliders": ["ground"]}, layer)
    node("chips_burst", "emitter", {
        "shape": "hemisphere", "radius": 0.5, "position": [0.0, 0.3, 0.0],
        "rate": 0, "burst_count": 70, "burst_times": [0.0],
        "velocity": 7.0, "velocity_variance": 3.5, "direction": [0, 0, 0],
        "start_time": fr(BURST_T) - 0.001, "duration": 0.05,
    }, {"particle": "chip_ps"}, layer)
    node("chips_front", "emitter", {
        "shape": "ring", "radius": 1.0, "inner_radius": 0.9, "position": [0.0, 0.15, 0.0],
        "scale": front_track(1.0, lead=-0.1, floor=0.3),
        "rate": track([(0.47, 0.0), (0.5, 300.0), (0.86, 340.0), (0.9, 0.0)]),
        "velocity": 3.4, "velocity_variance": 1.6, "direction": [0, 1, 0], "spread": 35.0, "radial_velocity": 3.0,
        "start_time": fr(0.47), "duration": 0.45,
    }, {"particle": "chip_ps"}, layer)

    # glitter: tiny points that twinkle (several bright beats in one life) and sink slowly
    twinkle = [[0.0, 0.0], [0.08, 1.0], [0.2, 0.15], [0.32, 1.0], [0.46, 0.1], [0.6, 0.9], [0.74, 0.12], [0.86, 0.7],
               [1.0, 0.0]]
    node("glitter_ps", "particle_system", {
        "max_particles": 420, "lifetime": 0.5, "lifetime_variance": 0.14, "size": 0.05, "size_variance": 0.025,
        "color": tint(WHITE, 1.4), "opacity_over_life": twinkle, "emissive": 3.5, "emissive_over_life": twinkle,
        "drag": 1.5, "blend": "additive",
    }, {"sprite": "tex_spark", "forces": ["drift"]}, layer)
    node("glitter_front", "emitter", {
        "shape": "ring", "radius": 1.0, "inner_radius": 0.75, "position": [0.0, 0.5, 0.0],
        "scale": front_track(1.0, lead=-0.2, floor=0.3),
        "rate": track([(0.47, 0.0), (0.5, 260.0), (0.9, 260.0), (0.93, 0.0)]),
        "velocity": 1.6, "velocity_variance": 1.2, "direction": [0, 1, 0], "spread": 60.0,
        "start_time": fr(0.47), "duration": 0.48,
    }, {"particle": "glitter_ps"}, layer)
    node("glitter_air", "emitter", {
        "shape": "disc", "radius": 4.4, "position": [0.0, 0.9, 0.0], "scale": [1.0, 1.0, 1.0],
        "rate": track([(0.9, 0.0), (0.95, 190.0), (1.5, 170.0), (1.55, 0.0)]),
        "velocity": 0.5, "velocity_variance": 0.5, "direction": [0, 1, 0], "spread": 180.0,
        "start_time": fr(0.9), "duration": 0.66,
    }, {"particle": "glitter_ps"}, layer)
    node("shatter_ps", "particle_system", {
        "max_particles": 420, "lifetime": 0.27, "lifetime_variance": 0.04, "size": 0.17, "size_variance": 0.08,
        "size_over_life": [[0.0, 0.5], [0.15, 1.0], [1.0, 0.3]], "rotation_variance": 180.0,
        "angular_velocity": 0.0, "angular_velocity_variance": 620.0,
        "color": tint(CYAN, 1.2), "color_over_life": [[0.0, tint(WHITE, 1.0)], [0.6, tint(CYAN, 1.0)], [1.0, tint(BLUE, 1.0)]],
        "opacity_over_life": [[0.0, 1.0], [0.7, 1.0], [1.0, 0.0]], "emissive": 1.4, "drag": 0.6, "blend": "additive",
    }, {"sprite": "tex_chip", "forces": ["gravity"]}, "aftermath")
    node("shatter", "emitter", {
        "shape": "ring", "radius": 4.4, "inner_radius": 0.6, "position": [0.0, 0.8, 0.0], "scale": [1.0, 1.0, 1.0],
        "rate": 0, "burst_count": 80, "burst_times": [0.0, 0.0333],
        "velocity": 1.8, "velocity_variance": 1.2, "direction": [0, 1, 0], "spread": 75.0, "radial_velocity": 1.0,
        "start_time": fr(SINK_T + 0.02) - 0.001, "duration": 0.1,
    }, {"particle": "shatter_ps"}, "aftermath", seed=13)
    node("glitter_fade_ps", "particle_system", {
        "max_particles": 470, "lifetime": 0.27, "lifetime_variance": 0.04, "size": 0.06, "size_variance": 0.03,
        "color": tint(WHITE, 1.4), "opacity_over_life": twinkle, "emissive": 3.5, "emissive_over_life": twinkle,
        "drag": 1.0, "blend": "additive",
    }, {"sprite": "tex_spark", "forces": ["chip_gravity"]}, "aftermath")
    node("glitter_fade", "emitter", {
        "shape": "ring", "radius": 4.4, "inner_radius": 0.6, "position": [0.0, 0.7, 0.0], "scale": [1.0, 1.0, 1.0],
        "rate": 0, "burst_count": 90, "burst_times": [0.0, 0.0333],
        "velocity": 1.4, "velocity_variance": 1.0, "direction": [0, 1, 0], "spread": 80.0,
        "start_time": fr(SINK_T + 0.02) - 0.001, "duration": 0.1,
    }, {"particle": "glitter_fade_ps"}, "aftermath")
    return ["shards", "shards_rim"], ["chips_burst", "chips_front", "glitter_front", "glitter_air", "glitter_fade", "shatter"]


# ---------------------------------------------------------------------------
# snow mist
# ---------------------------------------------------------------------------


def build_mist() -> list[str]:
    layer = "mist"
    # rolling out with the front: born on it, pushed on, swelling
    node("roll_ps", "particle_system", {
        "max_particles": 300, "lifetime": 0.85, "lifetime_variance": 0.2, "size": 1.45, "size_variance": 0.45,
        "size_over_life": [[0.0, 0.45], [0.4, 1.0], [1.0, 1.5]], "rotation_variance": 180.0,
        "angular_velocity_variance": 40.0,
        "color": [0.7, 0.84, 1.0, 1.0], "opacity": 0.5, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.55, 0.7], [1.0, 0.0]],
        "drag": 2.6, "blend": "alpha", "sort": True, "sprite_fps": 10.0,
    }, {"sprite": "tex_puff", "material": "mat_mist", "forces": ["drift"]}, layer)
    node("roll", "emitter", {
        "shape": "ring", "radius": 1.0, "inner_radius": 0.8, "position": [0.0, 0.3, 0.0],
        "scale": front_track(1.0, lead=-0.15, floor=0.3),
        "rate": track([(0.45, 0.0), (0.48, 230.0), (0.88, 300.0), (0.92, 0.0)]),
        "velocity": 0.7, "velocity_variance": 0.5, "direction": [0, 1, 0], "spread": 40.0, "radial_velocity": 4.2,
        "start_time": fr(0.45), "duration": 0.49,
    }, {"particle": "roll_ps"}, layer)

    # drifting over the frozen disc while it lingers
    node("haze_ps", "particle_system", {
        "max_particles": 80, "lifetime": 0.66, "lifetime_variance": 0.1, "size": 1.5, "size_variance": 0.45,
        "size_over_life": [[0.0, 0.6], [1.0, 1.4]], "rotation_variance": 180.0, "angular_velocity_variance": 20.0,
        "color": [0.8, 0.9, 1.0, 1.0], "opacity": 0.36, "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.65, 0.8], [1.0, 0.0]],
        "drag": 1.2, "blend": "alpha", "sort": True, "sprite_fps": 8.0,
    }, {"sprite": "tex_puff", "material": "mat_mist", "forces": ["drift"]}, layer)
    node("haze", "emitter", {
        "shape": "disc", "radius": 4.3, "position": [0.0, 0.28, 0.0], "scale": [1.0, 1.0, 1.0],
        "rate": track([(0.85, 0.0), (0.95, 60.0), (1.3, 56.0), (1.4, 0.0)]),
        "velocity": 0.3, "velocity_variance": 0.2, "direction": [0, 1, 0], "spread": 60.0, "radial_velocity": 0.5,
        "start_time": fr(0.85), "duration": 0.57,
    }, {"particle": "haze_ps"}, layer)
    return ["creep", "roll", "haze"]


# ---------------------------------------------------------------------------
# light
# ---------------------------------------------------------------------------


def build_lights() -> None:
    layer = "light"
    # key: from the left, so every crystal has lit facets and shadowed ones. The stage ground has no
    # albedo (the ground glow is the cast_glow decal) but it keeps a specular sheen, so every light
    # sits high: a low one, above all one behind the effect facing the camera, hazes the floor grey.
    node("key_light", "light", {
        "light_type": "point", "position": [-7.0, 12.0, 6.0], "color": [0.8, 0.92, 1.0, 1.0], "radius": 40.0,
        "intensity": track([(0.0, 0.0), (0.12, 40.0), (BURST_T, 60.0), (0.5, 150.0), (1.2, 140.0), (SINK_T, 95.0),
                            (2.18, 0.0)]),
    }, None, layer)
    # fill: deep blue from behind on the right, the colour of the shadows
    node("fill_light", "light", {
        "light_type": "point", "position": [8.0, 10.0, -6.0], "color": [0.12, 0.3, 1.0, 1.0], "radius": 40.0,
        "intensity": track([(0.3, 0.0), (0.5, 60.0), (1.2, 55.0), (SINK_T, 36.0), (2.18, 0.0)]),
        "start_time": 0.3,
    }, None, layer)
    # rim: behind and high above, so facet edges and the far mist catch a highlight
    node("rim_light", "light", {
        "light_type": "point", "position": [0.0, 13.0, -8.0], "color": [0.7, 0.9, 1.0, 1.0], "radius": 40.0,
        "intensity": track([(0.3, 0.0), (0.5, 170.0), (1.2, 160.0), (SINK_T, 100.0), (2.18, 0.0)]),
        "start_time": 0.3,
    }, None, layer)
    # the burst: a flash that settles into a cold glow above the crown
    node("core_light", "light", {
        "light_type": "point", "position": [0.0, 2.4, 0.0], "color": [0.3, 0.6, 1.0, 1.0], "radius": 12.0,
        "intensity": track([(0.0, 0.0), (0.2, 1.5), (0.4, 4.0), (BURST_T + 0.03, 18.0), (0.62, 8.0), (1.2, 6.0),
                            (SINK_T, 4.0), (2.15, 0.0)]),
    }, None, layer)


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

COLOUR_PARAMETERS = ("color", "color_over_life", "emissive_color", "base_color")


def build_controls(heroes: list[str], fills: list[str], cracks: list[str], shard_emitters: list[str],
                   sprite_emitters: list[str], mist_emitters: list[str]) -> list[dict[str, Any]]:
    def multiplier(cid, label, group, bindings, lo=0.0, hi=3.0, step=0.01):
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": 1.0, "value": 1.0,
                "step": step, "unit": "x", "bindings": bindings}

    def bind(ids, parameter, op="multiply"):
        return [{"node": nid, "parameter": parameter, "op": op} for nid in ids]

    def authored(nid, parameter):
        return next(n for n in nodes if n["id"] == nid)["parameters"].get(parameter)

    def with_parameter(ids, parameter):
        return [nid for nid in ids if authored(nid, parameter) not in (None, 0)]

    emitters = [n["id"] for n in nodes if n["type"] == "emitter"]
    scaled = [nid for nid in emitters if authored(nid, "scale") is not None]
    decals = [n["id"] for n in nodes if n["type"] == "decal" and n["id"] != "rune"]
    spike_systems = [f"spike_{c}_ps" for c in CLASSES]
    amount = fills + shard_emitters + sprite_emitters

    hue = []
    for n in nodes:
        for parameter in COLOUR_PARAMETERS:
            if parameter in n.get("parameters", {}):
                hue.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    return [
        # the whole disc: hero feet, ring emitters, front-following emitters, decals, how far things are thrown
        multiplier("nova_radius", "Nova radius", "Global",
                   bind(heroes, "position") + bind(scaled, "scale") + bind(decals, "size")
                   + bind(["roll", "chips_front", "shards_rim"], "radial_velocity"), lo=0.6, hi=1.4),
        multiplier("spike_amount", "Spike amount", "Ice spikes",
                   bind(with_parameter(amount, "burst_count"), "burst_count")
                   + bind([e for e in amount if isinstance(authored(e, "rate"), dict)], "rate"), hi=2.5, step=0.05),
        multiplier("spike_height", "Spike height", "Ice spikes",
                   bind(spike_systems, "size") + bind(spike_systems, "size_variance"), lo=0.5, hi=1.6),
        multiplier("crack_glow", "Crack glow", "Frozen ground", bind(cracks, "color")),
        multiplier("mist", "Snow mist", "Snow mist",
                   bind(mist_emitters, "rate") + bind(["roll_ps", "haze_ps", "creep_ps"], "opacity"), hi=2.5),
        {"id": "hue", "label": "Hue", "group": "Global", "min": -180.0, "max": 180.0, "default": 0.0, "value": 0.0,
         "step": 1.0, "unit": "deg", "bindings": hue},
        {"id": "global_speed", "label": "Speed", "group": "Global", "min": 0.25, "max": 4.0, "default": 1.0,
         "value": 1.0, "step": 0.05, "unit": "x",
         "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]},
    ]


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------


def build() -> dict[str, Any]:
    nodes.clear()
    build_textures()
    build_shared()
    build_cast()
    build_burst()
    heroes, fills = build_spikes()
    cracks = build_ground()
    shard_emitters, sprite_emitters = build_shards()
    mist_emitters = build_mist()
    build_lights()
    # Raised three-quarter view: the disc reads as a circle with depth and the rim spikes stand
    # against the black. The studio frames about 1.45x wider than this camera.
    node("cam", "camera", {"position": [0.0, 8.0, 12.6], "target": [0.0, 0.3, 0.0], "fov": 40.0})
    controls = build_controls(heroes, fills, cracks, shard_emitters, sprite_emitters, mist_emitters)

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": DESCRIPTION,
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [{"name": n, "start": s, "end": e} for n, s, e in PHASES]},
        "layers": [
            {"id": "telegraph", "name": "Cast rune", "role": "telegraph"},
            {"id": "burst", "name": "Explosion", "role": "ignition"},
            {"id": "spikes", "name": "Ice spikes", "role": "primary"},
            {"id": "ground", "name": "Frozen ground", "role": "interaction"},
            {"id": "shards", "name": "Shards and glitter", "role": "secondary"},
            {"id": "mist", "name": "Snow mist", "role": "secondary"},
            {"id": "light", "name": "Light", "role": "interaction"},
            {"id": "aftermath", "name": "Shatter", "role": "aftermath"},
        ],
        "nodes": list(nodes),
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/frost_nova.py",
            "prompt": "let's create the frost nova effect and add it to the list",
            "analysis": "cast_start 0-0.2 a thin rune ring ignites, mist creeps, a soft wide cold glow rises | "
                        "energy_build 0.2-0.4 the ring brightens, motes and mist are pulled inward, the first "
                        "fractures glow under it | explosion 0.4-0.6 a short flash, a many-pointed streak burst and a "
                        "crown of leaning spikes out of the centre | shockwave 0.6-0.9 the front races to the rim: "
                        "spikes erupt as it reaches them, fractures and rime spread behind a torn frost ring, mist "
                        "rolls out, shards fly | full_impact 0.9-1.2 everything at full extent | linger 1.2-1.8 "
                        "fractures dim, mist drifts, glitter twinkles | fade 1.8-2.2 spikes sink and shatter",
            "layout": {"radius": RADIUS, "front": [[t, r] for t, r in FRONT],
                       "crown": [list(row) for row in CROWN], "rim": [list(row) for row in RIM],
                       "fill": [[f.id, f.inner, f.outer, f.count, f.cls, f.lean] for f in FILL]},
            "render_settings": {
                "background": [0.0, 0.0, 0.0, 1.0],
                "ground_albedo": 0.0,
                "bloom_intensity": 0.26,
                "bloom_radius": 0.04,
                "exposure": 1.0,
                "grid": False,
            },
            "tags": ["ice", "frost", "nova", "aoe", "radial", "crowd-control", "spikes"],
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
