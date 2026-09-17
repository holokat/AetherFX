#!/usr/bin/env python3
"""Generate `examples/effects/homing_arcane_missile.json`.

    python tools/generators/homing_arcane_missile.py            # rewrites the library file
    python tools/generators/homing_arcane_missile.py --out DIR  # writes DIR/homing_arcane_missile.json

A violet crystal missile that bends after a target which moves mid-flight.  The
graph is authored from scratch for this effect; nothing is derived from another
library document.

How it is built
---------------
* THE PATH is integrated from three profiles - speed, turn rate and climb angle
  over time - so it is a C1-smooth curve by construction: an ease-in kick at
  launch, a gentle arc through the mid flight and a tightening, accelerating
  hook in `target_adjust`.  One position key is baked per simulation frame
  (1/60 s), so the runtime never interpolates across a bend.
* HOMING RIG.  The missile does not carry its path in world space.  From the
  moment the target "moves" (T_PIVOT) the path is expressed in spherical
  coordinates around the pivot point where the missile is at that moment:
  `aim_side` holds the azimuth swing as a keyframed Y rotation, `aim_height`
  the elevation swing as a keyframed Z rotation, and the `missile` node only
  keeps the distance from the pivot along the un-swung heading.  The default
  tracks reproduce the authored hook exactly; the `target_side` and
  `target_height` controls multiply the two swing tracks, so dragging them
  moves the target and the hook bends to follow it.  The impact hangs off the
  same rig, so it always sits where the missile arrives.
* TANGENT ORIENTATION.  Nothing in the vocabulary aligns a node to its motion,
  so the heading is baked: `head_yaw` / `head_tilt` carry yaw, pitch and a bank
  angle computed from the finite-difference tangent of the baked path.  Two
  small correction nodes (`head_yaw_fix`, `head_pitch_fix`) are bound to the
  same target controls and hold the first-order change of the tangent per unit
  of control, so the crystal keeps pointing along its travel when the target is
  dragged.
* BRAID.  Spinning child hubs behind the crystal carry the ribbon sources at a
  small radius.  The spin angle is a function of the distance travelled, not of
  time, so the helices keep a constant pitch in space and never coil up while
  the missile is slow.  Ribbons use soft cross-width textures, taper and fade.
* ORBITING SHARDS are small bipyramids on tilted, spinning parent chains, each
  with its own rate, radius, phase, tumble and a thin soft trail.
* FOLLOW SPRITES (halo, aura).  Particles are world-space, so a glow that has
  to sit on a fast hub is emitted with `inherit_velocity: 1` from an anchor that
  trails the hub by one frame of travel: a new particle is integrated in the
  step it spawns in, which puts it exactly on the hub, and it keeps pace for its
  three-frame life.

House style: no crosses or four-armed stars, no hard-edged ribbons, soft
many-rayed irregular impact flare, projectile ends in an impact.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SLUG = "homing_arcane_missile"
NAME = "Homing Arcane Missile"
SEED = 41739

# ---------------------------------------------------------------------------
# timing (the concept sheet, total 1.8 s)
# ---------------------------------------------------------------------------

FPS = 60
DT = 1.0 / FPS
DURATION = 1.8
PHASES = [
    ("spawn_charge", 0.0, 0.2),
    ("launch", 0.2, 0.4),
    ("mid_flight", 0.4, 1.0),
    ("target_adjust", 1.0, 1.4),
    ("impact", 1.4, 1.6),
    ("dissipate", 1.6, 1.8),
]
F_LAUNCH = 12            # frame 12 = 0.2 s: the kick
F_PIVOT = 59             # frame 59 = 0.983 s: the target moves, the hook starts here
F_IMPACT = 84            # frame 84 = 1.4 s
F_END = 108              # frame 108 = 1.8 s
T_LAUNCH = F_LAUNCH * DT
T_PIVOT = F_PIVOT * DT
T_IMPACT = F_IMPACT * DT

LAUNCH = (-4.2, 1.3, -0.6)     # where the orb charges
TARGET = (3.6, 1.2, 0.8)       # where the (moved) target is hit

# ---------------------------------------------------------------------------
# palette - textures stay greyscale so the `hue` control recolours everything
# ---------------------------------------------------------------------------

WHITE_HOT = [0.93, 0.88, 1.0]    # core, flash centre (near white, violet tint)
VIOLET = [0.56, 0.26, 1.0]       # the signature hue
DEEP = [0.22, 0.07, 0.62]        # body of ribbons and mist
MAGENTA = [0.82, 0.30, 0.98]     # warm secondary
AZURE = [0.34, 0.52, 1.0]        # blue ribbon / filaments
ICE = [0.74, 0.84, 1.0]          # blue-white filament cores


def rgba(rgb: list[float], a: float = 1.0, k: float = 1.0) -> list[float]:
    return [round(rgb[0] * k, 4), round(rgb[1] * k, 4), round(rgb[2] * k, 4), a]


def mix(a: list[float], b: list[float], t: float) -> list[float]:
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


# ---------------------------------------------------------------------------
# small 3x3 rotation helpers (engine convention: R = Rz * Ry * Rx, degrees,
# right-handed; Ry(a) takes +X to (cos a, 0, -sin a), Rz(a) takes +X to
# (cos a, sin a, 0))
# ---------------------------------------------------------------------------

Vec = tuple[float, float, float]
Mat = tuple[Vec, Vec, Vec]


def rot_x(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def rot_y(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def rot_z(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def mat_mul(a: Mat, b: Mat) -> Mat:
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))  # type: ignore[return-value]


def mat_t(a: Mat) -> Mat:
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def mat_vec(a: Mat, v: Vec) -> Vec:
    return tuple(sum(a[i][k] * v[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add3(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale3(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def norm(a: Vec) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def unit(a: Vec) -> Vec:
    n = norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (1.0, 0.0, 0.0)


def heading_of(d: Vec) -> tuple[float, float]:
    """(azimuth, elevation) in degrees of a direction: d = (cos e cos a, sin e, -cos e sin a)."""
    u = unit(d)
    return math.degrees(math.atan2(-u[2], u[0])), math.degrees(math.asin(max(-1.0, min(1.0, u[1]))))


def direction_of(azimuth: float, elevation: float) -> Vec:
    a, e = math.radians(azimuth), math.radians(elevation)
    return (math.cos(e) * math.cos(a), math.sin(e), -math.cos(e) * math.sin(a))


def unwrap(angles: list[float]) -> list[float]:
    out = [angles[0]]
    for a in angles[1:]:
        prev = out[-1]
        while a - prev > 180.0:
            a -= 360.0
        while a - prev < -180.0:
            a += 360.0
        out.append(a)
    return out


def smoothstep(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


# ---------------------------------------------------------------------------
# the flight: speed, turn rate and climb angle over time
# ---------------------------------------------------------------------------

V_CRUISE = 8.554         # m/s once the kick is over
AZIMUTH_0 = 25.08        # launch heading: away from the camera, away from the final target
TURN_MID = -36.0         # deg/s, the gentle arc of the mid flight (towards the camera)
TURN_END = -485.0        # deg/s, the turn rate the hook tightens to at the impact
T_TURN = 0.86            # the hook starts to tighten here
CLIMB_0 = 14.40          # launch climb angle
DIVE_END = -29.0         # dive angle at the impact
HOOK_ACCEL = 0.8         # how much speed the missile gains through the hook (x cruise)


def speed_at(t: float) -> float:
    """Metres per second along the path."""
    if t < 0.10:
        return 0.0
    if t < T_LAUNCH:                                   # wind-up: the orb recoils a few centimetres
        return -0.9 * math.sin(math.pi * (t - 0.10) / (T_LAUNCH - 0.10))
    if t < 0.42:                                       # the kick: ease-in acceleration
        u = (t - T_LAUNCH) / (0.42 - T_LAUNCH)
        return V_CRUISE * u * u * (3.0 - 2.0 * u)
    if t < 1.0:                                        # cruise, gaining a little
        return V_CRUISE * (1.0 + 0.07 * (t - 0.42) / 0.58)
    u = (t - 1.0) / (T_IMPACT - 1.0)                   # target adjust: accelerate into the hook
    return V_CRUISE * 1.07 * (1.0 + HOOK_ACCEL * u * u)


def turn_rate_at(t: float) -> float:
    """Degrees per second of azimuth change (negative = turning towards the camera)."""
    if t < 0.30:
        return 0.0
    mid = TURN_MID * smoothstep((t - 0.30) / 0.22)
    if t < T_TURN:
        return mid
    u = (t - T_TURN) / (T_IMPACT - T_TURN)
    return mid + (TURN_END - TURN_MID) * (u ** 1.2)


def climb_at(t: float) -> float:
    """Elevation of the heading in degrees."""
    if t < 0.62:
        return CLIMB_0
    if t < 0.98:                                       # level out over the top of the arc
        return CLIMB_0 * (1.0 - smoothstep((t - 0.62) / 0.36))
    u = (t - 0.98) / (T_IMPACT - 0.98)
    return DIVE_END * (u ** 1.35)


def travel_of(frames: list[Vec]) -> list[float]:
    """Distance flown since the kick (0 during the charge and its recoil)."""
    out = [0.0] * len(frames)
    for i in range(F_LAUNCH + 1, len(frames)):
        out[i] = out[i - 1] + norm(sub(frames[i], frames[i - 1]))
    return out


def integrate_flight() -> tuple[list[Vec], Vec]:
    """World position at every frame 0..F_END (held after the impact), and the
    landing error that was folded back into the path."""
    sub_steps = 32
    h = DT / sub_steps
    pos: Vec = LAUNCH
    azimuth = AZIMUTH_0
    frames: list[Vec] = [pos]
    for f in range(F_IMPACT):
        for k in range(sub_steps):
            t = f * DT + (k + 0.5) * h
            azimuth_mid = azimuth + 0.5 * h * turn_rate_at(t)
            pos = add3(pos, scale3(direction_of(azimuth_mid, climb_at(t)), speed_at(t) * h))
            azimuth += h * turn_rate_at(t)
        frames.append(pos)
    # Land exactly on the target: the residual of the hand-tuned profiles (a few
    # centimetres) is blended in over the distance flown, so the curve keeps its shape.
    residual = sub(TARGET, frames[-1])
    travel = travel_of(frames)
    total = travel[-1]
    frames = [add3(p, scale3(residual, smoothstep(s / total))) for p, s in zip(frames, travel)]
    frames += [frames[-1]] * (F_END - F_IMPACT)
    return frames, residual


def quadratic_fit(y0: float, y1: float, y2: float) -> tuple[float, float, float]:
    """y(k) = base + k * lin + k^2 * quad through (0, y0), (1, y1), (2, y2)."""
    c1 = (y2 - y0) / 2.0
    c2 = (y2 + y0) / 2.0 - y1
    return y1 - c1 + c2, c1 - 2.0 * c2, c2


class Flight:
    """Everything the rig needs, baked per frame.

    The homing rig works in spherical coordinates around the pivot - the point
    where the missile is when the target moves.  Up to the pivot the `missile`
    node carries the authored path; after it, it only keeps its distance from
    the pivot along the heading it had there, and two parent rotations swing
    that ray onto the authored hook:

        position(t) = pivot + Ry(pivot_azimuth + k_side * swing_side(t))
                            * Rz(k_height * swing_height(t)) * ray * distance(t)

    At k = 1 (the control defaults) this is the authored path exactly; the
    target controls multiply the two swing tracks.
    """

    def __init__(self) -> None:
        self.pos, self.residual = integrate_flight()
        self.travel = travel_of(self.pos)
        n = len(self.pos)
        self.first_fly, self.last_fly = F_LAUNCH + 2, F_IMPACT - 1

        # --- the rig ---------------------------------------------------------
        self.pivot = self.pos[F_PIVOT]
        self.pivot_azimuth, self.pivot_elevation = heading_of(sub(self.pos[F_PIVOT + 1], self.pos[F_PIVOT - 1]))
        self.ray: Vec = (math.cos(math.radians(self.pivot_elevation)), math.sin(math.radians(self.pivot_elevation)), 0.0)
        self.swing_side = [0.0] * n        # azimuth swing about the pivot (deg)
        self.swing_height = [0.0] * n      # elevation swing about the pivot (deg)
        self.local: list[Vec] = []         # `missile` position in the rig frame
        to_rig = rot_y(-self.pivot_azimuth)
        raw_side = []
        for i in range(n):
            rel = sub(self.pos[i], self.pivot)
            if i <= F_PIVOT:
                self.local.append(mat_vec(to_rig, rel))
                raw_side.append(self.pivot_azimuth)
                continue
            a, e = heading_of(rel)
            raw_side.append(a)
            self.swing_height[i] = e - self.pivot_elevation
            self.local.append(scale3(self.ray, norm(rel)))
        raw_side = unwrap(raw_side)
        for i in range(F_PIVOT + 1, n):
            self.swing_side[i] = raw_side[i] - self.pivot_azimuth

        # --- heading of the authored path: yaw, pitch and a bank into the turn ----
        self.speed = [norm(self.velocity_at(i)) for i in range(n)]
        self.azimuth, self.elevation = self.heading_track(1.0, 1.0)
        self.bank = []
        for i in range(n):
            j0 = max(self.first_fly, min(i - 1, self.last_fly))
            j1 = max(self.first_fly, min(i + 1, self.last_fly))
            if j1 == j0:
                j0, j1 = (j0 - 1, j1) if j0 > self.first_fly else (j0, j1 + 1)
            rate = (self.azimuth[j1] - self.azimuth[j0]) / ((j1 - j0) * DT)
            self.bank.append(max(-40.0, min(40.0, -0.115 * rate)))

        # --- the same heading in the rig frame, fitted over the target controls ----
        # local yaw as a quadratic in k_side, local pitch as a quadratic in k_height,
        # each through the control values 0, 1 and 2 (1 = authored, so it is exact there).
        yaw = {k: unwrap([self.local_heading(i, k, 1.0)[0] for i in range(n)]) for k in (0.0, 1.0, 2.0)}
        pitch = {k: [self.local_heading(i, 1.0, k)[1] for i in range(n)] for k in (0.0, 1.0, 2.0)}
        pitch_vs_side = {k: [self.local_heading(i, k, 1.0)[1] for i in range(n)] for k in (0.0, 2.0)}
        self.roll_local = [self.local_heading(i, 1.0, 1.0)[2] for i in range(n)]
        self.yaw_base, self.yaw_lin, self.yaw_quad = [], [], []
        self.pitch_base, self.pitch_lin, self.pitch_cross = [], [], []
        for i in range(n):
            y0 = yaw[1.0][i] + _wrap(yaw[0.0][i] - yaw[1.0][i])
            y2 = yaw[1.0][i] + _wrap(yaw[2.0][i] - yaw[1.0][i])
            b, l, q = quadratic_fit(y0, yaw[1.0][i], y2)
            self.yaw_base.append(b)
            self.yaw_lin.append(l)
            self.yaw_quad.append(q)
            # pitch = base + k_height * (lin + k_side * cross): the dive is the height swing's
            # vertical speed over a horizontal speed that the side swing changes
            cross = (pitch_vs_side[2.0][i] - pitch_vs_side[0.0][i]) / 2.0
            lin = (pitch[2.0][i] - pitch[0.0][i]) / 2.0 - cross
            self.pitch_lin.append(lin)
            self.pitch_cross.append(cross)
            self.pitch_base.append(pitch[1.0][i] - lin - cross)

    # -- the rig, evaluated for any control value ---------------------------------

    def rig_rotation(self, i: int, k_side: float, k_height: float) -> Mat:
        return mat_mul(rot_y(self.pivot_azimuth + k_side * self.swing_side[i]), rot_z(k_height * self.swing_height[i]))

    def world_at(self, i: int, k_side: float = 1.0, k_height: float = 1.0) -> Vec:
        return add3(self.pivot, mat_vec(self.rig_rotation(i, k_side, k_height), self.local[i]))

    def velocity_at(self, i: int, k_side: float = 1.0, k_height: float = 1.0) -> Vec:
        """Finite-difference velocity; one-sided at the impact."""
        j0, j1 = max(0, i - 1), min(F_IMPACT, i + 1)
        if i >= F_IMPACT:
            j0, j1 = F_IMPACT - 1, F_IMPACT
        return scale3(sub(self.world_at(j1, k_side, k_height), self.world_at(j0, k_side, k_height)), 1.0 / ((j1 - j0) * DT))

    def heading_track(self, k_side: float, k_height: float) -> tuple[list[float], list[float]]:
        """World (azimuth, elevation) per frame; held from the nearest flying frame where the hub is (nearly) still."""
        az, el = [], []
        for i in range(len(self.pos)):
            j = min(max(i, self.first_fly), self.last_fly)
            a, e = heading_of(self.velocity_at(j, k_side, k_height))
            az.append(a)
            el.append(e)
        return unwrap(az), el

    def local_heading(self, i: int, k_side: float, k_height: float) -> Vec:
        """(yaw, pitch, roll) of the travel direction inside the rig frame: R = Ry(yaw) Rz(pitch) Rx(roll)."""
        j = min(max(i, self.first_fly), self.last_fly)
        a, e = heading_of(self.velocity_at(j, k_side, k_height))
        bank = self.bank[i] if hasattr(self, "bank") and len(self.bank) > i else 0.0
        desired = mat_mul(mat_mul(rot_y(a), rot_z(e)), rot_x(bank))
        local = mat_mul(mat_t(self.rig_rotation(i, k_side, k_height)), desired)
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, local[1][0]))))
        yaw = math.degrees(math.atan2(-local[2][0], local[0][0]))
        roll = math.degrees(math.atan2(-local[1][2], local[1][1]))
        return yaw, pitch, roll

    def rig_forward(self, i: int, k_side: float, k_height: float) -> Vec:
        """The direction the node chain points the crystal in for a control setting (for the checks)."""
        yaw = self.yaw_base[i] + k_side * self.yaw_lin[i] + k_side * k_side * self.yaw_quad[i]
        pitch = self.pitch_base[i] + k_height * (self.pitch_lin[i] + k_side * self.pitch_cross[i])
        local = mat_mul(rot_y(yaw), rot_z(pitch))
        return mat_vec(mat_mul(self.rig_rotation(i, k_side, k_height), local), (1.0, 0.0, 0.0))


def _wrap(delta: float) -> float:
    while delta > 180.0:
        delta -= 360.0
    while delta < -180.0:
        delta += 360.0
    return delta


# ---------------------------------------------------------------------------
# document helpers
# ---------------------------------------------------------------------------


def tkey(frame: float) -> float:
    return round(frame * DT, 5)


def track(pairs: list[tuple[float, Any]], interp: str | None = None) -> dict[str, Any]:
    """A keyframed parameter {"value": first, "track": [...]} from (seconds, value) pairs."""
    keys = []
    for t, v in pairs:
        key: dict[str, Any] = {"time": round(t, 5), "value": v}
        if interp:
            key["interp"] = interp
        keys.append(key)
    return {"value": pairs[0][1], "track": keys}


def frame_track(values: list[Any], first: int = 0, last: int = F_END) -> dict[str, Any]:
    """One key per simulation frame, dropping keys that repeat their neighbours."""
    pairs: list[tuple[float, Any]] = []
    for i in range(first, last + 1):
        v = values[i]
        if first < i < last and values[i - 1] == v and values[i + 1] == v:
            continue
        pairs.append((tkey(i), v))
    return track(pairs)


def vec(v: Vec, digits: int = 4) -> list[float]:
    return [round(v[0], digits) + 0.0, round(v[1], digits) + 0.0, round(v[2], digits) + 0.0]


def node(nid: str, ntype: str, params: dict[str, Any], *, layer: str | None = None,
         parent: str | None = None, inputs: dict[str, Any] | None = None,
         seed: int | None = None) -> dict[str, Any]:
    n: dict[str, Any] = {"id": nid, "type": ntype}
    if layer:
        n["layer"] = layer
    if parent:
        n["parent"] = parent
    if seed is not None:
        n["seed"] = seed
    n["parameters"] = params
    if inputs:
        n["inputs"] = inputs
    return n


def hub(nid: str, params: dict[str, Any], *, layer: str, parent: str | None = None) -> dict[str, Any]:
    """An invisible transform node."""
    base = {"primitive": "sphere", "radius": 0.01, "segments": 6, "visible": False}
    base.update(params)
    return node(nid, "mesh", base, layer=layer, parent=parent)


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


# ---------------------------------------------------------------------------
# textures - greyscale, soft, filling their quads
# ---------------------------------------------------------------------------


def cross_profile(prefix: str) -> list[dict[str, Any]]:
    """0 at both edges of a ribbon, 0.5 on its centre line (v runs across the width)."""
    return [
        op(f"{prefix}g", "gradient_linear", {"angle": 90.0, "start": 0.0, "end": 1.0}),
        op(f"{prefix}gi", "invert", None, {"a": f"{prefix}g"}),
        op(f"{prefix}t", "math", {"mode": "min"}, {"a": f"{prefix}g", "b": f"{prefix}gi"}),
    ]


def build_textures() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out.append(tex("tex_glow", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
    ], "r"))
    out.append(tex("tex_core", 96, 96, [
        op("hot", "gradient_radial", {"radius": 0.3, "falloff": "quadratic"}),
        op("hotl", "levels", {"gamma": 0.6}, {"a": "hot"}),
        op("halo", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("halol", "levels", {"out_high": 0.42, "gamma": 0.8}, {"a": "halo"}),
        op("m", "math", {"mode": "max"}, {"a": "hotl", "b": "halol"}),
    ], "m"))
    out.append(tex("tex_mote", 32, 32, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        op("lv", "levels", {"gamma": 0.8}, {"a": "r"}),
    ], "lv"))
    # the aura: a thin bubble rim that is brightest ahead of the crystal and open behind it
    out.append(tex("tex_aura", 128, 128, [
        op("rim", "ring", {"radius": 0.45, "thickness": 0.012, "softness": 0.022}),
        op("glow", "ring", {"radius": 0.44, "thickness": 0.05, "softness": 0.07}),
        op("glowl", "levels", {"out_high": 0.22}, {"a": "glow"}),
        op("shell", "math", {"mode": "max"}, {"a": "rim", "b": "glowl"}),
        op("front", "gradient_linear", {"angle": 90.0, "start": 1.0, "end": 0.0}),
        op("frontl", "levels", {"in_low": 0.2, "in_high": 0.95, "out_low": 0.05, "gamma": 0.8}, {"a": "front"}),
        op("bow", "math", {"mode": "multiply"}, {"a": "shell", "b": "frontl"}),
    ], "bow"))
    # a thin soft ring, slightly uneven so it never looks plotted
    out.append(tex("tex_ring", 128, 128, [
        op("ring", "ring", {"radius": 0.45, "thickness": 0.02, "softness": 0.03}),
        op("halo", "ring", {"radius": 0.44, "thickness": 0.06, "softness": 0.07}),
        op("halol", "levels", {"out_high": 0.32}, {"a": "halo"}),
        op("m", "math", {"mode": "max"}, {"a": "ring", "b": "halol"}),
        op("n", "fbm", {"frequency": 2.2, "octaves": 2, "seed": 71}),
        op("nl", "levels", {"in_low": 0.25, "in_high": 0.8, "out_low": 0.45}, {"a": "n"}),
        op("o", "math", {"mode": "multiply"}, {"a": "m", "b": "nl"}),
    ], "o"))
    # impact rays: eleven and seven soft spokes at unrelated angles, bent and broken by noise
    out.append(tex("tex_rays", 256, 256, [
        op("s1", "spokes", {"count": 11, "width": 0.03, "softness": 0.03, "inner_radius": 0.03,
                            "outer_radius": 0.5, "rotation": 7.0}),
        op("s2", "spokes", {"count": 7, "width": 0.016, "softness": 0.02, "inner_radius": 0.02,
                            "outer_radius": 0.44, "rotation": 31.0}),
        op("s2l", "levels", {"out_high": 0.8}, {"a": "s2"}),
        op("s", "math", {"mode": "max"}, {"a": "s1", "b": "s2l"}),
        op("w", "fbm", {"frequency": 2.4, "octaves": 2, "seed": 913}),
        op("bent", "distort", {"amount": 0.05}, {"a": "s", "by": "w"}),
        op("n", "fbm", {"frequency": 5.0, "octaves": 3, "seed": 377}),
        op("nl", "levels", {"in_low": 0.3, "in_high": 0.72, "out_low": 0.08}, {"a": "n"}),
        op("broken", "math", {"mode": "multiply"}, {"a": "bent", "b": "nl"}),
        op("fall", "gradient_radial", {"radius": 0.5, "falloff": "quadratic"}),
        op("rays", "math", {"mode": "multiply"}, {"a": "broken", "b": "fall"}),
        op("soft", "blur", {"radius": 2.2}, {"a": "rays"}),
        op("lv", "levels", {"in_high": 0.55, "gamma": 0.85}, {"a": "soft"}),
    ], "lv"))
    # ribbon body: a wide soft bump across the width, streaked along the length, with a
    # thin brighter filament on the centre line.  u tiles once per metre, so the noise tiles.
    out.append(tex("tex_ribbon", 128, 64, cross_profile("") + [
        op("body", "levels", {"in_high": 0.5, "gamma": 0.6}, {"a": "t"}),
        op("n", "fbm", {"frequency": 3.0, "octaves": 3, "seed": 433, "tile": True}),
        op("nl", "levels", {"in_low": 0.28, "in_high": 0.74, "out_low": 0.3}, {"a": "n"}),
        op("streaks", "math", {"mode": "multiply"}, {"a": "body", "b": "nl"}),
        op("bodyl", "levels", {"out_high": 0.62}, {"a": "streaks"}),
        op("line", "levels", {"in_low": 0.385, "in_high": 0.5, "gamma": 0.75}, {"a": "t"}),
        op("n2", "fbm", {"frequency": 2.0, "octaves": 2, "seed": 877, "tile": True}),
        op("n2l", "levels", {"in_low": 0.3, "in_high": 0.7, "out_low": 0.4}, {"a": "n2"}),
        op("filament", "math", {"mode": "multiply"}, {"a": "line", "b": "n2l"}),
        op("m", "math", {"mode": "max"}, {"a": "bodyl", "b": "filament"}),
    ], "m"))
    out.append(tex("tex_filament", 8, 32, cross_profile("") + [
        op("lv", "levels", {"in_high": 0.5, "gamma": 0.5}, {"a": "t"}),
    ], "lv"))
    out.append(tex("tex_mist", 64, 64, [
        op("r", "gradient_radial", {"radius": 0.5, "falloff": "smooth"}),
        op("n", "fbm", {"frequency": 2.6, "octaves": 3, "seed": 191}),
        op("nl", "levels", {"in_low": 0.2, "in_high": 0.85, "out_low": 0.35}, {"a": "n"}),
        op("m", "math", {"mode": "multiply"}, {"a": "r", "b": "nl"}),
    ], "m"))
    return out


# ---------------------------------------------------------------------------
# the effect
# ---------------------------------------------------------------------------

LAYERS = [
    {"id": "charge", "name": "Spawn and launch", "role": "telegraph"},
    {"id": "head", "name": "Crystal head", "role": "primary"},
    {"id": "braid", "name": "Energy braid", "role": "primary"},
    {"id": "fragments", "name": "Orbiting fragments", "role": "secondary"},
    {"id": "motes", "name": "Sparkle motes", "role": "secondary"},
    {"id": "impact", "name": "Impact", "role": "interaction"},
    {"id": "aftermath", "name": "Dissipate", "role": "aftermath"},
    {"id": "light", "name": "Lighting", "role": "interaction"},
]

# camera: raised, from the front right, so the hook curls down and back on screen
CAMERA = {"position": [5.2, 3.9, 9.6], "target": [0.35, 1.7, -0.3], "fov": 40.0}

# the crystal: a hexagonal bipyramid, long and pointed ahead, short behind - the hero of the effect
GEM_RADIUS = 0.30
GEM_FRONT = 0.98
GEM_BACK = 0.50
GEM_GIRDLE_X = -0.10
GEM_TAIL_X = GEM_GIRDLE_X - GEM_BACK

# three orbiting shards: (x along the axis, orbit radius, revs per second, phase, tilt y, tilt z, size, sides)
SHARDS = [
    (0.28, 0.64, 1.25, 20.0, 14.0, -9.0, 1.00, 4),
    (-0.08, 0.78, -0.98, 150.0, -17.0, 12.0, 0.80, 6),
    (-0.40, 0.56, 1.6, 265.0, 8.0, 19.0, 0.66, 4),
]

BRAID_HUBS = [
    # hub id, metres of travel per turn (negative = counter-rotating), phase
    ("braid_a", 3.0, 0.0),
    ("braid_b", -4.1, 70.0),
    ("braid_c", 1.9, 200.0),
]


def build() -> dict[str, Any]:
    fl = Flight()
    n = len(fl.pos)
    nodes: list[dict[str, Any]] = []
    add = nodes.append

    hot, violet, deep = WHITE_HOT, VIOLET, DEEP

    nodes.extend(build_textures())

    # ---------------- materials ----------------
    add(node("mat_glow", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": rgba(violet),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.2,
    }))
    add(node("mat_spark", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": rgba(hot),
        "emissive_intensity": 0.35, "soft_particle": True, "depth_fade": 0.08,
    }))
    add(node("mat_ribbon", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": rgba(violet),
        "emissive_intensity": 0.25, "soft_particle": True, "depth_fade": 0.12, "double_sided": True,
    }, inputs={"base_texture": "tex_ribbon"}))
    add(node("mat_filament", "material", {
        "blend": "additive", "base_color": [1.0, 1.0, 1.0, 1.0], "emissive_color": rgba(ICE),
        "emissive_intensity": 0.3, "soft_particle": True, "depth_fade": 0.1, "double_sided": True,
    }, inputs={"base_texture": "tex_filament"}))
    # the gem: translucent violet facets that catch the key light, over a white-hot inner crystal
    add(node("mat_gem_shell", "material", {
        "blend": "alpha", "shading": "lit", "base_color": [0.3, 0.1, 0.72, 1.0],
        "emissive_color": rgba(violet), "emissive_intensity": 0.05, "opacity": 0.72,
    }))
    add(node("mat_gem_cut", "material", {
        "blend": "alpha", "shading": "lit", "base_color": rgba(mix(AZURE, violet, 0.35), 1.0, 0.8),
        "emissive_color": rgba(mix(violet, AZURE, 0.5)), "emissive_intensity": 0.05, "opacity": 0.34,
    }))
    add(node("mat_gem_core", "material", {
        "blend": "alpha", "shading": "lit", "base_color": rgba(hot), "emissive_color": rgba(hot),
        "emissive_intensity": 0.5, "opacity": 1.0,
    }))
    add(node("mat_fragment", "material", {
        "blend": "alpha", "shading": "lit", "base_color": [0.34, 0.14, 0.78, 1.0],
        "emissive_color": rgba(violet), "emissive_intensity": 0.1, "opacity": 1.0,
    }))
    add(node("mat_burst_shard", "material", {
        "blend": "alpha", "shading": "lit", "base_color": [0.3, 0.12, 0.7, 1.0],
        "emissive_color": rgba(mix(violet, MAGENTA, 0.2)), "emissive_intensity": 0.22,
        "fresnel_power": 3.2, "double_sided": True, "opacity": 1.0,
    }))

    # ---------------- forces ----------------
    add(node("f_drift", "force", {"force_type": "curl_noise", "strength": 0.9, "frequency": 1.3,
                                  "octaves": 2, "speed": 0.6}))
    add(node("f_fall", "force", {"force_type": "gravity", "strength": 4.2, "direction": [0.0, -1.0, 0.0]}))

    # ---------------- the homing rig ----------------
    add(hub("aim_pivot", {"position": vec(fl.pivot), "rotation": [0.0, round(fl.pivot_azimuth, 3), 0.0]},
            layer="head"))
    add(hub("aim_side", {"rotation": frame_track([[0.0, round(a, 3) + 0.0, 0.0] for a in fl.swing_side])},
            layer="head", parent="aim_pivot"))
    add(hub("aim_height", {"rotation": frame_track([[0.0, 0.0, round(a, 3) + 0.0] for a in fl.swing_height])},
            layer="head", parent="aim_side"))
    add(hub("missile", {"position": frame_track([vec(p) for p in fl.local])}, layer="head", parent="aim_height"))

    # tangent orientation: yaw = base + k_side * lin + k_side^2 * quad,
    # pitch = base + k_height * (lin + k_side * cross), roll = bank into the turn
    def y_track(values: list[float]) -> dict[str, Any]:
        return frame_track([[0.0, round(v, 3) + 0.0, 0.0] for v in values])

    def z_track(values: list[float]) -> dict[str, Any]:
        return frame_track([[0.0, 0.0, round(v, 3) + 0.0] for v in values])

    add(hub("head_yaw", {"rotation": y_track(fl.yaw_base)}, layer="head", parent="missile"))
    add(hub("head_yaw_k1", {"rotation": y_track(fl.yaw_lin)}, layer="head", parent="head_yaw"))
    add(hub("head_yaw_k2", {"rotation": y_track(fl.yaw_quad)}, layer="head", parent="head_yaw_k1"))
    add(hub("head_pitch_k1", {"rotation": z_track(fl.pitch_lin)}, layer="head", parent="head_yaw_k2"))
    add(hub("head_pitch_k2", {"rotation": z_track(fl.pitch_cross)}, layer="head", parent="head_pitch_k1"))
    add(hub("head_tilt", {"rotation": frame_track([[round(r, 3) + 0.0, 0.0, round(p, 3) + 0.0]
                                                   for r, p in zip(fl.roll_local, fl.pitch_base)])},
            layer="head", parent="head_pitch_k2"))
    # everything that scales with the missile hangs off `body`
    add(hub("body", {"scale": [1.0, 1.0, 1.0]}, layer="head", parent="head_tilt"))

    # ---------------- the crystal head ----------------
    form = [(0.0, 0.001), (T_LAUNCH, 0.001), (0.25, 0.5), (0.31, 1.14), (0.38, 0.97), (0.44, 1.0),
            (1.30, 1.0), (1.385, 1.1)]
    add(hub("head_form", {"scale": track([(t, [round(s, 4), round(s, 4), round(s * 0.86, 4)]) for t, s in form])},
            layer="head", parent="body"))
    gem_window = {"start_time": round(T_LAUNCH, 4), "duration": round(T_IMPACT - T_LAUNCH - 0.006, 4)}
    shell_glow = track([(T_LAUNCH, 0.3), (0.3, 1.0), (0.44, 0.3), (1.2, 0.3), (1.39, 0.9)])
    cut_glow = track([(T_LAUNCH, 0.2), (0.3, 0.8), (0.44, 0.25), (1.2, 0.25), (1.39, 0.7)])
    core_glow = track([(T_LAUNCH, 2.5), (0.3, 7.0), (0.44, 4.2), (1.2, 4.2), (1.39, 8.0)])
    # (id, material, radius x, front x, back x, emissive, spin about the axis)
    gem_parts = (
        ("gem_shell", "mat_gem_shell", 1.0, 1.0, 1.0, shell_glow, 0.0),
        ("gem_cut", "mat_gem_cut", 0.94, 0.8, 0.84, cut_glow, 30.0),
        ("gem_core", "mat_gem_core", 0.3, 0.34, 0.36, core_glow, 30.0),
    )
    for nid, mat, kr, kf, kb, glow, spin in gem_parts:
        for part, height, rot, sign in (("front", GEM_FRONT * kf, -90.0, 1.0), ("back", GEM_BACK * kb, 90.0, -1.0)):
            add(node(f"{nid}_{part}", "mesh", {
                "primitive": "crystal", "radius": round(GEM_RADIUS * kr, 4), "height": round(height, 4),
                "segments": 6, "irregularity": 0.0,
                "position": [round(GEM_GIRDLE_X + sign * height / 2.0, 4), 0.0, 0.0],
                "rotation": [0.0, spin, rot], "color": [1.0, 1.0, 1.0, 1.0], "emissive": glow, **gem_window,
            }, layer="head", parent="head_form", inputs={"material": mat}, seed=11))

    # follow sprites: emitted one frame of travel behind the hub with the hub's own velocity
    add(hub("follow_anchor", {"position": frame_track([[round(0.12 - fl.speed[i] * DT, 4) + 0.0, 0.0, 0.0]
                                                       if F_LAUNCH < i < F_IMPACT else [0.0, 0.0, 0.0]
                                                       for i in range(n)])},
            layer="head", parent="head_tilt"))
    add(node("ps_halo", "particle_system", {
        "max_particles": 24, "lifetime": 0.05, "size": 1.25,
        "color": rgba(violet), "color_over_life": [[0.0, rgba(mix(violet, hot, 0.25))], [1.0, rgba(violet)]],
        "opacity": 0.05, "opacity_over_life": [[0.0, 0.6], [0.4, 1.0], [1.0, 0.0]],
        "emissive": 1.0, "render_mode": "stretched_billboard", "velocity_stretch": 0.085, "blend": "additive",
        "soft_particle_distance": 0.3,
    }, layer="head", inputs={"sprite": "tex_glow", "material": "mat_glow"}))
    add(node("e_halo", "emitter", {
        "shape": "point", "velocity": 0.0, "inherit_velocity": 1.0,
        "rate": track([(0.0, 0.0), (0.04, 120.0), (T_IMPACT - 0.02, 120.0), (T_IMPACT, 0.0)]),
        "duration": round(T_IMPACT + 0.01, 4),
    }, layer="head", parent="follow_anchor", inputs={"particle": "ps_halo"}))
    add(node("ps_aura", "particle_system", {
        "max_particles": 24, "lifetime": 0.05, "size": 1.56,
        "color": rgba(mix(violet, AZURE, 0.35)), "opacity": 0.1,
        "opacity_over_life": [[0.0, 0.7], [0.4, 1.0], [1.0, 0.0]],
        "emissive": 1.6, "render_mode": "billboard", "align_to_velocity": True, "blend": "additive",
        "soft_particle_distance": 0.1,
    }, layer="head", inputs={"sprite": "tex_aura", "material": "mat_glow"}))
    add(node("e_aura", "emitter", {
        "shape": "point", "velocity": 0.0, "inherit_velocity": 1.0,
        "rate": track([(0.26, 0.0), (0.34, 120.0), (T_IMPACT - 0.02, 120.0), (T_IMPACT, 0.0)]),
        "start_time": 0.26, "duration": round(T_IMPACT - 0.25, 4),
    }, layer="head", parent="follow_anchor", inputs={"particle": "ps_aura"}))

    # ---------------- the braid ----------------
    for hid, pitch, phase in BRAID_HUBS:
        angles = [[round(phase + 360.0 * fl.travel[i] / pitch, 2) + 0.0, 0.0, 0.0] for i in range(n)]
        add(hub(hid, {"position": [round(GEM_TAIL_X - 0.06, 4), 0.0, 0.0], "rotation": frame_track(angles)},
                layer="braid", parent="body"))

    ribbon_taper = [[0.0, 0.0], [0.05, 0.45], [0.16, 1.0], [0.55, 0.85], [1.0, 0.15]]
    ribbon_fade = [[0.0, 0.0], [0.06, 1.0], [0.4, 0.62], [1.0, 0.0]]
    ribbons = [
        # id, hub, (y, z), colour, width, emissive, lifetime, material
        ("ribbon_violet", "braid_a", (0.17, 0.0), mix(violet, MAGENTA, 0.15), 0.4, 2.3, 0.5, "mat_ribbon"),
        ("ribbon_magenta", "braid_a", (-0.17, 0.0), mix(violet, MAGENTA, 0.7), 0.34, 2.0, 0.46, "mat_ribbon"),
        ("ribbon_azure", "braid_b", (0.0, 0.25), AZURE, 0.3, 1.9, 0.54, "mat_ribbon"),
        ("filament_a", "braid_c", (0.1, 0.0), mix(ICE, violet, 0.25), 0.055, 1.4, 0.36, "mat_filament"),
        ("filament_b", "braid_c", (-0.1, 0.0), mix(ICE, violet, 0.5), 0.046, 1.2, 0.32, "mat_filament"),
    ]
    for rid, hid, (oy, oz), colour, width, emissive, life, mat in ribbons:
        add(hub(f"{rid}_src", {"position": [0.0, oy, oz]}, layer="braid", parent=hid))
        add(node(rid, "trail", {
            "lifetime": life, "max_segments": 64, "min_vertex_distance": 0.02,
            "taper": ribbon_taper, "opacity_over_life": ribbon_fade,
            "color": rgba(colour), "blend": "additive", "uv_scroll": -0.9,
            "width": track([(0.22, 0.0), (0.32, round(width * 0.6, 4)), (0.46, width), (T_IMPACT, width),
                            (1.56, round(width * 0.7, 4)), (1.74, 0.0)]),
            "emissive": track([(0.22, round(emissive * 0.4, 3)), (0.4, emissive), (1.3, emissive),
                               (T_IMPACT, round(emissive * 1.4, 3)), (1.7, 0.0)]),
            "start_time": 0.22,
        }, layer="braid", inputs={"source": f"{rid}_src", "material": mat}))

    # ---------------- orbiting shards ----------------
    for i, (x, radius, revs, phase, tilt_y, tilt_z, size, sides) in enumerate(SHARDS, start=1):
        add(hub(f"orbit_{i}", {"position": [x, 0.0, 0.0], "rotation": [0.0, tilt_y, tilt_z]},
                layer="fragments", parent="body"))
        add(hub(f"orbit_{i}_spin", {"rotation": track([(0.0, [phase, 0.0, 0.0]),
                                                       (DURATION, [round(phase + 360.0 * revs * DURATION, 2), 0.0, 0.0])])},
                layer="fragments", parent=f"orbit_{i}"))
        pop = [(0.0, 0.001), (0.3 + 0.03 * i, 0.001), (0.4 + 0.03 * i, 1.12), (0.47 + 0.03 * i, 1.0),
               (1.34, 1.0), (1.39, 0.001)]
        add(hub(f"shard_{i}", {
            "position": [0.0, radius, 0.0],
            "rotation": track([(0.0, [0.0, 0.0, 0.0]),
                               (DURATION, [round(190.0 * (1 + 0.3 * i), 1), round(-140.0 + 60.0 * i, 1), 0.0])]),
            "scale": track([(t, [round(s * size, 4), round(s * size, 4), round(s * size * 0.8, 4)]) for t, s in pop]),
        }, layer="fragments", parent=f"orbit_{i}_spin"))
        window = {"start_time": round(0.3 + 0.03 * i, 4), "duration": round(T_IMPACT - 0.306 - 0.03 * i, 4)}
        for part, height, rot, sign in (("front", 0.38, -90.0, 1.0), ("back", 0.2, 90.0, -1.0)):
            add(node(f"shard_{i}_{part}", "mesh", {
                "primitive": "crystal", "radius": 0.1, "height": height, "segments": sides, "irregularity": 0.0,
                "position": [round(sign * height / 2.0, 4), 0.0, 0.0], "rotation": [0.0, 0.0, rot],
                "color": [1.0, 1.0, 1.0, 1.0], "emissive": 0.4, **window,
            }, layer="fragments", parent=f"shard_{i}", inputs={"material": "mat_fragment"}, seed=20 + i))
        w = round(0.1 * size, 4)
        add(node(f"shard_{i}_trail", "trail", {
            "lifetime": 0.12, "max_segments": 32, "min_vertex_distance": 0.02,
            "taper": [[0.0, 0.2], [0.2, 1.0], [1.0, 0.0]], "opacity_over_life": [[0.0, 0.55], [0.5, 0.3], [1.0, 0.0]],
            "color": rgba(mix(violet, ICE, 0.25)), "blend": "additive", "emissive": 0.8,
            "width": track([(0.34, 0.0), (0.48, w), (1.34, w), (T_IMPACT, 0.0)]),
            "start_time": 0.34,
        }, layer="fragments", inputs={"source": f"shard_{i}", "material": "mat_filament"}))

    # ---------------- sparkle motes and the soft haze along the braid ----------------
    # a fine trickle: a flurry at the kick, steady through the mid flight, thicker in the hook
    flight_rate = [(0.0, 0.0), (0.22, 0.0), (0.27, 1.7), (0.44, 1.0), (1.0, 1.0), (1.12, 1.75), (1.37, 1.75),
                   (T_IMPACT, 0.0)]

    def rate(scale: float) -> dict[str, Any]:
        return track([(t, round(v * scale, 2)) for t, v in flight_rate])

    twinkle = [[0.0, 0.3], [0.12, 1.0], [0.3, 0.55], [0.46, 1.0], [0.64, 0.45], [0.8, 0.8], [1.0, 0.0]]
    add(node("ps_motes", "particle_system", {
        "max_particles": 360, "lifetime": 0.62, "lifetime_variance": 0.24, "size": 0.046, "size_variance": 0.026,
        "size_over_life": twinkle, "color": rgba(hot),
        "color_over_life": [[0.0, rgba(hot)], [0.35, rgba(mix(violet, hot, 0.3))], [1.0, rgba(deep)]],
        "opacity_over_life": [[0.0, 1.0], [0.7, 0.8], [1.0, 0.0]], "emissive": 3.4, "drag": 1.6,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.05,
    }, layer="motes", inputs={"sprite": "tex_mote", "material": "mat_spark", "forces": ["f_drift"]}))
    add(node("e_motes", "emitter", {
        "shape": "sphere", "radius": 0.26, "position": [round(GEM_TAIL_X, 4), 0.0, 0.0], "direction": [0.0, 0.0, 0.0],
        "velocity": 0.55, "velocity_variance": 0.45, "inherit_velocity": 0.1, "rate": rate(200.0),
        "start_time": 0.22, "duration": round(T_IMPACT - 0.22, 4),
    }, layer="motes", parent="body", inputs={"particle": "ps_motes"}))
    # a few larger, slower twinkles with a soft halo (round: no star glyphs)
    add(node("ps_twinkles", "particle_system", {
        "max_particles": 50, "lifetime": 0.5, "lifetime_variance": 0.15, "size": 0.2, "size_variance": 0.07,
        "size_over_life": [[0.0, 0.0], [0.18, 1.0], [0.4, 0.45], [0.6, 0.9], [1.0, 0.0]],
        "color": rgba(mix(hot, AZURE, 0.3)), "color_over_life": [[0.0, rgba(hot)], [1.0, rgba(mix(violet, AZURE, 0.4))]],
        "opacity": 0.8, "opacity_over_life": [[0.0, 1.0], [0.75, 0.8], [1.0, 0.0]],
        "emissive": 2.2, "drag": 2.0, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.05,
    }, layer="motes", inputs={"sprite": "tex_core", "material": "mat_spark", "forces": ["f_drift"]}))
    add(node("e_twinkles", "emitter", {
        "shape": "sphere", "radius": 0.36, "position": [round(GEM_TAIL_X - 0.1, 4), 0.0, 0.0],
        "direction": [0.0, 0.0, 0.0], "velocity": 0.4, "velocity_variance": 0.3, "inherit_velocity": 0.06,
        "rate": rate(30.0), "start_time": 0.22, "duration": round(T_IMPACT - 0.22, 4),
    }, layer="motes", parent="body", inputs={"particle": "ps_twinkles"}))
    add(node("ps_haze", "particle_system", {
        "max_particles": 40, "lifetime": 0.44, "lifetime_variance": 0.1, "size": 0.8, "size_variance": 0.2,
        "size_over_life": [[0.0, 0.5], [0.4, 1.0], [1.0, 1.25]], "rotation_variance": 180.0,
        "color": rgba(mix(deep, violet, 0.5)), "opacity": 0.16,
        "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.55, 0.6], [1.0, 0.0]], "emissive": 0.9, "drag": 1.0,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.25,
    }, layer="braid", inputs={"sprite": "tex_mist", "material": "mat_glow"}))
    add(node("e_haze", "emitter", {
        "shape": "sphere", "radius": 0.14, "position": [round(GEM_TAIL_X - 0.2, 4), 0.0, 0.0], "velocity": 0.0,
        "inherit_velocity": 0.08, "rate": rate(50.0), "start_time": 0.22,
        "duration": round(T_IMPACT - 0.22, 4),
    }, layer="braid", parent="body", inputs={"particle": "ps_haze"}))

    # ---------------- spawn / charge and the launch kick (at the missile hub, which is still here) ----------------
    add(node("ps_orb", "particle_system", {
        "max_particles": 4, "lifetime": 0.34, "size": 1.3,
        "size_over_life": [[0.0, 0.12], [0.45, 0.62], [0.6, 0.72], [0.72, 1.25], [1.0, 0.3]],
        "color": rgba(mix(violet, hot, 0.3)),
        "color_over_life": [[0.0, rgba(violet)], [0.6, rgba(mix(violet, hot, 0.5))], [0.74, rgba(hot)], [1.0, rgba(violet)]],
        "opacity": 0.9, "opacity_over_life": [[0.0, 0.0], [0.2, 0.75], [0.7, 1.0], [1.0, 0.0]],
        "emissive": 2.0, "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.2,
    }, layer="charge", inputs={"sprite": "tex_core", "material": "mat_glow"}))
    add(node("e_orb", "emitter", {
        "shape": "point", "velocity": 0.0, "rate": 0.0, "burst_count": 1, "burst_times": [0.0], "duration": 0.1,
    }, layer="charge", parent="missile", inputs={"particle": "ps_orb"}))
    add(node("ps_halo_ring", "particle_system", {
        "max_particles": 6, "lifetime": 0.24, "size": 2.0,
        "size_over_life": [[0.0, 1.0], [0.7, 0.42], [1.0, 0.3]],
        "color": rgba(mix(violet, AZURE, 0.3)), "opacity": 0.75,
        "opacity_over_life": [[0.0, 0.0], [0.3, 1.0], [0.8, 0.8], [1.0, 0.0]], "emissive": 2.4,
        "rotation_variance": 180.0, "render_mode": "billboard", "blend": "additive",
        "soft_particle_distance": 0.1,
    }, layer="charge", inputs={"sprite": "tex_ring", "material": "mat_glow"}))
    add(node("e_halo_ring", "emitter", {
        "shape": "point", "velocity": 0.0, "rate": 0.0, "burst_count": 1, "burst_times": [0.0, 0.07],
        "duration": 0.15,
    }, layer="charge", parent="missile", inputs={"particle": "ps_halo_ring"}))
    add(node("ps_gather", "particle_system", {
        "max_particles": 110, "lifetime": 0.2, "lifetime_variance": 0.03, "size": 0.06, "size_variance": 0.025,
        "size_over_life": [[0.0, 0.4], [0.5, 1.0], [1.0, 0.5]], "color": rgba(mix(violet, hot, 0.5)),
        "color_over_life": [[0.0, rgba(violet)], [1.0, rgba(hot)]],
        "opacity_over_life": [[0.0, 0.0], [0.25, 1.0], [0.85, 1.0], [1.0, 0.0]], "emissive": 3.0,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.16, "blend": "additive",
        "soft_particle_distance": 0.05,
    }, layer="charge", inputs={"sprite": "tex_mote", "material": "mat_spark"}))
    add(node("e_gather", "emitter", {
        "shape": "sphere", "radius": 1.1, "surface_only": True, "direction": [0.0, 0.0, 0.0], "velocity": 0.0,
        "radial_velocity": -5.2, "rate": track([(0.0, 300.0), (0.12, 220.0), (0.17, 0.0)]), "duration": 0.18,
    }, layer="charge", parent="missile", inputs={"particle": "ps_gather"}))
    # the kick: a soft ring left hanging at the launch point and a fan of sparks thrown backwards
    add(node("ps_kick_ring", "particle_system", {
        "max_particles": 2, "lifetime": 0.3, "size": 2.0,
        "size_over_life": [[0.0, 0.18], [0.4, 0.75], [1.0, 1.0]], "color": rgba(violet), "opacity": 0.3,
        "opacity_over_life": [[0.0, 0.9], [0.4, 0.5], [1.0, 0.0]], "emissive": 1.8,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.1,
    }, layer="charge", inputs={"sprite": "tex_ring", "material": "mat_glow"}))
    add(node("e_kick_ring", "emitter", {
        "shape": "point", "position": vec(fl.pos[F_LAUNCH]), "velocity": 0.0, "rate": 0.0, "burst_count": 1,
        "burst_times": [0.0], "start_time": round(T_LAUNCH + 0.01, 4), "duration": 0.1,
    }, layer="charge", inputs={"particle": "ps_kick_ring"}))
    add(node("ps_kick_sparks", "particle_system", {
        "max_particles": 70, "lifetime": 0.32, "lifetime_variance": 0.12, "size": 0.05, "size_variance": 0.02,
        "size_over_life": [[0.0, 1.0], [1.0, 0.1]], "color": rgba(hot),
        "color_over_life": [[0.0, rgba(hot)], [0.5, rgba(violet)], [1.0, rgba(deep)]],
        "opacity_over_life": [[0.0, 1.0], [0.6, 0.8], [1.0, 0.0]], "emissive": 3.2, "drag": 4.0,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.14, "blend": "additive",
        "soft_particle_distance": 0.05,
    }, layer="charge", inputs={"sprite": "tex_mote", "material": "mat_spark", "forces": ["f_drift"]}))
    launch_back = scale3(direction_of(fl.azimuth[F_LAUNCH + 2], fl.elevation[F_LAUNCH + 2]), -1.0)
    add(node("e_kick_sparks", "emitter", {
        "shape": "sphere", "radius": 0.14, "position": vec(fl.pos[F_LAUNCH]), "direction": vec(launch_back, 3),
        "spread": 38.0, "velocity": 3.8, "velocity_variance": 2.4, "rate": 0.0, "burst_count": 54,
        "burst_times": [0.0], "start_time": round(T_LAUNCH + 0.01, 4), "duration": 0.1,
    }, layer="charge", inputs={"particle": "ps_kick_sparks"}))

    # ---------------- impact (parented to the missile hub, so it lands wherever the target is dragged) ----------------
    hit = round(T_IMPACT - 0.005, 4)      # between two frames: the burst fires on the impact frame

    def burst(eid: str, system: str, count: int, params: dict[str, Any], layer: str = "impact",
              times: list[float] | None = None) -> None:
        p = {"rate": 0.0, "burst_count": count, "burst_times": times or [0.0], "start_time": hit, "duration": 0.2}
        p.update(params)
        add(node(eid, "emitter", p, layer=layer, parent="missile", inputs={"particle": system}))

    add(node("ps_flash", "particle_system", {
        "max_particles": 3, "lifetime": 0.17, "size": 4.8,
        "size_over_life": [[0.0, 0.45], [0.18, 1.0], [1.0, 0.7]], "color": rgba(hot),
        "color_over_life": [[0.0, rgba(hot)], [0.35, rgba(mix(violet, hot, 0.5))], [1.0, rgba(violet)]],
        "opacity": 1.0, "opacity_over_life": [[0.0, 1.0], [0.3, 0.75], [1.0, 0.0]], "emissive": 3.8,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.3,
    }, layer="impact", inputs={"sprite": "tex_core", "material": "mat_glow"}))
    burst("e_flash", "ps_flash", 1, {"shape": "point", "velocity": 0.0})
    add(node("ps_rays", "particle_system", {
        "max_particles": 8, "lifetime": 0.25, "lifetime_variance": 0.03, "size": 5.0, "size_variance": 1.2,
        "size_over_life": [[0.0, 0.5], [0.25, 0.92], [1.0, 1.15]], "rotation_variance": 180.0,
        "angular_velocity_variance": 25.0, "color": rgba(mix(violet, hot, 0.5)),
        "color_over_life": [[0.0, rgba(hot)], [0.5, rgba(mix(violet, hot, 0.3))], [1.0, rgba(violet)]],
        "opacity": 0.9, "opacity_over_life": [[0.0, 1.0], [0.35, 0.7], [1.0, 0.0]], "emissive": 2.9,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.3,
    }, layer="impact", inputs={"sprite": "tex_rays", "material": "mat_glow"}))
    burst("e_rays", "ps_rays", 5, {"shape": "point", "velocity": 0.0})
    add(node("ps_shock", "particle_system", {
        "max_particles": 2, "lifetime": 0.36, "size": 3.2,
        "size_over_life": [[0.0, 0.1], [0.25, 0.55], [0.6, 0.85], [1.0, 1.0]], "rotation_variance": 180.0,
        "color": rgba(mix(violet, AZURE, 0.25)), "opacity": 0.36,
        "opacity_over_life": [[0.0, 1.0], [0.4, 0.55], [1.0, 0.0]], "emissive": 1.6,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.2,
    }, layer="impact", inputs={"sprite": "tex_ring", "material": "mat_glow"}))
    burst("e_shock", "ps_shock", 1, {"shape": "point", "velocity": 0.0})
    add(node("ps_streaks", "particle_system", {
        "max_particles": 60, "lifetime": 0.24, "lifetime_variance": 0.08, "size": 0.085, "size_variance": 0.03,
        "size_over_life": [[0.0, 1.0], [0.6, 0.8], [1.0, 0.0]], "color": rgba(hot),
        "color_over_life": [[0.0, rgba(hot)], [0.45, rgba(mix(violet, hot, 0.35))], [1.0, rgba(violet)]],
        "opacity_over_life": [[0.0, 1.0], [0.6, 0.7], [1.0, 0.0]], "emissive": 3.6, "drag": 7.0,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.3, "blend": "additive",
        "soft_particle_distance": 0.05,
    }, layer="impact", inputs={"sprite": "tex_mote", "material": "mat_spark"}))
    burst("e_streaks", "ps_streaks", 38, {"shape": "sphere", "radius": 0.1, "direction": [0.0, 0.0, 0.0],
                                          "velocity": 11.0, "velocity_variance": 6.0})
    add(node("mesh_burst_shard", "mesh", {
        "primitive": "crystal", "radius": 0.36, "height": 1.0, "segments": 5, "irregularity": 0.55,
        "variants": 6, "visible": False,
    }, seed=77))
    add(node("ps_burst_shards", "particle_system", {
        "max_particles": 60, "lifetime": 0.42, "lifetime_variance": 0.09, "size": 0.2, "size_variance": 0.1,
        "size_over_life": [[0.0, 0.35], [0.1, 1.0], [0.62, 0.85], [1.0, 0.0]], "color": rgba(mix(violet, hot, 0.3)),
        "emissive": 1.0, "render_mode": "mesh", "blend": "alpha", "orientation": "tumble",
        "angular_velocity": 340.0, "angular_velocity_variance": 160.0, "mesh_scale": [0.6, 1.4, 0.5],
        "mesh_scale_variance": [0.15, 0.5, 0.12], "drag": 3.2,
    }, layer="impact", inputs={"mesh": "mesh_burst_shard", "material": "mat_burst_shard", "forces": ["f_fall"]}))
    burst("e_burst_shards", "ps_burst_shards", 36, {"shape": "sphere", "radius": 0.16, "direction": [0.0, 0.0, 0.0],
                                                    "velocity": 6.4, "velocity_variance": 3.0})
    add(node("ps_burst_sparks", "particle_system", {
        "max_particles": 200, "lifetime": 0.32, "lifetime_variance": 0.13, "size": 0.046, "size_variance": 0.022,
        "size_over_life": [[0.0, 1.0], [0.5, 0.8], [1.0, 0.0]], "color": rgba(hot),
        "color_over_life": [[0.0, rgba(hot)], [0.4, rgba(mix(violet, hot, 0.3))], [1.0, rgba(deep)]],
        "opacity_over_life": [[0.0, 1.0], [0.7, 0.8], [1.0, 0.0]], "emissive": 3.4, "drag": 4.6,
        "render_mode": "stretched_billboard", "velocity_stretch": 0.07, "blend": "additive",
        "soft_particle_distance": 0.05,
    }, layer="impact", inputs={"sprite": "tex_mote", "material": "mat_spark", "forces": ["f_drift", "f_fall"]}))
    burst("e_burst_sparks", "ps_burst_sparks", 140, {"shape": "sphere", "radius": 0.12, "direction": [0.0, 0.0, 0.0],
                                                     "velocity": 5.2, "velocity_variance": 3.6})

    # ---------------- dissipate: a loose ring of motes that hangs and fades ----------------
    add(node("ps_ring_motes", "particle_system", {
        "max_particles": 160, "lifetime": 0.4, "lifetime_variance": 0.03, "size": 0.078, "size_variance": 0.035,
        "size_over_life": twinkle, "color": rgba(mix(violet, hot, 0.5)),
        "color_over_life": [[0.0, rgba(hot)], [0.4, rgba(mix(violet, hot, 0.25))], [1.0, rgba(deep)]],
        "opacity_over_life": [[0.0, 1.0], [0.7, 0.9], [1.0, 0.0]], "emissive": 3.4, "drag": 5.2,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.05,
    }, layer="aftermath", inputs={"sprite": "tex_mote", "material": "mat_spark", "forces": ["f_drift"]}))
    # the ring emitter lies in XZ: stand it up and turn it to face the camera
    to_camera = heading_of(sub(tuple(CAMERA["position"]), TARGET))      # type: ignore[arg-type]
    burst("e_ring_motes", "ps_ring_motes", 72, {
        "shape": "ring", "radius": 0.5, "inner_radius": 0.36, "direction": [0.0, 0.0, 0.0], "velocity": 0.0,
        "velocity_variance": 0.3, "radial_velocity": 3.8,
        "rotation": [round(90.0 - to_camera[1], 2),
                     round(to_camera[0] - fl.pivot_azimuth - fl.swing_side[F_IMPACT] + 90.0, 2), 0.0],
    }, layer="aftermath", times=[0.0, 0.05])
    add(node("ps_afterglow", "particle_system", {
        "max_particles": 2, "lifetime": 0.4, "size": 2.0, "size_over_life": [[0.0, 0.6], [1.0, 1.15]],
        "color": rgba(mix(deep, violet, 0.6)), "opacity": 0.5,
        "opacity_over_life": [[0.0, 0.0], [0.2, 1.0], [0.55, 0.5], [1.0, 0.0]], "emissive": 1.0,
        "render_mode": "billboard", "blend": "additive", "soft_particle_distance": 0.3,
    }, layer="aftermath", inputs={"sprite": "tex_mist", "material": "mat_glow"}))
    burst("e_afterglow", "ps_afterglow", 1, {"shape": "point", "velocity": 0.0}, layer="aftermath")

    # ---------------- lights ----------------
    # a tight pool on the ground straight under the head (a spot, so it stays small at any height)
    add(node("l_missile", "light", {
        "light_type": "spot", "position": [0.0, -0.35, 0.0], "direction": [0.0, -1.0, 0.0], "cone_angle": 30.0,
        "color": rgba(mix(violet, AZURE, 0.2)), "radius": 6.0, "flicker_amplitude": 0.1, "flicker_frequency": 16.0,
        "intensity": track([(0.0, 0.0), (0.16, 1.2), (0.24, 3.4), (0.4, 2.6), (1.3, 2.8), (T_IMPACT - 0.01, 3.6),
                            (T_IMPACT + 0.02, 0.0)]),
        "duration": round(T_IMPACT + 0.03, 4),
    }, layer="light", parent="missile"))
    # the facets are shaped by two lights that travel with the missile: a pale key above on the camera
    # side and an azure rim from below on the far side (a spot aimed upwards, so it never reaches the ground)
    add(node("l_key", "light", {
        "light_type": "point", "position": [0.5, 1.9, 1.5], "color": rgba(mix(ICE, hot, 0.6)), "radius": 4.5,
        "intensity": track([(T_LAUNCH, 0.0), (0.3, 5.5), (1.38, 5.5), (T_IMPACT, 0.0)]),
        "start_time": round(T_LAUNCH, 4), "duration": round(T_IMPACT - T_LAUNCH, 4),
    }, layer="light", parent="missile"))
    add(node("l_rim", "light", {
        "light_type": "spot", "position": [-0.6, -1.3, -1.0], "direction": [0.343, 0.743, 0.572],
        "cone_angle": 32.0, "color": rgba(AZURE), "radius": 4.0,
        "intensity": track([(T_LAUNCH, 0.0), (0.3, 3.2), (1.38, 3.2), (T_IMPACT, 0.0)]),
        "start_time": round(T_LAUNCH, 4), "duration": round(T_IMPACT - T_LAUNCH, 4),
    }, layer="light", parent="missile"))
    # the impact flash sits between the burst and the camera so the shards show lit facets
    rig_at_impact = fl.rig_rotation(F_IMPACT, 1.0, 1.0)
    towards_camera = unit(sub(tuple(CAMERA["position"]), TARGET))      # type: ignore[arg-type]
    flash_offset = mat_vec(mat_t(rig_at_impact), add3(scale3(towards_camera, 1.3), (0.0, 0.5, 0.0)))
    add(node("l_impact", "light", {
        "light_type": "point", "position": vec(flash_offset, 3), "color": rgba(mix(violet, hot, 0.45)), "radius": 6.0,
        "intensity": track([(hit, 0.0), (T_IMPACT + 0.03, 8.0), (1.5, 4.0), (1.64, 1.6), (1.78, 0.0)]),
        "start_time": hit,
    }, layer="light", parent="missile"))

    # ---------------- camera ----------------
    add(node("cam", "camera", dict(CAMERA)))

    controls = build_controls(nodes)

    return {
        "schema_version": "0.1.0",
        "name": NAME,
        "description": "A violet crystal missile charges, kicks off on a gentle arc, then hooks hard after a target "
                       "that moves mid-flight, trailing a braid of soft ribbons and orbiting shards into a crystal burst.",
        "duration": DURATION,
        "seed": SEED,
        "timeline": {"phases": [{"name": name, "start": s, "end": e} for name, s, e in PHASES]},
        "layers": LAYERS,
        "nodes": nodes,
        "controls": controls,
        "metadata": {
            "generator": "tools/generators/homing_arcane_missile.py",
            "tags": ["arcane", "projectile", "missile", "homing", "crystal", "impact", "magic"],
            "analysis": "spawn_charge 0-0.2 violet orb, contracting halo ring, motes rushing in | launch 0.2-0.4 "
                        "the crystal forms inside the flash and kicks forward, ribbons start | mid_flight 0.4-1.0 "
                        "gentle arc away from the target, braid of three ribbons and two filaments, three orbiting "
                        "shards with thin trails, sparkle motes | target_adjust 1.0-1.4 the target has moved: a "
                        "tightening, accelerating hook down and towards the camera | impact 1.4-1.6 core flash, "
                        "irregular soft rays, tumbling crystal shards, sparks, a faint expanding ring | dissipate "
                        "1.6-1.8 a loose ring of fading motes",
            "layout": {
                "launch": vec(fl.pos[0], 3), "pivot": vec(fl.pivot, 3), "target": vec(fl.pos[F_IMPACT], 3),
                "target_side": "1 = the authored target; 0 = the target never moved; 2 = it moved twice as far",
            },
            "render_settings": {
                "background": [0.0, 0.0, 0.0, 1.0],
                "ground_albedo": 0.03,
                "bloom_intensity": 0.26,
                "bloom_radius": 0.045,
                "exposure": 0.95,
                "grid": False,
            },
        },
    }


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

COLOUR_PARAMS = {
    "particle_system": ["color", "color_over_life"],
    "trail": ["color"],
    "light": ["color"],
    "material": ["base_color", "emissive_color"],
}


def build_controls(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def bind(nid: str, parameter: str, operation: str = "multiply") -> dict[str, str]:
        return {"node": nid, "parameter": parameter, "op": operation}

    def multiplier(cid: str, label: str, group: str, bindings: list[dict[str, str]],
                   lo: float = 0.0, hi: float = 3.0) -> dict[str, Any]:
        return {"id": cid, "label": label, "group": group, "min": lo, "max": hi, "default": 1.0, "value": 1.0,
                "step": 0.01, "unit": "x", "bindings": bindings}

    ids = [n["id"] for n in nodes]
    ribbons = [i for i in ids if i.startswith(("ribbon_", "filament_")) and not i.endswith("_src")]
    shard_hubs = [i for i in ids if i.startswith("shard_") and i.count("_") == 1]
    shard_trails = [i for i in ids if i.startswith("shard_") and i.endswith("_trail")]

    hue = []
    for n in nodes:
        for parameter in COLOUR_PARAMS.get(n["type"], []):
            if parameter in n["parameters"]:
                hue.append(bind(n["id"], parameter, "hue_shift"))

    return [
        multiplier("missile_size", "Missile size", "Crystal head",
                   [bind("body", "scale"), bind("ps_halo", "size"), bind("ps_aura", "size")]
                   + [bind(r, "width") for r in ribbons], 0.4, 1.6),
        multiplier("trail_intensity", "Braid glow", "Energy braid",
                   [bind(r, "color") for r in ribbons] + [bind("ps_haze", "color"), bind("ps_motes", "color"),
                                                          bind("ps_twinkles", "color")]),
        multiplier("trail_length", "Braid length", "Energy braid",
                   [bind(r, "lifetime") for r in ribbons] + [bind("ps_haze", "lifetime"), bind("ps_motes", "lifetime")],
                   0.3, 2.5),
        multiplier("fragments", "Orbiting fragments", "Orbiting fragments",
                   [bind(s, "scale") for s in shard_hubs] + [bind(t, "width") for t in shard_trails], 0.0, 2.5),
        multiplier("impact_burst", "Impact burst", "Impact",
                   [bind(e, "burst_count") for e in ("e_burst_shards", "e_burst_sparks", "e_streaks", "e_ring_motes")]
                   + [bind("ps_flash", "size"), bind("ps_rays", "size"), bind("ps_shock", "size"),
                      bind("l_impact", "intensity")]),
        {"id": "target_side", "label": "Target shift", "group": "Global", "min": 0.0, "max": 2.0, "default": 1.0,
         "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": [bind("aim_side", "rotation"), bind("head_yaw_k1", "rotation"),
                      bind("head_yaw_k2", "rotation"), bind("head_yaw_k2", "rotation"),
                      bind("head_pitch_k2", "rotation")]},
        {"id": "target_height", "label": "Target drop", "group": "Global", "min": -0.5, "max": 2.0, "default": 1.0,
         "value": 1.0, "step": 0.01, "unit": "x",
         "bindings": [bind("aim_height", "rotation"), bind("head_pitch_k1", "rotation"),
                      bind("head_pitch_k2", "rotation")]},
        {"id": "hue", "label": "Hue", "group": "Global", "min": -180.0, "max": 180.0, "default": 0.0, "value": 0.0,
         "step": 1.0, "unit": "deg", "bindings": hue},
        {"id": "global_speed", "label": "Speed", "group": "Global", "min": 0.25, "max": 4.0, "default": 1.0,
         "value": 1.0, "step": 0.05, "unit": "x",
         "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}]},
    ]


def render(effect: dict[str, Any]) -> str:
    return json.dumps(effect, indent=1) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "examples" / "effects"),
                    help="directory to write homing_arcane_missile.json into (default: examples/effects)")
    ap.add_argument("--check", action="store_true",
                    help="do not write: exit 1 when the file in --out differs from what would be generated")
    args = ap.parse_args()
    path = Path(args.out) / f"{SLUG}.json"
    text = render(build())
    if args.check:
        same = path.is_file() and path.read_text(encoding="utf-8") == text
        print(f"{path}: {'up to date' if same else 'DIFFERS from the generator output'}")
        raise SystemExit(0 if same else 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"{path}  ({len(json.loads(text)['nodes'])} nodes, {len(text)} bytes)")


if __name__ == "__main__":
    main()
