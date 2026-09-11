# Lobby, join handshake, and admin start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the timed SETUP wait with an admin-started lobby that every Bit gets by default: an aurora and breathing warm pad while waiting, a lobby-initiated double-tap handshake, a per-join ceremony (device green double flash, Room bell up the A major scale, device chime), a silent green pulse when full, and one start authority reached from a web URL (QR/NFC), the device wire, the Console, or the uplink.

**Architecture:** A pure `control/lobby.py` holds the lobby state machine, the start rule, and the small schedulers. `GameServer.request_start` is the single start authority; three thin adapters (www route, `/game/start` verb, Console/uplink `run`) call it. A `devicelink/lobby_runtime.py` `LobbyRuntime`, owned by `DeviceLinkAgent` and driven through injected sinks, does all light and sound: fixture manifest swap, hue drift and breath feed, the lobby drone, the join ceremony, the invite flashes, and the start feedback flashes.

**Tech Stack:** Python 3.12 stdlib (`tomllib`, `http.server`, `queue`), luxaeterna (dev/test dependency, only under `harness/`, `devicelink/agent.py` and the frames tests), pytest, node for the Console JS tests.

**Spec:** `docs/superpowers/specs/2026-09-11-metronome-lobby-and-admin-start-design.md` (read it first; the plan argues from it).

## Global Constraints

- Run the suite through the project venv only: `.venv/bin/python -m pytest tests -q`. In a fresh worktree first `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`.
- `control/` never imports luxaeterna, pyarco, or o2litepy (pinned by test). `control/lobby.py` is pure stdlib.
- A test double must never be more permissive than the library it stands for (boundary rule 5).
- Every outbound JSON payload goes through `control/wire_json.dumps()`; never bare `json.dumps`.
- Reserved dev id `"terrarium"` (`control.lobby.TERRARIUM_ADMIN`) is always admin and is refused as a hello'd dev.
- No em dashes anywhere in prose, comments, or docs.
- Commit after every task with a conventional-commit subject; no attribution lines.
- Timings from the spec, verbatim: invite re-flash 5.0 s; ceremony gap 1.0 s; double-tap window 1.5 s; device flash 0.2 s on / 0.2 s off, twice; bell at `at + 0.8`, 1.0 s long; device chime sent at `at + 1.8`; fixture flash 0.25 s on / 0.25 s off; scale A4 B4 C#5 D5 E5 F#5 G#5 = MIDI 69 71 73 74 76 78 80, wrapping every seven.

---

### Task 1: `control/lobby.py`, the pure lobby core

**Files:**
- Create: `control/lobby.py`
- Test: `tests/test_lobby.py`

**Interfaces:**
- Consumes: `control/roles.py` `RoleTable`/`Role` (`role.scored`), `control/registration.py` `RegistrationState.counts()` shape `[(name, count, capacity | None)]`.
- Produces (used by Tasks 2, 4, 6, 7, 9, 10, 11):
  - `TERRARIUM_ADMIN: str = "terrarium"`
  - `NOTE_SCALE: tuple[int, ...]`, `scale_note(join_index: int) -> int`
  - `LobbyConfig` frozen dataclass (`enabled`, `invite_interval_s`, `ceremony_gap_s`, `double_tap_window_s`), `DEFAULT_LOBBY`
  - `LobbyState` enum `WAITING`/`FULL`, `lobby_state(counts, role_table) -> LobbyState`
  - `hue_drift_cc(t: float, period: float = HUE_DRIFT_PERIOD_S) -> int`
  - `lobby_light_manifest() -> dict`
  - `StartDecision(accepted, reason, feedback)`, `decide_start(...)`, feedback constants `FEEDBACK_NONE/ACCEPT/MINIMUM/REFUSED`
  - `StartRequested(source, source_dev, accepted, reason, feedback)` observer record
  - `StartRequest(key, dev, source)` (the www queue item)
  - `DoubleTapDetector(window_s)`: `observe(dev, count, stamp) -> bool`, `forget(dev)`
  - `InviteSchedule(interval_s)`: `due(dev, now) -> bool`, `invited(dev) -> bool`, `forget(dev)`, `clear()`
  - `CeremonySlots(span_s, gap_s)`: `reserve(now) -> float`
  - constants `LOBBY_PROGRAM=89`, `LOBBY_DRONE_KEY=48`, `LOBBY_DRONE_VEL=80`, `BELL_PROGRAM=14`, `BELL_VEL=100`, `BELL_OFFSET_S=0.8`, `BELL_DURATION_S=1.0`, `CHIME_OFFSET_S=1.8`, `CEREMONY_SPAN_S=1.8`, `DEVICE_FLASH_ON_S=0.2`, `DEVICE_FLASH_GAP_S=0.2`, `FIXTURE_FLASH_ON_S=0.25`, `FIXTURE_FLASH_GAP_S=0.25`, `HUE_CC=74`, `GREEN_HUE_CC=42`, `HUE_DRIFT_PERIOD_S=20.0`, `GREEN=(0,255,0)`, `RED=(255,0,0)`, `WHITE=(255,255,255)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lobby.py
"""control/lobby.py: the pure lobby core (spec section 2, 5)."""
from control.lobby import (
    CeremonySlots, DEFAULT_LOBBY, DoubleTapDetector, FEEDBACK_ACCEPT,
    FEEDBACK_MINIMUM, FEEDBACK_NONE, FEEDBACK_REFUSED, InviteSchedule,
    LobbyConfig, LobbyState, NOTE_SCALE, TERRARIUM_ADMIN, decide_start,
    hue_drift_cc, lobby_light_manifest, lobby_state, scale_note)
from control.roles import Role, RoleClass, RoleTable


def _table(**roles):
    return RoleTable(roles={n: r for n, r in roles.items()}, node_map={})


def _role(name, *, scored, capacity):
    return Role(name=name, role_class=RoleClass.UNIQUE, capacity=capacity,
                scored=scored)


def test_reserved_admin_id_and_defaults():
    assert TERRARIUM_ADMIN == "terrarium"
    assert DEFAULT_LOBBY == LobbyConfig(enabled=True, invite_interval_s=5.0,
                                        ceremony_gap_s=1.0,
                                        double_tap_window_s=1.5)


def test_scale_climbs_a_major_and_wraps_every_seven():
    assert NOTE_SCALE == (69, 71, 73, 74, 76, 78, 80)
    assert [scale_note(i) for i in range(9)] == [69, 71, 73, 74, 76, 78, 80, 69, 71]


def test_full_when_every_capped_scored_role_is_at_capacity():
    table = _table(player=_role("player", scored=True, capacity=2),
                   jammer=_role("jammer", scored=False, capacity=None))
    assert lobby_state([("player", 1, 2), ("jammer", 5, None)], table) is LobbyState.WAITING
    assert lobby_state([("player", 2, 2), ("jammer", 0, None)], table) is LobbyState.FULL


def test_uncapped_scored_role_never_fills():
    table = _table(player=_role("player", scored=True, capacity=None))
    assert lobby_state([("player", 40, None)], table) is LobbyState.WAITING


def test_no_scored_roles_is_waiting_not_full():
    table = _table(jammer=_role("jammer", scored=False, capacity=None))
    assert lobby_state([("jammer", 0, None)], table) is LobbyState.WAITING


def test_count_for_a_role_missing_from_the_table_is_ignored():
    table = _table(player=_role("player", scored=True, capacity=1))
    assert lobby_state([("player", 1, 1), ("room_test", 1, 1)], table) is LobbyState.FULL


def test_hue_drift_is_a_triangle_over_the_period():
    assert hue_drift_cc(0.0) == 0
    assert hue_drift_cc(10.0) == 127
    assert hue_drift_cc(20.0) == 0
    assert hue_drift_cc(5.0) == 64


def test_lobby_light_manifest_drives_hue_and_level_lanes():
    m = lobby_light_manifest()
    decl = m["instruments"][0]
    assert decl["instrument"] == "aurora" and decl["target"] == "primary"
    assert "level" in decl["params"]
    assert {(l["source"], l["dest"]) for l in decl["lanes"]} == {("cc:74", "hue"), ("cc:11", "level")}


def _decide(**kw):
    base = dict(bit_loaded=True, in_setup=True, when="admin",
                expected_key="k", key="k", admin=False, scored=2, min_scored=2)
    base.update(kw)
    return decide_start(**base)


def test_start_rule_no_bit_is_silent():
    d = _decide(bit_loaded=False)
    assert (d.accepted, d.reason, d.feedback) == (False, "no Bit loaded", FEEDBACK_NONE)


def test_start_rule_bad_key_is_silent():
    d = _decide(key="wrong")
    assert (d.accepted, d.reason, d.feedback) == (False, "bad key", FEEDBACK_NONE)
    d = _decide(expected_key=None)
    assert d.reason == "bad key"


def test_start_rule_keyed_start_on_a_non_admin_bit_is_refused_silently():
    d = _decide(when="players")
    assert d.reason == "Bit does not take an admin start"
    assert d.feedback == FEEDBACK_NONE


def test_start_rule_unkeyed_operator_start_works_on_any_bit():
    d = _decide(when="players", key=None, admin=True)
    assert d.accepted and d.feedback == FEEDBACK_ACCEPT


def test_start_rule_valid_key_outside_setup_gives_three_red():
    d = _decide(in_setup=False)
    assert (d.accepted, d.reason, d.feedback) == (False, "not in SETUP", FEEDBACK_REFUSED)
    d = _decide(in_setup=False, key=None, admin=True)
    assert d.feedback == FEEDBACK_NONE


def test_start_rule_admin_overrides_the_minimum():
    d = _decide(admin=True, scored=0)
    assert d.accepted and d.feedback == FEEDBACK_ACCEPT


def test_start_rule_minimum_not_met_gives_two_red():
    d = _decide(scored=1)
    assert (d.accepted, d.reason, d.feedback) == (False, "minimum not met", FEEDBACK_MINIMUM)


def test_start_rule_zero_minimum_allows_an_empty_start():
    d = _decide(scored=0, min_scored=0)
    assert d.accepted


def test_double_tap_from_count_two_or_two_taps_in_window():
    det = DoubleTapDetector(1.5)
    assert det.observe("d", 2, 10.0) is True
    assert det.observe("d", 1, 20.0) is False
    assert det.observe("d", 1, 21.4) is True
    assert det.observe("d", 1, 30.0) is False
    assert det.observe("d", 1, 31.6) is False       # outside the window
    assert det.observe("d", 1, 31.7) is True        # but paired with 31.6
    det.forget("d")
    assert det.observe("d", 1, 40.0) is False


def test_invite_schedule_flashes_now_then_every_interval():
    inv = InviteSchedule(5.0)
    assert inv.invited("d") is False
    assert inv.due("d", 100.0) is True
    assert inv.invited("d") is True
    assert inv.due("d", 103.0) is False
    assert inv.due("d", 105.0) is True
    inv.forget("d")
    assert inv.invited("d") is False
    inv.due("e", 1.0)
    inv.clear()
    assert inv.invited("e") is False


def test_ceremony_slots_space_joins_by_span_plus_gap():
    slots = CeremonySlots(span_s=1.8, gap_s=1.0)
    assert slots.reserve(100.0) == 100.0
    assert slots.reserve(100.1) == 102.8
    assert slots.reserve(200.0) == 200.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lobby.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.lobby'`

- [ ] **Step 3: Write `control/lobby.py`**

```python
"""The lobby: what the Terrarium does in SETUP while it waits for an
admin start (spec docs/superpowers/specs/2026-09-11-metronome-lobby-and-
admin-start-design.md, sections 2 and 5).

Pure stdlib. Holds the lobby state (WAITING / FULL), the start rule, the
note scale, and the three small schedulers the device-link runtime drives.
Nothing here touches a session, a voice, or the wire; devicelink/
lobby_runtime.py does that through injected sinks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

# The Terrarium's own admin identity: always in the effective admin set,
# never removable by config, and refused as a hello'd dev id.
TERRARIUM_ADMIN = "terrarium"

# A major from A4, wrapping every seven joins (spec section 2).
NOTE_SCALE = (69, 71, 73, 74, 76, 78, 80)

# Lobby sound: the warm pad drone (General MIDI 89, the program TestBit's
# Room already uses) and the bell on a transient voice (GM 14, tubular
# bells).
LOBBY_PROGRAM = 89
LOBBY_DRONE_KEY = 48
LOBBY_DRONE_VEL = 80
BELL_PROGRAM = 14
BELL_VEL = 100

# Join ceremony offsets from the ceremony's start time `at` (spec 4).
DEVICE_FLASH_ON_S = 0.2
DEVICE_FLASH_GAP_S = 0.2
BELL_OFFSET_S = 0.8
BELL_DURATION_S = 1.0
CHIME_OFFSET_S = 1.8
CEREMONY_SPAN_S = CHIME_OFFSET_S

# Fixture feedback flashes (accept / refusal) and invites.
FIXTURE_FLASH_ON_S = 0.25
FIXTURE_FLASH_GAP_S = 0.25

HUE_CC = 74
GREEN_HUE_CC = 42            # aurora hue ~0.33
HUE_DRIFT_PERIOD_S = 20.0

GREEN = (0, 255, 0)
RED = (255, 0, 0)
WHITE = (255, 255, 255)

FEEDBACK_NONE = "none"
FEEDBACK_ACCEPT = "accept"
FEEDBACK_MINIMUM = "minimum"
FEEDBACK_REFUSED = "refused"


@dataclass(frozen=True)
class LobbyConfig:
    enabled: bool = True
    invite_interval_s: float = 5.0
    ceremony_gap_s: float = 1.0
    double_tap_window_s: float = 1.5


DEFAULT_LOBBY = LobbyConfig()


class LobbyState(Enum):
    WAITING = auto()
    FULL = auto()


def lobby_state(counts, role_table) -> LobbyState:
    """FULL when every scored role with a finite capacity is at capacity
    and at least one such role exists; an uncapped scored role means the
    lobby can never fill. `counts` is RegistrationState.counts()."""
    capped = False
    for name, count, capacity in counts:
        role = role_table.roles.get(name)
        if role is None or not role.scored:
            continue
        if capacity is None:
            return LobbyState.WAITING
        capped = True
        if count < capacity:
            return LobbyState.WAITING
    return LobbyState.FULL if capped else LobbyState.WAITING


def scale_note(join_index: int) -> int:
    return NOTE_SCALE[join_index % len(NOTE_SCALE)]


def hue_drift_cc(t: float, period: float = HUE_DRIFT_PERIOD_S) -> int:
    """A slow triangle over cc range, so the aurora's hue sweeps the wheel
    and back once per period."""
    phase = (t % period) / period
    tri = 2.0 * phase if phase < 0.5 else 2.0 * (1.0 - phase)
    return round(tri * 127)


def lobby_light_manifest() -> dict:
    """What every fixture renders while waiting: an aurora whose hue rides
    cc:74 (the drift) and whose level rides cc:11 (the breath). Declaring
    `level` opts aurora out of its private breathing clock, so light and
    sound read one number."""
    return {
        "instruments": [
            {"instrument": "aurora", "target": "primary",
             "params": {"hue": 0.55, "level": 0.55},
             "lanes": [{"source": "cc:74", "dest": "hue"},
                       {"source": "cc:11", "dest": "level"}]},
        ],
    }


@dataclass(frozen=True)
class StartDecision:
    accepted: bool
    reason: str | None
    feedback: str


def decide_start(*, bit_loaded: bool, in_setup: bool, when: str | None,
                 expected_key: str | None, key: str | None, admin: bool,
                 scored: int, min_scored: int) -> StartDecision:
    """The start rule (spec section 2). `key is None` means an unkeyed
    operator surface (Console, uplink); a keyed source is judged against
    the Bit's key before anything else so a stranger with an old poster
    gets no room reaction at all."""
    if not bit_loaded:
        return StartDecision(False, "no Bit loaded", FEEDBACK_NONE)
    keyed = key is not None
    if keyed:
        if when != "admin":
            return StartDecision(False, "Bit does not take an admin start",
                                 FEEDBACK_NONE)
        if not expected_key or key != expected_key:
            return StartDecision(False, "bad key", FEEDBACK_NONE)
    if not in_setup:
        return StartDecision(False, "not in SETUP",
                             FEEDBACK_REFUSED if keyed else FEEDBACK_NONE)
    if admin:
        return StartDecision(True, None, FEEDBACK_ACCEPT)
    if min_scored > 0 and scored < min_scored:
        return StartDecision(False, "minimum not met", FEEDBACK_MINIMUM)
    return StartDecision(True, None, FEEDBACK_ACCEPT)


@dataclass(frozen=True)
class StartRequested:
    """The engine observer record for every start attempt."""
    source: str
    source_dev: str | None
    accepted: bool
    reason: str | None
    feedback: str


@dataclass(frozen=True)
class StartRequest:
    """One queued web start (harness/www_server.py -> DeviceLinkAgent)."""
    key: str
    dev: str | None
    source: str


class DoubleTapDetector:
    """Two taps from one device within the window, or one tap carrying
    count >= 2, is a double tap."""

    def __init__(self, window_s: float) -> None:
        self._window = window_s
        self._last: dict[str, float] = {}

    def observe(self, dev: str, count: int, stamp: float) -> bool:
        if count >= 2:
            self._last.pop(dev, None)
            return True
        prev = self._last.get(dev)
        self._last[dev] = stamp
        if prev is not None and 0.0 <= stamp - prev <= self._window:
            self._last.pop(dev, None)
            return True
        return False

    def forget(self, dev: str) -> None:
        self._last.pop(dev, None)


class InviteSchedule:
    """When to (re)flash an invite: now on first sight, then every
    interval while the device stays un-joined."""

    def __init__(self, interval_s: float) -> None:
        self._interval = interval_s
        self._last: dict[str, float] = {}

    def due(self, dev: str, now: float) -> bool:
        last = self._last.get(dev)
        if last is None or now - last >= self._interval:
            self._last[dev] = now
            return True
        return False

    def invited(self, dev: str) -> bool:
        return dev in self._last

    def forget(self, dev: str) -> None:
        self._last.pop(dev, None)

    def clear(self) -> None:
        self._last.clear()


class CeremonySlots:
    """Serialises join ceremonies: each reserve() returns the start time
    of the next one, span plus gap after the previous."""

    def __init__(self, span_s: float, gap_s: float) -> None:
        self._span = span_s
        self._gap = gap_s
        self._next_free = 0.0

    def reserve(self, now: float) -> float:
        at = max(now, self._next_free)
        self._next_free = at + self._span + self._gap
        return at
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lobby.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add control/lobby.py tests/test_lobby.py
git commit -m "feat(lobby): pure lobby core: state, start rule, scale, schedulers"
```

---

### Task 2: manifest `[start] when = "admin"` + `key`, `[lobby]` table, `start_decision` for admin

**Files:**
- Modify: `control/bit_config.py` (lines 17-21 constants, 59-64 `StartCondition`, 89-103 `BitConfig`, 259-295 `_parse_start`, 336-400 `parse_manifest`, 403-411 `_OVERRIDE_TABLES`)
- Modify: `control/start_condition.py` (`start_decision`)
- Test: `tests/test_bit_config.py`, `tests/test_start_condition.py`

**Interfaces:**
- Consumes: `control.lobby.LobbyConfig`, `DEFAULT_LOBBY`.
- Produces: `StartCondition.key: str | None` (new field, default `None`); `BitConfig.lobby: LobbyConfig` (default `DEFAULT_LOBBY`); `_START_WHEN` includes `"admin"`; `_parse_lobby(raw, *, source) -> LobbyConfig`; `start_decision` returns `None` for admin unless the timeout has elapsed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bit_config.py` (it already defines `MINIMAL`, a minimal valid manifest text, and imports `parse_manifest`, `ManifestError`, `merge_overrides`; reuse them):

```python
from control.lobby import DEFAULT_LOBBY, LobbyConfig


def test_admin_start_requires_a_key():
    with pytest.raises(ManifestError) as err:
        parse_manifest(MINIMAL + "[start]\nwhen='admin'\n", source="t")
    assert err.value.key == "start.key"


def test_admin_start_parses_key_and_defaults_min_scored_to_zero():
    cfg = parse_manifest(MINIMAL + "[start]\nwhen='admin'\nkey='metro-dev'\n",
                         source="t")
    assert cfg.start.when == "admin"
    assert cfg.start.key == "metro-dev"
    assert cfg.start.min_scored == 0
    assert cfg.start.timeout_seconds is None


def test_admin_start_refuses_a_negative_minimum_but_allows_zero():
    parse_manifest(MINIMAL + "[start]\nwhen='admin'\nkey='k'\nmin_scored=0\n",
                   source="t")
    with pytest.raises(ManifestError) as err:
        parse_manifest(MINIMAL + "[start]\nwhen='admin'\nkey='k'\nmin_scored=-1\n",
                       source="t")
    assert err.value.key == "start.min_scored"


def test_key_is_ignored_with_a_warning_on_a_non_admin_start(caplog):
    cfg = parse_manifest(MINIMAL + "[start]\nwhen='players'\nkey='k'\n",
                         source="t")
    assert cfg.start.key is None


def test_lobby_table_defaults_and_parses():
    cfg = parse_manifest(MINIMAL, source="t")
    assert cfg.lobby == DEFAULT_LOBBY
    cfg = parse_manifest(MINIMAL + "[lobby]\nenabled=false\ninvite_interval_s=7\n"
                         "ceremony_gap_s=0.5\ndouble_tap_window_s=2\n", source="t")
    assert cfg.lobby == LobbyConfig(enabled=False, invite_interval_s=7.0,
                                    ceremony_gap_s=0.5, double_tap_window_s=2.0)


def test_lobby_table_refuses_wrong_types():
    with pytest.raises(ManifestError) as err:
        parse_manifest(MINIMAL + "[lobby]\nenabled='yes'\n", source="t")
    assert err.value.key == "lobby.enabled"


def test_start_key_and_lobby_ride_merge_overrides():
    cfg = parse_manifest(MINIMAL + "[start]\nwhen='admin'\nkey='k'\n", source="t")
    merged = merge_overrides(cfg, {"start": {"key": "venue-9"},
                                   "lobby": {"invite_interval_s": 3}}, source="p")
    assert merged.start.key == "venue-9"
    assert merged.lobby.invite_interval_s == 3.0
```

Append to `tests/test_start_condition.py`:

```python
def test_admin_never_self_starts_without_a_timeout():
    cond = StartCondition(when="admin", min_scored=0, key="k")
    assert start_decision(cond, scored=5, elapsed=9999.0, setup_seconds=0.0) is None


def test_admin_timeout_applies_on_timeout():
    cond = StartCondition(when="admin", min_scored=0, key="k",
                          timeout_seconds=30.0, on_timeout="abort")
    assert start_decision(cond, scored=0, elapsed=29.0, setup_seconds=0.0) is None
    assert start_decision(cond, scored=0, elapsed=30.0, setup_seconds=0.0) == "abort"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py tests/test_start_condition.py -q`
Expected: the new tests FAIL (`start.when must be one of [...]`, `TypeError: unexpected keyword 'key'`, `AttributeError: lobby`).

- [ ] **Step 3: Implement in `control/bit_config.py`**

At the top imports add `from control.lobby import DEFAULT_LOBBY, LobbyConfig`.

Change the constant at line 19: `_START_WHEN = frozenset({"immediate", "players", "operator", "admin"})`.

Add `key: str | None = None` as the last field of `StartCondition`.

Add `lobby: LobbyConfig = DEFAULT_LOBBY` to `BitConfig` after `ambient` (before `extras`).

Replace `_parse_start` with:

```python
def _parse_start(raw: dict, *, source: str) -> StartCondition:
    known = {"when", "min_scored", "timeout_seconds", "on_timeout", "key"}
    _warn_unknown_keys(raw, known, source=source, prefix="start")

    when = _get(raw, "when", str, "immediate", source=source, prefix="start")
    if when == "scheduled":
        raise ManifestError(
            source=source, key="start.when",
            message="scheduled start conditions are reserved for a later slice")
    if when not in _START_WHEN:
        raise ManifestError(source=source, key="start.when",
                             message=f"must be one of {sorted(_START_WHEN)}")

    admin = when == "admin"
    key = _get(raw, "key", str, None, source=source, prefix="start")
    if admin and not key:
        raise ManifestError(source=source, key="start.key",
                             message="required non-empty string when when = 'admin'")
    if not admin and key is not None:
        logger.warning("%s: [start.key] ignored: only an admin start takes a key",
                       source)
        key = None

    min_scored = _get(raw, "min_scored", int, 0 if admin else 1,
                      source=source, prefix="start")
    if min_scored < (0 if admin else 1):
        raise ManifestError(source=source, key="start.min_scored",
                             message="must be >= 0" if admin else "must be >= 1")

    on_timeout = _get(raw, "on_timeout", str, "start", source=source,
                       prefix="start")
    if on_timeout not in _ON_TIMEOUT:
        raise ManifestError(source=source, key="start.on_timeout",
                             message=f"must be one of {sorted(_ON_TIMEOUT)}")

    timeout_seconds = raw.get("timeout_seconds")
    if timeout_seconds is not None and not _is_number(timeout_seconds):
        raise ManifestError(
            source=source, key="start.timeout_seconds",
            message=f"expected float, got {type(timeout_seconds).__name__}")

    return StartCondition(
        when=when,
        min_scored=min_scored,
        timeout_seconds=(float(timeout_seconds)
                          if timeout_seconds is not None else None),
        on_timeout=on_timeout,
        key=key,
    )


def _parse_lobby(raw: dict, *, source: str) -> LobbyConfig:
    known = {"enabled", "invite_interval_s", "ceremony_gap_s",
             "double_tap_window_s"}
    _warn_unknown_keys(raw, known, source=source, prefix="lobby")
    enabled = _get(raw, "enabled", bool, True, source=source, prefix="lobby")
    out = {}
    for name in ("invite_interval_s", "ceremony_gap_s", "double_tap_window_s"):
        value = raw.get(name, getattr(DEFAULT_LOBBY, name))
        if not _is_number(value) or value < 0:
            raise ManifestError(source=source, key=f"lobby.{name}",
                                 message="expected a non-negative number")
        out[name] = float(value)
    return LobbyConfig(enabled=enabled, **out)
```

Note: `_get` with `default=None` for `key` must accept a `None` default; check `_get`'s body (line 141-164): it returns `default` when the key is missing and type-checks only a present value, so `None` is fine.

In `parse_manifest`: add `"lobby"` to `_KNOWN_TOP_TABLES`; after `console = _parse_console(...)` add `lobby = _parse_lobby(doc.get("lobby", {}), source=source)`; pass `lobby=lobby` into the `BitConfig(...)` construction.

In `_OVERRIDE_TABLES` add `"lobby": ("lobby", _parse_lobby, None),`. `_to_raw` needs no change (field names equal TOML keys).

- [ ] **Step 4: Implement in `control/start_condition.py`**

Add before the final `return None` in `start_decision`:

```python
    if cond.when == "admin":
        if cond.timeout_seconds is not None and elapsed >= cond.timeout_seconds:
            return cond.on_timeout
        return None
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py tests/test_start_condition.py tests/test_bit_registry.py -q`
Expected: all pass (the existing `min_scored = 0` refusal for `players` at `tests/test_bit_config.py:90` still passes).

- [ ] **Step 6: Commit**

```bash
git add control/bit_config.py control/start_condition.py tests/test_bit_config.py tests/test_start_condition.py
git commit -m "feat(config): admin start condition with key, and the [lobby] table"
```

---

### Task 3: `[admin] devices` in the terrarium config

**Files:**
- Modify: `control/terrarium_config.py` (`TerrariumConfig` lines 44-65, `parse_terrarium_config` lines 115-161)
- Test: `tests/test_terrarium_config.py`

**Interfaces:**
- Consumes: `control.lobby.TERRARIUM_ADMIN`.
- Produces: `TerrariumConfig.admin_devices: tuple[str, ...] = ()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_config.py` (it already has a minimal config text helper; if none named `MINIMAL_CONFIG` exists, define one with `schema = 1`, `[terrarium] name = "t"`, and one inline `[rooms.TEST]` copied from `tests/test_terrarium_config.py`'s existing smallest valid room text):

```python
def test_admin_devices_default_empty_and_parse():
    cfg = parse_terrarium_config(MINIMAL_CONFIG, "t")
    assert cfg.admin_devices == ()
    cfg = parse_terrarium_config(
        MINIMAL_CONFIG + "\n[admin]\ndevices = [\"gem-1\", \"gem-2\"]\n", "t")
    assert cfg.admin_devices == ("gem-1", "gem-2")


def test_admin_devices_refuses_non_strings_and_the_reserved_id():
    with pytest.raises(TerrariumConfigError) as err:
        parse_terrarium_config(MINIMAL_CONFIG + "\n[admin]\ndevices = [1]\n", "t")
    assert err.value.key == "admin.devices"
    with pytest.raises(TerrariumConfigError) as err:
        parse_terrarium_config(
            MINIMAL_CONFIG + "\n[admin]\ndevices = [\"terrarium\"]\n", "t")
    assert err.value.key == "admin.devices"
    assert "always" in str(err.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q -k admin`
Expected: FAIL with `AttributeError: ... 'admin_devices'`.

- [ ] **Step 3: Implement**

In `control/terrarium_config.py`: import `from control.lobby import TERRARIUM_ADMIN`; add field `admin_devices: tuple[str, ...] = ()` to `TerrariumConfig` (after `room_roots`). In `parse_terrarium_config`, after `bit_paths = ...` (line 133) add:

```python
    admin_raw = raw.get("admin", {})
    devices_raw = admin_raw.get("devices", []) if isinstance(admin_raw, dict) else []
    if not isinstance(devices_raw, list) or not all(
            isinstance(d, str) and d for d in devices_raw):
        raise TerrariumConfigError(source=source, key="admin.devices",
                                   message="expected a list of non-empty strings")
    if TERRARIUM_ADMIN in devices_raw:
        raise TerrariumConfigError(
            source=source, key="admin.devices",
            message=f"{TERRARIUM_ADMIN!r} is the Terrarium itself and is always "
                    f"an admin; do not list it")
    admin_devices = tuple(devices_raw)
```

and pass `admin_devices=admin_devices` into the `TerrariumConfig(...)` return. `load_terrarium_config`'s `replace(config, ...)` carries it through unchanged.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add control/terrarium_config.py tests/test_terrarium_config.py
git commit -m "feat(config): [admin] devices list in terrarium.toml"
```

---

### Task 4: engine: `is_admin`, `request_start`, `lobby_state`, `notify_lobby`

**Files:**
- Modify: `control/engine.py` (`__init__` line 86, new methods near `run()` line 459)
- Test: `tests/test_engine_start.py` (new)

**Interfaces:**
- Consumes: `control.lobby.decide_start/StartRequested/TERRARIUM_ADMIN/lobby_state/DEFAULT_LOBBY/FEEDBACK_REFUSED`, `control.start_condition.scored_count`.
- Produces:
  - `GameServer.__init__(..., admin_devices: Iterable[str] = ())`, attribute `self.admin_devices: frozenset[str]`
  - `GameServer.is_admin(dev: str | None) -> bool`
  - `GameServer.request_start(key: str | None, source_dev: str | None, source: str) -> str | None`
  - observer event `on_start_requested(record: StartRequested)`
  - `GameServer.lobby_config() -> LobbyConfig`
  - `GameServer.lobby_state() -> str | None` (`"WAITING"`, `"FULL"`, or `None` outside SETUP or when the lobby is disabled)
  - `GameServer.notify_lobby(event: str, dev: str) -> None` firing observer `on_lobby_event(event, dev)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine_start.py
"""GameServer.request_start: the single start authority (spec section 2)."""
from dataclasses import replace

from bits.test.test_bit import TestBit
from control.bit_registry import BitRegistry
from control.engine import GameServer
from control.lobby import (FEEDBACK_ACCEPT, FEEDBACK_MINIMUM, FEEDBACK_NONE,
                           FEEDBACK_REFUSED, TERRARIUM_ADMIN)
from control.state import State
from pathlib import Path


class _Observer:
    def __init__(self):
        self.starts = []
        self.lobby = []

    def on_start_requested(self, record):
        self.starts.append(record)

    def on_lobby_event(self, event, dev):
        self.lobby.append((event, dev))


def _admin_config():
    """TestBit's manifest, forced to an admin start with key 'k' and a
    minimum of 1 scored player."""
    registry = BitRegistry.scan([Path("bits")])
    cfg = registry.resolve_config("TestBit")
    return replace(cfg, start=replace(cfg.start, when="admin", key="k",
                                      min_scored=1))


def _loaded(config=None, admin_devices=()):
    gs = GameServer({"TestBit": TestBit}, admin_devices=admin_devices)
    obs = _Observer()
    gs.add_observer(obs)
    gs.load_bit("TestBit", config=config)
    return gs, obs


def test_terrarium_is_always_admin_and_config_adds():
    gs = GameServer({"TestBit": TestBit}, admin_devices=["gem-1"])
    assert gs.is_admin(TERRARIUM_ADMIN)
    assert gs.is_admin("gem-1")
    assert not gs.is_admin("ie1")
    assert not gs.is_admin(None)


def test_no_bit_loaded_is_refused_silently():
    gs = GameServer({"TestBit": TestBit})
    obs = _Observer()
    gs.add_observer(obs)
    assert gs.request_start(None, TERRARIUM_ADMIN, "console") == "no Bit loaded"
    assert obs.starts[-1].feedback == FEEDBACK_NONE
    assert gs.state is State.IDLE


def test_console_start_on_a_non_admin_bit_runs_like_before():
    gs, obs = _loaded()                       # TestBit, no config: immediate
    assert gs.request_start(None, TERRARIUM_ADMIN, "console") is None
    assert gs.state is State.RUNNING
    assert obs.starts[-1].accepted and obs.starts[-1].feedback == FEEDBACK_ACCEPT


def test_keyed_start_on_a_non_admin_bit_is_refused():
    gs, obs = _loaded()
    assert gs.request_start("k", "ie1", "device:ie1") == "Bit does not take an admin start"
    assert gs.state is State.SETUP


def test_bad_key_is_silent_and_logged_as_a_record():
    gs, obs = _loaded(_admin_config())
    assert gs.request_start("nope", "ie1", "device:ie1") == "bad key"
    assert obs.starts[-1].feedback == FEEDBACK_NONE
    assert obs.starts[-1].source == "device:ie1"


def test_minimum_not_met_then_met():
    gs, obs = _loaded(_admin_config())
    assert gs.request_start("k", "ie1", "device:ie1") == "minimum not met"
    assert obs.starts[-1].feedback == FEEDBACK_MINIMUM
    gs.hello("ie1", "sim", "1")
    assert gs.join("ie1", "TEST_PLAYER_NODE").granted
    assert gs.request_start("k", "ie1", "device:ie1") is None
    assert gs.state is State.RUNNING


def test_admin_device_overrides_the_minimum():
    gs, obs = _loaded(_admin_config(), admin_devices=["gem-1"])
    assert gs.request_start("k", "gem-1", "device:gem-1") is None
    assert gs.state is State.RUNNING


def test_valid_key_outside_setup_is_refused_with_feedback():
    gs, obs = _loaded(_admin_config())
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING
    assert gs.request_start("k", "ie1", "device:ie1") == "not in SETUP"
    assert obs.starts[-1].feedback == FEEDBACK_REFUSED


def test_lobby_state_follows_registration_and_setup():
    gs, obs = _loaded(_admin_config())
    assert gs.lobby_state() == "WAITING"      # TestBit's player is uncapped
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.lobby_state() is None


def test_lobby_state_is_none_when_disabled():
    cfg = _admin_config()
    cfg = replace(cfg, lobby=replace(cfg.lobby, enabled=False))
    gs, obs = _loaded(cfg)
    assert gs.lobby_state() is None
    assert gs.lobby_config().enabled is False


def test_notify_lobby_reaches_observers():
    gs, obs = _loaded()
    gs.notify_lobby("invite", "ie1")
    assert obs.lobby == [("invite", "ie1")]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_engine_start.py -q`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'admin_devices'`.

- [ ] **Step 3: Implement in `control/engine.py`**

Imports (top of file): `from control.lobby import (DEFAULT_LOBBY, FEEDBACK_REFUSED, StartRequested, TERRARIUM_ADMIN, decide_start, lobby_state as _lobby_state)` and `from control.start_condition import scored_count`.

`__init__` signature becomes:

```python
    def __init__(self, bit_registry: dict, room_binding=None,
                 cue_horizon: float = 0.0, clock=time.monotonic,
                 carried_instruments: dict | None = None,
                 admin_devices=()):
```

and after `self.carried_instruments = {...}` add:

```python
        # The Terrarium itself is always an admin (spec section 3); the
        # config list only adds to it.
        self.admin_devices: frozenset[str] = frozenset(admin_devices) | {TERRARIUM_ADMIN}
```

Add these methods right after `run()`:

```python
    def is_admin(self, dev: str | None) -> bool:
        return dev is not None and dev in self.admin_devices

    def lobby_config(self):
        cfg = getattr(self.bit, "config", None) if self.bit is not None else None
        return getattr(cfg, "lobby", None) or DEFAULT_LOBBY

    def lobby_state(self) -> str | None:
        """'WAITING' / 'FULL' while a lobby is active in SETUP, else None."""
        if self.state is not State.SETUP or self.registration is None:
            return None
        if not self.lobby_config().enabled:
            return None
        return _lobby_state(self.registration.counts(),
                            self.registration.role_table).name

    def notify_lobby(self, event: str, dev: str) -> None:
        """Let the device-link layer announce a lobby event (invite,
        handshake) through the engine's observer list, so the Console can
        log it without observing the transport."""
        self._notify("on_lobby_event", event, dev)

    def request_start(self, key: str | None, source_dev: str | None,
                      source: str) -> str | None:
        """The single start authority (spec section 2). Never raises; a
        refusal is a reason string. Fires on_start_requested for every
        attempt, after any state change it caused."""
        cfg = getattr(self.bit, "config", None) if self.bit is not None else None
        cond = getattr(cfg, "start", None)
        decision = decide_start(
            bit_loaded=self.bit is not None,
            in_setup=self.state is State.SETUP,
            when=cond.when if cond is not None else "immediate",
            expected_key=getattr(cond, "key", None),
            key=key,
            admin=self.is_admin(source_dev),
            scored=scored_count(self) if self.registration is not None else 0,
            min_scored=cond.min_scored if cond is not None else 0)
        accepted, reason, feedback = (decision.accepted, decision.reason,
                                      decision.feedback)
        if accepted:
            try:
                self.run()
            except InvalidTransition as exc:
                accepted, reason, feedback = False, str(exc), FEEDBACK_REFUSED
        self._notify("on_start_requested", StartRequested(
            source=source, source_dev=source_dev, accepted=accepted,
            reason=reason, feedback=feedback))
        return reason
```

`scored_count(self)` reads `gs.bit.role_table` and `gs.registration.counts()`; both exist whenever `self.registration is not None`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_engine_start.py tests/test_engine.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_engine_start.py
git commit -m "feat(engine): request_start authority, admin identity, lobby state"
```

---

### Task 5: `AudioBridge.play_note` and `set_control`; the bell program

**Files:**
- Modify: `control/audio.py` (`WELCOME_INSTRUMENTS` line 37, `_play_welcome` line 161, new methods)
- Test: `tests/test_audio.py`

**Interfaces:**
- Produces: `WELCOME_INSTRUMENTS["bell"] = (14, 69, 100)`; `AudioBridge.play_note(program: int, key: int, vel: int, duration: float) -> None` (transient voice, note off after `duration`, same `_pending_offs` path as a welcome); `AudioBridge.set_control(dev: str, cc: int, value: int) -> None` (writes the cc to the granted voice, bypassing the role's lane map; silent no-op for an unknown dev).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_audio.py`)

```python
from control.audio import WELCOME_INSTRUMENTS


def test_bell_is_a_known_welcome_instrument():
    assert WELCOME_INSTRUMENTS["bell"] == (14, 69, 100)


def test_play_note_uses_a_transient_voice_and_frees_it_after_duration():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([10.0, 10.5, 11.0, 11.5]))
    br.play_note(14, 71, 100, 1.0)                     # clock 10.0
    voice = pool.acquired[0]
    assert voice.sent[:2] == [("program", 14), ("note_on", 71, 100)]
    br.tick()                                          # clock 10.5: still sounding
    assert pool.released == []
    br.tick()                                          # clock 11.0: due
    assert ("note_off", 71) in voice.sent
    assert pool.released == [voice]


def test_set_control_bypasses_the_lane_map():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0] * 4))
    role = _role(ugens={"instruments": [{"instrument": "flsyn", "program": 89,
                                         "lanes": [{"source": "cc:74", "dest": "cc:74"}]}]})
    br.on_grant("fx", role)
    voice = pool.acquired[0]
    br.feed_midi("fx", 0xB0, 11, 100)                  # undeclared lane: dropped
    assert ("cc", 11, 100) not in voice.sent
    br.set_control("fx", 11, 100)
    assert ("cc", 11, 100) in voice.sent
    br.set_control("nobody", 11, 1)                    # unknown dev: no-op
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_audio.py -q -k "bell or play_note or set_control"`
Expected: FAIL (`KeyError: 'bell'`, `AttributeError: play_note`).

- [ ] **Step 3: Implement**

In `control/audio.py`:

```python
WELCOME_INSTRUMENTS: dict[str, tuple[int, int, int]] = {
    "chime": (9, 84, 88),        # 9 = Glockenspiel (General MIDI)
    "bell": (14, 69, 100),       # 14 = Tubular Bells; the lobby's join bell
}
```

Refactor `_play_welcome` to end with `self.play_note(program, key, vel, duration)` instead of the four acquire/program/note_on/append lines, and add:

```python
    def play_note(self, program: int, key: int, vel: int,
                  duration: float) -> None:
        """One note on its own transient voice, released `duration`
        seconds later by tick(). The welcome ceremony and the lobby bell
        both ride this so neither disturbs a sustained drone."""
        voice = self._pool.acquire()
        voice.program_change(int(program))
        voice.note_on(int(key), int(vel))
        self._pending_offs.append((self._clock() + float(duration), voice, int(key)))

    def set_control(self, dev: str, cc: int, value: int) -> None:
        """Write a controller straight to dev's voice, ignoring the role's
        lane map. The lobby breath uses this: a Bit's ROOM ugen need not
        declare cc:11 for the lobby to swell its pad."""
        entry = self._devices.get(dev)
        if entry is None:
            return
        entry.voice.control_change(int(cc), int(value))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_audio.py -q`
Expected: pass (the existing welcome tests still see two acquires, program then note_on on the second voice).

- [ ] **Step 5: Commit**

```bash
git add control/audio.py tests/test_audio.py
git commit -m "feat(audio): play_note transient voice, set_control, bell program"
```

---

### Task 6: `devicelink/lobby_runtime.py`: the lobby's light and sound, driven through sinks

**Files:**
- Create: `devicelink/lobby_runtime.py`
- Test: `tests/test_lobby_runtime.py`

**Interfaces:**
- Consumes: `control.lobby` (everything), `control.breath.breath_cc/BREATH_CC`, `control.timed_queue.TimedQueue`.
- Produces:
  - `LobbySinks` dataclass of callables: `fixture_names() -> list[str]`, `bound_dev(fixture_name) -> str | None`, `feed_light(fixture_name, status, d1, d2)`, `feed_audio(fixture_name, status, d1, d2)`, `set_audio_control(fixture_name, cc, value)`, `play_note(program, key, vel, duration)`, `set_override(dev, rgb, level, duration)` (applies now, expires after `duration`), `send_play(dev, name, params)`, `request_join(dev, client)`, `announce(event, dev)`.
  - `LobbyRuntime(config: LobbyConfig, sinks: LobbySinks, clock, *, room_program: int | None)` with `start()`, `stop()`, `set_state(LobbyState)`, `state` property, `tick()`, `on_scored_join(dev)`, `feedback(feedback: str)`, `consider_invite(dev)`, `is_invited(dev)`, `forget(dev)`, `observe_tap(dev, count, stamp, client) -> bool`, `join_count` property.
  - Module function `flash_fixtures(runtime, rgb, count)` is internal; feedback is via `runtime.feedback(...)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lobby_runtime.py
"""LobbyRuntime through fake sinks: no luxaeterna, no Arco (spec 4, 5)."""
from control.breath import BREATH_CC, breath_cc
from control.lobby import (BELL_PROGRAM, BELL_VEL, DEFAULT_LOBBY, FEEDBACK_ACCEPT,
                           FEEDBACK_MINIMUM, FEEDBACK_NONE, FEEDBACK_REFUSED,
                           GREEN, GREEN_HUE_CC, HUE_CC, LOBBY_DRONE_KEY,
                           LOBBY_PROGRAM, LobbyState, RED, WHITE, hue_drift_cc)
from devicelink.lobby_runtime import LobbyRuntime, LobbySinks


class _Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _Sinks:
    def __init__(self, fixtures=("main", "accent"), bound=None):
        self.fixtures = list(fixtures)
        self.bound = dict(bound or {"main": "sim-main", "accent": "sim-accent"})
        self.light = []      # (fixture, status, d1, d2)
        self.audio = []      # (fixture, status, d1, d2)
        self.controls = []   # (fixture, cc, value)
        self.notes = []      # (program, key, vel, duration)
        self.overrides = []  # (t, dev, rgb, level, duration)
        self.plays = []      # (dev, name, params)
        self.joins = []      # (dev, client)
        self.events = []     # (event, dev)
        self.t = 0.0

    def as_sinks(self):
        return LobbySinks(
            fixture_names=lambda: list(self.fixtures),
            bound_dev=lambda name: self.bound.get(name),
            feed_light=lambda f, s, a, b: self.light.append((f, s, a, b)),
            feed_audio=lambda f, s, a, b: self.audio.append((f, s, a, b)),
            set_audio_control=lambda f, cc, v: self.controls.append((f, cc, v)),
            play_note=lambda p, k, v, d: self.notes.append((p, k, v, d)),
            set_override=lambda dev, rgb, lvl, dur: self.overrides.append(
                (self.t, dev, rgb, lvl, dur)),
            send_play=lambda dev, n, p: self.plays.append((dev, n, p)),
            request_join=lambda dev, client: self.joins.append((dev, client)),
            announce=lambda ev, dev: self.events.append((ev, dev)),
        )


def _rt(clock=None, sinks=None, config=DEFAULT_LOBBY, room_program=115):
    clock = clock or _Clock()
    sinks = sinks or _Sinks()
    rt = LobbyRuntime(config, sinks.as_sinks(), clock, room_program=room_program)
    return rt, sinks, clock


def _run(rt, sinks, clock, seconds, dt=1 / 44):
    for _ in range(int(seconds / dt)):
        clock.advance(dt)
        sinks.t = clock.t
        rt.tick()


def test_start_sets_the_pad_and_sounds_the_drone_on_every_fixture():
    rt, sinks, clock = _rt()
    rt.start()
    for name in ("main", "accent"):
        assert (name, 0xC0, LOBBY_PROGRAM, 0) in sinks.audio
        assert (name, 0x90, LOBBY_DRONE_KEY, 80) in sinks.audio


def test_tick_feeds_drift_and_breath_to_light_and_breath_to_audio():
    rt, sinks, clock = _rt()
    rt.start()
    rt.tick()
    assert ("main", 0xB0, HUE_CC, hue_drift_cc(0.0)) in sinks.light
    assert ("main", 0xB0, BREATH_CC, breath_cc(0.0)) in sinks.light
    assert ("main", BREATH_CC, breath_cc(0.0)) in sinks.controls
    before = len(sinks.light)
    rt.tick()                                   # same clock: nothing new
    assert len(sinks.light) == before
    _run(rt, sinks, clock, 2.0)
    hues = [d2 for (f, s, d1, d2) in sinks.light if f == "main" and d1 == HUE_CC]
    assert len(set(hues)) > 3                   # the drift moves


def test_full_pins_green_stops_the_drone_and_keeps_the_breath_on_light_only():
    rt, sinks, clock = _rt()
    rt.start()
    _run(rt, sinks, clock, 0.5)
    rt.set_state(LobbyState.FULL)
    assert ("main", 0x80, LOBBY_DRONE_KEY, 0) in sinks.audio
    assert ("main", 0xB0, HUE_CC, GREEN_HUE_CC) in sinks.light
    n_light, n_ctrl = len(sinks.light), len(sinks.controls)
    _run(rt, sinks, clock, 3.0)
    hues = [d2 for (f, s, d1, d2) in sinks.light[n_light:] if d1 == HUE_CC]
    assert hues == []                           # no drift while FULL
    assert any(d1 == BREATH_CC for (f, s, d1, d2) in sinks.light[n_light:])
    assert len(sinks.controls) == n_ctrl        # breath no longer reaches audio
    rt.set_state(LobbyState.WAITING)
    assert sinks.audio.count(("main", 0x90, LOBBY_DRONE_KEY, 80)) == 2


def test_stop_silences_and_restores_the_bits_program():
    rt, sinks, clock = _rt(room_program=115)
    rt.start()
    rt.stop()
    for name in ("main", "accent"):
        assert (name, 0x80, LOBBY_DRONE_KEY, 0) in sinks.audio
        assert (name, 0xC0, 115, 0) in sinks.audio
    n = len(sinks.light)
    _run(rt, sinks, clock, 1.0)
    assert len(sinks.light) == n                # stopped: nothing fed


def test_join_ceremony_flashes_bells_and_chimes_in_order():
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_scored_join("ie1")
    rt.tick()
    t0 = clock.t
    assert sinks.overrides[-1][1:] == ("ie1", GREEN, 1.0, 0.2)
    _run(rt, sinks, clock, 0.45)
    greens = [o for o in sinks.overrides if o[1] == "ie1"]
    assert len(greens) == 2 and abs(greens[1][0] - (t0 + 0.4)) < 0.03
    assert sinks.notes == []
    _run(rt, sinks, clock, 0.4)                 # past 0.8
    assert sinks.notes == [(BELL_PROGRAM, 69, BELL_VEL, 1.0)]
    assert sinks.plays == []
    _run(rt, sinks, clock, 1.0)                 # past 1.8
    assert sinks.plays == [("ie1", "chime", "key=69")]
    assert rt.join_count == 1


def test_second_join_climbs_the_scale_and_waits_its_turn():
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_scored_join("ie1")
    rt.on_scored_join("ie2")                    # same instant
    _run(rt, sinks, clock, 5.0)
    assert [n[1] for n in sinks.notes] == [69, 71]
    bells = [n for n in sinks.notes]
    assert len(bells) == 2
    # second ceremony starts span (1.8) + gap (1.0) after the first
    ie2_first = [o for o in sinks.overrides if o[1] == "ie2"][0][0]
    ie1_first = [o for o in sinks.overrides if o[1] == "ie1"][0][0]
    assert abs((ie2_first - ie1_first) - 2.8) < 0.03


def test_feedback_flashes_fixtures_green_once_red_twice_or_thrice():
    rt, sinks, clock = _rt()
    rt.start()
    rt.feedback(FEEDBACK_ACCEPT)
    _run(rt, sinks, clock, 2.0)
    assert [o[1:4] for o in sinks.overrides] == [
        ("sim-main", GREEN, 1.0), ("sim-accent", GREEN, 1.0)]
    sinks.overrides.clear()
    rt.feedback(FEEDBACK_MINIMUM)
    _run(rt, sinks, clock, 2.0)
    assert [o[2] for o in sinks.overrides].count(RED) == 4      # 2 flashes x 2 fixtures
    times = sorted({round(o[0], 2) for o in sinks.overrides})
    assert abs(times[1] - times[0] - 0.5) < 0.03
    sinks.overrides.clear()
    rt.feedback(FEEDBACK_REFUSED)
    _run(rt, sinks, clock, 2.0)
    assert [o[2] for o in sinks.overrides].count(RED) == 6
    sinks.overrides.clear()
    rt.feedback(FEEDBACK_NONE)
    _run(rt, sinks, clock, 1.0)
    assert sinks.overrides == []


def test_unbound_fixture_gets_no_flash():
    rt, sinks, clock = _rt(sinks=_Sinks(bound={"main": "sim-main"}))
    rt.start()
    rt.feedback(FEEDBACK_ACCEPT)
    _run(rt, sinks, clock, 1.0)
    assert [o[1] for o in sinks.overrides] == ["sim-main"]


def test_invite_flashes_white_twice_and_repeats_every_interval():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    assert rt.is_invited("ie3")
    assert sinks.events == [("invite", "ie3")]
    _run(rt, sinks, clock, 1.0)
    whites = [o for o in sinks.overrides if o[1] == "ie3"]
    assert [o[2:] for o in whites] == [(WHITE, 1.0, 0.2), (WHITE, 1.0, 0.2)]
    for _ in range(4 * 44):
        clock.advance(1 / 44)
        sinks.t = clock.t
        rt.consider_invite("ie3")
        rt.tick()
    assert len([o for o in sinks.overrides if o[1] == "ie3"]) == 2   # not yet 5 s
    for _ in range(2 * 44):
        clock.advance(1 / 44)
        sinks.t = clock.t
        rt.consider_invite("ie3")
        rt.tick()
    assert len([o for o in sinks.overrides if o[1] == "ie3"]) == 4


def test_no_invites_while_full():
    rt, sinks, clock = _rt()
    rt.start()
    rt.set_state(LobbyState.FULL)
    rt.consider_invite("ie3")
    assert not rt.is_invited("ie3")


def test_double_tap_from_an_invited_device_requests_the_join():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    assert rt.observe_tap("ie3", 1, 50.0, "c3") is False
    assert rt.observe_tap("ie3", 1, 51.0, "c3") is True
    assert sinks.joins == [("ie3", "c3")]
    assert ("handshake", "ie3") in sinks.events
    assert rt.observe_tap("ie9", 2, 60.0, "c9") is False     # never invited
    rt.forget("ie3")
    assert not rt.is_invited("ie3")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_lobby_runtime.py -q`
Expected: FAIL with `ModuleNotFoundError: devicelink.lobby_runtime`.

- [ ] **Step 3: Write `devicelink/lobby_runtime.py`**

```python
"""LobbyRuntime: the lobby's light and sound, driven through sinks.

Owned by DeviceLinkAgent for the duration of one SETUP (spec sections 4
and 5). It never touches a session, a voice, or the wire directly: every
effect goes through a LobbySinks callable the agent supplies, so this
module is testable with plain lists and never imports luxaeterna.

Timing: all timed effects sit in one TimedQueue of thunks drained by
tick(); the join ceremony and the feedback flashes are pure schedules
over the spec's offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from control.breath import BREATH_CC, breath_cc
from control.lobby import (BELL_DURATION_S, BELL_OFFSET_S, BELL_PROGRAM,
                           BELL_VEL, CEREMONY_SPAN_S, CHIME_OFFSET_S,
                           DEVICE_FLASH_GAP_S, DEVICE_FLASH_ON_S,
                           FEEDBACK_ACCEPT, FEEDBACK_MINIMUM,
                           FEEDBACK_REFUSED, FIXTURE_FLASH_GAP_S,
                           FIXTURE_FLASH_ON_S, GREEN, GREEN_HUE_CC, HUE_CC,
                           LOBBY_DRONE_KEY, LOBBY_DRONE_VEL, LOBBY_PROGRAM,
                           RED, WHITE, CeremonySlots, DoubleTapDetector,
                           InviteSchedule, LobbyConfig, LobbyState,
                           hue_drift_cc, scale_note)
from control.timed_queue import TimedQueue


@dataclass
class LobbySinks:
    fixture_names: Callable[[], list]
    bound_dev: Callable[[str], str | None]
    feed_light: Callable[[str, int, int, int], None]
    feed_audio: Callable[[str, int, int, int], None]
    set_audio_control: Callable[[str, int, int], None]
    play_note: Callable[[int, int, int, float], None]
    set_override: Callable[[str, tuple, float, float], None]
    send_play: Callable[[str, str, str], None]
    request_join: Callable[[str, object], None]
    announce: Callable[[str, str], None]


class LobbyRuntime:
    def __init__(self, config: LobbyConfig, sinks: LobbySinks, clock, *,
                 room_program: int | None) -> None:
        self._cfg = config
        self._s = sinks
        self._clock = clock
        self._room_program = room_program
        self._state = LobbyState.WAITING
        self._running = False
        self._origin = clock()
        self._queue = TimedQueue()
        self._slots = CeremonySlots(CEREMONY_SPAN_S, config.ceremony_gap_s)
        self._invites = InviteSchedule(config.invite_interval_s)
        self._taps = DoubleTapDetector(config.double_tap_window_s)
        self._last_light: dict[tuple[str, int], int] = {}
        self._last_audio: dict[str, int] = {}
        self._joins = 0

    # --- lifecycle -----------------------------------------------------
    @property
    def state(self) -> LobbyState:
        return self._state

    @property
    def join_count(self) -> int:
        return self._joins

    def start(self) -> None:
        self._running = True
        self._origin = self._clock()
        for name in self._s.fixture_names():
            self._s.feed_audio(name, 0xC0, LOBBY_PROGRAM, 0)
        self._drone(True)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._drone(False)
        if self._room_program is not None:
            for name in self._s.fixture_names():
                self._s.feed_audio(name, 0xC0, int(self._room_program), 0)
        self._queue = TimedQueue()
        self._invites.clear()

    def set_state(self, state: LobbyState) -> None:
        if state is self._state:
            return
        self._state = state
        if state is LobbyState.FULL:
            self._drone(False)
            for name in self._s.fixture_names():
                self._light(name, HUE_CC, GREEN_HUE_CC)
        else:
            self._drone(True)

    def _drone(self, on: bool) -> None:
        for name in self._s.fixture_names():
            if on and self._state is LobbyState.WAITING:
                self._s.feed_audio(name, 0x90, LOBBY_DRONE_KEY, LOBBY_DRONE_VEL)
            elif not on:
                self._s.feed_audio(name, 0x80, LOBBY_DRONE_KEY, 0)

    # --- per tick ------------------------------------------------------
    def tick(self) -> None:
        now = self._clock()
        for thunk in self._queue.due(now):
            thunk()
        if not self._running:
            return
        t = now - self._origin
        breath = breath_cc(t)
        drift = hue_drift_cc(t)
        for name in self._s.fixture_names():
            if self._state is LobbyState.WAITING:
                self._light(name, HUE_CC, drift)
                if self._last_audio.get(name) != breath:
                    self._last_audio[name] = breath
                    self._s.set_audio_control(name, BREATH_CC, breath)
            self._light(name, BREATH_CC, breath)

    def _light(self, name: str, cc: int, value: int) -> None:
        if self._last_light.get((name, cc)) == value:
            return
        self._last_light[(name, cc)] = value
        self._s.feed_light(name, 0xB0, cc, value)

    def _at(self, when: float, thunk) -> None:
        self._queue.push(when, thunk, now=self._clock())

    # --- join ceremony (spec 4) ----------------------------------------
    def on_scored_join(self, dev: str) -> None:
        key = scale_note(self._joins)
        self._joins += 1
        at = self._slots.reserve(self._clock())
        for i in range(2):
            t = at + i * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
            self._at(t, lambda d=dev: self._s.set_override(d, GREEN, 1.0,
                                                           DEVICE_FLASH_ON_S))
        self._at(at + BELL_OFFSET_S,
                 lambda k=key: self._s.play_note(BELL_PROGRAM, k, BELL_VEL,
                                                 BELL_DURATION_S))
        self._at(at + CHIME_OFFSET_S,
                 lambda d=dev, k=key: self._s.send_play(d, "chime", f"key={k}"))

    # --- start feedback (spec 2) ---------------------------------------
    def feedback(self, feedback: str) -> None:
        count, rgb = {FEEDBACK_ACCEPT: (1, GREEN), FEEDBACK_MINIMUM: (2, RED),
                      FEEDBACK_REFUSED: (3, RED)}.get(feedback, (0, GREEN))
        self._flash_fixtures(rgb, count)

    def _flash_fixtures(self, rgb: tuple, count: int) -> None:
        now = self._clock()
        devs = [d for d in (self._s.bound_dev(n) for n in self._s.fixture_names())
                if d is not None]
        for i in range(count):
            t = now + i * (FIXTURE_FLASH_ON_S + FIXTURE_FLASH_GAP_S)
            for dev in devs:
                self._at(t, lambda d=dev: self._s.set_override(
                    d, rgb, 1.0, FIXTURE_FLASH_ON_S))

    # --- handshake (spec 5) --------------------------------------------
    def consider_invite(self, dev: str) -> None:
        if self._state is not LobbyState.WAITING:
            return
        first = not self._invites.invited(dev)
        if not self._invites.due(dev, self._clock()):
            return
        if first:
            self._s.announce("invite", dev)
        now = self._clock()
        for i in range(2):
            t = now + i * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
            self._at(t, lambda d=dev: self._s.set_override(d, WHITE, 1.0,
                                                           DEVICE_FLASH_ON_S))

    def is_invited(self, dev: str) -> bool:
        return self._invites.invited(dev)

    def forget(self, dev: str) -> None:
        self._invites.forget(dev)
        self._taps.forget(dev)

    def observe_tap(self, dev: str, count: int, stamp: float, client) -> bool:
        if not self._invites.invited(dev):
            return False
        if not self._taps.observe(dev, count, stamp):
            return False
        self._s.announce("handshake", dev)
        self._s.request_join(dev, client)
        return True
```

Note on `feedback` after an accepted start: the agent tears the runtime down on RUNNING before `on_start_requested` fires, so Task 7 keeps a second, agent-level flash path for the accept case (see Task 7 step 3, `_flash_fixtures_now`). `LobbyRuntime.feedback` still handles every refusal, which always happens while the runtime is alive.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_lobby_runtime.py -q`
Expected: pass. If `test_stop_silences_and_restores_the_bits_program` is brittle on ordering, assert membership only (both `0x80` note off and `0xC0 115` present for both fixtures).

- [ ] **Step 5: Commit**

```bash
git add devicelink/lobby_runtime.py tests/test_lobby_runtime.py
git commit -m "feat(devicelink): LobbyRuntime: drift, breath, drone, ceremony, invites, feedback"
```

---

### Task 7: wire the lobby into `DeviceLinkAgent`, the `start` verb, the hello guard, and the transport

**Files:**
- Modify: `devicelink/agent.py` (`__init__` 85-226, `_setup_room` 228-277, `poll` 469-488, `_handle` 765-793, `_on_hello` 818-824, `_on_join` 826-863, `_on_verb` 865-877, `on_state_change` 880-942, `on_registration_change` 1116-1117)
- Modify: `devicelink/o2_transport.py` line 108 (`GAME_VERBS`)
- Modify: `tests/test_devicelink_agent.py` (`FakeFixtureSession` gains `swap`; `_FakeAudioBridge` gains `play_note`, `set_control`)
- Test: `tests/test_lobby_agent.py` (new)

**Interfaces:**
- Consumes: Task 4 (`gs.request_start`, `gs.lobby_state`, `gs.lobby_config`, `gs.notify_lobby`), Task 6 (`LobbyRuntime`, `LobbySinks`), Task 1 (`TERRARIUM_ADMIN`, `LobbyState`, `lobby_light_manifest`, `StartRequest`).
- Produces: `DeviceLinkAgent.start_requests: queue.Queue | None` (set by the harness; drained each poll); observer method `on_start_requested(record)`; `/game/start "ss" dev key` handling; tap interception for invited devices; `"start"` in `GAME_VERBS`.

- [ ] **Step 1: Extend the test doubles in `tests/test_devicelink_agent.py`**

In `FakeFixtureSession` add:

```python
    def swap(self, manifest):
        self.manifest = manifest
        self.swaps = getattr(self, "swaps", []) + [manifest]
```

In `_FakeAudioBridge` add `self.notes = []` and `self.controls = []` to `__init__`, plus:

```python
    def play_note(self, program, key, vel, duration):
        self.notes.append((program, key, vel, duration))

    def set_control(self, dev, cc, value):
        self.controls.append((dev, cc, value))
```

- [ ] **Step 2: Write the failing agent tests**

```python
# tests/test_lobby_agent.py
"""DeviceLinkAgent drives the lobby (spec 4, 5) and routes start (spec 3).
Offline: FakeFixtureSession for light, _FakeAudioBridge for sound."""
import queue
from dataclasses import replace
from pathlib import Path

from bits.test.test_bit import TestBit
from control.bit_registry import BitRegistry
from control.breath import BREATH_CC
from control.engine import GameServer
from control.lobby import (GREEN, HUE_CC, LOBBY_DRONE_KEY, LOBBY_PROGRAM, RED,
                           StartRequest, TERRARIUM_ADMIN, WHITE)
from control.rooms import Room
from control.state import State
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import (FakeServer, TEST_PROFILE, _Clock,
                                         _FakeAudioBridge, _fake_sessions)


def _admin_cfg(**start):
    registry = BitRegistry.scan([Path("bits")])
    cfg = registry.resolve_config("TestBit")
    fields = {"when": "admin", "key": "k", "min_scored": 1}
    fields.update(start)
    return replace(cfg, start=replace(cfg.start, **fields))


def _rig(monkeypatch, config=None, admin_devices=()):
    clk = _Clock(100.0)
    gs = GameServer({"TestBit": TestBit}, clock=clk, admin_devices=admin_devices)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.room.bound["main"] = "sim-main"
    gs.room.bound["accent"] = "sim-accent"
    audio = _FakeAudioBridge()
    sessions = _fake_sessions(monkeypatch)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_audio=audio, clock=clk)
    gs.load_bit("TestBit", config=config)
    return gs, server, agent, audio, sessions, clk


def _poll(agent, clk, seconds, dt=1 / 44):
    for _ in range(int(seconds / dt)):
        clk.advance(dt)
        agent.poll()


def _hello(server, agent, client, dev):
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
    agent.poll()


def test_setup_swaps_fixtures_to_the_lobby_manifest_and_sounds_the_pad(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    main = sessions[f"{TEST_PROFILE.surface_id}_main"]
    assert main.manifest.instruments[0].instrument == "aurora"
    assert {(l.source, l.dest) for l in main.manifest.instruments[0].lanes} == {
        ("cc:74", "hue"), ("cc:11", "level")}
    assert ("main", 0xC0, LOBBY_PROGRAM, 0) in audio.fed
    assert ("main", 0x90, LOBBY_DRONE_KEY, 80) in audio.fed
    _poll(agent, clk, 0.5)
    assert any(d1 == HUE_CC for (s, d1, d2) in main.fed)
    assert any(d1 == BREATH_CC for (s, d1, d2) in main.fed)
    assert any(cc == BREATH_CC for (name, cc, v) in audio.controls)


def test_lobby_is_off_when_the_bit_opts_out(monkeypatch):
    cfg = _admin_cfg()
    cfg = replace(cfg, lobby=replace(cfg.lobby, enabled=False))
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, cfg)
    assert ("main", 0x90, LOBBY_DRONE_KEY, 80) not in audio.fed
    _poll(agent, clk, 0.5)
    assert sessions[f"{TEST_PROFILE.surface_id}_main"].fed == []


def test_running_swaps_back_to_the_bits_room_declaration(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    main = sessions[f"{TEST_PROFILE.surface_id}_main"]
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING
    assert main.manifest.instruments[0].instrument == "rainbow"   # TestBit's ROOM
    assert ("main", 0x80, LOBBY_DRONE_KEY, 0) in audio.fed
    assert ("main", 0xC0, 89, 0) in audio.fed                    # TestBit's program restored
    # accept: one green flash on both fixtures
    agent.poll()
    assert agent._overrides["sim-main"][0] == GREEN
    assert agent._overrides["sim-accent"][0] == GREEN


def test_hello_invites_with_two_white_flashes_and_double_tap_joins(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    events = []
    class Obs:
        def on_lobby_event(self, event, dev):
            events.append((event, dev))
    gs.add_observer(Obs())
    _hello(server, agent, "c1", "ie1")
    assert agent._overrides["ie1"][0] == WHITE
    assert events == [("invite", "ie1")]
    _poll(agent, clk, 0.3)
    assert "ie1" not in agent._overrides                       # first flash over
    _poll(agent, clk, 0.15)
    assert agent._overrides["ie1"][0] == WHITE                 # second flash
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 1], timestamp=clk.t)
    agent.poll()
    assert "ie1" not in gs.registration.assignments
    clk.advance(0.5)
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 1], timestamp=clk.t)
    agent.poll()
    assert gs.registration.assignments["ie1"][1] == "player"
    assert ("handshake", "ie1") in events
    assert any(m["address"] == "/ie1/role" for (_d, m) in server.sent)


def test_invite_frames_reach_an_unjoined_device_in_grb_then_go_black(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    leds = [bytes(m["args"][0]) for (_d, m) in server.sent if m["address"] == "/ie1/leds"]
    assert leds[-1] == bytes([255, 255, 255] * 12)
    _poll(agent, clk, 0.3)
    leds = [bytes(m["args"][0]) for (_d, m) in server.sent if m["address"] == "/ie1/leds"]
    assert leds[-1] == bytes(36)                              # black once the flash expires
    # a green override on a GRB surface lands as G=255,R=0,B=0
    agent._on_solid_cue("ie1", GREEN, 1.0, 1.0, clk.t)
    agent.poll()
    leds = [bytes(m["args"][0]) for (_d, m) in server.sent if m["address"] == "/ie1/leds"]
    assert leds[-1][:3] == bytes([255, 0, 0])


def test_tap_from_an_uninvited_unjoined_device_is_still_refused(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    gs.request_start(None, TERRARIUM_ADMIN, "console")         # RUNNING: no lobby
    _hello(server, agent, "c1", "ie1")
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 1.0, 50.0, 2])
    agent.poll()
    assert "ie1" not in gs.registration.assignments
    assert any(m["address"] == "/ie1/error" for (_d, m) in server.sent)


def test_scored_join_runs_the_ceremony(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert agent._overrides["ie1"][0] == GREEN
    _poll(agent, clk, 1.0)
    assert audio.notes == [(14, 69, 100, 1.0)]
    _poll(agent, clk, 1.0)
    plays = [m for (_d, m) in server.sent if m["address"] == "/ie1/play"]
    assert plays[-1]["args"] == ["chime", "key=69"]


def test_full_lobby_pins_green_and_stops_the_drone(monkeypatch):
    cfg = _admin_cfg()
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, cfg)
    # cap TestBit's player at 1 for this test
    gs.registration.role_table.roles["player"].capacity = 1
    _hello(server, agent, "c1", "ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert gs.lobby_state() == "FULL"
    assert ("main", 0x80, LOBBY_DRONE_KEY, 0) in audio.fed
    assert (0xB0, HUE_CC, 42) in sessions[f"{TEST_PROFILE.surface_id}_main"].fed
    _hello(server, agent, "c2", "ie2")
    assert "ie2" not in agent._overrides                       # no invite while FULL


def test_start_verb_from_a_device_routes_to_request_start(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg(min_scored=0))
    _hello(server, agent, "c1", "gem-1")
    server.deliver("c1", "/game/start", "ss", ["gem-1", "wrong"])
    agent.poll()
    assert gs.state is State.SETUP
    assert any(m["address"] == "/gem-1/error" and m["args"] == ["start", "bad key"]
               for (_d, m) in server.sent)
    server.deliver("c1", "/game/start", "ss", ["gem-1", "k"])
    agent.poll()
    assert gs.state is State.RUNNING


def test_minimum_not_met_flashes_fixtures_red_twice(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    _hello(server, agent, "c1", "ie5")
    server.deliver("c1", "/game/start", "ss", ["ie5", "k"])
    agent.poll()
    assert gs.state is State.SETUP
    assert agent._overrides["sim-main"][0] == RED
    _poll(agent, clk, 0.3)
    assert "sim-main" not in agent._overrides
    _poll(agent, clk, 0.25)
    assert agent._overrides["sim-main"][0] == RED


def test_web_start_queue_is_drained_on_poll(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg(min_scored=0))
    q = queue.Queue()
    agent.start_requests = q
    q.put(StartRequest("k", TERRARIUM_ADMIN, "web:terrarium"))
    agent.poll()
    assert gs.state is State.RUNNING


def test_hello_as_terrarium_is_refused(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    server.arrive("c9")
    server.deliver("c9", "/game/hello", "sss", [TERRARIUM_ADMIN, "sim", "1"])
    agent.poll()
    assert gs.devices.get(TERRARIUM_ADMIN) is None
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_lobby_agent.py -q`
Expected: FAIL (manifest still TestBit's `rainbow`, no lobby drone, `AttributeError: start_requests`).

- [ ] **Step 4: Implement in `devicelink/agent.py`**

Imports: `import queue`; `from control.lobby import (LobbyState, StartRequest, TERRARIUM_ADMIN, lobby_light_manifest)`; `from devicelink.lobby_runtime import LobbyRuntime, LobbySinks`.

In `__init__`, before `self._setup_room()`:

```python
        # Lobby (spec 4, 5): alive only while the engine is in SETUP and
        # the loaded Bit's [lobby] enabled is true. Built by _enter_lobby.
        self._lobby: LobbyRuntime | None = None
        self._lobby_known: set[str] = set()
        # Web starts (harness/www_server.py) arrive on the server thread
        # and are drained here, on the tick thread, so the engine is only
        # ever touched from one thread.
        self.start_requests: queue.Queue | None = None
```

Factor the Bit-slice computation out of `_setup_room` into a helper and use it there:

```python
    def _bit_fixture_light(self, fixture_name: str) -> dict | None:
        """The loaded Bit's ROOM light declaration sliced for one fixture,
        or None when no Bit ROOM role is loaded."""
        gs = self.game_server
        room = gs.room
        if room is None or gs.registration is None:
            return None
        role = gs.registration.role_table.roles.get(room_role_name(room.name))
        if role is None:
            return None
        blob = compose_role_config(gs.bit_name, gs.bit.version, role)
        return slice_light_manifest(blob["light_manifest"], self._room_profile,
                                    fixture_name)

    def _bit_room_program(self) -> int | None:
        gs = self.game_server
        if gs.room is None or gs.registration is None:
            return None
        role = gs.registration.role_table.roles.get(room_role_name(gs.room.name))
        if role is None:
            return None
        instruments = role.ugen_manifest.get("instruments", [])
        if not instruments or instruments[0].get("program") is None:
            return None
        return int(instruments[0]["program"])
```

(`_setup_room` keeps building sessions the way it does; only the per-fixture `light = slice_light_manifest(...)` branch may call `_bit_fixture_light(fixture.name)` to avoid duplication.)

Add the lobby entry/exit and sinks:

```python
    def _enter_lobby(self) -> None:
        gs = self.game_server
        if self._lobby is not None or not self._fixtures or gs.room is None:
            return
        if not gs.lobby_config().enabled:
            return
        manifest = LightManifest.from_dict(lobby_light_manifest())
        for st in self._fixtures.values():
            st.session.swap(manifest)
        self._lobby = LobbyRuntime(gs.lobby_config(), self._lobby_sinks(),
                                   self._clock,
                                   room_program=self._bit_room_program())
        self._lobby_known = set(gs.registration.assignments) if gs.registration else set()
        self._lobby.start()
        self._sync_lobby_state()

    def _exit_lobby(self, *, restore_light: bool) -> None:
        lobby, self._lobby = self._lobby, None
        if lobby is None:
            return
        lobby.stop()
        if restore_light:
            for name, st in self._fixtures.items():
                light = self._bit_fixture_light(name)
                if light is not None:
                    st.session.swap(LightManifest.from_dict(light))

    def _sync_lobby_state(self) -> None:
        if self._lobby is None:
            return
        name = self.game_server.lobby_state()
        if name is not None:
            self._lobby.set_state(LobbyState[name])

    def _lobby_sinks(self) -> LobbySinks:
        gs = self.game_server

        def feed_light(name, status, d1, d2):
            st = self._fixtures.get(name)
            dev = gs.room.bound.get(name) if gs.room is not None else None
            if st is None or (dev is not None and dev in self._muted):
                return
            try:
                st.session.feed_midi(status, d1, d2)
            except Exception:
                logger.exception("lobby light feed for %s failed", name)

        def feed_audio(name, status, d1, d2):
            if self._room_audio is not None and name in self._room_audio_fixtures:
                self._room_audio.feed_midi(name, status, d1, d2)

        def set_audio_control(name, cc, value):
            if self._room_audio is not None and name in self._room_audio_fixtures:
                self._room_audio.set_control(name, cc, value)

        def play_note(program, key, vel, duration):
            if self._room_audio is not None:
                self._room_audio.play_note(program, key, vel, duration)

        def set_override(dev, rgb, level, duration):
            self._on_solid_cue(dev, rgb, level, duration, self._clock())

        def send_play(dev, name, params):
            if dev not in self._muted:
                self._send(dev, protocol.play_event(dev, name, params))

        return LobbySinks(
            fixture_names=lambda: list(self._fixtures),
            bound_dev=lambda name: (gs.room.bound.get(name)
                                    if gs.room is not None else None),
            feed_light=feed_light, feed_audio=feed_audio,
            set_audio_control=set_audio_control, play_note=play_note,
            set_override=set_override, send_play=send_play,
            request_join=self._handshake_join,
            announce=gs.notify_lobby)

    def _handshake_join(self, dev: str, client) -> None:
        node = self._default_scored_node()
        if node is None:
            logger.warning("handshake for %s: the Bit declares no scored node", dev)
            return
        self._on_join(client, dev, [dev, node])

    def _default_scored_node(self) -> str | None:
        """The node a handshake joins: the manifest's default_join_role,
        else the first manifest node whose role is scored, else the first
        role-table node whose first role is scored. Never a jam node."""
        gs = self.game_server
        table = gs.registration.role_table if gs.registration is not None else None
        cfg = getattr(gs.bit, "config", None) if gs.bit is not None else None
        if cfg is not None and cfg.launch.default_join_role:
            node = cfg.node_for(cfg.launch.default_join_role)
            if node is not None:
                return node
        if cfg is not None and table is not None:
            for role_name, node in cfg.launch.nodes:
                role = table.roles.get(role_name)
                if role is not None and role.scored:
                    return node
        if table is not None:
            for node, roles in table.node_map.items():
                if roles and table.roles[roles[0]].scored:
                    return node
        return None

    def _flash_fixtures_now(self, rgb, count: int) -> None:
        """Feedback flashes that outlive the lobby: the accept flash fires
        after RUNNING has already torn the runtime down."""
        gs = self.game_server
        if gs.room is None:
            return
        now = self._clock()
        for i in range(count):
            for name in self._fixtures:
                dev = gs.room.bound.get(name)
                if dev is None:
                    continue
                self._light_cues.push(
                    now + i * 0.5,
                    ("__flash__", dev, rgb, now + i * 0.5), now=now)

    def _drain_start_requests(self) -> None:
        q = self.start_requests
        if q is None:
            return
        while True:
            try:
                req = q.get_nowait()
            except queue.Empty:
                return
            self.game_server.request_start(req.key, req.dev, req.source)

    def _tick_lobby(self) -> None:
        lobby = self._lobby
        if lobby is None:
            return
        gs = self.game_server
        joined = set(gs.registration.assignments) if gs.registration else set()
        fixture_devs = set(gs.room.bound.values()) if gs.room is not None else set()
        for info in gs.devices.all():
            dev = info.dev
            if dev in joined or dev in fixture_devs or dev in self._closing:
                continue
            lobby.consider_invite(dev)
        lobby.tick()
```

`_flash_fixtures_now` rides `_light_cues` with a sentinel payload; extend `_drain_light_cues` so a payload whose first element is `"__flash__"` calls `self._on_solid_cue(dev, rgb, 1.0, 0.25, when)` instead of `_feed_light_now`:

```python
    def _drain_light_cues(self) -> None:
        for payload in self._light_cues.due(self._clock()):
            if payload[0] == "__flash__":
                _tag, dev, rgb, when = payload
                self._on_solid_cue(dev, rgb, 1.0, 0.25, when)
                continue
            dev, status, d1, d2, at = payload
            if dev in self._muted:
                continue
            self._feed_light_now(dev, status, d1, d2, at)
```

(The mute purge in `_on_mute_change` uses `payload[0] == dev`; a flash payload's `[0]` is the tag, so a muted fixture's pending flash is not purged. That is acceptable: `_apply_override` is what paints, and `_on_mute_change` installs its own latched blackout after; add a `dev in self._muted` guard in the `__flash__` branch to be safe.)

**Colour order for overrides.** `_apply_override` today repeats the
`rgb` bytes verbatim, which on a GRB surface paints green as red. Give it a
`color_order` parameter and reorder:

```python
    def _apply_override(self, dev: str, frame: bytes,
                        color_order: str = "GRB") -> bytes:
        entry = self._overrides.get(dev)
        if entry is None:
            return frame
        rgb, level, _expires = entry
        by_name = dict(zip("RGB", rgb))
        pixel = bytes(max(0, min(255, round(by_name[ch] * level)))
                      for ch in color_order[:3])
        reps = len(frame) // 3 + 1
        return (pixel * reps)[:len(frame)]
```

`_render_room` passes `fixture.color_order`; `_render_frames` passes
`self._capability.color_order if self._capability is not None else "GRB"`
(module constant `_DEVICE_CHANNELS = 36` replaces the literal `[:36]`).

**Un-joined devices render their override.** `_render_frames` only walks
`self.bridges`, so a device that has only hello'd (the one being invited)
never receives a frame. Append to `_render_frames`, after the bridges loop:

```python
        # A device with no session (hello'd, not joined: the one being
        # invited) renders only its override, and one black frame when the
        # override expires, so an invite is visible before any role exists.
        black = bytes(_DEVICE_CHANNELS)
        order = self._capability.color_order if self._capability is not None else "GRB"
        for dev in list(set(self._overrides) | set(self._last_frames)):
            if dev in self.bridges or self._fixture_for_dev(dev) is not None:
                continue
            frame = (self._apply_override(dev, black, order)
                     if dev in self._overrides else black)
            if self._last_frames.get(dev) == frame:
                continue
            self._last_frames[dev] = frame
            try:
                self._send(dev, protocol.leds_event(
                    dev, frame, when=self._clock() + self._horizon))
            except Exception:
                logger.exception("leds send for %s failed", dev)
            if frame == black:
                self._last_frames.pop(dev, None)
```

`poll()`: after `self._feed_breath()` insert `self._drain_start_requests()` and `self._tick_lobby()`.

`_handle`: change the last dispatch line to `self._on_verb(dev, verb, env.args, env.timestamp, client=client)`.

`_on_hello`: as the first statement:

```python
        if dev == TERRARIUM_ADMIN:
            logger.warning("hello from reserved dev id %r refused", dev)
            return
```

`_on_join`: after `self._closing_revived.discard(dev)` add `if self._lobby is not None: self._lobby.forget(dev)`.

`_on_verb` becomes:

```python
    def _on_verb(self, dev: str, verb: str, args: list,
                 gesture_time: float = 0.0, client=None) -> None:
        if verb == "start":
            key = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
            reason = self.game_server.request_start(key, dev, f"device:{dev}")
            if reason is not None:
                self._send(dev, protocol.error_event(dev, "start", reason))
            return
        if (verb == "tap" and self._lobby is not None
                and self._lobby.is_invited(dev)):
            count = int(args[3]) if len(args) > 3 else 1
            stamp = gesture_time if gesture_time and gesture_time > 0 else self._clock()
            self._lobby.observe_tap(dev, count, stamp, client)
            return
        reason = self.game_server.data(dev, verb, args,
                                       gesture_time=gesture_time)
        if reason is not None:
            self._send(dev, protocol.error_event(dev, verb, reason))
```

`on_state_change`: after the existing `if new_state in (State.LOADED, State.IDLE): self._setup_room()` block add:

```python
        if new_state == State.SETUP:
            self._enter_lobby()
        elif new_state == State.RUNNING:
            self._exit_lobby(restore_light=True)
        elif new_state in (State.UNLOADING, State.IDLE, State.LOADED):
            self._exit_lobby(restore_light=False)
```

(`_exit_lobby` on RUNNING must run before the existing `start_drone` loop, which it does since that loop is below; a Bit ROOM role with no drone key is unaffected.) Also in `unwire_room` call `self._exit_lobby(restore_light=False)` first.

`on_registration_change` becomes:

```python
    def on_registration_change(self) -> None:
        self._broadcast_room()
        lobby = self._lobby
        if lobby is None:
            return
        gs = self.game_server
        assignments = gs.registration.assignments if gs.registration else {}
        for dev in set(assignments) - self._lobby_known:
            _node, role_name, _cls = assignments[dev]
            role = gs.registration.role_table.roles.get(role_name)
            if role is not None and role.scored:
                lobby.forget(dev)
                lobby.on_scored_join(dev)
        self._lobby_known = set(assignments)
        self._sync_lobby_state()
```

Add the observer method:

```python
    def on_start_requested(self, record) -> None:
        if record.feedback == "accept":
            self._flash_fixtures_now(GREEN, 1)
        elif self._lobby is not None:
            self._lobby.feedback(record.feedback)
```

with `from control.lobby import GREEN` in the import line.

`devicelink/o2_transport.py` line 108: add `"start"` to `GAME_VERBS`.

- [ ] **Step 5: Run the new tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_lobby_agent.py -q`
Expected: pass.

Run: `.venv/bin/python -m pytest tests -q -x`
Expected: mostly green. Existing agent tests that enter SETUP with fixtures and assert an exact `FakeFixtureSession.fed` list, or `_FakeAudioBridge.fed`, will now see the lobby's initial cc:74 / cc:11 feed and the lobby program/note-on. Fix each by either loading the Bit with a config whose `lobby.enabled` is False (build one with `replace(cfg, lobby=replace(cfg.lobby, enabled=False))` from `BitRegistry.scan([Path("bits")]).resolve_config("TestBit")`) or by filtering the assertion to the cc it tests. Do not weaken tests that assert order of Bit-driven cues. Also `tests/test_console_agent.py::test_bad_command_sends_error_to_origin_only` still passes because the Console's Run path is unchanged until Task 10.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py devicelink/o2_transport.py tests/test_devicelink_agent.py tests/test_lobby_agent.py
git commit -m "feat(devicelink): lobby in SETUP, handshake taps, start verb, web start queue"
```

---

### Task 8: real-luxaeterna frame test for the lobby pixels

**Files:**
- Test: `tests/test_lobby_frames.py` (new)

**Interfaces:**
- Consumes: the rig shape of `tests/test_metronome_bit_live_frames.py` (`FakeServer`, `_Clock`, `DEMO_PROFILE`), Task 7's agent.

- [ ] **Step 1: Write the test**

```python
# tests/test_lobby_frames.py
"""The lobby's pixels, rendered by real luxaeterna sessions (spec 8)."""
import pytest

pytest.importorskip("luxaeterna")

from dataclasses import replace
from pathlib import Path

from bits.test.test_bit import TestBit
from control.bit_registry import BitRegistry
from control.engine import GameServer
from control.lobby import TERRARIUM_ADMIN
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import FakeServer, _Clock

TEST_PROFILE = load_terrarium_config("terrarium.toml").rooms["TEST"].profile
TICK = 1.0 / 44.0


def _cfg():
    cfg = BitRegistry.scan([Path("bits")]).resolve_config("TestBit")
    return replace(cfg, start=replace(cfg.start, when="admin", key="k", min_scored=0))


def _rig():
    clk = _Clock(100.0)
    gs = GameServer({"TestBit": TestBit}, cue_horizon=0.06, clock=clk)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.room.bound["main"] = "sim-main"
    gs.room.bound["accent"] = "sim-accent"
    frames = {}
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk,
                            on_room_frame=lambda name, f: frames.setdefault(name, []).append(bytes(f)))
    gs.load_bit("TestBit", config=_cfg())
    return gs, server, agent, clk, frames


def _run(agent, clk, seconds):
    for _ in range(int(seconds / TICK)):
        clk.advance(TICK)
        agent.poll()


def _hue_of(frame):
    g, r, b = frame[0], frame[1], frame[2]      # GRB order (Task 7 reorders overrides)
    return (r, g, b)


def test_waiting_aurora_moves_and_breathes_on_both_fixtures():
    gs, server, agent, clk, frames = _rig()
    _run(agent, clk, 6.0)
    for name in ("main", "accent"):
        colours = {_hue_of(f) for f in frames[name]}
        assert len(colours) > 10                      # drift plus breath
        levels = [max(f) for f in frames[name]]
        assert max(levels) - min(levels) > 40         # the breath is visible


def test_invite_paints_the_device_white_twice():
    gs, server, agent, clk, frames = _rig()
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    whites = []
    for _ in range(int(1.0 / TICK)):
        clk.advance(TICK)
        agent.poll()
        for (_d, m) in server.sent:
            if m["address"] == "/ie1/leds":
                whites.append(all(b >= 250 for b in bytes(m["args"][0])))
    # two runs of white frames with a non-white gap between
    runs = 0
    prev = False
    for w in whites:
        if w and not prev:
            runs += 1
        prev = w
    assert runs == 2


def test_accept_flashes_the_fixtures_green_then_the_bits_declaration_returns():
    gs, server, agent, clk, frames = _rig()
    _run(agent, clk, 0.5)
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    _run(agent, clk, 0.1)
    r, g, b = _hue_of(frames["main"][-1])
    assert g >= 250 and r < 10 and b < 10
    _run(agent, clk, 2.0)
    r, g, b = _hue_of(frames["main"][-1])
    assert not (g >= 250 and r < 10 and b < 10)       # flash expired, rainbow back


def test_minimum_refusal_flashes_red_twice():
    gs, server, agent, clk, frames = _rig()
    gs.bit.config = replace(gs.bit.config, start=replace(gs.bit.config.start, min_scored=1))
    _run(agent, clk, 0.5)
    gs.request_start("k", "ie7", "device:ie7")
    reds = []
    for _ in range(int(1.5 / TICK)):
        clk.advance(TICK)
        agent.poll()
        r, g, b = _hue_of(frames["main"][-1])
        reds.append(r >= 250 and g < 10 and b < 10)
    runs, prev = 0, False
    for x in reds:
        if x and not prev:
            runs += 1
        prev = x
    assert runs == 2
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_lobby_frames.py -q`
Expected: pass. If `frames[name]` is empty, confirm `on_room_frame` is wired through `ConsoleFrameSink` for unbound and bound fixtures alike (it is, per `_sinks_for`). If the white-run count is off by one because the first frame after hello already carries the override, loosen to `runs >= 2`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_lobby_frames.py
git commit -m "test(lobby): real-luxaeterna pixel assertions for the lobby"
```

---

### Task 9: web `/start` route, start row on the Join card, `START_URL` marker, and the admin hold

**Files:**
- Modify: `harness/www_server.py`
- Modify: `control/join_info.py`
- Modify: `harness/markers.py`
- Modify: `harness/terrarium_boot.py` (`_start_www_server` 172-196, `_wait_in_setup` 439-566, `_join_info_provider` 960-982, `_print_join_urls` 984-992, main's round-1 hold 1830-1841, and the `_start_www_server(args, teardown)` call site at 1797)
- Test: `tests/test_www_server.py`, `tests/test_join_info.py`, `tests/test_markers.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Produces: `WwwServer.start_requests: queue.Queue[StartRequest]`; `GET /start?key=..&dev=..` answering `202`; `join_info.start_url(*, lan_ip, www_port, key)`; `build_join_info(..., start_key: str | None = None)` adding a `"start"` key (`None` or `{"url", "qr_svg", "key", "wire"}`); `markers.START_URL = "START_URL:"`; `_wait_in_setup` holding without a deadline for `when == "admin"`; `_start_www_server` returns the server and main wires `agent.start_requests`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_www_server.py`:

```python
import urllib.request
from control.lobby import StartRequest, TERRARIUM_ADMIN


def test_start_route_queues_a_loopback_hit_as_the_terrarium(www):
    with urllib.request.urlopen(f"http://127.0.0.1:{www.port}/start?key=abc&dev=gem-1") as r:
        assert r.status == 202
        body = r.read().decode()
    assert "abc" not in body
    req = www.start_requests.get_nowait()
    assert req == StartRequest("abc", TERRARIUM_ADMIN, "web:terrarium")


def test_start_route_labels_a_remote_hit_by_dev_or_anonymous(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "_LOOPBACK", ())          # pretend not loopback
    urllib.request.urlopen(f"http://127.0.0.1:{www.port}/start?key=k&dev=gem-2").read()
    assert www.start_requests.get_nowait() == StartRequest("k", "gem-2", "web:gem-2")
    urllib.request.urlopen(f"http://127.0.0.1:{www.port}/start?key=k").read()
    assert www.start_requests.get_nowait() == StartRequest("k", None, "web:anonymous")


def test_start_route_only_get_and_other_paths_still_serve_files(www):
    with urllib.request.urlopen(f"http://127.0.0.1:{www.port}/index.html") as r:
        assert r.status == 200
```

Append to `tests/test_join_info.py`:

```python
from control.join_info import start_url


def test_start_url_carries_the_key():
    assert start_url(lan_ip="10.0.0.7", www_port=8788, key="a b") == \
        "http://10.0.0.7:8788/start?key=a+b"


def test_build_join_info_adds_a_start_row_only_for_an_admin_bit():
    assert _info()["start"] is None
    info = _info(start_key="metro-dev")
    assert info["start"]["url"] == "http://10.0.0.7:8788/start?key=metro-dev"
    assert info["start"]["key"] == "metro-dev"
    assert info["start"]["qr_svg"].startswith("<svg>")
    assert info["start"]["wire"] == '/game/start "ss" <dev> metro-dev'
```

Append to `tests/test_markers.py` a check that `markers.START_URL == "START_URL:"` and that it is absent from both marker dicts, in the style of the existing `JOIN_URL` assertion in that file.

Append to `tests/test_terrarium_boot.py` (add `from control.state import State` at the top if the file does not already import it):

```python
def test_wait_in_setup_holds_without_a_deadline_for_an_admin_start():
    from control.bit_config import StartCondition
    from harness.terrarium_boot import _wait_in_setup

    class FakeAgent:
        def poll(self):
            pass

    class GS:
        state = State.SETUP
        bit_name = "X"
        registration = None
        bit = None

    ticks = iter([0.0, 1.0, 2.0, 50.0, 60.0])
    cond = StartCondition(when="admin", min_scored=0, key="k",
                          timeout_seconds=55.0, on_timeout="abort")
    reason = _wait_in_setup(FakeAgent(), 0.0, clock=lambda: next(ticks),
                            sleep=lambda _s: None, gs=GS(), condition=cond,
                            game_server=GS())
    assert reason == "timeout-abort"


def test_wait_in_setup_admin_yields_on_state_change_not_on_setup_seconds():
    from control.bit_config import StartCondition
    from harness.terrarium_boot import _wait_in_setup

    class GS:
        state = State.SETUP
        bit_name = "X"
        registration = None
        bit = None

    gs = GS()
    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)
            if len(polls) == 3:
                gs.state = State.RUNNING

    ticks = iter([0.0, 10.0, 20.0, 30.0, 40.0])
    cond = StartCondition(when="admin", min_scored=0, key="k")
    reason = _wait_in_setup(FakeAgent(), 5.0, clock=lambda: next(ticks),
                            sleep=lambda _s: None, gs=gs, condition=cond,
                            game_server=gs)
    assert reason == "state-changed"
```

(`scored_count(GS())` returns 0 because `registration is None`.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_www_server.py tests/test_join_info.py tests/test_markers.py tests/test_terrarium_boot.py -q -k "start or admin"`
Expected: FAIL.

- [ ] **Step 3: Implement `harness/www_server.py`**

Add imports `import queue`, `from urllib.parse import parse_qs, urlsplit`, `from control.lobby import StartRequest, TERRARIUM_ADMIN`; add constants:

```python
START_PATH = "/start"
_LOOPBACK = ("127.0.0.1", "::1")
START_QUEUE_MAX = 16
```

Replace `_QuietHandler` with:

```python
class _QuietHandler(SimpleHTTPRequestHandler):
    """Static files as before, plus the one dynamic route: GET /start.
    The handler runs on the server thread and never touches the engine;
    it only enqueues a StartRequest for DeviceLinkAgent to drain on its
    own tick (spec section 3)."""

    def __init__(self, *args, start_requests=None, **kwargs):
        self._start_requests = start_requests
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # noqa: A002 (stdlib signature)
        return

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != START_PATH:
            return super().do_GET()
        params = parse_qs(parsed.query)
        key = params.get("key", [""])[0]
        dev = params.get("dev", [""])[0] or None
        if self.client_address[0] in _LOOPBACK:
            dev, source = TERRARIUM_ADMIN, "web:terrarium"
        else:
            source = f"web:{dev}" if dev else "web:anonymous"
        if self._start_requests is None:
            self.send_error(404, "start is not wired on this server")
            return
        try:
            self._start_requests.put_nowait(StartRequest(key, dev, source))
        except queue.Full:
            self.send_error(503, "start queue full")
            return
        body = b"start requested\n"
        self.send_response(202)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
```

In `WwwServer.__init__` add `self.start_requests: queue.Queue = queue.Queue(maxsize=START_QUEUE_MAX)`; in `start()` build the handler as `functools.partial(_QuietHandler, directory=self._root, start_requests=self.start_requests)`.

- [ ] **Step 4: Implement `control/join_info.py`**

```python
def start_url(*, lan_ip: str, www_port: int, key: str) -> str:
    """The admin start URL a QR code or NFC tag carries (spec section 3)."""
    return f"http://{lan_ip}:{www_port}/start?{urlencode({'key': key})}"
```

`build_join_info` gains `start_key: str | None = None`; before the `return` build:

```python
    start = None
    if start_key:
        url = start_url(lan_ip=lan_ip, www_port=www_port, key=start_key)
        svg = None
        if qr_svg is not None:
            try:
                svg = qr_svg(url)
            except Exception:
                logger.exception("QR encoder failed for %s", url)
        start = {"url": url, "qr_svg": svg, "key": start_key,
                 "wire": f'/game/start "ss" <dev> {start_key}'}
```

and add `"start": start` to the returned dict.

- [ ] **Step 5: Implement `harness/markers.py` and `harness/terrarium_boot.py`**

`markers.py`: after `JOIN_URL` add `START_URL = "START_URL:"` with a comment (the admin start URL; collected, never waited on). Keep it out of both dicts.

`terrarium_boot.py`:

`_join_info_provider`'s inner `provider()` computes `start_key = cfg.start.key if cfg is not None and cfg.start.when == "admin" else None` and passes `start_key=start_key` to `build_join_info`.

`_print_join_urls`: after the nodes loop add:

```python
    if info.get("start"):
        print(f"{markers.START_URL} {info['start']['url']}", flush=True)
```

`_wait_in_setup`: replace the first lines and the deadline logic:

```python
    admin = condition is not None and condition.when == "admin"
    if setup_seconds <= 0 and not admin:
        return "expired"
    initial_bit_name = getattr(gs, "bit_name", None) if gs is not None else None
    start = clock()
    deadline = None if admin else start + setup_seconds
    next_countdown = start + 15.0
    while True:
        now = clock()
        if deadline is not None and now >= deadline:
            return "expired"
```

and the countdown print:

```python
        if now >= next_countdown:
            if deadline is None:
                print("SETUP open, waiting for an admin start", flush=True)
            else:
                print(f"SETUP open, {deadline - now:.0f}s remaining", flush=True)
            next_countdown = now + 15.0
```

Update the docstring's return list (unchanged values) and note the admin hold.

Main's round-1 hold (line 1831): change the marker print to

```python
            setup_seconds = cfg.launch.setup_seconds
            if cfg.start.when == "admin":
                print(f"{markers.CONTROL_SETUP_HOLD} until an admin start "
                      f"-- join now", flush=True)
            elif setup_seconds > 0:
                print(f"{markers.CONTROL_SETUP_HOLD} for {setup_seconds:g}s "
                      f"-- join now", flush=True)
```

`_serve_rounds` needs no change (it always calls `_wait_in_setup`; add the same admin-aware `CONTROL_SETUP_HOLD` print there if it prints one; if it prints none today, leave it).

`_start_www_server` already returns the server. At its call site (line 1797) capture it and wire the queue:

```python
            www = _start_www_server(args, teardown)
            if www is not None:
                agent.start_requests = www.start_requests
```

Check the test at `tests/test_terrarium_boot.py:2728` that monkeypatches `_start_www_server`; its fake must return `None` or an object with `start_requests` (fix the fake to return `None` if it returns something else).

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_www_server.py tests/test_join_info.py tests/test_markers.py tests/test_terrarium_boot.py -q`
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add harness/www_server.py control/join_info.py harness/markers.py harness/terrarium_boot.py tests/test_www_server.py tests/test_join_info.py tests/test_markers.py tests/test_terrarium_boot.py
git commit -m "feat(harness): GET /start route, start row and START_URL, admin SETUP hold"
```

---

### Task 10: Console and uplink: `run` through `request_start`, lobby events, the Start row

**Files:**
- Modify: `console/agent.py` (`_handle_command` line 220-221, observer methods near 879-919, `snapshot()` 591-632, `on_state_change` 825-837)
- Modify: `console/protocol.py` (add `lobby_changed_event`, `snapshot_event(..., lobby=None)`)
- Modify: `uplink/link.py` (line 141-142)
- Modify: `console/static/join.js`
- Test: `tests/test_console_agent.py`, `tests/test_uplink.py` (whichever file holds the uplink `run` test; grep `RunCommand` under `tests/`), `tests/js/join_card.test.js` (new)

**Interfaces:**
- Consumes: Task 4 (`request_start`, `lobby_state`, `on_start_requested`, `on_lobby_event`), Task 9 (`info["start"]`).
- Produces: wire event `{"event": "lobby_changed", "lobby": "WAITING"|"FULL"|null}`; snapshot key `lobby`; log lines `start accepted: <source>` / `start refused: <source> (<reason>)` / `lobby invite: <dev>` / `lobby handshake: <dev>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_console_agent.py` (reuse its `_server_with_agent()` helper and `FakeConsoleServer`):

```python
def test_run_goes_through_request_start_and_logs_the_outcome():
    gs, srv, agent = _server_with_agent()
    srv.deliver("c1", {"command": "run"})
    agent.poll()
    errors = [m for (_, m) in srv.sent if m.get("event") == "error"]
    assert errors[-1]["message"] == "no Bit loaded"
    logs = [m for m in srv.broadcasts if m.get("event") == "log"]
    assert logs[-1]["message"].startswith("start refused: console (no Bit loaded)")
    gs.load_bit("test_bit")
    srv.deliver("c1", {"command": "run"})
    agent.poll()
    assert gs.state.name == "RUNNING"
    logs = [m for m in srv.broadcasts if m.get("event") == "log"]
    assert logs[-1]["message"] == "start accepted: console"


def test_lobby_changed_rides_registration_and_state_and_the_snapshot():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("test_bit")
    lobby = [m for m in srv.broadcasts if m.get("event") == "lobby_changed"]
    assert lobby[-1]["lobby"] == "WAITING"
    snap = agent.snapshot()
    assert snap["lobby"] == "WAITING"
    gs.request_start(None, "terrarium", "console")
    lobby = [m for m in srv.broadcasts if m.get("event") == "lobby_changed"]
    assert lobby[-1]["lobby"] is None


def test_lobby_events_are_logged():
    gs, srv, agent = _server_with_agent()
    gs.notify_lobby("invite", "ie1")
    logs = [m for m in srv.broadcasts if m.get("event") == "log"]
    assert logs[-1]["message"] == "lobby invite: ie1"
```

Update the existing `test_bad_command_sends_error_to_origin_only` expectation only if it asserts the message text (it asserts count and command, so it should still pass).

In the uplink test file, locate the test that sends `{"command": "run"}` and assert the error message is now `"no Bit loaded"` when nothing is loaded (add such a test if none exists, mirroring the console one with the uplink's `FakeTransport`).

Create `tests/js/join_card.test.js`:

```javascript
"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

(async () => {
  const wire = await import("../../console/static/wire.js");
  const join = await import("../../console/static/join.js");
  join.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  const info = {
    www_url: "http://10.0.0.7:8788/app/", o2ws_host: "10.0.0.7:8080",
    ensemble: "arco", app_present: true, bit: "MetronomeBit",
    nodes: [{ role: "player", node: "METRO_PLAYER_NODE",
              url: "http://10.0.0.7:8788/app/?node=METRO_PLAYER_NODE",
              qr_svg: "<svg></svg>", tuneshroom_cmd: "flutter run" }],
    native_note: "",
    start: { url: "http://10.0.0.7:8788/start?key=metro-dev", qr_svg: "<svg id=\"sqr\"></svg>",
             key: "metro-dev", wire: '/game/start "ss" <dev> metro-dev' },
  };
  send({ event: "join_changed", join: info });
  const html = byId.get("joinCard").innerHTML;
  assert.ok(html.includes("Start"), "start heading");
  assert.ok(html.includes("http://10.0.0.7:8788/start?key=metro-dev"));
  assert.ok(html.includes("metro-dev"));
  assert.ok(html.includes('/game/start "ss"'));
  assert.ok(html.includes("sqr"), "start QR rendered");

  send({ event: "join_changed", join: { ...info, start: null } });
  assert.ok(!byId.get("joinCard").innerHTML.includes("/start?key="));
  console.log("join_card ok");
})();
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_console_js.py -q -k "run_goes or lobby or join_card"`
Expected: FAIL.

- [ ] **Step 3: Implement**

`console/protocol.py`: add

```python
def lobby_changed_event(lobby: str | None) -> dict:
    """The lobby is WAITING / FULL while a Bit sits in SETUP, else None."""
    return {"event": "lobby_changed", "lobby": lobby}
```

and a `lobby=None` keyword on `snapshot_event` that lands as `"lobby": lobby` in the returned dict; add both names to `__all__`.

`console/agent.py`: import `from control.lobby import TERRARIUM_ADMIN`; replace the `RunCommand` branch with:

```python
            elif isinstance(command, protocol.RunCommand):
                reason = self.game_server.request_start(None, TERRARIUM_ADMIN,
                                                        "console")
                if reason is not None:
                    return protocol.error_event(name, reason)
```

Add to `snapshot()`'s `snapshot_event(...)` call `lobby=self.game_server.lobby_state()`. In `on_state_change`, after the `state_changed` broadcast add `self.server.broadcast(protocol.lobby_changed_event(self.game_server.lobby_state()))`. In `on_registration_change`, add the same broadcast after the registration one. Add observer methods:

```python
    def on_start_requested(self, record) -> None:
        if record.accepted:
            message = f"start accepted: {record.source}"
            level = "info"
        else:
            message = f"start refused: {record.source} ({record.reason})"
            level = "warn"
        self.server.broadcast(protocol.log_event(level, message))

    def on_lobby_event(self, event: str, dev: str) -> None:
        self.server.broadcast(protocol.log_event("info", f"lobby {event}: {dev}"))
```

`uplink/link.py` line 141-142:

```python
            elif isinstance(command, protocol.RunCommand):
                reason = self.game_server.request_start(None, TERRARIUM_ADMIN,
                                                        "uplink")
                if reason is not None:
                    self._send(protocol.error_event(command_name, reason))
```

with the import added.

`console/static/join.js`: after the `for (const row of nodes)` loop and before the native note:

```javascript
  if (info.start) {
    const block = mk("div", "joinnode");
    block.appendChild(mk("h4", null, "Start (admin)"));
    if (info.start.qr_svg) {
      const qr = mk("div", "qr");
      qr.innerHTML = info.start.qr_svg;   // server-rendered segno SVG, trusted local Console
      block.appendChild(qr);
    }
    block.appendChild(lineWithCopy(info.start.url));
    block.appendChild(mk("p", "meta", "Key (also accepted on the device wire):"));
    block.appendChild(lineWithCopy(info.start.key));
    block.appendChild(lineWithCopy(info.start.wire));
    card.appendChild(block);
  }
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_console_js.py tests/test_uplink*.py tests/test_link*.py -q`
Expected: pass (`tests/test_console_js.py` globs `tests/js/*.test.js`, so the new node test runs automatically; it skips cleanly if node is absent).

- [ ] **Step 5: Commit**

```bash
git add console/agent.py console/protocol.py uplink/link.py console/static/join.js tests/test_console_agent.py tests/js/join_card.test.js
git commit -m "feat(console): Run via request_start, lobby_changed, start row on the Join card"
```

---

### Task 11: harness: keyed chime, Testshroom `--handshake`, `run_stack --start-after-grant`, MetronomeBit manifest

**Files:**
- Modify: `harness/sim_audio.py`
- Modify: `harness/o2_shroom.py` (argparse 413-498, `_on_frame` 538-547, join sends 580-584 and 671-673, retry 699-703, the tick loop 766-797, lobby loop 800-810)
- Modify: `harness/run_stack.py` (`StackConfig` 79-104, `device_command` 220-238, `collect_url` 291-313, device wait loop 339-371, argparse 640-670, `config_from_args` 720-793, `RunResult` unchanged)
- Modify: `bits/metronome/bit.toml`, `profiles/dev-metronome.toml`
- Test: `tests/test_sim_audio.py`, `tests/test_o2_shroom.py`, `tests/test_run_stack.py` (extend whichever exist; create `tests/test_sim_audio.py` if absent)

**Interfaces:**
- Produces: `sim_audio.play_key(params: str) -> int | None`; `sim_audio.chime_wav_for_key(key: int) -> bytes`; `sim_audio.KeyedChimePlayer(sink).play(key)`; `o2_shroom.invite_seen(frame: bytes) -> bool`; `--handshake` flag; run_stack flags `--start-after-grant`, `--handshake-devices N`; `run_stack._loopback(url) -> str`; `RunResult.stage == "start"` failures.

- [ ] **Step 1: Write the failing tests**

`tests/test_sim_audio.py` (create or append):

```python
import wave, io
from harness.sim_audio import (AfplaySink, KeyedChimePlayer, chime_wav_for_key,
                               play_key)


def test_play_key_parses_the_ceremony_params():
    assert play_key("key=71") == 71
    assert play_key("") is None
    assert play_key("key=x") is None


def test_keyed_chime_is_a_wav_whose_pitch_follows_the_key():
    for key in (69, 81):
        data = chime_wav_for_key(key)
        with wave.open(io.BytesIO(data)) as w:
            assert w.getnchannels() == 1 and w.getnframes() > 1000


def test_keyed_chime_player_writes_one_sample_per_key():
    written = []
    sink = AfplaySink(runner=lambda path: written.append(path))
    player = KeyedChimePlayer(sink)
    player.play(69)
    player.play(71)
    player.play(69)
    assert len(written) == 3
```

Append to `tests/test_o2_shroom.py`:

```python
from harness.o2_shroom import invite_seen


def test_invite_seen_is_a_solid_white_frame():
    assert invite_seen(bytes([255] * 36))
    assert invite_seen(bytes([250, 255, 252] * 12))
    assert not invite_seen(bytes([255, 0, 255] * 12))
    assert not invite_seen(b"")
```

Append to `tests/test_run_stack.py`:

```python
from harness.run_stack import _loopback, device_command


def test_loopback_rewrites_the_host_and_keeps_the_query():
    assert _loopback("http://10.0.0.7:8788/start?key=k") == \
        "http://127.0.0.1:8788/start?key=k"


def test_handshake_devices_get_the_flag(monkeypatch):
    cfg = _stack_config(devices=2, handshake_devices=1)     # use the file's config helper
    assert "--handshake" in device_command(cfg, 1, 1)
    assert "--handshake" not in device_command(cfg, 2, 1)
```

(If `tests/test_run_stack.py` builds `StackConfig` directly rather than via a helper, construct one the same way that file already does and set `handshake_devices=1`.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_sim_audio.py tests/test_o2_shroom.py tests/test_run_stack.py -q -k "key or chime or invite or loopback or handshake"`
Expected: FAIL.

- [ ] **Step 3: Implement `harness/sim_audio.py`**

```python
def play_key(params: str) -> int | None:
    """The ceremony's `key=<midi>` play-cue params, or None."""
    for part in (params or "").split():
        if part.startswith("key="):
            try:
                return int(part[4:])
            except ValueError:
                return None
    return None


def chime_wav_for_key(key: int) -> bytes:
    """A two-segment chime at the given MIDI key: the fundamental then a
    fifth above, so the lobby's bell and the device's chime agree."""
    f = 440.0 * 2.0 ** ((int(key) - 69) / 12.0)
    return tone_wav([(f, 0.12), (f * 1.5, 0.18)])


class KeyedChimePlayer:
    """Plays chime_wav_for_key through an AfplaySink, one cached WAV per key."""

    def __init__(self, sink) -> None:
        self._sink = sink
        self._cache: dict[int, bytes] = {}

    def play(self, key: int) -> None:
        data = self._cache.get(key)
        if data is None:
            data = self._cache[key] = chime_wav_for_key(key)
        self._sink.write(f"chime-{key}", data)
```

- [ ] **Step 4: Implement `harness/o2_shroom.py`**

Module-level:

```python
HANDSHAKE_RETAP_S = 2.0


def invite_seen(frame: bytes) -> bool:
    """A lobby invite is a solid white override on every pixel."""
    return len(frame) >= 3 and all(b >= 200 for b in frame)
```

Argparse: `parser.add_argument("--handshake", action="store_true", help="Hello without joining; double-tap on the first invite (a solid white frame) and let the lobby join this device (spec 5). Ignores --node.")`.

After parsing: `explicit_join = not args.no_join and not args.handshake`. Replace every `if not args.no_join: send_join()` (initial send at 672, and the lobby re-join at ~810) with `if explicit_join: send_join()`; the retry `next_join = (... if args.join_retry > 0 and explicit_join else None)`.

Keyed chime: replace `on_play=lambda name, params: player.play(name)` with:

```python
    keyed = KeyedChimePlayer(player.sink) if hasattr(player, "sink") else None

    def _play(name: str, params: str) -> None:
        key = play_key(params)
        if name == "chime" and key is not None and keyed is not None:
            keyed.play(key)
        else:
            player.play(name)
```

`SamplePlayer` stores its sink as `self._sink`; expose it with a `sink` property in `harness/local_sample.py` (one-line addition) so the branch above resolves. Pass `on_play=_play`.

Invite detection in `_on_frame`, before the tapper check:

```python
    def _on_frame(frame: bytes, now) -> None:
        if args.handshake and client.config is None and invite_seen(frame):
            invite_flag[0] = True
        if not tapper.armed or now is None:
            return
        ...
```

with `invite_flag = [False]` and `last_handshake_tap = [None]` defined just above. In the tick loop, right before `if not args.no_join and _gestures_ready(client):` add:

```python
                if (args.handshake and invite_flag[0] and client.config is None
                        and (last_handshake_tap[0] is None
                             or now - last_handshake_tap[0] >= HANDSHAKE_RETAP_S)):
                    o2lite.send("/game/tap", now, "sffi", args.dev, 1.0, 50.0, 2)
                    last_handshake_tap[0] = now
                    print(f"handshake: invite seen, double-tap sent at {now:.3f}",
                          flush=True)
                invite_flag[0] = False
```

- [ ] **Step 5: Implement `harness/run_stack.py`**

`StackConfig` gains `start_after_grant: bool = False` and `handshake_devices: int = 0`. Argparse (near `--persist-shrooms`):

```python
    ap.add_argument("--start-after-grant", action="store_true",
                    help="Once every spawned device is granted a role, GET the "
                         "START_URL Control printed (rewritten to loopback, so "
                         "the hit carries the Terrarium's own admin identity).")
    ap.add_argument("--handshake-devices", type=int, default=0,
                    help="The first N spawned Testshrooms join via the lobby "
                         "handshake (--handshake) instead of an explicit join.")
```

`config_from_args` copies both onto the config. `device_command`:

```python
    if index <= cfg.handshake_devices:
        command += ["--handshake"]
```

Add near `_URL_PATTERN`:

```python
def _loopback(url: str) -> str:
    parts = urlsplit(url)
    netloc = f"127.0.0.1:{parts.port}" if parts.port else "127.0.0.1"
    return urlunsplit(parts._replace(netloc=netloc))
```

(`from urllib.parse import urlsplit, urlunsplit`; `import urllib.request`.)

In `run()`: add `start_urls: list[str] = []` next to `urls`; in `collect_url` add first:

```python
        if markers.START_URL in line:
            match = _URL_PATTERN.search(line)
            if match is not None:
                start_urls.append(match.group())
            return
```

After the per-device `DEVICE_ROLE_GRANTED` loop:

```python
        if cfg.start_after_grant:
            if not start_urls:
                return RunResult(False, "start",
                                 "no START_URL line seen; does the Bit's [start] "
                                 "declare when = 'admin'?", logs, urls, room_urls)
            url = _loopback(start_urls[-1])
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    status = resp.status
            except (OSError, urllib.error.URLError) as exc:
                return RunResult(False, "start", f"start request failed: {exc}",
                                 logs, urls, room_urls)
            print(f"start requested via {url} -> HTTP {status}", flush=True)
```

`START_URL` is printed by Control's `_print_join_urls` at LOADED, which happens before the `CONTROL_SETUP_HOLD` gate `run()` waits on, so it is collected by then.

- [ ] **Step 6: MetronomeBit manifest and the dev profile**

`bits/metronome/bit.toml` `[start]` becomes:

```toml
[start]
when = "admin"
key = "metro-dev"
min_scored = 2
```

`profiles/dev-metronome.toml`: delete the `[bit.overrides.start]` table entirely; keep `[run]` and `[bit.overrides.rhythm]`.

Check `tests/test_bit_registry.py` / `tests/test_run_profile.py` for assertions on Metronome's start condition (`when == "players"`, `timeout_seconds == 120`) and update them to the admin shape.

- [ ] **Step 7: Run the suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass; note the count for the docs task.

- [ ] **Step 8: Commit**

```bash
git add harness/sim_audio.py harness/local_sample.py harness/o2_shroom.py harness/run_stack.py bits/metronome/bit.toml profiles/dev-metronome.toml tests/test_sim_audio.py tests/test_o2_shroom.py tests/test_run_stack.py tests/test_bit_registry.py
git commit -m "feat(harness): keyed chime, Testshroom --handshake, run_stack --start-after-grant, Metronome admin start"
```

---

### Task 12: docs, live gate checklist, and the deep-dive entry

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (append a dated section after the 2026-09-10 standup-and-join entry, before `## Boundary rules`)
- Modify: `docs/superpowers/specs/2026-09-11-metronome-lobby-and-admin-start-design.md` (section 10 deviations, section 8 live gate)

- [ ] **Step 1: Record the deviations in the spec's section 10**

Replace `None yet.` with:

```markdown
- **Start rule ordering (section 2).** The key is checked before the SETUP
  check: a valid key outside SETUP gives three red flashes (the "any other
  reason" case); a bad key stays silent; an unkeyed Console/uplink start
  outside SETUP is refused with no room reaction. With no Bit loaded every
  start is refused silently because there is no key to check against.
- **`min_scored` default.** 1 for `players` (unchanged, still refuses 0), 0
  for `admin` (absent means an empty start is allowed).
- **Accept flash.** The green accept flash is emitted by the agent, not the
  LobbyRuntime, because RUNNING tears the runtime down before the
  `on_start_requested` record arrives.
- **Testshroom handshake.** The invite is detected as a solid white frame
  (every byte >= 200) and answered with one count-2 tap, re-armed at most
  every 2 s.
```

- [ ] **Step 2: Append the deep-dive section to `docs/MM_TERRARIUM.md`**

Insert before `## Boundary rules (the load-bearing invariants)`:

```markdown
### Lobby, join handshake, and admin start (2026-09-11)
Design: `docs/superpowers/specs/2026-09-11-metronome-lobby-and-admin-start-design.md`.
The timed SETUP wait is replaced by an admin-started lobby that every Bit
gets by default.

- **`control/lobby.py`** (pure): `LobbyState` WAITING/FULL from
  `RegistrationState.counts()` (FULL = every capped scored role at
  capacity), the A major note scale, the start rule `decide_start`, and
  three schedulers (`DoubleTapDetector`, `InviteSchedule`,
  `CeremonySlots`). `TERRARIUM_ADMIN = "terrarium"` is the box's own
  admin identity: always admin, never removable, refused as a hello'd
  dev.
- **`GameServer.request_start(key, source_dev, source)`** is the one start
  authority; Console `run`, uplink `run`, `GET /start?key=` on the LAN
  static server (loopback hits count as the Terrarium), and the new
  `/game/start "ss" dev key` verb all call it. Refusals are reason strings;
  every attempt fires `on_start_requested`. `[admin] devices` in
  `terrarium.toml` adds GemIDs; `gs.is_admin(dev)` answers for Bits and the
  Console.
- **`[start] when = "admin"`** with a required `key`; `min_scored` defaults
  to 0 there. The harness hold waits with no deadline (a `timeout_seconds`
  still applies). MetronomeBit ships `when = "admin"`, `key = "metro-dev"`,
  `min_scored = 2`; `profiles/dev-metronome.toml` no longer overrides
  `[start]`.
- **`devicelink/lobby_runtime.py`** does the light and sound through sinks
  the agent injects: on SETUP every fixture session swaps to a lobby
  aurora (cc:74 hue drift, cc:11 breath), the fixture voices switch to the
  warm pad and drone; FULL pins green, keeps the breath on light only and
  stops the drone; RUNNING swaps back and restores the Bit's program. A
  scored join runs the ceremony from one reserved time `at`: device green
  flash x2, bell on a transient voice at `at + 0.8` up the scale, device
  chime play cue at `at + 1.8` carrying `key=<midi>`; ceremonies are spaced
  1 s apart. Invites are two white flashes every 5 s on any hello'd,
  un-joined, non-fixture device while WAITING; a double tap (count 2, or
  two taps within 1.5 s) joins it to the Bit's default join role via the
  same `GameServer.join` an explicit join uses. `[lobby] enabled = false`
  opts a Bit out.
- **Feedback on fixtures:** accept green x1, minimum not met red x2, any
  other keyed refusal red x3, bad key nothing.
- **Harness:** `harness/o2_shroom.py --handshake`, keyed chime in
  `harness/sim_audio.py`, `run_stack --start-after-grant` and
  `--handshake-devices N`, `START_URL:` marker, a Start row (URL, QR, key,
  wire row) on the Console's Join card, `lobby_changed` wire event and
  `lobby` snapshot key.
- **Cross-repo follow-ups (not built):** mm-tuneshroom sends `/game/start`
  and renders frames and the keyed chime before a role; the MycoQuest admin
  site writes a device's GemID into the venue's `[admin] devices`.
- **Live gate on MYCOLOGICAL:** pending; record the run id here.
```

Update the suite baseline line in the section with the count from Task 11 step 7.

- [ ] **Step 3: Run the full suite one more time and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass.

```bash
git add docs/MM_TERRARIUM.md docs/superpowers/specs/2026-09-11-metronome-lobby-and-admin-start-design.md
git commit -m "docs: lobby, handshake, and admin start in the deep-dive; spec deviations"
```

---

## Live gate (after the plan, on MYCOLOGICAL, by hand)

**RUN ON: MYCOLOGICAL**, from the worktree root with `MM_ARCO_PATH` and `MM_SOUNDFONT` exported:

```bash
./smoke-test.sh --ci --profile profiles/dev-metronome.toml --seconds 75 --start-after-grant --handshake-devices 1
```

Expected in `runs/<id>/control.log`: `lobby invite: ie1`, `lobby handshake: ie1`, both joins granted, two bell notes, `start accepted: web:terrarium`, the round runs to `Bit completed; tearing down`. Then `./terrarium.sh --room DEMO`, load MetronomeBit from the Console, scan the Start QR from a phone with one player joined (expect two red flashes), with two (expect green and the count-in), and once with the key edited in the URL (expect no room reaction and a `start refused: web:anonymous (bad key)` log line).
