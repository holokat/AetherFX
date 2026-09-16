// schema/vocabulary.json and schema/effect.schema.json are generated from the
// SpecRegistry. Run the suite once with AETHER_UPDATE_SCHEMA=1 to rewrite them.
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <fstream>
#include <string>

#include <catch2/catch_test_macros.hpp>

#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"

using namespace aether;

namespace {

std::filesystem::path schema_path(const std::string& name) {
    return std::filesystem::path(AETHER_SOURCE_DIR) / "schema" / name;
}

bool update_requested() {
    const char* env = std::getenv("AETHER_UPDATE_SCHEMA");
    return env != nullptr && std::string(env) == "1";
}

void write_json(const std::filesystem::path& path, const nlohmann::json& j) {
    std::filesystem::create_directories(path.parent_path());
    std::ofstream out(path);
    REQUIRE(out.good());
    out << j.dump(2) << "\n";
}

nlohmann::json read_json(const std::filesystem::path& path) {
    std::ifstream in(path);
    REQUIRE(in.good());
    nlohmann::json j;
    in >> j;
    return j;
}

void check_or_update(const std::string& file, const nlohmann::json& expected) {
    const std::filesystem::path path = schema_path(file);
    if (update_requested()) {
        write_json(path, expected);
        SUCCEED("rewrote " + path.string());
        return;
    }
    INFO("schema/" << file << " is out of date; re-run with AETHER_UPDATE_SCHEMA=1");
    REQUIRE(std::filesystem::exists(path));
    CHECK(read_json(path) == expected);
}

}  // namespace

TEST_CASE("schema files are up to date", "[core][schema]") {
    const SpecRegistry& registry = SpecRegistry::instance();
    check_or_update("vocabulary.json", registry.to_json());
    check_or_update("effect.schema.json", registry.effect_json_schema());
}

TEST_CASE("vocabulary json describes every node type", "[core][schema]") {
    const nlohmann::json vocabulary = SpecRegistry::instance().to_json();
    REQUIRE(vocabulary.at("schema_version") == kSchemaVersion);
    const nlohmann::json& node_types = vocabulary.at("node_types");
    REQUIRE(node_types.size() == static_cast<size_t>(kNodeTypeCount));
    for (int i = 0; i < kNodeTypeCount; ++i) {
        const std::string name(to_string(static_cast<NodeType>(i)));
        CAPTURE(name);
        REQUIRE(node_types.contains(name));
        const nlohmann::json& entry = node_types.at(name);
        for (const char* key : {"description", "version", "default_tier", "spatial", "time_bound", "parameters",
                                "inputs", "outputs"}) {
            CAPTURE(key);
            CHECK(entry.contains(key));
        }
        CHECK_FALSE(entry.at("description").get<std::string>().empty());
        CHECK_FALSE(entry.at("outputs").empty());
        for (const auto& [param_name, param] : entry.at("parameters").items()) {
            CAPTURE(param_name);
            for (const char* key : {"type", "default", "min", "max", "enum", "animatable", "description", "units"}) {
                CAPTURE(key);
                CHECK(param.contains(key));
            }
            ValueType parsed{};
            CHECK(parse_value_type(param.at("type").get<std::string>(), parsed));
            CHECK_FALSE(param.at("description").get<std::string>().empty());
        }
        for (const auto& [port_name, port] : entry.at("inputs").items()) {
            CAPTURE(port_name);
            for (const char* key : {"accepts", "multi", "required", "description"}) {
                CAPTURE(key);
                CHECK(port.contains(key));
            }
        }
    }
    CHECK(vocabulary.at("value_types").size() == 15);
    CHECK(vocabulary.at("layer_roles").size() == 7);
    CHECK(vocabulary.at("blend_modes").size() == 3);
    CHECK(vocabulary.at("render_modes").size() == 5);
    CHECK(vocabulary.at("validation_codes").size() == 25);
    for (const char* code : {"E001", "E010", "E020", "W001", "W005"}) {
        CAPTURE(code);
        CHECK(vocabulary.at("validation_codes").contains(code));
    }
}

TEST_CASE("effect json schema is well formed and covers every node type", "[core][schema]") {
    const nlohmann::json schema = SpecRegistry::instance().effect_json_schema();
    // Well formed: dumping and reparsing must be lossless.
    CHECK(nlohmann::json::parse(schema.dump()) == schema);
    CHECK(schema.at("$schema") == "https://json-schema.org/draft/2020-12/schema");
    CHECK(schema.at("type") == "object");
    REQUIRE(schema.contains("$defs"));
    const nlohmann::json& defs = schema.at("$defs");

    for (int i = 0; i < kNodeTypeCount; ++i) {
        const std::string name(to_string(static_cast<NodeType>(i)));
        CAPTURE(name);
        const std::string key = "node_" + name;
        REQUIRE(defs.contains(key));
        const nlohmann::json& node = defs.at(key);
        CHECK(node.at("properties").at("type").at("const") == name);
        CHECK(node.at("properties").contains("parameters"));
        CHECK(node.at("properties").contains("inputs"));
        // Every parameter of the type has a property entry.
        const NodeSpec& spec = SpecRegistry::instance().get(static_cast<NodeType>(i));
        const nlohmann::json& params = node.at("properties").at("parameters").at("properties");
        for (const ParamSpec& p : spec.params) {
            CAPTURE(p.name);
            CHECK(params.contains(p.name));
        }
        for (const PortSpec& p : spec.inputs) {
            CAPTURE(p.name);
            CHECK(node.at("properties").at("inputs").at("properties").contains(p.name));
        }
    }
    REQUIRE(defs.contains("node"));
    CHECK(defs.at("node").at("oneOf").size() == static_cast<size_t>(kNodeTypeCount));

    // Every $ref resolves inside $defs.
    std::function<void(const nlohmann::json&)> walk = [&](const nlohmann::json& j) {
        if (j.is_object()) {
            auto ref = j.find("$ref");
            if (ref != j.end() && ref->is_string()) {
                const std::string target = ref->get<std::string>();
                REQUIRE(target.rfind("#/$defs/", 0) == 0);
                const std::string key = target.substr(std::string("#/$defs/").size());
                CAPTURE(key);
                CHECK(defs.contains(key));
            }
            for (const auto& [k, v] : j.items()) { (void)k; walk(v); }
        } else if (j.is_array()) {
            for (const auto& v : j) walk(v);
        }
    };
    walk(schema);
}

TEST_CASE("schema files on disk parse and match the registry shape", "[core][schema]") {
    if (update_requested()) {
        SUCCEED("schema files are being regenerated in this run");
        return;
    }
    const nlohmann::json vocabulary = read_json(schema_path("vocabulary.json"));
    CHECK(vocabulary.at("node_types").size() == static_cast<size_t>(kNodeTypeCount));
    const nlohmann::json schema = read_json(schema_path("effect.schema.json"));
    for (int i = 0; i < kNodeTypeCount; ++i) {
        const std::string name(to_string(static_cast<NodeType>(i)));
        CAPTURE(name);
        CHECK(schema.at("$defs").contains("node_" + name));
    }
}
