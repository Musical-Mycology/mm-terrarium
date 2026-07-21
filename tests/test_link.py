from bits.test_bit import TestBit
from control.engine import GameServer
from uplink.link import UplinkAgent
from uplink.transport import FakeTransport

REGISTRY = {"test_bit": TestBit}


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
    assert completed == [{"event": "bit_completed", "result": {"score": 99}}]


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

    server.hello("ie1", "Tuneshroom 1", "1.0")
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

    assert transport.sent[0] == {"event": "state_changed", "state": "SETUP"}
    reg_event = transport.sent[1]
    assert reg_event["event"] == "registration_changed"
    roles = {r["role"]: r["count"] for r in reg_event["roles"]}
    assert roles["player"] == 1


def test_resync_omits_registration_snapshot_when_no_bit_loaded():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())

    agent.maintain_connection()

    assert transport.sent == [{"event": "state_changed", "state": "IDLE"}]


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
