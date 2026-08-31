# Terrarium Console: current front end audit

A complete inventory of what the Terrarium Console web front end does today,
written as a handoff for a visual overhaul. Everything below was read off the
code in this repo and confirmed against a live, populated instance; the two
screenshots in `docs/images/` are that instance.

**Scope note.** This is the *only* web front end mm-terrarium owns. The
browser canvases you see during a run (`WebSimBackend`, one browser tab per
simulated Tuneshroom and one for each Room fixture) are served by
**luxaeterna**, not by this repo, and are out of scope here.

---

## 1. What it is

The **Terrarium Console** is a Bit-agnostic local admin panel: the single
surface a venue operator uses to pick a Bit, start it, watch it, intervene in
it, and stop it. It is the durable front-end fixture every future Bit is
managed and monitored through, so it is deliberately generic; it renders
whatever the loaded Bit *declares* rather than knowing anything about any
particular Bit.

| | |
|---|---|
| **Served from** | `console/server.py` (`ConsoleServer`), one port, HTTP + websocket |
| **Brains** | `console/agent.py` (`ConsoleAgent`), driven from the engine tick loop |
| **Wire schemas** | `console/protocol.py`, sharing builders with `uplink/protocol.py` |
| **Front end** | `console/static/` (4 files, 500 lines total) |
| **Framework** | None. No build step, no bundler, no dependencies, no npm. |
| **Started by** | `harness/terrarium_boot.py --console-port N` (off by default) |
| **Trust model** | No authentication. Binds `127.0.0.1` by default; `0.0.0.0` is an explicit opt-in. Trusted LAN only. |

```bash
.venv/bin/python -m harness.terrarium_boot --console-port 8772
```

### The four static files

| File | Lines | Role |
|---|---|---|
| `console/static/index.html` | 45 | The whole page skeleton: nine `<h2>` sections, four empty tables, four empty divs |
| `console/static/console.js` | 174 | Websocket connect/reconnect, event dispatch, Bits panel, the three tables, event log |
| `console/static/room.js` | 237 | Room panel: fixture LED strips, zone bars, instrument cards |
| `console/static/triggers.js` | 168 | Trigger panel: one card per declared trigger, Fire buttons, device pickers |
| `console/static/style.css` | 53 | Every style on the page |

`room.js` and `triggers.js` are IIFE-wrapped and export exactly their entry
points onto `window`. That is not stylistic: a shared `buildCard` helper in
both files previously collided, `triggers.js` loaded second and silently won,
and `renderRoom` threw on every `room_changed`. **Any overhaul must preserve
that isolation or replace it with a module system.**

---

## 2. Screenshots

| State | File |
|---|---|
| Live run: MetronomeBit RUNNING, DEMO room bound, 2 players joined, every panel populated | `docs/images/terrarium-console-current.png` (2800 x 5960) |
| First open: IDLE, no Bit loaded, room unbound | `docs/images/terrarium-console-idle.png` (2800 x 1520) |

Both were captured from the real console with no code changes, served by a
scratch driver that constructs `GameServer` + `ConsoleServer` +
`ConsoleAgent` directly (no Arco, no simulator subprocess, no o2lite), the
same way `tests/test_console_agent.py` does.

---

## 3. The nine panels, in DOM order

### 3.1 Header
`Terrarium Console <span id="conn">`. The span is the only connection
indicator: `(connecting…)`, `(connected)`, or `(disconnected — retrying)` in
red. Reconnect is a fixed 1 s retry loop with no backoff and no attempt
counter.

### 3.2 Bits
- A one-line status paragraph: `Loaded Bit: <name>` and `State: <STATE>`.
- **Run** and **Abort** buttons. Always enabled, in every state.
- One card per discovered Bit package (`bits/*/bit.toml`), each showing
  display name, registry name, a `kind` badge (`TOOL`, `R_GAME`), version,
  supported room types, the start condition, the description, and a **Load**
  button.
- Cards for Bits marked `hidden` render at 50 % opacity but are still
  loadable.
- One red-bordered error card per package that failed to load, showing its
  path and the parse error.

Rebuilt only when the declared table changes, gated on a JSON signature.

### 3.3 Room
- A header line: `DEMO · 864 px · GRB · 1/1 fixture(s) bound`.
- **One LED strip per declared fixture**: a flex row of one `<div>` per
  pixel, repainted live from `room_frame` events. The DEMO room is 864
  pixels, so that is 864 DOM nodes repainted at ~10 Hz.
- **One zone bar per fixture** beneath the strip: the fixture name (plus
  `(not bound)` when no device holds it) and each named zone with its pixel
  range, sized proportionally.
- **One card per declared instrument**, light and audio together in one list
  discriminated by a `LIGHT` / `AUDIO` badge. Each card shows the instrument
  name, its target zone (light only), its `params`, any audio-only keys
  (`program`, `drone`), and every declared lane as `cc:74 → hue = 93`, where
  the trailing number is the **live controller value** off the Room's MIDI
  fan-out.

Rebuild is gated **per fixture**, on that fixture's own shape. A rebuilt
fixture is reinserted before the nearest later surviving fixture so
declaration order survives a partial rebuild. Frames paint swatch
backgrounds only and never touch the DOM structure.

The wire is **GRB, not RGB**, and `renderRoomFrame` decodes it as such.
Reading it as RGB renders every zone the wrong colour, which looks like a
lighting design decision rather than a bug.

**Privacy filter.** The Room's Registration Node id, role name and
registration counts are never sent to this panel. Its instruments, surface,
zone names and live controller values are. That split is deliberate and
tested; do not widen it.

### 3.4 Triggers
One card per trigger the loaded Bit declares:
- Trigger name, a target badge (`DEVICE` or `ROOM`), the description.
- The condition: its description plus its source, e.g.
  `Player matches the call phrase (bit-adjudicated)`.
- **The full script**, one monospace line per step:
  `+0.36s   @target   cc:70 = 125`.
- A **device picker** (`<select>`) for `DEVICE`-target triggers, populated
  from the live device list and preserving the operator's selection across
  device-list updates.
- A **Fire** button, which fires the trigger as `admin-manual`.
- A last-fired status line: `last fired by bit-adjudicated -> ie1 (2 cues)`,
  or `never fired`. An admin-manual fire is tagged `ADMIN MANUAL` in amber
  bold so an operator action never reads as gameplay.

Rebuilt only when the declared table changes (a rebuild would discard picker
selections).

### 3.5 Registration
Table: `Role | Count | Capacity`. Unbounded capacity renders as `∞`. The
Room's own role is filtered out.

### 3.6 Roles & media manifests
Table: `Role | Class | Cap | Scored | ugen_manifest | light_manifest`. The
two manifest columns are raw `JSON.stringify` output in a table cell.

### 3.7 Devices
Table: `Device | Name | Role`. Every device that has said hello, whether or
not it holds a role. A device bound as the Room appears here (it said hello
like any other) with its role blanked to `—`.

### 3.8 Bit status
Table of whatever `Bit.status()` returns, one row per key. Untyped: values
are stringified by assignment to `textContent`.

### 3.9 Event log
An 8 rem scrolling monospace box. Append-only `textContent`, auto-scrolled
to the bottom. Carries state transitions, bit completions, errors, and
server-pushed `log` events, each prefixed `[info]` or `[error]`.

---

## 4. Wire protocol

### 4.1 Inbound: browser to server (6 commands)

| Command | Payload | Sent by the UI? |
|---|---|---|
| `run` | none | Yes, the Run button |
| `abort` | none | Yes, the Abort button |
| `load_bit` | `name`, optional `overrides` | Yes, but **never with overrides** |
| `fire_trigger` | `name`, optional `dev` | Yes, the Fire buttons |
| `list_bits` | none | **No.** Parsed and handled, but nothing sends it |
| `arm_room` | `room_type`, `fixture`, `window_seconds` | **No.** No UI exists |
| `release_room` | `room_type`, optional `fixture` | **No.** No UI exists |

`fire_trigger`, `arm_room` and `release_room` are **console-only** admin
commands, deliberately parsed separately from the shared uplink parser so a
remote fairyring peer can never request them.

### 4.2 Outbound: server to browser (13 events)

| Event | When | Panel |
|---|---|---|
| `snapshot` | Once, on connect. The full read model. | All |
| `bits_listed` | Once, on connect. | Bits |
| `state_changed` | Every lifecycle transition | Bits header, log |
| `registration_changed` | Every join/leave | Registration |
| `devices_changed` | Every hello/reap | Devices, trigger pickers |
| `bit_status` | On change, polled per tick | Bit status |
| `room_changed` | On change (includes controller values) | Room |
| `room_frame` | **Decimated to ~10 Hz**, one per bound fixture dev | Room strips |
| `triggers_changed` | On change (in practice, load and unload) | Triggers |
| `trigger_fired` | Per fire | Trigger status lines |
| `bit_completed` | On UNLOADING, if `Bit.result()` is not None | Log |
| `error` | Per refused command | Log |
| `log` | Server-pushed | Log |

**Room frames are droppable by design.** The Room renders at 44 Hz; the
console is a monitor and gets ~10 Hz, with intermediate frames overwritten
rather than queued, keyed per dev. Nothing gameplay waits on may ever be put
behind this panel.

---

## 5. Rendering discipline that must survive an overhaul

These are all consequences of defects that were found live. A rewrite that
loses them re-introduces the bug.

1. **A high-frequency event must never rebuild a list.** Bits, Triggers and
   the Room fixture strips are each rebuilt only when their declared shape
   changes, gated on a signature comparison. `state_changed` fires
   continuously; `room_frame` fires ten times a second.
2. **A frame paints, it does not rebuild.** `renderRoomFrame` writes swatch
   background colours and nothing else.
3. **Per-fixture, not per-Room, rebuild granularity.** An untouched
   fixture's live strip must survive another fixture's reconfiguration.
4. **Declaration order is physical order.** A rebuilt fixture is reinserted
   in place, not appended.
5. **Per-dev pending frames.** A single pending-frame slot starved every
   fixture but the last one to render in a tick.
6. **GRB decode.** Not RGB.
7. **Script isolation.** No shared global helpers between panel scripts.
8. **The Room's node id, role name and registration counts stay hidden**
   while its instruments and surface stay visible.
9. **An unknown dev in a frame event is a no-op, not an error.** Frames can
   race a Room reconfiguration.

---

## 6. Gaps and weaknesses (the overhaul brief)

Grouped by how much they hurt at a venue.

### Hurts most

1. **Light mode only, and pinned to it.** `body` hard-codes `#fff` with an
   8-line comment explaining that the palette is designed for a light
   background and that a real dark theme is a separate design decision. The
   Terrarium is a dark room. This is the single biggest mismatch between the
   panel and where it is used.
2. **Trigger cards are unbounded in height.** Every script step renders as
   a flat monospace line. MetronomeBit's `fireworks_player` script is 36
   steps, so one card is taller than the viewport and the Triggers section
   alone is roughly 900 px tall. There is no collapse, no summary, no
   timeline view.
3. **No state gating on controls.** Run and Abort are always enabled. The
   console happily offers an illegal transition and surfaces the refusal as
   a red line in the event log after the fact. There is no confirmation on
   Abort.
4. **Manifests are raw JSON in a table cell.** `light_manifest` in the Roles
   table is a single `JSON.stringify` string wrapping across three lines.
   The Room panel already proves the readable version exists (instrument
   cards with badges, params and live lane values); the Roles table just
   does not use it.

### Missing affordances

5. **Room binding has no UI.** `arm_room` and `release_room` are fully
   implemented server-side, are the *only* way a fixture gets bound to a
   device at a venue, and have no button. Today they are reachable only by
   hand-crafting a websocket frame.
6. **Bit configuration has no UI.** `load_bit` accepts an `overrides` dict,
   `ConsoleAgent` resolves it through `BitRegistry.resolve_config`, and
   `ManifestError` comes back as a clean per-key error. The UI never sends
   one, so retuning e.g. MetronomeBit's bpm at a venue means editing a
   profile TOML and restarting.
7. **Devices show no liveness.** `Device | Name | Role` only. There is no
   last-seen, no protoversion, no signal, even though `reap_stale` silently
   removes a device after 15 s of silence and frees its role slot.
8. **Registration shows counts, not occupancy.** Who holds which slot lives
   in a separate table two sections away.

### Quality of life

9. **The event log is a `textContent` accumulator.** No cap, no timestamps,
   no filtering, no severity colour beyond the `[level]` prefix, no way to
   copy one line. It grows unbounded for the life of the tab.
10. **Trigger fire history is broadcast-only.** `trigger_fired` is not part
    of `snapshot`, so a browser opened after a fire shows `never fired` for
    a trigger that has fired all night.
11. **No responsive layout.** There is a viewport meta tag and nothing else.
    Tables and trigger cards overflow horizontally on a tablet or phone,
    which is the form factor an operator actually walks the room with.
12. **`Bit.status()` is untyped.** An empty list renders as an empty cell
    (see `tap_errors_ms` in the screenshot). A nested dict would render as
    `[object Object]`.
13. **No brand identity at all.** System font stack, browser-default
    buttons, no favicon, no logo, no Musical Mycology colour anywhere. It
    looks like a debug page because it is one.
14. **The 864-pixel strip is 864 DOM nodes.** It works, and it is the
    heaviest thing on the page. A canvas would be one node.

---

## 7. What an overhaul must not break

- **The engine seam.** `ConsoleAgent` attaches through `GameServer`'s
  multi-observer list and is driven by `poll()` from the tick loop. Nothing
  in the front end may become something gameplay waits on.
- **The single-port model.** One `ConsoleServer` serves static assets and
  the websocket on the same listener, with an extension allowlist and
  basename-only path resolution. Adding a build step means deciding how
  built assets reach that allowlist.
- **Offline testability.** `console/protocol.py`, `control/room_view.py` and
  `control/trigger_view.py` are pure dict builders with no engine imports,
  and the whole panel is tested against an in-process fake server with no
  socket. Six test files cover it:
  `tests/test_console_agent.py`, `test_console_protocol.py`,
  `test_console_server.py`, `test_console_static.py`,
  `test_console_wiring.py`, `test_console_script_isolation.py`.
- **The shared protocol builders.** `state_changed`, `registration_changed`,
  `bit_completed`, `bits_listed` and `error` are byte-identical between the
  console and the outbound uplink, on purpose.
- **The trust model,** unless authentication is added at the same time. The
  moment this panel faces an untrusted network, auth is a prerequisite and
  not an enhancement.

---

*Audited 2026-08-25 against branch `claude/terrarium-web-audit-084bcc`.*
