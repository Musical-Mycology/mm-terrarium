# mm-terrarium

The **Terrarium Server**: the per-installation venue server for Musical
Mycology Shroom installations. One Terrarium per room: a capable computer plus
LED display and speakers, hosting two processes:

- the **Arco server** (O2 hub: HTTP, websockets, o2lite; all synthesis for the
  room), and
- the **Control+GameServer** (an o2lite client of that hub, offering services
  `game` and `actl`): Bit runtime, registration and role assignment, scoring,
  adjudication.

Interactive Elements (hardware Tuneshrooms over o2lite, phones over
websockets) connect to the Arco server; gameplay traffic addresses
`/game/...`; only Control writes to `/arco`.

Arco is the only full-O2 process in the room — Control attaches over o2lite
just like every device, so anything travelling between two clients is relayed
by Arco (`/game/*` and `/ie<N>/*` are 2 hops; `/arco` and `/actl` are 1). See
the design doc's *Message Routing* section.

## Canonical design

[`docs/control-gameserver-design.md`](docs/control-gameserver-design.md)
(official path forward as of 2026-07-18, developed with Roger Dannenberg).
This repo is the canonical home of that doc so all collaborators can reach
it. Game-design background (RenQuest integration, Bit scoring and loop
rules, hardware) lives in MM-internal docs (`mm-documents/mm-shrooms-app/`)
and is not required to work on this architecture.

## Planned layout

```
control/     Control+GameServer package (Python, on pyarco)
bits/        Bit plugin modules (role tables, graph-builders, cues, scoring)
uplink/      Remote command/telemetry link to a future mm-fairyring broker
console/     Terrarium Console: local admin panel (HTTP+websocket)
devicelink/  device-facing websocket transport (simulated Tuneshrooms)
arcoserver/  Arco server build config for the Terrarium (dspmanifest.txt, prefs)
www/         deployed web root (simulator build ships here from mm-tuneshroom)
deploy/      venue provisioning and installation networking
docs/        repo docs; specs under docs/superpowers/specs/
```

`control/` and `bits/` hold the first implementation slice: the
Control+GameServer lifecycle engine (state machine, role/registration data
model) and `TestBit`, a durable reference fixture. `uplink/` adds a
`GameServer` observer (`UplinkAgent`) that makes that engine remotely
drivable over a persistent outbound websocket, tested against a fake
in-process transport plus a real local socket — see
`docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`
and `docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md` for
scope and rationale. Both run entirely offline in tests, with no O2/Arco/
pyarco/fairyring dependency. `console/` adds the Terrarium Console: a
Bit-agnostic local admin panel (served over a single-port HTTP+websocket)
that loads/runs/aborts a Bit and live-monitors lifecycle state,
registration, devices, and per-role audio + light manifests — the durable
front-end fixture every Bit reuses. It attaches to the same engine
observer list as the uplink and runs entirely offline in tests. See
`docs/superpowers/specs/2026-07-21-terrarium-console-design.md`.

It also carries a **Room panel**: the Room's declared light and audio
instruments side by side with each lane's live controller value, plus a
labelled view of the Room's live LED frame. Open it during a run by passing
`--console-port`, which is off by default:

```
.venv/bin/python -m harness.terrarium_boot --console-port 8772
.venv/bin/python -m harness.run_stack --ci --console-port 8772
```

See `docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md`.

It also carries a **Triggers panel**: every trigger a loaded Bit declares,
shown with its condition and its cue-script steps, plus a Fire button for
manual testing. A fire is tagged as either a real gameplay fire or an admin
manual fire, so the two are never confused in the event log. See
`docs/superpowers/specs/2026-08-17-bit-declared-triggers-and-cue-scripts-design.md`.

Run the test suite with:

```
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -v
```

**Use the venv explicitly.** There is no bare `python` on the dev boxes, and
the sibling luxaeterna dev dependency is installed only in `.venv`. Reaching
for `python3` instead collects an import error in
`tests/test_terrarium_boot.py` that looks exactly like a real failure and is
not one; that trap has already cost one debugging detour. A fresh git worktree
has no `.venv` at all, so symlink it:
`ln -s /path/to/mm-terrarium/.venv .venv` from the worktree root.

## Relationship to other repos

- **arco / o2** (rbdannenberg upstream, Musical-Mycology forks): the synthesis
  engine and transport this server builds on.
- **pyarco**: Python control layer used by Control+GameServer.
- **mm-tuneshroom**: the instrument app and browser simulator. Its web build
  deploys into `www/` as an artifact; it never contains Terrarium-side logic.
- **mm-fairyring** (planned): cloud broker for RenQuest integration. This
  repo's `uplink/` module (the Terrarium-side half) is implemented and
  talks outbound over a websocket, never in the real-time loop; the
  broker itself doesn't exist yet.
