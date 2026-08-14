import pytest

import argparse
import time

from bits.test_bit import RUN_DURATION_SECONDS, TestBit
from control.arco_process import FakePopen
from control.audio import AudioBridge, FakePool
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType
from control.state import State
from control.teardown import TeardownStack
from devicelink.server import DeviceLinkServer
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
    gs, server, agent, arco, teardown = _build_with_fakes(config)

    assert gs.room.bound_dev == "sim-room"
    assert agent._room_light is not None
    assert server.port != 0   # devicelink server actually bound before boot() ran

    shutdown(teardown)


def test_devicelink_server_starts_before_boot_spawns_the_simulator():
    """The whole point of building devicelink first (see design spec section
    6): by the time boot()'s simulator_factory spawns the subprocess, the
    server it needs to connect to already exists. Assert the ordering
    directly via the fake simulator Popen's recorded launch args."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    sim_popen = FakePopen()

    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=sim_popen,
        room_audio=_fake_room_audio())

    launched_command = sim_popen.commands[0]
    assert f"ws://127.0.0.1:{server.port}/ws" in launched_command
    shutdown(teardown)


def test_shutdown_tears_down_arco_and_simulator():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    fake_arco_popen = FakePopen()
    sim_popen = FakePopen()

    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=fake_arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())
    gs.run()

    shutdown(teardown)

    assert gs.state == State.IDLE
    assert fake_arco_popen.signals
    assert sim_popen.signals


def test_shutdown_stops_the_simulator_before_arco():
    """THE bug this slice exists for. shutdown() called control.boot's
    shutdown() first, which ended with arco.shutdown(), so the O2 hub died
    before the Room simulator was asked to stop and the simulator spent its
    last moments on a dead socket. PR #24 corrected both FAILURE paths and
    left this success path wrong, which is why the order is now a
    consequence of registration rather than a list."""
    order = []

    class _RecordingPopen(FakePopen):
        def __init__(self, label):
            super().__init__()
            self._label = label

        def send_signal(self, sig):
            if self.returncode is None:
                order.append(self._label)
            super().send_signal(sig)

    arco_popen = _RecordingPopen("arco")
    sim_popen = _RecordingPopen("simulator")

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())

    shutdown(teardown)

    assert order == ["simulator", "arco"]


def test_shutdown_stops_the_devicelink_server_last(monkeypatch):
    """The Room simulator is a CLIENT of that server, and the server is
    started before boot() precisely so the simulator has something to
    connect to. Started first, therefore stopped last.

    Patched at the class, before build() runs: build() pushes the BOUND
    method (teardown.push("devicelink-server", server.stop)), captured at
    push time, so an instance-attribute monkeypatch applied after build()
    returns would land on the instance and never be seen by that
    already-captured reference -- same reason
    test_teardown_aborts_the_bit_before_the_room_bridge in
    tests/test_boot.py patches RoomBridge at the class, and the same
    pattern test_build_threads_its_clock_into_the_default_room_audio below
    already uses for ArcoSynthPool/AudioBridge.

    Records BOTH the server AND Arco's stop into one shared `order` list,
    mirroring test_shutdown_stops_the_simulator_before_arco above: a lone
    `assert stopped == ["server"]` would keep passing even if the
    devicelink-server step were popped FIRST instead of last, since
    nothing else would touch that list either way. Arco is registered
    second -- right after the devicelink server, inside _boot() -- so
    checking it lands before "server" here is what actually pins down
    "stopped last", not just "stopped once"."""
    order = []
    monkeypatch.setattr(DeviceLinkServer, "stop",
                        lambda self: order.append("server"))

    class _RecordingPopen(FakePopen):
        def send_signal(self, sig):
            if self.returncode is None:
                order.append("arco")
            super().send_signal(sig)

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=_RecordingPopen()),
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())

    shutdown(teardown)

    assert order == ["arco", "server"]


def test_full_o2lite_unwind_order_through_main(monkeypatch):
    """The ordering this whole branch exists to fix, traced end to end
    through the actual --transport o2lite path harness/run_stack.py
    drives -- previously verified only by a throwaway script.

    main() cannot be driven directly here (argparse, a live Arco,
    o2litepy), so this calls build() for real with an adopted
    O2LiteTransport (covering the four build()-level o2lite steps: arco,
    simulator, room-bridge, bit) and then _register_o2lite_transport --
    the exact function main() calls, at the exact point main() calls it:
    after build() returns and after transport.start(). That is the one
    step build()-level tests could not reach on their own.

    NOTE ON "SIX STAGES": the final review's finding described this as a
    six-stage unwind ending in devicelink-server. Checked directly (see
    final-fix-report.md): build() only pushes "devicelink-server" when
    transport is None (websocket mode); in o2lite mode server = transport
    and that step is never pushed at all (confirmed by inspecting
    teardown._steps after a real build(transport=...) call). o2lite-
    transport and devicelink-server are mutually exclusive within one
    run -- one is o2lite mode's own teardown step, the other is what it
    replaces. This test asserts the five steps that actually coexist
    under --transport o2lite, which is the only mode run_stack.py ever
    drives and the one this whole branch is about."""
    from control.engine import GameServer
    from control.room_bridge import RoomBridge
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport
    from harness.terrarium_boot import _register_o2lite_transport

    order = []
    monkeypatch.setattr(RoomBridge, "shutdown",
                        lambda self: order.append("room-bridge"))
    monkeypatch.setattr(GameServer, "abort",
                        lambda self: order.append("bit"))
    monkeypatch.setattr(O2LiteTransport, "stop",
                        lambda self: order.append("o2lite-transport"))

    class _RecordingPopen(FakePopen):
        def __init__(self, label):
            super().__init__()
            self._label = label

        def send_signal(self, sig):
            if self.returncode is None:
                order.append(self._label)
            super().send_signal(sig)

    arco_popen = _RecordingPopen("arco")
    sim_popen = _RecordingPopen("simulator")

    fake_o2 = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake_o2)

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio(),
        transport=transport)

    _register_o2lite_transport(teardown, transport)

    shutdown(teardown)

    assert order == ["o2lite-transport", "bit", "room-bridge", "simulator",
                     "arco"]


def test_shutdown_reports_a_failing_step_without_skipping_the_rest():
    """A guarded stack: one broken teardown step must not orphan Arco."""
    arco_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())

    teardown.push("broken", _boom)

    shutdown(teardown)

    assert arco_popen.signals            # Arco still stopped


def test_build_passes_the_configured_horizon_to_the_agent():
    """The horizon lives in one place. An agent built with its own default
    would silently disagree with the audio path's scheduling."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit",
                        cue_horizon=0.075)
    gs, server, agent, arco, teardown = _build_with_fakes(config)
    try:
        assert agent._horizon == 0.075
    finally:
        shutdown(teardown)


def test_build_can_run_the_agent_on_the_o2lite_transport():
    """The whole point of the slice: device traffic crosses the Arco hub.
    A FakeO2Lite stands in for the connection pyarco owns, so this asserts
    the wiring with no Arco and no o2litepy."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

    fake = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake)

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = _build_with_fakes(config,
                                                     transport=transport)
    try:
        assert agent.server is transport
        assert fake.services == "actl,game"
    finally:
        shutdown(teardown)


def test_build_passes_the_supplied_clock_to_the_agent():
    """main()'s o2lite branch hands build() o2lite.time_get so Control
    stamps frames on the same clock the device ticks against -- see
    build()'s clock= docstring. This is the wiring seam that fix depends
    on; assert it directly rather than only through end-to-end behavior."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    fake_clock = lambda: 45.0
    gs, server, agent, arco, teardown = _build_with_fakes(config, clock=fake_clock)
    try:
        assert agent._clock is fake_clock
    finally:
        shutdown(teardown)


def test_build_omitting_clock_keeps_the_existing_default():
    """The websocket path (and every existing caller) must see no change:
    omitting clock= leaves build() -- and therefore the agent -- on
    time.monotonic, exactly as before this parameter existed."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
        room_audio=_fake_room_audio())
    try:
        assert agent._clock is time.monotonic
    finally:
        shutdown(teardown)


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
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
        clock=fake_clock)   # room_audio omitted: exercises the default branch
    try:
        assert captured["clock"] is fake_clock
    finally:
        shutdown(teardown)


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
    gs, server, agent, arco, teardown = _build_with_fakes(
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
        shutdown(teardown)


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


def test_wait_in_setup_exits_early_when_the_parent_is_gone(monkeypatch):
    """F5: a SIGKILLed or OOM-killed run_stack cannot signal this process,
    so a device joining during SETUP is not the only thing this loop must
    notice -- it must also notice its own supervisor is gone, and say so
    by returning True rather than silently finishing the window.

    Patched at harness.terrarium_boot.parent_is_gone (the name this
    module imported) rather than injecting a fake getppid: same seam
    tests/test_o2_shroom.py's own parent_is_gone tests use directly, one
    level up."""
    from harness.terrarium_boot import _wait_in_setup

    monkeypatch.setattr("harness.terrarium_boot.parent_is_gone",
                        lambda pid: True)
    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)

    ticks = iter([0.0, 0.1, 0.2, 5.0])
    parent_gone = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                                 sleep=lambda _s: None, parent_pid=111)
    assert parent_gone is True
    assert polls == []          # returned before ever polling the agent


def test_wait_in_setup_ignores_a_live_parent():
    """The default shape (no --exit-with-parent) must keep polling for the
    full window -- parent_pid=None is documented on parent_is_gone as
    'the caller did not ask for this guard', and it must never fire."""
    from harness.terrarium_boot import _wait_in_setup

    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)

    ticks = iter([0.0, 0.1, 0.2, 0.3, 5.0])
    parent_gone = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                                 sleep=lambda _s: None)
    assert parent_gone is False
    assert len(polls) >= 3


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


def test_serve_until_done_stops_when_the_parent_is_gone(monkeypatch):
    """F5, the other half: run_stack's own children (devices) already had
    --exit-with-parent; Control -- and through it Arco and the Room
    simulator -- did not, so a SIGKILLed run_stack left the whole rest of
    the stack running un-signalled in its own session. Checked first in
    the loop, same as arco.poll(), so a dead parent is noticed before
    another full tick runs."""
    from control.state import State
    from harness.terrarium_boot import _serve_until_done

    monkeypatch.setattr("harness.terrarium_boot.parent_is_gone",
                        lambda pid: True)

    class FakeGS:
        state = State.RUNNING

        def tick(self, dt):
            raise AssertionError("must not tick once the parent is gone")

    class FakeAgent:
        closing = 0

        def poll(self):
            raise AssertionError("must not poll once the parent is gone")

    class FakeArco:
        def poll(self):
            return None

    reason = _serve_until_done(FakeGS(), FakeAgent(), FakeArco(),
                               sleep=lambda _s: None, parent_pid=111)
    assert reason == "parent-gone"


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

    assert factory(TeardownStack()) == "sim-room"
    command = popen.commands[0]
    assert "--exit-with-parent" in command
    assert command[command.index("--exit-with-parent") + 1] == str(os.getpid())


def test_build_tears_down_both_subprocesses_if_room_audio_fails(monkeypatch):
    """_boot() has already spawned Arco AND the simulator by the time
    build() constructs room_audio. If that raises, build() never returns,
    so main() never binds `simulator` and its `finally: shutdown(...)` has
    no handles to work with: both subprocesses outlive the run, and the
    orphaned simulator re-claims sim-room on the next run's Arco.

    ArcoSynthPool.start() raising is not hypothetical -- it is
    arco.initialize(), which raises TimeoutError, and the documented
    macOS /host/clear trap makes a second run on one Arco start fragile by
    design. Patched at its defining module for the same reason
    test_build_threads_its_clock_into_the_default_room_audio does: build()
    imports it lazily inside the function body."""
    class _ExplodingArcoSynthPool:
        def __init__(self, soundfont=None):
            pass

        def start(self) -> None:
            raise TimeoutError("Could not connect to Arco server")

    monkeypatch.setattr("harness.arco_synth.ArcoSynthPool",
                        _ExplodingArcoSynthPool)

    arco_popen = FakePopen()
    sim_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")

    with pytest.raises(TimeoutError):
        build(config, {"TestBit": TestBit}, arco_command=["arco-server"],
              room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
              arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
              simulator_popen=sim_popen)   # room_audio omitted: real branch

    assert sim_popen.signals    # simulator was told to stop, not orphaned
    assert arco_popen.signals   # and so was Arco


def _boom():
    raise OSError("no such process")
