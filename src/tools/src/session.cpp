// Session: documents, undo/redo journal, compiled/runtime/renderer caches.
// See docs/ARCHITECTURE.md section 8.
#include "aether/tools/session.hpp"

#include <algorithm>
#include <utility>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"

namespace aether::tools {
namespace {

// Drops the runtime before the CompiledEffect it points at can go away.
void invalidate_caches(Document& doc) {
    doc.runtime.reset();
    doc.runtime_hash = 0;
    doc.compiled.reset();
    doc.compiled_hash = 0;
}

}  // namespace

Session::Session(std::filesystem::path output_dir) : output_dir_(std::move(output_dir)) {}

Document& Session::create(Effect effect) {
    const std::string id = "fx_" + std::to_string(next_id_++);
    Document& doc = documents_[id];
    doc.id = id;
    doc.effect = std::move(effect);
    order_.push_back(id);
    active_id_ = id;
    return doc;
}

Document& Session::open(const std::filesystem::path& path) {
    Effect effect = load_effect_file(path);
    Document& doc = create(std::move(effect));
    doc.path = path;
    doc.dirty = false;
    return doc;
}

Document* Session::find(const std::string& id) {
    auto it = documents_.find(id);
    return it == documents_.end() ? nullptr : &it->second;
}

Document& Session::get(const std::string& id) {
    Document* doc = find(id);
    if (doc == nullptr) {
        std::string known;
        for (const std::string& other : order_) known += (known.empty() ? "" : ", ") + other;
        throw Error("no_such_effect", "no effect \"" + id + "\" in this session" +
                                          (known.empty() ? "; none is loaded" : "; known effects: " + known));
    }
    return *doc;
}

Document& Session::active() {
    if (active_id_.empty() || find(active_id_) == nullptr)
        throw Error("no_active_effect", "no active effect; call create_effect or load_effect first");
    return *find(active_id_);
}

Document& Session::document_for(const nlohmann::json& args) {
    if (args.is_object()) {
        auto it = args.find("effect_id");
        if (it != args.end() && it->is_string()) return get(it->get<std::string>());
    }
    return active();
}

void Session::set_active(const std::string& id) {
    get(id);  // throws when unknown
    active_id_ = id;
}

bool Session::close(const std::string& id) {
    auto it = documents_.find(id);
    if (it == documents_.end()) return false;
    documents_.erase(it);
    order_.erase(std::remove(order_.begin(), order_.end(), id), order_.end());
    if (active_id_ == id) active_id_ = order_.empty() ? std::string() : order_.back();
    return true;
}

std::vector<std::string> Session::ids() const { return order_; }

void Session::begin_mutation(Document& doc) {
    doc.undo_stack.push_back(effect_to_json(doc.effect));
    if (max_undo > 0 && doc.undo_stack.size() > max_undo)
        doc.undo_stack.erase(doc.undo_stack.begin(), doc.undo_stack.begin() + static_cast<std::ptrdiff_t>(doc.undo_stack.size() - max_undo));
    doc.redo_stack.clear();
}

void Session::end_mutation(Document& doc) {
    invalidate_caches(doc);
    doc.dirty = true;
}

bool Session::undo(Document& doc) {
    if (doc.undo_stack.empty()) return false;
    nlohmann::json current = effect_to_json(doc.effect);
    doc.effect = effect_from_json(doc.undo_stack.back());
    doc.undo_stack.pop_back();
    doc.redo_stack.push_back(std::move(current));
    invalidate_caches(doc);
    doc.dirty = true;
    return true;
}

bool Session::redo(Document& doc) {
    if (doc.redo_stack.empty()) return false;
    nlohmann::json current = effect_to_json(doc.effect);
    doc.effect = effect_from_json(doc.redo_stack.back());
    doc.redo_stack.pop_back();
    doc.undo_stack.push_back(std::move(current));
    invalidate_caches(doc);
    doc.dirty = true;
    return true;
}

const compiler::CompiledEffect& Session::compiled(Document& doc, const compiler::CompileOptions& options) {
    const uint64_t hash = effect_hash(doc.effect);
    if (!doc.compiled || doc.compiled_hash != hash || doc.compiled_fixed_dt != options.fixed_dt) {
        doc.runtime.reset();  // it points at the old CompiledEffect
        doc.runtime_hash = 0;
        // A file texture's relative `path` is relative to the document it was authored in,
        // so the compiler resolves it against the document's directory unless the caller
        // already chose a base directory.
        compiler::CompileOptions effective = options;
        if (effective.base_dir.empty() && doc.path) effective.base_dir = doc.path->parent_path();
        doc.compiled = std::make_shared<compiler::CompiledEffect>(compiler::compile(doc.effect, effective));
        doc.compiled_hash = hash;
        doc.compiled_fixed_dt = options.fixed_dt;
    }
    return *doc.compiled;
}

sim::IRuntime& Session::runtime(Document& doc, const compiler::CompileOptions& options) {
    const compiler::CompiledEffect& plan = compiled(doc, options);
    if (!plan.ok()) {
        // The first error decides the code, so an agent sees "E008" (fixable) or
        // "not_implemented" (the backend is missing) rather than a generic failure.
        for (const Diagnostic& d : plan.diagnostics.items) {
            if (d.severity != Severity::Error) continue;
            throw Error(d.code, "cannot simulate \"" + doc.effect.name + "\": " + d.message);
        }
        throw Error("compile_failed", "cannot simulate \"" + doc.effect.name + "\": the effect did not compile");
    }
    if (!doc.runtime || doc.runtime_hash != doc.compiled_hash) {
        doc.runtime = sim::create_cpu_runtime(plan);
        doc.runtime_hash = doc.compiled_hash;
    }
    return *doc.runtime;
}

render::IRenderer& Session::renderer() {
    if (!renderer_) renderer_ = render::create_software_renderer();
    return *renderer_;
}

}  // namespace aether::tools
