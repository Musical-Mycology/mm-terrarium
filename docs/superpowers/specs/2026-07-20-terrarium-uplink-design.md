# Terrarium Uplink: Remote-Driving GameServer

**Date:** 2026-07-20
**Status:** Approved (brainstorm session with Chris)
**Canonical architecture:** `docs/control-gameserver-design.md` (this repo);
README's "Relationship to other repos" (`mm-fairyring (planned)`)
**Builds on:** `control/engine.py`'s `GameServer` (landed in
`docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`)

## 1. Purpose

`GameServer` today is driven entirely by direct, synchronous, in-process
Python calls (`hello`/`load_bit`/`run`/`join`/`tick`) — nothing outside the
process can reach it, and it can't tell anyone outside the process what it's
doing. This spec adds a **Terrarium uplink**: a module that makes
`GameServer` remotely drivable and observable over a persistent outbound
connection, without making `GameServer` depend on that connection existing,
being up, or ever being used at all.

This is the first of three systems identified for "launch a Bit from
RenQuest" (Terrarium uplink → mm-fairyring cloud broker → RenQuest trigger).
It's scoped to be buildable and fully testable now, standalone, against a
protocol contract that a future mm-fairyring can implement independently
later without requiring changes here.

## 2. Scope

**In scope:**
- `UplinkAgent`: owns connection lifecycle (connect, reconnect-with-backoff,
  resync-on-reconnect), translates inbound wire commands into `GameServer`
  calls, and pushes outbound events from `GameServer` state changes.
- `Transport` protocol + `WebSocketTransport`: the only code that touches an
  actual socket.
- A small observer-hook addition to `GameServer` (`on_state_change`),
  following the existing `on_release` convention.
- A public accessor on `RegistrationState` for live per-role counts
  (currently the private `_counts`).
- An optional `Bit.result()` hook (default `None`) for a Bit to hand back a
  completion payload.
- The wire protocol itself (§4): down-commands, up-events, resync shape.
- `FakeTransport`: in-process test double, no real socket.

**Out of scope (explicitly deferred):**
- Any real mm-fairyring server, or a repo for it.
- Auth/identity handshake, venue/Terrarium ID scheme, TLS/cert details —
  the protocol below is what a future fairyring needs to speak; standing up
  the other end is a separate spec.
- `join`/`tick` traffic on the uplink — stays local, device-to-Control, at
  o2lite speed. Never crosses this link (README constraint: "never in the
  real-time loop").
- Buffering/replay of events missed during an outage — resync on reconnect
  makes this unnecessary for now.
- Pause/resume of a running Bit based on link state — a live Bit runs
  identically whether or not the uplink is connected.

## 3. Architecture

```
control/engine.py           uplink/link.py            uplink/transport.py
┌─────────────┐  observer   ┌──────────────┐  send/    ┌──────────────────┐
│ GameServer   │───────────▶│ UplinkAgent  │◀─recv────▶│ Transport (proto) │
│ (unchanged   │  hook       │              │           │  WebSocketTransport│
│  lifecycle)  │◀───calls────│              │           │  FakeTransport    │
└─────────────┘  (load_bit/  └──────────────┘           └──────────────────┘
                  run/abort)
```

`GameServer` gains one observer-hook list (`on_state_change(old, new)`,
mirroring `on_release`) and stays otherwise untouched — no import of
`uplink/` anywhere in `control/`. `UplinkAgent` is constructed with a
`GameServer` instance and a `Transport`; it registers itself as an observer
and is the only thing that knows the wire protocol exists. This keeps
`GameServer` O2-agnostic *and* uplink-agnostic, matching its existing design
principle (see `control/engine.py` docstring).

`Transport` is a minimal protocol: `connect()`, `send(msg: dict)`,
`receive() -> dict | None` (non-blocking, or `None` on no message), and
`connected: bool`. `WebSocketTransport` implements it over a persistent
outbound `websockets` connection, Terrarium-initiated (venue boxes are
NAT'd, no inbound ports — matches the pattern already implicit in the
Arco/o2lite topology). `FakeTransport` implements it as two in-memory
queues a test can push into and inspect.

## 4. Wire Protocol

Plain JSON-serializable dicts, defined as dataclasses in
`uplink/protocol.py` so both sides of a schema mismatch fail loudly in
tests rather than silently on the wire.

**Down** (fairyring → Terrarium), each a direct `GameServer` call:

| Command | → | Notes |
|---|---|---|
| `load_bit {name}` | `GameServer.load_bit(name)` | Errors (`BitLoadError`, `InvalidTransition`) become an `error` event, not a raised exception across the wire. |
| `run {}` | `GameServer.run()` | Same error-wrapping. |
| `abort {}` | forces `GameServer._unload()` | New: an explicit abort path. Today `GameServer` only unloads via natural completion; `abort` is Control-initiated early termination. Bit's `on_complete`/`on_unload` still run (best-effort scoring/cleanup on early exit). |

**Up** (Terrarium → fairyring), pushed reactively from the `on_state_change`
hook and from `RegistrationState`'s public count accessor:

| Event | Payload | When |
|---|---|---|
| `state_changed` | `{state: "IDLE"\|"LOADING"\|...}` | Every `GameServer` state transition. |
| `registration_changed` | `{role: str, count: int, capacity: int \| null}[]` | On every `join`/`release` while a Bit is loaded. |
| `bit_completed` | `{result: <Bit.result() return value>}` | Once, when entering COMPLETING, if `Bit.result()` returns non-`None`. |
| `error` | `{command: str, message: str}` | A down-command failed against `GameServer`. |

**Resync** (sent once, immediately after `connect()` succeeds — including
after a reconnect): a single `state_changed` with the current state, plus a
current `registration_changed` snapshot if a Bit is loaded. This is the
only recovery mechanism for a gap in the event stream; nothing is buffered
during a disconnect.

## 5. Connection Handling

`UplinkAgent` runs reconnection with exponential backoff (capped), fully
decoupled from `GameServer.tick()` — the tick loop never blocks on or
checks link state. While disconnected, outbound events are simply dropped
(not queued); the resync-on-reconnect makes replay unnecessary. This is a
direct consequence of the "Bit keeps running untouched" decision: the link
is a best-effort observability/control channel, not part of the gameplay
loop's correctness.

## 6. Testing

`FakeTransport` is the only test double, in-process, no real socket or
server — matching how `GameServer`'s own suite already runs fully offline
(no live O2/Arco). Coverage:
- Each down-command translates to the correct `GameServer` call, and engine
  errors become `error` events rather than propagating.
- Each `GameServer` state transition produces exactly one `state_changed`
  event.
- `join`/`release` while a Bit is loaded produce `registration_changed`
  events with correct counts.
- `Bit.result()` returning a value produces `bit_completed`; returning
  `None` produces nothing.
- Disconnect mid-Bit: `GameServer.tick()` continues advancing normally;
  no exceptions, no state corruption.
- Reconnect: exactly one resync event pair is sent, reflecting current
  state.

## 7. Design Decisions Recap (with rationale)

- **Observer hook, not a `GameServer` subclass or wrapper** — keeps
  `GameServer` transport-agnostic and matches the existing `on_release`
  convention rather than inventing a second integration pattern.
- **Persistent outbound WebSocket**, not HTTP polling or MQTT — venue
  boxes are NAT'd (must dial out), and this matches the websocket precedent
  already used elsewhere in the O2/Arco stack. MQTT would add a new broker
  dependency the rest of the stack doesn't have; HTTP polling adds latency
  and load for no benefit here.
- **Lifecycle + registration status on the wire, not full device
  telemetry** — the README is explicit the uplink is never in the
  real-time loop; join/tick stay local. Registration counts are included
  (beyond bare lifecycle) so a remote operator can see fill-state without
  polling the room, without crossing into per-device tick-rate traffic.
- **Bit keeps running untouched on disconnect** — `GameServer` is already
  fully local/synchronous; making gameplay depend on link health would be
  a real behavioral change to the engine's contract, not just an uplink
  concern. Rejected as unnecessary complexity for no clear benefit yet.
- **In-process `FakeTransport` only, no runnable mock server** — matches
  the existing offline-test philosophy of this repo; a runnable mock
  fairyring server is a nice-to-have demo tool, not needed to prove
  correctness, and can be added later without touching this design.
- **`Bit.result()` as a new optional hook, not a return value from
  `on_complete()`** — keeps `on_complete()`'s existing signature and every
  existing Bit (i.e. `TestBit`) working unchanged; a Bit opts in only if it
  has something to report.

## 8. Open Questions / Risks (not blocking this slice)

1. **Abort semantics during LOADING/SETUP** — this spec assumes `abort` is
   valid from any non-IDLE state and forces `_unload()`, but `_unload()`
   currently assumes a `RegistrationState` exists; needs a guard for
   aborting before `RegistrationState` is created (mid-LOADING failure
   path). Small implementation detail, not a design blocker.
2. **Backoff parameters** (initial delay, cap, jitter) are an
   implementation choice, not specified here.
3. **Auth/identity** on the `WebSocketTransport` connection (how Terrarium
   proves which venue it is) is explicitly deferred to the mm-fairyring
   design — this spec's protocol doesn't assume any particular scheme, but
   a real deployment needs one before this is usable outside a lab.
4. **Multiple concurrent Bits per Terrarium** — out of scope; the existing
   `GameServer` model is one Bit at a time, and this uplink inherits that
   assumption unchanged.
