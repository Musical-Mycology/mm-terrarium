# Control+GameServer Design (Official Architecture Path)

**Terrarium / Tuneshroom / Bit architecture** · v2 · 2026-07-18 · Chris Oltyan — chris@musicalmycology.org, with Roger Dannenberg

**Status: OFFICIAL PATH FORWARD as of 2026-07-18. This file is the canonical
copy.** It supersedes the earlier Musical Mycology direction of an embedded
Arco engine on every device (mm-documents design, §4.5) and the M1a-era O2
service conventions (`o2host` hub, `te`/`sh<pid>` services) used by the first
test slices in mm-tuneshroom. Those slices remain valid test beds pending
migration. Broader game-design background (RenQuest integration, join tokens,
Bit scoring and loop rules, hardware) lives in MM-internal docs and is not
required to work on this architecture.

---

## Vocabulary

- **Terrarium**: the central unit in a room. One capable computer plus LED display
  and speakers. It hosts two processes: the Arco server (the O2 hub serving HTTP,
  websockets, and o2lite) and the Control+GameServer (**an o2lite client of that
  hub**, via pyarco's `o2litepy` — see *Message Routing* below).
- **Tuneshroom**: the physical Interactive Element. Processor, mic, speaker,
  sensors, LEDs. Joins as an o2lite client. A phone can simulate one.
- **Bit**: a loadable game/experience module inside the Control+GameServer. A
  Terrarium can be configured with any number of Bits. A Bit defines the roles
  players can adopt, which Registration Nodes grant which roles, the gameplay
  message vocabulary, the ugen graph it builds on the Arco server, the device
  light/sound behavior, and the scoring logic.
- **Registration Node**: a physical tap point that grants roles. A Bit can have
  several nodes active at once, and different nodes can grant different roles. A
  node is an identity in the protocol, not necessarily its own computer: an NFC
  tag or QR code encoding a node id is enough.

## Topology

```
Phone browser --ws--+
                    v
Shroom (o2lite) --> +--------------+     o2lite, same box
Shroom (o2lite) --> | Arco server  | <--------------------> Control+GameServer
Shroom (o2lite) --> | "arco"       |                        "game", "actl"
                    +--------------+
       each Tuneshroom offers "ie<N>", each browser offers "ui<X>"
             (both are Interactive Elements to the game layer)

  Every arrow is an o2lite link to the hub. The Arco server relays all of it.
```

## Services

- `arco`: the engine, as today.
- `actl`: Control's engine-facing service, per the existing reset/open convention.
- `game`: Control's domain service. All gameplay traffic from every Interactive
  Element addresses `/game/...`. This interface is what a Bit defines.
- `ie<N>`: offered by each Tuneshroom over o2lite. Commands to the device (LED
  patterns, sound cues, MIDI, config) arrive here, named from the receiver's
  perspective.
- `ui<X>`: offered by each browser client over its websocket, for state pushed to
  that UI. A phone simulating a Tuneshroom offers `ie<N>` semantics through the
  same websocket instead.

## Message Routing

Control is an **o2lite client**, not a full O2 peer. pyarco reaches Arco through
`o2litepy`, a pure-Python o2lite implementation; an o2lite client can *offer*
services (`set_services`) and receive on them (`method_new`), but every message it
sends leaves over its single link to the host, with no local short-circuit. So
(per Roger Dannenberg, 2026-07-24):

| Path | Hops | Note |
|---|---|---|
| Control → `/arco` | **1** | Control's host *is* Arco. The audio-critical writer path is the cheap one. |
| Arco → `/actl` | **1** | Same link, other direction. |
| Shroom → `/game/*` | **2** | device → Arco → Control. |
| Control → `/ie<N>/*` | **2** | Control → Arco → device. |

Two consequences the rest of this design has to live with:

1. **The Arco server relays 100% of gameplay traffic.** Every `/game/data`
   message from every device, at whatever rate the Bit requested, is forwarded by
   the same process doing all room synthesis — and on the fixed Terrarium that is
   a Raspberry Pi 5 also feeding Lux Aeterna's 44 Hz render loop
   (`MM_HARDWARE_DESIGN.md` §7.1). Message rate is a **capacity** question for the
   hub, not just a latency question; see *Open Questions*.
2. **Promoting Control to a full O2 peer would buy almost nothing.** The devices
   are o2lite clients whose host is Arco, so anything addressed to `ie<N>`
   transits Arco regardless of what Control is. Full O2 would only shorten
   Control↔Arco, which is already one hop. Staying on o2lite/pyarco is therefore
   the right call, not a compromise.

The implementation was never wrong here — only this document was. The first-slice
spec (2026-07-20, §7) already recorded "Control connects via o2lite, not a
full-O2 peer binding, despite the design doc's 'full O2 peer' framing," and
deferred revisiting it until a Python full-O2 binding exists. Point 2 above
supersedes that deferral: even given such a binding, the device paths would not
get shorter, so there is nothing to revisit.

**Inside the Control process, call Python directly.** `game` and `actl` are both
inbound-only today (devices → `game`, Arco → `actl`), so Control never messages
itself — but an o2lite service addressed from its own process would round-trip
through Arco and back. O2 addressing is for the process boundary; intra-process
collaboration is a method call. (This is why `console/` and `uplink/` ride
websockets rather than O2 — they were split that way for other reasons, and this
is the second justification.)

## Roles and Registration Nodes

A Bit's role table declares each role with:

- a **class**:
  - `unique`: exclusive to one player (or capacity K), specific to this Bit,
  - `shared`: the X+1 case, unbounded, every player who registers gets the same
    effect,
  - `jam`: the Y+1 case, unbounded, full light-and-sound interaction but excluded
    from scoring,
- a **capacity** (1..K for unique, unlimited for shared and jam),
- the **nodes** that grant it, as an ordered fallback list per node (a node grants
  the first role on its list with capacity remaining; deny only if every role on
  the list is full),
- a per-player **graph-builder** (each player gets their own channel strip patched
  onto that role's bus in the Bit's mix; shared roles share the bus effect chain
  while keeping per-player strips, which is what keeps X+1 players individually
  scorable),
- a **scored** flag (false for jam).

## Player Flow, Mapped to Messages

1. **Enter the room.** A Tuneshroom powers up, joins WiFi, discovers the ensemble,
   and connects to the Arco server via o2lite. A phone taps NFC (or scans a QR
   code) and gets a URL pointing at the Terrarium's own HTTP server, which the
   Arco server already provides through O2; the page carries o2ws.js and the
   simulator UI, so the phone joins the same ensemble over a websocket with no
   app install. Either way the device announces itself:
   `/game/hello "si" name protoversion`.
2. **Tap a Registration Node.** The tap is the join intent, and the node
   determines what is being asked for. The device reads the node's tag (or the
   node's reader reads the device; either side can originate) and sends
   `/game/join "sst" dev node time`. For phones, the node id is baked into the
   URL the tag or QR code carries, so the browser's join is byte-identical.
3. **Adopt a role.** Control looks up the node in the current Bit's role table
   and walks its fallback list. On success, Control allocates the player's ugens,
   patches their channel strip onto the role's bus, and tells the device what it
   has become: `/ie3/role "sssib" bit role class channel config`. The config blob
   carries what the role needs the device to know (local sample set, sensor
   rates, scored flag, and the role's light-manifest v2 blob -- instruments
   plus the per-role welcome gesture, with bit/role provenance stamped by
   Control; see the luxaeterna session-lifecycle spec section 9 and this
   repo's 2026-07-22 light-manifest-v2-adoption spec). If every role on the
   node's list is at capacity:
   `/ie3/deny "ss" reason hint`, where hint can name another node worth trying.
   Re-tapping a different node mid-session is a role switch: Control tears down
   the old strip and answers with a fresh `/ie3/role`. The device never touches
   `/arco`; its place in the audio graph is something Control built for it.
4. **Play.** Two return channels, and the role's config says which (or both):
   - *Control-rate data* for scoring and for driving the player's ugens:
     `/game/data "stb" dev time payload` at the rate the Bit requested, plus
     discrete events like `/game/hit "sti" dev time key`. Jam-role players send
     the same stream; it drives their sound and lights but the scorer ignores it.
   - *Audio* when the Bit wants the player's actual sound in the room mix:
     Control instantiates an `o2audioio` ugen as the player's input and the
     device streams mic audio into it. This is existing Arco machinery, including
     buffering and flow control.

   Meanwhile the Bit drives local feedback with scheduled cues:
   `/ie3/led "tib" time pattern args`, and `/ie3/play "tis" time id params` for
   locally stored sounds where latency matters, while the Terrarium renders the
   shared mix and LED display.
5. **Complete the Bit.** Control scores the scored roles from the `/game/data`
   stream, pushes progress to `/ui<X>/state` and device cues, and on completion
   sends `/ie3/release`, frees the player's strip, and returns the device to the
   joinable pool.

## Design Rules

1. **Receiver-perspective addressing.** Same idiom as `/arco/...` in,
   `/actl/...` back.
2. **Identity in arguments, not addresses.** Every input carries `dev`, and joins
   carry `node`. A phone simulating shroom 3 at node A sends byte-identical
   messages with `dev="ie3"`, `node="A"`. One handler per verb in Control
   regardless of fleet size, one place to validate and log.
3. **Single writer to `/arco`.** Only Control builds graphs and owns the ugen id
   space. Interactive Elements express intent to `/game`; Control decides the
   audio consequence.
4. **Timestamps at the source, scheduling at the sink.** Devices stamp inputs
   with `o2l_get_time()` at the physical event; Control schedules audio and cues
   ahead of time. With Arco as the sample-locked reference clock, the forwarding
   hop through the Arco server costs almost nothing musically. It only eats into
   true feedback paths (gesture to sound), where WiFi jitter dominates anyway.
   The `/ie<N>/play` local-sample path exists precisely so the tightest feedback
   never crosses the network.
   **The cue lead must cover two hops, not one.** Per *Message Routing*, a
   `/ie<N>/led` or `/ie<N>/play` cue travels Control → Arco → device, so the
   schedule-ahead window has to absorb two WiFi legs plus the hub's forwarding
   latency. A lead sized for one hop will land late under load. Measure the real
   round trip on the target hardware before picking the number — this is exactly
   what the o2lite bring-up slice has to establish.
5. **MIDI over o2lite as packed int32** (status, data1, data2 in one word), since
   o2lite lacks O2's native `'m'` type; blobs for sysex or bulk.

## What a Bit Is, in Code Terms

A module loaded by Control that declares:

1. its role table with classes, capacities, and node mappings,
2. handlers for the `/game` verbs it uses,
3. per-role graph-builders for the Bit's patch and per-player channel strips
   (built on the Python patch library — see *Implementation Proposal*),
4. cue logic for device light/sound, and
5. a scoring function over the input stream.

The `/game/*` message stream is the complete input history of a session, so
logging it gives record/replay for free: deterministic regression tests, headless
Control with scripted players in CI, and post-hoc debugging of live sessions.
Registration contention is part of what replay covers, since joins and denies are
just messages.

## Implementation Proposal

Control+GameServer in Python on pyarco, as an o2lite client of the Arco server on
the same box. Bits as Python plugin modules gives us fast iteration on game
design, and at these message rates Python overhead is dominated by the hub relay
either way. Anything that ever proves hot is isolated behind O2 addresses and
portable without touching the protocol.

### Sound: the patch library is the graph-builder's substrate

**Decision (2026-07-24, from Roger Dannenberg's offer):** Bits build sound through
a **Python-side patch library** — graphs of Arco ugens presented as Python
objects — and MM's own work stays on the control side. Two things follow, and the
split between them is the point:

- **The library is the substrate for a role's graph-builder.** It is *how* Control
  constructs and drives a patch; it is not a new authority over the graph. Design
  Rule 3 is unchanged: Control still owns the ugen id space and remains the single
  writer to `/arco`. A device never gains a path to Arco because a patch object
  exists.
- **`ugen_manifest` stays Bit-declared data**, exactly as `light_manifest` did. The
  Bit *declares* what a role sounds like; Control *interprets* that declaration and
  builds the graph. This is the same shape that worked for lighting, where
  luxaeterna's light-manifest v2 became the wire form and Control stamps
  bit/role provenance onto it — see the 2026-07-22 light-manifest-v2-adoption
  spec. Keeping the declaration declarative is what preserves record/replay and
  keeps the console a monitor rather than a driver.

Two starter sounds Roger proposed land on paths this design already has: *play a
sample* is the `/ie<N>/play` local-playback path (Design Rule 4), and *play FLsyn
with MIDI* is MIDI-over-o2lite as packed int32 (Design Rule 5). Neither needs new
protocol.

This decision is directional: the library itself does not exist here yet. pyarco
has **no dependency in this repo**, `ugen_manifest` is still a placeholder, and no
graph-builder is written. It is recorded now so the seam is not designed twice —
see *Open Questions* for what must be settled with Roger before code lands.

**Sound and instrument design ownership is still open.** If a student wants to do
instrument design, they author patches and this library is their tool; otherwise
MM consumes a fixed set from Roger and worries only about control. Worth
establishing interest before the first production Bit is scoped.

## Open Questions

1. Does one `game` service with lifecycle verbs (`hello`, `join`, `role`, `deny`,
   `data`, `release`) seem right, or would you rather lifecycle and Bit-specific
   gameplay live under separate services?
2. For per-player audio into the mix, is `o2audioio` over o2lite the intended use
   case? Any concerns about several simultaneous device streams over WiFi
   (roughly 768 kbps per 16-bit mono 48k stream)?
3. Browsers as `ui<X>` services versus a reply-address argument in `/game/hello`:
   preference?
4. **Hub forwarding cost.** Since the Arco server relays every gameplay message
   (*Message Routing*), does that forwarding contend with Arco's audio thread, and
   what sustained message rate is safe with synthesis running? The fixed Terrarium
   is a Raspberry Pi 5 also driving a 44 Hz Lux Aeterna render loop, so the
   headroom question is concrete rather than theoretical.
5. **Patch-library overlap.** MM has **already written a Python instrument
   framework**: `python25/arco_instr.py` in `Musical-Mycology/pyarco` (Chris
   Oltyan, 2026-04-08, ~830 lines — `instr_begin()` / `param()` / `Param_descr` /
   `Instrument` / `Note` / `Score` / `Synth`, plus `Reverb`, `Multi_reverb`, and a
   supersaw). It closely mirrors Roger's **Serpent** framework in
   `arco/serpent/srp/instr.srp` (Oct 2024) and `arco/doc/instruments.md` — same
   `instr_stack` / `instr_begin` / `param` / `Instrument` / `Synth` shape,
   including the `smooth`→`Smoothb` parameter idea — so it reads as a Python port
   of that design. Note this is **MM-side code, not upstream**: there is no
   `rbdannenberg/pyarco`, `Musical-Mycology/pyarco` is an original repo rather
   than a fork, and the arco repo tracks no `pyarco/` files.
   So the question is not "is the offered library `arco_instr.py`" — it is whether
   the offered library covers the same ground, in which case MM should retire its
   port and converge on Roger's, or sits at a different level and the two compose.
   Settling this before the first graph-builder avoids two overlapping
   abstractions over one ugen graph.

## Host Platform

The production Terrarium is **bare-metal Linux on a Raspberry Pi 5 with a mandatory
I2S DAC HAT** (`MM_HARDWARE_DESIGN.md` §7.1) — the Pi 5 has no analog out. There is
no virtualization layer in the venue path, which answers the standing concern about
emulated audio drivers forcing large buffers and high latency.

That concern does apply to **development** hosts. A WSL2 or VM workstation is fine
for the offline suite (all of what exists today) but is not a valid stand-in for
bring-up, for two reasons, the second of which is the more disqualifying:

- **Audio.** Virtualized sound devices generally cannot deliver small-buffer,
  low-latency operation, so any latency measured there is meaningless for the
  venue box.
- **Networking.** WSL2's NAT'd virtual NIC puts the Linux side on its own subnet.
  O2's UDP discovery will not reach LAN Shrooms, and Lux Aeterna's Art-Net will not
  reach WLED controllers, without mirrored networking or manual port proxying. For
  a box whose entire job is to be the room's O2 hub, that is a hard failure rather
  than a degradation.

**Rule:** latency, message-rate, and discovery numbers are only meaningful when
measured on target hardware. Budget a round-trip and sustained-rate measurement
into the o2lite bring-up slice.
