# Terrarium Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Terrarium Console — a Bit-agnostic local admin panel that lets on-site MM staff load/run/abort a Bit and live-monitor its state over a websocket, as the durable front-end fixture every future Bit reuses.

**Architecture:** A new `console/` package mirroring `uplink/`: a transport-agnostic `ConsoleAgent` (engine observer + command dispatcher + snapshot/event builder), a pure-dict `protocol` module, a threaded HTTP+WS `ConsoleServer` (the only socket-touching code, single port), and a self-contained `index.html`. The engine's single-callback observer hooks are promoted to a multi-observer list so the console and the existing uplink run simultaneously.

**Tech Stack:** Python 3.14, `websockets>=13` (installed: 16.1.1), pytest. No O2/Arco/pyarco/Lux Aeterna/fairyring. No JS build step.

## Global Constraints

- Runtime dep floor: `websockets>=13` (already in `requirements.txt`); no new runtime deps.
- Everything must run offline in tests — no O2, Arco, pyarco, Lux Aeterna, or fairyring.
- `index.html` is vanilla HTML/JS/CSS: no build step, no external asset/CDN fetches.
- Console monitors only — it never instantiates ugens on Arco and never drives Lux Aeterna's render loop. Manifests are placeholder data.
- No authentication: trusted-LAN operator assumption. Default bind `127.0.0.1`; LAN exposure (`0.0.0.0`) is an explicit opt-in.
- Follow the existing repo idiom: synchronous, plain-tick-loop style (see `control/engine.py`, `uplink/transport.py`); pure-dict protocol builders with no engine imports (see `uplink/protocol.py`).
- Spec: `docs/superpowers/specs/2026-07-21-terrarium-console-design.md`.
- Run the suite with `python3 -m pytest tests -q` (the environment has `python3`, not `python`).

---

### Task 1: `Role.light_manifest` placeholder

**Files:**
- Modify: `control/roles.py`
- Test: `tests/test_roles.py`

**Interfaces:**
- Produces: `control.roles.Role.light_manifest: list` (empty-list default), sibling to the existing `ugen_manifest: list`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_roles.py`:

```python
def test_role_has_empty_light_manifest_by_default():
    from control.roles import Role, RoleClass
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True)
    assert role.light_manifest == []
    # sibling placeholder to ugen_manifest; distinct list instances
    assert role.light_manifest is not role.ugen_manifest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_roles.py::test_role_has_empty_light_manifest_by_default -v`
Expected: FAIL — `TypeError` / `AttributeError` (no `light_manifest`).

- [ ] **Step 3: Add the field**

In `control/roles.py`, in the `Role` dataclass, immediately after the `ugen_manifest` field, add:

```python
    # Placeholder for this role's per-player light-lane declaration, sibling
    # to ugen_manifest. Light is authored in the same timeline as sound
    # (see mm-documents shroom-installations-design.md); this exists so the
    # schema doesn't change when the first real Bit declares light lanes.
    # The Terrarium Console displays it; it never drives Lux Aeterna's render
    # loop. Unused in this slice.
    light_manifest: list = field(default_factory=list)
```

(`field` is already imported in this module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_roles.py -v`
Expected: PASS (all role tests).

- [ ] **Step 5: Commit**

```bash
git add control/roles.py tests/test_roles.py
git commit -m "feat: add Role.light_manifest placeholder for the console media display"
```

---

### Task 2: `Bit.status()` seam + TestBit exemplar

**Files:**
- Modify: `control/bit.py`
- Modify: `bits/test_bit.py`
- Test: `tests/test_test_bit.py`

**Interfaces:**
- Produces: `control.bit.Bit.status() -> dict` (default returns `{}`); `bits.test_bit.TestBit.status()` returns `{"elapsed": <float>, "run_duration": <float>}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_test_bit.py`:

```python
def test_bit_status_defaults_to_empty_dict():
    from control.roles import RoleTable
    from control.bit import Bit

    class MinimalBit(Bit):
        @property
        def role_table(self) -> RoleTable:
            return RoleTable(roles={}, node_map={})

    assert MinimalBit().status() == {}


def test_test_bit_status_reports_elapsed_and_duration():
    from bits.test_bit import TestBit
    bit = TestBit(run_duration=5.0)
    bit.on_run_start()
    bit.update(1.5)
    status = bit.status()
    assert status["run_duration"] == 5.0
    assert status["elapsed"] == 1.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_test_bit.py -k status -v`
Expected: FAIL — `AttributeError: 'MinimalBit' object has no attribute 'status'`.

- [ ] **Step 3: Add the default seam to `Bit`**

In `control/bit.py`, add this method to the `Bit` class (place it after `result()`):

```python
    def status(self) -> dict:
        """Optional generic key/value read-out for the Terrarium Console to
        render as a table. Default: nothing to report. A Bit overrides this
        to surface its own live state. This is also the seam a future
        Lux Aeterna / Arco health read-out rides on.
        """
        return {}
```

- [ ] **Step 4: Override it in `TestBit`**

In `bits/test_bit.py`, add this method to `TestBit` (after `on_unload`):

```python
    def status(self) -> dict:
        return {"elapsed": round(self._elapsed, 2),
                "run_duration": self._run_duration}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_test_bit.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add control/bit.py bits/test_bit.py tests/test_test_bit.py
git commit -m "feat: add Bit.status() seam with TestBit exemplar"
```

---

### Task 3: `DevicePool.all()` accessor

**Files:**
- Modify: `control/device_pool.py`
- Test: `tests/test_device_pool.py`

**Interfaces:**
- Produces: `control.device_pool.DevicePool.all() -> list[DeviceInfo]` — snapshot of every known device, insertion order.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_device_pool.py`:

```python
def test_all_returns_every_known_device():
    from control.device_pool import DevicePool
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1")
    pool.hello("ie2", "Shroom Two", "1")
    devs = pool.all()
    assert [d.dev for d in devs] == ["ie1", "ie2"]
    # returns a fresh list, not the internal dict's view
    devs.clear()
    assert len(pool) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_device_pool.py::test_all_returns_every_known_device -v`
Expected: FAIL — `AttributeError: 'DevicePool' object has no attribute 'all'`.

- [ ] **Step 3: Add the accessor**

In `control/device_pool.py`, add to `DevicePool` (after `get`):

```python
    def all(self) -> list[DeviceInfo]:
        """Every known device, insertion order -- the public view for the
        Terrarium Console snapshot. Returns a fresh list; mutating it does
        not affect the pool.
        """
        return list(self._devices.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_device_pool.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control/device_pool.py tests/test_device_pool.py
git commit -m "feat: add DevicePool.all() accessor for the console snapshot"
```

---

### Task 4: Engine multi-observer refactor + uplink migration

Promote `on_state_change` / `on_registration_change` from single-callback attributes to a multi-observer list, add an `on_devices_change` notification, and migrate `UplinkAgent` and all existing tests onto the new API. This whole task lands together so the suite stays green (the refactor breaks the old attribute API atomically).

**Files:**
- Modify: `control/engine.py`
- Modify: `uplink/link.py`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_link.py`

**Interfaces:**
- Produces:
  - `GameServer.add_observer(observer) -> None` — registers an observer object. The engine calls, when present, `observer.on_state_change(old: State, new: State)`, `observer.on_registration_change()`, and `observer.on_devices_change()`. Missing methods are skipped; an observer raising is logged and never interrupts the engine or other observers. Notification order is registration order.
  - `on_devices_change` fires on `hello()`, on a granted `join()`, and once during `_unload()` after devices are released.
  - `GameServer.on_release` is unchanged (single transport-owned sink).
- Consumes: nothing new.
- Removed: the `GameServer.on_state_change` and `GameServer.on_registration_change` public attributes (replaced by `add_observer`).

- [ ] **Step 1: Write the failing engine tests**

Add to `tests/test_engine.py` (top-level, near the other observer tests). Note the `SimpleNamespace` adapter — it lets a test register just the callbacks it cares about:

```python
def test_add_observer_notifies_multiple_observers_of_state_changes():
    from types import SimpleNamespace
    from bits.test_bit import TestBit
    from control.engine import GameServer
    a, b = [], []
    server = GameServer({"TestBit": TestBit})
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: a.append(new)))
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: b.append(new)))
    server.load_bit("TestBit")
    assert a == b and len(a) >= 3  # both saw the same transitions


def test_observer_exception_does_not_break_engine_or_peers():
    from types import SimpleNamespace
    from bits.test_bit import TestBit
    from control.engine import GameServer
    seen = []
    server = GameServer({"TestBit": TestBit})

    def boom(old, new):
        raise RuntimeError("observer blew up")

    server.add_observer(SimpleNamespace(on_state_change=boom))
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: seen.append(new)))
    server.load_bit("TestBit")            # must not raise
    assert len(seen) >= 3                  # peer still notified


def test_on_devices_change_fires_on_hello_join_and_unload():
    from types import SimpleNamespace
    from bits.test_bit import TestBit
    from control.engine import GameServer
    calls = []
    server = GameServer({"TestBit": TestBit})
    server.add_observer(SimpleNamespace(
        on_devices_change=lambda: calls.append("devices")))
    server.hello("ie1", "Shroom One", "1")        # +1
    server.load_bit("TestBit")
    server.join("ie1", "TEST_PLAYER_NODE")        # +1 (granted)
    n_before_abort = len(calls)
    server.abort()                                 # +1 (unload releases devices)
    assert len(calls) == n_before_abort + 1
    assert n_before_abort == 2
```

- [ ] **Step 2: Migrate the existing attribute-style engine tests**

In `tests/test_engine.py`, replace the three direct-attribute assignments so they use `add_observer` with a `SimpleNamespace`. Add `from types import SimpleNamespace` at the top of the file if not already present.

- Line ~118 (`test_on_state_change_fires_for_every_transition`): replace
  `server.on_state_change = lambda old, new: transitions.append((old, new))`
  with
  `server.add_observer(SimpleNamespace(on_state_change=lambda old, new: transitions.append((old, new))))`
- Line ~139 (`test_on_state_change_fires_on_failed_load_bit`): same replacement for its `server.on_state_change = ...` line.
- Line ~154 (`test_on_registration_change_fires_only_on_granted_join`): replace
  `server.on_registration_change = lambda: calls.append(server.registration.counts())`
  with
  `server.add_observer(SimpleNamespace(on_registration_change=lambda: calls.append(server.registration.counts())))`

(The `server.on_release = ...` lines at ~67 and ~176 are unchanged — `on_release` stays a single attribute.)

- [ ] **Step 3: Run the new + migrated engine tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: FAIL — `AttributeError: 'GameServer' object has no attribute 'add_observer'`.

- [ ] **Step 4: Refactor `control/engine.py`**

In `GameServer.__init__`, replace these three lines:

```python
        # Set by UplinkAgent: called with (old_state, new_state) on every
        # state transition.
        self.on_state_change = None
        # Set by UplinkAgent: called with no arguments after a join grants a
        # role. Callers read self.registration.counts() for the snapshot.
        self.on_registration_change = None
```

with:

```python
        # Observers registered via add_observer(). Each may implement any of
        # on_state_change(old, new), on_registration_change(),
        # on_devices_change(); missing methods are skipped. Both the uplink
        # and the Terrarium Console attach here and run simultaneously.
        self._observers: list = []
```

Add these two methods to `GameServer` (place them just above `_set_state`):

```python
    def add_observer(self, observer) -> None:
        """Register an observer object. The engine calls, when present,
        observer.on_state_change(old, new), observer.on_registration_change(),
        and observer.on_devices_change(). Notification is in registration
        order; a raising observer is logged and never interrupts the engine
        or its peers.
        """
        self._observers.append(observer)

    def _notify(self, method: str, *args) -> None:
        for observer in self._observers:
            callback = getattr(observer, method, None)
            if callback is None:
                continue
            try:
                callback(*args)
            except Exception:
                logger.exception("observer %r %s raised; continuing",
                                 observer, method)
```

In `_set_state`, replace:

```python
        if self.on_state_change:
            self.on_state_change(old_state, new_state)
```

with:

```python
        self._notify("on_state_change", old_state, new_state)
```

In `hello`, change the body to notify devices:

```python
    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion)
        self._notify("on_devices_change")
```

In `join`, replace:

```python
        if result.granted and self.on_registration_change:
            self.on_registration_change()
```

with:

```python
        if result.granted:
            self._notify("on_registration_change")
            self._notify("on_devices_change")
```

In `_unload`, add a devices notification after the release loop. Replace:

```python
        released = self.registration.release_all()
        if self.on_release:
            for dev in released:
                self.on_release(dev)
```

with:

```python
        released = self.registration.release_all()
        if self.on_release:
            for dev in released:
                self.on_release(dev)
        self._notify("on_devices_change")
```

Finally, update the module docstring lines that mention the old attributes. Replace `on_state_change/on_registration_change` in the docstring (lines ~6-7) with:

```
observable by any number of add_observer() observers (the Terrarium uplink
and the Terrarium Console both attach) via on_state_change/
on_registration_change/on_devices_change, and remotely abortable via
```

- [ ] **Step 5: Run engine tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS.

- [ ] **Step 6: Migrate `UplinkAgent` onto `add_observer`**

In `uplink/link.py`, in `UplinkAgent.__init__`, replace:

```python
        game_server.on_state_change = self._on_state_change
        game_server.on_registration_change = self._on_registration_change
```

with:

```python
        game_server.add_observer(self)
```

Rename the two observer methods from private to the public names the engine looks up. Change `def _on_state_change(self, old_state: State, new_state: State) -> None:` to `def on_state_change(self, old_state: State, new_state: State) -> None:`, and `def _on_registration_change(self) -> None:` to `def on_registration_change(self) -> None:`. (UplinkAgent intentionally does **not** implement `on_devices_change`; the engine skips it.)

- [ ] **Step 7: Migrate the `UplinkAgent` registration test**

In `tests/test_link.py`, the test around lines 18-21 asserts the old attributes are set. Replace its body so it asserts behavior instead. Find:

```python
    UplinkAgent(server, FakeTransport())
    assert server.on_state_change is not None
    assert server.on_registration_change is not None
```

Replace with (keep the surrounding test function name and setup):

```python
    transport = FakeTransport()
    transport.connect()
    UplinkAgent(server, transport)
    server.load_bit("TestBit")   # drives state transitions through the observer
    assert any(m.get("event") == "state_changed" for m in transport.sent)
```

If that test relied on `TestBit` being registered, ensure its `server` is built as `GameServer({"TestBit": TestBit})` (import `TestBit` at the top of the test module if not already imported).

- [ ] **Step 8: Run the full suite to verify the migration is green**

Run: `python3 -m pytest tests -q`
Expected: PASS (all pre-existing tests + the new ones).

- [ ] **Step 9: Commit**

```bash
git add control/engine.py uplink/link.py tests/test_engine.py tests/test_link.py
git commit -m "refactor: promote engine hooks to multi-observer list; migrate uplink"
```

---

### Task 5: `console/protocol.py` — message builders

Pure JSON-dict builders and command re-exports. No engine imports (mirrors `uplink/protocol.py`).

**Files:**
- Create: `console/__init__.py`
- Create: `console/protocol.py`
- Test: `tests/test_console_protocol.py`

**Interfaces:**
- Consumes: `uplink.protocol.parse_command`, `LoadBitCommand`, `RunCommand`, `AbortCommand`, `state_changed_event`, `registration_changed_event`, `bit_completed_event`, `error_event` (re-used).
- Produces (all return plain `dict`s unless noted):
  - `parse_command` — re-exported from `uplink.protocol` (single source of truth).
  - `role_view(role) -> dict` → `{"role","class","capacity","scored","ugen_manifest","light_manifest"}` (`class` is `role.role_class.name`).
  - `device_view(info, role_name) -> dict` → `{"dev","name","role"}` (`role` is a str or `None`).
  - `snapshot_event(*, state, installed_bits, loaded_bit, roles, registration, devices, bit_status) -> dict` with `"event": "snapshot"`.
  - `devices_changed_event(devices) -> dict` with `"event": "devices_changed"`.
  - `bit_status_event(status) -> dict` with `"event": "bit_status"`.
  - `log_event(level, message) -> dict` with `"event": "log"`.
  - re-exports `state_changed_event`, `registration_changed_event`, `bit_completed_event`, `error_event` from `uplink.protocol`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_console_protocol.py`:

```python
from console import protocol
from control.roles import Role, RoleClass


def test_role_view_shape():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True)
    assert protocol.role_view(role) == {
        "role": "player", "class": "SHARED", "capacity": None,
        "scored": True, "ugen_manifest": [], "light_manifest": []}


def test_device_view_shape():
    from control.device_pool import DeviceInfo
    info = DeviceInfo(dev="ie3", name="Shroom Three", protoversion="1")
    assert protocol.device_view(info, "player") == {
        "dev": "ie3", "name": "Shroom Three", "role": "player"}
    assert protocol.device_view(info, None)["role"] is None


def test_snapshot_event_shape():
    msg = protocol.snapshot_event(
        state="SETUP", installed_bits=["TestBit"], loaded_bit="TestBit",
        roles=[{"role": "player"}], registration=[{"role": "player"}],
        devices=[{"dev": "ie3"}], bit_status={"elapsed": 0.0})
    assert msg["event"] == "snapshot"
    assert msg["state"] == "SETUP"
    assert msg["installed_bits"] == ["TestBit"]
    assert msg["loaded_bit"] == "TestBit"
    assert msg["roles"] == [{"role": "player"}]
    assert msg["registration"] == [{"role": "player"}]
    assert msg["devices"] == [{"dev": "ie3"}]
    assert msg["bit_status"] == {"elapsed": 0.0}


def test_incremental_event_shapes():
    assert protocol.devices_changed_event([{"dev": "ie1"}]) == {
        "event": "devices_changed", "devices": [{"dev": "ie1"}]}
    assert protocol.bit_status_event({"k": 1}) == {
        "event": "bit_status", "status": {"k": 1}}
    assert protocol.log_event("info", "hi") == {
        "event": "log", "level": "info", "message": "hi"}


def test_command_parsing_is_reused_from_uplink():
    from uplink.protocol import LoadBitCommand
    assert protocol.parse_command(
        {"command": "load_bit", "name": "TestBit"}) == LoadBitCommand("TestBit")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_console_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'console'`.

- [ ] **Step 3: Create the package + module**

Create `console/__init__.py`:

```python
"""Terrarium Console: the local admin panel for the Control+GameServer.
See docs/superpowers/specs/2026-07-21-terrarium-console-design.md.
"""
```

Create `console/protocol.py`:

```python
"""Wire message schemas for the Terrarium Console -- the JSON-serializable
contract between the browser panel and ConsoleAgent. Pure dict builders with
no engine imports, mirroring uplink/protocol.py. Command parsing and the
events shared with the uplink are re-used from uplink.protocol so there is a
single source of truth.
"""

from uplink.protocol import (  # re-exported: single source of truth
    AbortCommand,
    LoadBitCommand,
    RunCommand,
    bit_completed_event,
    error_event,
    parse_command,
    registration_changed_event,
    state_changed_event,
)

__all__ = [
    "AbortCommand", "LoadBitCommand", "RunCommand", "parse_command",
    "bit_completed_event", "error_event", "registration_changed_event",
    "state_changed_event", "role_view", "device_view", "snapshot_event",
    "devices_changed_event", "bit_status_event", "log_event",
]


def role_view(role) -> dict:
    return {
        "role": role.name,
        "class": role.role_class.name,
        "capacity": role.capacity,
        "scored": role.scored,
        "ugen_manifest": role.ugen_manifest,
        "light_manifest": role.light_manifest,
    }


def device_view(info, role_name) -> dict:
    return {"dev": info.dev, "name": info.name, "role": role_name}


def snapshot_event(*, state, installed_bits, loaded_bit, roles,
                   registration, devices, bit_status) -> dict:
    return {
        "event": "snapshot",
        "state": state,
        "installed_bits": installed_bits,
        "loaded_bit": loaded_bit,
        "roles": roles,
        "registration": registration,
        "devices": devices,
        "bit_status": bit_status,
    }


def devices_changed_event(devices) -> dict:
    return {"event": "devices_changed", "devices": devices}


def bit_status_event(status) -> dict:
    return {"event": "bit_status", "status": status}


def log_event(level: str, message: str) -> dict:
    return {"event": "log", "level": level, "message": message}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_console_protocol.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add console/__init__.py console/protocol.py tests/test_console_protocol.py
git commit -m "feat: add console protocol message builders"
```

---

### Task 6: `console/agent.py` — `ConsoleAgent`

The transport-agnostic brains: an engine observer that broadcasts events, builds the connect-time snapshot, dispatches inbound commands, and self-throttles `Bit.status()` broadcasts by change-detection. Driven from the tick loop via `poll()`. Tested against an in-process `FakeConsoleServer`.

**Files:**
- Create: `console/agent.py`
- Create: `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `control.engine.GameServer` (`add_observer`, `state`, `bit_registry`, `bit`, `registration`, `devices`, `load_bit`, `run`, `abort`), `control.engine.InvalidTransition`, `control.engine.BitLoadError`, `control.state.State`, `control.registration.RegistrationState.counts()`, `control.device_pool.DevicePool.all()`, `console.protocol`.
- Server boundary (what `ConsoleAgent` needs from a server — `console/server.py` and the test fake both implement it):
  - `drain_new_clients() -> list` — client handles connected since the last drain (each needs a snapshot).
  - `drain_inbound() -> list[tuple[object, dict]]` — `(client_handle, message_dict)` pairs received since the last drain.
  - `send(client_handle, msg: dict) -> None` — send to one client.
  - `broadcast(msg: dict) -> None` — send to all connected clients.
- Produces:
  - `console.agent.ConsoleAgent(game_server, server)` — registers itself as an engine observer in `__init__`.
  - `ConsoleAgent.snapshot() -> dict` — full current read model.
  - `ConsoleAgent.poll() -> None` — per-tick: snapshot new clients, dispatch inbound commands (error reply to origin), and broadcast a `bit_status` event when the status changed.
  - Observer callbacks: `on_state_change(old, new)`, `on_registration_change()`, `on_devices_change()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_console_agent.py`:

```python
from bits.test_bit import TestBit
from console.agent import ConsoleAgent
from control.engine import GameServer


class FakeConsoleServer:
    """In-process test double for console/server.py -- no threads, no socket.
    Tests push new clients + inbound messages and inspect sent/broadcast.
    """

    def __init__(self):
        self.broadcasts = []                # list[dict]
        self.sent = []                      # list[(client, dict)]
        self._new_clients = []
        self._inbound = []                  # list[(client, dict)]

    # --- tick-thread API consumed by ConsoleAgent ---
    def drain_new_clients(self):
        out, self._new_clients = self._new_clients, []
        return out

    def drain_inbound(self):
        out, self._inbound = self._inbound, []
        return out

    def send(self, client, msg):
        self.sent.append((client, msg))

    def broadcast(self, msg):
        self.broadcasts.append(msg)

    # --- test helpers ---
    def connect(self, client):
        self._new_clients.append(client)

    def deliver(self, client, msg):
        self._inbound.append((client, msg))


def _server_with_agent():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)
    return gs, srv, agent


def test_new_client_gets_a_snapshot_on_poll():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    agent.poll()
    assert len(srv.sent) == 1
    client, msg = srv.sent[0]
    assert client == "c1"
    assert msg["event"] == "snapshot"
    assert msg["state"] == "IDLE"
    assert msg["installed_bits"] == ["TestBit"]
    assert msg["loaded_bit"] is None


def test_snapshot_reflects_loaded_bit_and_registration():
    gs, srv, agent = _server_with_agent()
    gs.hello("ie1", "Shroom One", "1")
    gs.load_bit("TestBit")
    gs.join("ie1", "TEST_PLAYER_NODE")
    srv.connect("c1")
    agent.poll()
    snap = srv.sent[-1][1]
    assert snap["loaded_bit"] == "TestBit"
    assert {r["role"] for r in snap["roles"]} == {"player", "jammer"}
    assert any(d["dev"] == "ie1" and d["role"] == "player"
               for d in snap["devices"])
    assert snap["bit_status"]["run_duration"] == TestBit().status()["run_duration"]


def test_load_bit_command_drives_engine_and_broadcasts_state():
    gs, srv, agent = _server_with_agent()
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})
    agent.poll()
    assert gs.state.name == "SETUP"
    assert any(m.get("event") == "state_changed" and m["state"] == "SETUP"
               for m in srv.broadcasts)


def test_registration_change_is_broadcast():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    srv.broadcasts.clear()
    gs.join("ie9", "TEST_PLAYER_NODE")
    assert any(m.get("event") == "registration_changed" for m in srv.broadcasts)
    assert any(m.get("event") == "devices_changed" for m in srv.broadcasts)


def test_bad_command_sends_error_to_origin_only():
    gs, srv, agent = _server_with_agent()
    # run() from IDLE is an InvalidTransition
    srv.deliver("c1", {"command": "run"})
    agent.poll()
    errors = [m for (_, m) in srv.sent if m.get("event") == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "run"
    assert not any(m.get("event") == "error" for m in srv.broadcasts)


def test_unparseable_command_is_dropped_without_crashing():
    gs, srv, agent = _server_with_agent()
    srv.deliver("c1", {"command": "nonsense"})
    agent.poll()   # must not raise
    assert gs.state.name == "IDLE"


def test_bit_status_broadcast_only_on_change():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    gs.run()
    srv.broadcasts.clear()
    agent.poll()                       # first poll after run: status changed
    first = [m for m in srv.broadcasts if m.get("event") == "bit_status"]
    assert len(first) == 1
    srv.broadcasts.clear()
    agent.poll()                       # no elapsed change -> no new status
    assert not [m for m in srv.broadcasts if m.get("event") == "bit_status"]


def test_bit_completed_is_broadcast_on_unload():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    gs.run()
    srv.broadcasts.clear()
    gs.abort()                          # -> UNLOADING -> IDLE
    # TestBit.result() default is None, so no bit_completed; assert state only.
    assert any(m.get("event") == "state_changed" and m["state"] == "UNLOADING"
               for m in srv.broadcasts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_console_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'console.agent'`.

- [ ] **Step 3: Implement `ConsoleAgent`**

Create `console/agent.py`:

```python
"""ConsoleAgent: translates between the console wire protocol and GameServer
calls, and pushes live state to connected browsers. The local, inbound
sibling of uplink.UplinkAgent -- transport-agnostic (it talks to a server
object, see console/server.py), so it is fully testable offline against an
in-process fake. Driven from the engine tick loop via poll().
"""

import logging

from console import protocol
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State

logger = logging.getLogger(__name__)


class ConsoleAgent:
    def __init__(self, game_server: GameServer, server):
        self.game_server = game_server
        self.server = server
        self._last_status: dict | None = None
        game_server.add_observer(self)

    # --- driven once per tick-loop iteration -------------------------------
    def poll(self) -> None:
        for client in self.server.drain_new_clients():
            self.server.send(client, self.snapshot())
        for client, msg in self.server.drain_inbound():
            error = self._handle_command(msg)
            if error is not None:
                self.server.send(client, error)
        self._broadcast_status_if_changed()

    # --- inbound command dispatch ------------------------------------------
    def _handle_command(self, msg: dict) -> dict | None:
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable console message: %s", exc)
            return None
        name = msg.get("command")
        try:
            if isinstance(command, protocol.LoadBitCommand):
                self.game_server.load_bit(command.name)
            elif isinstance(command, protocol.RunCommand):
                self.game_server.run()
            elif isinstance(command, protocol.AbortCommand):
                self.game_server.abort()
        except (InvalidTransition, BitLoadError) as exc:
            return protocol.error_event(name, str(exc))
        return None

    # --- snapshot (connect-time full read model) ---------------------------
    def snapshot(self) -> dict:
        gs = self.game_server
        loaded_bit = None
        roles: list = []
        registration: list = []
        if gs.registration is not None:
            loaded_bit = self._loaded_bit_name()
            roles = [protocol.role_view(r)
                     for r in gs.registration.role_table.roles.values()]
            registration = protocol.registration_changed_event(
                gs.registration.counts())["roles"]
        return protocol.snapshot_event(
            state=gs.state.name,
            installed_bits=list(gs.bit_registry.keys()),
            loaded_bit=loaded_bit,
            roles=roles,
            registration=registration,
            devices=self._devices_view(),
            bit_status=self._current_status(),
        )

    def _loaded_bit_name(self) -> str | None:
        for name, cls in self.game_server.bit_registry.items():
            if isinstance(self.game_server.bit, cls):
                return name
        return None

    def _devices_view(self) -> list:
        gs = self.game_server
        assignments = gs.registration.assignments if gs.registration else {}
        out = []
        for info in gs.devices.all():
            assigned = assignments.get(info.dev)
            role_name = assigned[1] if assigned else None
            out.append(protocol.device_view(info, role_name))
        return out

    def _current_status(self) -> dict:
        bit = self.game_server.bit
        if bit is None:
            return {}
        try:
            return bit.status()
        except Exception:
            logger.exception("Bit.status raised; reporting empty status")
            return {}

    def _broadcast_status_if_changed(self) -> None:
        status = self._current_status()
        if status != self._last_status:
            self._last_status = status
            self.server.broadcast(protocol.bit_status_event(status))

    # --- engine observer callbacks -----------------------------------------
    def on_state_change(self, old_state: State, new_state: State) -> None:
        self.server.broadcast(protocol.state_changed_event(new_state.name))
        if new_state == State.UNLOADING:
            self._broadcast_bit_completed()

    def on_registration_change(self) -> None:
        counts = self.game_server.registration.counts()
        self.server.broadcast(protocol.registration_changed_event(counts))

    def on_devices_change(self) -> None:
        self.server.broadcast(protocol.devices_changed_event(
            self._devices_view()))

    def _broadcast_bit_completed(self) -> None:
        bit = self.game_server.bit
        if bit is None:
            return
        try:
            result = bit.result()
        except Exception:
            logger.exception("Bit.result raised; not broadcasting bit_completed")
            return
        if result is not None:
            self.server.broadcast(protocol.bit_completed_event(result))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_console_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add console/agent.py tests/test_console_agent.py
git commit -m "feat: add ConsoleAgent (engine observer + command dispatch + snapshot)"
```

---

### Task 7: `console/static/index.html` — the admin page

A single self-contained page. Created before the server task because the server's integration test serves this file on `GET /`.

**Files:**
- Create: `console/static/index.html`
- Test: `tests/test_console_static.py`

**Interfaces:**
- Produces: `console/static/index.html` — self-contained (no external fetches). Opens a websocket to `ws://<host>/ws`, renders `snapshot` then applies incremental events, and sends `{"command": ...}` messages on button clicks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_console_static.py`:

```python
from pathlib import Path


def test_index_html_is_self_contained():
    html = (Path(__file__).resolve().parent.parent
            / "console" / "static" / "index.html").read_text()
    # no external asset fetches (Global Constraint)
    for needle in ("http://", "https://", "//cdn", "src=\"//"):
        assert needle not in html, f"external reference found: {needle}"
    # the pieces the panel is built from
    assert "new WebSocket" in html
    assert "/ws" in html
    assert "load_bit" in html and "\"run\"" in html and "abort" in html
    assert "snapshot" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_console_static.py -v`
Expected: FAIL — `FileNotFoundError`.

- [ ] **Step 3: Create the page**

Create `console/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terrarium Console</title>
<style>
  body { font: 14px/1.4 system-ui, sans-serif; margin: 1rem; color: #111; }
  h1 { font-size: 1.2rem; } h2 { font-size: 1rem; margin: 1rem 0 .3rem; }
  #state { font-weight: 600; }
  button { margin-right: .5rem; padding: .3rem .7rem; }
  table { border-collapse: collapse; margin-top: .3rem; }
  th, td { border: 1px solid #ccc; padding: .2rem .5rem; text-align: left; }
  #log { height: 8rem; overflow: auto; background: #f6f6f6; padding: .3rem;
         font-family: ui-monospace, monospace; white-space: pre-wrap; }
  .conn-down { color: #a00; }
</style>
</head>
<body>
<h1>Terrarium Console <span id="conn" class="conn-down">(connecting…)</span></h1>

<p>State: <span id="state">—</span> &nbsp; Loaded Bit: <span id="loaded">—</span></p>

<h2>Controls</h2>
<div>
  <select id="bitPicker"></select>
  <button id="loadBtn">Load Bit</button>
  <button id="runBtn">Run</button>
  <button id="abortBtn">Abort</button>
</div>

<h2>Registration</h2>
<table id="registration"><thead><tr><th>Role</th><th>Count</th><th>Capacity</th></tr></thead><tbody></tbody></table>

<h2>Roles &amp; media manifests</h2>
<table id="roles"><thead><tr><th>Role</th><th>Class</th><th>Cap</th><th>Scored</th><th>ugen_manifest</th><th>light_manifest</th></tr></thead><tbody></tbody></table>

<h2>Devices</h2>
<table id="devices"><thead><tr><th>Device</th><th>Name</th><th>Role</th></tr></thead><tbody></tbody></table>

<h2>Bit status</h2>
<table id="bitStatus"><tbody></tbody></table>

<h2>Event log</h2>
<div id="log"></div>

<script>
const $ = (id) => document.getElementById(id);
let ws;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { $("conn").textContent = "(connected)"; $("conn").className = ""; };
  ws.onclose = () => {
    $("conn").textContent = "(disconnected — retrying)";
    $("conn").className = "conn-down";
    setTimeout(connect, 1000);
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

function send(command, extra) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(Object.assign({ command }, extra || {})));
  }
}

$("loadBtn").onclick = () => send("load_bit", { name: $("bitPicker").value });
$("runBtn").onclick = () => send("run");
$("abortBtn").onclick = () => send("abort");

function rows(tbodySel, data, cells) {
  const tbody = document.querySelector(tbodySel + " tbody");
  tbody.innerHTML = "";
  for (const item of data) {
    const tr = document.createElement("tr");
    for (const c of cells(item)) {
      const td = document.createElement("td");
      td.textContent = c;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function renderRegistration(reg) {
  rows("#registration", reg, (r) => [r.role, r.count, r.capacity ?? "∞"]);
}
function renderRoles(roles) {
  rows("#roles", roles, (r) => [r.role, r.class, r.capacity ?? "∞", r.scored,
    JSON.stringify(r.ugen_manifest), JSON.stringify(r.light_manifest)]);
}
function renderDevices(devs) {
  rows("#devices", devs, (d) => [d.dev, d.name, d.role ?? "—"]);
}
function renderStatus(status) {
  rows("#bitStatus", Object.entries(status || {}), (kv) => [kv[0], kv[1]]);
}
function populateBits(bits) {
  const sel = $("bitPicker");
  sel.innerHTML = "";
  for (const b of bits) {
    const opt = document.createElement("option");
    opt.value = b; opt.textContent = b;
    sel.appendChild(opt);
  }
}
function log(level, message) {
  const el = $("log");
  el.textContent += `[${level}] ${message}\n`;
  el.scrollTop = el.scrollHeight;
}

function handle(msg) {
  switch (msg.event) {
    case "snapshot":
      $("state").textContent = msg.state;
      $("loaded").textContent = msg.loaded_bit ?? "—";
      populateBits(msg.installed_bits);
      renderRegistration(msg.registration);
      renderRoles(msg.roles);
      renderDevices(msg.devices);
      renderStatus(msg.bit_status);
      break;
    case "state_changed":
      $("state").textContent = msg.state;
      log("info", "state → " + msg.state);
      break;
    case "registration_changed": renderRegistration(msg.roles); break;
    case "devices_changed": renderDevices(msg.devices); break;
    case "bit_status": renderStatus(msg.status); break;
    case "bit_completed": log("info", "bit completed: " + JSON.stringify(msg.result)); break;
    case "error": log("error", msg.command + ": " + msg.message); break;
    case "log": log(msg.level, msg.message); break;
  }
}

connect();
</script>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_console_static.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add console/static/index.html tests/test_console_static.py
git commit -m "feat: add self-contained Terrarium Console admin page"
```

---

### Task 8: `console/server.py` — threaded HTTP+WS server

The only socket-touching code. One port: `process_request` serves `index.html` on `GET /`, the websocket handshake proceeds on `/ws`. Handler threads only touch thread-safe queues/collections; all `GameServer` access stays on the tick thread via the `ConsoleAgent` boundary from Task 6.

**Files:**
- Create: `console/server.py`
- Create: `tests/test_console_server.py`

**Interfaces:**
- Consumes: `websockets.sync.server.serve`, `websockets.http11.Response`, `websockets.datastructures.Headers`.
- Produces `console.server.ConsoleServer(host="127.0.0.1", port=0)`:
  - `start() -> None` — bind + spawn the serve thread (non-blocking).
  - `stop() -> None` — shut the server down and join the thread.
  - `port -> int` — the bound port (useful when `port=0` picks an ephemeral one).
  - Tick-thread API matching what `ConsoleAgent` consumes: `drain_new_clients() -> list`, `drain_inbound() -> list[tuple[object, dict]]`, `send(client, msg) -> None`, `broadcast(msg) -> None`.
  - Client handles are the `websockets` `ServerConnection` objects.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_console_server.py`:

```python
import json
import time

from urllib.request import urlopen

from websockets.sync.client import connect as ws_connect

from bits.test_bit import TestBit
from console.agent import ConsoleAgent
from console.server import ConsoleServer
from control.engine import GameServer


def test_get_root_serves_index_html():
    server = ConsoleServer(port=0)
    server.start()
    try:
        body = urlopen(f"http://127.0.0.1:{server.port}/").read().decode()
        assert "Terrarium Console" in body
        assert "new WebSocket" in body
    finally:
        server.stop()


def test_client_gets_snapshot_and_command_round_trips():
    gs = GameServer({"TestBit": TestBit})
    server = ConsoleServer(port=0)
    agent = ConsoleAgent(gs, server)
    server.start()
    try:
        with ws_connect(f"ws://127.0.0.1:{server.port}/ws") as ws:
            # _recv_event drives agent.poll() until the event arrives; the
            # first poll drains the new client and sends its snapshot.
            snap = _recv_event(ws, agent, "snapshot")
            assert snap["state"] == "IDLE"
            assert snap["installed_bits"] == ["TestBit"]

            ws.send(json.dumps({"command": "load_bit", "name": "TestBit"}))
            state = _recv_event(ws, agent, "state_changed")
            assert state["state"] in ("LOADING", "LOADED", "SETUP")
    finally:
        server.stop()


def _recv_event(ws, agent, event_name, timeout=2.0):
    """Interleave agent.poll() (tick thread work) with client recv until the
    named event arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        agent.poll()
        try:
            raw = ws.recv(timeout=0.05)
        except TimeoutError:
            continue
        msg = json.loads(raw)
        if msg.get("event") == event_name:
            return msg
    raise AssertionError(f"did not receive {event_name!r} in time")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_console_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'console.server'`.

- [ ] **Step 3: Implement `ConsoleServer`**

Create `console/server.py`:

```python
"""ConsoleServer: the only socket-touching code in the console package. One
port serves the static admin page over HTTP (GET /) and upgrades websocket
clients (/ws) on the same listener. Handler threads only touch thread-safe
queues and a lock-guarded client set; every GameServer access stays on the
tick thread, which drives ConsoleAgent.poll(). Synchronous websockets API to
match this codebase's plain-tick-loop style.
"""

import json
import logging
import threading
from collections import deque
from pathlib import Path

from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.sync.server import serve

logger = logging.getLogger(__name__)

_INDEX_HTML = (Path(__file__).resolve().parent / "static" / "index.html")


class ConsoleServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._port = port
        self._server = None
        self._thread = None
        self._index_bytes = _INDEX_HTML.read_bytes()
        self._lock = threading.Lock()
        self._clients: set = set()
        self._new_clients: deque = deque()
        self._inbound: deque = deque()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._server = serve(
            self._handle, self._host, self._port,
            process_request=self._process_request)
        self._port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def port(self) -> int:
        return self._port

    # --- HTTP: serve index.html on GET / ; let /ws upgrade -----------------
    def _process_request(self, connection, request):
        if request.path == "/ws":
            return None   # proceed to the websocket handshake
        headers = Headers()
        headers["Content-Type"] = "text/html; charset=utf-8"
        headers["Content-Length"] = str(len(self._index_bytes))
        return Response(200, "OK", headers, self._index_bytes)

    # --- per-connection handler thread -------------------------------------
    def _handle(self, connection) -> None:
        with self._lock:
            self._clients.add(connection)
            self._new_clients.append(connection)
        try:
            for raw in connection:      # blocks until a message or close
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning("dropping non-JSON console frame")
                    continue
                with self._lock:
                    self._inbound.append((connection, msg))
        except Exception:
            logger.debug("console client handler ended", exc_info=True)
        finally:
            with self._lock:
                self._clients.discard(connection)

    # --- tick-thread API (consumed by ConsoleAgent) ------------------------
    def drain_new_clients(self) -> list:
        with self._lock:
            out = list(self._new_clients)
            self._new_clients.clear()
        return out

    def drain_inbound(self) -> list:
        with self._lock:
            out = list(self._inbound)
            self._inbound.clear()
        return out

    def send(self, client, msg: dict) -> None:
        try:
            client.send(json.dumps(msg))
        except Exception:
            logger.debug("console send failed; dropping client", exc_info=True)
            with self._lock:
                self._clients.discard(client)

    def broadcast(self, msg: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        payload = json.dumps(msg)
        for client in clients:
            try:
                client.send(payload)
            except Exception:
                logger.debug("console broadcast failed; dropping client",
                             exc_info=True)
                with self._lock:
                    self._clients.discard(client)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_console_server.py -v`
Expected: PASS. (If flaky on timing, the `_recv_event` interleave loop already retries within its 2s budget.)

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add console/server.py tests/test_console_server.py
git commit -m "feat: add ConsoleServer (single-port HTTP+WS admin transport)"
```

---

### Task 9: Wiring smoke test + README

Prove the three console pieces + the engine + the uplink all construct and coexist offline (the multi-observer promise), and point the README at the landed slice.

**Files:**
- Create: `tests/test_console_wiring.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything built above; `uplink.link.UplinkAgent`, `uplink.transport.FakeTransport`.

- [ ] **Step 1: Write the failing coexistence test**

Create `tests/test_console_wiring.py`:

```python
from bits.test_bit import TestBit
from console.agent import ConsoleAgent
from control.engine import GameServer
from uplink.link import UplinkAgent
from uplink.transport import FakeTransport


class _RecordingServer:
    def __init__(self):
        self.broadcasts = []
    def drain_new_clients(self): return []
    def drain_inbound(self): return []
    def send(self, client, msg): pass
    def broadcast(self, msg): self.broadcasts.append(msg)


def test_console_and_uplink_observe_the_same_engine_simultaneously():
    gs = GameServer({"TestBit": TestBit})
    transport = FakeTransport()
    transport.connect()
    uplink = UplinkAgent(gs, transport)
    console_server = _RecordingServer()
    ConsoleAgent(gs, console_server)

    gs.load_bit("TestBit")   # one transition sequence, two observers

    assert any(m.get("event") == "state_changed" for m in transport.sent)
    assert any(m.get("event") == "state_changed" for m in console_server.broadcasts)
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `python3 -m pytest tests/test_console_wiring.py -v`
Expected: PASS (the code all exists by now). If it FAILs, the multi-observer refactor (Task 4) regressed — fix before continuing.

- [ ] **Step 3: Update the README**

In `README.md`, in the "Planned layout" list, change the `uplink/` line's neighbourhood to add the console under `control/`+`bits/` context, and update the prose paragraph that begins "control/ and bits/ hold the first implementation slice". Append this sentence to that paragraph (after the sentence ending "…no O2/Arco/pyarco/fairyring dependency."):

```markdown
`console/` adds the Terrarium Console: a Bit-agnostic local admin panel
(served over a single-port HTTP+websocket) that loads/runs/aborts a Bit and
live-monitors lifecycle state, registration, devices, and per-role audio +
light manifests — the durable front-end fixture every Bit reuses. It attaches
to the same engine observer list as the uplink and runs entirely offline in
tests. See
`docs/superpowers/specs/2026-07-21-terrarium-console-design.md`.
```

Also add a `console/` line to the "Planned layout" code block, right after the `uplink/` line:

```
console/     Terrarium Console: local admin panel (HTTP+websocket)
```

- [ ] **Step 4: Run the full suite one final time**

Run: `python3 -m pytest tests -q`
Expected: PASS (all tests, no warnings regressions).

- [ ] **Step 5: Commit**

```bash
git add tests/test_console_wiring.py README.md
git commit -m "test: console+uplink coexistence; docs: point README at the console slice"
```

---

## Self-Review Notes

- **Spec coverage:** §3 package layout → Tasks 5–8; multi-observer + `light_manifest` edits (§3) → Tasks 4 & 1; `Bit.status()` + `devices_changed` seams (§4) → Tasks 2 & 4; protocol messages (§4) → Task 5; snapshot/error-routing/status-cadence (§4) → Task 6; single-port serving + binding + concurrency + failure isolation (§5) → Task 8; trusted-LAN/no-auth (§6) → Global Constraints + Task 8 default `127.0.0.1`; Lux Aeterna monitor-not-drive (§7) → `light_manifest` display only, no render code anywhere; testing (§8) → each task's tests + Task 9 coexistence. All covered.
- **Deferred correctly:** no ugen/Arco instantiation, no Lux Aeterna render loop, no auth, no persistence — none appear in any task.
- **Type consistency:** `add_observer`/`_notify`, `drain_new_clients`/`drain_inbound`/`send`/`broadcast`, `snapshot()`/`poll()`, and the `role_view`/`device_view`/`snapshot_event` shapes are used identically across Tasks 4/6/8. `parse_command` is defined once (uplink) and re-exported once (console).
```
