# Contributing an effect

**This guide is written for the agent doing the work.** If you are a person: point your
coding agent at this file, or read it yourself - the instructions are the same, they are just
blunt.

You are contributing **one effect document** to
[AetherFX](https://github.com/holokat/AetherFX). An effect is a single JSON file that
the engine validates, simulates and renders. Nothing else ships with it: no textures, no
meshes, no images, no code.

Everything below is enforced by one command. Run it before you open a pull request:

```bash
python -m aetherfx.community check community/effects/<slug>.json --previews out/previews
```

Exit code `0` means your contribution can be reviewed. Anything else means fix it first.

---

## 1. Hard requirements

The check fails the contribution if **any** of these is violated. There are no exceptions and
no flags to skip them.

### Engine

| Rule | Detail |
|---|---|
| Validates | `validate` reports **0 errors**. Warnings are listed and allowed. |
| Deterministic | Two runs of the same document produce identical frame hashes at 25%, 50%, 75% and 99% of the duration. If you used anything time-of-day or unseeded, it will fail here. |
| Schema | `schema_version` is `"0.1.0"`, the document parses, `name` and `duration` are set. |

### Budgets

| Budget | Limit |
|---|---|
| Live sprite particles at peak | 6000 |
| Live mesh particles at peak | 300 |
| Nodes | 160 |
| Duration | 12 s |
| Baked texture bytes | 96 MiB |
| Texture bake time | 6 s |

Aim well under these. The heaviest shipped example uses ~3000 sprites and 54 MiB.

### Procedural only

* No `source: "file"` on a texture or a mesh. Build the pixels with a texture graph and the
  geometry with a procedural primitive.
* No `path` parameter anywhere.
* No URL anywhere in the document.
* No image or video file named anywhere in the document - not in a parameter, not in a
  description, not in `metadata`.
* No embedded base64 blobs.

**Never commit reference art.** No concept sheets, no screenshots, no photos, no preview
GIFs or MP4s, no matter where they came from. `python/tests/test_repo_hygiene.py` fails the
build if a media file is tracked. Previews are rendered by CI and published outside the
default branch; your `out/` directory is git-ignored and that is where they belong.

### Licence and credit

```json
"metadata": {
  "author": { "github": "your-github-username", "name": "Your Name" },
  "license": "MIT",
  "tags": ["fire", "projectile", "impact"],
  "description": "One line that says what this effect is."
}
```

* `license` must be `"MIT"`. You are licensing the effect under the project's licence so
  anyone can ship it in a game. If you are not willing to do that, do not contribute it.
* `author.github` is **required** and is the whole account model: 1-39 letters, digits or
  hyphens. `name` is optional. There is no sign-up, no e-mail, no profile. The studio shows
  "by &lt;name&gt;" linking to `https://github.com/<username>`.
* `tags` are up to 8 lowercase slugs. They power the search in the studio, so use words
  someone would type: the element, the shape, the role (`fire`, `aoe`, `projectile`,
  `impact`, `support`).
* A one-line `description` is required (top level or in `metadata`).

### Controls

At least **3** named style controls, authored with `add_control`
(see `docs/CONTROLS.md`). Name them the way an artist would: "Flame height", "Ember amount",
"Impact power" - never a parameter name. Bind one control to every node that makes up that
idea, so one slider moves the whole look. Multipliers use min 0, max 3, default 1, step 0.01,
unit `"x"`; a hue control uses min -180, max 180, default 0, step 1, unit `"deg"`.

### House style

The full text lives in the authoring guide (`aetherfx://docs/authoring-guide` over MCP). The
lint enforces the parts a machine can see; a reviewing agent looks at the rest.

* **No crosses.** No four-armed stars, plus signs or cross motifs - not as flares, not in
  runes, not as sparkle sprites. A `star` or `spokes` texture op with 4 arms is an **error**,
  and `star` without an explicit `points` defaults to 4, which is also an error. Use 5, 6 or 8.
  Two arms on a large sprite is a warning.
* **No obvious hard lines.** A `beam` with `detail` < 2 and a uniform `width_profile` that
  stays on screen longer than 0.3 s reads as a neon tube: warning. Use `detail >= 2` and
  `taper_end`, `taper_both` or `bulge`.
* **No flat tape ribbons.** A `trail` wider than 0.12 m whose material has no base texture is
  a warning. Give the material a soft cross-width texture: bright thin centre fading to zero
  at both edges.
* **Projectiles land.** Anything that travels more than 3 m horizontally must end in an
  impact: an `impact`-named timeline phase, or a burst emitter in the last third. A flash
  light, a radial burst in the palette, debris with gravity, a camera-facing shock ring, a
  short residue. Never let a projectile fade out.
* **No uniform primitives as debris.** Use `mesh` `primitive: crystal|rock|shard` with
  `variants` and `irregularity`, never plain cubes or cones.
* **No blown-out glows.** A white blob hides the shape you made. Additive sheets saturate
  fast: keep `opacity` low and let bloom do the work.
* **Light looks like light.** Gradients and falloff, never a flat shape filled with white.

---

## 2. What qualifies

Contribute an effect that:

* is **original** - you made it in AetherFX, in this session, from a prompt or a generator
  script you also contribute;
* **reads at gameplay distance** - the silhouette and the colour tell you what it is in the
  first 200 ms, on a 3-4 m tall stage seen from ~5 m;
* has **phases** - a telegraph or anticipation, an activation, a peak, a decay. A single
  constant puff is not an effect;
* is **distinct** from everything already in `examples/effects/` and `community/effects/` -
  a different element, a different silhouette, a different idea;
* is **game-ready** - inside the budgets, deterministic, tuneable through its controls,
  holding 55-60 fps in the studio's GPU viewer;
* is **safe for everyone** - no text, no symbols, no iconography that is religious,
  political, hateful or sexual.

## 3. What does not qualify

These are declined on sight:

* **Near-duplicates and palette swaps** of an existing effect. The exception is a deliberate
  **variant set** produced by one generator script (see `tools/generators/heal_variants.py`):
  contribute the generator, the variants and say so in the PR.
* **Broken loops** - an effect that pops, jumps or leaves particles alive at the end.
* **Anything that needs an external asset**: a file texture, a mesh file, a font, a URL.
* **Reference art, concept sheets, screenshots, preview media** in the commit.
* **Copyrighted characters, logos, brand marks** or a recognisable recreation of one.
* **Offensive or ideological symbols**, text or iconography.
* **Over budget**, non-deterministic, or failing the lint.
* **A projectile with no impact.**
* An effect **you did not author** in AetherFX.

---

## 4. Authoring workflow (MCP)

Connect your agent to a running studio's MCP endpoint (`http://127.0.0.1:8770/mcp`) or the
stdio server (`aetherfx-mcp`). Then:

1. `describe_vocabulary` once. Use only what it lists.
2. Read the resource `aetherfx://docs/authoring-guide`. It has the conventions, the proven
   recipes and the house style.
3. `create_effect(name, duration, seed)`, `create_layer` per semantic layer
   (telegraph / ignition / primary / secondary / interaction / aftermath), then the nodes:
   textures, materials, forces, colliders, particle systems, emitters, trails, beams, lights,
   decals, a camera.
4. `set_timeline_phase` for the phases. Bind emitters and lights to a phase, or keyframe
   `rate` / `intensity` with `set_keyframe`.
5. `simulate()`, then `render_preview(fps=12, width=384, height=384)` and **look at the
   contact sheet**. Criticise it like a senior VFX artist: silhouette, colour, scale, timing,
   anything invisible or blown out. Fix and render again. **At least three passes.** One
   iteration with real changes beats three with tiny tweaks.
6. `generate_default_controls`, then keep the ones that read well, rename them to artist
   words, `remove_control` the rest. At least three.
7. Fill in `metadata` (author, license, tags, description).
8. `save_effect` into your own output directory, or press "Save as" in the studio.

Mutating tools return diagnostics. Fix every `E` code immediately; read the `W` codes.

---

## 5. How to submit

```bash
# 1. fork https://github.com/holokat/AetherFX and clone your fork
git checkout -b community/<slug>

# 2. put exactly one new file here (<slug> matches the branch name, snake_case)
cp <your effect>.json community/effects/<slug>.json

# 3. build the engine if you have not (the check needs the native library)
cmake --preset default && cmake --build --preset default

# 4. run the check - it must exit 0
python -m aetherfx.community check community/effects/<slug>.json --previews out/previews

# 5. commit with a sign-off
git add community/effects/<slug>.json
git commit -s -m "community: <Effect Name>"

# 6. push and open a pull request using the community effect template
git push -u origin community/<slug>
```

**Exactly one new file**, `community/effects/<slug>.json`. Optionally one generator script
under `tools/generators/community/<slug>.py` if you produced the effect programmatically or
shipped a variant set. Nothing else: no changes to the engine, the studio, the examples or
the index (`community/index.json` is rebuilt by CI).

`git commit -s` adds the `Signed-off-by:` line. It is required: it is your statement that you
wrote the effect and license it under MIT.

### The pull request body

Use `.github/PULL_REQUEST_TEMPLATE/community_effect.md`. It asks for:

* **The prompt** you used, verbatim.
* **The model** that authored it.
* **The check output** - paste the whole report.
* **One paragraph** describing the effect: what it is, when a game would play it, what the
  controls do.

---

## 6. What happens next

1. **CI runs on your pull request.** It builds the engine on Ubuntu and macOS, runs the C++
   and Python suites, runs the contribution check on your file with `--previews`, uploads the
   contact sheet and the GIF as artifacts, and posts one comment with the report and the
   preview links. If it is red, push a fix; the comment updates in place.
2. **A reviewing agent audits the queue periodically** against `docs/REVIEWING.md`: the check
   output, the contact sheet, a GPU capture with frame timings, and a similarity pass against
   the existing library.
3. **One of three outcomes:**
   * **Merged** - it lands in `community/effects/`, the index and the published previews are
     rebuilt, and the studio's Community view shows it with your credit.
   * **Changes requested** - with the specific rules it missed and what to change. Push to the
     same branch; CI re-runs.
   * **Declined** - with the reason, usually "too close to `<existing effect>`" or a house-style
     violation that cannot be fixed by tuning. Not personal, and you are welcome to send
     another.

Every merged effect keeps your credit in `metadata.author` and in the index, forever.
