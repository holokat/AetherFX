// Where a test finds an effect document by file name.
//
// Library effects live in examples/effects. Documents that exist only to be tested - the first-milestone
// Fireball is the main one - live in tests/fixtures/effects, so the shipped library folder holds nothing a
// user cannot see in the studio.
#pragma once

#include <filesystem>
#include <string>

inline std::filesystem::path aether_example_file(const std::string& name) {
    const std::filesystem::path root(AETHER_SOURCE_DIR);
    const std::filesystem::path shipped = root / "examples" / "effects" / name;
    if (std::filesystem::exists(shipped)) {
        return shipped;
    }
    return root / "tests" / "fixtures" / "effects" / name;
}
