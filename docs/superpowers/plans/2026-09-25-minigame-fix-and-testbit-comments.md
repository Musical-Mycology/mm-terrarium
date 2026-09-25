# MinigameBit Fix and TestBit Comment Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Victor's MinigameBit work in the normal lobby flow, cover it with tests, replace its Console placeholder text, and correct TestBit's stale comments.

**Architecture:** MinigameBit (`bits/minigame/minigame_bit.py`, commit `1ced5e9`) resets its player in `on_run_start()`, which the engine calls after SETUP joins, so a device that joined in the lobby is forgotten and every gesture is ignored. The fix keeps the joined player across run start, the way MetronomeBit keeps `_players`. Tests follow `tests/test_rev1_bit.py`: package discovery, unit-level handlers, and end to end through a real `GameServer`. TestBit stays the engine's regression fixture; only its comments change.

**Tech Stack:** Python 3, pytest, the in-repo `control/` engine. Run everything through `.venv/bin/python` (a fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first; never use bare `python3`, see `docs/MM_TERRARIUM.md` *Landed subsystems*).

**Spec:** No separate spec. The review in this session is the source; its findings are restated here:
1. Blocking: `on_run_start()` sets `self._dev = None` (`bits/minigame/minigame_bit.py:104`). Join in SETUP then start leaves `status()["dev"] is None` and hold/tap/ticks do nothing. Start then join works (10 blinks, END, tap resets).
2. No tests cover MinigameBit.
3. `bits/minigame/bit.toml` ships `TODO` `description` and `notes`, which the Console shows.
4. TestBit comments are stale: "BOTH shipped rooms" (there are three), a comment block describing "the Room's own role" that no longer exists (that light is `room_manifests()`), "instrument_requirements() above" (it is below), and `flash_device`'s condition says "Two-tap" though any tap fires it.

Out of scope (decided in review): the hold touch-down stamp shifting the blink grid early; the fleet-wide `version` drift between Bit classes and `bit.toml`; any TestBit behavior change.

## Global Constraints

- No em dashes in any text written (code comments, docstrings, Console copy, docs, commit messages).
- TestBit behavior must not change: only comments/docstrings and the `flash_device` condition `description` string.
- The full suite must stay green: `.venv/bin/python -m pytest tests -q` (baseline on `origin/main` 8289a3b: 2729 passed, 1 skipped).
- Console copy for MinigameBit is drafted from Victor's module docstring; keep his PENDING / INGAME / END vocabulary.

---

### Task 1: MinigameBit keeps its player across run start, with tests

**Files:**
- Modify: `bits/minigame/minigame_bit.py:102-106` (`on_run_start`)
- Create: `tests/test_minigame_bit.py`

**Interfaces:**
- Consumes: `MinigameBit`, `MINIGAME_PLAYER_NODE`, `BLINK_COUNT`, `BLINK_INTERVAL_S`, `BLINK_RGB` from `bits.minigame.minigame_bit`; `GameServer(bit_classes, clock=, carried_instruments=)`, `gs.hello(dev, name, ver, instrument=)`, `gs.join(dev, node)`, `gs.request_start(key, source_dev, source)`, `gs.run()`, `gs.data(dev, verb, args)`, `gs.tick(dt)`, `gs.on_solid_cue` sink called `(dev, rgb, level, duration, when)`.
- Produces: `MinigameBit.on_run_start()` no longer touches `_dev`. `status()` keys stay `{"phase", "dev", "blinks"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_minigame_bit.py`:

```python
"""MinigameBit: Victor's single-Tuneshroom bench toy (bits/minigame/).
Package discovery, unit-level handlers, and the round end to end through a
real GameServer in both join orders."""
from pathlib import Path

from bits.minigame.minigame_bit import (
    BLINK_COUNT,
    BLINK_INTERVAL_S,
    BLINK_RGB,
    MINIGAME_PLAYER_NODE,
    MinigameBit,
)
from control.bit_registry import BitRegistry
from control.catalog import load_catalog
from control.cues import FireFunction
from control.engine import GameServer

ROOT = Path(__file__).resolve().parents[1]
TICK = 1 / 44


# --- the package, as the Console sees it -----------------------------------

def test_package_is_discovered_and_visible_in_the_console():
    reg = BitRegistry.discover()
    assert "MinigameBit" in reg.packages, reg.errors
    row = next(r for r in reg.list_view(include_hidden=False)
               if r["name"] == "MinigameBit")
    assert row["hidden"] is False
    cfg = reg.resolve_config("MinigameBit", {})
    assert cfg.join_node() == MINIGAME_PLAYER_NODE
    assert reg.bit_class("MinigameBit") is MinigameBit


# --- handlers, unit level --------------------------------------------------

def _joined_bit():
    bit = MinigameBit()
    bit.on_join("ie1", "player")
    bit.on_run_start()
    return bit


def test_run_start_keeps_the_player_that_joined_in_setup():
    assert _joined_bit().status()["dev"] == "ie1"


def test_hold_starts_the_round_and_fires_blinks_on_a_2s_grid():
    bit = _joined_bit()
    assert bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=10.0) == []
    assert bit.status()["phase"] == "INGAME"
    assert bit.fires(10.0) == [FireFunction("blink", dev="ie1", at=10.0)]
    assert bit.fires(11.9) == []
    assert bit.fires(12.0) == [FireFunction("blink", dev="ie1", at=12.0)]


def test_tenth_blink_ends_the_round():
    bit = _joined_bit()
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=0.0)
    fired = bit.fires(BLINK_INTERVAL_S * BLINK_COUNT)
    assert len(fired) == BLINK_COUNT
    assert bit.status() == {"phase": "END", "dev": "ie1",
                            "blinks": BLINK_COUNT}
    assert bit.fires(1000.0) == []


def test_hold_outside_pending_does_not_restart_the_round():
    bit = _joined_bit()
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=0.0)
    bit.fires(2.0)
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=3.0)
    assert bit.status()["blinks"] == 2


def test_tap_resets_to_pending_from_any_phase():
    bit = _joined_bit()
    tap = bit.verb_handlers()["tap"]
    bit.verb_handlers()["hold"]("ie1", ["ie1", 0.8, 1], at=0.0)
    bit.fires(4.0)
    assert tap("ie1", ["ie1", 0.0, 80.0, 1], at=5.0) == []
    assert bit.status() == {"phase": "PENDING", "dev": "ie1", "blinks": 0}


def test_gestures_from_another_device_are_ignored():
    bit = _joined_bit()
    bit.verb_handlers()["hold"]("ie2", ["ie2", 0.8, 1], at=0.0)
    assert bit.status()["phase"] == "PENDING"


def test_never_completes_on_its_own():
    assert _joined_bit().update(3600.0) is False


# --- end to end through a real GameServer ----------------------------------

def _server(now):
    catalog = load_catalog(ROOT / "instruments")
    testshroom = catalog.get("published", "testshroom").instrument
    gs = GameServer({"MinigameBit": MinigameBit}, clock=lambda: now[0],
                    carried_instruments={"testshroom": testshroom})
    solid = []
    gs.on_solid_cue = lambda *a: solid.append(a)
    gs.load_bit("MinigameBit")
    gs.hello("ie1", "testshroom-dev", "1", instrument="testshroom")
    return gs, solid


def _play_a_round(gs, solid, now):
    assert gs.data("ie1", "hold", ["ie1", 0.8, 1]) is None
    for _ in range(int((BLINK_COUNT * BLINK_INTERVAL_S + 1) / TICK)):
        now[0] += TICK
        gs.tick(TICK)
    assert gs.bit.status()["phase"] == "END"
    assert len(solid) == BLINK_COUNT
    assert all(c[0] == "ie1" and c[1] == BLINK_RGB for c in solid)
    assert gs.data("ie1", "tap", ["ie1", 0.0, 80.0, 1]) is None
    assert gs.bit.status()["phase"] == "PENDING"


def test_join_in_setup_then_start_plays_a_round():
    """The lobby order: the regression that shipped in 1ced5e9."""
    now = [100.0]
    gs, solid = _server(now)
    assert gs.join("ie1", MINIGAME_PLAYER_NODE).granted
    assert gs.request_start(None, "ie1", "device") is None
    assert gs.bit.status()["dev"] == "ie1"
    _play_a_round(gs, solid, now)


def test_start_then_join_plays_a_round():
    now = [100.0]
    gs, solid = _server(now)
    gs.run()
    assert gs.join("ie1", MINIGAME_PLAYER_NODE).granted
    _play_a_round(gs, solid, now)
```

- [ ] **Step 2: Run the tests to verify the right ones fail**

Run: `.venv/bin/python -m pytest tests/test_minigame_bit.py -v`
Expected: `test_run_start_keeps_the_player_that_joined_in_setup`, the four other unit tests that go through `_joined_bit()` and act as `ie1` (hold/grid, tenth blink, hold outside pending, tap resets), and `test_join_in_setup_then_start_plays_a_round` FAIL (dev is `None`, phase stays `PENDING`). `test_package_is_discovered...`, `test_gestures_from_another_device_are_ignored`, `test_never_completes_on_its_own` and `test_start_then_join_plays_a_round` PASS.

- [ ] **Step 3: Write the minimal fix**

In `bits/minigame/minigame_bit.py`, replace `on_run_start`:

```python
    def on_run_start(self) -> None:
        # Joins land in SETUP, before this runs, so the player is kept:
        # clearing _dev here orphaned the lobby's device (MetronomeBit
        # keeps its _players across run start for the same reason).
        self._enter(Phase.PENDING)
        self._blink_t0 = None
        self._next_blink = 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_minigame_bit.py -v`
Expected: all 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add bits/minigame/minigame_bit.py tests/test_minigame_bit.py
git commit -m "fix(bits): MinigameBit keeps its lobby player across run start; add tests"
```

---

### Task 2: MinigameBit Console copy, and a guard against placeholder copy

**Files:**
- Modify: `bits/minigame/bit.toml:4` (`description`) and `bits/minigame/bit.toml:23` (`notes`)
- Modify: `tests/test_bit_packages.py` (append one test)

**Interfaces:**
- Consumes: `BitRegistry.discover().list_view(include_hidden=True)` rows with `"name"`, `"description"`, `"notes"` (`notes` may be `None`).
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bit_packages.py`:

```python
def test_no_shipped_bit_shows_placeholder_copy_in_the_console():
    """A scaffolded bit.toml ships TODO description/notes; the Console
    shows them verbatim to the operator."""
    reg = BitRegistry.discover()
    for row in reg.list_view(include_hidden=True):
        for field in ("description", "notes"):
            assert "TODO" not in (row.get(field) or ""), (row["name"], field)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bit_packages.py::test_no_shipped_bit_shows_placeholder_copy_in_the_console -v`
Expected: FAIL with `('MinigameBit', 'description')`.

- [ ] **Step 3: Replace the placeholders**

In `bits/minigame/bit.toml`, drafted from Victor's module docstring:

```toml
description = "Single-Tuneshroom bench toy: hold starts 10 white blinks 2 s apart, tap resets"
```

```toml
notes = "Join one device on MINIGAME_PLAYER_NODE. It waits in PENDING until a hold starts a round (INGAME): the LED blinks white 10 times, 2 s apart, then the round ends on its own (END). A tap resets to PENDING from any phase. No scoring, and it never completes on its own, so unload it when done. Hold needs a Rev 1 board or the simulator's long press; a standard Tuneshroom cannot send it."
```

- [ ] **Step 4: Run the package tests**

Run: `.venv/bin/python -m pytest tests/test_bit_packages.py tests/test_minigame_bit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bits/minigame/bit.toml tests/test_bit_packages.py
git commit -m "fix(bits): MinigameBit Console copy from its docstring; refuse TODO copy in any bit.toml"
```

---

### Task 3: TestBit comment corrections (no behavior change)

**Files:**
- Modify: `bits/test/test_bit.py:38-40`, `:75-77`, `:138-152`, `room_manifests` (currently no docstring), `:242`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. `TestBit` behavior, roles, functions and status are unchanged.

- [ ] **Step 1: Fix the room count** (lines 38-40). Replace:

```python
    # TestBit is the reference fixture for BOTH shipped rooms, so the
    # Scored/Jam validation loop works in either. control/boot.py reads
    # this off the class before instantiation.
```

with:

```python
    # TestBit is the reference fixture for every shipped room (TEST, DEMO
    # and VENUE), so the Scored/Jam validation loop works in each.
    # control/boot.py reads this off the class before instantiation.
```

- [ ] **Step 2: Fix the direction** (lines 75-77). Replace `# instrument_requirements() above -- the reference exemplar for` with `# instrument_requirements() below -- the reference exemplar for`.

- [ ] **Step 3: Move the orphaned Room comment into `room_manifests()`.** Delete the comment block at lines 138-152 (from `# The Room's own role. Its cc:74 lane is driven two ways now: by any` through `# proves (see design spec section 9).`), leaving `roles = {"player": player, "jammer": jammer}` directly after the jammer `Role(...)`. Add this docstring as the first line of `room_manifests`:

```python
    def room_manifests(self) -> tuple[dict, dict]:
        """The Room's own light and drone. Its cc:74 lane is driven two
        ways: by any player's tilt (the declared tilt_hue stream function)
        and by this Bit's own "drift" GENERATOR function, so the Room
        animates whether or not anyone has joined. A field-rate gesture,
        like the player's aurora: no note lane, so it renders continuously
        under cc:74 without a note-triggered strobe. Deliberately no
        cc:11/level lane (unlike player): breath-feeding the Room is a
        real, separable enhancement, not needed to prove the Room renders
        at all. The instrument is `rainbow`, not `aurora`: a scrolling hue
        gradient across the Room's whole concatenated surface, which makes
        the cross-fixture property (one declaration, one gradient spanning
        every fixture) the thing the reference fixture visibly proves (see
        design spec section 9)."""
```

- [ ] **Step 4: Fix the flash_device condition text** (line 242). Replace `description="Two-tap on the device",` with `description="Any tap on the device",`.

- [ ] **Step 5: Verify no behavior changed**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 2729 + the 11 new tests from Tasks 1-2 = 2740 passed, 1 skipped. Also run `git diff --stat bits/test/` and confirm only `test_bit.py` changed.

- [ ] **Step 6: Commit**

```bash
git add bits/test/test_bit.py
git commit -m "docs(bits): correct TestBit's stale room count, Room comment and tap condition text"
```

---

### Task 4: Deep-dive entry for MinigameBit

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (new section directly after the `### bits/rev1/` section, before the next `###` heading)

**Interfaces:** none.

- [ ] **Step 1: Add the section**

Insert after the last bullet of `### \`bits/rev1/\` -- Rev1Bit, the Rev 1 board bench check (2026-09-22)`:

```markdown
### `bits/minigame/` -- MinigameBit, a one-device bench toy (2026-09-24)
- **What it is.** Victor's Console-visible single-Tuneshroom Bit
  ("Minigame", TEST room) with a three-phase state machine: PENDING until
  a hold starts a round, INGAME while the LED blinks white 10 times 2 s
  apart (bit-adjudicated `blink` fires on its own grid through
  `FireFunction(at=...)`), then END. A tap resets to PENDING from any
  phase. No scoring; it never completes on its own.
- **Player kept across run start (2026-09-25).** As first checked in
  (`1ced5e9`), `on_run_start()` cleared the player, and joins land in
  SETUP before it runs, so in the normal lobby order the Bit ignored every
  gesture. It now keeps the joined player, like MetronomeBit.
  `tests/test_minigame_bit.py` pins both join orders end to end.
- **Hold needs a Rev 1 board or the sim.** The role `uses` hold, so
  `harness/o2_shroom.py` sends a long press as `/game/hold`; standard
  `tuneshroom`/`testshroom` hardware cannot send hold.
```

- [ ] **Step 2: Commit**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): MinigameBit entry; lobby-order player fix"
```

---

## Closeout

- Re-check `origin/main`'s tip before opening the PR (another session may have touched `bits/minigame/`).
- Push with `git push -u origin claude/minigame-fix-and-testbit-comments` (the branch was created tracking `origin/main`; `-u` repoints it).
- Open the PR, then run `finishing-a-development-branch`.
