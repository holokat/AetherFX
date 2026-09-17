// The CLI as the agent sees it: a subprocess speaking newline-delimited
// JSON-RPC 2.0 on stdio (python/aetherfx/jsonrpc.py SubprocessTransport).
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>
#include "../support/example_path.hpp"

namespace {

std::filesystem::path test_output_dir() {
    std::filesystem::path dir = std::filesystem::path(AETHER_TEST_OUTPUT_DIR) / "cli";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

// <build>/test_output/integration -> <build>/bin/aetherfx
std::filesystem::path binary_path() {
    return std::filesystem::path(AETHER_TEST_OUTPUT_DIR).parent_path().parent_path() / "bin" / "aetherfx";
}

std::string quoted(const std::filesystem::path& path) { return "\"" + path.string() + "\""; }

// Runs a command and returns its stdout.
std::string run(const std::string& command) {
    std::string output;
    FILE* pipe = popen(command.c_str(), "r");
    REQUIRE(pipe != nullptr);
    char buffer[4096];
    while (std::fgets(buffer, sizeof(buffer), pipe) != nullptr) output += buffer;
    const int status = pclose(pipe);
    INFO("command: " << command);
    CHECK(status == 0);
    return output;
}

int run_status(const std::string& command) {
    FILE* pipe = popen(command.c_str(), "r");
    REQUIRE(pipe != nullptr);
    char buffer[4096];
    while (std::fgets(buffer, sizeof(buffer), pipe) != nullptr) {
    }
    const int status = pclose(pipe);
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

// Feeds newline-delimited requests to `aetherfx serve` and parses the answers.
std::vector<nlohmann::json> serve(const std::vector<nlohmann::json>& requests, const std::string& name) {
    const std::filesystem::path dir = test_output_dir();
    const std::filesystem::path input = dir / (name + "_requests.jsonl");
    {
        std::ofstream out(input);
        REQUIRE(out.good());
        for (const nlohmann::json& request : requests) out << request.dump() << "\n";
    }
    const std::string command = quoted(binary_path()) + " serve --output-dir " + quoted(dir / name) + " < " +
                                quoted(input) + " 2>/dev/null";
    const std::string output = run(command);

    std::vector<nlohmann::json> responses;
    std::istringstream lines(output);
    std::string line;
    while (std::getline(lines, line)) {
        if (line.empty()) continue;
        INFO("response line: " << line);
        responses.push_back(nlohmann::json::parse(line));
    }
    return responses;
}

}  // namespace

TEST_CASE("the CLI binary exists where the client looks for it", "[integration][cli]") {
    INFO("binary: " << binary_path().string());
    REQUIRE(std::filesystem::is_regular_file(binary_path()));
}

TEST_CASE("aetherfx serve answers JSON-RPC over stdio", "[integration][cli]") {
    const std::vector<nlohmann::json> responses = serve(
        {
            {{"jsonrpc", "2.0"}, {"id", 1}, {"method", "ping"}},
            {{"jsonrpc", "2.0"}, {"id", 2}, {"method", "tools/list"}},
            {{"jsonrpc", "2.0"},
             {"id", "create"},
             {"method", "create_effect"},
             {"params", {{"name", "CLI Fire"}, {"duration", 1.0}, {"seed", 5}}}},
            {{"jsonrpc", "2.0"},
             {"id", 4},
             {"method", "create_node"},
             {"params", {{"type", "emitter"}, {"id", "flames"}, {"parameters", {{"rate", 150.0}}}}}},
            {{"jsonrpc", "2.0"}, {"id", 5}, {"method", "inspect_graph"}},
            {{"jsonrpc", "2.0"}, {"id", 6}, {"method", "definitely_not_a_tool"}},
            {{"jsonrpc", "2.0"},
             {"id", 7},
             {"method", "set_parameter"},
             {"params", {{"node_id", "flames"}, {"name", "not_a_parameter"}, {"value", 1}}}},
            {{"jsonrpc", "2.0"}, {"id", 8}, {"method", "shutdown"}},
        },
        "rpc");

    REQUIRE(responses.size() == 8);
    for (const nlohmann::json& response : responses) CHECK(response["jsonrpc"] == "2.0");

    CHECK(responses[0]["id"] == 1);
    CHECK(responses[0]["result"]["ok"] == true);

    REQUIRE(responses[1]["result"]["tools"].is_array());
    CHECK(responses[1]["result"]["tools"].size() > 50);
    bool found_render_frame = false;
    for (const auto& tool : responses[1]["result"]["tools"]) {
        CHECK(tool["description"].get<std::string>().size() >= 20);
        CHECK(tool["input_schema"].is_object());
        if (tool["name"] == "render_frame") found_render_frame = true;
    }
    CHECK(found_render_frame);

    CHECK(responses[2]["id"] == "create");  // string ids come back verbatim
    CHECK(responses[2]["result"]["effect_id"] == "fx_1");
    CHECK(responses[2]["result"]["effect"]["name"] == "CLI Fire");

    CHECK(responses[3]["result"]["node"]["id"] == "flames");
    CHECK(responses[4]["result"]["nodes"].size() == 1);
    CHECK(responses[4]["result"]["name"] == "CLI Fire");

    CHECK(responses[5]["error"]["code"] == -32601);
    CHECK(responses[6]["error"]["code"] == -32602);
    CHECK(responses[6]["error"]["data"]["aether_code"] == "E004");
    CHECK(responses[6]["error"]["data"]["node"] == "flames");

    CHECK(responses[7]["result"]["ok"] == true);
}

TEST_CASE("aetherfx serve survives a malformed line", "[integration][cli]") {
    const std::filesystem::path dir = test_output_dir();
    const std::filesystem::path input = dir / "malformed_requests.jsonl";
    {
        std::ofstream out(input);
        out << "this is not json\n";
        out << R"({"jsonrpc":"2.0","id":2,"method":"ping"})" << "\n";
        out << R"({"jsonrpc":"2.0","id":3,"method":"shutdown"})" << "\n";
    }
    const std::string output =
        run(quoted(binary_path()) + " serve --output-dir " + quoted(dir / "malformed") + " < " + quoted(input) +
            " 2>/dev/null");
    std::istringstream lines(output);
    std::string line;
    std::vector<nlohmann::json> responses;
    while (std::getline(lines, line))
        if (!line.empty()) responses.push_back(nlohmann::json::parse(line));

    REQUIRE(responses.size() == 3);
    CHECK(responses[0]["error"]["code"] == -32700);
    CHECK(responses[1]["result"]["ok"] == true);
    CHECK(responses[2]["result"]["ok"] == true);
}

TEST_CASE("the CLI commands work on the example effects", "[integration][cli]") {
    const std::filesystem::path example =
        aether_example_file("fireball.json");
    const std::filesystem::path dir = test_output_dir();

    SECTION("tools lists the registry by category") {
        const std::string output = run(quoted(binary_path()) + " tools");
        CHECK(output.find("create_effect") != std::string::npos);
        CHECK(output.find("render_frame") != std::string::npos);
        CHECK(output.find("tools (* = mutating)") != std::string::npos);
    }
    SECTION("describe prints the vocabulary") {
        const nlohmann::json vocabulary = nlohmann::json::parse(run(quoted(binary_path()) + " describe emitter"));
        CHECK(vocabulary["node_types"].size() == 1);
        CHECK(vocabulary.contains("texture_ops"));
    }
    SECTION("validate reports a clean document and exits 0") {
        const std::string output = run(quoted(binary_path()) + " validate " + quoted(example));
        CHECK(output.find("0 errors") != std::string::npos);
    }
    SECTION("tool calls one tool on a fresh session") {
        const nlohmann::json graph = nlohmann::json::parse(
            run(quoted(binary_path()) + " tool inspect_graph {} --effect " + quoted(example) + " --output-dir " +
                quoted(dir / "tool")));
        CHECK(graph["name"] == "Fireball");
        CHECK(graph["nodes"].size() == 22);
    }
    SECTION("usage errors exit 2 and unknown effects exit 1") {
        CHECK(run_status(quoted(binary_path()) + " frobnicate > /dev/null 2>&1") == 2);
        CHECK(run_status(quoted(binary_path()) + " tool > /dev/null 2>&1") == 2);
        CHECK(run_status(quoted(binary_path()) + " validate /no/such/file.json > /dev/null 2>&1") == 1);
        CHECK(run_status(quoted(binary_path()) + " --help > /dev/null 2>&1") == 0);
        CHECK(run_status(quoted(binary_path()) + " render --help > /dev/null 2>&1") == 0);
    }
    SECTION("render writes one frame") {
        const std::filesystem::path frame = dir / "cli_frame.png";
        const std::string output = run(quoted(binary_path()) + " render " + quoted(example) + " --time 1.0 --out " +
                                       quoted(frame) + " --width 128 --height 128 --output-dir " + quoted(dir));
        CHECK(std::filesystem::is_regular_file(frame));
        CHECK(output.find("coverage") != std::string::npos);
    }
    SECTION("run renders a preview sequence") {
        const std::string output = run(quoted(binary_path()) + " run " + quoted(example) +
                                       " --fps 4 --end 0.5 --width 96 --height 96 --out " + quoted(dir / "cli_run") +
                                       " --output-dir " + quoted(dir));
        CHECK(output.find("frames: 3") != std::string::npos);
        CHECK(std::filesystem::is_regular_file(dir / "cli_run" / "frame_0000.png"));
        CHECK(std::filesystem::is_regular_file(dir / "cli_run" / "contact_sheet.png"));
    }
    SECTION("export writes a flipbook and its manifest") {
        const std::filesystem::path sheet = dir / "cli_flipbook.png";
        run(quoted(binary_path()) + " export " + quoted(example) + " --format flipbook --out " + quoted(sheet) +
            " --fps 4 --width 64 --height 64 --output-dir " + quoted(dir));
        CHECK(std::filesystem::is_regular_file(sheet));
        REQUIRE(std::filesystem::is_regular_file(sheet.string() + ".manifest.json"));
        std::ifstream in(sheet.string() + ".manifest.json");
        nlohmann::json manifest;
        in >> manifest;
        CHECK(manifest["effect"] == "Fireball");
        CHECK(manifest["frame_width"] == 64);
        CHECK(manifest["fps"] == 4.0);
    }
}
