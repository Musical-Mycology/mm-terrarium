from devicelink.o2_transport import FakeO2Lite, O2LiteTransport


def _started():
    fake = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake)
    return transport, fake


def test_start_registers_game_alongside_actl():
    """set_services REPLACES rather than appends (o2litepy o2lite.py:707),
    so Control must write the whole string. Dropping actl would silently
    stop Arco's control replies -- the failure this test exists to catch."""
    transport, fake = _started()
    assert fake.services == "actl,game"


def test_start_refuses_an_unsynced_clock():
    """time_get() returns -1 until clock sync completes. Scheduling against
    -1 is garbage, so this is a hard error rather than a silent zero."""
    fake = FakeO2Lite(now=-1.0)
    transport = O2LiteTransport()
    try:
        transport.start(fake)
    except RuntimeError as exc:
        assert "clock" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError on an unsynced clock")


def test_inbound_game_messages_are_drained_as_envelopes():
    """o2litepy hands a handler the address with its leading '/' already
    stripped (O2lite_handler.__init__ does address[1:]). The transport must
    re-prefix it, or the agent sees "game/tilt" and drops every frame."""
    transport, fake = _started()
    fake.deliver("/game/tilt", "sf", ("ie1", 30.0))
    drained = transport.drain_inbound()
    assert len(drained) == 1
    _client, msg = drained[0]
    assert msg["address"] == "/game/tilt"
    assert msg["args"] == ["ie1", 30.0]


def test_the_inbound_timestamp_is_carried_from_the_message():
    """The device stamps its gesture at the source (Design Rule 4), and that
    time is what Control adds the horizon to. Losing it here would silently
    reintroduce the upward jitter the whole scheme exists to remove."""
    transport, fake = _started()
    fake.deliver("/game/tilt", "sf", ("ie1", 30.0), timestamp=555.5)
    _client, msg = transport.drain_inbound()[0]
    assert msg["timestamp"] == 555.5


def test_an_inbound_blob_is_decoded_back_to_a_value():
    transport, fake = _started()
    fake.deliver("/game/telemetry", "b", ([1, 2, 3],))
    _client, msg = transport.drain_inbound()[0]
    assert msg["args"] == [[1, 2, 3]]


def test_a_message_with_unreadable_args_is_dropped_not_raised():
    """A malformed frame is "drop this frame", never an engine error."""
    transport, fake = _started()
    fake.deliver("/game/tilt", "Z", ())        # 'Z' is not an O2 type
    assert transport.drain_inbound() == []


def test_draining_twice_does_not_repeat_a_message():
    transport, fake = _started()
    fake.deliver("/game/hello", "s", ("ie1",))
    assert len(transport.drain_inbound()) == 1
    assert transport.drain_inbound() == []


def test_drain_new_clients_is_a_noop():
    """o2lite has no connection to accept: a device is anonymous until it
    says /game/hello. agent.py already tolerates an empty list here."""
    transport, _fake = _started()
    assert transport.drain_new_clients() == []


def test_send_addresses_the_device_service_and_carries_the_timestamp():
    transport, fake = _started()
    transport.bind_dev("ie1", object())
    transport.send("ie1", {"address": "/ie1/leds", "typespec": "b",
                           "args": [[0] * 36], "timestamp": 42.5})
    assert len(fake.sent) == 1
    addr, timestamp, typespec, _args = fake.sent[0]
    assert addr == "/ie1/leds"
    assert timestamp == 42.5
    assert typespec == "b"


def test_an_led_list_is_sent_as_a_blob_of_bytes():
    """o2litepy's _add_blob reads x.size and x.data (o2lite.py's blob
    branch), so a bare Python list raises AttributeError on the wire. 36
    ints become 36 bytes."""
    transport, fake = _started()
    transport.bind_dev("ie1", object())
    transport.send("ie1", {"address": "/ie1/leds", "typespec": "b",
                           "args": [[255, 0, 128] * 12], "timestamp": 0.0})
    _addr, _ts, _typespec, args = fake.sent[0]
    blob = args[0]
    assert blob.size == 36
    assert bytes(blob.data)[:3] == b"\xff\x00\x80"


def test_a_role_config_dict_is_sent_as_utf8_json_in_a_blob():
    """The role blob must stay byte-identical to JoinResult.config, so it
    is serialized whole rather than flattened into typed args."""
    import json

    transport, fake = _started()
    transport.bind_dev("ie1", object())
    config = {"bit_name": "TestBit", "role": "player",
              "light_manifest": {"instruments": []}}
    transport.send("ie1", {"address": "/ie1/role", "typespec": "b",
                           "args": [config], "timestamp": 0.0})
    _addr, _ts, _typespec, args = fake.sent[0]
    assert json.loads(bytes(args[0].data).decode("utf-8")) == config


def test_non_blob_args_pass_through_untouched():
    transport, fake = _started()
    transport.bind_dev("ie1", object())
    transport.send("ie1", {"address": "/ie1/deny", "typespec": "ss",
                           "args": ["role full", "try the jam node"],
                           "timestamp": 0.0})
    _addr, _ts, _typespec, args = fake.sent[0]
    assert args == ("role full", "try the jam node")


def test_send_to_an_unbound_dev_is_a_silent_no_op():
    transport, fake = _started()
    transport.send("nobody", {"address": "/nobody/leds", "typespec": "",
                              "args": [], "timestamp": 0.0})
    assert fake.sent == []


def test_a_dev_id_too_long_for_o2_is_refused():
    """o2litepy refuses a service name over 31 characters, and a dev id IS
    the device's service name. Catch it at bind, not at send."""
    transport, _fake = _started()
    try:
        transport.bind_dev("i" * 32, object())
    except ValueError as exc:
        assert "31" in str(exc)
    else:
        raise AssertionError("expected ValueError on an over-long dev id")


def test_an_empty_dev_id_is_refused():
    transport, _fake = _started()
    try:
        transport.bind_dev("", object())
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on an empty dev id")
