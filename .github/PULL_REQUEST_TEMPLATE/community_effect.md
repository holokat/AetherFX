---
name: Community effect
about: Contribute one effect to community/effects/
title: 'community: <Effect Name>'
labels: community-effect
---

<!-- Read CONTRIBUTING.md first. It is written for the agent doing the work. -->

## The effect

**Name:**
**File:** `community/effects/<slug>.json`

One paragraph: what it is, when a game would play it, what the controls do.

<!-- your paragraph here -->

## How it was made

**Prompt (verbatim):**

```
<the prompt you gave the agent>
```

**Model:** <e.g. Claude Opus 4.5, GPT-5.2, …>
**Generator script:** <path under tools/generators/community/, or "none">
**Iterations:** <how many render-and-look passes>

## Check output

```
$ python -m aetherfx.community check community/effects/<slug>.json --previews out/previews
<paste the whole report, including the budget line>
```

## Checklist

- [ ] Exactly one new file under `community/effects/` (plus at most one generator script)
- [ ] `python -m aetherfx.community check …` exits `0`
- [ ] `metadata.author.github` is my GitHub username
- [ ] `metadata.license` is `"MIT"` and I license this effect under it
- [ ] `metadata.tags` and a one-line description are set
- [ ] At least 3 style controls, named in artist words
- [ ] Fully procedural: no file textures, no meshes, no paths, no URLs
- [ ] **No reference art, screenshots or preview media committed** (previews are CI artifacts)
- [ ] No copyrighted characters, logos, or religious / political / offensive iconography
- [ ] Not a palette swap or near-duplicate of an existing effect (or it is a declared variant set)
- [ ] A projectile, if any, ends in an impact
- [ ] `git commit -s` - the `Signed-off-by:` line is in my commits
