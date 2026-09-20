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

from contract_kit.contract_bit import CONTRACT_PLAYER_NODE
from contract_kit.recorder import Recorder


def _sends(data, address):
    """Every recorded control_sends step for `address`, in recorded order."""
    return [s for s in data["steps"]
            if s.get("control_sends", {}).get("address") == address]


def test_link_up_hellos_and_the_heartbeat_repeats_every_5s():
    """Control answers EVERY hello with a /$DEV/room snapshot, so the two
    room messages are proof the scripted heartbeat really reached the
    engine, not just that expect_hello appended two steps."""
    rec = Recorder(name="t", summary="s", join_node=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    data = rec.finish()

    rooms = _sends(data, "/$DEV/room")
    assert [s["t"] for s in rooms] == [0, 5000]

    hellos = [s for s in data["steps"]
              if s.get("expect_out", {}).get("address") == "/game/hello"]
    assert [s["t"] for s in hellos] == [0, 5000]
    for s in hellos:
        assert s["expect_out"]["args"] == ["$DEV", "*", "*", "*"]
        assert s["expect_out"]["typespec"] == "ssss"


def test_finish_shape_matches_export_format_v1():
    rec = Recorder(name="shape_check", summary="one line", join_node=None)
    rec.link_up(0)
    data = rec.finish()
    assert data["name"] == "shape_check"
    assert data["summary"] == "one line"
    assert data["profiles"] == ["rev1"]
    assert data["device"] == {"join_node": None}
    # No _provenance: the spec puts it only in the export's contract.json,
    # and a commit hash in every recording would dirty all of them on every
    # re-record.
    assert set(data) == {"name", "summary", "profiles", "device", "steps"}
    ts = [s["t"] for s in data["steps"]]
    assert ts == sorted(ts)


def test_join_at_link_up_gets_the_player_role_from_control():
    """The real engine's grant, decoded off the wire: a device carrying
    tuneshroom_rev1 that joins CONTRACT_PLAYER_NODE is sent /$DEV/role as a
    blob naming the player role."""
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_join(0, CONTRACT_PLAYER_NODE)
    data = rec.finish()

    roles = _sends(data, "/$DEV/role")
    assert len(roles) == 1
    assert roles[0]["control_sends"]["typespec"] == "b"
    assert roles[0]["control_sends"]["args"][0]["role"] == "player"

    joins = [s for s in data["steps"]
             if s.get("expect_out", {}).get("address") == "/game/join"]
    assert joins[0]["expect_out"]["args"] == ["$DEV", CONTRACT_PLAYER_NODE]


def test_join_now_joins_later_and_leaves_device_join_node_alone():
    """The device hellos with no node and only decides to join partway
    through: no role before the join, one after, and `device.join_node`
    stays null so a replaying device does not join at link-up."""
    rec = Recorder(name="t", summary="s", join_node=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(400)
    assert not _sends(rec.finish(), "/$DEV/role")
    rec.join_now(600, CONTRACT_PLAYER_NODE)
    data = rec.finish()

    roles = _sends(data, "/$DEV/role")
    assert [s["t"] for s in roles] == [600]
    assert roles[0]["control_sends"]["args"][0]["role"] == "player"
    assert data["device"] == {"join_node": None}
    joins = [s for s in data["steps"]
             if s.get("expect_out", {}).get("address") == "/game/join"]
    assert [s["t"] for s in joins] == [600]


def test_tap_plays_the_known_sample_and_stamps_a_future_look():
    """ContractBit's tap returns a PlayCue now and a LightCue half a second
    out. Both halves are read back off the wire: /$DEV/play "tick" at the
    gesture, and a /$DEV/leds frame whose presentation time `at` is exactly
    the cue's own `when`, not the tick it happened to be rendered on."""
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(100, duration_ms=80.0)
    rec.advance_to(700)
    data = rec.finish()

    plays = _sends(data, "/$DEV/play")
    assert [(s["t"], s["control_sends"]["args"]) for s in plays] == [
        (100, ["tick", ""])]

    looks = [s for s in _sends(data, "/$DEV/leds")
             if s["control_sends"]["at"] == 600]
    assert len(looks) == 1
    assert looks[0]["t"] > 600            # rendered on the tick after 600

    gestures = [s["gesture"] for s in data["steps"] if "gesture" in s]
    assert gestures == [{"kind": "tap", "onset_t": 100, "duration_ms": 80.0}]
    taps = [s["expect_out"] for s in data["steps"]
            if s.get("expect_out", {}).get("address") == "/game/tap"]
    assert taps[0]["args"] == ["$DEV", 0.0, 80.0, 1]
    assert taps[0]["stamp_t"] == 100
    assert taps[0]["typespec"] == "sffi"


def test_hold_plays_the_unknown_sample_and_swing_plays_nothing():
    """ContractBit's hold returns a PlayCue for a name the role never
    declares; its swing returns no cues at all. Both read off the wire."""
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.hold(200, held_s=0.65)
    rec.swing(400, signed_g=-2.1)
    rec.advance_to(600)
    data = rec.finish()

    plays = _sends(data, "/$DEV/play")
    assert [(s["t"], s["control_sends"]["args"]) for s in plays] == [
        (200, ["not_a_real_sample", ""])]
    assert not _sends(data, "/$DEV/error")

    outs = {s["expect_out"]["address"]: s["expect_out"] for s in data["steps"]
            if "expect_out" in s}
    assert outs["/game/hold"]["args"] == ["$DEV", 0.65, 1]
    assert outs["/game/hold"]["stamp_t"] == 200
    assert outs["/game/hold"]["typespec"] == "sfi"
    assert outs["/game/swing"]["args"] == ["$DEV", -2.1, 1]
    assert outs["/game/swing"]["stamp_t"] == 400
    assert outs["/game/swing"]["typespec"] == "sfi"


def test_a_gesture_before_a_role_is_refused_by_control():
    """The other half of rule 2, and what scenario 5's expect_quiet rests
    on: a hold with no role earns an /$DEV/error and no sample."""
    rec = Recorder(name="t", summary="s", join_node=None)
    rec.link_up(0)
    rec.hold(200, held_s=0.65)
    rec.expect_quiet(200, ["/game/hold", "/game/swing"], 1000)
    data = rec.finish()

    errors = _sends(data, "/$DEV/error")
    assert errors and errors[0]["control_sends"]["args"][0] == "hold"
    assert not _sends(data, "/$DEV/play")
    assert {"t": 200, "expect_quiet": {
        "addresses": ["/game/hold", "/game/swing"],
        "for_ms": 1000}} in data["steps"]


def test_expect_frame_records_the_last_grb_frame_control_really_sent():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(100, duration_ms=80.0)
    rec.advance_to(700)
    rec.expect_frame(700)
    data = rec.finish()

    frames = [s["expect_frame"] for s in data["steps"] if "expect_frame" in s]
    assert len(frames) == 1
    assert len(frames[0]["grb"]) == 36
    assert all(isinstance(c, int) and 0 <= c <= 255 for c in frames[0]["grb"])
    leds = _sends(data, "/$DEV/leds")
    assert frames[0]["grb"] == leds[-1]["control_sends"]["args"][0]


def test_expect_frame_raises_rather_than_recording_nothing():
    rec = Recorder(name="t", summary="s", join_node=None)
    rec.link_up(0)
    with pytest.raises(AssertionError):
        rec.expect_frame(0)


def test_expect_play_records_the_time_control_actually_sent_it():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(100, duration_ms=80.0)
    rec.expect_play(200, "tick")
    data = rec.finish()

    plays = [s for s in data["steps"] if "expect_play" in s]
    assert len(plays) == 1
    assert plays[0]["expect_play"]["name"] == "tick"
    assert plays[0]["expect_play"]["params"] == ""
    assert plays[0]["t"] == 100           # when it was sent, not the deadline


def test_expect_play_raises_when_control_never_sent_that_sample():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    with pytest.raises(AssertionError):
        rec.expect_play(100, "tick")


def test_unload_bit_makes_control_release_the_device():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.advance_to(100)
    rec.unload_bit()
    data = rec.finish()

    releases = _sends(data, "/$DEV/release")
    assert len(releases) == 1
    assert releases[0]["control_sends"]["typespec"] == ""
    assert releases[0]["control_sends"]["args"] == []


def test_control_send_now_appends_a_hand_authored_step():
    """Scenario 11's malformed inputs are authored, not captured: nothing
    real ever sends them, so they must still land in the steps."""
    rec = Recorder(name="t", summary="s", join_node=None)
    rec.link_up(0)
    rec.advance_to(300)
    rec.control_send_now("/$DEV/leds", "s", ["not-a-blob"], at=400)
    data = rec.finish()

    bogus = [s for s in _sends(data, "/$DEV/leds")
             if s["control_sends"]["typespec"] == "s"]
    assert bogus == [{"t": 300, "control_sends": {
        "address": "/$DEV/leds", "typespec": "s", "args": ["not-a-blob"],
        "at": 400}}]


def test_only_this_devices_traffic_is_captured():
    """The capture wrapper drops everything that is not addressed to the
    scripted device: the real ownership probe (driven here through the
    production function that sends it, after the wrapper is installed) and
    the Room fixtures' own frames."""
    from devicelink.o2_transport import verify_service_ownership

    rec = Recorder(name="t", summary="s", join_node=None, with_room=True)
    rec.link_up(0)
    assert verify_service_ownership(rec._fake, "game", timeout=0.0)
    rec.advance_to(200)
    data = rec.finish()

    addresses = {s["control_sends"]["address"] for s in data["steps"]
                 if "control_sends" in s}
    assert addresses
    assert not [a for a in addresses if "_svcheck" in a]
    assert all(a.startswith("/$DEV/") for a in addresses)


def test_the_real_dev_id_never_appears_anywhere_in_the_output():
    import json as _json
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(100, duration_ms=80.0)
    rec.advance_to(700)
    rec.expect_frame(700)
    rec.expect_play(700, "tick")
    rec.unload_bit()
    text = _json.dumps(rec.finish(), sort_keys=True)
    assert rec.dev == "ct1"
    assert "ct1" not in text
    assert "$DEV" in text


def test_the_lobby_chime_key_is_normalized_to_a_placeholder():
    """A real join ceremony, with the Room's fixtures loaded: the lobby's
    chime carries key=<midi note>, which counts joins and must not be
    pinned on a device."""
    rec = Recorder(name="t", summary="s", join_node=None, with_room=True)
    rec.link_up(0)
    rec.tap(100, duration_ms=50.0)
    rec.tap(700, duration_ms=50.0)
    rec.advance_to(4000)
    data = rec.finish()

    chimes = [s["control_sends"]["args"] for s in _sends(data, "/$DEV/play")
              if s["control_sends"]["args"][0] == "chime"]
    assert chimes == [["chime", "key=$KEY"]]
    assert _sends(data, "/$DEV/role")


def test_two_identical_runs_serialize_byte_for_byte():
    """Determinism is the whole point: Task 7 commits these recordings and
    a regression test re-records them. Two fresh rigs in one process, same
    script, must produce identical JSON."""
    import json as _json

    def record():
        rec = Recorder(name="det", summary="s", join_node=None,
                       with_room=True)
        rec.link_up(0)
        rec.expect_hello(0)
        rec.tap(100, duration_ms=50.0)
        rec.tap(700, duration_ms=50.0)
        rec.advance_to(3000)
        rec.hold(3100, held_s=0.4)
        rec.swing(3200, signed_g=1.8)
        rec.tap(3300, duration_ms=60.0)   # gameplay now, the lobby is done
        rec.advance_to(6000)
        rec.expect_frame(6000)
        rec.expect_play(6000, "tick")
        rec.expect_quiet(6000, ["/game/hold"], 500)
        rec.unload_bit()
        return _json.dumps(rec.finish(), indent=2, sort_keys=True) + "\n"

    first, second = record(), record()
    assert first == second
    assert len(first) > 1000              # a real recording, not an empty one
