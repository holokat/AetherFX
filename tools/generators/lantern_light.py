#!/usr/bin/env python3
"""Generate the Lantern Light effect: the always-on flame, glow, light and embers inside a lantern.

    python tools/generators/lantern_light.py --out examples/effects
    python tools/generators/lantern_light.py --check examples/effects/lantern_light.json

A game places this inside its own lantern model (the effect draws no lantern and no candle): attach the
`lantern` node at the wick. In the preview the wick hangs 1 m above the ground so the light pool shows.

* FLAME   one candle tongue from the `flame` texture op, baked as a seamless 16-frame flipbook and played
  as a single long-lived billboard, coloured by a candle temperature ramp (pale gold core, amber body, orange
  edge), breathing very slightly in size.
* GLOW    a soft warm halo around the flame and a much fainter, wider bloom for the lit glass and air.
* LIGHT   one warm point light, 3 m radius, with a slow, subtle flicker.
* EMBERS  two to eight tiny embers at a time drifting up from the tip on a gentle curl, fading out.

It is always on: nothing starts, peaks or ends. Every curve that shapes the loop starts and ends on the
same value, the flame and glow exist from the first frame, and a small burst pre-fills the embers, so
the preview loops without a visible restart.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

NAME = "Lantern Light"
SLUG = "lantern_light"
DURATION = 6.0
SEED = 51717

WICK_HEIGHT = 1.0                          # preview only: the wick hangs 1 m up so the light pool shows
FLAME_SIZE = 0.11                          # metres, the flame sprite (root at the bottom edge)

GOLD = [1.0, 0.72, 0.34, 1.0]
AMBER = [1.0, 0.56, 0.22, 1.0]             # about 1900 K
EMBER = [0.95, 0.36, 0.1, 1.0]
CANDLE_RAMP = [                            # heat -> colour: transparent edge, orange rim, gold body, pale core
    [0.0, [0.0, 0.0, 0.0, 1.0]],
    [0.2, [0.5, 0.12, 0.02, 1.0]],
    [0.45, [0.95, 0.4, 0.08, 1.0]],
    [0.72, [1.0, 0.6, 0.2, 1.0]],
    [0.9, [1.0, 0.77, 0.4, 1.0]],
    [1.0, [1.0, 0.9, 0.66, 1.0]],
]
# slow breathing over the loop; first and last values match so the loop closes
BREATH = [[0.0, 1.0], [0.13, 1.045], [0.27, 0.975], [0.41, 1.035], [0.55, 0.985], [0.69, 1.05],
          [0.83, 0.99], [1.0, 1.0]]
GLOW_BREATH = [[0.0, 1.0], [0.2, 0.92], [0.37, 1.03], [0.58, 0.95], [0.8, 1.04], [1.0, 1.0]]


def node(nid: str, ntype: str, params: dict[str, Any], inputs: dict[str, Any] | None = None,
         parent: str | None = "lantern", layer: str | None = "lantern") -> dict[str, Any]:
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
    return node(nid, "texture", params, parent=None, layer=None)


def always_on(nid: str, particle: str, position: list[float]) -> dict[str, Any]:
    """One particle born on the first frame that lives the whole loop."""
    return node(nid, "emitter", {
        "shape": "point", "position": position, "velocity": 0.0,
        "rate": 0.0, "burst_count": 1, "burst_times": [0.0], "start_time": 0.0, "duration": 0.05,
    }, {"particle": particle})


def build() -> dict[str, Any]:
    flame_centre = [0.0, round(FLAME_SIZE * 0.46, 4), 0.0]
    nodes = [
        node("cam", "camera", {"position": [0.95, 1.32, 1.95], "target": [0.0, 0.86, 0.0], "fov": 40.0},
             parent=None, layer=None),
        node("lantern", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                 "position": [0.0, WICK_HEIGHT, 0.0]}, parent=None),
        texture("tex_candle", 64, 128, [
            {"id": "f", "op": "flame", "params": {"frequency": 2.4, "speed": 0.7, "warp": 0.16, "width": 0.5,
                                                  "sharpness": 1.35, "licks": 0.05, "seed": 7}},
        ], frames=16),
        texture("tex_glow", 64, 64, [
            {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "smooth"}},
            {"id": "l", "op": "levels", "params": {"gamma": 0.9}, "inputs": {"a": "r"}},
        ]),
        texture("tex_ember", 32, 32, [
            {"id": "r", "op": "gradient_radial", "params": {"radius": 0.5, "falloff": "quadratic"}},
        ]),
        node("mat_candle", "material", {
            "blend": "additive", "shading": "unlit", "emissive_intensity": 1.0,
            "temperature_gradient": CANDLE_RAMP, "soft_particle": True, "depth_fade": 0.02,
        }, parent=None, layer=None),
        node("mat_glow", "material", {
            "blend": "additive", "shading": "unlit", "emissive_color": AMBER, "emissive_intensity": 0.0,
            "soft_particle": True, "depth_fade": 0.05,
        }, parent=None, layer=None),
        node("f_rise", "force", {"force_type": "directional", "direction": [0.0, 1.0, 0.0], "strength": 0.16},
             parent=None, layer=None),
        node("f_curl", "force", {"force_type": "curl_noise", "strength": 0.22, "frequency": 1.8, "speed": 0.4},
             parent=None, layer=None),

        # the flame: one long-lived billboard playing the looping flipbook
        node("ps_flame", "particle_system", {
            "max_particles": 2, "lifetime": DURATION, "size": FLAME_SIZE, "size_over_life": BREATH,
            "color": [1.0, 1.0, 1.0, 1.0], "opacity": 0.95, "opacity_over_life": [[0.0, 1.0], [1.0, 1.0]],
            # the viewer draws this texture's root at the top, so the billboard turns half a circle
            "rotation": 180.0,
            "emissive": 1.05, "render_mode": "billboard", "sprite_fps": 14.0, "blend": "additive",
            "soft_particle_distance": 0.01,
        }, {"sprite": "tex_candle", "material": "mat_candle"}, parent=None),
        always_on("e_flame", "ps_flame", flame_centre),

        # the glow: a soft halo, and a faint wide bloom for the lit glass and air
        node("ps_glow", "particle_system", {
            "max_particles": 2, "lifetime": DURATION, "size": 0.42, "size_over_life": BREATH,
            "color": AMBER, "opacity": 0.24, "opacity_over_life": GLOW_BREATH,
            "emissive": 0.9, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.05,
        }, {"sprite": "tex_glow", "material": "mat_glow"}, parent=None),
        always_on("e_glow", "ps_glow", [0.0, 0.045, 0.0]),
        node("ps_bloom", "particle_system", {
            "max_particles": 2, "lifetime": DURATION, "size": 1.15,
            "color": AMBER, "opacity": 0.07, "opacity_over_life": GLOW_BREATH,
            "emissive": 0.6, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.2,
        }, {"sprite": "tex_glow", "material": "mat_glow"}, parent=None),
        always_on("e_bloom", "ps_bloom", [0.0, 0.05, 0.0]),

        # the light: warm, low, slow flicker
        node("l_lantern", "light", {
            "light_type": "point", "position": [0.0, 0.06, 0.0], "color": AMBER,
            "intensity": 2.0, "radius": 3.2, "flicker_amplitude": 0.12, "flicker_frequency": 1.1,
        }),

        # the embers: sparse, slow, drifting up from the tip
        node("ps_embers", "particle_system", {
            "max_particles": 16, "lifetime": 2.0, "lifetime_variance": 0.8,
            "size": 0.02, "size_variance": 0.007,
            "color": GOLD, "color_over_life": [[0.0, [1.0, 0.86, 0.55, 1.0]], [0.4, GOLD], [1.0, EMBER]],
            "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.7, 0.7], [1.0, 0.0]],
            "emissive": 2.0, "drag": 0.6, "render_mode": "billboard", "blend": "additive",
            "soft_particle_distance": 0.02,
        }, {"sprite": "tex_ember", "material": "mat_glow", "forces": ["f_rise", "f_curl"]}, parent=None),
        node("e_embers", "emitter", {
            "shape": "disc", "radius": 0.02, "position": [0.0, round(FLAME_SIZE * 0.9, 4), 0.0],
            "direction": [0.0, 1.0, 0.0], "spread": 25.0, "velocity": 0.14, "velocity_variance": 0.06,
            "rate": 2.6, "burst_count": 3, "burst_times": [0.0], "start_time": 0.0, "duration": DURATION,
        }, {"particle": "ps_embers"}),
    ]

    colour_bindings = []
    for n in nodes:
        for parameter in ("color", "color_over_life", "emissive_color", "temperature_gradient"):
            if parameter in n["parameters"]:
                colour_bindings.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit_label: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit_label, "bindings": bindings}

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    controls = [
        control("brightness", "Brightness", "Light", 0.0, 2.5,
                [mul("l_lantern", "intensity"), mul("ps_flame", "emissive"), mul("ps_glow", "emissive"),
                 mul("ps_bloom", "emissive")]),
        control("light_radius", "Light radius", "Light", 0.3, 2.0,
                [mul("l_lantern", "radius"), mul("ps_bloom", "size")]),
        control("flicker", "Flicker", "Light", 0.0, 3.0, [mul("l_lantern", "flicker_amplitude")]),
        control("flame_size", "Flame size", "Flame", 0.5, 2.0,
                [mul("ps_flame", "size"), mul("ps_glow", "size")]),
        control("embers", "Embers", "Embers", 0.0, 4.0, [mul("e_embers", "rate"), mul("e_embers", "burst_count")]),
        control("hue", "Glass hue", "Global", -180.0, 180.0, colour_bindings, "deg", 0.0, 1.0),
        control("global_speed", "Speed", "Global", 0.25, 3.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]),
    ]

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": "The always-on light inside a lantern: a small candle flame, a warm soft glow, a gently "
                       "flickering light that pools about 3 m around it, and a few drifting embers.",
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [{"name": "always_on", "start": 0.0, "end": DURATION}]},
        "layers": [{"id": "lantern", "name": "Lantern flame", "role": "ambient"}],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/lantern_light.py",
            "category": "ambient",
            "loop": {"start": 0.0, "end": DURATION},
            "attachment": {
                "origin_node": "lantern", "preview_height": WICK_HEIGHT,
                "notes": "Attach the `lantern` node at the lantern's wick; the effect draws no lantern or candle. "
                         "It is always on and loops seamlessly. Use the glass hue for blue or red glass.",
            },
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.09,
                                "bloom_intensity": 0.3, "bloom_radius": 0.04, "exposure": 1.0, "grid": False},
            "tags": ["ambient", "lantern", "light", "flame", "candle", "environment"],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write lantern_light.json into")
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
