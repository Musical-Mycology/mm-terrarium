# Student Hardware Track — design

**2026-08-06 · Chris Oltyan — chris@musicalmycology.org**
**Status: APPROVED DESIGN.** Feeds an implementation plan; supersedes nothing.

Two students join full-time on **2026-08-24** and run a dedicated hardware track
through the **2026-12-04** Musical Mycology show. This document defines what they
build, what they do not, how the work is sequenced, and what has to be bought
before they arrive.

**Source of truth for dates and scope this plan reconciles against:** the Dec 4
show Gantt
([Google Sheet](https://docs.google.com/spreadsheets/d/1rLvcHqZI4DffOPvD9veYub6qxjYvw6Ev3NimXPjZE4w/edit?gid=2042942493#gid=2042942493)).
**Source of truth for parts and prices:** `mm-documents/MM_HARDWARE_DESIGN.md`
v0.6 (§4 Tuneshroom, §7.1 Terrarium, §11 Component Register).
**Source of truth for the software seams:** `docs/MM_TERRARIUM.md` and
`docs/control-gameserver-design.md`.

---

## 1. Goal

Produce **two complete, interchangeable hardware sets** capable of running
Terrarium + Arco + Lux Aeterna plus two Tuneshrooms, with a phone browser
simulator able to stand in for a Tuneshroom at any point.

One set is the **show set**, sealed after the Oct 16 gate. One is the **bench
twin**, which stays open for destructive learning and becomes the swap stock at
Dry Run 2. At full-time student capacity the marginal cost of the second set is
roughly $700, and it converts a Nov 13 hardware failure from a repair under
deadline into a swap.

---

## 2. Context that shapes every decision below

Four facts from the existing docs drive this plan. They are restated here
because each one silently invalidates an otherwise reasonable schedule.

1. **There is no Arco server and no o2lite.** `arcoserver/` does not exist and
   the repo has zero o2lite imports. The only working device wire is
   `devicelink/`, plain JSON over a websocket. The Gantt's "Arco server + o2lite"
   line is Chris-owned and is explicitly flagged *"CUT THIS FIRST if slipping."*
   Two full-time students building hardware will reach integration well before
   that line lands.
2. **The JSON envelopes mirror o2ws field-for-field** (`timestamp` / `address` /
   `typespec` / `args`). The vocabulary is real; only the framing is not. The
   later swap to o2ws is therefore mechanical, which is what makes it safe to
   build hardware against `devicelink` today.
3. **Student capacity is not the constraint.** The Gantt's hardware lines total
   roughly 210 to 375 hours. Two full-time students from Aug 24 to Dec 4 is
   roughly 900. The constraints are procurement lead time, Chris's unblock
   bandwidth, and the software the hardware talks to.
4. **The real hardware deadline is Dry Run 2, Fri Nov 13**, not the Dec 4 show.

### 2.1 Approach chosen

**Hardware track on `devicelink`, with a scheduled o2lite integration window and
a standing fallback.** Students drive the full hardware build to a demonstrable
end state on the wire that exists, finishing Oct 16. A named window (Oct 19 to
Nov 6) then attempts the o2lite/Arco swap, with `devicelink` as the committed
fallback for Dry Run 2 and the show.

Rejected alternatives:

- **Build only against `devicelink`, no o2lite attempt at all.** Zero coupling to
  the riskiest line, but the real transport is never exercised on real hardware,
  so o2lite surprises (discovery, clock sync, campus UDP behavior) all land at
  once and late.
- **Hand the students the transport.** Full-time attention on the item most
  likely to slip is genuinely attractive, but Arco is C++ in Roger Dannenberg's
  domain and the pyarco source-of-truth question (bootstrap open question #1) is
  an open decision that is not Chris's to make. Handing students an unsettled
  dependency is how six weeks disappear.

**What would reverse this call:** if Roger settles pyarco source-of-truth and
commits to Arco server support during September, staffing the transport
full-time becomes worth more than a second attempt window later.

---

## 3. Deliverables

| Item | Show set | Bench twin |
|---|---|---|
| Terrarium venue box (Pi 5 8 GB + PCM5122 DAC HAT) | 1 | 1 |
| Terrarium LED array | 6 m Muzata, 864 px, 3 fiber bundles | 2 m short array, ~288 px |
| Tuneshroom (Radxa Zero 3W, full §4.1 spec) | 2 | 2 |
| Bench first-light rig (WLED ESP32 + short strip) | shared | 1 |
| Bench network (dedicated AP, flat subnet) | shared | 1 |

### 3.1 Definition of done

All five demonstrated together, in one sitting, on the show set:

1. A venue box boots, runs Control plus Lux Aeterna, and drives the 864 px array
   across all 7 Art-Net universes at a **sustained 44 Hz measured on the Pi 5
   itself**. Per `MM_TERRARIUM.md` § *Host platform*, any timing figure must be
   measured on the venue box; figures from other hosts do not carry over.
2. Audio comes out the **PCM5122 line-out**. The Pi 5 has no analog jack
   (`MM_HARDWARE_DESIGN.md` §9.1), so this is the only analog path and it is
   unproven today.
3. Two Radxa Tuneshrooms join over `devicelink`, publish LIS3DH tap and tilt
   upstream, and render pushed SK6812 frames on their 12 LED cap-and-stem arrays.
4. **Tap to local sound under 20 ms, measured.** This is the §4.4 conformance
   figure that local sample playback exists to preserve now that synthesis is
   remote.
5. A phone browser Tuneshroom substitutes for either hardware unit at any point
   with no venue-box reconfiguration.

### 3.2 In scope and easy to under-count

Three student deliverables that read as physical build work but are mostly
software, and are all currently undocumented or untested:

- **`luxaeterna/artnet.py` has zero tests and has never been instantiated.**
  First light includes writing its first tests.
- **Multi-universe Art-Net does not exist.** `ArtNet.send()` is one universe per
  call. 864 px x 4 ch = 3456 ch = 6.75 universes, so 7 universes must be mapped
  and sequenced. Designed nowhere today.
- **Power injection, fusing, and wire gauge for the 6 m array are documented
  nowhere.** This phase must *produce* that documentation, not consume it.

### 3.3 Out of scope for students

Arco, o2lite, pyarco, Bit authoring, the Flutter and browser simulator,
`arcoserver/`. All Chris-owned. Booster hardware (Tier 3) is not part of this
build at all; this is the fixed-venue product line only.

---

## 4. Ownership

Two lanes, one convergence point. Students pair in W1 and from mid-October, and
run independently between.

**Lane A, Venue.** First light, Art-Net universe mapping, both Pi 5 venue boxes,
both LED arrays, power distribution and its documentation.

**Lane B, Instrument.** Radxa bring-up, the I2S full-duplex device-tree overlay,
all four Tuneshroom builds, local sample playback.

**Both, in parallel at ETC from W3.** Silicone enclosure casting. The body is
simultaneously light diffuser and acoustic resonance chamber
(`MM_HARDWARE_DESIGN.md` §4.1), so it needs optical and acoustic fitting
iterations, not one shell at the end. The Gantt has enclosures starting Sep 28;
this plan starts them Sep 7.

**Chris.** Procurement start to finish. Arco server and o2lite. Bit format and
loader. Bits A and B. The simulator. Unblocking, which at full-time capacity is
a real recurring cost and should be budgeted as such.

---

## 5. Schedule

Thirteen working weeks. Labor Day Mon Sep 7. Veterans Day Wed Nov 11.
Thanksgiving Nov 23 to 27 dark.

| Week | Lane A, Venue | Lane B, Instrument | Shared |
|---|---|---|---|
| **Aug 6 to 21** *(Chris, pre-arrival)* | Procurement, both waves. Confirm ETC bench and tooling. | | |
| **W1** Aug 24 | Bench setup, inventory, dedicated AP and flat subnet. **First light:** Art-Net to WLED to real LEDs. First tests for `artnet.py`. | Radxa Zero 3W OS image, boot, network, `devicelink` reachable. | Onboarding, ESD, repo access |
| **W2** Aug 31 | Venue box #1: Pi 5 build, PCM5122 verify, ALSA, audio out the line-out. | I2S full-duplex device-tree overlay. | |
| **W3** Sep 7 | Art-Net universe mapping: 7 universes, multi-universe send. | INMP441 mic in, MAX98357A out, both live. | **Enclosure casting begins** |
| **W4** Sep 14 | Universe mapping lands. Venue box #2. | LIS3DH I2C, hardware tap interrupt. SK6812 chain on GPIO2_C7. | |
| **W5** Sep 21 | Bench array (2 m) driven end to end from a venue box. | `devicelink` client on the Radxa: sensors up, LED frames down. | **GATE Fri Sep 25** |
| **W6** Sep 28 | Full 6 m array: build, power injection, fusing, AWG, all documented. | Tuneshroom first article, ~30 terminations. | |
| **W7** Oct 5 | Brightness cap enforced. Fiber bundles. | First article complete, in enclosure. | |
| **W8** Oct 12 | Array plus venue box integrated. 44 Hz measured on the Pi 5. | Tuneshrooms #2 and #3. | **GATE Fri Oct 16: hardware complete** |
| **W9** Oct 19 | Show set sealed per Gate 2. Bench twin stays open. | Tuneshroom #4. Local sample playback. | **o2lite window opens** |
| **W10** Oct 26 | Support Dry Run 1 | Tap-to-sound latency measured | **Dry Run 1, Fri Oct 30** |
| **W11** Nov 2 | Punch list | Punch list | **Lock, Fri Nov 6. o2lite window closes.** |
| **W12** Nov 9 | Full hardware path. Bench twin on standby as swap. | same | **Dry Run 2, Fri Nov 13** |
| **W13** Nov 16 | Hardening, spares, runbook | same | |
| **W14** Nov 23 | dark | dark | Thanksgiving |
| **W15** Nov 30 | Cold-start recovery | same | **Dry Run 3 Mon Nov 30. Show Fri Dec 4.** |

### 5.1 Deltas from the Dec 4 Gantt

| Line | Gantt | This plan | Why |
|---|---|---|---|
| Long-lead procurement | Aug 5 to Sep 4 | **Complete by Aug 21** | Two full-time students with an empty bench on Aug 24 is the most expensive failure mode available. |
| Lux Aeterna first light | Aug 5 to Aug 21, Chris | **W1, Lane A** | It is a hardware interface. It also seeds `artnet.py`'s first tests. |
| Art-Net universe mapping | Aug 24 to Sep 18, Chris | **W3 to W4, Lane A** | Same reasoning. |
| Radxa bring-up gate | **Oct 9** | **Sep 25** | Full-time capacity answers it two weeks earlier. This is the difference between having time to react to a failure and having none. |
| Pi 5 venue box | Sep 21 to Oct 9 | **W2 and W4** | Unblocks the array work and the audio path. |
| Tuneshroom builds | Oct 19 to Nov 6 | **W6 to W9** | Four units, not two. |
| Terrarium LED array | Oct 19 to Nov 6 | **W5 to W8** | |
| Enclosures | Sep 28 to Nov 6 | **W3 onward** | Casting iterates; it cannot be a single pass at the end. |
| **Hardware complete** | **Nov 6**, one week before the deadline and the same day as Lock | **Oct 16** | Four weeks of slack ahead of Dry Run 2. The slack is the deliverable. |

---

## 6. Gates

**GATE 1 — Fri Sep 25, Radxa bring-up.** Does a Radxa Tuneshroom work end to
end: I2S full duplex, sensors, LEDs, `devicelink` client?

- *Pass:* Lane B proceeds to four builds.
- *Fail:* the phone browser simulator becomes the **committed** Tuneshroom
  stand-in for Dry Run 2 and the show, not a contingency. Lane B folds into Lane
  A for the array and venue-box work. Seven weeks remain, which is why the gate
  is worth pulling forward.

**GATE 2 — Fri Oct 16, hardware complete.** All five §3.1 criteria demonstrated
on the show set.

- *Pass:* show set is sealed. Only the bench twin is touched after this date.
- *Fail:* the o2lite window (W9 to W11) is cancelled outright and reallocated to
  finishing hardware. Non-negotiable: hardware completeness outranks transport
  modernity.

**GATE 3 — Fri Nov 6, Lock and o2lite abort.** Whatever state the o2lite swap is
in, it stops. If it is not demonstrably better than `devicelink` on the show set
by this date, the show runs on `devicelink`.

---

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Campus network blocks UDP multicast/broadcast.** O2 discovery is multicast, Art-Net is broadcast. Client isolation is common on campus wireless. | Presents as a hardware bug for a week before anyone suspects the network. | **Dedicated AP and flat subnet on the bench from day one, in Wave 1 of the BOM.** Same reasoning that rules out WSL2 and NAT'd VMs in `MM_TERRARIUM.md` § *Host platform*. |
| **PCM5122 Pi 5 compatibility is unverified.** Flagged in the Gantt. | The Pi 5 has no analog out at all. No DAC means no audio path, period. | Order a second DAC candidate **in the same Wave 1 order**, not as a September follow-up. |
| **Wrong 12 V SK6812 variant.** The grouped variant is 1 IC per 3 LEDs. | 6 m becomes 288 controllable pixels instead of 864. Silently halves the canvas. | Confirm "addressable per LED" in the **listing body**, not the title. `MM_HARDWARE_DESIGN.md` §9.2 has the exact distinction. |
| **Array overcurrent.** 864 RGBW px at full white draws roughly 21.5 A against a 12.5 A LRS-150-12. | Supply damage or fire. | **Brightness cap enforced in software before the array is ever driven white.** A Lane A acceptance criterion, not a preference. Plus fusing and documented injection points. |
| **MT3608 not trimmed before connection.** | Kills the Radxa. | Trim to 5.0 V on the bench supply, verified with a meter, **before** the boost converter is ever connected to a board. Written into the Tuneshroom build checklist as a stop-point. |
| **Radxa Zero 3W stockout or bad batch.** Most volatile availability line. | Stalls Lane B entirely. | Five units in Wave 1. |
| **Students outrun Chris's unblock bandwidth.** Full-time pair generates questions fast. | Idle students, which at full-time is expensive. | Both lanes are designed to have independent work available. Standing weekly sync plus an async channel. Gate reviews are scheduled, not ad hoc. |
| **Arco/o2lite never lands.** Already flagged as the first cut. | o2lite window produces nothing. | Accepted by design. `devicelink` is the committed fallback; §2 note 2 is why the eventual swap stays cheap. |

---

## 8. Procurement

Both waves ordered the week of **Aug 6**. Nothing waits for the students.
Prices from `MM_HARDWARE_DESIGN.md` §11.

### Wave 1 — bench-critical, in hand by Aug 24 (~$1,000)

Radxa Zero 3W x5 · Pi 5 8 GB x2 with official 27 W USB-C PSUs and storage ·
PCM5122 DAC HAT x3 **plus one alternative DAC candidate** · WLED ESP32 x3 ·
short 5 V SK6812 strip · dedicated AP and small switch · LIS3DH, INMP441,
MAX98357A, 50 mm 4Ω drivers, x6 each · 18650 3500 mAh x8 with holders, MT3608
x8, TP4056 x8 · 5 V SK6812 Mini RGBW cap sets x6 · consumables (silicone wire,
JST, heatshrink, ferrules, solder, flux).

### Wave 2 — bulky and long-lead (~$800 to $1,300)

8 m of **12 V SK6812 RGBW 144/m, per-LED variant** (6 m plus spare) · 6 m Muzata
Spotless channel and diffusers · Mean Well LRS-150-12 · 12 V 5 A for the bench
array · 2 m bench strip · 3 fiber end-glow bundles with RGBW engines · powered
monitors for the line-out · fusing, distribution blocks, 14 to 16 AWG for
injection.

**Total roughly $1,800 to $2,300** for both sets. No cap set; this is the
estimate, not a budget.

### Pre-arrival action for Chris

Confirm with ETC what bench tooling exists (soldering station, hot air, bench
supply, multimeter, scope). Anything missing joins Wave 1.

---

## 9. Acceptance

The track is complete when, on the **show set**:

- [ ] All five §3.1 criteria pass in a single demonstrated sitting.
- [ ] The 44 Hz figure and the sub-20 ms tap latency are **measured on the venue
      box and the Radxa respectively**, with the measurement method recorded.
- [ ] Power injection, fusing, and AWG for the 6 m array are documented in-repo.
- [ ] The brightness cap is enforced in code with a test, not by operator
      discipline.
- [ ] A build runbook exists for both the venue box and the Tuneshroom,
      sufficient for someone who did not build them to rebuild one.
- [ ] The bench twin can substitute for the show set with no reconfiguration.

---

## 10. Open items

1. **ETC bench tooling inventory.** Blocks finalizing Wave 1. Chris, before
   Aug 21.
2. **pyarco source-of-truth** (submodule vs pinned sibling) remains Roger
   Dannenberg's decision. Out of student scope, but it gates whether the o2lite
   window can produce anything. Bootstrap open question #1.
3. **Terrarium external speaker for the show.** The Terrarium provides line-out
   only; the speaker is explicitly out of BOM (`MM_HARDWARE_DESIGN.md` §7.1).
   Powered monitors in Wave 2 cover bench and dry runs. The show venue's PA is a
   separate decision.
4. **Whether the Gantt gets updated to match this plan** or the two are
   maintained separately. Recommend updating the Gantt so there is one schedule.
