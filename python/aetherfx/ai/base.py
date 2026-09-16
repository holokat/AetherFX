"""The `ReferenceAnalyzer` interface and shared image helpers.

Per docs/ARCHITECTURE.md section 9 the AI layer is optional and lives entirely
behind this interface: the engine runs fully without it, and a new adapter only
has to implement these four methods.

Reading guide for an LLM:

* ``analyze_reference`` is Mode A: image (plus optional prompt) in, an
  :class:`~aetherfx.ead.EffectAnalysisDocument` out.
* ``segment_reference`` returns per-element regions; a vision-only adapter may
  return boxes with ``mask=None`` and must say so in the label/confidence rather
  than fabricating a mask.
* ``describe_effect`` is a plain-language description, useful for logs and for
  prompting a second pass.
* ``evaluate_render`` closes the loop: reference + render (+ the EAD that
  produced it) in, a :class:`~aetherfx.ead.RenderEvaluation` with
  :class:`~aetherfx.ead.ChangeSuggestion` items out.

Images are accepted as a filesystem path or as raw bytes everywhere.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Union

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..ead import EffectAnalysisDocument, RenderEvaluation

__all__ = ["ImageInput", "MEDIA_TYPES", "Segment", "ReferenceAnalyzer", "read_image_bytes", "media_type_for"]

#: Anything an analyzer accepts as an image.
ImageInput = Union[str, "os.PathLike[str]", Path, bytes, bytearray]

#: Extension -> IANA media type, for the media types the Claude API accepts.
MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

#: Magic-number prefixes used when the caller hands over raw bytes.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
)


def read_image_bytes(image: ImageInput) -> bytes:
    """Return the raw bytes of ``image``, reading the file when given a path."""
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    return Path(os.fspath(image)).read_bytes()


def media_type_for(image: ImageInput, default: str = "image/png") -> str:
    """Return the IANA media type of ``image``.

    Paths are classified by extension (see :data:`MEDIA_TYPES`); raw bytes are
    sniffed by magic number.  Unrecognised inputs fall back to ``default``.
    """
    if isinstance(image, (bytes, bytearray)):
        data = bytes(image[:16])
        for prefix, media in _MAGIC:
            if data.startswith(prefix):
                return media
        return default
    suffix = Path(os.fspath(image)).suffix.lower()
    return MEDIA_TYPES.get(suffix, default)


class Segment(BaseModel):
    """One segmented element of a reference image.

    ``bbox`` is ``[x0, y0, x1, y1]`` normalised to ``[0, 1]`` with the origin at
    the top left.  ``mask`` is a boolean numpy array shaped like the image, or
    ``None`` when the adapter can only give a box (which is the normal case for
    a vision LLM - do not synthesise a mask from the box).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    label: str
    bbox: tuple[float, float, float, float]
    mask: np.ndarray | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    def has_mask(self) -> bool:
        """True when a pixel mask is available (not just a bounding box)."""
        return self.mask is not None


class ReferenceAnalyzer(ABC):
    """Turn reference imagery into EADs and render verdicts.

    Implementations must be side-effect free apart from network calls, must not
    read credentials from anywhere but their constructor and their documented
    environment variable, and must mark every inference as inferred.
    """

    #: Registry key, e.g. ``"mock"`` or ``"claude"``.
    name: ClassVar[str] = "base"

    @abstractmethod
    def analyze_reference(self, image: ImageInput, prompt: str | None = None) -> EffectAnalysisDocument:
        """Analyse a reference image into an Effect Analysis Document.

        ``prompt`` adds caller intent ("make it a top-down ice AOE") and is
        recorded in the document's ``source.prompt``.
        """

    @abstractmethod
    def segment_reference(self, image: ImageInput) -> list[Segment]:
        """Split the reference into its visually separable elements."""

    @abstractmethod
    def describe_effect(self, image: ImageInput) -> str:
        """Describe the effect in plain language, for logs and follow-up prompts."""

    @abstractmethod
    def evaluate_render(
        self,
        reference: ImageInput,
        render: ImageInput,
        ead: EffectAnalysisDocument | None = None,
    ) -> RenderEvaluation:
        """Compare a render against its reference and propose concrete changes."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(name={self.name!r})"
