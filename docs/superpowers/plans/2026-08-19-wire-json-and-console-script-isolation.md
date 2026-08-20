# Valid Wire JSON and Isolated Console Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a non-finite float from making every outbound payload invalid JSON, and stop two Console scripts from silently overwriting each other's globals. Together these restore the Terrarium Console, which currently renders nothing at all on every `run_stack` run.

**Architecture:** Two independent fixes. Part A adds one `dumps()` at `control/wire_json.py` that replaces non-finite floats with `null` and is adopted at all eight outbound JSON sites. Part B wraps `room.js` and `triggers.js` in IIFEs that export only the five entry points `console.js` calls, making every helper private. Each part ships with a test that attacks the specific blindness that hid it.

**Tech Stack:** Python 3.14 stdlib only (`json`, `math`, `logging`). Plain browser JavaScript with no build step and no npm. Node's built-in `vm` and `assert` as a test runner only.

**Spec:** `docs/superpowers/specs/2026-08-19-wire-json-and-console-script-isolation-design.md`

## Global Constraints

- **Branch:** `claude/test-demo-simulator-tests-e1b940` in the worktree `/Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20`. Work there, not in the main checkout.
- **Run the suite as** `.venv/bin/python -m pytest tests -q` from the worktree root. Baseline: **1057 passed, 1 skipped**. There is no bare `python` on this box, and the sibling luxaeterna dependency is installed only in `.venv`; using `python3` produces an import error in `tests/test_terrarium_boot.py` that looks like a real failure and is not.
- **Never validate wire output with plain `json.loads`.** It accepts `Infinity`, `-Infinity` and `NaN`, so it is a more permissive double for the browser's `JSON.parse` and will pass against the very bug under test. Assert on the raw string, or pass `parse_constant=` a function that raises.
- **`TestBit` keeps returning `float("inf")`** for an unbounded run. It is the regression fixture that proves the boundary handles it. Do not "fix" it there.
- **`control/` imports no luxaeterna and no pyarco**, at module level or otherwise. `control/wire_json.py` is pure stdlib and must stay that way.
- **No npm, no `package.json`, no JS dependency.** Node built-ins only. A venue box must never need npm.
- **`console.js` and `console/static/index.html` are not modified by this plan.**
- Commit with explicit paths. Do not use `git add -A` or `git add .`.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `control/wire_json.py` | The single outbound JSON serialiser. Pure, no I/O. | 1 |
| `tests/test_wire_json.py` | Unit tests for the serialiser in isolation. | 1 |
| `console/server.py`, `devicelink/server.py`, `devicelink/o2_transport.py`, `uplink/transport.py`, `capture/store.py` | Adopt `wire_json.dumps`. | 2 |
| `tests/test_wire_json_boundaries.py` | Regression tests that a real Console snapshot and a real devicelink payload survive a non-finite value. | 2 |
| `tests/js/console_script_isolation.test.js` | Collision guard: no two scripts define the same global. | 3 |
| `tests/js/console_full_stack.test.js` | Behavioral: all three scripts loaded together render both panels. | 3 |
| `tests/test_console_script_isolation.py` | pytest wrapper for both JS files. | 3 |
| `console/static/room.js`, `console/static/triggers.js` | IIFE wrap with explicit exports. | 3 |

---

### Task 1: `control/wire_json.py`, the single outbound serialiser

**Files:**
- Create: `control/wire_json.py`
- Test: `tests/test_wire_json.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `control.wire_json.dumps(obj, **kwargs) -> str`. Task 2 replaces eight `json.dumps(x)` calls with `wire_json.dumps(x)`, passing `**kwargs` through unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wire_json.py`:

```python
"""The outbound JSON serialiser.

Note what these tests deliberately do NOT use: plain json.loads. Python's
decoder accepts the same Infinity/NaN extension its encoder emits, so a
round-trip through json.loads agrees with itself and disagrees with every
browser. That asymmetry is exactly what hid the defect this module exists
to fix, so every assertion here is either on the raw string or through a
strict parser.
"""

import json
import math

import pytest

from control.wire_json import dumps


def strict_loads(text: str):
    """json.loads with the Python-only extension tokens rejected, which is
    what a browser's JSON.parse does."""
    def reject(token):
        raise ValueError(f"non-JSON token {token!r}")
    return json.loads(text, parse_constant=reject)


def test_infinity_becomes_null_at_the_top_level():
    assert strict_loads(dumps({"d": float("inf")})) == {"d": None}


def test_negative_infinity_and_nan_become_null():
    out = strict_loads(dumps({"a": float("-inf"), "b": float("nan")}))
    assert out == {"a": None, "b": None}


def test_non_finite_nested_in_a_dict_becomes_null():
    out = strict_loads(dumps({"outer": {"inner": float("inf")}}))
    assert out == {"outer": {"inner": None}}


def test_non_finite_nested_in_a_list_becomes_null():
    out = strict_loads(dumps({"xs": [1.0, float("inf"), 3.0]}))
    assert out == {"xs": [1.0, None, 3.0]}


def test_output_carries_no_bare_extension_token():
    """Asserted on the raw string: this is the property browsers care about
    and the one a decoded comparison cannot see."""
    text = dumps({"a": float("inf"), "b": float("-inf"), "c": float("nan")})
    assert "Infinity" not in text
    assert "NaN" not in text
    assert text.count("null") == 3


def test_kwargs_pass_through():
    """capture/store.py already serialises with separators=(",", ":") and
    must keep doing so."""
    text = dumps({"a": 1, "b": 2}, separators=(",", ":"))
    assert text == '{"a":1,"b":2}'


def test_finite_payloads_are_byte_identical_to_json_dumps():
    """Proves this is not a formatting change for the overwhelmingly common
    case, so adopting it at eight call sites cannot alter any existing wire
    output."""
    payload = {"state": "RUNNING", "n": 3, "f": 1.5, "t": True,
               "z": None, "xs": [1, 2, 3], "d": {"k": "v"}}
    assert dumps(payload) == json.dumps(payload)


def test_a_missed_path_raises_rather_than_emitting_a_bad_token():
    """allow_nan=False is the belt to the sanitiser's braces: a path the
    walk misses must fail loudly, never emit a token no browser can read.

    The reachable missed path today is a non-finite float used as a dict
    KEY: _sanitise's dict branch sanitises values, not keys, so the key
    reaches json.dumps untouched and the belt is what stops it."""
    with pytest.raises(ValueError):
        dumps({float("inf"): "x"})


def test_bools_and_ints_are_untouched():
    assert dumps({"t": True, "f": False, "n": 7}) == '{"t": true, "f": false, "n": 7}'


def test_warns_once_per_path_not_once_per_value(caplog):
    """A 44 Hz loop must not produce a 44 Hz log."""
    import control.wire_json as wj
    wj._warned.clear()
    with caplog.at_level("WARNING"):
        for _ in range(5):
            dumps({"status": {"run_duration": float("inf")}})
    assert len([r for r in caplog.records if "run_duration" in r.message]) == 1


def test_list_indices_do_not_grow_the_warning_set_without_bound(caplog):
    """Paths are normalised so a 10000-element list logs one warning, not
    10000, and _warned stays bounded by schema shape rather than data size."""
    import control.wire_json as wj
    wj._warned.clear()
    with caplog.at_level("WARNING"):
        dumps({"xs": [float("inf")] * 50})
    assert len(wj._warned) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_wire_json.py -v
```

Expected: every test FAILS at collection with `ModuleNotFoundError: No module named 'control.wire_json'`.

- [ ] **Step 3: Write the implementation**

Create `control/wire_json.py`:

```python
"""json.dumps for anything leaving this process.

A non-finite float is not representable in JSON. Python's encoder emits the
bare tokens Infinity/-Infinity/NaN anyway, as a documented extension, and
Python's own decoder accepts them -- so a payload carrying one round-trips
cleanly within Python and is rejected outright by every strict parser,
including every browser's JSON.parse and Dart's jsonDecode.

That asymmetry cost a live run on 2026-08-19. TestBit.status()'s
run_duration is float("inf") under --hold, which harness/run_stack.py
always passes, so the Terrarium Console's snapshot failed JSON.parse in the
browser and every panel rendered empty while the stack was perfectly
healthy. Design:
docs/superpowers/specs/2026-08-19-wire-json-and-console-script-isolation-design.md

Every outbound JSON boundary in this repo calls dumps() rather than
json.dumps for that reason. Pure stdlib, so control/ stays free of
luxaeterna and pyarco.
"""

from __future__ import annotations

import json
import logging
import math
import re

logger = logging.getLogger(__name__)

# Paths already warned about, so a 44 Hz loop does not produce a 44 Hz log.
# Keyed on the SHAPE of the path (list indices collapsed to "[]") so the set
# is bounded by the payload's schema rather than by how much data flows
# through it.
_warned: set[str] = set()

_INDEX = re.compile(r"\[\d+\]")


def _note(value: float, path: str) -> None:
    shape = _INDEX.sub("[]", path)
    if shape in _warned:
        return
    _warned.add(shape)
    logger.warning(
        "non-finite float %r at %s is not representable in JSON; sending "
        "null instead", value, shape)


def _sanitise(value, path: str):
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        _note(value, path)
        return None
    if isinstance(value, dict):
        return {k: _sanitise(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise(v, f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def dumps(obj, **kwargs) -> str:
    """Serialise obj as JSON a strict parser will accept.

    Non-finite floats become null. That is not an arbitrary placeholder:
    this wire format already uses null to mean unbounded (an uncapped
    Role.capacity is None, and the Console renders it as an infinity sign),
    so null carries the meaning Infinity was reaching for and needs no
    consumer-side change.

    allow_nan=False is deliberate belt-and-braces. If _sanitise ever misses
    a path, json.dumps raises rather than emitting a token no browser can
    read: a loud failure in a test beats a silent one at a venue.

    kwargs pass through to json.dumps, because capture/store.py already
    serialises with separators=(",", ":").
    """
    return json.dumps(_sanitise(obj, "$"), allow_nan=False, **kwargs)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_wire_json.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run the whole suite**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests -q
```

Expected: `1068 passed, 1 skipped` (1057 baseline plus 11 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20
git add control/wire_json.py tests/test_wire_json.py
git commit -m "feat(wire): one JSON serialiser that cannot emit a non-finite float

Python's json encoder emits the bare tokens Infinity/-Infinity/NaN as a
documented extension, and Python's decoder accepts them, so such a payload
round-trips cleanly inside Python and is rejected by every strict parser,
including every browser's JSON.parse.

wire_json.dumps() replaces non-finite floats with null before serialising,
then passes allow_nan=False so a missed path raises rather than emitting a
token no browser can read. null is the right value rather than a
placeholder: this wire format already uses null for unbounded.

No caller yet; adoption is the next commit."
```

---

### Task 2: Adopt `wire_json.dumps` at all eight outbound sites

**Files:**
- Modify: `console/server.py:126`, `console/server.py:135`
- Modify: `devicelink/server.py:97`, `devicelink/server.py:106`
- Modify: `devicelink/o2_transport.py:59`
- Modify: `uplink/transport.py:85`
- Modify: `capture/store.py:194`, `capture/store.py:211`
- Test: `tests/test_wire_json_boundaries.py`

**Interfaces:**
- Consumes: `control.wire_json.dumps(obj, **kwargs) -> str` from Task 1.
- Produces: no new API. Every outbound payload is strictly parseable.

Note that `capture/` imports nothing from `control/` today, so this gives it its first such import. That is the same downward direction every other package already follows.

- [ ] **Step 1: Write the failing regression tests**

Create `tests/test_wire_json_boundaries.py`:

```python
"""The two boundaries with real non-Python consumers, tested end to end.

test_console_snapshot_survives_an_infinite_bit_status is the regression
test for the 2026-08-19 live defect: a Console connected to a healthy stack
rendered every panel empty because TestBit.status()'s run_duration is
float("inf") under --hold and the snapshot therefore failed JSON.parse.
"""

import json

import pytest

from console.agent import ConsoleAgent
from control.engine import GameServer
from bits.test_bit import TestBit


def strict_loads(text: str):
    def reject(token):
        raise ValueError(f"non-JSON token {token!r}")
    return json.loads(text, parse_constant=reject)


class _FakeServer:
    """The socket half ConsoleAgent talks to, reduced to what it calls."""

    def __init__(self):
        self.sent = []

    def drain_new_clients(self):
        return []

    def drain_inbound(self):
        return []

    def send(self, client, msg):
        self.sent.append(msg)

    def broadcast(self, msg):
        self.sent.append(msg)


def test_console_snapshot_survives_an_infinite_bit_status():
    """The exact live failure: an unbounded run_duration must not make the
    whole snapshot unparseable."""
    gs = GameServer({"TestBit": lambda: TestBit(run_duration=float("inf"))})
    gs.load_bit("TestBit")
    agent = ConsoleAgent(gs, _FakeServer())
    snapshot = agent.snapshot()

    assert snapshot["bit_status"]["run_duration"] == float("inf")   # source is unchanged

    # Serialise it exactly as ConsoleServer does, and parse it strictly.
    from control.wire_json import dumps
    text = dumps(snapshot)
    assert "Infinity" not in text
    assert strict_loads(text)["bit_status"]["run_duration"] is None


def test_console_server_send_uses_the_guarded_serialiser():
    """Pins the call site itself, not just the helper: a future edit that
    reverts console/server.py to json.dumps fails here."""
    import console.server as server_mod
    src = (server_mod.__file__)
    text = open(src).read()
    assert "json.dumps(" not in text, "console/server.py must use wire_json.dumps"


def test_devicelink_payload_survives_a_non_finite_value():
    """The device wire has real non-Python consumers (phones parsing with
    JSON.parse, Dart clients with jsonDecode), so it gets the same guard."""
    import devicelink.server as server_mod
    text = open(server_mod.__file__).read()
    assert "json.dumps(" not in text, "devicelink/server.py must use wire_json.dumps"

    from control.wire_json import dumps
    msg = {"timestamp": float("inf"), "address": "/ie1/leds",
           "typespec": "b", "args": [1, 2, 3]}
    out = dumps(msg)
    assert "Infinity" not in out
    assert strict_loads(out)["timestamp"] is None


@pytest.mark.parametrize("module_name", [
    "uplink.transport", "devicelink.o2_transport", "capture.store",
])
def test_remaining_outbound_sites_use_the_guarded_serialiser(module_name):
    import importlib
    mod = importlib.import_module(module_name)
    text = open(mod.__file__).read()
    assert "json.dumps(" not in text, f"{module_name} must use wire_json.dumps"
```

**If `ConsoleAgent.snapshot()` needs more setup than shown** (a room binding,
say, for the Room panel's read-out), build it exactly the way
`tests/test_console_agent.py` already does rather than inventing a second
construction. That file is the established reference for wiring a GameServer
and a ConsoleAgent together in a test, and matching it keeps one pattern in
the suite instead of two.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_wire_json_boundaries.py -v
```

Expected: the call-site tests FAIL because every module still contains `json.dumps(`. `test_console_snapshot_survives_an_infinite_bit_status` may pass already, since it calls `wire_json.dumps` directly rather than going through the server; that is fine, it is the regression pin for the payload shape.

- [ ] **Step 3: Adopt the serialiser at all eight sites**

In each file, add the import and replace the call. The import line for each is:

```python
from control.wire_json import dumps as _json_dumps
```

Then replace, exactly:

- `console/server.py:126`: `client.send(json.dumps(msg))` becomes `client.send(_json_dumps(msg))`
- `console/server.py:135`: `payload = json.dumps(msg)` becomes `payload = _json_dumps(msg)`
- `devicelink/server.py:97`: `client.send(json.dumps(msg))` becomes `client.send(_json_dumps(msg))`
- `devicelink/server.py:106`: `payload = json.dumps(msg)` becomes `payload = _json_dumps(msg)`
- `devicelink/o2_transport.py:59`: `return Blob(json.dumps(value).encode("utf-8"))` becomes `return Blob(_json_dumps(value).encode("utf-8"))`
- `uplink/transport.py:85`: `self._ws.send(json.dumps(msg))` becomes `self._ws.send(_json_dumps(msg))`
- `capture/store.py:194`: `body = json.dumps(trace.to_dict(audio_file), separators=(",", ":"))` becomes `body = _json_dumps(trace.to_dict(audio_file), separators=(",", ":"))`
- `capture/store.py:211`: `line = json.dumps({...})` becomes `line = _json_dumps({...})`, leaving the dict literal exactly as it is

The alias `_json_dumps` is used rather than a bare `dumps` so the call sites still read as serialisation and do not collide with any local name. After the edits, remove the now-unused `import json` from any module that no longer references `json.` for anything else, and keep it where the module still uses `json.loads` or `json.JSONDecodeError`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_wire_json_boundaries.py tests/test_console_agent.py tests/test_devicelink_server.py -v
```

Expected: all PASS. If `tests/test_devicelink_server.py` does not exist under that name, run `.venv/bin/python -m pytest tests -q -k devicelink` instead and report the actual file names in your report.

- [ ] **Step 5: Run the whole suite**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests -q
```

Expected: `1074 passed, 1 skipped` (1068 plus 6 new: three test functions
plus a three-way parametrised one). Every pre-existing test must still pass. In particular `tests/test_console_agent.py` and the capture tests read these payloads and must be unaffected, because Task 1 pinned that finite payloads serialise byte-identically.

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20
git add control/wire_json.py console/server.py devicelink/server.py \
        devicelink/o2_transport.py uplink/transport.py capture/store.py \
        tests/test_wire_json_boundaries.py
git commit -m "fix(wire): serialise every outbound payload through wire_json.dumps

All eight json.dumps sites now go through the guarded serialiser: the
Console's two, the devicelink server's two, the o2lite blob encoder, the
uplink, and capture's two on-disk writes.

The Console was the one observed failing: TestBit's run_duration is
float(\"inf\") under --hold, which run_stack always passes, so the snapshot
carried a bare Infinity token, JSON.parse rejected the whole message, and
every panel rendered empty against a healthy stack.

The device wire is the one that mattered most to fix at the same time: its
consumers parse with JSON.parse and jsonDecode, neither of which accepts
the extension Python emits."
```

---

### Task 3: Isolate the Console scripts, and prove no two can collide

**Files:**
- Create: `tests/js/console_script_isolation.test.js`
- Create: `tests/js/console_full_stack.test.js`
- Create: `tests/test_console_script_isolation.py`
- Modify: `console/static/room.js` (wrap in an IIFE, export two names)
- Modify: `console/static/triggers.js` (wrap in an IIFE, export three names)

**Interfaces:**
- Consumes: nothing from Tasks 1 and 2.
- Produces: `window.renderRoom`, `window.renderRoomFrame` from room.js; `window.renderTriggers`, `window.renderTriggerDevices`, `window.renderTriggerFired` from triggers.js. `console.js` already calls exactly these five names and is not modified.

This is TDD: both JS tests must fail before the wrap and pass after.

- [ ] **Step 1: Write the collision test**

Create `tests/js/console_script_isolation.test.js`:

```javascript
"use strict";
// Collision guard for console/static/*.js.
//
// These files load as plain scripts into one shared global scope. On
// 2026-08-19 room.js and triggers.js both declared `function buildCard`,
// triggers.js silently won, and renderRoom then called the wrong one and
// threw on every room_changed -- 222 throws in 2.5s live, killing the Room
// cards, the whole Triggers panel and the Event log.
//
// The existing behavioural tests could not see it BY CONSTRUCTION:
// room_panel_behavior.test.js loads room.js + console.js, and
// trigger_panel_behavior.test.js loads triggers.js. Nothing ever loaded
// room.js and triggers.js together, which is the only pair that collides.
// Each file is correct alone; the defect exists only in the combination the
// browser actually loads.
//
// This test reads the <script src> list out of index.html rather than
// hardcoding it, so a fourth script added later is covered automatically.
// That is what makes this a class-level guard instead of a second point fix.
//
// Run directly: node tests/js/console_script_isolation.test.js

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = path.join(__dirname, "..", "..", "console", "static");
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");

// The load order the browser actually uses.
const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)]
  .map((m) => m[1]);
assert.ok(scripts.length >= 3,
  `expected index.html to load at least 3 scripts, found ${scripts.length}`);

// A stub just rich enough for each file to evaluate its top level.
// console.js assigns button handlers and calls connect() immediately.
function freshSandbox() {
  const node = () => ({
    set onclick(_fn) {}, get onclick() { return null; },
    appendChild() {}, setAttribute() {}, addEventListener() {},
    style: {}, classList: { add() {}, remove() {} },
    innerHTML: "", textContent: "", value: "", children: [],
  });
  const sandbox = {
    console,
    document: {
      getElementById: () => node(),
      createElement: () => node(),
      querySelector: () => node(),
      querySelectorAll: () => [],
    },
    location: { host: "localhost:1", protocol: "http:" },
    setTimeout() {}, clearTimeout() {},
    WebSocket: function () { this.send = () => {}; },
  };
  sandbox.window = sandbox;          // as in a browser
  return sandbox;
}

// The names each script contributes to the shared global scope.
function globalsDefinedBy(file) {
  const sandbox = freshSandbox();
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  const src = fs.readFileSync(path.join(STATIC, file), "utf8");
  vm.runInContext(src, sandbox, { filename: file });
  return new Set(Object.keys(sandbox).filter((k) => !before.has(k)));
}

const defined = {};
for (const file of scripts) defined[file] = globalsDefinedBy(file);

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (e) { failures++; console.error(`FAIL ${name}\n     ${e.message}`); }
}

check("no two scripts define the same global name", () => {
  const collisions = [];
  for (let i = 0; i < scripts.length; i++) {
    for (let j = i + 1; j < scripts.length; j++) {
      const a = scripts[i], b = scripts[j];
      for (const nameA of defined[a]) {
        if (defined[b].has(nameA)) collisions.push(`${nameA}: ${a} vs ${b}`);
      }
    }
  }
  assert.deepStrictEqual(collisions, [],
    "these globals are defined by more than one script, so whichever loads "
    + "last silently wins:\n  " + collisions.join("\n  "));
});

check("room.js exports exactly its two entry points", () => {
  assert.deepStrictEqual([...defined["room.js"]].sort(),
    ["renderRoom", "renderRoomFrame"]);
});

check("triggers.js exports exactly its three entry points", () => {
  assert.deepStrictEqual([...defined["triggers.js"]].sort(),
    ["renderTriggerDevices", "renderTriggerFired", "renderTriggers"]);
});

check("console.js can reach every name it dispatches to", () => {
  const exported = new Set([...defined["room.js"], ...defined["triggers.js"]]);
  for (const needed of ["renderRoom", "renderRoomFrame", "renderTriggers",
                        "renderTriggerDevices", "renderTriggerFired"]) {
    assert.ok(exported.has(needed), `console.js calls ${needed}, nothing exports it`);
  }
});

process.exit(failures ? 1 : 0);
```

- [ ] **Step 2: Write the full-stack behavioural test**

Create `tests/js/console_full_stack.test.js`:

```javascript
"use strict";
// All three Console scripts loaded together, in index.html's order, into
// one context -- the combination the browser actually runs, and the one no
// existing test covered.
//
// This reproduces the 2026-08-19 live failure directly: with room.js and
// triggers.js both defining buildCard, dispatching a room_changed threw
// inside renderRoom, which aborted handle() and left the Room's instrument
// cards, the Triggers panel and the Event log permanently empty.
//
// Run directly: node tests/js/console_full_stack.test.js

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = path.join(__dirname, "..", "..", "console", "static");
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]);

// ---- minimal DOM stub ---------------------------------------------------
function makeNode(id) {
  const node = {
    id, tagName: "DIV", children: [], style: {}, dataset: {},
    innerHTML: "", textContent: "", value: "", className: "",
    onclick: null,
    appendChild(child) { this.children.push(child); return child; },
    setAttribute(k, v) { this[k] = v; },
    addEventListener() {},
    classList: { add() {}, remove() {}, contains: () => false },
  };
  return node;
}

function makeDocument() {
  const byId = {};
  return {
    _byId: byId,
    getElementById(id) { return (byId[id] = byId[id] || makeNode(id)); },
    createElement(tag) { const n = makeNode(null); n.tagName = tag.toUpperCase(); return n; },
    querySelector() { return makeNode(null); },
    querySelectorAll() { return []; },
  };
}

function loadAll() {
  const doc = makeDocument();
  const sandbox = {
    console, document: doc,
    location: { host: "localhost:1", protocol: "http:" },
    setTimeout() {}, clearTimeout() {},
    WebSocket: function () { sandbox.__sock = this; this.send = () => {}; },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const file of scripts) {
    const src = fs.readFileSync(path.join(STATIC, file), "utf8");
    vm.runInContext(src, sandbox, { filename: file });
  }
  return sandbox;
}

// ---- fixtures matching the real wire shapes -----------------------------
const room = () => ({
  room_type: "TEST",
  capability: { surface_id: "room_test", pixel_count: 90, color_order: "GRB",
                zones: [{ name: "main.left", start: 0, count: 20 }] },
  fixtures: [{ name: "main", pixel_count: 60, channel_start: 0,
               channel_count: 180, dev: "sim-room-main",
               zones: [{ name: "main.left", start: 0, count: 20 }] }],
  instruments: [{ kind: "light", instrument: "rainbow", target: "primary",
                  lanes: [{ source: "cc:74", dest: "hue" }] }],
  controllers: { "cc:74": 64 },
});

const triggers = () => ([{
  name: "play_aurora", description: "A slow rainbow sweep across the Room",
  target: "ROOM",
  condition: { name: "round_won", description: "User wins a round",
               source: "bit-adjudicated", verb: null },
  script: [{ offset: 0.0, kind: "light", dev: "@target",
             status: 176, data1: 74, data2: 127 }],
}]);

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (e) { failures++; console.error(`FAIL ${name}\n     ${e.message}`); }
}

check("a room_changed does not throw with every script loaded", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({ event: "room_changed", room: room() }) });
});

check("the Room panel renders its instrument cards", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({ event: "room_changed", room: room() }) });
  const cards = box.document._byId["roomCards"];
  assert.ok(cards && cards.children.length >= 1,
    "expected at least one instrument card in #roomCards");
});

check("the Triggers panel renders one card per declared trigger", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({
    event: "snapshot", state: "RUNNING", loaded_bit: "TestBit",
    installed_bits: ["TestBit"], registration: [], roles: [], devices: [],
    bit_status: {}, room: room(), triggers: triggers(),
  }) });
  const cards = box.document._byId["triggerCards"];
  assert.ok(cards && cards.children.length === 1,
    `expected 1 trigger card, got ${cards ? cards.children.length : "no #triggerCards"}`);
});

check("a room_changed after a snapshot leaves the trigger cards intact", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({
    event: "snapshot", state: "RUNNING", loaded_bit: "TestBit",
    installed_bits: ["TestBit"], registration: [], roles: [], devices: [],
    bit_status: {}, room: room(), triggers: triggers(),
  }) });
  const before = box.document._byId["triggerCards"].children.length;
  box.__sock.onmessage({ data: JSON.stringify({ event: "room_changed", room: room() }) });
  assert.strictEqual(box.document._byId["triggerCards"].children.length, before,
    "a room_changed must not disturb the trigger cards");
});

process.exit(failures ? 1 : 0);
```

- [ ] **Step 3: Write the pytest wrapper**

Create `tests/test_console_script_isolation.py`:

```python
"""The Console's scripts, tested as the browser actually loads them.

console/static/*.js load as plain scripts into one shared global scope.
Nothing tested that combination before: room_panel_behavior.test.js loads
room.js with console.js, trigger_panel_behavior.test.js loads triggers.js,
and no test loaded room.js and triggers.js together -- which is the only
pair that collided. See
docs/superpowers/specs/2026-08-19-wire-json-and-console-script-isolation-design.md.

No build step: node is used here only as a test runner for plain scripts,
never as a shipped dependency. Skips cleanly if node is unavailable.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    fallback = Path("/opt/homebrew/bin/node")
    return str(fallback) if fallback.exists() else None


NODE = _find_node()


@pytest.mark.skipif(NODE is None, reason="node not found on this box")
@pytest.mark.parametrize("script", [
    "console_script_isolation.test.js",
    "console_full_stack.test.js",
])
def test_console_scripts(script):
    result = subprocess.run(
        [NODE, str(ROOT / "tests" / "js" / script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"{script} failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
```

- [ ] **Step 4: Run both JS tests to verify they FAIL**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_console_script_isolation.py -v
```

Expected: BOTH parametrised cases FAIL.

`console_script_isolation.test.js` should report `buildCard: room.js vs triggers.js` in its collision list, and should fail the two "exports exactly" checks because both files currently define many globals.

`console_full_stack.test.js` should fail with the live error, `TypeError: Cannot read properties of undefined (reading 'description')`, thrown out of `renderRoom`.

Record both failure outputs; they are the RED evidence.

- [ ] **Step 5: Wrap room.js in an IIFE**

In `console/static/room.js`, leave the entire leading comment block exactly as it is. Immediately after it, and before `let roomFixtureShapes = {};`, insert:

```javascript
(function () {
"use strict";
```

At the very end of the file, after the final closing brace of `renderRoomFrame`, append:

```javascript

// The panel's entry points, and the ONLY names this file puts in the shared
// global scope. console.js dispatches to these; everything else above is
// private, so a helper here can never again silently overwrite a
// same-named helper in another script. See
// docs/superpowers/specs/2026-08-19-wire-json-and-console-script-isolation-design.md
window.renderRoom = renderRoom;
window.renderRoomFrame = renderRoomFrame;
})();
```

Do not re-indent the body. The diff should be the two added blocks only, so a reviewer can see nothing else changed.

- [ ] **Step 6: Wrap triggers.js in an IIFE**

In `console/static/triggers.js`, leave the leading comment block as it is. Immediately after it, and before `let triggerSignature = null;`, insert:

```javascript
(function () {
"use strict";
```

At the very end of the file, after the final closing brace of `renderTriggerFired`, append:

```javascript

// The panel's entry points, and the ONLY names this file puts in the shared
// global scope. buildCard in particular is private now: it previously
// collided with room.js's same-named helper, and because triggers.js loads
// second it silently won, making renderRoom throw on every room_changed.
window.renderTriggers = renderTriggers;
window.renderTriggerDevices = renderTriggerDevices;
window.renderTriggerFired = renderTriggerFired;
})();
```

- [ ] **Step 7: Run both JS tests to verify they PASS**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_console_script_isolation.py -v
```

Expected: both PASS, with every `ok` line reported.

- [ ] **Step 8: Run the pre-existing JS tests, which must still pass**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_room_panel_behavior.py tests/test_trigger_panel_behavior.py tests/test_console_static.py -v
```

Expected: all PASS. These load room.js and triggers.js and call their functions by name inside the same vm context. **If any fail because the wrapped functions are no longer reachable as bare names, that is expected and must be fixed by having those tests call them off `window` (the sandbox is its own `window` in those harnesses, or can be made so by adding `sandbox.window = sandbox` before `vm.createContext`). Report exactly what you changed and why.** Do not weaken any assertion to make them pass.

- [ ] **Step 9: Run the whole suite**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests -q
```

Expected: `1076 passed, 1 skipped` (1074 plus 2 parametrised cases).

- [ ] **Step 10: Commit**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20
git add console/static/room.js console/static/triggers.js \
        tests/js/console_script_isolation.test.js \
        tests/js/console_full_stack.test.js \
        tests/test_console_script_isolation.py
git commit -m "fix(console): isolate the panel scripts so helpers cannot collide

room.js and triggers.js both declared a global buildCard. They load as
plain scripts into one scope, so triggers.js silently won, and renderRoom
then called the trigger version with a room instrument and threw on every
room_changed -- 222 throws in 2.5s live. That aborted handle(), leaving the
Room's instrument cards, the whole Triggers panel and the Event log
permanently empty, which is what blocked the triggers live-verify
checklist.

Both files are now IIFE-wrapped and export only what console.js dispatches
to. Every helper is private.

Two new tests attack the gap that hid this. The existing behavioural tests
could not see it by construction: one loads room.js with console.js, the
other loads triggers.js, and nothing ever loaded the one pair that
collides. The collision guard reads the script list out of index.html, so a
script added later is covered automatically."
```

---

### Task 4: Live verification, the checklist this unblocked

**Files:** none. This task changes no code. It is the acceptance gate.

**Interfaces:**
- Consumes: the finished work of Tasks 1 through 3.
- Produces: nothing consumed by later tasks.

This re-runs the live-verify checklist from
`docs/superpowers/specs/2026-08-17-bit-declared-triggers-and-cue-scripts-design.md`
section 13.1, which both defects blocked. It needs a real Arco.

- [ ] **Step 1: Bring up the TEST stack with the Console**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.run_stack --room-type TEST --devices 0 --console-port 8099 --seconds 600
```

`--devices 0` is required, not a convenience: the checklist's whole point is that this acceptance does not depend on a device joining, because headless device clock-sync is an unfixed upstream defect. It also means `run_stack` keeps passing `--hold`, so `run_duration` is still `float("inf")` and this run exercises the exact payload that used to break.

Wait for `Terrarium Console: http://127.0.0.1:8099/` in the output.

- [ ] **Step 2: Confirm the Console renders**

Open `http://127.0.0.1:8099/`. Confirm, all of which were empty before this work:

- State reads `RUNNING` and Loaded Bit reads `TestBit`.
- The Room panel shows `TEST · 90 px · GRB · 2/2 fixture(s) bound`, two labelled strips, and at least one instrument card.
- The Triggers panel shows a card for `play_aurora` and one for `flash_device`.
- Registration shows `player` and `jammer` with count 0.
- Bit status shows `run_duration` as unbounded rather than blanking the panel.
- The browser console reports **no** uncaught exceptions.

- [ ] **Step 3: Fire the trigger from the panel**

Click Fire on the `play_aurora` card, with no device joined. Confirm:

- The card's status line updates in place and reads `ADMIN MANUAL`.
- The trigger card list is not rebuilt: the other card keeps its state.
- The Room's zones visibly move for the script's declared 2 seconds.

- [ ] **Step 4: Confirm the Room stays hidden**

With the panel now rendering, confirm the Room's role name (`room_test`), its registration counts, and its node id (`ROOM_TEST_NODE`) appear in no panel. The Room's surface id and zone names ARE expected to be visible: the 2026-08-17 narrowing made those deliberately visible while keeping the node id, counts and role name hidden.

- [ ] **Step 5: Tear down and confirm no orphans**

```bash
pkill -f 'harness.run_stack'; sleep 2; pkill -f 'harness.terrarium_boot|harness.o2_shroom|apps/pytest/server'; sleep 1; pgrep -fl 'run_stack|terrarium_boot|o2_shroom|pytest/server' || echo "clean"
```

Expected: `clean`.

---

## After the plan

The remaining live-verify checklist is the DEMO room's
(`2026-08-19-demo-room-and-block-profile-design.md` section 7): the rainbow
sweeping all 864 px with no seam at the six block boundaries, plus a device
joining and completing a scored round. The device-join half is gated behind
the upstream headless clock-sync defect and has to be run from an interactive
terminal.

A deep-dive sync for `docs/MM_TERRARIUM.md` is also outstanding: it describes
the Room canvas as a working visual check, which it was not until the
luxaeterna WebSim fix (that repo's PR #14), and it does not yet record either
defect fixed here.
