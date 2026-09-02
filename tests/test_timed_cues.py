"""One gesture, one shared presentation time, on both the Room's audio and
the Room's light. Success criterion 4 of docs/superpowers/specs/
2026-08-14-load-bearing-timed-cues-design.md.

Fully offline: no Arco, no pyarco, no O2. That is the whole point -- the
equality has to be checkable without the hardware it will eventually run on.
"""

import pytest

# devicelink.agent imports harness.device_bridge, which needs the sibling
# luxaeterna checkout. Same guard tests/test_devicelink_agent.py uses.
pytest.importorskip("luxaeterna")

from bits.test.test_bit import TestBit
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from tests.instrument_fixtures import GENERIC_SURFACE
from control.rooms import Room
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import FakeServer

HORIZON = 0.060
TICK = 1.0 / 44.0


class _NoChaseTestBit(TestBit):
    """TestBit minus chase: chase addresses both TEST fixtures ("main" and
    "accent") by name, which this module's deliberately ONE-fixture Room
    cannot satisfy -- and the tests below pin single-lane presentation-time
    behavior on the ROOM-targeted tilt_hue stream, which a second fixture
    would double."""
    @property
    def function_table(self):
        table = super().function_table
        del table.functions["chase"]
        return table


class TickRecordingSession:
    """Records the clock reading at which each MIDI feed arrived, not just
    the bytes, and forwards every call to the real LightSession underneath.

    Recording the time is not test convenience: "audio at `at`, light before
    it" is unassertable against a double that only keeps payloads, and
    boundary rule 5 covers what a double OMITS as much as what it permits.
    Forwarding matters too, because the light half has to actually reach the
    real LightSession or no frame changes and there is nothing to stamp --
    which is why this wraps the fixture's own session in place rather than
    standing in for it (each Room fixture owns a real session now, and
    _render_room renders through it).
    """

    def __init__(self, clock, inner):
        self._clock = clock
        self._inner = inner
        self.fed = []                      # (now, status, d1, d2)

    @property
    def state(self):
        return self._inner.state

    def feed_midi(self, status, d1, d2):
        self.fed.append((self._clock(), status, d1, d2))
        self._inner.feed_midi(status, d1, d2)

    def render_into(self, universe):
        self._inner.render_into(universe)

    def clear(self):
        self._inner.clear()

    def shutdown(self):
        pass


class TickRecordingAudioBridge:
    """The room_audio-shaped sibling of TickRecordingSession above: the Room's
    audio channel is now a per-fixture AudioBridge grant, not one Room-wide
    sink, so this double satisfies AudioBridge's dev-keyed surface instead
    (on_grant/on_release/feed_midi/start_drone/stop_drone/silence/tick).
    Records the clock reading at which each feed_midi arrived, same as
    TickRecordingSession, so "audio at `at`, light before it" stays assertable.
    """

    def __init__(self, clock):
        self._clock = clock
        self.fed = []                      # (now, dev, status, d1, d2)
        self.granted = set()

    def on_grant(self, dev, role):
        self.granted.add(dev)

    def on_release(self, dev):
        self.granted.discard(dev)

    def feed_midi(self, dev, status, d1, d2):
        self.fed.append((self._clock(), dev, status, d1, d2))

    def start_drone(self, dev):
        pass

    def stop_drone(self, dev):
        pass

    def silence(self, dev):
        pass

    def tick(self, now=None):
        pass


def _stack(now):
    """Control with TestBit loaded, the Room bound to 'sim-room', and one
    device joined to the scored `player` role, all on one settable clock.

    Ordering matters twice. load_bit() must precede DeviceLinkAgent, because
    the agent's _setup_room() reads the loaded Bit's Room declaration at
    construction time and silently builds nothing if there is no Bit yet.
    And the join must precede run(), because TestBit's `player` is a SCORED
    role and registration refuses those once RUNNING.

    Both sessions are then driven to RUNNING before returning: a session
    still playing its welcome signature does not render a cue's effect, and
    a frozen clock never finishes that signature. The caller reads now[0]
    afterwards for its own base time.

    run_duration is large so gs.tick() can be driven for several seconds in
    the ambient test without the Bit completing and unloading underneath it.
    """
    clock = lambda: now[0]
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": lambda: _NoChaseTestBit(run_duration=1000.0)},
                    room_binding=binding, cue_horizon=HORIZON, clock=clock)
    profile = RoomProfile(surface_id="room_test", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                   blocks=(RoomBlock("main", 0, 10),),
                   zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),))
    gs.room = Room(name="TEST", profile=profile, node_id="ROOM_TEST_NODE")
    gs.room.bound["main"] = "sim-room"
    binding.bind("TEST", "main", "sim-room")
    gs.load_bit("TestBit")

    server = FakeServer()
    audio = TickRecordingAudioBridge(clock)
    agent = DeviceLinkAgent(gs, server,
                            room_audio=audio, horizon=HORIZON, clock=clock)
    assert "main" in agent._fixtures, "the fixture session must have built"

    light = TickRecordingSession(clock, agent._fixtures["main"].session)
    agent._fixtures["main"].session = light
    server.bind_dev("sim-room", "c-room")

    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    for _ in range(200):
        if (agent.bridges["ie1"].session.state == "running"
                and agent._fixtures["main"].session.state == "running"):
            break
        now[0] += 0.1
        agent.poll()
    else:
        pytest.fail("a session never reached RUNNING")
    gs.run()
    server.sent.clear()
    light.fed.clear()
    audio.fed.clear()
    agent._pending_at.clear()
    return gs, server, agent, light, audio


def test_one_gesture_yields_one_shared_presentation_time():
    now = [1000.0]
    gs, server, agent, light, audio = _stack(now)
    base = now[0]

    gesture = base - 0.005                # 5 ms of delivery on the way up
    at = gesture + HORIZON

    # Deviation from the brief: aurora's hue is behind a Smooth() one-pole
    # glide (luxaeterna's synth/presets.py), which only moves with real
    # elapsed dt. Rendering at the exact same `now` as _stack's last
    # warm-up frame gives dt ~= 0, so the fed hue would not visibly move
    # any byte of the Room's frame -- unrelated to the cue-timing behavior
    # under test. Nudge the clock forward first so the glide has room to
    # move the frame; `gesture`/`at` stay anchored to the original `base`,
    # well ahead of this nudge, so "fed before at" and "audio not yet due"
    # below still hold. Same root cause and fix as
    # test_devicelink_frames.py's
    # test_device_frame_carries_the_cues_own_time_not_the_render_clock.
    now[0] = base + 0.01
    server.deliver("c1", "/game/tilt", "sf", ["ie1", 90.0],
                   timestamp=gesture)
    agent.poll()

    # Light: fed at once, because its frame still has to cross the wire.
    assert light.fed, "the Room's light should be fed on arrival"
    assert light.fed[0][0] < at
    assert light.fed[0][1:] == (0xB0, 74, 127)

    # And the frame it produced carries the gesture's own time, not
    # Control's render clock. This is the equality the whole design exists
    # for: the light frame's declared display time is derived once, from the
    # device's own stamp.
    leds = [m for d, m in server.sent if m["address"] == "/sim-room/leds"]
    assert leds, "the Room's frame should have changed"
    assert leds[-1]["timestamp"] == pytest.approx(at)

    # Audio: not yet. It reaches Arco with no wire, so it waits for `at`.
    assert audio.fed == []

    now[0] = at + TICK
    agent.poll()
    assert [f[1:] for f in audio.fed] == [("main", 0xB0, 74, 127)]
    assert audio.fed[0][0] >= at
    assert audio.fed[0][0] - at <= TICK, "released within one tick of at"


def test_a_late_gesture_clamps_and_counts_rather_than_raising():
    """A horizon smaller than the delivery time must be VISIBLE, not silent.
    The 2026-08-13 run had 762 of 820 frames already past their deadline."""
    now = [1000.0]
    gs, server, agent, light, audio = _stack(now)
    base = now[0]

    server.deliver("c1", "/game/tilt", "sf", ["ie1", 90.0],
                   timestamp=base - 1.0)   # at is already a second past
    agent.poll()

    assert agent.clamped == 1
    assert [f[1:] for f in audio.fed] == [("main", 0xB0, 74, 127)]   # released anyway


def test_the_room_animates_with_no_gesture_at_all():
    """Bit.cues(at) closes the other half: verb handlers can only react to a
    device, so without it the Room's light never moved during a real run."""
    now = [1000.0]
    gs, server, agent, light, audio = _stack(now)

    for _ in range(5):
        now[0] += 1.0
        gs.tick(1.0)
        agent.poll()

    assert light.fed, "cues(at) should have driven the Room with no gesture"
    assert {f[1:3] for f in light.fed} == {(0xB0, 74)}
    assert len({f[3] for f in light.fed}) > 1, "the hue should have moved"
