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
> Still absent: **fairyring**, real scoring, and any production Bit.
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
  waiting on).
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
keeps the empty defaults so the no-light path stays exercised.

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
  `LED_CHANNELS = 36` matches the current wire (12 px × GRB) — see the RGBW
  mismatch under *Not yet built* below.
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

### `capture/` + `bits/capture_bit.py` — labelled sensor telemetry capture (tool Bit)
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

### `control/simulator_process.py`, `harness/room_simulator.py`, `harness/terrarium_boot.py` — the Terrarium Visualization Simulator (TEST room)
The first concrete `Room` backend — closes the gap the Room-concept slice
above deliberately left open (`RoomBridge` existed but rendered nothing).
Design: [`.../2026-08-10-terrarium-visualization-simulator-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md).
TEST room only; DEMO's simulated venue array is a deferred follow-up.

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
             ROOM resolved to bound dev                      
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
    ┌────────────────────────────┐          
    │3. Room simulator (pop 3rd) │          
    │                            │          
    └────────────────────────────┘          
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
  process, its log path, and the log's tail.

  That second condition is worth stating separately because its absence was
  the one real correctness gap the whole-branch review found. `_hold()`
  originally took the Control tee and never read it, and nothing polled any
  child's exit status, so an unattended `--ci` run in which Control (and
  therefore Arco and every device under it) died seconds into a 45 s hold
  still exited **0**. It now polls every child each tick, mirroring
  `terrarium_boot`'s own `arco.poll() is not None` pattern, and reports a
  `child-exited` stage naming which child died and its code.

  **CI mode is honestly best-effort.** The headless clock-sync defect (see
  *Not yet built / deferred* below, "A device's clock-sync to Arco after
  Control has connected is unreliable -- in a headless run") is upstream
  and unfixed. `run_stack` does not fix it and does not pretend to -- its
  `--help` says so in the same words as here. What it contributes is that
  the failure is bounded and named: a `device-sync` stage failure pointing
  at the device's own log and at `o2debug.log`, rather than a hang.

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
  designed.
- **`harness/markers.py`** -- the readiness contract `run_stack` watches
  for: named constants emitted by `terrarium_boot`/`o2_shroom` and matched
  on both sides by `tests/test_markers.py`. Matching on incidental print
  wording would make a future reworded line a silent hang instead of a
  broken test; promoting the strings to constants is what makes
  stdout-watching honest. Failure markers (`JOIN DENIED:`, `FATAL: service`)
  are matched too, so a failure the child has already diagnosed ends the run
  immediately rather than sitting out the full timeout.
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
branch's start).

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
  same one `control/audio.py` has always followed for pyarco.
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
- **A stale device entry survives an ungraceful disconnect**, and this
  architecture cannot fix it as-is. Control is an o2lite **client**, not the O2
  host, so devices connect to Arco and Control never holds a socket to one.
  o2litepy exposes no per-peer liveness at all: its API is `set_services`,
  `bridge_id` (Control's own link to the host) and `tcp_close`. Closing this
  needs an application heartbeat or a registration expiry, which is a design
  question rather than a bug fix.
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
  unreliable -- in a headless run.** Measured repeatedly on 2026-08-14
  from a non-interactive context. **It does not reproduce from an
  interactive terminal**: the same commands run by hand joined, held a
  role and delivered thousands of frames the same afternoon, which is how
  the figures above were obtained. So this
  is a real and reproducible barrier to automating the measurement, not a
  fault in the o2lite path itself, and the cause of the interactive/headless
  difference is unknown. pyarco's `arco.initialize()` unconditionally calls
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

  Why 2026-08-13 worked and 2026-08-14 did not is unresolved. Treat a
  successful live o2lite run as **currently unreliable rather than routine**,
  and check `o2debug.log` first -- `dropping message because service was not
  found` means Control was not up yet, and silence means the socket is dead.
- **A refused o2lite service announcement is unobservable from the
  client.** `/_o2/*/sv` is fire-and-forget: O2 refuses a second claimant
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
- **o2litepy's discovery has no ensemble filter at all.**
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
- **Real Bits beyond `TestBit`.** No production Bit exists.
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
  first concrete answer for a web panel.
- **`RoomType.DEMO`'s backend.** Only TEST room has a simulator; the venue
  array's simulated (and, later, real-hardware) backend is a deferred
  follow-up spec, not yet written.
- **A real-hardware Room backend.** TEST room's only backend today is the
  browser simulator (`harness/room_simulator.py`); nothing implements the
  same seam against actual Tuneshroom/array hardware yet.
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
- Teardown order, and a one-command Arco stack runner:
  [`.../2026-08-14-teardown-order-and-stack-runner-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-14-teardown-order-and-stack-runner-design.md)
  and its plan
  [`.../plans/2026-08-14-teardown-order-and-stack-runner.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/plans/2026-08-14-teardown-order-and-stack-runner.md).


Game-design background (RenQuest integration, Bit scoring/loop rules, hardware)
lives in MM-internal docs (`mm-documents/mm-shrooms-app/`) and is not required to
work on this architecture.
