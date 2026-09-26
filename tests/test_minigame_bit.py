"""MinigameBit: Victor's single-Tuneshroom bench toy (bits/minigame/).
Package discovery, unit-level handlers, and the round end to end through a
real GameServer in both join orders."""
from pathlib import Path

from bits.minigame.minigame_bit import (
    BLINK_COUNT,
    BLINK_INTERVAL_S,
    BLINK_RGB,
    MINIGAME_PLAYER_NODE,
    MinigameBit,
)
from control.bit_registry import BitRegistry
from control.catalog import load_catalog
from control.cues import FireFunction
from control.engine import GameServer

ROOT = Path(__file__).resolve().parents[1]
TICK = 1 / 44


# --- the package, as the Console sees it -----------------------------------

def test_package_is_discovered_and_visible_in_the_console():
    reg = BitRegistry.discover()
    assert "MinigameBit" in reg.packages, reg.errors
    row = next(r for r in reg.list_view(include_hidden=False)
               if r["name"] == "MinigameBit")
    assert row["hidden"] is False
    cfg = reg.resolve_config("MinigameBit", {})
    assert cfg.join_node() == MINIGAME_PLAYER_NODE
    assert reg.bit_class("MinigameBit") is MinigameBit


# --- handlers, unit level --------------------------------------------------

def _joined_bit():
    bit = MinigameBit()
    bit.on_join("ie1", "player")
    bit.on_run_start()
    return bit


def test_run_start_keeps_the_player_that_joined_in_setup():
    assert _joined_bit().status()["dev"] == "ie1"


def test_hold_starts_the_round_and_fires_blinks_on_a_2s_grid():
    bit = _joined_bit()
    assert bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=10.0) == []
    assert bit.status()["phase"] == "INGAME"
    assert bit.fires(10.0) == [FireFunction("blink", dev="ie1", at=10.0)]
    assert bit.fires(11.9) == []
    assert bit.fires(12.0) == [FireFunction("blink", dev="ie1", at=12.0)]


def test_tenth_blink_ends_the_round():
    bit = _joined_bit()
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=0.0)
    fired = bit.fires(BLINK_INTERVAL_S * BLINK_COUNT)
    assert len(fired) == BLINK_COUNT
    assert bit.status() == {"phase": "END", "dev": "ie1",
                            "blinks": BLINK_COUNT}
    assert bit.fires(1000.0) == []


def test_hold_outside_pending_does_not_restart_the_round():
    bit = _joined_bit()
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=0.0)
    bit.fires(2.0)
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=3.0)
    assert bit.status()["blinks"] == 2


def test_tap_resets_to_pending_from_any_phase():
    bit = _joined_bit()
    tap = bit.verb_handlers()["tap"]
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=0.0)
    bit.fires(4.0)
    assert tap("ie1", ["ie1", 0.0, 80.0, 1], at=5.0) == []
    assert bit.status() == {"phase": "PENDING", "dev": "ie1", "blinks": 0}


def test_gestures_from_another_device_are_ignored():
    bit = _joined_bit()
    bit.verb_handlers()["hold"]("ie2", ["ie2", 0.8, 1], at=0.0)
    assert bit.status()["phase"] == "PENDING"


def test_never_completes_on_its_own():
    assert _joined_bit().update(3600.0) is False


# --- end to end through a real GameServer ----------------------------------

def _server(now):
    catalog = load_catalog(ROOT / "instruments")
    testshroom = catalog.get("published", "testshroom").instrument
    gs = GameServer({"MinigameBit": MinigameBit}, clock=lambda: now[0],
                    carried_instruments={"testshroom": testshroom})
    solid = []
    gs.on_solid_cue = lambda *a: solid.append(a)
    gs.load_bit("MinigameBit")
    gs.hello("ie1", "testshroom-dev", "1", instrument="testshroom")
    return gs, solid


def _play_a_round(gs, solid, now):
    assert gs.data("ie1", "hold", ["ie1", 0.8, 1]) is None
    for _ in range(int((BLINK_COUNT * BLINK_INTERVAL_S + 1) / TICK)):
        now[0] += TICK
        gs.tick(TICK)
    assert gs.bit.status()["phase"] == "END"
    assert len(solid) == BLINK_COUNT
    assert all(c[0] == "ie1" and c[1] == BLINK_RGB for c in solid)
    assert gs.data("ie1", "tap", ["ie1", 0.0, 80.0, 1]) is None
    assert gs.bit.status()["phase"] == "PENDING"


def test_join_in_setup_then_start_plays_a_round():
    """The lobby order: the regression that shipped in 1ced5e9."""
    now = [100.0]
    gs, solid = _server(now)
    assert gs.join("ie1", MINIGAME_PLAYER_NODE).granted
    assert gs.request_start(None, "ie1", "device") is None
    assert gs.bit.status()["dev"] == "ie1"
    _play_a_round(gs, solid, now)


def test_start_then_join_plays_a_round():
    now = [100.0]
    gs, solid = _server(now)
    gs.run()
    assert gs.join("ie1", MINIGAME_PLAYER_NODE).granted
    _play_a_round(gs, solid, now)
