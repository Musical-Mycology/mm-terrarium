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

import os
import subprocess
import sys
import time

from bits.test_bit import RUN_DURATION_SECONDS, TestBit
from control.arco_process import ArcoProcess
from control.boot import boot as _boot
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.simulator_process import SimulatorProcess
from control.state import State
from control.teardown import TeardownStack
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer
from harness import markers
from harness.signals import sigterm_as_keyboard_interrupt

SIM_DEV = "sim-room"


class _SimulatorFactory:
    """boot()'s simulator_factory contract is Callable[[TeardownStack], str]:
    the factory registers whatever it spawns on the stack it is handed, so
    an orphaned simulator is impossible by construction. `self.process` is
    kept only so tests can inspect the handle."""

    def __init__(self, server_url: str, *, popen=subprocess.Popen,
                 horizon: float | None = None) -> None:
        self._server_url = server_url
        self._popen = popen
        self._horizon = horizon
        self.process: SimulatorProcess | None = None

    def __call__(self, teardown) -> str:
        command = [sys.executable, "-u", "-m", "harness.room_simulator",
                   "--dev", SIM_DEV, "--server", self._server_url]
        if self._horizon is not None:
            # So the Room reports frame latency in absolute terms on exit.
            command += ["--control-horizon", str(self._horizon)]
        self.process = SimulatorProcess(command, popen=self._popen)
        self.process.start()
        teardown.push("simulator", self.process.shutdown)
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

    def __call__(self, teardown) -> str:
        # -u for the same reason _SimulatorFactory passes it: without it
        # this child's stdout is block-buffered, so its exit report is lost
        # on an ungraceful exit and harness/run_stack.py cannot watch it for
        # readiness markers.
        #
        # --exit-with-parent is what stops this subprocess outliving the
        # Terrarium. An orphan keeps its browser canvas open, reconnects to
        # the next Arco (o2litepy reconnects on its own) and claims sim-room
        # there, so the NEXT run's simulator is refused by O2 and renders
        # nothing. Passing our own pid covers the case teardown cannot: an
        # external SIGKILL of this process.
        self.process = SimulatorProcess(
            [sys.executable, "-u", "-m", "harness.o2_shroom",
             "--dev", SIM_DEV, "--ensemble", self._ensemble, "--no-join",
             "--exit-with-parent", str(os.getpid())],
            popen=self._popen)
        self.process.start()
        teardown.push("simulator", self.process.shutdown)
        return SIM_DEV


def build(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, host: str = "127.0.0.1",
         port: int = 0, arco_process_cls=ArcoProcess,
         simulator_popen=subprocess.Popen, room_audio=None, transport=None,
         clock=time.monotonic):
    """Construct the whole stack. Returns (game_server, devicelink_server,
    devicelink_agent, arco_process, teardown).

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
    this function never constructs or starts an O2LiteTransport itself.

    clock: threaded straight through to DeviceLinkAgent, whose default is
    the same time.monotonic -- so omitting this argument changes nothing.
    It exists so o2lite mode can hand in o2lite.time_get instead: Control
    stamps every frame's `when` off this clock (agent.py:253), and
    harness/o2_shroom.py ticks its device off the O2 clock, so the two
    must read the same clock or `when` is never reachable -- exactly the
    live-demo bug this parameter fixes. This function still never imports
    o2litepy itself; the caller (main(), only in the --transport o2lite
    branch) resolves o2lite.time_get and hands it in as a plain callable,
    the same way it already hands in the started transport. Also threaded
    into the default (room_audio=None) AudioBridge below, for the same
    reason: DeviceLinkAgent._tick_audio() ticks room_audio against this
    same clock (agent.py), so a welcome cue's due time (set at on_grant,
    against AudioBridge's own clock) and its expiry check (at tick, against
    the agent's) have to agree -- harness/led_smoke.py's own
    AudioBridge(pool, clock=clock) is the existing precedent for this."""
    teardown = TeardownStack()
    if transport is None:
        server = DeviceLinkServer(host=host, port=port)
        server.start()
        # Pushed BEFORE boot() so it is torn down LAST. The Room simulator
        # is a client of this server, and boot() spawns it, so registration
        # order is what keeps client-before-server true here.
        teardown.push("devicelink-server", server.stop)
    else:
        # o2lite mode: there is no socket to listen on. The connection is
        # pyarco's, already clock-synced by arco.initialize(), and the
        # caller started the transport on it -- and therefore the caller
        # registers its teardown, after this function returns, so it stops
        # before everything registered here.
        server = transport

    if transport is None:
        factory = _SimulatorFactory(f"ws://{host}:{server.port}/ws",
                                    popen=simulator_popen,
                                    horizon=config.cue_horizon)
    else:
        factory = _O2SimulatorFactory(config.o2_ensemble,
                                      popen=simulator_popen)
    gs, room_bridge, arco, teardown = _boot(
        config, bit_registry, arco_command=arco_command,
        room_binding=room_binding, arco_process_cls=arco_process_cls,
        simulator_factory=factory, teardown=teardown)

    try:
        if room_audio is None:
            from control.audio import AudioBridge
            from harness.arco_synth import ArcoSynthPool
            pool = ArcoSynthPool() if config.arco_soundfont is None \
                else ArcoSynthPool(soundfont=config.arco_soundfont)
            pool.start()
            room_audio = AudioBridge(pool, clock=clock)

        agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                                room_audio=room_audio,
                                horizon=config.cue_horizon, clock=clock)
    except BaseException:
        # _boot() has already spawned Arco AND the simulator by this point,
        # and main() cannot clean either up: build() never returns, so its
        # `finally: shutdown(...)` has no handle at all. Closing the stack
        # unwinds everything registered so far, in order, each step guarded
        # so cleanup cannot mask this failure. BaseException so a Ctrl-C
        # during the ArcoSynthPool connect -- which blocks for up to 30s --
        # is covered too.
        teardown.close()
        raise

    return gs, server, agent, arco, teardown


def shutdown(teardown) -> None:
    """Unwind everything, in reverse registration order, and report.

    Every step is registered at the point the thing it owns starts, so the
    order here is not a list anyone maintains: o2lite transport, then the
    Bit, then the Room bridge (which frees the Room's Arco voice), then the
    Room simulator subprocess, then Arco, then the devicelink server.

    Client before hub is the property that matters and the one that was
    broken: this function used to call control.boot.shutdown() first, which
    ends by killing Arco, and only then stop the simulator that talks to it.
    """
    for name, exc in teardown.close():
        print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)


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
    ap.add_argument("--arco-start-audio", action="store_true",
                    help="After boot, press Arco's (S)tart key to re-open "
                         "its audio devices. Needs --arco-pty (that is what "
                         "owns Arco's console). Works around an UPSTREAM "
                         "defect measured 2026-08-14: pyarco's "
                         "arco.initialize() sends /host/clear, which tears "
                         "down Arco's audio stream, and Arco then stops "
                         "serving O2 clock sync to any client connecting "
                         "AFTERWARD -- a device hangs forever in o2_shroom's "
                         "'while o2lite.time_get() < 0' loop while Control, "
                         "already synced, looks perfectly healthy. Pressing "
                         "(S)tart re-opens audio and sync works again. OFF "
                         "by default because (S)tart/Stop is a toggle Arco "
                         "gives no way to read, so pressing it on a healthy "
                         "server STOPS audio instead.")
    ap.add_argument("--arco-settle-seconds", type=float, default=0.0,
                    help="Pause between spawning Arco and first probing it. "
                         "The readiness probe is DESTRUCTIVE: pyarco's "
                         "arco.initialize() unconditionally calls reset(), "
                         "which sends /host/clear and tears down the "
                         "server's audio stream. One probe is survivable; a "
                         "probe that FAILS and retries adds another reset, "
                         "and the extra teardown can leave arco.output None "
                         "-- ArcoSynthPool.start() then dies with "
                         "\"'NoneType' object has no attribute 'ins'\". "
                         "Settling first makes probe #1 succeed, so only one "
                         "reset ever happens. Default 0 keeps existing "
                         "behavior.")
    ap.add_argument("--arco-ready-timeout", type=float, default=None,
                    help="Override BootConfig.arco_ready_timeout (15 s). "
                         "The FIRST readiness probe against a cold Arco can "
                         "take ~18 s -- it connects, then pyarco's reset() "
                         "times out after 5 s ('Could not reset Arco server "
                         "within 5 seconds') -- while the second attempt "
                         "succeeds instantly. When that happens the 15 s "
                         "budget expires inside probe #1 and boot fails with "
                         "ArcoReadyTimeout even though Arco is up and fine. "
                         "Raise this if you see that.")
    ap.add_argument("--arco-pty", action="store_true",
                    help="Spawn Arco on a pty with its own controlling "
                         "terminal, so its curses init can open /dev/tty "
                         "from a non-interactive context (CI, cron, an "
                         "agent-driven measurement run). Off by default: an "
                         "interactive terminal already provides one.")
    ap.add_argument("--arco-log", default=None, metavar="PATH",
                    help="Tee Arco's console output to this file. Needs "
                         "--arco-pty (that is what owns Arco's stdio). "
                         "Without it Arco's output is drained into memory "
                         "and discarded, which makes 'Arco never came up' "
                         "the least diagnosable failure in the stack.")
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

    # harness/run_stack.py stops this process with SIGTERM, and the whole
    # ordered teardown below lives in a finally that a bare SIGTERM skips.
    sigterm_as_keyboard_interrupt()

    transport = None
    clock = time.monotonic
    if args.transport == "o2lite":
        from o2litepy import o2lite            # lazy: websocket mode needs no o2litepy

        from devicelink.o2_transport import O2LiteTransport
        # pyarco's ArcoSynthPool.start() runs arco.initialize(), which
        # connects o2lite and blocks until clock sync. build() does that
        # while constructing room_audio, so the transport is started after
        # build() returns rather than before it.
        transport = O2LiteTransport()
        # Control must stamp frames on the same clock the device ticks
        # against (harness/o2_shroom.py: client.tick(o2lite.time_get())),
        # or a frame's `when` is never reachable -- see build()'s clock=
        # docstring. o2litepy is a module-level singleton (design spec
        # 2026-08-12 section 5.2), so this is the very same clock
        # arco.initialize() already synced by the time build() constructs
        # the agent below.
        clock = o2lite.time_get

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    if args.horizon is not None:
        config.cue_horizon = args.horizon
    if args.arco_ready_timeout is not None:
        config.arco_ready_timeout = args.arco_ready_timeout
    room_binding = RoomBindingRegistry()

    # boot() constructs the process with a single positional argument, so
    # both options below are one-argument factories rather than subclasses.
    # The settle pause lives in start() because boot() calls start() and
    # wait_ready() back to back with no seam between them -- putting it here
    # keeps control/boot.py free of a harness-only concern.
    arco_popen = subprocess.Popen
    if args.arco_pty:
        from control.arco_process import pty_popen
        log_path = args.arco_log

        def arco_popen(command):
            return pty_popen(command, log_path=log_path)
    elif args.arco_log:
        print("--arco-log needs --arco-pty; ignoring", file=sys.stderr)

    settle = args.arco_settle_seconds

    def arco_process_cls(command):
        proc = ArcoProcess(command, popen=arco_popen)
        if settle <= 0:
            return proc
        started = proc.start

        def start_then_settle():
            started()
            time.sleep(settle)

        proc.start = start_then_settle
        return proc

    gs, server, agent, arco, teardown = build(
        config, {"TestBit": _timed_test_bit_cls(_run_duration(args))},
        arco_command=[args.arco_command],
        room_binding=room_binding, host=args.host, port=args.port,
        transport=transport, clock=clock,
        arco_process_cls=arco_process_cls)

    # Once build() has returned, Arco and the simulator are live
    # subprocesses and room_audio's ArcoSynthPool is running -- everything
    # from here on must go through shutdown() on the way out, including a
    # failure to start the transport itself (its clock-sync assertion is an
    # expected failure mode, not a hypothetical one).
    try:
        if transport is not None:
            transport.start(o2lite)            # raises if the clock is unsynced
            # Registered AFTER everything build() registered, so it stops
            # BEFORE them -- including before Arco, whose hub this transport
            # is a guest on. Stopping it after Arco died was the same
            # client-after-hub bug as the simulator's, one layer up.
            teardown.push("o2lite-transport", transport.stop)
            print(f"{markers.CONTROL_TRANSPORT_READY} "
                  f"{config.o2_ensemble!r} (Ctrl-C to stop)", flush=True)
        else:
            print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws "
                  f"(Ctrl-C to stop)")
        if args.arco_start_audio:
            # After Control's own clock sync, so this cannot disturb it.
            console = getattr(arco, "_process", None)
            if hasattr(console, "write_console"):
                console.write_console("S")
                time.sleep(3.0)              # let the audio devices re-open
                print("pressed Arco's (S)tart key: audio re-opened, so Arco "
                      "serves clock sync to devices again")
            else:
                print("--arco-start-audio needs --arco-pty; ignoring",
                      file=sys.stderr)
        if args.setup_seconds > 0:
            print(f"{markers.CONTROL_SETUP_HOLD} for {args.setup_seconds:g}s "
                  f"-- join now", flush=True)
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
        shutdown(teardown)


if __name__ == "__main__":
    main()
