#!/usr/bin/env python3
"""Generate the six AetherFX "Heal" effects from one parametrised description.

Every variant is built by :func:`build` from the same node graph, so all six
documents carry an identical set of node ids (a variant that does not want a
feature disables its nodes instead of deleting them).  Game code can therefore
drive any of the six through the same handles.

    python tools/generators/heal_variants.py --out examples/effects

The output is deterministic: no RNG, no wall clock, no absolute paths.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

Vec = list[float]


def r4(x: float) -> float:
    """Round so the documents stay byte-stable and readable."""
    return round(float(x), 4) + 0.0


def rgba(c: Vec, a: float = 1.0, mul: float = 1.0) -> Vec:
    return [r4(c[0] * mul), r4(c[1] * mul), r4(c[2] * mul), r4(a)]


def track(keys: list[tuple[float, Any]], interp: str = "smooth") -> dict[str, Any]:
    """A keyframed parameter: {"value": first, "track": [...]}"""
    assert keys, "empty track"
    ordered = sorted(keys, key=lambda k: k[0])
    return {
        "value": ordered[0][1],
        "track": [{"time": r4(t), "value": v, "interp": interp} for t, v in ordered],
    }


def curve(points: list[tuple[float, float]]) -> list[list[float]]:
    return [[r4(t), r4(v)] for t, v in points]


def grad(points: list[tuple[float, Vec]]) -> list[list[Any]]:
    return [[r4(t), [r4(c[0]), r4(c[1]), r4(c[2]), r4(c[3])]] for t, c in points]


def node(nid: str, ntype: str, params: dict[str, Any], **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": nid, "type": ntype}
    if kw.get("layer"):
        out["layer"] = kw["layer"]
    if kw.get("parent"):
        out["parent"] = kw["parent"]
    if kw.get("enabled") is False:
        out["enabled"] = False
    out["parameters"] = params
    if kw.get("inputs"):
        out["inputs"] = kw["inputs"]
    if kw.get("metadata"):
        out["metadata"] = kw["metadata"]
    return out


def golden(i: int, a: float, b: float, offset: float = 0.0) -> float:
    """Deterministic, evenly spread pseudo-random value in [a, b)."""
    u = (offset + i * 0.6180339887498949) % 1.0
    return a + (b - a) * u


# --------------------------------------------------------------------------
# scheduling: one master 1.8 s envelope, warped per variant
# --------------------------------------------------------------------------


class Clock:
    """Turns master-timeline keys into this variant's keyframe tracks.

    Non-looping variants just scale the key times by ``time_scale``.  The
    looping variant (Regen) throws the one-shot shape away and replaces it with
    a periodic pulse that has the same peak, so value(0) == value(duration) and
    the 3 s playback loops without a seam.
    """

    def __init__(self, p: dict[str, Any]):
        self.dur = float(p["duration"])
        self.scale = float(p.get("time_scale", 1.0))
        self.loop = bool(p.get("loop"))
        self.pulses = int(p.get("loop_pulses", 3))
        self.floor = float(p.get("loop_floor", 0.4))
        self.steps = int(p.get("loop_steps", 10))

    def t(self, master: float) -> float:
        """Map one master time onto this variant's timeline."""
        if self.loop:
            return min(self.dur, max(0.0, master * self.dur / 1.8))
        return min(self.dur, max(0.0, master * self.scale))

    def start(self, master: float) -> float:
        return 0.0 if self.loop else self.t(master)

    def pulse(self, t: float, shift: float = 0.0) -> float:
        """0..1, one hump per pulse period, exactly 0 at t=0 and t=duration."""
        u = (t / self.dur) * self.pulses - shift
        return 0.5 - 0.5 * math.cos(2.0 * math.pi * u)

    def env(
        self,
        keys: list[tuple[float, float]],
        floor: float | None = None,
        shift: float = 0.0,
        cap: float | None = None,
    ) -> dict[str, Any]:
        """Keyframe track from master-timeline (time, value) pairs.

        ``cap`` clamps the values (parameters such as decal opacity are [0..1],
        so a variant that scales past 1 must carry the extra as emissive)."""
        def clip(v: float) -> float:
            return r4(min(v, cap) if cap is not None else v)

        if not self.loop:
            return track([(self.t(t), clip(v)) for t, v in keys])
        peak = max(v for _, v in keys)
        lo = peak * (self.floor if floor is None else floor)
        n = self.pulses * self.steps
        out: list[tuple[float, float]] = []
        for i in range(n + 1):
            t = self.dur * i / n
            out.append((t, clip(lo + (peak - lo) * self.pulse(t, shift))))
        return track(out)

    def env_vec(self, keys: list[tuple[float, Vec]]) -> Any:
        """Vector track (beam targets): times warped, values kept.  A looping
        variant holds the tallest value instead - a one-shot ramp would not
        match itself at the seam."""
        if self.loop:
            return max((v for _, v in keys), key=lambda v: v[1])
        return track([(self.t(t), v) for t, v in keys])

    def env_pos(self, keys: list[tuple[float, float]]) -> dict[str, Any]:
        """A y-only position ramp; a looping variant repeats it once per pulse
        and snaps back while the emitter rate is 0."""
        if not self.loop:
            return track([(self.t(t), [0.0, r4(y), 0.0]) for t, y in keys], interp="linear")
        (t0, y0), (t1, y1) = keys[0], keys[-1]
        span = self.dur / self.pulses
        out: list[tuple[float, Vec]] = []
        for k in range(self.pulses):
            base = k * span
            out.append((r4(base), [0.0, r4(y0), 0.0]))
            out.append((r4(base + min(span * 0.84, t1 - t0)), [0.0, r4(y1), 0.0]))
            out.append((r4(base + span * 0.94), [0.0, r4(y0), 0.0]))
        out.append((r4(self.dur), [0.0, r4(y0), 0.0]))
        return track(out, interp="linear")


# --------------------------------------------------------------------------
# procedural textures (grayscale masks; colour comes from particles/materials)
# --------------------------------------------------------------------------


def bell(prefix: str, angle: float, in_low: float, out_high: float = 1.0,
         gamma: float = 1.0) -> list[dict[str, Any]]:
    """A soft ridge of light through the centre, perpendicular to `angle`.

    House style forbids `star`/`spokes` with two or four arms - those bake a
    hard-edged cross.  Two opposed linear gradients `min`ed together give a
    triangular ridge (peak 0.5 at the centre line, 0 at both edges) which
    `levels` reshapes into a soft band with no hard edge anywhere.  `in_low`
    is 0.5 - half-width, so 0.0 is the full texture and 0.4 a thin core.
    """
    a, b, t = prefix + "_a", prefix + "_b", prefix + "_t"
    return [
        {"id": a, "op": "gradient_linear", "params": {"angle": angle, "start": 0.0, "end": 1.0}},
        {"id": b, "op": "gradient_linear", "params": {"angle": angle + 180.0,
                                                      "start": 0.0, "end": 1.0}},
        {"id": t, "op": "math", "params": {"mode": "min"}, "inputs": {"a": a, "b": b}},
        {"id": prefix, "op": "levels",
         "params": {"in_low": in_low, "in_high": 0.5, "out_high": out_high, "gamma": gamma},
         "inputs": {"a": t}},
    ]


def tex_ring_graph(p: dict[str, Any]) -> dict[str, Any]:
    """Several thin concentric rings plus a bright inner disc."""
    rings = p["ring_radii"]
    nodes: list[dict[str, Any]] = []
    prev = None
    for i, (radius, thick, level) in enumerate(rings):
        rid = f"r{i}"
        nodes.append(
            {
                "id": rid,
                "op": "ring",
                "params": {"radius": radius, "thickness": thick, "softness": max(0.003, thick * 0.55)},
            }
        )
        lid = f"r{i}l"
        nodes.append({"id": lid, "op": "levels", "params": {"out_high": level}, "inputs": {"a": rid}})
        if prev is None:
            prev = lid
        else:
            mid = f"m{i}"
            nodes.append({"id": mid, "op": "math", "params": {"mode": "max"}, "inputs": {"a": prev, "b": lid}})
            prev = mid
    # bright inner disc
    nodes += [
        {"id": "disc", "op": "gradient_radial", "params": {"radius": 0.185, "falloff": "quadratic"}},
        {"id": "discl", "op": "levels", "params": {"in_high": 1.0, "out_high": 0.72}, "inputs": {"a": "disc"}},
        {"id": "md", "op": "math", "params": {"mode": "max"}, "inputs": {"a": prev, "b": "discl"}},
        # faint fill inside the outer ring so the circle reads as a pool of light
        {"id": "fill", "op": "gradient_radial", "params": {"radius": 0.475, "falloff": "linear"}},
        {"id": "filln", "op": "levels", "params": {"out_high": 0.11}, "inputs": {"a": "fill"}},
        {"id": "out", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "md", "b": "filln"}},
    ]
    return {"nodes": nodes, "output": "out"}


TEX_RING_SPIN = {
    "nodes": [
        {"id": "ra", "op": "ring", "params": {"radius": 0.42, "thickness": 0.009, "softness": 0.005}},
        {"id": "rb", "op": "ring", "params": {"radius": 0.315, "thickness": 0.006, "softness": 0.004}},
        {"id": "rbl", "op": "levels", "params": {"out_high": 0.7}, "inputs": {"a": "rb"}},
        {"id": "tk", "op": "spokes",
         "params": {"count": 24, "width": 0.007, "softness": 0.004, "inner_radius": 0.335, "outer_radius": 0.405}},
        {"id": "tkl", "op": "levels", "params": {"out_high": 0.85}, "inputs": {"a": "tk"}},
        {"id": "rc", "op": "ring", "params": {"radius": 0.24, "thickness": 0.004, "softness": 0.004}},
        {"id": "rcl", "op": "levels", "params": {"out_high": 0.45}, "inputs": {"a": "rc"}},
        {"id": "m0", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "ra", "b": "rbl"}},
        {"id": "m1", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m0", "b": "tkl"}},
        {"id": "out", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m1", "b": "rcl"}},
    ],
    "output": "out",
}

TEX_POOL = {
    "nodes": [
        {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "quadratic"}},
        {"id": "n", "op": "fbm", "params": {"frequency": 2.6, "octaves": 3, "seed": 11}},
        {"id": "nl", "op": "levels", "params": {"in_low": 0.2, "in_high": 0.9, "out_low": 0.6, "out_high": 1.0},
         "inputs": {"a": "n"}},
        {"id": "out", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "r", "b": "nl"}},
    ],
    "output": "out",
}

TEX_CORE = {
    "nodes": [{"id": "out", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "quadratic"}}],
    "output": "out",
}

TEX_HAZE = {
    "nodes": [
        {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
        {"id": "n", "op": "fbm", "params": {"frequency": 2.2, "octaves": 4, "seed": 29, "animate": 1.0}},
        {"id": "nl", "op": "levels", "params": {"in_low": 0.18, "in_high": 0.92, "out_low": 0.32, "out_high": 1.0},
         "inputs": {"a": "n"}},
        {"id": "out", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "r", "b": "nl"}},
    ],
    "output": "out",
}


def tex_star_graph(p: dict[str, Any]) -> dict[str, Any]:
    """A sparkle: a soft bokeh glint, not a four-point star.

    Four-point stars are banned by house style (they are crosses) and a field
    of them read as stamped clip-art anyway, because a 4-fold symmetric sprite
    looks identical however `rotation_variance` turns it.  A hot dot inside a
    bloom, plus a faint soft smear that random rotation actually changes, gives
    glints that all sit differently."""
    nodes: list[dict[str, Any]] = [
        {"id": "hot", "op": "gradient_radial",
         "params": {"radius": p["star_core"], "falloff": "quadratic"}},
        {"id": "bloom0", "op": "gradient_radial",
         "params": {"radius": p["star_bloom"], "falloff": "quadratic"}},
        {"id": "bloom", "op": "levels", "params": {"out_high": p["star_glow"]},
         "inputs": {"a": "bloom0"}},
    ]
    nodes += bell("gx", 0.0, r4(0.5 - p["star_width"] * 3.0), 1.0, 0.9)
    nodes += bell("gy", 90.0, 0.26, 1.0, 1.6)
    nodes += [
        {"id": "wide0", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
        {"id": "wide", "op": "levels", "params": {"out_high": r4(p["star_glow"] * 0.3)},
         "inputs": {"a": "wide0"}},
        {"id": "sm0", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "gx", "b": "gy"}},
        {"id": "sm", "op": "levels", "params": {"out_high": 0.24}, "inputs": {"a": "sm0"}},
        {"id": "m1", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "hot", "b": "bloom"}},
        {"id": "m2", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m1", "b": "sm"}},
        {"id": "m3", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m2", "b": "wide"}},
        {"id": "out", "op": "blur", "params": {"radius": 2.0}, "inputs": {"a": "m3"}},
    ]
    return {"nodes": nodes, "output": "out"}


def tex_streak_graph(p: dict[str, Any]) -> dict[str, Any]:
    """A shaft of light: soft across its width, tapered at both ends.

    Built only from gradients.  `spokes`/`star` with two arms bakes a hard bar
    (and is a banned motif), and even softened it read as a straw; this is a
    bell across the shaft times a long plateau along it, broken up by drifting
    fbm, so many overlapping sprites melt into a volume of light."""
    nodes: list[dict[str, Any]] = []
    nodes += bell("bx", 0.0, r4(0.5 - p["streak_body"] * 1.15), 1.0, 0.55)
    nodes += bell("by", 90.0, 0.0, 1.0, 1.7)
    nodes += bell("cx", 0.0, r4(0.5 - p["streak_core"] * 1.6), 1.0, 0.7)
    nodes += [
        # a radial falloff on top guarantees the sprite dies to nothing well
        # inside the quad: any residual edge would read as a hard line
        {"id": "round", "op": "gradient_radial", "params": {"radius": 0.55, "falloff": "smooth"}},
        {"id": "body0", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "bx", "b": "by"}},
        {"id": "body", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "body0", "b": "round"}},
        {"id": "fib", "op": "fbm",
         "params": {"frequency": 2.0, "octaves": 3, "seed": 19, "animate": 1.0}},
        {"id": "fil", "op": "levels",
         "params": {"in_low": 0.18, "in_high": 0.86, "out_low": 0.82, "out_high": 1.0},
         "inputs": {"a": "fib"}},
        {"id": "bodyn", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "body", "b": "fil"}},
        {"id": "core0", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "cx", "b": "by"}},
        {"id": "core", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "core0", "b": "round"}},
        {"id": "corel", "op": "levels", "params": {"out_high": 0.9}, "inputs": {"a": "core"}},
        {"id": "mx", "op": "math", "params": {"mode": "max"},
         "inputs": {"a": "bodyn", "b": "corel"}},
        {"id": "out", "op": "blur", "params": {"radius": 5.0}, "inputs": {"a": "mx"}},
    ]
    return {"nodes": nodes, "output": "out"}


def tex_corona_graph(p: dict[str, Any]) -> dict[str, Any]:
    """The light core: a soft luminous swell, brightest at its centre.

    It has no arms of any kind.  The first version drew a four-armed star with
    a long vertical arm and the user rejected it as a cross, and the house rule
    now forbids two- and four-armed motifs outright, so this is radial falloff
    plus a gentle vertical smear that blends the core into the pillar."""
    nodes: list[dict[str, Any]] = [
        {"id": "hot", "op": "gradient_radial",
         "params": {"radius": p["core_hot"], "falloff": "quadratic"}},
        {"id": "mid0", "op": "gradient_radial",
         "params": {"radius": r4(p["core_hot"] * 2.4), "falloff": "quadratic"}},
        {"id": "mid", "op": "levels", "params": {"out_high": 0.58}, "inputs": {"a": "mid0"}},
        {"id": "wide0", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
        {"id": "wide", "op": "levels", "params": {"out_high": 0.2}, "inputs": {"a": "wide0"}},
    ]
    nodes += bell("vx", 0.0, r4(0.5 - p["core_lens"] * 0.42), 1.0, 0.6)
    nodes += bell("vy", 90.0, 0.0, 1.0, 1.8)
    nodes += [
        {"id": "vm0", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "vx", "b": "vy"}},
        {"id": "vround", "op": "gradient_radial", "params": {"radius": 0.55, "falloff": "smooth"}},
        {"id": "vm", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "vm0", "b": "vround"}},
        {"id": "vl", "op": "levels", "params": {"out_high": 0.5}, "inputs": {"a": "vm"}},
        {"id": "m1", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "hot", "b": "mid"}},
        {"id": "m2", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m1", "b": "wide"}},
        {"id": "m3", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m2", "b": "vl"}},
        {"id": "out", "op": "blur", "params": {"radius": 4.0}, "inputs": {"a": "m3"}},
    ]
    return {"nodes": nodes, "output": "out"}


# The ribbon's cross-width profile: V is a soft bell - a thin bright centre
# line fading to nothing at both edges - and the image is constant along U, so
# the ribbon tiles without a seam however long the trail grows.  Without it a
# trail draws a flat opaque tape with a hard edge down each side.
TEX_RIBBON = {
    "nodes": [
        {"id": "up", "op": "gradient_linear", "params": {"angle": 90, "start": 0.0, "end": 1.0}},
        {"id": "dn", "op": "gradient_linear", "params": {"angle": 270, "start": 0.0, "end": 1.0}},
        {"id": "tri", "op": "math", "params": {"mode": "min"}, "inputs": {"a": "up", "b": "dn"}},
        {"id": "skirt", "op": "levels",
         "params": {"in_low": 0.0, "in_high": 0.5, "out_high": 0.55, "gamma": 1.6},
         "inputs": {"a": "tri"}},
        {"id": "core", "op": "levels",
         "params": {"in_low": 0.2, "in_high": 0.5, "out_high": 1.0, "gamma": 1.2},
         "inputs": {"a": "tri"}},
        {"id": "mx", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "skirt", "b": "core"}},
        {"id": "out", "op": "blur", "params": {"radius": 3.0}, "inputs": {"a": "mx"}},
    ],
    "output": "out",
}


def tex_feather_graph(p: dict[str, Any]) -> dict[str, Any]:
    """A drifting feather (a leaf for Regen), built only from gradients.

    A ridge across the feather's axis times a strong taper along it gives a
    plume that is broad and round at the quill and closes to a point at the
    tip; a symmetric spindle (which is what a two-point `star` bakes, and a
    banned motif besides) reads as a grey crystal lozenge instead."""
    leaf = bool(p["leaf_motes"])
    ang = 101.0            # the feather's axis
    perp = ang - 90.0      # ridge runs perpendicular to the gradient direction
    vane_w = 0.2 if leaf else 0.125
    barb_f = 5.0 if leaf else 9.0
    shaft_w = 0.035 if leaf else 0.022
    nodes: list[dict[str, Any]] = []
    nodes += bell("vw", perp, r4(0.5 - vane_w), 1.0, 0.8)
    nodes += bell("qw", perp, r4(0.5 - shaft_w), 1.0, 0.95)
    nodes += [
        {"id": "tap", "op": "gradient_linear",
         "params": {"angle": ang, "start": 1.0, "end": 0.04}},
        {"id": "edge", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "linear"}},
        {"id": "vane0", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "vw", "b": "tap"}},
        {"id": "vane1", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "vane0", "b": "edge"}},
        {"id": "vane", "op": "levels", "params": {"in_high": 0.55, "out_high": 1.0},
         "inputs": {"a": "vane1"}},
        {"id": "barb", "op": "fbm", "params": {"frequency": barb_f, "octaves": 2, "seed": 41}},
        {"id": "barbl", "op": "levels",
         "params": {"in_low": 0.2, "in_high": 0.9, "out_low": 0.74, "out_high": 1.0},
         "inputs": {"a": "barb"}},
        {"id": "vaneb", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "vane", "b": "barbl"}},
        {"id": "quill0", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "qw", "b": "tap"}},
        {"id": "quill1", "op": "math", "params": {"mode": "multiply"},
         "inputs": {"a": "quill0", "b": "edge"}},
        {"id": "quill", "op": "levels", "params": {"in_high": 0.6, "out_high": 0.95},
         "inputs": {"a": "quill1"}},
        {"id": "flat", "op": "math", "params": {"mode": "max"},
         "inputs": {"a": "vaneb", "b": "quill"}},
        {"id": "warp", "op": "fbm", "params": {"frequency": 1.4, "octaves": 2, "seed": 7}},
        {"id": "bent", "op": "distort", "params": {"amount": 0.18 if leaf else 0.13},
         "inputs": {"a": "flat", "by": "warp"}},
        {"id": "out", "op": "blur", "params": {"radius": 2.6}, "inputs": {"a": "bent"}},
    ]
    return {"nodes": nodes, "output": "out"}


# --------------------------------------------------------------------------
# the helix the spiral ribbon rides
# --------------------------------------------------------------------------


def helix_keys(clock: Clock, p: dict[str, Any], phase_deg: float,
               rmul: float = 1.0, ymul: float = 1.0) -> list[tuple[float, Vec]]:
    sp = p["spiral"]
    t0, t1 = clock.t(sp["t0"]), clock.t(sp["t1"])
    steps = sp["steps"]
    phase = math.radians(phase_deg)
    keys: list[tuple[float, Vec]] = []
    for i in range(steps + 1):
        u = i / steps
        # rises quickly, then settles into the flare
        uy = u ** 0.88
        r = (sp["r0"] + (sp["r1"] - sp["r0"]) * u) * rmul
        ang = phase + 2.0 * math.pi * sp["turns"] * u
        y = (sp["y0"] + (sp["y1"] - sp["y0"]) * uy) * ymul
        keys.append((t0 + (t1 - t0) * u, [r4(r * math.cos(ang)), r4(y), r4(r * math.sin(ang))]))
    _ = t1
    return keys


# --------------------------------------------------------------------------
# the effect
# --------------------------------------------------------------------------


def build(slug: str, p: dict[str, Any]) -> dict[str, Any]:
    c = Clock(p)
    pal = p["pal"]
    S = p["intensity"]
    dur = p["duration"]
    sp = p["spiral"]
    spiral_on = sp["beads"] > 0
    nodes: list[dict[str, Any]] = []
    add = nodes.append

    # ---------------- textures ----------------
    add(node("tex_ring", "texture", {"width": 512, "height": 512, "graph": tex_ring_graph(p)}))
    add(node("tex_ring_spin", "texture", {"width": 512, "height": 512, "graph": TEX_RING_SPIN}))
    add(node("tex_pool", "texture", {"width": 256, "height": 256, "graph": TEX_POOL}))
    add(node("tex_core", "texture", {"width": 64, "height": 64, "graph": TEX_CORE}))
    add(node("tex_star", "texture", {"width": 128, "height": 128, "graph": tex_star_graph(p)}))
    add(node("tex_streak", "texture",
             {"width": 96, "height": 256, "frames": 10, "graph": tex_streak_graph(p)}))
    add(node("tex_haze", "texture", {"width": 128, "height": 128, "frames": 8, "graph": TEX_HAZE}))
    add(node("tex_corona", "texture",
             {"width": 256, "height": 256, "graph": tex_corona_graph(p)}))
    add(node("tex_ribbon", "texture", {"width": 32, "height": 128, "graph": TEX_RIBBON}))
    add(node("tex_feather", "texture", {"width": 128, "height": 128, "graph": tex_feather_graph(p)}))

    # ---------------- materials ----------------
    add(node("mat_soft", "material",
             {"blend": "additive", "soft_particle": True, "depth_fade": 0.12}))
    add(node("mat_glow", "material",
             {"blend": "additive", "emissive_color": pal["warm"], "emissive_intensity": 0.5,
              "soft_particle": True, "depth_fade": 0.16, "dissolve": 0.1, "erosion": 0.18}))
    add(node("mat_core", "material",
             {"blend": "additive", "emissive_color": pal["hot"], "emissive_intensity": 1.2,
              "soft_particle": True, "depth_fade": 0.09}))
    add(node("mat_haze", "material",
             {"blend": "additive", "emissive_color": pal["haze"], "emissive_intensity": 0.25,
              "soft_particle": True, "depth_fade": 0.45, "dissolve": 0.2, "erosion": 0.35}))
    add(node("mat_ribbon", "material",
             {"blend": "additive", "emissive_color": pal["mid"], "emissive_intensity": 1.0,
              "soft_particle": True, "depth_fade": 0.12},
             inputs={"base_texture": "tex_ribbon"}))
    add(node("mat_bead", "material",
             {"blend": "additive", "base_color": pal["hot"], "emissive_color": pal["hot"],
              "emissive_intensity": 5.0}))

    # ---------------- forces ----------------
    add(node("f_lift", "force", {"force_type": "buoyancy", "strength": 0.8}))
    add(node("f_lift_soft", "force", {"force_type": "buoyancy", "strength": 0.8}))
    add(node("f_turb", "force",
             {"force_type": "turbulence", "strength": 0.7, "frequency": 1.1, "octaves": 2, "speed": 0.7}))
    add(node("f_curl", "force",
             {"force_type": "curl_noise", "strength": 0.55, "frequency": 0.9, "octaves": 2, "speed": 0.5}))
    add(node("f_swirl", "force",
             {"force_type": "vortex", "strength": p["swirl"], "direction": [0, 1, 0],
              "position": [0, 0.9, 0], "radius": 2.2, "falloff": "smooth"}))
    add(node("f_hug", "force",
             {"force_type": "attractor", "strength": 1.3, "position": [0, 1.0, 0],
              "radius": 2.4, "falloff": "linear"}))

    # ---------------- camera ----------------
    add(node("cam", "camera",
             {"position": [0.0, 1.6, 5.2], "target": [0.0, 1.1, 0.0], "fov": 40}))

    # ================= ground ring =================
    ring_scale = p["ring_scale"]
    add(node("ring_pool", "decal",
             {"shape": "circle", "size": [r4(2.9 * ring_scale), r4(2.9 * ring_scale)],
              "color": pal["warm"], "emissive": 0.14, "blend": "additive",
              "fade_in": 0.0, "fade_out": 0.0,
              "opacity": c.env([(0.0, 0.0), (0.16, 0.025 * S), (0.5, 0.05 * S), (1.15, 0.06 * S),
                                (1.45, 0.045 * S), (1.7, 0.018 * S), (1.8, 0.0)], floor=0.35, cap=1.0)},
             layer="ground", inputs={"texture": "tex_pool"}))
    add(node("ring_main", "decal",
             {"shape": "circle", "size": [r4(2.15 * ring_scale), r4(2.15 * ring_scale)],
              "color": pal["warm"], "emissive": p["ring_emissive"], "blend": "additive",
              "fade_in": 0.0, "fade_out": 0.0,
              "opacity": c.env([(0.0, 0.0), (0.09, 0.42 * S), (0.2, 0.62 * S), (0.36, 0.95 * S),
                                (0.75, 0.82 * S), (1.15, 1.0 * S), (1.4, 0.86 * S),
                                (1.62, 0.34 * S), (1.8, 0.0)], floor=0.45, cap=1.0)},
             layer="ground", inputs={"texture": "tex_ring"}))
    add(node("ring_spin", "decal",
             {"shape": "circle", "size": [r4(1.62 * ring_scale), r4(1.62 * ring_scale)],
              "color": pal["mid"], "emissive": p["ring_emissive"] * 0.8, "blend": "additive",
              "fade_in": 0.0, "fade_out": 0.0,
              "rotation": track([(0.0, [0.0, 0.0, 0.0]), (dur, [0.0, r4(p["ring_spin"] * dur), 0.0])],
                                interp="linear"),
              "opacity": c.env([(0.0, 0.0), (0.14, 0.2 * S), (0.4, 0.5 * S), (1.1, 0.6 * S),
                                (1.45, 0.42 * S), (1.72, 0.0)], floor=0.45, cap=1.0)},
             layer="ground", inputs={"texture": "tex_ring_spin"}))
    add(node("ring_disc", "decal",
             {"shape": "circle", "size": [r4(1.05 * ring_scale), r4(1.05 * ring_scale)],
              "color": pal["warm"], "emissive": 0.7, "blend": "additive",
              "fade_in": 0.0, "fade_out": 0.0,
              "opacity": c.env([(0.0, 0.0), (0.12, 0.035 * S), (0.34, 0.07 * S), (0.85, 0.09 * S),
                                (1.18, 0.12 * S), (1.45, 0.08 * S), (1.75, 0.0)], floor=0.4, cap=1.0)},
             layer="ground", inputs={"texture": "tex_core"}))

    # ================= pillar =================
    ph = p["pillar_h"]
    # a wider column overlaps more sprites over the same pixels, so per-sprite
    # opacity is divided by the width - otherwise Intense/Critical bleach out.
    ow = 1.0 / p["pillar_w"]
    fw = 1.0 / max(1.0, p["core_scale"])
    PR = p["pillar_rate"] * 1.45 * S   # peak pillar-sprite rate
    # the fine shafts are sprites now: `shafts` sets how many, `shaft_w` how
    # thick and `shaft_a` how bright (they used to be that many `beam` nodes)
    CR = p["pillar_rate"] * 0.2 * S * (0.4 + 0.1 * p["shafts"])   # peak shaft rate
    KR = p["sparkle_rate"] * S         # peak sparkle rate
    add(node("pillar_ps", "particle_system",
             {"max_particles": 2400, "lifetime": 0.58, "lifetime_variance": 0.24,
              "size": r4(0.62 * p["pillar_w"]), "size_variance": r4(0.24 * p["pillar_w"]),
              "size_over_life": curve([(0.0, 0.55), (0.32, 1.0), (1.0, 1.28)]),
              "color": pal["warm"],
              "color_over_life": grad([(0.0, pal["hot"]), (0.3, pal["warm"]), (1.0, pal["warm"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.18, 0.105 * ow), (0.46, 0.12 * ow),
                                          (0.74, 0.055 * ow), (1.0, 0.0)]),
              "emissive": r4(0.42 * p["emissive"]),
              "emissive_over_life": curve([(0.0, 1.2), (0.45, 0.8), (1.0, 0.18)]),
              "drag": 1.3, "blend": "additive", "render_mode": "stretched_billboard",
              "velocity_stretch": 0.1, "sprite_fps": 0.0},
             layer="pillar",
             inputs={"sprite": "tex_streak", "material": "mat_glow", "forces": ["f_lift"]}))
    # a box emitter spanning the column keeps the pillar an even body of light;
    # a disc at the base piles every sprite into a blob and the top goes sparse.
    add(node("pillar_e", "emitter",
             {"shape": "box", "size": [r4(0.76 * p["pillar_w"]), r4(ph * 0.62), r4(0.7 * p["pillar_w"])],
              "position": [0.0, r4(ph * 0.34), 0.0],
              "rate": c.env([(0.05, 0.0), (0.2, 0.09 * PR), (0.45, 0.26 * PR), (0.75, 0.5 * PR),
                             (1.05, PR), (1.3, 0.8 * PR), (1.48, 0.22 * PR), (1.64, 0.0)],
                            floor=0.3),
              "velocity": 0.55, "velocity_variance": 0.35,
              "direction": [0, 1, 0], "spread": 9, "start_time": c.start(0.05)},
             layer="pillar", inputs={"particle": "pillar_ps"}))

    add(node("core_ps", "particle_system",
             {"max_particles": 900, "lifetime": 0.5, "lifetime_variance": 0.3,
              "size": r4(0.16 * p["pillar_w"] * p["shaft_w"]),
              "size_variance": r4(0.09 * p["pillar_w"] * p["shaft_w"]),
              "size_over_life": curve([(0.0, 0.5), (0.2, 1.0), (1.0, 0.55)]),
              "color": pal["hot"],
              "color_over_life": grad([(0.0, pal["hot"]), (1.0, pal["warm"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.06, 0.16 * ow * p["shaft_a"]),
                                          (0.18, 0.3 * ow * p["shaft_a"]),
                                          (0.55, 0.15 * ow * p["shaft_a"]), (1.0, 0.0)]),
              "emissive": r4(0.8 * p["emissive"]), "drag": 0.12, "blend": "additive",
              "render_mode": "stretched_billboard", "velocity_stretch": 0.4, "sprite_fps": 0.0},
             layer="pillar",
             inputs={"sprite": "tex_streak", "material": "mat_core", "forces": ["f_lift"]}))
    add(node("core_e", "emitter",
             {"shape": "disc", "radius": r4(0.16 * p["pillar_w"]), "position": [0.0, 0.03, 0.0],
              "rate": c.env([(0.1, 0.0), (0.3, 0.29 * CR), (0.7, 0.75 * CR), (1.1, CR),
                             (1.4, 0.79 * CR), (1.6, 0.37 * CR), (1.74, 0.0)], floor=0.3),
              "velocity": r4(5.4 * math.sqrt(ph / 2.6)), "velocity_variance": 2.6,
              "direction": [0, 1, 0], "spread": 4, "start_time": c.start(0.1)},
             layer="pillar", inputs={"particle": "core_ps"}))

    # NOTE: there used to be a `beam_core` and eight `shaft0..7` beams here.
    # A beam draws a constant-width rod whose core is opaque and whose ends are
    # cut square (docs/BACKLOG.md: no taper, core alpha fixed at 1.0), which is
    # exactly the "really obvious line" house style forbids.  The crisp centre
    # and the fine shafts are carried by `core_ps` sprites instead: soft, they
    # taper at both ends, and they scatter so no two are the same length.

    # ================= spiral ribbon =================
    for bi, tag in enumerate("ab"):
        bead_on = bi < sp["beads"]
        phase_deg = sp["phase"] + bi * 180.0
        bead_params: dict[str, Any] = {
            "primitive": "sphere", "radius": r4(0.05 * sp["width"]), "segments": 10,
            "color": pal["hot"], "visible": bead_on,
            "emissive": track([(0.0, 0.0), (c.t(sp["t0"]), 0.0),
                               (c.t(sp["t0"]) + 0.05, 3.0), (c.t(1.42), 3.0),
                               (c.t(1.62), 0.9), (c.t(1.78), 0.0)]) if bead_on else 0.0,
        }
        if bead_on:
            bead_params["position"] = track(
                helix_keys(c, p, phase_deg, rmul=1.0 if bi == 0 else 1.22,
                           ymul=1.0 if bi == 0 else 0.92))
        else:
            bead_params["position"] = [r4(sp["r0"]), r4(sp["y0"]), 0.0]
        add(node(f"bead_{tag}", "mesh", bead_params, layer="spiral", inputs={"material": "mat_bead"}))

        t_on, t_off = c.t(sp["t0"]), c.t(sp["t1"])
        # Three thin strands on the same path rather than one wide band: a very
        # faint soft halo, a luminous gold body, and a short-lived white head
        # that therefore only exists near the leading edge.  All three carry
        # `tex_ribbon`, whose V is a soft bell, so none of them has a hard edge.
        # Two overlapping helices would double the additive load, so the pair
        # shares one budget.
        bmul = 1.0 if sp["beads"] < 2 else 0.8
        for kind, wmul, alpha, col, emis, life in (
            ("halo", 2.2, 0.09 * bmul, pal["mid"], 0.55 * bmul, 1.4),
            ("", 1.0, 0.2 * bmul, pal["warm"], 1.5 * bmul, 1.4),
            ("core", 0.3, 0.45 * bmul, pal["hot"], 2.8 * bmul, 0.34),
        ):
            tid = f"ribbon_{tag}" + (f"_{kind}" if kind else "")
            width = 0.3 * sp["width"] * wmul
            add(node(tid, "trail",
                     {"width": track([(t_on, r4(width * 0.35)), (t_on + 0.12, r4(width)),
                                      (c.t(1.48), r4(width)), (c.t(1.68), r4(width * 0.6)),
                                      (c.t(1.79), 0.001)]),
                      "lifetime": life,
                      "taper": curve([(0.0, 1.0), (0.45, 0.95), (0.8, 0.7), (1.0, 0.15)]),
                      "opacity_over_life": curve([(0.0, 1.0), (0.18, 0.86), (0.6, 0.5),
                                                  (1.0, 0.0)]),
                      "color": rgba(col, r4(alpha)), "emissive": track(
                          [(t_on, r4(emis * p["emissive"] * 0.4)),
                           (t_on + 0.15, r4(emis * p["emissive"])),
                           (c.t(1.48), r4(emis * p["emissive"])),
                           (c.t(1.68), r4(emis * p["emissive"] * 0.5)),
                           (c.t(1.8), 0.0)]),
                      "blend": "additive", "noise_amplitude": 0.012, "noise_frequency": 2.2,
                      "min_vertex_distance": 0.01, "max_segments": 400,
                      "start_time": t_on, "duration": r4(dur - t_on)},
                     layer="spiral", enabled=bead_on,
                     inputs={"source": f"bead_{tag}", "material": "mat_ribbon"}))
        _ = t_off

        add(node(f"spark_{tag}_e", "emitter",
                 {"shape": "sphere", "radius": r4(0.07 * sp["width"]),
                  "rate": c.env([(sp["t0"], 0.0), (sp["t0"] + 0.06, 380.0 * S * bmul * bmul),
                                 (sp["t1"] - 0.1, 460.0 * S * bmul * bmul),
                                 (sp["t1"] + 0.12, 150.0 * S * bmul * bmul),  # noqa: E501
                                 (sp["t1"] + 0.4, 0.0)], floor=0.3),
                  "velocity": 0.45, "velocity_variance": 0.35, "direction": [0, 0, 0],
                  "spread": 60, "inherit_velocity": 0.25, "start_time": t_on},
                 layer="spiral", parent=f"bead_{tag}", enabled=bead_on,
                 inputs={"particle": "spiral_spark_ps"}))

    add(node("spiral_spark_ps", "particle_system",
             {"max_particles": 1000, "lifetime": 0.62, "lifetime_variance": 0.34,
              "size": r4(0.072 * p["mote_size"]), "size_variance": r4(0.058 * p["mote_size"]),
              "size_over_life": curve([(0.0, 0.25), (0.16, 1.0), (0.7, 0.85), (1.0, 0.1)]),
              "color": pal["hot"],
              "color_over_life": grad([(0.0, pal["hot"]), (0.45, pal["warm"]), (1.0, pal["mid"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.1, 1.0), (0.72, 0.85), (1.0, 0.0)]),
              "emissive": r4(2.5 * p["emissive"]), "drag": 2.4, "blend": "additive",
              "rotation_variance": 180.0, "angular_velocity": 14.0, "angular_velocity_variance": 95.0},
             layer="spiral", enabled=spiral_on,
             inputs={"sprite": "tex_star", "material": "mat_soft", "forces": ["f_lift_soft", "f_turb"]}))

    # ================= light core =================
    # A soft luminous core at chest height, NOT a cross: the user rejected the
    # four-armed flare as "weird and cartoony", so this is a vertical lens of
    # light plus a wide halo that sits inside the pillar.
    fy = p["core_y"]
    if c.loop:                              # one swell per pulse, dead by the seam
        span = dur / c.pulses
        core_life = r4(0.76 * span)
        seed_life = r4(0.34 * span)
        core_bursts = [r4(k * span + 0.2 * span) for k in range(c.pulses)]
        seed_bursts = [r4(k * span + 0.04 * span) for k in range(c.pulses)]
    else:
        core_life = r4(1.05 * (dur / 1.8))
        seed_life = r4(0.8 * (dur / 1.8))
        core_bursts = p["core_bursts"]
        seed_bursts = [0.0]

    add(node("corona_seed_ps", "particle_system",
             {"max_particles": 12, "lifetime": seed_life, "size": r4(0.95 * p["core_scale"]),
              "size_over_life": curve([(0.0, 0.12), (0.2, 1.0), (0.5, 0.8), (1.0, 0.5)]),
              "color": pal["hot"],
              "color_over_life": grad([(0.0, pal["hot"]), (0.6, pal["warm"]), (1.0, pal["mid"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.14, 0.85), (0.5, 0.52), (1.0, 0.0)]),
              "emissive": r4(2.6 * p["emissive"] * fw), "blend": "additive"},
             layer="corona", inputs={"sprite": "tex_corona", "material": "mat_soft"}))
    add(node("corona_seed_e", "emitter",
             {"shape": "point", "position": [0.0, r4(fy), 0.0], "rate": 0.0,
              "burst_count": 1, "burst_times": seed_bursts, "velocity": 0.0,
              "start_time": c.start(0.22)},
             layer="corona", inputs={"particle": "corona_seed_ps"}))

    add(node("corona_ps", "particle_system",
             {"max_particles": 12, "lifetime": core_life,
              "size": r4(2.2 * p["core_scale"]),
              "size_over_life": curve([(0.0, 0.2), (0.12, 0.68), (0.32, 1.0), (0.6, 0.98),
                                       (0.82, 0.8), (1.0, 0.5)]),
              "color": pal["hot"],
              "color_over_life": grad([(0.0, pal["hot"]), (0.4, pal["hot"]), (0.75, pal["warm"]),
                                       (1.0, pal["mid"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.12, 0.72), (0.34, 0.88), (0.66, 0.62),
                                          (1.0, 0.0)]),
              "emissive": r4(4.2 * p["emissive"] * fw), "blend": "additive"},
             layer="corona", inputs={"sprite": "tex_corona", "material": "mat_soft"}))
    add(node("corona_e", "emitter",
             {"shape": "point", "position": [0.0, r4(fy), 0.0], "rate": 0.0,
              "burst_count": 1, "burst_times": core_bursts, "velocity": 0.0,
              "start_time": c.start(0.7)},
             layer="corona", inputs={"particle": "corona_ps"}))

    add(node("corona_halo_ps", "particle_system",
             {"max_particles": 12, "lifetime": core_life,
              "size": r4(3.4 * p["core_scale"]),
              "size_over_life": curve([(0.0, 0.3), (0.3, 1.0), (0.72, 1.06), (1.0, 0.7)]),
              "color": pal["warm"],
              "color_over_life": grad([(0.0, pal["warm"]), (0.6, pal["mid"]), (1.0, pal["deep"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.16, 0.17), (0.38, 0.21), (0.72, 0.13),
                                          (1.0, 0.0)]),
              "emissive": r4(0.85 * p["emissive"] * fw), "blend": "additive"},
             layer="corona", inputs={"sprite": "tex_core", "material": "mat_soft"}))
    add(node("corona_halo_e", "emitter",
             {"shape": "point", "position": [0.0, r4(fy), 0.0], "rate": 0.0,
              "burst_count": 1, "burst_times": core_bursts, "velocity": 0.0,
              "start_time": c.start(0.7)},
             layer="corona", inputs={"particle": "corona_halo_ps"}))

    # ================= motes =================
    add(node("sparkle_ps", "particle_system",
             {"max_particles": 1100, "lifetime": r4(1.2 * (dur / 1.8)), "lifetime_variance": 0.65,
              "size": r4(0.085 * p["mote_size"]), "size_variance": r4(0.07 * p["mote_size"]),
              "size_over_life": curve([(0.0, 0.14), (0.18, 1.0), (0.7, 0.8), (1.0, 0.06)]),
              "color": pal["warm"],
              "color_over_life": grad([(0.0, pal["hot"]), (0.4, pal["warm"]), (1.0, pal["mid"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.1, 1.0), (0.72, 0.9), (1.0, 0.0)]),
              "emissive": r4(2.1 * p["emissive"]), "drag": 0.75, "blend": "additive",
              "rotation_variance": 180.0, "angular_velocity": 12.0, "angular_velocity_variance": 90.0},
             layer="motes",
             inputs={"sprite": "tex_star", "material": "mat_soft",
                     "forces": ["f_lift_soft", "f_turb", "f_swirl", "f_hug"]}))
    add(node("sparkle_e", "emitter",
             {"shape": "ring", "radius": 0.72, "inner_radius": 0.16, "position": [0.0, 0.06, 0.0],
              "rate": c.env([(0.05, 0.0), (0.2, 0.4 * KR), (0.45, 0.78 * KR), (0.9, KR),
                             (1.25, 0.8 * KR), (1.5, 0.36 * KR), (1.75, 0.0)], floor=0.4),
              "velocity": 1.5, "velocity_variance": 0.8, "direction": [0, 1, 0], "spread": 18,
              "start_time": c.start(0.05)},
             layer="motes", inputs={"particle": "sparkle_ps"}))

    add(node("feather_ps", "particle_system",
             {"max_particles": 200, "lifetime": r4(1.6 * (dur / 1.8)), "lifetime_variance": 0.5,
              "size": r4(0.4 * p["feather_size"]), "size_variance": 0.17,
              "size_over_life": curve([(0.0, 0.55), (0.28, 1.0), (1.0, 0.9)]),
              "color": pal["warm"],
              "color_over_life": grad([(0.0, pal["warm"]), (0.45, pal["mid"]), (1.0, pal["deep"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.2, 0.62), (0.65, 0.5), (1.0, 0.0)]),
              "emissive": r4(3.8 * p["emissive"]), "drag": 0.7, "blend": "additive",
              "rotation_variance": 180.0, "angular_velocity": 14.0, "angular_velocity_variance": 55.0},
             layer="motes", enabled=p["feathers"] > 0,
             inputs={"sprite": "tex_feather", "material": "mat_soft",
                     "forces": ["f_lift_soft", "f_turb", "f_curl"]}))
    add(node("feather_e", "emitter",
             {"shape": "ring", "radius": 0.85, "inner_radius": 0.26, "position": [0.0, 0.35, 0.0],
              "rate": c.env([(0.4, 0.0), (0.7, p["feathers"] * 0.55), (1.05, p["feathers"]),
                             (1.4, p["feathers"] * 0.85), (1.65, p["feathers"] * 0.4),
                             (1.8, p["feathers"] * 0.12)], floor=0.35),
              "velocity": 1.05, "velocity_variance": 0.45, "direction": [0, 1, 0], "spread": 30,
              "start_time": c.start(0.4)},
             layer="motes", enabled=p["feathers"] > 0, inputs={"particle": "feather_ps"}))

    add(node("haze_ps", "particle_system",
             {"max_particles": 220, "lifetime": r4(1.45 * (dur / 1.8)), "lifetime_variance": 0.5,
              "size": r4(1.4 * p["haze_size"]), "size_variance": 0.5,
              "size_over_life": curve([(0.0, 0.55), (0.4, 1.0), (1.0, 1.2)]),
              "color": pal["haze"],
              "color_over_life": grad([(0.0, pal["warm"]), (0.45, pal["haze"]), (1.0, pal["haze"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.28, 0.022), (0.6, 0.016), (1.0, 0.0)]),
              "emissive": r4(0.3 * p["emissive"]), "drag": 1.0, "blend": "additive",
              "rotation_variance": 180.0, "angular_velocity": 8.0, "sort": False},
             layer="motes",
             inputs={"sprite": "tex_haze", "material": "mat_haze",
                     "forces": ["f_lift_soft", "f_curl"]}))
    add(node("haze_e", "emitter",
             {"shape": "disc", "radius": 1.45, "position": [0.0, 0.22, 0.0],
              "rate": c.env([(0.0, 0.0), (0.25, 20.0 * S), (0.8, 34.0 * S), (1.25, 28.0 * S),
                             (1.55, 12.0 * S), (1.8, 0.0)], floor=0.45),
              "velocity": 0.3, "velocity_variance": 0.2, "direction": [0, 1, 0], "spread": 34,
              "start_time": 0.0},
             layer="motes", inputs={"particle": "haze_ps"}))

    # cleansing wave that travels up the body
    wave_on = p["wave"]["count"] > 0
    add(node("wave_ps", "particle_system",
             {"max_particles": 700, "lifetime": 0.42, "lifetime_variance": 0.14,
              "size": r4(0.15 * p["mote_size"]), "size_variance": 0.06,
              "size_over_life": curve([(0.0, 0.3), (0.2, 1.0), (1.0, 0.15)]),
              "color": pal["hot"],
              "color_over_life": grad([(0.0, pal["hot"]), (0.5, pal["warm"]), (1.0, pal["mid"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.14, 1.0), (0.7, 0.8), (1.0, 0.0)]),
              "emissive": r4(3.6 * p["emissive"]), "drag": 2.0, "blend": "additive",
              "rotation_variance": 180.0, "angular_velocity": 40.0},
             layer="motes", enabled=wave_on,
             inputs={"sprite": "tex_star", "material": "mat_soft", "forces": ["f_lift_soft"]}))
    for wi, wtag in enumerate("ab"):
        on = wave_on and wi < p["wave"]["count"]
        t0 = p["wave"]["t0"] + wi * p["wave"]["gap"]
        rise = p["wave"]["rise"]
        add(node(f"wave_{wtag}_e", "emitter",
                 {"shape": "ring", "radius": 0.5, "inner_radius": 0.34,
                  "position": c.env_pos([(t0, 0.06), (t0 + rise, 2.35)]),
                  "rate": c.env([(t0, 0.0), (t0 + 0.05, 900.0), (t0 + rise * 0.85, 820.0),
                                 (t0 + rise, 0.0)], floor=0.0, shift=wi * 0.33),
                  "velocity": 0.9, "velocity_variance": 0.5, "direction": [0, 0, 0],
                  "radial_velocity": 0.4, "spread": 40, "start_time": c.start(t0)},
                 layer="motes", enabled=on, inputs={"particle": "wave_ps"}))

    # ================= light =================
    add(node("key_light", "light",
             {"light_type": "point", "position": [0.0, 1.05, 0.0], "color": pal["light"],
              "radius": 4.2, "flicker_amplitude": 0.04, "flicker_frequency": 7.0,
              "intensity": c.env([(0.0, 0.0), (0.18, 0.5 * S), (0.5, 1.5 * S), (0.9, 3.0 * S),
                                  (1.2, p["light"] * S), (1.4, p["light"] * 0.85 * S),
                                  (1.62, 1.4 * S), (1.8, 0.0)], floor=0.35)},
             layer="light"))
    add(node("rim_light", "light",
             {"light_type": "point", "position": [0.0, 0.18, 0.0], "color": pal["warm"],
              "radius": 2.3,
              "intensity": c.env([(0.0, 0.0), (0.16, 0.2 * S), (0.55, 0.45 * S), (1.15, 0.62 * S),
                                  (1.45, 0.45 * S), (1.75, 0.0)], floor=0.4)},
             layer="light"))

    # critical's brief screen-filling wash
    add(node("burst_ps", "particle_system",
             {"max_particles": 6, "lifetime": r4(0.3 * (dur / 1.8)),
              "size": r4(p["burst"]["size"]),
              "size_over_life": curve([(0.0, 0.35), (0.25, 1.0), (1.0, 1.3)]),
              "color": pal["mid"],
              "color_over_life": grad([(0.0, pal["mid"]), (0.5, pal["mid"]), (1.0, pal["deep"])]),
              "opacity_over_life": curve([(0.0, 0.0), (0.18, p["burst"]["opacity"]),
                                          (0.42, p["burst"]["opacity"] * 0.42), (1.0, 0.0)]),
              "emissive": 0.5, "blend": "additive"},
             layer="light", enabled=p["burst"]["on"],
             inputs={"sprite": "tex_core", "material": "mat_soft"}))
    add(node("burst_e", "emitter",
             {"shape": "point", "position": [0.0, 1.15, 0.0], "rate": 0.0,
              "burst_count": 1, "burst_times": [0.0], "velocity": 0.0,
              "start_time": c.start(p["burst"]["t"])},
             layer="light", enabled=p["burst"]["on"], inputs={"particle": "burst_ps"}))

    phases = p["phases"]
    return {
        "schema_version": SCHEMA_VERSION,
        "name": p["name"],
        "description": p["description"],
        "duration": r4(dur),
        "seed": p["seed"],
        "timeline": {"phases": [{"name": n, "start": r4(a), "end": r4(b)} for n, a, b in phases]},
        "layers": [
            {"id": "ground", "name": "Ground ring", "role": "telegraph"},
            {"id": "pillar", "name": "Pillar of light", "role": "primary"},
            {"id": "spiral", "name": "Spiral ribbon", "role": "primary"},
            {"id": "corona", "name": "Light core", "role": "primary"},
            {"id": "motes", "name": "Sparkles, feathers, haze", "role": "secondary"},
            {"id": "light", "name": "Restorative light", "role": "interaction"},
        ],
        "nodes": nodes,
        "metadata": {
            "reference": "out/references/heal_sheet.jpg",
            "generator": "tools/generators/heal_variants.py",
            "variant": slug,
            "analysis": p["analysis"],
            "target": "invisible humanoid capsule, 1.8 m tall, radius 0.35 m, standing at the origin",
            "render_settings": p["render"],
        },
    }


# --------------------------------------------------------------------------
# the VARIANTS table - everything that differs between the six effects
# --------------------------------------------------------------------------

GOLD = {
    "hot": [1.0, 0.985, 0.9, 1.0],
    "warm": [1.0, 0.9, 0.63, 1.0],
    "mid": [1.0, 0.8, 0.42, 1.0],
    "deep": [1.0, 0.6, 0.2, 1.0],
    "haze": [0.8, 0.82, 0.46, 1.0],
    "light": [1.0, 0.8, 0.46, 1.0],
}
SILVER = {
    "hot": [0.93, 0.98, 1.0, 1.0],
    "warm": [0.7, 0.88, 1.0, 1.0],
    "mid": [0.45, 0.72, 1.0, 1.0],
    "deep": [0.24, 0.46, 0.92, 1.0],
    "haze": [0.62, 0.8, 0.98, 1.0],
    "light": [0.68, 0.86, 1.0, 1.0],
}
GREEN = {
    "hot": [0.88, 1.0, 0.85, 1.0],
    "warm": [0.56, 0.95, 0.5, 1.0],
    "mid": [0.32, 0.82, 0.38, 1.0],
    "deep": [0.12, 0.52, 0.24, 1.0],
    "haze": [0.52, 0.84, 0.48, 1.0],
    "light": [0.52, 0.92, 0.52, 1.0],
}
CRIT = {
    "hot": [1.0, 0.99, 0.92, 1.0],
    "warm": [1.0, 0.9, 0.62, 1.0],
    "mid": [1.0, 0.82, 0.44, 1.0],
    "deep": [1.0, 0.62, 0.22, 1.0],
    "haze": [0.86, 0.82, 0.44, 1.0],
    "light": [1.0, 0.86, 0.52, 1.0],
}

PHASES_ONESHOT = [
    ("cast", 0.0, 0.2), ("initiate", 0.2, 0.4), ("envelop", 0.4, 0.6),
    ("healing", 0.6, 1.0), ("peak", 1.0, 1.4), ("fade", 1.4, 1.8),
]
PHASES_LOOP = [("pulse_a", 0.0, 1.0), ("pulse_b", 1.0, 2.0), ("pulse_c", 2.0, 3.0)]

BASE: dict[str, Any] = {
    "duration": 1.8,
    "time_scale": 1.0,
    "loop": False,
    "seed": 9141,
    "pal": GOLD,
    "phases": PHASES_ONESHOT,
    "intensity": 1.0,
    "emissive": 1.0,
    "ring_scale": 1.0,
    "ring_emissive": 2.0,
    "ring_spin": 42.0,
    "ring_radii": [(0.465, 0.011, 1.0), (0.4, 0.006, 0.62), (0.335, 0.015, 0.9), (0.27, 0.005, 0.5)],
    "pillar_h": 2.9,
    "pillar_w": 1.0,
    "pillar_rate": 1250.0,
    "shafts": 6,
    "shaft_w": 1.0,
    "shaft_a": 1.0,
    "spiral": {"beads": 1, "r0": 0.62, "r1": 0.4, "y0": 0.1, "y1": 2.2, "turns": 2.0,
               "phase": 205.0, "t0": 0.4, "t1": 1.22, "steps": 44, "width": 1.0},
    "core_y": 1.42,
    "core_scale": 1.0,
    "core_lens": 0.72,          # width of the vertical lens of light, in UV
    "core_hot": 0.14,           # radius of the hot centre, in UV
    "core_bursts": [0.0],
    "sparkle_rate": 280.0,
    "mote_size": 1.0,
    "feathers": 17.0,
    "feather_size": 1.0,
    "leaf_motes": False,
    "haze_size": 1.0,
    "swirl": 1.5,
    "light": 4.2,
    "star_width": 0.055,
    "star_core": 0.055,
    "star_soft": 0.02,
    "star_glow": 0.62,
    "star_bloom": 0.3,
    "streak_body": 0.34,
    "streak_core": 0.07,
    "wave": {"count": 0, "t0": 0.5, "gap": 0.45, "rise": 0.55},
    "burst": {"on": False, "t": 1.05, "size": 8.0, "opacity": 0.3},
    "render": {"ground_albedo": 0.05, "background": [0.0, 0.0, 0.0, 1.0],
               "bloom_intensity": 0.26, "bloom_radius": 0.05, "exposure": 0.72, "grid": False},
}


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("pal", "render"):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


VARIANTS: dict[str, dict[str, Any]] = {
    # ---- the sheet, exactly -------------------------------------------------
    "heal": {
        "name": "Heal",
        "description": "A gold ground ring opens, a soft pillar of light rises, a luminous ribbon "
                       "spirals twice around the target and a soft core of light swells at the peak.",
        "analysis": "Base variant: the concept sheet's six keyframes at full strength; the sheet's "
                    "cross-shaped light core is a soft luminous core here (user feedback).",
    },
    # ---- quiet out-of-combat top-up ----------------------------------------
    "heal_subtle": {
        "name": "Heal Subtle",
        "description": "A quiet top-up: a thin gold ground ring, a soft narrow pillar and a few "
                       "rising sparkles, over almost before it starts.",
        "analysis": "Subtle: ring + thin pillar + a handful of sparkles, no feathers, no spiral, "
                    "fast in and out.",
        "seed": 9142,
        "time_scale": 0.78,
        "intensity": 0.62,
        "emissive": 0.68,
        "ring_scale": 0.92,
        "ring_emissive": 1.9,
        "ring_spin": 30.0,
        "ring_radii": [(0.465, 0.008, 0.8), (0.4, 0.005, 0.45), (0.335, 0.009, 0.6), (0.27, 0.004, 0.3)],
        "pillar_h": 2.2,
        "pillar_w": 0.72,
        "pillar_rate": 560.0,
        "shafts": 0,
        "spiral": {"beads": 0},
        "core_scale": 0.6,
        "core_y": 1.28,
        "sparkle_rate": 165.0,
        "mote_size": 0.85,
        "feathers": 0.0,
        "haze_size": 0.8,
        "swirl": 0.9,
        "light": 2.2,
        "render": {"ground_albedo": 0.048, "background": [0.0, 0.0, 0.0, 1.0],
                   "bloom_intensity": 0.22, "bloom_radius": 0.048, "exposure": 0.74, "grid": False},
    },
    # ---- big heal ----------------------------------------------------------
    "heal_intense": {
        "name": "Heal Intense",
        "description": "A big heal: a double ribbon spirals up a wide pillar of shafts, feathers "
                       "pour off it and the light core flares hard.",
        "analysis": "Intense: double spiral, more shafts and feathers, brighter everything.",
        "seed": 9143,
        "intensity": 1.22,
        "emissive": 0.98,
        "ring_scale": 1.12,
        "ring_emissive": 2.4,
        "ring_spin": 54.0,
        "pillar_h": 3.3,
        "pillar_w": 1.22,
        "pillar_rate": 1700.0,
        "shafts": 8,
        "shaft_w": 1.25,
        "shaft_a": 1.2,
        "spiral": {"beads": 2, "r0": 0.72, "r1": 0.44, "y1": 2.45, "turns": 2.25, "width": 1.02,
                   "t0": 0.36, "t1": 1.2},
        "core_scale": 1.25,
        "core_bursts": [0.0, 0.34],
        "sparkle_rate": 400.0,
        "feathers": 30.0,
        "feather_size": 1.1,
        "haze_size": 1.15,
        "swirl": 2.1,
        "light": 5.6,
        "render": {"ground_albedo": 0.055, "background": [0.0, 0.0, 0.0, 1.0],
                   "bloom_intensity": 0.3, "bloom_radius": 0.054, "exposure": 0.7, "grid": False},
    },
    # ---- cleanse -----------------------------------------------------------
    "heal_pure": {
        "name": "Heal Pure",
        "description": "A cleanse: silver and blue-white light, crisp four-point sparkles and two "
                       "ring waves that sweep up the target, washing the affliction off.",
        "analysis": "Pure: silver/blue-white palette, crisp sparkles, two cleansing ring waves "
                    "travelling up the body, no feathers - motes instead.",
        "seed": 9144,
        "pal": SILVER,
        "intensity": 1.05,
        "emissive": 1.15,
        "ring_emissive": 2.2,
        "ring_spin": 60.0,
        "ring_radii": [(0.465, 0.008, 1.0), (0.41, 0.004, 0.55), (0.35, 0.011, 0.85),
                       (0.29, 0.004, 0.45)],
        "pillar_h": 3.0,
        "pillar_w": 0.92,
        "pillar_rate": 1150.0,
        "shafts": 5,
        "shaft_w": 0.75,
        "spiral": {"beads": 1, "r0": 0.58, "r1": 0.38, "turns": 2.0, "width": 0.95},
        "core_scale": 1.05,
        "core_lens": 0.6,
        "core_hot": 0.12,
        "sparkle_rate": 400.0,
        "mote_size": 0.82,
        "feathers": 7.0,
        "feather_size": 0.7,
        "star_width": 0.04,
        "star_core": 0.038,
        "star_soft": 0.012,
        "star_glow": 0.48,
        "star_bloom": 0.22,
        "streak_body": 0.27,
        "streak_core": 0.055,
        "haze_size": 0.95,
        "swirl": 1.8,
        "light": 4.4,
        "wave": {"count": 2, "t0": 0.42, "gap": 0.5, "rise": 0.5},
        "render": {"ground_albedo": 0.05, "background": [0.0, 0.0, 0.0, 1.0],
                   "bloom_intensity": 0.28, "bloom_radius": 0.05, "exposure": 0.72, "grid": False},
    },
    # ---- heal over time ----------------------------------------------------
    "heal_regen": {
        "name": "Heal Regen",
        "description": "Heal over time: a gentle green aura that breathes in three soft pulses, "
                       "leaf-like motes drifting up on a 3 s seamless loop.",
        "analysis": "Regen: 3.0 s seamless loop, three soft pulses, green palette, leaf motes "
                    "instead of feathers, the swirl carried by a vortex on the motes rather than "
                    "a ribbon (a trail cannot wrap a loop seam).",
        "seed": 9145,
        "pal": GREEN,
        "duration": 3.0,
        "loop": True,
        "loop_pulses": 3,
        "loop_floor": 0.38,
        "loop_steps": 10,
        "phases": PHASES_LOOP,
        "intensity": 0.75,
        "emissive": 0.95,
        "ring_scale": 1.0,
        "ring_emissive": 1.6,
        "ring_spin": 20.0,   # 20 deg/s * 3.0 s = 60 deg = 4 ticks of the 24-tick ring
        "pillar_h": 2.4,
        "pillar_w": 0.88,
        "pillar_rate": 700.0,
        "shafts": 3,
        "shaft_w": 0.8,
        "shaft_a": 0.85,
        "spiral": {"beads": 0},
        "core_scale": 0.78,
        "core_y": 1.32,
        "sparkle_rate": 175.0,
        "feathers": 16.0,
        "feather_size": 1.12,
        "leaf_motes": True,
        "haze_size": 1.2,
        "swirl": 2.4,
        "light": 3.0,
        "wave": {"count": 1, "t0": 0.0, "gap": 0.0, "rise": 0.8},
        "render": {"ground_albedo": 0.05, "background": [0.0, 0.0, 0.0, 1.0],
                   "bloom_intensity": 0.25, "bloom_radius": 0.05, "exposure": 0.74, "grid": False},
    },
    # ---- critical ----------------------------------------------------------
    "heal_critical": {
        "name": "Heal Critical",
        "description": "A critical heal: a tall gold-white pillar of shafts, a burst of feathers "
                       "and a swelling core of light that washes the frame for a moment.",
        "analysis": "Critical: tallest pillar, all eight shafts, biggest light core, a feather burst and "
                    "a brief warm wash at the peak that stays gold instead of blowing to white.",
        "seed": 9146,
        "pal": CRIT,
        "time_scale": 0.94,
        "intensity": 1.2,
        "emissive": 0.86,
        "ring_scale": 1.18,
        "ring_emissive": 2.6,
        "ring_spin": 66.0,
        "pillar_h": 4.1,
        "pillar_w": 1.35,
        "pillar_rate": 1580.0,
        "shafts": 8,
        "shaft_w": 1.5,
        "shaft_a": 1.35,
        "spiral": {"beads": 2, "r0": 0.7, "r1": 0.42, "y1": 2.6, "turns": 2.25, "width": 1.0,
                   "t0": 0.34, "t1": 1.12},
        "core_y": 1.55,
        "core_scale": 1.6,
        "core_lens": 0.78,
        "core_hot": 0.16,
        "core_bursts": [0.0],
        "sparkle_rate": 470.0,
        "feathers": 44.0,
        "feather_size": 1.15,
        "haze_size": 1.25,
        "swirl": 2.2,
        "light": 7.0,
        "wave": {"count": 1, "t0": 0.62, "gap": 0.4, "rise": 0.42},
        "burst": {"on": True, "t": 0.98, "size": 9.0, "opacity": 0.1},
        "render": {"ground_albedo": 0.058, "background": [0.0, 0.0, 0.0, 1.0],
                   "bloom_intensity": 0.32, "bloom_radius": 0.056, "exposure": 0.66, "grid": False},
    },
}


def variant_params(slug: str) -> dict[str, Any]:
    return merge(BASE, VARIANTS[slug])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="directory the six JSON files are written to")
    ap.add_argument("--only", default="", help="comma-separated slugs (default: all six)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    slugs = [s for s in args.only.split(",") if s] or list(VARIANTS)
    for slug in slugs:
        effect = build(slug, variant_params(slug))
        path = out_dir / f"{slug}.json"
        path.write_text(json.dumps(effect, indent=2) + "\n", encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes, {effect['duration']} s)")


if __name__ == "__main__":
    main()
