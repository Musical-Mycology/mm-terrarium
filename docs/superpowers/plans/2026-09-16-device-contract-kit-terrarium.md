# Device Contract Kit (mm-terrarium: Phases 1 and 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give mm-terrarium a single, checked source of truth for the device
wire (a verb table plus `hold`/`swing`), a Rev 1 catalog instrument and
Mushica-style capability gate, and a recorder + export tool that turns
Control's real behavior into scenarios every device repo (mm-tuneshroom,
mm-devshroom) can replay as a failing test.

**Architecture:** Phase 1 adds one new declarative module
(`devicelink/contract.py`) that both `devicelink/o2_transport.py` and a new
export tool read, a new catalog instrument, and doc amendments recording
the same facts in the ESP32 track's spec/plan and in `docs/MM_TERRARIUM.md`.
Phase 3 adds a test-only `contract_kit/` package (a `Bit` subclass, a
recording rig built on `O2LiteTransport`/`FakeO2Lite`, eleven scenario
scripts, and their committed JSON recordings) plus `tools/export_contract.py`,
which packages the verb table, the Rev 1 instrument, and the recordings into
the EXPORT FORMAT v1 folder every device repo commits at `test/contract/`.

**Tech Stack:** Python 3 (stdlib + pytest), the existing `control`/
`devicelink`/`harness` packages, `luxaeterna` (sibling checkout, already a
test dependency), no new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`
(sections 4.2, 4.3, 5, 9, and the mm-terrarium bullets of 11 are what this
plan implements; §6, §7, §8 belong to the mm-tuneshroom and mm-devshroom
plans and the bench-replay feasibility spike, not here).

## Global Constraints

- All commands run on MYCOLOGICAL (the dev Mac) inside the plan's worktree
  unless a block is labeled otherwise with **RUN ON: \<HOST\>**.
- Test command, verified from `README.md`: `.venv/bin/python -m pytest tests -v`.
  Always invoke the venv's own interpreter explicitly (`.venv/bin/python`),
  never a bare `python`/`python3` — a fresh worktree has no `.venv` of its
  own; symlink one before running anything (Setup, below).
- Every new/modified Python file follows the repo's existing docstring and
  comment density (a short module docstring explaining *why*, not just
  *what*); match the style already in `devicelink/`, `control/`, `tools/`.
- `mm-terrarium` is a public repo. Doc edits (this plan's Task 4) record
  what changed and why in plain, neutral language; they do not restate
  §1's drift analysis or any other firmware-bug narrative from the spec.
- New Python modules use `from __future__ import annotations` and type
  hints consistent with neighboring files (see `devicelink/contract.py`'s
  siblings for the convention).
- JSON files this plan writes (contract kit recordings and exports) are
  UTF-8, `json.dumps(data, indent=2, sort_keys=True)` plus a trailing
  newline — the same convention `tools/export_solo.py` already uses.

## Setup (once, before Task 1)

- [ ] **Step 1: Create the execution branch from the spec branch**

```bash
git worktree add ../device-contract-kit-terrarium -b claude/device-contract-kit-terrarium claude/device-contract-kit-spec
cd ../device-contract-kit-terrarium
```

(Use whatever path your worktree tooling prefers; the branch's parent is
what matters — `claude/device-contract-kit-spec` already carries the
approved spec and this plan file, so both travel with the code.)

- [ ] **Step 2: Symlink the venv (a fresh worktree has none of its own)**

```bash
ln -s /Users/chris/projects/mm-terrarium/.venv .venv
.venv/bin/python -m pytest tests -v 2>&1 | tail -5
```

Expected: a real pass/fail summary (thousands of tests), not an import
error — confirms the venv symlink and `luxaeterna` sibling checkout both
resolve correctly before any new code is written.

---

# Phase 1 (land by Mon Sep 21)

## Task 1: The verb table (`devicelink/contract.py`)

**Files:**
- Create: `devicelink/contract.py`
- Create: `tests/test_devicelink_contract.py`
- Modify: `devicelink/o2_transport.py:16-23` (imports), `devicelink/o2_transport.py:106-109` (the `GAME_VERBS` definition)

**Interfaces:**
- Produces: `VerbRow` (frozen dataclass: `verb`, `direction`, `typespecs`,
  `args`, `transport`, `pre_role`, `notes`, plus an `address` property),
  `VERB_TABLE: tuple[VerbRow, ...]`, `GAME_VERBS: tuple[str, ...]` (derived
  from `VERB_TABLE`'s `direction == "up"` rows), `row_for(direction, verb) ->
  VerbRow`, `typespec_allowed(direction, verb, typespec) -> bool`.
- Consumes (in the new test only): `devicelink.protocol`'s builders,
  `devicelink.o2_transport.FakeO2Lite`/`O2LiteTransport`.

Two facts this task's table encodes were verified directly against the
running code, not assumed:

- Every message `O2LiteTransport.send` puts on the wire (`role`, `deny`,
  `leds`, `play`, `release`, `error`, `room`) calls `self._o2.send(...)`
  with no `tcp=` keyword, and o2litepy's `send(self, addr, timestamp,
  *args, tcp=False)` defaults to UDP (`send_cmd` is the one that passes
  `tcp=True`) — confirmed by reading
  `~/projects/o2/o2litepy/src/o2litepy/o2lite.py:359-367`. So every down
  row is `udp-ok`, matching current behavior; nothing about that
  reliability choice changes in this task.
- `capture`/`telemetry` have no existing reliability convention anywhere
  in the code or `docs/telemetry-trace-schema.md` — **(recommended)** `tcp`
  for both, because a dropped chunk shows up as a gap in a capture trace,
  which is exactly what `capture/store.py`'s `gaps` field exists to catch;
  losing a `join`/`start`-class lifecycle message is more disruptive than
  losing one gesture. Flagged in the row's own `notes` as this plan's
  choice, not a measured fact, so a future change is easy to find.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_devicelink_contract.py
"""devicelink/contract.py: the verb table, and consistency checks against
devicelink/protocol.py's builders and devicelink/o2_transport.py's
GAME_VERBS. See docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md section 5.1."""
from devicelink import protocol
from devicelink.contract import GAME_VERBS, VERB_TABLE, row_for, typespec_allowed
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport
from devicelink.o2_transport import GAME_VERBS as TRANSPORT_GAME_VERBS


def test_game_verbs_is_derived_from_the_up_rows():
    assert GAME_VERBS == tuple(r.verb for r in VERB_TABLE if r.direction == "up")


def test_o2_transport_game_verbs_matches_the_table():
    assert TRANSPORT_GAME_VERBS == GAME_VERBS


def test_hold_and_swing_are_new_up_rows_with_the_designed_shapes():
    hold = row_for("up", "hold")
    assert hold.typespecs == ("sfi",)
    assert hold.args == ("dev", "held_seconds", "count")
    assert hold.transport == "udp-ok"
    swing = row_for("up", "swing")
    assert swing.typespecs == ("sfi",)
    assert swing.args == ("dev", "signed_peak_g", "count")


def test_hello_join_start_are_tcp_and_gestures_are_udp_ok():
    for verb in ("hello", "join", "start"):
        assert row_for("up", verb).transport == "tcp"
    for verb in ("tap", "tilt", "shake", "hold", "swing"):
        assert row_for("up", verb).transport == "udp-ok"


def test_role_event_typespec_is_allowed():
    msg = protocol.role_event("ie1", {"role": "player"})
    assert typespec_allowed("down", "role", msg["typespec"])


def test_deny_event_typespec_is_allowed():
    msg = protocol.deny_event("ie1", "no such node", None)
    assert typespec_allowed("down", "deny", msg["typespec"])


def test_leds_event_typespec_is_allowed():
    msg = protocol.leds_event("ie1", [0] * 36)
    assert typespec_allowed("down", "leds", msg["typespec"])


def test_release_event_typespec_is_allowed():
    msg = protocol.release_event("ie1")
    assert typespec_allowed("down", "release", msg["typespec"])


def test_error_event_typespec_is_allowed():
    msg = protocol.error_event("ie1", "tap", "device not registered")
    assert typespec_allowed("down", "error", msg["typespec"])


def test_play_event_typespec_is_allowed():
    msg = protocol.play_event("ie1", "tick", "")
    assert typespec_allowed("down", "play", msg["typespec"])


def test_room_event_typespec_is_allowed():
    msg = protocol.room_event("ie1", {"state": "SETUP"})
    assert typespec_allowed("down", "room", msg["typespec"])


def test_hold_and_swing_delivered_through_fake_o2lite_reach_drain_inbound():
    fake = FakeO2Lite(now=100.0)
    fake.set_services("actl")
    transport = O2LiteTransport()
    transport.start(fake)
    fake.deliver("/game/hold", "sfi", ("ie1", 0.65, 1), timestamp=100.5)
    fake.deliver("/game/swing", "sfi", ("ie1", -2.1, 1), timestamp=100.6)
    drained = [env for (_client, env) in transport.drain_inbound()]
    addresses = [(e["address"], e["typespec"], e["args"]) for e in drained]
    assert ("/game/hold", "sfi", ["ie1", 0.65, 1]) in addresses
    assert ("/game/swing", "sfi", ["ie1", -2.1, 1]) in addresses
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_contract.py -v`
Expected: every test errors with `ModuleNotFoundError: No module named
'devicelink.contract'`.

- [ ] **Step 3: Write `devicelink/contract.py`**

```python
# devicelink/contract.py
"""The device contract kit's verb table: one row per verb Control and a
device exchange (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md section 5.1).

GAME_VERBS -- the /game/<verb> method names devicelink/o2_transport.py's
O2LiteTransport registers on the o2lite connection -- is DERIVED from this
table's up rows, so a new up verb is added in exactly one place.

This table is read by:
- tests/test_devicelink_contract.py (every devicelink/protocol.py builder's
  typespec must be one this table allows for its address)
- tools/export_contract.py (the exported contract.json "verbs" list)

Down-row transport is "udp-ok" for every row: O2LiteTransport.send calls
o2lite's send() with no tcp= keyword, and o2litepy's send() defaults to
UDP (send_cmd is the one that passes tcp=True) -- verified against
o2litepy/src/o2litepy/o2lite.py, not assumed. Nothing here changes that.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerbRow:
    verb: str                     # "hello", "tap", "role", ...
    direction: str                # "up" (/game/<verb>) or "down" (/<dev>/<verb>)
    typespecs: tuple[str, ...]    # every typespec this verb may be sent with
    args: tuple[str, ...] = field(default_factory=tuple)  # arg names of the LONGEST typespec
    transport: str = "udp-ok"     # "tcp" or "udp-ok"
    pre_role: bool = False        # may be sent/received before a role exists
    notes: str = ""

    @property
    def address(self) -> str:
        prefix = "/game/" if self.direction == "up" else "/<dev>/"
        return f"{prefix}{self.verb}"


VERB_TABLE: tuple[VerbRow, ...] = (
    # --- up: device -> Control (/game/<verb>) ---
    VerbRow("hello", "up", ("s", "ssss"),
            ("dev", "name", "protoversion", "instrument"), "tcp", True,
            "The 'ssss' form declares the carried instrument (2026-08-31 "
            "carried-instrument-wire); the bare 's' form declares nothing "
            "and resolves to defaultshroom."),
    VerbRow("join", "up", ("ss",), ("dev", "node"), "tcp", True, ""),
    VerbRow("start", "up", ("ss",), ("dev", "key"), "tcp", True,
            "key is '' for an unkeyed (Console/uplink) start."),
    VerbRow("tap", "up", ("sffi",),
            ("dev", "peak_g", "duration_ms", "count"), "udp-ok", True,
            "peak_g is 0 for a touch tap (Rev 1); count is always 1 on "
            "Rev 1 -- Control pairs double taps itself. Stamped at onset. "
            "Allowed before a role for the lobby's tap-to-join handshake."),
    VerbRow("tilt", "up", ("sf",), ("dev", "gamma"), "udp-ok", False, ""),
    VerbRow("shake", "up", ("sfff",),
            ("dev", "peak_g", "duration_ms", "sweep_deg"), "udp-ok", False, ""),
    VerbRow("hold", "up", ("sfi",), ("dev", "held_seconds", "count"),
            "udp-ok", False,
            "New for Rev 1. Sent on release of a touch held past the hold "
            "window; stamped at touch-down. count is always 1. Waits for "
            "a role, unlike tap."),
    VerbRow("swing", "up", ("sfi",), ("dev", "signed_peak_g", "count"),
            "udp-ok", False,
            "New for Rev 1. Negative means left; stamped at onset. count "
            "is always 1. Waits for a role, unlike tap."),
    VerbRow("canvas", "up", ("ss",), ("dev", "url"), "tcp", True,
            "Simulators only (harness/o2_shroom.py, run_stack's browser "
            "canvases); hardware and phones never send it."),
    VerbRow("capture", "up", ("ssb",), ("dev", "action", "meta"), "tcp", False,
            "(recommended) tcp: a research telemetry-capture lifecycle "
            "verb (docs/telemetry-trace-schema.md) whose loss would leave "
            "a capture session stuck open. No code currently pins a "
            "reliability choice for this verb; this is the contract "
            "kit's own choice, not a measured fact."),
    VerbRow("telemetry", "up", ("sfb",), ("dev", "t0", "batch"), "tcp", False,
            "(recommended) tcp, for the same reason as capture: a dropped "
            "chunk shows up as a gap in the trace (capture/store.py)."),
    # --- down: Control -> device (/<dev>/<verb>) ---
    VerbRow("role", "down", ("b",), ("config",), "udp-ok", False,
            "Sent once a join is granted."),
    VerbRow("deny", "down", ("ss",), ("reason", "hint"), "udp-ok", True,
            "Sent in reply to a join Control refuses; the device holds no "
            "role before or after."),
    VerbRow("leds", "down", ("b",), ("frame",), "udp-ok", True,
            "Rule 3: a frame shows at its presentation time; when several "
            "are due, only the newest shows; the last frame holds. Also "
            "sent to a hello'd-but-unjoined device (lobby invite/"
            "handshake flashes)."),
    VerbRow("play", "down", ("ss",), ("name", "params"), "udp-ok", False,
            "Fires a device-local sample by name; an unknown name is the "
            "device's own business."),
    VerbRow("release", "down", ("",), (), "udp-ok", False,
            "Ends the role but does not clear the display."),
    VerbRow("error", "down", ("ss",), ("context", "message"), "udp-ok", True,
            "A handler-declared or engine-level refusal; changes no "
            "state."),
    VerbRow("room", "down", ("b",), ("blob",), "udp-ok", True,
            "Informational room snapshot, sent after every hello whether "
            "or not the device has joined; hardware ignores it."),
)

GAME_VERBS: tuple[str, ...] = tuple(
    row.verb for row in VERB_TABLE if row.direction == "up")


def row_for(direction: str, verb: str) -> VerbRow:
    for row in VERB_TABLE:
        if row.direction == direction and row.verb == verb:
            return row
    raise KeyError(f"no {direction} row for verb {verb!r}")


def typespec_allowed(direction: str, verb: str, typespec: str) -> bool:
    return typespec in row_for(direction, verb).typespecs
```

- [ ] **Step 4: Wire `devicelink/o2_transport.py`'s `GAME_VERBS` to the table**

Edit `devicelink/o2_transport.py`. Old text (the import block, lines 16-23):

```python
from __future__ import annotations

import base64
import json
import logging
import time

from control.wire_json import dumps as _json_dumps
```

New text:

```python
from __future__ import annotations

import base64
import json
import logging
import time

from control.wire_json import dumps as _json_dumps
from devicelink.contract import GAME_VERBS
```

Old text (lines 106-109):

```python
# Every /game/* verb the agent routes. Registered as full-path handlers so
# o2lite dispatches straight into the drain queue.
GAME_VERBS = ("hello", "join", "tilt", "tap", "shake", "capture",
              "telemetry", "canvas", "start")
```

New text:

```python
# Every /game/* verb the agent routes, imported above from
# devicelink/contract.py's verb table so GAME_VERBS is derived from it
# rather than kept as a second, hand-maintained list. Registered as
# full-path handlers so o2lite dispatches straight into the drain queue.
```

- [ ] **Step 5: Run the new tests, then the full transport/agent suites**

Run: `.venv/bin/python -m pytest tests/test_devicelink_contract.py -v`
Expected: 11 tests PASS.

Run: `.venv/bin/python -m pytest tests/test_o2_transport.py tests/test_devicelink_agent.py tests/test_devicelink_protocol.py -v`
Expected: all PASS (47 + existing devicelink_agent tests + devicelink_protocol
tests), confirming the `GAME_VERBS` re-wiring changed no observable
behavior.

- [ ] **Step 6: Commit**

```bash
git add devicelink/contract.py devicelink/o2_transport.py tests/test_devicelink_contract.py
git commit -m "feat(devicelink): verb table with hold/swing; GAME_VERBS derived from it"
```

## Task 2: `gesture.hold` / `gesture.swing` capabilities

**Files:**
- Modify: `control/instrument.py:18-26` (`CAPABILITY_VOCABULARY`)
- Modify: `docs/carried-instrument-schema.md`
- Test: `tests/test_instrument.py` (existing file; add one test)

**Interfaces:**
- Consumes: nothing new.
- Produces: `"gesture.hold"` and `"gesture.swing"` as valid entries of
  `control.instrument.CAPABILITY_VOCABULARY`, usable in any
  `Instrument.capabilities`/`InstrumentRequirement.capabilities` frozenset.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_instrument.py` (append; the file already imports
`CAPABILITY_VOCABULARY` and `Instrument`/`validate_instrument` at its top —
verify that with `grep -n "^from control.instrument\|^import" tests/test_instrument.py`
before adding, and reuse the existing import line rather than adding a
second one):

```python
def test_gesture_hold_and_gesture_swing_are_valid_capabilities():
    assert {"gesture.hold", "gesture.swing"} <= CAPABILITY_VOCABULARY
    inst = Instrument(name="rev1-check", pixels=12,
                      capabilities=frozenset({"light.pixels", "gesture.hold",
                                              "gesture.swing"}))
    validate_instrument(inst)   # must not raise
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_instrument.py -k gesture_hold_and_gesture_swing -v`
Expected: FAIL — `assert {"gesture.hold", "gesture.swing"} <= CAPABILITY_VOCABULARY`
is false.

- [ ] **Step 3: Add the two capabilities**

Edit `control/instrument.py`. Old text:

```python
CAPABILITY_VOCABULARY: frozenset[str] = frozenset({
    "light.pixels",    # addressable pixels of any shape
    "light.surface",   # a linear multi-zone surface (Room-style array)
    "audio.flsyn",     # an Arco FluidSynth voice reachable
    "audio.samples",   # local sample playback
    "audio.mic",       # a microphone input reachable
    "gesture.tap",
    "gesture.tilt",
})
```

New text:

```python
CAPABILITY_VOCABULARY: frozenset[str] = frozenset({
    "light.pixels",    # addressable pixels of any shape
    "light.surface",   # a linear multi-zone surface (Room-style array)
    "audio.flsyn",     # an Arco FluidSynth voice reachable
    "audio.samples",   # local sample playback
    "audio.mic",       # a microphone input reachable
    "gesture.tap",
    "gesture.tilt",
    "gesture.hold",    # a held press/touch, reported via /game/hold
    "gesture.swing",   # a signed lateral swing, reported via /game/swing
})
```

- [ ] **Step 4: Run the test again**

Run: `.venv/bin/python -m pytest tests/test_instrument.py -v`
Expected: all PASS, including the new one.

- [ ] **Step 5: Document both in `docs/carried-instrument-schema.md`**

Old text (the end of the "### The 12-LED floor" section, immediately
before "## Compatibility"):

```
### The 12-LED floor

`validate_instrument` refuses any instrument that declares `light.pixels`
with `pixels < 12` -- enforced once, at publish/config-load time, never
discovered on a device at runtime. `DEFAULTSHROOM` itself sits exactly on
the floor (`pixels = 12`), so the worst case a hello can resolve to is
still a 12-LED-capable host. An instrument with no `light.pixels`
capability (e.g. a fixed installation surface like `venue_array`) is
exempt -- the floor is about handheld/carried hosts, not every instrument
in the vocabulary.

## Compatibility
```

New text:

```
### The 12-LED floor

`validate_instrument` refuses any instrument that declares `light.pixels`
with `pixels < 12` -- enforced once, at publish/config-load time, never
discovered on a device at runtime. `DEFAULTSHROOM` itself sits exactly on
the floor (`pixels = 12`), so the worst case a hello can resolve to is
still a 12-LED-capable host. An instrument with no `light.pixels`
capability (e.g. a fixed installation surface like `venue_array`) is
exempt -- the floor is about handheld/carried hosts, not every instrument
in the vocabulary.

### `gesture.hold` and `gesture.swing`

Two more tags in `CAPABILITY_VOCABULARY` (`control/instrument.py`),
alongside `gesture.tap`/`gesture.tilt`: `gesture.hold` for a held press
reported over `/game/hold`, `gesture.swing` for a signed lateral swing
reported over `/game/swing`. Both are new wire verbs; see
`devicelink/contract.py`'s verb table for their exact shapes and
`docs/superpowers/specs/2026-09-16-device-contract-kit-design.md` section
5 for the design. Event trigger names are already unrestricted
(`[A-Za-z0-9_-]+` with numeric thresholds, `control/triggers.py`), so no
change was needed there to carry `hold`/`swing` thresholds in a role's
`triggers` blob.

## Compatibility
```

- [ ] **Step 6: Commit**

```bash
git add control/instrument.py docs/carried-instrument-schema.md tests/test_instrument.py
git commit -m "feat(control): add gesture.hold and gesture.swing capabilities"
```

## Task 3: `instruments/tuneshroom_rev1.toml`

**Files:**
- Create: `instruments/tuneshroom_rev1.toml`
- Create: `tests/test_tuneshroom_rev1_instrument.py`

**Interfaces:**
- Consumes: `control.catalog.load_catalog` (auto-scans `instruments/*.toml`
  by file stem — confirmed in `control/catalog.py`'s `load_catalog`, which
  globs `root.glob("*.toml")` with no separate registration list to
  update), `control.instrument.satisfies`/`InstrumentRequirement`/
  `TUNESHROOM`.
- Produces: a published catalog instrument named `tuneshroom_rev1`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tuneshroom_rev1_instrument.py
"""instruments/tuneshroom_rev1.toml: the Rev 1 hardware/hardware-profile
instrument (docs/superpowers/specs/2026-09-16-device-contract-kit-design.md
section 5.2)."""
from pathlib import Path

from control.catalog import load_catalog
from control.instrument import TUNESHROOM, InstrumentRequirement, satisfies

ROOT = Path(__file__).resolve().parents[1]
REV1_CAPABILITIES = frozenset({"light.pixels", "gesture.tap", "gesture.hold",
                               "gesture.swing", "audio.samples"})


def _catalog():
    return load_catalog(ROOT / "instruments")


def test_tuneshroom_rev1_loads_and_validates_as_published():
    entry = _catalog().get("published", "tuneshroom_rev1")
    assert entry is not None
    assert entry.state == "published"
    assert entry.error is None
    assert entry.instrument.name == "tuneshroom_rev1"
    assert entry.instrument.capabilities == REV1_CAPABILITIES
    assert entry.instrument.pixels == 12


def test_satisfies_admits_tuneshroom_rev1_for_the_rev1_requirement():
    rev1 = _catalog().published["tuneshroom_rev1"]
    req = InstrumentRequirement(slot="player", capabilities=REV1_CAPABILITIES)
    assert satisfies(rev1, req) is None


def test_satisfies_refuses_tuneshroom_for_the_rev1_requirement():
    req = InstrumentRequirement(slot="player", capabilities=REV1_CAPABILITIES)
    assert satisfies(TUNESHROOM, req) is not None
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_tuneshroom_rev1_instrument.py -v`
Expected: `test_tuneshroom_rev1_loads_and_validates_as_published` FAILs
(`entry is None`); the other two also FAIL (`KeyError: 'tuneshroom_rev1'`
from `_catalog().published[...]`).

- [ ] **Step 3: Write `instruments/tuneshroom_rev1.toml`**

```toml
description = "Rev 1 ESP32 Tuneshroom board: 12-LED ring+stem, touch pad tap/hold, LIS3DH swing, local samples"
pixels = 12
capabilities = ["light.pixels", "gesture.tap", "gesture.hold", "gesture.swing", "audio.samples"]

[[event_triggers]]
name = "tap"
description = "touch pad release under the tap window"
  [event_triggers.thresholds]
  max_ms = 250

[[event_triggers]]
name = "hold"
description = "touch pad held past the hold window"
  [event_triggers.thresholds]
  min_ms = 400

[[event_triggers]]
name = "swing"
description = "a sustained lateral acceleration past the swing threshold"
  [event_triggers.thresholds]
  peak_g = 1.5
  window_ms = 80
```

No `[ambient]`, `[[functions]]`, or `[solo]` table — spec 5.2 declares
none of the three for this instrument.

- [ ] **Step 4: Run the tests again**

Run: `.venv/bin/python -m pytest tests/test_tuneshroom_rev1_instrument.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run the full catalog suite (nothing else should move)**

Run: `.venv/bin/python -m pytest tests/test_catalog.py tests/test_instrument.py -v`
Expected: all PASS, unchanged.

- [ ] **Step 6: Commit**

```bash
git add instruments/tuneshroom_rev1.toml tests/test_tuneshroom_rev1_instrument.py
git commit -m "feat(instruments): add tuneshroom_rev1 (Rev 1 hardware capabilities)"
```

## Task 4: Doc amendments (ESP32 spec, ESP32 plan, `docs/MM_TERRARIUM.md`)

**Files:**
- Modify: `docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md`
  (§4.1, §10)
- Modify: `docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md`
  (Tasks A1, A2, A3, A4, A5, C2, D3; a new Task A8)
- Modify: `docs/MM_TERRARIUM.md` (one new entry)

This task is a documentation edit, not new application code — there is no
pytest step. Each numbered step below is one `Edit`-shaped old-text/new-text
pair, verified against the current file content read in full before this
plan was written. Run a final `grep`/read-back verification after each file
and commit once per file.

### 4a. ESP32 spec: §4.1 and §10

- [ ] **Step 1: §4.1 — firmware lives in mm-devshroom, and the platform pin is exact**

File: `docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md`

Old text:

```
### 4.1 Firmware framework: Arduino-ESP32 core, in PlatformIO

**(recommended)** Build the firmware on the Arduino-ESP32 core (3.2 or later,
which carries ESP32-P4 board support and ESP-Hosted for the C6 radio), in a
PlatformIO project committed to a new `firmware/` directory in mm-terrarium.
```

New text:

```
### 4.1 Firmware framework: Arduino-ESP32 core, in PlatformIO

**(recommended)** Build the firmware on the Arduino-ESP32 core, in a
PlatformIO project in its own repo, **mm-devshroom** (owned by Victor) --
not a `firmware/` directory in mm-terrarium (superseded by
`2026-09-16-device-contract-kit-design.md` D1). Pin `platformio.ini` to
pioarduino espressif32 55.3.311 (Arduino-ESP32 3.3.11), the release the
project's vendored o2lite update is built against, rather than tracking
the `stable` platform channel.
```

- [ ] **Step 2: §10 — the acceptance bullet naming mm-terrarium as the firmware's home**

Old text:

```
- [ ] Firmware, runbooks and the Room profile are on `main` in mm-terrarium.
```

New text:

```
- [ ] Firmware is on `main` in mm-devshroom; runbooks and the Room profile
      are on `main` in mm-terrarium.
```

- [ ] **Step 3: Verify and commit**

```bash
grep -n "firmware/\` directory in mm-terrarium\|are on \`main\` in mm-terrarium" docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md
```

Expected: no output (both old phrasings are gone).

```bash
git add docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md
git commit -m "docs(esp32-spec): firmware lives in mm-devshroom, pin the platform version (spec amendment)"
```

### 4b. ESP32 plan: drop the `firmware/` path prefix (mechanical, whole-Phase-A + D1/D2)

The plan's own Task 0.3 (Week 4 checkpoint) is explicitly outside this
spec's scope (§10's schedule: "Now to Fri Sep 18: Outside this spec") and
is left untouched. Tasks A1-A7 and D1-D2 all reference a `firmware/`
subdirectory that no longer exists once the firmware moves to its own
repo; drop the prefix mechanically, verified against the current file's
exact line ranges before any other edit in this task (line numbers below
are from the pre-edit file and are used only to scope the `sed`, never to
locate a string for a later step).

- [ ] **Step 1: Drop the prefix in Phase A (lines 289-1120) and Tasks D1/D2 (lines 1969-2010)**

```bash
sed -i '' \
  -e '289,1120s#cd firmware && ##g' \
  -e '289,1120s#firmware/##g' \
  -e '1969,2010s#cd firmware && ##g' \
  -e '1969,2010s#firmware/##g' \
  docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
```

- [ ] **Step 2: Verify no stray `firmware/` paths or `cd firmware` remain in those ranges, and that the tag/commit-label lines were correctly left alone**

```bash
awk 'NR>=289 && NR<=1120 && /firmware\//' docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
awk 'NR>=1969 && NR<=2010 && /firmware\//' docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
awk 'NR>=289 && NR<=1120 && /cd firmware/' docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
grep -n "firmware-dec4-rc1\|feat(firmware)\|docs(firmware)\|Owner:\*\* Victor (firmware)" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
```

Expected: the first three commands print nothing. The last command still
prints the tag name and the `feat(firmware)`/`docs(firmware)` commit-message
labels and the `Owner:** Victor (firmware)` line unchanged — those are not
paths and correctly survive the substitution.

### 4c. ESP32 plan: Task A1 — hello declares an instrument; VENDORED.md records the patches

- [ ] **Step 1: Hello grows to the 4-arg carried-instrument shape**

Old text (in Task A1's `link.cpp` code block, after the path-prefix drop
in 4b):

```cpp
void link_hello() {
  // Same shape as harness/o2_shroom.py's undeclared hello: typespec "s".
  o2l_send_start("/game/hello", 0, "s", true);   // true = tcp (command)
  o2l_add_string(DEVICE_NAME);
  o2l_send();
  Serial.printf("HELLO sent t=%.3f\n", link_time());
}
```

New text:

```cpp
void link_hello() {
  // ssss [dev, name, protoversion, instrument] -- the carried-instrument
  // hello shape (docs/carried-instrument-schema.md). name/protoversion are
  // left blank, matching harness/o2_shroom.py's own declared-hello callers;
  // INSTRUMENT_NAME is a build flag ("testshroom" for TEST-room bring-up,
  // "tuneshroom_rev1" once Mushica lands -- see config.h).
  o2l_send_start("/game/hello", 0, "ssss", true);   // true = tcp (command)
  o2l_add_string(DEVICE_NAME);
  o2l_add_string("");
  o2l_add_string("");
  o2l_add_string(INSTRUMENT_NAME);
  o2l_send();
  Serial.printf("HELLO sent t=%.3f instrument=%s\n", link_time(), INSTRUMENT_NAME);
}
```

- [ ] **Step 2: Add the `INSTRUMENT_NAME` build flag next to `HEARTBEAT_S`**

Old text (`config.h`, in Task A1's own scaffold block):

```cpp
#define HEARTBEAT_S 5.0
```

New text:

```cpp
#define HEARTBEAT_S 5.0
#ifndef INSTRUMENT_NAME
#define INSTRUMENT_NAME "testshroom"
#endif
```

- [ ] **Step 3: VENDORED.md records the upstream commit and the two build patches, not "do not edit"**

Old text:

```bash
mkdir -p src/link/o2lite
for f in o2lite.c o2lite.h o2base.h hostip.h hostipimpl.h o2liteesp32.cpp o2liteesp32.h; do
  cp ~/projects/o2/src/$f src/link/o2lite/$f
done
echo "Vendored from o2 commit $(git -C ~/projects/o2 rev-parse --short HEAD) on $(date +%F). Do not edit; report defects to Roger." > src/link/o2lite/VENDORED.md
```

New text:

```bash
mkdir -p src/link/o2lite
for f in o2lite.c o2lite.h o2base.h hostip.h hostipimpl.h o2liteesp32.cpp o2liteesp32.h; do
  cp ~/projects/o2/src/$f src/link/o2lite/$f
done
cat > src/link/o2lite/VENDORED.md <<EOF
Vendored from o2 commit $(git -C ~/projects/o2 rev-parse --short HEAD) on $(date +%F).

This copy carries blob-argument support and the 4096-byte message cap
needed to read /role, /room and /leds. It also carries two Arduino-core
build patches on top of upstream; list each one here (file, line, one
sentence on what it changes and why) as it's made, and report it to Roger
Dannenberg upstream the same day. Do not otherwise edit; pull a fresh copy
from o2/src/ instead.
EOF
```

- [ ] **Step 4: Verify and commit**

```bash
grep -n "INSTRUMENT_NAME\|do not edit; report defects to Roger" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
```

Expected: `INSTRUMENT_NAME` appears (new), the old lowercase "do not edit;
report defects to Roger" line is gone.

### 4d. ESP32 plan: Task A2 — release follows D5 (no code change needed), handlers registered once, reconnect re-checks ownership

Release already does not clear the display (Task A2's `on_release` only
sets `joined = false` and calls the optional `release_cb`; nothing in A2 or
A3 touches the pixel buffer on release) — no code change is needed there,
only a one-line note making that explicit for a future reader. The other
two amendments are real fixes: `register_methods()` currently runs again
every time `link_begin()` is re-run in Step 2's reconnect path, and the
reconnect path never re-checks that the hub still routes `DEVICE_NAME`
back to this connection.

- [ ] **Step 1: Register handlers once, and note that release follows D5**

Old text:

```cpp
void link_join(const char *node) {
  o2l_send_start("/game/join", 0, "ss", true);
  o2l_add_string(DEVICE_NAME);
  o2l_add_string(node);
  o2l_send();
  Serial.printf("JOIN sent node=%s\n", node);
}
bool link_joined() { return joined; }
void link_on_frame(void (*cb)(double, const uint8_t *, int)) { frame_cb = cb; }
void link_on_play(void (*cb)(const char *, const char *)) { play_cb = cb; }
void link_on_release(void (*cb)()) { release_cb = cb; }
```

New text:

```cpp
void link_join(const char *node) {
  o2l_send_start("/game/join", 0, "ss", true);
  o2l_add_string(DEVICE_NAME);
  o2l_add_string(node);
  o2l_send();
  Serial.printf("JOIN sent node=%s\n", node);
}
bool link_joined() { return joined; }
void link_on_frame(void (*cb)(double, const uint8_t *, int)) { frame_cb = cb; }
void link_on_play(void (*cb)(const char *, const char *)) { play_cb = cb; }
// D5: release ends the role but must not clear the display -- release_cb
// is free to leave the last frame exactly where frames_tick() put it.
void link_on_release(void (*cb)()) { release_cb = cb; }
```

Old text:

```
Call `register_methods()` at the end of `link_begin()`, after
`o2l_set_services`. In `link_poll()`, after the heartbeat, add: if synced,
not joined, and 3 s have passed since the last join attempt, call
`link_join(JOIN_NODE)` (mirrors `--join-every` in `o2_shroom.py`). Add
`-DJOIN_NODE=\"MUSHICA_PLAYER_NODE\"` to the `tuneshroom` env in
`platformio.ini`.
```

New text:

```
`register_methods()` is called ONCE, from the end of `link_begin()`, guarded
by a static bool -- `o2l_method_new` only ever APPENDS a handler and never
removes one, so calling `register_methods()` again on every reconnect
(Step 2 re-runs `link_begin()`) would register each address's handler
again on every reconnect and o2litepy/o2lite always dispatches to the
FIRST match, silently freezing every handler at its state from the first
registration:

```cpp
// firmware/src/link/link.cpp (link_begin, revised)
static bool handlers_registered = false;

void link_begin() {
  connect_to_wifi(DEVICE_NAME, WIFI_SSID, WIFI_PASS);   // blocks until joined
  o2l_initialize(ENSEMBLE);
  o2l_set_services(DEVICE_NAME);
  if (!handlers_registered) { register_methods(); handlers_registered = true; }
  Serial.printf("O2LITE INIT ensemble=%s service=%s\n", ENSEMBLE, DEVICE_NAME);
}
```

In `link_poll()`, after the heartbeat, add: if synced, not joined, and 3 s
have passed since the last join attempt, call `link_join(JOIN_NODE)`
(mirrors `--join-every` in `o2_shroom.py`). Add
`-DJOIN_NODE=\"MUSHICA_PLAYER_NODE\"` to the `tuneshroom` env in
`platformio.ini`.
```

- [ ] **Step 2: Reconnect re-checks ownership when the bridge id changes**

Old text:

```
- [ ] **Step 2: Reconnect**

In `link_poll()`, if `WiFi.status() != WL_CONNECTED`, call `o2l_finish()`,
then `link_begin()` again, and reset `joined = false`. Test it by power
cycling the AP: the device must be back in the Console list within 30 s of
the AP returning, with no reflash.
```

New text:

```
- [ ] **Step 2: Reconnect, and re-check service ownership when the bridge changes**

o2lite auto-reconnects and stamps a new `o2l_bridge_id` when it does; a
device that keeps running against a stale claim on `DEVICE_NAME` would
sync its clock fine while every reply Control sends is silently dropped
by the hub. Track the bridge id and re-verify ownership whenever it
changes -- the same shape `harness/o2_shroom.py`'s `reconnect_recheck`
already uses on the Python side, hand-rolled here the same way
`devicelink/o2_transport.py`'s `verify_service_ownership` is: register a
handler for `/DEVICE_NAME/_svcheck`, send a fixed nonce over TCP, and wait
for it to route back.

```cpp
// firmware/src/link/link.cpp (additions)
static long last_bridge_id = -1;
static bool svcheck_received = false;
static const int32_t SVCHECK_NONCE = 0x5643484B;   // "VCHK", matches the Python check

static void on_svcheck(o2l_msg_ptr, const char *, void *, void *) {
  if (o2l_get_int32() == SVCHECK_NONCE) svcheck_received = true;
}

static bool verify_ownership(double timeout_s) {
  char addr[40];
  snprintf(addr, 40, "/%s/_svcheck", DEVICE_NAME);
  svcheck_received = false;
  o2l_method_new(addr, "i", true, on_svcheck, NULL);
  o2l_send_start(addr, 0, "i", true);   // tcp, same as the Python check
  o2l_add_int32(SVCHECK_NONCE);
  o2l_send();
  double deadline = link_time() + timeout_s;
  while (!svcheck_received && link_time() < deadline) { o2l_poll(); delay(5); }
  return svcheck_received;
}

void link_poll() {
  o2l_poll();
  if (link_synced()) {
    double now = link_time();
    if (now - last_hello >= HEARTBEAT_S) { link_hello(); last_hello = now; }
  }
  if (WiFi.status() != WL_CONNECTED) {
    o2l_finish();
    link_begin();
    joined = false;
    last_bridge_id = -1;                 // force a fresh ownership check below
  }
  if (link_synced() && o2l_bridge_id != last_bridge_id) {
    bool owned = verify_ownership(2.0);
    Serial.printf("BRIDGE %ld -> %ld, ownership %s\n",
                  last_bridge_id, o2l_bridge_id, owned ? "OK" : "LOST");
    last_bridge_id = o2l_bridge_id;
    if (!owned) joined = false;          // never trust a role granted on a service we no longer own
  }
}
```

Test by power cycling the AP: the device must be back in the Console list
within 30 s of the AP returning, with no reflash, and `BRIDGE ... OK`
printed once per reconnect rather than once per `link_poll()` call.
```

- [ ] **Step 3: Verify and commit**

```bash
grep -n "handlers_registered\|last_bridge_id\|o2l_bridge_id" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
```

Expected: all three appear in Task A2's revised text.

```bash
git add docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs(esp32-plan): drop firmware/ prefix; hello declares an instrument, handlers register once, reconnect re-checks ownership"
```

### 4e. ESP32 plan: Task A3 — state Rule 3 explicitly (no code change; the existing `FrameQueue::due` already implements it)

- [ ] **Step 1: Add the rule to the task's own description**

Old text (Task A3's **Interfaces** block):

```
**Interfaces:**
- Consumes: `link_on_frame`, `link_time`.
- Produces: `frames_begin(pin, count)`, `frames_push(when, grb, n)`,
  `frames_tick(now)` (shows the newest frame whose `when <= now`),
  `frames_limit(const uint8_t *grb, int n, float max_amps)` (pure; scales a
  frame so the modelled draw stays under `max_amps`), `int frames_late()`.
```

New text:

```
**Interfaces:**
- Consumes: `link_on_frame`, `link_time`.
- Produces: `frames_begin(pin, count)`, `frames_push(when, grb, n)`,
  `frames_tick(now)` (shows the newest frame whose `when <= now`),
  `frames_limit(const uint8_t *grb, int n, float max_amps)` (pure; scales a
  frame so the modelled draw stays under `max_amps`), `int frames_late()`.
  Rule 3 (docs/superpowers/specs/2026-09-16-device-contract-kit-design.md):
  a frame shows at its presentation time; when several are due, only the
  newest shows; the last frame holds through silence. `FrameQueue::due`
  below already has this shape -- it advances `pick` to the LAST (highest-
  index, i.e. most recently pushed) due frame and drops everything up to
  and including it, so no code change is needed here, only this note.
```

- [ ] **Step 2: Verify and commit**

```bash
grep -n "Rule 3" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git add docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs(esp32-plan): state Rule 3 explicitly in Task A3 (behavior already correct)"
```

### 4f. ESP32 plan: Task A4 — hold/swing shapes match §5.1, count is always 1, gestures other than tap wait for a role

- [ ] **Step 1: Fix the wire shapes and the pre-role rule in the Interfaces block**

Old text:

```
**Interfaces:**
- Consumes: `link_time()`, `o2l_send_*` (through a `link_send_gesture` added here).
- Produces on the wire, all with the device's O2-time stamp as the message time:
  - `/game/tap  "sffi"  dev, peak_g, duration_ms, count` (existing shape; `bits/test/test_bit.py` documents it)
  - `/game/hold "sfi"   dev, held_seconds, count` (new verb, Task C2 handles it)
  - `/game/swing "sfi"  dev, signed_peak_g, count` (new verb; negative = left)
```

New text:

```
**Interfaces:**
- Consumes: `link_time()`, `o2l_send_*` (through a `link_send_gesture` added here).
- Produces on the wire, all with the device's O2-time stamp as the message time
  (`devicelink/contract.py`'s verb table is the checked source for these shapes):
  - `/game/tap  "sffi"  dev, peak_g, duration_ms, count` (peak_g is 0 for a
    touch tap; count is always 1 -- Control pairs double taps itself)
  - `/game/hold "sfi"   dev, held_seconds, count` (new verb, Task C2 handles it; count always 1)
  - `/game/swing "sfi"  dev, signed_peak_g, count` (new verb; negative = left; count always 1)
  `tap` may be sent before a role (the lobby's tap-to-join handshake);
  `hold` and `swing` wait until `link_joined()` is true.
```

- [ ] **Step 2: Gate hold/swing on `link_joined()` and fix `count` to a literal 1 in `send()`**

Old text:

```cpp
static void send(const Gesture &g) {
  switch (g.kind) {
    case GESTURE_TAP:
      link_send_gesture("/game/tap", g.at, "sffi", 0.0f, g.value * 1000.0f, ++tap_count); break;
    case GESTURE_HOLD:
      link_send_gesture("/game/hold", g.at, "sfi", g.value, 0.0f, ++hold_count); break;
    case GESTURE_SWING:
      link_send_gesture("/game/swing", g.at, "sfi", g.value, 0.0f, ++swing_count); break;
    default: return;
  }
  Serial.printf("GESTURE %d at=%.3f value=%.3f\n", g.kind, g.at, g.value);
}
```

New text:

```cpp
static void send(const Gesture &g) {
  // count is always 1 on Rev 1 -- Control pairs double taps from two
  // separate count=1 messages (D6); tap_count/hold_count/swing_count
  // below are diagnostics only, not the wire value.
  switch (g.kind) {
    case GESTURE_TAP:
      link_send_gesture("/game/tap", g.at, "sffi", 0.0f, g.value * 1000.0f, 1); ++tap_count; break;
    case GESTURE_HOLD:
      if (!link_joined()) return;   // hold waits for a role
      link_send_gesture("/game/hold", g.at, "sfi", g.value, 0.0f, 1); ++hold_count; break;
    case GESTURE_SWING:
      if (!link_joined()) return;   // swing waits for a role
      link_send_gesture("/game/swing", g.at, "sfi", g.value, 0.0f, 1); ++swing_count; break;
    default: return;
  }
  Serial.printf("GESTURE %d at=%.3f value=%.3f\n", g.kind, g.at, g.value);
}
```

- [ ] **Step 3: Verify and commit**

```bash
grep -n "waits for a role\|count is always 1" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git add docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs(esp32-plan): hold/swing wait for a role, count is always 1 (matches the contract kit's verb table)"
```

### 4g. ESP32 plan: Task A5 — synthesize the join chime from `key=`

- [ ] **Step 1: `link_on_play`'s callback recognizes `chime` and synthesizes it from `key=`**

Old text:

```
Add `I2S_BCLK`, `I2S_LRC`, `I2S_DOUT` to `config.h` (record in the runbook).
In `gestures.cpp`, call `audio_play("tick")` **before** `send()` on a tap
and `audio_play("hold")` on a hold. In `main.cpp`, `audio_begin()` in
`setup()`, `audio_pump()` every `loop()`, and `link_on_play(audio_play_cb)`
where `audio_play_cb(name, params)` calls `audio_play(name)`.
```

New text:

```
Add `I2S_BCLK`, `I2S_LRC`, `I2S_DOUT` to `config.h` (record in the runbook).
In `gestures.cpp`, call `audio_play("tick")` **before** `send()` on a tap
and `audio_play("hold")` on a hold. In `main.cpp`, `audio_begin()` in
`setup()`, `audio_pump()` every `loop()`, and `link_on_play(audio_play_cb)`
where `audio_play_cb` synthesizes the join ceremony's chime (D9: the board
has no bundled "chime" sample, so it plays a short tone at the `key=`
MIDI note instead of staying silent) and otherwise defers to `audio_play`:

```cpp
static void audio_play_cb(const char *name, const char *params) {
  if (!strcmp(name, "chime")) {
    int key = 69;                       // NOTE_SCALE's own default (A4)
    const char *eq = strstr(params, "key=");
    if (eq) key = atoi(eq + 4);
    audio_play_tone_for_midi_key(key);  // a short sine/square burst at that pitch; no PCM asset needed
    return;
  }
  audio_play(name);
}
```
```

- [ ] **Step 2: Verify and commit**

```bash
grep -n "audio_play_cb\|D9" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git add docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs(esp32-plan): synthesize the join chime from key= (D9)"
```

### 4h. ESP32 plan: a new Task A8 — adopt native scenario replay from the exported contract

- [ ] **Step 1: Insert Task A8 right after Task A7, before the Phase A/B separator**

Old text (the tail of Task A7 and the start of Phase B):

```
- [ ] **Step 4: Commit**

```bash
git commit -m "feat(firmware): tower build target, 120 px, 2.4 A limiter, no sensors"
```

---

# Phase B: Hardware (Sophia)
```

New text:

```
- [ ] **Step 4: Commit**

```bash
git commit -m "feat(firmware): tower build target, 120 px, 2.4 A limiter, no sensors"
```

## Task A8 [FW]: Native scenario replay against the exported contract

**Owner:** Victor. **Gated on:** mm-terrarium's device contract kit Phase 3
(the first `test/contract/` export) landing first -- do not start this task
until `test/contract/contract.json` and `test/contract/scenarios/*.json`
exist in this repo.

**Files:**
- Create: `test/test_scenarios/test_scenarios.cpp`, `tools/scenario2h.py`
- Modify: `platformio.ini` (reuse the `[env:native]` environment Task A3 added)

**Interfaces:**
- Consumes: `test/contract/contract.json`, `test/contract/scenarios/*.json`
  (mm-terrarium's `tools/export_contract.py` output, committed here), the
  `link_*`/`frames_*`/`gestures_core.h` interfaces from A1-A4.
- Produces: a native (Mac-hosted, `pio test -e native`) replay of every
  `"rev1"`-or-`"any"`-tagged scenario against the Arduino-free
  session/classification code, plus two contract checks: the vendored
  o2lite exposes a blob type and a 4096-byte message cap, and the
  built-in Rev 1 thresholds (`TAP_MAX_S`, `HOLD_MIN_S`, the `SwingDetector`
  constants) match `contract.json`'s `instruments.tuneshroom_rev1.triggers`.

- [ ] **Step 1: Embed each scenario file as a header, in the style of A5's `wav2h.py`**

```python
# firmware/tools/scenario2h.py
"""scenario2h: JSON scenario file -> a C header holding it as a raw string.
Usage: scenario2h.py in.json name > out.h"""
import json, sys
path, name = sys.argv[1], sys.argv[2]
text = json.dumps(json.load(open(path)))            # re-serialize compactly; content is unchanged
escaped = text.replace("\\", "\\\\").replace('"', '\\"')
print(f'#pragma once\nstatic const char *{name}_json = "{escaped}";')
```

Run it for every `test/contract/scenarios/*.json` file into
`test/test_scenarios/scenarios_generated/`, and commit the generated
headers alongside the source JSON (both are checked in, same as A5's
`tick.h`/`hold.h` next to `tick.wav`/`hold.wav`) so `pio test` needs no
network or generation step.

- [ ] **Step 2: A header-only JSON parser, tests only**

Vendor a single-header JSON library (e.g. nlohmann/json's `json.hpp`) under
`test/test_scenarios/`, used only by these tests -- never by `src/`, which
must stay free of any dependency this heavy.

- [ ] **Step 3: Write the replay driver and the two contract checks**

```cpp
// firmware/test/test_scenarios/test_scenarios.cpp
#include <unity.h>
#include "json.hpp"
#include "../../src/sense/gestures_core.h"
#include "../../src/render/frames_core.h"
#include "scenarios_generated/boot_hello_heartbeat.h"
// ... one #include per scenario_generated header ...
#include "contract_generated.h"                      // scenario2h.py run once more on contract.json

using nlohmann::json;

void test_contract_declares_blob_and_4096_byte_cap() {
  json c = json::parse(contract_json);
  TEST_ASSERT_TRUE(std::find(c["link"]["arg_types"].begin(),
                             c["link"]["arg_types"].end(), "b")
                    != c["link"]["arg_types"].end());
  TEST_ASSERT_EQUAL(4096, c["link"]["max_message_bytes"].get<int>());
}

void test_builtin_rev1_thresholds_match_the_export() {
  json c = json::parse(contract_json);
  json triggers = c["instruments"]["tuneshroom_rev1"]["triggers"];
  TEST_ASSERT_FLOAT_WITHIN(0.001, triggers["tap"]["max_ms"].get<double>() / 1000.0,
                           TouchClassifier::TAP_MAX_S);
  TEST_ASSERT_FLOAT_WITHIN(0.001, triggers["hold"]["min_ms"].get<double>() / 1000.0,
                           TouchClassifier::HOLD_MIN_S);
}

// One replay function per scenario: feed control_sends/gesture steps in
// t order into a session built from gestures_core.h + frames_core.h, and
// check expect_frame/expect_play/expect_out/expect_quiet the same way
// mm-tuneshroom's replay runner will (see that repo's plan, Phase 4).
void test_replay_boot_hello_heartbeat() {
  json s = json::parse(boot_hello_heartbeat_json);
  // ... drive FrameQueue/TouchClassifier/SwingDetector against s["steps"] ...
  TEST_FAIL_MESSAGE("replay driver not written yet");   // fails until the
                        // driver above it is implemented: a red stop sign,
                        // never a green test that checks nothing
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_contract_declares_blob_and_4096_byte_cap);
  RUN_TEST(test_builtin_rev1_thresholds_match_the_export);
  RUN_TEST(test_replay_boot_hello_heartbeat);
  // ... one RUN_TEST per scenario ...
  return UNITY_END();
}
```

- [ ] **Step 4: Run and commit**

Run: `pio test -e native -f test_scenarios`
Expected: every test PASSes once the replay driver is filled in; the two
contract checks pass immediately given a correct `contract.json`.

```bash
git add test/test_scenarios/ tools/scenario2h.py platformio.ini
git commit -m "feat(firmware): native replay of the exported device contract scenarios"
```

---

# Phase B: Hardware (Sophia)
```

- [ ] **Step 2: Verify and commit**

```bash
grep -n "Task A8" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git add docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs(esp32-plan): add Task A8, native scenario replay against the exported contract"
```

### 4i. ESP32 plan: Task C2 — Mushica's player role `requires` the Rev 1 capabilities

- [ ] **Step 1: Import `InstrumentRequirement` in `mushica_bit.py`**

Old text:

```python
from control.bit import Bit
from control.cues import ROOM, TARGET, FireFunction, PlayCue
from control.functions import (
    Condition, ConditionSource, ScriptStep, Function, FunctionTable,
    FunctionTarget,
)
from control.roles import Role, RoleClass, RoleTable
```

New text:

```python
from control.bit import Bit
from control.cues import ROOM, TARGET, FireFunction, PlayCue
from control.functions import (
    Condition, ConditionSource, ScriptStep, Function, FunctionTable,
    FunctionTarget,
)
from control.instrument import InstrumentRequirement
from control.roles import Role, RoleClass, RoleTable
```

- [ ] **Step 2: `player` declares `requires`, and a new `instrument_requirements()`**

Old text:

```python
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.UNIQUE, capacity=1, scored=True,
            uses=["tap", "hold", "swing"], breath=False,
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.58, "level": 0.35},
                 "lanes": [{"source": f"cc:{CC_HUE}", "dest": "hue"},
                           {"source": f"cc:{CC_LEVEL}", "dest": "level"}]},
                {"instrument": "bloom", "target": "primary", "params": {"hue": 0.58},
                 "lanes": [{"source": "note", "dest": "trigger"},
                           {"source": f"cc:{CC_FLASH}", "dest": "hue"}]},
            ]},
        )
        return RoleTable(roles={"player": player},
                         node_map={"MUSHICA_PLAYER_NODE": ["player"]})
```

New text:

```python
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.UNIQUE, capacity=1, scored=True,
            uses=["tap", "hold", "swing"], breath=False,
            requires="rev1",
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.58, "level": 0.35},
                 "lanes": [{"source": f"cc:{CC_HUE}", "dest": "hue"},
                           {"source": f"cc:{CC_LEVEL}", "dest": "level"}]},
                {"instrument": "bloom", "target": "primary", "params": {"hue": 0.58},
                 "lanes": [{"source": "note", "dest": "trigger"},
                           {"source": f"cc:{CC_FLASH}", "dest": "hue"}]},
            ]},
        )
        return RoleTable(roles={"player": player},
                         node_map={"MUSHICA_PLAYER_NODE": ["player"]})

    def instrument_requirements(self) -> tuple:
        """The "rev1" slot the player role's `requires` names above: gates
        a join on the five capabilities the device contract kit defines
        for Rev 1 hardware (docs/superpowers/specs/
        2026-09-16-device-contract-kit-design.md D8). A carrier declaring
        `tuneshroom` (the app's full profile) is refused by name; the
        board and the app's `rev1` hardware profile both satisfy it."""
        return (InstrumentRequirement(
            slot="rev1",
            capabilities=frozenset({"light.pixels", "gesture.tap",
                                    "gesture.hold", "gesture.swing",
                                    "audio.samples"})),)
```

- [ ] **Step 3: Add the gating test to Task C2's Step 1**

Old text (the import line at the top of the test's Step 1 code block):

```python
# tests/test_mushica_bit.py
import pytest
from control.cues import FireFunction, ROOM
from bits.mushica.mushica_bit import MushicaBit, PERFECT_S, GOOD_S
```

New text:

```python
# tests/test_mushica_bit.py
from pathlib import Path

import pytest
from control.catalog import load_catalog
from control.cues import FireFunction, ROOM
from control.instrument import TUNESHROOM, satisfies
from bits.mushica.mushica_bit import MushicaBit, PERFECT_S, GOOD_S

ROOT = Path(__file__).resolve().parents[1]
```

Old text:

```python
def test_role_table_declares_one_scored_player_with_three_gestures():
    rt = make().role_table()
    player = rt.roles["player"]
    assert player.scored and player.capacity == 1
    assert set(player.uses) >= {"tap", "hold", "swing"}
    assert rt.node_map["MUSHICA_PLAYER_NODE"] == ["player"]

def test_verb_handlers_cover_tap_hold_swing():
```

New text:

```python
def test_role_table_declares_one_scored_player_with_three_gestures():
    rt = make().role_table()
    player = rt.roles["player"]
    assert player.scored and player.capacity == 1
    assert set(player.uses) >= {"tap", "hold", "swing"}
    assert rt.node_map["MUSHICA_PLAYER_NODE"] == ["player"]

def test_requires_admits_tuneshroom_rev1_and_refuses_tuneshroom():
    req = make().instrument_requirements()[0]
    rev1 = load_catalog(ROOT / "instruments").published["tuneshroom_rev1"]
    assert satisfies(rev1, req) is None
    assert satisfies(TUNESHROOM, req) is not None

def test_verb_handlers_cover_tap_hold_swing():
```

- [ ] **Step 4: Verify and commit**

```bash
grep -n "requires=\"rev1\"\|instrument_requirements\|test_requires_admits_tuneshroom_rev1" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git add docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs(esp32-plan): Mushica's player role requires the Rev 1 capabilities (D8)"
```

### 4j. ESP32 plan: Task D3 — record the contract kit and the firmware repo in `docs/MM_TERRARIUM.md`

- [ ] **Step 1: Broaden Step 1's instruction**

Old text:

```
- [ ] **Step 1: Add a `firmware/` section to `docs/MM_TERRARIUM.md` under *Landed subsystems*: the two build targets, the thin-device rule, the verbs and their arg shapes, the limiter figures, the measured latency and stamp numbers with their dates**
```

New text:

```
- [ ] **Step 1: Add a section to `docs/MM_TERRARIUM.md` under *Landed subsystems*: that Rev 1 firmware lives in its own repo, mm-devshroom (owned by Victor, D1 of `2026-09-16-device-contract-kit-design.md`), the two build targets, the thin-device rule, the verbs and their arg shapes (`devicelink/contract.py`'s verb table), the limiter figures, the measured latency and stamp numbers with their dates, and that mm-devshroom commits an export of the device contract kit at `test/contract/` (see that spec's section 4.2-4.3)**
```

- [ ] **Step 2: Verify and commit**

```bash
grep -n "mm-devshroom (owned by Victor" docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git add docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs(esp32-plan): Task D3 records the contract kit and mm-devshroom in the deep-dive"
```

### 4k. `docs/MM_TERRARIUM.md`: the contract kit's own landed-subsystem entry

- [ ] **Step 1: Add the entry at the end of *Landed subsystems*, right before *Boundary rules***

Old text (the boundary between the last landed-subsystem entry and the
next top-level section):

```
### `console/agent.py`'s `_ensure_room_for_bit` -- a failed post-load client restart was invisible except to the requester (2026-09-14)
```

Find this heading's full existing body first (`grep -n "console/agent.py.*_ensure_room_for_bit" docs/MM_TERRARIUM.md`
then read to the next `^## ` heading, which is "## Boundary rules"), and
insert the new entry immediately after that body ends, still before
`## Boundary rules (the load-bearing invariants)`. New text to insert
there (date it the day this task is actually committed, via
`` `$(date +%F)` ``, not a hardcoded guess):

```
### `devicelink/contract.py`, `contract_kit/`, `tools/export_contract.py` -- the device contract kit (2026-09-21)
`docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`, Phases 1
and 3, landed. mm-terrarium now owns one checked contract for the device
wire, shared by three programs that speak it: the Python Testshroom
(`harness/`), the Flutter Tuneshroom app (mm-tuneshroom, over o2ws), and
the Rev 1 ESP32 firmware (**mm-devshroom**, a new repo owned by Victor --
not a `firmware/` directory here).

- **Verb table.** `devicelink/contract.py` holds one row per verb, up
  (`/game/<verb>`) and down (`/<dev>/<verb>`); `devicelink/o2_transport.py`'s
  `GAME_VERBS` is derived from it. Two new up verbs, `hold` and `swing`
  (`sfi` each), for Rev 1's touch-hold and accelerometer-swing gestures.
- **Capabilities.** `gesture.hold` and `gesture.swing` join
  `CAPABILITY_VOCABULARY` (`control/instrument.py`).
- **Catalog.** A new published instrument, `instruments/tuneshroom_rev1.toml`
  (12 pixels; `light.pixels`, `gesture.tap`, `gesture.hold`,
  `gesture.swing`, `audio.samples`; no ambient light, functions, or solo
  table) -- what Bit testing gates a Rev 1-only role on, the way Mushica's
  player role does.
- **Contract Bit, recorder, export.** A test-only `ContractBit`
  (`contract_kit/`, never registered under `bits/`) exercises join,
  timed frames, gestures, deny, release and malformed input against a
  recording rig built on `O2LiteTransport`/`FakeO2Lite`. Eleven scenarios
  are committed as JSON (`contract_kit/recordings/`) and re-recorded with
  `.venv/bin/python -m tools.record_scenarios`; a regression test fails on
  any diff. `tools/export_contract.py <out-dir>` packages the verb table,
  the Rev 1 instrument, and the scenarios into the folder every device
  repo commits at `test/contract/`.
- **Suite at HEAD:** run `.venv/bin/python -m pytest tests -v` and record
  the actual pass count here before committing this entry.
```

- [ ] **Step 2: Verify and commit**

```bash
grep -n "the device contract kit (2026" docs/MM_TERRARIUM.md
git add docs/MM_TERRARIUM.md
git commit -m "docs(mm-terrarium): record the device contract kit landing"
```

## Phase 1 checkpoint

- [ ] **Step 1: Full suite green**

Run: `.venv/bin/python -m pytest tests -v 2>&1 | tail -20`
Expected: all tests PASS (the pre-existing skip count from Setup Step 2 is
the only acceptable non-pass), with the new `test_devicelink_contract.py`,
`test_tuneshroom_rev1_instrument.py`, and the `test_instrument.py`
addition all present in the summary.

- [ ] **Step 2: Open the Phase 1 PR**

```bash
git push -u origin claude/device-contract-kit-terrarium
gh pr create --base claude/device-contract-kit-spec --title "Device contract kit, Phase 1: verb table, capabilities, tuneshroom_rev1, ESP32 doc amendments" --body "$(cat <<'EOF'
## Summary
- devicelink/contract.py: one verb table (up + down rows), GAME_VERBS derived from it, hold/swing added
- gesture.hold / gesture.swing capabilities, documented in docs/carried-instrument-schema.md
- instruments/tuneshroom_rev1.toml: the Rev 1 catalog instrument
- ESP32 spec/plan amendments (firmware lives in mm-devshroom; hello declares an instrument; handlers register once; reconnect re-checks ownership; hold/swing wait for a role; Mushica requires the Rev 1 capabilities; a new native-replay task)
- docs/MM_TERRARIUM.md: the contract kit's landed-subsystem entry

## Test plan
- [ ] .venv/bin/python -m pytest tests -v is green
EOF
)"
```

Note the base branch: `claude/device-contract-kit-spec`, not `main` --
Phase 3 continues from this PR's merge point.

---

# Phase 3 (weeks of Sep 21 and Sep 28)

Phase 3 depends on Phase 1's `devicelink/contract.py`, `instruments/tuneshroom_rev1.toml`,
and `gesture.hold`/`gesture.swing` already being on the branch this work
continues from (merge Phase 1's PR, or branch Phase 3 from it directly).

## Module locations (decision)

Three constraints drove where the contract Bit, recorder, scenario
scripts, and recordings live, none of which an existing directory
satisfies on its own:

1. **Not `bits/`** (spec 5.4: the contract Bit must not be discoverable as
   a venue Bit). Confirmed `bits/__init__.py` is empty and nothing scans
   `bits/` implicitly -- `BitRegistry.scan()` only runs when a caller
   invokes it -- but `bits/test/` (`TestBit`) IS scannable (it ships a real
   `bit.toml`) and is exactly the shape to avoid here. `GameServer`'s
   constructor takes a plain `dict` (`GameServer({"ContractBit":
   ContractBit}, ...)`, verified in `control/engine.py`), so a Bit class
   never needs to live under `bits/` at all to be loadable.
2. **Not `devicelink/contract/`** (an explicit constraint: `devicelink/contract.py`
   already claims that name as a module, not a package).
3. **Pytest only collects `tests/`** (`README.md`'s documented command is
   `.venv/bin/python -m pytest tests -v`), so the actual regression test
   function must live there; but `tools/record_scenarios.py` needs to
   import the same scenario-running code from a plain CLI context with no
   pytest fixtures available, so the shared library code should not live
   inside `tests/` either.

**(recommended)** A new top-level package, `contract_kit/`, holds
everything reusable (the Bit, the recorder, the eleven scenario functions,
the committed recordings); `tests/test_contract_scenarios.py` is the thin
pytest wrapper that imports from it. This is the only arrangement that
satisfies all three constraints at once, and its name matches the spec's
own title ("device contract kit").

```
contract_kit/
  __init__.py
  contract_bit.py       # ContractBit, CONTRACT_PLAYER_NODE
  recorder.py            # Recorder: the O2LiteTransport(FakeO2Lite) rig
  scenarios.py            # the eleven scenario functions, ALL_SCENARIOS
  recordings/
    boot_hello_heartbeat.json
    explicit_join_role.json
    lobby_tap_join.json
    timed_frames_hold_last.json
    gestures_after_role.json
    deny_stays_hellod.json
    release_keeps_display.json
    play_known_and_unknown.json
    link_loss_rejoin.json
    error_no_state_change.json
    malformed_dropped.json
tests/
  test_contract_scenarios.py   # the regression test: re-record, diff against recordings/
tools/
  record_scenarios.py           # CLI: re-record every scenario, overwrite recordings/
  export_contract.py <out-dir>  # package contract.json + scenarios/ for a device repo
```

## Task 5: The Contract Bit

**Files:**
- Create: `contract_kit/__init__.py`, `contract_kit/contract_bit.py`
- Test: `tests/test_contract_bit.py`

**Interfaces:**
- Consumes: `control.bit.Bit`, `control.cues.LightCue`/`PlayCue`,
  `control.instrument.InstrumentRequirement`, `control.roles.Role`/`RoleClass`/`RoleTable`.
- Produces: `ContractBit` (a `Bit` subclass), `CONTRACT_PLAYER_NODE = "CONTRACT_PLAYER_NODE"`.
  `ContractBit().role_table` is a **property** (matching `Bit`'s own
  `@property @abstractmethod role_table`, and `bits/test/test_bit.py`'s
  `TestBit` -- not a plain method, which is what `GameServer.load_bit`
  actually calls via bare attribute access).

Design notes verified against the running engine before writing this
task's code (not asserted from the spec text alone):

- `GameServer.data()` only ever calls a Bit's verb handler with `(dev,
  args, at)`, where `at` is already `origin(gesture_time) + cue_horizon`
  -- a presentation time, not a raw clock reading. A handler that wants a
  frame to show LATER than "as soon as possible" returns a `LightCue`
  with an explicit `when` computed from that same `at` (`control/cues.py`'s
  `LightCue.when`); `_on_light_cue` queues it until `when - horizon`
  arrives (`devicelink/agent.py`), which is what makes "a future-stamped
  frame" possible with no Bit-side clock or timer at all.
- `Bit.on_join`'s return value is never read by `GameServer.join()` --
  there is no way for a Bit to emit a cue purely from a join. The
  "timed look" therefore rides the `tap` verb handler (already legal
  both before and after a role), not `on_join`.
- The `player` role's `light_manifest` maps ONLY `cc:74` (not `cc:11`,
  the breath lane) so Control's own per-tick breath feed
  (`devicelink/agent.py`'s `_feed_breath`, driven unconditionally for
  every joined bridge unless `Role.breath` is `False`) cannot change the
  rendered frame; `breath=False` is set anyway, for the same "goes
  quiet" guarantee, belt and suspenders.
- Setting the SAME target `when` from two separate tap gestures (rather
  than a time-rounding scheme) is what scenario 4 (Task 7) uses to
  produce "two frames due at the same time" deterministically: two taps
  scripted with the identical `onset_t` compute the identical `at`, hence
  the identical `when = at + _LOOK_LEAD_S`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_contract_bit.py
"""ContractBit: the device contract kit's test-only fixture (docs/superpowers/
specs/2026-09-16-device-contract-kit-design.md section 5.4). Exercised
directly here (unit-level, no transport); contract_kit/recorder.py and
tests/test_contract_scenarios.py exercise it end to end over the wire."""
import pytest

pytest.importorskip("luxaeterna")

from control.cues import LightCue, PlayCue
from control.instrument import DEFAULTSHROOM, satisfies
from contract_kit.contract_bit import CONTRACT_PLAYER_NODE, ContractBit


def test_role_table_has_one_scored_player_node_requiring_rev1():
    bit = ContractBit()
    rt = bit.role_table
    player = rt.roles["player"]
    assert player.scored
    assert rt.node_map[CONTRACT_PLAYER_NODE] == ["player"]
    assert player.requires == "rev1"


def test_instrument_requirements_admit_rev1_and_refuse_defaultshroom():
    req = ContractBit().instrument_requirements()[0]
    assert req.slot == "rev1"
    assert req.capabilities == frozenset({
        "light.pixels", "gesture.tap", "gesture.hold", "gesture.swing",
        "audio.samples"})
    assert satisfies(DEFAULTSHROOM, req) is not None   # no hold/swing/samples


def test_first_tap_returns_a_play_and_a_future_light_cue():
    bit = ContractBit()
    cues = bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 1], at=10.0)
    plays = [c for c in cues if isinstance(c, PlayCue)]
    lights = [c for c in cues if isinstance(c, LightCue)]
    assert plays and plays[0].name == "tick"
    assert lights and lights[0].when == pytest.approx(10.0 + 0.5)


def test_second_tap_at_the_same_at_targets_the_same_when_with_a_new_value():
    bit = ContractBit()
    first = bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 1], at=10.0)
    second = bit.verb_handlers()["tap"]("ie1", ["ie1", 0.0, 80.0, 1], at=10.0)
    light1 = next(c for c in first if isinstance(c, LightCue))
    light2 = next(c for c in second if isinstance(c, LightCue))
    assert light1.when == light2.when
    assert light1.data2 != light2.data2


def test_hold_plays_an_unknown_sample_name():
    bit = ContractBit()
    cues = bit.verb_handlers()["hold"]("ie1", ["ie1", 0.65, 1], at=10.0)
    assert any(isinstance(c, PlayCue) and c.name == "not_a_real_sample"
              for c in cues)


def test_swing_is_handled_with_no_cues():
    bit = ContractBit()
    assert bit.verb_handlers()["swing"]("ie1", ["ie1", -2.1, 1], at=10.0) == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_contract_bit.py -v`
Expected: `ModuleNotFoundError: No module named 'contract_kit'`.

- [ ] **Step 3: Write `contract_kit/__init__.py` and `contract_kit/contract_bit.py`**

```python
# contract_kit/__init__.py
"""Test-only fixtures for mm-terrarium's device contract kit
(docs/superpowers/specs/2026-09-16-device-contract-kit-design.md).
Nothing here is a venue Bit and nothing in control/, devicelink/, or
harness/ (production code) imports from this package."""
```

```python
# contract_kit/contract_bit.py
"""ContractBit: a control.bit.Bit subclass used only by
contract_kit/recorder.py and its scenarios. Loaded directly as
GameServer({"ContractBit": ContractBit}, ...) -- never through
BitRegistry.scan() -- so it carries no bit.toml and is never discoverable
as a venue Bit (spec section 5.4)."""
from __future__ import annotations

from control.bit import Bit
from control.cues import LightCue, PlayCue
from control.instrument import InstrumentRequirement
from control.roles import Role, RoleClass, RoleTable

CONTRACT_PLAYER_NODE = "CONTRACT_PLAYER_NODE"

REV1_CAPABILITIES = frozenset({"light.pixels", "gesture.tap", "gesture.hold",
                               "gesture.swing", "audio.samples"})

_LOOK_LEAD_S = 0.5          # how far into the future the "timed look" is stamped
_LOOK_CC = 74
_LOOK_VALUE_FIRST = 40
_LOOK_VALUE_LATER = 100
KNOWN_SAMPLE = "tick"
UNKNOWN_SAMPLE = "not_a_real_sample"


class ContractBit(Bit):
    version = "0.1"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._tap_count = 0

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.SHARED, capacity=None,
            scored=True, requires="rev1", breath=False,
            uses=["tap", "hold", "swing"], samples=[KNOWN_SAMPLE],
            # Only cc:74 is mapped, deliberately: Control's own per-tick
            # breath feed always targets cc:11, and a role with no cc:11
            # lane cannot have its rendered frame changed by it -- the
            # "then goes quiet" half of the timed-look behavior needs
            # nothing to hold still on its own.
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.5, "level": 0.6},
                 "lanes": [{"source": f"cc:{_LOOK_CC}", "dest": "hue"}]},
            ]},
        )
        return RoleTable(roles={"player": player},
                         node_map={CONTRACT_PLAYER_NODE: ["player"]})

    def instrument_requirements(self) -> tuple:
        return (InstrumentRequirement(slot="rev1",
                                      capabilities=REV1_CAPABILITIES),)

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "hold": self._on_hold,
                "swing": self._on_swing}

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        self._tap_count += 1
        value = _LOOK_VALUE_FIRST if self._tap_count == 1 else _LOOK_VALUE_LATER
        return [PlayCue(dev, KNOWN_SAMPLE, ""),
                LightCue(dev, 0xB0, _LOOK_CC, value, when=at + _LOOK_LEAD_S)]

    def _on_hold(self, dev: str, args: list, at: float) -> list:
        return [PlayCue(dev, UNKNOWN_SAMPLE, "")]

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        return []
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_contract_bit.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add contract_kit/ tests/test_contract_bit.py
git commit -m "feat(contract-kit): ContractBit, the device contract kit's test-only fixture"
```

## Task 6: The recorder rig

This is the largest task in Phase 3 — its steps are split into three
bite-sized pieces, each independently testable.

**Files:**
- Create: `contract_kit/recorder.py`
- Test: `tests/test_contract_recorder.py`

**Interfaces:**
- Consumes: `contract_kit.contract_bit.ContractBit`,
  `control.catalog.load_catalog`, `control.engine.GameServer`,
  `control.rooms.Room`, `control.terrarium_config.load_terrarium_config`,
  `devicelink.agent.DeviceLinkAgent`,
  `devicelink.o2_transport.{FakeO2Lite, O2LiteTransport, from_o2_arg}`.
- Produces: `Recorder` with methods `link_up(t_ms=0)`, `link_down(t_ms)`,
  `advance_to(target_ms)`, `join_now(t_ms, node)` (an explicit join sent
  later than `link_up`, for a scenario whose device hellos with
  `join_node=None` and only decides to join partway through),
  `expect_hello(t_ms)`,
  `expect_join(t_ms, node)`, `tap(onset_t, duration_ms)`,
  `hold(onset_t, held_s)`, `swing(onset_t, signed_g)`,
  `expect_quiet(t_ms, addresses, for_ms)`, `expect_frame(t_ms)`,
  `expect_play(t_ms, name, within_ms=50)`, `control_send_now(address,
  typespec, args, at=None)` (for scenario 11's hand-authored malformed
  `control_sends` steps), `unload_bit()` (calls `gs.abort()`), and
  `finish() -> dict` (the EXPORT FORMAT v1 scenario dict, `steps` sorted
  by `t`).

Design notes, verified before writing the code:

- `FakeO2Lite.send` (`devicelink/o2_transport.py`) is what
  `O2LiteTransport.send` ultimately calls; wrapping it (not
  `O2LiteTransport.send`) is required to see `Blob`-wrapped args exactly
  as the wire would carry them, matching the task's own requirement to
  decode blobs with `from_o2_arg`. Wrapping happens AFTER
  `O2LiteTransport.start(fake)` returns, so the two `_svcheck` probes
  `verify_service_ownership` sends during `start()` are never captured —
  confirmed by reading `O2LiteTransport.start`'s body. The wrapper still
  filters any stray `_svcheck` traffic defensively, because scenario 9
  (Task 7) calls `link_up` a second time mid-scenario.
- Ticking at the render rate (`1/44 s`, matching `harness/shroom_client.py`'s
  own `_TICK_INTERVAL` and `tests/test_lobby_agent.py`'s `_poll` helper),
  not jumping straight to a target time, is required so that two distinct
  light cues due a few hundred ms apart each get their own `agent.poll()`
  tick and are captured as two separate outgoing `/leds` messages rather
  than being collapsed into one. `TimedQueue.due(now)` returns every
  payload whose time has passed in ONE call, so a coarse jump would only
  ever render the LAST of several pending cues.
- A device declaring `"tuneshroom_rev1"` at hello is what the recorder's
  scripted device does, not `"testshroom"` — verified against
  `instruments/testshroom.toml`, whose capabilities
  (`light.pixels`/`audio.samples`/`gesture.tap`/`gesture.tilt`) do not
  include `gesture.hold`/`gesture.swing` and would be refused by
  `ContractBit`'s own `requires="rev1"` gate.
- `Room`-backed scenarios need no `build_session` patching (unlike
  `tests/test_lobby_agent.py`'s `_fake_sessions`): the fixtures' own
  luxaeterna sessions render onto `sim-main`/`sim-accent`, sentinel devs
  our recorder's own device is never bound to, so nothing about them
  reaches the wire this rig inspects. Real luxaeterna is already a test
  dependency (`pytest.importorskip("luxaeterna")`), so there is no cost
  to leaving it real, and `tools/record_scenarios.py` runs outside pytest
  with no `monkeypatch` fixture available regardless.

- [ ] **Step 1: Write the failing test for the rig's basic shape (hello + advance_to)**

```python
# tests/test_contract_recorder.py
"""contract_kit/recorder.py: the O2LiteTransport(FakeO2Lite) rig that
drives a ContractBit and captures Control's real behavior into EXPORT
FORMAT v1 scenario dicts (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md sections 4.2-4.3, 5.4)."""
import pytest

pytest.importorskip("luxaeterna")

from contract_kit.contract_bit import CONTRACT_PLAYER_NODE
from contract_kit.recorder import Recorder


def test_link_up_sends_hello_and_advance_to_repeats_it_every_5s():
    rec = Recorder(name="t", summary="s", join_node=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    data = rec.finish()
    hellos = [s for s in data["steps"] if s.get("expect_out", {}).get("address") == "/game/hello"]
    assert [s["t"] for s in hellos] == [0, 5000]
    for s in hellos:
        assert s["expect_out"]["args"] == ["$DEV", "*", "*", "*"]
        assert s["expect_out"]["typespec"] == "ssss"


def test_finish_shape_matches_export_format_v1():
    rec = Recorder(name="shape_check", summary="one line", join_node=None)
    rec.link_up(0)
    data = rec.finish()
    assert data["name"] == "shape_check"
    assert data["profiles"] == ["rev1"]
    assert data["device"] == {"join_node": None}
    assert set(data) == {"_provenance", "name", "summary", "profiles", "device", "steps"}
    assert data["_provenance"]["tool"] == "record_scenarios/1"
    ts = [s["t"] for s in data["steps"]]
    assert ts == sorted(ts)
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/test_contract_recorder.py -v`
Expected: `ModuleNotFoundError: No module named 'contract_kit.recorder'`.

- [ ] **Step 3: Write the rig's construction, `link_up`/`link_down`/`advance_to`, and `finish`**

```python
# contract_kit/recorder.py
"""The scenario recorder: drives a real GameServer + DeviceLinkAgent over
O2LiteTransport(FakeO2Lite), scripts the device's own sends directly (this
process IS the device, from the hub's point of view), and captures every
message Control sends back into an EXPORT FORMAT v1 scenario dict
(docs/superpowers/specs/2026-09-16-device-contract-kit-design.md sections
4.2-4.3). See contract_kit/scenarios.py for how the eleven scenarios use
this."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from control.catalog import load_catalog
from control.engine import GameServer
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport, from_o2_arg

from contract_kit.contract_bit import ContractBit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV = "ct1"
_TICK_MS = round(1000.0 / 44.0)     # the render/tick rate every other rig ticks at
_KEY_RE = re.compile(r"key=\d+")


def _provenance() -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {"commit": commit, "tool": "record_scenarios/1"}


def _normalize_key(value):
    if isinstance(value, str):
        return _KEY_RE.sub("key=$KEY", value)
    return value


class Recorder:
    def __init__(self, *, name: str, summary: str, profiles=("rev1",),
                 join_node: str | None = None, with_room: bool = False,
                 dev: str = DEV) -> None:
        self.name = name
        self.summary = summary
        self.profiles = list(profiles)
        self.join_node = join_node
        self.dev = dev
        self.steps: list[dict] = []
        self._sent: list[tuple[int, str, float, str, list]] = []
        self._now_ms = 0
        self._linked_up = False
        self._next_hello_ms: int | None = None

        self._fake = FakeO2Lite(now=0.0)
        self._fake.set_services("actl")
        self._transport = O2LiteTransport()
        self._transport.start(self._fake)
        self._orig_send = self._fake.send
        self._fake.send = self._wrapped_send

        catalog = load_catalog(REPO_ROOT / "instruments").published
        self._gs = GameServer({"ContractBit": ContractBit},
                              clock=self._fake.time_get,
                              carried_instruments=catalog)
        if with_room:
            profile = load_terrarium_config(
                str(REPO_ROOT / "terrarium.toml")).rooms["TEST"].profile
            self._gs.room = Room(name="TEST", profile=profile,
                                 node_id="ROOM_TEST_NODE")
            self._gs.room.bound["main"] = "sim-main"
            self._gs.room.bound["accent"] = "sim-accent"
        self._agent = DeviceLinkAgent(self._gs, self._transport,
                                      clock=self._fake.time_get)
        self._gs.load_bit("ContractBit")

    # --- capture -----------------------------------------------------------
    def _wrapped_send(self, addr: str, timestamp: float, *raw_args) -> None:
        self._orig_send(addr, timestamp, *raw_args)
        if not addr.startswith(f"/{self.dev}/"):
            return                          # _svcheck / a different dev
        typespec = raw_args[0] if raw_args else ""
        values = []
        for t, v in zip(typespec, raw_args[1:]):
            values.append(from_o2_arg(v) if t == "b" else v)
        self._sent.append((self._now_ms, addr, timestamp, typespec, values))

    # --- clock / link --------------------------------------------------------
    def advance_to(self, target_ms: int) -> None:
        while self._now_ms < target_ms:
            step_ms = min(_TICK_MS, target_ms - self._now_ms)
            if (self._linked_up and self._next_hello_ms is not None
                    and self._next_hello_ms < self._now_ms + step_ms):
                step_ms = max(0, self._next_hello_ms - self._now_ms)
            self._now_ms += step_ms
            self._fake.set_time(self._now_ms / 1000.0)
            self._agent.poll()
            if (self._linked_up and self._next_hello_ms is not None
                    and self._now_ms >= self._next_hello_ms):
                self._send_hello(self._now_ms)
                self._next_hello_ms += 5000

    def link_up(self, t_ms: int = 0) -> None:
        self.advance_to(t_ms)
        self.steps.append({"t": t_ms, "link": "up"})
        self._linked_up = True
        self._send_hello(t_ms)
        self._next_hello_ms = t_ms + 5000
        if self.join_node is not None:
            self._send_join(t_ms, self.join_node)

    def link_down(self, t_ms: int) -> None:
        self.advance_to(t_ms)
        self.steps.append({"t": t_ms, "link": "down"})
        self._linked_up = False
        self._next_hello_ms = None

    def _send_hello(self, t_ms: int) -> None:
        self._fake.deliver("/game/hello", "ssss",
                           (self.dev, "contract-kit", "1", "tuneshroom_rev1"),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    def _send_join(self, t_ms: int, node: str) -> None:
        self._fake.deliver("/game/join", "ss", (self.dev, node),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    # --- output --------------------------------------------------------------
    def finish(self) -> dict:
        control_steps = []
        for (t, addr, timestamp, typespec, values) in self._sent:
            norm_addr = addr.replace(f"/{self.dev}/", "/$DEV/")
            args = [_normalize_key(v) for v in values]
            at = None if not timestamp else round(timestamp * 1000)
            control_steps.append({"t": t, "control_sends": {
                "address": norm_addr, "typespec": typespec, "args": args,
                "at": at}})
        all_steps = self.steps + control_steps
        all_steps.sort(key=lambda s: s["t"])
        return {
            "_provenance": _provenance(),
            "name": self.name,
            "summary": self.summary,
            "profiles": self.profiles,
            "device": {"join_node": self.join_node},
            "steps": all_steps,
        }
```

- [ ] **Step 4: Run the Step 1 tests**

Run: `.venv/bin/python -m pytest tests/test_contract_recorder.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Write the failing tests for gestures, join/role, and the assertion helpers**

Append to `tests/test_contract_recorder.py`:

```python
def test_join_sends_at_link_up_and_role_is_recorded():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_join(0, CONTRACT_PLAYER_NODE)
    data = rec.finish()
    roles = [s for s in data["steps"]
            if s.get("control_sends", {}).get("address") == "/$DEV/role"]
    assert roles and roles[0]["control_sends"]["typespec"] == "b"
    assert roles[0]["control_sends"]["args"][0]["role"] == "player"


def test_tap_records_a_gesture_step_and_a_matching_expect_out():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(100, duration_ms=80.0)
    data = rec.finish()
    gestures = [s["gesture"] for s in data["steps"] if "gesture" in s]
    assert gestures == [{"kind": "tap", "onset_t": 100, "duration_ms": 80.0}]
    taps = [s["expect_out"] for s in data["steps"]
           if s.get("expect_out", {}).get("address") == "/game/tap"]
    assert taps and taps[0]["args"] == ["$DEV", 0.0, 80.0, 1]
    assert taps[0]["stamp_t"] == 100


def test_hold_and_swing_record_their_own_shapes():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.hold(200, held_s=0.65)
    rec.swing(400, signed_g=-2.1)
    data = rec.finish()
    outs = {s["expect_out"]["address"]: s["expect_out"] for s in data["steps"]
           if "expect_out" in s}
    assert outs["/game/hold"]["args"] == ["$DEV", 0.65, 1]
    assert outs["/game/hold"]["stamp_t"] == 200
    assert outs["/game/swing"]["args"] == ["$DEV", -2.1, 1]
    assert outs["/game/swing"]["stamp_t"] == 400


def test_expect_frame_records_the_last_frame_sent_by_t():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(100, duration_ms=80.0)
    rec.advance_to(700)              # past the tap's +0.5s future-stamped look
    rec.expect_frame(3000)
    data = rec.finish()
    frames = [s["expect_frame"] for s in data["steps"] if "expect_frame" in s]
    assert frames and len(frames[0]["grb"]) == 36


def test_expect_play_finds_the_actual_send_time_and_params():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(100, duration_ms=80.0)
    rec.expect_play(200, "tick")
    data = rec.finish()
    plays = [s for s in data["steps"] if "expect_play" in s]
    assert plays and plays[0]["expect_play"]["name"] == "tick"
    assert plays[0]["t"] <= 200


def test_expect_quiet_and_unload_bit_are_recorded():
    rec = Recorder(name="t", summary="s", join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_quiet(1, ["/game/hold", "/game/swing"], 1000)
    rec.unload_bit()
    data = rec.finish()
    assert {"t": 1, "expect_quiet": {"addresses": ["/game/hold", "/game/swing"],
                                     "for_ms": 1000}} in data["steps"]
    releases = [s for s in data["steps"]
               if s.get("control_sends", {}).get("address") == "/$DEV/release"]
    assert releases
```

- [ ] **Step 6: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_contract_recorder.py -v`
Expected: `AttributeError: 'Recorder' object has no attribute 'expect_join'`
(and similarly for the other missing methods).

- [ ] **Step 7: Add the gesture and assertion methods**

Append to `contract_kit/recorder.py` (inside the `Recorder` class, after
`_send_join`):

```python
    # --- gestures ------------------------------------------------------------
    def tap(self, onset_t: int, duration_ms: float) -> None:
        self.advance_to(onset_t)
        self.steps.append({"t": onset_t, "gesture": {
            "kind": "tap", "onset_t": onset_t, "duration_ms": duration_ms}})
        self._fake.deliver("/game/tap", "sffi",
                           (self.dev, 0.0, float(duration_ms), 1),
                           timestamp=onset_t / 1000.0)
        self._agent.poll()
        self.steps.append({"t": onset_t, "expect_out": {
            "address": "/game/tap", "typespec": "sffi",
            "args": ["$DEV", 0.0, duration_ms, 1],
            "stamp_t": onset_t, "within_ms": 50}})

    def hold(self, onset_t: int, held_s: float) -> None:
        self.advance_to(onset_t)
        self.steps.append({"t": onset_t, "gesture": {
            "kind": "hold", "onset_t": onset_t, "held_s": held_s}})
        self._fake.deliver("/game/hold", "sfi",
                           (self.dev, float(held_s), 1),
                           timestamp=onset_t / 1000.0)
        self._agent.poll()
        self.steps.append({"t": onset_t, "expect_out": {
            "address": "/game/hold", "typespec": "sfi",
            "args": ["$DEV", held_s, 1],
            "stamp_t": onset_t, "within_ms": 50}})

    def swing(self, onset_t: int, signed_g: float) -> None:
        self.advance_to(onset_t)
        self.steps.append({"t": onset_t, "gesture": {
            "kind": "swing", "onset_t": onset_t, "signed_g": signed_g}})
        self._fake.deliver("/game/swing", "sfi",
                           (self.dev, float(signed_g), 1),
                           timestamp=onset_t / 1000.0)
        self._agent.poll()
        self.steps.append({"t": onset_t, "expect_out": {
            "address": "/game/swing", "typespec": "sfi",
            "args": ["$DEV", signed_g, 1],
            "stamp_t": onset_t, "within_ms": 50}})

    # --- assertions ------------------------------------------------------------
    def expect_hello(self, t_ms: int) -> None:
        self.steps.append({"t": t_ms, "expect_out": {
            "address": "/game/hello", "typespec": "ssss",
            "args": ["$DEV", "*", "*", "*"], "stamp_t": None, "within_ms": 50}})

    def expect_join(self, t_ms: int, node: str) -> None:
        self.steps.append({"t": t_ms, "expect_out": {
            "address": "/game/join", "typespec": "ss",
            "args": ["$DEV", node], "stamp_t": None, "within_ms": 50}})

    def expect_quiet(self, t_ms: int, addresses: list[str], for_ms: int) -> None:
        self.steps.append({"t": t_ms, "expect_quiet": {
            "addresses": list(addresses), "for_ms": for_ms}})

    def expect_frame(self, t_ms: int) -> None:
        frames = [values[0] for (t, addr, _ts, _typespec, values) in self._sent
                 if addr == f"/{self.dev}/leds" and t <= t_ms]
        if not frames:
            raise AssertionError(f"no /leds frame sent to {self.dev} by t={t_ms}ms")
        self.steps.append({"t": t_ms, "expect_frame": {"grb": frames[-1]}})

    def expect_play(self, t_ms: int, name: str, within_ms: int = 50) -> None:
        matches = [(t, values) for (t, addr, _ts, _typespec, values) in self._sent
                  if addr == f"/{self.dev}/play" and t <= t_ms and values[0] == name]
        if not matches:
            raise AssertionError(f"no /play {name!r} sent to {self.dev} by t={t_ms}ms")
        t, values = matches[-1]
        self.steps.append({"t": t, "expect_play": {
            "name": values[0], "params": values[1], "within_ms": within_ms}})

    # --- lifecycle / hand-authored input --------------------------------------
    def unload_bit(self) -> None:
        self._gs.abort()
        self._agent.poll()

    def control_send_now(self, address: str, typespec: str, args: list,
                         at: int | None = None) -> None:
        """A hand-authored control_sends step (scenario 11's malformed
        inputs) -- appended directly rather than captured, since it is
        never actually sent through this rig's own O2 connection."""
        self.steps.append({"t": self._now_ms, "control_sends": {
            "address": address, "typespec": typespec, "args": args, "at": at}})
```

- [ ] **Step 8: Run all the recorder tests**

Run: `.venv/bin/python -m pytest tests/test_contract_recorder.py -v`
Expected: 8 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add contract_kit/recorder.py tests/test_contract_recorder.py
git commit -m "feat(contract-kit): the O2LiteTransport(FakeO2Lite) recording rig"
```

## Task 7: The eleven scenarios, the regression test, and `record_scenarios`

**Files:**
- Create: `contract_kit/scenarios.py`
- Create: `tools/record_scenarios.py`
- Create: `tests/test_contract_scenarios.py`
- Create (generated by Step 4 below, then committed): eleven files under
  `contract_kit/recordings/`

**Interfaces:**
- Consumes: `contract_kit.recorder.Recorder`, `contract_kit.contract_bit.CONTRACT_PLAYER_NODE`.
- Produces: `ALL_SCENARIOS: tuple[Callable[[], dict], ...]` (eleven
  zero-argument functions, one per scenario, each returning a
  `Recorder.finish()` dict); the committed JSON recordings; the
  `python -m tools.record_scenarios` command; the regression test.

A malformed-node join (`NO_SUCH_NODE`) is refused the same way any
unrecognized node is — `GameServer.join()`'s `self.registration.join(dev,
node, self.state)` returns `granted=False` for a node absent from the
role table's `node_map`, independent of anything this plan adds. Reality
is recorded as-is; where it differs from the guidance below (deny wording,
exact message order, whether `/room` accompanies every hello), the
committed JSON is the source of truth, not this plan's prose.

- [ ] **Step 1: Write scenarios 1-4 (link/hello, join, lobby, timed frames)**

```python
# contract_kit/scenarios.py
"""The eleven device contract kit scenarios (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md sections 4.3, 5.4). Each function
takes no arguments, builds its own Recorder, and returns an EXPORT FORMAT
v1 scenario dict. tools/record_scenarios.py writes these to
contract_kit/recordings/; tests/test_contract_scenarios.py re-runs them
and fails on any diff against the committed files."""
from __future__ import annotations

from contract_kit.contract_bit import CONTRACT_PLAYER_NODE
from contract_kit.recorder import Recorder

NO_SUCH_NODE = "NO_SUCH_NODE"


def boot_hello_heartbeat() -> dict:
    rec = Recorder(name="boot_hello_heartbeat",
                   summary="Hello repeats every 5s; nothing else goes "
                           "out before a join",
                   join_node=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.advance_to(10000)
    rec.expect_hello(10000)
    rec.expect_quiet(1, ["/game/join", "/game/tap", "/game/hold", "/game/swing"], 12000)
    rec.advance_to(13000)
    return rec.finish()


def explicit_join_role() -> dict:
    rec = Recorder(name="explicit_join_role",
                   summary="Joining a node and receiving the role",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_join(0, CONTRACT_PLAYER_NODE)
    rec.advance_to(500)
    return rec.finish()


def lobby_tap_join() -> dict:
    rec = Recorder(name="lobby_tap_join",
                   summary="Invite frames show before a role; two "
                           "count-1 taps join; the chime plays",
                   join_node=None, with_room=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(300)
    rec.expect_frame(300)                     # the invite's first white flash
    rec.tap(1000, duration_ms=80.0)
    rec.tap(1600, duration_ms=80.0)            # 600ms later, within the 1.5s double-tap window
    rec.advance_to(2000)
    rec.expect_frame(2000)                     # the accept flash / granted role's first frame
    rec.advance_to(4000)
    rec.expect_play(4000, "chime", within_ms=50)
    return rec.finish()


def timed_frames_hold_last() -> dict:
    rec = Recorder(name="timed_frames_hold_last",
                   summary="A future-stamped frame shows at its time; "
                           "two frames due together show only the "
                           "newest; the last frame holds",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    # Two taps at the SAME onset -> the same `at`, hence the same target
    # `when` in ContractBit._on_tap -- deliberately exercising "several
    # due at once, only the newest shows" rather than hoping two nearby
    # but distinct onsets happen to round the same way.
    rec.tap(100, duration_ms=80.0)
    rec.tap(100, duration_ms=80.0)
    rec.advance_to(700)                        # past the +0.5s future stamp
    rec.expect_frame(700)
    rec.advance_to(3700)
    rec.expect_frame(3700)                     # still holding, 3000ms later
    return rec.finish()


ALL_SCENARIOS = (
    boot_hello_heartbeat,
    explicit_join_role,
    lobby_tap_join,
    timed_frames_hold_last,
)
```

- [ ] **Step 2: Write `tools/record_scenarios.py` and record scenarios 1-4**

```python
# tools/record_scenarios.py
"""Re-record every device contract kit scenario and overwrite the
committed contract_kit/recordings/<name>.json files.

    .venv/bin/python -m tools.record_scenarios
"""
from __future__ import annotations

import json
from pathlib import Path

from contract_kit.scenarios import ALL_SCENARIOS

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "contract_kit" / "recordings"


def main(argv: list[str] | None = None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for scenario_fn in ALL_SCENARIOS:
        data = scenario_fn()
        out = OUT_DIR / f"{data['name']}.json"
        out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
```

Run: `.venv/bin/python -m tools.record_scenarios`
Expected: four `wrote contract_kit/recordings/....json` lines, no
traceback. Open each file and read it — this is the point at which
Control's actual behavior (message order, whether `/room` accompanies
`/role`, the exact deny/lobby wording) becomes visible; note anything
that differs from this task's prose above in a one-line code comment atop
the affected scenario function, then move on — the committed JSON is
authoritative either way.

- [ ] **Step 3: Write the regression test and run it against scenarios 1-4**

```python
# tests/test_contract_scenarios.py
"""Regression test: re-recording every device contract kit scenario must
match the committed JSON in contract_kit/recordings/. A diff means
Control's real behavior changed; run
`.venv/bin/python -m tools.record_scenarios`, review the diff, and commit
the new recording deliberately (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md section 5.4)."""
import json
from pathlib import Path

import pytest

pytest.importorskip("luxaeterna")

from contract_kit.scenarios import ALL_SCENARIOS

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / "contract_kit" / "recordings"


@pytest.mark.parametrize("scenario_fn", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_recording_matches_committed_json(scenario_fn):
    fresh = scenario_fn()
    committed_path = RECORDINGS / f"{fresh['name']}.json"
    assert committed_path.exists(), (
        f"no committed recording for {fresh['name']!r}; run "
        f"`.venv/bin/python -m tools.record_scenarios` and commit it")
    committed = json.loads(committed_path.read_text())
    # _provenance.commit legitimately differs between the commit HEAD is
    # on when this test runs and when the file was last recorded.
    fresh.pop("_provenance", None)
    committed.pop("_provenance", None)
    assert fresh == committed, (
        f"{fresh['name']} has drifted from its committed recording; "
        f"re-run `.venv/bin/python -m tools.record_scenarios` and review "
        f"the diff before committing")
```

Run: `.venv/bin/python -m pytest tests/test_contract_scenarios.py -v`
Expected: 4 tests PASS (against the files Step 2 just wrote).

- [ ] **Step 4: Commit scenarios 1-4**

```bash
git add contract_kit/scenarios.py contract_kit/recordings/ tools/record_scenarios.py tests/test_contract_scenarios.py
git commit -m "feat(contract-kit): scenarios 1-4 (boot/hello, join, lobby, timed frames) and the regression test"
```

- [ ] **Step 5: Write scenarios 5-8 (gestures after role, deny, release, play)**

Append to `contract_kit/scenarios.py`, before `ALL_SCENARIOS`:

```python
def gestures_after_role() -> dict:
    rec = Recorder(name="gestures_after_role",
                   summary="Tap works before a role; hold and swing "
                           "wait for one; all three stamp at onset "
                           "once granted",
                   join_node=None)
    rec.link_up(0)
    rec.tap(500, duration_ms=80.0)              # allowed before a role
    rec.expect_quiet(1, ["/game/hold", "/game/swing"], 900)
    rec.advance_to(1000)
    rec._send_join(1000, CONTRACT_PLAYER_NODE)  # explicit join now that the pre-role window is over
    rec.join_node = CONTRACT_PLAYER_NODE
    rec.tap(1500, duration_ms=80.0)
    rec.hold(2000, held_s=0.65)
    rec.swing(2500, signed_g=-2.1)
    return rec.finish()


def deny_stays_hellod() -> dict:
    rec = Recorder(name="deny_stays_hellod",
                   summary="A join to an unknown node is denied; the "
                           "device stays hello'd with its heartbeat "
                           "running",
                   join_node=NO_SUCH_NODE)
    rec.link_up(0)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.advance_to(10000)
    rec.expect_hello(10000)
    return rec.finish()


def release_keeps_display() -> dict:
    rec = Recorder(name="release_keeps_display",
                   summary="Unloading the Bit fades and releases a "
                           "joined device; the last fade frame holds; "
                           "hellos continue and hold/swing go quiet "
                           "after release",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(200, duration_ms=80.0)
    rec.advance_to(800)
    rec.unload_bit()
    rec.advance_to(2000)
    rec.expect_frame(2000)                      # the last fade frame
    rec.advance_to(5000)
    rec.expect_frame(5000)                      # holding, 3000ms later
    rec.expect_hello(5000)
    rec.expect_quiet(5000, ["/game/hold", "/game/swing"], 1000)
    return rec.finish()


def play_known_and_unknown() -> dict:
    rec = Recorder(name="play_known_and_unknown",
                   summary="A known sample plays; an unknown name is "
                           "ignored and later steps still pass",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.tap(200, duration_ms=80.0)               # plays the known "tick" sample
    rec.expect_play(200, "tick", within_ms=50)
    rec.hold(700, held_s=0.65)                   # plays the unknown sample; no expectation on it
    rec.advance_to(5000)
    rec.expect_hello(5000)                       # a later step still passes
    return rec.finish()
```

- [ ] **Step 6: Record, run the regression test, and commit scenarios 5-8**

```bash
.venv/bin/python -m tools.record_scenarios
.venv/bin/python -m pytest tests/test_contract_scenarios.py -v
```

Expected: 8 tests PASS. Then edit `ALL_SCENARIOS` to include the four new
functions:

```python
ALL_SCENARIOS = (
    boot_hello_heartbeat,
    explicit_join_role,
    lobby_tap_join,
    timed_frames_hold_last,
    gestures_after_role,
    deny_stays_hellod,
    release_keeps_display,
    play_known_and_unknown,
)
```

Re-run both commands above (record, then test) so the tuple change is
reflected. Expected: 8 tests PASS.

```bash
git add contract_kit/scenarios.py contract_kit/recordings/
git commit -m "feat(contract-kit): scenarios 5-8 (gestures after role, deny, release, play)"
```

- [ ] **Step 7: Write scenario 9 (link loss and rejoin)**

```python
def link_loss_rejoin() -> dict:
    rec = Recorder(name="link_loss_rejoin",
                   summary="After 15s of silence Control reaps the "
                           "device; it hellos and joins again once "
                           "the link is back",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.link_down(20000)
    rec.advance_to(36000)                       # 16s of silence: past the 15s stale timeout
    rec.link_up(36000)
    rec.expect_hello(36000)
    rec.expect_join(36000, CONTRACT_PLAYER_NODE)
    rec.advance_to(37000)
    return rec.finish()
```

Add `link_loss_rejoin` to `ALL_SCENARIOS`. Record, test, and commit:

```bash
.venv/bin/python -m tools.record_scenarios
.venv/bin/python -m pytest tests/test_contract_scenarios.py -v
git add contract_kit/scenarios.py contract_kit/recordings/
git commit -m "feat(contract-kit): scenario 9, link loss and rejoin"
```

- [ ] **Step 8: Write scenario 10 (error, no state change)**

```python
def error_no_state_change() -> dict:
    rec = Recorder(name="error_no_state_change",
                   summary="A tap with no lobby and no role reaches "
                           "an /error that changes nothing; hellos "
                           "continue",
                   join_node=None)
    rec.link_up(0)
    rec.tap(200, duration_ms=80.0)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    return rec.finish()
```

Add it to `ALL_SCENARIOS`. Record, test, and commit:

```bash
.venv/bin/python -m tools.record_scenarios
.venv/bin/python -m pytest tests/test_contract_scenarios.py -v
git add contract_kit/scenarios.py contract_kit/recordings/
git commit -m "feat(contract-kit): scenario 10, error changes no state"
```

- [ ] **Step 9: Write scenario 11 (malformed input dropped) — hand-authored steps interleaved with recorded ones**

```python
def malformed_dropped() -> dict:
    rec = Recorder(name="malformed_dropped",
                   summary="An unknown verb, a role with a bad "
                           "typespec, and non-list leds args are all "
                           "dropped; a later valid frame still shows",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.advance_to(200)
    # Hand-authored, not recorded: none of these three ever crosses this
    # rig's own O2 connection (there is no real hub here to route a
    # malformed message from), so they are declared directly. A replay
    # runner's own transport is what actually has to drop them.
    rec.control_send_now("/$DEV/bogus", "s", ["hello"], at=None)
    rec.control_send_now("/$DEV/role", "s", ["not json"], at=None)
    rec.control_send_now("/$DEV/leds", "b", ["not a list"], at=None)
    rec.tap(700, duration_ms=80.0)               # a later valid gesture still round-trips
    rec.advance_to(1200)
    rec.expect_frame(1200)                       # a later valid frame still shows
    rec.advance_to(5200)
    rec.expect_hello(5200)
    return rec.finish()
```

Add it to `ALL_SCENARIOS`:

```python
ALL_SCENARIOS = (
    boot_hello_heartbeat,
    explicit_join_role,
    lobby_tap_join,
    timed_frames_hold_last,
    gestures_after_role,
    deny_stays_hellod,
    release_keeps_display,
    play_known_and_unknown,
    link_loss_rejoin,
    error_no_state_change,
    malformed_dropped,
)
```

- [ ] **Step 10: Record, run the full regression test, and confirm the hand-authored steps are stable**

```bash
.venv/bin/python -m tools.record_scenarios
.venv/bin/python -m pytest tests/test_contract_scenarios.py -v
```

Expected: 11 tests PASS. Re-run `record_scenarios` a second time with no
further edits and `git diff --stat contract_kit/recordings/` — expected:
no output (empty diff), confirming `malformed_dropped`'s hand-authored
`control_sends` steps regenerate byte-identically every time, per the
task's own requirement.

- [ ] **Step 11: Commit**

```bash
git add contract_kit/scenarios.py contract_kit/recordings/
git commit -m "feat(contract-kit): scenario 11, malformed input dropped; all eleven scenarios land"
```

## Task 8: `tools/export_contract.py`

**Files:**
- Create: `tools/export_contract.py`
- Test: `tests/test_export_contract.py`

**Interfaces:**
- Consumes: `contract_kit.recordings/*.json`, `devicelink.contract.VERB_TABLE`,
  `control.catalog.load_catalog`, `control.role_config.carried_instrument_view`,
  `control.boot_config.BootConfig`, `control.lobby.LobbyConfig`,
  `devicelink.o2_transport.MAX_DEV_LEN`, `devicelink.protocol.O2_MAX_MSG_LEN`,
  `control.lobby.TERRARIUM_ADMIN`.
- Produces: `export_contract(*, catalog_path, commit) -> dict` (the
  `contract.json` payload, mirroring `tools/export_solo.py`'s
  `export_solo` shape) and a CLI, `python -m tools.export_contract <out-dir>`,
  writing `<out-dir>/contract.json` and `<out-dir>/scenarios/<name>.json`.

Every value under `lifecycle`/`limits` is read from the constant that
defines it, verified before writing this task:

| Export key | Source | Verified value |
|---|---|---|
| `stale_timeout_s` | `control.boot_config.BootConfig().stale_timeout` | `15.0` |
| `lobby_double_tap_window_s` | `control.lobby.LobbyConfig().double_tap_window_s` | `1.5` |
| `dev_id_max_len` | `devicelink.o2_transport.MAX_DEV_LEN` | `31` |
| `reserved_dev_ids` | `control.lobby.TERRARIUM_ADMIN` | `["terrarium"]` (the only reserved id in the code) |
| `max_message_bytes` (both `link` and `limits`) | `devicelink.protocol.O2_MAX_MSG_LEN` | `4096` |
| `hello_interval_s` | new `harness.o2_shroom.HELLO_INTERVAL_S` (Step 1 below) | `5.0` |

`hello_interval_s` has no existing named constant — `harness/o2_shroom.py`'s
`--heartbeat-interval` argparse flag has `default=5.0` as a bare literal
inline in `main()`, not importable on its own. Step 1 below promotes it to
a module-level constant (a one-line, behavior-preserving refactor) so the
export test can assert against it directly, matching every other row in
the table above.

- [ ] **Step 1: Promote the hello interval to a named constant in `harness/o2_shroom.py`**

Old text:

```python
SWEEP_RESUME_SECONDS = 5.0
```

New text:

```python
SWEEP_RESUME_SECONDS = 5.0

# The device's own re-hello cadence (GameServer.reap_stale expects a
# heartbeat at least this often; see control/boot_config.py's
# stale_timeout). Named here so tools/export_contract.py's "lifecycle"
# section and its test can read the one place this number is defined,
# instead of a bare literal buried in main()'s argparse default.
HELLO_INTERVAL_S = 5.0
```

Old text:

```python
    parser.add_argument("--heartbeat-interval", type=float, default=5.0,
```

New text:

```python
    parser.add_argument("--heartbeat-interval", type=float, default=HELLO_INTERVAL_S,
```

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -v` (if this file
imports `harness.o2_shroom`; confirm the exact test file name with `grep
-rl "harness.o2_shroom\|harness import o2_shroom" tests/` first) — expected:
unchanged, all PASS. This is a pure rename with the same default value, so
no behavior changes.

```bash
git add harness/o2_shroom.py
git commit -m "refactor(harness): name the hello interval constant so it's importable"
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_export_contract.py
"""tools/export_contract.py: packages devicelink/contract.py's verb table,
the tuneshroom_rev1 instrument, and the committed contract_kit/recordings/
into the EXPORT FORMAT v1 folder every device repo commits at
test/contract/ (docs/superpowers/specs/2026-09-16-device-contract-kit-design.md
sections 4.2-4.3)."""
import json
from pathlib import Path

import pytest

pytest.importorskip("luxaeterna")

from control.boot_config import BootConfig
from control.lobby import LobbyConfig, TERRARIUM_ADMIN
from control.role_config import carried_instrument_view
from devicelink.contract import VERB_TABLE
from devicelink.o2_transport import MAX_DEV_LEN
from devicelink.protocol import O2_MAX_MSG_LEN
from harness.o2_shroom import HELLO_INTERVAL_S
from tools.export_contract import export_contract, main

ROOT = Path(__file__).resolve().parents[1]


def test_verbs_list_is_sorted_by_address_and_matches_the_table():
    data = export_contract(commit="abc123")
    addresses = [v["address"] for v in data["verbs"]]
    assert addresses == sorted(addresses)
    assert len(data["verbs"]) == len(VERB_TABLE)
    hold = next(v for v in data["verbs"] if v["address"] == "/game/hold")
    assert hold["typespecs"] == ["sfi"]
    assert hold["args"] == ["dev", "held_seconds", "count"]
    assert hold["direction"] == "up"
    assert hold["transport"] == "udp-ok"
    assert hold["pre_role"] is False


def test_link_and_limits_and_lifecycle_come_from_the_code():
    data = export_contract(commit="abc123")
    assert data["link"] == {"arg_types": ["b", "f", "i", "s"],
                            "max_message_bytes": O2_MAX_MSG_LEN,
                            "service_is_dev_id": True}
    assert data["limits"] == {"max_message_bytes": O2_MAX_MSG_LEN,
                              "dev_id_max_len": MAX_DEV_LEN,
                              "reserved_dev_ids": [TERRARIUM_ADMIN]}
    assert data["lifecycle"]["hello_interval_s"] == HELLO_INTERVAL_S
    assert data["lifecycle"]["stale_timeout_s"] == BootConfig().stale_timeout
    assert data["lifecycle"]["lobby_double_tap_window_s"] == LobbyConfig().double_tap_window_s
    assert data["lifecycle"]["bench_tolerance_ms"] == {"frame": 50, "heartbeat": 1000}


def test_instruments_section_uses_the_carried_instrument_view():
    from control.catalog import load_catalog
    data = export_contract(commit="abc123")
    rev1 = load_catalog(ROOT / "instruments").published["tuneshroom_rev1"]
    section = data["instruments"]["tuneshroom_rev1"]
    assert section["instrument"] == carried_instrument_view(rev1)
    assert section["triggers"] == {t.name: dict(t.thresholds)
                                   for t in rev1.event_triggers}


def test_contract_version_and_provenance():
    data = export_contract(commit="abc123")
    assert data["contract_version"] == 1
    assert data["_provenance"] == {"commit": "abc123", "tool": "export_contract/1"}


def test_main_writes_contract_and_scenario_files(tmp_path):
    main([str(tmp_path)])
    contract = json.loads((tmp_path / "contract.json").read_text())
    assert contract["contract_version"] == 1
    scenario_files = sorted(p.name for p in (tmp_path / "scenarios").glob("*.json"))
    assert scenario_files == sorted(f"{f.__name__}.json" for f in
                                    __import__("contract_kit.scenarios",
                                              fromlist=["ALL_SCENARIOS"]).ALL_SCENARIOS)
    one = json.loads((tmp_path / "scenarios" / "boot_hello_heartbeat.json").read_text())
    assert one["_provenance"]["tool"] == "export_contract/1"
    assert one["name"] == "boot_hello_heartbeat"
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_export_contract.py -v`
Expected: `ModuleNotFoundError: No module named 'tools.export_contract'`.

- [ ] **Step 4: Write `tools/export_contract.py`**

```python
# tools/export_contract.py
"""Export the device contract kit -- the verb table, the Rev 1
instrument, and every recorded scenario -- as the EXPORT FORMAT v1 folder
each device repo commits at test/contract/ (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md sections 4.2-4.3). A sibling of
tools/export_solo.py.

    .venv/bin/python -m tools.export_contract /path/to/mm-tuneshroom/test/contract
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from control.boot_config import BootConfig
from control.catalog import load_catalog
from control.lobby import LobbyConfig, TERRARIUM_ADMIN
from control.role_config import carried_instrument_view
from devicelink.contract import VERB_TABLE
from devicelink.o2_transport import MAX_DEV_LEN
from devicelink.protocol import O2_MAX_MSG_LEN
from harness.o2_shroom import HELLO_INTERVAL_S

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION = "export_contract/1"
CONTRACT_VERSION = 1

BENCH_TOLERANCE_MS = {"frame": 50, "heartbeat": 1000}


def _head_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _verb_entry(row) -> dict:
    return {"address": row.address, "direction": row.direction,
            "typespecs": list(row.typespecs), "args": list(row.args),
            "transport": row.transport, "pre_role": row.pre_role,
            "notes": row.notes}


def export_contract(*, commit: str) -> dict:
    rev1 = load_catalog(REPO_ROOT / "instruments").published["tuneshroom_rev1"]
    verbs = sorted((_verb_entry(row) for row in VERB_TABLE),
                  key=lambda v: v["address"])
    return {
        "_provenance": {"commit": commit, "tool": TOOL_VERSION},
        "contract_version": CONTRACT_VERSION,
        "verbs": verbs,
        "link": {"arg_types": ["b", "f", "i", "s"],
                "max_message_bytes": O2_MAX_MSG_LEN,
                "service_is_dev_id": True},
        "limits": {"max_message_bytes": O2_MAX_MSG_LEN,
                  "dev_id_max_len": MAX_DEV_LEN,
                  "reserved_dev_ids": [TERRARIUM_ADMIN]},
        "lifecycle": {"hello_interval_s": HELLO_INTERVAL_S,
                     "stale_timeout_s": BootConfig().stale_timeout,
                     "lobby_double_tap_window_s": LobbyConfig().double_tap_window_s,
                     "bench_tolerance_ms": dict(BENCH_TOLERANCE_MS)},
        "instruments": {
            "tuneshroom_rev1": {
                "instrument": carried_instrument_view(rev1),
                "triggers": {t.name: dict(t.thresholds)
                            for t in rev1.event_triggers},
            },
        },
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: export_contract.py <out-dir>")
    out_dir = Path(argv[0])
    commit = _head_commit()

    _write_json(out_dir / "contract.json", export_contract(commit=commit))

    from contract_kit.scenarios import ALL_SCENARIOS
    for scenario_fn in ALL_SCENARIOS:
        data = scenario_fn()
        data["_provenance"] = {"commit": commit, "tool": TOOL_VERSION}
        _write_json(out_dir / "scenarios" / f"{data['name']}.json", data)
    print(f"wrote {out_dir}/contract.json and "
         f"{len(ALL_SCENARIOS)} scenario files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_export_contract.py -v`
Expected: 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/export_contract.py tests/test_export_contract.py
git commit -m "feat(tools): export_contract.py, packaging the device contract kit for a device repo"
```

## Phase 3 checkpoint

- [ ] **Step 1: Full suite green**

Run: `.venv/bin/python -m pytest tests -v 2>&1 | tail -20`
Expected: all PASS, including every `test_contract_*` file added in
Phase 3.

- [ ] **Step 2: A real export, sanity-checked by hand**

```bash
.venv/bin/python -m tools.export_contract /tmp/contract-export
ls /tmp/contract-export /tmp/contract-export/scenarios
python3 -m json.tool /tmp/contract-export/contract.json | head -30
```

Expected: `contract.json` plus eleven files under `scenarios/`; the head
of `contract.json` shows `_provenance`, `contract_version`, and the start
of a `verbs` list sorted by address.

- [ ] **Step 3: Open the Phase 3 PR**

```bash
git push -u origin claude/device-contract-kit-terrarium
gh pr create --base claude/device-contract-kit-spec --title "Device contract kit, Phase 3: contract Bit, recorder, eleven scenarios, export tool" --body "$(cat <<'EOF'
## Summary
- contract_kit/: ContractBit (test-only, never under bits/), the O2LiteTransport(FakeO2Lite) recording rig, and the eleven committed scenario recordings
- tools/record_scenarios.py: re-records the committed files; tests/test_contract_scenarios.py fails on any diff
- tools/export_contract.py <out-dir>: packages contract.json + scenarios/ for a device repo's test/contract/

## Test plan
- [ ] .venv/bin/python -m pytest tests -v is green
- [ ] .venv/bin/python -m tools.export_contract /tmp/contract-export produces a well-formed folder
EOF
)"
```

---

# Reference: EXPORT FORMAT v1

Copied verbatim from the approved spec so this plan's Task 8 and the
mm-tuneshroom/mm-devshroom plans that consume it agree on exactly one
text.

Layout written by `tools/export_contract.py <out-dir>`:
- `<out-dir>/contract.json`
- `<out-dir>/scenarios/<name>.json`, one per scenario, where `<name>` is the scenario's `name`

All files are UTF-8 JSON written with `indent=2`, `sort_keys=True` and a
trailing newline, the same as `tools/export_solo.py`. Device repos commit
the folder at `test/contract/`.

`contract.json` keys:
- `_provenance`: `{"commit": <12-char short HEAD, or "unknown">, "tool": "export_contract/1"}`
- `contract_version`: int, starting at 1
- `verbs`: a list sorted by `address`. Each entry is `{"address": str, "direction": "up"|"down", "typespecs": [str, ...], "args": [str, ...], "transport": "tcp"|"udp-ok", "pre_role": bool, "notes": str}`.
  - `address` is `/game/<verb>` for up rows and `/<dev>/<verb>` (a literal `<dev>`) for down rows.
  - `args` names the arguments of the longest typespec.
  - `pre_role` on an up row means a device may send it before holding a role; on a down row, that Control may send it before a role.
- `link`: `{"arg_types": ["b", "f", "i", "s"], "max_message_bytes": 4096, "service_is_dev_id": true}`
- `limits`: `{"max_message_bytes": 4096, "dev_id_max_len": <int from the code>, "reserved_dev_ids": [<from the code>]}`
- `lifecycle`: `{"hello_interval_s": 5.0, "stale_timeout_s": 15.0, "lobby_double_tap_window_s": 1.5, "bench_tolerance_ms": {"frame": 50, "heartbeat": 1000}}`
- `instruments`: `{"tuneshroom_rev1": {"instrument": <carried_instrument_view>, "triggers": {<trigger name>: {<key>: number}}}}`

Scenario file keys:
- `_provenance`: same shape as in `contract.json`
- `name`: snake_case str, equal to the file stem
- `summary`: a one-line str
- `profiles`: a list containing `"rev1"` and/or `"any"`
- `device`: `{"join_node": str | null}`. When non-null, the device sends `/game/join ["$DEV", join_node]` right after its first hello on each link-up.
- `steps`: a list sorted by `t`, ascending and stable. Each step has an int `t` (ms since scenario start; the device's O2 time is `t/1000` s) and exactly one of these keys:
  - `"link"`: `"up"` or `"down"`.
  - `"control_sends"`: `{"address": "/$DEV/<verb>", "typespec": str, "args": list, "at": int | null}`.
    - Args are already decoded: for `role` and `room`, `args[0]` is the JSON object; for `leds`, `args[0]` is a list of ints 0-255, GRB, 3 per pixel.
    - `at` is the presentation time in ms on the same timeline; null means act on arrival.
  - `"gesture"`: a classified gesture delivered to the device session at `t`, one of:
    - `{"kind": "tap", "onset_t": int, "duration_ms": number}`
    - `{"kind": "hold", "onset_t": int, "held_s": number}`
    - `{"kind": "swing", "onset_t": int, "signed_g": number}`
  - `"expect_out"`: `{"address": "/game/<verb>", "typespec": str, "args": list, "stamp_t": int | null, "within_ms": int}`.
    - Each arg is a literal (numbers match within 1e-3), `"$DEV"`, or `"*"` (anything).
    - A sent message satisfies the step when:
      - its send time is in `[t, t + within_ms]`
      - its address and typespec are equal
      - its args match
      - if `stamp_t` is non-null, its timestamp equals `stamp_t/1000` s within 1 ms
    - A sent message satisfies at most one `expect_out`.
  - `"expect_frame"`: `{"grb": [int, ...]}`. The frame shown at `t` equals `grb`, or does so one render tick (23 ms) later.
  - `"expect_play"`: `{"name": str, "params": str, "within_ms": int}`. A play effect with that name and params is emitted in `[t, t + within_ms]`.
  - `"expect_quiet"`: `{"addresses": [str, ...], "for_ms": int}`. No sent message with one of these addresses has a send time in `[t, t + for_ms)`.

Replay runners substitute a fixed dev id for `$DEV` and ignore unmatched
sent messages unless an `expect_quiet` step covers them.

---

## Self-review

**1. Spec coverage.**
- §5.1 (verb table) -> Task 1.
- §5.2 (capabilities, catalog) -> Tasks 2 and 3.
- §5.3 (Mushica gates on Rev 1) -> Task 4i (plan text only, since
  `bits/mushica/` does not exist yet, exactly as scoped).
- §5.4 (contract Bit, recorder, export, regression test) -> Tasks 5, 6, 7 (regression
  test), 8.
- §5.5 (the `/release`-before-fade-frames ordering to confirm while
  recording) -> Task 7's `release_keeps_display` scenario records
  whichever order Control actually produces; Task 7's own framing note
  says explicitly to record reality over the guidance's prose.
- §9 (ESP32 spec/plan amendments) -> Task 4a-4j.
- The mm-terrarium bullets of §11 (verb table matches `GAME_VERBS` and
  every builder; `tuneshroom_rev1` passes catalog validation; re-recording
  produces no diff; an export test; Mushica refuses/admits) -> Tasks 1, 3,
  7, 8, 4i respectively.
- §4.2/§4.3 (export format, scenarios) -> the Reference section, Task 7,
  Task 8.

**2. Placeholder scan.** No "TBD"/"similar to Task N"/bare "add error
handling" appears in this plan. The one intentionally-incomplete snippet
is Task A8's `test_replay_boot_hello_heartbeat` (a firmware task in
*another repo*, gated on this plan's own Phase 3 landing first, so its
driver cannot be written completely here) — it carries a failing
`TEST_FAIL_MESSAGE` and an explicit instruction to fill in the driver,
so an unimplemented replay test is red rather than a green test that
asserts nothing,
which is a property of a task this plan does not execute, not a gap in a
task it does.

**3. Type consistency.** `Recorder.finish()`'s returned dict shape matches
the Reference section exactly (checked field-by-field in Task 6 Step 1's
`test_finish_shape_matches_export_format_v1` and Task 8's tests).
`ContractBit`'s `role_table` is consistently a property everywhere it's
used (Task 5's tests, `contract_kit/recorder.py`'s `GameServer.load_bit`
call, which reads it via bare attribute access). `devicelink.contract.VerbRow`'s
field names (`verb`, `direction`, `typespecs`, `args`, `transport`,
`pre_role`, `notes`) are used identically in Task 1's tests and Task 8's
`_verb_entry`. The verb-table decision to keep down-verb transport as
`"udp-ok"` (Task 1) is carried through unchanged into Task 8's export test.

**4. Spec requirements not mapped to a task in this plan.** §6 (mm-tuneshroom
hardware profile, frame hold, `DeviceSession`, scenario replay) and §7
(mm-devshroom's Arduino-free session library and native replay
environment) are explicitly out of scope here — they belong to the
mm-tuneshroom plan and to Task A8 (a doc amendment recording what
mm-devshroom should build, not code this plan executes). §8 (the bench
replay feasibility spike) is its own short plan per the spec's own
schedule table. Within scope, every requirement is mapped; none were
found with no task.
