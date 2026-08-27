"""Owned-pid run records and the load-time stale sweep.

`load_room` begins by clearing anything left over from a crashed prior run
(design spec docs/superpowers/specs/
2026-08-26-terrarium-lifecycle-and-config-rooms-design.md section 5). Every
process a room load spawns is recorded (pid + spawn time + role) in a
run-scoped `procs.jsonl` under `runs/<run-id>/`; the sweep kills ONLY pids
found in such records, and only after confirming the pid still names the
process we started (a spawn-time comparison guards pid reuse). This module
never pattern-matches on process name -- a dev box legitimately runs
multiple stacks, and a name-based sweep is a machine-wide footgun.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass

from control.process import stop_process
from control.wire_json import dumps

logger = logging.getLogger(__name__)

# Pid reuse guard: how far a live pid's current spawn time may drift from
# the recorded one before we refuse to treat it as the process we started.
# Non-zero because spawn time is captured (recorded) and re-read (swept) at
# different moments and the `ps -o lstart=` seconds-resolution timestamp
# used by the default helper can itself round either way.
_SPAWN_TIME_TOLERANCE = 2.0


@dataclass(frozen=True)
class SpawnRecord:
    pid: int
    spawn_time: float
    role: str                      # "arco" | "simulator:<fixture>"


class RunRecorder:
    """Appends one JSON line per spawned process to `path`
    (runs/<run-id>/procs.jsonl). Written with control/wire_json.dumps, per
    this repo's rule that no outbound/persisted JSON uses bare
    json.dumps."""

    def __init__(self, path: str) -> None:
        self.path = path

    def record(self, pid: int, role: str, *, spawn_time: float) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        line = dumps({"pid": pid, "spawn_time": spawn_time, "role": role})
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    @staticmethod
    def load_all(runs_dir: str) -> list[SpawnRecord]:
        """Every record from every procs.jsonl found under runs_dir, one
        run-id directory at a time."""
        records: list[SpawnRecord] = []
        for path in _record_files(runs_dir):
            records.extend(_read_records(path))
        return records


def _record_files(runs_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(runs_dir, "*", "procs.jsonl")))


def _read_records(path: str) -> list[SpawnRecord]:
    records: list[SpawnRecord] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            records.append(SpawnRecord(
                pid=obj["pid"], spawn_time=obj["spawn_time"], role=obj["role"]))
    return records


def _default_is_alive(pid: int) -> bool:
    """os.kill(pid, 0) sends no signal, just checks. PermissionError means
    the pid exists but is owned by someone else -- still alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _default_spawn_time(pid: int) -> float | None:
    """Best-effort spawn time for `pid`, epoch seconds, via `ps -o lstart=`
    (darwin/linux). None if the pid can't be read (already gone, or a
    platform without lstart)."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, OSError):
        return None
    line = result.stdout.strip()
    if not line:
        return None
    try:
        parsed = time.strptime(line, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return time.mktime(parsed)


def _default_stop(pid: int):
    """Bounded SIGTERM->SIGKILL via control/process.py:stop_process,
    adapted to a bare pid: stop_process needs the poll()/send_signal()
    slice, which this tiny handle provides over os.kill."""
    return stop_process(_PidHandle(pid))


class _PidHandle:
    """The poll()/send_signal() slice of subprocess.Popen that
    control/process.py's stop_process needs, over a bare pid this process
    did not spawn itself (it was recorded by a prior run)."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._reaped = False

    def poll(self):
        if self._reaped:
            return 0
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            self._reaped = True
            return 0
        except PermissionError:
            return None
        return None

    def send_signal(self, sig: int) -> None:
        try:
            os.kill(self.pid, sig)
        except ProcessLookupError:
            self._reaped = True


def sweep_stale(runs_dir: str, *, stop=_default_stop,
                process_spawn_time=_default_spawn_time,
                is_alive=_default_is_alive) -> list[SpawnRecord]:
    """Kill every recorded pid this process can prove it owns, and consume
    (delete) each record file once every pid in it has been resolved.

    Per record: a dead pid is skipped (nothing to kill, already resolved --
    counts as handled). A live pid is killed only if its current spawn time
    matches the recorded one within _SPAWN_TIME_TOLERANCE -- a mismatch
    means the pid was recycled onto an unrelated process, so it is skipped
    AND NOT counted as handled, leaving the record in place rather than
    silently forgetting a pid we could not prove is (or isn't) ours.

    Never matches by process name -- only by recorded pid + spawn time.

    Cross-run safety: a run dir whose own procs.jsonl carries a "supervisor"
    record (control/terrarium.py's Terrarium writes one, at construction,
    when runs_dir/run_id are configured) that is still alive and spawn-time
    matching is skipped ENTIRELY -- none of its records are even inspected,
    let alone killed. Without this, on a dev box running two concurrent
    stacks, stack A's own load-time sweep would glob stack B's runs/*/
    procs.jsonl too and kill stack B's still-live, still-recorded Arco (its
    pid is alive and its spawn time matches -- sweep_stale has no other
    signal to tell "someone else's live run" apart from "a crashed prior
    run"). A dead or absent supervisor record leaves the dir sweepable
    exactly as before this guard existed.
    """
    acted: list[SpawnRecord] = []
    for path in _record_files(runs_dir):
        records = _read_records(path)
        supervisor = next((r for r in records if r.role == "supervisor"), None)
        if supervisor is not None and is_alive(supervisor.pid):
            current = process_spawn_time(supervisor.pid)
            if (current is not None
                    and abs(current - supervisor.spawn_time) <= _SPAWN_TIME_TOLERANCE):
                continue          # another run's supervisor is still alive
        all_handled = True
        for rec in records:
            if not is_alive(rec.pid):
                continue                      # already gone; handled
            current = process_spawn_time(rec.pid)
            if current is None or abs(current - rec.spawn_time) > _SPAWN_TIME_TOLERANCE:
                all_handled = False           # pid reuse guard: not ours (or unproven)
                continue
            try:
                stop(rec.pid)
            except Exception:
                # A failed stop (e.g. PermissionError against a foreign-
                # owned pid that happened to pass the spawn-time check)
                # must not abandon the remaining records/files in this
                # sweep -- one unkillable pid should not fail the whole
                # room load. Leave the record in place and move on.
                logger.exception(
                    "sweep_stale: failed to stop pid %d (role=%r); leaving "
                    "its record in place", rec.pid, rec.role)
                all_handled = False
                continue
            acted.append(rec)
        if all_handled:
            os.remove(path)
    return acted
