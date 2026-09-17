#pragma once
// Effect controls: named numeric knobs bound to node parameters, folded into
// the document at compile time. See docs/CONTROLS.md.
//
// A control never rewrites what the author typed: `apply_controls` works on a
// copy of the document (the compiler's own copy) and scales, offsets, sets or
// hue-rotates the bound parameters, constants and every keyframe alike. With
// every control at its identity value (multiply 1, add 0, hue_shift 0) the
// result is bit-identical to the same document without controls.
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/effect.hpp"

namespace aether {

// --- JSON -------------------------------------------------------------------

nlohmann::json control_to_json(const Control& control);
// Structural conversion only; semantic problems (unknown node, non-numeric
// parameter, value out of range) are reported by validate(). Throws
// Error("E005") when a field has the wrong JSON type.
Control control_from_json(const nlohmann::json& j);

// --- applying ---------------------------------------------------------------

// True when this binding would leave the parameter exactly as it is at `value`
// (multiply by 1, add 0, hue_shift 0). `set` is never an identity.
bool control_binding_is_identity(ControlOp op, double value);

// Folds every control into `effect` in document order and returns how many
// bindings changed a parameter. Bindings that cannot apply (unknown node,
// unknown or non-numeric parameter) are skipped: validate() is what reports
// them. Results are clamped into the parameter's spec range, so a control can
// never push a document out of the range its own validator enforces.
int apply_controls(Effect& effect);

// The value a control folds in: `value` clamped into [min, max].
double control_effective_value(const Control& control);

// --- defaults ---------------------------------------------------------------

// A sensible control set for any effect: a Global group (Intensity, Size,
// Density, Opacity, Hue) plus, per layer that has bindable nodes, Intensity,
// Size, Density and Opacity. Controls with no bindings are left out.
// Deterministic: same document in, same controls out.
std::vector<Control> generate_default_controls(const Effect& effect);

}  // namespace aether
