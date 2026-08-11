from control.room_bridge import FakeRoomAudioSink, FakeRoomLightSink, RoomBridge


def test_unbound_bridge_feed_midi_is_a_noop():
    bridge = RoomBridge()
    bridge.feed_midi(0xB0, 74, 64)   # must not raise


def test_bind_sets_dev():
    bridge = RoomBridge()
    bridge.bind("ie7")
    assert bridge.dev == "ie7"


def test_feed_midi_forwards_to_both_sinks():
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light, audio=audio)

    bridge.feed_midi(0xB0, 74, 64)

    assert light.fed == [(0xB0, 74, 64)]
    assert audio.fed == [(0xB0, 74, 64)]


def test_feed_midi_with_only_light_sink_skips_audio():
    light = FakeRoomLightSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light)

    bridge.feed_midi(0x90, 60, 100)

    assert light.fed == [(0x90, 60, 100)]


def test_release_clears_light_and_unbinds():
    light = FakeRoomLightSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light)

    bridge.release()

    assert light.cleared is True
    assert bridge.dev is None
    bridge.feed_midi(0xB0, 74, 64)   # unbound again: must not raise or re-feed
    assert light.fed == []


def test_shutdown_calls_audio_shutdown_then_releases():
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light, audio=audio)

    bridge.shutdown()

    assert audio.shut is True
    assert light.cleared is True
    assert bridge.dev is None
