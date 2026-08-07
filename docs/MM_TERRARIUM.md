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
> Still absent: **O2/o2lite** (the device wire is a direct websocket to Control,
> not o2lite through Arco), **fairyring**, real scoring, and any production Bit.
> Keep this doc honest about that line: see *Not yet built / deferred* below.

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

## Landed subsystems

All Python, all offline-tested. Run the suite with
`python -m pip install -r requirements-dev.txt && python -m pytest tests -v`.

### `control/` — the Control+GameServer lifecycle engine
The game-launching engine: load a Bit, open registration, run it, score it,
return to a clean waiting state. Landed in the first-slice spec
([`2026-07-20-control-gameserver-first-slice-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md)).

- **State machine:** `IDLE → LOADING → LOADED → SETUP → RUNNING → COMPLETING →
  UNLOADING → IDLE`. SETUP is the waiting-room (registration open); during
  RUNNING **scored roles are denied but jam roles stay open** (an installation
  has casual foot traffic). Control stays **Bit-agnostic** — it never evaluates
  a win condition itself; the Bit signals completion from `update(dt)`.
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
  and yields the generic `"handler error"`.
- **Observer hooks:** a **multi-observer** list (`add_observer()` with
  notify-all) fires `on_state_change` / `on_registration_change` /
  `on_devices_change`, plus **two** transport-owned sinks: `on_release` (one
  call per device during UNLOADING) and `on_light_cue` (added in Slice 2, for
  cues a Bit's verb handler emits). Both are wrapped by the engine, so a
  failing transport cannot wedge it. This is the shared seam the uplink,
  console, and devicelink all attach to.
- **`abort()`** — Control-initiated early termination that force-unloads while
  still running the Bit's `on_complete`/`on_unload` best-effort. COMPLETING and
  UNLOADING are **always reachable even if a Bit hook raises** (deliberate — a
  misbehaving Bit must never wedge Control loaded).

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

Driver: `python -m harness.devicelink_smoke --hold`.

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
  O2 hub and sole synthesizer.
- **pyarco**: the Python control layer Control+GameServer builds ugen graphs
  through. Now a **dev/test-only dependency reached by `PYTHONPATH`**, following
  the luxaeterna precedent: nothing is vendored or submoduled, and
  `control/audio.py` never imports it, so the whole suite still runs offline.
  Its source-of-truth (submodule vs. pinned sibling) remains Roger Dannenberg's
  open decision.
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

- **Real O2lite/pyarco transport wiring.** A device wire now exists
  (`devicelink/`, Slice 2) but it is a **direct websocket to Control, not
  o2lite through Arco** — no live O2 network, no Arco server, no clock sync.
  The whole suite still runs against fakes and localhost sockets.
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
- **pyarco source-of-truth** (submodule-vs-sibling; bootstrap open question #1)
  is Roger Dannenberg's open decision — must be settled before any Bit does real
  graph-building.
- **Operator command interface beyond the console** (physical control, a
  Registration Node convention) remains a later decision; the console is the
  first concrete answer for a web panel.

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


Game-design background (RenQuest integration, Bit scoring/loop rules, hardware)
lives in MM-internal docs (`mm-documents/mm-shrooms-app/`) and is not required to
work on this architecture.
