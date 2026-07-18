# mm-terrarium

The **Terrarium Server**: the per-installation venue server for Musical
Mycology Shroom installations. One Terrarium per room: a capable computer plus
LED display and speakers, hosting two processes:

- the **Arco server** (O2 hub: HTTP, websockets, o2lite; all synthesis for the
  room), and
- the **Control+GameServer** (full O2 peer, services `game` and `actl`): Bit
  runtime, registration and role assignment, scoring, adjudication.

Interactive Elements (hardware Tuneshrooms over o2lite, phones over
websockets) connect to the Arco server; gameplay traffic addresses
`/game/...`; only Control writes to `/arco`.

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
arcoserver/  Arco server build config for the Terrarium (dspmanifest.txt, prefs)
www/         deployed web root (simulator build ships here from mm-tuneshroom)
deploy/      venue provisioning and installation networking
docs/        repo docs; specs under docs/superpowers/specs/
```

No implementation yet: this repo starts at the design stage. First code lands
via the spec in `docs/superpowers/specs/`.

## Relationship to other repos

- **arco / o2** (rbdannenberg upstream, Musical-Mycology forks): the synthesis
  engine and transport this server builds on.
- **pyarco**: Python control layer used by Control+GameServer.
- **mm-tuneshroom**: the instrument app and browser simulator. Its web build
  deploys into `www/` as an artifact; it never contains Terrarium-side logic.
- **mm-fairyring** (planned): cloud broker for RenQuest integration; the
  Terrarium's uplink module talks outbound to it, never in the real-time loop.
