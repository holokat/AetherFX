"""The live frame stream: encoding, the WebSocket and the resource routes.

These run against the fake engine and the :class:`MockFrameSource`, so they are
fast and do not need the C++ binary.  What they protect is the contract the
browser viewer depends on: a frame message whose blob is 4-byte aligned (typed
array views), a socket that answers ``seek`` with exactly one frame, and
resource routes that 404 instead of guessing.
"""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from starlette.testclient import TestClient

from aetherfx.client import Client
from aetherfx.jsonrpc import InMemoryTransport
from aetherfx.studio.server import StudioConfig, create_app
from aetherfx.studio.stream import MockFrameSource, decode_header, encode_frame
from aetherfx.studio import stream_server
from aetherfx.studio.stream_server import create_frame_source, mesh_payload, public_resources


@pytest.fixture
def studio_client(fake_transport: InMemoryTransport, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """The studio with a fake engine, no generator and no MCP endpoint.

    The source is pinned to the mock: ``create_frame_source()`` prefers
    :class:`~aetherfx.studio.native_source.NativeFrameSource` whenever the C
    library is built, and the fake engine's ``get_effect_json`` is an echo, not
    a compilable effect.  What these tests are about is the transport, so they
    feed it the one source that always produces frames.
    """
    monkeypatch.setattr(stream_server, "create_frame_source", lambda: MockFrameSource())
    config = StudioConfig(
        output_dir=tmp_path / "out",
        examples_dir=tmp_path / "examples",
        client=Client(transport=fake_transport),
        generator="none",
        mcp=False,
    )
    with TestClient(create_app(config)) as client:
        yield client


# =====================================================================
# frame encoding
# =====================================================================


class TestFrameEncoding:
    def test_round_trip_preserves_every_array(self) -> None:
        source = MockFrameSource(count=64)
        source.open({"name": "Round Trip", "duration": 2.0})
        frame = source.frame_at(0.75)

        message = encode_frame(frame, fps=60.0)
        header, blob = decode_header(message)

        assert header["type"] == "frame"
        assert header["version"] == 1
        assert header["time"] == pytest.approx(0.75)
        assert header["fps"] == pytest.approx(60.0)
        assert len(header["systems"]) == 1
        assert header["camera"]["fov"] == 45

        system = header["systems"][0]
        assert system["id"] == "mock_ps"
        assert system["count"] == 64
        for name, ref in system["arrays"].items():
            expected = frame.systems[0].arrays[name].data
            components = ref["components"]
            dtype = np.uint32 if ref["dtype"] == "u32" else np.float32
            size = system["count"] * components * np.dtype(dtype).itemsize
            decoded = np.frombuffer(blob[ref["offset"]:ref["offset"] + size], dtype=dtype)
            np.testing.assert_allclose(decoded, np.asarray(expected, dtype=dtype).ravel(), rtol=1e-6)

    def test_blob_starts_on_a_four_byte_boundary(self) -> None:
        """The viewer builds typed-array views over the blob; misalignment throws."""
        source = MockFrameSource(count=7)
        for time in (0.0, 0.31, 1.7):
            message = encode_frame(source.frame_at(time))
            (header_length,) = struct.unpack_from("<I", message, 0)
            assert (4 + header_length) % 4 == 0
            json.loads(message[4:4 + header_length].decode("utf-8"))   # padding stays valid JSON

    def test_a_mock_source_is_the_fallback(self) -> None:
        source = create_frame_source()
        try:
            assert hasattr(source, "frame_at") and hasattr(source, "resources")
        finally:
            source.close()


# =====================================================================
# resource shapes (the NativeFrameSource contract)
# =====================================================================


class TestResourceShapes:
    def test_texture_bytes_are_replaced_by_a_url(self) -> None:
        public = public_resources({"textures": {"tex_puff": {"png": b"\x89PNG", "width": 256, "frames": 4}}})
        texture = public["textures"]["tex_puff"]
        assert texture["url"] == "/api/stream/texture/tex_puff.png"
        assert texture["width"] == 256 and texture["frames"] == 4
        assert "png" not in texture
        assert json.dumps(public)          # must survive JSON encoding

    def test_mesh_entries_report_variant_count_and_url(self) -> None:
        public = public_resources({"meshes": {"rock": {"positions": [0, 0, 0], "variants": [{}, {}, {}]}}})
        assert public["meshes"]["rock"] == {"variants": 3, "url": "/api/stream/mesh/rock.json"}

    def test_mesh_payload_flattens_numpy_arrays(self) -> None:
        payload = mesh_payload({
            "positions": np.array([[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]], dtype=np.float32),
            "indices": np.array([[0, 1, 0]], dtype=np.uint32),
            "variants": [{"positions": [1.0, 2.0, 3.0]}],
        })
        assert payload["positions"] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        assert payload["indices"] == [0, 1, 0]
        assert payload["variants"][0]["positions"] == [1.0, 2.0, 3.0]
        assert payload["normals"] == [] and payload["uvs"] == []


# =====================================================================
# the socket and the routes
# =====================================================================


class TestStreamEndpoint:
    def test_connect_sends_resources_then_a_frame(self, studio_client: TestClient) -> None:
        with studio_client.websocket_connect("/ws/stream") as socket:
            resources = json.loads(socket.receive_text())
            assert resources["type"] == "resources"
            assert "textures" in resources and "meshes" in resources
            assert resources["effect"]["duration"] > 0

            header, _ = decode_header(socket.receive_bytes())
            assert header["type"] == "frame"
            assert header["time"] == pytest.approx(0.0)

    def test_seek_answers_with_exactly_one_frame_at_that_time(self, studio_client: TestClient) -> None:
        with studio_client.websocket_connect("/ws/stream") as socket:
            socket.receive_text()          # resources
            socket.receive_bytes()         # the frame that follows open

            socket.send_json({"type": "seek", "time": 1.25})
            header, blob = decode_header(socket.receive_bytes())
            assert header["time"] == pytest.approx(1.25)
            assert header["systems"][0]["count"] > 0
            assert len(blob) > 0

    def test_an_unknown_command_reports_an_error_and_keeps_the_socket(self, studio_client: TestClient) -> None:
        with studio_client.websocket_connect("/ws/stream") as socket:
            socket.receive_text()
            socket.receive_bytes()
            socket.send_json({"type": "nonsense"})
            error = json.loads(socket.receive_text())
            assert error["type"] == "error" and error["code"] == "bad_command"

            socket.send_json({"type": "seek", "time": 0.5})
            header, _ = decode_header(socket.receive_bytes())
            assert header["time"] == pytest.approx(0.5)


class TestResourceRoutes:
    def test_unknown_mesh_is_a_404(self, studio_client: TestClient) -> None:
        response = studio_client.get("/api/stream/mesh/no_such_mesh.json")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_unknown_texture_is_a_404(self, studio_client: TestClient) -> None:
        response = studio_client.get("/api/stream/texture/no_such_texture.png")
        assert response.status_code == 404

    def test_the_viewer_assets_are_served(self, studio_client: TestClient) -> None:
        for asset in ("/static/viewer/viewer.js", "/static/viewer/bootstrap.js",
                      "/static/vendor/three/build/three.module.js"):
            response = studio_client.get(asset)
            assert response.status_code == 200, asset
            assert response.content
