"""FixtureSink: where one fixture's rendered frames go. Pure unit tests --
no agent, no transport, no luxaeterna."""

from control.fixture_sink import ConsoleFrameSink, DeviceLinkSink


def test_console_sink_forwards_by_fixture_name():
    seen = []
    ConsoleFrameSink("main", lambda name, frame: seen.append((name, frame))
                     ).send_frame(b"\x01\x02\x03", 5.0)
    assert seen == [("main", b"\x01\x02\x03")]


def test_console_sink_swallows_a_failing_console():
    def boom(name, frame):
        raise RuntimeError("console down")
    ConsoleFrameSink("main", boom).send_frame(b"\x00", 1.0)   # must not raise


def test_devicelink_sink_sends_a_leds_event_to_the_bound_dev():
    sent = []

    def leds_event(dev, frame, when=None):
        return {"event": "leds", "dev": dev, "frame": frame, "when": when}
    DeviceLinkSink("sim-room-main", lambda dev, msg: sent.append((dev, msg)),
                   leds_event).send_frame(b"\x07", 9.5)
    assert sent == [("sim-room-main", {"event": "leds", "dev": "sim-room-main",
                                       "frame": b"\x07", "when": 9.5})]


def test_devicelink_sink_swallows_a_failing_send():
    def boom(dev, msg):
        raise OSError("gone")
    DeviceLinkSink("d", boom, lambda dev, frame, when=None: {}).send_frame(b"", 1.0)
