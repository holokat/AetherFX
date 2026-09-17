# Studio generation worker protocol

The studio's "session" generator queues jobs under `<output_dir>/jobs/`.
A worker (an interactive Claude Code agent with the user's credentials)
executes them against the studio's engine through the studio HTTP API.

Worker loop:

1. `touch jobs/worker.heartbeat` at least every 2 minutes while alive.
2. Wait for a file in `jobs/pending/*.json`. Claim it by moving it to
   `jobs/running/<id>.json`. Read `prompt`, `mode` (`new` | `modify`),
   `active_effect` and `reference_images` (absolute paths, may be empty). When
   reference images are present this is a Mode A reconstruction: view each image
   (Read tool), post a semantic decomposition as a status event (category, scale,
   style, colours, primary forms, layers with role/primitive/depth/motion
   hypothesis, phases), build to match it, and use `compare_reference` with the
   first image after each preview.
3. Drive the engine through `POST <studio_url>/api/tool` with body
   `{"name": "<tool>", "args": {...}}` (same tools as docs/AGENT_API.md).
   Follow the authoring guide (`aetherfx.studio.authoring_guide`):
   create_effect -> layers -> nodes -> simulate -> render_preview -> look at
   the contact sheet PNG (open the file) -> adjust -> render again -> done.
4. Append one JSON object per line to `jobs/<id>.events.jsonl` as you go:
   `{"kind":"status","text":...}`, `{"kind":"tool_call","name":...,"args":{...}}`,
   `{"kind":"tool_result","name":...,"ok":true,"summary":...}`,
   `{"kind":"image","path":"/abs/render.png","caption":...}`,
   `{"kind":"text","text":...}`, and finally
   `{"kind":"done","effect_id":"fx_N","summary":"...","tool_calls":N,"renders":N}`.
5. If `jobs/<id>.cancel` appears, stop and write a `done` event with
   `"summary": "cancelled"`.
6. Move the job file to `jobs/done/<id>.json` and go back to step 2.
