# O2 Port Lookup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a dev shroom connect to a known Terrarium when mDNS is blocked,
by serving Arco's current O2 process name at `GET /o2proc` on the guest-page
server and teaching the firmware to fall back to it.

**Architecture:** Two parts in two repos.
- **Part A (mm-terrarium)** is standalone and verifiable with `curl`. A
  harness-side zeroconf lookup runs once per ROOM_READY and fills a
  thread-safe holder, which the existing `WwwServer` (port 8788) serves.
- **Part B (mm-devshroom)** adds an opt-in `O2_FALLBACK_HOST`. After mDNS
  has had `O2_FALLBACK_AFTER_MS`, the firmware fetches `/o2proc`, parses the
  ports, and connects through o2lite's public API.

**Tech Stack:** Python 3.13 stdlib + `zeroconf` + `netifaces` (both already
in `requirements-dev.txt`), pytest; C++11 on Arduino-ESP32 (`HTTPClient`),
PlatformIO, Unity native tests.

**Spec:** `docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md`

## Global Constraints

- Guest-page server port: `WWW_PORT = 8788` (`harness/www_server.py`); the route is `GET /o2proc`.
- O2 process name format: exactly 28 chars, `@<8 hex pub ip>:<8 hex internal ip>:<4 hex tcp port>:<4 hex udp port>`.
- Real-Arco test vector: `@00000000:ac17f983:afb9:9f48` gives internal IP `172.23.249.131`, tcp `44985`, udp `40776`.
- `/o2proc` responses: 200 = name + `\n`; 503 = reason (`arco not ready`, `lookup pending`, or a lookup failure reason); 404 `unknown ensemble` when `?ensemble=` mismatches; all `text/plain; charset=utf-8`.
- No zeroconf/o2litepy/netifaces import at module level in `control/` or `harness/`. Import lazily inside the function that browses.
- The firmware uses `O2_FALLBACK_HOST` for BOTH TCP and UDP, never the internal IP inside the name.
- Firmware defaults: `O2_FALLBACK_HOST ""` (disabled), `O2_FALLBACK_HTTP_PORT 8788`, `O2_FALLBACK_AFTER_MS 10000`, `O2_FALLBACK_RETRY_MS 5000`. IPv4 literals only.
- Nothing under `mm-devshroom/lib/o2/` (vendored) changes.
- Python tests run with `.venv/bin/python -m pytest` (never bare `python3`). In a worktree, first run `ln -s $HOME/projects/mm-terrarium/.venv .venv`.
- Part B starts only after mm-devshroom PR #5 (`claude/join-retry`) is merged to `main`. It builds on `lib/join_state` and the `[env:native]` block.

## Review Focus

1. **`--room` boot:** the Room is loaded inside `build()` before any observer exists. The watcher must be seeded from the current state, or `/o2proc` is 503 forever on the CLI path. Pinned in Task A2 (`test_seed_when_already_ready_starts_lookup`) and Task A4 (wiring test).
2. **Lookup finishing after the Room unloaded:** a stale name would send devices to a dead Arco. Pinned in Task A2 (`test_result_after_unload_is_discarded`).
3. **Another box's Arco on the same LAN, same ensemble:** it must never be served. Pinned in Task A1 (`test_select_ignores_remote_arco`).
4. **TCP connected but o2lite handshake not yet complete:** the retry timer must not call `o2l_network_connect` again and leak a socket. Pinned in Task B2 by the `tcp_sock == INVALID_SOCKET` guard, reviewed in code, since the wiring is hardware-only.
5. **Something else answering on 8788** (an HTML error page, a truncated body, CRLF): the parser must reject it and never connect. Pinned in Task B1 (`test_parse_rejects_html`, `test_parse_accepts_trailing_crlf`) and Task A1 (`test_parse_rejects_malformed`).

---

## Part A — mm-terrarium

### Task A1: O2 process-name parsing and local-Arco selection

**Files:**
- Create: `harness/o2proc_lookup.py`
- Test: `tests/test_o2proc_lookup.py`

**Interfaces:**
- Produces:
  - `ProcName(internal_ip: str, tcp: int, udp: int)` (NamedTuple).
  - `parse_proc_name(name: str) -> ProcName | None`.
  - `O2Record(instance: str, port: int, txt_name: str | None)` (NamedTuple).
  - `select_local(records: list[O2Record], ensemble: str, local_ips: set[str]) -> tuple[str | None, str]`.
    Returns `(name, "")` on exactly one match, else `(None, reason)`.

- [ ] **Step 1: Write the failing tests**

```python
"""harness/o2proc_lookup.py: Arco's O2 process name for GET /o2proc
(spec docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md)."""
from harness.o2proc_lookup import O2Record, ProcName, parse_proc_name, select_local

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_o2proc_lookup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.o2proc_lookup'`

- [ ] **Step 3: Write the minimal implementation**

```python
"""Find this box's Arco on zeroconf and report its O2 process name, which
carries Arco's ephemeral TCP/UDP ports (the Arco server build defines
O2_NO_O2DISCOVERY, so the ports change on every start). Served at
GET /o2proc by harness/www_server.py for firmware that cannot use mDNS.
Spec: docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md.

Pure parsing and selection here; the zeroconf browse is added in Task A2
and imported lazily (no zeroconf at module level: boundary rules)."""
from __future__ import annotations

from typing import NamedTuple

_HEX = set("0123456789abcdefABCDEF")


class ProcName(NamedTuple):
    internal_ip: str
    tcp: int
    udp: int


class O2Record(NamedTuple):
    instance: str
    port: int
    txt_name: str | None


def _hex(s: str) -> bool:
    return bool(s) and all(c in _HEX for c in s)


def parse_proc_name(name) -> ProcName | None:
    """`@pppppppp:iiiiiiii:tttt:uuuu` -> ProcName, else None."""
    if not isinstance(name, str) or len(name) != 28 or name[0] != "@":
        return None
    if name[9] != ":" or name[18] != ":" or name[23] != ":":
        return None
    pub, internal, tcp, udp = name[1:9], name[10:18], name[19:23], name[24:28]
    if not all(_hex(p) for p in (pub, internal, tcp, udp)):
        return None
    ip = ".".join(str(int(internal[i:i + 2], 16)) for i in range(0, 8, 2))
    return ProcName(ip, int(tcp, 16), int(udp, 16))


def select_local(records, ensemble: str, local_ips) -> tuple[str | None, str]:
    """The one record that is `ensemble`, well-formed, self-consistent and
    whose internal IP is this host's. Anything else is (None, reason)."""
    matches = []
    for rec in records:
        if rec.instance != ensemble:
            continue
        parsed = parse_proc_name(rec.txt_name)
        if parsed is None or parsed.tcp != rec.port:
            continue
        if parsed.internal_ip not in local_ips:
            continue
        matches.append(rec.txt_name)
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, f"no {ensemble} advertised by this host"
    return None, f"{len(matches)} local {ensemble} processes advertised"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_o2proc_lookup.py -v`
Expected: 7 passed. `test_select_ignores_other_ensemble` and `test_select_ignores_remote_arco` match `"no arco"` in `"no arco advertised by this host"`.

- [ ] **Step 5: Commit**

```bash
git add harness/o2proc_lookup.py tests/test_o2proc_lookup.py
git commit -m "feat(o2proc): parse O2 process names and pick this host's Arco"
```

### Task A2: Browse, holder and state watcher

**Files:**
- Modify: `harness/o2proc_lookup.py` (append)
- Test: `tests/test_o2proc_lookup.py` (append)

**Interfaces:**
- Consumes: `select_local`, `O2Record` (Task A1); `control.terrarium.TerrariumState`.
- Produces:
  - `browse(timeout: float = 3.0) -> list[O2Record]`, live only; lazy zeroconf.
  - `local_ipv4s() -> set[str]`, lazy netifaces.
  - `find_local_arco(ensemble: str, *, browse=browse, local_ips=local_ipv4s, attempts: int = 3) -> tuple[str | None, str]`.
  - `O2ProcHolder(ensemble: str)` with `.ensemble`, `get() -> tuple[str | None, str]`, `set_name(name: str)`, `set_unavailable(reason: str)`.
  - `O2ProcWatcher(holder, *, lookup, spawn)` with `on_terrarium_state_change(old, new)` and `seed(state)`.
    `lookup` is a zero-arg callable returning `(name, reason)`; `spawn(fn)` runs `fn` (a daemon thread in production).

- [ ] **Step 1: Write the failing tests**

```python
from control.terrarium import TerrariumState
from harness.o2proc_lookup import O2ProcHolder, O2ProcWatcher, find_local_arco


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


def test_lookup_failure_is_the_503_reason():
    holder, spawn = O2ProcHolder("arco"), _Deferred()
    w = O2ProcWatcher(holder, lookup=lambda: (None, "no arco here"),
                      spawn=spawn)
    w.on_terrarium_state_change(None, TerrariumState.ROOM_READY)
    spawn.run()
    assert holder.get() == (None, "no arco here")


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_o2proc_lookup.py -v`
Expected: FAIL with `ImportError: cannot import name 'O2ProcHolder'`

- [ ] **Step 3: Implement (append to `harness/o2proc_lookup.py`)**

```python
import threading

from control.terrarium import TerrariumState

NOT_READY = "arco not ready"
PENDING = "lookup pending"
_SERVICE = "_o2proc._tcp.local."


def browse(timeout: float = 3.0) -> list[O2Record]:
    """One zeroconf browse of _o2proc._tcp. Live network only; tests inject
    a fake. zeroconf is imported here, never at module level."""
    import time
    from zeroconf import ServiceBrowser, Zeroconf

    records: list[O2Record] = []
    zc = Zeroconf()

    class _Listener:
        def add_service(self, z, type_, name):
            info = z.get_service_info(type_, name, 2000)
            if info is None:
                return
            txt = info.properties.get(b"name")
            records.append(O2Record(name.split("." + type_)[0], info.port,
                                    txt.decode("utf-8", "replace") if txt else None))

        def update_service(self, *args):
            pass

        def remove_service(self, *args):
            pass

    try:
        ServiceBrowser(zc, _SERVICE, _Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    return records


def local_ipv4s() -> set[str]:
    """Every IPv4 address on this host's interfaces (netifaces, lazy)."""
    import netifaces

    ips = {"127.0.0.1"}
    for iface in netifaces.interfaces():
        for addr in netifaces.ifaddresses(iface).get(netifaces.AF_INET, []):
            ips.add(addr["addr"])
    return ips


def find_local_arco(ensemble: str, *, browse=browse, local_ips=local_ipv4s,
                    attempts: int = 3) -> tuple[str | None, str]:
    reason = ""
    for _ in range(attempts):
        name, reason = select_local(browse(), ensemble, local_ips())
        if name is not None:
            return name, ""
    return None, reason


class O2ProcHolder:
    """What GET /o2proc serves; written by O2ProcWatcher's thread, read by
    the www server's handler threads."""

    def __init__(self, ensemble: str) -> None:
        self.ensemble = ensemble
        self._lock = threading.Lock()
        self._name: str | None = None
        self._reason = NOT_READY

    def get(self) -> tuple[str | None, str]:
        with self._lock:
            return self._name, self._reason

    def set_name(self, name: str) -> None:
        with self._lock:
            self._name, self._reason = name, ""

    def set_unavailable(self, reason: str) -> None:
        with self._lock:
            self._name, self._reason = None, reason


def _spawn_daemon(fn) -> None:
    threading.Thread(target=fn, daemon=True, name="o2proc-lookup").start()


class O2ProcWatcher:
    """Terrarium observer: one lookup per ROOM_READY (Arco has passed
    wait_ready, so it is advertising), cleared on any other state (the next
    Arco has new ports). A generation counter drops a lookup that finishes
    after its Room is gone."""

    def __init__(self, holder: O2ProcHolder, *, lookup, spawn=_spawn_daemon):
        self._holder = holder
        self._lookup = lookup
        self._spawn = spawn
        self._lock = threading.Lock()
        self._gen = 0

    def seed(self, state) -> None:
        """For a Room loaded before this observer was registered (--room)."""
        if state is TerrariumState.ROOM_READY:
            self._start()

    def on_terrarium_state_change(self, old_state, new_state) -> None:
        if new_state is TerrariumState.ROOM_READY:
            self._start()
            return
        with self._lock:
            self._gen += 1
            self._holder.set_unavailable(NOT_READY)

    def _start(self) -> None:
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._holder.set_unavailable(PENDING)

        # NOTE: the shipped run() also catches lookup exceptions and retries
        # with backoff (see spec Error handling); this snippet is the first cut.
        def run():
            name, reason = self._lookup()
            with self._lock:
                if gen != self._gen:
                    return
                if name is not None:
                    self._holder.set_name(name)
                else:
                    self._holder.set_unavailable(reason)

        self._spawn(run)
```

Place the `import threading` and `from control.terrarium import TerrariumState` lines with the module's other imports at the top of the file. `control.terrarium` is pure stdlib, so the import keeps the offline suite offline.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_o2proc_lookup.py -v`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add harness/o2proc_lookup.py tests/test_o2proc_lookup.py
git commit -m "feat(o2proc): per-Room lookup watcher and thread-safe holder"
```

### Task A3: `GET /o2proc` on the guest-page server

**Files:**
- Modify: `harness/www_server.py` (constants near `START_PATH`; `_QuietHandler.__init__` and `do_GET`; `WwwServer.__init__` and `start`)
- Test: `tests/test_www_server.py` (append)

**Interfaces:**
- Consumes: `O2ProcHolder` (Task A2), duck-typed: `.ensemble` and `.get()`.
- Produces: `O2PROC_PATH = "/o2proc"`; a `WwwServer.o2proc` attribute (default `None`), read in `start()`. Callers set it before `start()`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_www_server.py`)**

```python
from harness.o2proc_lookup import O2ProcHolder
from harness.www_server import O2PROC_PATH

REAL = "@00000000:ac17f983:afb9:9f48"


@pytest.fixture
def www_o2proc(tmp_path):
    holder = O2ProcHolder("arco")
    server = WwwServer(str(tmp_path), host="127.0.0.1", port=0)
    server.o2proc = holder
    server.start()
    try:
        yield server, holder
    finally:
        server.stop()


def _get(server, query=""):
    url = f"http://127.0.0.1:{server.port}{O2PROC_PATH}{query}"
    try:
        resp = urlopen(url)
        return resp.status, resp.read().decode(), resp.headers["Content-Type"]
    except HTTPError as err:
        return err.code, err.read().decode(), err.headers["Content-Type"]


def test_o2proc_serves_the_cached_name(www_o2proc):
    server, holder = www_o2proc
    holder.set_name(REAL)
    status, body, ctype = _get(server, "?ensemble=arco")
    assert (status, body) == (200, REAL + "\n")
    assert ctype.startswith("text/plain")


def test_o2proc_is_503_with_the_reason_until_ready(www_o2proc):
    server, _ = www_o2proc
    assert _get(server)[:2] == (503, "arco not ready\n")


def test_o2proc_is_404_for_another_ensemble(www_o2proc):
    server, holder = www_o2proc
    holder.set_name(REAL)
    assert _get(server, "?ensemble=other")[:2] == (404, "unknown ensemble\n")


def test_o2proc_is_404_when_not_wired(www):
    status, _, _ = _get(www)
    assert status == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_www_server.py -v -k o2proc`
Expected: FAIL with `ImportError: cannot import name 'O2PROC_PATH'`

- [ ] **Step 3: Implement**

In `harness/www_server.py`, beside `PREPARE_PATH`:

```python
# Arco's current O2 process name, for firmware whose network blocks mDNS
# (spec 2026-09-29-o2proc-port-lookup-design). Read-only; no key.
O2PROC_PATH = "/o2proc"
```

In `_QuietHandler.__init__`, add a keyword and store it:

```python
    def __init__(self, *args, start_requests=None, prepare_requests=None,
                 o2proc=None, **kwargs):
        self._start_requests = start_requests
        self._prepare_requests = prepare_requests
        self._o2proc = o2proc
        super().__init__(*args, **kwargs)
```

In `do_GET`, route before the `/start` check:

```python
        if parsed.path == O2PROC_PATH:
            return self._do_o2proc(parsed)
```

Add the method to `_QuietHandler`:

```python
    def _do_o2proc(self, parsed):
        if self._o2proc is None:
            self.send_error(404, "o2proc is not wired on this server")
            return
        ensemble = parse_qs(parsed.query).get("ensemble", [None])[0]
        if ensemble is not None and ensemble != self._o2proc.ensemble:
            self._plain(404, "unknown ensemble")
            return
        name, reason = self._o2proc.get()
        if name is None:
            self._plain(503, reason)
            return
        self._plain(200, name)
```

In `WwwServer.__init__` add `self.o2proc = None`. In `WwwServer.start`, pass it to the partial:

```python
        handler = functools.partial(_QuietHandler, directory=self._root,
                                    start_requests=self.start_requests,
                                    prepare_requests=self.prepare_requests,
                                    o2proc=self.o2proc)
```

Update the `_QuietHandler` docstring's "the one dynamic route" wording to list `/start`, `/prepare` and `/o2proc`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_www_server.py -v`
Expected: all pass (the existing tests plus 4 new).

- [ ] **Step 5: Commit**

```bash
git add harness/www_server.py tests/test_www_server.py
git commit -m "feat(www): GET /o2proc serves Arco's current O2 process name"
```

### Task A4: Wire into terrarium_boot, document, verify live

**Files:**
- Modify: `harness/terrarium_boot.py`: `_start_www_server` (~line 219) and its call site (~line 1960, `www = _start_www_server(args, teardown)`)
- Modify: `docs/MM_TERRARIUM.md`: the **Ports** paragraph under *Running it*
- Test: `tests/test_terrarium_boot.py` (append; find the existing `_start_www_server` tests with `grep -n "_start_www_server" tests/test_terrarium_boot.py` and follow their fake-`server_cls` style)

**Interfaces:**
- Consumes: `O2ProcHolder`, `O2ProcWatcher`, `find_local_arco` (Task A2); `WwwServer.o2proc` (Task A3).
- Produces: `_start_www_server(args, teardown, *, server_cls=WwwServer, ip=lan_ip, o2proc=None)`, which sets `server.o2proc = o2proc` before `server.start()`.

- [ ] **Step 1: Write the failing test**

```python
def test_start_www_server_sets_o2proc_before_start(tmp_path):
    seen = {}

    class FakeServer:
        def __init__(self, root, host, port):
            self.o2proc = None

        def start(self):
            seen["o2proc_at_start"] = self.o2proc

        def stop(self):
            pass

        def url(self, host=None):
            return "http://x/"

    class Args:
        www_port = 8788

    class Teardown:
        def push(self, name, fn):
            pass

    holder = object()
    terrarium_boot._start_www_server(Args(), Teardown(), server_cls=FakeServer,
                                     ip=lambda: "127.0.0.1", o2proc=holder)
    assert seen["o2proc_at_start"] is holder
```

Adjust the module import name to match how `tests/test_terrarium_boot.py` already imports `harness.terrarium_boot`.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -v -k o2proc`
Expected: FAIL with `TypeError: _start_www_server() got an unexpected keyword argument 'o2proc'`

- [ ] **Step 3: Implement**

In `_start_www_server`, add the `o2proc=None` keyword and set the attribute right after construction:

```python
def _start_www_server(args, teardown, *, server_cls=WwwServer, ip=lan_ip,
                      o2proc=None):
    ...
    server = server_cls(os.path.join(REPO_ROOT, "www"), host="0.0.0.0",
                        port=args.www_port)
    server.o2proc = o2proc
    try:
        server.start()
```

At the call site (~line 1960), build the holder and watcher first. Register the watcher on the Terrarium and seed it (the `--room` path loaded the Room before any observer existed). Then pass the holder:

```python
        from harness.o2proc_lookup import (O2ProcHolder, O2ProcWatcher,
                                           find_local_arco)
        o2proc = O2ProcHolder(config.o2_ensemble)
        o2proc_watcher = O2ProcWatcher(
            o2proc, lookup=lambda: find_local_arco(config.o2_ensemble))
        terrarium.add_observer(o2proc_watcher)
        o2proc_watcher.seed(terrarium.state)
        www = _start_www_server(args, teardown, o2proc=o2proc)
```

Check the names `config.o2_ensemble` and `terrarium.state` at that point in `main()` (`grep -n "o2_ensemble\|terrarium.state" harness/terrarium_boot.py`). Use whatever `main()` actually binds. If the Terrarium's state attribute has a different name, use it; `control/terrarium.py` `TerrariumState` is the type.

In `docs/MM_TERRARIUM.md`, append to the **Ports** paragraph:

```markdown
The guest-page server also answers `GET /o2proc` with Arco's current O2
process name (`@pub:internal:tcp:udp`, hex), 503 until a Room is ready:
Arco's O2 ports are ephemeral (the server build defines
`O2_NO_O2DISCOVERY`), so firmware on a network that blocks mDNS learns them
here (mm-devshroom `O2_FALLBACK_HOST`; spec
`docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md`).
```

If mm-terrarium PR #163 (*Linux / WSL host setup*) has merged, also add one
sentence to its step 9 there: networks that block mDNS can use the
firmware's `O2_FALLBACK_HOST` with the guest-page server on 8788.

- [ ] **Step 4: Run the full suites**

Run: `.venv/bin/python -m pytest tests -q && node --test tests/js/*.test.js`
Expected: all pass (baseline plus the new tests); 26 JS pass.

- [ ] **Step 5: Live verification**

```bash
set -m
./terrarium.sh --room TEST --seconds 40 > /tmp/o2proc-live.log 2>&1 &
sleep 25
curl -s -w ' %{http_code}\n' localhost:8788/o2proc
curl -s -w ' %{http_code}\n' 'localhost:8788/o2proc?ensemble=nope'
wait; ./terrarium.sh --clean
```

Expected: the first curl prints a 28-char `@...` name and `200`, and its TCP field matches the port a zeroconf browse reports (`.venv/bin/python -c "from harness.o2proc_lookup import browse; print(browse())"` while the stack is up). The second prints `unknown ensemble 404`. `--clean` reports nothing left running.

- [ ] **Step 6: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py docs/MM_TERRARIUM.md
git commit -m "feat(boot): serve /o2proc from a per-Room O2 lookup"
```

---

## Part B — mm-devshroom (after PR #5 is merged)

Work on a branch from an up-to-date `main` in
`$HOME/projects/mm-devshroom`. PlatformIO: use an existing `pio`, or
`python3 -m venv <tmp>/pio && <tmp>/pio/bin/pip install platformio`.

### Task B1: `FallbackState` timing and O2 name parser (native TDD)

**Files:**
- Create: `lib/fallback_state/fallback_state.h`
- Test: `test/test_fallback_state/test_fallback_state.cpp`

**Interfaces:**
- Produces:
  - `struct O2ProcPorts { int tcp; int udp; };`
  - `bool parse_o2_proc_name(const char *body, O2ProcPorts *out);` trims trailing `\r`, `\n` and spaces, then requires exactly 28 chars.
  - `struct FallbackState { FallbackState(uint32_t after_ms, uint32_t retry_ms); void on_boot(uint32_t now); void on_link_down(uint32_t now); void on_link_up(); bool should_attempt(uint32_t now); };`

- [ ] **Step 1: Write the failing tests**

```cpp
#include <unity.h>

#include "fallback_state.h"

void setUp() {}
void tearDown() {}

// Measured from a live Arco on 2026-09-29 (mm-terrarium spec vector).
static const char *REAL = "@00000000:ac17f983:afb9:9f48";

void test_parse_real_arco_name() {
  O2ProcPorts p;
  TEST_ASSERT_TRUE(parse_o2_proc_name(REAL, &p));
  TEST_ASSERT_EQUAL_INT(44985, p.tcp);
  TEST_ASSERT_EQUAL_INT(40776, p.udp);
}

void test_parse_accepts_trailing_crlf() {
  O2ProcPorts p;
  TEST_ASSERT_TRUE(parse_o2_proc_name("@00000000:ac17f983:afb9:9f48\r\n", &p));
  TEST_ASSERT_EQUAL_INT(44985, p.tcp);
}

void test_parse_rejects_html() {
  O2ProcPorts p;
  TEST_ASSERT_FALSE(parse_o2_proc_name("<html><body>404</body></html>", &p));
}

void test_parse_rejects_truncated_and_null() {
  O2ProcPorts p;
  TEST_ASSERT_FALSE(parse_o2_proc_name("@00000000:ac17f983:afb9:9f4", &p));
  TEST_ASSERT_FALSE(parse_o2_proc_name("", &p));
  TEST_ASSERT_FALSE(parse_o2_proc_name(nullptr, &p));
  TEST_ASSERT_FALSE(parse_o2_proc_name("@0000000g:ac17f983:afb9:9f48", &p));
}

void test_no_attempt_before_after_ms() {
  FallbackState f(10000, 5000);
  f.on_boot(0);
  TEST_ASSERT_FALSE(f.should_attempt(9999));
  TEST_ASSERT_TRUE(f.should_attempt(10000));
}

void test_retries_every_retry_ms() {
  FallbackState f(10000, 5000);
  f.on_boot(0);
  TEST_ASSERT_TRUE(f.should_attempt(10000));
  TEST_ASSERT_FALSE(f.should_attempt(14999));
  TEST_ASSERT_TRUE(f.should_attempt(15000));
}

void test_no_attempt_while_linked() {
  FallbackState f(10000, 5000);
  f.on_boot(0);
  f.on_link_up();
  TEST_ASSERT_FALSE(f.should_attempt(60000));
}

void test_link_loss_rearms_after_ms() {
  FallbackState f(10000, 5000);
  f.on_boot(0);
  f.on_link_up();
  f.on_link_down(100000);
  TEST_ASSERT_FALSE(f.should_attempt(109999));
  TEST_ASSERT_TRUE(f.should_attempt(110000));
}

void test_survives_millis_rollover() {
  FallbackState f(10000, 5000);
  f.on_boot(0xFFFFF000u);
  TEST_ASSERT_FALSE(f.should_attempt(0xFFFFFFF0u));
  TEST_ASSERT_TRUE(f.should_attempt(0x00001800u));  // 10 s after, wrapped
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_parse_real_arco_name);
  RUN_TEST(test_parse_accepts_trailing_crlf);
  RUN_TEST(test_parse_rejects_html);
  RUN_TEST(test_parse_rejects_truncated_and_null);
  RUN_TEST(test_no_attempt_before_after_ms);
  RUN_TEST(test_retries_every_retry_ms);
  RUN_TEST(test_no_attempt_while_linked);
  RUN_TEST(test_link_loss_rearms_after_ms);
  RUN_TEST(test_survives_millis_rollover);
  return UNITY_END();
}
```

Check how `test/test_join_state/test_join_state.cpp` ends (its `main` / `setup` structure under `[env:native]`) and match it exactly.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pio test -e native -f test_fallback_state`
Expected: build ERROR, `fallback_state.h: No such file or directory`

- [ ] **Step 3: Implement `lib/fallback_state/fallback_state.h`**

```cpp
#pragma once
// Fixed-host fallback for networks that block mDNS (mm-terrarium spec
// docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md). Pure:
// no Arduino, no o2lite, so `pio test -e native` covers it.
//
// Arco's O2 ports are ephemeral, so the device asks the Terrarium's
// guest-page server (GET /o2proc) for Arco's O2 process name,
// "@pppppppp:iiiiiiii:tttt:uuuu", and takes only the ports from it: the
// connection goes to O2_FALLBACK_HOST, which just answered, never to the
// internal IP inside the name (on a multi-homed host it may be unreachable).

#include <stdint.h>
#include <string.h>

struct O2ProcPorts {
  int tcp;
  int udp;
};

inline int fallback_hex_nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

inline bool fallback_hex_field(const char *s, int n, int *value) {
  int v = 0;
  for (int i = 0; i < n; i++) {
    int d = fallback_hex_nibble(s[i]);
    if (d < 0) return false;
    v = (v << 4) | d;
  }
  if (value) *value = v;
  return true;
}

inline bool parse_o2_proc_name(const char *body, O2ProcPorts *out) {
  if (!body) return false;
  size_t len = strlen(body);
  while (len > 0 && (body[len - 1] == '\n' || body[len - 1] == '\r' ||
                     body[len - 1] == ' ')) {
    len--;
  }
  if (len != 28 || body[0] != '@') return false;
  if (body[9] != ':' || body[18] != ':' || body[23] != ':') return false;
  int tcp, udp;
  if (!fallback_hex_field(body + 1, 8, nullptr) ||
      !fallback_hex_field(body + 10, 8, nullptr) ||
      !fallback_hex_field(body + 19, 4, &tcp) ||
      !fallback_hex_field(body + 24, 4, &udp)) {
    return false;
  }
  out->tcp = tcp;
  out->udp = udp;
  return true;
}

struct FallbackState {
  FallbackState(uint32_t after_ms, uint32_t retry_ms)
      : after_ms_(after_ms), retry_ms_(retry_ms) {}

  void on_boot(uint32_t now) { on_link_down(now); }

  // mDNS gets after_ms first, again after every link loss.
  void on_link_down(uint32_t now) {
    linked_ = false;
    next_ = now + after_ms_;
  }

  void on_link_up() { linked_ = true; }

  // True means "try /o2proc now"; the next try is retry_ms later. Signed
  // difference, so the 49-day millis() rollover cannot stall it.
  bool should_attempt(uint32_t now) {
    if (linked_) return false;
    if ((int32_t) (now - next_) < 0) return false;
    next_ = now + retry_ms_;
    return true;
  }

 private:
  uint32_t after_ms_;
  uint32_t retry_ms_;
  bool linked_ = false;
  uint32_t next_ = 0;
};
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pio test -e native`
Expected: all native suites pass (join_state 12 plus fallback_state 9).

- [ ] **Step 5: Commit**

```bash
git add lib/fallback_state test/test_fallback_state
git commit -m "feat(fallback): O2 name parser and fixed-host retry timing"
```

### Task B2: Config, wiring and docs

**Files:**
- Create: `include/fallback_config.h`
- Modify: `include/config.example.h` (append the documented, commented-out settings)
- Modify: `src/main.cpp` (includes, a `FallbackState` instance, `try_fallback()`, `setup()`/`loop()` hooks)
- Modify: `README.md` (Configuration table; Troubleshooting "Networks that block mDNS")

**Interfaces:**
- Consumes: `FallbackState`, `parse_o2_proc_name`, `O2ProcPorts` (Task B1); from `lib/o2/o2lite.h`: `o2l_address_init`, `o2l_network_connect`, `udp_server_sa`, `tcp_sock`, `INVALID_SOCKET`, `o2l_bridge_id`.
- Produces: the macros `O2_FALLBACK_HOST`, `O2_FALLBACK_HTTP_PORT`, `O2_FALLBACK_AFTER_MS`, `O2_FALLBACK_RETRY_MS`.

- [ ] **Step 1: Create `include/fallback_config.h`**

```cpp
#pragma once
// Defaults for the optional fixed-host fallback. Include AFTER config.h so a
// developer's #defines there win; an existing config.h without them still
// compiles and behaves exactly as before (fallback disabled).
#ifndef O2_FALLBACK_HOST
#define O2_FALLBACK_HOST ""
#endif
#ifndef O2_FALLBACK_HTTP_PORT
#define O2_FALLBACK_HTTP_PORT 8788
#endif
#ifndef O2_FALLBACK_AFTER_MS
#define O2_FALLBACK_AFTER_MS 10000
#endif
#ifndef O2_FALLBACK_RETRY_MS
#define O2_FALLBACK_RETRY_MS 5000
#endif
```

- [ ] **Step 2: Append to `include/config.example.h`**

```cpp
// --- fixed-host fallback (optional, testing) ---
// For networks that block mDNS (campus/enterprise Wi-Fi). Set to the
// Terrarium machine's LAN IPv4 (a literal, no hostnames). After mDNS has
// had O2_FALLBACK_AFTER_MS, the device asks
// http://<host>:O2_FALLBACK_HTTP_PORT/o2proc for Arco's current ports and
// connects there. Needs the Terrarium's guest-page server on (its default;
// --www-port 0 turns it off). Leave commented out for mDNS only.
// #define O2_FALLBACK_HOST "192.168.1.20"
// #define O2_FALLBACK_HTTP_PORT 8788
// #define O2_FALLBACK_AFTER_MS 10000
// #define O2_FALLBACK_RETRY_MS 5000
```

- [ ] **Step 3: Wire into `src/main.cpp`**

Add the includes after `#include "config.h"`:

```cpp
#include <HTTPClient.h>

#include "fallback_config.h"
#include "fallback_state.h"
```

Add below the `JoinState` declaration:

```cpp
// Optional fixed-host fallback, see lib/fallback_state and config.example.h.
static FallbackState fallback(O2_FALLBACK_AFTER_MS, O2_FALLBACK_RETRY_MS);
static int last_fallback_code = 0;  // log only when the outcome changes

static bool fallback_enabled() { return O2_FALLBACK_HOST[0] != '\0'; }

static void try_fallback() {
  // Never open a second socket while o2lite still has one (connected but
  // handshake pending): o2l_network_connect would leak the first.
  if (tcp_sock != INVALID_SOCKET) return;
  char url[128];
  snprintf(url, sizeof url, "http://%s:%d/o2proc?ensemble=%s",
           O2_FALLBACK_HOST, O2_FALLBACK_HTTP_PORT, O2_ENSEMBLE_NAME);
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(1500);  // loop() must keep polling o2lite
  if (!http.begin(url)) return;
  int code = http.GET();
  String body = (code == 200) ? http.getString() : String();
  http.end();
  O2ProcPorts ports;
  if (code != 200 || !parse_o2_proc_name(body.c_str(), &ports)) {
    if (code != last_fallback_code) {
      last_fallback_code = code;
      Serial.printf("fallback: %s -> %d%s\n", url, code,
                    code == 200 ? " (unparseable body)" : "");
    }
    return;
  }
  last_fallback_code = code;
  Serial.printf("fallback: connecting %s tcp %d udp %d\n", O2_FALLBACK_HOST,
                ports.tcp, ports.udp);
  o2l_address_init(&udp_server_sa, O2_FALLBACK_HOST, ports.udp, false);
  o2l_network_connect(O2_FALLBACK_HOST, ports.tcp);
}
```

At the end of `setup()`: `fallback.on_boot(millis());`

In `loop()`:
- in the `connected && !link_up` branch, add `fallback.on_link_up();`
- in the `!connected && link_up` branch, add `fallback.on_link_down(millis());`
- replace `if (!link_up) { return; }` with:

```cpp
  if (!link_up) {
    if (fallback_enabled() && fallback.should_attempt(millis())) {
      try_fallback();
    }
    return;
  }
```

- [ ] **Step 4: Build every env and run native tests**

```bash
cp include/config.example.h include/config.h
pio test -e native
pio run -e esp32-p4-evboard -e accel-test -e speaker-test
# fallback-enabled build compiles too:
echo '#define O2_FALLBACK_HOST "192.168.1.20"' >> include/config.h
pio run -e esp32-p4-evboard
rm include/config.h
```

Expected: native tests all pass; all four builds SUCCESS; `git status` shows no `include/config.h`.

- [ ] **Step 5: README**

Add rows to the Configuration table for `O2_FALLBACK_HOST` / `O2_FALLBACK_HTTP_PORT` / `O2_FALLBACK_AFTER_MS` / `O2_FALLBACK_RETRY_MS`, matching the text of `config.example.h`. Add a Troubleshooting entry, **Network blocks mDNS**: set `O2_FALLBACK_HOST` to the Terrarium's LAN IP. Serial then shows `fallback: connecting ...`; a `fallback: ... -> 503` line means no Room is loaded yet. Mention the mm-terrarium `/o2proc` route.

- [ ] **Step 6: Commit**

```bash
git add include/fallback_config.h include/config.example.h src/main.cpp README.md
git commit -m "feat(fallback): connect via the Terrarium's /o2proc when mDNS finds nothing"
```

- [ ] **Step 7: Hardware check (manual, a teammate with a board)**

With Part A running (`./terrarium.sh --room TEST`), `O2_FALLBACK_HOST` set, and mDNS blocked (a network that drops multicast, or a Windows firewall rule dropping inbound UDP 5353 for WSL):
- Serial shows `fallback: connecting <host> tcp N udp M`, then the o2lite connection.
- After loading a Bit, it shows `ROLE granted`.
- Restarting the Terrarium shows a link loss, then a fresh fallback connect with the new ports.
