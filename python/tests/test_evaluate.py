"""Image metrics: the numbers the C++ side has to reproduce."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import pytest

from aetherfx.evaluate import (
    COMPARE_SIZE,
    SCORE_WEIGHTS,
    compare_images,
    image_stats,
    kmeans_palette,
    letterbox,
    luminance,
    otsu_threshold,
    srgb_to_linear,
)

from conftest import FIRE_AOE_REFERENCE

ImageFactory = Callable[..., Path]


class TestColour:
    def test_srgb_eotf_endpoints(self) -> None:
        assert srgb_to_linear(np.array([0.0]))[0] == pytest.approx(0.0)
        assert srgb_to_linear(np.array([1.0]))[0] == pytest.approx(1.0)

    def test_srgb_eotf_is_below_the_identity_in_the_midtones(self) -> None:
        assert srgb_to_linear(np.array([0.5]))[0] == pytest.approx(0.2140, abs=1e-3)

    def test_luminance_weights_sum_to_one(self) -> None:
        white = np.array([[[1.0, 1.0, 1.0]]])
        assert luminance(white)[0][0] == pytest.approx(1.0)


class TestOtsu:
    def test_constant_image_has_empty_coverage(self) -> None:
        assert otsu_threshold(np.zeros((16, 16))) == pytest.approx(1.0 / 256)

    def test_bimodal_image_splits_between_the_modes(self) -> None:
        values = np.concatenate([np.full(500, 0.05), np.full(500, 0.9)])
        threshold = otsu_threshold(values)
        assert 0.05 < threshold < 0.9


class TestImageStats:
    def test_reports_dimensions_and_coverage(self, make_image: ImageFactory) -> None:
        stats = image_stats(make_image("disc.png", radius=60))
        assert (stats["width"], stats["height"]) == (256, 256)
        expected = np.pi * 60 * 60 / (256 * 256)
        assert stats["coverage"] == pytest.approx(expected, rel=0.05)

    def test_bbox_is_normalised_and_centred(self, make_image: ImageFactory) -> None:
        bbox = image_stats(make_image("disc.png", radius=60))["bbox"]
        assert bbox[0] == pytest.approx(1 - bbox[2], abs=0.01)
        assert bbox[1] == pytest.approx(1 - bbox[3], abs=0.01)
        assert 0.0 <= bbox[0] < bbox[2] <= 1.0

    def test_blank_image_has_zero_coverage_and_empty_bbox(self, make_image: ImageFactory) -> None:
        stats = image_stats(make_image("blank.png", kind="blank"))
        assert stats["coverage"] == 0.0
        assert stats["bbox"] == [0.0, 0.0, 0.0, 0.0]
        assert stats["max_luminance"] == 0.0

    def test_palette_has_five_entries_summing_to_one(self, make_image: ImageFactory) -> None:
        palette = image_stats(make_image("disc.png"))["dominant_colors"]
        assert len(palette) == 5
        assert sum(entry["share"] for entry in palette) == pytest.approx(1.0)

    def test_palette_is_sorted_by_share(self, make_image: ImageFactory) -> None:
        palette = image_stats(make_image("disc.png"))["dominant_colors"]
        shares = [entry["share"] for entry in palette]
        assert shares == sorted(shares, reverse=True)

    def test_palette_is_deterministic_across_runs(self, make_image: ImageFactory) -> None:
        path = make_image("disc.png")
        assert image_stats(path)["dominant_colors"] == image_stats(path)["dominant_colors"]

    def test_palette_finds_the_disc_colour(self, make_image: ImageFactory) -> None:
        path = make_image("disc.png", color=(255, 140, 30))
        palette = image_stats(path)["dominant_colors"]
        lit = [entry for entry in palette if sum(entry["rgb"]) > 0.1]
        assert lit, "the lit colour should survive clustering"
        red, green, blue = lit[0]["rgb"]
        assert red > green > blue

    def test_kmeans_palette_handles_a_flat_image(self) -> None:
        flat = np.full((8, 8, 3), 0.5)
        palette = kmeans_palette(flat)
        assert len(palette) == 5
        assert sum(entry["share"] for entry in palette) == pytest.approx(1.0)


class TestLetterbox:
    def test_output_is_square(self, make_image: ImageFactory) -> None:
        canvas = letterbox(make_image("wide.png", size=(512, 128)))
        assert canvas.shape == (COMPARE_SIZE, COMPARE_SIZE, 3)

    def test_aspect_ratio_is_preserved(self, make_image: ImageFactory) -> None:
        square = letterbox(make_image("sq.png", size=(128, 128), radius=40))
        wide = letterbox(make_image("wide.png", size=(256, 128), centre=(128, 64), radius=40))
        # The wide image is padded top and bottom, so its top row stays black.
        assert wide[0].sum() == 0.0
        assert square.shape == wide.shape


class TestCompareImages:
    def test_identical_images_score_one(self, make_image: ImageFactory) -> None:
        path = make_image("a.png")
        result = compare_images(path, path)
        assert result["score"] == pytest.approx(1.0)
        assert result["coverage_iou"] == pytest.approx(1.0)
        assert result["palette_distance"] == pytest.approx(0.0)
        assert result["luminance_histogram_distance"] == pytest.approx(0.0)
        assert result["centroid_offset"] == pytest.approx(0.0)
        assert result["radial_profile_distance"] == pytest.approx(0.0)
        assert result["notes"] == []

    def test_blank_against_bright_disc_scores_low(self, make_image: ImageFactory) -> None:
        reference = make_image("disc.png", radius=70, color=(255, 200, 120))
        blank = make_image("blank.png", kind="blank")
        result = compare_images(reference, blank)
        assert result["score"] < 0.6
        assert result["coverage_iou"] == 0.0
        assert result["centroid_offset"] == 1.0
        assert any("no lit coverage" in note for note in result["notes"])

    def test_blank_is_much_worse_than_identical(self, make_image: ImageFactory) -> None:
        reference = make_image("disc.png")
        blank = make_image("blank.png", kind="blank")
        assert compare_images(reference, reference)["score"] - compare_images(reference, blank)["score"] > 0.3

    def test_translated_disc_moves_only_the_centroid(self, make_image: ImageFactory) -> None:
        reference = make_image("a.png", centre=(128, 128))
        moved = make_image("b.png", centre=(180, 128))
        result = compare_images(reference, moved)
        assert result["centroid_offset"] > 0.0
        assert result["coverage_iou"] < 1.0
        # A pure translation must not also be punished as a shape change.
        assert result["radial_profile_distance"] == pytest.approx(0.0, abs=1e-6)
        assert any("different place" in note for note in result["notes"])

    def test_larger_disc_changes_the_radial_profile_not_the_centroid(self, make_image: ImageFactory) -> None:
        reference = make_image("a.png", radius=50)
        bigger = make_image("big.png", radius=100)
        result = compare_images(reference, bigger)
        assert result["centroid_offset"] == pytest.approx(0.0, abs=1e-6)
        assert result["radial_profile_distance"] > 0.2

    def test_different_hue_moves_the_palette_distance(self, make_image: ImageFactory) -> None:
        warm = make_image("warm.png", color=(255, 140, 30))
        cold = make_image("cold.png", color=(30, 140, 255))
        result = compare_images(warm, cold)
        assert result["palette_distance"] > 0.05
        assert result["coverage_iou"] > 0.9

    def test_all_metrics_are_in_range(self, make_image: ImageFactory) -> None:
        reference = make_image("a.png", centre=(100, 100), radius=40)
        render = make_image("b.png", centre=(160, 150), radius=80, color=(30, 60, 255))
        result = compare_images(reference, render)
        for key in SCORE_WEIGHTS:
            assert 0.0 <= result[key] <= 1.0, key
        assert 0.0 <= result["score"] <= 1.0

    def test_score_matches_the_documented_weights(self, make_image: ImageFactory) -> None:
        reference = make_image("a.png", centre=(100, 100), radius=40)
        render = make_image("b.png", centre=(160, 150), radius=80, color=(30, 60, 255))
        result = compare_images(reference, render)
        expected = 1.0 - (
            SCORE_WEIGHTS["coverage_iou"] * (1.0 - result["coverage_iou"])
            + SCORE_WEIGHTS["palette_distance"] * result["palette_distance"]
            + SCORE_WEIGHTS["luminance_histogram_distance"] * result["luminance_histogram_distance"]
            + SCORE_WEIGHTS["centroid_offset"] * result["centroid_offset"]
            + SCORE_WEIGHTS["radial_profile_distance"] * result["radial_profile_distance"]
        )
        assert result["score"] == pytest.approx(max(0.0, expected))

    def test_weights_sum_to_one(self) -> None:
        assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0)

    def test_results_are_json_serialisable(self, make_image: ImageFactory) -> None:
        import json

        path = make_image("a.png")
        json.dumps(compare_images(path, path))
        json.dumps(image_stats(path))

    def test_resolution_independence(self, make_image: ImageFactory) -> None:
        small = make_image("small.png", size=(128, 128), centre=(64, 64), radius=30)
        large = make_image("large.png", size=(512, 512), centre=(256, 256), radius=120)
        assert compare_images(small, large)["score"] > 0.95


@pytest.mark.skipif(
    not FIRE_AOE_REFERENCE.is_file(),
    reason=f"{FIRE_AOE_REFERENCE} is missing; it is generated by the examples pipeline",
)
class TestAgainstTheProjectReference:
    """Sanity-check the metrics on the checked-in fire-AOE reference image."""

    def test_stats_describe_a_warm_effect_on_a_dark_ground(self) -> None:
        stats = image_stats(FIRE_AOE_REFERENCE)
        assert stats["width"] > 0 and stats["height"] > 0
        assert 0.02 < stats["coverage"] < 0.6, "a VFX reference is neither empty nor fully lit"
        warm = [
            entry
            for entry in stats["dominant_colors"]
            if entry["share"] > 0.01 and entry["rgb"][0] > entry["rgb"][1] > entry["rgb"][2]
        ]
        assert warm, "the fire palette should dominate over the dark ground"
        assert max(entry["rgb"][0] for entry in stats["dominant_colors"]) > 0.5

    def test_bbox_covers_most_of_the_frame(self) -> None:
        x0, y0, x1, y1 = image_stats(FIRE_AOE_REFERENCE)["bbox"]
        assert x1 - x0 > 0.5 and y1 - y0 > 0.5

    def test_self_comparison_is_a_perfect_match(self) -> None:
        assert compare_images(FIRE_AOE_REFERENCE, FIRE_AOE_REFERENCE)["score"] == pytest.approx(1.0)

    def test_a_blank_render_scores_far_worse(self, make_image: ImageFactory) -> None:
        blank = make_image("blank.png", kind="blank")
        assert compare_images(FIRE_AOE_REFERENCE, blank)["score"] < 0.6

    def test_a_warm_disc_beats_a_cold_disc(self, make_image: ImageFactory) -> None:
        warm = make_image("warm.png", radius=100, color=(255, 120, 25))
        cold = make_image("cold.png", radius=100, color=(25, 120, 255))
        warm_score = compare_images(FIRE_AOE_REFERENCE, warm)["score"]
        cold_score = compare_images(FIRE_AOE_REFERENCE, cold)["score"]
        assert warm_score > cold_score
