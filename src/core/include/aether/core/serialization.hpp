#pragma once
#include <filesystem>
#include <string>

#include <nlohmann/json.hpp>

#include "aether/core/effect.hpp"

namespace aether {

inline constexpr const char* kSchemaVersion = "0.1.0";

// Structural conversion. Throws Error("E020"/"E003"/"E005"...) on malformed input.
// Unknown parameters are kept as ValueType::Json so validate() can report E004
// instead of silently dropping data.
Effect effect_from_json(const nlohmann::json& j);
nlohmann::json effect_to_json(const Effect& e);

// Migrates a document with an older schema_version to kSchemaVersion in place
// and returns the list of migrations applied (empty when already current).
std::vector<std::string> migrate_effect_json(nlohmann::json& j);

Effect load_effect_file(const std::filesystem::path& path);
void save_effect_file(const Effect& e, const std::filesystem::path& path, int indent = 2);

// Canonical JSON text (stable key order, fixed float formatting) used for
// snapshots, undo journal and content hashing.
std::string effect_to_canonical_string(const Effect& e);
uint64_t effect_hash(const Effect& e);

}  // namespace aether
