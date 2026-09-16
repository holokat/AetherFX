#pragma once
// The non-AI evaluation baseline: the metrics behind `inspect_render` and
// `compare_reference`. Mirrors python/aetherfx/evaluate.py formula by formula
// (sRGB -> linear, Rec.709 luminance, Otsu coverage, k-means palettes,
// letterboxed 256x256 comparison) so both sides report the same numbers.
#include <filesystem>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/image.hpp"

namespace aether::tools {

inline constexpr int kCompareSize = 256;
inline constexpr int kOtsuBins = 256;
inline constexpr int kPaletteK = 5;
inline constexpr int kHistogramBins = 32;
inline constexpr int kRadialRings = 16;

struct PaletteEntry {
    Color color;      // linear RGB
    double share = 0.0;
};

// Rec. 709 luminance of every pixel of a linear image.
std::vector<double> luminance_of(const Image& image);
// Otsu's threshold over a fixed 256-bin [0,1] histogram; ties go to the lower split.
double otsu_threshold(const std::vector<double>& luminance);
// Deterministic k-means (k-means++ seeded with Pcg32(0)) over the linear pixels.
std::vector<PaletteEntry> kmeans_palette(const Image& image, int k = kPaletteK);
// Aspect-preserving fit into a size x size black canvas, resampled the way
// Pillow's BILINEAR does (in the 8-bit sRGB domain), then back to linear.
Image letterbox(const Image& image, int size = kCompareSize);

// {width, height, mean_luminance, max_luminance, coverage, coverage_threshold,
//  bbox, dominant_colors, hash}
nlohmann::json image_stats(const Image& image);
nlohmann::json image_stats_file(const std::filesystem::path& path);

// {coverage_iou, palette_distance, luminance_histogram_distance, centroid_offset,
//  radial_profile_distance, score, notes}
nlohmann::json compare_images(const Image& reference, const Image& render);
nlohmann::json compare_image_files(const std::filesystem::path& reference, const std::filesystem::path& render);

}  // namespace aether::tools
