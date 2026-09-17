#!/usr/bin/env python3
"""Derive `Lesser Fireball` from `Fire Bolt`.

Lesser Fireball is a deliberate clone of `examples/effects/fire_bolt.json`: same
phases, same node ids, same animation structure (cast / launch / flight / impact /
decay) and the same camera, tuned down into the weaker cantrip version of the
spell.  Everything here is a deterministic table of scale factors and absolute
overrides applied to the Fire Bolt document, so re-deriving after Fire Bolt is
re-tuned keeps the two in sync.

    python tools/generators/lesser_fireball.py --out out/work/lesser_fireball/effects

Writes ``<out>/lesser_fireball.json``.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SRC = os.path.join(REPO, "examples", "effects", "fire_bolt.json")

# ---------------------------------------------------------------------------
# The scale table.  One named factor per artistic intent; the per-node table
# below only ever refers to these, so the whole tuning pass is readable at once.
# ---------------------------------------------------------------------------
CORE = 0.60      # head / core size, and every emitter volume hung off the head
PEBBLE = 0.48    # the molten rock itself: smaller than CORE so the flame sheath
                 # covers it instead of showing as a dark pupil inside the ball
SPRITE = 0.65    # flame + spark sprite sizes
RATE = 0.55      # flame emission rates
TRAIL = 0.60     # trail widths, trail lifetimes, ribbon particle lifetimes
LIFE = 0.60      # flame particle lifetimes (with SPRAY this is the tail length)
SPRAY = 0.70     # emitter spray velocities (a shorter, tighter tail)
EMBER = 0.40     # ember + spark counts
SMOKE = 0.40     # smoke counts
LIGHT = 0.60     # light intensity and radius
IMPACT = 0.50    # impact burst counts, flash strength, scorch size
IMPACT_SIZE = 0.60  # impact sprite sizes
PETAL = 0.58     # impact flame-petal counts (kept above IMPACT so the burst
                 # still reads as fire rather than as a scatter of spark dots)
SPARK_SIZE = 0.50  # spark sprite size (finer than the flame factor)
CAP = 0.55       # max_particles budget caps
CAST = 0.68      # cast-phase gather ring (kept above the other cuts so the
                 # telegraph still reads at full framing)
SHORT = 0.70     # "lives a bit less long" for embers / smoke / sparks
CURL = 0.65      # curl-noise strength (displacement scales with the body)
CURL_FREQ = 1.40 # ...and its frequency goes up so the detail stays in scale

# node id -> {parameter: factor}.  Factors walk scalars, vectors and
# {"value":..,"track":[{"time":..,"value":..}]} animation tracks alike.
SCALE: dict[str, dict[str, float]] = {
    # --- forces ------------------------------------------------------------
    "f_curl_fire":   {"strength": CURL, "frequency": CURL_FREQ},
    "f_curl_fine":   {"strength": CURL, "frequency": CURL_FREQ},
    "f_curl_ribbon": {"strength": CURL, "frequency": CURL_FREQ},
    "f_buoy_fire":   {"strength": 0.75},
    "f_buoy_soft":   {"strength": 0.8},
    "f_turb_smoke":  {"strength": 0.7},
    "f_slipstream":  {"strength": SPRAY},
    "f_pull_in":     {"radius": CAST},

    # --- molten core -------------------------------------------------------
    "core":      {"radius": PEBBLE},
    "core_lava": {"radius": PEBBLE},

    # --- head sheath / nose / vent ----------------------------------------
    "ps_sheath": {"size": SPRITE, "size_variance": SPRITE,
                  "lifetime": 0.85, "lifetime_variance": 0.85,
                  "max_particles": CAP},
    "e_sheath":  {"radius": CORE, "rate": RATE,
                  "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_nose":   {"size": SPRITE, "size_variance": SPRITE,
                  "lifetime": 0.85, "lifetime_variance": 0.85,
                  "max_particles": CAP},
    "e_nose":    {"radius": CORE, "position": CORE, "rate": RATE,
                  "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_vent":   {"size": SPARK_SIZE, "size_variance": SPARK_SIZE,
                  "max_particles": EMBER},
    "e_vent":    {"rate": EMBER, "velocity": SPRAY, "velocity_variance": SPRAY},

    # --- flame body --------------------------------------------------------
    "ps_flame_mid": {"size": SPRITE, "size_variance": SPRITE,
                     "lifetime": LIFE, "lifetime_variance": LIFE,
                     "max_particles": CAP},
    "e_flame_mid":  {"radius": CORE, "rate": RATE,
                     "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_flame_far": {"size": SPRITE, "size_variance": SPRITE,
                     "lifetime": LIFE, "lifetime_variance": LIFE,
                     "max_particles": CAP},
    "e_flame_far":  {"radius": CORE, "rate": RATE,
                     "velocity": SPRAY, "velocity_variance": SPRAY},

    # --- launch ------------------------------------------------------------
    "e_launch_jet":   {"radius": CORE, "burst_count": IMPACT,
                       "velocity": SPRAY, "velocity_variance": SPRAY},
    "e_launch_spark": {"radius": CORE, "burst_count": EMBER,
                       "velocity": SPRAY, "velocity_variance": SPRAY},

    # --- flame ribbons + head streak --------------------------------------
    "ps_ribbon_a": {"lifetime": TRAIL, "lifetime_variance": TRAIL, "max_particles": TRAIL},
    "ps_ribbon_b": {"lifetime": TRAIL, "lifetime_variance": TRAIL, "max_particles": TRAIL},
    "ps_ribbon_c": {"lifetime": TRAIL, "lifetime_variance": TRAIL, "max_particles": TRAIL},
    "e_ribbon_a":  {"radius": CORE, "rate": RATE, "velocity": SPRAY, "velocity_variance": SPRAY},
    "e_ribbon_b":  {"radius": CORE, "rate": RATE, "velocity": SPRAY, "velocity_variance": SPRAY},
    "e_ribbon_c":  {"radius": CORE, "rate": RATE, "velocity": SPRAY, "velocity_variance": SPRAY},
    "t_ribbon_a":  {"width": TRAIL, "lifetime": TRAIL, "noise_amplitude": TRAIL},
    "t_ribbon_b":  {"width": TRAIL, "lifetime": TRAIL, "noise_amplitude": TRAIL},
    "t_ribbon_c":  {"width": TRAIL, "lifetime": TRAIL, "noise_amplitude": TRAIL},
    "t_streak":    {"width": TRAIL, "lifetime": TRAIL, "noise_amplitude": TRAIL},

    # --- embers ------------------------------------------------------------
    "ps_ember": {"size": SPARK_SIZE, "size_variance": SPARK_SIZE,
                 "lifetime": SHORT, "lifetime_variance": SHORT,
                 "max_particles": EMBER},
    "e_ember":  {"radius": CORE, "rate": EMBER,
                 "velocity": SPRAY, "velocity_variance": SPRAY},

    # --- cast --------------------------------------------------------------
    "ps_gather": {"size": SPARK_SIZE, "max_particles": CAST},
    "e_gather":  {"radius": CAST, "rate": CAST,
                  "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_flare":  {"size": 0.85, "size_variance": 0.85},

    # --- smoke trail -------------------------------------------------------
    "ps_smoke":       {"size": 0.55, "size_variance": 0.55,
                       "lifetime": SHORT, "lifetime_variance": SHORT,
                       "max_particles": SMOKE},
    "e_smoke":        {"radius": CORE, "rate": SMOKE,
                       "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_trail_ember": {"size": SPARK_SIZE, "lifetime": SHORT, "lifetime_variance": SHORT,
                       "max_particles": SMOKE},
    "e_trail_ember":  {"radius": CORE, "rate": EMBER,
                       "velocity": SPRAY, "velocity_variance": SPRAY},

    # --- impact ------------------------------------------------------------
    "ps_petal":        {"size": IMPACT_SIZE, "size_variance": IMPACT_SIZE,
                        "lifetime": LIFE, "lifetime_variance": LIFE,
                        "max_particles": PETAL},
    "e_petal_disc":    {"radius": CORE, "inner_radius": CORE, "burst_count": PETAL,
                        "velocity": SPRAY, "velocity_variance": SPRAY,
                        "radial_velocity": SPRAY},
    "e_petal_back":    {"radius": CORE, "burst_count": PETAL,
                        "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_burst_spark":  {"size": SPARK_SIZE, "size_variance": SPARK_SIZE,
                        "lifetime": SHORT, "lifetime_variance": SHORT,
                        "max_particles": IMPACT},
    "e_burst_spark":   {"radius": CORE, "burst_count": EMBER,
                        "velocity": SPRAY, "velocity_variance": SPRAY,
                        "radial_velocity": SPRAY},
    "e_burst_back":    {"radius": CORE, "burst_count": EMBER,
                        "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_impact_flash": {"size": IMPACT, "size_variance": IMPACT, "emissive": 0.7},
    "e_impact_smoke":  {"radius": CORE, "burst_count": SMOKE,
                        "velocity": SPRAY, "velocity_variance": SPRAY},
    "ps_impact_smoke": {"size": 0.55, "size_variance": 0.55,
                        "lifetime": SHORT, "lifetime_variance": SHORT,
                        "max_particles": SMOKE},

    # --- lights / decal / post --------------------------------------------
    "l_head":   {"radius": LIGHT, "intensity": LIGHT},
    "l_flash":  {"radius": LIGHT, "intensity": IMPACT},
    "d_scorch": {"size": IMPACT, "opacity": 0.8},
    "fx_haze":  {"intensity": LIGHT, "radius": TRAIL},
}

# node id -> {parameter: absolute value}.  Applied after SCALE.
SET: dict[str, dict[str, object]] = {
    # A cantrip is a *small* fire, not a dim one: keep the sheath and nose hot
    # so the head still reads as a bright orange-yellow ball at this size.
    "ps_sheath": {
        "color_over_life": [[0.0, [1.0, 0.88, 0.62]], [0.35, [0.98, 0.62, 0.24]],
                            [0.75, [0.86, 0.28, 0.05]], [1.0, [0.36, 0.05, 0.005]]],
        "opacity_over_life": [[0.0, 0.2], [0.16, 0.72], [0.7, 0.6], [1.0, 0.0]],
        "emissive": 0.82,
    },
    # A cantrip burns cooler than Fire Bolt: the head reads orange-yellow rather
    # than white-hot, which is the clearest single "lesser" cue at a glance.
    "mat_fire_head": {"emissive_color": [1.0, 0.70, 0.33]},
    "ps_nose": {"emissive": 1.35},
    # The tail is short and licks, so it stays hotter over its (shorter) life
    # instead of falling all the way to near-black soot.
    "ps_flame_far": {
        "color_over_life": [[0.0, [1.0, 0.72, 0.34]], [0.35, [0.97, 0.36, 0.08]],
                            [0.75, [0.68, 0.12, 0.014]], [1.0, [0.2, 0.02, 0.0]]],
        "opacity_over_life": [[0.0, 0.12], [0.16, 0.66], [0.6, 0.5], [1.0, 0.0]],
        "emissive": 0.72,
        "velocity_stretch": 0.42,
    },
    "ps_flame_mid": {"velocity_stretch": 0.28},
    # Smoke: thinner and lighter, a wisp rather than a plume.
    "ps_smoke": {
        "opacity": 0.38,
        "color_over_life": [[0.0, [1.1, 0.78, 0.52]], [0.25, [0.8, 0.68, 0.6]],
                            [1.0, [0.56, 0.52, 0.5]]],
        "opacity_over_life": [[0.0, 0.0], [0.24, 0.85], [0.65, 0.55], [1.0, 0.0]],
        "emissive": 0.18,
        "size_over_life": [[0.0, 0.35], [0.4, 1.15], [1.0, 1.9]],
    },
    "ps_impact_smoke": {
        "opacity": 0.3,
        "color_over_life": [[0.0, [1.05, 0.8, 0.55]], [0.3, [0.74, 0.65, 0.58]],
                            [1.0, [0.48, 0.45, 0.43]]],
    },
    # The molten rock is a pebble now: less crust, more visible lava, and the
    # crust glows instead of silhouetting as a dark pupil inside the flame ball.
    "core": {"irregularity": 0.62, "emissive": 0.40},
    # Scorch: a hand-sized burn, not a crater.
    "d_scorch": {"color": [0.055, 0.04, 0.034, 1.0]},
}

# Top-level document overrides.
DOC: dict[str, object] = {
    "name": "Lesser Fireball",
    "description": (
        "A cantrip-scale bolt of fire: a compact orange-yellow ember ball with a "
        "short licking tail, a few embers and a small scorching pop on impact."
    ),
    "seed": 4271,
}

META: dict[str, str] = {
    "reference": (
        "Lesser Fireball concept sheet (projectile, weaker sibling of Fire Bolt): "
        "cast 0-0.3 (a small ember orb pulls in a handful of sparks), launch "
        "0.3-0.54 (short pop), mid flight 0.54-1.6 (compact head, stubby tail), "
        "approach 1.6-2.3, impact 2.3-3.0 (a modest spark scatter, a few flame "
        "petals, a small flash)."
    ),
    "analysis": (
        "A fist-sized molten pebble wrapped in a bright orange-yellow flame sheath "
        "about 0.35 m across; the tail licks barely one core length back instead of "
        "Fire Bolt's two to three; a thin ember scatter and a pale thinning smoke "
        "wisp read as a cantrip, not a siege spell."
    ),
}


def scale(value, factor):
    """Scale a scalar, a vector, or a {value, track} animation track."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        out = round(float(value) * factor, 6)
        return int(round(out)) if isinstance(value, int) and not isinstance(value, bool) else out
    if isinstance(value, list):
        return [scale(v, factor) for v in value]
    if isinstance(value, dict):
        out = dict(value)
        if "value" in out:
            out["value"] = scale(out["value"], factor)
        if "track" in out:
            out["track"] = [dict(kf, value=scale(kf["value"], factor)) for kf in out["track"]]
        return out
    return value


def derive(source: dict) -> dict:
    doc = copy.deepcopy(source)
    doc.update(DOC)
    doc.setdefault("metadata", {}).update(META)

    seen: set[str] = set()
    for node in doc.get("nodes", []):
        nid = node.get("id")
        params = node.get("parameters")
        if not isinstance(params, dict):
            continue
        for key, factor in SCALE.get(nid, {}).items():
            if key not in params:
                raise KeyError(f"{nid}: no parameter {key!r} to scale")
            params[key] = scale(params[key], factor)
            seen.add(nid)
        for key, value in SET.get(nid, {}).items():
            params[key] = copy.deepcopy(value)
            seen.add(nid)

    missing = (set(SCALE) | set(SET)) - seen
    if missing:
        raise KeyError(f"table references nodes absent from the source: {sorted(missing)}")
    return doc


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="directory to write lesser_fireball.json into")
    ap.add_argument("--src", default=DEFAULT_SRC, help="Fire Bolt JSON to derive from")
    ap.add_argument("--name", default="lesser_fireball.json", help="output file name")
    args = ap.parse_args(argv)

    with open(args.src, "r", encoding="utf-8") as fh:
        source = json.load(fh)

    doc = derive(source)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, args.name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
