# Console load stabilization: load-only loads, RESTART/ABORT, All-target Stop

**Date:** 2026-09-01
**Status:** Approved design, pre-implementation
**Driver:** First sustained live operator session (2026-09-01) surfaced four
defects in the serve-mode console flow. This spec stabilizes the load path.

## Defects observed live

1. Loading TestBit appeared to crash Arco and spawn an extra Testshroom.
   Root cause analysis: TestBit self-completes after 2 s
   (`run_duration_seconds = 2.0`, `when = "immediate"`), so the round-end
   bit-cycle recycle (tear down Arco, restart Arco, respawn Room simulators)
   fired 2 s after Load and read as a load crash. The recycle's core
   assumption (`arco.initialize()` can run a second time in one OS process)
   was documented as live-unproven; this session was its first live
   exercise. The extra Testshroom was `run_stack`'s per-round device respawn
   (`ie1-r2`), which is not gated off under `--persist-shrooms`, so a
   persistent device AND a respawned one both arrived.
2. Stop did not stop sound. The MuteCue audio half (expression 0 on the
   room voice, cue purge, breath skip) had never been live-verified, and a
   failed recycle can leave the pool quiesced with no hub, so Stop fires
   into dead handles. Additionally the deep-dive's claim that MetronomeBit
   declares its own `stop` is stale: the current source declares none.
3. Flash/Stop/Ping could not act on IE1. Spawned Testshrooms declare no
   carried instrument (`--instrument` defaults to None everywhere and
   `run_stack` forwards nothing), so they resolve to DEFAULTSHROOM, which
   has no audio capability: built-in `ping` resolves nothing and is
   silently skipped; `flash` loses its chime. The light halves were also
   never live-verified.
4. MetronomeBit needs to come out of the loadable set pending redesign,
   and there is no mechanism for that: `hidden = true` only affects a
   list-view filter the Console does not use, and every discovered
   `bits/*/bit.toml` is loadable via `lazy_class_map()`.

## Decisions

All confirmed with the operator (Chris) during brainstorm, 2026-09-01:

- Remove the automatic per-round Arco recycle; verify live whether round-2
  audio actually degrades on a long-lived Arco before deciding the
  machinery's final fate.
- Console `load_bit` loads the Bit and nothing else: no device respawn, no
  room churn.
- The surface picker's "Room" aggregate entry becomes **All** (room
  fixtures plus every connected instrument). Per-surface entries remain.
- Spawned Testshrooms declare a new `testshroom` carried instrument with
  `audio.samples` so ping and flash-chime resolve.
- New **RESTART** console button: soft cycle, reload the currently loaded
  Bit, Arco untouched.
- **ABORT** semantics change: hard stop, ends Arco (room down, `NO_ROOM`);
  the operator reloads the room to continue. The ~15 s Arco respawn cost
  on the next Load Room is accepted; RESTART covers the routine case.
- MetronomeBit is disabled via a new `enabled = false` manifest key, not
  deleted (its test suite keeps passing; re-enabling is a one-line flip).

## Design

### 1. `enabled = false` manifest key

- `[bit] enabled = false` in `bit.toml` (default `true`; parsed in
  `control/bit_config.py` alongside the existing `[bit]` keys).
- `BitRegistry.scan` still discovers and parses a disabled package (its
  tests and direct imports keep working), but:
  - `lazy_class_map()` excludes it, so neither the Console nor `--bit`
    can load it;
  - `list_view()` rows carry `enabled`, the Console bit cards omit
    disabled bits, and `--list-bits` prints them with a `disabled` marker
    so they are not invisible;
  - `resolve_config()` on a disabled bit returns a located refusal (it is
    the loadable-ness authority for `run_stack`'s `--bit` path too).
- `bits/metronome/bit.toml` gains `enabled = false`. No other Metronome
  change; the redesign is a separate future effort.

### 2. Load loads only the Bit

- Delete `run_stack`'s per-round device respawn machinery: the
  `CONTROL_ROUND_LOADED`-driven queue, the respawn spawn path, and the
  round-numbered `ie<k>-r<N>` naming. The `CONTROL_ROUND_LOADED` marker
  itself stays (log visibility, and `terrarium_boot` still emits it).
- `run_stack` passes `--persist` to every spawned Testshroom **by
  default**; new `--no-persist-shrooms` opts out. Devices are created
  exactly once, at stack launch, from `--devices N`, and live in the
  lobby between rounds.
- `device_command()`'s `dev`/`node` override parameters (which existed
  for respawn) are removed with their only caller.

### 3. No automatic Arco churn

- Remove the `recycle()` invocations from `_serve_rounds`'s round-end
  and from `main()`'s round-1 call sites in `harness/terrarium_boot.py`.
- `Terrarium.recycle_room()`, `_recycle_room()`, and
  `_restart_room_clients()` remain as callable seams but nothing invokes
  them automatically. They are explicitly candidates for deletion in a
  follow-up once the live verdict is in; the failed-recycle recovery
  bookkeeping (`clients_stopped`) simplifies accordingly.
- The live session (section 8) answers the open question: if round-2+
  audio on a long-lived Arco is healthy, the recycle machinery goes; if
  it degrades, the escape hatch is the new heavyweight ABORT (room down,
  fresh Arco on next Load Room), and a flat defect report on
  `arco.initialize()` second-run behavior goes upstream to Roger.

### 4. The All target

- The Console surface picker's aggregate entry is renamed **All**,
  sentinel `@all` (replacing the operator-facing `@room` entry; the
  internal ROOM cue target and Bit-declared ROOM-targeted scripts are
  untouched on the wire).
- `GameServer.fire_function` resolves `@all` engine-side: iterate the
  Room's canonical dev plus every connected device through the existing
  ladder. Devices that resolve nothing stay skipped-and-logged. One
  aggregate `FunctionFired` record (not one per surface) so the Console
  log is not spammed.
- Stop fired at All: mute the Room voice, mute every device, purge all
  pending timed/play cues (`TimedQueue.purge` with an always-true
  predicate). The existing any-non-mute-fire-un-mutes rule is unchanged
  per surface.
- `console/static/functions.js`: picker offers All first, then each
  surface; compatibility filtering treats All as compatible when at
  least one underlying surface is.

### 5. The `testshroom` carried instrument

- New `instruments/testshroom.toml`: 12 px, `light.pixels`,
  `gesture.tap`, `gesture.tilt`, `audio.samples` (the sim genuinely
  plays chime/click WAVs via afplay), **no** `audio.mic`; all four
  `accepted_cues`; tap/shake thresholds copied from DEFAULTSHROOM (same
  guessed-provenance caveat).
- `harness/o2_shroom.py`'s `--instrument` default becomes
  `"testshroom"` (was None). `ShroomClient`'s constructor default stays
  `None`: the Room simulator path stays undeclared.
- Result: built-in `ping` and the flash chime resolve on IE1; the
  Console compatibility filter offers all three built-ins for a
  Testshroom.

### 6. RESTART (new console button)

- Placement: next to Abort in the merged control bar (the Bits panel
  owns load/run/abort/restart).
- Semantics: soft cycle. Capture the loaded Bit's name and resolved
  overrides, `gs.abort()` (bit hooks run, devices released, voices
  freed), then `load_bit` with the same name and overrides. Arco and the
  Room are untouched. The new round opens its normal SETUP window per
  the manifest; persistent Testshrooms rejoin from the lobby on their
  own.
- Implementation: a ConsoleAgent-level `restart_bit` command composing
  existing engine calls. No new engine states; the engine stays
  Bit-agnostic. `GameServer` must retain the loaded bit's name and
  resolved config for the reload (it already knows `bit_name`; the
  config handle is kept alongside it at `load_bit` time).
- Gating: valid only while a Bit is loaded (LOADED/SETUP/RUNNING);
  otherwise a refusal reason string, never a raise (house convention).

### 7. ABORT (semantics change: hard stop)

- Console abort now runs `gs.abort()` then
  `terrarium.unload_room(force=True)` when a Terrarium is wired
  (`terrarium=None` callers keep today's bit-only abort: zero behavior
  change for pre-Terrarium embeddings and tests).
- Post-state: `NO_ROOM`. The Console's room panel is the path back
  (Load Room, ~15 s Arco spawn, then Load Bit). This is the operator's
  guaranteed-silence panic button: the Arco process is gone, so sound
  physically cannot continue, a guarantee no mute can match.
- `harness/terrarium_boot.py`'s serve loop must treat a round that ends
  with the room gone as a fall-through to the existing `NO_ROOM` wait
  (`_serve_roomless` shape), not as an error. This is the trickiest
  integration point; the round-outcome plumbing in `_serve_rounds` and
  `_wait_for_load` is written to tolerate `TerrariumState.NO_ROOM`
  appearing mid-loop.

### 8. Live verification protocol (after the structural slices land)

One live session on MYCOLOGICAL, `run_stack --open --devices 1`:

1. Load TestBit from the Console: confirm no Arco churn at load or at
   the 2 s self-complete, no extra Testshroom appears, the persistent
   device rejoins on the next round.
2. Flash IE1 (white 5 s + chime), Ping IE1 (chime), Stop IE1 (dark +
   silent, next play un-mutes).
3. Stop at All while the TestBit drone sounds: everything dark and
   silent, cues purged.
4. RESTART TestBit five times consecutively: audio health verdict each
   cycle. This answers the recycle question (section 3).
5. ABORT mid-round: room down, silence, Console shows NO_ROOM; Load
   Room brings a fresh Arco; next round works with sound.

Any step that fails gets its own systematic-debugging pass rather than a
guessed fix. Failures in step 4 produce the upstream report to Roger
(flat defect statement, per house convention for upstream reports).

## Testing (offline, per slice)

- Registry: disabled bits excluded from `lazy_class_map()` and
  `resolve_config()`, flagged in `list_view()`; Console snapshot omits
  them; `--list-bits` prints the disabled marker.
- run_stack: no respawn on `CONTROL_ROUND_LOADED`; `--persist` forwarded
  by default; `--no-persist-shrooms` honored.
- terrarium_boot: `_serve_rounds` round-end performs no recycle;
  round-end with `NO_ROOM` falls through to the roomless wait.
- fire_function: `@all` fan-out (room + devices, skip-and-log
  preserved, single aggregate `FunctionFired`); Stop-at-All purges all
  queues and mutes all surfaces.
- Instrument: `testshroom.toml` passes `validate_instrument`;
  `o2_shroom` declares it by default; hello lands it at `args[3]`;
  composed role blob carries it.
- Console: `restart_bit` command round-trip (refusal with nothing
  loaded; reload same name/overrides when loaded); abort command
  triggers room unload when Terrarium wired, bit-only abort when not.

## Out of scope

- The MetronomeBit redesign (its own future brainstorm).
- Device-side audio streaming (`o2audioio`-out), unchanged deferral.
- The mm-tuneshroom ensemble/instrument declaration (separate session,
  already in flight).
- Deleting the recycle machinery (follow-up after the live verdict).

## Doc updates at closeout

`docs/MM_TERRARIUM.md`: supersede notes on the bit-cycle recycle entry,
the per-round respawn note in the console-operator-rounds entry, the
four-operator-triggers entry (Room picker -> All), abort semantics in the
lifecycle entry, and the stale MetronomeBit-declares-stop claim. Via
`mm-deepdive-sync`.
