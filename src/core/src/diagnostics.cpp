#include "aether/core/diagnostics.hpp"

namespace aether {

std::string_view to_string(Severity s) {
    switch (s) {
        case Severity::Info: return "info";
        case Severity::Warning: return "warning";
        case Severity::Error: return "error";
    }
    return "error";
}

nlohmann::json Diagnostic::to_json() const {
    nlohmann::json j{{"severity", to_string(severity)}, {"code", code}, {"message", message}};
    if (node) j["node"] = *node;
    if (param) j["param"] = *param;
    return j;
}

void Diagnostics::error(std::string code, std::string message, std::optional<std::string> node, std::optional<std::string> param) {
    items.push_back({Severity::Error, std::move(code), std::move(message), std::move(node), std::move(param)});
}
void Diagnostics::warning(std::string code, std::string message, std::optional<std::string> node, std::optional<std::string> param) {
    items.push_back({Severity::Warning, std::move(code), std::move(message), std::move(node), std::move(param)});
}
void Diagnostics::info(std::string code, std::string message, std::optional<std::string> node, std::optional<std::string> param) {
    items.push_back({Severity::Info, std::move(code), std::move(message), std::move(node), std::move(param)});
}
void Diagnostics::append(const Diagnostics& other) { items.insert(items.end(), other.items.begin(), other.items.end()); }
bool Diagnostics::ok() const { return error_count() == 0; }
size_t Diagnostics::error_count() const {
    size_t n = 0;
    for (const auto& d : items) if (d.severity == Severity::Error) ++n;
    return n;
}
size_t Diagnostics::warning_count() const {
    size_t n = 0;
    for (const auto& d : items) if (d.severity == Severity::Warning) ++n;
    return n;
}
nlohmann::json Diagnostics::to_json() const {
    nlohmann::json arr = nlohmann::json::array();
    for (const auto& d : items) arr.push_back(d.to_json());
    return {{"ok", ok()}, {"errors", error_count()}, {"warnings", warning_count()}, {"items", arr}};
}
std::string Diagnostics::summary() const {
    std::string s;
    for (const auto& d : items) {
        s += std::string(to_string(d.severity)) + " " + d.code;
        if (d.node) s += " [" + *d.node + (d.param ? "." + *d.param : "") + "]";
        s += ": " + d.message + "\n";
    }
    return s;
}

}  // namespace aether
