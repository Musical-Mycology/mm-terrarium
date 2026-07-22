# mm-terrarium — the per-room venue server (Arco + Control+GameServer)

The **Terrarium Server**: the per-installation venue box for Musical Mycology
Shroom installations. **One Terrarium per room** — a capable computer plus an
LED display and speakers — hosting **two processes on the same box**:

- the **Arco server** (the O2 hub: HTTP, websockets, o2lite; **all** room
  synthesis), and
- the **Control+GameServer** (a full O2 peer, services `game` and `actl`): the
  Bit runtime, registration and role assignment, scoring, and adjudication.

Interactive Elements — hardware Tuneshrooms over o2lite, phones over websockets
— connect to the Arco server; all gameplay traffic addresses `/game/...`; and
**only Control writes to `/arco`**. This repo is `mm-terrarium`'s canonical
service doc; the authoritative *architecture* is in-repo at
[`docs/control-gameserver-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/control-gameserver-design.md)
(the official path forward as of 2026-07-18, developed with Roger Dannenberg).
See `MM_ARCHITECTURE.md` (MM-internal) → *Per-service summary* for the cross-repo slot, and
`MM_HARDWARE_DESIGN.md` (Tier 4 — Terrarium) for where the box sits in the
hardware fleet.

> **Status: early, offline/test-only.** Everything landed so far is pure Python
> that runs and tests **fully offline** — no O2, Arco, pyarco, Lux Aeterna, or
> fairyring dependency yet. The lifecycle engine, its remote uplink, and the
> local admin console all exist and are exercised end-to-end against fakes; the
> real-time audio/lighting outputs and real Bits do not exist yet. Keep this doc
> honest about that line — see *Not yet built / deferred* below.

## What it is, in one picture

```
Phone browser --ws--+
                    v
Shroom (o2lite) --> +--------------+    full O2, same box
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
  `result()` (completion payload) and `status()` (generic key/value read-out).
- **Observer hooks:** a **multi-observer** list (`add_observer()` with
  notify-all) fires `on_state_change` / `on_registration_change` /
  `on_devices_change`, plus a single transport-owned `on_release` sink. This is
  the shared seam the uplink and console both attach to.
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
a **real light-manifest v2 declaration** (one instrument, note + CC lanes) plus
a welcome pair, the declaration that formally froze the v2 schema; `jammer`
keeps the empty defaults so the no-light path stays exercised.

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

## Relationships to other repos

- **arco / o2** (rbdannenberg upstream, Musical-Mycology forks) — the synthesis
  engine and O2 transport this server builds on. The Arco server *is* the room's
  O2 hub and sole synthesizer.
- **pyarco** — the Python control layer Control+GameServer will build ugen
  graphs through. **No dependency yet** (this slice does zero graph-building);
  its source-of-truth (submodule vs. pinned sibling) is Roger Dannenberg's open
  decision — see *Not yet built* below.
- **mm-tuneshroom** — the instrument app and browser simulator. Its web build
  deploys into the Terrarium's `www/` as an artifact; it never contains
  Terrarium-side logic. (The legacy M1a / Sensor-Check harness stays in
  mm-tuneshroom as a working reference until this stack reproduces its behavior;
  nothing was ported.)
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

- **Real O2lite/pyarco transport wiring.** Control connects via o2lite in the
  design framing, but nothing talks to a live O2 network or Arco server yet —
  the whole suite runs against fakes (`FakeO2Lite`-style transport,
  `FakeTransport`, a localhost websocket for the console server).
- **Real ugen graph-building on Arco** and **real scoring.** `ugen_manifest`
  is still a placeholder and `on_complete()` scoring is a stub hook.
  (`light_manifest` is no longer a placeholder — v2 schema frozen, validated
  at load — but nothing *sends* the composed `/ie<N>/role` blob yet: the
  o2lite transport that reads `JoinResult.config`, and the Arco cue path that
  plays the welcome audio half, are both unbuilt.)
- **Real Bits beyond `TestBit`.** No production Bit exists.
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

Game-design background (RenQuest integration, Bit scoring/loop rules, hardware)
lives in MM-internal docs (`mm-documents/mm-shrooms-app/`) and is not required to
work on this architecture.
