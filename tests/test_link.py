from bits.test.test_bit import TestBit
from control.bit_config import ManifestError
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from tests.test_engine import RoomCapableBit, make_room
from uplink.link import UplinkAgent
from uplink.transport import FakeTransport

REGISTRY = {"test_bit": TestBit}


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
        return [{"name": "test_bit"}]

    def errors_view(self):
        return [{"path": "x", "message": "bad"}]


def make_agent():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport)
    transport.connect()
    return agent, server, transport


def test_construction_registers_as_game_server_observer():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    transport.connect()
    UplinkAgent(server, transport)
    server.load_bit("test_bit")   # drives state transitions through the observer
    assert any(m.get("event") == "state_changed" for m in transport.sent)


def test_poll_does_nothing_when_disconnected():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport)
    transport.push_incoming({"command": "run"})

    agent.poll()  # never connected

    assert server.state.name == "IDLE"


def test_load_bit_command_drives_game_server():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "load_bit", "name": "test_bit"})

    agent.poll()

    assert server.state.name == "SETUP"


def test_run_command_drives_game_server():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    transport.push_incoming({"command": "run"})

    agent.poll()

    assert server.state.name == "RUNNING"


def test_abort_command_drives_game_server():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    transport.push_incoming({"command": "abort"})

    agent.poll()

    assert server.state.name == "IDLE"


def test_invalid_command_sends_error_event_without_raising():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "run"})  # requires SETUP; server is IDLE

    agent.poll()  # must not raise

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "run"


def test_unparseable_message_is_dropped_not_raised():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "self_destruct"})

    agent.poll()  # must not raise

    assert server.state.name == "IDLE"
    assert transport.sent == []


def test_state_changes_are_sent_as_events():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")

    events = [m["state"] for m in transport.sent if m["event"] == "state_changed"]
    assert events == ["LOADING", "LOADED", "SETUP"]


def test_registration_changes_are_sent_as_events():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    transport.sent.clear()

    server.join("ie1", "TEST_PLAYER_NODE")

    reg_events = [m for m in transport.sent if m["event"] == "registration_changed"]
    assert len(reg_events) == 1
    roles = {r["role"]: r["count"] for r in reg_events[0]["roles"]}
    assert roles["player"] == 1


def test_bit_completed_sent_at_unload_when_result_present():
    class ScoringBit(TestBit):
        def result(self):
            return {"score": 99}

    server = GameServer(bit_registry={"scoring_bit": ScoringBit})
    transport = FakeTransport()
    UplinkAgent(server, transport)
    transport.connect()

    server.load_bit("scoring_bit")
    server.run()
    server.tick(3.0)  # crosses TestBit's default 2.0s completion threshold

    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed == [{"event": "bit_completed", "result": {"score": 99},
                          "bit": {"name": "scoring_bit", "version": "0.1"}}]


def test_exploding_result_does_not_wedge_state_machine():
    class ExplodingResultBit(TestBit):
        def result(self):
            raise RuntimeError("boom")

    server = GameServer(bit_registry={"exploding_result_bit": ExplodingResultBit})
    released = []
    server.on_release = released.append
    transport = FakeTransport()
    UplinkAgent(server, transport)
    transport.connect()

    server.hello("ie1", "Testshroom 1", "1.0")
    server.load_bit("exploding_result_bit")
    server.join("ie1", "TEST_PLAYER_NODE")
    server.run()
    server.tick(3.0)  # crosses TestBit's default 2.0s completion threshold

    assert server.state.name == "IDLE"
    assert released == ["ie1"]  # device was released, not stranded
    assert server.bit is None
    assert server.registration is None
    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []


def test_no_bit_completed_event_when_result_is_none():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    server.run()
    server.tick(3.0)

    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []


def test_events_not_sent_while_disconnected():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    UplinkAgent(server, transport)
    # never connected

    server.load_bit("test_bit")

    assert transport.sent == []


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FlakyTransport(FakeTransport):
    def __init__(self, fail_times: int):
        super().__init__()
        self._fail_times = fail_times

    def connect(self) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise ConnectionError("no route")
        super().connect()


def test_maintain_connection_connects_immediately_when_disconnected():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())

    agent.maintain_connection()

    assert transport.connected is True
    assert transport.connect_count == 1


def test_maintain_connection_is_a_noop_when_already_connected():
    agent, server, transport = make_agent()  # helper already connects once
    agent.maintain_connection()
    assert transport.connect_count == 1


def test_reconnect_sends_resync_snapshot():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")
    transport.disconnect()
    transport.sent.clear()

    agent.maintain_connection()

    assert transport.sent[0] == {
        "event": "state_changed", "state": "SETUP", "loaded_bit": "test_bit",
        "terrarium_state": None,
    }
    reg_event = transport.sent[1]
    assert reg_event["event"] == "registration_changed"
    roles = {r["role"]: r["count"] for r in reg_event["roles"]}
    assert roles["player"] == 1


def test_resync_omits_registration_snapshot_when_no_bit_loaded():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())

    agent.maintain_connection()

    assert transport.sent == [
        {"event": "state_changed", "state": "IDLE", "loaded_bit": None,
         "terrarium_state": None},
    ]


def test_resync_never_sends_the_room_role():
    server = GameServer(bit_registry={"room_bit": RoomCapableBit},
                         room_binding=RoomBindingRegistry())
    server.room = make_room()
    transport = FakeTransport()
    agent = UplinkAgent(server, transport)
    transport.connect()

    server.load_bit("room_bit")
    server.hello("ie9", "Shroom Nine", "1")
    server.room_binding.arm("TEST", "main", window_seconds=10.0)
    server.join("ie9", "ROOM_TEST_NODE")

    transport.disconnect()
    transport.sent.clear()

    agent.maintain_connection()  # reconnect -> _send_resync()

    reg_event = next(m for m in transport.sent
                      if m["event"] == "registration_changed")
    role_names = {r["role"] for r in reg_event["roles"]}
    assert "room_test" not in role_names
    # the ordinary roles from the Bit's own role_table are untouched
    assert "player" in role_names and "jammer" in role_names


def test_on_registration_change_never_sends_the_room_role():
    server = GameServer(bit_registry={"room_bit": RoomCapableBit},
                         room_binding=RoomBindingRegistry())
    server.room = make_room()
    transport = FakeTransport()
    agent = UplinkAgent(server, transport)
    transport.connect()

    server.load_bit("room_bit")
    server.hello("ie9", "Shroom Nine", "1")
    server.room_binding.arm("TEST", "main", window_seconds=10.0)
    server.join("ie9", "ROOM_TEST_NODE")  # a Room join alone doesn't fire
                                           # on_registration_change

    transport.sent.clear()

    server.hello("ie1", "Shroom One", "1")
    server.join("ie1", "TEST_PLAYER_NODE")  # an ordinary join does

    reg_events = [m for m in transport.sent if m["event"] == "registration_changed"]
    assert len(reg_events) == 1
    role_names = {r["role"] for r in reg_events[0]["roles"]}
    assert "room_test" not in role_names
    assert "player" in role_names


def test_failed_connect_backs_off_before_retrying():
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FlakyTransport(fail_times=1)
    agent = UplinkAgent(server, transport, time_source=clock)

    agent.maintain_connection()  # fails, schedules retry at t=1.0
    assert transport.connected is False

    clock.advance(0.5)
    agent.maintain_connection()  # too soon (0.5s < 1.0s backoff)
    assert transport.connected is False

    clock.advance(0.6)  # total 1.1s elapsed -- past the 1.0s backoff
    agent.maintain_connection()
    assert transport.connected is True


def test_backoff_doubles_on_repeated_failures():
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FlakyTransport(fail_times=2)
    agent = UplinkAgent(server, transport, time_source=clock)

    agent.maintain_connection()  # fail 1, next attempt scheduled at t=1.0
    clock.advance(1.0)
    agent.maintain_connection()  # fail 2, next attempt scheduled at t=3.0
    assert transport.connected is False

    clock.advance(1.9)  # t=2.9, still short of 3.0
    agent.maintain_connection()
    assert transport.connected is False

    clock.advance(0.2)  # t=3.1
    agent.maintain_connection()
    assert transport.connected is True


def test_list_bits_without_registry_sends_no_registry_error():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "list_bits"})

    agent.poll()

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert len(errors) == 1
    assert errors[0]["message"] == "no registry"


def test_list_bits_with_registry_sends_bits_listed():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    registry = FakeBitRegistry()
    agent = UplinkAgent(server, transport, registry=registry)
    transport.connect()
    transport.push_incoming({"command": "list_bits"})

    agent.poll()

    listed = [m for m in transport.sent if m["event"] == "bits_listed"]
    assert listed == [{"event": "bits_listed",
                       "bits": [{"name": "test_bit"}],
                       "errors": [{"path": "x", "message": "bad"}]}]


def test_load_bit_with_registry_resolves_overrides_and_loads():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    registry = FakeBitRegistry(config=None)
    agent = UplinkAgent(server, transport, registry=registry)
    transport.connect()
    overrides = {"launch": {"setup_seconds": 1}}
    transport.push_incoming({"command": "load_bit", "name": "test_bit",
                             "overrides": overrides})

    agent.poll()

    assert registry.resolve_calls == [("test_bit", overrides)]
    assert server.state.name == "SETUP"


def test_load_bit_with_registry_bad_overrides_sends_error_not_raise():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    registry = FakeBitRegistry(raises=ManifestError(
        source="s", key="launch.setup_seconds", message="bad value"))
    agent = UplinkAgent(server, transport, registry=registry)
    transport.connect()
    transport.push_incoming({"command": "load_bit", "name": "test_bit",
                             "overrides": {"launch": {"setup_seconds": "x"}}})

    agent.poll()  # must not raise

    assert server.state.name == "IDLE"
    errors = [m for m in transport.sent if m["event"] == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "load_bit"


def test_load_bit_with_registry_unknown_bit_sends_error_not_raise():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    registry = FakeBitRegistry(raises=KeyError("nope"))
    agent = UplinkAgent(server, transport, registry=registry)
    transport.connect()
    transport.push_incoming({"command": "load_bit", "name": "nope"})

    agent.poll()  # must not raise

    assert server.state.name == "IDLE"
    errors = [m for m in transport.sent if m["event"] == "error"]
    assert len(errors) == 1


# --- Task 6: room commands, terrarium-state gating -------------------------

from control.terrarium import TerrariumState
from control.wire_json import dumps
from tests.test_terrarium import make_terrarium


def test_load_room_command_without_terrarium_sends_error():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "load_room", "name": "TEST"})

    agent.poll()

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert errors == [{"event": "error", "command": "load_room",
                       "message": "no terrarium"}]


def test_unload_room_command_without_terrarium_sends_error():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "unload_room"})

    agent.poll()

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert errors == [{"event": "error", "command": "unload_room",
                       "message": "no terrarium"}]


def test_load_room_command_drives_terrarium_and_sends_room_loaded():
    terrarium = make_terrarium()
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.push_incoming({"command": "load_room", "name": "TEST"})

    agent.poll()

    assert terrarium.state == TerrariumState.ROOM_READY
    assert [m for m in transport.sent if m["event"] == "error"] == []
    loaded = [m for m in transport.sent if m["event"] == "room_loaded"]
    assert loaded == [{"event": "room_loaded", "name": "TEST"}]


def test_load_room_refusal_is_error_event_and_sends_room_load_failed():
    terrarium = make_terrarium(
        ownership_probe=lambda: "another Console owns this room")
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.push_incoming({"command": "load_room", "name": "TEST"})

    agent.poll()   # must not raise

    assert terrarium.state == TerrariumState.NO_ROOM
    errors = [m for m in transport.sent if m["event"] == "error"]
    assert errors == [{"event": "error", "command": "load_room",
                       "message": "another Console owns this room"}]
    failed = [m for m in transport.sent if m["event"] == "room_load_failed"]
    assert failed == [{"event": "room_load_failed", "name": "TEST",
                       "reason": "another Console owns this room"}]
    assert not [m for m in transport.sent if m["event"] == "room_unloaded"]


def test_unload_room_command_drives_terrarium_and_sends_room_unloaded():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.push_incoming({"command": "unload_room"})

    agent.poll()

    assert terrarium.state == TerrariumState.NO_ROOM
    assert [m for m in transport.sent if m["event"] == "error"] == []
    unloaded = [m for m in transport.sent if m["event"] == "room_unloaded"]
    assert unloaded == [{"event": "room_unloaded", "name": "TEST"}]


def test_load_bit_is_gated_while_room_not_ready():
    terrarium = make_terrarium()
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.push_incoming({"command": "load_bit", "name": "RoomCapableBit"})

    agent.poll()

    assert terrarium.gs.state.name == "IDLE"
    errors = [m for m in transport.sent if m["event"] == "error"]
    assert errors == [{"event": "error", "command": "load_bit",
                       "message": "no room loaded"}]


def test_load_bit_succeeds_once_room_is_ready():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.push_incoming({"command": "load_bit", "name": "RoomCapableBit"})

    agent.poll()

    assert terrarium.gs.state.name == "SETUP"
    assert [m for m in transport.sent if m["event"] == "error"] == []


def test_load_bit_without_terrarium_is_never_gated():
    agent, server, transport = make_agent()   # terrarium=None
    transport.push_incoming({"command": "load_bit", "name": "test_bit"})

    agent.poll()

    assert server.state.name == "SETUP"


def test_resync_carries_terrarium_state_and_active_room():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, time_source=FakeClock(),
                        terrarium=terrarium)

    agent.maintain_connection()

    state_events = [m for m in transport.sent if m["event"] == "state_changed"]
    assert state_events[0]["terrarium_state"] == "ROOM_READY"
    loaded = [m for m in transport.sent if m["event"] == "room_loaded"]
    assert loaded == [{"event": "room_loaded", "name": "TEST"}]


def test_resync_terrarium_state_is_none_without_terrarium():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())

    agent.maintain_connection()

    assert transport.sent == [
        {"event": "state_changed", "state": "IDLE", "loaded_bit": None,
         "terrarium_state": None},
    ]


def test_room_lifecycle_event_byte_shapes():
    terrarium = make_terrarium()
    transport = FakeTransport()
    UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.sent.clear()

    terrarium.load_room("TEST")
    loaded = next(m for m in transport.sent if m["event"] == "room_loaded")
    assert dumps(loaded) == '{"event": "room_loaded", "name": "TEST"}'

    transport.sent.clear()
    terrarium.unload_room()
    unloaded = next(m for m in transport.sent if m["event"] == "room_unloaded")
    assert dumps(unloaded) == '{"event": "room_unloaded", "name": "TEST"}'


def test_room_load_progress_is_sent_per_stage():
    terrarium = make_terrarium()
    transport = FakeTransport()
    UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.sent.clear()

    terrarium.load_room("TEST")

    stages = [m["stage"] for m in transport.sent
             if m["event"] == "room_load_progress"]
    assert "validating" in stages
    assert "room ready" in stages
    progress_msg = next(m for m in transport.sent
                        if m["event"] == "room_load_progress")
    assert dumps(progress_msg) == (
        '{"event": "room_load_progress", "stage": "validating"}')
