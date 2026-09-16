#pragma once
#include <stdexcept>
#include <string>
#include <utility>

namespace aether {

// Thrown for invalid input at API boundaries (bad JSON, unknown node, bad
// argument). Validation *reporting* uses Diagnostics instead; do not throw
// for warnings. `code` is a stable machine-readable identifier such as
// "E004" (see docs/VOCABULARY.md) or "bad_argument".
class Error : public std::runtime_error {
public:
    Error(std::string code, const std::string& message)
        : std::runtime_error(message), code_(std::move(code)) {}
    explicit Error(const std::string& message) : Error("error", message) {}
    const std::string& code() const noexcept { return code_; }

private:
    std::string code_;
};

}  // namespace aether
