"""ShroomClient: the Radxa's devicelink participation, tested without a socket."""

from __future__ import annotations

import asyncio

import pytest

from devicelink import protocol
from harness.shroom_client import LED_CHANNELS, ShroomClient, pump_tick

DEV = "ie1"
NODE = "node-a"


class FakeLEDs:
    def __init__(self) -> None:
        self.shown: list[bytes] = []
        self.cleared = 0

    def show(self, channels: bytes) -> None:
        self.shown.append(bytes(channels))

    def clear(self) -> None:
        self.cleared += 1


def client(**kw) -> ShroomClient:
    return ShroomClient(DEV, NODE, leds=FakeLEDs(), **kw)


# --- outbound ---

def test_hello_matches_the_wire():
    env = protocol.decode(client().hello())
    assert env.address == "/game/hello"
    assert env.typespec == "s"
    assert env.args == [DEV]


def test_join_carries_dev_and_node():
    env = protocol.decode(client().join())
    assert env.address == "/game/join"
    assert env.typespec == "ss"
    assert env.args == [DEV, NODE]


def test_tilt_carries_dev_and_a_float():
    env = protocol.decode(client().tilt(0.75))
    assert env.address == "/game/tilt"
    assert env.typespec == "sf"
    assert env.args[0] == DEV
    assert env.args[1] == pytest.approx(0.75)


def test_tilt_coerces_an_int_to_float():
    env = protocol.decode(client().tilt(1))
    assert isinstance(env.args[1], float)


def test_every_outbound_message_survives_a_decode_round_trip():
    c = client()
    for msg in (c.hello(), c.join(), c.tilt(0.1)):
        assert protocol.decode(msg).address.startswith("/game/")


def test_outbound_addresses_parse_as_game_verbs():
    """The agent routes on parse_game_address; anything else is dropped."""
    c = client()
    assert protocol.parse_game_address(
        protocol.decode(c.hello()).address) == "hello"
    assert protocol.parse_game_address(
        protocol.decode(c.join()).address) == "join"
    assert protocol.parse_game_address(
        protocol.decode(c.tilt(0.0)).address) == "tilt"


def test_dev_is_the_first_arg_of_every_outbound_message():
    """The agent reads args[0] as the device id and drops messages without it."""
    c = client()
    for msg in (c.hello(), c.join(), c.tilt(0.5)):
        assert protocol.decode(msg).args[0] == DEV


# --- inbound ---

def test_role_stores_the_config_blob_verbatim():
    c = client()
    blob = {"bit_name": "test_bit", "role": "player", "light_manifest": {"v": 2}}
    assert c.handle(protocol.role_event(DEV, blob)) == f"/{DEV}/role"
    assert c.config == blob


def test_role_fires_the_on_role_callback():
    seen = []
    c = client(on_role=seen.append)
    blob = {"role": "player"}
    c.handle(protocol.role_event(DEV, blob))
    assert seen == [blob]


def test_leds_are_forwarded_to_the_strip():
    """An untimed frame (the default) displays on arrival -- which, for this
    clock-free client, means at the next tick() of its own render loop."""
    c = client()
    c.handle(protocol.leds_event(DEV, list(range(LED_CHANNELS))))
    c.tick(now=0.0)
    assert c.leds.shown[-1] == bytes(range(LED_CHANNELS))


def test_leds_with_a_wrong_channel_count_are_dropped_not_raised():
    c = client()
    assert c.handle(protocol.leds_event(DEV, list(range(12)))) == ""
    assert c.leds.shown == []


def test_release_clears_the_strip_and_sets_released():
    c = client()
    assert c.handle(protocol.release_event(DEV)) == f"/{DEV}/release"
    assert c.released is True
    assert c.leds.cleared == 1


def test_deny_is_recorded_and_leaves_config_unset():
    c = client()
    assert c.handle(protocol.deny_event(DEV, "full", "try node-b")) == f"/{DEV}/deny"
    assert c.config is None
    assert c.last_deny == ("full", "try node-b")


def test_error_is_recorded():
    c = client()
    c.handle(protocol.error_event(DEV, "join", "missing node"))
    assert c.last_error == ("join", "missing node")


def test_a_client_with_no_leds_still_handles_led_frames():
    c = ShroomClient(DEV, NODE, leds=None)
    assert c.handle(protocol.leds_event(DEV, list(range(LED_CHANNELS)))) == \
        f"/{DEV}/leds"


def test_a_client_with_no_leds_still_handles_release():
    c = ShroomClient(DEV, NODE, leds=None)
    assert c.handle(protocol.release_event(DEV)) == f"/{DEV}/release"
    assert c.released is True


# --- robustness: a malformed frame is dropped, never raised ---

def test_a_message_for_another_device_is_ignored():
    c = client()
    assert c.handle(protocol.leds_event("ie2", list(range(LED_CHANNELS)))) == ""
    assert c.leds.shown == []


def test_a_malformed_envelope_is_dropped():
    c = client()
    assert c.handle({"address": f"/{DEV}/leds", "typespec": "b", "args": []}) == ""


def test_a_non_dict_message_is_dropped():
    c = client()
    assert c.handle("not a message") == ""


def test_none_is_dropped():
    c = client()
    assert c.handle(None) == ""


def test_an_unknown_address_is_dropped():
    c = client()
    msg = protocol.encode(protocol.Envelope(0.0, f"/{DEV}/wat", "", []))
    assert c.handle(msg) == ""


def test_a_role_with_a_non_dict_payload_is_dropped():
    c = client()
    msg = protocol.encode(
        protocol.Envelope(0.0, f"/{DEV}/role", "b", ["not a dict"]))
    assert c.handle(msg) == ""
    assert c.config is None


def test_a_leds_frame_with_a_non_list_payload_is_dropped():
    c = client()
    msg = protocol.encode(protocol.Envelope(0.0, f"/{DEV}/leds", "b", ["nope"]))
    assert c.handle(msg) == ""


def test_led_channel_values_are_masked_to_a_byte():
    c = client()
    c.handle(protocol.leds_event(DEV, [300] * LED_CHANNELS))
    c.tick(now=0.0)
    assert all(v == 300 & 0xFF for v in c.leds.shown[-1])


def test_a_rejoin_clears_the_released_flag():
    c = client()
    c.handle(protocol.release_event(DEV))
    c.join()
    assert c.released is False


# --- timed frames: a device lights up at its declared time, not on arrival ---

def test_a_timestamped_frame_is_held_until_its_time():
    c = client()

    c.handle(protocol.leds_event(DEV, [7] * LED_CHANNELS, when=10.0))
    c.tick(now=9.9)
    assert c.leds.shown == []

    c.tick(now=10.0)
    assert len(c.leds.shown) == 1


def test_an_unstamped_frame_shows_on_the_next_tick():
    """timestamp 0.0 means no declared time, and must not be treated as a
    time far in the past that trips the clamp counter."""
    c = client()

    c.handle(protocol.leds_event(DEV, [7] * LED_CHANNELS))
    c.tick(now=500.0)
    assert len(c.leds.shown) == 1
    assert c.clamped == 0


def test_a_frame_whose_time_has_passed_shows_immediately_and_clamps():
    c = client()

    c.handle(protocol.leds_event(DEV, [7] * LED_CHANNELS, when=5.0))
    c.tick(now=9.0)
    assert len(c.leds.shown) == 1
    assert c.clamped == 1


def test_release_still_clears_immediately():
    """A release must not sit in the queue behind a pending frame: the
    device is being torn down."""
    c = client()
    c.handle(protocol.release_event(DEV))
    assert c.leds.cleared == 1
    assert c.released is True


def test_pending_frames_are_bounded_when_tick_is_never_called():
    """A caller that drives handle() without ever calling tick() -- the gap
    the Task 7 review found in harness/room_simulator.py and this module's
    own main(), both fixed to call tick() now -- must not grow _pending
    without bound. The oldest unrendered frame is dropped instead, mirroring
    devicelink/agent.py's _MAX_CLOSING_FRAMES bound on an analogous
    unrendered backlog."""
    from harness.shroom_client import _MAX_PENDING_FRAMES

    c = client()
    for i in range(_MAX_PENDING_FRAMES + 5):
        c.handle(protocol.leds_event(DEV, [0] * LED_CHANNELS, when=float(i + 1)))

    assert len(c._pending) == _MAX_PENDING_FRAMES
    # the 5 oldest (when 1.0..5.0) were dropped; the oldest survivor is 6.0
    assert c._pending[0][0] == 6.0


# --- pump_tick: the shared asyncio tick-loop, used by main() and
# room_simulator.py -- now a module-level function and therefore testable
# on its own, rather than a closure buried inside main() ---

class _FakeTickClient:
    """Fakes just enough of ShroomClient for pump_tick: a released flag and
    a tick() call. Flips released on its own after a set number of ticks,
    the way a real client's tick() never does -- release only ever comes
    from the server via _on_release -- so the test can assert pump_tick
    notices the flag changing underneath it and stops."""

    def __init__(self, release_after: int) -> None:
        self.released = False
        self.ticks: list[float] = []
        self._release_after = release_after

    def tick(self, now: float) -> None:
        self.ticks.append(now)
        if len(self.ticks) >= self._release_after:
            self.released = True


def test_pump_tick_ticks_the_client_at_the_given_interval():
    client = _FakeTickClient(release_after=3)

    asyncio.run(asyncio.wait_for(pump_tick(client, interval=0.0), timeout=1.0))

    assert len(client.ticks) == 3


def test_pump_tick_exits_once_released_flips_true():
    """No sentinel needed: exiting is exactly what lets asyncio.run above
    return instead of asyncio.wait_for's timeout firing. This test pins the
    other half -- that it stops AT release, not one tick early or late."""
    client = _FakeTickClient(release_after=1)

    asyncio.run(asyncio.wait_for(pump_tick(client, interval=0.0), timeout=1.0))

    assert client.released is True
    assert len(client.ticks) == 1


def test_tap_encodes_the_documented_wire_row():
    client = ShroomClient("ie1", "node-a")
    msg = client.tap(count=2)
    assert msg["address"] == "/game/tap"
    assert msg["typespec"] == "sffi"
    assert msg["args"] == ["ie1", 1.0, 50.0, 2]


def test_default_width_is_still_thirty_six():
    """Every existing caller constructs ShroomClient without this argument
    and must be unaffected."""
    from harness.shroom_client import LED_CHANNELS, ShroomClient
    client = ShroomClient("ie1", "node-a")
    assert client.expected_channels == LED_CHANNELS == 36


def test_a_wider_client_accepts_its_own_width():
    from harness.shroom_client import ShroomClient
    from devicelink import protocol

    class Leds:
        def __init__(self):
            self.shown = []

        def show(self, frame):
            self.shown.append(frame)

        def clear(self):
            self.shown.append(b"")

    leds = Leds()
    client = ShroomClient("sim-room", "", leds=leds, expected_channels=180)

    assert client.handle(protocol.leds_event("sim-room", list(range(180)))) \
        == "/sim-room/leds"
    client.tick(now=1.0)
    assert leds.shown == [bytes(v & 0xFF for v in range(180))]


def test_a_wider_client_drops_a_thirty_six_channel_frame():
    """Dropped, never truncated: rendering a short frame would turn a
    configuration mismatch into a subtly wrong picture instead of a logged
    drop."""
    from harness.shroom_client import ShroomClient
    from devicelink import protocol

    client = ShroomClient("sim-room", "", expected_channels=180)

    assert client.handle(protocol.leds_event("sim-room", list(range(36)))) == ""


def test_a_default_client_drops_a_one_eighty_channel_frame():
    from harness.shroom_client import ShroomClient
    from devicelink import protocol

    client = ShroomClient("ie1", "node-a")

    assert client.handle(protocol.leds_event("ie1", list(range(180)))) == ""
