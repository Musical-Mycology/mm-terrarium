"""MetronomeBit's player light, rendered the way a live run renders it.

Every other MetronomeBit test asserts on the cues the Bit REPORTS. This one
asserts on the bytes a player device actually displays: a real GameServer, a
real DeviceLinkAgent, real luxaeterna LightSessions, two devices joined at
METRO_PLAYER_NODE, ticked at 44 Hz on a hand-advanced clock -- and then the
harness's own BeatTapper (harness/beat_tapper.py) fed those frames in
display order, at the times they were displayed.

This is the layer the live run of 2026-09-08 (runs/20260908-214919) failed
at, with every layer above it green: the Bit reported its pulses, the agent
sent frames, the tapper was armed, and not one beat was visible in the
light. Two causes, both covered here:

  1. Control's breath (control/breath.py) drove cc:11 on every tick, the
     same lane MetronomeBit's every-beat pulse uses, so the pulse was
     overwritten within a frame or two and the player's light was the
     breath. Role.breath=False is the opt-out.
  2. The count-in started under luxaeterna's 1.5 s `sys:loaded` adoption
     ceremony, so the first beats the tapper saw were the ceremony's soft
     pulses. MetronomeBit.LEAD_IN_S now clears it.

The frame-order/`when` handling below mirrors harness/shroom_client.py's
_on_leds + tick(): a frame carries its own presentation time and is
displayed at it.
"""

import pytest

pytest.importorskip("luxaeterna")

from bits.metronome.metronome_bit import MetronomeBit
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from harness.beat_tapper import BeatTapper

from tests.test_devicelink_agent import FakeServer, _Clock

DEMO_PROFILE = load_terrarium_config("terrarium.toml").rooms["DEMO"].profile

# BootConfig.cue_horizon's shipped default, so these frames carry the same
# presentation lead a live run gives them.
HORIZON = 0.060
TICK = 1.0 / 44.0


def _rig():
    """A running MetronomeBit with ie1 and ie2 joined as players, on one
    hand-advanced clock shared by the engine and the agent (see
    tests/test_devicelink_agent.py's _agent_with_joined_device for why the
    two clocks may never diverge)."""
    clk = _Clock(100.0)
    binding = RoomBindingRegistry()
    gs = GameServer({"MetronomeBit": MetronomeBit}, room_binding=binding,
                    cue_horizon=HORIZON, clock=clk)
    gs.room = Room(name="DEMO", profile=DEMO_PROFILE, node_id="ROOM_DEMO_NODE")
    gs.load_bit("MetronomeBit")
    gs.room.bound["array"] = "sim-room-array"
    binding.bind("DEMO", "array", "sim-room-array")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, horizon=HORIZON)
    for client, dev in (("c1", "ie1"), ("c2", "ie2")):
        server.arrive(client)
        server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
        agent.poll()
        server.deliver(client, "/game/join", "ss", [dev, "METRO_PLAYER_NODE"])
        agent.poll()
    return gs, server, agent, clk


def _run_frames(seconds, dev="ie1"):
    """Tick the live loop for `seconds` and return (bit, frames), where
    frames is [(when, frame)] for `dev` in send order -- the order and the
    times a Testshroom displays them in."""
    gs, server, agent, clk = _rig()
    gs.run()
    seen, frames = 0, []
    for _ in range(int(seconds / TICK)):
        clk.advance(TICK)
        agent.poll()
        gs.tick(TICK)
        for _sent_dev, msg in server.sent[seen:]:
            if msg["address"] == f"/{dev}/leds":
                frames.append((msg["timestamp"], bytes(msg["args"][0])))
        seen = len(server.sent)
    return gs.bit, frames


def _rise_times(frames, ratio=0.05, gap=0.18):
    """The onset time of each brightness rise, a slewed pulse counting
    once: a rise within `gap` of the previous one continues it."""
    out, prev, last = [], None, None
    for when, frame in frames:
        total = sum(frame)
        if prev is not None and total > prev * (1.0 + ratio):
            if last is None or when - last > gap:
                out.append(when)
            last = when
        prev = total
    return out


def test_every_beat_of_cycle_zero_is_a_visible_rise_in_the_player_s_light():
    """The pulse MetronomeBit fires on every beat (metro_pulse_player,
    cc:11 60 -> 110) has to reach the player's own strip as a brightness
    rise AT that beat's gridpoint. With Control's breath on the same lane
    it did not: the light was the breath's 6 s triangle and no beat was
    visible at all."""
    bit, frames = _run_frames(12.0)
    assert frames, "the player device never rendered a frame"
    grid = [bit._t0 + k * bit.BEAT_S for k in range(8)]
    rises = _rise_times(frames)
    for k, at in enumerate(grid):
        near = [r for r in rises if abs(r - at) <= 0.5 * TICK]
        assert near, (f"beat {k} at {at:.4f} produced no rise; "
                      f"rises were {[round(r, 4) for r in rises]}")


def test_the_count_in_starts_after_the_adoption_ceremony_has_finished():
    """luxaeterna plays a 1.5 s `sys:loaded` signature on a role grant
    (synth/status.py _sig_loaded: a flash and two soft pulses). Beat 0 must
    land after it, or the player's first beats are invisible under it --
    and a synthetic player locks onto the ceremony instead of the beat."""
    bit, frames = _run_frames(12.0)
    grant_at = frames[0][0]
    assert bit._t0 - grant_at >= 1.5


def test_a_beat_tapper_fed_the_real_frames_taps_cycle_zero_s_answer_beats():
    """End to end for the synthetic player: the frames a Testshroom would
    display, fed to the BeatTapper at the times it would display them,
    produce a tap on each of beats 4-7 and on no call beat -- each stamped
    within a frame of its own gridpoint, which is what keeps the Bit's
    judgment inside TOLERANCE_S."""
    bit, frames = _run_frames(12.0)
    tapper = BeatTapper()
    tapper.armed = True
    taps = []
    for when, frame in frames:
        beat = tapper.observe(frame, when)
        if beat is not None:
            taps.append((beat, when))
    assert tapper.locked
    assert [beat for beat, _ in taps[:4]] == [4, 5, 6, 7]
    assert tapper.taps >= 4
    for beat, when in taps[:4]:
        at = bit._t0 + beat * bit.BEAT_S
        assert abs(when - at) <= TICK, f"beat {beat} tapped at {when - at:+.4f}"


def test_the_bit_judges_those_taps_as_a_successful_phrase():
    """The taps the tapper produces have to survive the whole input path:
    the device stamps them with its own display time, GameServer adds the
    cue_horizon, and the Bit subtracts it again (Bit.cue_horizon). A cycle
    of them must judge as a success, not merely land near the grid."""
    gs, server, agent, clk = _rig()
    gs.run()
    tapper = BeatTapper()
    tapper.armed = True
    seen = 0
    for _ in range(int(12.0 / TICK)):
        clk.advance(TICK)
        agent.poll()
        gs.tick(TICK)
        for _sent_dev, msg in server.sent[seen:]:
            if msg["address"] != "/ie1/leds":
                continue
            when = msg["timestamp"]
            if tapper.observe(bytes(msg["args"][0]), when) is not None:
                server.deliver("c1", "/game/tap", "sffi",
                               ["ie1", 1.0, 50.0, 1], timestamp=when)
        seen = len(server.sent)
    phrase = gs.bit._phrases.get(0)
    assert phrase is not None, "cycle 0 saw no tap at all"
    assert phrase["hits"] == {0, 1, 2, 3} and not phrase["spoiled"], \
        f"cycle 0 not clean: {phrase}, errors {gs.bit._tap_errors_ms}"
