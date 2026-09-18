#!/usr/bin/env python3
"""Generate the six Power Up effects (a quick, subtle one-shot buff) from one parametrised description.

    python tools/generators/power_up.py --out examples/effects
    python tools/generators/power_up.py --check examples/effects

"Strength reveals itself quietly": slim energy streams wrap and spiral up around the character in a
quick one-shot and a few soft motes rise with them. No ground base, nothing above the head, no drawn body. The origin is
the character's FEET at y = 0; the look is authored for a character about 1.8 m tall with a body
radius of about 0.3 m, and the Character height control scales it for bigger or smaller ones.

Every variant shares the same node ids, animation and controls and differs only by its palette
(particles, trails, the light and the materials all read from it).

Layers
------
* STREAMS  five slim ribbons, each a thin bright core trail over a wide soft glow trail, drawn by an
           invisible bead on a parent chain: anchor -> tilt (a small lean of the helix axis, which
           stays on the body axis) -> spin (keyframed Y rotation: the orbit angle) -> bead (keyframed
           local [radius, height, 0]). Each bead flies once from the ankles to just above the head on
           its own radius, pitch, phase and speed, about 1.2 turns a second, so every visible ribbon
           covers about one full turn and the body is wrapped on both sides at once. One or two
           strands are bright, the rest fainter and thinner. At the top the trail's width, emissive
           and colour fade to zero, so the ribbon dissolves upward. Rotation is linear between keys,
           so the arcs are exact circles, never polygons.
* MOTES    sparse soft motes rising on a tight ellipsoid shell around the body, a short burst of
           faster ones out of the ankles during the rise, and small glints riding along every ribbon.
* LIGHT    one faint point light at chest height.

Timing (a one-shot: "very quick, a few seconds max")
------
rise 0-0.6 s (the streams climb out of the ankles), surge 0.6-1.65 s (the body is wrapped at full
strength), dissolve 1.65-2.3 s (the ribbons dissolve upward at head height), fade 2.3-2.8 s (the
last glints and motes fade). The Speed control plays it faster or slower.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# palettes - every colour in the effect comes from one of these
# ---------------------------------------------------------------------------

HOLY = {"hot": [1.00, 0.88, 0.62], "mid": [1.00, 0.62, 0.22], "deep": [0.86, 0.34, 0.07]}
ARCANE = {"hot": [0.42, 0.62, 1.00], "mid": [0.10, 0.30, 1.00], "deep": [0.06, 0.10, 0.80]}
NATURE = {"hot": [0.80, 1.00, 0.64], "mid": [0.34, 0.92, 0.26], "deep": [0.08, 0.52, 0.10]}
VOID = {"hot": [0.80, 0.54, 1.00], "mid": [0.60, 0.26, 1.00], "deep": [0.34, 0.08, 0.78]}
FIRE = {"hot": [1.00, 0.52, 0.26], "mid": [1.00, 0.18, 0.04], "deep": [0.70, 0.05, 0.01]}
ICE = {"hot": [0.66, 0.96, 1.00], "mid": [0.36, 0.86, 1.00], "deep": [0.16, 0.58, 0.84]}

BASE_TAGS = ["buff", "support", "power_up", "aura", "target"]

VARIANTS: dict[str, dict[str, Any]] = {
    "power_up": {
        "name": "Power Up", "palette": HOLY, "seed": 71401, "theme": "holy",
        "flavour": "holy, warm gold",
        "tags": BASE_TAGS + ["holy", "gold"],
    },
    "power_up_arcane": {
        "name": "Power Up Arcane", "palette": ARCANE, "seed": 71402, "theme": "arcane",
        "flavour": "mana, royal electric blue",
        "tags": BASE_TAGS + ["arcane", "mana", "blue"],
    },
    "power_up_nature": {
        "name": "Power Up Nature", "palette": NATURE, "seed": 71403, "theme": "nature",
        "flavour": "vitality, fresh green",
        "tags": BASE_TAGS + ["nature", "vitality", "green"],
    },
    "power_up_void": {
        "name": "Power Up Void", "palette": VOID, "seed": 71404, "theme": "void",
        "flavour": "mystic, violet",
        "tags": BASE_TAGS + ["void", "mystic", "violet"],
    },
    "power_up_fire": {
        "name": "Power Up Fire", "palette": FIRE, "seed": 71405, "theme": "fire",
        "flavour": "rage, red-orange",
        "tags": BASE_TAGS + ["fire", "rage", "red"],
    },
    "power_up_ice": {
        "name": "Power Up Ice", "palette": ICE, "seed": 71406, "theme": "ice",
        "flavour": "focus, pale icy cyan",
        "tags": BASE_TAGS + ["ice", "focus", "cyan"],
    },
}

# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------

DURATION = 2.8              # one shot: the last glint and mote are gone by here
TRAIL_LIFE = 0.9            # how long a stretch of ribbon stays visible behind the bead
FADE_OUT = 0.55             # the dissolve at the top of a flight
KEY_STEP = 0.05             # key spacing of the orbit / height tracks inside a flight

# One entry per bead: radius (m), turns per flight, heights y0 -> y1 (m), flight time V (s), start
# time (s), start phase (deg), brightness gain (one or two hero strands, the rest fainter and thinner),
# radius wobble (m) and its frequency (per flight), helix-axis lean (deg about x, z).
STREAMS = [
    {"id": "a", "radius": 0.46, "turns": 2.05, "y0": 0.15, "y1": 1.92, "v": 1.7, "start": 0.05, "phase": 20.0,
     "gain": 1.0, "wobble": 0.05, "wobble_f": 1.3, "lean": [2.0, -1.5]},
    {"id": "b", "radius": 0.56, "turns": 2.4, "y0": 0.2, "y1": 1.85, "v": 2.0, "start": 0.45, "phase": 1.0,
     "gain": 0.55, "wobble": 0.06, "wobble_f": 0.9, "lean": [-1.5, 2.0]},
    {"id": "c", "radius": 0.40, "turns": 1.6, "y0": 0.15, "y1": 1.95, "v": 1.35, "start": 0.0, "phase": 110.0,
     "gain": 0.8, "wobble": 0.04, "wobble_f": 1.7, "lean": [1.0, 2.0]},
    {"id": "d", "radius": 0.36, "turns": 2.0, "y0": 0.3, "y1": 1.88, "v": 1.65, "start": 0.6, "phase": 129.0,
     "gain": 0.5, "wobble": 0.05, "wobble_f": 1.2, "lean": [-1.0, -2.0]},
    {"id": "e", "radius": 0.52, "turns": 2.3, "y0": 0.15, "y1": 1.9, "v": 1.9, "start": 0.25, "phase": 184.0,
     "gain": 0.7, "wobble": 0.05, "wobble_f": 0.8, "lean": [1.5, 1.5]},
]

CORE_WIDTH = 0.015
GLOW_WIDTH = 0.12
CORE_EMISSIVE = 1.7
GLOW_EMISSIVE = 0.8
SHED_RATE = 8.0

MOTE_RATE = 14.0

# Opacity along a ribbon: a visible ribbon spans about 1.2 turns, so a ripple with a period of about
# one turn in normalised age reads as the strand dimming as it swings round the far side.
RIPPLE_CORE = [[0.0, 1.0], [0.2, 0.9], [0.42, 0.5], [0.64, 0.85], [0.86, 0.45], [1.0, 0.0]]
RIPPLE_GLOW = [[0.0, 1.0], [0.2, 0.85], [0.42, 0.5], [0.64, 0.8], [0.86, 0.4], [1.0, 0.0]]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def lum(rgb: list[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def gain(p: dict[str, Any], key: str = "mid") -> float:
    """Equalise stage brightness across palettes: blue and violet are far less luminous than gold
    at equal RGB, so they get a little more emissive, and pale ice a little less."""
    return round((lum(HOLY[key]) / lum(p[key])) ** 0.6, 4)


def c(rgb: list[float], a: float = 1.0) -> list[float]:
    return [round(rgb[0], 4), round(rgb[1], 4), round(rgb[2], 4), a]


def tr(pairs: list[tuple[float, Any]]) -> dict[str, Any]:
    keys = [{"time": round(t, 4), "value": v} for t, v in pairs]
    return {"value": pairs[0][1], "track": keys}


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


def op(nid: str, kind: str, params: dict[str, Any] | None = None,
       inputs: dict[str, str] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": nid, "op": kind}
    if params:
        entry["params"] = params
    if inputs:
        entry["inputs"] = inputs
    return entry


def tex(nid: str, w: int, h: int, nodes: list[dict[str, Any]], output: str) -> dict[str, Any]:
    return node(nid, "texture", {"width": w, "height": h, "graph": {"nodes": nodes, "output": output}})


def flights(s: dict[str, Any]) -> list[float]:
    """Absolute start times of a stream's flights (one each: the effect is a one-shot)."""
    return [s["start"]]


EASE_IN = 0.25             # seconds for a bead to come up to speed at the start of a flight


def progress(s: dict[str, Any], u: float) -> float:
    """Eased flight progress: the bead starts at rest and comes up to speed over EASE_IN, so a young
    ribbon (whose oldest vertex is not yet old enough for the taper to close it) stays short and dim
    while it fades in, and no flat tail end ever shows."""
    def p(x: float) -> float:
        return x - EASE_IN * (1.0 - math.exp(-x / EASE_IN))
    return p(u * s["v"]) / p(s["v"])


def pose(s: dict[str, Any], u: float) -> tuple[float, list[float]]:
    """(orbit angle in degrees, local [radius, height, 0]) at flight fraction u in [0, 1]."""
    u = progress(s, u)
    ease = 0.9 * u + 0.1 * (u * u * (3.0 - 2.0 * u))
    y = s["y0"] + (s["y1"] - s["y0"]) * ease
    r = s["radius"] * (1.0 - 0.2 * u) + s["wobble"] * math.sin(2.0 * math.pi * s["wobble_f"] * u + s["phase"] * 0.017)
    ang = s["phase"] + 360.0 * s["turns"] * (u + 0.035 * math.sin(2.0 * math.pi * u))
    return round(ang, 3), [round(r, 4), round(y, 4), 0.0]


def flight_tracks(s: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rotation and position tracks for a stream's bead: flights, jumps back down while invisible."""
    rot: list[tuple[float, Any]] = []
    pos: list[tuple[float, Any]] = []
    starts = flights(s)
    a0, p0 = pose(s, 0.0)
    if starts[0] > 0.0:
        rot.append((0.0, [0.0, a0, 0.0]))
        pos.append((0.0, p0))
    for t0 in starts:
        v = s["v"]
        n = max(2, int(math.ceil(v / KEY_STEP)))
        for i in range(n + 1):
            u = i / n
            a, p = pose(s, u)
            t = t0 + v * u
            if rot and abs(rot[-1][0] - t) < 1e-6:
                continue
            rot.append((t, [0.0, a, 0.0]))
            pos.append((t, p))
        # invisible now: jump back to the start pose and park there
        rot.append((t0 + v + 0.02, [0.0, a0, 0.0]))
        pos.append((t0 + v + 0.02, p0))
    rot.append((DURATION, [0.0, a0, 0.0]))
    pos.append((DURATION, p0))
    return tr(rot), tr(pos)


def envelope(s: dict[str, Any], peak: float, ramp: str = "width") -> dict[str, Any]:
    """Width / emissive envelope of a stream's trails: in at the knees, dissolve at the top, and a
    graceful dissolve at the top.

    The trail's width is global while its taper follows vertex age, so a young ribbon, whose oldest
    vertex is not yet old enough for the taper to close it, would end in a flat cut. The width
    therefore grows as (age / 0.8 TRAIL_LIFE)^1.5, which keeps the product of the two small at
    the tail; the emissive comes up faster, so
    the thin bright core is there from the start and the soft glow swells around it."""
    if ramp == "width":
        rise = [(0.8 * TRAIL_LIFE * f, f ** 1.5) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    else:
        rise = [(0.0, 0.0), (0.15, 0.5), (0.4, 1.0)]
    keys: list[tuple[float, float]] = []
    starts = flights(s)
    if starts[0] > 0.0:
        keys.append((0.0, 0.0))
    for t0 in starts:
        t1 = t0 + s["v"]
        hold = t1 - FADE_OUT
        seg = [(t0 + dt, peak * f) for dt, f in rise if t0 + dt < hold - 1e-6]
        top = peak * min(1.0, ((hold - t0) / (0.8 * TRAIL_LIFE)) ** 1.5) if ramp == "width" else peak
        seg += [(hold, top), (t1, 0.0)]
        keys.extend(seg)
    keys.append((DURATION, 0.0))
    out: list[tuple[float, float]] = []
    for t, v in keys:
        if out and abs(out[-1][0] - t) < 1e-6:
            continue
        out.append((t, round(v, 5)))
    return tr(out)


def colour_envelope(s: dict[str, Any], rgb: list[float]) -> dict[str, Any]:
    """The trail colour follows the emissive envelope down to black, so a renderer that draws a
    zero-width ribbon as a hairline (the CPU reference does) still shows nothing between flights."""
    keys = envelope(s, 1.0, "glow")["track"]
    return tr([(k["time"], c([x * k["value"] for x in rgb])) for k in keys])


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------

def build(slug: str, spec: dict[str, Any]) -> dict[str, Any]:
    p = spec["palette"]
    hot, mid, deep = p["hot"], p["mid"], p["deep"]
    g = gain(p)
    nodes: list[dict[str, Any]] = []
    add = nodes.append

    add(node("cam", "camera", {"position": [1.55, 1.2, 2.35], "target": [0.0, 1.0, 0.0], "fov": 40.0}))

    # ---- textures: a soft cross-width profile for the ribbons, a round mote ----
    band = [op("g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
            op("gi", "invert", None, {"a": "g"}),
            op("t", "math", {"mode": "min"}, {"a": "g", "b": "gi"})]
    add(tex("tex_core", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.42},
                                          {"a": "t"})], "lv"))
    add(tex("tex_glow", 8, 64, band + [op("lv", "levels", {"in_low": 0.0, "in_high": 0.5, "gamma": 0.62},
                                          {"a": "t"})], "lv"))
    add(tex("tex_mote", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("lv", "levels", {"gamma": 0.8}, {"a": "r"}),
    ], "lv"))

    add(node("mat_core", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(hot),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.08, "double_sided": True,
    }, {"base_texture": "tex_core"}))
    add(node("mat_glow", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(mid),
        "emissive_intensity": 0.2, "soft_particle": True, "depth_fade": 0.12, "double_sided": True,
    }, {"base_texture": "tex_glow"}))
    add(node("mat_mote", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": c(hot),
        "emissive_intensity": 0.25, "soft_particle": True, "depth_fade": 0.06,
    }))

    add(node("f_curl", "force", {"force_type": "curl_noise", "strength": 0.12, "frequency": 1.1,
                                 "speed": 0.2}))
    add(node("f_lift", "force", {"force_type": "buoyancy", "strength": 0.1}))

    # ---- the attach point: the character's feet ----
    add(node("anchor", "mesh", {"primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
                                "scale": [1.0, 1.0, 1.0]}))

    # ---- energy streams ----
    add(node("ps_shed", "particle_system", {
        "max_particles": 32, "lifetime": 0.5, "lifetime_variance": 0.1,
        "size": 0.026, "size_variance": 0.008,
        "size_over_life": [[0.0, 0.6], [0.25, 1.0], [1.0, 0.5]],
        "color": c(hot), "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [1.0, c(mid)]],
        "opacity": 0.9, "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.6, 0.7], [1.0, 0.0]],
        "emissive": round(2.2 * g, 3), "drag": 1.4, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.04,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_curl", "f_lift"]}))

    for s in STREAMS:
        sid = s["id"]
        layer = "streams"
        add(node(f"tilt_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False,
            "rotation": [s["lean"][0], 0.0, s["lean"][1]],
        }, parent="anchor", layer=layer))
        rot, pos = flight_tracks(s)
        add(node(f"spin_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, "rotation": rot,
        }, parent=f"tilt_{sid}", layer=layer))
        add(node(f"bead_{sid}", "mesh", {
            "primitive": "sphere", "radius": 0.01, "segments": 4, "visible": False, "position": pos,
        }, parent=f"spin_{sid}", layer=layer))
        add(node(f"glow_{sid}", "trail", {
            "lifetime": TRAIL_LIFE, "max_segments": 240, "min_vertex_distance": 0.022,
            "taper": [[0.0, 0.0], [0.14, 0.8], [0.3, 1.0], [0.5, 0.62], [0.75, 0.22], [1.0, 0.0]],
            "opacity_over_life": RIPPLE_GLOW,
            "color": colour_envelope(s, mid), "blend": "additive", "noise_amplitude": 0.0,
            "width": envelope(s, round(GLOW_WIDTH * (0.6 + 0.4 * s["gain"]), 4)),
            "emissive": envelope(s, round(GLOW_EMISSIVE * g * s["gain"], 4), "glow"),
        }, {"source": f"bead_{sid}", "material": "mat_glow"}, layer=layer))
        add(node(f"core_{sid}", "trail", {
            "lifetime": TRAIL_LIFE, "max_segments": 240, "min_vertex_distance": 0.022,
            "taper": [[0.0, 0.0], [0.12, 0.85], [0.26, 1.0], [0.5, 0.55], [0.75, 0.18], [1.0, 0.0]],
            "opacity_over_life": RIPPLE_CORE,
            "color": colour_envelope(s, hot), "blend": "additive", "noise_amplitude": 0.0,
            "width": envelope(s, round(CORE_WIDTH * (0.6 + 0.4 * s["gain"]), 4)),
            "emissive": envelope(s, round(CORE_EMISSIVE * g * s["gain"], 4), "glow"),
        }, {"source": f"bead_{sid}", "material": "mat_core"}, layer=layer))
        add(node(f"e_shed_{sid}", "emitter", {
            "shape": "point", "direction": [0.0, 0.0, 0.0], "velocity": 0.06, "velocity_variance": 0.04,
            "inherit_velocity": 0.35, "rate": envelope(s, round(SHED_RATE * s["gain"], 3), "glow"),
        }, {"particle": "ps_shed"}, parent=f"bead_{sid}", layer=layer))

    # ---- motes ----
    add(node("ps_motes", "particle_system", {
        "max_particles": 48, "lifetime": 0.9, "lifetime_variance": 0.2,
        "size": 0.05, "size_variance": 0.02,
        "size_over_life": [[0.0, 0.5], [0.2, 1.0], [0.8, 0.85], [1.0, 0.4]],
        "color": c(hot), "color_over_life": [[0.0, [1.0, 1.0, 1.0, 1.0]], [0.5, c(mid)], [1.0, c(deep)]],
        "opacity": 0.85, "opacity_over_life": [[0.0, 0.0], [0.15, 1.0], [0.45, 0.6], [0.7, 0.9], [1.0, 0.0]],
        "emissive": round(1.8 * g, 3), "drag": 0.5, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.05,
    }, {"sprite": "tex_mote", "material": "mat_mote", "forces": ["f_curl", "f_lift"]}))
    add(node("e_motes", "emitter", {
        "shape": "sphere", "radius": 0.42, "surface_only": True, "position": [0.0, 0.95, 0.0],
        "scale": [1.0, 1.55, 1.0], "direction": [0.0, 1.0, 0.0], "spread": 12.0,
        "velocity": 0.45, "velocity_variance": 0.15,
        "rate": tr([(0.0, 4.0), (0.3, MOTE_RATE), (1.5, MOTE_RATE), (1.9, 0.0), (DURATION, 0.0)]),
    }, {"particle": "ps_motes"}, parent="anchor", layer="motes"))
    add(node("e_rise", "emitter", {
        "shape": "ring", "radius": 0.46, "inner_radius": 0.3, "position": [0.0, 0.3, 0.0],
        "direction": [0.0, 1.0, 0.0], "spread": 6.0, "velocity": 0.95, "velocity_variance": 0.35,
        "rate": tr([(0.0, 0.0), (0.05, 22.0), (0.4, 22.0), (0.6, 0.0)]),
        "start_time": 0.0, "duration": 0.65,
    }, {"particle": "ps_motes"}, parent="anchor", layer="motes"))

    # ---- a faint light at chest height ----
    light_keys = [(0.0, 0.0), (0.5, 0.4 * g), (1.6, 0.4 * g), (2.3, 0.0), (DURATION, 0.0)]
    add(node("l_glow", "light", {
        "light_type": "point", "position": [0.0, 1.15, 0.0], "color": c(mid), "radius": 2.4,
        "intensity": tr(light_keys),
    }, parent="anchor", layer="light"))

    # ---- controls ----
    colour_bindings = []
    for n in nodes:
        for parameter in ("color", "color_over_life", "emissive_color"):
            if parameter in n["parameters"]:
                colour_bindings.append({"node": n["id"], "parameter": parameter, "op": "hue_shift"})

    def mul(nid: str, parameter: str) -> dict[str, Any]:
        return {"node": nid, "parameter": parameter, "op": "multiply"}

    def control(cid: str, label: str, group: str, lo: float, hi: float, bindings: list[dict[str, Any]],
                unit: str = "x", default: float = 1.0, step: float = 0.01) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": default,
                "value": default, "step": step, "unit": unit, "bindings": bindings}

    trails = [f"{k}_{s['id']}" for s in STREAMS for k in ("glow", "core")]
    sheds = [f"e_shed_{s['id']}" for s in STREAMS]
    controls = [
        control("intensity", "Intensity", "Global", 0.0, 3.0,
                [mul(t, "emissive") for t in trails] + [mul("ps_motes", "emissive"), mul("ps_shed", "emissive"),
                                                        mul("l_glow", "intensity")]),
        control("ribbon_brightness", "Ribbon brightness", "Energy streams", 0.0, 3.0,
                [mul(t, "emissive") for t in trails]),
        control("ribbon_width", "Ribbon width", "Energy streams", 0.3, 2.5, [mul(t, "width") for t in trails]),
        control("mote_amount", "Mote amount", "Motes", 0.0, 3.0,
                [mul("e_motes", "rate"), mul("e_rise", "rate")] + [mul(e, "rate") for e in sheds]),
        control("character_height", "Character height", "Global", 0.5, 2.0,
                [mul("anchor", "scale")] + [mul(t, "width") for t in trails]
                + [mul("ps_motes", "size"), mul("ps_shed", "size"), mul("e_motes", "velocity"),
                   mul("e_rise", "velocity"), mul("l_glow", "radius")]),
        control("hue", "Energy hue", "Global", -180.0, 180.0, colour_bindings, "deg", 0.0, 1.0),
        control("global_speed", "Speed", "Global", 0.25, 4.0,
                [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}], step=0.05),
    ]

    return {
        "schema_version": "0.1.0",
        "name": spec["name"],
        "description": f"A quick power-up buff ({spec['flavour']}): slim energy ribbons wrap and spiral up the "
                       "character from ankles to head and dissolve, with a few soft motes; a 2.8 s one-shot, no "
                       "ground base, nothing above the head. Origin at the character's feet (about 1.8 m tall).",
        "duration": DURATION,
        "seed": spec["seed"],
        "timeline": {"phases": [
            {"name": "rise", "start": 0.0, "end": 0.6},
            {"name": "surge", "start": 0.6, "end": 1.65},
            {"name": "dissolve", "start": 1.65, "end": 2.3},
            {"name": "fade", "start": 2.3, "end": DURATION},
        ]},
        "layers": [
            {"id": "streams", "name": "Energy streams", "role": "primary"},
            {"id": "motes", "name": "Motes", "role": "secondary"},
            {"id": "light", "name": "Light", "role": "secondary"},
        ],
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/power_up.py",
            "variant": slug,
            "theme": spec["theme"],
            "category": "support",
            "attachment": {
                "origin": [0.0, 0.0, 0.0], "origin_node": "anchor", "character_height": 1.8,
                "body_radius": 0.3,
                "notes": "Attach the `anchor` node (the effect origin) at the character's feet, y = 0, and "
                         "let it follow the character. Nothing is drawn for the character. Scale for bigger "
                         "or smaller characters with the Character height control.",
            },
            "analysis": "rise 0-0.6 five slim ribbons and a burst of motes climb out of the ankles and "
                        "start to wrap the body | surge 0.6-1.65 three or four ribbons at staggered heights "
                        "circle the whole body from ankles to head, one or two bright, the rest fainter, with "
                        "glints riding along and motes hugging the silhouette | dissolve 1.65-2.3 the ribbons "
                        "dissolve upward at head height | fade 2.3-2.8 the last glints and motes fade",
            "render_settings": {"background": [0.0, 0.0, 0.0, 1.0], "ground_albedo": 0.05,
                                "bloom_intensity": 0.3, "bloom_radius": 0.05, "exposure": 1.0, "grid": False},
            "tags": spec["tags"],
        },
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="directory to write the six effect JSON files into")
    parser.add_argument("--only", default="", help="comma-separated slugs to write (default: all six)")
    parser.add_argument("--check", help="directory whose files must match the generator output byte for byte")
    args = parser.parse_args()
    only = {s for s in args.only.split(",") if s}
    if args.check:
        bad = 0
        for slug, spec in VARIANTS.items():
            path = Path(args.check) / f"{slug}.json"
            same = path.exists() and path.read_text(encoding="utf-8") == render(build(slug, spec))
            bad += 0 if same else 1
            print(f"{path}: {'identical' if same else 'DIFFERENT'}")
        return 1 if bad else 0
    out = Path(args.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    for slug, spec in VARIANTS.items():
        if only and slug not in only:
            continue
        effect = build(slug, spec)
        path = out / f"{slug}.json"
        path.write_text(render(effect), encoding="utf-8")
        print(f"{path}  ({len(effect['nodes'])} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
