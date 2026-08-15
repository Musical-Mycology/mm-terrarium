# Room Simulator Service Collision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop an orphaned Room simulator from stealing the `sim-room` O2 service from the next run, and make a refused service announcement fail loudly instead of silently rendering nothing.

**Architecture:** Two independent halves. **Prevention:** the simulator subprocess gains a parent-death guard (`--exit-with-parent PID`) so it cannot outlive its Terrarium even under `SIGKILL`, and `control/boot.py` plus `harness/terrarium_boot.py` extend their existing teardown guarantees to cover the simulator they spawn. **Detection:** a self-addressed round trip through the O2 hub proves whether a service really routes back to this connection, used as a one-shot startup assertion by both the device (`harness/o2_shroom.py`) and Control (`devicelink/o2_transport.py`).

**Tech Stack:** Python 3, pytest. No new dependencies. o2litepy and pyarco stay `PYTHONPATH`-only dev/test dependencies and are never imported by anything this plan touches at module level.

**Spec:** `docs/superpowers/specs/2026-08-14-room-simulator-service-collision-design.md`

## Global Constraints

- **The whole test suite must keep running fully offline:** no O2 network, no Arco server, no pyarco, no o2litepy importable. That property is load-bearing and pinned by existing tests. Baseline before this plan: **621 passed, 1 skipped**.
- **No module under `control/` may import `o2litepy` or `pyarco`.** `devicelink/o2_transport.py` must not import o2litepy either, at module level or anywhere: the caller injects an already-connected object.
- **Boundary rule 5:** a test double must never be more permissive than the library it stands for. `FakeO2Lite` must model what the hub *refuses*, not only what it accepts.
- **Boundary rule 4** says `game` and `actl` are inbound-only and Control never messages itself. The ownership check in Task 2 is a deliberate, documented exception: one message, before the tick loop starts, as a startup assertion. Annotate it as such at the call site.
- **Run tests with:** `PYTHONPATH=/Users/chris/projects/arco` is *not* needed; the suite is offline. Use the project virtualenv, there is no bare `python`:
  ```
  /Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q
  ```
- **Style:** match the surrounding code. Docstrings explain *why*, cite `file.py:line` evidence, and use `--` rather than an em dash.

---

### Task 1: Parent-death guard for the Room simulator

**Files:**
- Modify: `harness/o2_shroom.py` (module imports, new `parent_is_gone`, new `--exit-with-parent` flag, both blocking loops)
- Modify: `harness/terrarium_boot.py:53-71` (`_O2SimulatorFactory` passes the flag; module imports)
- Test: `tests/test_o2_shroom.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `harness.o2_shroom.parent_is_gone(expected_ppid: int | None, getppid=os.getppid) -> bool`. No later task depends on it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_o2_shroom.py`:

```python
# --- Parent-death guard. The Room simulator is spawned by
# harness/terrarium_boot.py and, with --no-join, never exits on its own:
# main()'s loop waits for a /release that only a live Control sends. An
# orphan therefore runs forever, and o2litepy reconnects it to the NEXT
# Arco (o2lite.py:912 connects whenever _tcp_socket is None; _id_handler
# at :601 re-announces services on connect), where it claims the same dev
# name. O2 then refuses the new run's own simulator with "not from service
# provider" (o2/src/bridge.cpp:231-237). See docs/superpowers/specs/
# 2026-08-14-room-simulator-service-collision-design.md. ------------------

def test_parent_is_gone_is_false_while_the_parent_still_owns_us():
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(4242, getppid=lambda: 4242) is False


def test_parent_is_gone_is_true_once_we_have_been_reparented():
    """A dead parent's children are reparented to init/launchd (pid 1).

    This is also why the check compares against a RECORDED pid rather than
    watching getppid() for a change: if the parent died before this process
    read its argv, getppid() already reads 1 at startup and a change
    detector would wait forever. Comparison catches both orderings with the
    same expression, which is why there is only one case to test here."""
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(4242, getppid=lambda: 1) is True


def test_parent_is_gone_never_fires_without_an_expected_pid():
    """--exit-with-parent is opt-in. A hand-run device passes nothing and
    must never exit because of this guard."""
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(None, getppid=lambda: 1) is False
```

Append to `tests/test_terrarium_boot.py`:

```python
def test_o2_simulator_factory_ties_the_simulator_to_this_process():
    """The only guard that survives an external kill of the parent, which
    is how agent-driven runs are actually terminated and which no teardown
    path can catch. An orphaned Room simulator reconnects to the NEXT Arco
    and claims sim-room before that run's own simulator is even spawned."""
    import os

    from harness.terrarium_boot import _O2SimulatorFactory

    popen = FakePopen()
    factory = _O2SimulatorFactory("arco", popen=popen)

    assert factory() == "sim-room"
    command = popen.commands[0]
    assert "--exit-with-parent" in command
    assert command[command.index("--exit-with-parent") + 1] == str(os.getpid())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_terrarium_boot.py -q
```
Expected: FAIL with `ImportError: cannot import name 'parent_is_gone'` and, for the factory test, `assert '--exit-with-parent' in [...]`.

- [ ] **Step 3: Add `parent_is_gone` to `harness/o2_shroom.py`**

Add `import os` to the module-level imports (below `import math`), then add this function after `tilt_sweep`:

```python
def parent_is_gone(expected_ppid, getppid=os.getppid) -> bool:
    """True once this process's parent is no longer the one that spawned it.

    The Room simulator is spawned by harness/terrarium_boot.py and, with
    --no-join, never exits on its own: main()'s loop below waits for a
    /release that only a live Control sends. So a Terrarium that dies
    without running its shutdown leaves this process running forever, and
    o2litepy reconnects it to the NEXT Arco that starts (o2lite.py:912
    connects whenever _tcp_socket is None, and _id_handler at :601
    re-announces every service on connect). There it claims this same dev
    name, and O2 refuses the new run's own simulator with "not from service
    provider" (o2/src/bridge.cpp:231-237) -- silently, since /_o2/*/sv is
    fire-and-forget. See docs/superpowers/specs/
    2026-08-14-room-simulator-service-collision-design.md.

    Compares against the pid the parent stamped in rather than watching
    getppid() for a change: if the parent died before this process read its
    argv, getppid() is ALREADY 1 and a change detector would wait forever.
    Comparison against a recorded value is correct in either order.

    expected_ppid None means the caller did not ask for this guard -- the
    default for a hand-run device -- and it never fires.
    """
    return expected_ppid is not None and getppid() != expected_ppid
```

- [ ] **Step 4: Add the `--exit-with-parent` flag and wire both loops**

In `main()`'s argument parser, after the `--no-join` argument:

```python
    parser.add_argument("--exit-with-parent", type=int, default=None,
                        metavar="PID",
                        help="Exit as soon as this process's parent is no "
                             "longer PID. harness/terrarium_boot.py passes "
                             "its own pid so a Room simulator cannot outlive "
                             "the Terrarium that spawned it and steal its dev "
                             "name from the next run.")
```

Replace the clock-sync loop:

```python
    while o2lite.time_get() < 0:           # block until clock sync
        if parent_is_gone(args.exit_with_parent):
            print("parent is gone; exiting before clock sync")
            backend.close()
            return
        o2lite.poll()
        time.sleep(0.01)
```

And add the same guard as the first statement inside the main tick loop, so it is checked even while waiting for a role:

```python
        while not client.released:
            if parent_is_gone(args.exit_with_parent):
                print("parent is gone; exiting")
                break
            o2lite.poll()
```

- [ ] **Step 5: Make `_O2SimulatorFactory` pass the flag**

Add `import os` to `harness/terrarium_boot.py`'s module-level imports (above `import subprocess`), then change `_O2SimulatorFactory.__call__`:

```python
    def __call__(self) -> str:
        # --exit-with-parent is what stops this subprocess outliving the
        # Terrarium. An orphan keeps its browser canvas open, reconnects to
        # the next Arco (o2litepy reconnects on its own) and claims sim-room
        # there, so the NEXT run's simulator is refused by O2 and renders
        # nothing. Passing our own pid covers the case teardown cannot: an
        # external SIGKILL of this process.
        self.process = SimulatorProcess(
            [sys.executable, "-m", "harness.o2_shroom",
             "--dev", SIM_DEV, "--ensemble", self._ensemble, "--no-join",
             "--exit-with-parent", str(os.getpid())],
            popen=self._popen)
        self.process.start()
        return SIM_DEV
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_terrarium_boot.py -q
```
Expected: PASS, no regressions in either file.

- [ ] **Step 7: Commit**

```bash
git add harness/o2_shroom.py harness/terrarium_boot.py tests/test_o2_shroom.py tests/test_terrarium_boot.py
git commit -m "fix(terrarium): stop the Room simulator outliving its Terrarium

An orphaned harness/o2_shroom.py never exits (its loop waits for a
/release only a live Control sends) and o2litepy reconnects it to the next
Arco, where it re-claims sim-room. O2 then refuses that run's own
simulator, silently. --exit-with-parent ties the subprocess to the pid
that spawned it, which is the only guard that survives a SIGKILLed parent."
```

---

### Task 2: Service-ownership check in the o2lite transport

**Files:**
- Modify: `devicelink/o2_transport.py` (module imports, `FakeO2Lite`, new `verify_service_ownership`, `O2LiteTransport.start`)
- Test: `tests/test_o2_transport.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `devicelink.o2_transport.verify_service_ownership(o2lite, service: str, *, timeout: float = 2.0, clock=time.monotonic, sleep=time.sleep) -> bool`. Task 3 calls this.
  - `FakeO2Lite.refuse(service: str) -> None`.
  - `O2LiteTransport.start(self, o2lite, *, ownership_timeout: float = 2.0, clock=time.monotonic, sleep=time.sleep) -> None` (three new keyword-only parameters, all defaulted, so every existing caller is unchanged).

- [ ] **Step 1: Write the failing tests**

Add `import pytest` as the first line of `tests/test_o2_transport.py`, then append:

```python
def _fake_clock():
    """A clock that only advances when sleep() is called, so a timeout can
    be exhausted without spending real time. Same shape as the helper in
    tests/test_boot.py."""
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    return clock, sleep


def test_a_self_addressed_message_comes_back_when_the_service_is_ours():
    """Boundary rule 4: o2lite send() has NO local short circuit, so a
    message addressed to our own service leaves for the hub and returns
    only if the hub really routes that service to us. That is what makes
    ownership measurable at all."""
    from devicelink.o2_transport import verify_service_ownership

    fake = FakeO2Lite()
    fake.set_services("actl,game")

    assert verify_service_ownership(fake, "game") is True


def test_a_refused_service_never_routes_back():
    """O2 refuses a second claimant with "not from service provider"
    (o2/src/bridge.cpp:231-237) and logs it on the HUB, never telling the
    client: /_o2/*/sv is fire-and-forget. The refused client stays
    connected and clock-synced and looks perfectly healthy, so this round
    trip is the only thing that can tell the two apart."""
    from devicelink.o2_transport import verify_service_ownership

    fake = FakeO2Lite()
    fake.set_services("actl,game")
    fake.refuse("game")
    clock, sleep = _fake_clock()

    assert verify_service_ownership(fake, "game", timeout=2.0,
                                    clock=clock, sleep=sleep) is False


def test_the_fake_withholds_the_loopback_only_for_a_refused_service():
    """Boundary rule 5: a double must never be more permissive than the
    library it stands for. A fake that looped every send back would make
    the ownership check pass in every test while failing live -- the exact
    trap that rule was added for, on this same transport."""
    fake = FakeO2Lite()
    fake.set_services("actl,game")
    fake.refuse("game")
    seen = []
    fake.method_new("/game/_svcheck", "i", True,
                    lambda address, types, info: seen.append(fake.get_int32()),
                    None)

    fake.send_cmd("/game/_svcheck", 0, "i", 7)
    fake.poll()

    assert seen == []


def test_a_send_to_a_service_we_do_not_offer_never_loops_back():
    """Sending to a DEVICE's service must not come back to us. Without
    this the fake would loop every outbound LED frame into Control's own
    inbound queue."""
    fake = FakeO2Lite()
    fake.set_services("actl,game")
    seen = []
    fake.method_new("/ie1/leds", "b", True,
                    lambda address, types, info: seen.append(1), None)

    fake.send("/ie1/leds", 0, "b", [1, 2, 3])
    fake.poll()

    assert seen == []


def test_start_refuses_when_game_is_held_by_another_process():
    """Control's own `game` service has exactly the same exposure as a
    device's: an orphaned Terrarium holding it would make every device
    silently unreachable."""
    fake = FakeO2Lite()
    fake.refuse("game")
    transport = O2LiteTransport()
    clock, sleep = _fake_clock()

    with pytest.raises(RuntimeError, match="game"):
        transport.start(fake, ownership_timeout=1.0, clock=clock, sleep=sleep)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_o2_transport.py -q
```
Expected: FAIL with `ImportError: cannot import name 'verify_service_ownership'` and `AttributeError: 'FakeO2Lite' object has no attribute 'refuse'`.

- [ ] **Step 3: Teach `FakeO2Lite` to model a refused service**

Add `import time` to `devicelink/o2_transport.py`'s module imports (below `import logging`).

In `FakeO2Lite.__init__`, after the `_queue` line:

```python
        # Services the hub has REFUSED to register to this connection. A
        # message addressed to one of these never routes back, exactly as
        # O2 behaves when a second claimant loses the race
        # (o2/src/bridge.cpp:231-237). Boundary rule 5: the double has to
        # encode the strictness, not only the shape -- a fake that looped
        # every send back would hide precisely the bug this models.
        self.refused_services: set[str] = set()
```

Add these two methods to `FakeO2Lite`, after `set_services`:

```python
    def refuse(self, service: str) -> None:
        """Model the hub refusing this connection's claim on `service`."""
        self.refused_services.add(service)

    def _owns(self, address: str) -> bool:
        """Does the hub route `address` back to this connection?"""
        service = address.lstrip("!/").split("/")[0]
        claimed = [name for name in self.services.split(",") if name]
        return service in claimed and service not in self.refused_services
```

Replace `FakeO2Lite.send`:

```python
    def send(self, addr, timestamp, *args) -> None:
        typespec = args[0] if len(args) > 1 else ""
        rest = tuple(args[1:])
        self.sent.append((addr, timestamp, typespec, rest))
        # The hub has no local short circuit either (boundary rule 4): a
        # message addressed to a service THIS connection owns goes out and
        # comes back around to our own handler. That round trip is what
        # verify_service_ownership reads, so the fake has to reproduce it.
        if self._owns(addr):
            self._queue.append((addr, typespec, rest, timestamp))
```

- [ ] **Step 4: Add `verify_service_ownership`**

Add to `devicelink/o2_transport.py`, after the `pull_args`/`from_o2_arg` helpers:

```python
# The nonce verify_service_ownership round-trips. Fixed rather than random:
# the check is one request and one response with no concurrency, and a
# constant keeps the test deterministic.
_OWNERSHIP_NONCE = 0x5643484B          # "VCHK"

# How often the ownership check pumps o2lite while waiting for its own
# message to come back.
_OWNERSHIP_POLL_INTERVAL = 0.005


def verify_service_ownership(o2lite, service: str, *, timeout: float = 2.0,
                             clock=time.monotonic, sleep=time.sleep) -> bool:
    """Does `service` actually route back to THIS o2lite connection?

    o2lite's /_o2/*/sv is fire-and-forget. O2 refuses a second claimant
    with "not from service provider" (o2/src/bridge.cpp:231-237) and logs
    it on the HUB, never telling the client. A client that lost that race
    clock-syncs and is indistinguishable from a healthy one, while nothing
    addressed to it ever arrives -- it is delivered to whoever won.

    Boundary rule 4 is what makes this measurable: o2lite send() has no
    local short circuit, so a message addressed to our own service leaves
    for the hub and comes back only if the hub really routes that service
    to us. Rule 4 also asks that Control never message itself; this is a
    deliberate, documented exception -- ONE message, before the tick loop
    starts, as a startup assertion rather than a steady-state path.

    Sent over TCP (send_cmd), because a dropped UDP datagram would be
    indistinguishable from a lost service. Returns a bool and raises
    nothing: each caller decides what a failed check means. `clock` and
    `sleep` are injected so a test can exhaust the timeout without
    spending real time, the same way control/boot.py's
    wait_for_room_binding already does.
    """
    received = []

    def _on_check(address, typespec, info) -> None:
        received.append(o2lite.get_int32())

    o2lite.method_new(f"/{service}/_svcheck", "i", True, _on_check, None)
    o2lite.send_cmd(f"/{service}/_svcheck", 0, "i", _OWNERSHIP_NONCE)

    deadline = clock() + timeout
    while True:
        o2lite.poll()
        if _OWNERSHIP_NONCE in received:
            return True
        if clock() >= deadline:
            return False
        sleep(_OWNERSHIP_POLL_INTERVAL)
```

- [ ] **Step 5: Guard `O2LiteTransport.start`**

Replace `O2LiteTransport.start` with:

```python
    def start(self, o2lite, *, ownership_timeout: float = 2.0,
              clock=time.monotonic, sleep=time.sleep) -> None:
        """Adopt an already-connected o2lite object and claim `game` on it.

        Raises RuntimeError if the clock is not synced: time_get() returns
        -1 before sync, and a cue scheduled against -1 is meaningless.

        Also raises RuntimeError if the hub does not route `game` back
        here. set_services is fire-and-forget and O2 refuses a second
        claimant silently, so without this check an orphaned Terrarium
        holding `game` would make every device unreachable with no error
        anywhere. See verify_service_ownership on why the round trip is a
        deliberate, one-shot exception to boundary rule 4.
        """
        now = o2lite.time_get()
        if now < 0:
            raise RuntimeError(
                "o2lite clock is not synchronized (time_get() < 0); "
                "Arco must be clock master before Control offers `game`")
        self._o2 = o2lite
        o2lite.set_services(self._services)
        for verb in GAME_VERBS:
            # typespec None means "match any": a verb's shape is the Bit's
            # business, and GameServer.data already validates it.
            o2lite.method_new(f"/game/{verb}", None, True,
                              self._on_message, None)
        if not verify_service_ownership(o2lite, "game",
                                        timeout=ownership_timeout,
                                        clock=clock, sleep=sleep):
            self._o2 = None
            raise RuntimeError(
                "the `game` service is not routed back to this connection: "
                "another process on the Arco hub already offers it. O2 "
                "refuses a second claimant silently "
                "(o2/src/bridge.cpp:231-237). Look for an orphaned "
                "Terrarium or a stale harness/o2_shroom.py holding it.")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_o2_transport.py -q
```
Expected: PASS, including every pre-existing test in the file. If
`test_send_addresses_the_device_service_and_carries_the_timestamp` or any
other send test now fails, `_owns` is matching too broadly: a device's
service is never in `self.services`, so those sends must not loop back.

- [ ] **Step 7: Commit**

```bash
git add devicelink/o2_transport.py tests/test_o2_transport.py
git commit -m "feat(terrarium): detect a service the hub refused to register

o2lite's /_o2/*/sv is fire-and-forget, so a client that loses a service
race clock-syncs and looks healthy while nothing addressed to it ever
arrives. verify_service_ownership uses the property boundary rule 4
documents as a cost -- send() has no local short circuit -- as the
measurement: a self-addressed message returns only if the hub really
routes that service here. FakeO2Lite models the refusal, per rule 5."
```

---

### Task 3: The device fails loudly when its dev name is taken

**Files:**
- Modify: `harness/o2_shroom.py` (new `service_conflict`, `main()` gate)
- Test: `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: `devicelink.o2_transport.verify_service_ownership` from Task 2.
- Produces: `harness.o2_shroom.service_conflict(o2lite, dev: str, *, verify=None) -> str | None`. `verify` defaults to `None`, not to the function object, because resolving the default at module import would pull `devicelink.o2_transport` in at import time; the lazy resolution happens inside the body. No later task depends on it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_o2_shroom.py`:

```python
# --- The service the device just announced may have been refused. O2
# drops a second claimant's /_o2/*/sv with "not from service provider"
# (o2/src/bridge.cpp:231-237) and logs it on the HUB. Measured side by
# side, a refused simulator and an accepted player print the same two
# lines: a watch URL and "clock synced". This gate is what makes them
# distinguishable. -------------------------------------------------------

def test_service_conflict_is_silent_when_the_dev_is_ours():
    from harness.o2_shroom import service_conflict

    assert service_conflict(object(), "sim-room",
                            verify=lambda o2lite, dev: True) is None


def test_service_conflict_names_the_dev_and_the_remedy():
    """The whole cost of this bug was that it was invisible: the refused
    client printed its watch URL and clock-synced exactly like a healthy
    one, and Control saw no error either because the hub routed its frames
    successfully, to the wrong process. The message has to end the
    investigation on the spot."""
    from harness.o2_shroom import service_conflict

    message = service_conflict(object(), "sim-room",
                               verify=lambda o2lite, dev: False)

    assert message is not None
    assert "sim-room" in message
    assert "harness.o2_shroom" in message


def test_service_conflict_asks_about_the_dev_it_was_given():
    """A typo here would check the wrong service and always pass."""
    from harness.o2_shroom import service_conflict

    asked = []

    def _verify(o2lite, dev):
        asked.append(dev)
        return True

    service_conflict(object(), "ie1", verify=_verify)
    assert asked == ["ie1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_o2_shroom.py -q
```
Expected: FAIL with `ImportError: cannot import name 'service_conflict'`.

- [ ] **Step 3: Add `service_conflict`**

`harness/o2_shroom.py` must stay importable with no o2litepy on the path, so the default for `verify` is resolved lazily rather than at module import. Add this function after `parent_is_gone`:

```python
def service_conflict(o2lite, dev: str, *, verify=None):
    """Return a diagnostic string if `dev` is not ours, else None.

    Pure apart from the injected `verify`, so the message this prints is
    testable without an O2 hub. `verify` defaults to
    devicelink.o2_transport.verify_service_ownership, imported lazily
    because that module resolves its own o2litepy-free contract and this
    one must stay importable with no o2litepy present.

    Why this exists: a device whose service announcement O2 refused is
    indistinguishable from a healthy one. Both clock-sync, both print a
    watch URL, and Control sees no error because the hub routes its frames
    successfully -- to whoever won the service. See docs/superpowers/specs/
    2026-08-14-room-simulator-service-collision-design.md.
    """
    if verify is None:
        from devicelink.o2_transport import verify_service_ownership
        verify = verify_service_ownership
    if verify(o2lite, dev):
        return None
    return (f"FATAL: service {dev!r} is not routed back to this process. "
            f"Another process on the Arco hub already offers it, and O2 "
            f"refuses a second claimant silently "
            f"(o2/src/bridge.cpp:231-237). Nothing addressed to "
            f"/{dev}/* will ever arrive here. Look for a stale "
            f"'python -m harness.o2_shroom --dev {dev}' and kill it.")
```

- [ ] **Step 4: Gate `main()` on it**

Add `import sys` to `main()`'s local imports (beside `import argparse` and `import time`). Then, immediately after the `clock synced` print and before `o2lite.send_cmd("/game/hello", ...)`:

```python
    # The service announcement went out at set_services time and was never
    # acknowledged. Check it actually took before serving a canvas that
    # would otherwise stay dark for the whole run with no explanation.
    problem = service_conflict(o2lite, args.dev)
    if problem is not None:
        print(problem, file=sys.stderr)
        backend.close()
        raise SystemExit(1)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_o2_shroom.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "feat(terrarium): o2_shroom exits loudly when its dev name is taken

A device whose /_o2/*/sv O2 refused printed its watch URL and clock-synced
exactly like a healthy one, and Control saw no error either because the
hub routed frames successfully to whoever won the service. That silence
cost a whole live-demo debugging session."
```

---

### Task 4: `boot()` shuts the simulator down on any post-spawn failure

**Files:**
- Modify: `control/boot.py:27-99` (`boot`'s docstring and teardown block, new `_shutdown_simulator`)
- Test: `tests/test_boot.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the contract that a `simulator_factory` which spawns a process exposes it as `.process` with a `shutdown()`. Task 5 relies on the same attribute, which `harness/terrarium_boot.py`'s factories already set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_boot.py`:

```python
class _SpyProcess:
    """Stands in for control/simulator_process.py's SimulatorProcess:
    boot() only ever calls shutdown() on it."""

    def __init__(self):
        self.shutdowns = 0

    def shutdown(self):
        self.shutdowns += 1


class _SpyFactory:
    """A simulator_factory that SPAWNS. The contract is still a bare
    Callable[[], str]; a factory that spawns a process additionally
    exposes the handle as .process with a shutdown(), which is what
    harness/terrarium_boot.py's real factories already do and what its
    build() already reads back off the same object."""

    def __init__(self):
        self.process = None

    def __call__(self):
        self.process = _SpyProcess()
        return "sim-room-dev"


def test_boot_shuts_down_the_simulator_on_a_failure_after_it_spawned():
    """boot()'s structural guarantee covered Arco and never the simulator
    the same function spawns, three lines earlier. An orphaned Room
    simulator never exits on its own, reconnects to the NEXT Arco and
    claims sim-room there, so that run's own simulator is refused by O2
    (o2/src/bridge.cpp:231-237) and renders nothing."""
    factory = _SpyFactory()
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=factory)

    assert factory.process.shutdowns == 1


def test_boot_shuts_down_the_simulator_when_the_bit_fails_to_load():
    class _BrokenBit(RoomCapableBit):
        def __init__(self):
            raise ValueError("bad Bit")

    factory = _SpyFactory()
    config = BootConfig(room_type=RoomType.TEST, bit_name="BrokenBit")

    with pytest.raises(BootFailure, match="Bit load failed"):
        boot(config, {"BrokenBit": _BrokenBit}, arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=factory)

    assert factory.process.shutdowns == 1


def test_boot_shuts_both_down_on_a_keyboard_interrupt():
    """`except Exception` does not catch KeyboardInterrupt, so a Ctrl-C
    during boot leaked Arco AND the simulator. GameServer.load_bit's own
    handler is also `except Exception` (control/engine.py:80), so a
    KeyboardInterrupt raised while instantiating a Bit propagates straight
    out to boot()."""
    class _InterruptingBit(RoomCapableBit):
        def __init__(self):
            raise KeyboardInterrupt

    factory = _SpyFactory()
    fake_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="InterruptingBit")

    with pytest.raises(KeyboardInterrupt):
        boot(config, {"InterruptingBit": _InterruptingBit},
             arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=factory)

    assert factory.process.shutdowns == 1
    assert fake_popen.signals      # and Arco was told to stop too


def test_boot_still_accepts_a_factory_that_spawns_nothing():
    """Every other test in this file passes `lambda: "sim-room-dev"`, which
    has no .process at all. That must stay a no-op, not an AttributeError."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda: "sim-room-dev")


def test_boot_shuts_arco_down_even_if_the_simulator_shutdown_raises():
    """Cleanup must not mask the failure that triggered it, and must not
    let one leaked subprocess cause a second."""
    class _RaisingProcess:
        def shutdown(self):
            raise OSError("no such process")

    class _RaisingFactory:
        def __init__(self):
            self.process = None

        def __call__(self):
            self.process = _RaisingProcess()
            return "sim-room-dev"

    fake_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=_RaisingFactory())

    assert fake_popen.signals
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_boot.py -q
```
Expected: FAIL. `test_boot_shuts_down_the_simulator_on_a_failure_after_it_spawned` fails on `assert factory.process.shutdowns == 1` (it is 0), and `test_boot_shuts_both_down_on_a_keyboard_interrupt` fails on the same assertion plus `assert fake_popen.signals`.

- [ ] **Step 3: Add `_shutdown_simulator` to `control/boot.py`**

Add this function directly below `_bind_room_fast_path`:

```python
def _shutdown_simulator(simulator_factory) -> None:
    """Shut down a simulator subprocess the factory spawned, if any.

    A simulator_factory is a bare Callable[[], str] by contract, but one
    that SPAWNS a process exposes the handle as `.process` with a
    shutdown() -- harness/terrarium_boot.py's factories already do exactly
    that, and its build() already reads the attribute back off the same
    object. A factory that spawns nothing has no such attribute and this
    is a no-op.

    Swallows a failing shutdown deliberately: this runs on the way out of
    a boot that is already failing, and neither masking that failure nor
    skipping arco.shutdown() below it is acceptable.
    """
    process = getattr(simulator_factory, "process", None)
    if process is None:
        return
    try:
        process.shutdown()
    except Exception:
        pass
```

- [ ] **Step 4: Extend `boot()`'s teardown to cover it**

Replace `boot()`'s `except Exception:` block with:

```python
    except BaseException:
        # Arco is a live subprocess by this point, and _bind_room_fast_path
        # may have spawned a simulator subprocess too -- any failure below
        # here must orphan neither. An orphaned Room simulator never exits
        # on its own, reconnects to the NEXT Arco and re-claims its dev
        # name there, so that run's own simulator is refused by O2
        # (o2/src/bridge.cpp:231-237) and renders nothing, silently. See
        # docs/superpowers/specs/
        # 2026-08-14-room-simulator-service-collision-design.md.
        #
        # BaseException, not Exception: a Ctrl-C during boot used to leak
        # both subprocesses, since KeyboardInterrupt is not an Exception.
        # Re-raise unchanged: the inner handlers above already produced a
        # well-labeled BootFailure for every stage.
        _shutdown_simulator(simulator_factory)
        arco.shutdown()
        raise
```

Update `boot()`'s docstring: replace the sentence "Once Arco has actually
started, EVERY failure ... shuts Arco down before propagating." so it reads:

```
    Raises BootFailure on any stage failure. Once Arco has actually
    started, EVERY failure -- wait_ready timing out, an unknown/unsupported
    Bit, a Bit load error, a Ctrl-C, or anything unanticipated -- shuts
    down both Arco AND any simulator subprocess the factory spawned,
    before propagating. That's a structural guarantee (one try/except
    around the whole post-start section) rather than a shutdown call
    enumerated at each failure site, so a future failure mode added to
    this section can't accidentally orphan either subprocess by forgetting
    one.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_boot.py -q
```
Expected: PASS, including all pre-existing tests in the file.

- [ ] **Step 6: Commit**

```bash
git add control/boot.py tests/test_boot.py
git commit -m "fix(terrarium): boot() no longer orphans the simulator it spawned

The structural shutdown guarantee covered Arco and never the simulator
_bind_room_fast_path spawns three lines earlier, and its except Exception
missed KeyboardInterrupt entirely, so a Ctrl-C during boot leaked both."
```

---

### Task 5: `build()` cannot leak a spawned simulator

**Files:**
- Modify: `harness/terrarium_boot.py:114-145` (`build`'s post-`_boot` section)
- Test: `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: the `.process` attribute contract from Task 4 (`build()` already reads it at line 145).
- Produces: nothing new. `build()`'s signature and return tuple are unchanged.

- [ ] **Step 1: Write the failing test**

`tests/test_terrarium_boot.py` has no `import pytest` yet. Add it as the
first line of the file, then append:

```python
def test_build_tears_down_both_subprocesses_if_room_audio_fails(monkeypatch):
    """_boot() has already spawned Arco AND the simulator by the time
    build() constructs room_audio. If that raises, build() never returns,
    so main() never binds `simulator` and its `finally: shutdown(...)` has
    no handles to work with: both subprocesses outlive the run, and the
    orphaned simulator re-claims sim-room on the next run's Arco.

    ArcoSynthPool.start() raising is not hypothetical -- it is
    arco.initialize(), which raises TimeoutError, and the documented
    macOS /host/clear trap makes a second run on one Arco start fragile by
    design. Patched at its defining module for the same reason
    test_build_threads_its_clock_into_the_default_room_audio does: build()
    imports it lazily inside the function body."""
    class _ExplodingArcoSynthPool:
        def __init__(self, soundfont=None):
            pass

        def start(self) -> None:
            raise TimeoutError("Could not connect to Arco server")

    monkeypatch.setattr("harness.arco_synth.ArcoSynthPool",
                        _ExplodingArcoSynthPool)

    arco_popen = FakePopen()
    sim_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")

    with pytest.raises(TimeoutError):
        build(config, {"TestBit": TestBit}, arco_command=["arco-server"],
              room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
              arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
              simulator_popen=sim_popen)   # room_audio omitted: real branch

    assert sim_popen.signals    # simulator was told to stop, not orphaned
    assert arco_popen.signals   # and so was Arco
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_terrarium_boot.py::test_build_tears_down_both_subprocesses_if_room_audio_fails -q
```
Expected: FAIL on `assert sim_popen.signals` (the list is empty: nothing shut the simulator down).

- [ ] **Step 3: Wrap `build()`'s post-boot section**

In `harness/terrarium_boot.py`, record whether `build()` owns the devicelink
server. Change the transport branch at the top of `build()`:

```python
    owns_server = transport is None
    if transport is None:
        server = DeviceLinkServer(host=host, port=port)
        server.start()
    else:
        # o2lite mode: there is no socket to listen on. The connection is
        # pyarco's, already clock-synced by arco.initialize(), and the
        # caller started the transport on it.
        server = transport
```

Then replace everything from `if room_audio is None:` through the `return`:

```python
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
        # `finally: shutdown(...)` has no handles at all. An orphaned
        # simulator re-claims sim-room on the NEXT run's Arco, where O2
        # refuses that run's own simulator (o2/src/bridge.cpp:231-237) and
        # it renders nothing, silently. BaseException so a Ctrl-C during
        # the ArcoSynthPool connect -- which blocks for up to 30s -- is
        # covered too. See docs/superpowers/specs/
        # 2026-08-14-room-simulator-service-collision-design.md.
        if factory.process is not None:
            try:
                factory.process.shutdown()
            except Exception:
                pass        # never let cleanup mask the real failure
        arco.shutdown()
        if owns_server:
            server.stop()   # an injected transport belongs to the caller
        raise

    return gs, server, agent, arco, factory.process
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_terrarium_boot.py -q
```
Expected: PASS, including all pre-existing tests in the file.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "fix(terrarium): build() tears down both subprocesses on a late failure

_boot() spawns Arco and the simulator before build() constructs
room_audio. If ArcoSynthPool.start() raises -- it is arco.initialize(),
which raises TimeoutError -- build() never returns, so main() has no
handles and its finally: shutdown() cannot run."
```

---

### Task 6: Full suite, then sync the deep-dive

**Files:**
- Modify: `docs/MM_TERRARIUM.md`
- Test: the whole suite

- [ ] **Step 1: Run the full suite**

Run:
```bash
/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q
```
Expected: PASS. The baseline was 621 passed, 1 skipped; this plan adds 18
tests, so expect **639 passed, 1 skipped**. A different count means a test
was missed or an existing one broke. Investigate rather than adjusting the
number.

- [ ] **Step 2: Confirm the suite is still fully offline**

Run, from a shell with no `PYTHONPATH` set, so neither pyarco nor o2litepy
is importable:
```bash
env -u PYTHONPATH /Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q
```
Expected: the same result. If anything now fails on an import, a lazy
import was hoisted to module level and must go back inside its function.

- [ ] **Step 3: Sync the service deep-dive**

Invoke the `mm-deepdive-sync` skill. It updates `docs/MM_TERRARIUM.md` for
this branch. The content it needs to land, from the spec's section 4.3:

1. **Remove** the *Not yet built / deferred* bullet beginning "**Arco
   rejects the Room simulator's service announcement.**" It is resolved.
2. **Add**, in the landed-subsystems section covering
   `devicelink/o2_transport.py`, the resolved finding: an orphaned
   `harness/o2_shroom.py` never exits on its own and o2litepy reconnects it
   to the next Arco, where it re-claims `sim-room`; O2 refuses the new run's
   own simulator (`o2/src/bridge.cpp:231-237`) and the refusal is invisible
   to the client, so its frames are delivered to the zombie. Note the three
   guards now in place: `--exit-with-parent`, `boot()`'s extended teardown,
   and the startup ownership check.
3. **Add** to *Not yet built / deferred*, these two bullets verbatim:

```markdown
- **A refused o2lite service announcement is unobservable from the
  client.** `/_o2/*/sv` is fire-and-forget: O2 refuses a second claimant
  (`o2/src/bridge.cpp:231-237`), logs the drop on the **hub**, and offers
  the client no acknowledgement, no error callback and no way to query
  whether a registration took. A client that loses a service race
  clock-syncs and is indistinguishable from a healthy one while everything
  addressed to it is delivered to whoever won.
  `devicelink/o2_transport.py`'s `verify_service_ownership` works around
  this with a self-addressed round trip; it does not fix it. Upstream in
  O2.
- **o2litepy's discovery has no ensemble filter at all.**
  `o2litepy/o2lite_disc.py:24` takes `ensemble` as a constructor argument
  and never stores it, and `py3discovery.py:74` browses
  `_o2proc._tcp.local.` and appends every host it resolves. So an o2lite
  client joins whatever O2 host mDNS offers first: any ensemble, any
  machine on the LAN. Reproduced 2026-08-14, an `--ensemble arco` client
  registering its service on a host whose ensemble was something else
  entirely. Venue consequence: two Terrariums on one network would
  cross-connect today, which the "one Terrarium per room" model assumes
  they do not. Deserves an upstream report to Roger.
```
4. **Amend boundary rule 4's entry** to record the one deliberate
   exception: `verify_service_ownership` sends Control one message
   addressed to its own `game` service at startup. It is an assertion that
   *uses* the no-local-short-circuit property rule 4 documents as a cost,
   run once before the tick loop, not a steady-state path. `game` and
   `actl` remain inbound-only in steady state.

- [ ] **Step 4: Commit the doc sync**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): record the sim-room service collision and its fix"
```

- [ ] **Step 5: Report what could not be verified**

State plainly in the completion summary that spec success criterion 5 (a
live o2lite run showing `sim-room` registered on Arco and receiving
`/sim-room/leds`) **was not checked**, because `ArcoProcess` cannot spawn
Arco without a controlling TTY and this session has none. Criteria 1-4 are
covered by the suite. Do not claim the live path works.

Also state that **frame delivery is the measurement, not visible motion**:
nothing in `bits/test_bit.py` emits a cue targeting the Room, so its
declared `aurora` reaches one static hue and holds even when everything
here is correct.

---

## Manual verification (needs an interactive terminal)

These cannot run from an agent-driven session. Hand them to the operator.

**RUN ON: MYCOLOGICAL** (terminal 1, the Terrarium)

```bash
cd /Users/chris/projects/mm-terrarium && PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -u -m harness.terrarium_boot --transport o2lite --setup-seconds 20 --hold --horizon 0.15
```

**RUN ON: MYCOLOGICAL** (terminal 2, a player device)

```bash
cd /Users/chris/projects/mm-terrarium && PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.o2_shroom --dev ie1 --node TEST_PLAYER_NODE
```

**RUN ON: MYCOLOGICAL** (terminal 3, checks)

Before starting, confirm no orphan is already holding the service:

```bash
ps -o pid,ppid,command -ax | grep '[o]2_shroom'
```

While the run is up, confirm Arco accepted `sim-room`:

```bash
grep -c 'not from service provider' /Users/chris/projects/arco/apps/pytest/o2debug.log
```

Expected: `0`. Then kill the Terrarium the hard way and confirm nothing
survives it:

```bash
pkill -9 -f 'harness.terrarium_boot'; sleep 3; ps -ax | grep -c '[o]2_shroom'
```

Expected: `0`.
