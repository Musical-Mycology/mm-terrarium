"""contract_kit/scenarios.py and contract_kit/recordings/.

Three kinds of test live here:

1. The regression test (spec docs/superpowers/specs/
   2026-09-16-device-contract-kit-design.md section 5.4): re-recording every
   scenario must reproduce the committed JSON exactly. A diff means
   Control's real behavior changed; run
   `.venv/bin/python -m tools.record_scenarios`, review the diff, and commit
   the new recording deliberately.
2. The contract check: every real message in every recording names a verb
   devicelink/contract.py declares, with an allowed typespec, the right
   direction and argument types, and everything a device sends before it
   holds a role is a verb the table marks `pre_role`. The malformed-input
   scenario's own flagged steps are required to FAIL that same check. No
   recording asks a device to send a retired verb (contract v3: /game/join).
3. One test per scenario, asserting the recording still demonstrates what
   the spec's scenario table says it covers. A recording that is
   byte-stable but no longer contains its own point is a failed scenario,
   and the regression test alone cannot notice.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("luxaeterna")

from contract_kit.contract_bit import (JAMMER_REFUSAL, KNOWN_SAMPLE,
                                       UNKNOWN_SAMPLE)
from contract_kit.recorder import CUE_HORIZON_S, ROOM_NODE_ID, Recorder
from contract_kit.scenarios import (ACCEPT_AFTER_MS, ACCEPT_POLICY,
                                    ALL_SCENARIOS, AUTHORED_CHECK_T,
                                    DENY_FLASH_CHECK_T, DENY_PULSE_CHECK_T,
                                    READY_PULSE_CHECK_T,
                                    BLIP_BACK_T, BLIP_TAP_T,
                                    AUTHORED_NEWER_AT, AUTHORED_NEWER_GRB,
                                    AUTHORED_OLDER_AT, AUTHORED_OLDER_GRB,
                                    AUTHORED_PAIR_T, GOOD_ACCEPT_T,
                                    HOLD_CHECK_T, LEGACY_JOIN_T, LINK_BACK_T,
                                    LOOK_SETTLED_T, NO_SUCH_NODE,
                                    RIVAL_ACCEPT_T, ROLE_SETTLED_T,
                                    ROOM_ACCEPT_T, SIGNATURE_SETTLED_MS,
                                    STALE_ACCEPT_T, STALE_ROUND_ID, START_T,
                                    VALIDATE_START_T, WALK_UP_T)
from control.lobby import PULSE_PEAK
from devicelink.contract import RETIRED_UP_VERBS, row_for, typespec_allowed

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / "contract_kit" / "recordings"

HORIZON_MS = round(CUE_HORIZON_S * 1000)


# --- helpers ---------------------------------------------------------------

def _load(name):
    return json.loads((RECORDINGS / f"{name}.json").read_text(encoding="utf-8"))


def _sends(data, address=None):
    out = [s for s in data["steps"] if "control_sends" in s]
    if address is not None:
        out = [s for s in out if s["control_sends"]["address"] == address]
    return out


def _outs(data, address=None):
    out = [s for s in data["steps"] if "expect_out" in s]
    if address is not None:
        out = [s for s in out if s["expect_out"]["address"] == address]
    return out


def _kind(data, key):
    return [s for s in data["steps"] if key in s]


def _frames(data):
    return _sends(data, "/$DEV/leds")


def _first_t(data, address):
    """The `t` of the first control_sends for `address`, or None."""
    found = _sends(data, address)
    return found[0]["t"] if found else None


def _direction_and_verb(address):
    if address.startswith("/game/"):
        return "up", address[len("/game/"):]
    if address.startswith("/$DEV/"):
        return "down", address[len("/$DEV/"):]
    raise AssertionError(f"address {address!r} is neither /game/ nor /$DEV/")


_ARG_TYPES = {"s": str, "f": float, "i": int, "b": (list, dict)}


def _contract_violation(address, typespec, args):
    """Why this message is not a valid contract message, or None if it is.

    Used in both directions: every real recorded message must return None,
    and every step the malformed scenario flags must return a reason.
    """
    try:
        direction, verb = _direction_and_verb(address)
    except AssertionError as exc:
        return str(exc)
    try:
        row = row_for(direction, verb)
    except KeyError:
        return f"no {direction} row for verb {verb!r}"
    if not typespec_allowed(direction, verb, typespec):
        return (f"typespec {typespec!r} is not allowed for {address}; "
                f"the table allows {row.typespecs}")
    if len(args) != len(typespec):
        return f"{address} {typespec!r} carries {len(args)} args"
    for code, value in zip(typespec, args):
        want = _ARG_TYPES[code]
        if code == "i" and isinstance(value, bool):
            return f"{address} arg for {code!r} is a bool"
        if not isinstance(value, want):
            return (f"{address} arg {value!r} is a {type(value).__name__}, "
                    f"not the {code!r} the typespec declares")
    return None


# --- 1: the regression test -------------------------------------------------

@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_recording_matches_committed_json(scenario_fn):
    fresh = scenario_fn()
    committed_path = RECORDINGS / f"{fresh['name']}.json"
    assert committed_path.exists(), (
        f"no committed recording for {fresh['name']!r}; run "
        f"`.venv/bin/python -m tools.record_scenarios` and commit it")
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    assert fresh == committed, (
        f"{fresh['name']} has drifted from its committed recording; re-run "
        f"`.venv/bin/python -m tools.record_scenarios` and review the diff "
        f"before committing")


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_the_committed_bytes_are_the_agreed_serialization(scenario_fn):
    """The export format is UTF-8, `indent=2`, `sort_keys=True` and a
    trailing newline. Every device repo diffs these files on re-export, so a
    hand edit or a different writer that happens to parse the same would
    still churn every one of them."""
    path = RECORDINGS / f"{scenario_fn.__name__}.json"
    raw = path.read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"


def test_every_scenario_has_a_recording_and_nothing_else_is_committed():
    """A scenario dropped from ALL_SCENARIOS would leave its recording
    behind, still exported to every device repo and no longer checked."""
    expected = {f"{fn.__name__}.json" for fn in ALL_SCENARIOS}
    assert {p.name for p in RECORDINGS.glob("*.json")} == expected


# --- 2: the contract check --------------------------------------------------

@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_every_recorded_message_is_a_contract_message(scenario_fn):
    data = _load(scenario_fn.__name__)
    checked = 0
    for step in data["steps"]:
        for key in ("control_sends", "expect_out"):
            msg = step.get(key)
            if msg is None:
                continue
            reason = _contract_violation(msg["address"], msg["typespec"],
                                         msg["args"])
            if msg.get("malformed"):
                assert reason is not None, (
                    f"t={step['t']} {key} is flagged malformed but is a "
                    f"valid contract message")
                continue
            assert reason is None, f"t={step['t']} {key}: {reason}"
            checked += 1
    assert checked > 0


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_what_the_device_sends_respects_the_pre_role_column(scenario_fn):
    """Rule 2. Every verb the device sends before the first `/$DEV/role` is
    one the table marks `pre_role`, and every verb the table does NOT mark
    is sent only once a role has been granted.
    """
    data = _load(scenario_fn.__name__)
    role_t = _first_t(data, "/$DEV/role")
    for step in _outs(data):
        _direction, verb = _direction_and_verb(step["expect_out"]["address"])
        row = row_for("up", verb)
        if role_t is None or step["t"] < role_t:
            assert row.pre_role, (
                f"t={step['t']} the device sends /game/{verb} with no role "
                f"held, but the verb table marks it pre_role=False")
        if not row.pre_role:
            assert role_t is not None and step["t"] >= role_t


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_every_quiet_window_names_real_verbs(scenario_fn):
    data = _load(scenario_fn.__name__)
    for step in _kind(data, "expect_quiet"):
        for address in step["expect_quiet"]["addresses"]:
            direction, verb = _direction_and_verb(address)
            row_for(direction, verb)


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_no_device_ever_sends_a_retired_verb(scenario_fn):
    """Firmware checklist item 8: a v3 device never sends /game/join, so
    no recording may ask it to, as an expectation or an input."""
    data = _load(scenario_fn.__name__)
    retired = {f"/game/{verb}" for verb in RETIRED_UP_VERBS}
    assert not [s for s in _outs(data)
                if s["expect_out"]["address"] in retired]
    assert not _kind(data, "join")


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_the_device_field_is_the_v3_handshake_policy(scenario_fn):
    data = _load(scenario_fn.__name__)
    assert set(data["device"]) == {"handshake"}
    policy = data["device"]["handshake"]
    assert policy is None or set(policy) == {"node", "ack_after_ms"}


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_control_verbs_are_tcp_rows_and_only_leds_and_play_are_udp(scenario_fn):
    """Every down verb a recording carries routes by its row's transport
    (spec 2026-10-01 section 3.3)."""
    data = _load(scenario_fn.__name__)
    for step in _sends(data):
        if step["control_sends"].get("malformed"):
            continue
        _direction, verb = _direction_and_verb(step["control_sends"]["address"])
        expected = "udp-ok" if verb in ("leds", "play") else "tcp"
        assert row_for("down", verb).transport == expected


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_the_timeline_runs_forward_and_carries_no_dev_id(scenario_fn):
    data = _load(scenario_fn.__name__)
    times = [s["t"] for s in data["steps"]]
    assert times == sorted(times)
    assert data["profiles"] == ["rev1"]
    assert "ct1" not in json.dumps(data)


# --- 3: one test per scenario ----------------------------------------------

def test_v3_scenario_set():
    from contract_kit.scenarios import ALL_SCENARIOS
    names = {f.__name__ for f in ALL_SCENARIOS}
    assert {"handshake_validate_then_role", "handshake_over_cap_deny",
            "handshake_stale_round", "late_hello_gets_jam",
            "jam_solo_fallback", "room_node_handshake_binds",
            "join_retired_error"} <= names
    assert not names & {"explicit_join_role", "lobby_tap_join"}


def _role_blob(step):
    return step["control_sends"]["args"][0]


def test_boot_hello_heartbeat_hellos_every_5s_and_says_nothing_else():
    """Rule 1, the hello's instrument declaration, and v3's /room on first
    contact only."""
    data = _load("boot_hello_heartbeat")
    hellos = _outs(data, "/game/hello")
    assert [s["t"] for s in hellos] == [0, 5000, 10000]
    for step in hellos:
        assert step["expect_out"]["typespec"] == "ssss"
        assert len(step["expect_out"]["args"]) == 4
        assert step["expect_out"]["args"][0] == "$DEV"
    # A device that never accepts sends nothing but hello, asserted twice.
    assert _outs(data) == hellos
    quiet = _kind(data, "expect_quiet")[0]["expect_quiet"]
    assert set(quiet["addresses"]) == {"/game/handshake", "/game/tap",
                                       "/game/hold", "/game/swing"}
    assert quiet["for_ms"] == 12000
    assert data["device"] == {"handshake": None}
    # /room once, on first contact, not per heartbeat; the invite repeats.
    assert [s["t"] for s in _sends(data, "/$DEV/room")] == [0]
    invites = _sends(data, "/$DEV/handshake")
    assert [s["t"] for s in invites] == [0, 5000, 10000]
    assert all(s["control_sends"]["args"] == ["$ROUND"] for s in invites)
    assert _sends(data, "/$DEV/role") == []
    assert _frames(data) == []


def test_handshake_validate_then_role_reserves_then_grants_at_start():
    data = _load("handshake_validate_then_role")
    assert data["device"] == {"handshake": ACCEPT_POLICY}
    assert [s["t"] for s in _sends(data, "/$DEV/handshake")] == [0]
    accept = _outs(data, "/game/handshake")
    assert [(s["t"], s["expect_out"]["args"]) for s in accept] == [
        (ACCEPT_AFTER_MS, ["$DEV", "$ROUND", ""])]
    # The policy's own accept is not an input step: device.handshake is.
    assert _kind(data, "accept") == []
    validated = _sends(data, "/$DEV/validated")
    assert [(s["t"], s["control_sends"]["args"]) for s in validated] == [
        (ACCEPT_AFTER_MS, ["$ROUND", "player"])]
    # The validation raised the room's player count before start.
    counts = [(s["t"], _role_blob(s)["nodes"][0]["count"])
              for s in _sends(data, "/$DEV/room")]
    assert counts[:2] == [(0, 0), (ACCEPT_AFTER_MS, 1)]

    # The lobby: a white invite flash before the accept, a green ceremony
    # flash after it, and the chime with its key placeheld.
    white, green = _kind(data, "expect_frame")[:2]
    assert white["t"] < ACCEPT_AFTER_MS < green["t"]
    assert set(white["expect_frame"]["grb"]) == {255}
    assert green["expect_frame"]["grb"][:3] == [255, 0, 0]   # GRB green
    chime = _kind(data, "expect_play")[0]
    assert chime["expect_play"] == {"name": "chime", "params": "key=$KEY",
                                    "within_ms": 50}
    assert ACCEPT_AFTER_MS < chime["t"] < VALIDATE_START_T

    # Validation cancels the invite's queued second white flash: nothing
    # white follows /validated.
    assert not [s for s in _frames(data) if s["t"] >= ACCEPT_AFTER_MS
                and set(s["control_sends"]["args"][0]) == {255}]

    # Ready (lexicon): a dim green pulse holds until the role.
    ready = [s for s in _kind(data, "expect_frame")
             if s["t"] == READY_PULSE_CHECK_T]
    assert len(ready) == 1
    grb = ready[0]["expect_frame"]["grb"]
    assert grb == [grb[0], 0, 0] * 12
    assert 0 < grb[0] <= round(PULSE_PEAK * 255)

    # Validated is a reservation: the role only arrives at start.
    role = _sends(data, "/$DEV/role")
    assert [s["t"] for s in role] == [VALIDATE_START_T]
    assert _role_blob(role[0])["role"] == "player"
    assert _role_blob(role[0])["scored"] is True
    assert _kind(data, "expect_frame")[-1]["t"] == (
        VALIDATE_START_T + SIGNATURE_SETTLED_MS)


def test_handshake_over_cap_deny_denies_then_grants_jam():
    data = _load("handshake_over_cap_deny")
    rooms = _sends(data, "/$DEV/room")
    assert (RIVAL_ACCEPT_T, 1) in [(s["t"], _role_blob(s)["nodes"][0]["count"])
                                   for s in rooms]
    deny = _sends(data, "/$DEV/deny")
    assert [s["t"] for s in deny] == [ACCEPT_AFTER_MS]
    reason, hint = deny[0]["control_sends"]["args"]
    assert reason == "scored full" and "jam" in hint
    assert _sends(data, "/$DEV/validated") == []
    role = _sends(data, "/$DEV/role")
    assert [s["t"] for s in role] == [START_T]
    assert _role_blob(role[0])["role"] == "jammer"
    assert _role_blob(role[0])["class"] == "JAM"
    assert _role_blob(role[0])["scored"] is False


def test_handshake_stale_round_is_dropped_then_a_good_accept_validates():
    data = _load("handshake_stale_round")
    accepts = _kind(data, "accept")
    assert [(s["t"], s["accept"]) for s in accepts] == [
        (STALE_ACCEPT_T, {"node": "", "round_id": STALE_ROUND_ID}),
        (GOOD_ACCEPT_T, {"node": ""})]
    outs = _outs(data, "/game/handshake")
    assert [s["expect_out"]["args"] for s in outs] == [
        ["$DEV", STALE_ROUND_ID, ""], ["$DEV", "$ROUND", ""]]
    # Each input precedes its own expect_out at the same t.
    for accept, out in zip(accepts, outs):
        assert data["steps"].index(accept) < data["steps"].index(out)
    # Nothing at all answers the stale one.
    assert [s for s in _sends(data)
            if STALE_ACCEPT_T <= s["t"] < GOOD_ACCEPT_T] == []
    assert [s["t"] for s in _sends(data, "/$DEV/validated")] == [GOOD_ACCEPT_T]
    assert _sends(data, "/$DEV/deny") == []


def test_late_hello_gets_jam_at_once():
    data = _load("late_hello_gets_jam")
    assert [s["t"] for s in _outs(data, "/game/hello")] == [WALK_UP_T]
    role = _sends(data, "/$DEV/role")
    assert [s["t"] for s in role] == [WALK_UP_T]
    assert _role_blob(role[0])["class"] == "JAM"
    # The first-contact /room (already RUNNING) comes before the role.
    ordered = _sends(data)
    assert ordered[0]["control_sends"]["address"] == "/$DEV/room"
    assert _role_blob(ordered[0])["state"] == "RUNNING"
    assert _sends(data, "/$DEV/handshake") == []
    assert _frames(data)


def test_jam_solo_fallback_synthesizes_a_solo_role():
    data = _load("jam_solo_fallback")
    nodes = [n["id"] for n in _role_blob(_sends(data, "/$DEV/room")[0])["nodes"]]
    assert nodes == ["CONTRACT_PLAYER_NODE"]          # no jam node at all
    role = _sends(data, "/$DEV/role")
    assert [s["t"] for s in role] == [START_T]
    blob = _role_blob(role[0])
    assert blob["role"] == "solo:tuneshroom_rev1"
    assert blob["class"] == "JAM" and blob["scored"] is False


def test_room_node_handshake_binds_with_no_validated_and_no_role():
    data = _load("room_node_handshake_binds")
    # The lobby's white invite flash shows before the accept.
    first = _kind(data, "expect_frame")[0]
    assert first["t"] < ROOM_ACCEPT_T
    assert set(first["expect_frame"]["grb"]) == {255}
    accept = _kind(data, "accept")
    assert [(s["t"], s["accept"]) for s in accept] == [
        (ROOM_ACCEPT_T, {"node": ROOM_NODE_ID})]
    assert _sends(data, "/$DEV/validated") == []
    assert _sends(data, "/$DEV/role") == []
    assert _sends(data, "/$DEV/deny") == []
    fixture_frames = [s for s in _frames(data) if s["t"] >= ROOM_ACCEPT_T]
    assert fixture_frames and fixture_frames[0]["t"] == ROOM_ACCEPT_T
    # Room-bound frames are the fixture's width (180 for the TEST fixture),
    # not 36: no display expectation may follow the bind.
    assert all(s["t"] < ROOM_ACCEPT_T for s in _kind(data, "expect_frame"))
    assert len(fixture_frames[0]["control_sends"]["args"][0]) == 180


def test_join_retired_error_answers_and_changes_nothing():
    data = _load("join_retired_error")
    errors = _sends(data, "/$DEV/error")
    assert [(s["t"], s["control_sends"]["args"]) for s in errors] == [
        (LEGACY_JOIN_T, ["join",
                         "retired in contract v3: use /game/handshake"])]
    # Nothing else answers the join, and the device still gets jam at start.
    assert [s for s in _sends(data) if s["t"] == LEGACY_JOIN_T] == errors
    role = _sends(data, "/$DEV/role")
    assert [s["t"] for s in role] == [START_T]
    assert _role_blob(role[0])["role"] == "jammer"
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]


def test_timed_frames_show_at_their_stamp_then_hold():
    """Rule 3, clause by clause."""
    data = _load("timed_frames_hold_last")
    frames = _frames(data)

    # Clause 1: the look's frame is stamped at the CUE's presentation time,
    # not at the tick it was sent on.
    early = [s for s in frames
             if s["control_sends"]["at"] < s["t"] + HORIZON_MS]
    assert len(early) == 1
    look = early[0]
    assert look["control_sends"]["at"] == ROLE_SETTLED_T + 500 + HORIZON_MS

    # Clause 2, device side: the hand-authored pair, newer one first.
    checks = _kind(data, "expect_frame")
    assert [s["t"] for s in checks] == [ROLE_SETTLED_T, LOOK_SETTLED_T,
                                        AUTHORED_CHECK_T, HOLD_CHECK_T]
    pair = [s for s in frames if s["t"] == AUTHORED_PAIR_T]
    assert len(pair) == 2
    assert [s["control_sends"]["at"] for s in pair] == [AUTHORED_NEWER_AT,
                                                        AUTHORED_OLDER_AT]
    assert pair[0]["control_sends"]["args"][0] == AUTHORED_NEWER_GRB
    assert pair[1]["control_sends"]["args"][0] == AUTHORED_OLDER_GRB
    newest_check = checks[2]
    assert newest_check["t"] > AUTHORED_NEWER_AT > AUTHORED_OLDER_AT
    assert newest_check["expect_frame"]["grb"] == AUTHORED_NEWER_GRB
    # Nothing real intervenes: Control had gone quiet before the settled
    # check, let alone the pair.
    real_frames = [s for s in frames if s["t"] != AUTHORED_PAIR_T]
    assert real_frames[-1]["t"] + HORIZON_MS < LOOK_SETTLED_T

    # Clause 2, Control side: the two cues due together were collapsed into
    # that single early-stamped frame, carrying the NEWER cue's value.
    onsets = [s["t"] for s in _outs(data, "/game/tap")]
    assert onsets == [ROLE_SETTLED_T, ROLE_SETTLED_T]
    assert len([s for s in frames
                if s["control_sends"]["at"] == look["control_sends"]["at"]]) == 1
    assert real_frames[-1]["control_sends"]["args"][0] != _one_tap_control(), (
        "the second cue due at that moment made no difference, so this "
        "scenario no longer shows that the newest of several wins")

    # Clause 3: the last frame still shows almost six seconds later.
    assert checks[-1]["expect_frame"]["grb"] == AUTHORED_NEWER_GRB
    assert checks[-1]["t"] - AUTHORED_NEWER_AT > 5000


def _one_tap_control():
    """The final frame of the same script with only ONE tap at the shared
    onset. Not a recording: a live control run, so the scenario's claim
    about the second cue is checked against the real engine."""
    rec = Recorder(name="one_tap_control", summary="control run",
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.tap(ROLE_SETTLED_T, duration_ms=80.0)
    rec.advance_to(LOOK_SETTLED_T + 2000)
    return _frames(rec.finish())[-1]["control_sends"]["args"][0]


def test_gestures_after_role_shows_all_three_shapes_and_the_pre_role_rule():
    data = _load("gestures_after_role")
    role_t = _first_t(data, "/$DEV/role")
    assert role_t == START_T

    shapes = {s["expect_out"]["address"]: s for s in _outs(data)
              if s["expect_out"]["address"].endswith(("tap", "hold", "swing"))}
    assert shapes["/game/tap"]["expect_out"]["typespec"] == "sffi"
    assert shapes["/game/hold"]["expect_out"]["typespec"] == "sfi"
    assert shapes["/game/swing"]["expect_out"]["typespec"] == "sfi"
    gestures = list(shapes.values())
    assert len(gestures) == 3
    for step in gestures:
        assert step["t"] > role_t
        assert step["expect_out"]["stamp_t"] == step["t"]
        assert step["expect_out"]["args"][-1] == 1          # count
        assert step["expect_out"]["args"][0] == "$DEV"
    assert shapes["/game/hold"]["expect_out"]["args"][1] == 0.65
    assert shapes["/game/swing"]["expect_out"]["args"][1] == -2.1

    # No gesture, tap included, goes out before the role.
    quiet = _kind(data, "expect_quiet")[0]
    assert quiet["t"] == 0
    assert set(quiet["expect_quiet"]["addresses"]) == {
        "/game/tap", "/game/hold", "/game/swing"}
    assert quiet["t"] + quiet["expect_quiet"]["for_ms"] == role_t
    assert _sends(data, "/$DEV/error") == []


def test_deny_leaves_the_device_hellod_with_its_heartbeat_running():
    data = _load("deny_stays_hellod")
    assert data["device"]["handshake"]["node"] == NO_SUCH_NODE
    deny = _sends(data, "/$DEV/deny")
    assert [s["t"] for s in deny] == [ACCEPT_AFTER_MS]
    reason, hint = deny[0]["control_sends"]["args"]
    assert reason == "no such node" and hint
    assert _outs(data, "/game/handshake")[0]["expect_out"]["args"] == [
        "$DEV", "$ROUND", NO_SUCH_NODE]
    # Denied means denied: no validation, no role.
    assert _sends(data, "/$DEV/validated") == []
    assert _sends(data, "/$DEV/role") == []
    # Failure (lexicon): red x2 from the deny, then the white invite pulse
    # comes back, since the lobby is WAITING and the device is still invited.
    red = [0, 255, 0] * 12                                   # GRB red
    reds = [s for s in _frames(data) if s["control_sends"]["args"][0] == red]
    assert len(reds) == 2
    assert ACCEPT_AFTER_MS <= reds[0]["t"] <= ACCEPT_AFTER_MS + 23
    flash, pulse = _kind(data, "expect_frame")
    assert flash["t"] == DENY_FLASH_CHECK_T and flash["expect_frame"]["grb"] == red
    grb = pulse["expect_frame"]["grb"]
    assert pulse["t"] == DENY_PULSE_CHECK_T
    assert grb == [grb[0]] * 36 and 0 < grb[0] < 255
    # The heartbeat carries on, and the policy accepts only once.
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000, 10000]
    assert len(_outs(data, "/game/handshake")) == 1


def test_release_follows_the_fade_on_the_wire_but_lands_before_it_shows():
    """Rule 4, decision D5, and the spec section 5.5 ordering question."""
    data = _load("release_keeps_display")
    release = _sends(data, "/$DEV/release")
    assert len(release) == 1
    frames = _frames(data)
    last_frame = frames[-1]

    ordered = _sends(data)
    assert ordered[ordered.index(last_frame) + 1] is release[0]
    assert release[0]["t"] == last_frame["t"]
    assert release[0]["control_sends"]["at"] is None
    assert last_frame["control_sends"]["at"] == last_frame["t"] + HORIZON_MS
    assert last_frame["control_sends"]["at"] > release[0]["t"]

    assert any(last_frame["control_sends"]["args"][0])
    held = _kind(data, "expect_frame")[-1]
    assert held["t"] - last_frame["t"] > 5000
    assert held["expect_frame"]["grb"] == last_frame["control_sends"]["args"][0]

    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]
    idle = _sends(data, "/$DEV/room")[-1]["control_sends"]["args"][0]
    assert idle["state"] == "IDLE" and idle["bit"] is None


def test_play_known_and_unknown_both_reach_the_device():
    data = _load("play_known_and_unknown")
    plays = [(s["t"], s["control_sends"]["args"][0])
             for s in _sends(data, "/$DEV/play")]
    assert plays == [(START_T + 200, KNOWN_SAMPLE),
                     (START_T + 700, UNKNOWN_SAMPLE)]
    assert UNKNOWN_SAMPLE not in _sends(data, "/$DEV/role")[0][
        "control_sends"]["args"][0]["samples"]
    expect = _kind(data, "expect_play")[0]
    assert expect["expect_play"]["name"] == KNOWN_SAMPLE
    assert len(_kind(data, "expect_play")) == 1
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]


def test_link_loss_drops_the_device_and_it_validates_again():
    """Rule 7: no session resume."""
    data = _load("link_loss_rejoin")
    links = [(s["t"], s["link"]) for s in _kind(data, "link")]
    assert links == [(0, "up"), (2000, "down"), (17000, "up")]
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 17000]
    # Invited, accepted and validated once per link-up, from scratch.
    assert [s["t"] for s in _sends(data, "/$DEV/handshake")] == [0, 17000]
    assert [s["t"] for s in _outs(data, "/game/handshake")] == [
        ACCEPT_AFTER_MS, 17000 + ACCEPT_AFTER_MS]
    assert [s["t"] for s in _sends(data, "/$DEV/validated")] == [
        ACCEPT_AFTER_MS, 17000 + ACCEPT_AFTER_MS]
    # The round never left SETUP: no role ever.
    assert _sends(data, "/$DEV/role") == []
    # The reap left the room's count back at 0 before the rejoin.
    rejoin_room = [s for s in _sends(data, "/$DEV/room") if s["t"] == 17000]
    assert _role_blob(rejoin_room[0])["nodes"][0]["count"] == 0


def test_an_error_changes_nothing():
    data = _load("error_no_state_change")
    errors = _sends(data, "/$DEV/error")
    assert len(errors) == 1
    assert errors[0]["control_sends"]["args"] == ["hold", JAMMER_REFUSAL]
    error_t = errors[0]["t"]
    role = _sends(data, "/$DEV/role")
    assert len(role) == 1 and role[0]["t"] < error_t
    assert _role_blob(role[0])["role"] == "jammer"
    # Nothing but frames follows the error: no room, no role, no release.
    after = [s["control_sends"]["address"] for s in _sends(data)
             if s["t"] > error_t]
    assert set(after) <= {"/$DEV/leds", "/$DEV/play"}
    # The device carries on: heartbeat, and a later tap still plays.
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]
    play = _kind(data, "expect_play")[0]
    assert play["t"] > error_t and play["expect_play"]["name"] == KNOWN_SAMPLE


def test_malformed_input_is_dropped_and_the_device_carries_on():
    """Rule 6's message half."""
    data = _load("malformed_dropped")
    bad = [s for s in _sends(data) if s["control_sends"].get("malformed")]
    assert len(bad) == 3
    assert [s["control_sends"]["address"] for s in bad] == [
        "/$DEV/bogus", "/$DEV/role", "/$DEV/leds"]
    reasons = [_contract_violation(s["control_sends"]["address"],
                                   s["control_sends"]["typespec"],
                                   s["control_sends"]["args"]) for s in bad]
    assert all(reasons) and len(set(reasons)) == 3

    bad_t = bad[0]["t"]
    assert bad_t > _first_t(data, "/$DEV/role")
    play = _kind(data, "expect_play")[0]
    assert play["t"] > bad_t and play["expect_play"]["name"] == KNOWN_SAMPLE
    frame = _kind(data, "expect_frame")[0]
    assert frame["t"] > bad_t
    later_frames = [s for s in _frames(data)
                    if s["t"] > bad_t and not s["control_sends"].get("malformed")]
    assert later_frames
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]


def test_link_loss_keeps_display_holds_the_frame_and_starts_over():
    """Rule 8's two device-side halves. "Role ends" leaves no message of
    its own; what this checks is its wire-observable consequence: back on
    the link the device hellos from scratch and, the round being RUNNING,
    is granted a fresh role as a walk-up."""
    data = _load("link_loss_keeps_display")
    links = [(s["t"], s["link"]) for s in _kind(data, "link")]
    assert links == [(0, "up"), (ROLE_SETTLED_T, "down"), (LINK_BACK_T, "up")]
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, LINK_BACK_T]

    roles = _sends(data, "/$DEV/role")
    assert [s["t"] for s in roles] == [START_T, LINK_BACK_T]
    assert _role_blob(roles[0])["role"] == "player"
    assert _role_blob(roles[1])["role"] == "jammer"
    # Control reaped the device during the outage (unheard release).
    release = _sends(data, "/$DEV/release")
    assert len(release) == 1 and ROLE_SETTLED_T < release[0]["t"] < LINK_BACK_T

    checks = _kind(data, "expect_frame")
    assert [s["t"] for s in checks] == [ROLE_SETTLED_T, 9000,
                                        LINK_BACK_T + SIGNATURE_SETTLED_MS]
    assert checks[1]["expect_frame"]["grb"] == checks[0]["expect_frame"]["grb"]
    assert [s for s in _frames(data) if s["t"] >= LINK_BACK_T]

    quiet = _kind(data, "expect_quiet")[0]
    assert quiet["t"] == ROLE_SETTLED_T
    assert set(quiet["expect_quiet"]["addresses"]) == {
        "/game/hello", "/game/tap", "/game/hold", "/game/swing"}
    assert quiet["t"] + quiet["expect_quiet"]["for_ms"] == LINK_BACK_T


def test_link_blip_keeps_role_sends_nothing_new_and_the_role_still_plays():
    """Checklist item 7: a reconnect inside the 15 s stale timeout while
    RUNNING. Control was never told anything happened, so it sends no
    fresh /role, no /handshake and no /release; the device must still hold
    the role it had, which the tap after the blip proves."""
    data = _load("link_blip_keeps_role")
    links = [(s["t"], s["link"]) for s in _kind(data, "link")]
    assert links == [(0, "up"), (ROLE_SETTLED_T, "down"), (BLIP_BACK_T, "up")]
    assert BLIP_BACK_T - ACCEPT_AFTER_MS < 15000
    # The heartbeat halts with the link and resumes on its own grid.
    assert [s["t"] for s in _outs(data, "/game/hello")] == [
        0, BLIP_BACK_T, BLIP_BACK_T + 5000]
    # One role, at start; nothing re-sent after the blip.
    assert [s["t"] for s in _sends(data, "/$DEV/role")] == [START_T]
    assert _role_blob(_sends(data, "/$DEV/role")[0])["role"] == "player"
    for verb in ("/$DEV/handshake", "/$DEV/release", "/$DEV/room"):
        assert [s for s in _sends(data, verb) if s["t"] >= ROLE_SETTLED_T] \
            == [], verb
    # A gesture after the blip goes out and plays: the role was kept.
    tap = _outs(data, "/game/tap")
    assert [s["t"] for s in tap] == [BLIP_TAP_T]
    play = _kind(data, "expect_play")
    assert [(s["t"], s["expect_play"]["name"]) for s in play] == [
        (BLIP_TAP_T, KNOWN_SAMPLE)]
    quiet = _kind(data, "expect_quiet")[0]
    assert quiet["t"] == ROLE_SETTLED_T
    assert quiet["t"] + quiet["expect_quiet"]["for_ms"] == BLIP_BACK_T


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_a_policy_accept_is_always_checked_as_an_expect_out(scenario_fn):
    """A device that never sends its policy's /game/handshake must fail
    every scenario whose device.handshake is set, not just the ones about
    the handshake."""
    data = _load(scenario_fn.__name__)
    policy = data["device"]["handshake"]
    if policy is None:
        return
    first_invite = _first_t(data, "/$DEV/handshake")
    outs = [s["t"] for s in _outs(data, "/game/handshake")]
    assert first_invite + policy["ack_after_ms"] in outs
