// TEMPORARY STUB so that dependent modules link before the real compiler lands.
// The compiler agent deletes this file when it implements compiled_effect.hpp.
#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/error.hpp"

namespace aether::compiler {

std::string_view to_string(Tier t) {
    switch (t) {
        case Tier::Analytic: return "analytic";
        case Tier::Particles: return "particles";
        case Tier::Physics: return "physics";
        case Tier::Volumetric: return "volumetric";
    }
    return "analytic";
}
nlohmann::json CompiledNode::to_json() const { return {{"id", id}, {"tier", to_string(tier)}, {"backend", backend}}; }
const CompiledNode* CompiledEffect::find(std::string_view id) const {
    for (const auto& n : nodes) if (n.id == id) return &n;
    return nullptr;
}
std::vector<const CompiledNode*> CompiledEffect::of_type(NodeType t) const {
    std::vector<const CompiledNode*> out;
    for (const auto& n : nodes) if (n.type == t) out.push_back(&n);
    return out;
}
nlohmann::json CompiledEffect::plan_json() const { return {{"nodes", nlohmann::json::array()}, {"diagnostics", diagnostics.to_json()}}; }
CompiledEffect compile(const Effect& effect, const CompileOptions& options) {
    CompiledEffect c;
    c.effect = effect;
    c.fixed_dt = options.fixed_dt;
    c.diagnostics.error("not_implemented", "compiler stub: real compiler not built yet");
    return c;
}
void resolve_window(const Effect&, const Node&, double&, double&, Diagnostics*) {}

}  // namespace aether::compiler
