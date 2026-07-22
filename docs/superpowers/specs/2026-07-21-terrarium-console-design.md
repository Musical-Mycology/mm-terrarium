# Terrarium Console: Local Admin Panel

**Date:** 2026-07-21
**Status:** Approved (brainstorm session with Chris)
**Canonical architecture:** `docs/control-gameserver-design.md` (this repo)
**Builds on:** `docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`
(the lifecycle engine) and `docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md`
(the observer/command pattern this slice mirrors locally).

## 1. Purpose

Stand up the **Terrarium Console**: a small, Bit-agnostic local admin panel —
the durable front-end fixture that every Bit going forward is managed and
monitored through. It resolves the first-slice spec's deferred question of
*how a human actually triggers `load_bit`/`run` at a venue* (§2 of that spec:
"physical control, web panel, another Registration Node — a later decision").

MM staff on a trusted LAN open a web page (a laptop, or the Terrarium's own
screen) that talks to Control over a websocket. It drives the same lifecycle
the `UplinkAgent` already drives — the console is the *inbound, local* sibling
of the *outbound, remote* uplink — and adds richer read-only monitoring.

Like the engine and uplink slices before it, this slice runs entirely offline
in tests, with no O2 / Arco / pyarco / Lux Aeterna / fairyring dependency.

## 2. Scope

**In scope:**
- A new `console/` package mirroring `uplink/`'s shape: a transport-agnostic
  `ConsoleAgent`, a JSON protocol module, a real-socket server, and a
  self-contained static page.
- Lifecycle controls from the browser: list installed Bits, `load_bit`,
  `run`, `abort`/unload.
- Live monitoring: lifecycle state, registration (roles / capacities /
  occupancy), device pool, per-role **media manifests (audio `ugen_manifest`
  + new `light_manifest`)**, a generic `Bit.status()` read-out, and an
  in-memory event log.
- A multiple-observer refactor of the engine hooks so the console and uplink
  run simultaneously.
- Two small, schema-stable seams (`Role.light_manifest`, `Bit.status()`) so
  the panel is a genuine fixture for future Bits, with `TestBit` as the lone
  exemplar.
- Offline test suite (`FakeTransport` for `ConsoleAgent`; a real localhost
  websocket client for the server).

**Out of scope (explicitly deferred, same gates as prior slices):**
- Any pyarco/Arco ugen instantiation. Manifests display as placeholder data,
  exactly like `ugen_manifest` does today.
- **Driving Lux Aeterna's real-time render loop.** The console *monitors*
  declared light lanes (and, later, Lux Aeterna health); it never renders.
  See §7.
- Player-facing UI. This is the admin/operator screen, whether or not players
  ever see a related surface.
- Authentication / authorization. Trusted-LAN operator is an explicit,
  documented assumption (§6).
- Persistence or history beyond the in-memory event log (cleared on restart).
- Any JS build step or front-end test harness. The page is vanilla
  HTML/JS/CSS with no external asset fetches.

## 3. Architecture

New package `console/`, deliberately shaped like `uplink/`:

```
console/
  protocol.py     JSON message schemas (browser <-> Control)
  agent.py        ConsoleAgent: protocol <-> GameServer, transport-agnostic
  server.py       local HTTP+WS server (the only socket-touching code)
  static/
    index.html    self-contained admin page (no build step)
```

- **`console/protocol.py`** — the JSON contract. Reuses the command
  dataclasses and `parse_command` from `uplink/protocol.py` for
  `load_bit`/`run`/`abort` (single source of truth for command parsing).
  Adds console-only down-message builders: a full `snapshot` and the
  incremental events in §4. Where a down-event is byte-identical to the
  uplink's (`state_changed`, `registration_changed`), it reuses the uplink
  builder rather than redefining it.

- **`console/agent.py` — `ConsoleAgent`** — the transport-agnostic brains,
  the local sibling of `UplinkAgent`. It registers as a `GameServer`
  observer, translates inbound command messages into `GameServer` calls,
  builds the connect-time `snapshot`, and produces broadcast events on
  state / registration / device changes. It contains zero socket code, so it
  is unit-tested against `FakeTransport`. `ConsoleAgent` is transport-shaped
  the same way `UplinkAgent` is, but fans out to *N* connected browsers
  rather than one upstream link (the server owns the fan-out; see below).

- **`console/server.py`** — the only code that touches a real socket. A local
  server that **serves the static page over HTTP and upgrades to a websocket
  on the same port** (via the `websockets` server's `process_request` hook):
  a plain `GET /` returns `index.html`; any other request upgrades to the
  websocket. It maintains the set of connected browser clients, delivers each
  new client its `snapshot`, broadcasts subsequent events to all, and drops a
  dead/slow client without blocking others. Thin adapter, tested against a
  real localhost websocket client (mirroring `tests/test_websocket_transport.py`).

- **`console/static/index.html`** — a single self-contained page (vanilla
  HTML/JS/CSS, no build, no external fetches): the controls (installed-Bit
  picker + load/run/abort buttons) and the live monitoring tables
  (state, registration, devices, per-role media manifests, Bit status, event
  log). This is the durable front-end fixture every Bit reuses.

### Two edits to landed code

1. **`control/engine.py` — multiple observers.** Promote `on_state_change`
   and `on_registration_change` from single callable attributes to an
   **observer list** with an `add_observer(...)` registration API and
   notify-all semantics, so `UplinkAgent` and `ConsoleAgent` both attach
   cleanly. Migrate `UplinkAgent` onto the new API; its existing tests are
   the safety net for the refactor and must stay green. `on_release` stays a
   single transport-owned sink (unchanged). A `devices_changed` notification
   is added on the same mechanism (the console wants device-pool deltas;
   today only `on_release` fires).

2. **`control/roles.py` — `light_manifest`.** Add a `light_manifest: list`
   placeholder field to `Role`, sibling to the existing `ugen_manifest`
   (empty default, present so the schema does not change when the first real
   Bit declares light lanes). No Lux Aeterna dependency is introduced.

### Data flow

```
browser --(command)--> server --> ConsoleAgent --> GameServer
GameServer observer fires --> ConsoleAgent builds event --> server broadcasts --> all browsers
new browser connects --> server asks ConsoleAgent for snapshot --> that one browser
```

The server runs in Control's process, driven from the same tick loop as
`UplinkAgent`: `ConsoleAgent.poll()` drains inbound commands and the server
flushes queued broadcast events once per iteration. Never in any hot
render/audio path.

## 4. Console Protocol

JSON both directions, mirroring the uplink split: parsed dataclasses up,
dict builders down.

### Browser -> Control (commands)

Reuse `uplink.protocol.parse_command` verbatim:

- `{"command": "load_bit", "name": "<bit>"}`
- `{"command": "run"}`
- `{"command": "abort"}`

A malformed or unrecognized command is dropped with a logged warning, and an
`error` event is sent back to the originating client (same discipline as the
uplink).

### Control -> Browser (down)

**`snapshot`** — sent once, on connect, so a late-joining browser renders
immediately:

```json
{"event": "snapshot",
 "state": "SETUP",
 "installed_bits": ["TestBit"],
 "loaded_bit": "TestBit",
 "roles": [{"role": "...", "class": "...", "capacity": 4, "scored": true,
            "ugen_manifest": [], "light_manifest": []}],
 "registration": [{"role": "...", "count": 0, "capacity": 4}],
 "devices": [{"dev": "ie3", "name": "...", "role": "..."}],
 "bit_status": {}}
```

- `installed_bits` is the `bit_registry` keys.
- `loaded_bit` is the loaded Bit's registry name, or `null` when `IDLE`.
- `roles` is the loaded Bit's role table (empty when no Bit is loaded).
- `registration` is `RegistrationState.counts()` (empty when no Bit loaded).
- `devices` is the device pool joined with current role assignments.
- `bit_status` is `Bit.status()` (or `{}`).

**Incremental events** — broadcast to all connected browsers:

- `{"event": "state_changed", "state": "RUNNING"}` (reuses uplink builder)
- `{"event": "registration_changed", "roles": [...]}` (reuses uplink builder)
- `{"event": "devices_changed", "devices": [...]}`
- `{"event": "bit_status", "status": {...}}`
- `{"event": "bit_completed", "result": {...}}`
- `{"event": "error", "command": "run", "message": "..."}`
- `{"event": "log", "level": "info", "message": "..."}` (feeds the event-log pane)

### Two Bit/engine seams this needs

Both optional, default no-op, so `TestBit` is the lone exemplar:

- **`Bit.status() -> dict`** — a generic key/value read-out the panel renders
  as a table. Returns `{}` by default; a Bit overrides it to surface its own
  state. This is the same generic seam that later carries Lux Aeterna / Arco
  health (§7).
- **`devices_changed` observer trigger** — device-pool deltas, on the same
  observer-list mechanism as the §3 refactor.

**`bit_status` cadence.** The console *polls* `Bit.status()` on each existing
observer fire plus on a low-rate console tick, rather than adding a
Bit-initiated push channel. Rationale: Bits need no back-reference to the
console, and it matches the pull-style `result()` / `counts()` already in the
model. A future Bit needing sub-second status latency would revisit this; no
current Bit does.

## 5. Serving Model & Operational Concerns

- **Single port, HTTP + WS.** One `websockets` server with a `process_request`
  hook serves `index.html` on `GET /` and upgrades everything else to the
  websocket. No second HTTP server, no build step, no external asset fetches.
- **Binding & auth.** Bind to a configurable host/port, **default
  `127.0.0.1:<port>`**, with LAN exposure (`0.0.0.0`) as an explicit opt-in
  flag. Safe-by-default for the trusted-LAN operator while still letting staff
  reach it from a laptop when they choose. **No authentication** — a
  deliberate, documented scope decision predicated on the trusted-LAN
  assumption; if these panels ever face an untrusted network, auth becomes a
  prerequisite and this decision must be revisited.
- **Concurrency.** N browsers may connect; every event broadcasts to all,
  commands are accepted from any (trusted operators). A dead or slow client is
  dropped without blocking others.
- **Lifecycle integration.** The server runs in Control's process, driven from
  the same tick loop as `UplinkAgent`. Never in any hot render/audio path.
- **Failure isolation.** A console/server exception never propagates into the
  engine tick — the same guarantee the uplink already provides.

## 6. Trust & Safety Assumptions

The console assumes a **single trusted operator population on a trusted LAN**.
It has no authentication, and any connected client may issue lifecycle
commands. This is acceptable only because:

1. the operator is MM staff on-site (established in the brainstorm), and
2. the default bind is loopback, with LAN exposure an explicit opt-in.

This assumption is load-bearing. The moment a console is exposed beyond a
trusted LAN, authentication and per-command authorization become
prerequisites, not enhancements.

## 7. Relationship to Lux Aeterna (and Arco)

Lux Aeterna is MM's Python DMX512 / Art-Net -> WLED lighting library (see
mm-documents `MM_HARDWARE_DESIGN.md` and
`mm-shrooms-app/shroom-installations-design.md`). It is the **real-time
lighting renderer** — the visual analog of the Arco audio engine — driving the
Terrarium array and the Shroom LEDs in a **44 Hz hot loop**, downstream of a
*Bit's* cue/graph logic.

The console's relationship to it is **monitor, never drive**, exactly the
boundary already drawn for Arco:

- The console *displays* each role's declared media manifest — audio
  (`ugen_manifest`) and light (`light_manifest`) lanes — as placeholder data.
  It does **not** instantiate ugens on Arco, and it does **not** push frames
  to Lux Aeterna's render loop. Driving either belongs to a later "real Bit +
  real outputs" slice.
- A **future, out-of-scope** console feature this design's `Bit.status()` /
  generic-status seam anticipates: a Lux Aeterna health read-out (Art-Net link
  up? WLED reachable? active light lanes?) surfaced through the same generic
  channel. That requires Lux Aeterna actually running, so it is not this
  slice — but the seam is shaped to accept it without a protocol change.

The `light_manifest` placeholder added in §3 exists precisely so the fixture
reflects the architecture's rule that light is authored in the same timeline
as sound, from day one.

## 8. Testing

- **`ConsoleAgent`** — pure offline unit tests against `FakeTransport`:
  command dispatch -> engine calls, snapshot correctness across lifecycle
  states, event broadcast on each observer fire, `Bit.status()` polling, and
  the error paths. No socket, no Arco, no pyarco.
- **`console/server.py`** — a real localhost websocket client connects,
  fetches `GET /`, sends a command, and asserts the resulting broadcast —
  mirroring `tests/test_websocket_transport.py`.
- **Engine multi-observer refactor** — new tests for `add_observer` /
  notify-all fan-out and the `devices_changed` trigger; the existing uplink
  tests are migrated onto the new API and kept green as the refactor's safety
  net.
- **`index.html`** — no JS test harness this slice (vanilla, no build). It is
  exercised manually plus by the server integration test proving the page is
  served on `GET /`.

## 9. Non-Goals (restated)

No ugen/Arco instantiation; no Lux Aeterna render-loop driving; no player UI;
no authentication; no persistence/history beyond the in-memory event log; no
JS build step or front-end test harness.
