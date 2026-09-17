#pragma once
// export_effect(format="package"): the engine-agnostic interchange package.
// Not a public header: only src/tools/src includes it. The written layout is
// specified field by field in docs/PACKAGE_FORMAT.md.
#include <filesystem>

#include <nlohmann/json.hpp>

#include "aether/tools/session.hpp"

namespace aether::tools {

// Writes <dir>/{manifest.json, effect.json, runtime.json, textures/, meshes/,
// preview/} and returns {path, files, manifest, format}. Never mutates `doc`
// beyond the caches the session owns. Options: fps (30), curve_samples (32),
// exr (false), preview (true), obj (true), plus the render arguments
// (width/height/camera/settings) used for the preview frames.
nlohmann::json export_package(Session& session, Document& doc, const nlohmann::json& options,
                              const std::filesystem::path& dir);

}  // namespace aether::tools
