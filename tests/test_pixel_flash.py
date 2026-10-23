"""PixelFlashCue: one pixel of a fixture painted a solid colour over the
frame for a moment (the Deux Ara Drop Bit's white Tower flickers).

Engine half: routing and the fires() allow-list, on the Task 2 fixtures.
Agent half: the paint-over, expiry and mute semantics, which need the
sibling luxaeterna checkout (same guard as tests/test_devicelink_agent.py).
"""

import pytest

from control.cues import ROOM, MuteCue, PixelFlashCue, fixture_dev
from tests.test_engine_functions import _BaseBit, _running

WHITE = (0, 0, 0, 255)


def _flashes(gs):
    flashes = []
    gs.on_pixel_flash = lambda *a: flashes.append(a)
    return flashes


def test_pixel_flash_reaches_sink():
    gs, _, _ = _running(bound={"main": "sim-room-main"})
    flashes = _flashes(gs)
    gs._dispatch_cues([PixelFlashCue("sim-room-main", 3, WHITE, 0.08)],
                      at=100.0)
    assert flashes == [("sim-room-main", 3, WHITE, 0.08, 100.0)]


def test_pixel_flash_when_overrides_at():
    gs, _, _ = _running(bound={"main": "sim-room-main"})
    flashes = _flashes(gs)
    gs._dispatch_cues(
        [PixelFlashCue("sim-room-main", 3, WHITE, 0.08, when=7.5)], at=100.0)
    assert flashes[0][4] == 7.5


def test_pixel_flash_is_skipped_for_a_muted_dev():
    gs, _, _ = _running(bound={"main": "sim-room-main"})
    flashes = _flashes(gs)
    gs._dispatch_cues([MuteCue("sim-room-main")], at=100.0)
    gs._dispatch_cues([PixelFlashCue("sim-room-main", 3, WHITE, 0.08)],
                      at=100.0)
    assert flashes == []


def test_fires_may_return_pixel_flash():
    class FlashFiresBit(_BaseBit):
        def fires(self, at):
            return [PixelFlashCue("sim-room-main", 3, WHITE, 0.08, when=at)]

    gs, _, _ = _running(bit_cls=FlashFiresBit,
                        bound={"main": "sim-room-main"})
    flashes = _flashes(gs)
    gs.tick(0.01)
    assert len(flashes) == 1 and flashes[0][:4] == (
        "sim-room-main", 3, WHITE, 0.08)


# --- agent half -------------------------------------------------------------

pytest.importorskip("luxaeterna")

from tests.test_devicelink_agent import (  # noqa: E402
    _last_leds_payload, two_fixture_agent)

__all__ = ["two_fixture_agent"]   # the fixture is used by name below

PIXEL = 3
LO, HI = PIXEL * 4, PIXEL * 4 + 4


def _settled(agent, main_dev):
    """Poll once and return main's last frame as sent."""
    agent.poll()
    return bytes(_last_leds_payload(agent.transport, main_dev))


def test_flash_paints_one_pixel(two_fixture_agent):
    agent, _audio, main_dev, _accent = two_fixture_agent
    before = _settled(agent, main_dev)
    assert before[LO:HI] != bytes(WHITE)

    agent._on_pixel_flash(main_dev, PIXEL, WHITE, 0.5, agent._clock())
    after = _settled(agent, main_dev)

    assert after[LO:HI] == bytes(WHITE)
    assert after[:LO] == before[:LO] and after[HI:] == before[HI:]


def test_flash_three_tuple_means_no_white(two_fixture_agent):
    agent, _audio, main_dev, _accent = two_fixture_agent
    agent._on_pixel_flash(main_dev, PIXEL, (10, 20, 30), 0.5, agent._clock())
    assert _settled(agent, main_dev)[LO:HI] == bytes((10, 20, 30, 0))


def test_flash_expires(two_fixture_agent):
    agent, _audio, main_dev, _accent = two_fixture_agent
    agent._on_pixel_flash(main_dev, PIXEL, WHITE, 0.08, agent._clock())
    assert _settled(agent, main_dev)[LO:HI] == bytes(WHITE)

    # The rainbow scrolls with the clock, so compare against the session's
    # own un-overlaid frame (the universe) rather than a pre-flash capture.
    agent._clock.advance(0.1)
    sent = _settled(agent, main_dev)
    st = agent._fixtures["main"]
    session_frame = bytes(st.universe.get_frame()[:len(sent)])
    assert sent == session_frame
    assert sent[LO:HI] != bytes(WHITE)
    assert agent._pixel_flashes == {}


def test_flash_ignored_while_muted(two_fixture_agent):
    agent, _audio, main_dev, _accent = two_fixture_agent
    _settled(agent, main_dev)
    agent._on_mute_change(main_dev, True)
    blackout = _settled(agent, main_dev)

    agent._on_pixel_flash(main_dev, PIXEL, WHITE, 0.5, agent._clock())
    assert _settled(agent, main_dev) == blackout
    assert agent._pixel_flashes == {}


def test_mute_clears_a_pending_flash(two_fixture_agent):
    agent, _audio, main_dev, _accent = two_fixture_agent
    agent._on_pixel_flash(main_dev, PIXEL, WHITE, 5.0, agent._clock())
    agent.poll()
    agent._on_mute_change(main_dev, True)
    assert set(_settled(agent, main_dev)) == {0}

    agent._on_mute_change(main_dev, False)
    assert agent._pixel_flashes == {}


def test_out_of_range_pixel_is_ignored(two_fixture_agent):
    agent, _audio, main_dev, _accent = two_fixture_agent
    before = _settled(agent, main_dev)
    for pixel in (10_000, -1):
        agent._on_pixel_flash(main_dev, pixel, WHITE, 0.5, agent._clock())
        assert _settled(agent, main_dev) == before


def test_float_pixel_paints_that_pixel(two_fixture_agent):
    agent, _audio, main_dev, _accent = two_fixture_agent
    agent._on_pixel_flash(main_dev, float(PIXEL), WHITE, 0.5, agent._clock())
    assert _settled(agent, main_dev)[LO:HI] == bytes(WHITE)


def test_non_numeric_pixel_is_dropped_before_the_render(two_fixture_agent):
    """A bad pixel must fail inside the engine's guarded sink call, never
    later in the render loop where it would take down every tick."""
    agent, _audio, main_dev, _accent = two_fixture_agent
    gs = agent.game_server
    assert gs.on_pixel_flash is not None
    gs._dispatch_cues([PixelFlashCue(main_dev, "three", WHITE, 0.5)],
                      at=agent._clock())
    assert agent._pixel_flashes == {}
    agent.poll()                                   # must not raise


def test_flash_touches_only_its_fixture(two_fixture_agent):
    agent, _audio, main_dev, accent_dev = two_fixture_agent
    agent.poll()
    accent_before = bytes(_last_leds_payload(agent.transport, accent_dev))
    agent._on_pixel_flash(main_dev, PIXEL, WHITE, 0.5, agent._clock())
    agent.poll()
    assert bytes(_last_leds_payload(agent.transport, accent_dev)) == accent_before
    assert fixture_dev("main") in agent._pixel_flashes
    assert fixture_dev("accent") not in agent._pixel_flashes
