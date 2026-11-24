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

## Layout

```
control/       Control+GameServer: lifecycle engine, Bit runtime, Rooms, audio
bits/          the in-repo Bit packages (bit.toml + class each); see --list-bits
devicelink/    Control's device-facing transport: the game service on the Arco hub (o2lite)
harness/       boot, the stack runner (run_stack), simulated Testshrooms, benches
console/       Terrarium Console: local admin panel (HTTP+websocket, no build step)
uplink/        outbound remote command/telemetry link to a future mm-fairyring broker
capture/       labelled sensor telemetry capture
contract_kit/  device contract scenarios and recordings
instruments/   instrument definitions (TOML)
rooms/         Room definitions (TOML)
profiles/      venue launch profiles
tools/         bundle_bit, export_contract, model baking
arcoserver/    Arco launch dir: arco_server_prefs.json (HTTP root www/, port 8080); Arco's cwd
www/           what Arco serves: o2ws.js, the clock-sync page; the guest app build lands in www/app/
docker/        the terrarium-dev container
docs/          repo docs; MM_TERRARIUM.md is the service deep-dive; specs under docs/superpowers/specs/
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

## Setup

### Repos you need

Clone these side by side under `~/projects`. Arco and O2 come straight from
Roger Dannenberg's repos. The harness finds `arco` as a sibling of this
repo's main checkout, and Arco's cmake finds `o2` as a sibling of `arco`.

| Repo | What it provides |
|---|---|
| `Musical-Mycology/mm-terrarium` | this repo: Control+GameServer, the harness, the Console and the in-repo Bits |
| `rbdannenberg/arco` (Roger's; clone this as `origin`) | the Arco server (room O2 hub and synth), plus `pyarco/` and `o2litepy/`, reached by `PYTHONPATH` |
| `rbdannenberg/o2` | O2, which Arco links against. Not in the Musical-Mycology org |
| `Musical-Mycology/luxaeterna` | the light renderer, installed editable into `.venv` |

The `mm` clone-missing-repos sweep skips `arco` and `luxaeterna`, so clone
them by hand. The standalone `Musical-Mycology/pyarco` repo is archived; use
the copy inside `arco`.

**mm-tuneshroom is optional.** Every Bit under `bits/` runs against
simulated Testshrooms (`./smoke-test.sh`, driven by the Console's gesture
panel), so no Tuneshroom checkout is needed to build the server or play
Bits. Add it only for:

- **phones joining from a browser**: the guest app is mm-tuneshroom's
  Flutter web build, staged with `--web-build /path/to/mm-tuneshroom/build/web`;
- **instrument-shipped Bits** such as its `GlowBit`, which live in
  mm-tuneshroom's `bits/` and load only once that directory is added to
  `bit_paths` in `terrarium.toml`.

`mm-devshroom` is ESP32 firmware for real hardware and is not needed on the
server.

### Dev container (Linux / WSL2)

The `terrarium-dev` image bakes in o2, the patched Arco server and the venv,
so none of the native steps below are needed. Install Docker Engine inside
WSL2 Ubuntu (not Docker Desktop; setup in
[`docker/README.md`](docker/README.md)), then:

```bash
docker run --rm ghcr.io/musical-mycology/terrarium-dev:main launcher > terrarium-dev && chmod +x terrarium-dev
sudo mv terrarium-dev /usr/local/bin/    # once, so `terrarium-dev` is on PATH
terrarium-dev --headless run --room TEST    # Arco + the TEST Room; drop --headless for audio
terrarium-dev test               # pytest, then node --test
```

The image is not published yet (Phase 2); until then build it with
`docker/build.sh` and add `--tag local`. On macOS the container can run only
`test`, `clean` and `selfcheck` (no host Avahi socket), so a Mac uses the
native build. See `docker/README.md` for networking, audio and pins.

### Native build (Linux / WSL2)

The authoritative, step-by-step version (with the reasons behind each
step) is *Linux / WSL host setup* in
[`docs/MM_TERRARIUM.md`](docs/MM_TERRARIUM.md). In short:

1. **apt packages**, and enable systemd in WSL so `avahi-daemon` runs (Arco
   never advertises without it, and the harness preflight refuses to start):

   ```bash
   sudo apt install cmake cmake-curses-gui portaudio19-dev libavahi-client-dev \
     libsndfile1-dev libfluidsynth-dev libportmidi-dev fluid-soundfont-gm \
     libncurses-dev libogg-dev libvorbis-dev libflac-dev libopus-dev \
     libglib2.0-dev avahi-daemon
   ```

2. **Patch and build o2.** The patch is required on every host: unpatched O2
   aborts Arco when a device drops with a ping in flight.

   ```bash
   git -C ~/projects/o2 apply "$PWD/docs/upstream/o2-csget-bad-id.patch"   # from this repo's root
   cd ~/projects/o2 && cmake -S . -B Release -DCMAKE_BUILD_TYPE=Release \
     -DTESTS_BUILD=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
     && cmake --build Release -j"$(nproc)"
   ```

3. **Configure Arco** with the Ubuntu `libraries.txt` and the Linux build
   patch (pending upstream with Roger; never commit it to arco or its MM mirror):

   ```bash
   cp docs/upstream/arco-libraries-ubuntu.txt ~/projects/arco/apps/common/libraries.txt
   git -C ~/projects/arco apply "$PWD/docs/upstream/arco-linux-build.patch"
   ```

4. **Build the Arco server.** The second configure is required (the first
   omits the MIDI service and the link fails):

   ```bash
   cd ~/projects/arco/apps/pytest
   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
   cmake -S . -B build
   cmake --build build -j"$(nproc)"
   ln -s pytestserver server    # the harness expects `server`
   ```

5. **The venv**, from this repo's root:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements-dev.txt
   .venv/bin/python -m pip install -e "$HOME/projects/luxaeterna[websim]"
   ```

6. **Smoke check:** `./terrarium.sh --room TEST --seconds 45` should exit 0
   with "room loaded: TEST". Dozens of ALSA "cannot find card" lines in
   `arco.log` on WSL are harmless.

Real ESP32 devices need the host on the LAN: WSL2's default NAT mode hides
it (use mirrored networking on Windows 11; see the deep-dive, step 9).

### Native build (macOS)

The Dec 4 show machine is a Mac. This repo does not carry a macOS recipe:
build o2 and Arco per Arco's own `doc/building.md`, with a machine-local
`apps/common/libraries.txt` pointing at your PortAudio, libsndfile,
FluidSynth and codec libraries. Two parts are the same as Linux: apply
`docs/upstream/o2-csget-bad-id.patch` to o2 before building (skip the
Linux-only Arco patch), and end with an `arco/apps/pytest/server` symlink
to the built `pytestserver`. Then create the venv as in step 5.

### Audio

Room audio needs a General MIDI soundfont. `harness/arco_synth.py` uses
`$MM_SOUNDFONT` if set, else `FluidR3_GM.sf2` from a sibling `fluidsynth`
checkout's `sf2/`, else `/usr/share/sounds/sf2/FluidR3_GM.sf2` (Ubuntu's
`fluid-soundfont-gm`).

## Quick start: a clean Terrarium

```
./terrarium.sh              # Console at http://127.0.0.1:8772/, no Room, no Bit
./terrarium.sh --room TEST  # Arco + the TEST Room up, no Bit
```

`./terrarium.sh` wraps `harness/run_stack.py --no-bit`: no Bit is
loaded and no Testshrooms are spawned. From the Console, **Load** picks a
Bit and the Room it should run in: from `NO_ROOM` the first Load brings
the chosen Room up (about 15 s of Arco spawn); a different Room than the
active one needs a restart: `./terrarium.sh --room NAME` (pyarco can
only ever connect to one Arco per process). Once a Bit is loaded the
Console's **Join** card shows, per registration node, the guest URL, a
QR code for a phone on the same LAN, and a `flutter run` line to paste
into an mm-tuneshroom checkout for a Chrome Testshroom. The same URLs
print on stdout as
`JOIN_URL:` lines. Pass `--web-build /path/to/mm-tuneshroom/build/web` to
stage the guest app under `/app/`; without it the URL serves no page.
Native iOS/Android and the Radxa app cannot connect until mm-tuneshroom's
FFI o2lite link lands.

`./smoke-test.sh` remains the Bit-first launcher (spawned Testshrooms, CI
mode, profiles).

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
node --test tests/js/*.test.js   # the Console JS; the bare directory form fails
```

**Use the venv explicitly.** There is no bare `python` on the dev boxes, and
the sibling luxaeterna dev dependency is installed only in `.venv`. Reaching
for `python3` instead collects an import error in
`tests/test_terrarium_boot.py` that looks exactly like a real failure and is
not one; that trap has already cost one debugging detour. A fresh git worktree
has no `.venv` at all, so symlink it:
`ln -s /path/to/mm-terrarium/.venv .venv` from the worktree root.

## Relationship to other repos

- **arco / o2** (Roger Dannenberg's `rbdannenberg/arco` and `rbdannenberg/o2`;
  `Musical-Mycology/arco` is only a mirror, not the source to clone): the
  synthesis engine and transport this server builds on. The Arco server is
  the room's O2 hub and sole synthesizer.
- **pyarco** and **o2litepy**: the Python layers Control drives Arco and the
  hub through, both from the sibling `arco` checkout (`harness/arco_paths.py`;
  `terrarium.sh` honours `ARCO_ROOT` or an existing `PYTHONPATH`).
- **luxaeterna**: the lighting renderer, imported by Control in-process.
- **mm-tuneshroom** (optional): the instrument app and browser simulator.
  Its web build deploys into `www/app/` as an artifact; its `bits/` holds
  Bits that ship with the instrument, loaded through `bit_paths`. It never
  contains Terrarium-side logic.
- **mm-devshroom**: Rev 1 ESP32 Tuneshroom firmware; talks to the server,
  not needed to build it.
- **mm-fairyring** (planned): cloud broker for RenQuest integration. This
  repo's `uplink/` module (the Terrarium-side half) is implemented and
  talks outbound over a websocket, never in the real-time loop; the
  broker itself doesn't exist yet.

## License

MIT; see [`LICENSE`](LICENSE). Arco and O2, which this server builds on, are Roger
Dannenberg's and carry their own terms.
