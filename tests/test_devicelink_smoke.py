"""End-to-end: a real websocket client drives registration, receives its
config blob, tilts, and watches the LEDs change.

aurora breathes continuously and independently of any MIDI input (see
tests/test_devicelink_frames.py's test_cue_changes_the_frame), so "the
frames aren't all identical" is satisfied by breathing alone even with the
tilt -> cc:74 cue path completely broken. Isolating the cue path's actual
contribution needs two rigs rendering off the *identical* clock schedule,
with only cue delivery differing between them.

Registration (hello/join) rides a real socket, so its timing is not
controlled -- but ClockFeed.reset() rewinds each rig's render clock back to
the head of the same explicit schedule right before the comparable
tilt-sweep segment starts. LightSession.render_into() hands ugens the
clock's own reading as t (since luxaeterna PR #18 there is no per-session
`_start` epoch) and clamps a negative post-reset dt to a floor, so however
many renders registration's socket jitter produced, both rigs enter the
compared segment in the same state: t = CLOCK_SCHEDULE[0] at the reset, then
identical t/dt for every subsequent render, call-for-call, since both replay
the same explicit schedule and run the same fixed number of polls/ticks per
branch.
"""

import json

import pytest

# harness.devicelink_smoke pulls in devicelink.agent -> harness.device_bridge,
# which needs the sibling luxaeterna checkout. Guard it the same way
# tests/test_devicelink_agent.py, tests/test_device_bridge.py, and
# tests/test_led_smoke.py do, so the core suite still collects without it
# (requirements-dev.txt states that contract).
pytest.importorskip("luxaeterna")

from websockets.sync.client import connect

from control.state import State
from harness.devicelink_smoke import _wait_in_setup, build

# Long enough for the ~65 render_into calls one _run_rig() call produces
# (1 settle + 5 cc steps x 12-tick collects), with generous headroom.
CLOCK_SCHEDULE = [i * 0.1 for i in range(5000)]


class ClockFeed:
    """A resettable render clock. Threaded into build(clock=...) once at rig
    construction; `reset()` rewinds it to the head of CLOCK_SCHEDULE later,
    so real-socket registration timing (which happens before the reset)
    can't leak clock drift into the segment under comparison."""

    def __init__(self, schedule):
        self._schedule = schedule
        self._it = iter(schedule)

    def reset(self) -> None:
        self._it = iter(self._schedule)

    def __call__(self) -> float:
        return next(self._it)


@pytest.fixture
def rig():
    gs, server, agent = build(host="127.0.0.1", port=0, run_duration=60.0)
    yield gs, server, agent
    server.stop()


def _send(client, address, typespec, args):
    client.send(json.dumps({"timestamp": 0.0, "address": address,
                            "typespec": typespec, "args": args}))


def _collect(client, agent, gs, ticks=12):
    """Interleave engine ticks with reads so the sync client sees output."""
    out = []
    for _ in range(ticks):
        agent.poll()
        gs.tick(0.02)
        try:
            while True:
                out.append(json.loads(client.recv(timeout=0.05)))
        except TimeoutError:
            pass
    return out


def _run_rig(apply_cues: bool) -> list:
    """Register a fresh device over its own real socket, run the Bit, reset
    the render clock to the head of CLOCK_SCHEDULE (discarding whatever the
    real-socket registration phase consumed), then drive an identical
    5-step tilt sweep -- sending /game/tilt each step only when apply_cues
    -- and return the rendered LED frames.

    Every step from the reset onward runs the same fixed number of
    agent.poll()/gs.tick() calls regardless of apply_cues or how long
    registration took, so the two calls to this function consume the shared
    schedule identically; the tilt message on the wire is the only thing
    that differs."""
    clock = ClockFeed(CLOCK_SCHEDULE)
    gs, server, agent = build(host="127.0.0.1", port=0, run_duration=60.0,
                              clock=clock)
    try:
        gs.load_bit("test_bit")
        with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
            _send(client, "/game/hello", "sss", ["ie1", "sim", "1"])
            _send(client, "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
            msgs = _collect(client, agent, gs)
            roles = [m for m in msgs if m["address"] == "/ie1/role"]
            assert len(roles) == 1
            assert roles[0]["args"][0]["role"] == "player"

            gs.run()
            clock.reset()
            _collect(client, agent, gs, ticks=1)  # settle, discard

            frames = []
            for cc in (0, 30, 60, 90, 120):
                if apply_cues:
                    _send(client, "/game/tilt", "sf", ["ie1", float(cc)])
                frames += [tuple(m["args"][0])
                           for m in _collect(client, agent, gs)
                           if m["address"] == "/ie1/leds"]
        return frames
    finally:
        server.stop()


def test_tilt_drives_a_visible_hue_change():
    frames_no_cue = _run_rig(apply_cues=False)
    frames_with_cue = _run_rig(apply_cues=True)
    assert frames_with_cue != frames_no_cue, (
        "tilting must change the rendered frames beyond what aurora's own "
        "breathing already produces")


def test_denied_join_reports_the_engine_reason(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        _send(client, "/game/hello", "sss", ["ie9", "sim", "1"])
        _send(client, "/game/join", "ss", ["ie9", "NOPE"])
        msgs = _collect(client, agent, gs)
    denies = [m for m in msgs if m["address"] == "/ie9/deny"]
    assert denies[0]["args"][0] == "no such node"


def test_scored_role_joins_during_the_setup_hold_survive_run():
    """Reproduces the harness bug _wait_in_setup fixes: main() used to call
    load_bit() straight into run() with no real-world gap, so a scored role
    (TestBit's `player`, RegistrationState.join() refuses it once RUNNING)
    was unjoinable by anything slower than a Python call stack -- confirmed
    live against a real phone. Drive a real /game/join over the wire while
    _wait_in_setup is holding the Bit in SETUP, then call run() and confirm
    the join was granted and stays registered."""
    gs, server, agent = build(host="127.0.0.1", port=0)
    try:
        gs.load_bit("test_bit")
        assert gs.state == State.SETUP
        with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
            _send(client, "/game/hello", "sss", ["ie1", "sim", "1"])
            _send(client, "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
            _wait_in_setup(agent, 0.2)
            msgs = []
            try:
                while True:
                    msgs.append(json.loads(client.recv(timeout=0.05)))
            except TimeoutError:
                pass
        roles = [m for m in msgs if m["address"] == "/ie1/role"]
        assert len(roles) == 1
        assert roles[0]["args"][0]["role"] == "player"
        assert gs.state == State.SETUP  # run() not called yet -- still open
        gs.run()
        assert "ie1" in gs.registration.assignments
    finally:
        server.stop()
