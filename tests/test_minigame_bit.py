"""MinigameBit: the Tower progress bar (bits/minigame/). Package discovery,
the tower role's surface, the tap arithmetic at unit level, and role
routing (ie<N> players, devtower<N> Towers) through a real GameServer."""
from pathlib import Path

import pytest

from bits.minigame.minigame_bit import (
    LIGHT_CC_BASE,
    MINIGAME_PLAYER_NODE,
    MinigameBit,
    light_level,
)
from control.bit_registry import BitRegistry
from control.catalog import load_catalog
from control.cues import LightCue
from control.engine import GameServer
from control.jam_role import is_solo_role
from control.role_config import validate_role_declarations

ROOT = Path(__file__).resolve().parents[1]
PROGRESS_LIGHTS = 4
TAP = ["ie1", 0.0, 80.0, 1]


def cc(tower, k, value, when):
    return LightCue(tower, 0xB0, LIGHT_CC_BASE + k, value, when=when)


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


def test_needs_no_room_light():
    assert MinigameBit().room_manifests() == ({}, {})
    assert MinigameBit.room_types == {"TEST"}


# --- the tower role --------------------------------------------------------

def test_the_tower_role_lights_four_single_pixel_zones():
    tower = MinigameBit().role_table.roles["tower"]
    assert tower.zones == tuple((f"p{k}", k, 1)
                                for k in range(PROGRESS_LIGHTS))
    assert tower.breath is False and tower.scored is False
    assert [(d["target"], d["lanes"][0]["source"], d["params"]["level"])
            for d in tower.light_manifest["instruments"]] == [
        (f"p{k}", f"cc:{LIGHT_CC_BASE + k}", 0.0)
        for k in range(PROGRESS_LIGHTS)]


def test_the_role_table_validates():
    validate_role_declarations(MinigameBit().role_table)


# --- taps, unit level ------------------------------------------------------

def _joined_bit(*devs, towers=("devtower1",)):
    bit = MinigameBit()
    for dev in devs or ("ie1",):
        bit.on_join(dev, "player")
    for dev in towers:
        bit.on_join(dev, "tower")
    bit.on_run_start()
    return bit


def _synced_bit(*devs, towers=("devtower1",)):
    """Joined, with every Tower already shown the (empty) bar once."""
    bit = _joined_bit(*devs, towers=towers)
    bit._unsynced.clear()
    return bit


@pytest.mark.parametrize("progress, levels", [
    (0, [0.0] * 4),
    (1, [0.04] + [0.0] * 3),
    (25, [1.0] + [0.0] * 3),
    (30, [1.0, 0.2] + [0.0] * 2),
    (100, [1.0] * 4),
])
def test_one_percent_is_a_twentyfifth_of_one_light(progress, levels):
    assert [light_level(progress, k) for k in range(4)] == \
        pytest.approx(levels)


def test_a_new_tower_gets_the_whole_bar_on_the_next_tap():
    bit = _joined_bit()
    out = bit.verb_handlers()["tap"]("ie1", TAP, at=5.0)
    assert out == [cc("devtower1", 0, 5, 5.0)] + [
        cc("devtower1", k, 0, 5.0) for k in range(1, PROGRESS_LIGHTS)]


def test_each_tap_adds_one_percent_to_the_light_it_lands_in():
    bit = _synced_bit()
    tap = bit.verb_handlers()["tap"]
    assert tap("ie1", TAP, at=5.0) == [cc("devtower1", 0, 5, 5.0)]
    for _ in range(24):
        out = tap("ie1", TAP, at=6.0)
    assert out == [cc("devtower1", 0, 127, 6.0)]
    assert tap("ie1", TAP, at=7.0) == [            # 26%: the second light
        cc("devtower1", 1, 5, 7.0)]
    assert bit.status()["progress"] == 26


def test_every_tower_shows_the_same_bar():
    bit = _synced_bit(towers=("devtower1", "devtower2"))
    assert bit.verb_handlers()["tap"]("ie1", TAP, at=1.0) == [
        cc("devtower1", 0, 5, 1.0), cc("devtower2", 0, 5, 1.0)]


def test_a_tower_joining_late_catches_up_while_the_others_get_the_change():
    bit = _synced_bit()
    tap = bit.verb_handlers()["tap"]
    for _ in range(29):
        tap("ie1", TAP, at=0.0)
    bit.on_join("devtower2", "tower")
    out = tap("ie1", TAP, at=1.0)                  # 30%
    assert [c for c in out if c.dev == "devtower1"] == \
        [cc("devtower1", 1, 25, 1.0)]
    assert [c.data2 for c in out if c.dev == "devtower2"] == \
        [127, 25] + [0] * 2


def test_every_player_fills_the_same_bar():
    bit = _synced_bit("ie1", "ie2")
    tap = bit.verb_handlers()["tap"]
    tap("ie1", TAP, at=0.0)
    tap("ie2", ["ie2", 0.0, 80.0, 1], at=0.0)
    assert bit.status() == {"progress": 2, "players": ["ie1", "ie2"],
                            "towers": ["devtower1"]}


def test_the_bar_holds_full_at_100_percent():
    bit = _synced_bit()
    tap = bit.verb_handlers()["tap"]
    for _ in range(100):
        out = tap("ie1", TAP, at=0.0)
    assert out == [cc("devtower1", 3, 127, 0.0)]
    assert tap("ie1", TAP, at=1.0) == []
    assert bit.status()["progress"] == 100


def test_taps_from_a_tower_or_a_stranger_are_ignored():
    bit = _synced_bit()
    tap = bit.verb_handlers()["tap"]
    assert tap("devtower1", ["devtower1", 0.0, 80.0, 1], at=0.0) == []
    assert tap("other", ["other", 0.0, 80.0, 1], at=0.0) == []
    assert bit.status()["progress"] == 0


def test_taps_with_no_tower_still_count():
    bit = _joined_bit(towers=())
    assert bit.verb_handlers()["tap"]("ie1", TAP, at=0.0) == []
    assert bit.status()["progress"] == 1


def test_a_multi_tap_gesture_counts_every_tap():
    bit = _synced_bit()
    assert bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 2],
                                      at=0.0) == [cc("devtower1", 0, 10, 0.0)]
    assert bit.status()["progress"] == 2


def test_a_tap_with_no_count_still_counts_once():
    bit = _synced_bit()
    bit.verb_handlers()["tap"]("ie1", ["ie1"], at=0.0)
    assert bit.status()["progress"] == 1


def test_never_completes_on_its_own():
    assert _joined_bit().update(3600.0) is False


# --- role routing through a real GameServer --------------------------------

def test_devices_are_admitted_by_their_id():
    bit = MinigameBit()
    assert bit.admits("ie1", "player") and bit.admits("ie12", "player")
    assert bit.admits("devtower1", "tower") and bit.admits("devtower12", "tower")
    for dev in ("devtower", "devtower1x", "tower1", "ie", "ie2-rev1",
                "other"):
        assert not bit.admits(dev, "tower")
    for dev in ("devtower1", "ie", "ie2-rev1", "other"):
        assert not bit.admits(dev, "player")


def _running_gs():
    now = [100.0]
    catalog = load_catalog(ROOT / "instruments")
    testshroom = catalog.get("published", "testshroom").instrument
    gs = GameServer({"MinigameBit": MinigameBit}, clock=lambda: now[0],
                    carried_instruments={"testshroom": testshroom})
    gs.load_bit("MinigameBit")
    grants = []
    gs.on_grant = lambda dev, result: grants.append((dev, result))
    return gs, grants


def test_towers_players_and_strangers_each_get_their_own_role():
    gs, grants = _running_gs()
    for dev in ("other", "ie1", "ie2", "devtower1"):
        gs.hello(dev, "dev", "1", instrument="testshroom")
    gs.run()
    roles = {dev: a[1] for dev, a in gs.registration.assignments.items()}
    assert is_solo_role(roles.pop("other"))
    assert roles == {"ie1": "player", "ie2": "player", "devtower1": "tower"}
    assert gs.bit.status()["players"] == ["ie1", "ie2"]
    assert gs.bit.status()["towers"] == ["devtower1"]
    tower_grant = dict(grants)["devtower1"]
    assert tower_grant.zones == tuple((f"p{k}", k, 1)
                                      for k in range(PROGRESS_LIGHTS))
    assert dict(grants)["ie1"].zones == ()
    # A walk-up after start is sorted the same way.
    gs.hello("devtower2", "dev", "1", instrument="testshroom")
    gs.hello("ie3", "dev", "1", instrument="testshroom")
    assert gs.registration.assignments["devtower2"][1] == "tower"
    assert gs.registration.assignments["ie3"][1] == "player"
    # Only a player's tap moves the bar.
    gs.data("other", "tap", ["other", 0.0, 80.0, 1])
    gs.data("devtower1", "tap", ["devtower1", 0.0, 80.0, 1])
    gs.data("ie1", "tap", TAP)
    assert gs.bit.status()["progress"] == 1


def test_a_tap_lights_every_tower_through_the_engine():
    gs, _ = _running_gs()
    cues = []
    gs.on_light_cue = lambda dev, *rest: cues.append((dev,) + tuple(rest))
    for dev in ("ie1", "devtower1", "devtower2"):
        gs.hello(dev, "dev", "1", instrument="testshroom")
    gs.run()
    gs.data("ie1", "tap", TAP)
    lit = {(dev, d1, d2) for dev, _st, d1, d2, *_ in cues if d2}
    assert lit == {("devtower1", LIGHT_CC_BASE, 5),
                   ("devtower2", LIGHT_CC_BASE, 5)}
