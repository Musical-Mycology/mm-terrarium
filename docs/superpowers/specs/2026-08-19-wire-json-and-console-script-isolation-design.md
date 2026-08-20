# Valid JSON on every wire, and isolated Console scripts

**Status:** Approved, not yet implemented.
**Found:** 2026-08-19, during the first live attempt at the Bit-declared-triggers
live-verify checklist (`2026-08-17-bit-declared-triggers-and-cue-scripts-design.md`
section 13.1). Both defects below blocked it.

Two independent defects, each of which leaves the Terrarium Console showing
nothing at a venue. They are unrelated in mechanism and are fixed
independently; they share a spec because they were found in one run and
because both survived 1057 passing tests for reasons worth writing down
together.

Neither fix changes any observable behavior when values are already
well-formed.

---

## 1. Defect A: a non-finite float makes every outbound payload invalid JSON

`TestBit.status()` returns `run_duration`, which `harness/terrarium_boot.py`'s
`_run_duration()` sets to `float("inf")` whenever `--hold` is passed.
`ConsoleAgent` puts that dict straight into the snapshot, and
`console/server.py` serialises it with a bare `json.dumps`.

Python emits the bare token `Infinity`, which is a **Python extension, not
JSON**. The browser's `JSON.parse` rejects it:

```
SyntaxError: Unexpected token 'I', ..."uration": Infinity, "... is not valid JSON
```

The whole snapshot fails to parse, so **every panel stays empty**: state,
loaded Bit, Room, Triggers, Registration, Devices, Bit status, Event log.
Measured live on 2026-08-19: a connected Console rendering nothing at all
while the stack was healthy and holding in SETUP.

**This happens on every `run_stack` run**, because
`harness/run_stack.py:103` hardcodes `--hold` in the `terrarium_boot` command
it builds. There is no flag that avoids it. The only way a working Console
was obtained during the investigation was to bypass `run_stack` and drive
`harness/terrarium_boot.py` directly with a finite `--seconds`.

### 1.1 The blast radius is not only the Console

Eight `json.dumps` call sites carry no guard:

| File | Sites | Consumer |
|------|-------|----------|
| `console/server.py` | 126, 135 | the operator's browser |
| `devicelink/server.py` | 97, 106 | **device wire** (phones, simulators) |
| `devicelink/o2_transport.py` | 59 | **device wire**, as an o2lite blob |
| `uplink/transport.py` | 85 | the future mm-fairyring broker |
| `capture/store.py` | 194, 211 | on-disk trace files |

The device-wire sites matter most. A phone parsing `/ie<N>/role` with
`JSON.parse`, or a Dart client with `jsonDecode`, fails exactly as the
Console does. Nothing produces a non-finite value on those paths today, which
is the only reason it has not been seen there.

`capture/store.py` writes files rather than a wire, and is included anyway: a
trace no strict parser can read is the same defect one step later.

### 1.2 Why 1057 tests missed it

**`json.loads` accepts `Infinity`.** Python's decoder takes the same
extension its encoder emits, so every Python-side round trip agrees with
itself and disagrees with every browser. `tests/test_console_agent.py:101`
asserts `snap["bit_status"]["run_duration"] == TestBit().status()["run_duration"]`,
which passes, and which would still pass with the bug present, because it
compares decoded Python objects and because `TestBit()`'s default duration is
a finite 2.0 anyway.

This is the same shape as the rule this repo already wrote down after the
o2lite pump bug: **a test double must never be more permissive than the thing
it stands for.** Here the permissive double is `json.loads` standing in for
`JSON.parse`. Section 4 states the testing consequence.

---

## 2. Defect B: two Console scripts define the same global

`console/static/room.js:164` declares `function buildCard(inst, controllers)`.
`console/static/triggers.js:63` declares `function buildCard(trigger)`.

`console/static/index.html` loads them as plain scripts, in this order:

```html
<script src="room.js"></script>
<script src="triggers.js"></script>
<script src="console.js"></script>
```

Both declarations land in the same global scope, so **triggers.js silently
overwrites room.js's**. Then `room.js:128` calls it with a room instrument,
which has no `condition`:

```
TypeError: Cannot read properties of undefined (reading 'description')
    at buildCard (triggers.js:82)
    at renderRoom (room.js:128)
    at handle (console.js:89)
    at ws.onmessage (console.js:12)
```

Measured live: **222 throws in 2.5 seconds**, one per `room_changed`. The
throw aborts `handle()`, so everything dispatched after `renderRoom` in that
message never runs. Observed consequences: the Room's instrument cards, the
entire Triggers panel and the Event log are all permanently empty. With no
Triggers panel there is no Fire button, which is what blocked the checklist.

### 2.1 Why the JS tests missed it

The repo already has behavioral JS tests, added precisely because "a grep
over source text is not a test of behavior". They do not catch this, and the
reason is structural rather than an oversight:

- `tests/js/room_panel_behavior.test.js` loads **room.js and console.js**.
- `tests/js/trigger_panel_behavior.test.js` loads **triggers.js**.

**No test loads room.js and triggers.js together**, and that is the only pair
that collides. Each file is correct in isolation. The defect exists only in
the combination the browser actually loads, so isolation testing cannot see
it by construction.

### 2.2 The global surface, and which names are actually shared

`console.js` genuinely depends on five names being global, because it is the
dispatcher:

`renderRoom`, `renderRoomFrame`, `renderTriggers`, `renderTriggerDevices`,
`renderTriggerFired`.

Everything else that is currently global is an internal helper that leaked:
`buildCard` (both), `addRow`, `buildFixtureStrip`, `buildFixtureZoneLabels`,
`fixtureShapeMatches`, `stepText`, `firedText`, `applyFired`,
`fillDevicePicker`, plus module state (`roomFixtureShapes`, `lastFired`,
`triggerDevices`, `triggerSignature`, `currentDeviceTargets`,
`fixtureDevByName`, `fixtureNameByDev`).

Only `buildCard` collides today. `addRow` and the rest are the same hazard
waiting for the next file.

---

## 3. The fixes

### 3.1 Part A: one serialisation boundary, `control/wire_json.py`

A new pure-stdlib module exposing one function:

```python
def dumps(obj, **kwargs) -> str:
    """json.dumps for anything leaving this process."""
```

It walks the payload, replaces every non-finite float (`inf`, `-inf`, `nan`)
with `None`, then calls `json.dumps(sanitised, allow_nan=False, **kwargs)`.

- **`allow_nan=False` is the belt to the sanitiser's braces.** If the walk
  ever misses a path, `json.dumps` raises `ValueError` instead of emitting an
  invalid token. A loud failure in a test beats a silent one at a venue.
- **`**kwargs` passes through**, because `capture/store.py:194` already uses
  `separators=(",", ":")` and must keep it.
- **A warning is logged once per offending key**, via the repo's existing
  `logger = logging.getLogger(__name__)` convention, so a Bit emitting
  garbage stays visible without spamming a 44 Hz loop. Deduplicated on the
  key path, not the value.

**Why `null`, specifically.** This wire format already uses `null` to mean
unbounded: `Role.capacity` is `None` for an uncapped role and the Console
renders it as an infinity sign. So `run_duration: null` renders correctly
with **no Console-side change at all**, and it means to a reader exactly what
`Infinity` was trying to mean. A large sentinel number would lie, and a
string would change the field's type.

**Why `control/`.** It is already the shared, dependency-free layer that
`console/`, `devicelink/` and `uplink/` import from, and this module is pure
stdlib, so it does not weaken the rule that `control/` imports no luxaeterna
and no pyarco.

`capture/` is the exception worth naming: it imports nothing from `control/`
today, so taking this helper gives it its first such import. That is
acceptable because the direction is the same downward one every other package
already follows, and because the alternative is either a second copy of the
sanitiser or a trace file that no strict parser can read. If a reviewer would
rather keep `capture/` dependency-free, dropping it from scope costs only the
two on-disk sites and changes nothing else in this spec.

All eight call sites in the table above switch to it.

### 3.2 Part B: IIFE-wrapped scripts with explicit exports

`room.js` and `triggers.js` are each wrapped in an IIFE that assigns only
their entry points onto `window`:

```javascript
(function () {
  "use strict";
  // ... everything currently at top level, unchanged ...
  window.renderRoom = renderRoom;
  window.renderRoomFrame = renderRoomFrame;
})();
```

Helpers and module state become private and can no longer collide. The five
names in section 2.2 become an explicit, documented interface instead of an
accident.

`console.js` is not wrapped and is not otherwise changed: it is the entry
point, it already assigns its own button handlers at top level, and it reads
the five exported names off the global scope exactly as it does now.

**Not chosen: ES modules.** They give real lexical isolation and need no
build step here (the assets are served over HTTP and there are no inline
handlers). They were rejected as more churn than this defect warrants:
restructuring all three files, changing `index.html`, teaching the
allowlisted asset map to serve `type="module"`, and changing script timing to
deferred. The IIFE keeps the load model, `index.html`, and the asset-serving
code identical. Revisit if a fourth or fifth script lands.

**Not chosen: renaming `room.js`'s `buildCard`.** One line, fixes the live
symptom, leaves every other leaked helper able to collide the same way.

---

## 4. Testing

Each defect gets a test that attacks the specific blindness that hid it.
A test that merely re-covers the happy path is not acceptable here.

### 4.1 Part A

**The rule: never validate wire output with `json.loads`.** It accepts
`Infinity` and `NaN`, so it is a more permissive double for `JSON.parse` and
would pass against the bug. Validate on the raw text, or with
`json.loads(..., parse_constant=<raiser>)`, which turns the extension tokens
into an error.

1. `dumps()` converts `inf`, `-inf` and `nan` to `null`, at the top level,
   nested in a dict, and nested in a list.
2. `dumps()` output for a payload containing `float("inf")` contains no bare
   `Infinity`, `-Infinity` or `NaN` token, asserted on the raw string.
3. `dumps()` passes `**kwargs` through, pinned with `separators=(",", ":")`.
4. `dumps()` leaves finite values byte-identical to `json.dumps` for a
   representative payload, so this is provably not a formatting change.
5. A `ConsoleAgent` snapshot built from a Bit whose `status()` returns
   `float("inf")` is strictly parseable. This is the regression test for the
   live defect and it must fail before the fix.
6. The same for `devicelink/server.py`'s outbound path, since that is the
   wire with a real non-Python consumer.

`TestBit` keeps returning `float("inf")` for an unbounded run. That is now
deliberate: it is the fixture that proves the boundary handles it. Changing
`TestBit` instead would delete the regression test's own input.

### 4.2 Part B

7. **A collision test that reads `index.html`.** Parse the `<script src=...>`
   tags in order, load each file into a fresh `vm` context, record the global
   names each one defines, and assert the sets are disjoint except for the
   five declared entry points. Reading the load order out of `index.html`
   rather than hardcoding it means a script added later is covered
   automatically, which is the property that makes this a class-level guard
   rather than a second point fix.
8. **A full-stack behavioral test.** Load all three scripts together, in
   `index.html` order, into one DOM stub. Dispatch a `room_changed` carrying
   instruments and then a snapshot carrying triggers. Assert the Room's
   instrument cards render, the Triggers panel renders one card per declared
   trigger, and no exception escapes `handle()`. This is exactly the live
   failure and it must fail before the fix.

Both go in `tests/js/`, wrapped by a pytest file that skips cleanly where
node is absent, matching the existing `tests/test_room_panel_behavior.py`
pattern.

---

## 5. What does not change

- No Console UI change. `run_duration: null` renders through the existing
  null handling.
- No protocol or wire-shape change. Field names, types and message
  vocabulary are untouched; only non-finite floats, which were never
  representable in JSON, become `null`.
- No `Bit` interface change. `status()` stays a free-form key/value read-out.
- No `console.js` change, no `index.html` change, no change to the
  allowlisted asset map.
- `harness/run_stack.py` keeps passing `--hold`. It was the trigger, not the
  cause, and a finite-duration workaround would leave the boundary unguarded.

---

## 6. Success criteria

1. A Console opened during a `run_stack --console-port N` run renders every
   panel, with `run_duration` shown as unbounded.
2. The Triggers panel renders one card per declared trigger, with a working
   Fire button, and firing `play_aurora` updates that card's status line
   without rebuilding the list.
3. The Room panel renders its instrument cards, and the Event log populates.
4. No uncaught exception in the browser console across a full run.
5. Every outbound payload from all eight call sites is strictly parseable.
6. The offline suite passes (baseline 1057 passed, 1 skipped), including the
   two new JS tests, and skips them cleanly with no node present.
7. Tests 5 and 8 in section 4 demonstrably fail before their respective fix.

---

## 7. Live verification

Re-run the checklist this blocked:
`harness/run_stack.py --room-type TEST --devices 0 --console-port N`, open the
Console, confirm every panel renders, fire `play_aurora` from the Fire button
with no device joined, and confirm the card's status line reads `ADMIN MANUAL`
while the Room's role name, registration counts and node id stay absent.

The backend half of that checklist is already verified as of 2026-08-19: the
fire resolves to both bound fixtures with three steps, reports
`fired_by: "admin-manual"` distinctly from `declared_source: "bit-adjudicated"`,
and the Room's zones visibly depart from their ambient drift for the script's
declared duration. What this spec restores is the operator's ability to do it
from the panel rather than from a raw websocket.
