from uplink.transport import FakeTransport


def test_fake_transport_starts_disconnected():
    t = FakeTransport()
    assert t.connected is False


def test_connect_sets_connected_and_counts_calls():
    t = FakeTransport()
    t.connect()
    assert t.connected is True
    assert t.connect_count == 1


def test_receive_returns_none_when_empty():
    t = FakeTransport()
    assert t.receive() is None


def test_push_incoming_then_receive_fifo_order():
    t = FakeTransport()
    t.push_incoming({"command": "run"})
    t.push_incoming({"command": "abort"})
    assert t.receive() == {"command": "run"}
    assert t.receive() == {"command": "abort"}
    assert t.receive() is None


def test_send_records_sent_messages():
    t = FakeTransport()
    t.send({"event": "state_changed", "state": "RUNNING"})
    assert t.sent == [{"event": "state_changed", "state": "RUNNING"}]


def test_disconnect_clears_connected_flag():
    t = FakeTransport()
    t.connect()
    t.disconnect()
    assert t.connected is False
