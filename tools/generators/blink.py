#!/usr/bin/env python3
"""Generate examples/effects/blink.json: an arcane blink authored in character space.

    python tools/generators/blink.py --out examples/effects
    python tools/generators/blink.py --check examples/effects/blink.json

Blink is a one-second mobility skill: a thin arcane ring ignites under the caster,
a vertical warp ring forms around the body facing the travel direction, implodes
and bursts into crystal glints and light streaks, violet afterimage wakes race to
the destination, where a second warp ring flares open, contracts and leaves motes
and a faint glow at both ends.

There is no character, no silhouette and no placeholder body anywhere. The effect
is authored in CHARACTER SPACE so a game can attach it to any character:

  anchors   ORIGIN = the caster's root (feet) at cast time = world (0, 0, 0).
            DESTINATION = the root on arrival = the hub node `destination` at
            (8, 0, 0); travel is along +X, the body is 1.8 m tall. Everything at
            the destination (warp ring, burst, ground ring, settle, fade) hangs
            under that ONE node, so the `blink_distance` control (multiply on
            `destination.position`) moves the whole arrival.
  body      `origin_body` and `destination_body` carry the body-sized parts (warp
            rings, bursts, glints, motes) and each wake runner has a `wake_N_body`;
            the `body_height` control multiplies their `scale`, so a small or a
            large character gets rings and wakes that fit.
  transit   three runner hubs `wake_1..3` whose keyframed positions go from the
            origin to (8, 0, 0) with y = z = 0 in every key. `blink_distance`
            multiplies those tracks key by key, so the runners always end on the
            destination: the wakes connect both rings at any distance. The path
            streaks are line emitters on `path_mid` (the midpoint hub, moved by the
            same control) whose `length` is bound to it too.
  afterimage no body is drawn: each runner carries a 0.5 m x 1.8 m column of soft
            vertical light streaks, horizontal velocity-stretched motes, a soft haze
            and two tapered light trails. Particles are world space, so the column
            is laid down along the path and fades within a few frames - a smeared,
            body-sized wake racing to the destination. Engines can layer real
            afterimages of the character mesh on top (metadata.attachment).
  rings     the warp rings use the parent-chain technique of teleport_variants.py,
            rotated to stand across the travel axis: a hub rotated 90 degrees about
            Z (its local XZ plane is the world YZ plane) whose non-uniform keyframed
            scale is the ellipse and its open / flare / contract envelope. Ring
            emitters on that hub lay dense soft glow sprites exactly on the ellipse
            (never a polygon), and two spinning beads draw short comet arcs.
  distortion a brief `heat_haze` post effect at the implosion and at the flare, plus
            camera-facing soft ripple rings.

House style: no crosses, plus signs or four-armed stars (bursts are swarms of 40+
irregular streaks, glints are thin two-tone flakes); nothing is a hard line (rings
are overlapping soft sprites, trails taper to zero with a soft cross-width texture);
hot cores stay small and short.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

SLUG = "blink"
NAME = "Blink"
DESCRIPTION = ("A warp ring swallows the caster in a burst of crystal glints, violet afterimage wakes race to the "
               "destination and a second ring flares open there, leaving motes and a faint glow at both ends.")
DURATION = 1.0
SEED = 51413
FPS = 60.0

DISTANCE = 8.0          # default blink distance along +X (m)
BODY = 1.8              # body height the rings and wakes are sized for (m)
RING_Y = 1.0            # warp ring centre above the root
RING_RY = 1.02          # vertical semi-axis of the warp ellipse
RING_RZ = 0.78          # horizontal semi-axis (across the travel axis)

# The concept sheet's key frames.
PHASES = [
    ("pre_cast", 0.0, 0.1),
    ("distortion", 0.1, 0.2),
    ("disappear", 0.2, 0.3),
    ("transit", 0.3, 0.4),
    ("reappear", 0.4, 0.5),
    ("settle", 0.5, 0.6),
    ("fade", 0.6, 1.0),
]

BURST_O = 15 / FPS      # the caster vanishes (0.25 s)
BURST_D = 24 / FPS      # ... and reappears (0.40 s)

#: Afterimage wakes: (id, departure frame, arrival frame). Seven frames of travel each, staggered by three.
WAKES = [("wake_1", 15, 22), ("wake_2", 18, 25), ("wake_3", 21, 28)]
EASE = 1.35             # x(u) = 1 - (1 - u)^EASE: leaves fast, eases into the destination
#: The frame each wake leaves its lingering afterimage copy: about 20%, 53% and 82% of the path.
STAMPS = [16, 21, 26]

# Palette (linear RGB). Every colour in the document derives from these, so one hue control recolours it.
WHITE_HOT = [0.9, 0.86, 1.0, 1.0]
LILAC = [0.7, 0.58, 1.0, 1.0]
VIOLET = [0.5, 0.24, 1.0, 1.0]
INDIGO = [0.24, 0.13, 0.9, 1.0]
DEEP = [0.12, 0.05, 0.45, 1.0]
AZURE = [0.4, 0.56, 1.0, 1.0]

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def r4(v: float) -> float:
    return round(float(v), 4)


def fr(t: float) -> float:
    """Snap a time to the 60 fps frame grid."""
    return r4(round(t * FPS) / FPS)


def tint(rgba: list[float], k: float, alpha: float | None = None) -> list[float]:
    return [r4(rgba[0] * k), r4(rgba[1] * k), r4(rgba[2] * k), rgba[3] if alpha is None else alpha]


def mix(a: list[float], b: list[float], t: float) -> list[float]:
    return [r4(a[i] + (b[i] - a[i]) * t) for i in range(3)] + [1.0]


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


def sample(pairs: list[tuple[float, float]], t: float) -> float:
    if t <= pairs[0][0]:
        return pairs[0][1]
    for (t0, v0), (t1, v1) in zip(pairs, pairs[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pairs[-1][1]


nodes: list[dict[str, Any]] = []


def node(nid: str, ntype: str, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None,
         layer: str | None = None, parent: str | None = None, seed: int | None = None) -> str:
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


def hub(nid: str, params: dict[str, Any] | None = None, layer: str | None = None, parent: str | None = None) -> str:
    """An invisible transform node: anchors, runners and ring rigs."""
    return node(nid, "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, **(params or {})},
                layer=layer, parent=parent)


def g(nid: str, op: str, params: dict[str, Any] | None = None, inputs: dict[str, str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": nid, "op": op}
    if params:
        out["params"] = params
    if inputs:
        out["inputs"] = inputs
    return out


def fold(prefix: str, mode: str, ids: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Chain a binary math op over several inputs; returns (ops, id of the result)."""
    ops, acc = [], ids[0]
    for i, other in enumerate(ids[1:]):
        nid = f"{prefix}{i}"
        ops.append(g(nid, "math", {"mode": mode}, {"a": acc, "b": other}))
        acc = nid
    return ops, acc


def window(start: float, end: float) -> dict[str, float]:
    return {"start_time": fr(start), "duration": r4(fr(end) - fr(start))}


def burst_window(when: float) -> dict[str, Any]:
    """An emitter that fires once, on the frame `when` lands on."""
    return {"rate": 0, "burst_times": [0.0], "start_time": r4(fr(when) - 0.001), "duration": 0.05}


# ---------------------------------------------------------------------------
# textures (all grayscale: the node that draws them brings the colour)
# ---------------------------------------------------------------------------


def build_textures() -> None:
    # A soft glow whose falloff reaches the quad edge, so no fill is wasted.
    node("tex_glow", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("l", "levels", {"gamma": 0.8}, {"a": "r"}),
    ], "output": "l"}})

    node("tex_mote", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        g("l", "levels", {"gamma": 0.85}, {"a": "r"}),
    ], "output": "l"}})

    # Soft light smear for the wake haze: a glow broken by noise so stacked copies never look round.
    node("tex_haze", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("n", "fbm", {"frequency": 3.2, "octaves": 3, "seed": 23}),
        g("nl", "levels", {"in_low": 0.25, "in_high": 0.8, "out_low": 0.3}, {"a": "n"}),
        g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
    ], "output": "m"}})

    # The afterimage column: a broad soft glow with gentle noise. Drawn stretched ~2x upward, the noise
    # becomes vertical fibres and the glow a body-high sheet of light with no edge anywhere.
    node("tex_column", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("rl", "levels", {"gamma": 0.6}, {"a": "r"}),
        g("n", "fbm", {"frequency": 4.0, "octaves": 3, "seed": 41}),
        g("nl", "levels", {"in_low": 0.3, "in_high": 0.75, "out_low": 0.55}, {"a": "n"}),
        g("m", "math", {"mode": "multiply"}, {"a": "rl", "b": "nl"}),
    ], "output": "m"}})

    # Ribbon cross-width: 0 at both edges, a soft bright centre.
    node("tex_band", "texture", {"width": 8, "height": 64, "graph": {"nodes": [
        g("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        g("gi", "invert", None, {"a": "g"}),
        g("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"}),
        g("l", "levels", {"in_high": 0.5, "gamma": 0.8}, {"a": "t"}),
    ], "output": "l"}})

    # A crystal glint: a thin two-tone flake inside a faint halo. Spinning billboards flash as the facets turn.
    node("tex_glint", "texture", {"width": 48, "height": 48, "graph": {"nodes": [
        g("d", "shape", {"shape": "diamond", "radius": 0.46, "aspect": 0.34, "softness": 0.05}),
        g("split", "gradient_linear", {"angle": 0.0}),
        g("tone", "levels", {"in_low": 0.47, "in_high": 0.53, "out_low": 0.4, "out_high": 1.0}, {"a": "split"}),
        g("m", "math", {"mode": "multiply"}, {"a": "d", "b": "tone"}),
        g("h", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("hl", "levels", {"out_high": 0.22, "gamma": 1.6}, {"a": "h"}),
        g("k", "math", {"mode": "max"}, {"a": "m", "b": "hl"}),
    ], "output": "k"}})

    # The thin arcane ground ring: two hairlines, a soft halo, a broken glyph band and 13 dots.
    parts = [
        g("r0", "ring", {"radius": 0.462, "thickness": 0.011, "softness": 0.009}),
        g("r1", "ring", {"radius": 0.404, "thickness": 0.006, "softness": 0.006}),
        g("halo", "ring", {"radius": 0.44, "thickness": 0.06, "softness": 0.08}),
        g("halol", "levels", {"out_high": 0.26}, {"a": "halo"}),
        g("ck", "cracks", {"density": 15.0, "width": 0.011, "seed": 7}),
        g("band", "ring", {"radius": 0.433, "thickness": 0.026, "softness": 0.012}),
        g("glyph", "math", {"mode": "multiply"}, {"a": "ck", "b": "band"}),
        g("dots", "spokes", {"count": 13, "width": 0.011, "softness": 0.008, "inner_radius": 0.352,
                             "outer_radius": 0.366, "rotation": 9.0}),
        g("r2", "ring", {"radius": 0.29, "thickness": 0.005, "softness": 0.008}),
        g("r2l", "levels", {"out_high": 0.5}, {"a": "r2"}),
    ]
    folds, top = fold("gm", "max", ["r0", "r1", "halol", "glyph", "dots", "r2l"])
    node("tex_ground", "texture", {"width": 256, "height": 256, "graph": {"nodes": parts + folds + [
        g("n", "fbm", {"frequency": 2.6, "octaves": 3, "seed": 5}),
        g("nl", "levels", {"in_low": 0.3, "in_high": 0.72, "out_low": 0.45}, {"a": "n"}),
        g("v", "math", {"mode": "multiply"}, {"a": top, "b": "nl"}),
        g("k", "blur", {"radius": 0.8}, {"a": "v"}),
    ], "output": "k"}})

    # Ground afterglow pool: soft, uneven, bright at the centre.
    node("tex_pool", "texture", {"width": 96, "height": 96, "graph": {"nodes": [
        g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        g("n", "fbm", {"frequency": 2.4, "octaves": 3, "seed": 17}),
        g("nl", "levels", {"in_low": 0.25, "in_high": 0.9, "out_low": 0.55}, {"a": "n"}),
        g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
        g("k", "levels", {"gamma": 1.7}, {"a": "m"}),
    ], "output": "k"}})


# ---------------------------------------------------------------------------
# shared: materials, forces, the ground
# ---------------------------------------------------------------------------


def build_shared() -> None:
    node("mat_glow", "material", {"blend": "additive", "shading": "unlit", "emissive_color": [1.0, 1.0, 1.0, 1.0],
                                  "emissive_intensity": 0.0, "soft_particle": True, "depth_fade": 0.12})
    node("mat_ribbon", "material", {"blend": "additive", "shading": "unlit", "base_color": [1.0, 1.0, 1.0, 1.0],
                                    "emissive_color": [1.0, 1.0, 1.0, 1.0], "emissive_intensity": 0.0,
                                    "soft_particle": True, "depth_fade": 0.1},
         {"base_texture": "tex_band"})
    node("mat_ground", "material", {"blend": "additive", "shading": "unlit", "emissive_color": [1.0, 1.0, 1.0, 1.0],
                                    "emissive_intensity": 0.0, "soft_particle": True})

    node("f_fall", "force", {"force_type": "gravity", "strength": 4.5})
    node("f_rise", "force", {"force_type": "buoyancy", "strength": 0.7})
    node("f_drift", "force", {"force_type": "curl_noise", "strength": 1.4, "frequency": 1.3, "octaves": 2,
                              "speed": 0.6})
    # The implosion pulls the gathered motes into the body centre at the origin (x = z = 0, so only
    # body_height moves it).
    node("f_implode", "force", {"force_type": "attractor", "position": [0.0, RING_Y, 0.0], "strength": 34.0,
                                "radius": 3.0, "falloff": "smooth", **window(0.19, BURST_O + 0.02)})
    node("ground", "collider", {"collider_type": "plane", "normal": [0.0, 1.0, 0.0], "bounce": 0.12,
                                "friction": 0.75})


# ---------------------------------------------------------------------------
# anchors: origin, destination, the runners
# ---------------------------------------------------------------------------


def runner_track(depart: int, arrive: int) -> dict[str, Any]:
    """Origin -> (DISTANCE, 0, 0), one key per frame, y = z = 0 so a multiply only stretches the path."""
    keys: list[tuple[float, list[float]]] = [(0.0, [0.0, 0.0, 0.0])]
    frames = arrive - depart
    for i in range(frames + 1):
        u = i / frames
        keys.append(((depart + i) / FPS, [DISTANCE * (1.0 - (1.0 - u) ** EASE), 0.0, 0.0]))
    keys.append((DURATION, [DISTANCE, 0.0, 0.0]))
    return track(keys)


def build_anchors() -> None:
    hub("origin_body", {"scale": [1.0, 1.0, 1.0]}, "warp")
    hub("destination", {"position": [DISTANCE, 0.0, 0.0]}, "warp")
    hub("destination_body", {"scale": [1.0, 1.0, 1.0]}, "warp", parent="destination")
    for wid, depart, arrive in WAKES:
        hub(wid, {"position": runner_track(depart, arrive)}, "afterimage")
        hub(f"{wid}_body", {"scale": [1.0, 1.0, 1.0]}, "afterimage", parent=wid)
    hub("path_mid", {"position": [DISTANCE * 0.5, 0.0, 0.0]}, "afterimage")


# ---------------------------------------------------------------------------
# pre-cast: the ground ring and gathering motes
# ---------------------------------------------------------------------------


def build_cast() -> None:
    layer = "cast"
    node("ground_ring_o", "decal", {
        "shape": "circle", "blend": "additive", "color": tint(mix(VIOLET, LILAC, 0.35), 1.0), "emissive": 1.4,
        "size": track([(0.0, [1.3, 1.3]), (0.07, [2.25, 2.25]), (BURST_O, [2.4, 2.4]), (0.33, [2.75, 2.75]),
                       (0.62, [2.9, 2.9])]),
        "rotation": track([(0.0, [0.0, 0.0, 0.0]), (0.62, [0.0, 38.0, 0.0])]),
        "opacity": track([(0.0, 0.0), (0.05, 0.85), (0.2, 0.95), (BURST_O, 1.0), (0.34, 0.55), (0.6, 0.0)]),
        "fade_in": 0.02, "fade_out": 0.05, **window(0.0, 0.62),
    }, {"texture": "tex_ground", "material": "mat_ground"}, layer, parent="origin_body")
    node("ground_pool_o", "decal", {
        "shape": "circle", "blend": "additive", "color": tint(INDIGO, 1.0), "emissive": 0.6,
        "size": [3.0, 3.0],
        "opacity": track([(0.0, 0.0), (0.12, 0.3), (BURST_O, 0.35), (0.4, 0.22), (0.7, 0.1), (0.95, 0.0)]),
        "fade_in": 0.05, "fade_out": 0.1, **window(0.0, 0.97),
    }, {"texture": "tex_pool", "material": "mat_ground"}, layer, parent="origin_body")

    node("gather_ps", "particle_system", {
        "max_particles": 220, "lifetime": 0.22, "lifetime_variance": 0.05, "size": 0.036, "size_variance": 0.014,
        "color": tint(LILAC, 1.0), "color_over_life": [[0.0, tint(VIOLET, 1.0)], [0.7, tint(LILAC, 1.0)],
                                                       [1.0, tint(WHITE_HOT, 1.0)]],
        "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.85, 1.0], [1.0, 0.0]], "emissive": 2.2, "drag": 0.6,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.14, "blend": "additive",
    }, {"sprite": "tex_mote", "material": "mat_glow", "forces": ["f_implode"]}, layer)
    # motes drawn in from a shell around the body ...
    node("gather_shell", "emitter", {
        "shape": "sphere", "radius": 1.3, "surface_only": True, "position": [0.0, 0.95, 0.0],
        "direction": [0.0, 0.0, 0.0], "velocity": 0.0, "radial_velocity": -5.2,
        "rate": track([(0.0, 220.0), (0.05, 320.0), (0.17, 300.0), (0.22, 0.0)]), **window(0.0, 0.22),
    }, {"particle": "gather_ps"}, layer, parent="origin_body")
    # ... and rising off the ground ring
    node("gather_ring", "emitter", {
        "shape": "ring", "radius": 1.12, "inner_radius": 1.0, "position": [0.0, 0.03, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 18.0, "velocity": 1.6, "velocity_variance": 0.6,
        "radial_velocity": -1.8, "rate": track([(0.0, 90.0), (0.04, 180.0), (0.18, 120.0), (0.22, 0.0)]),
        **window(0.0, 0.22),
    }, {"particle": "gather_ps"}, layer, parent="origin_body")
    # the implosion: one last inward rush
    node("implode_shell", "emitter", {
        "shape": "sphere", "radius": 1.5, "surface_only": True, "position": [0.0, RING_Y, 0.0],
        "direction": [0.0, 0.0, 0.0], "velocity": 0.0, "radial_velocity": -7.5, "burst_count": 60,
        **burst_window(0.2),
    }, {"particle": "gather_ps"}, layer, parent="origin_body")


# ---------------------------------------------------------------------------
# warp rings (origin and destination share the systems)
# ---------------------------------------------------------------------------

#: radius envelopes (time, multiplier on the ellipse)
ENV_O = [(0.083, 0.02), (0.1, 0.3), (0.125, 0.8), (0.15, 1.0), (0.17, 1.05), (0.2, 1.0), (0.217, 1.03),
         (0.233, 0.72), (0.245, 0.3), (0.255, 0.04), (0.267, 0.02)]
ENV_D = [(0.383, 0.02), (0.4, 0.2), (0.417, 0.74), (0.433, 1.12), (0.467, 1.02), (0.5, 1.0), (0.533, 0.86),
         (0.567, 0.52), (0.583, 0.24), (0.6, 0.02)]


def ring_scale(env: list[tuple[float, float]]) -> dict[str, Any]:
    return track([(t, [RING_RY * e, e, RING_RZ * e]) for t, e in env])


def spin_track(t0: float, t1: float, revs: float, direction: float) -> dict[str, Any]:
    keys = []
    n = max(2, int(round((t1 - t0) * FPS)))
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        keys.append((t, [0.0, direction * 360.0 * revs * (t - t0), 0.0]))
    keys.insert(0, (0.0, [0.0, 0.0, 0.0]))
    return track(keys)


def build_rings() -> None:
    layer = "warp"
    node("ring_hot_ps", "particle_system", {
        "max_particles": 1100, "lifetime": 0.07, "lifetime_variance": 0.012, "size": 0.15, "size_variance": 0.02,
        "color": tint(LILAC, 1.0), "color_over_life": [[0.0, tint(mix(LILAC, AZURE, 0.35), 1.0)],
                                                       [0.45, tint(mix(LILAC, VIOLET, 0.4), 1.0)],
                                                       [1.0, tint(VIOLET, 1.0)]],
        "opacity": 0.46, "opacity_over_life": [[0.0, 0.3], [0.2, 1.0], [1.0, 0.0]], "emissive": 1.1, "drag": 4.0,
        "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer)
    node("ring_soft_ps", "particle_system", {
        "max_particles": 420, "lifetime": 0.09, "lifetime_variance": 0.02, "size": 0.46, "size_variance": 0.06,
        "color": tint(VIOLET, 1.0), "color_over_life": [[0.0, tint(LILAC, 1.0)], [1.0, tint(INDIGO, 1.0)]],
        "opacity": 0.19, "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [1.0, 0.0]], "emissive": 0.9,
        "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer)

    ends = [
        # prefix, body hub, envelope, emission window, hot rate, soft rate, spin (t0, t1, revs, dir)
        ("o", "origin_body", ENV_O, (0.083, 0.258),
         [(0.083, 0.0), (0.1, 2600.0), (0.15, 3800.0), (0.217, 4400.0), (0.25, 3000.0), (0.258, 0.0)],
         [(0.083, 0.0), (0.12, 1100.0), (0.217, 1400.0), (0.25, 800.0), (0.258, 0.0)], (0.083, 0.267, 1.6, 1.0)),
        ("d", "destination_body", ENV_D, (0.383, 0.6),
         [(0.383, 0.0), (0.4, 3200.0), (0.433, 5000.0), (0.5, 3800.0), (0.567, 2400.0), (0.6, 0.0)],
         [(0.383, 0.0), (0.417, 1500.0), (0.467, 1400.0), (0.55, 800.0), (0.6, 0.0)], (0.383, 0.6, 1.3, -1.0)),
    ]
    for p, body, env, (w0, w1), hot_rate, soft_rate, (s0, s1, revs, direction) in ends:
        rig = hub(f"{p}_ring", {"position": [0.0, RING_Y, 0.0], "rotation": [0.0, 0.0, 90.0],
                                "scale": ring_scale(env)}, layer, parent=body)
        node(f"{p}_ring_hot", "emitter", {
            "shape": "ring", "radius": 1.0, "inner_radius": 1.0, "direction": [0.0, 0.0, 0.0], "velocity": 0.04,
            "rate": track(hot_rate), **window(w0, w1),
        }, {"particle": "ring_hot_ps"}, layer, parent=rig)
        node(f"{p}_ring_soft", "emitter", {
            "shape": "ring", "radius": 1.0, "inner_radius": 1.0, "direction": [0.0, 0.0, 0.0], "velocity": 0.12,
            "rate": track(soft_rate), **window(w0, w1),
        }, {"particle": "ring_soft_ps"}, layer, parent=rig)
        spin = hub(f"{p}_ring_spin", {"rotation": spin_track(s0, s1, revs, direction)}, layer, parent=rig)
        for i, x in ((1, 1.0), (2, -1.0)):
            bead = hub(f"{p}_bead_{i}", {"position": [x, 0.0, 0.0]}, layer, parent=spin)
            node(f"{p}_arc_{i}", "trail", {
                "lifetime": 0.1, "max_segments": 24, "min_vertex_distance": 0.02,
                "taper": [[0.0, 0.0], [0.12, 1.0], [0.5, 0.7], [1.0, 0.0]],
                "opacity_over_life": [[0.0, 0.8], [0.6, 0.45], [1.0, 0.0]],
                "color": tint(mix(VIOLET, LILAC, 0.45), 1.0), "blend": "additive",
                "width": track([(w0, 0.0), (w0 + 0.03, 0.22), (w1 - 0.04, 0.22), (w1, 0.0)]),
                "emissive": track([(w0, 0.1), (w0 + 0.04, 0.45), (w1 - 0.03, 0.5), (w1, 0.0)]),
                **window(w0, w1 + 0.1),
            }, {"source": bead, "material": "mat_ribbon"}, layer)


# ---------------------------------------------------------------------------
# bursts: implosion flash at the origin, flare at the destination
# ---------------------------------------------------------------------------


def build_bursts() -> None:
    layer = "burst"
    node("flash_ps", "particle_system", {
        "max_particles": 4, "lifetime": 0.2, "size": 1.5,
        "size_over_life": [[0.0, 0.4], [0.25, 1.0], [1.0, 1.2]],
        "color": tint(mix(VIOLET, LILAC, 0.4), 1.0), "opacity": 0.42,
        "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.45, 0.4], [1.0, 0.0]], "emissive": 0.6,
        "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer)
    node("core_ps", "particle_system", {
        "max_particles": 4, "lifetime": 0.09, "size": 0.26,
        "size_over_life": [[0.0, 0.5], [0.3, 1.0], [1.0, 0.6]],
        "color": tint(mix(WHITE_HOT, LILAC, 0.5), 1.0), "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [1.0, 0.0]],
        "emissive": 0.7,
        "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer)
    node("rays_ps", "particle_system", {
        "max_particles": 160, "lifetime": 0.2, "lifetime_variance": 0.07, "size": 0.07, "size_variance": 0.03,
        "size_over_life": [[0.0, 1.0], [1.0, 0.5]],
        "color": tint(LILAC, 1.0), "color_over_life": [[0.0, tint(LILAC, 1.0)], [0.4, tint(mix(LILAC, VIOLET, 0.5), 1.0)],
                                                       [1.0, tint(VIOLET, 1.0)]],
        "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.55, 0.7], [1.0, 0.0]], "emissive": 1.4, "drag": 6.0,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.6, "blend": "additive",
    }, {"sprite": "tex_mote", "material": "mat_glow"}, layer)
    for p, body, when, rays, speed in (("o", "origin_body", BURST_O, 42, 8.0), ("d", "destination_body", BURST_D, 56, 9.5)):
        centre = [0.0, RING_Y, 0.0]
        node(f"{p}_flash", "emitter", {"shape": "point", "position": centre, "velocity": 0.0, "burst_count": 1,
                                       **burst_window(when)}, {"particle": "flash_ps"}, layer, parent=body)
        node(f"{p}_core", "emitter", {"shape": "point", "position": centre, "velocity": 0.0, "burst_count": 1,
                                      **burst_window(when)}, {"particle": "core_ps"}, layer, parent=body)
        node(f"{p}_rays", "emitter", {
            "shape": "sphere", "radius": 0.3, "surface_only": True, "position": centre, "direction": [0.0, 0.0, 0.0],
            "velocity": speed, "velocity_variance": speed * 0.6, "burst_count": rays, **burst_window(when),
        }, {"particle": "rays_ps"}, layer, parent=body)

    # brief local distortion at the implosion and at the flare
    node("haze", "post_effect", {
        "post_type": "heat_haze", "frequency": 7.0,
        "intensity": track([(0.0, 0.0), (0.167, 0.0), (0.233, 0.32), (0.3, 0.0), (0.383, 0.0), (0.433, 0.38),
                            (0.5, 0.0), (DURATION, 0.0)]),
    }, None, layer)


# ---------------------------------------------------------------------------
# crystal glints
# ---------------------------------------------------------------------------


def build_glints() -> list[str]:
    layer = "shards"
    node("glint_ps", "particle_system", {
        "max_particles": 260, "lifetime": 0.55, "lifetime_variance": 0.2, "size": 0.1, "size_variance": 0.04,
        "size_over_life": [[0.0, 0.3], [0.1, 1.0], [0.25, 0.6], [0.4, 1.0], [0.6, 0.55], [0.8, 0.9], [1.0, 0.0]],
        "rotation_variance": 180.0, "angular_velocity": 480.0, "angular_velocity_variance": 360.0,
        "color": tint(LILAC, 1.15), "color_over_life": [[0.0, tint(WHITE_HOT, 1.0)], [0.35, tint(LILAC, 1.0)],
                                                        [1.0, tint(VIOLET, 1.0)]],
        "opacity_over_life": [[0.0, 0.0], [0.08, 1.0], [0.7, 0.85], [1.0, 0.0]], "emissive": 2.0, "drag": 2.8,
        "render_mode": "billboard", "blend": "additive", "bounce": 0.15, "friction": 0.7,
    }, {"sprite": "tex_glint", "material": "mat_glow", "forces": ["f_fall"], "colliders": ["ground"]}, layer)
    node("shard_ps", "particle_system", {
        "max_particles": 160, "lifetime": 0.6, "lifetime_variance": 0.2, "size": 0.16, "size_variance": 0.06,
        "size_over_life": [[0.0, 0.2], [0.08, 1.0], [0.3, 0.8], [0.5, 1.0], [0.75, 0.75], [1.0, 0.0]],
        "rotation_variance": 180.0, "angular_velocity": 260.0, "angular_velocity_variance": 220.0,
        "color": tint(LILAC, 1.0), "color_over_life": [[0.0, tint(mix(WHITE_HOT, AZURE, 0.3), 1.0)],
                                                       [0.3, tint(LILAC, 1.0)], [1.0, tint(VIOLET, 1.0)]],
        "opacity": 0.9, "opacity_over_life": [[0.0, 0.0], [0.06, 1.0], [0.7, 0.8], [1.0, 0.0]], "emissive": 1.5,
        "drag": 3.2, "render_mode": "billboard", "blend": "additive", "bounce": 0.12, "friction": 0.8,
    }, {"sprite": "tex_glint", "material": "mat_glow", "forces": ["f_fall"], "colliders": ["ground"]}, layer)
    emitters = []
    # shards appear around the forming ring
    for p, rig, w0, w1, rate in (("o", "o_ring", 0.117, 0.25, 55.0), ("d", "d_ring", 0.4, 0.55, 60.0)):
        emitters.append(node(f"{p}_ring_glints", "emitter", {
            "shape": "ring", "radius": 1.1, "inner_radius": 0.85, "direction": [0.0, 0.0, 0.0], "velocity": 0.7,
            "velocity_variance": 0.4, "rate": track([(w0, 0.0), (w0 + 0.03, rate), (w1 - 0.02, rate), (w1, 0.0)]),
            **window(w0, w1),
        }, {"particle": "shard_ps"}, layer, parent=rig))
    for p, body, when, count, speed in (("o", "origin_body", BURST_O, 28, 4.5), ("d", "destination_body", BURST_D, 34, 5.0)):
        emitters.append(node(f"{p}_glints", "emitter", {
            "shape": "sphere", "radius": 0.4, "position": [0.0, RING_Y, 0.0], "direction": [0.0, 0.0, 0.0],
            "velocity": speed, "velocity_variance": speed * 0.5, "burst_count": count, **burst_window(when),
        }, {"particle": "shard_ps"}, layer, parent=body))
    # shards flung along the path by each wake
    for wid, depart, arrive in WAKES:
        emitters.append(node(f"{wid}_glints", "emitter", {
            "shape": "box", "size": [1.2, 1.5, 0.4], "position": [-0.6, 0.9, 0.0], "direction": [0.0, 1.0, 0.0],
            "spread": 70.0, "velocity": 1.2, "velocity_variance": 0.8, "inherit_velocity": 0.12,
            "rate": 110.0, **window((depart + 1) / FPS, arrive / FPS + 0.001),
        }, {"particle": "glint_ps"}, layer, parent=f"{wid}_body"))
    return emitters


# ---------------------------------------------------------------------------
# the afterimage: body-sized wakes and light streaks along the path
# ---------------------------------------------------------------------------


def build_afterimage() -> tuple[list[str], list[str]]:
    """Three wakes of body-sized light. Returns (emitters the Afterimage control scales, line emitters
    whose length follows blink_distance)."""
    layer = "afterimage"
    # One tall soft sheet of light a frame at each runner: the bright front of the wake, and because it
    # stays where it was laid and fades in ~4 frames, fading copies of it trail the runner - afterimages
    # made of light, never a silhouette.
    node("ghost_column_ps", "particle_system", {
        "max_particles": 80, "lifetime": 0.055, "lifetime_variance": 0.015, "size": 0.78, "size_variance": 0.12,
        "size_over_life": [[0.0, 0.92], [0.3, 1.0], [1.0, 0.8]],
        "color": tint(mix(VIOLET, LILAC, 0.45), 1.0), "color_over_life": [[0.0, tint(LILAC, 1.0)],
                                                                           [0.5, tint(VIOLET, 1.0)],
                                                                           [1.0, tint(INDIGO, 1.0)]],
        "opacity": 0.34, "opacity_over_life": [[0.0, 0.6], [0.1, 1.0], [0.4, 0.45], [1.0, 0.0]], "emissive": 1.0,
        "drag": 1.0, "render_mode": "stretched_billboard", "velocity_stretch": 3.6, "blend": "additive",
    }, {"sprite": "tex_column", "material": "mat_glow"}, layer)
    # The afterimage copies: the same sheet of light, dimmer and longer lived, fibres slowly rising.
    node("ghost_stamp_ps", "particle_system", {
        "max_particles": 16, "lifetime": 0.3, "lifetime_variance": 0.04, "size": 0.72, "size_variance": 0.08,
        "size_over_life": [[0.0, 0.85], [0.25, 1.0], [1.0, 1.12]],
        "color": tint(mix(VIOLET, LILAC, 0.35), 1.0), "color_over_life": [[0.0, tint(LILAC, 1.0)],
                                                                           [0.4, tint(VIOLET, 1.0)],
                                                                           [1.0, tint(INDIGO, 1.0)]],
        "opacity": 0.36, "opacity_over_life": [[0.0, 0.0], [0.08, 1.0], [0.35, 0.6], [1.0, 0.0]], "emissive": 0.9,
        "drag": 3.0, "render_mode": "stretched_billboard", "velocity_stretch": 4.2, "blend": "additive",
    }, {"sprite": "tex_column", "material": "mat_glow"}, layer)
    # The smear: body-high stacks of soft light stretched along the path by a third of the runner's speed.
    node("ghost_smear_ps", "particle_system", {
        "max_particles": 220, "lifetime": 0.1, "lifetime_variance": 0.02, "size": 0.62, "size_variance": 0.12,
        "color": tint(mix(VIOLET, LILAC, 0.2), 1.0), "color_over_life": [[0.0, tint(mix(VIOLET, LILAC, 0.35), 1.0)],
                                                                          [1.0, tint(INDIGO, 1.0)]],
        "opacity": 0.24, "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.5, 0.5], [1.0, 0.0]], "emissive": 0.8,
        "drag": 8.0, "render_mode": "stretched_billboard", "velocity_stretch": 0.2, "blend": "additive",
    }, {"sprite": "tex_haze", "material": "mat_glow"}, layer)
    # Horizontal speed lines filling the column; they inherit a third of the runner's speed and brake hard.
    node("ghost_lines_ps", "particle_system", {
        "max_particles": 700, "lifetime": 0.15, "lifetime_variance": 0.04, "size": 0.05, "size_variance": 0.018,
        "color": tint(LILAC, 1.0), "color_over_life": [[0.0, tint(mix(LILAC, AZURE, 0.4), 1.0)],
                                                       [0.5, tint(mix(LILAC, VIOLET, 0.5), 1.0)],
                                                       [1.0, tint(VIOLET, 1.0)]],
        "opacity": 0.85, "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.5, 0.55], [1.0, 0.0]], "emissive": 1.5,
        "drag": 10.0, "render_mode": "stretched_billboard", "velocity_stretch": 0.36, "blend": "additive",
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer)
    # Fine vertical wisps rising and sinking in place.
    node("ghost_wisp_ps", "particle_system", {
        "max_particles": 260, "lifetime": 0.13, "lifetime_variance": 0.04, "size": 0.1, "size_variance": 0.035,
        "size_over_life": [[0.0, 0.6], [0.2, 1.0], [1.0, 0.7]],
        "color": tint(mix(VIOLET, LILAC, 0.3), 1.0), "color_over_life": [[0.0, tint(LILAC, 1.0)],
                                                                          [1.0, tint(INDIGO, 1.0)]],
        "opacity": 0.55, "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.35, 0.55], [1.0, 0.0]], "emissive": 1.2,
        "drag": 2.0, "render_mode": "stretched_billboard", "velocity_stretch": 2.6, "blend": "additive",
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer)

    amount: list[str] = []
    lines: list[str] = []
    seg = 2.4          # about twice the path a runner covers in its fastest frame, so frames overlap
    for k, (wid, depart, arrive) in enumerate(WAKES):
        body = f"{wid}_body"
        w = window((depart + 1) / FPS, arrive / FPS + 0.001)
        # The path just behind the runner (the last two frames of travel): a hub half a segment back (x
        # only, so blink_distance can scale it) carrying LINE emitters whose length is that segment.
        # Particles are spawned where the runner has just been, never stamped at one point per frame, and
        # consecutive frames overlap so no frame-sized blocks show.
        tail = hub(f"{wid}_tail", {"position": [-seg / 2, 0.0, 0.0]}, layer, parent=body)
        lines.append(node(f"{wid}_column", "emitter", {
            "shape": "line", "length": seg, "position": [0.0, 0.92, 0.0], "direction": [0.0, 1.0, 0.0],
            "velocity": 0.33, "velocity_variance": 0.05, "rate": 240.0, **w,
        }, {"particle": "ghost_column_ps"}, layer, parent=tail))
        amount.append(lines[-1])
        # one afterimage copy per wake, left at its own fraction of the path (the runner's position at
        # that frame, so it scales with blink_distance): lingers ~0.3 s, drifts after the runner, fades
        stamp = STAMPS[k]
        amount.append(node(f"{wid}_afterimage", "emitter", {
            "shape": "line", "length": 0.3, "position": [0.0, 0.92, 0.0], "direction": [0.0, 1.0, 0.0],
            "spread": 4.0, "velocity": 0.3, "velocity_variance": 0.06,
            "burst_count": 4, **burst_window(stamp / FPS),
        }, {"particle": "ghost_stamp_ps"}, layer, parent=body))
        for j, y in enumerate((0.2, 0.46, 0.72, 0.97, 1.22, 1.46, 1.68)):
            lines.append(node(f"{wid}_smear_{j + 1}", "emitter", {
                "shape": "line", "length": seg, "position": [0.0, r4(y + 0.05 * ((j + k) % 3 - 1)), 0.0],
                "direction": [1.0, 0.0, 0.0], "spread": 2.0, "velocity": 0.0, "inherit_velocity": 0.35,
                "rate": 30.0, **w,
            }, {"particle": "ghost_smear_ps"}, layer, parent=tail))
            amount.append(lines[-1])
        for j, (y, z) in enumerate(((0.22, 0.1), (0.55, -0.14), (0.9, 0.05), (1.24, -0.08), (1.58, 0.12))):
            lines.append(node(f"{wid}_lines_{j + 1}", "emitter", {
                "shape": "line", "length": seg, "position": [0.0, y, z], "direction": [1.0, 0.0, 0.0],
                "spread": 3.0, "velocity": 2.0, "velocity_variance": 1.5, "inherit_velocity": 0.3,
                "rate": 120.0, **w,
            }, {"particle": "ghost_lines_ps"}, layer, parent=tail))
            amount.append(lines[-1])
        for suffix, direction in (("up", [0.0, 1.0, 0.0]), ("down", [0.0, -1.0, 0.0])):
            amount.append(node(f"{wid}_wisps_{suffix}", "emitter", {
                "shape": "box", "size": [seg, 1.5, 0.3], "position": [0.0, 0.92, 0.0], "direction": direction,
                "spread": 10.0, "velocity": 0.9, "velocity_variance": 0.5, "rate": 90.0, **w,
            }, {"particle": "ghost_wisp_ps"}, layer, parent=tail))
    # After the wakes: a faint residue of motes along the whole path. `path_mid` is the midpoint hub and
    # the line's length is bound to blink_distance, so it always spans origin -> destination.
    lines.append(node("path_residue", "emitter", {
        "shape": "line", "length": DISTANCE, "position": [0.0, 0.8, 0.0], "direction": [0.0, 1.0, 0.0],
        "spread": 60.0, "velocity": 0.4, "velocity_variance": 0.3,
        "rate": track([(0.3, 0.0), (0.35, 120.0), (0.45, 80.0), (0.6, 0.0)]), **window(0.3, 0.6),
    }, {"particle": "mote_ps"}, layer, parent="path_mid"))
    return amount, lines


# ---------------------------------------------------------------------------
# settle and fade: motes, the destination ground ring, afterglow
# ---------------------------------------------------------------------------


def build_aftermath() -> None:
    layer = "aftermath"
    node("ground_ring_d", "decal", {
        "shape": "circle", "blend": "additive", "color": tint(mix(VIOLET, LILAC, 0.35), 1.0), "emissive": 1.4,
        "size": track([(0.383, [1.2, 1.2]), (0.44, [2.4, 2.4]), (0.6, [2.7, 2.7]), (0.88, [2.85, 2.85])]),
        "rotation": track([(0.383, [0.0, 0.0, 0.0]), (0.88, [0.0, -34.0, 0.0])]),
        "opacity": track([(0.383, 0.0), (0.43, 1.0), (0.6, 0.72), (0.86, 0.0)]),
        "fade_in": 0.02, "fade_out": 0.05, **window(0.383, 0.88),
    }, {"texture": "tex_ground", "material": "mat_ground"}, layer, parent="destination_body")
    node("ground_pool_d", "decal", {
        "shape": "circle", "blend": "additive", "color": tint(INDIGO, 1.0), "emissive": 0.6, "size": [3.0, 3.0],
        "opacity": track([(0.383, 0.0), (0.42, 0.28), (0.6, 0.18), (0.8, 0.09), (0.98, 0.0)]),
        "fade_in": 0.03, "fade_out": 0.1, **window(0.383, 0.99),
    }, {"texture": "tex_pool", "material": "mat_ground"}, layer, parent="destination_body")

    node("mote_ps", "particle_system", {
        "max_particles": 160, "lifetime": 0.42, "lifetime_variance": 0.14, "size": 0.05, "size_variance": 0.02,
        "size_over_life": [[0.0, 0.2], [0.15, 1.0], [0.35, 0.55], [0.55, 1.0], [0.8, 0.6], [1.0, 0.0]],
        "color": tint(LILAC, 1.0), "color_over_life": [[0.0, tint(WHITE_HOT, 1.0)], [0.4, tint(LILAC, 1.0)],
                                                       [1.0, tint(VIOLET, 1.0)]],
        "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.75, 0.8], [1.0, 0.0]], "emissive": 2.0, "drag": 1.4,
        "render_mode": "billboard", "blend": "additive",
    }, {"sprite": "tex_mote", "material": "mat_glow", "forces": ["f_rise", "f_drift"]}, layer)
    for p, body, keys, last in (
        ("o", "origin_body", [(0.25, 0.0), (0.3, 70.0), (0.55, 36.0), (0.78, 0.0)], 0.7),
        ("d", "destination_body", [(0.42, 0.0), (0.5, 95.0), (0.68, 40.0), (0.82, 0.0)], 0.717),
    ):
        node(f"{p}_linger", "emitter", {
            "shape": "sphere", "radius": 0.75, "position": [0.0, 0.75, 0.0],
            "direction": [0.0, 0.0, 0.0], "velocity": 0.35, "velocity_variance": 0.2,
            "rate": track(keys), **window(keys[0][0], keys[-1][0]),
        }, {"particle": "mote_ps"}, layer, parent=body)
        node(f"{p}_last_motes", "emitter", {
            "shape": "ring", "radius": 1.05, "inner_radius": 0.8, "position": [0.0, 0.05, 0.0],
            "direction": [0.0, 1.0, 0.0], "spread": 20.0, "velocity": 0.8, "velocity_variance": 0.4,
            "burst_count": 10, **burst_window(last),
        }, {"particle": "mote_ps"}, layer, parent=body)


def build_lights() -> None:
    layer = "light"
    node("light_o", "light", {
        "light_type": "point", "position": [0.4, 1.9, 0.9], "color": tint(mix(VIOLET, LILAC, 0.3), 1.0),
        "radius": 5.0,
        "intensity": track([(0.0, 0.0), (0.08, 1.0), (0.2, 2.2), (BURST_O, 3.0), (0.33, 2.0), (0.5, 0.6),
                            (0.8, 0.0)]),
    }, None, layer, parent="origin_body")
    node("light_d", "light", {
        "light_type": "point", "position": [-0.4, 1.9, 0.9], "color": tint(mix(VIOLET, LILAC, 0.3), 1.0),
        "radius": 5.0,
        "intensity": track([(0.0, 0.0), (0.383, 0.0), (BURST_D + 0.017, 3.0), (0.48, 1.4), (0.6, 1.5),
                            (0.9, 0.0)]),
    }, None, layer, parent="destination_body")
    node("light_wake", "light", {
        "light_type": "point", "position": [0.0, 1.0, 0.5], "color": tint(mix(VIOLET, AZURE, 0.3), 1.0),
        "radius": 3.0,
        "intensity": track([(0.0, 0.0), (18 / FPS, 0.0), (19 / FPS, 2.0), (25 / FPS, 2.0), (26 / FPS, 0.0)]),
    }, None, layer, parent="wake_2_body")


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

COLOUR_PARAMETERS = ("color", "color_over_life", "emissive_color", "base_color")


def build_controls(glint_emitters: list[str], wake_emitters: list[str], path_lines: list[str]) -> list[dict[str, Any]]:
    def multiplier(cid, label, group, bindings, lo=0.0, hi=3.0, step=0.01):
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": 1.0, "value": 1.0,
                "step": step, "unit": "x", "bindings": bindings}

    def bind(ids, parameter, op="multiply"):
        return [{"node": nid, "parameter": parameter, "op": op} for nid in ids]

    def of_type(t):
        return [n["id"] for n in nodes if n["type"] == t]

    def authored(nid, parameter):
        return next(n for n in nodes if n["id"] == nid).get("parameters", {}).get(parameter)

    runners = [w[0] for w in WAKES]
    bodies = ["origin_body", "destination_body"] + [f"{w}_body" for w in runners]
    decals = of_type("decal")
    systems = of_type("particle_system")
    glint_bursts = [e for e in glint_emitters if authored(e, "burst_count")]
    glint_rates = [e for e in glint_emitters if authored(e, "rate")]

    hue = []
    for n in nodes:
        for parameter in COLOUR_PARAMETERS:
            if parameter in n.get("parameters", {}):
                hue.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    return [
        # the destination hub and every runner track: the wakes always land on the destination
        multiplier("blink_distance", "Blink distance", "Global",
                   bind(["destination"] + runners + ["path_mid"] + [f"{w}_tail" for w in runners], "position")
                   + bind(path_lines, "length"), lo=0.25, hi=2.0),
        multiplier("body_height", "Body height", "Global",
                   bind(bodies, "scale") + bind(decals, "size") + bind(["f_implode"], "position"), lo=0.5, hi=1.6),
        multiplier("intensity", "Glow intensity", "Global",
                   bind(systems, "color") + bind(of_type("trail") + decals, "color")
                   + bind(of_type("light"), "intensity"), hi=2.5),
        multiplier("afterimage", "Afterimage strength", "Afterimage",
                   bind(wake_emitters, "rate"), hi=2.0),
        multiplier("shard_amount", "Shard amount", "Crystal shards",
                   bind(glint_bursts, "burst_count") + bind(glint_rates, "rate"), hi=2.5, step=0.05),
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
    build_anchors()
    build_cast()
    build_rings()
    build_bursts()
    glint_emitters = build_glints()
    build_aftermath()
    wake_emitters, path_lines = build_afterimage()
    build_lights()
    # Three-quarter view from in front of the destination: travel runs left to right towards the camera,
    # both anchors 8 m apart, and the warp rings (standing across the travel axis) read as vertical
    # ellipses. The studio frames ~1.45x wider.
    node("cam", "camera", {"position": [11.6, 3.6, 7.3], "target": [4.5, 0.85, 0.0], "fov": 38.0})
    controls = build_controls(glint_emitters, wake_emitters, path_lines)

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": DESCRIPTION,
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [{"name": n, "start": s, "end": e} for n, s, e in PHASES]},
        "layers": [
            {"id": "cast", "name": "Cast ring", "role": "telegraph"},
            {"id": "warp", "name": "Warp rings", "role": "primary"},
            {"id": "burst", "name": "Vanish and flare", "role": "ignition"},
            {"id": "afterimage", "name": "Afterimage", "role": "secondary"},
            {"id": "shards", "name": "Crystal shards", "role": "secondary"},
            {"id": "light", "name": "Light", "role": "interaction"},
            {"id": "aftermath", "name": "Settle and fade", "role": "aftermath"},
        ],
        "nodes": list(nodes),
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/blink.py",
            "prompt": "lets create an effect for \"blink\" this will be transferrable to characters",
            "analysis": "pre_cast 0-0.1 a thin arcane ring ignites under the caster, motes gather | distortion "
                        "0.1-0.2 a vertical elliptical warp ring opens around the body facing the travel direction, "
                        "space ripples, shards appear | disappear 0.2-0.3 the ring implodes, a small flash, a burst "
                        "of crystal glints and light streaks | transit 0.3-0.4 three body-sized afterimage wakes "
                        "and tapered light trails race to the destination, shards flung along the path | reappear "
                        "0.4-0.5 the destination ring flares open with a small bright core and a many-pointed "
                        "burst | settle 0.5-0.6 the ring contracts, shards fall and settle, the ground ring glows "
                        "| fade 0.6-1.0 lingering motes and a faint afterglow at both ends",
            "layout": {
                "origin": [0.0, 0.0, 0.0], "destination": [DISTANCE, 0.0, 0.0], "ground": 0.0,
                "note": "Two anchors and no character: `origin` is the caster's root at cast time, `destination` "
                        "(the node of the same name) the root on arrival.",
            },
            "attachment": {
                "origin": [0.0, 0.0, 0.0], "destination_node": "destination", "default_distance": DISTANCE,
                "travel_axis": "+x", "body_height": BODY,
                "notes": "Place the effect at the caster's root with +X facing the destination, set blink_distance "
                         "to distance / 8 and body_height to height / 1.8. Afterimages of the real character mesh "
                         "can be layered on top in engine.",
            },
            "render_settings": {
                "background": [0.0, 0.0, 0.0, 1.0],
                "ground_albedo": 0.05,
                "bloom_intensity": 0.22,
                "bloom_radius": 0.035,
                "exposure": 0.95,
                "grid": False,
            },
            "tags": ["blink", "teleport", "mobility", "arcane", "dash", "afterimage"],
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
