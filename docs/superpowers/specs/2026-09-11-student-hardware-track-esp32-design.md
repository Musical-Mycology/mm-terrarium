# Student Hardware Track, ESP32 revision: design

**2026-09-11 · Chris Oltyan, chris@musicalmycology.org**
**Status: APPROVED DESIGN.** Supersedes
[`2026-08-06-student-hardware-track-design.md`](2026-08-06-student-hardware-track-design.md).
Feeds [`../plans/2026-09-11-student-hardware-track-esp32.md`](../plans/2026-09-11-student-hardware-track-esp32.md).

Two students (Sophia, hardware; Victor, software) have been full-time since
2026-08-24 and run the hardware track through the 2026-12-04 show. This
revision records the direction set on 2026-09-11 and re-plans the eleven
working weeks that remain.

**Schedule of record:** `MM_Project_Plan.docx` Rev 4 (Chris's Drive, Product
folder). **Experience of record:** the Mushica design document (Google Doc
`1pP6GcT7wglO-s7f3vIHaCOsJ6PPjqvA27Q2GDEh1hAw`). **Tower definition:** Drive
file `1PpfBMbNlIt-sUmyWG4jPh7aZovVvYkVs`. **Software seams:**
`docs/MM_TERRARIUM.md`, `docs/control-gameserver-design.md`,
`docs/carried-instrument-schema.md`, and the o2lite connectivity migration
spec (`2026-09-08-o2lite-connectivity-migration-design.md`).

---

## 1. What changed, and why the old plan is void

| Was (Aug 6) | Is (Sep 11) | Consequence |
|---|---|---|
| Radxa Zero 3W runs the Tuneshroom (Linux, Python `devicelink` client) | **ESP32-P4 with a paired ESP32-C6 radio runs every Instrument, on o2lite firmware** | No OS image, no device-tree overlays, no Python on the device. Firmware is a new track and it starts from zero code. |
| Pi 5 venue box with a DAC HAT runs the Terrarium | **A laptop or Mac runs the Terrarium for Dec 4**; Pi 5 later | Venue box build, DAC verification and the 44 Hz-on-Pi measurement leave the plan. Venue audio is the laptop's line-out. |
| 6 m, 864 px Art-Net array is the Room display | **The Tower is the Room display**: a 2100 mm fixture, eight chainable LED segments, diffusion film, fiber optics, PAR lights, printed connectors, props; ESP32-driven over o2lite | The array's power model and luxaeterna's multi-universe work stay on record for later. The Tower is a *device* the Room binds, not an Art-Net fixture. |
| Two Bits (A, B), two-to-four Tuneshrooms | **One Bit, Mushica, one Tuneshroom, one Tower** through Oct 30; extra Tuneshrooms in November | Scope is a single-player game on exactly two devices. |
| `devicelink` JSON over websocket, o2lite in a late window | **o2lite is the only device wire** (Phase 1 landed Sep 8) | The firmware speaks o2lite from day one. Nothing is built against a wire that will be replaced. |
| Gate 1 Sep 25, Gate 2 Oct 16 | **Gate 1 Fri Oct 9** (hardware viability); Hardware Complete folded into **Alt Ctrl, Fri Oct 30** | Fixed by the project plan; not re-derived here. |

Facts that still shape everything:

1. **The real hardware deadline is Dry Run 2, Fri Nov 13.** Dec 4 is the show.
2. **Any timing figure must be measured on the target hardware.** Mushica's
   perfect window is 30 ms. The firmware's tap timestamp, the o2lite clock
   sync, and Control's `cue_horizon` all sit inside that window; none of them
   may be assumed.
3. **Discovery is zeroconf.** o2lite's ESP32 port (`o2/src/o2liteesp32.cpp`)
   finds the O2 host by mDNS. A Mac Terrarium advertises Bonjour natively; a
   bench AP with client isolation or mDNS filtering breaks discovery and looks
   like a firmware bug. The dedicated flat bench network is a precondition,
   not a nicety.
4. **The migration spec explicitly left the ESP32 firmware port for its own
   plan** (section 8). This is that plan.

---

## 2. Goal

By **Fri Oct 30**: Mushica runs end to end on real hardware, one ESP32
Tuneshroom and one ESP32 Tower, against the Terrarium on a laptop, well enough
to record the Alt Ctrl submission video. By **Fri Nov 13**: the same, from a
cold start, with a spare Tuneshroom.

Explicitly **not** the goal: a general instrument host in firmware. Rev 1
firmware is a *thin device*: it reports gestures with timestamps and displays
the frames Control sends it at the time Control says. The render engine, cue
interpreter and ambient animations that mm-tuneshroom's instrument host
prototypes (`2026-08-31-flexible-instrument-host-design.md`) are ported after
Dec 4, module by module, per that spec's port map.

---

## 3. Deliverables

| Item | Dec 4 | Owner |
|---|---|---|
| Tuneshroom firmware (o2lite link, gestures, 12-pixel renderer, local sample playback) | 1 image, on 2+ boards | Victor |
| Tuneshroom prototype board (P4 + C6, LIS3DH, touch pad, 12 RGBW mini pixels, MAX98357A + 40 mm speaker, 18650 + MT3608 + TP4056) | 1 through Oct 30, 2 to 3 by Nov 13 | Sophia |
| Tuneshroom enclosure (ETC cast silicone or print; diffuser and resonance chamber) | 1, then copies | Sophia, ETC maker lab |
| Tower (2100 mm, 8 LED segments, diffusion, fiber, props, ESP32 in the base, supply, mounting) | 1 | Sophia |
| Tower firmware (same image, tower build flags: segment count, no sensors) | 1 | Victor |
| Mushica Bit (`bits/mushica/`) and a Tower Room profile | 1 | Chris (Bit), Victor (Room profile) |
| Runbooks: Tuneshroom build, Tower build, firmware flash and bench bring-up | 3 docs | authors of each |
| Bench: dedicated AP and flat subnet, Terrarium laptop on it, mDNS verified | 1 | Sophia |

### 3.1 Definition of done (Oct 30)

Demonstrated in one sitting, video-recorded:

1. The Terrarium laptop starts with `./terrarium.sh`; the Tuneshroom and the
   Tower appear in the Console device list within 15 s of power-on, over
   o2lite, with no cable to the laptop.
2. The Tuneshroom joins Mushica's player node (QR or a hard-coded node in the
   firmware) and receives its role. The Tower is bound as the Room's fixture.
3. A **tap**, a **long tap** and a **swing** on the Tuneshroom each reach the
   Bit as the right verb. Taps carry a device-side timestamp in O2 time.
4. Mushica runs a full level: metronomic grounding, call-and-response, free
   performance. The Tower shows the beat and the progress bar; the Tuneshroom
   shows the cue lights and the hit/miss feedback; the Terrarium plays the
   track and the tones.
5. **Tap to local sound under 20 ms, measured acoustically** on the Tuneshroom
   (one recording with both the tap transient and the speaker onset).
6. **Tap timestamp error under 10 ms**, measured: a known mechanical tap
   period against the timestamps Control logs. This is the budget Mushica's
   30 ms window can afford after clock sync and horizon.
7. The phone browser Tuneshroom (o2ws) substitutes for the hardware
   Tuneshroom with no Terrarium change.

### 3.2 Out of scope for Dec 4

Pi 5 venue box and its DAC; the 864 px array and its 7 universes; the Radxa
(both boards are bench spares); a microphone on the Tuneshroom (Mushica has
no mic mechanic); DMX control of the PAR lights (they are plugged in and set
by hand); porting the render engine to firmware; Booster hardware; more than
one Bit.

---

## 4. Design decisions

### 4.1 Firmware framework: Arduino-ESP32 core, in PlatformIO

**(recommended)** Build the firmware on the Arduino-ESP32 core (3.2 or later,
which carries ESP32-P4 board support and ESP-Hosted for the C6 radio), in a
PlatformIO project committed to a new `firmware/` directory in mm-terrarium.

- o2lite's ESP32 port is written for the Arduino environment
  (`o2/src/o2liteesp32.cpp` uses `WiFi.h`; `o2/arduino/README.md` is the
  install note). Using it as-is is the difference between a hello in Week 4
  and a port in Week 6.
- The Arduino core runs as an ESP-IDF component, so the IDF drivers the
  instrument-host port map names (RMT for the pixels, I2S for the amplifier,
  the touch sensor peripheral) are all reachable when needed.
- PlatformIO over the Arduino IDE because the project, its pinned core
  version and its library versions live in one `platformio.ini` in git, and
  the second student can build the same image on day one.

**What would reverse this:** if the P4's WiFi through the C6 (ESP-Hosted)
does not work under the Arduino core on the bench by Fri Sep 18, switch the
Dec 4 Tuneshroom to a classic ESP32 or ESP32-S3 dev board, where o2lite has
already been run, and keep the P4 as the post-show target. The firmware is
written so this is a board-target change, not a rewrite: nothing outside
`link/` may touch the radio.

### 4.2 Thin device, timed frames

Rev 1 firmware does exactly three things:

1. **Link.** Join the ensemble, offer service `ie<N>`, send `/game/hello`
   every 5 s (the heartbeat `GameServer.reap_stale` expects), send
   `/game/join` once, receive `/ie<N>/role`, `/ie<N>/room`, `/ie<N>/leds`,
   `/ie<N>/play`, `/ie<N>/release`. Argument shapes are the ones
   `harness/o2_shroom.py` already sends and handles; the firmware is a second
   client of the same wire, not a new protocol.
2. **Sense.** Report gestures as `/game/<verb>` with the device's O2-time
   stamp: `tap`, `hold` (long tap, with its duration), `swing` (with signed
   magnitude). Detection thresholds are constants in Rev 1 and move to the
   role blob's `triggers` later, per the carried-instrument schema.
3. **Render.** Hold each `/ie<N>/leds` frame in a small timed queue and show
   it when `o2l_time_get()` reaches its `when`, exactly as `TimedQueue` does
   in Python. Play a preloaded PCM sample on a local tap immediately, before
   the message leaves, for the sub-20 ms path.

Nothing renders ambient animation on the device. Between frames the last
frame holds. That is what the Python Testshroom does today and it is what
the Bit's lights were designed against.

### 4.3 Gesture sensing: touch pad for tap and hold, accelerometer for swing

**(recommended)** A copper touch pad on the ESP32's touch-sensor peripheral
distinguishes **tap** (release under 250 ms) from **hold** (held 400 ms or
longer) on one surface. The LIS3DH gives **swing** from a sustained lateral
acceleration with a sign. The LIS3DH's hardware click interrupt is kept
wired and reported as a second tap source, so the two can be compared on
the bench and the one that survives the silicone body wins.

- A long tap is a *duration*; an accelerometer sees only the onset. Touch
  gives the duration for free.
- The mm-tuneshroom sim's detectors (`peak_g`, `window_ms`, `double_ms`) stay
  the contract for the accelerometer path, so a firmware tap from the LIS3DH
  reads the same as a phone's.

**What would reverse this:** touch sensing through the cast silicone proving
unreliable. Then tap stays on the LIS3DH click and hold becomes a
double-tap, and the Mushica cue table is updated to say so.

### 4.4 Tower: eight segments on one controller, bound as a Room fixture

**(recommended)** Eight 250 mm segments of 12 V SK6812 RGBW strip at 60 px/m
(15 px each, 120 px total, one data line chained top to bottom), diffusion
film over each, end-glow fiber bundles fed from the segments, PAR lights
plugged in and set by hand. An ESP32 in the base, same firmware image with
`TOWER` build flags: service `ie<N>`, 120-pixel renderer, no sensors, no
speaker, mains supply.

- 120 px × 0.025 A full white = **3.0 A at 12 V**. A 12 V 5 A supply with a
  fuse and injection at both ends of the chain covers it with margin. The
  power-limiter rule still applies: the frame renderer clamps total current
  to 4.0 A before the pixels are ever driven white.
- Binding it as a device (not an Art-Net fixture) means the Room concept in
  Control already knows how to give it a role, a light manifest and cues,
  and the Bit addresses it like any other device. No luxaeterna Art-Net
  path, no second wire.
- Same firmware, same wire, same timed-frame queue: the beat on the Tower
  and the cue on the Tuneshroom are presented at the same O2 time.

**What would reverse this:** the Tower definition changing to a part that is
not an addressable strip, or to more than ~1,300 px (the o2lite message cap
of 4096 bytes divides by 3 channels). Then the Tower gets a second data
line or a second controller, and the Room profile gets two fixtures.

### 4.5 The Terrarium is a Mac on the bench AP

Arco, Control and luxaeterna run on a Mac. Bonjour advertises the O2 host;
the Mac's line-out feeds powered monitors on the bench and the venue PA on
Dec 4. `./terrarium.sh` is the standup. Which Mac is a project-plan open
item; the plan below assumes it is on the bench from Week 4.

### 4.6 Mushica builds on MetronomeBit

`bits/mushica/` starts as a copy of `bits/metronome/`: the same beat grid in
presentation time, the same `at - cue_horizon` correction on input, the same
`function_table` cue pattern. It adds: three cue types (tap, long tap,
swing), the 30 ms / 100 ms scoring windows and a progress score in
`result()`, the phase sequence from the design document, and a Room-targeted
cue stream for the Tower (beat pulse, progress bar fill). Free-performance
phases route gestures to lights only, unscored, as the document requires.

---

## 5. Ownership

**Victor, firmware and software.** The firmware image, its PlatformIO
project, the flash-and-bench runbook, the Tower Room profile, the timestamp
measurement, and the firmware half of every integration task. Victor keeps
the migration spec's Phase 2 (o2ws browser) as the fallback device and does
not start Phase 3 (Radxa FFI link).

**Sophia, hardware.** Bench network, the Tuneshroom prototype board and its
enclosure, the Tower build and its power, the Tuneshroom and Tower runbooks,
the acoustic latency measurement, the November unit copies.

**Chris.** Procurement for the Tower and any firmware-track parts, the
Mushica Bit, the Terrarium Mac, Alt Ctrl submission, unblocking, and the
weekly gate reviews. Anything in Arco, pyarco or o2 is Roger Dannenberg's
and is reported upstream, never patched locally.

---

## 6. Schedule

Eleven working weeks from Mon Sep 14. Fall Break Oct 12 to 16 and
Thanksgiving Nov 23 to 27 are dark. Weeks are numbered as in the project
plan (W4 = Sep 14).

| Week | Victor (firmware, software) | Sophia (hardware) | Gate |
|---|---|---|---|
| **W4** Sep 14 | PlatformIO project; P4 boots, joins the bench WiFi through the C6; **o2lite hello reaches Control** | Bench AP and flat subnet; Terrarium Mac on it; mDNS verified. Tower parts list and order out. Prototype board wired: touch pad, LIS3DH, pixels, amp | **Fri Sep 18: hello or board-target fallback (4.1)** |
| **W5** Sep 21 | `/game/join` and the role blob; `/ie<N>/leds` timed renderer on the 12 pixels; local sample playback | Board in a mock body; MT3608 trimmed; battery path; touch pad under silicone sample | |
| **W6** Sep 28 | Gestures: tap, hold, swing reach a running Bit; timestamp measurement; Tower build flags | Tower parts arrive; mast, segments, injection, fuse; controller in base | Mushica Bit playable in the sim (Chris) |
| **W7** Oct 5 | Firmware hardening: reconnect, heartbeat, brownout; Tower first light from Control | Tuneshroom first article into the enclosure; Tower standing at full height | **GATE 1, Fri Oct 9** |
| **W8** Oct 12 | dark | dark | Fall Break |
| **W9** Oct 19 | Tower Room profile; Mushica cues on the Tower; acoustic latency measured with Sophia | Tower diffusion, fiber, props; Tuneshroom finish | |
| **W10** Oct 26 | Integration, reliability, submission video | same | **Alt Ctrl, Fri Oct 30** |
| **W11** Nov 2 | Freeze firmware; Dry Run 1 | Tuneshroom #2 from the runbook | **Lock, Fri Nov 6** |
| **W12** Nov 9 | Punch list | Tuneshroom #3; spares kit | **Dry Run 2, Fri Nov 13** |
| **W13** Nov 16 | Hardening; runbooks final | same | |
| **W14** Nov 23 | dark | dark | Thanksgiving |
| **W15** Nov 30 | Cold-start recovery | same | **Dry Run 3 Mon Nov 30; Show Fri Dec 4** |

---

## 7. Gates

**Fri Sep 18, board target.** An ESP32-P4 on the bench WiFi sends
`/game/hello` and Control logs it. *Fail:* the Dec 4 Tuneshroom moves to a
classic ESP32 or S3 board (4.1) the same day; the P4 stays the post-show
target. This is a one-week checkpoint, not a project gate, but it is the
only step on the critical path with zero slack.

**GATE 1, Fri Oct 9, hardware viability.** The Tuneshroom first article, in
its enclosure, joins Mushica and plays a call-and-response phase with all
three gestures; tap-to-sound is under 20 ms; the Tower shows the beat.
*Pass:* the polish window (W9, W10) runs on hardware. *Fail:* the o2ws
phone browser becomes the **committed** Tuneshroom for Alt Ctrl and the
show; firmware work continues on the Tower only, which has no fallback.

**Fri Oct 30, Alt Ctrl submission.** Definition of done (3.1) demonstrated
and recorded. This is also where the old plan's Hardware Complete gate
lives now.

**GATE, Fri Nov 6, Deliverables Lock.** Firmware image frozen; only the
runbook copies and fixes are made after this date.

---

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **P4 + C6 WiFi under the Arduino core does not work** (ESP-Hosted is new in the core) | No link, nothing else can start | One-week checkpoint Sep 18 with a named fallback board. Nothing outside `link/` touches the radio. |
| **mDNS does not cross the bench AP** | Discovery never completes; reads as a firmware bug | Flat subnet, client isolation off, verified with `dns-sd` from the Mac on W4 day 1, before any firmware runs. |
| **Tap timestamp too loose for a 30 ms window** | Perfect taps graded Good; the game feels wrong | Measured in W6 (3.1 item 6) with a mechanical tapper. The firmware stamps at the interrupt, not at send. |
| **Touch pad unreliable through silicone** | No hold gesture | LIS3DH click kept wired as the tap fallback; hold becomes double-tap (4.3). Tested on a silicone sample in W5, before the body is cast. |
| **Tower LED type or count changes after the order** | Power budget and blob size wrong | Renderer takes pixel count and current limit as build flags; the limiter is unconditional. |
| **MT3608 untrimmed** | Destroys the board | Trim to 5.0 V on a bench supply, verified with a meter, before connecting. Stop-point in the build checklist. |
| **Students outrun Chris's unblock bandwidth** | Idle time | Each lane has independent work every week; weekly gate review scheduled, not ad hoc. |
| **Arco or o2 defect on the ESP32 path** | Blocked on upstream | Reported to Roger the day it is found, with a minimal reproduction; local workaround only in `link/`, never in vendored o2lite. |

---

## 9. Procurement

Already in hand at ETC (ordered Aug 28): ESP32-P4 boards, LIS3DH ×4,
MAX98357A ×3, 40 mm speakers ×3, RGBW mini pixels ×20, jumper wire, USB-C
cables, heat shrink, folding bench table. Two Radxa Zero 3W (spares).

**Tower order, Week 4 (Sophia lists, Chris orders):** 2.5 m of 12 V SK6812
RGBW 60 px/m strip, per-LED addressable, confirmed in the listing body ·
12 V 5 A supply with barrel and fuse holder · 3-pin JST and 18 AWG for
injection · diffusion film sheets · three end-glow fiber bundles · 2100 mm
mast (aluminum channel or PVC) and a weighted base · PAR lights ×2 with
stands · a classic ESP32 or S3 dev board ×2 (the 4.1 fallback and the Tower
controller) · 3D-print filament or ETC print time for connectors and
responders · props per the Tower definition.

**Tuneshroom consumables:** 18650 cells ×4 with holders, MT3608 ×4, TP4056
×4, copper tape for the touch pad, silicone and pigment for the body. Confirm
against the Amazon receipt before ordering duplicates.

No budget figure is set; the project plan carries the Tower as unbudgeted
until the list is priced.

---

## 10. Acceptance

The track is complete when, on Dry Run 2 hardware:

- [ ] All seven 3.1 criteria pass in one recorded sitting.
- [ ] Tap latency (acoustic) and tap timestamp error are measured on the
      Tuneshroom, method recorded in the runbook.
- [ ] The Tower current limiter is enforced in firmware with a bench test,
      not by discipline.
- [ ] A second Tuneshroom built from the runbook alone, by the person who
      did not build the first, joins and plays.
- [ ] Firmware, runbooks and the Room profile are on `main` in mm-terrarium.

---

## 11. Open items

1. Which Mac is the Dec 4 Terrarium (Chris, before W4 day 1).
2. Tower LED part confirmed and ordered (Sophia and Chris, by Fri Sep 18).
3. Whether the Tuneshroom's hard-coded join node is acceptable for Alt Ctrl,
   or the QR join is required (Chris; affects nothing in firmware until W9).
4. pyarco source-of-truth (Roger Dannenberg); unchanged, no longer on the
   Dec 4 critical path.
5. `MM_HARDWARE_DESIGN.md` §11 update for the ESP32-P4 and the Tower (Chris).
