"""contract_kit/recorder.py: the O2LiteTransport(FakeO2Lite) rig that
drives a ContractBit and captures Control's real behavior into EXPORT
FORMAT v1 scenario dicts (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md sections 4.2-4.3, 5.4).

Every assertion here is against what the REAL Control engine sent back
through the wrapped transport, not against a literal some recorder helper
just appended to its own step list.
"""
import pytest

pytest.importorskip("luxaeterna")

from contract_kit.contract_bit import JAMMER_REFUSAL
from contract_kit.recorder import CUE_HORIZON_S, ROOM_NODE_ID, Recorder

# The device accepts its first invite 300 ms after it arrives.
ACCEPT = {"node": "", "ack_after_ms": 300}


def _playing(**kwargs):
    """A recorder whose device links up at 0, accepts its invite at 300 and
    holds the scored player role from the operator's start at START."""
    rec = Recorder(name="t", summary="s", handshake=ACCEPT, **kwargs)
    rec.link_up(0)
    rec.start(START)
    return rec


START = 400


def _sends(data, address):
    """Every recorded control_sends step for `address`, in recorded order."""
    return [s for s in data["steps"]
            if s.get("control_sends", {}).get("address") == address]


def test_link_up_hellos_and_the_heartbeat_repeats_every_5s():
    """Control answers the first hello with /$DEV/room and every SETUP
    hello of an un-validated device with the invite cycle's
    /$DEV/handshake, so the two invites are proof the scripted heartbeat
    really reached the engine, not just that expect_hello appended two
    steps. /room goes out on first contact only (contract v3)."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    data = rec.finish()

    assert [s["t"] for s in _sends(data, "/$DEV/room")] == [0]
    assert [s["t"] for s in _sends(data, "/$DEV/handshake")] == [0, 5000]

    hellos = [s for s in data["steps"]
              if s.get("expect_out", {}).get("address") == "/game/hello"]
    assert [s["t"] for s in hellos] == [0, 5000]
    for s in hellos:
        assert s["expect_out"]["args"] == ["$DEV", "*", "*", "*"]
        assert s["expect_out"]["typespec"] == "ssss"


def test_finish_shape_matches_export_format_v1():
    rec = Recorder(name="shape_check", summary="one line", handshake=None)
    rec.link_up(0)
    data = rec.finish()
    assert data["name"] == "shape_check"
    assert data["summary"] == "one line"
    assert data["profiles"] == ["rev1"]
    assert data["device"] == {"handshake": None}
    # No _provenance: the spec puts it only in the export's contract.json,
    # and a commit hash in every recording would dirty all of them on every
    # re-record.
    assert set(data) == {"name", "summary", "profiles", "device", "steps"}
    ts = [s["t"] for s in data["steps"]]
    assert ts == sorted(ts)


def test_the_handshake_policy_accepts_the_first_invite_and_is_validated():
    """device.handshake: the device answers its first /$DEV/handshake
    ack_after_ms later, echoing that invite's round id (recorded as
    $ROUND), and the real engine answers /$DEV/validated. No role until
    start."""
    rec = Recorder(name="t", summary="s", handshake=ACCEPT)
    rec.link_up(0)
    rec.advance_to(300)
    rec.expect_handshake_out(300)
    data = rec.finish()

    assert data["device"] == {"handshake": ACCEPT}
    invites = _sends(data, "/$DEV/handshake")
    assert [(s["t"], s["control_sends"]["args"]) for s in invites] == [
        (0, ["$ROUND"])]
    validated = _sends(data, "/$DEV/validated")
    assert [(s["t"], s["control_sends"]["args"]) for s in validated] == [
        (300, ["$ROUND", "player"])]
    outs = [s for s in data["steps"]
            if s.get("expect_out", {}).get("address") == "/game/handshake"]
    assert [s["expect_out"]["args"] for s in outs] == [["$DEV", "$ROUND", ""]]
    assert not [s for s in data["steps"] if "accept" in s]
    assert not _sends(data, "/$DEV/role")
    # The real round id never reaches the output.
    assert rec.control_round_id() not in str(data)


def test_start_grants_the_validated_device_its_scored_role():
    rec = _playing()
    data = rec.finish()
    roles = _sends(data, "/$DEV/role")
    assert [s["t"] for s in roles] == [START]
    assert roles[0]["control_sends"]["typespec"] == "b"
    assert roles[0]["control_sends"]["args"][0]["role"] == "player"
    assert rec._fake.channels  # the fake recorded per-message transports


def test_start_grants_a_device_that_never_accepted_the_jam_role():
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.start(START)
    roles = _sends(rec.finish(), "/$DEV/role")
    assert roles[0]["control_sends"]["args"][0]["role"] == "jammer"


def test_a_jammers_hold_is_refused_with_an_error():
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.start(START)
    rec.hold(START + 100, held_s=0.65)
    errors = _sends(rec.finish(), "/$DEV/error")
    assert [s["control_sends"]["args"] for s in errors] == [
        ["hold", JAMMER_REFUSAL]]


def test_solo_contract_bit_falls_back_to_a_solo_role():
    rec = Recorder(name="t", summary="s", handshake=None,
                   bit="SoloContractBit")
    rec.link_up(0)
    rec.start(START)
    roles = _sends(rec.finish(), "/$DEV/role")
    assert roles[0]["control_sends"]["args"][0]["role"] == \
        "solo:tuneshroom_rev1"


def test_accept_records_an_input_step_before_its_own_expect_out():
    """An explicit accept is a decision the person makes: a runner has to
    be told when to make it, so it is an `accept` INPUT step, and the
    device's own expect_out follows it at the same t."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.advance_to(400)
    assert not _sends(rec.finish(), "/$DEV/validated")
    rec.accept(600)
    data = rec.finish()

    assert [s["t"] for s in _sends(data, "/$DEV/validated")] == [600]
    assert data["device"] == {"handshake": None}
    inputs = [s for s in data["steps"] if "accept" in s]
    assert inputs == [{"t": 600, "accept": {"node": ""}}]
    outs = [s for s in data["steps"]
            if s.get("expect_out", {}).get("address") == "/game/handshake"]
    assert data["steps"].index(inputs[0]) < data["steps"].index(outs[0])


def test_a_stale_round_id_is_recorded_literally_and_draws_nothing():
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.accept(300, round_id="stale")
    rec.advance_to(800)
    data = rec.finish()
    assert {"t": 300, "accept": {"node": "", "round_id": "stale"}} \
        in data["steps"]
    outs = [s["expect_out"]["args"] for s in data["steps"]
            if s.get("expect_out", {}).get("address") == "/game/handshake"]
    assert outs == [["$DEV", "stale", ""]]
    assert [s for s in data["steps"]
            if "control_sends" in s and s["t"] >= 300] == []


def test_accept_raises_with_no_invite_held():
    """A device that has received no /$DEV/handshake has no round id to
    echo, and the rig refuses to invent one."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.start(0)                        # RUNNING: no invite will ever come
    rec.link_up(100)
    with pytest.raises(AssertionError, match="no round id"):
        rec.accept(200)


def test_the_policy_accepts_once_per_link_up():
    """A link-down forgets the round id; the next link-up's first invite
    is accepted again."""
    rec = Recorder(name="t", summary="s", handshake=ACCEPT)
    rec.link_up(0)
    rec.link_down(1000)
    rec.advance_to(17000)
    rec.link_up(17000)
    rec.advance_to(23000)
    data = rec.finish()
    outs = [s["t"] for s in data["steps"]
            if s.get("expect_out", {}).get("address") == "/game/handshake"]
    assert outs == []                   # the policy records no expect_out
    assert [s["t"] for s in _sends(data, "/$DEV/validated")] == [300, 17300]


def test_a_rival_fills_the_scored_slot_and_the_device_is_denied():
    rec = Recorder(name="t", summary="s", handshake=ACCEPT)
    rec.link_up(0)
    rec.rival_accept(100)
    rec.advance_to(500)
    data = rec.finish()
    deny = _sends(data, "/$DEV/deny")
    assert deny[0]["control_sends"]["args"][0] == "scored full"
    assert "ct2" not in str(data)


def test_a_legacy_join_records_only_controls_error():
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.legacy_join(200, "CONTRACT_PLAYER_NODE")
    data = rec.finish()
    assert [s["control_sends"]["args"] for s in _sends(data, "/$DEV/error")] \
        == [["join", "retired in contract v3: use /game/handshake"]]
    assert not [s for s in data["steps"]
                if s.get("expect_out", {}).get("address") == "/game/join"]
    assert [s for s in data["steps"] if s["t"] == 200] == \
        _sends(data, "/$DEV/error")


def test_a_room_node_accept_binds_the_armed_fixture():
    rec = Recorder(name="t", summary="s", handshake=None, with_room=True)
    rec.link_up(0)
    rec.arm_fixture(200)
    rec.accept(400, node=ROOM_NODE_ID)
    rec.advance_to(600)
    data = rec.finish()
    assert not _sends(data, "/$DEV/validated")
    assert not _sends(data, "/$DEV/role")
    assert [s for s in _sends(data, "/$DEV/leds") if s["t"] >= 400]


def test_arm_fixture_needs_a_room():
    rec = Recorder(name="t", summary="s", handshake=None)
    with pytest.raises(AssertionError, match="with_room"):
        rec.arm_fixture(0)


def test_a_bad_handshake_policy_is_refused():
    with pytest.raises(ValueError):
        Recorder(name="t", summary="s", handshake={"node": ""})


def test_tap_plays_the_known_sample_and_stamps_a_future_look():
    """ContractBit's tap returns a PlayCue now and a LightCue half a second
    out. Both halves are read back off the wire: /$DEV/play "tick" at the
    gesture, and a /$DEV/leds frame whose presentation time `at` is exactly
    the cue's own `when`, not the tick it happened to be rendered on."""
    rec = _playing()
    rec.tap(500, duration_ms=80.0)
    rec.advance_to(1100)
    data = rec.finish()

    plays = _sends(data, "/$DEV/play")
    assert [(s["t"], s["control_sends"]["args"]) for s in plays] == [
        (500, ["tick", ""])]

    # onset + the production cue horizon + ContractBit's own 0.5 s lead
    look_at = 500 + round(CUE_HORIZON_S * 1000) + 500
    looks = [s for s in _sends(data, "/$DEV/leds")
             if s["control_sends"]["at"] == look_at]
    assert len(looks) == 1
    assert looks[0]["t"] < look_at        # sent ahead of its own showing time

    gestures = [s["gesture"] for s in data["steps"] if "gesture" in s]
    assert gestures == [{"kind": "tap", "onset_t": 500, "duration_ms": 80.0}]
    taps = [s["expect_out"] for s in data["steps"]
            if s.get("expect_out", {}).get("address") == "/game/tap"]
    assert taps[0]["args"] == ["$DEV", 0.0, 80.0, 1]
    assert taps[0]["stamp_t"] == 500
    assert taps[0]["typespec"] == "sffi"


def test_hold_plays_the_unknown_sample_and_swing_plays_nothing():
    """ContractBit's hold returns a PlayCue for a name the role never
    declares; its swing returns no cues at all. Both read off the wire."""
    rec = _playing()
    rec.hold(600, held_s=0.65)
    rec.swing(800, signed_g=-2.1)
    rec.advance_to(1000)
    data = rec.finish()

    plays = _sends(data, "/$DEV/play")
    assert [(s["t"], s["control_sends"]["args"]) for s in plays] == [
        (600, ["not_a_real_sample", ""])]
    assert not _sends(data, "/$DEV/error")

    outs = {s["expect_out"]["address"]: s["expect_out"] for s in data["steps"]
            if "expect_out" in s}
    assert outs["/game/hold"]["args"] == ["$DEV", 0.65, 1]
    assert outs["/game/hold"]["stamp_t"] == 600
    assert outs["/game/hold"]["typespec"] == "sfi"
    assert outs["/game/swing"]["args"] == ["$DEV", -2.1, 1]
    assert outs["/game/swing"]["stamp_t"] == 800
    assert outs["/game/swing"]["typespec"] == "sfi"


def test_a_gesture_before_a_role_is_refused_by_control():
    """The other half of rule 2, and what scenario 5's expect_quiet rests
    on: a hold with no role earns an /$DEV/error and no sample."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.hold(200, held_s=0.65)
    data = rec.finish()

    errors = _sends(data, "/$DEV/error")
    assert errors and errors[0]["control_sends"]["args"][0] == "hold"
    assert not _sends(data, "/$DEV/play")


def test_expect_quiet_records_a_window_the_rig_really_kept_quiet():
    rec = _playing()
    rec.hold(600, held_s=0.65)
    rec.advance_to(800)
    rec.expect_quiet(800, ["/game/hold", "/game/swing"], 1000)
    data = rec.finish()
    assert {"t": 800, "expect_quiet": {
        "addresses": ["/game/hold", "/game/swing"],
        "for_ms": 1000}} in data["steps"]


def test_expect_quiet_raises_when_the_rig_itself_sent_one_of_them():
    """An expect_quiet the scenario's own gestures contradict would ask a
    device to do the impossible."""
    rec = _playing()
    rec.hold(900, held_s=0.65)
    with pytest.raises(AssertionError, match="/game/hold"):
        rec.expect_quiet(600, ["/game/hold", "/game/swing"], 1000)


def test_expect_hello_and_expect_handshake_out_check_the_rig_scripted_them():
    rec = _playing()
    rec.expect_hello(0)                       # really scripted at 0
    rec.expect_handshake_out(300)             # the policy's accept
    with pytest.raises(AssertionError, match="no /game/hello scripted"):
        rec.expect_hello(123)
    with pytest.raises(AssertionError, match="no /game/handshake scripted"):
        rec.expect_handshake_out(123)


def test_advance_to_refuses_to_run_the_timeline_backwards():
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.advance_to(500)
    with pytest.raises(ValueError, match="already at t=500ms"):
        rec.advance_to(400)


def test_the_rig_runs_at_the_production_cue_horizon():
    """Every recorded presentation time is computed with it, so it must be
    BootConfig's shipped default and not the library default of 0."""
    import dataclasses

    from control.boot_config import BootConfig

    shipped = [f.default for f in dataclasses.fields(BootConfig)
               if f.name == "cue_horizon"][0]
    rec = Recorder(name="t", summary="s", handshake=ACCEPT)
    assert rec.cue_horizon == shipped != 0.0
    assert rec._gs._horizon == shipped
    assert rec._agent._horizon == shipped

    rec.link_up(0)
    rec.start(START)
    rec.advance_to(START + 200)
    leds = _sends(rec.finish(), "/$DEV/leds")
    # A stream frame's own origin is this tick plus the horizon.
    assert all(s["control_sends"]["at"] == s["t"] + round(shipped * 1000)
               for s in leds)


def test_only_leds_carries_a_presentation_time():
    """`at` distinguishes "no declared presentation time" from a real one:
    it is null for every verb protocol.py does not stamp, and an int for
    /leds even when that int is small."""
    rec = _playing()
    rec.tap(500, duration_ms=80.0)
    rec.advance_to(700)
    data = rec.finish()
    for step in data["steps"]:
        cs = step.get("control_sends")
        if cs is None:
            continue
        if cs["address"].endswith("/leds"):
            assert isinstance(cs["at"], int)
        else:
            assert cs["at"] is None


def test_the_look_goes_quiet_once_it_has_settled():
    """ContractBit "sends a timed look, then goes quiet" (spec 5.4): once
    the look has landed and the instrument's glide has settled, Control
    emits no further frames at all, so scenario 4 has real silence for the
    last frame to hold through."""
    rec = _playing()
    rec.tap(500, duration_ms=80.0)
    rec.advance_to(4000)
    settled = len(_sends(rec.finish(), "/$DEV/leds"))
    rec.advance_to(12000)
    data = rec.finish()
    leds = _sends(data, "/$DEV/leds")
    assert len(leds) == settled           # eight more seconds, no new frame
    assert [s for s in leds if s["t"] > 4000] == []
    rec.expect_frame(12000)               # the last frame is still showing


def test_expect_frame_records_the_frame_showing_not_the_one_last_sent():
    """Send time and presentation time differ by the cue horizon, so the
    newest frame on the wire is not yet the one on the pixels."""
    rec = _playing()
    rec.tap(500, duration_ms=80.0)
    rec.advance_to(1100)
    rec.expect_frame(1100)
    data = rec.finish()

    frames = [s["expect_frame"] for s in data["steps"] if "expect_frame" in s]
    assert len(frames) == 1
    assert len(frames[0]["grb"]) == 36
    assert all(isinstance(c, int) and 0 <= c <= 255 for c in frames[0]["grb"])

    leds = _sends(data, "/$DEV/leds")
    showing = [s for s in leds if s["control_sends"]["at"] <= 1100]
    assert frames[0]["grb"] == showing[-1]["control_sends"]["args"][0]
    assert showing[-1] is not leds[-1]     # later frames are still in flight


def test_expect_frame_picks_the_newest_by_at_not_the_last_received():
    """Spec rule 3's middle clause: when several frames are due, only the
    NEWEST shows. Control's own stream never puts two frames on one
    presentation time, so the pair here is hand-authored, and the newer one
    is sent FIRST: a recorder that simply took whatever arrived last would
    record the older payload."""
    newer = [0, 0, 255] * 12
    older = [255, 0, 0] * 12
    rec = _playing()
    rec.advance_to(6000)
    rec.control_send_now("/$DEV/leds", "b", [newer], at=6200)
    rec.control_send_now("/$DEV/leds", "b", [older], at=6100)
    rec.advance_to(6500)
    rec.expect_frame(6500)
    data = rec.finish()

    frames = [s["expect_frame"] for s in data["steps"] if "expect_frame" in s]
    assert frames[-1]["grb"] == newer


def test_expect_frame_ignores_a_frame_step_flagged_malformed():
    """A deliberately broken /leds step is what the device must DROP, so it
    can never be the answer to "what is showing"."""
    rec = _playing()
    rec.advance_to(6000)
    rec.control_send_now("/$DEV/leds", "b", ["not a list"], at=6100,
                         malformed=True)
    rec.advance_to(6500)
    rec.expect_frame(6500)
    data = rec.finish()

    grb = [s["expect_frame"] for s in data["steps"] if "expect_frame" in s][-1]
    assert grb["grb"] != "not a list"
    assert len(grb["grb"]) == 36


def test_expect_frame_raises_rather_than_recording_nothing():
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    with pytest.raises(AssertionError):
        rec.expect_frame(0)


def test_expect_frame_held_repeats_the_pre_outage_frame_not_a_later_one():
    """A link-down window: Control goes on sending real (unheard) frames
    past `since_t_ms`, and expect_frame_held must still report what was
    showing at `since_t_ms`, not whatever an ordinary expect_frame(t_ms)
    would have found among those later, undelivered sends."""
    rec = _playing()
    rec.advance_to(6000)
    pre_outage = rec._frame_showing_at(6000)
    # A later frame Control "sends" that a device whose link is down never
    # actually hears -- exactly what a real stale-timeout reap fade would
    # look like on the wire during an outage.
    later = [9, 9, 9] * 12
    rec.control_send_now("/$DEV/leds", "b", [later], at=6100)
    rec.expect_frame_held(6050, since_t_ms=6000)
    data = rec.finish()

    held = [s["expect_frame"] for s in data["steps"] if "expect_frame" in s]
    assert held == [{"grb": pre_outage}]
    assert held[0]["grb"] != later


def test_expect_frame_held_raises_when_nothing_had_reached_the_device_yet():
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    with pytest.raises(AssertionError):
        rec.expect_frame_held(1000, since_t_ms=0)


def test_expect_play_records_the_time_control_actually_sent_it():
    rec = _playing()
    rec.tap(500, duration_ms=80.0)
    rec.expect_play(600, "tick")
    data = rec.finish()

    plays = [s for s in data["steps"] if "expect_play" in s]
    assert len(plays) == 1
    assert plays[0]["expect_play"]["name"] == "tick"
    assert plays[0]["expect_play"]["params"] == ""
    assert plays[0]["t"] == 500           # when it was sent, not the deadline


def test_expect_play_raises_when_control_never_sent_that_sample():
    rec = _playing()
    with pytest.raises(AssertionError):
        rec.expect_play(START, "tick")


def test_unload_bit_makes_control_release_the_device():
    rec = _playing()
    rec.advance_to(START + 100)
    rec.unload_bit()
    data = rec.finish()

    releases = _sends(data, "/$DEV/release")
    assert len(releases) == 1
    assert releases[0]["control_sends"]["typespec"] == ""
    assert releases[0]["control_sends"]["args"] == []


def test_control_send_now_appends_a_hand_authored_step():
    """Scenario 11's malformed inputs are authored, not captured: nothing
    real ever sends them, so they must still land in the steps."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.advance_to(300)
    rec.control_send_now("/$DEV/leds", "s", ["not-a-blob"], at=400)
    data = rec.finish()

    bogus = [s for s in _sends(data, "/$DEV/leds")
             if s["control_sends"]["typespec"] == "s"]
    assert bogus == [{"t": 300, "control_sends": {
        "address": "/$DEV/leds", "typespec": "s", "args": ["not-a-blob"],
        "at": 400}}]


def test_a_hand_authored_step_can_declare_itself_malformed():
    """The malformed-input scenario's own steps have to be distinguishable
    from every real recorded message, because the contract's verb table
    validates the real ones and must refuse these (spec section 4.3,
    rule 6). A step that does NOT declare itself malformed carries no such
    key, so ordinary recordings keep the published step shape."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.advance_to(300)
    rec.control_send_now("/$DEV/bogus", "s", ["hello"], malformed=True)
    rec.control_send_now("/$DEV/play", "ss", ["tick", ""])
    data = rec.finish()

    assert _sends(data, "/$DEV/bogus")[0]["control_sends"]["malformed"] is True
    assert "malformed" not in _sends(data, "/$DEV/play")[0]["control_sends"]


def test_a_tcp_routed_down_message_is_captured():
    """/$DEV/room is a tcp row, so it reaches the fake through send_cmd.
    The recorder must capture that channel as well as send."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    assert "tcp" in rec._fake.channels
    assert [a for (_t, a, _ts, _spec, _v) in rec._sent
            if a.endswith("/room")]
    assert all(a.startswith(f"/{rec.dev}/") for (_t, a, *_r) in rec._sent)


def test_only_this_devices_traffic_is_captured():
    """The capture wrapper drops everything that is not addressed to the
    scripted device: the real ownership probe (driven here through the
    production function that sends it, after the wrapper is installed) and
    the Room fixtures' own frames."""
    from devicelink.o2_transport import verify_service_ownership

    rec = Recorder(name="t", summary="s", handshake=None, with_room=True)
    rec.link_up(0)
    assert verify_service_ownership(rec._fake, "game", timeout=0.0)
    rec.advance_to(200)
    data = rec.finish()

    addresses = {s["control_sends"]["address"] for s in data["steps"]
                 if "control_sends" in s}
    assert addresses
    assert not [a for a in addresses if "_svcheck" in a]
    assert all(a.startswith("/$DEV/") for a in addresses)


def test_link_down_stops_the_heartbeat_and_link_up_resumes_it():
    """Observed on Control's side, not asserted from the rig's own list:
    the device pool's last-seen time stops at the last hello before the
    link fell, and moves again once it is back. (Neither /room, now sent
    on first contact only, nor the time-driven invite cycle can show it.)"""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    rec.link_down(1000)
    rec.advance_to(7000)
    assert rec._gs.devices.get(rec.dev).last_seen == 0.0   # no 5000 hello

    rec.link_up(8000)
    rec.advance_to(14000)
    assert rec._gs.devices.get(rec.dev).last_seen == 13.0  # 8000, 13000


@pytest.mark.parametrize("with_room", [False, True])
def test_the_real_dev_id_never_appears_anywhere_in_the_output(with_room):
    import json as _json
    rec = _playing(with_room=with_room)
    rec.tap(500, duration_ms=80.0)
    rec.advance_to(1100)
    rec.expect_frame(1100)
    rec.expect_play(1100, "tick")
    rec.unload_bit()
    text = _json.dumps(rec.finish(), sort_keys=True)
    assert rec.dev == "ct1"
    assert "ct1" not in text
    assert "$DEV" in text


def test_the_lobby_chime_key_is_normalized_to_a_placeholder():
    """A real validation ceremony, with the Room's fixtures loaded: the
    lobby's chime carries key=<midi note>, which counts validations and
    must not be pinned on a device."""
    rec = Recorder(name="t", summary="s", handshake=ACCEPT, with_room=True)
    rec.link_up(0)
    rec.advance_to(4000)
    data = rec.finish()

    chimes = [s["control_sends"]["args"] for s in _sends(data, "/$DEV/play")
              if s["control_sends"]["args"][0] == "chime"]
    assert chimes == [["chime", "key=$KEY"]]
    assert _sends(data, "/$DEV/validated")


def test_two_identical_runs_serialize_byte_for_byte():
    """Determinism is the whole point: Task 7 commits these recordings and
    a regression test re-records them. Two fresh rigs in one process, same
    script, must produce identical JSON."""
    import json as _json

    def record():
        rec = Recorder(name="det", summary="s", handshake=ACCEPT,
                       with_room=True)
        rec.link_up(0)
        rec.expect_hello(0)
        rec.advance_to(3000)              # the validation ceremony plays
        rec.start(3000)
        rec.hold(3100, held_s=0.4)
        rec.swing(3200, signed_g=1.8)
        rec.tap(3300, duration_ms=60.0)
        rec.advance_to(6000)
        rec.expect_frame(6000)
        rec.expect_play(6000, "tick")
        rec.expect_quiet(6000, ["/game/hold"], 500)
        rec.unload_bit()
        return _json.dumps(rec.finish(), indent=2, sort_keys=True) + "\n"

    first, second = record(), record()
    assert first == second
    assert len(first) > 1000              # a real recording, not an empty one


_SEED_SCRIPT = """
import hashlib, json
from contract_kit.recorder import Recorder
rec = Recorder(name="det", summary="s", handshake={"node": "", "ack_after_ms": 300},
               with_room=True)
rec.link_up(0)
rec.advance_to(3000)
rec.start(3000)
rec.tap(3300, 50.0)
rec.advance_to(4000)
text = json.dumps(rec.finish(), indent=2, sort_keys=True) + "\\n"
print(hashlib.sha256(text.encode()).hexdigest())
"""


def test_a_recording_does_not_depend_on_the_hash_seed():
    """Dict and set iteration are hash-ordered, and Task 7's re-record runs
    in a fresh interpreter with a fresh seed. Two seeds, one digest. The
    digest is compared between the runs, not to a literal, so a deliberate
    change to a recording does not have to be restated here."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    def digest(seed):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(root)}
        out = subprocess.run([sys.executable, "-c", _SEED_SCRIPT], env=env,
                             cwd=root, capture_output=True, text=True,
                             check=True, timeout=60)
        return out.stdout.strip()

    first = digest("0")
    assert len(first) == 64
    assert digest("99999") == first


def test_an_echo_of_a_previous_rounds_id_is_not_recorded_as_round():
    """Only the CURRENT round id becomes $ROUND. After an unload and a
    reload, an echo of the first round's id must stay distinguishable from
    a correct echo, or a scenario could hide a real stale id."""
    rec = Recorder(name="t", summary="s", handshake=None)
    rec.link_up(0)
    first = rec.control_round_id()
    rec.unload_bit()
    rec.load_bit(1000)
    second = rec.control_round_id()
    assert second != first
    rec.advance_to(5000)                 # the new round's invite arrives
    rec.accept(5100, round_id=first)     # the device echoes the OLD id
    rec.accept(5200)                     # then the current one
    data = rec.finish()

    outs = [s["expect_out"]["args"] for s in data["steps"]
            if s.get("expect_out", {}).get("address") == "/game/handshake"]
    assert outs == [["$DEV", "$ROUND_PREV", ""], ["$DEV", "$ROUND", ""]]
    assert {"t": 5100, "accept": {"node": "", "round_id": "$ROUND_PREV"}} \
        in data["steps"]
    invites = [(s["t"], s["control_sends"]["args"])
               for s in _sends(data, "/$DEV/handshake")]
    # Each invite was labelled against the round current when it was sent.
    assert invites[0] == (0, ["$ROUND"])
    assert invites[-1][1] == ["$ROUND"] and invites[-1][0] >= 1000
    assert first not in str(data) and second not in str(data)
    # The old echo is dropped; the current one validates.
    assert [s["t"] for s in _sends(data, "/$DEV/validated")] == [5200]
