import argparse
import time

from bits.test_bit import RUN_DURATION_SECONDS, TestBit
from control.arco_process import FakePopen
from control.audio import AudioBridge, FakePool
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType
from control.state import State
from harness.terrarium_boot import _run_duration, _timed_test_bit_cls, build, shutdown


def _fake_arco(command, popen=None):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True)


def _fake_room_audio():
    return AudioBridge(FakePool())


def _build_with_fakes(config, *, transport=None, clock=time.monotonic):
    """Shared fake-injecting build() call for tests that don't need to
    inspect a specific fake's recorded calls afterward (contrast the tests
    below, which construct their own FakePopen so they can assert on it
    post-shutdown). clock defaults to build()'s own default, so existing
    callers that don't pass one see no change in behavior."""
    return build(
        config, {"TestBit": TestBit},
        arco_command=["arco-server"], room_binding=RoomBindingRegistry(),
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio(),
        transport=transport, clock=clock)


def test_build_wires_devicelink_room_bridge_and_simulator():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, simulator = _build_with_fakes(config)

    assert gs.room.bound_dev == "sim-room"
    assert agent._room_light is not None
    assert server.port != 0   # devicelink server actually bound before boot() ran

    shutdown(gs, agent, arco, simulator)


def test_devicelink_server_starts_before_boot_spawns_the_simulator():
    """The whole point of building devicelink first (see design spec section
    6): by the time boot()'s simulator_factory spawns the subprocess, the
    server it needs to connect to already exists. Assert the ordering
    directly via the fake simulator Popen's recorded launch args."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    sim_popen = FakePopen()

    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=sim_popen,
        room_audio=_fake_room_audio())

    launched_command = sim_popen.commands[0]
    assert f"ws://127.0.0.1:{server.port}/ws" in launched_command
    shutdown(gs, agent, arco, simulator)


def test_shutdown_tears_down_arco_and_simulator():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    fake_arco_popen = FakePopen()
    sim_popen = FakePopen()

    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=fake_arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())
    gs.run()

    shutdown(gs, agent, arco, simulator)

    assert gs.state == State.IDLE
    assert fake_arco_popen.signals
    assert sim_popen.signals


def test_build_passes_the_configured_horizon_to_the_agent():
    """The horizon lives in one place. An agent built with its own default
    would silently disagree with the audio path's scheduling."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit",
                        cue_horizon=0.075)
    gs, server, agent, arco, sim = _build_with_fakes(config)
    try:
        assert agent._horizon == 0.075
    finally:
        shutdown(gs, agent, arco, sim)


def test_build_can_run_the_agent_on_the_o2lite_transport():
    """The whole point of the slice: device traffic crosses the Arco hub.
    A FakeO2Lite stands in for the connection pyarco owns, so this asserts
    the wiring with no Arco and no o2litepy."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

    fake = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake)

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, sim = _build_with_fakes(config,
                                                     transport=transport)
    try:
        assert agent.server is transport
        assert fake.services == "actl,game"
    finally:
        shutdown(gs, agent, arco, sim)


def test_build_passes_the_supplied_clock_to_the_agent():
    """main()'s o2lite branch hands build() o2lite.time_get so Control
    stamps frames on the same clock the device ticks against -- see
    build()'s clock= docstring. This is the wiring seam that fix depends
    on; assert it directly rather than only through end-to-end behavior."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    fake_clock = lambda: 45.0
    gs, server, agent, arco, sim = _build_with_fakes(config, clock=fake_clock)
    try:
        assert agent._clock is fake_clock
    finally:
        shutdown(gs, agent, arco, sim)


def test_build_omitting_clock_keeps_the_existing_default():
    """The websocket path (and every existing caller) must see no change:
    omitting clock= leaves build() -- and therefore the agent -- on
    time.monotonic, exactly as before this parameter existed."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, sim = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
        room_audio=_fake_room_audio())
    try:
        assert agent._clock is time.monotonic
    finally:
        shutdown(gs, agent, arco, sim)


def test_build_threads_its_clock_into_the_default_room_audio(monkeypatch):
    """room_audio=None's real-pool branch built a plain AudioBridge(pool),
    silently defaulting to AudioBridge's own time.monotonic regardless of
    what clock= build() was given. DeviceLinkAgent._tick_audio() ticks
    room_audio against the agent's own clock (devicelink/agent.py), and a
    welcome cue's due time is set (at on_grant) against room_audio's own
    clock -- those two have to agree, or a cue's expiry check runs against
    the wrong clock the same way frame stamping did before build() grew
    clock= at all. FakeArcoSynthPool substitutes for the real one (see
    harness/arco_synth.py) so this never touches pyarco or a live Arco
    server; only AudioBridge's clock= is under test here."""
    class _FakeArcoSynthPool:
        def __init__(self, soundfont=None):
            pass

        def start(self) -> None:
            pass

    captured = {}

    def _capturing_audio_bridge(pool, clock=time.monotonic, **kwargs):
        captured["clock"] = clock
        return AudioBridge(FakePool(), clock=clock)

    # Both patched at their defining module: build()'s `from X import Y`
    # inside the function body resolves against X's namespace at call time
    # (it is a lazy import, run fresh on every build() call -- see the
    # module's own docstring on why), not against harness.terrarium_boot's.
    monkeypatch.setattr("harness.arco_synth.ArcoSynthPool", _FakeArcoSynthPool)
    monkeypatch.setattr("control.audio.AudioBridge", _capturing_audio_bridge)
    fake_clock = lambda: 45.0

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, sim = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
        clock=fake_clock)   # room_audio omitted: exercises the default branch
    try:
        assert captured["clock"] is fake_clock
    finally:
        shutdown(gs, agent, arco, sim)


def test_o2lite_frame_is_released_across_the_shared_clock():
    """Regression for a live-demo bug: on the o2lite transport, a device
    never displayed any LED frame, ever. Cause was a clock-base mismatch --
    devicelink/agent.py stamps `when = self._clock() + self._horizon`
    (agent.py:253); pre-fix, build() always defaulted DeviceLinkAgent's
    clock to time.monotonic (hundreds of thousands of seconds on a
    long-uptime machine) regardless of transport, while
    harness/o2_shroom.py ticks the device with the O2 clock
    (o2lite.time_get(), which starts near zero when Arco boots).
    control/timed_queue.py held every frame forever because the two ends
    disagreed about what time meant -- the device and TimedQueue were both
    correct, only the inputs disagreed.

    No existing test could catch this: every one either injects a single
    clock for both "ends", or only inspects the agent's outbound message
    without ever feeding it to a client actually ticked from a different
    clock. FakeO2Lite stands in for the one O2 clock a real device and
    Control genuinely share (o2litepy is a module-level singleton -- design
    spec 2026-08-12 section 5.2); ShroomClient.tick() is the exact client
    code harness/o2_shroom.py drives with o2lite.time_get()."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport, from_o2_arg
    from harness.shroom_client import ShroomClient

    fake_o2 = FakeO2Lite(now=45.0)          # O2 clock starts near zero
    transport = O2LiteTransport()
    transport.start(fake_o2)

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, sim = _build_with_fakes(
        config, transport=transport, clock=fake_o2.time_get)
    try:
        fake_o2.deliver("/game/hello", "s", ("ie1",))
        agent.poll()
        fake_o2.deliver("/game/join", "ss", ("ie1", "TEST_PLAYER_NODE"))
        agent.poll()
        gs.run()

        class _FakeLEDs:
            def __init__(self):
                self.shown = []

            def show(self, channels):
                self.shown.append(bytes(channels))

            def clear(self):
                pass

        leds = _FakeLEDs()
        client = ShroomClient("ie1", "TEST_PLAYER_NODE", leds=leds)

        # The join poll above already rendered and sent one frame (aurora
        # breathes continuously with no cue needed, same as
        # tests/test_devicelink_frames.py's test_emits_a_36_channel_frame).
        # This loop's job is to advance the shared clock far enough past
        # that frame's `when = clock() + horizon` for the device's own
        # tick(now) to actually reach it.
        for _ in range(10):
            fake_o2.set_time(fake_o2.time_get() + 0.02)
            agent.poll()

        led_sends = [entry for entry in fake_o2.sent if entry[0] == "/ie1/leds"]
        assert led_sends, "expected the agent to emit at least one /ie1/leds frame"

        for addr, ts, typespec, args in led_sends:
            decoded = [from_o2_arg(a) if t == "b" else a
                      for t, a in zip(typespec, args)]
            client.handle({"timestamp": ts, "address": addr,
                           "typespec": typespec, "args": decoded})
        client.tick(fake_o2.time_get())

        assert leds.shown, (
            "a frame the agent stamped off the shared O2 clock must be "
            "released once the device's own tick(now), reading that same "
            "clock, reaches its `when` -- pre-fix this stays empty forever, "
            "because the agent's default clock (time.monotonic) never "
            "comes near the O2 clock's small values")
    finally:
        shutdown(gs, agent, arco, sim)


def test_wait_in_setup_polls_for_the_requested_window():
    """A scored role is refused once RUNNING, so a device needs a window to
    join before run() closes it."""
    from harness.terrarium_boot import _wait_in_setup

    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)

    ticks = iter([0.0, 0.1, 0.2, 0.3, 5.0])
    _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                   sleep=lambda _s: None)
    assert len(polls) >= 3


def test_wait_in_setup_returns_immediately_when_not_requested():
    """Default 0 preserves the existing load-straight-into-run behavior."""
    from harness.terrarium_boot import _wait_in_setup

    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)

    _wait_in_setup(FakeAgent(), 0.0, clock=lambda: 0.0,
                   sleep=lambda _s: None)
    assert polls == []


def test_serve_until_done_stops_when_the_bit_completes():
    """The Bit declares itself finished via update(); the driver must
    notice and tear down rather than ticking an unloaded Bit forever."""
    from control.state import State
    from harness.terrarium_boot import _serve_until_done

    class FakeGS:
        def __init__(self):
            self.state = State.RUNNING
            self.ticks = 0

        def tick(self, dt):
            self.ticks += 1
            if self.ticks >= 3:
                self.state = State.IDLE

    class FakeAgent:
        closing = 0

        def poll(self):
            pass

    class FakeArco:
        def poll(self):
            return None

    reason = _serve_until_done(FakeGS(), FakeAgent(), FakeArco(),
                               sleep=lambda _s: None)
    assert reason == "completed"


def test_serve_until_done_stops_when_arco_dies():
    """Fail loud: silent degradation in a venue is worse than a stop."""
    from control.state import State
    from harness.terrarium_boot import _serve_until_done

    class FakeGS:
        state = State.RUNNING

        def tick(self, dt):
            pass

    class FakeAgent:
        closing = 0

        def poll(self):
            pass

    class FakeArco:
        def poll(self):
            return 1                      # exited

    reason = _serve_until_done(FakeGS(), FakeAgent(), FakeArco(),
                               sleep=lambda _s: None)
    assert reason == "arco-exited"


def test_serve_until_done_lets_closing_devices_finish_their_fade():
    """Release is asynchronous: /<dev>/release is only sent after the
    closing fade renders. Exiting the instant state hits IDLE would freeze
    every device on its last frame."""
    from control.state import State
    from harness.terrarium_boot import _serve_until_done

    class FakeGS:
        state = State.IDLE

        def tick(self, dt):
            pass

    class FakeAgent:
        def __init__(self):
            self.closing = 2
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls >= 4:
                self.closing = 0

    class FakeArco:
        def poll(self):
            return None

    agent = FakeAgent()
    _serve_until_done(FakeGS(), agent, FakeArco(), sleep=lambda _s: None)
    assert agent.closing == 0
    assert agent.polls >= 4


def _args(seconds=None, hold=False):
    return argparse.Namespace(seconds=seconds, hold=hold)


def test_run_duration_default_is_test_bit_natural():
    """No flags at all -- the demo run length must stay exactly what it
    is today."""
    assert _run_duration(_args()) == RUN_DURATION_SECONDS


def test_run_duration_seconds_overrides():
    assert _run_duration(_args(seconds=12.0)) == 12.0


def test_run_duration_hold_is_infinite():
    assert _run_duration(_args(hold=True)) == float("inf")


def test_run_duration_hold_beats_seconds():
    """--hold and --seconds together: --hold wins, matching
    harness/devicelink_smoke.py's _run_duration."""
    assert _run_duration(_args(seconds=5.0, hold=True)) == float("inf")


def test_timed_test_bit_cls_carries_duration_and_exposes_room_types():
    """This is the part most likely to break silently: control/boot.py's
    boot() reads bit_cls.room_types off the registry entry BEFORE
    instantiating it, and control/engine.py's GameServer.load_bit() then
    calls bit_cls() with no arguments. Whatever gets registered must
    satisfy both."""
    bit_cls = _timed_test_bit_cls(12.0)

    assert bit_cls.room_types == TestBit.room_types

    bit = bit_cls()
    assert isinstance(bit, TestBit)
    assert bit._run_duration == 12.0


def test_o2_simulator_factory_ties_the_simulator_to_this_process():
    """The only guard that survives an external kill of the parent, which
    is how agent-driven runs are actually terminated and which no teardown
    path can catch. An orphaned Room simulator reconnects to the NEXT Arco
    and claims sim-room before that run's own simulator is even spawned."""
    import os

    from harness.terrarium_boot import _O2SimulatorFactory

    popen = FakePopen()
    factory = _O2SimulatorFactory("arco", popen=popen)

    assert factory() == "sim-room"
    command = popen.commands[0]
    assert "--exit-with-parent" in command
    assert command[command.index("--exit-with-parent") + 1] == str(os.getpid())
