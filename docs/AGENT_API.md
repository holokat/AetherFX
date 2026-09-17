# Agent Tool API

Every operation an agent can perform is a tool in `aether::tools::ToolRegistry`
(src/tools). The CLI (`aetherfx tool <name> '<json>'`, `aetherfx serve`),
the Python client (`aetherfx.Client`), the MCP server and the future GUI all
call the same registry. Arguments and results are JSON objects. Errors are
`{"error": {"code": "...", "message": "...", "node": ?, "param": ?}}` at the
transport level (JSON-RPC error with `data.aether_code`).

Conventions:

* `effect_id` is optional everywhere; omitted means the active effect.
* Node references are node ids. Port references are `"node_id.port"`.
* Parameter values use the JSON encoding in docs/VOCABULARY.md.
* Every mutating tool returns `{"ok": true, "diagnostics": {...}}` plus the
  affected object, so the agent gets validation feedback immediately.
* Times are seconds. Sizes are pixels. Paths are absolute or relative to the
  session output directory.

## effect

| tool | args | returns |
|---|---|---|
| `create_effect` | `name`, `duration=2`, `seed=1`, `time_scale=1`, `template?` (`empty`, or an example name) | `{effect_id, effect}` becomes active |
| `delete_effect` | `effect_id` | `{ok}` |
| `list_effects` | | `{effects:[{effect_id,name,path,dirty,active}]}` |
| `set_active_effect` | `effect_id` | `{ok}` |
| `set_effect_property` | `name?`, `duration?`, `time_scale?`, `seed?`, `metadata?` | `{effect}` |
| `describe_vocabulary` | `node_type?` | vocabulary json (all node specs, or one), plus `texture_ops` |
| `get_effect_json` | `effect_id?` | the full document |

## graph

| tool | args | returns |
|---|---|---|
| `create_layer` | `name`, `role`, `id?` | `{layer}` |
| `delete_layer` | `layer_id`, `delete_nodes=false` | `{ok, removed_nodes}` |
| `duplicate_layer` | `layer_id`, `new_id?`, `suffix="_copy"` | `{layer, nodes:[ids]}` (internal refs remapped) |
| `set_layer_property` | `layer_id`, `name?`, `role?`, `enabled?` | `{layer}` |
| `create_node` | `type`, `id?`, `layer?`, `parent?`, `parameters?`, `inputs?`, `metadata?`, `seed?` | `{node, diagnostics}` |
| `create_emitter` .. `create_texture` | same as `create_node` minus `type` | one wrapper per node type: `create_emitter`, `create_particle_system`, `create_volume`, `create_force`, `create_field`, `create_mesh`, `create_curve`, `create_trail`, `create_beam`, `create_light`, `create_decal`, `create_material`, `create_event`, `create_noise`, `create_collider`, `create_camera`, `create_post_effect`, `create_texture` |
| `delete_node` | `node_id` | `{ok, dangling:[{node,port}]}` (references removed) |
| `duplicate_node` | `node_id`, `new_id?` | `{node}` |
| `set_node_property` | `node_id`, `enabled?`, `parent?`, `layer?`, `seed?`, `metadata?` | `{node}` |
| `connect_nodes` | `from` (node id), `to` (node id), `port` | `{node, diagnostics}` appends on multi ports, replaces on single ports |
| `disconnect_nodes` | `to`, `port`, `from?` | `{node}` removes one ref or clears the port |

## parameters

| tool | args | returns |
|---|---|---|
| `set_parameter` | `node_id`, `name`, `value` | `{node_id, name, value, diagnostics}` clears any track |
| `set_parameters` | `node_id`, `parameters` (object) | `{node, diagnostics}` |
| `get_parameter` | `node_id`, `name`, `time?` | `{value, default, animated, track, spec}` |
| `reset_parameter` | `node_id`, `name` | `{ok}` back to spec default |
| `set_keyframe` | `node_id`, `name`, `time`, `value`, `interp="linear"` | `{track}` |
| `remove_keyframe` | `node_id`, `name`, `time` | `{track}` |
| `clear_track` | `node_id`, `name` | `{ok}` |

## controls

Controls are the effect's named numeric knobs: non-destructive scales and
offsets on node parameters that the compiler folds into a copy of the document.
The authored values never change, so a control is safe to move live, undo, or
set per instance from a game. Full contract: docs/CONTROLS.md.

| tool | args | returns |
|---|---|---|
| `list_controls` | | `{controls:[{id,label,group,min,max,default,value,step,unit,bindings}], groups, count}` |
| `set_control` | `id`, `value` | `{ok, control, diagnostics}` (E024 outside `[min, max]`) |
| `reset_controls` | `id?` | `{ok, reset, controls}` back to `default` |
| `add_control` | `label`, `id?`, `group?`, `min=0`, `max=3`, `default=1`, `value?`, `step=0.01`, `unit?`, `bindings?` | `{ok, control, diagnostics}` |
| `update_control` | `id`, any of the `add_control` fields | `{ok, control, diagnostics}` |
| `remove_control` | `id` | `{ok, removed, controls}` |
| `generate_default_controls` | `replace=false` | `{ok, added, replaced, controls, groups}` |

A binding is `{node, parameter, op}` with `op` one of `multiply` (scalars, every
component of a vector, the rgb of a colour), `add`, `set`, or `hue_shift`
(degrees; colours and gradients). `group` is the UI section: `"Global"` (or
empty) for effect-wide, otherwise the layer name. Bad bindings are diagnostics,
not exceptions: E022 unknown node/parameter, E023 op that does not fit the
parameter type, E024 range problem, W007 a control with no bindings.

## timeline

| tool | args | returns |
|---|---|---|
| `set_timeline_phase` | `name`, `start`, `end` | `{timeline}` (creates or updates) |
| `remove_timeline_phase` | `name` | `{timeline}` |
| `get_timeline` | | `{duration, time_scale, wall_duration, phases, bound_nodes:{phase:[ids]}}` |

## simulate

| tool | args | returns |
|---|---|---|
| `simulate` | `time?` (default: effect duration), `fixed_dt?` | `{statistics}` runs from 0 to `time` |
| `simulate_range` | `start`, `end`, `sample_every?` | `{samples:[{time,total_alive,per_system}], statistics}` |
| `step_simulation` | `frames=1` | `{time, frame, statistics}` |
| `reset_simulation` | | `{ok}` |

## render

| tool | args | returns |
|---|---|---|
| `render_frame` | `time`, `width?`, `height?`, `camera?`, `settings?`, `path?`, `format="png"` | `{path, time, render_statistics, image_stats}` |
| `render_preview` | `fps=24`, `start=0`, `end?`, `width?`, `height?`, `camera?`, `settings?`, `contact_sheet=true`, `video=false`, `out_dir?` | `{frames:[paths], contact_sheet?, video?, statistics}` |
| `render_turntable` | `time`, `frames=8`, `distance?`, `height?`, `width?`, `height_px?` | `{frames, contact_sheet}` |
| `inspect_render` | `path` | `{width, height, mean_luminance, max_luminance, coverage, bbox, dominant_colors:[{rgb, share}], hash}` |

`camera` is `{position, target, up?, fov?}`; `settings` is any subset of
`RenderSettings` (`bloom`, `ground_plane`, `background`, `exposure`, ...).
Render tools return file paths; the MCP server additionally returns the image
content so the agent can see it.

## inspect

| tool | args | returns |
|---|---|---|
| `inspect_graph` | `effect_id?`, `verbose=false` | `{name, duration, seed, timeline, layers:[{..., node_count}], nodes:[{id,type,layer,enabled,parent,inputs,consumers,tier,window}], diagnostics}` |
| `inspect_node` | `node_id` | `{node, spec, effective_parameters, resolved_inputs, consumers, tier, window, diagnostics}` |
| `inspect_statistics` | | `{simulation, render, plan}` (last known) |
| `inspect_plan` | | compiled plan (`CompiledEffect::plan_json`) |
| `validate_effect` | `effect_id?` | `{diagnostics}` |

## evaluate

| tool | args | returns |
|---|---|---|
| `compare_reference` | `reference_path`, `render_path?` or `time?` | `{coverage_iou, palette_distance, luminance_histogram_distance, centroid_offset, radial_profile_distance, score, notes}` |
| `evaluate_effect` | `time?` | `{statistics, budgets:[warnings], render_statistics, diagnostics, score_hints}` |

## io

| tool | args | returns |
|---|---|---|
| `save_effect` | `path?` | `{path}` |
| `load_effect` | `path` | `{effect_id, effect, diagnostics}` becomes active |
| `export_effect` | `format` (`json`, `flipbook`, `frames`, `package`), `path`, `options?` | `{path, files, manifest}` |

| format | `path` | writes |
|---|---|---|
| `json` | file | the effect document (the same bytes `save_effect` writes) |
| `frames` | directory | `frame_0000.png` ... at `fps` over `start`..`end` |
| `flipbook` | file | one sprite sheet plus `<path>.manifest.json` describing the grid |
| `package` | directory | the engine-agnostic interchange package (docs/PACKAGE_FORMAT.md) |

`frames`/`flipbook` options: `fps`, `columns`, `width`, `height`, `start`,
`end`, `camera`, `settings`; the flipbook manifest records frame count, grid,
fps, duration and the effect hash.

`package` options: `fps` (sampling rate for animated parameters, default 30),
`curve_samples` (32), `exr` (false: also write linear EXR next to each PNG),
`preview` (true), `obj` (true), plus `width`, `height`, `camera` and `settings`
for the preview images. `path` is a directory and may end in `.aetherfx`. It
holds `manifest.json`, `effect.json` (the source document), `runtime.json` (the
resolved, compiler-free description: phases, layers, controls with their
current values, every enabled node with its window, seed, effective parameters
- controls already folded in - sampled tracks and resolved references, plus the
texture/mesh/material tables), `textures/<id>.png`,
`meshes/<id>.obj` and `preview/`. Field-by-field reference:
docs/PACKAGE_FORMAT.md.

## history

| tool | args | returns |
|---|---|---|
| `undo` | | `{ok, remaining}` |
| `redo` | | `{ok, remaining}` |

## MCP surface

The MCP server exposes this curated subset, in this order, with the same
argument names: `describe_vocabulary`, `create_effect`, `load_effect`,
`save_effect`, `list_effects`, `inspect_graph`, `inspect_node`,
`create_layer`, `create_node`, `delete_node`, `duplicate_layer`,
`set_parameter`, `set_parameters`, `get_parameter`, `set_keyframe`,
`list_controls`, `set_control`, `reset_controls`, `generate_default_controls`,
`connect_nodes`, `disconnect_nodes`, `set_timeline_phase`, `simulate`,
`render_frame`, `render_preview`, `inspect_statistics`, `compare_reference`,
`evaluate_effect`, `export_effect`, `undo`, `redo`, plus `get_effect_json`.
Everything else stays reachable through the CLI/Python. Keep it small.
