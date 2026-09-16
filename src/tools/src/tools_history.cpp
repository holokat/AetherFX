// docs/AGENT_API.md "history": the undo/redo journal of whole-document snapshots.
#include "aether/core/serialization.hpp"
#include "aether/core/validation.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

nlohmann::json undo(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const bool ok = session.undo(doc);
    return {{"ok", ok},
            {"remaining", doc.undo_stack.size()},
            {"redo_available", doc.redo_stack.size()},
            {"effect_id", doc.id},
            {"diagnostics", validate(doc.effect).to_json()}};
}

nlohmann::json redo(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const bool ok = session.redo(doc);
    return {{"ok", ok},
            {"remaining", doc.redo_stack.size()},
            {"undo_available", doc.undo_stack.size()},
            {"effect_id", doc.id},
            {"diagnostics", validate(doc.effect).to_json()}};
}

}  // namespace

void register_history_tools(ToolRegistry& registry) {
    registry.add({"undo",
                  "Undo the last mutating tool call on the active effect, restoring the whole document. `ok` is false "
                  "when there is nothing left to undo; `remaining` says how many steps are still available.",
                  make_schema({}), false, "history"},
                 undo);

    registry.add({"redo",
                  "Redo the last undone change. `ok` is false when there is nothing to redo; `remaining` says how "
                  "many redo steps are left.",
                  make_schema({}), false, "history"},
                 redo);
}

}  // namespace aether::tools
