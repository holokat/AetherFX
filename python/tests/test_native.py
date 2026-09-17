"""The ctypes binding and the real-engine frame source.

These are the only tests that run the actual simulation: they load
``libaetherfx``, compile the shipped example effects and assert the things the
studio viewer and a game engine depend on - flipbook layout, mesh variants,
array shapes, determinism, and a frame that survives the round trip through
:func:`~aetherfx.studio.stream.encode_frame`.

The whole module skips when the library has not been built, so a checkout
without ``build/src/capi/libaetherfx.*`` still runs green.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aetherfx import native
from aetherfx.studio.stream import STREAM_VERSION, decode_header, encode_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "effects"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "effects"   # documents kept only for tests (the first Fireball)


def _document_camera(name: str) -> dict:
    """Camera framing as authored in an example document (position/target/fov as floats)."""
    doc = json.loads((EXAMPLES_DIR / f"{name}.json").read_text())
    params = next(node for node in doc["nodes"] if node["type"] == "camera")["parameters"]
    return {"position": [float(v) for v in params["position"]],
            "target": [float(v) for v in params["target"]],
            "fov": float(params["fov"])}

HAVE_NATIVE = native.is_available()

pytestmark = pytest.mark.skipif(not HAVE_NATIVE, reason="libaetherfx is not built (see cmake --preset capi)")

if HAVE_NATIVE:  # pragma: no branch - the module is skipped otherwise
    from aetherfx.studio.native_source import NativeFrameSource, effect_camera


def effect_document(name: str) -> dict:
    path = EXAMPLES_DIR / f"{name}.json"
    if not path.exists():
        path = FIXTURES_DIR / f"{name}.json"          # test-only documents such as the first Fireball
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def fireball() -> native.Compiled:
    with native.Effect.from_file(FIXTURES_DIR / "fireball.json") as effect:
        compiled = effect.compile(1.0 / 60.0)
    yield compiled
    compiled.close()


@pytest.fixture(scope="module")
def fire_aoe() -> native.Compiled:
    with native.Effect.from_file(EXAMPLES_DIR / "fire_aoe.json") as effect:
        compiled = effect.compile(1.0 / 60.0)
    yield compiled
    compiled.close()


# =====================================================================
# library and effects
# =====================================================================


class TestLibrary:
    def test_abi_matches_the_header_this_binding_was_written_against(self):
        library = native.load_library()
        assert library.aetherfx_abi_version() == native.ABI_VERSION
        assert native.version_string().count(".") == 2
        assert native.version() >= (0, 1, 0)

    def test_every_abi_function_is_declared(self):
        library = native.load_library()
        # 74 through ABI 1, plus the additive entry points: two for volumes, four for beam
        # strike data, four for effect controls and three for time_scale.
        assert len(native._SIGNATURES) == 87
        for name in native._SIGNATURES:
            assert getattr(library, name).argtypes is not None, name

    def test_bad_json_is_an_error_not_a_crash(self):
        with pytest.raises(native.NativeError) as excinfo:
            native.Effect.from_json("{ nope")
        assert excinfo.value.message
        assert excinfo.value.code < 0


class TestEffect:
    def test_fireball_loads_validates_and_compiles(self, fireball: native.Compiled):
        with native.Effect.from_file(FIXTURES_DIR / "fireball.json") as effect:
            assert effect.name == "Fireball"
            assert effect.duration == pytest.approx(2.5)
            diagnostics = effect.validate()
            assert diagnostics["ok"] is True
            assert diagnostics["errors"] == 0
            assert isinstance(diagnostics["items"], list)
        assert fireball.ok is True
        assert fireball.fixed_dt == pytest.approx(1.0 / 60.0)
        assert set(fireball.plan) >= {"nodes", "tiers", "resources", "diagnostics"}

    def test_to_json_round_trips_and_set_parameter_takes_effect(self):
        with native.Effect.from_file(FIXTURES_DIR / "fireball.json") as effect:
            document = effect.to_dict()
            assert document["name"] == "Fireball"
            effect.set_parameter("cam", "fov", 63.5)
            assert json.loads(effect.to_json(indent=-1))["name"] == "Fireball"
            with effect.compile(1.0 / 60.0) as compiled:
                runtime = compiled.runtime()
                assert runtime.camera()["fov"] == pytest.approx(63.5)
                runtime.close()

    def test_controls_scale_the_effect_before_it_compiles(self):
        # A game spawns a weaker instance of the same effect: set the control,
        # then compile.  The authored values never change (docs/CONTROLS.md).
        document = json.loads((FIXTURES_DIR / "fireball.json").read_text())
        document["controls"] = [
            {
                "id": "brightness",
                "label": "Brightness",
                "group": "Global",
                "min": 0.0,
                "max": 3.0,
                "default": 1.0,
                "value": 1.0,
                "step": 0.01,
                "unit": "x",
                "bindings": [{"node": "glow", "parameter": "intensity", "op": "multiply"}],
            }
        ]
        authored = next(n for n in document["nodes"] if n["id"] == "glow")["parameters"]["intensity"]

        with native.Effect.from_json(json.dumps(document)) as effect:
            controls = effect.controls()
            assert [c["id"] for c in controls] == ["brightness"]
            assert controls[0] == {
                "id": "brightness", "label": "Brightness", "group": "Global", "unit": "x",
                "min": 0.0, "max": 3.0, "default": 1.0, "value": 1.0, "step": 0.01, "bindings": 1,
            }

            with pytest.raises(native.NativeError):
                effect.set_control("nope", 1.0)
            with pytest.raises(native.NativeError):
                effect.set_control("brightness", 9.0)

            def light_at(time: float) -> float:
                with effect.compile(1.0 / 60.0) as compiled:
                    runtime = compiled.runtime()
                    runtime.simulate_to(time)
                    lights = runtime.frame().lights
                    assert lights, "fireball has a light"
                    value = float(lights[0]["intensity"])
                    runtime.close()
                    return value

            full = light_at(0.2)
            effect.set_control("brightness", 0.5)
            assert effect.controls()[0]["value"] == pytest.approx(0.5)
            assert light_at(0.2) == pytest.approx(full * 0.5, rel=1e-4)

            # the document still carries what the author wrote
            glow = next(n for n in effect.to_dict()["nodes"] if n["id"] == "glow")
            assert glow["parameters"]["intensity"] == pytest.approx(authored)

    def test_time_scale_maps_wall_time_onto_effect_time(self):
        # The default: an effect plays at the speed it was authored at.
        with native.Effect.from_file(FIXTURES_DIR / "fireball.json") as effect:
            assert effect.time_scale == pytest.approx(1.0)
            assert effect.wall_duration == pytest.approx(effect.duration)
            with effect.compile(1.0 / 60.0) as compiled:
                assert compiled.time_scale == pytest.approx(1.0)
                assert compiled.wall_duration == pytest.approx(effect.duration)

        # An authored speed, plus a Speed control bound to the reserved
        # "$effect" target, which only folds in when the effect compiles.
        document = json.loads((FIXTURES_DIR / "fireball.json").read_text())
        document["time_scale"] = 1.5
        document["controls"] = [
            {
                "id": "global_speed",
                "label": "Speed",
                "group": "Global",
                "min": 0.25,
                "max": 4.0,
                "default": 1.0,
                "value": 1.0,
                "step": 0.05,
                "unit": "x",
                "bindings": [{"node": "$effect", "parameter": "time_scale", "op": "multiply"}],
            }
        ]
        duration = document["duration"]

        with native.Effect.from_json(json.dumps(document)) as effect:
            assert effect.validate()["ok"] is True
            assert effect.time_scale == pytest.approx(1.5)
            with effect.compile(1.0 / 60.0) as compiled:
                assert compiled.time_scale == pytest.approx(1.5)
                assert compiled.wall_duration == pytest.approx(duration / 1.5)
                assert compiled.plan["time_scale"] == pytest.approx(1.5)

            effect.set_control("global_speed", 2.0)
            with effect.compile(1.0 / 60.0) as compiled:
                assert compiled.time_scale == pytest.approx(3.0)
                assert compiled.wall_duration == pytest.approx(duration / 3.0)
                # The simulation is untouched: same timestep, and a host simply
                # reaches effect time faster for the same wall seconds.
                assert compiled.fixed_dt == pytest.approx(1.0 / 60.0)
                runtime = compiled.runtime()
                runtime.simulate_to(0.2 * compiled.time_scale)
                assert runtime.time == pytest.approx(0.6, abs=1.0 / 60.0)
                runtime.close()

            # The authored document is never rewritten by a control.
            assert effect.time_scale == pytest.approx(1.5)
            assert effect.to_dict()["time_scale"] == pytest.approx(1.5)

    def test_an_effect_without_controls_has_none(self):
        with native.Effect.from_file(FIXTURES_DIR / "fireball.json") as effect:
            assert effect.controls() == []

    def test_closed_handles_refuse_to_be_used(self):
        effect = native.Effect.from_file(FIXTURES_DIR / "fireball.json")
        effect.close()
        effect.close()  # idempotent
        assert effect.closed
        with pytest.raises(native.NativeError):
            _ = effect.name


# =====================================================================
# baked resources
# =====================================================================


class TestTextures:
    def test_fireball_bakes_the_textures_its_systems_reference(self, fireball: native.Compiled):
        by_id = {texture.id: texture for texture in fireball.textures}
        assert "tex_puff" in by_id
        for texture in by_id.values():
            assert texture.channels == 4
            assert texture.frames >= 1
            assert texture.width == texture.frame_width * texture.frames

    def test_fire_aoe_tex_puff_is_an_eight_frame_flipbook(self, fire_aoe: native.Compiled):
        puff = fire_aoe.texture("tex_puff")
        assert puff is not None
        assert puff.frames == 8
        assert puff.frame_width == 128
        assert puff.width == 128 * 8

        pixels = puff.pixels
        assert pixels.shape == (puff.height, puff.width, 4)
        assert pixels.dtype == np.float32
        assert np.isfinite(pixels).all()

        image = Image.open(io.BytesIO(puff.png_bytes()))
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.size == (128 * 8, puff.height)

    def test_unknown_texture_is_none(self, fireball: native.Compiled):
        assert fireball.texture("tex_nope") is None


class TestMeshes:
    def test_fire_aoe_bakes_eight_rock_variants_with_valid_indices(self, fire_aoe: native.Compiled):
        variants = fire_aoe.mesh_variants("rock_mesh")
        assert len(variants) == 8
        assert [mesh.variant_index for mesh in variants] == list(range(8))
        assert variants[0].id == "rock_mesh"
        assert variants[3].id == "rock_mesh#3"

        for mesh in variants:
            positions = mesh.positions
            indices = mesh.indices
            assert positions.shape == (mesh.vertex_count, 3)
            assert indices.shape == (mesh.index_count,)
            assert indices.dtype == np.uint32
            assert mesh.index_count % 3 == 0
            assert int(indices.max()) < mesh.vertex_count
            assert np.isfinite(positions).all()
            if mesh.has_normals:
                assert mesh.normals.shape == (mesh.vertex_count, 3)
            if mesh.has_uvs:
                assert mesh.uvs.shape == (mesh.vertex_count, 2)

    def test_the_mesh_system_only_asks_for_variants_that_exist(self, fire_aoe: native.Compiled):
        runtime = fire_aoe.runtime()
        runtime.simulate_to(1.0)
        rocks = next(s for s in runtime.frame().systems if s.render_mode == "mesh")
        assert rocks.mesh_id == "rock_mesh"
        assert rocks.mesh_variants == len(fire_aoe.mesh_variants("rock_mesh"))
        assert int(rocks.variant.max()) < rocks.mesh_variants
        runtime.close()


class TestMaterials:
    def test_materials_are_json_dicts_with_vocabulary_enums(self, fireball: native.Compiled):
        materials = fireball.materials
        assert {m["id"] for m in materials} >= {"mat_core", "mat_flame", "mat_smoke"}
        json.dumps(materials)
        for material in materials:
            assert material["blend"] in native.BLEND_MODES
            assert material["shading"] in native.SHADING_MODES
            assert len(material["base_color"]) == 4
            assert len(material["emissive_color"]) == 4


# =====================================================================
# runtime
# =====================================================================


class TestRuntime:
    def test_sixty_steps_produce_particles_with_consistent_arrays(self, fireball: native.Compiled):
        runtime = fireball.runtime()
        assert runtime.backend == "cpu"
        for _ in range(60):
            runtime.step()
        assert runtime.frame_index == 60
        assert runtime.time == pytest.approx(1.0)

        frame = runtime.frame()
        assert frame.particle_count > 0
        components = {"position": 3, "previous_position": 3, "velocity": 3, "color": 4, "orientation": 4, "scale3": 3}
        for system in frame.systems:
            assert system.render_mode in native.RENDER_MODES
            assert system.blend in native.BLEND_MODES
            for name, array in system.arrays.items():
                expected = components.get(name)
                assert array.shape == ((system.count, expected) if expected else (system.count,)), name
            assert np.isfinite(system.position).all()
            assert ((system.custom0 >= 0.0) & (system.custom0 <= 1.0)).all()
        runtime.close()

    def test_statistics_and_camera(self, fireball: native.Compiled):
        runtime = fireball.runtime()
        runtime.simulate_to(1.0)
        statistics = runtime.statistics()
        assert statistics["frame"] == 60
        assert statistics["total_alive"] > 0
        assert isinstance(statistics["systems"], list)
        camera = runtime.camera()
        assert camera["fov"] > 0.0
        assert len(camera["position"]) == 3
        runtime.close()

    def test_simulate_to_never_steps_backwards_but_reset_replays(self, fireball: native.Compiled):
        runtime = fireball.runtime()
        runtime.simulate_to(1.0)
        before = runtime.frame().systems[0].position.copy()
        runtime.simulate_to(0.5)
        assert runtime.time == pytest.approx(1.0)
        runtime.reset()
        assert runtime.frame_index == 0
        runtime.simulate_to(1.0)
        assert np.array_equal(runtime.frame().systems[0].position, before)
        runtime.close()

    def test_two_runtimes_of_the_same_plan_are_bit_identical(self, fireball: native.Compiled):
        first, second = fireball.runtime(), fireball.runtime()
        for _ in range(60):
            first.step()
            second.step()
        left, right = first.frame(), second.frame()
        assert [s.id for s in left.systems] == [s.id for s in right.systems]
        for a, b in zip(left.systems, right.systems):
            assert a.count == b.count
            assert np.array_equal(a.position, b.position)
            assert np.array_equal(a.velocity, b.velocity)
            assert np.array_equal(a.seed, b.seed)
        first.close()
        second.close()

    def test_frame_arrays_are_copies_that_outlive_the_next_step(self, fireball: native.Compiled):
        runtime = fireball.runtime()
        runtime.simulate_to(1.0)
        kept = runtime.frame().systems[0].position.copy()
        snapshot = runtime.frame().systems[0].position
        for _ in range(30):
            runtime.step()
        assert np.array_equal(snapshot, kept)
        assert snapshot.flags.owndata
        runtime.close()

    def test_lightning_strike_flashes_a_beam_and_a_light(self):
        with native.Effect.from_file(EXAMPLES_DIR / "lightning_strike.json") as effect:
            with effect.compile(1.0 / 60.0) as compiled:
                runtime = compiled.runtime()
                runtime.simulate_to(0.1)
                frame = runtime.frame()
                assert len(frame.lights) >= 1
                assert len(frame.beams) >= 1
                bolt = frame.beams[0]
                assert bolt["polylines"], "a beam always has at least the main bolt"
                for polyline in bolt["polylines"]:
                    assert polyline.ndim == 2 and polyline.shape[1] == 3
                    assert np.isfinite(polyline).all()
                assert bolt["polylines"][0].shape[0] >= 2
                # the strike detail: one path per polyline, 5 floats per vertex
                assert len(bolt["paths"]) == len(bolt["polylines"])
                assert bolt["core_width"] > 0.0 and bolt["glow_width"] > 0.0
                for path in bolt["paths"]:
                    vertices = path["vertices"]
                    assert vertices.ndim == 2 and vertices.shape[1] == 5  # xyz, width, intensity
                    assert np.isfinite(vertices).all()
                    assert (vertices[:, 3] >= 0.0).all()  # width, metres
                    assert (vertices[:, 4] > 0.0).all()   # intensity
                    assert path["depth"] >= 0 and 0.0 < path["fade"] <= 1.0
                assert np.allclose(bolt["paths"][0]["vertices"][:, 0:3], bolt["polylines"][0])
                for ghost in bolt["ghosts"]:
                    assert ghost["fade"] <= 1.0
                for flare in bolt["flares"]:
                    assert flare["radius"] > 0.0 and len(flare["position"]) == 3
                runtime.close()

    def test_life_drain_strands_flow_and_close_over_the_sustain_loop(self):
        """docs/VOCABULARY.md beam flow: `noise_scroll` makes a strand undulate a
        little every frame (no re-roll jumps), `noise_taper` keeps it on both
        anchors, and `noise_loop` makes the sustain window repeat exactly - which
        is what `metadata.loop` promises a game that holds the channel."""
        document = effect_document("life_drain")
        loop = document["metadata"]["loop"]
        layout = document["metadata"]["layout"]
        strands = {node["id"]: node["parameters"] for node in document["nodes"]
                   if node["type"] == "beam" and node.get("enabled", True)}
        assert strands, "the drain is built from beams"
        for parameters in strands.values():
            assert parameters["noise_scroll"] > 0.0          # origin (the victim) -> target (the caster)
            assert parameters["jitter_rate"] == 0.0 and parameters["detail"] == 0
            assert parameters["noise_loop"] == pytest.approx(loop["end"] - loop["start"])
            assert parameters["target"] == layout["source"]   # life flows INTO the caster's hand
            pulses = parameters.get("pulse_speed", 0.0) * parameters["noise_loop"]   # absent = the default, 0
            assert pulses == pytest.approx(round(pulses), abs=1e-3)  # whole pulses per loop

        with native.Effect.from_file(EXAMPLES_DIR / "life_drain.json") as effect:
            with effect.compile(1.0 / 60.0) as compiled:
                runtime = compiled.runtime()

                def paths(time: float) -> dict[str, np.ndarray]:
                    runtime.simulate_to(time)
                    return {beam["id"]: beam["polylines"][0].copy() for beam in runtime.frame().beams}

                start = paths(loop["start"])
                next_frame = paths(loop["start"] + 1.0 / 60.0)
                middle = paths(0.5 * (loop["start"] + loop["end"]))
                end = paths(loop["end"])
                runtime.close()

        assert set(start) == set(strands)
        source, target = np.array(layout["source"]), np.array(layout["target"])
        for beam_id, path in start.items():
            assert np.allclose(path[0], target, atol=1e-4), beam_id     # pinned to the victim's chest ...
            assert np.allclose(path[-1], source, atol=1e-4), beam_id    # ... and to the caster's hand
            step = np.linalg.norm(next_frame[beam_id] - path, axis=1).max()
            # it flows, and it never jumps: a re-rolled path would move by the undulation amplitude
            # (0.5-0.9 m for the wide braided stream), a flowing one by a few centimetres per frame
            assert 1e-5 < step < 0.12, (beam_id, step)
            assert np.linalg.norm(middle[beam_id] - path, axis=1).max() > 0.01, beam_id
            assert np.allclose(end[beam_id], path, atol=2e-4), beam_id  # the loop closes

    def test_fireball_trail_ribbons_use_the_wire_layout(self, fireball: native.Compiled):
        runtime = fireball.runtime()
        runtime.simulate_to(1.0)
        trails = runtime.frame().trails
        assert trails, "fireball has a trail node"
        ribbons = [r for trail in trails for r in trail["ribbons"]]
        assert ribbons
        for ribbon in ribbons:
            assert ribbon.shape[1] == 12  # pos3, width, age_norm, u, color4, opacity, emissive
            assert ribbon.dtype == np.float32
            assert np.isfinite(ribbon).all()
            assert ((ribbon[:, 4] >= 0.0) & (ribbon[:, 4] <= 1.0)).all()  # normalized age
        runtime.close()


    def test_void_nebula_exposes_its_procedural_volume(self):
        """docs/VOLUMES.md: a `volume` in mode `procedural` resolves the whole field."""
        with native.Effect.from_file(EXAMPLES_DIR / "void_nebula.json") as effect:
            with effect.compile(1.0 / 60.0) as compiled:
                runtime = compiled.runtime()
                runtime.simulate_to(1.2)
                frame = runtime.frame()
                assert len(frame.volumes) == 1
                volume = frame.volumes[0]
                assert volume["id"] == "void_core"
                assert volume["mode"] == "procedural"
                assert volume["shape"] == "nebula"
                assert volume["backend"] == "procedural_volume"
                assert volume["spiral_arms"] == 3
                assert volume["march_steps"] == 48
                assert volume["density"] > 0.0
                assert volume["emission"] > 0.0
                assert len(volume["transform"]) == 16
                assert all(np.isfinite(volume["transform"]))
                assert volume["bounds_max"][1] > volume["bounds_min"][1]
                assert len(volume["color"]) == 4 and len(volume["color_hot"]) == 4
                assert volume["time"] == pytest.approx(1.2, abs=0.02)
                runtime.close()

    def test_fireball_has_no_volumes(self, fireball: native.Compiled):
        runtime = fireball.runtime()
        runtime.simulate_to(0.5)
        assert runtime.frame().volumes == []
        runtime.close()


# =====================================================================
# the studio frame source
# =====================================================================


class TestNativeFrameSource:
    def test_resources_is_json_serialisable_apart_from_the_png_bytes(self):
        with NativeFrameSource() as source:
            source.open(effect_document("fire_aoe"))
            resources = source.resources()

            assert resources["type"] == "resources"
            assert resources["effect"] == {
                "name": "Fire AOE", "duration": pytest.approx(3.0),
                "time_scale": pytest.approx(1.0), "wall_duration": pytest.approx(3.0),
                "fixed_dt": pytest.approx(1.0 / 60.0), "seed": 7,
            }
            assert source.duration() == pytest.approx(3.0)
            assert source.time_scale() == pytest.approx(1.0)
            assert source.wall_duration() == pytest.approx(3.0)
            assert source.fixed_dt() == pytest.approx(1.0 / 60.0)

            puff = resources["textures"]["tex_puff"]
            assert (puff["width"], puff["height"], puff["frames"], puff["frame_width"]) == (1024, 128, 8, 128)
            assert puff["png"].startswith(b"\x89PNG")

            rock = resources["meshes"]["rock_mesh"]
            assert rock["variant_count"] == 8
            assert len(rock["variants"]) == 8
            assert rock["variants"] is rock["variants_data"]
            assert rock["positions"] == rock["variants"][0]["positions"]
            assert len(rock["positions"]) % 3 == 0
            assert max(rock["indices"]) < len(rock["positions"]) // 3

            assert set(resources["materials"]) == {"mat_fire", "mat_rock", "mat_smoke"}
            assert resources["render_settings"]["exposure"] == pytest.approx(0.95)
            # the expected framing comes from the document, so art changes do not break the test
            doc_camera = _document_camera("fire_aoe")
            assert resources["camera"] == {"position": doc_camera["position"], "target": doc_camera["target"],
                                           "up": [0.0, 1.0, 0.0], "fov": doc_camera["fov"]}
            assert len(resources["timeline"]["phases"]) == 5

            stripped = {
                **resources,
                "textures": {k: {kk: vv for kk, vv in v.items() if kk != "png"}
                             for k, v in resources["textures"].items()},
            }
            json.dumps(stripped)  # everything but the PNG bytes goes over the wire as JSON

    def test_the_server_can_publish_what_resources_returns(self):
        from aetherfx.studio.stream_server import mesh_payload, public_resources

        with NativeFrameSource() as source:
            source.open(effect_document("fire_aoe"))
            resources = source.resources()
            public = public_resources(resources)
            assert public["meshes"]["rock_mesh"]["variants"] == 8
            assert public["meshes"]["rock_mesh"]["url"].startswith("/api/stream/mesh/rock_mesh.json?v=")
            assert public["textures"]["tex_puff"]["url"].startswith("/api/stream/texture/tex_puff.png?v=")
            assert "png" not in public["textures"]["tex_puff"]
            json.dumps(public)

            payload = mesh_payload(resources["meshes"]["rock_mesh"])
            assert len(payload["variants"]) == 8
            assert all(v["positions"] for v in payload["variants"])

    def test_frame_at_encodes_and_decodes(self):
        with NativeFrameSource() as source:
            source.open(effect_document("fire_aoe"))
            frame = source.frame_at(1.0)
            assert frame.time == pytest.approx(1.0)
            assert frame.frame == 60
            assert sum(s.count for s in frame.systems) > 0

            header, blob = decode_header(encode_frame(frame, fps=60.0))
            assert header["type"] == "frame" and header["version"] == STREAM_VERSION
            assert header["fps"] == 60.0
            mesh_system = next(s for s in header["systems"] if s["render_mode"] == "mesh")
            assert set(mesh_system["arrays"]) == {
                "position", "velocity", "size", "rotation", "color", "emissive",
                "age_norm", "age", "orientation", "scale3", "variant",
            }
            assert mesh_system["arrays"]["variant"]["dtype"] == "u32"
            billboard = next(s for s in header["systems"] if s["render_mode"] != "mesh")
            assert "orientation" not in billboard["arrays"]

            # the colour that goes over the wire is rgb + opacity as alpha
            spec = mesh_system["arrays"]["color"]
            assert spec["components"] == 4
            colors = np.frombuffer(blob, dtype=np.float32, count=mesh_system["count"] * 4,
                                   offset=spec["offset"]).reshape(-1, 4)
            source_system = next(s for s in frame.systems if s.id == mesh_system["id"])
            assert np.array_equal(colors, source_system.arrays["color"].data)
            assert ((colors[:, 3] >= 0.0) & (colors[:, 3] <= 1.0)).all()

            assert header["lights"] and "cone_angle" in header["lights"][0]
            assert all("texture" in d and "material" in d for d in header["decals"])
            # the per-frame camera is the runtime's (float32), not the document's doubles
            camera = header["camera"]
            doc_camera = _document_camera("fire_aoe")
            assert camera["position"] == pytest.approx(doc_camera["position"], rel=1e-6)
            assert camera["target"] == pytest.approx(doc_camera["target"], rel=1e-6)
            assert camera["up"] == pytest.approx([0.0, 1.0, 0.0])
            assert camera["fov"] == pytest.approx(doc_camera["fov"])
            assert set(camera) == {"position", "target", "up", "fov"}
            assert header["post_effects"] == []

    def test_scrubbing_backwards_replays_deterministically(self):
        with NativeFrameSource() as source:
            source.open(effect_document("fireball"))
            first = source.frame_at(1.0).systems[0].arrays["position"].data.copy()
            source.frame_at(1.5)
            again = source.frame_at(1.0).systems[0].arrays["position"].data
            assert np.array_equal(first, again)

    def test_lightning_strike_frame_carries_beams_and_lights(self):
        with NativeFrameSource() as source:
            source.open(effect_document("lightning_strike"))
            frame = source.frame_at(0.1)
            assert frame.lights and frame.lights[0]["type"] in ("point", "spot", "area")
            assert frame.beams
            assert all(p["vertices"].shape[1] == 5 for beam in frame.beams for p in beam["paths"])

            header, blob = decode_header(encode_frame(frame, fps=60.0))
            wire = header["beams"][0]
            paths = wire["paths"]
            assert paths["count"] == len(frame.beams[0]["paths"])
            assert "material" in wire and "ghosts" in wire and "flares" in wire
            assert wire["core_width"] > 0.0 and wire["glow_width"] > 0.0

            # The per-path metadata lives in the blob: one 4-float record per path
            # (first vertex, vertex count, branch depth, fade) beside one contiguous
            # run of 5-float vertices.
            records = np.frombuffer(blob, dtype=np.float32, count=paths["count"] * 4,
                                    offset=paths["offset"]).reshape(-1, 4)
            assert records[0][0] == 0 and records[0][1] >= 2
            assert records[0][2] == 0 and records[0][3] == pytest.approx(1.0)
            assert records[:, 1].sum() == paths["total"]

            run = np.frombuffer(blob, dtype=np.float32, count=paths["total"] * 5,
                                offset=paths["vertices"]).reshape(-1, 5)
            for record, source_path in zip(records, frame.beams[0]["paths"]):
                start, count = int(record[0]), int(record[1])
                assert np.allclose(run[start:start + count], source_path["vertices"])

    def test_a_broken_effect_reports_its_diagnostics(self):
        broken = effect_document("fireball")
        broken["nodes"] = [n for n in broken["nodes"] if n["type"] != "material"]
        with NativeFrameSource() as source:
            with pytest.raises(native.NativeError) as excinfo:
                source.open(broken)
            assert "compile failed" in str(excinfo.value)
            with pytest.raises(native.NativeError):
                source.resources()

    def test_camera_reads_the_first_enabled_camera_node(self):
        document = {"nodes": [
            {"id": "off", "type": "camera", "enabled": False, "parameters": {"fov": 10}},
            {"id": "on", "type": "camera", "parameters": {
                "position": {"value": [1, 2, 3], "track": [{"time": 0, "value": [1, 2, 3]}]},
                "fov": {"value": 55.0}}},
        ]}
        assert effect_camera(document) == {"position": [1.0, 2.0, 3.0], "target": [0.0, 1.0, 0.0],
                                           "up": [0.0, 1.0, 0.0], "fov": 55.0}
        assert effect_camera({"nodes": []}) is None

    def test_frame_at_is_fast_enough_for_sixty_hz(self, capsys):
        steps = 60
        with NativeFrameSource() as source:
            source.open(effect_document("fire_aoe"))
            for index in range(30):  # warm up: allocate the pools, reach a busy frame
                source.frame_at(index / 60.0)
            started = time.perf_counter()
            for index in range(steps):
                source.frame_at((30 + index) / 60.0)
            average_ms = (time.perf_counter() - started) / steps * 1000.0
        with capsys.disabled():
            print(f"\nfire_aoe frame_at: {average_ms:.2f} ms average over {steps} one-step frames")
        assert average_ms < 25.0, f"{average_ms:.2f} ms per frame is too slow for 60 Hz playback"
