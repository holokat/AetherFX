"""AetherFX Studio HTTP surface, exercised against the real engine.

The studio is a thin shell over docs/AGENT_API.md, so the interesting failures
are integration failures: a route that does not reach the engine, a render that
never lands on disk, a path guard that lets the browser out of the session
output directory.  Everything here therefore runs against a real
``aetherfx serve`` and skips when the binary is missing.

One module-scoped :class:`TestClient` means one engine process for the whole
file, which keeps it well under ten seconds.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aetherfx.client import find_binary
from aetherfx.studio.server import StudioConfig, create_app, slugify

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "effects"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "effects"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

BINARY = find_binary(None)

pytestmark = pytest.mark.skipif(
    not (BINARY and Path(BINARY).is_file()),
    reason="the AetherFX engine binary is not available ($AETHERFX_BINARY / build/bin/aetherfx)",
)


@pytest.fixture(scope="module")
def output_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("studio-out")


@pytest.fixture(scope="module")
def examples_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The shipped library plus the test-only Fireball, which is a hidden built-in in this studio."""
    folder = tmp_path_factory.mktemp("studio-examples")
    for source in list(EXAMPLES_DIR.glob("*.json")) + list(FIXTURES_DIR.glob("*.json")):
        shutil.copy(source, folder / source.name)
    return folder


@pytest.fixture(scope="module")
def fireball(examples_dir: Path) -> Path:
    return examples_dir / "fireball.json"


@pytest.fixture(scope="module")
def client(output_dir: Path, examples_dir: Path):
    """The studio with a real engine and no generator backend."""
    config = StudioConfig(
        output_dir=output_dir,
        examples_dir=examples_dir,
        binary=BINARY,
        generator="none",
        port=8779,
    )
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def loaded(client: TestClient, fireball: Path) -> dict:
    """Load the fireball example once; the whole module edits that effect."""
    response = client.post("/api/effects/load", json={"path": str(fireball)})
    assert response.status_code == 200, response.text
    return response.json()


# =====================================================================
# shell
# =====================================================================


def test_index_and_static_assets_are_served(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "AetherFX Studio" in page.text
    for asset in ("/static/app.js", "/static/style.css"):
        response = client.get(asset)
        assert response.status_code == 200, asset
        assert response.content


def test_status_reports_a_live_engine(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert status["engine"]["ok"] is True
    assert status["engine"]["binary"]
    assert status["generator"]["name"] == "none"
    assert status["generator"]["available"] is False
    assert status["output_dir"]
    assert status["studio_url"].startswith("http://")


def test_effects_listing_includes_the_examples(client: TestClient) -> None:
    listing = client.get("/api/effects").json()
    names = {item["name"] for item in listing["examples"]}
    assert {"fireball", "fire_aoe", "lightning_strike"} <= names
    assert isinstance(listing["saved"], list)
    assert isinstance(listing["open"], list)


# =====================================================================
# the effect
# =====================================================================


def test_load_makes_the_effect_active(client: TestClient, loaded: dict) -> None:
    assert loaded["effect_id"]
    assert loaded["name"] == "Fireball"
    assert loaded["duration"] == pytest.approx(2.5)
    active = client.get("/api/status").json()["active_effect"]
    assert active["effect_id"] == loaded["effect_id"]
    assert active["name"] == "Fireball"


def test_effect_has_nodes_layers_timeline_and_statistics(client: TestClient, loaded: dict) -> None:
    payload = client.get("/api/effect").json()
    graph = payload["graph"]
    assert {node["id"] for node in graph["nodes"]} >= {"flames", "flame_ps", "core"}
    assert {layer["id"] for layer in graph["layers"]} >= {"core_layer", "trail_layer"}
    assert payload["effect"]["name"] == "Fireball"
    assert [phase["name"] for phase in payload["timeline"]["phases"]] == ["activation", "sustain", "decay"]
    assert "statistics" in payload
    # the per-node plan dump is trimmed away; the summary survives
    assert "nodes" not in (payload["statistics"].get("plan") or {})


# =====================================================================
# controls (the Style panel)
# =====================================================================


def test_loading_an_effect_gives_it_default_controls(client: TestClient, loaded: dict) -> None:
    listed = client.get("/api/controls").json()
    assert listed["count"] > 0
    assert listed["groups"][0] == "Global"
    by_id = {control["id"]: control for control in listed["controls"]}
    assert "global_intensity" in by_id
    assert by_id["global_intensity"]["value"] == pytest.approx(by_id["global_intensity"]["default"])
    assert by_id["global_intensity"]["unit"] == "x"
    assert by_id["global_hue"]["unit"] == "deg"
    assert by_id["global_hue"]["min"] == pytest.approx(-180.0)
    assert by_id["global_intensity"]["bindings"]

    # Generating them is not an edit the user has to save: the working copy is
    # still clean and the built-in file on disk was never touched.
    assert client.get("/api/status").json()["active_effect"]["dirty"] is False
    document = json.loads(Path(loaded["path"]).read_text(encoding="utf-8"))
    assert "controls" not in document


def test_setting_a_control_is_visible_and_undoable(client: TestClient, loaded: dict) -> None:
    before = client.get("/api/node/flame_ps").json()["effective_parameters"]["emissive"]

    response = client.post("/api/controls/global_intensity", json={"value": 2.0})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["control"]["value"] == pytest.approx(2.0)
    assert body["diagnostics"]["ok"] is True

    # the control moved; the authored parameter did not
    assert client.get("/api/node/flame_ps").json()["effective_parameters"]["emissive"] == pytest.approx(before)
    listed = {c["id"]: c for c in client.get("/api/controls").json()["controls"]}
    assert listed["global_intensity"]["value"] == pytest.approx(2.0)
    # ... and the document carries the value, which is what "Save as" writes
    saved = {c["id"]: c for c in client.get("/api/effect").json()["effect"]["controls"]}
    assert saved["global_intensity"]["value"] == pytest.approx(2.0)

    assert client.post("/api/undo", json={}).json()["ok"] is True
    listed = {c["id"]: c for c in client.get("/api/controls").json()["controls"]}
    assert listed["global_intensity"]["value"] == pytest.approx(1.0)


def test_a_control_out_of_range_is_a_client_error(client: TestClient, loaded: dict) -> None:
    response = client.post("/api/controls/global_intensity", json={"value": 99.0})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "E024"


def test_an_unknown_control_is_a_client_error(client: TestClient, loaded: dict) -> None:
    response = client.post("/api/controls/does_not_exist", json={"value": 1.0})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "E021"


def test_a_control_needs_a_numeric_value(client: TestClient, loaded: dict) -> None:
    assert client.post("/api/controls/global_intensity", json={}).status_code == 400
    assert client.post("/api/controls/global_intensity", json={"value": "loud"}).status_code == 400


def test_reset_puts_every_control_back(client: TestClient, loaded: dict) -> None:
    client.post("/api/controls/global_intensity", json={"value": 2.0})
    client.post("/api/controls/global_size", json={"value": 0.5})

    reset = client.post("/api/controls/reset", json={}).json()
    assert reset["ok"] is True
    assert reset["reset"] == 2
    for control in reset["controls"]:
        assert control["value"] == pytest.approx(control["default"])


def test_generate_is_idempotent(client: TestClient, loaded: dict) -> None:
    before = client.get("/api/controls").json()["count"]
    generated = client.post("/api/controls/generate", json={}).json()
    assert generated["added"] == 0
    assert generated["count"] == before
    assert generated["diagnostics"]["ok"] is True


def test_missing_node_is_a_client_error(client: TestClient, loaded: dict) -> None:
    response = client.get("/api/node/does_not_exist")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "E008"


def test_node_exposes_spec_and_effective_parameters(client: TestClient, loaded: dict) -> None:
    node = client.get("/api/node/flames").json()
    assert node["node"]["type"] == "emitter"
    assert node["effective_parameters"]["rate"] == pytest.approx(180.0)
    assert node["spec"]["parameters"]["rate"]["type"] == "float"
    assert node["resolved_inputs"]["particle"][0]["node"] == "flame_ps"
    assert node["diagnostics"]["ok"] is True


def test_set_parameter_is_visible_and_undoable(client: TestClient, loaded: dict) -> None:
    before = client.get("/api/node/flames").json()["effective_parameters"]["rate"]

    response = client.post("/api/param", json={"node_id": "flames", "name": "rate", "value": 42.5})
    assert response.status_code == 200, response.text
    assert response.json()["diagnostics"]["ok"] is True

    assert client.get("/api/node/flames").json()["effective_parameters"]["rate"] == pytest.approx(42.5)

    assert client.post("/api/undo", json={}).json()["ok"] is True
    assert client.get("/api/node/flames").json()["effective_parameters"]["rate"] == pytest.approx(before)


def test_rejected_parameter_returns_the_engine_error(client: TestClient, loaded: dict) -> None:
    response = client.post("/api/param", json={"node_id": "flames", "name": "rate", "value": "fast"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "E005"
    assert error["node"] == "flames"
    assert error["param"] == "rate"


def test_node_property_toggles_enabled(client: TestClient, loaded: dict) -> None:
    assert client.post("/api/node/property", json={"node_id": "smoke", "enabled": False}).status_code == 200
    nodes = {node["id"]: node for node in client.get("/api/effect").json()["graph"]["nodes"]}
    assert nodes["smoke"]["enabled"] is False
    assert client.post("/api/undo", json={}).json()["ok"] is True


def test_save_writes_into_the_output_directory(client: TestClient, output_dir: Path, loaded: dict) -> None:
    # The working copy of a built-in is saved under a new name; the built-in file is never touched.
    builtin = Path(loaded["path"])
    before = builtin.stat().st_mtime
    result = client.post("/api/effects/save", json={}).json()
    saved = Path(result["path"])
    assert saved.is_file()
    assert saved.parent == (output_dir / "effects").resolve()
    assert result["name"] == "Fireball copy"
    assert saved.name == slugify("Fireball copy") + ".json"
    assert builtin.stat().st_mtime == before
    listing = client.get("/api/effects").json()
    assert {item["name"] for item in listing["saved"]} == {"fireball_copy"}
    names = {(item["name"], item["builtin"]) for item in listing["library"]}
    # the first-milestone Fireball is a hidden built-in (metadata.library.hidden): it stays loadable by
    # path but is not offered in the Library; a saved copy of it is the user's own and is listed
    assert ("Fireball", True) not in names and ("Fireball copy", False) in names
    assert ("Fire Bolt", True) in names


def test_builtin_library_entries_are_protected(client: TestClient, loaded: dict) -> None:
    builtin = loaded["path"]
    assert client.post("/api/effects/load", json={"path": builtin}).status_code == 200  # fresh working copy
    # explicit path into the built-in library via the agent tool passthrough
    response = client.post("/api/tool", json={"name": "save_effect", "args": {"path": builtin}})
    assert response.status_code == 403, response.text
    assert "protected" in response.json()["error"]["message"]
    # the studio's own save-as refuses it too
    response = client.post("/api/effects/save", json={"path": builtin})
    assert response.status_code == 403
    # loading a built-in opens a working copy, not the file: the document has no path of its own
    working = client.get("/api/effects").json()["working"]
    assert working["source"]["builtin"] is True
    assert client.get("/api/effect").json()["working_source"]["path"] == builtin


# =====================================================================
# rendering
# =====================================================================


def test_frame_returns_png_bytes(client: TestClient, loaded: dict) -> None:
    response = client.get("/api/frame", params={"time": 0.5, "width": 128, "height": 128})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(PNG_MAGIC)


def test_preview_renders_servable_frames(client: TestClient, loaded: dict) -> None:
    payload = client.post(
        "/api/preview", json={"fps": 4, "start": 0, "end": 0.5, "width": 128, "height": 128}
    ).json()
    assert payload["count"] >= 2
    assert len(payload["frames"]) == payload["count"]
    assert payload["fps"] == pytest.approx(4)
    for url in payload["frames"]:
        assert url.startswith("/api/file?path=")
        frame = client.get(url)
        assert frame.status_code == 200
        assert frame.content.startswith(PNG_MAGIC)
    if payload["contact_sheet"]:
        assert client.get(payload["contact_sheet"]).content.startswith(PNG_MAGIC)


def test_frame_needs_an_effect(output_dir: Path) -> None:
    config = StudioConfig(output_dir=output_dir / "empty", examples_dir=EXAMPLES_DIR, binary=BINARY, generator="none")
    with TestClient(create_app(config)) as fresh:
        assert fresh.get("/api/effect").status_code == 404
        assert fresh.get("/api/frame", params={"time": 0}).status_code == 404


# =====================================================================
# path guard
# =====================================================================


@pytest.mark.parametrize(
    "path",
    [str(EXAMPLES_DIR / "fire_aoe.json"), "../../etc/hosts", "/etc/hosts", str(REPO_ROOT / "README.md")],
)
def test_file_refuses_paths_outside_the_output_dir(client: TestClient, path: str) -> None:
    response = client.get("/api/file", params={"path": path})
    assert response.status_code == 403, path
    assert response.json()["error"]["code"] == "forbidden"


def test_file_serves_a_rendered_frame(client: TestClient, output_dir: Path, loaded: dict) -> None:
    client.get("/api/frame", params={"time": 0.25, "width": 64, "height": 64})
    rendered = sorted((output_dir / "studio_frames").glob("*.png"))
    assert rendered
    response = client.get("/api/file", params={"path": str(rendered[0])})
    assert response.status_code == 200
    assert response.content.startswith(PNG_MAGIC)


# =====================================================================
# vocabulary, raw tools, generation
# =====================================================================


def test_vocabulary_is_served(client: TestClient) -> None:
    vocabulary = client.get("/api/vocabulary").json()
    assert "emitter" in vocabulary["node_types"]
    assert vocabulary["schema_version"]


def test_tools_list_and_raw_tool_call(client: TestClient, loaded: dict) -> None:
    names = {tool["name"] for tool in client.get("/api/tools").json()["tools"]}
    assert {"inspect_graph", "set_parameter", "render_frame"} <= names

    result = client.post("/api/tool", json={"name": "get_parameter", "args": {"node_id": "flames", "name": "rate"}})
    assert result.status_code == 200, result.text
    assert result.json()["value"] == pytest.approx(180.0)

    unknown = client.post("/api/tool", json={"name": "no_such_tool", "args": {}})
    assert unknown.status_code == 404
    assert "error" in unknown.json()


def test_generate_is_unavailable_without_a_backend(client: TestClient) -> None:
    response = client.post("/api/generate", json={"prompt": "a blue magic missile", "mode": "new"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "generator_unavailable"


def test_unknown_job_is_a_404(client: TestClient) -> None:
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs/nope/cancel", json={}).status_code == 404


# =====================================================================
# reference-image attachments
# =====================================================================


def make_png(size: tuple[int, int] = (24, 18), color: str = "#c0392b") -> bytes:
    """A tiny in-memory PNG; the store only accepts real images."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def attachment(client: TestClient) -> dict:
    response = client.post("/api/attachments", files={"file": ("reference.png", make_png(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()


def test_attachment_upload_returns_an_id_and_urls(attachment: dict) -> None:
    assert attachment["id"]
    assert attachment["name"] == "reference.png"
    assert (attachment["width"], attachment["height"]) == (24, 18)
    assert attachment["url"] == "/api/attachments/" + attachment["id"]
    assert attachment["thumb_url"] == attachment["url"] + "/thumb"


def test_attachment_serves_the_image_and_a_png_thumbnail(client: TestClient, attachment: dict) -> None:
    full = client.get(attachment["url"])
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("image/png")
    assert full.content.startswith(PNG_MAGIC)

    thumb = client.get(attachment["thumb_url"])
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/png")
    assert thumb.content.startswith(PNG_MAGIC)


def test_attachment_rejects_a_file_that_is_not_an_image(client: TestClient) -> None:
    response = client.post("/api/attachments", files={"file": ("notes.txt", b"not an image", "text/plain")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_unknown_attachment_is_a_404(client: TestClient) -> None:
    for path in ("/api/attachments/nope", "/api/attachments/19990101-000000-deadbeef/thumb"):
        assert client.get(path).status_code == 404, path


def test_attachment_delete_removes_it(client: TestClient, attachment: dict) -> None:
    assert client.delete(attachment["url"]).status_code == 200
    assert client.get(attachment["url"]).status_code == 404
    assert client.get(attachment["thumb_url"]).status_code == 404


def test_generate_validates_attachments_before_the_generator(client: TestClient, attachment: dict) -> None:
    unknown = client.post("/api/generate", json={"prompt": "like this", "attachments": ["19990101-000000-deadbeef"]})
    assert unknown.status_code == 400
    assert "unknown attachment" in unknown.json()["error"]["message"]

    not_a_list = client.post("/api/generate", json={"prompt": "like this", "attachments": "abc"})
    assert not_a_list.status_code == 400

    # a resolvable attachment gets past validation and stops at the missing backend
    accepted = client.post("/api/generate", json={"prompt": "like this", "attachments": [attachment["id"]]})
    assert accepted.status_code == 503
    assert accepted.json()["error"]["code"] == "generator_unavailable"


# =====================================================================
# export
# =====================================================================


def test_export_targets_lists_every_engine(client: TestClient) -> None:
    targets = client.get("/api/export/targets").json()["targets"]
    assert {target["id"] for target in targets} == {"unreal", "unity", "godot", "package", "flipbook"}
    for target in targets:
        assert target["label"] and target["hint"]
        assert "destination" in target


def test_export_package_without_a_destination_downloads_a_zip(client: TestClient, loaded: dict) -> None:
    info = client.post("/api/export", json={"target": "package"})
    assert info.status_code == 200, info.text
    payload = info.json()
    assert payload["download"].startswith("/api/file?path=")
    assert payload["path"].endswith(".zip")

    download = client.get(payload["download"])
    assert download.status_code == 200
    assert download.content.startswith(b"PK")
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]


def test_export_into_a_unity_project(client: TestClient, tmp_path: Path, loaded: dict) -> None:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    response = client.post("/api/export", json={"target": "unity", "destination": str(project), "remember": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    slug = slugify(client.get("/api/status").json()["active_effect"]["name"])
    package = project / "Assets" / "AetherFX" / "Effects" / f"{slug}.aetherfx"
    assert Path(payload["path"]) == package
    assert (package / "runtime.json").is_file()
    assert payload["download"] is None
    assert payload["remembered"] is True

    remembered = client.get("/api/export/targets").json()
    assert remembered["destinations"]["unity"] == str(project)


def test_export_refuses_a_destination_that_is_not_a_project(client: TestClient, tmp_path: Path, loaded: dict) -> None:
    empty = tmp_path / "not-a-project"
    empty.mkdir()
    response = client.post("/api/export", json={"target": "godot", "destination": str(empty)})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"

    missing = client.post("/api/export", json={"target": "unity", "destination": str(tmp_path / "gone")})
    assert missing.status_code == 400

    unknown = client.post("/api/export", json={"target": "nope"})
    assert unknown.status_code == 400


def test_a_freshly_loaded_effect_is_not_dirty(client: TestClient, loaded: dict) -> None:
    # Loading a second library entry discards the first working copy; that housekeeping must not
    # mark the new working copy as edited (it made "unsaved changes were discarded" fire on every switch).
    library = client.get("/api/effects").json()["library"]
    other = next(item for item in library if item["builtin"] and item["path"] != loaded["path"])
    assert client.post("/api/effects/load", json={"path": other["path"]}).status_code == 200
    active = client.get("/api/status").json()["active_effect"]
    assert active["name"] == other["name"]
    assert active["dirty"] is False
    # and again, back to the first
    assert client.post("/api/effects/load", json={"path": loaded["path"]}).status_code == 200
    assert client.get("/api/status").json()["active_effect"]["dirty"] is False


def test_the_generated_controls_include_a_speed_that_changes_the_wall_duration(
    client: TestClient, loaded: dict
) -> None:
    """Speed is a control like any other, except that what it drives is the
    document itself: how long the effect takes to play (docs/CONTROLS.md)."""
    listed = client.get("/api/controls").json()
    by_id = {control["id"]: control for control in listed["controls"]}
    assert "global_speed" in by_id
    speed = by_id["global_speed"]
    assert speed["group"] == "Global"
    assert speed["label"] == "Speed"
    assert speed["unit"] == "x"
    assert (speed["min"], speed["max"], speed["default"]) == (
        pytest.approx(0.25), pytest.approx(4.0), pytest.approx(1.0)
    )

    timeline = client.get("/api/effect").json()["timeline"]
    duration = timeline["duration"]
    assert timeline["time_scale"] == pytest.approx(1.0)
    assert timeline["wall_duration"] == pytest.approx(duration)

    assert client.post("/api/controls/global_speed", json={"value": 2.0}).status_code == 200
    timeline = client.get("/api/effect").json()["timeline"]
    # Twice as fast: the same effect seconds over half the wall clock. The
    # timeline itself does not move - that is where the keyframes live.
    assert timeline["duration"] == pytest.approx(duration)
    assert timeline["time_scale"] == pytest.approx(2.0)
    assert timeline["wall_duration"] == pytest.approx(duration / 2.0)

    assert client.post("/api/controls/global_speed", json={"value": 0.5}).status_code == 200
    assert client.get("/api/effect").json()["timeline"]["wall_duration"] == pytest.approx(duration * 2.0)

    # Out of its range is a client error, like every other control.
    assert client.post("/api/controls/global_speed", json={"value": 9.0}).status_code == 400

    # And the value rides along in the document, which is what "Save as" writes.
    saved = {c["id"]: c for c in client.get("/api/effect").json()["effect"]["controls"]}
    assert saved["global_speed"]["value"] == pytest.approx(0.5)


def test_a_speed_control_never_rewrites_the_authored_document(client: TestClient, loaded: dict) -> None:
    before = client.get("/api/effect").json()["timeline"]["time_scale"]
    client.post("/api/controls/global_speed", json={"value": 3.0})
    document = client.get("/api/effect").json()["effect"]
    # The authored time_scale is untouched: only the compiler folds the control
    # in, and only into its own copy.
    assert document.get("time_scale", 1.0) == pytest.approx(1.0)
    assert client.get("/api/effect").json()["timeline"]["time_scale"] == pytest.approx(3.0)

    # ... so moving it is an ordinary, undoable document edit.
    assert client.post("/api/undo", json={}).json()["ok"] is True
    assert client.get("/api/effect").json()["timeline"]["time_scale"] == pytest.approx(before)


def test_an_edit_that_is_undone_is_not_a_change(client: TestClient, loaded: dict) -> None:
    # Dragging a style slider and resetting it leaves the document exactly as loaded: that must not
    # report unsaved changes (the revision counter alone said "dirty" and toasted a discard warning).
    assert client.post("/api/effects/load", json={"path": loaded["path"]}).status_code == 200
    assert client.get("/api/status").json()["active_effect"]["dirty"] is False
    controls = client.get("/api/controls").json()
    controls = controls.get("controls", controls)
    target = next(c for c in controls if c["id"] == "global_intensity")
    assert client.post(f"/api/controls/{target['id']}", json={"value": 1.5}).status_code == 200
    assert client.get("/api/status").json()["active_effect"]["dirty"] is True
    assert client.post("/api/controls/reset", json={}).status_code == 200
    assert client.get("/api/status").json()["active_effect"]["dirty"] is False
