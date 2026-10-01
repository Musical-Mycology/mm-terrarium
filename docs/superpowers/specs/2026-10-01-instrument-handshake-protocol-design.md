# Instrument handshake protocol: hello, handshake, validate, scored or jam

**Date:** 2026-10-01
**Repos:** mm-terrarium (Control, harness, contract kit, docs), mm-tuneshroom
(device session, simulator, contract replay; paired PR in this pass),
mm-devshroom (firmware checklist, handed to Victor)
**Status:** implemented in mm-terrarium and mm-tuneshroom (branches claude/instrument-handshake-protocol-833d7c, claude/instrument-handshake-v3); Task 1 TCP relay confirmed (o2 commit f21499e, src/bridge.cpp:411-414).
**Supersedes:** the registration flow in `docs/control-gameserver-design.md`
and the lobby double-tap join of
`2026-09-11-metronome-lobby-and-admin-start-design.md` (the lobby's visuals,
ceremony and admin start stay).

## 1. Problem

Today a device gets a role in one of two unrelated ways, and neither tells
the device or Control enough:

- **Explicit join.** `/game/join <dev> <node>` grants a role at once, scored
  or jam, with no opt-in from a person. This is what mm-devshroom's
  `src/o2_test/` firmware does, once per link with no retry (its
  `origin/main`, `cce3dec`; `docs/MM_TERRARIUM.md` claims a 5 s resend that
  the current firmware does not do).
- **Lobby double tap.** In SETUP, un-joined devices get two white flashes
  every 5 s; a double tap is pulled out of the `/game/tap` stream
  (`DoubleTapDetector`, `control/lobby.py`) and runs a full scored join. The
  "invite" exists only as pixels (the Testshroom infers it from "every byte
  >= 200"), and a re-tap after the grant leaks into gameplay.

Then, at SETUP to RUNNING, **nothing is sent**: joined devices keep the role
from SETUP, and connected devices that never joined get no role at all and
sit dark until they `/game/join` a jam node themselves.

Every down message is UDP (`devicelink/contract.py`: Control uses
`o2lite.send` with no `tcp=`), including `/role` and `/deny`, and nothing is
ever acknowledged. A lost `/role` leaves Control believing a device joined
that never learned it did.

## 2. Goals

1. One entry path: **Hello, Handshake, Received Handshake, Validated**, then
   exactly one role per device at RUNNING.
2. A per-Bit cap on validated (scored) devices.
3. At RUNNING every connected non-fixture device gets a role: **scored** if
   it validated, else **jam**.
4. A Bit with no jam role still gives jammers something alive: the carried
   instrument's **Solo** behaviour.
5. Control-verb delivery that cannot silently drop.
6. Fold in every cleanup the review found (section 9).
7. A working test path at every layer: unit, contract kit, live TestBit
   smoke, and the mm-tuneshroom browser simulator.
8. Docs and generated diagrams updated in the same change.

Non-goals: native iOS/Android connectivity, Room liveness, a scoring
framework, writing mm-devshroom firmware ourselves.

## 3. The protocol

### 3.1 Per-device states (Control-side, per Bit round)

```
CONNECTED --(SETUP, not FULL: /handshake sent)--> INVITED
INVITED --(/game/handshake accepted)--> VALIDATED   (scored slot reserved)
INVITED --(/game/handshake refused)--> INVITED      (/deny; stays hello'd)
VALIDATED --(reaped, 15 s silence)--> gone          (slot frees; FULL -> WAITING)
CONNECTED|INVITED --(SETUP -> RUNNING)--> JAM       (/role, jam)
VALIDATED --(SETUP -> RUNNING)--> SCORED            (/role, scored)
new hello during RUNNING --> JAM                    (/role, jam, at once)
SCORED|JAM --(Bit completes / aborts / reaped)--> CONNECTED (fade, /release)
```

Room fixtures never enter this machine (section 3.5).

### 3.2 Round id

`GameServer.load_bit` mints a `round_id`: a short string unique per load
within the process (`f"{bit_name}-{counter}-{token}"`, `token` 6 hex chars
from `secrets`). It is the only thing that ties an ack to this round. It is
cleared at UNLOADING.

### 3.3 Wire (contract v3)

| Verb | Dir | Typespec, args | Transport | Notes |
|---|---|---|---|---|
| `/game/hello` | up | `ssss` dev, name, protoversion, instrument (bare `s` still accepted) | TCP | Connect and heartbeat (every `HELLO_INTERVAL_S`, 5 s). Sends `/room` only on **first contact** (dev new to the pool) and on state or registration change, never on every beat |
| `/<dev>/handshake` | down, **new** | `s` round_id | TCP | "You may validate for this round." Sent on first hello while SETUP and not FULL, then each invite cycle (5 s) until the dev validates, the lobby goes FULL, or SETUP ends. The schedule is the agent's own (it runs with or without a Room or lobby) and needs the Bit to have a scored node. The white x2 flash stays as the visible cue; a validation cancels that dev's still-queued flashes |
| `/game/handshake` | up, **new** | `sss` dev, round_id, node | TCP | "Received Handshake": sent when the user makes the device's accept gesture (the device decides which; the reference is a double tap). `node` empty = the Bit's default scored role; else a Registration Node id (NFC/QR) |
| `/<dev>/validated` | down, **new** | `ss` round_id, role | TCP | Ack accepted, slot reserved for `role`. The device stops prompting; the green ceremony plays as today |
| `/<dev>/deny` | down | `ss` reason, hint | TCP (was UDP) | Refused ack. `hint` is now filled (section 3.6) |
| `/<dev>/role` | down | `b` (or `s` for `o2ws/*`), the composed config blob, shape unchanged | TCP (was UDP) | Sent **once per round, at RUNNING** (or at a RUNNING walk-up's first hello). `class` and `scored` keys say scored or jam |
| `/<dev>/release` | down | unchanged | TCP (was UDP) | |
| `/<dev>/room` | down | unchanged | TCP (was UDP) | |
| `/<dev>/error` | down | unchanged | TCP (was UDP) | |
| `/<dev>/leds` | down | unchanged | UDP | High rate; loss is tolerated |
| `/<dev>/play` | down | unchanged | UDP | |
| `/game/join` | up | **removed** | | A v3 Control answers it with `/<dev>/error ["join", "retired in contract v3: use /game/handshake"]` and otherwise ignores it |

Gesture and other up verbs (`start`, `tap`, `tilt`, `shake`, `hold`,
`swing`, `canvas`, `capture`, `telemetry`) are unchanged. Before a role a
device may send only `hello`, `handshake`, `start`; `canvas` stays allowed
for simulators.

**Why TCP for control verbs.** They are low rate, and a TCP send is
reliable while the o2lite link is up; across a link loss the device keeps
its role and round id until a later `/role`, `/handshake` or `/release`
supersedes them (section 8, item 7). This needs no ack verb and no
firmware change to *receive*. `O2LiteTransport.send` routes by the
`VerbRow`'s down transport: `tcp` rows through o2litepy's `send_cmd`, `udp`
rows through `send`. **Assumption to verify first (plan task 1):** Arco
relays a TCP-sent message to an o2lite client over that client's TCP
socket. If it does not, fall back to the "UDP plus resend on mismatch"
option recorded in section 11.

### 3.4 Validation (`/game/handshake` handling)

In order, the first failure answers `/<dev>/deny`:

1. Dev not in the pool (no hello yet): deny `not connected`, hint `send
   /game/hello first`.
2. `node` names a Room node: section 3.5 (round id not checked).
   A dev already bound to a Room fixture that names any other node is
   denied `registration closed`, hint `this device is bound to a Room
   fixture` (materialize would otherwise overwrite its ROOM assignment).
3. No Bit, or state is not SETUP: deny `registration closed`, hint `scored
   slots open only in SETUP; you hold a jam role for this round` (RUNNING) or
   `no Bit loaded` (other states).
4. `round_id` does not match: dropped silently and logged
   (`handshake: stale round <id> from <dev>`); the next `/handshake` carries
   the current id.
5. Already VALIDATED this round: re-send `/<dev>/validated` (idempotent;
   covers a lost-then-retried ack), no ceremony replay.
6. Node walk (today's `RegistrationState` fallback walk) over scored roles
   only, skipping full ones: none fits gives deny `scored full`, hint `the
   Bit's scored slots are taken; you will get a jam role at start`. Unknown
   node: deny `no such node`.
7. Carried-instrument `requires` (moved here from join): a miss gives deny
   with the `satisfies()` reason as `reason`, hint `this role needs: <slot
   contract>`.
8. Accept: reserve the slot in `validated`, send `/<dev>/validated`, fire
   `on_registration_change` (which triggers the ceremony).

**Cap.** The number of validated devices is bounded by the sum of the
scored roles' capacities, lowered by an optional `[lobby] max_scored`
(positive int; `load_bit` refuses a value above a bounded capacity sum).
Unbounded scored roles: section 3.4.1. The lobby is FULL when the cap is
reached: no more `/handshake` sends, existing invites stop.

#### 3.4.1 Unbounded scored roles

A `SHARED` scored role has no capacity. Then the cap is `[lobby]
max_scored` if set, else unbounded (every ack validates). TestBit is
changed so its scored role is bounded (section 7.3).

### 3.5 Room nodes

A `/game/handshake` whose `node` is a Room node binds the armed fixture
exactly as today's Room-node `/game/join` does (`GameServer._bind_room`,
`RoomBindingRegistry`). The operator's arming is the credential, so the
round id is not checked, and it is accepted in every state where arming is
(today SETUP and RUNNING; unchanged). Unarmed: deny `no such node`. A bound
device sends no `/validated` and gets no `/role`; its fixture frames start,
as today. Fixtures are excluded from the RUNNING jam sweep.

### 3.6 SETUP to RUNNING: materialize

`GameServer.run()` (reached only through the single start decider, section
5.4) calls `RegistrationState.materialize(pool)` before `bit.on_run_start()`:

1. Each VALIDATED dev, in validation order, becomes a scored assignment of
   its reserved role.
2. Each other pooled dev that is not a fixture, not closing, and not the
   admin id becomes a jam assignment (section 3.7).
3. For each new assignment, in that order (scored first, then jam):
   compose the role blob, call `Bit.on_join(dev, role)` (guarded), and let
   the agent build the bridge and send `/<dev>/role` over TCP.
4. One `on_registration_change` and one `on_devices_change` after the batch.

`Bit.on_join` therefore fires at RUNNING, not at validation. Bits that learn
turn order from it (MetronomeBit) still see validation order. `Bit.on_join`
gains no new arguments; a Bit that needs the validated set during SETUP
reads `GameServer.registration.validated` (read-only view) through
`status()` or its hooks.

**RUNNING walk-ups.** A first hello during RUNNING from a non-fixture dev
gets a jam assignment at once (same steps 2-4 for one dev). A re-hello from
a dev that already holds a role does nothing new.

### 3.7 The jam role

Resolved per device by `control/jam_role.py`:

1. The Bit's first fitting unscored role: JAM-class roles first, then the
   Bit's other unscored (SHARED or UNIQUE) roles in declaration order,
   skipping a full role and one whose `requires` the carried instrument
   fails (logged). ROOM-class roles are never candidates.
2. Else a synthesized **solo role** for that device's carried instrument:
   - name `solo`, class `jam`, `scored = False`, no capacity;
   - `light_manifest` = `instrument.solo.light_manifest` if the instrument
     declares `[solo]`, else its ambient light manifest;
   - `uses` = the gesture verbs the instrument's `[solo.bindings]` name
     (`tap`, `shake`; `double_tap` maps to `tap` with count 2);
   - triggers = the carried instrument's event triggers (as every granted
     blob already carries);
   - no `ugen_manifest` (audio stays device-local samples, as Solo is).

   The bindings are served Control-side: a gesture from a dev holding a
   synthesized solo role is resolved against its instrument's
   `[solo.bindings]` and fires that instrument function on that dev through
   the existing fire ladder (`fire_function` step 2, instrument SCRIPTED
   functions). Unbound gestures are dropped. An instrument with no `[solo]`
   (e.g. `defaultshroom`) gets its ambient and no bindings.

The synthesized role is added to the registration's role table under a
per-instrument name (`solo:<instrument>`) at materialize time, so
`counts()`, the Console rollup and uplink `players[]` (class `jam`) see it
like any jam role.

### 3.8 Release and round end

Unchanged: Bit completion or abort releases every assignment with the
closing fade, then `/<dev>/release` (now TCP), then `drop_dev` unless the
dev spoke during the fade. Released devices stay in the pool and are
invited again at the next SETUP.

## 4. Diagrams (generated)

Sources in `docs/diagrams/`, rendered by `tools/render_diagrams.py` into
`docs/MM_TERRARIUM.md` between its markers:

- **`player-flow.seq` (rewritten):** Tuneshroom, Arco, Control lanes.
  hello, /room; /handshake plus white invite flash; user accepts;
  /game/handshake; /validated plus green ceremony (bell, chime); start;
  /role (scored) over TCP; gameplay; /release. Notes for the over-cap deny
  and the stale round.
- **`device-lifecycle.d2` (new):** the section 3.1 state machine, with the
  RUNNING walk-up edge, the reap edge, and the deny loop.
- **`role-assignment.seq` (new):** the RUNNING transition: materialize,
  scored roles in validation order, then jam roles (Bit jam or synthesized
  solo), `Bit.on_join` per device, `/role` per device over TCP.

`docs/diagrams/manifest.json` gains the two new entries.

## 5. Terrarium changes (mm-terrarium)

### 5.1 `control/registration.py`

- `validated: dict[str, str]` (dev to reserved role, insertion order =
  validation order).
- `validate(dev, node, state) -> JoinResult` (section 3.4 steps 5-6).
- `materialize(pool_devs, jam_resolver) -> list[Assignment]`.
- `counts()`, `granted()` and `start_condition.scored_count` count
  validated devs as scored during SETUP.
- `_assign` is idempotent: assigning the held role is a no-op (no
  `on_join`, no new bridge).
- `release(dev)` clears both `validated` and `assignments`.

### 5.2 `control/engine.py`

- `join()` removed; `handshake(dev, round_id, node) -> JoinResult` added
  (section 3.4), and `hello()` grants a RUNNING walk-up (section 3.6).
- `round_id` minted in `load_bit`, cleared at UNLOADING.
- `run()` calls `materialize` (section 3.6).
- The `requires` refusal path calls the `on_release` sink, so the agent
  drops any stale bridge (fixes the leaked LED stream).
- One default instrument: `DEFAULTSHROOM` replaces the `TUNESHROOM`
  fallback.
- `_notify` stays private; the agent's call becomes a public
  `notify_devices_changed()`.
- `lobby_state` exists once (the engine method; `control/lobby.py`'s free
  function is removed or made the method's implementation, not both).

### 5.3 `control/jam_role.py` (new)

Section 3.7. Pure, stdlib, unit-tested on its own.

### 5.4 One start decider

`control/start_condition.start_decision` and `control/lobby.decide_start`
merge into one pure decider in `control/start_condition.py`, covering keyed
admin starts, `immediate`, `operator`, `players`, timeouts and
`min_scored`. Every start goes through `GameServer.request_start(key,
source_dev, source)`, including the harness timer (`source="timer"`), so
the accept flash and `on_start_requested` fire on every start.
`terrarium_boot._wait_in_setup` stops calling `gs.run()` directly.

### 5.5 `devicelink/`

- `contract.py`: `VerbRow` gains `handshake` (up), `handshake` and
  `validated` (down); `join` removed; down rows carry their transport
  (`tcp` for role, deny, release, room, error, handshake, validated; `udp`
  for leds, play). `GAME_VERBS` still derives from the up rows.
- `protocol.py`: builders for the new verbs; `tests/test_devicelink_contract.py`
  checks them against the table.
- `o2_transport.py`: `send()` routes by the down row's transport;
  `drain_new_clients` and its stale comment removed.
- `agent.py`:
  - the invite cycle sends `/<dev>/handshake` with the white flash;
  - `_on_handshake` replaces `_on_join`; a `/game/join` gets the
    retirement `/error`;
  - `DoubleTapDetector`, tap interception and `_handshake_join` removed;
    taps are always gameplay;
  - at RUNNING, builds bridges and sends `/role` per materialized device;
  - hello sends `/room` only on first contact;
  - reaping a never-joined dev calls `drop_dev` and clears canvas and
    lobby state;
  - the double `lobby.forget` collapses to one site;
  - `/ie<N>` docstrings become `/<dev>`.
- `lobby_runtime.py`: `stop()` lets an in-flight ceremony's queued cues
  play out (bell, chime) instead of clearing them; new invites stop.
- `control/role_config.py`: the flat `config["instrument"]` write that is
  always overwritten is removed.

### 5.6 `FakeO2Lite`

Learns `send_cmd`, records the channel per sent message, and stays as
strict as o2litepy (boundary rule 5): a test can assert `/role` went TCP.

### 5.7 Harness

- `harness/o2_shroom.py`: `--handshake` answers `/<dev>/handshake` with
  `/game/handshake` after a simulated accept (default: immediately; flag
  `--handshake-delay SECONDS`), instead of watching for white frames.
  `--join-retry` and the explicit-join path are removed. Gestures gate on
  a received `/role` (unchanged `_gestures_ready`).
- `harness/shroom_client.py`: handles `/validated` and `/handshake`.
- `harness/run_stack.py`: stage markers `HANDSHAKE VALIDATED: <dev> <role>`
  and `ROLE GRANTED: <dev> scored|jam <role>` (constants
  `HANDSHAKE_VALIDATED` and `ROLE_GRANTED` in `harness/markers.py`, each
  failure marker with a remedy); `DEVICE_JOIN_DENIED` (`JOIN DENIED:`) is
  informational, not a failure: an over-cap device is denied and ends jam.
  `--handshake-devices N` keeps its meaning (the first N devices accept),
  the rest only hello and must end jam.

## 6. Contract kit (v3)

- `CONTRACT_VERSION` 2 to 3 in `tools/export_contract.py`.
- Scenario `device` field: `join_node` replaced by `handshake: {node: str,
  ack_after_ms: int} | null` (null: the device never accepts). The accept
  is a runner **input**, never inferred from an `expect_out`.
- Scenarios (re-recorded with `tools.record_scenarios`):
  - `explicit_join_role` and `lobby_tap_join` replaced by
    `handshake_validate_then_role`;
  - new: `handshake_over_cap_deny`, `handshake_stale_round`,
    `late_hello_gets_jam`, `jam_solo_fallback`, `room_node_handshake_binds`,
    `join_retired_error`, `link_blip_keeps_role` (a reconnect inside 15 s
    while RUNNING: nothing re-sent, the held role still plays);
  - `boot_hello_heartbeat` (no `/room` per beat), `deny_stays_hellod`,
    `link_loss_rejoin`, `gestures_after_role` updated to handshake
    semantics; the rest re-recorded unchanged in intent.
- `contract_kit`'s `ContractBit` declares a bounded scored role and a jam
  role; a second test-only bit (no jam role) backs `jam_solo_fallback`.
- `docs/device-contract-guide.md`: header fixed (v3, scenario count from
  `ALL_SCENARIOS`), device rules rewritten (section 8 checklist), wire table
  with the transport column.

## 7. Test path

### 7.1 Unit (offline)

`validate`/`materialize`; cap and `max_scored`; stale round; ack in
RUNNING; idempotent re-ack; reap frees a validated slot (FULL to WAITING,
invites resume); RUNNING walk-up gets jam; Bit jam role vs synthesized
solo; solo with and without `[solo]`; solo bindings fire the instrument
function, unbound gestures drop; `requires` refusal drops the bridge;
TCP/UDP routing per verb; the single start decider (each `when`, keyed,
timer source gets the accept flash); ceremony survives a start inside
1.8 s; `/game/join` gets the retirement error; hello sends `/room` only
on first contact; reaped never-joined dev leaves no transport state.

### 7.2 Contract kit

Section 6; `tests/test_contract_scenarios.py` fails on any diff.

### 7.3 Live smoke (TestBit and SoloTestBit)

- **TestBit** (`bits/test/`): `player` becomes `UNIQUE`, capacity 1,
  still scored; `jammer` stays the declared jam role.
- **SoloTestBit** (`bits/solotest/`, `[console] hidden`, TEST): one scored
  `UNIQUE` role, capacity 1, **no** jam role, so jammers get the solo
  fallback.
- Recipe, each under `--ci`:
  `./smoke-test.sh --bit TestBit --devices 3 --handshake-devices 2
  --expect-scored 1` and the same with `--bit SoloTestBit`. Expected:
  exactly one `ROLE GRANTED: ... scored`; one over-cap `JOIN DENIED:` then
  `ROLE GRANTED: ... jam`; one never-accepted device `ROLE GRANTED: ...
  jam`. `run_stack --expect-scored 1` asserts these counts.
- MetronomeBit and Rev1Bit run under `--ci` in the same pass; MinigameBit
  and CaptureBit are checked to load and still make sense (CaptureBit's
  unscored `recorder` becomes everyone's jam role, which is its intent).

### 7.4 mm-tuneshroom (paired PR, this pass)

- `lib/host/device_session.dart`: `join`/`joinNode` replaced by
  `acceptHandshake({String? node})`; handles `/handshake` (stores the
  round id, exposes `handshakePending`) and `/validated`; no join on
  link-up; handles the `join` retirement `/error` as a visible error.
- `lib/link/envelope.dart`: mirrors `devicelink/protocol.py` for the new
  verbs.
- Simulator (`lib/sim/`): an **Accept** control, enabled while a handshake
  is pending; a real double tap also accepts; the node panel
  (`lib/sim/node_panel.dart`) sends `/game/handshake` with its node (also
  how a simulated Room fixture binds).
- `test/contract/`: re-export from this repo at v3; the replay passes.
- `bits/` (GlowBit): roles checked for sense under scored-or-jam.
- Its deep-dive updated via the `mm-deepdive-sync` flow.

### 7.5 mm-devshroom

Not built here (section 8). Until it lands the dev shroom's `/game/join`
gets the retirement error; it still hellos, so it gets jam at RUNNING and
stays usable on the bench.

## 8. Firmware checklist (mm-devshroom, handed to Victor)

Filed as an mm-devshroom issue and mirrored in the contract guide:

1. One identity: derive the dev id from the MAC (e.g.
   `ts-<last 6 hex>`, at most 31 chars), and use it for both
   `o2l_set_services` and every hello/handshake arg (replaces the separate
   `DEVICE_ID` and `O2_SERVICE_NAME` constants).
2. Hello (`ssss`, instrument `tuneshroom_rev1`) on link-up and every 5 s.
3. On `/<dev>/handshake`: store the round id, show a prompt (the white
   flash arrives as frames anyway). On a double tap while a round id is
   held: send `/game/handshake [dev, round_id, node]` (node from NFC if
   touched, else empty).
4. On `/<dev>/validated`: clear the prompt. On `/<dev>/deny`: clear it,
   log reason and hint.
5. On `/<dev>/role`: parse the blob; gestures are sent only after a role
   is **received** (not after a send).
6. On `/<dev>/release`: keep the last frame, drop the role, stop gestures.
7. On link loss: keep the role and the round id (and any validation);
   hello again on link-up. A later message supersedes them: a new
   `/<dev>/role` replaces the held role; a `/<dev>/handshake` while a role
   is held means that role's round is over, so drop the role (and the
   validation) and treat the handshake as a fresh invite; `/<dev>/release`
   ends the role as in item 6.
8. Never send `/game/join`.

## 9. Cleanups folded in

All from the review, each with a test in section 7.1:

1. Idempotent assignment (no `on_join` or opening-signature replay on a
   retry).
2. `requires` refusal leaked the old bridge and sent no release.
3. Ceremony bell and chime cut off by a start within 1.8 s.
4. Two start deciders; the timer path skipped `on_start_requested`.
5. `TUNESHROOM` vs `DEFAULTSHROOM` default instrument.
6. Reaped never-joined devs left transport, canvas and lobby state.
7. `JoinResult.hint` never set.
8. Hello pushed `/room` and fired `on_devices_change` every 5 s per device.
9. Gesture stream polluted by lobby tap interception.
10. Dead code: `drain_new_clients`, flat `config["instrument"]`, duplicate
    `lobby_state`, double `lobby.forget`, private `_notify` from the agent,
    `/ie<N>` docstrings.
11. Stale docs: device-contract-guide header (v1, eleven scenarios, "other
    than 1"); `MM_TERRARIUM.md`'s mm-devshroom join-resend claim.

## 10. Docs

- `docs/MM_TERRARIUM.md`: *Lobby and join handshake* (rewritten as the
  handshake), *Message vocabulary* (verb table with down transport),
  *Join, release and overrides*, *Start conditions and profiles* (one
  decider), *The device contract* (v3), *Relationships* (mm-devshroom
  paragraph), *`bits/`* (TestBit change, SoloTestBit), diagrams (section 4).
- `docs/device-contract-guide.md`: section 6.
- `docs/control-gameserver-design.md`: a "superseded by" note on its
  registration section pointing here.
- mm-tuneshroom deep-dive: section 7.4.

## 11. Rollout and alternatives

**Order** (each leaves `main` green):

1. This spec.
2. mm-terrarium, one PR: transport and contract table; registration and
   engine; jam role; agent and lobby; start decider; harness; TestBit and
   SoloTestBit; contract kit v3; docs and diagrams.
3. mm-tuneshroom, paired PR, merged right after step 2.
4. mm-devshroom issue (section 8).

**No dual-protocol window.** v2 clients get a visible `/error` on
`/game/join`, still hello, and still get jam at RUNNING.

**Recorded alternatives** (decided against in brainstorming):

- Role at handshake time instead of RUNNING: two role sends per scored
  device and a role that does not mean "game on".
- Keep the gesture-only handshake: devices cannot tell an invite from a
  white frame, taps keep leaking.
- Keep `/game/join` as a bypass: two entry paths to test and keep
  consistent.
- **UDP plus resend on mismatch** (the fallback if plan task 1 finds Arco
  does not relay TCP to o2lite clients): hello carries the held round and
  role, Control resends `/role` on mismatch; recovery up to 5 s.
- JAM-class-only jam role: production Bits (CaptureBit, MinigameBit, Rev1Bit)
  declare only unscored non-JAM roles, so their handlers would never run for
  non-validated devices; decided against in favour of first fitting unscored
  role.
- TCP plus a role ack verb: Console visibility, one more verb; deferred.
