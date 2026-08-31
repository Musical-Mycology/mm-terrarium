# Bit-cycle recycle and persistent Testshrooms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the console `load_bit` overrides bug, make every Bit close recycle the Room (fresh Arco per round), and give Testshrooms a `--persist` lobby mode so they join Bits loaded later from the Console.

**Architecture:** Three independent seams. (1) A pure JS fix in `console/static/bit.js` so the console sends real nested overrides. (2) A `Terrarium.recycle_room()` plus a harness-level `_recycle_room()` wrapper that stops Control's own Arco clients (o2lite transport, `ArcoSynthPool`) before the unload and restarts them after the load, called from the serve-round loop at every round end. (3) An outer lobby loop in `harness/o2_shroom.py` gated on `--persist`, backed by a new `ShroomClient.reset_for_lobby()`.

**Tech Stack:** Python 3 (offline pytest suite via `.venv/bin/python -m pytest tests`), vanilla JS console, o2litepy/pyarco touched only behind fakes.

**Spec:** `docs/superpowers/specs/2026-08-31-bit-cycle-recycle-and-persistent-testshrooms-design.md`

## Global Constraints

- The whole test suite stays fully offline: no O2 network, no Arco server, no pyarco import at test time. Every new seam gets a fake.
- Run tests through the project venv only: `.venv/bin/python -m pytest tests -q` (bare `python3` produces a phantom failure; a fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`).
- Never `import o2litepy` or `pyarco` at module level in `devicelink/` or `control/`; lazy imports in `harness/` only.
- `Terrarium.load_room`/`unload_room`/`recycle_room` never raise to their caller; they return `None` or a reason string.
- Existing behavior without new flags must stay byte-identical (`--persist` off, non-serve mode unchanged).
- Test baseline before this plan: 1737 passed, 1 skipped (measured 2026-08-31 after merging origin/main's instrument-scripted-functions work into this branch; keep it green after every task).

---

### Task 1: Console overrides fix (spec section 1)

**Files:**
- Modify: `console/static/bit.js:248-258` (`overridesFromPairs`) and its caller at `console/static/bit.js:280-284`
- Test: `tests/test_console_agent.py` (append)

**Interfaces:**
- Consumes: `uplink/protocol.py`'s `LoadBitCommand(name, overrides: dict | None)`; `console/agent.py:138-150` already passes `command.overrides` to `registry.resolve_config`.
- Produces: wire messages `{"command": "load_bit", "name": ..., "overrides": <nested dict or null>}`.

- [ ] **Step 1: Write the failing agent-side tests** (these pass a correctly shaped overrides dict end-to-end, pinning the wire contract the JS fix must emit; they fail today only if the registry path mishandles nested overrides or null, so run them first to find out which already pass)

Find the existing ConsoleAgent test setup in `tests/test_console_agent.py` (it builds a `GameServer`, a fake server with `deliver`, and a `ConsoleAgent`; reuse the registry-wired fixture that `bits_listed` tests use). Append:

```python
def test_load_bit_with_nested_overrides_resolves_and_loads(...existing fixture args...):
    # registry-wired agent, TestBit available
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "overrides": {"defaults": {"run_duration_seconds": 9.0}}})
    agent.poll()
    assert gs.bit_name == "TestBit"
    assert gs.bit.config.extras["run_duration_seconds"] == 9.0

def test_load_bit_with_null_overrides_loads(...):
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "overrides": None})
    agent.poll()
    assert gs.bit_name == "TestBit"

def test_load_bit_with_wrapper_table_is_refused_with_located_error(...):
    # the exact broken shape the old JS sent; pins the server-side error
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "overrides": {"table": {}}})
    agent.poll()
    sent = srv.sent_to("c1")
    assert any(e.get("event") == "error" and "unknown override table" in e["message"]
               for e in sent)
    assert gs.bit_name is None
```

Match the fixture/helper names actually present in the file; `srv.sent_to` may be spelled differently there. Adapt, don't invent.

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -q -k overrides`
Expected: the first two likely PASS already (server side was always correct), the third may PASS too. If all three pass, they are still worth keeping as pins; continue.

- [ ] **Step 3: Fix `overridesFromPairs` in `console/static/bit.js`**

Replace lines 248-258 with:

```javascript
// Build the nested overrides dict merge_overrides expects:
// "rhythm.bpm" -> {rhythm: {bpm: 100}}. Returns {overrides} (null when the
// form is empty) or {error} naming the first malformed key. A key with no
// dot is refused here: every override lives in a table, and the old
// {table: {...}} wrapper this replaces made EVERY console load fail with
// "[table] unknown override table" (live log 2026-08-31 11:39:25).
function overridesFromPairs(pairs) {
  const overrides = {};
  let any = false;
  for (const [keyInput, valInput] of pairs) {
    const key = (keyInput.value || "").trim();
    if (!key) continue;
    const dot = key.indexOf(".");
    if (dot <= 0 || dot === key.length - 1) return { error: key };
    const table = key.slice(0, dot);
    const field = key.slice(dot + 1);
    const raw = valInput.value;
    const num = Number(raw);
    if (!(table in overrides)) overrides[table] = {};
    overrides[table][field] = raw !== "" && Number.isFinite(num) ? num : raw;
    any = true;
  }
  return { overrides: any ? overrides : null };
}
```

And the caller (`loadBtn.onclick`):

```javascript
  loadBtn.onclick = () => {
    const result = overridesFromPairs(pairs);
    if (result.error !== undefined) {
      flashError(`override key must be table.key (got "${result.error}")`);
      return;
    }
    wire.send("load_bit", { name: bitRow.name, overrides: result.overrides }, loadBtn);
    closeOverlay();
  };
```

`flashError` is whatever inline error affordance `bit.js`/`shell.js` already has (rooms.js flashes refusal reasons; reuse that helper's actual name; if none is importable here, set the button's title and add a `.err` class the same way the file handles wire errors).

- [ ] **Step 4: Run the console-adjacent suite**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_console_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add console/static/bit.js tests/test_console_agent.py
git commit -m "fix(console): send real nested load_bit overrides, not a {table:...} wrapper"
```

---

### Task 2: `ArcoSynthPool.quiesce()` and pool reachability (spec section 2)

**Files:**
- Modify: `harness/arco_synth.py` (class `ArcoSynthPool`, after `shutdown()` at :140-151)
- Modify: `control/audio.py:126` region (class `AudioBridge`)
- Modify: `devicelink/agent.py` (class `DeviceLinkAgent`, near the `canvas_urls`-style accessors)
- Test: `tests/test_arco_synth.py` (append; create if absent, checking first with Glob), `tests/test_audio.py` or wherever `AudioBridge` is tested (Grep for `AudioBridge(` under tests/)

**Interfaces:**
- Produces: `ArcoSynthPool.quiesce() -> None` (drop dead-hub handles, NO wire traffic, pool returns to pre-`start()` state and `start()` can run again); `AudioBridge.pool` read-only property returning the injected pool; `DeviceLinkAgent.room_audio` read-only property returning the injected `AudioBridge` (or None).
- Consumed by: Task 4's `_recycle_room`.

- [ ] **Step 1: Write the failing tests**

```python
def test_quiesce_drops_handles_without_wire_traffic():
    pool = ArcoSynthPool()
    # Simulate a started pool whose hub has died: hand-set the private
    # handles with recording fakes, exactly what start() would have set.
    class DeadFlsyn:
        def __getattr__(self, name):
            raise AssertionError(f"quiesce touched the wire: {name}")
    class DeadArco:
        def __getattr__(self, name):
            raise AssertionError(f"quiesce touched the wire: {name}")
    pool._flsyn = DeadFlsyn()
    pool._arco = DeadArco()
    pool._sched = object()
    pool._free = []          # all voices out
    pool.quiesce()
    assert pool._flsyn is None and pool._arco is None and pool._sched is None
    assert pool._free == list(range(16))

def test_audio_bridge_exposes_pool():
    pool = FakePool()        # the existing test fake, reuse it
    bridge = AudioBridge(pool)
    assert bridge.pool is pool
```

Plus a `DeviceLinkAgent.room_audio` assertion appended to an existing agent-construction test (Grep `room_audio=` under tests/ for the fixture that already injects one):

```python
    assert agent.room_audio is bridge
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests -q -k "quiesce or exposes_pool or room_audio"`
Expected: FAIL (`AttributeError: quiesce` / no `pool` property).

- [ ] **Step 3: Implement**

`harness/arco_synth.py`, after `shutdown()`:

```python
    def quiesce(self) -> None:
        """Drop every handle to a hub that is already dead, with NO wire
        traffic -- the room-recycle counterpart of shutdown(). shutdown()
        speaks to the server (alloff per channel, arco.finish()); quiesce()
        must not, because the recycle calls it precisely when Arco is about
        to be (or already is) SIGTERMed and every ugen dies with it. Resets
        the pool to its pre-start() state so start() can run again against
        the next Arco."""
        self._flsyn = None
        self._arco = None
        self._sched = None
        self._free = list(range(self._max_channels))
```

`__init__` currently builds `self._free = list(range(max_channels))` but does not keep `max_channels`; add `self._max_channels = max_channels` there.

Note: `arco.finish()` is deliberately NOT called in quiesce even though `_arco` is live-ish; pyarco's `finish()` writes to the connection. If review finds `finish()` is safe on a dead hub, keep quiesce as specified anyway: the spec's contract is no wire traffic.

`control/audio.py`, inside `AudioBridge`:

```python
    @property
    def pool(self):
        """The injected voice pool -- the room-recycle path
        (harness/terrarium_boot.py's _recycle_room) needs to quiesce and
        restart it across an Arco replacement, and reaching into _pool from
        the harness would couple it to a private."""
        return self._pool
```

`devicelink/agent.py`, inside `DeviceLinkAgent`:

```python
    @property
    def room_audio(self):
        """The injected AudioBridge (None when audio is off) -- see
        AudioBridge.pool for who needs it and why."""
        return self._room_audio
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests -q -k "quiesce or exposes_pool or room_audio or arco_synth or audio"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/arco_synth.py control/audio.py devicelink/agent.py tests/
git commit -m "feat(audio): ArcoSynthPool.quiesce() and pool/room_audio accessors for room recycle"
```

---

### Task 3: `Terrarium.recycle_room()` (spec section 2)

**Files:**
- Modify: `control/terrarium.py` (after `unload_room`, :363-389)
- Test: `tests/test_terrarium.py` (append)

**Interfaces:**
- Consumes: existing `unload_room(force=True)` and `load_room(name)`.
- Produces: `Terrarium.recycle_room() -> str | None` (None on success; reason string on refusal/failure; never raises). On success `terrarium.arco` is a fresh instance and `terrarium.room_stack` a fresh stack.

- [ ] **Step 1: Write the failing tests** (reuse `make_cycle_terrarium` from `tests/test_terrarium_cycle.py` or the fixtures `tests/test_terrarium.py` already has; prefer this file's own fixtures)

```python
def test_recycle_room_refuses_outside_room_ready():
    terr = make_terrarium()          # this file's existing helper
    reason = terr.recycle_room()
    assert reason is not None and "no_room" in reason

def test_recycle_room_replaces_arco_and_room_stack():
    terr = make_terrarium()
    assert terr.load_room("TEST") is None
    first_arco = terr.arco
    first_stack = terr.room_stack
    assert terr.recycle_room() is None
    assert terr.state is TerrariumState.ROOM_READY
    assert terr.arco is not first_arco
    assert terr.room_stack is not first_stack
    assert terr.room.name == "TEST"

def test_recycle_room_aborts_a_live_bit_first():
    terr = make_terrarium()
    assert terr.load_room("TEST") is None
    terr.gs.load_bit("TestBit")
    terr.gs.run()
    assert terr.recycle_room() is None
    assert terr.gs.state is State.IDLE

def test_recycle_room_load_failure_reports_and_lands_no_room(monkeypatch):
    terr = make_terrarium()
    assert terr.load_room("TEST") is None
    original = terr.load_room
    monkeypatch.setattr(terr, "load_room",
                        lambda name: "boom: injected load failure")
    reason = terr.recycle_room()
    assert reason == "boom: injected load failure"
    assert terr.state is TerrariumState.NO_ROOM
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests/test_terrarium.py -q -k recycle`
Expected: FAIL with `AttributeError: recycle_room`.

- [ ] **Step 3: Implement** in `control/terrarium.py`:

```python
    def recycle_room(self) -> str | None:
        """Unload and immediately reload the active Room, so the next Bit
        round starts against a fresh Arco (and fresh fixture simulators).
        The bit-cycle rule (design spec 2026-08-31): a long-lived Arco is
        known-broken for round two upstream -- only the first client after
        an Arco start gets working audio -- so closing a Bit recycles the
        whole room rather than reusing its hub.

        Returns None on success, else a reason string; never raises
        (load_room/unload_room's shared convention). A live Bit is aborted
        by the unload half (force=True). A failed reload leaves NO_ROOM
        with the reason returned -- same recovery position as any failed
        load_room: the next load_room (Console or harness) starts fresh.

        Callers that hold their own clients of the dying hub (the o2lite
        transport, ArcoSynthPool) must stop them BEFORE calling this and
        restart them after -- see harness/terrarium_boot.py's
        _recycle_room, the one production call site."""
        if self.state != TerrariumState.ROOM_READY:
            return (f"cannot recycle: Terrarium is {self.state.value}, "
                    "not room_ready")
        name = self.room.name
        reason = self.unload_room(force=True)
        if reason is not None:
            return reason
        return self.load_room(name)
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_terrarium.py tests/test_terrarium_cycle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control/terrarium.py tests/test_terrarium.py
git commit -m "feat(terrarium): recycle_room() for the bit-cycle fresh-Arco rule"
```

---

### Task 4: harness `_recycle_room()` with client stop/restart ordering (spec section 2, o2lite consequence)

**Files:**
- Modify: `harness/terrarium_boot.py` (new function, place it next to `_register_o2lite_transport` at :916)
- Test: `tests/test_terrarium_boot.py` (append)

**Interfaces:**
- Consumes: `Terrarium.recycle_room()` (Task 3), `ArcoSynthPool.quiesce()`/`start()` (Task 2), `O2LiteTransport.stop()`/`start(o2lite)` (`devicelink/o2_transport.py:310,429`).
- Produces: `_recycle_room(terrarium, *, transport=None, pool=None, o2lite=None) -> str | None`.

- [ ] **Step 1: Write the failing ordering test**

```python
def test_recycle_room_orders_client_stops_before_unload_and_restarts_after():
    calls = []
    class FakeTerrarium:
        room = types.SimpleNamespace(name="TEST")
        def recycle_room(self):
            calls.append("recycle")
            return None
    class FakeTransport:
        def stop(self): calls.append("transport-stop")
        def start(self, o2): calls.append(("transport-start", o2))
    class FakePool:
        def quiesce(self): calls.append("pool-quiesce")
        def start(self): calls.append("pool-start")
    o2 = object()
    reason = terrarium_boot._recycle_room(
        FakeTerrarium(), transport=FakeTransport(), pool=FakePool(), o2lite=o2)
    assert reason is None
    assert calls == ["transport-stop", "pool-quiesce", "recycle",
                     "pool-start", ("transport-start", o2)]

def test_recycle_room_websocket_mode_skips_transport():
    calls = []
    class FakeTerrarium:
        room = types.SimpleNamespace(name="TEST")
        def recycle_room(self):
            calls.append("recycle"); return None
    class FakePool:
        def quiesce(self): calls.append("pool-quiesce")
        def start(self): calls.append("pool-start")
    assert terrarium_boot._recycle_room(FakeTerrarium(), pool=FakePool()) is None
    assert calls == ["pool-quiesce", "recycle", "pool-start"]

def test_recycle_room_failure_skips_restarts_and_returns_reason():
    calls = []
    class FakeTerrarium:
        room = types.SimpleNamespace(name="TEST")
        def recycle_room(self):
            return "arco failed to start: injected"
    class FakePool:
        def quiesce(self): calls.append("pool-quiesce")
        def start(self): calls.append("pool-start")
    reason = terrarium_boot._recycle_room(FakeTerrarium(), pool=FakePool())
    assert reason == "arco failed to start: injected"
    assert "pool-start" not in calls
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -k _recycle or -k recycle_room_orders` (use `-k "recycle_room_orders or websocket_mode_skips or skips_restarts"`)
Expected: FAIL with `AttributeError: _recycle_room`.

- [ ] **Step 3: Implement** in `harness/terrarium_boot.py`:

```python
def _recycle_room(terrarium, *, transport=None, pool=None, o2lite=None):
    """Recycle the active Room with Control's own Arco clients handled in
    the only survivable order. Control is a client of the hub it is about
    to kill, twice over in o2lite mode: the O2LiteTransport (game/actl on
    pyarco's o2lite singleton) and the ArcoSynthPool (a Flsyn and voices
    on the dying Arco). Client-before-hub (control/teardown.py's
    invariant) demands both stop BEFORE the unload; the relaunch mirrors
    process launch order -- pool.start() first (arco.initialize() blocks
    until clock sync with the NEW hub), then transport.start(o2lite)
    (which asserts a synced clock and re-claims actl,game).

    Returns None on success, else the reason string (never raises). On
    failure the restarts are skipped: there is no hub to restart against,
    and the caller (the serve-round loop) treats the reason like a
    Console unload_room -- back to the NO_ROOM wait.

    Websocket mode passes transport=None (the devicelink server is
    process-scoped, not an Arco client); pool applies in both modes
    (audio is unconditionally on)."""
    if transport is not None:
        transport.stop()
    if pool is not None:
        pool.quiesce()
    reason = terrarium.recycle_room()
    if reason is not None:
        return reason
    if pool is not None:
        pool.start()
    if transport is not None:
        transport.start(o2lite)
    return None
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "feat(harness): _recycle_room with client-before-hub stop/restart ordering"
```

---

### Task 5: Wire the recycle into every round end (spec section 2, behavior)

**Files:**
- Modify: `harness/terrarium_boot.py`: `_serve_rounds` (:609-680), `_serve_roomless` (:705-727), main()'s round-1 paths (:1387-1413)
- Test: `tests/test_terrarium_boot.py` (append)

**Interfaces:**
- Consumes: `_recycle_room` (Task 4).
- Produces: `_serve_rounds(gs, agent, arco, *, parent_pid=None, console_agent=None, drain_arco=None, terrarium=None, recycle=None)` where `recycle` is a zero-arg callable returning `str | None`; `_serve_roomless(..., recycle=None)` threads it through. main() builds `recycle = lambda: _recycle_room(terrarium, transport=transport, pool=pool, o2lite=o2lite_module)`.

- [ ] **Step 1: Write the failing tests**

`_serve_rounds` is already driven by fakes in `tests/test_terrarium_boot.py` (Grep `_serve_rounds` there and reuse that harness). Add:

```python
def test_serve_rounds_recycles_after_a_completed_round(...):
    recycles = []
    # fake gs scripted: IDLE -> (console load during _wait_for_load) ->
    # SETUP -> RUNNING -> IDLE; reuse the file's existing scripted-gs
    # pattern for _serve_rounds tests.
    ...
    _serve_rounds(gs, agent, arco, console_agent=console,
                  terrarium=fake_terrarium,
                  recycle=lambda: recycles.append("r") or None)
    assert recycles == ["r"]          # one recycle per finished round

def test_serve_rounds_recycles_after_timeout_abort(...):
    # scripted so _wait_in_setup yields "timeout-abort"
    ...
    assert recycles == ["r"]

def test_serve_rounds_recycle_failure_returns_no_room(...):
    reason = _serve_rounds(..., recycle=lambda: "injected failure")
    assert reason == "no-room"

def test_serve_rounds_rereads_arco_after_recycle(...):
    # fake terrarium whose .arco is swapped by the recycle callable; the
    # scripted arco liveness check after the recycle must hit the NEW
    # object (record poll() calls on both).
    ...
```

- [ ] **Step 2: Run, expect failure** (`recycle` keyword unknown)

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -k serve_rounds`

- [ ] **Step 3: Implement**

In `_serve_rounds`, add the keyword and a small local:

```python
def _serve_rounds(gs, agent, arco, *, parent_pid=None, console_agent=None,
                  drain_arco=None, terrarium=None, recycle=None) -> str:
    ...
    def _end_round() -> str | None:
        """Recycle the room at every round end (bit-cycle rule). Returns
        "no-room" to bubble as this loop's outcome when the recycle
        fails, else None. Re-reads terrarium.arco: the recycle replaced
        the process, and the liveness checks above must watch the new
        one, not a handle to the SIGTERMed old one."""
        nonlocal arco
        if recycle is None:
            return None
        reason = recycle()
        if reason is not None:
            print(f"room recycle failed: {reason}", file=sys.stderr)
            return "no-room"
        if terrarium is not None and terrarium.arco is not None:
            arco = terrarium.arco
        return None
```

Call sites inside the loop:
- the `timeout-abort` branch (currently `gs.abort(); continue`) becomes `gs.abort()`, then `outcome = _end_round()`, `if outcome: return outcome`, `continue`.
- after `_serve_until_done` returns (the tail at :673-677): when the reason is neither "parent-gone" nor "arco-exited" (i.e. "completed"), run the same `_end_round()` dance before `print("round complete; waiting for next load")`.

In `_serve_roomless`, accept `recycle=None` and pass it to its inner `_serve_rounds` call (:723-725). NOTE: `_serve_roomless` re-reads `terrarium.arco` each lap already; no other change.

In `main()`:
- in the o2lite branch, keep a reference to the imported module (`o2lite_module = o2lite`) and to the pool: after `build()` returns, `pool = agent.room_audio.pool if agent.room_audio is not None else None` (Task 2's accessors).
- build the callable once: `recycle = (lambda: _recycle_room(terrarium, transport=transport, pool=pool, o2lite=o2lite_module if transport is not None else None)) if effective_serve else None`. One-shot mode passes None: non-serve behavior is unchanged by Global Constraints.
- pass `recycle=recycle` to BOTH `_serve_rounds` call sites (:1392, :1409) and to `_serve_roomless` (:1420).
- round 1's own end: at :1407, before entering `_serve_rounds` after `reason == "completed"`, and at :1390-1394 after the round-1 `gs.abort()` for "timeout-abort", run the same recycle (call `recycle()` and on a non-None reason print `room recycle failed: ...` and skip straight to `_serve_roomless`-style handling by letting `_serve_rounds` return "no-room" naturally -- simplest: hoist round-1's end into `_serve_rounds` semantics by calling `recycle()` and, if it fails, printing and falling through to `_print_round_outcome("no-room")`).

Also update `_serve_rounds`'s docstring step list (:611-637) to mention the recycle as step 6.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: baseline count + new tests, all green.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "feat(harness): recycle the room at every serve-round end (fresh Arco per bit)"
```

---

### Task 6: Round-ended visibility (spec section 4)

**Files:**
- Modify: `harness/markers.py` (add `CONTROL_ROUND_ENDED = "round ended:"` next to `CONTROL_ROUND_LOADED` at :43, and its `"CONTROL_ROUND_ENDED"` entry in the dict at :95)
- Modify: `console/agent.py` (new method on `ConsoleAgent`)
- Modify: `harness/terrarium_boot.py` (`_serve_rounds`)
- Test: `tests/test_console_agent.py`, `tests/test_terrarium_boot.py` (append)

**Interfaces:**
- Produces: `ConsoleAgent.announce_round_ended(bit_name: str, reason: str) -> None` broadcasting `protocol.log_event("info", f"round ended: {bit_name} ({reason})")` (`console/protocol.py:124`'s existing builder; the console event log already renders `log` events).
- `_serve_rounds` prints `f"{markers.CONTROL_ROUND_ENDED} {bit_name} ({reason})"` on stdout at every round end and calls `console_agent.announce_round_ended(...)` when a console is wired.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_console_agent.py
def test_announce_round_ended_broadcasts_a_log_event(...):
    agent.announce_round_ended("TestBit", "timeout-abort (0 scored joined)")
    assert any(e.get("event") == "log"
               and "round ended: TestBit (timeout-abort (0 scored joined))" in e["message"]
               for e in srv.broadcasts)   # match the fake server's actual field

# tests/test_terrarium_boot.py -- extend the Task 5 completed-round test:
def test_serve_rounds_announces_round_end(...):
    ...
    assert any("round ended:" in line for line in printed)   # capsys or the
    # file's existing print-capture pattern
    assert console.round_ended_calls == [("TestBit", "completed")]
```

Round reasons to announce: `"completed"` (normal end, which includes an operator abort mid-run: both land IDLE), `"timeout-abort (N scored joined)"` with N from `scored_count(gs)` read before the abort. Capture `bit_name` at round load (`_serve_rounds` already reads `gs.bit_name` for its round-loaded print; keep it in a local, since `gs.bit_name` is None by the time the round ends).

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests -q -k "round_ended or announces_round"`

- [ ] **Step 3: Implement**

`console/agent.py`:

```python
    def announce_round_ended(self, bit_name: str, reason: str) -> None:
        """Broadcast a round's outcome into the console event log. Driven
        by the harness round loop (the only place that knows why a round
        ended); rides the existing `log` event so no front-end change is
        needed. Spec 2026-08-31 section 4: a Bit that vanished
        (self-completed in 2s, or timeout-aborted with nobody joined)
        must be impossible to misread as 'no load occurred'."""
        self.server.broadcast(protocol.log_event(
            "info", f"round ended: {bit_name} ({reason})"))
```

`_serve_rounds`: in `_end_round()` (Task 5), grow parameters `bit_name, reason_text` (or capture via enclosing locals): print the marker line and call `console_agent.announce_round_ended(bit_name, reason_text)` guarded on `console_agent is not None`, BEFORE the recycle (so the event is on the wire before the console shows recycle progress stages). main()'s round-1 recycle site prints/announces the same way for round 1 (reason "completed" or the timeout-abort text).

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add harness/markers.py console/agent.py harness/terrarium_boot.py tests/
git commit -m "feat(console): round-ended events and stdout marker at every round end"
```

---

### Task 7: `ShroomClient.reset_for_lobby()` (spec section 3)

**Files:**
- Modify: `harness/shroom_client.py` (class `ShroomClient`; per-round fields set at :106-109 and :149)
- Test: `tests/test_shroom_client.py` (append)

**Interfaces:**
- Produces: `ShroomClient.reset_for_lobby() -> None`: clears exactly `config`, `released`, `last_deny`, `last_error` (back to their `__init__` values); everything else (leds backend, session/fade state, counters like `clamped`) is preserved.
- Consumed by: Task 8's persist loop.

- [ ] **Step 1: Write the failing test**

```python
def test_reset_for_lobby_clears_round_state_and_keeps_the_rest():
    client = make_client()            # the file's existing helper/fixture
    client.config = {"role": "player"}
    client.released = True
    client.last_deny = ("full", "hint")
    client.last_error = ("ctx", "msg")
    client.clamped = 7
    client.reset_for_lobby()
    assert client.config is None
    assert client.released is False
    assert client.last_deny is None
    assert client.last_error is None
    assert client.clamped == 7        # cumulative stats survive rounds
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests/test_shroom_client.py -q -k lobby`

- [ ] **Step 3: Implement**

```python
    def reset_for_lobby(self) -> None:
        """Return this client to its pre-join state so a --persist
        o2_shroom can re-enter the hello+join lobby after a release,
        without reconstructing the client (the WebSim backend and its
        browser tab must survive rounds). Owns exactly which fields a
        round clears, so harness/o2_shroom.py's loop never reaches into
        internals. Cumulative diagnostics (clamped, latency samples) are
        deliberately kept: the exit report spans the whole process."""
        self.config = None
        self.released = False
        self.last_deny = None
        self.last_error = None
```

Check `__init__`/the `:149` site for any other role-scoped latch (Grep the class for fields assigned in the `role` handler) and clear those too if the role handler sets more than `config`; the test then pins whatever the real set is.

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_shroom_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/shroom_client.py tests/test_shroom_client.py
git commit -m "feat(harness): ShroomClient.reset_for_lobby() for persistent Testshrooms"
```

---

### Task 8: `o2_shroom --persist` lobby mode and run_stack passthrough (spec section 3)

**Files:**
- Modify: `harness/o2_shroom.py` (argparser at :355-417, main loop at :536-629; new pure helper near `next_heartbeat_time`)
- Modify: `harness/run_stack.py` (shroom command construction at :179-183, new `--persist-shrooms` flag)
- Test: `tests/test_o2_shroom.py` (Glob first; the pure helpers of this module are already unit-tested there, e.g. `next_heartbeat_time`), `tests/test_run_stack.py` (append)

**Interfaces:**
- Consumes: `ShroomClient.reset_for_lobby()` (Task 7).
- Produces: CLI flag `--persist` on o2_shroom; `--persist-shrooms` on run_stack; pure helper `lobby_round_over(client, persist: bool) -> str | None` returning `None` (keep looping), `"exit"` (round over, one-shot mode), or `"lobby"` (round over, persist: reset and rejoin).

- [ ] **Step 1: Write the failing helper tests**

```python
def test_lobby_round_over_release_one_shot_exits():
    client = make_client(); client.released = True
    assert lobby_round_over(client, persist=False) == "exit"

def test_lobby_round_over_release_persist_relobbies():
    client = make_client(); client.released = True
    assert lobby_round_over(client, persist=True) == "lobby"

def test_lobby_round_over_deny_one_shot_exits():
    client = make_client(); client.last_deny = ("closed", "hint")
    assert lobby_round_over(client, persist=False) == "exit"

def test_lobby_round_over_deny_persist_keeps_looping():
    # under persist a deny is not terminal: the node may reopen next round,
    # so the device stays in the lobby and keeps join-retrying.
    client = make_client(); client.last_deny = ("closed", "hint")
    assert lobby_round_over(client, persist=True) is None

def test_lobby_round_over_quiet_keeps_looping():
    assert lobby_round_over(make_client(), persist=False) is None
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests -q -k lobby_round_over`

- [ ] **Step 3: Implement**

Helper in `harness/o2_shroom.py` (module level, above `main()`):

```python
def lobby_round_over(client, persist: bool) -> str | None:
    """The per-tick round-over decision, factored pure so it is testable
    with no socket (this module's convention -- see next_heartbeat_time).
    Release always ends the round; a deny ends it only in one-shot mode.
    Under --persist a deny is not terminal: the node may reopen when the
    Console loads the next Bit, and the join-retry cadence keeps asking."""
    if client.released:
        return "lobby" if persist else "exit"
    if client.last_deny is not None and not persist:
        return "exit"
    return None
```

Argparser: add

```python
    parser.add_argument("--persist", action="store_true",
                        help="Lobby mode: on /"
                             "release, return to the hello+join-retry "
                             "lobby instead of exiting, and treat a deny "
                             "as retryable -- so this device joins "
                             "whatever Bit the Console loads next, across "
                             "room recycles (each Bit close replaces "
                             "Arco; o2lite auto-reconnects and "
                             "reconnect_recheck re-verifies the service). "
                             "Implies --join-retry 2.0 when --join-retry "
                             "is 0. Meaningless with --no-join.")
```

After parse: `if args.persist and args.join_retry <= 0: args.join_retry = 2.0`.

Main loop restructure (the `while not client.released` loop at :559): wrap it in an outer round loop and swap the conditions to use the helper:

```python
        round_num = 1
        while True:                     # rounds; one lap in one-shot mode
            ... existing per-round state init (deny_printed, error_printed,
                next_join, joins_sent, next_tilt) moves INSIDE this loop ...
            outcome = None
            while outcome is None:
                ... existing loop body, with these changes:
                - loop condition: replace `while not client.released` with
                  the explicit `outcome = lobby_round_over(client,
                  args.persist)` check at the top of each lap (after
                  o2lite.poll()).
                - the deny branch: keep the one-print-per-round guard, but
                  `break` only via lobby_round_over (remove the hard
                  `break` at :599; one-shot mode still exits because the
                  helper returns "exit" on deny).
                - clock guard for hub restarts: after `now =
                  o2lite.time_get()`, `if now < 0: time.sleep(0.05);
                  continue` -- across a room recycle the clock goes
                  unsynced until the new Arco masters it, and every
                  now-based branch below would misfire on -1.
                ...
            if outcome == "exit" or args.no_join or not args.persist:
                break
            print(f"round {round_num} released; returning to lobby",
                  flush=True)
            client.reset_for_lobby()
            round_num += 1
            send_hello()
            next_join = o2lite.time_get() + args.join_retry
            o2lite.send_cmd("/game/join", 0, "ss", args.dev, args.node)
```

Keep the existing `reconnect_recheck` call in the inner loop untouched: it is what re-verifies the device's service on the new hub after a recycle (`bridge_id` changes).

`harness/run_stack.py`: add `--persist-shrooms` (default False, help: "launch every player o2_shroom with --persist so devices survive Bit rounds; pair with terrarium_boot --serve") and append `"--persist"` to the shroom command list at :179-183 when set. Test: the file's existing command-construction tests (Grep `o2_shroom` in `tests/test_run_stack.py`) get one case asserting `"--persist"` appears iff the flag is given.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add harness/o2_shroom.py harness/run_stack.py tests/
git commit -m "feat(harness): o2_shroom --persist lobby mode; run_stack --persist-shrooms"
```

---

### Task 9: Full-suite verification and live acceptance handoff

**Files:**
- Modify: none (verification); live-acceptance notes go in the PR description
- Test: the whole suite

- [ ] **Step 1: Full suite through the venv**

Run: `.venv/bin/python -m pytest tests -q`
Expected: baseline + all new tests green, same skip count as baseline.

- [ ] **Step 2: Marker/docs consistency check**

Run: `grep -rn "CONTROL_ROUND_ENDED" harness/ tests/` and confirm the marker is in `markers.py`'s dict; run `grep -n "persist" harness/run_stack.py` to confirm the passthrough.

- [ ] **Step 3: Write the live acceptance checklist into the PR body** (from spec "Testing / Live acceptance"): serve-mode o2lite boot with console and one `--persist` Testshroom; console-load a Bit WITH an override; abort it; watch Arco pid change across the recycle; console-load MetronomeBit; the same Testshroom joins with no relaunch; audio works in round two. Flag the spec's open question explicitly in the PR: whether `arco.initialize()` can run twice in-process is unproven until this live pass; if it cannot, the fallback is the supervisor-restart pattern (spec section 2) and `_recycle_room` is the seam to swap.

- [ ] **Step 4: Commit anything outstanding and stop**

The branch is ready for review/PR; closeout (deep-dive sync via mm-deepdive-sync, finishing-a-development-branch) happens after review.

## Self-Review Notes

- Spec coverage: section 1 → Task 1; section 2 → Tasks 2-5 (+9 live); section 3 → Tasks 7-8; section 4 → Task 6; spec "Testing" → per-task tests + Task 9.
- Deliberate deviation from spec prose: the spec's "prints a round summary" per Testshroom round is reduced to a "round N released; returning to lobby" line; per-round lateness-stat resets are YAGNI (cumulative exit report kept). Record this in the PR body.
- Types: `recycle` callable is `() -> str | None` everywhere; `lobby_round_over` returns `None | "exit" | "lobby"`; `_recycle_room` mirrors Terrarium's never-raises reason-string convention.
