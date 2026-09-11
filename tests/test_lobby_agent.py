"""DeviceLinkAgent drives the lobby (spec 4, 5) and routes start (spec 3).
Offline: FakeFixtureSession for light, _FakeAudioBridge for sound."""
import queue
from dataclasses import replace
from pathlib import Path

from bits.test.test_bit import TestBit
from control.bit_registry import BitRegistry
from control.breath import BREATH_CC
from control.engine import GameServer
from control.lobby import (GREEN, HUE_CC, LOBBY_DRONE_KEY, LOBBY_PROGRAM, RED,
                           StartRequest, TERRARIUM_ADMIN, WHITE)
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


def _rig(monkeypatch, config=None, admin_devices=()):
    clk = _Clock(100.0)
    gs = GameServer({"TestBit": TestBit}, clock=clk, admin_devices=admin_devices)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.room.bound["main"] = "sim-main"
    gs.room.bound["accent"] = "sim-accent"
    audio = _FakeAudioBridge()
    sessions = _fake_sessions(monkeypatch)
    server = FakeServer()
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
    # accept: one green flash on both fixtures
    agent.poll()
    assert agent._overrides["sim-main"][0] == GREEN
    assert agent._overrides["sim-accent"][0] == GREEN


def test_hello_invites_with_two_white_flashes_and_double_tap_joins(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    events = []
    class Obs:
        def on_lobby_event(self, event, dev):
            events.append((event, dev))
    gs.add_observer(Obs())
    _hello(server, agent, "c1", "ie1")
    assert agent._overrides["ie1"][0] == WHITE
    assert events == [("invite", "ie1")]
    _poll(agent, clk, 0.3)
    assert "ie1" not in agent._overrides                       # first flash over
    _poll(agent, clk, 0.15)
    assert agent._overrides["ie1"][0] == WHITE                 # second flash
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 1], timestamp=clk.t)
    agent.poll()
    assert "ie1" not in gs.registration.assignments
    clk.advance(0.5)
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 1], timestamp=clk.t)
    agent.poll()
    assert gs.registration.assignments["ie1"][1] == "player"
    assert ("handshake", "ie1") in events
    assert any(m["address"] == "/ie1/role" for (_d, m) in server.sent)


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


def test_tap_from_an_uninvited_unjoined_device_is_still_refused(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    gs.request_start(None, TERRARIUM_ADMIN, "console")         # RUNNING: no lobby
    _hello(server, agent, "c1", "ie1")
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 2])
    agent.poll()
    assert "ie1" not in gs.registration.assignments
    assert any(m["address"] == "/ie1/error" for (_d, m) in server.sent)


def test_scored_join_runs_the_ceremony(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
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
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert gs.lobby_state() == "FULL"
    assert ("main", 0x80, LOBBY_DRONE_KEY, 0) in audio.fed
    assert (0xB0, HUE_CC, 42) in sessions[f"{TEST_PROFILE.surface_id}_main"].fed
    _hello(server, agent, "c2", "ie2")
    assert "ie2" not in agent._overrides                       # no invite while FULL


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
    assert agent._overrides["sim-main"][0] == RED
    _poll(agent, clk, 0.3)
    assert "sim-main" not in agent._overrides
    _poll(agent, clk, 0.25)
    assert agent._overrides["sim-main"][0] == RED


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
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 2], timestamp=clk.t)
    agent.poll()
    assert gs.registration.assignments["ie1"][0] == "TEST_PLAYER_NODE"


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
