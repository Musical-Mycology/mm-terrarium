import json
import os

import pytest

from control.run_record import RunRecorder, SpawnRecord, sweep_stale


def test_record_append_load_round_trip_through_wire_json(tmp_path):
    path = tmp_path / "run-1" / "procs.jsonl"
    recorder = RunRecorder(str(path))
    recorder.record(111, "arco", spawn_time=1000.0)
    recorder.record(222, "simulator:main", spawn_time=1000.5)

    # Written via control/wire_json.dumps, not bare json.dumps -- confirm
    # the file is plain-JSON-parseable, one object per line.
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert lines == [
        {"pid": 111, "spawn_time": 1000.0, "role": "arco"},
        {"pid": 222, "spawn_time": 1000.5, "role": "simulator:main"},
    ]

    records = RunRecorder.load_all(str(tmp_path))
    assert records == [
        SpawnRecord(pid=111, spawn_time=1000.0, role="arco"),
        SpawnRecord(pid=222, spawn_time=1000.5, role="simulator:main"),
    ]


def test_load_all_reads_every_procs_jsonl_under_runs_dir(tmp_path):
    RunRecorder(str(tmp_path / "run-1" / "procs.jsonl")).record(
        1, "arco", spawn_time=1.0)
    RunRecorder(str(tmp_path / "run-2" / "procs.jsonl")).record(
        2, "arco", spawn_time=2.0)

    records = RunRecorder.load_all(str(tmp_path))
    assert {r.pid for r in records} == {1, 2}


class _FakeProcessTable:
    """Boundary-rule-5 fake: raises if asked to kill a pid it never
    spawned. Models liveness/spawn-time by an explicit in-memory table so
    tests never touch a real process."""

    def __init__(self, alive: dict[int, float]) -> None:
        self._alive = dict(alive)     # pid -> spawn_time
        self._spawned = set(alive)
        self.stopped: list[int] = []

    def is_alive(self, pid: int) -> bool:
        return pid in self._alive

    def spawn_time(self, pid: int):
        return self._alive.get(pid)

    def stop(self, pid: int) -> None:
        if pid not in self._spawned:
            raise AssertionError(f"refusing to kill unrecorded/unowned pid {pid}")
        self.stopped.append(pid)
        self._alive.pop(pid, None)


def _sweep(runs_dir, table: _FakeProcessTable):
    return sweep_stale(
        runs_dir, stop=table.stop,
        process_spawn_time=table.spawn_time, is_alive=table.is_alive)


def test_sweep_kills_recorded_alive_time_matching_pid(tmp_path):
    RunRecorder(str(tmp_path / "run-1" / "procs.jsonl")).record(
        100, "arco", spawn_time=500.0)
    table = _FakeProcessTable({100: 500.0})

    acted = _sweep(str(tmp_path), table)

    assert table.stopped == [100]
    assert acted == [SpawnRecord(pid=100, spawn_time=500.0, role="arco")]


def test_sweep_skips_dead_pid(tmp_path):
    RunRecorder(str(tmp_path / "run-1" / "procs.jsonl")).record(
        101, "arco", spawn_time=500.0)
    table = _FakeProcessTable({})   # nothing alive

    acted = _sweep(str(tmp_path), table)

    assert table.stopped == []
    assert acted == []


def test_sweep_skips_pid_whose_spawn_time_no_longer_matches(tmp_path):
    RunRecorder(str(tmp_path / "run-1" / "procs.jsonl")).record(
        102, "arco", spawn_time=500.0)
    # Same pid, alive, but spawn time drifted well beyond tolerance: pid was
    # recycled onto an unrelated process.
    table = _FakeProcessTable({102: 900.0})

    acted = _sweep(str(tmp_path), table)

    assert table.stopped == []
    assert acted == []


def test_sweep_never_touches_an_unrecorded_pid(tmp_path):
    RunRecorder(str(tmp_path / "run-1" / "procs.jsonl")).record(
        103, "arco", spawn_time=500.0)
    table = _FakeProcessTable({103: 500.0, 999: 500.0})   # 999 never recorded

    acted = _sweep(str(tmp_path), table)

    # sweep_stale must never even attempt to stop the unrecorded pid;
    # the fake would raise if it tried.
    assert table.stopped == [103]
    assert acted == [SpawnRecord(pid=103, spawn_time=500.0, role="arco")]


def test_sweep_consumes_record_file_after_clean_sweep(tmp_path):
    path = tmp_path / "run-1" / "procs.jsonl"
    RunRecorder(str(path)).record(104, "arco", spawn_time=500.0)
    table = _FakeProcessTable({104: 500.0})

    _sweep(str(tmp_path), table)

    assert not os.path.exists(path)


def test_sweep_continues_past_a_stop_that_raises(tmp_path):
    path_a = tmp_path / "run-1" / "procs.jsonl"
    RunRecorder(str(path_a)).record(107, "arco", spawn_time=500.0)
    path_b = tmp_path / "run-2" / "procs.jsonl"
    RunRecorder(str(path_b)).record(108, "arco", spawn_time=500.0)
    table = _FakeProcessTable({107: 500.0, 108: 500.0})

    def flaky_stop(pid):
        if pid == 107:
            raise PermissionError("not ours after all")
        table.stop(pid)

    acted = sweep_stale(
        str(tmp_path), stop=flaky_stop,
        process_spawn_time=table.spawn_time, is_alive=table.is_alive)

    # The raising stop must not abandon the rest of the sweep: pid 108 in
    # the other record file is still handled, and sweep_stale itself must
    # not raise.
    assert acted == [SpawnRecord(pid=108, spawn_time=500.0, role="arco")]
    assert table.stopped == [108]
    assert os.path.exists(path_a)      # 107's file kept: not handled
    assert not os.path.exists(path_b)  # 108's file consumed: handled


def test_sweep_keeps_record_file_when_a_pid_is_not_handled(tmp_path):
    path = tmp_path / "run-1" / "procs.jsonl"
    recorder = RunRecorder(str(path))
    recorder.record(105, "arco", spawn_time=500.0)
    recorder.record(106, "simulator:main", spawn_time=500.0)
    # 105 matches and gets killed; 106's pid was reused, so it is left
    # unresolved -- the file must survive so 106 isn't silently forgotten.
    table = _FakeProcessTable({105: 500.0, 106: 999.0})

    acted = _sweep(str(tmp_path), table)

    assert table.stopped == [105]
    assert acted == [SpawnRecord(pid=105, spawn_time=500.0, role="arco")]
    assert os.path.exists(path)
