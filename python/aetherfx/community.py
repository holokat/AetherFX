"""Community contributions: credit metadata, the contribution check and the index.

Contributed effects live in the repository under ``community/effects/*.json`` and
arrive by pull request (docs/COMMUNITY.md, CONTRIBUTING.md).  There is no AetherFX
server: CI runs the check in this module, rebuilds ``community/index.json`` and
publishes the previews, and the studio reads the local directory (plus,
optionally, one remote index).

Three things live here:

* **Credit metadata** - :func:`parse_author`, :func:`parse_tags`,
  :func:`parse_license`.  Credit is deliberately minimal: a GitHub username, and
  an optional display name.  No accounts, no e-mail, no arbitrary links.
  Contributed strings are never trusted - the username is validated against
  GitHub's own pattern before it is interpolated into a URL, and
  :meth:`Author.to_html` escapes everything it emits.
* **The contribution check** - :func:`check_effect`: engine validation,
  determinism, budgets, procedural-only assets (no image files, no links, no
  embedded binaries), MIT licensing, required credit and the house-style lint
  (the rules in ``studio/authoring_guide.py``: no four-armed stars, no
  constant-width tube beams, no hard-edged ribbons, no projectile without an
  impact).
* **The index** - :func:`build_index`, used by ``tools/build_community_index.py``.

Command line::

    python -m aetherfx.community check community/effects/arcane_missile.json --previews out/previews
    python -m aetherfx.community lint-library
    python -m aetherfx.community index community/effects --out community/index.json

Contributed effects are published under the project's licence, **MIT**, so
``metadata.license`` must say ``"MIT"``.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import urlsplit

__all__ = [
    "Author",
    "AuthorError",
    "Budgets",
    "CheckReport",
    "Finding",
    "GITHUB_REPO_URL",
    "INDEX_SCHEMA",
    "LICENSE",
    "REMOTE_INDEX_URL",
    "build_index",
    "check_effect",
    "effect_description",
    "house_style_findings",
    "lint_library",
    "main",
    "parse_author",
    "parse_license",
    "parse_tags",
    "slugify",
]

JsonDict = dict[str, Any]

#: Version tag written into ``community/index.json``.
INDEX_SCHEMA = "aetherfx.community.index/1"

#: The only licence a contributed effect may carry: the project's own.
LICENSE = "MIT"

#: The public repository contributions are opened against.
GITHUB_REPO_URL = "https://github.com/holokat/AetherFX"

#: Where the studio looks for the published index when nothing overrides it.
REMOTE_INDEX_URL = "https://raw.githubusercontent.com/holokat/AetherFX/main/community/index.json"

#: Library files allowed to fail the house-style lint while they are being
#: revised.  Keep this tiny and delete entries as they are fixed.
LINT_ALLOWLIST: frozenset[str] = frozenset()


# =========================================================================
# credit metadata
# =========================================================================

MAX_AUTHOR_NAME = 60
MAX_TAGS = 8
MAX_TAG = 24
MAX_DESCRIPTION = 400

GITHUB_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")
TAG_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
#: Anything that smells like a link in a contributed string.  The bare-scheme list is
#: explicit so ordinary prose ("Note: ...") is not mistaken for one.
URLISH_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|\bwww\.[a-z0-9-]|\b(?:javascript|data|vbscript|file|mailto|blob):)"
)
#: Reference art, screenshots and video never enter the repository (see
#: python/tests/test_repo_hygiene.py); a document must not even name one.
MEDIA_RE = re.compile(
    r"(?i)[\w./\\-]+\.(?:png|jpe?g|webp|gif|bmp|tga|tiff?|exr|psd|mp4|mov|webm|avi|svg)\b"
)
#: A long run of base64 is an embedded image, not a parameter.
BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\s]{256,}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class AuthorError(ValueError):
    """A contributed credit block that must not be shown to anyone."""


@dataclass(frozen=True)
class Author:
    """Validated credit for one contributed effect: a GitHub username.

    Deliberately minimal - no accounts, no e-mail, no arbitrary links.  The
    username has already passed :func:`parse_author`, so it is safe to
    interpolate into ``https://github.com/<username>``; :meth:`to_html` still
    escapes, because the optional display name has a human-sized character set.
    """

    github: str
    name: str | None = None

    @property
    def url(self) -> str:
        return f"https://github.com/{self.github}"

    @property
    def display(self) -> str:
        """What a one-line credit says."""
        return self.name or self.github

    def links(self) -> list[JsonDict]:
        """``[{"label", "url"}]`` - github.com and nothing else."""
        return [{"label": f"github.com/{self.github}", "url": self.url}]

    def to_json(self) -> JsonDict:
        """The wire form the studio and the index use.  Deterministic key order."""
        out: JsonDict = {"github": self.github, "url": self.url}
        if self.name:
            out["name"] = self.name
        out["display"] = self.display
        return out

    def to_html(self) -> str:
        """An escaped ``by <a ...>`` credit fragment, safe to inject anywhere."""
        return (
            f'by <a href="{html.escape(self.url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(self.display)}</a>'
        )


def _clean(value: Any, field_name: str, limit: int) -> str:
    """A contributed scalar string: no control characters, no markup, length capped."""
    if not isinstance(value, str):
        raise AuthorError(f"metadata.author.{field_name} must be a string")
    text = value.strip()
    if not text:
        raise AuthorError(f"metadata.author.{field_name} must not be empty")
    if CONTROL_RE.search(text):
        raise AuthorError(f"metadata.author.{field_name} contains control characters")
    if len(text) > limit:
        raise AuthorError(f"metadata.author.{field_name} is longer than {limit} characters")
    if any(ch in text for ch in "<>"):
        raise AuthorError(f"metadata.author.{field_name} must not contain markup (< or >)")
    return text


def parse_author(metadata: Any) -> Author | None:
    """``metadata`` -> :class:`Author`, or ``None`` when there is no credit block.

    ``metadata`` may be the whole effect document or just its ``metadata``
    object; both are accepted so callers do not have to dig.  Raises
    :class:`AuthorError` for anything malformed - a contribution with a broken
    credit block is rejected, never rendered.
    """
    if isinstance(metadata, dict) and "metadata" in metadata and "author" not in metadata:
        metadata = metadata.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("author")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AuthorError('metadata.author must be an object, e.g. {"github": "octocat"}')

    known = {"github", "name"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise AuthorError(
            f"metadata.author has unknown field(s): {', '.join(unknown)}. Credit is the GitHub "
            'username only: {"github": "octocat", "name": "Octo Cat"}'
        )

    username = _clean(raw.get("github"), "github", 39)
    if not GITHUB_RE.match(username):
        raise AuthorError(
            "metadata.author.github must be a GitHub username: 1-39 letters, digits or hyphens"
        )

    name: str | None = None
    if raw.get("name") not in (None, ""):
        name = _clean(raw.get("name"), "name", MAX_AUTHOR_NAME)
        if URLISH_RE.search(name) or MEDIA_RE.search(name):
            raise AuthorError("metadata.author.name must not contain a link or a file name")
    return Author(github=username, name=name)


def parse_tags(metadata: Any) -> list[str]:
    """``metadata.tags`` -> lowercase slugs.  Raises :class:`AuthorError` when malformed."""
    if isinstance(metadata, dict) and "metadata" in metadata and "tags" not in metadata:
        metadata = metadata.get("metadata")
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("tags")
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise AuthorError("metadata.tags must be a list of lowercase slugs")
    if len(raw) > MAX_TAGS:
        raise AuthorError(f"metadata.tags holds {len(raw)} tags; at most {MAX_TAGS} are allowed")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise AuthorError("metadata.tags must hold strings")
        tag = item.strip()
        if len(tag) > MAX_TAG or not TAG_RE.match(tag):
            raise AuthorError(
                f'metadata.tags entry "{item[:32]}" is not a lowercase slug '
                f"(a-z, 0-9 and - or _, at most {MAX_TAG} characters)"
            )
        if tag not in out:
            out.append(tag)
    return out


def parse_license(metadata: Any) -> str | None:
    """``metadata.license`` -> the SPDX id.  Only :data:`LICENSE` is accepted."""
    if isinstance(metadata, dict) and "metadata" in metadata and "license" not in metadata:
        metadata = metadata.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("license")
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or raw.strip().upper() != LICENSE:
        shown = str(raw)[:32]
        raise AuthorError(
            f'metadata.license must be "{LICENSE}" (got "{shown}"): contributed effects are '
            "published under the project's licence so anyone can ship them in a game"
        )
    return LICENSE


def effect_description(document: JsonDict) -> str:
    """``metadata.description`` or the top-level ``description``, whichever is set."""
    metadata = document.get("metadata")
    if isinstance(metadata, dict):
        text = metadata.get("description")
        if isinstance(text, str) and text.strip():
            return text.strip()
    text = document.get("description")
    return text.strip() if isinstance(text, str) else ""


def slugify(name: str, fallback: str = "effect") -> str:
    """``"Arcane Missile"`` -> ``"arcane_missile"``; matches the studio's rule."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return slug or fallback


# =========================================================================
# findings
# =========================================================================


@dataclass(frozen=True)
class Finding:
    """One thing the check noticed.  ``level`` is ``"error"``, ``"warning"`` or ``"info"``."""

    level: str
    code: str
    message: str
    where: str | None = None

    @property
    def is_error(self) -> bool:
        return self.level == "error"

    def to_json(self) -> JsonDict:
        out: JsonDict = {"level": self.level, "code": self.code, "message": self.message}
        if self.where:
            out["where"] = self.where
        return out

    def __str__(self) -> str:
        where = f" [{self.where}]" if self.where else ""
        return f"{self.level:<7} {self.code}{where}: {self.message}"


def _error(code: str, message: str, where: str | None = None) -> Finding:
    return Finding("error", code, message, where)


def _warning(code: str, message: str, where: str | None = None) -> Finding:
    return Finding("warning", code, message, where)


# =========================================================================
# budgets
# =========================================================================


@dataclass(frozen=True)
class Budgets:
    """What a contributed effect may cost.

    Calibrated against the shipped library (the heaviest example today is Fire
    Bolt at 2.3 s of texture baking and 54 MB of baked pixels, Holy AOE at ~3000
    live sprites and Ice AOE at 239 live mesh particles), with headroom.
    """

    max_live_sprites: int = 6000
    max_live_mesh_particles: int = 300
    max_texture_bake_seconds: float = 6.0
    max_texture_bytes: int = 96 * 1024 * 1024
    max_duration: float = 12.0
    max_nodes: int = 160
    min_controls: int = 3


#: Fractions of the duration the determinism check hashes.
DETERMINISM_FRACTIONS: tuple[float, ...] = (0.25, 0.5, 0.75, 0.99)


# =========================================================================
# document walking helpers
# =========================================================================


def _nodes(document: JsonDict) -> list[JsonDict]:
    nodes = document.get("nodes")
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def _params(node: JsonDict) -> JsonDict:
    params = node.get("parameters")
    return params if isinstance(params, dict) else {}


def _inputs(node: JsonDict) -> JsonDict:
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _track(value: Any) -> list[JsonDict] | None:
    """The keyframe track of an animated parameter, or ``None`` for a static one."""
    if isinstance(value, dict) and isinstance(value.get("track"), list):
        return [kf for kf in value["track"] if isinstance(kf, dict)]
    return None


def _static(value: Any, default: float) -> float:
    """The static value of a parameter (the base value of an animated one)."""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _peak(value: Any, default: float) -> float:
    """The largest value a scalar parameter reaches, keyframes included."""
    track = _track(value)
    if track:
        numbers = [
            float(kf["value"])
            for kf in track
            if isinstance(kf.get("value"), (int, float)) and not isinstance(kf.get("value"), bool)
        ]
        if numbers:
            return max(numbers)
    return _static(value, default)


def _curve_peak(value: Any, default: float = 1.0) -> float:
    """The largest value of a ``[[t, v], ...]`` curve, or ``default``."""
    if not isinstance(value, list) or not value:
        return default
    peaks = [
        float(point[1])
        for point in value
        if isinstance(point, (list, tuple)) and len(point) >= 2
        and isinstance(point[1], (int, float)) and not isinstance(point[1], bool)
    ]
    return max(peaks) if peaks else default


def _phases(document: JsonDict) -> list[JsonDict]:
    timeline = document.get("timeline")
    phases = timeline.get("phases") if isinstance(timeline, dict) else None
    return [p for p in phases if isinstance(p, dict)] if isinstance(phases, list) else []


def _window(node: JsonDict, document: JsonDict, duration: float) -> tuple[float, float]:
    """``(start, length)`` of a time-bound node: its phase, or start_time/duration."""
    params = _params(node)
    phase = params.get("phase")
    if isinstance(phase, str) and phase:
        for candidate in _phases(document):
            if candidate.get("name") == phase:
                start = _static(candidate.get("start"), 0.0)
                end = _static(candidate.get("end"), duration)
                return start, max(0.0, end - start)
    start = max(0.0, _static(params.get("start_time"), 0.0))
    length = _static(params.get("duration"), -1.0)
    if length < 0:
        length = max(0.0, duration - start)
    return start, length


def _walk_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string in a JSON tree with a dotted path to it."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


# =========================================================================
# credit / metadata rules
# =========================================================================


def metadata_findings(document: JsonDict, *, budgets: Budgets = Budgets()) -> list[Finding]:
    """The credit, naming and control requirements every contribution has to meet."""
    out: list[Finding] = []
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        out.append(_error("meta.name", "the effect needs a non-empty top-level name"))
    elif len(name) > 60:
        out.append(_error("meta.name", "the effect name is longer than 60 characters"))

    description = effect_description(document)
    if not description:
        out.append(
            _error(
                "meta.description",
                "add a one-line description (top-level `description` or `metadata.description`) "
                "so the Community view can say what the effect is",
            )
        )
    elif len(description) > MAX_DESCRIPTION:
        out.append(_error("meta.description", f"the description is longer than {MAX_DESCRIPTION} characters"))

    try:
        author = parse_author(document)
    except AuthorError as exc:
        out.append(_error("meta.author", str(exc)))
    else:
        if author is None:
            out.append(
                _error(
                    "meta.author",
                    'community effects need credit: metadata.author = {"github": "<your username>"}',
                )
            )

    try:
        parse_tags(document)
    except AuthorError as exc:
        out.append(_error("meta.tags", str(exc)))

    try:
        licence = parse_license(document)
    except AuthorError as exc:
        out.append(_error("meta.license", str(exc)))
    else:
        if licence is None:
            out.append(
                _error(
                    "meta.license",
                    f'metadata.license is required and must be "{LICENSE}" - contributed effects are '
                    "published under the project's licence",
                )
            )

    controls = document.get("controls")
    count = len(controls) if isinstance(controls, list) else 0
    if count < budgets.min_controls:
        out.append(
            _error(
                "meta.controls",
                f"ship at least {budgets.min_controls} named style controls so the effect can be tuned "
                f"without editing the graph (found {count})",
            )
        )
    return out


def asset_findings(document: JsonDict) -> list[Finding]:
    """Everything must be procedural: no image files, no links, no embedded binaries.

    Reference art, screenshots and preview media never enter the repository
    (python/tests/test_repo_hygiene.py), so a contributed document may not even
    name one.
    """
    out: list[Finding] = []
    for node in _nodes(document):
        params = _params(node)
        node_id = str(node.get("id") or node.get("type") or "?")
        if node.get("type") == "texture" and str(params.get("source") or "procedural") == "file":
            out.append(
                _error(
                    "asset.file_texture",
                    "texture nodes must be procedural: build the pixels with a texture graph "
                    "instead of `source: file`",
                    node_id,
                )
            )
        if node.get("type") == "mesh" and str(params.get("source") or "") == "file":
            out.append(
                _error("asset.file_mesh", "mesh nodes must use a procedural primitive, not a file", node_id)
            )
        path = params.get("path")
        if isinstance(path, str) and path.strip():
            out.append(
                _error(
                    "asset.path",
                    f'"{path[:60]}" references a file; contributed effects carry no assets',
                    node_id,
                )
            )

    for where, text in _walk_strings(document):
        if URLISH_RE.search(text):
            out.append(
                _error("asset.url", f'"{text[:60]}" contains a link; effect documents carry no URLs', where)
            )
        elif MEDIA_RE.search(text):
            out.append(
                _error(
                    "asset.media",
                    f'"{text[:60]}" names an image or video file; reference art and preview media never '
                    "enter the repository and effects must be fully procedural",
                    where,
                )
            )
        elif len(text) >= 256 and BASE64_RE.match(text):
            out.append(
                _error("asset.embedded", "an embedded binary blob is not allowed in an effect document", where)
            )
    return out


# =========================================================================
# house style lint
# =========================================================================


def _sprite_sizes(document: JsonDict) -> dict[str, float]:
    """texture id -> the largest on-screen diameter a particle draws it at (metres)."""
    material_textures: dict[str, list[str]] = {}
    for node in _nodes(document):
        if node.get("type") != "material":
            continue
        inputs = _inputs(node)
        material_textures[str(node.get("id"))] = [
            str(inputs[port])
            for port in ("base_texture", "gradient_texture", "noise_texture")
            if isinstance(inputs.get(port), str)
        ]

    sizes: dict[str, float] = {}
    for node in _nodes(document):
        if node.get("type") != "particle_system":
            continue
        params = _params(node)
        size = _static(params.get("size"), 0.1) + max(0.0, _static(params.get("size_variance"), 0.0))
        size *= _curve_peak(params.get("size_over_life"), 1.0)
        inputs = _inputs(node)
        ids: list[str] = []
        if isinstance(inputs.get("sprite"), str):
            ids.append(str(inputs["sprite"]))
        if isinstance(inputs.get("material"), str):
            ids.extend(material_textures.get(str(inputs["material"]), []))
        for texture_id in ids:
            sizes[texture_id] = max(sizes.get(texture_id, 0.0), size)
    return sizes


def _graph_ops(node: JsonDict) -> list[JsonDict]:
    graph = _params(node).get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def _texture_findings(document: JsonDict) -> list[Finding]:
    """No four-armed stars anywhere (HOUSE STYLE in studio/authoring_guide.py)."""
    out: list[Finding] = []
    sizes = _sprite_sizes(document)
    for node in _nodes(document):
        if node.get("type") != "texture":
            continue
        texture_id = str(node.get("id") or "?")
        drawn_at = sizes.get(texture_id, 0.0)
        for op_node in _graph_ops(node):
            op = op_node.get("op")
            if op not in ("star", "spokes"):
                continue
            params = op_node.get("params") if isinstance(op_node.get("params"), dict) else {}
            key = "points" if op == "star" else "count"
            raw = params.get(key)
            where = f"{texture_id}.{op_node.get('id') or op}"
            if op == "star" and not isinstance(raw, (int, float)):
                out.append(
                    _error(
                        "style.star_default",
                        "`star` defaults to 4 points, which is a cross: set `points` to 5, 6 or 8 "
                        "(house style: no cross or four-armed star motifs)",
                        where,
                    )
                )
                continue
            arms = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 8
            if arms == 4:
                out.append(
                    _error(
                        "style.four_arms",
                        f"`{op}` with 4 arms is a cross; use 5, 6 or 8 (house style: no cross motifs)",
                        where,
                    )
                )
            elif arms == 2 and drawn_at > 0.5:
                out.append(
                    _warning(
                        "style.two_arms",
                        f"`{op}` with 2 arms reads as a bar and it is drawn at {drawn_at:.2f} m, "
                        "big enough to notice - use an asymmetric glint or a soft radial glow",
                        where,
                    )
                )
    return out


def _beam_findings(document: JsonDict, duration: float) -> list[Finding]:
    """A constant-width beam that stays on screen reads as a neon tube."""
    out: list[Finding] = []
    for node in _nodes(document):
        if node.get("type") != "beam":
            continue
        params = _params(node)
        detail = int(_static(params.get("detail"), 0.0))
        profile = str(params.get("width_profile") or "uniform")
        _, life = _window(node, document, duration)
        if detail < 2 and profile == "uniform" and life > 0.3:
            out.append(
                _warning(
                    "style.tube_beam",
                    f"reads as a tube: detail {detail} with a uniform width profile for {life:.2f} s "
                    "- give it detail >= 2 and a taper_end / taper_both / bulge profile",
                    str(node.get("id") or "?"),
                )
            )
    return out


def _trail_findings(document: JsonDict) -> list[Finding]:
    """A wide ribbon with no cross-width texture is a flat tape."""
    out: list[Finding] = []
    textured: set[str] = {
        str(node.get("id"))
        for node in _nodes(document)
        if node.get("type") == "material" and isinstance(_inputs(node).get("base_texture"), str)
    }
    for node in _nodes(document):
        if node.get("type") != "trail":
            continue
        width = _peak(_params(node).get("width"), 0.1)
        material = _inputs(node).get("material")
        if width > 0.12 and (not isinstance(material, str) or material not in textured):
            out.append(
                _warning(
                    "style.hard_ribbon",
                    f"hard-edged ribbon: {width:.2f} m wide with no material texture - give its material "
                    "a soft cross-width texture (bright thin centre fading to zero at both edges)",
                    str(node.get("id") or "?"),
                )
            )
    return out


def _travel(track: Sequence[JsonDict]) -> tuple[float, float]:
    """``(net, horizontal)`` displacement in metres from the first key to the last.

    Net displacement, not path length: a healing mote that spirals two metres
    upwards covers a lot of ground without going anywhere, and it is not a
    projectile.  The horizontal component separates "flies across the scene"
    from "rises off the ground".
    """
    points: list[tuple[float, float, float]] = []
    for keyframe in track:
        value = keyframe.get("value")
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            try:
                points.append((float(value[0]), float(value[1]), float(value[2])))
            except (TypeError, ValueError):
                continue
    if len(points) < 2:
        return 0.0, 0.0
    first, last = points[0], points[-1]
    return math.dist(first, last), math.hypot(last[0] - first[0], last[2] - first[2])


def _projectile_findings(document: JsonDict, duration: float) -> list[Finding]:
    """A projectile must land: an impact phase, or a burst near the end."""
    flying = [
        (str(node.get("id") or "?"), net)
        for node, (net, horizontal) in (
            (n, _travel(_track(_params(n).get("position")) or []))
            for n in _nodes(document)
            if n.get("type") in ("mesh", "emitter") and _track(_params(n).get("position"))
        )
        if net > 3.0 and horizontal > 2.0
    ]
    if not flying:
        return []
    if any("impact" in str(phase.get("name") or "").lower() for phase in _phases(document)):
        return []
    for node in _nodes(document):
        if node.get("type") != "emitter":
            continue
        params = _params(node)
        if _peak(params.get("burst_count"), 0.0) <= 0:
            continue
        start, _ = _window(node, document, duration)
        times = params.get("burst_times")
        times = times if isinstance(times, list) else [0.0]
        if any(
            isinstance(t, (int, float)) and not isinstance(t, bool) and start + float(t) >= 0.7 * duration
            for t in times
        ):
            return []
    node_id, distance = max(flying, key=lambda item: item[1])
    return [
        _warning(
            "style.no_impact",
            f'"{node_id}" travels {distance:.1f} m but nothing happens when it arrives: add an '
            "`impact` timeline phase with a flash, a radial burst and debris, or a burst emitter "
            "in the last third - never let a projectile just fade out",
            node_id,
        )
    ]


def house_style_findings(document: JsonDict) -> list[Finding]:
    """Every house-style rule (studio/authoring_guide.py HOUSE STYLE) as findings."""
    duration = max(0.001, _static(document.get("duration"), 2.0))
    return [
        *_texture_findings(document),
        *_beam_findings(document, duration),
        *_trail_findings(document),
        *_projectile_findings(document, duration),
    ]


# =========================================================================
# engine-backed checks
# =========================================================================


class EngineUnavailable(RuntimeError):
    """The native library could not be loaded, so nothing can be simulated."""


def _load_native() -> Any:
    from . import native  # noqa: PLC0415 - optional, and it dlopen()s on import

    if not native.is_available():
        raise EngineUnavailable(
            "libaetherfx was not found: build the engine (cmake --build --preset default) or set "
            "$AETHERFX_LIBRARY to build/src/capi/libaetherfx.<dylib|so|dll>"
        )
    return native


def _frame_digest(systems: Iterable[Any]) -> str:
    """A stable hash of one simulated frame: every particle array of every system."""
    digest = hashlib.blake2b(digest_size=16)
    for system in sorted(systems, key=lambda s: s.id):
        digest.update(system.id.encode("utf-8"))
        digest.update(str(system.count).encode("ascii"))
        for name in sorted(system.arrays):
            digest.update(name.encode("ascii"))
            digest.update(system.arrays[name].tobytes())
    return digest.hexdigest()


def _simulate(native: Any, path: Path, duration: float, fractions: Sequence[float]) -> JsonDict:
    """One full simulation pass: peak counts plus a frame hash at each fraction."""
    effect = native.Effect.from_file(path)
    start = time.perf_counter()
    compiled = effect.compile()
    bake_seconds = time.perf_counter() - start
    result: JsonDict = {
        "diagnostics": compiled.diagnostics,
        "bake_seconds": bake_seconds,
        "texture_bytes": sum(t.width * t.height * t.channels * 4 for t in compiled.textures),
        "texture_count": len(compiled.textures),
        "hashes": {},
        "peak_sprites": 0,
        "peak_mesh_particles": 0,
    }
    if not compiled.ok:
        return result

    wanted = sorted({round(max(0.0, min(1.0, f)) * duration, 6) for f in fractions})
    runtime = compiled.runtime()
    pending = list(wanted)
    guard = int(duration / max(1e-6, runtime.fixed_dt)) + 4
    hashes: dict[str, str] = {}
    for _ in range(guard):
        if runtime.time >= duration and not pending:
            break
        runtime.step()
        systems = runtime.particle_systems()
        result["peak_sprites"] = max(result["peak_sprites"], sum(s.count for s in systems if not s.is_mesh))
        result["peak_mesh_particles"] = max(
            result["peak_mesh_particles"], sum(s.count for s in systems if s.is_mesh)
        )
        while pending and runtime.time >= pending[0]:
            hashes[f"{pending.pop(0):.3f}"] = _frame_digest(systems)
    result["hashes"] = hashes
    return result


def engine_findings(path: Path, document: JsonDict, budgets: Budgets) -> tuple[list[Finding], JsonDict]:
    """Validate, simulate twice and measure.  Returns ``(findings, metrics)``."""
    native = _load_native()
    duration = max(0.001, _static(document.get("duration"), 2.0))
    findings: list[Finding] = []

    first = _simulate(native, path, duration, DETERMINISM_FRACTIONS)
    diagnostics = first["diagnostics"] if isinstance(first["diagnostics"], dict) else {}
    levels = {"E": "error", "W": "warning"}
    for item in diagnostics.get("items") or []:
        code = str(item.get("code") or "E000")
        where = item.get("node") or item.get("param")
        findings.append(
            Finding(
                levels.get(code[:1], "info"),        # I-codes are notes, not problems
                f"engine.{code}",
                str(item.get("message") or ""),
                str(where) if where else None,
            )
        )

    metrics: JsonDict = {
        "duration": duration,
        "node_count": len(_nodes(document)),
        "control_count": len(document["controls"]) if isinstance(document.get("controls"), list) else 0,
        "engine_errors": int(diagnostics.get("errors") or 0),
        "engine_warnings": int(diagnostics.get("warnings") or 0),
        "texture_bake_seconds": round(first["bake_seconds"], 3),
        "texture_bytes": first["texture_bytes"],
        "texture_count": first["texture_count"],
        "peak_live_sprites": first["peak_sprites"],
        "peak_live_mesh_particles": first["peak_mesh_particles"],
        "frame_hashes": first["hashes"],
        "deterministic": None,
        "schema_version": str(document.get("schema_version") or ""),
        "seed": document.get("seed"),
    }

    if metrics["engine_errors"]:
        return findings, metrics

    second = _simulate(native, path, duration, DETERMINISM_FRACTIONS)
    if not first["hashes"]:
        findings.append(_warning("engine.no_frames", "the effect produced no simulated frames to hash"))
    elif first["hashes"] != second["hashes"]:
        differing = sorted(t for t, value in first["hashes"].items() if second["hashes"].get(t) != value)
        metrics["deterministic"] = False
        findings.append(
            _error(
                "engine.nondeterministic",
                "two runs of the same document produced different frames at t = "
                + ", ".join(f"{t} s" for t in differing)
                + "; an effect must replay identically (look for wall-clock or unseeded randomness)",
            )
        )
    else:
        metrics["deterministic"] = True

    # -- budgets -----------------------------------------------------------
    if duration > budgets.max_duration:
        findings.append(
            _error("budget.duration", f"{duration:.2f} s is longer than the {budgets.max_duration:g} s limit")
        )
    if metrics["node_count"] > budgets.max_nodes:
        findings.append(
            _error("budget.nodes", f"{metrics['node_count']} nodes is over the {budgets.max_nodes} node limit")
        )
    if metrics["peak_live_sprites"] > budgets.max_live_sprites:
        findings.append(
            _error(
                "budget.sprites",
                f"{metrics['peak_live_sprites']} live sprites at peak is over the "
                f"{budgets.max_live_sprites} limit; lower the emitter rates or the lifetimes",
            )
        )
    if metrics["peak_live_mesh_particles"] > budgets.max_live_mesh_particles:
        findings.append(
            _error(
                "budget.mesh_particles",
                f"{metrics['peak_live_mesh_particles']} live mesh particles at peak is over the "
                f"{budgets.max_live_mesh_particles} limit",
            )
        )
    if first["bake_seconds"] > budgets.max_texture_bake_seconds:
        findings.append(
            _error(
                "budget.bake_time",
                f"baking the textures took {first['bake_seconds']:.1f} s, over the "
                f"{budgets.max_texture_bake_seconds:g} s limit; use smaller textures or fewer frames",
            )
        )
    if first["texture_bytes"] > budgets.max_texture_bytes:
        findings.append(
            _error(
                "budget.texture_bytes",
                f"{first['texture_bytes'] / 1048576:.1f} MiB of baked texture is over the "
                f"{budgets.max_texture_bytes / 1048576:.0f} MiB limit",
            )
        )
    return findings, metrics


# =========================================================================
# previews
# =========================================================================

#: Frames on the contact sheet the check renders with --previews.
PREVIEW_FRAMES = 6


def _render_settings(document: JsonDict) -> JsonDict:
    """``metadata.render_settings`` - the engine does not read it, so we pass it on."""
    metadata = document.get("metadata")
    settings = metadata.get("render_settings") if isinstance(metadata, dict) else None
    return dict(settings) if isinstance(settings, dict) else {}


def preview_names(slug: str) -> JsonDict:
    """The file names the check writes and the index records."""
    return {"sheet": f"{slug}_sheet.png", "animation": f"{slug}.gif"}


def render_previews(
    path: Path, document: JsonDict, out_dir: Path, *, binary: str | None = None, fps: float = 12.0
) -> tuple[JsonDict, list[Finding]]:
    """Contact sheet + GIF with the CPU reference renderer.  Never raises.

    The files land in ``out_dir``, which is an artifact directory - previews are
    published by CI and never committed to the repository.
    """
    from .client import Client  # noqa: PLC0415 - spawns the engine, keep the import local
    from .jsonrpc import AetherError, TransportError  # noqa: PLC0415

    slug = slugify(document.get("name") or path.stem, path.stem)
    names = preview_names(slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = max(0.001, _static(document.get("duration"), 2.0))
    settings = _render_settings(document)
    produced: JsonDict = {}
    findings: list[Finding] = []
    try:
        with Client(binary=binary, output_dir=str(out_dir)) as engine:
            engine.load_effect(str(path))
            engine.simulate()
            result = engine.render_preview(
                fps=fps,
                start=0.0,
                end=duration,
                width=320,
                height=320,
                settings=settings or None,
                contact_sheet=False,
                video=False,
                out_dir=str(out_dir / f"{slug}_frames"),
            )
        frames = [Path(p) for p in (result.get("frames") or []) if isinstance(p, str)]
        frames = [p for p in frames if p.is_file()]
        if not frames:
            return {}, [_warning("preview.empty", "the renderer produced no frames")]
        produced.update(_write_sheet(frames, out_dir / names["sheet"], names["sheet"]))
        produced.update(_write_gif(frames, out_dir / names["animation"], names["animation"], fps))
    except (AetherError, TransportError, OSError, ValueError) as exc:
        findings.append(_warning("preview.failed", f"could not render previews: {exc}"))
    return produced, findings


def _write_sheet(frames: list[Path], target: Path, name: str) -> JsonDict:
    from PIL import Image  # noqa: PLC0415

    count = min(PREVIEW_FRAMES, len(frames))
    picks = [frames[round(i * (len(frames) - 1) / max(1, count - 1))] for i in range(count)]
    tiles = [Image.open(p).convert("RGB") for p in picks]
    width, height = tiles[0].size
    columns = 3 if len(tiles) > 2 else len(tiles)
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * height), (0, 0, 0))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % columns) * width, (index // columns) * height))
    sheet.save(target)
    return {"sheet": name}


def _write_gif(frames: list[Path], target: Path, name: str, fps: float) -> JsonDict:
    from PIL import Image  # noqa: PLC0415

    images = [Image.open(p).convert("RGB").resize((256, 256)) for p in frames]
    images[0].save(
        target,
        save_all=True,
        append_images=images[1:],
        duration=int(1000 / max(1.0, fps)),
        loop=0,
        optimize=True,
    )
    return {"animation": name}


# =========================================================================
# the check
# =========================================================================


@dataclass
class CheckReport:
    """What :func:`check_effect` found.  ``ok`` is false when any error was raised."""

    path: Path
    slug: str = ""
    name: str = ""
    findings: list[Finding] = field(default_factory=list)
    metrics: JsonDict = field(default_factory=dict)
    author: Author | None = None
    tags: list[str] = field(default_factory=list)
    license: str | None = None
    previews: JsonDict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.is_error]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> JsonDict:
        return {
            "ok": self.ok,
            "path": str(self.path),
            "slug": self.slug,
            "name": self.name,
            "author": self.author.to_json() if self.author else None,
            "tags": list(self.tags),
            "license": self.license,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [f.to_json() for f in self.findings],
            "metrics": self.metrics,
            "previews": self.previews,
            "skipped": list(self.skipped),
        }

    def report(self, *, verbose: bool = True) -> str:
        """The human report the CLI and the PR comment print."""
        lines = [f"{self.path.name}  -  {self.name or '(unnamed)'}"]
        metrics = self.metrics
        if self.author:
            lines.append(f"  credit       {self.author.display}  ({self.author.url})")
        if self.license or self.tags:
            lines.append(
                f"  license      {self.license or '-'}"
                + (f"   tags: {', '.join(self.tags)}" if self.tags else "")
            )
        if metrics:
            lines.append(
                f"  engine       {metrics.get('engine_errors', 0)} error(s), "
                f"{metrics.get('engine_warnings', 0)} warning(s)"
            )
            determinism = metrics.get("deterministic")
            hashes = metrics.get("frame_hashes") or {}
            lines.append(
                "  determinism  "
                + (
                    f"identical at {len(hashes)} sample time(s)"
                    if determinism
                    else ("NOT deterministic" if determinism is False else "not checked")
                )
            )
            lines.append(
                "  budgets      "
                f"sprites {metrics.get('peak_live_sprites', 0)}  "
                f"mesh {metrics.get('peak_live_mesh_particles', 0)}  "
                f"nodes {metrics.get('node_count', 0)}  "
                f"duration {metrics.get('duration', 0):.2f} s  "
                f"textures {metrics.get('texture_bytes', 0) / 1048576:.1f} MiB  "
                f"bake {metrics.get('texture_bake_seconds', 0):.2f} s"
            )
        if self.previews:
            lines.append("  previews     " + ", ".join(sorted(self.previews.values())))
        lines.extend(f"  skipped      {note}" for note in self.skipped)
        if verbose:
            lines.extend(f"  {finding}" for finding in self.findings)
        lines.append(
            f"  {'PASS' if self.ok else 'FAIL'}  ({len(self.errors)} error(s), {len(self.warnings)} warning(s))"
        )
        return "\n".join(lines)


def check_effect(
    path: str | os.PathLike[str],
    *,
    previews: str | os.PathLike[str] | None = None,
    budgets: Budgets = Budgets(),
    binary: str | None = None,
    engine: bool = True,
    require_credit: bool = True,
    house_style: bool = True,
) -> CheckReport:
    """Run the whole contribution check on one effect document.

    ``engine=False`` skips validation, determinism and the budgets (the static
    rules still run); ``require_credit=False`` is what ``lint-library`` uses on
    ``examples/effects``, which carry no author block.
    """
    path = Path(path)
    report = CheckReport(path=path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        report.findings.append(_error("file.unreadable", str(exc)))
        return report
    except ValueError as exc:
        report.findings.append(_error("file.json", f"not valid JSON: {exc}"))
        return report
    if not isinstance(document, dict):
        report.findings.append(_error("file.json", "an effect document must be a JSON object"))
        return report

    report.name = str(document.get("name") or "")
    report.slug = slugify(report.name or path.stem, path.stem)
    for attribute, parse in (("author", parse_author), ("tags", parse_tags), ("license", parse_license)):
        try:
            setattr(report, attribute, parse(document))
        except AuthorError:
            pass

    if require_credit:
        report.findings.extend(metadata_findings(document, budgets=budgets))
    report.findings.extend(asset_findings(document))
    if house_style:
        report.findings.extend(house_style_findings(document))

    if engine:
        try:
            findings, metrics = engine_findings(path, document, budgets)
        except EngineUnavailable as exc:
            report.skipped.append(str(exc))
        else:
            report.findings.extend(findings)
            report.metrics = metrics
    else:
        report.skipped.append("engine checks skipped (--no-engine)")

    if previews is not None and report.ok:
        produced, preview_findings = render_previews(path, document, Path(previews), binary=binary)
        report.previews = produced
        report.findings.extend(preview_findings)
    elif previews is not None:
        report.skipped.append("previews skipped: the effect did not pass")
    return report


def lint_library(
    directories: Sequence[str | os.PathLike[str]],
    *,
    allowlist: Iterable[str] = LINT_ALLOWLIST,
    budgets: Budgets = Budgets(),
    engine: bool = True,
) -> tuple[list[CheckReport], list[CheckReport]]:
    """House style + validator over whole directories.

    Returns ``(reports, excused)``: effects named in ``allowlist`` that failed
    are reported separately and do not fail the run (they are being revised).
    Community directories additionally require the credit metadata.
    """
    allowed = set(allowlist)
    reports: list[CheckReport] = []
    excused: list[CheckReport] = []
    for directory in directories:
        base = Path(directory)
        if not base.is_dir():
            continue
        resolved = base.resolve()
        community = "community" in (resolved.name, resolved.parent.name)
        for path in sorted(base.glob("*.json")):
            report = check_effect(
                path, budgets=budgets, engine=engine, require_credit=community, house_style=True
            )
            (excused if (path.name in allowed and not report.ok) else reports).append(report)
    return reports, excused


# =========================================================================
# the index
# =========================================================================


def index_entry(path: Path, *, previews_dir: Path | None = None) -> JsonDict:
    """One ``community/index.json`` entry.  Deterministic: no timestamps, no paths."""
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    name = str(document.get("name") or path.stem)
    controls = document.get("controls")
    control_list = [
        {
            "id": str(control.get("id") or ""),
            "label": str(control.get("label") or control.get("id") or ""),
            "group": str(control.get("group") or ""),
        }
        for control in (controls if isinstance(controls, list) else [])
        if isinstance(control, dict)
    ]
    parsed: dict[str, Any] = {"author": None, "tags": [], "license": None}
    for key, parse in (("author", parse_author), ("tags", parse_tags), ("license", parse_license)):
        try:
            parsed[key] = parse(document)
        except AuthorError:
            pass

    entry: JsonDict = {
        "slug": path.stem,
        "file": f"effects/{path.name}",
        "name": name,
        "description": effect_description(document),
        "author": parsed["author"].to_json() if parsed["author"] else None,
        "tags": parsed["tags"],
        "license": parsed["license"],
        "duration": _static(document.get("duration"), 0.0),
        "node_count": len(_nodes(document)),
        "controls": control_list,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "schema_version": str(document.get("schema_version") or ""),
        "previews": {},
    }
    if previews_dir is not None:
        names = preview_names(slugify(name, path.stem))
        entry["previews"] = {
            key: f"previews/{value}"
            for key, value in sorted(names.items())
            if (previews_dir / value).is_file()
        }
    return entry


def build_index(
    effects_dir: str | os.PathLike[str], *, previews_dir: str | os.PathLike[str] | None = None
) -> JsonDict:
    """Build the whole index document.  Same inputs always give the same bytes."""
    base = Path(effects_dir)
    previews = Path(previews_dir) if previews_dir is not None else None
    entries = [index_entry(path, previews_dir=previews) for path in sorted(base.glob("*.json"))]
    entries.sort(key=lambda entry: entry["slug"])
    return {
        "schema": INDEX_SCHEMA,
        "repository": GITHUB_REPO_URL,
        "license": LICENSE,
        "count": len(entries),
        "effects": entries,
    }


def index_text(index: JsonDict) -> str:
    """The canonical serialisation: sorted keys, 2-space indent, one trailing newline."""
    return json.dumps(index, indent=2, sort_keys=True) + "\n"


def write_index(index: JsonDict, target: str | os.PathLike[str]) -> Path:
    """Write ``index`` deterministically."""
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index_text(index), encoding="utf-8")
    return path


# =========================================================================
# command line
# =========================================================================


def _repo_root() -> Path:
    """``<repo>/python/aetherfx/community.py`` -> ``<repo>``."""
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aetherfx-community",
        description=(
            "Checks for contributed AetherFX effects: validation, determinism, budgets, "
            "procedural-only assets, credit metadata and the house style (CONTRIBUTING.md)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="run the full contribution check on one or more effects")
    check.add_argument("effects", nargs="+", help="effect JSON documents")
    check.add_argument("--previews", default=None, metavar="DIR",
                       help="render a 6-frame contact sheet and a GIF into DIR (never commit them)")
    check.add_argument("--json", action="store_true", help="print the report as JSON")
    check.add_argument("--no-engine", action="store_true",
                       help="skip validation, determinism and budgets (static rules only)")
    check.add_argument("--binary", default=None, help="engine binary for the preview render")

    lint = sub.add_parser("lint-library", help="house style + validator over the shipped libraries")
    lint.add_argument("directories", nargs="*", default=None,
                      help="directories of effect JSON (default: examples/effects and community/effects)")
    lint.add_argument("--json", action="store_true", help="print the reports as JSON")
    lint.add_argument("--no-engine", action="store_true", help="static rules only")
    lint.add_argument("--no-allowlist", action="store_true",
                      help="fail on the effects that are known to be in revision too")

    index = sub.add_parser("index", help="build community/index.json")
    index.add_argument("effects_dir", nargs="?", default=None, help="default: community/effects")
    index.add_argument("--out", default=None, help="default: <effects_dir>/../index.json")
    index.add_argument("--previews", default=None, metavar="DIR", help="record the previews found in DIR")
    index.add_argument("--check", action="store_true",
                       help="exit 1 when the file on disk is not what this run produces (CI)")
    return parser


def _run_check(args: argparse.Namespace) -> int:
    reports = [
        check_effect(path, previews=args.previews, binary=args.binary, engine=not args.no_engine)
        for path in args.effects
    ]
    if args.json:
        print(json.dumps(
            {"ok": all(r.ok for r in reports), "effects": [r.to_json() for r in reports]},
            indent=2, sort_keys=True,
        ))
    else:
        for report in reports:
            print(report.report())
            print()
    return 0 if all(report.ok for report in reports) else 1


def _run_lint(args: argparse.Namespace) -> int:
    root = _repo_root()
    directories = args.directories or [root / "examples" / "effects", root / "community" / "effects"]
    reports, excused = lint_library(
        directories, allowlist=() if args.no_allowlist else LINT_ALLOWLIST, engine=not args.no_engine
    )
    failed = [report for report in reports if not report.ok]
    if args.json:
        print(json.dumps(
            {
                "ok": not failed,
                "checked": len(reports) + len(excused),
                "failed": [report.to_json() for report in failed],
                "excused": [report.to_json() for report in excused],
                "effects": [report.to_json() for report in reports],
            },
            indent=2, sort_keys=True,
        ))
        return 0 if not failed else 1

    for report in reports:
        marker = "ok  " if report.ok else "FAIL"
        print(f"{marker} {report.path.name:<30} {len(report.errors)} error(s), "
              f"{len(report.warnings)} warning(s)")
        for finding in report.findings:
            print(f"       {finding}")
    for report in excused:
        print(f"note {report.path.name:<30} in revision, not failing the run "
              f"({len(report.errors)} error(s))")
        for finding in report.errors:
            print(f"       {finding}")
    print(f"\n{len(reports) + len(excused)} effect(s) checked, {len(failed)} failing, {len(excused)} excused")
    return 0 if not failed else 1


def _run_index(args: argparse.Namespace) -> int:
    root = _repo_root()
    effects_dir = Path(args.effects_dir) if args.effects_dir else root / "community" / "effects"
    target = Path(args.out) if args.out else effects_dir.parent / "index.json"
    index = build_index(effects_dir, previews_dir=args.previews)
    text = index_text(index)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            print(f"{target} is out of date; run tools/build_community_index.py", file=sys.stderr)
            return 1
        print(f"{target} is up to date ({index['count']} effect(s))")
        return 0
    write_index(index, target)
    print(f"wrote {target} ({index['count']} effect(s))")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _run_check(args)
    if args.command == "lint-library":
        return _run_lint(args)
    return _run_index(args)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
