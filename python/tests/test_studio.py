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

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aetherfx.client import find_binary
from aetherfx.studio.server import StudioConfig, create_app, slugify

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "effects"
FIREBALL = EXAMPLES_DIR / "fireball.json"
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
def client(output_dir: Path):
    """The studio with a real engine and no generator backend."""
    config = StudioConfig(
        output_dir=output_dir,
        examples_dir=EXAMPLES_DIR,
        binary=BINARY,
        generator="none",
        port=8779,
    )
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def loaded(client: TestClient) -> dict:
    """Load the fireball example once; the whole module edits that effect."""
    response = client.post("/api/effects/load", json={"path": str(FIREBALL)})
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
    result = client.post("/api/effects/save", json={}).json()
    saved = Path(result["path"])
    assert saved.is_file()
    assert saved.parent == (output_dir / "effects").resolve()
    assert saved.name == slugify("Fireball") + ".json"
    assert {item["name"] for item in client.get("/api/effects").json()["saved"]} == {"fireball"}


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
    [str(FIREBALL), "../../etc/hosts", "/etc/hosts", str(REPO_ROOT / "README.md")],
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
