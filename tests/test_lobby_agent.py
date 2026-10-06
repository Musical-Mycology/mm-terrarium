"""DeviceLinkAgent drives the lobby (spec 4, 5) and routes start (spec 3).
Offline: FakeFixtureSession for light, _FakeAudioBridge for sound."""
import queue
from dataclasses import replace
from pathlib import Path

from bits.test.test_bit import TestBit
from control.bit_registry import BitRegistry
from control.breath import BREATH_CC
from control.engine import GameServer
from control.lobby import (FEEDBACK_REFUSED, GREEN, HUE_CC, LOBBY_DRONE_KEY,
                           LOBBY_PROGRAM, RED, StartRequest, TERRARIUM_ADMIN,
                           WHITE)
from control.cues import MuteCue, fixture_dev
from control.rooms import Room
from control.state import State
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import (FakeServer, TEST_PROFILE, _Clock,
                                         _FakeAudioBridge, _fake_sessions)


def _admin_cfg(**start):
    registry = BitRegistry.scan([Path("bits")])
    cfg = registry.resolve_config("TestBit")
    fields = {"when": "admin", "key": "k", "min_scored": 1}
    fields.update(start)
    return replace(cfg, start=replace(cfg.start, **fields))


def _rig(monkeypatch, config=None, admin_devices=(), *, server=None,
         clk=None):
    """A Room-loaded TestBit in SETUP with both fixtures bound. `server`
    and `clk` default to a FakeServer and a hand-advanced clock; the
    transport tests in tests/test_devicelink_agent.py pass an
    O2LiteTransport over FakeO2Lite and that fake's own clock."""
    clk = clk if clk is not None else _Clock(100.0)
    gs = GameServer({"TestBit": TestBit}, clock=clk, admin_devices=admin_devices)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.room.bound["main"] = "sim-main"
    gs.room.bound["accent"] = "sim-accent"
    audio = _FakeAudioBridge()
    sessions = _fake_sessions(monkeypatch)
    server = server if server is not None else FakeServer()
    agent = DeviceLinkAgent(gs, server, room_audio=audio, clock=clk)
    gs.load_bit("TestBit", config=config)
    return gs, server, agent, audio, sessions, clk


def _poll(agent, clk, seconds, dt=1 / 44):
    for _ in range(int(seconds / dt)):
        clk.advance(dt)
        agent.poll()


def _hello(server, agent, client, dev):
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
    agent.poll()


def _handshake(server, agent, gs, client, dev, node=""):
    """The device's answer to /<dev>/handshake (spec 2026-10-01 section
    3.4). An empty node validates against the Bit's default scored node."""
    server.deliver(client, "/game/handshake", "sss", [dev, gs.round_id, node])
    agent.poll()


def _sent(server, address):
    return [m for (_d, m) in server.sent if m["address"] == address]


def test_setup_swaps_fixtures_to_the_lobby_manifest_and_sounds_the_pad(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    main = sessions[f"{TEST_PROFILE.surface_id}_main"]
    assert main.manifest.instruments[0].instrument == "aurora"
    assert {(l.source, l.dest) for l in main.manifest.instruments[0].lanes} == {
        ("cc:74", "hue"), ("cc:11", "level")}
    assert ("main", 0xC0, LOBBY_PROGRAM, 0) in audio.fed
    assert ("main", 0x90, LOBBY_DRONE_KEY, 80) in audio.fed
    _poll(agent, clk, 0.5)
    assert any(d1 == HUE_CC for (s, d1, d2) in main.fed)
    assert any(d1 == BREATH_CC for (s, d1, d2) in main.fed)
    assert any(cc == BREATH_CC for (name, cc, v) in audio.controls)


def test_lobby_is_off_when_the_bit_opts_out(monkeypatch):
    cfg = _admin_cfg()
    cfg = replace(cfg, lobby=replace(cfg.lobby, enabled=False))
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, cfg)
    assert ("main", 0x90, LOBBY_DRONE_KEY, 80) not in audio.fed
    _poll(agent, clk, 0.5)
    assert sessions[f"{TEST_PROFILE.surface_id}_main"].fed == []


def test_running_swaps_back_to_the_bits_room_declaration(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    main = sessions[f"{TEST_PROFILE.surface_id}_main"]
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING
    assert main.manifest.instruments[0].instrument == "rainbow"   # TestBit's ROOM
    assert ("main", 0x80, LOBBY_DRONE_KEY, 0) in audio.fed
    assert ("main", 0xC0, 89, 0) in audio.fed                    # TestBit's program restored
    # accept: the first of two green flashes on both fixtures
    agent.poll()
    assert agent._overrides[fixture_dev("main")][0] == GREEN
    assert agent._overrides[fixture_dev("accent")][0] == GREEN


def test_hello_invites_with_two_white_flashes_and_the_handshake_validates(monkeypatch):
    # The double tap is retired (spec 2026-10-01 section 5.5): the invite
    # carries /<dev>/handshake, the device's /game/handshake answers it, a
    # tap is plain gameplay, and the role itself arrives at start.
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    events = []
    class Obs:
        def on_lobby_event(self, event, dev):
            events.append((event, dev))
    gs.add_observer(Obs())
    _hello(server, agent, "c1", "ie1")
    assert agent._overrides["ie1"][0] == WHITE
    assert events == [("invite", "ie1")]
    assert [m["args"] for m in _sent(server, "/ie1/handshake")] == [[gs.round_id]]
    _poll(agent, clk, 0.3)
    assert "ie1" not in agent._overrides                       # first flash over
    _poll(agent, clk, 0.15)
    assert agent._overrides["ie1"][0] == WHITE                 # second flash
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 2], timestamp=clk.t)
    agent.poll()
    assert "ie1" not in gs.registration.validated
    assert "ie1" not in gs.registration.assignments
    _handshake(server, agent, gs, "c1", "ie1")
    assert gs.registration.validated["ie1"] == ("TEST_PLAYER_NODE", "player")
    assert ("handshake", "ie1") in events
    assert [m["args"] for m in _sent(server, "/ie1/validated")] == [
        [gs.round_id, "player"]]
    assert _sent(server, "/ie1/role") == []
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    agent.poll()
    assert gs.registration.assignments["ie1"][1] == "player"
    assert _sent(server, "/ie1/role")


def test_invites_repeat_the_handshake_until_validated(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 5.2)
    assert len(_sent(server, "/ie1/handshake")) == 2
    _handshake(server, agent, gs, "c1", "ie1")
    _poll(agent, clk, 6.0)
    assert len(_sent(server, "/ie1/handshake")) == 2           # no more invites


def test_no_invite_and_no_handshake_when_the_bit_has_no_scored_node(monkeypatch):
    """Nothing to validate means nothing to accept: neither the white
    invite nor /<dev>/handshake goes out (controller ruling, Task 6)."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    roles = gs.registration.role_table.roles
    roles["player"] = replace(roles["player"], scored=False)
    assert gs.default_scored_node() is None
    events = []
    class Obs:
        def on_lobby_event(self, event, dev):
            events.append((event, dev))
    gs.add_observer(Obs())
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 6.0)
    assert _sent(server, "/ie1/handshake") == []
    assert "ie1" not in agent._overrides
    assert events == []


def test_a_ceremony_validated_just_before_start_still_plays_out(monkeypatch):
    """Spec 2026-10-01 section 5.5: start tears the lobby down, but the
    queued bell and chime of a validation that just landed still play."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _handshake(server, agent, gs, "c1", "ie1")
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING and agent._lobby is None
    _poll(agent, clk, 2.0)
    assert audio.notes == [(14, 69, 100, 1.0)]
    plays = _sent(server, "/ie1/play")
    assert plays[-1]["args"] == ["chime", "key=69"]
    assert agent._draining_lobby is None                       # drained, dropped


def test_invite_frames_reach_an_unjoined_device_in_grb_then_go_black(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    leds = [bytes(m["args"][0]) for (_d, m) in server.sent if m["address"] == "/ie1/leds"]
    assert leds[-1] == bytes([255, 255, 255] * 12)
    _poll(agent, clk, 0.3)
    leds = [bytes(m["args"][0]) for (_d, m) in server.sent if m["address"] == "/ie1/leds"]
    assert leds[-1] == bytes(36)                              # black once the flash expires
    # a green override on a GRB surface lands as G=255,R=0,B=0
    agent._on_solid_cue("ie1", GREEN, 1.0, 1.0, clk.t)
    agent.poll()
    leds = [bytes(m["args"][0]) for (_d, m) in server.sent if m["address"] == "/ie1/leds"]
    assert leds[-1][:3] == bytes([255, 0, 0])


def test_tap_from_an_invited_unjoined_device_is_gameplay_and_refused(monkeypatch):
    # Was "an uninvited device in RUNNING": a RUNNING hello now gets a jam
    # role at once (spec 2026-10-01 section 3.6), so the role-less tapper
    # is the invited SETUP device, and its tap is gameplay (section 5.5).
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    assert agent._lobby.is_invited("ie1")
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 2])
    agent.poll()
    assert "ie1" not in gs.registration.assignments
    assert "ie1" not in gs.registration.validated
    assert any(m["address"] == "/ie1/error" for (_d, m) in server.sent)


def test_validation_runs_the_ceremony(monkeypatch):
    # The ceremony celebrates a validation (spec 2026-10-01 section 3.4).
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _handshake(server, agent, gs, "c1", "ie1", "TEST_PLAYER_NODE")
    assert agent._overrides["ie1"][0] == GREEN
    _poll(agent, clk, 1.0)
    assert audio.notes == [(14, 69, 100, 1.0)]
    _poll(agent, clk, 1.0)
    plays = [m for (_d, m) in server.sent if m["address"] == "/ie1/play"]
    assert plays[-1]["args"] == ["chime", "key=69"]


def test_full_lobby_pins_green_and_stops_the_drone(monkeypatch):
    cfg = _admin_cfg()
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, cfg)
    # cap TestBit's player at 1 for this test
    gs.registration.role_table.roles["player"].capacity = 1
    _hello(server, agent, "c1", "ie1")
    _handshake(server, agent, gs, "c1", "ie1", "TEST_PLAYER_NODE")
    assert gs.lobby_state() == "FULL"
    assert ("main", 0x80, LOBBY_DRONE_KEY, 0) in audio.fed
    assert (0xB0, HUE_CC, 42) in sessions[f"{TEST_PROFILE.surface_id}_main"].fed
    _hello(server, agent, "c2", "ie2")
    assert "ie2" not in agent._overrides                       # no invite while FULL
    assert _sent(server, "/ie2/handshake") == []


def test_start_verb_from_a_device_routes_to_request_start(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg(min_scored=0))
    _hello(server, agent, "c1", "gem-1")
    server.deliver("c1", "/game/start", "ss", ["gem-1", "wrong"])
    agent.poll()
    assert gs.state is State.SETUP
    assert any(m["address"] == "/gem-1/error" and m["args"] == ["start", "bad key"]
               for (_d, m) in server.sent)
    server.deliver("c1", "/game/start", "ss", ["gem-1", "k"])
    agent.poll()
    assert gs.state is State.RUNNING


def test_minimum_not_met_flashes_fixtures_red_twice(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie5")
    server.deliver("c1", "/game/start", "ss", ["ie5", "k"])
    agent.poll()
    assert gs.state is State.SETUP
    assert agent._overrides[fixture_dev("main")][0] == RED
    _poll(agent, clk, 0.3)
    assert fixture_dev("main") not in agent._overrides
    _poll(agent, clk, 0.25)
    assert agent._overrides[fixture_dev("main")][0] == RED


def test_web_start_queue_is_drained_on_poll(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg(min_scored=0))
    q = queue.Queue()
    agent.start_requests = q
    q.put(StartRequest("k", TERRARIUM_ADMIN, "web:terrarium"))
    agent.poll()
    assert gs.state is State.RUNNING


def test_hello_as_terrarium_is_refused(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    server.arrive("c9")
    server.deliver("c9", "/game/hello", "sss", [TERRARIUM_ADMIN, "sim", "1"])
    agent.poll()
    assert gs.devices.get(TERRARIUM_ADMIN) is None


def test_start_claiming_the_terrarium_identity_never_reaches_the_engine(monkeypatch):
    """The reserved id is refused at DISPATCH, not at hello: nothing on this
    wire obliges a device to hello first, so a bare /game/start claiming
    `terrarium` would otherwise be an unconditional keyless start."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    records = []

    class Obs:
        def on_start_requested(self, record):
            records.append(record)

    gs.add_observer(Obs())
    server.arrive("c9")                                    # never hello'd
    server.deliver("c9", "/game/start", "ss", [TERRARIUM_ADMIN, ""])
    agent.poll()
    assert gs.state is State.SETUP
    assert records == []
    assert gs.devices.get(TERRARIUM_ADMIN) is None


def test_handshake_ignores_a_default_join_role_that_is_not_scored(monkeypatch):
    """default_join_role is the launcher's hint and a Bit may point it at a
    jam role; a handshake still lands on a scored node."""
    cfg = _admin_cfg()
    cfg = replace(cfg, launch=replace(cfg.launch, default_join_role="jammer"))
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, cfg)
    _hello(server, agent, "c1", "ie1")
    _handshake(server, agent, gs, "c1", "ie1")                 # no node named
    assert gs.registration.validated["ie1"][0] == "TEST_PLAYER_NODE"


def test_accept_flash_is_gated_on_the_lobby_being_enabled(monkeypatch):
    """[lobby] enabled = false means no room reaction at all, including
    the green accept flash. The runtime is already gone on accept, so the
    gate has to be the config."""
    cfg = _admin_cfg()
    cfg = replace(cfg, lobby=replace(cfg.lobby, enabled=False))
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, cfg)
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING
    agent.poll()
    assert agent._overrides == {}


import threading

from control.prepare import PrepareReply, PrepareRequest


class _Authority:
    def __init__(self, decision=None, raises=None):
        self.decision = decision
        self.raises = raises
        self.seen = []

    def request(self, req):
        self.seen.append(req)
        if self.raises is not None:
            raise self.raises
        return self.decision


def _prepare_req():
    return PrepareRequest("k", "TestBit", "gem-1", "web:gem-1",
                          PrepareReply(threading.Event()))


def test_prepare_queue_is_drained_on_poll_and_the_reply_is_filled(monkeypatch):
    from control.prepare import PrepareDecision
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    auth = _Authority(PrepareDecision(False, "busy", "none", True))
    agent.prepare_requests = queue.Queue()
    agent.prepare_authority = auth
    req = _prepare_req()
    agent.prepare_requests.put(req)
    agent.poll()
    assert auth.seen == [req]
    assert req.reply.done.is_set()
    assert (req.reply.accepted, req.reply.reason, req.reply.visible) == (False, "busy", True)


def test_prepare_without_an_authority_answers_not_wired(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    agent.prepare_requests = queue.Queue()
    req = _prepare_req()
    agent.prepare_requests.put(req)
    agent.poll()
    assert req.reply.done.is_set()
    assert (req.reply.accepted, req.reply.reason, req.reply.visible) == (
        False, "prepare is not wired", True)


def test_prepare_raising_authority_answers_failed_and_keeps_polling(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    agent.prepare_requests = queue.Queue()
    agent.prepare_authority = _Authority(raises=RuntimeError("boom"))
    req = _prepare_req()
    agent.prepare_requests.put(req)
    agent.poll()
    assert req.reply.done.is_set()
    assert (req.reply.accepted, req.reply.reason, req.reply.visible) == (
        False, "prepare failed", True)
    agent.poll()      # nothing left, no raise


def test_start_feedback_flash_leaves_a_muted_fixture_dark(monkeypatch):
    """_flash_fixtures (start feedback) goes through the set_override sink;
    a muted fixture must keep its latched blackout, an unmuted one flashes."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    server.bind_dev("sim-main", object())
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=clk.t)

    agent._lobby.feedback(FEEDBACK_REFUSED)
    saw_accent_flash = False
    for _ in range(int(2.0 / (1 / 44))):
        clk.advance(1 / 44)
        agent.poll()
        assert agent._overrides[fixture_dev("main")] == ((0, 0, 0), 0.0, None)
        entry = agent._overrides.get(fixture_dev("accent"))
        saw_accent_flash |= entry is not None and entry[0] == RED
    assert saw_accent_flash
    main_leds = [m["args"][0] for (d, m) in server.sent
                 if d == "sim-main" and m["address"] == "/sim-main/leds"]
    assert main_leds
    assert all(bytes(f) == bytes(len(f)) for f in main_leds)


def test_an_abort_during_the_ceremony_drops_its_queued_cues(monkeypatch):
    """A ceremony draining across start belongs to that Bit: an abort
    inside the ceremony span must not play its bell or chime afterwards."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _handshake(server, agent, gs, "c1", "ie1")
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert agent._draining_lobby is not None
    _poll(agent, clk, 0.5)
    gs.abort()
    assert agent._draining_lobby is None
    _poll(agent, clk, 2.0)
    assert audio.notes == []
    assert _sent(server, "/ie1/play") == []


def test_validating_mid_invite_cancels_the_queued_second_white_flash(monkeypatch):
    """The invite's second white flash is queued when the first goes out;
    a device that validates between them must see no white override after
    its /validated (it would read as a second invite), only the ceremony's
    green flashes."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    assert agent._overrides["ie1"][0] == WHITE
    _poll(agent, clk, 0.3)                                     # between flashes
    _handshake(server, agent, gs, "c1", "ie1")
    assert _sent(server, "/ie1/validated")
    seen = []
    for _ in range(int(2.0 * 44)):
        clk.advance(1 / 44)
        agent.poll()
        if "ie1" in agent._overrides:
            seen.append(agent._overrides["ie1"][0])
    assert WHITE not in seen
    assert GREEN in seen


def test_an_accepted_start_flashes_every_fixture_green_twice(monkeypatch):
    """Lexicon *Start accepted*: green x2 on every bound Room fixture, on
    the agent's own flash path because RUNNING has torn the lobby down."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING
    episodes = {"main": 0, "accent": 0}
    was_green = {"main": False, "accent": False}
    for _ in range(int(1.5 * 44)):
        agent.poll()
        for name in episodes:
            entry = agent._overrides.get(fixture_dev(name))
            green = entry is not None and entry[0] == GREEN
            episodes[name] += green and not was_green[name]
            was_green[name] = green
        clk.advance(1 / 44)
    assert episodes == {"main": 2, "accent": 2}


def _leds(server, dev):
    return [bytes(m["args"][0]) for (_d, m) in server.sent
            if m["address"] == f"/{dev}/leds"]


def _dim(frame, pixel):
    """True if every pixel of `frame` is `pixel` scaled to one level in
    (0, 255): a pulse frame, not a flash and not black."""
    peak = max(pixel)
    level = frame[pixel.index(peak)]
    want = bytes(round(c * level / peak) for c in pixel) * 12
    return 0 < level < 255 and frame == want


def test_an_invited_device_pulses_white_between_invite_flashes(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 2.0)
    assert _dim(_leds(server, "ie1")[-1], (255, 255, 255))


def test_a_validated_device_pulses_green_until_its_role(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _handshake(server, agent, gs, "c1", "ie1")
    _poll(agent, clk, 2.5)
    assert _dim(_leds(server, "ie1")[-1], (255, 0, 0))   # GRB green
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    agent.poll()
    assert agent._bases == {}
    assert _sent(server, "/ie1/role")


def test_a_deny_flashes_the_device_red_twice(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 1.0)
    n = len(server.sent)
    _handshake(server, agent, gs, "c1", "ie1", "NO_SUCH_NODE")
    _poll(agent, clk, 1.0)
    later = [m for (_d, m) in server.sent[n:]
             if m["address"] in ("/ie1/deny", "/ie1/leds")]
    assert later[0]["address"] == "/ie1/deny"
    red = bytes([0, 255, 0] * 12)                        # GRB red
    reds = [m for m in later if m["address"] == "/ie1/leds"
            and bytes(m["args"][0]) == red]
    assert len(reds) == 2


def test_a_muted_invited_device_stays_black(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    gs._dispatch_cues([MuteCue("ie1")], at=clk.t)
    n = len(_leds(server, "ie1"))
    _poll(agent, clk, 3.0)
    assert "ie1" in agent._bases                  # a pulse exists, suppressed
    after = _leds(server, "ie1")[n:]
    assert after, "the mute must send the clearing black frame"
    assert all(f == bytes(36) for f in after)


def test_a_full_lobby_clears_the_white_pulse_with_one_black_frame(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    gs.registration.role_table.roles["player"].capacity = 1
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 2.0)
    assert _dim(_leds(server, "ie1")[-1], (255, 255, 255))
    n = len(_leds(server, "ie1"))
    _hello(server, agent, "c2", "ie2")
    _handshake(server, agent, gs, "c2", "ie2")
    assert gs.lobby_state() == "FULL"
    _poll(agent, clk, 1.0)
    assert _leds(server, "ie1")[n:] == [bytes(36)]


def test_a_denied_fixture_bound_device_does_not_flash_the_fixture_red(monkeypatch):
    """A device bound to a Room fixture is denied `registration closed`; the
    deny flash is a player signal and must not reach the whole fixture."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch)
    _hello(server, agent, "c1", "sim-main")
    _handshake(server, agent, gs, "c1", "sim-main")
    assert _sent(server, "/sim-main/deny")
    for _ in range(44):
        clk.advance(1 / 44)
        agent.poll()
        entry = agent._overrides.get(fixture_dev("main"))
        assert entry is None or entry[0] != RED
