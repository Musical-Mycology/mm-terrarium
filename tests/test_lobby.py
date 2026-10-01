"""control/lobby.py: the pure lobby core (spec section 2, 5)."""
from control.lobby import (
    CeremonySlots, DEFAULT_LOBBY, FEEDBACK_ACCEPT,
    FEEDBACK_MINIMUM, FEEDBACK_NONE, FEEDBACK_REFUSED, InviteSchedule,
    LobbyConfig, NOTE_SCALE, TERRARIUM_ADMIN,
    hue_drift_cc, lobby_light_manifest, scale_note)
from control.start_condition import decide_start


def test_reserved_admin_id_and_defaults():
    assert TERRARIUM_ADMIN == "terrarium"
    assert DEFAULT_LOBBY == LobbyConfig(enabled=True, invite_interval_s=5.0,
                                        ceremony_gap_s=1.0)


def test_scale_climbs_a_major_and_wraps_every_seven():
    assert NOTE_SCALE == (69, 71, 73, 74, 76, 78, 80)
    assert [scale_note(i) for i in range(9)] == [69, 71, 73, 74, 76, 78, 80, 69, 71]


# The free lobby_state(counts, role_table) is gone (spec 2026-10-01 cleanup
# 10: one copy). FULL now follows validated devices against scored_cap();
# see GameServer.lobby_state and tests/test_engine_handshake.py.


def test_hue_drift_is_a_triangle_over_the_period():
    assert hue_drift_cc(0.0) == 0
    assert hue_drift_cc(10.0) == 127
    assert hue_drift_cc(20.0) == 0
    assert hue_drift_cc(5.0) == 64


def test_lobby_light_manifest_drives_hue_and_level_lanes():
    m = lobby_light_manifest()
    decl = m["instruments"][0]
    assert decl["instrument"] == "aurora" and decl["target"] == "primary"
    assert "level" in decl["params"]
    assert {(l["source"], l["dest"]) for l in decl["lanes"]} == {("cc:74", "hue"), ("cc:11", "level")}


def _decide(**kw):
    base = dict(bit_loaded=True, in_setup=True, when="admin",
                expected_key="k", key="k", admin=False, scored=2, min_scored=2)
    base.update(kw)
    return decide_start(**base)


def test_start_rule_no_bit_is_silent():
    d = _decide(bit_loaded=False)
    assert (d.accepted, d.reason, d.feedback) == (False, "no Bit loaded", FEEDBACK_NONE)


def test_start_rule_bad_key_is_silent():
    d = _decide(key="wrong")
    assert (d.accepted, d.reason, d.feedback) == (False, "bad key", FEEDBACK_NONE)
    d = _decide(expected_key=None)
    assert d.reason == "bad key"


def test_start_rule_keyed_start_on_a_non_admin_bit_is_refused_silently():
    d = _decide(when="players")
    assert d.reason == "Bit does not take an admin start"
    assert d.feedback == FEEDBACK_NONE


def test_start_rule_unkeyed_operator_start_works_on_any_bit():
    d = _decide(when="players", key=None, admin=True)
    assert d.accepted and d.feedback == FEEDBACK_ACCEPT


def test_start_rule_valid_key_outside_setup_gives_three_red():
    d = _decide(in_setup=False)
    assert (d.accepted, d.reason, d.feedback) == (False, "not in SETUP", FEEDBACK_REFUSED)
    d = _decide(in_setup=False, key=None, admin=True)
    assert d.feedback == FEEDBACK_NONE


def test_start_rule_admin_overrides_the_minimum():
    d = _decide(admin=True, scored=0)
    assert d.accepted and d.feedback == FEEDBACK_ACCEPT


def test_start_rule_minimum_not_met_gives_two_red():
    d = _decide(scored=1)
    assert (d.accepted, d.reason, d.feedback) == (False, "minimum not met", FEEDBACK_MINIMUM)


def test_start_rule_zero_minimum_allows_an_empty_start():
    d = _decide(scored=0, min_scored=0)
    assert d.accepted


def test_invite_schedule_flashes_now_then_every_interval():
    inv = InviteSchedule(5.0)
    assert inv.invited("d") is False
    assert inv.due("d", 100.0) is True
    assert inv.invited("d") is True
    assert inv.due("d", 103.0) is False
    assert inv.due("d", 105.0) is True
    inv.forget("d")
    assert inv.invited("d") is False
    inv.due("e", 1.0)
    inv.clear()
    assert inv.invited("e") is False


def test_ceremony_slots_space_joins_by_span_plus_gap():
    slots = CeremonySlots(span_s=1.8, gap_s=1.0)
    assert slots.reserve(100.0) == 100.0
    assert slots.reserve(100.1) == 102.8
    assert slots.reserve(200.0) == 200.0
