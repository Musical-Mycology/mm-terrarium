import json

from bits.test.test_bit import TestBit
from console.agent import ConsoleAgent
from control.bit_config import ManifestError, merge_overrides, parse_manifest
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from tests.instrument_fixtures import GENERIC_SURFACE
from control.rooms import Room, room_role_name

ROOM_PROFILE = RoomProfile(surface_id="room_test", fixtures=(
    RoomFixture(name="main", color_order="GRB",
               blocks=(RoomBlock("main", 0, 60),),
               zones=(RoomZone("left", 0, 20),
                     RoomZone("center", 20, 20),
                     RoomZone("right", 40, 20)), instrument=GENERIC_SURFACE),
    RoomFixture(name="accent", color_order="GRB",
               blocks=(RoomBlock("accent", 0, 30),),
               zones=(RoomZone("low", 0, 15),
                     RoomZone("high", 15, 15)), instrument=GENERIC_SURFACE),
))


def make_room(name="TEST"):
    return Room(name=name, profile=ROOM_PROFILE, node_id="ROOM_TEST_NODE")
from control.triggers import TriggerFired
from tests.test_engine import RoomCapableBit


class FakeBitRegistry:
    """Records what it was asked to resolve, and returns/raises canned
    results -- a stand-in for control.bit_registry.BitRegistry."""

    def __init__(self, config=None, raises=None):
        self._config = config
        self._raises = raises
        self.resolve_calls = []

    def resolve_config(self, name, overrides):
        self.resolve_calls.append((name, overrides))
        if self._raises is not None:
            raise self._raises
        return self._config

    def list_view(self, *, include_hidden=True):
        return [{"name": "TestBit"}]

    def errors_view(self):
        return [{"path": "x", "message": "bad"}]


class FakeConsoleServer:
    """In-process test double for console/server.py -- no threads, no socket.
    Tests push new clients + inbound messages and inspect sent/broadcast.
    """

    def __init__(self):
        self.broadcasts = []                # list[dict]
        self.sent = []                      # list[(client, dict)]
        self.dropped = []                   # list[client]
        self._new_clients = []
        self._inbound = []                  # list[(client, dict)]
        self._clients = set()
        self._raising = set()               # clients whose send() raises

    # --- tick-thread API consumed by ConsoleAgent ---
    def drain_new_clients(self):
        out, self._new_clients = self._new_clients, []
        return out

    def drain_inbound(self):
        out, self._inbound = self._inbound, []
        return out

    def send(self, client, msg):
        # Mirrors ConsoleServer.send: a client whose send raises is DROPPED,
        # not retried and not recorded as delivered. Boundary rule 5 -- this
        # fake must not be more permissive than the library it stands for.
        if client in self._raising:
            self._clients.discard(client)
            self.dropped.append(client)
            return
        self.sent.append((client, msg))

    def broadcast(self, msg):
        # Mirrors ConsoleServer.broadcast: a client whose send raises is
        # DROPPED, not retried and not allowed to break the loop. Modelling
        # this is boundary rule 5 -- a double must never be more permissive
        # than the library it stands for.
        self.broadcasts.append(msg)
        for client in list(self._clients):
            if client in self._raising:
                self._clients.discard(client)
                self.dropped.append(client)

    def fail_sends_to(self, client):
        self._clients.add(client)
        self._raising.add(client)

    # --- test helpers ---
    def connect(self, client):
        self._new_clients.append(client)
        self._clients.add(client)

    def deliver(self, client, msg):
        self._inbound.append((client, msg))


def _server_with_agent():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)
    return gs, srv, agent


def test_new_client_gets_a_snapshot_on_poll():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    agent.poll()
    assert len(srv.sent) == 1
    client, msg = srv.sent[0]
    assert client == "c1"
    assert msg["event"] == "snapshot"
    assert msg["state"] == "IDLE"
    assert msg["installed_bits"] == ["TestBit"]
    assert msg["loaded_bit"] is None


def test_snapshot_reflects_loaded_bit_and_registration():
    gs, srv, agent = _server_with_agent()
    gs.hello("ie1", "Shroom One", "1")
    gs.load_bit("TestBit")
    gs.join("ie1", "TEST_PLAYER_NODE")
    srv.connect("c1")
    agent.poll()
    snap = srv.sent[-1][1]
    assert snap["loaded_bit"] == "TestBit"
    assert {r["role"] for r in snap["roles"]} == {"player", "jammer"}
    assert any(d["dev"] == "ie1" and d["role"] == "player"
               for d in snap["devices"])
    assert snap["bit_status"]["run_duration"] == TestBit().status()["run_duration"]


def test_load_bit_command_drives_engine_and_broadcasts_state():
    gs, srv, agent = _server_with_agent()
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})
    agent.poll()
    assert gs.state.name == "SETUP"
    assert any(m.get("event") == "state_changed" and m["state"] == "SETUP"
               for m in srv.broadcasts)


def test_registration_change_is_broadcast():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    srv.broadcasts.clear()
    gs.join("ie9", "TEST_PLAYER_NODE")
    assert any(m.get("event") == "registration_changed" for m in srv.broadcasts)
    assert any(m.get("event") == "devices_changed" for m in srv.broadcasts)


def test_bad_command_sends_error_to_origin_only():
    gs, srv, agent = _server_with_agent()
    # run() from IDLE is an InvalidTransition
    srv.deliver("c1", {"command": "run"})
    agent.poll()
    errors = [m for (_, m) in srv.sent if m.get("event") == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "run"
    assert not any(m.get("event") == "error" for m in srv.broadcasts)


def test_unparseable_command_is_dropped_without_crashing():
    gs, srv, agent = _server_with_agent()
    srv.deliver("c1", {"command": "nonsense"})
    agent.poll()   # must not raise
    assert gs.state.name == "IDLE"


def test_bit_status_broadcast_only_on_change():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    gs.run()
    srv.broadcasts.clear()
    agent.poll()                       # first poll after run: status changed
    first = [m for m in srv.broadcasts if m.get("event") == "bit_status"]
    assert len(first) == 1
    srv.broadcasts.clear()
    agent.poll()                       # no elapsed change -> no new status
    assert not [m for m in srv.broadcasts if m.get("event") == "bit_status"]


def test_snapshot_loaded_bit_name_comes_from_game_server_bit_name():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    assert gs.bit_name == "TestBit"
    assert agent.snapshot()["loaded_bit"] == "TestBit"


def test_bit_completed_is_broadcast_on_unload():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    gs.run()
    srv.broadcasts.clear()
    gs.abort()                          # -> UNLOADING -> IDLE
    # TestBit.result() default is None, so no bit_completed; assert state only.
    assert any(m.get("event") == "state_changed" and m["state"] == "UNLOADING"
               for m in srv.broadcasts)


def test_arm_room_arms_the_configured_room_binding():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = make_room()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "arm_room", "room_type": "TEST", "fixture": "main"})

    assert error is None
    assert gs.room_binding.is_armed("TEST") is True
    assert gs.room_binding.armed_fixture("TEST") == "main"


def test_arm_room_without_a_fixture_is_refused():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = make_room()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command({"command": "arm_room", "room_type": "TEST"})

    assert error is not None
    assert error["event"] == "error"


def test_release_room_clears_one_fixtures_binding():
    binding = RoomBindingRegistry()
    binding.bind("TEST", "main", "ie7")
    binding.bind("TEST", "accent", "ie8")
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = make_room()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "release_room", "room_type": "TEST", "fixture": "main"})

    assert error is None
    assert binding.bound_device("TEST", "main") is None
    assert binding.bound_device("TEST", "accent") == "ie8"


def test_release_room_without_a_fixture_clears_every_fixture():
    binding = RoomBindingRegistry()
    binding.bind("TEST", "main", "ie7")
    binding.bind("TEST", "accent", "ie8")
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = make_room()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "release_room", "room_type": "TEST"})

    assert error is None
    assert binding.bound_device("TEST", "main") is None
    assert binding.bound_device("TEST", "accent") is None


def test_arm_room_errors_when_no_room_configured():
    gs = GameServer({"TestBit": TestBit})   # no room_binding, no room
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "arm_room", "room_type": "TEST", "fixture": "main"})

    assert error is not None
    assert error["event"] == "error"


def test_arm_room_errors_for_mismatched_room_type():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = make_room()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "arm_room", "room_type": "DEMO", "fixture": "main"})

    assert error is not None


def test_snapshot_never_lists_the_room_role():
    gs = GameServer({"RoomCapableBit": RoomCapableBit},
                     room_binding=RoomBindingRegistry())
    gs.room = make_room()
    gs.load_bit("RoomCapableBit")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    snapshot = agent.snapshot()

    role_names = {r["role"] for r in snapshot["roles"]}
    assert "room_test" not in role_names
    registration_names = {r["role"] for r in snapshot["registration"]}
    assert "room_test" not in registration_names
    # the ordinary player/jam roles from TestBit's own role_table are
    # untouched by the filter
    assert "player" in role_names and "jammer" in role_names


def test_devices_view_hides_the_room_assignment():
    binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    gs.room = make_room()
    gs.load_bit("RoomCapableBit")
    gs.hello("ie9", "Shroom Nine", "1")
    binding.arm("TEST", "main", window_seconds=10.0)
    gs.join("ie9", "ROOM_TEST_NODE")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    devices = agent._devices_view()

    ie9 = next(d for d in devices if d["dev"] == "ie9")
    assert ie9["role"] is None    # device is listed, but not as "room_test"


def test_devices_view_carries_muted_flag():
    gs, srv, agent = _server_with_agent()
    gs.hello("ie1", "Shroom One", "1")
    gs.muted.add("ie1")

    view = agent._devices_view()

    ie1 = next(d for d in view if d["dev"] == "ie1")
    assert ie1["muted"] is True


def _room_console(bit_name="TestBit", canvas_urls=None):
    """A GameServer with a bound TEST Room and a loaded Bit, plus a
    ConsoleAgent wired to a RoomBridge carrying a live cc value.

    TestBit, NOT tests/test_engine.py's RoomCapableBit: that fixture overrides
    role_table and rebuilds the Room role with a bare room_role(...),
    so its light_manifest and ugen_manifest are both empty. TestBit declares
    the real aurora + flsyn Room instruments (bits/test_bit.py), which is what
    these tests are asserting on."""
    from control.room_bridge import RoomBridge
    binding = RoomBindingRegistry()
    gs = GameServer({bit_name: TestBit}, room_binding=binding)
    gs.room = make_room()
    gs.room.bound = {"main": "sim-room-main"}
    gs.load_bit(bit_name)
    bridge = RoomBridge()
    bridge.bind("sim-room-main")
    bridge.feed_light(0xB0, 74, 93)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv, room_bridge=bridge, canvas_urls=canvas_urls)
    return gs, srv, agent


def test_snapshot_carries_the_room_panel():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"]["room_type"] == "TEST"
    main = msg["room"]["fixtures"][0]
    assert main["name"] == "main"
    assert main["dev"] == "sim-room-main"
    assert [z["name"] for z in main["zones"]] == \
        ["main.left", "main.center", "main.right"]


def test_snapshot_room_instruments_include_light_and_audio():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    kinds = [i["kind"] for i in msg["room"]["instruments"]]
    assert "light" in kinds and "audio" in kinds


def test_snapshot_room_carries_live_controller_values():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"]["controllers"] == {74: 93}


def test_room_payload_carries_fixture_urls():
    gs, srv, agent = _room_console(
        canvas_urls=lambda: {"sim-room-main": "http://h:9/"})
    room = agent.snapshot()["room"]
    by_name = {f["name"]: f for f in room["fixtures"]}
    assert by_name["main"]["url"] == "http://h:9/"


def test_device_view_carries_url_when_known():
    binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    gs.hello("ie1", "Shroom One", "1")
    gs.hello("ie2", "Shroom Two", "1")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv,
                          canvas_urls=lambda: {"ie1": "http://h:9/"})

    devices = agent.snapshot()["devices"]

    by_dev = {d["dev"]: d for d in devices}
    assert by_dev["ie1"]["url"] == "http://h:9/"
    assert by_dev["ie2"]["url"] is None


def test_room_changed_broadcasts_only_when_it_changes():
    gs, srv, agent = _room_console()
    agent.poll()
    srv.broadcasts.clear()
    agent.poll()
    assert [b for b in srv.broadcasts if b["event"] == "room_changed"] == []

    agent._room_bridge.feed_light(0xB0, 74, 12)
    agent.poll()
    changed = [b for b in srv.broadcasts if b["event"] == "room_changed"]
    assert len(changed) == 1
    assert changed[0]["room"]["controllers"] == {74: 12}


def test_no_room_configured_yields_a_null_room():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"] is None


def test_the_room_stays_hidden_from_roles_and_registration_while_visible_as_room():
    """The section 3 regression. BOTH halves in one test, because the whole
    safety argument for amending the 2026-08-10 spec's section 7 is that they
    hold simultaneously. This is the test most likely to catch a future
    accidental widening."""
    import json
    from control.rooms import room_role_name
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]

    # visible
    assert msg["room"] is not None
    assert msg["room"]["instruments"], "the Room panel must show instruments"

    # hidden
    room_name = room_role_name("TEST")
    assert room_name not in [r["role"] for r in msg["roles"]]
    assert room_name not in [r["role"] for r in msg["registration"]]
    for key in ("roles", "registration"):
        assert "ROOM_TEST_NODE" not in json.dumps(msg[key])


def test_a_dead_console_client_is_dropped_not_retried():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    srv.fail_sends_to("c1")
    agent.poll()
    agent._room_bridge.feed_light(0xB0, 74, 5)
    agent.poll()
    assert srv.dropped == ["c1"]


def test_a_dead_console_client_is_dropped_not_retried_on_send():
    """Boundary rule 5: FakeConsoleServer.send() must be no more permissive
    than the real ConsoleServer.send() (console/server.py), which also drops
    a client whose send() raises. Modelled on
    test_a_dead_console_client_is_dropped_not_retried, which covers the
    broadcast() path."""
    gs, srv, agent = _room_console()
    srv.connect("c1")
    srv.fail_sends_to("c1")
    agent.poll()
    assert srv.dropped == ["c1"]


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def test_room_frames_are_broadcast_at_the_decimated_rate():
    from console.agent import ROOM_FRAME_INTERVAL
    gs, srv, agent = _room_console()
    clock = FakeClock(100.0)
    agent._clock = clock

    agent.on_room_frame("sim-room", bytes(range(180)))
    agent.poll()
    frames = [b for b in srv.broadcasts if b["event"] == "room_frame"]
    assert len(frames) == 1
    assert frames[0]["dev"] == "sim-room"
    assert frames[0]["channels"] == list(range(180))

    # Too soon: dropped, not queued.
    agent.on_room_frame("sim-room", bytes(180))
    clock.now += ROOM_FRAME_INTERVAL / 2
    agent.poll()
    assert len([b for b in srv.broadcasts if b["event"] == "room_frame"]) == 1

    # Interval elapsed: the LATEST frame goes, the skipped one is gone.
    clock.now += ROOM_FRAME_INTERVAL
    agent.on_room_frame("sim-room", bytes([7] * 180))
    agent.poll()
    frames = [b for b in srv.broadcasts if b["event"] == "room_frame"]
    assert len(frames) == 2
    assert frames[1]["channels"] == [7] * 180


def test_two_fixtures_changing_in_the_same_window_both_broadcast():
    from console.agent import ROOM_FRAME_INTERVAL
    gs, srv, agent = _room_console()
    clock = FakeClock(100.0)
    agent._clock = clock

    # Both fixtures change before the first poll -- the old single-slot
    # implementation would only ever relay the second call's dev.
    agent.on_room_frame("sim-room-main", bytes(180))
    agent.on_room_frame("sim-room-accent", bytes(90))
    agent.poll()
    frames = [b for b in srv.broadcasts if b["event"] == "room_frame"]
    assert {f["dev"] for f in frames} == {"sim-room-main", "sim-room-accent"}

    # A later main-only change must still relay on its own -- accent's
    # entry being consumed must not block main's next one, or vice versa.
    # (2x, not 1x: 100.0 + ROOM_FRAME_INTERVAL - 100.0 rounds to just under
    # ROOM_FRAME_INTERVAL in float64, which would spuriously trip the "too
    # soon" guard -- see test_room_frames_are_broadcast_at_the_decimated_rate
    # above, which sidesteps the same hazard with its own 1.5x net bump.)
    clock.now += ROOM_FRAME_INTERVAL * 2
    agent.on_room_frame("sim-room-main", bytes([9] * 180))
    agent.poll()
    frames = [b for b in srv.broadcasts if b["event"] == "room_frame"]
    assert len(frames) == 3
    assert frames[2]["dev"] == "sim-room-main"
    assert frames[2]["channels"] == [9] * 180


def test_no_frame_received_broadcasts_nothing():
    gs, srv, agent = _room_console()
    agent._clock = FakeClock(100.0)
    agent.poll()
    assert [b for b in srv.broadcasts if b["event"] == "room_frame"] == []


def test_snapshot_carries_the_loaded_bits_triggers():
    gs, srv, agent = _room_console()
    names = sorted(t["name"] for t in agent.snapshot()["triggers"])
    assert names == ["flash_device", "play_aurora", "stop", "win"]


def test_snapshot_triggers_is_empty_with_no_bit_loaded():
    gs, srv, agent = _server_with_agent()
    assert agent.snapshot()["triggers"] == []


def test_the_room_stays_hidden_while_triggers_are_visible():
    """The Spec A section 3 regression, extended. Both halves in one test,
    because the safety argument is that they hold simultaneously: a trigger
    panel must not become the thing that leaks the Room's role.
    """
    gs, srv, agent = _room_console()
    snapshot = agent.snapshot()

    assert snapshot["triggers"]                       # the new surface is live
    room_name = room_role_name("TEST")
    assert all(r["role"] != room_name for r in snapshot["roles"])
    assert all(r["role"] != room_name for r in snapshot["registration"])
    # The node id must not appear anywhere in the payload, including inside
    # the new triggers key.
    assert "ROOM_TEST_NODE" not in json.dumps(snapshot["triggers"])


def test_triggers_changed_broadcasts_on_change_only():
    gs, srv, agent = _room_console()
    agent.poll()
    srv.broadcasts.clear()
    agent.poll()
    assert not [b for b in srv.broadcasts
                if b.get("event") == "triggers_changed"]


def test_triggers_changed_broadcasts_when_a_bit_unloads():
    # NOTE: adjusted from the task-8 brief. TestBit currently declares no
    # triggers (see the note on test_snapshot_carries_the_loaded_bits_triggers
    # above), so _current_triggers() is already [] before the abort and the
    # brief's before/after transition never actually occurs against today's
    # fixtures -- there would be nothing to diff. `_last_triggers` is seeded
    # with a non-empty sentinel directly (the same in-place technique the
    # fire_trigger tests below use on gs.fire_trigger) so the abort's real []
    # is exercised as a genuine change. Re-tighten to drop the seed once
    # Task 10 gives TestBit real triggers.
    gs, srv, agent = _room_console()
    agent.poll()
    srv.broadcasts.clear()
    agent._last_triggers = [{"name": "sentinel"}]
    gs.abort()
    agent.poll()
    changed = [b for b in srv.broadcasts
               if b.get("event") == "triggers_changed"]
    assert changed and changed[-1]["triggers"] == []


def test_on_trigger_fired_broadcasts_the_record():
    gs, srv, agent = _room_console()
    agent.on_trigger_fired(TriggerFired(
        name="play_aurora", condition="round_won", fired_by="admin-manual",
        declared_source="bit-adjudicated", dev=None, devs=("sim-room",),
        at=1.0, steps=3))
    fired = [b for b in srv.broadcasts if b["event"] == "trigger_fired"]
    assert fired[0]["fired"]["fired_by"] == "admin-manual"
    assert fired[0]["fired"]["declared_source"] == "bit-adjudicated"
    assert fired[0]["fired"]["devs"] == ["sim-room"]


def test_a_fire_trigger_command_reaches_the_engine_as_admin_manual():
    gs, srv, agent = _room_console()
    calls = []
    gs.fire_trigger = lambda name, **kw: calls.append((name, kw))
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger", "name": "play_aurora"})
    agent.poll()
    assert calls == [("play_aurora",
                      {"fired_by": "admin-manual", "dev": None})]


def test_a_fire_trigger_command_forwards_its_device():
    gs, srv, agent = _room_console()
    calls = []
    gs.fire_trigger = lambda name, **kw: calls.append((name, kw))
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger", "name": "flash_device",
                       "dev": "ie1"})
    agent.poll()
    assert calls[0][1]["dev"] == "ie1"


def test_a_refused_fire_is_surfaced_as_an_error_event():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger", "name": "nope"})
    agent.poll()
    errors = [msg for _client, msg in srv.sent
              if msg.get("event") == "error"]
    assert "unknown trigger" in errors[0]["message"]


def test_an_unparseable_fire_command_is_surfaced_not_dropped():
    """arm_room/release_room already surface a parse failure rather than
    logging and dropping it, because an operator pressing a button deserves to
    see why nothing happened."""
    gs, srv, agent = _room_console()
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger"})
    agent.poll()
    errors = [msg for _client, msg in srv.sent
              if msg.get("event") == "error"]
    assert "name" in errors[0]["message"]


def test_list_bits_without_registry_sends_no_registry_error():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    srv.deliver("c1", {"command": "list_bits"})
    agent.poll()
    errors = [msg for _client, msg in srv.sent if msg.get("event") == "error"]
    assert len(errors) == 1
    assert errors[0]["message"] == "no registry"


def test_list_bits_with_registry_answers_bits_listed():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    registry = FakeBitRegistry()
    agent = ConsoleAgent(gs, srv, registry=registry)
    srv.connect("c1")
    srv.deliver("c1", {"command": "list_bits"})
    agent.poll()
    sent = [msg for _client, msg in srv.sent if msg.get("event") == "bits_listed"]
    # Two: one from the connect-time snapshot path (so the Bits panel needs
    # no request round-trip, see console/agent.py's poll()) and one from the
    # explicit list_bits command this test also sends.
    expected = {"event": "bits_listed",
                "bits": [{"name": "TestBit"}],
                "errors": [{"path": "x", "message": "bad"}]}
    assert sent == [expected, expected]


def test_connect_with_registry_sends_bits_listed_without_a_request():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    registry = FakeBitRegistry()
    agent = ConsoleAgent(gs, srv, registry=registry)
    srv.connect("c1")
    agent.poll()
    sent = [msg for _client, msg in srv.sent if msg.get("event") == "bits_listed"]
    assert sent == [{"event": "bits_listed",
                     "bits": [{"name": "TestBit"}],
                     "errors": [{"path": "x", "message": "bad"}]}]


def test_connect_without_registry_sends_no_bits_listed_and_no_error():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    agent.poll()
    events = [msg.get("event") for _client, msg in srv.sent]
    assert "bits_listed" not in events
    assert "error" not in events


def test_load_bit_with_registry_resolves_overrides_into_constructed_bit():
    base = parse_manifest(open("bits/test/bit.toml").read(),
                          source="bits/test/bit.toml")
    merged = merge_overrides(base, {"launch": {"setup_seconds": 1}},
                             source="bits/test/bit.toml")
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    registry = FakeBitRegistry(config=merged)
    agent = ConsoleAgent(gs, srv, registry=registry)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "overrides": {"launch": {"setup_seconds": 1}}})
    agent.poll()
    assert registry.resolve_calls == [
        ("TestBit", {"launch": {"setup_seconds": 1}})]
    assert gs.bit.config.launch.setup_seconds == 1
    assert gs.state.name == "SETUP"


def test_load_bit_with_registry_bad_overrides_sends_error_state_stays_idle():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    registry = FakeBitRegistry(raises=ManifestError(
        source="s", key="launch.setup_seconds", message="bad value"))
    agent = ConsoleAgent(gs, srv, registry=registry)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "overrides": {"launch": {"setup_seconds": "x"}}})
    agent.poll()
    errors = [msg for _client, msg in srv.sent if msg.get("event") == "error"]
    assert len(errors) == 1
    assert gs.state.name == "IDLE"


def test_load_bit_with_registry_unknown_bit_sends_error_state_stays_idle():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    registry = FakeBitRegistry(raises=KeyError("nope"))
    agent = ConsoleAgent(gs, srv, registry=registry)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "nope"})
    agent.poll()
    errors = [msg for _client, msg in srv.sent if msg.get("event") == "error"]
    assert len(errors) == 1
    assert gs.state.name == "IDLE"


def test_bit_completed_event_carries_bit_name_and_version():
    class ScoringBit(TestBit):
        def result(self):
            return {"score": 99}

    gs = GameServer(bit_registry={"scoring_bit": ScoringBit})
    srv = FakeConsoleServer()
    ConsoleAgent(gs, srv)
    gs.load_bit("scoring_bit")
    gs.run()
    gs.tick(3.0)  # crosses TestBit's default 2.0s completion threshold

    completed = [m for m in srv.broadcasts if m.get("event") == "bit_completed"]
    assert completed == [{"event": "bit_completed", "result": {"score": 99},
                          "bit": {"name": "scoring_bit", "version": "0.1"}}]


# --- Task 6: room commands, terrarium-state gating, rooms snapshot --------

from control.terrarium import TerrariumState
from control.wire_json import dumps
from tests.test_terrarium import make_terrarium


def test_load_room_command_without_terrarium_sends_error():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_room", "name": "TEST"})
    agent.poll()
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors == [{"event": "error", "command": "load_room",
                       "message": "no terrarium"}]


def test_unload_room_command_without_terrarium_sends_error():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    srv.deliver("c1", {"command": "unload_room"})
    agent.poll()
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors == [{"event": "error", "command": "unload_room",
                       "message": "no terrarium"}]


def test_load_room_command_drives_terrarium_and_broadcasts_room_loaded():
    terrarium = make_terrarium()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_room", "name": "TEST"})

    agent.poll()

    assert terrarium.state == TerrariumState.ROOM_READY
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors == []
    loaded = [m for m in srv.broadcasts if m.get("event") == "room_loaded"]
    assert loaded == [{"event": "room_loaded", "name": "TEST"}]


def test_room_panel_controllers_read_terrarium_room_bridge_live():
    """ConsoleAgent constructed with no room_bridge= at all (the NO_ROOM
    boot shape: harness/terrarium_boot.py's main() builds ConsoleAgent
    before any Room exists) must still show live controller values once a
    Room loads THROUGH terrarium -- this panel cannot be reading a frozen
    __init__-time snapshot (there wasn't one to freeze), only
    `terrarium.room_bridge` fresh on every render."""
    terrarium = make_terrarium()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)

    reason = terrarium.load_room("TEST")
    assert reason is None
    terrarium.room_bridge.feed_light(0xB0, 74, 93)

    srv.connect("c1")
    agent.poll()

    _, msg = srv.sent[0]
    assert msg["room"] is not None
    assert msg["room"]["controllers"] == {74: 93}


def test_load_room_refusal_is_error_event_and_broadcasts_room_load_failed():
    terrarium = make_terrarium(
        ownership_probe=lambda: "another Console owns this room")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_room", "name": "TEST"})

    agent.poll()   # must not raise

    assert terrarium.state == TerrariumState.NO_ROOM
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors == [{"event": "error", "command": "load_room",
                       "message": "another Console owns this room"}]
    failed = [m for m in srv.broadcasts if m.get("event") == "room_load_failed"]
    assert failed == [{"event": "room_load_failed", "name": "TEST",
                       "reason": "another Console owns this room"}]
    # a load failure must never be reported as room_unloaded
    assert not [m for m in srv.broadcasts if m.get("event") == "room_unloaded"]


def test_unload_room_command_drives_terrarium_and_broadcasts_room_unloaded():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.broadcasts.clear()
    srv.connect("c1")
    srv.deliver("c1", {"command": "unload_room"})

    agent.poll()

    assert terrarium.state == TerrariumState.NO_ROOM
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors == []
    unloaded = [m for m in srv.broadcasts if m.get("event") == "room_unloaded"]
    assert unloaded == [{"event": "room_unloaded", "name": "TEST"}]


def test_unload_room_refusal_is_error_event():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    terrarium.gs.load_bit("RoomCapableBit")
    terrarium.gs.hello("ie9", "Shroom Nine", "1")
    terrarium.gs.join("ie9", room_role_name("TEST"))
    terrarium.gs.run()   # not IDLE -- unload without force should refuse
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "unload_room"})

    agent.poll()

    assert terrarium.state == TerrariumState.ROOM_READY
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "unload_room"


def test_load_bit_is_gated_while_room_not_ready():
    terrarium = make_terrarium()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "RoomCapableBit"})

    agent.poll()

    assert terrarium.gs.state.name == "IDLE"
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors == [{"event": "error", "command": "load_bit",
                       "message": "no room loaded"}]


def test_load_bit_succeeds_once_room_is_ready():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "RoomCapableBit"})

    agent.poll()

    assert terrarium.gs.state.name == "SETUP"
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors == []


def test_load_bit_without_terrarium_is_never_gated():
    gs, srv, agent = _server_with_agent()   # terrarium=None
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})
    agent.poll()
    assert gs.state.name == "SETUP"


def test_snapshot_carries_terrarium_state_and_rooms():
    terrarium = make_terrarium()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)

    snap = agent.snapshot()
    assert snap["terrarium_state"] == "NO_ROOM"
    assert snap["rooms"] == [
        {"name": "TEST", "description": "", "status": None, "active": False}]

    terrarium.load_room("TEST")
    snap = agent.snapshot()
    assert snap["terrarium_state"] == "ROOM_READY"
    assert snap["rooms"] == [
        {"name": "TEST", "description": "", "status": None, "active": True}]


def test_snapshot_terrarium_fields_are_none_safe_without_terrarium():
    gs, srv, agent = _server_with_agent()
    snap = agent.snapshot()
    assert snap["terrarium_state"] is None
    assert snap["rooms"] == []


def test_room_lifecycle_event_byte_shapes():
    terrarium = make_terrarium()
    srv = FakeConsoleServer()
    ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.broadcasts.clear()

    terrarium.load_room("TEST")
    loaded = next(m for m in srv.broadcasts if m["event"] == "room_loaded")
    assert dumps(loaded) == '{"event": "room_loaded", "name": "TEST"}'
    state_changed = [m for m in srv.broadcasts if m["event"] == "state_changed"]
    assert dumps(state_changed[-1]) == (
        '{"event": "state_changed", "state": "IDLE", "loaded_bit": null, '
        '"terrarium_state": "ROOM_READY"}')

    srv.broadcasts.clear()
    terrarium.unload_room()
    unloaded = next(m for m in srv.broadcasts if m["event"] == "room_unloaded")
    assert dumps(unloaded) == '{"event": "room_unloaded", "name": "TEST"}'


def test_room_load_progress_is_broadcast_per_stage():
    terrarium = make_terrarium()
    srv = FakeConsoleServer()
    ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    srv.broadcasts.clear()

    terrarium.load_room("TEST")

    stages = [m["stage"] for m in srv.broadcasts if m["event"] == "room_load_progress"]
    assert "validating" in stages
    assert "spawning arco" in stages
    assert "room ready" in stages
    progress_msg = next(m for m in srv.broadcasts
                        if m["event"] == "room_load_progress")
    assert dumps(progress_msg) == (
        '{"event": "room_load_progress", "stage": "validating"}')
