# Per-round device respawn — design

**Date:** 2026-08-21
**Status:** Implemented
**Depends on:** console-operator-rounds slice (PR #47) and the serve-mode
clean-exit tolerance (PR #48).
**Origin:** With clean device exits tolerated, round 2+ under `run_stack`
runs device-less — the released simulators exit by design and nothing
replaces them. Hand-starting `o2_shroom` also hit the PYTHONPATH trap
(no `ARCO_PYTHONPATH` fallback outside `run_stack`).

## 1. Decisions

- **run_stack respawns simulated devices per round (serve mode only).**
  When Control announces a newly loaded round, `run_stack` spawns a fresh
  set of `o2_shroom` children for it. Real-device (Tuneshroom)
  reconnection stays deferred and is unaffected: real devices are never
  `run_stack` children, so this machinery cannot fight that later design.
- **Node and count come from the loaded Bit's manifest.** Control emits a
  new marker line naming the round's bit; `run_stack` resolves that name
  against the `BitRegistry` it already holds: join node via
  `cfg.join_node()`, count via `launch.default_devices`. Explicit
  `--node` / `--devices` are overrides applied to every round. A loaded
  bit with no resolvable node (and no `--node`) logs a warning and spawns
  nothing — an operator-only round is legitimate.
- **New marker, emitted every round:** `CONTROL_ROUND_LOADED =
  "round loaded:"` — printed by `_serve_rounds` (and once for the initial
  CLI-selected bit before the first hold in serve mode) as
  `round loaded: <BitName>`, pinned in `harness/markers.py` /
  `tests/test_markers.py` like every watched line. `run_stack` skips the
  first occurrence (its launch path already spawned round 1's devices).
- **Fresh ids per round:** round N's devices are `ie<k>-r<N>` with logs
  `ie<k>-r<N>.log`, avoiding any service-name race with a predecessor
  still draining its closing fade. Each respawned child rides the same
  `spawn()` path: registered on the `TeardownStack` at spawn, tee'd to
  its own log, `--exit-with-parent` passed, `BROWSE_URL` collected (and
  opened under `--open` — tab accumulation across many rounds is
  accepted for now and noted).
- **Respawns are best-effort:** the launch path's readiness gating
  (clock-sync / role-granted waits) applies to round 1 only; a respawned
  device that fails to sync or join shows up in its own log and in
  Control's join lines, and does not fail the stack. Serve mode already
  tolerates its clean exit.
- **Fold-in:** the `ARCO_PYTHONPATH` fallback moves to a shared helper
  (`harness/arco_paths.py`), used by `run_stack` (unchanged behavior) and
  now by `harness/o2_shroom.py`'s `main()`, so a hand-run simulator works
  without an explicit `PYTHONPATH`.

## 2. Non-goals

- Tuneshroom/real-device reconnection across rounds (still deferred).
- Respawn under `--ci` (CI stays one-shot; serve is refused there).
- Closing old browser tabs or reusing canvases across rounds.

## 3. Verification

- Offline: marker emit sites pinned; respawn trigger unit-tested against
  a scripted control tee (marker seen → spawn called with
  manifest-resolved node/count; first occurrence skipped; --node/--devices
  overrides win; unknown bit name → warning, no spawn).
- Live (Mycological): serve stack, round 1 TestBit with 1 device; from
  the Console abort → load MetronomeBit → a fresh device pair joins
  METRO_PLAYER_NODE and the round runs; teardown clean, zero orphans.
  Hand-run `o2_shroom` with no PYTHONPATH imports o2litepy.

## Status (live-verified 2026-08-24)

**What ran, headlessly, on the dev box:** `run_stack --console-port 0
--room-type DEMO` (no `--devices`/`--node`, so both stay manifest-resolved
every round), driven over the Console `/ws` with a small `websockets`
client.

- Round 1 (CLI-selected TestBit, 1 device) came up, `ie1` clock-synced and
  joined TEST_PLAYER_NODE, then an `abort` command released it cleanly
  (serve mode's clean-exit tolerance from PR #48 held — Control stayed
  up).
- `load_bit MetronomeBit` produced `round loaded: MetronomeBit` and
  spawned exactly two fresh children, `ie1-r2` and `ie2-r2` — matching
  MetronomeBit's manifest `launch.default_devices = 2` with no CLI
  override. Both clock-synced and were join-granted on
  `METRO_PLAYER_NODE`, confirming both node and count resolution from the
  just-loaded Bit's manifest.
- A second `abort` released both round-2 devices cleanly; `load_bit
  TestBit` produced `round loaded: TestBit` and spawned one child,
  `ie1-r3` (TestBit's manifest `launch.default_devices = 1`). `ie1-r3`
  clock-synced and attempted to join `TEST_PLAYER_NODE`, confirming node
  resolution for round 3 as well — the join itself was denied ("no Bit
  accepting registrations") because TestBit's own short SETUP window
  (see the console-operator-rounds section's already-documented
  per-round `setup_seconds` race) closed before the join landed; this is
  the pre-existing, separately documented timing behavior, not a defect
  in the respawn machinery.
- `SIGINT` on the process group produced a clean teardown: `run_stack`
  exited 0, and `pgrep -f "o2_shroom|terrarium_boot|room_simulator"`
  was empty afterward, both before and after this run.
- `env -u PYTHONPATH .venv/bin/python -m harness.o2_shroom --dev probe
  --node TEST_PLAYER_NODE`, run with no stack up and no `PYTHONPATH` set,
  got past the o2litepy import (`ensure_o2litepy`'s shared fallback):
  it reached its `BROWSE_URL` / frame-summary output with no
  `ModuleNotFoundError`. No discovery/connect attempt was pushed to
  completion (no Control running to discover), which is expected and not
  part of what this check verifies.
- Full suite: `1284 passed, 1 skipped`.

**What was not live-verified:** the offline-suite items in §3 (marker
emit-site pins, the scripted-control-tee respawn-trigger unit tests,
`--node`/`--devices` override behavior, unknown-bit-name warning path)
ran only as part of the automated suite above, not exercised by hand in
this live pass. A real Tuneshroom reconnecting across rounds remains
unbuilt per the non-goals. Tab accumulation under `--open` across many
rounds was not exercised (this run did not pass `--open`).
