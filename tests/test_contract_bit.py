"""ContractBit: the device contract kit's test-only fixture (docs/superpowers/
specs/2026-09-16-device-contract-kit-design.md section 5.4). Exercised
directly here (unit-level, no transport); contract_kit/recorder.py and
tests/test_contract_scenarios.py exercise it end to end over the wire."""
from pathlib import Path

import pytest

pytest.importorskip("luxaeterna")

from control.catalog import load_catalog
from control.cues import LightCue, PlayCue
from control.engine import GameServer
from control.instrument import DEFAULTSHROOM, satisfies
from tests.helpers_admit import admit
from contract_kit.contract_bit import (
    CONTRACT_JAM_NODE,
    CONTRACT_PLAYER_NODE,
    JAMMER_REFUSAL,
    REV1_CAPABILITIES,
    ContractBit,
)
from contract_kit.solo_contract_bit import SoloContractBit
from control.roles import RoleClass

ROOT = Path(__file__).resolve().parents[1]


def _catalog():
    return load_catalog(ROOT / "instruments")


def test_role_table_has_one_scored_player_node_requiring_rev1():
    bit = ContractBit()
    rt = bit.role_table
    player = rt.roles["player"]
    assert player.scored
    assert rt.node_map[CONTRACT_PLAYER_NODE] == ["player"]
    assert player.requires == "rev1"
    # Contract v3: a bounded scored role, so one accept fills the round.
    assert player.role_class is RoleClass.UNIQUE and player.capacity == 1


def test_role_table_has_an_unscored_jam_role_using_tap():
    rt = ContractBit().role_table
    jammer = rt.roles["jammer"]
    assert jammer.role_class is RoleClass.JAM and not jammer.scored
    assert jammer.uses == ["tap"] and jammer.requires is None
    assert rt.node_map[CONTRACT_JAM_NODE] == ["jammer"]


def test_solo_contract_bit_has_the_same_player_and_no_jam_role():
    rt = SoloContractBit().role_table
    assert set(rt.roles) == {"player"}
    assert rt.roles["player"] == ContractBit().role_table.roles["player"]


def test_a_jammers_hold_and_swing_are_refused_and_a_players_are_not():
    bit = ContractBit()
    bit.on_join("jam1", "jammer")
    bit.on_join("ply1", "player")
    handlers = bit.verb_handlers()
    assert handlers["hold"]("jam1", ["jam1", 0.65, 1], at=1.0) == JAMMER_REFUSAL
    assert handlers["swing"]("jam1", ["jam1", 1.8, 1], at=1.0) == JAMMER_REFUSAL
    assert handlers["hold"]("ply1", ["ply1", 0.65, 1], at=1.0) != JAMMER_REFUSAL
    assert handlers["swing"]("ply1", ["ply1", 1.8, 1], at=1.0) == []


def test_instrument_requirements_admit_rev1_and_refuse_defaultshroom():
    req = ContractBit().instrument_requirements()[0]
    assert req.slot == "rev1"
    assert req.capabilities == frozenset({
        "light.pixels", "gesture.tap", "gesture.hold", "gesture.swing",
        "audio.samples"})
    assert satisfies(DEFAULTSHROOM, req) is not None   # no hold/swing/samples


def test_rev1_capabilities_matches_the_published_tuneshroom_rev1_instrument():
    """The Bit's gate and the instrument catalog entry must not drift apart:
    both are supposed to describe the same Rev 1 hardware capability set."""
    entry = _catalog().get("published", "tuneshroom_rev1")
    assert entry is not None and entry.error is None
    assert REV1_CAPABILITIES == entry.instrument.capabilities


def test_first_tap_returns_a_play_and_a_future_light_cue():
    bit = ContractBit()
    cues = bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 1], at=10.0)
    plays = [c for c in cues if isinstance(c, PlayCue)]
    lights = [c for c in cues if isinstance(c, LightCue)]
    assert plays and plays[0].name == "tick"
    assert lights and lights[0].when == pytest.approx(10.0 + 0.5)


def test_second_tap_at_the_same_at_targets_the_same_when_with_a_new_value():
    bit = ContractBit()
    first = bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 1], at=10.0)
    second = bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 1], at=10.0)
    light1 = next(c for c in first if isinstance(c, LightCue))
    light2 = next(c for c in second if isinstance(c, LightCue))
    assert light1.when == light2.when
    assert light1.data2 != light2.data2


def test_hold_plays_an_unknown_sample_name():
    bit = ContractBit()
    cues = bit.verb_handlers()["hold"]("ie1", ["ie1", 0.65, 1], at=10.0)
    assert any(isinstance(c, PlayCue) and c.name == "not_a_real_sample"
              for c in cues)


def test_swing_is_handled_with_no_cues():
    bit = ContractBit()
    assert bit.verb_handlers()["swing"]("ie1", ["ie1", -2.1, 1], at=10.0) == []


def test_gameserver_grants_player_role_to_tuneshroom_rev1_and_refuses_others():
    """Real engine behavior, not a restated literal: a device that hello's
    in carrying tuneshroom_rev1 is granted CONTRACT_PLAYER_NODE's player
    role by a real GameServer; one carrying tuneshroom (the base handheld,
    seeded by GameServer itself) or testshroom (the harness fixture, no
    hold/swing) is refused, because neither has the Rev 1 gesture set."""
    catalog = _catalog()
    rev1 = catalog.get("published", "tuneshroom_rev1").instrument
    testshroom = catalog.get("published", "testshroom").instrument
    gs = GameServer({"ContractBit": ContractBit},
                     carried_instruments={"tuneshroom_rev1": rev1,
                                          "testshroom": testshroom})
    gs.load_bit("ContractBit")

    gs.hello("dev_rev1", "rev1-dev", "1", instrument="tuneshroom_rev1")
    # Handshake validates in SETUP (spec 3.2); the role is bound at start.
    granted = admit(gs, "dev_rev1", CONTRACT_PLAYER_NODE)
    assert granted.granted
    assert granted.role == "player"

    gs.hello("dev_tune", "tune-dev", "1", instrument="tuneshroom")
    refused_tune = admit(gs, "dev_tune", CONTRACT_PLAYER_NODE)
    assert not refused_tune.granted

    gs.hello("dev_test", "test-dev", "1", instrument="testshroom")
    refused_test = admit(gs, "dev_test", CONTRACT_PLAYER_NODE)
    assert not refused_test.granted
