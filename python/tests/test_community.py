"""The community collection: credit metadata, the contribution check, the index, the studio.

Three layers, deliberately separated:

* pure functions (author parsing, tags, licence, the house-style lint, the index builder) run
  everywhere and are where the hostile inputs are exercised;
* the full :func:`check_effect` needs the native library and skips without it;
* the studio endpoints need the engine binary and skip without it, exactly like test_studio.py.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aetherfx import community, native
from aetherfx.client import find_binary
from aetherfx.studio.community_index import RemoteIndex, RemoteIndexError, normalise_index
from aetherfx.studio.server import StudioConfig, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMUNITY_DIR = REPO_ROOT / "community" / "effects"
EXAMPLES_DIR = REPO_ROOT / "examples" / "effects"
SAMPLE = COMMUNITY_DIR / "arcane_missile.json"
BINARY = find_binary(None)

needs_engine = pytest.mark.skipif(
    not native.is_available(), reason="libaetherfx is not available ($AETHERFX_LIBRARY / build/)"
)
needs_binary = pytest.mark.skipif(
    not (BINARY and Path(BINARY).is_file()), reason="the AetherFX engine binary is not available"
)


# =====================================================================
# credit metadata
# =====================================================================


def test_author_needs_a_github_username() -> None:
    author = community.parse_author({"metadata": {"author": {"github": "octocat"}}})
    assert author is not None
    assert author.github == "octocat"
    assert author.url == "https://github.com/octocat"
    assert author.display == "octocat"          # falls back to the username
    assert author.to_json()["url"] == "https://github.com/octocat"


def test_author_display_name_is_optional() -> None:
    author = community.parse_author({"author": {"github": "octo-cat-9", "name": "Octo Cat"}})
    assert author is not None and author.display == "Octo Cat"
    assert author.links() == [{"label": "github.com/octo-cat-9", "url": "https://github.com/octo-cat-9"}]


def test_no_author_block_is_not_an_error() -> None:
    assert community.parse_author({"metadata": {}}) is None
    assert community.parse_author({}) is None
    assert community.parse_author(None) is None


@pytest.mark.parametrize(
    "block, expect",
    [
        ({}, "github"),                                        # missing username
        ({"github": ""}, "empty"),
        ({"github": "not a username"}, "GitHub username"),     # spaces
        ({"github": "a" * 40}, "longer than"),                 # over GitHub's 39
        ({"github": "octo/cat"}, "GitHub username"),           # path traversal in a URL
        ({"github": "octocat", "x": "@evil"}, "unknown field"),         # dropped fields stay dropped
        ({"github": "octocat", "url": "https://evil.test"}, "unknown field"),
        ({"github": "octocat", "email": "a@b.c"}, "unknown field"),
        ({"github": "<script>alert(1)</script>"}, "markup"),
        ({"github": "octocat", "name": "<img src=x onerror=alert(1)>"}, "markup"),
        ({"github": "octocat", "name": "javascript:alert(1)"}, "link"),
        ({"github": "octocat", "name": "see http://evil.test"}, "link"),
        ({"github": "octocat", "name": "logo.png"}, "file name"),
        ({"github": "octocat", "name": "a" * 61}, "longer than"),
        ({"github": "octo\ncat"}, "control characters"),
    ],
)
def test_hostile_credit_blocks_are_rejected(block: dict, expect: str) -> None:
    with pytest.raises(community.AuthorError) as excinfo:
        community.parse_author({"metadata": {"author": block}})
    assert expect in str(excinfo.value)


def test_author_as_a_bare_string_is_rejected() -> None:
    with pytest.raises(community.AuthorError):
        community.parse_author({"metadata": {"author": "octocat"}})


def test_author_html_is_escaped_and_only_links_github() -> None:
    author = community.Author(github="octocat", name='Bobby "><script>')
    html = author.to_html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&quot;" in html
    assert html.count("href=") == 1 and 'href="https://github.com/octocat"' in html
    assert 'rel="noopener noreferrer"' in html and 'target="_blank"' in html


def test_tags_must_be_lowercase_slugs() -> None:
    assert community.parse_tags({"metadata": {"tags": ["fire", "aoe", "fire"]}}) == ["fire", "aoe"]
    assert community.parse_tags({"metadata": {}}) == []
    for bad in (["Fire"], ["a b"], ["über"], [1], "fire", ["x" * 25], ["a"] * 9):
        with pytest.raises(community.AuthorError):
            community.parse_tags({"metadata": {"tags": bad}})


def test_license_must_be_mit() -> None:
    assert community.parse_license({"metadata": {"license": "MIT"}}) == "MIT"
    assert community.parse_license({"metadata": {"license": " mit "}}) == "MIT"
    assert community.parse_license({"metadata": {}}) is None
    for bad in ("Apache-2.0", "TBD", "CC0-1.0", 7):
        with pytest.raises(community.AuthorError):
            community.parse_license({"metadata": {"license": bad}})


# =====================================================================
# static rules: assets and the house style
# =====================================================================


def _document(**overrides) -> dict:
    """A minimal but valid-looking contribution."""
    doc = {
        "schema_version": "0.1.0",
        "name": "Test Effect",
        "description": "A small test effect.",
        "duration": 2.0,
        "seed": 1,
        "nodes": [],
        "controls": [{"id": f"c{i}", "label": f"C{i}", "bindings": []} for i in range(3)],
        "metadata": {"author": {"github": "octocat"}, "license": "MIT", "tags": ["test"]},
    }
    doc.update(overrides)
    return doc


def _codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def test_file_textures_paths_and_urls_are_rejected() -> None:
    doc = _document(nodes=[
        {"id": "tex", "type": "texture", "parameters": {"source": "file", "path": "../sheets/fire.png"}},
        {"id": "m", "type": "mesh", "parameters": {"source": "file"}},
    ])
    codes = _codes(community.asset_findings(doc))
    assert {"asset.file_texture", "asset.path", "asset.file_mesh", "asset.media"} <= codes


def test_media_file_names_and_links_are_rejected_anywhere() -> None:
    assert "asset.media" in _codes(community.asset_findings(
        _document(metadata={"reference": "out/references/hero_sheet.jpg"})))
    assert "asset.url" in _codes(community.asset_findings(
        _document(description="see https://example.test/ref")))
    assert "asset.embedded" in _codes(community.asset_findings(
        _document(metadata={"blob": "QUJDRA==" * 60})))


def test_a_clean_document_has_no_asset_findings() -> None:
    assert community.asset_findings(_document()) == []


def test_four_armed_stars_are_errors() -> None:
    def texture(params):
        return _document(nodes=[{"id": "tex", "type": "texture", "parameters": {
            "graph": {"nodes": [{"id": "s", "op": "star", "params": params}], "output": "s"}}}])

    assert "style.four_arms" in _codes(community.house_style_findings(texture({"points": 4})))
    assert "style.star_default" in _codes(community.house_style_findings(texture({})))
    assert community.house_style_findings(texture({"points": 6})) == []


def test_four_armed_spokes_are_errors_too() -> None:
    doc = _document(nodes=[{"id": "tex", "type": "texture", "parameters": {
        "graph": {"nodes": [{"id": "k", "op": "spokes", "params": {"count": 4}}], "output": "k"}}}])
    assert "style.four_arms" in _codes(community.house_style_findings(doc))


def test_two_arms_warn_only_on_a_big_sprite() -> None:
    def doc(size):
        return _document(nodes=[
            {"id": "tex", "type": "texture", "parameters": {
                "graph": {"nodes": [{"id": "s", "op": "star", "params": {"points": 2}}], "output": "s"}}},
            {"id": "ps", "type": "particle_system", "parameters": {"size": size},
             "inputs": {"sprite": "tex"}},
        ])

    assert "style.two_arms" in _codes(community.house_style_findings(doc(0.9)))
    assert community.house_style_findings(doc(0.2)) == []


def test_a_long_uniform_beam_reads_as_a_tube() -> None:
    tube = _document(nodes=[{"id": "b", "type": "beam", "parameters": {"duration": 1.5}}])
    assert "style.tube_beam" in _codes(community.house_style_findings(tube))

    shaped = _document(nodes=[{"id": "b", "type": "beam", "parameters": {
        "duration": 1.5, "detail": 3, "width_profile": "bulge"}}])
    assert community.house_style_findings(shaped) == []

    brief = _document(nodes=[{"id": "b", "type": "beam", "parameters": {"duration": 0.2}}])
    assert community.house_style_findings(brief) == []


def test_a_wide_ribbon_needs_a_material_texture() -> None:
    bare = _document(nodes=[
        {"id": "mat", "type": "material", "parameters": {}},
        {"id": "t", "type": "trail", "parameters": {"width": 0.3}, "inputs": {"material": "mat"}},
    ])
    assert "style.hard_ribbon" in _codes(community.house_style_findings(bare))

    textured = _document(nodes=[
        {"id": "tex", "type": "texture", "parameters": {}},
        {"id": "mat", "type": "material", "parameters": {}, "inputs": {"base_texture": "tex"}},
        {"id": "t", "type": "trail", "parameters": {"width": 0.3}, "inputs": {"material": "mat"}},
    ])
    assert community.house_style_findings(textured) == []


def test_a_keyframed_width_is_judged_at_its_peak() -> None:
    doc = _document(nodes=[
        {"id": "mat", "type": "material", "parameters": {}},
        {"id": "t", "type": "trail", "inputs": {"material": "mat"}, "parameters": {
            "width": {"value": 0.05, "track": [{"time": 0.0, "value": 0.05}, {"time": 1.0, "value": 0.4}]}}},
    ])
    assert "style.hard_ribbon" in _codes(community.house_style_findings(doc))


def _projectile(**extra) -> dict:
    core = {"id": "core", "type": "mesh", "parameters": {"position": {"value": [-4, 1.5, 0], "track": [
        {"time": 0.0, "value": [-4, 1.5, 0]}, {"time": 2.0, "value": [4, 1.5, 0]}]}}}
    return _document(duration=3.0, nodes=[core, *extra.get("nodes", [])], **{
        k: v for k, v in extra.items() if k != "nodes"})


def test_a_projectile_without_an_impact_is_a_warning() -> None:
    assert "style.no_impact" in _codes(community.house_style_findings(_projectile()))


def test_an_impact_phase_or_a_late_burst_satisfies_the_rule() -> None:
    phased = _projectile(timeline={"phases": [{"name": "impact", "start": 2.0, "end": 2.4}]})
    assert community.house_style_findings(phased) == []

    burst = _projectile(nodes=[{"id": "e", "type": "emitter", "parameters": {
        "burst_count": 200, "burst_times": [2.4]}}])
    assert community.house_style_findings(burst) == []


def test_a_rising_mote_is_not_a_projectile() -> None:
    """A healing bead climbs two metres; net travel is vertical, so it never lands."""
    doc = _document(nodes=[{"id": "bead", "type": "mesh", "parameters": {"position": {
        "value": [0, 0.1, 0],
        "track": [{"time": 0.0, "value": [-0.5, 0.1, -0.2]}, {"time": 1.0, "value": [-0.3, 4.2, -0.1]}]}}}])
    assert community.house_style_findings(doc) == []


def test_metadata_rules_report_what_is_missing() -> None:
    codes = _codes(community.metadata_findings({"nodes": []}))
    assert {"meta.name", "meta.description", "meta.author", "meta.license", "meta.controls"} <= codes
    assert community.metadata_findings(_document()) == []


def test_fewer_than_three_controls_fails() -> None:
    doc = _document(controls=[{"id": "a"}, {"id": "b"}])
    assert "meta.controls" in _codes(community.metadata_findings(doc))


# =====================================================================
# the check, end to end
# =====================================================================


@needs_engine
def test_the_sample_community_effect_passes() -> None:
    report = community.check_effect(SAMPLE)
    assert report.ok, "\n".join(str(f) for f in report.errors)
    assert report.author is not None and report.author.github
    assert report.license == "MIT"
    assert report.tags
    assert report.metrics["deterministic"] is True
    assert len(report.metrics["frame_hashes"]) == len(community.DETERMINISM_FRACTIONS)
    assert report.metrics["peak_live_sprites"] <= community.Budgets().max_live_sprites
    assert report.metrics["peak_live_mesh_particles"] <= community.Budgets().max_live_mesh_particles
    assert "PASS" in report.report()


@needs_engine
def test_a_broken_copy_fails_with_clear_messages(tmp_path: Path) -> None:
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    doc["metadata"].pop("author")                                    # no credit
    doc["metadata"]["license"] = "Apache-2.0"                        # wrong licence
    doc["controls"] = doc["controls"][:1]                            # too few controls
    doc["nodes"].append({"id": "bad_tex", "type": "texture",
                         "parameters": {"source": "file", "path": "sheets/fire.png"}})
    for node in doc["nodes"]:                                        # a four-armed star
        if node["id"] == "tex_flash":
            node["parameters"]["graph"]["nodes"][1]["params"]["points"] = 4
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(doc), encoding="utf-8")

    report = community.check_effect(broken)
    assert not report.ok
    codes = _codes(report.errors)
    assert {"meta.author", "meta.license", "meta.controls",
            "asset.file_texture", "asset.path", "style.four_arms"} <= codes
    assert "FAIL" in report.report()
    for finding in report.errors:
        assert len(finding.message) > 20, "every message has to say what to do"


@needs_engine
def test_a_budget_blow_up_is_reported(tmp_path: Path) -> None:
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    doc["duration"] = 30.0                                           # over the 12 s limit
    doc["nodes"] = doc["nodes"] * 4                                  # over the node cap (duplicate ids: E001)
    blown = tmp_path / "blown.json"
    blown.write_text(json.dumps(doc), encoding="utf-8")

    report = community.check_effect(blown)
    assert not report.ok
    assert any(code.startswith(("budget.", "engine.E")) for code in _codes(report.errors))


@needs_engine
def test_a_nonprocedural_effect_is_rejected_before_it_is_simulated(tmp_path: Path) -> None:
    doc = _document(nodes=[{"id": "t", "type": "texture",
                            "parameters": {"source": "file", "path": "/tmp/x.png"}}])
    path = tmp_path / "file_texture.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    report = community.check_effect(path, engine=False)
    assert not report.ok and "asset.file_texture" in _codes(report.errors)


def test_check_without_the_engine_still_runs_the_static_rules(tmp_path: Path) -> None:
    path = tmp_path / "e.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    report = community.check_effect(path, engine=False)
    assert report.ok and report.skipped and not report.metrics


def test_check_reports_unreadable_and_invalid_files(tmp_path: Path) -> None:
    missing = community.check_effect(tmp_path / "nope.json")
    assert not missing.ok and "file.unreadable" in _codes(missing.errors)

    garbage = tmp_path / "bad.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert "file.json" in _codes(community.check_effect(garbage).errors)


@needs_engine
def test_lint_library_passes_on_the_shipped_library() -> None:
    reports, excused = community.lint_library([EXAMPLES_DIR, COMMUNITY_DIR])
    failing = [r for r in reports if not r.ok]
    assert not failing, "\n".join(f"{r.path.name}: {f}" for r in failing for f in r.errors)
    assert len(reports) >= 20
    assert {r.path.name for r in excused} <= set(community.LINT_ALLOWLIST)


def test_the_lint_allowlist_stays_small() -> None:
    assert len(community.LINT_ALLOWLIST) <= 2, "fix the effect instead of excusing it"


@needs_engine
@needs_binary
def test_previews_are_rendered_into_the_given_directory(tmp_path: Path) -> None:
    report = community.check_effect(SAMPLE, previews=tmp_path, binary=BINARY)
    assert report.ok
    assert set(report.previews) == {"sheet", "animation"}
    sheet = tmp_path / report.previews["sheet"]
    gif = tmp_path / report.previews["animation"]
    assert sheet.is_file() and sheet.stat().st_size > 1000
    assert gif.is_file() and gif.read_bytes()[:6] in (b"GIF87a", b"GIF89a")


# =====================================================================
# the index
# =====================================================================


def test_the_index_entry_carries_what_a_listing_needs() -> None:
    index = community.build_index(COMMUNITY_DIR)
    assert index["schema"] == community.INDEX_SCHEMA
    assert index["license"] == "MIT"
    assert index["count"] == len(index["effects"]) >= 1
    entry = next(e for e in index["effects"] if e["slug"] == "arcane_missile")
    assert entry["name"] and entry["description"] and entry["tags"]
    assert entry["author"]["url"].startswith("https://github.com/")
    assert entry["file"] == "effects/arcane_missile.json"
    assert len(entry["sha256"]) == 64 and entry["bytes"] > 0
    assert entry["node_count"] > 0 and entry["duration"] > 0
    assert len(entry["controls"]) >= 3
    assert entry["schema_version"] == "0.1.0"


def test_the_index_is_deterministic(tmp_path: Path) -> None:
    first = community.index_text(community.build_index(COMMUNITY_DIR))
    second = community.index_text(community.build_index(COMMUNITY_DIR))
    assert first == second
    assert first.endswith("\n")
    written = community.write_index(community.build_index(COMMUNITY_DIR), tmp_path / "index.json")
    assert written.read_text(encoding="utf-8") == first


def test_the_committed_index_is_up_to_date() -> None:
    on_disk = (REPO_ROOT / "community" / "index.json").read_text(encoding="utf-8")
    assert on_disk == community.index_text(community.build_index(COMMUNITY_DIR)), \
        "run tools/build_community_index.py"


def test_previews_are_only_recorded_when_they_exist(tmp_path: Path) -> None:
    without = community.build_index(COMMUNITY_DIR)["effects"][0]
    assert without["previews"] == {}

    names = community.preview_names("arcane_missile")
    (tmp_path / names["sheet"]).write_bytes(b"x")
    with_sheet = community.build_index(COMMUNITY_DIR, previews_dir=tmp_path)["effects"][0]
    assert with_sheet["previews"] == {"sheet": f"previews/{names['sheet']}"}


# =====================================================================
# the studio
# =====================================================================


@pytest.fixture(scope="module")
def studio(tmp_path_factory: pytest.TempPathFactory):
    config = StudioConfig(
        output_dir=tmp_path_factory.mktemp("community-studio"),
        examples_dir=EXAMPLES_DIR,
        community_dir=COMMUNITY_DIR,
        binary=BINARY,
        generator="none",
        port=8787,
    )
    with TestClient(create_app(config)) as client:
        yield client


pytestmark_studio = needs_binary


@needs_binary
def test_effects_listing_carries_sections_authors_tags_and_descriptions(studio: TestClient) -> None:
    library = studio.get("/api/effects").json()["library"]
    sections = {entry["section"] for entry in library}
    assert {"core", "community"} <= sections

    core = next(e for e in library if e["section"] == "core")
    assert core["protected"] is True and core["author"] is None
    assert isinstance(core["tags"], list) and core["tags"], "examples carry metadata.tags for search"
    assert core["description"]

    contributed = next(e for e in library if e["section"] == "community")
    assert contributed["protected"] is True
    assert contributed["author"]["url"].startswith("https://github.com/")
    assert contributed["author"]["display"]
    assert "arcane" in contributed["tags"]


@needs_binary
def test_the_community_endpoint_lists_the_local_collection(studio: TestClient) -> None:
    data = studio.get("/api/community").json()
    assert data["repository"].startswith("https://github.com/")
    assert data["index"]["enabled"] is False
    assert data["remote"] == []
    entry = next(e for e in data["local"] if e["name"] == "Arcane Missile")
    assert entry["author"]["github"] and entry["tags"] and entry["description"]
    assert entry["duration"] == pytest.approx(3.2)


@needs_binary
def test_loading_a_community_effect_gives_a_protected_working_copy(studio: TestClient) -> None:
    result = studio.post("/api/effects/load", json={"path": str(SAMPLE)})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["section"] == "community" and body["protected"] is True
    assert body["author"]["url"] == "https://github.com/" + body["author"]["github"]

    # the credit chip reads this
    source = studio.get("/api/effects").json()["working"]["source"]
    assert source["section"] == "community" and source["author"]["display"]


@needs_binary
def test_every_write_path_refuses_a_community_file(studio: TestClient) -> None:
    studio.post("/api/effects/load", json={"path": str(SAMPLE)})

    # 1. the UI's Save as, by explicit path
    response = studio.post("/api/effects/save", json={"name": "Arcane Missile", "path": str(SAMPLE)})
    assert response.status_code == 403 and "protected" in response.json()["error"]["message"]

    # 2. the agent tool passthrough (this is also the MCP path: both call guard_tool)
    response = studio.post("/api/tool", json={"name": "save_effect", "args": {"path": str(SAMPLE)}})
    assert response.status_code == 403 and response.json()["error"]["code"] == "protected"

    # 3. export_effect as json would write the document too
    response = studio.post("/api/tool", json={"name": "export_effect",
                                              "args": {"format": "json", "path": str(SAMPLE)}})
    assert response.status_code == 403

    # 4. Save as with no path must not fall back to the community file
    response = studio.post("/api/effects/save", json={"name": "Arcane Missile"})
    assert response.status_code == 200, response.text
    saved = Path(response.json()["path"])
    assert not community.Path(saved).is_relative_to(COMMUNITY_DIR)
    assert saved.name != SAMPLE.name, "a community name is never shadowed"
    assert SAMPLE.read_text(encoding="utf-8")  # untouched


@needs_binary
def test_guard_uses_the_mcp_path_too(studio: TestClient) -> None:
    """``Studio.guard_tool`` is the single choke point; the MCP endpoint calls the same function."""
    from aetherfx.studio.server import Studio, StudioError

    instance: Studio = studio.app.state.studio
    with pytest.raises(StudioError):
        instance.guard_tool("save_effect", {"path": str(SAMPLE)})
    with pytest.raises(StudioError):
        instance.guard_tool("save_effect", {"path": str(EXAMPLES_DIR / "fireball.json")})
    instance.guard_tool("save_effect", {"path": str(instance.effects_dir / "mine.json")})


@needs_binary
def test_status_reports_the_community_configuration(studio: TestClient) -> None:
    status = studio.get("/api/status").json()
    assert status["community_dir"] == str(COMMUNITY_DIR)
    assert status["community_index"]["enabled"] is False
    assert status["repository"].startswith("https://github.com/")


@needs_binary
def test_the_remote_endpoints_are_off_by_default(studio: TestClient) -> None:
    assert studio.get("/api/community/remote").status_code == 404
    assert studio.post("/api/community/download", json={"slug": "x"}).status_code == 404


# =====================================================================
# the optional remote index
# =====================================================================


class _Server(threading.Thread):
    """A local http server over a directory, for the remote-index tests."""

    def __init__(self, directory: Path) -> None:
        super().__init__(daemon=True)
        handler = type("Handler", (http.server.SimpleHTTPRequestHandler,),
                       {"directory": str(directory),
                        "log_message": lambda *args, **kwargs: None,
                        "__init__": lambda self, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(
                            self, *a, directory=str(directory), **k)})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]

    def run(self) -> None:
        self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def published(tmp_path: Path):
    """A published collection on a local http server: index.json plus effects/."""
    root = tmp_path / "site"
    (root / "effects").mkdir(parents=True)
    (root / "effects" / "arcane_missile.json").write_bytes(SAMPLE.read_bytes())
    community.write_index(community.build_index(COMMUNITY_DIR), root / "index.json")
    server = _Server(root)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_a_plain_http_index_is_refused_unless_it_is_opted_into(published) -> None:
    strict = RemoteIndex(url=f"{published.url}/index.json")
    entries, error = strict.fetch()
    assert entries == [] and error and "https" in error

    relaxed = RemoteIndex(url=f"{published.url}/index.json", allow_insecure=True)
    entries, error = relaxed.fetch()
    assert error is None and [e["slug"] for e in entries] == ["arcane_missile"]
    assert entries[0]["url"].endswith("/effects/arcane_missile.json")
    assert entries[0]["author"]["github"]


def test_a_file_url_index_works_for_local_testing(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    community.write_index(community.build_index(COMMUNITY_DIR), index)
    remote = RemoteIndex(url=index.as_uri(), allow_insecure=True)
    entries, error = remote.fetch()
    assert error is None and len(entries) == 1


def test_the_size_cap_rejects_a_large_index(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"schema": community.INDEX_SCHEMA, "effects": [], "pad": "x" * 3000}))
    from aetherfx.studio.community_index import fetch_json

    with pytest.raises(RemoteIndexError) as excinfo:
        fetch_json(big.as_uri(), allow_insecure=True, max_bytes=1024)
    assert "cap" in str(excinfo.value)


def test_the_index_schema_is_checked() -> None:
    with pytest.raises(RemoteIndexError):
        normalise_index([], "https://x.test/")
    with pytest.raises(RemoteIndexError):
        normalise_index({"schema": "something-else", "effects": []}, "https://x.test/")
    with pytest.raises(RemoteIndexError):
        normalise_index({"schema": community.INDEX_SCHEMA}, "https://x.test/")


def test_hostile_entries_are_dropped_not_rendered() -> None:
    entries = normalise_index({
        "schema": community.INDEX_SCHEMA,
        "effects": [
            {"slug": "ok", "name": "Fine", "author": {"github": "octocat"}},
            {"slug": "../../etc/passwd", "name": "Escape"},
            {"slug": "traverse", "name": "Escape2", "file": "../../../etc/passwd"},
            {"slug": "absolute", "name": "Escape3", "file": "/etc/passwd"},
            {"slug": "remote_file", "name": "Escape4", "file": "https://evil.test/x.json"},
            {"slug": "bad_author", "name": "Bad", "author": {"github": "<script>"}},
            {"name": "no slug"},
            "not an object",
        ],
    }, "https://x.test/community/index.json")
    assert [e["slug"] for e in entries] == ["ok"]


def test_a_failed_fetch_never_raises_and_keeps_the_last_good_copy(published) -> None:
    remote = RemoteIndex(url=f"{published.url}/index.json", allow_insecure=True, ttl=0.0)
    entries, error = remote.fetch()
    assert error is None and entries
    published.stop()
    entries, error = remote.fetch(force=True)
    assert error is not None
    assert [e["slug"] for e in entries] == ["arcane_missile"], "the cached copy survives"


def test_the_cache_serves_repeated_reads(published) -> None:
    remote = RemoteIndex(url=f"{published.url}/index.json", allow_insecure=True)
    first, _ = remote.fetch()
    published.stop()
    second, error = remote.fetch()            # inside the TTL: no network
    assert error is None and [e["slug"] for e in second] == [e["slug"] for e in first]
    assert remote.status()["cached"] == 1


@needs_binary
@needs_engine
def test_a_remote_effect_is_checked_before_it_is_cached(published, tmp_path_factory) -> None:
    config = StudioConfig(
        output_dir=tmp_path_factory.mktemp("remote-studio"),
        examples_dir=EXAMPLES_DIR,
        community_dir=tmp_path_factory.mktemp("empty-community"),
        community_index=f"{published.url}/index.json",
        community_index_allow_insecure=True,
        binary=BINARY,
        generator="none",
        port=8788,
    )
    with TestClient(create_app(config)) as client:
        listing = client.get("/api/community").json()
        assert listing["local"] == []
        assert [e["slug"] for e in listing["remote"]] == ["arcane_missile"]
        assert listing["remote"][0]["remote"] is True

        result = client.post("/api/community/download", json={"slug": "arcane_missile"})
        assert result.status_code == 200, result.text
        saved = Path(result.json()["path"])
        assert saved.is_file() and saved.name == "arcane_missile.json"

        # it is protected like everything else in the collection, and no longer listed as remote
        again = client.get("/api/community").json()
        assert [e["name"] for e in again["local"]] == ["Arcane Missile"]
        assert again["remote"] == []
        refused = client.post("/api/tool", json={"name": "save_effect", "args": {"path": str(saved)}})
        assert refused.status_code == 403

        assert client.post("/api/community/download", json={"slug": "nope"}).status_code == 400


@needs_binary
@needs_engine
def test_a_remote_effect_that_fails_the_check_is_not_saved(tmp_path, tmp_path_factory) -> None:
    root = tmp_path / "bad-site"
    (root / "effects").mkdir(parents=True)
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    doc["metadata"].pop("author")                       # no credit -> the check fails
    (root / "effects" / "arcane_missile.json").write_text(json.dumps(doc), encoding="utf-8")
    index = community.build_index(COMMUNITY_DIR)
    community.write_index(index, root / "index.json")

    server = _Server(root)
    server.start()
    try:
        config = StudioConfig(
            output_dir=tmp_path_factory.mktemp("bad-remote"),
            examples_dir=EXAMPLES_DIR,
            community_dir=tmp_path_factory.mktemp("empty-community-2"),
            community_index=f"{server.url}/index.json",
            community_index_allow_insecure=True,
            binary=BINARY,
            generator="none",
            port=8789,
        )
        with TestClient(create_app(config)) as client:
            response = client.post("/api/community/download", json={"slug": "arcane_missile"})
            assert response.status_code == 400
            assert "check" in response.json()["error"]["message"]
            studio_state = client.app.state.studio
            assert not list(studio_state.community_cache_dir.glob("*.json"))
    finally:
        server.stop()
