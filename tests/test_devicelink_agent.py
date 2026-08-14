"""DeviceLinkAgent: inbound dispatch and the registration path, against an
in-process fake server (no sockets -- see test_devicelink_server.py)."""

import pytest

# devicelink.agent imports harness.device_bridge, which needs the sibling
# luxaeterna checkout. Guard it the same way tests/test_device_bridge.py and
# tests/test_led_smoke.py do, so the core suite still collects without it
# (requirements-dev.txt states that contract).
pytest.importorskip("luxaeterna")

from bits.test_bit import TestBit
from control.audio import AudioBridge, FakePool
from control.breath import BREATH_CC
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import FakeRoomLightSink, RoomBridge
from control.rooms import Room, RoomType
from control.state import State
from devicelink.agent import DeviceLinkAgent


class FakeServer:
    """Same tick-thread API as DeviceLinkServer, no sockets."""

    def __init__(self):
        self.new_clients = []
        self.inbound = []
        self.sent = []          # (dev, msg)
        self.broadcasts = []
        self._devs = {}         # dev -> client

    def drain_new_clients(self):
        out, self.new_clients = self.new_clients, []
        return out

    def drain_inbound(self):
        out, self.inbound = self.inbound, []
        return out

    def bind_dev(self, dev, client):
        self._devs[dev] = client

    def drop_dev(self, dev):
        self._devs.pop(dev, None)

    def send(self, dev, msg):
        # Mirrors DeviceLinkServer.send: an unbound dev is a silent no-op
        # (boundary rule 2), not a recorded send.
        if dev not in self._devs:
            return
        self.sent.append((dev, msg))

    def broadcast(self, msg):
        self.broadcasts.append(msg)

    # --- test helpers ---
    def arrive(self, client):
        self.new_clients.append(client)

    def deliver(self, client, address, typespec="", args=None):
        self.inbound.append((client, {"address": address,
                                      "typespec": typespec,
                                      "args": args or []}))

    def addressed(self, address):
        return [m for _, m in self.sent if m["address"] == address]


class RaisingSendServer(FakeServer):
    """A FakeServer whose send() always raises -- exercises the boundary
    guarantee that a transport failure notifying one device can never
    strand another device's bridge or wedge the engine in UNLOADING."""

    def send(self, dev, msg):
        raise RuntimeError("transport exploded")


@pytest.fixture
def rig():
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    return gs, server, agent


def _hello(server, agent, client="c1", dev="ie1"):
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
    agent.poll()


class _Clock:
    """A hand-advanced clock. The breath only changes value every ~47 ms of
    7-bit quantization, so a test has to move time deliberately to see a new
    one; real time between two statements never would."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _agent_with_joined_device(dev="ie1"):
    """An agent with one device already joined to TEST_PLAYER_NODE -- the
    role that declares aurora's `level` param and so needs Control to drive
    its breath (see control/breath.py). Same deliver-then-poll() shape every
    other join test in this file uses (e.g.
    test_granted_join_builds_a_light_session), just factored out since the
    breath tests below all need the same joined-device starting point. Built
    on a hand-advanced clock (see tests at :202 and :239 for this file's
    existing clock=clk convention) rather than real time, since the join
    poll() itself already sends the device's first breath -- the tests need
    to move time deliberately to observe a change from there, not rely on
    however many microseconds elapse between two Python statements."""
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")
    _hello(server, agent, client="c1", dev=dev)
    server.deliver("c1", "/game/join", "ss", [dev, "TEST_PLAYER_NODE"])
    agent.poll()
    return gs, server, agent, dev, clk


# A fake clock with coarse-enough steps that the ~0.6s sys:closing
# GainSignature (luxaeterna's synth/status.py) finishes within a handful of
# render_into() calls -- see tests/test_devicelink_frames.py for the
# detailed fade-then-release test; this file's release tests only need to
# know it eventually finishes, not observe the fade frames themselves.
_CLOSING_CLOCK_SCHEDULE = [i * 0.1 for i in range(5000)]

# Released devices now stay in DeviceLinkAgent.bridges until their closing
# fade finishes (devicelink/agent.py _on_release/_render_frames), so
# release-path tests must drive poll() forward instead of asserting
# immediately after gs.abort(). Bounded so a regression here fails fast
# instead of hanging.
_CLOSING_POLL_LIMIT = 200


def _drain_releases(agent, devs, limit=_CLOSING_POLL_LIMIT):
    """Poll until every dev in `devs` is gone from agent.bridges (its
    closing fade -- or the stuck-session guard -- has finished), or fail."""
    remaining = set(devs)
    for _ in range(limit):
        agent.poll()
        remaining &= set(agent.bridges)
        if not remaining:
            return
    pytest.fail(f"release never finished for {remaining}")


def test_hello_registers_the_device_in_the_pool(rig):
    gs, server, agent = rig
    _hello(server, agent)
    assert [d.dev for d in gs.devices.all()] == ["ie1"]


def test_granted_join_sends_role_blob_byte_identical(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    roles = server.addressed("/ie1/role")
    assert len(roles) == 1
    blob = roles[0]["args"][0]
    assert blob["role"] == "player"
    assert blob["light_manifest"]["bit_name"] == "test_bit"
    assert blob["light_manifest"]["instruments"][0]["instrument"] == "aurora"


def test_granted_join_builds_a_light_session(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert agent.bridges["ie1"].session is not None


def test_denied_join_sends_deny_with_engine_reason(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "NO_SUCH_NODE"])
    agent.poll()

    denies = server.addressed("/ie1/deny")
    assert denies[0]["args"][0] == "no such node"
    assert "ie1" not in agent.bridges


def test_join_with_no_bit_loaded_is_denied(rig):
    gs, server, agent = rig
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert server.addressed("/ie1/deny")[0]["args"][0] == \
        "no Bit accepting registrations"


def test_verb_refusal_becomes_an_error_event(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_JAM_NODE"])
    agent.poll()
    server.deliver("c1", "/game/wiggle", "s", ["ie1"])
    agent.poll()
    assert server.addressed("/ie1/error")[0]["args"] == \
        ["wiggle", "unknown verb 'wiggle'"]


def test_malformed_envelope_is_dropped_silently(rig):
    gs, server, agent = rig
    server.arrive("c1")
    server.inbound.append(("c1", {"nonsense": True}))
    agent.poll()          # must not raise
    assert server.sent == []


def test_release_sends_release_and_clears_the_bridge():
    """gs.abort() starts the release path (DeviceLinkAgent._on_release),
    which now plays the device's closing fade before dropping it -- see
    tests/test_devicelink_frames.py for the detailed fade-then-release test.
    A fake clock (rather than the `rig` fixture's real one) is needed so the
    fade actually finishes within a bounded number of poll()s here."""
    clk = iter(_CLOSING_CLOCK_SCHEDULE).__next__
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    gs.abort()
    _drain_releases(agent, ["ie1"])
    assert server.addressed("/ie1/release")
    assert "ie1" not in agent.bridges


def test_light_cue_reaches_the_devices_session(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    session = agent.bridges["ie1"].session
    gs.on_light_cue("ie1", 0xB0, 74, 100)     # must not raise
    assert session is agent.bridges["ie1"].session


def test_a_raising_transport_does_not_strand_any_device_on_release():
    """A release-notify send failure for one device must not leave any
    device's bridge stranded, nor wedge the engine outside IDLE -- the
    boundary-rule-2 guarantee that both DeviceLinkAgent._on_release and
    GameServer._unload's release loop now provide independently. This holds
    all the way through the closing fade too: every /<dev>/leds and the
    eventual /<dev>/release send can fail (RaisingSendServer always raises)
    without preventing the fade from finishing or the bridge from being
    dropped -- _finish_release's map cleanup does not depend on the send
    succeeding, see devicelink/agent.py."""
    clk = iter(_CLOSING_CLOCK_SCHEDULE).__next__
    gs = GameServer({"test_bit": TestBit})
    server = RaisingSendServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")

    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    _hello(server, agent, client="c2", dev="ie2")
    server.deliver("c2", "/game/join", "ss", ["ie2", "TEST_JAM_NODE"])
    agent.poll()

    assert set(agent.bridges) == {"ie1", "ie2"}

    gs.run()
    gs.abort()          # must not raise, must not wedge

    assert gs.state == State.IDLE
    _drain_releases(agent, ["ie1", "ie2"])   # must not raise, must not wedge
    assert agent.bridges == {}


def test_failing_on_grant_sends_error_not_role_and_omits_the_bridge(rig, monkeypatch):
    gs, server, agent = rig
    gs.load_bit("test_bit")

    class ExplodingBridge:
        def __init__(self, capability=None, clock=None):
            pass

        def on_grant(self, result):
            raise RuntimeError("boom")

    monkeypatch.setattr("devicelink.agent.DeviceBridge", ExplodingBridge)
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    assert server.addressed("/ie1/role") == []
    assert server.addressed("/ie1/error")[0]["args"] == \
        ["role", "could not build light session"]
    assert "ie1" not in agent.bridges


def test_joined_device_receives_the_breath_on_cc11():
    # Declaring `level` opts aurora out of its own breathing clock, so Control
    # has to drive it. Without this, a devicelink device renders a static
    # surface: the regression this task exists to prevent. The join poll()
    # already sent this device's first breath (see _agent_with_joined_device),
    # so time has to move deliberately for a further poll() to send another
    # one -- a real clock's microseconds between statements would not clear
    # the ~47 ms quantization step, which is why this fixture hands us clk.
    gs, server, agent, dev, clk = _agent_with_joined_device()
    seen = []
    agent.bridges[dev].session.feed_midi = lambda s, a, b: seen.append((s, a, b))
    clk.advance(1.0)
    agent.poll()
    assert [m for m in seen if m[0] == 0xB0 and m[1] == BREATH_CC]


def test_breath_is_only_sent_when_the_value_changes():
    # 44 Hz tick, ~6 s envelope: resending an unchanged 7-bit value every frame
    # is pure noise on the render path. Pin both directions: no time movement
    # means no resend, and moving time means exactly one.
    gs, server, agent, dev, clk = _agent_with_joined_device()
    seen = []
    agent.bridges[dev].session.feed_midi = lambda s, a, b: seen.append((s, a, b))
    for _ in range(3):
        agent.poll()                      # clock does not move: no resend
    assert not [m for m in seen if m[1] == BREATH_CC]
    clk.advance(1.0)
    agent.poll()
    breaths = [m for m in seen if m[1] == BREATH_CC]
    assert len(breaths) == 1


def test_a_closing_device_is_not_fed_the_breath():
    # It is rendering its release fade; the breath would fight it. Genuinely
    # non-vacuous: clk.advance(1.0) guarantees a changed value, so without the
    # closing guard this would produce exactly one breath (see
    # test_joined_device_receives_the_breath_on_cc11, same advance, same
    # starting state, breath IS sent there).
    gs, server, agent, dev, clk = _agent_with_joined_device()
    agent._closing[dev] = 0
    seen = []
    agent.bridges[dev].session.feed_midi = lambda s, a, b: seen.append((s, a, b))
    clk.advance(1.0)
    agent.poll()
    assert not [m for m in seen if m[1] == BREATH_CC]


def test_play_cue_is_sent_to_the_device():
    """A Bit's PlayCue reaches the joined device as /ie<N>/play."""
    from control.cues import PlayCue

    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("test_bit")

    client = object()
    server.arrive(client)
    server.inbound.append((client, {
        "timestamp": 0.0, "address": "/game/hello",
        "typespec": "sss", "args": ["ie1", "fake", "1"]}))
    server.inbound.append((client, {
        "timestamp": 0.0, "address": "/game/join",
        "typespec": "ss", "args": ["ie1", "TEST_PLAYER_NODE"]}))
    agent.poll()
    # TEST_PLAYER_NODE maps to the scored "player" role, which
    # RegistrationState.join() closes to new joins once RUNNING (see
    # control/registration.py) -- run() must come after the join, same
    # ordering as test_light_cue_reaches_the_devices_session above.
    gs.run()

    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args, at: [PlayCue(d, "click", "hard")]}
    gs.data("ie1", "boop", ["ie1"])

    plays = [m for _c, m in server.sent if m["address"] == "/ie1/play"]
    assert plays == [{"timestamp": 0.0, "address": "/ie1/play",
                      "typespec": "ss", "args": ["click", "hard"]}]


def test_play_cue_for_unknown_device_is_dropped():
    """_send with no registered client must not raise."""
    from control.cues import PlayCue

    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("test_bit")
    gs.run()

    agent._on_play_cue("ie9", "click", "")
    assert server.sent == []


# --- Room light wiring (devicelink/agent.py's own routing, see design spec
# section 5's approved behavior plus this task's brief for why the check
# lives here rather than in control/engine.py's cue dispatch) -------------

def _room_ready_game_server():
    """A GameServer with TestBit loaded and its Room already bound to
    'sim-room' -- the state DeviceLinkAgent sees once harness/terrarium_boot.py
    (Task 6) has already called boot()."""
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("TestBit")
    gs.room.bound_dev = "sim-room"
    binding.bind(RoomType.TEST, "sim-room")
    return gs


def _agent_with_bound_room():
    """An agent with its Room bound to 'sim-room', light routed through a
    bare FakeRoomLightSink rather than a real luxaeterna session -- for
    tests about cue routing/timing, where what matters is which MIDI tuple
    reached the sink and when, not the rendered frame. (Tests that need the
    real session -- e.g. that a fed cue actually changes the rendered
    hue -- build their own agent against the real _room_light instead; see
    test_room_dev_cue_routes_to_room_bridge_not_normal_bridges.)"""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)
    bridge = FakeRoomLightSink()
    room_bridge.bind(gs.room.bound_dev, light=bridge)
    return gs, agent, bridge


def test_room_light_session_built_from_bit_declaration():
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()

    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)

    assert room_bridge.dev == "sim-room"
    assert agent._room_light is not None


def test_room_dev_cue_routes_to_room_bridge_not_normal_bridges():
    """Real wall-clock time (the DeviceLinkAgent default) can't advance the
    session past its ~1.5s sys:loaded signature within a synchronous test
    body -- same fake-clock idiom as tests/test_devicelink_frames.py's
    CLOCK_SCHEDULE. Settle the session into RUNNING (and let aurora's level
    glide converge, luxaeterna's synth/presets.py _AURORA_LEVEL_GLIDE_TAU)
    before taking the baseline frame, so the room's aurora is not still
    breathing/fading on its own -- the room role declares `level` with no
    cc:11 lane, so once settled the frame is otherwise static, and any
    change after feeding cc:74 can only come from the cue actually reaching
    the session (not a tautological "frames differ" from an unrelated
    transition)."""
    clk = _Clock()
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge, clock=clk)
    session = agent._room_light.session
    universe = agent._room_light.universe

    for _ in range(5):
        clk.advance(2.0)
        session.render_into(universe)
    assert session.state == "running"
    baseline = bytes(universe.get_frame()[:36])

    gs.on_light_cue("sim-room", 0xB0, 74, 100)
    agent._render_room()   # drains the Room's timed queue (Task 6) so the
                            # untimed cue above -- due at once -- reaches
                            # the session before the render below
    clk.advance(2.0)
    session.render_into(universe)
    after = bytes(universe.get_frame()[:36])

    assert "sim-room" not in agent.bridges   # never treated as a player device
    assert after != baseline   # the fed cc:74 actually changed aurora's hue


def test_render_room_sends_leds_event_when_frame_changes():
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)
    client = object()
    agent.server.bind_dev("sim-room", client)   # simulate the hello handshake

    agent._render_room()

    sent = server.addressed("/sim-room/leds")
    assert sent


def test_no_room_configured_leaves_room_wiring_inert():
    gs = GameServer({"TestBit": TestBit})   # no room_binding, no room
    gs.load_bit("TestBit")
    server = FakeServer()

    agent = DeviceLinkAgent(gs, server)   # room_bridge defaults to None

    assert agent._room_light is None
    agent._render_room()   # must not raise


# --- Room cue timing: the Room branch of _on_light_cue queues on
# self._room_cues (a TimedQueue) instead of feeding immediately, so the
# Room's light waits for its declared time the same way Task 7's per-device
# path (a different mechanism -- a held frame, not held MIDI) will. Only
# _render_room's drain, at the top of the existing render step, actually
# feeds the bridge. ---------------------------------------------------------

def test_a_timed_room_cue_is_withheld_until_its_time():
    """The Room's light must not jump ahead to its declared time before
    that time arrives."""
    gs, agent, bridge = _agent_with_bound_room()      # existing helper
    agent._clock = lambda: 100.0

    gs.on_light_cue("sim-room", 0xB0, 74, 100, 100.5)
    agent.poll()
    assert bridge.fed == []

    agent._clock = lambda: 100.5
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]


def test_an_untimed_room_cue_still_applies_on_arrival():
    gs, agent, bridge = _agent_with_bound_room()
    gs.on_light_cue("sim-room", 0xB0, 74, 100)
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]


def test_a_late_room_cue_applies_and_counts_as_clamped():
    gs, agent, bridge = _agent_with_bound_room()
    agent._clock = lambda: 100.0
    gs.on_light_cue("sim-room", 0xB0, 74, 100, 99.0)
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]
    assert agent.clamped == 1


# --- Room audio wiring: room_audio (a real AudioBridge) exercised for the
# first time, plus DeviceLinkAgent.on_state_change() starting/stopping the
# Room's Arco drone as the Bit transitions RUNNING/UNLOADING -------------

def test_room_audio_bridge_gets_on_grant_at_setup():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)

    assert len(pool.acquired) == 1   # TestBit's room_test role has one instrument


@pytest.mark.skip(reason=(
    "RoomBridge.feed_midi's fan-out was removed by Task 5 of "
    "docs/superpowers/plans/2026-08-14-load-bearing-timed-cues.md "
    "(control/room_bridge.py now has separate feed_light/feed_audio, since "
    "the two halves of a Room cue are meant to release at different times). "
    "_render_room only calls feed_light until Task 8 of that plan rewrites "
    "it to release audio through feed_audio at the cue's own time. This "
    "test asserts same-tick fan-out, which no longer happens by design; "
    "superseded by that task's "
    "test_room_audio_waits_for_its_moment_and_light_does_not. Un-skip or "
    "delete once that task lands."
))
def test_room_dev_cue_reaches_audio_bridge_too():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    room_bridge = RoomBridge()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=room_bridge,
                            room_audio=room_audio)

    gs.on_light_cue("sim-room", 0xB0, 74, 90)
    agent._render_room()   # drains the Room's timed queue (Task 6); the
                            # untimed cue above is due at once

    voice = pool.acquired[0]
    assert ("cc", 74, 90) in voice.sent


def test_on_state_change_running_starts_the_drone():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)

    agent.on_state_change(State.SETUP, State.RUNNING)

    voice = pool.acquired[0]
    assert any(call[0] == "note_on" for call in voice.sent)


def test_on_state_change_unloading_stops_the_drone():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)
    agent.on_state_change(State.SETUP, State.RUNNING)

    agent.on_state_change(State.RUNNING, State.UNLOADING)

    voice = pool.acquired[0]
    assert voice.sent[-1][0] in ("note_off", "all_off")


def test_no_room_audio_injected_state_change_is_a_noop():
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    agent.on_state_change(State.SETUP, State.RUNNING)   # must not raise


# --- Audio bridge tick: poll() is the driver-loop step, so it is what has
# to drive AudioBridge.tick() -- nothing else in the stack ever calls it
# (see control/audio.py's tick() docstring: "Called once per driver-loop
# iteration"). Left uncalled, welcome-cue voices are acquired and never
# released (leaking the pool), and a real ArcoSynthPool's poll() -- which
# drives pyarco's scheduler -- never runs either. -------------------------

def test_poll_ticks_the_room_audio_bridge():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)
    assert pool.polls == 0   # nothing ticks at construction time

    agent.poll()

    assert pool.polls == 1


def test_poll_with_no_room_audio_injected_does_not_raise():
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    agent.poll()   # room_audio defaults to None -- must not raise


def test_poll_releases_a_pending_welcome_cue_after_elapsed_time():
    """Pins the leak: a welcome-cue voice acquired by AudioBridge.on_grant()
    must actually come back to the pool once its declared duration has
    elapsed and poll() has ticked. TestBit's room role (see bits/test_bit.py)
    declares no welcome, so the pending cue here is granted directly against
    the SAME AudioBridge the agent already has wired -- the same shape a
    real player join's welcome chime would take, without threading a whole
    join through DeviceLinkAgent for a case control/audio.py's own
    test_welcome_cue_note_off_fires_after_its_duration_and_frees_the_voice
    already covers at the unit level. clock=clk is shared by both the
    AudioBridge and the agent, matching the fixed construction in
    harness/terrarium_boot.py -- see this task's report for why that
    matters."""
    clk = _Clock()
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool, clock=clk)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio, clock=clk)
    player_role = gs.bit.role_table.roles["player"]   # declares welcome.audio
    room_audio.on_grant("extra-dev", player_role)
    cue_voice = pool.acquired[-1]                     # welcome voice, acquired last
    assert cue_voice not in pool.released

    clk.advance(1.0)
    agent.poll()                # welcome duration is 1.5s -- not due yet
    assert cue_voice not in pool.released

    clk.advance(1.0)
    agent.poll()                # now 2.0s elapsed: past due
    assert cue_voice in pool.released


class _RaisingAudioBridge:
    """A room_audio double whose tick() always raises -- exercises the
    boundary guarantee that an audio failure cannot escape into the engine
    tick (boundary rule 2), the same guarantee RaisingSendServer above
    exercises for transport failures. on_grant() is a no-op rather than
    raising too: _setup_room() calls it at construction time, before poll()
    is ever reached, and only the tick() failure is under test here."""

    def on_grant(self, dev, role) -> None:
        pass

    def tick(self, now=None) -> None:
        raise RuntimeError("audio tick exploded")


def test_poll_survives_a_raising_audio_tick():
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=_RaisingAudioBridge())

    agent.poll()   # must not raise; must not wedge the engine tick
