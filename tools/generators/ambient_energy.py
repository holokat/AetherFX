#!/usr/bin/env python3
"""Generate the Ambient Energy effect: a subtle, always-on magical presence for an environment.

    python tools/generators/ambient_energy.py --out examples/effects
    python tools/generators/ambient_energy.py --check examples/effects/ambient_energy.json

"A hint of something greater": extremely sparse, low-opacity light that a player notices only when
they look for it. It fills a volume about 7 m wide, 5 m deep and 2.6 m high around the origin; a game
drops it into ruins, a night forest, an altar or a cave and scales it with the area control.

* MOTES    the primary particle: soft glowing dots, very low opacity, drifting slowly on a gentle curl
           and twinkling as they fade in and out over five to fifteen seconds.
* RISERS   the secondary particle: fewer, finer motes that drift slowly upward, a little stretched.
* CLUSTERS rare: three times a loop, a small knot of four or five motes gathers somewhere and disperses.
* WISP     rare: once a loop, a tiny point of light circles slowly and leaves a faint curl behind it.
* POOLS    three wide, very faint pools of light on the ground that breathe on their own beats.

Always on: the loop is long, the pools exist from the first frame, a small burst pre-fills
the motes, and every loop-shaping curve starts and ends on the same value.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

NAME = "Ambient Energy"
SLUG = "ambient_energy"
DURATION = 12.0                            # the library's longest allowed loop
SEED = 60413

AREA = [7.0, 2.6, 5.0]                     # the volume the motes live in (box extents)
AREA_CENTRE = [0.0, 1.4, 0.0]

ARCANE = [0.36, 0.56, 1.0, 1.0]            # the default: arcane blue
PALE = [0.66, 0.8, 1.0, 1.0]
DEEP = [0.26, 0.32, 0.96, 1.0]

# rare events: (time, position) of the clusters, and the wisp's window and orbit centre
CLUSTERS = [(1.8, [-2.2, 1.1, 0.8]), (5.6, [1.9, 1.7, -0.6]), (9.3, [-0.4, 0.8, 1.5])]
WISP_START, WISP_END = 3.0, 8.4
WISP_CENTRE = [1.2, 1.25, 0.9]
GLOWS = [[-2.4, 0.35, -0.8], [2.1, 0.3, 0.4], [0.2, 0.25, -1.9]]   # where the faint pools breathe

TWINKLE = [[0.0, 0.0], [0.12, 1.0], [0.26, 0.55], [0.4, 0.95], [0.55, 0.6], [0.7, 0.9], [0.86, 0.5], [1.0, 0.0]]


def node(nid: str, ntype: str, params: dict[str, Any], inputs: dict[str, Any] | None = None,
         parent: str | None = None, layer: str | None = "presence") -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        entry["layer"] = layer
    if parent:
        entry["parent"] = parent
    entry["parameters"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def build() -> dict[str, Any]:
    mote = {"sprite": "tex_mote", "material": "mat_glow"}
    drift = {"forces": ["f_curl", "f_rise"]}
    nodes: list[dict[str, Any]] = [
        node("cam", "camera", {"position": [0.0, 1.55, 5.0], "target": [0.0, 1.05, 0.0], "fov": 42.0}, layer=None),
        node("tex_mote", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
            {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
            {"id": "l", "op": "levels", "params": {"gamma": 1.25}, "inputs": {"a": "r"}},
        ], "output": "l"}}, layer=None),
        node("mat_glow", "material", {
            "blend": "additive", "shading": "unlit", "emissive_color": ARCANE, "emissive_intensity": 0.0,
            "soft_particle": True, "depth_fade": 0.1,
        }, layer=None),
        node("f_curl", "force", {"force_type": "curl_noise", "strength": 0.05, "frequency": 0.45, "speed": 0.08},
             layer=None),
        node("f_rise", "force", {"force_type": "directional", "direction": [0.0, 1.0, 0.0], "strength": 0.012},
             layer=None),

        # the primary motes
        node("ps_motes", "particle_system", {
            "max_particles": 40, "lifetime": 9.0, "lifetime_variance": 4.0,
            "size": 0.1, "size_variance": 0.045,
            "color": ARCANE, "color_over_life": [[0.0, PALE], [0.5, ARCANE], [1.0, DEEP]],
            "opacity": 0.24, "opacity_over_life": TWINKLE,
            "emissive": 2.2, "drag": 0.4, "render_mode": "billboard", "blend": "additive",
            "soft_particle_distance": 0.1,
        }, {**mote, **drift}),
        node("e_motes", "emitter", {
            "shape": "box", "size": AREA, "position": AREA_CENTRE, "direction": [0.0, 0.0, 0.0],
            "velocity": 0.05, "velocity_variance": 0.03,
            "rate": 0.9, "burst_count": 9, "burst_times": [0.0], "start_time": 0.0, "duration": DURATION,
        }, {"particle": "ps_motes"}),

        # the secondary risers: finer, slower upward drift, a little stretched
        node("ps_risers", "particle_system", {
            "max_particles": 16, "lifetime": 6.0, "lifetime_variance": 2.0,
            "size": 0.034, "size_variance": 0.012,
            "color": PALE, "color_over_life": [[0.0, PALE], [1.0, ARCANE]],
            "opacity": 0.24, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.7, 0.8], [1.0, 0.0]],
            "emissive": 1.8, "drag": 0.2, "render_mode": "stretched_billboard", "velocity_stretch": 16.0,
            "blend": "additive", "soft_particle_distance": 0.1,
        }, mote),
        node("e_risers", "emitter", {
            "shape": "box", "size": [AREA[0], 0.8, AREA[2]], "position": [0.0, 0.5, 0.0],
            "direction": [0.0, 1.0, 0.0], "spread": 8.0, "velocity": 0.16, "velocity_variance": 0.05,
            "rate": 0.35, "burst_count": 2, "burst_times": [0.0], "start_time": 0.0, "duration": DURATION,
        }, {"particle": "ps_risers"}),

        # rare: small clusters
        node("ps_cluster", "particle_system", {
            "max_particles": 24, "lifetime": 4.0, "lifetime_variance": 1.2,
            "size": 0.07, "size_variance": 0.025,
            "color": ARCANE, "color_over_life": [[0.0, PALE], [1.0, ARCANE]],
            "opacity": 0.26, "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.55, 0.7], [1.0, 0.0]],
            "emissive": 1.7, "drag": 0.5, "render_mode": "billboard", "blend": "additive",
            "soft_particle_distance": 0.1,
        }, {**mote, **drift}),
    ]
    for i, (when, where) in enumerate(CLUSTERS, start=1):
        nodes.append(node(f"e_cluster_{i}", "emitter", {
            "shape": "sphere", "radius": 0.14, "position": where, "direction": [0.0, 0.0, 0.0],
            "velocity": 0.05, "velocity_variance": 0.03,
            "rate": 0.0, "burst_count": 4 + (i % 2), "burst_times": [0.0], "start_time": when, "duration": 0.05,
        }, {"particle": "ps_cluster"}))

    # rare: a wisp that circles slowly and leaves a faint curl
    turns = 1.6
    nodes += [
        node("wisp_pivot", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, "position": WISP_CENTRE,
            "rotation": {"value": [12.0, 0.0, 0.0], "track": [
                {"time": WISP_START, "value": [12.0, 0.0, 0.0]},
                {"time": WISP_END, "value": [12.0, round(360.0 * turns, 1), 0.0]}]},
        }),
        node("wisp", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                              "position": [0.34, 0.0, 0.0]}, parent="wisp_pivot"),
        node("ps_wisp", "particle_system", {
            "max_particles": 16, "lifetime": 0.05, "size": 0.05, "color": PALE, "opacity": 0.5,
            "opacity_over_life": [[0.0, 0.7], [0.4, 1.0], [1.0, 0.0]],
            "emissive": 2.0, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.05,
        }, mote),
        node("e_wisp", "emitter", {
            "shape": "point", "velocity": 0.0, "inherit_velocity": 1.0,
            "rate": {"value": 0.0, "track": [
                {"time": WISP_START, "value": 0.0}, {"time": WISP_START + 0.8, "value": 90.0},
                {"time": WISP_END - 0.8, "value": 90.0}, {"time": WISP_END, "value": 0.0}]},
            "start_time": WISP_START, "duration": WISP_END - WISP_START,
        }, {"particle": "ps_wisp"}, parent="wisp"),
        node("t_wisp", "trail", {
            "lifetime": 1.1, "max_segments": 48, "min_vertex_distance": 0.01,
            "taper": [[0.0, 1.0], [1.0, 0.0]], "opacity_over_life": [[0.0, 0.5], [0.5, 0.2], [1.0, 0.0]],
            "color": ARCANE, "emissive": 1.2, "blend": "additive",
            "width": {"value": 0.0, "track": [
                {"time": WISP_START, "value": 0.0}, {"time": WISP_START + 0.8, "value": 0.018},
                {"time": WISP_END - 0.8, "value": 0.018}, {"time": WISP_END, "value": 0.0}]},
            "start_time": WISP_START, "duration": WISP_END - WISP_START + 1.2,
        }, {"source": "wisp", "material": "mat_glow"}),

        # three very faint pools of light on the ground that breathe for the whole loop
        node("tex_pool", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
            {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
            {"id": "l", "op": "levels", "params": {"gamma": 1.4}, "inputs": {"a": "r"}},
        ], "output": "l"}}, layer=None),
    ]
    breathe = [(0.0, 0.7), (0.18, 1.0), (0.37, 0.62), (0.55, 0.95), (0.74, 0.66), (0.9, 0.9), (1.0, 0.7)]
    for i, where in enumerate(GLOWS, start=1):
        phase = (i - 1) * 0.29                # each pool breathes on its own beat
        keys = sorted(((round((u + phase) % 1.0 * DURATION, 3), v) for u, v in breathe[:-1]), key=lambda kv: kv[0])
        keys = [(0.0, keys[-1][1])] + keys + [(DURATION, keys[-1][1])] if keys[0][0] > 0 else keys + [(DURATION, keys[0][1])]
        nodes.append(node(f"pool_{i}", "decal", {
            "shape": "circle", "position": [where[0], 0.0, where[2]], "size": [1.7 + 0.3 * i, 1.7 + 0.3 * i],
            "color": DEEP, "blend": "additive", "emissive": 1.0, "fade_in": 0.0, "fade_out": 0.0,
            "opacity": {"value": 0.08, "track": [{"time": t, "value": round(0.08 * v, 4)} for t, v in keys]},
            "start_time": 0.0, "duration": DURATION,
        }, {"texture": "tex_pool"}))

    colour_bindings = []
    for n in nodes:
        for parameter in ("color", "color_over_life", "emissive_color"):
            if parameter in n["parameters"]:
                colour_bindings.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit_label: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit_label, "bindings": bindings}

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    cluster_ids = [f"e_cluster_{i}" for i in range(1, len(CLUSTERS) + 1)]
    controls = [
        control("density", "Density", "Particles", 0.0, 4.0,
                [mul("e_motes", "rate"), mul("e_motes", "burst_count"), mul("e_risers", "rate"),
                 mul("e_risers", "burst_count")] + [mul(c, "burst_count") for c in cluster_ids]),
        control("brightness", "Brightness", "Global", 0.0, 3.0,
                [mul(p, "emissive") for p in ("ps_motes", "ps_risers", "ps_cluster", "ps_wisp")]
                + [mul("t_wisp", "emissive")] + [mul(f"pool_{i}", "emissive") for i in range(1, len(GLOWS) + 1)]),
        control("drift", "Drift speed", "Particles", 0.2, 3.0,
                [mul("e_motes", "velocity"), mul("e_risers", "velocity"), mul("f_curl", "strength")]),
        control("area", "Area size", "Global", 0.3, 3.0,
                [mul("e_motes", "size"), mul("e_risers", "size")]),
        control("hue", "Energy hue", "Global", -180.0, 180.0, colour_bindings, "deg", 0.0, 1.0),
        control("global_speed", "Speed", "Global", 0.25, 3.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]),
    ]

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": "A subtle, always-on magical presence: very sparse soft motes drifting and twinkling, a "
                       "few rising sparks, rare small clusters and a slow wisp, over faint breathing pools of light.",
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [{"name": "always_on", "start": 0.0, "end": DURATION}]},
        "layers": [{"id": "presence", "name": "Magical presence", "role": "ambient"}],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/ambient_energy.py",
            "category": "ambient",
            "loop": {"start": 0.0, "end": DURATION},
            "attachment": {
                "origin": [0.0, 0.0, 0.0], "area": AREA,
                "notes": "Place at ground level in the middle of the space; scale with the area control. It is "
                         "always on and loops. Use the energy hue for void, nature or holy zones.",
            },
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.06,
                                "bloom_intensity": 0.35, "bloom_radius": 0.05, "exposure": 1.0, "grid": False},
            "tags": ["ambient", "environment", "magic", "arcane", "motes", "atmosphere"],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write ambient_energy.json into")
    parser.add_argument("--check", help="compare an existing file with the generator output byte for byte")
    args = parser.parse_args()
    text = render(build())
    if args.check:
        same = Path(args.check).read_text() == text
        print(f"{args.check}: {'identical' if same else 'DIFFERENT'}")
        return 0 if same else 1
    out = Path(args.out or ".") / f"{SLUG}.json"
    out.write_text(text)
    print(f"{out}  ({len(build()['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
