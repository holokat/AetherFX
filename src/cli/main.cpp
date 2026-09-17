// aetherfx: the command line face of the Agent Tool API. Every command goes
// through aether::tools::ToolRegistry, so the CLI, the JSON-RPC server, the
// Python client and the MCP server share one code path (docs/ARCHITECTURE.md 8).
//
// Usage lines live in `usage()`; exit codes are 0 success, 1 failure, 2 usage.
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/validation.hpp"
#include "aether/tools/registry.hpp"
#include "aether/tools/session.hpp"

namespace {

using aether::Error;
using aether::tools::Session;
using aether::tools::ToolRegistry;
using json = nlohmann::json;

constexpr int kOk = 0;
constexpr int kFailure = 1;
constexpr int kUsage = 2;

struct Options {
    std::vector<std::string> positional;
    std::map<std::string, std::string> values;
    std::set<std::string> flags;

    bool has(const std::string& name) const {
        return flags.count(name) != 0 || values.count(name) != 0;
    }
    std::string value(const std::string& name, const std::string& fallback = "") const {
        auto it = values.find(name);
        return it == values.end() ? fallback : it->second;
    }
    double number(const std::string& name, double fallback) const {
        auto it = values.find(name);
        if (it == values.end()) return fallback;
        try {
            return std::stod(it->second);
        } catch (const std::exception&) {
            throw Error("bad_argument", "--" + name + " expects a number, got \"" + it->second + "\"");
        }
    }
    int integer(const std::string& name, int fallback) const {
        return static_cast<int>(number(name, fallback));
    }
};

// `--name value`, `--name=value` and boolean `--name` for the names in `switches`.
Options parse_options(const std::vector<std::string>& argv, const std::set<std::string>& switches) {
    Options options;
    for (size_t i = 0; i < argv.size(); ++i) {
        const std::string& token = argv[i];
        if (token.rfind("--", 0) != 0) {
            options.positional.push_back(token);
            continue;
        }
        const std::string body = token.substr(2);
        const size_t equals = body.find('=');
        if (equals != std::string::npos) {
            options.values[body.substr(0, equals)] = body.substr(equals + 1);
            continue;
        }
        if (switches.count(body) != 0) {
            options.flags.insert(body);
            continue;
        }
        if (i + 1 >= argv.size()) throw Error("bad_argument", "--" + body + " expects a value");
        options.values[body] = argv[++i];
    }
    return options;
}

const std::set<std::string>& common_switches() {
    static const std::set<std::string> switches{"help", "video", "no-contact-sheet", "verbose", "version"};
    return switches;
}

void usage() {
    std::cout <<
        R"(aetherfx - AI-native VFX authoring engine (docs/AGENT_API.md)

Usage:
  aetherfx serve [--output-dir DIR]
  aetherfx tools [--category NAME]
  aetherfx describe [node_type]
  aetherfx validate <file.json>
  aetherfx tool <name> [json-args] [--effect FILE] [--output-dir DIR]
  aetherfx run <file.json> [--out DIR] [--fps N] [--width W] [--height H]
                           [--start S] [--end S] [--video] [--no-contact-sheet]
  aetherfx render <file.json> --time T [--out PATH] [--width W] [--height H]
  aetherfx export <file.json> --format flipbook|frames|json|package --out PATH
                              [--fps N] [--columns N] [--width W] [--height H]

  serve      newline-delimited JSON-RPC 2.0 over stdin/stdout (the agent transport)
  tools      list the tool registry by category
  describe   print the node vocabulary as JSON, optionally for one node type
  validate   validate an effect document and print its diagnostics
  tool       call one tool on a fresh session and print its JSON result
  run        load, simulate and render a preview sequence with a contact sheet
  render     render a single frame at a given time
  export     export json, a PNG frame sequence, a flipbook sprite sheet, or an
             engine-agnostic interchange package (docs/PACKAGE_FORMAT.md)

Add --help after any command for its own options. Exit codes: 0 ok, 1 failure, 2 usage.
)";
}

std::filesystem::path output_directory(const Options& options) {
    if (options.has("output-dir")) return options.value("output-dir");
    if (const char* from_env = std::getenv("AETHERFX_OUTPUT_DIR")) {
        if (from_env[0] != '\0') return std::filesystem::path(from_env);
    }
    return std::filesystem::path("out");
}

Session make_session(const Options& options) {
    std::filesystem::path dir = output_directory(options);
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    Session session(std::move(dir));
    return session;
}

void print_result(const json& result) { std::cout << result.dump(2) << "\n"; }

void print_error(const std::exception& e) {
    const json error = aether::tools::jsonrpc_error_object(e);
    std::cerr << json{{"error", error}}.dump(2) << "\n";
}

// --- commands ---------------------------------------------------------------

int command_serve(const Options& options) {
    if (options.has("help")) {
        std::cout << "aetherfx serve [--output-dir DIR]\n"
                     "  Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout: one request object per\n"
                     "  line, one response per line. Methods are tool names plus tools/list, ping and\n"
                     "  shutdown. stderr carries logs only. Honours $AETHERFX_OUTPUT_DIR.\n";
        return kOk;
    }
    Session session = make_session(options);
    const ToolRegistry& registry = ToolRegistry::standard();
    std::cerr << "aetherfx: serving " << registry.list().size() << " tools, output dir "
              << session.output_dir().string() << "\n";
    std::cerr.flush();

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        json response = aether::tools::handle_jsonrpc_line(session, registry, line);
        std::cout << response.dump() << "\n";
        std::cout.flush();
        json request = json::object();
        try {
            request = json::parse(line);
        } catch (const std::exception&) {
            continue;
        }
        if (request.is_object() && request.value("method", std::string()) == "shutdown") return kOk;
    }
    return kOk;
}

int command_tools(const Options& options) {
    if (options.has("help")) {
        std::cout << "aetherfx tools [--category NAME]\n"
                     "  Lists every tool in the registry grouped by category, with a * for the tools\n"
                     "  that mutate the document (and therefore push an undo step).\n";
        return kOk;
    }
    const std::string filter = options.value("category");
    std::string category;
    size_t shown = 0;
    for (const aether::tools::ToolSpec& spec : ToolRegistry::standard().list()) {
        if (!filter.empty() && spec.category != filter) continue;
        if (spec.category != category) {
            category = spec.category;
            std::cout << "\n" << category << "\n";
        }
        std::string summary = spec.description.substr(0, spec.description.find(". "));
        if (summary.size() > 96) summary = summary.substr(0, 93) + "...";
        std::cout << "  " << (spec.mutating ? "*" : " ") << " " << spec.name;
        for (size_t i = spec.name.size(); i < 24; ++i) std::cout << ' ';
        std::cout << summary << "\n";
        ++shown;
    }
    if (shown == 0) {
        std::cerr << "no tools in category \"" << filter << "\"\n";
        return kFailure;
    }
    std::cout << "\n" << shown << " tools (* = mutating)\n";
    return kOk;
}

int command_describe(const Options& options) {
    if (options.has("help")) {
        std::cout << "aetherfx describe [node_type]\n"
                     "  Prints the machine-readable vocabulary as JSON: node types with their\n"
                     "  parameters, ports and outputs, plus the procedural texture ops.\n";
        return kOk;
    }
    Session session = make_session(options);
    json args = json::object();
    if (!options.positional.empty()) args["node_type"] = options.positional.front();
    print_result(ToolRegistry::standard().call(session, "describe_vocabulary", args));
    return kOk;
}

int command_validate(const Options& options) {
    if (options.has("help")) {
        std::cout << "aetherfx validate <file.json>\n"
                     "  Loads an effect document and prints one line per diagnostic. Exits 1 when the\n"
                     "  document has errors.\n";
        return kOk;
    }
    if (options.positional.empty()) {
        std::cerr << "usage: aetherfx validate <file.json>\n";
        return kUsage;
    }
    const aether::Effect effect = aether::load_effect_file(options.positional.front());
    const aether::Diagnostics diagnostics = aether::validate(effect);
    std::cout << diagnostics.summary();
    std::cout << effect.name << ": " << diagnostics.error_count() << " errors, " << diagnostics.warning_count()
              << " warnings, " << effect.nodes.size() << " nodes\n";
    return diagnostics.ok() ? kOk : kFailure;
}

int command_tool(const Options& options) {
    if (options.has("help") || options.positional.empty()) {
        std::cout << "aetherfx tool <name> [json-args] [--effect FILE] [--output-dir DIR]\n"
                     "  Calls one tool on a fresh session and prints its JSON result. `json-args` is a\n"
                     "  JSON object; --effect loads an effect document first, so tools that need an\n"
                     "  active effect work in one call. Errors go to stderr as JSON and exit 1.\n";
        return options.positional.empty() && !options.has("help") ? kUsage : kOk;
    }
    const std::string name = options.positional.front();
    json args = json::object();
    if (options.positional.size() > 1) {
        try {
            args = json::parse(options.positional[1]);
        } catch (const std::exception& e) {
            std::cerr << "aetherfx tool: arguments must be a JSON object: " << e.what() << "\n";
            return kUsage;
        }
        if (!args.is_object()) {
            std::cerr << "aetherfx tool: arguments must be a JSON object\n";
            return kUsage;
        }
    }
    Session session = make_session(options);
    const ToolRegistry& registry = ToolRegistry::standard();
    if (registry.find(name) == nullptr) {
        std::cerr << "aetherfx tool: unknown tool \"" << name << "\"; run `aetherfx tools` for the list\n";
        return kUsage;
    }
    if (options.has("effect")) registry.call(session, "load_effect", json{{"path", options.value("effect")}});
    print_result(registry.call(session, name, args));
    return kOk;
}

int command_run(const Options& options) {
    if (options.has("help") || options.positional.empty()) {
        std::cout << "aetherfx run <file.json> [--out DIR] [--fps N] [--width W] [--height H]\n"
                     "                         [--start S] [--end S] [--video] [--no-contact-sheet]\n"
                     "  Loads an effect, simulates it and renders a preview sequence plus a contact\n"
                     "  sheet, then prints the frame paths and the simulation statistics.\n";
        return options.positional.empty() && !options.has("help") ? kUsage : kOk;
    }
    Session session = make_session(options);
    const ToolRegistry& registry = ToolRegistry::standard();
    registry.call(session, "load_effect", json{{"path", options.positional.front()}});

    json preview{{"fps", options.number("fps", 24.0)},
                 {"start", options.number("start", 0.0)},
                 {"contact_sheet", !options.has("no-contact-sheet")},
                 {"video", options.has("video")}};
    if (options.has("end")) preview["end"] = options.number("end", 0.0);
    if (options.has("width")) preview["width"] = options.integer("width", 512);
    if (options.has("height")) preview["height"] = options.integer("height", 512);
    if (options.has("out")) preview["out_dir"] = std::filesystem::absolute(options.value("out")).string();

    const json simulated = registry.call(session, "simulate", json::object());
    const json rendered = registry.call(session, "render_preview", preview);

    std::cout << "frames: " << rendered["frames"].size() << " in " << rendered["out_dir"].get<std::string>() << "\n";
    if (rendered["contact_sheet"].is_string())
        std::cout << "contact sheet: " << rendered["contact_sheet"].get<std::string>() << "\n";
    if (rendered["video"].is_string()) std::cout << "video: " << rendered["video"].get<std::string>() << "\n";
    for (const auto& note : rendered["notes"]) std::cout << "note: " << note.get<std::string>() << "\n";
    std::cout << "statistics: " << simulated["statistics"].dump(2) << "\n";
    return kOk;
}

int command_render(const Options& options) {
    if (options.has("help") || options.positional.empty()) {
        std::cout << "aetherfx render <file.json> --time T [--out PATH] [--width W] [--height H]\n"
                     "  Renders one frame at time T to a PNG (or EXR when --out ends in .exr) and\n"
                     "  prints the path together with the measured image statistics.\n";
        return options.positional.empty() && !options.has("help") ? kUsage : kOk;
    }
    if (!options.has("time")) {
        std::cerr << "usage: aetherfx render <file.json> --time T [--out PATH] [--width W] [--height H]\n";
        return kUsage;
    }
    Session session = make_session(options);
    const ToolRegistry& registry = ToolRegistry::standard();
    registry.call(session, "load_effect", json{{"path", options.positional.front()}});

    json args{{"time", options.number("time", 0.0)}};
    if (options.has("out")) args["path"] = std::filesystem::absolute(options.value("out")).string();
    if (options.has("width")) args["width"] = options.integer("width", 512);
    if (options.has("height")) args["height"] = options.integer("height", 512);
    const json result = registry.call(session, "render_frame", args);
    std::cout << result["path"].get<std::string>() << "\n";
    std::cout << "image: " << result["image_stats"].dump(2) << "\n";
    return kOk;
}

int command_export(const Options& options) {
    if (options.has("help") || options.positional.empty()) {
        std::cout << "aetherfx export <file.json> --format flipbook|frames|json|package --out PATH\n"
                     "                            [--fps N] [--columns N] [--width W] [--height H]\n"
                     "  Exports the effect: the document as json, a PNG frame sequence, a flipbook\n"
                     "  sprite sheet with a <path>.manifest.json describing the grid, or an\n"
                     "  engine-agnostic interchange package - a directory (--out DIR, which may end\n"
                     "  in .aetherfx) holding manifest.json, effect.json, the resolved runtime.json,\n"
                     "  baked textures as PNG, meshes as OBJ and preview images, so an Unreal, Unity\n"
                     "  or Godot importer can rebuild the effect. See docs/PACKAGE_FORMAT.md.\n"
                     "  For package, --fps sets the animated-parameter sampling rate (default 30).\n";
        return options.positional.empty() && !options.has("help") ? kUsage : kOk;
    }
    if (!options.has("format") || !options.has("out")) {
        std::cerr << "usage: aetherfx export <file.json> --format flipbook|frames|json|package --out PATH\n";
        return kUsage;
    }
    Session session = make_session(options);
    const ToolRegistry& registry = ToolRegistry::standard();
    registry.call(session, "load_effect", json{{"path", options.positional.front()}});

    json export_options = json::object();
    if (options.has("fps")) export_options["fps"] = options.number("fps", 24.0);
    if (options.has("columns")) export_options["columns"] = options.integer("columns", 0);
    if (options.has("width")) export_options["width"] = options.integer("width", 256);
    if (options.has("height")) export_options["height"] = options.integer("height", 256);

    const json result = registry.call(
        session, "export_effect",
        json{{"format", options.value("format")}, {"path", std::filesystem::absolute(options.value("out")).string()},
             {"options", export_options}});
    std::cout << result["path"].get<std::string>() << "\n";
    for (const auto& file : result["files"]) std::cout << "file: " << file.get<std::string>() << "\n";
    if (!result["manifest"].is_null()) std::cout << "manifest: " << result["manifest"].dump(2) << "\n";
    return kOk;
}

}  // namespace

int main(int argc, char** argv) {
    const std::vector<std::string> arguments(argv + 1, argv + argc);
    if (arguments.empty() || arguments.front() == "--help" || arguments.front() == "-h" ||
        arguments.front() == "help") {
        usage();
        return arguments.empty() ? kUsage : kOk;
    }
    if (arguments.front() == "--version") {
        std::cout << "aetherfx 0.1.0 (schema " << aether::kSchemaVersion << ")\n";
        return kOk;
    }

    const std::string command = arguments.front();
    try {
        const Options options =
            parse_options(std::vector<std::string>(arguments.begin() + 1, arguments.end()), common_switches());
        if (command == "serve") return command_serve(options);
        if (command == "tools") return command_tools(options);
        if (command == "describe") return command_describe(options);
        if (command == "validate") return command_validate(options);
        if (command == "tool") return command_tool(options);
        if (command == "run") return command_run(options);
        if (command == "render") return command_render(options);
        if (command == "export") return command_export(options);
        std::cerr << "aetherfx: unknown command \"" << command << "\"\n\n";
        usage();
        return kUsage;
    } catch (const std::exception& e) {
        print_error(e);
        return kFailure;
    }
}
