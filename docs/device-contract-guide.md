# The device contract kit: a guide for firmware implementers

**Audience:** whoever builds an embedded device that speaks to Control. Today
that is Victor, who owns the Rev 1 ESP32-P4 firmware in **mm-devshroom**
(checked out at `/Users/chris/projects/mm-devshroom` on Chris's machines).
**Written:** 2026-09-22, against mm-terrarium `0896d5b` (`contract_version` 1,
eleven scenarios) and the merged Dart runner in mm-tuneshroom
([PR #29](https://github.com/Musical-Mycology/mm-tuneshroom/pull/29)).

**Binding spec:** `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`
(sections 4.2, 4.3, 5.1, 7 and 9). **Firmware plan it amends:**
`docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md`, Tasks A2
and A8. This guide is meant to make Task A8 (native scenario replay) doable on
its own. Where the two disagree, the spec wins, and where the spec and a
recording disagree, the recording wins.

## 1. What the contract is and who owns what

The contract is one checked description of the device wire: the verb table
(every `/game/<verb>` a device may send and every `/<dev>/<verb>` Control may
send, with typespecs, argument names, transport and whether it is allowed
before a role), the Rev 1 instrument and its gesture thresholds, the lifecycle
numbers, and eleven recorded scenarios that pin what a device must do. It is
owned by mm-terrarium, which implements it as Control. A device repo
(mm-tuneshroom for the Flutter app, mm-devshroom for the board) commits an
**export** of it and replays the scenarios against its own session code. The
change flow is one way (spec 4.1): a verb or a Control behavior changes in
mm-terrarium, the scenarios are re-recorded there, the export is regenerated
into each device repo, and that device's replay tests fail until the device
matches. A device repo never edits a verb or a scenario. The wire changes only
through that flow.

## 2. Getting the export

The export tool runs **only as a module**, from an mm-terrarium checkout, and
always through the project venv (there is no bare `python` on the dev boxes):

**RUN ON: any Mac with an mm-terrarium checkout (Mycological or Mycelium)**

```bash
cd /Users/chris/projects/mm-terrarium && .venv/bin/python -m tools.export_contract /Users/chris/projects/mm-devshroom/test/contract
```

Spec section 4.2 quotes the script form, `tools/export_contract.py <out-dir>`.
That form fails with `No module named 'control'`. The spec is wrong on that
point; use the module form above.

The tool writes `contract.json` plus `scenarios/<name>.json`, one file per
scenario. The scenario files are byte copies of
`contract_kit/recordings/` and carry no `_provenance`. The tool does **not**
delete stale files in its output folder, which is why the guard below checks
the index against the files in both directions.

Commit the folder at `test/contract/` in the device repo, by hand, in its own
commit naming `_provenance.commit`, and never hand-edit it. (As of 2026-09-22
mm-devshroom has no `test/contract/` yet and no `[env:native]` in
`platformio.ini`; Task A8 is gated on the export landing first.)

**Guard checks worth porting**, from mm-tuneshroom's
`test/contract_export_test.dart`, each a one-line assertion against
`contract.json`:

| Check | Why |
|---|---|
| `_provenance.tool` equals `export_contract/1` and `_provenance.commit` is 12 hex characters | catches a hand-made or partial folder |
| `contract_version` equals the number the runner was written for (1 today) | a bump means a device can observe a difference; review the runner against the new `step_schema` before changing the number |
| the `scenarios` index and the files under `scenarios/` match, both ways | the tool never deletes stale files |
| each file's `name` equals its stem and its `profiles` equal its index row; no file has `_provenance` | a stale or edited copy |
| the built-in Rev 1 thresholds equal `instruments.tuneshroom_rev1.triggers` | `tap.max_ms`, `hold.min_ms`, `swing.peak_g`, `swing.window_ms`; the plan names the firmware constants `TouchClassifier::TAP_MAX_S` and `HOLD_MIN_S` in seconds, so divide by 1000 |
| the firmware hello interval equals `lifecycle.hello_interval_s` | the plan's `HEARTBEAT_S` |
| `link.arg_types` contains `b` and `link.max_message_bytes` is 4096 | the vendored o2lite must decode blobs and accept that size (Task A8's first check) |

## 3. What is in `contract.json`, key by key

Everything a device author needs is inline. Nothing in the file points outside
the export folder.

| Key | What it holds | What a runner does with it |
|---|---|---|
| `_provenance` | `commit` (12 hex) and `tool` | guard only |
| `contract_version` | integer, 1 | refuse to run on any other value |
| `verbs` | one row per verb: `address`, `direction` (`up` or `down`), `typespecs`, `args`, `transport` (`tcp` or `udp-ok`), `pre_role`, `notes` | the shapes your send path and handlers must match; `pre_role` false means the session must not send it without a role |
| `link` | `arg_types` (`b`, `f`, `i`, `s`), `max_message_bytes` 4096, `service_is_dev_id` true | what the o2lite link must be able to do; the dev id is the O2 service name |
| `limits` | `dev_id_max_len` 31, `max_message_bytes` 4096, `reserved_dev_ids` (`terrarium`) | validate your own dev id |
| `lifecycle` | `hello_interval_s` 5.0, `stale_timeout_s` 15.0, `lobby_double_tap_window_s` 1.5, `cue_horizon_s` 0.06, `bench_tolerance_ms` `{frame: 50, heartbeat: 1000}` | the hello cadence is the only one the session implements; the others explain what Control does |
| `lifecycle_notes` | one sentence per `lifecycle` key, with units | read once; the `cue_horizon_s` note says a runner must always use a step's own `at` and never compute it |
| `instruments.tuneshroom_rev1` | `instrument` (name, 12 pixels, five capabilities, no ambient, no functions) and `triggers` | the threshold guard above; the instrument name is what hello's fourth argument declares |
| `scenarios` | index rows: `name`, `profiles`, `summary` | iterate this, not a directory glob, to pick which files to replay and in which profiles |
| `step_schema` | `notation`, `t`, `tolerance`, `link_down_delivery`, `placeholders`, `scenario_fields`, `kinds` | the format reference for the runner; section 5 restates it |
| `replay_notes` | three plain sentences | the rules a scenario file alone does not state: no delivery while down, malformed steps are delivered and must be dropped, and the hand-authored pair in `timed_frames_hold_last` |

Inside `step_schema`:

- `t` is integer milliseconds from the scenario's start. `steps` is sorted by
  `t`, stably. Steps sharing one `t` are delivered and checked in file order.
- `kinds` has seven entries. Three are inputs: `link` (a bare string, `up` or
  `down`), `control_sends` (`address`, `typespec`, `args`, `at`, optional
  `malformed`) and `gesture` (`kind`, `onset_t`, then `duration_ms` for tap,
  `held_s` for hold, `signed_g` for swing). Four are expectations:
  `expect_out` (`address`, `typespec`, `args`, `stamp_t`, `within_ms`),
  `expect_frame` (`grb`, 36 ints), `expect_play` (`name`, `params`,
  `within_ms`) and `expect_quiet` (`addresses`, `for_ms`).
- `control_sends.at` is the presentation time on the `t` timeline. Only
  `/leds` ever carries one. Every other verb's `at` is `null`, meaning no
  presentation time, not due at t=0.
- `placeholders`: `$DEV` (your own dev id, in `control_sends.address` and
  `expect_out.args`), `$KEY` (a chime key, always inside `key=$KEY`, in
  `control_sends.args` and `expect_play.params`; match the shape
  `key=<integer>` and never compare the number) and `*` (any value, in
  `expect_out.args`).
- `scenario_fields.device` is `{"join_node": str or null}`. Non-null means the
  device sends `/game/join ["$DEV", join_node]` right after its first hello on
  every link-up.

## 4. The session interface a device should expose

The point of a session boundary is that a scenario means the same thing on a
phone and on the board. mm-tuneshroom's `lib/host/README.md` states the
C-portable shape its `DeviceSession` (in `lib/host/device_session.dart`)
exposes; the firmware's session library should take the same inputs and give
the same outputs (spec 6.3 and 7).

**Five inputs:** `linkUp(protoversion)`, `linkDown(reason)`,
`onMessage(envelope, atMs)` (returns whether it acted), `onGesture(gesture)`
(an already classified gesture), and `tick(nowMs)`, the only source of time.
There are no timers and no callbacks inside the session.

**Three outputs:** `takeEffects()`, one ordered list of `Send{envelope,
stampMs}` and `Play{name, params}` (one list because order is contract: the
`tick` sample plays immediately before its tap is sent); `frame`, the GRB bytes
to show; and `snapshot`, a plain value with equality (phase, role config,
instrument, deny reason, link state and so on).

**Two invariants.** First, `linkUp` means the transport handshake is already
complete and service ownership is proven; the session never sees a half-open
link, and in replay a `link: "up"` step stands for "handshake already done".
Second, the session knows one integer-millisecond timeline and nothing else.
O2 seconds never enter it: the host converts an outgoing `stampMs` to an O2
stamp and an incoming O2 timestamp to `atMs`. On firmware `nowMs` is O2 time
in milliseconds, so both conversions are the identity.

**Mapping onto the firmware plan's names.** The plan's modules are transport
and hardware, and the session sits between them:

| Session call | Plan interface it wraps or replaces |
|---|---|
| `linkUp` | called once `link_begin()` has run and Task A2's `verify_ownership()` has returned true; `link_synced()` alone is not enough |
| `linkDown` | called when `link_poll()` sees WiFi drop or ownership lost (A2 sets `joined = false` there; the session owns that flag now) |
| `onMessage` | the bodies of A2's `on_role`, `on_room`, `on_leds`, `on_play`, `on_release` handlers, with `o2l_get_timestamp()` converted to `atMs` |
| `onGesture` | fed from A4's `TouchClassifier` and `SwingDetector` in `src/sense/gestures_core.h`, which stay outside the session |
| `tick` | called from `loop()` with `link_time()` in ms; drives the hello cadence (replacing A1's `last_hello` check in `link_poll()`) and A3's `frames_tick(now)` |
| `takeEffects` | drained after every input and tick: `Send` goes to `link_hello()`, `link_join()`, or A4's `link_send_gesture()`; `Play` goes to the A5 sample player |
| `frame` | what A3's `frames_push`/`frames_tick` queue currently shows, after `frames_limit` |

Keep the queue in A3's `src/render/frames_core.h` and the classifiers in
`src/sense/gestures_core.h` Arduino-free, as the plan already does, and put the
session beside them under `lib/`, as spec section 7 recommends. The native test
build links those three and nothing from `src/`.

## 5. The seven device rules, and what the recordings check

| # | Rule (spec 4.3) | Scenario that pins it | What the recording actually checks | Concrete behavior |
|---|---|---|---|---|
| 1 | Hello goes out once the link is up, then every 5 s over TCP | `boot_hello_heartbeat`, and every other scenario | `expect_out /game/hello "ssss" ["$DEV", "*", "*", "*"]` within 50 ms of t=0, 5000 and 10000 | `linkUp` queues a hello and `tick` queues another once 5000 ms have passed, on a grid so a late tick neither drifts nor bursts. Always send the four-argument form; the fourth is the instrument name |
| 2 | Before a role, only `hello`, `join`, `start` and `tap` go out | `boot_hello_heartbeat` (`expect_quiet` on join, tap, hold, swing for 12 s), `gestures_after_role` (`expect_quiet` on hold and swing for 500 ms after a pre-role tap) | no listed address has a send time in the window | hold and swing are gated on holding a role; tap is not. Control answers a pre-role tap outside a lobby with `/$DEV/error ["tap", "device not registered"]` |
| 3 | A frame shows at its presentation time; when several are due only the newest shows; the last holds through silence | `timed_frames_hold_last` | pixels at t=2000, 5500, 6500 and 12000 | **newest by presentation time, not by arrival.** The two `/leds` steps at t=6000 are hand-authored: blue with `at` 6200 is sent first, red with `at` 6100 second. Both expect_frames at 6500 and 12000 want blue. Plan Task A3's `FrameQueue::due` picks the most recently pushed due frame and drops the rest, so it shows red and fails here. Select by greatest `at`, ties to the later arrival; drop an older timed arrival than the frame on screen |
| 4 | `/release` ends the role; frames already queued still show and the last holds | `release_keeps_display` | a dim non-black frame at t=3000 and again at t=8000 after release at t=2621; hold and swing quiet for 5 s; hello continues at t=5000 | release arrives untimed in the same millisecond as the fade's last frame, which is stamped 60 ms later. Do not clear the queue or the pixels on release (spec D5); do keep the heartbeat |
| 5 | `count` is the taps in one gesture and Rev 1 sends 1; stamps mark onset | `gestures_after_role`, `play_known_and_unknown`, `error_no_state_change`, `lobby_tap_join`, `timed_frames_hold_last` | `expect_out` with `stamp_t` equal to the gesture's `onset_t`, within 1 ms; tap args `["$DEV", 0.0, <duration_ms>, 1]`, hold `["$DEV", <held_s>, 1]`, swing `["$DEV", <signed_g>, 1]` | `peak_g` is 0.0 for a touch tap. Control pairs double taps itself (`lobby_tap_join` sends two count-1 taps 600 ms apart). The O2 timestamp on the message is the onset time, so a hold released after 650 ms is stamped at touch-down |
| 6 | An unknown sample is ignored; a malformed or unknown message is dropped without changing state | `malformed_dropped`, `play_known_and_unknown`, `error_no_state_change` | the three flagged steps at t=200 (`/$DEV/bogus` typespec `s`; `/$DEV/role` typespec `s` carrying `"not json"`; `/$DEV/leds` typespec `b` carrying `"not a list"`) are delivered, then a tap still sends, `tick` still plays and the role's frame still shows at t=2000 | validate the blob before touching state: a `/leds` blob that is not exactly 36 bytes is dropped, never truncated (A2 already does this); a `/role` that does not decode to a JSON object with a `role` string is dropped |
| 7 | No session resume: after 15 s of silence Control has dropped the device, which must join again | `link_loss_rejoin` | link down at t=2000, Control's own `/release` at t=15616 is **not** delivered, link up at t=17000, then hello and `/game/join ["$DEV", "CONTRACT_PLAYER_NODE"]` within 50 ms | `linkDown` ends the role. Every `linkUp` hellos and then joins `device.join_node` when it is set. The join is a request: a deny leaves the device hello'd (`deny_stays_hellod`). Re-checking service ownership after a reconnect is inside the link and only the bench replay can cover it |

Two Control behaviors the recordings also show and a session must tolerate: a
newly granted role opens with a signature of about 1.5 s that ignores light
cues (the last signature frame is sent at t=1518 or t=1503), and the join
chime is `/play ["chime", "key=$KEY"]` about 1.8 s after the role.

## 6. Writing the runner in C or C++

mm-tuneshroom's `test/replay/scenario_runner.dart` is the reference. The nine
rules below are its design addendum's section 6
(`docs/superpowers/specs/2026-09-21-device-session-and-replay-design.md` in
mm-tuneshroom), restated for a C or C++ test build.

1. **Two passes.** Pass one plays the inputs on a fake clock and logs every
   `Send` (with the `nowMs` it was drained at and its `stampMs`), every
   `Play`, and the frame at each `expect_frame` time and one tick later. Pass
   two checks every expectation against the log. Windows such as
   `[t, t + within_ms]` look forward, so a single pass cannot check them.
2. **Tick grid.** Collect every time the clock must stop at: every multiple of
   23 ms (the export's render tick, `step_schema.tolerance`) up to the last
   window's end, each step's own `t`, and `t + 23` for each `expect_frame`.
   Walk them in order; at each, call `tick(now)`, drain effects, record any
   late frames due, then run the steps at that `t` in file order, draining
   after each. Never re-sort steps.
3. **Inputs.** `link` is a bare string: `up` calls `linkUp`, `down` calls
   `linkDown`. `control_sends` becomes a message with its own `typespec` and
   `args`, `$DEV` substituted with the replay dev id, `$KEY` replaced by a
   fixed integer, and `atMs` taken from the step's `at` (null stays null).
   Honor `step_schema.link_down_delivery`: while the last `link` step was
   `down`, skip the step and count it; `link_loss_rejoin` depends on this.
   `gesture` becomes your gesture struct with `onsetMs` from `onset_t`.
4. **Malformed steps are delivered.** Snapshot the state and the frame, call
   `onMessage`, then assert the snapshot is equal, the frame is equal and no
   effects were produced. (This cannot see a frame parked in the timed queue
   with a future `at`; no exported scenario does that today.)
5. **`join_node`.** Construct the session with `device.join_node` when it is
   non-null, so every `linkUp` sends the join after its hello.
6. **Late join inference.** The export has no `join` input step yet: the
   recorder's `join_now` writes only the `expect_out`. `error_no_state_change`
   (t=6000) and `gestures_after_role` (t=1000) carry `expect_out /game/join`
   with `join_node` null. When an `expect_out` for `/game/join` is not explained
   by a link-up at that `t` with a matching `join_node`, the runner calls
   `join(node)` at that `t` first and labels the expectation as inferred in its
   report. That one expectation cannot fail meaningfully; everything after it
   can. This is follow-up 1 in section 9.
7. **Matching, with the exact tolerances:**

   | Expectation | Match |
   |---|---|
   | `expect_out` | address and typespec equal; args element-wise with `$DEV`, `*` and numbers within 1e-3; send time in `[t, t + within_ms]` inclusive; when `stamp_t` is non-null the message's stamp within 1 ms of it. Consume the matched send so two identical expectations need two sends (the double tap at t=2000 in `timed_frames_hold_last`) |
   | `expect_play` | name equal; params equal after turning `key=$KEY` into `key=<integer>`; time in `[t, t + within_ms]`; consumed |
   | `expect_quiet` | no send of a listed address with time in `[t, t + for_ms)`, half-open |
   | `expect_frame` | all 36 values equal at `t`, or else all 36 equal at `t + 23` |
   | malformed `control_sends` | state equal, frame equal, no effects |

8. **Loud on drift.** An unknown step kind, an unknown gesture kind, an
   unknown `link` value, an unknown profile tag or a `contract_version` other
   than 1 must fail the test with the file and step index, never skip.
9. **Profile tags.** `rev1` runs a Rev 1 session; `any` runs every profile
   the device has. The board has one profile, so both tags run it once. Take
   the names from the `scenarios` index.

**(recommended) Embed the scenario JSON as headers and use a header-only JSON
parser in tests only**, exactly as plan Task A8 lays out: `tools/scenario2h.py`
turns each `test/contract/scenarios/*.json` file into a C string header under
`test/test_scenarios/scenarios_generated/`, committed beside the JSON, and a
single-header JSON library vendored under `test/test_scenarios/` parses it
(the plan names `json.hpp` as one option). The reason is that `pio test -e
native` then needs no filesystem access, no network and no generation step,
and `src/` stays free of a parser it would never use on the board. What would
change the call: if the native test build can read files from the repo
reliably, loading the JSON directly saves the generator and the duplicate
copies; the guard in section 2 must then run against the JSON on disk. The
`contract.json` itself goes through the same generator once, for the guard
checks and the `scenarios` index.

## 7. Proving the runner has teeth

A runner is green the moment it exists if it checks nothing, so mm-tuneshroom's
`test/contract_replay_test.dart` carries must-fail cases beside the eleven
replays. Port these; each is an inline scenario or a stub target:

| Must-fail case | How the Dart test builds it |
|---|---|
| wrong frame | link up, then `expect_frame` with 36 nines at t=100; the single failure mentions `frame` |
| missing send | `expect_out /game/tap` with no gesture before it |
| quiet-window breach | `expect_quiet` on `/game/tap` for 500 ms, then a tap at t=100 |
| wrong stamp | a tap at t=100 and an `expect_out` with `stamp_t` 150; the message must name both 150 and the 100 it got |
| wrong argument | same tap, `duration_ms` 999.0 in the expectation while address and typespec match |
| malformed step that changes state | deliver a flagged `/$DEV/bogus` to a stub target whose `onMessage` increments a counter; the failure mentions `malformed` |
| unknown step kind | a step `{"t": 5, "teleport": {}}` must throw or fail loudly |
| `timed_frames_hold_last` without a timed queue | run the real session with the hold-until-due mode off (show every frame on arrival); the report must fail and one failure must be at t=6500 |

Also prove the runner rules: a `control_sends` inside a down window is never
delivered and the session has no role afterwards; a late join is inferred and
counted; a join explained by `join_node` at link-up is not counted; `$KEY`
matches `key=<integer>` and not a specific number.

**Mutation check.** Once everything is green, neuter one matcher at a time
(make `expect_frame` always pass, drop the stamp comparison, widen the quiet
window to zero) and run the suite. It must go red each time. Then revert. This
is how PR #29 found that no scenario pins the link-loss display decisions in
the next section.

## 8. What the recordings pin, and what they do not

**Pinned by a scenario:** rules 1, 3, 4, 6 and 7 as rejoin, plus the shapes and
onset stamps of rule 5 and the pre-role quiet windows of rule 2. A device that
gets any of these wrong fails a replay.

**Not pinned by any scenario:**

- **"A lost link ends the role."** No recording checks the session's state
  after `link: "down"` before the next `link: "up"`.
- **"Rev 1 keeps its display through a link loss."** No scenario holds a role
  across a link drop with a later `expect_frame`. `link_loss_rejoin` has no
  `expect_frame` at all inside or after its down window.

Both are decisions the Tuneshroom app made to mirror the board (addendum
sections 5.1 and 5.2 in mm-tuneshroom). Reverting either leaves all eleven
scenarios green; they are pinned by unit tests in mm-tuneshroom only. The
second one was **inferred** from plan Task A2's reconnect code, which only
sets `joined = false` and clears nothing on the NeoPixels. It was not
measured. Victor should confirm on the bench that the board really keeps its
pixels when WiFi drops, and say so in mm-devshroom's `README.md` or a test.
If the board blanks, the app's decision 2 is the one to revisit, not the
board.

## 9. Open items a firmware author will meet

From the addendum's section 8 and this guide:

1. **No `join` input step kind.** Until mm-terrarium's recorder writes one
   from `join_now` (with a `contract_version` bump), the runner infers late
   joins (section 6, rule 6).
2. **`any` retags.** `boot_hello_heartbeat`, `deny_stays_hellod` and
   `explicit_join_role` are candidates to be retagged `any`; on the board
   both tags run the one profile, so nothing changes for firmware.
3. **Spec 4.2 quotes the script-form invocation**, which does not run. Use
   the module form in section 2.
4. **No link-drop-with-role scenario.** Recording one is the way to pin the
   two decisions in section 8; it is exactly the Rev 1 behavior they mirror.
5. **Hands-free reconnect is not in the contract.** Plan Task A2 step 2 does
   reconnect and re-check ownership; the scenarios only see the resulting
   `linkDown` and `linkUp`. The bench replay (spec section 8, not yet run) is
   the only place that path is exercised end to end.
6. **`/play` scheduling and the microphone are out of scope** for the
   contract. `expect_play` checks name and params inside a window and nothing
   about audio timing.
7. **Plan Task A3's queue selects by arrival order** (section 5, rule 3). Fix
   it before wiring the runner, or `timed_frames_hold_last` will be the first
   red test.
