// docs/AGENT_API.md "render": frames, previews, turntables and image measurement.
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/render/image_io.hpp"
#include "aether/render/renderer.hpp"
#include "aether/sim/runtime.hpp"
#include "image_metrics.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

std::string frame_name(size_t index) {
    std::ostringstream out;
    out << "frame_" << std::setw(4) << std::setfill('0') << index << ".png";
    return out.str();
}

void write_frame(const std::filesystem::path& path, const Image& image, const std::string& format) {
    ensure_dir(path.parent_path());
    if (format == "exr") render::write_exr(path, image);
    else render::write_png(path, image);
}

// The renderer needs the compiled resources, so everything here goes through the
// runtime, which owns the compile the frames belong to.
sim::IRuntime& runtime_at(Session& session, Document& doc, const nlohmann::json& args, double time) {
    sim::IRuntime& runtime = session.runtime(doc, compile_options_for(doc, args));
    if (time < runtime.time()) runtime.reset();  // the runtime never steps backwards
    runtime.simulate_to(time);
    return runtime;
}

nlohmann::json render_frame(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const double time = require_number(args, "time", "render_frame");
    sim::IRuntime& runtime = runtime_at(session, doc, args, time);

    const RenderSettings settings = render_settings_for(session, args);
    const CameraDesc camera = camera_for(session, args, runtime.state().camera);
    const Image image = session.renderer().render(runtime.state(), runtime.compiled().resources, camera, settings);
    const RenderStatistics statistics = session.renderer().last_statistics();

    std::filesystem::path path;
    std::string format = arg_string(args, "format", "");
    if (has_arg(args, "path")) path = resolve_output_path(session, arg_string(args, "path"));
    if (format.empty()) format = (!path.empty() && path.extension() == ".exr") ? "exr" : "png";
    if (format != "png" && format != "exr")
        throw Error("bad_argument", "render_frame: format must be \"png\" or \"exr\" (got \"" + format + "\")");
    if (path.empty())
        path = output_path(session, slugify(doc.effect.name) + "_t" + time_tag(time) + "." + format);
    write_frame(path, image, format);

    doc.last_simulation_statistics = runtime.statistics().to_json();
    doc.last_render_statistics = statistics.to_json();
    return {{"path", path.string()},
            {"time", runtime.time()},
            {"render_statistics", doc.last_render_statistics},
            {"image_stats", image_stats_file(path)},
            {"camera", camera.to_json()},
            {"settings", settings.to_json()}};
}

nlohmann::json render_preview(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    PreviewRequest request;
    request.fps = arg_number(args, "fps", 24.0);
    request.start = arg_number(args, "start", 0.0);
    request.end = arg_number(args, "end", -1.0);

    std::filesystem::path out_dir = has_arg(args, "out_dir")
                                        ? resolve_output_path(session, arg_string(args, "out_dir"))
                                        : output_path(session, slugify(doc.effect.name) + "_preview");
    const PreviewResult sequence = render_sequence(session, doc, args, request, out_dir, true);

    nlohmann::json frames = nlohmann::json::array();
    for (const std::filesystem::path& path : sequence.paths) frames.push_back(path.string());

    nlohmann::json notes = nlohmann::json::array();
    nlohmann::json contact_sheet;
    if (arg_bool(args, "contact_sheet", true) && !sequence.images.empty())
        contact_sheet = write_contact_sheet(sequence.images, out_dir / "contact_sheet.png").string();

    nlohmann::json video;
    if (arg_bool(args, "video", false)) {
        std::string error;
        const std::filesystem::path video_path = out_dir / "preview.mp4";
        const int fps = std::max(1, static_cast<int>(std::lround(request.fps)));
        if (render::encode_video_ffmpeg(sequence.paths, video_path, fps, &error)) {
            video = video_path.string();
        } else {
            notes.push_back("video encoding is unavailable (" + (error.empty() ? std::string("ffmpeg not found") : error) +
                            "); the frames are on disk and can be encoded later");
        }
    }

    return {{"frames", std::move(frames)},
            {"contact_sheet", contact_sheet},
            {"video", video},
            {"out_dir", out_dir.string()},
            {"notes", std::move(notes)},
            {"statistics",
             {{"simulation", sequence.simulation_statistics},
              {"render", sequence.render_statistics},
              {"frame_count", sequence.paths.size()}}}};
}

nlohmann::json render_turntable(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const double time = require_number(args, "time", "render_turntable");
    const int frames = std::max(1, arg_int(args, "frames", 8));
    sim::IRuntime& runtime = runtime_at(session, doc, args, time);

    const RenderSettings settings = render_settings_for(session, args);
    const CameraDesc base = camera_for(session, args, runtime.state().camera);
    const Vec3 centre = effect_center(doc.effect, time);
    const double distance = arg_number(args, "distance", static_cast<double>(length(base.position - base.target)));
    const double height = arg_number(args, "height", static_cast<double>(base.position.y));

    const std::filesystem::path out_dir = output_path(session, slugify(doc.effect.name) + "_turntable");
    ensure_dir(out_dir);

    std::vector<Image> images;
    nlohmann::json paths = nlohmann::json::array();
    nlohmann::json last_statistics;
    for (int i = 0; i < frames; ++i) {
        const double angle = 2.0 * static_cast<double>(kPi) * i / frames;
        CameraDesc camera = base;
        camera.target = centre;
        camera.position = Vec3{centre.x + static_cast<float>(std::cos(angle) * distance), static_cast<float>(height),
                               centre.z + static_cast<float>(std::sin(angle) * distance)};
        Image image = session.renderer().render(runtime.state(), runtime.compiled().resources, camera, settings);
        last_statistics = session.renderer().last_statistics().to_json();
        const std::filesystem::path path = out_dir / frame_name(static_cast<size_t>(i));
        write_frame(path, image, "png");
        paths.push_back(path.string());
        images.push_back(std::move(image));
    }
    doc.last_render_statistics = last_statistics;

    return {{"frames", std::move(paths)},
            {"contact_sheet", write_contact_sheet(images, out_dir / "contact_sheet.png").string()},
            {"out_dir", out_dir.string()},
            {"time", runtime.time()},
            {"distance", distance},
            {"height", height},
            {"center", {centre.x, centre.y, centre.z}},
            {"render_statistics", last_statistics}};
}

nlohmann::json inspect_render(Session& session, const nlohmann::json& args) {
    const std::filesystem::path path = resolve_input_path(session, require_string(args, "path", "inspect_render"));
    std::error_code ec;
    if (!std::filesystem::is_regular_file(path, ec))
        throw Error("io", "inspect_render: no image at \"" + path.string() + "\"");
    return image_stats_file(path);
}

}  // namespace

PreviewResult render_sequence(Session& session, Document& doc, const nlohmann::json& args, const PreviewRequest& request,
                              const std::filesystem::path& out_dir, bool write_files) {
    const double fps = request.fps > 0.0 ? request.fps : 24.0;
    const double start = std::max(0.0, request.start);
    const double end = request.end >= 0.0 ? request.end : doc.effect.duration;
    if (end < start) throw Error("bad_argument", "render: \"end\" must not be before \"start\"");

    int count = static_cast<int>(std::floor((end - start) * fps + 1e-9)) + 1;
    count = std::max(1, std::min(count, request.max_frames));

    sim::IRuntime& runtime = session.runtime(doc, compile_options_for(doc, args));
    if (start < runtime.time()) runtime.reset();
    const RenderSettings settings = render_settings_for(session, args);
    if (write_files) ensure_dir(out_dir);

    PreviewResult result;
    result.images.reserve(static_cast<size_t>(count));
    for (int i = 0; i < count; ++i) {
        const double time = start + static_cast<double>(i) / fps;
        runtime.simulate_to(time);  // the sequence walks forward, reusing the state
        const CameraDesc camera = camera_for(session, args, runtime.state().camera);
        Image image = session.renderer().render(runtime.state(), runtime.compiled().resources, camera, settings);
        result.render_statistics = session.renderer().last_statistics().to_json();
        result.times.push_back(runtime.time());
        if (write_files) {
            const std::filesystem::path path = out_dir / frame_name(static_cast<size_t>(i));
            write_frame(path, image, "png");
            result.paths.push_back(path);
        }
        result.images.push_back(std::move(image));
    }
    result.simulation_statistics = runtime.statistics().to_json();
    doc.last_simulation_statistics = result.simulation_statistics;
    doc.last_render_statistics = result.render_statistics;
    return result;
}

void register_render_tools(ToolRegistry& registry) {
    const nlohmann::json camera_prop =
        prop("object", "Camera override {position:[x,y,z], target:[x,y,z], up?:[x,y,z], fov?:deg, exposure?}. "
                       "Defaults to the effect's camera node, then the session camera.");
    const nlohmann::json settings_prop =
        prop("object", "RenderSettings subset: background, ground_plane, grid, bloom, bloom_threshold, "
                       "bloom_intensity, exposure, tonemap, soft_particles, supersample, ...");

    registry.add({"render_frame",
                  "Simulate to `time` and render one frame to a file (PNG tonemapped sRGB, or EXR linear HDR). "
                  "Returns {path, time, render_statistics, image_stats} - image_stats measures coverage, luminance "
                  "and the dominant colours of what was actually drawn, so you can judge the frame without seeing it.",
                  make_schema({{"time", prop("number", "Effect time to render, in seconds.")},
                               {"width", prop("integer", "Image width in pixels (default 512).")},
                               {"height", prop("integer", "Image height in pixels (default 512).")},
                               {"camera", camera_prop},
                               {"settings", settings_prop},
                               {"path", prop("string", "Output file; absolute or relative to the output directory.")},
                               {"format", prop("string", "\"png\" (default) or \"exr\".")}},
                              {"time"}),
                  false, "render"},
                 render_frame);

    registry.add({"render_preview",
                  "Render a frame sequence over a time range at `fps` into an output directory, plus a contact sheet "
                  "that shows the whole sequence in one image. This is the tool to call to see how an effect evolves. "
                  "Returns {frames, contact_sheet, video, statistics}.",
                  make_schema({{"fps", prop("number", "Frames per second (default 24).")},
                               {"start", prop("number", "First frame time in seconds (default 0).")},
                               {"end", prop("number", "Last frame time in seconds (default: the effect duration).")},
                               {"width", prop("integer", "Frame width in pixels (default 512).")},
                               {"height", prop("integer", "Frame height in pixels (default 512).")},
                               {"camera", camera_prop},
                               {"settings", settings_prop},
                               {"contact_sheet", prop("boolean", "Also write a contact sheet (default true).")},
                               {"video", prop("boolean", "Also encode preview.mp4 with ffmpeg when available.")},
                               {"out_dir", prop("string", "Directory for the frames; defaults to "
                                                          "<output_dir>/<effect>_preview.")}}),
                  false, "render"},
                 render_preview);

    registry.add({"render_turntable",
                  "Render one moment of the effect from `frames` camera positions orbiting its centre, plus a contact "
                  "sheet. Use it to check the silhouette and the volume of an effect from every side. "
                  "Returns {frames, contact_sheet}.",
                  make_schema({{"time", prop("number", "Effect time to freeze, in seconds.")},
                               {"frames", prop("integer", "Number of orbit positions (default 8).")},
                               {"distance", prop("number", "Orbit radius in metres (default: the camera distance).")},
                               {"height", prop("number", "Camera height in metres (default: the camera height).")},
                               {"width", prop("integer", "Frame width in pixels (default 512).")},
                               {"height_px", prop("integer", "Frame height in pixels (default 512).")},
                               {"camera", camera_prop},
                               {"settings", settings_prop}},
                              {"time"}),
                  false, "render"},
                 render_turntable);

    registry.add({"inspect_render",
                  "Measure an image file: size, mean and maximum luminance, the fraction of the frame covered by lit "
                  "pixels (Otsu threshold), the coverage bounding box, five dominant colours with their shares, and a "
                  "content hash. Works on any PNG/EXR, including references.",
                  make_schema({{"path", prop("string", "Image file to measure.")}}, {"path"}), false, "render"},
                 inspect_render);
}

}  // namespace aether::tools
