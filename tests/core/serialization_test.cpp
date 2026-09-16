// Document IO: structural parsing, optional-field omission, migration,
// canonical text and content hashing.
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <string>

#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"

using namespace aether;

namespace {

nlohmann::json minimal_document() {
    return nlohmann::json::parse(R"({
      "schema_version": "0.1.0",
      "name": "Minimal",
      "duration": 2.0,
      "seed": 7,
      "timeline": {"phases": [{"name": "activation", "start": 0.0, "end": 1.0}]},
      "layers": [{"id": "main", "name": "Main", "role": "primary"}],
      "nodes": [
        {"id": "ps", "type": "particle_system", "layer": "main", "parameters": {"max_particles": 100}},
        {"id": "em", "type": "emitter", "layer": "main", "parameters": {"rate": 10}, "inputs": {"particle": "ps"}}
      ],
      "metadata": {"prompt": "test"}
    })");
}

std::string error_code_of(const std::function<void()>& fn) {
    try {
        fn();
    } catch (const Error& e) {
        return e.code();
    }
    return "<no throw>";
}

}  // namespace

TEST_CASE("effect_from_json reads the full document", "[core][serialization]") {
    Effect e = effect_from_json(minimal_document());
    CHECK(e.schema_version == "0.1.0");
    CHECK(e.name == "Minimal");
    CHECK(e.duration == 2.0);
    CHECK(e.seed == 7u);
    REQUIRE(e.timeline.phases.size() == 1);
    CHECK(e.timeline.phases[0].name == "activation");
    CHECK(e.timeline.phases[0].end == 1.0);
    REQUIRE(e.layers.size() == 1);
    CHECK(e.layers[0].role == LayerRole::Primary);
    CHECK(e.layers[0].enabled);
    REQUIRE(e.nodes.size() == 2);
    CHECK(e.nodes[1].type == NodeType::Emitter);
    CHECK(e.nodes[1].layer.value() == "main");
    CHECK(param_float(e.nodes[1], "rate") == 10.0f);  // json int coerced to the spec's float
    REQUIRE(e.nodes[1].inputs_on("particle") != nullptr);
    CHECK(e.nodes[1].inputs_on("particle")->front().node == "ps");
    CHECK(e.metadata.at("prompt") == "test");
}

TEST_CASE("effect_from_json applies defaults for absent fields", "[core][serialization]") {
    Effect e = effect_from_json(nlohmann::json::parse(R"({"nodes":[{"id":"m","type":"material"}]})"));
    CHECK(e.schema_version == kSchemaVersion);
    CHECK(e.name == "untitled");
    CHECK(e.duration == 2.0);
    CHECK(e.seed == 1u);
    CHECK(e.timeline.phases.empty());
    CHECK(e.layers.empty());
    REQUIRE(e.nodes.size() == 1);
    CHECK(e.nodes[0].version == 1);
    CHECK(e.nodes[0].enabled);
    CHECK_FALSE(e.nodes[0].seed.has_value());
    CHECK_FALSE(e.nodes[0].parent.has_value());
    CHECK_FALSE(e.nodes[0].layer.has_value());
    CHECK(e.metadata.is_object());
    CHECK(e.metadata.empty());
}

TEST_CASE("unknown node types throw E003", "[core][serialization]") {
    CHECK(error_code_of([] {
              effect_from_json(nlohmann::json::parse(R"({"nodes":[{"id":"x","type":"wormhole"}]})"));
          }) == "E003");
    CHECK(error_code_of([] {
              effect_from_json(nlohmann::json::parse(R"({"nodes":[{"id":"x"}]})"));
          }) == "E003");
    CHECK(error_code_of([] {
              effect_from_json(nlohmann::json::parse(R"({"nodes":[{"id":"x","type":12}]})"));
          }) == "E003");
}

TEST_CASE("structural problems throw E005", "[core][serialization]") {
    CHECK(error_code_of([] { effect_from_json(nlohmann::json::array()); }) == "E005");
    CHECK(error_code_of([] { effect_from_json(nlohmann::json::parse(R"({"duration":"long"})")); }) == "E005");
    CHECK(error_code_of([] { effect_from_json(nlohmann::json::parse(R"({"name":3})")); }) == "E005");
    CHECK(error_code_of([] { effect_from_json(nlohmann::json::parse(R"({"nodes":{}})")); }) == "E005");
    CHECK(error_code_of([] { effect_from_json(nlohmann::json::parse(R"({"layers":[{"name":"x"}]})")); }) == "E005");
    CHECK(error_code_of([] {
              effect_from_json(nlohmann::json::parse(R"({"timeline":{"phases":[{"start":0}]}})"));
          }) == "E005");
    CHECK(error_code_of([] {
              effect_from_json(nlohmann::json::parse(R"({"nodes":[{"id":"m","type":"material","inputs":{"base_texture":3}}]})"));
          }) == "E005");
    CHECK(error_code_of([] {
              effect_from_json(nlohmann::json::parse(R"({"nodes":[{"id":"m","type":"material","parameters":[]}]})"));
          }) == "E005");
}

TEST_CASE("unknown and ill-typed parameters are kept for the validator", "[core][serialization]") {
    Effect e = effect_from_json(nlohmann::json::parse(R"({
      "nodes":[{"id":"em","type":"emitter","parameters":{"wobble": 3, "radius": "wide", "shape": "sphere"}}]
    })"));
    REQUIRE(e.nodes.size() == 1);
    const Node& n = e.nodes[0];
    // unknown parameter: stored verbatim as json rather than dropped
    REQUIRE(n.find_param("wobble") != nullptr);
    CHECK(value_type_of(n.find_param("wobble")->value) == ValueType::Json);
    // known parameter with a wrong-typed value: also kept as json so E005 can fire
    REQUIRE(n.find_param("radius") != nullptr);
    CHECK(value_type_of(n.find_param("radius")->value) == ValueType::Json);
    // a well formed parameter is typed by the spec
    CHECK(value_type_of(n.find_param("shape")->value) == ValueType::String);
    // and the data survives a round trip
    Effect again = effect_from_json(effect_to_json(e));
    CHECK(value_type_of(again.find_node("em")->find_param("wobble")->value) == ValueType::Json);
    CHECK(effect_to_canonical_string(e) == effect_to_canonical_string(again));
}

TEST_CASE("effect_to_json omits unset optional fields", "[core][serialization]") {
    Effect e;
    e.name = "compact";
    Node n;
    n.id = "m";
    n.type = NodeType::Material;
    e.add_node(n);
    nlohmann::json j = effect_to_json(e);
    for (const char* key : {"schema_version", "name", "duration", "seed", "timeline", "layers", "nodes"}) {
        CAPTURE(key);
        CHECK(j.contains(key));
    }
    CHECK_FALSE(j.contains("metadata"));
    const nlohmann::json& node = j.at("nodes")[0];
    CHECK(node.at("id") == "m");
    CHECK(node.at("type") == "material");
    CHECK_FALSE(node.contains("parent"));
    CHECK_FALSE(node.contains("layer"));
    CHECK_FALSE(node.contains("seed"));
    CHECK_FALSE(node.contains("inputs"));
    CHECK_FALSE(node.contains("metadata"));
    CHECK_FALSE(node.contains("parameters"));
    CHECK_FALSE(node.contains("version"));  // version 1 is the default
    CHECK_FALSE(node.contains("enabled"));  // enabled is the default

    // ... and writes them when they are set
    e.nodes[0].seed = 42u;
    e.nodes[0].parent = "p";
    e.nodes[0].layer = "l";
    e.nodes[0].enabled = false;
    e.nodes[0].version = 2;
    e.nodes[0].metadata = nlohmann::json{{"why", "test"}};
    e.metadata = nlohmann::json{{"prompt", "x"}};
    nlohmann::json full = effect_to_json(e);
    const nlohmann::json& written = full.at("nodes")[0];
    CHECK(written.at("seed") == 42);
    CHECK(written.at("parent") == "p");
    CHECK(written.at("layer") == "l");
    CHECK(written.at("enabled") == false);
    CHECK(written.at("version") == 2);
    CHECK(written.at("metadata").at("why") == "test");
    CHECK(full.at("metadata").at("prompt") == "x");
}

TEST_CASE("single and multi refs use the matching json shape", "[core][serialization]") {
    Effect e = effect_from_json(nlohmann::json::parse(R"({
      "nodes":[
        {"id":"g","type":"force"},{"id":"w","type":"force"},{"id":"mat","type":"material"},
        {"id":"ps","type":"particle_system","inputs":{"forces":["g","w"],"material":"mat"}}
      ]})"));
    nlohmann::json j = effect_to_json(e);
    const nlohmann::json& inputs = j.at("nodes")[3].at("inputs");
    CHECK(inputs.at("material").is_string());
    CHECK(inputs.at("forces").is_array());
    CHECK(inputs.at("forces").size() == 2);
    // a single-element multi port collapses to a string and still reads back as one ref
    Effect single = effect_from_json(nlohmann::json::parse(R"({
      "nodes":[{"id":"g","type":"force"},{"id":"ps","type":"particle_system","inputs":{"forces":["g"]}}]})"));
    CHECK(effect_to_json(single).at("nodes")[1].at("inputs").at("forces").is_string());
    Effect back = effect_from_json(effect_to_json(single));
    CHECK(back.find_node("ps")->inputs_on("forces")->size() == 1);
}

TEST_CASE("migrate_effect_json adds a missing schema_version", "[core][serialization]") {
    nlohmann::json j = nlohmann::json::parse(R"({"name":"old","nodes":[]})");
    std::vector<std::string> applied = migrate_effect_json(j);
    REQUIRE(applied.size() == 1);
    CHECK(applied[0] == "added schema_version");
    CHECK(j.at("schema_version") == kSchemaVersion);
    // running it again is a no-op
    CHECK(migrate_effect_json(j).empty());

    // a non-string schema_version is treated as missing
    nlohmann::json bad = nlohmann::json::parse(R"({"schema_version":1,"nodes":[]})");
    CHECK(migrate_effect_json(bad).size() == 1);
    CHECK(bad.at("schema_version") == kSchemaVersion);
}

TEST_CASE("migrate_effect_json refuses unsupported versions with E020", "[core][serialization]") {
    nlohmann::json newer = nlohmann::json::parse(R"({"schema_version":"0.2.0","nodes":[]})");
    CHECK(error_code_of([&] { migrate_effect_json(newer); }) == "E020");
    nlohmann::json much_newer = nlohmann::json::parse(R"({"schema_version":"1.0.0","nodes":[]})");
    CHECK(error_code_of([&] { migrate_effect_json(much_newer); }) == "E020");
    // an older version with no migration path is also E020 (the chain is empty at 0.1.0)
    nlohmann::json older = nlohmann::json::parse(R"({"schema_version":"0.0.9","nodes":[]})");
    CHECK(error_code_of([&] { migrate_effect_json(older); }) == "E020");
    // the current version passes through untouched
    nlohmann::json current = nlohmann::json::parse(R"({"schema_version":"0.1.0","nodes":[]})");
    CHECK(migrate_effect_json(current).empty());
    CHECK(error_code_of([] {
              nlohmann::json array = nlohmann::json::array();
              migrate_effect_json(array);
          }) == "E005");
}

TEST_CASE("canonical string and hash are stable and sensitive", "[core][serialization]") {
    Effect a = effect_from_json(minimal_document());
    Effect b = effect_from_json(minimal_document());
    const std::string canonical = effect_to_canonical_string(a);
    CHECK(canonical == effect_to_canonical_string(b));
    CHECK(effect_hash(a) == effect_hash(b));
    CHECK(canonical.find('\n') == std::string::npos);  // no whitespace
    CHECK(canonical.find(": ") == std::string::npos);
    // keys are sorted, so "duration" precedes "name" precedes "nodes"
    CHECK(canonical.find("\"duration\"") < canonical.find("\"name\""));
    CHECK(canonical.find("\"name\"") < canonical.find("\"nodes\""));

    b.seed = 8;
    CHECK(effect_hash(b) != effect_hash(a));
    Effect c = effect_from_json(minimal_document());
    c.nodes[0].parameters["size"] = Parameter{Value(0.2f)};
    CHECK(effect_hash(c) != effect_hash(a));
    Effect d = effect_from_json(minimal_document());
    d.name += "!";
    CHECK(effect_hash(d) != effect_hash(a));
}

TEST_CASE("save and load round trip on disk", "[core][serialization]") {
    const std::filesystem::path dir = std::filesystem::path(AETHER_TEST_OUTPUT_DIR);
    std::filesystem::create_directories(dir);
    const std::filesystem::path path = dir / "serialization_roundtrip.json";
    Effect original = effect_from_json(minimal_document());
    save_effect_file(original, path);

    std::ifstream in(path);
    REQUIRE(in.good());
    std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    CHECK(text.back() == '\n');          // trailing newline
    CHECK(text.find("\n  \"") != std::string::npos);  // indent 2

    Effect reloaded = load_effect_file(path);
    CHECK(effect_hash(reloaded) == effect_hash(original));

    // a document without schema_version is migrated on load
    const std::filesystem::path legacy = dir / "serialization_legacy.json";
    {
        std::ofstream out(legacy);
        out << R"({"name":"legacy","duration":1.0,"nodes":[{"id":"m","type":"material"}]})" << "\n";
    }
    Effect migrated = load_effect_file(legacy);
    CHECK(migrated.schema_version == kSchemaVersion);
    CHECK(migrated.name == "legacy");

    CHECK(error_code_of([&] { load_effect_file(dir / "does_not_exist.json"); }) == "io_error");
    const std::filesystem::path broken = dir / "serialization_broken.json";
    {
        std::ofstream out(broken);
        out << "{not json";
    }
    CHECK(error_code_of([&] { load_effect_file(broken); }) == "bad_json");
}

TEST_CASE("layer roles round trip and unknown roles fall back to custom", "[core][serialization]") {
    for (int i = 0; i <= static_cast<int>(LayerRole::Custom); ++i) {
        LayerRole role = static_cast<LayerRole>(i);
        CAPTURE(to_string(role));
        nlohmann::json j = nlohmann::json::parse(R"({"layers":[{"id":"l","name":"L"}],"nodes":[]})");
        j["layers"][0]["role"] = to_string(role);
        Effect e = effect_from_json(j);
        REQUIRE(e.layers.size() == 1);
        CHECK(e.layers[0].role == role);
        CHECK(effect_to_json(e).at("layers")[0].at("role") == to_string(role));
    }
    Effect odd = effect_from_json(nlohmann::json::parse(R"({"layers":[{"id":"l","role":"nonsense"}],"nodes":[]})"));
    CHECK(odd.layers[0].role == LayerRole::Custom);
    CHECK(odd.layers[0].name == "l");  // name defaults to the id
}
