"""`./terrarium.sh --clean`: clear leftovers of past Terrarium runs from
EVERY checkout of this repo, then report whatever is still running.

`load_room`'s own stale sweep (control/run_record.py) only reads the
`runs/` of the checkout it was launched from, so a crashed stack started
from the main clone or another worktree is invisible to it. This walks
`git worktree list` and runs the same `sweep_stale` in each checkout's
`runs/` -- same rules: only recorded pids, only after the spawn-time
check, and never a run whose supervisor is still alive.

What it will NOT kill, it reports, with the command to stop it:
  - a LIVE stack (its supervisor is alive -- a `--detach` launch, or a
    stack another session is using). SIGINT to the supervisor
    (terrarium_boot) runs its ordered teardown; run_stack's hold ends as
    soon as that child exits and tears the rest down.
  - an UNRECORDED harness process (run_stack and Testshrooms write no run
    records). Found by command line, listed only: run_record's rule
    against name-matching is about killing, and this never kills by name.

Exit 0 when nothing is left running, 1 otherwise.

    .venv/bin/python -m harness.clean
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from control.run_record import live_supervisors, sweep_stale

_HARNESS = re.compile(r"harness[./](run_stack|terrarium_boot|o2_shroom)\b")


def list_checkouts(*, run=subprocess.run, isdir=os.path.isdir,
                   cwd=os.getcwd) -> list[str]:
    """Every checkout of this repo (the main clone first), minus any whose
    directory no longer exists (a prunable worktree). Where git cannot list
    worktrees (the dev container's snapshot has no .git, and a mounted git
    worktree's .git file points at a host path), only the current checkout
    is swept."""
    try:
        result = run(["git", "worktree", "list", "--porcelain"],
                     capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [cwd()]
    paths = [line[len("worktree "):] for line in result.stdout.splitlines()
             if line.startswith("worktree ")]
    return [p for p in paths if isdir(p)]


def parse_ps(text: str) -> list[tuple[int, int, str]]:
    """`ps -Ao pid=,ppid=,command=` output -> (pid, ppid, command)."""
    procs = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            procs.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return procs


def _default_processes() -> list[tuple[int, int, str]]:
    result = subprocess.run(["ps", "-Ao", "pid=,ppid=,command="],
                            capture_output=True, text=True, check=True)
    return parse_ps(result.stdout)


def _covered(live_pids, procs) -> set[int]:
    """Every process belonging to a live stack: the supervisor's parent
    when that parent is a harness process (run_stack), and everything
    under it -- so the stack's Testshrooms are not listed a second time
    as unrecorded suspects."""
    parent = {pid: ppid for pid, ppid, _ in procs}
    harness = {pid for pid, _, cmd in procs if _HARNESS.search(cmd)}
    roots = set()
    for pid in live_pids:
        ppid = parent.get(pid)
        roots.add(ppid if ppid in harness else pid)
    covered = set(roots)
    grew = True
    while grew:
        grew = False
        for pid, ppid, _ in procs:
            if ppid in covered and pid not in covered:
                covered.add(pid)
                grew = True
    return covered


def clean(checkouts, *, sweep=sweep_stale, live_runs=live_supervisors,
          processes=_default_processes, isdir=os.path.isdir,
          out=sys.stdout) -> int:
    live = []
    for checkout in checkouts:
        runs_dir = os.path.join(checkout, "runs")
        if not isdir(runs_dir):
            continue
        for rec in sweep(runs_dir):
            print(f"swept {runs_dir}: stopped pid {rec.pid} ({rec.role})",
                  file=out)
        live.extend(live_runs(runs_dir))

    for run in live:
        owned = ", ".join(f"{r.role} {r.pid}" for r in run.records) or "none"
        print(f"LIVE stack {run.run_dir}: supervisor pid {run.supervisor.pid};"
              f" recorded: {owned}", file=out)
        print(f"  stop it: kill -INT {run.supervisor.pid}", file=out)

    procs = processes()
    covered = _covered({r.supervisor.pid for r in live}, procs)
    suspects = [(pid, cmd) for pid, _, cmd in procs
                if _HARNESS.search(cmd) and pid not in covered]
    for pid, cmd in suspects:
        print(f"UNRECORDED pid {pid}: {cmd}", file=out)
        print(f"  stop it: kill -INT {pid}", file=out)

    if live or suspects:
        return 1
    print("clean: nothing left running", file=out)
    return 0


def main() -> int:
    return clean(list_checkouts())


if __name__ == "__main__":
    sys.exit(main())
