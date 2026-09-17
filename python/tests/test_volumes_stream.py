"""Procedural volumes on the wire (docs/VOLUMES.md, docs/GPU_VIEWER.md).

A volume is ~25 scalars, so it travels as plain JSON in the frame header and
never touches the binary blob. These tests pin that: the header carries the
whole field description the viewer's raymarcher needs, byte offsets of the
arrays that *do* live in the blob are unaffected, and the blob stays 4-byte
aligned so the browser can keep building typed-array views instead of copying.
"""

from __future__ import annotations

import numpy as np

from aetherfx.studio.stream import ArrayRef, Frame, SystemFrame, decode_header, encode_frame

#: Every key the viewer's volumes.js reads off a frame volume.
VOLUME_KEYS = {
    "id", "mode", "shape", "transform", "bounds_min", "bounds_max", "radius", "height",
    "density", "emission", "color", "color_hot", "filament_scale", "strands", "carve",
    "softness", "spiral_arms", "arm_sharpness", "twist", "spin", "climb", "scatter",
    "march_steps", "seed", "time",
}


def a_volume(**overrides) -> dict:
    volume = {
        "id": "void_core",
        "mode": "procedural",
        "shape": "nebula",
        "transform": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.95, 0.0, 1.0],
        "bounds_min": [-2.2, 0.75, -2.2],
        "bounds_max": [2.2, 3.15, 2.2],
        "radius": 2.2,
        "height": 2.4,
        "density": 1.9,
        "emission": 1.5,
        "color": [0.32, 0.1, 0.82, 1.0],
        "color_hot": [0.3, 0.88, 0.95, 1.0],
        "filament_scale": 1.8,
        "strands": 0.6,
        "carve": 0.5,
        "softness": 0.75,
        "spiral_arms": 3,
        "arm_sharpness": 1.7,
        "twist": 0.4,
        "spin": 0.15,
        "climb": 0.1,
        "scatter": 0.3,
        "march_steps": 48,
        "seed": 4213,
        "time": 1.2,
    }
    volume.update(overrides)
    return volume


class TestEncodeVolumes:
    def test_a_frame_with_a_volume_round_trips_through_the_header(self):
        frame = Frame(time=1.2, frame=72, volumes=[a_volume()])
        header, blob = decode_header(encode_frame(frame))

        assert len(header["volumes"]) == 1
        volume = header["volumes"][0]
        assert set(volume) == VOLUME_KEYS
        assert volume["id"] == "void_core"
        assert volume["mode"] == "procedural"
        assert volume["shape"] == "nebula"
        assert volume["spiral_arms"] == 3
        assert volume["march_steps"] == 48
        assert volume["radius"] == 2.2
        assert len(volume["transform"]) == 16
        assert len(volume["color"]) == len(volume["color_hot"]) == 4
        # Volumes are JSON only: nothing of theirs is in the blob.
        assert len(blob) == 0

    def test_an_empty_volume_list_is_still_present(self):
        header, _ = decode_header(encode_frame(Frame(time=0.0, frame=0)))
        assert header["volumes"] == []

    def test_several_volumes_keep_their_order(self):
        frame = Frame(
            time=0.5,
            frame=30,
            volumes=[a_volume(id="a"), a_volume(id="b", shape="column"), a_volume(id="c", shape="ring")],
        )
        header, _ = decode_header(encode_frame(frame))
        assert [v["id"] for v in header["volumes"]] == ["a", "b", "c"]
        assert [v["shape"] for v in header["volumes"]] == ["nebula", "column", "ring"]

    def test_volumes_do_not_disturb_the_blob_of_a_particle_system(self):
        positions = np.arange(12, dtype=np.float32).reshape(4, 3)
        sizes = np.full(4, 0.25, dtype=np.float32)
        system = SystemFrame(id="ps", arrays={"position": ArrayRef(positions), "size": ArrayRef(sizes)})

        without = decode_header(encode_frame(Frame(time=1.0, frame=60, systems=[system])))
        with_volume = decode_header(
            encode_frame(Frame(time=1.0, frame=60, systems=[system], volumes=[a_volume()]))
        )

        assert without[0]["systems"][0]["arrays"] == with_volume[0]["systems"][0]["arrays"]
        assert bytes(without[1]) == bytes(with_volume[1])
        offset = with_volume[0]["systems"][0]["arrays"]["position"]["offset"]
        view = np.frombuffer(with_volume[1], dtype=np.float32, count=12, offset=offset).reshape(4, 3)
        assert np.array_equal(view, positions)

    def test_the_blob_stays_four_byte_aligned_behind_a_long_volume_header(self):
        # Long ids stretch the JSON header; the encoder pads it back to a multiple
        # of four so the client can keep using views over the ArrayBuffer.
        volumes = [a_volume(id=f"volume_with_a_rather_long_identifier_{i}") for i in range(7)]
        message = encode_frame(
            Frame(
                time=2.0,
                frame=120,
                systems=[SystemFrame(id="ps", arrays={"position": ArrayRef(np.zeros((3, 3), np.float32))})],
                volumes=volumes,
            )
        )
        header_length = int(np.frombuffer(message[:4], dtype="<u4")[0])
        assert header_length % 4 == 0
        assert (4 + header_length) % 4 == 0
        header, blob = decode_header(message)
        assert len(header["volumes"]) == 7
        assert len(blob) >= 36
