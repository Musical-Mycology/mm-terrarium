import pytest

from bits.test.test_bit import TestBit
from control.bit_config import ManifestError
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from tests.test_engine import RoomCapableBit, make_room
from uplink.journal import Journal
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


def test_restart_command_reloads_the_same_bit_with_its_config():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    server.run()
    cfg_before = getattr(server.bit, "config", None)
    transport.push_incoming({"command": "restart"})

    agent.poll()

    assert server.bit_name == "test_bit"
    assert getattr(server.bit, "config", None) is cfg_before
    assert server.state.name == "SETUP"   # reloaded, not just aborted
    assert not [m for m in transport.sent if m["event"] == "error"]


def test_restart_command_with_no_bit_sends_error_event():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "restart"})

    agent.poll()  # must not raise

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "restart"
    assert errors[0]["message"] == "no bit loaded"


def test_invalid_command_sends_error_event_without_raising():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "run"})  # requires SETUP; server is IDLE

    agent.poll()  # must not raise

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "run"
    assert errors[0]["message"] == "no Bit loaded"


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


def test_bit_completed_sent_at_completing_when_result_present():
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
                          "bit": {"name": "scoring_bit", "version": "0.1"}, "players": []}]


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
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert len(completed) == 1 and completed[0]["result"] is None
    assert completed[0]["players"] == [{"dev": "ie1", "role": "player", "class": "scored"}]


def test_bit_completed_with_null_result_when_bit_has_none():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    server.run()
    server.tick(3.0)

    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert len(completed) == 1 and completed[0]["result"] is None
    assert completed[0]["players"] == []


def test_events_not_sent_while_disconnected():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    UplinkAgent(server, transport)
    # never connected

    server.load_bit("test_bit")

    assert transport.sent == []


def test_bit_completed_carries_players_captured_before_release():
    agent, server, transport = make_agent()
    server.hello("ie1", "Testshroom 1", "1.0")
    server.hello("ie2", "Testshroom 2", "1.0")
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")
    server.join("ie2", "TEST_JAM_NODE")
    server.run()
    server.tick(3.0)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed[0]["players"] == [
        {"dev": "ie1", "role": "player", "class": "scored"},
        {"dev": "ie2", "role": "jammer", "class": "jam"}]
    states = [m["state"] for m in transport.sent if m["event"] == "state_changed"]
    # sent on COMPLETING, i.e. before the UNLOADING state_changed
    idx_completed = transport.sent.index(completed[0])
    idx_unloading = next(i for i, m in enumerate(transport.sent)
                         if m.get("event") == "state_changed" and m["state"] == "UNLOADING")
    assert idx_completed < idx_unloading


def test_abort_sends_no_bit_completed():
    class ScoringBit(TestBit):
        def result(self):
            return {"score": 99}
    server = GameServer(bit_registry={"scoring_bit": ScoringBit})
    transport = FakeTransport()
    UplinkAgent(server, transport)
    transport.connect()
    server.load_bit("scoring_bit")
    server.run()
    server.abort()
    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []
    assert server.state.name == "IDLE"


def test_the_reserved_terrarium_id_never_appears_in_players():
    agent, server, transport = make_agent()
    server.hello("terrarium", "Box", "1.0")
    server.load_bit("test_bit")
    server.join("terrarium", "TEST_PLAYER_NODE")  # engine grants it; wire refusal is elsewhere
    server.hello("ie1", "Testshroom 1", "1.0")
    server.join("ie1", "TEST_PLAYER_NODE")
    server.run()
    server.tick(3.0)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed[0]["players"] == [
        {"dev": "ie1", "role": "player", "class": "scored"}]


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


from uplink.protocol import UplinkIdentity


def test_identity_is_the_first_frame_on_every_connect():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    ident = UplinkIdentity("mm", "main-stage", "ab" * 32)
    agent = UplinkAgent(server, transport, identity=ident, time_source=FakeClock())
    agent.maintain_connection()
    assert transport.sent[0] == {"event": "identity", "tenant_slug": "mm",
                                 "terrarium_name": "main-stage", "secret": "ab" * 32}
    assert transport.sent[1]["event"] == "state_changed"
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert transport.sent[0]["event"] == "identity"


def test_no_identity_means_no_identity_frame():
    agent, server, transport = make_agent()
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert transport.sent[0]["event"] == "state_changed"


def test_resync_carries_the_injected_lan_ip_and_ordinary_events_do_not():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, lan_ip=lambda: "10.0.0.7",
                        time_source=FakeClock())
    agent.maintain_connection()
    assert transport.sent[0]["lan_ip"] == "10.0.0.7"
    server.load_bit("test_bit")
    later = [m for m in transport.sent[1:] if m["event"] == "state_changed"]
    assert all("lan_ip" not in m for m in later)


def test_a_raising_lan_ip_is_omitted_not_fatal():
    def boom():
        raise OSError("no route")
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, lan_ip=boom, time_source=FakeClock())
    agent.maintain_connection()
    assert "lan_ip" not in transport.sent[0]


def _completed_round(server):
    server.load_bit("test_bit")
    server.run()
    server.tick(3.0)


def test_bit_completed_is_journaled_even_while_disconnected(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    _completed_round(server)          # never connected
    assert [e["event"] for e in journal.entries()] == ["bit_completed"]
    assert transport.sent == []


def test_replay_after_resync_on_a_durable_transport_then_clear(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append({"event": "bit_completed", "bit": {"name": "old", "version": "0"},
                    "result": None, "players": []})
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()
    events = [m["event"] for m in transport.sent]
    assert events[0] == "state_changed"
    assert events[-1] == "bit_completed"
    assert transport.sent[-1]["bit"]["name"] == "old"
    assert journal.entries() == []


def test_live_send_and_journal_both_happen_when_connected(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()
    _completed_round(server)
    assert [m for m in transport.sent if m["event"] == "bit_completed"]
    assert len(journal.entries()) == 1       # trimmed only on the next replay
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert [m for m in transport.sent if m["event"] == "bit_completed"]
    assert journal.entries() == []


def test_non_durable_transport_replays_nothing_and_clears_nothing(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport(durable=False)
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append({"event": "bit_completed", "bit": {"name": "old", "version": "0"},
                    "result": None, "players": []})
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()
    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []
    assert len(journal.entries()) == 1


def test_a_send_that_raises_mid_replay_leaves_the_journal_intact(tmp_path):
    class DroppingTransport(FakeTransport):
        def send(self, msg):
            if msg.get("event") == "bit_completed" and msg["bit"]["name"] == "second":
                self.connected = False
                raise ConnectionError("dropped")
            super().send(msg)

    server = GameServer(bit_registry=REGISTRY)
    transport = DroppingTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    for name in ("first", "second", "third"):
        journal.append({"event": "bit_completed", "bit": {"name": name, "version": "0"},
                        "result": None, "players": []})
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()          # must not raise
    assert [e["bit"]["name"] for e in journal.entries()] == ["first", "second", "third"]
    assert transport.connected is False


def test_no_journal_means_no_replay_and_no_file(tmp_path):
    agent, server, transport = make_agent()
    transport.disconnect()
    agent.maintain_connection()
    assert not list(tmp_path.iterdir())


# --- fix wave: maintain_connection/poll must never raise to the caller ----

class SendFailsOnceTransport(FakeTransport):
    """Connects fine, but the first send() after connect raises -- e.g. the
    identity frame or the resync hits a socket that closed right away."""

    def __init__(self, fail_on: str):
        super().__init__()
        self._fail_on = fail_on
        self.fail_count = 0

    def send(self, msg: dict) -> None:
        should_fail = (
            self.fail_count == 0
            and ((self._fail_on == "identity" and msg.get("event") == "identity")
                 or (self._fail_on == "resync" and msg.get("event") == "state_changed")))
        if should_fail:
            self.fail_count += 1
            self.connected = False
            raise ConnectionError("closed right after connect")
        super().send(msg)


class ReceiveRaisesTransport(FakeTransport):
    def receive(self):
        raise ConnectionError("socket error on recv")


def test_send_failure_on_identity_frame_does_not_raise_and_allows_reconnect():
    from uplink.protocol import UplinkIdentity
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = SendFailsOnceTransport(fail_on="identity")
    ident = UplinkIdentity("mm", "main-stage", "ab" * 32)
    agent = UplinkAgent(server, transport, identity=ident, time_source=clock)

    agent.maintain_connection()  # connects, then identity send raises
    assert transport.fail_count == 1
    assert transport.connected is False

    clock.advance(agent.INITIAL_BACKOFF_SECONDS + 0.1)
    agent.maintain_connection()  # backoff elapsed -- retries and connects
    assert transport.connected is True


def test_send_failure_on_resync_does_not_raise_and_allows_reconnect():
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = SendFailsOnceTransport(fail_on="resync")
    agent = UplinkAgent(server, transport, time_source=clock)

    agent.maintain_connection()  # connects, resync send raises
    assert transport.fail_count == 1
    assert transport.connected is False

    clock.advance(agent.INITIAL_BACKOFF_SECONDS + 0.1)
    agent.maintain_connection()
    assert transport.connected is True


def test_poll_returns_without_raising_when_receive_raises():
    server = GameServer(bit_registry=REGISTRY)
    transport = ReceiveRaisesTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())
    transport.connect()

    agent.poll()  # must not raise


# --- fix wave: an all-corrupt journal is still truncated on replay --------

def test_all_corrupt_journal_replays_nothing_and_is_cleared(tmp_path):
    path = tmp_path / "j.jsonl"
    path.write_text("not json\nalso not json\n")
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(path))
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())

    agent.maintain_connection()

    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []
    assert journal.is_empty() is True


class SendAlwaysFailsTransport(FakeTransport):
    """A broker that accepts the socket and drops it on the first frame,
    every time (a rejected secret looks exactly like this)."""

    def __init__(self):
        super().__init__()
        self.fail_count = 0

    def send(self, msg):
        self.fail_count += 1
        self.connected = False
        raise ConnectionError("dropped on first frame")


def test_post_connect_send_failure_backs_off_like_a_failed_connect():
    from uplink.protocol import UplinkIdentity
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = SendAlwaysFailsTransport()
    ident = UplinkIdentity("mm", "main-stage", "ab" * 32)
    agent = UplinkAgent(server, transport, identity=ident, time_source=clock)

    agent.maintain_connection()
    assert transport.connect_count == 1 and transport.fail_count == 1

    # Same tick and the next few: no reconnect until the backoff elapses.
    agent.maintain_connection()
    clock.advance(agent.INITIAL_BACKOFF_SECONDS / 2)
    agent.maintain_connection()
    assert transport.connect_count == 1

    clock.advance(agent.INITIAL_BACKOFF_SECONDS)
    agent.maintain_connection()
    assert transport.connect_count == 2 and transport.fail_count == 2

    # And the backoff doubles, as it does for a failed connect.
    clock.advance(agent.INITIAL_BACKOFF_SECONDS + 0.1)
    agent.maintain_connection()
    assert transport.connect_count == 2
    clock.advance(agent.INITIAL_BACKOFF_SECONDS)
    agent.maintain_connection()
    assert transport.connect_count == 3
