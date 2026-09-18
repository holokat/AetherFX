# The community collection

Contributed effects live in this repository under `community/effects/*.json` and arrive by
pull request. There is no AetherFX server and no account system: **GitHub is the backend**.
CI checks a contribution, renders its previews, rebuilds a static index and publishes it; the
studio reads the local directory and, optionally, one published index over https.

* Contributors (and their agents) read **`CONTRIBUTING.md`**.
* The reviewing agent reads **`docs/REVIEWING.md`**.
* This document is the reference: the metadata, what the check enforces, the index format,
  the hosting model and how the studio consumes it.

Everything here is MIT, the same licence as the engine (`LICENSE`).

---

## 1. Credit metadata

Credit is deliberately minimal: a GitHub username. No sign-up, no e-mail, no profile, no
arbitrary links.

```json
{
  "name": "Arcane Missile",
  "description": "A sapphire arcane missile that streaks downrange and detonates.",
  "duration": 3.2,
  "metadata": {
    "author": { "github": "octocat", "name": "Octo Cat" },
    "license": "MIT",
    "tags": ["arcane", "projectile", "missile", "impact", "magic"],
    "description": "Optional; overrides the top-level description in listings."
  }
}
```

| field | required | rule |
|---|---|---|
| `metadata.author.github` | yes | `^[A-Za-z0-9-]{1,39}$` - a GitHub username. The link is built as `https://github.com/<username>`; nothing else is ever linked. |
| `metadata.author.name` | no | 1-60 characters, no control characters, no `<` or `>`, no link, no file name. Shown instead of the username when present. |
| `metadata.license` | yes | Must be `"MIT"`. |
| `metadata.tags` | no | At most 8 lowercase slugs, `^[a-z0-9]+(?:[-_][a-z0-9]+)*$`, at most 24 characters each. They drive the studio's search. |
| `metadata.category` | no | The studio groups effects by category: `aoe` (Area of Effect), `targeted` (bolts, missiles, strikes, drains and curses aimed at one enemy), `support` (heals, buffs), `melee` or `mobility` (teleports, blinks). Without it the tags decide: `melee`, then `support`/`heal`/`buff`, then `mobility`/`teleport`/`blink`/`utility`, then `aoe`/`area`, otherwise `targeted`. |
| `metadata.description` / `description` | yes | One line, at most 400 characters, no link. |

Any other field inside `metadata.author` is an error, including `x`, `twitter`, `email` and
`url`. A malformed credit block fails the check and, in the studio, is dropped rather than
rendered.

`python/aetherfx/community.py` is the one implementation:

```python
from aetherfx.community import parse_author, parse_tags, parse_license

author = parse_author(document)     # -> Author | None, raises AuthorError on anything malformed
author.url                          # https://github.com/octocat
author.display                      # "Octo Cat", else "octocat"
author.to_json()                    # what /api/effects and the index carry
author.to_html()                    # an escaped "by <a ...>" fragment
```

Contributed strings are untrusted input. They are validated against a strict pattern before
they are used, `to_html()` escapes everything it emits, and the studio's frontend builds every
row with `textContent`, never `innerHTML`.

---

## 2. The contribution check

```bash
python -m aetherfx.community check <effect.json> [--previews DIR] [--json] [--no-engine]
python -m aetherfx.community lint-library [DIR ...] [--json] [--no-allowlist]
python -m aetherfx.community index [DIR] [--out FILE] [--previews DIR] [--check]
```

`aetherfx-community` is the same entry point as a console script. Exit code is `0` when there
are no errors, `1` otherwise. Warnings are listed and do not fail; engine `I`-codes are shown
as notes.

### What `check` runs

1. **Metadata** - `name`, a description, the credit block, MIT, at least 3 controls.
2. **Assets** - no `source: "file"` texture or mesh, no `path`, no URL, no image or video file
   named anywhere, no embedded base64.
3. **Engine validation** through the native library: 0 errors; warnings listed.
4. **Determinism** - the document is compiled and simulated twice; the particle buffers are
   hashed at 25%, 50%, 75% and 99% of the duration and must be identical.
5. **Budgets** - measured from the same simulation.
6. **House-style lint** - the rules below.
7. **Previews** with `--previews`: a 6-frame contact sheet and a GIF from the CPU reference
   renderer, honouring `metadata.render_settings` (the render tools do not read it, so the
   check passes it explicitly).

### Budgets

| budget | limit | constant |
|---|---|---|
| live sprite particles at peak | 6000 | `Budgets.max_live_sprites` |
| live mesh particles at peak | 300 | `Budgets.max_live_mesh_particles` |
| nodes | 160 | `Budgets.max_nodes` |
| duration | 12 s | `Budgets.max_duration` |
| baked texture bytes | 96 MiB | `Budgets.max_texture_bytes` |
| texture bake time | 6 s | `Budgets.max_texture_bake_seconds` |
| style controls | at least 3 | `Budgets.min_controls` |

Calibrated against the shipped library with headroom: the heaviest example bakes for 2.3 s and
54 MiB, the busiest peaks near 3000 sprites, and Ice AOE peaks at 239 mesh particles.

### House-style lint

| code | level | rule |
|---|---|---|
| `style.four_arms` | error | a `star` or `spokes` texture op with 4 arms is a cross |
| `style.star_default` | error | `star` without an explicit `points` defaults to 4 |
| `style.two_arms` | warning | 2 arms on a sprite drawn larger than 0.5 m reads as a bar |
| `style.tube_beam` | warning | a `beam` with `detail < 2` and a uniform `width_profile` on screen for more than 0.3 s |
| `style.hard_ribbon` | warning | a `trail` wider than 0.12 m whose material has no base texture |
| `style.no_impact` | warning | a keyframed `mesh`/`emitter` whose net travel is over 3 m with more than 2 m of it horizontal, and no `impact` phase and no burst in the last 30% |

`lint-library` runs the lint and the validator over `examples/effects/` and
`community/effects/` (community directories additionally require the credit metadata). It is
what CI and the reviewing agent run. `LINT_ALLOWLIST` in `community.py` excuses named files
that are being revised; it is currently **empty** and should stay tiny.

---

## 3. Repository layout

```
community/
  effects/
    README.md            the rules, short
    .gitkeep
    arcane_missile.json  one contributed effect per file
  index.json             generated - never edited by hand
```

### `community/index.json`

Built by `tools/build_community_index.py` (which calls `aetherfx.community.build_index`).
Deterministic: same inputs, same bytes - sorted keys, 2-space indent, one trailing newline, no
timestamps and no absolute paths, so CI can rebuild it and commit only a real change.

```json
{
  "schema": "aetherfx.community.index/1",
  "repository": "https://github.com/holokat/AetherFX",
  "license": "MIT",
  "count": 1,
  "effects": [
    {
      "slug": "arcane_missile",
      "file": "effects/arcane_missile.json",
      "name": "Arcane Missile",
      "description": "...",
      "author": {"github": "octocat", "url": "https://github.com/octocat", "name": "Octo Cat", "display": "Octo Cat"},
      "tags": ["arcane", "projectile"],
      "license": "MIT",
      "duration": 3.2,
      "node_count": 47,
      "controls": [{"id": "missile_glow", "label": "Missile glow", "group": "Projectile"}],
      "sha256": "…",
      "bytes": 45679,
      "schema_version": "0.1.0",
      "previews": {"animation": "previews/arcane_missile.gif", "sheet": "previews/arcane_missile_sheet.png"}
    }
  ]
}
```

`previews` is filled in only when the index is built with `--previews DIR` and the files are
actually there. Preview media is **never committed**; CI renders it and publishes it with the
index.

---

## 4. Hosting

Nothing of ours runs anywhere. `.github/workflows/effects.yml` does two jobs:

* **On a pull request** touching `examples/effects/**` or `community/effects/**`: build the
  engine on Ubuntu and macOS, run ctest and pytest, run the contribution check on the changed
  community files with `--previews`, upload the previews as workflow artifacts, and post (or
  refresh) one PR comment with the report.
* **On a push to `main`**: rebuild `community/index.json` and every preview, and publish
  `index.json`, `effects/` and `previews/` to the `gh-pages` branch under `community/`.

GitHub Pages then serves:

```
https://holokat.github.io/AetherFX/community/index.json
https://holokat.github.io/AetherFX/community/effects/<slug>.json
https://holokat.github.io/AetherFX/community/previews/<slug>_sheet.png
```

Put a CDN in front of that hostname and nothing changes: the index is a static JSON document
with relative `file` paths, so it works from any origin that serves the directory. The raw
default the studio ships with is
`https://raw.githubusercontent.com/holokat/AetherFX/main/community/index.json`.

Only `GITHUB_TOKEN` is used. There are no secrets.

---

## 5. How the studio consumes it

The studio has two views in its header: **Studio** and **Community**.

### Local effects

`--community-dir DIR` (default `<repo>/community/effects` when the checkout has one) is read
on every listing. Contributed effects appear in the Community view as cards - name,
description, tags, duration, "by &lt;name&gt;" linking to the GitHub profile, and an **Open**
button.

Opening one creates a **working copy**, exactly like a Core effect: the contributed file is
never written. The header of the right column shows the effect name with a credit chip next to
it linking to `https://github.com/<username>` (`target="_blank"`, `rel="noopener noreferrer"`).
"Save as" writes into the user's own output directory, and **every** write path refuses a
community file - the UI, `/api/effects/save`, `/api/tool` and the MCP endpoint all go through
`Studio.guard_tool` / `is_protected_path`.

### Endpoints

| endpoint | returns |
|---|---|
| `GET /api/effects` | `library` entries with `section` (`core`/`community`/`mine`), `author`, `tags`, `description`, `protected`; plus `examples`, `community`, `saved`, `open`, `working` |
| `GET /api/community` | `{local, remote, index, dir, repository}` for the Community view |
| `GET /api/community/remote[?refresh=1]` | the published index alone (404 when none is configured) |
| `POST /api/community/download` | `{slug}`: fetch, check, then cache one remote effect locally |
| `GET /api/status` | adds `community_dir`, `community_index` (cache status) and `repository` |

### The optional remote index

Off by default. Set `AETHERFX_COMMUNITY_INDEX`, or pass `--community-index URL`:

```bash
aetherfx-studio --community-index https://holokat.github.io/AetherFX/community/index.json
```

The studio fetches it **server side** - the browser never talks to another origin - and:

* **https only.** `--community-index-insecure` permits `http://` and `file://`; that flag
  exists for tests and local experiments, not for deployment.
* **8 s timeout, 4 MiB cap**, on the index and on every downloaded effect.
* **Schema checked**: the `schema` tag must match, `effects` must be a list, every entry is
  validated and anything unrecognised is dropped. A slug must be alphanumeric with `-`/`_`,
  and a `file` may not escape the index's directory.
* **Cached in memory for 5 minutes**, with the last good copy kept. A failed fetch never
  breaks the view: the Community section keeps showing the local entries and records the
  error.

Remote entries that are not already on disk show a **Download** button. A download re-fetches
the effect, runs the **whole contribution check** on it in a staging file, and only then moves
it into `<output-dir>/community_cache/`, where it is protected like any other community
effect. An effect that fails the check is never written.

---

## 6. The studio's library search

`/api/effects` carries `tags` and `description` per entry so the Library and the Community view
can filter without a round trip: case-insensitive, accent-folded, multi-term AND over name,
slug, tags, description and the author's GitHub name. Every shipped example carries a small
`metadata.tags` list for exactly this reason - adding tags touches `metadata` only and changes
nothing the compiler reads.
