import pytest

import argparse
import sys
import time

from bits.test.test_bit import TestBit
from control.arco_process import FakePopen
from control.audio import AudioBridge, FakePool
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from tests.instrument_fixtures import GENERIC_SURFACE
from control.state import State
from control.teardown import TeardownStack
from control.terrarium_config import RoomSpec
from devicelink.server import DeviceLinkServer
from harness.terrarium_boot import (_LifecycleLogger, _print_join_denied,
                                    _run_duration, build, main,
                                    resolve_room_spec, shutdown)

TEST_PROFILE = RoomProfile(surface_id="room_test", fixtures=(
    RoomFixture(name="main", color_order="GRB",
               blocks=(RoomBlock("main", 0, 60),),
               zones=(RoomZone("left", 0, 20),
                     RoomZone("center", 20, 20),
                     RoomZone("right", 40, 20)), instrument=GENERIC_SURFACE),
    RoomFixture(name="accent", color_order="GRB",
               blocks=(RoomBlock("accent", 0, 30),),
               zones=(RoomZone("low", 0, 15),
                     RoomZone("high", 15, 15)), instrument=GENERIC_SURFACE),
))
TEST_SPEC = RoomSpec(name="TEST", description="", backends=("devicelink",),
                     node_id="ROOM_TEST_NODE", profile=TEST_PROFILE)


def _fake_arco(command, popen=None, record=None):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True,
                       record=record)


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
        room_spec=TEST_SPEC,
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio(),
        transport=transport, clock=clock)


def test_resolve_room_spec_raises_a_located_error_for_an_unknown_room():
    """resolve_room_spec is the successor to the deleted resolve_room_type's
    fail-hard semantics: an unknown room name must raise, with a message
    naming the bad value and listing the valid ones, rather than silently
    falling through."""
    with pytest.raises(SystemExit) as exc:
        resolve_room_spec("NOPE")
    message = str(exc.value)
    assert "unknown room" in message
    assert "NOPE" in message
    assert "DEMO" in message and "TEST" in message


def test_resolve_room_spec_returns_the_named_rooms_spec():
    spec = resolve_room_spec("TEST")
    assert spec.name == "TEST"
    assert spec.node_id == "ROOM_TEST_NODE"


def test_build_wires_devicelink_room_bridge_and_simulator():
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config)

    assert gs.room.bound == {"main": "sim-room-main", "accent": "sim-room-accent"}
    assert agent._room_light is not None
    assert server.port != 0   # devicelink server actually bound before boot() ran

    shutdown(teardown, terrarium)


def test_devicelink_server_starts_before_boot_spawns_the_simulator():
    """The whole point of building devicelink first (see design spec section
    6): by the time boot()'s simulator_factory spawns the subprocess, the
    server it needs to connect to already exists. Assert the ordering
    directly via the fake simulator Popen's recorded launch args."""
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    sim_popen = FakePopen()

    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=sim_popen,
        room_audio=_fake_room_audio())

    launched_command = sim_popen.commands[0]
    assert f"ws://127.0.0.1:{server.port}/ws" in launched_command
    shutdown(teardown, terrarium)


def test_shutdown_tears_down_arco_and_simulator():
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    fake_arco_popen = FakePopen()
    sim_popen = FakePopen()

    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=fake_arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())
    gs.run()

    shutdown(teardown, terrarium)

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

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())

    shutdown(teardown, terrarium)

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

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=_RecordingPopen()),
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())

    shutdown(teardown, terrarium)

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

    THREE unwind phases now (see shutdown()'s own docstring): the o2lite
    transport (registered on its own `pre_room_teardown` stack, exactly as
    main() does -- NOT on the process-level `teardown`) closes FIRST,
    because it is Control's own o2lite CLIENT of the same Arco hub the Room
    simulator also talks to -- "no client outlives the hub it is a guest
    on" (control/teardown.py's own invariant) applies to it just as much
    as to the simulator. Then terrarium.room_stack (bit, room-bridge,
    simulator, arco). Then the remaining process-level `teardown` (empty
    here: o2lite mode pushes no devicelink-server, and this test gives no
    console)."""
    from control.engine import GameServer
    from control.room_bridge import RoomBridge
    from control.teardown import TeardownStack
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

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio(),
        transport=transport)

    pre_room_teardown = TeardownStack()
    _register_o2lite_transport(pre_room_teardown, transport)

    shutdown(teardown, terrarium, pre_room_teardown=pre_room_teardown)

    assert order == ["o2lite-transport", "bit", "room-bridge", "simulator",
                     "arco"]


def test_shutdown_reports_a_failing_step_without_skipping_the_rest():
    """A guarded stack: one broken teardown step must not orphan Arco."""
    arco_popen = FakePopen()
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())

    teardown.push("broken", _boom)

    shutdown(teardown, terrarium)

    assert arco_popen.signals            # Arco still stopped


def test_build_passes_the_configured_horizon_to_the_agent():
    """The horizon lives in one place. An agent built with its own default
    would silently disagree with the audio path's scheduling."""
    config = BootConfig(room_name="TEST", bit_name="TestBit",
                        cue_horizon=0.075)
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config)
    try:
        assert agent._horizon == 0.075
    finally:
        shutdown(teardown, terrarium)


def test_build_threads_terrarium_config_instruments_into_the_game_server():
    """A loaded TerrariumConfig's [instruments.*] must reach GameServer's
    carried_instruments -- otherwise a device's hello can never resolve a
    config-defined instrument name."""
    from control.instrument import Instrument
    from control.terrarium_config import TerrariumConfig

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    venue_array = Instrument(name="venue_array")
    terrarium_config = TerrariumConfig(
        schema=1, name="terrarium-boot", bit_paths=(),
        rooms={TEST_SPEC.name: TEST_SPEC},
        version="terrarium-boot",
        instruments={"venue_array": venue_array})
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit},
        arco_command=["arco-server"], room_binding=RoomBindingRegistry(),
        room_spec=TEST_SPEC, terrarium_config=terrarium_config,
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())
    try:
        assert "venue_array" in gs.carried_instruments
    finally:
        shutdown(teardown, terrarium)


def test_build_can_run_the_agent_on_the_o2lite_transport():
    """The whole point of the slice: device traffic crosses the Arco hub.
    A FakeO2Lite stands in for the connection pyarco owns, so this asserts
    the wiring with no Arco and no o2litepy."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

    fake = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake)

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config,
                                                     transport=transport)
    try:
        assert agent.server is transport
        assert fake.services == "actl,game"
    finally:
        shutdown(teardown, terrarium)


def test_build_passes_the_supplied_clock_to_the_agent():
    """main()'s o2lite branch hands build() o2lite.time_get so Control
    stamps frames on the same clock the device ticks against -- see
    build()'s clock= docstring. This is the wiring seam that fix depends
    on; assert it directly rather than only through end-to-end behavior."""
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    fake_clock = lambda: 45.0
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config, clock=fake_clock)
    try:
        assert agent._clock is fake_clock
    finally:
        shutdown(teardown, terrarium)


def test_build_gives_the_engine_and_the_agent_one_clock_and_one_horizon():
    """Two clock bases is the bug that made the 2026-08-13 live run dark:
    Control stamped frames off time.monotonic while the device ticked on the
    O2 clock, roughly 518,000 against 45, so every frame was queued half a
    million seconds out and none ever displayed. GameServer now reads a clock
    too -- for Bit.cues origins and for the no-stamp fallback -- so the same
    failure is available again unless the two are literally the same
    callable.
    """
    clk = lambda: 4242.0
    config = BootConfig(room_name="TEST", bit_name="TestBit",
                        cue_horizon=0.111)
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config, clock=clk)
    try:
        assert gs._clock is agent._clock is clk
        assert gs._horizon == 0.111
        assert agent._horizon == 0.111
    finally:
        shutdown(teardown, terrarium)


def test_build_omitting_clock_keeps_the_existing_default():
    """The websocket path (and every existing caller) must see no change:
    omitting clock= leaves build() -- and therefore the agent -- on
    time.monotonic, exactly as before this parameter existed."""
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
        room_audio=_fake_room_audio())
    try:
        assert agent._clock is time.monotonic
    finally:
        shutdown(teardown, terrarium)


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

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
        clock=fake_clock)   # room_audio omitted: exercises the default branch
    try:
        assert captured["clock"] is fake_clock
    finally:
        shutdown(teardown, terrarium)


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

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(
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
        shutdown(teardown, terrarium)


def test_wait_in_setup_polls_for_the_requested_window():
    """A scored role is refused once RUNNING, so a device needs a window to
    join before run() closes it."""
    from harness.terrarium_boot import _wait_in_setup

    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)

    ticks = iter([0.0, 0.1, 0.2, 0.3, 5.0])
    reason = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                            sleep=lambda _s: None)
    assert reason == "expired"
    assert len(polls) >= 3


def test_wait_in_setup_returns_immediately_when_not_requested():
    """Default 0 preserves the existing load-straight-into-run behavior."""
    from harness.terrarium_boot import _wait_in_setup

    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)

    reason = _wait_in_setup(FakeAgent(), 0.0, clock=lambda: 0.0,
                            sleep=lambda _s: None)
    assert reason == "expired"
    assert polls == []


def test_wait_in_setup_drains_arco_every_iteration():
    """The 2026-08-20 freeze: nothing drained Arco's pty during the hold,
    so Arco blocked mid-write and the whole room froze (0-byte arco.log
    tee 11 minutes after spawn, static o2debug.log, no drone at RUNNING).
    Every loop that holds while Arco is alive must drain Arco's pty."""
    from harness.terrarium_boot import _wait_in_setup

    class FakeAgent:
        def poll(self):
            pass

    class FakeArco:
        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return None                      # still running

    ticks = iter([0.0, 0.1, 0.2, 0.3, 0.4, 1.1, 1.2])
    arco = FakeArco()
    reason = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                            sleep=lambda s: None, arco=arco)
    assert reason == "expired"
    assert arco.polls >= 4          # once per iteration, not once total


def test_wait_in_setup_returns_state_changed_when_the_engine_leaves_setup():
    """The 2026-08-20 crash: the operator pressed Run on the Console during
    the hold, and main() then called gs.run() into a RUNNING engine ->
    InvalidTransition killed the harness. The hold must yield instead."""
    from control.state import State
    from harness.terrarium_boot import _wait_in_setup

    class FakeAgent:
        def poll(self):
            pass

    class FakeGs:
        def __init__(self):
            self.state = State.SETUP

    gs = FakeGs()
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        if calls["n"] == 3:
            gs.state = State.RUNNING         # operator clicks Run mid-hold
        return calls["n"] * 0.1

    reason = _wait_in_setup(FakeAgent(), 10.0, clock=clock,
                            sleep=lambda s: None, gs=gs)
    assert reason == "state-changed"


def test_wait_in_setup_yields_on_abort_too():
    from control.state import State
    from harness.terrarium_boot import _wait_in_setup

    class FakeAgent:
        def poll(self):
            pass

    class FakeGs:
        state = State.IDLE                   # operator aborted instantly

    reason = _wait_in_setup(FakeAgent(), 10.0, clock=iter(
        [0.0, 0.1]).__next__, sleep=lambda s: None, gs=FakeGs())
    assert reason == "state-changed"


def _make_fake_swap_console_agent(gs, State):
    """Shared fixture for the mid-hold swap tests below: a console_agent
    whose first poll() lands both a queued Abort and a queued LoadBit(
    NewBit) inside that one call -- SETUP -> IDLE -> SETUP with a new
    bit_name, never visible as a state change from outside."""
    class FakeConsoleAgent:
        def __init__(self):
            self.polled = False

        def poll(self):
            if not self.polled:
                self.polled = True
                gs.state = State.IDLE
                gs.bit_name = "NewBit"
                gs.state = State.SETUP

    return FakeConsoleAgent()


def test_wait_in_setup_announces_a_bit_swapped_in_by_one_console_poll(
        capsys):
    """Round-review 2026-08-24 finding: if an Abort and a LoadBit are both
    queued when console_agent.poll() runs, gs goes SETUP -> IDLE -> SETUP
    inside that single poll call. The plain `gs.state is not State.SETUP`
    check never observes the mid-poll dip, so it alone would let the
    swapped-in Bit run with no `round loaded:` line and no "state-changed"
    handoff at all. bit_name changing while state reads SETUP both times
    is the only signal available, so it has to be watched too. serve mode
    (announce_swaps=True, as `_serve_rounds` always passes) must print the
    line."""
    from control.state import State
    from harness.terrarium_boot import _wait_in_setup
    from harness import markers

    class FakeAgent:
        def poll(self):
            pass

    class FakeGs:
        def __init__(self):
            self.state = State.SETUP
            self.bit_name = "OldBit"

    gs = FakeGs()

    reason = _wait_in_setup(FakeAgent(), 10.0, clock=iter(
        [0.0, 0.1]).__next__, sleep=lambda s: None, gs=gs,
        console_agent=_make_fake_swap_console_agent(gs, State),
        announce_swaps=True)

    assert reason == "state-changed"
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines()
             if l.startswith(markers.CONTROL_ROUND_LOADED)]
    assert lines == [f"{markers.CONTROL_ROUND_LOADED} NewBit"]


def test_wait_in_setup_swap_detection_is_silent_in_one_shot_mode(capsys):
    """Round-review 2026-08-24 fix-round-2 finding: the swap-detection
    print above must not fire for a one-shot run (--console-port combined
    with --seconds/--hold makes effective_serve False but still
    constructs a console_agent). The handoff itself ("state-changed")
    must still fire -- only the print is gated -- so main() still calls
    gs.run() (or hands off) correctly for the swapped-in Bit; it just
    never gets a "round loaded:" line, matching one-shot mode's other two
    emit sites."""
    from control.state import State
    from harness.terrarium_boot import _wait_in_setup
    from harness import markers

    class FakeAgent:
        def poll(self):
            pass

    class FakeGs:
        def __init__(self):
            self.state = State.SETUP
            self.bit_name = "OldBit"

    gs = FakeGs()

    reason = _wait_in_setup(FakeAgent(), 10.0, clock=iter(
        [0.0, 0.1]).__next__, sleep=lambda s: None, gs=gs,
        console_agent=_make_fake_swap_console_agent(gs, State),
        announce_swaps=False)

    assert reason == "state-changed"
    out = capsys.readouterr().out
    assert markers.CONTROL_ROUND_LOADED not in out


def test_wait_in_setup_prints_a_countdown(capsys):
    from harness.terrarium_boot import _wait_in_setup

    class FakeAgent:
        def poll(self):
            pass

    t = {"now": 0.0}

    def clock():
        t["now"] += 4.0                      # 4s per iteration
        return t["now"]
    _wait_in_setup(FakeAgent(), 60.0, clock=clock, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert "SETUP open," in out
    assert out.count("SETUP open,") >= 2     # every ~15s across 60s


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
    reason = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                            sleep=lambda _s: None, parent_pid=111)
    assert reason == "parent-gone"
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
    reason = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                            sleep=lambda _s: None)
    assert reason == "expired"
    assert len(polls) >= 3


def test_wait_in_setup_returns_players_met_when_threshold_crossed():
    """A `players` StartCondition ends the hold the instant enough scored
    devices have joined -- Task 8's whole reason for threading `condition`
    and `game_server` through this loop."""
    from control.bit_config import StartCondition
    from harness.terrarium_boot import _wait_in_setup

    class FakeAgent:
        def poll(self):
            pass

    class FakeRole:
        scored = True

    class FakeRoleTable:
        roles = {"player": FakeRole()}

    class FakeBit:
        role_table = FakeRoleTable()

    class FakeRegistration:
        def __init__(self, count):
            self._count = count

        def counts(self):
            return [("player", self._count, None)]

    class FakeGameServer:
        def __init__(self):
            self.bit = FakeBit()
            self.registration = FakeRegistration(0)

    game_server = FakeGameServer()
    ticks = iter([0.0, 0.1, 0.2])

    def clock():
        now = next(ticks)
        if now >= 0.2:
            game_server.registration = FakeRegistration(1)
        return now

    condition = StartCondition(when="players", min_scored=1)
    reason = _wait_in_setup(FakeAgent(), 5.0, clock=clock,
                            sleep=lambda _s: None, condition=condition,
                            game_server=game_server)
    assert reason == "players-met"


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


def test_serve_until_done_reports_restart():
    """A Console RESTART lands mid-poll: gs.abort()+load_bit() (already run
    synchronously inside _handle_command) put the engine back in LOADED
    without this loop ever calling run() again. Only a restart can do
    that while this function is running."""
    from control.state import State
    from harness.terrarium_boot import _serve_until_done

    class FakeGS:
        def __init__(self):
            self.state = State.RUNNING
            self.ticks = 0

        def tick(self, dt):
            self.ticks += 1
            if self.ticks >= 3:
                self.state = State.LOADED     # a restart landed mid-poll

    class FakeAgent:
        closing = 0

        def poll(self):
            pass

    class FakeArco:
        def poll(self):
            return None

    reason = _serve_until_done(FakeGS(), FakeAgent(), FakeArco(),
                               sleep=lambda _s: None)
    assert reason == "restarted"


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


def test_room_down_mid_run_returns_no_room_not_arco_exited():
    """An operator hard-abort unloads the room (Arco dies with it) while
    `_serve_until_done` polls. The terrarium check must win over the
    arco.poll() check or the abort misreports as a crash."""
    from control.state import State
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _serve_until_done

    class FakeGS:
        state = State.RUNNING

        def tick(self, dt):
            raise AssertionError("must not tick once the room is down")

    class FakeAgent:
        closing = 0

        def poll(self):
            raise AssertionError("must not poll once the room is down")

    class FakeArco:
        def poll(self):
            return 1                      # dead process too

    class FakeTerrarium:
        state = TerrariumState.NO_ROOM

    reason = _serve_until_done(FakeGS(), FakeAgent(), FakeArco(),
                               sleep=lambda _s: None,
                               terrarium=FakeTerrarium())
    assert reason == "no-room"


class _FakeLaunch:
    def __init__(self, setup_seconds):
        self.setup_seconds = setup_seconds


class _FakeBitConfig:
    def __init__(self, setup_seconds, start=None):
        self.launch = _FakeLaunch(setup_seconds)
        self.start = start


class _FakeBit:
    def __init__(self, config, role_table=None):
        self.config = config
        self.role_table = role_table


class _FakeArco:
    def poll(self):
        return None


class _FakeAgent:
    closing = 0

    def poll(self):
        pass


class _FakeConsoleAgent:
    """Fires the next scripted (state, action) pair the first time gs is
    found in that state -- a "console load/abort happens now" stand-in,
    gated on engine state so it never fires early (e.g. mid-round)."""

    def __init__(self, gs, script):
        self._gs = gs
        self._script = list(script)
        self.round_ended_calls = []

    def poll(self):
        if not self._script:
            return
        required_state, action = self._script[0]
        if self._gs.state is required_state:
            self._script.pop(0)
            action()

    def announce_round_ended(self, bit_name, reason):
        self.round_ended_calls.append((bit_name, reason))


def test_serve_rounds_cycles_idle_load_run_idle(monkeypatch, capsys):
    """Round 1: IDLE -> (console load) SETUP -> RUNNING -> IDLE. Round 2:
    IDLE -> (console load) SETUP -> operator abort -> IDLE -> parent-gone.
    The second round's operator abort happens DURING the hold, so it never
    reaches run() -- run_calls stays at 1."""
    from harness.terrarium_boot import _serve_rounds

    class FakeGS:
        def __init__(self):
            self.state = State.IDLE
            self.bit = None
            self.bit_name = None
            self.run_calls = 0
            self._tick_count = 0

        def tick(self, dt):
            self._tick_count += 1
            if self.state is State.RUNNING and self._tick_count >= 2:
                self.state = State.IDLE

        def run(self):
            self.run_calls += 1
            self.state = State.RUNNING
            self._tick_count = 0

        def abort(self):
            self.state = State.IDLE

    gs = FakeGS()

    def load_round1():
        gs.bit = _FakeBit(_FakeBitConfig(0.0))
        gs.bit_name = "Round1Bit"
        gs.state = State.SETUP

    def load_round2():
        gs.bit = _FakeBit(_FakeBitConfig(5.0))
        gs.bit_name = "Round2Bit"
        gs.state = State.SETUP

    aborted = {"since": 0, "fired": False}

    def abort_round2():
        gs.abort()
        aborted["fired"] = True

    console_agent = _FakeConsoleAgent(gs, [
        (State.IDLE, load_round1),
        (State.IDLE, load_round2),
        (State.SETUP, abort_round2),
    ])

    def fake_parent_is_gone(pid):
        if not aborted["fired"]:
            return False
        aborted["since"] += 1
        return aborted["since"] > 1

    monkeypatch.setattr("harness.terrarium_boot.parent_is_gone",
                        fake_parent_is_gone)

    reason = _serve_rounds(gs, _FakeAgent(), _FakeArco(),
                           console_agent=console_agent)

    assert reason == "parent-gone"
    assert gs.run_calls == 1
    from harness import markers
    lines = [l for l in capsys.readouterr().out.splitlines()
             if l.startswith(markers.CONTROL_ROUND_LOADED)]
    assert lines == [f"{markers.CONTROL_ROUND_LOADED} Round1Bit",
                      f"{markers.CONTROL_ROUND_LOADED} Round2Bit"]


def test_serve_rounds_honors_players_condition_per_round(monkeypatch):
    """Round 2's Bit config asks for a `players` start condition -- the
    round must start via "players-met" the instant enough scored devices
    join, not by falling through to a timeout."""
    from control.bit_config import StartCondition
    from harness.terrarium_boot import _serve_rounds

    class FakeRole:
        scored = True

    class FakeRoleTable:
        roles = {"player": FakeRole()}

    class FakeRegistration:
        def __init__(self):
            self.count = 0

        def counts(self):
            return [("player", self.count, None)]

    class FakeGS:
        def __init__(self):
            self.state = State.IDLE
            self.bit = None
            self.bit_name = None
            self.registration = FakeRegistration()
            self.run_calls = 0
            self._tick_count = 0

        def tick(self, dt):
            self._tick_count += 1
            if self.state is State.RUNNING and self._tick_count >= 2:
                self.state = State.IDLE

        def run(self):
            self.run_calls += 1
            self.state = State.RUNNING
            self._tick_count = 0

        def abort(self):
            self.state = State.IDLE

    gs = FakeGS()

    def load_round1():
        condition = StartCondition(when="players", min_scored=1)
        gs.bit = _FakeBit(_FakeBitConfig(5.0, start=condition),
                          role_table=FakeRoleTable())
        gs.bit_name = "PlayersBit"
        gs.state = State.SETUP

    def player_joins():
        gs.registration.count = 1

    completed = {"count": 0}

    def fake_parent_is_gone(pid):
        # Only relevant after round 1 has completed once -- ends the test
        # by refusing round 2's load.
        if gs.state is State.IDLE and completed["count"] > 0:
            return True
        if gs.state is State.IDLE:
            completed["count"] += 1
        return False

    console_agent = _FakeConsoleAgent(gs, [
        (State.IDLE, load_round1),
        (State.SETUP, player_joins),
    ])

    monkeypatch.setattr("harness.terrarium_boot.parent_is_gone",
                        fake_parent_is_gone)

    reason = _serve_rounds(gs, _FakeAgent(), _FakeArco(),
                           console_agent=console_agent)

    assert reason == "parent-gone"
    assert gs.run_calls == 1


def test_serve_rounds_does_not_reannounce_a_bit_already_loaded_on_entry(
        capsys):
    """If `_serve_rounds` is ever entered with `gs` already out of IDLE
    (not the normal case -- main() only calls in here after a round has
    completed back to IDLE -- but `_wait_for_load`'s immediate-return path
    exists for exactly this), it must not print a second `round loaded:`
    line for a Bit main() already announced once itself."""
    from harness.terrarium_boot import _serve_rounds

    class FakeGS:
        def __init__(self):
            self.state = State.SETUP
            self.bit = _FakeBit(_FakeBitConfig(0.0))
            self.bit_name = "AlreadyLoadedBit"
            self.run_calls = 0
            self._tick_count = 0

        def tick(self, dt):
            self._tick_count += 1
            if self.state is State.RUNNING and self._tick_count >= 2:
                self.state = State.IDLE

        def run(self):
            self.run_calls += 1
            self.state = State.RUNNING
            self._tick_count = 0

        def abort(self):
            self.state = State.IDLE

    gs = FakeGS()

    def fake_parent_is_gone(pid):
        return gs.state is State.IDLE

    import harness.terrarium_boot as tb
    orig = tb.parent_is_gone
    tb.parent_is_gone = fake_parent_is_gone
    try:
        reason = _serve_rounds(gs, _FakeAgent(), _FakeArco())
    finally:
        tb.parent_is_gone = orig

    assert reason == "parent-gone"
    assert gs.run_calls == 1
    from harness import markers
    out = capsys.readouterr().out
    assert markers.CONTROL_ROUND_LOADED not in out


class _RecycleGS:
    """Shared FakeGS shape for the recycle tests below: IDLE -> (console
    load) SETUP -> RUNNING -> IDLE after two ticks, matching the
    `_serve_until_done` "completed" detection (`agent.closing` falsy)."""

    def __init__(self):
        self.state = State.IDLE
        self.bit = None
        self.bit_name = None
        self.registration = None
        self.run_calls = 0
        self.abort_calls = 0
        self._tick_count = 0

    def tick(self, dt):
        self._tick_count += 1
        if self.state is State.RUNNING and self._tick_count >= 2:
            self.state = State.IDLE

    def run(self):
        self.run_calls += 1
        self.state = State.RUNNING
        self._tick_count = 0

    def abort(self):
        self.abort_calls += 1
        self.state = State.IDLE


def test_round_end_never_touches_the_room(monkeypatch, capsys):
    """A completed round announces CONTROL_ROUND_ENDED and loops to the
    next `_wait_for_load` without any recycle_room/unload_room churn -- the
    automatic per-round recycle was removed by the 2026-09-01
    console-load-stabilization spec (Task 5)."""
    import inspect

    from control.terrarium import TerrariumState
    from harness import markers
    from harness.terrarium_boot import _serve_rounds

    class FakeTerrarium:
        state = TerrariumState.ROOM_READY

        def recycle_room(self):
            calls.append("recycle")

        def unload_room(self, force=False):
            calls.append("unload")

    terrarium = FakeTerrarium()
    gs = _RecycleGS()

    def load_round1():
        gs.bit = _FakeBit(_FakeBitConfig(0.0))
        gs.bit_name = "Round1Bit"
        gs.state = State.SETUP

    console_agent = _FakeConsoleAgent(gs, [(State.IDLE, load_round1)])

    ended = {"count": 0}

    def fake_parent_is_gone(pid):
        if gs.state is State.IDLE:
            ended["count"] += 1
            return ended["count"] > 1
        return False

    monkeypatch.setattr("harness.terrarium_boot.parent_is_gone",
                        fake_parent_is_gone)

    calls = []
    reason = _serve_rounds(gs, _FakeAgent(), _FakeArco(),
                           console_agent=console_agent, terrarium=terrarium)

    assert reason == "parent-gone"
    assert calls == []
    assert console_agent.round_ended_calls == [("Round1Bit", "completed")]
    printed = capsys.readouterr().out
    assert any(
        line == f"{markers.CONTROL_ROUND_ENDED} Round1Bit (completed)"
        for line in printed.splitlines())
    assert "recycle" not in inspect.signature(_serve_rounds).parameters


def test_wait_for_load_returns_loaded_immediately_when_not_idle():
    """The first, CLI-selected round: main() has already loaded a Bit
    before this is ever called, so there is nothing to wait for."""
    from harness.terrarium_boot import _wait_for_load

    class FakeGS:
        state = State.SETUP

        def tick(self, dt):
            raise AssertionError("must not tick when already loaded")

    class FakeAgent:
        def poll(self):
            raise AssertionError("must not poll when already loaded")

    reason = _wait_for_load(FakeGS(), FakeAgent(), _FakeArco())
    assert reason == "loaded"


def test_console_port_implies_serve_and_seconds_suppresses_it():
    """--console-port with neither --seconds nor --hold implies rounds;
    either bounded/one-shot flag suppresses that implication. --serve
    itself always wins regardless of the other two."""
    from harness.terrarium_boot import _build_arg_parser, _effective_serve

    ap = _build_arg_parser()

    args = ap.parse_args(["--console-port", "0"])
    assert _effective_serve(args) is True

    args = ap.parse_args(["--console-port", "0", "--seconds", "5"])
    assert _effective_serve(args) is False

    args = ap.parse_args(["--console-port", "0", "--hold"])
    assert _effective_serve(args) is False

    args = ap.parse_args([])
    assert _effective_serve(args) is False

    args = ap.parse_args(["--serve"])
    assert _effective_serve(args) is True


def _args(seconds=None, hold=False):
    return argparse.Namespace(seconds=seconds, hold=hold)


def test_run_duration_default_is_none():
    """No flags at all -- main() must add no `defaults.run_duration_seconds`
    override, leaving the selected Bit's manifest (TestBit's is 2.0, the
    same RUN_DURATION_SECONDS this used to hardcode) or its own fallback in
    force."""
    assert _run_duration(_args()) is None


def test_run_duration_seconds_overrides():
    assert _run_duration(_args(seconds=12.0)) == 12.0


def test_run_duration_hold_is_infinite():
    assert _run_duration(_args(hold=True)) == float("inf")


def test_run_duration_hold_beats_seconds():
    """--hold and --seconds together: --hold wins, matching
    harness/devicelink_smoke.py's _run_duration."""
    assert _run_duration(_args(seconds=5.0, hold=True)) == float("inf")


def _run_main_capturing_build(monkeypatch, argv):
    """main() cannot be driven directly to completion (see
    test_full_o2lite_unwind_order_through_main above -- it needs a live
    Arco and o2litepy), but everything up to and including the build()
    call is pure argument plumbing. Stubbing build() to raise as soon as
    it is invoked captures the BootConfig and bit registry main() built
    without running any of that."""
    captured = {}

    def fake_build(config, bit_registry, **kwargs):
        captured["config"] = config
        captured["bit_registry"] = bit_registry
        raise SystemExit(0)

    import harness.terrarium_boot as terrarium_boot_module
    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py"] + argv)

    with pytest.raises(SystemExit):
        main()

    return captured


def test_build_records_supervisor_and_spawns_when_runs_dir_given(tmp_path):
    """Wiring for design spec section 5: build() forwards runs_dir/run_id
    straight into Terrarium (already the case), which records its own
    supervisor entry at construction and one entry per spawned Arco/
    simulator during load_room -- this is the "dead code in production"
    finding, verified end to end through build() rather than only at the
    Terrarium unit level."""
    import os

    from control.run_record import RunRecorder

    class _FakePopenWithPid(FakePopen):
        """FakePopen (control/arco_process.py) never sets .pid -- it is a
        pure boundary-rule-5 double for poll/send_signal/wait, and real
        subprocess.Popen instances always have .pid. ArcoProcess/
        SimulatorProcess.start() both read `getattr(self._process, "pid",
        None)` to feed the record callback, so a pid is needed here to
        actually exercise that path."""

        def __init__(self, pid: int, **kwargs) -> None:
            super().__init__(**kwargs)
            self.pid = pid

        def __call__(self, command, **kwargs):
            super().__call__(command, **kwargs)
            return self

    def _fake_arco_with_pid(command, popen=None, record=None):
        from control.arco_process import ArcoProcess
        return ArcoProcess(command, popen=popen or _FakePopenWithPid(9001),
                           probe=lambda: True, record=record)

    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit},
        arco_command=["arco-server"], room_binding=RoomBindingRegistry(),
        room_spec=TEST_SPEC,
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco_with_pid,
        simulator_popen=_FakePopenWithPid(9002), room_audio=_fake_room_audio(),
        runs_dir=str(tmp_path), run_id="run-1")
    shutdown(teardown, terrarium)

    records = RunRecorder.load_all(str(tmp_path))
    roles = {r.role for r in records}
    assert "supervisor" in roles
    assert any(r.pid == os.getpid() and r.role == "supervisor" for r in records)
    assert "arco" in roles
    assert any(role.startswith("simulator:") for role in roles)


def test_main_forwards_runs_dir_and_run_id_to_build(monkeypatch):
    """main() must derive a run_id (the same runs/<timestamp> convention
    harness/run_stack.py already uses for --log-dir) and forward it, plus
    --runs-dir (default "runs"), into build() -- without this the sweep
    guardrail is never wired and every live load_room runs with sweep=None
    (the Critical finding this Task fixes)."""
    captured_kwargs = {}

    def fake_build(config, bit_registry, **kwargs):
        captured_kwargs.update(kwargs)
        raise SystemExit(0)

    import harness.terrarium_boot as terrarium_boot_module
    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py", "--room", "TEST"])

    with pytest.raises(SystemExit):
        main()

    assert captured_kwargs["runs_dir"] == "runs"
    run_id = captured_kwargs["run_id"]
    assert run_id is not None
    time.strptime(run_id, "%Y%m%d-%H%M%S")   # raises if the shape is wrong


def test_main_no_run_records_disables_runs_dir_and_run_id(monkeypatch):
    captured_kwargs = {}

    def fake_build(config, bit_registry, **kwargs):
        captured_kwargs.update(kwargs)
        raise SystemExit(0)

    import harness.terrarium_boot as terrarium_boot_module
    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(
        sys, "argv",
        ["terrarium_boot.py", "--room", "TEST", "--no-run-records"])

    with pytest.raises(SystemExit):
        main()

    assert captured_kwargs["runs_dir"] is None
    assert captured_kwargs["run_id"] is None


def test_main_defaults_bit_to_test_bit(monkeypatch):
    captured = _run_main_capturing_build(monkeypatch, ["--room", "TEST"])
    assert captured["config"].bit_name == "TestBit"
    assert "TestBit" in captured["bit_registry"]


def test_main_forwards_bit_flag_to_boot_config(monkeypatch, metronome_enabled_scan):
    captured = _run_main_capturing_build(
        monkeypatch, ["--bit", "MetronomeBit", "--room", "DEMO"])
    assert captured["config"].bit_name == "MetronomeBit"
    assert "MetronomeBit" in captured["bit_registry"]


def test_main_hands_build_every_discovered_bit_name(monkeypatch):
    """main() now wires the full registry (via lazy_class_map()) into
    build(), not just the one bit named on the command line -- the Console
    can load_bit() any discovered package, not only the boot-time default.
    A disabled bit (MetronomeBit, pending redesign) is still discovered but
    excluded from the lazy map, so the expected set is the enabled subset,
    not every discovered name."""
    from control.bit_registry import BitRegistry

    captured = _run_main_capturing_build(monkeypatch, ["--room", "TEST"])
    reg = BitRegistry.discover()
    enabled_names = {n for n, p in reg.packages.items()
                      if p.config.identity.enabled}
    assert set(captured["bit_registry"]) == enabled_names


def test_list_bits_prints_every_discovered_package(monkeypatch, capsys):
    """--bit is discovery-driven now (bits/*/bit.toml), not a hardcoded
    choices= list -- --list-bits is how an operator finds out what's
    actually installed."""
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py", "--list-bits"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "TestBit" in out
    assert "MetronomeBit" in out
    assert "CaptureBit" in out


def test_unknown_bit_exits_nonzero_naming_available_bits(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv",
                        ["terrarium_boot.py", "--bit", "NoSuchBit"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "NoSuchBit" in err
    assert "TestBit" in err
    assert "MetronomeBit" in err


def test_o2_simulator_factory_ties_the_simulator_to_this_process():
    """The only guard that survives an external kill of the parent, which
    is how agent-driven runs are actually terminated and which no teardown
    path can catch. An orphaned Room simulator reconnects to the NEXT Arco
    and claims sim-room before that run's own simulator is even spawned."""
    import os

    from harness.terrarium_boot import _O2SimulatorFactory

    popen = FakePopen()
    factory = _O2SimulatorFactory("arco", popen=popen)

    assert factory(TeardownStack(), "main") == "sim-room-main"
    command = popen.commands[0]
    assert "--exit-with-parent" in command
    assert command[command.index("--exit-with-parent") + 1] == str(os.getpid())


def test_simulator_factory_spawns_with_its_room_type():
    """_SimulatorFactory hardcoded --room-type TEST until this Task -- DEMO
    (control/room_profile.py, spec 2026-08-19) could resolve and boot but
    never actually reach the simulator subprocess it spawns."""
    from harness.terrarium_boot import _SimulatorFactory

    popen = FakePopen()
    factory = _SimulatorFactory("ws://x/ws", popen=popen, room_type="DEMO")

    assert factory(TeardownStack(), "array") == "sim-room-array"
    command = popen.commands[0]
    i = command.index("--room-type")
    assert command[i + 1] == "DEMO"


def test_o2_simulator_factory_spawns_with_its_room_type():
    """Same defect as _SimulatorFactory above, for the o2lite-transport
    factory."""
    from harness.terrarium_boot import _O2SimulatorFactory

    popen = FakePopen()
    factory = _O2SimulatorFactory("arco", popen=popen, room_type="DEMO")

    assert factory(TeardownStack(), "main") == "sim-room-main"
    command = popen.commands[0]
    i = command.index("--room-type")
    assert command[i + 1] == "DEMO"


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
    config = BootConfig(room_name="TEST", bit_name="TestBit")

    with pytest.raises(TimeoutError):
        build(config, {"TestBit": TestBit}, arco_command=["arco-server"],
              room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        host="127.0.0.1", port=0,
              arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
              simulator_popen=sim_popen)   # room_audio omitted: real branch

    assert sim_popen.signals    # simulator was told to stop, not orphaned
    assert arco_popen.signals   # and so was Arco


def _boom():
    raise OSError("no such process")


def test_agent_exposes_its_room_bridge():
    """main() reaches the bridge through the agent, since build() does not
    return it and its signature is deliberately unchanged."""
    from control.room_bridge import RoomBridge
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config)
    try:
        assert isinstance(agent.room_bridge, RoomBridge)
    finally:
        teardown.close()


def test_console_is_off_by_default():
    """Every existing invocation must be byte-identical."""
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config)
    try:
        assert agent._on_room_frame is None
    finally:
        teardown.close()


# --- Task 4: device lifecycle lines on Control's stdout --------------------
#
# Scripted against a real GameServer + DeviceLinkAgent (the same fixtures
# tests/test_devicelink_agent.py already uses), not through build()/main(),
# since _LifecycleLogger only needs gs.add_observer() and
# _print_join_denied only needs DeviceLinkAgent's on_join_denied sink --
# neither depends on Arco, the simulator, or the Console.

def _lifecycle_rig():
    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer

    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, on_join_denied=_print_join_denied)
    gs.add_observer(_LifecycleLogger(gs))
    return gs, server, agent


def _deliver_hello(server, agent, dev="ie1", client="c1"):
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
    agent.poll()


def _deliver_join(server, agent, dev, node, client="c1"):
    server.deliver(client, "/game/join", "ss", [dev, node])
    agent.poll()


def test_device_hello_line(capsys):
    gs, server, agent = _lifecycle_rig()
    _deliver_hello(server, agent, dev="ie1")

    out = capsys.readouterr().out
    assert "device hello: ie1\n" in out


def test_join_granted_line(capsys):
    gs, server, agent = _lifecycle_rig()
    gs.load_bit("test_bit")
    _deliver_hello(server, agent, dev="ie1")
    capsys.readouterr()   # discard the hello line
    _deliver_join(server, agent, "ie1", "TEST_PLAYER_NODE")

    out = capsys.readouterr().out
    assert "join granted: ie1 -> player (scored) via TEST_PLAYER_NODE\n" in out


def test_join_granted_line_for_a_jam_role(capsys):
    gs, server, agent = _lifecycle_rig()
    gs.load_bit("test_bit")
    _deliver_hello(server, agent, dev="ie1")
    capsys.readouterr()
    _deliver_join(server, agent, "ie1", "TEST_JAM_NODE")

    out = capsys.readouterr().out
    assert "join granted: ie1 -> jammer (jam) via TEST_JAM_NODE\n" in out


def test_join_denied_line(capsys):
    gs, server, agent = _lifecycle_rig()
    gs.load_bit("test_bit")
    _deliver_hello(server, agent, dev="ie1")
    capsys.readouterr()
    _deliver_join(server, agent, "ie1", "NO_SUCH_NODE")

    out = capsys.readouterr().out
    assert "join denied: ie1 -> NO_SUCH_NODE (no such node)\n" in out


def test_device_released_line(capsys):
    gs, server, agent = _lifecycle_rig()
    gs.load_bit("test_bit")
    _deliver_hello(server, agent, dev="ie1")
    _deliver_join(server, agent, "ie1", "TEST_PLAYER_NODE")
    capsys.readouterr()   # discard hello/granted lines

    gs.abort()

    out = capsys.readouterr().out
    assert "device released: ie1\n" in out


def test_build_wires_on_join_denied_to_the_agent_constructor():
    """The production path: build() threads on_join_denied straight into
    DeviceLinkAgent's constructor (the whole-branch review's Important
    finding was main() poking agent._on_join_denied after construction
    instead) -- exercised end to end through a denied /game/join on the
    FakeServer the agent test fixtures already use, not just an attribute
    check on the built agent."""
    calls = []
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit},
        arco_command=["arco-server"], room_binding=RoomBindingRegistry(),
        room_spec=TEST_SPEC,
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio(),
        clock=time.monotonic,
        on_join_denied=lambda dev, node, reason: calls.append(
            (dev, node, reason)))
    try:
        # build() already loads TestBit and lets its own simulators join
        # (see test_build_wires_devicelink_room_bridge_and_simulator above),
        # so a nonexistent node -- not "no Bit loaded" -- is the reliable
        # deny here. Drives the real, built agent's own inbound dispatch
        # (_on_join -> _notify_join_denied), not a substitute server.
        agent._on_join("fake-client", "ie1", ["ie1", "NO_SUCH_NODE"])

        assert calls == [("ie1", "NO_SUCH_NODE", "no such node")]
    finally:
        shutdown(teardown, terrarium)


def test_a_raising_on_join_denied_sink_does_not_stop_the_deny_reply(capsys):
    """The deny path must survive a raising sink, and the device must still
    get its /deny reply -- same guarantee devicelink/agent.py already gives
    on_room_frame."""
    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer

    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()

    def boom(dev, node, reason):
        raise RuntimeError("sink exploded")

    agent = DeviceLinkAgent(gs, server, on_join_denied=boom)
    gs.load_bit("test_bit")
    _deliver_hello(server, agent, dev="ie1")
    _deliver_join(server, agent, "ie1", "NO_SUCH_NODE")   # must not raise

    denies = server.addressed("/ie1/deny")
    assert denies[0]["args"][0] == "no such node"


def test_device_timed_out_line(capsys):
    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer, _Clock

    clk = _Clock()
    # gs and agent MUST share the same clock instance -- see control/
    # engine.py's comment on GameServer.__init__'s clock= param and
    # tests/test_devicelink_agent.py's _agent_with_joined_device(). An
    # unsynced pair (e.g. GameServer's default time.monotonic alongside
    # this agent's hand-advanced _Clock) makes GameServer.reap_stale()
    # see DevicePool.last_seen as enormously stale on the very next poll()
    # and reap the device before this test can observe a timeout at 11s.
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.add_observer(_LifecycleLogger(gs))
    _deliver_hello(server, agent, dev="ie1")
    capsys.readouterr()   # discard the hello line

    clk.advance(11.0)
    agent.poll()

    out = capsys.readouterr().out
    assert "device timed out: ie1\n" in out


def test_timed_out_role_holder_prints_both_released_and_timed_out_lines(capsys):
    """Final-review Finding 1 regression. reap_stale() notified
    on_registration_change BEFORE on_devices_change, and
    on_registration_change unconditionally overwrites _LifecycleLogger.
    _last_assignments as a side effect of printing "join granted" lines --
    so by the time on_devices_change's "device released" diff ran (against
    that just-clobbered snapshot), there was nothing left to diff and the
    line silently never printed for a reaped role-holding device. This
    contradicts both the design spec (docs/superpowers/specs/
    2026-08-25-device-liveness-detection-design.md section 7: "a timed-out
    player that held a role prints BOTH lines") and _LifecycleLogger's own
    docstring above. test_device_timed_out_line above never caught this: an
    un-joined device never sets released_any, so on_registration_change
    never even fires for it."""
    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer, _Clock

    clk = _Clock()
    # gs and agent MUST share the same clock instance -- see
    # test_device_timed_out_line above.
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.add_observer(_LifecycleLogger(gs))
    gs.load_bit("test_bit")
    _deliver_hello(server, agent, dev="ie1")
    _deliver_join(server, agent, "ie1", "TEST_PLAYER_NODE")
    capsys.readouterr()   # discard the hello/join-granted lines

    clk.advance(11.0)
    agent.poll()

    out = capsys.readouterr().out
    assert "device released: ie1\n" in out
    assert "device timed out: ie1\n" in out


# --- Task 7: --room (CLI shorthand for --room-type), NO_ROOM idle -------


def test_unknown_room_flag_exits_naming_test_and_demo(monkeypatch, capsys):
    """--room BOGUS is the CLI-level version of
    test_resolve_room_spec_raises_a_located_error_for_an_unknown_room
    above: main() must fail the exact same way, before ever calling
    build()."""
    monkeypatch.setattr(sys, "argv",
                        ["terrarium_boot.py", "--room", "BOGUS"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0
    message = str(exc_info.value)
    assert "BOGUS" in message
    assert "TEST" in message and "DEMO" in message


def test_no_room_and_no_console_port_is_refused(monkeypatch, capsys):
    """Omitting --room only makes sense if the Console is going to load a
    Room later -- with no console port either, nothing would ever load
    one, so this is refused up front rather than booting into a NO_ROOM
    idle nothing can ever leave."""
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "no --room" in err


def test_wait_for_room_ready_returns_immediately_when_already_ready():
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _wait_for_room_ready

    class FakeTerrarium:
        state = TerrariumState.ROOM_READY

    class FakeAgent:
        def poll(self):
            raise AssertionError("must not poll when already ready")

    reason = _wait_for_room_ready(FakeAgent(), FakeTerrarium())
    assert reason == "ready"


def test_wait_for_room_ready_polls_until_a_console_load_room_lands():
    """The NO_ROOM idle loop: main() falls in here with no --room given
    and a console port. Ticks the transport and the console each lap,
    exactly like a scripted console `load_room` command would drive it --
    landing ROOM_READY on the console_agent's own poll() call (its
    _handle_command dispatch is what would actually run terrarium
    .load_room in production; this fake stands in for that side effect
    directly, matching how console.agent.ConsoleAgent's own tests fake a
    scripted inbound command)."""
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _wait_for_room_ready

    class FakeTerrarium:
        def __init__(self):
            self.state = TerrariumState.NO_ROOM

    terrarium = FakeTerrarium()

    class FakeAgent:
        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1

    class FakeConsoleAgent:
        """Stands in for a scripted console `load_room` command: on its
        third poll() (as if a client had just sent load_room), it drives
        terrarium to ROOM_READY -- console.agent.ConsoleAgent's own
        _load_room does exactly this in production, via
        terrarium.load_room()."""

        def __init__(self, terrarium):
            self._terrarium = terrarium
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == 3:
                self._terrarium.state = TerrariumState.ROOM_READY

    agent = FakeAgent()
    console_agent = FakeConsoleAgent(terrarium)

    reason = _wait_for_room_ready(agent, terrarium, console_agent=console_agent,
                                  sleep=lambda _s: None)

    assert reason == "ready"
    assert console_agent.polls == 3
    assert agent.polls == 3


def test_wait_for_room_ready_exits_when_the_parent_is_gone(monkeypatch):
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _wait_for_room_ready

    class FakeTerrarium:
        state = TerrariumState.NO_ROOM

    class FakeAgent:
        def poll(self):
            pass

    monkeypatch.setattr("harness.terrarium_boot.parent_is_gone",
                        lambda pid: True)

    reason = _wait_for_room_ready(FakeAgent(), FakeTerrarium(),
                                  parent_pid=999, sleep=lambda _s: None)
    assert reason == "parent-gone"


def test_serve_roomless_loops_back_to_no_room_after_serve_rounds_no_room(
        monkeypatch):
    """main()'s top-level loop for a NO_ROOM boot: wait for a Room, serve
    rounds against it, and -- the behavior this Task's brief calls out by
    name -- return to the NO_ROOM wait rather than stopping outright when
    `_serve_rounds` reports "no-room" (a Console `unload_room` mid-serve).
    A second lap then runs to "parent-gone" so this test terminates."""
    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _serve_roomless

    class FakeTerrarium:
        def __init__(self):
            self.state = TerrariumState.ROOM_READY
            self.arco = _FakeArco()

    class FakeGS:
        state = State.IDLE

        def tick(self, dt):
            pass

    class FakeAgent:
        def poll(self):
            pass

    terrarium = FakeTerrarium()
    serve_rounds_calls = []

    def fake_serve_rounds(gs, agent, arco, *, parent_pid=None,
                          console_agent=None, terrarium=None, **_kw):
        serve_rounds_calls.append(terrarium.state)
        if len(serve_rounds_calls) == 1:
            terrarium.state = TerrariumState.NO_ROOM
            return "no-room"
        return "parent-gone"

    def fake_wait_for_room_ready(agent, terr, **kwargs):
        # A fresh Console load_room would leave terrarium ROOM_READY again
        # -- fake_serve_rounds above only flips it to NO_ROOM for the first
        # lap's "no-room" return, so this stands in for that next load.
        terr.state = TerrariumState.ROOM_READY
        return "ready"

    monkeypatch.setattr(terrarium_boot_module, "_serve_rounds",
                        fake_serve_rounds)
    monkeypatch.setattr(terrarium_boot_module, "_wait_for_room_ready",
                        fake_wait_for_room_ready)

    reason = _serve_roomless(FakeGS(), FakeAgent(), terrarium)

    assert reason == "parent-gone"
    assert len(serve_rounds_calls) == 2


def test_serve_roomless_stops_clients_on_no_room(monkeypatch):
    """A `stop_clients` given to `_serve_roomless` must run on every
    "no-room" lap -- the room went down under live Arco clients (a Console
    hard abort or `unload_room`), so the transport/pool need to be stopped
    before the next NO_ROOM wait, not left running against a dead hub."""
    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _serve_roomless

    class FakeTerrarium:
        def __init__(self):
            self.state = TerrariumState.ROOM_READY
            self.arco = _FakeArco()

    class FakeGS:
        state = State.IDLE

        def tick(self, dt):
            pass

    class FakeAgent:
        def poll(self):
            pass

    terrarium = FakeTerrarium()
    calls = []
    serve_rounds_calls = []

    def fake_wait_for_room_ready(agent, terr, **kwargs):
        return "ready"

    def fake_serve_rounds(gs, agent, arco, *, parent_pid=None,
                          console_agent=None, terrarium=None, **_kw):
        serve_rounds_calls.append(1)
        if len(serve_rounds_calls) == 1:
            return "no-room"
        return "parent-gone"

    monkeypatch.setattr(terrarium_boot_module, "_wait_for_room_ready",
                        fake_wait_for_room_ready)
    monkeypatch.setattr(terrarium_boot_module, "_serve_rounds",
                        fake_serve_rounds)

    reason = _serve_roomless(FakeGS(), FakeAgent(), terrarium,
                             stop_clients=lambda: calls.append("stop"),
                             restart_clients=lambda: None)

    assert reason == "parent-gone"
    assert calls == ["stop"]


def test_serve_roomless_restarts_pool_then_transport_after_failed_recycle(
        monkeypatch):
    """A `recycle()` failure stops Control's own transport/pool
    (client-before-hub) but leaves no hub to restart them against -- so
    when a later plain Console `load_room` succeeds, `_serve_roomless`
    must restart them itself (pool first, then transport, mirroring
    `_recycle_room`'s own order) before serving, or the process would sit
    in ROOM_READY with a live Arco but dead clients."""
    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _serve_roomless

    class FakeTerrarium:
        def __init__(self):
            self.state = TerrariumState.ROOM_READY
            self.arco = _FakeArco()
            self.unload_calls = 0

        def unload_room(self, force=False):
            self.unload_calls += 1

    class FakeGS:
        state = State.IDLE

        def tick(self, dt):
            pass

    class FakeAgent:
        def poll(self):
            pass

    terrarium = FakeTerrarium()
    calls = []

    def fake_wait_for_room_ready(agent, terr, **kwargs):
        return "ready"

    def fake_serve_rounds(gs, agent, arco, *, parent_pid=None,
                          console_agent=None, terrarium=None, **_kw):
        calls.append("serve-rounds")
        return "parent-gone"

    def restart_clients():
        calls.append("pool-start")
        calls.append("transport-start")
        return None

    monkeypatch.setattr(terrarium_boot_module, "_wait_for_room_ready",
                        fake_wait_for_room_ready)
    monkeypatch.setattr(terrarium_boot_module, "_serve_rounds",
                        fake_serve_rounds)

    reason = _serve_roomless(FakeGS(), FakeAgent(), terrarium,
                             restart_clients=restart_clients)

    assert reason == "parent-gone"
    assert calls == ["pool-start", "transport-start", "serve-rounds"]
    assert terrarium.unload_calls == 0


def test_serve_roomless_skips_restart_when_recycle_already_succeeded(
        monkeypatch):
    """No double-start: when `restart_clients` reports nothing was stopped
    (the ordinary case -- no prior failed recycle, or `_recycle_room`
    already restarted the clients itself), `_serve_roomless` must not
    restart them again."""
    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _serve_roomless

    class FakeTerrarium:
        def __init__(self):
            self.state = TerrariumState.ROOM_READY
            self.arco = _FakeArco()

    class FakeGS:
        state = State.IDLE

        def tick(self, dt):
            pass

    class FakeAgent:
        def poll(self):
            pass

    terrarium = FakeTerrarium()
    calls = []

    def fake_wait_for_room_ready(agent, terr, **kwargs):
        return "ready"

    def fake_serve_rounds(gs, agent, arco, *, parent_pid=None,
                          console_agent=None, terrarium=None, **_kw):
        calls.append("serve-rounds")
        return "parent-gone"

    def restart_clients():
        # Simulates the real closure's own "nothing was stopped" no-op.
        return None

    monkeypatch.setattr(terrarium_boot_module, "_wait_for_room_ready",
                        fake_wait_for_room_ready)
    monkeypatch.setattr(terrarium_boot_module, "_serve_rounds",
                        fake_serve_rounds)

    reason = _serve_roomless(FakeGS(), FakeAgent(), terrarium,
                             restart_clients=restart_clients)

    assert reason == "parent-gone"
    assert calls == ["serve-rounds"]


def test_serve_roomless_unloads_and_returns_to_no_room_wait_when_restart_fails(
        monkeypatch):
    """If the restart-after-reload itself fails, `_serve_roomless` must
    unload the Room (so ROOM_READY never lies about live clients) and
    loop back to the NO_ROOM wait rather than serving with dead clients --
    never calling `_serve_rounds` for that lap."""
    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _serve_roomless

    class FakeTerrarium:
        def __init__(self):
            self.state = TerrariumState.ROOM_READY
            self.arco = _FakeArco()
            self.unload_calls = []

        def unload_room(self, force=False):
            self.unload_calls.append(force)
            self.state = TerrariumState.NO_ROOM

    class FakeGS:
        state = State.IDLE

        def tick(self, dt):
            pass

    class FakeAgent:
        def poll(self):
            pass

    terrarium = FakeTerrarium()
    wait_calls = []
    serve_rounds_calls = []

    def fake_wait_for_room_ready(agent, terr, **kwargs):
        wait_calls.append(1)
        if len(wait_calls) == 1:
            return "ready"
        return "parent-gone"

    def fake_serve_rounds(gs, agent, arco, *, parent_pid=None,
                          console_agent=None, terrarium=None, **_kw):
        serve_rounds_calls.append(1)
        return "parent-gone"

    def restart_clients():
        return "injected restart failure"

    monkeypatch.setattr(terrarium_boot_module, "_wait_for_room_ready",
                        fake_wait_for_room_ready)
    monkeypatch.setattr(terrarium_boot_module, "_serve_rounds",
                        fake_serve_rounds)

    reason = _serve_roomless(FakeGS(), FakeAgent(), terrarium,
                             restart_clients=restart_clients)

    assert reason == "parent-gone"
    assert serve_rounds_calls == []
    assert terrarium.unload_calls == [True]
    assert len(wait_calls) == 2


def test_restart_room_clients_starts_pool_then_transport():
    """`_restart_room_clients` is the restart half of `_recycle_room`,
    factored out so `_serve_roomless` can call it too: pool.start() before
    transport.start(o2lite), matching process launch order."""
    import harness.terrarium_boot as terrarium_boot

    calls = []

    class FakePool:
        def start(self):
            calls.append("pool-start")

    class FakeTransport:
        def start(self, o2):
            calls.append(("transport-start", o2))

    o2 = object()
    reason = terrarium_boot._restart_room_clients(
        transport=FakeTransport(), pool=FakePool(), o2lite=o2)
    assert reason is None
    assert calls == ["pool-start", ("transport-start", o2)]


def test_restart_room_clients_catches_a_raising_start_and_returns_reason():
    """Unlike Terrarium's own methods, pool.start()/transport.start() DO
    raise on failure -- `_restart_room_clients` must catch that and
    stringify it rather than let it propagate, so callers get the same
    "reason string, never raises" contract as `_recycle_room`."""
    import harness.terrarium_boot as terrarium_boot

    class FailingPool:
        def start(self):
            raise RuntimeError("injected pool failure")

    reason = terrarium_boot._restart_room_clients(pool=FailingPool())
    assert reason == "injected pool failure"


def test_wait_for_load_returns_no_room_when_terrarium_leaves_room_ready():
    """`_serve_rounds` threads `terrarium` straight into `_wait_for_load`
    so a Console `unload_room` landing while a round waits in IDLE (no Bit
    loaded yet) is noticed as "no-room", not misreported as "arco-exited"
    -- checked FIRST, ahead of the arco liveness check, exactly because
    unload_room(force=True) has already shut arco down by the time this
    would otherwise notice (see _wait_for_load's own docstring)."""
    from control.terrarium import TerrariumState
    from harness.terrarium_boot import _wait_for_load

    class FakeTerrarium:
        state = TerrariumState.NO_ROOM

    class FakeGS:
        state = State.IDLE

        def tick(self, dt):
            raise AssertionError("must not tick past the no-room check")

    class FakeArco:
        def poll(self):
            raise AssertionError("must not poll arco past the no-room check")

    class FakeAgent:
        def poll(self):
            raise AssertionError("must not poll past the no-room check")

    reason = _wait_for_load(FakeGS(), FakeAgent(), FakeArco(),
                            terrarium=FakeTerrarium())
    assert reason == "no-room"


def test_console_load_room_after_a_no_room_boot_wires_room_rendering():
    """The full path a NO_ROOM boot's Console `load_room` actually takes:
    build() with no room_spec parks `agent` at room_bridge=None (nothing
    to render yet), main()'s own _RoomWiring observer is what has to pick
    that back up once a Room finally loads THROUGH terrarium -- this test
    drives terrarium.load_room("TEST") directly (the same call
    console.agent.ConsoleAgent's own _load_room makes) rather than a
    scripted console message, since the Terrarium-level wiring is what is
    under test here, not the console wire protocol (see
    tests/test_console_agent.py's own
    test_room_panel_controllers_read_terrarium_room_bridge_live for that
    side)."""
    from console.agent import ConsoleAgent
    from control.terrarium import TerrariumState
    from control.terrarium_config import TerrariumConfig
    from harness.terrarium_boot import _RoomWiring
    from tests.test_console_agent import FakeConsoleServer

    config = BootConfig(room_name=None, bit_name="TestBit")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        terrarium_config=TerrariumConfig(
            schema=1, name="test", bit_paths=(), rooms={"TEST": TEST_SPEC},
            version="test"),
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())

    assert terrarium.state is TerrariumState.NO_ROOM
    assert arco is None
    assert agent._room_light is None
    assert agent.room_bridge is None

    terrarium.add_observer(_RoomWiring(agent, terrarium))
    fake_srv = FakeConsoleServer()
    console_agent = ConsoleAgent(gs, fake_srv, terrarium=terrarium)

    reason = terrarium.load_room("TEST")
    assert reason is None
    gs.load_bit("TestBit")

    # The devicelink agent's own Room session/bridge, wired live by
    # _RoomWiring -- not what a snapshot alone can prove.
    assert agent.room_bridge is terrarium.room_bridge
    assert agent._room_light is not None

    # The Console's own view: a room payload that is no longer None.
    fake_srv.connect("c1")
    console_agent.poll()
    _, msg = fake_srv.sent[0]
    assert msg["room"] is not None
    assert msg["room"]["room_type"] == "TEST"

    shutdown(teardown, terrarium)


def test_make_arco_process_cls_accepts_the_record_kwarg_load_room_passes():
    # Terrarium.load_room passes record= whenever run records are on (the
    # default), so the harness factory refusing it broke every live launch
    # with "unexpected keyword argument 'record'" (runs/20260828-201202).
    from harness.terrarium_boot import make_arco_process_cls

    def record(pid):
        pass

    cls = make_arco_process_cls(FakePopen(), settle=0)
    proc = cls(["arco"], record=record)
    assert proc._record is record
    # And record= stays optional for the settle-wrapped path too.
    cls_settle = make_arco_process_cls(FakePopen(), settle=0.001)
    assert cls_settle(["arco"]) is not None


def test_main_wires_the_shipped_instrument_catalog_root_into_the_console_agent(
        monkeypatch):
    """main() loads terrarium.toml's own instrument_paths (default
    ["instruments"]) via load_terrarium_config, resolved relative to the
    config file's own directory -- and must thread the first resolved root
    straight into the ConsoleAgent it builds, so the Console's design panel
    (list/get/save/publish/clone) has somewhere to read and write. Uses the
    repo's own shipped terrarium.toml/instruments -- --config defaults to
    it -- with build() and _serve_roomless stubbed out (no room, no live
    Arco needed) so only the argument plumbing through to ConsoleAgent's
    construction is exercised."""
    import types

    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState

    class _FakeObservable:
        room = None
        state = TerrariumState.NO_ROOM

        def add_observer(self, observer):
            pass

    def fake_build(config, bit_registry, **kwargs):
        gs = _FakeObservable()
        server = types.SimpleNamespace(port=0)
        agent = types.SimpleNamespace(room_bridge=None, canvas_urls=[])
        teardown = TeardownStack()
        terrarium = _FakeObservable()
        return gs, server, agent, None, teardown, terrarium

    def fake_serve_roomless(gs, agent, terrarium, *, console_agent=None,
                            parent_pid=None,
                            restart_clients=None, stop_clients=None):
        captured["catalog_root"] = console_agent.catalog_root
        raise SystemExit(0)

    captured = {}
    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(terrarium_boot_module, "_serve_roomless",
                        fake_serve_roomless)
    monkeypatch.setattr(sys, "argv",
                        ["terrarium_boot.py", "--console-port", "0"])

    with pytest.raises(SystemExit):
        terrarium_boot_module.main()

    assert captured["catalog_root"] is not None
    assert captured["catalog_root"].name == "instruments"


def test_main_wires_the_bench_session_factory_and_captures_root(monkeypatch):
    """main() must also thread a bench_session_factory (from
    harness.design_session) and a captures_root into the ConsoleAgent it
    builds, so the Console's design bench and capture panel are backed for
    real rather than answering error_event on every command. Same
    fake-build/fake-serve harness as the catalog-root test above."""
    import types

    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState

    class _FakeObservable:
        room = None
        state = TerrariumState.NO_ROOM

        def add_observer(self, observer):
            pass

    def fake_build(config, bit_registry, **kwargs):
        gs = _FakeObservable()
        server = types.SimpleNamespace(port=0)
        agent = types.SimpleNamespace(room_bridge=None, canvas_urls=[])
        teardown = TeardownStack()
        terrarium = _FakeObservable()
        return gs, server, agent, None, teardown, terrarium

    def fake_serve_roomless(gs, agent, terrarium, *, console_agent=None,
                            parent_pid=None,
                            restart_clients=None, stop_clients=None):
        captured["bench_session_factory"] = console_agent.bench_session_factory
        captured["captures_root"] = console_agent.captures_root
        raise SystemExit(0)

    captured = {}
    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(terrarium_boot_module, "_serve_roomless",
                        fake_serve_roomless)
    monkeypatch.setattr(sys, "argv",
                        ["terrarium_boot.py", "--console-port", "0"])

    with pytest.raises(SystemExit):
        terrarium_boot_module.main()

    assert captured["bench_session_factory"] is not None
    assert captured["captures_root"].name == "captures"


def test_main_wires_stop_clients_into_the_no_room_boot_serve_loop(monkeypatch):
    """main()'s NO_ROOM-boot branch (no --room, a console port given) must
    pass its own `stop_clients` into `_serve_roomless`, same as every other
    call site in the file. Otherwise a Console hard ABORT mid-round on a
    console-only boot returns "no-room" without transport.stop() /
    pool.quiesce() ever running, and clients_stopped stays False -- so the
    next load_room's restart_clients() no-ops and the o2lite
    transport/pool stay wired to a dead hub. Same fake-build/fake-serve
    harness as the catalog-root and bench-session-factory tests above,
    but this one captures the full kwargs `_serve_roomless` was called
    with instead of reaching into the console_agent."""
    import types

    import harness.terrarium_boot as terrarium_boot_module
    from control.terrarium import TerrariumState

    class _FakeObservable:
        room = None
        state = TerrariumState.NO_ROOM

        def add_observer(self, observer):
            pass

    def fake_build(config, bit_registry, **kwargs):
        gs = _FakeObservable()
        server = types.SimpleNamespace(port=0)
        agent = types.SimpleNamespace(room_bridge=None, canvas_urls=[])
        teardown = TeardownStack()
        terrarium = _FakeObservable()
        return gs, server, agent, None, teardown, terrarium

    def fake_serve_roomless(gs, agent, terrarium, *, console_agent=None,
                            parent_pid=None, restart_clients=None,
                            stop_clients=None):
        captured["restart_clients"] = restart_clients
        captured["stop_clients"] = stop_clients
        raise SystemExit(0)

    captured = {}
    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(terrarium_boot_module, "_serve_roomless",
                        fake_serve_roomless)
    monkeypatch.setattr(sys, "argv",
                        ["terrarium_boot.py", "--console-port", "0"])

    with pytest.raises(SystemExit):
        terrarium_boot_module.main()

    assert captured["restart_clients"] is not None
    assert captured["stop_clients"] is not None


def test_recycle_room_orders_client_stops_before_unload_and_restarts_after():
    """Client-before-hub (control/teardown.py's invariant): both of
    Control's own Arco clients -- the O2LiteTransport and the
    ArcoSynthPool -- must stop before terrarium.recycle_room() tears down
    the old Arco, and the relaunch mirrors process launch order: pool
    first (arco.initialize() blocks on clock sync with the new hub), then
    transport (which asserts a synced clock)."""
    import types

    import harness.terrarium_boot as terrarium_boot

    calls = []

    class FakeTerrarium:
        room = types.SimpleNamespace(name="TEST")

        def recycle_room(self):
            calls.append("recycle")
            return None

    class FakeTransport:
        def stop(self):
            calls.append("transport-stop")

        def start(self, o2):
            calls.append(("transport-start", o2))

    class FakePool:
        def quiesce(self):
            calls.append("pool-quiesce")

        def start(self):
            calls.append("pool-start")

    o2 = object()
    reason = terrarium_boot._recycle_room(
        FakeTerrarium(), transport=FakeTransport(), pool=FakePool(), o2lite=o2)
    assert reason is None
    assert calls == ["transport-stop", "pool-quiesce", "recycle",
                     "pool-start", ("transport-start", o2)]


def test_recycle_room_websocket_mode_skips_transport():
    """Websocket mode passes transport=None -- the devicelink server is
    process-scoped, not an Arco client -- but the pool still quiesces and
    restarts since audio is unconditionally on."""
    import types

    import harness.terrarium_boot as terrarium_boot

    calls = []

    class FakeTerrarium:
        room = types.SimpleNamespace(name="TEST")

        def recycle_room(self):
            calls.append("recycle")
            return None

    class FakePool:
        def quiesce(self):
            calls.append("pool-quiesce")

        def start(self):
            calls.append("pool-start")

    assert terrarium_boot._recycle_room(FakeTerrarium(), pool=FakePool()) is None
    assert calls == ["pool-quiesce", "recycle", "pool-start"]


def test_recycle_room_failure_skips_restarts_and_returns_reason():
    """On failure the restarts are skipped -- there is no hub to restart
    against -- and the reason string propagates so the caller can treat it
    like a Console unload_room."""
    import types

    import harness.terrarium_boot as terrarium_boot

    calls = []

    class FakeTerrarium:
        room = types.SimpleNamespace(name="TEST")

        def recycle_room(self):
            return "arco failed to start: injected"

    class FakePool:
        def quiesce(self):
            calls.append("pool-quiesce")

        def start(self):
            calls.append("pool-start")

    reason = terrarium_boot._recycle_room(FakeTerrarium(), pool=FakePool())
    assert reason == "arco failed to start: injected"
    assert "pool-start" not in calls
