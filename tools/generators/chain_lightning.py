#!/usr/bin/env python3
"""Generate examples/effects/chain_lightning.json from a table of anchors and hops.

    python tools/generators/chain_lightning.py --out examples/effects
    python tools/generators/chain_lightning.py --check examples/effects/chain_lightning.json

The chain is defined by ANCHORS (points in space: the caster's hand and one per
target, roughly chest height) and HOPS (which anchor jumps to which, and when).
There are no characters and no placeholder targets: a target is the place where
a link lands, and everything that sells "something stands here being hit" is
emitted around that point - the impact, crawling arcs over a human-sized capsule,
a ground scorch with electric veins and a point light.

Every hop is built from the same recipe, so a longer chain ("Extended, 5+
targets") is one more row in ANCHORS and one more row in HOPS:

  leader    a thin forked streamer whose `target` is keyframed from the emitting
            anchor to the receiving one - the readable travel arc of the jump
  stroke    the hero bolt (the Lightning Strike recipe: fractal `detail`, bulge
            width profile, two generations of thinning branches, flicker,
            afterglow); its `target` races along the leader's channel in four
            frames with the impact flare riding the tip, and lands on `hit`
  linger    the link that stays: a thinner channel on a slower re-roll that
            flickers, re-strikes every time a later hop fires (the whole chain
            re-energises) and dies with the other links around LINK_END
  arcs      two thin companions on slightly different end points that blink
  impact    at the receiving anchor, on `hit`: core flash, an irregular soft ray
            burst, radial streaks, sparks with gravity and ground bounce, a shock
            ring, a flash light, ground veins, crawling arcs, drifting motes

Frames are 60 per second; every key sits on a frame so peaks are never missed.

House style: no constant-width tube bolts (every beam has `detail` >= 2 and a
non-uniform width profile), ray bursts are many, irregular and soft (9 + 13
warped spokes, never 4 or 2), hot cores stay small and glows stay soft.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SLUG = "chain_lightning"
NAME = "Chain Lightning"
DESCRIPTION = ("A crackling orb throws a forked bolt that strikes the first target and jumps target to target, "
               "forking at the third jump; every link lingers and re-strikes until the chain dies into drifting sparks.")
DURATION = 2.0
SEED = 61803
FPS = 60.0

# The concept sheet's key frames.
PHASES = [
    ("cast", 0.0, 0.2),
    ("initial_strike", 0.2, 0.4),
    ("first_impact", 0.4, 0.6),
    ("jump", 0.6, 0.9),
    ("second_impact", 0.9, 1.1),
    ("branch_jump", 1.1, 1.4),
    ("final_impact", 1.4, 1.8),
    ("dissipate", 1.8, 2.0),
]

# ---------------------------------------------------------------------------
# the table: anchors and hops
# ---------------------------------------------------------------------------

#: Points in space, metres. A zig-zag in depth so the chain reads as 3D.
ANCHORS: dict[str, tuple[float, float, float]] = {
    "src": (-4.6, 1.45, 0.4),
    "t1": (-1.7, 1.25, -0.7),
    "t2": (0.7, 1.15, 0.9),
    "t3": (3.0, 1.3, -0.9),
    "t4": (3.4, 1.2, 1.7),
}
SOURCE = "src"
FADE_STOP = 106          # last frame that may emit a 0.22 s fade mote: nothing outlives the 2.0 s


@dataclass(frozen=True)
class Hop:
    id: str
    a: str          # emitting anchor
    b: str          # receiving anchor
    lead: int       # frame the leader leaves `a`
    hit: int        # frame the stroke lands on `b` (the impact)
    end: int        # frame the link dies
    tier: int       # impact tier: 1 modest, 2 bigger, 3 the final ones
    power: float    # bolt width / brightness multiplier

    @property
    def final(self) -> bool:
        return self.tier >= 3


#: One row per jump. Two rows leaving the same anchor are a fork.
HOPS = [
    Hop("hop1", "src", "t1", lead=12, hit=24, end=101, tier=1, power=0.92),
    Hop("hop2", "t1", "t2", lead=42, hit=54, end=102, tier=2, power=1.04),
    Hop("hop3", "t2", "t3", lead=72, hit=84, end=103, tier=3, power=1.18),
    Hop("hop4", "t2", "t4", lead=73, hit=86, end=104, tier=3, power=1.24),
]

#: What an impact is made of, per tier. The chain escalates: modest, bigger, and the two final ones
#: clearly the strongest (a larger double ray burst, a double shock halo, the most sparks).
TIERS: dict[int, dict[str, float]] = {
    1: {"flash": 0.7, "rays": 1.7, "ray_count": 1, "halos": 1, "streaks": 20, "streak_speed": 8.5,
        "sparks": 55, "spark_speed": 3.8, "light": 3.0, "veins": 1.9},
    2: {"flash": 0.95, "rays": 2.4, "ray_count": 1, "halos": 1, "streaks": 32, "streak_speed": 11.0,
        "sparks": 90, "spark_speed": 4.6, "light": 4.4, "veins": 2.3},
    3: {"flash": 1.25, "rays": 3.4, "ray_count": 2, "halos": 2, "streaks": 50, "streak_speed": 14.0,
        "sparks": 150, "spark_speed": 5.6, "light": 6.2, "veins": 2.7},
}
STROKE_TRAVEL = 4        # frames the stroke takes from `a` to `b` (0.067 s)
CAPSULE_RADIUS = 0.45    # the human-sized region around a target: crawl arcs live on it
CAPSULE_Y = (0.3, 1.8)

# palette (linear). White lives only in the hot cores: the beam shader washes its own core to white,
# everything else is saturated electric blue with violet in the glow. Beam emissive stays low enough
# that the glow layers keep their colour instead of clipping to white.
BOLT = [0.17, 0.33, 1.0, 1.0]         # electric blue
BOLT_VIOLET = [0.46, 0.25, 1.0, 1.0]  # companions and crawl arcs lean violet
HOT = [0.66, 0.8, 1.0, 1.0]           # the hottest a sprite gets
MID = [0.22, 0.42, 1.0, 1.0]
DEEP = [0.2, 0.13, 0.85, 1.0]         # ramps die into blue-violet
LIGHT = [0.2, 0.36, 1.0, 1.0]

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def r4(v: float) -> float:
    return round(float(v), 4)


def ft(frame: float) -> float:
    """Frame -> seconds."""
    return r4(frame / FPS)


def fs(frame: float) -> float:
    """Frame -> seconds for a step key or a window start: a hair early, so the
    switch has always happened by the time that frame is evaluated."""
    return r4(frame / FPS - 0.002)


def v3(p: tuple[float, float, float] | list[float]) -> list[float]:
    return [r4(p[0]), r4(p[1]), r4(p[2])]


def lerp3(a, b, t: float) -> list[float]:
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def add3(a, b) -> list[float]:
    return [a[i] + b[i] for i in range(3)]


def dist(a, b) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def tint(rgba: list[float], k: float) -> list[float]:
    return [r4(rgba[0] * k), r4(rgba[1] * k), r4(rgba[2] * k), rgba[3]]


def track(keys: list[tuple], scale: float = 1.0) -> dict[str, Any]:
    """keys = [(frame, value[, interp])] -> {"value": first, "track": [...]}; scalars are scaled."""
    out = []
    for key in keys:
        frame, value = key[0], key[1]
        interp = key[2] if len(key) > 2 else None
        if isinstance(value, (int, float)):
            value = r4(value * scale)
        entry: dict[str, Any] = {"time": fs(frame) if interp == "step" else ft(frame), "value": value}
        if interp:
            entry["interp"] = interp
        out.append(entry)
    return {"value": out[0]["value"], "track": out}


def window(first: float, last: float) -> dict[str, float]:
    """start_time / duration covering frames first..last inclusive."""
    start = max(0.0, fs(first))
    return {"start_time": start, "duration": r4(ft(last + 1) - start)}


nodes: list[dict[str, Any]] = []


def node(nid: str, ntype: str, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None,
         layer: str | None = None) -> str:
    n: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        n["layer"] = layer
    if params:
        n["parameters"] = params
    if inputs:
        n["inputs"] = inputs
    nodes.append(n)
    return nid


def capsule_point(rng: random.Random, centre, theta: float | None = None, y: float | None = None,
                  swell: float = 1.0) -> tuple[list[float], float, float]:
    """A point on the surface of the target capsule (radius CAPSULE_RADIUS, CAPSULE_Y high)."""
    lo, hi = CAPSULE_Y
    if theta is None:
        theta = rng.uniform(0.0, 2.0 * math.pi)
    if y is None:
        y = rng.uniform(lo + 0.08, hi - 0.08)
    y = min(max(y, lo + 0.03), hi - 0.03)
    radius = CAPSULE_RADIUS
    if y < lo + radius:
        radius = math.sqrt(max(radius ** 2 - (lo + radius - y) ** 2, 0.0))
    elif y > hi - radius:
        radius = math.sqrt(max(radius ** 2 - (y - (hi - radius)) ** 2, 0.0))
    radius = max(radius, 0.12) * swell
    return [centre[0] + math.cos(theta) * radius, y, centre[2] + math.sin(theta) * radius], theta, y


def hits_after(frame: int) -> list[Hop]:
    return [h for h in HOPS if h.hit > frame]


def hops_into(anchor: str) -> list[Hop]:
    return [h for h in HOPS if h.b == anchor]


def hops_out_of(anchor: str) -> list[Hop]:
    return [h for h in HOPS if h.a == anchor]


# ---------------------------------------------------------------------------
# textures (all grayscale: the colour comes from the node that draws them, so
# one hue control recolours the whole effect)
# ---------------------------------------------------------------------------


def warp_field(prefix: str, frequency: float, seed: int, animate: float = 0.0) -> list[dict[str, Any]]:
    """A packed two-channel fbm so `distort` displaces in x and y independently."""
    out = []
    for i, channel in enumerate(("a", "b")):
        params: dict[str, Any] = {"frequency": frequency, "octaves": 4, "seed": seed + 31 * i}
        if animate:
            params["animate"] = animate
        out.append({"id": f"{prefix}{channel}", "op": "fbm", "params": params})
        out.append({"id": f"{prefix}{channel}l", "op": "levels", "params": {"in_low": 0.34, "in_high": 0.66},
                    "inputs": {"a": f"{prefix}{channel}"}})
    out.append({"id": prefix, "op": "channel_pack", "params": {"channel": "luminance"},
                "inputs": {"r": f"{prefix}al", "g": f"{prefix}bl"}})
    return out


def build_textures() -> None:
    node("tex_glow", "texture", {"width": 96, "height": 96, "graph": {"nodes": [
        {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
        {"id": "l", "op": "levels", "params": {"gamma": 0.62}, "inputs": {"a": "r"}},
    ], "output": "l"}})

    node("tex_spark", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
        {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "quadratic"}},
    ], "output": "r"}})

    # Impact ray burst: 9 long + 13 short rays, warped off their even spacing, each
    # ray's brightness and reach broken by noise, faded to nothing at the tips and
    # blurred. Many, irregular, soft - never a four-armed star.
    node("tex_rays", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
        *warp_field("w", 1.7, 211),
        {"id": "s1", "op": "spokes", "params": {"count": 9, "width": 0.011, "softness": 0.012,
                                                "inner_radius": 0.015, "outer_radius": 0.5}},
        {"id": "s1d", "op": "distort", "params": {"amount": 0.032}, "inputs": {"a": "s1", "by": "w"}},
        {"id": "s2", "op": "spokes", "params": {"count": 13, "width": 0.0055, "softness": 0.008,
                                                "inner_radius": 0.03, "outer_radius": 0.4, "rotation": 13.0}},
        {"id": "s2d", "op": "distort", "params": {"amount": 0.05}, "inputs": {"a": "s2", "by": "w"}},
        {"id": "s2l", "op": "levels", "params": {"out_high": 0.7}, "inputs": {"a": "s2d"}},
        {"id": "u", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "s1d", "b": "s2l"}},
        {"id": "n", "op": "fbm", "params": {"frequency": 2.6, "octaves": 3, "seed": 97}},
        {"id": "nl", "op": "levels", "params": {"in_low": 0.3, "in_high": 0.66, "out_low": 0.16},
         "inputs": {"a": "n"}},
        {"id": "un", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "u", "b": "nl"}},
        {"id": "fall", "op": "gradient_radial", "params": {"radius": 0.5, "inner_radius": 0.02, "falloff": "quadratic"}},
        {"id": "rays", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "un", "b": "fall"}},
        {"id": "soft", "op": "blur", "params": {"radius": 1.6}, "inputs": {"a": "rays"}},
        {"id": "gain", "op": "levels", "params": {"in_high": 0.42}, "inputs": {"a": "soft"}},
        {"id": "hot", "op": "gradient_radial", "params": {"radius": 0.085, "falloff": "quadratic"}},
        {"id": "k", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "gain", "b": "hot"}},
    ], "output": "k"}})

    # Shock front: a wide soft band torn into wisps by noise - a pressure halo, never a drawn circle.
    node("tex_ring", "texture", {"width": 128, "height": 128, "graph": {"nodes": [
        {"id": "r", "op": "ring", "params": {"radius": 0.36, "thickness": 0.05, "softness": 0.13}},
        {"id": "n", "op": "fbm", "params": {"frequency": 4.5, "octaves": 4, "seed": 41}},
        {"id": "nl", "op": "levels", "params": {"in_low": 0.32, "in_high": 0.7}, "inputs": {"a": "n"}},
        {"id": "rb", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "r", "b": "nl"}},
        {"id": "m", "op": "levels", "params": {"out_high": 0.8}, "inputs": {"a": "rb"}},
    ], "output": "m"}})

    # Ground veins under a target: the Lightning Strike web (warped spokes + cracks
    # under a blurred glow), kept gray so the decal colour tints it.
    node("tex_veins", "texture", {"width": 384, "height": 384, "graph": {"nodes": [
        *warp_field("w", 2.4, 517),
        {"id": "s1", "op": "spokes", "params": {"count": 7, "width": 0.009, "softness": 0.005,
                                                "inner_radius": 0.01, "outer_radius": 0.5}},
        {"id": "s1d", "op": "distort", "params": {"amount": 0.15}, "inputs": {"a": "s1", "by": "w"}},
        {"id": "s2", "op": "spokes", "params": {"count": 17, "width": 0.0055, "softness": 0.003,
                                                "inner_radius": 0.08, "outer_radius": 0.46, "rotation": 9.0}},
        {"id": "s2d", "op": "distort", "params": {"amount": 0.2}, "inputs": {"a": "s2", "by": "w"}},
        {"id": "s2l", "op": "levels", "params": {"out_high": 0.7}, "inputs": {"a": "s2d"}},
        {"id": "cr", "op": "cracks", "params": {"density": 5.0, "width": 0.005, "seed": 23}},
        {"id": "crd", "op": "distort", "params": {"amount": 0.04}, "inputs": {"a": "cr", "by": "w"}},
        {"id": "crm", "op": "gradient_radial", "params": {"radius": 0.5, "inner_radius": 0.04, "falloff": "linear"}},
        {"id": "crx", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "crd", "b": "crm"}},
        {"id": "crl", "op": "levels", "params": {"out_high": 0.4}, "inputs": {"a": "crx"}},
        {"id": "u1", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "s1d", "b": "s2l"}},
        {"id": "u2", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "u1", "b": "crl"}},
        {"id": "fall", "op": "gradient_radial", "params": {"radius": 0.5, "inner_radius": 0.22, "falloff": "linear"}},
        {"id": "web", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "u2", "b": "fall"}},
        {"id": "gl", "op": "blur", "params": {"radius": 6.0}, "inputs": {"a": "web"}},
        {"id": "gll", "op": "levels", "params": {"in_high": 0.55, "out_high": 0.26}, "inputs": {"a": "gl"}},
        {"id": "pool", "op": "gradient_radial", "params": {"radius": 0.42, "falloff": "quadratic"}},
        {"id": "pooll", "op": "levels", "params": {"out_high": 0.2}, "inputs": {"a": "pool"}},
        {"id": "k1", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "web", "b": "gll"}},
        {"id": "k2", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "k1", "b": "pooll"}},
        {"id": "col", "op": "colorize", "params": {"gradient": [
            [0.0, [0.0, 0.0, 0.0, 0.0]], [0.2, [0.3, 0.3, 0.3, 0.16]], [0.5, [0.62, 0.62, 0.62, 0.5]],
            [0.8, [0.9, 0.9, 0.9, 0.85]], [1.0, [1.0, 1.0, 1.0, 1.0]]]}, "inputs": {"a": "k2"}},
    ], "output": "col"}})

    node("tex_scorch", "texture", {"width": 128, "height": 128, "graph": {"nodes": [
        {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "inner_radius": 0.08, "falloff": "smooth"}},
        {"id": "n", "op": "fbm", "params": {"frequency": 5.0, "octaves": 4, "seed": 8}},
        {"id": "m", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "r", "b": "n"}},
        {"id": "l", "op": "levels", "params": {"in_low": 0.06, "in_high": 0.5}, "inputs": {"a": "m"}},
    ], "output": "l"}})

    # Charge ring under the caster: a broken soft ring with a faint inner pool.
    node("tex_charge", "texture", {"width": 256, "height": 256, "graph": {"nodes": [
        *warp_field("w", 3.0, 733),
        {"id": "r1", "op": "ring", "params": {"radius": 0.38, "thickness": 0.03, "softness": 0.07}},
        {"id": "r1d", "op": "distort", "params": {"amount": 0.09}, "inputs": {"a": "r1", "by": "w"}},
        {"id": "n", "op": "fbm", "params": {"frequency": 3.5, "octaves": 3, "seed": 5}},
        {"id": "nl", "op": "levels", "params": {"in_low": 0.42, "in_high": 0.7}, "inputs": {"a": "n"}},
        {"id": "rb", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "r1d", "b": "nl"}},
        {"id": "cr", "op": "spokes", "params": {"count": 11, "width": 0.005, "softness": 0.004,
                                                "inner_radius": 0.05, "outer_radius": 0.42, "rotation": 7.0}},
        {"id": "crd", "op": "distort", "params": {"amount": 0.16}, "inputs": {"a": "cr", "by": "w"}},
        {"id": "crm", "op": "ring", "params": {"radius": 0.27, "thickness": 0.18, "softness": 0.12}},
        {"id": "crx", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "crd", "b": "crm"}},
        {"id": "crl", "op": "levels", "params": {"out_high": 0.5}, "inputs": {"a": "crx"}},
        {"id": "g", "op": "ring", "params": {"radius": 0.38, "thickness": 0.08, "softness": 0.11}},
        {"id": "gl", "op": "levels", "params": {"out_high": 0.2}, "inputs": {"a": "g"}},
        {"id": "m1", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "rb", "b": "crl"}},
        {"id": "m2", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m1", "b": "gl"}},
    ], "output": "m2"}})

    # Plasma ball (flipbook): wriggling tendrils over a cloudy body and a hot heart.
    node("tex_plasma", "texture", {"width": 128, "height": 128, "frames": 12, "graph": {"nodes": [
        *warp_field("w", 2.6, 139, animate=3.0),
        {"id": "cr", "op": "cracks", "params": {"density": 3.6, "width": 0.014, "seed": 77}},
        {"id": "crd", "op": "distort", "params": {"amount": 0.3}, "inputs": {"a": "cr", "by": "w"}},
        {"id": "mask", "op": "gradient_radial", "params": {"radius": 0.48, "inner_radius": 0.1, "falloff": "smooth"}},
        {"id": "ten", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "crd", "b": "mask"}},
        {"id": "b", "op": "fbm", "params": {"frequency": 4.5, "octaves": 4, "seed": 19, "animate": 2.0}},
        {"id": "bl", "op": "levels", "params": {"in_low": 0.38, "in_high": 0.8, "out_high": 0.55}, "inputs": {"a": "b"}},
        {"id": "bm", "op": "gradient_radial", "params": {"radius": 0.36, "falloff": "smooth"}},
        {"id": "body", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "bl", "b": "bm"}},
        {"id": "hot", "op": "gradient_radial", "params": {"radius": 0.17, "falloff": "quadratic"}},
        {"id": "m1", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "ten", "b": "body"}},
        {"id": "m2", "op": "math", "params": {"mode": "max"}, "inputs": {"a": "m1", "b": "hot"}},
    ], "output": "m2"}})

    # Crackle (flipbook): a few hair-thin broken arcs, different on every frame.
    node("tex_crackle", "texture", {"width": 128, "height": 128, "frames": 8, "graph": {"nodes": [
        *warp_field("w", 2.2, 311, animate=7.0),
        {"id": "cr", "op": "cracks", "params": {"density": 2.3, "width": 0.0085, "seed": 3}},
        {"id": "crd", "op": "distort", "params": {"amount": 0.34}, "inputs": {"a": "cr", "by": "w"}},
        {"id": "n", "op": "fbm", "params": {"frequency": 2.4, "octaves": 3, "seed": 58, "animate": 5.0}},
        {"id": "nl", "op": "levels", "params": {"in_low": 0.46, "in_high": 0.62}, "inputs": {"a": "n"}},
        {"id": "cut", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "crd", "b": "nl"}},
        {"id": "mask", "op": "gradient_radial", "params": {"radius": 0.5, "inner_radius": 0.12, "falloff": "smooth"}},
        {"id": "m", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "cut", "b": "mask"}},
    ], "output": "m"}})


# ---------------------------------------------------------------------------
# shared physics and particle systems
# ---------------------------------------------------------------------------


def build_shared() -> None:
    node("gravity", "force", {"force_type": "gravity", "strength": 9.81})
    node("drift_gravity", "force", {"force_type": "gravity", "strength": 1.4})
    node("drift_curl", "force", {"force_type": "curl_noise", "strength": 1.5, "frequency": 1.1, "octaves": 2,
                                 "speed": 0.8})
    node("ground", "collider", {"collider_type": "plane", "normal": [0, 1, 0], "bounce": 0.4, "friction": 0.45})

    # One flash and one ray-burst system per impact tier in use: a particle's size belongs to its
    # system, and the impacts have to escalate.
    white = [0.9, 0.95, 1.0, 1.0]
    flash_ramp = [[0.0, white], [0.25, HOT], [0.6, MID], [1.0, DEEP]]
    for tier in sorted({h.tier for h in HOPS}):
        spec = TIERS[tier]
        node(f"ps_flash_{tier}", "particle_system", {
            "max_particles": 8, "lifetime": r4(0.13 + 0.025 * tier), "size": spec["flash"], "size_variance": 0.08,
            "size_over_life": [[0.0, 0.3], [0.2, 1.0], [1.0, 1.3]],
            "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": flash_ramp,
            "opacity": r4(0.7 + 0.06 * tier), "opacity_over_life": [[0.0, 1.0], [0.3, 0.55], [1.0, 0.0]],
            "emissive": r4(0.7 + 0.12 * tier), "render_mode": "billboard", "blend": "additive",
        }, {"sprite": "tex_glow"}, layer="impact")
        node(f"ps_rays_{tier}", "particle_system", {
            "max_particles": 8, "lifetime": r4(0.14 + 0.025 * tier), "lifetime_variance": 0.02,
            "size": spec["rays"], "size_variance": r4(0.12 * spec["rays"]),
            "size_over_life": [[0.0, 0.35], [0.25, 1.0], [1.0, 1.2]],
            "rotation_variance": 180.0,
            "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, HOT], [0.4, MID], [1.0, DEEP]],
            "opacity": 1.0, "opacity_over_life": [[0.0, 1.0], [0.35, 0.7], [1.0, 0.0]],
            "emissive": r4(1.7 + 0.3 * tier), "render_mode": "billboard", "blend": "additive",
        }, {"sprite": "tex_rays"}, layer="impact")

    node("ps_streaks", "particle_system", {
        "max_particles": 260, "lifetime": 0.17, "lifetime_variance": 0.07, "size": 0.03, "size_variance": 0.012,
        "size_over_life": [[0.0, 1.0], [1.0, 0.35]],
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, HOT], [0.4, MID], [1.0, DEEP]],
        "opacity_over_life": [[0.0, 1.0], [0.6, 0.8], [1.0, 0.0]],
        "emissive": 3.2, "render_mode": "stretched_billboard", "velocity_stretch": 1.5, "drag": 7.5,
        "blend": "additive",
    }, {"sprite": "tex_spark"}, layer="impact")

    node("ps_ring", "particle_system", {
        "max_particles": 12, "lifetime": 0.22, "size": 2.3, "size_variance": 0.2,
        "size_over_life": [[0.0, 0.15], [0.35, 0.72], [1.0, 1.0]],
        "rotation_variance": 180.0,
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, MID], [0.5, MID], [1.0, DEEP]],
        "opacity": 0.55, "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.5, 0.4], [1.0, 0.0]],
        "emissive": 1.1, "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_ring"}, layer="impact")

    spark = {
        "size": 0.032, "size_variance": 0.012,
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, HOT], [0.3, MID], [1.0, DEEP]],
        "emissive": 3.4, "opacity_over_life": [[0.0, 1.0], [0.75, 1.0], [1.0, 0.0]],
        "render_mode": "stretched_billboard", "velocity_stretch": 0.2, "drag": 0.6, "bounce": 0.4, "friction": 0.4,
        "blend": "additive",
    }
    node("ps_sparks", "particle_system", {"max_particles": 420, "lifetime": 0.72, "lifetime_variance": 0.26, **spark},
         {"sprite": "tex_spark", "forces": ["gravity"], "colliders": ["ground"]}, layer="sparks")
    # The final impacts land 0.57 s before the end: shorter lives, so nothing pops at the loop.
    node("ps_sparks_final", "particle_system",
         {"max_particles": 520, "lifetime": 0.44, "lifetime_variance": 0.11, **spark},
         {"sprite": "tex_spark", "forces": ["gravity"], "colliders": ["ground"]}, layer="sparks")

    mote = {
        "size": 0.06, "size_variance": 0.035, "size_over_life": [[0.0, 0.5], [0.2, 1.0], [1.0, 0.4]],
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, HOT], [0.3, MID], [1.0, DEEP]],
        "emissive": 3.4, "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.75, 0.9], [1.0, 0.0]],
        "render_mode": "billboard", "drag": 1.6, "blend": "additive",
    }
    node("ps_motes", "particle_system", {"max_particles": 500, "lifetime": 0.55, "lifetime_variance": 0.14, **mote},
         {"sprite": "tex_spark", "forces": ["drift_gravity", "drift_curl"]}, layer="sparks")
    # What is left when the links die: short lives, so the last one is gone at 2.0 s.
    node("ps_fade", "particle_system", {"max_particles": 1100, "lifetime": 0.2, "lifetime_variance": 0.02, **mote},
         {"sprite": "tex_spark", "forces": ["drift_gravity", "drift_curl"]}, layer="sparks")

    node("ps_crackle", "particle_system", {
        "max_particles": 80, "lifetime": 0.085, "lifetime_variance": 0.03, "size": 0.62, "size_variance": 0.24,
        "rotation_variance": 180.0,
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, HOT], [0.4, BOLT_VIOLET], [1.0, DEEP]],
        "opacity_over_life": [[0.0, 1.0], [0.7, 1.0], [1.0, 0.0]],
        "emissive": 2.6, "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_crackle"}, layer="crawl")


# ---------------------------------------------------------------------------
# one hop: leader, stroke, linger, companions
# ---------------------------------------------------------------------------


def restrike_keys(hop: Hop, rng: random.Random) -> list[tuple]:
    """Emissive of the lingering link: a dim flickering base, a re-strike every time
    a later hop lands (the whole chain re-energises) and a couple of its own."""
    base = 1.5
    bumps: dict[int, float] = {}
    for later in hits_after(hop.hit):
        if later.hit - hop.hit >= 4:
            bumps[later.hit] = max(bumps.get(later.hit, 0.0), 3.4 * later.power)
    # its own re-strikes, dimmer each time, in the gaps
    frame = hop.hit + 24 + int(rng.uniform(0, 8))
    own = 3.0
    while frame < hop.end - 12:
        if all(abs(frame - b) > 9 for b in bumps):
            bumps[frame] = own
            own = max(2.1, own * 0.82)
        frame += 15 + int(rng.uniform(0, 12))
    keys: list[tuple] = [(hop.hit + 1, 0.0, "step"), (hop.hit + 2, 0.0), (hop.hit + 9, base)]
    for frame in sorted(bumps):
        if frame <= hop.hit + 10:
            continue
        keys += [(frame - 1, base), (frame, bumps[frame]), (frame + 3, base + (bumps[frame] - base) * 0.35),
                 (frame + 8, base)]
    # the death: one last flare, then nothing
    keys += [(hop.end - 5, base * 0.8), (hop.end - 3, 2.8), (hop.end - 1, 0.8), (hop.end, 0.0, "step")]
    keys.sort(key=lambda k: k[0])
    clean: list[tuple] = []
    for key in keys:
        if clean and key[0] <= clean[-1][0]:
            continue
        clean.append(key)
    return clean


def build_hop(hop: Hop, index: int) -> list[str]:
    rng = random.Random(SEED * 7 + index * 101)
    a, b = ANCHORS[hop.a], ANCHORS[hop.b]
    p = hop.power
    beams: list[str] = []
    depart = hop.hit - STROKE_TRAVEL
    prong = 0.78 if len(hops_out_of(hop.a)) > 1 else 1.0

    # --- leader: the travel arc. The tip accelerates into the target. ------------------
    steps = depart - hop.lead
    tip = [(hop.lead + i, v3(lerp3(a, b, 0.05 + 0.95 * (i / steps) ** 1.7))) for i in range(steps + 1)]
    beams.append(node(f"{hop.id}_leader", "beam", {
        "origin": v3(a), "target": track(tip),
        "width": r4(0.05 * p), "segments": 8, "detail": 3,
        "noise_amplitude": 0.3, "noise_frequency": 1.4, "jitter_rate": 36.0,
        "width_profile": "taper_end", "width_variance": 0.3,
        "branching": 4, "branch_probability": 0.7, "branch_length": 0.32, "branch_width": 0.6,
        "branch_depth": 2, "branch_intensity": 0.6,
        "intensity_noise": 0.4, "flicker": 0.5, "flicker_frequency": 52.0, "afterglow": 0.05,
        "impact_flare": 0.13, "core_width": 0.16, "glow_width": 3.0,
        "color": BOLT,
        "emissive": track([(hop.lead - 1, 0.0, "step"), (hop.lead, 2.2), (depart, 3.6), (hop.hit - 2, 1.6),
                           (hop.hit - 1, 0.0, "step")]),
        "blend": "additive", **window(hop.lead - 1, hop.hit),
    }, layer="links"))

    # --- stroke: the hero bolt, racing down the channel and landing on `hit` ----------
    h = hop.hit
    beams.append(node(f"{hop.id}_stroke", "beam", {
        "origin": v3(a), "target": track([(depart, v3(lerp3(a, b, 0.02))), (h, v3(b))]),
        "width": track([(depart, 0.08), (h, 0.125), (h + 3, 0.11), (h + 10, 0.09), (h + 19, 0.065)], p * prong),
        "segments": 10, "detail": 3,
        "noise_amplitude": 0.4, "noise_frequency": 1.15, "jitter_rate": 30.0,
        "width_profile": "taper_end", "width_variance": 0.34,
        "branching": 5, "branch_probability": 0.75, "branch_length": 0.3, "branch_width": 0.5,
        "branch_depth": 2, "branch_intensity": 0.55,
        "intensity_noise": 0.4, "flicker": 0.45, "flicker_frequency": 48.0, "afterglow": 0.08,
        "impact_flare": r4(0.25 * p), "core_width": 0.1, "glow_width": 3.6,
        "color": BOLT,
        "emissive": track([(depart, 0.0, "step"), (depart + 1, 5.0), (h - 1, 6.0), (h, 7.6), (h + 2, 4.2),
                           (h + 4, 6.2), (h + 7, 3.0), (h + 10, 4.4), (h + 13, 2.2), (h + 16, 2.9),
                           (h + 19, 0.0, "step")], p),
        "blend": "additive", **window(depart, h + 19),
    }, layer="links"))

    # --- linger: the link that stays, flickers and re-strikes ---------------------------
    beams.append(node(f"{hop.id}_linger", "beam", {
        "origin": v3(a), "target": v3(b),
        "width": track([(h + 2, 0.08), (h + 14, 0.07), (hop.end, 0.05)], p),
        "segments": 9, "detail": 3,
        "noise_amplitude": 0.34, "noise_frequency": 1.2, "jitter_rate": 15.0,
        "width_profile": "taper_both", "width_variance": 0.3,
        "branching": 3, "branch_probability": 0.6, "branch_length": 0.22, "branch_width": 0.5,
        "branch_depth": 1, "branch_intensity": 0.5,
        "intensity_noise": 0.45, "flicker": 0.55, "flicker_frequency": 36.0, "afterglow": 0.06,
        "impact_flare": r4(0.15 * p), "core_width": 0.12, "glow_width": 3.3,
        "color": BOLT, "emissive": track(restrike_keys(hop, rng)),
        "blend": "additive", **window(h + 1, hop.end),
    }, layer="links"))

    # --- companions: thin arcs on slightly different end points, blinking ----------------
    for k in range(2):
        oa = [rng.uniform(-0.12, 0.12), rng.uniform(-0.14, 0.14), rng.uniform(-0.12, 0.12)]
        ob = [rng.uniform(-0.2, 0.2), rng.uniform(-0.3, 0.3), rng.uniform(-0.2, 0.2)]
        level = 1.5 - 0.3 * k
        keys: list[tuple] = [(h - 1, 0.0, "step"), (h, 4.2 - 0.6 * k), (h + 5, 2.6), (h + 12, level)]
        frame = h + 14 + int(rng.uniform(0, 8))
        stop = hop.end - 2 - int(rng.uniform(0, 5))
        later = {x.hit: x.power for x in hits_after(h)}
        while frame < stop - 6:
            gap = 2 + int(rng.uniform(0, 3))               # blink out for a few frames
            keys += [(frame, 0.0, "step"), (frame + gap, level * rng.uniform(1.2, 1.9)), (frame + gap + 4, level)]
            frame += gap + 9 + int(rng.uniform(0, 14))
        for hit_frame, power in later.items():
            if h + 12 < hit_frame < stop - 4:
                keys += [(hit_frame, 3.0 * power), (hit_frame + 5, level)]
        keys += [(stop, 0.0, "step")]
        keys.sort(key=lambda key: key[0])
        clean: list[tuple] = []
        for key in keys:
            if clean and key[0] <= clean[-1][0]:
                continue
            clean.append(key)
        beams.append(node(f"{hop.id}_arc{k + 1}", "beam", {
            "origin": v3(add3(a, oa)), "target": v3(add3(b, ob)),
            "width": r4((0.044 - 0.008 * k) * p), "segments": 6 if k == 0 else 12, "detail": 3,
            "noise_amplitude": 0.5 if k == 0 else 0.24, "noise_frequency": 0.8 if k == 0 else 1.7,
            "jitter_rate": r4(20.0 + 8 * k),
            "width_profile": "taper_both", "width_variance": 0.3,
            "branching": 2, "branch_probability": 0.6, "branch_length": 0.25, "branch_width": 0.5,
            "branch_depth": 1, "branch_intensity": 0.5,
            "intensity_noise": 0.4, "flicker": 0.5, "flicker_frequency": 50.0,
            "core_width": 0.17, "glow_width": 2.7,
            "color": BOLT_VIOLET, "emissive": track(clean),
            "blend": "additive", **window(h - 1, stop),
        }, layer="links"))
    return beams


# ---------------------------------------------------------------------------
# crawling mini arcs over the capsule of a target, and around the orb
# ---------------------------------------------------------------------------


def blip_schedule(rng: random.Random, first: int, last: int, busy: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """(on_frame, off_frame) pairs: dense inside the `busy` windows, sparse elsewhere."""
    out = []
    frame = first
    while frame < last - 2:
        dense = any(lo <= frame <= hi for lo, hi in busy)
        on = 3 + int(rng.uniform(0, 3 if dense else 4))
        off = int(rng.uniform(1, 4)) if dense else int(rng.uniform(4, 11))
        out.append((frame, min(frame + on, last)))
        frame += on + off
    return out


def build_crawl(anchor: str, index: int) -> list[str]:
    rng = random.Random(SEED * 13 + index * 53)
    centre = ANCHORS[anchor]
    arriving = hops_into(anchor)
    leaving = hops_out_of(anchor)
    first = min(x.hit for x in arriving)
    last = max(x.end for x in arriving + leaving) + 2
    power = max(x.power for x in arriving)
    # busy right after the impact and while the next jump gathers here
    busy = [(first, first + 16)] + [(x.lead - 8, x.hit) for x in leaving]
    beams = []
    for k in range(3):
        grounding = k == 0
        origin_keys, target_keys, width_keys, glow_keys = [], [], [], []
        for on, off in blip_schedule(rng, first + k * 2, last, busy):
            if grounding:
                # from where the bolt lands, down the body, into the floor
                p1, theta, _ = capsule_point(rng, centre, y=centre[1] + rng.uniform(-0.3, 0.25), swell=0.9)
                foot, reach = theta + rng.uniform(-0.9, 0.9), rng.uniform(0.3, 0.8)
                p2 = [centre[0] + math.cos(foot) * reach, 0.03, centre[2] + math.sin(foot) * reach]
            else:
                p1, theta, y = capsule_point(rng, centre, swell=1.05)
                turn = rng.uniform(0.8, 2.0) * (1 if rng.random() < 0.5 else -1)
                p2, _, _ = capsule_point(rng, centre, theta + turn,
                                         y + rng.uniform(0.35, 0.85) * (1 if rng.random() < 0.5 else -1), swell=1.05)
            level = rng.uniform(2.2, 3.4) * (1.25 if any(lo <= on <= hi for lo, hi in busy) else 1.0)
            origin_keys.append((on, v3(p1), "step"))
            target_keys.append((on, v3(p2), "step"))
            width_keys += [(on, r4(rng.uniform(0.02, 0.03) * power), "step"), (off, 0.0, "step")]
            glow_keys += [(on, r4(level), "step"), (off, 0.0, "step")]
        beams.append(node(f"{anchor}_crawl{k + 1}", "beam", {
            "origin": track(origin_keys), "target": track(target_keys), "width": track(width_keys),
            "segments": 6, "detail": 3 if grounding else 2, "noise_amplitude": 0.16 if grounding else 0.12,
            "noise_frequency": 3.0 if grounding else 5.0, "jitter_rate": 40.0,
            "width_profile": "taper_end" if grounding else "taper_both", "width_variance": 0.3,
            "branching": 1, "branch_probability": 0.6, "branch_length": 0.4, "branch_width": 0.5,
            "branch_depth": 1, "branch_intensity": 0.6,
            "intensity_noise": 0.35, "flicker": 0.5, "flicker_frequency": 56.0,
            "core_width": 0.2, "glow_width": 2.6,
            "color": BOLT_VIOLET, "emissive": track(glow_keys),
            "blend": "additive", **window(first, last),
        }, layer="crawl"))
    return beams


def build_orb_arcs() -> list[str]:
    rng = random.Random(SEED * 17)
    centre = ANCHORS[SOURCE]
    last = max(x.end for x in hops_out_of(SOURCE))
    first_hit = min(x.hit for x in hops_out_of(SOURCE))
    busy = [(4, first_hit + 6)]
    beams = []
    for k in range(3):
        target_keys, width_keys, glow_keys = [], [], []
        for on, off in blip_schedule(rng, 4 + k, last, busy):
            grow = min(1.0, 0.45 + on / 14.0)                        # the arcs reach further as the orb charges
            reach = rng.uniform(0.3, 0.58) * grow
            theta, phi = rng.uniform(0.0, 2.0 * math.pi), rng.uniform(-0.9, 1.1)
            end = [centre[0] + math.cos(theta) * math.cos(phi) * reach, centre[1] + math.sin(phi) * reach,
                   centre[2] + math.sin(theta) * math.cos(phi) * reach]
            target_keys.append((on, v3(end), "step"))
            width_keys += [(on, r4(rng.uniform(0.02, 0.03)), "step"), (off, 0.0, "step")]
            glow_keys += [(on, r4(rng.uniform(2.4, 3.8) * (1.0 if on < first_hit + 6 else 0.7)), "step"),
                          (off, 0.0, "step")]
        beams.append(node(f"orb_arc{k + 1}", "beam", {
            "origin": v3(centre), "target": track(target_keys), "width": track(width_keys),
            "segments": 6, "detail": 2, "noise_amplitude": 0.09, "noise_frequency": 6.0, "jitter_rate": 44.0,
            "width_profile": "taper_end", "width_variance": 0.3,
            "branching": 2, "branch_probability": 0.65, "branch_length": 0.45, "branch_width": 0.55,
            "branch_depth": 1, "branch_intensity": 0.6,
            "intensity_noise": 0.35, "flicker": 0.5, "flicker_frequency": 58.0,
            "core_width": 0.2, "glow_width": 2.6,
            "color": BOLT_VIOLET, "emissive": track(glow_keys),
            "blend": "additive", **window(4, last),
        }, layer="cast"))
    return beams


# ---------------------------------------------------------------------------
# the cast: a crackling plasma orb at the source
# ---------------------------------------------------------------------------


def build_cast() -> None:
    src = ANCHORS[SOURCE]
    out = hops_out_of(SOURCE)
    depart = min(x.hit for x in out) - STROKE_TRAVEL
    hit = min(x.hit for x in out)
    end = max(x.end for x in out)

    white = [0.9, 0.95, 1.0, 1.0]
    node("ps_orb", "particle_system", {
        "max_particles": 24, "lifetime": 0.16, "lifetime_variance": 0.04, "size": 0.74, "size_variance": 0.16,
        "size_over_life": [[0.0, 0.55], [0.3, 1.0], [1.0, 0.8]],
        "rotation_variance": 180.0, "angular_velocity": 60.0, "angular_velocity_variance": 120.0,
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, HOT], [0.5, MID], [1.0, BOLT_VIOLET]],
        "opacity": 0.85, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": 2.0, "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_plasma"}, layer="cast")
    node("orb", "emitter", {
        "shape": "sphere", "radius": 0.03, "position": v3(src), "velocity": 0.0,
        "rate": track([(0, 26.0), (12, 40.0), (depart, 46.0), (hit + 4, 24.0), (hit + 20, 13.0),
                       (end - 4, 10.0), (end, 0.0)]),
        **window(0, end),
    }, {"particle": "ps_orb"}, layer="cast")

    node("ps_orb_core", "particle_system", {
        "max_particles": 16, "lifetime": 0.11, "lifetime_variance": 0.03, "size": 0.42, "size_variance": 0.1,
        "size_over_life": [[0.0, 0.6], [0.4, 1.0], [1.0, 0.7]],
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, white], [0.5, HOT], [1.0, MID]],
        "opacity": 0.9, "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [1.0, 0.0]],
        "emissive": 1.8, "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_glow"}, layer="cast")
    node("orb_core", "emitter", {
        "shape": "point", "position": v3(src), "velocity": 0.0,
        "rate": track([(0, 22.0), (12, 34.0), (hit, 40.0), (hit + 10, 22.0), (end - 3, 16.0), (end, 0.0)]),
        **window(0, end),
    }, {"particle": "ps_orb_core"}, layer="cast")

    # charge gathering: streaks falling into the orb, and again wherever a jump is about to leave
    node("ps_gather", "particle_system", {
        "max_particles": 220, "lifetime": 0.24, "lifetime_variance": 0.04, "size": 0.04, "size_variance": 0.015,
        "size_over_life": [[0.0, 0.5], [0.6, 1.0], [1.0, 0.6]],
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": [[0.0, DEEP], [0.6, MID], [1.0, HOT]],
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.9, 1.0], [1.0, 0.0]],
        "emissive": 3.0, "render_mode": "stretched_billboard", "velocity_stretch": 0.28, "blend": "additive",
    }, {"sprite": "tex_spark"}, layer="cast")
    node("orb_gather", "emitter", {
        "shape": "sphere", "radius": 0.95, "surface_only": True, "position": v3(src),
        "velocity": 0.0, "radial_velocity": -3.9,
        "rate": track([(0, 120.0), (9, 260.0), (depart - 6, 150.0), (depart - 2, 0.0)]),
        **window(0, depart),
    }, {"particle": "ps_gather"}, layer="cast")

    node("src_light", "light", {
        "light_type": "point", "position": v3(add3(src, [0.0, 0.1, 0.35])), "color": LIGHT, "radius": 3.4,
        "intensity": track([(0, 0.0), (12, 1.3), (depart, 1.9), (hit, 3.0), (hit + 8, 1.0), (end - 4, 0.7),
                            (end + 2, 0.0)]),
        "flicker_amplitude": 0.35, "flicker_frequency": 34.0, **window(0, end + 2),
    }, layer="light")

    node("src_charge", "decal", {
        "shape": "circle", "position": [r4(src[0]), 0.014, r4(src[2])],
        "size": track([(0, [2.6, 2.6]), (12, [1.9, 1.9]), (depart, [1.7, 1.7]), (hit + 6, [2.1, 2.1]),
                       (end, [2.2, 2.2])]),
        "rotation": track([(0, [0.0, 0.0, 0.0]), (end + 2, [0.0, 50.0, 0.0])]),
        "color": MID, "blend": "additive",
        "emissive": track([(0, 0.6), (12, 1.5), (depart, 1.9), (hit, 2.6), (hit + 10, 1.0), (end, 0.5)]),
        "opacity": track([(0, 0.0), (10, 0.4), (depart, 0.55), (hit, 0.8), (hit + 12, 0.32), (end - 4, 0.22),
                          (end + 2, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, **window(0, end + 2),
    }, {"texture": "tex_charge"}, layer="ground")


# ---------------------------------------------------------------------------
# a target: the impact, and everything that says "something stands here"
# ---------------------------------------------------------------------------


def link_rotation(a, b) -> list[float]:
    """Euler XYZ (R = Rz Ry Rx) taking local +X onto the direction a -> b."""
    d = [b[i] - a[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in d)) or 1.0
    d = [c / length for c in d]
    return [0.0, r4(-math.degrees(math.asin(max(-1.0, min(1.0, d[2]))))), r4(math.degrees(math.atan2(d[1], d[0])))]


def build_target(anchor: str, index: int) -> None:
    rng = random.Random(SEED * 19 + index * 71)
    pos = ANCHORS[anchor]
    arriving = hops_into(anchor)
    leaving = hops_out_of(anchor)
    hop = min(arriving, key=lambda x: x.hit)
    h, p = hop.hit, hop.power
    end = max(x.end for x in arriving + leaving)
    final = hop.final
    tier = TIERS[hop.tier]
    incoming = ANCHORS[hop.a]
    along = [pos[i] - incoming[i] for i in range(3)]
    norm = math.sqrt(sum(c * c for c in along)) or 1.0
    along = [c / norm for c in along]
    burst = {"rate": 0.0, "burst_times": [0.0], "start_time": fs(h), "duration": 0.1}

    # core flash + irregular ray burst + shock ring (one or two quads each)
    node(f"{anchor}_flash", "emitter", {"shape": "point", "position": v3(pos), "velocity": 0.0,
                                         "burst_count": 1, **burst},
         {"particle": f"ps_flash_{hop.tier}"}, layer="impact")
    node(f"{anchor}_rays", "emitter", {"shape": "point", "position": v3(pos), "velocity": 0.0,
                                        "burst_count": int(tier["ray_count"]), **burst},
         {"particle": f"ps_rays_{hop.tier}"}, layer="impact")
    halo = dict(burst)
    halo["burst_times"] = [0.0, 0.06][:int(tier["halos"])]
    node(f"{anchor}_ring", "emitter", {"shape": "point", "position": v3(pos), "velocity": 0.0,
                                        "burst_count": 1, **halo},
         {"particle": "ps_ring"}, layer="impact")
    # radial streaks: the particle half of the ray burst, random by construction
    node(f"{anchor}_streaks", "emitter", {
        "shape": "sphere", "radius": 0.07, "position": v3(pos), "direction": [0, 0, 0],
        "velocity": tier["streak_speed"], "velocity_variance": r4(tier["streak_speed"] * 0.55),
        "burst_count": int(tier["streaks"]), **burst,
    }, {"particle": "ps_streaks"}, layer="impact")

    # sparks: thrown onward along the bolt and outward, gravity, ground bounce
    node(f"{anchor}_sparks", "emitter", {
        "shape": "sphere", "radius": 0.12, "position": v3(pos),
        "direction": v3([along[0], along[1] + 0.35, along[2]]), "spread": 80.0,
        "velocity": tier["spark_speed"], "velocity_variance": r4(tier["spark_speed"] * 0.62),
        "radial_velocity": 1.6, "burst_count": int(tier["sparks"]), **burst,
    }, {"particle": "ps_sparks_final" if final else "ps_sparks"}, layer="sparks")

    # crackle sprites and drifting motes from the capsule volume (emit from it, never draw it)
    body = {"shape": "box", "size": [0.8, 1.45, 0.8], "position": [r4(pos[0]), 1.05, r4(pos[2])], "velocity": 0.0}
    gather = [(x.lead - 8, x.hit) for x in leaving]
    crackle = [(h - 1, 0.0), (h, 60.0 * p), (h + 10, 30.0), (h + 22, 15.0)]
    for lo, hi in gather:
        crackle += [(lo, 15.0), (hi - 6, 46.0), (hi + 4, 18.0)]
    crackle += [(end - 8, 14.0), (end - 2, 24.0), (end + 1, 0.0)]
    crackle.sort(key=lambda key: key[0])
    node(f"{anchor}_crackle", "emitter", {**body, "rate": track(crackle), **window(h - 1, end + 1)},
         {"particle": "ps_crackle"}, layer="crawl")

    motes_stop = min(end - 6, 78)          # 0.55 s lives: nothing may outlast the effect
    if motes_stop > h + 6:
        node(f"{anchor}_motes", "emitter", {
            **body, "velocity": 0.5, "velocity_variance": 0.4, "direction": [0, 1, 0], "spread": 70.0,
            "rate": track([(h - 1, 0.0), (h, 90.0 * p), (h + 6, 26.0), (motes_stop - 2, 20.0), (motes_stop, 0.0)]),
            **window(h - 1, motes_stop),
        }, {"particle": "ps_motes"}, layer="sparks")
    node(f"{anchor}_fade", "emitter", {
        **body, "velocity": 0.7, "velocity_variance": 0.5, "direction": [0, 0, 0],
        "rate": track([(end - 1, 0.0), (end, 150.0 * p), (FADE_STOP - 1, 80.0), (FADE_STOP, 0.0)]),
        "burst_count": int(round(46 * p)), "burst_times": [0.0], **window(end - 1, FADE_STOP),
    }, {"particle": "ps_fade"}, layer="sparks")

    # pre-jump gather: charge falls into the target just before the chain leaves it
    for x in leaving[:1]:
        node(f"{anchor}_gather", "emitter", {
            "shape": "sphere", "radius": 0.85, "surface_only": True, "position": v3(pos),
            "velocity": 0.0, "radial_velocity": -3.5,
            "rate": track([(x.lead - 9, 0.0), (x.lead - 8, 110.0), (x.lead, 240.0), (x.hit - 8, 80.0),
                           (x.hit - 5, 0.0)]),
            **window(x.lead - 9, x.hit - 5),
        }, {"particle": "ps_gather"}, layer="cast")

    # light: a flash on the hit, then the glow of a target that is still in the chain
    later = sorted({x.hit: x.power for x in hits_after(h)}.items())
    peak = tier["light"]
    keys: list[tuple] = [(h - 1, 0.0), (h, peak), (h + 3, peak * 0.45), (h + 9, 0.55)]
    for frame, power in later:
        if h + 10 < frame < end - 4:
            keys += [(frame - 1, 0.5), (frame, 1.3 * power), (frame + 6, 0.5)]
    keys += [(end - 3, 0.45), (end + 2, 0.0)]
    keys.sort(key=lambda key: key[0])
    node(f"{anchor}_light", "light", {
        "light_type": "point", "position": v3(add3(pos, [0.0, -0.15, 0.4])), "color": LIGHT,
        "radius": r4(2.6 + 0.5 * hop.tier), "intensity": track(keys),
        "flicker_amplitude": 0.4, "flicker_frequency": 42.0, **window(h - 1, end + 2),
    }, layer="light")

    # ground: a faint scorch, and two vein decals flickering out of phase so the arcs crawl
    size = tier["veins"]
    node(f"{anchor}_scorch", "decal", {
        "shape": "circle", "position": [r4(pos[0]), 0.006, r4(pos[2])], "size": [r4(size * 0.8), r4(size * 0.8)],
        "rotation": [0.0, r4(rng.uniform(0, 360)), 0.0],
        "color": [0.02, 0.023, 0.035, 1.0], "blend": "alpha",
        "opacity": track([(h - 1, 0.0), (h + 2, 0.5), (110, 0.42), (120, 0.0)]),
        "fade_in": 0.0, "fade_out": 0.0, **window(h - 1, 120),
    }, {"texture": "tex_scorch"}, layer="ground")
    for k in range(2):
        phase = k * 5
        glow: list[tuple] = [(h - 1, 0.0), (h, 2.4 - 0.5 * k), (h + 4, 1.1)]
        frame = h + 8 + phase
        while frame < end - 6:
            glow += [(frame, rng.uniform(1.0, 1.7)), (frame + 4, rng.uniform(0.35, 0.6))]
            frame += 9 + int(rng.uniform(0, 5))
        for hit_frame, power in later:
            if h + 8 < hit_frame < end - 4:
                glow += [(hit_frame, 1.9 * power)]
        glow += [(end - 2, 0.9), (end + 5, 0.0)]
        glow.sort(key=lambda key: key[0])
        clean: list[tuple] = []
        for key in glow:
            if clean and key[0] <= clean[-1][0]:
                continue
            clean.append(key)
        start_angle = rng.uniform(0, 360)
        node(f"{anchor}_veins{k + 1}", "decal", {
            "shape": "circle", "position": [r4(pos[0]), r4(0.016 + 0.006 * k), r4(pos[2])],
            "size": track([(h - 1, [r4(size * 0.5), r4(size * 0.5)]), (h + 4, [size, size]),
                           (end + 5, [r4(size * 1.12), r4(size * 1.12)])]),
            "rotation": track([(h - 1, [0.0, r4(start_angle), 0.0]),
                               (end + 5, [0.0, r4(start_angle + (26 if k else -34)), 0.0])]),
            "color": MID, "blend": "additive", "emissive": track(clean),
            "opacity": track([(h - 1, 0.0), (h, 0.85), (h + 10, 0.6), (end - 2, 0.42), (end + 5, 0.0)]),
            "fade_in": 0.0, "fade_out": 0.0, **window(h - 1, end + 5),
        }, {"texture": "tex_veins"}, layer="ground")


def build_link_fades() -> None:
    """When the links die, what is left of each one is a drift of sparks where the bolt wandered -
    a loose volume around the link, never a dotted ruler line. Plus the stroke's own brief light."""
    for hop in HOPS:
        a, b = ANCHORS[hop.a], ANCHORS[hop.b]
        mid = lerp3(a, b, 0.5)
        length = dist(a, b)
        common = {"shape": "box", "size": [r4(length * 0.94), 0.62, 0.62], "position": v3(mid),
                  "rotation": link_rotation(a, b)}
        h = hop.hit
        node(f"{hop.id}_light", "light", {
            "light_type": "point", "position": v3([mid[0], mid[1] - 0.25, mid[2]]), "color": LIGHT,
            "radius": 3.0, "intensity": track([(h - 3, 0.0), (h, 2.2 * hop.power), (h + 4, 0.9), (h + 12, 0.0)]),
            "flicker_amplitude": 0.4, "flicker_frequency": 46.0, **window(h - 3, h + 12),
        }, layer="light")
        node(f"{hop.id}_fade", "emitter", {
            **common, "velocity": 0.6, "velocity_variance": 0.5, "direction": [0, 1, 0], "spread": 180.0,
            "rate": track([(hop.end - 2, 0.0), (hop.end, 170.0 * length / 3.0), (FADE_STOP - 1, 90.0),
                           (FADE_STOP, 0.0)]),
            "burst_count": int(round(36 * length / 3.0)), "burst_times": [0.0, 0.035],
            **window(hop.end - 2, FADE_STOP),
        }, {"particle": "ps_fade"}, layer="sparks")
        stop = min(hop.end - 8, 80)
        if stop > hop.hit + 8:
            node(f"{hop.id}_motes", "emitter", {
                **common, "velocity": 0.5, "velocity_variance": 0.4, "direction": [0, 1, 0], "spread": 180.0,
                "rate": track([(hop.hit, 0.0), (hop.hit + 1, 34.0), (hop.hit + 8, 12.0), (stop - 2, 10.0), (stop, 0.0)]),
                **window(hop.hit, stop),
            }, {"particle": "ps_motes"}, layer="sparks")


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

COLOUR_PARAMETERS = ("color", "color_over_life", "emissive_color", "base_color")


def build_controls(link_beams: list[str], small_beams: list[str]) -> list[dict[str, Any]]:
    def multiplier(cid, label, group, bindings, lo=0.0, hi=3.0, step=0.01):
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": 1.0, "value": 1.0,
                "step": step, "unit": "x", "bindings": bindings}

    def bind(ids, parameter, op="multiply"):
        return [{"node": nid, "parameter": parameter, "op": op} for nid in ids]

    def authored(nid, parameter):
        return next(n for n in nodes if n["id"] == nid)["parameters"].get(parameter)

    by_type: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        by_type.setdefault(n["type"], []).append(n)
    ids = lambda kind, test=lambda n: True: [n["id"] for n in by_type.get(kind, []) if test(n)]  # noqa: E731
    branched = [n["id"] for n in by_type["beam"] if n["id"] in link_beams]
    impact_systems = ids("particle_system", lambda n: n.get("layer") == "impact")
    spark_emitters = ids("emitter", lambda n: n["inputs"]["particle"] in
                         ("ps_sparks", "ps_sparks_final", "ps_motes", "ps_fade"))

    hue = []
    for n in nodes:
        if n["id"].endswith("_scorch"):
            continue
        for parameter in COLOUR_PARAMETERS:
            if parameter in n.get("parameters", {}):
                hue.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    return [
        multiplier("bolt_thickness", "Bolt thickness", "Chain links", bind(link_beams + small_beams, "width"),
                   lo=0.4, hi=2.5),
        multiplier("bolt_intensity", "Bolt intensity", "Chain links", bind(link_beams + small_beams, "emissive")),
        multiplier("branching", "Branching", "Chain links", bind(branched, "branching"), hi=2.5, step=0.05),
        multiplier("impact_burst", "Impact burst", "Impacts",
                   bind(impact_systems, "emissive") + bind(impact_systems, "color")
                   + bind(ids("light", lambda n: n["id"] != "src_light"), "intensity")
                   + bind(ids("emitter", lambda n: n["inputs"]["particle"] == "ps_streaks"), "burst_count")),
        multiplier("sparks", "Spark amount", "Sparks",
                   bind([e for e in spark_emitters if authored(e, "burst_count")], "burst_count")
                   + bind([e for e in spark_emitters if isinstance(authored(e, "rate"), dict)], "rate")),
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
    small = build_orb_arcs()
    links: list[str] = []
    for i, hop in enumerate(HOPS):
        links += build_hop(hop, i)
    targets = [name for name in ANCHORS if name != SOURCE and hops_into(name)]
    for i, name in enumerate(targets):
        build_target(name, i)
        small += build_crawl(name, i)
    build_link_fades()
    # Raised three-quarter view from the far end of the chain: the caster is up-left in the distance, the
    # hops come toward the viewer and the fork opens across the frame. The studio frames 1.45x wider.
    node("cam", "camera", {"position": [3.8, 4.87, 8.94], "target": [-0.2, 0.85, 0.35], "fov": 40.0})
    controls = build_controls(links, small)

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": DESCRIPTION,
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [{"name": n, "start": s, "end": e} for n, s, e in PHASES]},
        "layers": [
            {"id": "cast", "name": "Cast orb", "role": "telegraph"},
            {"id": "links", "name": "Chain links", "role": "primary"},
            {"id": "impact", "name": "Impacts", "role": "secondary"},
            {"id": "crawl", "name": "Crawling arcs", "role": "secondary"},
            {"id": "sparks", "name": "Sparks", "role": "aftermath"},
            {"id": "ground", "name": "Ground", "role": "interaction"},
            {"id": "light", "name": "Light", "role": "interaction"},
        ],
        "nodes": list(nodes),
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/chain_lightning.py",
            "prompt": "let's create the chain lightning effect. this one must chain between targets, see sheet.",
            "analysis": "cast 0-0.2 a plasma orb gathers charge | initial_strike 0.2-0.4 a forked leader reaches "
                        "target 1 and the stroke races down it | first_impact 0.4 flash, ray burst, sparks | "
                        "jump 0.6-0.9 charge gathers on target 1, leader then stroke to target 2 | second_impact 0.9 | "
                        "branch_jump 1.1-1.4 target 2 forks to targets 3 and 4 | final_impact 1.4 the two strongest "
                        "hits, the whole chain re-strikes and stands until 1.7 | dissipate 1.8-2.0 drifting sparks",
            "layout": {"anchors": {k: list(v) for k, v in ANCHORS.items()},
                       "hops": [{"id": x.id, "from": x.a, "to": x.b, "leader": ft(x.lead), "hit": ft(x.hit),
                                 "dies": ft(x.end)} for x in HOPS]},
            "render_settings": {
                "background": [0.0, 0.0, 0.0, 1.0],
                "ground_albedo": 0.08,
                "bloom_intensity": 0.2,
                "bloom_radius": 0.034,
                "exposure": 0.9,
                "grid": False,
            },
            "tags": ["lightning", "chain", "multi-target", "beam", "impact", "storm"],
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
