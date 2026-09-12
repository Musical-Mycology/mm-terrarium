# run_stack parent watch and hangup teardown

**Date:** 2026-09-12. **Status:** approved, implemented in the same PR.

## Problem

Live on MYCOLOGICAL, 2026-09-12: a `./terrarium.sh --room DEMO` stack
kept playing the DEMO Room's drone through the laptop speakers after the
terminal that launched it was gone. `ps` showed `run_stack`,
`terrarium_boot`, the Arco server and the Room simulator all alive and
`run_stack` reparented to PID 1. Nothing had told the stack to stop.

Two gaps let that happen:

1. Every child is guarded by `--exit-with-parent` (`terrarium_boot`
   watches `run_stack`, `o2_shroom` watches `terrarium_boot`), but
   `run_stack` sits at the top of the chain and has no parent watch of
   its own. When its shell disappears it keeps going, its children see a
   healthy parent, and Arco keeps the audio device open.
2. `harness/signals.py` maps only SIGTERM to `KeyboardInterrupt`. A
   hangup from a closing terminal, when one is delivered at all, hits
   Python's default SIGHUP disposition and kills `run_stack` without
   unwinding, so `TeardownStack` never SIGTERMs the children (which sit
   in their own sessions via `start_new_session=True` and never see the
   hangup themselves).

## Design

- **Parent watch in `run_stack`.** `run()` records `getppid()` at entry
  and polls `o2_shroom.parent_is_gone` (the predicate `terrarium_boot`
  already reuses) on every `_hold` tick. When the launcher is gone,
  `_hold` returns the `PARENT_GONE` sentinel and `run()` returns
  `RunResult(ok=True, stage="parent-gone", ...)` through the normal
  `finally: teardown.close()` path, so Control, Arco and the simulator
  are stopped in reverse spawn order exactly as on Ctrl-C. The startup
  marker waits are already bounded by `ready_timeout` / `join_timeout`
  and are left alone; the unbounded hold is the loop that matters.
- **Opt-out for deliberate detachment.** `--detach` sets
  `StackConfig.watch_parent = False`. A `nohup` or launchd launch is
  orphaned on purpose and must not be torn down for it.
- **SIGHUP joins SIGTERM.** `sigterm_as_keyboard_interrupt()` also maps
  SIGHUP to `KeyboardInterrupt`, unless SIGHUP is already `SIG_IGN`
  (what `nohup` sets, inherited across exec), so `nohup` keeps working.
  Both `run_stack` and `terrarium_boot` call the helper, so both unwind
  cleanly on a hangup.

## Not changed

- `terrarium_boot` and `o2_shroom` parent watches are unchanged.
- No change to the `--exit-with-parent` wiring.

## Tests

`tests/test_run_stack.py`: the hold ends with stage `parent-gone` and
the children are signalled when `getppid()` changes; `--detach` and
`watch_parent=False` ignore the change; `parse_args` maps `--detach`.
`tests/test_signals.py`: SIGHUP handler installed, and left alone under
`SIG_IGN`.
