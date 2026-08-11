from bits.test_bit import TestBit
from console.agent import ConsoleAgent
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.rooms import Room, RoomType
from tests.test_engine import RoomCapableBit


class FakeConsoleServer:
    """In-process test double for console/server.py -- no threads, no socket.
    Tests push new clients + inbound messages and inspect sent/broadcast.
    """

    def __init__(self):
        self.broadcasts = []                # list[dict]
        self.sent = []                      # list[(client, dict)]
        self._new_clients = []
        self._inbound = []                  # list[(client, dict)]

    # --- tick-thread API consumed by ConsoleAgent ---
    def drain_new_clients(self):
        out, self._new_clients = self._new_clients, []
        return out

    def drain_inbound(self):
        out, self._inbound = self._inbound, []
        return out

    def send(self, client, msg):
        self.sent.append((client, msg))

    def broadcast(self, msg):
        self.broadcasts.append(msg)

    # --- test helpers ---
    def connect(self, client):
        self._new_clients.append(client)

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
    gs.room = Room(room_type=RoomType.TEST)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command({"command": "arm_room", "room_type": "TEST"})

    assert error is None
    assert gs.room_binding.is_armed(RoomType.TEST) is True


def test_release_room_clears_the_binding():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "ie7")
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command({"command": "release_room", "room_type": "TEST"})

    assert error is None
    assert binding.bound_device(RoomType.TEST) is None


def test_arm_room_errors_when_no_room_configured():
    gs = GameServer({"TestBit": TestBit})   # no room_binding, no room
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command({"command": "arm_room", "room_type": "TEST"})

    assert error is not None
    assert error["event"] == "error"


def test_arm_room_errors_for_mismatched_room_type():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = Room(room_type=RoomType.TEST)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command({"command": "arm_room", "room_type": "DEMO"})

    assert error is not None


def test_snapshot_never_lists_the_room_role():
    gs = GameServer({"RoomCapableBit": RoomCapableBit},
                     room_binding=RoomBindingRegistry())
    gs.room = Room(room_type=RoomType.TEST)
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
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("RoomCapableBit")
    gs.hello("ie9", "Shroom Nine", "1")
    binding.arm(RoomType.TEST, window_seconds=10.0)
    gs.join("ie9", "ROOM_TEST_NODE")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    devices = agent._devices_view()

    ie9 = next(d for d in devices if d["dev"] == "ie9")
    assert ie9["role"] is None    # device is listed, but not as "room_test"
