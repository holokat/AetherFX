"""Guards for a public repository: no reference art, no stray media, a licence that matches the metadata."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Media files that may be tracked. Anything else (concept sheets, screenshots, pasted references)
#: must stay out of git; reference images live under the ignored ``out/`` directory only.
ALLOWED_MEDIA = {
    "examples/references/fire_aoe_synthetic.png",   # rendered by the engine itself, used by tests
}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".psd",
                  ".mp4", ".mov", ".webm", ".avi"}


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [line for line in out.splitlines() if line]


#: README media: ONLY captures rendered by this engine of this library's own effects, and screenshots of
#: the studio showing them. Never concept art, never anything a user pasted as a reference.
ALLOWED_MEDIA_DIRS = ("docs/media/",)


def test_no_reference_art_or_stray_media_is_tracked():
    media = {path for path in _tracked_files() if Path(path).suffix.lower() in MEDIA_SUFFIXES}
    media = {path for path in media if not path.startswith(ALLOWED_MEDIA_DIRS)}
    unexpected = sorted(media - ALLOWED_MEDIA)
    assert not unexpected, (
        "media files are tracked: " + ", ".join(unexpected) + ". Reference images and screenshots never go into "
        "the repository; keep them under out/ (ignored). Engine-rendered test fixtures must be added to ALLOWED_MEDIA."
    )


def test_licence_is_mit_everywhere():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert 'license = { text = "MIT" }' in (REPO_ROOT / "python" / "pyproject.toml").read_text(encoding="utf-8")
    assert (REPO_ROOT / "THIRD_PARTY_NOTICES.md").is_file()
