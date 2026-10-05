# Device Status Light Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close light-lexicon gaps G1 to G3: Control renders a slow white pulse on an invited device and a slow green pulse on a validated one until `/role`, flashes red x2 on a `/deny`, and flashes Room fixtures green x2 on an accepted start.

**Architecture:** `LobbyRuntime` (pure, sink-driven) owns a per-device status (`WHITE` invited, `GREEN` validated) and pushes a pulse level through a new `set_base` sink each tick; `DeviceLinkAgent` stores it in `_bases` and paints it as the starting frame of its role-less render pass, with override flashes on top. A deny calls `LobbyRuntime.on_deny`. The accept count goes from 1 to 2 in both accept paths. Three contract scenarios are re-recorded and the export is regenerated into mm-tuneshroom.

**Tech Stack:** Python 3 (`.venv/bin/python`), pytest, the contract kit (`contract_kit/`, `tools/record_scenarios.py`, `tools/export_contract.py`), Flutter for the mm-tuneshroom replay.

**Spec:** `docs/superpowers/specs/2026-10-05-device-status-light-design.md`

## Global Constraints

- Always run Python as `.venv/bin/python` from the worktree root (`/Users/chris/projects/mm-terrarium/.claude/worktrees/keen-colden-3dee80`). The worktree's `.venv` is already a symlink to `$HOME/projects/mm-terrarium/.venv`; never use `python3`.
- Branch: `claude/device-status-light` (already checked out; the spec commit is on it).
- Pulse: `PULSE_PERIOD_S = 4.0`, `PULSE_PEAK = 0.35`, raised cosine from dark.
- Device flash timing: `DEVICE_FLASH_ON_S = 0.2`, `DEVICE_FLASH_GAP_S = 0.2`; a two-flash train is `DEVICE_FLASH_TRAIN_S = 0.8`. Fixture timing (`FIXTURE_FLASH_ON_S`, `FIXTURE_FLASH_GAP_S` = 0.25) is unchanged.
- No `contract_version` bump.
- No em dashes in any prose (docs, docstrings, comments, commit messages).
- Match the surrounding code's comment density and docstring voice.
- Full suite before every commit that touches code: `.venv/bin/python -m pytest tests -q` must pass.

---

### Task 1: Start accepted flashes green x2 (G3)

**Files:**
- Modify: `devicelink/lobby_runtime.py` (`LobbyRuntime.feedback`)
- Modify: `devicelink/agent.py` (`on_start_requested`, near line 1741)
- Test: `tests/test_lobby_runtime.py`, `tests/test_lobby_agent.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `FEEDBACK_ACCEPT` maps to `(2, GREEN)`; `_flash_fixtures_now(GREEN, 2)` on accept.

- [ ] **Step 1: Update the runtime tests to expect two green flashes**

In `tests/test_lobby_runtime.py`, rename `test_feedback_flashes_fixtures_green_once_red_twice_or_thrice` to `test_feedback_flashes_fixtures_green_twice_red_twice_or_thrice` and change its first assertion to:

```python
    assert [o[1:4] for o in sinks.overrides] == [
        ("sim-main", GREEN, 1.0), ("sim-accent", GREEN, 1.0)] * 2
```

In `test_unbound_fixture_gets_no_flash`, change the assertion to:

```python
    assert [o[1] for o in sinks.overrides] == ["sim-main", "sim-main"]
```

- [ ] **Step 2: Add the agent test**

Append to `tests/test_lobby_agent.py`:

```python
def test_an_accepted_start_flashes_every_fixture_green_twice(monkeypatch):
    """Lexicon *Start accepted*: green x2 on every bound Room fixture, on
    the agent's own flash path because RUNNING has torn the lobby down."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING
    episodes = {"main": 0, "accent": 0}
    was_green = {"main": False, "accent": False}
    for _ in range(int(1.5 * 44)):
        agent.poll()
        for name in episodes:
            entry = agent._overrides.get(fixture_dev(name))
            green = entry is not None and entry[0] == GREEN
            episodes[name] += green and not was_green[name]
            was_green[name] = green
        clk.advance(1 / 44)
    assert episodes == {"main": 2, "accent": 2}
```

Also in `test_running_swaps_back_to_the_bits_room_declaration`, change the comment `# accept: one green flash on both fixtures` to `# accept: the first of two green flashes on both fixtures`.

- [ ] **Step 3: Run the tests to see them fail**

Run: `.venv/bin/python -m pytest tests/test_lobby_runtime.py tests/test_lobby_agent.py -q`
Expected: FAIL in the two runtime tests (one green per fixture, not two) and in `test_an_accepted_start_flashes_every_fixture_green_twice` (`episodes == {"main": 1, "accent": 1}`).

- [ ] **Step 4: Implement**

In `devicelink/lobby_runtime.py`, `LobbyRuntime.feedback`:

```python
        count, rgb = {FEEDBACK_ACCEPT: (2, GREEN), FEEDBACK_MINIMUM: (2, RED),
                      FEEDBACK_REFUSED: (3, RED)}.get(feedback, (0, GREEN))
```

In `devicelink/agent.py`, `on_start_requested`:

```python
            if self.game_server.lobby_config().enabled:
                self._flash_fixtures_now(GREEN, 2)
```

- [ ] **Step 5: Run the tests to see them pass, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_lobby_runtime.py tests/test_lobby_agent.py -q` (PASS), then `.venv/bin/python -m pytest tests -q`.
Expected: all pass. If a contract recording test (`tests/test_contract_scenarios.py::test_recording_matches_committed_json`) fails, a scenario records a fixture accept flash; note which one for Task 4 and re-record it there (do not re-record here). None is expected.

- [ ] **Step 6: Commit**

```bash
git add devicelink/lobby_runtime.py devicelink/agent.py tests/test_lobby_runtime.py tests/test_lobby_agent.py
git commit -m "feat(lobby): an accepted start flashes Room fixtures green x2 (lexicon G3)"
```

---

### Task 2: The pulse and per-device status in LobbyRuntime (G1 and G2, runtime half)

**Files:**
- Modify: `control/lobby.py` (constants, `pulse_level`)
- Modify: `devicelink/lobby_runtime.py` (`LobbySinks.set_base`, `_Status`, status bookkeeping, `on_deny`)
- Modify: `devicelink/agent.py` (`_lobby_sinks`: supply `set_base`; `__init__`: `self._bases`)
- Test: `tests/test_lobby_runtime.py`

**Interfaces:**
- Consumes: Task 1's `feedback` (unchanged here).
- Produces:
  - `control.lobby.PULSE_PERIOD_S: float = 4.0`, `PULSE_PEAK: float = 0.35`, `DEVICE_FLASH_TRAIN_S: float = 0.8`, `pulse_level(t: float) -> float`.
  - `LobbySinks.set_base: Callable[[str, tuple | None, float], None]`, called `set_base(dev, rgb, level)` or `set_base(dev, None, 0.0)` to clear.
  - `LobbyRuntime.on_deny(dev: str) -> None`.
  - `DeviceLinkAgent._bases: dict[str, tuple[tuple[int, int, int], float]]` (filled here, rendered in Task 3).

- [ ] **Step 1: Write the failing tests**

In `tests/test_lobby_runtime.py`:

Extend the `control.lobby` import with `DEVICE_FLASH_TRAIN_S, PULSE_PEAK, PULSE_PERIOD_S, pulse_level`.

In `_Sinks.__init__` add `self.bases = []     # (t, dev, rgb-or-None, level)`, and in `as_sinks()` add the keyword:

```python
            set_base=lambda dev, rgb, lvl: self.bases.append(
                (self.t, dev, rgb, lvl)),
```

Add a helper after `_run`:

```python
def _bases_for(sinks, dev):
    return [b for b in sinks.bases if b[1] == dev]
```

Append these tests:

```python
def test_pulse_level_rises_from_dark_to_its_peak_and_back():
    assert pulse_level(-1.0) == 0.0 and pulse_level(0.0) == 0.0
    assert abs(pulse_level(PULSE_PERIOD_S / 2) - PULSE_PEAK) < 1e-9
    assert pulse_level(PULSE_PERIOD_S) < 1e-9
    assert 0.0 < pulse_level(1.0) < PULSE_PEAK
    assert DEVICE_FLASH_TRAIN_S == 0.8


def test_an_invite_holds_the_white_base_dark_through_its_flashes_then_pulses():
    rt, sinks, clock = _rt()
    rt.start()
    t0 = clock.t
    rt.consider_invite("ie3")
    _run(rt, sinks, clock, 0.75)
    dark = _bases_for(sinks, "ie3")
    assert dark and all(b[2] == WHITE and b[3] == 0.0 for b in dark)
    _run(rt, sinks, clock, 2.0)
    lit = [b for b in _bases_for(sinks, "ie3") if b[3] > 0]
    assert lit and lit[0][0] >= t0 + DEVICE_FLASH_TRAIN_S - 1e-9
    assert all(b[2] == WHITE and b[3] <= PULSE_PEAK + 1e-9 for b in lit)


def test_a_steady_base_is_sent_once():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    _run(rt, sinks, clock, 0.5)                 # still held dark
    assert len(_bases_for(sinks, "ie3")) == 1


def test_validation_turns_the_base_green_after_the_ceremony_flashes():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    rt.consider_invite("ie2")
    _run(rt, sinks, clock, 1.0)
    t0 = clock.t
    rt.forget("ie1")
    rt.on_scored_join("ie1")                    # ceremony slot at t0
    rt.forget("ie2")
    rt.on_scored_join("ie2")                    # queued: slot at t0 + 2.8
    assert _bases_for(sinks, "ie1")[-1][2:] == (None, 0.0)
    _run(rt, sinks, clock, 0.75)
    new = [b for b in _bases_for(sinks, "ie1") if b[0] > t0]
    assert new and all(b[2] == GREEN and b[3] == 0.0 for b in new)
    _run(rt, sinks, clock, 1.5)
    assert _bases_for(sinks, "ie1")[-1][2] == GREEN
    assert _bases_for(sinks, "ie1")[-1][3] > 0
    assert [b for b in _bases_for(sinks, "ie2") if b[0] > t0 and b[3] > 0] == []
    _run(rt, sinks, clock, 2.0)
    lit2 = [b for b in _bases_for(sinks, "ie2") if b[0] > t0 and b[3] > 0]
    assert lit2 and lit2[0][2] == GREEN
    assert lit2[0][0] >= t0 + 2.8 + DEVICE_FLASH_TRAIN_S - 1e-6


def test_full_clears_white_bases_and_keeps_green_ones():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    rt.consider_invite("ie2")
    _run(rt, sinks, clock, 1.0)
    rt.forget("ie2")
    rt.on_scored_join("ie2")
    _run(rt, sinks, clock, 0.1)
    rt.set_state(LobbyState.FULL)
    assert _bases_for(sinks, "ie1")[-1][2] is None
    _run(rt, sinks, clock, 2.0)
    assert _bases_for(sinks, "ie1")[-1][2] is None
    assert _bases_for(sinks, "ie2")[-1][2] == GREEN


def test_stop_clears_every_base():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    rt.on_scored_join("ie2")
    _run(rt, sinks, clock, 0.1)
    rt.stop()
    assert _bases_for(sinks, "ie1")[-1][2] is None
    assert _bases_for(sinks, "ie2")[-1][2] is None
    n = len(sinks.bases)
    _run(rt, sinks, clock, 1.0)
    assert len(sinks.bases) == n


def test_a_deny_purges_the_white_flash_flashes_red_twice_and_holds_the_pulse_dark():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    _run(rt, sinks, clock, 0.3)                 # second white flash still queued
    t0 = clock.t
    rt.on_deny("ie1")
    _run(rt, sinks, clock, 0.75)
    after = [o for o in sinks.overrides if o[1] == "ie1" and o[0] >= t0]
    assert [o[2:] for o in after] == [(RED, 1.0, 0.2), (RED, 1.0, 0.2)]
    assert abs(after[1][0] - after[0][0] - 0.4) < 0.03
    assert [b for b in _bases_for(sinks, "ie1") if b[3] > 0] == []
    _run(rt, sinks, clock, 1.0)
    lit = [b for b in _bases_for(sinks, "ie1") if b[3] > 0]
    assert lit and lit[0][2] == WHITE
    assert lit[0][0] >= t0 + DEVICE_FLASH_TRAIN_S - 1e-9


def test_a_deny_with_no_status_flashes_red_and_sets_no_base():
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_deny("ie9")
    _run(rt, sinks, clock, 2.0)
    assert [o[2] for o in sinks.overrides if o[1] == "ie9"] == [RED, RED]
    assert _bases_for(sinks, "ie9") == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_lobby_runtime.py -q`
Expected: collection ERROR, `ImportError: cannot import name 'DEVICE_FLASH_TRAIN_S'`.

- [ ] **Step 3: Implement the pure half in `control/lobby.py`**

Add `import math` after `from __future__ import annotations` (with the other imports). After the `DEVICE_FLASH_GAP_S = 0.2` line add:

```python
# A two-flash device train, first flash on to last gap off: the pulse
# under it is held dark this long so the off-gaps read as black.
DEVICE_FLASH_TRAIN_S = 2 * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
```

After `HUE_DRIFT_PERIOD_S = 20.0` add:

```python
# The lexicon's slow pulse (Invite between flashes, Ready): Lux Aeterna's
# sys:idle period, peaking well under a flash's full level.
PULSE_PERIOD_S = 4.0
PULSE_PEAK = 0.35
```

After `hue_drift_cc` add:

```python
def pulse_level(t: float) -> float:
    """A raised cosine from dark: 0 at and before t=0, PULSE_PEAK at half a
    period, dark again at a full one."""
    if t <= 0:
        return 0.0
    return PULSE_PEAK * (1.0 - math.cos(2.0 * math.pi * t / PULSE_PERIOD_S)) / 2.0
```

- [ ] **Step 4: Implement the runtime half in `devicelink/lobby_runtime.py`**

Extend the `control.lobby` import with `DEVICE_FLASH_TRAIN_S` and `pulse_level`.

Add the sink as the last `LobbySinks` field:

```python
    set_base: Callable[[str, tuple | None, float], None]
```

Before `class LobbyRuntime`, add:

```python
@dataclass
class _Status:
    """One hello'd device's slow pulse: WHITE while invited, GREEN once
    validated. Dark until `start`, which every flash train for the device
    pushes past its own end."""
    rgb: tuple
    start: float
```

In `LobbyRuntime.__init__`, after `self._joins = 0`:

```python
        self._status: dict[str, _Status] = {}
        # dev -> (rgb, level byte) last handed to set_base, so a steady
        # pulse (held dark, say) costs nothing per tick.
        self._last_base: dict[str, tuple] = {}
```

In `stop()`, after `self._invites.clear()`:

```python
        # No pulse outlives the lobby: at RUNNING every device gets a role,
        # and an abort leaves nothing to wait for.
        for dev in list(self._status):
            self._clear_status(dev)
```

Replace `set_state` with:

```python
    def set_state(self, state: LobbyState) -> None:
        if state is self._state:
            return
        self._state = state
        if state is LobbyState.FULL:
            self._drone(False)
            for name in self._s.fixture_names():
                self._light(name, HUE_CC, GREEN_HUE_CC)
            # FULL stops invites, so nobody is invited any more. A
            # validated device keeps its green Ready pulse.
            for dev in [d for d, st in self._status.items() if st.rgb == WHITE]:
                self._clear_status(dev)
        else:
            self._drone(True)
```

In `tick()`, after the `if not self._running: return` line and before `t = now - self._origin`:

```python
        for dev, st in list(self._status.items()):
            self._base(dev, st.rgb, pulse_level(now - st.start))
```

After `_at`, add the status helpers:

```python
    # --- device status pulse (lexicon G1) ------------------------------
    def _hold(self, dev: str, rgb: tuple, until: float) -> None:
        """Give `dev` the `rgb` pulse, dark until at least `until`."""
        st = self._status.get(dev)
        if st is None or st.rgb != rgb:
            self._status[dev] = _Status(rgb, until)
        else:
            st.start = max(st.start, until)

    def _base(self, dev: str, rgb: tuple, level: float) -> None:
        key = (rgb, round(level * 255))
        if self._last_base.get(dev) == key:
            return
        self._last_base[dev] = key
        self._s.set_base(dev, rgb, level)

    def _clear_status(self, dev: str) -> None:
        self._status.pop(dev, None)
        if self._last_base.pop(dev, None) is not None:
            self._s.set_base(dev, None, 0.0)
```

In `on_scored_join`, after `at = self._slots.reserve(self._clock())`:

```python
        # Ready: the green pulse rises once the ceremony's flashes are done.
        self._hold(dev, GREEN, at + DEVICE_FLASH_TRAIN_S)
```

In `consider_invite`, after the `for i in range(2): ... self._at(t, _InviteFlash(dev, self._s))` loop:

```python
        self._hold(dev, WHITE, now + DEVICE_FLASH_TRAIN_S)
```

After `consider_invite` (before `is_invited`), add:

```python
    # --- deny flash (lexicon G2) ---------------------------------------
    def on_deny(self, dev: str) -> None:
        """Failure: red x2 on the denied device. Its still-queued white
        invite flash goes first, so white and red never interleave. A
        device still invited gets its white pulse back after the red."""
        self._purge_invite_flashes(dev)
        now = self._clock()
        for i in range(2):
            t = now + i * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
            self._at(t, lambda d=dev: self._s.set_override(d, RED, 1.0,
                                                           DEVICE_FLASH_ON_S))
        st = self._status.get(dev)
        if st is not None:
            st.start = max(st.start, now + DEVICE_FLASH_TRAIN_S)
```

Replace the body of `forget` (keep its docstring, adding one sentence: "Its status pulse goes too.") with:

```python
        self._invites.forget(dev)
        self._purge_invite_flashes(dev)
        self._clear_status(dev)

    def _purge_invite_flashes(self, dev: str) -> None:
        self._queue.purge(
            lambda thunk: isinstance(thunk, _InviteFlash) and thunk.dev == dev)
```

- [ ] **Step 5: Supply the sink in the agent**

In `devicelink/agent.py` `__init__`, directly after the `self._override_only: set[str] = set()` block, add:

```python
        # dev -> (rgb, level): the lobby's status pulse for a hello'd device
        # with no role (white while invited, green once validated; lexicon
        # G1), fed by LobbyRuntime's set_base sink. The role-less pass in
        # _render_frames paints it under any override.
        self._bases: dict[str, tuple[tuple[int, int, int], float]] = {}
```

In `_lobby_sinks`, next to `def set_override(...)`, add:

```python
        def set_base(dev, rgb, level):
            if rgb is None:
                self._bases.pop(dev, None)
            else:
                self._bases[dev] = (rgb, level)
```

and pass `set_base=set_base` in the `LobbySinks(...)` call (after `announce=gs.notify_lobby`).

- [ ] **Step 6: Run the tests to see them pass, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_lobby_runtime.py -q` (PASS), then `.venv/bin/python -m pytest tests -q`.
Expected: all pass. The agent stores bases but does not render them yet, so no recording changes.

- [ ] **Step 7: Commit**

```bash
git add control/lobby.py devicelink/lobby_runtime.py devicelink/agent.py tests/test_lobby_runtime.py
git commit -m "feat(lobby): per-device status pulse and the deny flash in LobbyRuntime (lexicon G1, G2)"
```

---

### Task 3: The agent renders the base and wires the deny (G1 and G2, agent half)

**Files:**
- Modify: `devicelink/agent.py` (`_solid_frame`, `_apply_override`, the role-less pass of `_render_frames`, `_on_handshake`, and the four forget paths)
- Test: `tests/test_lobby_agent.py`

**Interfaces:**
- Consumes: Task 2's `self._bases`, `LobbyRuntime.on_deny(dev)`.
- Produces: `DeviceLinkAgent._solid_frame(rgb, level, length, color_order) -> bytes` (staticmethod).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lobby_agent.py` (add `MuteCue` is already imported from `control.cues`):

```python
def _leds(server, dev):
    return [bytes(m["args"][0]) for (_d, m) in server.sent
            if m["address"] == f"/{dev}/leds"]


def _dim(frame, pixel):
    """True if every pixel of `frame` is `pixel` scaled to one level in
    (0, 255): a pulse frame, not a flash and not black."""
    peak = max(pixel)
    level = frame[pixel.index(peak)]
    want = bytes(round(c * level / peak) for c in pixel) * 12
    return 0 < level < 255 and frame == want


def test_an_invited_device_pulses_white_between_invite_flashes(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 2.0)
    assert _dim(_leds(server, "ie1")[-1], (255, 255, 255))


def test_a_validated_device_pulses_green_until_its_role(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _handshake(server, agent, gs, "c1", "ie1")
    _poll(agent, clk, 2.5)
    assert _dim(_leds(server, "ie1")[-1], (255, 0, 0))   # GRB green
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    agent.poll()
    assert agent._bases == {}
    assert _sent(server, "/ie1/role")


def test_a_deny_flashes_the_device_red_twice(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 1.0)
    n = len(server.sent)
    _handshake(server, agent, gs, "c1", "ie1", "NO_SUCH_NODE")
    _poll(agent, clk, 1.0)
    later = [m for (_d, m) in server.sent[n:]
             if m["address"] in ("/ie1/deny", "/ie1/leds")]
    assert later[0]["address"] == "/ie1/deny"
    red = bytes([0, 255, 0] * 12)                        # GRB red
    reds = [m for m in later if m["address"] == "/ie1/leds"
            and bytes(m["args"][0]) == red]
    assert len(reds) == 2


def test_a_muted_invited_device_stays_black(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    gs._dispatch_cues([MuteCue("ie1")], at=clk.t)
    n = len(_leds(server, "ie1"))
    _poll(agent, clk, 3.0)
    assert all(f == bytes(36) for f in _leds(server, "ie1")[n:])


def test_a_full_lobby_clears_the_white_pulse_with_one_black_frame(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    gs.registration.role_table.roles["player"].capacity = 1
    _hello(server, agent, "c1", "ie1")
    _poll(agent, clk, 2.0)
    assert _dim(_leds(server, "ie1")[-1], (255, 255, 255))
    n = len(_leds(server, "ie1"))
    _hello(server, agent, "c2", "ie2")
    _handshake(server, agent, gs, "c2", "ie2")
    assert gs.lobby_state() == "FULL"
    _poll(agent, clk, 1.0)
    assert _leds(server, "ie1")[n:] == [bytes(36)]
```

The FULL path is the real-world cause of a cleared white base. (`LobbyRuntime.forget` would not do: the next tick re-invites the device at once.)

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_lobby_agent.py -q`
Expected: FAIL in the white pulse, green pulse, deny and FULL tests (frames are black, or no red frames); the mute test passes already (that is fine: it pins behavior the change must keep).

- [ ] **Step 3: Factor the solid painter**

In `devicelink/agent.py`, replace the tail of `_apply_override` (from `rgb, level, _expires = entry` to the end) with:

```python
        rgb, level, _expires = entry
        return self._solid_frame(rgb, level, len(frame), color_order)

    @staticmethod
    def _solid_frame(rgb, level: float, length: int,
                     color_order: str = "GRB") -> bytes:
        """`length` bytes of one colour at `level`, in the surface's own
        channel order; W stays 0."""
        by_name = {**dict(zip("RGB", rgb)), "W": 0}
        pixel = bytes(max(0, min(255, round(by_name[ch] * level)))
                      for ch in color_order)
        reps = length // len(pixel) + 1
        return (pixel * reps)[:length]
```

- [ ] **Step 4: Render the base in the role-less pass**

In `_render_frames`, replace the role-less pass (from the comment `# A device with no session (hello'd, not joined: ...` to the end of the method) with:

```python
        # A device with no session (hello'd, not joined: the one being
        # invited or waiting on start) renders its lobby status pulse
        # (_bases) with any override painted on top, so an invite and
        # Ready are visible before any role exists. With neither, it gets
        # one black frame and drops out.
        black = bytes(_DEVICE_CHANNELS)
        gs = self.game_server
        bound = set(gs.room.bound.values()) if gs.room is not None else set()
        for dev in list(set(self._overrides) | self._override_only
                        | set(self._bases)):
            # Only a KNOWN device that is not a Room fixture may be painted
            # here. `bound` is checked as well as _fixture_for because
            # the two disagree while a Room is being torn down, and a
            # fixture dev must never be handed a 36-channel player frame.
            if (fixture_name(dev) is not None or dev in self.bridges
                    or dev in bound or self._fixture_for(dev) is not None
                    or gs.devices.get(dev) is None):
                self._override_only.discard(dev)
                continue
            base = self._bases.get(dev)
            frame = black
            if base is not None and dev not in self._muted:
                frame = self._solid_frame(base[0], base[1], _DEVICE_CHANNELS,
                                          order)
            if dev in self._overrides:
                frame = self._apply_override(dev, frame, order)
            if self._last_frames.get(dev) == frame:
                continue
            self._last_frames[dev] = frame
            try:
                self._send(dev, protocol.leds_event(
                    dev, frame, when=self._clock() + self._horizon))
            except Exception:
                logger.exception("leds send for %s failed", dev)
            if frame == black and dev not in self._bases:
                # Nothing more to say until another override or base
                # lands: forget the frame so this dev drops out of the loop.
                self._override_only.discard(dev)
                self._last_frames.pop(dev, None)
            else:
                self._override_only.add(dev)
```

- [ ] **Step 5: Wire the deny and forget the base everywhere an override is forgotten**

In `_on_handshake`, after `self._notify_join_denied(dev, node, result.reason)`:

```python
        # Failure on the device (lexicon G2): lobby-gated like the invite
        # flash, so a deny with no lobby (no Room, RUNNING) shows nothing.
        if self._lobby is not None:
            self._lobby.on_deny(dev)
```

Then add `self._bases.pop(dev, None)` directly after each existing `self._override_only.discard(dev)` that sits beside a `self._overrides.pop(dev, None)`: in `unwire_room` (near line 746), `_forget_reaped` (near line 879), `_drop_player_bridge` (near line 1410) and `_finish_release` (near line 1578). Find them with:

Run: `grep -n "self._override_only.discard(dev)" devicelink/agent.py`
Expected: those four sites plus the two inside `_render_frames` (leave those two alone).

- [ ] **Step 6: Run the tests to see them pass, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_lobby_agent.py tests/test_devicelink_agent.py -q` (PASS), then `.venv/bin/python -m pytest tests -q`.
Expected: everything passes EXCEPT `tests/test_contract_scenarios.py::test_recording_matches_committed_json` for `handshake_validate_then_role` and `room_node_handshake_binds` (their frames now include pulses). Those are re-recorded in Task 4. Any other failure is a bug in this task: fix it before committing. Commit with those two known failures and say so in the commit body.

- [ ] **Step 7: Commit**

```bash
git add devicelink/agent.py tests/test_lobby_agent.py
git commit -m "feat(devicelink): render the lobby status pulse and flash red on deny (lexicon G1, G2)

The recordings for handshake_validate_then_role and
room_node_handshake_binds now differ; they are re-recorded in the
next commit."
```

---

### Task 4: Re-record the contract scenarios

**Files:**
- Modify: `contract_kit/scenarios.py` (`handshake_validate_then_role`, `room_node_handshake_binds` docstring, `deny_stays_hellod`, new constants)
- Modify: `contract_kit/recordings/handshake_validate_then_role.json`, `room_node_handshake_binds.json`, `deny_stays_hellod.json` (regenerated, never hand-edited)
- Test: `tests/test_contract_scenarios.py`

**Interfaces:**
- Consumes: Tasks 1 to 3.
- Produces: constants `READY_PULSE_CHECK_T = 2900`, `DENY_FLASH_CHECK_T = 400`, `DENY_PULSE_CHECK_T = 3100` in `contract_kit/scenarios.py`.

Timing these constants rely on (both scenarios accept at `ACCEPT_AFTER_MS` = 300, and the handshake, the flash and its frame all land in one agent poll at 300): the green Ready pulse starts at 300 + 800 = 1100 ms and peaks at 3100; 2900 is 1.8 s in, about 0.34 of full level, on the flat top of the cosine. In `deny_stays_hellod` the red flashes are at 300 and 700 (400 is mid-flash) and the white pulse starts at 1100, peaking at 3100.

- [ ] **Step 1: Update the scenario-level tests first**

In `tests/test_contract_scenarios.py`, add `READY_PULSE_CHECK_T, DENY_FLASH_CHECK_T, DENY_PULSE_CHECK_T` to the `from contract_kit.scenarios import (...)` list.

In `test_deny_leaves_the_device_hellod_with_its_heartbeat_running`, replace

```python
    # Denied means denied: no validation, no role, no pixels.
    assert _sends(data, "/$DEV/validated") == []
    assert _sends(data, "/$DEV/role") == []
    assert _frames(data) == []
```

with

```python
    # Denied means denied: no validation, no role.
    assert _sends(data, "/$DEV/validated") == []
    assert _sends(data, "/$DEV/role") == []
    # Failure (lexicon): red x2 from the deny, then the white invite pulse
    # comes back, since the lobby is WAITING and the device is still invited.
    red = [0, 255, 0] * 12                                   # GRB red
    reds = [s for s in _frames(data) if s["control_sends"]["args"][0] == red]
    assert len(reds) == 2
    assert ACCEPT_AFTER_MS <= reds[0]["t"] <= ACCEPT_AFTER_MS + 23
    flash, pulse = _kind(data, "expect_frame")
    assert flash["t"] == DENY_FLASH_CHECK_T and flash["expect_frame"]["grb"] == red
    grb = pulse["expect_frame"]["grb"]
    assert pulse["t"] == DENY_PULSE_CHECK_T
    assert grb == [grb[0]] * 36 and 0 < grb[0] < 255
```

In `test_handshake_validate_then_role...` (the test that loads `handshake_validate_then_role`), after the `assert not [s for s in _frames(data) if s["t"] >= ACCEPT_AFTER_MS and set(...) == {255}]` block, add:

```python
    # Ready (lexicon): a dim green pulse holds until the role.
    ready = [s for s in _kind(data, "expect_frame")
             if s["t"] == READY_PULSE_CHECK_T]
    assert len(ready) == 1
    grb = ready[0]["expect_frame"]["grb"]
    assert grb == [grb[0], 0, 0] * 12 and 0 < grb[0] < 255
```

- [ ] **Step 2: Update the scenarios**

In `contract_kit/scenarios.py`, after the `VALIDATE_START_T = 3000` line add:

```python
# Ready (light lexicon): the validation ceremony's green flashes end 800 ms
# after the accept, then a 4 s green pulse rises from dark; this check sits
# near its first peak, where the frame barely moves within a tolerance.
READY_PULSE_CHECK_T = 2900
# deny_stays_hellod with the Room's lobby up: red x2 from the deny at
# ACCEPT_AFTER_MS (this check is mid first flash), then the white invite
# pulse resumes 800 ms after the deny and peaks 2 s later.
DENY_FLASH_CHECK_T = 400
DENY_PULSE_CHECK_T = 3100
```

In `handshake_validate_then_role`, insert before `rec.advance_to(VALIDATE_START_T)`:

```python
    rec.advance_to(READY_PULSE_CHECK_T)
    rec.expect_frame(READY_PULSE_CHECK_T)    # Ready: the dim green pulse
```

and in its docstring, after the sentence ending "(and only `/$DEV/handshake` is ever an invite in any case).", add:

```
    Between the invite's flashes the device shows a dim white pulse, and
    once the ceremony's green flashes are done a dim green pulse (the light
    lexicon's Ready) holds until `/$DEV/role`; READY_PULSE_CHECK_T checks
    it near its peak.
```

In `room_node_handshake_binds`'s docstring, change "the lobby's white invite flash shows before the accept (the frames at t=0 and t=414)" so it reads: "the lobby's white invite flash shows before the accept, then the dim white invite pulse rises from 800 ms until the bind clears it". Read the re-recorded file in Step 4 and correct any timestamp this sentence states to the recorded one.

Replace `deny_stays_hellod` with:

```python
def deny_stays_hellod() -> dict:
    """An accept naming a node no role table declares is denied with
    `/$DEV/deny ["no such node", <hint>]`: no validation and no role, and
    the hello heartbeat keeps running on its own 5 s cadence. Control
    keeps inviting the device every 5 s; its `device.handshake` policy
    answers only the first invite of a link-up, so it accepts once.

    With a Room's lobby up, the deny is visible (the light lexicon's
    Failure): red x2 on `/$DEV/leds` from the deny, replacing the invite's
    still-queued second white flash, then the dim white invite pulse
    resumes, because the lobby is WAITING and the device is still invited.
    DENY_FLASH_CHECK_T checks the first red flash and DENY_PULSE_CHECK_T
    the pulse near its peak.
    """
    rec = Recorder(name="deny_stays_hellod",
                   summary="An accept naming an unknown node is denied with "
                           "a red flash; the device stays hello'd and "
                           "invited with its heartbeat running",
                   handshake={"node": NO_SUCH_NODE,
                              "ack_after_ms": ACCEPT_AFTER_MS},
                   with_room=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.advance_to(DENY_FLASH_CHECK_T)
    rec.expect_frame(DENY_FLASH_CHECK_T)     # Failure: the first red flash
    rec.advance_to(DENY_PULSE_CHECK_T)
    rec.expect_frame(DENY_PULSE_CHECK_T)     # still invited: the white pulse
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.advance_to(10000)
    rec.expect_hello(10000)
    rec.advance_to(11000)
    return rec.finish()
```

The summary string is exported into each device repo's `contract.json` index; keep it one line of plain text.

- [ ] **Step 3: Re-record**

Run: `.venv/bin/python -m tools.record_scenarios`
Then: `git status --short contract_kit/recordings`
Expected: exactly `handshake_validate_then_role.json`, `room_node_handshake_binds.json` and `deny_stays_hellod.json` modified. If any other recording changed, stop: inspect its diff (`git diff contract_kit/recordings/<name>.json | head -80`), explain the change, and only keep it if it follows from this spec (for example a fixture accept flash, now two). Otherwise it is a bug in Tasks 1 to 3.

- [ ] **Step 4: Inspect the three diffs**

Run, for each of the three files: `git diff contract_kit/recordings/<name>.json | head -120`
Check by eye:
- `handshake_validate_then_role`: dim white frames (all 36 values equal, under 255) between the invite flash and 300 are absent (the pulse is held dark until 800 and the accept is at 300), the green flashes at 300 and about 722, then dim green frames (`[g, 0, 0] * 12`) from about 1100 until 3000, and nothing white after 300.
- `room_node_handshake_binds`: dim white frames from about 800 to 1000, then the fixture frames at 1000 exactly as before.
- `deny_stays_hellod`: a white flash at 0, a black frame, red at 300, black, red at about 700, black, then dim white frames rising from about 1100; around 5000 a new white invite flash then dark until about 5800, and the same again at 10000.
Fix the `room_node_handshake_binds` docstring timestamps against what was recorded.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, including `test_recording_matches_committed_json` for all eighteen scenarios and the export tests (`tests/test_export_contract.py`).

- [ ] **Step 6: Commit**

```bash
git add contract_kit/scenarios.py contract_kit/recordings tests/test_contract_scenarios.py
git commit -m "test(contract): re-record the status light into three scenarios

deny_stays_hellod now loads a Room so it records Failure (red x2) and
the resumed invite pulse; handshake_validate_then_role checks Ready."
```

---

### Task 5: Re-export the contract to mm-tuneshroom

**Files:**
- Modify (in `/Users/chris/projects/mm-tuneshroom`): `test/contract/contract.json`, `test/contract/scenarios/handshake_validate_then_role.json`, `room_node_handshake_binds.json`, `deny_stays_hellod.json`

**Interfaces:**
- Consumes: Task 4's committed recordings (the export stamps `_provenance.commit` from mm-terrarium's HEAD, so Task 4 must be committed first).
- Produces: a branch `claude/contract-status-light` in mm-tuneshroom with one export commit.

- [ ] **Step 1: Branch mm-tuneshroom from a fresh main**

```bash
git -C /Users/chris/projects/mm-tuneshroom status --short
git -C /Users/chris/projects/mm-tuneshroom fetch -q origin
git -C /Users/chris/projects/mm-tuneshroom switch -c claude/contract-status-light origin/main
```

Expected: an empty status first. If it is not empty, stop and ask: another session may own that checkout.

- [ ] **Step 2: Export (module form, device contract guide section 2)**

From the mm-terrarium worktree root:

Run: `.venv/bin/python -m tools.export_contract /Users/chris/projects/mm-tuneshroom/test/contract`
Then: `git -C /Users/chris/projects/mm-tuneshroom status --short`
Expected: `test/contract/contract.json` and the three scenario files modified, nothing added or deleted. `contract.json` should differ only in `_provenance.commit` and the `deny_stays_hellod` summary row: check with `git -C /Users/chris/projects/mm-tuneshroom diff test/contract/contract.json`.

- [ ] **Step 3: Run mm-tuneshroom's contract tests**

```bash
cd /Users/chris/projects/mm-tuneshroom && flutter test test/contract_export_test.dart test/contract_replay_test.dart
```

Expected: PASS. A device is a pixel sink, so the new frames should replay as delivered. If a replay test fails, do not edit the export: report the failing scenario and assertion. The guide's rule is that the device catches up, and that is a separate mm-tuneshroom change for the user to schedule.

- [ ] **Step 4: Commit in mm-tuneshroom**

```bash
git -C /Users/chris/projects/mm-tuneshroom add test/contract
git -C /Users/chris/projects/mm-tuneshroom commit -m "test(contract): re-export mm-terrarium contract at <12-hex _provenance.commit>

The light lexicon's status light (Ready and invite pulses, red x2 on
deny) changes the frames recorded in three scenarios; no verb and no
contract_version change."
```

Replace `<12-hex _provenance.commit>` with the actual value; read it with `grep -m1 '"commit"' /Users/chris/projects/mm-tuneshroom/test/contract/contract.json`.

Do not push; the finishing step decides.

---

### Task 6: Flip the docs

**Files:**
- Modify: `docs/light-lexicon.md`
- Modify: `docs/MM_TERRARIUM.md`
- Modify: `docs/team-walkthrough-metronome-bit.md`
- Modify: `docs/device-contract-guide.md`

**Interfaces:**
- Consumes: the behavior shipped in Tasks 1 to 4, and the final recordings for any timestamp quoted.

- [ ] **Step 1: `docs/light-lexicon.md`**

Section 4 table, *Today* cells:
- **Invite:** `Ships (LobbyRuntime.consider_invite; needs a Room loaded with the lobby enabled; the /handshake itself goes out regardless). Between invites the device shows the slow white pulse.`
- **Ready:** `Ships (LobbyRuntime status pulse, from the end of the ceremony's green flashes until /role; lobby-gated like the invite).`
- **Failure:** `Ships (LobbyRuntime.on_deny; lobby-gated, so a deny with no Room, or in RUNNING, shows nothing).`

Section 4's pattern column for Invite and Ready stays as it is. Section 5 table, **Start accepted** *Today*: `Ships (FEEDBACK_ACCEPT, and the agent's own flash path after RUNNING).`

Section 6: change the opening sentence to say G1 to G3 shipped on 2026-10-05 (spec `docs/superpowers/specs/2026-10-05-device-status-light-design.md`), keep each of G1 to G3 as a one-line "Done:" entry naming where it lives (`LobbyRuntime` status pulse plus `DeviceLinkAgent._bases`; `LobbyRuntime.on_deny`; `FEEDBACK_ACCEPT` and `_flash_fixtures_now`), keep G4 unchanged, and replace the closing "G1 to G3 are mm-terrarium work..." paragraph with one sentence: "G4 is firmware (mm-devshroom)."

Also fix the section 4 sentence "the device never draws Invite, Ready or Failure itself" only if it now reads wrong (it does not; leave it).

- [ ] **Step 2: `docs/MM_TERRARIUM.md`**

Find each gap note:

Run: `grep -n "gap G1\|gap G2\|green x1\|light lexicon's gaps\|white x2 flash" docs/MM_TERRARIUM.md`

Edit:
- *Lobby and the handshake*, the Validation bullet: replace "A deny shows no light today; the lexicon's target is red x2 on the device (gap G2)." with "With the lobby up, a deny flashes the device red x2 (`LobbyRuntime.on_deny`), then its white invite pulse resumes if it is still invited."
- The invites bullet ("The lobby only adds the white x2 flash..."): add "and the status pulse: white while invited, green once validated, until `/role` (both held dark through any flash train so its gaps read black)".
- The lobby bullet (*The lobby (every Bit...*): after "a device chime cue with `key=<midi>` at +1.8 s." add "A validated device then pulses green until its role."
- The start feedback line: "accept green x1" becomes "accept green x2".
- The *Overrides* bullet: replace "A hello'd device with no role shows only its override, then one black frame, so it is dark between invites and after the ceremony; the light lexicon's target is a white pulse while invited and a green pulse once validated (gap G1)." with "A hello'd device with no role shows its lobby status pulse (`_bases`, from `LobbyRuntime`'s `set_base` sink: white while invited, green once validated) with any override on top, black while muted; when both are gone it gets one black frame."
- *Not yet built / deferred*, the light lexicon bullet: reduce to "**The light lexicon's G4** (`docs/light-lexicon.md` section 6): Rev 1 firmware does not yet render Solo or the white Looking pulse, and link-loss fallback to Looking is undecided." (keep its existing link).

Then run `grep -n "G1\|G2\|G3" docs/MM_TERRARIUM.md` and confirm no remaining line says G1 to G3 are unbuilt.

- [ ] **Step 3: `docs/team-walkthrough-metronome-bit.md`**

- Step 2 (Invite): replace "Between flashes the device goes dark for now; the light lexicon's target is a slow white pulse (`docs/light-lexicon.md`, gap G1)." with "Between flashes the device shows a slow white pulse (the lexicon's *Invite*)."
- Step 4 (Validated): replace "(no light yet; the lexicon's target is red x2)" with "(the device flashes red x2, the lexicon's *Failure*)".
- Step 5 (Ceremony): replace "The device then holds dark until start; the lexicon's target is a slow green pulse (*Ready*)." with "The device then pulses green until start (the lexicon's *Ready*)."
- Step 6 (Start): replace "(today green x1 for an accept; the lexicon's target is green x2)" with "(green x2 for an accept)".
- Lines 26 to 30 (the "light lexicon's gaps" bullet near the top): reduce to G4 only, as in the deep-dive.

Run `grep -n "G1\|G2\|G3\|x1\|target is" docs/team-walkthrough-metronome-bit.md` and confirm nothing stale remains.

- [ ] **Step 4: `docs/device-contract-guide.md`**

Rule 3's table row (near line 219): replace "nothing changes on the pixels until `/role`" with "the device's own state does not change until `/role`, though Control's frames do (the lobby's green Ready pulse; a `/deny` brings red x2 then the white invite pulse back); the device just displays them". Section 1's "eighteen recorded scenarios" stays.

Run `grep -n "no frame\|no pixels" docs/device-contract-guide.md` and fix any line that describes `deny_stays_hellod` as frameless.

- [ ] **Step 5: Check prose rules and commit**

Run: `grep -n "—" docs/light-lexicon.md docs/MM_TERRARIUM.md docs/team-walkthrough-metronome-bit.md docs/device-contract-guide.md docs/superpowers/specs/2026-10-05-device-status-light-design.md docs/superpowers/plans/2026-10-05-device-status-light.md`
Expected: no output (no em dashes).

```bash
git add docs/light-lexicon.md docs/MM_TERRARIUM.md docs/team-walkthrough-metronome-bit.md docs/device-contract-guide.md
git commit -m "docs: the light lexicon's G1 to G3 ship; only G4 (firmware) remains"
```

---

## After the plan

Run `superpowers:finishing-a-development-branch`: verify `main`'s tip first (another session may have touched the lobby), rebase if needed and re-run the full suite, then open the mm-terrarium PR, and, as a second PR, push mm-tuneshroom's `claude/contract-status-light` naming the mm-terrarium PR.
