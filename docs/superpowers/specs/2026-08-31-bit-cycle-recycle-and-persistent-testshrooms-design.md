# Bit-cycle room recycle, persistent Testshrooms, and console load fixes

Date: 2026-08-31
Status: draft, awaiting review

## Problem

Live UAT on 2026-08-31 surfaced three coupled defects around closing one
Bit and starting the next:

1. **Arco survives a Bit close.** Arco is room-scoped by design
   (`Terrarium.load_room()` spawns it onto `room_stack`; only
   `unload_room()` closes it). Aborting or completing a Bit leaves Arco
   running. Operator requirement: closing a Bit (and loading a new room)
   must shut everything down and relaunch, because a long-lived Arco is
   known-broken for round two anyway: upstream, only the first client
   after an Arco start gets working audio (`arco.initialize()` sends
   `/host/clear`, the audio re-open then fails with PortAudio -9988), and
   device clock sync after Control has connected is unreliable.

2. **Every console `load_bit` fails.** `console/static/bit.js`'s
   `overridesFromPairs()` returns `{ table: {...} }`, so the wire carries
   an overrides table literally named `"table"`, which
   `control/bit_config.py`'s `merge_overrides` rejects:
   `[table] unknown override table` (observed live in the console error
   log). This happens with the override fields empty or filled. The form
   also never expands dotted keys (`rhythm.bpm`) into the nested
   `{rhythm: {bpm: ...}}` shape `merge_overrides` expects, so it was
   doubly broken. Symptom as reported: "no load occurs, no bit loaded is
   present after loading a bit".

3. **Testshrooms cannot join a Bit loaded later.** `harness/o2_shroom.py`
   exits its main loop on `client.released` and `break`s on a join deny.
   So the moment one Bit closes, every Testshroom process is gone, and a
   Bit loaded from the Console afterwards has no devices left to join it.
   Testing a second Bit currently requires relaunching the device
   processes at the prompt.

## Approved direction (brainstorm 2026-08-31: options A+B+D)

- **A. Persistent Testshroom lobby mode** so a device survives Bit
  cycles and joins whatever the Console loads next.
- **B. Arco-per-bit-cycle**: every Bit close drives a full room recycle
  (unload_room then load_room of the same room), so each round gets a
  fresh Arco.
- **D. Console visibility**: fix the overrides bug and surface round
  outcomes so a silently vanished Bit is impossible to misread.

## Section 1: Console load fix (D, part 1; independent bugfix)

`overridesFromPairs()` builds and returns a nested overrides dict
directly (no `{table: ...}` wrapper):

- Empty form: send `overrides: null` (ConsoleAgent already treats absent
  overrides as none via `resolve_config(name, command.overrides)`;
  protocol parse must map null/missing to `None`).
- A filled pair `rhythm.bpm = 100` becomes `{"rhythm": {"bpm": 100}}`.
  Split on the FIRST dot only: left half is the table, remainder is the
  key. A key with no dot is refused client-side with an inline message
  (every override lives in a table; `merge_overrides` has no top-level
  scalars).
- Numeric coercion stays as is (finite Number, else string).

Server side is already correct and located (`merge_overrides` raises
`ManifestError` with the offending table name; ConsoleAgent turns it
into an `error_event`). No server change.

Tests: a JS-free unit test is not available for `bit.js`; cover the
protocol/agent side with a `LoadBitCommand` carrying
`{"rhythm": {"bpm": 90}}` resolving into a loaded MetronomeBit config,
and one carrying `null` overrides. The dotted-key expansion lives in one
small pure JS function; keep it factored so a future console JS test rig
can pin it.

This section lands first, as its own commit (arguably its own PR): it
unblocks all live testing and is independent of the lifecycle work.

## Section 2: Bit-cycle room recycle (B)

### Behavior

When a Bit leaves the engine (normal completion or abort) while a Room
is loaded, Terrarium recycles the room: `unload_room()` then
`load_room(<same name>)`. Every round therefore starts against a fresh
Arco, a fresh simulator set, and a fresh room stack. This also covers
"loading a new room": `load_room` of a different name already gets a
fresh Arco today; the recycle closes the same-bit-cycle gap.

### Mechanism

- New `Terrarium.recycle_room() -> str | None` (None on success, reason
  string on refusal, matching load_room/unload_room's never-raises
  convention): refuses unless ROOM_READY; runs `unload_room(force=True)`
  then `load_room(name)` with the previously loaded name. Progress
  events flow through the existing observer machinery, so the Console
  shows the recycle live for free.
- The recycle is driven from the harness round loop, not from inside
  GameServer: `_serve_rounds` (and main()'s round-1 completion path in
  serve mode) calls `terrarium.recycle_room()` after each round returns
  to IDLE, before `_wait_for_load`. GameServer stays Bit-agnostic and
  room-agnostic; the engine's IDLE-return contract is unchanged.
  ConsoleAgent needs no new command: an operator abort already lands the
  round loop in the same place.
- One-shot (non-serve) mode is unchanged: the process tears everything
  down on exit already, which satisfies "Arco terminates after closing a
  bit" for that mode.

### The o2lite-mode consequence (the hard part)

Control itself is a client of the Arco hub it is about to kill, twice
over: the `O2LiteTransport` (`game`/`actl` services on pyarco's o2lite
singleton) and `ArcoSynthPool` (started once in `build()`, holding a
`Flsyn` and voices on the dead Arco). A recycle must therefore:

1. Before `unload_room()`: stop the transport (`transport.stop()`, which
   detaches but does not close pyarco's connection object) and quiesce
   the audio pool (`ArcoSynthPool` has `start()` but no `stop()` today;
   the recycle adds one that drops its ugen handles without touching the
   wire, since every ugen dies with Arco regardless and AudioBridge
   already frees voices at Bit unload).
2. After `load_room()` respawns Arco: re-run the connect sequence the
   process ran at launch, in the same order: `arco.initialize()` (or
   pool restart, which performs it), wait for clock sync,
   `transport.start(o2lite)` (which re-claims `actl,game` and re-runs
   the ownership probe).

Open question the plan must resolve with a live probe before freezing
implementation detail: whether o2litepy's module-level singleton can be
re-initialized in-process against a fresh hub (its discovery layer
auto-reconnects for devices, per `reconnect_recheck`'s bridge_id
machinery, but Control's path through `arco.initialize()` has never been
run twice in one process). If it cannot, the fallback design is the
supervisor pattern the repo already owns: `harness/run_stack.py`-style
process supervision, where the recycle restarts the whole
terrarium_boot child rather than reconnecting in-process. The behavior
contract above (fresh Arco per round, Console shows the recycle) is
identical under either mechanism; only the seam moves.

Websocket mode has none of this: the devicelink server is
process-scoped, the pool is the only Arco client, and the existing
`_RoomWiring` observer already rewires the agent across
load_room/unload_room.

### Interaction with run records

The recycle path reuses `load_room`'s existing sweep and owned-pid
recording unchanged; each round's Arco/simulator pids append to the same
run's `procs.jsonl`.

## Section 3: Persistent Testshroom lobby mode (A)

### Behavior

`harness/o2_shroom.py` gains `--persist`:

- On `/release`: instead of exiting, the device prints a round summary
  (the current lateness report, reset per round), clears per-round
  client state (`config`, `released`, `last_deny`, `last_error`, session
  visuals via the existing release fade), and returns to the lobby:
  hello heartbeat plus join retry (`--join-retry` semantics, re-armed).
  It joins the next Bit the Console loads, whenever that is.
- On deny while persisting: print the deny once, keep the device alive,
  and keep retrying at the join-retry cadence (a deny during RUNNING for
  a scored role becomes joinable again next round; today's
  exit-on-deny remains correct only for one-shot mode).
- Hub restart survival: the recycle in Section 2 kills the hub under
  every persistent device. The device rides o2litepy's auto-reconnect
  (the existing `reconnect_recheck` already detects a changed bridge_id
  and re-verifies its service), waits for clock re-sync (`time_get()`
  can go invalid across a hub restart; the lobby loop tolerates that
  rather than treating it as fatal), then resumes hello+join retry.
  The device's WebSim canvas stays up throughout, so an operator's
  browser tabs survive rounds.
- Default off. `--persist` is opt-in; every existing invocation and
  test is byte-identical without it. `run_stack` grows a passthrough so
  a serve-mode stack launches its Testshrooms persistent.

`--no-join` (Room simulator) is unaffected: fixtures are respawned by
`load_room` per cycle, so the simulator keeps its exit-on-release
lifecycle.

### Client state seam

`ShroomClient` gains a `reset_for_lobby()` method owning exactly which
fields a round clears (`config`, `released`, deny/error latches), so
main()'s loop does not reach into client internals and the reset is unit
testable offline.

## Section 4: Round outcome visibility (D, part 2)

- `_serve_rounds` outcomes become Console events: ConsoleAgent
  broadcasts a `round_ended` event carrying bit name and reason
  (`completed`, `timeout-abort` with the scored count it saw, operator
  abort), plus the existing state machinery. The console event log
  renders it; no new panel.
- The recycle stages already appear via `room_load_progress`.
- Control's stdout prints the same line (markers module), so a
  console-less run reads identically.

## Testing

Offline (the suite stays fully offline):

- `tests/test_terrarium.py`: recycle_room refusals (not ROOM_READY),
  success path asserts a fresh arco-process instance and a rebuilt
  room_stack (mirror `test_terrarium_cycle`'s fresh-instance assertion).
- `tests/test_terrarium_boot.py`: serve-round loop drives a fake
  terrarium's recycle between rounds; o2lite-mode ordering pin:
  transport stop and pool stop happen before unload, restart after
  load, in that order (fakes record the sequence).
- Console overrides: protocol/agent tests per Section 1.
- `tests/test_shroom_client.py`: `reset_for_lobby()` clears exactly the
  per-round fields and preserves the backend/session.
- o2_shroom lobby loop: factor the per-tick decision (join-retry
  re-arm, deny handling under persist) into pure helpers with unit
  tests, per this repo's socket-free harness convention.

Live acceptance (not automatable offline):

1. Boot serve mode with console, o2lite transport, one persistent
   Testshroom.
2. Load TestBit from the Console with an override (`rhythm.bpm`-style on
   Metronome for the overrides fix), see it load (bug 2 gone).
3. Abort it. Watch the room recycle: Arco process replaced (new pid),
   simulator replaced, Console shows the progress stages.
4. Load MetronomeBit. The same Testshroom joins it with no relaunch at
   the prompt, and audio works in round two (the upstream
   first-client-only trap is what this proves out).

## Out of scope

- Fixing the upstream Arco defects themselves (`/host/clear`, no
  message-based quit). The recycle designs around them.
- Websocket-mode persistent phones (real phones re-scan a QR per bit;
  lobby mode is a Testshroom testing affordance).
- Scoring framework, fairyring.
