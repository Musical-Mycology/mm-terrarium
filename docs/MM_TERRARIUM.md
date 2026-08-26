# mm-terrarium — the per-room venue server (Arco + Control+GameServer)

The **Terrarium Server**: the per-installation venue box for Musical Mycology
Shroom installations. **One Terrarium per room** — a capable computer plus an
LED display and speakers — hosting **two processes on the same box**:

- the **Arco server** (the O2 hub: HTTP, websockets, o2lite; **all** room
  synthesis), and
- the **Control+GameServer** (an **o2lite client** of that hub, offering services
  `game` and `actl`): the Bit runtime, registration and role assignment,
  scoring, and adjudication.

Arco is the only full-O2 process in the room. Control attaches over o2lite
exactly like every device, so Arco relays anything travelling between two
clients — `/arco` and `/actl` are 1 hop, `/game/*` and `/ie<N>/*` are 2, and a
sensor-to-LED round trip is 4. A full-O2 Control would shorten none of them
(see *Message Routing* in the design doc).

Interactive Elements — hardware Tuneshrooms over o2lite, phones over websockets
— connect to the Arco server; all gameplay traffic addresses `/game/...`; and
**only Control writes to `/arco`**. This repo is `mm-terrarium`'s canonical
service doc; the authoritative *architecture* is in-repo at
[`docs/control-gameserver-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/control-gameserver-design.md)
(the official path forward as of 2026-07-18, developed with Roger Dannenberg).
See `MM_ARCHITECTURE.md` (MM-internal) → *Per-service summary* for the cross-repo slot, and
`MM_HARDWARE_DESIGN.md` (Tier 4 — Terrarium) for where the box sits in the
hardware fleet.

> **Status: early, and the whole test suite still runs fully offline** against
> fakes and localhost sockets, with **no O2 network, no Arco server and no
> pyarco importable**. That property is load-bearing and is pinned by tests.
>
> What has since crossed the line into real: **Lux Aeterna** (a dev/test
> dependency since Slice 1, driving the LED sim and per-device rendering) and,
> as of the 2026-08-06 Tuneshroom audio slice, **pyarco** (dev/test-only,
> reached by `PYTHONPATH`), which builds the first real ugen graph on a live
> Arco server. `harness/led_smoke.py --audio` has been verified making sound on
> hardware: a sustained drone whose loudness and timbre track the light off one
> shared MIDI stream.
>
> **O2/o2lite is no longer absent, and the old blanket "no O2" line was always
> imprecise.** It described the *device wire*. The Arco connection was a
> different story all along: pyarco talks to Arco over `o2litepy`
> (`pyarco/arco_engine.py:12`) and `arco.initialize()` blocks until clock sync
> completes, so mm-terrarium has been a **clock-synced o2lite client of Arco**
> whenever the Room's audio is up. As of the 2026-08-12 slice Control also
> **offers `game` over that same connection** (`devicelink/o2_transport.py`),
> so device registration can cross a real O2 hub. The websocket transport
> remains and is still the default; o2lite is opt-in per run.
>
> **The o2lite path has been run against a live Arco and observed working**
> (2026-08-13), which is the first time anything in this repo's device path
> has been confirmed on a real O2 network rather than against fakes. What was
> measured: a simulated Tuneshroom joined `TEST_PLAYER_NODE` and received its
> composed role blob over the hub; **820** LED frames were delivered and
> rendered with visible gesture-driven hue motion; the Room drone sounded
> from Arco on RUNNING. Reproduced on **2026-08-14** with 2418 more frames.
>
> Two claims from that first run have since been **corrected by measurement**
> (2026-08-14), both in the same direction — the path is healthier than it
> looked. The clocks agree to **well under a millisecond**, not 7 ms; the 7 ms
> was read off a single frame that also carried delivery time. And **timing
> IS honored** — O2 delivers each frame at its declared `when`, and end-to-end
> delivery measures 4.5 ms at p50 and 11.8 ms at p99. The "762 of 820 frames
> clamped" that read as *timing not honored* was an artifact of re-checking a
> deadline on a frame that O2 had already delivered on time. See
> *`cue_horizon` is measured as of 2026-08-14* under *Not yet built* below,
> which carries the numbers and the method.
>
> **As of 2026-08-14, the cue machinery is also load-bearing.** The earlier
> gap here -- no Bit could compute a `T`, no Bit emitted a `LightCue` -- was
> closed the same day by a separate slice: `TestBit`'s `tilt` handler now
> emits a Room-targeted cue alongside the calling device's own, and a new
> `Bit.cues(at)` hook drives the Room's hue on its own with nobody joined.
> Both were confirmed against a real Arco: a tilt visibly moved the calling
> device's light, the Room's light, and the Room's drone timbre together,
> from one shared computed time; the Room animated with no device joined.
> That confirmation used the default 60 ms horizon and read a device-side
> clamp count of 1405, then 1081, across two runs -- read against the
> measurement above, that is the same saturation artifact, not a sign the
> horizon is wrong.
>
> Still absent: **fairyring** and a real scoring framework. The "no
> production Bit" gap closed 2026-08-20: **MetronomeBit** (see *Landed
> subsystems*) is the first production game Bit, though scoring beyond a
> Bit's own `result()` payload remains unbuilt.
> As of 2026-08-10, **`Room`** exists as a boot-time concept and orchestration
> (`control/boot.py` now spawns Arco itself, resolves a `RoomType`, and gates
> Bit loading on it), and **TEST room now has a real renderer**: a devicelink-
> connected simulator subprocess (browser-canvas LEDs) plus a real Arco voice,
> both genuinely wired to `RoomBridge`.

## What it is, in one picture

```
Phone browser --ws--+
                    v
Shroom (o2lite) --> +--------------+     o2lite, same box
Shroom (o2lite) --> | Arco server  | <--------------------> Control+GameServer
Shroom (o2lite) --> | "arco"       |                        "game", "actl"
                    +--------------+
       each Tuneshroom offers "ie<N>", each browser offers "ui<X>"
```

A **Bit** is a loadable game/experience module inside Control. It declares the
**roles** players can adopt, which **Registration Nodes** (tap points — an NFC
tag or QR code is enough) grant which roles, the `/game` message vocabulary, the
ugen graph it builds on Arco, the per-device light/sound behavior, and the
scoring logic. Roles have a **class** (`unique` capacity-K, `shared` unbounded,
`jam` unbounded-but-unscored), a capacity, an ordered node→role fallback list, a
per-player graph-builder, and a `scored` flag. The full player flow (hello →
join → role → play → complete, mapped to `/game/*` and `/ie<N>/*` messages) is
specified in the in-repo design doc — this deep-dive does not restate it.

<!-- diagram:player-flow GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
Player flow, hello to complete

+------------+                             +------+                             +---------+
| Tuneshroom |                             | Arco |                             | Control |
+------------+                             +------+                             +---------+
       |                                       |                                     |
       |-------------/game/hello-------------->|                                     |
       |                                       |                                     |
       |                                       |------------/game/hello------------->|
       |                                       |                                     |
[ SETUP holds registration open; --setup-seconds widens the window ]
       |                                       |                                     |
       |-----/game/join TEST_PLAYER_NODE------>|                                     |
       |                                       |                                     |
       |                                       |-------------/game/join------------->|
       |                                       |                                     |
       |                                       |<---/ie1/role composed config blob---|
       |                                       |                                     |
       |<--------------/ie1/role---------------|                                     |
       |                                       |                                     |
       |--------------/game/tilt-------------->|                                     |
       |                                       |                                     |
       |                                       |-------------/game/tilt------------->|
       |                                       |                                     |
       |                                       |</ie1/leds at = origin + cue_horizon-|
       |                                       |                                     |
       |<--------------/ie1/leds---------------|                                     |
       |                                       |                                     |
       |                                       |<------------/ie1/release------------|
       |                                       |                                     |
       |<-------------/ie1/release-------------|                                     |
       |                                       |                                     |
+------------+                             +------+                             +---------+
| Tuneshroom |                             | Arco |                             | Control |
+------------+                             +------+                             +---------+
```
<!-- /diagram:player-flow -->

## Landed subsystems

All Python, all offline-tested. **Run the suite through the project venv**, not
a bare interpreter:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -v
```

There is no bare `python` on the dev boxes, and the sibling **luxaeterna**
dev dependency is installed **only** in `.venv`. Invoking `python3` instead
collects an import error in `tests/test_terrarium_boot.py` that looks exactly
like a real failure and is not. That trap has already cost one debugging
detour: a contributor chased the phantom error, concluded the suite was
broken, and filed a follow-up task for it before the environment was
identified as the cause.

**A fresh git worktree has no `.venv` at all**, so that trap is one step
away every time one is created: the suite command above fails outright,
and the obvious recovery is to reach for `python3` and land straight in
the paragraph above. Symlink it instead:
`ln -s /Users/chris/projects/mm-terrarium/.venv .venv` from the worktree
root. `.gitignore` matches `.venv` without a trailing slash specifically
so that symlink is ignored -- a directory-only pattern does not match a
symlink, and every worktree used to show a spurious untracked `.venv`
because of it.

### `control/` — the Control+GameServer lifecycle engine
The game-launching engine: load a Bit, open registration, run it, score it,
return to a clean waiting state. Landed in the first-slice spec
([`2026-07-20-control-gameserver-first-slice-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md)).

- **State machine:** `IDLE → LOADING → LOADED → SETUP → RUNNING → COMPLETING →
  UNLOADING → IDLE`. SETUP is the waiting-room (registration open); during
  RUNNING **scored roles are denied but jam roles stay open** (an installation
  has casual foot traffic). Control stays **Bit-agnostic** — it never evaluates
  a win condition itself; the Bit signals completion from `update(dt)`.
<!-- diagram:lifecycle GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
           ┌───────┐                
           │ IDLE  │                
           │       │                
           └───────┘                
               │                    
               ▼                    
          ┌──────────┐              
          │ LOADING  │              
          │          │              
          └──────────┘              
               │                    
               ▼                    
          ┌─────────┐               
          │ LOADED  │               
          │         │               
          └─────────┘               
               │                    
               ▼                    
    ┌──────────────────────────┐    
    │SETUP (registration open) │    
    │                          │    
    └──────────────────────────┘    
               │                    
               ▼                    
┌──────────────────────────────────┐
│RUNNING (scored closed, jam open) │
│                                  │
└──────────────────────────────────┘
        │              │            
  Bit says done        │            
        │              │            
        ▼  abort(): skips COMPLETING
 ┌─────────────┐       │            
 │ COMPLETING  │       │            
 │             │       │            
 └─────────────┘       │            
           │           │            
           ▼           ▼            
  ┌─────────────────────────────┐   
  │UNLOADING, then back to IDLE │   
  │                             │   
  └─────────────────────────────┘   
```
<!-- /diagram:lifecycle -->
- **Data model:** `RoleTable` (static, Bit-declared: `Role` = name/class/
  capacity/`scored`/`ugen_manifest`/`light_manifest` — the latter in
  luxaeterna's **light-manifest v2 wire shape** — plus an optional `welcome`
  pair declaring the role's light+audio adoption ceremony in one place, and
  the node fallback map), `DevicePool` (Control-global, `dev → connection
  info`, survives Bit lifecycles), `RegistrationState` (runtime
  `dev → (node, role, class)` with live per-role counts via the public
  `counts()` accessor).
- **Per-role config blobs (PR #5):** `control/role_config.py` validates each
  Bit's authored `light_manifest`/`welcome` declarations at `load_bit`
  (shallow structural checks with located errors — a typo'd Bit fails as a
  load-time `BitLoadError`, never as a device-side parse error
  mid-installation) and composes the `/ie<N>/role` config blob at grant time:
  the v2 manifest with `bit_name`/`bit_version`/`role` provenance stamped and
  the welcome **light** half folded in, deep-copied. Granted joins surface the
  blob on `JoinResult.config` for the future o2lite transport; the welcome
  **audio** half never ships to the device — it stays readable off
  `Role.welcome` for the future Arco cue path. `Bit.version` +
  `GameServer.bit_name` (the registry key) supply the provenance.
- **`Bit` interface:** minimal hook set — `role_table`, `on_setup_enter()`,
  `on_run_start()`, `update(dt)`, `on_complete()`, `on_unload()`, plus optional
  `result()` (completion payload), `status()` (generic key/value read-out), and
  `verb_handlers()` — **routed as of Slice 2** via `GameServer.data()`; it was
  declared but unreachable before then. As of the telemetry-capture slice, a
  verb handler's return value carries two meanings: a list of light cues (as
  before) or a `str`, which `GameServer.data()` treats as a handler-declared
  refusal surfaced to the device as `/<dev>/error` — checked *before* the cue
  list's truthiness test, so a refusal string is never iterated
  character-by-character as garbage cues. Raising is still reserved for bugs
  and yields the generic `"handler error"`. As of the 2026-08-14
  load-bearing-timed-cues slice, a `verb_handlers()` handler is called
  `handler(dev, args, at)` — `at` is `GameServer`'s computed presentation
  time, `origin + cue_horizon`, so a Bit never sees a raw gesture stamp or the
  horizon itself — and the interface gained `cues(at) -> list`, called once
  per RUNNING tick with the same cue vocabulary, for a Bit to emit cues with
  no gesture behind them (the seam the Room-ambient-animation gap below was
  waiting on). As of the MetronomeBit slice (2026-08-20) the interface also
  carries an optional no-op `on_join(dev, role_name)` hook, called guarded
  once per granted non-ROOM join -- the only way a Bit can learn join order,
  which turn-based gameplay needs.
- **Observer hooks:** a **multi-observer** list (`add_observer()` with
  notify-all) fires `on_state_change` / `on_registration_change` /
  `on_devices_change`, plus **two** transport-owned sinks: `on_release` (one
  call per device during UNLOADING) and `on_light_cue` (added in Slice 2, for
  cues a Bit's verb handler emits). Both are wrapped by the engine, so a
  failing transport cannot wedge it. This is the shared seam the uplink,
  console, and devicelink all attach to.
- **`abort()`** — Control-initiated early termination. It runs the Bit's
  `on_complete`/`on_unload` hooks best-effort, same as a normal completion,
  but **skips the COMPLETING state** and goes straight to UNLOADING — the
  hooks run, the state does not change. `State.COMPLETING` has exactly one
  call site, inside `_complete()`, the tick-triggered path; `abort()` never
  reaches it. Separately, and regardless of which path got there,
  UNLOADING is **always reachable even if a Bit hook raises** (deliberate —
  a misbehaving Bit must never wedge Control loaded).

### `bits/` — reference Bits
`TestBit` is the **durable reference/regression fixture** (not throwaway): a
**scored** `shared` role (`player`) and an unscored **jam** role (`jammer`),
each granted by its own Registration Node — which is what makes the
scored-vs-jam RUNNING join rule a *tested* behavior rather than an assumption.
It auto-signals completion after a fixed duration so the whole lifecycle is
exercisable with no live Arco. It is the lone exemplar of the `ugen_manifest` /
`light_manifest` / `status()` seams — and as of PR #5 its `player` role carries
a **real light-manifest v2 declaration** (one instrument, one `cc:74 → hue` lane)
plus a welcome pair, the declaration that formally froze the v2 schema; `jammer`
glows too since PR #50 — a dim green aurora (hue 0.33 / level 0.18) on its own
`cc:1` (level) / `cc:2` (hue) lanes, tilt brightening it and bending hue toward
yellow (negative gamma) or purple (positive). It deliberately does NOT share the
player's `cc:74` lane, whose plain full-rainbow mapping is the wrong shape.
`jammer` keeps an empty `ugen_manifest` (no-audio path); the no-light session
path it used to pin lives in luxaeterna's empty-manifest director test now.

Both of its light instruments are luxaeterna **field-rate** gestures that render
without a note — deliberately, after the note-triggered `bloom` proved wrong for
both slots. The welcome light half is `glow` (a bare `bloom` welcome rendered
**dark**: a `SignatureDecl` has no lanes, so it never triggered the voice it
needed). The running visual is `aurora`, which breathes and glides its hue under
`cc:74`; `bloom` froze its colour at note-on, so sweeping the hue meant
re-triggering constantly — a visible **strobe**. The running declaration
therefore carries **no note lane**. As of the Tuneshroom audio slice the
pipeline *does* feed a note-on, a sustained drone the audio path needs because
FluidSynth is silent without one, but light still ignores it: no note lane, so
the strobe fix holds. `player` also gained a `cc:11 → level` lane, which opts
`aurora` out of its private breathing clock so Control generates the breath and
the light and the sound read the same number.

### `uplink/` — outbound remote control (the *outbound* sibling)
`UplinkAgent`: makes `GameServer` remotely drivable/observable over a
**persistent outbound websocket** to a *future* mm-fairyring broker, without
`GameServer` depending on that link existing or being up. Landed in
[`2026-07-20-terrarium-uplink-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md).

- Down-commands `load_bit` / `run` / `abort` map to `GameServer` calls (engine
  errors become `error` events, never raised across the wire); up-events
  `state_changed` / `registration_changed` / `bit_completed` / `error` are
  pushed reactively from the observer hooks.
- Owns connection lifecycle: reconnect-with-backoff and **resync-on-reconnect**
  (a `state_changed` + `registration_changed` snapshot); nothing is buffered
  during an outage. A small JSON wire protocol (dataclasses in
  `uplink/protocol.py`); `WebSocketTransport` (real socket) + `FakeTransport`
  (in-process test double).
- **Never in the real-time loop:** `join`/`tick` device traffic stays local at
  o2lite speed; only lifecycle + registration counts cross the link. A live Bit
  runs **identically** whether or not the uplink is connected.

### `console/` — the Terrarium Console, a local admin panel (the *inbound* sibling)
**(landed PR #3.)** A Bit-agnostic **local admin panel** — the durable
front-end fixture every future Bit is managed and monitored through. It resolves
the first slice's deferred "how does a human trigger `load_bit`/`run` at a
venue" question. Landed in
[`2026-07-21-terrarium-console-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-21-terrarium-console-design.md).

- **`ConsoleServer`** — a single-port server that serves a self-contained
  `static/index.html` over HTTP (`GET /`) and upgrades everything else to a
  websocket on the same port (via the `websockets` `process_request` hook). It
  fans out to *N* connected browsers: each new client gets a full `snapshot`,
  subsequent events broadcast to all, and a dead/slow client is dropped without
  blocking others.
- **`ConsoleAgent`** — the transport-agnostic brains, local sibling of
  `UplinkAgent`: registers as a `GameServer` observer, translates inbound
  commands, builds the connect-time `snapshot`, and produces broadcast events.
  It monitors lifecycle **state**, **registration** (roles/capacities/
  occupancy), the **device pool**, per-role **media manifests — audio
  `ugen_manifest` + light `light_manifest`**, and a generic `Bit.status()`
  read-out, plus an in-memory event log. It **reuses** `uplink.protocol`'s
  command parsing and the byte-identical `state_changed` / `registration_changed`
  builders (single source of truth).
- Two schema-stable seams landed with it so the panel is a genuine fixture:
  `Role.light_manifest` (sibling to `ugen_manifest` — a placeholder until
  PR #5 froze it to the light-manifest v2 wire shape; `role_view` now also
  carries the role's `welcome` declaration) and `Bit.status()`.
- **Trust model:** trusted-LAN operator, **no authentication**, default bind
  **`127.0.0.1`** with `0.0.0.0` LAN exposure an explicit opt-in. This
  assumption is load-bearing — the moment a console faces an untrusted network,
  auth becomes a prerequisite, not an enhancement.
- **Reachable during a live run only as of 2026-08-17.** Until then
  `ConsoleServer`/`ConsoleAgent` were constructed **only under `tests/`** and
  no harness driver built either, so there was no panel to open at a venue.
  `harness/terrarium_boot.py --console-port N` (and `run_stack`'s passthrough)
  now serves it and prints the URL. Off by default. It also gained a **Room
  panel**; see the Room-panel section below.

### `harness/` — the in-process LED-sim harness (Slice 1)
`DeviceBridge` + `led_smoke.py`: the first end-to-end exercise of the
light-manifest-v2 seam. It grants TestBit's `player` role, feeds the composed
`/ie<N>/role` blob into a luxaeterna `LightSession` (via a **dev/test dependency
on luxaeterna** — the first code coupling, venue-server → renderer), and renders
it to luxaeterna's new `WebSimBackend` (a browser canvas Shroom). Injects canned
MIDI via `LightSession.feed_midi`: **a `cc:74` ping-pong ramp plus the
Control-generated `cc:11` breath** (no note-ons: `aurora` has no note lane, and
both glide between the coarse steps).
Still **in-process — no o2lite wire**; the
device wire is Slice 2. `led_smoke.py` takes `--hold` (serve until Ctrl-C) /
`--seconds N` to keep the browser demo watchable — otherwise it's TestBit's
~2 s one-shot. Regression: `tests/test_led_smoke.py` (headless) + CLI/`build()`
unit tests in `tests/test_led_smoke_cli.py`.

`--audio` (off by default) additionally starts an Arco-backed voice pool and
feeds the **same** `cc:74`/`cc:11` bytes to the synth that go to the light
session, from one statement. `cc:74` glides aurora's hue and sweeps FluidSynth's
filter; `cc:11` drives aurora's `level` and the synth's expression, so the
visible breath and the audible swell are one value rather than two clocks that
agree. Needs a hand-started Arco server (`apps/pytest/server` is a curses app)
and `PYTHONPATH=/Users/chris/projects/arco`. Without the flag the demo is
unchanged and needs no Arco.

Two operational traps, both hit during live testing:

1. **The soundfont must be a real General MIDI set.** `harness/arco_synth.py`'s
   `DEFAULT_SOUNDFONT` points at `FluidR3_GM.sf2`, and that is the one to use. A
   non-GM soundfont silently produces the wrong instruments, because program
   numbers mean different things in it. The trap actually hit:
   `VintageDreamsWaves-v2.sf2` is a 314 KB synth-waveform collection whose
   program 89 is "Techno Bells" (percussive, decays fast) rather than the GM
   "Warm Pad" that `bits/test_bit.py` and `control/audio.py`'s
   `WELCOME_INSTRUMENTS` assume, so the sustained drone died within seconds.

2. **Only the first client after an Arco server start gets working audio, on
   macOS.** `pyarco`'s `arco.initialize()` unconditionally calls `reset()`,
   which sends `/host/clear`. That tears down the server's audio stream and
   frees every ugen, including the `Flsyn` and its loaded soundfont. The
   server's audio re-open then fails with PortAudio `-9988, Invalid stream
   pointer`. Practical consequence: restart the Arco server before each run of
   `--audio`. This is upstream in Arco, not something this repo can fix.

`control/breath.py` generates that shared `cc:11` value: it holds the breath
envelope (point-for-point what luxaeterna's aurora preset used to loop on its
own clock) and `BREATH_CC`. It moved out of `led_smoke.py`'s own `main()`
because declaring `level` opts *every* renderer of that role out of its private
breathing clock, not just this demo, so a generator living in one demo's
`main()` would have left other consumers of the role rendering a static
surface. `harness/led_smoke.py` and `devicelink/agent.py` both tick it now.

### `control/audio.py` + `harness/arco_synth.py`: the first Arco write path
`AudioBridge` is the audio-side sibling of `DeviceBridge`: it reads a role's
`ugen_manifest`, acquires a voice, applies the role's declared cc lanes, holds
the drone, plays the welcome audio half (its first consumer since PR #5), and
frees every voice at unload. It is **pure and never imports pyarco**, which is
what keeps the offline suite green. `ArcoSynthPool` is the concrete backend: one
`Flsyn` ugen with up to 16 voices sharing it, lazy pyarco imports inside
`start()`, and `sched.poll()` driven from the existing tick rather than
`sched.run()` owning the loop.

Two things here are **deliberately provisional**. The voice type is
`DeviceVoice`, not `Synth`, and takes **no channel parameter**: the channel is
real but internal, so the abstraction Roger has open is not frozen by this demo.
And `ugen_manifest` v0 is *not* the audio-manifest freeze that light-manifest v2
was for light: shallow validation, no cross-repo contract, no device-side
parser. The backend living in `harness/` is likewise a holding position until
pyarco's source-of-truth is settled.

### `harness/` — venue-array and device tooling (pre-hardware)
Four modules landed 2026-08-06 ahead of the hardware they drive, so the student
hardware track starting 2026-08-24 inherits working code rather than writing it.
**None of them has touched real hardware.** They are offline-tested tools, and
every claim below is about code, not about a measured installation.

- **`array_smoke.py`** — the venue-side sibling of `led_smoke.py`: a real strip
  over Art-Net instead of a browser canvas, and a pixel count large enough to
  span universes. The 6 m Terrarium array is 864 px × 4 ch = 3456 channels =
  **7 Art-Net universes**, and both `ArtNet.send()` and `Universe` are
  single-universe, so it builds on luxaeterna's new `PixelSpan` / `UniverseSet` /
  `MultiUniverseOutputLoop`.
  **Power limiting is not optional here.** 864 SK6812 RGBW pixels draw **21.6 A**
  at full white against a **12.5 A** supply, so `build(max_amps=...)` installs a
  `PowerLimiter` and `limited()` wraps the paint hook so every frame passes
  through it. The CLI always passes `TERRARIUM_MAX_AMPS` and exposes no flag to
  disable it.
- **`render_bench.py`** — frame-timing statistics (mean, min, p95, worst frame).
  See *Host platform* below for why the worst-frame figure is the one that
  matters. `summarise()` and `measure()` take no luxaeterna dependency, so they
  run in the core offline suite.
- **`shroom_client.py`** — the Radxa Tuneshroom's `devicelink` participation.
  Socket-free by design: `handle()` takes a decoded message and returns the
  address it handled or `""` if it dropped the frame, so the whole protocol
  surface is testable with no socket and no device. The transport half is
  confined to `main()` because that is the part o2lite replaces.
  `LED_CHANNELS = 36` matches the Tuneshroom wire (12 px × GRB) — see the RGBW
  mismatch under *Not yet built* below. As of 2026-08-17 that is a **default,
  not a fixed width**: `ShroomClient(..., expected_channels=...)` is
  per-instance, because a Room is not a Tuneshroom and ships 180 channels
  (60 px × 3). A wrong-width frame is still dropped and logged, never
  truncated: rendering a short frame would turn a configuration mismatch into
  a subtly wrong picture instead of a logged drop.
- **`local_sample.py`** — preloaded sample playback for the sub-20 ms tap path.
  `last_latency_ms` measures **dispatch, not sound**; the real tap-to-sound
  figure has to be read off a waveform and must not be quoted from this number.

**Dependency note:** `array_smoke` and `render_bench.main()` need luxaeterna's
`pixelspan`, `universeset` and `power` modules. Those are on luxaeterna `main`
as of its PR #11, so a current editable install has them and no special checkout
is needed. `tests/test_array_smoke.py` `importorskip`s luxaeterna anyway, so the
core suite runs without it.

### `devicelink/` — the device-facing websocket transport (Slice 2)
Control's first device wire. The inbound sibling of `console/`, with the same
split: `DeviceLinkServer` (socket-only, drain-based) plus `DeviceLinkAgent`
(transport-agnostic brains, driven from the tick loop). It holds one
`DeviceBridge` → luxaeterna `LightSession` per joined device, ships
`JoinResult.config` verbatim as `/<dev>/role`, and streams rendered frames as
`/<dev>/leds` on change.

Messages are **JSON envelopes mirroring o2ws field-for-field**
(`timestamp`/`address`/`typespec`/`args`) — the vocabulary is real, the framing
is not, so the later swap to o2ws is mechanical. **Arco is not in this path**,
so nothing here may be read as a hop count or a latency figure. Same trust
model as the console: trusted LAN, no auth, `127.0.0.1` by default.

- `/ie<N>/room` is pushed on hello and on state/registration change; devices
  never request it.

`DeviceLinkAgent` also ticks `control/breath.py` now, feeding every joined,
non-closing device's `cc:11` on change. The Tuneshroom audio design originally
scoped devicelink out entirely, but that was wrong for the light half: once
`player` declares aurora's `level` param, a connected device that is never fed
`cc:11` renders a static surface pinned at 0.55 rather than breathing, which is
a regression, not a deferral. So driving the breath here (light) is in scope
and done; wiring devicelink to an `AudioBridge` (audio) remains genuinely
deferred, see *Not yet built* below.

Slice 2 also **routed `Bit.verb_handlers()`**, which had been declared on the
`Bit` interface since the first slice but which `GameServer` never called.
`GameServer.data(dev, verb, args)` now dispatches to it and forwards emitted
light cues through the transport-owned `on_light_cue` sink — so a Bit still
decides the light consequence (boundary rule 3). `TestBit` gained a `tilt`
handler mapping tilt onto `cc:74`, making verb dispatch a tested behavior.

**Release is asynchronous, and that is load-bearing.** `LightSession.clear()`
only *enqueues* a `ClearEvent`; the queue is drained inside `render_into()`. So
a device dropped from the render map at release never renders its closing fade
at all — the session never even enters `CLOSING`, and the device freezes on its
last running frame. `DeviceLinkAgent` therefore keeps a released device **in**
`bridges`/`_universes`, tracks it in `_closing`, renders it every tick until
`session.state` leaves `CLOSING`, and only then tears it down and sends
`/<dev>/release`. A `_MAX_CLOSING_FRAMES` bound force-releases a session that
never finishes closing, so one stuck device cannot render forever. Two
consequences for anyone editing this: a fresh `/game/join` must clear that
device's `_closing` entry (a rejoin mid-fade would otherwise destroy its own new
session), and `/<dev>/release` arrives **after** the fade, not at the moment the
Bit ends.

**Both transport-owned sinks are guarded on the engine side.** `_unload` wraps
each `on_release(dev)` call and `data()` wraps each `on_light_cue(...)` call, so
a failing transport cannot strand the remaining devices or wedge Control in
`UNLOADING`. That guarantee has its own engine-level regression test.

Driver: `python -m harness.devicelink_smoke --hold`. **Trap:** `main()` calls
`load_bit()` straight into `run()` with zero real-world gap, and
`RegistrationState.join()` refuses a **scored** role (e.g. TestBit's
`player`, node `TEST_PLAYER_NODE`) once `RUNNING` — a phone scanning a QR
that instant was denied instantly, with no window to join. Pass
`--setup-seconds N` (default `0`, unchanged behavior) to hold the Bit in
`SETUP` — polling DeviceLink so joins land — for `N` seconds before `run()`
closes it; only the unscored jam role stayed joinable without this.

### `capture/` + `bits/capture/capture_bit.py` — labelled sensor telemetry capture (tool Bit)
A **tool Bit**, not a production game Bit — it doesn't close the "no
production Bit exists" gap below. Built to answer a concrete measurement
question: mm-tuneshroom's two gesture detectors (native `TapDetector` and
browser `sensors.js`) both guess a tap/shake threshold and, on inspection,
disagree with each other by roughly 3x despite a comment claiming they
mirror one another. `CaptureBit` records what a real phone's accelerometer,
gyroscope, and microphone actually emit during a labelled gesture, so a
future slice can derive real thresholds instead of guessing.

- **Wire:** two new verbs riding the *existing* generic `/game/<verb>` path
  (`DeviceLinkAgent._on_verb` → `GameServer.data()` → `Bit.verb_handlers()`)
  — `devicelink/agent.py` needed **no change**. `/game/capture "ssb" dev
  action meta` (`open`/`close`/`abandon`; `open` declares the label and the
  device's own clock reading, `t0`, before any sample arrives — Design Rule
  4, timestamps at the source) and `/game/telemetry "sfb" dev t0 batch`
  (~100 ms batches of structure-of-arrays accel/gyro samples plus optional
  16 kHz mono PCM). Decoders live in `devicelink/protocol.py`, which remains
  the single source of truth for the wire shape.
- **`capture/trace.py`:** a pure, I/O-free `Trace` record — accumulates
  batches, detects and records sequence gaps (a skipped `seq` is kept and
  flagged; a stale/duplicate `seq` is refused, never silently corrupts
  ordering), serializes to the on-disk trace shape.
- **`capture/store.py`:** the *only* filesystem-touching code in the path.
  One write per capture, at `close` (or idle-expiry/unload-truncation) —
  never per batch, so filesystem contact stays off the hot path. Layout is
  `captures/<session-id>/<label>/<series>.json` (+ a sidecar `.wav`, never
  base64-in-JSON) plus an appended `index.jsonl`. `label` is restricted to
  `[A-Za-z0-9_-]` at the decode boundary (it becomes a path component); a
  same-`label`/`series` collision is refused at open (across devices) and
  refused-not-overwritten at write (a `self.failures` counter, never a
  silent clobber); a capture whose accumulated span exceeds its declared
  `window_ms` plus a fixed grace is force-truncated rather than growing
  memory unboundedly for a client that never sends `close`.
- **`CaptureBit`:** deliberately thin — one unscored `shared` role
  (`recorder`, node `CAPTURE_NODE`), empty light/audio manifests (the phone
  is the whole instrument), `update(dt)` never self-completes (a capture
  session ends only when the operator ends it from the console) but does
  drive `store.expire()` each tick, `on_unload()` truncates anything still
  open. `Bit.status()` surfaces live per-label counts and open captures, so
  **the Terrarium Console is a live capture dashboard with zero console-side
  changes**.
- **`harness/capture_smoke.py`:** the driver, mirroring
  `devicelink_smoke.py`'s `build()`/`main()` split; `python -m
  harness.capture_smoke --hold --host 0.0.0.0` for a real phone (same
  `127.0.0.1`-default / explicit-LAN-opt-in trust model as `devicelink/`).
- **`tools/trace_stats.py`:** an offline CLI (not part of the runtime) that
  reads a capture session directory and reports peak/deviation-from-rest
  acceleration, time-above-threshold, spike/inter-spike intervals, peak
  angular rate and integrated swept angle, and mic peak level/attack time —
  per trace and per label. This is what turns "we have data" into "we have
  definitions"; deriving the actual replacement thresholds is deferred, not
  done here.
- **`docs/telemetry-trace-schema.md`:** the cross-repo wire/on-disk contract
  a future mm-tuneshroom capture client implements against — same pattern as
  `devicelink/protocol.py` ↔ `lib/link/envelope.dart`.

Nothing here is o2lite and nothing here touches Arco: same caveat as
`devicelink/` throughout.

### `control/rooms.py`, `control/room_binding.py`, `control/room_bridge.py`, `control/boot_config.py`, `control/arco_process.py`, `control/boot.py` — the Room concept and load sequence
**Room**: a first-class, boot-time concept representing the physical (or
simulated) LED/mic/speaker hardware a Terrarium installation offers, analogous
in shape to how a `Bit` already declares per-player devices via `RoleTable`.
Landed as spec 1 of 2 toward a Terrarium Visualization Simulator (design:
[`.../2026-08-10-room-concept-and-load-sequence-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md)).
This slice defines the shape a Room backend must fill; it builds **no
renderer** — the concrete simulator/hardware backend is deferred, see
*Not yet built* below.

- **`RoomType`** (`control/rooms.py`) — an enum (`TEST`, `DEMO`), each with a
  code-defined **recipe**: which backend *capabilities* (not live device
  counts) must be available for Terrarium to resolve as that type. `TEST`
  needs only devicelink capability; `DEMO` additionally needs an array output
  backend configured. `resolve_room_type()` is boot-time, deterministic, and
  **fails hard** — there is no silent downgrade to a lesser type.
- **`RoleClass.ROOM`** (`control/roles.py`) — a fourth role class (capacity 1)
  reusing the existing Registration Node/role machinery unchanged: a Bit
  merges a `room_role()`-built `Role` (its `light_manifest`/`ugen_manifest`
  fields carry the Room's declared instruments, validated for free by the
  existing `control/role_config.py`) directly into its own `role_table`. A
  device joining that node doesn't get a normal player grant — `GameServer`
  binds it as the resolved Room's rendering backend instead. The node is
  **never surfaced** on the Console or uplink (see below) and is normally
  closed: it only accepts a join while an admin has explicitly armed a
  short-lived registration window (`RoomBindingRegistry.arm()`), which is
  stronger than plain unlisted-node obscurity.
- **`RoomBindingRegistry`** (`control/room_binding.py`) — Control-global,
  survives Bit load/unload cycles like `DevicePool` does. Tracks which device
  is bound per `RoomType`, the admin-armed window, and persists just the
  bound device ID to disk (`save()`/`load()`) — **not yet wired into `boot()`**,
  see *Not yet built*.
- **`RoomBridge`** (`control/room_bridge.py`) — the Room-scoped sibling of
  `harness/device_bridge.py`/`control/audio.py`'s `AudioBridge`: backend-
  agnostic by construction (never imports luxaeterna or pyarco), `Protocol`-
  typed light/audio sinks with fakes only. Fans a Room's MIDI stream to
  whatever sinks are bound, mirroring `led_smoke.py`'s light-and-sound-read-
  the-same-bytes pattern. Cue *routing* into it (a Bit targeting `gs.room.
  bound_dev`) is deliberately left as future harness glue — `on_light_cue` is
  already dev-generic, so no new engine sink was built ahead of a real
  consumer.
- **`ArcoProcess`** (`control/arco_process.py`) — spawns and owns the Arco
  server subprocess (previously always hand-started); polls for readiness via
  a lazy pyarco import (mirroring `harness/arco_synth.py`). Arco has **no
  message-based quit** (only a console keypress, per its own `doc/server.md`),
  so shutdown is **SIGTERM** -- the same signal `harness/signals.py`'s
  `sigterm_as_keyboard_interrupt` teaches this repo's own Python processes to
  handle, so the choice is consistent rather than arbitrary.
- **`boot()`** (`control/boot.py`) — the orchestrated load sequence: config →
  spawn/wait for Arco → resolve Room → bind Room (a Terrarium-spawned
  simulator or a reconnect to a previously recorded device, else
  `wait_for_room_binding()` holds in `SETUP` for a fresh admin-armed tap,
  reusing the `--setup-seconds` hold pattern `devicelink_smoke` already
  established) → gate the Bit on `bit_cls.room_types` (read off the class,
  before instantiation) → `load_bit`. **Structural shutdown guarantee,
  twice now.** The first version funnelled every failure after Arco starts
  through one `try/except` that called `arco.shutdown()` exactly once, so a
  future failure mode added to this section couldn't accidentally orphan
  the subprocess by forgetting a call site -- replacing an earlier,
  enumerated-call-site version that a review round caught leaking Arco on
  an `ArcoReadyTimeout`. That guarantee covered Arco and nothing else this
  function spawns, which is why the Room simulator needed its own
  separate, hand-written guard bolted on by PR #24 rather than being
  covered by the same mechanism. **As of this branch, the same
  `try/except` closes a `TeardownStack` instead**, covering Arco, any
  simulator the factory registered, the room bridge and the Bit's abort
  together, so nothing this function starts can be left out by omission
  again. See the teardown-order section below for the mechanism and the
  three-separately-maintained-orderings history behind it.
- **Console and uplink filtering.** Because a `ROOM`-class role lives in a
  Bit's normal `role_table`, the pre-existing `ConsoleAgent.snapshot()`/
  `on_registration_change()`/`_devices_view()` and `UplinkAgent`'s
  `_send_resync()`/`on_registration_change()` would otherwise leak the
  Room's role name and live occupancy to both the local admin panel and the
  outbound fairyring-bound uplink. Both are now filtered through a shared
  `control.rooms.non_room_counts()` helper (a device bound as the Room still
  appears in the Console's device list — it said hello like any other device
  — just without revealing which role it holds).

  **Narrowed 2026-08-17, and the shape of the narrowing is the point.** The
  original rule said the Room is never surfaced at all. That was written
  broader than the concern behind it, which is that the Room's Registration
  Node grants control of the installation's rendering backend. A Bit's
  declared Room *instruments* are not a credential; they are exactly what an
  operator needs to see. So the Console now shows the Room's instruments, its
  surface and zone names, and its live controller values, while the node id,
  the registration counts and the role name stay hidden and the uplink stays
  filtered unconditionally. **Implemented as addition, not relaxation:** every
  filter named above is byte-identical to before, and visibility arrives
  through a separately built `room` key. Prefer that shape if this is ever
  extended: a filter that was never loosened cannot be accidentally widened,
  and `tests/test_console_agent.py`'s
  `test_the_room_stays_hidden_from_roles_and_registration_while_visible_as_room`
  can assert both halves in one test, which a relaxed filter makes impossible
  to state. See `docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md`
  section 3.

### `control/simulator_process.py`, `harness/room_simulator.py`, `harness/terrarium_boot.py` — the Terrarium Visualization Simulator (TEST room)
The first concrete `Room` backend — closes the gap the Room-concept slice
above deliberately left open (`RoomBridge` existed but rendered nothing).
Design: [`.../2026-08-10-terrarium-visualization-simulator-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md).
TEST room only as of this slice; DEMO's own real-scale simulated backend
landed later, see the *`control/room_profile.py`'s `RoomBlock`, and the DEMO
room* section below. A real-hardware Art-Net venue array remains deferred.

- **`SimulatorProcess`** (`control/simulator_process.py`) — spawns/SIGTERM-
  shuts-down the simulator subprocess, peer to `ArcoProcess` minus a
  readiness probe (nothing blocks on the simulator's own devicelink connect).
- **`harness/room_simulator.py`** — the simulator itself: an ordinary
  devicelink client (reuses `harness/shroom_client.py`'s `ShroomClient`
  unmodified), rendering into luxaeterna's `WebSimBackend` (browser canvas)
  via a small `WebSimLeds` adapter. Sends only `/game/hello`, **never**
  `/game/join` — Room binding is already recorded, by the Terrarium-assigned
  dev id, before this process is even spawned; there is no Registration Node
  to tap for this path. As of the 2026-08-14 label slice, `build()` passes
  its own `dev` (always `sim-room` here) as `WebSimBackend`'s new `label`
  (landed in luxaeterna, `label` appended to the served page's `<title>`),
  so this canvas's browser tab reads distinctly from a player device's own —
  previously both were the identical generic title with no way to tell them
  apart at a glance.
- **`devicelink/agent.py`'s Room wiring** — `DeviceLinkAgent` now builds a
  real `LightSession` (via the loaded Bit's `room_role_name()`-declared
  Role, the same `compose_role_config`/`LightManifest.from_dict`/
  `build_session` pipeline every per-role device already uses) and a real
  Arco voice (an injected `AudioBridge`, never constructed by this file — no
  pyarco import here, keeping it offline-testable) at construction time,
  renders the Room each tick via a new `_render_room()` step, and starts/
  stops the Room's Arco drone on Bit `RUNNING`/`UNLOADING`. **Deliberate
  deviation from the design spec's prose:** cue routing (a Room-bound dev's
  cue → `RoomBridge.feed_midi(...)`) lives in `DeviceLinkAgent._on_light_cue`
  — a transport-owned sink — rather than in `GameServer`'s cue dispatch as
  the spec described; same approved behavior, zero `control/engine.py`
  changes, and matches boundary rule 3 ("the transport only delivers it to
  that device's renderer") more literally than the original prose did.
- **`harness/terrarium_boot.py`** — the real, runnable end-to-end driver.
  Constructs `DeviceLinkServer` and starts it **before** calling `boot()`,
  deliberately: `boot()`'s `simulator_factory` spawns the simulator
  subprocess, which connects immediately, so the server must already be
  listening or the connection races server construction. **Teardown is a
  `TeardownStack`, not `control.boot.shutdown()`** (that function was
  deleted -- see the teardown-order section below for why). The real unwind
  is reverse of registration order, and the devicelink server and the
  o2lite transport never appear in the same run, so the order differs by
  mode. Websocket mode: the Bit, then the Room bridge (which frees the
  Room's Arco voice), then the Room simulator subprocess, then Arco, then
  the devicelink server itself. O2lite mode has no devicelink server to
  unwind; in its place, the o2lite transport unwinds first, then the same
  Bit, Room bridge, Room simulator, Arco order -- corrected here in both
  mechanism and order from what this line used to say.
- **The gap that survived this slice.** Every seam above is real and tested
  — but nothing in `TestBit`'s gameplay logic ever emits a cue *targeting*
  `gs.room.bound_dev`: `Bit.update(dt)` has no cue-emission mechanism at all
  (only a completion bool), and `TestBit`'s verb handlers always address the
  calling player device, never the Room. Practical effect: during a real
  run, the Room's declared `aurora` light glides to its static hue once and
  holds there for the whole run — no animation, no fade on completion.
  Audio is unaffected (the drone genuinely starts/stops). Documented at the
  call site (`bits/test_bit.py`, near the Room declaration) and in
  `devicelink/agent.py`'s `_setup_room()`; closing it for real means
  extending the `Bit` interface to let `update()` (or something like it)
  emit cues, which is out of scope here — see *Not yet built* below.

### `devicelink/o2_transport.py`, `control/timed_queue.py`, `harness/o2_shroom.py` — Control on o2lite, and timed cues
Control becomes a real O2 participant, and a cue gains a time. Design:
[`.../2026-08-12-control-o2lite-and-timed-cues-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-12-control-o2lite-and-timed-cues-design.md).

- **`O2LiteTransport`** (`devicelink/o2_transport.py`) — Control's `game`
  service on the Arco hub. It satisfies the same small interface
  `DeviceLinkServer` does (`drain_new_clients` / `drain_inbound` / `send` /
  `bind_dev` / `drop_dev`), so `DeviceLinkAgent` was unchanged by the swap:
  Slice 2's server/agent split paid off exactly as intended. It **never
  imports o2litepy**, at module level or anywhere; the caller passes an
  already-connected object into `start()`, and a local `Blob` class
  duck-types `O2blob` (`_add_blob` reads only `.size`/`.data`) so the module
  imports fine with no o2litepy present.
  Four o2litepy facts are encoded here because each one breaks the transport
  at runtime otherwise: a handler takes **three** parameters
  `(address, types, info)` with arguments **pulled** off the o2lite object in
  typespec order, not handed over as a list; the delivered address has its
  leading `/` **already stripped** (`O2lite_handler.__init__` does
  `address[1:]`), so the transport re-prefixes it; blobs must be objects with
  `.size`/`.data`, so a bare 36-int LED list raises `AttributeError` on the
  wire; and `set_services` **replaces rather than appends**, over a
  comma-separated string. That last one is why Control writes the whole
  `"actl,game"` string: pyarco already claimed `actl` on the shared
  connection, and writing just `"game"` would silently drop it and stop
  Arco's control replies. **Control is a guest on pyarco's connection**, not
  the owner of its own.
- **`TimedQueue`** (`control/timed_queue.py`) — holds `(when, payload)` and
  releases at the tick covering `when`. Payload-generic on purpose, because
  the two consumers hold different things: Control holds `(when, midi)` and
  feeds a `LightSession`, while a device holds `(when, frame)` and lights its
  LEDs. `when=None` means "no time declared", releases next drain, and
  deliberately does **not** count as a clamp; a `when` already past does.
  Sequence-numbered so `sort()` can never fall through to comparing two
  payloads, which are unorderable on one side.
- **`LightCue`** (`control/cues.py`) — a cue carrying an absolute O2 time,
  sibling to `PlayCue`. Plain 4-tuples still work and mean "apply on
  arrival", so every Bit written before it keeps running unchanged.
- **`harness/o2_shroom.py`** — a simulated Tuneshroom over real o2lite,
  rendering to a browser canvas. `--no-join` makes it serve as the Room
  simulator too (hello, never join), which is why `terrarium_boot`'s o2lite
  mode spawns this one file rather than a second near-copy. Same label slice
  as `room_simulator.py`: `build()` passes its own `dev` through unconditionally
  as `WebSimBackend`'s `label`, so the same one line covers both roles this file
  plays — the Room path (`dev="sim-room"`, `--no-join`) and a real player
  device's own canvas (`dev` is that device's id) — with no `--no-join`
  branching needed, since `dev` already differs between them.
- **`harness/sync_bench.py`** — reduces measured deltas to mean, p95, **p99**
  and worst, using absolute values so an early frame cannot cancel a late one
  into a flattering zero. p99 is the design point for `cue_horizon`
  specifically: the horizon is fixed added latency on *every* cue, so sizing
  it to the worst frame ever seen makes every gesture in the room pay for one
  hiccup, while the clamp counter reports the ~1-in-100 it does not cover.
  It has a `main()` as of 2026-08-14 (`python -m harness.sync_bench
  SAMPLES.json --offset <horizon>`); before that, following
  `terrarium_boot --horizon`'s own advice to run it simply failed.
  **Callers measuring one-directional latency must convert to absolute
  latency before calling `summarise()`** — it absolutises, which is right for
  a two-sided agreement error and wrong for latency, where it would report a
  frame arriving a healthy 80 ms early as 80 ms of error. Every figure it
  produces is a **dev-box figure**; see *Host platform*.
- **`harness/terrarium_boot.py --transport o2lite`** — opt-in, because it
  needs a running Arco. `--setup-seconds` holds the Bit in SETUP so a device
  can join a **scored** role before `run()` closes registration for it
  (`control/registration.py:41-42`); without it a Tuneshroom joining
  `TEST_PLAYER_NODE` is denied every time. The driver now exits when the Bit
  declares itself done, but keeps polling until released devices finish
  their closing fade, because release is asynchronous and exiting at IDLE
  would freeze every device on its last frame.

**Two bugs that only a live run could find, both now fixed.** Neither was
visible to 611 passing tests, an eleven-task review chain, or a whole-branch
review, and both left the path completely dark rather than degraded:

1. **Two different clock bases.** Control stamped frames with
   `time.monotonic` while the device ticked on the O2 clock. On one machine
   those read roughly 518,000 and 45, so every frame was queued for a time
   half a million seconds out and none ever displayed. Fixed by threading
   `o2lite.time_get` into `DeviceLinkAgent` on the o2lite path
   (`harness/terrarium_boot.py`'s `main()`), so both ends measure the same
   thing. The websocket path keeps `time.monotonic`, which is correct there
   because both processes are local.
2. **Nothing pumped o2lite.** o2litepy dispatches inbound messages to
   handlers **only** from inside `o2lite.poll()`, and `O2LiteTransport`
   never called it, so `drain_inbound()` faithfully returned an empty list
   forever and Control never saw a single `/game/*` message. Fixed by
   pumping in `drain_inbound()`.

**Why the tests could not catch either, and the rule that follows.** The
second one is the instructive case: `FakeO2Lite.deliver()` invoked the
handler directly and its `poll()` was a no-op, so every test dispatched
messages the real library would only dispatch on a pump. The fake and the
tests agreed with each other and both disagreed with o2litepy. The fake now
enqueues and dispatches only on `poll()`, so a transport that forgets to
pump cannot pass its own tests. Treat that as a general rule when writing
any double in this repo: **a test double must never be more permissive than
the library it stands for**, because the dimension nobody thought to check
is exactly where the real thing will differ.

**A third bug, found in a later investigation (2026-08-14): an orphaned Room
simulator steals the next run's `sim-room` service, and O2's refusal is
invisible to the client.** `harness/o2_shroom.py --dev sim-room --no-join`
never exits on its own -- it loops `while not client.released`
(`harness/o2_shroom.py:283`) and only a live Control ever sends `/release` --
so a `Popen` child that outlives a killed parent (a `SIGKILL` on
`terrarium_boot`, say) becomes a permanent orphan. o2litepy reconnects it
automatically to whatever Arco starts next and re-announces every service on
that connect, re-claiming `sim-room` before the new run's own simulator is
even spawned. O2 then refuses the new simulator's announcement
(`o2/src/bridge.cpp:231-237`: a bridge cannot coopt an existing local
service), and because `/_o2/*/sv` is fire-and-forget the refusal is a log
line on the **hub** only -- the refused simulator clock-syncs and looks
exactly as healthy as the one that won, while every frame the new run
addresses to `/sim-room/leds` is delivered to the zombie instead. Three
guards now close this: `harness/o2_shroom.py --exit-with-parent`
(`harness/o2_shroom.py:241,284`) checks the parent pid recorded at launch
inside both blocking loops, so a killed parent is detected without ever
needing `/release` -- `harness/run_stack.py` now passes the identical flag
to every player device it spawns, not just the Room simulator, **and
`harness/terrarium_boot.py` carries the same flag now too**, reusing
`o2_shroom`'s `parent_is_gone` predicate rather than a second copy and
checking it in both `_wait_in_setup` and `_serve_until_done` so the exit
runs the normal `finally: shutdown(teardown)` path. That last one closes
the orphan path the other two cannot: `run_stack` SIGTERMs and Ctrl-Cs are
already handled, but a **SIGKILLed or OOM-killed `run_stack`** would
otherwise leave `terrarium_boot` alive and, with it, Arco and the Room
simulator, un-signalled because they sit in their own session
(`start_new_session=True`). Default off, so a hand-run `terrarium_boot` is
unchanged; `control/boot.py`'s
`boot()` and `harness/terrarium_boot.py`'s `build()` now shut the simulator
(and everything else spawned so far) down on any failure,
`KeyboardInterrupt` included, via the guarded `TeardownStack` described
below -- which replaced the four hand-written `except Exception: pass`
guards this paragraph used to describe one by one; and
`verify_service_ownership` (`devicelink/o2_transport.py:119`) sends a
self-addressed round trip before the tick loop starts, so a refused
announcement fails loud instead of silently. Design:
`docs/superpowers/specs/2026-08-14-room-simulator-service-collision-design.md`.

**The gap that survived this slice was closed 2026-08-14.** All of the above
was built and unit-tested with nothing driving it end to end; a follow-up
slice (design:
[`.../2026-08-14-load-bearing-timed-cues-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-14-load-bearing-timed-cues-design.md))
closed it. `GameServer.data()` now computes `at = origin + cue_horizon` once
and hands a verb handler `handler(dev, args, at)`; `control/cues.py` gained a
`ROOM` sentinel a Bit uses to target the Room without holding its runtime
device id; `Bit` gained a `cues(at)` hook for self-driven cues, called once
per RUNNING tick. `AudioBridge.feed_midi(when=...)` and
`ArcoSynthPool.schedule_at` are gone rather than wired up: pyarco's scheduler
polls on the same 44 Hz tick `TimedQueue` already does, so splitting light
and audio bought no accuracy, and pyarco's `cause()` raises on a past time
where `TimedQueue` clamps, which would have raised on the majority of cues
given the horizon figure below. `TestBit`'s `tilt` now emits a cue for the
calling device and a `ROOM`-targeted one from a single gesture, and its
`cues(at)` drifts the Room's hue on its own. Live-verified against a real
Arco 2026-08-14: gesture-driven Room light+drone confirmed, ambient drift
with nobody joined confirmed. This run's ~1000-frame-scale device clamp
counts are the same clamp-counter saturation artifact a separate, same-day
measurement identified on the o2lite path -- see *`cue_horizon` is measured
as of 2026-08-14* under *Not yet built* -- not evidence the horizon needs
retuning. See the *Control on o2lite, and timed cues* status callout above.

<!-- diagram:cue-path GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
           ┌───────────────┐                                 
           │Device gesture │                                 
           │               │                                 
           └───────────────┘                                 
                   │                                         
           raw gesture stamp                                 
                   │                                         
                   ▼                                         
┌─────────────────────────────────────────────┐              
│GameServer.data(): at = origin + cue_horizon │              
│                                             │              
└─────────────────────────────────────────────┘              
                   │                                         
     at, never the horizon itself                            
                   │                                         
                   ▼                                         
            ┌────────────┐ ┌─────────────┐                   
            │Bit handler │ │Bit.cues(at) │                   
            │            │ │             │                   
            └────────────┘ └─────────────┘                   
                      │       │                              
                      ▼       ▼                              
              ┌──────────────────────────┐                   
              │GameServer._dispatch_cues │                   
              │                          │                   
              └──────────────────────────┘                   
                          │                                  
         ROOM resolved to bound fixture devs                 
                          │                                  
                          ▼                                  
   ┌────────────────────────────────────────────────────────┐
   │on_light_cue (transport-owned sink): push to TimedQueue │
   │                                                        │
   └────────────────────────────────────────────────────────┘
              │                       │                      
           on time                 on time                   
              │                       │                      
              ▼                       ▼                      
    ┌─────────────────────┌─────────────────────────┐        
    │Device renders frame │Room renders light+drone │        
    │                     │                         │        
    └─────────────────────└─────────────────────────┘        
```
<!-- /diagram:cue-path -->

### `control/device_pool.py`, `control/engine.py`'s `reap_stale`, and the harness heartbeat clients -- device liveness detection
Closes the "stale device entry survives an ungraceful disconnect" gap.
Design: [`.../2026-08-25-device-liveness-detection-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md).

- **`DevicePool`** gained `last_seen` per device, updated by
  `DeviceLinkAgent._handle()` on every inbound message (not just hello --
  a device mid-gesture-stream is obviously alive) plus `touch()`/
  `stale()`/`remove()`. `stale()` is a pure query; nothing removes an
  entry except the reaper below.
- **`GameServer.reap_stale(timeout)`**, called every tick from
  `DeviceLinkAgent.poll()` (the one loop that already runs unconditionally
  across every engine state, including the SETUP-hold wait). A stale
  device that held a role has its slot freed synchronously via
  `RegistrationState.release()` before the existing `on_release` sink
  fires -- a new player can join the freed slot immediately, without
  waiting for the departed device's closing fade to finish playing out.
  Room-bound devices are skipped entirely: `RoomBridge`/`AudioBridge`
  keep feeding whatever fixture-to-dev binding `RoomBindingRegistry` still
  holds, which is deliberate -- see *Not yet built* below.
- **`drop_dev()`**, defined on both `DeviceLinkServer` and
  `O2LiteTransport` since PR #20 (o2lite) but called from **nowhere**
  until now -- not even by a graceful Bit-unload release -- is now wired
  into both `_finish_release` (the faded-release path) and `_on_release`'s
  no-bridge early return (the immediate-release path, e.g. a device whose
  `on_grant` failed). **Guarded against a reconnect race the whole-branch
  review caught before merge, not while a task was in flight**: a device
  that sends a fresh message (a heartbeat, or a hello-only reconnect --
  exactly how the Room simulator behaves) while a PRIOR release's closing
  fade is still finishing must not have that stale fade's later
  `_finish_release` call `drop_dev` on its just-re-established connection.
  `_handle()` marks the dev revived on any inbound traffic while it is
  still in `_closing`, and `_finish_release` skips `drop_dev` (but still
  tears down the old bridge/universe/frame/breath state, which genuinely
  is finished) when that mark is set -- mirroring the existing precedent
  `_on_join` already set for the equivalent rejoin-mid-fade race.
- **The heartbeat itself is `/game/hello`, resent, not a new verb.**
  `harness/o2_shroom.py --heartbeat-interval` (default 5s; 0 disables) and
  `harness/room_simulator.py --heartbeat-interval` both gained the resend.
  `mm-tuneshroom`'s Dart client has not, yet -- the real-hardware path
  stays open until that cross-repo change lands, same relationship
  `devicelink/protocol.py`'s docstring already documents for its Dart
  counterpart contract.
- `harness/terrarium_boot.py`'s `_LifecycleLogger` gained a "device timed
  out: `<dev>`" line, unambiguous by construction: `reap_stale` is the
  only thing that ever removes a `DevicePool` entry, so a dev leaving
  `gs.devices.all()` between ticks can only mean this. A reaped device
  that held a role prints **both** lines -- "device released" from the
  assignments diff and "device timed out" from the devices diff -- which
  depends on `reap_stale` notifying `on_devices_change` **before**
  `on_registration_change`: the logger's registration-change handler
  overwrites the assignments snapshot the devices-change handler's release
  diff reads, so the wrong order silently drops the "released" line. Also
  caught by the whole-branch review, not a task review -- the only
  existing test for this logger exercised an un-joined device, where
  `on_registration_change` never fires at all.

### `control/teardown.py`, `control/process.py`, `harness/signals.py`, `harness/markers.py`, `harness/run_stack.py` -- teardown order, structurally, and a one-command Arco stack runner
Closes the ordering gap the previous section's "third bug" investigation
left open: every individual guard there was correct and the ordering they
composed into was still wrong on the path that matters most. Design:
[`.../2026-08-14-teardown-order-and-stack-runner-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-14-teardown-order-and-stack-runner-design.md).

- **`TeardownStack`** (`control/teardown.py`) -- a guarded, idempotent, LIFO
  stack of named teardown steps. The invariant, stated plainly because it is
  the whole point: **anything registered later is torn down earlier**.
<!-- diagram:boot-teardown GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
  ┌──────────────────────────────────────────┐  
  │1. devicelink server (pop 5th, no o2lite) │  
  │                                          │  
  └──────────────────────────────────────────┘  
                   │                            
                   ▼                            
          ┌──────────────────┐                  
          │2. Arco (pop 4th) │                  
          │                  │                  
          └──────────────────┘                  
                   │                            
                   ▼                            
┌──────────────────────────────────────────────┐
│3. Room simulators, one per fixture (pop 3rd) │
│                                              │
└──────────────────────────────────────────────┘
                   │                            
                   ▼                            
       ┌─────────────────────────┐              
       │4. Room bridge (pop 2nd) │              
       │                         │              
       └─────────────────────────┘              
                   │                            
                   ▼                            
           ┌─────────────────┐                  
           │5. Bit (pop 1st) │                  
           │                 │                  
           └─────────────────┘                  
```
<!-- /diagram:boot-teardown -->
  Client-before-hub stops being an ordering someone maintains and becomes a
  consequence of *when* things start -- the devicelink server is pushed
  before `boot()` runs and therefore stops last; Arco is pushed at spawn;
  the Room simulator is pushed after Arco and therefore stops before it.
  The o2lite transport (only present on the o2lite path, where there is no
  devicelink server at all) is the cleanest illustration of the same
  invariant working the other way: `main()` pushes it after `build()`
  returns, which is after Arco, the simulator, the Room bridge and the Bit
  are already registered, so it is registered last and **stops first** --
  before the Arco hub it is a guest on. Each step is guarded (`close()` catches
  `BaseException`, not `Exception` -- a second Ctrl-C landing inside a
  subprocess wait must not abandon the remaining steps) and `close()` is
  idempotent, so a failure path and the caller's normal teardown can both
  call it without coordinating.
- **`control.boot.shutdown()` was deleted, not reordered.** Its docstring
  said Arco goes last "since everything else may still want to address it
  during teardown," which was correct *within `boot.py`'s own scope* --
  and wrong composed with `harness/terrarium_boot.py`, which owns o2lite
  **client** subprocesses (the Room simulator, and on the o2lite path the
  transport itself) that talk to that same hub. `boot()` now returns the
  stack instead of a fixed shutdown order, and also *accepts* one, so a
  caller that starts something before `boot()` -- the devicelink server --
  registers it first and gets it torn down last. Reverse-of-registration is
  correct in both scopes without either module knowing about the other.
- **`stop_process`** (`control/process.py`) -- the bounded signal/escalate/
  reap cycle every spawned subprocess in this repo now shares: SIGTERM, poll
  to a timeout, escalate to SIGKILL, poll again, reap. Before this,
  `ArcoProcess.shutdown()` and `SimulatorProcess.shutdown()` both called
  `Popen.wait()` with no timeout, and the Room simulator is always a plain
  `Popen`, so a client that ignored or was slow to handle its stop signal
  hung teardown indefinitely. It polls rather than calling
  `Popen.wait(timeout=)` because `_PtyProcess.poll()` drains the pty, and an
  undrained pty blocks Arco's curses app on its own screen writes.

**The hard-won fact this exists to fix.** mm-terrarium had **three
separately-maintained teardown orderings**: `control/boot.py`'s failure
handler, `harness/terrarium_boot.py`'s `build()` failure handler, and
`harness/terrarium_boot.py`'s success-path `shutdown()`. PR #24 corrected the
first two -- both failure paths -- and did not notice the third. So on
**every successful run**, the one that matters most, the O2 hub was being
killed before the o2lite clients still talking to it, and the Room simulator
spent its last moments writing to a dead socket. That is the argument for
making this structural rather than fixing it a third time by hand: nothing
prevented the ordering from disagreeing with itself again, and it had.

- **`harness/run_stack.py`** -- `python -m harness.run_stack` brings up the
  whole Arco stack (Arco, Control, the Room simulator, and N player devices)
  from one command, replacing a two-terminal, right-order-or-lose-your-output
  workflow. It waits on real readiness markers rather than sleeping, tees
  each child's stdout to its own log under `runs/<timestamp>/` (`.gitignore`d
  alongside `captures/` and `o2debug.log`), and tears down device-first:
  every process is registered on the same `TeardownStack` at the moment it
  is spawned, so the ordering guarantee above extends to it for free.
  `--ci` mode bounds the run with `--seconds` (default 45s) and turns off
  echo; either mode exits non-zero on any unmet marker **or on a child that
  exits during the hold**, and a failure prints the stage that failed, the
  process, its log path, and the log's tail. One deliberate exception
  (2026-08-21): a `control` child that exits **zero** after emitting
  `CONTROL_BIT_COMPLETED` ("Bit completed; tearing down") is a
  self-completing Bit ending the run on its own -- MetronomeBit does this,
  TestBit under `--hold` never does -- and the run exits 0 with stage
  `bit-completed`; a markerless or nonzero exit keeps the `child-exited`
  failure diagnosis.

  **`--open` (2026-08-20) makes it the one-command simulator test
  environment.** Every browser-facing surface -- the Terrarium Console,
  each Room fixture canvas, each simulated Tuneshroom canvas -- prints its
  URL behind a new `markers.BROWSE_URL` prefix, and `run_stack` collects
  each one as it appears (a `ProcTee(on_line=...)` hook, readiness-driven,
  not sleep-and-guess) and opens it in the default browser via
  **Flutter simulators ride the same harvester:** `--flutter-sim PATH
  --flutter-devices N` spawns mm-tuneshroom's `PATH/tool/sim serve --devices
  N --link ws://127.0.0.1:8771/ws --no-open` as one more child after
  Control reports SETUP. It prints one `BROWSE_URL:` line per device (the
  colon is the contract -- `markers.BROWSE_URL`), is a websocket client
  rather than o2lite so it is **excluded** from the `DEVICE_CLOCK_SYNCED`
  wait, and its clean exit never fails a hold in any mode.
  `webbrowser.open`. `--open` implies `--console-port 0` when no port was
  given (`ConsoleServer` already binds an ephemeral port and
  `terrarium_boot` prints the real URL, so the implied Console cannot
  collide); `--ci --open` is refused at argument parsing. Without
  `--open`, behavior is unchanged except the collected URLs are echoed,
  one labelled line each, in the success summary. The Room simulators'
  URLs arrive on Control's own tee because `SimulatorProcess` spawns them
  with inherited stdout; each player device's URL arrives on its own tee.
  **Room fixture canvases are never opened, under `--open` or otherwise**:
  they arrive as `ROOM_URL` lines, not `BROWSE_URL`, so `--open` only ever
  auto-opens the Console and each simulated device; a Room surface is
  echoed in the summary as `room surface (open from the Console): <url>`
  and reached from the Room card's own pop-out links instead, one per
  bound fixture.

  That second condition is worth stating separately because its absence was
  the one real correctness gap the whole-branch review found. `_hold()`
  originally took the Control tee and never read it, and nothing polled any
  child's exit status, so an unattended `--ci` run in which Control (and
  therefore Arco and every device under it) died seconds into a 45 s hold
  still exited **0**. It now polls every child each tick, mirroring
  `terrarium_boot`'s own `arco.poll() is not None` pattern, and reports a
  `child-exited` stage naming which child died and its code.

  **CI mode works as of 2026-08-20.** The "headless clock-sync defect"
  this paragraph used to disclaim turned out to be mm-terrarium's own
  SETUP-hold loop starving Arco's pty (see the operator/harness handoff
  slice below): spawned devices always synced into a frozen hold, which is
  why headless failed while interactive sometimes squeaked through. With
  the hold draining, `run_stack --ci --devices 1` produced the repo's
  first green end-to-end run: clock sync at O2 12.5 s, role granted on the
  first join, clean exit, zero orphans (runs/20260820-161425). The
  `device-sync` failure stage remains as a guard, not a disclaimer.

  **Exercised against a live Arco for the first time on 2026-08-14.** Three
  runs, one device each, via `--ci --seconds N --devices 1`. No run reached
  a granted role: one SETUP-window `JOIN DENIED` (fixed on this branch) and
  two clock-sync failures (upstream, not fixed). This does not make the
  runner work end to end -- it has not produced a green run. What did hold,
  none of it previously exercised outside fakes: teardown reaped every
  process, all three times, zero orphans, including on the two failing runs
  -- the primary claim of the teardown work. Arco spawned on a pty and came
  up with audio (`Audio open completed successfully`, `Audio latency = 10
  ms`); `--arco-log` captured its curses output, which is what made the
  failures diagnosable at all. `o2debug.log` recorded zero dropped messages
  and zero service-provider refusals across all three runs. CI mode bounded
  and named both failure modes and exited non-zero rather than hanging, as
  designed. **Postscript, 2026-08-20:** those two "upstream" clock-sync
  failures were the pty starvation above; with it fixed, the same command
  runs green.
- **`harness/markers.py`** -- the readiness contract `run_stack` watches
  for: named constants emitted by `terrarium_boot`/`o2_shroom` and matched
  on both sides by `tests/test_markers.py`. Matching on incidental print
  wording would make a future reworded line a silent hang instead of a
  broken test; promoting the strings to constants is what makes
  stdout-watching honest. Failure markers (`JOIN DENIED:`, `FATAL: service`)
  are matched too, so a failure the child has already diagnosed ends the run
  immediately rather than sitting out the full timeout. A third kind,
  `BROWSE_URL` (2026-08-20), marks a line carrying a URL worth a browser
  tab; it is collected rather than waited on (a run has a variable number
  of them), so it lives outside both marker dicts, emitted by
  `terrarium_boot`, `room_simulator`, and `o2_shroom` and pinned to all
  three emit sites by `tests/test_markers.py`. `ROOM_URL` is the sibling
  marker for a URL worth knowing but not worth an automatic tab: a Room
  fixture canvas, reached from the Console's Room card instead. `run_stack`
  collects and echoes `ROOM_URL` lines the same way, but never hands them to
  the opener.
- **`harness/signals.py`** -- one copy of the SIGTERM-skips-`finally`
  gotcha: Python's `finally` blocks do not run on a bare SIGTERM, so a
  process whose cleanup (an exit report, a `WebSimBackend.close()`) lives in
  one loses it the instant a supervisor signals it rather than asking. This
  lived as an identical six-line copy in `harness/led_smoke.py` and
  `harness/room_simulator.py`; `harness/o2_shroom.py` (SIGTERM'd by
  `SimulatorProcess`), `harness/terrarium_boot.py` (SIGTERM'd by
  `run_stack`), and `run_stack.py` itself (guarding its own
  `finally: teardown.close()` against a bare `kill <pid>` or a CI job's
  timeout) now install it too, so there is one home for the gotcha
  instead of five copies that could each drift independently.

**Suite baseline as of this slice: 721 passed, 1 skipped** (662 at this
branch's start). **844 passed, 1 skipped as of the 2026-08-17 Room-panel
slice; 933 passed, 1 skipped as of the trigger slice that follows it; 1037
passed, 1 skipped as of the N-fixture Room slice ; 1076 passed, 1 skipped
as of the wire-JSON and Console-script-isolation slice; 1099 passed, 1
skipped as of the operator/harness handoff slice; 1254 passed, 1 skipped
as of the Bit packaging and launch slice; 1267 passed, 1 skipped as of
the console-operator-rounds slice; 1284 passed, 1 skipped as of the
per-round device respawn slice.**

### The Room panel and the Room's own fixtures (`control/room_profile.py`, `control/room_view.py`, `harness/room_surface.py`, `console/static/`)
The Room stops being shaped like a Tuneshroom, and the Console becomes an
operator surface for it. Design:
[`.../2026-08-17-room-panel-and-room-fixtures-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md)
(Spec A of two; Spec B covers triggers, cue scripts, conditions and firing,
see the Design docs list below).

- **The Room had no fixtures of its own.** `devicelink/agent.py` built the
  Room's `LightSession` from `self._capability or shroom_capability()` and
  sliced its frame with a literal `[:36]`, so structurally a Room *was* a
  12-LED Tuneshroom with a ring and a stem. `control/room_profile.py` now
  declares its own surface: `RoomProfile(surface_id="room_test",
  pixel_count=60, color_order="GRB", zones=(left/center/right, 20 px each))`,
  linear because the real Terrarium array is a single 6 m run. `channel_count`
  (180) is the single source of truth for frame width, honored by the agent's
  slice, the client's `expected_channels`, and both simulator entry points.
  A mismatch renders **nothing at all**, because a wrong-width frame is
  dropped rather than truncated.
- **`control/` stays dependency-free, and that is now pinned by a test.**
  `room_profile.py` is pure; `harness/room_surface.py`'s `to_capability()`
  does the luxaeterna conversion, mirroring how `harness/device_bridge.py`
  already adapts a player role's declaration. The `primary` zone lives only in
  the adapter: the renderer needs it to resolve an untargeted instrument, and
  the Console must not draw it, since it spans the whole surface and would
  cover every real zone. **The invariant is module-level imports only**: one
  deliberate function-scoped `from pyarco.arco_engine import arco` exists at
  `control/arco_process.py:37`, marked `# noqa: PLC0415 (lazy by design)`,
  and is fine because a function-scoped import runs only when called. An
  earlier draft of this work asserted a package-wide ban and was wrong; the
  grep behind it was anchored at `^` and could not see an indented import.
- **`control/room_view.py`** is the read model the browser renders: light and
  audio instruments in **one list discriminated by `kind`**, not two. They are
  declared in two fields of one `Role` and fed from one shared MIDI stream, so
  two separate tables would hide the property the architecture is built
  around. On the panel this shows as `cc:74` reading the same value on
  `aurora`'s hue lane and FluidSynth's cutoff lane simultaneously.
- **Frame relay.** `DeviceLinkAgent` gained an optional `on_room_frame` sink,
  guarded at its call site exactly like `on_release` and `on_light_cue`, wired
  by constructor injection so `control/engine.py` was not touched.
  `ConsoleAgent` holds only the **latest** frame and broadcasts at ~10 Hz
  against the 44 Hz render; intermediate frames are **dropped, never queued**.
  That is what keeps it compatible with boundary rule 2: nothing is
  retransmitted, nothing is awaited, and dropping every frame degrades the
  picture and changes nothing else.
- **`console/static/` is a directory now**, split into `index.html` /
  `style.css` / `console.js` / `room.js` and served from an allowlisted asset
  map, still with **no build step** (a venue box must never need npm).
  (This split was later superseded by the ES-module front-end rewrite — see
  the dated section near the end of this file for the shipped six-module
  layout.) Path
  handling takes the request's **basename only**, so the server has no code
  path that touches the filesystem after construction and no request can
  escape `console/static/` or the extension allowlist.
- **`--console-port`** on `harness/terrarium_boot.py` and
  `harness/run_stack.py`, off by default. Before this the Console was
  **unreachable during a live run**: `ConsoleServer`/`ConsoleAgent` were
  constructed only under `tests/`. `main()` owns the console rather than
  `build()`, because `build()`'s 5-tuple return is unpacked at 17 sites; that
  also gets the teardown ordering right, since the console is registered last
  and therefore torn down first, correct because its only clients are browsers
  outside the stack, unlike the devicelink server whose client is the Room
  simulator.

**Two defects here reached a live browser run past 843 passing tests, and the
reason is worth internalizing.** The console's JavaScript was covered only by
substring greps over its own source. `renderRoom` replaced the `#roomStrip`
node on every `room_changed`, and `room_changed` fires on every controller
change, so the strip was rebuilt roughly four times per painted frame
(measured 1726 against 464) and the Room's live light was effectively never
displayed *while the room was active*. Separately `body` had `color: #111`
with no `background-color`, so under `prefers-color-scheme: dark` the whole
Console rendered near-black on black. Both are fixed, and `room.js` plus
`console.js`'s event dispatch now have real behavioral tests
(`tests/js/room_panel_behavior.test.js` under a DOM stub via Node's `vm`,
wrapped by `tests/test_room_panel_behavior.py`, skipping cleanly where node is
absent). **A grep over source text is not a test of behavior**; if more
browser code lands here, give it the same treatment.

### `control/triggers.py`, `control/trigger_view.py`, `console/static/triggers.js` -- Bit-declared triggers, cue scripts and conditions
A Bit can now say what will make something happen, not only what it is doing
now. Design:
[`.../2026-08-17-bit-declared-triggers-and-cue-scripts-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-17-bit-declared-triggers-and-cue-scripts-design.md)
(Spec B of two; Spec A is the Room panel above).

- **The declaration.** `control/triggers.py` holds `Trigger` (name,
  description, target, condition, script), `Condition` (name, description,
  source -- gesture-verb / bit-adjudicated / admin-manual -- and, for a
  gesture-verb condition, the verb it names), and `ScriptStep` (an offset in
  seconds plus a plain MIDI-shaped cue or a `PlayCue`). `TriggerTable` is
  declared alongside `RoleTable`. Validated at `load_bit`, in the same
  location and the same shallow-structural style
  `control/role_config.py` already established: a trigger whose condition
  names a verb the Bit's own `verb_handlers()` does not implement fails there
  as a `BitLoadError`, never as a surprise mid-installation.
- **Firing costs no new scheduler.** `GameServer.fire_trigger` resolves the
  trigger's target to one or more devs (`_resolve_target`, deliberately
  list-returning even though the Room resolves to at most one today -- see
  the Spec C entry below), expands the script into concrete, timed cues
  (`expand_script`), and hands them to the pre-existing `_dispatch_cues`. That
  method already knows how to stamp a cue with an absolute `when` and route it
  through `on_light_cue`, and `DeviceLinkAgent._on_light_cue` already holds a
  cue whose `when` is further out than one horizon on its `_light_cues` queue
  (see the 2026-08-14 load-bearing-timed-cues slice above) -- so a trigger's
  script rides machinery that already existed and was already live-verified,
  rather than a new one. A `FireTrigger` cue is how a Bit reports a fire, from
  a verb handler or from `cues(at)`, so it inherits that call's single
  presentation time; `GameServer.fire_trigger` guards its own body end to end
  (not only the `Bit.trigger_table` property read) so a Bit whose declaration
  changes shape between `load_bit` and a later fire is refused, not a crash --
  this was the one fix round in the slice's own implementation plan.
- **A fire is observed, never decided, by Control.** `_notify("on_trigger_fired",
  record)` rides the engine's existing multi-observer list (the same one
  `on_state_change`/`on_registration_change`/`on_devices_change` already use),
  not a transport-owned sink: the record has no device destination to
  deliver, and it is exactly the shape of event a future uplink observer will
  want. `TriggerFired` carries `fired_by` (what actually fired it this time)
  and `declared_source` (what the condition declares) as separate fields, so
  an operator manually firing a gesture-verb trigger from the Console is
  never mistakable for a real gameplay event in the record or in the log.
- **The Console gained a Triggers panel.** One card per declared trigger,
  showing its description, its condition, and its script's actual steps --
  not a prose summary -- plus a Fire button (with a device picker for a
  `DEVICE`-target trigger). A `trigger_fired` event updates only that one
  card's status line; the panel does not rebuild on every fire, the same
  discipline the Room panel needed retroactively after its own strip-rebuild
  defect (see above). `tests/js/triggers_and_rail.test.js` asserts this
  directly: the card list survives a `trigger_fired` re-render with its
  children intact. (This paragraph used to name a
  `trigger_panel_behavior.test.js` that never existed; the assertion always
  lived in the file named here.)
- **`TestBit` declares two reference triggers.** *(Superseded 2026-08-26:
  four SURFACE-target operator triggers now — see the SolidCue/SURFACE slice
  below. Read this entry as history of the two-trigger first slice.)*
  `play_aurora`
  (bit-adjudicated, latched after three full-deflection tilts, targets the
  Room) and `flash_device` (gesture-verb on the existing `tap` handler,
  targets the firing device) -- one per fire source, so both paths are
  exercised through the full engine by the suite's own reference fixture, not
  only by isolated unit Bits.
- **Live-verified against a real Arco: DONE 2026-08-20**, per the spec's
  section 13.1, and the attempt is what surfaced the two Console defects the
  wire-JSON slice below fixes -- the first try found every Console panel
  empty, so nothing could be fired from the panel at all. After the fix:
  `play_aurora` fired from the panel's own Fire button with **no device
  joined**; the card's status line updated in place to `last fired by
  admin-manual -> sim-room-main, sim-room-accent (3 cues) ADMIN MANUAL`
  while `flash_device` stayed at `never fired` (so the card list was not
  rebuilt); `fired_by` stayed distinct from `declared_source`
  (`bit-adjudicated`); and the Room's role name, counts and node id appeared
  nowhere in the rendered page. The zone sweep itself was confirmed off the
  wire at ~9 Hz (Console `room_frame` relay): all three zones drift in
  lockstep ambiently, depart into independent motion during the script's
  declared 2 s, and snap back to lockstep at the `+2.00 s` step. One
  honesty note: the ambient `cues(at)` drift is superimposed on the script,
  so the per-step values were bounded and visible but not individually
  attributable.

### `control/room_profile.py`, `control/room_binding.py`, `control/engine.py`, `devicelink/agent.py` -- the N-fixture Room (Spec C)
The Room stops being exactly one bound device. Design:
[`.../2026-08-18-n-fixture-room-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-18-n-fixture-room-design.md).

- **One logical light, N surfaces, namespaced zones.** `RoomProfile` now
  declares N named `RoomFixture`s laid end to end in physical declaration
  order; TEST declares `main` (60 px, the original three zones) and `accent`
  (30 px, two new zones). One `LightSession` renders the WHOLE concatenated
  surface every tick -- a spatial instrument targeting `primary` sees one
  continuous position axis across every fixture, which is what lets a single
  declaration (luxaeterna's new `rainbow` preset) paint a gradient that
  crosses fixture boundaries with no seam.
- **`Room.bound` is a fixture map, `RoomBindingRegistry` keys by
  `(RoomType, fixture)`.** Admin arming now names which fixture the next
  Room-node join binds; `RoleClass.ROOM`'s capacity is the profile's own
  fixture count.
- **`GameServer` resolves ROOM cues to one canonical dev** (the first bound
  fixture in declaration order) so the shared session is fed exactly once
  per cue; a script step addressed at `cues.TARGET` on a ROOM-target trigger
  is collapsed the same way before expansion, so it cannot double-apply the
  same relative MIDI once per bound fixture.
- **`DeviceLinkAgent._render_room()` renders once, slices, sends N.** Each
  bound fixture receives its own channel slice of the one rendered frame,
  stamped with the same presentation time. A partially bound Room renders to
  the fixtures it has.
- **Boot spawns one simulator subprocess per fixture**, each its own o2lite
  client with a unique service name (`sim-room-main`, `sim-room-accent`),
  each on the `TeardownStack` individually.
- **The Console shows one strip per fixture**, each painted only from its
  own fixture's `room_frame` events.
- **Live-verified against a real Arco: DONE 2026-08-19**, with both
  simulator tabs open and measured off the canvas bitmaps rather than
  eyeballed: `main` (60 px) and `accent` (30 px) carry ONE continuous ramp.
  The decisive number is hue slope per pixel -- `main` 4.04 deg/px, `accent`
  4.049 deg/px; an independent per-fixture rainbow on a 30 px surface would
  be ~12 deg/px, three times steeper. 90 px x 4.04 deg ~= 364 deg: a single
  rainbow spanning both fixtures, `accent` continuing `main`'s ramp rather
  than restarting it. The Console's Room panel corroborated independently
  (`main` ends and `accent` begins on the same hue). Note the measurement
  needed luxaeterna's WebSim canvas fix first (its PR #14, below): before
  it, only 12 pixels of ANY linear surface were ever on-canvas.

### `control/room_profile.py`'s `RoomBlock`, and the DEMO room
`RoomType.DEMO` — previously a stub enum value with a recipe and a
Registration Node id but no `RoomProfile` (`room_profile(RoomType.DEMO)`
raised `NotImplementedError`) and no supporting Bit — now has a real,
real-scale profile, and `TestBit` runs in it. Design:
[`.../2026-08-19-demo-room-and-block-profile-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-19-demo-room-and-block-profile-design.md).

- **`RoomBlock`** (`control/room_profile.py`) — a new declarative
  sub-structure inside `RoomFixture`: which physical LED device drives which
  pixel range within one continuous fixture. A different axis from the
  pre-existing `RoomZone` (gameplay/Console targeting) — a Block is hardware
  composition, a Zone is what a light instrument can address. `RoomFixture`
  reshaped: `pixel_count` is now a derived property (sum of its blocks'
  counts) rather than a stored field, so a fixture's total size is no longer
  capped — only each block individually is, at `_MAX_PROFILE_PIXELS` (170 px,
  one DMX universe / one controller's worth). This is the load-bearing change:
  the old whole-*profile* cap is gone, replaced by a per-*block* cap, which is
  what makes a real-scale DEMO profile representable at all. TEST's existing
  `main`/`accent` fixtures each gained one explicit block spanning their full
  size — every fixture's build-out is always explicit, no implicit
  single-block default exists.
- **DEMO's profile** — one fixture (`"array"`, matching the name
  `tests/test_room_binding.py` already used), **864 px**, matching the real
  6 m / 144-LED-per-m Terrarium array (`MM_HARDWARE_DESIGN.md` section 7.1):
  six 144 px blocks (`m1`..`m6`, one per physical meter run) and three 288 px
  zones (`left`/`center`/`right`), deliberately not 1:1 with the blocks.
  Blocks are **purely declarative this slice** — no renderer, backend, or
  engine code consumes block boundaries for actual per-controller output
  routing; that real Art-Net/multi-controller wiring (`harness/array_smoke.py`
  already exists standalone but is not plugged into `boot()`) remains a
  separate, deferred slice.
- **`TestBit` supports DEMO** (`bits/test_bit.py`) — `room_types = {RoomType.TEST,
  RoomType.DEMO}` (the `Bit` base class default is `{RoomType.TEST}` alone),
  and `role_table` now merges a `room_role()` for each supported RoomType, both
  built from the same shared `light_manifest`/`ugen_manifest` (an instrument
  targets `primary`/zones, never blocks, so nothing room-specific differs
  between the two declarations). `player` (scored) and `jammer` (jam) were
  already room-agnostic, so the existing Scored/Jam validation loop applies to
  DEMO for free — this closes the original gap: every shipped RoomType now has
  a reference Bit that exercises both a scored and a jam role.
- **Boot harness threading** — `harness/room_simulator.py` and
  `harness/o2_shroom.py` already took `--room-type`/`--fixture` generically;
  only `harness/terrarium_boot.py`'s two simulator factories (`_SimulatorFactory`,
  `_O2SimulatorFactory`) and `main()` hardcoded `"TEST"`. Both now read a new
  `--room-type {TEST,DEMO}` CLI flag (default `TEST`), which `main()` also uses
  to set `BootConfig.array_backend="simulator"` for DEMO (satisfying its
  `RoomRecipe(requires_array_backend=True)`) — `array_backend=None` for TEST,
  unchanged. `harness/run_stack.py` gained the identical flag and forwards it
  into the `terrarium_boot` child command it spawns. So `python -m
  harness.run_stack --room-type DEMO` brings up a DEMO room simulator
  end-to-end the same way TEST already worked — this repo's **first working
  simulated backend for `RoomType.DEMO`**, superseding the "TEST room only"
  framing the *Terrarium Visualization Simulator* section above still carries
  in its own prose (the section itself is unedited; read it as TEST-shaped
  history, not current scope).
- **`--identify-blocks`** (`harness/room_simulator.py`) — a debug-only CLI
  flag: bypasses Control entirely (no websocket connect at all in this mode)
  and paints each of a fixture's declared blocks a distinct fixed solid color
  (red/orange/yellow/green/blue/violet, repeating past six), reading block
  boundaries straight off `room_profile()`, so a human can visually confirm
  the physical build-out mapping on the canvas. The one and only consumer of
  block boundaries in the codebase this slice.
- **First live-verify attempt found the Room's light didn't render at all —
  fixed 2026-08-19.** `devicelink/agent.py`'s `_setup_room()` built the
  Room's `LightSession` over a bare `Universe()`, and luxaeterna's `Universe`
  was hardcoded to exactly 512 DMX channels everywhere a bound was checked.
  DEMO's profile is 864 px / 2592 channels (this section, above), so every
  `render_into()` call raised `ChannelError: Range 0:2592 exceeds universe
  bounds` — caught and silently logged-and-skipped by `_render_room()`, so
  the Room's light never rendered a single frame against a real Arco even
  though audio came up fine. 2172 occurrences over one 45s run. Latent since
  before this slice: the old whole-*profile* 170 px cap (superseded by the
  per-block cap above) meant no `RoomProfile` had ever been constructible
  wide enough to exercise this path — 1056 offline tests across both repos
  never caught it because nothing drove `_render_room()` above 512 channels.
  Fixed by widening luxaeterna's `Universe` to accept an optional
  `channel_count` constructor param (default unchanged at 512 — every other
  caller, including the real Art-Net/sACN/serial-Enttec backends and
  per-device player buffers, is unaffected, since this `Universe` instance
  is never handed to any of them) and passing the Room profile's real
  `channel_count` through at construction. Design:
  [`.../2026-08-19-room-universe-channel-count-fix-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-19-room-universe-channel-count-fix-design.md).
  Regression-tested offline (a new test drives `_render_room()` against
  DEMO's real 2592-channel profile) and live-verified: a full 45s `--ci
  --room-type DEMO` run shows 0 `ChannelError`, Room light actively
  rendering the whole run. That run used `--devices 0` — `--devices 1`
  independently hit the pre-existing, unrelated headless clock-sync defect
  documented under *Not yet built* below before the render bug could be
  isolated, and separately hit a transient `game`-service-already-claimed
  race on the Arco hub; neither reproduced with `--devices 0`, and the
  Room's fixture binds and renders at boot independent of player-device
  count, so this is genuine full-duration coverage of the fixed path.
- **Live-verified against a real Arco: PARTIALLY DONE.** The
  light-rendering crash above is confirmed fixed live, and as of 2026-08-19
  `--identify-blocks` is confirmed too (needs no Arco): six distinct solid
  colors in declaration order, each measured off the canvas bitmap at
  exactly 200 screen px = 144 LEDs at the fitted 1.389 px/LED pitch, first
  painted pixel at x=20 (the margin) and last at x=1220 on a 1240 px
  canvas. That confirmation was also the discovery vehicle for two
  luxaeterna WebSim defects (12-px truncation of every linear surface, and
  a one-shot frame sent before any browser connects being lost forever) --
  fixed in luxaeterna PR #14; without them the canvas was black or 12 px
  wide and no visual confirmation was possible at all. The device-join half
  completed 2026-08-20 on the operator/harness handoff branch: a scored
  round joined during the hold (clock sync in seconds once the hold
  stopped starving Arco's pty) and completed live, and an unscored jam
  join was granted mid-RUNNING the same day -- the spec's section 7
  checklist is now fully verified. The rainbow seam sweep is
  DONE as of 2026-08-20, measured rather than eyeballed: all 864 LEDs lit,
  total hue span 359.6 deg (one full rainbow across the array), per-pixel
  hue deltas median 0.429 deg / p99 1.04 deg / max 1.252 deg -- and the max
  sits at LED 177, NOT at a block boundary. The five interior boundaries
  (144/288/432/576/720) measured 0.429-0.857 deg, indistinguishable from
  ordinary neighbouring steps. Firing `play_aurora` (resolved to
  `sim-room-array`, 3 steps, admin-manual) moved the centre LED's hue 121
  deg in 1.5 s, so the cue visibly sweeps the whole array. The transient
  `game`-service-already-claimed failures (five occurrences across
  2026-08-19/20, always the first attempt after idle) were root-caused on
  2026-08-20 and were never a second claimant at all: the hosts were
  proven process-clean, a 15 s LAN probe found no other hub, and the hub
  log carried no refusal. The probe's 2 s single shot was expiring against
  a hub blocked in its cold audio-device open (and, during holds, frozen
  on the undrained pty). Fixed on the handoff branch: the probe now
  resends every 2 s across a 10 s window, and its error names the
  blocked-hub cause first. The earlier "lingering registration"
  speculation this entry carried is withdrawn.

### `control/wire_json.py`, and the isolated Console scripts
Two defects found in one live run (the triggers live-verify above), each of
which left the Terrarium Console rendering nothing at a venue. Design:
`docs/superpowers/specs/2026-08-19-wire-json-and-console-script-isolation-design.md`.
PR #38.

- **The rule this slice establishes: every outbound JSON payload goes
  through `control/wire_json.dumps()`, never bare `json.dumps`.** Python's
  encoder emits non-finite floats as the bare tokens `Infinity`/`NaN` (a
  documented extension), and Python's own decoder accepts them back -- so a
  payload carrying one round-trips cleanly inside Python and is rejected by
  every strict parser, including every browser's `JSON.parse` and Dart's
  `jsonDecode`. The observed failure: `TestBit.status()`'s `run_duration`
  is `float("inf")` under `--hold` (which `run_stack` always passes), the
  snapshot carried a bare `Infinity`, and the whole Console rendered empty
  against a healthy stack, on every `run_stack` run. `dumps()` replaces
  non-finite floats with `null` (this wire's existing word for unbounded --
  an uncapped capacity is already `null` and renders as an infinity sign),
  passes `allow_nan=False` so a missed path raises rather than emitting an
  unparseable token, and warns once per offending path shape. Adopted at
  all eight outbound sites: the Console's two, the devicelink server's two,
  the o2lite blob encoder, the uplink, and capture's two on-disk writes.
  Finite payloads serialise byte-identically, pinned by test.
- **The testing rule that follows, worth internalizing alongside the
  o2lite-fake lesson: never validate wire output with bare `json.loads`.**
  It accepts the same extension the encoder emits, so it is a more
  permissive double for `JSON.parse` and passes against exactly this bug.
  Assert on the raw text, or parse with `parse_constant=` a raiser.
  `tests/test_wire_json.py` and `tests/test_wire_json_boundaries.py` do
  this throughout.
- **Console scripts are IIFE-isolated now, exporting only their entry
  points.** (This plain-`<script>`-tag, shared-global-scope mechanism no
  longer exists: the front-end rewrite documented in the dated section near
  the end of this file replaced it with ES modules, which structurally
  cannot collide on a global function name the way this defect did. The
  historical account below is kept for the defect's own lesson — never
  validate browser JS with a source-text grep — which still applies.)
  `room.js` and `triggers.js` both declared a global
  `function buildCard`; loaded as plain scripts, triggers.js silently won,
  and `renderRoom` called the trigger version with a room instrument and
  threw on every `room_changed` -- 222 throws in 2.5 s live -- aborting
  `handle()` and killing the Room's instrument cards, the whole Triggers
  panel and the Event log. Both files now export exactly the five names
  `console.js` dispatches to (`renderRoom`, `renderRoomFrame`,
  `renderTriggers`, `renderTriggerDevices`, `renderTriggerFired`);
  everything else is private. The guard is structural:
  `tests/js/console_script_isolation.test.js` reads the `<script>` list out
  of `index.html` and asserts the global surfaces are disjoint, so a fourth
  script is covered automatically, and
  `tests/js/console_full_stack.test.js` loads all three scripts together --
  the combination the browser actually runs, which no earlier test loaded
  (one loaded room.js+console.js, another triggers.js alone; each file was
  correct in isolation, the defect existed only in the pair).
- **Known accepted gaps, recorded here so nobody rediscovers them:**
  non-finite dict KEYS raise loudly via the `allow_nan=False` belt rather
  than degrading to null (deliberate; the belt test exercises exactly that
  path); harness dev clients (`shroom_client.py`, `room_simulator.py`)
  still send raw `json.dumps` toward Control (their payloads carry no
  float fields today; candidate follow-up); and `bit_status` renders a
  `null` `run_duration` as an empty cell rather than an infinity sign
  (`renderStatus` is a generic k/v table; only the Registration capacity
  column maps null to infinity). Cosmetic.

### The operator/harness handoff, and the starved Arco pty
Five defects from one live UAT afternoon (2026-08-20), all in the harness
and operator surface, none in the engine. Design:
`docs/superpowers/specs/2026-08-20-operator-harness-handoff-design.md`.

- **The load-bearing rule this slice earned: every loop that holds while
  Arco is alive must drain Arco's pty.** `_wait_in_setup` did not, so for
  the whole of any `--setup-seconds` hold Arco (a curses app on a pty)
  filled its buffer, blocked mid-write, and froze: no clock sync served,
  no routing, no audio. Verified live before the fix: a 0-byte
  `--arco-log` tee eleven minutes after spawn, a byte-static
  `o2debug.log`, and an engine in RUNNING with no drone. This one starved
  pty explained, at a stroke: devices "taking 60-100 s to clock-sync"
  (blocked, not slow -- they synced the instant the hold expired and
  draining resumed, straight into a closed scored window, which read as
  "the tuneshroom crashes when the sound starts"); most of the
  cold-start ownership-probe failures; and the entire "headless
  clock-sync defect" (spawned devices always synced into a frozen hold).
- **The hold yields to the operator.** `_wait_in_setup` returns a reason
  string (`"expired"`/`"parent-gone"`/`"state-changed"`) and watches
  `gs.state`; `main()` calls `gs.run()` only when the state is still
  SETUP. Before this, an operator pressing Run on the Console during the
  hold killed the harness with an uncaught `InvalidTransition` at hold
  expiry, taking Arco down mid-session while a lingering console thread
  kept serving stale state. The engine was and is correct; the harness
  stopped assuming it is the only driver.
- **The ownership probe distinguishes a blocked hub from a conflict**:
  resend every 2 s across a 10 s window (`ownership_timeout` is now the
  total). A genuine second claimant never answers; a blocked hub answers
  when it unblocks. The error names the blocked-hub cause first.
- **`o2_shroom` re-verifies its service on any `bridge_id` change** (an
  auto-reconnected device once lost its announcement and heard silence
  forever while fifteen Control replies were dropped hub-side), passes
  `surface_id=dev` so the canvas header stops calling every device `ie0`,
  prints `role has no light declaration -- canvas stays dark by design`
  for a light-less role (TestBit's `jammer` was such until PR #50 gave it
  a glow; the message still fires for any role without a declaration), and
  its unanswered-join hint now names the lost-service cause and the hub
  log line to check instead of pointing at a healthy Control.
- **Control's stdout narrates the device lifecycle**: `device hello:`,
  `join granted: ie1 -> player (scored) via TEST_PLAYER_NODE`,
  `join denied: ... (reason)`, `device released:`, plus
  `SETUP open, Ns remaining` every 15 s. Hellos/grants/releases ride an
  engine observer (the ConsoleAgent seam; releases are derived from
  registration diffs because `on_release` is a transport-owned sink);
  denials ride a guarded `on_join_denied` sink on `DeviceLinkAgent`
  threaded through `build()`. The lowercase `join denied:` casing is
  load-bearing against the uppercase `JOIN DENIED:` marker.
- **Live-verified 2026-08-20, both halves.** Headless: the repo's first
  green end-to-end CI run (above). Interactive: scored round joined
  during the hold and completed, Console Run mid-hold handed off with no
  crash, drone and device animation live.

### WebSim two-way input -- browser gestures become real /game/* messages
The simulated Tuneshroom is now playable from its own canvas. Design:
[`.../2026-08-20-websim-two-way-input-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-20-websim-two-way-input-design.md).
Cross-repo: luxaeterna's `WebSimBackend` gained an optional `on_input`
callback (inbound JSON text messages over the already-open page websocket;
binary stays down-only; malformed JSON and raising callbacks are dropped,
never fatal) and `PAGE_HTML` gained pointer handlers -- click sends
`{"type":"tap","count":1}` (**as of the MetronomeBit slice, immediately on
pointerup**: the original 250 ms held click and its double-click count-2
path were removed as fatal to rhythm input; a pointerup with no preceding
pointerdown is guarded and sends nothing), and a
horizontal drag maps canvas x onto gamma in [-90, 90] as
`{"type":"tilt","gamma":g}` at most every 50 ms, with a >5 px drag
suppressing the click that browsers still fire after it. On this side,
`harness/o2_shroom.py` bridges callback -> bounded `queue.Queue`
(drop-oldest; the callback runs on the websocket handler thread) ->
`drain_gestures()` in the existing tick loop, which sends the documented
wire rows `/game/tap sffi [dev, 1.0, 50.0, count]` and `/game/tilt sf`.
Stamping follows Design Rule 4: the harness stamps `o2lite.time_get()`
**at enqueue time, on the websocket callback thread** (moved from drain
time by the MetronomeBit slice -- drain-time stamping added up to ~23 ms
of 44 Hz tick quantization, fatal to a +/-50 ms rhythm window; `time_get`
was verified a pure read before being called off-thread). The whole
simulator process is still the device, so the browser hop is inside it
and browser clocks never touch the wire. An operator
drag suspends the synthetic tilt sweep for `SWEEP_RESUME_SECONDS` (5.0)
while `next_tilt` keeps advancing, so the sweep resumes on schedule with
no overdue-tilt burst. Gestures are dropped until the role is granted
(same UDP-overtakes-TCP race guard as the sweep) and on `--no-join`
(Room) runs. No `devicelink/protocol.py`, engine, or Bit changes:
`tap`/`tilt` already ride the generic `/game/<verb>` path into TestBit's
handlers. `ShroomClient` also gained the `tap()` encoder its docstring's
wire table had documented but never implemented. Live-verified
2026-08-20 against a real Arco via `run_stack --ci --devices 1`: taps
sent over ie1's WebSim socket came back as `/ie1/play` cues (click,
chime, and the `flash_device` trigger), teardown clean. Note the device
still ignores `/<dev>/play` by design, so the sample plays nowhere on the
simulator yet -- local sample playback on the sim is a separate, later
slice.

### `bits/metronome/metronome_bit.py` -- MetronomeBit, the first production game Bit
A call-and-response rhythm game for `RoomType.DEMO`, built entirely on
existing seams. Design:
[`.../2026-08-20-metronome-bit-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-20-metronome-bit-design.md)
and its plan `.../plans/2026-08-20-metronome-bit.md`. PR #44.

- **Gameplay:** 100 BPM 4/4 metronome (woodblock, 1 HARD + 3 soft), 8-beat
  cycle (4 call + 4 wait) x4 per run, round-robin turns over up to 2
  Tuneshrooms (`RoleClass.UNIQUE`, capacity 2, node `METRO_PLAYER_NODE`).
  A phrase succeeds only when all 4 wait beats get an in-time tap
  (+/-50 ms) and no off-grid tap spoils it; success fires fireworks on
  that shroom and the array, failure goes red with a low synth-bass tone.
  Any success earns a 10 s rainbow + modulating warm-pad finale.
- **Timing model:** the whole game lives in `at`-space -- the beat grid is
  anchored on the first `cues(at)` call and taps are graded against it, so
  the `cue_horizon` offset cancels by construction. `INPUT_OFFSET_S` is a
  class-constant calibration knob; `status()` surfaces the last 8 signed
  tap errors in ms, making the Console the measurement instrument for
  whether the +/-50 ms window is achievable on a given input path.
- **All consequences are `TriggerTable` scripts** (fireworks_player/room,
  fail_player/room, finale), so the Console shows and can manually fire
  each. All audio is ROOM-side (players hold no Arco voice); the room's
  flsyn switches programs mid-run via 0xC0 cues (woodblock 115 / synth
  bass 38 / warm pad 89). A failed shroom is tracked in a failed-devs set
  so the ambient beat pulse skips it -- it stays dark until its own turn's
  recovery cues relight it.
- **Harness:** at the time this Bit landed, `--bit {TestBit,MetronomeBit}`
  was a hardcoded choice on `terrarium_boot`/`run_stack`, and `run_stack`
  derived each spawned device's join node from a hardcoded bit-to-node
  dict (explicit `--node` overrides) -- found by the first live run, where
  devices joined `TEST_PLAYER_NODE` and were denied. The Bit packaging and
  launch slice (2026-08-21, below) replaced both the choices list and the
  node dict with manifest-driven discovery: `--bit` now accepts any
  registered package name (`--list-bits` enumerates them), and the join
  node comes from the Bit's own `bit.toml` (`[launch.nodes]`), not a table
  in the harness.
- **Live-verified headless 2026-08-20** (`--ci --devices 2 --room-type DEMO
  --bit MetronomeBit`, runs/20260820-231958): both joins granted, clean
  teardown. The interactive tap-through (fireworks/red/finale by playing,
  reading `tap_errors_ms`) is the remaining human verification. CI note:
  a `--seconds` bound below ~35 s can truncate the finale window.

### Bit packaging, manifests, start conditions, profiles, and launch (2026-08-21)
Design:
[`.../2026-08-21-bit-packaging-and-launch-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-21-bit-packaging-and-launch-design.md).
A Bit used to be a Python class plus knowledge hardcoded across three
harness files (the class map, the `--bit` choices list, a bit-to-join-node
dict). This slice turns a Bit into a **discoverable package**: a directory
under `bits/` with a declarative `bit.toml` manifest, so the harness, the
Console, and (in principle) a third-party launcher over the uplink can all
enumerate, configure, and launch Bits without importing their code first.

- **`bits/<name>/bit.toml` + no-import discovery.** Each of the three
  existing Bits (`bits/test/`, `bits/metronome/`, `bits/capture/`) is now a
  package: `bit.toml` (schema below) plus the unchanged Bit class module.
  `control/bit_registry.py` scans `bits/*/bit.toml` with stdlib `tomllib`
  and never imports the Bit's own module until `load_bit` — a broken
  manifest is collected as a located, per-package error (surfaced by
  `--list-bits` and the Console) and never breaks discovery of the other
  packages. The Bit's module is imported only via the manifest's
  `entry = "module:Class"`.
- **`control/bit_config.py` — `BitConfig` schema v1.** Validates and parses
  `bit.toml`: `kind` (`music`/`r_game`/`game`/`tool`/`ambient`), `[launch]`
  (room types, default room type, default device count, setup/expected
  seconds, transport, default join role, `[launch.nodes]` role->join-node
  map), `[start]` (start condition — see below), `[console]` (display name,
  notes), `[results]` (declarative result keys), and the Bit-specific
  `[rhythm]`/`[ambient]` blocks. `merge_overrides` re-validates after
  applying CLI/profile overrides — an override can't silently produce an
  invalid config; it fails the same way a bad manifest would.
- **`Bit(config)` + `GameServer.load_bit(name, config=None)`.** The engine
  stays Bit-agnostic: `load_bit` resolves the manifest, imports the entry
  class, and passes the opaque `BitConfig` into the constructor — Control
  never inspects the config's contents, only threads it through. This is
  the slice's one change to the engine itself.
- **`control/start_condition.py` — declarative start conditions.**
  `immediate` (starts as soon as SETUP's own deadline is reached),
  `players` (starts the instant `scored >= min_scored`, evaluated inside
  the existing SETUP hold in `harness/terrarium_boot.py` — see the live
  caveat below), and `operator` (console-driven only), each with a
  `timeout_seconds` / `on_timeout` (`start`/`abort`) fallback.
  `start_decision(cond, scored=, elapsed=, setup_seconds=)` is the single
  function both the harness hold and its CI-timeout math call.
- **`terrarium_boot` + `run_stack` are discovery-driven.** `--bit` accepts
  any registered package name (previously a hardcoded choices list);
  `--list-bits` enumerates name/version/kind/room-types/start-condition/
  description for every discovered package (including located manifest
  errors); when `--room-type`/`--node`/`--devices` are omitted they come
  from the manifest's `[launch]` defaults, not a CLI default or a hardcoded
  dict. The CI timeout bound is `max(manifest setup_seconds, --setup-seconds)
  + expected_run_seconds + 15`. A manifest's `[launch] setup_seconds` governs
  the actual SETUP hold only for a bare `terrarium_boot` launch; `run_stack`
  always forwards its own `--setup-seconds` (default 90.0), so when launching
  via `run_stack` the manifest value only feeds the CI-bound formula above,
  not the hold itself.
- **`control/run_profile.py` + `--profile`.** A profile (e.g.
  `profiles/dev-metronome.toml`) names a bit and can override any of its
  manifest fields (`[bit.overrides.*]`); precedence is
  **manifest < profile < CLI flag**, so a profile can pin defaults for a
  named scenario (a demo, a load test) while a CLI flag still wins for a
  one-off tweak.
- **Uplink/console.** `uplink/protocol.py` gained `list_bits` and a
  `load_bit` override path; `console/agent.py`/`uplink/link.py` stamp
  `bit_completed` with the active bit's name. The Console's Load picker
  (in `console/static/bit.js` since the ES-module front-end rewrite — see
  the dated section near the end of this file) renders the same
  `bits_listed` snapshot sent at connect time.

**Load-bearing:** the engine boundary rule holds — `GameServer.load_bit`
threading an opaque `config` through to the Bit constructor is the *only*
change to `control/` proper; every other consumer (harness, console,
uplink) reads the manifest, never the Bit's Python.

**Traps live-verified 2026-08-21** (see the design doc's Status section for
full run evidence):
- A `players` start condition with `min_scored = N` closes scored
  registration **the instant** the Nth scored device joins — not after the
  full SETUP hold. A CI smoke test that spawns more devices than
  `min_scored` (e.g. `--devices 2` against MetronomeBit's shipped
  `min_scored = 1`) will see the extra device(s) denied with
  "registration closed for scored roles"; this is the start condition
  working as designed, not a bug, but it means the device count for a
  smoke test must match (or a profile must override) the Bit's own
  `min_scored`.
- `run_stack` treats a child that exits cleanly (code 0) **during the
  SETUP-hold-turned-RUNNING duration**, before the requested `--seconds`
  elapses, as a `child-exited` stage failure — even though the game
  itself completed normally. A short demo game (MetronomeBit's 4 cycles)
  finishes well inside a generous `--seconds`, so pick a `--seconds` close
  to `expected_run_seconds`, not an arbitrarily large one, when driving it
  through `run_stack` directly.
- `console/server.py`'s websocket endpoint is `/ws`, not the port root —
  a raw client (or a future non-browser consumer) must dial
  `ws://host:port/ws`.

### Console-operator rounds: serve-mode round loop, lazy full registry, merged control bar (2026-08-21)

> **Trap, live 2026-08-21 (fixed the same day):** under `run_stack`, a
> Console abort looked like "Arco closes". It was `run_stack._hold`
> treating the released device's by-design code-0 exit as `child-exited`
> and SIGTERMing a healthy Control; Control's normal teardown then took
> Arco down (`Arco_engine: finish called` is the room-bridge teardown
> step, not a failure). Serve mode now tolerates clean device exits;
> control death and non-zero device exits still fail loud.
>
> **Per-round device respawn, live 2026-08-24:** the device-less-round-2
> consequence above is gone. `run_stack` watches its Control tee for
> `markers.CONTROL_ROUND_LOADED` ("round loaded: `<Bit>`", emitted once
> per round including round 1) and, from round 2 on, spawns a fresh set
> of simulated devices for the just-loaded Bit -- `_hold` drains a queue
> fed by the tee's `on_line` hook once per tick, so the respawn never
> races the main thread's teardown-stack pushes. Node and device count
> come from the loaded Bit's own manifest (`launch.nodes` /
> `launch.default_devices`) unless `--node`/`--devices` were passed
> explicitly on the CLI, in which case those pin every round, not only
> round 1. Respawned children are named `ie<k>-r<N>` (`N` = round
> number, e.g. `ie1-r2`, `ie2-r2` for a two-device round 2, `ie1-r3` for
> a one-device round 3) so their logs and `runs/<run-id>/` sample files
> never collide with round 1's `ie<k>`. The spawn is best-effort: no
> DEVICE_CLOCK_SYNCED/DEVICE_ROLE_GRANTED gating like round 1's launch
> path, no readiness wait -- `_hold`'s own polling is what notices a
> respawned device's eventual exit, and a manifest with no
> `launch.nodes` (or an unknown Bit name) just skips the respawn with a
> stderr note rather than failing the round. Live-verified headlessly
> (`run_stack --console-port 0 --room-type DEMO`, driven over the
> console `/ws`): round 2 (`load_bit MetronomeBit`, 2-device manifest)
> spawned `ie1-r2` and `ie2-r2`, both clock-synced and join-granted on
> `METRO_PLAYER_NODE`; round 3 (`load_bit TestBit`) spawned `ie1-r3`,
> which clock-synced and attempted to join `TEST_PLAYER_NODE` -- the
> join itself raced TestBit's short SETUP window and was denied ("no
> Bit accepting registrations"), the same documented per-round
> `setup_seconds` race noted below, but the respawn, naming, and node
> resolution all landed correctly. `SIGINT` on the process group still
> tears the whole stack down cleanly (`pgrep -f "o2_shroom|
> terrarium_boot|room_simulator"` empty afterward). **Real-device
> reconnection is still deferred** -- this closes the simulated-device
> gap only; a physical Tuneshroom reconnecting mid-session across
> rounds is unbuilt.
Design:
[`.../2026-08-21-console-operator-rounds-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-21-console-operator-rounds-design.md).
Before this slice, `run_stack`/`terrarium_boot` ran exactly one Bit and
exited when it finished — an operator working the Console could load
whatever Bit they wanted, but only for round one; loading a second Bit
after the first completed had nowhere to go because the process was
already tearing down. This slice makes the stack **stay up across rounds**
and lets discovery (not a hardcoded class map) decide what the Console can
load.

- **`BitRegistry.lazy_class_map()`.** Previously the engine's
  `bit_registry` dict was built from a fixed, hand-maintained set of
  imports; any newly-packaged Bit needed a code change to become
  *loadable*, even though `--list-bits` already discovered it. This method
  returns a `Mapping[str, type]` that resolves each entry's class from its
  manifest's `entry = "module:Class"` lazily, on first `__getitem__` — so
  every discovered package (`bits/*/bit.toml`) is loadable from the
  Console with no import list to keep in sync. A broken manifest still
  surfaces as a located error at discovery time, not as a `KeyError` at
  load time.
- **`terrarium_boot --serve` round loop.** `_wait_for_load` (sit in IDLE
  until a console `load_bit` moves the engine out of it) and
  `_serve_rounds` (load → hold-in-SETUP → run → complete → repeat, calling
  `_wait_for_load` again for the next round) turn the single-round driver
  into a loop that keeps the Arco/Room/device processes alive across
  Console `load_bit`/`run`/`abort` cycles. A console-port flag now
  **implies** `--serve` unless the caller also passes `--seconds` or
  `--hold` (an operator who opened a Console clearly wants to drive
  rounds from it, not have the process exit under them the instant the
  first one ends).
- **`run_stack --serve` forwarding.** `run_stack` forwards `--serve` to
  `terrarium_boot` the same way; a console requested outside `--ci`
  implies `--serve` for the identical reason. `--ci --serve` together is
  refused — a headless CI run has no operator to drive a round loop, so
  asking for one is almost certainly a mistake, not an unusual but valid
  combination.
- **Merged Console top control bar.** The Console used to show two
  separate ideas of "which Bit is active" — a legacy status block (state
  + a bare Load button) above the Bits panel's own cards. That block is
  deleted; the Bits panel is now the single owner of load/run/abort, and
  `state_changed` (`console/protocol.py`'s `state_changed_event`) carries
  `loaded_bit` alongside `state` so the header updates on every state
  transition — not only at connect (`snapshot`) or on a `bits_listed`
  refresh — without a page reload.

**Live-verified 2026-08-21** (headless, no browser): `run_stack
--console-port 0 --devices 0 --room-type DEMO --setup-seconds 90`, driven
over the console `/ws` with a small `websockets` client. Full round cycle
exercised: `load_bit MetronomeBit` → `abort` → `load_bit TestBit` → `run`
→ TestBit's own run completes → `load_bit MetronomeBit` again — all under
one `run_stack`/`terrarium_boot` process pair that never restarted, then
a clean `SIGINT` teardown (`pgrep -f "o2_shroom|terrarium_boot|
room_simulator"` empty afterward). One correction to the plan's live-test
assumption surfaced in the process: `--setup-seconds` on `run_stack` only
bounds **round one**'s SETUP hold (the CLI-selected Bit); every later
round's SETUP window comes from that round's own Bit manifest
(`cfg.launch.setup_seconds`, read by `_serve_rounds`, not the CLI flag) —
so TestBit's own (short) manifest window governed its round, and the
explicit `run` command raced a state already mid-transition to `SETUP`/
`RUNNING` on its own. This is `_serve_rounds`'s documented per-round
config lookup working as designed, not a bug — see its docstring in
`harness/terrarium_boot.py`. Real-browser click-through of the merged
control bar remains for a human: see the spec's Status section for the
exact split of what ran here versus what's still unverified.

### Terrarium Console front-end rewrite: six ES modules replace `console.js`/`room.js`/`triggers.js`/`style.css` (2026-08-25)
The plain-`<script>`-tag front end (`console/static/{console.js,room.js,
triggers.js,style.css}`) described in earlier entries above is **gone**,
deleted whole and replaced by six ES modules under `console/static/`, each
with its own node test file plus a shared test DOM stub. The
plain-scripts/shared-global-scope mechanism that produced the `buildCard`
collision defect (see the wire-json/Console-script-isolation entry above) no
longer exists — ES modules have their own scope by construction, so that
whole defect class is structurally unreachable now, not merely guarded
against.

- **`wire.js`** — the sole WebSocket owner. Exports `on`/`send`/
  `flashRefusal`/`connect`, plus the shared `confirmTap` two-tap-confirm
  helper: arm on first tap, revert on timeout, fire the real action on a
  second tap within the window. `confirmTap` keys its armed/timer state off
  the specific button element passed in — this is why every other module's
  render path is under the same discipline described below: minting a fresh
  button node on a tick that shouldn't have touched it silently drops an
  in-progress confirm arm.
- **`shell.js`** — the entry point (what `index.html` loads) and the top
  bar (connection state, room/bit summary chips).
- **`bit.js`** — the sidebar: the Loaded-Bit panel, the Load picker (reads
  the same `bits_listed` snapshot `console/agent.py` has always sent at
  connect time), and the Bit status card. Its Abort button uses
  `wire.confirmTap`.
- **`surface.js`** — the Room card: the LED array rendered as one `<canvas>`
  dot-row per physical block (never DOM nodes per pixel), the zone bar, each
  fixture's binding controls (chip + Release/Arm, also on `confirmTap`), and
  the Instruments accordion. Exports `buildInstrumentCard`, reused by
  `rail.js`. Each bound fixture also gets a pop-out link (a plain `<a
  target="_blank" rel="noopener">`), one per fixture, sourced from the
  `/game/canvas` messages the Console has collected keyed by device; a
  fixture with no reported canvas URL gets no link. The URL a device reports
  travels the `["ss", [dev, url]]` `/game/canvas` message and is validated at
  the decode boundary in `devicelink/protocol.py`'s `parse_canvas_url`,
  which enforces the `http://`/`https://` scheme allowlist so a hostile
  device cannot plant a `javascript:` link in an operator's browser.

  The file's own header comment states the rule its render path
  is built around: **a controllers-only `room_changed` must repaint nothing
  but live lane values** — no DOM subtree with node-identity-dependent state
  may be rebuilt just because a live CC value ticked. Fixtures, their
  binding controls, and Instruments-grid cards are each keyed by a stable
  identity and gated on a signature of their own declaration
  (`fixtureShapeMatches`/`bindStateKey` for fixtures and binding controls,
  the equivalent instrument-declaration key for Instruments cards); only a
  card/fixture whose own declaration actually changed is torn down and
  rebuilt, everything else gets its live values patched in place.
- **`triggers.js`** — compact trigger cards, one per declared trigger,
  updated in place on `trigger_fired` (rebuilding the whole list on every
  fire was the discipline this panel needed from day one; see the
  triggers-and-cue-scripts entry above).
- **`rail.js`** — registration, devices, roles, and the event log. Its
  Roles & Manifests panel imports `buildInstrumentCard` from `surface.js`
  rather than duplicating instrument-card rendering.

**The standing rule is front-end-wide now, not scoped to one file:** every
high-frequency wire event (`room_changed`, `devices_changed`,
`trigger_fired`) must never rebuild a DOM subtree whose underlying
declaration has not changed, because DOM-identity-dependent state — most
concretely an armed `wire.confirmTap` button — is silently lost the moment
its element is discarded and replaced. This is the same defect shape as the
old `renderRoom` strip-rebuild bug (see the Room-panel entry above), now
enforced structurally across all six modules rather than fixed once in one
file.

**Test infrastructure:** `tests/js/_dom_stub.js` is the one shared DOM stub
behind six per-module test files (`wire_and_shell.test.js`,
`bit_panel.test.js`, `surface_panel.test.js`, `triggers_and_rail.test.js`,
plus the ones named above) and a whole-graph `tests/js/full_stack.test.js`
that loads every module together — the combination the browser actually
runs, the same lesson the old `console_full_stack.test.js` existed to teach
(see the wire-json entry above).

**The one backend addition this rewrite needed:** `control/bit_registry.py`'s
`list_view()` now includes a best-effort `roles` summary per Bit, so the
Load picker can show what a Bit will grant without loading it first.

### SolidCue overrides, SURFACE targeting, per-surface mute, and the four operator triggers (2026-08-26)
The Triggers panel becomes a real operator control surface. Design:
[`.../2026-08-26-trigger-cards-and-surface-triggers-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-26-trigger-cards-and-surface-triggers-design.md)
(PR #56). **Live Arco verification is pending** — the spec carries the
operator checklist; nothing below has been confirmed against real hardware.

- **`SolidCue(dev, rgb, level, duration, when=None)`** (`control/cues.py`) —
  a solid-color override applied ON TOP of a surface's rendered session
  frame, bypassing instruments entirely, so it works on roles with empty
  light manifests. Implemented wholly Control-side at `DeviceLinkAgent`'s
  two send seams (`_render_frames` per device, `_render_room` on the whole
  frame before slicing) — **no device-wire change**; devices stay dumb pixel
  sinks. `duration` seconds from `when`, then the override expires and the
  session frame force-resends (`_last_frames` invalidated, per-fixture for
  the Room); `duration=None` latches until cleared.
- **`MuteCue(dev)` + the per-surface mute latch** — Stop's mechanism.
  `GameServer.muted: set[str]` (resolved dev ids; the Room by its canonical
  dev). Muting purges that surface's pending timed cues (new payload-generic
  `TimedQueue.purge(predicate)` — spec section 4 step 1; the drain also
  guards on the mute as a second line of defense), silences the Room voice
  (expression 0, guarded), installs a latched blackout override, and skips
  breath/light/play cues for the surface. **Any non-mute trigger fire at a
  muted surface un-mutes it first** — there is no dedicated un-mute control.
  Mute state rides `devices_changed` (`muted` flag per device row) and
  clears at UNLOADING.
- **`TriggerTarget.SURFACE`** — third target kind: the Console card renders
  a picker listing the Room first (sentinel value `@room`, resolved exactly
  as ROOM cues are) plus every connected device (muted ones labelled). The
  fire command reuses `FireTriggerCommand.dev` unchanged.
- **TestBit now declares four SURFACE triggers** (supersedes the
  two-trigger entry above): `flash_device` (chime `PlayCue` + 5 s solid
  white at 90%, then back to ambient), `play_aurora` (script unchanged; the
  three-tilt latch still fires it at the Room via
  `FireTrigger("play_aurora", ROOM)`), `stop` (single `MuteCue`), and `win`
  (new `"win"` sample + a cc:74 flourish). Gotcha: a Room-targeted
  Flash/Win has **no sound half** — the Room has no local-sample player;
  per-device Arco audio streaming (`o2audioio`-out) was assessed and
  deliberately deferred as its own future slice (the upstream ugen is
  bidirectional, but no device-side receiver exists anywhere in the stack).
- **Trigger cards compacted to the Instrument form factor** (~3 across,
  `minmax(215px,1fr)`): `triggers.js` markup reconciled to the 2026-08-25
  redesign classes — which also fixed a latent `trigrid`→`.triggrid`
  class-name mismatch that had left the redesign's grid CSS entirely
  unapplied to this panel.

## Boundary rules (the load-bearing invariants)

These are the rules that keep the architecture coherent as real outputs land —
honor them in any new work:

1. **Single writer to `/arco`.** Only Control builds ugen graphs and owns the
   ugen id space. Interactive Elements express intent to `/game`; Control
   decides the audio consequence. A device never touches `/arco`.
2. **Uplink and console are monitor/control shells, never the hot loop.** Both
   attach via the engine's observer list and run from the same tick loop as the
   engine, but a console/uplink exception must never propagate into the engine
   tick, and neither carries per-device `join`/`tick` traffic. Gameplay
   correctness never depends on either link's health.
3. **Lux Aeterna is the lighting renderer, downstream of Bit cue logic.**
   [Lux Aeterna](https://github.com/Musical-Mycology/luxaeterna) is MM's Python
   DMX512 / Art-Net → WLED lighting library — the visual analog of the Arco
   audio engine — driving the Terrarium array and Shroom LEDs in a **44 Hz hot
   render loop**, downstream of a Bit's cue/graph logic (see
   `MM_HARDWARE_DESIGN.md` and `mm-shrooms-app/shroom-installations-design.md`).
   The console's relationship to it is **monitor, never drive**, exactly the
   boundary drawn for Arco: it *displays* each role's declared `light_manifest`
   (real light-manifest v2 declarations as of PR #5); it never instantiates
   Arco ugens and never pushes frames to Lux Aeterna's render loop. (A future Lux Aeterna health read-out — Art-Net
   link up? WLED reachable? — is anticipated through the generic `Bit.status()`
   seam, but needs Lux Aeterna actually running, so it is a later slice.)
4. **An in-process consumer is reached by a Python method call, not by O2.**
   Control is an o2lite client, and o2lite `send()` has **no local
   short-circuit** — addressing a service from inside the process that offers it
   round-trips through Arco and back. O2 addressing is for the process boundary
   only. This is why the Lux Aeterna renderer inside Control is driven by direct
   calls (`session.feed_midi(...)`, `.swap(...)`) at **zero** hops, while the
   same renderer on a Tuneshroom takes `/light/midi` at 2. Corollary worth
   preserving: `game` and `actl` are **inbound-only** today (devices → `game`,
   Arco → `actl`), so Control never messages itself and there is no round trip
   to eliminate. Keep it that way. See design doc § *Message Routing*.
   **One deliberate exception:** `verify_service_ownership`
   (`devicelink/o2_transport.py:119`) sends Control's own `game` service one
   self-addressed message at startup. It is not a steady-state message path;
   it is an assertion that *uses* the no-local-short-circuit property this
   rule documents as a cost -- a message addressed to a service the process
   itself offers only comes back if the hub really routed it there, which is
   exactly the measurement a refused announcement (see *Not yet built*)
   needs. Run once before the tick loop begins, never again after. `game`
   and `actl` remain inbound-only in steady state.
5. **A test double must never be more permissive than the library it stands
   for.** Earned the hard way on 2026-08-13: `FakeO2Lite.deliver()` called
   handlers directly while real o2litepy dispatches only from inside
   `poll()`, so 611 passing tests and a full review chain all agreed the
   o2lite transport worked while it had never delivered a single message.
   The fake and the tests were consistent with each other, and both were
   wrong about the library. When a double stands in for something outside
   this repo, encode the *strictness* as well as the shape: what the real
   thing refuses, when it dispatches, and what it requires you to call. The
   dimension nobody thinks to check is the one reality will differ on.
   (Appended as rule 5 rather than inserted: rules are referenced by number
   from code comments, tests and other repos' docs, so renumbering an
   existing one silently repoints every reference to it.)

## Host platform (gotcha)

The venue target is **bare-metal Linux on a Raspberry Pi 5** with a mandatory
I2S DAC HAT. **Virtualized hosts are ruled out for bring-up:** both O2 discovery
and Art-Net-to-WLED are UDP on the LAN, and a NAT'd VM or **WSL2** host sits on
its own virtual subnet, so neither arrives. Treat WSL2 as a non-starter rather
than something to work around.

Develop without hardware using luxaeterna's `WebSimBackend` (browser-canvas
12-LED Shroom; `serve=False` for a headless frame recorder) — `harness/led_smoke.py`
is the worked example. **Any timing figure must be measured on the venue box**,
which relays every hop above through the same process doing all room synthesis
while feeding a 44 Hz render loop. The M1a-era "round trip under 50 ms" number
does **not** carry over — it was measured with Control not in the path. See
design doc § *Host Platform*.

`harness/render_bench.py` is the tool for that measurement, and it deliberately
reports **worst frame and p95 alongside the mean**: a loop that averages 44 Hz
while stalling 200 ms once a second reads as healthy and is not. It drives the
output loop synchronously rather than reading `MultiUniverseOutputLoop.fps`,
because that property is a smoothed once-per-second average — exactly the
averaging the tool exists to defeat. **No venue-box figures have been recorded
yet**; the box does not exist.

## Relationships to other repos

- **arco / o2** (rbdannenberg upstream, Musical-Mycology forks) — the synthesis
  engine and O2 transport this server builds on. The Arco server *is* the room's
  O2 hub and sole synthesizer. Two O2/o2litepy defects found while
  investigating the Room-simulator service collision (a refused service
  announcement is silent on the client; o2litepy's discovery has no ensemble
  filter) are written up for Roger at
  [`docs/upstream/2026-08-14-o2-service-and-discovery-report.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/upstream/2026-08-14-o2-service-and-discovery-report.md)
  -- reports, not proposals; see *Not yet built / deferred* below for the
  standing descriptions of both.
- **pyarco**: the Python control layer Control+GameServer builds ugen graphs
  through. A **dev/test-only dependency reached by `PYTHONPATH`**, following
  the luxaeterna precedent: nothing is vendored or submoduled, and
  `control/audio.py` never imports it, so the whole suite still runs offline.
  Its source-of-truth is now settled (2026-08-10): the sibling `arco` checkout's
  `pyarco/` subdirectory (`PYTHONPATH=/Users/chris/projects/arco`), maintained
  upstream by Roger Dannenberg in `rbdannenberg/arco` and mirrored to the
  `Musical-Mycology/arco` fork — not a submodule. The earlier standalone
  `Musical-Mycology/pyarco` repo was an independent MM implementation that
  predated this decision; it is now archived and superseded.
- **o2litepy** (`arco/o2litepy/`, same checkout as pyarco) — the Python o2lite
  implementation pyarco connects through, and now the one `O2LiteTransport`
  rides. Reached by the same `PYTHONPATH`, never vendored. **No module under
  `control/` may import it**, and `devicelink/o2_transport.py` does not either
  (the caller injects an already-connected object), which is what keeps the
  whole suite runnable with no Arco, no pyarco and no O2. That rule is the
  same one `control/audio.py` has always followed for pyarco. As of
  2026-08-20, `harness/run_stack.py` no longer requires the PYTHONPATH to
  be set by hand: when o2litepy is not importable it falls back to the
  same hardcoded checkout (`ARCO_PYTHONPATH`), appending it to `sys.path`
  and to the spawned children's `PYTHONPATH`; an explicit PYTHONPATH
  still wins. As of 2026-08-21 this fallback lives in one place,
  `harness/arco_paths.ensure_o2litepy()`, and `harness/o2_shroom.py`
  calls the same function rather than duplicating the `sys.path` append
  -- a hand-run `o2_shroom --dev probe` with no `PYTHONPATH` set gets
  past the o2litepy import the same way `run_stack` does (live-verified
  2026-08-24: no stack running, `PYTHONPATH` unset, the probe reached
  its `BROWSE_URL`/frame-summary output with no `ModuleNotFoundError`).
  Upstream note, same day: o2litepy's canonical home is now
  the `rbdannenberg/o2` repo's reworked package (`o2litepy/src/o2litepy`),
  with the `arco/o2litepy/` copy Roger describes as a downstream copy he
  may remove -- the two are byte-identical today, but if the arco copy
  disappears, `ARCO_PYTHONPATH` and every `PYTHONPATH=` recipe in this
  doc must repoint at the o2 checkout.
- **mm-tuneshroom** — the instrument app and browser simulator. Its web build
  deploys into the Terrarium's `www/` as an artifact; it never contains
  Terrarium-side logic. (The legacy M1a / Sensor-Check harness stays in
  mm-tuneshroom as a working reference until this stack reproduces its behavior;
  nothing was ported.) As of the telemetry-capture slice the repos also share
  a second wire contract, `docs/telemetry-trace-schema.md` — the
  `/game/capture`/`/game/telemetry` shape and the on-disk trace format a
  future mm-tuneshroom capture client implements against, same
  keep-both-in-sync pattern as `devicelink/protocol.py` ↔
  `lib/link/envelope.dart`. Only the server half (this repo) exists yet; the
  phone-side capture client, the derived tap/shake threshold definitions, and
  the simulator preset buttons that would consume a captured trace are
  separate, later slices (design:
  `docs/superpowers/specs/2026-08-07-sensor-telemetry-capture-design.md`).
- **mm-fairyring** *(planned)* — the cloud broker for RenQuest integration. This
  repo's `uplink/` is the Terrarium-side half, implemented and tested now
  against a protocol contract a future fairyring can implement independently.
  The broker itself does not exist. Chain: Terrarium uplink → mm-fairyring →
  RenQuest trigger.
- **Lux Aeterna** — the lighting renderer (see boundary rule 3). As of PR #5
  the repos also share a **wire contract**: the per-role config blobs Control
  composes carry `light_manifest` in luxaeterna's light-manifest v2 shape
  (parsed device-side by `LightManifest.from_dict`; ratified in luxaeterna's
  2026-07-22 session-lifecycle spec §9, adopted here in
  `docs/superpowers/specs/2026-07-22-light-manifest-v2-adoption-design.md`).
  Two facts landed by luxaeterna's PR #14 (merged 2026-08-20) that this
  repo's harness work depends on: the WebSim canvas now fits a LINEAR
  surface of any pixel count in one fitted row (before, `pos()`'s fallback
  drew a fixed 24 px pitch on a 320 px canvas, so pixel 12 landed at x=328
  and 852 of an 864 px Room surface were drawn off-canvas -- every Room
  canvas "visual check" before this fix was looking at 12 of N pixels), and
  `WebSimBackend` replays its last frame to a client that connects after a
  send (before, a one-shot consumer like `--identify-blocks` always showed
  a black canvas, because the human necessarily opens the printed URL after
  the single frame was sent). The same PR also added `SurfaceCapability`
  **zone-coverage validation**: zones must account for the surface, checked
  at construction. mm-terrarium's `harness/room_surface.py` adapters
  already conform (verified: the 1076-test suite is green against it), but
  any future hand-built capability that under-covers its `pixel_count`
  now fails loudly in luxaeterna instead of silently truncating pixels.

## Not yet built / deferred

Kept explicit so the doc doesn't over-claim:

- ~~**Timed cues are plumbed but not load-bearing.**~~ **Closed 2026-08-14.**
  See *`devicelink/o2_transport.py`, `control/timed_queue.py`,
  `harness/o2_shroom.py`* above for what landed and *`cue_horizon`'s default
  is measurably too small* below for what is still genuinely open (the
  horizon value itself, not the mechanism).
- **Device frame timing depends on which transport is in use.** On the
  o2lite transport, Control and the device now share one clock by
  construction: `DeviceLinkAgent` stamps frames off `o2lite.time_get()`
  (`harness/terrarium_boot.py`'s `main()` wires this in, since o2litepy is
  a module-level singleton both processes are already guests on), and
  `harness/o2_shroom.py` ticks the device against that same O2 clock. That
  holds over a real network, not just on one machine -- it is the real fix,
  not a workaround. The websocket transport still relies on both processes
  sharing a `time.monotonic` epoch, and that only holds because
  `harness/room_simulator.py` is always spawned as a local subprocess of
  Control; it would not hold for an over-network websocket device.
- ~~**A stale device entry survives an ungraceful disconnect.**~~ **Closed
  2026-08-25.** A device-initiated heartbeat riding the existing
  `/game/hello` verb (no new wire message): `DevicePool` tracks
  `last_seen` off every inbound message, `GameServer.reap_stale()` runs
  every tick from `DeviceLinkAgent.poll()` and removes any device silent
  past `BootConfig.stale_timeout` (default 15s), freeing its role slot
  immediately and reusing the existing closing-fade release path. One
  mechanism for both transports, not two: reading `devicelink/server.py`
  during this design found that the websocket transport did not actually
  propagate a disconnect into engine state either -- `drop_dev()` was
  defined on both transports and called from neither, which this slice
  also fixed. `harness/o2_shroom.py` and `harness/room_simulator.py`
  resend hello on a timer (`--heartbeat-interval`, default 5s); the real
  `mm-tuneshroom` Dart client needs the same change and does not have it
  yet -- a cross-repo follow-up, not a gap in this repo. Room-class
  devices are explicitly excluded from reaping (Room liveness is a
  separate, not-yet-designed question -- see `RoomBindingRegistry.save()/
  load()` a few entries below, which is the same kind of open question).
  See `docs/superpowers/specs/
  2026-08-25-device-liveness-detection-design.md`.
- **`cue_horizon` is measured as of 2026-08-14, and 60 ms turned out to be
  right. The belief that it was far too small was an artifact.** This
  supersedes the earlier claim here that the live 2026-08-13 run proved the
  default too small.

  Live o2lite run against a real Arco, **2418 frames**, taken with
  `--horizon 0` so nothing could be held back and the number is genuine
  one-way delivery:

  | p50 | p95 | p99 | p99.9 | worst |
  |-----|-----|-----|-------|-------|
  | 4.5 ms | 9.3 ms | **11.8 ms** | 38.6 ms | 80.2 ms |

  42 of those samples came back very slightly negative, which is the two
  clocks agreeing to well under a millisecond rather than a systematic
  offset. The path is roughly **5 ms typical, 12 ms at p99** — not the ~67 ms
  previously recorded.

  The horizon covers the whole gesture-to-display chain, not just that hop:
  Control's 44 Hz render tick (22.7 ms of quantization) + delivery (11.8 ms
  at p99) + the device's own tick (~5 ms) ≈ **40 ms**, so the existing 60 ms
  covers it with ~20 ms of headroom for the jitter tail. p99 rather than
  worst-case is deliberate: the horizon is fixed added latency on *every*
  cue, so sizing it to the worst frame ever seen taxes every gesture in the
  room for one hiccup.

  **Why the old numbers were wrong, because the trap is still live.** O2
  honors the timestamp and delivers each frame **at** `when`. A device-side
  queue that re-checks the deadline on arrival therefore always finds it a
  few ms past due, *at any horizon*. Measured directly: **93.3% clamped at a
  150 ms horizon and 95.6% at 300 ms**, with lateness pinned near +3 ms and a
  floor near −2 ms in both runs. Doubling the horizon doubled the apparent
  "latency" (154 ms → 304 ms) and did not reduce clamping, which also rules
  out a clock offset — an offset would shrink as the horizon grew. The 2026-
  08-13 "~67 ms" was simply 60 ms of horizon plus ~6 ms of that overhead, and
  the 762-of-820 clamp rate was the same artifact.

  Method for anyone re-running this: **measure at `--horizon 0`**, which is
  the only setting where nothing is held and lateness is real delivery.
  Measuring at a generous horizon looks safe and is exactly what produced the
  wrong answer. The tooling is `TimedQueue.lateness` (bounded, so a Radxa
  cannot leak) → `ShroomClient.lateness` → `harness/sync_bench.py`
  (mean/p95/p99/worst, and it finally has the `main()` that
  `terrarium_boot --horizon` has always told you to run), with
  `harness/o2_shroom.py --control-horizon --samples-out` and the same flags
  on `harness/room_simulator.py`.

  Every figure here is a **dev-box figure**: the venue box does not exist.
- **The clamp counter does not report a wrong horizon on the o2lite path.**
  The timed-cue design spec, and this doc until 2026-08-14, treated a rising
  `DeviceLinkAgent.clamped` / `ShroomClient.clamped` as the production signal
  that `cue_horizon` is too small. Because O2 delivers at `when` (above), the
  counter **saturates at 93–96% regardless of the horizon** and carries no
  information about it there. It still means what the spec says wherever
  nothing else schedules delivery: the websocket transport, and Control's own
  room cues. Read the `lateness` distribution instead when the question is
  whether the horizon is right.

  This also raises a real design question, deliberately not answered here:
  if O2 already schedules delivery, the device-side `TimedQueue` is largely
  redundant on that path, and "one gesture, one shared `T`" may be enforced a
  layer lower than the design assumed.

  A separate, same-day gesture-driven live verification of the
  load-bearing-timed-cues slice (default 60 ms horizon, not `--horizon 0`, so
  not a clean latency measurement) read a device-side clamp count of **1405**,
  then **1081**, across two runs. Read against the finding above, that is the
  same saturation artifact, not independent evidence the horizon is wrong.
- **A device's clock-sync to Arco after Control has connected is
  unreliable -- SOLVED 2026-08-20, and it was never headless-specific.**
  The intermittent-sync half of this entry was mm-terrarium's own
  `_wait_in_setup` never draining Arco's pty (see the operator/harness
  handoff slice above): a held stack froze its hub, and any device
  syncing during the hold stalled until the hold expired. "Headless"
  correlated only because spawned devices always joined during holds and
  buffer-fill time varied run to run -- note the 2026-08-14 measurement
  below, where the 1-of-3 that synced did so at O2time 30, early in a
  90 s hold before the buffer filled. With the hold draining, headless CI
  runs green (first ever, runs/20260820-161425) and interactive devices
  sync in seconds. What follows is preserved as the measurement record,
  and the second half -- pyarco's reset killing sockets of clients that
  connected before it -- remains real and upstream, unchanged. pyarco's `arco.initialize()` unconditionally calls
  `reset()`,
  which sends `/host/clear`; from that moment a **new** o2lite client hangs
  in `o2_shroom`'s `while o2lite.time_get() < 0` loop. Isolated: a lone
  client syncs against a bare Arco in **0.6 s**. Against the same Arco after
  one pyarco `initialize()`, sync is intermittent, not deterministic:
  measured via `harness/run_stack.py` on 2026-08-14, **1 of 3 runs synced**,
  at O2time 30.04. Two or three plain o2lite clients coexist fine, so it is
  neither a client-count limit nor contention -- it is the reset
  specifically.

  The other half: a client that synced **before** the reset keeps reporting
  a valid `time_get()` while its socket is dead, so it sends into the void.
  Measured: such a device sent **120 joins over 240 s** and Control received
  none of them, with nothing in Arco's `o2debug.log` after the point Control
  came up. When this entry was written that was silent and unnoticeable; it
  no longer is. `verify_service_ownership` (`devicelink/o2_transport.py`)
  landed in PR #24, after this entry was written, and a dead socket now
  fails loud with `FATAL: service ... is not routed back to this process`.
  The **defect** is unchanged; its **observability** is not -- do not read
  this as fixed. So there is no ordering that reliably works from a cold
  start: connect first and the socket is killed (now diagnosable, not
  fixed), connect after and sync is unreliable -- 1 of 3 in the measurement
  above, so 2 of 3 still fail.

  Pressing Arco's `(S)tart` key after the reset **does** restore sync
  immediately (`/host/run received. Starting audio devices.`, then 0.6 s),
  which is why `--arco-start-audio` exists. It is off by default and is not
  a general fix: `(S)tart/Stop` is a toggle Arco gives no way to read, so on
  a boot where audio has already come back up it *stops* audio instead. This
  is an upstream Arco/pyarco question, the same family as the documented
  "only the first client after an Arco server start gets working audio"
  trap, and it is not something this repo can fix.

  Why 2026-08-13 worked and 2026-08-14 did not resolved itself with the
  2026-08-20 root cause: the 08-13 runs joined before the hold's pty
  buffer filled, the 08-14 ones after. A live o2lite run is routine now;
  when one does fail, check `o2debug.log` first -- `dropping message
  because service was not found` means Control was not up yet (or a
  device's own service announcement was lost; the device now re-verifies
  on reconnect), and silence means the socket is dead (still the
  upstream reset behavior).
- **A refused o2lite service announcement is unobservable from the client.
  Not a defect: this is O2 working as designed** (Roger, 2026-08-17). The
  host will not forward messages to two providers of the same service name,
  so one must win; full O2 picks the highest IP+port lexicographically, and
  **o2lite keeps no fallback list and does not prioritize at all, so it is
  first-come-first-serve**. Roger explicitly blessed this repo's workaround:
  "the detection method described (send a message to the service and time out
  if you don't receive it) is OK", so `verify_service_ownership` is sanctioned
  rather than a hack. **Do not expect an upstream fix**, and treat a service
  collision here as a design question on this side rather than a bug to report.
  For the record of what was actually observed: Control offers `actl,game`, the
  Room simulator offers `sim-room`, and players offer their own dev ids, so no
  two live processes claim one name by design; the collision that prompted the
  investigation was an *orphan* re-claiming `sim-room` on o2litepy's automatic
  reconnect, which is why the guards below are about orphan lifetime rather
  than about naming. The client-side silence stands as a property to design
  around. `/_o2/*/sv` is fire-and-forget: O2 refuses a second claimant
  (`o2/src/bridge.cpp:231-237`), logs the drop on the **hub**, and offers
  the client no acknowledgement, no error callback and no way to query
  whether a registration took. A client that loses a service race
  clock-syncs and is indistinguishable from a healthy one while everything
  addressed to it is delivered to whoever won.
  `devicelink/o2_transport.py`'s `verify_service_ownership` works around
  this with a self-addressed round trip; it does not fix it. Upstream in
  O2. (This is the O2-layer mechanism behind the Room-simulator service
  collision described above -- see the third bug in the o2lite section --
  which `--exit-with-parent` and the extended shutdown guarantees close on
  the mm-terrarium side, but the underlying silence at the O2 layer stays
  open.)
- ~~**o2litepy's discovery has no ensemble filter at all.**~~ **Fixed
  upstream 2026-08-17** (`rbdannenberg/arco` commit `379424e`, merged into the
  local checkout; `o2litepy/o2lite_disc.py` now stores the ensemble and
  `py3discovery.py` filters on it). Roger: "real, only in the Python port, and
  is now fixed and tested in the regression tests". **Verified in source, not
  yet re-verified live**, and properly closing it means re-running the original
  reproduction, and the venue consequence below needs two O2 hosts to
  exercise. The description of the original defect follows.

  **The defect as reported.**
  `o2litepy/o2lite_disc.py:24` takes `ensemble` as a constructor argument
  and never stores it, and `py3discovery.py:74` browses
  `_o2proc._tcp.local.` and appends every host it resolves. So an o2lite
  client joins whatever O2 host mDNS offers first: any ensemble, any
  machine on the LAN. Reproduced 2026-08-14, an `--ensemble arco` client
  registering its service on a host whose ensemble was something else
  entirely. Venue consequence: two Terrariums on one network would
  cross-connect today, which the "one Terrarium per room" model assumes
  they do not. Deserves an upstream report to Roger.
- **`ArcoProcess` cannot spawn Arco without a controlling TTY — solved, as
  an opt-in.** Arco's curses init opens `/dev/tty` and fails with `Could not
  open /dev/tty. Initialization Failed!` under a plain `Popen` whose stdio is
  a pipe or socket, after which `wait_ready` times out into a clean
  `BootFailure`. `script` does not rescue it.

  `control/arco_process.py`'s `pty_popen` does: `pty.fork()` makes the child
  call `setsid()` and adopt the pty slave as its **controlling terminal**,
  which is what makes `/dev/tty` resolvable at all. It rides `ArcoProcess`'s
  existing `popen=` seam, so the default path is untouched, and
  `harness/terrarium_boot.py --arco-pty` turns it on. Two details each cost a
  **silent** failure -- curses exits with no diagnostic, so it reads as a
  hard incompatibility: `TERM` must be set, and the pty needs a non-zero
  window size via `TIOCSWINSZ` (a fresh pty is 0×0). Owning the pty master
  also makes Arco's console keys reachable (`_PtyProcess.write_console`),
  which is its only control surface -- its own `doc/server.md` documents no
  message-based equivalent, which is why `shutdown()` resorts to SIGTERM.

  Two further things bite a cold headless boot, both now flagged:
  `--arco-ready-timeout` exists because the **first** readiness probe against
  a cold Arco can take ~18 s (it connects, then pyarco's `reset()` times out
  after 5 s) while the second succeeds instantly, so the 15 s default expires
  inside probe #1 and boot fails with Arco perfectly healthy. And
  `--arco-settle-seconds` exists because that failed probe adds a **second**
  `/host/clear`; the extra teardown can leave `arco.output` None, and
  `ArcoSynthPool.start()` then dies with `'NoneType' object has no attribute
  'ins'`. Settling first makes probe #1 succeed so only one reset happens.

  Startup is therefore no longer the blocker on a headless run. Device clock
  sync is — see the entry above.
- **The websocket device wire is still the default.** `--transport o2lite` is
  opt-in because it requires a running Arco. Both transports are maintained.
- **Real ugen graph-building on Arco** has a first, provisional slice: the
  Tuneshroom audio demo builds one `Flsyn` and up to 16 voices, driven by a
  role's `ugen_manifest` v0. Still unbuilt: per-role synthesis beyond
  FluidSynth, the real Flsyn-parameterizing manifest schema, audio over the
  device wire, and **real scoring** (`on_complete()` is still a stub hook).
  (`light_manifest` is no longer a placeholder — v2 schema frozen, validated
  at load — but nothing *sends* the composed `/ie<N>/role` blob yet: the
  o2lite transport that reads `JoinResult.config` is still unbuilt; the Arco
  cue path that plays the welcome audio half now exists in `control/audio.py`.)
- ~~**Real Bits beyond `TestBit`.** No production Bit exists.~~ **Closed
  2026-08-20** by MetronomeBit (see *Landed subsystems*). A general scoring
  framework is still absent; MetronomeBit reports through `result()` only.
- ~~**Bit-declared triggers, cue scripts and conditions (Spec B).**~~ **Closed.**
  See *Bit-declared triggers, cue scripts and conditions* under Landed
  subsystems above. Live-verified against a real Arco: NOT YET DONE. Offline
  suite only.
- ~~**A real venue Room is N light fixtures, not one (Spec C).**~~ **Closed.**
  See *The N-fixture Room (Spec C)* under Landed subsystems above. Live-
  verified against a real Arco: NOT YET DONE. Offline suite only.
- **The Tuneshroom LED wire cannot reach the white die.** The hardware is
  SK6812 Mini **RGBW** (4 channels, chosen for its dedicated white die and the
  clean diffusion that buys), but `protocol.leds_event` ships **36 ints
  (12 px × GRB)** and `shroom_capability` declares `color_order: "GRB"`. So the
  white channel is unreachable today and any device-side driver has to hardcode
  `w=0`. Widening the wire to 48 is the likely answer, but it is a coordinated
  change across `devicelink/protocol.py`, `shroom_capability`, the WebSim
  backend, and the Dart counterpart in `mm-tuneshroom/lib/link/envelope.dart`
  (the protocol docstring says explicitly that the Dart side changes with it) —
  and it crosses into simulator territory, so it is **an open decision, not a
  bug to quietly fix.**
- **No hardware exists.** Every module under *venue-array and device tooling*
  above is written against hardware nobody has plugged in. Treat all of it as
  unexercised until the student track's Gate 2 (2026-10-16) records otherwise.
- **The mm-fairyring broker** (the uplink's other end) and its auth/identity /
  venue-ID scheme.
- **Directories still unbuilt:** `arcoserver/` (Arco build config —
  dspmanifest/prefs), `www/` (simulator web root), and `deploy/` (venue
  provisioning/networking) are in the README's planned layout but not created.
- **Operator command interface beyond the console** (physical control, a
  Registration Node convention) remains a later decision; the console is the
  first concrete answer for a web panel, and as of 2026-08-17 it is actually
  openable during a run and shows the Room.
- **A real-hardware Room backend, for either RoomType.** Both TEST and DEMO's
  only backend today is the browser simulator (`harness/room_simulator.py`,
  `harness/o2_shroom.py`); nothing implements the same seam against actual
  Tuneshroom/array hardware yet. For DEMO specifically, `RoomBlock`
  boundaries are declarative-only (see the *`RoomBlock`, and the DEMO room*
  section above) — no real Art-Net/multi-controller output backend exists;
  `harness/array_smoke.py` drives the real 864 px array standalone but is not
  wired into `boot()`.
- ~~**Nothing drives the Room's light during a live run.**~~ **Closed
  2026-08-14** by `Bit.cues(at)` (see the `Bit` interface bullet above);
  `TestBit`'s implementation and its live confirmation are described in the
  *Control on o2lite, and timed cues* status callout above.
- **`RoomBindingRegistry.save()`/`.load()` are implemented and tested but not
  called from `boot()`.** A restarted Terrarium does not yet reconnect a
  previously-bound physical Room device automatically; every restart
  currently requires a fresh admin-armed tap.

## Design docs (in-repo, authoritative)

- Canonical architecture:
  [`docs/control-gameserver-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/control-gameserver-design.md).
- Bootstrap:
  [`docs/superpowers/specs/2026-07-18-mm-terrarium-bootstrap-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-18-mm-terrarium-bootstrap-design.md).
- First slice (lifecycle engine + TestBit):
  [`.../2026-07-20-control-gameserver-first-slice-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md).
- Uplink:
  [`.../2026-07-20-terrarium-uplink-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md).
- Console:
  [`.../2026-07-21-terrarium-console-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-21-terrarium-console-design.md).
- Tuneshroom audio:
  [`.../2026-08-06-tuneshroom-audio-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md).
- Student hardware track (the physical build feeding the 2026-12-04 show):
  [`.../2026-08-06-student-hardware-track-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-06-student-hardware-track-design.md)
  and its plan
  [`.../plans/2026-08-06-student-hardware-track.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-06-student-hardware-track.md).
  These are where the venue-box, LED-array and Tuneshroom builds, their gates,
  and their acceptance criteria live.
- Room concept and load sequence (spec 1 of 2 toward the Terrarium
  Visualization Simulator):
  [`.../2026-08-10-room-concept-and-load-sequence-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md)
  and its plan
  [`.../plans/2026-08-10-room-concept-and-load-sequence.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-10-room-concept-and-load-sequence.md).
- Terrarium Visualization Simulator (spec 2 of 2, TEST room):
  [`.../2026-08-10-terrarium-visualization-simulator-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md)
  and its plan
  [`.../plans/2026-08-10-terrarium-visualization-simulator.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-10-terrarium-visualization-simulator.md).
- Control on o2lite, and timed cues:
  [`.../2026-08-12-control-o2lite-and-timed-cues-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-12-control-o2lite-and-timed-cues-design.md)
  and its plan
  [`.../plans/2026-08-12-control-o2lite-and-timed-cues.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-12-control-o2lite-and-timed-cues.md).
  Read the spec's *What is built but not yet load-bearing* section before
  extending anything that touches cue timing.
- Making timed cues load-bearing (closes the gap the slice above left open):
  [`.../2026-08-14-load-bearing-timed-cues-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-14-load-bearing-timed-cues-design.md)
  and its plan
  [`.../plans/2026-08-14-load-bearing-timed-cues.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-14-load-bearing-timed-cues.md).
  Live-verified against a real Arco 2026-08-14; read its section 2 (the
  timing model) before touching cue timing anywhere in this repo.
- Room panel, and the Room's own fixtures (Spec A of two; Spec B covers
  triggers, cue scripts, conditions and firing, see below):
  [`.../2026-08-17-room-panel-and-room-fixtures-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md)
  and its plan
  [`.../plans/2026-08-17-room-panel-and-room-fixtures.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-17-room-panel-and-room-fixtures.md).
  Its Status line records what was live-verified and what was not.
- Bit-declared triggers, cue scripts and conditions (Spec B of two):
  [`.../2026-08-17-bit-declared-triggers-and-cue-scripts-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-17-bit-declared-triggers-and-cue-scripts-design.md)
  and its plan
  [`.../plans/2026-08-17-bit-declared-triggers-and-cue-scripts.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-17-bit-declared-triggers-and-cue-scripts.md).
  Live-verified against a real Arco: not yet done, offline suite only.
- Teardown order, and a one-command Arco stack runner:
  [`.../2026-08-14-teardown-order-and-stack-runner-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-14-teardown-order-and-stack-runner-design.md)
  and its plan
  [`.../plans/2026-08-14-teardown-order-and-stack-runner.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-14-teardown-order-and-stack-runner.md).
- The N-fixture Room (Spec C, following Spec A and Spec B above -- the slice
  Spec B's section 4.2 deferred by name):
  [`.../2026-08-18-n-fixture-room-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-18-n-fixture-room-design.md)
  and its plan
  [`.../plans/2026-08-18-n-fixture-room.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-18-n-fixture-room.md).
  Live-verified against a real Arco: not yet done, offline suite only.


Game-design background (RenQuest integration, Bit scoring/loop rules, hardware)
lives in MM-internal docs (`mm-documents/mm-shrooms-app/`) and is not required to
work on this architecture.
