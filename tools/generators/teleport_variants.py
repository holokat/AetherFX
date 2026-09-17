#!/usr/bin/env python3
"""Generate the four Teleport effects from one parametrised description.

    python tools/generators/teleport_variants.py --out out/work/teleport/effects

Every variant shares the same node graph - identical ids, identical animation -
and differs only through the VARIANTS table: a palette that reaches every colour
(decals, particles, trails, beams, lights, volume, materials) and a rune
"language" for the portal ring and the ground circle.

The vertical portal ring is built with the parent-chain technique from
examples/effects/arcane_aoe.json: an invisible hub tilted 90 degrees about X so
its local XZ plane is the world XY plane, a child whose Y rotation is keyframed
(the orbit angle) and whose scale is keyframed (the ring radius), and beads at a
fixed local offset that carry `trail` ribbons.  The trails therefore draw exact
circles in a vertical plane in world space - a real disc, not a billboard.

House style: no crosses, plus signs or four-armed star motifs anywhere; every
ring, ribbon and shaft is soft-edged, tapered and luminous (soft cross-width
textures, taper curves, gradient falloff).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# palettes - every colour in the effect is derived from one of these entries
# ---------------------------------------------------------------------------

VIOLET = {
    "hot": [0.88, 0.76, 1.00],       # ring cores, hot sparks (near-white, tinted)
    "mid": [0.55, 0.24, 1.00],       # the signature hue: ring glow, ribbons, light
    "deep": [0.13, 0.05, 0.42],      # nebula shell, mist
    "accent": [0.95, 0.32, 0.92],    # secondary hue (magenta)
    "core_hot": [0.46, 0.60, 1.00],  # bright inside of the portal nebula
    "light": [0.58, 0.38, 1.00],     # point light
    "shard": [0.72, 0.62, 1.00],     # crystal albedo
    "mist": [0.34, 0.24, 0.62],      # low ground mist
    "bg": [0.006, 0.006, 0.017, 1.0],
}

GOLD = {
    "hot": [1.00, 0.95, 0.80],
    "mid": [1.00, 0.68, 0.18],
    "deep": [0.32, 0.13, 0.010],
    "accent": [1.00, 0.40, 0.07],
    "core_hot": [1.00, 0.87, 0.50],
    "light": [1.00, 0.74, 0.32],
    "shard": [1.00, 0.86, 0.58],
    "mist": [0.52, 0.34, 0.14],
    "bg": [0.016, 0.009, 0.003, 1.0],
}

JADE = {
    "hot": [0.80, 1.00, 0.90],
    "mid": [0.10, 0.95, 0.52],
    "deep": [0.010, 0.22, 0.14],
    "accent": [0.62, 1.00, 0.30],
    "core_hot": [0.42, 1.00, 0.80],
    "light": [0.26, 1.00, 0.62],
    "shard": [0.62, 1.00, 0.82],
    "mist": [0.16, 0.44, 0.32],
    "bg": [0.003, 0.013, 0.008, 1.0],
}

#: The one table.  slug -> display name, palette, rune language, seed.
VARIANTS: dict[str, dict[str, Any]] = {
    "teleport": {
        "name": "Teleport",
        "description": "A violet rune circle ignites, a vertical portal opens on a starry indigo core, "
                       "then collapses into a tightening spiral and a burst of fading shards.",
        "palette": VIOLET,
        "runes": "script",
        "seed": 90210,
    },
    "teleport_rune_style_2": {
        "name": "Teleport Rune Style 2",
        "description": "The violet teleport portal written in a second rune language: segmented arcs, "
                       "angular strokes, dot markers and a rotating dashed outer ring.",
        "palette": VIOLET,
        "runes": "angular",
        "seed": 90211,
    },
    "teleport_golden": {
        "name": "Teleport Golden",
        "description": "The teleport portal in warm gold and amber, a white-gold double ring over an "
                       "ember-lit core.",
        "palette": GOLD,
        "runes": "script",
        "seed": 90212,
    },
    "teleport_green": {
        "name": "Teleport Green",
        "description": "The teleport portal in emerald and jade, a mint double ring over a deep "
                       "green-gold core.",
        "palette": JADE,
        "runes": "script",
        "seed": 90213,
    },
}

# ---------------------------------------------------------------------------
# timing (the concept sheet, total 2.0 s)
# ---------------------------------------------------------------------------

DURATION = 2.0
PHASES = [
    ("initiate", 0.0, 0.2),
    ("opening", 0.2, 0.5),
    ("open", 0.5, 1.5),
    ("closing", 1.5, 1.8),
    ("fade", 1.8, 2.0),
]

PORTAL_Y = 1.25          # centre of the vertical portal
R_RING = 1.10            # portal radius -> 2.2 m across

# radius envelope of every ring strand (a multiplier on the bead's local offset)
RADIUS_ENVELOPE = [
    (0.00, 0.0), (0.16, 0.0), (0.19, 0.07), (0.24, 0.31), (0.29, 0.60),
    (0.34, 0.83), (0.39, 0.98), (0.44, 1.06), (0.50, 1.0), (0.95, 1.018),
    (1.28, 0.994), (1.46, 1.0), (1.55, 1.055), (1.62, 0.87), (1.70, 0.55),
    (1.78, 0.19), (1.85, 0.0), (2.00, 0.0),
]

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def c(rgb: list[float], a: float = 1.0, k: float = 1.0) -> list[float]:
    """An RGBA colour from a palette entry, optionally scaled."""
    return [round(rgb[0] * k, 4), round(rgb[1] * k, 4), round(rgb[2] * k, 4), a]


def lum(rgb: list[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def palette_gain(p: dict[str, Any], key: str = "mid") -> float:
    """Scale lights, emissive decals, volumes and additive particles so every
    palette lands at the same stage brightness: gold and jade are far more
    luminous than violet at equal RGB values, so an unscaled green or gold
    variant washes the whole frame out."""
    return round((lum(VIOLET[key]) / lum(p[key])) ** 0.8, 4)


def mix(a: list[float], b: list[float], t: float, alpha: float = 1.0) -> list[float]:
    return [round(a[i] + (b[i] - a[i]) * t, 4) for i in range(3)] + [alpha]


def tr(pairs: list[tuple[float, Any]], interp: str | None = None) -> dict[str, Any]:
    """A keyframed parameter {"value": first, "track": [...]}"""
    keys = []
    for t, v in pairs:
        key: dict[str, Any] = {"time": round(t, 4), "value": v}
        if interp:
            key["interp"] = interp
        keys.append(key)
    return {"value": pairs[0][1], "track": keys}


def env(pairs: list[tuple[float, float]], scale: float = 1.0) -> dict[str, Any]:
    return tr([(t, round(v * scale, 5)) for t, v in pairs])


def vec_env(pairs: list[tuple[float, float]], base: list[float]) -> dict[str, Any]:
    return tr([(t, [round(base[0] * v, 5), round(base[1] * v, 5), round(base[2] * v, 5)]) for t, v in pairs])


def sample(pairs: list[tuple[float, float]], t: float) -> float:
    """Piecewise-linear lookup used to bake the radius envelope into curves."""
    if t <= pairs[0][0]:
        return pairs[0][1]
    for (t0, v0), (t1, v1) in zip(pairs, pairs[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * f
    return pairs[-1][1]


def spin_speed(t: float, revs: float) -> float:
    """Angular speed (deg/s) at time t for a strand doing `revs` rev/s while open.

    Fast in-spiral during the opening, steady while the portal is open, and an
    accelerating tightening spiral through the close.
    """
    base = revs * 360.0
    if t < 0.16:
        return 0.0
    if t < 0.40:                       # opening: spin up to 2.4x -> a spiral
        return base * (0.2 + 2.2 * (t - 0.16) / 0.24)
    if t < 0.56:                       # settle back to the open-portal rate
        return base * (2.4 - 1.4 * (t - 0.40) / 0.16)
    if t < 1.46:
        return base
    if t < 1.85:                       # closing: tighten
        return base * (1.0 + 1.35 * (t - 1.46) / 0.39)
    return base * 2.35


def angle_track(revs: float, direction: float, step: float = 0.04) -> dict[str, Any]:
    """Integrate spin_speed into a keyframed Y rotation (a vec3 track)."""
    keys: list[tuple[float, list[float]]] = []
    angle = 0.0
    t = 0.0
    keys.append((0.0, [0.0, 0.0, 0.0]))
    while t < DURATION - 1e-9:
        nxt = min(t + step, DURATION)
        dt = nxt - t
        angle += 0.5 * (spin_speed(t, revs) + spin_speed(nxt, revs)) * dt
        keys.append((nxt, [0.0, round(direction * angle, 3), 0.0]))
        t = nxt
    return tr(keys)


def helix(t0: float, t1: float, turns: float, r0: float, r1: float, y0: float, y1: float,
          phase_deg: float, y_gamma: float = 0.85, step: float = 0.02,
          hold_after: float | None = None) -> dict[str, Any]:
    """A baked conical-helix position track (world space) for a ribbon bead."""
    keys: list[tuple[float, list[float]]] = []
    n = max(2, int(round((t1 - t0) / step)))
    for i in range(n + 1):
        u = i / n
        ang = math.radians(phase_deg + turns * 360.0 * u)
        r = r0 + (r1 - r0) * u
        y = y0 + (y1 - y0) * (u ** y_gamma)
        keys.append((t0 + (t1 - t0) * u, [round(r * math.cos(ang), 4), round(y, 4), round(r * math.sin(ang), 4)]))
    if hold_after is not None and hold_after > t1:
        keys.append((hold_after, keys[-1][1]))
    if t0 > 0.0:
        keys.insert(0, (0.0, keys[0][1]))
    return tr(keys)


def node(nid: str, ntype: str, params: dict[str, Any], *, layer: str | None = None,
         parent: str | None = None, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    n: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        n["layer"] = layer
    if parent:
        n["parent"] = parent
    n["parameters"] = params
    if inputs:
        n["inputs"] = inputs
    return n


def tex(nid: str, w: int, h: int, nodes: list[dict[str, Any]], output: str,
        frames: int = 1) -> dict[str, Any]:
    params: dict[str, Any] = {"width": w, "height": h}
    if frames > 1:
        params["frames"] = frames
    params["graph"] = {"nodes": nodes, "output": output}
    return node(nid, "texture", params)


def op(oid: str, name: str, params: dict[str, Any] | None = None,
       inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": oid, "op": name}
    if params:
        d["params"] = params
    if inputs:
        d["inputs"] = inputs
    return d


def maxes(prefix: str, ids: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Fold a list of graph node ids together with `math max`."""
    out: list[dict[str, Any]] = []
    cur = ids[0]
    for i, nxt in enumerate(ids[1:]):
        nid = f"{prefix}{i}"
        out.append(op(nid, "math", {"mode": "max"}, {"a": cur, "b": nxt}))
        cur = nid
    return out, cur


# ---------------------------------------------------------------------------
# rune languages
# ---------------------------------------------------------------------------
#
# "script": a flowing band of script-like strokes between two thin bright rings
#           (the base sheet).
# "angular": segmented arcs with gaps, angular runic strokes, dot markers and
#            chevron ticks, under three thin rings.
# Neither contains a cross, a plus sign or a four-armed star.


def ground_rings_graph(style: str) -> tuple[list[dict[str, Any]], str]:
    """The static ring structure of the ground circle (soft-edged hairlines)."""
    if style == "script":
        parts = [
            op("r0", "ring", {"radius": 0.472, "thickness": 0.0075, "softness": 0.007}),
            op("r1", "ring", {"radius": 0.446, "thickness": 0.017, "softness": 0.011}),
            op("r2", "ring", {"radius": 0.352, "thickness": 0.012, "softness": 0.009}),
            op("r3", "ring", {"radius": 0.243, "thickness": 0.0075, "softness": 0.007}),
            op("r4", "ring", {"radius": 0.146, "thickness": 0.013, "softness": 0.010}),
        ]
        ids = ["r0", "r1", "r2", "r3", "r4"]
    else:
        # three thin rings + a segmented outer arc band with nine gaps
        parts = [
            op("r0", "ring", {"radius": 0.474, "thickness": 0.0065, "softness": 0.006}),
            op("r1", "ring", {"radius": 0.455, "thickness": 0.0065, "softness": 0.006}),
            op("r2", "ring", {"radius": 0.436, "thickness": 0.0065, "softness": 0.006}),
            op("seg", "ring", {"radius": 0.398, "thickness": 0.028, "softness": 0.012}),
            op("gaps", "spokes", {"count": 9, "width": 0.052, "softness": 0.02,
                                  "inner_radius": 0.30, "outer_radius": 0.52, "rotation": 20.0}),
            op("gapsi", "invert", None, {"a": "gaps"}),
            op("segc", "math", {"mode": "multiply"}, {"a": "seg", "b": "gapsi"}),
            op("r3", "ring", {"radius": 0.286, "thickness": 0.009, "softness": 0.008}),
            op("r4", "ring", {"radius": 0.152, "thickness": 0.011, "softness": 0.009}),
        ]
        ids = ["r0", "r1", "r2", "segc", "r3", "r4"]
    folds, out = maxes("gm", ids)
    return parts + folds, out


def ground_runes_graph(style: str, seed: int) -> tuple[list[dict[str, Any]], str]:
    """The rotating glyph band of the ground circle."""
    if style == "script":
        parts = [
            # flowing script strokes confined to an annulus
            op("ck", "cracks", {"density": 16.0, "width": 0.0105, "seed": seed}),
            op("band", "ring", {"radius": 0.399, "thickness": 0.095, "softness": 0.030}),
            op("sc", "math", {"mode": "multiply"}, {"a": "ck", "b": "band"}),
            # a fainter inner line of script
            op("ck2", "cracks", {"density": 7.0, "width": 0.009, "seed": seed + 17}),
            op("band2", "ring", {"radius": 0.196, "thickness": 0.050, "softness": 0.026}),
            op("sc2", "math", {"mode": "multiply"}, {"a": "ck2", "b": "band2"}),
            op("sc2d", "levels", {"in_low": 0.0, "in_high": 1.0, "out_high": 0.6}, {"a": "sc2"}),
            op("all", "math", {"mode": "max"}, {"a": "sc", "b": "sc2d"}),
            op("soft", "blur", {"radius": 1.1}, {"a": "all"}),
            op("lv", "levels", {"in_low": 0.02, "in_high": 0.85, "gamma": 0.9}, {"a": "soft"}),
        ]
        return parts, "lv"
    parts = [
        # angular runic strokes: voronoi cell edges clipped to a band
        op("vo", "voronoi", {"cells": 26, "mode": "edges", "seed": seed}),
        op("vol", "levels", {"in_low": 0.55, "in_high": 0.93, "gamma": 1.0}, {"a": "vo"}),
        op("band", "ring", {"radius": 0.352, "thickness": 0.062, "softness": 0.028}),
        op("ang", "math", {"mode": "multiply"}, {"a": "vol", "b": "band"}),
        # 14 dot markers just inside the band
        op("dots", "spokes", {"count": 14, "width": 0.017, "softness": 0.010,
                              "inner_radius": 0.300, "outer_radius": 0.320}),
        # chevrons: two sets of seven short radial ticks, splayed +/- 11 degrees
        op("cv1", "spokes", {"count": 7, "width": 0.012, "softness": 0.008,
                             "inner_radius": 0.205, "outer_radius": 0.268, "rotation": 11.0}),
        op("cv2", "spokes", {"count": 7, "width": 0.012, "softness": 0.008,
                             "inner_radius": 0.205, "outer_radius": 0.268, "rotation": -11.0}),
        op("m0", "math", {"mode": "max"}, {"a": "ang", "b": "dots"}),
        op("m1", "math", {"mode": "max"}, {"a": "m0", "b": "cv1"}),
        op("m2", "math", {"mode": "max"}, {"a": "m1", "b": "cv2"}),
        op("soft", "blur", {"radius": 1.0}, {"a": "m2"}),
        op("lv", "levels", {"in_low": 0.02, "in_high": 0.88, "gamma": 0.9}, {"a": "soft"}),
    ]
    return parts, "lv"


def glyph_graph(style: str, seed: int) -> tuple[list[dict[str, Any]], str]:
    """A small morphing glyph tile for the sprites on the portal ring."""
    if style == "script":
        parts = [
            op("t", "time", {"speed": 1.0}),
            op("w", "fbm", {"frequency": 2.2, "octaves": 2, "seed": seed + 5, "animate": 1.0}),
            op("ck", "cracks", {"density": 3.4, "width": 0.075, "seed": seed}),
            op("d", "distort", {"amount": 0.26}, {"a": "ck", "by": "w"}),
            op("mask", "gradient_radial", {"radius": 0.46, "inner_radius": 0.06, "falloff": "smooth"}),
            op("m", "math", {"mode": "multiply"}, {"a": "d", "b": "mask"}),
            op("b", "blur", {"radius": 1.4}, {"a": "m"}),
            op("lv", "levels", {"in_low": 0.06, "in_high": 0.68, "gamma": 0.85}, {"a": "b"}),
        ]
        return parts, "lv"
    parts = [
        op("t", "time", {"speed": 1.0}),
        op("w", "fbm", {"frequency": 2.6, "octaves": 2, "seed": seed + 5, "animate": 1.0}),
        op("vo", "voronoi", {"cells": 5, "mode": "edges", "seed": seed}),
        op("vl", "levels", {"in_low": 0.62, "in_high": 0.97}, {"a": "vo"}),
        op("d", "distort", {"amount": 0.16}, {"a": "vl", "by": "w"}),
        op("mask", "gradient_radial", {"radius": 0.44, "inner_radius": 0.05, "falloff": "smooth"}),
        op("m", "math", {"mode": "multiply"}, {"a": "d", "b": "mask"}),
        op("b", "blur", {"radius": 1.1}, {"a": "m"}),
        op("lv", "levels", {"in_low": 0.05, "in_high": 0.72, "gamma": 0.8}, {"a": "b"}),
    ]
    return parts, "lv"


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------


def build(slug: str, spec: dict[str, Any]) -> dict[str, Any]:
    p = spec["palette"]
    style = spec["runes"]
    seed = spec["seed"]
    angular = style == "angular"

    hot, mid, deep = p["hot"], p["mid"], p["deep"]
    accent, core_hot = p["accent"], p["core_hot"]
    g = palette_gain(p)            # lights, decals, the volume
    gh = palette_gain(p, "hot")    # additive ribbons, glyphs, motes, sparks

    nodes: list[dict[str, Any]] = []
    add = nodes.append

    # ---------------- textures ----------------
    # soft cross-width profile for every ribbon: bright thin centre -> 0 at both
    # edges, so no trail ever shows a hard edge.
    add(tex("tex_band", 8, 64, [
        op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("gi", "invert", None, {"a": "g"}),
        op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 0.46, "gamma": 0.52}, {"a": "t"}),
    ], "lv"))
    add(tex("tex_band_soft", 8, 64, [
        op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("gi", "invert", None, {"a": "g"}),
        op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 2.1}, {"a": "t"}),
    ], "lv"))
    add(tex("tex_shaft", 8, 64, [
        op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("gi", "invert", None, {"a": "g"}),
        op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 1.55}, {"a": "t"}),
    ], "lv"))
    add(tex("tex_glow", 128, 128, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 1.9}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_dot", 48, 48, [
        op("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.02, "falloff": "quadratic"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 1.5}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_puff", 128, 128, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 3.4, "octaves": 4, "seed": seed % 991}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "n"}),
        op("lv", "levels", {"in_low": 0.06, "in_high": 0.62}, {"a": "m"}),
    ], "lv"))
    add(tex("tex_pool", 256, 256, [
        op("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.03, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 2.6, "octaves": 3, "seed": (seed + 13) % 991}),
        op("nl", "levels", {"in_low": 0.25, "in_high": 0.95, "out_low": 0.62, "out_high": 1.0}, {"a": "n"}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 2.5}, {"a": "m"}),
    ], "lv"))
    gr_nodes, gr_out = ground_rings_graph(style)
    add(tex("tex_ground_rings", 512, 512, gr_nodes, gr_out))
    ru_nodes, ru_out = ground_runes_graph(style, seed % 7919)
    add(tex("tex_ground_runes", 512, 512, ru_nodes, ru_out))
    gl_nodes, gl_out = glyph_graph(style, (seed + 41) % 7919)
    add(tex("tex_glyph", 64, 64, gl_nodes, gl_out, frames=6))

    # ---------------- materials ----------------
    add(node("mat_ring", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": c(mid), "emissive_intensity": 0.34,
        "soft_particle": True, "depth_fade": 0.1, "double_sided": True,
    }, inputs={"base_texture": "tex_band"}))
    add(node("mat_ribbon", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": c(mid), "emissive_intensity": 0.28,
        "soft_particle": True, "depth_fade": 0.14, "double_sided": True,
    }, inputs={"base_texture": "tex_band_soft"}))
    add(node("mat_shaft", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": c(hot), "emissive_intensity": 0.22,
        "soft_particle": True, "depth_fade": 0.3, "double_sided": True,
    }, inputs={"base_texture": "tex_shaft"}))
    add(node("mat_spark", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": c(hot), "emissive_intensity": 0.3,
        "soft_particle": True, "depth_fade": 0.1,
    }))
    add(node("mat_glyph", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": c(hot), "emissive_intensity": 0.25,
        "soft_particle": True, "depth_fade": 0.06,
    }))
    add(node("mat_shard", "material", {
        "blend": "alpha", "shading": "lit", "base_color": c(deep, 1.0, 0.55),
        "emissive_color": c(mid), "emissive_intensity": round(1.0 * g, 3),
        "fresnel_power": 0.0, "double_sided": True, "opacity": 0.8,
    }))
    add(node("mat_mist", "material", {
        "blend": "alpha", "shading": "lit", "base_color": c(p["mist"]),
        "emissive_color": c(mid), "emissive_intensity": 0.06,
        "soft_particle": True, "depth_fade": 0.32,
    }))
    add(node("mat_pool", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": c(mid), "emissive_intensity": 0.4, "soft_particle": True,
    }))
    add(node("mat_rune", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": c(mix(mid, hot, 0.5)), "emissive_intensity": 0.5, "soft_particle": True,
    }))

    # ---------------- forces / collider ----------------
    add(node("f_vortex", "force", {
        "force_type": "vortex", "direction": [0.0, 1.0, 0.0], "position": [0.0, 1.0, 0.0],
        "strength": tr([(0.0, 0.0), (0.3, 0.6), (0.6, 1.5), (1.45, 1.5), (1.62, 3.4), (1.9, 0.5)]),
        "radius": 4.5, "falloff": "smooth",
    }))
    add(node("f_lift", "force", {
        "force_type": "buoyancy", "direction": [0.0, 1.0, 0.0], "strength": 1.35, "temperature": 1.0,
    }))
    add(node("f_curl", "force", {
        "force_type": "curl_noise", "strength": 1.5, "frequency": 1.4, "octaves": 2, "speed": 0.7,
    }))
    add(node("f_drift", "force", {
        "force_type": "curl_noise", "strength": 0.55, "frequency": 0.7, "octaves": 2, "speed": 0.35,
    }))
    add(node("f_pull", "force", {
        "force_type": "attractor", "position": [0.0, PORTAL_Y, 0.0],
        "strength": tr([(0.0, 0.0), (0.45, 1.1), (1.45, 1.1), (1.58, 5.5), (1.85, 0.0)]),
        "radius": 3.6, "falloff": "smooth",
    }))
    add(node("ground", "collider", {"collider_type": "plane", "normal": [0.0, 1.0, 0.0], "bounce": 0.1,
                                    "friction": 0.6}))

    # ---------------- ground circle (phase 1 + top view) ----------------
    add(node("d_pool", "decal", {
        "shape": "circle", "size": [5.0, 5.0], "position": [0.0, 0.0, 0.0],
        "color": c(mid), "emissive": round(0.85 * g, 3), "blend": "additive", "fade_in": 0.05, "fade_out": 0.2,
        "opacity": tr([(0.0, 0.0), (0.09, 0.16), (0.24, 0.24), (0.55, 0.32), (1.45, 0.34),
                       (1.55, 0.5), (1.76, 0.18), (1.95, 0.0)]),
    }, layer="ground", inputs={"texture": "tex_pool", "material": "mat_pool"}))
    add(node("d_rings", "decal", {
        "shape": "circle", "size": tr([(0.0, [1.0, 1.0]), (0.06, [2.5, 2.5]), (0.2, [3.32, 3.32]),
                                       (1.5, [3.32, 3.32]), (1.62, [3.46, 3.46]), (1.86, [2.9, 2.9])]),
        "position": [0.0, 0.0, 0.0],
        "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (2.0, [0.0, 26.0, 0.0])]),
        "color": c(mix(mid, hot, 0.55)), "emissive": round(2.0 * g, 3), "blend": "additive",
        "fade_in": 0.04, "fade_out": 0.16,
        "opacity": tr([(0.0, 0.0), (0.07, 0.75), (0.2, 0.95), (1.45, 0.9), (1.56, 1.0),
                       (1.74, 0.42), (1.94, 0.0)]),
    }, layer="ground", inputs={"texture": "tex_ground_rings", "material": "mat_rune"}))
    add(node("d_runes", "decal", {
        "shape": "circle", "size": [3.32, 3.32], "position": [0.0, 0.0, 0.0],
        "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (2.0, [0.0, -62.0 if angular else -34.0, 0.0])]),
        "color": c(hot), "emissive": round(2.3 * g, 3), "blend": "additive", "fade_in": 0.05, "fade_out": 0.16,
        "opacity": tr([(0.0, 0.0), (0.1, 0.5), (0.26, 0.92), (1.45, 0.88), (1.56, 1.0),
                       (1.72, 0.36), (1.92, 0.0)]),
    }, layer="ground", inputs={"texture": "tex_ground_runes", "material": "mat_rune"}))

    # ---------------- vertical light shaft (phase 1 and the close) ----------------
    # a bead rising out of the rune circle; its trail is the shaft.  The taper is
    # widest at the oldest end (the ground) and goes to zero at the head, so the
    # shaft is a soft cone of light, never a constant-width rod, and the emissive
    # stays low enough that the cross-width gradient survives the bloom.
    shaft_specs = [
        # (x, z, width scale, emissive scale, rise end, top, colour mix)
        (0.0, 0.0, 1.0, 1.0, 0.42, 2.75, 0.4),
        (0.06, -0.04, 0.44, 1.15, 0.34, 2.25, 0.15),
    ]
    for idx, (bx, bz, w_scale, e_scale, t_end, top, cmix) in enumerate(shaft_specs, start=1):
        keys = [(0.0, [bx, 0.015, bz])]
        for k in range(1, 13):
            u = k / 12.0
            keys.append((round(0.02 + (t_end - 0.02) * u, 4),
                         [bx, round(0.015 + (top - 0.015) * (u ** 0.72), 4), bz]))
        keys.append((2.0, [bx, round(top + 0.3, 3), bz]))
        add(node(f"shaft_bead{idx}", "mesh", {
            "primitive": "sphere", "radius": 0.008, "visible": False,
            "position": tr(keys),
        }, layer="shaft"))
        add(node(f"shaft_trail{idx}", "trail", {
            "lifetime": 1.0, "max_segments": 240, "min_vertex_distance": 0.015,
            "taper": [[0.0, 0.0], [0.05, 0.3], [0.14, 0.78], [0.3, 1.0], [0.72, 0.98], [1.0, 0.82]],
            "opacity_over_life": [[0.0, 0.9], [0.35, 1.0], [0.8, 0.85], [1.0, 0.7]],
            "color": c(mix(mid, hot, cmix)), "blend": "additive",
            "noise_amplitude": 0.07, "noise_frequency": 0.75,
            "width": env([(0.0, 0.0), (0.06, 0.4), (0.2, 0.78), (0.4, 0.88), (0.58, 0.6),
                          (0.72, 0.0), (2.0, 0.0)], w_scale),
            "emissive": env([(0.0, 0.16), (0.1, 0.38), (0.3, 0.35), (0.5, 0.22), (0.7, 0.0),
                             (2.0, 0.0)], e_scale * gh),
        }, layer="shaft", inputs={"source": f"shaft_bead{idx}", "material": "mat_shaft"}))

    # ---------------- the vertical portal ring ----------------
    # hub tilted 90 deg about X: its local XZ plane is the world XY plane, so the
    # beads orbit in a vertical circle standing on the ground rune circle.
    add(node("ring_hub", "mesh", {
        "primitive": "sphere", "radius": 0.008, "visible": False,
        "position": [0.0, PORTAL_Y, 0.0], "rotation": [90.0, 0.0, 0.0],
    }, layer="rings"))

    # Each strand is a spin node (orbit angle + radius envelope) carrying N beads
    # spread evenly around the circle.  A trail lives one bead-spacing, so the N
    # arcs tile a complete circle and the ribbon holds no history from the
    # opening spiral once the portal is open.
    # strand -> (revs/s, direction, bead radius, bead ids)
    strands = {
        "a": (1.55, 1.0, R_RING + 0.048, ["bead_a1", "bead_a2"]),
        "b": (1.30, -1.0, R_RING - 0.062, ["bead_b1", "bead_b2"]),
        "c": (1.05, 1.0, R_RING - 0.004, ["bead_c1", "bead_c2", "bead_c3"]),
        "d": (0.85 if angular else 0.85, -1.0, (R_RING + 0.195) if angular else (R_RING + 0.100),
              ["bead_d1", "bead_d2", "bead_d3"]),
        "glyph": (1.00, 1.0, R_RING - 0.006, ["glyph_bead1", "glyph_bead2", "glyph_bead3"]),
    }
    for key, (revs, direction, radius, beads) in strands.items():
        add(node(f"spin_{key}", "mesh", {
            "primitive": "sphere", "radius": 0.008, "visible": False,
            "rotation": angle_track(revs, direction),
            "scale": vec_env(RADIUS_ENVELOPE, [1.0, 1.0, 1.0]),
        }, layer="rings", parent="ring_hub"))
        for i, bid in enumerate(beads):
            ang = math.radians(360.0 * i / len(beads))
            add(node(bid, "mesh", {
                "primitive": "sphere", "radius": 0.018 if key == "a" else 0.008,
                "segments": 10, "visible": key == "a",
                "position": [round(radius * math.cos(ang), 4), 0.0, round(radius * math.sin(ang), 4)],
                "start_time": 0.17,
                "emissive": tr([(0.0, 0.0), (0.22, 1.2), (0.55, 3.2), (1.45, 3.0),
                                (1.58, 4.6), (1.84, 0.0)]),
            }, layer="rings", parent=f"spin_{key}", inputs={"material": "mat_spark"}))

    def arc_life(revs: float, n: int) -> float:
        """One bead spacing plus a 6% overlap so the arcs close without a seam."""
        return round(1.06 / (revs * n), 4)

    # two bright strands: the sheet's "bright double ring"
    ring_trails = [
        ("trail_a1", "bead_a1", arc_life(1.55, 2), 0.050, 2.3, mix(hot, [1, 1, 1], 0.4), 0.010),
        ("trail_a2", "bead_a2", arc_life(1.55, 2), 0.050, 2.3, mix(hot, [1, 1, 1], 0.4), 0.010),
        ("trail_b1", "bead_b1", arc_life(1.30, 2), 0.036, 1.9, mix(hot, mid, 0.35), 0.009),
        ("trail_b2", "bead_b2", arc_life(1.30, 2), 0.036, 1.9, mix(hot, mid, 0.35), 0.009),
    ]
    for tid, src, life, width, emis, col, noise in ring_trails:
        add(node(tid, "trail", {
            "lifetime": life, "max_segments": 200, "min_vertex_distance": 0.014,
            "taper": [[0.0, 0.0], [0.035, 1.0], [0.94, 1.0], [1.0, 0.0]],
            "opacity_over_life": [[0.0, 1.0], [0.9, 1.0], [1.0, 0.0]],
            "color": col, "blend": "additive",
            "noise_amplitude": noise, "noise_frequency": 1.6, "start_time": 0.17,
            "width": tr([(0.17, 0.0), (0.24, width * 0.55), (0.5, width), (1.45, width),
                         (1.55, width * 1.45), (1.76, width * 0.7), (1.86, 0.0)]),
            "emissive": env([(0.17, 0.5), (0.3, emis * 0.7), (0.55, emis), (1.45, emis * 0.94),
                             (1.55, emis * 1.5), (1.78, emis * 0.4), (1.88, 0.0)], gh),
        }, layer="rings", inputs={"source": src, "material": "mat_ring"}))

    # the broad soft halo that keeps the rings from reading as hard lines
    for i in (1, 2, 3):
        add(node(f"trail_c{i}", "trail", {
            "lifetime": arc_life(1.05, 3), "max_segments": 200, "min_vertex_distance": 0.02,
            "taper": [[0.0, 0.0], [0.05, 1.0], [0.93, 1.0], [1.0, 0.0]],
            "opacity_over_life": [[0.0, 0.3], [0.9, 0.3], [1.0, 0.0]],
            "color": c(mid), "blend": "additive", "start_time": 0.32,
            "width": tr([(0.32, 0.0), (0.42, 0.14), (0.58, 0.32), (1.45, 0.32), (1.55, 0.44),
                         (1.78, 0.14), (1.87, 0.0)]),
            "emissive": env([(0.32, 0.1), (0.58, 0.8), (1.45, 0.78), (1.55, 1.3), (1.85, 0.0)], g),
        }, layer="rings", inputs={"source": f"bead_c{i}", "material": "mat_ribbon"}))

    # strand d: base -> a wide soft outer bloom ring; style 2 -> rotating dashes
    d_life = 0.2 if angular else arc_life(0.85, 3)
    d_width = 0.055 if angular else 0.17
    d_emis = 3.2 if angular else 0.6
    d_opacity = ([[0.0, 1.0], [0.5, 0.85], [1.0, 0.0]] if angular
                 else [[0.0, 0.26], [0.92, 0.26], [1.0, 0.0]])
    for i in (1, 2, 3):
        add(node(f"trail_d{i}", "trail", {
            "lifetime": d_life, "max_segments": 200, "min_vertex_distance": 0.014,
            "taper": ([[0.0, 0.0], [0.12, 1.0], [0.45, 0.9], [1.0, 0.0]] if angular
                      else [[0.0, 0.0], [0.05, 1.0], [0.93, 1.0], [1.0, 0.0]]),
            "opacity_over_life": d_opacity,
            "color": c(mix(mid, hot, 0.55 if angular else 0.1)), "blend": "additive",
            "start_time": 0.3,
            "width": tr([(0.3, 0.0), (0.4, d_width * 0.5), (0.56, d_width), (1.45, d_width),
                         (1.55, d_width * 1.4), (1.78, d_width * 0.5), (1.87, 0.0)]),
            "emissive": env([(0.3, 0.1), (0.56, d_emis), (1.45, d_emis), (1.55, d_emis * 1.4),
                             (1.84, 0.0)], gh),
        }, layer="rings", inputs={"source": f"bead_d{i}", "material": "mat_ring"}))

    # ---------------- glyph band on the portal ring ----------------
    add(node("ps_glyph", "particle_system", {
        "max_particles": 400, "lifetime": 0.34, "lifetime_variance": 0.08,
        "size": 0.15 if angular else 0.145, "size_variance": 0.035,
        "size_over_life": [[0.0, 0.4], [0.2, 1.0], [0.8, 1.0], [1.0, 0.5]],
        "color": c(hot), "color_over_life": [[0.0, c(hot, 1.0)], [0.5, c(mix(hot, mid, 0.4))],
                                             [1.0, c(mid, 1.0, 0.7)]],
        "opacity": 1.0, "opacity_over_life": [[0.0, 0.0], [0.16, 1.0], [0.72, 0.85], [1.0, 0.0]],
        "emissive": round(3.6 * gh, 3), "render_mode": "billboard", "blend": "additive",
        "rotation_variance": 180.0, "sprite_columns": 6, "sprite_rows": 1, "sprite_fps": 0.0,
        "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_glyph", "material": "mat_glyph"}))
    for i in (1, 2, 3):
        add(node(f"e_glyph{i}", "emitter", {
            "shape": "point", "velocity": 0.0, "start_time": 0.24,
            "rate": tr([(0.24, 0.0), (0.34, 16.0), (0.55, 36.0), (1.42, 36.0), (1.5, 0.0)]),
        }, layer="glyphs", parent=f"glyph_bead{i}", inputs={"particle": "ps_glyph"}))

    # ---------------- portal core: a thin VERTICAL procedural volume ----------------
    add(node("core_volume", "volume", {
        "mode": "procedural", "volume_type": "magic", "shape": "nebula",
        "position": [0.0, PORTAL_Y, 0.0], "rotation": [90.0, 0.0, 0.0],
        "radius": tr([(0.0, 0.05), (0.2, 0.12), (0.3, 0.46), (0.38, 0.84), (0.46, 1.03),
                      (0.54, 0.99), (1.46, 0.99), (1.55, 1.04), (1.64, 0.7), (1.74, 0.28),
                      (1.83, 0.04)]),
        "height": 0.52,
        # a deep nebula, not a lamp: low density and emission so the filaments,
        # the carved holes and the dark shell all survive the bloom
        "density": tr([(0.0, 0.0), (0.26, 0.0), (0.38, 0.6), (0.54, 1.5), (0.8, 1.8),
                       (1.45, 1.75), (1.55, 2.6), (1.7, 1.2), (1.82, 0.0)]),
        "emission": env([(0.0, 1.8), (0.6, 2.4), (1.45, 2.4), (1.55, 4.0), (1.8, 0.9)], g),
        "color": c(deep, 1.0, 0.8), "color_hot": c(core_hot, 1.0, 0.9),
        "filament_scale": 4.4, "strands": 0.85, "carve": 0.62, "softness": 0.45,
        "spiral_arms": 3, "arm_sharpness": 2.0,
        "spin": tr([(0.0, 0.4), (0.6, 0.26), (1.46, 0.26), (1.9, 1.3)]),
                # A/B'd in the GPU viewer: 12 steps is visually identical to 30 here (the
        # disc is only ~0.5 m deep) and costs 11 fps less - 60 fps instead of 49.
        "twist": 0.0, "climb": 0.09, "scatter": 0.36, "march_steps": 14,
    }, layer="core"))

    # stars in front of the nebula core
    add(node("ps_star", "particle_system", {
        "max_particles": 320, "lifetime": 1.0, "lifetime_variance": 0.55,
        "size": 0.036, "size_variance": 0.026,
        "size_over_life": [[0.0, 0.2], [0.3, 1.0], [0.7, 0.9], [1.0, 0.1]],
        "color": c(mix(core_hot, [1.0, 1.0, 1.0], 0.45)),
        "color_over_life": [[0.0, c(hot)], [0.5, c(mix(core_hot, hot, 0.5))], [1.0, c(mid)]],
        "opacity_over_life": [[0.0, 0.0], [0.18, 1.0], [0.45, 0.45], [0.62, 1.0], [1.0, 0.0]],
        "emissive": round(5.4 * gh, 3), "render_mode": "billboard", "blend": "additive", "drag": 2.0,
        "soft_particle_distance": 0.04,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark"}))
    add(node("e_star", "emitter", {
        "shape": "disc", "radius": 0.94, "position": [0.0, PORTAL_Y, 0.06],
        "rotation": [90.0, 0.0, 0.0], "velocity": 0.12, "velocity_variance": 0.1,
        "direction": [0.0, 0.0, 0.0], "start_time": 0.4,
        "rate": tr([(0.4, 0.0), (0.55, 150.0), (0.8, 210.0), (1.45, 210.0), (1.6, 60.0), (1.72, 0.0)]),
        "scale": tr([(0.4, [0.35, 0.35, 0.35]), (0.58, [1.0, 1.0, 1.0]), (1.5, [1.0, 1.0, 1.0]),
                     (1.72, [0.35, 0.35, 0.35])]),
    }, layer="core", inputs={"particle": "ps_star"}))

    # ---------------- energy swirls (opening) and the closing spiral ----------------
    for i in (1, 2, 3):
        phase = 120.0 * (i - 1)
        add(node(f"swirl_bead{i}", "mesh", {
            "primitive": "sphere", "radius": 0.008, "visible": False,
            "position": helix(0.12, 0.66, 1.3, 1.16, 0.46, 0.04, 2.45, phase, hold_after=2.0),
        }, layer="swirl"))
        add(node(f"swirl_trail{i}", "trail", {
            "lifetime": 0.4, "max_segments": 200, "min_vertex_distance": 0.018,
            "taper": [[0.0, 0.0], [0.08, 0.55], [0.22, 1.0], [0.6, 0.78], [0.88, 0.32], [1.0, 0.0]],
            "opacity_over_life": [[0.0, 1.0], [0.45, 0.9], [0.85, 0.4], [1.0, 0.0]],
            "color": c(mix(hot, accent, 0.3 if i == 2 else 0.0)), "blend": "additive",
            "noise_amplitude": 0.03, "noise_frequency": 1.1,
            "start_time": 0.12, "duration": 0.62,
            "width": tr([(0.12, 0.0), (0.2, 0.1), (0.38, 0.135), (0.55, 0.09), (0.66, 0.0)]),
            "emissive": env([(0.12, 0.7), (0.28, 1.5), (0.5, 1.0), (0.68, 0.0)], gh),
        }, layer="swirl", inputs={"source": f"swirl_bead{i}", "material": "mat_ribbon"}))

        add(node(f"spiral_bead{i}", "mesh", {
            "primitive": "sphere", "radius": 0.008, "visible": False,
            "position": helix(1.50, 1.90, 1.45, 1.25, 0.04, 0.05, 2.5, phase + 40.0,
                              y_gamma=0.8, hold_after=2.0),
        }, layer="swirl"))
        add(node(f"spiral_trail{i}", "trail", {
            "lifetime": 0.46, "max_segments": 200, "min_vertex_distance": 0.016,
            "taper": [[0.0, 0.0], [0.09, 0.5], [0.24, 1.0], [0.62, 0.76], [0.9, 0.28], [1.0, 0.0]],
            "opacity_over_life": [[0.0, 1.0], [0.5, 0.85], [1.0, 0.0]],
            "color": c(mix(hot, [1.0, 1.0, 1.0], 0.25)), "blend": "additive",
            "noise_amplitude": 0.026, "noise_frequency": 1.3,
            "start_time": 1.5, "duration": 0.42,
            "width": tr([(1.5, 0.0), (1.57, 0.1), (1.7, 0.08), (1.82, 0.03), (1.9, 0.0)]),
            "emissive": env([(1.5, 0.9), (1.58, 2.2), (1.74, 1.2), (1.9, 0.0)], gh),
        }, layer="swirl", inputs={"source": f"spiral_bead{i}", "material": "mat_ribbon"}))

    # ---------------- crystal shards ----------------
    add(node("shard_mesh", "mesh", {
        "primitive": "crystal", "radius": 0.5, "height": 1.0, "segments": 6,
        "variants": 8, "irregularity": 0.7, "visible": False,
    }))
    add(node("flake_mesh", "mesh", {
        "primitive": "shard", "radius": 0.5, "height": 1.0, "segments": 5,
        "variants": 6, "irregularity": 0.55, "visible": False,
    }))
    add(node("ps_shard", "particle_system", {
        "max_particles": 260, "lifetime": 1.25, "lifetime_variance": 0.4,
        "size": 0.2, "size_variance": 0.11,
        "size_over_life": [[0.0, 0.05], [0.13, 1.05], [0.2, 1.0], [0.82, 0.95], [1.0, 0.0]],
        "color": c(mix(mid, p["shard"], 0.12)), "opacity": 0.78, "emissive": 0.7,
        "render_mode": "mesh", "blend": "alpha", "orientation": "tumble",
        "angular_velocity": 62.0, "angular_velocity_variance": 55.0,
        "mesh_scale": [0.56, 1.8, 0.56], "mesh_scale_variance": [0.18, 0.8, 0.18],
        "drag": 0.9, "sort": True,
    }, inputs={"mesh": "shard_mesh", "material": "mat_shard",
               "forces": ["f_vortex", "f_lift", "f_drift"], "colliders": ["ground"]}))
    add(node("ps_flake", "particle_system", {
        "max_particles": 220, "lifetime": 1.4, "lifetime_variance": 0.5,
        "size": 0.085, "size_variance": 0.04,
        "size_over_life": [[0.0, 0.05], [0.14, 1.0], [0.85, 0.92], [1.0, 0.0]],
        "color": c(mix(mid, p["shard"], 0.25)), "opacity": 0.55, "emissive": 0.95,
        "render_mode": "mesh", "blend": "alpha", "orientation": "tumble",
        "angular_velocity": 110.0, "angular_velocity_variance": 90.0,
        "mesh_scale": [1.0, 1.35, 1.0], "mesh_scale_variance": [0.3, 0.5, 0.3],
        "drag": 1.1, "sort": True,
    }, inputs={"mesh": "flake_mesh", "material": "mat_shard",
               "forces": ["f_vortex", "f_lift", "f_drift"], "colliders": ["ground"]}))
    add(node("e_shard_ring", "emitter", {
        "shape": "ring", "radius": 2.3, "inner_radius": 1.2, "position": [0.0, 0.14, 0.0],
        "velocity": 1.5, "velocity_variance": 0.75, "direction": [0.0, 1.0, 0.0], "spread": 20.0,
        "rate": tr([(0.0, 0.0), (0.05, 17.0), (0.3, 22.0), (1.45, 19.0), (1.56, 24.0), (1.66, 0.0)]),
    }, layer="shards", inputs={"particle": "ps_shard"}))
    add(node("e_shard_near", "emitter", {
        "shape": "ring", "radius": 1.75, "inner_radius": 0.9, "position": [0.0, 1.0, 0.0],
        "velocity": 0.85, "velocity_variance": 0.6, "direction": [0.0, 1.0, 0.0], "spread": 40.0,
        "rate": tr([(0.0, 0.0), (0.24, 9.0), (0.6, 14.0), (1.45, 14.0), (1.56, 17.0), (1.66, 0.0)]),
    }, layer="shards", inputs={"particle": "ps_shard"}))
    add(node("e_flake", "emitter", {
        "shape": "hemisphere", "radius": 2.6, "position": [0.0, 0.15, 0.0], "surface_only": False,
        "velocity": 0.8, "velocity_variance": 0.6, "direction": [0.0, 1.0, 0.0], "spread": 55.0,
        "rate": tr([(0.0, 0.0), (0.1, 8.0), (0.6, 13.0), (1.45, 12.0), (1.58, 18.0), (1.68, 0.0)]),
    }, layer="shards", inputs={"particle": "ps_flake"}))

    # ---------------- motes, embers, mist ----------------
    add(node("ps_mote", "particle_system", {
        "max_particles": 1400, "lifetime": 1.1, "lifetime_variance": 0.6,
        "size": 0.05, "size_variance": 0.035,
        "size_over_life": [[0.0, 0.25], [0.25, 1.0], [0.75, 0.8], [1.0, 0.0]],
        "color": c(hot), "color_over_life": [[0.0, c(hot)], [0.45, c(mix(hot, mid, 0.6))],
                                             [1.0, c(mid, 1.0, 0.5)]],
        "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.7, 0.7], [1.0, 0.0]],
        "emissive": round(4.2 * gh, 3), "render_mode": "stretched_billboard", "velocity_stretch": 0.09,
        "blend": "additive", "drag": 1.6, "soft_particle_distance": 0.06,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark",
               "forces": ["f_curl", "f_lift", "f_pull"]}))
    add(node("e_mote", "emitter", {
        "shape": "ring", "radius": 1.85, "inner_radius": 0.7, "position": [0.0, 0.05, 0.0],
        "velocity": 1.5, "velocity_variance": 1.1, "direction": [0.0, 1.0, 0.0], "spread": 22.0,
        "rate": tr([(0.0, 0.0), (0.05, 220.0), (0.3, 320.0), (0.55, 260.0), (1.45, 230.0),
                    (1.56, 520.0), (1.75, 90.0), (1.95, 0.0)]),
    }, layer="motes", inputs={"particle": "ps_mote"}))

    add(node("ps_ember", "particle_system", {
        "max_particles": 900, "lifetime": 0.85, "lifetime_variance": 0.35,
        "size": 0.055, "size_variance": 0.03,
        "size_over_life": [[0.0, 0.3], [0.3, 1.0], [1.0, 0.15]],
        "color": c(mix(hot, accent, 0.55)),
        "color_over_life": [[0.0, c(mix(hot, accent, 0.3))], [0.6, c(accent)], [1.0, c(mid, 1.0, 0.4)]],
        "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.65, 0.8], [1.0, 0.0]],
        "emissive": round(4.0 * gh, 3), "render_mode": "stretched_billboard", "velocity_stretch": 0.14,
        "blend": "additive", "drag": 2.4, "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_curl", "f_pull"]}))
    add(node("e_ember", "emitter", {
        "shape": "ring", "radius": 1.16, "inner_radius": 1.0, "position": [0.0, PORTAL_Y, 0.0],
        "rotation": [90.0, 0.0, 0.0], "velocity": 0.9, "velocity_variance": 0.8,
        "direction": [0.0, 0.0, 0.0], "start_time": 0.3,
        "rate": tr([(0.3, 0.0), (0.45, 120.0), (0.62, 200.0), (1.45, 180.0), (1.56, 260.0),
                    (1.7, 40.0), (1.86, 0.0)]),
        "scale": vec_env(RADIUS_ENVELOPE, [1.0, 1.0, 1.0]),
    }, layer="motes", inputs={"particle": "ps_ember"}))

    add(node("ps_mist", "particle_system", {
        "max_particles": 260, "lifetime": 2.0, "lifetime_variance": 0.7,
        "size": 1.35, "size_variance": 0.55,
        "size_over_life": [[0.0, 0.45], [0.5, 1.0], [1.0, 1.5]],
        "color": c(p["mist"]), "color_over_life": [[0.0, c(p["mist"])], [0.5, c(mix(p["mist"], mid, 0.4))],
                                                   [1.0, c(deep)]],
        "opacity": 0.4, "opacity_over_life": [[0.0, 0.0], [0.28, 1.0], [0.7, 0.7], [1.0, 0.0]],
        "emissive": 0.25, "render_mode": "billboard", "blend": "alpha", "drag": 1.1,
        "sort": True, "rotation_variance": 180.0, "angular_velocity": 8.0,
        "angular_velocity_variance": 14.0, "soft_particle_distance": 0.4,
    }, inputs={"sprite": "tex_puff", "material": "mat_mist", "forces": ["f_drift"]}))
    add(node("e_mist", "emitter", {
        "shape": "ring", "radius": 2.9, "inner_radius": 1.2, "position": [0.0, 0.16, 0.0],
        "velocity": 0.28, "velocity_variance": 0.22, "direction": [0.0, 0.25, 0.0], "spread": 60.0,
        "rate": tr([(0.0, 20.0), (0.35, 34.0), (1.45, 30.0), (1.6, 48.0), (1.9, 6.0)]),
    }, layer="mist", inputs={"particle": "ps_mist"}))

    # soft halo around the portal - pure gradient, so the ring never reads as a
    # hard line against the background
    add(node("ps_aura", "particle_system", {
        "max_particles": 90, "lifetime": 0.9, "lifetime_variance": 0.3,
        "size": 2.9, "size_variance": 0.5,
        "size_over_life": [[0.0, 0.6], [0.35, 1.0], [1.0, 1.1]],
        "color": c(mid), "color_over_life": [[0.0, c(mix(mid, hot, 0.2))], [1.0, c(deep)]],
        "opacity": 0.035, "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": 0.55, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.5,
    }, inputs={"sprite": "tex_glow", "material": "mat_pool"}))
    add(node("e_aura", "emitter", {
        "shape": "sphere", "radius": 0.22, "position": [0.0, PORTAL_Y, -0.12], "velocity": 0.0,
        "start_time": 0.22,
        "rate": tr([(0.22, 0.0), (0.4, 8.0), (0.6, 14.0), (1.45, 14.0), (1.55, 26.0),
                    (1.72, 5.0), (1.86, 0.0)]),
        "scale": vec_env(RADIUS_ENVELOPE, [1.0, 1.0, 1.0]),
    }, layer="core", inputs={"particle": "ps_aura"}))

    # ---------------- closing burst ----------------
    add(node("ps_burst", "particle_system", {
        "max_particles": 700, "lifetime": 0.34, "lifetime_variance": 0.16,
        "size": 0.055, "size_variance": 0.03,
        "size_over_life": [[0.0, 0.5], [0.2, 1.0], [1.0, 0.05]],
        "color": c(mix(hot, [1.0, 1.0, 1.0], 0.4)),
        "color_over_life": [[0.0, c(hot)], [0.45, c(mid)], [1.0, c(mid, 1.0, 0.3)]],
        "opacity_over_life": [[0.0, 1.0], [0.6, 0.8], [1.0, 0.0]],
        "emissive": round(3.8 * gh, 3), "render_mode": "stretched_billboard", "velocity_stretch": 0.16,
        "blend": "additive", "drag": 6.0, "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_curl"]}))
    add(node("e_burst", "emitter", {
        "shape": "ring", "radius": 0.34, "inner_radius": 0.0, "position": [0.0, PORTAL_Y, 0.0],
        "rotation": [90.0, 0.0, 0.0], "rate": 0.0, "burst_count": 130, "burst_times": [0.0, 0.08],
        "velocity": 5.4, "velocity_variance": 2.6, "direction": [0.0, 0.0, 0.0],
        "start_time": 1.55, "duration": 0.2,
    }, layer="burst", inputs={"particle": "ps_burst"}))

    # ---------------- lights ----------------
    add(node("portal_light", "light", {
        "light_type": "point", "position": [0.0, PORTAL_Y, 0.35], "color": c(p["light"]),
        "radius": 5.0, "flicker_amplitude": 0.1, "flicker_frequency": 9.0,
        "intensity": env([(0.0, 0.0), (0.12, 1.4), (0.3, 3.2), (0.55, 8.5), (1.0, 9.5),
                          (1.45, 8.5), (1.55, 16.0), (1.72, 5.0), (1.92, 0.0)], g),
    }, layer="light"))
    add(node("rim_light", "light", {
        "light_type": "point", "position": [0.0, 0.35, 1.5], "color": c(mix(mid, hot, 0.4)),
        "radius": 5.0,
        "intensity": env([(0.0, 0.0), (0.2, 1.0), (0.6, 2.2), (1.45, 2.1), (1.55, 4.0), (1.9, 0.0)], g),
    }, layer="light"))

    # ---------------- camera ----------------
    add(node("cam", "camera", {"position": [0.0, 1.6, 6.0], "target": [0.0, 1.25, 0.0], "fov": 40.0}))

    return {
        "schema_version": "0.1.0",
        "name": spec["name"],
        "description": spec["description"],
        "duration": DURATION,
        "seed": seed,
        "timeline": {"phases": [{"name": n, "start": s, "end": e} for n, s, e in PHASES]},
        "layers": [
            {"id": "ground", "name": "Ground circle", "role": "telegraph"},
            {"id": "shaft", "name": "Light shaft", "role": "ignition"},
            {"id": "rings", "name": "Portal rings", "role": "primary"},
            {"id": "glyphs", "name": "Glyph band", "role": "primary"},
            {"id": "core", "name": "Portal core", "role": "primary"},
            {"id": "swirl", "name": "Energy swirls", "role": "secondary"},
            {"id": "shards", "name": "Crystal shards", "role": "secondary"},
            {"id": "motes", "name": "Motes and embers", "role": "secondary"},
            {"id": "mist", "name": "Ground mist", "role": "aftermath"},
            {"id": "burst", "name": "Closing burst", "role": "aftermath"},
            {"id": "light", "name": "Lighting", "role": "interaction"},
        ],
        "nodes": nodes,
        "metadata": {
            "reference": "out/references/teleport_sheet.jpg",
            "generator": "tools/generators/teleport_variants.py",
            "variant": slug,
            "rune_language": style,
            "analysis": "initiate 0-0.2 ground rune circle ignites and a soft shaft rises | "
                        "opening 0.2-0.5 ribbons spiral up and the ring strands spiral out from zero | "
                        "open 0.5-1.5 a 2.2 m vertical double ring with a glyph band stands on the "
                        "circle over a starry nebula core | closing 1.5-1.8 the ring collapses into a "
                        "tightening spiral with a radial burst | fade 1.8-2.0 shards and motes fade",
            "render_settings": {
                "background": p["bg"],
                "ground_albedo": 0.06,
                "bloom_intensity": 0.22,
                "bloom_radius": 0.045,
                "exposure": 0.85,
                "grid": False,
            },
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="directory to write the four effect JSON files into")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for slug, spec in VARIANTS.items():
        effect = build(slug, spec)
        path = out / f"{slug}.json"
        path.write_text(json.dumps(effect, indent=1) + "\n", encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes)")


if __name__ == "__main__":
    main()
