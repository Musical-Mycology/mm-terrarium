# Device contract kit: one checked contract for the Tuneshroom app and Rev 1 firmware

**Date:** 2026-09-16
**Status:** Approved design, pre-implementation. Brainstormed and approved
section by section with Chris, 2026-09-15 and 16.
**Amends:** [`2026-09-11-student-hardware-track-esp32-design.md`](2026-09-11-student-hardware-track-esp32-design.md)
§4.1 and §10, and its plan (see §9).
**Driver:** Three programs now speak the device side of Control's wire:
- the Python Testshroom (`harness/`)
- the Flutter Tuneshroom app (mm-tuneshroom), over o2ws
- the Rev 1 ESP32 firmware (mm-devshroom), over o2lite

The contract exists only as Python message builders, `GAME_VERBS` and prose. The app's Dart copy is maintained by hand, and nothing can check an outside device against the contract. Before any firmware existed, the approved ESP32 plan had already drifted from Control in ways that would have failed on the bench (§1).

This spec makes the contract executable:
- mm-terrarium owns a verb table and scenarios recorded from Control's real engine, and exports both to each device repo.
- Each device replays the scenarios in its own language.
- The contract gains what Rev 1 needs.
- The app gets a hardware profile, so Bit testing on phones matches the board.
- The work is scheduled against the student hardware track's gates.

---

## 1. Drift found before any firmware existed

The approved plan,
[`plans/2026-09-11-student-hardware-track-esp32.md`](../plans/2026-09-11-student-hardware-track-esp32.md),
against Control and the app as they stand:

| Plan | Control or app today | Effect on the bench |
|---|---|---|
| A1 sends `/game/hello` with typespec `s` | A device declares its instrument in hello `args[3]` (`ssss`) | The board resolves to DEFAULTSHROOM |
| A4 adds `/game/hold` and `/game/swing` | Neither is in `GAME_VERBS` (`devicelink/o2_transport.py`), and no task adds them | o2lite drops both before Control sees them, so the Mushica handlers in C2 never run |
| A4 sends `count` as a running total | The lobby treats any `count >= 2` as a double tap (`control/lobby.py`) | After the first tap, every tap joins a waiting lobby |
| A4 polls gestures whenever the clock is synced | Control answers a non-tap gesture from an un-joined device with `/<dev>/error` | One error per gesture before a role |
| A2 never wires release, and its reconnect re-runs `link_begin()` (which registers handlers) without re-checking service ownership | `harness/o2_shroom.py` re-checks when `o2l_bridge_id` changes; `o2l_method_new` only ever adds handlers | Release behavior is undefined, and handler entries pile up on each reconnect |
| A5 bundles only `tick` and `hold` | The lobby ceremony sends `/play chime "key=<midi>"` | Joins are silent on the board |
| Gesture thresholds are constants | Control ships thresholds in the role blob's `triggers` | The board and the app can disagree |

Two more gaps sat outside the plan:

- **Static looks went dark on phones.** With a role held, the app fell back to the instrument's ambient light, or to black, when no frame had arrived for 2 s (mm-tuneshroom `lib/host/frame_priority.dart`). Control sends a frame only when it changes, and the board holds its last frame, so the gap showed only on phones.
- **No blob support.** The firmware's first vendored o2lite predated blob arguments, so the board could not read `/role`, `/room` or `/leds`. That is fixed in the firmware repo, and §4.2 makes it a stated link requirement.

## 2. Goals and non-goals

**Goals**

1. The firmware learns device behavior from recorded scenarios, not by reading Dart or Python.
2. Bit and gameplay testing on phones matches what the Rev 1 board does.
3. A contract change reaches every device repo as a failing test, not as a surprise in a live room.
4. Nothing new lands on the firmware's path before the Fri Sep 18 hello checkpoint.

**Non-goals**

- **Shared device code.** No C++ core used by the firmware directly and by the app through FFI or wasm.
  - Revisit after Dec 4, and only for rendering, if phones must render pixel-identically to hardware.
  - This kit's scenarios are the safety net for that move.
- **JSON Schema.** mm-terrarium does not depend on `jsonschema`, and the scenarios are example-based.
- **Runtime version negotiation.** An export's provenance commit shows when a device repo has drifted.
- **Microphone, `/play` scheduling and the RGBW white channel.** Rev 1 sends W = 0.

## 3. Decisions

| # | Decision | Rejected |
|---|---|---|
| D1 | Rev 1 firmware lives in its own repo, **mm-devshroom**, owned by Victor | `firmware/` in mm-terrarium (ESP32 spec §4.1); a firmware repo only after the show |
| D2 | The app gets a **hardware profile** (`profile=rev1`) for Bit and gameplay testing, and the **full profile** stays for feature prototyping | Always full-featured; freezing the app at Rev 1 |
| D3 | **Contract kit:** a verb table plus recorded scenarios, exported to every device and replayed there | A shared C++ core; the app's Dart modules as the spec |
| D4 | This spec lives in mm-terrarium, next to the contract and the ESP32 spec it amends | mm-tuneshroom |
| D5 | `/release` ends the role but does not clear the display | Blanking on release |
| D6 | Rev 1 devices always send `count = 1`, and Control pairs double taps | Double-tap detection on the device |
| D7 | New verbs `/game/hold` and `/game/swing`, capabilities `gesture.hold` and `gesture.swing`, and a new catalog instrument `tuneshroom_rev1` | Changing the capabilities of `tuneshroom` |
| D8 | Mushica's player role `requires` the Rev 1 capabilities | Relying on convention |
| D9 | The board synthesizes the join chime from `key=` | A silent join on hardware |
| D10 | The board declares `testshroom` for TEST-room bring-up and switches to `tuneshroom_rev1` when Mushica lands | Switching now: TestBit's player node requires `gesture.tilt` |
| D11 | Vendored o2lite comes from o2 `src/` (blob arguments, 4096-byte messages) with two documented Arduino-core build patches, built on pioarduino espressif32 55.3.311 | Vendoring the demo copy unchanged; tracking the `stable` platform |

## 4. Architecture

### 4.1 Ownership and flow

| Repo | Role | Implements the contract as |
|---|---|---|
| mm-terrarium | Owns the contract: verb table, contract Bit, recorder, export tool | Control |
| mm-tuneshroom | Commits an export and replays it through `DeviceSession` | The app, in its `rev1` and `full` profiles |
| mm-devshroom | Commits an export and replays it against Arduino-free libraries in a native test build | The Rev 1 board |

A contract change flows one way:

1. Change the verb table, and Control if needed, in mm-terrarium.
2. Re-record the scenarios. The recorder test's diff shows exactly what a device will now see differently.
3. Re-export into each device repo.
4. Each device's replay tests fail until the device matches. The new export and the device fix land together.

Device repos never change a verb or a scenario themselves. The wire changes only through this flow.

### 4.2 The export

`tools/export_contract.py <out-dir>` writes a folder that each device repo commits at `test/contract/`:

- **`contract.json`**
  - **`_provenance`:** `{commit, tool: "export_contract/1"}`, stamped the way `export_solo` does.
  - **`contract_version`:** an integer, bumped on any change a device can observe.
  - **`verbs`:** one row per verb, each with:
    - address (`/game/<verb>` or `/<dev>/<verb>`)
    - direction
    - allowed typespecs
    - argument names
    - transport (`tcp` or `udp-ok`)
    - whether the verb is allowed before a role
  - **`link`:** what a device's o2lite link must do:
    - decode argument types `s`, `f`, `i` and `b`
    - accept messages up to 4096 bytes
    - send `tcp` verbs reliably
    - offer the dev id as its O2 service name
  - **`limits`:** the message cap, dev id rules and reserved ids, copied from the code.
  - **`lifecycle`:** the hello interval (5 s), the stale timeout (15 s), the lobby's double-tap window (1.5 s), and the tolerances the live bench replay uses.
  - **`instruments.tuneshroom_rev1`:** the carried instrument view and triggers, produced by the same serializer `export_solo` uses. Devices test their built-in Rev 1 thresholds against it.
- **`scenarios/<name>.json`:** one scenario per file (§4.3).

### 4.3 Scenarios

**Where scenarios cut in.** A scenario sits above the transport and above gesture detection:

- **Transport.** Links decode a message before anything else sees it: role and room become JSON objects, and leds become bytes. The o2ws string encoding never appears in a scenario.
- **Gestures.** Detection is device-specific: a phone uses its screen and accelerometer, the board its touch pad and LIS3DH. Scenarios inject gestures that are already classified.

**Steps** run on one timeline, `t`, in milliseconds from the scenario's start.

- **Inputs:**
  - `control_sends`: a recorded Control message, with its presentation time `at` (on the same timeline as `t`) when it has one
  - `gesture`: `tap`, `hold {held_s}` or `swing {signed_g}`
  - `link`: `up` or `down`
- **Expectations:**
  - `expect_out`: a message the device must send, with `$DEV` and `*` placeholders
  - `expect_frame`: the pixels showing at `t`
  - `expect_play`: a sample name and its params
  - `expect_quiet`: verbs that must not be sent from `t` for `for_ms` milliseconds

**Tags.**
- `rev1` scenarios must pass on the board and in the app's hardware profile.
- `any` scenarios must pass in every device profile.
- Full-profile behavior (solo mode, ambient light, previews) stays in the app's own tests until it is ported to firmware.

**Timing.**
- In-process replay runs on a fake clock and allows a frame to land one render tick late.
- The live bench replay (§8) uses the `lifecycle` tolerances.

```json
{
  "name": "timed_frames_hold_last",
  "profiles": ["rev1"],
  "steps": [
    {"t": 0,    "control_sends": {"address": "/$DEV/role", "typespec": "b", "args": [{"role": "player"}]}},
    {"t": 100,  "control_sends": {"address": "/$DEV/leds", "typespec": "b", "at": 600, "args": ["<36 bytes: green>"]}},
    {"t": 600,  "expect_frame": "<36 bytes: green>"},
    {"t": 9000, "expect_frame": "<36 bytes: green>"}
  ]
}
```

*(Abbreviated. Real files carry the recorded blob and bytes.)*

**Rules the scenarios pin:**

1. Hello goes out only once the link is up, then repeats every 5 s over TCP.
2. Before a role, a device sends only `hello`, `join`, `start` and `tap`. Simulators may also send `canvas`.
3. A frame shows at its presentation time. When several are due, only the newest shows. The last frame holds through silence.
4. `/release` ends the role. Frames already queued still show, and the last one holds.
5. `count` is the number of taps in one gesture, and Rev 1 devices always send 1. Gesture timestamps mark the gesture's onset.
6. An unknown sample name is ignored. A malformed or unknown message is dropped without changing state.
7. There is no session resume: after 15 s of silence Control has dropped the device, which must join again.

**The first eleven `rev1` scenarios**

| # | Scenario | Covers |
|---|---|---|
| 1 | `boot_hello_heartbeat` | Rule 1; hello declares an instrument; nothing else goes out before a join |
| 2 | `explicit_join_role` | Joining a node and receiving the role |
| 3 | `lobby_tap_join` | Invite frames show before a role; two count-1 taps; role; `chime` with `key=` |
| 4 | `timed_frames_hold_last` | Rule 3 |
| 5 | `gestures_after_role` | Tap, hold and swing shapes and stamps; `expect_quiet` for hold and swing before a role |
| 6 | `deny_stays_hellod` | A deny leaves the device hello'd with its heartbeat running |
| 7 | `release_keeps_display` | Rule 4; the heartbeat continues |
| 8 | `play_known_and_unknown` | A known sample plays; an unknown name is ignored |
| 9 | `link_loss_rejoin` | Rule 7 |
| 10 | `error_no_state_change` | `/error` changes nothing |
| 11 | `malformed_dropped` | Rule 6 |

Re-checking service ownership after a reconnect happens inside the link, so only the bench replay can cover it.

## 5. mm-terrarium changes

### 5.1 Verb table

`devicelink/contract.py` holds one row per verb, with the columns listed in §4.2.
- `GAME_VERBS` is derived from its device-to-Control rows.
- A test checks every builder in `devicelink/protocol.py` against the table.

Rows that change for Rev 1:

| Verb | Typespec | Meaning |
|---|---|---|
| `/game/tap` | `sffi [dev, peak_g, duration_ms, count]` | `count` is the number of taps in the gesture (always 1 on Rev 1); `peak_g` is 0 for touch taps; stamped at onset |
| `/game/hold` (new) | `sfi [dev, held_seconds, count]` | Sent on release; stamped at touch-down; `count` 1 |
| `/game/swing` (new) | `sfi [dev, signed_peak_g, count]` | Negative means left; stamped at onset; `count` 1 |

`hello`, `join` and `start` are `tcp`; gestures are `udp-ok`. The hold and swing shapes are the plan's own, so Mushica's planned handlers don't change.

### 5.2 Capabilities and catalog

- **Capabilities.** Add `gesture.hold` and `gesture.swing` to `CAPABILITY_VOCABULARY` in `control/instrument.py`, and document them in `docs/carried-instrument-schema.md`.
  - Event trigger names are already unrestricted (`[A-Za-z0-9_-]+` with numeric thresholds), so the trigger validator needs no change.
- **New instrument: `instruments/tuneshroom_rev1.toml`.**
  - 12 pixels.
  - Capabilities `light.pixels`, `gesture.tap`, `gesture.hold`, `gesture.swing` and `audio.samples`.
  - No ambient light, no functions and no `[solo]` table.
  - Event triggers, from the plan's constants: `tap {max_ms = 250}`, `hold {min_ms = 400}` and `swing {peak_g = 1.5, window_ms = 80}`.
  - When Victor tunes these on the bench, this file changes and the contract is re-exported.

### 5.3 Mushica gates on Rev 1

- **The requirement.** Mushica's player role (plan C2) declares `requires` with an `InstrumentRequirement` for `light.pixels`, `gesture.tap`, `gesture.hold`, `gesture.swing` and `audio.samples`. This follows TestBit's example in `bits/test/test_bit.py`.
- **Who gets in.** A device declaring `tuneshroom`, as the app's full profile does, is refused by name. The board and the app's hardware profile are admitted.

### 5.4 Contract Bit, recorder and export

- **Contract Bit.** Test-only, and kept out of `bits/`. Gameplay Bits can then change without touching the scenarios. It:
  - has one player node gated on the Rev 1 capabilities
  - sends a timed look, then goes quiet
  - plays one known sample and one unknown one
  - is unloaded to trigger a release
- **Recorder.** A pytest rig built from `tests/test_lobby_agent.py`'s shared fake clock and Room-with-fixtures setup, running Control on `O2LiteTransport` over `FakeO2Lite`. (`tests/test_capture_o2.py` and `tests/test_terrarium_boot.py` already show those pieces working together.) The rig:
  - records each message's send time by wrapping `send`
  - drops `_svcheck` probes
  - decodes blobs
  - replaces dev ids with `$DEV`, keys with `$KEY`, and absolute O2 times with offsets from the start
  - scripts the device's heartbeats

  Scenarios are single-device. The invite loop iterates a set, so with more than one device the message order isn't stable.
- **Regression test.** Re-records every scenario and fails on any diff against the committed files.
- **Export.** `tools/export_contract.py`, a sibling of `tools/export_solo.py`.

### 5.5 A Control ordering to confirm while recording

- **The concern.** Control sends `/release` with timestamp 0 after the closing fade, but the fade's frames carry `clock() + horizon`. Reading `devicelink/agent.py` (not observed), a device can receive `/release` before the fade's last frames.
- **What's done about it.** D5 makes devices tolerant of that order. The recorder should confirm it; a later Control change can then stamp `/release` after the fade.

## 6. mm-tuneshroom changes

### 6.1 Hardware profile (first)

- **Selection:** `?profile=rev1` or `--dart-define=PROFILE=rev1`, resolved in `lib/sim/join_params.dart` the same way `admin` is. The default stays `full`.
- **Hello:** declares `tuneshroom_rev1`. Arguments 1 and 2 stay `flutter-sim` and `o2ws/1`, because Control picks the o2ws encoding from the protoversion.
- **Off in this profile:**
  - solo mode (the app starts dark, like the board)
  - function cards and previews
  - ambient light
  - tilt and shake
  - the count-2 double tap on the display
- **Gestures,** from two new detectors in `lib/sense/` named after the plan's `TouchClassifier` and `SwingDetector`:
  - A screen press shorter than `tap.max_ms` sends a tap with `count` 1 and `peak_g` 0.
  - A press held past `hold.min_ms` sends hold with its duration when released, stamped at touch-down.
  - A press between the two thresholds sends nothing, as on the board.
  - Swing is read from the accelerometer's lateral axis crossing `swing.peak_g` for `swing.window_ms`, with sign. Left and right buttons cover browsers without a usable accelerometer.
  - Once a role is held, thresholds come from the role blob's `triggers`. Before that (for the lobby join taps), they come from built-in constants that a test pins to `instruments.tuneshroom_rev1` in the export. The board does the same.
- **Sound:** `tick` for a tap and `hold` for a hold, each played immediately before its gesture is sent, as plan A5 does on the board. `tool/make_samples.py` generates both to A5's spec: a 30 ms 1 kHz click and a 200 ms low hum.
- **Release:** follows D5. In this profile, `/room` IDLE and UNLOADING are informational only.
- **Surface tiles:** `surfaceActive` in `lib/sim/sim_controller.dart` gains `hold` (from `gesture.hold`) and `swing` (from `gesture.swing`).
- **Docs:** the app's `CLAUDE.md` and the mm-tuneshroom deep-dive link to this spec.

### 6.2 Frame hold with a role (first, alongside 6.1)

In both profiles, while a role is held, the last frame from Control stays up indefinitely. The 2 s fallback applies only when no role is held, as in the lobby or solo mode.

- **Why:** this is the fix mm-tuneshroom's `docs/superpowers/specs/2026-08-31-flexible-instrument-host-design.md` already named: fall back only on link loss or role release.
- **Nothing depends on the old fallback:** no Bit relies on ambient light returning mid-round, and `tuneshroom` declares no ambient light.

### 6.3 `DeviceSession` (second)

- **What moves:** the session decisions leave `SimController` and become `DeviceSession` in `lib/host/`. That module is the `HostState` the port map listed but nobody built. It is Flutter-free, so `test/module_boundaries_test.dart` already covers it.
- **Interface,** in C-portable terms:
  - **In:** `linkUp()`, `linkDown(reason)`, `onMessage(envelope)`, `onGesture(gesture)`, `tick(nowMs)`.
  - **Out:** messages to send, the frame to show, samples to play, and a plain state snapshot the UI reads (phase, role, deny and error reasons).
  - **No timers inside:** time arrives only through `tick`.
- **Frame queue:** `DeviceSession` owns the timed frame queue.
  - On web, the queue releases each frame at once, because `o2ws.js` already delivers frames at their time.
  - On native builds and firmware, the queue does the holding.
- **Profiles:** both profiles run this one state machine. Solo mode, previews and ambient light are switched on per profile.
- **What stays in `SimController`:** an adapter for the link, the ticker, the sample player, pixel conversion and `ChangeNotifier`.
- **Order of work:** a pure refactor comes first, under the existing `test/sim_controller_test.dart` with behavior unchanged. The replay runner comes after.

### 6.4 Scenario replay (after 6.3)

- **Where the export lives:** it is committed at `test/contract/` and re-exported the same way as `assets/solo/tuneshroom.json`.
- **The runner:** a test builds a `DeviceSession` for each profile a scenario is tagged with, feeds the scenario's inputs on a fake clock, and checks its expectations.

## 7. mm-devshroom recommendations

Victor owns this repo. These recommendations reach him through the plan amendments in §9.

- **Code layout:**
  - Arduino-only code (WiFi, touch pad, LIS3DH, NeoPixel, I2S) stays in `src/`.
  - Session logic and tap, hold and swing classification go in Arduino-free libraries under `lib/`.
  - The session library takes the same inputs and outputs as `DeviceSession` (§6.3), so a scenario means the same thing on both devices.
- **Tests:**
  - Add a native test environment to `platformio.ini`, and replay the `test/contract/` scenarios in `test/` on a Mac.
  - A small generator, in the style of the plan's `wav2h.py`, embeds the scenario files. A header-only JSON library parses them, in the tests only.
  - The same tests assert that the vendored o2lite has blob functions and a 4096-byte message limit, and that the built-in Rev 1 thresholds match the export.
- **Build:** pin `platformio.ini` to pioarduino espressif32 55.3.311 (Arduino-ESP32 3.3.11), the release the o2lite update was built against.
- **Timing:** adopt this once A2 is in and the first export exists. Boot, join, timed frames, release and link loss come first; gestures follow with A4.

## 8. Bench replay: a feasibility test first

- **Question:** can a replay driver take the GameServer's place inside the normal Terrarium startup, and replay scenarios to a real board or phone within the `lifecycle` tolerances? Arco and the `game` service would stay owned by the standard boot.
- **What's known:** there is no hub-only mode today, Control starts Arco itself, and a second process offering `game` is silently refused.
- **Fallback to try:** Arco started by hand, plus a scripted o2lite client that verifies it owns `game`.
- **Budget:** two days, ending in a go or no-go and the chosen approach.
- **On a no-go:** Gate 1 relies on in-process replay plus the manual checks in the ESP32 spec §3.1.

## 9. Amendments to the ESP32 spec and plan

- **Spec §4.1 and §10:**
  - The firmware lives in mm-devshroom.
  - The Tower Room profile and Mushica stay in mm-terrarium.
  - Plan paths drop the `firmware/` prefix.
- **A1:**
  - Hello is `ssss [dev, "", "", instrument]`, sent once the link is up, over TCP, every 5 s. The instrument follows D10.
  - Vendoring follows D11 and replaces "Do not edit": `VENDORED.md` records the upstream commit and the patches, and the patches are reported upstream.
- **A2:**
  - Release follows D5.
  - Handlers are registered once.
  - After a reconnect, re-check service ownership when `o2l_bridge_id` changes, then join again.
- **A3:** only the newest due frame shows, and the last frame holds.
- **A4:**
  - Gestures other than tap wait for a role.
  - `count` is 1, and timestamps mark onset.
  - Hold and swing shapes follow §5.1.
- **A5:** synthesize the chime from `key=` (D9).
- **C2:** hold and swing are registered through the verb table, and Mushica declares `requires` (§5.3).
- **D3 (deep-dive sync):** record the contract kit and the firmware repo in `docs/MM_TERRARIUM.md`.
- **New task:** adopt scenario replay in a native test environment (§7).

## 10. Schedule

| When | Work | Why then |
|---|---|---|
| Now to Fri Sep 18 | Outside this spec: the firmware's hello checkpoint and its o2lite update (blob support) | Friday's checkpoint; A2 and A3 cannot read roles or frames without blobs |
| By Mon Sep 21 | **Phase 1, mm-terrarium:** verb table with hold and swing, capabilities, `tuneshroom_rev1`, and the §9 amendments | A2, A3 and A5 start that week, and A4 the week after, against these definitions |
| By Fri Sep 25 | **Phase 2, mm-tuneshroom:** hardware profile and frame hold (§6.1, §6.2) | Mushica becomes playable in the app the week of Sep 28 and needs hold and swing from phones |
| Weeks of Sep 21 and 28 | **Phase 3, mm-terrarium:** contract Bit, recorder, export (§5.4) | The firmware's replay tests can't start until the first export exists |
| Weeks of Sep 28 and Oct 5 | **Phase 4, mm-tuneshroom:** `DeviceSession` and replay (§6.3, §6.4) | Refactoring after the profile ships keeps it from blocking Mushica |
| Week of Oct 5 | **Phase 5:** bench replay feasibility test (§8) | So a working replay can run against the Tuneshroom first article at Gate 1 (Fri Oct 9) |

**Plans:**
- one for mm-terrarium (phases 1 and 3)
- one for mm-tuneshroom (phases 2 and 4)
- a short one for phase 5

## 11. Testing

- **mm-terrarium:**
  - the verb table matches `GAME_VERBS` and every builder
  - `tuneshroom_rev1` passes catalog validation
  - re-recording the scenarios produces no diff
  - an export test, modeled on `tests/test_export_solo.py`
  - Mushica refuses `tuneshroom` and admits `tuneshroom_rev1`
- **mm-tuneshroom:**
  - hardware-profile controller tests
  - frame-hold tests
  - `DeviceSession` unit tests
  - scenario replay
  - the module-boundaries test, unchanged
- **mm-devshroom:** native scenario replay, plus the link and threshold checks in §7.
- **Live:** the bench replay if §8 is a go. Either way, the ESP32 spec's §3.1 checks remain the acceptance test.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Phase 1 slips past Mon Sep 21 | §5.1 and §9 already specify the shapes and rules the firmware needs; the table only codifies them |
| Recorder nondeterminism: set iteration order, when the idle breath starts, join-order keys | Single-device scenarios, offsets from the start, `$KEY` |
| Bench replay isn't feasible without a hub-only mode | A two-day budget; the fallback is in-process replay plus the §3.1 checks |
| Swing on phones depends on how the phone is held | The left and right buttons are the reliable path |
| Chime synthesis doesn't fit the board's audio path | The ceremony stays silent on hardware, and the app's hardware profile matches |
| `/release` arrives before the fade's last frames (§5.5) | D5 |
| o2lite discovery can hang on a non-matching mDNS result | Reported upstream |
