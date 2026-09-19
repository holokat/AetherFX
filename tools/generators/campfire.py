#!/usr/bin/env python3
"""Generate the five Campfire effects (always-on, one per intensity state) from one parametrised description.

    python tools/generators/campfire.py --out examples/effects
    python tools/generators/campfire.py --check examples/effects

A game places the `fire_pit` node at the centre of its fire pit on the ground (y = 0); the effect draws no
logs, stones or pit, only the fire, the glowing coal bed, the embers, the smoke and the light.

* COALS    a glowing, cracked coal-bed decal on the ground that breathes, and low hot-glow puffs that shimmer
           on it (the white-yellow base the flames grow out of).
* FLAMES   ONE shared `fire_sim` flipbook (a real 2D flame simulation: tongues that neck, split and tear
           off, ragged eroded edges) played per particle on big overlapping stretched sprites, coloured by a
           temperature ramp: white-yellow at the root, orange body, deep red tips. Three systems: the body
           (a broad mass rooted over the whole bed), tongues (taller, faster licks from the middle that give
           the ragged, varying top) and licks (small short flames around the edge of the bed).
* EMBERS   small hot sparks thrown up out of the flames that drift on curl noise, lean with the wind and
           cool from yellow to red as they rise (1-4 s).
* SMOKE    the Chimney Smoke puff (lit, alpha-blended, churning flipbook), darker near the fire, rising from
           the flame tips, leaning and bending with the gusting wind and thinning into haze (2-6 s).
* LIGHT    a warm point light over the bed with a layered natural flicker (slow breathing + gusts + fast
           flicker), and one faint big glow quad for the warm air around the fire.
* WIND     the Chimney Smoke rig: particles keep the horizontal speed they are launched with, so the wind is
           the lean of the launch direction. Emitters sit under invisible rig nodes: `wind_gust` tilts smoke
           and embers by the wind strength and breathes in gusts, `wind_flame` leans the flames less, and
           `wind_heading` turns everything to the wind direction.

The five states share every node id, the loop and the controls, and differ only by the INTENSITY table:
flame height / rate / size, ember count, smoke density and darkness, coal glow and light intensity, so a
game can switch or blend states with the same handles.

Always on and seamless: every track is periodic over the 12 s loop (starts and ends on the same value), and
every system is pre-filled on the first frame by bursts of particles that are already "in flight" - one band
per age range, spawned along the path the running particles of that age trace and carrying the remaining
part of their over-life curves (the Chimney Smoke method). The tables are measured by simulating each state
to the end of the loop (out/work/campfire/measure_prefill.py rewrites the PREFILL block below).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

DURATION = 12.0
SEED = 60311
BED_RADIUS = 0.42                          # metres: the burning area of the pit

# --- the five states ---------------------------------------------------------------------------------------
# flame: flame height multiplier (sprite size and launch speed); flame_rate: flame emitter rates;
# embers: ember rate; smoke: smoke density (rate and thickness); dark: smoke darkness near the fire (0 light
# grey .. 1 charcoal); coal: coal-bed glow; light: light intensity; smoke_y: where the smoke leaves the fire;
# smoke_life / smoke_size: multipliers on the puffs' lifetime and size.
INTENSITY: dict[str, dict[str, Any]] = {
    "campfire": {
        "name": "Campfire", "state": "medium", "seed": SEED,
        "flame": 1.0, "flame_rate": 1.0, "embers": 1.0, "smoke": 0.5, "dark": 0.45, "coal": 1.0,
        "light": 1.0, "smoke_y": 1.2, "smoke_life": 1.0, "smoke_size": 1.0,
        "blurb": "a balanced, natural campfire: lively flames licking up over a glowing coal bed, embers "
                 "drifting up with the wind and soft grey smoke rising above",
    },
    "campfire_smolder": {
        "name": "Campfire Smolder", "state": "smolder", "seed": SEED + 1,
        "flame": 0.42, "flame_rate": 0.45, "embers": 0.35, "smoke": 1.0, "dark": 0.8, "coal": 0.85,
        "light": 0.38, "smoke_y": 0.35, "smoke_life": 1.1, "smoke_size": 1.35,
        "blurb": "a smouldering campfire: low small flames under a thick, dark column of smoke, a few embers "
                 "and a dim light",
    },
    "campfire_low": {
        "name": "Campfire Low", "state": "low", "seed": SEED + 2,
        "flame": 0.62, "flame_rate": 0.65, "embers": 0.55, "smoke": 0.35, "dark": 0.4, "coal": 0.9,
        "light": 0.62, "smoke_y": 0.6, "smoke_life": 0.9, "smoke_size": 0.85,
        "blurb": "a small, steady campfire: short flames over a glowing coal bed, a few embers and a thin "
                 "wisp of smoke",
    },
    "campfire_high": {
        "name": "Campfire High", "state": "high", "seed": SEED + 3,
        "flame": 1.5, "flame_rate": 1.05, "embers": 2.0, "smoke": 0.6, "dark": 0.5, "coal": 1.2,
        "light": 1.6, "smoke_y": 1.5, "smoke_life": 1.05, "smoke_size": 1.0,
        "blurb": "a big, energetic campfire: tall roaring flames, showers of embers, more smoke and a bright "
                 "warm light",
    },
    "campfire_dying": {
        "name": "Campfire Dying", "state": "dying", "seed": SEED + 4,
        "flame": 0.22, "flame_rate": 0.05, "embers": 0.4, "smoke": 0.3, "dark": 0.3, "coal": 0.8,
        "light": 0.26, "smoke_y": 0.2, "smoke_life": 1.0, "smoke_size": 0.8,
        "blurb": "a dying campfire: a glowing coal bed with only a rare tiny lick of flame, embers "
                 "drifting up and a thin trail of smoke",
    },
}

# --- look -------------------------------------------------------------------------------------------------
FIRE_RAMP = [                               # heat -> colour: deep red tips, orange body, white-yellow base
    [0.0, [0.26, 0.02, 0.0, 1.0]],
    [0.2, [0.7, 0.07, 0.0, 1.0]],
    [0.42, [1.0, 0.27, 0.03, 1.0]],
    [0.64, [1.0, 0.54, 0.12, 1.0]],
    [0.84, [1.0, 0.78, 0.38, 1.0]],
    [1.0, [1.0, 0.92, 0.7, 1.0]],
]
FLAME_COLOR_LIFE = [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.3, [1.0, 0.94, 0.86, 1.0]],
                    [0.62, [1.0, 0.82, 0.66, 1.0]], [1.0, [0.9, 0.6, 0.45, 1.0]]]
FIRE_LIGHT = [1.0, 0.46, 0.14, 1.0]
# The flames burn at half speed (the user: "the actual flame speed should be halved"). Each flame system is
# slowed in time by this factor: longer lives, slower launch and flipbook, a quarter of the buoyancy and curl
# accelerations, half the drag and emission rate - same paths, heights and particle counts, played slower.
FLAME_SLOW = 2.0
COAL = [1.0, 0.36, 0.08, 1.0]
EMBER_LIFE_COLOR = [[0.0, [1.0, 0.88, 0.5, 1.0]], [0.25, [1.0, 0.62, 0.2, 1.0]],
                    [0.6, [1.0, 0.32, 0.07, 1.0]], [1.0, [0.7, 0.1, 0.02, 1.0]]]

# --- over-life curves (also resampled for the pre-fill bands) ------------------------------------------------
BODY_SIZE = [[0.0, 0.55], [0.3, 1.0], [0.7, 0.9], [1.0, 0.6]]
BODY_OPACITY = [[0.0, 0.0], [0.14, 1.0], [0.62, 0.78], [1.0, 0.0]]
TONGUE_SIZE = [[0.0, 0.45], [0.3, 1.05], [1.0, 0.65]]
TONGUE_OPACITY = [[0.0, 0.0], [0.12, 1.0], [0.5, 0.6], [0.8, 0.2], [1.0, 0.0]]
LICK_SIZE = [[0.0, 0.5], [0.3, 1.0], [1.0, 0.45]]
LICK_OPACITY = [[0.0, 0.0], [0.12, 1.0], [0.6, 0.8], [1.0, 0.0]]
GLOW_SIZE = [[0.0, 0.7], [0.4, 1.0], [1.0, 1.15]]
GLOW_OPACITY = [[0.0, 0.0], [0.3, 1.0], [0.7, 0.8], [1.0, 0.0]]
EMBER_OPACITY = [[0.0, 0.0], [0.06, 1.0], [0.7, 0.75], [1.0, 0.0]]
EMBER_EMISSIVE = [[0.0, 1.4], [0.4, 1.0], [1.0, 0.45]]
SMOKE_SIZE = [[0.0, 0.45], [0.2, 0.85], [0.5, 1.25], [1.0, 1.95]]
SMOKE_OPACITY = [[0.0, 0.0], [0.1, 0.8], [0.22, 1.0], [0.42, 0.6], [0.62, 0.3], [0.8, 0.1], [1.0, 0.0]]

# --- physics shared by every state (the states scale rates, sizes and launch speeds) -------------------------
FLAME_TILT = 7.0                           # flame lean at the default wind strength, degrees
WIND_TILT = 9.0                            # smoke / ember launch lean at the default wind strength
SMOKE_LAUNCH = 1.35
SMOKE_LIFE = 4.6
SMOKE_RATE = 12.0                          # at smoke density 1
SMOKE_ALPHA = 0.5                          # at smoke density 1
BAND_FLAME_OPACITY = 0.38                  # pre-fill flame bands, see build()

# systems pre-filled on the first frame: id -> normalised-age bands
BANDS: dict[str, list[tuple[float, float]]] = {
    "ps_flame_body": [(0.0, 0.5), (0.5, 1.0)],
    "ps_flame_tongue": [(0.0, 0.5), (0.5, 1.0)],
    "ps_flame_lick": [(0.0, 1.0)],
    "ps_coal_glow": [(0.0, 1.0)],
    "ps_embers": [(0.0, 0.4), (0.4, 1.0)],
    "ps_smoke": [(0.0, 0.06), (0.06, 0.3), (0.3, 0.55), (0.55, 0.78), (0.78, 1.0)],
}

# --- PREFILL BEGIN (written by out/work/campfire/measure_prefill.py) ------------------------------------------
# state -> system -> one row per band: (count, direction, speed, speed variance, spread deg, remaining life,
# its variance, positions ordered by age) - measured on the loop's last frame in the system's rig frame at t = 0
PREFILL: dict[str, dict[str, list[Any]]] = {
    'campfire': {
        'ps_flame_body': [
            [18, [0.018, 1.0, 0.007], 0.677, 0.122, 8.8, 1.024, 0.336, [[-0.09, 0.15, -0.24], [0.03, 0.16, 0.18], [0.1, 0.18, 0.23], [-0.08, 0.21, -0.06], [0.16, 0.22, 0.2], [0.14, 0.32, -0.08], [-0.15, 0.25, -0.12], [-0.1, 0.3, -0.04], [-0.06, 0.36, -0.18], [0.11, 0.35, -0.02], [-0.02, 0.46, 0.12], [-0.02, 0.36, -0.16], [0.11, 0.44, 0.13], [0.25, 0.56, 0.13], [0.17, 0.56, 0.05], [0.21, 0.51, 0.05], [-0.16, 0.66, -0.0], [0.2, 0.63, 0.06]]],
            [14, [-0.082, 0.994, -0.067], 0.607, 0.137, 11.0, 0.317, 0.376, [[-0.06, 0.59, -0.25], [0.14, 0.71, 0.18], [-0.39, 0.85, 0.02], [-0.12, 0.55, 0.03], [-0.22, 0.67, 0.11], [0.3, 0.75, 0.04], [-0.07, 0.76, -0.0], [-0.22, 0.72, -0.07], [-0.05, 0.71, -0.1], [0.18, 0.76, -0.18], [0.16, 0.91, -0.22], [-0.22, 1.11, -0.13], [-0.08, 0.92, -0.03], [0.15, 1.19, -0.08]]],
        ],
        'ps_flame_tongue': [
            [9, [-0.059, 0.996, -0.069], 0.979, 0.297, 15.4, 0.685, 0.275, [[0.06, 0.39, -0.08], [-0.2, 0.44, -0.13], [0.01, 0.49, 0.2], [0.04, 0.48, -0.1], [0.25, 0.44, -0.17], [-0.13, 0.5, -0.02], [-0.39, 0.85, -0.09], [-0.0, 0.9, -0.26], [0.09, 1.0, -0.1]]],
            [5, [-0.155, 0.987, 0.042], 0.896, 0.104, 17.6, 0.298, 0.254, [[-0.08, 1.07, 0.33], [-0.16, 1.03, 0.35], [-0.04, 0.85, -0.17], [-0.12, 1.22, -0.24], [0.25, 1.16, 0.05]]],
        ],
        'ps_flame_lick': [
            [17, [-0.113, 0.993, 0.042], 0.404, 0.084, 13.2, 0.377, 0.394, [[-0.17, 0.08, 0.06], [0.13, 0.08, 0.14], [-0.1, 0.09, 0.22], [-0.08, 0.1, -0.21], [-0.16, 0.12, -0.07], [0.13, 0.18, 0.13], [0.13, 0.14, 0.14], [0.21, 0.16, 0.07], [0.15, 0.23, -0.0], [0.2, 0.25, -0.08], [0.08, 0.19, 0.23], [0.15, 0.31, -0.02], [0.14, 0.25, -0.13], [0.02, 0.25, -0.28], [0.22, 0.22, 0.12], [0.15, 0.38, -0.04], [0.27, 0.38, 0.02]]],
        ],
        'ps_coal_glow': [
            [17, [-0.116, 0.99, 0.074], 0.034, 0.026, 13.7, 0.712, 0.756, [[0.19, 0.1, -0.15], [-0.21, 0.11, -0.06], [0.1, 0.12, -0.05], [-0.15, 0.11, -0.01], [-0.17, 0.13, 0.06], [0.14, 0.11, 0.1], [-0.14, 0.13, 0.01], [0.14, 0.12, -0.09], [-0.04, 0.12, -0.05], [0.09, 0.14, 0.09], [-0.12, 0.13, -0.01], [0.18, 0.12, 0.09], [0.13, 0.15, 0.02], [0.13, 0.16, -0.21], [-0.09, 0.14, -0.1], [-0.22, 0.16, 0.1], [0.04, 0.15, 0.13]]],
        ],
        'ps_embers': [
            [15, [-0.028, 0.977, -0.212], 1.758, 0.57, 19.9, 1.945, 1.233, [[0.02, 0.63, -0.16], [0.19, 0.73, -0.17], [0.09, 0.89, 0.11], [-0.02, 1.04, 0.11], [-0.13, 0.94, -0.09], [-0.17, 1.36, -0.1], [-0.11, 1.4, 0.02], [-0.11, 1.41, -0.12], [0.29, 1.46, -0.09], [-0.26, 1.68, -0.14], [0.18, 1.91, -0.11], [-0.21, 1.56, -0.39], [-0.48, 2.24, -0.24], [-0.35, 2.46, -0.7], [0.27, 1.79, 0.31]]],
            [12, [-0.072, 0.983, 0.169], 0.79, 0.689, 57.4, 0.881, 0.542, [[-0.97, 3.15, -0.69], [-0.84, 1.94, 0.5], [-1.35, 2.62, -1.19], [-1.15, 2.12, -0.48], [-1.34, 2.57, 0.18], [-0.53, 2.22, 0.37], [-1.54, 4.68, -0.14], [1.52, 1.46, 1.17], [-0.04, 4.28, -0.35], [-1.84, 4.49, -1.71], [0.21, 5.19, 0.6], [-1.93, 6.66, -0.07]]],
        ],
        'ps_smoke': [
            [3, [0.051, 0.997, -0.051], 1.19, 0.128, 4.6, 4.494, 0.258, [[0.26, 1.22, -0.12], [0.12, 1.39, -0.17], [0.2, 1.51, 0.07]]],
            [6, [-0.108, 0.984, -0.143], 1.197, 0.168, 11.6, 3.789, 0.472, [[-0.05, 1.8, 0.14], [0.12, 1.89, 0.07], [0.0, 2.04, -0.22], [0.13, 2.33, 0.01], [-0.24, 2.67, -0.28], [-0.6, 2.44, -0.05]]],
            [6, [-0.167, 0.925, -0.341], 1.003, 0.233, 24.0, 2.608, 0.264, [[-0.6, 3.07, -0.38], [-0.7, 2.51, -0.83], [-0.88, 2.42, -0.57], [-0.63, 3.85, -0.92], [0.01, 3.98, -0.45], [0.04, 3.8, -0.12]]],
            [6, [0.331, 0.868, -0.37], 0.977, 0.175, 20.3, 1.62, 0.467, [[-0.17, 3.83, -0.85], [0.71, 3.49, -1.49], [0.72, 4.13, -0.99], [-0.27, 4.29, -0.24], [0.43, 3.78, -0.98], [-1.17, 3.96, -1.99]]],
            [5, [0.341, 0.906, -0.25], 1.049, 0.338, 29.6, 0.513, 0.335, [[0.47, 6.64, -2.42], [0.41, 4.97, -1.66], [0.12, 6.32, -1.72], [-1.31, 8.06, -0.06], [-0.07, 5.05, -0.79]]],
        ],
    },
    'campfire_smolder': {
        'ps_flame_body': [
            [8, [-0.212, 0.969, 0.126], 0.286, 0.062, 23.7, 0.918, 0.294, [[-0.08, 0.06, 0.23], [-0.13, 0.08, 0.15], [0.17, 0.11, 0.01], [-0.15, 0.12, -0.02], [0.07, 0.14, 0.01], [-0.17, 0.21, -0.03], [-0.22, 0.19, 0.14], [0.12, 0.24, 0.09]]],
            [7, [-0.377, 0.905, -0.194], 0.194, 0.104, 35.3, 0.227, 0.27, [[-0.16, 0.19, -0.21], [-0.13, 0.24, -0.19], [-0.1, 0.27, -0.13], [-0.16, 0.41, 0.05], [-0.14, 0.29, -0.01], [0.11, 0.32, -0.09], [-0.26, 0.27, -0.23]]],
        ],
        'ps_flame_tongue': [
            [4, [-0.031, 0.984, -0.176], 0.354, 0.174, 13.8, 0.859, 0.348, [[-0.07, 0.15, -0.05], [0.02, 0.16, -0.14], [-0.21, 0.24, -0.17], [-0.25, 0.26, -0.11]]],
            [4, [-0.142, 0.99, 0.003], 0.397, 0.179, 27.3, 0.263, 0.251, [[-0.31, 0.36, -0.07], [-0.11, 0.39, -0.13], [0.07, 0.53, -0.2], [0.05, 0.83, 0.22]]],
        ],
        'ps_flame_lick': [
            [7, [-0.391, 0.785, 0.481], 0.136, 0.074, 40.5, 0.364, 0.413, [[0.17, 0.06, 0.12], [0.21, 0.06, -0.09], [0.13, 0.1, 0.21], [-0.19, 0.07, 0.18], [-0.23, 0.09, -0.01], [-0.2, 0.14, 0.06], [0.11, 0.1, -0.11]]],
        ],
        'ps_coal_glow': [
            [18, [-0.046, 0.997, -0.056], 0.036, 0.028, 19.3, 0.587, 0.639, [[0.13, 0.11, 0.22], [-0.18, 0.11, -0.04], [0.11, 0.11, 0.03], [0.14, 0.11, -0.06], [-0.01, 0.13, 0.02], [-0.21, 0.12, -0.13], [-0.14, 0.14, 0.01], [-0.17, 0.14, 0.05], [0.19, 0.14, 0.13], [0.18, 0.14, 0.13], [-0.22, 0.14, 0.08], [0.09, 0.14, 0.22], [0.03, 0.13, -0.22], [0.06, 0.11, 0.18], [-0.06, 0.12, -0.17], [0.16, 0.12, -0.19], [-0.14, 0.14, 0.03], [0.11, 0.16, -0.18]]],
        ],
        'ps_embers': [
            [4, [0.043, 0.993, 0.11], 1.413, 0.292, 22.9, 2.159, 1.088, [[0.21, 0.42, 0.05], [0.2, 0.62, 0.02], [0.1, 1.15, 0.1], [-0.07, 1.41, 0.14]]],
            [6, [0.292, 0.956, 0.036], 0.697, 0.409, 60.0, 0.916, 0.698, [[-0.14, 1.95, -0.43], [-0.15, -0.08, -0.06], [-1.51, 4.37, 0.4], [0.83, 1.3, 0.2], [0.54, 3.49, -1.07], [1.09, 2.82, 0.24]]],
        ],
        'ps_smoke': [
            [5, [0.035, 0.999, 0.024], 1.396, 0.151, 5.2, 4.842, 0.341, [[-0.01, 0.37, -0.18], [-0.0, 0.45, 0.03], [-0.06, 0.55, -0.02], [0.03, 0.63, -0.24], [0.2, 0.76, 0.1]]],
            [14, [0.198, 0.961, 0.193], 1.01, 0.178, 14.0, 4.233, 0.774, [[0.05, 0.84, 0.1], [0.19, 0.91, 0.19], [0.12, 0.89, -0.2], [0.19, 1.03, -0.02], [0.07, 1.14, -0.04], [-0.04, 1.16, 0.02], [0.0, 1.24, 0.28], [0.26, 1.24, 0.2], [0.22, 1.41, 0.05], [0.02, 1.52, 0.01], [0.04, 1.62, 0.12], [-0.06, 1.75, -0.02], [0.43, 1.55, 0.41], [0.24, 1.94, 0.07]]],
            [12, [-0.545, 0.834, 0.084], 0.802, 0.283, 27.3, 2.797, 0.602, [[-0.2, 2.43, 0.35], [-0.31, 2.43, 0.21], [-0.13, 1.19, 0.04], [-0.44, 1.78, 0.04], [-0.51, 2.78, 0.29], [-0.37, 2.81, 0.25], [-0.81, 2.67, 0.5], [-0.49, 3.23, 0.19], [-1.08, 3.22, 0.55], [-0.84, 2.42, 0.51], [-0.67, 2.57, 0.19], [-0.46, 2.55, 0.88]]],
            [10, [-0.504, 0.856, 0.119], 0.637, 0.214, 38.8, 1.823, 0.634, [[-0.67, 3.09, -0.17], [-0.02, 2.98, 0.25], [-0.45, 3.64, -0.45], [-0.12, 3.11, 0.36], [-1.13, 1.86, 0.88], [-0.15, 2.92, 0.39], [0.34, 3.52, -0.3], [0.32, 4.09, -1.07], [0.77, 3.6, -0.06], [0.83, 3.6, -0.61]]],
            [22, [-0.05, 0.876, -0.48], 0.502, 0.242, 40.8, 0.465, 0.495, [[-0.64, 3.53, 0.2], [0.75, 3.21, -0.09], [1.79, 2.88, -1.12], [1.43, 2.8, -0.71], [1.4, 2.17, -0.73], [0.44, 3.5, -1.09], [1.33, 2.66, -0.52], [1.31, 2.69, -0.19], [1.57, 2.53, -0.29], [1.8, 2.11, -0.27], [1.69, 2.14, -0.46], [2.32, 1.75, -0.0], [1.59, 3.89, -0.04], [1.39, 2.0, -0.48], [1.09, 4.46, 0.61], [2.94, 1.81, -1.15], [2.24, 2.12, 0.48], [1.47, 3.44, -0.53], [1.79, 2.09, -0.32], [0.29, 1.68, -0.2], [-0.15, 3.34, 0.39], [1.49, 3.31, -0.35]]],
        ],
    },
    'campfire_low': {
        'ps_flame_body': [
            [13, [0.132, 0.991, -0.01], 0.463, 0.127, 16.6, 0.993, 0.501, [[0.09, 0.11, 0.18], [-0.04, 0.14, 0.09], [-0.1, 0.17, 0.13], [0.17, 0.16, 0.07], [-0.1, 0.17, -0.12], [0.04, 0.17, -0.21], [0.02, 0.3, 0.21], [-0.17, 0.21, 0.15], [0.17, 0.29, 0.01], [0.12, 0.31, 0.15], [0.07, 0.42, 0.19], [0.15, 0.37, -0.07], [-0.2, 0.34, -0.12]]],
            [9, [-0.333, 0.926, -0.177], 0.32, 0.131, 23.6, 0.182, 0.17, [[0.01, 0.39, -0.23], [0.17, 0.48, 0.04], [-0.29, 0.35, -0.19], [-0.12, 0.37, 0.17], [0.06, 0.5, -0.19], [-0.11, 0.25, 0.03], [-0.04, 0.33, 0.0], [-0.14, 0.49, -0.15], [-0.2, 0.45, -0.37]]],
        ],
        'ps_flame_tongue': [
            [5, [-0.102, 0.995, -0.024], 0.668, 0.142, 7.6, 0.76, 0.425, [[-0.09, 0.21, 0.21], [-0.05, 0.23, -0.02], [-0.0, 0.36, -0.02], [-0.27, 0.4, 0.08], [-0.13, 0.49, 0.08]]],
            [5, [-0.015, 0.991, -0.135], 0.621, 0.124, 10.3, 0.175, 0.198, [[-0.16, 0.46, 0.02], [0.13, 0.6, -0.33], [0.06, 0.75, 0.07], [0.05, 0.67, 0.1], [0.05, 0.77, -0.26]]],
        ],
        'ps_flame_lick': [
            [11, [0.118, 0.98, 0.159], 0.227, 0.093, 28.1, 0.373, 0.341, [[-0.19, 0.06, 0.09], [-0.21, 0.09, -0.1], [-0.22, 0.07, 0.07], [-0.25, 0.08, 0.0], [0.25, 0.12, -0.1], [0.25, 0.11, -0.15], [-0.24, 0.13, 0.05], [0.16, 0.11, -0.16], [-0.24, 0.15, -0.01], [0.14, 0.16, -0.11], [-0.24, 0.34, -0.07]]],
        ],
        'ps_coal_glow': [
            [18, [0.054, 0.995, 0.085], 0.032, 0.019, 18.5, 0.668, 0.533, [[-0.17, 0.1, -0.08], [-0.06, 0.11, -0.08], [-0.04, 0.11, -0.02], [-0.06, 0.12, 0.16], [-0.06, 0.11, -0.17], [0.07, 0.13, 0.11], [0.08, 0.14, 0.14], [-0.05, 0.13, 0.21], [-0.07, 0.14, -0.18], [-0.03, 0.15, 0.07], [0.2, 0.11, 0.11], [-0.07, 0.13, -0.17], [-0.12, 0.15, -0.11], [0.08, 0.14, -0.05], [0.19, 0.16, 0.01], [-0.13, 0.12, -0.02], [0.06, 0.16, -0.01], [0.03, 0.12, -0.06]]],
        ],
        'ps_embers': [
            [7, [-0.055, 0.998, -0.026], 1.377, 0.442, 28.9, 2.084, 0.997, [[-0.09, 0.5, 0.2], [0.08, 0.81, 0.23], [-0.09, 0.73, -0.19], [-0.01, 0.69, 0.01], [-0.33, 1.23, 0.12], [-0.16, 1.43, -0.03], [0.18, 2.0, 0.75]]],
            [10, [-0.025, 0.946, -0.323], 0.651, 0.318, 60.0, 0.891, 1.055, [[-0.49, 1.72, 0.23], [-0.23, 1.35, 0.71], [0.66, 2.26, -0.14], [-1.56, 0.89, 0.56], [0.53, 3.28, -0.89], [-1.14, 1.94, 0.07], [0.93, 1.17, 1.5], [-0.42, 3.42, -0.7], [1.65, 0.4, -0.55], [-0.1, 5.22, 1.32]]],
        ],
        'ps_smoke': [
            [2, [0.051, 0.995, -0.083], 1.368, 0.04, 1.4, 3.97, 0.631, [[-0.02, 0.72, 0.12], [0.04, 0.98, 0.19]]],
            [4, [0.145, 0.989, 0.006], 1.156, 0.175, 5.1, 3.522, 0.467, [[0.06, 1.29, -0.23], [-0.0, 1.58, -0.14], [-0.17, 1.63, -0.07], [-0.01, 1.73, 0.12]]],
            [2, [0.499, 0.858, 0.123], 0.68, 0.178, 5.3, 2.47, 0.058, [[0.29, 2.33, -0.32], [0.26, 2.04, -0.49]]],
            [4, [-0.341, 0.893, -0.294], 0.813, 0.117, 33.4, 1.538, 0.489, [[0.09, 3.28, -0.51], [0.84, 2.74, -0.13], [-0.41, 2.99, -0.64], [-1.39, 3.54, 0.33]]],
            [3, [-0.896, -0.014, 0.444], 0.757, 0.165, 8.1, 0.428, 0.306, [[-1.45, 2.81, 0.73], [-1.33, 3.62, 0.8], [-1.46, 3.02, 0.86]]],
        ],
    },
    'campfire_high': {
        'ps_flame_body': [
            [18, [-0.036, 0.999, -0.024], 1.017, 0.206, 7.5, 0.939, 0.425, [[0.02, 0.25, -0.19], [0.06, 0.27, 0.13], [0.2, 0.33, 0.02], [-0.05, 0.32, -0.15], [0.21, 0.35, 0.05], [0.11, 0.49, -0.01], [-0.05, 0.54, 0.15], [-0.12, 0.44, 0.14], [-0.19, 0.54, 0.07], [-0.21, 0.53, -0.07], [-0.07, 0.63, 0.18], [-0.27, 0.71, -0.04], [-0.13, 0.86, -0.11], [-0.28, 0.59, -0.13], [-0.28, 0.73, 0.07], [0.02, 0.84, -0.17], [-0.06, 0.75, -0.18], [-0.03, 1.17, -0.02]]],
            [16, [-0.079, 0.991, -0.111], 1.028, 0.159, 7.8, 0.319, 0.323, [[0.09, 0.61, 0.11], [-0.03, 0.61, 0.25], [0.11, 1.22, -0.44], [0.25, 1.27, -0.25], [-0.22, 1.28, -0.05], [-0.07, 0.9, -0.03], [-0.21, 1.28, 0.2], [0.06, 1.46, 0.0], [0.24, 1.48, -0.29], [-0.1, 1.17, -0.04], [-0.21, 1.53, -0.38], [0.23, 1.54, -0.39], [-0.05, 1.71, 0.08], [0.14, 1.81, -0.23], [0.27, 1.45, -0.09], [-0.35, 1.81, 0.22]]],
        ],
        'ps_flame_tongue': [
            [8, [-0.076, 0.996, -0.051], 1.398, 0.26, 11.6, 0.858, 0.414, [[0.08, 0.5, -0.19], [-0.06, 0.56, 0.21], [0.1, 0.63, -0.13], [-0.25, 0.7, -0.08], [-0.1, 0.94, 0.14], [0.08, 0.91, -0.26], [-0.25, 1.03, -0.13], [-0.36, 1.34, -0.05]]],
            [6, [-0.018, 1.0, 0.001], 1.481, 0.255, 8.3, 0.342, 0.213, [[0.07, 1.02, -0.35], [0.14, 1.18, -0.17], [-0.24, 1.83, 0.29], [0.12, 1.79, 0.14], [-0.17, 1.49, 0.22], [0.43, 1.66, -0.03]]],
        ],
        'ps_flame_lick': [
            [17, [-0.179, 0.983, 0.033], 0.559, 0.088, 11.9, 0.353, 0.389, [[0.15, 0.06, 0.13], [-0.24, 0.08, -0.0], [0.09, 0.11, -0.19], [-0.22, 0.14, 0.09], [-0.19, 0.12, -0.11], [-0.23, 0.18, -0.08], [0.2, 0.19, 0.15], [-0.08, 0.2, -0.17], [-0.3, 0.25, 0.05], [-0.3, 0.22, 0.02], [0.16, 0.23, 0.02], [-0.2, 0.33, 0.17], [0.23, 0.25, 0.07], [-0.2, 0.36, 0.18], [-0.37, 0.31, -0.06], [0.12, 0.47, -0.0], [-0.26, 0.44, 0.15]]],
        ],
        'ps_coal_glow': [
            [17, [-0.04, 0.999, -0.022], 0.028, 0.014, 15.9, 0.656, 0.574, [[0.08, 0.1, -0.23], [0.2, 0.1, 0.02], [-0.11, 0.11, 0.16], [-0.01, 0.11, -0.26], [-0.17, 0.12, 0.18], [-0.12, 0.11, 0.14], [0.09, 0.14, 0.17], [-0.01, 0.14, 0.22], [-0.17, 0.12, 0.17], [-0.1, 0.12, 0.21], [0.0, 0.12, -0.15], [0.12, 0.14, -0.05], [-0.16, 0.14, -0.02], [0.06, 0.15, 0.09], [-0.06, 0.15, -0.04], [-0.15, 0.13, 0.17], [0.1, 0.15, 0.07]]],
        ],
        'ps_embers': [
            [29, [0.0, 0.997, 0.078], 1.668, 0.646, 20.6, 2.251, 0.883, [[0.02, 0.79, 0.13], [-0.01, 0.86, -0.06], [-0.14, 0.83, -0.18], [0.18, 1.0, -0.0], [-0.03, 0.96, 0.15], [-0.14, 0.98, 0.07], [0.21, 1.11, 0.07], [0.09, 1.06, 0.15], [-0.15, 1.01, 0.12], [-0.32, 1.12, -0.06], [0.26, 1.39, -0.15], [-0.2, 1.46, 0.19], [-0.27, 1.16, 0.02], [-0.2, 1.08, 0.01], [-0.14, 1.51, 0.16], [0.02, 1.71, 0.3], [-0.2, 1.2, -0.17], [0.12, 1.92, 0.3], [-0.38, 2.1, 0.02], [-1.09, 2.19, 0.46], [-0.21, 2.04, -0.3], [-0.61, 2.08, 0.45], [0.04, 2.49, -0.01], [0.0, 2.97, -0.14], [-0.38, 2.09, -0.72], [-0.63, 1.45, -0.01], [0.21, 2.58, 0.33], [0.12, 3.45, -0.42], [-0.0, 2.98, -0.41]]],
            [28, [0.073, 0.972, -0.223], 0.858, 0.571, 57.0, 0.838, 0.906, [[-0.5, 1.56, 0.4], [0.32, 2.63, 0.08], [-0.5, 0.77, -1.03], [0.61, 2.75, 0.68], [-0.01, 4.38, -0.11], [1.66, 2.23, 0.39], [-0.62, 1.97, -0.7], [-0.42, 3.0, -0.08], [1.32, 1.71, 0.19], [0.79, 3.96, -2.03], [-0.15, 3.15, -1.3], [0.37, 4.01, 0.82], [-1.26, 4.05, 0.16], [0.75, 3.63, -0.91], [-0.78, 4.3, 0.29], [-1.13, 4.96, 0.38], [-1.9, 4.83, -1.25], [1.87, 3.95, -1.37], [0.27, 6.88, 0.89], [1.44, 3.67, -1.67], [0.75, 5.96, -0.79], [2.4, 3.32, -0.21], [2.35, 2.48, -3.02], [1.05, 4.44, -0.5], [-0.43, 7.23, 0.12], [-0.16, 1.57, 1.52], [0.18, 5.62, -1.28], [1.38, 8.18, -0.93]]],
        ],
        'ps_smoke': [
            [3, [0.027, 0.999, -0.027], 1.126, 0.052, 6.4, 4.691, 0.306, [[0.03, 1.56, -0.28], [0.02, 1.68, 0.26], [-0.13, 1.84, 0.07]]],
            [8, [0.033, 0.997, 0.063], 1.31, 0.118, 9.3, 4.054, 0.576, [[0.02, 2.04, -0.04], [0.33, 2.16, -0.02], [-0.24, 2.47, -0.16], [-0.19, 2.35, -0.44], [0.34, 2.62, -0.02], [-0.46, 2.94, -0.26], [-0.1, 2.82, -0.34], [-0.23, 3.41, -0.34]]],
            [7, [-0.326, 0.861, 0.39], 0.849, 0.128, 22.5, 2.701, 0.735, [[-0.33, 3.39, -0.13], [-0.55, 3.34, -0.7], [-0.9, 4.0, -0.21], [-1.01, 3.95, -0.37], [-0.59, 3.51, 0.51], [-0.27, 3.9, -0.1], [-0.84, 3.5, 0.01]]],
            [8, [-0.493, 0.553, 0.672], 0.649, 0.295, 28.5, 1.489, 0.688, [[-0.94, 4.19, -1.14], [-0.41, 3.46, 0.06], [-0.48, 4.04, -0.07], [-1.31, 3.87, 1.6], [-1.04, 2.85, 0.96], [-1.45, 4.44, 1.76], [-0.75, 2.83, 2.32], [-0.41, 3.19, 0.75]]],
            [8, [-0.283, 0.519, 0.806], 0.528, 0.34, 45.6, 0.609, 0.559, [[-0.24, 4.67, 0.83], [-0.38, 3.48, 1.7], [0.66, 4.56, 2.15], [0.93, 1.82, 2.21], [0.79, 4.49, 1.66], [-0.42, 2.23, 0.17], [-0.07, 4.98, 2.04], [-1.12, 5.76, 3.6]]],
        ],
    },
    'campfire_dying': {
        'ps_flame_body': [
            [1, [-0.048, 0.86, 0.508], 0.399, 0.0, 0.0, 0.882, 0.0, [[-0.15, 0.24, -0.03]]],
            [1, [0.677, 0.339, -0.653], 0.481, 0.0, 0.0, 0.156, 0.0, [[0.29, 0.11, -0.23]]],
        ],
        'ps_flame_tongue': [
            None,
            None,
        ],
        'ps_flame_lick': [
            [2, [0.008, 0.609, 0.793], 0.206, 0.065, 23.2, 0.311, 0.403, [[-0.09, 0.07, 0.25], [-0.23, 0.16, -0.04]]],
        ],
        'ps_coal_glow': [
            [19, [0.075, 0.996, 0.043], 0.032, 0.021, 19.5, 0.7, 0.773, [[-0.1, 0.1, 0.08], [-0.09, 0.11, 0.11], [-0.11, 0.11, 0.1], [0.16, 0.12, 0.14], [0.24, 0.12, 0.04], [0.11, 0.13, 0.1], [-0.2, 0.14, -0.07], [0.15, 0.14, 0.17], [0.03, 0.11, 0.09], [0.09, 0.12, 0.05], [0.13, 0.15, 0.02], [-0.16, 0.13, 0.08], [0.05, 0.15, 0.06], [0.11, 0.12, 0.03], [0.03, 0.15, -0.05], [-0.04, 0.13, 0.18], [-0.11, 0.15, 0.16], [-0.19, 0.16, 0.06], [0.13, 0.14, 0.18]]],
        ],
        'ps_embers': [
            [4, [0.258, 0.914, -0.313], 0.72, 0.45, 43.9, 1.866, 1.5, [[0.17, 0.39, 0.16], [-0.03, 0.49, -0.14], [0.02, 0.6, 0.1], [0.47, 1.03, -0.11]]],
            [6, [-0.075, 0.91, -0.407], 0.875, 0.643, 58.9, 0.947, 0.972, [[0.18, 0.63, 0.29], [-0.79, 1.92, 0.43], [-0.64, 2.04, -1.3], [-0.31, 2.63, -0.37], [-0.97, 3.66, -0.36], [-0.32, 0.87, 1.26]]],
        ],
        'ps_smoke': [
            [2, [-0.059, 0.996, -0.063], 1.324, 0.175, 1.7, 4.426, 0.197, [[0.14, 0.26, -0.06], [-0.1, 0.62, 0.19]]],
            [3, [-0.077, 0.997, -0.032], 1.337, 0.111, 8.1, 3.973, 0.66, [[0.06, 0.91, -0.17], [0.21, 1.33, 0.1], [-0.29, 1.88, -0.16]]],
            [3, [0.112, 0.987, 0.115], 0.768, 0.084, 11.8, 2.542, 0.64, [[0.04, 2.17, 0.11], [-0.34, 2.87, 0.03], [-0.36, 2.52, 0.37]]],
            [5, [0.525, 0.545, 0.654], 0.411, 0.2, 58.8, 1.547, 0.453, [[-0.01, 2.1, 0.24], [0.27, 2.43, 0.48], [-0.66, 3.92, 0.72], [-0.35, 4.23, 0.24], [-0.15, 2.76, -0.17]]],
            [3, [0.88, -0.472, -0.049], 0.698, 0.013, 41.1, 0.438, 0.117, [[0.46, 4.3, -1.79], [-0.2, 2.7, -0.29], [0.14, 2.74, -0.13]]],
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


def periodic(terms: list[tuple[float, int, float]], base: float, step: float = 0.5) -> list[dict[str, float]]:
    """A keyframe track base * (1 + sum a*sin(2 pi k t / T + p)), exactly periodic over the loop."""
    n = int(round(DURATION / step))
    return [{"time": r4(i * step), "value": r4(base * max(0.0, 1.0 + harmonics(terms, i * step)))}
            for i in range(n + 1)]


def animated(track: list[dict[str, Any]]) -> dict[str, Any]:
    return {"value": track[0]["value"], "track": track}


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


def mix(a: list[float], b: list[float], f: float) -> list[float]:
    return [r4(x + (y - x) * f) for x, y in zip(a, b)]


def build(slug: str, spec: dict[str, Any], prefill: bool = True) -> dict[str, Any]:
    fh = spec["flame"]                       # flame height multiplier
    fr = spec["flame_rate"]
    smoke = spec["smoke"]
    dark = spec["dark"]

    nodes: list[dict[str, Any]] = [
        node("cam", "camera", {"position": [2.3, 2.05, 6.4], "target": [0.0, 1.5, 0.0], "fov": 38.0}),
        # the attachment point (the centre of the pit on the ground) and the wind rigs under it
        node("fire_pit", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False}),
        node("wind_heading", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                      "rotation": [0.0, 1.0, 0.0]}, parent="fire_pit"),
    ]
    # gusts: the tilt breathes on 12, 4 and 1.7 s harmonics, with a lateral sway on 6 and 2.4 s
    gust = [(0.18, 1, 0.4), (0.08, 3, 1.9), (0.04, 7, 0.3)]
    sway = [(0.3, 2, 1.1), (0.18, 5, 2.5)]
    for rid, tilt in (("wind_gust", WIND_TILT), ("wind_flame", FLAME_TILT)):
        keys = []
        for i in range(25):
            t = i * 0.5
            lean = tilt * (1.0 + harmonics(gust, t))
            keys.append({"time": r4(t), "value": [r4(tilt * harmonics(sway, t)), 0.0, r4(-lean)]})
        nodes.append(node(rid, "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                        "rotation": animated(keys)}, parent="wind_heading"))

    # ---------------------------------------------------------------- textures ---
    # the one fire flipbook (shared by every flame system and every state). `fire_sim` bakes the root at
    # image row 0 and both renderers draw row 0 at the TOP of a sprite, so the sheet is mirrored in V with a
    # `distort` (the Fire AOE workaround); the same remap crops the bright fuel slab under the tongues.
    slab, top = 0.34, 0.995
    s0 = 0.5 + top / 2.0
    s1 = s0 - (1.0 + top - slab) / 2.0
    nodes += [
        texture("tex_flame", 96, 144, [
            g("sim", "fire_sim", {"seed": 21, "fuel": 1.3, "fuel_width": 0.62, "buoyancy": 3.0, "turbulence": 1.8,
                                  "turbulence_scale": 3.0, "cooling": 1.65, "detail": 4, "speed": 0.06,
                                  "substeps": 4, "loop": True, "flicker": 0.3, "sharpness": 1.35}),
            g("half", "constant", {"color": [0.5, 0.5, 0.5, 1.0]}),
            g("vmap", "gradient_linear", {"angle": 90, "start": r4(s0), "end": r4(s1)}),
            g("byv", "channel_pack", {"channel": "luminance"}, {"r": "half", "g": "vmap", "b": "half"}),
            g("flip", "distort", {"amount": 2.0}, {"a": "sim", "by": "byv"}),
            # fade the sides, the root and the very top so no straight quad edge can show
            g("gu", "gradient_linear", {"angle": 0, "start": 0.0, "end": 1.0}),
            g("gui", "invert", None, {"a": "gu"}),
            g("par", "math", {"mode": "multiply"}, {"a": "gu", "b": "gui"}),
            g("side", "levels", {"in_high": 0.09, "gamma": 0.8}, {"a": "par"}),
            g("m1", "math", {"mode": "multiply"}, {"a": "flip", "b": "side"}),
            g("gv", "gradient_linear", {"angle": 90, "start": 1.0, "end": 0.0}),
            g("root", "levels", {"in_high": 0.4}, {"a": "gv"}),
            g("m2", "math", {"mode": "multiply"}, {"a": "m1", "b": "root"}),
            g("gvi", "invert", None, {"a": "gv"}),
            g("tip", "levels", {"in_high": 0.07}, {"a": "gvi"}),
            g("m3", "math", {"mode": "multiply"}, {"a": "m2", "b": "tip"}),
            g("lv", "levels", {"in_low": 0.2, "in_high": 0.9}, {"a": "m3"}),
        ], frames=24),
        # the Chimney Smoke puff: an irregular warped silhouette with billowing fbm inside, churning over life
        texture("tex_puff", 96, 96, [
            g("w1", "fbm", {"frequency": 1.8, "octaves": 2, "seed": 11, "animate": 0.5}),
            g("w2", "fbm", {"frequency": 1.8, "octaves": 2, "seed": 29, "animate": 0.5}),
            g("w1l", "levels", {"in_low": 0.25, "in_high": 0.75}, {"a": "w1"}),
            g("w2l", "levels", {"in_low": 0.25, "in_high": 0.75}, {"a": "w2"}),
            g("wp", "channel_pack", {"channel": "luminance"}, {"r": "w1l", "g": "w2l"}),
            g("rd", "gradient_radial", {"radius": 0.47, "falloff": "smooth"}),
            g("shape", "distort", {"amount": 0.09}, {"a": "rd", "by": "wp"}),
            g("n", "fbm", {"frequency": 2.1, "octaves": 5, "gain": 0.55, "seed": 3, "animate": 0.7}),
            g("nw", "distort", {"amount": 0.07}, {"a": "n", "by": "wp"}),
            g("nl", "levels", {"in_low": 0.2, "in_high": 0.8, "out_low": 0.3}, {"a": "nw"}),
            g("cells", "worley", {"frequency": 3.2, "octaves": 2, "seed": 8, "animate": 0.4}),
            g("lobes", "invert", None, {"a": "cells"}),
            g("lobes_l", "levels", {"in_low": 0.3, "in_high": 1.0}, {"a": "lobes"}),
            g("lobes_w", "distort", {"amount": 0.05}, {"a": "lobes_l", "by": "wp"}),
            g("detail", "math", {"mode": "lerp", "factor": 0.45}, {"a": "nl", "b": "lobes_w"}),
            g("m", "math", {"mode": "multiply"}, {"a": "shape", "b": "detail"}),
            g("alpha", "levels", {"in_low": 0.1, "in_high": 0.75, "gamma": 1.15}, {"a": "m"}),
            g("shade", "levels", {"in_low": 0.0, "in_high": 0.6, "out_low": 0.72}, {"a": "m"}),
            g("out", "channel_pack", {"channel": "luminance"},
              {"r": "shade", "g": "shade", "b": "shade", "a": "alpha"}),
        ], frames=12),
        texture("tex_erode", 64, 64, [
            g("a", "fbm", {"frequency": 3.0, "octaves": 4, "gain": 0.5, "seed": 4407, "tile": True}),
            g("b", "blur", {"radius": 1.5}, {"a": "a"}),
            g("l", "levels", {"in_low": 0.22, "in_high": 0.78}, {"a": "b"}),
        ]),
        # the coal bed seen from above: glowing lumps split by dark cracks, hottest in the middle, the rim
        # broken up by noise so it never reads as a disc
        texture("tex_coals", 192, 192, [
            g("cells", "worley", {"frequency": 7.0, "octaves": 1, "seed": 17}),
            g("lumps", "invert", None, {"a": "cells"}),
            g("lumps_l", "levels", {"in_low": 0.25, "in_high": 0.85, "gamma": 1.4}, {"a": "lumps"}),
            g("heat", "fbm", {"frequency": 3.5, "octaves": 4, "seed": 5}),
            g("heat_l", "levels", {"in_low": 0.3, "in_high": 0.75, "out_low": 0.15}, {"a": "heat"}),
            g("glow", "math", {"mode": "multiply"}, {"a": "lumps_l", "b": "heat_l"}),
            g("rad", "gradient_radial", {"radius": 0.48, "falloff": "smooth"}),
            g("edge_n", "fbm", {"frequency": 4.0, "octaves": 3, "seed": 9}),
            g("edge_p", "channel_pack", {"channel": "luminance"}, {"r": "edge_n", "g": "heat"}),
            g("rad_w", "distort", {"amount": 0.12}, {"a": "rad", "by": "edge_p"}),
            g("rad_l", "levels", {"in_low": 0.05, "in_high": 0.8, "gamma": 0.8}, {"a": "rad_w"}),
            g("m", "math", {"mode": "multiply"}, {"a": "glow", "b": "rad_l"}),
            g("under", "levels", {"in_high": 1.0, "out_high": 0.22}, {"a": "rad_l"}),
            g("sum", "math", {"mode": "max"}, {"a": "m", "b": "under"}),
            g("out", "levels", {"in_low": 0.03, "in_high": 0.8}, {"a": "sum"}),
        ]),
        # soft living hot glow for the bed and the air around the fire
        texture("tex_glow", 64, 64, [
            g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
            g("n", "fbm", {"frequency": 2.5, "octaves": 3, "seed": 31, "animate": 0.8}),
            g("nl", "levels", {"in_low": 0.2, "in_high": 0.8, "out_low": 0.45}, {"a": "n"}),
            g("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
            g("l", "levels", {"in_low": 0.02, "in_high": 0.75}, {"a": "m"}),
        ], frames=8),
        texture("tex_halo", 64, 64, [
            g("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
            g("l", "levels", {"gamma": 1.3}, {"a": "r"}),
        ]),
        texture("tex_spark", 32, 32, [
            g("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        ]),
    ]

    # ------------------------------------------------------- materials, forces ---
    nodes += [
        node("mat_fire", "material", {
            "blend": "additive", "emissive_intensity": 0.8, "soft_particle": True, "depth_fade": 0.15,
            "dissolve": 0.3, "erosion": 0.15, "temperature_gradient": FIRE_RAMP,
        }, {"noise_texture": "tex_erode"}),
        node("mat_glow", "material", {
            "blend": "additive", "shading": "unlit", "emissive_color": COAL, "emissive_intensity": 0.0,
            "soft_particle": True, "depth_fade": 0.2,
        }),
        node("mat_smoke", "material", {
            "blend": "alpha", "shading": "lit", "base_color": [1.0, 1.0, 1.0, 1.0],
            "emissive_color": [0.6, 0.62, 0.7, 1.0], "soft_particle": True, "depth_fade": 0.3,
        }, {"noise_texture": "tex_erode"}),
        node("f_buoy_fire", "force", {"force_type": "buoyancy", "strength": r4(2.4 * fh / FLAME_SLOW ** 2)}),
        node("f_curl_fire", "force", {"force_type": "curl_noise", "strength": r4(0.95 / FLAME_SLOW ** 2),
                                      "frequency": 1.2, "octaves": 3, "speed": r4(1.2 / FLAME_SLOW)}),
        node("f_buoy_ember", "force", {"force_type": "buoyancy", "strength": 0.55}),
        node("f_curl_ember", "force", {"force_type": "curl_noise", "strength": 1.6, "frequency": 0.8, "octaves": 2,
                                       "speed": 0.6}),
        node("f_cool", "force", {"force_type": "directional", "direction": [0.0, -1.0, 0.0], "strength": 0.04}),
        node("f_billow", "force", {"force_type": "curl_noise", "strength": 0.2, "frequency": 0.22, "octaves": 2,
                                   "speed": 0.22}),
        node("f_curl_smoke", "force", {"force_type": "curl_noise", "strength": 0.22, "frequency": 0.9,
                                       "octaves": 2, "speed": 0.45}),
    ]

    # ------------------------------------------------------------ systems ---
    systems: dict[str, dict[str, Any]] = {}       # id -> the spec the pre-fill bands copy
    emitters: dict[str, str] = {}                 # system id -> running emitter id

    def flame_system(sid: str, cap: int, life: float, life_var: float, size: float, size_var: float,
                     size_curve: list[list[Any]], opacity: float, opacity_curve: list[list[Any]],
                     stretch: float, fps: float, emissive: float, drag: float) -> dict[str, Any]:
        life, life_var, fps, drag, stretch = (life * FLAME_SLOW, life_var * FLAME_SLOW, fps / FLAME_SLOW,
                                              drag / FLAME_SLOW, stretch * FLAME_SLOW)
        params = {
            "max_particles": cap, "lifetime": r4(life), "lifetime_variance": r4(life_var),
            "size": r4(size), "size_variance": r4(size_var), "size_over_life": size_curve,
            "color": [1.0, 0.52, 0.13, 1.0], "color_over_life": FLAME_COLOR_LIFE,
            "opacity": opacity, "opacity_over_life": opacity_curve,
            "emissive": emissive, "drag": drag, "blend": "additive", "soft_particle_distance": 0.25,
            "render_mode": "stretched_billboard", "velocity_stretch": stretch, "sprite_fps": fps,
        }
        entry = node(sid, "particle_system", params,
                     {"sprite": "tex_flame", "material": "mat_fire", "forces": ["f_buoy_fire", "f_curl_fire"]},
                     layer="flames")
        systems[sid] = entry
        return entry

    def emitter(eid: str, sid: str, parent: str, layer: str, rate: dict[str, Any] | float, **params: Any) -> dict[str, Any]:
        p = {**params, "rate": rate, "start_time": 0.0, "duration": -1.0}
        emitters[sid] = eid
        return node(eid, "emitter", p, {"particle": sid}, parent=parent, layer=layer)

    # the flame rate breathes on its own harmonics, so the fire swells and settles over the loop
    breath = [(0.16, 1, 0.9), (0.12, 3, 2.3), (0.1, 5, 0.4), (0.07, 11, 1.7)]
    lick_breath = [(0.2, 2, 0.2), (0.12, 7, 2.9), (0.08, 13, 1.1)]

    # BODY: the broad burning mass over the whole bed
    nodes.append(flame_system("ps_flame_body", 260, 0.62, 0.22, 1.12 * fh, 0.34 * fh, BODY_SIZE, 0.44,
                              BODY_OPACITY, 0.1, 22.0, 0.24, 2.2))
    nodes.append(emitter("e_flame_body", "ps_flame_body", "wind_flame", "flames",
                         animated(periodic(breath, 46.0 * fr / FLAME_SLOW)),
                         shape="disc", radius=r4(BED_RADIUS * 0.62), position=[0.0, r4(0.14 * fh), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=14.0, velocity=r4(1.55 * fh / FLAME_SLOW),
                         velocity_variance=r4(0.6 * fh / FLAME_SLOW)))
    # TONGUES: taller, faster licks from the middle - the ragged, varying top of the fire
    nodes.append(flame_system("ps_flame_tongue", 120, 0.5, 0.18, 1.0 * fh, 0.3 * fh, TONGUE_SIZE, 0.34,
                              TONGUE_OPACITY, 0.2, 20.0, 0.3, 1.5))
    nodes.append(emitter("e_flame_tongue", "ps_flame_tongue", "wind_flame", "flames",
                         animated(periodic([(0.3, 2, 1.4), (0.2, 5, 0.3), (0.15, 9, 2.2)], 26.0 * fr / FLAME_SLOW)),
                         shape="disc", radius=r4(BED_RADIUS * 0.6), position=[0.0, r4(0.3 * fh), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=22.0, velocity=r4(2.1 * fh / FLAME_SLOW),
                         velocity_variance=r4(1.1 * fh / FLAME_SLOW)))
    # LICKS: small short flames around the edge of the bed
    nodes.append(flame_system("ps_flame_lick", 100, 0.36, 0.1, 0.42 * max(fh, 0.5), 0.14 * max(fh, 0.5),
                              LICK_SIZE, 0.34, LICK_OPACITY, 0.1, 26.0, 0.2, 2.5))
    nodes.append(emitter("e_flame_lick", "ps_flame_lick", "wind_flame", "flames",
                         animated(periodic(lick_breath, 44.0 * max(fr, 0.1) / FLAME_SLOW)),
                         shape="ring", radius=r4(BED_RADIUS * 0.62), inner_radius=r4(BED_RADIUS * 0.45),
                         position=[0.0, 0.05, 0.0], direction=[0.0, 1.0, 0.0], spread=20.0,
                         velocity=r4(0.8 * max(fh, 0.5) / FLAME_SLOW), velocity_variance=r4(0.35 / FLAME_SLOW)))

    # COALS: the glowing bed on the ground and the hot shimmer on it
    coal = spec["coal"]
    coal_track = periodic([(0.1, 1, 0.3), (0.07, 4, 1.8), (0.05, 9, 0.7), (0.04, 17, 2.6)], 1.4 * coal, 0.25)
    nodes.append(node("coal_bed", "decal", {
        "shape": "circle", "size": [r4(BED_RADIUS * 3.0), r4(BED_RADIUS * 3.0)], "color": COAL,
        "emissive": animated(coal_track), "blend": "additive", "opacity": 1.0, "fade_in": 0.0, "fade_out": 0.0,
        "position": [0.0, 0.01, 0.0],
    }, {"texture": "tex_coals"}, parent="fire_pit", layer="coals"))
    systems["ps_coal_glow"] = node("ps_coal_glow", "particle_system", {
        "max_particles": 60, "lifetime": 1.3, "lifetime_variance": 0.4, "size": 0.5, "size_variance": 0.18,
        "size_over_life": GLOW_SIZE, "color": [1.0, 0.36, 0.07, 1.0],
        "opacity": r4(0.09 * min(1.0, coal)), "opacity_over_life": GLOW_OPACITY,
        "emissive": 0.9, "drag": 1.5, "rotation_variance": 180.0, "angular_velocity_variance": 30.0,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.15, "sprite_fps": 8.0,
    }, {"sprite": "tex_glow", "material": "mat_glow"}, layer="coals")
    nodes.append(systems["ps_coal_glow"])
    nodes.append(emitter("e_coal_glow", "ps_coal_glow", "fire_pit", "coals",
                         animated(periodic([(0.15, 2, 0.6), (0.1, 7, 1.9)], 14.0)),
                         shape="disc", radius=r4(BED_RADIUS * 0.62), position=[0.0, 0.1, 0.0],
                         direction=[0.0, 1.0, 0.0], spread=30.0, velocity=0.08, velocity_variance=0.05))
    # the warm air around the fire: one big soft quad that lives the whole loop
    nodes.append(node("ps_halo", "particle_system", {
        "max_particles": 2, "lifetime": DURATION, "size": r4(1.9 + 0.8 * fh),
        "size_over_life": [[0.0, 1.0], [0.25, 1.04], [0.5, 0.97], [0.75, 1.03], [1.0, 1.0]],
        "color": [1.0, 0.34, 0.07, 1.0], "opacity": r4(0.03 + 0.025 * min(fh, 1.5)),
        "opacity_over_life": [[0.0, 1.0], [0.2, 0.86], [0.4, 1.0], [0.6, 0.9], [0.8, 1.04], [1.0, 1.0]],
        "emissive": 0.6, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.5,
    }, {"sprite": "tex_halo", "material": "mat_glow"}, layer="light"))
    nodes.append(node("e_halo", "emitter", {
        "shape": "point", "position": [0.0, r4(0.25 + 0.35 * fh), 0.0], "velocity": 0.0, "rate": 0.0,
        "burst_count": 1, "burst_times": [0.0], "start_time": 0.0, "duration": 0.05,
    }, {"particle": "ps_halo"}, parent="fire_pit", layer="light"))

    # EMBERS: small sparks thrown up out of the flames, drifting and cooling
    systems["ps_embers"] = node("ps_embers", "particle_system", {
        "max_particles": 220, "lifetime": 2.4, "lifetime_variance": 1.2, "size": 0.032, "size_variance": 0.014,
        "color": [1.0, 1.0, 1.0, 1.0], "color_over_life": EMBER_LIFE_COLOR,
        "opacity": 1.0, "opacity_over_life": EMBER_OPACITY, "emissive": 6.0, "emissive_over_life": EMBER_EMISSIVE,
        "drag": 0.9, "render_mode": "stretched_billboard", "velocity_stretch": 0.35, "blend": "additive",
        "soft_particle_distance": 0.02,
    }, {"sprite": "tex_spark", "material": "mat_glow", "forces": ["f_buoy_ember", "f_curl_ember"]}, layer="embers")
    nodes.append(systems["ps_embers"])
    nodes.append(emitter("e_embers", "ps_embers", "wind_gust", "embers",
                         animated(periodic([(0.35, 2, 0.1), (0.25, 5, 1.2), (0.2, 9, 2.8)], 16.0 * spec["embers"])),
                         shape="disc", radius=r4(BED_RADIUS * 0.6), position=[0.0, r4(0.25 + 0.3 * fh), 0.0],
                         direction=[0.0, 1.0, 0.0], spread=24.0, velocity=r4(1.5 + 0.7 * fh),
                         velocity_variance=1.0))

    # SMOKE: lit grey puffs, darker near the fire, rising from the flame tips and bending with the wind
    near = mix([0.7, 0.68, 0.66, 1.0], [0.2, 0.19, 0.18, 1.0], dark)
    mid = mix([0.8, 0.8, 0.8, 1.0], [0.42, 0.41, 0.41, 1.0], dark)
    far = mix([0.95, 0.96, 1.0, 1.0], [0.62, 0.63, 0.66, 1.0], dark)
    smoke_tint = [[0.0, near], [0.35, mid], [1.0, far]]
    smoke_life = SMOKE_LIFE * spec["smoke_life"]
    systems["ps_smoke"] = node("ps_smoke", "particle_system", {
        "max_particles": 80, "lifetime": r4(smoke_life), "lifetime_variance": 0.5,
        "size": r4((0.8 + 0.25 * min(fh, 1.5)) * spec["smoke_size"]), "size_variance": 0.2, "size_over_life": SMOKE_SIZE,
        "color": [0.78, 0.77, 0.76, 1.0], "color_over_life": smoke_tint,
        "opacity": r4(SMOKE_ALPHA * (0.45 + 0.55 * smoke)), "opacity_over_life": SMOKE_OPACITY,
        "emissive": 0.4, "drag": 0.25, "rotation_variance": 180.0, "angular_velocity_variance": 16.0,
        "render_mode": "billboard", "blend": "alpha", "sort": True, "soft_particle_distance": 0.3,
    }, {"sprite": "tex_puff", "material": "mat_smoke", "forces": ["f_cool", "f_billow", "f_curl_smoke"]},
        layer="smoke")
    nodes.append(systems["ps_smoke"])
    nodes.append(emitter("e_smoke", "ps_smoke", "wind_gust", "smoke",
                         animated(periodic([(0.24, 2, 0.7), (0.14, 5, 2.1)], SMOKE_RATE * smoke)),
                         shape="disc", radius=r4(0.2 + 0.12 * min(fh, 1.5)),
                         position=[0.0, r4(spec["smoke_y"]), 0.0], direction=[0.0, 1.0, 0.0], spread=8.0,
                         velocity=SMOKE_LAUNCH, velocity_variance=0.25))

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
            if row is None:                      # nothing of that age in this state: keep the ids, spawn none
                row = [0, [0.0, 1.0, 0.0], 0.0, 0.0, 0.0, r4(life * 0.5), 0.0, [[0.0, 0.1, 0.0]]]
            count, direction, speed, speed_var, spread, remaining, remaining_var, points = row
            start = max(0.0, 1.0 - remaining / life)
            k = f"{sid[3:]}_pre{i + 1}"
            params = dict(bp)
            params["max_particles"] = max(4, int(count * 2))
            params["lifetime"] = r4(max(0.05, remaining))
            params["lifetime_variance"] = r4(min(remaining_var, remaining * 0.8))
            if sid.startswith("ps_flame_"):
                # a band's sprites are all born on the same flipbook frame, so their hot cores line up and add
                # far brighter than the running fire's staggered frames: draw them fainter
                params["opacity"] = r4(bp["opacity"] * BAND_FLAME_OPACITY)
            for curve in ("size_over_life", "opacity_over_life", "color_over_life", "emissive_over_life"):
                if curve in bp:
                    params[curve] = tail(bp[curve], start)
            nodes.append(node(f"ps_{k}", "particle_system", params, dict(base["inputs"]), layer=base.get("layer")))
            if len(points) == 1:
                points = [points[0], [points[0][0], points[0][1] + 0.02, points[0][2]]]
            nodes.append(node(f"path_{k}", "curve", {"points": points, "curve_type": "linear",
                                                    "segments": max(8, 2 * len(points))}))
            nodes.append(node(f"e_{k}", "emitter", {
                "shape": "curve", "direction": direction, "spread": spread, "velocity": speed,
                "velocity_variance": speed_var, "rate": 0.0, "burst_count": count, "burst_times": [0.0],
                "start_time": 0.0, "duration": 0.05,
            }, {"particle": f"ps_{k}", "shape_curve": f"path_{k}"}, parent=running.get("parent"),
                layer=running.get("layer")))
            band_emitters.append(f"e_{k}")
            band_systems.setdefault(sid, []).append(f"ps_{k}")

    # -------------------------------------------------------------- light ---
    lt = spec["light"]
    flicker = periodic([(0.1, 1, 0.2), (0.08, 3, 1.3), (0.07, 7, 2.2), (0.06, 13, 0.5), (0.05, 23, 1.9),
                        (0.04, 37, 2.7)], 2.2 * lt, 0.125)
    nodes.append(node("l_fire", "light", {
        "light_type": "point", "position": [0.0, r4(0.7 + 0.35 * fh), 0.0], "color": FIRE_LIGHT,
        "intensity": animated(flicker), "radius": r4(6.0 + 3.0 * min(lt, 1.6)),
        "flicker_amplitude": 0.16, "flicker_frequency": 7.0, "cast_shadows": True,
    }, parent="fire_pit", layer="light"))

    # ------------------------------------------------------------ controls ---
    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit_label: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit_label, "bindings": bindings}

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    flame_sys = ["ps_flame_body", "ps_flame_tongue", "ps_flame_lick"]
    flame_all = [s for base in flame_sys for s in [base] + band_systems.get(base, [])]
    flame_em = ["e_flame_body", "e_flame_tongue", "e_flame_lick"]
    flame_bands = [e for e in band_emitters if e.startswith("e_flame_")]
    ember_em = ["e_embers"] + [e for e in band_emitters if e.startswith("e_embers_")]
    smoke_all = ["ps_smoke"] + band_systems.get("ps_smoke", [])
    smoke_bands = [e for e in band_emitters if e.startswith("e_smoke_")]
    coal_em = [e for e in band_emitters if e.startswith("e_coal_glow_")]

    def amount(emitter_ids: list[str]) -> list[dict[str, Any]]:
        return [mul(e, "burst_count" if e in band_emitters else "rate") for e in emitter_ids]

    controls = [
        control("fire_intensity", "Fire intensity", "Global", 0.0, 3.0,
                amount(flame_em + flame_bands + ember_em)
                + [mul("l_fire", "intensity"), mul("coal_bed", "emissive"), mul("ps_halo", "opacity")]
                + [mul(s, "opacity") for s in ["ps_coal_glow"] + band_systems.get("ps_coal_glow", [])]),
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

    state = spec["state"]
    return {
        "schema_version": "0.1.0",
        "name": spec["name"],
        "description": f"Always-on campfire ({state}): {spec['blurb']}.",
        "duration": DURATION,
        "seed": spec["seed"],
        "timeline": {"phases": [{"name": "always_on", "start": 0.0, "end": DURATION}]},
        "layers": [{"id": "coals", "name": "Coals", "role": "ambient"},
                   {"id": "flames", "name": "Flames", "role": "ambient"},
                   {"id": "embers", "name": "Embers", "role": "ambient"},
                   {"id": "smoke", "name": "Smoke", "role": "ambient"},
                   {"id": "light", "name": "Light", "role": "ambient"}],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/campfire.py",
            "variant": slug,
            "state": state,
            "category": "ambient",
            "loop": {"start": 0.0, "end": DURATION},
            "attachment": {
                "origin_node": "fire_pit",
                "notes": "Attach the `fire_pit` node at the centre of the fire pit on the ground (y = 0); the "
                         "effect draws no logs, stones or pit. The five Campfire states share every node id and "
                         "control, so a game can switch or blend them. Feed the global wind into Wind strength "
                         "(0 calm, 1 light, 3 strong) and Wind direction (degrees around Y). It is always on and "
                         "loops seamlessly.",
            },
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.08, "grid": False,
                                "bloom_intensity": 0.14, "bloom_radius": 0.04, "exposure": 0.95},
            "tags": ["ambient", "environment", "fire", "campfire", state],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write the five effect JSON files into")
    parser.add_argument("--only", default="", help="comma-separated slugs to write (default: all five)")
    parser.add_argument("--no-prefill", action="store_true", help="leave the pre-fill bands out (for measuring)")
    parser.add_argument("--check", help="directory whose files must match the generator output byte for byte")
    args = parser.parse_args()
    only = {s for s in args.only.split(",") if s}
    if args.check:
        bad = 0
        for slug, spec in INTENSITY.items():
            path = Path(args.check) / f"{slug}.json"
            same = path.is_file() and path.read_text(encoding="utf-8") == render(build(slug, spec))
            bad += 0 if same else 1
            print(f"{path}: {'identical' if same else 'DIFFERENT'}")
        return 1 if bad else 0
    out = Path(args.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    for slug, spec in INTENSITY.items():
        if only and slug not in only:
            continue
        effect = build(slug, spec, prefill=not args.no_prefill)
        path = out / f"{slug}.json"
        path.write_text(render(effect), encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
