# Console operator rounds — design

**Date:** 2026-08-21
**Status:** Implemented
**Depends on:** Bit packaging and launch slice (same branch, PR #46).
**Origin:** First interactive UAT of the Bits panel found three defects:
after a console abort no Bit can be loaded; the Bits panel duplicates the
old crude load interface instead of replacing it; load→abort→load cycles
do not work at all.

## 1. Root causes (measured, not guessed)

1. **The harness is a one-shot driver.** `_serve_until_done`
   (`harness/terrarium_boot.py`) returns `"completed"` the moment
   `gs.state == IDLE` with no devices mid-fade — and `abort()` lands in
   IDLE — so a console abort tears down Arco, the simulators and the
   Console itself. "Cannot load bits" was the stack being gone.
2. **The engine registry holds one entry.** `main()` passes
   `{bit: registry.bit_class(bit)}`, so `load_bit` on any other discovered
   name raises `BitLoadError` (KeyError). The panel lists three Bits;
   only the CLI-selected one is loadable.

## 2. Decisions

- **Serve mode.** `terrarium_boot --serve`: on IDLE the harness keeps
  ticking (Arco, devicelink/o2 agent, console agent all stay up) and waits
  for the next console `load_bit`; it exits only on Ctrl-C, parent-gone, or
  Arco death. Giving `--console-port` interactively implies `--serve`
  (a console with no rounds is pointless); `--ci` keeps today's
  exit-on-complete semantics and refuses `--serve`.
- **Rounds honor start conditions.** Each console-driven load re-enters the
  same hold logic the first round uses, reading the loaded Bit's resolved
  config off `gs.bit.config` (the ConsoleAgent already resolves overrides
  into the config it passes to `load_bit`): `players` / `operator` /
  `immediate` per manifest+overrides. The hold still yields to the operator
  pressing Run (state-change watch) and still drains Arco's pty.
- **Lazy full registry.** The engine receives a Mapping over ALL discovered
  packages that imports a Bit's module only on `__getitem__`
  (`BitRegistry.lazy_class_map()`). Discovery still never imports Bit
  code; a broken package fails at `load_bit` as the existing
  `BitLoadError`, not at boot.
- **One top control bar.** The old crude load interface and the new Bits
  panel merge into a single "Bits" section at the TOP of the Console page:
  current bit + engine state + Run/Abort on the left, the discovered-bit
  cards (Load buttons) beside/below. The duplicated structure is deleted.
  Room / Triggers / Registration / Devices / Event log follow underneath.

## 3. Non-goals

- Graceful Tuneshroom reconnection across rounds (devices that joined round
  N re-joining round N+1) — explicitly deferred by the user; a released
  device must simply not wedge the next round.
- run_stack `--ci` multi-round exercising; CI stays one-shot.
- Any engine change: the round loop, holds, and registry stay in the
  harness/registry layer. `GameServer` is untouched.

## 4. Verification

- Offline: round-loop unit tests against fake gs/agents (serve mode:
  IDLE→load→SETUP→run→IDLE→load again; abort mid-hold and mid-run both
  return to waiting, never exit); lazy-map test (imports only on access,
  broken package → BitLoadError); console JS tests for the merged bar
  (no duplicate load controls remain).
- Live (Mycological): run_stack with console; from the panel: load
  MetronomeBit → abort → load TestBit → run → complete → load again; stack
  stays up throughout; Ctrl-C tears down clean.

## Status (2026-08-21, Task 5)

All four implementation tasks landed (`lazy_class_map()`, the `--serve`
round loop, `run_stack --serve` forwarding, the merged control bar) and
the full suite is green: 1267 passed, 1 skipped.

**Live-verified, with evidence:**
- Started `.venv/bin/python -m harness.run_stack --console-port 0
  --devices 0 --room-type DEMO --setup-seconds 90` on this box (Arco
  spawned by `run_stack` itself, run dir `runs/20260821-123104/`).
  `--room-type DEMO` was needed because MetronomeBit is DEMO-only and the
  stack's CLI default room type is TEST — matches the brief's documented
  caveat, not a workaround.
- Drove the full round cycle over the console `/ws` with a small
  `websockets` client (headless, no browser): initial snapshot showed
  `state: SETUP, loaded_bit: TestBit`; sent `abort` → `IDLE`; sent
  `load_bit MetronomeBit` → `state_changed` with `loaded_bit:
  "MetronomeBit"`; sent `abort` → `IDLE`; sent `load_bit TestBit` →
  loaded, `SETUP`; TestBit's own manifest-driven SETUP window is short
  enough that it moved into `RUNNING` before the explicit `run` command
  landed (a genuine race against `_serve_rounds`'s per-round hold, not a
  bug — see the harness doc's write-up); the round then completed
  (`RUNNING` → `COMPLETING` → `UNLOADING` → `IDLE`) with `bit_completed`
  observed; sent `load_bit MetronomeBit` again → loaded successfully.
  Both `run_stack` and `terrarium_boot` processes stayed alive
  (confirmed via `ps`) across every step — the stack never restarted.
- Sent `SIGINT` to `run_stack` afterward: clean exit, `pgrep -f
  "o2_shroom|terrarium_boot|room_simulator"` returned empty (exit 1) —
  zero orphaned processes.
- Full event transcript, driver script, and process logs are recorded in
  `.superpowers/sdd/2026-08-21-console-operator-rounds/task-5-report.md`.

**Not live-verified (remaining for the user):**
- Real-browser click-through of the merged Console control bar (clicking
  Load/Run/Abort in an actual browser tab, watching the header update
  live). Only the underlying `/ws` protocol was exercised headlessly here.
- Confirming every discovered Bit's Load works from the browser UI
  specifically (CaptureBit was listed by `bits_listed` but not
  load-tested in this pass — it requires a phone-side capture client to
  do anything interesting once loaded).
- Interactive verification that an operator can drive rounds indefinitely
  over a long session (this run exercised exactly the round sequence in
  the brief, not an extended soak).

## Live UAT follow-up (2026-08-21, after PR #47)

**Reported:** after a Console abort, "Arco closes" and no Bit can be loaded
(`runs/20260821-152658`). **Root cause, traced not guessed:** Arco never
failed. The released simulated device exits with code 0 by design
(`o2_shroom` loops `while not client.released`), `run_stack._hold` read
that as `child-exited`, SIGTERMed a healthy Control mid-serve, and
Control's normal teardown (room bridge → `ArcoSynthPool.shutdown` →
pyarco `finish()`, the "Arco_engine: finish called" line) took Arco down.
Control's round loop had already printed "round complete; waiting for
next load". A bare `terrarium_boot --serve` was never affected.

**Decision (user):** tolerate clean device exits only. In serve mode
`_dead_child` ignores a code-0 exit from a non-control child; a control
exit of any code and a non-zero device exit still fail loud. Round 2+
under `run_stack` therefore runs device-less until Tuneshroom reconnection
(explicitly deferred) or per-round device respawn (offered, declined for
now) lands. "Spin up a new Arco on load" was considered and rejected:
restarting Arco per round would reintroduce the documented pyarco-reset
audio trap, and Arco was not the failing component.
