# Control+GameServer: First Implementation Slice

**Date:** 2026-07-20
**Status:** Approved (brainstorm session with Chris)
**Canonical architecture:** `docs/control-gameserver-design.md` (this repo)
**Precedes:** `docs/superpowers/specs/2026-07-18-mm-terrarium-bootstrap-design.md`,
which deferred all Control/Bit implementation to this spec.

## 1. Purpose

Stand up the Control+GameServer's game-launching engine: the lifecycle
machinery that loads a Bit, opens registration, runs it, scores it, and
returns to a clean waiting state. This slice is scaffold-only — no real
gameplay, no ugen graph-building, no scoring logic beyond a stub. The goal is
to prove the launching engine end-to-end so later slices can add real Bits
without re-deciding how loading/registration/running/unloading works.

Roger Dannenberg's work (pyarco, Arco-level engine changes) is out of scope
here by design — this slice has no dependency on pyarco (see §7).

## 2. Scope

**In scope:**
- Control+GameServer as an O2lite client of the Arco server (services `game`
  and `actl`), following pyarco's existing connect/poll pattern.
- The lifecycle state machine (§3) and its Python-API triggers.
- Role table, device pool, and registration-state data model (§4).
- `TestBit`: a durable reference/test Bit exercising the full lifecycle,
  including both scored and jam roles (§4).
- Error handling for the join/deny paths and forced-unload safety (§5).
- Offline test suite using a fake O2lite transport (§6).

**Out of scope (explicitly deferred):**
- Any real Bit gameplay, per-role graph-building, or ugen creation on Arco.
- Scoring logic beyond a stub hook and TestBit's placeholder `ugen_manifest`.
- An operator-facing O2 service for `load_bit`/`run` — this slice uses direct
  Python API calls; how a human actually triggers these at a venue (physical
  control, web panel, another Registration Node) is a later decision.
- pyarco dependency pinning / arco-o2 submodule-vs-sibling decision (bootstrap
  spec's open question #1) — still Roger's to settle.
- `o2audioio` per-player audio streaming, browser `ui<X>` service
  implementation details beyond the addressing decision in §7.

## 3. Lifecycle State Machine

```
IDLE ──load_bit──▶ LOADING ──ready──▶ LOADED ──(auto)──▶ SETUP ──run──▶ RUNNING ──bit_complete──▶ COMPLETING ──closed──▶ UNLOADING ──done──▶ IDLE
```

- **IDLE** — no Bit loaded. Only valid trigger: `load_bit(name)` (direct
  Python call).
- **LOADING** — Control imports/instantiates the named Bit module and builds
  its role table. Synchronous in this slice (no real resource allocation).
  Success → LOADED. Failure → back to IDLE, error surfaced to the caller.
- **LOADED** — technical readiness only, no human-facing behavior. Transient:
  immediately auto-advances to SETUP. No Bit in this slice needs to gate this
  transition, but the state exists so a future Bit that needs async setup
  work has somewhere to do it before SETUP begins.
- **SETUP** — registration is open; this is the waiting-room stage. Bit's
  `on_setup_enter()` hook runs (instructions, local feedback — no-op for
  TestBit). `/game/join` resolves against the role table for both scored and
  jam roles. Re-tapping a node to switch roles uses the same join path (no
  separate protocol verb — see design doc rule 3). Waits for `run()`.
- **RUNNING** — timed/synced gameplay. Bit's `on_run_start()` fires, then
  `update(dt)` runs once per tick. `/game/join` stays valid but **scored
  roles are denied**, **jam roles are still accepted** (an installation has
  casual foot traffic; jam is explicitly the unscored, always-open case).
  Ends when the Bit itself signals completion via `update(dt)` — not when
  Control observes some external condition. Control's state machine stays
  Bit-agnostic: it never evaluates a win condition itself.
- **COMPLETING** — Bit's `on_complete()` hook runs (scoring, closing
  actions — "appropriate tables" is undefined in this slice; the hook exists,
  its content doesn't yet). Always advances to UNLOADING, even if the hook
  raises (see §5).
- **UNLOADING** — Control sends `/ie<N>/release` to every device in the
  current `RegistrationState`, frees role assignments, discards the Bit
  instance, calls `on_unload()`. → IDLE.

## 4. Components

**`DevicePool`** — Control-global, spans Bit lifecycles. `dev → connection
info`, populated by `/game/hello "si" name protoversion`, valid in any
state. This is what makes "returns the device to the joinable pool" (design
doc §Player Flow step 5) meaningful — release doesn't forget the device.

**`RoleTable`** — static, declared by the Bit. Each `Role`: `name`, `class`
(`unique`/`shared`/`jam`), `capacity`, `scored` (bool), `ugen_manifest`
(placeholder — empty/stub for this slice; the future home for the per-role
graph-builder declaration, added now so the schema doesn't change later).
Plus `node_map`: `node_id → ordered [role_name, ...]` fallback list.

**`RegistrationState`** — runtime, created on LOADED, torn down on
UNLOADING. `dev → (node, role, class)`, plus a live per-role count for
capacity checks. Join/switch resolution (walk the node's fallback list,
check capacity, deny if class/state rules forbid it) is Control's generic
logic — the design doc is explicit that Control does this lookup, not the
Bit.

**`Bit` interface** — minimal hook set, most are no-ops in this slice:
`role_table`, `on_setup_enter()`, `on_run_start()`, `update(dt)` (signals
completion), `on_complete()`, `on_unload()`, and an optional dict of extra
`/game/*` verb handlers (empty for TestBit).

**`TestBit`** — durable reference/test fixture, not throwaway. Two roles:
a **scored** `shared` role (`player`) and an unscored **jam** role
(`jammer`), each granted by its own Registration Node — this is what makes
the scored-vs-jam RUNNING join rule an actual tested behavior instead of an
assumption. `update()` auto-signals completion after a fixed duration once
RUNNING starts, so the full lifecycle is exercisable without a live Arco
connection. Each role carries a placeholder `ugen_manifest` (empty list) for
future graph-builder work.

**Tick loop** — `o2lite.poll()` → drain inbound O2 messages → dispatch each
through the state-aware lifecycle router → if a Bit is loaded and in
SETUP/RUNNING, call `bit.update(dt)` → sleep to hit target tick rate. Matches
pyarco's existing synchronous poll pattern (`o2lite.poll()` + `time.sleep()`)
— borrowed philosophy from pygame's "you own the loop, nothing hidden"
approach, without taking pygame as a dependency (see §7 rationale).

## 5. Error Handling

- **Unknown node on join** → `/ie<N>/deny "no such node"`, no state change.
- **Node's fallback list exhausted** → `/ie<N>/deny "<role> at capacity"`,
  optional `hint` naming another node.
- **Scored join attempted during RUNNING** → deny with a distinct reason
  (e.g. `"registration closed for scored roles"`) so a device can tell
  "wrong node" apart from "wrong time."
- **`load_bit()` called when not IDLE, or `run()` called when not SETUP** →
  raise in the calling Python code; these are direct API calls in this
  slice, not O2 messages needing a wire-level reply.
- **`on_complete()` or `on_unload()` raises** → log and force-advance the
  state machine anyway. UNLOADING/IDLE must always be reachable so Control
  never gets stuck loaded because one Bit's hook misbehaved. This is a
  deliberate choice, not an oversight — worth a code comment since it's not
  obvious from the code alone.

## 6. Testing

Offline test suite using a fake O2lite transport, following pyarco's
existing `FakeO2Lite` precedent (`python25/tests/`). The full
LOADING→...→IDLE cycle, including TestBit's scored/jam join rules under
SETUP and RUNNING, is testable without a live O2 network or Arco server.
Since the design doc treats `/game/*` as the complete input history of a
session, tests can be literally scripted message sequences replayed against
the state machine — the same mechanism that gives record/replay for free
later.

## 7. Design Decisions Recap (with rationale)

- **Control connects via o2lite**, not a full-O2 peer binding, despite the
  design doc's "full O2 peer" framing — no Python full-O2 binding exists
  today (only `o2litepy`, which pyarco itself uses). Revisit if/when one
  exists; until then this reuses proven infrastructure at zero new cost.
- **pygame was evaluated and rejected as a dependency.** Its headless-loop
  philosophy (explicit tick loop, no hidden runtime) is worth borrowing, but
  pygame has no networking, its event system is input-shaped rather than
  message-shaped, and it would add an SDL dependency to a server that never
  renders or plays audio locally (Arco owns room audio, Tuneshrooms own
  local LEDs/sound). Implemented directly instead: a plain tick loop.
- **Single `game` service** for both lifecycle and gameplay verbs, matching
  mm-tuneshroom's existing convention and the design doc's default framing.
- **Browsers register as `ui<X>` O2 services**, symmetric with `ie<N>` for
  hardware Tuneshrooms — one addressing idiom for all Interactive
  Elements/UIs.

## 8. Open Questions / Risks (not blocking this slice)

1. **pyarco's source of truth is unsettled.** Roger may be actively working
   in his own fork/repo rather than `Musical-Mycology/pyarco` (last commit
   there: Chris, 2026-07-09). This slice has zero dependency on pyarco (no
   graph-building happens), so it doesn't block here — but must be resolved
   before any Bit does real graph-building work.
2. **Operator command interface** (`load_bit`/`run`, and eventually
   `abort`/force-end) needs a real trigger mechanism at a venue — physical
   control, web panel, or a Registration Node convention. Direct Python API
   is a placeholder for this slice only.
3. **"Appropriate tables/closing actions"** in COMPLETING is an empty hook
   in this slice — undefined until a real Bit needs it.
4. Design doc's own open questions (o2audioio bandwidth concerns, `game`
   vs. split services already decided above) remain otherwise unresolved
   where not addressed here.
