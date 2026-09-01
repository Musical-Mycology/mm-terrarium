from control.room_bridge import FakeRoomLightSink, RoomBridge


def test_unbound_bridge_feed_is_a_noop():
    bridge = RoomBridge()
    bridge.feed_light(0xB0, 74, 64)   # must not raise


def test_bind_sets_dev():
    bridge = RoomBridge()
    bridge.bind("ie7")
    assert bridge.dev == "ie7"


def test_no_fan_out_call_survives():
    """A method feeding both sinks at once is the one remaining way to lose
    the shared anchor. Removed, not deprecated."""
    assert not hasattr(RoomBridge(), "feed_midi")


def test_no_audio_sink_survives_on_this_bridge():
    """The Room's audio channel moved to per-fixture AudioBridge grants
    (devicelink/agent.py); this bridge is light-only now."""
    import inspect

    assert not hasattr(RoomBridge(), "feed_audio")
    assert "audio" not in inspect.signature(RoomBridge.bind).parameters


def test_release_clears_light_and_unbinds():
    light = FakeRoomLightSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light)

    bridge.release()

    assert light.cleared is True
    assert bridge.dev is None
    bridge.feed_light(0xB0, 74, 64)   # unbound again: must not raise or re-feed
    assert light.fed == []


def test_shutdown_releases_the_light_sink():
    light = FakeRoomLightSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light)

    bridge.shutdown()

    assert light.cleared is True
    assert bridge.dev is None


def test_controllers_starts_empty():
    from control.room_bridge import RoomBridge
    assert RoomBridge().controllers == {}


def test_feed_light_records_the_controller_value():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())

    bridge.feed_light(0xB0, 74, 93)

    assert bridge.controllers == {74: 93}


def test_feed_light_keeps_the_latest_value_per_controller():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())

    bridge.feed_light(0xB0, 74, 10)
    bridge.feed_light(0xB0, 11, 55)
    bridge.feed_light(0xB0, 74, 120)

    assert bridge.controllers == {74: 120, 11: 55}


def test_a_note_on_is_not_recorded_as_a_controller():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())

    bridge.feed_light(0x90, 45, 90)

    assert bridge.controllers == {}


def test_controllers_are_recorded_even_with_no_light_sink_bound():
    """The Console reads this whether or not a renderer is attached."""
    from control.room_bridge import RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room")

    bridge.feed_light(0xB0, 74, 42)

    assert bridge.controllers == {74: 42}


def test_release_clears_the_controllers():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())
    bridge.feed_light(0xB0, 74, 93)

    bridge.release()

    assert bridge.controllers == {}
