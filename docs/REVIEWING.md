# Reviewing community contributions

**This guide is written for the reviewing agent.** You audit the open pull requests against
`community/effects/` periodically, decide merge / changes / decline, and leave a comment that
the contributor (usually another agent) can act on without asking a question back.

You are the quality bar. CI proves an effect is *legal*; you decide whether it is *good*.

---

## 1. Set up the review

```bash
# one checkout per PR, in your own worktree - never in the user's
gh pr checkout <number>
cmake --preset default && cmake --build --preset default
ctest --test-dir build -j 8
PYTHONPATH=$PWD/python python -m pytest python/tests -q
```

Find what changed. A contribution is **exactly one new file** under `community/effects/`, plus
at most one generator under `tools/generators/community/`:

```bash
git diff --name-status main...HEAD
```

Anything else in the diff - engine changes, example changes, a hand-edited
`community/index.json`, any media file - is an automatic **changes requested**.

---

## 2. Run the machine checks

```bash
# the contribution check, with previews, on the changed file
python -m aetherfx.community check community/effects/<slug>.json --previews out/review/<slug> --json

# the whole library still has to lint
python -m aetherfx.community lint-library

# no tracked media, licence intact
python -m pytest python/tests/test_repo_hygiene.py -q

# the index is generated, not hand-written
python tools/build_community_index.py --check || true   # stale here is fine: CI rebuilds it on main
```

Read the JSON report. `metrics` gives you the numbers you will quote in the comment: peak
sprites, peak mesh particles, node count, duration, texture bytes, bake time, determinism.

---

## 3. Look at it

**Never merge an effect you have not watched.** The check cannot see whether something reads.

### The contact sheet

`out/review/<slug>/<slug>_sheet.png` is six frames across the duration. Ask:

* **Silhouette** - can you name the effect from frame 2 alone, at thumbnail size?
* **Phases** - do the six frames tell a story (build, hit, settle), or are they six of the
  same picture?
* **Exposure** - is the core a white blob? Is anything invisible?
* **Palette** - two or three hues with a hot core, or mud?
* **Scale** - does it sit 1-3 m tall around the origin, framed by its own camera node?

### The GPU viewer

Start **your own** studio - never port 8770, that is the user's - and capture with frame
timings:

```bash
python -m aetherfx.studio.server --port 8801 --no-open --generator none \
  --output-dir out/review/studio &

python tools/gl_capture.py --url http://127.0.0.1:8801/ --effect "<Effect Name>" \
  --times 0.2,0.6,1.2,2.0 --out out/review/<slug>/gl --size 1280x800 \
  --perf 6 --cdp-port 9361 --profile out/review/chrome

kill $(lsof -ti tcp:8801 -sTCP:LISTEN); pkill -f "remote-debugging-port=9361"
```

`--perf` prints `render_fps`, `stream_fps`, `worst_frame_gap_ms`, `avg_draw_calls` and
`avg_triangles`. **55-60 render fps** is the bar on the reviewer's machine. Anything below 45,
or a worst frame gap over 60 ms, is a performance change request even when the budgets pass.

Look at the captures for what the CPU sheet cannot show: ribbon edges, beam cross-sections,
mesh particle shading, bloom behaviour, popping at the loop point.

---

## 4. Score it

Six axes. Anything **red** blocks the merge; two or more **amber** is changes requested.

| Axis | Green | Amber | Red |
|---|---|---|---|
| **Correctness** | 0 errors, deterministic, inside budget with headroom | passes but within 10% of a budget | fails the check, non-deterministic, over budget |
| **House style** | no lint warnings, or warnings that are clearly deliberate | one or two warnings the effect could live without | a lint error, a cross motif, a tube beam, a projectile with no impact |
| **Originality** | a new element, silhouette or idea | a fresh take on an existing category | a palette swap or a near-duplicate of a library effect |
| **Readability** | nameable from one thumbnail, clear phases | reads once you know what it is | a bright mess, or invisible at gameplay distance |
| **Performance** | 55-60 fps, modest draw calls | 45-55 fps | under 45 fps, or frame gaps over 60 ms |
| **Metadata** | credit, MIT, 3+ artist-named controls, useful tags, honest description | controls named after parameters, thin tags | missing credit or licence, fewer than 3 controls |

### The similarity pass (do not skip it)

A palette swap is the most common decline. Compare the candidate against every effect in
`examples/effects/` and `community/effects/`:

* **Node structure** - node type counts, layer names and roles, the emitter shapes. Two
  documents with the same type histogram and the same layer names are the same effect.
* **Palette** - the dominant `color` and `color_over_life` hues. A hue shift is not a new
  effect.
* **Silhouette and timing** - the phase boundaries and what the contact sheet shows.

If the closest match shares structure *and* silhouette, it is a duplicate however different
the colours are. If it shares only the palette, say so and judge the rest on its merits.

A quick starting point:

```bash
python - <<'PY'
import json, collections, pathlib
def shape(p):
    d = json.loads(pathlib.Path(p).read_text())
    return (collections.Counter(n["type"] for n in d["nodes"]),
            sorted(l.get("role","") for l in d.get("layers", [])))
cand = shape("community/effects/<slug>.json")
for other in sorted(pathlib.Path("examples/effects").glob("*.json")):
    types, roles = shape(other)
    shared = sum((cand[0] & types).values()) / max(1, sum(cand[0].values()))
    if shared > 0.8 and roles == cand[1]:
        print(f"{other.name}: {shared:.0%} of the node histogram, same layer roles")
PY
```

---

## 5. Decide

### Merge

All green, or green with one amber you can live with.

```markdown
Merged. Thanks for **<Effect Name>**.

- check: 0 errors, <n> warning(s), deterministic at 4 sample times
- budget: <sprites> sprites, <mesh> mesh particles, <nodes> nodes, <dur> s, <mb> MiB baked
- viewer: <fps> fps, <calls> draw calls
- reads as: <one sentence on what the silhouette says>

It is in the Community view now, credited to @<github>. The published index and previews
rebuild on the next push to main.
```

Then squash-merge with the contributor's `Signed-off-by:` preserved, and let the main-branch
workflow rebuild `community/index.json` and the previews.

### Changes requested

Anything red that tuning can fix, or two or more ambers. Be specific: name the node, the
parameter and the target value. Never write "polish the timing".

```markdown
Close. Three things before this can land:

1. **<rule>** - `<node id>`: <what is wrong>. <exact change>, e.g. set `detail: 3` and
   `width_profile: "bulge"`.
2. **<rule>** - <same shape>.
3. **<rule>** - <same shape>.

Everything else looks good: <one honest positive>. Push to the same branch and CI will
re-run; ping me when it is green.
```

### Decline

An idea that duplicates the library, or a house-style violation that tuning cannot fix.

```markdown
Declining this one, and it is not about the craft.

**<Effect Name>** is structurally the same effect as `<existing>`: <the evidence - node
histogram, layer roles, silhouette, palette>. The library already covers that idea.

What would land: <one concrete, different direction - a different element, a different
silhouette, a different phase structure>. `CONTRIBUTING.md` section 2 has the bar. Please do
send another.
```

Always leave the door open. Contributors are agents driven by people; a decline with a
direction usually comes back as a better effect.

---

## 6. Promoting a community effect to Core

A community effect that a lot of people load, that has no warnings, and that fills a gap in
`examples/effects/` can be promoted.

1. Open your own PR; do not rewrite history on the contributor's.
2. `git mv community/effects/<slug>.json examples/effects/<slug>.json`.
3. Keep `metadata.author` exactly as it is. Promotion never removes credit.
4. Add `metadata.tags` if they are thin, and make sure the controls are named well - Core
   effects are what new users see first.
5. Rebuild the index: `python tools/build_community_index.py`.
6. `python -m aetherfx.community lint-library` must still pass.
7. Mention the promotion in the original PR thread so the contributor sees it.

A promoted effect stays MIT and stays credited. That is the whole deal.
