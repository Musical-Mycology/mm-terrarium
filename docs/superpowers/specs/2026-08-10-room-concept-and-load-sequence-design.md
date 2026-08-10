# Room Concept & Load Sequence

**Date:** 2026-08-10
**Status:** Approved (brainstorm session with Chris)
**Canonical architecture:** `docs/control-gameserver-design.md` (this repo)
**Builds on:** `docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`
(the lifecycle engine and `Bit`/`RoleTable` data model this slice extends) and
`docs/superpowers/specs/2026-07-21-terrarium-console-design.md` (the admin-panel
pattern this slice's admin registration action attaches to).
**Precedes:** a second, not-yet-written spec for the **Terrarium Visualization
Simulator** — the first concrete Room *backend* (simulated LEDs/mic/speaker for
TEST and DEMO), which consumes the interfaces this spec defines. That spec is
out of scope here; see §8.

## 1. Purpose

Today, every harness script (`led_smoke.py`, `devicelink_smoke.py`,
`array_smoke.py`) is a disconnected, hand-run demo: one simulates a single
Shroom, one drives real gameplay over a live device wire but needs an
*external* browser client to see anything, one drives the venue LED array
directly against real hardware with a fixed test pattern and no Bit behind it.
Nothing today can boot the Terrarium as a coherent whole — Arco included — and
land it in a running, Bit-driven state without a human stitching several
processes together by hand.

This spec introduces two things that close that gap:

1. **`Room`** — a first-class concept representing the physical (or simulated)
   LED/mic/speaker hardware an installation offers, analogous in shape to how
   a `Bit` already declares per-player devices via `RoleTable`. A Bit declares
   which Room type(s) it supports; a Terrarium instance resolves to one
   concrete Room at boot and fails loudly if its available hardware can't
   satisfy it.
2. **An orchestrated load sequence** — a single startup path that spawns and
   waits for Arco, resolves the Room, binds its backend (simulated or
   physical), and loads a Bit compatible with the resolved Room type, in place
   of today's manual "start Arco by hand, then run a harness script" workflow.

Nothing here builds a renderer. The concrete simulated or hardware backend a
`Room` binds to is the next spec's job (§8). This spec defines the shape
everything else — simulator and, later, real hardware — plugs into.

## 2. Scope

**In scope:**
- `RoomType` enum (`TEST`, `DEMO` initially) with a code-defined, per-type
  backend-presence "recipe" checked at boot.
- `Bit.room_types` (declared support set; every Bit includes `TEST`) and
  `Bit.room_manifest` (Room-scoped light/sound instrument declarations,
  authored the same way `Role.light_manifest`/`ugen_manifest` are today).
- A fourth `RoleClass` value, `ROOM` (capacity 1), and the grant-handling
  branch that binds a joining device to the resolved `Room` instead of
  granting a player role.
- The admin-only "Connect LED Device as Room" registration flow, its
  ephemeral join window, and the Control-global binding registry that
  survives Bit load/unload cycles and records a device ID for reconnect-on-
  restart.
- The Arco process lifecycle: Terrarium spawns it, waits for readiness, and
  SIGTERMs it on shutdown.
- The end-to-end boot sequence: config -> Arco -> Room resolution -> Room
  binding -> Bit load -> tick loop -> shutdown.
- `RoomBridge`, the engine-side peer to `DeviceBridge`/`AudioBridge` that
  renders `room_manifest` cues once a backend is bound.
- Offline tests for all of the above using fakes, matching this repo's
  existing "no real Arco/O2/pyarco needed" test philosophy.

**Out of scope (explicitly deferred):**
- The concrete simulator implementation (browser LED view, simulated
  mic/speaker) — a separate spec, see §8.
- Real hardware discovery/probing beyond "is devicelink acceptable" / "is an
  array backend configured." No physical DEMO hardware exists yet (per
  `docs/MM_TERRARIUM.md`), so DEMO's exact component inventory is out of
  scope here and stays owned by the external hardware-design doc.
  `RoomType.DEMO`'s recipe in this spec is deliberately minimal (array
  backend presence only) and will grow as real hardware is specified
  elsewhere.
- Console UI polish for the admin registration action beyond a functional
  hook (button -> opens window -> tap binds).
- Real O2lite/pyarco transport wiring — Arco connectivity here uses the same
  dev/test-only `PYTHONPATH` path already used throughout this repo.
- Authentication. The Room registration node is unlisted and admin-window-
  gated, which is *stronger* than plain obscurity, but it is still not
  credential-based auth — see §7.
- Disk persistence of anything beyond the single bound device ID per Room
  type.
- `RoomType` values beyond `TEST`/`DEMO` (`TYPE1`/`TYPE2` etc. are named in
  conversation as a future extension point, not designed here).

## 3. Data Model

### `RoomType` and its recipe

```
class RoomType(Enum):
    TEST = auto()
    DEMO = auto()
```

Each `RoomType` has a small, code-defined **recipe**: which backend
*capabilities* (not live device counts — see §5) must be available for
Terrarium to resolve as that type.

- `TEST` — requires devicelink to be capable of accepting a join (always true
  once the device server starts). Nothing else.
- `DEMO` — requires devicelink *and* an array output backend configured for
  this installation (real ArtNet/WLED host, or a simulated backend — either
  counts as "configured").

Resolution is boot-time, deterministic, and **fails hard** if the target type
isn't satisfiable — there is no silent downgrade to a lesser type.

### `Bit.room_types` and `Bit.room_manifest`

Two new declarations on `Bit`, alongside the existing `role_table`:

- **`room_types: set[RoomType]`** — which Room types this Bit can run in.
  Every Bit includes `TEST` (the universal baseline); a Bit may declare more.
- **`room_manifest`** — Room-scoped light/sound instrument declarations,
  authored by the Bit in the same spirit as `Role.light_manifest`/
  `ugen_manifest`, validated at `load_bit` the same way
  `control/role_config.py` already validates per-role manifests today. This
  is what lets a Bit say "when I run in a DEMO room, the array plays these
  instruments" — the exact wire shape is left open here and settled when
  the first Bit actually declares one (matching how light-manifest v2 itself
  wasn't frozen until `TestBit` needed it).

### `RoleClass.ROOM`

```
class RoleClass(Enum):
    UNIQUE = auto()
    SHARED = auto()
    JAM = auto()
    ROOM = auto()   # capacity 1; binds the room backend, not a player
```

A `room_manifest` declares a `ROOM`-class role bound to its own dedicated
Registration Node. Granting it does not compose a player `/ie<N>/role` blob —
it binds the joining device as the resolved `Room`'s rendering backend (§4).

### `Room` (runtime object)

Resolved once at boot, `Room` is a peer to `DevicePool`/`RegistrationState` in
Control's data model, but scoped to room-owned hardware only:

- `room_type: RoomType` — the resolved type.
- a reference to its bound backend (whatever concretely implements
  rendering — simulator or real hardware; opaque to this spec).

Tuneshroom-hosted LEDs/mic/speaker are **not** touched by any of this — they
stay entirely on the existing per-role `DeviceBridge`/`AudioBridge` pipeline.

## 4. Room Binding

Binding — how a concrete backend gets attached to the resolved `Room` — works
differently depending on backend kind.

### Simulator-backed Room

Terrarium spawns/owns the simulator instance directly (the same ownership
pattern as Arco, §5) as part of Room binding, before any Bit is loaded, and
connects to it immediately. No tap, no registration window, no ambiguity:
Terrarium created both ends, so there is nothing to discover.

### Physical-device-backed Room

Normally closed to joins. An admin-only Console action — "Connect LED Device
as Room" — opens a short-lived registration window; **only** a tap/QR scan
*during that window* binds a device via the `ROOM`-class Registration Node
described in §3. This is stronger than plain obscurity (an always-open but
unlisted node): the window only exists when an admin deliberately opens it.

Once bound:
- The device ID is held in a Control-global, in-memory registry (survives Bit
  load/unload cycles, like `DevicePool` already does), and is additionally
  written to a small on-disk record — just the ID, not broader state — so it
  survives a full process restart too.
- On a Terrarium **process restart**, if a recorded device ID exists for the
  target Room type, boot attempts to reconnect to that specific device as
  part of Room resolution. If it's unavailable, Room resolution falls back to
  requiring a fresh admin-armed tap (or fails outright, per the no-downgrade
  rule in §3).
- An admin "release" action clears the record, returning the node to closed.

This is exactly the mechanism that lets an extra Tuneshroom stand in as a
TEST room: an admin arms the window, taps that Tuneshroom instead of a player
node, and it becomes the Room's backend rather than a player's device.

## 5. Load Sequence

1. **Read boot config**: target `RoomType`, target `bit_name`, Arco launch
   params (soundfont, etc.), array backend config (host/port, or "use
   simulator") if this installation is configured for one.
2. **Spawn Arco** as a subprocess; poll for readiness (`/host/started`, per
   Arco's own documented state-transition protocol) with a timeout — fail
   loudly if it never comes up. Arco has no message-based quit (per
   `doc/server.md` in the `arco` checkout: the only documented shutdown is a
   console keypress), so Terrarium's own shutdown path sends **SIGTERM**
   to the subprocess rather than attempting an in-protocol quit — the same
   pattern `harness/led_smoke.py` already uses on itself via
   `_sigterm_as_keyboard_interrupt`.
3. **Connect** Control's pyarco client to the now-running Arco (today's
   `PYTHONPATH`-based dev/test path; real O2lite transport is unbuilt
   elsewhere and out of scope here).
4. **Resolve Room**: check the target `RoomType`'s recipe (§3) against what's
   actually configured. Hard-fail, no downgrade, if unsatisfied.
5. **Bind Room**: per §4, either spawn the simulator and connect directly
   (simulator backend), or attempt reconnect-to-recorded-device, falling
   back to requiring an admin-armed tap (physical backend).
6. **Gate the Bit**: look up `bit_name` in the Bit registry; verify its
   declared `room_types` includes the resolved `RoomType` — hard-fail if not.
7. **`gs.load_bit(bit_name)`** as today (`LOADING -> LOADED -> SETUP`).
   Control holds in SETUP for Room binding to complete if it hasn't already
   (reusing the SETUP-hold pattern this repo already built for
   `devicelink_smoke`'s scored-role join trap, `docs/MM_TERRARIUM.md`'s
   `--setup-seconds`) — again a timeout, again a hard boot failure rather
   than proceeding into RUNNING with an unbound Room.
8. **Tick loop proceeds** (`SETUP -> RUNNING -> ...`) exactly as today, with
   `RoomBridge` (§6) now rendering alongside per-role bridges.
9. **Shutdown**: tear down Room-bound voices/sessions (mirroring
   `AudioBridge.shutdown()`), then SIGTERM the Arco subprocess.

## 6. Engine Integration

- **`RoomBridge`** — new, peer to `DeviceBridge`/`AudioBridge`. Owns the
  bound backend and applies `room_manifest` cc lanes the same way those
  bridges apply per-role manifests.
- Driven from the same tick loop as everything else: a Bit's `update(dt)`/
  `verb_handlers()` emits cues, Control forwards Room-scoped ones to
  `RoomBridge` through a room-scoped sibling of the existing `on_light_cue`
  sink. No new loop, no new thread.
- Extends **boundary rule 1** ("single writer to `/arco`", see
  `docs/MM_TERRARIUM.md`): Room's Arco registrations go through Control like
  everything else. No new writer path.

## 7. Trust & Safety

This repo's existing trust model is "trusted LAN, no authentication" —
Console and DeviceLink both state this outright today. Room registration
follows the same model, not a stronger one: the `ROOM`-class Registration
Node is never surfaced in the Console, any app, or QR/NFC generation, and
binding additionally requires an admin to have explicitly opened the
registration window first. That combination (unlisted + admin-armed-window)
is meaningfully stronger than a standing, always-scannable node, but it is
still obscurity plus an operator gate, not credential verification. If Room
binding ever needs to resist a genuinely untrusted actor on the LAN, that is
a deliberate future escalation — the same caveat the console spec already
carries for its own trust boundary.

## 8. Relationship to the Simulator Spec

This spec defines the shape a Room backend must fill (bind, render
`room_manifest` cues, tear down) without designing any concrete backend. The
next spec — the Terrarium Visualization Simulator — implements the first one:
a simulated LED/mic/speaker backend satisfying both TEST (a simulated
Tuneshroom-shaped device) and DEMO (a simulated venue array), reachable the
same way a real hardware backend eventually will be. Because binding (§4) and
rendering (§6) are already backend-agnostic here, a later real-hardware
backend should be addable without revisiting this spec.

## 9. Testing

Room resolution, `ROOM`-class binding, the admin registration window, binding
persistence, and Bit-gating are all offline-testable with fakes — no real
Arco/devicelink required — matching this repo's existing suite. Arco
process-spawning and readiness-polling need a lightweight fake-subprocess
seam (mirroring how `ArcoSynthPool` already isolates its pyarco import behind
`start()`) so the boot sequence itself stays testable without a real Arco
binary.

## 10. Open Questions (deferred, not blocking)

- Exact wire shape of `room_manifest` — left open until the first Bit
  declares one, matching how light-manifest v2 wasn't frozen until it had a
  real consumer.
- `DEMO`'s full component inventory (beyond "array backend configured") —
  depends on hardware not yet specified elsewhere.
- Whether `RoomType` grows further values (`TYPE1`/`TYPE2`, discussed as a
  future direction) and what their recipes would require.
