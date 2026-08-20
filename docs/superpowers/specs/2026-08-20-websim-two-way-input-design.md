# WebSim two-way input: browser canvas gestures for the simulated Tuneshroom

Date: 2026-08-20
Status: approved for implementation (autonomous session; key calls made
per the request's own framing, each with the reasoning recorded).
Repos: luxaeterna (the WebSim seam) + mm-terrarium (the harness wiring).

## 1. Problem

`harness/o2_shroom.py` is a full simulated Tuneshroom on the wire -- it
joins, receives its role, renders frames on a browser canvas -- but its
gesture side is synthetic only (a scripted tilt sweep). An operator
watching the canvas cannot *play* it. Real Tuneshrooms send `tap`,
`tilt` and `shake` over `/game/<verb>`; the simulator should let the
operator do the same from the thing they are already looking at.

## 2. Decisions and reasoning

**D1 -- Input arrives via the WebSim page, not stdin.** (recommended,
adopted.) The page's websocket is already open in both directions; the
`_handle` loop in `WebSimBackend` reads and discards inbound frames
today (`for _ in connection: pass`). The browser *is* the operator's
view of the device, so input belongs on it. A stdin channel would be a
second surface, unusable when `o2_shroom` is a `run_stack` child with
teed stdio. What would change the call: no browser available at all --
not a real constraint anywhere this harness runs.

**D2 -- Verbs: `tap` first, `tilt` via horizontal drag. No `shake`.**
Click = tap count 1; double-click = tap count 2 (TestBit's `_on_tap`
distinguishes exactly these -- click vs chime). Dragging horizontally
maps canvas x onto gamma in [-90, 90] and sends `/game/tilt` at a
bounded rate; while the operator is driving tilt, the synthetic sweep
is suspended (and resumes a few seconds after the last operator tilt,
so an unattended run still animates). Shake has no natural mouse
gesture and exercises nothing tap+tilt don't already reach.

**D3 -- Timestamps: the harness stamps at send, on the O2 clock.**
Design Rule 4 says devices stamp at the source. The "device" here is
the whole simulator process -- browser tab plus Python harness -- so
the browser-to-backend hop is *inside* the device, and the correct
source stamp is `o2lite.time_get()` read when the harness drains the
input queue, exactly as the synthetic tilt already stamps `now`.
Browser event timestamps are on an unrelated clock and are not sent.
GameServer then computes `at = origin + cue_horizon` as for any
gesture; nothing engine-side changes.

**D4 -- The luxaeterna seam is one optional callback.**
`WebSimBackend(on_input=...)`: called with the decoded dict for every
JSON **text** message a page sends (binary frames stay ignored;
frames flow only server-to-browser). Malformed JSON is dropped and
logged at debug, never raised -- same drop-don't-error rule as the rest
of the wire. The callback runs on the websocket handler thread;
callers needing their own thread must hand off (mm-terrarium does, via
a queue). `on_input=None` (default) preserves today's behavior
byte-for-byte.

## 3. Wire shapes

### 3.1 Browser -> backend (new, luxaeterna-internal)

JSON text messages over the existing `/ws` socket:

```json
{"type": "tap", "count": 1}
{"type": "tilt", "gamma": -42.5}
```

The page owns gesture *detection* (double-click windowing, drag
mapping, rate bounding); the harness owns wire *translation*. The page
never knows about `/game/*`.

### 3.2 Harness -> Control (existing shapes, no protocol change)

From `devicelink/protocol.py`'s documented rows, unchanged:

    up /game/tap   sffi [dev, peak_g, duration_ms, count]
    up /game/tilt  sf   [dev, gamma]

Simulated tap fills `peak_g=1.0`, `duration_ms=50.0` -- honest
placeholders for values a mouse cannot measure; `count` is real.

## 4. Changes by repo

### 4.1 luxaeterna

- `luxaeterna/backends/websim.py`:
  - `WebSimBackend.__init__` gains `on_input: Callable[[dict], None] |
    None = None`.
  - `_handle`'s hold-open loop becomes: for each inbound message, if it
    is a `str`, `json.loads` it (drop on failure) and call `on_input`
    if set. A raising callback is caught and logged, never allowed to
    kill the connection handler.
  - `PAGE_HTML` gains pointer handlers on the canvas:
    - `click` / `dblclick`: send `{"type":"tap","count":1|2}`. Single
      click is delayed ~250 ms and cancelled by a following dblclick,
      so a double-click sends one count-2 tap, not 1-then-2.
    - `pointerdown` + `pointermove` while down: map x across the canvas
      to gamma in [-90, 90], send `{"type":"tilt","gamma":g}` at most
      every 50 ms; `pointerup`/`pointerleave` ends the drag. A drag
      of more than a few px suppresses the click-tap so one physical
      gesture never sends both.
    - Status line briefly shows the sent gesture, so input has visible
      feedback even before the round trip lights the canvas.
- Tests:
  - `tests/backends/test_websim.py`: inbound text message reaches
    `on_input`; malformed JSON dropped; binary inbound ignored; default
    `on_input=None` unchanged.
  - `tests/js/websim_input.test.js` (Node vm, same harness as
    `websim_layout.test.js`): click sends tap 1, dblclick sends a
    single tap 2, drag maps x to gamma and rate-bounds, drag suppresses
    click, messages are well-formed JSON. The websocket stub grows a
    `sent` capture list.

### 4.2 mm-terrarium

- `harness/shroom_client.py`: `ShroomClient.tap(peak_g, duration_ms,
  count)` and (for symmetry with the documented wire) nothing else --
  encoders only, socket-free, mirroring `tilt()`.
- `harness/o2_shroom.py`:
  - `build()` wires `WebSimBackend(on_input=...)` to a bounded
    thread-safe `queue.Queue` (drop-oldest on overflow, like
    `_MAX_PENDING_FRAMES`'s philosophy). Room/`--no-join` runs wire it
    too but `main()` ignores gestures there, same as the existing
    no-gestures rule for `--no-join`.
  - `main()`'s tick loop drains the queue each pass (only when
    `_gestures_ready(client)` and not `--no-join`; input before the
    role is granted is dropped -- same race-avoidance as the sweep):
    - tap -> `o2lite.send("/game/tap", now, "sffi", dev, 1.0, 50.0,
      count)`.
    - tilt -> `o2lite.send("/game/tilt", now, "sf", dev, gamma)` and
      records `last_operator_tilt = now`; the synthetic sweep is
      skipped while `now - last_operator_tilt < SWEEP_RESUME_SECONDS`
      (5.0).
  - Backward compatibility: with no browser input, behavior is
    byte-identical to today.
- Tests (offline, no sockets): queue-drain translation (tap and tilt
  mapping, stamps taken from the injected clock), sweep suspension and
  resumption, drop-before-role, `--no-join` ignores input, overflow
  drops oldest.

## 5. What does not change

- `devicelink/protocol.py`, `devicelink/agent.py`, `control/` -- zero
  changes. The verbs already ride `/game/<verb>` into
  `Bit.verb_handlers()`, and TestBit already implements `tap`/`tilt`.
- The websocket-path simulators (`room_simulator.py`,
  `devicelink_smoke`) -- input is a player-device feature on the
  o2lite path this slice. The seam is generic, so extending later is
  additive.
- The Room canvas: bound Room fixtures never send gestures.

## 6. Error handling

- Page-side: gestures while the socket is closed are dropped silently
  (send guarded by `readyState`).
- Backend: malformed JSON or a raising callback -> debug log, drop,
  connection stays up.
- Harness: unknown `type` or non-numeric fields -> drop with one debug
  line; the queue bound protects against a stuck main loop.

## 7. Live verification

`harness/run_stack.py --devices 1` (per the operator/harness handoff
docs), open the device canvas, then: click (device flash + click
sample + `flash_device` trigger fire on the Console), double-click
(chime), drag (hue follows the pointer on device and Room; sweep
resumes ~5 s after release). Suites: mm-terrarium 1099+1 baseline,
luxaeterna ~230 baseline, both green plus the new tests.
