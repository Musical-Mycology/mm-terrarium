"""Rev1Bit: the venue-loadable bench check for Rev 1 boards (bits/rev1/).
Unit-level handler checks, the capability gate against the published
instrument, and the gestures end to end through a real GameServer."""
from pathlib import Path

import pytest

from bits.rev1.rev1_bit import (
    HOLD_RGB,
    REV1_CAPABILITIES,
    REV1_PLAYER_NODE,
    REV1_SIM_NODE,
    SWING_NEG_RGB,
    SWING_POS_RGB,
    TAP_HUE_STEPS,
    Rev1Bit,
)
from control.bit_registry import BitRegistry
from control.catalog import load_catalog
from control.cues import PlayCue, SolidCue
from control.engine import GameServer

ROOT = Path(__file__).resolve().parents[1]


def _catalog():
    return load_catalog(ROOT / "instruments")


# --- the package, as the Console sees it -----------------------------------

def test_package_is_discovered_and_visible_in_the_console():
    reg = BitRegistry.discover()
    assert "Rev1Bit" in reg.packages, reg.errors
    row = next(r for r in reg.list_view(include_hidden=False)
               if r["name"] == "Rev1Bit")
    assert row["hidden"] is False
    cfg = reg.resolve_config("Rev1Bit", {})
    assert cfg.node_for("player") == REV1_PLAYER_NODE
    assert cfg.node_for("sim") == REV1_SIM_NODE
    assert reg.bit_class("Rev1Bit") is Rev1Bit


def test_run_stacks_spawned_devices_join_the_sim_node():
    """run_stack spawns its default devices as `testshroom`, which the rev1
    gate refuses, so the manifest's default join role is the sim node."""
    cfg = BitRegistry.discover().resolve_config("Rev1Bit", {})
    assert cfg.join_node() == REV1_SIM_NODE


# --- the capability gate ---------------------------------------------------

def test_capabilities_match_the_published_tuneshroom_rev1_instrument():
    entry = _catalog().get("published", "tuneshroom_rev1")
    assert entry is not None and entry.error is None
    assert REV1_CAPABILITIES == entry.instrument.capabilities


def test_capabilities_match_the_contract_kits_gate():
    from contract_kit.contract_bit import REV1_CAPABILITIES as CONTRACT_CAPS
    assert REV1_CAPABILITIES == CONTRACT_CAPS


def test_player_role_requires_rev1_and_declares_the_firmware_samples():
    rt = Rev1Bit().role_table
    player = rt.roles["player"]
    assert rt.node_map[REV1_PLAYER_NODE] == ["player"]
    assert player.requires == "rev1"
    assert player.breath is False
    assert set(player.uses) == {"tap", "hold", "swing"}
    assert player.samples == ["tick", "hold"]


def test_sim_role_asks_only_for_tap():
    rt = Rev1Bit().role_table
    sim = rt.roles["sim"]
    assert rt.node_map[REV1_SIM_NODE] == ["sim"]
    assert sim.requires == "sim"
    assert sim.uses == ["tap"]
    assert sim.light_manifest == rt.roles["player"].light_manifest


# --- handlers, unit level --------------------------------------------------

def test_taps_play_tick_and_step_the_hue_then_wrap():
    bit = Rev1Bit()
    tap = bit.verb_handlers()["tap"]
    values = []
    for _ in range(len(TAP_HUE_STEPS) + 1):
        cues = tap("ie1", ["ie1", 0.0, 80.0, 1], at=10.0)
        assert PlayCue("ie1", "tick", "") in cues
        values.append(next(c for c in cues if isinstance(c, tuple))[3])
    assert values == [*TAP_HUE_STEPS, TAP_HUE_STEPS[0]]
    assert bit.status()["taps"] == len(TAP_HUE_STEPS) + 1


def test_hold_plays_hold_and_flashes_white():
    bit = Rev1Bit()
    cues = bit.verb_handlers()["hold"]("ie1", ["ie1", 0.65, 1], at=10.0)
    assert PlayCue("ie1", "hold", "") in cues
    solid = next(c for c in cues if isinstance(c, SolidCue))
    assert solid.rgb == HOLD_RGB
    assert bit.status()["holds"] == 1
    assert bit.status()["last_held_s"] == pytest.approx(0.65)


@pytest.mark.parametrize("g,rgb", [(-2.1, SWING_NEG_RGB), (1.8, SWING_POS_RGB)])
def test_swing_flash_color_follows_the_sign(g, rgb):
    bit = Rev1Bit()
    cues = bit.verb_handlers()["swing"]("ie1", ["ie1", g, 1], at=10.0)
    assert [c.rgb for c in cues if isinstance(c, SolidCue)] == [rgb]
    assert bit.status()["last_swing_g"] == pytest.approx(g)


def test_never_completes_on_its_own():
    bit = Rev1Bit()
    assert bit.update(3600.0) is False


# --- end to end through a real GameServer ----------------------------------

def _server():
    catalog = _catalog()
    rev1 = catalog.get("published", "tuneshroom_rev1").instrument
    testshroom = catalog.get("published", "testshroom").instrument
    gs = GameServer({"Rev1Bit": Rev1Bit}, clock=lambda: 100.0,
                    carried_instruments={"tuneshroom_rev1": rev1,
                                         "testshroom": testshroom})
    light, play, solid = [], [], []
    gs.on_light_cue = lambda *a: light.append(a)
    gs.on_play_cue = lambda *a: play.append(a)
    gs.on_solid_cue = lambda *a: solid.append(a)
    gs.load_bit("Rev1Bit")
    return gs, light, play, solid


def test_only_a_rev1_carrier_is_granted_the_player_role():
    gs, *_ = _server()
    gs.hello("ie1", "rev1-board", "1", instrument="tuneshroom_rev1")
    granted = gs.join("ie1", REV1_PLAYER_NODE)
    assert granted.granted and granted.role == "player"

    for dev, instrument in (("ie2", "tuneshroom"), ("ie3", "testshroom")):
        gs.hello(dev, f"{instrument}-dev", "1", instrument=instrument)
        assert not gs.join(dev, REV1_PLAYER_NODE).granted, instrument


def test_every_gesture_reaches_the_board_through_the_engine():
    gs, light, play, solid = _server()
    gs.hello("ie1", "rev1-board", "1", instrument="tuneshroom_rev1")
    assert gs.join("ie1", REV1_PLAYER_NODE).granted
    gs.run()

    assert gs.data("ie1", "tap", ["ie1", 0.0, 80.0, 1]) is None
    assert gs.data("ie1", "hold", ["ie1", 0.65, 1]) is None
    assert gs.data("ie1", "swing", ["ie1", -2.1, 1]) is None

    assert play == [("ie1", "tick", ""), ("ie1", "hold", "")]
    assert [(c[0], c[2], c[3]) for c in light] == [("ie1", 74, TAP_HUE_STEPS[0])]
    assert [(c[0], c[1]) for c in solid] == [("ie1", HOLD_RGB),
                                            ("ie1", SWING_NEG_RGB)]
    assert gs.bit.status()["taps"] == 1
    assert gs.bit.status()["holds"] == 1
    assert gs.bit.status()["swings"] == 1


@pytest.mark.parametrize("instrument",
                         ["testshroom", "tuneshroom", "tuneshroom_rev1"])
def test_any_tap_capable_shroom_joins_the_sim_node_and_its_tap_lands(instrument):
    gs, light, play, _ = _server()
    gs.hello("ie1", f"{instrument}-dev", "1", instrument=instrument)
    granted = gs.join("ie1", REV1_SIM_NODE)
    assert granted.granted and granted.role == "sim"
    gs.run()

    assert gs.data("ie1", "tap", ["ie1", 1.0, 50.0, 1]) is None
    assert play == [("ie1", "tick", "")]
    assert [(c[0], c[2], c[3]) for c in light] == [("ie1", 74, TAP_HUE_STEPS[0])]


def test_a_gesture_from_an_unjoined_device_is_refused():
    gs, _, play, solid = _server()
    gs.hello("ie1", "rev1-board", "1", instrument="tuneshroom_rev1")
    assert gs.data("ie1", "hold", ["ie1", 0.65, 1]) == "device not registered"
    assert play == [] and solid == []
