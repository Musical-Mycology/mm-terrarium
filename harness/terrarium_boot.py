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

from bits.metronome_bit import MetronomeBit
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
from harness.o2_shroom import parent_is_gone
from harness.signals import sigterm_as_keyboard_interrupt


def sim_dev(fixture: str) -> str:
    """Deterministic o2lite service name per fixture -- unique per fixture,
    which is the entire reason each is spawned as its own client (design
    spec section 3)."""
    return f"sim-room-{fixture}"


class _SimulatorFactory:
    """boot()'s simulator_factory contract is Callable[[TeardownStack, str],
    str]: (teardown, fixture_name) -> dev, called once per fixture. The
    factory registers whatever it spawns on the stack it is handed, so an
    orphaned simulator is impossible by construction. `self.processes` is
    kept only so tests can inspect the handles."""

    def __init__(self, server_url: str, *, popen=subprocess.Popen,
                 horizon: float | None = None,
                 room_type: str = "TEST") -> None:
        self._server_url = server_url
        self._popen = popen
        self._horizon = horizon
        self._room_type = room_type
        self.processes: list[SimulatorProcess] = []

    def __call__(self, teardown, fixture: str) -> str:
        dev = sim_dev(fixture)
        command = [sys.executable, "-u", "-m", "harness.room_simulator",
                   "--dev", dev, "--server", self._server_url,
                   "--fixture", fixture]
        command += ["--room-type", self._room_type]
        if self._horizon is not None:
            # So the Room reports frame latency in absolute terms on exit.
            command += ["--control-horizon", str(self._horizon)]
        process = SimulatorProcess(command, popen=self._popen)
        process.start()
        teardown.push(f"simulator-{fixture}", process.shutdown)
        self.processes.append(process)
        return dev


class _O2SimulatorFactory:
    """Spawns the Room simulator as an o2lite client rather than a
    websocket one. Reuses harness/o2_shroom.py with --no-join: Control has
    already recorded this dev as the bound Room before the process is
    spawned, so there is no Registration Node to tap -- the same rule
    harness/room_simulator.py follows. Called once per fixture, same as
    _SimulatorFactory."""

    def __init__(self, ensemble: str, *, popen=subprocess.Popen,
                 room_type: str = "TEST") -> None:
        self._ensemble = ensemble
        self._popen = popen
        self._room_type = room_type
        self.processes: list[SimulatorProcess] = []

    def __call__(self, teardown, fixture: str) -> str:
        # -u for the same reason _SimulatorFactory passes it: without it
        # this child's stdout is block-buffered, so its exit report is lost
        # on an ungraceful exit and harness/run_stack.py cannot watch it for
        # readiness markers.
        #
        # --exit-with-parent is what stops this subprocess outliving the
        # Terrarium. An orphan keeps its browser canvas open, reconnects to
        # the next Arco (o2litepy reconnects on its own) and claims this
        # fixture's dev name there, so the NEXT run's simulator for that
        # fixture is refused by O2 and renders nothing. Passing our own pid
        # covers the case teardown cannot: an external SIGKILL of this
        # process.
        dev = sim_dev(fixture)
        process = SimulatorProcess(
            [sys.executable, "-u", "-m", "harness.o2_shroom",
             "--dev", dev, "--ensemble", self._ensemble, "--no-join",
             "--exit-with-parent", str(os.getpid()),
             "--room-type", self._room_type, "--fixture", fixture],
            popen=self._popen)
        process.start()
        teardown.push(f"simulator-{fixture}", process.shutdown)
        self.processes.append(process)
        return dev


def build(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, host: str = "127.0.0.1",
         port: int = 0, arco_process_cls=ArcoProcess,
         simulator_popen=subprocess.Popen, room_audio=None, transport=None,
         clock=time.monotonic, on_join_denied=None):
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
    AudioBridge(pool, clock=clock) is the existing precedent for this.

    It is ALSO threaded into GameServer via boot(), because the engine now
    computes every cue's target time and reads this clock both for a
    self-driven cue's origin and for the fallback when a device did not
    stamp its gesture. The engine and the agent must read the same clock or
    a cue's time is unreachable -- the same failure this parameter was added
    to fix, one layer up."""
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
                                    horizon=config.cue_horizon,
                                    room_type=config.room_type.name)
    else:
        factory = _O2SimulatorFactory(config.o2_ensemble,
                                      popen=simulator_popen,
                                      room_type=config.room_type.name)
    gs, room_bridge, arco, teardown = _boot(
        config, bit_registry, arco_command=arco_command,
        room_binding=room_binding, arco_process_cls=arco_process_cls,
        simulator_factory=factory, teardown=teardown, clock=clock)

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
                                horizon=config.cue_horizon, clock=clock,
                                on_join_denied=on_join_denied)
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
    order here is not a list anyone maintains -- and it is two orders, not
    one, since the devicelink server and the o2lite transport never both
    exist in the same run. Websocket mode: the Bit, then the Room bridge
    (which frees the Room's Arco voice), then the Room simulator subprocess,
    then Arco, then the devicelink server. O2lite mode: the o2lite
    transport, then that same Bit/Room bridge/simulator/Arco order.

    Client before hub is the property that matters and the one that was
    broken: this function used to call control.boot.shutdown() first, which
    ends by killing Arco, and only then stop the simulator that talks to it.
    """
    for name, exc in teardown.close():
        print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)


def _wait_in_setup(agent, setup_seconds: float, clock=time.monotonic,
                   sleep=time.sleep, parent_pid: int | None = None,
                   console_agent=None, arco=None, gs=None) -> str:
    """Poll the transport for setup_seconds while the Bit sits in SETUP, so
    a device can join a scored role before run() closes the window.
    registration.join() refuses scored roles once RUNNING
    (control/registration.py:41-42), and TestBit's `player` is scored, so
    without this window harness/o2_shroom.py is denied every time.
    setup_seconds <= 0 -- the default -- returns immediately, preserving the
    existing load-straight-into-run behavior. Same shape as
    harness/devicelink_smoke.py's _wait_in_setup.

    parent_pid, when given, is checked every tick via
    harness/o2_shroom.py's parent_is_gone -- see F5 in the final review
    for why this reuses that predicate rather than a second one. A
    SIGKILLed or OOM-killed run_stack cannot signal this process, so the
    only way to notice is to keep asking. Returns "parent-gone" if that
    fired, so main() can skip straight to shutdown() instead of calling
    gs.run() into a stack whose supervisor is already gone.

    console_agent, when given, is polled once per iteration too -- a device
    joining during SETUP is exactly what the console's registration view
    exists to show live.

    arco, when given, is drained every iteration. Every loop that holds
    while Arco is alive must drain Arco's pty: Arco is a curses app, an
    undrained pty blocks it mid-write, and a blocked Arco serves no clock
    sync, routes no messages and plays no audio. This loop not draining
    it froze whole rooms for the length of the hold (2026-08-20).

    gs, when given, is watched: the Console is a second driver, and if
    the operator moves the engine out of SETUP (Run, Abort) this hold
    yields immediately instead of letting main() call run() into a
    RUNNING engine.

    Returns "expired", "parent-gone", or "state-changed".
    """
    if setup_seconds <= 0:
        return "expired"
    start = clock()
    deadline = start + setup_seconds
    next_countdown = start + 15.0
    while True:
        now = clock()
        if now >= deadline:
            return "expired"
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if arco is not None:
            arco.poll()
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        if gs is not None and gs.state is not State.SETUP:
            return "state-changed"
        if now >= next_countdown:
            print(f"SETUP open, {deadline - now:.0f}s remaining", flush=True)
            next_countdown = now + 15.0
        sleep(1.0 / 44.0)


def _serve_until_done(gs, agent, arco, clock=time.monotonic,
                      sleep=time.sleep, parent_pid: int | None = None,
                      console_agent=None) -> str:
    """Tick until the Bit finishes, Arco dies, or the parent is gone.
    Returns the reason.

    Three exit conditions, all deliberate:

    "completed" -- the Bit signalled done from update(dt), so the engine
    ran COMPLETING/UNLOADING synchronously and state is back to IDLE. The
    loop then keeps polling until the transport has no devices still
    closing, because release is ASYNCHRONOUS: a released device renders its
    closing fade over several ticks and only then receives /<dev>/release.
    Exiting the instant state hit IDLE would freeze every device on its
    last frame.

    "arco-exited" -- the Arco subprocess is gone. Fail loud: silent
    degradation in a venue is worse than a visible stop.

    "parent-gone" -- run_stack, which passes --exit-with-parent, is no
    longer this process's parent (SIGKILLed or OOM-killed). Checked here,
    not left to fall out of some other symptom, so the exit runs through
    this function's caller's `finally: shutdown(teardown)` instead of
    leaving Arco and the Room simulator running un-signalled in their own
    session -- the orphan class docs/upstream/2026-08-14-o2-service-and-
    discovery-report.md names as a venue-scale hazard. See
    harness/o2_shroom.py's parent_is_gone for why this compares against a
    recorded pid rather than watching getppid() for a change.

    console_agent, when given, is polled once per tick too, right after
    agent.poll() -- the same tick loop, so the console's picture of the run
    (bit status, Room frames, registration) is never more than one tick
    stale.
    """
    while True:
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if arco.poll() is not None:
            return "arco-exited"
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
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


class _LifecycleLogger:
    """Prints the device lifecycle (hello / join granted / released) to
    Control's stdout. Registered via gs.add_observer(), the same
    multi-observer seam console.agent.ConsoleAgent already rides -- so it
    sees exactly the same on_devices_change/on_registration_change payload
    shapes ConsoleAgent does (there is no per-call payload at all; both
    hooks are pure "something changed, go re-read gs" signals).

    Denials never reach this seam: GameServer.join() returns a refused
    JoinResult without ever touching registration state, so neither hook
    fires for a deny. Those print via DeviceLinkAgent's own on_join_denied
    sink instead -- see _print_join_denied below.

    Derivation:
      - "device hello: <dev>" -- a dev appearing in gs.devices.all() that
        was not there the last time on_devices_change fired. DevicePool
        never drops a device (control/device_pool.py), so this set only
        grows.
      - "join granted: <dev> -> <role> (<category>) via <node>" -- a dev
        whose (node, role, role_class) tuple in gs.registration.assignments
        is new or changed since the last on_registration_change (a role
        switch -- re-tapping a different node -- changes the tuple without
        the dev ever leaving assignments, and must still print).
      - "device released: <dev>" -- a dev that HAD an assignments entry
        last time but has none now. control/engine.py's on_release is a
        single transport-owned sink (already claimed by DeviceLinkAgent for
        the /release wire message), not a multi-observer event, so release
        can't ride that hook. GameServer._unload() releases every assigned
        device via registration.release_all() and notifies ONLY
        on_devices_change afterward (never on_registration_change) -- see
        engine.py's _unload(). So this diff runs in on_devices_change,
        against the assignments snapshot on_registration_change last left
        behind.

    A Room join (role_class ROOM) never reaches either hook's assignments
    diff as a grant: GameServer.join() returns before notifying
    on_registration_change for those (control/engine.py's _bind_room()
    notifies on_devices_change only), the same exclusion
    ConsoleAgent._non_room_counts() applies to the registration panel.
    """

    def __init__(self, game_server) -> None:
        self._gs = game_server
        self._last_devices: set = set()
        self._last_assignments: dict = {}

    def _current_assignments(self) -> dict:
        registration = self._gs.registration
        return dict(registration.assignments) if registration is not None else {}

    def on_devices_change(self) -> None:
        current_devs = {info.dev for info in self._gs.devices.all()}
        for dev in current_devs - self._last_devices:
            print(f"device hello: {dev}", flush=True)
        self._last_devices = current_devs

        current_assignments = self._current_assignments()
        for dev in self._last_assignments:
            if dev not in current_assignments:
                print(f"device released: {dev}", flush=True)
        self._last_assignments = current_assignments

    def on_registration_change(self) -> None:
        registration = self._gs.registration
        current_assignments = self._current_assignments()
        for dev, value in current_assignments.items():
            if value == self._last_assignments.get(dev):
                continue
            node, role_name, role_class = value
            role = registration.role_table.roles[role_name]
            category = "scored" if role.scored else role_class.name.lower()
            print(f"join granted: {dev} -> {role_name} ({category}) "
                  f"via {node}", flush=True)
        self._last_assignments = current_assignments


def _print_join_denied(dev: str, node: str, reason: str) -> None:
    """DeviceLinkAgent's on_join_denied sink (see devicelink/agent.py's
    _notify_join_denied, called at the deny reply's send site and guarded
    there so a raise here can never cost the device its /deny reply).
    Denials never touch registration state, so _LifecycleLogger's
    engine-observer seam above never sees them -- this is Control's only
    stdout line for a denial.

    Lowercase "join denied:" is deliberate and load-bearing: the DEVICE-side
    marker is harness/markers.py's DEVICE_JOIN_DENIED = "JOIN DENIED:", and
    harness/run_stack.py matches markers as plain substrings of a child's
    stdout. Uppercasing this line to "match" would make Control's own
    stdout satisfy the device's readiness marker and desync run_stack's
    bookkeeping -- keep the casing apart."""
    print(f"join denied: {dev} -> {node} ({reason})", flush=True)


def _register_o2lite_transport(teardown, transport) -> None:
    """Push the o2lite transport's teardown -- exactly where main() calls
    this, right after transport.start(o2lite) has actually adopted the
    connection, and after build() has already registered arco, the
    simulator, the room bridge and the Bit.

    Pushed LAST of the steps this run registers, so it tears down FIRST:
    stopping the transport before Arco, whose hub it is a guest on, is the
    client-before-hub property one layer up from build()'s own -- see
    shutdown()'s docstring for the full order.

    Extracted so tests/test_terrarium_boot.py can assert this step's
    position without driving the whole of main(), which needs argparse, a
    live Arco, and o2litepy.
    """
    teardown.push("o2lite-transport", transport.stop)


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
    ap.add_argument("--exit-with-parent", type=int, default=None,
                    metavar="PID",
                    help="Exit through the normal shutdown() teardown path "
                         "as soon as this process's parent is no longer "
                         "PID. harness/run_stack.py passes its own pid: "
                         "without this, a SIGKILLed or OOM-killed run_stack "
                         "leaves this process running, and with it Arco "
                         "and the Room simulator, un-signalled in their own "
                         "session -- this process has always been started "
                         "by a human before, so it had no equivalent of "
                         "o2_shroom's --exit-with-parent. Off by default: "
                         "a hand-run terrarium_boot is unchanged.")
    ap.add_argument("--console-port", type=int, default=None,
                    help="Serve the Terrarium Console on this port. Off by "
                         "default, so an existing invocation is unchanged. "
                         "Binds --host, which defaults to 127.0.0.1: the "
                         "console is unauthenticated and trusted-LAN only.")
    ap.add_argument("--room-type", default="TEST", choices=["TEST", "DEMO"],
                    help="Which RoomType to boot. DEMO configures the "
                         "simulated array backend (spec 2026-08-19); its "
                         "864 px canvas is otherwise identical in kind to "
                         "TEST's.")
    ap.add_argument("--bit", default="TestBit",
                    choices=["TestBit", "MetronomeBit"],
                    help="Which Bit to run. MetronomeBit is DEMO-only -- "
                         "boot() already fails loud if the resolved "
                         "RoomType is not in the chosen Bit's room_types, "
                         "so pair this with --room-type DEMO.")
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

    room_type = RoomType[args.room_type]
    config = BootConfig(
        room_type=room_type, bit_name=args.bit,
        # DEMO's recipe requires an array backend (control/rooms.py);
        # "simulator" is the Terrarium-spawns-one value BootConfig already
        # defines. TEST ignores the field.
        array_backend="simulator" if room_type is RoomType.DEMO else None)
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
        config, {"TestBit": _timed_test_bit_cls(_run_duration(args)),
                 "MetronomeBit": MetronomeBit},
        arco_command=[args.arco_command],
        room_binding=room_binding, host=args.host, port=args.port,
        transport=transport, clock=clock,
        arco_process_cls=arco_process_cls, on_join_denied=_print_join_denied)

    # Device lifecycle on Control's stdout (2026-08-20 UAT: a denial was
    # invisible anywhere but the denied device's own terminal). Unconditional
    # -- unlike the Console below, this costs nothing and needs no port, so
    # it is wired for every invocation, not gated behind --console-port. The
    # deny sink is threaded through build() to DeviceLinkAgent's constructor
    # (above) rather than poked in here as agent._on_join_denied -- the
    # console-frame sink two functions below still does that (build() never
    # took an on_room_frame parameter), but on_join_denied has one, so
    # production wiring uses it rather than reaching past it.
    gs.add_observer(_LifecycleLogger(gs))

    # Once build() has returned, Arco and the simulator are live
    # subprocesses and room_audio's ArcoSynthPool is running -- everything
    # from here on must go through shutdown() on the way out, including a
    # failure to start the transport itself (its clock-sync assertion is an
    # expected failure mode, not a hypothetical one). The console is
    # constructed first, inside this same try, for that reason: ConsoleServer
    # .start() binding a busy port is a real failure mode too, and it must
    # unwind through the same shutdown(teardown) path rather than leaking
    # Arco and the simulator that build() already spawned.
    try:
        console_agent = None
        if args.console_port is not None:
            from console.agent import ConsoleAgent
            from console.server import ConsoleServer
            console_server = ConsoleServer(host=args.host,
                                           port=args.console_port)
            console_server.start()
            # Registered AFTER build(), so it is torn down FIRST. That is
            # correct here rather than an oversight: the console is a
            # monitor shell whose only clients are browsers, outside this
            # stack entirely. The devicelink server is last because the Room
            # simulator is its client; nothing in the stack is a client of
            # the console.
            teardown.push("console-server", console_server.stop)
            # clock is main()'s own already-resolved local (time.monotonic
            # on the websocket path, o2lite.time_get on the o2lite path),
            # not a fresh time.monotonic -- see build()'s clock= docstring
            # for the two-clocks bug this guards against.
            console_agent = ConsoleAgent(gs, console_server,
                                         room_bridge=agent.room_bridge,
                                         clock=clock)
            agent._on_room_frame = console_agent.on_room_frame
            print(f"{markers.BROWSE_URL} Terrarium Console at "
                  f"http://{args.host}:{console_server.port}/", flush=True)
        if transport is not None:
            transport.start(o2lite)            # raises if the clock is unsynced
            _register_o2lite_transport(teardown, transport)
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
        reason = _wait_in_setup(agent, args.setup_seconds,
                                parent_pid=args.exit_with_parent,
                                console_agent=console_agent,
                                arco=arco, gs=gs)
        if reason == "parent-gone":
            print("parent is gone; tearing down", file=sys.stderr)
        else:
            if gs.state is State.SETUP:
                gs.run()
            else:
                # The operator drove the engine from the Console during the
                # hold. That is a handoff, not an error: run() from here
                # would raise InvalidTransition into a live room.
                print("operator changed state from the Console; "
                      "skipping harness run()", flush=True)
            reason = _serve_until_done(gs, agent, arco,
                                       parent_pid=args.exit_with_parent,
                                       console_agent=console_agent)
            if reason == "arco-exited":
                print("Arco exited; tearing down", file=sys.stderr)
            elif reason == "parent-gone":
                print("parent is gone; tearing down", file=sys.stderr)
            else:
                print("Bit completed; tearing down")
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(teardown)


if __name__ == "__main__":
    main()
