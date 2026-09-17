#!/usr/bin/env python3
"""Generate the eight Shield Buff effects from one parametrised description.

    python tools/generators/shield_variants.py --out examples/effects

Every variant shares the same node graph - identical ids, identical animation,
identical controls - and differs only through the VARIANTS table: a palette that
reaches every colour (decals, particles, meshes, trails, lights, materials), a
style switch (`basic` | `rune`) and one cheap elemental accent layer.  A variant
that does not use a feature (the rune band, the accent) keeps its nodes and
disables them, so game code can drive all eight through the same handles.

How the barrier is built
------------------------
A sphere looks the same from every direction, so the barrier is a stack of three
camera-facing quads, one long-lived particle each: a fresnel body (dark centre,
bright rim, crisp silhouette, soft halo), a front shell of cell seams and a
dimmer, finer back shell.  The seams are a flat `cracks` pattern pushed through a
spherical bulge (`distort` by a packed offset field: big cells in the middle,
cells squeezed against the rim), and the two shells roll slowly in opposite
directions about the view axis, which is what a turning glass ball does.  The
quads are born on the ground with an upward velocity and a drag, and their size
curve is that same exponential, so the sphere grows up out of the ring with its
base on the ground.  `barrier_size` scales the size and the launch speed
together, which keeps it standing on the ground at every size.

The emblem is the `shape` texture op (heater shield: outline, inset face, inner
line, centre ridge) on a quad that rides up with the sphere.  The comet swirl and
the rune band use the parent-chain technique from examples/effects/arcane_aoe.json
and tools/generators/teleport_variants.py: a tilted hub, a child whose Y rotation
is keyframed, and beads at a fixed local offset that carry ribbons and emitters.

On dissipate the intact shells cross-fade into copies whose material dissolves,
while glowing flakes and sparks leave the surface.

House style: no crosses, plus signs or four-armed stars anywhere (the emblem is a
shield, the rune seals are hexagons); every ribbon has a soft cross-width texture
and a taper; the sheet's vertical light lines are soft, short-lived, tapered
streaks.  The output is deterministic: no RNG, no wall clock, no absolute paths.
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

GOLD = {
    "hot": [1.00, 0.93, 0.74],      # seams, emblem outline, spark cores (near-white, tinted)
    "mid": [1.00, 0.60, 0.13],      # the signature hue: barrier body, rings, ribbons
    "deep": [0.36, 0.13, 0.012],    # halo falloff, dying sparks
    "accent": [1.00, 0.40, 0.06],   # secondary hue
    "light": [1.00, 0.70, 0.30],    # point lights
    "shard": [0.78, 0.47, 0.16],    # crystal albedo
    "shard_glow": [1.00, 0.62, 0.18],
    "mist": [0.52, 0.34, 0.14],
}

ICE = {
    "hot": [0.86, 0.97, 1.00],
    "mid": [0.20, 0.58, 1.00],
    "deep": [0.02, 0.09, 0.40],
    "accent": [0.42, 0.92, 1.00],
    "light": [0.40, 0.72, 1.00],
    "shard": [0.80, 0.93, 1.00],    # bright albedo -> the viewer's glass path: ice, not amber
    "shard_glow": [0.55, 0.85, 1.00],
    "mist": [0.30, 0.46, 0.66],
}

FIRE = {
    "hot": [1.00, 0.84, 0.58],
    "mid": [1.00, 0.19, 0.045],
    "deep": [0.34, 0.018, 0.0],
    "accent": [1.00, 0.46, 0.06],
    "light": [1.00, 0.34, 0.11],
    "shard": [0.70, 0.20, 0.08],
    "shard_glow": [1.00, 0.30, 0.08],
    "mist": [0.50, 0.20, 0.10],
}

POISON = {
    "hot": [0.84, 1.00, 0.68],
    "mid": [0.28, 0.95, 0.15],
    "deep": [0.015, 0.22, 0.03],
    "accent": [0.72, 1.00, 0.14],
    "light": [0.38, 1.00, 0.28],
    "shard": [0.30, 0.66, 0.20],
    "shard_glow": [0.42, 1.00, 0.24],
    "mist": [0.17, 0.42, 0.13],
}

#: The one table.  slug -> display name, palette, style, elemental accent, seed.
VARIANTS: dict[str, dict[str, Any]] = {
    "shield_buff": {
        "name": "Shield Buff",
        "description": "A golden defense buff: ground rings ignite, a cellular energy sphere rises around "
                       "the target with a heater-shield emblem inside, comets and crystal shards circle "
                       "it, then the shell breaks into fragments.",
        "palette": GOLD, "style": "basic", "accent": None, "seed": 61801,
        "tags": ["buff", "shield", "defense", "support", "target"],
    },
    "shield_buff_rune": {
        "name": "Shield Buff Rune",
        "description": "The golden defense buff in rune style: a glyph ring on the ground circle, hexagonal "
                       "seals in the rings and a rune band orbiting the sphere's equator.",
        "palette": GOLD, "style": "rune", "accent": None, "seed": 61802,
        "tags": ["buff", "shield", "defense", "support", "target", "rune"],
    },
    "shield_buff_ice": {
        "name": "Shield Buff Ice",
        "description": "Ice resistance buff: the defense sphere in glacial blue with cyan-white seams, "
                       "ice-crystal shards and frost sparkles drifting down.",
        "palette": ICE, "style": "basic", "accent": "ice", "seed": 61803,
        "tags": ["buff", "shield", "defense", "support", "target", "ice"],
    },
    "shield_buff_ice_rune": {
        "name": "Shield Buff Ice Rune",
        "description": "Ice resistance buff in rune style: the glacial blue sphere with a glyph ring, "
                       "hexagonal seals, an equator rune band and drifting frost sparkles.",
        "palette": ICE, "style": "rune", "accent": "ice", "seed": 61804,
        "tags": ["buff", "shield", "defense", "support", "target", "ice", "rune"],
    },
    "shield_buff_fire": {
        "name": "Shield Buff Fire",
        "description": "Fire resistance buff: the defense sphere in ember red and orange with rising, "
                       "curling embers and a flickering hearth light.",
        "palette": FIRE, "style": "basic", "accent": "fire", "seed": 61805,
        "tags": ["buff", "shield", "defense", "support", "target", "fire"],
    },
    "shield_buff_fire_rune": {
        "name": "Shield Buff Fire Rune",
        "description": "Fire resistance buff in rune style: the ember red sphere with a glyph ring, "
                       "hexagonal seals, an equator rune band and rising embers.",
        "palette": FIRE, "style": "rune", "accent": "fire", "seed": 61806,
        "tags": ["buff", "shield", "defense", "support", "target", "fire", "rune"],
    },
    "shield_buff_poison": {
        "name": "Shield Buff Poison",
        "description": "Poison resistance buff: the defense sphere in toxic green with slowly rising spores "
                       "and a soft mist hugging the ground ring.",
        "palette": POISON, "style": "basic", "accent": "poison", "seed": 61807,
        "tags": ["buff", "shield", "defense", "support", "target", "poison"],
    },
    "shield_buff_poison_rune": {
        "name": "Shield Buff Poison Rune",
        "description": "Poison resistance buff in rune style: the toxic green sphere with a glyph ring, "
                       "hexagonal seals, an equator rune band, rising spores and ground mist.",
        "palette": POISON, "style": "rune", "accent": "poison", "seed": 61808,
        "tags": ["buff", "shield", "defense", "support", "target", "poison", "rune"],
    },
}

# ---------------------------------------------------------------------------
# timing (the concept sheet, total 2.6 s) and the barrier's geometry
# ---------------------------------------------------------------------------

DURATION = 2.6
PHASES = [
    ("cast", 0.0, 0.2),
    ("formation", 0.2, 0.4),
    ("shield_appears", 0.4, 0.6),
    ("active", 0.6, 2.0),
    ("dissipate", 2.0, 2.4),
    ("fade", 2.4, 2.6),
]

T_FORM = 0.2             # the barrier is born
T_EMBLEM = 0.4           # the emblem starts to materialise
T_BREAK = 2.0            # the shell starts to break up
R_SPHERE = 1.1           # 2.2 m across ...
CY = 1.1                 # ... centred 1.1 m up, so it stands on the ground
RS_UV = 0.46             # silhouette radius inside the shell textures (the rest is halo)
SHELL_QUAD = round(R_SPHERE / RS_UV, 4)   # quad size whose silhouette is 2.2 m

# The rise: x_n = CY * (1 - d^n) with d = 1 - RISE_DRAG / 60 (docs/RUNTIME.md section 5,
# semi-implicit Euler at the fixed 60 Hz step), so the launch speed is CY * drag / d.
STEP = 1.0 / 60.0
RISE_DRAG = 8.0
RISE_DECAY = 1.0 - RISE_DRAG * STEP
RISE_SPEED = round(CY * RISE_DRAG / RISE_DECAY, 4)


def grown(age: float) -> float:
    """Fraction of the full size (and of the full height) `age` seconds after birth."""
    return 1.0 - RISE_DECAY ** (age / STEP + 1.0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def c(rgb: list[float], a: float = 1.0, k: float = 1.0) -> list[float]:
    """An RGBA colour from a palette entry, optionally scaled."""
    return [round(rgb[0] * k, 4), round(rgb[1] * k, 4), round(rgb[2] * k, 4), a]


def lum(rgb: list[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def palette_gain(p: dict[str, Any], key: str = "mid") -> float:
    """Scale lights, decals and additive particles so every palette lands at the
    same stage brightness: red carries a third of gold's luminance at equal RGB
    and green carries more, so an unscaled fire variant is dim and an unscaled
    poison variant washes out."""
    return round((lum(GOLD[key]) / lum(p[key])) ** 0.7, 4)


def mix(a: list[float], b: list[float], t: float) -> list[float]:
    return [round(a[i] + (b[i] - a[i]) * t, 4) for i in range(3)]


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


def curve(pairs: list[tuple[float, float]]) -> list[list[float]]:
    return [[round(t, 4), round(v, 4)] for t, v in pairs]


def life_curve(life: float, pairs: list[tuple[float, float]]) -> list[list[float]]:
    """An over-life curve written in SECONDS of age for a particle that lives `life` seconds."""
    out: list[list[float]] = []
    for age, v in pairs:
        t = round(min(1.0, max(0.0, age / life)), 4)
        if out and t <= out[-1][0]:
            continue
        out.append([t, round(v, 4)])
    return out


def life_gradient(life: float, pairs: list[tuple[float, list[float]]]) -> list[list[Any]]:
    """An over-life gradient written in SECONDS of age for a particle that lives `life` seconds."""
    out: list[list[Any]] = []
    for age, col in pairs:
        t = round(min(1.0, max(0.0, age / life)), 4)
        if out and t <= out[-1][0]:
            continue
        out.append([t, col])
    return out


def pulse_track(t0: float, t1: float, base: float, depth: float, period: float,
                head: list[tuple[float, float]], tail: list[tuple[float, float]],
                scale: float = 1.0) -> dict[str, Any]:
    """`head` keys, then a gentle sine between t0 and t1, then `tail` keys."""
    keys = list(head)
    n = max(2, int(round((t1 - t0) / (period / 4.0))))
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        keys.append((t, base * (1.0 + depth * math.sin(2.0 * math.pi * (t - t0) / period))))
    keys.extend(tail)
    return env(keys, scale)


def angle_track(revs: float, direction: float, t0: float, t1: float, phase: float = 0.0,
                step: float = 0.05, ease: float = 0.25) -> dict[str, Any]:
    """A keyframed Y rotation: eases up to `revs` rev/s over `ease` seconds from t0."""
    keys: list[tuple[float, list[float]]] = [(0.0, [0.0, round(phase, 3), 0.0])]
    if t0 > 0.0:
        keys.append((round(t0, 4), [0.0, round(phase, 3), 0.0]))
    angle = 0.0
    t = t0
    while t < t1 - 1e-9:
        nxt = min(t + step, t1)
        s0 = min(1.0, (t - t0) / ease) if ease > 0 else 1.0
        s1 = min(1.0, (nxt - t0) / ease) if ease > 0 else 1.0
        angle += 0.5 * (s0 + s1) * revs * 360.0 * (nxt - t)
        keys.append((round(nxt, 4), [0.0, round(phase + direction * angle, 3), 0.0]))
        t = nxt
    return tr(keys)


def node(nid: str, ntype: str, params: dict[str, Any], *, layer: str | None = None,
         parent: str | None = None, inputs: dict[str, Any] | None = None,
         enabled: bool = True) -> dict[str, Any]:
    n: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        n["layer"] = layer
    if parent:
        n["parent"] = parent
    if not enabled:
        n["enabled"] = False
    n["parameters"] = params
    if inputs:
        n["inputs"] = inputs
    return n


def tex(nid: str, w: int, h: int, nodes: list[dict[str, Any]], output: str,
        frames: int = 1, enabled: bool = True) -> dict[str, Any]:
    params: dict[str, Any] = {"width": w, "height": h}
    if frames > 1:
        params["frames"] = frames
    params["graph"] = {"nodes": nodes, "output": output}
    return node(nid, "texture", params, enabled=enabled)


def op(oid: str, name: str, params: dict[str, Any] | None = None,
       inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": oid, "op": name}
    if params:
        d["params"] = params
    if inputs:
        d["inputs"] = inputs
    return d


def fold(prefix: str, ids: list[str], mode: str = "max") -> tuple[list[dict[str, Any]], str]:
    """Fold a list of graph node ids together with one `math` mode."""
    out: list[dict[str, Any]] = []
    cur = ids[0]
    for i, nxt in enumerate(ids[1:]):
        nid = f"{prefix}{i}"
        out.append(op(nid, "math", {"mode": mode}, {"a": cur, "b": nxt}))
        cur = nid
    return out, cur


def scaled(oid: str, src: str, k: float) -> dict[str, Any]:
    """`src` * k as a graph node."""
    return op(oid, "levels", {"in_low": 0.0, "in_high": 1.0, "out_low": 0.0, "out_high": round(k, 4)}, {"a": src})


# ---------------------------------------------------------------------------
# texture graphs
# ---------------------------------------------------------------------------


def bulge_field() -> list[dict[str, Any]]:
    """Offset field for `distort` that wraps a flat pattern onto a sphere.

    A point at screen radius r on a sphere shows the surface at angle asin(r), so a
    pattern laid out by arc length is sampled at p * m(r), m = asin(r) / (r * pi/2):
    0.64 in the middle (cells look bigger) rising to 1 at the rim (cells squeezed).
    0.3634 * (1 - r)^0.2 tracks 1 - m to within a few percent; `distort` reads the
    offset as (rg - 0.5) * amount, used with amount -1.
    """
    return [
        op("bx", "gradient_linear", {"angle": 0.0, "start": -0.5, "end": 0.5}),
        op("by", "gradient_linear", {"angle": 90.0, "start": -0.5, "end": 0.5}),
        op("bt", "gradient_radial", {"radius": RS_UV, "falloff": "linear"}),
        op("bg", "levels", {"in_low": 0.0, "in_high": 1.0, "out_low": 0.0, "out_high": 0.3634, "gamma": 5.0},
           {"a": "bt"}),
        op("bmx", "math", {"mode": "multiply"}, {"a": "bx", "b": "bg"}),
        op("bmy", "math", {"mode": "multiply"}, {"a": "by", "b": "bg"}),
        op("box", "math", {"mode": "add", "value": 0.5}, {"a": "bmx"}),
        op("boy", "math", {"mode": "add", "value": 0.5}, {"a": "bmy"}),
        op("bulge", "channel_pack", {"channel": "r"}, {"r": "box", "g": "boy"}),
    ]


def shell_cells_graph(seed: int, density: float, width: float, glow: float, soften: float,
                      centre: float) -> tuple[list[dict[str, Any]], str]:
    """Cell seams on a sphere: one `cracks` pattern, energy-modulated, bulged, rim-weighted."""
    parts = [
        op("ck", "cracks", {"density": density, "width": width, "seed": seed}),
        # energy running through the seams: some stretches bright, some faint
        op("en", "fbm", {"frequency": 2.4, "octaves": 3, "seed": seed + 3}),
        op("enl", "levels", {"in_low": 0.3, "in_high": 0.72, "out_low": 0.34, "out_high": 1.0}, {"a": "en"}),
        op("cke", "math", {"mode": "multiply"}, {"a": "ck", "b": "enl"}),
        *bulge_field(),
        op("wrap", "distort", {"amount": -1.0}, {"a": "cke", "by": "bulge"}),
    ]
    seams = "wrap"
    if soften > 0.0:
        parts.append(op("wsoft", "blur", {"radius": soften}, {"a": "wrap"}))
        seams = "wsoft"
    if glow > 0.0:
        parts += [
            op("gl", "blur", {"radius": 5.0}, {"a": "wrap"}),
            op("gll", "levels", {"in_low": 0.0, "in_high": 0.55, "out_low": 0.0, "out_high": glow}, {"a": "gl"}),
            op("sg", "math", {"mode": "max"}, {"a": seams, "b": "gll"}),
        ]
        seams = "sg"
    parts += [
        # fresnel weighting: the seams are faint where the glass faces the camera and strong at the rim
        op("rt", "gradient_radial", {"radius": RS_UV, "falloff": "linear"}),
        op("rr", "invert", None, {"a": "rt"}),
        op("rw", "levels", {"in_low": 0.0, "in_high": 1.0, "out_low": centre, "out_high": 1.0, "gamma": 0.6},
           {"a": "rr"}),
        op("disc", "shape", {"shape": "circle", "radius": RS_UV - 0.004, "softness": 0.006}),
        op("rm", "math", {"mode": "multiply"}, {"a": "rw", "b": "disc"}),
        op("out", "math", {"mode": "multiply"}, {"a": seams, "b": "rm"}),
    ]
    return parts, "out"


def shell_rim_graph() -> tuple[list[dict[str, Any]], str]:
    """The glass body: almost nothing in the middle, a fresnel rise to a crisp silhouette, a soft halo."""
    parts = [
        op("rt", "gradient_radial", {"radius": RS_UV, "falloff": "linear"}),
        op("rr", "invert", None, {"a": "rt"}),
        op("fres", "levels", {"in_low": 0.0, "in_high": 1.0, "out_low": 0.10, "out_high": 0.88, "gamma": 0.22},
           {"a": "rr"}),
        op("disc", "shape", {"shape": "circle", "radius": RS_UV, "softness": 0.004}),
        op("body", "math", {"mode": "multiply"}, {"a": "fres", "b": "disc"}),
        op("edge", "shape", {"shape": "circle", "mode": "outline", "radius": RS_UV, "inset": 0.006,
                             "outline_width": 0.007, "softness": 0.006}),
        op("halo", "shape", {"shape": "circle", "mode": "bevel", "radius": RS_UV, "inset": -0.04, "bevel": 0.04}),
        op("halol", "levels", {"in_low": 0.0, "in_high": 1.0, "out_low": 0.0, "out_high": 0.5, "gamma": 0.55},
           {"a": "halo"}),
        op("ring_out", "math", {"mode": "subtract"}, {"a": "halol", "b": "disc"}),
        op("halo_c", "levels", {"in_low": 0.0, "in_high": 1.0}, {"a": "ring_out"}),
        op("m0", "math", {"mode": "max"}, {"a": "body", "b": "edge"}),
        op("out", "math", {"mode": "max"}, {"a": "m0", "b": "halo_c"}),
    ]
    return parts, "out"


EMBLEM_R = 0.44   # the shield is 0.88 of the tile tall


def emblem_graph(seed: int) -> tuple[list[dict[str, Any]], str]:
    """Heater shield: bright bevelled outline, thin inner line, two-tone face, soft centre ridge."""
    sh = {"shape": "shield", "radius": EMBLEM_R, "shoulder": 0.27, "crest": 0.06, "corner_radius": 0.012}
    parts = [
        op("rim", "shape", {**sh, "mode": "outline", "outline_width": 0.034, "inset": 0.017, "softness": 0.006}),
        op("rimglow", "shape", {**sh, "mode": "outline", "outline_width": 0.03, "inset": 0.02, "softness": 0.05}),
        scaled("rimglow_d", "rimglow", 0.5),
        op("line", "shape", {**sh, "mode": "outline", "outline_width": 0.008, "inset": 0.064, "softness": 0.004}),
        scaled("line_d", "line", 0.78),
        op("face", "shape", {**sh, "mode": "fill", "inset": 0.076, "softness": 0.005}),
        # two-tone facets either side of the ridge, the lit one on the left, and brighter towards the top
        op("split", "gradient_linear", {"angle": 0.0, "start": 0.0, "end": 1.0}),
        op("tone", "levels", {"in_low": 0.492, "in_high": 0.508, "out_low": 0.62, "out_high": 0.36}, {"a": "split"}),
        op("fall", "gradient_linear", {"angle": 90.0, "start": 1.0, "end": 0.55}),
        op("ft", "math", {"mode": "multiply"}, {"a": "tone", "b": "fall"}),
        op("en", "fbm", {"frequency": 5.0, "octaves": 3, "seed": seed}),
        op("enl", "levels", {"in_low": 0.25, "in_high": 0.8, "out_low": 0.72, "out_high": 1.0}, {"a": "en"}),
        op("fte", "math", {"mode": "multiply"}, {"a": "ft", "b": "enl"}),
        op("facet", "math", {"mode": "multiply"}, {"a": "face", "b": "fte"}),
        # an inner glow hugging the rim of the face: the bevel ramp, inverted
        op("bev", "shape", {**sh, "mode": "bevel", "inset": 0.076, "bevel": 0.11}),
        op("bevi", "invert", None, {"a": "bev"}),
        op("bevf", "math", {"mode": "multiply"}, {"a": "bevi", "b": "face"}),
        scaled("bevd", "bevf", 0.42),
        # the soft vertical centre ridge
        op("ridge", "shape", {"shape": "rounded_box", "radius": 0.36, "aspect": 0.009, "softness": 0.011,
                              "center": [0.5, 0.49]}),
        op("ridgef", "math", {"mode": "multiply"}, {"a": "ridge", "b": "face"}),
        scaled("ridged", "ridgef", 0.8),
    ]
    folds, out = fold("em", ["rim", "rimglow_d", "line_d", "facet", "ridged"], "max")
    parts += folds
    parts += [
        op("sum", "math", {"mode": "add"}, {"a": out, "b": "bevd"}),
        op("out", "levels", {"in_low": 0.0, "in_high": 1.0}, {"a": "sum"}),
    ]
    return parts, "out"


def ground_rings_graph(style: str) -> tuple[list[dict[str, Any]], str]:
    """Concentric hairline rings.  The rune style breaks two of them into arcs,
    sets hexagonal seals into the gaps and adds dot markers."""
    parts = [
        op("r0", "ring", {"radius": 0.474, "thickness": 0.0055, "softness": 0.006}),
        op("r1", "ring", {"radius": 0.449, "thickness": 0.011, "softness": 0.008}),
        op("r2", "ring", {"radius": 0.372, "thickness": 0.0055, "softness": 0.006}),
        op("r3", "ring", {"radius": 0.296, "thickness": 0.016, "softness": 0.010}),
        op("r4", "ring", {"radius": 0.214, "thickness": 0.0055, "softness": 0.006}),
        op("r5", "ring", {"radius": 0.128, "thickness": 0.009, "softness": 0.008}),
        # a broad soft band under the footprint of the sphere so the hairlines sit in light
        op("wash", "ring", {"radius": 0.30, "thickness": 0.10, "softness": 0.11}),
        scaled("washd", "wash", 0.30),
        # nine gaps turn the second ring into arcs, so its slow rotation reads
        op("gaps", "spokes", {"count": 9, "width": 0.05, "softness": 0.02, "inner_radius": 0.40,
                              "outer_radius": 0.5, "rotation": 12.0}),
        op("gapsi", "invert", None, {"a": "gaps"}),
        op("r1c", "math", {"mode": "multiply"}, {"a": "r1", "b": "gapsi"}),
    ]
    ids = ["r0", "r1c", "r2", "r3", "r4", "r5", "washd"]
    if style == "rune":
        parts += [
            # the main ring becomes seven arcs, each gap holding a small hexagonal seal
            op("gaps3", "spokes", {"count": 7, "width": 0.062, "softness": 0.014, "inner_radius": 0.25,
                                   "outer_radius": 0.345}),
            op("gaps3i", "invert", None, {"a": "gaps3"}),
            op("r3c", "math", {"mode": "multiply"}, {"a": "r3", "b": "gaps3i"}),
            # dot markers riding the fourth ring, tick marks between the two outer rings
            op("dots", "spokes", {"count": 14, "width": 0.015, "softness": 0.009, "inner_radius": 0.206,
                                  "outer_radius": 0.224, "rotation": 8.0}),
            op("ticks", "spokes", {"count": 36, "width": 0.0055, "softness": 0.004, "inner_radius": 0.452,
                                   "outer_radius": 0.472, "rotation": 5.0}),
            scaled("ticksd", "ticks", 0.75),
        ]
        ids = ["r0", "r1c", "r2", "r3c", "r4", "r5", "washd", "dots", "ticksd"]
        for i in range(7):
            ang = 2.0 * math.pi * i / 7.0          # `spokes` puts its first gap on +u
            cx = round(0.5 + 0.296 * math.cos(ang), 4)
            cy = round(0.5 + 0.296 * math.sin(ang), 4)
            rot = round(-math.degrees(ang) + 90.0, 2)
            parts += [
                op(f"seal{i}", "shape", {"shape": "hexagon", "mode": "outline", "radius": 0.021,
                                         "outline_width": 0.0055, "softness": 0.004, "center": [cx, cy],
                                         "rotation": rot}),
                op(f"pip{i}", "shape", {"shape": "diamond", "radius": 0.008, "aspect": 0.7, "softness": 0.004,
                                        "center": [cx, cy], "rotation": rot}),
            ]
            ids += [f"seal{i}", f"pip{i}"]
    folds, out = fold("gm", ids, "max")
    return parts + folds, out


def ground_runes_graph(seed: int) -> tuple[list[dict[str, Any]], str]:
    """The rotating glyph ring of the ground circle (the rune language the Teleport Rune Style 2
    effect uses: angular strokes clipped to a band, dot markers, splayed tick pairs)."""
    parts = [
        op("vo", "voronoi", {"cells": 30, "mode": "edges", "seed": seed}),
        op("vol", "levels", {"in_low": 0.66, "in_high": 0.95}, {"a": "vo"}),
        op("band", "ring", {"radius": 0.411, "thickness": 0.044, "softness": 0.016}),
        op("ang", "math", {"mode": "multiply"}, {"a": "vol", "b": "band"}),
        # a second, fainter line of glyphs inside the main ring
        op("vo2", "voronoi", {"cells": 22, "mode": "edges", "seed": seed + 19}),
        op("vo2l", "levels", {"in_low": 0.72, "in_high": 0.97}, {"a": "vo2"}),
        op("band2", "ring", {"radius": 0.255, "thickness": 0.036, "softness": 0.014}),
        op("ang2", "math", {"mode": "multiply"}, {"a": "vo2l", "b": "band2"}),
        scaled("ang2d", "ang2", 0.7),
        # splayed tick pairs (chevrons) between the glyph lines
        op("cv1", "spokes", {"count": 9, "width": 0.010, "softness": 0.007, "inner_radius": 0.322,
                             "outer_radius": 0.356, "rotation": 9.0}),
        op("cv2", "spokes", {"count": 9, "width": 0.010, "softness": 0.007, "inner_radius": 0.322,
                             "outer_radius": 0.356, "rotation": -9.0}),
        op("m0", "math", {"mode": "max"}, {"a": "ang", "b": "ang2d"}),
        op("m1", "math", {"mode": "max"}, {"a": "m0", "b": "cv1"}),
        op("m2", "math", {"mode": "max"}, {"a": "m1", "b": "cv2"}),
        op("soft", "blur", {"radius": 0.9}, {"a": "m2"}),
        op("lv", "levels", {"in_low": 0.02, "in_high": 0.86, "gamma": 0.9}, {"a": "soft"}),
    ]
    return parts, "lv"


def glyph_graph(seed: int) -> tuple[list[dict[str, Any]], str]:
    """A small morphing glyph tile for the sprites of the equator band."""
    parts = [
        op("w", "fbm", {"frequency": 2.6, "octaves": 2, "seed": seed + 5, "animate": 1.0}),
        op("vo", "voronoi", {"cells": 5, "mode": "edges", "seed": seed}),
        op("vl", "levels", {"in_low": 0.62, "in_high": 0.97}, {"a": "vo"}),
        op("d", "distort", {"amount": 0.16}, {"a": "vl", "by": "w"}),
        op("mask", "gradient_radial", {"radius": 0.44, "inner_radius": 0.05, "falloff": "smooth"}),
        op("m", "math", {"mode": "multiply"}, {"a": "d", "b": "mask"}),
        op("b", "blur", {"radius": 1.1}, {"a": "m"}),
        op("lv", "levels", {"in_low": 0.05, "in_high": 0.56, "gamma": 0.8}, {"a": "b"}),
    ]
    return parts, "lv"


def accent_sprite_graph(accent: str | None) -> tuple[list[dict[str, Any]], str]:
    """ice: a six-rayed frost glint; fire: a soft ember; poison: a bubble (rim plus an off-centre glint)."""
    if accent == "ice":
        return [
            op("s", "star", {"points": 6, "width": 0.05, "outer_radius": 0.46, "core_radius": 0.10,
                             "softness": 0.02}),
            op("g", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
            scaled("gd", "g", 0.45),
            op("out", "math", {"mode": "max"}, {"a": "s", "b": "gd"}),
        ], "out"
    if accent == "poison":
        return [
            op("rim", "shape", {"shape": "circle", "mode": "outline", "radius": 0.40, "inset": 0.03,
                                "outline_width": 0.05, "softness": 0.035}),
            op("body", "shape", {"shape": "circle", "radius": 0.40, "softness": 0.05}),
            scaled("bodyd", "body", 0.22),
            op("glint", "shape", {"shape": "circle", "radius": 0.075, "softness": 0.06, "center": [0.37, 0.36]}),
            op("m0", "math", {"mode": "max"}, {"a": "rim", "b": "bodyd"}),
            op("out", "math", {"mode": "max"}, {"a": "m0", "b": "glint"}),
        ], "out"
    return [
        op("g", "gradient_radial", {"radius": 0.5, "inner_radius": 0.03, "falloff": "quadratic"}),
        op("out", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 1.4}, {"a": "g"}),
    ], "out"


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------


def build(slug: str, spec: dict[str, Any]) -> dict[str, Any]:
    p = spec["palette"]
    rune = spec["style"] == "rune"
    accent = spec["accent"]
    seed = spec["seed"]

    hot, mid, deep, acc = p["hot"], p["mid"], p["deep"], p["accent"]
    white = [1.0, 1.0, 1.0]
    g = palette_gain(p)             # body colours: lights, decals, glows
    gh = palette_gain(p, "hot")     # hot colours: seams, emblem, sparks

    nodes: list[dict[str, Any]] = []
    add = nodes.append

    # ---------------- textures ----------------
    # soft cross-width profile for every ribbon: a bright thin centre falling to zero at both edges
    add(tex("tex_band", 8, 64, [
        op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("gi", "invert", None, {"a": "g"}),
        op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 0.46, "gamma": 0.55}, {"a": "t"}),
    ], "lv"))
    add(tex("tex_band_soft", 8, 64, [
        op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op("gi", "invert", None, {"a": "g"}),
        op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 1.7}, {"a": "t"}),
    ], "lv"))
    add(tex("tex_glow", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 1.5}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_dot", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.02, "falloff": "quadratic"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 1.5}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_pool", 128, 128, [
        op("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.02, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 2.6, "octaves": 3, "seed": (seed + 13) % 991}),
        op("nl", "levels", {"in_low": 0.25, "in_high": 0.95, "out_low": 0.66, "out_high": 1.0}, {"a": "n"}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 2.2}, {"a": "m"}),
    ], "lv"))
    rim_nodes, rim_out = shell_rim_graph()
    add(tex("tex_shell_rim", 256, 256, rim_nodes, rim_out))
    cf_nodes, cf_out = shell_cells_graph(seed % 7919, density=5.4, width=0.0055, glow=0.38, soften=0.0,
                                         centre=0.5)
    add(tex("tex_shell_cells", 384, 384, cf_nodes, cf_out))
    cb_nodes, cb_out = shell_cells_graph((seed + 101) % 7919, density=7.6, width=0.008, glow=0.0, soften=1.2,
                                         centre=0.62)
    add(tex("tex_shell_back", 256, 256, cb_nodes, cb_out))
    em_nodes, em_out = emblem_graph((seed + 7) % 991)
    add(tex("tex_emblem", 256, 256, em_nodes, em_out))
    gr_nodes, gr_out = ground_rings_graph(spec["style"])
    add(tex("tex_ground_rings", 384, 384, gr_nodes, gr_out))
    ru_nodes, ru_out = ground_runes_graph(seed % 7919)
    add(tex("tex_ground_runes", 384, 384, ru_nodes, ru_out, enabled=rune))
    gl_nodes, gl_out = glyph_graph((seed + 41) % 7919)
    add(tex("tex_glyph", 64, 64, gl_nodes, gl_out, frames=6, enabled=rune))
    ac_nodes, ac_out = accent_sprite_graph(accent)
    add(tex("tex_accent", 48, 48, ac_nodes, ac_out, enabled=accent is not None))
    add(tex("tex_puff", 96, 96, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 3.2, "octaves": 4, "seed": seed % 991}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "n"}),
        op("lv", "levels", {"in_low": 0.07, "in_high": 0.6}, {"a": "m"}),
    ], "lv", enabled=accent == "poison"))

    # ---------------- materials ----------------
    def additive(nid: str, emissive_rgb: list[float], intensity: float, **extra: Any) -> None:
        inputs = extra.pop("inputs", None)
        add(node(nid, "material", {
            "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0],
            "emissive_color": c(emissive_rgb), "emissive_intensity": intensity,
            "soft_particle": True, "depth_fade": 0.1, **extra,
        }, inputs=inputs))

    additive("mat_shell", mid, 0.3)
    additive("mat_seam", hot, 0.35)
    additive("mat_break", hot, 0.4, dissolve=0.96, erosion=0.05)
    additive("mat_emblem", hot, 0.4)
    additive("mat_glow", mid, 0.3, depth_fade=0.3)
    additive("mat_spark", hot, 0.3)
    additive("mat_glyph", hot, 0.25, depth_fade=0.06)
    additive("mat_ring", mix(mid, hot, 0.5), 0.5)
    additive("mat_pool", mid, 0.4)
    additive("mat_ribbon", mid, 0.3, double_sided=True, inputs={"base_texture": "tex_band"})
    additive("mat_ribbon_soft", mid, 0.26, depth_fade=0.2, double_sided=True,
             inputs={"base_texture": "tex_band_soft"})
    add(node("mat_bead", "material", {
        "blend": "alpha", "shading": "unlit", "base_color": c(hot), "emissive_color": c(hot),
        "emissive_intensity": 1.0,
    }))
    ice = accent == "ice"
    add(node("mat_shard", "material", {
        "blend": "alpha", "shading": "lit", "base_color": c(p["shard"]),
        "emissive_color": c(p["shard_glow"]), "emissive_intensity": round((0.34 if ice else 0.8) * g, 3),
        "fresnel_power": 2.6, "double_sided": True, "opacity": 0.9 if ice else 0.86,
    }))
    add(node("mat_flake", "material", {
        "blend": "additive", "shading": "unlit", "base_color": c(mix(mid, hot, 0.35), 1.0, 0.8),
        "emissive_color": c(hot), "emissive_intensity": round(1.6 * gh, 3), "double_sided": True,
        "opacity": 0.85,
    }))
    add(node("mat_mist", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(p["mist"]),
        "emissive_intensity": 0.2, "soft_particle": True, "depth_fade": 0.35,
    }, enabled=accent == "poison"))

    # ---------------- forces / collider ----------------
    add(node("f_lift", "force", {"force_type": "buoyancy", "direction": [0.0, 1.0, 0.0], "strength": 1.1,
                                 "temperature": 1.0}))
    add(node("f_curl", "force", {"force_type": "curl_noise", "strength": 1.3, "frequency": 1.3, "octaves": 2,
                                 "speed": 0.7}))
    add(node("f_drift", "force", {"force_type": "curl_noise", "strength": 0.3, "frequency": 0.7, "octaves": 2,
                                  "speed": 0.35}))
    add(node("f_orbit", "force", {"force_type": "vortex", "direction": [0.0, 1.0, 0.0],
                                  "position": [0.0, CY, 0.0], "strength": 0.55, "radius": 4.5,
                                  "falloff": "smooth"}))
    add(node("f_fall", "force", {"force_type": "gravity", "direction": [0.0, -1.0, 0.0], "strength": 3.2}))
    accent_force = {
        "ice": {"force_type": "gravity", "direction": [0.0, -1.0, 0.0], "strength": 0.55},
        "fire": {"force_type": "curl_noise", "strength": 2.4, "frequency": 1.7, "octaves": 2, "speed": 1.1},
        "poison": {"force_type": "curl_noise", "strength": 0.7, "frequency": 1.1, "octaves": 2, "speed": 0.5},
    }.get(accent or "", {"force_type": "curl_noise", "strength": 1.0, "frequency": 1.0, "octaves": 2,
                         "speed": 0.5})
    add(node("f_accent", "force", accent_force, enabled=accent is not None))
    add(node("ground", "collider", {"collider_type": "plane", "normal": [0.0, 1.0, 0.0], "bounce": 0.15,
                                    "friction": 0.6}))

    # ---------------- ground circle (cast, and what is left in the fade) ----------------
    ring_size = 3.8
    add(node("d_pool", "decal", {
        "shape": "circle", "size": [5.2, 5.2], "position": [0.0, 0.0, 0.0],
        "color": c(mid), "emissive": round(0.8 * g, 3), "blend": "additive", "fade_in": 0.05, "fade_out": 0.2,
        "opacity": tr([(0.0, 0.0), (0.08, 0.2), (0.2, 0.3), (0.5, 0.4), (2.0, 0.38), (2.12, 0.5),
                       (2.4, 0.22), (2.58, 0.0)]),
    }, layer="ground", inputs={"texture": "tex_pool", "material": "mat_pool"}))
    add(node("d_rings", "decal", {
        "shape": "circle",
        "size": tr([(0.0, [1.2, 1.2]), (0.07, [2.9, 2.9]), (0.2, [ring_size, ring_size]),
                    (2.0, [ring_size, ring_size]), (2.14, [ring_size * 1.035, ring_size * 1.035]),
                    (2.58, [ring_size * 0.96, ring_size * 0.96])]),
        "position": [0.0, 0.0, 0.0],
        "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (DURATION, [0.0, 34.0, 0.0])]),
        "color": c(mix(mid, hot, 0.55)), "blend": "additive", "fade_in": 0.04, "fade_out": 0.12,
        "emissive": pulse_track(0.6, 2.0, 2.0, 0.16, 0.7, [(0.0, 1.0), (0.1, 2.6), (0.3, 2.0), (0.45, 2.9)],
                                [(2.1, 2.6), (2.4, 1.3), (2.58, 0.4)], g),
        "opacity": tr([(0.0, 0.0), (0.06, 0.8), (0.2, 0.95), (2.0, 0.92), (2.1, 1.0), (2.4, 0.5),
                       (2.58, 0.0)]),
    }, layer="ground", inputs={"texture": "tex_ground_rings", "material": "mat_ring"}))
    add(node("d_runes", "decal", {
        "shape": "circle", "size": [ring_size, ring_size], "position": [0.0, 0.0, 0.0],
        "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (DURATION, [0.0, -70.0, 0.0])]),
        "color": c(hot), "blend": "additive", "fade_in": 0.05, "fade_out": 0.12,
        "emissive": pulse_track(0.6, 2.0, 2.2, 0.2, 0.7, [(0.0, 1.0), (0.3, 2.2)], [(2.4, 1.2), (2.58, 0.4)], gh),
        "opacity": tr([(0.0, 0.0), (0.1, 0.4), (0.28, 0.95), (2.0, 0.9), (2.1, 1.0), (2.36, 0.35),
                       (2.55, 0.0)]),
    }, layer="ground", inputs={"texture": "tex_ground_runes", "material": "mat_ring"}, enabled=rune))

    # ---------------- cast: a soft glow rising out of the centre ----------------
    # a bead climbs out of the ring and its ribbon is the shaft: widest at the ground, tapering to
    # nothing at the head, low emissive so the cross-width gradient survives the bloom
    shaft_keys = [(0.0, [0.0, 0.02, 0.0])]
    for k in range(1, 13):
        u = k / 12.0
        shaft_keys.append((round(0.02 + 0.36 * u, 4), [0.0, round(0.02 + 2.5 * (u ** 0.7), 4), 0.0]))
    shaft_keys.append((DURATION, [0.0, 2.8, 0.0]))
    add(node("cast_bead", "mesh", {"primitive": "sphere", "radius": 0.008, "visible": False,
                                   "position": tr(shaft_keys)}, layer="cast"))
    add(node("cast_shaft", "trail", {
        "lifetime": 0.9, "max_segments": 160, "min_vertex_distance": 0.02,
        "taper": curve([(0.0, 0.0), (0.06, 0.32), (0.16, 0.8), (0.32, 1.0), (0.75, 0.96), (1.0, 0.8)]),
        "opacity_over_life": curve([(0.0, 0.9), (0.35, 1.0), (0.8, 0.85), (1.0, 0.7)]),
        "color": c(mix(mid, hot, 0.4)), "blend": "additive", "noise_amplitude": 0.05, "noise_frequency": 0.8,
        "width": env([(0.0, 0.0), (0.05, 0.36), (0.18, 0.7), (0.36, 0.78), (0.52, 0.5), (0.66, 0.0),
                      (DURATION, 0.0)]),
        "emissive": env([(0.0, 0.2), (0.1, 0.42), (0.3, 0.36), (0.48, 0.2), (0.66, 0.0), (DURATION, 0.0)], gh),
    }, layer="cast", inputs={"source": "cast_bead", "material": "mat_ribbon_soft"}))
    add(node("ps_cast_glow", "particle_system", {
        "max_particles": 8, "lifetime": 0.62, "size": 2.3,
        "size_over_life": curve([(0.0, 0.35), (0.3, 1.0), (1.0, 1.25)]),
        "color": c(mix(mid, hot, 0.3)), "opacity": 0.5,
        "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (0.55, 0.6), (1.0, 0.0)]),
        "emissive": round(0.5 * g, 3), "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.35,
    }, inputs={"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_cast_glow", "emitter", {
        "shape": "point", "position": [0.0, 0.5, 0.0], "rate": 0.0, "burst_count": 1, "burst_times": [0.0],
        "velocity": 0.6, "direction": [0.0, 1.0, 0.0], "start_time": 0.02,
    }, layer="cast", inputs={"particle": "ps_cast_glow"}))

    # ---------------- the barrier: three camera-facing shells that rise and grow ----------------
    shell_life = round(T_BREAK + 0.13 - T_FORM, 4)          # the intact shells are gone by 2.13 s
    grow_keys = [(a, grown(a)) for a in (0.0, 0.0333, 0.0667, 0.1, 0.1333, 0.1667, 0.2, 0.25, 0.3, 0.4, 0.5)]

    def shell_size_curve(life: float, breathe: float) -> list[list[float]]:
        keys = list(grow_keys)
        t, up = 0.6, True
        while t < life - 0.2:
            keys.append((t, 1.0 + (breathe if up else 0.0)))
            t, up = t + 0.35, not up
        keys.append((life - 0.13, 1.0))
        keys.append((life, 1.035))
        return life_curve(life, keys)

    def rise_emitter(nid: str, system: str, layer: str) -> None:
        add(node(nid, "emitter", {
            "shape": "point", "position": [0.0, 0.0, 0.0], "rate": 0.0, "burst_count": 1,
            "burst_times": [0.0], "velocity": RISE_SPEED, "direction": [0.0, 1.0, 0.0],
            "start_time": T_FORM,
        }, layer=layer, inputs={"particle": system}))

    shells = [
        # id, sprite, material, colour keys (age s, colour), opacity, emissive, roll deg/s, fade-in, breathe
        ("ps_shell_rim", "tex_shell_rim", "mat_shell",
         [(0.0, c(mix(mid, hot, 0.75))), (0.35, c(mix(mid, hot, 0.3))), (0.6, c(mid))],
         0.9, 1.15 * g, 0.0, 0.08, 0.012),
        ("ps_shell_cells", "tex_shell_cells", "mat_seam",
         [(0.0, c(white)), (0.3, c(hot)), (0.7, c(mix(hot, mid, 0.3)))],
         1.0, 1.9 * gh, 5.0, 0.2, 0.012),
        ("ps_shell_back", "tex_shell_back", "mat_seam",
         [(0.0, c(hot)), (0.5, c(mix(mid, hot, 0.35)))],
         0.62, 0.8 * gh, -4.0, 0.26, 0.012),
    ]
    for sid, sprite, material, colours, opacity, emissive, roll, fade_in, breathe in shells:
        add(node(sid, "particle_system", {
            "max_particles": 4, "lifetime": shell_life, "size": SHELL_QUAD,
            "size_over_life": shell_size_curve(shell_life, breathe),
            "color": [1.0, 1.0, 1.0, 1.0],
            "color_over_life": life_gradient(shell_life, colours + [(shell_life, colours[-1][1])]),
            "opacity": opacity,
            "opacity_over_life": life_curve(shell_life, [(0.0, 0.0), (fade_in, 1.0), (shell_life - 0.13, 1.0),
                                                         (shell_life - 0.05, 0.35), (shell_life, 0.0)]),
            "emissive": round(emissive, 3),
            "emissive_over_life": life_curve(shell_life, [(0.0, 1.7), (0.25, 1.35), (0.42, 1.0),
                                                          (shell_life - 0.13, 1.0), (shell_life, 1.6)]),
            "angular_velocity": roll, "drag": RISE_DRAG, "render_mode": "billboard", "blend": "additive",
            "soft_particle_distance": 0.12,
        }, inputs={"sprite": sprite, "material": material}))
        rise_emitter(sid.replace("ps_", "e_"), sid, "barrier")

    # a faint volume of light inside the glass
    add(node("ps_shell_core", "particle_system", {
        "max_particles": 4, "lifetime": shell_life + 0.2, "size": 2.5,
        "size_over_life": life_curve(shell_life + 0.2, grow_keys + [(shell_life, 1.0), (shell_life + 0.2, 1.2)]),
        "color": c(mid), "opacity": 0.2,
        "opacity_over_life": life_curve(shell_life + 0.2, [(0.0, 0.0), (0.25, 1.0), (0.45, 0.8),
                                                           (shell_life - 0.1, 0.8), (shell_life, 1.3),
                                                           (shell_life + 0.2, 0.0)]),
        "emissive": round(0.5 * g, 3), "drag": RISE_DRAG, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.4,
    }, inputs={"sprite": "tex_glow", "material": "mat_glow"}))
    rise_emitter("e_shell_core", "ps_shell_core", "barrier")

    # ---------------- the shield emblem ----------------
    em_life = 2.16                                             # 0.2 -> 2.36 s
    em_in = T_EMBLEM - T_FORM                                   # invisible while it rides up
    em_out = T_BREAK - T_FORM
    add(node("ps_emblem", "particle_system", {
        "max_particles": 4, "lifetime": em_life, "size": 1.14,
        "size_over_life": life_curve(em_life, [(0.0, 0.7), (em_in, 0.74), (em_in + 0.1, 1.09),
                                               (em_in + 0.2, 1.0), (1.0, 1.015), (1.4, 1.0), (em_out, 1.01),
                                               (em_life, 1.16)]),
        "color": [1.0, 1.0, 1.0, 1.0],
        "color_over_life": life_gradient(em_life, [(0.0, c(mix(hot, white, 0.5))),
                                                   (em_in + 0.12, c(mix(hot, white, 0.5))),
                                                   (em_in + 0.3, c(mix(hot, mid, 0.1))),
                                                   (em_out, c(mix(hot, mid, 0.1))), (em_life, c(mid))]),
        "opacity": 1.0,
        "opacity_over_life": life_curve(em_life, [(0.0, 0.0), (em_in, 0.0), (em_in + 0.12, 1.0), (em_out, 1.0),
                                                  (em_out + 0.16, 0.5), (em_life, 0.0)]),
        "emissive": round(1.5 * gh, 3),
        "emissive_over_life": life_curve(em_life, [(0.0, 1.0), (em_in, 2.4), (em_in + 0.1, 2.6),
                                                   (em_in + 0.32, 1.0), (0.95, 1.12), (1.3, 1.0), (1.65, 1.12),
                                                   (em_out, 1.0), (em_life, 0.6)]),
        "drag": RISE_DRAG, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.1,
    }, inputs={"sprite": "tex_emblem", "material": "mat_emblem"}))
    rise_emitter("e_emblem", "ps_emblem", "emblem")
    add(node("ps_emblem_glow", "particle_system", {
        "max_particles": 4, "lifetime": em_life, "size": 1.9,
        "size_over_life": life_curve(em_life, [(0.0, 0.5), (em_in, 0.6), (em_in + 0.1, 1.25), (em_in + 0.3, 1.0),
                                               (em_out, 1.0), (em_life, 1.3)]),
        "color": c(mix(mid, hot, 0.25)), "opacity": 0.34,
        "opacity_over_life": life_curve(em_life, [(0.0, 0.0), (em_in, 0.0), (em_in + 0.1, 1.6), (em_in + 0.32, 1.0),
                                                  (em_out, 1.0), (em_life, 0.0)]),
        "emissive": round(0.6 * g, 3), "drag": RISE_DRAG, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.3,
    }, inputs={"sprite": "tex_glow", "material": "mat_glow"}))
    rise_emitter("e_emblem_glow", "ps_emblem_glow", "emblem")

    # ---------------- comet swirl: beads on tilted orbits carrying tapered ribbons ----------------
    add(node("ps_comet_head", "particle_system", {
        "max_particles": 60, "lifetime": 0.15, "lifetime_variance": 0.04, "size": 0.36, "size_variance": 0.08,
        "size_over_life": curve([(0.0, 0.6), (0.25, 1.0), (1.0, 0.35)]),
        "color": c(mix(hot, white, 0.3)),
        "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [1.0, c(mix(mid, hot, 0.2))]],
        "opacity": 0.55, "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (1.0, 0.0)]),
        "emissive": round(1.5 * gh, 3), "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.15,
    }, inputs={"sprite": "tex_glow", "material": "mat_spark"}))
    comets = [
        # key, hub tilt (deg), orbit radius, rev/s, direction, start phase, ribbon width, emissive, head rate
        ("a", [13.0, 0.0, -9.0], 1.47, 0.82, 1.0, 200.0, 0.105, 2.4, 46.0),
        ("b", [-11.0, 0.0, 15.0], 1.63, 0.6, -1.0, 40.0, 0.07, 1.35, 26.0),
    ]
    sw0, sw1 = 0.46, 2.16
    for key, tilt, radius, revs, direction, phase, width, emis, head_rate in comets:
        add(node(f"swirl_hub_{key}", "mesh", {"primitive": "sphere", "radius": 0.008, "visible": False,
                                              "position": [0.0, CY, 0.0], "rotation": tilt}, layer="swirl"))
        add(node(f"swirl_spin_{key}", "mesh", {
            "primitive": "sphere", "radius": 0.008, "visible": False,
            "rotation": angle_track(revs, direction, sw0, sw1 + 0.1, phase),
        }, layer="swirl", parent=f"swirl_hub_{key}"))
        add(node(f"comet_{key}", "mesh", {
            "primitive": "sphere", "radius": 0.03, "segments": 10, "visible": True,
            "position": [radius, 0.0, 0.0], "start_time": sw0, "duration": round(sw1 - sw0, 4),
            "color": c(hot), "emissive": env([(sw0, 0.0), (sw0 + 0.14, 3.0 * gh), (T_BREAK, 3.0 * gh), (sw1, 0.0)]),
            "scale": tr([(sw0, [0.2, 0.2, 0.2]), (sw0 + 0.16, [1.0, 1.0, 1.0]), (T_BREAK, [1.0, 1.0, 1.0]),
                         (sw1, [0.1, 0.1, 0.1])]),
        }, layer="swirl", parent=f"swirl_spin_{key}", inputs={"material": "mat_bead"}))
        life = round(0.46 / revs * 0.82, 4)
        add(node(f"comet_trail_{key}", "trail", {
            "lifetime": life, "max_segments": 180, "min_vertex_distance": 0.03,
            "taper": curve([(0.0, 0.35), (0.04, 1.0), (0.3, 0.7), (0.65, 0.34), (1.0, 0.0)]),
            "opacity_over_life": curve([(0.0, 1.0), (0.25, 0.8), (0.7, 0.35), (1.0, 0.0)]),
            "color": c(mix(hot, mid, 0.2)), "blend": "additive", "start_time": sw0,
            "width": tr([(sw0, 0.0), (sw0 + 0.16, width), (T_BREAK, width), (sw1 - 0.04, width * 0.3), (sw1, 0.0)]),
            "emissive": env([(sw0, 0.3), (sw0 + 0.18, emis), (T_BREAK, emis), (sw1, 0.0)], gh),
        }, layer="swirl", inputs={"source": f"comet_{key}", "material": "mat_ribbon"}))
        add(node(f"comet_haze_{key}", "trail", {
            "lifetime": round(life * 0.8, 4), "max_segments": 140, "min_vertex_distance": 0.04,
            "taper": curve([(0.0, 0.3), (0.06, 1.0), (0.4, 0.62), (1.0, 0.0)]),
            "opacity_over_life": curve([(0.0, 0.5), (0.5, 0.3), (1.0, 0.0)]),
            "color": c(mid), "blend": "additive", "start_time": sw0,
            "width": tr([(sw0, 0.0), (sw0 + 0.16, width * 3.4), (T_BREAK, width * 3.4), (sw1, 0.0)]),
            "emissive": env([(sw0, 0.1), (sw0 + 0.18, emis * 0.4), (T_BREAK, emis * 0.4), (sw1, 0.0)], g),
        }, layer="swirl", inputs={"source": f"comet_{key}", "material": "mat_ribbon_soft"}))
        add(node(f"e_comet_head_{key}", "emitter", {
            "shape": "point", "velocity": 0.0, "start_time": sw0,
            "rate": tr([(sw0, 0.0), (sw0 + 0.12, head_rate), (T_BREAK, head_rate), (sw1 - 0.02, 0.0)]),
        }, layer="swirl", parent=f"comet_{key}", inputs={"particle": "ps_comet_head"}))

    # ---------------- crystal shards ----------------
    add(node("shard_mesh", "mesh", {"primitive": "sphere", "radius": 0.5, "segments": 3, "visible": False}))
    shard_life = 1.94
    add(node("ps_shard", "particle_system", {
        "max_particles": 40, "lifetime": shard_life, "lifetime_variance": 0.05,
        "size": 0.21, "size_variance": 0.085,
        "size_over_life": life_curve(shard_life, [(0.0, 0.0), (0.2, 1.08), (0.3, 1.0), (shard_life - 0.34, 1.0),
                                                  (shard_life - 0.12, 0.5), (shard_life, 0.0)]),
        "color": c(p["shard"]), "opacity": 0.9, "emissive": 0.6,
        "render_mode": "mesh", "blend": "alpha", "orientation": "tumble",
        "angular_velocity": 34.0, "angular_velocity_variance": 22.0,
        "mesh_scale": [0.5, 1.75, 0.5], "mesh_scale_variance": [0.13, 0.55, 0.13],
        "drag": 0.9, "sort": True,
    }, inputs={"mesh": "shard_mesh", "material": "mat_shard", "forces": ["f_orbit", "f_drift"]}))
    shard_rings = [
        # id, height, radius, inner radius, count
        ("e_shard_low", 0.55, 1.78, 1.5, 2),
        ("e_shard_mid", 1.2, 1.95, 1.62, 3),
        ("e_shard_high", 1.92, 1.62, 1.22, 2),
    ]
    for eid, height, radius, inner, count in shard_rings:
        add(node(eid, "emitter", {
            "shape": "ring", "radius": radius, "inner_radius": inner, "position": [0.0, height, 0.0],
            "rate": 0.0, "burst_count": count, "burst_times": [0.0], "start_time": 0.42,
            "velocity": 0.16, "velocity_variance": 0.1, "direction": [0.0, 1.0, 0.0], "spread": 40.0,
        }, layer="shards", inputs={"particle": "ps_shard"}))

    # ---------------- sparks, embers and the soft rising streaks ----------------
    add(node("ps_mote", "particle_system", {
        "max_particles": 500, "lifetime": 0.95, "lifetime_variance": 0.45,
        "size": 0.05, "size_variance": 0.03,
        "size_over_life": curve([(0.0, 0.3), (0.25, 1.0), (0.75, 0.8), (1.0, 0.0)]),
        "color": c(hot), "color_over_life": [[0.0, c(hot)], [0.5, c(mix(hot, mid, 0.6))], [1.0, c(deep, 1.0, 1.6)]],
        "opacity_over_life": curve([(0.0, 0.0), (0.12, 1.0), (0.7, 0.7), (1.0, 0.0)]),
        "emissive": round(3.6 * gh, 3), "render_mode": "stretched_billboard", "velocity_stretch": 0.1,
        "blend": "additive", "drag": 1.5, "soft_particle_distance": 0.06,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_curl", "f_lift"]}))
    add(node("e_mote", "emitter", {
        "shape": "ring", "radius": 1.75, "inner_radius": 0.5, "position": [0.0, 0.05, 0.0],
        "velocity": 1.7, "velocity_variance": 1.1, "direction": [0.0, 1.0, 0.0], "spread": 20.0,
        "rate": tr([(0.0, 0.0), (0.04, 170.0), (0.22, 250.0), (0.5, 200.0), (0.7, 110.0), (2.0, 105.0),
                    (2.08, 230.0), (2.3, 60.0), (2.45, 14.0), (2.52, 0.0)]),
    }, layer="sparks", inputs={"particle": "ps_mote"}))
    # sparks that leave the surface of the glass, so the sphere reads as a volume from every side
    add(node("ps_glint", "particle_system", {
        "max_particles": 300, "lifetime": 0.7, "lifetime_variance": 0.3, "size": 0.038, "size_variance": 0.02,
        "size_over_life": curve([(0.0, 0.2), (0.2, 1.0), (1.0, 0.0)]),
        "color": c(mix(hot, white, 0.3)), "color_over_life": [[0.0, c(white)], [1.0, c(mid)]],
        "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.45, 0.35), (0.6, 0.9), (1.0, 0.0)]),
        "emissive": round(3.2 * gh, 3), "render_mode": "billboard", "blend": "additive", "drag": 2.0,
        "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_drift", "f_lift"]}))
    add(node("e_glint", "emitter", {
        "shape": "sphere", "radius": R_SPHERE, "surface_only": True, "position": [0.0, CY, 0.0],
        "velocity": 0.25, "velocity_variance": 0.2, "direction": [0.0, 0.0, 0.0], "start_time": 0.36,
        "rate": tr([(0.36, 0.0), (0.55, 120.0), (0.8, 70.0), (2.0, 70.0), (2.05, 0.0)]),
        "scale": tr([(0.36, [0.8, 0.8, 0.8]), (0.6, [1.0, 1.0, 1.0])]),
    }, layer="sparks", inputs={"particle": "ps_glint"}))
    add(node("ps_streak", "particle_system", {
        "max_particles": 80, "lifetime": 0.9, "lifetime_variance": 0.25, "size": 0.2, "size_variance": 0.07,
        "size_over_life": curve([(0.0, 0.4), (0.3, 1.0), (1.0, 0.7)]),
        "color": c(mix(mid, hot, 0.4)), "opacity": 0.2,
        "opacity_over_life": curve([(0.0, 0.0), (0.3, 1.0), (0.65, 0.7), (1.0, 0.0)]),
        "emissive": round(0.9 * gh, 3), "render_mode": "stretched_billboard", "velocity_stretch": 3.2,
        "blend": "additive", "drag": 0.25, "soft_particle_distance": 0.2,
    }, inputs={"sprite": "tex_glow", "material": "mat_spark"}))
    add(node("e_streak", "emitter", {
        "shape": "ring", "radius": 1.62, "inner_radius": 0.85, "position": [0.0, 0.12, 0.0],
        "velocity": 1.7, "velocity_variance": 0.6, "direction": [0.0, 1.0, 0.0], "spread": 2.0,
        "rate": tr([(0.0, 0.0), (0.06, 26.0), (0.4, 18.0), (0.7, 11.0), (2.0, 11.0), (2.1, 20.0), (2.3, 0.0)]),
    }, layer="sparks", inputs={"particle": "ps_streak"}))

    # ---------------- rune band on the equator (rune style) ----------------
    add(node("ps_glyph", "particle_system", {
        "max_particles": 120, "lifetime": 0.96, "lifetime_variance": 0.06, "size": 0.17, "size_variance": 0.025,
        "size_over_life": curve([(0.0, 0.5), (0.15, 1.0), (0.85, 1.0), (1.0, 0.6)]),
        "color": c(hot), "color_over_life": [[0.0, c(white)], [0.3, c(hot)], [1.0, c(mix(hot, mid, 0.5))]],
        "opacity": 0.95, "opacity_over_life": curve([(0.0, 0.0), (0.14, 1.0), (0.75, 0.85), (1.0, 0.0)]),
        "emissive": round(2.6 * gh, 3), "render_mode": "billboard", "blend": "additive",
        "rotation_variance": 180.0, "sprite_columns": 6, "sprite_rows": 1, "sprite_fps": 0.0,
        "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_glyph", "material": "mat_glyph"}, enabled=rune))
    add(node("rune_hub", "mesh", {"primitive": "sphere", "radius": 0.008, "visible": False,
                                  "position": [0.0, CY, 0.0], "rotation": [4.0, 0.0, 3.0]},
             layer="runes", enabled=rune))
    add(node("rune_spin", "mesh", {"primitive": "sphere", "radius": 0.008, "visible": False,
                                   "rotation": angle_track(0.22, 1.0, 0.5, 2.2, 0.0, ease=0.0)},
             layer="runes", parent="rune_hub", enabled=rune))
    rune_beads = 5
    for i in range(rune_beads):
        ang = 2.0 * math.pi * i / rune_beads
        add(node(f"rune_bead{i + 1}", "mesh", {
            "primitive": "sphere", "radius": 0.008, "visible": False,
            "position": [round(1.25 * math.cos(ang), 4), 0.0, round(1.25 * math.sin(ang), 4)],
        }, layer="runes", parent="rune_spin", enabled=rune))
        add(node(f"e_glyph{i + 1}", "emitter", {
            "shape": "point", "velocity": 0.0, "start_time": 0.5,
            "rate": tr([(0.5, 0.0), (0.56, 9.0), (2.0, 9.0), (2.04, 0.0)]),
        }, layer="runes", parent=f"rune_bead{i + 1}", inputs={"particle": "ps_glyph"}, enabled=rune))

    # ---------------- the elemental accent (one cheap layer per element) ----------------
    accents: dict[str, dict[str, Any]] = {
        # frost sparkles drifting down around the sphere
        "ice": {
            "ps": {"lifetime": 1.5, "lifetime_variance": 0.5, "size": 0.085, "size_variance": 0.045,
                   "color": c(mix(hot, white, 0.5)),
                   "color_over_life": [[0.0, c(white)], [0.6, c(hot)], [1.0, c(p["accent"])]],
                   "opacity_over_life": curve([(0.0, 0.0), (0.12, 1.0), (0.3, 0.3), (0.45, 1.0), (0.62, 0.35),
                                               (0.78, 0.9), (1.0, 0.0)]),
                   "emissive": round(2.6 * gh, 3), "render_mode": "billboard", "drag": 1.4,
                   "rotation_variance": 180.0, "angular_velocity": 40.0, "angular_velocity_variance": 60.0},
            "emitter": {"shape": "sphere", "radius": 1.9, "position": [0.0, 1.75, 0.0], "velocity": 0.2,
                        "velocity_variance": 0.15, "direction": [0.0, -1.0, 0.0], "spread": 40.0},
            "rate": 46.0,
        },
        # embers that climb and curl
        "fire": {
            "ps": {"lifetime": 1.15, "lifetime_variance": 0.4, "size": 0.06, "size_variance": 0.03,
                   "color": c(mix(acc, hot, 0.4)),
                   "color_over_life": [[0.0, c(hot)], [0.35, c(acc)], [1.0, c(mid, 1.0, 0.7)]],
                   "opacity_over_life": curve([(0.0, 0.0), (0.1, 1.0), (0.4, 0.6), (0.55, 1.0), (1.0, 0.0)]),
                   "emissive": round(3.4 * gh, 3), "render_mode": "stretched_billboard",
                   "velocity_stretch": 0.16, "drag": 0.9},
            "emitter": {"shape": "ring", "radius": 1.5, "inner_radius": 0.3, "position": [0.0, 0.06, 0.0],
                        "velocity": 2.0, "velocity_variance": 1.0, "direction": [0.0, 1.0, 0.0], "spread": 16.0},
            "rate": 85.0,
        },
        # spores / bubbles that rise slowly and wobble
        "poison": {
            "ps": {"lifetime": 1.7, "lifetime_variance": 0.5, "size": 0.085, "size_variance": 0.05,
                   "color": c(mix(acc, hot, 0.3)),
                   "color_over_life": [[0.0, c(hot)], [0.5, c(acc)], [1.0, c(mid)]],
                   "opacity_over_life": curve([(0.0, 0.0), (0.15, 1.0), (0.8, 0.8), (0.9, 1.2), (1.0, 0.0)]),
                   "emissive": round(1.7 * gh, 3), "render_mode": "billboard", "drag": 1.6},
            "emitter": {"shape": "ring", "radius": 1.7, "inner_radius": 0.4, "position": [0.0, 0.08, 0.0],
                        "velocity": 0.75, "velocity_variance": 0.4, "direction": [0.0, 1.0, 0.0], "spread": 14.0},
            "rate": 42.0,
        },
    }
    a_spec = accents.get(accent or "", accents["fire"])
    a_on = accent is not None
    add(node("ps_accent", "particle_system", {
        "max_particles": 260,
        "size_over_life": curve([(0.0, 0.3), (0.2, 1.0), (0.8, 0.9), (1.0, 0.2)]),
        "blend": "additive", "soft_particle_distance": 0.06, **a_spec["ps"],
    }, inputs={"sprite": "tex_accent", "material": "mat_spark",
               "forces": ["f_accent", "f_lift"] if accent != "ice" else ["f_accent", "f_drift"]},
        enabled=a_on))
    a_rate = a_spec["rate"]
    add(node("e_accent", "emitter", {
        **a_spec["emitter"], "start_time": 0.3,
        "rate": tr([(0.3, 0.0), (0.55, a_rate), (2.0, a_rate), (2.12, a_rate * 0.4), (2.3, 0.0)]),
    }, layer="accent", inputs={"particle": "ps_accent"}, enabled=a_on))
    mist_on = accent == "poison"
    add(node("ps_accent_mist", "particle_system", {
        "max_particles": 40, "lifetime": 1.5, "lifetime_variance": 0.4, "size": 1.25, "size_variance": 0.35,
        "size_over_life": curve([(0.0, 0.55), (0.5, 1.0), (1.0, 1.3)]),
        "color": c(p["mist"]), "color_over_life": [[0.0, c(mix(p["mist"], mid, 0.3))], [1.0, c(deep)]],
        "opacity": 0.16, "opacity_over_life": curve([(0.0, 0.0), (0.3, 1.0), (0.7, 0.7), (1.0, 0.0)]),
        "emissive": 0.5, "render_mode": "billboard", "blend": "additive", "drag": 1.2,
        "rotation_variance": 180.0, "angular_velocity": 10.0, "angular_velocity_variance": 14.0,
        "soft_particle_distance": 0.4,
    }, inputs={"sprite": "tex_puff", "material": "mat_mist", "forces": ["f_drift"]}, enabled=mist_on))
    add(node("e_accent_mist", "emitter", {
        "shape": "ring", "radius": 2.0, "inner_radius": 1.1, "position": [0.0, 0.3, 0.0],
        "velocity": 0.2, "velocity_variance": 0.15, "direction": [0.0, 0.2, 0.0], "spread": 70.0,
        "start_time": 0.1,
        "rate": tr([(0.1, 0.0), (0.4, 9.0), (2.0, 8.0), (2.3, 0.0)]),
    }, layer="accent", inputs={"particle": "ps_accent_mist"}, enabled=mist_on))

    # ---------------- dissipate: the shells dissolve, flakes and sparks leave the surface ----------------
    break_life = 0.44
    breaks = [
        ("ps_break_rim", "tex_shell_rim", c(mid), 0.85, 1.2 * g, 0.0, 0.0),
        ("ps_break_cells", "tex_shell_cells", c(mix(hot, mid, 0.3)), 1.0, 2.1 * gh, 5.0, 5.0 * (T_BREAK - T_FORM)),
    ]
    for sid, sprite, colour, opacity, emissive, roll, angle in breaks:
        add(node(sid, "particle_system", {
            "max_particles": 4, "lifetime": break_life, "size": SHELL_QUAD,
            "size_over_life": curve([(0.0, 1.0), (1.0, 1.13)]),
            "rotation": round(angle, 3), "angular_velocity": roll,
            "color": colour,
            "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.25, [1.25, 1.25, 1.25, 1.0]],
                                [1.0, [0.7, 0.7, 0.7, 1.0]]],
            "opacity": opacity, "opacity_over_life": curve([(0.0, 0.0), (0.2, 1.0), (0.75, 0.85), (1.0, 0.0)]),
            "emissive": round(emissive, 3), "render_mode": "billboard", "blend": "additive",
            "soft_particle_distance": 0.12,
        }, inputs={"sprite": sprite, "material": "mat_break"}))
        add(node(sid.replace("ps_", "e_"), "emitter", {
            "shape": "point", "position": [0.0, CY, 0.0], "rate": 0.0, "burst_count": 1, "burst_times": [0.0],
            "velocity": 0.0, "start_time": T_BREAK,
        }, layer="breakup", inputs={"particle": sid}))

    add(node("flake_mesh", "mesh", {"primitive": "shard", "radius": 0.5, "height": 1.0, "segments": 5,
                                    "variants": 4, "irregularity": 0.6, "visible": False}))
    add(node("ps_flake", "particle_system", {
        "max_particles": 90, "lifetime": 0.5, "lifetime_variance": 0.16, "size": 0.125, "size_variance": 0.06,
        "size_over_life": curve([(0.0, 0.2), (0.12, 1.0), (0.6, 0.75), (1.0, 0.0)]),
        "color": c(mix(mid, hot, 0.4)), "opacity": 0.85, "emissive": 1.0,
        "render_mode": "mesh", "blend": "additive", "orientation": "tumble",
        "angular_velocity": 210.0, "angular_velocity_variance": 120.0,
        "mesh_scale": [1.0, 1.2, 1.0], "mesh_scale_variance": [0.3, 0.4, 0.3], "drag": 1.3,
    }, inputs={"mesh": "flake_mesh", "material": "mat_flake", "forces": ["f_fall"], "colliders": ["ground"]}))
    add(node("e_flake", "emitter", {
        "shape": "sphere", "radius": R_SPHERE, "surface_only": True, "position": [0.0, CY, 0.0],
        "rate": 0.0, "burst_count": 24, "burst_times": [0.0, 0.1], "start_time": T_BREAK + 0.03,
        "velocity": 1.5, "velocity_variance": 0.9, "direction": [0.0, 0.0, 0.0],
    }, layer="breakup", inputs={"particle": "ps_flake"}))
    add(node("ps_burst", "particle_system", {
        "max_particles": 400, "lifetime": 0.46, "lifetime_variance": 0.2, "size": 0.05, "size_variance": 0.025,
        "size_over_life": curve([(0.0, 0.5), (0.2, 1.0), (1.0, 0.05)]),
        "color": c(mix(hot, white, 0.3)), "color_over_life": [[0.0, c(hot)], [0.5, c(mid)], [1.0, c(deep, 1.0, 1.5)]],
        "opacity_over_life": curve([(0.0, 1.0), (0.6, 0.8), (1.0, 0.0)]),
        "emissive": round(3.6 * gh, 3), "render_mode": "stretched_billboard", "velocity_stretch": 0.15,
        "blend": "additive", "drag": 3.2, "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_fall", "f_curl"],
               "colliders": ["ground"]}))
    add(node("e_burst", "emitter", {
        "shape": "sphere", "radius": R_SPHERE, "surface_only": True, "position": [0.0, CY, 0.0],
        "rate": 0.0, "burst_count": 70, "burst_times": [0.0, 0.09], "start_time": T_BREAK + 0.02,
        "velocity": 2.6, "velocity_variance": 1.5, "direction": [0.0, 0.0, 0.0],
    }, layer="breakup", inputs={"particle": "ps_burst"}))

    # ---------------- lights ----------------
    fire = accent == "fire"
    add(node("shield_light", "light", {
        "light_type": "point", "position": [0.0, CY, 0.55], "color": c(p["light"]), "radius": 5.5,
        "flicker_amplitude": 0.3 if fire else 0.06, "flicker_frequency": 11.0 if fire else 7.0,
        "intensity": env([(0.0, 0.0), (0.1, 1.2), (0.3, 3.0), (0.46, 7.5), (0.62, 5.2), (2.0, 5.0),
                          (2.08, 8.5), (2.3, 2.2), (2.55, 0.0)], g),
    }, layer="light"))
    add(node("ground_light", "light", {
        "light_type": "point", "position": [0.0, 0.3, 0.0], "color": c(mix(mid, hot, 0.3)), "radius": 4.0,
        "flicker_amplitude": 0.22 if fire else 0.0, "flicker_frequency": 9.0,
        "intensity": env([(0.0, 0.0), (0.06, 2.4), (0.25, 1.6), (2.0, 1.4), (2.4, 0.9), (2.58, 0.0)], g),
    }, layer="light"))

    # ---------------- camera ----------------
    add(node("cam", "camera", {"position": [0.0, 2.05, 6.4], "target": [0.0, 1.02, 0.0], "fov": 40.0}))

    # ---------------- controls (identical ids and bindings in all eight) ----------------
    def bind(node_id: str, parameter: str, operation: str = "multiply") -> dict[str, str]:
        return {"node": node_id, "parameter": parameter, "op": operation}

    def control(cid: str, label: str, group: str, bindings: list[dict[str, str]], lo: float = 0.0,
                hi: float = 3.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": 1.0, "value": 1.0,
                "step": step, "unit": "x", "bindings": bindings}

    shell_ids = ["ps_shell_rim", "ps_shell_cells", "ps_shell_back", "ps_shell_core", "ps_break_rim",
                 "ps_break_cells"]
    size_bindings = [bind(sid, "size") for sid in shell_ids + ["ps_emblem", "ps_emblem_glow"]]
    size_bindings += [bind(eid, "velocity") for eid in ("e_shell_rim", "e_shell_cells", "e_shell_back",
                                                        "e_shell_core", "e_emblem", "e_emblem_glow")]
    size_bindings += [bind(eid, "position") for eid in ("e_break_rim", "e_break_cells")]
    for eid in ("e_flake", "e_burst", "e_glint"):
        size_bindings += [bind(eid, "position"), bind(eid, "radius")]
    for hub in ("swirl_hub_a", "swirl_hub_b", "rune_hub"):
        size_bindings += [bind(hub, "position"), bind(hub, "scale")]
    for eid, *_ in shard_rings:
        size_bindings += [bind(eid, "position"), bind(eid, "radius"), bind(eid, "inner_radius")]
    size_bindings += [bind("d_rings", "size"), bind("d_runes", "size"), bind("d_pool", "size"),
                      bind("f_orbit", "position")]

    controls = [
        control("barrier_intensity", "Barrier glow", "Barrier",
                [b for sid in shell_ids for b in (bind(sid, "color"), bind(sid, "emissive"))]),
        control("barrier_size", "Barrier size", "Barrier", size_bindings, lo=0.6, hi=1.5),
        control("emblem_intensity", "Emblem glow", "Shield emblem",
                [bind("ps_emblem", "color"), bind("ps_emblem", "emissive"), bind("ps_emblem_glow", "color"),
                 bind("ps_emblem_glow", "emissive")]),
        control("shard_amount", "Crystal shards", "Crystal shards",
                [bind(eid, "burst_count") for eid, *_ in shard_rings], step=0.05),
        control("ring_intensity", "Ground ring glow", "Ground ring",
                [b for did in ("d_rings", "d_runes", "d_pool") for b in (bind(did, "color"), bind(did, "emissive"))]
                + [bind("ground_light", "intensity")]),
        control("sparks", "Sparks and embers", "Sparks and embers",
                [bind("e_mote", "rate"), bind("e_glint", "rate"), bind("e_streak", "rate"),
                 bind("e_accent", "rate"), bind("e_burst", "burst_count"), bind("e_flake", "burst_count")]),
        {"id": "global_speed", "label": "Speed", "group": "Global", "min": 0.25, "max": 4.0, "default": 1.0,
         "value": 1.0, "step": 0.05, "unit": "x",
         "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]},
    ]

    return {
        "schema_version": "0.1.0",
        "name": spec["name"],
        "description": spec["description"],
        "duration": DURATION,
        "seed": seed,
        "timeline": {"phases": [{"name": n, "start": s, "end": e} for n, s, e in PHASES]},
        "layers": [
            {"id": "ground", "name": "Ground ring", "role": "telegraph"},
            {"id": "cast", "name": "Cast glow", "role": "ignition"},
            {"id": "barrier", "name": "Barrier", "role": "primary"},
            {"id": "emblem", "name": "Shield emblem", "role": "primary"},
            {"id": "swirl", "name": "Light swirl", "role": "secondary"},
            {"id": "shards", "name": "Crystal shards", "role": "secondary"},
            {"id": "sparks", "name": "Sparks and embers", "role": "secondary"},
            {"id": "runes", "name": "Rune band", "role": "secondary"},
            {"id": "accent", "name": "Elemental accent", "role": "secondary"},
            {"id": "breakup", "name": "Dissipation", "role": "aftermath"},
            {"id": "light", "name": "Lighting", "role": "interaction"},
        ],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/shield_variants.py",
            "variant": slug,
            "style": spec["style"],
            "element": accent or "none",
            "target": "a single target standing at the origin; nothing is drawn for it",
            "analysis": "cast 0-0.2 concentric ground rings ignite and a soft glow rises from the centre | "
                        "formation 0.2-0.4 the cellular sphere grows up out of the ring, sparks rise | "
                        "shield_appears 0.4-0.6 the 2.2 m sphere completes and the heater-shield emblem "
                        "materialises inside it | active 0.6-2.0 comets orbit on tilted rings, crystal "
                        "shards float and tumble, soft streaks rise, the rings pulse | dissipate 2.0-2.4 "
                        "the shell dissolves into flakes and sparks, the emblem fades | fade 2.4-2.6 only "
                        "the ground ring glow and a few sparks remain",
            "render_settings": {
                "background": [0.0, 0.0, 0.0, 1.0],
                "ground_albedo": 0.06,
                "bloom_intensity": 0.22,
                "bloom_radius": 0.045,
                "exposure": 0.85,
                "grid": False,
            },
            "tags": spec["tags"],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="directory to write the eight effect JSON files into")
    ap.add_argument("--only", default="", help="comma-separated slugs to write (default: all eight)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = {s for s in args.only.split(",") if s}
    for slug, spec in VARIANTS.items():
        if only and slug not in only:
            continue
        effect = build(slug, spec)
        path = out / f"{slug}.json"
        path.write_text(json.dumps(effect, indent=1) + "\n", encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes)")


if __name__ == "__main__":
    main()
