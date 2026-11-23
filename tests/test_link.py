import itertools
import re
import uuid
from datetime import datetime, timezone

import pytest

from bits.test.test_bit import TestBit
from control.bit_config import ManifestError
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from tests.fakes import FakeClock
from tests.helpers_admit import admit
from tests.test_engine import RoomCapableBit, make_room
from uplink import protocol
from uplink.journal import Journal
from uplink.link import UplinkAgent
from uplink.transport import FakeTransport, frame_size

REGISTRY = {"test_bit": TestBit}

WALL = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)


def fixed(**kwargs):
    """Deterministic stamping sources: boot "boot-1", completion ids
    uuid.UUID(int=1), (int=2), ... and a wall clock stuck at WALL."""
    counter = itertools.count(1)
    return dict(boot_id="boot-1", id_source=lambda: uuid.UUID(int=next(counter)),
                wall_clock=lambda: WALL, **kwargs)


def strip(frames):
    """Frames without the boot_id/seq stamp, for whole-frame assertions."""
    return [{k: v for k, v in f.items() if k not in ("boot_id", "seq")}
            for f in frames]


def stored(name, cid, *, boot_id="old", seq=7):
    """A journal entry as an earlier boot would have written it."""
    return {"event": "bit_completed", "result": None,
            "bit": {"name": name, "version": "0"}, "players": [],
            "completion_id": cid, "completed_at": "2026-11-22T09:00:00Z",
            "boot_id": boot_id, "seq": seq}


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


def make_agent(**kwargs):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, **fixed(**kwargs))
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

    admit(server, "ie1", "TEST_PLAYER_NODE")

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
    UplinkAgent(server, transport, **fixed())
    transport.connect()

    server.load_bit("scoring_bit")
    server.run()
    server.tick(3.0)  # crosses TestBit's default 2.0s completion threshold

    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert strip(completed) == [{
        "event": "bit_completed", "result": {"score": 99},
        "bit": {"name": "scoring_bit", "version": "0.1"}, "players": [],
        "completion_id": str(uuid.UUID(int=1)),
        "completed_at": "2026-11-23T12:00:00Z"}]


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
    admit(server, "ie1", "TEST_PLAYER_NODE")
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
    admit(server, "ie1", "TEST_PLAYER_NODE")
    admit(server, "ie2", "TEST_JAM_NODE")
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
    admit(server, "terrarium", "TEST_PLAYER_NODE")  # engine validates it; wire refusal is elsewhere
    server.hello("ie1", "Testshroom 1", "1.0")
    admit(server, "ie1", "TEST_PLAYER_NODE")
    server.run()
    server.tick(3.0)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    # The reserved id is excluded from players however it was validated.
    # "terrarium" took the scored slot, so ie1 is the jammer.
    assert completed[0]["players"] == [
        {"dev": "ie1", "role": "jammer", "class": "jam"}]


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
    admit(server, "ie1", "TEST_PLAYER_NODE")
    transport.disconnect()
    transport.sent.clear()

    agent.maintain_connection()

    assert strip(transport.sent)[0] == {
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

    assert strip(transport.sent) == [
        {"event": "state_changed", "state": "IDLE", "loaded_bit": None,
         "terrarium_state": None},
        {"event": "admin_devices_applied", "version": 0},
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
    admit(server, "ie9", "ROOM_TEST_NODE")

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
    admit(server, "ie9", "ROOM_TEST_NODE")  # a Room bind alone doesn't fire
                                            # on_registration_change

    transport.sent.clear()

    server.hello("ie1", "Shroom One", "1")
    admit(server, "ie1", "TEST_PLAYER_NODE")  # a scored validation does

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
    assert strip(listed) == [{"event": "bits_listed",
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

UNLOAD_REFUSAL = ("unloading the Room in a running Terrarium is not "
                  "supported yet: pyarco cannot reconnect to a new Arco in "
                  "one process; stop and run ./terrarium.sh")


def test_load_room_command_without_terrarium_sends_error():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "load_room", "name": "TEST"})

    agent.poll()

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert strip(errors) == [{"event": "error", "command": "load_room",
                       "message": "no terrarium"}]


def test_unload_room_command_without_terrarium_sends_error():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "unload_room"})

    agent.poll()

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert strip(errors) == [{"event": "error", "command": "unload_room",
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
    assert strip(loaded) == [{"event": "room_loaded", "name": "TEST"}]


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
    assert strip(errors) == [{"event": "error", "command": "load_room",
                       "message": "another Console owns this room"}]
    failed = [m for m in transport.sent if m["event"] == "room_load_failed"]
    assert strip(failed) == [{"event": "room_load_failed", "name": "TEST",
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
    assert strip(unloaded) == [{"event": "room_unloaded", "name": "TEST"}]


def test_unload_room_is_refused_while_arco_clients_are_live():
    """D7's unload case, same guard as the Console's: pyarco cannot
    reconnect to a new Arco in one process, so a broker unload with live
    clients would strand the Terrarium in NO_ROOM. The refusal is an error
    event and terrarium.unload_room never runs."""
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    calls = []
    real_unload = terrarium.unload_room
    terrarium.unload_room = lambda force=False: calls.append(force) or real_unload(force=force)
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium,
                        clients_live=lambda: True)
    transport.connect()
    transport.push_incoming({"command": "unload_room", "force": True})

    agent.poll()

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert strip(errors) == [{"event": "error", "command": "unload_room",
                       "message": UNLOAD_REFUSAL}]
    assert calls == []
    assert terrarium.state == TerrariumState.ROOM_READY
    assert not [m for m in transport.sent if m["event"] == "room_unloaded"]


def test_unload_room_refusal_consults_clients_live_at_command_time():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    live = [False]
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium,
                        clients_live=lambda: live[0])
    transport.connect()
    live[0] = True
    transport.push_incoming({"command": "unload_room"})
    agent.poll()
    assert len([m for m in transport.sent if m["event"] == "error"]) == 1
    assert terrarium.state == TerrariumState.ROOM_READY

    live[0] = False
    transport.push_incoming({"command": "unload_room"})
    agent.poll()
    assert terrarium.state == TerrariumState.NO_ROOM


def test_load_bit_is_gated_while_room_not_ready():
    terrarium = make_terrarium()
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.push_incoming({"command": "load_bit", "name": "RoomCapableBit"})

    agent.poll()

    assert terrarium.gs.state.name == "IDLE"
    errors = [m for m in transport.sent if m["event"] == "error"]
    assert strip(errors) == [{"event": "error", "command": "load_bit",
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
    assert strip(loaded) == [{"event": "room_loaded", "name": "TEST"}]


def test_resync_terrarium_state_is_none_without_terrarium():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())

    agent.maintain_connection()

    assert strip(transport.sent) == [
        {"event": "state_changed", "state": "IDLE", "loaded_bit": None,
         "terrarium_state": None},
        {"event": "admin_devices_applied", "version": 0},
    ]


def test_room_lifecycle_event_byte_shapes():
    terrarium = make_terrarium()
    transport = FakeTransport()
    UplinkAgent(terrarium.gs, transport, terrarium=terrarium)
    transport.connect()
    transport.sent.clear()

    terrarium.load_room("TEST")
    loaded = next(m for m in transport.sent if m["event"] == "room_loaded")
    assert dumps(strip([loaded])[0]) == '{"event": "room_loaded", "name": "TEST"}'

    transport.sent.clear()
    terrarium.unload_room()
    unloaded = next(m for m in transport.sent if m["event"] == "room_unloaded")
    assert dumps(strip([unloaded])[0]) == '{"event": "room_unloaded", "name": "TEST"}'


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
    assert dumps(strip([progress_msg])[0]) == (
        '{"event": "room_load_progress", "stage": "validating"}')


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
    UplinkAgent(server, transport, journal=journal, time_source=FakeClock(),
                **fixed())
    _completed_round(server)          # never connected
    entries = journal.entries()
    assert [e["event"] for e in entries] == ["bit_completed"]
    assert entries[0]["completion_id"] == str(uuid.UUID(int=1))
    assert transport.sent == []


def test_replay_after_resync_on_a_durable_transport(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append(stored("old", "c-old"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    events = [m["event"] for m in transport.sent]
    assert events[0] == "state_changed"
    assert events[-1] == "bit_completed"
    assert transport.sent[-1]["bit"]["name"] == "old"
    assert journal.entries() == [stored("old", "c-old")]   # kept until confirmed


def test_live_send_and_journal_both_happen_when_connected(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    _completed_round(server)
    live = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert len(live) == 1
    assert journal.entries() == live         # the same stamped frame

    # Unconfirmed: the next connect sends it again, unchanged.
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert [m for m in transport.sent if m["event"] == "bit_completed"] == live

    # Confirmed: it leaves the journal and is never replayed again.
    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": [live[0]["completion_id"]]})
    agent.poll()
    assert journal.entries() == []
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []


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
    for seq, name in enumerate(("first", "second", "third"), start=1):
        journal.append(stored(name, f"c-{name}", seq=seq))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()          # must not raise
    assert [e["bit"]["name"] for e in journal.entries()] == ["first", "second", "third"]
    assert transport.connected is False
    assert [m["bit"]["name"] for m in transport.sent
            if m["event"] == "bit_completed"] == ["first"]


def test_no_journal_means_no_replay_and_no_file(tmp_path):
    agent, server, transport = make_agent()
    transport.disconnect()
    agent.maintain_connection()
    assert not list(tmp_path.iterdir())


# --- fix wave: maintain_connection/poll must never raise to the caller ----

class SendFailsOnceTransport(FakeTransport):
    """Connects fine, but the first send() after connect raises -- e.g. the
    resync hits a socket that closed right away."""

    def __init__(self, fail_on: str):
        super().__init__()
        self._fail_on = fail_on
        self.fail_count = 0

    def send(self, msg: dict) -> None:
        should_fail = (
            self.fail_count == 0
            and self._fail_on == "resync" and msg.get("event") == "state_changed")
        if should_fail:
            self.fail_count += 1
            self.connected = False
            raise ConnectionError("closed right after connect")
        super().send(msg)


class ReceiveRaisesTransport(FakeTransport):
    def receive(self):
        raise ConnectionError("socket error on recv")


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


# --- an all-corrupt journal is purged at construction, replays nothing ----

def test_all_corrupt_journal_replays_nothing_and_is_purged(tmp_path):
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
    every time. (A rejected secret is refused in the handshake instead: an
    HTTP 401 raised by connect().)"""

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


def test_new_broker_commands_are_ignored_by_the_agent():
    agent, server, transport = make_agent()
    transport.sent.clear()
    for msg in ({"command": "completions_stored", "completion_ids": ["a"]},
                {"command": "session_replaced"}):
        transport.push_incoming(msg)
    agent.poll()
    assert transport.sent == []


# --- box contract (mm-fairyring spec 2026-11-23 section 4) ------------------

import logging
from datetime import timedelta

from uplink.protocol import UplinkIdentity


def stamped(transport):
    return [m for m in transport.sent if m["event"] != "heartbeat"]


def heartbeats(transport):
    return [m for m in transport.sent if m["event"] == "heartbeat"]


def test_no_identity_frame_is_sent_on_connect(caplog):
    caplog.set_level(logging.DEBUG)
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    ident = UplinkIdentity("mm", "main-stage", "s3cr3t" * 8)
    agent = UplinkAgent(server, transport, identity=ident,
                        time_source=FakeClock(), **fixed())

    agent.maintain_connection()

    assert transport.sent[0]["event"] == "state_changed"
    assert "identity" not in [m["event"] for m in transport.sent]
    assert all(ident.secret not in dumps(m) for m in transport.sent)
    assert ident.secret not in caplog.text


def test_resync_order_ends_with_admin_devices_applied_then_journal(tmp_path):
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    terrarium.gs.load_bit("RoomCapableBit")
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append(stored("old", "c-old"))
    transport = FakeTransport()
    agent = UplinkAgent(terrarium.gs, transport, terrarium=terrarium,
                        journal=journal, lan_ip=lambda: "10.0.0.7",
                        time_source=FakeClock(), **fixed())

    agent.maintain_connection()

    assert [m["event"] for m in transport.sent] == [
        "state_changed", "room_loaded", "registration_changed",
        "admin_devices_applied", "bit_completed"]
    assert transport.sent[0]["lan_ip"] == "10.0.0.7"
    assert strip([transport.sent[3]]) == [
        {"event": "admin_devices_applied", "version": 0}]

    # Without a room or a registration the two optional frames drop out,
    # and the version comes from the admin_version callable.
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock(),
                        admin_version=lambda: 3, **fixed())
    agent.maintain_connection()
    assert strip(transport.sent)[1:] == [
        {"event": "admin_devices_applied", "version": 3}]


def test_every_frame_but_heartbeat_carries_boot_id_and_gapless_seq():
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=clock, **fixed())

    agent.maintain_connection()                  # resync
    _completed_round(server)                     # state_changed..., bit_completed
    clock.advance(agent.HEARTBEAT_SECONDS)
    agent.maintain_connection()                  # heartbeat
    transport.push_incoming({"command": "restart"})
    agent.poll()                                 # error after the heartbeat

    frames = stamped(transport)
    assert "bit_completed" in [m["event"] for m in frames]
    assert all(m["boot_id"] == "boot-1" for m in frames)
    assert [m["seq"] for m in frames] == list(range(1, len(frames) + 1))
    assert heartbeats(transport) == [{"event": "heartbeat"}]

    # The default boot_id is one random hex string per agent (per process).
    a = UplinkAgent(GameServer(bit_registry=REGISTRY), FakeTransport())
    b = UplinkAgent(GameServer(bit_registry=REGISTRY), FakeTransport())
    assert re.fullmatch(r"[0-9a-f]{32}", a.boot_id) and a.boot_id != b.boot_id


def test_heartbeat_is_exact_and_sent_every_60_seconds(tmp_path):
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, time_source=clock, journal=journal,
                        **fixed())
    agent.maintain_connection()                  # connect at t=0
    transport.sent.clear()

    clock.now = 59.9
    agent.maintain_connection()
    assert transport.sent == []
    clock.now = 60.0
    agent.maintain_connection()
    assert transport.sent == [{"event": "heartbeat"}]
    clock.now = 119.9
    agent.maintain_connection()
    assert transport.sent == [{"event": "heartbeat"}]
    clock.now = 120.0
    agent.maintain_connection()
    assert transport.sent == [{"event": "heartbeat"}, {"event": "heartbeat"}]
    assert journal.is_empty()                    # never journalled


def test_heartbeat_timer_restarts_on_reconnect():
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=clock, **fixed())
    agent.maintain_connection()                  # connect at t=0
    assert agent._connected_at == 0.0

    clock.now = 50.0
    transport.disconnect()
    agent.maintain_connection()                  # reconnect at t=50
    assert transport.connect_count == 2
    assert agent._connected_at == 50.0

    clock.now = 60.0                             # 60 after the first connect
    agent.maintain_connection()
    assert heartbeats(transport) == []
    clock.now = 109.9
    agent.maintain_connection()
    assert heartbeats(transport) == []
    clock.now = 110.0                            # 60 after the reconnect
    agent.maintain_connection()
    assert heartbeats(transport) == [{"event": "heartbeat"}]


def test_bit_completed_carries_completion_id_and_completed_at():
    agent, server, transport = make_agent()
    _completed_round(server)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed[0]["completion_id"] == str(uuid.UUID(int=1))
    assert completed[0]["completed_at"] == "2026-11-23T12:00:00Z"

    # A clock in another zone is reported in UTC.
    mountain = timezone(timedelta(hours=-7))
    agent, server, transport = make_agent()
    agent._wall_clock = lambda: datetime(2026, 11, 23, 5, 0, 0, tzinfo=mountain)
    _completed_round(server)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed[0]["completed_at"] == "2026-11-23T12:00:00Z"

    # The defaults: a v4 uuid, lower-case and hyphenated; the real clock.
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    UplinkAgent(server, transport)
    transport.connect()
    _completed_round(server)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    cid = completed[0]["completion_id"]
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", cid)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                        completed[0]["completed_at"])


def test_completion_is_journalled_with_its_stamp_before_the_send(tmp_path):
    class JournalCheckingTransport(FakeTransport):
        def __init__(self, journal):
            super().__init__()
            self.journal = journal
            self.journal_at_send = []

        def send(self, msg):
            if msg.get("event") == "bit_completed":
                self.journal_at_send.append(self.journal.entries())
            super().send(msg)

    server = GameServer(bit_registry=REGISTRY)
    journal = Journal(str(tmp_path / "j.jsonl"))
    transport = JournalCheckingTransport(journal)
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    _completed_round(server)

    sent = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert len(sent) == 1
    assert sent[0]["boot_id"] == "boot-1" and isinstance(sent[0]["seq"], int)
    assert transport.journal_at_send == [[sent[0]]]


def test_frame_not_sent_while_disconnected_consumes_no_seq():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock(), **fixed())
    server.load_bit("test_bit")                  # three state_changed, link down

    agent.maintain_connection()                  # state, registration, admin
    assert [m["seq"] for m in transport.sent] == [1, 2, 3]

    transport.disconnect()
    server.abort()                               # more frames while down
    transport.sent.clear()
    agent.maintain_connection()                  # state, admin
    assert [m["seq"] for m in transport.sent] == [4, 5]


def test_completion_while_disconnected_is_journalled_and_stamped(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    _completed_round(server)                     # never connected

    [entry] = journal.entries()
    assert entry["boot_id"] == "boot-1" and entry["seq"] == 1
    assert entry["completion_id"] == str(uuid.UUID(int=1))
    assert transport.sent == []

    agent.maintain_connection()                  # resync, then the replay
    assert [m["seq"] for m in transport.sent] == [2, 3, 1]
    assert transport.sent[-1] == entry


def test_replay_sends_journal_entries_with_original_boot_id_and_seq(tmp_path):
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append(stored("old", "c-old", boot_id="old", seq=7))
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())

    agent.maintain_connection()

    assert transport.sent[-1] == stored("old", "c-old", boot_id="old", seq=7)
    assert [(m["boot_id"], m["seq"]) for m in transport.sent[:-1]] == [
        ("boot-1", 1), ("boot-1", 2)]


def test_replay_does_not_clear_the_journal(tmp_path):
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append(stored("old", "c-old"))
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())

    for _ in range(2):
        transport.sent.clear()
        agent.maintain_connection()
        assert [m for m in transport.sent if m["event"] == "bit_completed"] == [
            stored("old", "c-old")]
        assert journal.entries() == [stored("old", "c-old")]
        transport.disconnect()


def _journal_with(tmp_path, *names):
    journal = Journal(str(tmp_path / "j.jsonl"))
    for seq, name in enumerate(names, start=1):
        journal.append(stored(name, f"c-{name}", seq=seq))
    return journal


def test_completions_stored_removes_only_those_entries(tmp_path):
    journal = _journal_with(tmp_path, "a", "b", "c")
    agent, server, transport = make_agent(journal=journal)
    transport.sent.clear()

    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["c-b"]})
    agent.poll()
    assert [e["completion_id"] for e in journal.entries()] == ["c-a", "c-c"]

    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["c-a", "c-c"]})
    agent.poll()
    assert journal.entries() == []
    assert transport.sent == []                  # nothing is sent in reply


def test_completions_stored_with_unknown_id_is_ignored(tmp_path):
    journal = _journal_with(tmp_path, "a", "b")
    before = (tmp_path / "j.jsonl").read_bytes()
    agent, server, transport = make_agent(journal=journal)

    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["c-zzz"]})
    agent.poll()                                 # must not raise

    assert (tmp_path / "j.jsonl").read_bytes() == before


def test_malformed_completions_stored_leaves_journal_untouched(tmp_path):
    journal = _journal_with(tmp_path, "a", "b")
    before = (tmp_path / "j.jsonl").read_bytes()
    agent, server, transport = make_agent(journal=journal)

    for bad in ({"completion_ids": "c-a"}, {"completion_ids": [""]},
                {"completion_ids": [1]}, {"completion_ids": None}, {}):
        transport.push_incoming({"command": "completions_stored", **bad})
        agent.poll()                             # must not raise

    assert (tmp_path / "j.jsonl").read_bytes() == before


def test_replay_skipped_on_non_durable_transport(tmp_path):
    journal = _journal_with(tmp_path, "a")
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport(durable=False)
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())

    agent.maintain_connection()

    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []
    assert [e["completion_id"] for e in journal.entries()] == ["c-a"]


def test_oversize_bit_completed_is_sent_with_result_dropped(tmp_path):
    class HugeResultBit(TestBit):
        def result(self):
            return "x" * 40000

    server = GameServer(bit_registry={"test_bit": HugeResultBit})
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    _completed_round(server)

    [frame] = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert frame["result"] is None
    assert frame["result_dropped"] is True
    assert frame["completion_id"] == str(uuid.UUID(int=1))
    assert frame_size(frame) <= agent.MAX_FRAME_BYTES
    assert journal.entries() == [frame]
    frames = stamped(transport)
    assert [m["seq"] for m in frames] == list(range(1, len(frames) + 1))


def test_bit_completed_still_oversize_after_strip_is_dropped_and_not_journalled(
        tmp_path, monkeypatch, caplog):
    big = [{"dev": f"dev-{i:05d}", "role": "player", "class": "scored"}
           for i in range(1000)]
    monkeypatch.setattr(protocol, "players_view", lambda granted: big)
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()

    _completed_round(server)

    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []
    assert journal.is_empty()
    frames = stamped(transport)
    assert [m["seq"] for m in frames] == list(range(1, len(frames) + 1))
    assert any("oversize" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_oversize_other_frame_is_dropped_and_consumes_no_seq(caplog):
    class HugeRegistry(FakeBitRegistry):
        def list_view(self, *, include_hidden=True):
            return [{"name": "x" * 40000}]

    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, registry=HugeRegistry(),
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()                  # seq 1, 2
    transport.push_incoming({"command": "list_bits"})
    transport.push_incoming({"command": "restart"})   # an error frame next

    agent.poll()

    assert [m["event"] for m in transport.sent] == [
        "state_changed", "admin_devices_applied", "error"]
    assert [m["seq"] for m in transport.sent] == [1, 2, 3]
    assert any("oversize" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_unidentified_journal_entries_are_purged_at_construction(tmp_path):
    path = tmp_path / "j.jsonl"
    keep = dumps(stored("a", "c-a"))
    path.write_text(
        dumps({"event": "bit_completed", "result": None}) + "\n"
        + "not json\n" + keep + "\n")
    journal = Journal(str(path))

    UplinkAgent(GameServer(bit_registry=REGISTRY), FakeTransport(),
                journal=journal, **fixed())

    assert path.read_text() == keep + "\n"


def test_send_failure_right_after_connect_still_backs_off():
    class FailsOnAdminApplied(FakeTransport):
        def __init__(self):
            super().__init__()
            self.fail_count = 0

        def send(self, msg):
            if msg.get("event") == "admin_devices_applied":
                self.fail_count += 1
                self.connected = False
                raise ConnectionError("dropped")
            super().send(msg)

    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FailsOnAdminApplied()
    agent = UplinkAgent(server, transport, time_source=clock, **fixed())

    agent.maintain_connection()                  # must not raise
    assert transport.connect_count == 1 and transport.fail_count == 1
    assert transport.connected is False

    agent.maintain_connection()                  # same tick: backing off
    clock.advance(agent.INITIAL_BACKOFF_SECONDS / 2)
    agent.maintain_connection()
    assert transport.connect_count == 1

    clock.advance(agent.INITIAL_BACKOFF_SECONDS)
    agent.maintain_connection()
    assert transport.connect_count == 2


# --- session_replaced back-off (box contract, Task 5) -----------------------


def replaced_agent(**kwargs):
    clock = FakeClock()
    transport = FakeTransport()
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                        time_source=clock, **fixed(**kwargs))
    agent.maintain_connection()                  # connect at t=0
    return agent, transport, clock


def replace(agent, transport):
    transport.push_incoming({"command": "session_replaced"})
    agent.poll()


def test_session_replaced_closes_and_waits_60_seconds():
    agent, transport, clock = replaced_agent()
    clock.now = 10.0
    replace(agent, transport)
    assert transport.connected is False and transport.close_count == 1

    clock.now = 10.0 + 59.9
    agent.maintain_connection()
    assert transport.connect_count == 1
    clock.now = 10.0 + 60.0
    agent.maintain_connection()
    assert transport.connect_count == 2 and transport.connected is True


def test_repeated_replacement_doubles_to_a_600_second_cap():
    agent, transport, clock = replaced_agent()
    for wait in (60, 120, 240, 480, 600, 600):
        replace(agent, transport)
        start = clock.now
        clock.now = start + wait - 0.1
        agent.maintain_connection()
        assert transport.connected is False, wait
        clock.now = start + wait
        agent.maintain_connection()
        assert transport.connected is True, wait


def test_replaced_wait_resets_after_300_seconds_connected():
    agent, transport, clock = replaced_agent()
    replace(agent, transport)                    # wait 60
    clock.now = 60.0
    agent.maintain_connection()                  # reconnected at 60
    assert transport.connected is True

    clock.now = 60.0 + 299.9
    agent.maintain_connection()
    clock.now = 60.0 + 300.0
    agent.maintain_connection()                  # stable for 300 seconds
    replace(agent, transport)                    # back to 60, not 120
    start = clock.now
    clock.now = start + 59.9
    agent.maintain_connection()
    assert transport.connected is False
    clock.now = start + 60.0
    agent.maintain_connection()
    assert transport.connected is True


def test_ordinary_drop_keeps_the_one_second_backoff():
    agent, transport, clock = replaced_agent()
    replace(agent, transport)                    # raises the replaced wait
    clock.now = 60.0
    agent.maintain_connection()
    transport.disconnect()                       # an ordinary drop

    agent.maintain_connection()                  # first retry is immediate
    assert transport.connect_count == 3
    transport.disconnect()
    agent.maintain_connection()
    assert transport.connect_count == 4
    # Ordinary failures never touch the replaced wait: it is still 120.
    replace(agent, transport)
    start = clock.now
    clock.now = start + 119.9
    agent.maintain_connection()
    assert transport.connected is False
    clock.now = start + 120.0
    agent.maintain_connection()
    assert transport.connected is True


def test_session_replaced_warns_and_notifies_without_the_secret(caplog):
    notes = []
    identity = UplinkIdentity("acme", "lobby-1", "s3cret-value")
    agent, transport, clock = replaced_agent(identity=identity,
                                             notify=notes.append)
    with caplog.at_level(logging.WARNING, logger="uplink.link"):
        replace(agent, transport)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "another box is using this identity" in r.getMessage()]
    assert len(warnings) == 1
    assert len(notes) == 1
    assert "acme" in notes[0] and "lobby-1" in notes[0]
    assert "s3cret-value" not in notes[0]
    assert "s3cret-value" not in caplog.text


# --- set_admin_devices (box contract spec 4.10, issue 100) -------------------

from control.admin_devices_store import APPLIED_FILENAME, AdminDevicesStore
from control.lobby import TERRARIUM_ADMIN


def manifest(*, version=7, devices=None, **overrides):
    """The issue 100 payload for tenant mm, terrarium main-stage, as the
    broker sends it (the manifest plus "command")."""
    if devices is None:
        devices = [("A", "active"), ("B", "lost")]
    msg = {
        "command": "set_admin_devices", "schema": 1, "tenant_slug": "mm",
        "terrarium_name": "main-stage", "version": version,
        "issued_at": "2026-09-11T18:04:00Z",
        "issued_by": {"account_id": "acct-1", "handle": "chris"},
        "devices": [{"gem_uid": uid, "status": status, "player_handle": "p",
                     "added_at": "2026-09-10T12:00:00Z",
                     "added_by_handle": "chris"}
                    for uid, status in devices],
    }
    msg.update(overrides)
    return msg


def admin_agent(tmp_path, *, store=True, **kwargs):
    server = GameServer(bit_registry=REGISTRY, admin_devices=["old-admin"])
    transport = FakeTransport()
    admin_store = (AdminDevicesStore(str(tmp_path / APPLIED_FILENAME))
                   if store else None)
    kwargs.setdefault("identity", UplinkIdentity("mm", "main-stage", "s" * 64))
    agent = UplinkAgent(server, transport, admin_store=admin_store,
                        time_source=FakeClock(), **fixed(**kwargs))
    transport.connect()
    return agent, server, transport, admin_store


def deliver(agent, transport, msg):
    transport.sent.clear()
    transport.push_incoming(msg)
    agent.poll()
    return strip(transport.sent)


def test_valid_manifest_applies_active_devices_and_reports_version(tmp_path):
    agent, gs, transport, store = admin_agent(tmp_path)

    sent = deliver(agent, transport, manifest())

    assert gs.is_admin("A")
    assert not gs.is_admin("B")
    assert gs.is_admin(TERRARIUM_ADMIN)
    assert not gs.is_admin("old-admin")
    assert store.version == 7
    assert store.devices == ("A",)
    assert sent == [{"event": "admin_devices_applied", "version": 7}]


def test_manifest_with_no_devices_leaves_only_the_terrarium_admin(tmp_path):
    agent, gs, transport, store = admin_agent(tmp_path)

    sent = deliver(agent, transport, manifest(version=3, devices=[]))

    assert gs.admin_devices == frozenset({TERRARIUM_ADMIN})
    assert store.version == 3
    assert store.devices == ()
    assert sent == [{"event": "admin_devices_applied", "version": 3}]


@pytest.mark.parametrize("version", [7, 6, 0])
def test_same_or_older_version_is_a_silent_noop(tmp_path, version):
    agent, gs, transport, store = admin_agent(tmp_path)
    deliver(agent, transport, manifest())
    path = tmp_path / APPLIED_FILENAME
    before = path.read_text()

    sent = deliver(agent, transport, manifest(
        version=version, devices=[("C", "active")]))

    assert sent == []
    assert path.read_text() == before
    assert store.version == 7
    assert store.devices == ("A",)
    assert gs.is_admin("A") and not gs.is_admin("C")


REFUSALS = {
    "schema-2": (manifest(schema=2), {}),
    "wrong-tenant": (manifest(tenant_slug="crf"), {}),
    "wrong-terrarium": (manifest(terrarium_name="side-stage"), {}),
    "string-version": (manifest(version="7"), {}),
    "bool-version": (manifest(version=True), {}),
    "devices-not-a-list": (manifest() | {"devices": {"gem_uid": "A"}}, {}),
    "reserved-id-even-lost": (manifest(devices=[("A", "active"),
                                                (TERRARIUM_ADMIN, "lost")]), {}),
    "device-missing-gem-uid": (
        manifest() | {"devices": [{"status": "active"}]}, {}),
    "no-admin-store": (manifest(), {"store": False}),
    "no-identity": (manifest(), {"identity": None}),
}


@pytest.mark.parametrize("msg,kwargs", REFUSALS.values(), ids=REFUSALS.keys())
def test_refused_manifest(tmp_path, msg, kwargs):
    agent, gs, transport, store = admin_agent(tmp_path, **kwargs)
    before = gs.admin_devices

    sent = deliver(agent, transport, msg)

    assert len(sent) == 1
    assert sent[0]["event"] == "error"
    assert sent[0]["command"] == "set_admin_devices"
    assert isinstance(sent[0]["message"], str) and sent[0]["message"]
    assert gs.admin_devices == before
    assert not (tmp_path / APPLIED_FILENAME).exists()
    if store is not None:
        assert store.version == 0 and store.devices is None


def test_malformed_or_misaddressed_manifest_is_refused_whatever_its_version(
        tmp_path):
    agent, gs, transport, store = admin_agent(tmp_path)
    deliver(agent, transport, manifest(version=7))

    for msg in (manifest(version=2, tenant_slug="crf"),
                manifest(version=2, schema=2),
                manifest(version=2, devices=[(TERRARIUM_ADMIN, "active")])):
        sent = deliver(agent, transport, msg)
        assert [m["event"] for m in sent] == ["error"]
    assert store.version == 7


def test_failed_save_sends_error_and_keeps_the_old_admins(tmp_path):
    agent, gs, transport, store = admin_agent(tmp_path)
    deliver(agent, transport, manifest(version=2, devices=[("A", "active")]))

    def broken_save(devices, version, **identity):
        raise OSError("read-only filesystem")

    store.save = broken_save
    sent = deliver(agent, transport, manifest(version=3,
                                              devices=[("B", "active")]))

    assert [m["event"] for m in sent] == ["error"]
    assert sent[0]["command"] == "set_admin_devices"
    assert gs.is_admin("A") and not gs.is_admin("B")
    assert store.version == 2
    assert store.devices == ("A",)


def test_resync_reports_the_stored_version(tmp_path):
    AdminDevicesStore(str(tmp_path / APPLIED_FILENAME)).save(
        ["A"], 5, tenant_slug="mm", terrarium_name="main-stage")
    store = AdminDevicesStore(str(tmp_path / APPLIED_FILENAME))
    transport = FakeTransport()
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                        admin_store=store, time_source=FakeClock(), **fixed())

    agent.maintain_connection()
    assert strip(transport.sent)[-1] == {
        "event": "admin_devices_applied", "version": 5}

    # A manifest applied later is what the next resync reports.
    agent.identity = UplinkIdentity("mm", "main-stage", "s" * 64)
    deliver(agent, transport, manifest(version=9))
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert strip(transport.sent)[-1] == {
        "event": "admin_devices_applied", "version": 9}


def test_bad_manifests_never_raise_out_of_poll(tmp_path):
    agent, gs, transport, store = admin_agent(tmp_path)
    for msg in ({"command": "set_admin_devices"},
                manifest() | {"devices": [None, 3, "A"]},
                manifest() | {"devices": [{"gem_uid": ["A"], "status": "active"}]},
                manifest() | {"devices": [{"gem_uid": "", "status": "active"}]},
                manifest() | {"devices": [{"gem_uid": "A", "status": None}]},
                manifest(tenant_slug=["mm"]),
                manifest(version=7.0),
                manifest(version=None)):
        sent = deliver(agent, transport, msg)
        assert [m["event"] for m in sent] == ["error"], msg
    assert store.version == 0


# --- final fix wave (2026-11-23 whole-branch review) ------------------------

def test_valid_manifest_is_saved_with_the_agents_identity(tmp_path):
    import json
    agent, gs, transport, store = admin_agent(tmp_path)
    deliver(agent, transport, manifest())
    data = json.loads((tmp_path / APPLIED_FILENAME).read_text())
    assert data == {"version": 7, "devices": ["A"], "tenant_slug": "mm",
                    "terrarium_name": "main-stage"}
    reread = AdminDevicesStore(str(tmp_path / APPLIED_FILENAME),
                               tenant_slug="mm", terrarium_name="main-stage")
    assert reread.version == 7 and reread.devices == ("A",)


# item 2: undecodable journal bytes must not stop boot

def test_undecodable_journal_line_is_purged_and_valid_entries_replayed(
        tmp_path, caplog):
    path = tmp_path / "j.jsonl"
    path.write_bytes(dumps(stored("a", "c-a", seq=1)).encode() + b"\n"
                     + b"\xff\xfe\n"
                     + dumps(stored("b", "c-b", seq=2)).encode() + b"\n")
    journal = Journal(str(path))
    transport = FakeTransport()
    with caplog.at_level(logging.WARNING):
        agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                            journal=journal, time_source=FakeClock(), **fixed())
    assert any("dropped 1 journal entries" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    assert b"\xff" not in path.read_bytes()
    assert "�" not in path.read_text()

    agent.maintain_connection()
    assert [m["completion_id"] for m in transport.sent
            if m["event"] == "bit_completed"] == ["c-a", "c-b"]


class ValueErrorJournal(Journal):
    def purge_unidentified(self):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    def append(self, event):
        raise ValueError("bad bytes")

    def remove(self, completion_ids):
        raise ValueError("bad bytes")


def test_value_errors_from_the_journal_never_escape_the_agent(tmp_path):
    journal = ValueErrorJournal(str(tmp_path / "j.jsonl"))
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())   # purge: no raise
    agent.maintain_connection()
    _completed_round(server)                                  # append: no raise
    assert [m["event"] for m in transport.sent].count("bit_completed") == 1
    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["x"]})
    agent.poll()                                              # remove: no raise


# item 3: a completion is journalled even if the preceding send raises

class RaisesOnTransport(FakeTransport):
    """Raises on the first send matching `when`; marks the link down when
    `drop` is set (the real transport does) and leaves it up otherwise."""

    def __init__(self, when, *, drop=True):
        super().__init__()
        self._when = when
        self._drop = drop
        self.raised = 0

    def send(self, msg):
        if not self.raised and self._when(msg):
            self.raised += 1
            if self._drop:
                self.connected = False
            raise ConnectionError("socket closed")
        super().send(msg)


def test_completion_is_journalled_when_the_completing_state_send_raises(
        tmp_path):
    transport = RaisesOnTransport(
        lambda m: m.get("event") == "state_changed"
        and m.get("state") == "COMPLETING")
    server = GameServer(bit_registry=REGISTRY)
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    _completed_round(server)

    assert transport.raised == 1
    [entry] = journal.entries()
    assert entry["event"] == "bit_completed"
    assert entry["boot_id"] == "boot-1" and isinstance(entry["seq"], int)
    assert entry["completion_id"] == str(uuid.UUID(int=1))


def test_wire_order_is_unchanged_when_nothing_fails():
    agent, server, transport = make_agent()
    _completed_round(server)
    events = [(m["event"], m.get("state")) for m in transport.sent]
    i = events.index(("state_changed", "COMPLETING"))
    assert events[i + 1] == ("bit_completed", None)


@pytest.mark.parametrize("drop", [True, False], ids=["link-dropped", "link-up"])
def test_failed_live_bit_completed_send_warns_once_and_stays_journalled(
        tmp_path, caplog, drop):
    transport = RaisesOnTransport(lambda m: m.get("event") == "bit_completed",
                                  drop=drop)
    server = GameServer(bit_registry=REGISTRY)
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    with caplog.at_level(logging.WARNING, logger="uplink.link"):
        _completed_round(server)
    assert transport.raised == 1
    assert [e["completion_id"] for e in journal.entries()] == [
        str(uuid.UUID(int=1))]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "live send failed; kept in the journal for the next connect" in \
        warnings[0].getMessage()
    assert warnings[0].name == "uplink.link"
    assert not warnings[0].exc_info


def test_failed_live_send_does_not_raise_out_of_on_state_change(tmp_path):
    from control.state import State
    transport = RaisesOnTransport(lambda m: m.get("event") == "bit_completed",
                                  drop=False)
    server = GameServer(bit_registry=REGISTRY)
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal,
                        time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    server.load_bit("test_bit")
    agent.on_state_change(State.RUNNING, State.COMPLETING)   # must not raise
    assert transport.raised == 1
    assert len(journal.entries()) == 1


# item 6: connect outcomes are visible

def _invalid_status(code):
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response
    return InvalidStatus(Response(code, "Unauthorized", Headers()))


class ConnectRaisesTransport(FakeTransport):
    def __init__(self, exc):
        super().__init__()
        self._exc = exc

    def connect(self):
        self.connect_count += 1
        raise self._exc


def test_a_401_logs_the_status_and_the_hint_and_not_the_secret(caplog):
    ident = UplinkIdentity("mm", "main-stage", "s3cr3t-" * 6)
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY),
                        ConnectRaisesTransport(_invalid_status(401)),
                        identity=ident, time_source=FakeClock(), **fixed())
    with caplog.at_level(logging.DEBUG):
        agent.maintain_connection()
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    text = warning.getMessage()
    assert "401" in text and "InvalidStatus" in text
    assert "check [uplink] tenant_slug and secret, and [terrarium] name" in text
    assert ident.secret not in caplog.text


def test_another_http_status_is_logged_without_the_401_hint(caplog):
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY),
                        ConnectRaisesTransport(_invalid_status(503)),
                        time_source=FakeClock(), **fixed())
    with caplog.at_level(logging.WARNING):
        agent.maintain_connection()
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "503" in warning.getMessage()
    assert "tenant_slug" not in warning.getMessage()


@pytest.mark.parametrize("exc", [ConnectionRefusedError(61, "refused"),
                                 OSError(8, "nodename nor servname")],
                         ids=["refused", "dns"])
def test_a_generic_connect_failure_names_its_type(caplog, exc):
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY),
                        ConnectRaisesTransport(exc),
                        time_source=FakeClock(), **fixed())
    with caplog.at_level(logging.WARNING):
        agent.maintain_connection()
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert type(exc).__name__ in warning.getMessage()
    assert "retrying in 1.0s" in warning.getMessage()


def test_each_successful_connect_logs_one_info_line(caplog):
    ident = UplinkIdentity("mm", "main-stage", "s3cr3t-" * 6)
    transport = FakeTransport()
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                        identity=ident, time_source=FakeClock(), **fixed())
    with caplog.at_level(logging.INFO, logger="uplink.link"):
        agent.maintain_connection()
        agent.maintain_connection()              # already up: no new line
        transport.disconnect()
        agent.maintain_connection()
    infos = [r for r in caplog.records if r.levelno == logging.INFO
             and "uplink connected" in r.getMessage()]
    assert len(infos) == 2
    assert ident.secret not in caplog.text


def test_connect_comment_no_longer_says_a_rejected_secret_drops_a_frame():
    import inspect
    source = inspect.getsource(UplinkAgent.maintain_connection)
    assert "rejected secret looks exactly like this" not in source


# item 7 at the agent: frames left on a closed link are drained before a
# reconnect

def test_session_replaced_buffered_before_a_failed_send_still_backs_off():
    clock = FakeClock()
    transport = RaisesOnTransport(lambda m: m.get("event") == "heartbeat")
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                        time_source=clock, **fixed())
    agent.maintain_connection()                  # connected at t=0
    transport.push_incoming({"command": "session_replaced"})
    clock.now = 60.0
    agent.maintain_connection()                  # heartbeat send raises
    assert transport.connected is False and transport.raised == 1

    agent.poll()
    agent.maintain_connection()                  # drains, finds the command
    assert transport.connect_count == 1
    clock.now = 60.0 + 59.9
    agent.maintain_connection()
    assert transport.connect_count == 1
    clock.now = 120.0
    agent.maintain_connection()
    assert transport.connect_count == 2


def test_completions_stored_buffered_on_a_closed_link_still_empties_journal(
        tmp_path):
    journal = _journal_with(tmp_path, "a")
    transport = FakeTransport()
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                        journal=journal, time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["c-a"]})
    transport.disconnect()
    agent.maintain_connection()                  # drain, then reconnect
    assert journal.entries() == []
    assert transport.connected is True


# item 8: journal removals are batched per poll

class SpyJournal(Journal):
    def __init__(self, path):
        super().__init__(path)
        self.remove_calls = []

    def remove(self, completion_ids):
        ids = list(completion_ids)
        self.remove_calls.append(ids)
        return super().remove(ids)


def test_fifty_confirmations_in_one_poll_remove_once(tmp_path):
    journal = SpyJournal(str(tmp_path / "j.jsonl"))
    for n in range(50):
        journal.append(stored("x", f"c-{n}", seq=n + 1))
    agent, server, transport = make_agent(journal=journal)
    for n in range(50):
        transport.push_incoming({"command": "completions_stored",
                                 "completion_ids": [f"c-{n}"]})
    agent.poll()
    assert len(journal.remove_calls) == 1
    assert sorted(journal.remove_calls[0]) == sorted(f"c-{n}" for n in range(50))
    assert journal.entries() == []


def test_no_confirmations_means_no_remove(tmp_path):
    journal = SpyJournal(str(tmp_path / "j.jsonl"))
    agent, server, transport = make_agent(journal=journal)
    transport.push_incoming({"command": "list_bits"})
    agent.poll()
    agent.poll()
    assert journal.remove_calls == []


def test_confirmations_are_removed_when_the_drain_ends_on_a_receive_error(
        tmp_path):
    class ThenRaises(FakeTransport):
        def receive(self):
            msg = super().receive()
            if msg is None:
                raise ConnectionError("recv failed")
            return msg

    journal = SpyJournal(str(tmp_path / "j.jsonl"))
    for name in ("a", "b"):
        journal.append(stored(name, f"c-{name}"))
    transport = ThenRaises()
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                        journal=journal, time_source=FakeClock(), **fixed())
    transport.connect()
    for name in ("a", "b"):
        transport.push_incoming({"command": "completions_stored",
                                 "completion_ids": [f"c-{name}"]})
    agent.poll()                                 # must not raise
    assert journal.remove_calls == [["c-a", "c-b"]]
    assert journal.entries() == []


def test_confirmations_are_removed_when_the_drain_ends_on_session_replaced(
        tmp_path):
    journal = SpyJournal(str(tmp_path / "j.jsonl"))
    for name in ("a", "b"):
        journal.append(stored(name, f"c-{name}"))
    agent, server, transport = make_agent(journal=journal)
    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["c-a"]})
    transport.push_incoming({"command": "session_replaced"})
    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["c-b"]})
    agent.poll()
    assert transport.connected is False
    assert journal.remove_calls == [["c-a"]]
    assert [e["completion_id"] for e in journal.entries()] == ["c-b"]


def test_a_command_whose_reply_send_raises_does_not_stop_the_drain(tmp_path):
    journal = SpyJournal(str(tmp_path / "j.jsonl"))
    journal.append(stored("a", "c-a"))
    transport = RaisesOnTransport(lambda m: m.get("event") == "error")
    agent = UplinkAgent(GameServer(bit_registry=REGISTRY), transport,
                        journal=journal, time_source=FakeClock(), **fixed())
    agent.maintain_connection()
    transport.push_incoming({"command": "restart"})      # replies with an error
    transport.push_incoming({"command": "completions_stored",
                             "completion_ids": ["c-a"]})
    agent.poll()                                         # must not raise
    assert transport.raised == 1
    assert journal.entries() == []


# item 10: a repeated gem_uid refuses the manifest; schema must be an int

@pytest.mark.parametrize("devices", [
    [("A", "active"), ("A", "lost")],
    [("A", "lost"), ("A", "active")],
    [("A", "active"), ("A", "active")],
    [("A", "lost"), ("A", "lost")],
], ids=["active-then-lost", "lost-then-active", "both-active", "both-lost"])
def test_a_repeated_gem_uid_refuses_the_whole_manifest(tmp_path, devices):
    agent, gs, transport, store = admin_agent(tmp_path)
    before = gs.admin_devices
    sent = deliver(agent, transport, manifest(devices=devices))
    assert sent == [{"event": "error", "command": "set_admin_devices",
                     "message": "duplicate gem_uid"}]
    assert gs.admin_devices == before
    assert store.version == 0
    assert not (tmp_path / APPLIED_FILENAME).exists()


def test_a_repeated_gem_uid_is_refused_whatever_the_version(tmp_path):
    agent, gs, transport, store = admin_agent(tmp_path)
    deliver(agent, transport, manifest(version=7))
    sent = deliver(agent, transport, manifest(
        version=3, devices=[("A", "active"), ("A", "lost")]))
    assert [m["message"] for m in sent] == ["duplicate gem_uid"]


@pytest.mark.parametrize("schema", [True, 1.0, "1", None])
def test_schema_must_be_the_integer_1(tmp_path, schema):
    agent, gs, transport, store = admin_agent(tmp_path)
    sent = deliver(agent, transport, manifest(schema=schema))
    assert [m["event"] for m in sent] == ["error"]
    assert sent[0]["message"] == "unsupported schema"
    assert store.version == 0
