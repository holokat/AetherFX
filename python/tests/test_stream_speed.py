"""Playback speed multiplier on the studio's live stream."""

from __future__ import annotations

import math

import pytest

from aetherfx.studio import stream_server
from aetherfx.studio.stream_server import MAX_SPEED, MIN_SPEED, clamp_speed, playback_target


def test_playback_target_scales_wall_clock_by_speed():
    assert playback_target(0.5, 1.0, 1.0) == pytest.approx(1.5)
    assert playback_target(0.5, 1.0, 2.0) == pytest.approx(2.5)
    assert playback_target(0.5, 1.0, 3.0) == pytest.approx(3.5)
    assert playback_target(1.0, 2.0, 0.5) == pytest.approx(2.0)


@pytest.mark.parametrize("value, expected", [
    (1.5, 1.5), ("2", 2.0), (3, 3.0), (0.25, 0.25),
    (100, MAX_SPEED), (0.0001, MIN_SPEED),
])
def test_clamp_speed_accepts_numbers_and_clamps(value, expected):
    assert clamp_speed(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [None, "fast", 0, -2, math.nan, [], {}])
def test_clamp_speed_falls_back_on_garbage(value):
    assert clamp_speed(value, 1.0) == 1.0
    assert clamp_speed(value, 2.0) == 2.0


def test_static_page_offers_the_speed_choices():
    html = (stream_server.__file__.rsplit("/", 1)[0] + "/static/index.html")
    text = open(html, encoding="utf-8").read()
    assert 'id="sel-speed"' in text
    for choice in ("1", "1.5", "2", "3"):
        assert f'<option value="{choice}"' in text
