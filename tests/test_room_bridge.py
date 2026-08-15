from control.room_bridge import FakeRoomAudioSink, FakeRoomLightSink, RoomBridge


def test_unbound_bridge_feeds_are_noops():
    bridge = RoomBridge()
    bridge.feed_light(0xB0, 74, 64)   # must not raise
    bridge.feed_audio(0xB0, 74, 64)   # must not raise


def test_bind_sets_dev():
    bridge = RoomBridge()
    bridge.bind("ie7")
    assert bridge.dev == "ie7"


def test_feed_light_and_feed_audio_are_separately_addressable():
    """The two halves of a Room cue are released at DIFFERENT times against
    one shared `at`: light as early as possible, because the frame it
    renders still has to cross the wire, and audio at `at`, because it
    reaches Arco from Control with no wire in between. A single fan-out call
    could not express that. See the 2026-08-14 spec section 2."""
    bridge = RoomBridge()
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    bridge.bind("sim-room", light=light, audio=audio)

    bridge.feed_light(0xB0, 74, 64)
    assert light.fed == [(0xB0, 74, 64)]
    assert audio.fed == []

    bridge.feed_audio(0xB0, 74, 64)
    assert audio.fed == [(0xB0, 74, 64)]
    assert light.fed == [(0xB0, 74, 64)]


def test_feeds_with_only_a_light_sink_bound_skip_audio():
    bridge = RoomBridge()
    light = FakeRoomLightSink()
    bridge.bind("sim-room", light=light)
    bridge.feed_light(0x90, 60, 100)
    bridge.feed_audio(0x90, 60, 100)     # must not raise
    assert light.fed == [(0x90, 60, 100)]


def test_no_fan_out_call_survives():
    """A method feeding both sinks at once is the one remaining way to lose
    the shared anchor. Removed, not deprecated."""
    assert not hasattr(RoomBridge(), "feed_midi")


def test_release_clears_light_and_unbinds():
    light = FakeRoomLightSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light)

    bridge.release()

    assert light.cleared is True
    assert bridge.dev is None
    bridge.feed_light(0xB0, 74, 64)   # unbound again: must not raise or re-feed
    assert light.fed == []


def test_shutdown_calls_audio_shutdown_then_releases():
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light, audio=audio)

    bridge.shutdown()

    assert audio.shut is True
    assert light.cleared is True
    assert bridge.dev is None
