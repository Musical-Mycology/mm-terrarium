# Bit packaging and launch — design

**Date:** 2026-08-21
**Status:** Implemented
**Depends on:** MetronomeBit slice (PR #44), operator/harness handoff slice,
wire-JSON slice.

## 1. Problem

A Bit today is a Python class plus knowledge scattered across the harness:
`harness/terrarium_boot.py` hardcodes the class map and the `--bit` choices
list, `harness/run_stack.py` hardcodes a bit-to-join-node dict, TestBit's
run duration needs a `_TimedTestBit` subclass hack to reach its constructor,
and MetronomeBit's gameplay knobs (BPM, grading window, input offset) are
class constants nothing outside the class can read. Adding a Bit means
editing three files; nothing outside Python can enumerate what Bits exist or
what they need.

The goal: a Bit is a **distinct package** that a launcher — the harness, the
Console, or a future third-party app over the uplink — can discover, read,
configure, and launch, with a defaults framework expected to grow.

## 2. Shape: manifest + code directory

Each Bit becomes a directory under `bits/`:

```
bits/
  metronome/
    bit.toml          # declarative manifest (this spec's schema)
    metronome_bit.py  # the Bit class, unchanged in role
  test/
    bit.toml
    test_bit.py
  capture/
    bit.toml
    capture_bit.py
```

- **Discovery never imports Bit code.** `control/bit_registry.py` scans
  `bits/*/bit.toml` with stdlib `tomllib`. A broken manifest disables that
  one package (collected as a located error, reported by `--list-bits` and
  the Console) and never breaks discovery of the others. The Bit's module is
  imported only at `load_bit`, via the manifest's `entry = "module:Class"`.
- All three existing Bits migrate this slice. No legacy unpackaged path
  survives; the suite pins behavior across the move.
- External/installable packages are out of scope but the contract is the
  manifest, so a later slice can add search paths without changing consumers.

## 3. Manifest schema v1 (`bit.toml`)

Validated at discovery (structure) and at `load_bit` (semantics), in
`control/role_config.py`'s located-error style: a typo'd manifest is a
`BitLoadError` naming the file and key, never a device-side surprise.
Unknown top-level tables and keys **warn, never fail** — the growth path.

```toml
[bit]
name = "MetronomeBit"          # registry key; must be unique across packages
version = "1.0.0"
description = "Call-and-response rhythm game"
entry = "metronome_bit:MetronomeBit"
kind = "r_game"                # music | r_game | game | tool | ambient
author = "Musical Mycology"
min_terrarium = "0.1"          # BitLoadError if the engine is older

[launch]
room_types = ["DEMO"]          # names of control.rooms.RoomType
default_room_type = "DEMO"
default_devices = 2            # run_stack's --devices default
setup_seconds = 20             # SETUP hold default
expected_run_seconds = 45      # CI bound hint (fixes the truncated-finale trap)
transport = "any"              # any | o2lite  (o2lite = meaningless without Arco)

[launch.nodes]                 # role -> Registration Node (kills run_stack's dict)
player = "METRO_PLAYER_NODE"

[start]                        # start-condition table, one `when` + params
when = "players"               # immediate | players | operator | scheduled(later)
min_scored = 1
timeout_seconds = 120          # after timeout:
on_timeout = "start"           # start | abort

[console]
display_name = "Metronome"
notes = "Players tap back the 4-beat call. Watch tap_errors_ms."
hidden = false                 # true for reference fixtures (TestBit)

[results]                      # declarative shape of result(); no consumer yet
keys = ["phrases_won", "tap_errors_ms"]

[assets]                       # files the package ships; paths package-relative
# soundfont = "FluidR3_GM.sf2" # none shipped yet; reserved

[rhythm]                       # r_game kinds only
bpm = 100
beats_per_cycle = 8
grading_window_ms = 50
input_offset_ms = 0

[ambient]                      # ambient kinds only
# jam_control = true
# default_pattern = "aurora_drift"

[defaults]                     # bit-specific knobs -> BitConfig.extras
# run_duration_seconds = 2.0   # (TestBit's)
```

### Kind semantics

- `music` — guided musical experience for a player.
- `r_game` — rhythm game on a metronome loop; carries `[rhythm]`, the
  contract the planned rhythm-game support tooling reads.
- `game` — standard game loop, non-rhythm.
- `tool` — operator instrument, includes tests/capture; never scored.
- `ambient` — room-holding patterns; may allow Jam control of the room;
  carries `[ambient]`. Candidate for a future idle/default slot between games.

An unknown `kind` fails at load (it is enumerated, consumers key behavior
off it). A kind-specific table on the wrong kind warns.

## 4. `BitConfig`: how defaults reach the Bit

`control/bit_config.py` — a frozen dataclass tree parsed from the manifest,
with defaults filled and types checked:

```
BitConfig
  .identity   (name, version, kind, ...)
  .launch     (room_types, nodes, setup_seconds, ...)
  .start      (StartCondition)
  .console    (display_name, notes, hidden)
  .results    (keys)
  .assets     (resolved absolute paths)
  .rhythm     (RhythmConfig | None)
  .ambient    (AmbientConfig | None)
  .extras     (the [defaults] table, dict, for bit-specific knobs)
```

**One constructor signature forever: `Bit(config: BitConfig)`.** The `Bit`
base class stores it; existing hooks are unchanged. This deletes
`_TimedTestBit`: TestBit reads `config.extras["run_duration_seconds"]`, and
`terrarium_boot --run-seconds` becomes an override merged into the config
rather than a subclass. MetronomeBit's class constants move to `[rhythm]`
(constants remain as the dataclass defaults, so behavior is byte-identical
with an empty table).

Override precedence, one merge implemented once:
**manifest defaults < profile file < explicit CLI flags / uplink overrides.**
The merged, resolved config is what the Console displays — an operator sees
what the run will actually use, not what the file says.

## 5. Start conditions

`StartCondition` is data; **the engine does not change**. The harness (and
any launcher) already holds in SETUP and watches registration via the
observer seam (the operator/harness handoff slice made the hold yield to
state changes and drain Arco's pty — that machinery is reused, not forked).

- `immediate` — today's behavior; `setup_seconds` still applies as the hold.
- `players` — hold until `min_scored` scored registrations exist (counts via
  the existing `RegistrationState.counts()` observer path), else until
  `timeout_seconds`, then `on_timeout` (`start` or `abort`).
- `operator` — hold until Console Run (already works; now declarable).
- `scheduled` — reserved name, rejected in v1 with a clear message.

`_wait_in_setup` grows a condition parameter and returns the existing reason
strings plus `"players-met"` / `"timeout-start"` / `"timeout-abort"`.

## 6. Launch script changes

- **`run_stack` / `terrarium_boot`:** `--bit` accepts any discovered package
  name (no `choices=` list); node, device count, room type, setup and CI
  bounds default from the manifest; `--list-bits` prints the registry
  (name, version, kind, rooms, start condition, description, plus any
  disabled packages and why). All per-bit dicts and the choices lists are
  deleted.
- **`--profile venue.toml`:** a run profile selecting `bit`, `room_type`,
  `devices`, `console_port`, and a `[bit.overrides]` table merged into
  `BitConfig` ahead of CLI flags. Same TOML parser, located errors. Profiles
  live in-repo (`profiles/`) or on the venue box.
- CI note: `--ci` with no `--seconds` derives the bound from
  `expected_run_seconds` plus the setup hold, so a Bit's finale can no
  longer be silently truncated by a default.

## 7. Console and uplink (the third-party launch seam)

Shared protocol change in `uplink/protocol.py`, consumed by both (single
source of truth, as today):

- **`list_bits`** command → each package's manifest view (identity, launch,
  start, console blocks, resolved — with `hidden` honored for the venue
  Console listing but not for the uplink, which is an operator surface).
- **`load_bit`** gains `bit` (package name) and optional `overrides`
  (merged into `BitConfig` with the same precedence rules; refused with a
  located error on unknown keys, same as the profile).
- **`bit_completed`** is stamped with `bit_name`, `bit_version`, and the
  `result()` payload; `[results].keys` is advisory metadata carried in
  `list_bits`, not enforced on the payload (no consumer scores yet).
- All of it rides `wire_json.dumps()` like every outbound payload.
- **No auth added.** The uplink still targets a broker that does not exist;
  auth/identity is mm-fairyring's design question (unchanged from the uplink
  spec). The Console keeps its trusted-LAN model.

This is the whole third-party story for now: an app that speaks the uplink
protocol can enumerate packaged Bits, launch one with overrides, watch
state, and receive results — with fairyring later brokering/authenticating
that connection.

## 8. Boundary rules honored

- Engine stays Bit-agnostic: start conditions are evaluated by the
  launcher/harness through the observer seam, never by `GameServer`.
- `control/` stays dependency-free at module level; `tomllib` is stdlib.
- Discovery executes no Bit code; a hostile or broken manifest can only
  disable its own package.
- Suite stays fully offline; registry, config merge, and start conditions
  are pure and tested against fixture packages under `tests/`.

## 9. Out of scope (named so nobody rediscovers them)

- Installable/external Bit distributions and search paths.
- The rhythm-game support tooling itself ([rhythm] is its contract only).
- A real scoring framework ([results] is declarative only).
- `scheduled` start conditions.
- Any auth on console/uplink.
- Shipping actual assets (the [assets] table is reserved; the soundfont
  stays a harness concern this slice).

## 10. Live verification plan

- `run_stack --list-bits` shows all three packages.
- `run_stack --ci --bit MetronomeBit --room-type DEMO --devices 2` green
  with zero per-bit flags beyond `--bit` (nodes/counts/bounds from manifest).
- A `players` start condition run: devices join during hold, run starts on
  threshold, not on timer.
- Console `list_bits` renders; `load_bit` by name from the panel works.

## Status (2026-08-21, Task 13 live verification)

Live-verified on Mycological (dev box, Arco reachable):

- `--list-bits` (`runs/` none created; stdout only): all three packages
  (`CaptureBit`, `MetronomeBit`, `TestBit`) listed with correct kind/room
  types/start condition, no manifest errors.
- `--ci --bit MetronomeBit --devices 2` with **no** `--room-type`/`--node`:
  the manifest alone drives `default_room_type = DEMO` and
  `launch.nodes.player = METRO_PLAYER_NODE`; confirmed live (control.log:
  `join granted: ie2 -> player (scored) via METRO_PLAYER_NODE`). Node/room
  resolution from the manifest is verified.
  Caveat: this exact invocation exits 1, reproducibly. MetronomeBit's
  manifest declares `start.min_scored = 1`, so with the "players" start
  condition the run transitions out of SETUP the instant the *first*
  scored device joins (`control/start_condition.py`, the SETUP hold in
  `harness/terrarium_boot.py`). `--devices 2` then spawns a second
  simulated device that arrives after registration has already closed
  ("registration closed for scored roles"), which `run_stack` reports as
  a failed device-join stage. This is the start-condition machinery
  working as designed (§5), not a regression from this slice — but it
  means `--devices 2` is the wrong shape for a min_scored=1 Bit's CI
  smoke test; `--devices 1` (or a manifest/profile with `min_scored = 2`,
  as `profiles/dev-metronome.toml` uses) is the correct invocation. Filed
  as an observation, not fixed here (out of scope for a docs+verify task).
- `players` start condition, via
  `run_stack --profile profiles/dev-metronome.toml --seconds 240`
  (profile overrides `min_scored = 2`): control.log shows both `ie1` and
  `ie2` granted scored joins within ~0.5s of SETUP opening (a 90s hold),
  and gameplay starts immediately after the second join — confirmed via
  `terrarium_boot.py`'s `players-met` path, not `expired`/timeout. Threshold-
  driven start is verified live.
  Caveat: the overall run still exits 1, because MetronomeBit's own
  gameplay (4 cycles at the profile's 80 BPM) finishes in well under the
  requested `--seconds 240` hold, and `run_stack` treats the child's early,
  clean exit (code 0) during the hold as a `child-exited` stage failure.
  Teardown itself is clean (no orphaned processes; Arco releases both
  devices and exits). This is a `--seconds`-vs-game-length mismatch in the
  test invocation, unrelated to Task 13's changes.
- Console wire check: a standalone websocket client (`websockets` from
  `.venv`) connected to `ws://127.0.0.1:<port>/ws` during a
  `--console-port 8901` run and received a `bits_listed` event
  (`CaptureBit`/`MetronomeBit`/`TestBit`, each with kind/room
  types/start condition) in the connect snapshot. Verified live.
- Full suite: `pytest tests -q` → 1254 passed, 1 skipped.

Not verified live (offline-only, per house style): the browser Console UI
itself (`renderBits`) was not opened in a real browser this pass — only the
underlying `bits_listed` wire event was checked with a raw websocket client.
Everything else in this section was exercised end-to-end on real
Room/Arco/DeviceLink processes, not mocked.
