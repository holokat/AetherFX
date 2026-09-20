#!/usr/bin/env python3
"""Generate the four Fire Torch effects (always-on wall / brazier / standing / handheld) from one description.

    python tools/generators/fire_torch.py --out examples/effects
    python tools/generators/fire_torch.py --check examples/effects

A game places the `torch_head` node at the TOP of the torch head (the mouth of the bowl), y = 0 there; the
effect draws no torch, bracket, pole, bowl or hand, only the flame, the embers, the smoke, the head glow and
the light. A handheld torch parents `torch_head` to the hand socket.

* FLAME    ONE shared `fire_sim` flipbook (a real 2D flame simulation: tongues that neck, split and tear off,
           ragged eroded edges) played per particle on overlapping stretched sprites, coloured by a
           temperature ramp: white-yellow core, orange body, deep red curling tips. Three narrow bands:
           `core` (the short white-hot root sitting in the torch head), `body` (the upward-licking column)
           and `tongue` (the tall thin licks that give the ragged, curling top).
* EMBERS   small hot sparks torn out of the flame that drift on curl noise, lean with the wind and cool from
           yellow to red as they rise (1-4 s). A gust rips off a shower of them.
* SMOKE    the Chimney Smoke puff (lit, alpha-blended, churning flipbook) but thin: a wisp leaving the flame
           TIP, bending with the wind and dissipating into haze (2-8 s).
* HEAD     a faint warm glow right at the head - the burning fuel/coals in the basket. No ground decal.
* LIGHT    a warm point light over the head with a layered natural flicker (slow breathing + gusts + fast
           flicker), and one faint big glow quad for the warm air around the flame.
* WIND     the Chimney Smoke rig: particles keep the horizontal speed they are launched with, so the wind is
           the lean of the launch direction. Emitters sit under invisible rig nodes: `wind_gust` tilts smoke
           and embers, `wind_flame` leans the flame less, and `wind_heading` turns everything to the wind.

The sheet's five frames are STATES OF ONE 12 s LOOP, not separate files. There is no ignition at t = 0 (the
effect is always on and pre-filled; a game scrubs the Fire intensity control up from 0 to light it):
  ESTABLISH  the stable burn everything settles back to (t ~ 0-2, 6-7.5, 10.5-12)
  FLICKER    the flame rate, the lean and the light breathe on their own harmonics the whole loop
  GUST       two wind gusts per loop (peaks at 3.1 s, strong, and 8.3 s, softer): the rigs tilt over, the
             launch speed rises so the flame leans AND stretches, the ember rate jumps and the light surges
  SETTLE     each gust decays back to the idle lean over ~2 s (see GUSTS / gust_env)

The four variants share every node id, the loop and the controls, and differ only by the VARIANTS table:
flame width and height, ember and smoke amount, wind lean, light - so a game can swap them freely.

Seamless: every track is periodic over the 12 s loop (starts and ends on the same value), and every system is
pre-filled on the first frame by bursts of particles that are already "in flight" - one band per age range,
spawned along the path the running particles of that age trace and carrying the remaining part of their
over-life curves (the Chimney Smoke / Campfire method). The tables are measured by simulating each variant to
the end of the loop (out/work/fire_torch/measure_prefill.py rewrites the PREFILL block below).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

DURATION = 12.0
SEED = 71410
HEAD_RADIUS = 0.085                        # metres: the burning mouth of the wall torch's head

# The flames burn slower than real time so the fire reads calm instead of frantic (the Campfire uses 2.0 -
# half speed). A torch is smaller and livelier than a campfire, so it is slowed less.
FLAME_SLOW = 1.5

# --- the four variants ---------------------------------------------------------------------------------------
# w: flame width (sprite size, head radius); h: flame height (launch speed, buoyancy, smoke/light height);
# rate: flame emission; embers / smoke: amounts; lean: how hard the smoke and embers are raked over by the
# wind, flame_lean: how hard the FLAME itself leans (less - the fuel holds it, and a raked column stacks its
# own sprites); expose: per-sprite flame opacity, tuned so all four variants clip the same amount (measured
# with out/work/fire_torch/hot.py); drift: ember drag/life (a carried torch trails its embers);
# light: light intensity; head: head glow.
VARIANTS: dict[str, dict[str, Any]] = {
    "fire_torch": {
        "name": "Fire Torch", "variant": "wall", "seed": SEED,
        "w": 1.0, "h": 1.0, "rate": 1.0, "embers": 1.0, "smoke": 1.0, "lean": 1.0, "flame_lean": 1.0,
        "expose": 1.0, "drift": 1.0, "light": 1.0, "head": 1.0,
        "blurb": "a wall-mounted torch: a tall, narrow, upward-licking flame about 0.85 m high with a "
                 "white-hot core, embers torn off by the wind and a thin wisp of smoke off the tip",
    },
    "fire_torch_brazier": {
        "name": "Fire Torch Brazier", "variant": "brazier", "seed": SEED + 1,
        "w": 1.62, "h": 1.85, "rate": 1.7, "embers": 1.85, "smoke": 1.7, "lean": 0.85, "flame_lean": 0.85,
        "expose": 0.62, "drift": 1.0, "light": 1.85, "head": 1.7,
        "blurb": "a wall brazier: a wider, fuller flame about 1.6 m high out of a burning bowl, with more "
                 "embers, a fatter column of smoke and a bright warm light",
    },
    "fire_torch_standing": {
        "name": "Fire Torch Standing", "variant": "standing", "seed": SEED + 2,
        "w": 1.14, "h": 1.18, "rate": 1.08, "embers": 1.35, "smoke": 1.15, "lean": 1.35, "flame_lean": 1.15,
        "expose": 0.9, "drift": 1.25, "light": 1.22, "head": 1.1,
        "blurb": "a standing torch on a post: a slightly taller, fuller and more exposed flame about 1.0 m high "
                 "that leans harder in the wind and sheds embers that drift a long way",
    },
    "fire_torch_handheld": {
        "name": "Fire Torch Handheld", "variant": "handheld", "seed": SEED + 3,
        "w": 0.92, "h": 0.94, "rate": 1.05, "embers": 1.2, "smoke": 0.45, "lean": 1.85, "flame_lean": 1.3,
        "expose": 0.95, "drift": 1.45, "light": 0.9, "head": 0.95,
        "blurb": "a carried torch: the smallest and liveliest flame, about 0.8 m high, leaning with the "
                 "movement of the bearer so its embers and a thin wisp of smoke trail away behind it",
    },
}

# --- look -------------------------------------------------------------------------------------------------
FIRE_RAMP = [                               # heat -> colour: deep red tips, orange body, white-yellow core
    [0.0, [0.3, 0.05, 0.0, 1.0]],
    [0.18, [0.76, 0.14, 0.01, 1.0]],
    [0.4, [1.0, 0.32, 0.05, 1.0]],
    [0.62, [1.0, 0.53, 0.11, 1.0]],
    [0.83, [1.0, 0.79, 0.4, 1.0]],
    [1.0, [1.0, 0.94, 0.76, 1.0]],
]
FLAME_COLOR_LIFE = [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.28, [1.0, 0.93, 0.84, 1.0]],
                    [0.6, [1.0, 0.8, 0.62, 1.0]], [1.0, [0.88, 0.54, 0.38, 1.0]]]
FIRE_LIGHT = [1.0, 0.47, 0.15, 1.0]
# per-band tints multiplying the temperature ramp: the fire gets cooler from the core out to the tips
CORE_TINT = [1.0, 0.86, 0.58, 1.0]
BODY_TINT = [1.0, 0.66, 0.3, 1.0]
TONGUE_TINT = [1.0, 0.46, 0.14, 1.0]
HEAD_COLOR = [1.0, 0.34, 0.07, 1.0]
EMBER_LIFE_COLOR = [[0.0, [1.0, 0.9, 0.55, 1.0]], [0.24, [1.0, 0.63, 0.2, 1.0]],
                    [0.6, [1.0, 0.31, 0.06, 1.0]], [1.0, [0.68, 0.09, 0.02, 1.0]]]

# --- over-life curves (also resampled for the pre-fill bands) ------------------------------------------------
CORE_SIZE = [[0.0, 0.5], [0.28, 1.0], [0.7, 0.86], [1.0, 0.5]]
CORE_OPACITY = [[0.0, 0.0], [0.12, 1.0], [0.58, 0.82], [1.0, 0.0]]
BODY_SIZE = [[0.0, 0.5], [0.3, 1.0], [0.72, 0.94], [1.0, 0.62]]
BODY_OPACITY = [[0.0, 0.0], [0.13, 1.0], [0.6, 0.76], [1.0, 0.0]]
TONGUE_SIZE = [[0.0, 0.42], [0.32, 1.06], [1.0, 0.6]]
TONGUE_OPACITY = [[0.0, 0.0], [0.11, 1.0], [0.48, 0.58], [0.8, 0.18], [1.0, 0.0]]
GLOW_SIZE = [[0.0, 0.72], [0.4, 1.0], [1.0, 1.18]]
GLOW_OPACITY = [[0.0, 0.0], [0.3, 1.0], [0.7, 0.8], [1.0, 0.0]]
EMBER_OPACITY = [[0.0, 0.0], [0.05, 1.0], [0.68, 0.72], [1.0, 0.0]]
EMBER_EMISSIVE = [[0.0, 1.5], [0.4, 1.0], [1.0, 0.4]]
SMOKE_SIZE = [[0.0, 0.55], [0.2, 0.9], [0.5, 1.35], [1.0, 2.1]]
SMOKE_OPACITY = [[0.0, 0.0], [0.06, 0.85], [0.2, 1.0], [0.42, 0.58], [0.62, 0.28], [0.8, 0.09], [1.0, 0.0]]

# --- physics shared by every variant --------------------------------------------------------------------------
FLAME_TILT = 5.0                           # idle flame lean at the default wind strength, degrees
WIND_TILT = 11.0                           # idle smoke / ember launch lean
GUST_FLAME_TILT = 12.0                     # extra flame lean at the peak of the strong gust
GUST_WIND_TILT = 22.0                      # extra smoke / ember lean at the peak of the strong gust
SMOKE_LAUNCH = 1.15
SMOKE_LIFE = 4.4
SMOKE_RATE = 7.2                           # at smoke density 1
SMOKE_ALPHA = 0.34
BAND_FLAME_OPACITY = 0.34                 # pre-fill flame bands, see build()
BAND_FLAME_EMISSIVE = 0.5                  # and how much of their HDR emissive they keep

# the two gusts of the loop: (peak time, strength, rise seconds, fall seconds)
GUSTS = [(3.1, 1.0, 1.0, 2.2), (8.3, 0.6, 0.8, 1.7)]

# systems pre-filled on the first frame: id -> normalised-age bands
BANDS: dict[str, list[tuple[float, float]]] = {
    "ps_flame_core": [(0.0, 0.5), (0.5, 1.0)],
    "ps_flame_body": [(0.0, 0.35), (0.35, 0.68), (0.68, 1.0)],
    "ps_flame_tongue": [(0.0, 0.5), (0.5, 1.0)],
    "ps_head_glow": [(0.0, 1.0)],
    "ps_embers": [(0.0, 0.4), (0.4, 1.0)],
    "ps_smoke": [(0.0, 0.08), (0.08, 0.3), (0.3, 0.55), (0.55, 0.78), (0.78, 1.0)],
}

# The wall torch seed is deliberately an ordinary one: at the gust peak the curl-noise field decides how
# fat the column gets, and seed 71409 happened to give an unusually lean frame that the whole family would
# have been exposed against.
# --- PREFILL BEGIN (written by out/work/fire_torch/measure_prefill.py) ----------------------------------------
# variant -> system -> one row per band: (count, direction, speed, speed variance, spread deg, remaining life,
# its variance, positions ordered by age) - measured on the loop's last frame in the system's rig frame at t = 0
PREFILL: dict[str, dict[str, list[Any]]] = {
    'fire_torch': {
        'ps_flame_core': [
            [8, [0.143, 0.99, -0.015], 0.497, 0.119, 11.7, 0.38, 0.158, [[-0.02, 0.03, 0.06], [0.02, 0.05, -0.0], [0.02, 0.06, 0.05], [0.03, 0.1, -0.05], [0.04, 0.07, -0.07], [0.02, 0.1, -0.06], [0.06, 0.19, -0.05], [-0.03, 0.2, 0.04]]],
            [6, [-0.042, 0.984, -0.175], 0.578, 0.146, 18.3, 0.067, 0.106, [[0.03, 0.25, -0.09], [-0.02, 0.22, 0.0], [-0.05, 0.29, 0.01], [-0.0, 0.25, -0.05], [0.05, 0.15, 0.05], [0.01, 0.33, -0.02]]],
        ],
        'ps_flame_body': [
            [12, [-0.001, 1.0, -0.028], 0.742, 0.078, 9.1, 0.731, 0.292, [[0.03, 0.09, 0.06], [-0.02, 0.12, -0.05], [0.03, 0.14, -0.01], [0.03, 0.15, 0.03], [0.06, 0.17, 0.02], [-0.06, 0.2, -0.05], [-0.03, 0.26, 0.05], [0.04, 0.24, 0.0], [0.04, 0.27, -0.0], [-0.01, 0.32, 0.02], [0.1, 0.38, 0.01], [0.05, 0.36, -0.01]]],
            [9, [-0.027, 0.999, -0.032], 0.699, 0.15, 10.7, 0.369, 0.199, [[0.04, 0.24, 0.02], [-0.09, 0.4, -0.02], [-0.03, 0.42, -0.05], [-0.05, 0.48, 0.06], [0.03, 0.38, -0.03], [-0.01, 0.53, -0.06], [0.04, 0.43, 0.03], [-0.03, 0.44, 0.01], [0.03, 0.53, 0.13]]],
            [8, [0.065, 0.99, 0.122], 0.672, 0.095, 11.9, 0.101, 0.094, [[0.04, 0.6, 0.11], [0.09, 0.46, -0.04], [0.14, 0.66, -0.05], [-0.01, 0.54, 0.14], [-0.01, 0.71, 0.12], [0.09, 0.76, 0.02], [0.02, 0.52, 0.23], [0.13, 0.95, -0.03]]],
        ],
        'ps_flame_tongue': [
            [7, [0.004, 0.999, -0.037], 1.157, 0.185, 7.9, 0.479, 0.257, [[0.01, 0.23, -0.01], [-0.01, 0.29, -0.03], [-0.02, 0.38, -0.01], [-0.0, 0.44, 0.05], [0.04, 0.42, -0.04], [0.08, 0.51, 0.01], [-0.03, 0.68, -0.09]]],
            [4, [0.024, 0.998, 0.062], 1.16, 0.113, 6.1, 0.207, 0.217, [[0.06, 0.73, -0.04], [-0.06, 0.69, -0.09], [0.05, 0.73, -0.01], [0.08, 0.79, 0.18]]],
        ],
        'ps_head_glow': [
            [4, [0.112, 0.956, -0.27], 0.029, 0.009, 13.8, 0.698, 0.56, [[0.01, 0.03, 0.01], [-0.02, 0.04, -0.04], [-0.06, 0.03, 0.03], [-0.06, 0.07, 0.02]]],
        ],
        'ps_embers': [
            [9, [0.183, 0.88, 0.437], 1.275, 0.585, 26.3, 1.751, 0.896, [[0.01, 0.52, -0.03], [-0.02, 0.58, 0.11], [0.09, 0.68, 0.06], [0.11, 0.91, -0.02], [-0.08, 0.57, -0.0], [0.34, 1.01, 0.34], [0.08, 0.87, 0.78], [0.29, 1.14, 0.4], [-0.09, 2.03, 0.56]]],
            [9, [0.133, 0.951, -0.28], 0.96, 0.487, 45.7, 0.702, 0.494, [[0.89, 2.11, 0.07], [-0.82, 1.45, 0.45], [-0.03, 2.75, -0.23], [0.47, 1.85, 0.41], [-0.87, 1.65, 0.49], [0.29, 1.57, 0.26], [1.38, 4.13, -1.29], [-0.83, 3.34, 0.63], [0.54, 3.52, -1.23]]],
        ],
        'ps_smoke': [
            [3, [0.044, 0.998, -0.036], 1.084, 0.127, 7.7, 3.959, 0.164, [[0.08, 0.77, -0.06], [0.09, 0.87, -0.06], [-0.02, 0.94, -0.06]]],
            [7, [0.087, 0.936, 0.342], 0.926, 0.184, 17.3, 3.597, 0.607, [[-0.11, 1.09, -0.04], [-0.13, 1.3, -0.02], [0.02, 1.36, 0.02], [-0.14, 1.69, 0.11], [-0.16, 1.5, 0.23], [0.13, 1.81, 0.08], [0.3, 1.56, 0.15]]],
            [5, [-0.09, 0.822, 0.562], 0.715, 0.137, 38.7, 2.647, 0.517, [[0.34, 1.46, 0.34], [0.5, 2.76, 0.2], [0.81, 2.85, 0.27], [-0.44, 2.48, 0.65], [-0.18, 2.64, 0.68]]],
            [9, [0.716, 0.549, 0.431], 0.7, 0.346, 46.4, 1.548, 0.544, [[-0.16, 2.73, 0.61], [0.94, 2.26, 1.24], [1.1, 2.65, 0.54], [0.31, 2.92, 0.06], [1.52, 2.45, 0.9], [1.85, 1.63, 0.66], [1.9, 2.69, -0.41], [3.3, 1.97, 0.34], [2.7, 2.95, -0.31]]],
            [7, [0.849, 0.157, 0.505], 0.545, 0.278, 51.7, 0.566, 0.404, [[1.31, 1.62, 1.31], [1.72, 1.41, 0.31], [3.85, 1.22, -0.01], [2.08, 2.35, -0.42], [0.11, 1.71, -0.51], [0.05, 3.97, -3.34], [0.84, -0.02, -0.45]]],
        ],
    },
    'fire_torch_brazier': {
        'ps_flame_core': [
            [11, [0.052, 0.997, -0.062], 0.919, 0.207, 11.8, 0.413, 0.137, [[0.07, 0.05, -0.01], [0.06, 0.06, 0.08], [0.02, 0.08, 0.06], [0.06, 0.09, -0.0], [-0.05, 0.12, 0.06], [-0.1, 0.12, -0.06], [0.13, 0.15, 0.01], [0.03, 0.12, 0.0], [-0.11, 0.24, 0.01], [0.02, 0.15, 0.06], [-0.02, 0.29, -0.1]]],
            [13, [-0.015, 1.0, -0.023], 1.046, 0.229, 13.5, 0.158, 0.138, [[0.07, 0.34, -0.13], [0.09, 0.21, 0.01], [0.02, 0.27, 0.01], [0.02, 0.29, -0.03], [0.02, 0.24, -0.05], [0.21, 0.51, 0.05], [0.04, 0.35, -0.07], [0.12, 0.46, 0.01], [-0.05, 0.49, 0.04], [0.04, 0.45, -0.02], [-0.04, 0.58, -0.06], [-0.08, 0.55, -0.01], [-0.21, 0.67, -0.03]]],
        ],
        'ps_flame_body': [
            [19, [0.035, 0.999, 0.02], 1.372, 0.282, 7.8, 0.733, 0.305, [[0.11, 0.18, -0.02], [-0.06, 0.18, 0.08], [-0.03, 0.23, -0.04], [-0.0, 0.2, 0.03], [-0.01, 0.24, -0.01], [0.06, 0.23, 0.01], [0.02, 0.27, 0.03], [0.03, 0.27, 0.1], [0.07, 0.34, 0.05], [0.02, 0.38, 0.07], [-0.0, 0.41, 0.13], [0.02, 0.5, 0.01], [-0.03, 0.35, -0.06], [-0.01, 0.55, 0.05], [-0.03, 0.47, 0.12], [-0.0, 0.61, 0.05], [-0.01, 0.45, 0.09], [0.01, 0.54, 0.09], [0.1, 0.58, 0.02]]],
            [14, [-0.073, 0.997, -0.01], 1.45, 0.229, 8.2, 0.386, 0.15, [[0.08, 0.54, 0.03], [-0.02, 0.5, 0.16], [0.02, 0.73, -0.1], [-0.03, 0.64, 0.03], [0.16, 0.62, 0.16], [-0.05, 0.88, -0.0], [-0.07, 0.94, 0.02], [-0.07, 0.6, -0.07], [-0.12, 0.78, -0.12], [-0.13, 0.95, -0.12], [-0.06, 1.04, -0.08], [-0.01, 0.76, -0.08], [0.07, 0.91, -0.0], [0.09, 1.01, -0.05]]],
            [15, [0.003, 1.0, 0.014], 1.302, 0.165, 7.9, 0.133, 0.185, [[-0.03, 0.82, -0.02], [0.03, 1.02, 0.08], [0.09, 0.93, -0.05], [0.07, 1.2, -0.12], [0.02, 1.24, -0.05], [-0.03, 1.33, -0.05], [0.14, 1.22, 0.04], [-0.14, 1.36, -0.25], [0.12, 1.35, 0.16], [-0.08, 1.16, -0.03], [0.04, 1.33, -0.18], [0.16, 1.63, -0.04], [-0.1, 1.15, -0.3], [0.23, 1.52, 0.03], [0.08, 1.54, -0.03]]],
        ],
        'ps_flame_tongue': [
            [10, [0.039, 0.999, -0.035], 1.875, 0.377, 9.1, 0.639, 0.25, [[0.04, 0.46, -0.01], [0.02, 0.47, 0.04], [0.05, 0.5, -0.07], [-0.0, 0.55, -0.07], [-0.03, 0.59, -0.06], [0.0, 0.6, -0.04], [0.02, 0.81, -0.04], [0.08, 0.91, -0.0], [-0.02, 0.85, -0.14], [0.01, 0.87, 0.04]]],
            [10, [-0.065, 0.996, -0.061], 1.809, 0.279, 8.5, 0.151, 0.165, [[-0.11, 0.91, 0.02], [0.04, 1.16, -0.09], [-0.13, 1.37, -0.14], [-0.12, 1.04, 0.0], [0.03, 1.1, -0.03], [0.04, 1.22, 0.05], [-0.02, 1.34, 0.18], [-0.05, 1.49, -0.04], [-0.16, 1.96, 0.34], [-0.13, 1.91, -0.26]]],
        ],
        'ps_head_glow': [
            [6, [0.162, 0.983, -0.085], 0.023, 0.018, 16.1, 0.549, 0.433, [[-0.06, 0.05, 0.08], [-0.09, 0.06, 0.06], [-0.07, 0.05, -0.08], [-0.0, 0.06, 0.07], [0.01, 0.07, -0.0], [-0.05, 0.09, -0.04]]],
        ],
        'ps_embers': [
            [16, [0.075, 0.995, 0.068], 1.97, 0.448, 17.6, 1.852, 0.792, [[-0.04, 0.71, 0.01], [-0.02, 0.82, -0.05], [0.11, 0.87, 0.04], [-0.15, 1.08, 0.12], [-0.14, 1.01, 0.07], [-0.08, 1.14, -0.09], [-0.09, 1.27, 0.11], [-0.09, 1.33, 0.03], [-0.13, 1.26, -0.13], [-0.08, 1.48, 0.09], [-0.13, 1.6, 0.15], [0.45, 1.79, 0.02], [0.3, 1.8, 0.19], [0.44, 2.01, 0.07], [0.15, 1.66, 0.5], [1.09, 2.51, -0.09]]],
            [16, [0.135, 0.973, 0.186], 0.599, 0.541, 60.0, 0.776, 0.713, [[0.6, 2.22, -0.12], [0.59, 1.67, -0.55], [0.35, 0.91, 0.11], [0.26, 1.24, 0.42], [-0.01, 2.29, -0.66], [0.2, 2.35, -1.05], [0.21, 1.39, -0.61], [0.81, 1.51, -0.55], [0.07, 2.48, 0.07], [-0.78, 0.62, 0.48], [1.42, 2.68, -0.26], [0.24, 1.69, -0.01], [0.17, 4.15, -1.38], [0.03, 1.32, 0.57], [1.7, 4.93, 0.13], [-0.81, 3.56, -0.79]]],
        ],
        'ps_smoke': [
            [6, [-0.094, 0.982, -0.161], 1.116, 0.098, 7.5, 4.175, 0.348, [[0.11, 1.24, -0.1], [-0.05, 1.31, -0.13], [-0.14, 1.37, 0.06], [-0.04, 1.5, 0.1], [-0.18, 1.57, -0.04], [-0.16, 1.6, -0.04]]],
            [10, [0.042, 0.942, -0.334], 1.183, 0.205, 10.0, 3.505, 0.585, [[-0.05, 1.66, -0.17], [-0.25, 1.8, -0.08], [-0.03, 1.9, -0.25], [-0.1, 1.9, -0.02], [0.02, 1.99, -0.42], [-0.12, 2.09, -0.22], [-0.13, 2.23, -0.22], [0.1, 2.26, -0.44], [-0.09, 2.84, -0.47], [-0.04, 2.98, -0.53]]],
            [11, [-0.096, 0.954, -0.285], 1.091, 0.312, 21.2, 2.624, 0.529, [[-0.02, 3.12, -0.65], [-0.03, 3.32, -0.45], [-0.64, 3.57, -0.63], [-0.49, 3.59, -0.34], [-1.08, 3.34, -0.68], [-0.27, 2.71, -0.62], [-0.22, 3.19, -0.92], [-1.26, 4.03, -0.63], [-0.5, 3.67, -0.45], [-0.7, 4.45, -0.07], [-0.15, 3.11, -0.99]]],
            [12, [0.028, 0.81, -0.586], 0.517, 0.251, 48.1, 1.477, 0.579, [[-1.03, 3.45, -0.78], [-0.8, 3.62, -0.63], [-1.32, 3.23, -0.63], [-1.05, 4.01, -0.79], [-0.44, 3.23, -0.92], [-0.66, 3.94, -1.14], [-0.2, 1.61, -0.47], [0.94, 3.16, -1.47], [0.2, 2.1, -1.25], [0.25, 3.17, -1.97], [0.29, 2.05, -1.49], [0.93, 4.2, -1.08]]],
            [13, [0.297, -0.053, -0.953], 0.506, 0.208, 47.8, 0.578, 0.594, [[-0.28, 2.22, -1.79], [0.38, 2.36, -1.15], [0.9, 2.75, -1.67], [0.2, 3.31, -2.03], [0.4, 2.97, -2.11], [-1.38, 3.81, -2.88], [-0.33, 2.21, -1.51], [-0.64, 2.6, -2.42], [0.23, 1.84, -1.87], [1.13, 3.04, -2.26], [-0.23, 2.74, -2.71], [0.37, 4.16, -2.09], [2.34, 4.0, -0.95]]],
        ],
    },
    'fire_torch_standing': {
        'ps_flame_core': [
            [8, [-0.17, 0.983, 0.065], 0.641, 0.154, 9.4, 0.359, 0.13, [[-0.04, 0.03, -0.07], [-0.05, 0.04, -0.04], [-0.02, 0.06, -0.07], [0.04, 0.07, -0.04], [-0.01, 0.12, 0.09], [-0.03, 0.17, 0.11], [-0.04, 0.21, 0.02], [0.02, 0.16, 0.0]]],
            [7, [-0.064, 0.975, -0.214], 0.758, 0.082, 9.2, 0.174, 0.134, [[0.02, 0.22, -0.09], [-0.1, 0.28, -0.03], [-0.07, 0.29, -0.05], [0.1, 0.29, -0.08], [0.02, 0.34, -0.07], [0.05, 0.47, -0.09], [-0.08, 0.4, -0.04]]],
        ],
        'ps_flame_body': [
            [14, [-0.068, 0.991, -0.118], 0.891, 0.142, 9.5, 0.758, 0.147, [[-0.03, 0.11, -0.05], [-0.05, 0.13, -0.05], [0.01, 0.15, -0.04], [-0.03, 0.17, -0.04], [-0.04, 0.2, -0.0], [0.05, 0.24, 0.04], [0.07, 0.23, -0.01], [-0.02, 0.22, -0.08], [0.07, 0.29, -0.05], [-0.0, 0.36, 0.03], [-0.1, 0.37, -0.0], [-0.09, 0.39, -0.03], [-0.07, 0.38, -0.07], [0.04, 0.46, -0.05]]],
            [8, [-0.16, 0.985, -0.059], 0.857, 0.098, 11.8, 0.474, 0.215, [[0.12, 0.39, -0.09], [-0.16, 0.52, 0.01], [0.02, 0.48, -0.08], [-0.15, 0.51, 0.05], [-0.06, 0.48, -0.09], [-0.13, 0.57, -0.05], [0.0, 0.67, 0.0], [-0.02, 0.8, -0.12]]],
            [8, [-0.107, 0.992, -0.068], 0.766, 0.144, 13.0, 0.167, 0.137, [[0.04, 0.69, -0.12], [-0.09, 0.5, 0.06], [-0.04, 0.69, -0.14], [-0.04, 0.8, 0.07], [-0.06, 0.71, 0.06], [-0.03, 0.66, 0.01], [-0.12, 1.01, -0.21], [-0.07, 0.92, -0.24]]],
        ],
        'ps_flame_tongue': [
            [7, [-0.01, 1.0, -0.027], 1.249, 0.323, 12.2, 0.568, 0.334, [[0.0, 0.28, -0.02], [-0.07, 0.39, -0.03], [0.06, 0.36, 0.01], [-0.06, 0.41, 0.02], [-0.05, 0.6, 0.0], [0.02, 0.57, -0.01], [0.05, 0.49, 0.02]]],
            [5, [-0.046, 0.999, 0.018], 1.204, 0.196, 11.3, 0.1, 0.128, [[0.14, 0.57, -0.06], [-0.08, 0.91, 0.05], [-0.05, 0.82, 0.03], [0.04, 0.8, -0.05], [-0.12, 0.97, -0.02]]],
        ],
        'ps_head_glow': [
            [5, [-0.174, 0.978, 0.119], 0.022, 0.014, 13.1, 0.496, 0.532, [[-0.04, 0.03, -0.0], [-0.05, 0.03, -0.05], [-0.03, 0.03, -0.07], [-0.02, 0.05, 0.02], [-0.01, 0.08, 0.0]]],
        ],
        'ps_embers': [
            [13, [-0.398, 0.905, 0.148], 1.818, 0.437, 17.3, 2.305, 1.183, [[-0.04, 0.54, 0.07], [0.03, 0.6, 0.03], [0.05, 0.67, -0.01], [-0.03, 0.79, -0.12], [-0.23, 1.02, 0.04], [-0.2, 0.89, 0.18], [-0.56, 1.31, 0.13], [-0.29, 1.43, 0.11], [-0.53, 1.46, 0.2], [-0.72, 1.61, 0.27], [-0.59, 1.94, 0.18], [-1.0, 1.66, 0.48], [-0.51, 2.25, -0.2]]],
            [22, [-0.034, 0.916, -0.401], 0.907, 0.476, 53.8, 0.794, 1.008, [[-0.54, 2.17, -0.33], [-0.27, 2.96, -1.33], [-0.42, 3.42, -1.44], [-0.04, 1.9, -0.26], [-0.02, 0.63, 1.07], [-0.64, 2.26, -2.34], [-0.96, 3.88, -0.33], [0.48, 1.11, 0.55], [-2.28, 3.49, -0.48], [-0.57, 4.21, 0.24], [-0.35, 5.42, -0.53], [-1.19, 3.02, -0.38], [1.26, 3.71, 0.98], [-1.08, 4.24, -0.31], [0.8, 2.37, -1.42], [-0.64, 4.65, -0.36], [-0.45, 5.33, -2.43], [0.92, 1.42, -1.27], [-0.24, 2.11, -0.18], [-1.84, 4.77, -2.82], [-1.28, 5.22, -1.84], [-2.03, 2.86, -4.02]]],
        ],
        'ps_smoke': [
            [3, [-0.14, 0.989, -0.044], 1.247, 0.045, 5.6, 3.926, 0.31, [[-0.08, 0.85, -0.06], [0.03, 0.97, 0.03], [-0.07, 1.09, 0.07]]],
            [8, [-0.161, 0.982, -0.096], 1.134, 0.145, 8.7, 3.668, 0.273, [[0.05, 1.12, 0.12], [-0.24, 1.33, -0.08], [-0.04, 1.32, 0.04], [-0.11, 1.73, -0.09], [-0.09, 1.66, -0.27], [-0.11, 1.98, -0.19], [-0.2, 2.11, -0.25], [-0.15, 2.03, -0.43]]],
            [6, [0.145, 0.918, -0.37], 0.68, 0.157, 25.3, 2.624, 0.464, [[-0.15, 2.42, -0.4], [-0.5, 2.51, -0.31], [0.11, 1.83, -0.66], [-0.05, 2.15, -0.41], [-0.54, 2.48, -0.22], [-0.15, 3.07, -0.08]]],
            [10, [0.688, 0.72, 0.092], 0.727, 0.161, 22.2, 1.665, 0.474, [[-0.13, 3.13, -0.29], [-0.24, 3.2, 0.03], [0.31, 3.68, 0.09], [0.75, 3.35, -0.45], [0.74, 3.84, -0.37], [1.07, 4.05, 0.09], [1.4, 4.32, -0.19], [1.91, 3.76, 0.31], [1.58, 4.33, 0.53], [1.69, 3.63, 0.98]]],
            [6, [0.819, 0.52, 0.242], 0.632, 0.388, 53.9, 0.55, 0.441, [[1.85, 3.73, 1.14], [2.22, 3.63, -1.12], [1.48, 3.82, 1.54], [1.67, 3.43, 1.91], [1.71, 3.53, 0.35], [-1.11, 0.12, -0.45]]],
        ],
    },
    'fire_torch_handheld': {
        'ps_flame_core': [
            [7, [-0.052, 0.985, -0.166], 0.552, 0.099, 8.2, 0.417, 0.228, [[0.01, 0.02, -0.02], [0.04, 0.04, -0.03], [-0.05, 0.04, 0.05], [0.03, 0.06, -0.02], [0.07, 0.11, 0.01], [0.01, 0.11, 0.0], [-0.01, 0.12, -0.08]]],
            [8, [0.005, 1.0, -0.013], 0.578, 0.083, 11.2, 0.128, 0.163, [[-0.04, 0.14, 0.03], [-0.01, 0.21, -0.03], [0.05, 0.16, 0.04], [0.01, 0.18, -0.05], [0.1, 0.22, -0.03], [-0.08, 0.27, 0.05], [0.02, 0.34, -0.01], [-0.07, 0.3, 0.08]]],
        ],
        'ps_flame_body': [
            [10, [-0.019, 0.998, -0.06], 0.738, 0.114, 6.6, 0.726, 0.301, [[-0.01, 0.1, 0.04], [-0.02, 0.11, -0.03], [0.06, 0.14, 0.01], [-0.0, 0.17, -0.03], [-0.05, 0.17, 0.01], [0.03, 0.17, -0.05], [0.04, 0.2, 0.01], [-0.04, 0.2, 0.01], [-0.05, 0.27, 0.03], [-0.02, 0.34, -0.03]]],
            [12, [0.017, 1.0, 0.011], 0.783, 0.132, 8.7, 0.443, 0.222, [[0.02, 0.24, -0.0], [-0.03, 0.34, -0.01], [-0.0, 0.29, -0.0], [-0.03, 0.43, 0.01], [0.04, 0.35, 0.05], [-0.05, 0.33, 0.04], [0.03, 0.46, -0.02], [-0.07, 0.36, 0.03], [0.14, 0.55, 0.05], [0.07, 0.54, 0.05], [0.04, 0.64, 0.03], [-0.1, 0.77, 0.04]]],
            [8, [0.121, 0.99, 0.071], 0.765, 0.1, 8.6, 0.163, 0.155, [[0.03, 0.51, -0.01], [0.08, 0.58, 0.06], [-0.06, 0.62, 0.02], [0.04, 0.69, 0.12], [0.01, 0.79, 0.04], [0.01, 0.59, 0.08], [0.14, 0.72, 0.06], [0.07, 0.77, -0.03]]],
        ],
        'ps_flame_tongue': [
            [6, [-0.009, 0.993, 0.121], 1.161, 0.214, 7.3, 0.544, 0.201, [[-0.04, 0.23, 0.0], [0.0, 0.31, -0.0], [-0.04, 0.37, 0.06], [-0.02, 0.32, 0.01], [-0.06, 0.4, -0.03], [0.02, 0.56, 0.07]]],
            [6, [0.007, 1.0, 0.008], 1.138, 0.159, 5.8, 0.217, 0.144, [[-0.02, 0.53, 0.05], [-0.04, 0.68, 0.05], [0.01, 0.81, 0.01], [0.04, 0.75, -0.06], [0.06, 0.75, -0.06], [-0.03, 0.78, -0.06]]],
        ],
        'ps_head_glow': [
            [5, [-0.118, 0.991, 0.068], 0.035, 0.023, 20.4, 0.482, 0.642, [[0.01, 0.04, 0.06], [-0.05, 0.03, 0.0], [-0.02, 0.06, -0.02], [0.0, 0.06, -0.08], [-0.01, 0.07, 0.01]]],
        ],
        'ps_embers': [
            [12, [-0.064, 0.997, 0.046], 1.886, 0.307, 18.9, 2.875, 1.494, [[0.02, 0.41, -0.03], [-0.02, 0.56, -0.06], [-0.05, 0.74, -0.03], [-0.16, 0.71, -0.08], [-0.2, 0.97, -0.12], [-0.17, 0.95, 0.0], [-0.19, 1.02, -0.08], [-0.13, 1.38, 0.25], [-0.12, 1.77, 0.12], [-0.31, 1.09, 0.35], [-0.54, 1.58, 0.73], [0.47, 1.86, -0.31]]],
            [25, [0.084, 0.784, -0.615], 0.929, 0.515, 57.9, 0.997, 1.178, [[-0.28, 1.55, -0.39], [-0.57, 1.4, -0.35], [-0.2, 1.76, -0.51], [-0.56, 3.25, -1.6], [-0.08, 1.33, -0.94], [0.71, 1.74, -0.95], [-1.25, 3.81, -0.17], [-0.76, 1.74, -1.66], [0.43, 2.45, -1.23], [-0.51, 4.53, -1.12], [-3.22, 4.27, 0.17], [1.46, 0.29, -1.52], [1.34, 3.29, -2.35], [0.26, 1.51, 0.57], [2.17, 2.71, -4.13], [4.25, 1.1, -2.16], [0.84, 3.0, -1.62], [1.46, 3.91, -3.97], [1.69, 4.08, -1.83], [1.41, 3.44, -0.23], [1.14, -0.5, -1.97], [1.19, -2.02, 0.41], [-1.32, 5.7, -1.35], [-3.86, -1.56, -3.16], [-3.28, 1.31, -3.44]]],
        ],
        'ps_smoke': [
            [1, [-0.19, 0.975, 0.117], 1.183, 0.0, 0.0, 4.34, 0.0, [[-0.0, 0.84, 0.02]]],
            [3, [-0.337, 0.758, -0.558], 1.191, 0.11, 18.0, 3.743, 0.3, [[-0.17, 1.07, -0.07], [-0.23, 1.5, -0.41], [-0.32, 1.59, -0.76]]],
            [3, [-0.936, 0.245, -0.252], 1.243, 0.157, 14.4, 2.744, 0.677, [[-0.87, 1.46, -1.05], [-1.7, 1.73, -0.8], [-1.54, 1.88, -0.91]]],
            [3, [-0.6, 0.693, 0.401], 0.593, 0.106, 42.4, 1.39, 0.247, [[-0.56, 3.07, -0.61], [-0.86, 1.5, -0.93], [0.52, 2.61, -0.55]]],
            [3, [-0.089, 0.913, 0.397], 0.701, 0.509, 47.3, 0.61, 0.262, [[1.05, 4.03, -1.4], [0.43, 3.04, -1.57], [1.1, 4.71, 0.67]]],
        ],
    },
}
# --- PREFILL END ----------------------------------------------------------------------------------------------


def r4(x: float) -> float:
    return round(x, 4)


def node(nid: str, ntype: str, params: dict[str, Any], inputs: dict[str, Any] | None = None,
         parent: str | None = None, layer: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        entry["layer"] = layer
    if parent:
        entry["parent"] = parent
    entry["parameters"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def g(nid: str, op: str, params: dict[str, Any] | None = None, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "op": op}
    if params:
        entry["params"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def texture(nid: str, width: int, height: int, ops: list[dict[str, Any]], frames: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"width": width, "height": height}
    if frames:
        params["frames"] = frames
    params["graph"] = {"nodes": ops, "output": ops[-1]["id"]}
    return node(nid, "texture", params)


def harmonics(terms: list[tuple[float, int, float]], t: float) -> float:
    return sum(a * math.sin(2.0 * math.pi * k * t / DURATION + p) for a, k, p in terms)


def smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def gust_env(t: float) -> float:
    """The wind-gust envelope of the loop: 0 at idle, up to 1 at the peak of the strong gust.

    Each gust is a smooth pulse - a fast rise (the wind catches the flame) and a slower fall (it settles).
    The pulses are evaluated on the circle t mod DURATION, so the envelope is exactly periodic and the loop
    starts and ends in the calm ESTABLISH state.
    """
    out = 0.0
    for centre, amp, rise, fall in GUSTS:
        d = (t - centre) % DURATION
        if d > DURATION * 0.5:
            d -= DURATION
        if -rise <= d <= 0.0:
            out = max(out, amp * smoothstep(1.0 + d / rise))
        elif 0.0 < d <= fall:
            out = max(out, amp * smoothstep(1.0 - d / fall))
    return out


def track(fn: Any, step: float = 0.25) -> list[dict[str, float]]:
    """Sample a periodic function of loop time into a keyframe track (first and last key agree)."""
    n = int(round(DURATION / step))
    return [{"time": r4(i * step), "value": r4(fn(i * step))} for i in range(n + 1)]


def breathe(terms: list[tuple[float, int, float]], base: float, gust: float = 0.0, step: float = 0.25) -> list[dict[str, float]]:
    """base * (1 + harmonics) * (1 + gust * gust_env), clamped at 0 and exactly periodic."""
    return track(lambda t: base * max(0.0, 1.0 + harmonics(terms, t)) * (1.0 + gust * gust_env(t)), step)


def animated(keys: list[dict[str, Any]]) -> dict[str, Any]:
    return {"value": keys[0]["value"], "track": keys}


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


def build(slug: str, spec: dict[str, Any], prefill: bool = True) -> dict[str, Any]:
    w = spec["w"]                            # flame width multiplier
    h = spec["h"]                            # flame height multiplier
    fr = spec["rate"]
    lean = spec["lean"]
    flean = spec["flame_lean"]
    head_r = r4(HEAD_RADIUS * w)
    # where the flame ends and the smoke leaves it (the wall torch's flame is about 0.85 m tall)
    tip = 0.82 * h
    cs = 0.28 + 0.72 * h                     # camera framing scale (a brazier still reads as bigger)
    # A wider, busier, more strongly leaning variant stacks far more additive sprites over the same
    # pixels, so its white-hot core clips into a formless blob unless every flame sprite is drawn
    # fainter. How much fainter follows no tidy law - additive clipping is very non-linear in the number
    # of overlaps - so `expose` is MEASURED per variant instead: out/work/fire_torch/hot.py reports the
    # fraction of clipped pixels in a capture, and each variant was tuned until it matched the wall
    # torch, which is the one balanced by eye.
    dens = spec["expose"]

    nodes: list[dict[str, Any]] = [
        # framed like the sheet: the torch head low in the frame, flame and smoke above, 3/4 view. The
        # camera pulls back with the flame but less than proportionally, so a brazier reads as bigger.
        node("cam", "camera", {"position": [r4(1.32 * cs), r4(1.2 * cs), r4(3.35 * cs)],
                               "target": [0.0, r4(0.62 * cs), 0.0], "fov": 34.0}),
        # the attachment point (the top of the torch head) and the wind rigs under it
        node("torch_head", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False}),
        node("wind_heading", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                      "rotation": [0.0, 1.0, 0.0]}, parent="torch_head"),
    ]
    # FLICKER: the idle lean breathes on 12, 4 and 1.7 s harmonics with a lateral sway on 6 and 2.4 s;
    # GUST/SETTLE: gust_env adds the big lean over the top and decays it away again.
    wobble = [(0.2, 1, 0.4), (0.1, 3, 1.9), (0.05, 7, 0.3)]
    sway = [(0.34, 2, 1.1), (0.2, 5, 2.5)]
    for rid, tilt, gust_tilt, k in (("wind_gust", WIND_TILT, GUST_WIND_TILT, lean),
                                    ("wind_flame", FLAME_TILT, GUST_FLAME_TILT, flean)):
        keys = []
        for i in range(49):
            t = i * 0.25
            base = tilt * k
            amount = base * (1.0 + harmonics(wobble, t)) + gust_tilt * k ** 0.6 * gust_env(t)
            keys.append({"time": r4(t), "value": [r4(base * harmonics(sway, t)), 0.0, r4(-amount)]})
        nodes.append(node(rid, "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                        "rotation": animated(keys)}, parent="wind_heading"))

    # ---------------------------------------------------------------- textures ---
    # the one fire flipbook (shared by every flame system and every variant). `fire_sim` bakes the root at
    # image row 0 and both renderers draw row 0 at the TOP of a sprite, so the sheet is mirrored in V with a
    # `distort` (the Fire AOE workaround); the same remap crops the bright fuel slab under the tongues.
    # Tuned narrower and more buoyant than the Campfire's: a torch necks into a thin vertical lick.
    slab, top = 0.36, 0.995
    s0 = 0.5 + top / 2.0
    s1 = s0 - (1.0 + top - slab) / 2.0
    nodes += [
        texture("tex_flame", 80, 144, [
            g("sim", "fire_sim", {"seed": 19, "fuel": 1.25, "fuel_width": 0.44, "buoyancy": 3.6,
                                  "turbulence": 2.0, "turbulence_scale": 3.4, "cooling": 1.75, "detail": 4,
                                  "speed": 0.06, "substeps": 4, "loop": True, "flicker": 0.34,
                                  "sharpness": 1.4}),
            g("half", "constant", {"color": [0.5, 0.5, 0.5, 1.0]}),
            g("vmap", "gradient_linear", {"angle": 90, "start": r4(s0), "end": r4(s1)}),
            g("byv", "channel_pack", {"channel": "luminance"}, {"r": "half", "g": "vmap", "b": "half"}),
            g("flip", "distort", {"amount": 2.0}, {"a": "sim", "by": "byv"}),
            # fade the sides, the root and the very top so no straight quad edge can show
            g("gu", "gradient_linear", {"angle": 0, "start": 0.0, "end": 1.0}),
            g("gui", "invert", None, {"a": "gu"}),
            g("par", "math", {"mode": "multiply"}, {"a": "gu", "b": "gui"}),
            g("side", "levels", {"in_high": 0.1, "gamma": 0.8}, {"a": "par"}),
            g("m1", "math", {"mode": "multiply"}, {"a": "flip", "b": "side"}),
            g("gv", "gradient_linear", {"angle": 90, "start": 1.0, "end": 0.0}),
            g("root", "levels", {"in_high": 0.36}, {"a": "gv"}),
            g("m2", "math", {"mode": "multiply"}, {"a": "m1", "b": "root"}),
            g("gvi", "invert", None, {"a": "gv"}),
            g("tip", "levels", {"in_high": 0.07}, {"a": "gvi"}),
            g("m3", "math", {"mode": "multiply"}, {"a": "m2", "b": "tip"}),
            g("lv", "levels", {"in_low": 0.1, "in_high": 0.76, "gamma": 0.92}, {"a": "m3"}),
        ], frames=24),
        # the Chimney Smoke puff: an irregular warped silhouette with billowing fbm inside, churning over life
        texture("tex_puff", 80, 80, [
            g("w1", "fbm", {"frequency": 1.8, "octaves": 2, "seed": 11, "animate": 0.5}),
            g("w2", "fbm", {"frequency": 1.8, "octaves": 2, "seed": 29, "animate": 0.5}),
            g("w1l", "levels", {"in_low": 0.25, "in_high": 0.75}, {"a": "w1"}),
            g("w2l", "levels", {"in_low": 0.25, "in_high": 0.75}, {"a": "w2"}),
            g("wp", "channel_pack", {"channel": "luminance"}, {"r": "w1l", "g": "w2l"}),
            g("rd", "gradient_radial", {"radius": 0.47, "falloff": "smooth"}),
            g("shape", "distort", {"amount": 0.1}, {"a": "rd", "by": "wp"}),
            g("n", "fbm", {"frequency": 2.1, "octaves": 5, "gain": 0.55, "seed": 3, "animate": 0.7}),
            g("nw", "distort", {"amount": 0.07}, {"a": "n", "by": "wp"}),
            g("nl", "levels", {"in_low": 0.2, "in_high": 0.8, "out_low": 0.3}, {"a": "nw"}),
            g("cells", "worley", {"frequency": 3.2, "octaves": 2, "seed": 8, "animate": 0.4}),
            g("lobes", "invert", None, {"a": "cells"}),
            g("lobes_l", "levels", {"in_low": 0.3, "in_high": 1.0}, {"a": "lobes"}),
            g("lobes_w", "distort", {"amount": 0.05}, {"a": "lobes_l", "by": "wp"}),
            g("detail", "math", {"mode": "lerp", "factor": 0.45}, {"a": "nl", "b": "lobes_w"}),
            g("m", "math", {"mode": "multiply"}, {"a": "shape", "b": "detail"}),
            g("alpha", "levels", {"in_low": 0.12, "in_high": 0.78, "gamma": 1.2}, {"a": "m"}),
            g("shade", "levels", {"in_low": 0.0, "in_high": 0.6, "out_low": 0.72}, {"a": "m"}),
            g("out", "channel_pack", {"channel": "luminance"},
              {"r": "shade", "g": "shade", "b": "shade", "a": "alpha"}),
        ], frames=12),
        texture("tex_erode", 64, 64, [
            g("a", "fbm", {"frequency": 3.0, "octaves": 4, "gain": 0.5, "seed": 4407, "tile": True}),
            g("b", "blur", {"radius": 1.5}, {"a": "a"}),
            g("l", "levels", {"in_low": 0.22, "in_high": 0.78}, {"a": "b"}),
        ]),
        # soft living hot glow for the head and the warm air around the flame
        texture("tex_halo", 64, 64, [
            g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
            g("s", "levels", {"gamma": 2.4}, {"a": "r"}),
            g("n", "fbm", {"frequency": 1.6, "octaves": 3, "seed": 77, "animate": 0.35}),
            g("nl", "levels", {"in_low": 0.25, "in_high": 0.8, "out_low": 0.55}, {"a": "n"}),
            g("m", "math", {"mode": "multiply"}, {"a": "s", "b": "nl"}),
        ], frames=8),
        texture("tex_glow", 64, 64, [
            g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
            g("n", "fbm", {"frequency": 2.5, "octaves": 3, "seed": 31, "animate": 0.8}),
            g("nl", "levels", {"in_low": 0.2, "in_high": 0.8, "out_low": 0.45}, {"a": "n"}),
            g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
            g("l", "levels", {"in_low": 0.02, "in_high": 0.75}, {"a": "m"}),
        ], frames=8),
        texture("tex_spark", 32, 32, [
            g("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        ]),
    ]

    # ------------------------------------------------------- materials, forces ---
    nodes += [
        node("mat_fire", "material", {
            "blend": "additive", "emissive_intensity": 0.95, "soft_particle": True, "depth_fade": 0.12,
            "dissolve": 0.18, "erosion": 0.1, "temperature_gradient": FIRE_RAMP,
        }, {"noise_texture": "tex_erode"}),
        node("mat_glow", "material", {
            "blend": "additive", "shading": "unlit", "emissive_color": HEAD_COLOR, "emissive_intensity": 0.0,
            "soft_particle": True, "depth_fade": 0.2,
        }),
        node("mat_smoke", "material", {
            "blend": "alpha", "shading": "lit", "base_color": [1.0, 1.0, 1.0, 1.0],
            "emissive_color": [0.6, 0.62, 0.7, 1.0], "soft_particle": True, "depth_fade": 0.3,
        }, {"noise_texture": "tex_erode"}),
        # a torch necks upward hard: strong buoyancy over a small root
        node("f_buoy_fire", "force", {"force_type": "buoyancy", "strength": r4(2.6 * h / FLAME_SLOW ** 2)}),
        node("f_curl_fire", "force", {"force_type": "curl_noise", "strength": r4(1.0 * w / FLAME_SLOW ** 2),
                                      "frequency": 2.0, "octaves": 3, "speed": r4(1.5 / FLAME_SLOW)}),
        node("f_buoy_ember", "force", {"force_type": "buoyancy", "strength": 0.5}),
        node("f_curl_ember", "force", {"force_type": "curl_noise", "strength": 1.5, "frequency": 0.9,
                                       "octaves": 2, "speed": 0.65}),
        node("f_cool", "force", {"force_type": "directional", "direction": [0.0, -1.0, 0.0], "strength": 0.04}),
        node("f_billow", "force", {"force_type": "curl_noise", "strength": 0.2, "frequency": 0.24, "octaves": 2,
                                   "speed": 0.22}),
        node("f_curl_smoke", "force", {"force_type": "curl_noise", "strength": 0.24, "frequency": 0.95,
                                       "octaves": 2, "speed": 0.45}),
    ]

    # ------------------------------------------------------------ systems ---
    systems: dict[str, dict[str, Any]] = {}       # id -> the spec the pre-fill bands copy
    emitters: dict[str, str] = {}                 # system id -> running emitter id

    def flame_system(sid: str, cap: int, life: float, life_var: float, size: float, size_var: float,
                     size_curve: list[list[Any]], opacity: float, opacity_curve: list[list[Any]],
                     stretch: float, fps: float, emissive: float, drag: float,
                     tint: list[float]) -> dict[str, Any]:
        life, life_var, fps, drag, stretch = (life * FLAME_SLOW, life_var * FLAME_SLOW, fps / FLAME_SLOW,
                                              drag / FLAME_SLOW, stretch * FLAME_SLOW)
        params = {
            "max_particles": cap, "lifetime": r4(life), "lifetime_variance": r4(life_var),
            "size": r4(size), "size_variance": r4(size_var), "size_over_life": size_curve,
            "color": tint, "color_over_life": FLAME_COLOR_LIFE,
            "opacity": opacity, "opacity_over_life": opacity_curve,
            "emissive": emissive, "drag": r4(drag), "blend": "additive", "soft_particle_distance": 0.18,
            "render_mode": "stretched_billboard", "velocity_stretch": r4(stretch), "sprite_fps": r4(fps),
        }
        entry = node(sid, "particle_system", params,
                     {"sprite": "tex_flame", "material": "mat_fire", "forces": ["f_buoy_fire", "f_curl_fire"]},
                     layer="flames")
        systems[sid] = entry
        return entry

    def emitter(eid: str, sid: str, parent: str, layer: str, rate: dict[str, Any] | float,
                **params: Any) -> dict[str, Any]:
        p = {**params, "rate": rate, "start_time": 0.0, "duration": -1.0}
        emitters[sid] = eid
        return node(eid, "emitter", p, {"particle": sid}, parent=parent, layer=layer)

    # a variant whose flame already rakes over in the gust gets a smaller launch/rate boost on top of it,
    # or the raked column stacks its own sprites and the white core clips
    gk = 1.0 / flean

    def launch(base: float, gust: float) -> dict[str, Any]:
        """An emitter launch speed that rises with the gust, so the flame leans AND stretches."""
        return animated(track(lambda t: base * (1.0 + gust * gk * gust_env(t))))

    # the flame rate breathes on its own harmonics, so the torch swells and settles over the loop
    breath = [(0.17, 1, 0.9), (0.13, 3, 2.3), (0.1, 5, 0.4), (0.08, 11, 1.7)]
    tongue_breath = [(0.3, 2, 1.4), (0.2, 5, 0.3), (0.16, 9, 2.2)]
    core_breath = [(0.14, 2, 0.2), (0.1, 7, 2.9), (0.07, 13, 1.1)]

    # CORE: the short white-hot root sitting in the torch head
    nodes.append(flame_system("ps_flame_core", 110, 0.34, 0.1, 0.34 * w, 0.11 * w, CORE_SIZE, r4(0.44 * dens),
                              CORE_OPACITY, 0.1, 26.0, 1.9, 2.8, CORE_TINT))
    nodes.append(emitter("e_flame_core", "ps_flame_core", "wind_flame", "flames",
                         animated(breathe(core_breath, 38.0 * fr / FLAME_SLOW, gust=0.15 * gk)),
                         shape="disc", radius=r4(head_r * 0.9), position=[0.0, r4(0.015 * h), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=15.0,
                         velocity=launch(r4(0.8 * h / FLAME_SLOW), 0.18),
                         velocity_variance=r4(0.3 * h / FLAME_SLOW)))
    # BODY: the upward-licking column - the readable silhouette of the torch flame
    nodes.append(flame_system("ps_flame_body", 170, 0.56, 0.2, 0.6 * w, 0.19 * w, BODY_SIZE, r4(0.46 * dens),
                              BODY_OPACITY, 0.11, 22.0, 1.3, 2.6, BODY_TINT))
    nodes.append(emitter("e_flame_body", "ps_flame_body", "wind_flame", "flames",
                         animated(breathe(breath, 42.0 * fr / FLAME_SLOW, gust=0.18 * gk)),
                         shape="disc", radius=r4(head_r * 0.8), position=[0.0, r4(0.08 * h), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=13.0,
                         velocity=launch(r4(1.25 * h / FLAME_SLOW), 0.2),
                         velocity_variance=r4(0.45 * h / FLAME_SLOW)))
    # TONGUES: the tall thin licks that tear off the top and curl over into deep red
    nodes.append(flame_system("ps_flame_tongue", 110, 0.46, 0.17, 0.5 * w, 0.17 * w, TONGUE_SIZE, r4(0.34 * dens),
                              TONGUE_OPACITY, 0.2, 20.0, 1.2, 1.8, TONGUE_TINT))
    nodes.append(emitter("e_flame_tongue", "ps_flame_tongue", "wind_flame", "flames",
                         animated(breathe(tongue_breath, 21.0 * fr / FLAME_SLOW, gust=0.3 * gk)),
                         shape="disc", radius=r4(head_r * 0.62), position=[0.0, r4(0.2 * h), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=17.0,
                         velocity=launch(r4(1.7 * h / FLAME_SLOW), 0.26),
                         velocity_variance=r4(0.7 * h / FLAME_SLOW)))

    # HEAD: the faint warm glow of the burning fuel in the torch head (no ground decal, no coal bed)
    head = spec["head"]
    systems["ps_head_glow"] = node("ps_head_glow", "particle_system", {
        "max_particles": 26, "lifetime": 1.2, "lifetime_variance": 0.4, "size": r4(0.2 * w),
        "size_variance": r4(0.06 * w), "size_over_life": GLOW_SIZE, "color": HEAD_COLOR,
        "opacity": r4(0.05 * head * dens), "opacity_over_life": GLOW_OPACITY,
        "emissive": 0.8, "drag": 1.6, "rotation_variance": 180.0, "angular_velocity_variance": 30.0,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.12, "sprite_fps": 8.0,
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer="head")
    nodes.append(systems["ps_head_glow"])
    nodes.append(emitter("e_head_glow", "ps_head_glow", "torch_head", "head",
                         animated(breathe([(0.16, 2, 0.6), (0.11, 7, 1.9)], 4.5)),
                         shape="disc", radius=r4(head_r * 0.8), position=[0.0, r4(0.02 * h), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=30.0, velocity=0.08, velocity_variance=0.05))
    # the warm air around the flame: one big soft quad that lives the whole loop
    nodes.append(node("ps_halo", "particle_system", {
        "max_particles": 2, "lifetime": DURATION, "size": r4(1.5 * h + 0.5 * w),
        "size_over_life": [[0.0, 1.0], [0.25, 1.05], [0.5, 0.96], [0.75, 1.04], [1.0, 1.0]],
        "color": HEAD_COLOR, "opacity": r4(0.008 + 0.005 * min(h, 1.9)),
        "opacity_over_life": [[0.0, 1.0], [0.2, 0.85], [0.4, 1.0], [0.6, 0.9], [0.8, 1.05], [1.0, 1.0]],
        "emissive": 0.6, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.4,
        "sprite_fps": 2.0,
    }, {"sprite": "tex_halo", "material": "mat_glow"}, layer="light"))
    nodes.append(node("e_halo", "emitter", {
        "shape": "point", "position": [0.0, r4(0.34 * h), 0.0], "velocity": 0.0, "rate": 0.0,
        "burst_count": 1, "burst_times": [0.0], "start_time": 0.0, "duration": 0.05,
    }, {"particle": "ps_halo"}, parent="torch_head", layer="light"))

    # EMBERS: small sparks torn out of the flame, drifting and cooling; a gust rips off a shower of them
    drift = spec["drift"]
    systems["ps_embers"] = node("ps_embers", "particle_system", {
        "max_particles": 150, "lifetime": r4(2.1 * drift), "lifetime_variance": r4(0.95 * drift),
        "size": r4(0.022 * (0.6 + 0.4 * w)), "size_variance": 0.009,
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": EMBER_LIFE_COLOR,
        "opacity": 1.0, "opacity_over_life": EMBER_OPACITY, "emissive": 6.0,
        "emissive_over_life": EMBER_EMISSIVE, "drag": r4(0.95 / drift),
        "render_mode": "stretched_billboard", "velocity_stretch": 0.35, "blend": "additive",
        "soft_particle_distance": 0.02,
    }, {"sprite": "tex_spark", "material": "mat_glow", "forces": ["f_buoy_ember", "f_curl_ember"]},
        layer="embers")
    nodes.append(systems["ps_embers"])
    nodes.append(emitter("e_embers", "ps_embers", "wind_gust", "embers",
                         animated(breathe([(0.3, 2, 0.1), (0.22, 5, 1.2), (0.18, 9, 2.8)],
                                          10.5 * spec["embers"], gust=1.7)),
                         shape="disc", radius=r4(head_r * 0.9), position=[0.0, r4(tip * 0.45), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=20.0,
                         velocity=launch(r4(1.25 + 0.5 * h), 0.5), velocity_variance=0.8))

    # SMOKE: a thin lit wisp leaving the flame TIP, bending with the wind and dissipating
    smoke = spec["smoke"]
    smoke_tint = [[0.0, [0.34, 0.32, 0.3, 1.0]], [0.35, [0.5, 0.49, 0.49, 1.0]],
                  [1.0, [0.66, 0.67, 0.7, 1.0]]]
    systems["ps_smoke"] = node("ps_smoke", "particle_system", {
        "max_particles": 60, "lifetime": SMOKE_LIFE, "lifetime_variance": 0.5,
        "size": r4((0.2 + 0.14 * h) * (0.7 + 0.3 * w)), "size_variance": 0.07, "size_over_life": SMOKE_SIZE,
        "color": [0.78, 0.77, 0.76, 1.0], "color_over_life": smoke_tint,
        "opacity": r4(SMOKE_ALPHA * min(1.0, 0.5 + 0.5 * smoke)), "opacity_over_life": SMOKE_OPACITY,
        "emissive": 0.22, "drag": 0.25, "rotation_variance": 180.0, "angular_velocity_variance": 16.0,
        "render_mode": "billboard", "blend": "alpha", "sort": True, "soft_particle_distance": 0.3,
    }, {"sprite": "tex_puff", "material": "mat_smoke", "forces": ["f_cool", "f_billow", "f_curl_smoke"]},
        layer="smoke")
    nodes.append(systems["ps_smoke"])
    nodes.append(emitter("e_smoke", "ps_smoke", "wind_gust", "smoke",
                         animated(breathe([(0.22, 2, 0.7), (0.13, 5, 2.1)], SMOKE_RATE * smoke, gust=0.45)),
                         shape="disc", radius=r4(0.07 + 0.06 * w), position=[0.0, r4(tip * 0.78), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=7.0, velocity=SMOKE_LAUNCH,
                         velocity_variance=0.22))

    # ----------------------------------------------------------- pre-fill ---
    band_emitters: list[str] = []
    band_systems: dict[str, list[str]] = {}
    table = PREFILL.get(slug, {}) if prefill else {}
    for sid, rows in table.items():
        base = systems[sid]
        running = next(n for n in nodes if n["id"] == emitters[sid])
        bp = base["parameters"]
        life = bp["lifetime"]
        for i, row in enumerate(rows):
            if row is None:                      # nothing of that age in this variant: keep the ids, spawn none
                row = [0, [0.0, 1.0, 0.0], 0.0, 0.0, 0.0, r4(life * 0.5), 0.0, [[0.0, 0.1, 0.0]]]
            count, direction, speed, speed_var, spread, remaining, remaining_var, points = row
            start = max(0.0, 1.0 - remaining / life)
            k = f"{sid[3:]}_pre{i + 1}"
            params = dict(bp)
            params["max_particles"] = max(4, int(count * 2 + 6))
            params["lifetime"] = r4(max(0.05, remaining))
            params["lifetime_variance"] = r4(min(remaining_var, remaining * 0.8))
            if sid.startswith("ps_flame_"):
                # a band's sprites are all born at age 0, so they all play the SAME flipbook frame and their
                # hot cores line up and add far brighter (and paler) than the running fire's staggered
                # frames: draw them fainter and keep less of their HDR emissive
                params["opacity"] = r4(bp["opacity"] * BAND_FLAME_OPACITY)
                params["emissive"] = r4(bp["emissive"] * BAND_FLAME_EMISSIVE)
                # and run their flipbook at double rate so they spread across the sheet (and past each
                # other) within a few frames instead of all showing the same fat white-hot root frame
                params["sprite_fps"] = r4(bp["sprite_fps"] * 2.2)
            for curve in ("size_over_life", "opacity_over_life", "color_over_life", "emissive_over_life"):
                if curve in bp:
                    params[curve] = tail(bp[curve], start)
            nodes.append(node(f"ps_{k}", "particle_system", params, dict(base["inputs"]), layer=base.get("layer")))
            if len(points) == 1:
                points = [points[0], [points[0][0], points[0][1] + 0.02, points[0][2]]]
            nodes.append(node(f"path_{k}", "curve", {"points": points, "curve_type": "linear",
                                                     "segments": max(8, 2 * len(points))}))
            # A band is spawned as three bursts a few frames apart instead of one: particles born at the
            # same instant all sit on the same flipbook frame and stack into one pale smear, and 0.05 s of
            # stagger is enough to desynchronise them while staying invisible at the seam.
            sub = max(1, (count + 2) // 3) if count else 0
            nodes.append(node(f"e_{k}", "emitter", {
                "shape": "curve", "direction": direction, "spread": spread, "velocity": speed,
                "velocity_variance": speed_var, "rate": 0.0, "burst_count": sub,
                "burst_times": [0.0, 0.05, 0.1], "start_time": 0.0, "duration": 0.15,
            }, {"particle": f"ps_{k}", "shape_curve": f"path_{k}"}, parent=running.get("parent"),
                layer=running.get("layer")))
            band_emitters.append(f"e_{k}")
            band_systems.setdefault(sid, []).append(f"ps_{k}")

    # -------------------------------------------------------------- light ---
    lt = spec["light"]
    flick = [(0.11, 1, 0.2), (0.09, 3, 1.3), (0.07, 7, 2.2), (0.06, 13, 0.5), (0.05, 23, 1.9),
             (0.04, 37, 2.7)]
    nodes.append(node("l_fire", "light", {
        "light_type": "point", "position": [0.0, r4(0.42 * h), 0.0], "color": FIRE_LIGHT,
        "intensity": animated(breathe(flick, 1.9 * lt, gust=0.28, step=0.125)),
        "radius": r4(4.5 + 2.2 * min(lt, 1.9)),
        "flicker_amplitude": 0.17, "flicker_frequency": 8.0, "cast_shadows": True,
    }, parent="torch_head", layer="light"))

    # ------------------------------------------------------------ controls ---
    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit_label: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit_label, "bindings": bindings}

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    flame_sys = ["ps_flame_core", "ps_flame_body", "ps_flame_tongue"]
    flame_all = [s for base in flame_sys for s in [base] + band_systems.get(base, [])]
    flame_em = ["e_flame_core", "e_flame_body", "e_flame_tongue"]
    flame_bands = [e for e in band_emitters if e.startswith("e_flame_")]
    ember_em = ["e_embers"] + [e for e in band_emitters if e.startswith("e_embers_")]
    smoke_all = ["ps_smoke"] + band_systems.get("ps_smoke", [])
    smoke_bands = [e for e in band_emitters if e.startswith("e_smoke_")]
    head_all = ["ps_head_glow"] + band_systems.get("ps_head_glow", [])

    def amount(emitter_ids: list[str]) -> list[dict[str, Any]]:
        return [mul(e, "burst_count" if e in band_emitters else "rate") for e in emitter_ids]

    controls = [
        control("fire_intensity", "Fire intensity", "Global", 0.0, 3.0,
                amount(flame_em + flame_bands + ember_em)
                + [mul("l_fire", "intensity"), mul("ps_halo", "opacity")]
                + [mul(s, "opacity") for s in head_all]),
        control("flame_height", "Flame height", "Flames", 0.3, 2.0,
                [mul(s, "size") for s in flame_all]
                + [mul(e, "velocity") for e in flame_em + flame_bands] + [mul("f_buoy_fire", "strength")]),
        control("ember_amount", "Ember amount", "Embers", 0.0, 4.0, amount(ember_em)),
        control("smoke_density", "Smoke density", "Smoke", 0.0, 3.0,
                amount(["e_smoke"] + smoke_bands) + [mul(s, "opacity") for s in smoke_all]),
        control("wind_strength", "Wind strength", "Wind", 0.0, 3.0,
                [mul("wind_gust", "rotation"), mul("wind_flame", "rotation")]),
        control("wind_direction", "Wind direction", "Wind", -180.0, 180.0, [mul("wind_heading", "rotation")],
                "deg", 1.0, 1.0),
        control("light_brightness", "Light brightness", "Light", 0.0, 3.0,
                [mul("l_fire", "intensity"), mul("l_fire", "radius")]),
        control("global_speed", "Speed", "Global", 0.25, 3.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}], step=0.05),
    ]

    variant = spec["variant"]
    hand = (" A handheld torch is parented to the hand socket, so the flame leans with the bearer."
            if variant == "handheld" else "")
    return {
        "schema_version": "0.1.0",
        "name": spec["name"],
        "description": f"Always-on fire torch ({variant}): {spec['blurb']}.",
        "duration": DURATION,
        "seed": spec["seed"],
        "timeline": {"phases": [{"name": "always_on", "start": 0.0, "end": DURATION}]},
        "layers": [{"id": "head", "name": "Head", "role": "ambient"},
                   {"id": "flames", "name": "Flames", "role": "ambient"},
                   {"id": "embers", "name": "Embers", "role": "ambient"},
                   {"id": "smoke", "name": "Smoke", "role": "ambient"},
                   {"id": "light", "name": "Light", "role": "ambient"}],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/fire_torch.py",
            "variant": slug,
            "style": variant,
            "category": "ambient",
            "loop": {"start": 0.0, "end": DURATION},
            "attachment": {
                "origin_node": "torch_head",
                "notes": "Attach the `torch_head` node at the TOP of the torch head (the mouth of the bowl), "
                         "y = 0 there and the flame rising above it; the effect draws no torch, bracket, "
                         "pole, bowl or hand." + hand + " The four Fire Torch variants share every node id "
                         "and control, so a game can swap them freely. Feed the global wind into Wind "
                         "strength (0 calm, 1 light, 3 strong) and Wind direction (degrees around Y). It is "
                         "always on and loops seamlessly; scrub Fire intensity up from 0 to light it.",
            },
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_plane": False,
                                "ground_albedo": 0.06, "grid": False, "bloom_intensity": 0.2,
                                "bloom_radius": 0.05, "exposure": 1.0},
            "tags": ["ambient", "environment", "fire", "torch", variant],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write the four effect JSON files into")
    parser.add_argument("--only", default="", help="comma-separated slugs to write (default: all four)")
    parser.add_argument("--no-prefill", action="store_true", help="leave the pre-fill bands out (for measuring)")
    parser.add_argument("--check", help="directory whose files must match the generator output byte for byte")
    args = parser.parse_args()
    only = {s for s in args.only.split(",") if s}
    if args.check:
        bad = 0
        for slug, spec in VARIANTS.items():
            path = Path(args.check) / f"{slug}.json"
            same = path.is_file() and path.read_text(encoding="utf-8") == render(build(slug, spec))
            bad += 0 if same else 1
            print(f"{path}: {'identical' if same else 'DIFFERENT'}")
        return 1 if bad else 0
    out = Path(args.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    for slug, spec in VARIANTS.items():
        if only and slug not in only:
            continue
        effect = build(slug, spec, prefill=not args.no_prefill)
        path = out / f"{slug}.json"
        path.write_text(render(effect), encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
