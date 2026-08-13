"""python -m harness.terrarium_boot -- boot a Terrarium into a TEST Room,
simulator included. The first real entry point for
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
and its follow-up,
docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md.

Ordering matters here and is why this script -- not control/boot.py --
constructs DeviceLinkServer: the server must already be listening before
boot() calls its simulator_factory, which spawns harness/room_simulator.py
and expects to connect immediately. See design spec section 6.
"""

from __future__ import annotations

import subprocess
import sys
import time

from bits.test_bit import RUN_DURATION_SECONDS, TestBit
from control.arco_process import ArcoProcess
from control.boot import boot as _boot
from control.boot import shutdown as _boot_shutdown
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.simulator_process import SimulatorProcess
from control.state import State
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer

SIM_DEV = "sim-room"


class _SimulatorFactory:
    """boot()'s simulator_factory contract is a bare Callable[[], str] --
    this closure captures the SimulatorProcess handle in an attribute so the
    caller can retrieve it once boot() returns, without changing boot()'s
    signature (see design spec section 8's open question, resolved here)."""

    def __init__(self, server_url: str, *, popen=subprocess.Popen) -> None:
        self._server_url = server_url
        self._popen = popen
        self.process: SimulatorProcess | None = None

    def __call__(self) -> str:
        self.process = SimulatorProcess(
            [sys.executable, "-m", "harness.room_simulator",
             "--dev", SIM_DEV, "--server", self._server_url],
            popen=self._popen)
        self.process.start()
        return SIM_DEV


class _O2SimulatorFactory:
    """Spawns the Room simulator as an o2lite client rather than a
    websocket one. Reuses harness/o2_shroom.py with --no-join: Control has
    already recorded this dev as the bound Room before the process is
    spawned, so there is no Registration Node to tap -- the same rule
    harness/room_simulator.py follows."""

    def __init__(self, ensemble: str, *, popen=subprocess.Popen) -> None:
        self._ensemble = ensemble
        self._popen = popen
        self.process: SimulatorProcess | None = None

    def __call__(self) -> str:
        self.process = SimulatorProcess(
            [sys.executable, "-m", "harness.o2_shroom",
             "--dev", SIM_DEV, "--ensemble", self._ensemble, "--no-join"],
            popen=self._popen)
        self.process.start()
        return SIM_DEV


def build(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, host: str = "127.0.0.1",
         port: int = 0, arco_process_cls=ArcoProcess,
         simulator_popen=subprocess.Popen, room_audio=None, transport=None):
    """Construct the whole stack. Returns (game_server, devicelink_server,
    devicelink_agent, arco_process, simulator_process).

    room_audio: an already-constructed AudioBridge. Default None builds a
    real one backed by ArcoSynthPool -- lazily imported, exactly like
    harness/arco_synth.py already does elsewhere, so this module costs
    nothing when Arco/pyarco are absent. Tests inject an
    AudioBridge(FakePool()) instead, so no test ever attempts a real pyarco
    import or calls ArcoSynthPool.start() (which FakePool has no equivalent
    of -- it needs no live connection to fake). Audio is unconditionally on
    once real (design spec section 3): there is no --audio-style opt-out.

    transport: an already-adopted O2LiteTransport (see
    devicelink/o2_transport.py), or None for the default websocket
    DeviceLinkServer. o2lite mode has no socket to listen on -- the
    connection is pyarco's, already clock-synced by arco.initialize() and
    started by the caller before this transport was handed in here -- so
    this function never constructs or starts an O2LiteTransport itself."""
    if transport is None:
        server = DeviceLinkServer(host=host, port=port)
        server.start()
    else:
        # o2lite mode: there is no socket to listen on. The connection is
        # pyarco's, already clock-synced by arco.initialize(), and the
        # caller started the transport on it.
        server = transport

    if transport is None:
        factory = _SimulatorFactory(f"ws://{host}:{server.port}/ws",
                                    popen=simulator_popen)
    else:
        factory = _O2SimulatorFactory(config.o2_ensemble,
                                      popen=simulator_popen)
    gs, room_bridge, arco = _boot(
        config, bit_registry, arco_command=arco_command,
        room_binding=room_binding, arco_process_cls=arco_process_cls,
        simulator_factory=factory)

    if room_audio is None:
        from control.audio import AudioBridge
        from harness.arco_synth import ArcoSynthPool
        pool = ArcoSynthPool() if config.arco_soundfont is None \
            else ArcoSynthPool(soundfont=config.arco_soundfont)
        pool.start()
        room_audio = AudioBridge(pool)

    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                            room_audio=room_audio,
                            horizon=config.cue_horizon)
    return gs, server, agent, arco, factory.process


def shutdown(gs, agent: DeviceLinkAgent, arco: ArcoProcess,
            simulator: SimulatorProcess) -> None:
    """Tear down in order: the Bit/Room first (via control.boot.shutdown,
    which also frees the Room's Arco voice through room_bridge.shutdown()
    and shuts Arco itself down as its last step), then the simulator
    subprocess, then the devicelink server (agent.server -- no new plumbing
    needed, DeviceLinkAgent already holds the reference it was built
    with)."""
    _boot_shutdown(gs, agent._room_bridge or _NullRoomBridge(), arco)
    simulator.shutdown()
    agent.server.stop()


class _NullRoomBridge:
    """control.boot.shutdown() always calls room_bridge.shutdown() -- if
    this driver somehow ran with no Room configured at all, hand it
    something inert rather than special-casing shutdown()'s signature.
    Unreachable via this module's own build(): control.boot.boot() always
    returns a real RoomBridge(), never None. Kept for a DeviceLinkAgent
    built some other way, with room_bridge=None passed explicitly."""

    def shutdown(self) -> None:
        pass


def _wait_in_setup(agent, setup_seconds: float, clock=time.monotonic,
                   sleep=time.sleep) -> None:
    """Poll the transport for setup_seconds while the Bit sits in SETUP, so
    a device can join a scored role before run() closes the window.
    registration.join() refuses scored roles once RUNNING
    (control/registration.py:41-42), and TestBit's `player` is scored, so
    without this window harness/o2_shroom.py is denied every time.
    setup_seconds <= 0 -- the default -- returns immediately, preserving the
    existing load-straight-into-run behavior. Same shape as
    harness/devicelink_smoke.py's _wait_in_setup.
    """
    if setup_seconds <= 0:
        return
    deadline = clock() + setup_seconds
    while clock() < deadline:
        agent.poll()
        sleep(1.0 / 44.0)


def _serve_until_done(gs, agent, arco, clock=time.monotonic,
                      sleep=time.sleep) -> str:
    """Tick until the Bit finishes or Arco dies. Returns the reason.

    Two exit conditions, both deliberate:

    "completed" -- the Bit signalled done from update(dt), so the engine
    ran COMPLETING/UNLOADING synchronously and state is back to IDLE. The
    loop then keeps polling until the transport has no devices still
    closing, because release is ASYNCHRONOUS: a released device renders its
    closing fade over several ticks and only then receives /<dev>/release.
    Exiting the instant state hit IDLE would freeze every device on its
    last frame.

    "arco-exited" -- the Arco subprocess is gone. Fail loud: silent
    degradation in a venue is worse than a visible stop.
    """
    while True:
        if arco.poll() is not None:
            return "arco-exited"
        agent.poll()
        gs.tick(1.0 / 44.0)
        if gs.state == State.IDLE and not getattr(agent, "closing", 0):
            return "completed"
        sleep(1.0 / 44.0)


def _run_duration(args) -> float:
    """Same shape as harness/devicelink_smoke.py's _run_duration: --hold
    wins over --seconds, and no flags at all preserves the exact default
    (RUN_DURATION_SECONDS) that a bare `python -m harness.terrarium_boot`
    has always used."""
    if args.hold:
        return float("inf")
    return RUN_DURATION_SECONDS if args.seconds is None else args.seconds


def _timed_test_bit_cls(run_duration: float) -> type:
    """Wrap TestBit in a zero-arg subclass carrying the resolved duration.

    control/boot.py's boot() reads `bit_cls.room_types` straight off the
    registry entry -- before ever instantiating it -- to gate the Bit
    against the Room type (`if room.room_type not in bit_cls.room_types`).
    control/engine.py's GameServer.load_bit() then calls `bit_cls()` with no
    arguments. A functools.partial(TestBit, run_duration=...) would satisfy
    the second half but not the first: a partial object has no `room_types`
    of its own, only what TestBit would have if called. A small subclass
    satisfies both -- room_types is inherited normally through the MRO
    (TestBit doesn't override it, so it resolves to control.bit.Bit's
    `{RoomType.TEST}`), and __init__ takes no arguments while closing over
    the resolved duration.
    """
    class _TimedTestBit(TestBit):
        def __init__(self) -> None:
            super().__init__(run_duration=run_duration)

    return _TimedTestBit


def main() -> None:
    import argparse

    from control.rooms import RoomType

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--seconds", type=float, default=None,
                    help="How long the Bit stays RUNNING before completing.")
    ap.add_argument("--hold", action="store_true",
                    help="Never auto-complete; run until Ctrl-C.")
    ap.add_argument("--arco-command", default="/Users/chris/projects/arco/apps/pytest/server")
    ap.add_argument("--horizon", type=float, default=None,
                    help="Cue scheduling horizon in seconds. Default: "
                         "BootConfig.cue_horizon. Measure with "
                         "python -m harness.sync_bench.")
    ap.add_argument("--transport", choices=("websocket", "o2lite"),
                    default="websocket",
                    help="websocket: the JSON devicelink shim, no Arco in "
                         "the device path. o2lite: real O2 through the Arco "
                         "hub, which requires a running Arco server.")
    ap.add_argument("--setup-seconds", type=float, default=0.0,
                    help="Hold the Bit in SETUP for this long before "
                         "run(), so a device can join a scored role (e.g. "
                         "TEST_PLAYER_NODE) before registration closes for "
                         "it. Default 0 keeps the instant-run behavior.")
    args = ap.parse_args()

    transport = None
    if args.transport == "o2lite":
        from o2litepy import o2lite            # lazy: websocket mode needs no o2litepy

        from devicelink.o2_transport import O2LiteTransport
        # pyarco's ArcoSynthPool.start() runs arco.initialize(), which
        # connects o2lite and blocks until clock sync. build() does that
        # while constructing room_audio, so the transport is started after
        # build() returns rather than before it.
        transport = O2LiteTransport()

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    if args.horizon is not None:
        config.cue_horizon = args.horizon
    room_binding = RoomBindingRegistry()
    gs, server, agent, arco, simulator = build(
        config, {"TestBit": _timed_test_bit_cls(_run_duration(args))},
        arco_command=[args.arco_command],
        room_binding=room_binding, host=args.host, port=args.port,
        transport=transport)

    # Once build() has returned, Arco and the simulator are live
    # subprocesses and room_audio's ArcoSynthPool is running -- everything
    # from here on must go through shutdown() on the way out, including a
    # failure to start the transport itself (its clock-sync assertion is an
    # expected failure mode, not a hypothetical one).
    try:
        if transport is not None:
            transport.start(o2lite)            # raises if the clock is unsynced
            print(f"DeviceLink running on o2lite ensemble "
                  f"{config.o2_ensemble!r} (Ctrl-C to stop)")
        else:
            print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws "
                  f"(Ctrl-C to stop)")
        if args.setup_seconds > 0:
            print(f"Holding in SETUP for {args.setup_seconds:g}s -- join now")
        _wait_in_setup(agent, args.setup_seconds)
        gs.run()
        reason = _serve_until_done(gs, agent, arco)
        if reason == "arco-exited":
            print("Arco exited; tearing down", file=sys.stderr)
        else:
            print("Bit completed; tearing down")
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(gs, agent, arco, simulator)


if __name__ == "__main__":
    main()
