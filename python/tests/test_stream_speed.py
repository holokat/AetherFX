"""The two speeds on the studio's live stream.

The *play bar's* speed is a viewing preference and lives only in the browser
session.  The *effect's* speed is its ``time_scale``: part of the document,
driven by its Speed control, exported, and honoured by a game.  They compose.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from aetherfx.studio import stream_server
from aetherfx.studio.stream import MockFrameSource
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


# =====================================================================
# the EFFECT's own speed (time_scale), which is not the play bar's
# =====================================================================


class _Source:
    """A FrameSource stub that only has to answer ``time_scale``."""

    def __init__(self, scale):
        self._scale = scale

    def time_scale(self):
        if self._scale is _RAISES:
            raise RuntimeError("no effect open")
        return self._scale


_RAISES = object()


def test_playback_target_multiplies_the_two_speeds():
    # The play bar and the effect compose: 2x on a 1.5x effect is 3x.
    assert playback_target(0.0, 1.0, 1.0, 1.0) == pytest.approx(1.0)
    assert playback_target(0.0, 1.0, 1.0, 2.0) == pytest.approx(2.0)
    assert playback_target(0.0, 1.0, 2.0, 1.5) == pytest.approx(3.0)
    assert playback_target(0.0, 1.0, 0.5, 0.5) == pytest.approx(0.25)
    # The origin is an effect time and is never scaled, so changing a speed
    # mid-playback changes the rate without jumping the picture.
    assert playback_target(0.5, 1.0, 1.0, 2.0) == pytest.approx(2.5)
    # Omitting it is 1x, which is what every existing caller meant.
    assert playback_target(0.5, 1.0, 2.0) == pytest.approx(2.5)


@pytest.mark.parametrize("scale, expected", [
    (2.0, 2.0), (0.5, 0.5), (1, 1.0), ("1.5", 1.5),
    (0.0, 1.0), (-1.0, 1.0), (math.nan, 1.0), (None, 1.0), ("fast", 1.0), (_RAISES, 1.0),
])
def test_source_time_scale_reads_a_source_and_falls_back_to_1x(scale, expected):
    assert stream_server.source_time_scale(_Source(scale)) == pytest.approx(expected)


def test_source_time_scale_accepts_a_source_that_has_no_such_method():
    class Old:
        """A FrameSource written before time_scale existed."""

    assert stream_server.source_time_scale(Old()) == pytest.approx(1.0)
    assert stream_server.source_time_scale(Old(), default=2.0) == pytest.approx(2.0)


def test_the_mock_source_reports_the_documents_time_scale():
    source = MockFrameSource(count=8)
    source.open({"name": "Mock", "duration": 3.0})
    assert source.time_scale() == pytest.approx(1.0)
    assert source.resources()["effect"]["wall_duration"] == pytest.approx(3.0)

    source.open({"name": "Mock", "duration": 3.0, "time_scale": 2.0})
    assert source.time_scale() == pytest.approx(2.0)
    effect = source.resources()["effect"]
    assert effect["duration"] == pytest.approx(3.0)
    assert effect["time_scale"] == pytest.approx(2.0)
    assert effect["wall_duration"] == pytest.approx(1.5)


def test_the_state_message_carries_the_effect_speed_and_wall_duration():
    connection = stream_server._Connection.__new__(stream_server._Connection)
    connection.duration = 3.0
    connection.time_scale = 1.0
    assert connection.wall_duration() == pytest.approx(3.0)

    connection.time_scale = 2.0
    assert connection.wall_duration() == pytest.approx(1.5)
    connection.time_scale = 0.5
    assert connection.wall_duration() == pytest.approx(6.0)
    # A source that never answered leaves it unusable rather than dividing by 0.
    connection.time_scale = 0.0
    assert connection.wall_duration() == pytest.approx(3.0)


def test_the_play_bar_offers_speeds_and_the_style_panel_shows_the_effects_own():
    """The two are deliberately separate controls in the UI."""
    static = Path(stream_server.__file__).parent / "static"
    app = (static / "app.js").read_text(encoding="utf-8")
    # the effect's speed reaches the readout
    assert "setTimeScale" in app
    assert "wallDuration" in app
    assert "plays in" in app
    # ... from both the resources message and the state message
    assert "resources.effect.time_scale" in app
    assert "state.time_scale" in app
