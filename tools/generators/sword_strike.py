#!/usr/bin/env python3
"""Generate the Sword Strike effect: a subtle, clean contact spark for heavy or critical melee hits.

    python tools/generators/sword_strike.py --out examples/effects
    python tools/generators/sword_strike.py --check examples/effects/sword_strike.json

"Less, but better": a game fires this on the hit event of a heavy two-handed swing or a critical strike,
at the contact point, oriented so +X of the effect points back along the hit normal (towards the
attacker). t = 0 is the contact, so nothing waits before the spark.

* SPARKS  about a dozen warm, velocity-stretched sparks sprayed in a cone around the hit normal, 3-8 m/s,
  living about a tenth of a second, pulled down by gravity.
* FLECKS  a few smaller, slower flecks that fall a little longer - the follow-through.
* FLASH   a brief soft warm glow with a small hot centre, round on purpose (no four-pointed star glint),
  plus a low, short point light.

No lingering decals, no screen-filling light, no weapon trail: the swing trail, if a game wants one,
belongs on the weapon itself.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

NAME = "Sword Strike"
SLUG = "sword_strike"
DURATION = 0.5
SEED = 26041

CONTACT = [0.0, 1.25, 0.0]                 # where the blade meets the target (chest height)
NORMAL = [-1.0, 0.32, 0.18]                # the hit normal: back towards the attacker, a little up

GOLD = [1.0, 0.82, 0.48, 1.0]              # #FFD27A
AMBER = [1.0, 0.7, 0.28, 1.0]              # #FFB347
EMBER = [0.92, 0.34, 0.08, 1.0]
HOT = [1.0, 0.94, 0.82, 1.0]


def unit(v: list[float]) -> list[float]:
    length = math.sqrt(sum(c * c for c in v))
    return [round(c / length, 4) for c in v]


def node(nid: str, ntype: str, params: dict[str, Any], inputs: dict[str, Any] | None = None,
         layer: str | None = "impact") -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        entry["layer"] = layer
    entry["parameters"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def burst(nid: str, particle: str, count: int, velocity: float, variance: float, spread: float,
          direction: list[float]) -> dict[str, Any]:
    return node(nid, "emitter", {
        "shape": "sphere", "radius": 0.02, "position": CONTACT, "direction": direction,
        "spread": spread, "velocity": velocity, "velocity_variance": variance,
        "rate": 0.0, "burst_count": count, "burst_times": [0.0], "start_time": 0.0, "duration": 0.05,
    }, {"particle": particle})


def build() -> dict[str, Any]:
    normal = unit(NORMAL)
    nodes = [
        node("cam", "camera", {"position": [0.95, 1.5, 2.45], "target": [-0.3, 1.25, 0.0], "fov": 36.0}, layer=None),
        node("tex_spark", "texture", {"width": 32, "height": 32, "graph": {"nodes": [
            {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "quadratic"}},
            {"id": "l", "op": "levels", "params": {"gamma": 0.7}, "inputs": {"a": "r"}},
        ], "output": "l"}}, layer=None),
        node("tex_glow", "texture", {"width": 64, "height": 64, "graph": {"nodes": [
            {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
            {"id": "l", "op": "levels", "params": {"gamma": 0.85}, "inputs": {"a": "r"}},
        ], "output": "l"}}, layer=None),
        node("mat_spark", "material", {
            "blend": "additive", "shading": "unlit", "emissive_color": AMBER, "emissive_intensity": 0.0,
            "soft_particle": True, "depth_fade": 0.05,
        }, layer=None),
        node("f_gravity", "force", {"force_type": "gravity", "strength": 9.81}, layer=None),

        # the sparks: a tight, fast spray along the hit normal
        node("ps_sparks", "particle_system", {
            "max_particles": 48, "lifetime": 0.15, "lifetime_variance": 0.05,
            "size": 0.022, "size_variance": 0.009, "size_over_life": [[0.0, 1.0], [1.0, 0.45]],
            "color": GOLD, "color_over_life": [[0.0, HOT], [0.25, GOLD], [0.7, AMBER], [1.0, EMBER]],
            "opacity_over_life": [[0.0, 1.0], [0.6, 0.85], [1.0, 0.0]],
            "emissive": 2.6, "emissive_over_life": [[0.0, 1.0], [1.0, 0.35]],
            "drag": 2.5, "render_mode": "stretched_billboard", "velocity_stretch": 0.95, "blend": "additive",
            "soft_particle_distance": 0.02,
        }, {"sprite": "tex_spark", "material": "mat_spark", "forces": ["f_gravity"]}),
        burst("e_sparks", "ps_sparks", 12, 5.5, 2.5, 34.0, normal),

        # the follow-through: a few slower flecks that fall and fade
        node("ps_flecks", "particle_system", {
            "max_particles": 24, "lifetime": 0.26, "lifetime_variance": 0.08,
            "size": 0.02, "size_variance": 0.008,
            "color": AMBER, "color_over_life": [[0.0, GOLD], [0.5, AMBER], [1.0, EMBER]],
            "opacity_over_life": [[0.0, 1.0], [0.5, 0.7], [1.0, 0.0]],
            "emissive": 1.7, "drag": 1.5, "render_mode": "stretched_billboard", "velocity_stretch": 0.3,
            "blend": "additive", "soft_particle_distance": 0.02,
        }, {"sprite": "tex_spark", "material": "mat_spark", "forces": ["f_gravity"]}),
        burst("e_flecks", "ps_flecks", 6, 2.2, 1.0, 58.0, unit([NORMAL[0], NORMAL[1] + 0.35, NORMAL[2]])),

        # the flash: brief, soft and round, with a small hot centre
        node("ps_flash", "particle_system", {
            "max_particles": 2, "lifetime": 0.055, "size": 0.24,
            "size_over_life": [[0.0, 0.55], [0.3, 1.0], [1.0, 0.85]],
            "color": GOLD, "opacity": 0.5, "opacity_over_life": [[0.0, 0.6], [0.12, 1.0], [1.0, 0.0]],
            "emissive": 1.5, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.05,
        }, {"sprite": "tex_glow", "material": "mat_spark"}),
        burst("e_flash", "ps_flash", 1, 0.0, 0.0, 0.0, [0.0, 1.0, 0.0]),
        node("ps_flash_core", "particle_system", {
            "max_particles": 2, "lifetime": 0.045, "size": 0.075,
            "size_over_life": [[0.0, 0.6], [0.3, 1.0], [1.0, 0.7]],
            "color": HOT, "opacity_over_life": [[0.0, 0.7], [0.15, 1.0], [1.0, 0.0]],
            "emissive": 2.4, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.02,
        }, {"sprite": "tex_glow", "material": "mat_spark"}),
        burst("e_flash_core", "ps_flash_core", 1, 0.0, 0.0, 0.0, [0.0, 1.0, 0.0]),

        # a low, short light: enough to warm what is right there, never to light the scene
        node("l_hit", "light", {
            "light_type": "point", "position": [round(CONTACT[0] - 0.15, 4), CONTACT[1] + 0.05, 0.1],
            "color": AMBER, "radius": 2.2,
            "intensity": {"value": 0.0, "track": [{"time": 0.0, "value": 0.0}, {"time": 0.015, "value": 3.0},
                                                   {"time": 0.1, "value": 0.0}]},
        }),
    ]

    colour_bindings = []
    for n in nodes:
        for parameter in ("color", "color_over_life", "emissive_color"):
            if parameter in n["parameters"]:
                colour_bindings.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit_label: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit_label, "bindings": bindings}

    controls = [
        control("spark_amount", "Spark amount", "Sparks", 0.0, 3.0,
                [{"node": "e_sparks", "parameter": "burst_count", "op": "multiply"},
                 {"node": "e_flecks", "parameter": "burst_count", "op": "multiply"}]),
        control("spark_speed", "Spark speed", "Sparks", 0.4, 2.0,
                [{"node": "e_sparks", "parameter": "velocity", "op": "multiply"},
                 {"node": "e_flecks", "parameter": "velocity", "op": "multiply"}]),
        control("flash", "Flash", "Flash", 0.0, 2.5,
                [{"node": "ps_flash", "parameter": "size", "op": "multiply"},
                 {"node": "ps_flash_core", "parameter": "size", "op": "multiply"},
                 {"node": "l_hit", "parameter": "intensity", "op": "multiply"}]),
        control("hue", "Element hue", "Global", -180.0, 180.0, colour_bindings, "deg", 0.0, 1.0),
        control("global_speed", "Speed", "Global", 0.25, 3.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]),
    ]

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": "A subtle contact spark for heavy two-handed or critical melee hits: a short warm spray, a "
                       "brief soft flash and a few falling flecks, gone in a third of a second.",
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [
            {"name": "impact", "start": 0.0, "end": 0.1},
            {"name": "follow_through", "start": 0.1, "end": 0.3},
            {"name": "settle", "start": 0.3, "end": DURATION},
        ]},
        "layers": [{"id": "impact", "name": "Contact spark", "role": "impact"}],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/sword_strike.py",
            "attachment": {
                "origin": CONTACT, "normal": "+x",
                "notes": "Spawn at the weapon's contact point on the hit event, rotated so +X points back "
                         "along the hit normal; t = 0 is the contact. Use the hue control for fire, frost, "
                         "holy or shadow weapons.",
            },
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.05,
                                "bloom_intensity": 0.24, "bloom_radius": 0.03, "exposure": 1.0, "grid": False},
            "tags": ["melee", "impact", "spark", "sword", "hit", "critical"],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write sword_strike.json into")
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
