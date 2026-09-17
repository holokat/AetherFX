#!/usr/bin/env python3
"""Generate the nine "drain" channel effects from one parametrised description.

    python tools/generators/drain_variants.py --out examples/effects

Three palettes (Life Drain, Corruption Drain, Soul Drain) times three styles
(standard, thin beam, thick beam).  Every variant is built by :func:`build` from
the same node graph, so all nine documents carry an identical set of node ids
and the same seven controls; a variant that does not want a feature disables its
nodes instead of deleting them.  The VARIANTS table picks a palette, which
reaches every colour in the effect (decals, particles, trails, beams, lights,
volumes, materials), and a style, which sets how much stream there is.

The effect is a sustained channel between two anchor points and draws neither
character: SOURCE is the caster's hand, TARGET the victim's chest, and life
flows from the TARGET to the SOURCE.  Everything that moves travels that way:

* the strands are `beam` nodes whose origin is the TARGET and whose target is
  the SOURCE, so `pulse_speed` runs pulses toward the caster and `noise_scroll`
  makes the undulation itself travel toward the caster (`noise_loop` closes the
  motion over the sustain window, `noise_taper` lets every strand leave both
  anchors smoothly - a spindle, never a fan of kinks);
* filaments and smoke tendrils are per-particle `trail` ribbons behind particles
  that are born at the TARGET, pulled along the stream by an attractor at the
  SOURCE and held on a small sphere collider there until their ribbon has been
  drawn in, so a strand is never cut off in mid air;
* streaks, motes, droplets and wisps are torn off the TARGET and sucked into the
  hand by the same attractor;
* the release animates the beams' TARGET end along the path into the hand, so
  the stream breaks from the victim first.

The output is deterministic: no RNG, no wall clock, no absolute paths.

House style: no crosses, plus signs or four-armed star motifs anywhere (the
target flare is a swarm of short-lived velocity-stretched rays of random length
and direction, never a `star` texture); every strand is soft-edged, tapered at
both ends and sinuous; ribbons carry a soft cross-width texture.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# layout and timing (the concept sheet, total 3.0 s)
# ---------------------------------------------------------------------------

SOURCE = [-3.0, 1.35, 0.0]       # the caster's hand
TARGET = [3.0, 1.25, 0.0]        # the victim's chest
MID = [0.0, 1.30, 0.0]
AXIS = [-1.0, 0.0167, 0.0]       # unit-ish direction TARGET -> SOURCE (the flow)
BODY = [3.0, 1.15, 0.0]          # centre of the victim's body volume (0.4 .. 1.9 m high)

DURATION = 3.0
PHASES = [
    ("cast", 0.0, 0.3),
    ("channel_start", 0.3, 0.6),
    ("sustain", 0.6, 2.0),
    ("peak_drain", 2.0, 2.4),
    ("release", 2.4, 2.7),
    ("fade", 2.7, 3.0),
]
LOOP_START, LOOP_END = 0.6, 2.0
LOOP = LOOP_END - LOOP_START      # 1.4 s: every periodic motion divides it

T_CAST = 0.0
T_SHOOT = 0.30                    # the stream leaves the hand
T_CONNECT = 0.46                  # ... and reaches the chest: the flare ignites
T_SUSTAIN = 0.6
T_PEAK = 2.0
T_RELEASE = 2.4                   # the stream lets go of the target
T_GONE = 2.68                     # ... and has been drawn into the hand
T_END = 3.0

# ---------------------------------------------------------------------------
# palettes - every colour in the effect is derived from one of these entries
# ---------------------------------------------------------------------------

LIFE = {
    "key": "life",
    "hot": [1.00, 0.56, 0.60],      # pink-white: strand cores, flare core, sparks
    "mid": [1.00, 0.055, 0.045],    # the signature hue: saturated blood red
    "deep": [0.36, 0.004, 0.012],   # dark red glow, haze, ground pools
    "smoke": [0.060, 0.004, 0.007], # black-red smoke body
    "rim": [0.46, 0.014, 0.022],    # lit edge of the smoke tendrils
    "ember": [1.00, 0.20, 0.10],    # embers and droplets
    "light": [1.00, 0.10, 0.08],    # point lights
    "sign": [0.85, 0.02, 0.03],     # signature element: blood droplets
    "beam_gain": 1.0, "sheath_gain": 1.0, "tendril_gain": 1.0, "tendril_width": 1.0,
    "tendril_opacity": 0.7, "smoke_gain": 1.0, "turbulence": 1.0, "haze_always": False,
    "sign_kind": "droplets",
}

CORRUPTION = {
    "key": "corruption",
    "hot": [0.86, 1.00, 0.46],      # yellow-green cores
    "mid": [0.22, 1.00, 0.06],      # toxic green
    "deep": [0.035, 0.26, 0.010],   # sickly dark green
    "smoke": [0.012, 0.050, 0.008],
    "rim": [0.20, 0.72, 0.04],
    "ember": [0.62, 1.00, 0.16],
    "light": [0.34, 1.00, 0.12],
    "sign": [0.70, 1.00, 0.22],     # signature element: rising spores
    "beam_gain": 1.0, "sheath_gain": 1.0, "tendril_gain": 1.0, "tendril_width": 1.0,
    "tendril_opacity": 0.74, "smoke_gain": 1.0, "turbulence": 1.32, "haze_always": False,
    "sign_kind": "spores",
}

SOUL = {
    "key": "soul",
    "hot": [1.00, 0.34, 0.36],      # the thin crimson core
    "mid": [0.80, 0.010, 0.040],    # deep red
    "deep": [0.20, 0.000, 0.016],   # the faint lit haze behind the black strands
    "smoke": [0.0035, 0.0025, 0.004],  # BLACK: the body of the stream
    "rim": [0.62, 0.006, 0.030],    # crimson rim that makes black read on a black stage
    "ember": [0.90, 0.07, 0.08],
    "light": [0.95, 0.05, 0.07],
    "sign": [0.72, 0.78, 0.90],     # signature element: pale ghostly wisps
    "beam_gain": 0.62, "sheath_gain": 0.5, "tendril_gain": 1.0, "tendril_width": 1.55,
    "tendril_opacity": 0.96, "smoke_gain": 1.7, "turbulence": 1.1, "haze_always": True,
    "sign_kind": "wisps",
}

# ---------------------------------------------------------------------------
# styles - how much stream there is
# ---------------------------------------------------------------------------
#
# wave:    (noise_frequency 1/m, noise_amplitude m) of the shared spine.  Every
#          strand reads the SAME displacement field (`noise_seed`), so the stream
#          is one slow S-curve in 3D, and each strand is a scaled, phase-shifted
#          copy of that travelling wave: they separate and re-cross like a braid.
# strands: (width m, amplitude x, noise_offset m, noise_scroll m/s,
#           pulses per loop, emissive, core_width, glow_width)
# A strand with core_width 0 is a "silk band": glow only, a soft ribbon of pure
# colour with no white line in it (it carries no pulse - a pulse on a glow-only
# band burns out).  A strand with a core is a thin hot filament, and its
# `pulses per loop` becomes pulse_speed = n / LOOP so every pulse train closes
# over the sustain window.  A width of 0 disables the strand.

STYLES: dict[str, dict[str, Any]] = {
    "standard": {
        "wave": (0.17, 1.45),
        "strands": [
            (0.52, 1.00, 0.00, 2.4, 0, 11.0, 0.0, 1.0),     # the spine: wide soft silk
            (0.30, 1.20, 0.95, 2.6, 0, 10.5, 0.0, 1.0),     # silk that swings wider and crosses it
            (0.20, 0.72, -0.85, 2.2, 0, 10.0, 0.0, 1.0),    # silk on the inside of the curve
            (0.085, 1.06, 0.30, 2.4, 2, 0.6, 0.20, 3.0),    # thin hot filament riding the spine
            (0.062, 1.26, -1.35, 2.7, 3, 0.5, 0.22, 3.0),   # thin hot filament weaving round it
            (0.0, 1.0, 0.0, 2.4, 0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0, 2.4, 0, 0.0, 0.2, 3.0),
        ],
        "sheath": (1.15, 3.6),           # width m, emissive
        "filaments": 6.0, "filament_width": 0.075, "filament_spread": 24.0,
        "tendrils": 8.0, "tendril_width": 0.2, "tendril_spread": 34.0,
        "streaks": 110.0, "streak_radius": 0.5, "motes": 46.0, "droplets": 14.0, "smoke": 14.0,
        "rays": 70.0, "ray_size": 0.075, "soft_rays": 20.0, "sparks": 26.0, "flare": 1.0,
        "shower": 120, "hand": 0.9, "arcs": 24.0,
        "sign": 1.0, "body": 0.5, "body_extras": False, "haze": 0.0, "circle": 1.0, "light": 1.0,
    },
    "thin": {
        "wave": (0.19, 0.8),
        "strands": [
            (0.17, 1.00, 0.00, 2.4, 0, 10.5, 0.0, 1.0),     # one slender strand of silk ...
            (0.0, 1.0, 0.0, 2.4, 0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0, 2.4, 0, 0.0, 0.0, 1.0),
            (0.07, 1.00, 0.00, 2.4, 3, 0.75, 0.20, 3.0),    # ... with its hot core on the same path
            (0.04, 1.30, 0.55, 2.6, 2, 0.5, 0.24, 2.8),     # and one faint companion winding round it
            (0.0, 1.0, 0.0, 2.4, 0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0, 2.4, 0, 0.0, 0.2, 3.0),
        ],
        "sheath": (0.42, 2.8),
        "filaments": 0.0, "filament_width": 0.05, "filament_spread": 12.0,
        "tendrils": 0.0, "tendril_width": 0.12, "tendril_spread": 16.0,
        "streaks": 24.0, "streak_radius": 0.2, "motes": 12.0, "droplets": 0.0, "smoke": 0.0,
        "rays": 34.0, "ray_size": 0.05, "soft_rays": 8.0, "sparks": 8.0, "flare": 0.55,
        "shower": 36, "hand": 0.55, "arcs": 10.0,
        "sign": 0.0, "body": 0.14, "body_extras": False, "haze": 0.0, "circle": 0.8, "light": 0.6,
    },
    "thick": {
        "wave": (0.16, 1.55),
        "strands": [
            (0.80, 1.00, 0.00, 2.4, 0, 11.0, 0.0, 1.0),
            (0.50, 1.28, 0.95, 2.6, 0, 10.5, 0.0, 1.0),
            (0.36, 0.70, -0.85, 2.2, 0, 10.0, 0.0, 1.0),
            (0.125, 1.06, 0.30, 2.4, 2, 0.65, 0.18, 3.0),
            (0.095, 1.30, -1.35, 2.7, 3, 0.55, 0.2, 3.0),
            (0.28, 1.55, 1.9, 2.5, 0, 9.5, 0.0, 1.0),
            (0.08, 0.80, -2.1, 2.3, 1, 0.5, 0.22, 3.0),
        ],
        "sheath": (1.8, 3.8),
        "filaments": 10.0, "filament_width": 0.095, "filament_spread": 28.0,
        "tendrils": 13.0, "tendril_width": 0.28, "tendril_spread": 38.0,
        "streaks": 170.0, "streak_radius": 0.68, "motes": 70.0, "droplets": 24.0, "smoke": 22.0,
        "rays": 96.0, "ray_size": 0.09, "soft_rays": 28.0, "sparks": 38.0, "flare": 1.25,
        "shower": 170, "hand": 1.05, "arcs": 30.0,
        "sign": 1.6, "body": 1.0, "body_extras": True, "haze": 1.0, "circle": 1.15, "light": 1.25,
    },
}

STYLE_NAMES = {"standard": "", "thin": " Thin Beam", "thick": " Thick Beam"}
STYLE_TAGS = {"standard": [], "thin": ["thin"], "thick": ["thick", "volumetric"]}
STYLE_BLURB = {
    "standard": "a braided stream of undulating strands, smoke tendrils and embers",
    "thin": "one slender sinuous strand with travelling pulses, the light low-cost version",
    "thick": "a wide dense stream with a raymarched haze and motes ripped off the whole target",
}

PALETTE_NAMES = {"life": "Life Drain", "corruption": "Corruption Drain", "soul": "Soul Drain"}
PALETTE_BLURB = {
    "life": "Blood-red life drain channel",
    "corruption": "Toxic green corruption drain channel",
    "soul": "Black-and-crimson soul drain channel",
}
PALETTE_TAIL = {
    "life": "life flows from the target into the caster's hand",
    "corruption": "rot is pulled from the target into the caster's hand, spores rising off the victim",
    "soul": "black smoke wrapped round a thin crimson core pulls pale wisps out of the target",
}


def _variants() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    seed = 66100
    for palette in (LIFE, CORRUPTION, SOUL):
        for style in ("standard", "thin", "thick"):
            slug = f"{palette['key']}_drain" + ("" if style == "standard" else f"_{style}")
            out[slug] = {
                "name": PALETTE_NAMES[palette["key"]] + STYLE_NAMES[style],
                "description": f"{PALETTE_BLURB[palette['key']]}: {STYLE_BLURB[style]}; "
                               f"{PALETTE_TAIL[palette['key']]}.",
                "palette": palette,
                "style": style,
                "seed": seed,
            }
            seed += 1
    return out


#: The one table.  slug -> display name, description, palette, style, seed.
VARIANTS: dict[str, dict[str, Any]] = _variants()

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def r4(x: float) -> float:
    """Round so the documents stay byte-stable and readable."""
    return round(float(x), 4) + 0.0


def c(rgb: list[float], a: float = 1.0, k: float = 1.0) -> list[float]:
    """An RGBA colour from a palette entry, optionally scaled."""
    return [r4(rgb[0] * k), r4(rgb[1] * k), r4(rgb[2] * k), r4(a)]


def mix(a: list[float], b: list[float], t: float) -> list[float]:
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def lum(rgb: list[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def palette_gain(p: dict[str, Any], key: str = "mid") -> float:
    """Scale additive light so every palette lands at the same stage brightness:
    toxic green is three times as luminous as blood red at equal RGB values, so
    an unscaled green variant would wash the whole frame out."""
    return r4((lum(LIFE[key]) / max(lum(p[key]), 1e-4)) ** 0.62)


def tr(pairs: list[tuple[float, Any]], interp: str | None = None) -> dict[str, Any]:
    """A keyframed parameter {"value": first, "track": [...]}"""
    keys = []
    for t, v in pairs:
        key: dict[str, Any] = {"time": r4(t), "value": v}
        if interp:
            key["interp"] = interp
        keys.append(key)
    return {"value": pairs[0][1], "track": keys}


def env(pairs: list[tuple[float, float]], scale: float = 1.0) -> dict[str, Any]:
    return tr([(t, r4(v * scale)) for t, v in pairs])


def lerp3(a: list[float], b: list[float], t: float) -> list[float]:
    return [r4(a[i] + (b[i] - a[i]) * t) for i in range(3)]


def offset(p: list[float], dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> list[float]:
    return [r4(p[0] + dx), r4(p[1] + dy), r4(p[2] + dz)]


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


def tex(nid: str, w: int, h: int, nodes: list[dict[str, Any]], output: str) -> dict[str, Any]:
    return node(nid, "texture", {"width": w, "height": h, "graph": {"nodes": nodes, "output": output}})


def op(oid: str, name: str, params: dict[str, Any] | None = None,
       inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": oid, "op": name}
    if params:
        d["params"] = params
    if inputs:
        d["inputs"] = inputs
    return d


def maxes(prefix: str, ids: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Fold a list of graph node ids together with `math max`."""
    out: list[dict[str, Any]] = []
    cur = ids[0]
    for i, nxt in enumerate(ids[1:]):
        nid = f"{prefix}{i}"
        out.append(op(nid, "math", {"mode": "max"}, {"a": cur, "b": nxt}))
        cur = nid
    return out, cur


# ---------------------------------------------------------------------------
# envelopes
# ---------------------------------------------------------------------------


def heartbeat(depth: float, beats: int = 2, steps: int = 8) -> list[tuple[float, float]]:
    """A gentle throb over the sustain window that closes on itself: `beats`
    whole periods fit LOOP exactly, so value(LOOP_START) == value(LOOP_END)."""
    out: list[tuple[float, float]] = []
    n = beats * steps
    for i in range(n + 1):
        t = LOOP_START + LOOP * i / n
        out.append((t, 1.0 + depth * math.sin(2.0 * math.pi * beats * i / n)))
    return out


def stream_envelope(connect: float, peak: float, throb: float) -> list[tuple[float, float]]:
    """0 before the stream leaves the hand, a flash as it connects, a steady
    (gently throbbing, exactly periodic) sustain, the peak, then the release."""
    return ([(T_SHOOT, 0.0), (T_SHOOT + 0.05, 0.7), (T_CONNECT + 0.02, connect)]
            + heartbeat(throb)
            + [(T_PEAK + 0.12, peak * 0.96), (T_PEAK + 0.28, peak), (T_RELEASE, peak * 0.8),
               (T_RELEASE + 0.16, 0.62), (T_GONE - 0.02, 0.3), (T_GONE + 0.03, 0.0)])


def tip_track() -> dict[str, Any]:
    """World position of the stream's far end: it shoots from the hand to the
    chest, holds, and at the release lets go of the chest and is drawn back into
    the hand (slow to break, then fast)."""
    keys: list[tuple[float, list[float]]] = [(0.0, list(SOURCE)), (T_SHOOT, list(SOURCE))]
    for i in range(1, 9):
        u = i / 8.0
        keys.append((T_SHOOT + (T_CONNECT - T_SHOOT) * u, lerp3(SOURCE, TARGET, 1.0 - (1.0 - u) ** 2.2)))
    keys.append((T_RELEASE, list(TARGET)))
    for i in range(1, 9):
        u = i / 8.0
        keys.append((T_RELEASE + (T_GONE - T_RELEASE) * u, lerp3(TARGET, SOURCE, u ** 2.0)))
    keys.append((T_END, list(SOURCE)))
    return tr(keys, "linear")


def channel_rate(base: float, peak: float = 1.9, start: float = T_CONNECT,
                 stop: float = T_RELEASE - 0.04) -> dict[str, Any]:
    """Emission that belongs to the open channel: on at the connect, steady over
    the sustain (so a held loop keeps it), up at the peak, off at the release."""
    return env([(start, 0.0), (start + 0.08, base), (T_PEAK, base), (T_PEAK + 0.12, base * peak),
                (T_PEAK + 0.3, base * peak), (stop, base * 0.6), (stop + 0.04, 0.0)])


# ---------------------------------------------------------------------------
# textures
# ---------------------------------------------------------------------------


def band(prefix: str, in_high: float, gamma: float) -> tuple[list[dict[str, Any]], str]:
    """Soft cross-width ribbon profile: 1 on the centre line, 0 at both edges."""
    return [
        op(prefix + "g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op(prefix + "i", "invert", None, {"a": prefix + "g"}),
        op(prefix + "t", "math", {"mode": "min"}, {"a": prefix + "g", "b": prefix + "i"}),
        op(prefix, "levels", {"in_low": 0.0, "in_high": in_high, "gamma": gamma}, {"a": prefix + "t"}),
    ], prefix


def rel(value: float, reference: float) -> float:
    """value / reference, clamped to [0, 1] (texture channels are relative tints)."""
    return r4(min(1.0, max(0.0, value / max(reference, 1e-4))))


def tendril_texture(p: dict[str, Any], seed: int) -> dict[str, Any]:
    """The smoke ribbon: a bundle of wisps, never a tape.  Tiling noise eats the
    band into separate streaks (several across the width) and the colour follows
    the alpha, so every wisp is dark at its heart and lit along its edge - which
    is what lets black smoke read on a black stage.  u runs along the ribbon (one
    tile per metre, scrolled by the trail's uv_scroll), v across it; the texture
    is only eight texels long, so the wisps run on for decimetres and wander
    slowly instead of repeating a pattern every metre like the links of a chain."""
    wide, wide_id = band("w", 0.5, 1.6)
    smoke, rim = p["smoke"], p["rim"]
    nodes = wide + [
        op("n", "fbm", {"frequency": 4.0, "octaves": 3, "seed": seed % 997, "tile": True}),
        op("nl", "levels", {"in_low": 0.36, "in_high": 0.72, "out_low": 0.12, "out_high": 1.0}, {"a": "n"}),
        op("w1", "math", {"mode": "multiply"}, {"a": wide_id, "b": "nl"}),
        op("al", "levels", {"in_low": 0.02, "in_high": 0.7, "gamma": 1.1}, {"a": "w1"}),
        # rim -> heart colour, one grey image per channel, driven by the alpha itself.
        # Stored relative to the rim colour (1 = rim): the trail's own `color` is the
        # rim, so a renderer that does not texture ribbons still draws the right hue.
        op("cr", "levels", {"in_low": 0.1, "in_high": 0.75, "out_low": 1.0, "out_high": rel(smoke[0], rim[0])}, {"a": "al"}),
        op("cg", "levels", {"in_low": 0.1, "in_high": 0.75, "out_low": 1.0, "out_high": rel(smoke[1], rim[1])}, {"a": "al"}),
        op("cb", "levels", {"in_low": 0.1, "in_high": 0.75, "out_low": 1.0, "out_high": rel(smoke[2], rim[2])}, {"a": "al"}),
        op("out", "channel_pack", {"channel": "luminance"}, {"r": "cr", "g": "cg", "b": "cb", "a": "al"}),
    ]
    return tex("tex_tendril", 8, 128, nodes, "out")


def circle_texture(seed: int) -> dict[str, Any]:
    """Ground circle: hairline rings, a band of wavering script-like dashes, a
    ring of dots and five short inner arcs.  Nothing passes through the centre
    and nothing has two or four arms."""
    parts = [
        op("r0", "ring", {"radius": 0.470, "thickness": 0.0050, "softness": 0.0050}),
        op("r1", "ring", {"radius": 0.447, "thickness": 0.0030, "softness": 0.0040}),
        op("r2", "ring", {"radius": 0.300, "thickness": 0.0035, "softness": 0.0040}),
        # script band: 17 dashes, bent by noise so no two read alike
        op("bd", "ring", {"radius": 0.398, "thickness": 0.030, "softness": 0.010}),
        op("gap", "spokes", {"count": 17, "width": 0.050, "softness": 0.012,
                             "inner_radius": 0.30, "outer_radius": 0.5, "rotation": 7.0}),
        op("gapi", "invert", None, {"a": "gap"}),
        op("dash", "math", {"mode": "multiply"}, {"a": "bd", "b": "gapi"}),
        op("wn", "fbm", {"frequency": 9.0, "octaves": 2, "seed": seed % 7919}),
        op("wl", "levels", {"in_low": 0.2, "in_high": 0.8}, {"a": "wn"}),
        op("scr", "distort", {"amount": 0.030}, {"a": "dash", "by": "wl"}),
        # 29 dots and five short arcs
        op("dots", "spokes", {"count": 29, "width": 0.010, "softness": 0.006,
                              "inner_radius": 0.345, "outer_radius": 0.357, "rotation": 3.0}),
        op("ar", "ring", {"radius": 0.232, "thickness": 0.010, "softness": 0.006}),
        op("arm", "spokes", {"count": 5, "width": 0.150, "softness": 0.030,
                             "inner_radius": 0.18, "outer_radius": 0.29, "rotation": 22.0}),
        op("arc", "math", {"mode": "multiply"}, {"a": "ar", "b": "arm"}),
    ]
    folds, out = maxes("m", ["r0", "r1", "r2", "scr", "dots", "arc"])
    tail = [
        op("soft", "blur", {"radius": 0.9}, {"a": out}),
        op("lv", "levels", {"in_low": 0.02, "in_high": 0.8, "gamma": 0.9}, {"a": "soft"}),
    ]
    return tex("tex_circle", 384, 384, parts + folds + tail, "lv")


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------


def build(slug: str, spec: dict[str, Any]) -> dict[str, Any]:
    p = spec["palette"]
    style_key = spec["style"]
    s = STYLES[style_key]
    seed = spec["seed"]

    hot, mid, deep = p["hot"], p["mid"], p["deep"]
    smoke, ember, sign = p["smoke"], p["ember"], p["sign"]
    g = palette_gain(p)             # additive light in the signature hue
    gh = palette_gain(p, "hot")     # additive light in the hot hue
    turb = p["turbulence"]
    thick = style_key == "thick"
    haze_on = thick or p["haze_always"]

    nodes: list[dict[str, Any]] = []
    add = nodes.append
    # control bindings are collected while the graph is built
    bind: dict[str, list[dict[str, str]]] = {k: [] for k in (
        "stream_intensity", "stream_width", "flow_speed", "turbulence", "target_burst", "particles")}

    def drive(control: str, nid: str, parameter: str) -> None:
        bind[control].append({"node": nid, "parameter": parameter, "op": "multiply"})

    # ---------------- textures ----------------
    nb, nb_out = band("b", 0.46, 0.5)
    add(tex("tex_band", 8, 64, nb, nb_out))                       # filament ribbons: thin bright centre
    add(tendril_texture(p, seed))
    add(tex("tex_glow", 128, 128, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 0.55}, {"a": "r"}),
    ], "lv"))
    add(tex("tex_dot", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "inner_radius": 0.02, "falloff": "quadratic"}),
    ], "r"))
    add(tex("tex_puff", 128, 128, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 3.6, "octaves": 4, "seed": (seed + 7) % 997}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "n"}),
        op("lv", "levels", {"in_low": 0.07, "in_high": 0.6}, {"a": "m"}),
    ], "lv"))
    add(tex("tex_pool", 128, 128, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"in_low": 0.0, "in_high": 1.0, "gamma": 0.8}, {"a": "r"}),
    ], "lv"))
    add(circle_texture(seed))

    # ---------------- materials ----------------
    add(node("mat_glow", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(mid),
        "emissive_intensity": 0.2, "soft_particle": True, "depth_fade": 0.2}))
    add(node("mat_spark", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(hot),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.08}))
    add(node("mat_filament", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(mid),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.1, "double_sided": True,
    }, inputs={"base_texture": "tex_band"}))
    add(node("mat_tendril", "material", {
        "blend": "alpha", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(p["rim"]),
        "emissive_intensity": 0.5, "soft_particle": True, "depth_fade": 0.12, "double_sided": True,
    }, inputs={"base_texture": "tex_tendril"}))
    add(node("mat_smoke", "material", {
        "blend": "alpha", "shading": "lit", "base_color": c(mix(smoke, deep, 0.35)),
        "emissive_color": c(deep), "emissive_intensity": 0.12, "soft_particle": True, "depth_fade": 0.3,
        "dissolve": 0.42, "erosion": 0.26}))
    add(node("mat_circle", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(mix(mid, hot, 0.35)),
        "emissive_intensity": 0.45, "soft_particle": True}))

    # ---------------- forces and colliders ----------------
    add(node("f_pull", "force", {          # what makes it a drain: everything falls into the hand
        "force_type": "attractor", "position": list(SOURCE), "strength": 80.0}))
    add(node("f_pull_soft", "force", {
        "force_type": "attractor", "position": list(SOURCE), "strength": 27.0}))
    add(node("f_meander", "force", {       # big slow eddies: no ribbon flies a straight line
        "force_type": "curl_noise", "strength": r4(26.0 * turb), "frequency": 0.33, "octaves": 2, "speed": 0.5}))
    add(node("f_funnel", "force", {        # the last metre: a steep well, so every ribbon lands in the palm
        "force_type": "attractor", "position": list(SOURCE), "strength": 260.0, "radius": 1.3,
        "falloff": "smooth"}))
    add(node("f_flutter", "force", {
        "force_type": "curl_noise", "strength": r4(2.4 * turb), "frequency": 1.7, "octaves": 2, "speed": 0.9}))
    add(node("f_braid", "force", {         # twist about the stream axis: strands wind round each other
        "force_type": "vortex", "direction": list(AXIS), "position": list(MID), "strength": 3.0,
        "radius": 1.6, "falloff": "smooth"}))
    add(node("f_hand_swirl", "force", {     # two crossed vortices: orbits in every plane, a ball of arcs
        "force_type": "vortex", "direction": [0.3, 1.0, 0.2], "position": list(SOURCE), "strength": 20.0,
        "radius": 1.0, "falloff": "smooth"}))
    add(node("f_hand_swirl2", "force", {
        "force_type": "vortex", "direction": [1.0, 0.1, 0.6], "position": list(SOURCE), "strength": 17.0,
        "radius": 1.0, "falloff": "smooth"}))
    add(node("f_hand_hold", "force", {
        "force_type": "attractor", "position": list(SOURCE), "strength": 44.0, "radius": 1.2,
        "falloff": "smooth"}))
    add(node("f_rise", "force", {"force_type": "buoyancy", "strength": 0.7}))
    add(node("f_gravity", "force", {"force_type": "gravity", "strength": 5.5}))
    for fid in ("f_pull", "f_pull_soft"):
        drive("flow_speed", fid, "strength")
    for fid in ("f_meander", "f_flutter"):
        drive("turbulence", fid, "strength")

    # ribbons are held on this sphere until their tail has been drawn in
    add(node("hand_hold", "collider", {
        "collider_type": "sphere", "position": list(SOURCE), "radius": 0.17, "bounce": 0.0, "friction": 1.0}))
    add(node("hand_sink", "collider", {
        "collider_type": "sphere", "position": list(SOURCE), "radius": 0.15, "kill_on_collision": True}))
    add(node("ground", "collider", {
        "collider_type": "plane", "normal": [0.0, 1.0, 0.0], "bounce": 0.15, "friction": 0.7}))

    # ---------------- ground circles ----------------
    circle = s["circle"]
    for key, centre, size, t_on, level in (
        ("src", SOURCE, 2.7, T_CAST, 1.0),
        ("tgt", TARGET, 2.3, T_CONNECT - 0.04, 0.62),
    ):
        x = centre[0]
        add(node(f"d_pool_{key}", "decal", {      # a faint inner glow, never a disc
            "shape": "circle", "size": [r4(size * 2.0), r4(size * 2.0)], "position": [x, 0.0, 0.0],
            "color": c(mid), "emissive": r4(0.35 * g * circle), "blend": "additive",
            "fade_in": 0.05, "fade_out": 0.2,
            "opacity": env([(t_on, 0.0), (t_on + 0.14, 0.035 * level), (t_on + 0.3, 0.045 * level),
                            (T_PEAK, 0.045 * level), (T_PEAK + 0.2, 0.09 * level), (T_RELEASE, 0.05 * level),
                            (T_GONE, 0.025 * level), (T_END - 0.04, 0.0)]),
        }, layer="ground", inputs={"texture": "tex_pool", "material": "mat_circle"}))
        add(node(f"d_circle_{key}", "decal", {
            "shape": "circle", "position": [x, 0.0, 0.0],
            "size": tr([(t_on, [r4(size * 0.55), r4(size * 0.55)]), (t_on + 0.1, [r4(size * 1.04), r4(size * 1.04)]),
                        (t_on + 0.24, [size, size]), (T_PEAK, [size, size]),
                        (T_PEAK + 0.2, [r4(size * 1.06), r4(size * 1.06)]), (T_END, [r4(size * 0.98), r4(size * 0.98)])]),
            "rotation": tr([(0.0, [0.0, 0.0, 0.0]), (T_END, [0.0, 38.0 if key == "src" else -30.0, 0.0])]),
            "color": c(mix(mid, hot, 0.3)), "emissive": r4(1.1 * g * circle), "blend": "additive",
            "fade_in": 0.04, "fade_out": 0.16,
            "opacity": env([(t_on, 0.0), (t_on + 0.08, 0.9 * level), (t_on + 0.26, 0.5 * level),
                            (T_PEAK, 0.48 * level), (T_PEAK + 0.2, 0.85 * level), (T_RELEASE, 0.5 * level),
                            (T_GONE, 0.26 * level), (T_END - 0.04, 0.0)]),
        }, layer="ground", inputs={"texture": "tex_circle", "material": "mat_circle"}))

    # ---------------- the strands (beams, TARGET -> SOURCE) ----------------
    tip = tip_track()
    beam_gain = p["beam_gain"]
    wave_freq, wave_amp = s["wave"]
    for i, (width, amp, phase, scroll, pulses, emissive, core_w, glow_w) in enumerate(s["strands"], start=1):
        on = width > 0.0
        if not on:   # keep the node (identical ids across the nine documents), switched off
            width, emissive = 0.05, 1.0
        silk = core_w <= 0.0
        sid = f"strand_{i}"
        add(node(sid, "beam", {
            "origin": tip, "target": list(SOURCE),
            "start_time": T_SHOOT, "duration": r4(T_GONE + 0.04 - T_SHOOT),
            "width": env(stream_envelope(1.15, 1.7, 0.05), width),
            "segments": 56, "detail": 0, "jitter_rate": 0.0,
            "noise_amplitude": r4(wave_amp * amp * turb), "noise_frequency": wave_freq,
            "noise_seed": 1, "noise_offset": phase,
            "noise_scroll": scroll, "noise_loop": LOOP, "noise_taper": 0.32,
            "width_profile": "taper_both", "width_variance": 0.0, "intensity_noise": 0.0,
            "flicker": 0.06 if silk else 0.22, "flicker_frequency": 9.0 if silk else 13.0,
            "pulse_speed": 0.0 if silk else r4(pulses / LOOP),
            "color": c(mix(mid, deep, 0.10 * (i - 1))) if silk else c(mix(mid, hot, 0.2)),
            "emissive": env(stream_envelope(1.6, 1.7, 0.07), emissive * beam_gain * g),
            "core_width": core_w, "glow_width": glow_w, "blend": "additive",
        }, layer="stream", enabled=on))
        drive("stream_intensity", sid, "emissive")
        drive("stream_width", sid, "width")
        drive("flow_speed", sid, "noise_scroll")
        drive("flow_speed", sid, "pulse_speed")
        drive("turbulence", sid, "noise_amplitude")

    # the soft glow body round the strands: glow only, no core, on the spine itself
    sheath_w, sheath_e = s["sheath"]
    add(node("stream_sheath", "beam", {
        "origin": tip, "target": list(SOURCE),
        "start_time": T_SHOOT, "duration": r4(T_GONE + 0.04 - T_SHOOT),
        "width": env(stream_envelope(1.1, 1.6, 0.06), sheath_w),
        "segments": 40, "detail": 0, "jitter_rate": 0.0,
        "noise_amplitude": r4(wave_amp * turb), "noise_frequency": wave_freq,
        "noise_seed": 1, "noise_offset": 0.25,
        "noise_scroll": 2.4, "noise_loop": LOOP, "noise_taper": 0.32,
        "width_profile": "taper_both", "flicker": 0.08, "flicker_frequency": 7.0,
        "color": c(mix(mid, deep, 0.35)),
        "emissive": env(stream_envelope(1.5, 1.7, 0.08), sheath_e * p["sheath_gain"] * g),
        "core_width": 0.0, "glow_width": 1.0, "blend": "additive",
    }, layer="stream"))
    drive("stream_intensity", "stream_sheath", "emissive")
    drive("stream_width", "stream_sheath", "width")
    drive("flow_speed", "stream_sheath", "noise_scroll")
    drive("turbulence", "stream_sheath", "noise_amplitude")

    # ---------------- flowing filaments (hot ribbons) ----------------
    fil_on = s["filaments"] > 0.0
    add(node("ps_filament", "particle_system", {
        "max_particles": 64, "lifetime": 0.95, "lifetime_variance": 0.04,
        "size": 0.05, "size_over_life": [[0.0, 0.4], [0.2, 1.0], [0.5, 0.6], [0.62, 0.0], [1.0, 0.0]],
        "color": c(hot), "opacity_over_life": [[0.0, 0.0], [0.1, 0.9], [0.5, 0.7], [0.6, 0.0], [1.0, 0.0]],
        "emissive": r4(2.6 * gh), "render_mode": "billboard", "blend": "additive",
        "drag": 6.0, "bounce": 0.0, "friction": 1.0, "collision_radius": 0.01,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark",
               "forces": ["f_pull", "f_funnel", "f_meander", "f_braid"], "colliders": ["hand_hold"]}, enabled=fil_on))
    add(node("trail_filament", "trail", {
        "lifetime": 0.34, "max_segments": 40, "min_vertex_distance": 0.05,
        "taper": [[0.0, 0.0], [0.1, 1.0], [0.45, 0.7], [1.0, 0.0]],
        "opacity_over_life": [[0.0, 1.0], [0.5, 0.75], [1.0, 0.0]],
        "color": c(mix(hot, mid, 0.45)), "blend": "additive", "start_time": T_CONNECT,
        "width": env([(T_CONNECT, s["filament_width"]), (T_PEAK, s["filament_width"]),
                      (T_PEAK + 0.15, s["filament_width"] * 1.45), (T_RELEASE, s["filament_width"] * 1.2),
                      (T_END, s["filament_width"] * 0.7)]),
        "emissive": env([(T_CONNECT, 1.5), (T_PEAK, 1.5), (T_PEAK + 0.15, 2.3), (T_RELEASE, 1.7),
                         (T_END, 0.4)], beam_gain * gh),
    }, layer="stream", inputs={"source": "ps_filament", "material": "mat_filament"}, enabled=fil_on))
    add(node("e_filament", "emitter", {
        "shape": "sphere", "radius": 0.07, "position": list(TARGET), "start_time": T_CONNECT,
        "direction": list(AXIS), "spread": s["filament_spread"], "velocity": 10.0, "velocity_variance": 3.0,
        "rate": channel_rate(max(s["filaments"], 0.1), 2.2),
    }, layer="stream", inputs={"particle": "ps_filament"}, enabled=fil_on))
    drive("stream_intensity", "trail_filament", "emissive")
    drive("stream_width", "trail_filament", "width")
    drive("flow_speed", "e_filament", "velocity")

    # ---------------- smoke tendrils (dark ribbons with a lit rim) ----------------
    ten_on = s["tendrils"] > 0.0
    ten_w = s["tendril_width"] * p["tendril_width"]
    add(node("ps_tendril", "particle_system", {
        "max_particles": 96, "lifetime": 1.3, "lifetime_variance": 0.05,
        "size": 0.02, "render_mode": "none", "drag": 6.0, "bounce": 0.0, "friction": 1.0,
        "collision_radius": 0.01,
    }, inputs={"forces": ["f_pull", "f_funnel", "f_meander", "f_braid"], "colliders": ["hand_hold"]}, enabled=ten_on))
    add(node("trail_tendril", "trail", {
        "lifetime": 0.46, "max_segments": 64, "min_vertex_distance": 0.04,
        "taper": [[0.0, 0.0], [0.1, 0.6], [0.32, 1.0], [0.7, 0.7], [1.0, 0.0]],
        "opacity_over_life": [[0.0, p["tendril_opacity"]], [0.55, p["tendril_opacity"] * 0.85], [1.0, 0.0]],
        "color": c(p["rim"]), "blend": "alpha", "uv_scroll": -1.6, "start_time": T_CONNECT,
        "width": env([(T_CONNECT, ten_w * 0.8), (T_SUSTAIN, ten_w), (T_PEAK, ten_w),
                      (T_PEAK + 0.15, ten_w * 1.4), (T_RELEASE, ten_w * 1.2), (T_END, ten_w * 0.8)]),
        "emissive": env([(T_CONNECT, 0.7), (T_PEAK, 0.7), (T_PEAK + 0.15, 1.3), (T_END, 0.3)],
                        p["tendril_gain"] * g),
    }, layer="tendrils", inputs={"source": "ps_tendril", "material": "mat_tendril"}, enabled=ten_on))
    add(node("e_tendril", "emitter", {
        "shape": "sphere", "radius": 0.12, "position": list(TARGET), "start_time": T_CONNECT,
        "direction": list(AXIS), "spread": s["tendril_spread"], "velocity": 8.5, "velocity_variance": 3.0,
        "rate": channel_rate(max(s["tendrils"], 0.1), 2.0),
    }, layer="tendrils", inputs={"particle": "ps_tendril"}, enabled=ten_on))
    drive("stream_width", "trail_tendril", "width")
    drive("flow_speed", "e_tendril", "velocity")
    drive("flow_speed", "trail_tendril", "uv_scroll")

    # ---------------- streaks riding the stream ----------------
    add(node("ps_streak", "particle_system", {
        "max_particles": 320, "lifetime": 0.46, "lifetime_variance": 0.16,
        "size": 0.062, "size_variance": 0.034,
        "size_over_life": [[0.0, 0.3], [0.25, 1.0], [1.0, 0.2]],
        "color": c(mix(hot, mid, 0.35)),
        "color_over_life": [[0.0, c(hot)], [0.4, c(mix(hot, mid, 0.6))], [1.0, c(mid, 1.0, 0.6)]],
        "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": r4(2.6 * gh), "render_mode": "stretched_billboard", "velocity_stretch": 0.85,
        "blend": "additive", "drag": 0.6, "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_flutter", "f_braid"],
               "colliders": ["hand_sink"]}))
    add(node("e_streak", "emitter", {      # a spindle round the axis, as wide as the stream swings
        "shape": "sphere", "radius": 1.0, "position": list(MID),
        "scale": [2.75, s["streak_radius"], s["streak_radius"]],
        "start_time": T_CONNECT - 0.06, "direction": list(AXIS), "spread": 6.0,
        "velocity": 7.0, "velocity_variance": 2.6,
        "rate": channel_rate(s["streaks"], 2.4, start=T_CONNECT - 0.06, stop=T_RELEASE + 0.02),
    }, layer="flow", inputs={"particle": "ps_streak"}))
    drive("particles", "e_streak", "rate")
    drive("flow_speed", "e_streak", "velocity")
    drive("stream_intensity", "ps_streak", "emissive")

    # sparks thrown off the stream's far end as it shoots across and as it is drawn back
    add(node("ps_tip", "particle_system", {
        "max_particles": 220, "lifetime": 0.34, "lifetime_variance": 0.14,
        "size": 0.04, "size_variance": 0.02, "size_over_life": [[0.0, 1.0], [1.0, 0.1]],
        "color": c(hot), "color_over_life": [[0.0, c(hot)], [1.0, c(mid)]],
        "opacity_over_life": [[0.0, 1.0], [0.6, 0.8], [1.0, 0.0]],
        "emissive": r4(3.0 * gh), "render_mode": "stretched_billboard", "velocity_stretch": 0.2,
        "blend": "additive", "drag": 5.0,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_flutter"]}))
    add(node("e_tip", "emitter", {
        "shape": "sphere", "radius": 0.08, "position": tip, "direction": [0.0, 0.0, 0.0],
        "velocity": 2.4, "velocity_variance": 1.6, "inherit_velocity": 0.25,
        "rate": env([(T_SHOOT, 0.0), (T_SHOOT + 0.02, 260.0), (T_CONNECT, 260.0), (T_CONNECT + 0.04, 0.0),
                     (T_RELEASE, 0.0), (T_RELEASE + 0.03, 150.0), (T_GONE - 0.03, 150.0), (T_GONE, 0.0)],
                    s["flare"]),
    }, layer="flow", inputs={"particle": "ps_tip"}))
    drive("particles", "e_tip", "rate")

    # ---------------- embers and motes torn off the target ----------------
    add(node("ps_mote", "particle_system", {
        "max_particles": 420, "lifetime": 1.0, "lifetime_variance": 0.12,
        "size": 0.046, "size_variance": 0.026,
        "size_over_life": [[0.0, 0.2], [0.15, 1.0], [0.75, 0.8], [1.0, 0.25]],
        "color": c(ember),
        "color_over_life": [[0.0, c(hot)], [0.3, c(ember)], [1.0, c(mid, 1.0, 0.7)]],
        "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.8, 0.9], [1.0, 0.0]],
        "emissive": r4(3.0 * gh), "render_mode": "stretched_billboard", "velocity_stretch": 0.16,
        "blend": "additive", "drag": 2.3, "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_pull_soft", "f_flutter"],
               "colliders": ["hand_sink"]}))
    add(node("e_mote", "emitter", {
        "shape": "sphere", "radius": 0.3, "position": list(TARGET), "start_time": T_CONNECT,
        "direction": [0.0, 0.0, 0.0], "velocity": 1.4, "velocity_variance": 0.9,
        "rate": channel_rate(s["motes"], 2.2),
    }, layer="motes", inputs={"particle": "ps_mote"}))
    add(node("e_shower", "emitter", {      # the peak: a shower of motes bursts off the chest
        "shape": "sphere", "radius": 0.18, "position": list(TARGET), "start_time": T_PEAK,
        "duration": 0.4, "direction": [0.0, 0.0, 0.0], "velocity": 3.6, "velocity_variance": 2.2,
        "rate": 0.0, "burst_count": s["shower"], "burst_times": [0.02, 0.14],
    }, layer="motes", inputs={"particle": "ps_mote"}))
    drive("particles", "e_mote", "rate")
    drive("target_burst", "e_shower", "burst_count")

    drop_on = s["droplets"] > 0.0
    add(node("ps_droplet", "particle_system", {
        "max_particles": 120, "lifetime": 1.05, "lifetime_variance": 0.12,
        "size": 0.085, "size_variance": 0.04,
        "size_over_life": [[0.0, 0.3], [0.2, 1.0], [0.8, 0.7], [1.0, 0.2]],
        "color": c(mix(mid, deep, 0.3)),
        "color_over_life": [[0.0, c(mix(mid, hot, 0.3))], [0.4, c(mid)], [1.0, c(deep)]],
        "opacity_over_life": [[0.0, 0.0], [0.12, 1.0], [0.85, 0.9], [1.0, 0.0]],
        "emissive": r4(1.5 * g), "render_mode": "stretched_billboard", "velocity_stretch": 0.34,
        "blend": "additive", "drag": 2.0, "soft_particle_distance": 0.05,
    }, inputs={"sprite": "tex_dot", "material": "mat_glow", "forces": ["f_pull_soft", "f_flutter", "f_braid"],
               "colliders": ["hand_sink"]}, enabled=drop_on))
    add(node("e_droplet", "emitter", {
        "shape": "sphere", "radius": 0.22, "position": list(TARGET), "start_time": T_CONNECT,
        "direction": [0.0, 0.0, 0.0], "velocity": 1.0, "velocity_variance": 0.7,
        "rate": channel_rate(max(s["droplets"], 0.1), 2.0),
    }, layer="motes", inputs={"particle": "ps_droplet"}, enabled=drop_on))
    drive("particles", "e_droplet", "rate")

    # ---------------- black-red smoke along the stream and at both anchors ----------------
    smoke_on = s["smoke"] > 0.0
    add(node("ps_smoke", "particle_system", {
        "max_particles": 120, "lifetime": 1.1, "lifetime_variance": 0.3,
        "size": 0.7, "size_variance": 0.25, "size_over_life": [[0.0, 0.45], [0.5, 1.0], [1.0, 1.35]],
        "color": c(mix(smoke, deep, 0.5)), "opacity": r4(min(1.0, 0.5 * p["smoke_gain"])),
        "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.7, 0.6], [1.0, 0.0]],
        "emissive": 0.2, "render_mode": "stretched_billboard", "velocity_stretch": 0.22, "blend": "alpha",
        "sort": True, "drag": 1.2, "rotation_variance": 180.0, "soft_particle_distance": 0.3,
    }, inputs={"sprite": "tex_puff", "material": "mat_smoke", "forces": ["f_pull_soft", "f_flutter", "f_rise"]},
        enabled=smoke_on))
    add(node("e_smoke", "emitter", {       # dark wisps travelling along the stream, round its outside
        "shape": "sphere", "radius": 1.0, "position": offset(MID, 0.6),
        "scale": [2.3, r4(s["streak_radius"] * 1.15), r4(s["streak_radius"] * 1.15)],
        "start_time": T_CONNECT, "direction": list(AXIS), "spread": 20.0,
        "velocity": 2.4, "velocity_variance": 1.0,
        "rate": channel_rate(max(s["smoke"], 0.1), 1.8),
    }, layer="tendrils", inputs={"particle": "ps_smoke"}, enabled=smoke_on))
    # what lingers when the channel has gone: wisps at both anchors
    add(node("ps_linger", "particle_system", {
        "max_particles": 80, "lifetime": 0.85, "lifetime_variance": 0.25,
        "size": 0.5, "size_variance": 0.18, "size_over_life": [[0.0, 0.4], [0.5, 1.0], [1.0, 1.4]],
        "color": c(mix(smoke, deep, 0.45)), "opacity": r4(min(1.0, 0.34 * p["smoke_gain"])),
        "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.6, 0.6], [1.0, 0.0]],
        "emissive": 0.14, "render_mode": "billboard", "blend": "alpha", "sort": True, "drag": 1.6,
        "rotation_variance": 180.0, "angular_velocity_variance": 22.0, "soft_particle_distance": 0.3,
    }, inputs={"sprite": "tex_puff", "material": "mat_smoke", "forces": ["f_flutter", "f_rise"]}))
    for key, centre in (("src", SOURCE), ("tgt", TARGET)):
        add(node(f"e_linger_{key}", "emitter", {
            "shape": "sphere", "radius": 0.22, "position": list(centre), "start_time": T_RELEASE - 0.1,
            "duration": 0.5, "direction": [0.0, 1.0, 0.0], "spread": 70.0, "velocity": 0.5,
            "velocity_variance": 0.3,
            "rate": env([(T_RELEASE - 0.1, 0.0), (T_RELEASE, 16.0), (T_GONE, 12.0), (T_GONE + 0.12, 0.0)],
                        s["hand"]),
        }, layer="aftermath", inputs={"particle": "ps_linger"}))

    # ---------------- the target: a wound of light ----------------
    flare = s["flare"]
    add(node("ps_flare", "particle_system", {     # a ragged red glow: a few overlapping torn quads
        "max_particles": 16, "lifetime": 0.3, "lifetime_variance": 0.08,
        "size": r4(1.5 * flare), "size_variance": r4(0.3 * flare),
        "size_over_life": [[0.0, 0.65], [0.4, 1.0], [1.0, 0.9]],
        "color": c(mix(mid, hot, 0.2)), "opacity": 0.55,
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": r4(1.6 * g), "render_mode": "billboard", "blend": "additive",
        "rotation_variance": 180.0, "angular_velocity_variance": 40.0, "soft_particle_distance": 0.4,
    }, inputs={"sprite": "tex_puff", "material": "mat_glow"}))
    add(node("e_flare", "emitter", {
        "shape": "sphere", "radius": 0.06, "position": list(TARGET), "velocity": 0.0,
        "start_time": T_CONNECT - 0.03,
        "rate": env([(T_CONNECT - 0.03, 0.0), (T_CONNECT, 24.0), (T_SUSTAIN, 11.0), (T_PEAK, 11.0),
                     (T_PEAK + 0.1, 22.0), (T_PEAK + 0.3, 18.0), (T_RELEASE - 0.06, 9.0), (T_RELEASE - 0.02, 0.0)]),
    }, layer="target", inputs={"particle": "ps_flare"}))
    add(node("ps_flare_core", "particle_system", {   # the small hot heart of the wound
        "max_particles": 12, "lifetime": 0.24, "lifetime_variance": 0.06,
        "size": r4(0.4 * flare), "size_variance": r4(0.08 * flare),
        "size_over_life": [[0.0, 0.7], [0.4, 1.0], [1.0, 0.8]],
        "color": c(hot), "opacity": 0.7,
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": r4(2.6 * gh), "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.3,
    }, inputs={"sprite": "tex_glow", "material": "mat_spark"}))
    add(node("e_flare_core", "emitter", {
        "shape": "point", "position": list(TARGET), "velocity": 0.0, "start_time": T_CONNECT - 0.03,
        "rate": env([(T_CONNECT - 0.03, 0.0), (T_CONNECT, 24.0), (T_SUSTAIN, 11.0), (T_PEAK, 11.0),
                     (T_PEAK + 0.1, 22.0), (T_PEAK + 0.3, 18.0), (T_RELEASE - 0.06, 9.0), (T_RELEASE - 0.02, 0.0)]),
    }, layer="target", inputs={"particle": "ps_flare_core"}))
    # broad soft rays under the thin ones: irregular wedges of glow, slower to change
    add(node("ps_ray_soft", "particle_system", {
        "max_particles": 48, "lifetime": 0.4, "lifetime_variance": 0.15,
        "size": r4(s["ray_size"] * 3.0), "size_variance": r4(s["ray_size"] * 1.4),
        "size_over_life": [[0.0, 0.4], [0.35, 1.0], [1.0, 0.6]],
        "color": c(mix(mid, hot, 0.15)), "opacity": 0.6,
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.65, 0.7], [1.0, 0.0]],
        "emissive": r4(1.1 * g), "render_mode": "stretched_billboard", "velocity_stretch": 3.2,
        "blend": "additive", "drag": 1.0,
    }, inputs={"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_ray_soft", "emitter", {
        "shape": "sphere", "radius": r4(0.3 * flare), "surface_only": True, "position": list(TARGET),
        "direction": [0.0, 0.0, 0.0], "start_time": T_CONNECT - 0.02,
        "velocity": env([(T_CONNECT - 0.02, 1.4), (T_SUSTAIN, 0.8), (T_PEAK, 0.8), (T_PEAK + 0.1, 1.7),
                         (T_PEAK + 0.3, 1.5), (T_RELEASE, 0.7)]),
        "velocity_variance": 0.5,
        "rate": env([(T_CONNECT - 0.02, 0.0), (T_CONNECT, 2.0), (T_SUSTAIN, 1.0), (T_PEAK, 1.0),
                     (T_PEAK + 0.08, 2.6), (T_PEAK + 0.3, 2.2), (T_RELEASE - 0.04, 0.8), (T_RELEASE, 0.0)],
                    s["soft_rays"]),
    }, layer="target", inputs={"particle": "ps_ray_soft"}))
    # the rays: short-lived, velocity-stretched, random in length, direction and brightness
    add(node("ps_ray", "particle_system", {
        "max_particles": 90, "lifetime": 0.24, "lifetime_variance": 0.12,
        "size": s["ray_size"], "size_variance": r4(s["ray_size"] * 0.55),
        "size_over_life": [[0.0, 0.35], [0.3, 1.0], [1.0, 0.5]],
        "color": c(mix(hot, mid, 0.3)),
        "color_over_life": [[0.0, c(hot)], [0.5, c(mix(hot, mid, 0.55))], [1.0, c(mid)]],
        "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.6, 0.7], [1.0, 0.0]],
        "emissive": r4(2.0 * gh), "render_mode": "stretched_billboard", "velocity_stretch": 6.0,
        "blend": "additive", "drag": 1.0,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark"}))
    add(node("e_ray", "emitter", {
        "shape": "sphere", "radius": r4(0.2 * flare), "surface_only": True, "position": list(TARGET),
        "direction": [0.0, 0.0, 0.0], "start_time": T_CONNECT - 0.02,
        "velocity": env([(T_CONNECT - 0.02, 1.6), (T_SUSTAIN, 0.9), (T_PEAK, 0.9), (T_PEAK + 0.1, 1.9),
                         (T_PEAK + 0.3, 1.7), (T_RELEASE, 0.8)]),
        "velocity_variance": 0.6,
        "rate": env([(T_CONNECT - 0.02, 0.0), (T_CONNECT, 2.2), (T_SUSTAIN, 1.0), (T_PEAK, 1.0),
                     (T_PEAK + 0.08, 3.0), (T_PEAK + 0.3, 2.6), (T_RELEASE - 0.04, 0.8), (T_RELEASE, 0.0)],
                    s["rays"]),
    }, layer="target", inputs={"particle": "ps_ray"}))
    add(node("ps_spark", "particle_system", {
        "max_particles": 260, "lifetime": 0.5, "lifetime_variance": 0.2,
        "size": 0.03, "size_variance": 0.014, "size_over_life": [[0.0, 1.0], [0.6, 0.7], [1.0, 0.1]],
        "color": c(hot), "color_over_life": [[0.0, c(hot)], [0.4, c(ember)], [1.0, c(mid, 1.0, 0.6)]],
        "opacity_over_life": [[0.0, 1.0], [0.7, 0.9], [1.0, 0.0]],
        "emissive": r4(3.4 * gh), "render_mode": "stretched_billboard", "velocity_stretch": 0.11,
        "blend": "additive", "drag": 2.6, "bounce": 0.2,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_gravity", "f_flutter"],
               "colliders": ["ground"]}))
    add(node("e_spark", "emitter", {
        "shape": "sphere", "radius": 0.1, "position": list(TARGET), "direction": [0.0, 0.0, 0.0],
        "start_time": T_CONNECT - 0.02, "velocity": 3.4, "velocity_variance": 2.0,
        "burst_count": int(round(s["sparks"] * 1.6)), "burst_times": [0.02],
        "rate": env([(T_CONNECT - 0.02, 0.0), (T_CONNECT + 0.05, 1.4), (T_SUSTAIN, 1.0), (T_PEAK, 1.0),
                     (T_PEAK + 0.1, 3.4), (T_PEAK + 0.3, 2.6), (T_RELEASE, 0.6), (T_RELEASE + 0.06, 0.0)],
                    s["sparks"]),
    }, layer="target", inputs={"particle": "ps_spark"}))
    drive("target_burst", "ps_flare", "size")
    drive("target_burst", "ps_flare_core", "size")
    drive("target_burst", "ps_ray", "size")
    drive("target_burst", "ps_ray_soft", "size")
    drive("target_burst", "e_ray", "rate")
    drive("target_burst", "e_ray_soft", "rate")
    drive("target_burst", "e_spark", "rate")
    drive("particles", "e_spark", "burst_count")

    # ---------------- the source: a contained vortex in the hand ----------------
    hand = s["hand"]
    add(node("ps_hand", "particle_system", {
        "max_particles": 12, "lifetime": 0.6, "lifetime_variance": 0.1,
        "size": r4(0.95 * hand), "size_variance": r4(0.15 * hand),
        "size_over_life": [[0.0, 0.75], [0.5, 1.0], [1.0, 0.8]],
        "color": c(mix(mid, hot, 0.22)), "opacity": 0.5,
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": r4(1.3 * g), "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.4,
    }, inputs={"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_hand", "emitter", {
        "shape": "point", "position": list(SOURCE), "velocity": 0.0,
        "rate": env([(0.0, 0.0), (0.06, 3.0), (T_SHOOT, 6.0), (T_SUSTAIN, 5.0), (T_PEAK, 5.0),
                     (T_PEAK + 0.15, 8.0), (T_RELEASE, 6.0), (T_GONE, 5.0), (T_GONE + 0.15, 0.0)]),
    }, layer="hand", inputs={"particle": "ps_hand"}))
    add(node("ps_hand_core", "particle_system", {
        "max_particles": 12, "lifetime": 0.4, "lifetime_variance": 0.1,
        "size": r4(0.36 * hand), "size_variance": r4(0.06 * hand),
        "size_over_life": [[0.0, 0.7], [0.4, 1.0], [1.0, 0.8]],
        "color": c(hot), "opacity": 0.65,
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.7, 0.8], [1.0, 0.0]],
        "emissive": r4(2.2 * gh), "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.3,
    }, inputs={"sprite": "tex_glow", "material": "mat_spark"}))
    add(node("e_hand_core", "emitter", {
        "shape": "point", "position": list(SOURCE), "velocity": 0.0,
        "rate": env([(0.0, 0.0), (0.1, 3.0), (T_SHOOT, 7.0), (T_SUSTAIN, 6.0), (T_PEAK, 6.0),
                     (T_PEAK + 0.15, 10.0), (T_RELEASE, 7.0), (T_GONE, 6.0), (T_GONE + 0.12, 0.0)]),
    }, layer="hand", inputs={"particle": "ps_hand_core"}))
    # small arcs circling the palm: the vortices give the speed, the attractor the orbit
    add(node("ps_arc", "particle_system", {
        "max_particles": 90, "lifetime": 0.6, "lifetime_variance": 0.2, "size": 0.02,
        "render_mode": "none", "drag": 5.5,
    }, inputs={"forces": ["f_hand_swirl", "f_hand_swirl2", "f_hand_hold"]}))
    add(node("trail_arc", "trail", {
        "lifetime": 0.19, "max_segments": 18, "min_vertex_distance": 0.02,
        "taper": [[0.0, 0.0], [0.25, 1.0], [1.0, 0.0]],
        "opacity_over_life": [[0.0, 1.0], [0.6, 0.7], [1.0, 0.0]],
        "color": c(mix(hot, mid, 0.3)), "blend": "additive", "width": r4(0.06 * (0.6 + 0.4 * hand)),
        "emissive": env([(0.0, 0.6), (T_SHOOT, 1.6), (T_PEAK, 1.5), (T_PEAK + 0.15, 2.4), (T_GONE, 1.4),
                         (T_END, 0.0)], gh),
    }, layer="hand", inputs={"source": "ps_arc", "material": "mat_filament"}))
    add(node("e_arc", "emitter", {
        "shape": "sphere", "radius": 0.34, "surface_only": True, "position": list(SOURCE),
        "direction": [0.0, 0.0, 0.0], "velocity": 0.5, "velocity_variance": 0.4,
        "rate": env([(0.0, 0.0), (0.05, 0.6), (T_SHOOT, 1.0), (T_PEAK, 1.0), (T_PEAK + 0.15, 1.6),
                     (T_GONE, 0.9), (T_GONE + 0.16, 0.0)], s["arcs"]),
    }, layer="hand", inputs={"particle": "ps_arc"}))
    # the cast: embers gather into the palm before the stream leaves it
    add(node("ps_gather", "particle_system", {
        "max_particles": 160, "lifetime": 0.34, "lifetime_variance": 0.08,
        "size": 0.04, "size_variance": 0.02, "size_over_life": [[0.0, 0.4], [0.5, 1.0], [1.0, 0.3]],
        "color": c(ember), "color_over_life": [[0.0, c(mid)], [1.0, c(hot)]],
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.85, 1.0], [1.0, 0.0]],
        "emissive": r4(2.8 * gh), "render_mode": "stretched_billboard", "velocity_stretch": 0.2,
        "blend": "additive", "drag": 0.4,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_hand_swirl"],
               "colliders": ["hand_sink"]}))
    add(node("e_gather", "emitter", {
        "shape": "sphere", "radius": 0.95, "surface_only": True, "position": list(SOURCE),
        "direction": [0.0, 0.0, 0.0], "velocity": 0.0, "radial_velocity": -2.9, "duration": 0.36,
        "rate": env([(0.0, 0.0), (0.03, 190.0), (0.24, 150.0), (0.34, 0.0)], hand),
    }, layer="hand", inputs={"particle": "ps_gather"}))
    drive("particles", "e_gather", "rate")
    drive("particles", "e_arc", "rate")

    # ---------------- the signature element of each palette ----------------
    # life: heavy blood droplets arcing off the chest; corruption: spores rising
    # off the victim; soul: pale ghostly wisps dragged out of the chest.
    kind = p["sign_kind"]
    sign_amt = s["sign"]
    sign_on = sign_amt > 0.0
    add(node("ps_spore", "particle_system", {
        "max_particles": 160, "lifetime": 1.5, "lifetime_variance": 0.4,
        "size": 0.05, "size_variance": 0.03, "size_over_life": [[0.0, 0.2], [0.3, 1.0], [1.0, 0.4]],
        "color": c(sign), "color_over_life": [[0.0, c(hot)], [0.4, c(sign)], [1.0, c(mid, 1.0, 0.5)]],
        "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.45, 0.5], [0.65, 1.0], [1.0, 0.0]],
        "emissive": r4(2.4 * gh), "render_mode": "billboard", "blend": "additive", "drag": 1.2,
    }, inputs={"sprite": "tex_dot", "material": "mat_spark", "forces": ["f_rise", "f_flutter"]},
        enabled=sign_on and kind == "spores"))
    add(node("e_spore", "emitter", {
        "shape": "sphere", "radius": 0.5, "scale": [1.0, 1.4, 1.0], "position": list(BODY),
        "start_time": T_CONNECT, "direction": [0.0, 1.0, 0.0], "spread": 35.0, "velocity": 0.5,
        "velocity_variance": 0.3, "rate": channel_rate(26.0 * max(sign_amt, 0.1), 1.6),
    }, layer="signature", inputs={"particle": "ps_spore"}, enabled=sign_on and kind == "spores"))
    add(node("ps_blood", "particle_system", {
        "max_particles": 160, "lifetime": 0.8, "lifetime_variance": 0.25,
        "size": 0.06, "size_variance": 0.03, "size_over_life": [[0.0, 0.5], [0.2, 1.0], [1.0, 0.5]],
        "color": c(sign), "color_over_life": [[0.0, c(mix(sign, hot, 0.3))], [1.0, c(deep)]],
        "opacity_over_life": [[0.0, 0.0], [0.1, 1.0], [0.8, 0.9], [1.0, 0.0]],
        "emissive": r4(1.7 * g), "render_mode": "stretched_billboard", "velocity_stretch": 0.3,
        "blend": "additive", "drag": 0.8, "kill_on_collision": True,
    }, inputs={"sprite": "tex_dot", "material": "mat_glow", "forces": ["f_gravity", "f_pull_soft"],
               "colliders": ["ground", "hand_sink"]}, enabled=sign_on and kind == "droplets"))
    add(node("e_blood", "emitter", {
        "shape": "sphere", "radius": 0.16, "position": list(TARGET), "start_time": T_CONNECT,
        "direction": [-0.5, 1.0, 0.0], "spread": 55.0, "velocity": 2.6, "velocity_variance": 1.3,
        "rate": channel_rate(22.0 * max(sign_amt, 0.1), 2.4),
    }, layer="signature", inputs={"particle": "ps_blood"}, enabled=sign_on and kind == "droplets"))
    wisp_on = sign_on and kind == "wisps"
    add(node("ps_wisp", "particle_system", {
        "max_particles": 48, "lifetime": 1.6, "lifetime_variance": 0.08, "size": 0.02,
        "render_mode": "none", "drag": 3.6, "bounce": 0.0, "friction": 1.0, "collision_radius": 0.01,
    }, inputs={"forces": ["f_pull_soft", "f_funnel", "f_meander", "f_flutter", "f_braid"], "colliders": ["hand_hold"]},
        enabled=wisp_on))
    add(node("trail_wisp", "trail", {
        "lifetime": 0.55, "max_segments": 48, "min_vertex_distance": 0.05,
        "taper": [[0.0, 0.0], [0.12, 1.0], [0.5, 0.6], [1.0, 0.0]],
        "opacity_over_life": [[0.0, 0.55], [0.4, 0.4], [1.0, 0.0]],
        "color": c(sign), "blend": "additive", "start_time": T_CONNECT, "width": 0.11,
        "emissive": env([(T_CONNECT, 0.5), (T_PEAK, 0.5), (T_PEAK + 0.15, 0.9), (T_END, 0.2)]),
    }, layer="signature", inputs={"source": "ps_wisp", "material": "mat_filament"}, enabled=wisp_on))
    add(node("e_wisp", "emitter", {
        "shape": "sphere", "radius": 0.3, "scale": [1.0, 1.5, 1.0], "position": list(BODY),
        "start_time": T_CONNECT, "direction": [-0.4, 1.0, 0.0], "spread": 60.0, "velocity": 2.2,
        "velocity_variance": 1.0, "rate": channel_rate(5.0 * max(sign_amt, 0.1), 1.8),
    }, layer="signature", inputs={"particle": "ps_wisp"}, enabled=wisp_on))
    for eid in ("e_spore", "e_blood", "e_wisp"):
        drive("particles", eid, "rate")

    # ---------------- particle elements ripped off the whole target ----------------
    # The victim is a capsule about 0.5 m in radius standing 0.4 .. 1.9 m high; it is
    # never drawn, only emitted from.  Every style tears motes off it; the thick
    # beam adds droplets and smoke.
    body = s["body"]
    extras = bool(s["body_extras"])
    add(node("e_body_mote", "emitter", {
        "shape": "sphere", "radius": 0.5, "scale": [1.0, 1.5, 1.0], "surface_only": True,
        "position": list(BODY), "start_time": T_CONNECT, "direction": [0.0, 0.0, 0.0],
        "velocity": 0.8, "velocity_variance": 0.5, "rate": channel_rate(64.0 * body, 2.4),
    }, layer="motes", inputs={"particle": "ps_mote"}))
    add(node("e_body_droplet", "emitter", {
        "shape": "sphere", "radius": 0.45, "scale": [1.0, 1.5, 1.0], "surface_only": True,
        "position": list(BODY), "start_time": T_CONNECT, "direction": [0.0, 0.0, 0.0],
        "velocity": 0.6, "velocity_variance": 0.4, "rate": channel_rate(18.0, 2.0),
    }, layer="motes", inputs={"particle": "ps_droplet"}, enabled=extras))
    add(node("e_body_smoke", "emitter", {
        "shape": "sphere", "radius": 0.45, "scale": [1.0, 1.5, 1.0], "position": list(BODY),
        "start_time": T_CONNECT, "direction": [-0.6, 0.5, 0.0], "spread": 50.0, "velocity": 0.7,
        "velocity_variance": 0.4, "rate": channel_rate(11.0, 1.6),
    }, layer="tendrils", inputs={"particle": "ps_smoke"}, enabled=extras))
    for eid in ("e_body_mote", "e_body_droplet", "e_body_smoke", "e_smoke"):
        drive("particles", eid, "rate")

    # ---------------- volumetric haze (thick, and always behind Soul Drain's black strands) -------
    haze_k = 1.0 if thick else 0.55
    add(node("haze_stream", "volume", {   # a column laid along the stream; `climb` flows toward the hand
        "mode": "procedural", "volume_type": "magic", "shape": "column",
        "position": list(MID), "rotation": [0.0, 0.0, 90.0],
        "radius": r4(0.34 + 0.2 * haze_k), "height": 5.5,
        "start_time": T_SHOOT + 0.05, "duration": r4(T_GONE + 0.2 - T_SHOOT),
        "density": env([(T_SHOOT + 0.05, 0.0), (T_SUSTAIN, 0.75), (T_PEAK, 0.75), (T_PEAK + 0.2, 1.15),
                        (T_RELEASE, 0.8), (T_GONE + 0.2, 0.0)], haze_k),
        "emission": r4(0.85 * g), "color": c(deep), "color_hot": c(mix(mid, deep, 0.35)),
        "filament_scale": 2.6, "strands": 0.75, "carve": 0.5, "softness": 0.95,
        "twist": 0.5, "spin": 0.0, "climb": 1.7, "scatter": 0.3, "march_steps": 12,
    }, layer="haze", enabled=haze_on))
    add(node("haze_target", "volume", {
        "mode": "procedural", "volume_type": "magic", "shape": "nebula",
        "position": list(BODY), "radius": 0.95, "height": 2.1,
        "start_time": T_CONNECT - 0.05, "duration": r4(T_END - T_CONNECT),
        "density": env([(T_CONNECT - 0.05, 0.0), (T_SUSTAIN, 0.7), (T_PEAK, 0.7), (T_PEAK + 0.2, 1.2),
                        (T_RELEASE, 0.8), (T_END - 0.06, 0.0)]),
        "emission": r4(0.9 * g), "color": c(deep), "color_hot": c(mix(mid, hot, 0.2)),
        "filament_scale": 3.0, "strands": 0.7, "carve": 0.52, "softness": 0.9,
        "twist": 0.6, "spin": 0.12, "climb": 0.35, "scatter": 0.35, "march_steps": 14,
    }, layer="haze", enabled=thick))
    drive("flow_speed", "haze_stream", "climb")

    # ---------------- lights ----------------
    light = s["light"]
    add(node("l_hand", "light", {
        "light_type": "point", "position": offset(SOURCE, 0.25, 0.1, 0.55), "color": c(p["light"]),
        "radius": 3.6, "flicker_amplitude": 0.12, "flicker_frequency": 9.0,
        "intensity": env([(0.0, 0.0), (0.12, 0.8), (T_SHOOT, 1.6), (T_SUSTAIN, 1.8), (T_PEAK, 1.8),
                          (T_PEAK + 0.2, 3.6), (T_RELEASE, 2.4), (T_GONE, 1.4), (T_END - 0.04, 0.0)], g * light),
    }, layer="light"))
    add(node("l_target", "light", {
        "light_type": "point", "position": offset(TARGET, -0.25, 0.1, 0.6), "color": c(p["light"]),
        "radius": 3.8, "flicker_amplitude": 0.16, "flicker_frequency": 11.0,
        "intensity": env([(T_CONNECT - 0.04, 0.0), (T_CONNECT + 0.02, 4.0), (T_SUSTAIN, 2.0), (T_PEAK, 2.0),
                          (T_PEAK + 0.12, 6.0), (T_PEAK + 0.3, 4.6), (T_RELEASE, 1.8), (T_GONE, 0.5),
                          (T_END - 0.06, 0.0)], g * light),
    }, layer="light"))
    add(node("l_stream", "light", {
        "light_type": "point", "position": offset(MID, 0.0, -0.2, 0.5), "color": c(p["light"]),
        "radius": 3.6,
        "intensity": env([(T_SHOOT, 0.0), (T_SUSTAIN, 0.7), (T_PEAK, 0.7), (T_PEAK + 0.2, 1.5),
                          (T_RELEASE, 0.8), (T_GONE, 0.0)], g * light),
    }, layer="light"))
    for lid in ("l_hand", "l_stream"):
        drive("stream_intensity", lid, "intensity")
    drive("target_burst", "l_target", "intensity")

    # ---------------- camera ----------------
    add(node("cam", "camera", {"position": [2.4, 2.7, 9.0], "target": [0.0, 1.0, 0.0], "fov": 40.0}))

    controls = [
        {"id": "stream_intensity", "label": "Stream glow", "group": "Energy stream",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": bind["stream_intensity"]},
        {"id": "stream_width", "label": "Stream width", "group": "Energy stream",
         "min": 0.3, "max": 2.5, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": bind["stream_width"]},
        {"id": "flow_speed", "label": "Flow speed", "group": "Energy stream",
         "min": 0.4, "max": 2.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": bind["flow_speed"]},
        {"id": "turbulence", "label": "Turbulence", "group": "Energy stream",
         "min": 0.0, "max": 2.5, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": bind["turbulence"]},
        {"id": "target_burst", "label": "Target burst", "group": "Target flare",
         "min": 0.0, "max": 2.5, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": bind["target_burst"]},
        {"id": "particles", "label": "Particle amount", "group": "Embers and motes",
         "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": bind["particles"]},
        {"id": "global_speed", "label": "Speed", "group": "Global",
         "min": 0.25, "max": 4.0, "default": 1.0, "value": 1.0, "step": 0.05, "unit": "x",
         "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]},
    ]

    tags = ["channel", "drain", "beam", p["key"]] + STYLE_TAGS[style_key] + ["target"]
    return {
        "schema_version": SCHEMA_VERSION,
        "name": spec["name"],
        "description": spec["description"],
        "duration": DURATION,
        "seed": seed,
        "timeline": {"phases": [{"name": n, "start": a, "end": b} for n, a, b in PHASES]},
        "layers": [
            {"id": "ground", "name": "Ground circles", "role": "telegraph"},
            {"id": "hand", "name": "Source hand", "role": "ignition"},
            {"id": "stream", "name": "Energy stream", "role": "primary"},
            {"id": "tendrils", "name": "Smoke tendrils", "role": "primary"},
            {"id": "flow", "name": "Flowing streaks", "role": "secondary"},
            {"id": "target", "name": "Target flare", "role": "interaction"},
            {"id": "motes", "name": "Embers and motes", "role": "secondary"},
            {"id": "signature", "name": "Droplets, spores, wisps", "role": "secondary"},
            {"id": "haze", "name": "Volumetric haze", "role": "secondary"},
            {"id": "aftermath", "name": "Lingering wisps", "role": "aftermath"},
            {"id": "light", "name": "Lighting", "role": "interaction"},
        ],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/drain_variants.py",
            "variant": slug,
            "palette": p["key"],
            "style": style_key,
            "analysis": "cast 0-0.3 embers gather into a glow in the caster's hand, a thin ground circle "
                        "ignites under the caster | channel_start 0.3-0.6 the stream shoots across, connects "
                        "and a flare ignites on the target's chest | sustain 0.6-2.0 braided strands undulate "
                        "toward the caster while pulses, filaments, smoke tendrils, embers and droplets "
                        "travel from the target into the hand | peak_drain 2.0-2.4 the stream thickens and "
                        "brightens, the flare bursts into many irregular rays and a shower of motes | "
                        "release 2.4-2.7 the stream breaks from the target and is drawn into the hand | "
                        "fade 2.7-3.0 lingering motes and smoke wisps at both anchors",
            "layout": {
                "source": list(SOURCE), "target": list(TARGET), "ground": 0.0,
                "note": "Two anchors and no characters: attach `source` to the caster's hand and `target` "
                        "to the victim's chest. Everything flows from the target to the source.",
            },
            "loop": {
                "start": LOOP_START, "end": LOOP_END,
                "note": "Hold the channel by keeping effect time inside the sustain phase: every keyframe "
                        "track is steady or periodic there, the strands' undulation (noise_loop) and their "
                        "pulses repeat exactly every 1.4 s, and the emitters run at constant rates, so a "
                        "runtime may wrap time from 2.0 s back to 0.6 s while the simulation keeps stepping, "
                        "then let it run on into peak_drain and release when the channel ends.",
            },
            "render_settings": {
                "background": [0.0, 0.0, 0.0, 1.0],
                "ground_albedo": 0.035,
                "bloom_intensity": 0.2,
                "bloom_radius": 0.04,
                "exposure": 0.85,
                "grid": False,
            },
            "tags": tags,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="directory to write the nine effect JSON files into")
    ap.add_argument("--only", default=None, help="comma-separated slugs (default: all nine)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = set(args.only.split(",")) if args.only else None
    for slug, spec in VARIANTS.items():
        if wanted is not None and slug not in wanted:
            continue
        effect = build(slug, spec)
        path = out / f"{slug}.json"
        path.write_text(json.dumps(effect, indent=1) + "\n", encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes, {len(effect['controls'])} controls)")


if __name__ == "__main__":
    main()
