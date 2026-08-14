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

DEFAULT_ARCO_COMMAND = "/Users/chris/projects/arco/apps/pytest/server"
PLAYER_NODE = "TEST_PLAYER_NODE"


@dataclass
class StackConfig:
    log_dir: str
    arco_command: str = DEFAULT_ARCO_COMMAND
    devices: int = 1
    ensemble: str = "arco"
    setup_seconds: float = 20.0
    seconds: float | None = None      # None means hold until Ctrl-C
    horizon: float = 0.060
    echo: bool = True
    ready_timeout: float = 90.0       # covers the ~18s first probe with room
    join_timeout: float = 60.0
    settle_seconds: float = 5.0
    arco_ready_timeout: float = 60.0


@dataclass
class RunResult:
    ok: bool
    stage: str
    detail: str
    logs: dict = field(default_factory=dict)


def control_command(cfg: StackConfig) -> list[str]:
    return [
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
    ]


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
    logs = {}

    def spawn(name: str, command: list[str], watch) -> ProcTee:
        process = popen(command, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True,
                        start_new_session=True)
        teardown.push(name, lambda: _stop(process, tees.get(name)))
        log_path = os.path.join(cfg.log_dir, f"{name}.log")
        logs[name] = log_path
        tee = ProcTee(name, process.stdout, log_path, markers=watch,
                      echo=cfg.echo)
        tee.start()
        tees[name] = tee
        return tee

    try:
        control = spawn("control", control_command(cfg),
                        [markers.CONTROL_TRANSPORT_READY,
                         markers.CONTROL_SETUP_HOLD])

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
                [markers.DEVICE_CLOCK_SYNCED, markers.DEVICE_ROLE_GRANTED,
                 markers.DEVICE_JOIN_DENIED, markers.DEVICE_SERVICE_CONFLICT]))

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

        _hold(cfg, control, clock, sleep)
        return RunResult(True, "complete", "", logs)
    except KeyboardInterrupt:
        return RunResult(True, "interrupted", "stopped by Ctrl-C", logs)
    finally:
        for name, exc in teardown.close():
            print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)


def _failed_marker(tee: ProcTee) -> str | None:
    """Both of these are conditions the child has already diagnosed
    precisely, and neither ever recovers, so waiting out the timeout is
    pure lost time."""
    if tee.seen(markers.DEVICE_JOIN_DENIED):
        return (f"{tee.name}: Control refused the join. See "
                f"{tee.name}.log for the reason and hint.")
    if tee.seen(markers.DEVICE_SERVICE_CONFLICT):
        return (f"{tee.name}: the hub refused this device's service "
                f"announcement because another process already offers that "
                f"name. Look for a stale `python -m harness.o2_shroom "
                f"--dev {tee.name}` and kill it.")
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


def _hold(cfg: StackConfig, control: ProcTee, clock, sleep) -> None:
    """Run for --seconds, or until Ctrl-C when no duration was asked for."""
    if cfg.seconds is None:
        while True:
            sleep(0.5)
    deadline = clock() + cfg.seconds
    while clock() < deadline:
        sleep(0.1)


def _stop(process, tee) -> None:
    """Stop one child and then drain what it said on the way out.

    Order matters within the step too: a device prints its whole lateness
    summary from a finally block, so joining the tee BEFORE the process has
    exited would cut it off, and skipping the join entirely would lose it.
    """
    stop_process(process)
    if tee is not None:
        tee.join(timeout=2.0)
