# Bidirectional heartbeat: beat, reply, grace and Looking

**Date:** 2026-10-08
**Repos:** mm-terrarium (contract, Control, Console, Testshroom, contract
kit, docs), mm-tuneshroom (device link and session, simulator, browser
guest, contract replay), mm-devshroom (firmware issue for Victor)
**Status:** implemented in mm-terrarium (branch claude/terrarium-heartbeat-devshroom-d5dec8); mm-tuneshroom and mm-devshroom pending.
**Supersedes:** section 2 of
`2026-08-25-device-liveness-detection-design.md` where it rejects a
Control-side reply ("Control-initiated ping/pong, and any ack"). Its reap
algorithm (section 4) and its Room-device exclusion (section 5) stand.
**Amends:** contract guide rule 9 (a clause for beat-capable devices) and light lexicon gap
G4 (both decided here, section 6).

## 1. Problem

Liveness today runs one way only.

- **Device to Control works.** A device resends its first `/game/hello`
  over TCP every 5 s (`HELLO_INTERVAL_S`, `devicelink/contract.py`); every
  inbound message calls `DevicePool.touch()`, and `reap_stale()` drops a
  device after 15 s of silence (`stale_timeout`, `control/boot_config.py`).
- **Control to device does not exist.** A repeated hello gets nothing back
  (`DeviceLinkAgent._on_hello` sends `/room` on first contact only).
  `/leds` goes out only when a frame changes, and `/room` only on a state
  or registration change. During RUNNING with a static frame, Control sends
  a device nothing at all, so the device cannot tell a quiet round from a
  dead Terrarium. Victor raised this from the mm-devshroom side.
- **The device's own link check is weak.** mm-devshroom (`origin/main`
  `cce3dec`, `src/o2_test/main.cpp:76`) treats `o2l_bridge_id >= 0` as
  connected. o2lite clears it only on a TCP recv error or EOF
  (`lib/o2/o2lite.c:775-782`, `833-839`), and nothing sets TCP keepalive,
  so a Wi-Fi drop or a dead host with no FIN/RST leaves the device "up"
  indefinitely.
- **o2lite health is not Control health.** A device's o2lite link and clock
  sync terminate at Arco; Control is a separate o2lite client of the same
  hub. Arco answering clock pings proves nothing about Control.
- **One timer does two jobs.** The 15 s reap is both "the link is gone"
  and "the player's scored slot is free".

## 2. Prior art

Console controllers keep a reliable link status without an application
"hello every N seconds". Public detail on Xbox wireless (GIP) is thin and
reverse-engineered (Linux `xone`/`xpad`; Microsoft notes sniffed behaviour
is undocumented), but what is visible, plus Bluetooth HID controllers,
points to three properties:

1. **Continuous traffic both ways.** Controllers report every 8 to 15 ms;
   GIP acknowledges packets with a sequence number (`xone`
   `bus/protocol.c`, command 0x01 and the ack-request flag). Any packet
   proves life.
2. **A hard silence timeout on both ends.** Bluetooth's supervision timeout
   (100 ms to 32 s) drops the link symmetrically; the `xone` dongle sets an
   idle-disconnect timer.
3. **The player slot outlives the radio link.** A controller that drops and
   returns keeps its player number.

Network practice agrees: MQTT 3.1.1 (client PINGREQ, server PINGRESP,
server gives up at 1.5 x keepalive, client should close on a missing
reply), Gaffer on Games' virtual connection (any packet resets the timer;
sequence plus ack for loss and RTT), ENet (500 ms ping, 5 s minimum
timeout). TCP keepalive is widely advised against for application liveness
(2 h default, validates the peer kernel, not the app).

## 3. Goals and non-goals

**Goals**

1. Both sides detect a lost link within about 3 s.
2. A short Wi-Fi drop never costs a player their scored slot (15 s grace).
3. A device detects a Terrarium restart.
4. Control measures per-device link quality (RTT, loss) for the Console.
5. Purely additive on the wire: any mix of old and new Terrarium and
   client builds keeps working.
6. Every client adopts it in this effort: mm-terrarium's Testshroom,
   mm-tuneshroom (app, simulator, and its web build, the browser guest),
   mm-devshroom firmware.

**Non-goals**

- Room liveness. Room-bound devices are still never reaped
  (2026-08-25 section 5); they beat and get link stats only.
- Changing the 15 s reap or what reaping does.
- Reliability for any other verb.

## 4. Wire contract

Two new verbs, both `udp-ok`, both `pre_role` (allowed before a role).

| Verb | Direction | Typespec | Args | Meaning |
|---|---|---|---|---|
| `/game/beat` | up | `si`, `sii` | `dev`, `seq`, `rtt_ms` | Device heartbeat. `seq` is 0 on the first beat of each link and increments by 1 per beat (wraps at 2^31). Optional `rtt_ms` is the device's last measured round trip (section 7). |
| `/<dev>/beat` | down | `is` | `seq`, `epoch` | Control's reply to every `/game/beat`, sent at once. Echoes `seq`. `epoch` is 6 lowercase hex characters minted once per Control process (`secrets.token_hex(3)`), fixed for its lifetime. |

- **UDP on purpose.** The beat measures the link; TCP would retransmit and
  hide exactly the loss being measured. o2lite accepts UDP only while its
  TCP link is up, which is fine: no TCP, no link. Browser clients (o2ws)
  have no UDP; their beat rides the websocket.
- **`/game/hello` is unchanged** (TCP, `s`/`ssss`). A beat-capable client
  sends it on first contact and once after every relink, and stops the 5 s
  repeat; beats replace it. A client that never beats keeps its 5 s hello
  and is handled exactly as today.
- **Capability is implicit.** Sending `/game/beat` is the declaration;
  Control marks the device `beats` on its first beat. No hello field
  changes.
- **The contract kit's `CONTRACT_VERSION` bumps 3 to 4** (new verbs, new
  and rewritten scenarios, new lifecycle values). The wire stays
  backward compatible; the bump only forces device repos to adopt the new
  export deliberately.
- **New contract constants** in `devicelink/contract.py`, exported in the
  kit's `lifecycle` block: `BEAT_INTERVAL_S = 1.0`,
  `BEAT_JITTER_S = 0.1`, `LINK_LOST_S = 3.0`, `GRACE_S = 15.0` (equal to
  `stale_timeout`'s default; the contract names it, boot config may still
  override the reap). `HELLO_INTERVAL_S = 5.0` stays for non-beat
  clients.

## 5. Timers

| What | Value | Owner |
|---|---|---|
| Beat interval | 1.0 s, each gap drawn uniformly from 0.9 to 1.1 s | device |
| Device declares the link lost | 3.0 s with no message from Control | device |
| Control marks a device `missing` | 3.0 s with no beat (beat-capable devices only) | Control (display only) |
| Control reaps and frees the slot | 15 s with no message (unchanged) | Control |
| Device falls from Looking to Solo | 15 s lost (same moment as the reap) | device |
| ESP32 Wi-Fi power save | `esp_wifi_set_ps(WIFI_PS_NONE)` | firmware |

Power save: in the default modem-sleep mode an ESP32 may hold received
data up to one DTIM period (typically 100 to 300 ms; third-party sources,
confirm against ESP-IDF docs during the bench). With it off, a 3 s window
tolerates two lost beats plus jitter comfortably.

## 6. Device behaviour

### 6.1 States

```
          first beat reply (epoch E)
 LINKING ----------------------------> LINKED
    ^                                    |
    |  hello sent, seq = 0               | 3 s with nothing from Control
    |                                    v
    +------------- relink <--------- LOOKING ---- 15 s lost ----> SOLO
                                         ^                         |
                                         +------ relink -----------+
```

- **Arming.** The 3 s lost timer starts only after the first `/<dev>/beat`
  reply on the current link. If no reply ever comes (an older Terrarium),
  the device stays in legacy mode: it resends hello every 5 s and never
  enters LOOKING on its own. This rule makes rollout order irrelevant.
- **Proof of life is any message from Control.** `/leds`, `/role`,
  `/room`, `/handshake` and every other down verb reset the 3 s timer, not
  only `/<dev>/beat`.
- **LINKED to LOOKING** (3 s silence): render the lexicon's Looking (slow
  white pulse) over the held frame, and force the transport down (o2lite:
  close the TCP socket and clear the bridge id, so the existing 2 s mDNS
  rediscovery starts now instead of waiting on a socket that may never
  error). Stop beating. o2lite has no public call for this today: its
  `disconnect()` (`lib/o2/o2lite.c:772`) is non-static but not in the
  header, so the firmware either declares it or adds a small
  `o2l_disconnect()` wrapper (a candidate upstream change for Roger).
- **Relink**: on transport up, send `/game/hello`, reset `seq` to 0, resume
  beats. The device is LINKED again at the first beat reply, not on TCP
  connect: only a reply proves Control.
- **LOOKING to SOLO** (15 s lost): the device's slot is gone at Control
  too, so render Solo (the aurora). Keep trying to relink.
- **Epoch change.** If a reply's `epoch` differs from the one this device
  last saw, Control restarted and holds nothing for it: drop the role,
  round id and validation, and send a fresh `/game/hello`.

### 6.2 Contract rule 9 stays, its display clause changes

Rule 9 (`docs/device-contract-guide.md`) already says a lost link keeps the
role, the round id and any validation. That stands: on relink inside the
grace window the device resumes its role with no `/role` re-sent. What
changes is the display: a device holds its last frame for up to 3 s, then
shows Looking, then Solo at 15 s. This decides light lexicon gap G4.

mm-devshroom's firmware currently clears `g_device_registered` on link loss
(`src/o2_test/main.cpp:85-88`), which breaks rule 9 already; the firmware
issue (section 9) fixes that.

## 7. Control behaviour

- **Reply.** `DeviceLinkAgent` handles `/game/beat` by touching the pool
  entry (as every verb does), marking the device `beats`, recording the
  arrival, and sending `/<dev>/beat seq epoch` at once. The epoch is minted
  in `DeviceLinkAgent.__init__`. A beat from a dev not in the pool is
  answered too, without touching the pool or the monitor: after a
  Control-only restart Arco keeps the device's TCP link, so this reply is
  how the device sees the new epoch at once instead of going Looking.
- **Missing.** For a `beats` device, `now - last_seen > LINK_LOST_S` makes
  it `missing`: shown on the Console, nothing else changes. A beat or any
  other message clears it. Non-beat devices never show `missing`.
- **Grace resync.** A `/game/hello` from a device already in the pool and
  marked `beats` is a relink. Control resends that device's current
  `/leds` frame (it clears that device's last-sent frame so the next tick
  sends it whole), because Looking painted over the display. It resends no
  `/role`, `/validated` or `/room`: rule 9 means the device kept them, and
  any state change in between already broadcast its own `/room`. A hello
  from a non-beat device is handled exactly as today (one `/room` on first
  contact, nothing after).
- **Link quality.** Per beat-capable device: an RTT estimate and the loss
  rate over the last 30 beats. Control cannot see the device's send time,
  so loss is inferred from `seq` gaps (beats received vs. `seq` span), and
  RTT is reported by the device: a client may append its measured RTT in
  milliseconds as an optional third arg, `/game/beat sii dev seq rtt_ms`
  (0 or absent means unknown). The Console shows bars from loss and RTT.
  The device computes RTT from its own clock: the time it sent `seq`
  versus the time the matching reply arrived.
- **Reaping is unchanged**: 15 s, same release path, Room-bound devices
  exempt.
- **Console.** Each device shows live, missing or gone, plus signal bars
  for beat-capable devices. Room-bound devices show their link state too.


## 8. Contract kit

Every scenario gains `device.beats` (bool). The recorder's scripted device
runs the reference device state machine (section 6.1) when it is true.

New scenarios (in `contract_kit/scenarios.py`, recorded to
`contract_kit/recordings/`, exported by `tools/export_contract.py`), all
`device.beats: true`:

- `beat_reply_echo`: hello and beat `seq` 0 at link-up, then beats 1 and 2
  a second apart, each answered by `/$DEV/beat` with the same `seq` and
  one fixed epoch; no 5 s hello.
- `beat_link_lost_looking`: RUNNING with a role; Control goes silent (the
  recorder stops ticking it); the device stops beating and reports
  LOOKING 3 s after Control's last message, then SOLO at 15 s.
- `beat_relink_within_grace`: RUNNING; the link drops and is back at 8 s:
  hello, `seq` restarts at 0, Control resends the current frame and no
  `/role`; a tap still plays.
- `beat_epoch_change_rehellos`: a hand-authored reply carrying a new epoch
  makes the device drop its role and send hello.

The existing scenarios are unchanged and carry `device.beats: false`.
They are the legacy-client contract, and they also cover a beat-capable
device facing an older Terrarium: their recordings hold no `/$DEV/beat`
reply, so such a device never arms, keeps the 5 s hello and never enters
LOOKING. A runner replaying them against a beat-capable device ignores
its `/game/beat` outputs (a new `replay_notes` sentence). Contract guide
rule 9 keeps its text for unarmed devices and gains a clause for armed
ones (held frame for 3 s, then Looking, then Solo), and the kit's
`replay_notes` describe LOOKING and SOLO.

## 9. Rollout

1. **mm-terrarium** (first; everything else pins its export)
   - Spec (this file); verb rows and constants in `devicelink/contract.py`.
   - Control: reply, epoch, `missing`, grace resync, link stats.
   - Console: states and signal bars.
   - Testshroom (`harness/o2_shroom.py`): beats, arming, LOOKING, forced
     relink, epoch handling; `--heartbeat-interval` keeps meaning the
     legacy hello interval, and a new `--no-beat` runs a legacy client.
   - Contract kit scenarios, export, `CONTRACT_VERSION = 4`.
   - Docs: `docs/MM_TERRARIUM.md` (device pool section, player-flow
     diagram's heartbeat note), `docs/device-contract-guide.md` (verb
     table, rule 9, section 5.3 firmware checklist),
     `docs/light-lexicon.md` (G4 decided), and a superseded note at the top
     of the 2026-08-25 liveness spec.
2. **mm-tuneshroom** (paired PR): beat sender and watchdog in
   `lib/link/device_link.dart` and `lib/host/device_session.dart`; Looking
   and Solo in the simulator; the web build (browser guest) beats over
   o2ws; replay tests against export v4.
3. **mm-devshroom**: an issue for Victor, as contract v3 went in
   mm-devshroom#7. Checklist: beats and arming; force o2lite down on loss;
   Looking and Solo rendering; keep role state through link loss (rule 9);
   `WIFI_PS_NONE`; replay export v4.

   **Prerequisite: the mDNS discovery hang must be fixed before or with
   the heartbeat firmware.** In `o2ldisc_poll()`
   (`lib/o2/o2liteesp32.cpp:181-203` on mm-devshroom `origin/main`
   `cce3dec`), both `continue` statements (lines 190 and 202) skip
   advancing `r`, so a first mDNS result that is not a usable Arco
   (another ensemble's `_o2proc._tcp`, or a stale or renamed record such
   as "arco (2)") spins the main loop forever, freezing the lights with
   it. It is upstream o2lite code (rbdannenberg/o2 `src/o2liteesp32.cpp`
   lines 182 and 194, present since 2021), reported to Roger on
   2026-09-16 and unfixed in both upstream and the vendored copy.

   The heartbeat makes it worse. Today discovery runs at boot and after a
   TCP error; with section 6.1 it runs after every lost link. Right after
   a Terrarium restart the old Arco's record can still be cached or
   re-advertised under a renamed instance, which is exactly the result
   that hangs, so the epoch-change recovery would freeze the device
   instead of recovering it.

   Fix: iterate as `for (r = results; r; r = r->next)` (or advance `r`
   before each `continue`), as a documented local patch listed in
   `lib/o2/VENDORED.md` alongside the existing ones, and send the same
   change to Roger as a follow-up on the existing report. If Roger has
   fixed it upstream by the time the firmware work starts, re-vendoring
   replaces the local patch. The bench (section 10) includes a second
   O2 host on the LAN to prove discovery skips it.

## 10. Testing

| Layer | Proves |
|---|---|
| Python unit | Reply echoes `seq` with a stable epoch; `missing` at 3 s, reap still at 15 s and not before; a beat-capable relink gets exactly its current frame and no `/role`; a non-beat client's repeated hello still yields one `/room` (guide rule 1); RTT arg parsed in both typespecs; loss from `seq` gaps. |
| Contract kit | The section 8 scenarios replay identically in mm-tuneshroom and mm-devshroom. |
| Harness integration (`run_stack --ci`) | SIGSTOP Control: the Testshroom reports LOOKING within 3.5 s. SIGCONT inside 15 s: same role, gestures play. Past 15 s: a fresh jam role. Control restart: epoch change, fresh hello. A `--no-beat` Testshroom against the new Control behaves as today. |
| Load | 30 simulated beat-capable devices at 1 Hz stay within the 44 Hz tick budget. |
| Bench (real Rev 1 board, with Victor) | AP power-off, Terrarium kill, cable pull: measured detection time on both sides. Device clock after a forced relink (see section 11). Discovery with a second O2 host (another ensemble or a renamed "arco (2)") advertised first, and a Terrarium restart: the device must relink, not hang (section 9, prerequisite). |

**Live check 2026-10-09** (real Arco, MetronomeBit, HEAD 92e3459, SIGSTOP/SIGCONT on the Control process). A 2 device `--ci` run exited 0 with `linking` then `linked` on both devices. With 1 device and a 6 s freeze, Looking appeared 3.25 s after SIGSTOP, Linked again 0.06 s after SIGCONT, and no second `ROLE GRANTED` was logged. With a 20 s freeze, Looking appeared 3.05 s after SIGSTOP and Solo 15.06 s after it; Linked returned 0.04 s after SIGCONT, but the role was kept (no fresh `ROLE GRANTED`), because the queued beats reached Control on thaw before it reaped. A hand-run `--no-beat` Testshroom against the same Control joined, was granted a jam role, displayed 773 frames and printed no `LINK STATE:` lines. The 30 device load run (45 s) did not complete: Arco aborted (SIGABRT in `o2_postpone_delivery`, an O2 assertion) after all 30 devices were granted, twice with beat-capable devices and once with 30 `--no-beat` devices, so the abort is not caused by beats; no tick overrun warning appeared in Control's log in any run, but the 44 Hz budget under 30 devices is not yet measured. The Control-only restart check (new epoch with Arco left up) could not be run: Control spawns and owns Arco, and no Console or `run_stack` path restarts Control alone.

## 11. Risks

- **o2lite stale clock after a forced disconnect.** o2lite resets
  `clock_synchronized` only in `o2l_clock_initialize`, not on disconnect,
  and does not reset its ping schedule (`lib/o2/o2lite.c`, clock ping
  ~1147-1160). A relinked device may stamp gestures with a stale offset.
  The bench checks it; if real, the firmware re-initialises the clock on
  relink and it joins the o2lite defect list for Roger.
- **Wi-Fi beacon loss.** With power save off the 3 s window has margin; if
  the bench shows false LOOKING on a busy venue AP, raise `LINK_LOST_S`
  before touching the interval.
- **Traffic.** 30 devices x 2 small UDP packets per second is negligible on
  the LAN and for Arco's relay; the load test confirms Control's side.
