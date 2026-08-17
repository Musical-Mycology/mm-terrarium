"""python -m harness.run_stack -- run the whole Arco stack from one command.

Before this, running the stack meant two interactive terminals in the right
order, a pile of non-obvious flags, and Ctrl-C in the right sequence or the
run's output was lost. Nothing captured either process's output to a file.

WHAT IT SUPERVISES. harness/terrarium_boot.py (which itself spawns Arco and
the Room simulator) plus N harness/o2_shroom.py player devices. It does NOT
reimplement terrarium_boot's sequencing: that module stays the single
definition of how Control boots.

o2lite throughout. There is no websocket variant, because the point is to
run the Arco stack.

WHY THE FLAGS ARE NOT OPTIONAL, all of these bought the hard way:

  --arco-pty: Arco's curses init opens /dev/tty, and a plain Popen whose
    stdio is a pipe fails with "Could not open /dev/tty". `script` does not
    rescue it.
  --arco-settle-seconds: the readiness probe is DESTRUCTIVE (pyarco's
    arco.initialize() sends /host/clear). One reset is survivable; a probe
    that fails and retries adds a second, and the extra teardown can leave
    arco.output None, after which ArcoSynthPool.start() dies.
  --arco-ready-timeout: the FIRST probe against a cold Arco can take ~18s
    while the second succeeds instantly, so the 15s default expires inside
    probe #1 with Arco perfectly healthy.
  --setup-seconds: `player` is a SCORED role and RegistrationState.join()
    refuses scored roles once RUNNING, so a device must join during SETUP.
  --join-retry: a join sent before Control is listening is dropped by Arco
    with no queue behind it.
  --exit-with-parent: an o2lite client that outlives its supervisor
    re-claims its dev name on the next hub, where O2 silently refuses the
    next run's own client.

WHAT IT CANNOT FIX. docs/MM_TERRARIUM.md records that a headless device
often never clock-syncs after Control's /host/clear, and that this does not
reproduce from an interactive terminal. The cause is unknown and upstream.
This runner does not fix it and does not pretend to. What it contributes is
that the failure is BOUNDED and NAMED rather than a hang.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

from control.process import stop_process
from control.teardown import TeardownStack
from harness import markers
from harness.proc_tee import ProcTee
from harness.signals import sigterm_as_keyboard_interrupt

DEFAULT_ARCO_COMMAND = "/Users/chris/projects/arco/apps/pytest/server"
PLAYER_NODE = "TEST_PLAYER_NODE"


@dataclass
class StackConfig:
    log_dir: str
    arco_command: str = DEFAULT_ARCO_COMMAND
    devices: int = 1
    ensemble: str = "arco"
    # 90s, not 20s. A live run on 2026-08-14 measured device cold start at
    # about 22s (python, luxaeterna import, WebSim backend, o2lite discovery,
    # clock sync), and devices are spawned only AFTER Control reports SETUP,
    # so the whole cold start burns this window. At 20s it closed first and
    # the device was refused: `player` is a scored role, and
    # RegistrationState.join() refuses scored roles once RUNNING.
    setup_seconds: float = 90.0
    seconds: float | None = None      # None means hold until Ctrl-C
    horizon: float = 0.060
    echo: bool = True
    ready_timeout: float = 90.0       # covers the ~18s first probe with room
    join_timeout: float = 60.0
    settle_seconds: float = 5.0
    arco_ready_timeout: float = 60.0
    console_port: int | None = None   # None = no Terrarium Console


@dataclass
class RunResult:
    ok: bool
    stage: str
    detail: str
    logs: dict = field(default_factory=dict)


def control_command(cfg: StackConfig, ppid: int) -> list[str]:
    command = [
        sys.executable, "-u", "-m", "harness.terrarium_boot",
        "--transport", "o2lite",
        "--arco-command", cfg.arco_command,
        "--arco-pty",
        "--arco-log", os.path.join(cfg.log_dir, "arco.log"),
        "--arco-settle-seconds", str(cfg.settle_seconds),
        "--arco-ready-timeout", str(cfg.arco_ready_timeout),
        "--setup-seconds", str(cfg.setup_seconds),
        "--horizon", str(cfg.horizon),
        "--hold",
        # Symmetric with the devices' own --exit-with-parent below: a
        # SIGKILLed or OOM-killed run_stack cannot signal this process
        # either, and without this flag terrarium_boot -- and through it
        # Arco and the Room simulator -- would keep running un-signalled
        # in their own session. See F5 in the final review.
        "--exit-with-parent", str(ppid),
    ]
    if cfg.console_port is not None:
        command += ["--console-port", str(cfg.console_port)]
    return command


def device_command(cfg: StackConfig, index: int, ppid: int) -> list[str]:
    dev = f"ie{index}"
    return [
        sys.executable, "-u", "-m", "harness.o2_shroom",
        "--dev", dev,
        "--node", PLAYER_NODE,
        "--ensemble", cfg.ensemble,
        "--join-retry", "2.0",
        "--control-horizon", str(cfg.horizon),
        "--samples-out", os.path.join(cfg.log_dir, f"{dev}-samples.json"),
        "--exit-with-parent", str(ppid),
    ]


def run(cfg: StackConfig, *, popen=subprocess.Popen, clock=time.monotonic,
        sleep=time.sleep, getpid=os.getpid) -> RunResult:
    """Bring the stack up, hold it, and tear it down in order.

    Every process is registered on the TeardownStack at the moment it is
    spawned, so Control (spawned first) stops last and the devices stop
    before it. That is the whole point of the primitive: the ordering is a
    consequence of startup, not a list this function maintains.
    """
    os.makedirs(cfg.log_dir, exist_ok=True)
    teardown = TeardownStack()
    tees: dict[str, ProcTee] = {}
    processes: dict[str, object] = {}
    logs = {}

    def spawn(name: str, command: list[str], watch) -> ProcTee:
        process = popen(command, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True,
                        start_new_session=True)
        processes[name] = process
        teardown.push(name, lambda: _stop(process, tees.get(name)))
        log_path = os.path.join(cfg.log_dir, f"{name}.log")
        logs[name] = log_path
        tee = ProcTee(name, process.stdout, log_path, markers=watch,
                      echo=cfg.echo)
        tee.start()
        tees[name] = tee
        return tee

    try:
        control = spawn("control", control_command(cfg, getpid()),
                        _watch_list("CONTROL_"))

        for stage, marker, detail in (
            ("control-ready", markers.CONTROL_TRANSPORT_READY,
             "Control never reported its o2lite transport up. Check "
             "arco.log for a failed Arco start, and o2debug.log."),
            ("control-setup", markers.CONTROL_SETUP_HOLD,
             "Control came up but never opened registration."),
        ):
            if not control.wait_for(marker, cfg.ready_timeout, clock, sleep):
                return RunResult(False, stage, detail, logs)

        devices = []
        for index in range(1, cfg.devices + 1):
            devices.append(spawn(
                f"ie{index}", device_command(cfg, index, getpid()),
                _watch_list("DEVICE_")))

        for tee in devices:
            ok, failed = _wait_for_marker(tee, markers.DEVICE_CLOCK_SYNCED,
                                          cfg.join_timeout, clock, sleep)
            if failed is not None:
                return RunResult(False, "device-join", failed, logs)
            if not ok:
                return RunResult(
                    False, "device-sync",
                    f"{tee.name} never clock-synced. This is the documented "
                    f"headless clock-sync defect: pyarco's initialize() sends "
                    f"/host/clear and a NEW o2lite client then hangs on "
                    f"time_get() < 0. It does not reproduce from an "
                    f"interactive terminal. Check o2debug.log -- 'dropping "
                    f"message because service was not found' means Control "
                    f"was not up yet, and total silence means the socket is "
                    f"dead. See docs/MM_TERRARIUM.md 'Not yet built'.", logs)
            ok, failed = _wait_for_marker(tee, markers.DEVICE_ROLE_GRANTED,
                                          cfg.join_timeout, clock, sleep)
            if failed is not None:
                return RunResult(False, "device-join", failed, logs)
            if not ok:
                return RunResult(
                    False, "device-join",
                    f"{tee.name} synced but was never granted a role. Is "
                    f"Control still in SETUP? `player` is a scored role "
                    f"and is refused once RUNNING.", logs)

        dead = _hold(cfg, processes, clock, sleep)
        if dead is not None:
            name, code = dead
            return RunResult(
                False, "child-exited",
                f"{name} exited (code {code!r}) during the hold, before "
                f"the run ended on its own. Check {name}.log for what "
                f"happened.", logs)
        return RunResult(True, "complete", "", logs)
    except KeyboardInterrupt:
        return RunResult(True, "interrupted", "stopped by Ctrl-C", logs)
    finally:
        for name, exc in teardown.close():
            print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)


def _watch_list(prefix: str) -> list[str]:
    """The markers a spawned child's ProcTee should track.

    Derived from markers.READY_MARKERS and markers.FAILURE_MARKERS rather
    than hand-listed, so a marker added to either dict is automatically
    watched. Without this, a third failure marker landing in
    FAILURE_MARKERS but not in a device's watch list would make
    tee.seen(marker) raise KeyError the moment _failed_marker asked about
    it (ProcTee only creates an Event for markers it was told to watch) --
    the same desynchronisation bug _failed_marker itself used to have, one
    layer up.

    `prefix` picks the role: "CONTROL_" or "DEVICE_", matching
    markers.py's naming convention. FAILURE_MARKERS is device-only today
    (every failure marker Control's own log is watched for is instead a
    ready/unready timeout, not a diagnosed failure), so it is folded into
    the device watch list only.
    """
    watch = [v for k, v in markers.READY_MARKERS.items()
             if k.startswith(prefix)]
    if prefix == "DEVICE_":
        watch += list(markers.FAILURE_MARKERS.values())
    return watch


_FAILURE_REMEDIES = {
    "DEVICE_JOIN_DENIED": lambda tee: (
        f"{tee.name}: Control refused the join. See "
        f"{tee.name}.log for the reason and hint."),
    "DEVICE_SERVICE_CONFLICT": lambda tee: (
        f"{tee.name}: the hub refused this device's service "
        f"announcement because another process already offers that "
        f"name. Look for a stale `python -m harness.o2_shroom "
        f"--dev {tee.name}` and kill it."),
}


def _failed_marker(tee: ProcTee) -> str | None:
    """Both of the failure markers today are conditions the child has
    already diagnosed precisely, and neither ever recovers, so waiting out
    the timeout is pure lost time.

    Iterates markers.FAILURE_MARKERS -- the single source of truth for
    what counts as a failure -- instead of hand-checking each name by
    value. A marker added to that dict without a matching entry in
    _FAILURE_REMEDIES now fails LOUD (KeyError) rather than silently
    doing nothing and regressing to the full-timeout behaviour
    tests/test_run_stack.py's test_a_denied_join_fails_immediately and
    test_a_service_conflict_fails_immediately exist to catch -- the same
    bug class run()'s device-join loop was already fixed for once on this
    branch (see _wait_for_marker's docstring), now closed at this level
    too.
    """
    for name, marker in markers.FAILURE_MARKERS.items():
        if tee.seen(marker):
            return _FAILURE_REMEDIES[name](tee)
    return None


def _wait_for_marker(tee: ProcTee, target: str, timeout: float, clock,
                     sleep) -> tuple[bool, str | None]:
    """Poll for `target`, without going deaf to a failure marker for the
    whole timeout budget.

    DEVICE_JOIN_DENIED and DEVICE_SERVICE_CONFLICT both arrive AFTER
    DEVICE_CLOCK_SYNCED in the real join sequence -- a device syncs its
    clock and only then finds out whether the join was accepted. A plain
    ProcTee.wait_for(target, ...) call watches exactly one marker, so
    calling it for DEVICE_ROLE_GRANTED while a denial lands moments later
    would sit out the entire cfg.join_timeout on a join that had already
    failed: the two per-call _failed_marker() checks that used to bracket
    the wait_for calls only catch a failure that was ALREADY seen before
    the wait started or after it timed out, not one that arrives mid-wait.
    Same poll cadence as ProcTee.wait_for (0.05s); this just checks a
    second condition on every pass instead of finding out only at the
    edges.
    """
    deadline = clock() + timeout
    while True:
        if tee.seen(target):
            return True, None
        failed = _failed_marker(tee)
        if failed is not None:
            return False, failed
        if clock() >= deadline:
            return False, None
        sleep(0.05)


def _hold(cfg: StackConfig, children: dict[str, object], clock,
         sleep) -> tuple[str, int] | None:
    """Run for --seconds, or until Ctrl-C when no duration was asked for.

    Polls every spawned child on each tick and returns as soon as one has
    exited, instead of only watching the clock. Design spec section 3.4
    promises --ci "exits non-zero on any unmet marker or non-zero child
    exit"; before this, nothing here read process exit status at all, so a
    `--ci` run with a `control` child marked exited still returned
    ok=True, stage="complete" -- verified live by the final review.
    Mirrors harness/terrarium_boot.py's _serve_until_done, which polls
    Arco (`arco.poll() is not None`) on every tick of its own loop for the
    same reason: a dead child is news the instant it happens, not news
    worth waiting out the rest of the hold for.
    """
    if cfg.seconds is None:
        while True:
            dead = _dead_child(children)
            if dead is not None:
                return dead
            sleep(0.5)
    deadline = clock() + cfg.seconds
    while clock() < deadline:
        dead = _dead_child(children)
        if dead is not None:
            return dead
        sleep(0.1)
    return None


def _dead_child(children: dict[str, object]) -> tuple[str, int] | None:
    """The first child (in spawn order: control, then ie1, ie2, ...)
    whose process has already exited, paired with its exit code."""
    for name, process in children.items():
        code = process.poll()
        if code is not None:
            return name, code
    return None


def _stop(process, tee) -> None:
    """Stop one child and then drain what it said on the way out.

    Order matters within the step too: a device prints its whole lateness
    summary from a finally block, so joining the tee BEFORE the process has
    exited would cut it off, and skipping the join entirely would lose it.
    """
    stop_process(process)
    if tee is not None:
        tee.join(timeout=2.0)


CI_DEFAULT_SECONDS = 45.0


def parse_args(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        description="Run the whole Arco stack from one command.",
        epilog="Needs PYTHONPATH=/Users/chris/projects/arco for pyarco and "
               "o2litepy. CI mode is BEST-EFFORT: the headless clock-sync "
               "defect documented in docs/MM_TERRARIUM.md is upstream and "
               "unfixed, and this runner bounds and names it rather than "
               "fixing it.")
    ap.add_argument("--ci", action="store_true",
                    help="Non-interactive: no terminal echo, a bounded run, "
                         "and a non-zero exit on any failure.")
    ap.add_argument("--devices", type=int, default=1,
                    help="How many simulated player devices to join.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="How long to hold the stack up. Default: forever "
                         "(Ctrl-C to stop), or 45s under --ci.")
    ap.add_argument("--log-dir", default=None,
                    help="Where per-process logs and sample files go. "
                         "Default: runs/<timestamp>/.")
    ap.add_argument("--ensemble", default="arco")
    ap.add_argument("--arco-command", default=DEFAULT_ARCO_COMMAND)
    ap.add_argument("--setup-seconds", type=float, default=90.0,
                    help="How long Control holds registration open. A "
                         "device must join a SCORED role inside this "
                         "window or be refused. Default 90s, not a round "
                         "number: a live run measured device cold start at "
                         "~22s, and devices only spawn once Control reports "
                         "SETUP, so the cold start is inside this window.")
    ap.add_argument("--horizon", type=float, default=0.060,
                    help="Cue scheduling horizon, passed to both Control "
                         "and the devices so their reports agree.")
    ap.add_argument("--console-port", type=int, default=None,
                    help="Serve the Terrarium Console on this port and print "
                         "its URL. Off by default.")
    return ap.parse_args(argv)


def config_from_args(args) -> StackConfig:
    log_dir = args.log_dir or os.path.join(
        "runs", time.strftime("%Y%m%d-%H%M%S"))
    seconds = args.seconds
    if seconds is None and args.ci:
        seconds = CI_DEFAULT_SECONDS      # an unbounded CI run is a hung job
    return StackConfig(
        log_dir=log_dir, arco_command=args.arco_command,
        devices=args.devices, ensemble=args.ensemble,
        setup_seconds=args.setup_seconds, seconds=seconds,
        horizon=args.horizon, echo=not args.ci,
        console_port=args.console_port)


def _failing_log_key(result: RunResult) -> str | None:
    """Which of result.logs the failure is actually about.

    Deriving this from the stage name alone only works for the two control
    stages: "control-ready" and "control-setup" both reduce to "control",
    and "control" IS a real log key. It does not work for the device
    stages -- "device-sync" and "device-join" both reduce to "device", and
    no log is ever keyed "device"; devices are keyed "ie1", "ie2", and so
    on. run() writes every device-stage detail as f"{tee.name} ..." (or
    f"{tee.name}: ..." for the two _failed_marker cases), so the failing
    device's own name is always the detail's first word. Parsing that is
    what actually finds the right log; guessing from the stage prefix
    does not.
    """
    if result.stage.startswith("control"):
        return "control"
    if not result.detail:
        return None
    first_word = result.detail.split(None, 1)[0].rstrip(":")
    return first_word if first_word in result.logs else None


def format_failure(result: RunResult, tail_lines: int = 20) -> str:
    lines = [
        "",
        "=" * 70,
        f"STACK RUN FAILED at stage {result.stage!r}",
        result.detail,
        "",
    ]
    for name, path in result.logs.items():
        lines.append(f"  {name}: {path}")
    lines.append("")
    failing = result.logs.get(_failing_log_key(result)) or \
        next(iter(reversed(list(result.logs.values()))), None)
    if failing:
        lines.append(f"last {tail_lines} lines of {failing}:")
        try:
            with open(failing, encoding="utf-8") as handle:
                for line in handle.read().splitlines()[-tail_lines:]:
                    lines.append(f"  | {line}")
        except OSError as exc:
            lines.append(f"  (could not read: {exc})")
    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> None:
    sigterm_as_keyboard_interrupt()
    args = parse_args()
    cfg = config_from_args(args)

    try:
        from o2litepy import o2lite      # noqa: F401 (import is the check)
    except ImportError:
        print("run_stack needs o2litepy on the path. Re-run with "
              "PYTHONPATH=/Users/chris/projects/arco", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"logs: {cfg.log_dir}")
    result = run(cfg)
    if result.ok:
        print(f"stack run {result.stage}; logs in {cfg.log_dir}")
        return
    print(format_failure(result), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
