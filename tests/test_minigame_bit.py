"""MinigameBit: the Tower progress bar (bits/minigame/). Package discovery,
the tap arithmetic at unit level, and role routing through a real
GameServer."""
from dataclasses import replace
from pathlib import Path

import pytest

from bits.minigame.minigame_bit import (
    LIGHT_CC_BASE,
    MINIGAME_PLAYER_NODE,
    TOWER_FIXTURE,
    MinigameBit,
    light_level,
)
from control.bit_registry import BitRegistry
from control.catalog import load_catalog
from control.cues import LightCue, fixture_dev
from control.engine import GameServer
from control.room_profile import RoomZone
from control.rooms import Room
from control.jam_role import is_solo_role
from control.role_config import slice_light_manifest
from control.terrarium_config import load_terrarium_config

ROOT = Path(__file__).resolve().parents[1]
PROGRESS_LIGHTS = 10
TAP = ["ie1", 0.0, 80.0, 1]
TOWER = fixture_dev(TOWER_FIXTURE)


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


# --- the TOWER_BAR Room ------------------------------------------------------

def _tower_bar():
    return load_terrarium_config(ROOT / "terrarium.toml").rooms["TOWER_BAR"]


def test_tower_bar_room_is_one_10_light_fixture_one_zone_per_light():
    room = _tower_bar()
    assert room.node_id == "ROOM_TOWER_NODE"
    (fx,) = room.profile.fixtures
    assert fx.name == TOWER_FIXTURE and fx.pixel_count == PROGRESS_LIGHTS
    assert [(z.name, z.start, z.count) for z in fx.zones] == [
        (f"p{k}", k, 1) for k in range(PROGRESS_LIGHTS)]


def test_each_light_is_a_dark_aurora_on_its_own_cc_lane():
    light, ugen = MinigameBit().room_manifests()
    assert ugen == {}
    sliced = slice_light_manifest(light, _tower_bar().profile, TOWER_FIXTURE)
    assert [(d["target"], d["lanes"][0]["source"], d["params"]["level"])
            for d in sliced["instruments"]] == [
        (f"p{k}", f"cc:{LIGHT_CC_BASE + k}", 0.0)
        for k in range(PROGRESS_LIGHTS)]


# --- taps, unit level ------------------------------------------------------

def _joined_bit(*devs):
    bit = MinigameBit()
    for dev in devs or ("ie1",):
        bit.on_join(dev, "player")
    bit.on_run_start()
    return bit


@pytest.mark.parametrize("progress, levels", [
    (0, [0.0] * 10),
    (1, [0.1] + [0.0] * 9),
    (10, [1.0] + [0.0] * 9),
    (15, [1.0, 0.5] + [0.0] * 8),
    (100, [1.0] * 10),
])
def test_one_percent_is_a_tenth_of_one_light(progress, levels):
    assert [light_level(progress, k, 10) for k in range(10)] == \
        pytest.approx(levels)


@pytest.mark.parametrize("lights, progress, levels", [
    (4, 25, [1.0, 0.0, 0.0, 0.0]),
    (4, 30, [1.0, 0.2, 0.0, 0.0]),
    (3, 50, [1.0, 0.5, 0.0]),
    (8, 100, [1.0] * 8),
])
def test_progress_spreads_over_however_many_lights(lights, progress, levels):
    assert [light_level(progress, k, lights) for k in range(lights)] == \
        pytest.approx(levels)


def test_each_tap_adds_one_percent_to_the_light_it_lands_in():
    bit = _joined_bit()
    tap = bit.verb_handlers()["tap"]
    assert tap("ie1", TAP, at=5.0) == [
        LightCue(TOWER, 0xB0, LIGHT_CC_BASE, 13, when=5.0)]
    for _ in range(9):
        out = tap("ie1", TAP, at=6.0)
    assert out == [LightCue(TOWER, 0xB0, LIGHT_CC_BASE, 127, when=6.0)]
    assert tap("ie1", TAP, at=7.0) == [            # 11%: the second light
        LightCue(TOWER, 0xB0, LIGHT_CC_BASE + 1, 13, when=7.0)]
    assert bit.status()["progress"] == 11


def test_every_player_fills_the_same_bar():
    bit = _joined_bit("ie1", "ie2")
    tap = bit.verb_handlers()["tap"]
    tap("ie1", TAP, at=0.0)
    tap("ie2", ["ie2", 0.0, 80.0, 1], at=0.0)
    assert bit.status() == {"progress": 2, "players": ["ie1", "ie2"]}


def test_the_bar_holds_full_at_100_percent():
    bit = _joined_bit()
    tap = bit.verb_handlers()["tap"]
    for _ in range(100):
        out = tap("ie1", TAP, at=0.0)
    assert out == [LightCue(TOWER, 0xB0, LIGHT_CC_BASE + 9, 127, when=0.0)]
    assert tap("ie1", TAP, at=1.0) == []
    assert bit.status()["progress"] == 100


def test_taps_from_a_non_player_are_ignored():
    bit = _joined_bit()
    assert bit.verb_handlers()["tap"]("tower", ["tower", 0.0, 80.0, 1],
                                      at=0.0) == []
    assert bit.status()["progress"] == 0


def test_never_completes_on_its_own():
    assert _joined_bit().update(3600.0) is False


# --- role routing through a real GameServer --------------------------------

def test_only_ie_devices_are_admitted_to_the_player_role():
    bit = MinigameBit()
    assert bit.admits("ie1", "player")
    assert bit.admits("ie12", "player")
    assert not bit.admits("tower", "player")
    assert not bit.admits("ie", "player")
    assert not bit.admits("ie2-rev1", "player")


def test_a_non_ie_device_beside_two_ie_devices_keeps_its_solo_role():
    now = [100.0]
    catalog = load_catalog(ROOT / "instruments")
    testshroom = catalog.get("published", "testshroom").instrument
    gs = GameServer({"MinigameBit": MinigameBit}, clock=lambda: now[0],
                    carried_instruments={"testshroom": testshroom})
    gs.load_bit("MinigameBit")
    gs.hello("other", "testshroom-dev", "1", instrument="testshroom")
    gs.hello("ie1", "testshroom-dev", "1", instrument="testshroom")
    gs.hello("ie2", "testshroom-dev", "1", instrument="testshroom")
    gs.run()
    roles = {dev: a[1] for dev, a in gs.registration.assignments.items()}
    assert is_solo_role(roles.pop("other"))
    assert roles == {"ie1": "player", "ie2": "player"}
    assert gs.bit.status()["players"] == ["ie1", "ie2"]
    # A walk-up after start is sorted the same way.
    gs.hello("ie3", "testshroom-dev", "1", instrument="testshroom")
    assert gs.registration.assignments["ie3"][1] == "player"
    assert "ie3" in gs.bit.status()["players"]
    # The other device's taps never move the bar.
    gs.data("other", "tap", ["other", 0.0, 80.0, 1])
    gs.data("ie1", "tap", TAP)
    assert gs.bit.status()["progress"] == 1


def test_a_multi_tap_gesture_counts_every_tap():
    bit = _joined_bit()
    assert bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 2],
                                      at=0.0) == [
        LightCue(TOWER, 0xB0, LIGHT_CC_BASE, 25, when=0.0)]
    assert bit.status()["progress"] == 2


def test_a_tap_with_no_count_still_counts_once():
    bit = _joined_bit()
    bit.verb_handlers()["tap"]("ie1", ["ie1"], at=0.0)
    assert bit.status()["progress"] == 1


# --- the bar sizes itself to the Room --------------------------------------

def _room_with_lights(n):
    """TOWER_BAR's profile with the tower cut down to n one-pixel zones."""
    room = _tower_bar()
    (fx,) = room.profile.fixtures
    zones = tuple(RoomZone(name=f"p{k}", start=k, count=1) for k in range(n))
    fixture = replace(fx, zones=zones)
    profile = replace(room.profile, fixtures=(fixture,))
    return Room(name="TOWER_BAR", profile=profile, node_id=room.node_id)


def test_the_bar_is_as_long_as_the_rooms_tower_zones():
    bit = MinigameBit()
    bit.room = _room_with_lights(4)
    light, _ = bit.room_manifests()
    assert [d["target"] for d in light["instruments"]] == [
        f"tower.p{k}" for k in range(4)]
    bit.on_join("ie1", "player")
    tap = bit.verb_handlers()["tap"]
    out = []
    for _ in range(25):            # 25% of 4 lights: the first one full
        out = tap("ie1", TAP, at=0.0)
    assert out == [LightCue(TOWER, 0xB0, LIGHT_CC_BASE, 127, when=0.0)]
    assert tap("ie1", TAP, at=1.0) == [
        LightCue(TOWER, 0xB0, LIGHT_CC_BASE + 1, 5, when=1.0)]


def test_a_room_with_no_tower_fixture_refuses_to_load():
    room = _tower_bar()
    (fx,) = room.profile.fixtures
    bit = MinigameBit()
    bit.room = Room(name="X", node_id="X",
                    profile=replace(room.profile,
                                    fixtures=(replace(fx, name="other"),)))
    with pytest.raises(ValueError, match="no fixture 'tower'"):
        bit.room_manifests()
