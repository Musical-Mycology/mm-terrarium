import logging

from uplink.transport import FakeTransport, LogTransport, WebSocketTransport


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


def test_fake_transport_is_durable_by_default_and_settable():
    assert FakeTransport().durable is True
    assert FakeTransport(durable=False).durable is False


def test_websocket_transport_is_durable():
    assert WebSocketTransport("ws://127.0.0.1:1/").durable is True


def test_log_transport_connects_is_not_durable_and_receives_nothing():
    t = LogTransport()
    assert t.connected is False and t.durable is False
    t.connect()
    assert t.connected is True
    assert t.receive() is None


def test_log_transport_logs_frames_as_is(caplog):
    t = LogTransport()
    t.connect()
    with caplog.at_level(logging.DEBUG, logger="uplink.transport"):
        t.send({"event": "state_changed", "state": "IDLE"})
    assert '"state": "IDLE"' in caplog.text or '"state":"IDLE"' in caplog.text


def test_fake_transport_close_counts_and_disconnects():
    t = FakeTransport()
    t.connect()
    t.close()
    t.close()
    assert t.connected is False
    assert t.close_count == 2


def test_log_transport_close_never_raises():
    t = LogTransport()
    t.connect()
    t.close()
    t.close()
    assert t.connected is False


def test_frame_size_is_the_utf8_wire_length():
    from control.wire_json import dumps
    from uplink.transport import frame_size

    msg = {"a": "é"}
    assert frame_size(msg) == len(dumps(msg).encode("utf-8"))
