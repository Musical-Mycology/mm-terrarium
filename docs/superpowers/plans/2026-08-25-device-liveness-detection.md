# Device Liveness Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the "stale device entry survives an ungraceful disconnect" gap in `docs/MM_TERRARIUM.md` -- a device that crashes or walks out of range without a clean release currently sits in `DevicePool` (and, if it held a role, its registration slot) forever, on both transports.

**Architecture:** A device-initiated heartbeat riding the existing `/game/hello` verb (no new wire message). `DevicePool` tracks `last_seen` per device, updated by any inbound traffic. `GameServer.reap_stale(timeout)`, driven every tick from `DeviceLinkAgent.poll()`, removes any device silent for too long, freeing its registration slot immediately and reusing the existing closing-fade release path. Room-bound devices are explicitly excluded (separate, not-yet-designed problem). Two harness device clients (`o2_shroom.py`, `room_simulator.py`) gain the periodic hello resend; `mm-tuneshroom` is a documented cross-repo follow-up.

**Tech Stack:** Python, pytest, the existing `control`/`devicelink`/`harness` package layout. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md](../specs/2026-08-25-device-liveness-detection-design.md)

## Global Constraints

- Suite must stay fully offline throughout and finish at or above **1295 passed, 1 skipped** (`.venv/bin/python -m pytest tests -v`).
- A fresh worktree has no `.venv`: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` from the worktree root before running tests (already done in this worktree as of plan-writing time; a future worktree executing this plan needs it again).
- No new wire verbs. The heartbeat reuses `/game/hello` exactly as it exists today in `devicelink/protocol.py`.
- Room-class devices (`RoleClass.ROOM`, tracked in `GameServer.room.bound`) are never reaped by this mechanism -- `reap_stale` must skip any dev present in `self.room.bound.values()` unconditionally.
- `mm-tuneshroom` (separate repo) is out of scope. Do not touch it; the follow-up is a documentation note only (Task 7).
- Every new/changed default value: `stale_timeout = 15.0` (Control-side, `BootConfig` field), `heartbeat_interval = 5.0` (device-side, per-harness-client CLI flag, not a `BootConfig` field).
- Style: this repo's prose (docs, docstrings, comments) uses `" -- "` rather than a literal em dash character. Match it in every new comment/docstring/doc line written by this plan.

---

### Task 1: `DevicePool` gains liveness tracking

**Files:**
- Modify: `control/device_pool.py` (all 41 lines -- shown in full below)
- Test: `tests/test_device_pool.py` (extend; 4 existing tests must keep passing unmodified)

**Interfaces:**
- Produces: `DeviceInfo.last_seen: float`; `DevicePool.hello(dev, name, protoversion, now: float = 0.0) -> DeviceInfo` (now takes an optional 4th param, existing 3-arg call sites keep working); `DevicePool.touch(dev: str, now: float) -> None`; `DevicePool.stale(now: float, timeout: float) -> list[str]`; `DevicePool.remove(dev: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_device_pool.py`:

```python
def test_touch_updates_last_seen_for_a_known_device():
    pool = DevicePool()
    pool.hello("ie3", "Tuneshroom 3", "1.0", now=10.0)
    pool.touch("ie3", now=20.0)
    assert pool.get("ie3").last_seen == 20.0


def test_touch_is_a_no_op_for_an_unknown_device():
    pool = DevicePool()
    pool.touch("ie9", now=20.0)   # must not raise
    assert pool.known("ie9") is False


def test_hello_sets_last_seen():
    pool = DevicePool()
    pool.hello("ie3", "Tuneshroom 3", "1.0", now=5.0)
    assert pool.get("ie3").last_seen == 5.0


def test_stale_returns_devices_past_the_timeout():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1", now=0.0)
    pool.hello("ie2", "Shroom Two", "1", now=9.0)
    assert pool.stale(now=10.0, timeout=5.0) == ["ie1"]


def test_stale_is_a_pure_query():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1", now=0.0)
    pool.stale(now=10.0, timeout=5.0)
    assert pool.known("ie1") is True   # stale() did not remove it


def test_remove_drops_the_entry_outright():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1")
    pool.remove("ie1")
    assert pool.known("ie1") is False
    assert len(pool) == 0


def test_remove_is_a_no_op_for_an_unknown_device():
    pool = DevicePool()
    pool.remove("ie9")   # must not raise
    assert len(pool) == 0


def test_a_removed_device_can_say_hello_again_as_if_new():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1", now=0.0)
    pool.remove("ie1")
    pool.hello("ie1", "Shroom One (reconnected)", "1", now=100.0)
    assert pool.known("ie1") is True
    assert pool.get("ie1").last_seen == 100.0
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_device_pool.py -v`
Expected: the 4 pre-existing tests pass; the 8 new tests fail with `AttributeError` (no `touch`/`stale`/`remove`) or `TypeError` (no `now` kwarg on `hello`).

- [ ] **Step 3: Implement**

Replace `control/device_pool.py` in full:

```python
"""Tracks known devices across Bit lifecycles. See design spec section 4,
and docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md
sections 3-4 for last_seen/touch/stale/remove."""

from dataclasses import dataclass


@dataclass
class DeviceInfo:
    dev: str
    name: str
    protoversion: str
    # The last time ANY traffic arrived from this dev -- not just hello.
    # See devicelink/agent.py's _handle(), which touches this on every
    # inbound message, and GameServer.reap_stale(), the only reader.
    last_seen: float = 0.0


class DevicePool:
    """dev -> DeviceInfo, populated by /game/hello. Global to Control, not
    reset when a Bit unloads -- a released device stays in the joinable pool.

    A device is removed only by GameServer.reap_stale() finding it silent
    past a timeout (see the design spec above) -- there is still no
    graceful-release path that removes a DevicePool entry, exactly as
    before this liveness mechanism existed.
    """

    def __init__(self):
        self._devices: dict[str, DeviceInfo] = {}

    def hello(self, dev: str, name: str, protoversion: str,
             now: float = 0.0) -> DeviceInfo:
        info = DeviceInfo(dev=dev, name=name, protoversion=protoversion,
                          last_seen=now)
        self._devices[dev] = info
        return info

    def touch(self, dev: str, now: float) -> None:
        """Record proof of life for an already-known device. A no-op for an
        unknown dev, same tolerance known()/get() already have -- the first
        hello is what actually creates the entry, not this method."""
        info = self._devices.get(dev)
        if info is not None:
            info.last_seen = now

    def known(self, dev: str) -> bool:
        return dev in self._devices

    def get(self, dev: str) -> DeviceInfo | None:
        return self._devices.get(dev)

    def all(self) -> list[DeviceInfo]:
        """Every known device, insertion order -- the public view for the
        Terrarium Console snapshot. Returns a fresh list; mutating it does
        not affect the pool.
        """
        return list(self._devices.values())

    def stale(self, now: float, timeout: float) -> list[str]:
        """Dev ids whose last_seen is older than `timeout`. Pure query --
        mutates nothing. GameServer.reap_stale is the only caller that acts
        on the result."""
        return [dev for dev, info in self._devices.items()
                if now - info.last_seen > timeout]

    def remove(self, dev: str) -> None:
        """Drop the entry outright, not a tombstone -- a device that
        reconnects later says hello again and is indistinguishable from a
        first-time connection, which is correct: there is nothing to
        preserve about a device that was never cleanly released."""
        self._devices.pop(dev, None)

    def __len__(self) -> int:
        return len(self._devices)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_device_pool.py -v`
Expected: all 12 tests (4 original + 8 new) PASS.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `.venv/bin/python -m pytest tests -q`
Expected: at least 1295 passed, 1 skipped (this task adds 8, so 1303 passed, 1 skipped).

- [ ] **Step 6: Commit**

```bash
git add control/device_pool.py tests/test_device_pool.py
git commit -m "feat(control): DevicePool tracks last_seen and can reap stale entries"
```

---

### Task 2: `GameServer.reap_stale()`

**Files:**
- Modify: `control/engine.py:99-101` (the `hello` method) and insert a new method immediately after it (before `load_bit` at line 103)
- Test: `tests/test_engine.py` (new section at the end)

**Interfaces:**
- Consumes: `DevicePool.stale(now, timeout)`, `DevicePool.remove(dev)` (Task 1); `RegistrationState.assignments` (dict), `RegistrationState.release(dev) -> bool` (existing, unchanged); `GameServer.room.bound` (dict, existing, unchanged); `GameServer.on_release` (existing transport-owned sink, unchanged); `GameServer._notify(method)` (existing, unchanged).
- Produces: `GameServer.reap_stale(timeout: float) -> list[str]` -- returns the dev ids actually reaped (Room-bound devs are never in this list).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py` (uses `RoomCapableBit`, `Room`, `RoomType`, `RoomBindingRegistry` already imported at the top of this file):

```python
def test_reap_stale_removes_a_silent_unjoined_device():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.hello("ie1", "sim", "1")
    changes = []
    gs.add_observer(SimpleNamespace(
        on_devices_change=lambda: changes.append("devices"),
        on_registration_change=lambda: changes.append("registration")))

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert reaped == ["ie1"]
    assert [d.dev for d in gs.devices.all()] == []
    assert changes == ["devices"]   # no registration_change: it never joined


def test_reap_stale_leaves_a_fresh_device_alone():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.hello("ie1", "sim", "1")
    clk.t = 3.0
    reaped = gs.reap_stale(timeout=50.0)
    assert reaped == []
    assert gs.devices.known("ie1") is True


def test_reap_stale_frees_a_scored_roles_slot_immediately():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.load_bit("test_bit")
    gs.hello("ie1", "sim", "1")
    gs.join("ie1", "TEST_PLAYER_NODE")
    released = []
    gs.on_release = released.append
    counts_before = dict((n, c) for n, c, _ in gs.registration.counts())
    assert counts_before["player"] == 1

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert reaped == ["ie1"]
    counts_after = dict((n, c) for n, c, _ in gs.registration.counts())
    assert counts_after["player"] == 0
    assert released == ["ie1"]
    assert gs.devices.known("ie1") is False


def test_reap_stale_batches_observer_notifications_once():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.load_bit("test_bit")
    for dev in ("ie1", "ie2"):
        gs.hello(dev, "sim", "1")
        gs.join(dev, "TEST_PLAYER_NODE")
    calls = []
    gs.add_observer(SimpleNamespace(
        on_devices_change=lambda: calls.append("devices"),
        on_registration_change=lambda: calls.append("registration")))

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert sorted(reaped) == ["ie1", "ie2"]
    assert calls == ["devices", "registration"] or calls == ["registration", "devices"]
    assert len(calls) == 2   # not 2 per device


def test_reap_stale_never_reaps_a_room_bound_device():
    from control.room_binding import RoomBindingRegistry
    from control.rooms import Room, RoomType
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding,
                    clock=lambda: clk.t)
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("RoomCapableBit")
    gs.hello("sim-room", "room", "1")
    binding.arm(RoomType.TEST, "main", window_seconds=10.0)
    gs.join("sim-room", "ROOM_TEST_NODE")
    assert gs.room.bound == {"main": "sim-room"}

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert reaped == []
    assert gs.devices.known("sim-room") is True
    assert gs.room.bound == {"main": "sim-room"}
    assert "sim-room" in gs.registration.assignments


def test_reap_stale_on_release_exception_does_not_stop_the_rest():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.load_bit("test_bit")
    for dev in ("ie1", "ie2"):
        gs.hello(dev, "sim", "1")
        gs.join(dev, "TEST_PLAYER_NODE")

    def boom(dev):
        raise RuntimeError("transport exploded")

    gs.on_release = boom
    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)   # must not raise
    assert sorted(reaped) == ["ie1", "ie2"]
    assert gs.devices.known("ie1") is False
    assert gs.devices.known("ie2") is False
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine.py -k reap_stale -v`
Expected: FAIL with `AttributeError: 'GameServer' object has no attribute 'reap_stale'`.

- [ ] **Step 3: Implement**

Edit `control/engine.py`. First, `hello()` (lines 99-101) becomes:

```python
    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion, self._clock())
        self._notify("on_devices_change")
```

Then insert this new method immediately after it (still before `def load_bit`):

```python
    def reap_stale(self, timeout: float) -> list[str]:
        """Remove every DevicePool entry silent for `timeout` seconds,
        freeing any role slot it held. See docs/superpowers/specs/
        2026-08-25-device-liveness-detection-design.md sections 4-5.

        A dev currently bound to a Room fixture is left untouched
        entirely -- Room liveness is a separate, not-yet-designed
        question (section 5 of that spec): reaping it would clear
        registration.assignments but not room.bound, leaving RoomBridge
        feeding a fixture whose device no longer exists.

        Never raises: on_release is guarded exactly like _unload()
        already guards it, so a failing transport cannot wedge this call
        or strand the remaining stale devices. Notifications are batched
        once per call, not once per device, matching _unload()'s existing
        shape.
        """
        now = self._clock()
        room_devs = (set(self.room.bound.values())
                    if self.room is not None else set())
        reaped: list[str] = []
        released_any = False
        for dev in self.devices.stale(now, timeout):
            if dev in room_devs:
                continue
            if self.registration is not None and \
                    dev in self.registration.assignments:
                self.registration.release(dev)
                released_any = True
                if self.on_release:
                    try:
                        self.on_release(dev)
                    except Exception:
                        logger.exception(
                            "on_release raised for %s during reap; "
                            "continuing", dev)
            self.devices.remove(dev)
            reaped.append(dev)
        if released_any:
            self._notify("on_registration_change")
        if reaped:
            self._notify("on_devices_change")
        return reaped
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine.py -k reap_stale -v`
Expected: all 6 new tests PASS.

- [ ] **Step 5: Run the full engine test file and the full suite**

Run: `.venv/bin/python -m pytest tests/test_engine.py tests/test_engine_data.py tests/test_engine_on_join.py tests/test_engine_triggers.py tests/test_engine_bit_config.py -v`
Expected: all pass, no regressions (hello()'s signature change is caller-transparent).

Run: `.venv/bin/python -m pytest tests -q`
Expected: at least 1303 passed (Task 1's count), +6 here = 1309 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add control/engine.py tests/test_engine.py
git commit -m "feat(control): GameServer.reap_stale frees a silent device's role slot"
```

---

### Task 3: Wire the reaper and `drop_dev` into `DeviceLinkAgent`

**Files:**
- Modify: `devicelink/agent.py` (constructor ~line 76-90, `poll()` ~line 237-253, `_handle()` ~line 425-444, `_on_release()` ~line 522-543, `_finish_release()` ~line 545-556)
- Test: `tests/test_devicelink_agent.py` (new section at the end; reuses the existing `rig` fixture, `FakeServer`, `_Clock`, `_hello` helpers already in that file)

**Interfaces:**
- Consumes: `GameServer.reap_stale(timeout)` (Task 2); `GameServer.devices.touch(dev, now)` (Task 1); `server.drop_dev(dev)` (already exists on both `DeviceLinkServer` and `O2LiteTransport`, and on the test file's `FakeServer`).
- Produces: `DeviceLinkAgent.__init__(..., stale_timeout: float = 15.0)` -- one new keyword-only-by-convention parameter, appended after the existing `on_join_denied=None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_devicelink_agent.py`, using the file's existing
`_Clock`/`_agent_with_joined_device`/`FakeServer`/`_hello` helpers already
defined above in this file:

```python
def test_handle_touches_last_seen_on_every_inbound_message():
    gs, server, agent, dev, clk = _agent_with_joined_device()
    clk.advance(3.0)
    server.deliver("c1", "/game/tilt", "sf", [dev, 10.0])
    agent.poll()
    assert gs.devices.get(dev).last_seen == clk.t


def test_poll_reaps_a_device_silent_past_stale_timeout():
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert "ie1" in agent.bridges

    clk.advance(11.0)
    agent.poll()   # no inbound traffic this tick -- ie1 has gone silent

    assert gs.devices.known("ie1") is False
    counts = dict((n, c) for n, c, _ in gs.registration.counts())
    assert counts["player"] == 0
    # the closing fade started: bridge/universe stay present until it
    # finishes (see _render_frames/_check_closing_done), same as a
    # graceful release.
    assert "ie1" in agent._closing


def test_a_fresh_heartbeat_prevents_reaping():
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    clk.advance(8.0)
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()   # heartbeat lands before the 10s timeout

    clk.advance(8.0)   # 16s since join, but only 8s since the heartbeat
    agent.poll()

    assert gs.devices.known("ie1") is True
    assert "ie1" in agent.bridges


def test_finish_release_calls_drop_dev():
    gs, server, agent, dev, clk = _agent_with_joined_device()
    assert dev in server._devs
    gs.abort()          # -> _unload -> on_release -> fade -> _finish_release
    # drive the closing fade to completion
    for _ in range(250):
        agent.poll()
        clk.advance(1.0 / 44.0)
    assert dev not in server._devs


def test_on_release_with_no_bridge_calls_drop_dev():
    """Mirrors test_failing_on_grant_sends_error_not_role... -- a device
    whose on_grant failed never got a bridge, so _on_release takes the
    early-return branch, not the fade. That branch must still forget the
    transport's connection mapping."""
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("test_bit")
    _hello(server, agent, client="c1", dev="ie1")
    assert "ie1" in server._devs

    # Simulate a grant with no bridge ever created, exactly what
    # devicelink/agent.py's _on_join does on a failing on_grant: the
    # engine-level assignment exists, but self.bridges never got an entry.
    gs.join("ie1", "TEST_PLAYER_NODE")
    agent._on_release("ie1")

    assert "ie1" not in server._devs
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -k "touches_last_seen or reaps_a_device or fresh_heartbeat or calls_drop_dev" -v`
Expected: FAIL -- `stale_timeout` is not an accepted keyword yet, `last_seen` stays at its hello-time value (no touch), `drop_dev` is never called.

- [ ] **Step 3: Implement**

In `devicelink/agent.py`, the constructor signature (currently lines 77-80) becomes:

```python
    def __init__(self, game_server: GameServer, server,
                 capability=None, clock=time.monotonic,
                 room_bridge=None, room_audio=None, horizon: float = 0.0,
                 room_profile=None, on_room_frame=None, on_join_denied=None,
                 stale_timeout: float = 15.0):
```

And in the body, right after `self._horizon = horizon` (line ~90), add:

```python
        # Control-side reap threshold: a device silent this many seconds
        # is removed by GameServer.reap_stale(), called from poll() below.
        # See docs/superpowers/specs/
        # 2026-08-25-device-liveness-detection-design.md.
        self._stale_timeout = stale_timeout
```

`poll()` (lines 237-253) gains one line, right after the inbound-handling loop and before `self._feed_breath()`:

```python
    def poll(self) -> None:
        self.server.drain_new_clients()      # devices are anonymous until hello
        for client, msg in self.server.drain_inbound():
            try:
                self._handle(client, msg)
            except Exception:
                logger.exception("devicelink inbound handling failed; "
                                 "dropping frame")
        self.game_server.reap_stale(self._stale_timeout)
        self._feed_breath()
        # Before both renders: a feed released this tick must be reflected in
        # the frame rendered this tick, not the next one. Draining after would
        # delay every cue by one frame, exactly the class of error this
        # design exists to remove.
        self._drain_light_cues()
        self._render_frames()
        self._render_room()
        self._tick_audio()
```

`_handle()` (lines 425-444) gains one line, right after `dev = env.args[0]`:

```python
    def _handle(self, client, msg: dict) -> None:
        try:
            env = protocol.decode(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable device frame: %s", exc)
            return
        verb = protocol.parse_game_address(env.address)
        if verb is None:
            logger.warning("dropping non-/game address %r", env.address)
            return
        if not env.args or not isinstance(env.args[0], str):
            logger.warning("dropping /game/%s with no dev argument", verb)
            return
        dev = env.args[0]
        self.game_server.devices.touch(dev, self._clock())
        if verb == "hello":
            self._on_hello(client, dev, env.args)
        elif verb == "join":
            self._on_join(client, dev, env.args)
        else:
            self._on_verb(dev, verb, env.args, env.timestamp)
```

`_on_release()` (lines 522-543) gains one line in the no-bridge early-return branch:

```python
    def _on_release(self, dev: str) -> None:
        """Engine released dev. Kick off the closing fade -- but keep the
        device in the render maps (see _render_frames) so its bridge/session
        are still there on the next poll() to actually play the fade out and
        emit /<dev>/leds. /<dev>/release itself is deferred to
        _finish_release(), once CLOSING has actually finished.

        A device can be released with no bridge at all (e.g. its on_grant
        failed earlier -- see test_failing_on_grant_sends_error_not_role...):
        nothing to fade in that case, so release immediately -- including
        forgetting the transport's connection mapping, the same cleanup
        _finish_release does for the faded case below."""
        bridge = self.bridges.get(dev)
        if bridge is None:
            self.server.drop_dev(dev)
            try:
                self._send(dev, protocol.release_event(dev))
            except Exception:
                logger.exception("release notify for %s failed", dev)
            return
        try:
            bridge.on_release(dev)   # -> session.clear(): enqueues the fade
        except Exception:
            logger.exception("session clear for %s failed", dev)
        self._closing[dev] = 0
```

`_finish_release()` (lines 545-556) gains one line:

```python
    def _finish_release(self, dev: str) -> None:
        """The closing fade (or the stuck-session guard) is done: drop the
        device from every map and send /<dev>/release."""
        self.bridges.pop(dev, None)
        self._universes.pop(dev, None)
        self._last_frames.pop(dev, None)
        self._closing.pop(dev, None)
        self._last_breath.pop(dev, None)
        self.server.drop_dev(dev)
        try:
            self._send(dev, protocol.release_event(dev))
        except Exception:
            logger.exception("release notify for %s failed", dev)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -v`
Expected: every test in the file PASSES, including the 5 new ones (the placeholder from Step 1 must be gone -- if `grep -c "def test_handle_touches_last_seen_on_every_inbound_message" tests/test_devicelink_agent.py` prints anything other than `1`, fix that first).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: at least 1309 passed (Task 2's count) + 5 here = 1314 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(devicelink): DeviceLinkAgent reaps stale devices and forgets drop_dev connections"
```

---

### Task 4: `BootConfig.stale_timeout`, `terrarium_boot.py` wiring, and the "device timed out" log line

**Files:**
- Modify: `control/boot_config.py` (add one field after `cue_horizon`, ~line 66)
- Modify: `harness/terrarium_boot.py`:
  - `build()`'s `DeviceLinkAgent(...)` call, lines 204-207
  - `_build_arg_parser()`, add `--stale-timeout` near `--horizon` (~line 723-726)
  - `main()`'s config assignment, near `if args.arco_ready_timeout is not None:` (~line 882-883)
  - `_LifecycleLogger`'s docstring (lines 528-567) and `on_devices_change` (lines 578-582)
- Test: `tests/test_boot_config.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: `DeviceLinkAgent.__init__(..., stale_timeout=...)` (Task 3).
- Produces: `BootConfig.stale_timeout: float = 15.0`; `--stale-timeout` CLI flag on `harness/terrarium_boot.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_boot_config.py`:

```python
def test_stale_timeout_default():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.stale_timeout == 15.0
```

Append to `tests/test_terrarium_boot.py` (in the "Task 4: device lifecycle lines" section, using the `_lifecycle_rig`/`_deliver_hello`/`_deliver_join` helpers already defined there):

```python
def test_device_timed_out_line(capsys):
    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer, _Clock

    clk = _Clock()
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.add_observer(_LifecycleLogger(gs))
    _deliver_hello(server, agent, dev="ie1")
    capsys.readouterr()   # discard the hello line

    clk.advance(11.0)
    agent.poll()

    out = capsys.readouterr().out
    assert "device timed out: ie1\n" in out
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_boot_config.py tests/test_terrarium_boot.py -k "stale_timeout or timed_out" -v`
Expected: `test_stale_timeout_default` fails with `AttributeError`; `test_device_timed_out_line` fails because no such line is printed.

- [ ] **Step 3: Implement**

`control/boot_config.py`: add this field right after `cue_horizon` (end of the dataclass body, before the `array_backend_configured` property):

```python
    # Control-side reap threshold (seconds of silence before a device is
    # removed from DevicePool and, if it held one, its role slot freed).
    # Default is three missed heartbeats at the harness clients' own
    # default --heartbeat-interval (5.0s) -- the same generous-multiple
    # shape _MAX_CLOSING_FRAMES already uses relative to a session's fade
    # time. See docs/superpowers/specs/
    # 2026-08-25-device-liveness-detection-design.md.
    stale_timeout: float = 15.0
```

`harness/terrarium_boot.py`'s `build()` (lines 204-207) becomes:

```python
        agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                                room_audio=room_audio,
                                horizon=config.cue_horizon, clock=clock,
                                on_join_denied=on_join_denied,
                                stale_timeout=config.stale_timeout)
```

`_build_arg_parser()`: add this argument right after the existing `--horizon` block (after line 726, before `--transport`):

```python
    ap.add_argument("--stale-timeout", type=float, default=None,
                    help="Override BootConfig.stale_timeout (15 s). A "
                         "device silent this long -- no /game/hello, no "
                         "gesture, nothing -- is removed from DevicePool "
                         "and, if it held one, its role slot freed. Paired "
                         "with the harness device clients' own "
                         "--heartbeat-interval (default 5 s each): three "
                         "missed heartbeats before a reap.")
```

`main()`: add this right after the existing `if args.arco_ready_timeout is not None:` block:

```python
    if args.stale_timeout is not None:
        config.stale_timeout = args.stale_timeout
```

`_LifecycleLogger`'s docstring: replace this paragraph (part of the derivation list):

```
      - "device hello: <dev>" -- a dev appearing in gs.devices.all() that
        was not there the last time on_devices_change fired. DevicePool
        never drops a device (control/device_pool.py), so this set only
        grows.
```

with:

```
      - "device hello: <dev>" -- a dev appearing in gs.devices.all() that
        was not there the last time on_devices_change fired.
      - "device timed out: <dev>" -- the reverse: a dev that WAS in
        gs.devices.all() last time and is gone now. The only thing that
        removes a DevicePool entry is GameServer.reap_stale() (control/
        device_pool.py, control/engine.py), called every tick from
        DeviceLinkAgent.poll() -- so this line is unambiguous: it always
        means a device went silent past BootConfig.stale_timeout, never a
        graceful release (that only ever empties gs.registration.
        assignments, diffed separately below as "device released"). A
        timed-out player that held a role prints BOTH lines: "released"
        from the assignments diff and "timed out" from this one, which is
        accurate -- both things happened.
```

`on_devices_change` (lines 578-582) becomes:

```python
    def on_devices_change(self) -> None:
        current_devs = {info.dev for info in self._gs.devices.all()}
        for dev in current_devs - self._last_devices:
            print(f"device hello: {dev}", flush=True)
        for dev in self._last_devices - current_devs:
            print(f"device timed out: {dev}", flush=True)
        self._last_devices = current_devs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_boot_config.py tests/test_terrarium_boot.py -v`
Expected: all pass, including the 2 new tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: at least 1314 passed (Task 3's count) + 2 here = 1316 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add control/boot_config.py harness/terrarium_boot.py tests/test_boot_config.py tests/test_terrarium_boot.py
git commit -m "feat(harness): wire stale_timeout through BootConfig/CLI, log timed-out devices"
```

---

### Task 5: Heartbeat resend in `harness/o2_shroom.py`

**Files:**
- Modify: `harness/o2_shroom.py` (add a pure helper near the top-level functions, ~line 60; add a CLI flag in `main()`'s parser, ~line 349; wire the resend into `main()`'s tick loop, ~lines 500-521 and ~540-549)
- Test: `tests/test_o2_shroom.py`

**Interfaces:**
- Produces: `next_heartbeat_time(now: float, interval: float) -> float` (pure, importable, unit-testable with no o2litepy); `--heartbeat-interval` CLI flag, default `5.0`, `<= 0` disables the resend.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_o2_shroom.py`:

```python
def test_next_heartbeat_time_advances_by_the_interval():
    from harness.o2_shroom import next_heartbeat_time
    assert next_heartbeat_time(now=10.0, interval=5.0) == 15.0


def test_next_heartbeat_time_disabled_by_a_non_positive_interval():
    from harness.o2_shroom import next_heartbeat_time
    assert next_heartbeat_time(now=10.0, interval=0.0) == float("inf")
    assert next_heartbeat_time(now=10.0, interval=-1.0) == float("inf")


def test_main_resends_hello_inside_a_while_loop():
    """Source-inspection, same technique and reason as
    test_main_has_exactly_one_backend_close: main() imports o2litepy,
    absent from this offline suite by design. main() has TWO while loops
    (the clock-sync wait, then the tick loop), and ast.walk's traversal
    order across sibling subtrees is not a documented guarantee -- so
    this deliberately does not index into "the first While found".
    Instead it walks EVERY While node's own subtree for a
    send_cmd("/game/hello", ...) Call and sums across all of them: since
    the two loops' subtrees are disjoint, this is equivalent to "how many
    hello sends live inside some while loop" without needing to identify
    which loop is which. There must be at least 2 (the join-retry block's
    existing one, inside the tick loop; plus the heartbeat's own) -- the
    clock-sync loop has none. That proves the resend is wired into a loop
    body rather than only sent once at startup."""
    import ast
    import inspect

    import harness.o2_shroom

    source = inspect.getsource(harness.o2_shroom)
    tree = ast.parse(source)
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    while_nodes = [node for node in ast.walk(main)
                  if isinstance(node, ast.While)]
    assert while_nodes, "main() must have at least one while loop"

    hello_call_count = sum(
        1 for w in while_nodes for node in ast.walk(w)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_cmd"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "/game/hello")
    assert hello_call_count >= 2, (
        f"expected at least 2 /game/hello send_cmd calls inside some "
        f"while loop in main() (join-retry's existing one plus the "
        f"heartbeat's own), found {hello_call_count}")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -k "heartbeat or resends_hello" -v`
Expected: `next_heartbeat_time` tests fail with `ImportError`; the AST test fails with `assert 1 >= 2` (only the existing join-retry hello call).

- [ ] **Step 3: Implement**

Add this pure function to `harness/o2_shroom.py`, near the other pure helpers (after `tilt_sweep`, before `SWEEP_RESUME_SECONDS`, i.e. right after line 51's `return SWEEP_DEGREES * (triangle - 1.0)`):

```python
def next_heartbeat_time(now: float, interval: float) -> float:
    """The next O2 time a heartbeat /game/hello should be resent.

    interval <= 0 disables the heartbeat: returns float('inf') so a
    `now >= next_heartbeat_time(...)` check in main()'s tick loop never
    fires again, mirroring --join-retry's own "0 keeps send-once" contract.
    """
    if interval <= 0:
        return float("inf")
    return now + interval
```

In `main()`'s argument parser, add this argument right after `--join-retry` (after its closing `")` around line 361):

```python
    parser.add_argument("--heartbeat-interval", type=float, default=5.0,
                        help="Resend /game/hello every N seconds while "
                             "connected, so Control's GameServer.reap_stale "
                             "does not time this device out for going "
                             "quiet between gestures. 0 disables the "
                             "resend (pre-liveness-detection behavior). "
                             "Applies with or without --no-join: a Room "
                             "device needs it too, even though "
                             "reap_stale() never actually reaps a "
                             "Room-bound dev today.")
```

In `main()`'s body, right after `start = o2lite.time_get()` (line 500), add:

```python
        next_heartbeat = next_heartbeat_time(start, args.heartbeat_interval)
```

Then, inside the `while not client.released:` loop, right after the existing `next_join` block (after line 549's closing of that `if`/`else`, before the `if not deny_printed` check at line 550), add:

```python
            if now >= next_heartbeat:
                o2lite.send_cmd("/game/hello", 0, "s", args.dev)
                next_heartbeat = next_heartbeat_time(now, args.heartbeat_interval)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -v`
Expected: every test in the file PASSES.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: at least 1316 passed (Task 4's count) + 3 here = 1319 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "feat(harness): o2_shroom resends /game/hello as a liveness heartbeat"
```

---

### Task 6: Heartbeat resend in `harness/room_simulator.py`

**Files:**
- Modify: `harness/room_simulator.py` (add a CLI flag in `main()`'s parser, ~line 116; add a `pump_heartbeat` coroutine and wire it into `asyncio.gather`, ~lines 164-172)
- Test: `tests/test_room_simulator.py`

**Interfaces:**
- Consumes: `ShroomClient.hello() -> dict` (existing, unchanged).
- Produces: `--heartbeat-interval` CLI flag on `harness/room_simulator.py`, same default and disable-at-0 semantics as Task 5's.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_room_simulator.py`:

```python
def test_main_has_a_heartbeat_interval_flag_wired_to_a_pump():
    """room_simulator.py's main() is asyncio + a real websocket connect,
    same untestable-end-to-end shape as o2_shroom.py's main() (see that
    module's test_main_has_exactly_one_backend_close for the precedent).
    Source-inspection: assert the CLI flag exists AND that main()'s run()
    gathers a coroutine call named pump_heartbeat alongside the existing
    pump_down/pump_tick, proving the resend is actually wired into the
    connection rather than just parsed and discarded."""
    import ast
    import inspect

    import harness.room_simulator

    source = inspect.getsource(harness.room_simulator)
    tree = ast.parse(source)
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")

    add_argument_flags = [
        node.args[0].value for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args and isinstance(node.args[0], ast.Constant)
    ]
    assert "--heartbeat-interval" in add_argument_flags

    gather_calls = [
        node for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "gather"
    ]
    assert gather_calls, "main() must still gather its pump coroutines"
    gathered_names = [
        arg.func.id for call in gather_calls for arg in call.args
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
    ]
    assert "pump_heartbeat" in gathered_names
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py -k heartbeat -v`
Expected: FAIL -- neither the flag nor `pump_heartbeat` exist yet.

- [ ] **Step 3: Implement**

In `harness/room_simulator.py`'s `main()` argument parser, add this right after `--control-horizon`'s block (after line 125, before `--samples-out`):

```python
    parser.add_argument("--heartbeat-interval", type=float, default=5.0,
                        help="Resend /game/hello every N seconds while "
                             "connected, so Control's GameServer.reap_stale "
                             "does not time this device out. Same flag and "
                             "meaning as harness/o2_shroom.py's. 0 disables "
                             "the resend.")
```

Replace the `run()` function (lines 164-172):

```python
    async def run() -> None:
        async with websockets.connect(args.server) as ws:
            await ws.send(json.dumps(client.hello()))

            async def pump_down() -> None:
                async for raw in ws:
                    client.handle(json.loads(raw))

            await asyncio.gather(pump_down(), pump_tick(client))
```

with:

```python
    async def run() -> None:
        async with websockets.connect(args.server) as ws:
            await ws.send(json.dumps(client.hello()))

            async def pump_down() -> None:
                async for raw in ws:
                    client.handle(json.loads(raw))

            async def pump_heartbeat() -> None:
                """Resend /game/hello on a timer so Control's
                GameServer.reap_stale (docs/superpowers/specs/
                2026-08-25-device-liveness-detection-design.md) never
                times this Room device out for going quiet -- it only
                ever sends hello, never join, so it has no gesture
                traffic of its own to prove it is still alive."""
                if args.heartbeat_interval <= 0:
                    return
                while not client.released:
                    await asyncio.sleep(args.heartbeat_interval)
                    if not client.released:
                        await ws.send(json.dumps(client.hello()))

            await asyncio.gather(pump_down(), pump_tick(client),
                                 pump_heartbeat())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py -v`
Expected: every test in the file PASSES.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: at least 1319 passed (Task 5's count) + 1 here = 1320 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add harness/room_simulator.py tests/test_room_simulator.py
git commit -m "feat(harness): room_simulator resends /game/hello as a liveness heartbeat"
```

---

### Task 7: Close the deep-dive entry and note the `mm-tuneshroom` follow-up

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (the "Not yet built / deferred" entry at ~line 2080-2086, and add a new "Landed subsystems" entry)

No tests -- this is prose, reviewed not tested, same as every other closed entry in this file (see e.g. the cue_horizon and clock-sync closures already in the doc).

- [ ] **Step 1: Strike the deferred entry**

In `docs/MM_TERRARIUM.md`, find:

```
- **A stale device entry survives an ungraceful disconnect**, and this
  architecture cannot fix it as-is. Control is an o2lite **client**, not the O2
  host, so devices connect to Arco and Control never holds a socket to one.
  o2litepy exposes no per-peer liveness at all: its API is `set_services`,
  `bridge_id` (Control's own link to the host) and `tcp_close`. Closing this
  needs an application heartbeat or a registration expiry, which is a design
  question rather than a bug fix.
```

Replace with:

```
- ~~**A stale device entry survives an ungraceful disconnect.**~~ **Closed
  2026-08-25.** A device-initiated heartbeat riding the existing
  `/game/hello` verb (no new wire message): `DevicePool` tracks
  `last_seen` off every inbound message, `GameServer.reap_stale()` runs
  every tick from `DeviceLinkAgent.poll()` and removes any device silent
  past `BootConfig.stale_timeout` (default 15s), freeing its role slot
  immediately and reusing the existing closing-fade release path. One
  mechanism for both transports, not two: reading `devicelink/server.py`
  during this design found that the websocket transport did not actually
  propagate a disconnect into engine state either -- `drop_dev()` was
  defined on both transports and called from neither, which this slice
  also fixed. `harness/o2_shroom.py` and `harness/room_simulator.py`
  resend hello on a timer (`--heartbeat-interval`, default 5s); the real
  `mm-tuneshroom` Dart client needs the same change and does not have it
  yet -- a cross-repo follow-up, not a gap in this repo. Room-class
  devices are explicitly excluded from reaping (Room liveness is a
  separate, not-yet-designed question -- see `RoomBindingRegistry.save()/
  load()` a few entries below, which is the same kind of open question).
  See `docs/superpowers/specs/
  2026-08-25-device-liveness-detection-design.md`.
```

- [ ] **Step 2: Add a Landed subsystems entry**

In the `## Landed subsystems` section, after the `devicelink/o2_transport.py` subsection (which ends around the `harness/sync_bench.py` paragraph, before the next `###` heading), add:

```
### `control/device_pool.py`, `control/engine.py`'s `reap_stale`, and the harness heartbeat clients -- device liveness detection
Closes the "stale device entry survives an ungraceful disconnect" gap.
Design: [`.../2026-08-25-device-liveness-detection-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md).

- **`DevicePool`** gained `last_seen` per device, updated by
  `DeviceLinkAgent._handle()` on every inbound message (not just hello --
  a device mid-gesture-stream is obviously alive) plus `touch()`/
  `stale()`/`remove()`. `stale()` is a pure query; nothing removes an
  entry except the reaper below.
- **`GameServer.reap_stale(timeout)`**, called every tick from
  `DeviceLinkAgent.poll()` (the one loop that already runs unconditionally
  across every engine state, including the SETUP-hold wait). A stale
  device that held a role has its slot freed synchronously via
  `RegistrationState.release()` before the existing `on_release` sink
  fires -- a new player can join the freed slot immediately, without
  waiting for the departed device's closing fade to finish playing out.
  Room-bound devices are skipped entirely: `RoomBridge`/`AudioBridge`
  keep feeding whatever fixture-to-dev binding `RoomBindingRegistry` still
  holds, which is deliberate -- see *Not yet built* below.
- **`drop_dev()`**, defined on both `DeviceLinkServer` and
  `O2LiteTransport` since PR #24 (o2lite) but called from **nowhere**
  until now -- not even by a graceful Bit-unload release -- is now wired
  into both `_finish_release` (the faded-release path) and `_on_release`'s
  no-bridge early return (the immediate-release path, e.g. a device whose
  `on_grant` failed).
- **The heartbeat itself is `/game/hello`, resent, not a new verb.**
  `harness/o2_shroom.py --heartbeat-interval` (default 5s; 0 disables) and
  `harness/room_simulator.py --heartbeat-interval` both gained the resend.
  `mm-tuneshroom`'s Dart client has not, yet -- the real-hardware path
  stays open until that cross-repo change lands, same relationship
  `devicelink/protocol.py`'s docstring already documents for its Dart
  counterpart contract.
- `harness/terrarium_boot.py`'s `_LifecycleLogger` gained a "device timed
  out: `<dev>`" line, unambiguous by construction: `reap_stale` is the
  only thing that ever removes a `DevicePool` entry, so a dev leaving
  `gs.devices.all()` between ticks can only mean this.
```

- [ ] **Step 3: Commit**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): close the stale-device deep-dive entry"
```

---

## Final Verification

- [ ] **Run the complete suite one more time**

Run: `.venv/bin/python -m pytest tests -v`
Expected: **1320 passed, 1 skipped** (1295 baseline + 8 [Task 1] + 6 [Task 2] + 5 [Task 3] + 2 [Task 4] + 3 [Task 5] + 1 [Task 6] = 1320), fully offline, no new skips or xfails introduced.

- [ ] **Review the full diff against the spec's success criteria (section 10)**

```bash
git log --oneline main..HEAD
git diff main --stat
```
Walk each of the 7 numbered success criteria in `docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md` section 10 against the actual diff before calling this done.
