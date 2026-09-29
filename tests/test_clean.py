"""harness/clean.py: `./terrarium.sh --clean`. Every seam (worktree
lister, sweep, live-run report, process table) is injected, so nothing
here touches git, a real process, or a real signal."""
import io
import subprocess

from control.run_record import LiveRun, SpawnRecord
from harness import clean

PORCELAIN = """\
worktree /repo
HEAD 9dd35c3
branch refs/heads/main

worktree /repo/.claude/worktrees/a
HEAD 9dd35c3
branch refs/heads/claude/a

worktree /gone
HEAD ea58d21
detached
prunable gitdir file points to non-existent location

"""


def test_list_checkouts_parses_porcelain_and_drops_missing_dirs():
    def run(cmd, **kw):
        assert cmd == ["git", "worktree", "list", "--porcelain"]
        return subprocess.CompletedProcess(cmd, 0, stdout=PORCELAIN)

    got = clean.list_checkouts(run=run, isdir=lambda p: p != "/gone")
    assert got == ["/repo", "/repo/.claude/worktrees/a"]


class _Fakes:
    def __init__(self, *, runs=(), swept=None, live=None, procs=()):
        self.runs = set(runs)              # checkouts that have runs/
        self.swept = swept or {}           # runs_dir -> [SpawnRecord]
        self.live = live or {}             # runs_dir -> [LiveRun]
        self.procs = list(procs)           # (pid, ppid, command)
        self.sweep_calls: list[str] = []

    def isdir(self, path):
        return path.endswith("/runs") and path[:-len("/runs")] in self.runs

    def sweep(self, runs_dir):
        self.sweep_calls.append(runs_dir)
        return self.swept.get(runs_dir, [])

    def live_runs(self, runs_dir):
        return self.live.get(runs_dir, [])

    def processes(self):
        return self.procs


def _clean(checkouts, f: _Fakes):
    out = io.StringIO()
    code = clean.clean(checkouts, sweep=f.sweep, live_runs=f.live_runs,
                       processes=f.processes, isdir=f.isdir, out=out)
    return code, out.getvalue()


def test_sweeps_every_checkout_that_has_a_runs_dir_and_skips_the_rest():
    f = _Fakes(runs={"/a", "/c"})
    code, text = _clean(["/a", "/b", "/c"], f)
    assert f.sweep_calls == ["/a/runs", "/c/runs"]
    assert code == 0
    assert "nothing left running" in text


def test_reports_what_the_sweep_stopped():
    f = _Fakes(runs={"/a"}, swept={"/a/runs": [
        SpawnRecord(pid=41, spawn_time=1.0, role="arco")]})
    code, text = _clean(["/a"], f)
    assert code == 0
    assert "stopped pid 41 (arco)" in text


def test_a_live_stack_is_reported_with_its_kill_command_and_exits_1():
    live = LiveRun(run_dir="/a/runs/20260928-120000",
                   supervisor=SpawnRecord(pid=50, spawn_time=1.0, role="supervisor"),
                   records=[SpawnRecord(pid=51, spawn_time=1.0, role="arco")])
    f = _Fakes(runs={"/a"}, live={"/a/runs": [live]})
    code, text = _clean(["/a"], f)
    assert code == 1
    assert "/a/runs/20260928-120000" in text
    assert "arco 51" in text
    assert "kill -INT 50" in text


def test_an_unrecorded_harness_process_is_listed_as_a_suspect():
    f = _Fakes(procs=[
        (70, 1, ".venv/bin/python -m harness.o2_shroom --dev sim-room"),
        (71, 1, "/usr/bin/vim notes.txt"),
    ])
    code, text = _clean([], f)
    assert code == 1
    assert "70" in text and "harness.o2_shroom" in text
    assert "kill -INT 70" in text
    assert "vim" not in text


def test_processes_belonging_to_a_live_stack_are_not_double_reported():
    # run_stack (60) -> terrarium_boot supervisor (61) and a Testshroom
    # sibling (62). All three are covered by the live-stack report.
    live = LiveRun(run_dir="/a/runs/r",
                   supervisor=SpawnRecord(pid=61, spawn_time=1.0, role="supervisor"),
                   records=[])
    f = _Fakes(runs={"/a"}, live={"/a/runs": [live]}, procs=[
        (60, 1, "python -m harness.run_stack --no-bit"),
        (61, 60, "python -m harness.terrarium_boot"),
        (62, 60, "python -m harness.o2_shroom --dev ie1"),
    ])
    code, text = _clean(["/a"], f)
    assert code == 1
    assert "kill -INT 61" in text
    assert "UNRECORDED" not in text


def test_parse_ps_skips_malformed_lines():
    text = "  10     1 python -m harness.run_stack\nbogus\n  11 10 x\n"
    assert clean.parse_ps(text) == [
        (10, 1, "python -m harness.run_stack"), (11, 10, "x")]


def test_list_checkouts_falls_back_to_cwd_when_git_fails():
    def run(cmd, **kw):
        raise subprocess.CalledProcessError(128, cmd, stderr="not a git repo")

    got = clean.list_checkouts(run=run, isdir=lambda p: True,
                               cwd=lambda: "/opt/mm/terrarium")
    assert got == ["/opt/mm/terrarium"]


def test_list_checkouts_falls_back_to_cwd_when_git_missing():
    def run(cmd, **kw):
        raise FileNotFoundError("git")

    got = clean.list_checkouts(run=run, isdir=lambda p: True,
                               cwd=lambda: "/work")
    assert got == ["/work"]
