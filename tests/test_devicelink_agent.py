"""DeviceLinkAgent: inbound dispatch and the registration path, against an
in-process fake server (no sockets -- see test_devicelink_server.py)."""

import pytest

# devicelink.agent imports harness.device_bridge, which needs the sibling
# luxaeterna checkout. Guard it the same way tests/test_device_bridge.py and
# tests/test_led_smoke.py do, so the core suite still collects without it
# (requirements-dev.txt states that contract).
pytest.importorskip("luxaeterna")

from bits.test.test_bit import TestBit
from control.audio import AudioBridge, FakePool
from control.breath import BREATH_CC
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import FakeRoomAudioSink, FakeRoomLightSink, RoomBridge
from control.rooms import Room
from control.terrarium_config import load_terrarium_config

TEST_PROFILE = load_terrarium_config("terrarium.toml").rooms["TEST"].profile
DEMO_PROFILE = load_terrarium_config("terrarium.toml").rooms["DEMO"].profile
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

    def deliver(self, client, address, typespec="", args=None,
                timestamp=0.0):
        # `timestamp` is not optional decoration. The real o2lite transport
        # puts o2lite's msg_timestamp here (devicelink/o2_transport.py's
        # _on_message), and the real websocket transport leaves it 0.0
        # (devicelink/protocol.py's _event default). A double that could
        # only ever produce one of those would hide half the design --
        # boundary rule 5 covers what a double omits as much as what it
        # permits.
        self.inbound.append((client, {"address": address,
                                      "typespec": typespec,
                                      "args": args or [],
                                      "timestamp": timestamp}))

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
    however many microseconds elapse between two Python statements.

    gs shares this SAME clk with the agent -- control/engine.py's own
    comment on GameServer.__init__'s clock= param ("MUST be the same
    callable DeviceLinkAgent was built with") is not just about frame
    timestamps: GameServer.reap_stale() (poll()'s stale-device check) reads
    its own clock against DevicePool.last_seen, which DeviceLinkAgent
    writes using ITS clock on every touch() call. Two unsynced clocks here
    would make every device look enormously stale on the very next poll()."""
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
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


def test_denied_join_calls_the_on_join_denied_sink():
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    calls = []
    agent = DeviceLinkAgent(gs, server,
                            on_join_denied=lambda dev, node, reason: calls.append(
                                (dev, node, reason)))
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "NO_SUCH_NODE"])
    agent.poll()

    assert calls == [("ie1", "NO_SUCH_NODE", "no such node")]


def test_a_raising_on_join_denied_sink_does_not_stop_the_deny_reply():
    """Same guarantee as on_room_frame: a failing sink must not strand the
    device without its /deny reply."""
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()

    def boom(dev, node, reason):
        raise RuntimeError("sink exploded")

    agent = DeviceLinkAgent(gs, server, on_join_denied=boom)
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "NO_SUCH_NODE"])
    agent.poll()          # must not raise

    denies = server.addressed("/ie1/deny")
    assert denies[0]["args"][0] == "no such node"


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
    fade actually finishes within a bounded number of poll()s here. gs
    shares the SAME clk as the agent -- see _agent_with_joined_device's
    docstring for why an unsynced GameServer clock now makes poll()'s
    reap_stale() reap the device immediately."""
    clk = iter(_CLOSING_CLOCK_SCHEDULE).__next__
    gs = GameServer({"test_bit": TestBit}, clock=clk)
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


def test_hello_then_canvas_is_stored(rig):
    gs, server, agent = rig
    _hello(server, agent)
    server.deliver("c1", "/game/canvas", "ss", ["ie1", "http://h:1/"])
    agent.poll()
    assert agent.canvas_urls() == {"ie1": "http://h:1/"}


def test_bad_scheme_canvas_url_is_refused_not_stored(rig):
    gs, server, agent = rig
    _hello(server, agent)
    server.deliver("c1", "/game/canvas", "ss", ["ie1", "javascript:x"])
    agent.poll()
    assert agent.canvas_urls() == {}


def test_canvas_urls_returns_a_copy(rig):
    gs, server, agent = rig
    _hello(server, agent)
    server.deliver("c1", "/game/canvas", "ss", ["ie1", "http://h:1/"])
    agent.poll()
    agent.canvas_urls().clear()
    assert agent.canvas_urls() == {"ie1": "http://h:1/"}


class _RecordingObserver:
    """Records every on_devices_change() call -- see control/engine.py's
    GameServer.add_observer for the observer protocol this mimics."""

    def __init__(self):
        self.calls = 0

    def on_devices_change(self):
        self.calls += 1


def test_canvas_for_non_fixture_dev_fires_devices_changed(rig):
    """A canvas url for a non-fixture dev arrives strictly after hello's own
    devices_changed broadcast (which went out with url still null), and
    nothing else re-fires devices_changed for it -- so _on_canvas has to poke
    the engine's observers itself. Room-fixture devs need no such poke:
    poll()'s _broadcast_room_if_changed diff already covers them (see
    docs/MM_TERRARIUM.md and this file's other canvas tests)."""
    gs, server, agent = rig
    observer = _RecordingObserver()
    gs.add_observer(observer)
    _hello(server, agent)
    before = observer.calls
    server.deliver("c1", "/game/canvas", "ss", ["ie1", "http://h:1/"])
    agent.poll()
    assert observer.calls == before + 1
    assert agent.canvas_urls() == {"ie1": "http://h:1/"}


def test_repeat_canvas_with_same_url_does_not_refire(rig):
    gs, server, agent = rig
    observer = _RecordingObserver()
    gs.add_observer(observer)
    _hello(server, agent)
    server.deliver("c1", "/game/canvas", "ss", ["ie1", "http://h:1/"])
    agent.poll()
    after_first = observer.calls

    server.deliver("c1", "/game/canvas", "ss", ["ie1", "http://h:1/"])
    agent.poll()
    assert observer.calls == after_first


def test_release_clears_the_canvas_url():
    """Same release path as test_release_sends_release_and_clears_the_bridge
    above: gs.abort() starts the closing fade, and _finish_release is what
    actually pops per-dev state once it completes."""
    clk = iter(_CLOSING_CLOCK_SCHEDULE).__next__
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/canvas", "ss", ["ie1", "http://h:1/"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    gs.abort()
    _drain_releases(agent, ["ie1"])
    assert agent.canvas_urls() == {}


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
    succeeding, see devicelink/agent.py. gs shares the SAME clk as the
    agent -- see _agent_with_joined_device's docstring."""
    clk = iter(_CLOSING_CLOCK_SCHEDULE).__next__
    gs = GameServer({"test_bit": TestBit}, clock=clk)
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

def _room_ready_game_server(bound=None):
    """A GameServer with TestBit loaded and its Room's `main` fixture
    already bound to 'sim-room-main' -- the state DeviceLinkAgent sees once
    harness/terrarium_boot.py has already called boot(). `accent` is left
    unbound by default: these tests are fundamentally about cue
    timing/routing on ONE fixture, and leaving `accent` unbound doubles as
    coverage that a partially bound Room still renders (design spec section
    6)."""
    if bound is None:
        bound = {"main": "sim-room-main"}
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.load_bit("TestBit")
    for fixture, dev in bound.items():
        gs.room.bound[fixture] = dev
        binding.bind("TEST", fixture, dev)
    return gs


def _demo_room_ready_game_server(bound=None):
    """DEMO-flavored sibling of _room_ready_game_server(): TestBit loaded
    against RoomType.DEMO instead of TEST, with DEMO's one fixture ("array",
    864px / 2592 channels -- see control/room_profile.py's ROOM_PROFILES)
    bound. This is the regression coverage for the ChannelError bug: nothing
    before this test drove _render_room() above 512 channels."""
    if bound is None:
        bound = {"array": "sim-room-array"}
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(name="DEMO", profile=DEMO_PROFILE, node_id="ROOM_DEMO_NODE")
    gs.load_bit("TestBit")
    for fixture, dev in bound.items():
        gs.room.bound[fixture] = dev
        binding.bind("DEMO", fixture, dev)
    return gs


def test_render_room_does_not_raise_for_a_profile_wider_than_512_channels():
    """Regression test for the DEMO Room ChannelError bug: DEMO's profile is
    864px / 2592 channels, well past one DMX universe. Before the
    channel_count fix, _setup_room() built the Room's light sink over a
    hardcoded 512-channel Universe, so every render_into() call raised
    ChannelError -- caught and silently swallowed by _render_room(), so the
    Room's light never rendered a single frame against a real Arco."""
    gs = _demo_room_ready_game_server()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    agent.server.bind_dev("sim-room-array", object())   # simulate the hello handshake

    assert agent._room_light is not None
    universe = agent._room_light.universe
    assert len(universe) == DEMO_PROFILE.channel_count

    agent._render_room()   # must not raise, and must actually send a frame

    sent = server.addressed("/sim-room-array/leds")
    assert sent


def _agent_with_bound_room():
    """An agent with its Room's `main` fixture bound to 'sim-room-main',
    light routed through a bare FakeRoomLightSink rather than a real
    luxaeterna session -- for tests about cue routing/timing, where what
    matters is which MIDI tuple reached the sink and when, not the rendered
    frame."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)
    bridge = FakeRoomLightSink()
    room_bridge.bind("sim-room-main", light=bridge)
    return gs, agent, bridge


def test_room_light_session_built_from_bit_declaration():
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()

    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)

    assert room_bridge.dev == "sim-room-main"
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

    gs.on_light_cue("sim-room-main", 0xB0, 74, 100)
    agent._render_room()   # the untimed cue above already reached the
                            # session synchronously inside on_light_cue; this
                            # call renders+sends the resulting frame, it
                            # does not feed anything
    clk.advance(2.0)
    session.render_into(universe)
    after = bytes(universe.get_frame()[:36])

    assert "sim-room-main" not in agent.bridges   # never treated as a player device
    assert after != baseline   # the fed cc:74 actually changed aurora's hue


def test_render_room_sends_leds_event_when_frame_changes():
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)
    client = object()
    agent.server.bind_dev("sim-room-main", client)   # simulate the hello handshake

    agent._render_room()

    sent = server.addressed("/sim-room-main/leds")
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

    gs.on_light_cue("sim-room-main", 0xB0, 74, 100, 100.5)
    agent.poll()
    assert bridge.fed == []

    agent._clock = lambda: 100.5
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]


def test_an_untimed_room_cue_still_applies_on_arrival():
    gs, agent, bridge = _agent_with_bound_room()
    gs.on_light_cue("sim-room-main", 0xB0, 74, 100)
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]


def test_a_late_room_cue_applies_and_counts_as_clamped():
    gs, agent, bridge = _agent_with_bound_room()
    agent._clock = lambda: 100.0
    gs.on_light_cue("sim-room-main", 0xB0, 74, 100, 99.0)
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


def test_gesture_stamp_reaches_the_engine():
    """The stamp is already on the envelope and already decoded; only
    _on_verb dropped it. Design Rule 4, timestamps at the source: jitter on
    the way up must not become jitter in the output."""
    gs, server, agent, dev, clk = _agent_with_joined_device()
    seen = []
    gs.data = lambda d, v, a, gesture_time=None: seen.append(gesture_time)
    server.deliver("c1", "/game/tilt", "sf", [dev, 12.0], timestamp=987.5)
    agent.poll()
    assert seen == [987.5]


def test_unstamped_gesture_reaches_the_engine_as_zero():
    """The websocket transport never stamps. GameServer falls back to its
    own clock there, and it can only do that if it is told 0.0 rather than
    something invented by the transport."""
    gs, server, agent, dev, clk = _agent_with_joined_device()
    seen = []
    gs.data = lambda d, v, a, gesture_time=None: seen.append(gesture_time)
    server.deliver("c1", "/game/tilt", "sf", [dev, 12.0])
    agent.poll()
    assert seen == [0.0]


def test_room_audio_waits_for_its_moment_and_light_does_not():
    """One anchor, two releases. Light is fed immediately because its frame
    still has to cross the wire; audio waits until `at` because it reaches
    Arco from Control with no wire in between."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    now = [1000.0]
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=room_bridge,
                            horizon=0.060, clock=lambda: now[0])
    room_bridge.bind("sim-room-main", light=light, audio=audio)

    gs.on_light_cue("sim-room-main", 0xB0, 74, 100, 1000.05)
    assert light.fed == [(0xB0, 74, 100)]     # fed on arrival
    agent._render_room()
    assert audio.fed == []                    # not yet: at is 1000.05

    now[0] = 1000.06
    agent._render_room()
    assert audio.fed == [(0xB0, 74, 100)]


def test_room_frame_carries_a_time():
    """Room frames carried NO when at all before this: _render_room called
    leds_event with no timestamp, so they bypassed the device's queue and its
    clamp counter entirely while every per-device frame was scheduled."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    now = [1000.0]
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                            horizon=0.060, clock=lambda: now[0])
    server.bind_dev("sim-room-main", "c-room")

    gs.on_light_cue("sim-room-main", 0xB0, 74, 100, 1000.05)
    agent._render_room()

    leds = [m for d, m in server.sent if m["address"] == "/sim-room-main/leds"]
    assert leds
    assert leds[-1]["timestamp"] == pytest.approx(1000.05)


def test_a_room_audio_cue_already_past_clamps_and_counts():
    """The horizon being too small must be VISIBLE, not silent. This counter
    is what the separate horizon-measurement task consumes."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    audio = FakeRoomAudioSink()
    now = [1000.0]
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=room_bridge,
                            horizon=0.060, clock=lambda: now[0])
    room_bridge.bind("sim-room-main", light=FakeRoomLightSink(), audio=audio)

    gs.on_light_cue("sim-room-main", 0xB0, 74, 100, 999.0)   # already past
    agent._render_room()
    assert audio.fed == [(0xB0, 74, 100)]               # released anyway
    assert agent.clamped == 1


# --- Room fixture profile: the Room's session is built from its own
# RoomProfile (control/room_profile.py), not borrowed from the player
# Tuneshroom's shroom_capability(). See this task's brief. ------------------

def test_room_session_is_built_from_the_whole_concatenated_profile():
    """The session renders every fixture's pixels, bound or not -- only the
    SEND is scoped to bound fixtures. This is what lets a spatial instrument
    (e.g. luxaeterna's rainbow) paint one gradient across every fixture from
    one declaration."""
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    assert agent._room_profile == TEST_PROFILE
    assert agent._room_light.session.cap.pixel_count == 90
    assert agent._room_light.session.cap.surface_id == "room_test"


def test_player_devices_still_get_the_shroom_capability():
    """Players remain Tuneshrooms. Only the Room changed."""
    gs, server, agent, dev, clk = _agent_with_joined_device("ie1")
    assert agent.bridges["ie1"].session.cap.pixel_count == 12


def test_room_frame_is_the_bound_fixtures_own_width_not_the_whole_profile():
    gs = _room_ready_game_server()
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-room")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    frames = [m for dev, m in server.sent if m["address"] == "/sim-room-main/leds"]
    assert frames, "the Room emitted no frame for its bound fixture"
    main = next(f for f in TEST_PROFILE.fixtures if f.name == "main")
    assert len(frames[-1]["args"][0]) == main.pixel_count * 3
    assert len(frames[-1]["args"][0]) == 180


def test_two_bound_fixtures_each_receive_their_own_slice_of_one_render():
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    main_frames = [m for d, m in server.sent if m["address"] == "/sim-room-main/leds"]
    accent_frames = [m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"]
    assert main_frames and accent_frames
    assert len(main_frames[-1]["args"][0]) == 180     # main: 60 px
    assert len(accent_frames[-1]["args"][0]) == 90    # accent: 30 px
    # same presentation time for both slices of the same render
    assert main_frames[-1]["timestamp"] == accent_frames[-1]["timestamp"]


def test_an_unbound_second_fixture_does_not_block_the_first_from_rendering():
    """Partial binding renders -- design spec section 6. One unplugged
    fixture must not black out the rest of the room mid-show."""
    gs = _room_ready_game_server(bound={"main": "sim-room-main"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    main_frames = [m for d, m in server.sent if m["address"] == "/sim-room-main/leds"]
    accent_frames = [m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"]
    assert main_frames
    assert accent_frames == []   # never bound, never sent to


def test_a_room_cue_feeds_the_shared_session_once_and_reaches_both_fixtures():
    """Integration-level proof of control/engine.py's _collapse_room_fanout:
    a single ROOM-sentinel cue must not double-apply, and its rendered
    consequence must reach every bound fixture's own slice."""
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())
    agent.poll()   # settle the initial render

    gs.on_light_cue("sim-room-main", 0xB0, 74, 100)   # canonical dev, single cue
    agent.poll()

    main_frames = [m for d, m in server.sent if m["address"] == "/sim-room-main/leds"]
    accent_frames = [m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"]
    assert main_frames and accent_frames   # both slices of the one render went out


def test_an_unchanged_fixture_slice_is_not_resent_after_settling():
    """_last_frames is keyed per fixture dev, so once the shared session's
    output is stable, neither fixture keeps resending on every tick.

    TestBit's Room manifest targets "primary" (the whole concatenated
    surface) with one instrument, so there is no way to change only ONE
    fixture's pixels through its real declaration -- proving per-fixture
    selectivity that way is not available at this integration level. What
    IS provable, and is the same underlying _last_frames mechanism: with
    no NEW cue and no elapsed time (no breath reaching the Room either --
    TestBit's Room role declares no cc:11 lane, unlike player), a second
    render produces byte-identical output to the first, and NEITHER
    fixture resends it -- which could only hold if each fixture's slice is
    compared against its OWN last-sent bytes rather than some shared or
    always-different state.

    Uses a fake, manually-advanced clock (same idiom as
    test_room_dev_cue_routes_to_room_bridge_not_normal_bridges above) so
    the settling loop's Smooth-driven params (hue/level glide) actually
    converge before the counts being compared are captured -- with the
    default wall clock, successive polls advance real time by
    microseconds, nowhere near enough to settle, and this assertion would
    be flaky by construction without it.

    The final "before" vs "after" comparison deliberately does NOT advance
    the clock further, unlike the settling loop above it. TestBit's Room
    declares rainbow (see bits/test_bit.py), which -- unlike aurora's
    settle-to-a-constant behavior -- keeps its hue scrolling forever from
    ctx.time even with no new cue, by design (that animation is the whole
    point of the instrument). So "render again after real time passes,
    expect no resend" is no longer a universally true property once the
    Room's instrument can be a perpetually-animating one; "render again at
    the SAME instant, expect no resend" still is, for any instrument,
    because RenderContext.time is derived from the injected clock
    (luxaeterna's LightSession.render_into: t = now - self._start), so a
    frozen clock yields byte-identical output regardless of which
    instrument computed it. This isolates the property actually under
    test (_last_frames' own comparison logic) from whichever instrument
    the Room happens to declare."""
    clk = _Clock()
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge(), clock=clk)

    for _ in range(5):
        clk.advance(2.0)
        agent.poll()   # let hue/level glide converge

    def counts():
        main = len([m for d, m in server.sent if m["address"] == "/sim-room-main/leds"])
        accent = len([m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"])
        return main, accent

    before = counts()
    agent.poll()   # same clock instant, no new cue: output must be identical
    after = counts()

    assert after == before   # neither fixture resent an unchanged frame


def test_setup_room_builds_the_session_even_with_nothing_bound_yet():
    """A late admin tap must not need a session rebuild -- the session
    spans the whole profile regardless of binding state (see this task's
    changed _setup_room gate)."""
    gs = _room_ready_game_server(bound={})
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    assert agent._room_light is not None
    assert agent._room_profile is not None


def test_an_explicit_room_profile_overrides_the_resolved_one():
    from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
    profile = RoomProfile(surface_id="custom", fixtures=(
        RoomFixture(name="only", color_order="GRB",
                   blocks=(RoomBlock("only", 0, 24),),
                   zones=(RoomZone("all", 0, 24),)),))
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_profile=profile)

    assert agent._room_light.session.cap.pixel_count == 24


def test_no_room_configured_leaves_the_profile_unset():
    """A GameServer built the pre-Room way must keep working."""
    gs = GameServer({"TestBit": TestBit})
    agent = DeviceLinkAgent(gs, FakeServer())
    assert agent._room_profile is None
    assert agent._room_light is None


def test_rewire_room_builds_the_session_after_a_no_room_boot():
    """harness/terrarium_boot.py's NO_ROOM boot constructs this agent
    BEFORE any Room exists (room_bridge=None): _setup_room() at __init__
    time is a no-op then, same as test_no_room_configured_leaves_the_profile
    _unset above. rewire_room() is what a later Console `load_room` drives
    (via the harness's own Terrarium observer) -- re-running Room setup now
    that gs.room/gs.bit both actually exist, exactly as if boot() had
    always known about them."""
    gs = GameServer({"TestBit": TestBit})
    agent = DeviceLinkAgent(gs, FakeServer())
    assert agent._room_light is None
    assert agent.room_bridge is None

    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.room.bound["main"] = "sim-room-main"
    gs.load_bit("TestBit")
    room_bridge = RoomBridge()

    agent.rewire_room(room_bridge)

    assert agent.room_bridge is room_bridge
    assert agent._room_light is not None
    assert agent._room_profile is not None
    assert room_bridge.dev == "sim-room-main"


def test_unwire_room_clears_a_previously_wired_session():
    """The NO_ROOM-entry counterpart: a Console `unload_room` must not
    leave a stale session/bridge/profile behind for the next NO_ROOM wait
    (or a subsequent, differently-shaped Room) to render against."""
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())
    assert agent._room_light is not None

    agent.unwire_room()

    assert agent._room_light is None
    assert agent.room_bridge is None
    assert agent._room_profile is None


# --- Room frame relay to the Console: an optional, guarded, best-effort
# sink (see this task's brief and boundary rule 2). --------------------------

def test_room_frames_reach_the_sink():
    gs = _room_ready_game_server()
    seen = []
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            on_room_frame=lambda dev, frame: seen.append((dev, frame)))

    for _ in range(3):
        agent.poll()

    assert seen, "no room frame reached the sink"
    assert seen[0][0] == "sim-room-main"
    assert len(seen[0][1]) == 180


def test_a_raising_room_frame_sink_does_not_stop_the_leds_going_out():
    """Boundary rule 2, and the same guard the other two transport sinks
    already carry: a failing console must not wedge the Room."""
    gs = _room_ready_game_server()
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-room")

    def boom(dev, frame):
        raise RuntimeError("console exploded")

    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge(),
                            on_room_frame=boom)

    for _ in range(3):
        agent.poll()

    assert [m for _, m in server.sent if m["address"] == "/sim-room-main/leds"]


def test_no_sink_is_the_default_and_changes_nothing():
    gs = _room_ready_game_server()
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-room")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    assert [m for _, m in server.sent if m["address"] == "/sim-room-main/leds"]


# --- UNLOADING drops pending timed cues: a trigger's cue script can
# schedule a step past its Bit's own completion, and the Room's bridge
# persists across a Bit lifecycle by design, so without this the Room keeps
# gliding after the drone has stopped and the Bit is gone. ------------------

def test_pending_script_cues_are_dropped_at_unloading():
    """A step scheduled past its Bit's completion must not still feed the Room
    after UNLOADING. Player devices are already safe by accident, because
    _feed_light_now returns early once _finish_release has cleared the bridge,
    but the Room's bridge persists across a Bit lifecycle by design, so the
    Room is the case that needs saying."""
    gs, agent, bridge = _agent_with_bound_room()   # existing helper, line 438
    now = agent._clock()
    # Far enough out that the room queue holds it AND the light-session feed
    # is deferred too (feed_at = when - horizon is still in the future).
    agent._on_light_cue("sim-room-main", 0xB0, 74, 40, when=now + 5.0)
    assert agent._room_cues.pending() == 1
    assert agent._light_cues.pending() == 1

    agent.on_state_change(State.RUNNING, State.UNLOADING)

    assert agent._room_cues.pending() == 0
    assert agent._light_cues.pending() == 0


def test_unloading_still_stops_the_room_drone():
    """The queue clear must not displace what this branch already did.

    No manual on_grant call here: _room_ready_game_server() binds the Room
    before the agent is constructed, so DeviceLinkAgent._setup_room() already
    grants "sim-room-main" during __init__ (see devicelink/agent.py's
    self._room_audio.on_grant call). Granting it again would acquire a
    second voice and desync pool.acquired[0] from the one start_drone/
    stop_drone actually operate on."""
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)
    agent.on_state_change(State.SETUP, State.RUNNING)
    agent.on_state_change(State.RUNNING, State.UNLOADING)
    voice = pool.acquired[0]
    assert voice.sent[-1][0] in ("note_off", "all_off")


def test_unloading_clears_queues_even_with_no_room_audio_injected():
    """The clear sits before the room-audio early return on purpose."""
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())
    agent._light_cues.push(agent._clock() + 5.0, ("ie1", 0xB0, 74, 1, 0.0),
                           now=agent._clock())
    agent.on_state_change(State.RUNNING, State.UNLOADING)
    assert agent._light_cues.pending() == 0


def _room_msgs(server, dev="ie1"):
    return [m for d, m in server.sent if d == dev and m["address"] == f"/{dev}/room"]


def test_hello_is_answered_with_an_idle_room_snapshot(rig):
    gs, server, agent = rig
    _hello(server, agent)
    rooms = _room_msgs(server)
    assert len(rooms) == 1
    blob = rooms[0]["args"][0]
    assert blob == {"state": "IDLE", "bit": None, "version": None, "nodes": []}


def test_load_bit_broadcasts_setup_with_tappable_nodes_only(rig):
    gs, server, agent = rig
    _hello(server, agent, client="c1", dev="ie1")
    _hello(server, agent, client="c2", dev="ie2")
    gs.load_bit("test_bit")
    for dev in ("ie1", "ie2"):
        blob = _room_msgs(server, dev)[-1]["args"][0]
        assert blob["state"] == "SETUP"
        assert blob["bit"] == "test_bit"
        assert blob["version"] == TestBit.version
        ids = [n["id"] for n in blob["nodes"]]
        assert ids == ["TEST_PLAYER_NODE", "TEST_JAM_NODE"]
        player, jam = blob["nodes"]
        assert player == {"id": "TEST_PLAYER_NODE", "roles": ["player"],
                          "scored": True, "count": 0,
                          "capacity": gs.bit.role_table.roles["player"].capacity}
        assert jam["scored"] is False
        assert jam["count"] == 0


def test_join_broadcasts_updated_count(rig):
    gs, server, agent = rig
    _hello(server, agent)
    gs.load_bit("test_bit")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    blob = _room_msgs(server)[-1]["args"][0]
    player = next(n for n in blob["nodes"] if n["id"] == "TEST_PLAYER_NODE")
    assert player["count"] == 1


def test_run_broadcasts_running(rig):
    gs, server, agent = rig
    _hello(server, agent)
    gs.load_bit("test_bit")
    gs.run()
    assert _room_msgs(server)[-1]["args"][0]["state"] == "RUNNING"


def test_room_broadcast_skips_unbound_devices_without_raising(rig):
    gs, server, agent = rig
    _hello(server, agent)
    server.drop_dev("ie1")
    gs.load_bit("test_bit")   # must not raise; FakeServer.send is a no-op for unbound
    assert _room_msgs(server)[-1]["args"][0]["state"] == "IDLE"


# --- Stale-device reaping and drop_dev cleanup: DeviceLinkAgent.poll() now
# calls GameServer.reap_stale() every tick, and _handle() touches last_seen
# on every inbound message (not just hello), so a device that stays quiet
# past stale_timeout is reaped even though it never sends another /game/hello.
# See docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md.

def test_handle_touches_last_seen_on_every_inbound_message():
    gs, server, agent, dev, clk = _agent_with_joined_device()
    clk.advance(3.0)
    server.deliver("c1", "/game/tilt", "sf", [dev, 10.0])
    agent.poll()
    assert gs.devices.get(dev).last_seen == clk.t


def test_poll_reaps_a_device_silent_past_stale_timeout():
    # gs shares clk with the agent -- see _agent_with_joined_device's
    # docstring for why an unsynced GameServer clock breaks reap_stale.
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert "ie1" in agent.bridges

    clk.advance(11.0)
    agent.poll()   # no inbound traffic this tick -- ie1 has gone silent

    assert gs.devices.known("ie1") is False
    counts = dict((n, c) for n, c, _ in gs.registration.counts())
    assert counts["player"] == 0
    # Reaped through the SAME graceful path a normal release takes
    # (GameServer.reap_stale fires the on_release sink, exactly like
    # _unload does), not some separate hard-drop code path: the closing
    # fade started _and_ finished, sending /ie1/release and forgetting the
    # transport's connection mapping. It resolves within this one poll()
    # rather than staying mid-fade (contrast test_finish_release_calls_
    # drop_dev's fine-grained clock schedule) because the single 11s clock
    # jump is itself the render's dt on the next frame -- far past the
    # ~0.6s sys:closing signature's own duration -- so the same call that
    # starts CLOSING also renders it to completion (see
    # luxaeterna's synth/director.py StatusDirector.frame()).
    assert "ie1" not in agent._closing
    assert "ie1" not in agent.bridges
    assert server.addressed("/ie1/release")
    assert "ie1" not in server._devs


def test_a_fresh_heartbeat_prevents_reaping():
    # gs shares clk with the agent -- see _agent_with_joined_device's
    # docstring for why an unsynced GameServer clock breaks reap_stale.
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    clk.advance(8.0)
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()   # heartbeat lands before the 10s timeout

    clk.advance(8.0)   # 16s since join, but only 8s since the heartbeat
    agent.poll()

    assert gs.devices.known("ie1") is True
    assert "ie1" in agent.bridges


def test_finish_release_calls_drop_dev():
    gs, server, agent, dev, clk = _agent_with_joined_device()
    assert dev in server._devs
    gs.abort()          # -> _unload -> on_release -> fade -> _finish_release
    # drive the closing fade to completion
    for _ in range(250):
        agent.poll()
        clk.advance(1.0 / 44.0)
    assert dev not in server._devs


def test_a_hello_mid_fade_survives_finish_release():
    """Final-review Finding 2 regression. A device that reconnects
    (hello-only, no rejoin -- exactly how the Room simulator's heartbeat
    resend behaves) while a PRIOR release's closing fade is still in
    flight must not have that FRESH connection severed once the STALE
    fade finishes. _on_hello rebinds server._devs[dev] to the new client
    but never touches self._closing (unlike _on_join, which already pops
    self._closing on a rejoin); left unguarded, _finish_release's
    unconditional drop_dev(dev) would drop the connection that just
    proved dev is alive, not the stale one."""
    gs, server, agent, dev, clk = _agent_with_joined_device()
    old_client = server._devs[dev]
    gs.abort()   # -> _unload -> on_release -> fade starts, self._closing[dev] = 0

    # A fresh connection re-hellos mid-fade -- never rejoins. Queued here,
    # BEFORE the fade is driven at all: in this fixture the very next
    # poll() is what both delivers this hello AND can finish the stale
    # fade in the same call, which is exactly the race Finding 2 describes
    # -- so the assertion below must hold no matter how many poll()s it
    # actually takes.
    new_client = "c2"
    assert new_client != old_client
    server.arrive(new_client)
    server.deliver(new_client, "/game/hello", "sss", [dev, "sim", "1"])

    # Drive the closing fade to completion -- same bounded tick-by-tick
    # pattern as test_finish_release_calls_drop_dev above.
    for _ in range(250):
        agent.poll()
        clk.advance(1.0 / 44.0)

    # The fade itself still finished and cleaned up the OLD session state.
    assert dev not in agent.bridges
    assert dev not in agent._closing
    # But the FRESH connection must survive _finish_release's drop_dev.
    assert dev in server._devs
    assert server._devs[dev] == new_client


def test_on_release_with_no_bridge_calls_drop_dev():
    """Mirrors test_failing_on_grant_sends_error_not_role... -- a device
    whose on_grant failed never got a bridge, so _on_release takes the
    early-return branch, not the fade. That branch must still forget the
    transport's connection mapping."""
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("test_bit")
    _hello(server, agent, client="c1", dev="ie1")
    assert "ie1" in server._devs

    # Simulate a grant with no bridge ever created, exactly what
    # devicelink/agent.py's _on_join does on a failing on_grant: the
    # engine-level assignment exists, but self.bridges never got an entry.
    gs.join("ie1", "TEST_PLAYER_NODE")
    # A canvas url can land for this dev before its release ever runs (the
    # protocol makes no ordering guarantee here); the no-bridge branch must
    # clear it just like _finish_release does for the faded path, or it
    # leaks in agent._canvas_urls forever.
    server.deliver("c1", "/game/canvas", "ss", ["ie1", "http://h:1/"])
    agent.poll()
    assert agent.canvas_urls() == {"ie1": "http://h:1/"}

    agent._on_release("ie1")

    assert "ie1" not in server._devs
    assert agent.canvas_urls() == {}
