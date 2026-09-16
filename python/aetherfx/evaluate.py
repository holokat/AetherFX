"""Non-AI image metrics: the evaluation baseline for reference matching.

These are the metrics ``inspect_render`` and ``compare_reference`` expose in
docs/AGENT_API.md.  The C++ side (``src/render`` + ``src/tools``) implements the
same metrics later and must agree with this module, so **every formula below is
specified exactly** - filters, bin edges, tie-breaks, normalisation and all.
Where a choice was arbitrary, the docstring states the choice rather than
leaving it to the implementer.

Conventions used throughout:

* Images are read as 8-bit sRGB RGB and converted to **linear RGB** with the
  IEC 61966-2-1 sRGB EOTF before any measurement.
* Luminance is Rec. 709 relative luminance of the linear RGB triple:
  ``L = 0.2126 R + 0.7152 G + 0.0722 B``.
* All returned numbers are plain Python floats/ints, so results are directly
  JSON-serialisable.
* All distances are oriented "0 = identical, 1 = maximally different"; the
  single combined ``score`` is flipped so that 1 = indistinguishable.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

__all__ = [
    "LUMA_WEIGHTS",
    "OTSU_BINS",
    "COMPARE_SIZE",
    "PALETTE_K",
    "HISTOGRAM_BINS",
    "RADIAL_RINGS",
    "SCORE_WEIGHTS",
    "MAX_PALETTE_SAMPLES",
    "srgb_to_linear",
    "load_linear_rgb",
    "luminance",
    "otsu_threshold",
    "coverage_mask",
    "kmeans_palette",
    "image_stats",
    "letterbox",
    "compare_images",
]

#: Rec. 709 relative-luminance weights applied to linear RGB.
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)

#: Histogram resolution used by the Otsu threshold search.
OTSU_BINS = 256

#: Both images are letterboxed into this square before comparison.
COMPARE_SIZE = 256

#: Number of k-means centroids in a palette.
PALETTE_K = 5

#: Bin count of the luminance histogram distance.
HISTOGRAM_BINS = 32

#: Number of concentric rings in the radial energy profile.
RADIAL_RINGS = 16

#: Deterministic RNG seed for k-means++ initialisation.
PALETTE_SEED = 0

#: Upper bound on pixels fed to k-means; larger images are strided down.
MAX_PALETTE_SAMPLES = 20_000

#: Maximum Lloyd iterations, and the centroid-movement tolerance that stops it.
PALETTE_MAX_ITERATIONS = 32
PALETTE_TOLERANCE = 1e-6

#: Weights of the five distance terms in :func:`compare_images`.  They sum to 1.
SCORE_WEIGHTS: dict[str, float] = {
    "coverage_iou": 0.30,
    "palette_distance": 0.25,
    "luminance_histogram_distance": 0.15,
    "centroid_offset": 0.15,
    "radial_profile_distance": 0.15,
}


# ---------------------------------------------------------------------------
# colour and loading
# ---------------------------------------------------------------------------


def srgb_to_linear(values: np.ndarray) -> np.ndarray:
    """Apply the sRGB EOTF to values already normalised to [0, 1].

    ``c_lin = c/12.92`` for ``c <= 0.04045`` and
    ``((c + 0.055)/1.055) ** 2.4`` otherwise (IEC 61966-2-1).
    """
    values = np.asarray(values, dtype=np.float64)
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def load_linear_rgb(path: str | Path) -> np.ndarray:
    """Load an image file as an ``(H, W, 3)`` float64 array of linear RGB.

    The file is converted to 8-bit RGB first (alpha is composited away by
    Pillow's ``convert("RGB")``, i.e. simply dropped), divided by 255 and passed
    through :func:`srgb_to_linear`.
    """
    with Image.open(path) as handle:
        rgb = handle.convert("RGB")
        array = np.asarray(rgb, dtype=np.float64) / 255.0
    return srgb_to_linear(array)


def luminance(linear_rgb: np.ndarray) -> np.ndarray:
    """Rec. 709 relative luminance of an ``(..., 3)`` linear RGB array."""
    weights = np.asarray(LUMA_WEIGHTS, dtype=np.float64)
    return np.asarray(linear_rgb, dtype=np.float64) @ weights


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def otsu_threshold(lum: np.ndarray, bins: int = OTSU_BINS) -> float:
    """Otsu's threshold on luminance, computed on a fixed [0, 1] histogram.

    Exact definition (mirror this in C++):

    1. Clamp luminance to ``[0, 1]`` and bin it into ``bins`` equal-width bins
       over ``[0, 1]``; bin ``k`` covers ``[k/bins, (k+1)/bins)`` and the top bin
       includes 1.0.
    2. For each split index ``k`` in ``[0, bins-2]``, background is bins
       ``0..k`` and foreground is bins ``k+1..bins-1``; the between-class
       variance is ``w0 * w1 * (mu0 - mu1) ** 2`` with bin centres
       ``(i + 0.5) / bins`` as the bin values.
    3. Pick the **smallest** ``k`` maximising that variance (ties go to the
       lower split), and return the threshold ``(k + 1) / bins`` - the upper
       edge of the last background bin.

    A constant image yields an all-zero variance curve, so ``k = 0`` wins and
    the threshold is ``1/bins``; a black image therefore has empty coverage.
    """
    flat = np.clip(np.asarray(lum, dtype=np.float64).reshape(-1), 0.0, 1.0)
    if flat.size == 0:
        return 1.0 / bins
    counts, _ = np.histogram(flat, bins=bins, range=(0.0, 1.0))
    counts = counts.astype(np.float64)
    total = counts.sum()
    if total <= 0:  # pragma: no cover - histogram of a non-empty array is non-empty
        return 1.0 / bins
    centres = (np.arange(bins, dtype=np.float64) + 0.5) / bins

    weight0 = np.cumsum(counts)[:-1] / total
    weight1 = 1.0 - weight0
    sum0 = np.cumsum(counts * centres)[:-1]
    total_sum = float((counts * centres).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        mean0 = np.where(weight0 > 0, sum0 / (weight0 * total), 0.0)
        mean1 = np.where(weight1 > 0, (total_sum - sum0) / (weight1 * total), 0.0)
    variance = weight0 * weight1 * (mean0 - mean1) ** 2
    variance = np.nan_to_num(variance, nan=0.0, posinf=0.0, neginf=0.0)
    split = int(np.argmax(variance))  # argmax returns the first maximum -> lowest k
    return (split + 1) / bins


def coverage_mask(lum: np.ndarray, bins: int = OTSU_BINS) -> tuple[np.ndarray, float]:
    """Return ``(mask, threshold)`` where ``mask = luminance > otsu_threshold``."""
    threshold = otsu_threshold(lum, bins=bins)
    return np.asarray(lum, dtype=np.float64) > threshold, threshold


def _coverage_bbox(mask: np.ndarray) -> tuple[float, float, float, float]:
    """Normalised ``[x0, y0, x1, y1]`` bounding box of a boolean mask.

    Coordinates are fractions of width/height with the origin at the top left.
    ``x1``/``y1`` are exclusive edges, so a single lit pixel of a 256-wide image
    spans ``1/256``.  An empty mask gives ``(0, 0, 0, 0)``.
    """
    if not mask.any():
        return (0.0, 0.0, 0.0, 0.0)
    height, width = mask.shape
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return (
        float(cols[0] / width),
        float(rows[0] / height),
        float((cols[-1] + 1) / width),
        float((rows[-1] + 1) / height),
    )


def _coverage_centroid(mask: np.ndarray) -> tuple[float, float] | None:
    """Pixel-space centroid ``(x, y)`` of a mask, using pixel centres.

    Returns ``None`` when the mask is empty.
    """
    if not mask.any():
        return None
    rows, cols = np.nonzero(mask)
    return (float(cols.mean()) + 0.5, float(rows.mean()) + 0.5)


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------


def _stride_sample(pixels: np.ndarray, limit: int = MAX_PALETTE_SAMPLES) -> np.ndarray:
    """Deterministically thin an ``(N, 3)`` pixel array to at most ``limit`` rows.

    Pixels are taken in row-major order with stride ``ceil(N / limit)``; no
    randomness and no resampling, so the C++ implementation can reproduce the
    exact sample set.
    """
    count = pixels.shape[0]
    if count <= limit:
        return pixels
    stride = math.ceil(count / limit)
    return pixels[::stride]


def _kmeans_plusplus_init(samples: np.ndarray, k: int, seed: int) -> np.ndarray:
    """k-means++ seeding with ``numpy.random.default_rng(seed)``.

    The first centre is drawn uniformly; each subsequent centre is drawn with
    probability proportional to the squared distance to the nearest chosen
    centre.  Both draws use ``rng.random()`` against the cumulative
    distribution, which is the form a C++ port can reproduce with any RNG that
    yields the same uniform stream.
    """
    rng = np.random.default_rng(seed)
    count = samples.shape[0]
    centres = np.empty((k, samples.shape[1]), dtype=np.float64)
    first = int(rng.integers(0, count))
    centres[0] = samples[first]
    closest = np.sum((samples - centres[0]) ** 2, axis=1)
    for index in range(1, k):
        total = float(closest.sum())
        if total <= 0.0:
            centres[index] = samples[int(rng.integers(0, count))]
        else:
            cumulative = np.cumsum(closest / total)
            pick = int(np.searchsorted(cumulative, float(rng.random()), side="left"))
            pick = min(pick, count - 1)
            centres[index] = samples[pick]
        closest = np.minimum(closest, np.sum((samples - centres[index]) ** 2, axis=1))
    return centres


def kmeans_palette(
    linear_rgb: np.ndarray, k: int = PALETTE_K, seed: int = PALETTE_SEED
) -> list[dict[str, Any]]:
    """Cluster linear-RGB pixels into ``k`` centroids with their shares.

    Deterministic by construction: stride sampling (:func:`_stride_sample`),
    k-means++ init seeded with ``seed``, then Lloyd iterations until centroids
    move less than :data:`PALETTE_TOLERANCE` or
    :data:`PALETTE_MAX_ITERATIONS` is reached.  Empty clusters keep their
    previous centroid and get share 0.

    The result always has ``k`` entries, sorted by descending share and, for
    equal shares, by descending luminance then by RGB lexicographic order, so
    the output is stable across runs and platforms.
    """
    pixels = np.asarray(linear_rgb, dtype=np.float64).reshape(-1, 3)
    if pixels.size == 0:  # pragma: no cover - images always have pixels
        return [{"rgb": [0.0, 0.0, 0.0], "share": 0.0} for _ in range(k)]
    samples = _stride_sample(pixels)
    if samples.shape[0] < k:
        samples = np.repeat(samples, math.ceil(k / samples.shape[0]), axis=0)

    centres = _kmeans_plusplus_init(samples, k, seed)
    labels = np.zeros(samples.shape[0], dtype=np.int64)
    for _ in range(PALETTE_MAX_ITERATIONS):
        distances = ((samples[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        labels = np.argmin(distances, axis=1)
        moved = 0.0
        for index in range(k):
            members = samples[labels == index]
            if members.shape[0] == 0:
                continue
            new_centre = members.mean(axis=0)
            moved = max(moved, float(np.abs(new_centre - centres[index]).max()))
            centres[index] = new_centre
        if moved <= PALETTE_TOLERANCE:
            break

    total = float(samples.shape[0])
    entries: list[dict[str, Any]] = []
    for index in range(k):
        share = float(np.count_nonzero(labels == index) / total)
        rgb = [float(component) for component in centres[index]]
        entries.append({"rgb": rgb, "share": share})
    entries.sort(
        key=lambda entry: (
            -entry["share"],
            -(
                LUMA_WEIGHTS[0] * entry["rgb"][0]
                + LUMA_WEIGHTS[1] * entry["rgb"][1]
                + LUMA_WEIGHTS[2] * entry["rgb"][2]
            ),
            entry["rgb"][0],
            entry["rgb"][1],
            entry["rgb"][2],
        )
    )
    return entries


# ---------------------------------------------------------------------------
# single-image statistics
# ---------------------------------------------------------------------------


def image_stats(path: str | Path) -> dict[str, Any]:
    """Measure one image.

    Returns, matching ``inspect_render`` in docs/AGENT_API.md:

    ``width``, ``height``
        Pixel dimensions of the file.
    ``mean_luminance``, ``max_luminance``
        Rec. 709 luminance of the linear RGB image, averaged and maximised over
        every pixel.
    ``coverage``
        Fraction of pixels whose luminance exceeds :func:`otsu_threshold`.
    ``coverage_threshold``
        The Otsu threshold itself, so a caller can reproduce the mask.
    ``bbox``
        ``[x0, y0, x1, y1]`` of the coverage mask, normalised to [0, 1] with the
        origin top-left and exclusive far edges.  ``[0,0,0,0]`` when empty.
    ``dominant_colors``
        Five ``{"rgb": [r, g, b], "share": s}`` entries from
        :func:`kmeans_palette` (linear RGB, deterministic k-means++ seed 0).
    """
    linear = load_linear_rgb(path)
    height, width = linear.shape[:2]
    lum = luminance(linear)
    mask, threshold = coverage_mask(lum)
    return {
        "width": int(width),
        "height": int(height),
        "mean_luminance": float(lum.mean()),
        "max_luminance": float(lum.max()),
        "coverage": float(np.count_nonzero(mask) / lum.size),
        "coverage_threshold": float(threshold),
        "bbox": list(_coverage_bbox(mask)),
        "dominant_colors": kmeans_palette(linear),
    }


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------


def letterbox(path: str | Path, size: int = COMPARE_SIZE) -> np.ndarray:
    """Load an image into a ``size x size`` linear-RGB canvas, preserving aspect.

    Exact definition (mirror this in C++):

    1. Convert to 8-bit sRGB RGB.
    2. ``scale = size / max(width, height)``; the new size is
       ``(max(1, round(width * scale)), max(1, round(height * scale)))``.
    3. Resample with **bilinear** filtering (``PIL.Image.BILINEAR``).
    4. Paste onto a black ``size x size`` canvas at
       ``((size - new_width) // 2, (size - new_height) // 2)``.
    5. Convert the canvas to linear RGB.

    Padding is black, which both images receive, so it cancels out of IoU but
    does bias the luminance histogram towards the dark bins - accepted, and
    identical on both sides.
    """
    with Image.open(path) as handle:
        rgb = handle.convert("RGB")
        width, height = rgb.size
        scale = size / max(width, height)
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        resized = rgb.resize((new_width, new_height), Image.BILINEAR)
        canvas = Image.new("RGB", (size, size), (0, 0, 0))
        canvas.paste(resized, ((size - new_width) // 2, (size - new_height) // 2))
        array = np.asarray(canvas, dtype=np.float64) / 255.0
    return srgb_to_linear(array)


def _palette_distance(left: Sequence[dict[str, Any]], right: Sequence[dict[str, Any]]) -> float:
    """Share-weighted greedy transport between two palettes, normalised to [0, 1].

    Exact definition (mirror this in C++):

    * ``d(i, j)`` is the Euclidean distance between the two linear-RGB
      centroids divided by ``sqrt(3)`` and clamped to ``[0, 1]`` - ``sqrt(3)``
      is the diagonal of the unit RGB cube.
    * Each side keeps a remaining weight, initially its ``share``.
    * Repeatedly take the pair ``(i, j)`` with remaining weight on both sides
      that minimises ``d(i, j)``, breaking ties by the lowest ``i`` then the
      lowest ``j``.  Move ``w = min(remaining_i, remaining_j)`` of weight,
      accumulate ``w * d(i, j)``, and subtract ``w`` from both.
    * The result is the accumulated distance divided by the total moved weight,
      i.e. the share-weighted mean distance of the matching.  0 when nothing
      could be matched.

    Greedy rather than optimal transport: it is deterministic, needs no solver,
    and terminates in at most ``len(left) + len(right) - 1`` moves.
    """
    if not left or not right:
        return 0.0
    left_rgb = np.asarray([entry["rgb"] for entry in left], dtype=np.float64)
    right_rgb = np.asarray([entry["rgb"] for entry in right], dtype=np.float64)
    distances = np.sqrt(((left_rgb[:, None, :] - right_rgb[None, :, :]) ** 2).sum(axis=2))
    distances = np.clip(distances / math.sqrt(3.0), 0.0, 1.0)

    remaining_left = np.asarray([float(entry["share"]) for entry in left], dtype=np.float64)
    remaining_right = np.asarray([float(entry["share"]) for entry in right], dtype=np.float64)
    epsilon = 1e-12
    total = 0.0
    moved = 0.0
    while True:
        live_left = np.flatnonzero(remaining_left > epsilon)
        live_right = np.flatnonzero(remaining_right > epsilon)
        if live_left.size == 0 or live_right.size == 0:
            break
        sub = distances[np.ix_(live_left, live_right)]
        flat = int(np.argmin(sub))  # first minimum -> lowest (i, j) in row-major order
        row, column = divmod(flat, sub.shape[1])
        i = int(live_left[row])
        j = int(live_right[column])
        weight = float(min(remaining_left[i], remaining_right[j]))
        total += weight * float(distances[i, j])
        moved += weight
        remaining_left[i] -= weight
        remaining_right[j] -= weight
    return float(total / moved) if moved > epsilon else 0.0


def _luminance_histogram(lum: np.ndarray, bins: int = HISTOGRAM_BINS) -> np.ndarray:
    """Normalised histogram of luminance clamped to [0, 1] over ``bins`` bins."""
    counts, _ = np.histogram(np.clip(lum, 0.0, 1.0), bins=bins, range=(0.0, 1.0))
    counts = counts.astype(np.float64)
    total = counts.sum()
    if total <= 0:  # pragma: no cover
        return np.full(bins, 1.0 / bins)
    return counts / total


def _radial_profile(
    lum: np.ndarray,
    centre: tuple[float, float],
    rings: int = RADIAL_RINGS,
    max_radius: float | None = None,
) -> np.ndarray:
    """Normalised luminance energy in ``rings`` concentric rings around ``centre``.

    Exact definition (mirror this in C++):

    * ``centre`` is in pixel coordinates (pixel centres at ``x + 0.5``).
    * ``max_radius`` defaults to **half the image diagonal**, a value that does
      not depend on where the centroid sits.  Using a fixed ring scale is what
      makes this metric measure *shape* only: two identical shapes at different
      screen positions produce the same profile, and their displacement is
      reported by ``centroid_offset`` instead of being counted twice.
    * Ring ``k`` covers ``r in [k/rings * max_radius, (k+1)/rings * max_radius)``;
      anything beyond the last ring (possible when the centroid is off-centre)
      is clamped into it, so no energy is discarded.
    * Each ring accumulates the sum of pixel luminance; the vector is then
      normalised to sum 1, or set to uniform ``1/rings`` when the image is black.
    """
    height, width = lum.shape
    if max_radius is None:
        max_radius = math.hypot(width, height) / 2.0
    if max_radius <= 0:  # pragma: no cover - a 0x0 image cannot be loaded
        return np.full(rings, 1.0 / rings)
    ys, xs = np.mgrid[0:height, 0:width]
    dx = (xs + 0.5) - centre[0]
    dy = (ys + 0.5) - centre[1]
    radius = np.sqrt(dx * dx + dy * dy)
    index = np.minimum((radius / max_radius * rings).astype(np.int64), rings - 1)
    energy = np.bincount(index.reshape(-1), weights=np.clip(lum, 0.0, None).reshape(-1), minlength=rings)
    energy = energy[:rings].astype(np.float64)
    total = float(energy.sum())
    if total <= 0:
        return np.full(rings, 1.0 / rings)
    return energy / total


def compare_images(reference_path: str | Path, render_path: str | Path) -> dict[str, Any]:
    """Compare a render against a reference image with five metrics plus a score.

    Both images are first letterboxed into a ``256 x 256`` linear-RGB canvas
    (:func:`letterbox`), so the metrics are resolution- and aspect-independent.
    Each metric is oriented 0 = identical, 1 = maximally different, except
    ``coverage_iou`` which is an overlap (1 = identical).

    ``coverage_iou``
        Intersection over union of the two Otsu coverage masks.  Empty union
        (both images unlit) counts as a perfect match, 1.0.
    ``palette_distance``
        Share-weighted greedy matching of the two five-colour palettes in linear
        RGB, normalised by ``sqrt(3)`` - see :func:`_palette_distance`.
    ``luminance_histogram_distance``
        ``0.5 * sum |p - q|`` over 32-bin normalised luminance histograms; the
        halved L1 of two probability vectors is the total-variation distance in
        ``[0, 1]``.
    ``centroid_offset``
        Euclidean distance between the two coverage centroids divided by the
        canvas diagonal (``sqrt(2) * 256``).  Both masks empty -> 0.0; exactly
        one empty -> 1.0.
    ``radial_profile_distance``
        ``0.5 * sum |p - q|`` over 16-ring normalised radial energy profiles,
        each taken around **its own** image's coverage centroid (falling back to
        the image centre when the mask is empty) with a fixed ring scale of half
        the canvas diagonal, so this term measures shape distribution rather
        than position - translation is reported by ``centroid_offset`` alone.

    ``score`` is ``1 - (0.30 * (1 - coverage_iou) + 0.25 * palette_distance +
    0.15 * luminance_histogram_distance + 0.15 * centroid_offset +
    0.15 * radial_profile_distance)``, clamped to ``[0, 1]``.  The weights are
    :data:`SCORE_WEIGHTS` and sum to 1, so identical images score 1.0.

    ``notes`` carries short human-readable observations, matching the ``notes``
    member of the ``compare_reference`` tool.
    """
    reference = letterbox(reference_path)
    render = letterbox(render_path)

    reference_lum = luminance(reference)
    render_lum = luminance(render)
    reference_mask, _ = coverage_mask(reference_lum)
    render_mask, _ = coverage_mask(render_lum)

    intersection = float(np.count_nonzero(reference_mask & render_mask))
    union = float(np.count_nonzero(reference_mask | render_mask))
    coverage_iou = 1.0 if union == 0.0 else intersection / union

    palette_distance = _palette_distance(kmeans_palette(reference), kmeans_palette(render))

    histogram_distance = 0.5 * float(
        np.abs(_luminance_histogram(reference_lum) - _luminance_histogram(render_lum)).sum()
    )

    reference_centroid = _coverage_centroid(reference_mask)
    render_centroid = _coverage_centroid(render_mask)
    diagonal = math.hypot(COMPARE_SIZE, COMPARE_SIZE)
    if reference_centroid is None and render_centroid is None:
        centroid_offset = 0.0
    elif reference_centroid is None or render_centroid is None:
        centroid_offset = 1.0
    else:
        centroid_offset = min(
            1.0,
            math.hypot(
                reference_centroid[0] - render_centroid[0],
                reference_centroid[1] - render_centroid[1],
            )
            / diagonal,
        )

    centre = (COMPARE_SIZE / 2.0, COMPARE_SIZE / 2.0)
    radial_distance = 0.5 * float(
        np.abs(
            _radial_profile(reference_lum, reference_centroid or centre)
            - _radial_profile(render_lum, render_centroid or centre)
        ).sum()
    )

    penalty = (
        SCORE_WEIGHTS["coverage_iou"] * (1.0 - coverage_iou)
        + SCORE_WEIGHTS["palette_distance"] * palette_distance
        + SCORE_WEIGHTS["luminance_histogram_distance"] * histogram_distance
        + SCORE_WEIGHTS["centroid_offset"] * centroid_offset
        + SCORE_WEIGHTS["radial_profile_distance"] * radial_distance
    )
    score = max(0.0, min(1.0, 1.0 - penalty))

    notes: list[str] = []
    if union == 0.0:
        notes.append("both images are below the Otsu coverage threshold; the comparison is uninformative")
    if reference_centroid is not None and render_centroid is None:
        notes.append("the render has no lit coverage while the reference does")
    if render_centroid is not None and reference_centroid is None:
        notes.append("the render has lit coverage while the reference does not")
    if coverage_iou < 0.4 and union > 0.0:
        notes.append("coverage silhouettes differ substantially; check emitter shape, radius and scale")
    if palette_distance > 0.3:
        notes.append("palette differs substantially; check colour, color_over_life and emissive intensity")
    if centroid_offset > 0.1:
        notes.append("the effect sits in a different place on screen; check camera, position and emitter centre")
    if radial_distance > 0.3:
        notes.append("energy is distributed differently from the centre outwards; check radius and falloff")

    return {
        "coverage_iou": float(coverage_iou),
        "palette_distance": float(palette_distance),
        "luminance_histogram_distance": float(histogram_distance),
        "centroid_offset": float(centroid_offset),
        "radial_profile_distance": float(radial_distance),
        "score": float(score),
        "notes": notes,
    }
