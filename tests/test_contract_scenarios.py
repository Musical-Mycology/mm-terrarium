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
   scenario's own flagged steps are required to FAIL that same check.
3. One test per scenario, asserting the recording still demonstrates what
   the spec's scenario table says it covers. A recording that is
   byte-stable but no longer contains its own point is a failed scenario,
   and the regression test alone cannot notice.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("luxaeterna")

from contract_kit.contract_bit import (CONTRACT_PLAYER_NODE, KNOWN_SAMPLE,
                                       UNKNOWN_SAMPLE)
from contract_kit.recorder import CUE_HORIZON_S, Recorder
from contract_kit.scenarios import (ALL_SCENARIOS, AUTHORED_NEWER_AT,
                                    AUTHORED_NEWER_GRB, AUTHORED_OLDER_AT,
                                    AUTHORED_OLDER_GRB, AUTHORED_PAIR_T,
                                    NO_SUCH_NODE, SIGNATURE_SETTLED_MS)
from devicelink.contract import row_for, typespec_allowed

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
def test_the_timeline_runs_forward_and_carries_no_dev_id(scenario_fn):
    data = _load(scenario_fn.__name__)
    times = [s["t"] for s in data["steps"]]
    assert times == sorted(times)
    assert data["profiles"] == ["rev1"]
    assert "ct1" not in json.dumps(data)


# --- 3: one test per scenario ----------------------------------------------

def test_boot_hello_heartbeat_hellos_every_5s_and_says_nothing_else():
    """Rule 1, and the hello's instrument declaration."""
    data = _load("boot_hello_heartbeat")
    hellos = _outs(data, "/game/hello")
    assert [s["t"] for s in hellos] == [0, 5000, 10000]
    for step in hellos:
        # The four-argument form; args[3] is where a device declares its
        # carried instrument. The name itself is the `*` placeholder,
        # because which instrument a device carries is its own business.
        assert step["expect_out"]["typespec"] == "ssss"
        assert len(step["expect_out"]["args"]) == 4
        assert step["expect_out"]["args"][0] == "$DEV"
    # Nothing but hello goes out before a join, asserted twice: the device
    # sends nothing else at all, and the quiet window names the four verbs
    # a device replaying this must hold back.
    assert _outs(data) == hellos
    quiet = _kind(data, "expect_quiet")[0]["expect_quiet"]
    assert set(quiet["addresses"]) == {"/game/join", "/game/tap",
                                       "/game/hold", "/game/swing"}
    assert quiet["for_ms"] == 12000
    assert _sends(data, "/$DEV/role") == []
    assert _frames(data) == []


def test_explicit_join_role_is_granted_and_rendered():
    data = _load("explicit_join_role")
    join = _outs(data, "/game/join")[0]
    assert join["t"] == 0
    assert join["expect_out"]["args"] == ["$DEV", CONTRACT_PLAYER_NODE]
    role = _sends(data, "/$DEV/role")
    assert len(role) == 1 and role[0]["t"] == 0
    assert role[0]["control_sends"]["args"][0]["role"] == "player"
    # Control answers the join with a fresh room snapshot whose node count
    # has gone up, and then renders the granted role.
    counts = [s["control_sends"]["args"][0]["nodes"][0]["count"]
              for s in _sends(data, "/$DEV/room")]
    assert counts[:2] == [0, 1]
    assert _frames(data), "a granted role must reach the pixels"
    assert _kind(data, "expect_frame")[0]["t"] == SIGNATURE_SETTLED_MS


def test_lobby_tap_join_shows_invites_then_two_taps_then_role_and_chime():
    data = _load("lobby_tap_join")
    role_t = _first_t(data, "/$DEV/role")
    assert role_t is not None

    # Invite frames show BEFORE the role, and the first one is white.
    invites = [s for s in _frames(data) if s["t"] < role_t]
    assert len(invites) >= 2
    first_invite = _kind(data, "expect_frame")[0]
    assert first_invite["t"] < role_t
    assert set(first_invite["expect_frame"]["grb"]) == {255}

    # Two count-1 taps inside the 1.5 s double-tap window are what join it.
    taps = _outs(data, "/game/tap")
    assert [s["t"] for s in taps] == [1000, 1600]
    for step in taps:
        assert step["expect_out"]["typespec"] == "sffi"
        assert step["expect_out"]["args"][3] == 1        # count is 1 on Rev 1
    assert taps[1]["t"] - taps[0]["t"] < 1500
    assert role_t == taps[1]["t"]
    # No /error: with a Room loaded the lobby receives these pre-role taps.
    assert _sends(data, "/$DEV/error") == []

    # The join ceremony's chime, with its key parameter placeheld.
    chime = _kind(data, "expect_play")[0]
    assert chime["expect_play"]["name"] == "chime"
    assert chime["expect_play"]["params"] == "key=$KEY"
    assert chime["t"] > role_t


def test_timed_frames_show_at_their_stamp_then_hold():
    """Rule 3, clause by clause."""
    data = _load("timed_frames_hold_last")
    frames = _frames(data)

    # Clause 1: the look's frame is stamped at the CUE's presentation time,
    # not at the tick it was sent on, so it is the one frame in the file
    # whose `at` is earlier than its own send time plus the cue horizon.
    early = [s for s in frames
             if s["control_sends"]["at"] < s["t"] + HORIZON_MS]
    assert len(early) == 1
    look = early[0]
    # Onset + ContractBit's half-second lead + the cue horizon.
    assert look["control_sends"]["at"] == SIGNATURE_SETTLED_MS + 500 + HORIZON_MS

    # Clause 2, device side: the hand-authored pair. Two frames on ONE send
    # time with two presentation times, both already past by the check that
    # follows, and the NEWER one sent first, so a device that shows whatever
    # arrived last records the wrong answer.
    checks = _kind(data, "expect_frame")
    assert [s["t"] for s in checks] == [SIGNATURE_SETTLED_MS, 5500, 6500,
                                        12000]
    pair = [s for s in frames if s["t"] == AUTHORED_PAIR_T]
    assert len(pair) == 2
    assert [s["control_sends"]["at"] for s in pair] == [AUTHORED_NEWER_AT,
                                                        AUTHORED_OLDER_AT]
    assert pair[0]["control_sends"]["at"] > pair[1]["control_sends"]["at"]
    assert pair[0]["control_sends"]["args"][0] == AUTHORED_NEWER_GRB
    assert pair[1]["control_sends"]["args"][0] == AUTHORED_OLDER_GRB
    newest_check = checks[2]
    assert newest_check["t"] > AUTHORED_NEWER_AT > AUTHORED_OLDER_AT
    assert newest_check["expect_frame"]["grb"] == AUTHORED_NEWER_GRB
    # Nothing real intervenes: Control had gone quiet well before the pair.
    real_frames = [s for s in frames if s["t"] != AUTHORED_PAIR_T]
    assert real_frames[-1]["t"] < AUTHORED_PAIR_T

    # Clause 2, Control side: the two cues that fell due together (both taps
    # share one onset) were collapsed into that single early-stamped frame,
    # and it carries the NEWER cue's value. The comparison run taps once at
    # the same moment, so the second cue is the only difference between them.
    onsets = [s["t"] for s in _outs(data, "/game/tap")]
    assert onsets == [SIGNATURE_SETTLED_MS, SIGNATURE_SETTLED_MS]
    assert len([s for s in frames
                if s["control_sends"]["at"] == look["control_sends"]["at"]]) == 1
    assert real_frames[-1]["control_sends"]["args"][0] != _one_tap_control(), (
        "the second cue due at that moment made no difference, so this "
        "scenario no longer shows that the newest of several wins")

    # Clause 3: the frames stop, and the last one is still what shows almost
    # six seconds later.
    assert checks[-1]["expect_frame"]["grb"] == AUTHORED_NEWER_GRB
    assert checks[-1]["t"] - AUTHORED_NEWER_AT > 5000


def _one_tap_control():
    """The final frame of the same script with only ONE tap at the shared
    onset. Not a recording: a live control run, so the scenario's claim
    about the second cue is checked against the real engine."""
    rec = Recorder(name="one_tap_control", summary="control run",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.advance_to(SIGNATURE_SETTLED_MS)
    rec.tap(SIGNATURE_SETTLED_MS, duration_ms=80.0)
    rec.advance_to(10000)
    return _frames(rec.finish())[-1]["control_sends"]["args"][0]


def test_gestures_after_role_shows_all_three_shapes_and_the_pre_role_rule():
    data = _load("gestures_after_role")
    role_t = _first_t(data, "/$DEV/role")
    assert role_t == 1000

    shapes = {s["expect_out"]["address"]: s for s in _outs(data)
              if s["expect_out"]["address"] != "/game/hello"}
    assert shapes["/game/tap"]["expect_out"]["typespec"] == "sffi"
    assert shapes["/game/hold"]["expect_out"]["typespec"] == "sfi"
    assert shapes["/game/swing"]["expect_out"]["typespec"] == "sfi"
    # Rule 5: every gesture is stamped at its own onset, and carries count 1.
    gestures = [s for s in _outs(data)
                if s["expect_out"]["address"] in ("/game/tap", "/game/hold",
                                                  "/game/swing")]
    assert len(gestures) == 4
    for step in gestures:
        assert step["expect_out"]["stamp_t"] == step["t"]
        assert step["expect_out"]["args"][-1] == 1          # count
        assert step["expect_out"]["args"][0] == "$DEV"
    assert shapes["/game/hold"]["expect_out"]["args"][1] == 0.65
    assert shapes["/game/swing"]["expect_out"]["args"][1] == -2.1

    # Tap is the one gesture allowed before a role, and it goes out there.
    taps = [s["t"] for s in _outs(data, "/game/tap")]
    assert taps == [500, 1500]
    # Hold and swing wait, under a quiet window that covers the pre-role gap.
    quiet = _kind(data, "expect_quiet")[0]
    assert quiet["t"] == 500
    assert set(quiet["expect_quiet"]["addresses"]) == {"/game/hold",
                                                       "/game/swing"}
    assert quiet["t"] + quiet["expect_quiet"]["for_ms"] <= role_t
    assert min(_outs(data, "/game/hold")[0]["t"],
               _outs(data, "/game/swing")[0]["t"]) > role_t

    # The pre-role tap is answered with an /error, not acted on: there is no
    # Room here, so no lobby receives it.
    errors = _sends(data, "/$DEV/error")
    assert [s["t"] for s in errors] == [500]
    assert errors[0]["control_sends"]["args"] == ["tap",
                                                  "device not registered"]


def test_deny_leaves_the_device_hellod_with_its_heartbeat_running():
    data = _load("deny_stays_hellod")
    deny = _sends(data, "/$DEV/deny")
    assert len(deny) == 1 and deny[0]["t"] == 0
    assert deny[0]["control_sends"]["args"] == ["no such node", ""]
    assert _outs(data, "/game/join")[0]["expect_out"]["args"][1] == NO_SUCH_NODE
    # Denied means denied: no role, no pixels.
    assert _sends(data, "/$DEV/role") == []
    assert _frames(data) == []
    # And the heartbeat carries on across the deny.
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000, 10000]


def test_release_follows_the_fade_on_the_wire_but_lands_before_it_shows():
    """Rule 4, decision D5, and the spec section 5.5 ordering question."""
    data = _load("release_keeps_display")
    release = _sends(data, "/$DEV/release")
    assert len(release) == 1
    frames = _frames(data)
    last_frame = frames[-1]

    # By send time: the release is the very next message after the fade's
    # last frame, in the same millisecond.
    ordered = _sends(data)
    assert ordered[ordered.index(last_frame) + 1] is release[0]
    assert release[0]["t"] == last_frame["t"]
    # By presentation time: the release declares none, so a device acts on
    # it at once, one cue horizon BEFORE that last frame is due to show.
    assert release[0]["control_sends"]["at"] is None
    assert last_frame["control_sends"]["at"] == last_frame["t"] + HORIZON_MS
    assert last_frame["control_sends"]["at"] > release[0]["t"]

    # The display is not cleared: the last frame is not black, and it is
    # still what shows five seconds later.
    assert any(last_frame["control_sends"]["args"][0])
    held = _kind(data, "expect_frame")[-1]
    assert held["t"] - last_frame["t"] > 5000
    assert held["expect_frame"]["grb"] == last_frame["control_sends"]["args"][0]

    # The heartbeat continues after the release, and the room says IDLE.
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]
    idle = _sends(data, "/$DEV/room")[-1]["control_sends"]["args"][0]
    assert idle["state"] == "IDLE" and idle["bit"] is None


def test_play_known_and_unknown_both_reach_the_device():
    data = _load("play_known_and_unknown")
    plays = [(s["t"], s["control_sends"]["args"][0])
             for s in _sends(data, "/$DEV/play")]
    assert plays == [(200, KNOWN_SAMPLE), (700, UNKNOWN_SAMPLE)]
    assert UNKNOWN_SAMPLE not in _sends(data, "/$DEV/role")[0][
        "control_sends"]["args"][0]["samples"]
    expect = _kind(data, "expect_play")[0]
    assert expect["expect_play"]["name"] == KNOWN_SAMPLE
    # The unknown name carries no expectation of its own: the device is
    # free to ignore it, and the hello afterwards is the proof it carried on.
    assert len(_kind(data, "expect_play")) == 1
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]


def test_link_loss_drops_the_device_and_a_fresh_join_is_granted():
    """Rule 7: no session resume."""
    data = _load("link_loss_rejoin")
    links = [(s["t"], s["link"]) for s in _kind(data, "link")]
    assert links == [(0, "up"), (2000, "down"), (17000, "up")]

    # The heartbeat stops with the link, and 15 s after the last hello
    # Control reaps the device.
    hellos = [s["t"] for s in _outs(data, "/game/hello")]
    assert hellos == [0, 17000]
    release = _sends(data, "/$DEV/release")
    assert len(release) == 1
    assert 15000 <= release[0]["t"] < 17000

    # The device joins again from scratch, and is granted again.
    roles = [s["t"] for s in _sends(data, "/$DEV/role")]
    assert roles == [0, 17000]
    rejoin = _outs(data, "/game/join")[-1]
    assert rejoin["t"] == 17000
    assert rejoin["expect_out"]["args"] == ["$DEV", CONTRACT_PLAYER_NODE]


def test_an_error_changes_nothing():
    data = _load("error_no_state_change")
    errors = _sends(data, "/$DEV/error")
    assert len(errors) == 1 and errors[0]["t"] == 200
    assert errors[0]["control_sends"]["args"] == ["tap",
                                                  "device not registered"]

    # The room snapshot either side of the error is identical.
    rooms = _sends(data, "/$DEV/room")
    before = [s for s in rooms if s["t"] < errors[0]["t"]][-1]
    after = [s for s in rooms if s["t"] > errors[0]["t"]][0]
    assert before["control_sends"]["args"] == after["control_sends"]["args"]
    # The device held no role before the error and is still free to take one.
    role = _sends(data, "/$DEV/role")
    assert len(role) == 1 and role[0]["t"] > errors[0]["t"]
    assert _outs(data, "/game/join")[0]["t"] == role[0]["t"]
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]


def test_malformed_input_is_dropped_and_the_device_carries_on():
    """Rule 6's message half."""
    data = _load("malformed_dropped")
    bad = [s for s in _sends(data) if s["control_sends"].get("malformed")]
    assert len(bad) == 3
    assert [s["control_sends"]["address"] for s in bad] == [
        "/$DEV/bogus", "/$DEV/role", "/$DEV/leds"]
    # Each is broken in its own way, and none of them is a legal message
    # (test_every_recorded_message_is_a_contract_message proves the second
    # half of that for every scenario).
    reasons = [_contract_violation(s["control_sends"]["address"],
                                   s["control_sends"]["typespec"],
                                   s["control_sends"]["args"]) for s in bad]
    assert all(reasons) and len(set(reasons)) == 3

    bad_t = bad[0]["t"]
    # Nothing here asserts that Control's own state survived them: these
    # three messages are authored, never sent, and never reach Control, so
    # such an assertion could not fail. What the scenario really pins is the
    # DEVICE side, below: after dropping all three it still round-trips a
    # valid gesture and still shows a valid frame.
    play = _kind(data, "expect_play")[0]
    assert play["t"] > bad_t and play["expect_play"]["name"] == KNOWN_SAMPLE
    frame = _kind(data, "expect_frame")[0]
    assert frame["t"] > bad_t
    later_frames = [s for s in _frames(data)
                    if s["t"] > bad_t and not s["control_sends"].get("malformed")]
    assert later_frames
    assert [s["t"] for s in _outs(data, "/game/hello")] == [0, 5000]
