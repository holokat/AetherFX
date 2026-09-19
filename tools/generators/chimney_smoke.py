#!/usr/bin/env python3
"""Generate the Chimney Smoke effect: always-on, wind-aware wood smoke rising from a chimney opening.

    python tools/generators/chimney_smoke.py --out examples/effects
    python tools/generators/chimney_smoke.py --check examples/effects

A game attaches the `chimney` node at the centre of the chimney opening (y = 0 there); the effect draws no
chimney, roof or house. The plume rises about 5 m before it has thinned into haze.

* WISPS    EMIT / RISE: a thin, dense, fast-churning wisp right at the opening, lit warm from the hearth below.
* PLUME    BILLOW / DRIFT / DISPERSE: overlapping alpha-blended lit puffs that leave the opening at about
           1.5 m/s, slow down as they cool, grow from 0.3 m to about 2.3 m, roll slowly, curl on two scales of
           curl noise, lean with the wind and thin out (erosion widens with age) into soft grey haze.
* WIND     particles keep the horizontal speed they are launched with, so the wind is the lean of the launch
           direction: the emitters sit under two invisible rig nodes. The inner rig tilts them by the wind
           strength (about 20 degrees at the default, breathing in gusts on 12, 4 and 1.7 s harmonics with a
           lateral sway on 6 and 2.4 s), the outer rig turns the whole thing to the wind direction. A plume
           that is launched leaning and decelerates as it rises bends over higher up, like smoke in wind.
* LIGHT    a warm low key light from the side, a cool sky fill from above and behind, and a small warm hearth
           glow just under the opening that lights the underside of the first puffs.

Always on and seamless: the wind, the gusts and the puff rate are periodic over the 12 s loop (every track
starts and ends on the same value), and the plume is pre-filled on the first frame by bursts of particles
that are already "in flight" - one band per age range, placed where a puff of that age would be, with the
remaining part of the over-life curves - so the loop closes without the column restarting.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

NAME = "Chimney Smoke"
SLUG = "chimney_smoke"
DURATION = 12.0                            # the library's longest allowed loop
SEED = 40917

# --- plume physics (see the module docstring) -------------------------------------------------------------
LAUNCH = 1.6                               # m/s out of the opening
COOL = 0.03                                # m/s^2 of deceleration as the smoke cools
DRAG = 0.25
WIND_TILT = 22.0                           # launch lean at the default wind strength, degrees
PLUME_LIFE = 5.0
WISP_LIFE = 1.5
PLUME_RATE = 12.0
WISP_RATE = 9.0
PLUME_SCALE = 1.0                          # metres, times PLUME_SIZE over life
PLUME_ALPHA = 0.34

SMOKE_GREY = [0.74, 0.74, 0.76, 1.0]
KEY = [1.0, 0.84, 0.68, 1.0]               # low warm dusk sun
SKY = [0.42, 0.52, 0.78, 1.0]              # cool fill from the sky
HEARTH = [1.0, 0.46, 0.16, 1.0]

# over-life curves of the plume (normalised age), also resampled for the pre-fill bands
PLUME_SIZE = [[0.0, 0.3], [0.2, 0.7], [0.5, 1.3], [1.0, 2.6]]
PLUME_OPACITY = [[0.0, 0.0], [0.06, 0.85], [0.16, 1.0], [0.35, 0.62], [0.55, 0.3], [0.75, 0.1], [1.0, 0.0]]
PLUME_TINT = [[0.0, [0.78, 0.76, 0.74, 1.0]], [0.3, [1.0, 1.0, 1.0, 1.0]], [1.0, [1.04, 1.06, 1.1, 1.0]]]
WISP_SIZE = [[0.0, 0.5], [1.0, 2.1]]
WISP_OPACITY = [[0.0, 0.0], [0.08, 1.0], [0.55, 0.75], [1.0, 0.0]]
WISP_TINT = [[0.0, [0.86, 0.8, 0.74, 1.0]], [1.0, [0.95, 0.94, 0.94, 1.0]]]
PLUME_DISSOLVE = 0.0
WISP_DISSOLVE = 0.0
WISP_STRETCH = 0.4
EROSION = 0.0

PLUME_BANDS = [(0.0, 0.3), (0.3, 1.4), (1.4, 2.6), (2.6, 3.8), (3.8, 4.9)]   # ages pre-filled on the first frame
WISP_BANDS = [(0.0, 1.4)]
# The pre-fill: for each band above, the running plume measured on the loop's last frame (t = 12 s) in the wind
# rig's frame at t = 0 (out/work/chimney_smoke/measure_prefill.py; re-measure after changing the physics):
# (count, direction, speed, speed variance, spread deg, remaining life, its variance, positions ordered by age)
PREFILL_PLUME = [
    (5, [0.003, 1.0, 0.017], 1.595, 0.061, 5.6, 4.788, 0.306,
     [[0.01, 0.02, 0.01], [0.11, 0.11, -0.1], [0.06, 0.23, -0.17], [-0.02, 0.32, 0.07], [0.05, 0.45, 0.1]]),
    (13, [-0.069, 0.997, -0.045], 1.529, 0.123, 7.2, 4.121, 0.701,
     [[-0.04, 0.51, -0.15], [-0.11, 0.72, 0.11], [-0.12, 0.73, 0.02], [-0.04, 0.98, 0.13], [-0.1, 1.11, -0.07], [-0.04, 1.15, -0.28], [-0.13, 1.24, -0.19], [-0.35, 1.34, -0.12], [-0.12, 1.41, -0.26], [0.16, 1.54, -0.41], [-0.04, 1.8, -0.17], [0.17, 1.81, -0.56], [-0.53, 2.16, -0.22]]),
    (11, [-0.395, 0.899, -0.189], 1.329, 0.251, 12.7, 2.924, 0.699,
     [[-0.09, 2.14, -0.64], [-0.63, 2.7, -0.37], [0.1, 2.4, -0.68], [-0.28, 2.38, -0.74], [-0.69, 2.55, -0.52], [-1.01, 3.09, -0.49], [-0.31, 2.79, -0.65], [-1.02, 3.28, -0.54], [-0.75, 4.02, -0.56], [-0.56, 2.93, -1.3], [-0.86, 3.15, -0.59]]),
    (13, [-0.171, 0.959, -0.226], 0.953, 0.235, 17.5, 1.765, 0.614,
     [[-0.07, 2.89, -0.63], [0.04, 3.05, -1.48], [-0.39, 4.09, -0.65], [-0.08, 3.25, -0.7], [-0.46, 3.82, -0.87], [0.02, 2.89, -0.59], [-0.1, 3.3, -1.74], [0.33, 3.0, -1.7], [0.28, 4.13, -0.63], [-0.08, 3.98, -1.2], [-0.19, 4.31, -1.16], [-0.13, 3.92, -1.17], [0.63, 4.12, -2.2]]),
    (15, [0.165, 0.965, -0.206], 0.973, 0.237, 24.1, 0.608, 0.57,
     [[0.98, 5.02, -1.96], [0.46, 4.48, -1.85], [-0.1, 3.6, -1.72], [-0.13, 4.28, -1.96], [-0.06, 4.63, -1.33], [0.07, 3.73, -1.0], [0.14, 5.8, -1.76], [-0.02, 3.04, -1.88], [0.31, 5.55, -1.13], [0.34, 5.74, -1.76], [0.18, 4.44, -1.73], [-0.31, 4.23, -2.11], [-0.1, 5.67, -1.11], [-0.51, 5.44, -0.8], [-2.18, 6.28, -0.71]]),
]
PREFILL_WISP = [
    (12, [-0.067, 0.998, -0.02], 1.673, 0.201, 4.4, 0.893, 0.661,
     [[-0.08, 0.03, -0.06], [0.09, 0.16, 0.01], [0.07, 0.33, 0.08], [-0.09, 0.45, -0.11], [0.06, 0.71, -0.01], [-0.05, 0.89, -0.06], [0.07, 1.19, -0.18], [-0.19, 1.47, -0.17], [-0.2, 1.45, -0.16], [-0.09, 1.62, -0.34], [-0.08, 2.0, -0.32], [-0.23, 1.69, -0.3]]),
]


def r4(x: float) -> float:
    return round(x, 4)


def node(nid: str, ntype: str, params: dict[str, Any], inputs: dict[str, Any] | None = None,
         parent: str | None = None, layer: str | None = "smoke") -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        entry["layer"] = layer
    if parent:
        entry["parent"] = parent
    entry["parameters"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def texture(nid: str, width: int, height: int, ops: list[dict[str, Any]], frames: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"width": width, "height": height}
    if frames:
        params["frames"] = frames
    params["graph"] = {"nodes": ops, "output": ops[-1]["id"]}
    return node(nid, "texture", params, layer=None)


def periodic(terms: list[tuple[float, int, float]], base: float, step: float = 0.5) -> list[dict[str, float]]:
    """A keyframe track base * (1 + sum a*sin(2 pi k t / T + p)), exactly periodic over the loop."""
    keys = []
    n = int(round(DURATION / step))
    for i in range(n + 1):
        t = i * step
        v = 1.0 + sum(a * math.sin(2.0 * math.pi * k * t / DURATION + p) for a, k, p in terms)
        keys.append({"time": r4(t), "value": r4(base * v)})
    return keys


def sway(terms: list[tuple[float, int, float]], step: float = 0.5) -> list[float]:
    n = int(round(DURATION / step))
    return [sum(a * math.sin(2.0 * math.pi * k * (i * step) / DURATION + p) for a, k, p in terms) for i in range(n + 1)]


def sample(curve: list[list[Any]], x: float) -> Any:
    """Piecewise-linear lookup of a curve or gradient at x."""
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, v0), (x1, v1) in zip(curve, curve[1:]):
        if x <= x1:
            f = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
            if isinstance(v0, list):
                return [r4(a + (b - a) * f) for a, b in zip(v0, v1)]
            return r4(v0 + (v1 - v0) * f)
    return curve[-1][1]


def tail(curve: list[list[Any]], start: float, samples: int = 7) -> list[list[Any]]:
    """The part of an over-life curve after normalised age `start`, re-spread over 0..1."""
    return [[r4(i / (samples - 1)), sample(curve, start + (1.0 - start) * i / (samples - 1))] for i in range(samples)]


def build() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        node("cam", "camera", {"position": [2.6, 1.3, 12.4], "target": [1.2, 3.1, 0.0], "fov": 35.0},
             layer=None),
        # the attachment point (centre of the chimney opening) and the two wind rigs under it
        node("chimney", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False}),
        node("wind_heading", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                      "rotation": [0.0, 1.0, 0.0]}, parent="chimney"),
    ]
    tilt = periodic([(0.15, 1, 0.4), (0.07, 3, 1.9), (0.03, 7, 0.3)], WIND_TILT)
    lateral = sway([(7.0, 2, 1.1), (4.0, 5, 2.5)])
    gust_track = [{"time": k["time"], "value": [r4(lat), 0.0, r4(-k["value"])]} for k, lat in zip(tilt, lateral)]
    nodes.append(node("wind_gust", "mesh", {
        "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
        "rotation": {"value": gust_track[0]["value"], "track": gust_track},
    }, parent="wind_heading"))

    nodes += [
        # one flipbook for every puff: an irregular, warped silhouette with billowing fbm inside, 16 frames
        # mapped over each particle's life so a puff churns as it grows
        texture("tex_puff", 128, 128, [
            {"id": "w1", "op": "fbm", "params": {"frequency": 1.8, "octaves": 2, "seed": 11, "animate": 0.5}},
            {"id": "w2", "op": "fbm", "params": {"frequency": 1.8, "octaves": 2, "seed": 29, "animate": 0.5}},
            {"id": "w1l", "op": "levels", "params": {"in_low": 0.25, "in_high": 0.75}, "inputs": {"a": "w1"}},
            {"id": "w2l", "op": "levels", "params": {"in_low": 0.25, "in_high": 0.75}, "inputs": {"a": "w2"}},
            {"id": "wp", "op": "channel_pack", "params": {"channel": "luminance"}, "inputs": {"r": "w1l", "g": "w2l"}},
            # the silhouette: a soft disc warped into an irregular lobe, kept inside the quad
            {"id": "rd", "op": "gradient_radial", "params": {"radius": 0.47, "falloff": "smooth"}},
            {"id": "shape", "op": "distort", "params": {"amount": 0.09}, "inputs": {"a": "rd", "by": "wp"}},
            # the inside: billowing fbm, domain-warped by the same field so it folds and curls
            {"id": "n", "op": "fbm", "params": {"frequency": 2.1, "octaves": 5, "gain": 0.55, "seed": 3, "animate": 0.7}},
            {"id": "nw", "op": "distort", "params": {"amount": 0.07}, "inputs": {"a": "n", "by": "wp"}},
            {"id": "nl", "op": "levels", "params": {"in_low": 0.2, "in_high": 0.8, "out_low": 0.3}, "inputs": {"a": "nw"}},
            # rounded billow lobes with soft creases between them (inverted worley cells, gently warped)
            {"id": "cells", "op": "worley", "params": {"frequency": 3.2, "octaves": 2, "seed": 8, "animate": 0.4}},
            {"id": "lobes", "op": "invert", "inputs": {"a": "cells"}},
            {"id": "lobes_l", "op": "levels", "params": {"in_low": 0.3, "in_high": 1.0}, "inputs": {"a": "lobes"}},
            {"id": "lobes_w", "op": "distort", "params": {"amount": 0.05}, "inputs": {"a": "lobes_l", "by": "wp"}},
            {"id": "detail", "op": "math", "params": {"mode": "lerp", "factor": 0.45}, "inputs": {"a": "nl", "b": "lobes_w"}},
            {"id": "m", "op": "math", "params": {"mode": "multiply"}, "inputs": {"a": "shape", "b": "detail"}},
            {"id": "alpha", "op": "levels", "params": {"in_low": 0.1, "in_high": 0.75, "gamma": 1.15}, "inputs": {"a": "m"}},
            {"id": "shade", "op": "levels", "params": {"in_low": 0.0, "in_high": 0.6, "out_low": 0.72}, "inputs": {"a": "m"}},
            {"id": "out", "op": "channel_pack", "params": {"channel": "luminance"},
             "inputs": {"r": "shade", "g": "shade", "b": "shade", "a": "alpha"}},
        ], frames=16),
        texture("tex_erode", 128, 128, [
            {"id": "a", "op": "fbm", "params": {"frequency": 3.0, "octaves": 4, "gain": 0.5, "seed": 4407, "tile": True}},
            {"id": "b", "op": "blur", "params": {"radius": 2.0}, "inputs": {"a": "a"}},
            {"id": "l", "op": "levels", "params": {"in_low": 0.22, "in_high": 0.78}, "inputs": {"a": "b"}},
        ]),
        # forces: cooling slows the rise, curl noise on two scales rolls and curls the plume
        node("f_cool", "force", {"force_type": "directional", "direction": [0.0, -1.0, 0.0], "strength": COOL},
             layer=None),
        node("f_billow", "force", {"force_type": "curl_noise", "strength": 0.18, "frequency": 0.2, "octaves": 2,
                                   "speed": 0.22}, layer=None),
        node("f_curl", "force", {"force_type": "curl_noise", "strength": 0.2, "frequency": 0.9, "octaves": 2,
                                 "speed": 0.45}, layer=None),
    ]

    def material(mid: str, dissolve: float) -> dict[str, Any]:
        return node(mid, "material", {
            "blend": "alpha", "shading": "lit", "base_color": [1.0, 1.0, 1.0, 1.0],
            "emissive_color": [0.62, 0.66, 0.78, 1.0], "soft_particle": True, "depth_fade": 0.3,
            "dissolve": r4(dissolve), "erosion": EROSION,
        }, {"noise_texture": "tex_erode"}, layer=None)

    # a strong curl downdraft can drag an old, faint puff below the opening: it rolls along the roof instead
    nodes.append(node("roof", "collider", {"collider_type": "plane", "normal": [0.0, 1.0, 0.0],
                                           "position": [0.0, -0.15, 0.0], "bounce": 0.0, "friction": 0.1},
                      parent="chimney", layer=None))
    nodes.append(material("mat_plume", PLUME_DISSOLVE))
    nodes.append(material("mat_wisp", WISP_DISSOLVE))

    forces = ["f_cool", "f_billow", "f_curl"]

    def system(sid: str, mat: str, life: float, life_var: float, size: float, size_curve: list[list[Any]],
               opacity: float, opacity_curve: list[list[Any]], tint: list[list[Any]], cap: int,
               emissive: float, stretch: float = 0.0) -> dict[str, Any]:
        look: dict[str, Any] = {"render_mode": "billboard"}
        if stretch > 0.0:
            look = {"render_mode": "stretched_billboard", "velocity_stretch": stretch}
        return node(sid, "particle_system", {
            "max_particles": cap, "lifetime": r4(life), "lifetime_variance": r4(life_var),
            "size": size, "size_variance": r4(size * 0.22), "size_over_life": size_curve,
            "color": SMOKE_GREY, "color_over_life": tint,
            "opacity": opacity, "opacity_over_life": opacity_curve,
            "emissive": emissive, "drag": DRAG, "rotation_variance": 180.0, "angular_velocity_variance": 16.0,
            **look, "blend": "alpha", "sort": True, "soft_particle_distance": 0.3,
            "collision_radius": 0.05, "bounce": 0.0, "friction": 0.1,
        }, {"sprite": "tex_puff", "material": mat, "forces": forces, "colliders": ["roof"]})

    # the puff rate breathes on its own harmonics, so the column thickens and thins over the loop
    plume_rate = periodic([(0.24, 2, 0.7), (0.14, 5, 2.1)], PLUME_RATE)
    wisp_rate = periodic([(0.2, 3, 1.3), (0.12, 7, 0.2)], WISP_RATE)

    nodes.append(system("ps_wisp", "mat_wisp", WISP_LIFE, 0.25, 0.32, WISP_SIZE, 0.45, WISP_OPACITY, WISP_TINT,
                        40, 0.24, WISP_STRETCH))
    nodes.append(node("e_wisp", "emitter", {
        "shape": "disc", "radius": 0.16, "direction": [0.0, 1.0, 0.0], "spread": 6.0,
        "velocity": r4(LAUNCH * 1.1), "velocity_variance": 0.25,
        "rate": {"value": wisp_rate[0]["value"], "track": wisp_rate}, "start_time": 0.0, "duration": -1.0,
    }, {"particle": "ps_wisp"}, parent="wind_gust"))
    nodes.append(system("ps_plume", "mat_plume", PLUME_LIFE, 0.4, PLUME_SCALE, PLUME_SIZE, PLUME_ALPHA, PLUME_OPACITY, PLUME_TINT,
                        110, 0.13))
    nodes.append(node("e_plume", "emitter", {
        "shape": "disc", "radius": 0.2, "direction": [0.0, 1.0, 0.0], "spread": 7.0,
        "velocity": LAUNCH, "velocity_variance": 0.2,
        "rate": {"value": plume_rate[0]["value"], "track": plume_rate}, "start_time": 0.0, "duration": -1.0,
    }, {"particle": "ps_plume"}, parent="wind_gust"))

    # pre-fill: particles already in flight on the first frame, one band per age range
    prefill_ids: list[tuple[str, str]] = []

    def bands(prefix: str, table: list[tuple[Any, ...]], life: float, size: float, size_curve: list[list[Any]],
              opacity: float, opacity_curve: list[list[Any]], tint: list[list[Any]], dissolve: float,
              emissive: float, stretch: float = 0.0) -> None:
        for i, (count, direction, speed, speed_var, spread, remaining, remaining_var, points) in enumerate(table):
            start = max(0.0, 1.0 - remaining / life)       # normalised age the band's puffs already have
            k = f"{prefix}_pre{i + 1}"
            # the shader erodes by dissolve * (0.25 + 0.75 age): match it at the middle of the band's own life
            matched = dissolve * (0.25 + 0.75 * (start + (1.0 - start) * 0.5)) / (0.25 + 0.75 * 0.5)
            nodes.append(material(f"mat_{k}", min(0.95, matched)))
            nodes.append(system(f"ps_{k}", f"mat_{k}", remaining, remaining_var, size, tail(size_curve, start),
                                opacity, tail(opacity_curve, start), tail(tint, start), 24, emissive, stretch))
            # the band's puffs are re-spawned along the path the running plume's puffs of that age trace
            nodes.append(node(f"path_{k}", "curve", {"points": points, "curve_type": "linear",
                                                    "segments": max(8, 2 * len(points))}))
            nodes.append(node(f"e_{k}", "emitter", {
                "shape": "curve", "direction": direction, "spread": spread, "velocity": speed,
                "velocity_variance": speed_var, "rate": 0.0, "burst_count": count, "burst_times": [0.0],
                "start_time": 0.0, "duration": 0.05,
            }, {"particle": f"ps_{k}", "shape_curve": f"path_{k}"}, parent="wind_gust"))
            prefill_ids.append((f"ps_{k}", f"e_{k}"))

    bands("plume", PREFILL_PLUME, PLUME_LIFE, PLUME_SCALE, PLUME_SIZE, PLUME_ALPHA, PLUME_OPACITY, PLUME_TINT,
          PLUME_DISSOLVE, 0.13)
    bands("wisp", PREFILL_WISP, WISP_LIFE, 0.32, WISP_SIZE, 0.45, WISP_OPACITY, WISP_TINT, WISP_DISSOLVE, 0.24,
          WISP_STRETCH)

    nodes += [
        node("l_key", "light", {"light_type": "point", "position": [-2.0, 7.0, 6.5], "color": KEY,
                                "intensity": 300.0, "radius": 30.0}, layer="light"),
        node("l_sky", "light", {"light_type": "point", "position": [1.5, 10.0, -5.0], "color": SKY,
                                "intensity": 22.0, "radius": 30.0}, layer="light"),
        node("l_hearth", "light", {"light_type": "point", "position": [0.0, -0.45, 0.0], "color": HEARTH,
                                   "intensity": 2.4, "radius": 2.4, "flicker_amplitude": 0.18,
                                   "flicker_frequency": 2.2}, parent="chimney", layer="light"),
    ]

    plume_systems = ["ps_wisp", "ps_plume"] + [sid for sid, _ in prefill_ids]
    plume_emitters = ["e_wisp", "e_plume"] + [eid for _, eid in prefill_ids]
    band_emitters = [eid for _, eid in prefill_ids]

    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit_label: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit_label, "bindings": bindings}

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    controls = [
        control("wind_strength", "Wind strength", "Wind", 0.0, 2.5, [mul("wind_gust", "rotation")]),
        control("wind_direction", "Wind direction", "Wind", -180.0, 180.0, [mul("wind_heading", "rotation")],
                "deg", 1.0, 1.0),
        control("turbulence", "Turbulence", "Wind", 0.0, 2.0, [mul("f_billow", "strength"), mul("f_curl", "strength")]),
        control("smoke_amount", "Smoke amount", "Smoke", 0.0, 3.0,
                [mul(e, "rate") for e in ("e_wisp", "e_plume")] + [mul(e, "burst_count") for e in band_emitters]),
        control("smoke_height", "Smoke height", "Smoke", 0.6, 2.0,
                # the launch speed sets how high the plume climbs before it stalls and spreads; the pre-fill
                # paths stretch with it
                [mul(e, "velocity") for e in plume_emitters] + [mul(e, "scale") for e in band_emitters]),
        control("thickness", "Thickness", "Smoke", 0.0, 2.0, [mul(s, "opacity") for s in plume_systems]),
        control("brightness", "Smoke brightness", "Smoke", 0.2, 2.0, [mul(s, "color") for s in plume_systems]),
        control("global_speed", "Speed", "Global", 0.25, 3.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}], step=0.05),
    ]

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": "Always-on chimney smoke: soft, lit grey wood smoke that curls out of the opening, leans and "
                       "bends with gusting wind and thins into haze about 5 m up.",
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [{"name": "always_on", "start": 0.0, "end": DURATION}]},
        "layers": [{"id": "smoke", "name": "Smoke", "role": "ambient"},
                   {"id": "light", "name": "Light", "role": "ambient"}],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/chimney_smoke.py",
            "category": "ambient",
            "loop": {"start": 0.0, "end": DURATION},
            "attachment": {
                "origin_node": "chimney",
                "notes": "Attach the `chimney` node at the centre of the chimney opening; the effect draws no "
                         "chimney. Feed the global wind into Wind strength (0 calm, 1 light, 3 strong) and Wind "
                         "direction (degrees around Y). l_key and l_sky are preview stage lights: drop them where the "
                         "scene lights the smoke. It is always on and loops seamlessly.",
            },
            "render_settings": {"background": [0.034, 0.045, 0.066, 1.0], "ground_plane": False, "grid": False,
                                "bloom_intensity": 0.12, "bloom_radius": 0.04, "exposure": 1.0},
            "tags": ["ambient", "environment", "smoke", "chimney"],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write chimney_smoke.json into")
    parser.add_argument("--check", help="compare an existing file (or <dir>/chimney_smoke.json) with the generator "
                                        "output byte for byte")
    args = parser.parse_args()
    text = render(build())
    if args.check:
        path = Path(args.check)
        if path.is_dir():
            path = path / f"{SLUG}.json"
        same = path.is_file() and path.read_text() == text
        print(f"{path}: {'identical' if same else 'DIFFERENT'}")
        return 0 if same else 1
    out = Path(args.out or ".") / f"{SLUG}.json"
    out.write_text(text)
    print(f"{out}  ({len(build()['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
