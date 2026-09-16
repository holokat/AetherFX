#pragma once
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace aether {

enum class Severity { Info, Warning, Error };
std::string_view to_string(Severity s);

struct Diagnostic {
    Severity severity = Severity::Error;
    std::string code;     // "E001".."E999", "W001".., "I001".. (docs/VOCABULARY.md)
    std::string message;  // human readable, agent readable
    std::optional<std::string> node;   // node id
    std::optional<std::string> param;  // parameter or port name
    nlohmann::json to_json() const;
};

struct Diagnostics {
    std::vector<Diagnostic> items;
    void error(std::string code, std::string message, std::optional<std::string> node = {}, std::optional<std::string> param = {});
    void warning(std::string code, std::string message, std::optional<std::string> node = {}, std::optional<std::string> param = {});
    void info(std::string code, std::string message, std::optional<std::string> node = {}, std::optional<std::string> param = {});
    void append(const Diagnostics& other);
    bool ok() const;  // no errors
    size_t error_count() const;
    size_t warning_count() const;
    nlohmann::json to_json() const;  // {"ok": bool, "errors": n, "warnings": n, "items": [...]}
    std::string summary() const;     // one line per item
};

}  // namespace aether
