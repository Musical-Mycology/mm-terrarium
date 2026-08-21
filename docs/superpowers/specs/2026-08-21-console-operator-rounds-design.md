# Console operator rounds — design

**Date:** 2026-08-21
**Status:** Draft
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
