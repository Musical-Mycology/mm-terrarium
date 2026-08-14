import pytest

from devicelink.o2_transport import FakeO2Lite, O2LiteTransport


def _started():
    fake = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake)
    # start() now round-trips a /game/_svcheck handshake through send() to
    # verify ownership (see verify_service_ownership); that handshake is an
    # implementation detail of start() itself, not something the tests that
    # reuse this fixture to assert on device-directed sends care about.
    fake.sent.clear()
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


def test_a_delivered_message_is_only_seen_after_a_pump():
    """Regression for the live failure: on the real device, Control never
    received anything, because nothing in its tick loop ever called
    o2lite.poll(). FakeO2Lite.deliver() now only QUEUES a message --
    matching real o2litepy, which dispatches to a handler solely from
    inside poll() -- so this can only pass if drain_inbound() itself pumps
    before it drains. Fails against a transport whose drain_inbound() is
    just `drained, self._inbound = self._inbound, []` with no poll() call."""
    transport, fake = _started()
    fake.deliver("/game/hello", "s", ("ie1",))
    # Before any pump, the message is queued at the fake but has not
    # reached a handler yet -- the fake's handlers dict was populated by
    # start(), but nothing has dispatched into it.
    assert fake._queue, "expected deliver() to queue rather than dispatch"
    drained = transport.drain_inbound()
    assert len(drained) == 1
    _client, msg = drained[0]
    assert msg["address"] == "/game/hello"
    assert msg["args"] == ["ie1"]


def test_a_raising_poll_does_not_escape_drain_inbound():
    """Boundary rule 2 applies to poll() exactly as it does to send(): a hub
    that has gone away must never propagate an exception into the engine
    tick."""
    transport, fake = _started()

    def _raise():
        raise RuntimeError("o2lite hub is gone")

    fake.poll = _raise
    assert transport.drain_inbound() == []


def test_drain_inbound_before_start_does_not_raise():
    """self._o2 is None until start() runs; draining before then must be a
    quiet no-op, not an AttributeError on None.poll()."""
    transport = O2LiteTransport()
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


def _fake_clock():
    """A clock that only advances when sleep() is called, so a timeout can
    be exhausted without spending real time. Same shape as the helper in
    tests/test_boot.py."""
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    return clock, sleep


def test_a_self_addressed_message_comes_back_when_the_service_is_ours():
    """Boundary rule 4: o2lite send() has NO local short circuit, so a
    message addressed to our own service leaves for the hub and returns
    only if the hub really routes that service to us. That is what makes
    ownership measurable at all."""
    from devicelink.o2_transport import verify_service_ownership

    fake = FakeO2Lite()
    fake.set_services("actl,game")

    assert verify_service_ownership(fake, "game") is True


def test_a_refused_service_never_routes_back():
    """O2 refuses a second claimant with "not from service provider"
    (o2/src/bridge.cpp:231-237) and logs it on the HUB, never telling the
    client. The refused client stays connected and clock-synced and looks
    perfectly healthy, so this round trip is the only thing that can tell
    the two apart."""
    from devicelink.o2_transport import verify_service_ownership

    fake = FakeO2Lite()
    fake.set_services("actl,game")
    fake.refuse("game")
    clock, sleep = _fake_clock()

    assert verify_service_ownership(fake, "game", timeout=2.0,
                                    clock=clock, sleep=sleep) is False


def test_the_fake_withholds_the_loopback_only_for_a_refused_service():
    """Boundary rule 5: a double must never be more permissive than the
    library it stands for. A fake that looped every send back would make
    the ownership check pass in every test while failing live -- the exact
    trap that rule was added for, on this same transport."""
    fake = FakeO2Lite()
    fake.set_services("actl,game")
    fake.refuse("game")
    seen = []
    fake.method_new("/game/_svcheck", "i", True,
                    lambda address, types, info: seen.append(fake.get_int32()),
                    None)

    fake.send_cmd("/game/_svcheck", 0, "i", 7)
    fake.poll()

    assert seen == []


def test_a_send_to_a_service_we_do_not_offer_never_loops_back():
    """Sending to a DEVICE's service must not come back to us. Without
    this the fake would loop every outbound LED frame into Control's own
    inbound queue."""
    fake = FakeO2Lite()
    fake.set_services("actl,game")
    seen = []
    fake.method_new("/ie1/leds", "b", True,
                    lambda address, types, info: seen.append(1), None)

    fake.send("/ie1/leds", 0, "b", [1, 2, 3])
    fake.poll()

    assert seen == []


def test_start_refuses_when_game_is_held_by_another_process():
    """Control's own `game` service has exactly the same exposure as a
    device's: an orphaned Terrarium holding it would make every device
    silently unreachable."""
    fake = FakeO2Lite()
    fake.refuse("game")
    transport = O2LiteTransport()
    clock, sleep = _fake_clock()

    with pytest.raises(RuntimeError, match="game"):
        transport.start(fake, ownership_timeout=1.0, clock=clock, sleep=sleep)
