# Terrarium lifecycle and config-defined rooms

Spec 1 of the Room/Instrument/Trigger restructure (brainstormed 2026-08-26).
This spec covers the **two-level lifecycle** (a Terrarium that boots with no
room and no Bit, loads and unloads rooms as an operator-driven operation)
and **config-defined rooms** (the valid-room set moves out of the
`RoomType` enum into a versioned Terrarium config file). The companion
concepts agreed in the same brainstorm -- Instruments/Fixtures, the
Function/Trigger split, capability-contract matching, external Bit repos,
and bundled Bit packages -- are recorded in section 12 as follow-on specs;
this spec builds the ground they instantiate on and deliberately does not
build them.

Baseline: **1362 passed, 1 skipped**, fully offline (`.venv/bin/python -m
pytest tests -v`; a fresh worktree needs
`ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first -- see
`docs/MM_TERRARIUM.md`, *Landed subsystems*).

## 1. What changes, in one picture

Today `control/boot.py`'s `boot()` is a one-shot linear sequence: config ->
spawn Arco -> resolve `RoomType` -> bind Room -> gate Bit -> `load_bit`. The
room is chosen at boot from a **code enum** (`control/rooms.py`), its profile
is a **Python function** (`control/room_profile.py:room_profile()`), and the
process cannot exist without a room.

After this spec:

```ascii
TERRARIUM process level (new)                     Bit level (existing, unchanged)
+----------+  load_room   +--------------+
| NO_ROOM  | -----------> | ROOM_LOADING |        IDLE -> LOADING -> LOADED
|          |              |  sweep stale |          -> SETUP -> RUNNING
+----------+              |  spawn Arco  |          -> COMPLETING -> UNLOADING
     ^                    |  fixtures,   |          -> IDLE
     |                    |  bindings    |
     | unload_room        +------+-------+        ...nests entirely inside
     | (Bit must be              v                ROOM_READY. load_bit is
     |  IDLE; abort()     +--------------+        refused in any other
     |  first if not)     |  ROOM_READY  | <-->   Terrarium state.
     +--------------------+--------------+
```

- The process boots with only **Terrarium-scoped** services: the Console
  (now usable roomless -- it is how an operator loads a room), the uplink,
  and the device transport listener.
- `load_room(name)` validates the named room against the Terrarium config,
  sweeps stale owned processes, spawns a fresh Arco, brings up fixtures and
  bindings, and enters ROOM_READY. Any failure mid-load unwinds the
  room-scoped teardown stack back to a clean NO_ROOM -- never a half-room.
- `unload_room()` closes the room-scoped stack (Bit abort if needed, room
  bridge, fixtures, Arco) and returns to NO_ROOM.
- `--room X --bit Y` on the CLI is **pure shorthand** that drives the same
  `load_room` -> `load_bit` path the Console uses. There is no second boot
  path.

## 2. The Terrarium config file

A new, versioned TOML -- `terrarium.toml` at the repo root by default, with
a `--config PATH` override -- parsed by a new `control/terrarium_config.py`
in the same stdlib-`tomllib`, shallow-structural, located-errors style
`control/bit_config.py` established. Schema v1:

```toml
schema = 1

[terrarium]
name = "dev-box"                 # provenance stamp (section 10)
bit_paths = ["bits"]             # bit.toml search roots; in-repo default

[rooms.TEST]                     # the valid-room set, one table per room
description = "12-LED dev room"
backends = ["devicelink"]        # capability requirements, ex-RoomRecipe
  [[rooms.TEST.fixtures]]        # ex-room_profile(): fixtures/blocks/zones
  name = "main"
  color_order = "GRB"
    [[rooms.TEST.fixtures.blocks]]
    name = "b1"
    pixel_count = 60
    [[rooms.TEST.fixtures.zones]]
    name = "left"
    start = 0
    count = 20
    # ... center/right ...
  # ... accent fixture ...
  [rooms.TEST.arco]              # per-room Arco knobs, ex-CLI flags
  ready_timeout = 30.0
  settle_seconds = 2.0

[rooms.DEMO]
backends = ["devicelink", "array"]
# 864 px "array" fixture, six 144 px blocks m1..m6, three 288 px zones
```

Consequences:

- **`RoomType` the enum is deleted.** Rooms are config names. TEST and DEMO
  become shipped example configs (checked-in `terrarium.toml` carries both),
  which forces anything the profile code does that cannot be expressed as
  data out into the open now. `RoomProfile`/`RoomFixture`/`RoomBlock`/
  `RoomZone` survive as the parsed dataclasses; only their construction
  moves from code to config. All existing invariants keep holding:
  per-block `_MAX_PROFILE_PIXELS`, zone-coverage validation, derived
  `pixel_count`/`channel_count` as the single source of frame width.
- **`resolve_room_type()` becomes `validate_rooms(config, capabilities)`**:
  boot-time, per-room, fail-hard on the room actually being loaded, and
  advisory (a validation status per room) for the rest of the set, surfaced
  on the Console's rooms panel and `--list-bits`-style CLI output.
- **`RoomBindingRegistry` keys by `(room_name, fixture)`** instead of
  `(RoomType, fixture)`. Mechanical. Its `save()`/`load()` finally get
  wired: `load_room` loads the persisted bindings for that room name and
  attempts reconnect before falling back to the admin-armed tap window.
- **`bit.toml`'s `[launch] room_types` becomes `rooms = [...]` by config
  name.** A Bit naming a room absent from this Terrarium's config is
  flagged unloadable-here at discovery (located error in `--list-bits` and
  the Console Load picker, discovery of other Bits unaffected) and refused
  at `load_bit` with a `BitLoadError`. A Bit naming a room that exists but
  is not the *active* room is likewise refused at load, with the active
  and required names in the error.

## 3. The Terrarium state machine

A new `TerrariumState` (`NO_ROOM`, `ROOM_LOADING`, `ROOM_READY`,
`ROOM_UNLOADING`) owned by a new `control/terrarium.py` orchestrator that
holds the `GameServer`, the active room (or None), the two teardown stacks
(section 4), and the config. Gating rules, each engine-enforced (not
harness-enforced) and each tested:

1. `load_bit` is refused unless `ROOM_READY`. The refusal is an ordinary
   located error to the caller (Console refusal flash / CLI message), never
   an exception that kills the process.
2. `load_room` is refused unless `NO_ROOM` (load) -- switching rooms is
   `unload_room` then `load_room`, two explicit operations, so there is
   never an ambiguous "which room is Arco serving" moment.
3. `unload_room` requires the Bit lifecycle at `IDLE`. If a Bit is loaded,
   the operator aborts it first (the Console already has the two-tap-confirm
   Abort); `unload_room(force=True)` exists for the CLI/uplink and calls
   `abort()` itself, reusing the hooks-always-run, UNLOADING-always-reachable
   guarantee.
4. State transitions notify the existing multi-observer list via a new
   `on_terrarium_state_change`, riding the same seam as
   `on_state_change`/`on_registration_change`. Console and uplink both gain
   `room_loaded`/`room_unloaded`/`room_load_failed` events and the Console
   snapshot gains the rooms panel data (valid-room list + per-room
   validation status + active room).

`ROOM_LOADING` reports **progress**, not just an outcome: Arco cold-start
alone can take ~18 s (the first readiness probe against a cold Arco, see
`docs/MM_TERRARIUM.md` *Not yet built*), and an operator mid-event will be
watching the panel. Progress lines (`spawning arco` / `waiting for audio` /
`binding fixture main (1/2)` / `room ready`) ride the observer seam as a
`room_load_progress` event and print on Control's stdout via the existing
lifecycle-logger pattern.

## 4. Two teardown stacks

The single `TeardownStack` splits by lifetime. Same guarded, idempotent,
LIFO mechanism (`control/teardown.py`, unchanged); what changes is that
there are two instances with different owners:

- **Terrarium-scoped stack**: created at process start, closed at process
  exit. Holds the devicelink server / o2lite transport, the Console, the
  uplink.
- **Room-scoped stack**: created fresh by each `load_room`, closed by
  `unload_room` and by any mid-load failure. Holds Arco, each fixture
  backend (simulator subprocess or, later, hardware output), the room
  bridge, and -- registered last, so popped first -- the Bit abort guard.

The invariant this preserves is the hard-won one: **anything a room load
spawns is registered on the room stack in the same statement that spawns
it**, so no future addition to `load_room` can be orphaned by a forgotten
call site, and a `load_room` failure at any step unwinds exactly what that
load had started. `harness/terrarium_boot.py`'s existing stack becomes the
Terrarium-scoped one; `boot()`'s current body becomes `load_room`'s.

## 5. The stale-instance sweep (guardrails)

`load_room` begins by clearing anything left over from a crashed prior run.
Three rules, in order, each load-bearing:

1. **Only kill what we can prove we own.** Every process a room load spawns
   is recorded (pid + spawn time + role) in a run-scoped record under
   `runs/` (the directory `run_stack` already owns). The sweep kills only
   pids found in such records -- current or leftover -- via the existing
   bounded `stop_process` SIGTERM -> SIGKILL cycle, and only after checking
   the pid still names a process we started (spawn-time comparison guards
   pid reuse). **Never pattern-match process names**: a dev box
   legitimately runs multiple stacks, and a name-based sweep is a
   machine-wide footgun.
2. **Probe, then refuse, for what we don't own.** After the owned sweep,
   run the existing ownership verification (the Roger-blessed self-addressed
   round trip with the 10 s resend window, `devicelink/o2_transport.py`).
   If a foreign hub or `game`-service claimant still answers, **fail the
   room load with a named error** identifying what answered. A human
   decides that one; the sweep never escalates to killing an unowned
   process.
3. **Sweep on load, not only on unload.** Unload can be skipped by a crash;
   the load-time sweep is the backstop for the SIGKILL/OOM cases the
   existing `--exit-with-parent` guards cannot cover. Those guards remain
   the first line.

Fresh-Arco-per-room-load is deliberately embraced rather than optimized
away: the two standing upstream traps (only the first pyarco client after
an Arco start gets working audio on macOS; `initialize()`'s reset kills
earlier clients' sockets) both reward a virgin hub per room load. What was
an operational workaround becomes the architecture.

## 6. Device consequences

- **`DevicePool` does not survive a room cycle.** A room teardown kills
  Arco, and every o2lite device's clock sync and socket die with the hub; a
  pool entry pointing at a dead connection is exactly the stale-device
  problem the 2026-08-25 liveness slice closed. `unload_room` clears the
  pool (after the closing-fade release path runs); the device heartbeat
  (`/game/hello` resend) repopulates it as devices reconnect and re-sync
  against the next room's hub. Room-bound fixture devices follow the
  binding registry instead (section 2): persisted by room name, reconnect
  attempted on the next `load_room` of that room.
- Devices mid-fade at `unload_room` follow the existing asynchronous
  release path; the room stack's Bit/bridge steps wait for closing fades
  the same way `terrarium_boot` already does, bounded by
  `_MAX_CLOSING_FRAMES`.

## 7. Harness and CLI

- `harness/terrarium_boot.py` gains `--room NAME` (replacing `--room-type`)
  and `--config PATH`; `--bit` unchanged. With neither `--room` nor
  `--serve`-implying flags, the process boots to NO_ROOM and waits on the
  Console -- the new roomless idle. `--room X --bit Y` front-loads
  `load_room(X)` then the existing round-one `load_bit(Y)`; every
  subsequent operation is Console/uplink-driven, exactly as serve mode
  works today.
- `harness/run_stack.py` forwards `--room`/`--config`. Its device respawn
  and marker machinery is unchanged; two new markers
  (`CONTROL_ROOM_LOADED`, `CONTROL_ROOM_UNLOADED`) join
  `harness/markers.py` with the same constant-pinned-by-test discipline,
  and `run_stack` uses `CONTROL_ROOM_LOADED` as the gate it currently
  hangs off boot completing.
- CI smoke: `run_stack --ci` pins one full cycle -- boot roomless, load
  TEST, load/run/complete a Bit, unload TEST, load DEMO, verify fresh Arco
  (new pid), unload, clean exit, zero orphans.

## 8. Console and uplink surface

- **Rooms panel**: the config's valid-room list, per-room validation
  status, active room, Load/Unload controls (Unload on
  `wire.confirmTap`, same two-tap discipline as Abort). Room load progress
  renders on the same panel. Follows the front-end standing rule: the
  panel updates in place on `room_load_progress`; only a room card whose
  own declaration changed is rebuilt.
- **Gating**: every Bit control (Load picker, Run, Abort) disables outside
  ROOM_READY, driven by the `terrarium_state` carried on `state_changed`
  and the snapshot.
- **Uplink**: `load_room`/`unload_room` down-commands,
  `room_loaded`/`room_unloaded`/`room_load_failed` up-events, resync
  snapshot extended with terrarium state + active room. Same
  never-in-the-hot-loop rule (boundary rule 2).

## 9. What this deliberately does not change

- The Bit lifecycle state machine, `RoleTable`, registration, scoring
  classes, and the join flow: untouched.
- The cue path (`_dispatch_cues`, `cue_horizon`, `TimedQueue`), the
  one-shared-MIDI-stream property, render-once-slice-N: untouched.
- `control/triggers.py` keeps its current name and shape in this spec; the
  Function/Trigger rename is spec 3 (section 12).
- Both device transports; the websocket default.
- `control/` stays free of luxaeterna/pyarco/o2litepy module-level imports;
  `terrarium_config.py` is pure stdlib.

## 10. Provenance stamping

With multiple valid rooms and (eventually) external Bits, records must say
which room produced them. The composed device blob, `TriggerFired` records,
`Bit.result()` payloads as relayed by the uplink, and capture traces gain
`room_name` and `terrarium_config_version` (the config file's `schema` plus
a content hash) alongside the existing `bit_name`/`bit_version`
provenance. A score or trace from "DEMO under config v3" is never
conflatable with the same Bit under a different room.

## 11. Testing

Offline throughout, per the repo's load-bearing property. Key suites:

- `terrarium_config` parse/validate: schema v1 happy path, located errors
  (bad zone coverage, oversize block, unknown backend), TEST/DEMO example
  configs parse to profiles byte-equivalent to the current code-built ones
  (the migration guard -- pinned until the enum is deleted, then kept as
  golden fixtures).
- State machine gating: every refused transition in section 3, including
  `load_bit` in NO_ROOM and `unload_room` with a running Bit (without and
  with `force`).
- Two-stack teardown: a mid-`load_room` failure at each step unwinds only
  the room stack; Terrarium services survive; a second `load_room` after a
  failed one starts clean.
- Sweep: kills only recorded pids (fake process table), refuses on a
  foreign-claimant probe answer, pid-reuse guard.
- DevicePool cleared on unload; binding registry persistence round-trip by
  room name.
- Console/uplink: rooms panel in-place updates (node-identity discipline,
  DOM stub tests), gating, new events byte-shape pinned via
  `wire_json.dumps`.
- Doubles no more permissive than the real thing (boundary rule 5):
  the fake process table refuses to "kill" a pid it never spawned.

Live verification checklist (a real Arco, dev box):

1. Boot roomless; Console reachable; Bit controls disabled.
2. Load TEST from the panel; progress lines; fixtures bind; ambient light.
3. Load/run/complete a Bit; unload room; Arco gone (pid), NO_ROOM.
4. Load DEMO; fresh Arco pid; devices re-sync and rejoin via heartbeat.
5. SIGKILL the stack mid-room; relaunch; load-time sweep reaps the
   recorded orphans; ownership probe passes; room loads clean.
6. CLI shorthand `--room DEMO --bit MetronomeBit` end to end.

## 12. Follow-on specs (agreed direction, not built here)

Recorded so the sequence is explicit; each instantiates inside
`load_room`'s world:

- **Spec 2 -- Instruments and Fixtures.** Instrument as a first-class
  entity (elements = light-manifest v2 + ugen manifest, capabilities,
  functions, accepted triggers). A **Fixture is an Instrument a room
  loads**: the Instrument structure plus placement (blocks/zones) and
  device binding -- unifying with, not duplicating, today's `RoomFixture`.
  Rooms declare their instrument set in `terrarium.toml`; a standard
  Tuneshroom instrument definition exists once, instantiated per joined
  carrier device; a role grant binds the carried instrument into a Bit's
  requirement slot. Bits declare instrument **requirements as capability
  contracts**, resolved at `load_bit`, `BitLoadError` on no match.
- **Spec 3 -- Functions and the Trigger rename.** Today's `Trigger`
  becomes a `Function` (scripted kind); `Bit.cues(at)` becomes per-
  instrument generator Functions (one generator per element lane,
  last-start-wins, scripted Functions overlay rather than kill -- the
  behavior the `play_aurora` live verify already exhibited); declared
  stream Functions replace hardcoded verb-handler cc mappings. The name
  `Trigger` is reassigned to the sensing side: event triggers (device-side
  detection, **server-owned thresholds** derived from `capture/` traces,
  shipped in the binding blob) and stream triggers (server-side transform/
  fusion pipelines). Full rename, no dual vocabulary period.
- **Spec 4 -- External and bundled Bits.** `bit_paths` already lands in
  spec 1's config; this spec adds the versioned Bit API contract
  (`requires_terrarium_api` in `bit.toml`, checked at discovery), a
  dedicated Bits repo (new repo, name TBD -- not mm-tuneshroom, whose
  boundary excludes Terrarium-side logic), an `[assets]` manifest section
  resolved only through config, and the **bundling script design note**:
  single-archive format, integrity/provenance (an external bundle is code
  the venue box executes), and the sys.path-vs-venv-install question.

## Status

Spec written 2026-08-26. Implemented 2026-08-27 -- see
`docs/MM_TERRARIUM.md`'s *Terrarium lifecycle and config-defined rooms*
landed-subsystems entry for the as-built shape and file list.

Deliberate deviations from this spec's prose, recorded during execution:

- **`start_terrarium()`** is realized as `harness/terrarium_boot.py`'s
  existing `build()`/`main()` owning the Terrarium-scoped stack, rather
  than a new `control/`-level function -- the harness already owns
  transport/console construction, and a second constructor would
  duplicate its many-site tuple contract.
- **`--list-bits` now requires `--config`.** A Bit's shown
  `room_types`/loadability is meaningless without a resolved room set to
  check it against, so the CLI refuses `--list-bits` without `--config`
  rather than falling back to some default room shape.
- **`CaptureBit`'s live provenance construction is deferred**, not built
  in this slice.

Section 11's live-verification checklist (real Arco, dev box) has NOT
been run yet -- pending, unchecked:

- [ ] 1. Boot roomless; Console reachable; Bit controls disabled.
- [ ] 2. Load TEST from the panel; progress lines; fixtures bind; ambient
      light.
- [ ] 3. Load/run/complete a Bit; unload room; Arco gone (pid), NO_ROOM.
- [ ] 4. Load DEMO; fresh Arco pid; devices re-sync and rejoin via
      heartbeat.
- [ ] 5. SIGKILL the stack mid-room; relaunch; load-time sweep reaps the
      recorded orphans; ownership probe passes; room loads clean.
- [ ] 6. CLI shorthand `--room DEMO --bit MetronomeBit` end to end.
