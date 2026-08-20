# Operator/harness control handoff, and the starved Arco pty

**Status:** Approved, not yet implemented.
**Found:** 2026-08-20, across one live UAT session attempting the DEMO
checklist's device-join half (spec 2026-08-19 section 7). Five distinct
defects surfaced; the deepest one explains three of the day's other
failures retroactively.

The operator this spec serves is real: every finding below misled the
project's own operator during a single afternoon, on a stack that was
functioning exactly as written.

---

## 1. Findings, each with its live evidence

### 1.1 The SETUP hold loop starves Arco's pty, freezing the whole room

`harness/terrarium_boot.py` runs Arco as a curses app on a pty
(`--arco-pty`), and the repo already documents the trap this creates:
`control/process.py`'s `stop_process` polls rather than waits precisely
because "an undrained pty blocks Arco's curses app on its own screen
writes."

`_serve_until_done` honors that: it calls `arco.poll()` (which drains the
pty into the `--arco-log` tee) every tick. **`_wait_in_setup` does not.**
Its loop body polls the transport agent, the console agent, and the
parent-pid check, and never touches `arco`. So for the entire duration of
a `--setup-seconds` hold, nothing drains the pty. Arco's curses output
accumulates until the buffer fills, at which point Arco **blocks mid-write
and its main loop freezes**: no clock-sync replies to devices, no message
routing, no audio.

Measured live, 2026-08-20, on a stack whose engine state was RUNNING (via
a Console Run click) while the harness still held:

- `o2debug.log` byte-for-byte static across a 5 s window.
- The run's `arco.log` tee at **0 bytes** eleven minutes after spawn:
  `arco.poll()` had never run, so nothing had ever drained.
- No drone despite state RUNNING: the note-on sat undelivered in front of
  a frozen hub.

The freeze explains three earlier mysteries at once:

- **Devices "taking 60-100 s to clock-sync."** They were not slow; they
  were held hostage. A device connects and pings for sync into a frozen
  hub; the moment the hold expires and `_serve_until_done` starts
  draining, Arco thaws and the sync completes "instantly" -- measured at
  O2 time 68.6 s on a hold that ended at ~68 s, and the drone bursts out
  at the same moment. To the operator this reads as "the tuneshroom
  crashed the moment sound started": the deny for the now-closed scored
  window arrives in the same breath as the thaw.
- **Why the Room simulator never hits this:** it clock-syncs during
  `boot()`, before any hold exists, while Arco is still being drained by
  the readiness path.
- **Part of the cold-start ownership-probe failures** (1.3): a hub frozen
  or mid-audio-open cannot answer the probe either.

**Fix:** `_wait_in_setup` gains an `arco` parameter and calls
`arco.poll()` every iteration, exactly as `_serve_until_done` already
does. One structural rule lands with it, as a comment at both call sites:
**every loop that holds while Arco is alive must drain Arco's pty.**

### 1.2 The hold and the Console fight over `run()`, and the harness dies

`main()` unconditionally calls `gs.run()` when `_wait_in_setup` returns.
If the operator already pressed Run on the Console during the hold (valid;
the Console is the operator surface), the state is RUNNING and
`InvalidTransition` escapes uncaught, killing the harness mid-session.
Observed live 2026-08-20 (`control.engine.InvalidTransition: run requires
SETUP, current state is State.RUNNING`), taking Arco and the Room down
with it while a lingering console thread kept port 8099 serving stale
state -- a half-dead stack that answered snapshots and did nothing.

Nobody could have hit this before this week: the Console was unreachable
during live runs until 2026-08-17 and its buttons only became usable with
the wire-JSON fix. The harness predates a second driver and assumes it is
the only one.

**Fix:** `_wait_in_setup` watches the engine. Its return type changes from
`bool` to a reason string: `"expired"` (window ran out), `"parent-gone"`
(existing behavior), `"state-changed"` (the engine left SETUP -- operator
Run, operator Abort, or anything else). `main()` calls `gs.run()` **only
when `gs.state is State.SETUP`**; on `"state-changed"` with state RUNNING
it logs `operator started the run from the Console` and proceeds straight
to `_serve_until_done`; with state IDLE (an Abort) the serve loop exits
immediately by its existing `"completed"` path. The engine is not touched:
`InvalidTransition` is correct, and the harness stops earning it.

### 1.3 The ownership probe misreads a blocked hub as a service conflict

`devicelink/o2_transport.py`'s `verify_service_ownership` sends one
self-addressed round trip and waits `timeout=2.0` s. Five live
occurrences on 2026-08-19/20 -- always the first attempt after idle,
retries usually clean -- produced `the game service is not routed back to
this connection: another process on the Arco hub already offers it`, and
sent the operator hunting orphans. The hypothesis was disproven both ways:
a host verified process-clean seconds before the failure, and a LAN probe
(15 s, ensemble `arco`, no local Arco) that no hub answered. The hub's own
log carried no refusal. The actual causes: a hub blocked in its cold
audio-device open (measured 1.8 s, longer after idle) and, per 1.1, a hub
frozen on an undrained pty.

A genuine second claimant *never* answers; a blocked hub answers as soon
as it unblocks. Those are distinguishable by waiting.

**Fix:** `O2LiteTransport.start()` retries the probe: resend every 2 s up
to a 10 s default (`ownership_timeout` becomes the total window). The
fixed nonce makes a late reply to an earlier send indistinguishable from a
fresh one, which is correct here. The error message is rewritten to name
the blocked-hub cause first (cold audio open; an undrained pty upstream of
this fix) and the orphaned-claimant cause second, with the hub-side log
line to check for each.

### 1.4 A device that loses its service across a reconnect gets silence forever

`harness/o2_shroom.py` verifies its own service once, at startup (the
`FATAL: service` marker). Observed live: a device launched during a stack
transition was auto-reconnected by o2litepy to the new hub, and its `ie<N>`
service announcement was lost in the handoff. Every reply Control sent --
fifteen denials -- was dropped hub-side as `/ie1/deny ... service was not
found`. The device saw pure silence, retried joins into the void, and its
own one-shot startup check had already passed against the previous hub.

**Fix:** `o2_shroom` re-runs its service self-verification whenever
o2litepy reports a reconnect (bridge id change), and its join-retry loop,
after 5 consecutive unanswered joins, prints what to actually check:

```
5 joins unanswered. Either Control is not up yet, or this device's
service announcement was lost (check o2debug.log on the hub for
"/ie1/... service was not found").
```

replacing today's `is Control up and in SETUP?`, which pointed the
operator at the one component that was healthy.

### 1.5 The operator cannot see any of this happening

Four small observability gaps, each of which cost real time on 2026-08-20:

- **Control's stdout says nothing about devices.** Hellos, grants,
  denials, and releases are all invisible outside the Console panel and
  the hub debug log. A denial is the worst case: it appears nowhere on
  the Control side at all.
- **The SETUP window is invisible.** No countdown; the operator learns
  the window closed by watching a device get denied.
- **Every device canvas header reads `ie0`.** `harness/o2_shroom.py:148`
  builds `shroom_capability()` bare, so the on-page header shows the
  default `surface_id` while the real identity lives only in the browser
  tab title. The operator reasonably read this as a mis-registration.
- **A dark-by-design canvas is indistinguishable from a broken one.**
  TestBit's `jammer` deliberately declares no light; the resulting black
  canvas was reported as a failure the same day two genuinely-black-canvas
  bugs were fixed.

**Fixes**, all in `harness/` (the engine is untouched):

- `terrarium_boot` prints one line per device lifecycle event: `device
  hello: ie1`, `join granted: ie1 -> player (scored) via
  TEST_PLAYER_NODE`, `join denied: ie1 -> TEST_PLAYER_NODE (reason)`,
  `device released: ie1`. Two seams, because denials never cross the
  engine's observer list (the engine notifies on grants and releases,
  not refusals): hellos, grants and releases ride an engine observer
  exactly as `ConsoleAgent` does, while denials ride a new optional
  `on_join_denied` sink on `DeviceLinkAgent`, constructor-injected and
  guarded at its call site -- the same pattern `on_room_frame` already
  established. `run_stack`'s tee then lands all of it in `control.log`
  for free.
- `_wait_in_setup` prints `SETUP open, {n:.0f}s remaining` every 15 s.
- `o2_shroom` builds `shroom_capability(surface_id=dev)`.
- On role grant, if the config blob's `light_manifest` has no
  instruments, `o2_shroom` prints `role has no light declaration --
  canvas stays dark by design`.

An on-canvas banner for the dark case would need a luxaeterna change and
is an explicit non-goal.

---

## 2. What does not change

- `control/engine.py`, byte for byte. Every fix lives in `harness/` and
  `devicelink/o2_transport.py`.
- The engine's registration rules: scored roles still close at RUNNING.
- `run_stack`'s markers contract (`tests/test_markers.py`): the new
  countdown and lifecycle lines are additive; every existing marker
  string is emitted unchanged.
- `--setup-seconds` semantics: the hold still runs its full window unless
  the parent dies or the operator acts.

## 3. Testing

- **1.1:** a fake arco records `poll()` calls; `_wait_in_setup` must call
  it every iteration. The fake-must-not-be-more-permissive rule applies:
  the fake's `poll()` is how the test *sees* draining, not a no-op.
- **1.2:** drive `gs` to RUNNING mid-hold via an injected clock; assert
  `_wait_in_setup` returns `"state-changed"` promptly, `main`'s helper
  path skips `gs.run()`, and nothing raises. A second case for Abort
  (state IDLE). This is the regression test for the live crash.
- **1.3:** a fake o2lite that answers the probe only after the second
  send passes; one that never answers still fails with the new message.
  Assert the resend actually happens (count sends).
- **1.4:** a fake o2lite that changes `bridge_id` mid-run must trigger
  re-verification; unanswered-join counting hits 5 and prints the hint.
- **1.5:** the logging observer's lines are asserted against a scripted
  hello/join/deny/release sequence; countdown lines appear under an
  injected clock; `surface_id` equals the dev id; the dark-canvas notice
  prints exactly when the manifest has no instruments.
- Suite baseline entering: 1076 passed, 1 skipped.

## 4. Live verification (the acceptance gate)

The operator's exact two-terminal recipe, previously impossible:

1. `run_stack --room-type DEMO --devices 0 --console-port 8099
   --setup-seconds 240`, then `o2_shroom --dev ie1 --node
   TEST_PLAYER_NODE ...` started after the hold line prints.
2. The device clock-syncs **within seconds, during the hold** (the pty
   drain working; previously it froze until expiry).
3. `join granted: ie1 -> player` appears on Control's stdout; countdown
   lines tick down around it.
4. Pressing Run on the Console starts the round immediately: drone
   sounds, device canvas animates, **no crash at the hold's would-be
   expiry** (the handoff working).
5. The full round completes; the device fades and releases cleanly.

A cold-start run (first after >10 min idle) additionally exercises 1.3:
the stack comes up without the service-conflict error.

## 5. Deep-dive obligations

On this branch's closeout, `docs/MM_TERRARIUM.md` must be corrected where
this spec's findings supersede it: the "device clock-sync is unreliable
in a headless run" entry (the pty starvation is the mm-terrarium-side
mechanism for the interactive stalls, and it is now fixed; whether an
upstream residue remains headless is then measurable), the
`game`-service race entry (now: blocked hub, not lingering registration),
and the ~22 s device cold-start figure.
