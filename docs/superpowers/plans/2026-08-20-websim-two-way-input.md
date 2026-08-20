# WebSim Two-Way Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator tap/drag the simulated Tuneshroom's browser canvas and have those gestures reach Control as real `/game/tap` and `/game/tilt` messages.

**Architecture:** The WebSim page gains pointer handlers that send small JSON text messages back over its existing websocket; `WebSimBackend` gains an optional `on_input` callback surfacing them; `harness/o2_shroom.py` bridges callback → thread-safe queue → its existing o2lite tick loop, stamping gestures at `o2lite.time_get()` (Design Rule 4: the whole simulator process is the device). No engine, protocol, or Bit changes.

**Tech Stack:** Python 3 (both repos), luxaeterna `websockets`, Node `vm` JS behavioral tests, o2litepy (injected, never imported at module level).

**Spec:** `docs/superpowers/specs/2026-08-20-websim-two-way-input-design.md` (mm-terrarium repo, this branch).

## Global Constraints

- Two repos: luxaeterna work happens in `/Users/chris/projects/luxaeterna` on a new branch `claude/websim-input` off `main`; mm-terrarium work happens in this worktree on branch `claude/tuneshroom-two-way-input`.
- Suites: mm-terrarium `.venv/bin/python -m pytest tests -q` (baseline 1099 passed, 1 skipped; NEVER bare `python3` — phantom failure). luxaeterna `.venv/bin/python -m pytest tests -q` from that repo's root (~230 baseline).
- mm-terrarium's `.venv` in a fresh worktree is a symlink: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` (already done here).
- `on_input=None` must leave `WebSimBackend` byte-identical in behavior; no browser input must leave `o2_shroom` byte-identical in behavior.
- Drop, never raise, on malformed inbound data (house wire rule).
- No em dashes in any prose written for this project's docs/commits.
- luxaeterna's dev install is editable in mm-terrarium's `.venv`, so luxaeterna edits are picked up by mm-terrarium tests immediately.

---

### Task 1: `WebSimBackend.on_input` (luxaeterna, Python half)

**Files:**
- Modify: `/Users/chris/projects/luxaeterna/luxaeterna/backends/websim.py`
- Test: `/Users/chris/projects/luxaeterna/tests/backends/test_websim_input.py`

**Interfaces:**
- Produces: `WebSimBackend(..., on_input: Callable[[dict], None] | None = None)`. For every inbound websocket **text** message that parses as a JSON object, `on_input(msg_dict)` is called on the handler thread. Binary inbound, malformed JSON, non-dict JSON: dropped. A raising callback is caught; the connection stays up.

- [ ] **Step 1: Create the branch**

```bash
cd /Users/chris/projects/luxaeterna && git checkout -b claude/websim-input main
```

- [ ] **Step 2: Write the failing tests**

Create `tests/backends/test_websim_input.py`:

```python
"""WebSimBackend's inbound seam: browser -> on_input.

Drives _handle() directly with a fake connection, mirroring how
tests/backends/test_websim.py already avoids real sockets. The fake
yields inbound messages exactly as websockets' sync connection does:
str for text frames, bytes for binary."""

from __future__ import annotations

from luxaeterna.backends.websim import WebSimBackend


class FakeConnection:
    def __init__(self, inbound):
        self._inbound = list(inbound)
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)

    def __iter__(self):
        return iter(self._inbound)


def test_text_message_reaches_on_input():
    got = []
    backend = WebSimBackend(serve=False, on_input=got.append)
    backend._handle(FakeConnection(['{"type": "tap", "count": 2}']))
    assert got == [{"type": "tap", "count": 2}]


def test_malformed_json_and_non_dict_are_dropped():
    got = []
    backend = WebSimBackend(serve=False, on_input=got.append)
    backend._handle(FakeConnection(["{not json", "[1, 2]", '"str"']))
    assert got == []


def test_binary_inbound_is_ignored():
    got = []
    backend = WebSimBackend(serve=False, on_input=got.append)
    backend._handle(FakeConnection([b"\x00\x01", '{"type": "tilt", "gamma": 3}']))
    assert got == [{"type": "tilt", "gamma": 3}]


def test_raising_callback_does_not_kill_the_handler():
    calls = []

    def bad(msg):
        calls.append(msg)
        raise RuntimeError("boom")

    backend = WebSimBackend(serve=False, on_input=bad)
    # Two messages: the first raises, the second must still be delivered.
    backend._handle(FakeConnection(['{"a": 1}', '{"b": 2}']))
    assert calls == [{"a": 1}, {"b": 2}]


def test_default_on_input_none_still_drains_inbound():
    backend = WebSimBackend(serve=False)
    conn = FakeConnection(['{"type": "tap"}'])
    backend._handle(conn)  # must not raise
    # The capability handshake still went out.
    assert conn.sent
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/chris/projects/luxaeterna && .venv/bin/python -m pytest tests/backends/test_websim_input.py -q`
Expected: FAIL (`on_input` is an unexpected keyword argument).

- [ ] **Step 4: Implement**

In `luxaeterna/backends/websim.py`:

Add near the top (after existing imports):

```python
import logging

logger = logging.getLogger(__name__)
```

Extend `__init__`'s signature and body:

```python
    def __init__(self, capability: SurfaceCapability | None = None,
                 host: str = "127.0.0.1", port: int = 0,
                 serve: bool = True, label: str | None = None,
                 on_input=None) -> None:
        ...
        self.on_input = on_input
```

Document it in the class docstring's Parameters block:

```
    on_input : callable or None
        Called with the decoded dict for every inbound JSON **text**
        message a connected page sends (the input side of the two-way
        seam). Runs on the websocket handler thread; hand off to your
        own loop if you need one. Binary frames, malformed JSON and
        non-dict payloads are dropped. ``None`` (default) drains and
        discards inbound, exactly as before this seam existed.
```

Replace `_handle`'s hold-open loop (`for _ in connection: pass`) with:

```python
            for raw in connection:               # hold open until close
                if not isinstance(raw, str):
                    continue                     # frames only flow down
                try:
                    msg = json.loads(raw)
                except ValueError:
                    logger.debug("dropping malformed inbound JSON")
                    continue
                if not isinstance(msg, dict) or self.on_input is None:
                    continue
                try:
                    self.on_input(msg)
                except Exception:
                    logger.debug("on_input callback raised", exc_info=True)
```

- [ ] **Step 5: Run the new tests, then the whole luxaeterna suite**

Run: `cd /Users/chris/projects/luxaeterna && .venv/bin/python -m pytest tests/backends/test_websim_input.py -q && .venv/bin/python -m pytest tests -q`
Expected: new tests PASS; suite at baseline (~230 passed) plus these.

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/luxaeterna && git add luxaeterna/backends/websim.py tests/backends/test_websim_input.py && git commit -m "feat(websim): on_input, the inbound half of the browser seam"
```

---

### Task 2: WebSim page gesture handlers (luxaeterna, JS half)

**Files:**
- Modify: `/Users/chris/projects/luxaeterna/luxaeterna/backends/websim.py` (PAGE_HTML only)
- Create: `/Users/chris/projects/luxaeterna/tests/js/websim_input.test.js`
- Create: `/Users/chris/projects/luxaeterna/tests/backends/test_websim_input_page.py`

**Interfaces:**
- Consumes: Task 1's inbound path (the page's sends arrive at `on_input`).
- Produces: the page sends `{"type":"tap","count":1|2}` and `{"type":"tilt","gamma":<float in [-90,90]>}` as JSON text over the existing socket. Gesture detection rules: single click sends tap count 1 after a 250 ms window; a dblclick inside that window cancels it and sends one tap count 2; dragging (pointer down + move) maps canvas x to gamma and sends tilt at most every 50 ms; a drag of more than 5 px suppresses the click that follows it; nothing is sent when the socket is not open.

- [ ] **Step 1: Write the failing JS test and its pytest wrapper**

Create `tests/js/websim_input.test.js`:

```js
/* Behavioral tests for the WebSim page's input handlers.
 *
 * Same harness shape as websim_layout.test.js: the page script is
 * extracted from PAGE_HTML by tests/backends/test_websim_input_page.py
 * and handed here as argv[2]. The WebSocket stub additionally captures
 * sends and models readyState; timers are captured so the click delay
 * window can be stepped deterministically.
 */
const assert = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(process.argv[2], 'utf8');

const tests = [];
function test(name, fn) { tests.push([name, fn]); }

function makeCanvas() {
  const ctx2d = {
    clearRect: () => {}, fillRect: () => {}, beginPath: () => {},
    arc: () => {}, fill: () => {},
    createRadialGradient: () => ({ addColorStop: () => {} }),
    set fillStyle(v) {}, get fillStyle() { return null; },
  };
  return { width: 320, height: 420, clientWidth: 320, getContext: () => ctx2d };
}

function run() {
  const canvas = makeCanvas();
  const status = { textContent: '' };
  const timers = [];
  const sandbox = {
    console, Math, JSON, Uint8Array,
    setTimeout: (fn, ms) => { timers.push([fn, ms]); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1][0] = null; },
    location: { protocol: 'http:', host: 'localhost:1' },
    document: { getElementById: (id) => (id === 'c' ? canvas : status) },
    window: { innerWidth: 800, innerHeight: 600, addEventListener: () => {} },
  };
  sandbox.WebSocket = function () {
    sandbox.__sock = this;
    this.readyState = 1;
    this.sent = [];
    this.send = (m) => this.sent.push(m);
  };
  sandbox.WebSocket.OPEN = 1;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const sock = sandbox.__sock;
  assert.ok(sock, 'the page script did not construct a WebSocket');
  const fireTimers = () => {
    for (const t of timers.splice(0)) if (t[0]) t[0]();
  };
  const gestures = () => sock.sent
    .filter((m) => typeof m === 'string')
    .map((m) => JSON.parse(m));
  return { canvas, sock, fireTimers, gestures };
}

test('a single click sends tap count 1 after the delay window', () => {
  const { canvas, fireTimers, gestures } = run();
  canvas.onclick({ offsetX: 100, offsetY: 100 });
  assert.deepStrictEqual(gestures(), []);       // held for the dblclick window
  fireTimers();
  assert.deepStrictEqual(gestures(), [{ type: 'tap', count: 1 }]);
});

test('a double click sends exactly one tap count 2', () => {
  const { canvas, fireTimers, gestures } = run();
  canvas.onclick({ offsetX: 100, offsetY: 100 });
  canvas.onclick({ offsetX: 100, offsetY: 100 });
  canvas.ondblclick({ offsetX: 100, offsetY: 100 });
  fireTimers();
  assert.deepStrictEqual(gestures(), [{ type: 'tap', count: 2 }]);
});

test('a drag maps canvas x onto gamma in [-90, 90]', () => {
  const { canvas, gestures } = run();
  canvas.onpointerdown({ offsetX: 160, offsetY: 100 });
  canvas.onpointermove({ offsetX: 320, offsetY: 100 });  // right edge
  canvas.onpointerup({ offsetX: 320, offsetY: 100 });
  const tilts = gestures().filter((g) => g.type === 'tilt');
  assert.ok(tilts.length >= 1);
  assert.strictEqual(tilts[tilts.length - 1].gamma, 90);
});

test('drag tilts are rate-bounded to one per 50 ms', () => {
  const { canvas, gestures } = run();
  canvas.onpointerdown({ offsetX: 0, offsetY: 100 });
  for (let x = 0; x <= 320; x += 8) canvas.onpointermove({ offsetX: x, offsetY: 100 });
  canvas.onpointerup({ offsetX: 320, offsetY: 100 });
  const tilts = gestures().filter((g) => g.type === 'tilt');
  // Date.now() does not advance inside one test run, so the throttle
  // admits only the first move (plus the final pointerup flush).
  assert.ok(tilts.length <= 2, `expected <= 2 tilts, got ${tilts.length}`);
});

test('a real drag suppresses the click that follows it', () => {
  const { canvas, fireTimers, gestures } = run();
  canvas.onpointerdown({ offsetX: 100, offsetY: 100 });
  canvas.onpointermove({ offsetX: 200, offsetY: 100 });
  canvas.onpointerup({ offsetX: 200, offsetY: 100 });
  canvas.onclick({ offsetX: 200, offsetY: 100 });        // browsers still fire it
  fireTimers();
  assert.deepStrictEqual(gestures().filter((g) => g.type === 'tap'), []);
});

test('nothing is sent when the socket is not open', () => {
  const { canvas, sock, fireTimers, gestures } = run();
  sock.readyState = 3;                                    // CLOSED
  canvas.onclick({ offsetX: 100, offsetY: 100 });
  fireTimers();
  assert.deepStrictEqual(gestures(), []);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log('ok', name); }
  catch (err) { failed += 1; console.error('FAIL', name); console.error(err); }
}
process.exit(failed ? 1 : 0);
```

Create `tests/backends/test_websim_input_page.py`:

```python
"""The page's input handlers, tested as behavior rather than as text.

Same wrapper shape as test_websim_layout.py: extract the <script> body
from PAGE_HTML so the served page stays the single source of truth."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from luxaeterna.backends.websim import PAGE_HTML

RUNNER = Path(__file__).resolve().parents[1] / "js" / "websim_input.test.js"


def _page_script() -> str:
    match = re.search(r"<script>(.*)</script>", PAGE_HTML, re.S)
    assert match, "PAGE_HTML no longer contains a single <script> block"
    return match.group(1)


def test_page_declares_input_handlers():
    script = _page_script()
    assert "onpointerdown" in script
    assert "ondblclick" in script


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_input_behaviour():
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(_page_script())
        script_path = handle.name
    try:
        proc = subprocess.run(
            ["node", str(RUNNER), script_path],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/chris/projects/luxaeterna && .venv/bin/python -m pytest tests/backends/test_websim_input_page.py -q`
Expected: FAIL (`onpointerdown` not in script; behavior test fails on missing handlers).

- [ ] **Step 3: Implement the page handlers**

In `PAGE_HTML`'s `<script>` block, append after the `draw` function (before `</script>`), using handler **property assignment** (`cv.onclick = ...`), not `addEventListener` — the layout test's canvas stub has no `addEventListener`, and properties assign cleanly on any object:

```js
/* --- operator input: gestures sent back over the same socket --------- */
const TAP_DELAY_MS=250,TILT_MIN_MS=50,DRAG_PX=5;
let tapTimer=null,dragging=false,dragMoved=false,lastTiltMs=0,dragX0=0;
function sendGesture(g){
  if(ws.readyState!==1)return;
  ws.send(JSON.stringify(g));
  st.textContent='sent '+g.type+(g.type==='tap'?' x'+g.count:' '+g.gamma.toFixed(0)+'°');
}
function dragGamma(x){
  const w=cv.clientWidth||cv.width;
  const g=(x/Math.max(1,w))*180-90;
  return Math.max(-90,Math.min(90,g));
}
cv.onclick=(e)=>{
  if(dragMoved){dragMoved=false;return;}
  if(tapTimer!==null)return;                      // second click of a pair
  tapTimer=setTimeout(()=>{tapTimer=null;sendGesture({type:'tap',count:1});},TAP_DELAY_MS);
};
cv.ondblclick=(e)=>{
  if(tapTimer!==null){clearTimeout(tapTimer);tapTimer=null;}
  sendGesture({type:'tap',count:2});
};
cv.onpointerdown=(e)=>{dragging=true;dragMoved=false;dragX0=e.offsetX;lastTiltMs=0;};
cv.onpointermove=(e)=>{
  if(!dragging)return;
  if(Math.abs(e.offsetX-dragX0)>DRAG_PX)dragMoved=true;
  if(!dragMoved)return;
  const now=Date.now();
  if(now-lastTiltMs<TILT_MIN_MS)return;
  lastTiltMs=now;
  sendGesture({type:'tilt',gamma:dragGamma(e.offsetX)});
};
function endDrag(e){
  if(!dragging)return;
  dragging=false;
  if(dragMoved)sendGesture({type:'tilt',gamma:dragGamma(e.offsetX)});
}
cv.onpointerup=endDrag;
cv.onpointerleave=endDrag;
```

Note the rate-bound test's expectation: `Date.now()` is frozen inside one vm run, so after the first admitted tilt every subsequent move is throttled; only `pointerup`'s flush adds one more. Also add `Date` to the test sandbox if the vm context lacks it — `Date` is a JS builtin available in the vm by default via the context's globals; if the run errors on it, add `Date` to the sandbox object.

- [ ] **Step 4: Run the JS/page tests, then both full suites**

Run: `cd /Users/chris/projects/luxaeterna && .venv/bin/python -m pytest tests/backends/test_websim_input_page.py tests/backends/test_websim_layout.py tests/backends/test_websim.py -q && .venv/bin/python -m pytest tests -q`
Expected: PASS, including the pre-existing layout tests (proves the new handlers don't break script load under the older, sparser stub).
Then: `cd /Users/chris/projects/mm-terrarium/.claude/worktrees/weekly-summary-three-projects-043f9e && .venv/bin/python -m pytest tests -q`
Expected: 1099 passed, 1 skipped (luxaeterna is editable-installed; this proves no cross-repo breakage).

- [ ] **Step 5: Commit**

```bash
cd /Users/chris/projects/luxaeterna && git add luxaeterna/backends/websim.py tests/js/websim_input.test.js tests/backends/test_websim_input_page.py && git commit -m "feat(websim): page gesture handlers, tap and drag-tilt over the socket"
```

---

### Task 3: `ShroomClient.tap()` encoder (mm-terrarium)

**Files:**
- Modify: `harness/shroom_client.py` (after `tilt()`, ~line 122)
- Test: `tests/test_shroom_client.py` (append)

**Interfaces:**
- Produces: `ShroomClient.tap(peak_g: float = 1.0, duration_ms: float = 50.0, count: int = 1) -> dict`, encoding the documented `up /game/tap sffi [dev, peak_g, duration_ms, count]` row. Socket-free, mirrors `tilt()`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_shroom_client.py`, matching its existing style — read the file's `tilt` encoder test first and mirror it):

```python
def test_tap_encodes_the_documented_wire_row():
    client = ShroomClient("ie1", "node-a")
    msg = client.tap(count=2)
    assert msg["address"] == "/game/tap"
    assert msg["typespec"] == "sffi"
    assert msg["args"] == ["ie1", 1.0, 50.0, 2]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_shroom_client.py -q`
Expected: FAIL (`AttributeError: tap`).

- [ ] **Step 3: Implement** (in `harness/shroom_client.py`, directly after `tilt()`):

```python
    def tap(self, peak_g: float = 1.0, duration_ms: float = 50.0,
            count: int = 1) -> dict:
        """The documented tap row. Defaults are the simulator's honest
        placeholders for values a mouse cannot measure; count is real."""
        return self._up("tap", "sffi",
                        [self.dev, float(peak_g), float(duration_ms),
                         int(count)])
```

Also update the module docstring's line "this client sends tilt only" to "this client sends tilt and tap".

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_shroom_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/shroom_client.py tests/test_shroom_client.py && git commit -m "feat(shroom_client): tap encoder for the documented wire row"
```

---

### Task 4: Operator input bridge in `o2_shroom` (mm-terrarium)

**Files:**
- Modify: `harness/o2_shroom.py`
- Test: `tests/test_o2_shroom_input.py` (create)

**Interfaces:**
- Consumes: Task 1's `WebSimBackend(on_input=...)`; Task 2's `{"type","count"/"gamma"}` message shapes.
- Produces, all module-level in `harness/o2_shroom.py`:
  - `SWEEP_RESUME_SECONDS = 5.0`
  - `INPUT_QUEUE_MAX = 64`
  - `enqueue_input(q: queue.Queue, msg: dict) -> None` — non-blocking, drops the OLDEST entry when full.
  - `drain_gestures(q: queue.Queue, send, dev: str, now: float) -> float | None` — drains everything queued; for each valid message calls `send(address, now, typespec, *args)` (the `o2lite.send` signature); returns `now` if at least one tilt was sent, else `None`. Invalid messages are dropped with a `print` diagnostic at most once per drain.
  - `build(..., input_queue: queue.Queue | None = None)` — when given, the backend is constructed with `on_input=lambda msg: enqueue_input(input_queue, msg)`. Return shape `(client, backend)` unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_o2_shroom_input.py`:

```python
"""The operator-input bridge: browser gestures -> /game/* sends.

Everything here is socket-free: drain_gestures takes an injected send
and an injected now, so the o2lite stamp-at-source rule (Design Rule 4)
is asserted without o2litepy present."""

from __future__ import annotations

import queue

from harness.o2_shroom import (INPUT_QUEUE_MAX, SWEEP_RESUME_SECONDS,
                               drain_gestures, enqueue_input)


def _q(*msgs):
    q = queue.Queue(maxsize=INPUT_QUEUE_MAX)
    for m in msgs:
        enqueue_input(q, m)
    return q


def test_tap_maps_to_the_documented_wire_row_stamped_at_now():
    sent = []
    got = drain_gestures(_q({"type": "tap", "count": 2}),
                         lambda *a: sent.append(a), "ie1", now=12.5)
    assert sent == [("/game/tap", 12.5, "sffi", "ie1", 1.0, 50.0, 2)]
    assert got is None                    # a tap is not a tilt


def test_tilt_maps_clamped_and_reports_operator_tilt_time():
    sent = []
    got = drain_gestures(_q({"type": "tilt", "gamma": 120.0}),
                         lambda *a: sent.append(a), "ie1", now=3.0)
    assert sent == [("/game/tilt", 3.0, "sf", "ie1", 90.0)]
    assert got == 3.0


def test_tap_count_defaults_to_1_and_is_clamped_to_at_least_1():
    sent = []
    drain_gestures(_q({"type": "tap"}, {"type": "tap", "count": 0}),
                   lambda *a: sent.append(a), "ie1", now=1.0)
    assert [s[6] for s in sent] == [1, 1]


def test_unknown_type_and_bad_fields_are_dropped():
    sent = []
    got = drain_gestures(
        _q({"type": "shake"}, {"type": "tilt", "gamma": "sideways"},
           {"no_type": True}, {"type": "tilt"}),
        lambda *a: sent.append(a), "ie1", now=1.0)
    assert sent == []
    assert got is None


def test_drain_empties_the_queue():
    q = _q({"type": "tap", "count": 1})
    drain_gestures(q, lambda *a: None, "ie1", now=1.0)
    assert q.empty()


def test_enqueue_drops_oldest_on_overflow():
    q = queue.Queue(maxsize=2)
    enqueue_input(q, {"type": "tap", "count": 1})
    enqueue_input(q, {"type": "tap", "count": 2})
    enqueue_input(q, {"type": "tap", "count": 3})
    sent = []
    drain_gestures(q, lambda *a: sent.append(a), "ie1", now=1.0)
    assert [s[6] for s in sent] == [2, 3]


def test_build_wires_the_queue_into_the_backend():
    from harness.o2_shroom import build
    q = queue.Queue(maxsize=INPUT_QUEUE_MAX)
    client, backend = build("ie1", serve=False, input_queue=q)
    try:
        assert backend.on_input is not None
        backend.on_input({"type": "tap", "count": 1})
        assert q.get_nowait() == {"type": "tap", "count": 1}
    finally:
        backend.close()


def test_build_without_queue_leaves_the_backend_input_free():
    from harness.o2_shroom import build
    client, backend = build("ie1", serve=False)
    try:
        assert backend.on_input is None
    finally:
        backend.close()


def test_sweep_resume_window_is_five_seconds():
    assert SWEEP_RESUME_SECONDS == 5.0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom_input.py -q`
Expected: FAIL (ImportError on the new names).

- [ ] **Step 3: Implement the bridge functions**

In `harness/o2_shroom.py`, add `import queue` up top with the stdlib imports, and after `tilt_sweep`:

```python
# Seconds after the operator's last drag-tilt before the synthetic sweep
# resumes. Long enough that hue does not snap back mid-exploration, short
# enough that an unattended run still animates.
SWEEP_RESUME_SECONDS = 5.0

# Bound on browser gestures queued between ticks. Generous: the page
# rate-bounds tilts to 20 Hz and the loop drains every ~5 ms.
INPUT_QUEUE_MAX = 64


def enqueue_input(q: "queue.Queue", msg: dict) -> None:
    """Queue one browser gesture, dropping the OLDEST on overflow.

    Runs on WebSimBackend's websocket handler thread, so it must never
    block; drop-oldest keeps the freshest gestures, matching the
    drop-not-queue rule frame relay already follows elsewhere."""
    while True:
        try:
            q.put_nowait(msg)
            return
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass


def drain_gestures(q: "queue.Queue", send, dev: str, now: float):
    """Translate every queued browser gesture into a /game/* send.

    `send` has o2lite.send's signature: send(address, time, typespec,
    *args). Gestures are stamped `now` -- the caller's o2lite clock
    reading -- because the whole simulator process is the device, so
    this IS the source stamp (Design Rule 4); the browser hop happened
    inside the device. Returns `now` if any tilt went out (the caller
    suspends its synthetic sweep against it), else None. Malformed
    entries are dropped with one diagnostic per drain, mirroring the
    engine's drop-this-frame rule."""
    tilted = None
    complained = False
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            return tilted
        kind = msg.get("type") if isinstance(msg, dict) else None
        try:
            if kind == "tap":
                count = max(1, int(msg.get("count", 1)))
                send("/game/tap", now, "sffi", dev, 1.0, 50.0, count)
            elif kind == "tilt":
                gamma = max(-90.0, min(90.0, float(msg["gamma"])))
                send("/game/tilt", now, "sf", dev, gamma)
                tilted = now
            else:
                raise ValueError(kind)
        except (KeyError, TypeError, ValueError):
            if not complained:
                print(f"dropping operator gesture {msg!r}")
                complained = True
```

- [ ] **Step 4: Wire `build()`**

Extend `build()`'s signature with `input_queue=None` (after `fixture`), document it in the docstring ("input_queue, when given, receives every gesture the browser page sends back; see drain_gestures"), and pass the callback into the backend:

```python
    on_input = (None if input_queue is None
                else lambda msg: enqueue_input(input_queue, msg))
    backend = WebSimBackend(capability=capability,
                            host=sim_host, port=sim_port, serve=serve,
                            label=dev, on_input=on_input)
```

- [ ] **Step 5: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom_input.py -q`
Expected: PASS.

- [ ] **Step 6: Wire `main()`**

In `main()`: create the queue and pass it to `build()`:

```python
    operator_input = queue.Queue(maxsize=INPUT_QUEUE_MAX)
    client, backend = build(args.dev, args.node,
                            args.sim_host, args.sim_port,
                            room_type=args.room_type, fixture=args.fixture,
                            input_queue=operator_input)
```

In the tick loop, inside the existing `if not args.no_join and _gestures_ready(client):` block, drain before the sweep and gate the sweep on the resume window. Add `last_operator_tilt = None` next to `next_tilt = None`, then replace the block's body:

```python
            if not args.no_join and _gestures_ready(client):
                if next_tilt is None:
                    next_tilt = now       # first tilt fires now the role is in
                    print(f"{markers.DEVICE_ROLE_GRANTED} {joins_sent} "
                          f"join(s); gestures starting at {now:.3f}", flush=True)
                operator = drain_gestures(operator_input, o2lite.send,
                                          args.dev, now)
                if operator is not None:
                    last_operator_tilt = operator
                sweeping = (last_operator_tilt is None
                            or now - last_operator_tilt >= SWEEP_RESUME_SECONDS)
                if now >= next_tilt:
                    if sweeping:
                        gamma = tilt_sweep(now - start)
                        # Timestamps at the source (Design Rule 4): the
                        # device's own synced clock reading, not Control's
                        # receipt time.
                        o2lite.send("/game/tilt", now, "sf", args.dev, gamma)
                    # Advance even while suspended, so the sweep resumes on
                    # schedule instead of firing a backlog of overdue tilts.
                    next_tilt += interval
```

(`--no-join` runs get the queue too, but this block never executes for them, which is the existing no-gestures rule for the Room path.)

- [ ] **Step 7: Full mm-terrarium suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 1099 + new passed, 1 skipped, zero failures.

- [ ] **Step 8: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom_input.py && git commit -m "feat(o2_shroom): browser gestures ride the tick loop onto /game/*"
```

---

### Task 5: Both suites green, live verify, PRs

**Files:**
- None new (verification and delivery).

- [ ] **Step 1: Run both full suites**

```bash
cd /Users/chris/projects/luxaeterna && .venv/bin/python -m pytest tests -q
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/weekly-summary-three-projects-043f9e && .venv/bin/python -m pytest tests -q
```
Expected: both at baseline plus the new tests, zero failures.

- [ ] **Step 2: Live verify against a real Arco (best-effort in this session)**

With `PYTHONPATH=/Users/chris/projects/arco`, bring the stack up held:

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.run_stack --devices 1 --setup-seconds 30
```

Then (headless session) drive the device page's websocket directly to stand in for the browser: connect to the device's WebSim port and send `{"type":"tap","count":1}` and a few `{"type":"tilt","gamma":...}` messages; confirm on Control's stdout/Console that `/game/tap` fired the `flash_device` trigger and tilt moved cc:74. If the known headless clock-sync defect blocks the run, record that verification is deferred to an interactive run and say so plainly in the PR body (the deep-dive documents this exact limitation).

- [ ] **Step 3: PRs**

luxaeterna first (mm-terrarium's live path depends on it):

```bash
cd /Users/chris/projects/luxaeterna && git push -u origin claude/websim-input && gh pr create --fill
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/weekly-summary-three-projects-043f9e && git push -u origin claude/tuneshroom-two-way-input && gh pr create --base main --fill
```

Note in the mm-terrarium PR body that it depends on the luxaeterna PR and that this branch is based on `claude/operator-harness-handoff` (PR #39's content, which never reached main), so its diff includes that work until the handoff branch merges.
