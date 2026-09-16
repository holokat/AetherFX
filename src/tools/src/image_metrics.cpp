#include "image_metrics.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <numeric>
#include <sstream>
#include <string>

#include "aether/core/rng.hpp"
#include "aether/render/image_io.hpp"

namespace aether::tools {
namespace {

constexpr int kPaletteMaxIterations = 32;   // evaluate.py PALETTE_MAX_ITERATIONS
constexpr double kPaletteTolerance = 1e-6;  // evaluate.py PALETTE_TOLERANCE
constexpr size_t kMaxPaletteSamples = 20000;

double luminance_of(Color c) { return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b; }

// --- Pillow-compatible resampling -------------------------------------------
// Pillow resamples in the 8-bit sRGB domain with a triangle filter whose support
// grows with the reduction factor (that is what makes its downscale antialiased).
// The comparison metrics are defined on its output, so reproduce it here.

std::vector<uint8_t> to_srgb_bytes(const Image& image) {
    std::vector<uint8_t> out(static_cast<size_t>(image.width) * image.height * 3);
    for (int y = 0; y < image.height; ++y) {
        for (int x = 0; x < image.width; ++x) {
            const Color c = image.get(x, y);
            const size_t index = (static_cast<size_t>(y) * image.width + x) * 3;
            out[index + 0] = static_cast<uint8_t>(std::lround(linear_to_srgb(c.r) * 255.0f));
            out[index + 1] = static_cast<uint8_t>(std::lround(linear_to_srgb(c.g) * 255.0f));
            out[index + 2] = static_cast<uint8_t>(std::lround(linear_to_srgb(c.b) * 255.0f));
        }
    }
    return out;
}

uint8_t clip_round(double v) {
    const long rounded = std::lround(v);
    return static_cast<uint8_t>(rounded < 0 ? 0 : (rounded > 255 ? 255 : rounded));
}

// One separable pass of Pillow's ImagingResample with the BILINEAR filter.
std::vector<uint8_t> resample_axis(const std::vector<uint8_t>& src, int width, int height, int out_size,
                                   bool horizontal) {
    const int in_size = horizontal ? width : height;
    const int other = horizontal ? height : width;
    const int out_width = horizontal ? out_size : width;
    std::vector<uint8_t> out(static_cast<size_t>(out_size) * other * 3);

    const double scale = static_cast<double>(in_size) / out_size;
    const double filter_scale = std::max(1.0, scale);
    const double support = 1.0 * filter_scale;  // bilinear support is 1

    std::vector<double> weights;
    for (int o = 0; o < out_size; ++o) {
        const double center = (o + 0.5) * scale;
        int begin = static_cast<int>(center - support + 0.5);
        int end = static_cast<int>(center + support + 0.5);
        begin = std::max(begin, 0);
        end = std::min(end, in_size);
        weights.assign(static_cast<size_t>(std::max(0, end - begin)), 0.0);
        double total = 0.0;
        for (int i = begin; i < end; ++i) {
            const double x = std::fabs((i - center + 0.5) / filter_scale);
            const double w = x < 1.0 ? 1.0 - x : 0.0;
            weights[static_cast<size_t>(i - begin)] = w;
            total += w;
        }
        if (total != 0.0)
            for (double& w : weights) w /= total;

        for (int o2 = 0; o2 < other; ++o2) {
            double sums[3]{0.0, 0.0, 0.0};
            for (int i = begin; i < end; ++i) {
                const int sx = horizontal ? i : o2;
                const int sy = horizontal ? o2 : i;
                const size_t index = (static_cast<size_t>(sy) * width + sx) * 3;
                const double w = weights[static_cast<size_t>(i - begin)];
                for (int c = 0; c < 3; ++c) sums[c] += w * src[index + static_cast<size_t>(c)];
            }
            const int dx = horizontal ? o : o2;
            const int dy = horizontal ? o2 : o;
            const size_t index = (static_cast<size_t>(dy) * out_width + dx) * 3;
            for (int c = 0; c < 3; ++c) out[index + static_cast<size_t>(c)] = clip_round(sums[c]);
        }
    }
    return out;
}

double clamp01(double v) { return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v); }

std::vector<double> normalized_histogram(const std::vector<double>& values, int bins) {
    std::vector<double> histogram(static_cast<size_t>(bins), 0.0);
    for (double v : values) {
        int bin = static_cast<int>(clamp01(v) * bins);
        if (bin >= bins) bin = bins - 1;
        histogram[static_cast<size_t>(bin)] += 1.0;
    }
    const double total = std::accumulate(histogram.begin(), histogram.end(), 0.0);
    if (total <= 0.0) return std::vector<double>(static_cast<size_t>(bins), 1.0 / bins);
    for (double& count : histogram) count /= total;
    return histogram;
}

struct Mask {
    std::vector<uint8_t> bits;
    size_t count = 0;
    int width = 0;
    int height = 0;
    bool any() const { return count > 0; }
};

Mask coverage_mask(const std::vector<double>& luminance, int width, int height, double threshold) {
    Mask mask;
    mask.width = width;
    mask.height = height;
    mask.bits.resize(luminance.size(), 0);
    for (size_t i = 0; i < luminance.size(); ++i) {
        if (luminance[i] <= threshold) continue;
        mask.bits[i] = 1;
        ++mask.count;
    }
    return mask;
}

// Pixel-space centroid using pixel centres; {-1,-1} when the mask is empty.
Vec2 mask_centroid(const Mask& mask) {
    if (!mask.any()) return {-1.0f, -1.0f};
    double sum_x = 0.0;
    double sum_y = 0.0;
    for (int y = 0; y < mask.height; ++y) {
        for (int x = 0; x < mask.width; ++x) {
            if (mask.bits[static_cast<size_t>(y) * mask.width + x] == 0) continue;
            sum_x += x;
            sum_y += y;
        }
    }
    const double n = static_cast<double>(mask.count);
    return {static_cast<float>(sum_x / n + 0.5), static_cast<float>(sum_y / n + 0.5)};
}

std::vector<double> radial_profile(const std::vector<double>& luminance, int width, int height, Vec2 centre,
                                   int rings) {
    const double max_radius = std::hypot(static_cast<double>(width), static_cast<double>(height)) / 2.0;
    std::vector<double> energy(static_cast<size_t>(rings), 0.0);
    if (max_radius <= 0.0) return std::vector<double>(static_cast<size_t>(rings), 1.0 / rings);
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const double dx = (x + 0.5) - centre.x;
            const double dy = (y + 0.5) - centre.y;
            const double radius = std::sqrt(dx * dx + dy * dy);
            int ring = static_cast<int>(radius / max_radius * rings);
            if (ring >= rings) ring = rings - 1;
            energy[static_cast<size_t>(ring)] += std::max(0.0, luminance[static_cast<size_t>(y) * width + x]);
        }
    }
    const double total = std::accumulate(energy.begin(), energy.end(), 0.0);
    if (total <= 0.0) return std::vector<double>(static_cast<size_t>(rings), 1.0 / rings);
    for (double& e : energy) e /= total;
    return energy;
}

double l1_over_two(const std::vector<double>& a, const std::vector<double>& b) {
    double sum = 0.0;
    for (size_t i = 0; i < a.size() && i < b.size(); ++i) sum += std::fabs(a[i] - b[i]);
    return 0.5 * sum;
}

double palette_distance(const std::vector<PaletteEntry>& left, const std::vector<PaletteEntry>& right) {
    if (left.empty() || right.empty()) return 0.0;
    const size_t rows = left.size();
    const size_t columns = right.size();
    std::vector<double> distances(rows * columns, 0.0);
    for (size_t i = 0; i < rows; ++i) {
        for (size_t j = 0; j < columns; ++j) {
            const double dr = static_cast<double>(left[i].color.r) - right[j].color.r;
            const double dg = static_cast<double>(left[i].color.g) - right[j].color.g;
            const double db = static_cast<double>(left[i].color.b) - right[j].color.b;
            distances[i * columns + j] = clamp01(std::sqrt(dr * dr + dg * dg + db * db) / std::sqrt(3.0));
        }
    }
    std::vector<double> remaining_left(rows);
    std::vector<double> remaining_right(columns);
    for (size_t i = 0; i < rows; ++i) remaining_left[i] = left[i].share;
    for (size_t j = 0; j < columns; ++j) remaining_right[j] = right[j].share;

    constexpr double kEpsilonShare = 1e-12;
    double total = 0.0;
    double moved = 0.0;
    while (true) {
        size_t best_i = rows;
        size_t best_j = columns;
        double best = 0.0;
        for (size_t i = 0; i < rows; ++i) {
            if (remaining_left[i] <= kEpsilonShare) continue;
            for (size_t j = 0; j < columns; ++j) {
                if (remaining_right[j] <= kEpsilonShare) continue;
                const double d = distances[i * columns + j];
                if (best_i != rows && d >= best) continue;  // first minimum wins, lowest (i, j)
                best = d;
                best_i = i;
                best_j = j;
            }
        }
        if (best_i == rows) break;
        const double weight = std::min(remaining_left[best_i], remaining_right[best_j]);
        total += weight * best;
        moved += weight;
        remaining_left[best_i] -= weight;
        remaining_right[best_j] -= weight;
    }
    return moved > kEpsilonShare ? total / moved : 0.0;
}

std::string hash_hex(uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << value;
    return out.str();
}

nlohmann::json palette_json(const std::vector<PaletteEntry>& palette) {
    nlohmann::json colors = nlohmann::json::array();
    for (const PaletteEntry& entry : palette)
        colors.push_back({{"rgb", {entry.color.r, entry.color.g, entry.color.b}}, {"share", entry.share}});
    return colors;
}

// Mean (red - blue) of a palette: a cheap "warmth" measure for the notes.
double palette_warmth(const std::vector<PaletteEntry>& palette) {
    double warmth = 0.0;
    double weight = 0.0;
    for (const PaletteEntry& entry : palette) {
        warmth += entry.share * (static_cast<double>(entry.color.r) - entry.color.b);
        weight += entry.share;
    }
    return weight > 0.0 ? warmth / weight : 0.0;
}

std::string percent(double fraction) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(0) << fraction * 100.0 << "%";
    return out.str();
}

}  // namespace

std::vector<double> luminance_of(const Image& image) {
    std::vector<double> out(image.pixel_count(), 0.0);
    for (size_t i = 0; i < out.size(); ++i) {
        const float* p = &image.rgba[i * 4];
        out[i] = 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
    }
    return out;
}

double otsu_threshold(const std::vector<double>& luminance) {
    if (luminance.empty()) return 1.0 / kOtsuBins;
    std::vector<double> counts(kOtsuBins, 0.0);
    for (double value : luminance) {
        int bin = static_cast<int>(clamp01(value) * kOtsuBins);
        if (bin >= kOtsuBins) bin = kOtsuBins - 1;
        counts[static_cast<size_t>(bin)] += 1.0;
    }
    const double total = static_cast<double>(luminance.size());
    double total_sum = 0.0;
    for (int i = 0; i < kOtsuBins; ++i) total_sum += counts[static_cast<size_t>(i)] * ((i + 0.5) / kOtsuBins);

    double weight_sum = 0.0;
    double sum0 = 0.0;
    double best_variance = -1.0;
    int best_split = 0;
    for (int k = 0; k < kOtsuBins - 1; ++k) {
        weight_sum += counts[static_cast<size_t>(k)];
        sum0 += counts[static_cast<size_t>(k)] * ((k + 0.5) / kOtsuBins);
        const double w0 = weight_sum / total;
        const double w1 = 1.0 - w0;
        const double mean0 = w0 > 0.0 ? sum0 / (w0 * total) : 0.0;
        const double mean1 = w1 > 0.0 ? (total_sum - sum0) / (w1 * total) : 0.0;
        const double variance = w0 * w1 * (mean0 - mean1) * (mean0 - mean1);
        if (variance > best_variance) {  // strict >: the smallest maximising k wins
            best_variance = variance;
            best_split = k;
        }
    }
    return (best_split + 1.0) / kOtsuBins;
}

std::vector<PaletteEntry> kmeans_palette(const Image& image, int k) {
    std::vector<Vec3> pixels;
    const size_t count = image.pixel_count();
    if (count == 0) return std::vector<PaletteEntry>(static_cast<size_t>(k), PaletteEntry{Color::black(), 0.0});
    const size_t stride = count <= kMaxPaletteSamples ? 1 : (count + kMaxPaletteSamples - 1) / kMaxPaletteSamples;
    pixels.reserve(count / stride + 1);
    for (size_t i = 0; i < count; i += stride) {
        const float* p = &image.rgba[i * 4];
        pixels.push_back(Vec3{p[0], p[1], p[2]});
    }
    while (pixels.size() < static_cast<size_t>(k)) pixels.push_back(pixels.front());

    // k-means++ seeding, deterministic: the same image always gives the same palette.
    Pcg32 rng(0);
    std::vector<Vec3> centres(static_cast<size_t>(k));
    centres[0] = pixels[rng.next_u32() % pixels.size()];
    std::vector<double> closest(pixels.size());
    for (size_t i = 0; i < pixels.size(); ++i) closest[i] = length_squared(pixels[i] - centres[0]);
    for (int index = 1; index < k; ++index) {
        const double total = std::accumulate(closest.begin(), closest.end(), 0.0);
        size_t pick = 0;
        if (total <= 0.0) {
            pick = rng.next_u32() % pixels.size();
        } else {
            const double target = static_cast<double>(rng.next_float()) * total;
            double cumulative = 0.0;
            for (size_t i = 0; i < pixels.size(); ++i) {
                cumulative += closest[i];
                if (cumulative >= target) {
                    pick = i;
                    break;
                }
                pick = i;
            }
        }
        centres[static_cast<size_t>(index)] = pixels[pick];
        for (size_t i = 0; i < pixels.size(); ++i)
            closest[i] = std::min(closest[i], static_cast<double>(length_squared(pixels[i] - centres[static_cast<size_t>(index)])));
    }

    std::vector<int> labels(pixels.size(), 0);
    for (int iteration = 0; iteration < kPaletteMaxIterations; ++iteration) {
        for (size_t i = 0; i < pixels.size(); ++i) {
            double best = std::numeric_limits<double>::max();
            int best_index = 0;
            for (int c = 0; c < k; ++c) {
                const double d = length_squared(pixels[i] - centres[static_cast<size_t>(c)]);
                if (d >= best) continue;
                best = d;
                best_index = c;
            }
            labels[i] = best_index;
        }
        double moved = 0.0;
        for (int c = 0; c < k; ++c) {
            Vec3 sum{0, 0, 0};
            size_t members = 0;
            for (size_t i = 0; i < pixels.size(); ++i) {
                if (labels[i] != c) continue;
                sum += pixels[i];
                ++members;
            }
            if (members == 0) continue;
            const Vec3 centre = sum / static_cast<float>(members);
            const Vec3 delta = vabs(centre - centres[static_cast<size_t>(c)]);
            moved = std::max(moved, static_cast<double>(std::max(delta.x, std::max(delta.y, delta.z))));
            centres[static_cast<size_t>(c)] = centre;
        }
        if (moved <= kPaletteTolerance) break;
    }

    std::vector<PaletteEntry> palette(static_cast<size_t>(k));
    for (int c = 0; c < k; ++c) {
        const size_t members = static_cast<size_t>(std::count(labels.begin(), labels.end(), c));
        palette[static_cast<size_t>(c)].color =
            Color{centres[static_cast<size_t>(c)].x, centres[static_cast<size_t>(c)].y, centres[static_cast<size_t>(c)].z, 1.0f};
        palette[static_cast<size_t>(c)].share = static_cast<double>(members) / static_cast<double>(pixels.size());
    }
    std::sort(palette.begin(), palette.end(), [](const PaletteEntry& a, const PaletteEntry& b) {
        if (a.share != b.share) return a.share > b.share;
        const double la = luminance_of(a.color);
        const double lb = luminance_of(b.color);
        if (la != lb) return la > lb;
        if (a.color.r != b.color.r) return a.color.r < b.color.r;
        if (a.color.g != b.color.g) return a.color.g < b.color.g;
        return a.color.b < b.color.b;
    });
    return palette;
}

Image letterbox(const Image& image, int size) {
    Image canvas(size, size, Color::black());
    if (image.empty()) return canvas;
    const double scale = static_cast<double>(size) / std::max(image.width, image.height);
    const int new_width = std::max(1, static_cast<int>(std::lround(image.width * scale)));
    const int new_height = std::max(1, static_cast<int>(std::lround(image.height * scale)));

    std::vector<uint8_t> bytes = to_srgb_bytes(image);
    if (new_width != image.width) bytes = resample_axis(bytes, image.width, image.height, new_width, true);
    if (new_height != image.height) bytes = resample_axis(bytes, new_width, image.height, new_height, false);

    const int offset_x = (size - new_width) / 2;
    const int offset_y = (size - new_height) / 2;
    for (int y = 0; y < new_height; ++y) {
        for (int x = 0; x < new_width; ++x) {
            const size_t index = (static_cast<size_t>(y) * new_width + x) * 3;
            canvas.set(offset_x + x, offset_y + y,
                       Color{srgb_to_linear(bytes[index + 0] / 255.0f), srgb_to_linear(bytes[index + 1] / 255.0f),
                             srgb_to_linear(bytes[index + 2] / 255.0f), 1.0f});
        }
    }
    return canvas;
}

nlohmann::json image_stats(const Image& image) {
    const std::vector<double> luminance = luminance_of(image);
    const double threshold = otsu_threshold(luminance);
    const Mask mask = coverage_mask(luminance, image.width, image.height, threshold);

    double mean = 0.0;
    double maximum = 0.0;
    for (double value : luminance) {
        mean += value;
        maximum = std::max(maximum, value);
    }
    mean = luminance.empty() ? 0.0 : mean / static_cast<double>(luminance.size());

    nlohmann::json bbox = {0.0, 0.0, 0.0, 0.0};
    if (mask.any()) {
        int x0 = image.width;
        int y0 = image.height;
        int x1 = -1;
        int y1 = -1;
        for (int y = 0; y < image.height; ++y) {
            for (int x = 0; x < image.width; ++x) {
                if (mask.bits[static_cast<size_t>(y) * image.width + x] == 0) continue;
                x0 = std::min(x0, x);
                y0 = std::min(y0, y);
                x1 = std::max(x1, x);
                y1 = std::max(y1, y);
            }
        }
        bbox = {static_cast<double>(x0) / image.width, static_cast<double>(y0) / image.height,
                static_cast<double>(x1 + 1) / image.width, static_cast<double>(y1 + 1) / image.height};
    }

    return {{"width", image.width},
            {"height", image.height},
            {"mean_luminance", mean},
            {"max_luminance", maximum},
            {"coverage", luminance.empty() ? 0.0 : static_cast<double>(mask.count) / static_cast<double>(luminance.size())},
            {"coverage_threshold", threshold},
            {"bbox", bbox},
            {"dominant_colors", palette_json(kmeans_palette(image))},
            {"hash", hash_hex(image.hash())}};
}

nlohmann::json image_stats_file(const std::filesystem::path& path) {
    nlohmann::json stats = image_stats(render::read_image(path));
    stats["path"] = path.string();
    return stats;
}

nlohmann::json compare_images(const Image& reference_image, const Image& render_image) {
    const Image reference_fit = letterbox(reference_image, kCompareSize);
    const Image render_fit = letterbox(render_image, kCompareSize);

    const std::vector<double> reference_luminance = luminance_of(reference_fit);
    const std::vector<double> render_luminance = luminance_of(render_fit);
    const Mask reference_mask =
        coverage_mask(reference_luminance, kCompareSize, kCompareSize, otsu_threshold(reference_luminance));
    const Mask render_mask = coverage_mask(render_luminance, kCompareSize, kCompareSize, otsu_threshold(render_luminance));

    size_t intersection = 0;
    size_t reunion = 0;
    for (size_t i = 0; i < reference_mask.bits.size(); ++i) {
        const bool a = reference_mask.bits[i] != 0;
        const bool b = render_mask.bits[i] != 0;
        intersection += (a && b) ? 1 : 0;
        reunion += (a || b) ? 1 : 0;
    }
    const double coverage_iou = reunion == 0 ? 1.0 : static_cast<double>(intersection) / static_cast<double>(reunion);

    const std::vector<PaletteEntry> reference_palette = kmeans_palette(reference_fit);
    const std::vector<PaletteEntry> render_palette = kmeans_palette(render_fit);
    const double palette = palette_distance(reference_palette, render_palette);

    const double histogram = l1_over_two(normalized_histogram(reference_luminance, kHistogramBins),
                                         normalized_histogram(render_luminance, kHistogramBins));

    const Vec2 reference_centroid = mask_centroid(reference_mask);
    const Vec2 render_centroid = mask_centroid(render_mask);
    const double diagonal = std::hypot(static_cast<double>(kCompareSize), static_cast<double>(kCompareSize));
    double centroid_offset = 0.0;
    if (reference_mask.any() != render_mask.any()) {
        centroid_offset = 1.0;
    } else if (reference_mask.any() && render_mask.any()) {
        centroid_offset = std::min(1.0, std::hypot(static_cast<double>(reference_centroid.x - render_centroid.x),
                                                   static_cast<double>(reference_centroid.y - render_centroid.y)) /
                                            diagonal);
    }

    const Vec2 centre{kCompareSize / 2.0f, kCompareSize / 2.0f};
    const double radial = l1_over_two(
        radial_profile(reference_luminance, kCompareSize, kCompareSize,
                       reference_mask.any() ? reference_centroid : centre, kRadialRings),
        radial_profile(render_luminance, kCompareSize, kCompareSize, render_mask.any() ? render_centroid : centre,
                       kRadialRings));

    const double penalty = 0.30 * (1.0 - coverage_iou) + 0.25 * palette + 0.15 * histogram + 0.15 * centroid_offset +
                           0.15 * radial;
    const double score = std::max(0.0, std::min(1.0, 1.0 - penalty));

    const double reference_coverage =
        static_cast<double>(reference_mask.count) / static_cast<double>(reference_mask.bits.size());
    const double render_coverage = static_cast<double>(render_mask.count) / static_cast<double>(render_mask.bits.size());

    nlohmann::json notes = nlohmann::json::array();
    if (reunion == 0)
        notes.push_back("both images are below the Otsu coverage threshold; the comparison is uninformative");
    if (reference_mask.any() && !render_mask.any())
        notes.push_back("the render has no lit coverage while the reference does: raise emissive, rate or size");
    if (!reference_mask.any() && render_mask.any())
        notes.push_back("the render has lit coverage while the reference does not");
    if (reunion > 0 && std::fabs(render_coverage - reference_coverage) > 0.05) {
        notes.push_back("render covers " + percent(render_coverage) + " vs reference " + percent(reference_coverage) +
                        (render_coverage < reference_coverage ? ": increase size/rate or the emitter radius"
                                                              : ": reduce size/rate or the emitter radius"));
    }
    if (coverage_iou < 0.4 && reunion > 0)
        notes.push_back("coverage silhouettes differ substantially; check emitter shape, radius and scale");
    if (palette > 0.3)
        notes.push_back("palette differs substantially; check colour, color_over_life and emissive intensity");
    const double warmth = palette_warmth(render_palette) - palette_warmth(reference_palette);
    if (std::fabs(warmth) > 0.05)
        notes.push_back(warmth > 0.0 ? "palette warmer than reference; cool the colour down towards yellow/white"
                                     : "palette cooler than reference; push the colour towards orange/red");
    if (centroid_offset > 0.1)
        notes.push_back("the effect sits in a different place on screen; check camera, position and emitter centre");
    if (radial > 0.3)
        notes.push_back("energy is distributed differently from the centre outwards; check radius and falloff");

    return {{"coverage_iou", coverage_iou},
            {"palette_distance", palette},
            {"luminance_histogram_distance", histogram},
            {"centroid_offset", centroid_offset},
            {"radial_profile_distance", radial},
            {"score", score},
            {"reference_coverage", reference_coverage},
            {"render_coverage", render_coverage},
            {"reference_palette", palette_json(reference_palette)},
            {"render_palette", palette_json(render_palette)},
            {"notes", std::move(notes)}};
}

nlohmann::json compare_image_files(const std::filesystem::path& reference_path,
                                   const std::filesystem::path& render_path) {
    return compare_images(render::read_image(reference_path), render::read_image(render_path));
}

}  // namespace aether::tools
