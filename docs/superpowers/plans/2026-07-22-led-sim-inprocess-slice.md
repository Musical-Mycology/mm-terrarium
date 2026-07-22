# In-process LED Simulator Slice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one simple Bit drive a watchable, faithfully-rendered LED display end-to-end in a single Python process — proving the light-manifest-v2 blob that mm-terrarium composes actually parses and renders in luxaeterna — with no o2lite wire.

**Architecture:** Two repos, two PRs. **Phase A (luxaeterna):** add a `WebSimBackend(DMXBackend)` that records DMX frames and streams them to a self-contained browser canvas, plus a tiny `LightSession.feed_midi(...)` seam for injecting MIDI without a live device, plus a `python -m luxaeterna.websim_demo` viewer. **Phase B (mm-terrarium):** rename TestBit's bad `base_hue` param to `hue`, add a `harness/` package whose `DeviceBridge` turns a granted `JoinResult.config` blob into a luxaeterna `LightSession`, and land a runnable demo (`harness/led_smoke.py`) plus a headless integration regression (`tests/test_led_smoke.py`). Phase B dev-depends on Phase A.

**Tech Stack:** Python 3.10+, luxaeterna (numpy render engine + DMX `Universe`), `websockets>=13` (optional `websim` extra), pytest.

## Global Constraints

- **luxaeterna branch base:** all Phase A work branches from **`origin/main`** (canonical merged v2), branch name `claude/websim-led-simulator`. The luxaeterna *working tree is checked out at stale v1* — a fetch + fresh branch off `origin/main` is the first step. Never edit the v1 working-tree files in place.
- **Omit `midi_capacity`:** `origin/main` advanced to `3e5f2e9` (PR #5 merged the `bounded-midi-drain` fast-follow *after* this plan was drafted), which adds a **defaulted** `midi_capacity: int = 256` to `LightSession`/`build_session`. Our code simply **omits** it — every `build_session(...)` call passes **only** `clock=` and relies on the default. Do not add or thread `midi_capacity`. (The captured signatures in this plan already match `3e5f2e9`; branch off current `origin/main`.)
- **`websockets` is an optional extra in luxaeterna.** `luxaeterna/backends/websim.py` MUST import `websockets` **lazily** (inside methods, never at module top) so `from luxaeterna.backends.websim import WebSimBackend` and record-only mode work without the extra installed.
- **Bloom's only param is `hue`** (0–1 HSV). `_make_bloom` raises `KeyError` on any other param; `binding.resolve` raises `ValueError` on an unknown cc-lane `dest`. TestBit must use `hue`, never `base_hue`.
- **MIDI wire packing:** one int32 = `(status << 16) | (data1 << 8) | data2`. CC and velocity are normalized to 0–1 (`/127.0`) inside luxaeterna's `dispatch_midi`; callers pass raw 0–127.
- **DMX color order for the Shroom is GRB:** per pixel the frame bytes are `[G, R, B]`. `shroom_capability()` = 12 px (ring 0–7, stem 8–11).
- **Bloom captures color at note-on:** a cc change to `hue` only recolors *subsequently* triggered voices. Any "hue sweep" must interleave note-ons across the cc ramp.
- **mm-terrarium tests run from the repo root** (`python -m pytest tests -v`); repo root is on `sys.path` via CWD, so `import control`, `import bits`, `import harness` all resolve. luxaeterna tests run from the luxaeterna repo root (`pythonpath = ["."]`).
- **Spec:** `docs/superpowers/specs/2026-07-22-led-sim-inprocess-slice-design.md`.

---

## Phase A — luxaeterna (repo `/Users/chris/projects/luxaeterna`, branch `claude/websim-led-simulator` off `origin/main`)

> Every Phase A task runs `cd /Users/chris/projects/luxaeterna` first. Tests run there with `python -m pytest tests -v`.

### Task A0 (setup, folded into A1): create the branch

- [ ] **Step 1: Fetch and branch off `origin/main`**

```bash
cd /Users/chris/projects/luxaeterna
git fetch origin
git checkout -b claude/websim-led-simulator origin/main
git log --oneline -1   # expect a0ab277 (Merge PR #4 ... light-manifest v2)
```

Expected: HEAD is the v2 merge commit; the working tree now has `luxaeterna/synth/session.py`, `luxaeterna/synth/presets.py`, `luxaeterna/backends/base.py` present in their v2 form.

---

### Task A1: `LightSession.feed_midi` injection seam

**Files:**
- Modify: `luxaeterna/synth/session.py` (add one method to `LightSession`)
- Test: `tests/synth/test_session_feed_midi.py`

**Interfaces:**
- Consumes: `LightSession._bridge.on_midi(packed: int)` (existing), `decode_midi`/`dispatch_midi` gating (existing — MIDI only dispatched in RUNNING).
- Produces: `LightSession.feed_midi(status: int, data1: int, data2: int) -> None` — packs a MIDI message and enqueues it exactly as an o2lite packet would arrive. Consumed by Phase B's demo and the luxaeterna demo.

- [ ] **Step 1: Write the failing test**

Create `tests/synth/test_session_feed_midi.py`:

```python
"""feed_midi injects MIDI as if it arrived over o2lite — for device sims/tests
with no live o2lite client. It reaches instruments only while RUNNING."""

from __future__ import annotations

from luxaeterna.synth.capability import shroom_capability
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session
from luxaeterna.universe import Universe

MANIFEST = {
    "instruments": [{
        "instrument": "bloom", "target": "primary", "params": {"hue": 1.0 / 3.0},
        "lanes": [{"source": "note", "dest": "trigger"},
                  {"source": "cc:74", "dest": "hue"}],
    }]
}


def _to_running(session, uni):
    for _ in range(200):
        session.render_into(uni)
        if session.state == "running":
            return
    raise AssertionError("session never reached RUNNING")


def test_feed_midi_note_lights_the_instrument_when_running():
    clk = iter([i * (1 / 44) for i in range(400)]).__next__
    session = build_session(LightManifest.from_dict(MANIFEST),
                            shroom_capability(), clock=clk)
    uni = Universe()
    _to_running(session, uni)
    session.render_into(uni)
    assert max(uni.get_frame()[:36]) == 0          # dark before any note

    session.feed_midi(0x90, 60, 100)               # note-on
    session.render_into(uni)
    assert max(uni.get_frame()[:36]) > 0           # lit after feed_midi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/synth/test_session_feed_midi.py -v`
Expected: FAIL with `AttributeError: 'LightSession' object has no attribute 'feed_midi'`.

- [ ] **Step 3: Add `feed_midi` to `LightSession`**

In `luxaeterna/synth/session.py`, add this method to `LightSession` (place it right after `attach`, in the "wiring" section):

```python
    def feed_midi(self, status: int, data1: int, data2: int) -> None:
        """Inject a MIDI message as if it arrived over o2lite — for device
        simulators and tests that have no live o2lite client. Packed and
        enqueued exactly like a real packet, so it is gated to RUNNING and
        drained on the render thread like any other MIDI event."""
        packed = ((status & 0xFF) << 16) | ((data1 & 0xFF) << 8) | (data2 & 0xFF)
        self._bridge.on_midi(packed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/synth/test_session_feed_midi.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full luxaeterna suite (no regressions)**

Run: `python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add luxaeterna/synth/session.py tests/synth/test_session_feed_midi.py
git commit -m "feat(synth): LightSession.feed_midi — inject MIDI without a live o2lite client"
```

---

### Task A2: `WebSimBackend` record-only core + capability message

**Files:**
- Create: `luxaeterna/backends/websim.py`
- Test: `tests/backends/test_websim.py`

**Interfaces:**
- Consumes: `DMXBackend` ABC (`open`/`close`/`send(frame, universe_id=0)`/`is_open`), `SurfaceCapability` (`surface_id`, `pixel_count`, `color_order`, `zones` of `Zone(name,start,count)`), `shroom_capability()`.
- Produces:
  - `capability_message(cap) -> dict` — module-level; the connect-time handshake payload.
  - `WebSimBackend(capability=None, host="127.0.0.1", port=0, serve=True)` with `.frames: list[bytes]` (each = the first `pixel_count*3` bytes of a sent frame), `.open()`, `.close()`, `.send(frame, universe_id=0)`, `.is_open`, `.port`. Consumed by Task A3 (adds serving), the demo, and Phase B.

- [ ] **Step 1: Write the failing test**

Create `tests/backends/test_websim.py`:

```python
"""WebSimBackend: records DMX frames (record-only mode) and describes the
surface to browser clients via a capability handshake."""

from __future__ import annotations

from luxaeterna.backends.websim import WebSimBackend, capability_message
from luxaeterna.synth.capability import shroom_capability


def test_capability_message_describes_the_shroom():
    msg = capability_message(shroom_capability())
    assert msg["type"] == "capability"
    assert msg["pixel_count"] == 12
    assert msg["color_order"] == "GRB"
    assert {"name": "ring", "start": 0, "count": 8} in msg["zones"]
    assert {"name": "stem", "start": 8, "count": 4} in msg["zones"]


def test_record_only_backend_records_pixel_slice_without_serving():
    b = WebSimBackend(capability=shroom_capability(), serve=False)
    assert b.is_open is False
    b.open()
    assert b.is_open is True
    frame = bytearray(range(36)) + bytearray(512 - 36)   # 12 px * 3 = 36
    b.send(frame)
    assert len(b.frames) == 1
    assert b.frames[0] == bytes(range(36))               # sliced to pixel_count*3
    b.close()
    assert b.is_open is False


def test_send_does_not_mutate_frame():
    b = WebSimBackend(capability=shroom_capability(), serve=False)
    b.open()
    frame = bytearray([7]) * 512
    b.send(frame)
    assert frame == bytearray([7]) * 512
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backends/test_websim.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'luxaeterna.backends.websim'`.

- [ ] **Step 3: Create the record-only backend**

Create `luxaeterna/backends/websim.py`:

```python
"""Lux Aeterna — WebSimBackend: a DMX backend that records frames and (when
serving) streams them to a self-contained browser canvas — an on-screen LED
simulator for the canonical Shroom. websockets is imported lazily so record-only
mode and this import work without the optional 'websim' extra installed."""

from __future__ import annotations

import json
import threading

from .base import DMXBackend
from ..synth.capability import SurfaceCapability, shroom_capability


def capability_message(cap: SurfaceCapability) -> dict:
    """The connect-time handshake: enough geometry for a browser to lay out and
    color the pixels from raw DMX frames."""
    return {
        "type": "capability",
        "surface_id": cap.surface_id,
        "pixel_count": cap.pixel_count,
        "color_order": cap.color_order,
        "zones": [{"name": z.name, "start": z.start, "count": z.count}
                  for z in cap.zones],
    }


class WebSimBackend(DMXBackend):
    def __init__(self, capability: SurfaceCapability | None = None,
                 host: str = "127.0.0.1", port: int = 0,
                 serve: bool = True) -> None:
        self._cap = capability or shroom_capability()
        self._n = self._cap.pixel_count * 3          # bytes we care about
        self._host = host
        self._port = port
        self._serve = serve
        self.frames: list[bytes] = []
        self._open = False
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._clients: set = set()

    # --- DMXBackend ---------------------------------------------------------
    def open(self) -> None:
        self._open = True                            # serving added in Task A3

    def close(self) -> None:
        self._open = False

    def send(self, frame, universe_id: int = 0) -> None:
        payload = bytes(frame[:self._n])             # copy; never mutate frame
        self.frames.append(payload)

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def port(self) -> int:
        return self._port
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backends/test_websim.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add luxaeterna/backends/websim.py tests/backends/test_websim.py
git commit -m "feat(backends): WebSimBackend record-only core + capability handshake"
```

---

### Task A3: `WebSimBackend` serving — browser page + frame streaming + `websim` extra

**Files:**
- Modify: `luxaeterna/backends/websim.py` (serving path + embedded page)
- Modify: `pyproject.toml` (add `websim` extra; add `websockets` to `dev` and `all`)
- Test: `tests/backends/test_websim_serve.py`

**Interfaces:**
- Consumes: the record-only `WebSimBackend` from A2; `websockets.sync.server.serve`, `websockets.datastructures.Headers`, `websockets.http11.Response` (lazily imported).
- Produces: a live server — `open()` binds `host:port` (port 0 → ephemeral, read back via `.port`), serves `PAGE_HTML` on `GET /`, upgrades `/ws`, sends `capability_message` on connect, and broadcasts each sent frame's pixel slice as a binary message. Dead clients are dropped without blocking.

- [ ] **Step 1: Write the failing test**

Create `tests/backends/test_websim_serve.py`:

```python
"""WebSimBackend serving: a real websocket client receives the capability
handshake, then each sent frame as a binary message."""

from __future__ import annotations

import json

import pytest

from luxaeterna.backends.websim import WebSimBackend, PAGE_HTML
from luxaeterna.synth.capability import shroom_capability


def test_page_is_self_contained_canvas():
    lower = PAGE_HTML.lower()
    assert "<canvas" in lower
    assert "websocket" in lower          # connects itself, no external libs
    assert "http" not in lower.split("</style>")[0] or "cdn" not in lower


def test_client_receives_capability_then_frame():
    pytest.importorskip("websockets")
    from websockets.sync.client import connect

    b = WebSimBackend(capability=shroom_capability(), host="127.0.0.1", port=0)
    b.open()
    try:
        with connect(f"ws://127.0.0.1:{b.port}/ws") as c:
            cap = json.loads(c.recv())               # capability arrives first
            assert cap["type"] == "capability"
            assert cap["pixel_count"] == 12
            b.send(bytearray(range(36)) + bytearray(512 - 36))
            frame = c.recv()                         # binary frame follows
            assert bytes(frame) == bytes(range(36))
    finally:
        b.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backends/test_websim_serve.py -v`
Expected: FAIL with `ImportError: cannot import name 'PAGE_HTML'`.

- [ ] **Step 3: Add the embedded page + serving path**

In `luxaeterna/backends/websim.py`, add the page constant near the top (after the imports):

```python
PAGE_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Lux Aeterna — Shroom LED Simulator</title>
<style>
 body{background:#0b0b0f;margin:0;display:flex;height:100vh;
      align-items:center;justify-content:center}
 canvas{background:#0b0b0f}
 #s{position:fixed;top:8px;left:8px;color:#556;font:12px monospace}
</style></head><body>
<div id="s">connecting…</div><canvas id="c" width="320" height="420"></canvas>
<script>
const cv=document.getElementById('c'),cx=cv.getContext('2d'),st=document.getElementById('s');
let cap=null;
const ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
ws.binaryType='arraybuffer';
ws.onopen=()=>st.textContent='connected';
ws.onclose=()=>st.textContent='disconnected';
ws.onmessage=(e)=>{
  if(typeof e.data==='string'){cap=JSON.parse(e.data);st.textContent=cap.surface_id+' · '+cap.pixel_count+'px '+cap.color_order;return;}
  if(!cap)return; draw(new Uint8Array(e.data));
};
function pos(i){
  const ring=cap.zones.find(z=>z.name==='ring'),stem=cap.zones.find(z=>z.name==='stem');
  if(ring&&i>=ring.start&&i<ring.start+ring.count){
    const k=i-ring.start,a=-Math.PI/2+k*2*Math.PI/ring.count;
    return [160+90*Math.cos(a),150+90*Math.sin(a)];
  }
  if(stem&&i>=stem.start&&i<stem.start+stem.count){
    const k=i-stem.start;return [160,270+k*38];
  }
  return [40+i*24,380];
}
function rgb(f,i){
  const o=cap.color_order,b=[f[i*3],f[i*3+1],f[i*3+2]],m={};
  for(let j=0;j<3;j++)m[o[j]]=b[j];
  return 'rgb('+(m.R||0)+','+(m.G||0)+','+(m.B||0)+')';
}
function draw(f){
  cx.clearRect(0,0,cv.width,cv.height);
  for(let i=0;i<cap.pixel_count;i++){
    const [x,y]=pos(i),c=rgb(f,i);
    const g=cx.createRadialGradient(x,y,1,x,y,20);
    g.addColorStop(0,c);g.addColorStop(1,'rgba(0,0,0,0)');
    cx.fillStyle=g;cx.beginPath();cx.arc(x,y,20,0,2*Math.PI);cx.fill();
    cx.fillStyle=c;cx.beginPath();cx.arc(x,y,7,0,2*Math.PI);cx.fill();
  }
}
</script></body></html>"""
```

Then replace the `open`/`close`/`send` section and add the server helpers so the class reads:

```python
    # --- DMXBackend ---------------------------------------------------------
    def open(self) -> None:
        if self._open:
            return
        if self._serve:
            from websockets.sync.server import serve
            self._server = serve(self._handle, self._host, self._port,
                                 process_request=self._process_request)
            self._port = self._server.socket.getsockname()[1]
            self._thread = threading.Thread(target=self._server.serve_forever,
                                            daemon=True)
            self._thread.start()
        self._open = True

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._open = False

    def send(self, frame, universe_id: int = 0) -> None:
        payload = bytes(frame[:self._n])             # copy; never mutate frame
        self.frames.append(payload)
        if not self._serve:
            return
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            try:
                c.send(payload)
            except Exception:
                with self._lock:
                    self._clients.discard(c)

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def port(self) -> int:
        return self._port

    # --- server internals ---------------------------------------------------
    def _process_request(self, connection, request):
        if request.path == "/ws":
            return None                              # proceed to WS handshake
        from websockets.datastructures import Headers
        from websockets.http11 import Response
        body = PAGE_HTML.encode("utf-8")
        headers = Headers()
        headers["Content-Type"] = "text/html; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        return Response(200, "OK", headers, body)

    def _handle(self, connection) -> None:
        with self._lock:
            self._clients.add(connection)
        try:
            connection.send(json.dumps(capability_message(self._cap)))
            for _ in connection:                     # hold open until close
                pass
        except Exception:
            pass
        finally:
            with self._lock:
                self._clients.discard(connection)
```

(Delete the old minimal `open`/`close`/`send`/`is_open`/`port` block from Task A2 — this supersedes it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backends/test_websim_serve.py tests/backends/test_websim.py -v`
Expected: PASS. (If `websockets` is not installed, the serve test is skipped — install it: `python -m pip install "websockets>=13"`.)

- [ ] **Step 5: Add the `websim` extra to `pyproject.toml`**

In `pyproject.toml`, replace the `[project.optional-dependencies]` block with:

```toml
[project.optional-dependencies]
serial = ["pyserial>=3.5"]
websim = ["websockets>=13"]
all = ["pyserial>=3.5", "websockets>=13"]
dev = ["pytest>=7", "numpy>=1.24", "websockets>=13"]
```

- [ ] **Step 6: Run the full luxaeterna suite**

Run: `python -m pytest tests -q`
Expected: all pass (serve test included now that `websockets` is in `dev`).

- [ ] **Step 7: Commit**

```bash
git add luxaeterna/backends/websim.py tests/backends/test_websim_serve.py pyproject.toml
git commit -m "feat(backends): WebSimBackend serving — canvas page + frame streaming + websim extra"
```

---

### Task A4: `websim_demo` entry point + docs

**Files:**
- Create: `luxaeterna/websim_demo.py`
- Modify: `README.md` (add a WebSimBackend section)
- Test: `tests/backends/test_websim_demo.py`

**Interfaces:**
- Consumes: `build_session`, `shroom_capability`, `Universe`, `OutputLoop`, `WebSimBackend`, `LightSession.feed_midi`.
- Produces: `luxaeterna.websim_demo.build_demo() -> tuple[OutputLoop, LightSession]` (constructs the pipeline without starting it — the testable seam) and `main()` (`python -m luxaeterna.websim_demo`).

- [ ] **Step 1: Write the failing test**

Create `tests/backends/test_websim_demo.py`:

```python
"""The websim demo wires a canned bloom manifest into the full render pipeline."""

from __future__ import annotations

from luxaeterna import websim_demo


def test_build_demo_constructs_running_pipeline():
    loop, session = websim_demo.build_demo(serve=False)
    uni = loop.universe
    for _ in range(200):
        loop._loop_once()
        if session.state == "running":
            break
    assert session.state == "running"
    session.feed_midi(0x90, 60, 100)               # note-on via the demo seam
    loop._loop_once()
    assert max(uni.get_frame()[:36]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backends/test_websim_demo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'luxaeterna.websim_demo'`.

- [ ] **Step 3: Create the demo module**

Create `luxaeterna/websim_demo.py`:

```python
"""python -m luxaeterna.websim_demo — watch a canned bloom manifest render to
the Web LED simulator. Requires the 'websim' extra:  pip install luxaeterna[websim]
Then open the printed URL and watch the Shroom bloom and sweep hue."""

from __future__ import annotations

import time

from .backends.websim import WebSimBackend
from .output import OutputLoop
from .synth.capability import shroom_capability
from .synth.manifest import LightManifest
from .synth.session import build_session
from .universe import Universe

_MANIFEST = {
    "instruments": [{
        "instrument": "bloom", "target": "primary", "params": {"hue": 1.0 / 3.0},
        "lanes": [{"source": "note", "dest": "trigger"},
                  {"source": "cc:74", "dest": "hue"}],
    }],
    "welcome": {"instrument": "bloom", "params": {"hue": 1.0 / 3.0}, "duration": 1.5},
}


def build_demo(host: str = "127.0.0.1", port: int = 8770, serve: bool = True):
    """Construct the pipeline without starting the loop. Returns (loop, session)."""
    cap = shroom_capability()
    session = build_session(LightManifest.from_dict(_MANIFEST), cap)
    uni = Universe()
    backend = WebSimBackend(capability=cap, host=host, port=port, serve=serve)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    return loop, session


def main() -> None:
    loop, session = build_demo()
    loop.start()
    print(f"Watch the Shroom at http://127.0.0.1:{loop.backend.port}/  (Ctrl-C to stop)")
    try:
        while session.state != "running":
            time.sleep(0.02)
        cc = 0
        while True:
            session.feed_midi(0xB0, 74, cc)          # cc:74 -> hue
            session.feed_midi(0x90, 60, 100)         # new voice at current hue
            cc = (cc + 8) % 128
            time.sleep(0.15)
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backends/test_websim_demo.py -v`
Expected: PASS.

- [ ] **Step 5: Document in README**

Append to `README.md`:

```markdown
## Web LED Simulator (WebSimBackend)

`WebSimBackend` is a `DMXBackend` that records DMX frames and streams them to a
self-contained browser canvas — an on-screen simulator of the 12-LED Shroom
(8-ring + 4-stem, GRB). No hardware required.

Install the extra and run the demo:

    pip install luxaeterna[websim]
    python -m luxaeterna.websim_demo
    # open the printed http://127.0.0.1:8770/ and watch it bloom + sweep hue

To drive your own render, point an `OutputLoop` at it:

    backend = WebSimBackend(capability=shroom_capability())
    OutputLoop(universe, backend, on_frame=session.render_into, always_send=True).start()

In tests, construct with `serve=False` for a headless frame recorder (`.frames`).
```

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest tests -q`  (Expected: all pass.)

```bash
git add luxaeterna/websim_demo.py tests/backends/test_websim_demo.py README.md
git commit -m "feat(websim): python -m luxaeterna.websim_demo viewer + docs"
```

- [ ] **Step 7: Push the branch and open the luxaeterna PR**

```bash
git push -u origin claude/websim-led-simulator
gh pr create --repo Musical-Mycology/luxaeterna --fill
```

---

## Phase B — mm-terrarium (this repo/worktree, branch `claude/mm-tuneshroom-test-approach-11a21c`)

> Phase B dev-depends on Phase A. Before running Phase B tests, install luxaeterna editable from the Phase A branch:
>
> ```bash
> python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"
> ```
>
> (The luxaeterna checkout must be on `claude/websim-led-simulator` — the branch that has `WebSimBackend` + `feed_midi`.) `tests/test_led_smoke.py` and `tests/test_device_bridge.py` `importorskip` luxaeterna, so the core suite still passes without it.

### Task B1: rename `base_hue` → `hue` in TestBit and its tests

**Files:**
- Modify: `bits/test_bit.py` (3 occurrences)
- Modify: `tests/test_test_bit.py` (3 occurrences)
- Modify: `tests/test_role_config.py` (3 occurrences)

**Interfaces:**
- Produces: TestBit's `player` light_manifest and welcome use `hue` (luxaeterna's canonical bloom param). No signature changes.

- [ ] **Step 1: Confirm the current failing reality (optional sanity)**

Run: `grep -rn base_hue bits tests`
Expected: 9 hits across the three files.

- [ ] **Step 2: Replace `base_hue` with `hue` in all three files**

In `bits/test_bit.py`, change the `player` manifest and welcome so they read:

```python
            light_manifest={
                "instruments": [
                    {"instrument": "bloom", "target": "primary",
                     "params": {"hue": 0.33},
                     "lanes": [{"source": "note", "dest": "trigger"},
                               {"source": "cc:74", "dest": "hue"}]},
                ],
            },
            welcome={
                "light": {"instrument": "bloom",
                          "params": {"hue": 0.33}, "duration": 1.5},
                "audio": {"instrument": "chime", "duration": 1.5},
            },
```

In `tests/test_test_bit.py`, update the literal manifest the assertions compare against (lines ~77–84): the instrument `params` `{"base_hue": 0.33}` → `{"hue": 0.33}`, the cc lane `"dest": "base_hue"` → `"dest": "hue"`, and the welcome light `params` `{"base_hue": 0.33}` → `{"hue": 0.33}`.

In `tests/test_role_config.py`, make the same three substitutions in its fixture manifest (lines ~19–26).

Quick way to verify you got all of them:

```bash
grep -rn base_hue bits tests   # expect: no output
```

- [ ] **Step 3: Run the affected tests**

Run: `python -m pytest tests/test_test_bit.py tests/test_role_config.py -v`
Expected: PASS.

- [ ] **Step 4: Run the full mm-terrarium suite (no regressions)**

Run: `python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bits/test_bit.py tests/test_test_bit.py tests/test_role_config.py
git commit -m "fix(test-bit): bloom param is 'hue' not 'base_hue' (matches luxaeterna preset)"
```

---

### Task B2: `harness/` package + `DeviceBridge`

**Files:**
- Create: `harness/__init__.py` (empty package marker)
- Create: `harness/device_bridge.py`
- Test: `tests/test_device_bridge.py`

**Interfaces:**
- Consumes: `GameServer.join(dev, node) -> JoinResult` with `.granted` and `.config["light_manifest"]`; `GameServer.on_release` callback slot (called with `dev`); luxaeterna `LightManifest.from_dict`, `build_session`, `shroom_capability`.
- Produces: `DeviceBridge(capability=None, clock=time.monotonic)` with `.session`, `.on_grant(join_result) -> LightSession`, `.on_release(dev) -> None`. `on_grant` builds a session from the composed blob; `on_release` requests a `clear()` (device-side CLOSING fade).

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_bridge.py`:

```python
"""DeviceBridge: the in-process stand-in for a device consuming /ie<N>/role.
Turns a granted JoinResult's composed config blob into a luxaeterna session and
maps GameServer release -> session.clear()."""

from __future__ import annotations

import pytest

pytest.importorskip("luxaeterna.backends.websim")

from bits.test_bit import TestBit
from control.engine import GameServer
from harness.device_bridge import DeviceBridge


def _granted_join():
    gs = GameServer({"test_bit": TestBit})
    gs.load_bit("test_bit")
    res = gs.join("dev1", "TEST_PLAYER_NODE")
    assert res.granted
    return res


def test_on_grant_builds_a_session_that_lights_from_the_composed_blob():
    from luxaeterna.universe import Universe
    clk = iter([i * (1 / 44) for i in range(400)]).__next__
    bridge = DeviceBridge(clock=clk)
    session = bridge.on_grant(_granted_join())
    assert session is not None

    uni = Universe()
    for _ in range(200):
        session.render_into(uni)
        if session.state == "running":
            break
    assert session.state == "running"
    session.render_into(uni)
    assert max(uni.get_frame()[:36]) == 0                 # dark before note

    session.feed_midi(0xB0, 74, 0)                        # cc:74=0 -> hue red
    session.feed_midi(0x90, 60, 100)                      # note-on
    session.render_into(uni)
    frame = uni.get_frame()[:36]
    assert max(frame) > 0
    assert max(frame[1::3]) > max(frame[0::3])            # GRB: red > green


def test_on_release_requests_close():
    from luxaeterna.universe import Universe
    clk = iter([i * (1 / 44) for i in range(400)]).__next__
    bridge = DeviceBridge(clock=clk)
    session = bridge.on_grant(_granted_join())
    uni = Universe()
    for _ in range(200):
        session.render_into(uni)
        if session.state == "running":
            break
    bridge.on_release("dev1")
    session.render_into(uni)
    assert session.state == "closing"


def test_on_release_is_safe_before_any_grant():
    DeviceBridge().on_release("dev1")                     # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_device_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness'`.

- [ ] **Step 3: Create the package + bridge**

Create `harness/__init__.py`:

```python
"""In-process test harnesses for the Control+GameServer stack."""
```

Create `harness/device_bridge.py`:

```python
"""DeviceBridge: in-process stand-in for a device consuming /ie<N>/role.

Turns a granted JoinResult's composed light-manifest-v2 blob into a luxaeterna
LightSession (the device's local renderer), and maps GameServer release onto
session.clear() (the device-side CLOSING fade). This is the seam the real
o2lite transport will replace in Slice 2."""

from __future__ import annotations

import time

from luxaeterna.synth.capability import shroom_capability
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session


class DeviceBridge:
    def __init__(self, capability=None, clock=time.monotonic) -> None:
        self._cap = capability or shroom_capability()
        self._clock = clock
        self.session = None

    def on_grant(self, join_result):
        """Build the device's LightSession from the composed /ie<N>/role blob."""
        blob = join_result.config["light_manifest"]
        manifest = LightManifest.from_dict(blob)
        self.session = build_session(manifest, self._cap, clock=self._clock)
        return self.session

    def on_release(self, dev) -> None:
        """GameServer released this device -> ask the session to close/fade."""
        if self.session is not None:
            self.session.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_device_bridge.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest tests -q`  (Expected: all pass; device-bridge tests skip if luxaeterna absent.)

```bash
git add harness/__init__.py harness/device_bridge.py tests/test_device_bridge.py
git commit -m "feat(harness): DeviceBridge — composed role blob -> luxaeterna session"
```

---

### Task B3: `led_smoke` demo + `test_led_smoke` integration regression

**Files:**
- Create: `harness/led_smoke.py` (runnable demo)
- Create: `tests/test_led_smoke.py` (headless in-process full-stack regression)
- Modify: `requirements-dev.txt` (document the editable luxaeterna install)

**Interfaces:**
- Consumes: `GameServer`, `TestBit`, `DeviceBridge`, `WebSimBackend`, `OutputLoop`, `Universe`, `State`.
- Produces: `harness/led_smoke.py::main()` (`python -m harness.led_smoke`) and the regression test.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_led_smoke.py`:

```python
"""In-process full-stack regression: TestBit -> GameServer grant -> composed
light-manifest-v2 blob -> luxaeterna session -> OutputLoop -> WebSimBackend
recorder. Deterministic (fake clock, hand-driven ticks, no threads, no browser).
Asserts welcome -> dark-when-running -> note lights + hue routing -> fade."""

from __future__ import annotations

import pytest

pytest.importorskip("luxaeterna.backends.websim")

from bits.test_bit import TestBit
from control.engine import GameServer
from control.state import State
from harness.device_bridge import DeviceBridge
from luxaeterna.backends.websim import WebSimBackend
from luxaeterna.output import OutputLoop
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.universe import Universe


def test_full_inprocess_stack_lights_and_fades():
    gs = GameServer({"test_bit": TestBit})
    clk = iter([i * (1 / 44) for i in range(3000)]).__next__
    bridge = DeviceBridge(capability=shroom_capability(), clock=clk)
    gs.on_release = bridge.on_release

    gs.load_bit("test_bit")
    res = gs.join("dev1", "TEST_PLAYER_NODE")
    assert res.granted
    session = bridge.on_grant(res)

    uni = Universe()
    backend = WebSimBackend(capability=shroom_capability(), serve=False)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    backend.open()

    # (a) welcome signature visible during LOADING
    loop._loop_once()
    assert session.state == "loading"
    assert max(backend.frames[-1]) > 0

    # ride the welcome out to RUNNING
    for _ in range(200):
        loop._loop_once()
        if session.state == "running":
            break
    assert session.state == "running"

    # (b) dark before any note
    loop._loop_once()
    assert max(backend.frames[-1]) == 0

    # (c) cc:74=0 -> hue red; note-on -> lit + red-dominant (GRB: byte1 red, byte0 green)
    session.feed_midi(0xB0, 74, 0)
    session.feed_midi(0x90, 60, 100)
    loop._loop_once()
    frame = backend.frames[-1]
    assert max(frame) > 0
    assert max(frame[1::3]) > max(frame[0::3])
    lit = max(frame)

    # (d) complete the Bit -> unload -> on_release -> session.clear() -> fade
    gs.run()
    gs.tick(2.1)                              # elapsed >= RUN_DURATION -> complete
    assert gs.state == State.IDLE

    closing_maxes = []
    for _ in range(30):
        loop._loop_once()
        closing_maxes.append(max(backend.frames[-1]))
    assert session.state in ("closing", "idle")
    assert min(closing_maxes) < lit          # a real fade dip occurred
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_led_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.led_smoke'`? No — this test doesn't import `led_smoke`. It fails only if `harness.device_bridge` or luxaeterna pieces are missing. If Task B2 is done and luxaeterna is installed, run it and expect PASS already (the test exercises only B2 + luxaeterna). If it PASSES here, that is correct — proceed to write the demo in Step 3 (its own deliverable).

Expected (with B2 + luxaeterna present): PASS. (If luxaeterna is not installed: SKIPPED.)

- [ ] **Step 3: Create the runnable demo**

Create `harness/led_smoke.py`:

```python
"""python -m harness.led_smoke — drive TestBit through the in-process stack and
watch it on the Web LED simulator.

Requires luxaeterna[websim] installed editable (see requirements-dev.txt):
    python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"
"""

from __future__ import annotations

import time

from bits.test_bit import TestBit
from control.engine import GameServer
from control.state import State
from harness.device_bridge import DeviceBridge
from luxaeterna.backends.websim import WebSimBackend
from luxaeterna.output import OutputLoop
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.universe import Universe

HOST, PORT = "127.0.0.1", 8770


def main() -> None:
    gs = GameServer({"test_bit": TestBit})
    cap = shroom_capability()
    bridge = DeviceBridge(capability=cap)
    gs.on_release = bridge.on_release

    gs.load_bit("test_bit")
    res = gs.join("sim-dev", "TEST_PLAYER_NODE")
    session = bridge.on_grant(res)

    uni = Universe()
    backend = WebSimBackend(capability=cap, host=HOST, port=PORT)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    loop.start()
    print(f"Watch the Shroom at http://{HOST}:{PORT}/  (Ctrl-C to stop)")

    gs.run()
    try:
        while session.state != "running":
            time.sleep(0.02)
        cc = 0
        while gs.state == State.RUNNING:
            session.feed_midi(0xB0, 74, cc)          # cc:74 -> hue
            session.feed_midi(0x90, 60, 100)         # new voice at current hue
            cc = (cc + 8) % 128
            gs.tick(0.15)                            # advances TestBit toward complete
            time.sleep(0.15)
        time.sleep(1.2)                              # let the closing fade + idle play
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Smoke-run the demo by hand (optional but recommended)**

**RUN ON: this session host (MacBookPro)**
```bash
python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"
python -m harness.led_smoke
```
Expected: prints `Watch the Shroom at http://127.0.0.1:8770/`. Open it: idle breathing → welcome flash → the ring/stem bloom and sweep hue for ~2 s → fade → idle. Ctrl-C to stop.

- [ ] **Step 5: Document the dev dependency in `requirements-dev.txt`**

Append to `requirements-dev.txt`:

```
# --- Optional: LED simulator harness (harness/led_smoke.py, tests/test_led_smoke.py) ---
# These need the sibling luxaeterna renderer with its web-sim extra. Install it
# editable (adjust the path if your checkout lives elsewhere):
#     python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"
# tests/test_led_smoke.py and tests/test_device_bridge.py importorskip luxaeterna,
# so the core suite still runs without it.
```

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest tests -q`
Expected: all pass (LED tests run because luxaeterna is now installed).

```bash
git add harness/led_smoke.py tests/test_led_smoke.py requirements-dev.txt
git commit -m "feat(harness): led_smoke demo + in-process full-stack LED regression"
```

---

### Task B4: sync the deep-dive doc

**Files:**
- Modify: `docs/MM_TERRARIUM.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Update the deep-dive to reflect the new harness**

In `docs/MM_TERRARIUM.md`, under *Landed subsystems*, add a short subsection after the `console/` entry:

```markdown
### `harness/` — the in-process LED-sim harness (Slice 1)
`DeviceBridge` + `led_smoke.py`: the first end-to-end exercise of the
light-manifest-v2 seam. It grants TestBit's `player` role, feeds the composed
`/ie<N>/role` blob into a luxaeterna `LightSession` (via a **dev/test dependency
on luxaeterna** — the first code coupling, venue-server → renderer), and renders
it to luxaeterna's new `WebSimBackend` (a browser canvas Shroom). Injects canned
MIDI via `LightSession.feed_midi`. Still **in-process — no o2lite wire**; the
device wire is Slice 2. Regression: `tests/test_led_smoke.py` (headless).
```

And in *Not yet built / deferred*, adjust the transport bullet to note that the render/contract path is now proven in-process (only the wire remains): append to the "Real O2lite/pyarco transport wiring" bullet: `The render/contract path (composed blob → luxaeterna → LEDs) is now proven in-process via harness/ (Slice 1); only the device wire itself is unbuilt.`

- [ ] **Step 2: Commit**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(deep-dive): record the in-process LED-sim harness (Slice 1)"
```

> Note: at closeout, `mm-deepdive-sync` will reconcile/verify this doc; this task seeds the change so the branch is self-consistent.

- [ ] **Step 3: Push and open the mm-terrarium PR**

```bash
git push -u origin claude/mm-tuneshroom-test-approach-11a21c
gh pr create --repo Musical-Mycology/mm-terrarium --fill
```

---

## Self-Review

**1. Spec coverage:**
- WebSimBackend (record + serve + capability + GRB page) → A2, A3. ✓
- `websim` optional extra, lazy websockets → A3 + Global Constraints. ✓
- `LightSession.feed_midi` seam → A1. ✓
- Reuse `www/leds.js` aesthetic → A3 `PAGE_HTML` (ring/stem canvas + glow). ✓
- Demo + luxaeterna docs → A4. ✓
- `base_hue → hue` in the three files → B1. ✓
- DeviceBridge (on_grant/on_release) → B2. ✓
- led_smoke demo + headless full-stack regression (welcome → dark → note+hue → fade) → B3. ✓
- luxaeterna dev-dependency, `importorskip` so core suite still passes → B2/B3 + Phase B preamble. ✓
- Deep-dive doc update, mm-tuneshroom CLAUDE.md flagged out-of-scope → B4 (CLAUDE.md remains a named follow-up in the spec, intentionally not a task). ✓
- Branch base `origin/main`, no `midi_capacity`, stale-tree caution → Global Constraints + A0. ✓

**2. Placeholder scan:** No "TBD/handle errors/similar to". Every code step shows complete code; every run step shows the exact command + expected result. ✓

**3. Type consistency:** `WebSimBackend(capability, host, port, serve)`, `.frames`, `.port`, `.send(frame, universe_id=0)`, `capability_message(cap)`, `PAGE_HTML`, `DeviceBridge(capability, clock)` / `.on_grant` / `.on_release` / `.session`, `build_session(manifest, cap, clock=...)` (no `midi_capacity`), `LightSession.feed_midi(status, data1, data2)`, `GameServer({"test_bit": TestBit})` / `.join` / `.on_release` / `.state == State.RUNNING`, `res.config["light_manifest"]` — all consistent across tasks. ✓

**4. Ambiguity:** Note B3 Step 2's callout — `test_led_smoke.py` may PASS as soon as B2 + luxaeterna exist (it doesn't import the demo); that is expected, and the demo file is still its own deliverable in Step 3.
