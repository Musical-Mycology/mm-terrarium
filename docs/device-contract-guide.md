# The device contract kit: a guide for firmware implementers

**Audience:** whoever builds an embedded device that speaks to Control. Today
that is Victor, who owns the Rev 1 ESP32-P4 firmware in **mm-devshroom**
(checked out at `/Users/chris/projects/mm-devshroom` on Chris's machines).
**Covers:** `contract_version` **3**, with **seventeen** recorded scenarios
(`len(contract_kit.scenarios.ALL_SCENARIOS)`). Contract v3 replaces
`/game/join` with the instrument handshake (hello, handshake, received
handshake, validated, then one role per device at start) and moves every
control verb to TCP.
**History:** first written 2026-09-22 against `contract_version` 1 (eleven
scenarios) and the merged Dart runner in mm-tuneshroom
([PR #29](https://github.com/Musical-Mycology/mm-tuneshroom/pull/29)); a fix
wave the same day added a `join` step kind and `link_loss_keeps_display`
(`contract_version` 2). Rewritten 2026-10-01 for v3, which retires both the
`join` step kind and `device.join_node`.

**Binding specs:** `docs/superpowers/specs/2026-10-01-instrument-handshake-protocol-design.md`
(sections 3, 6 and 8: the protocol, the v3 kit, and the firmware checklist)
and `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`
(sections 4.2, 4.3, 5.1, 7 and 9: the kit itself). **Firmware plan it
amends:** `docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md`,
Tasks A2 and A8. Where this guide and a spec disagree, the spec wins, and
where a spec and a recording disagree, the recording wins.

## 1. What the contract is and who owns what

The contract is one checked description of the device wire: the verb table
(every `/game/<verb>` a device may send and every `/<dev>/<verb>` Control may
send, with typespecs, argument names, transport and whether it is allowed
before a role), the Rev 1 instrument and its gesture thresholds, the lifecycle
numbers, and seventeen recorded scenarios that pin what a device must do. It
is owned by mm-terrarium, which implements it as Control. A device repo
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
delete stale files in its output folder, so when you move from v2 to v3,
delete `scenarios/explicit_join_role.json` and `scenarios/lobby_tap_join.json`
by hand (the guard below catches them if you forget).

Commit the folder at `test/contract/` in the device repo, by hand, in its own
commit naming `_provenance.commit`, and never hand-edit it.

**Guard checks worth porting**, from mm-tuneshroom's
`test/contract_export_test.dart`, each a one-line assertion against
`contract.json`:

| Check | Why |
|---|---|
| `_provenance.tool` equals `export_contract/1` and `_provenance.commit` is 12 hex characters | catches a hand-made or partial folder |
| `contract_version` equals the number the runner was written for (3 today) | a bump means a device can observe a difference; review the runner against the new `step_schema` before changing the number |
| the `scenarios` index and the files under `scenarios/` match, both ways | the tool never deletes stale files |
| each file's `name` equals its stem and its `profiles` equal its index row; no file has `_provenance` | a stale or edited copy |
| the built-in Rev 1 thresholds equal `instruments.tuneshroom_rev1.triggers` | `tap.max_ms`, `hold.min_ms`, `swing.peak_g`, `swing.window_ms`; the plan names the firmware constants `TouchClassifier::TAP_MAX_S` and `HOLD_MIN_S` in seconds, so divide by 1000 |
| the firmware hello interval equals `lifecycle.hello_interval_s` | the plan's `HEARTBEAT_S` |
| `link.arg_types` contains `b` and `link.max_message_bytes` is 4096 | the vendored o2lite must decode blobs and accept that size (Task A8's first check) |
| every `verbs` row your firmware sends or handles has the `transport` you route it on | v3 control verbs are TCP; see section 5 |

## 3. What is in `contract.json`, key by key

Everything a device author needs is inline. Nothing in the file points outside
the export folder.

| Key | What it holds | What a runner does with it |
|---|---|---|
| `_provenance` | `commit` (12 hex) and `tool` | guard only |
| `contract_version` | integer, 3 | refuse to run on any other value |
| `verbs` | one row per verb: `address`, `direction` (`up` or `down`), `typespecs`, `args`, `transport` (`tcp` or `udp-ok`), `pre_role`, `notes` | the shapes your send path and handlers must match; `pre_role` false means the session must not send it without a role; `transport` is the channel Control sends a down verb on |
| `link` | `arg_types` (`b`, `f`, `i`, `s`), `max_message_bytes` 4096, `service_is_dev_id` true | what the o2lite link must be able to do; the dev id is the O2 service name |
| `limits` | `dev_id_max_len` 31, `max_message_bytes` 4096, `reserved_dev_ids` (`terrarium`) | validate your own dev id |
| `lifecycle` | `hello_interval_s` 5.0, `stale_timeout_s` 15.0, `cue_horizon_s` 0.06, `bench_tolerance_ms` `{frame: 50, heartbeat: 1000}` | the hello cadence is the only one the session implements; the others explain what Control does. (`lobby_double_tap_window_s` is gone: Control no longer reads taps as a join.) |
| `lifecycle_notes` | one sentence per `lifecycle` key, with units | read once; the `cue_horizon_s` note says a runner must always use a step's own `at` and never compute it |
| `instruments.tuneshroom_rev1` | `instrument` (name, 12 pixels, five capabilities, no ambient, no functions) and `triggers` | the threshold guard above; the instrument name is what hello's fourth argument declares |
| `scenarios` | index rows: `name`, `profiles`, `summary` | iterate this, not a directory glob, to pick which files to replay and in which profiles |
| `step_schema` | `notation`, `t`, `tolerance`, `link_down_delivery`, `placeholders`, `scenario_fields`, `kinds` | the format reference for the runner; section 6 restates it |
| `replay_notes` | seven plain sentences | the rules a scenario file alone does not state: no delivery while down, malformed steps are delivered and must be dropped, the hand-authored frames in `timed_frames_hold_last` and `link_loss_keeps_display`, accept steps are inputs, `$ROUND` substitution, and which verbs go TCP |

Inside `step_schema`:

- `t` is integer milliseconds from the scenario's start. `steps` is sorted by
  `t`, stably. Steps sharing one `t` are delivered and checked in file order.
- `kinds` has eight entries. Four are inputs: `link` (a bare string, `up` or
  `down`), `control_sends` (`address`, `typespec`, `args`, `at`, optional
  `malformed`), `gesture` (`kind`, `onset_t`, then `duration_ms` for tap,
  `held_s` for hold, `signed_g` for swing) and `accept` (`node`, optional
  `round_id`). Four are expectations: `expect_out` (`address`, `typespec`,
  `args`, `stamp_t`, `within_ms`), `expect_frame` (`grb`, 36 ints),
  `expect_play` (`name`, `params`, `within_ms`) and `expect_quiet`
  (`addresses`, `for_ms`).
- `control_sends.at` is the presentation time on the `t` timeline. Only
  `/leds` ever carries one. Every other verb's `at` is `null`, meaning no
  presentation time, not due at t=0.
- `placeholders`: `$DEV` (your own dev id, in `control_sends.address` and
  `expect_out.args`), `$KEY` (a validation chime key, always inside
  `key=$KEY`, in `control_sends.args` and `expect_play.params`; match the
  shape `key=<integer>` and never compare the number), `$ROUND` (the round
  id, in `control_sends.args` and `expect_out.args`; substitute one fixed
  string in what you deliver and expect your device to echo it) and `*`
  (any value, in `expect_out.args`).
- `scenario_fields.device` is `{"handshake": null or {"node": str,
  "ack_after_ms": int}}`. It is the device's own accept policy, an input:
  non-null means that on every link-up the device answers the **first**
  `/$DEV/handshake` it receives, `ack_after_ms` later, with
  `/game/handshake ["$DEV", <that round id>, node]`. Null means it never
  accepts on its own.

## 4. The session interface a device should expose

The point of a session boundary is that a scenario means the same thing on a
phone and on the board. mm-tuneshroom's `lib/host/README.md` states the
C-portable shape its `DeviceSession` (in `lib/host/device_session.dart`)
exposes; the firmware's session library should take the same inputs and give
the same outputs (spec 6.3 and 7).

**Six inputs:** `linkUp(protoversion)`, `linkDown(reason)`,
`onMessage(envelope, atMs)` (returns whether it acted), `onGesture(gesture)`
(an already classified gesture), `acceptHandshake(node)` (the person
accepted; the session sends `/game/handshake` with the round id it holds),
and `tick(nowMs)`, the only source of time. There are no timers and no
callbacks inside the session.

**Three outputs:** `takeEffects()`, one ordered list of `Send{envelope,
stampMs}` and `Play{name, params}` (one list because order is contract: the
`tick` sample plays immediately before its tap is sent); `frame`, the GRB bytes
to show; and `snapshot`, a plain value with equality (phase, role config,
instrument, held round id, handshake pending, deny reason, link state and so
on).

**Two invariants.** First, `linkUp` means the transport handshake is already
complete and service ownership is proven; the session never sees a half-open
link, and in replay a `link: "up"` step stands for "transport handshake
already done" (not to be confused with the instrument handshake, which is
part of the session). Second, the session knows one integer-millisecond
timeline and nothing else. O2 seconds never enter it: the host converts an
outgoing `stampMs` to an O2 stamp and an incoming O2 timestamp to `atMs`. On
firmware `nowMs` is O2 time in milliseconds, so both conversions are the
identity.

**Mapping onto the firmware plan's names.** The plan's modules are transport
and hardware, and the session sits between them:

| Session call | Plan interface it wraps or replaces |
|---|---|
| `linkUp` | called once `link_begin()` has run and Task A2's `verify_ownership()` has returned true; `link_synced()` alone is not enough |
| `linkDown` | called when `link_poll()` sees WiFi drop or ownership lost (A2 sets `joined = false` there; the session owns that flag now, and drops the round id with it) |
| `onMessage` | the bodies of A2's `on_role`, `on_room`, `on_leds`, `on_play`, `on_release` handlers, plus new `on_handshake`, `on_validated`, `on_deny` and `on_error`, with `o2l_get_timestamp()` converted to `atMs` |
| `onGesture` | fed from A4's `TouchClassifier` and `SwingDetector` in `src/sense/gestures_core.h`, which stay outside the session |
| `acceptHandshake` | called by the firmware when it sees its accept gesture (the reference is a double tap) while the session reports a handshake pending; the node comes from NFC when touched, else `""` |
| `tick` | called from `loop()` with `link_time()` in ms; drives the hello cadence (replacing A1's `last_hello` check in `link_poll()`) and A3's `frames_tick(now)` |
| `takeEffects` | drained after every input and tick: `Send` goes to `link_hello()`, a new `link_handshake()`, or A4's `link_send_gesture()`; `Play` goes to the A5 sample player. There is no `link_join()` any more |
| `frame` | what A3's `frames_push`/`frames_tick` queue currently shows, after `frames_limit` |

Keep the queue in A3's `src/render/frames_core.h` and the classifiers in
`src/sense/gestures_core.h` Arduino-free, as the plan already does, and put the
session beside them under `lib/`, as spec section 7 recommends. The native test
build links those three and nothing from `src/`.

## 5. The wire, the device rules, and the firmware checklist

### 5.1 The v3 wire

From spec section 3.3, with the transport column the export's `verbs` rows
carry. "Pre-role" is the `pre_role` column: whether a device may send (up) or
must accept (down) the verb before it holds a role.

| Verb | Dir | Typespec, args | Transport | Pre-role | Notes |
|---|---|---|---|---|---|
| `/game/hello` | up | `ssss` dev, name, protoversion, instrument (bare `s` still accepted) | TCP | yes | On link-up and every 5 s. Control sends `/room` only on first contact and on state or registration change, never per beat |
| `/<dev>/handshake` | down | `s` round_id | TCP | yes | "You may validate for this round." Sent on the first hello in SETUP and every 5 s invite cycle until the device validates, the lobby is full, or SETUP ends |
| `/game/handshake` | up | `sss` dev, round_id, node | TCP | yes | Received Handshake: the person accepted. `round_id` echoes the latest `/<dev>/handshake`; `node` `""` asks for the Bit's default scored role, else a Registration Node or Room node id |
| `/<dev>/validated` | down | `ss` round_id, role | TCP | yes | The accept succeeded and a scored slot is reserved for `role`. Stop prompting. The role itself arrives at start |
| `/<dev>/deny` | down | `ss` reason, hint | TCP | yes | A refused accept. Reasons: `not connected`, `registration closed`, `scored full`, `no such node`, or a capability miss. `hint` is always filled |
| `/<dev>/role` | down | `b` the composed role blob | TCP | no | Once per round, at start (or at once for a hello into a running round). `class` and `scored` say scored or jam |
| `/<dev>/release` | down | (none) | TCP | no | Ends the role; does not clear the display |
| `/<dev>/room` | down | `b` blob | TCP | yes | Informational; hardware may ignore it |
| `/<dev>/error` | down | `ss` context, message | TCP | yes | A refusal that changes no state |
| `/<dev>/leds` | down | `b` 36-byte GRB frame | UDP | yes | High rate; loss tolerated. Also carries the lobby's white invite and green ceremony flashes when a Room is loaded |
| `/<dev>/play` | down | `ss` name, params | UDP | no | A device-local sample by name |
| `/game/start` | up | `ss` dev, key | TCP | yes | A keyed admin start; Rev 1 does not send it |
| `/game/tap` | up | `sffi` dev, peak_g, duration_ms, count | UDP | no | Gameplay only; Control never reads a tap as a join |
| `/game/hold` | up | `sfi` dev, held_seconds, count | UDP | no | |
| `/game/swing` | up | `sfi` dev, signed_peak_g, count | UDP | no | |
| `/game/join` | up | **retired** | | | A v3 Control answers it with `/<dev>/error ["join", "retired in contract v3: use /game/handshake"]` and otherwise ignores it. Not in `verbs` |

`tilt`, `shake`, `canvas`, `capture` and `telemetry` are in `verbs` too and
unchanged; Rev 1 sends none of them.

### 5.2 The device rules, and what the recordings check

| # | Rule | Scenario that pins it | What the recording checks | Concrete behavior |
|---|---|---|---|---|
| 1 | Hello goes out once the link is up, then every 5 s over TCP | `boot_hello_heartbeat`, and every other scenario | `expect_out /game/hello "ssss" ["$DEV", "*", "*", "*"]` within 50 ms of t=0, 5000 and 10000; only one `/room` (t=0) for three hellos | `linkUp` queues a hello and `tick` queues another once 5000 ms have passed, on a grid so a late tick neither drifts nor bursts. Always send the four-argument form; the fourth is the instrument name |
| 2 | Before a role, only `hello`, `handshake` and `start` go out; no gesture, tap included | `boot_hello_heartbeat` (`expect_quiet` on handshake, tap, hold, swing for 12 s with no accept), `gestures_after_role` (`expect_quiet` on tap, hold and swing until the role at t=1000) | no listed address has a send time in the window | gate every gesture on a **received** `/role`, not on a sent accept or a `/validated` |
| 3 | The handshake: hold the round id from `/<dev>/handshake`; on the accept gesture send `/game/handshake` with it; stop prompting on `/validated` or `/deny` | `handshake_validate_then_role`, `handshake_over_cap_deny`, `handshake_stale_round`, `deny_stays_hellod`, `room_node_handshake_binds` | `expect_out /game/handshake "sss" ["$DEV", "$ROUND", node]` at the policy's or the `accept` step's time; a stale echo (`"stale"`) gets no answer at all | a `/validated` reserves a slot but is **not** a role: nothing changes on the pixels until `/role`. A `/deny` leaves the device hello'd and still invited. A Room-node accept gets neither `/validated` nor `/role`; the fixture's frames simply start arriving |
| 4 | At start every connected device gets exactly one `/role`: scored if it validated, else jam | `handshake_validate_then_role` (scored, `class` `UNIQUE`), `handshake_over_cap_deny` and `join_retired_error` (`jammer`, `class` `JAM`), `jam_solo_fallback` (`solo:tuneshroom_rev1`, `class` `JAM`), `late_hello_gets_jam` (a hello into a running round gets jam in the same millisecond) | the role blob's `role`, `class` and `scored` keys | treat a scored and a jam role identically on the device: parse the blob, render, send the gestures its `uses` names. `class` values are upper case |
| 5 | A frame shows at its presentation time; when several are due only the newest shows; the last holds through silence | `timed_frames_hold_last` | pixels at t=3000, 6500, 7500 and 13000 | **newest by presentation time, not by arrival.** The two `/leds` steps at t=7000 are hand-authored: blue with `at` 7200 is sent first, red with `at` 7100 second. Both expect_frames at 7500 and 13000 want blue. Plan Task A3's `FrameQueue::due` picks the most recently pushed due frame and drops the rest, so it shows red and fails here. Select by greatest `at`, ties to the later arrival; drop an older timed arrival than the frame on screen |
| 6 | `/release` ends the role; frames already queued still show and the last holds | `release_keeps_display` | a dim non-black frame at t=4000 and again at t=9000 after release at t=3621; tap, hold and swing quiet for 5 s; hello continues at t=5000 | release arrives untimed in the same millisecond as the fade's last frame, which is stamped 60 ms later (and on a real link they travel on different channels, TCP and UDP). Do not clear the queue or the pixels on release (spec D5); do keep the heartbeat |
| 7 | `count` is the taps in one gesture and Rev 1 sends 1; stamps mark onset | `gestures_after_role`, `play_known_and_unknown`, `error_no_state_change`, `timed_frames_hold_last` | `expect_out` with `stamp_t` equal to the gesture's `onset_t`, within 1 ms; tap args `["$DEV", 0.0, <duration_ms>, 1]`, hold `["$DEV", <held_s>, 1]`, swing `["$DEV", <signed_g>, 1]` | `peak_g` is 0.0 for a touch tap. The O2 timestamp on the message is the onset time, so a hold released after 650 ms is stamped at touch-down. The accept double tap is the firmware's own business: it becomes `/game/handshake`, never two `/game/tap`s before a role |
| 8 | An unknown sample is ignored; a malformed or unknown message is dropped; an `/error` changes nothing | `malformed_dropped`, `play_known_and_unknown`, `error_no_state_change`, `join_retired_error` | the three flagged steps at t=1200 are delivered, then a tap still sends, `tick` still plays and the role's frame still shows at t=3000; after a jammer's refused hold (`/error ["hold", "jammer role uses tap only"]`) a later tap still plays | validate the blob before touching state: a `/leds` blob that is not exactly 36 bytes is dropped, never truncated (A2 already does this); a `/role` that does not decode to a JSON object with a `role` string is dropped |
| 9 | No session resume: a lost link ends the role and the round id; after 15 s of silence Control has dropped the device, which starts over | `link_loss_rejoin` (in SETUP: hello, a fresh `/handshake`, accept, `/validated` again at t=17000 and 17300), `link_loss_keeps_display` (while RUNNING: hello at t=18000 and a fresh jam `/role`) | the `expect_out` hello after each `link: up`, and in `link_loss_rejoin` the second `/game/handshake` | `linkDown` drops the role and the round id. A device that wrongly kept its round id would answer nothing new; one that kept its role would send gestures with no role Control knows. A rev1 device keeps its last frame lit through the outage (section 8) |

Two Control behaviors the recordings also show and a session must tolerate: a
newly granted role opens with a signature of about 1.5 s that ignores light
cues (the last signature frame is sent about 1518 ms after the role), and with
a Room loaded the validation ceremony is two green flashes and then
`/play ["chime", "key=$KEY"]` about 1.8 s after the accept, with the invite's
already-queued second white flash still showing once in between (t=415 in
`handshake_validate_then_role`). Only `/<dev>/handshake` is an invite; a white
frame never is.

### 5.3 Firmware checklist (spec section 8)

This is the list filed for mm-devshroom. Each item names the scenarios that
fail without it.

1. **One identity.** Derive the dev id from the MAC (for example
   `ts-<last 6 hex>`, at most 31 characters) and use it for both
   `o2l_set_services` and every hello and handshake argument, replacing the
   separate `DEVICE_ID` and `O2_SERVICE_NAME` constants. (`limits`,
   `link.service_is_dev_id`.)
2. **Hello** (`ssss`, instrument `tuneshroom_rev1`) on link-up and every 5 s.
   (`boot_hello_heartbeat`, every scenario.)
3. **On `/<dev>/handshake`:** store the round id and show a prompt (the white
   flash arrives as frames anyway when a Room is loaded). On a double tap
   while a round id is held, send `/game/handshake [dev, round_id, node]`,
   with `node` from NFC if a tag was touched, else `""`.
   (`handshake_validate_then_role`, `handshake_stale_round`,
   `room_node_handshake_binds`.)
4. **On `/<dev>/validated`:** clear the prompt. **On `/<dev>/deny`:** clear
   it, and log `reason` and `hint`. (`handshake_over_cap_deny`,
   `deny_stays_hellod`.)
5. **On `/<dev>/role`:** parse the blob; send gestures only after a role is
   **received**, not after a send. (`gestures_after_role`,
   `late_hello_gets_jam`, `jam_solo_fallback`.)
6. **On `/<dev>/release`:** keep the last frame, drop the role, stop
   gestures. (`release_keeps_display`.)
7. **On link loss:** drop the role and the round id; start over on link-up.
   (`link_loss_rejoin`, `link_loss_keeps_display`.)
8. **Never send `/game/join`.** (`join_retired_error` shows the answer a v2
   device gets; no scenario ever asks a device to send it.)

Until this lands, the current firmware's `/game/join` gets the retirement
`/error`; it still hellos, so it gets a jam role at start and stays usable on
the bench (spec section 7.5).

## 6. Writing the runner in C or C++

mm-tuneshroom's `test/replay/scenario_runner.dart` is the reference. The nine
rules below are its design addendum's section 6
(`docs/superpowers/specs/2026-09-21-device-session-and-replay-design.md` in
mm-tuneshroom), restated for a C or C++ test build and updated for v3.

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
   fixed integer, `$ROUND` replaced by one fixed string for the whole
   scenario, and `atMs` taken from the step's `at` (null stays null). Honor
   `step_schema.link_down_delivery`: while the last `link` step was `down`,
   skip the step and count it. `gesture` becomes your gesture struct with
   `onsetMs` from `onset_t`.
4. **Malformed steps are delivered.** Snapshot the state and the frame, call
   `onMessage`, then assert the snapshot is equal, the frame is equal and no
   effects were produced. (This cannot see a frame parked in the timed queue
   with a future `at`; no exported scenario does that today.)
5. **`device.handshake`.** When it is non-null, the runner (not the session)
   owns the policy: after each `linkUp`, watch for the first
   `/$DEV/handshake` the session receives and call
   `acceptHandshake(node)` exactly `ack_after_ms` later on the fake clock.
   Add that time to the tick grid. Do it once per link-up. When it is null,
   never accept except on an `accept` step.
6. **`accept` input steps.** An `accept` step (`{"node": "..."}`) is the
   person accepting at its own `t`: call `acceptHandshake(node)` when the
   runner delivers it, and check the `expect_out /game/handshake` that
   follows it the ordinary way, never as a cue to send anything. When the
   step also carries `round_id` (only `handshake_stale_round` at t=300, with
   `"stale"`), make the session send that literal instead of the round id it
   holds; a test hook on the session is the simplest way. The v2 `join` step
   kind and `device.join_node` are gone; an exported file that still has
   either is stale.
7. **Matching, with the exact tolerances:**

   | Expectation | Match |
   |---|---|
   | `expect_out` | address and typespec equal; args element-wise with `$DEV`, `$ROUND` (the fixed string from rule 3), `*` and numbers within 1e-3; send time in `[t, t + within_ms]` inclusive; when `stamp_t` is non-null the message's stamp within 1 ms of it. Consume the matched send so two identical expectations need two sends (the double tap at t=3000 in `timed_frames_hold_last`) |
   | `expect_play` | name equal; params equal after turning `key=$KEY` into `key=<integer>`; time in `[t, t + within_ms]`; consumed |
   | `expect_quiet` | no send of a listed address with time in `[t, t + for_ms)`, half-open |
   | `expect_frame` | all 36 values equal at `t`, or else all 36 equal at `t + 23` |
   | malformed `control_sends` | state equal, frame equal, no effects |

8. **Loud on drift.** An unknown step kind (including the retired `join`), an
   unknown gesture kind, an unknown `link` value, an unknown profile tag, a
   `device` field other than `handshake`, or a `contract_version` other than
   3 must fail the test with the file and step index, never skip.
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
`test/contract_replay_test.dart` carries must-fail cases beside the replays.
Port these; each is an inline scenario or a stub target:

| Must-fail case | How the Dart test builds it |
|---|---|
| wrong frame | link up, then `expect_frame` with 36 nines at t=100; the single failure mentions `frame` |
| missing send | `expect_out /game/tap` with no gesture before it |
| quiet-window breach | `expect_quiet` on `/game/tap` for 500 ms, then a tap at t=100 |
| wrong stamp | a tap at t=100 and an `expect_out` with `stamp_t` 150; the message must name both 150 and the 100 it got |
| wrong argument | same tap, `duration_ms` 999.0 in the expectation while address and typespec match |
| wrong round echo | deliver `/$DEV/handshake ["$ROUND"]`, accept, and have a stub session echo a different string; the `expect_out /game/handshake` must fail |
| malformed step that changes state | deliver a flagged `/$DEV/bogus` to a stub target whose `onMessage` increments a counter; the failure mentions `malformed` |
| unknown step kind | a step `{"t": 5, "teleport": {}}`, and a v2 `{"t": 5, "join": {"node": "X"}}`, must each throw or fail loudly |
| `timed_frames_hold_last` without a timed queue | run the real session with the hold-until-due mode off (show every frame on arrival); the report must fail and one failure must be at t=7500 |

Also prove the runner rules: a `control_sends` inside a down window is never
delivered and the session has no role afterwards; an `accept` step is
delivered as an input and its own `expect_out` checked like any other send,
never inferred from it; the `device.handshake` policy accepts once per link-up
and only after the first invite; `$KEY` matches `key=<integer>` and not a
specific number.

**Mutation check.** Once everything is green, neuter one matcher at a time
(make `expect_frame` always pass, drop the stamp comparison, widen the quiet
window to zero, accept any round echo) and run the suite. It must go red each
time. Then revert.

## 8. What the recordings pin, and what they do not

**Pinned by a scenario:** every rule in section 5.2. A device that gets any
of them wrong fails a replay.

**Pinned only by its consequence:**

- **"A lost link ends the role and the round id."** A device's internal state
  never crosses the wire, so no exported step can name it. What the
  scenarios check is what follows: in `link_loss_rejoin` the device accepts
  the fresh invite after reconnecting (a device that kept a stale round id
  would echo it and be dropped silently), and in `link_loss_keeps_display`
  the device hellos from scratch and is granted a fresh jam role as a
  running-round walk-up, not the scored role it held before (a scored slot is
  won only in SETUP).
- **"Rev 1 keeps its display through a link loss."** `link_loss_keeps_display`
  holds a role across a link drop with a later `expect_frame` at t=9000
  (hand-authored, per `replay_notes`: this recorder cannot observe a device's
  own screen during an outage, only what Control sends, and during the outage
  the device hears none of it).

This was **inferred** from plan Task A2's reconnect code, which only sets
`joined = false` and clears nothing on the NeoPixels; it was not measured.
Victor should still confirm on the bench that the board really keeps its
pixels when WiFi drops, and say so in mm-devshroom's `README.md` or a test,
before mm-devshroom's replay tests adopt `link_loss_keeps_display`. If the
board blanks, the app's decision 2 (and this scenario) is what needs
revisiting, not the board.

**Not pinned:** the channel each message arrives on. The recordings carry no
per-step transport; `verbs[].transport` says it, and only the bench replay
over a real link exercises it.

## 9. Open items a firmware author will meet

1. **`any` retags.** `boot_hello_heartbeat`, `deny_stays_hellod` and
   `handshake_validate_then_role` are candidates to be retagged `any`; on the
   board both tags run the one profile, so nothing changes for firmware.
2. **Spec 4.2 quotes the script-form invocation**, which does not run. Use
   the module form in section 2.
3. **Hands-free reconnect is not in the contract.** Plan Task A2 step 2 does
   reconnect and re-check ownership; the scenarios only see the resulting
   `linkDown` and `linkUp`. The bench replay (spec section 8 of the kit
   design, not yet run) is the only place that path is exercised end to end.
4. **`/play` scheduling and the microphone are out of scope** for the
   contract. `expect_play` checks name and params inside a window and nothing
   about audio timing.
5. **Plan Task A3's queue selects by arrival order** (section 5.2, rule 5).
   Fix it before wiring the runner, or `timed_frames_hold_last` will be the
   first red test.
6. **The accept gesture is the device's choice.** The contract fixes only
   the `/game/handshake` it produces. The reference is a double tap; the
   firmware's own classifier must keep it out of the gameplay tap stream
   (before a role there is no gameplay tap stream anyway, rule 2).
