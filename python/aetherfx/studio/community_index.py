"""The optional remote community index the studio can list next to the local one.

Off by default.  When ``AETHERFX_COMMUNITY_INDEX`` (or ``--community-index URL``)
is set, the studio fetches that one JSON document **server side** - the browser
never talks to another origin - validates it, caches it for a few minutes and
shows the entries that are not already on disk.  A download then re-fetches the
effect itself and runs the full contribution check
(:func:`aetherfx.community.check_effect`) before the file is allowed anywhere
near the Library.

Every request is fenced in: https only (a ``file://`` or ``http://`` source has
to be opted into explicitly, which is what the tests do), a short timeout, a
size cap, and a strict schema that drops anything it does not recognise.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..community import INDEX_SCHEMA, AuthorError, parse_author, parse_tags

__all__ = ["RemoteIndexError", "RemoteIndex", "fetch_json", "normalise_index"]

LOGGER = logging.getLogger("aetherfx.studio.community")

#: Never read more than this from a remote source, index or effect.
MAX_BYTES = 4 * 1024 * 1024
#: Seconds to wait for the whole fetch.
TIMEOUT = 8.0
#: How long a successful fetch is served from memory.
TTL = 300.0
#: Entries a remote index may offer.
MAX_ENTRIES = 500

JsonDict = dict[str, Any]


class RemoteIndexError(RuntimeError):
    """The remote index could not be fetched, or did not look like an index."""


def _check_scheme(url: str, *, allow_insecure: bool) -> None:
    scheme = urlsplit(url).scheme.lower()
    if scheme == "https":
        return
    if allow_insecure and scheme in ("http", "file"):
        return
    raise RemoteIndexError(f"the community index must be served over https (got {scheme or 'no'} scheme)")


def fetch_json(url: str, *, allow_insecure: bool = False, max_bytes: int = MAX_BYTES,
               timeout: float = TIMEOUT) -> Any:
    """Fetch one JSON document with a scheme check, a timeout and a size cap."""
    _check_scheme(url, allow_insecure=allow_insecure)
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AetherFX-Studio"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked above
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise RemoteIndexError(f"the document is {int(declared)} bytes, over the {max_bytes} byte cap")
            raw = response.read(max_bytes + 1)
    except RemoteIndexError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RemoteIndexError(f"could not fetch {url}: {exc}") from exc
    if len(raw) > max_bytes:
        raise RemoteIndexError(f"the document is larger than the {max_bytes} byte cap")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteIndexError(f"{url} is not valid JSON: {exc}") from exc


def _entry(raw: Any, base_url: str) -> JsonDict | None:
    """One validated index entry, or ``None`` when it is not usable."""
    if not isinstance(raw, dict):
        return None
    slug = raw.get("slug")
    name = raw.get("name")
    if not isinstance(slug, str) or not slug or not isinstance(name, str) or not name:
        return None
    if not slug.replace("_", "").replace("-", "").isalnum():
        return None                                   # a slug is also a file name: keep it boring
    # An index entry carries the derived fields `url` and `display` too; parse_author is
    # strict about unknown keys, so hand it only what an effect document would hold.
    credit = raw.get("author")
    credit = {key: credit[key] for key in ("github", "name") if key in credit} \
        if isinstance(credit, dict) else None
    try:
        author = parse_author({"author": credit}) if credit else None
        tags = parse_tags({"tags": raw.get("tags")})
    except AuthorError as exc:
        LOGGER.info("remote entry %s dropped: %s", slug, exc)
        return None
    file_name = raw.get("file") if isinstance(raw.get("file"), str) else f"effects/{slug}.json"
    if ".." in file_name or file_name.startswith("/") or urlsplit(file_name).scheme:
        return None
    description = raw.get("description")
    return {
        "slug": slug,
        "name": name,
        "description": description.strip()[:400] if isinstance(description, str) else "",
        "author": author.to_json() if author else None,
        "tags": tags,
        "license": raw.get("license") if isinstance(raw.get("license"), str) else None,
        "duration": float(raw["duration"]) if isinstance(raw.get("duration"), (int, float)) else None,
        "node_count": int(raw["node_count"]) if isinstance(raw.get("node_count"), int) else None,
        "sha256": raw.get("sha256") if isinstance(raw.get("sha256"), str) else None,
        "url": urljoin(base_url, file_name),
        "section": "community",
        "remote": True,
    }


def normalise_index(document: Any, base_url: str) -> list[JsonDict]:
    """Validate a fetched index and return the entries worth showing."""
    if not isinstance(document, dict):
        raise RemoteIndexError("the community index must be a JSON object")
    schema = document.get("schema")
    if schema is not None and schema != INDEX_SCHEMA:
        raise RemoteIndexError(f"unknown index schema {schema!r} (expected {INDEX_SCHEMA!r})")
    effects = document.get("effects")
    if not isinstance(effects, list):
        raise RemoteIndexError("the community index has no `effects` list")
    entries = [entry for entry in (_entry(raw, base_url) for raw in effects[:MAX_ENTRIES]) if entry]
    entries.sort(key=lambda entry: entry["slug"])
    return entries


@dataclass
class RemoteIndex:
    """One remote index URL with a small server-side cache.

    ``fetch()`` returns the cached entries while they are fresh and never raises:
    the Community view keeps working from the local directory when the network,
    or the repository, is not there yet.
    """

    url: str | None = None
    allow_insecure: bool = False
    ttl: float = TTL
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _entries: list[JsonDict] = field(default_factory=list, repr=False)
    _fetched_at: float = 0.0
    _error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def status(self) -> JsonDict:
        return {
            "enabled": self.enabled,
            "url": self.url,
            "cached": len(self._entries),
            "age": round(time.monotonic() - self._fetched_at, 1) if self._fetched_at else None,
            "error": self._error,
        }

    def fetch(self, *, force: bool = False) -> tuple[list[JsonDict], str | None]:
        """``(entries, error)``.  Blocking: call it off the event loop."""
        if not self.url:
            return [], None
        with self._lock:
            fresh = self._fetched_at and (time.monotonic() - self._fetched_at) < self.ttl
            if fresh and not force:
                return list(self._entries), self._error
            try:
                document = fetch_json(self.url, allow_insecure=self.allow_insecure)
                self._entries = normalise_index(document, self.url)
                self._error = None
            except RemoteIndexError as exc:
                self._error = str(exc)
                LOGGER.info("community index unavailable: %s", exc)
            self._fetched_at = time.monotonic()
            return list(self._entries), self._error

    def entry(self, slug: str) -> JsonDict | None:
        entries, _ = self.fetch()
        return next((entry for entry in entries if entry["slug"] == slug), None)

    def download(self, slug: str, target_dir: Path) -> Path:
        """Fetch one remote effect, run the contribution check, then save it.

        Raises :class:`RemoteIndexError` when the entry is unknown, the download
        fails, or the effect does not pass the check.
        """
        from ..community import check_effect  # noqa: PLC0415 - pulls in the native binding

        entry = self.entry(slug)
        if entry is None:
            raise RemoteIndexError(f"no community effect called {slug!r} in the index")
        document = fetch_json(entry["url"], allow_insecure=self.allow_insecure)
        if not isinstance(document, dict) or "nodes" not in document:
            raise RemoteIndexError(f"{entry['url']} is not an effect document")

        target_dir.mkdir(parents=True, exist_ok=True)
        staging = target_dir / f".{slug}.download.json"
        staging.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            report = check_effect(staging)
            if not report.ok:
                problems = "; ".join(f"{f.code}: {f.message}" for f in report.errors[:4])
                raise RemoteIndexError(f"{slug} did not pass the contribution check - {problems}")
            final = target_dir / f"{slug}.json"
            staging.replace(final)
        finally:
            staging.unlink(missing_ok=True)
        LOGGER.info("downloaded community effect %s to %s", slug, final)
        return final
