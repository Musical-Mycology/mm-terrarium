"""harness/o2proc_lookup.py: Arco's O2 process name for GET /o2proc
(spec docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md)."""
from control.terrarium import TerrariumState
from harness.o2proc_lookup import O2Record, O2ProcHolder, O2ProcWatcher, ProcName, find_local_arco, parse_proc_name, select_local

REAL = "@00000000:ac17f983:afb9:9f48"  # measured from a live Arco, 2026-09-29


def test_parse_real_arco_name():
    assert parse_proc_name(REAL) == ProcName("172.23.249.131", 44985, 40776)


def test_parse_rejects_malformed():
    for bad in ["", REAL[:-1], REAL + "0", "#" + REAL[1:],
                REAL.replace(":", ";", 1), "@0000000g:ac17f983:afb9:9f48",
                "<html>nope</html>", None]:
        assert parse_proc_name(bad) is None, bad


def _rec(name=REAL, instance="arco", port=44985):
    return O2Record(instance, port, name)


def test_select_one_local_match():
    assert select_local([_rec()], "arco", {"172.23.249.131"}) == (REAL, "")


def test_select_ignores_other_ensemble():
    name, reason = select_local([_rec(instance="other")], "arco",
                                {"172.23.249.131"})
    assert name is None and "no arco" in reason


def test_select_ignores_remote_arco():
    remote = "@00000000:c0a80105:afb9:9f48"  # 192.168.1.5, not this host
    name, reason = select_local([_rec(remote)], "arco", {"172.23.249.131"})
    assert name is None and "no arco" in reason


def test_select_ignores_port_mismatch_and_bad_txt():
    recs = [_rec(port=1234), _rec(name=None), _rec(name="garbage")]
    name, _ = select_local(recs, "arco", {"172.23.249.131"})
    assert name is None


def test_select_refuses_two_local_matches():
    other = "@00000000:ac17f983:1f90:1f91"
    name, reason = select_local([_rec(), _rec(other, port=0x1F90)], "arco",
                                {"172.23.249.131"})
    assert name is None and "2 local" in reason


def test_find_local_arco_retries_then_succeeds():
    calls = []

    def fake_browse():
        calls.append(1)
        return [] if len(calls) < 2 else [O2Record("arco", 44985, REAL)]

    name, reason = find_local_arco("arco", browse=fake_browse,
                                   local_ips=lambda: {"172.23.249.131"})
    assert (name, reason) == (REAL, "") and len(calls) == 2


def test_find_local_arco_gives_up_with_reason():
    name, reason = find_local_arco("arco", browse=lambda: [],
                                   local_ips=lambda: set(), attempts=2)
    assert name is None and reason


def test_holder_starts_unavailable():
    assert O2ProcHolder("arco").get() == (None, "arco not ready")


class _Deferred:
    """spawn() that holds the job until run() -- a thread we control."""
    def __init__(self):
        self.jobs = []

    def __call__(self, fn):
        self.jobs.append(fn)

    def run(self):
        jobs, self.jobs = self.jobs, []
        for fn in jobs:
            fn()


def test_room_ready_runs_lookup_and_fills_holder():
    holder, spawn = O2ProcHolder("arco"), _Deferred()
    w = O2ProcWatcher(holder, lookup=lambda: (REAL, ""), spawn=spawn)
    w.on_terrarium_state_change(TerrariumState.ROOM_LOADING,
                                TerrariumState.ROOM_READY)
    assert holder.get() == (None, "lookup pending")
    spawn.run()
    assert holder.get() == (REAL, "")


class _Sleeps:
    """Injected sleep: records delays, then runs an optional hook (a test's
    stand-in for what happens while the watcher waits)."""
    def __init__(self, hook=None):
        self.delays, self.hook = [], hook

    def __call__(self, delay):
        self.delays.append(delay)
        if self.hook:
            self.hook(len(self.delays))


def _end_room(w):
    """Bump the watcher's generation without touching the holder, so a retry
    loop ends and the holder keeps the failure reason under test."""
    def hook(n):
        with w._lock:
            w._gen += 1
    return hook


def test_lookup_failure_is_the_503_reason():
    holder, spawn, sleeps = O2ProcHolder("arco"), _Deferred(), _Sleeps()
    w = O2ProcWatcher(holder, lookup=lambda: (None, "no arco here"),
                      spawn=spawn, sleep=sleeps)
    sleeps.hook = _end_room(w)
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    spawn.run()
    assert holder.get() == (None, "no arco here")
    assert sleeps.delays == [5]


def test_failed_lookup_is_retried_with_backoff_until_it_succeeds():
    holder, spawn, sleeps = O2ProcHolder("arco"), _Deferred(), _Sleeps()
    results = iter([(None, "no arco here"), (None, "no arco here"),
                    (REAL, "")])
    seen = []
    w = O2ProcWatcher(holder, lookup=lambda: next(results), spawn=spawn,
                      sleep=sleeps)
    sleeps.hook = lambda n: seen.append(holder.get())
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    spawn.run()
    assert seen == [(None, "no arco here")] * 2
    assert sleeps.delays == [5, 10]
    assert holder.get() == (REAL, "")


def test_room_change_during_backoff_stops_retrying():
    holder, spawn, sleeps = O2ProcHolder("arco"), _Deferred(), _Sleeps()
    calls = []

    def lookup():
        calls.append(1)
        return None, "no arco here"

    w = O2ProcWatcher(holder, lookup=lookup, spawn=spawn, sleep=sleeps)
    sleeps.hook = lambda n: w.on_terrarium_state_change(
        TerrariumState.ROOM_READY, TerrariumState.ROOM_UNLOADING)
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    spawn.run()
    assert len(calls) == 1
    assert holder.get() == (None, "arco not ready")


def test_backoff_caps_at_thirty_seconds():
    holder, spawn, sleeps = O2ProcHolder("arco"), _Deferred(), _Sleeps()
    w = O2ProcWatcher(holder, lookup=lambda: (None, "x"), spawn=spawn,
                      sleep=sleeps)
    sleeps.hook = lambda n: _end_room(w)(n) if n == 7 else None
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    spawn.run()
    assert sleeps.delays == [5, 10, 20, 30, 30, 30, 30]


def test_unload_clears_the_name():
    holder, spawn = O2ProcHolder("arco"), _Deferred()
    w = O2ProcWatcher(holder, lookup=lambda: (REAL, ""), spawn=spawn)
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    spawn.run()
    w.on_terrarium_state_change(TerrariumState.ROOM_READY,
                                TerrariumState.ROOM_UNLOADING)
    assert holder.get() == (None, "arco not ready")


def test_result_after_unload_is_discarded():
    holder, spawn = O2ProcHolder("arco"), _Deferred()
    w = O2ProcWatcher(holder, lookup=lambda: (REAL, ""), spawn=spawn)
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    w.on_terrarium_state_change(TerrariumState.ROOM_READY,
                                TerrariumState.ROOM_UNLOADING)
    spawn.run()  # the stale lookup finishes late
    assert holder.get() == (None, "arco not ready")


def test_seed_when_already_ready_starts_lookup():
    holder, spawn = O2ProcHolder("arco"), _Deferred()
    w = O2ProcWatcher(holder, lookup=lambda: (REAL, ""), spawn=spawn)
    w.seed(TerrariumState.ROOM_READY)
    spawn.run()
    assert holder.get() == (REAL, "")


def test_seed_when_no_room_does_nothing():
    holder, spawn = O2ProcHolder("arco"), _Deferred()
    O2ProcWatcher(holder, lookup=lambda: (REAL, ""), spawn=spawn).seed(
        TerrariumState.NO_ROOM)
    assert spawn.jobs == [] and holder.get() == (None, "arco not ready")


def test_lookup_exception_is_caught_and_logged(caplog):
    holder, spawn = O2ProcHolder("arco"), _Deferred()

    def failing_lookup():
        raise OSError("no interface")

    sleeps = _Sleeps()
    w = O2ProcWatcher(holder, lookup=failing_lookup, spawn=spawn, sleep=sleeps)
    sleeps.hook = _end_room(w)
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    assert holder.get() == (None, "lookup pending")

    import logging
    with caplog.at_level(logging.ERROR):
        spawn.run()

    name, reason = holder.get()
    assert name is None
    assert "lookup failed: no interface" in reason
    assert "o2proc lookup failed" in caplog.text
