# Effect controls

A **control** is a named numeric knob stored in the effect document and bound
to node parameters. Moving it is not an edit of the graph: the authored values
stay exactly as they are, and the compiler folds the controls into its own copy
of the document. One effect can therefore be played weaker, stronger, bigger or
in a different colour without a second document and without a second graph.

Controls are deterministic, agent-authorable, exportable, and settable from a
game through the C ABI before it compiles an instance.

```
"controls": [
  { "id": "flames_intensity", "label": "Intensity", "group": "Flames",
    "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "unit": "x",
    "bindings": [ {"node": "flame_ps", "parameter": "emissive",  "op": "multiply"},
                  {"node": "fire_light", "parameter": "intensity", "op": "multiply"} ] }
]
```

## 1. The document

`controls` is a top-level array, next to `nodes` and `layers`. An effect
without it behaves exactly as it did before controls existed, and a document
that has none does not write the key at all.

| field | default | meaning |
|---|---|---|
| `id` | required | `^[a-z][a-z0-9_]*$`, unique within the effect |
| `label` | the id | what a slider is called: human words, e.g. "Flame height" |
| `group` | `""` | UI section: `"Global"` (or empty) for effect-wide, otherwise the layer *name* |
| `min`, `max` | `0`, `3` | the range the slider covers; `min < max` |
| `default` | `1` | what a reset returns to |
| `value` | `default` | what the next compile applies |
| `step` | `0.01` | slider and arrow-key increment |
| `unit` | `""` | shown next to the number: `"x"`, `"deg"` |
| `bindings` | `[]` | the node parameters this control drives |

A binding is `{node, parameter, op}`.

## 2. Operations

| op | applies to | what it does |
|---|---|---|
| `multiply` | int, float, vec2/3/4, colour | scales the value; **every** component of a vector; the **rgb** of a colour, never its alpha |
| `add` | the same | offsets it, componentwise |
| `set` | the same | replaces it |
| `hue_shift` | colour, gradient | rotates the hue by `value` degrees, keeping saturation, value and alpha |

Rules that hold for every op:

* **Keyframe tracks are transformed key by key**, and so is the constant. A
  control on a keyframed `rate` scales the whole envelope, it does not flatten
  it.
* **Gradients** are transformed key by key too, but only by `hue_shift`: a
  gradient is an over-lifetime *modulation*, so scaling it is not what an
  intensity control means.
* **Results are clamped into the parameter's own range**. `opacity` stops at 1
  no matter how far the slider goes, so a control can never push a document
  outside the range its own validator enforces.
* **Integers round**, they do not truncate: `burst_count` 40 at 1.5x is 60.
* A binding that cannot apply (unknown node, unknown parameter, an op that does
  not fit the type) is **skipped** when compiling and **reported** when
  validating. A typo never stops an effect from playing.

### Identity

`multiply` at 1, `add` at 0 and `hue_shift` at 0 are identities and are skipped
entirely, so nothing is written and nothing is recomputed. With every control at
its default, a controlled effect compiles to the same plan and simulates to the
same frame hashes as the same effect with no controls at all.
`tests/sim/controls_test.cpp` asserts exactly that for every document in
`examples/effects/`.

### Order

Controls apply in document order, on one shared copy, so they compose. A Global
Intensity of 2 and a Flames Intensity of 1.5 give the flames 3x. That is the
point of having both.

## 3. Validation

| code | meaning |
|---|---|
| E021 | invalid or duplicate control id |
| E022 | the binding names a node or a parameter that does not exist |
| E023 | the op does not fit the parameter type (`multiply` on an enum, `hue_shift` on a number) |
| E024 | `min >= max`, `value` or `default` outside `[min, max]`, or `step <= 0` |
| W007 | the control has no bindings, so moving it does nothing |

## 4. Where they are applied

`compile()` validates the **source** document, then folds the controls into
`CompiledEffect::effect` before anything reads a parameter. Every later stage -
window resolution, tier selection, baked textures and meshes, the runtime, the
renderer, the package export - sees one already-resolved document. The source
`Effect` is never mutated; `CompiledEffect::controls_applied` counts the
bindings that changed something, and the plan reports
`{"controls": {"count", "applied"}}`.

Because the control values live in the document, `effect_hash` covers them, so
the session's compile and runtime caches invalidate on their own when a control
moves.

## 5. Tools

docs/AGENT_API.md "controls": `list_controls`, `set_control`,
`reset_controls`, `add_control`, `update_control`, `remove_control`,
`generate_default_controls`. All of them take the usual optional `effect_id`.

`generate_default_controls {replace?}` builds a sensible set for any effect:

* a **Global** group - Intensity, Size, Density, Opacity, Hue - over every node;
* one group **per layer** - Intensity, Size, Density, Opacity - over the nodes
  in that layer, named after the layer (Fire AOE gets "Telegraph", "Eruption",
  "Flames", "Debris, sparks, smoke", "Light", "Aftermath").

Multipliers run 0..3 with default 1 and unit `x`; Hue runs -180..180 with
default 0 and unit `deg`. A knob with no bindings is left out. With
`replace: false` (the default) it only fills in what is missing, so a set an
author trimmed by hand survives.

The binding heuristics, by node type:

| knob | binds |
|---|---|
| Intensity | particle/beam/trail/decal/mesh `emissive` **and** `color`, material `emissive_intensity`, light `intensity`, volume `emission` and `color`, post_effect `intensity` |
| Size | particle `size`, trail/beam `width`, decal `size`, light `radius`, volume `radius`, visible mesh `scale`, invisible mesh `radius` |
| Density | emitter `rate` and `burst_count` |
| Opacity | particle/decal/material `opacity`, volume `density` |
| Hue | every colour and every gradient the node type declares |

Two deliberate departures from the obvious list:

* **Intensity also scales `color`.** On the shipped effects `emissive` is a
  small extra glow (0.05 - 0.2) and scaling it alone moves the picture by under
  2%. What makes an additive sprite bright is the HDR magnitude of its `color`,
  and scaling rgb uniformly keeps the hue, so both are bound.
* **Size does not touch `mesh_scale`.** `size` already scales mesh instances;
  `mesh_scale` is the per-axis *shape* of the mesh particle. Scaling both would
  square the control.

## 6. Authoring by hand

Generated controls are a starting point, not the destination. An authored
effect should ship 4-8 meaningful knobs with names an artist recognises -
"Flame height", "Ember amount", "Core glow" - grouped by layer, plus the Global
set. `add_control` and `update_control` are how they are written;
`remove_control` is how the generated ones that say nothing are dropped.

## 7. In the studio

The **Style** panel sits above PARAMETERS in the right column: Global first,
then one collapsible group per layer, each control with its label, unit,
slider, numeric input and its own reset, plus "Reset all". Dragging updates the
viewport live (debounced to ~100 ms) without restarting playback; arrow keys
step by `step` and shift by ten of them.

Loading a library effect that ships no controls generates a default set **on
the working copy only** - the file on disk is untouched, the document does not
count as modified, and "Save as" keeps the controls and the values they were
left at.

Endpoints: `GET /api/controls`, `POST /api/controls/<id>` `{value}`,
`POST /api/controls/reset` `{id?}`, `POST /api/controls/generate` `{replace?}`.

## 8. In a game

The C ABI is additive (docs/ENGINE_INTEGRATION.md):

```c
int n = aetherfx_effect_control_count(effect);
struct aetherfx_control_info info;
aetherfx_control_info(effect, 0, &info);          /* id, label, group, min, max, default, value, step */

aetherfx_effect_set_control(effect, "flames_intensity", 0.4);  /* a weaker instance */
aetherfx_compiled* compiled = aetherfx_compile(effect, 1.0 / 60.0);
```

Set the controls, then compile: the change applies to the next
`aetherfx_compile()`, and compiled effects and runtimes that already exist keep
running the plan they were made with. Python gets the same through
`aetherfx.native.Effect.controls()` and `.set_control()`.

The interchange package lists them too: `runtime.json` has a `controls` array
with the current values, and the resolved `parameters` on every node already
have them folded in (docs/PACKAGE_FORMAT.md section 5).
