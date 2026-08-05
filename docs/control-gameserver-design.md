# Control+GameServer Design (Official Architecture Path)

**Terrarium / Tuneshroom / Bit architecture** · v4 · 2026-08-05 · Chris Oltyan — chris@musicalmycology.org, with Roger Dannenberg

**Status: OFFICIAL PATH FORWARD as of 2026-07-18. This file is the canonical
copy.**

> **v4 (2026-08-05) — the instrument interface, from the 2026-07-28 Roger/Chris
> check-in and Roger's follow-up notes.** Three things that were open are now
> decided or corrected. (a) The **Fluid Synth ugen is the standard instrument
> interface** and MIDI is the system-wide control representation for *both*
> sound and light — see the new *Instrument Interface* section. (b) Design rule
> 5 was **wrong as a system-wide rule**: packed-int32 MIDI describes the device
> path only; Arco takes typed per-verb messages and never sees a packed word.
> (c) *Message Routing* now states what a hop actually costs, so hop counts stop
> reading as pure overhead. Rules 1 and 2 also gained the two obligations that
> the check-in surfaced as live failure modes.
>
> **v3 (2026-07-27) — correction.** v1–v2 described the Control+GameServer as
> "a full O2 peer" in three places. That was wrong as written and was never
> what got built: **Control is an o2lite client of the Arco server**, exactly
> like every device. The Arco server is the only full-O2 process in the room —
> a hub, not a peer among peers. No Python full-O2 binding exists (only
> `o2litepy`, which pyarco itself uses), so a full-peer Control was never
> available; the deviation was recorded in
> `docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`
> §7 on 2026-07-20. The new **Message Routing** and **Host Platform** sections
> below state the consequences explicitly. Downstream repos (luxaeterna,
> mm-tuneshroom) already cite both by name.

It supersedes the earlier Musical Mycology direction of an embedded
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
  websockets, and o2lite) and the Control+GameServer, which attaches to that hub
  as an **o2lite client** — the same binding every device uses. Arco is the only
  full-O2 process in the room. See *Message Routing* for what this costs.
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

Everything in the room except the Arco server attaches over **o2lite**, and an
o2lite client has exactly one link — to its host. Its `send()` has **no local
short-circuit**: every message it sends leaves over that link, even one
addressed to a service the sending process itself offers. Arco is the host for
all of them, so Arco relays anything travelling between two clients.

That single fact fixes every hop count in the design:

| Path | Route | Hops |
|---|---|---|
| Control → `/arco` | Control's host *is* Arco | **1** |
| Arco → `/actl` | host → its client | **1** |
| device → `/game/*` | device → Arco → Control | **2** |
| Control → `/ie<N>/*` | Control → Arco → device | **2** |
| sensor-to-LED round trip | device → Arco → Control → Arco → device | **4** |

Two consequences worth stating outright:

**A full-O2 Control would shorten nothing.** The devices are o2lite clients
whose host is Arco, so anything addressed to `ie<N>` transits Arco regardless of
what Control is. Control → `/arco` is already 1 hop because Control's host *is*
the engine. The only thing full-peer status would buy is a local short-circuit
on messages Control sends to itself — and `game` and `actl` are both
**inbound-only** today (devices → `game`, Arco → `actl`). Control never messages
itself, so there is no round trip to eliminate. Keep it that way: if Control
ever needs to reach one of its own services, call the Python method, do not
address the o2lite service.

**An in-process consumer is reached by a Python method call, not by O2.**
Addressing an o2lite service from inside the process that offers it round-trips
through Arco and back. O2 addressing is for the process boundary. This is why
the Lux Aeterna renderer running inside Control is driven by direct calls
(`session.feed_midi(...)`, `.swap(...)`) at **zero** hops, while the same
renderer on a Tuneshroom takes `/light/midi` over the wire at 2 — see
luxaeterna's `docs/deployment.md` for that matrix.

Relaying is also a **capacity** question, not only a latency one: every hop
above is relayed by the same Arco process doing all room synthesis, on a box
that is simultaneously feeding a 44 Hz Lux Aeterna render loop.

**What a hop actually costs** (Roger, 2026-07-29). The hop counts above are not
all the same price, and none of them are dominated by the wire:

- **On-box hops are cheap.** Control↔Arco traffic is loopback to `127.0.0.1` —
  through the kernel, never touching the network interface. There is no WiFi
  latency on those hops at all.
- **The real cost is the polling period at each end**, not the transit. Roger
  runs **2 ms** as his normal compromise between CPU spent polling the network
  and response time, and reckons **1 ms** is affordable on current hardware with
  cores to spare. That is the tunable to reach for during Pi 5 bring-up.
- **Off-box hops are limited by message *count*, not bandwidth.** A 3-byte MIDI
  payload is smaller than its own headers, and WiFi charges per message for
  channel contention and framing regardless of size. So the ceiling on device
  traffic is messages-per-second, and the fix is fewer, fatter messages — not a
  faster link. For scale: Roger's CMU laptop orchestra ran ~25 devices over
  O2/WiFi with occasional drop-and-reconnect (which O2 handles well); published
  experiments reaching ~500 participants needed cellular and still saw heavy
  dropouts. Small-group rooms are comfortably inside the working range; crowd-
  scale Bits are a different engineering problem, not a bigger version of this
  one.

None of this licenses a quoted figure — see *Host Platform*. It tells you which
knob to turn and which hops are not worth optimizing.

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

## Instrument Interface

Decided at the 2026-07-28 check-in and refined in Roger's 2026-07-28 design
notes. This is the shape `ugen_manifest` takes; until now it was a placeholder
with no agreed content.

**MIDI is the control representation, system-wide.** Not because the output is
"MIDI music", but because MIDI is fundamentally a *controller* encoding — it was
built to carry what a player physically did. Device gestures (tilt, shake, tap,
button) map onto MIDI control messages cleanly, and instruments map controller
numbers onto their own synthesis parameters internally. The payoff is
interoperability: one control stream drives any instrument, so swapping a Bit's
sound does not invalidate its gesture mapping. The cost is MIDI's coarseness —
quantized pitch, 0–127 controllers — which is accepted for v1 and revisited only
if a Bit genuinely needs continuous or granular control.

**The same stream drives light.** Lux Aeterna's light manifests already bind
`cc:<n>` lanes onto visual parameters (see the light-manifest v2 adoption spec),
so a gesture that moves a filter cutoff can move a hue with no second vocabulary.
This is why the abstraction below spans audio *and* lighting rather than being an
audio-only concern.

**The default instrument is the Fluid Synth ugen.** `Flsyn` wraps the FluidSynth
general-MIDI library inside Arco, giving a full GM sound set for free — the right
trade while the team's depth is hardware and game programming rather than sound
design. A Bit's `ugen_manifest` therefore parameterizes MIDI instruments against
`Flsyn` rather than hand-building a synthesis graph per role.

**Control needs its own `Synth` abstraction** (Roger's guidance, and the piece
that does not exist yet):

- Do **not** subclass `arco_instr.Instrument` — that is a `Ugen` subclass
  representing a collection of ugens, which is the wrong level. `Synth` is a new
  abstract class of ours, sitting between Interactive Elements and *both* Arco
  instruments and lighting controllers.
- Give it **human method names**, not encoded MIDI: `noteon` / `noteoff` /
  `program_change` / `pitch_bend` / `control_change` / `alloff`. Bit authors
  should never hand-assemble a status byte. Keep the command set small.
- A subclass initialized with an **Arco address plus channel** constructs MIDI
  messages and sends them there — which is how an Interactive Element with its
  own speaker becomes just another sound generator addressable by Control.

`arco/apps/pytest/miditest.py` carries Roger's own reference implementation
(`MidiSender`), including a documented extension path to "another object that
would interpret MIDI in order to control some other Arco Instrument" — i.e. the
lighting target. Start there rather than from scratch.

**Open: channel-per-call, or channel-less Synths?** Roger's notes argue *against*
a channel argument — allocate more `Synth`s instead, since a channel parameter
implies you may send to any channel and get something, which in turn implies
every synth must prepare instruments for all 16. His suggested shape is up to 16
`Synth`s each owning one channel of a shared `Flsyn`. But the shipped
`MidiSender` takes `chan` on every method. The reference code and the written
advice disagree; settle it with him before building on either.

## Design Rules

1. **Receiver-perspective addressing.** Same idiom as `/arco/...` in,
   `/actl/...` back.
2. **Identity in arguments, not addresses.** Every input carries `dev`, and joins
   carry `node`. A phone simulating shroom 3 at node A sends byte-identical
   messages with `dev="ie3"`, `node="A"`. One handler per verb in Control
   regardless of fleet size, one place to validate and log.

   **Never substitute `o2lite.bridge_id` for `dev`.** o2lite hands each client a
   per-O2-process id (`-1` until connected, non-negative after), and it is
   tempting as a ready-made device key. It is not one: **it changes when a client
   disconnects and reconnects**, so a device that drops mid-session comes back as
   a different id while remaining the same player at the same role. `dev` is
   stable by construction and is what `DevicePool` and `RegistrationState` key
   on. Use `bridge_id` for connection bookkeeping only, never identity.
3. **Single writer to `/arco`.** Only Control builds graphs and owns the ugen id
   space. Interactive Elements express intent to `/game`; Control decides the
   audio consequence.

   **Owning the id space means freeing it.** A Bit unload must tear down every
   ugen that Bit created, before the next Bit builds its graph. The failure this
   guards against is real and was hit in an early prototype: loading a new Bit
   over an old one allocated a fresh ugen set without releasing or re-addressing
   the previous one, so the room kept playing the *old* Bit's graph while the new
   Bit's ugens sat orphaned and silent. UNLOADING is always reachable even when a
   Bit hook raises precisely so this teardown cannot be skipped — that guarantee
   is worth nothing if the teardown itself does not free the ugens.
4. **Timestamps at the source, scheduling at the sink.** Devices stamp inputs
   with `o2l_get_time()` at the physical event; Control schedules audio and cues
   ahead of time. With Arco as the sample-locked reference clock, the forwarding
   hop through the Arco server costs almost nothing musically. It only eats into
   true feedback paths (gesture to sound), where WiFi jitter dominates anyway.
   The `/ie<N>/play` local-sample path exists precisely so the tightest feedback
   never crosses the network.
5. **The two MIDI paths use different encodings. This is correct, not an
   oversight.** v1–v3 stated packed-int32 as a system-wide rule; that was wrong,
   and the two paths never meet:

   - **To a device** (`/ie<N>/...`, and Lux Aeterna's `/light/midi`): **packed
     int32**, because o2lite lacks O2's native `'m'` type. Lux Aeterna's ratified
     packing is right-aligned — `(status << 16) | (data1 << 8) | data2`. Blobs
     for sysex or bulk.
   - **To Arco** (`/arco/flsyn/...`): **typed per-verb messages with unpacked
     integer arguments** — `/arco/flsyn/noteon "iiii" ref chan key vel`,
     `/arco/flsyn/cc "iiii" ref chan num val`, and so on. No packed word is ever
     constructed, so the missing `'m'` type is simply not a problem on this path.

   **Trap worth naming.** `arco/apps/pytest/miditest.py` includes a
   `midi_osc_fmt` helper that decodes packed MIDI **left-aligned**
   (`status << 24 | data1 << 16 | data2 << 8`) — Roger flags in-line that this
   is also the reverse of PortMIDI's order. It is a decoder for MIDI arriving
   from elsewhere, and it fans straight out to the unpacked `Flsyn` calls above;
   it is not the Control↔Arco contract. Copy it toward Lux Aeterna unexamined and
   every value lands shifted by a byte.

## What a Bit Is, in Code Terms

A module loaded by Control that declares:

1. its role table with classes, capacities, and node mappings,
2. handlers for the `/game` verbs it uses,
3. per-role graph-builders for the Bit's patch and per-player channel strips,
4. cue logic for device light/sound, and
5. a scoring function over the input stream.

The `/game/*` message stream is the complete input history of a session, so
logging it gives record/replay for free: deterministic regression tests, headless
Control with scripted players in CI, and post-hoc debugging of live sessions.
Registration contention is part of what replay covers, since joins and denies are
just messages.

## Implementation Proposal

Control+GameServer in Python on pyarco. Bits as Python plugin modules gives us
fast iteration on game design, and the process is an o2lite client of the Arco
server on the same box, so Python overhead is irrelevant at these message rates.
Anything that ever proves hot is isolated behind O2 addresses and portable
without touching the protocol.

o2lite is not a compromise here — it is the only Python binding that exists
(`o2litepy`, which pyarco itself uses), and per *Message Routing* a full-O2
Control would not shorten a single path in the current design.

## Host Platform

The venue target is **bare-metal Linux on a Raspberry Pi 5**, with a mandatory
I2S DAC HAT and no virtualization layer anywhere in the venue path.

**Virtualized hosts are ruled out for bring-up.** Both transports the room
depends on are UDP over the LAN:

- **O2 discovery** is UDP. A client that cannot receive it cannot find the hub.
- **Art-Net to WLED controllers** is UDP. Frames must reach controllers on the
  LAN directly.

A NAT'd VM or a **WSL2** host sits on its own virtual subnet, so neither arrives
without mirrored networking or hand-rolled port proxying. Treat WSL2 as a
non-starter for anything that must reach a live hub or real LEDs, rather than
something to be worked around.

**Develop without hardware using luxaeterna's `WebSimBackend`.** It is a
`DMXBackend` that records DMX frames and streams them to a self-contained
browser canvas — an on-screen 12-LED Shroom. No LEDs, no controller, no LAN, so
it is the supported path on a laptop, VM, or WSL2 box (`serve=False` gives a
headless frame recorder for tests). mm-terrarium's `harness/led_smoke.py` is the
worked example.

**Timing numbers are only meaningful measured on target.** Frame rate, render
loop headroom, drain latency, and sustained message rate taken on a laptop or a
virtualized host say nothing about the Pi 5 — which is relaying every hop in
*Message Routing* through the same process doing all room synthesis while
feeding a 44 Hz render loop. Do not quote a figure that was not measured on the
venue box. The M1a-era "round trip under 50 ms" figure in particular does **not**
carry over: it was measured against the `o2host` topology with Control not in
the path.

## Open Questions

1. Does one `game` service with lifecycle verbs (`hello`, `join`, `role`, `deny`,
   `data`, `release`) seem right, or would you rather lifecycle and Bit-specific
   gameplay live under separate services?
2. For per-player audio into the mix, is `o2audioio` over o2lite the intended use
   case? Any concerns about several simultaneous device streams over WiFi
   (roughly 768 kbps per 16-bit mono 48k stream)?
3. Browsers as `ui<X>` services versus a reply-address argument in `/game/hello`:
   preference?
4. **`/game/data` or `/actl/from_ie` for device input?** This design routes
   Interactive Element input to `/game/data "stb" dev time payload` plus discrete
   verbs like `/game/hit`. Roger's 2026-07-28 notes instead suggest
   `/actl/from_ie` carrying `(ie_number, parameter_name, parameter_value)`,
   dispatched to `object.from_ie(...)` on whatever object that element is bound
   to. The intent matches — his `/actl` is "the arco controller", which in this
   split *is* Control — but the address and the payload shape do not. Worth
   settling before the o2lite transport lands, since it fixes the device-side
   vocabulary.
5. **Channel-per-call or channel-less `Synth`s?** See *Instrument Interface* —
   the written guidance and the `MidiSender` reference implementation disagree.
6. **Confirm pyarco's source of truth is the arco repo.** Upstream now ships
   `pyarco/` and `o2litepy/` at the root of `rbdannenberg/arco`, with
   `apps/pytest/mintest.py` as the worked example and `doc/pyarco.md` alongside.
   That reads as the answer to bootstrap open question #1 (submodule vs. pinned
   sibling), and it makes MM's standalone `pyarco` checkout a legacy fork — but
   it is inference from code, not something Roger has stated. One sentence from
   him closes it.
