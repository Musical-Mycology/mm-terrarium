# mm-terrarium: the per-room venue server (Arco + Control+GameServer)

The **Terrarium Server**: the per-installation venue box for Musical Mycology
Shroom installations. **One Terrarium per room** (a capable computer plus an
LED display and speakers) hosting **two processes on the same box**: the
**Arco server** (the O2 hub: HTTP, websockets, o2lite; all room synthesis)
and **Control+GameServer** (an **o2lite client** of that hub, offering
services `game` and `actl`: the Bit runtime, registration and role
assignment, scoring, and adjudication). Arco is the only full-O2 process in
the room; Control attaches over o2lite exactly like every device, so Arco
relays anything travelling between two clients (`/arco` and `/actl` are 1
hop, `/game/*` and `/ie<N>/*` are 2). A full-O2 Control would shorten none
of them; see *Message Routing* in the design doc. **Only Control writes to
`/arco`**: interactive elements and browsers address only `/game/...` and
receive `/ie<N>/...`/`/ui<X>/...` back; Control's own process is the only
one that ever sends to `/arco` (`control/arco_process.py` and
`harness/arco_synth.py` both call pyarco's `arco.initialize()`, both
inside that process). This repo is
`mm-terrarium`'s canonical service doc; the authoritative architecture is
in-repo at
[`docs/control-gameserver-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/control-gameserver-design.md),
developed with Roger Dannenberg.

**Status:**
- Running today: the o2lite device path end to end on contract v3 (the
  Chrome/Flutter guest app and Testshrooms hello, accept the handshake, and
  play over a real Arco; the Rev 1 Tuneshroom firmware is still on v2 and
  gets a jam role every round until mm-devshroom#7 lands); Room
  audio and light (a Room fixture's drone and hue driven the same way a
  device's are); the cue machinery (`Bit.fires(at)`, generators and
  device-triggered `LightCue`s reach both the calling device and the Room
  from one shared computed time); and **MetronomeBit**, the first
  production game Bit.
- Missing or deferred: see *Not yet built / deferred* below, most notably
  fairyring, a scoring framework beyond a Bit's own `result()` payload, and
  native iOS/Android/Radxa connectivity.

Full pre-rewrite history: `git show 9dd35c3:docs/MM_TERRARIUM.md`.

**Contents**

- [What it is, in one picture](#what-it-is-in-one-picture)
- [Running it](#running-it):
  [Running in the container](#running-in-the-container),
  [Linux / WSL host setup](#linux--wsl-host-setup)
- [Landed subsystems](#landed-subsystems):
  [`control/` lifecycle and Bit runtime](#control-the-lifecycle-engine-and-bit-runtime),
  [`control/` Terrarium, Rooms, instruments and audio](#control-terrarium-rooms-instruments-and-audio),
  [`devicelink/`](#devicelink-the-device-facing-side-over-o2lite),
  [`harness/`](#harness-boot-the-stack-runner-and-tooling),
  [`console/`](#console-the-terrarium-console),
  [`uplink/`](#uplink-outbound-remote-control),
  [`capture/`](#capture-labelled-sensor-telemetry),
  [`bits/`](#bits-the-in-repo-bits),
  [`www/` and `arcoserver/`](#www-and-arcoserver)
- [Boundary rules (the load-bearing invariants)](#boundary-rules-the-load-bearing-invariants)
- [Host platform (gotcha)](#host-platform-gotcha)
- [Relationships to other repos](#relationships-to-other-repos)
- [Not yet built / deferred](#not-yet-built--deferred)
- [Design docs (in-repo, authoritative)](#design-docs-in-repo-authoritative)

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

**Where the light is rendered: in Control, never on a device.** Lux Aeterna
is a Python library imported into the Control+GameServer process. Control
builds one `LightSession` per granted device and one per Room fixture, drives
them with direct method calls (`feed_midi`, `swap`), and renders them on the
44 Hz tick. Every output is then a **pixel sink** fed through
`FixtureSink.send_frame(frame, when)`. A Tuneshroom gets its 36 raw bytes on
`/<dev>/leds` and shows them at `when`. It runs no Lux Aeterna and no Python
renderer. The venue array's WLED ESP32 controllers are the third sink,
`ArtNetFixtureSink` (`devicelink/artnet_sink.py`), wired in through
`[[artnet]]` (see *Fixture sinks and Art-Net* below); no physical LED has
been driven over it yet, hardware bring-up is still pending.

**What each status light means** (invite, success, ready, failure, solo) is
[`docs/light-lexicon.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/light-lexicon.md):
the normative visual language every device, fixture and Bit shares, agreed
2026-10-05. Its patterns are reserved, so a Bit never reuses one for game
feedback, and its section 6 lists where Control does not match it yet.

The diagram below traces one frame from a Bit's cue through both live sinks
(device and Console) to the Art-Net sink.

<!-- diagram:light-path GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
                  ┌────────────────────────────────────┐         
                  │Bit cues, lobby, generators, breath │         
                  │                                    │         
                  └────────────────────────────────────┘         
                                │                                
             feed_midi and swap, in-process, 0 hops              
                                │                                
                                ▼                                
            ┌───────────────────────────────────────────────────┐
            │luxaeterna LightSession per device and per fixture │
            │                                                   │
            └───────────────────────────────────────────────────┘
                                │                                
            render_into at 44 Hz, changed frames only            
                                │                                
                                ▼                                
                ┌────────────────────────────────────┐           
                │FixtureSink.send_frame(frame, when) │           
                │                                    │           
                └────────────────────────────────────┘           
                        │       │        │                       
             ┌──────────┘       │        └─────────┐             
             │                  │                  │             
             ▼                  ▼                  │             
     ┌───────────────┐ ┌─────────────────┐         │             
     │DeviceLinkSink │ │ConsoleFrameSink │         │             
     │               │ │                 │    [[artnet]]         
     └───────────────┘ └─────────────────┘         │             
             │                  │                  │             
             │                  ▼                  ▼             
             │           ┌──────────────┐ ┌──────────────────┐   
        /<dev>/leds      │Console strip │ │ArtNetFixtureSink │   
             │           │              │ │                  │   
             │           └──────────────┘ └──────────────────┘   
             │                                     │             
             ▼                                     │             
        ┌─────────┐                                │             
        │Arco hub │                                │             
        │         │                           Art-Net UDP        
        └─────────┘                                │             
             │                                     │             
             │                                     ▼             
             │                              ┌─────────────┐      
    relayed over o2lite                     │ WLED ESP32  │      
             │                              │             │      
             │                              └─────────────┘      
             │                                                   
             ▼                                                   
┌────────────────────────────┐                                   
│Tuneshroom shows it at when │                                   
│                            │                                   
└────────────────────────────┘                                   
```
<!-- /diagram:light-path -->

A **Bit** is a loadable game/experience module inside Control. It declares
the **roles** players can adopt, which **Registration Nodes** (tap points,
an NFC tag or QR code is enough) grant which roles, the `/game` message
vocabulary, the ugen graph it builds on Arco, the per-device light/sound
behavior, and the scoring logic. Roles have a **class** (`unique`
capacity-K, `shared` unbounded, `jam` unbounded-but-unscored), a capacity,
an ordered node-to-role fallback list, a per-player graph-builder, and a
`scored` flag.

The player flow below is hello to role to release in contract v3 (mapped to
`/game/*` and `/ie<N>/*` messages); the verb table is *Message vocabulary*,
the states and the RUNNING grant are *Lobby and the handshake*.

<!-- diagram:player-flow GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
Player flow, hello to role (contract v3, 2026-10-01)

+------------+                             +------+                             +---------+
| Tuneshroom |                             | Arco |                             | Control |
+------------+                             +------+                             +---------+
       |                                       |                                     |
       |/game/hello dev name proto instrument->|                                     |
       |                                       |                                     |
       |                                       |------------/game/hello------------->|
       |                                       |                                     |
       |                                       |<---/ie1/room (first contact only)---|
       |                                       |                                     |
       |<--------------/ie1/room---------------|                                     |
       |                                       |                                     |
[ heartbeat: the identical hello every 5 s; no /room per beat ]
       |                                       |                                     |
       |                                       |<-----/ie1/handshake round (TCP)-----|
       |                                       |                                     |
       |<------------/ie1/handshake------------|                                     |
       |                                       |                                     |
       |                                       |<--/ie1/leds white invite flash x2---|
       |                                       |                                     |
       |<--------------/ie1/leds---------------|                                     |
       |                                       |                                     |
[ user double-taps (accept) ]
       |                                       |                                     |
       |----/game/handshake dev round node---->|                                     |
       |                                       |                                     |
       |                                       |----------/game/handshake----------->|
       |                                       |                                     |
       |                                       |<--/ie1/validated round role (TCP)---|
       |                                       |                                     |
       |<------------/ie1/validated------------|                                     |
       |                                       |                                     |
[ over cap: /ie1/deny "scored full" then jam role at start ]
       |                                       |                                     |
[ stale round: dropped ]
       |                                       |                                     |
       |                                       |<------/ie1/leds green flash x2------|
       |                                       |                                     |
       |<--------------/ie1/leds---------------|                                     |
       |                                       |                                     |
[ a Room fixture bell climbs the A major scale on Control's own Arco voice ]
       |                                       |                                     |
       |                                       |<-----/ie1/play chime key=<midi>-----|
       |                                       |                                     |
       |<--------------/ie1/play---------------|                                     |
       |                                       |                                     |
[ start (timer, operator, admin, /game/start) ]
       |                                       |                                     |
       |                                       |<-------/ie1/role scored (TCP)-------|
       |                                       |                                     |
       |<--------------/ie1/role---------------|                                     |
       |                                       |                                     |
[ every other pooled device gets /<dev>/role jam here ]
       |                                       |                                     |
       |--------------/game/tap--------------->|                                     |
       |                                       |                                     |
       |                                       |-------------/game/tap-------------->|
       |                                       |                                     |
       |                                       |</ie1/leds at = origin + cue_horizon-|
       |                                       |                                     |
       |<--------------/ie1/leds---------------|                                     |
       |                                       |                                     |
       |                                       |<---------/ie1/release (TCP)---------|
       |                                       |                                     |
       |<-------------/ie1/release-------------|                                     |
       |                                       |                                     |
+------------+                             +------+                             +---------+
| Tuneshroom |                             | Arco |                             | Control |
+------------+                             +------+                             +---------+
```
<!-- /diagram:player-flow -->

## Running it

**Use the venv explicitly.** There is no bare `python` on the dev boxes, and
the sibling **luxaeterna** dev dependency is installed only in `.venv`.
Invoking `python3` instead collects an import error in
`tests/test_terrarium_boot.py` that looks exactly like a real failure and is
not; that trap has already cost one debugging detour.

**A fresh git worktree has no `.venv` at all**, so that trap is one step
away every time one is created: the commands below fail outright, and the
obvious recovery is to reach for `python3` and land in the paragraph above.
Symlink it instead: `ln -s "$HOME/projects/mm-terrarium/.venv" .venv`
from the worktree root. `.gitignore` matches `.venv` without a trailing
slash specifically so the symlink is ignored (a directory-only pattern does
not match a symlink).

**Test suites:**

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -v
node --test tests/js/*.test.js   # the bare directory form fails
```

`console/static/` ships as plain ES-module JS with no build step (a venue
box must never need npm); `node --test` runs it directly.

**`./terrarium.sh [--room NAME]`** wraps `harness.run_stack --no-bit --serve
--devices 0 --console-port 8772`: a clean Terrarium with no Bit and no
spawned Testshrooms. Closing its terminal tears the whole stack down (Arco
included); pass `--detach` to keep it running without one. Without `--room`
it boots to `NO_ROOM` and waits for the Console to load one; any
`harness.run_stack` flag after it overrides the defaults.
`./terrarium.sh --clean` instead sweeps leftovers of past runs from every
worktree and reports what is still running (see *Terrarium lifecycle*, run
records).

**`./smoke-test.sh`** is the Bit-first launcher: a thin wrapper that
forwards every argument to `harness.run_stack` verbatim (spawned
Testshrooms, `--ci` mode, profiles).

Both scripts, and `harness.run_stack` itself, take the flags below (see
`.venv/bin/python -m harness.run_stack --help` for the full list):

- `--room ROOM`: which Room to boot (a `[rooms.<NAME>]` table in
  `--config`, default `terrarium.toml`). Default: the selected Bit's
  `launch.default_room_type`.
- `--bit BIT` / `--no-bit`: which Bit to run, by its discovered manifest
  name (`bits/*/bit.toml`; see `--list-bits`), or stand up a clean
  Terrarium with no Bit and no spawned devices (`--room` optional,
  `NO_ROOM` without it). `--no-bit` is refused together with `--bit`,
  `--profile`, `--node`, or `--devices N>0`; it is what `./terrarium.sh`
  runs.
- `--profile PATH`: a venue TOML (see `profiles/dev-metronome.toml`)
  supplying launch defaults (Bit, Room type, devices, console port,
  seconds) and a `[bit.overrides]` table. Precedence is manifest < profile
  < explicit CLI flags.
- `--serve`: hold until Ctrl-C or a child exit instead of a fixed
  duration; implied when a console is requested outside `--ci`.
- `--ci`: non-interactive: no terminal echo, a bounded run (default 45s),
  and a non-zero exit on any failure. A device that never clock-syncs
  fails as stage `device-sync` rather than hanging; the one remaining
  cause is upstream (*Not yet built / deferred*). Its intermittent half
  was the undrained Arco pty below (fixed by the drain thread, never
  headless-specific).
- `--seconds SECONDS`: how long to hold the stack up. Default: forever
  (Ctrl-C), or 45s under `--ci`.
- `--devices DEVICES`: how many simulated player devices to join.
- `--list-bits`: print every discovered Bit package (name, version, kind,
  Room types, start condition, description) and any manifest errors, then
  exit.

**Profiles** (`profiles/*.toml`) pin a venue's launch defaults so a
one-word command reproduces a specific stand-up; `[bit.overrides]` is
forwarded verbatim to `terrarium_boot`.

**Ports**, as `harness/run_stack.py` and `harness/terrarium_boot.py` define
them: Arco's own HTTP/websocket port is `8080` (`ARCO_HTTP_PORT`;
`harness/terrarium_boot.py`); phones' o2ws websocket goes there. The guest
page (`www/`, what phones scan a QR to reach) serves on `8788` by default
(`WWW_PORT`, `harness/www_server.py`); `0` disables it. The Terrarium
Console has no fixed default port: it is off unless `--console-port` is
passed (`./terrarium.sh` fixes it at `8772`).
The guest-page server also answers `GET /o2proc` with Arco's current O2
process name (`@pub:internal:tcp:udp`, hex), 503 until a Room is ready and Arco has been found (a failed lookup is
retried in the background with backoff and its reason shown):
Arco's O2 ports are ephemeral (the server build defines
`O2_NO_O2DISCOVERY`), so firmware on a network that blocks mDNS learns them
here (mm-devshroom `O2_FALLBACK_HOST`, Part B, pending; spec
`docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md`).

**`runs/<timestamp>/` logs and markers.** Every run writes per-process logs
under `--log-dir` (default `runs/<timestamp>/`): `arco.log`, per-Bit and
per-device logs, and sample files. The harness and Control print
machine-parseable marker lines on stdout for orchestration and tests to
watch for, e.g. `CONTROL_TRANSPORT_READY`, `CONTROL_NO_ROOM_WAIT`,
`JOIN_URL: <role> <node> <url>` (one per registration node, never waited
on), and `TRANSPORT_READY ... (restarted)` after a Room load from
`NO_ROOM`.

**Arco checkout resolution.** `pyarco`/`o2litepy` come from a sibling
`arco` checkout, resolved in order: an explicit `MM_ARCO_PATH`
(`harness/arco_paths.py`), else a sibling `arco` directory next to this
repo's main checkout (a git worktree resolves to the same place). The
`terrarium.sh`/`smoke-test.sh` wrappers do the equivalent resolution at the
shell level before invoking Python, using `ARCO_ROOT` (or an already-set
`PYTHONPATH`) instead of `MM_ARCO_PATH`, and refuse to start with a clear
error rather than let a missing `pyarco` import masquerade as something
else.

**The Arco pty gotcha: Arco's pty must be read continuously.** Arco is a
curses app running on a pty whose buffer holds only ~19.6 KB (measured); if
nothing reads it, the write blocks and Arco freezes mid-write with no
mDNS advertisement, clock sync, routing, or audio, even though the process is
alive. On WSL Arco writes ~40 KB of harmless ALSA "cannot find card" errors
at startup, so it froze before advertising whenever the master was unread:
during the 5 s `--arco-settle-seconds` sleep and inside each readiness probe
(pyarco's `arco.initialize()` blocks up to 30 s + 15 s and never calls
`poll()`). That was the root cause of probe 1 always timing out (~32 s), every
boot running ~35 s slow, and `room_load_failed` ("stuck spinning up arco") on
a box with more startup output. `_PtyProcess` now owns a daemon thread that
drains the master from spawn until `close()` (which joins it before closing
the fd), feeding `arco.log` and the bounded `output` tail, so no holding loop
has to drain. The serve loops' `pump` hooks (`_serve_rounds`,
`_wait_for_load`, `_wait_in_setup`, `_serve_until_done`, the ownership probe)
remain as redundant, harmless `poll()`s. Earlier, a `--setup-seconds` hold
without draining looked like devices "taking 60-100 s to clock-sync".

**Backgrounding `./terrarium.sh` for a scripted or unattended run: use
`set -m` first, or send SIGTERM.** bash runs an asynchronous command
(`cmd &`) with SIGINT set to ignored whenever job control is off, and
Python leaves that inherited ignore in place rather than installing its
`KeyboardInterrupt` handler, so `kill -INT` on a plain-backgrounded run is
a silent no-op. SIGTERM still works: `sigterm_as_keyboard_interrupt`
(*Boot and teardown order*) turns a bare `kill` into the same clean
shutdown Ctrl-C gives interactively. Enable job control before
backgrounding to get SIGINT too:

```bash
set -m
./terrarium.sh --room TEST > run.log 2>&1 &
PID=$!
kill -INT "$PID"   # or: kill -TERM "$PID", works either way
```

### Running in the container

The pre-built `terrarium-dev` image is the recommended Linux/WSL dev path: it
bakes in o2, the patched Arco server, the venv and a snapshot of `main`, so
none of the steps below are needed. Install the launcher, then run against
your own checkout (from its root) or the snapshot:

**RUN ON: WSL UBUNTU**

```bash
docker run --rm ghcr.io/musical-mycology/terrarium-dev:main launcher > terrarium-dev && chmod +x terrarium-dev
sudo mv terrarium-dev /usr/local/bin/    # once, so `terrarium-dev` is on PATH
terrarium-dev run --room TEST --seconds 45
terrarium-dev test
```

Setup (Docker Engine in WSL2, avahi-daemon, mirrored networking), flags,
audio, pins and the self-check are in
[`docker/README.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docker/README.md).
The image is not published yet (Phase 2): build it with `docker/build.sh` and
pass `--tag local` until it is. Networking limit: a container shares the
host's network, so real devices need a host already on the LAN (native Linux
or WSL2 in mirrored mode); simulated devices need no LAN, but `run`,
`smoke` and `shell` also need a host Avahi socket, so on macOS only `test`,
`clean` and `selfcheck` work and a native setup runs the stack. The native
steps below remain the reference for what the image does and for building
without Docker.

### Linux / WSL host setup

**WSL2 Ubuntu on a Windows box is the default dev host** for MM engineers
(the Mac stays the Dec show machine). Everything below was verified on
Windows 10 + WSL2 Ubuntu 26.04 except the real-device networking in step 9.
The end state: `./terrarium.sh --room TEST --seconds 45` runs clean.

1. **apt packages.** (`portaudio19-dev`, not `libportaudio19-dev`, which does
   not exist.)

   ```bash
   sudo apt install cmake cmake-curses-gui portaudio19-dev libavahi-client-dev \
     libsndfile1-dev libfluidsynth-dev libportmidi-dev fluid-soundfont-gm \
     libncurses-dev libogg-dev libvorbis-dev libflac-dev libopus-dev \
     libglib2.0-dev avahi-daemon
   ```

   **Enable systemd in WSL** (`/etc/wsl.conf`: `[boot]` then `systemd=true`,
   then `wsl --shutdown` from Windows) so avahi-daemon starts on its own. On
   Linux, O2 advertises `_o2proc._tcp` through the Avahi client API; with no
   daemon Arco logs "Avahi failed to create client: Daemon not running",
   never advertises, and the readiness probe dies 60 s later with "Arco did
   not report ready". `harness/host_preflight.py` now checks for
   `/run/avahi-daemon/socket` first (Linux only; `harness.run_stack` and
   `harness.terrarium_boot` both call it before spawning anything) and
   refuses with the fix. `./terrarium.sh --clean` skips it. The soundfont
   lands at `/usr/share/sounds/sf2/FluidR3_GM.sf2`, which
   `harness/arco_synth.py` already probes.
2. **Sibling checkouts under `~/projects`**: `arco`
   (`Musical-Mycology/arco`), `luxaeterna`, and `o2` from
   `rbdannenberg/o2` (not in the Musical-Mycology org; arco's cmake finds it
   as a sibling). `mm`'s clone-missing-repos sweep skips arco, luxaeterna
   and mm-devshroom (no `mm-meta.yml`), so clone them by hand.
3. **Build o2:**

   ```bash
   cd ~/projects/o2 && cmake -S . -B Release -DCMAKE_BUILD_TYPE=Release \
     -DTESTS_BUILD=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
     && cmake --build Release -j"$(nproc)"     # -> Release/libo2_static.a
   ```
4. **`arco/apps/common/libraries.txt`** is machine-local and gitignored, and
   Arco will not configure without it. Copy the working Ubuntu one:
   `cp docs/upstream/arco-libraries-ubuntu.txt ~/projects/arco/apps/common/libraries.txt`
   (system `-dev` shared libs plus the sibling o2 build).
5. **Three Linux build fixes to Arco source** (Mac-only code: one-argument
   `pthread_setname_np` in `arco/src/audioio.cpp` and `server/src/arco.cpp`,
   a missing `<pthread.h>` in `audioio.cpp`, missing
   `<cstring>/<algorithm>/<iterator>` in `server/src/termui/termui.cpp`).
   Made against arco `c8092e2`: `git -C ~/projects/arco apply
   "$PWD/docs/upstream/arco-linux-build.patch"`. **They are pending upstream
   with Roger Dannenberg and must never be committed to the arco mirror**;
   drop the patch once he lands them.
6. **Build the server:**

   ```bash
   cd ~/projects/arco/apps/pytest
   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
   cmake -S . -B build          # the SECOND configure is required
   cmake --build build -j"$(nproc)"
   ln -s pytestserver server    # the harness expects `server`
   ```

   `server/arcoserver.cmakeinclude` tests `if(USE_MIDI)` (adding
   `midiservice.cpp`, ~line 58) before `option(USE_MIDI ...)` (~line 144)
   defines it, so a first configure omits midiservice and the link fails on
   undefined `midi_*` references. Faust is not needed (the committed reson
   sources compile; its regeneration step errors harmlessly).
7. **The venv**, from this repo's root:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements-dev.txt
   .venv/bin/python -m pip install -e "$HOME/projects/luxaeterna[websim]"
   ```
8. **Expected noise:** dozens of ALSA "cannot find card '0'" lines in
   `arco.log` (WSL has no sound card) are harmless.
9. **Networking for real devices.** WSL2
   defaults to NAT, so Linux sits on its own subnet: LAN devices cannot
   discover or reach Arco (ESP32 firmware connects to the internal IP in
   the O2 mDNS TXT record). Simulated devices are unaffected; `./terrarium.sh`
   prints a WARNING when `wslinfo --networking-mode` reports `nat`. To try
   real devices, follow Microsoft's WSL networking docs
   (<https://learn.microsoft.com/windows/wsl/networking>):
   - **Windows 11 (22H2 or later):** `networkingMode=mirrored` under
     `[wsl2]` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`. That
     page lists multicast support and direct LAN access to WSL for this
     mode. Allow inbound traffic for the WSL VM in the Hyper-V firewall
     (admin PowerShell, verbatim from that page):
     `Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow`.
     **Confirmed 2026-09-29 on a teammate's Windows 11 box:** mirrored mode
     with avahi-daemon active, and an ESP32 dev shroom discovers Arco over
     mDNS and connects. Avahi coexists with Windows' own mDNS responder on
     UDP 5353.
   - **Windows 10:** mirrored mode is unavailable. `networkingMode=bridged`
     with a Hyper-V external switch (Pro only) is deprecated and not on that
     page; unverified. The reliable fallback is a native host.
10. **Smoke check:** `./terrarium.sh --room TEST --seconds 45` should exit 0
    with "room loaded: TEST" and both `sim-room-*` device hellos. "device
    timed out" lines printed AFTER `Arco_engine: finish called` are normal
    teardown.

## Landed subsystems

### `control/`: the lifecycle engine and Bit runtime

`control/engine.py`'s `GameServer` loads a Bit, opens registration, runs it
and returns to `IDLE`. It is O2-agnostic: a transport drives it and
receives output through sinks. Specs: *Design docs (in-repo, authoritative)*.

#### State machine

- `control/state.py`, as drawn below. `load_bit` runs LOADING through SETUP
  synchronously; a failure returns to IDLE as `BitLoadError`. A wrong-state
  call raises `InvalidTransition`. Scored slots are reserved only in SETUP;
  in RUNNING a newcomer gets a jam role at once, for casual foot traffic.
- Control never evaluates a win condition: the Bit returns `True` from
  `update(dt)` and that `tick()` runs COMPLETING through IDLE. `abort()`
  runs `on_complete`/`on_unload` best-effort but skips COMPLETING (its one
  call site is `_complete()`). UNLOADING is reached even if a hook raises,
  so a Bit can never wedge Control loaded. The Console's ABORT does more
  (*Terrarium lifecycle*).

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

#### Data model

- **`RoleTable`** (`control/roles.py`): `roles` plus `node_map` (node id to
  ordered role fallbacks). A `Role` has `role_class`
  (`UNIQUE`/`SHARED`/`JAM`/`ROOM`), `capacity` (`None` = unlimited),
  `scored`, `uses`, `samples`, `ugen_manifest`, `light_manifest` (the v2
  light manifest), optional `welcome` (light and audio halves), `breath` (set
  `False` if the Bit drives cc:11 itself, or the breath overwrites it) and
  `requires` (a slot gated against the joiner's carried instrument).
- **`RegistrationState`** (`control/registration.py`), per loaded Bit:
  `validated` (SETUP reservations, in validation order), `assignments`,
  public `counts()`, `granted()` and `scored_cap()`. `validate` walks the
  node's fallbacks over scored roles only, skipping full ones;
  `materialize` turns reservations plus jam roles into assignments at
  RUNNING; `assign` of the held role is a no-op. There is no role switch
  (*Lobby and the handshake*).
- `control/role_config.py` validates manifests at `load_bit` (a typo is a
  `BitLoadError`, never a device-side failure mid-show).
  `compose_role_config` builds the `/ie<N>/role` blob: the manifest stamped
  with `bit_name`/`bit_version`/`role` plus the welcome **light** half (the
  **audio** half never ships). Optional keys are omitted, never null.
  `carried_instrument_view` is the one `instrument` serializer.

#### Bit interface

`control/bit.py`'s `Bit` ABC requires only `role_table`. No-op hooks:
`on_setup_enter`, `on_run_start`, `update(dt)`, `on_complete`, `on_unload`,
`on_join(dev, role_name)` (guarded; fires at RUNNING, scored devices in
validation order, so it is how a Bit learns turn order). Optional:
`function_table`, `room_manifests()`, `instrument_requirements()`,
`result()` (uplink payload), `status()` (Console read-out). Class
attributes: `version`, `room_types` (default `{"TEST"}`; override, never
mutate) and `cue_horizon`, stamped at load for Bits grading input.

- `verb_handlers()` handlers run as `handler(dev, args, at)`, `at = origin
  + cue_horizon`, and return cues or a `str` refusal (sent as
  `/<dev>/error`, checked before iterating, and dropping the gesture's
  stream cues); a raise is `"handler error"`. `fires(at)` runs each RUNNING
  tick after `update` and may return only `FireFunction`.
- `data()`, `fire_function()` and `_dispatch_cues()` never raise. A gesture
  stamp over 5 s ahead (`_MAX_GESTURE_LEAD`) falls back to Control's clock
  and bumps `rejected_stamps`; a non-positive one (o2lite's -1 before sync)
  falls back silently. `GameServer`'s `clock` must be `DeviceLinkAgent`'s:
  two clock bases once left a live run dark.
- Observers (`add_observer()`; a raiser is logged, never fatal) get state,
  registration, device and event hooks (`on_function_fired`,
  `on_start_requested`, ...). Device output uses guarded transport sinks
  (`on_release`, `on_light_cue`, `on_play_cue`, `on_solid_cue`,
  `on_mute_change`).

#### Bit packages and manifests

- A Bit is a directory holding `bit.toml` and its class.
  `control/bit_registry.py`'s `BitRegistry.scan()` parses every
  `<root>/*/bit.toml` without importing Bit code; `entry = "module:Class"`
  imports on first access. A bad manifest, API mismatch, bad asset,
  duplicate name or missing root is a located `PackageError` that never
  blocks other packages. Roots are `terrarium.toml`'s `bit_paths`; the
  first root to claim a name wins.
- External Bits live in their instrument's repo (e.g. mm-tuneshroom's
  `GlowBit`). Bits may use only the stdlib and mm-terrarium, so a package
  stays self-contained and deleting it uninstalls it.
- `control/bit_config.py`'s `BitConfig` (schema v1) parses `[bit]` (`kind`
  in `music`/`r_game`/`game`/`tool`/`ambient`, `requires_terrarium_api`,
  `enabled`), `[launch]`, `[start]`, `[lobby]`, `[console]`, `[results]`,
  `[assets]`, `[rhythm]` and `[defaults]`; unknown keys warn.
- `load_bit(name, config=None)` hands the opaque `BitConfig` to
  `Bit(config)`; Control never reads it, and other consumers read the
  manifest, never the Bit's Python. `[assets]` are package-relative, must
  exist inside the package, and are reached only via
  `BitConfig.asset_path()`, never a guessed path.
- `load_bit` refuses a gesture-verb condition on an unimplemented verb, a
  fixture the Room lacks, two generators on one resolved fixture lane, and
  an unmet instrument requirement. `load_warnings` lists empty-script
  entries no target instrument resolves (fires there no-op).
- `tools/bundle_bit.py` bundles a package as `.mmbit` (zip plus per-file
  sha256). `install` verifies, blocks zip-slip and replaces only with
  `--force`; sha256 is integrity, not authenticity.

#### Start conditions and profiles

- `[start] when`: `immediate` (at SETUP's deadline), `players` (the instant
  `scored >= min_scored` validated, so extra smoke-test devices end up jam
  by design), `operator` or `admin` (no deadline), each with
  `timeout_seconds`/`on_timeout`.
- **One decider module**, `control/start_condition.py`: `decide_start`
  (a start request) and `timer_decision` (the harness timer), plus
  `scored_count` (validated devices count as scored in SETUP), which skips
  a role that left the table (a Room unloaded mid-SETUP once crashed boot).
  Pure and engine-free (the engine imports it).
- **Every start goes through `request_start`** (*Lobby and the
  handshake*), including the harness timer:
  `request_start(None, TERRARIUM_ADMIN, "timer")`, so a timer start fires
  `on_start_requested` and the accept flash like any other.
  `_wait_in_setup` never calls `gs.run()` directly.
- `admin` requires a `key` and defaults `min_scored` to 0; MetronomeBit
  ships `key = "metro-dev"`, `min_scored = 1`.
- `control/run_profile.py`: a profile (`profiles/dev-metronome.toml`) has
  `[run]` defaults and `[bit.overrides.*]`; precedence is manifest <
  profile < CLI, re-validated by `merge_overrides`. `run_stack`'s own
  `--setup-seconds` overrides the manifest's (*`run_stack`*).

#### Lobby and the handshake

One entry path (contract v3): **hello, handshake, validated**, then exactly
one role per device at RUNNING, scored if it validated, else jam.
`/game/join` and the lobby double-tap join are retired. Spec:
[`2026-10-01-instrument-handshake-protocol-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-10-01-instrument-handshake-protocol-design.md).

<!-- diagram:device-lifecycle GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
                       ┌──  ┌──────────┐                      
                       deny▶│ INVITED  │                      
                            │          │                      
                            └──────────┘                      
                               ▲  │  │                        
             ┌─────────────────┘  │  └────────────────┐       
             │                    │                   │       
             │        /game/handshake accepted        │       
             │                    │                   │       
             │                    ▼                   │       
             │              ┌────────────┐            │       
             │              │ VALIDATED  │            │       
             │              │            │            │       
             │              └────────────┘            │       
             │                  │   │                 │       
             │              ┌───┘   └────┐            │       
             │              │            │            │       
SETUP, not FULL: /handshake │            │            │       
             │              │            │            │       
             │              │   start: /role scored   │       
             │              │            │            │       
             │              │            ▼            │       
             │              │       ┌─────────┐       │       
             │              │       │ SCORED  │       │       
             │     reaped: slot frees         start: /role jam
             │              │       └─────────┘       │       
             │              │            │            │       
             │              round ends (fade, release)│       
             │              │            │            │       
             └──────────┐   │  ┌─────────┘            │       
                        │   │  │                      │       
                        │   ▼  ▼                      │       
                     ┌────────────┐                   │       
                     │ CONNECTED  │                   │       
                     │            │                   │       
                     └────────────┘                   │       
                         │    ▲                       │       
                     ┌───┘    └───┐                   │       
                     │            │                   │       
        start, or hello in RUNNING│                   │       
                     │       round ends               │       
                     │            │                   │       
                     └──────────┐ │ ┌─────────────────┘       
                                │ │ │                         
                                ▼ │ ▼                         
                              ┌──────┐                        
                              │ JAM  │                        
                              │      │                        
                              └──────┘                        
```
<!-- /diagram:device-lifecycle -->

- **States**, per device per round, Control-side: CONNECTED (in the pool),
  INVITED (sent `/<dev>/handshake`), VALIDATED (scored slot reserved in
  `RegistrationState.validated`), then SCORED or JAM at RUNNING; the
  round's end (fade, `/release`) returns both to CONNECTED. Room fixtures
  never enter it.
- **Round id**: `load_bit` mints `"<bit>-<counter>-<hex>"` (6 hex from
  `secrets`), cleared at UNLOADING; it alone ties an ack to this round. A
  stale id is dropped silently and logged (`handshake: stale round`),
  never denied: the next invite carries the current one.
- **Invites are the agent's own schedule**, not the lobby's:
  `/<dev>/handshake round_id` (TCP) on a device's first hello in SETUP,
  then every `invite_interval_s` (5 s) while SETUP, the round id is set,
  the lobby is not FULL and the Bit has a scored node, whether or not a
  Room or lobby exists. The lobby only adds the white x2 flash and the status pulse (white
  while invited, green once validated, until `/role`; both held dark
  through any flash train so its gaps read black), and a validation cancels
  that device's still-queued flashes.
- **Validation** (`GameServer.handshake`; the first failure answers
  `/<dev>/deny reason hint`): not pooled, `not connected`; no Bit,
  `registration closed`; a Room node binds (below); a bound fixture or a
  state other than SETUP, `registration closed` (the RUNNING hint says jam
  comes at start); stale round, dropped; already validated, `/validated`
  re-sent with no ceremony replay; the node walk (empty node means
  `default_scored_node()`) over scored roles, skipping full ones, gives
  `scored full` or `no such node`; a `requires` miss frees the slot and
  denies with the `satisfies()` reason, hint `this role needs: ...`.
  Accept: `/<dev>/validated round_id role` (TCP), `on_registration_change`,
  the ceremony. A deny never ends anything: the device stays hello'd and
  gets a jam role at start. With the lobby up, a deny flashes the
  device red x2 (`LobbyRuntime.on_deny`), then its white invite pulse
  resumes if it is still invited.
- **Cap**: `scored_cap()` sums the scored roles' capacities, lowered by
  `[lobby] max_scored` (positive int; `load_bit` refuses one above a
  bounded sum); an unbounded scored role makes the cap `max_scored` or
  none. FULL only when the cap is above 0 and reached (cap 0 waits); FULL
  stops invites. A reaped VALIDATED device frees its slot (FULL back to
  WAITING, invites resume).
- **Room nodes**: a handshake naming a Room node binds the armed fixture
  (`join_room`, `_bind_room`; *Room binding*), round id unchecked, in
  SETUP and RUNNING; unarmed is `no such node`. No `/validated`, no
  `/role`: the fixture's frames just start.
- **Materialize** (`run()`): state goes RUNNING first (the lobby tears
  down), then `RegistrationState.materialize` gives each validated device
  its reserved scored role in validation order, then every other pooled
  device that is not a bound fixture or an admin id a jam role. Per new
  assignment `_grant` composes the blob and calls `Bit.on_join` (guarded,
  in that order), and the `on_grant` sink (`DeviceLinkAgent._on_grant`)
  builds the bridge and sends `/<dev>/role` over TCP; one
  `on_registration_change` and `on_devices_change` follow the batch. A
  RUNNING walk-up's first hello gets its jam role at once (after its
  first `/room`); a re-hello from a role holder does nothing.
- **Jam and Solo** (`control/jam_role.py`): the jam role is the Bit's
  first fitting unscored role (JAM class first, then its other unscored
  roles in declaration order, skipping full roles and unmet `requires`;
  never ROOM), else a synthesized `solo:<instrument>` (class `jam`,
  unscored, uncapped; light from the carried instrument's `[solo]`, else
  its ambient; `uses` from `[solo.bindings]`; no `ugen_manifest`). Its
  gestures are served Control-side: `_solo_gesture` resolves the binding
  (`double_tap` is `tap` count 2) and fires that instrument function
  through the fire ladder; unbound gestures drop. A synthesized solo role
  is never a jam candidate. CaptureBit, MinigameBit and Rev1Bit declare
  only unscored non-JAM roles and work unchanged through this rule.
- **The lobby** (every Bit, unless `[lobby] enabled = false`):
  `control/lobby.py` is pure (WAITING/FULL, `NOTE_SCALE`, schedulers);
  `devicelink/lobby_runtime.py` renders an aurora with pad and drone,
  green and silent when FULL. Per validation, 1 s apart: green x2, a bell
  up the scale at +0.8 s, a device chime cue with `key=<midi>` at +1.8 s. A validated device then
  pulses green until its role.
  Every flash here is a reserved signal in the light lexicon
  ([`docs/light-lexicon.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/light-lexicon.md)).
  **The ceremony survives a fast start**: `stop()` keeps the queue, so a
  start inside 1.8 s lets the bell and chime play out (`draining()`); an
  abort drops it.
- `TERRARIUM_ADMIN = "terrarium"` is always admin and refused as a device id
  before any dispatch; `[admin] devices` in `terrarium.toml` adds more.
- **`GameServer.request_start(key, source_dev, source)` is the one start
  authority**: Console and uplink `run` (unkeyed, as the Terrarium), `GET
  /start?key=` on `harness/www_server.py` (loopback is the Terrarium;
  always 202 once queued), `/game/start` and the harness timer. It never
  raises and fires `on_start_requested`. `decide_start` checks a key first
  (wrong key or no admin start: refused with no feedback), then SETUP,
  then accepts an admin or refuses a non-admin below `min_scored`. Fixture
  feedback: accept green x2, minimum red x2, other refusal red x3, bad key
  nothing.
- **`GET /prepare?key=&bit=[&dev=]`** (`control/prepare.py`) loads a Bit
  for MycoQuest: no Room is a visible 409; unknown Bit, non-admin Bit or bad
  key is a silent 202 like an accept; IDLE loads it; SETUP with that Bit is
  a 202 no-op; anything else is 409 `busy`. While a Room loads it answers
  409 `room loading` at once, unqueued (transient; retry). It reads the
  parsed `[start] key` without importing; no drain reply within 3 s is 503.

<!-- diagram:role-assignment GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
Role assignment at RUNNING (contract v3, 2026-10-01)

+---------+                   +-----+                   +-------+                   +--------+
| Control |                   | Bit |                   | Agent |                   | Device |
+---------+                   +-----+                   +-------+                   +--------+
     |                           |                          |                            |
[ request_start accepts: run() sets RUNNING (lobby down), then materialize ]
     |                           |                          |                            |
[ per validated dev: its reserved scored role ]
     |                           |                          |                            |
     |on_join(dev, scored role)->|                          |                            |
     |                           |                          |                            |
     |--------------on_grant(dev, role blob)--------------->|                            |
     |                           |                          |                            |
     |                           |                          |-/<dev>/role scored (TCP)-->|
     |                           |                          |                            |
[ per other dev: first fitting unscored role, else solo:<instrument> ]
     |                           |                          |                            |
     |--on_join(dev, jam role)-->|                          |                            |
     |                           |                          |                            |
     |--------------on_grant(dev, role blob)--------------->|                            |
     |                           |                          |                            |
     |                           |                          |---/<dev>/role jam (TCP)--->|
     |                           |                          |                            |
     |------on_run_start()------>|                          |                            |
     |                           |                          |                            |
[ RUNNING walk-up: a first hello gets the jam steps at once ]
     |                           |                          |                            |
+---------+                   +-----+                   +-------+                   +--------+
| Control |                   | Bit |                   | Agent |                   | Device |
+---------+                   +-----+                   +-------+                   +--------+
```
<!-- /diagram:role-assignment -->

#### Functions and builtins

Acting side: **Function** (`control/functions.py`); sensing side:
**Trigger** (`control/triggers.py`), pinned by `tests/test_vocabulary.py`.

- A `FunctionTable` sits beside the `RoleTable`; names match
  `[A-Za-z0-9_-]+` (they become DOM ids). **SCRIPTED**: target,
  `Condition`, script of `ScriptStep(offset, cue)`. **GENERATOR**: a
  `"triangle"` lane driver run each RUNNING tick by
  `control/generator_runner.py`, phase from elapsed run time, one per lane.
  **STREAM**: a gesture arg mapped onto lanes in `data()` before the
  handler (a verb may be stream-only). Same-verb streams may touch but not
  overlap on a lane; values past the domain hull edge-clamp, gaps drop. A
  scripted fire suppresses only the generator lanes it writes, for its span.
- `Condition.source` is `gesture-verb`, `bit-adjudicated` or
  `admin-manual`. A `verb` never auto-fires: the Bit returns a
  `FireFunction` (optionally with its own `at`, as MetronomeBit's beat grid
  does). `FunctionFired` keeps `fired_by` apart from `declared_source`, so
  a manual fire never reads as gameplay.
- `expand_script` makes `at + offset` cues; a `TARGET` step fans out to
  every resolved dev. Scripts cannot chain Functions.
- `EventTrigger`: device-side detection with server-owned thresholds, sent
  as the role blob's `"triggers"` on each non-ROOM grant.
  `StreamTrigger`: a server-side `"smooth"` EMA on gesture args in
  `data()`, seeded from the first sample, cleared when the dev leaves.
- **Built-ins** (`control/builtins.py`) derive from capabilities so
  diagnostics match everywhere: `flash` (`light.*`: white 0.9 for 5 s,
  chime first with `audio.samples`), `stop` (light or audio: one
  `MuteCue`), `ping` (`chime`, or a
  key-57 note pair on `audio.flsyn`). `RESERVED_NAMES` are refused on Bit
  and instrument tables, so a built-in is never shadowed.
- **The fire ladder** (`fire_function`; the Bit's table counts only in
  SETUP/RUNNING): (1) a non-empty-script entry fires as declared, refused
  whole if a cue kind is outside a destination's `accepted_cues`; (2) an
  empty-script name-fire or undeclared name resolves per dev, built-ins
  first, then the instrument's SCRIPTED function; (3) a dev resolving
  nothing is skipped and logged. An undeclared name targets SURFACE in any
  state, so the Console can fire `flash`/`stop`/`ping`. A fire reaching
  nothing still emits `FunctionFired` (`steps=0`) and returns `None`.
- `control/instrument.py` deliberately copies MetronomeBit's constants and
  `_fireworks_script()` for TUNESHROOM (Bits were not migrated), so edit both.

#### Cues (SolidCue, SURFACE, mute)

- `control/cues.py` kinds are told apart by type, not arity. A plain
  4-tuple or `LightCue` without `when` takes its producer's `at` (one
  gesture, one frame); `PlayCue` names an untimed device-local sample.
- Sentinels: `@room` (every declared fixture), `@all` (the operator's
  fixtures plus every connected device, lobby included), `@fixture:<name>`
  (Bit declarations only) and `TARGET`. A fixture resolves to its bound dev,
  else its own token. `FunctionTarget` is `ROOM`, `DEVICE`, `ALL` or
  `SURFACE` (operator-picked, via `FireFunctionCommand.dev`).
- **`SolidCue(dev, rgb, level, duration, when=None)`** paints a solid colour
  over the rendered frame, bypassing instruments, entirely Control-side at
  `DeviceLinkAgent`'s send seams: no device-wire change. After `duration`
  the frame force-resends; `None` latches.
- **`MuteCue`** (Stop, script offset 0) latches a surface in
  `GameServer.muted`: queued cues purged (`TimedQueue.purge`), Room voice
  silenced, blackout latched, and later breath, light, play and `SolidCue`
  dropped. Any non-mute fire at the surface un-mutes it first; there is no
  separate un-mute; UNLOADING clears all. A fixture's mute is keyed by its
  `@fixture:` token (`_mute_key`); a player's moves there when it binds.
- Room fixtures have no `audio.samples`, so a Room `flash` is light-only.

#### Wire JSON

**All outbound JSON goes through `control/wire_json.dumps()`** (only
offline `tools/` scripts use `json.dumps`): Python writes non-finite floats
as `Infinity`/`NaN`, which browsers and Dart reject, and one once blanked
the Console. `dumps()` sends `null` (the wire's "unbounded"), warns once per
path, and sets `allow_nan=False` so a miss raises. Test wire output on raw
text or with a raising `parse_constant=`, never bare `json.loads` (it
accepts the extension); never check browser JS by grepping source.

#### Device pool and stale reaping

- `control/device_pool.py` survives Bits. `carried` defaults to
  `DEFAULTSHROOM` (fallbacks: *Carried wire* under *Instruments and the
  catalog*).
- Every inbound message calls `touch()`. `reap_stale()` runs each tick from
  `DeviceLinkAgent.poll()` (`stale_timeout` 15 s) and frees a reaped
  device's role (or SETUP reservation) before `on_release`, so the slot
  reopens at once. A reaped device that never held a role is dropped from
  the transport and its canvas, invite and lobby state cleared
  (`_forget_reaped`). Room-bound devices are never reaped (Room liveness
  is undesigned). Only `reap_stale` removes one entry; `unload_room`
  clears all.
- `on_devices_change` fires before `on_registration_change` so
  `terrarium_boot`'s logger prints both "released" and "timed out".
- The heartbeat is the first `/game/hello` resent identically
  (`harness/o2_shroom.py --heartbeat-interval`, default 5 s, 0 disables);
  it pushes no `/room` and fires no `on_devices_change`. A dev heard from in its
  closing fade is marked revived, so that fade skips `transport.drop_dev`.

#### API version

`control/api_version.py`'s `TERRARIUM_API = 1` versions the Bit-facing
contract (interface, manifest v1, cue/Function/Trigger vocabulary); only a
breaking change bumps it. Every `bit.toml` sets `requires_terrarium_api`,
and discovery requires an exact match, else a `PackageError`.

#### Design bench and gesture eval

- `control/gesture_eval.py` copies `tools/trace_stats.py`'s edge math on
  purpose (that is an offline CLI). `evaluate_trace()` counts debounced
  rising edges past `peak_g`; `propose_thresholds()` puts `peak_g` at 0.8 of
  the weakest captured peak, under every gesture.
- `control/design_bench.py`'s `DesignBench` previews one `Instrument` on an
  injected `BenchSession`, with no Arco, transport or Bit, mirroring the
  engine's ladder, EMA and generator suppression. With one surface and no
  audio it drops `PlayCue`, and any fire but `"stop"` un-latches mute. A
  `_dirty` flag reports a frame after any state change, even pixel-equal.
- `harness/design_session.py`'s `LuxBenchSession` is the real session;
  `console/static/design.js`'s `applyProposal` edits raw TOML client-side.

### `control/`: Terrarium, Rooms, instruments and audio

`control/terrarium.py`'s `Terrarium` loads and unloads a Room (physical or
simulated LED, mic and speaker hardware): an ordered list of fixtures, each an
Instrument plus placement and binding. All pure stdlib.

#### Terrarium lifecycle

- `TerrariumState` is drawn below; observers get `on_terrarium_state_change`
  and `on_room_load_progress` (a raiser is logged and skipped).
- `load_room(name)`/`unload_room(force=False)` never raise; they return `None`
  or a reason, sent by the Console as an `error_event`. `load_room` broadcasts
  `"validating"`, `"sweeping"`, `"spawning arco"`, `"binding fixtures"`, `"room
  ready"`; any `BaseException` mid-load closes the room-scoped `TeardownStack`
  (`room_stack`) and returns to `NO_ROOM`.
- `unload_room` refuses outside `ROOM_READY`, and a non-IDLE Bit unless `force`
  (which aborts it); it saves bindings, closes `room_stack` and calls
  `gs.clear_devices()`, since every clock died with the Room's Arco. A loaded
  Room sets `gs.provenance` (`room_name`, `terrarium_config_version`), stamped
  into role blobs and `FunctionFired.room_name`. `loading_room` names the Room
  mid-load, so the harness spawns simulators for it rather than the boot Room.
- A Room load runs on the tick thread and blocks it (~9 s, once per boot: a
  Room change needs a restart). The Console shows elapsed seconds per stage
  (`console/static/elapsed.js`, ticked client-side), and `GET /prepare`
  answers 409 `room loading` instead of queueing behind the frozen tick.
- **Run records** (`control/run_record.py`; `--no-run-records` opts out): each
  spawned pid and spawn time goes to `runs/<run_id>/procs.jsonl`, and the next
  `load_room`'s `sweep_stale` stops any still alive with a matching spawn time
  (never by name). A dir whose `"supervisor"` (the `Terrarium`'s pid) is alive
  is skipped, so two stacks never reap each other. That sweep reads only its
  own checkout's `runs/`; `./terrarium.sh --clean` (`harness/clean.py`) runs
  it across every checkout in `git worktree list`, lists (never kills) live
  stacks with a `kill -INT <supervisor pid>` hint and unrecorded
  `run_stack`/`terrarium_boot`/`o2_shroom` processes, needs no Arco
  checkout, and exits 0 only when nothing is left running.
- Unwired: `ownership_probe`, and `recycle_room()` with the harness's
  `_recycle_room` (tests only: a round ending must never churn Arco).
- Arco has no message-based quit, so `ArcoProcess.shutdown()` sends SIGTERM
  (which `harness/signals.py` handles for our own processes). Its lazy pyarco
  import is the only one in `control/`: the rule is no luxaeterna, pyarco or
  o2litepy at module level (`tests/test_room_profile.py`).

<!-- diagram:terrarium-state GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
                    ┌───────────────┐                
                    │ ROOM_LOADING  │                
                    │               │                
                    └───────────────┘                
                        │   │   ▲                    
          ┌────ready────┘   │   └────────────┐       
          │                 │                │       
          ▼                 │                │       
  ┌─────────────┐           │                │       
  │ ROOM_READY  │           │                │       
  │             │           │                │       
  └─────────────┘           │                │       
          │                 │         load_room(name)
          │                 │                │       
          │    failure unwinds room_stack    │       
          │                 │                │       
    unload_room()           │                │       
          │                 │                │       
          ▼                 │                │       
┌─────────────────┐         │                │       
│ ROOM_UNLOADING  │         │                │       
│                 │         │                │       
└─────────────────┘         │                │       
          │                 │                │       
          └──────────────┐  │  ┌─────────────┘       
                         │  │  │                     
                         ▼  ▼  │                     
                      ┌──────────┐                   
                      │ NO_ROOM  │                   
                      │          │                   
                      └──────────┘                   
```
<!-- /diagram:terrarium-state -->

**One Arco per Control process.** pyarco's `arco.initialize()` returns early
once o2lite has ever synced and `finish()` cannot prepare a restart, so a
process can talk to one Arco: upstream (reported to Roger Dannenberg, no fix
pending) and treated as the design. In `console/agent.py`:

- While `clients_live` (Control's o2lite transport and `ArcoSynthPool` have
  started) holds, Unload is refused (`control/terrarium.py`'s
  `unload_room_refusal`, shared with `UplinkAgent`; "stop and run
  ./terrarium.sh", shown as the Rooms row's `unload_blocked`). It is not
  enforced in `Terrarium.unload_room`, whose shutdown callers must still
  take the Room down.
- `load_bit` takes a `room`; `_ensure_room_for_bit` checks `room_types`,
  existence and `validate_rooms` before touching anything, so a refusal never
  strands `NO_ROOM`. From `NO_ROOM` it loads the Room and restarts the clients
  (`TRANSPORT_READY ... (restarted)`; on failure it broadcasts
  `room_load_failed` and unloads). Another Room while one is ready is refused,
  naming `./terrarium.sh --room <name>`; the picker disables it.
- ABORT unloads the Room only when Unload would be allowed; with live clients
  it ends the Bit alone. `stop_room_clients` is kept, uncalled.
- **Operator recipe.** Boot roomless (`./terrarium.sh`) and load a Bit, which
  brings up its Room; or boot `--room X` and load only Bits listing X. Never
  unload; to change Room, stop and rerun `terrarium.sh`.

**Deliberate deviations from the lifecycle spec's prose** (current):

- Its `start_terrarium()` is `harness/terrarium_boot.py`'s `build()`/`main()`
  owning the Terrarium-scoped stack: the harness already builds transport and
  Console, and a `control/` constructor would duplicate `build()`'s many-site
  tuple contract.
- `--list-bits` needs a loadable `--config` (default `terrarium.toml`), whose
  `bit_paths` are the Bit roots.
- `CaptureBit` provenance is deferred (it accepts `provenance`; `load_bit`
  never passes one). The instruments spec's one: `requires="room"` passes at
  handshake (*Instruments and the catalog*).

#### `terrarium.toml`

- Schema 1, `control/terrarium_config.py`; every defect is a located
  `TerrariumConfigError`. `[terrarium]`: `name`, `bit_paths`,
  `instrument_paths`, `room_paths` (defaults `bits`, `instruments`, `rooms`,
  relative to the config file; `instrument_roots[0]`/`room_roots[0]` are the
  Console's design roots). The shipped file holds only `schema` and
  `[terrarium]`; inline `[instruments.<name>]`/`[rooms.<NAME>]` still parse,
  but a name both inline and in a catalog ("pick one home"), or in two roots,
  is refused. `version` hashes this file alone
  (`f"{schema}-{sha256(text)[:12]}"`).
- A Room: `description`, `backends` (`devicelink`, `array`), `node_id` (default
  `ROOM_<NAME>_NODE`), `[[fixtures]]`, `[arco] ready_timeout` (15 s;
  `--arco-ready-timeout` overrides). `[arco] settle_seconds` parses but nothing
  reads it; `--arco-settle-seconds` applies.
- `validate_rooms()` gives each Room `None` or a reason: fatal for the Room
  being loaded, advisory (Rooms panel) for the rest. An `array` Room needs the
  simulator or `[[artnet]]` on every fixture; `terrarium_boot` always passes
  `array_backend="simulator"`, so every Room loads, and `BootConfig` refuses
  other values (a real array is `[[artnet]]`).

#### Rooms and fixtures (TEST, DEMO, VENUE)

- `control/rooms.py`'s `Room` has `bound` (fixture to dev);
  `control/room_profile.py`'s `RoomProfile` is N `RoomFixture`s end to end in
  physical order, each with `color_order` (RGB or RGBW, one per Room),
  `blocks`, `zones` and a required `instrument`.
- **Blocks are hardware, zones are targets.** A `RoomBlock` (max 170 px) is one
  physical run; blocks tile the fixture and sum to its `pixel_count`, and only
  `harness/o2_shroom.py --identify-blocks` reads them (a colour per block, to
  check a build-out by eye). Zones may not overlap or overrun. `primary` is
  synthesized by the adapter, never declared or drawn.
- `channel_count` (3 or 4 per pixel) is the one frame width; a simulator drops
  a wrong-width frame rather than truncating, so a mismatch renders nothing.
  luxaeterna's `Universe` defaults to 512: pass `channel_count=`.
- **TEST**: GRB `main` (60 px, `dev_strip_main`) and `accent` (30 px,
  `dev_strip_accent`: identical instruments, separate identities). **DEMO**:
  one RGBW `array`, 864 px (the real 6 m array), blocks `m1`..`m6` of 144,
  zones `left`/`center`/`right` of 288, `venue_array`. RGBW is Art-Net wire
  order; WLED holds the strip's physical GRBW.
- **VENUE**: RGBW `bars` (DEMO's `array`) and `fiber` (a block `e1`..`e3` and
  zone `b1`..`b3` per fiber-optic engine; `venue_fiber`, light only, so one
  drone), each on its own WLED controller and `[[artnet]]`-covered, so VENUE
  loads with nothing bound. N = 1 LED per engine is a placeholder
  (`rooms/VENUE.toml` and `tests/test_venue_room.py`'s `N` change together).
  Bits address `@fixture:bars`/`@fixture:fiber` and targets
  `bars[.left|center|right]`, `fiber[.b1|b2|b3]` or `primary` (both); TestBit
  and MetronomeBit list it.
- `[psus.<name>] amps` with `[[artnet]] psu`: outputs on one PSU sum `max_amps`
  to at most 80 % of it, across every Room (one box, one supply). An output
  with no `psu` is unchecked.
- **The engine synthesizes the ROOM role.** A non-empty `Bit.room_manifests()`
  makes `load_bit` merge a `room_role()` (role `room_<name>`, `RoleClass.ROOM`,
  capacity = fixture count). Light targets are `primary`, `<fixture>` or
  `<fixture>.<zone>`, sliced per fixture by `role_config.slice_light_manifest`;
  any other, or a missing `@fixture:`, is a `BitLoadError` (why `bits/chase/`
  lists TEST only).
- **Shown by addition.** The Room's node id, registration counts and role name
  stay filtered from Console and uplink (`non_room_counts()`): its
  Registration Node grants control of the rendering backend, while its
  instruments are not a credential. Its instruments,
  zones and controller values come through `control/room_view.py`'s separate
  `room` payload, so no filter was ever loosened: extend it that way. Light and
  audio instruments share one list keyed by `kind`: both read one MIDI stream.

#### Room binding

- `control/room_binding.py`'s `RoomBindingRegistry` is Control-global, keyed by
  Room and fixture. `arm(room, fixture, seconds)` names the fixture the next
  Room-node handshake binds, one per Room at a time; an unarmed Room node answers
  "no such node". The Console's `arm_room` handler (`console/agent.py`), not
  the registry, refuses an `[[artnet]]`-covered fixture.
- `load_room` skips an `[[artnet]]`-covered fixture; otherwise the harness's
  factory spawns a `sim-room-<fixture>` o2lite client on `room_stack`, or a
  recorded, connected device rebinds; the rest go to `wait_for_room_binding`
  (arming in order within `room_setup_timeout`). A partly bound Room proceeds;
  `RoomBindingTimeout` fails only when nothing bound and nothing is covered.
- **Save and load exist**: `save()` writes bound dev ids per fixture as JSON
  (never the armed window), `load()` restores them (missing file: no-op; old
  flat format: ignored). `load_room`/`unload_room` call them given a
  `binding_store_path`; `build()` accepts one but `terrarium_boot`'s `main()`
  passes none, so nothing persists and each restart needs an admin-armed
  tap to rebind a physical Room device.

#### Instruments and the catalog

- `control/instrument.py`'s `Instrument`: `capabilities` (`light.pixels`,
  `light.surface`, `audio.flsyn|samples|mic`, `gesture.tap|tilt|hold|swing`),
  `pixels`, `functions`, `accepted_cues` (`midi`/`play`/`solid`/`mute`),
  ambient manifests, triggers, `solo`. `validate_instrument` refuses unknown
  tags, `light.pixels` under 12 px (at load or publish, never on a device) and
  unaccepted script cues.
- `TUNESHROOM` (12 px, samples, mic, tap/tilt) and `DEFAULTSHROOM` (the floor:
  12 px, tap/tilt, an aurora ambient so an idle unknown device is visibly
  alive) are code constants pinned equal to their `instruments/*.toml` by
  `tests/test_catalog.py`: edit both. Thresholds are mm-tuneshroom's
  `TapDetector` constants, not measured. Also published: `testshroom`,
  `tuneshroom_rev1`, the dev strips, `venue_*`.
- **Carried wire.** `/game/hello`'s optional 4th argument names the instrument,
  resolved against `carried_instruments`: unknown or non-`light.pixels` falls
  to `DEFAULTSHROOM` with a deduped `on_device_warning`, absent silently, and a
  bare re-hello keeps the old one. A granted non-ROOM blob carries
  `"instrument"` (`docs/carried-instrument-schema.md`). A Tuneshroom not
  declaring `"tuneshroom"` loses mic, samples and its functions.
- **Requirements** (`Bit.instrument_requirements()`) match contracts, not
  names. Room slots resolve at `load_bit` against all fixtures together
  (`min_pixels` against the total), a miss naming each fixture's `satisfies()`
  reason; non-empty `room_manifests()` adds a `"room"` slot (`light.surface`,
  `audio.flsyn`). Role slots (`Role.requires`) resolve against the carried
  instrument at handshake (a refusal frees the slot and denies) and in the
  jam walk (a failing role is skipped). `requires="room"` passes: a
  fixture has no gestures, so checking it would make gesture-gated roles
  unloadable.
- **Instrument `[[functions]]`**: `kind` defaults to `"generator"` (a `lane`
  table); `"scripted"` needs a non-empty `script` of steps, each an `offset`
  plus one of `midi = [status, d1, d2]`, `play`, `solid = {rgb, level,
  duration}`, `mute`. All are TARGET-implicit: an explicit `target` or
  `condition`, a non-`TARGET` step, `@fixture:`, a non-`@target` generator
  (`lane.dev = "room"` parses, then fails) and STREAM are refused
  (`control/functions.py`), because an instrument is a type that cannot know a
  Room. The dev strips and `venue_array` carry `play_aurora`, `win`,
  `fireworks_room`, `fail_room`, `finale`, `metro_downbeat`, `metro_click`,
  `metro_pulse_room`; `TUNESHROOM`'s `play_aurora`, `win`, `fireworks_player`,
  `fail_player`, `metro_pulse_player`, `metro_recovery` sit on its Python
  literal. None ships a generator. `[solo]` (on `tuneshroom`) is no-hub
  behaviour (ambient plus tap/double-tap/shake bindings;
  `tools/export_solo.py`).
- **Catalog** (`control/catalog.py`, kinds `instrument`, `room`): `*.toml`
  published, `drafts/*.toml` drafts, name = stem, keyed `"<state>:<name>"` so a
  draft never shadows. A missing root is empty; a bad published entry fails
  hard, a draft's error lands on `CatalogEntry.error`. Writes never raise:
  `save_draft` always writes under `drafts/` (invalid text too, errors returned
  apart), `clone_entry` copies bytes, `publish_entry` re-validates, then
  renames atomically.

#### LED layout models (`control/model_layout.py`)

An instrument's `instruments/<name>.toml` may declare `model =
"models/<name>.glb"`, a path relative to `instruments/`, pointing at an
artist-authored glTF binary. `control/model_layout.py` (pure stdlib: no
third-party glTF library) reads the file's JSON chunk only, finds LED
marker spheres under a node named `LEDs` (one sublayer per zone, e.g.
`LEDs::ring`), and resolves each into millimetre position, size band, and
zone -- `Instrument.layout`, a tuple of `PixelLayout`. `Instrument.
model_sha256` is the source file's own SHA-256; it ships on the
carried-instrument wire blob (`docs/carried-instrument-schema.md`) only for
an instrument that declares a model. A published instrument with a missing
or invalid model fails to load, exactly like invalid TOML; a draft records
the error on its `CatalogEntry` instead. See
`docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md`
in the mm-tuneshroom repo, sections 3-5, for the full picture -- the
consumer is mm-tuneshroom's 3D view (`docs/instrument-model-guide.md` is the artist's brief). Neither
the parser nor `Instrument.layout` is read by the server in this slice.

**Bake and export.** `tools/bake_model.py` bakes per-LED light maps in
headless Blender (Cycles Diffuse + Transmission per LED, summed, row-flipped,
normalised by the brightest texel, packed 4 LEDs per RGBA PNG) and writes
`<stem>.baked.glb` beside the source; `tools/model_bake_helpers.inject_bake`
is the one writer of the baked-file contract (`extras.mm_bake`, spec
section 4.1) and refuses anything `validate_baked_glb` rejects.
`control.model_layout.layout_to_json` is the one layout serializer (bake,
fixture generator, export). `.venv/bin/python -m tools.export_models
<mm-tuneshroom checkout>` (stdlib, no Blender) copies every published
instrument's fresh bake to `assets/models/<name>.<hash8>.baked.glb` with
`models.json`, refuses a stale bake or one whose layout differs from the
catalog's, and copies the shared fixture pair into `test/fixtures/models/`;
it writes nothing unless every model exports. Before the first export that
carries a real model, mm-tuneshroom's startup must stop awaiting every
bundled model.

**Bake host: Mycologist, Blender 4.5 LTS** (`PINNED_BLENDER = "4.5"`;
Intel Mac, and Blender 5.x ships no Intel macOS build; CPU-only Cycles).
Installed by the operator at `/Applications/Blender.app`. Claude bakes as
`claude-ops` through the `portal` skill, with the operator's go-ahead per
run: `git archive HEAD control tools <model dir> | portal ssh mycologist
'... tar -x -C ~/mm-bake/src'`, then `nice -n 10
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup
--python-exit-code 1 -P tools/bake_model.py -- <model.glb>` in
`/Users/claude-ops/mm-bake/src` (disposable), then copy the bake back as
base64 and compare sha256 on both ends. After a Blender upgrade, re-run
`tools/blender_probe.py` there before moving the pin. The committed
`tests/fixtures/models/marker_fixture.baked.glb` is the first real bake
(256 px, the T2 gate).

**Room fixtures and the Tower.** `_parse_room` refuses a fixture whose
instrument disagrees with it (`fixture_instrument_mismatch`,
`control/room_profile.py`): a declared `pixels` must equal the blocks' total,
and a model must have one marker per pixel with each marker's zone one of the
room zones covering it. Instrument publishes get the same check from the
other side: `publish_entry(..., rooms_root=)` (wired in the Console's Design
Panel) refuses an instrument that would break a published room binding it,
so a publish can never leave the Terrarium unable to boot.
`rooms/TOWER.toml` is the first fixture this matters
for: 14 px (`progress` 0-7, `responder` 8-11, `beat` 12-13, the two base PARs),
`GRB`, instrument `tower` with no model yet, so it runs on the no-layout
fallback (a 14-dot strip in the Console). The artist's brief and the import
recipe are `docs/tower-model-brief.md`; `tests/test_tower_import.py` dry-runs
the import. Spec: `docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md`.

#### Per-fixture light sessions and sinks

- `DeviceLinkAgent._setup_room` gives every declared fixture, bound or not, its
  own `LightSession` (local zone names) from Room load; the Room has none. It
  renders its slice of a Bit's ROOM manifest, else its ambient one.
- **Instruments animate ambiently**: with no Bit, each fixture's instrument
  generators drive its own lanes; a Bit's ROOM role supersedes them.
- `_resolve_devs` makes `@room` every declared fixture in order (a broadcast,
  never collapsed) and `@fixture:<name>` its bound dev or own token; generators
  emit per fixture, so a scripted fire suppresses only its own fixture's lane.
- Each fixture has its own `Universe`, override and mute, sends only a changed
  frame (a rebind forces one) and stamps its own `when`.
- `control/fixture_sink.py`: a sink is anything with `send_frame(frame, when)`.
  `ConsoleFrameSink` from Room load (the Console keeps the latest frame per
  fixture at about 10 Hz, dropping, never queuing), `DeviceLinkSink` while
  bound, `[[artnet]]` third. `room_frame` is keyed by fixture name, so an
  unbound fixture still paints.

#### Audio

- `control/audio.py`'s `AudioBridge`: `on_grant` takes a voice, program and
  declared cc lanes; `feed_midi` applies notes, programs and lane-mapped cc (an
  undeclared cc is dropped: a lane is a real remap seam); `start_drone` holds
  the declared drone (FluidSynth is silent without a note); `silence` is Stop's
  audio half; `play_note` rings a welcome or lobby bell on a transient voice.
  It **never imports pyarco**, and `feed_midi` takes no `when` (timing is
  Control's `TimedQueue`).
- **Provisional.** The type is `DeviceVoice`, not `Synth`, with no channel
  parameter (open with Roger); `ugen_manifest` v0 is shallow-validated, has no
  cross-repo contract, and never ships to a device (boundary rule 1).
- `harness/arco_synth.py`'s `ArcoSynthPool`: one `Flsyn`, up to 16 voices (a
  MIDI channel each), pyarco imported in `start()`, `poll()` pumping
  `sched.poll()` from the tick (`harness/` is a holding position). No
  `schedule_at()`: pyarco's scheduler raises on a past time where `TimedQueue`
  clamps, silently killing audio on a late run. `shutdown()` runs
  `arco.finish()` in a `finally` (else `Ugen.__del__` hits a dead socket at
  exit); `quiesce()` drops handles with no wire traffic so `start()` can rerun.
- The soundfont must be General MIDI (`FluidR3_GM.sf2`, or `$MM_SOUNDFONT`): a
  non-GM font killed the drone. Fix fonts, not programs.
- **Per-fixture voices.** The Room owns no channel.
  `DeviceLinkAgent._grant_room_audio` gives each `audio.*` fixture a voice
  keyed by name, bound or not: the Bit's ROOM role, else its ambient ugen
  (droning at once), else `_DEFAULT_FIXTURE_ROLE` (cc:11 pass-through, so
  `ping`/`stop` always land). Only the first fixture plays the welcome.
- **`AudioBridge.shutdown()` is terminal**: it frees every voice, always shuts
  the pool, and then `on_grant`/`play_note` are no-ops. Harness teardown shuts
  it before the Room unwinds, whose forced abort re-grants every fixture; that
  used to raise.
- **The breath** (`control/breath.py`): a 6 s cc:11 envelope so light `level`
  and sound expression swell from one number. A role declaring aurora's `level`
  no longer breathes on its own, so every renderer must be fed it:
  `DeviceLinkAgent` sends it to granted devices on change (not muted, fading or
  `breath=False` ones); the lobby feeds the fixtures.

### `devicelink/`: the device-facing side over o2lite

The inbound sibling of `console/` (trusted LAN, no auth).
`devicelink/o2_transport.py` moves O2 messages; `DeviceLinkAgent`
(`devicelink/agent.py`, transport-agnostic, driven by `poll()` each tick)
holds a `LightSession` per granted device and per fixture, ships
`JoinResult.config` verbatim as `/<dev>/role` and sends changed frames as
`/<dev>/leds`. Design:
[`2026-08-12-control-o2lite-and-timed-cues-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-12-control-o2lite-and-timed-cues-design.md).

#### Transport

- **`O2LiteTransport`** is Control's `game` service on the hub:
  `drain_inbound`, `send`, `bind_dev`, `drop_dev` (a device is anonymous
  until hello). `send` routes by the down row's transport: `tcp` rows
  through o2litepy's `send_cmd`, `udp-ok` rows through `send` (the hub
  relays a TCP send to an o2lite client over its TCP socket, probed
  2026-10-01). It never imports
  o2litepy (`start()` takes a connected object; `Blob` duck-types
  `O2blob`), so the offline suite needs none. The agent holds it as
  `.transport` (`.server` before PR #153) and requires `clock=`
  (`o2lite.time_get`): one clock for Control and devices.
- **Control is a guest on pyarco's connection** (one o2lite connection per
  process). pyarco announces `actl`; `set_services` **replaces**, so
  Control writes all of `SERVICES = "actl,game"`, or Arco's replies stop.
- `start()` refuses an unsynced clock (`time_get() < 0`) or a missing
  `actl`, then `verify_service_ownership` round-trips a TCP probe on
  **both** services (resent every 2 s, 10 s timeout); either failing is
  fatal (without `actl` the next ugen build hangs). Its `pump=` is now redundant
  (the pty drains itself; see "The Arco pty gotcha" under Running it).
- **o2lite facts**, each of which breaks the link if ignored:
  - handlers take `(address, types, info)`, **pull** args in typespec
    order (`pull_args`) and get the address without its leading `/`;
  - a blob must have `.size`/`.data`; a bare int list raises (`to_o2_arg`);
  - handlers run only inside `o2lite.poll()`, which `drain_inbound()` pumps;
  - `method_new` appends and dispatch takes the **first** match, so the
    svcheck handler is registered once per (connection, service);
  - a **UDP** send right after `set_services()` can beat the registration
    to the hub and vanish (Roger found it in his own test program): send
    the first message over TCP, as the ownership probe does.
- `bind_dev` refuses a dev id over 31 characters (`MAX_DEV_LEN`; it is the
  device's O2 service name). `send()` to an unknown dev is a silent no-op
  (so `/<dev>/release` goes out **before** `drop_dev`), and a typespec and
  argument count that disagree are refused, never truncated.
- **`FakeO2Lite` is as strict as o2litepy** (boundary rule 5): only
  `poll()` dispatches, handlers are first-match, `refuse()` loses a claim.

#### Message vocabulary

- **`devicelink/contract.py`'s `VERB_TABLE`** is the source (contract v3):
  a `VerbRow` per verb (typespecs, arg names, `tcp`/`udp-ok`, `pre_role`).
  Up (`/game/<verb>`): `hello` (`ssss` dev, name, protoversion,
  instrument; bare `s` still accepted), `handshake` (`sss` dev, round_id,
  node), `start`, `tap`, `tilt`, `shake`, `hold`, `swing`, `canvas`
  (simulators), `capture`, `telemetry`. Down (`/<dev>/<verb>`):
  `handshake` (`s` round_id), `validated` (`ss` round_id, role), `deny`
  (`ss` reason, hint), `role`, `room`, `release`, `error`, `leds`, `play`.
  Before a role a device may send only `hello`, `handshake`, `start` (and
  `canvas`).
- **Down rows carry a transport**: `tcp` for `role`, `deny`, `release`,
  `room`, `error`, `handshake`, `validated`; `udp` (`udp-ok`) for `leds`,
  `play`. `FakeO2Lite` records the channel per message, so a test can
  assert `/role` went TCP.
- **`/game/join` is retired**: `contract.RETIRED_UP_VERBS` keeps it
  registered only so Control can answer `/<dev>/error ["join", "retired in
  contract v3: use /game/handshake"]`; it changes nothing else.
  `GAME_VERBS` derives from the up rows; `tests/test_devicelink_contract.py`
  checks each `protocol.py` builder against the table.
- In-process a message is an o2ws-shaped JSON envelope
  (`protocol.Envelope`); on the wire, an O2 message. Malformed: dropped.
- **Wire flavor.** o2ws has no blob type, so a hello `protoversion`
  starting `o2ws/` gets three messages as strings; all else is identical:

| Message | blob flavor (hardware, native, Testshroom) | string flavor (`o2ws/*`) |
|---|---|---|
| `/<dev>/role` | `b`, UTF-8 JSON of the composed config | `s`, the identical JSON text |
| `/<dev>/room` | `b`, UTF-8 JSON of the room blob | `s`, the identical JSON text |
| `/<dev>/leds` | `b`, raw channel bytes; timestamp is the presentation time | `s`, base64 of the identical bytes; same timestamp |

- Only `O2LiteTransport.send` rewrites, keyed by the protoversion recorded
  at hello (a handshake rebinds without erasing it); a string holding `0x03`
  (o2ws's field end) is refused. The agent and `protocol.py` are blind.
- `/<dev>/room` (state, Bit, nodes, counts) goes out on first contact
  (a dev new to the pool) and on every state or registration change, never
  on a heartbeat hello. `/game/telemetry` is chunked under the
  4096-byte cap (`chunk_telemetry_batch`; `tests/test_capture_o2.py`).

#### Grants, release and overrides

- **Grants happen at RUNNING** (or at a RUNNING walk-up's first hello):
  the engine's `on_grant` sink calls `DeviceLinkAgent._on_grant`, which
  builds the `DeviceBridge` and sends `/<dev>/role` once per round. A
  validation in SETUP builds nothing. There is no role switch: a re-grant
  of the held role is a no-op, and a `requires` refusal at handshake frees
  the reservation before any bridge exists.
- **Release is asynchronous, and that is load-bearing.**
  `LightSession.clear()` only enqueues the fade, so a device dropped at
  release freezes on its last frame. It stays in `_closing`, rendered until
  `CLOSING` ends, then gets `/<dev>/release` (`_MAX_CLOSING_FRAMES`, 200,
  forces it). A new grant clears it; drivers poll while `.closing` is nonzero.
- **A ROOM binding builds no bridge and sends nothing**; the fixture's frames
  just start. `_drop_player_bridge` forgets a player-era bridge at once
  (no fade, `/release` or `drop_dev`), else the device gets two LED
  streams. The Bit is not told its player left (*Not yet built / deferred*).
- **Overrides** (`SolidCue`, lobby flashes, mute blackout) are painted in
  the strip's channel order (`SolidCue` names R, G, B; W stays 0). A
  hello'd device with no role shows its lobby status pulse (`_bases`, from
  `LobbyRuntime`'s `set_base` sink: white while invited, green once
  validated) with any override on top, black while muted; when both are
  gone it gets one black frame.
  `_finish_release` and `unwire_room` drop overrides, since a blackout
  never expires. A muted surface ignores `SolidCue` (*Cues (SolidCue,
  SURFACE, mute)*).
- **Lobby** (*Lobby and the handshake*): FULL still breathes the light;
  RUNNING restores the Bit's light and program.

#### Timed cues and `cue_horizon`

- **`TimedQueue`** (`control/timed_queue.py`) releases `(when, payload)`
  at the drain covering `when`; payload-generic (Control queues MIDI, a
  device frames). `when=None` is not a clamp; a past `when` is. A sequence
  number stops `sort()` comparing payloads. `lateness` holds signed
  samples (bounded, 20000); `purge()` serves mute, `next_due()` Art-Net.
- **One gesture, one `at`** (*Bit interface*; `BootConfig.cue_horizon`,
  0.060 s). Light feeds the session now (held to `at - horizon` if further
  out, so it cannot leak into a breath frame); the frame is stamped `at`
  and Room audio waits on `_room_cues` until `at`. An uncued frame gets
  `clock() + horizon`; the earliest pending `at` wins, popped per render.

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
            ┌────────────┐ ┌─────────────────────┐┌──────────────┐
            │Bit handler │ │GeneratorRunner.cues ││Bit.fires(at) │
            │            │ │                     ││              │
            └────────────┘ └─────────────────────┘└──────────────┘
                   │                  │                  │        
                   └───────────┐      │     ┌────────────┘        
                               │      │     │                     
                               ▼      ▼     ▼                     
                         ┌──────────────────────────┐             
                         │GameServer._dispatch_cues │             
                         │                          │             
                         └──────────────────────────┘             
                                      │                           
                     ROOM resolved to bound fixture devs          
                                      │                           
                                      ▼                           
                      ┌────────────────────────────────────┐      
                      │on_light_cue (transport-owned sink) │      
                      │                                    │      
                      └────────────────────────────────────┘      
                          │                     │                 
                          ▼                     ▼                 
                ┌──────────────────────┌───────────────────┐      
                │Feed light session now│Queue audio for at │      
                │                      ││                  │      
                └──────────────────────└───────────────────┘      
                          │                     │                 
                          ▼                     ▼                 
                ┌──────────────────────┌─────────────────────┐    
                │Control renders frame │Arco voice fed at at │    
                │                      │                     │    
                └──────────────────────└─────────────────────┘    
                          │                                       
               luxaeterna render_into                             
                          │                                       
                          ▼                                       
         ┌───────────────────────────────────────┐                
         │FixtureSink.send_frame(frame, when=at) │                
         │                                       │                
         └───────────────────────────────────────┘                
                          │                                       
                    over the wire                                 
                          │                                       
                          ▼                                       
          ┌──────────────────────────────────────────┐            
          │Device holds the frame, displays it at at │            
          │                                          │            
          └──────────────────────────────────────────┘            
```
<!-- /diagram:cue-path -->

**`cue_horizon` is measured, and 60 ms is right.** Live o2lite run, real
Arco, 2418 frames at `--horizon 0` (nothing held, so genuine delivery;
the 42 slightly negative samples mean the clocks agree to well under 1 ms):

| p50 | p95 | p99 | p99.9 | worst |
|-----|-----|-----|-------|-------|
| 4.5 ms | 9.3 ms | **11.8 ms** | 38.6 ms | 80.2 ms |

- Control's 44 Hz tick (22.7 ms) + delivery (11.8 ms p99) + device tick
  (~5 ms) is ~40 ms, leaving ~20 ms of jitter headroom. **p99, not worst
  case**: the horizon delays every cue; one hiccup must not tax them all.
- **The trap: O2 delivers each frame at `when`**, so a device queue
  re-checking on arrival finds it a few ms late at any horizon: 93.3%
  clamped at 150 ms and 95.6% at 300 ms, lateness pinned near +3 ms with a
  floor near -2 ms in both runs. Doubling the horizon doubled the apparent
  latency (154 ms to 304 ms) without reducing clamping, which rules out a
  clock offset (an offset would shrink as the horizon grew). So device
  clamp counts (`ShroomClient.clamped`) saturate; read `lateness`.
  `DeviceLinkAgent.clamped` (Room audio) still means "horizon too small".
- **Method: measure at `--horizon 0`**, the only setting where nothing is
  held. A generous horizon looks safe and once produced a false "~67 ms":
  60 ms of horizon plus ~6 ms of that delivery overhead. Tooling:
  `TimedQueue.lateness` -> `ShroomClient.lateness` ->
  `harness/o2_shroom.py --control-horizon --samples-out` ->
  `python -m harness.sync_bench SAMPLES.json --offset <horizon>`, whose
  `summarise()` takes absolute values: convert one-way latency first, or
  an 80 ms early frame reads as 80 ms of error. All dev-box figures;
  whether the device queue is redundant is open.

#### Service refusal

**A refused o2lite service announcement is unobservable from the client,
and that is O2 working as designed** (Roger Dannenberg's ruling, 2026-08-16):

- A service goes to one provider: full O2 picks the highest IP and port
  (which can change as processes come and go); **o2lite keeps no fallback
  list (first-come-first-serve)**. Two claimants of one name is a client
  design error; expect no upstream fix.
- `/_o2/*/sv` is fire-and-forget: a refusal (`o2/src/bridge.cpp:231-237`)
  logs on the hub only, with no ack, error callback or registration query;
  the loser looks healthy while its traffic goes to the winner. Roger
  sanctioned detecting it by round trip, which is what
  `verify_service_ownership` does (it detects, never fixes).
- **Naming meets the rule by construction** (`actl,game`,
  `sim-room-<fixture>`, each player's dev id), his per-process namespacing
  in substance. Names from `o2lite.bridge_id` (his other idea) are out:
  unique per host only, and blind to the one collision seen, an orphaned
  run re-claiming its own name on reconnect. Namespacing cannot stop that
  (the orphan has the same namespace), so the guards target orphan
  lifetime: `--exit-with-parent`, `TeardownStack` and the probe (*The
  orphan chain* under *`run_stack`*).

#### Browser guests over o2ws

A browser cannot run o2lite's C library, so it joins as an o2ws guest of
the hub with the string flavor. Design: [`2026-09-08-o2ws-browser-link-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md).

- **`harness/www_server.py`** serves `www/` on 8788 at `0.0.0.0` (LAN
  guests, static files), not Arco: O2's HTTP server labels every file
  `text/html`, which a Flutter build's `.wasm` and modules refuse. Arco
  serves `www/` on 8080 for o2ws. A failed bind only warns; it prints
  `WWW_URL:` (collected by `run_stack`; IP from `lan_ip()`'s UDP-connect
  probe). `--www-port` (`0` off); `run_stack --web-build DIR` replaces
  the guest tree's `app/` with a Flutter build.
- **A Flutter build needs base href `/app/`** or the page is black:
  `tool/sim build --base-href /app/`, or `run_stack.stage_web_build`
  rewrites `<base href="/">`.
- Cross-origin o2ws (page 8788, Arco 8080) works; `www/o2ws.js` carries
  three patches (*`www/` and `arcoserver/`*). Real-phone timing is unmeasured.
  A refused name is not retried (the dev id is fixed before connect).

#### Fixture sinks and Art-Net

Design: [`2026-09-23-artnet-fixture-sink-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-23-artnet-fixture-sink-design.md).

- **Outputs persist per Room** (a per-tick sink would drop its thread and
  socket): `_ensure_outputs` builds them once via `outputs_for` (a failed
  `start()` drops that sink); `_close_outputs` closes them on unwire. A
  play cue to an unbound fixture drops, warned once per Room.
- **`ArtNetFixtureSink`** (`devicelink/artnet_sink.py`): `send_frame` only
  locks, pushes and notifies (boundary rule 2). A sender thread sends at
  `when - lead_ms` (WLED has no clock), newest due frame wins, and the last
  frame repeats every `keepalive_ms` to hold realtime mode. Every frame,
  close-to-black included, passes a `PowerLimiter`; a stuck thread skips
  the black frame. RGBW only (4 ch/px, 128 px per universe).
- **`[[artnet]]`** (`ArtNetOutput`): `room`, `fixture`, `host`, `max_amps`
  required; `start_universe` 0, `port` 6454, `amps_per_pixel_full` 0.025,
  `lead_ms` 0, `keepalive_ms` 250, `psu`. Refused: a non-RGBW fixture, two
  outputs per fixture, overlapping universes per host:port, a universe
  past 32767. Coverage and PSUs: *Rooms and fixtures (TEST, DEMO, VENUE)*
  and *Room binding*.
- `harness/artnet_listen.py` is a strict fake WLED: DEMO got ~34-40 fps
  pre-`TickPacer`, 0 gaps (dev-box loopback); paced, ~38-39 fps (*Tick
  pacing*). Spec section 9 gates real hardware.

#### The device contract and `contract_kit/`

One checked device-wire contract, owned here, shared by the Testshroom,
mm-tuneshroom's Flutter app and mm-devshroom's Rev 1 ESP32 firmware
(Victor's): the verb table, `instruments/tuneshroom_rev1.toml` and
`HELLO_INTERVAL_S` (5 s; Testshroom, recorder and export all read it).
Design: [`2026-09-16-device-contract-kit-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-16-device-contract-kit-design.md);
firmware guide: `docs/device-contract-guide.md`.

- **`contract_kit/`**: a test-only `ContractBit` (never under `bits/`) and
  a `Recorder` driving the real engine and agent over `FakeO2Lite`,
  deterministically, at `CUE_HORIZON_S` (read off `BootConfig`).
  Eighteen scenarios sit in `contract_kit/recordings/`; `.venv/bin/python -m
  tools.record_scenarios` re-records; `tests/test_contract_scenarios.py`
  fails on any diff.
- **Export**: `.venv/bin/python -m tools.export_contract <out-dir>` writes
  `contract.json` (verbs, limits, lifecycle values from their owning
  constants, the Rev 1 instrument, scenario index, notes, `step_schema`)
  and byte-copies the scenarios, refusing a mismatch with `ALL_SCENARIOS`.
  Device repos commit it at `test/contract/`; it alone must suffice.
- `CONTRACT_VERSION` (3) bumps on any device-observable change. 2 added
  `link_loss_keeps_display`, whose outage frame is hand-authored
  (`expect_frame_held`): the recorder sees only Control. **3** is the
  handshake: `device.join_node` became `device.handshake` (`null` or
  `{node, ack_after_ms}`, the device's own accept policy), the `join` step
  became `accept` (an input, never inferred from an `expect_out`), every
  Control-minted round id is the `$ROUND` placeholder, and the down rows
  carry their transport. New scenarios: `handshake_validate_then_role`
  (replacing `explicit_join_role` and `lobby_tap_join`),
  `handshake_over_cap_deny`, `handshake_stale_round`,
  `late_hello_gets_jam`, `jam_solo_fallback`, `room_node_handshake_binds`,
  `join_retired_error`, `link_blip_keeps_role` (a device keeps its role
  and round id across a link loss until a later `/role`, `/handshake` or
  `/release` supersedes them). `ContractBit` declares a bounded scored role and a
  jam role; `SoloContractBit` (no jam role) backs `jam_solo_fallback`. The firmware-side rules and the v3 checklist are in
  `docs/device-contract-guide.md`.
- **The change flow is one-way**: change the table (and Control) here,
  re-record, re-export; device replays fail until they match. Device repos
  never edit a verb or a scenario.
- A device must tolerate: `/$DEV/release` leaving in the same millisecond
  as the fade's last frame, stamped a horizon later (spec D5); and a ~1.5 s
  role-opening signature that ignores light cues (`SIGNATURE_SETTLED_MS`,
  2000, is the scenarios' wait, not that length).

### `harness/`: boot, the stack runner and tooling

`harness/terrarium_boot.py` is Control, `harness/run_stack.py` the stack
supervisor, `harness/o2_shroom.py` the Testshroom and Room simulator; flags,
ports, `runs/` logs and the pty rule are in *Running it*. `print_bit_list`
(`control/bit_registry.py`) serves both `--list-bits`.

#### `terrarium_boot`

- **`build()`** makes the `GameServer`, `Terrarium`, an `AudioBridge` over
  `ArcoSynthPool` and the `DeviceLinkAgent`, loading `--room`'s Room and Bit
  if given (else `NO_ROOM`, pool unstarted). Any `BaseException` (Ctrl-C in
  the up-to-30 s pool connect too) unloads and closes before re-raising.
- **`clock=` is required**: `o2lite.time_get`, for engine, agent and
  `AudioBridge` alike (frames once stamped on `time.monotonic` against the
  devices' O2 clock were never due). `main()` starts the transport after
  `build()`, on pyarco's already-synced connection, `pump=arco.poll`.
- **`_O2SimulatorFactory`** spawns `o2_shroom --no-join` per simulated
  fixture as `sim-room-<fixture>` on `room_stack`: `-u` (else markers
  buffer), `--exit-with-parent`, `--room-type` from `loading_room`. No
  `--sim-host`, so a Room canvas binds `127.0.0.1` only.
- **Loops.** `_wait_in_setup` holds SETUP until `expired`, `parent-gone`,
  `state-changed` (the Console is a second driver; a mid-hold Abort+LoadBit
  shows only as a new `bit_name`), `players-met`, `timeout-start` or
  `timeout-abort` (`admin`: no deadline). `_wait_for_load` (IDLE until a
  load) and `_serve_until_done` share **`_run_tick_loop`**: per tick,
  parent-gone, Room down (`no-room`, before Arco so an unload is not
  `arco-exited`), `arco.poll()`, **`_pump`** (agent, Console, uplink),
  `gs.tick`, the exit predicate. `completed` needs IDLE **and**
  `agent.closing == 0` (release is asynchronous).
- `_serve_rounds` (serve mode: `--serve`, `--no-bit`, or a Console with no
  `--seconds`/`--hold`) loops load, hold on the Bit's `[start]`,
  `request_start(None, TERRARIUM_ADMIN, "timer")` only if still SETUP (never
  `gs.run()` directly), serve, `round ended: <bit> (<reason>)`, never
  touching Arco. `_serve_roomless` wraps it around `_wait_for_room_ready`
  (20 Hz, nothing to drain). `_live_arco` re-reads `terrarium.arco` per tick.
- **`parent_is_gone`** (`harness/signals.py`, re-exported by `o2_shroom`)
  is resolved as a `terrarium_boot` global on every call: tests patch
  `harness.terrarium_boot.parent_is_gone`, so never bind it to a local.
- **Arco clients across Rooms**: `stop_clients` (transport stop, pool
  `quiesce()`) when a Room drops under them; `restart_clients` (pool, then
  transport, via `_restart_room_clients`; `TRANSPORT_READY ... (restarted)`),
  idempotent via `clients_stopped`, from `_RoomWiring`, `_serve_roomless`
  and `_ensure_room_for_bit`. A failed restart unloads, so `ROOM_READY`
  never sits over dead clients; `clients_live` gates Unload.
- **`_RoomWiring`** serves Rooms loaded after `build()`: `ROOM_READY`
  restarts clients **first** (a grant before `ArcoSynthPool.start()`
  raises), then `rewire_room()`; a failed restart leaves `_pending_rewire`
  for `on_clients_restarted` to finish once; `NO_ROOM` calls `unwire_room()`.
- Arco flags, each a measured workaround: `--arco-pty` (curses needs
  `/dev/tty`), `--arco-log`, `--arco-settle-seconds` (the probe sends
  `/host/clear`; a failed probe plus retry can leave `arco.output` `None`),
  `--arco-ready-timeout` (a cold first probe can take ~18 s),
  `--arco-start-audio` (presses (S)tart; off: a toggle Arco cannot report).
  The settle sleep, the readiness probe and `ArcoSynthPool.start()` no
  longer need draining: `_PtyProcess`'s thread reads the master throughout.
- Loggers print device lifecycle and, per Bit load, `JOIN_URL:` (and
  `START_URL:`/`PREPARE_URL:`). `join denied:` stays lowercase, or it would
  match the device marker `JOIN DENIED:` inside Control's log.

#### Boot and teardown order

- **`TeardownStack`** (`control/teardown.py`): registered later, torn down
  earlier, so client-before-hub falls out of start order (three hand-kept
  orders once killed the hub before its clients). Steps are guarded
  (`BaseException`: a second Ctrl-C cannot abandon the rest), `close()` is
  idempotent and returns named failures, a push after close raises. Not
  `ExitStack`, which re-raises only the last failure and names none.
- **`shutdown()` unwinds three stacks**: `pre_room_teardown` (the o2lite
  transport, then `room-audio`, whose `arco.finish()` stops `Ugen.__del__`
  writing a dead socket at exit); `room_stack` via
  `unload_room(force=True)`; the process stack (Console, www server).

<!-- diagram:boot-teardown GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
    ┌────────────────────────────────────┐      
    │1. pre-room stack: o2lite transport │      
    │                                    │      
    └────────────────────────────────────┘      
                   │                            
                   ▼                            
  ┌────────────────────────────────────────────┐
  │2. pre-room stack: synth pool (arco.finish) │
  │                                            │
  └────────────────────────────────────────────┘
                   │                            
                   ▼                            
     ┌──────────────────────────────────┐       
     │3. unload_room: Bit aborted first │       
     │                                  │       
     └──────────────────────────────────┘       
                   │                            
                   ▼                            
     ┌──────────────────────────────────┐       
     │4. room stack: fixture simulators │       
     │                                  │       
     └──────────────────────────────────┘       
                   │                            
                   ▼                            
          ┌────────────────────┐                
          │5. room stack: Arco │                
          │                    │                
          └────────────────────┘                
                   │                            
                   ▼                            
┌──────────────────────────────────────────┐    
│6. NO_ROOM: _RoomWiring unwires the agent │    
│                                          │    
└──────────────────────────────────────────┘    
                   │                            
                   ▼                            
        ┌──────────────────────────┐            
        │7. process stack: console │            
        │                          │            
        └──────────────────────────┘            
```
<!-- /diagram:boot-teardown -->

- A mid-run unload never closes `pre_room_teardown` (`stop_clients` does);
  `room_stack` closes the last-declared fixture's simulator first.
- **`stop_process`** (`control/process.py`): SIGTERM, poll 5 s, SIGKILL,
  poll 5 s, return the code or `None`; polling, not `Popen.wait(timeout=)`,
  because `_PtyProcess.poll()` is the reap path and must stay non-blocking. Used by e.g.
  `ArcoProcess`, `SimulatorProcess` (no readiness probe) and `run_stack`.
- **`sigterm_as_keyboard_interrupt()`**: `finally` never runs on a bare
  SIGTERM, so `run_stack`, `terrarium_boot` and `o2_shroom` map it (and
  SIGHUP, unless already `SIG_IGN` as `nohup` sets) to `KeyboardInterrupt`.

#### `run_stack`

- Spawns `terrarium_boot` and N devices, each pushed on one
  `TeardownStack` at spawn (devices stop first), in its own session, teed
  by `harness/proc_tee.py` to `<log-dir>/<name>.log` and marker-watched.
- Control always gets `--arco-pty`, `--arco-log`, settle 5 s, ready 60 s,
  `--setup-seconds 90` (device cold start ~22 s), `--horizon`, `--hold`,
  `--exit-with-parent`; when serving, `--serve` explicitly (`--hold`
  defeats terrarium_boot's implied rule). Devices (`ie<N>`) get the node,
  horizon, samples, `--exit-with-parent`, `--persist`
  (`--no-persist-shrooms` opts out), and the first `--handshake-devices N`
  get `--handshake`; the rest only hello and end jam.
- Named stages: `room loaded:`, transport, `Holding in SETUP` (fewer under
  `--no-bit`), then per device `clock synced at`; with a start-after-grant
  each handshake device's `HANDSHAKE VALIDATED:` (or an informational `JOIN
  DENIED:`); then, after the start, every device's `ROLE GRANTED:`.
  `_wait_for_marker` checks failure markers every poll. A failure prints
  the stage, every log path and the failing log's tail.
- **`--expect-scored K`** forces the role wait and then fails (stage
  `expect-scored`) unless exactly K devices printed `ROLE GRANTED: ...
  scored` and the rest `... jam`; a run that never starts fails at stage
  `device-join` instead of passing vacuously.
- `--start-after-grant` GETs the last `START_URL` via loopback (the
  Terrarium's admin identity) once the handshake devices are validated or
  denied; implied under `--ci` for an admin-start Bit, which would
  otherwise idle in SETUP and pass. Without it an admin-start Bit skips
  the role wait.
- `_hold` polls every child: an exit is `child-exited`, except a device's
  clean exit under `--serve`, and Control's exit 0 after `Bit completed;
  tearing down` (`bit-completed`, success).
- **CI bound** (no `--seconds`): `max(manifest setup_seconds,
  --setup-seconds) + (expected_run_seconds or 45) + 15`, max since 90 wins.
- `BROWSE_URL:`/`WWW_URL:` are collected and opened under `--open` (Console
  port 0; refused with `--ci`); `ROOM_URL:` canvases are only echoed.
- **`harness/markers.py`**: the stdout contract (`tests/test_markers.py`);
  `_watch_list` derives from `READY_MARKERS`/`FAILURE_MARKERS`/
  `INFO_MARKERS`, a failure marker with no `_FAILURE_REMEDIES` entry
  raises. `HANDSHAKE_VALIDATED` and `ROLE_GRANTED` are ready markers;
  `DEVICE_JOIN_DENIED` is informational, never fatal. URL markers never
  block.
- **The orphan chain.** An orphaned `sim-room-*` re-claims its name on the
  next Arco and O2 silently refuses the new one (*Service refusal*), so
  each link watches its parent pid: `o2_shroom --exit-with-parent`
  (clock-sync wait, tick loop, `--identify-blocks`), `terrarium_boot
  --exit-with-parent` (in its own session, so a SIGKILLed `run_stack`
  cannot signal it; exits via `shutdown()`), and `run_stack`'s watch
  (`run()` records `getppid()`, `_hold` polls it, stage `parent-gone`;
  `--detach` opts out). `parent_is_gone` compares that recorded pid: the
  parent may be dead before the child reads its argv.

#### The Testshroom (`o2_shroom`) and WebSim input

- **Two roles**: a player (`ShroomClient`, 36 ch, `--instrument testshroom`)
  or, `--no-join`, the Room simulator (hello only; `--room-type`/`--fixture`),
  titled by its `dev`, printing `BROWSE_URL:` or `ROOM_URL:`.
- It syncs its clock (parent-watched), then `service_conflict` checks
  ownership: a loss prints `FATAL: service`, exit 1. The heartbeat hello
  is identical to the first. `--handshake` answers each `/<dev>/handshake`
  once per round id with `/game/handshake dev round_id node`
  (`send_handshake_ack`, TCP), after `--handshake-delay SECONDS` (default
  0); without it the device only hellos and ends jam. There is no
  `--join-retry` and no `/game/join`.
- **Gestures wait for the role** (`_gestures_ready`): gestures go UDP and
  can overtake the `/role`; earlier ones are discarded, counted. The tilt
  sweep (8 s triangle) runs only if `uses` allows `tilt`; `BeatTapper`
  arms on `tap`. `--persist`: release means `reset_for_lobby()` and loop.
  A deny never ends a round (only a release does); its `JOIN DENIED:` line
  is informational (`INFO_MARKERS`).
- **ABORT resilience** (ABORT stops the hub): hello/handshake sends swallow
  `AssertionError`/`OSError`; `reconnect_recheck` idles on a lost bridge id
  (`HUB_AWAY_NOTE`), re-verifies a new one (10 s, resend 2 s, retry if the
  send fails, exit 1 on a conflict); the loop idles on `time_get() < 0`.
- **WebSim input**
  ([design](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-20-websim-two-way-input-design.md)):
  luxaeterna's page sends JSON to `on_input` (bad or raising: dropped).
  Pointerup taps; a horizontal drag over 5 px tilts instead (gamma +/-90,
  at most every 50 ms); a 400 ms press holds; arrow keys swing 2 g.
- A 64-deep drop-oldest queue, **stamped `o2lite.time_get()` at enqueue**
  on the websocket thread (drain-time stamps cost up to ~23 ms; `time_get`
  is a pure read). `drain_gestures` sends `/game/tap sffi`, `/game/tilt
  sf`, and `/game/hold sfi` (touch-down stamp) or `/game/swing sfi` only if
  `uses` names the verb, stricter than `wants_verb` as no pre-Rev 1 Bit
  handles either (else hold is a tap, swing dropped). A drag pauses the
  sweep `SWEEP_RESUME_SECONDS` (5) while it keeps schedule: no burst.
- **`/<dev>/play`** plays `harness/sim_audio.py`'s in-memory sine WAVs via
  `afplay`, fire-and-forget (else `play: <name>`); a `chime` with
  `key=<midi>` (the validation ceremony) plays `KeyedChimePlayer`'s
  fundamental-plus-fifth at that key. A process per play: never on a device.
- `harness/websim_leds.py` feeds the canvas and `BeatTapper`; `ShroomClient`
  drops a wrong-width frame, never truncating. `harness/device_bridge.py`'s
  `DeviceBridge` is Control's per-device session (release: `clear()`).

#### Tick pacing

- **`TickPacer`** (`harness/tick_pacer.py`,
  [spec](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-25-tick-pacing-design.md))
  sleeps to an absolute deadline and advances one period, so oversleep is
  repaid and the mean holds; over a period late, it resyncs, never bursts.
- On the dev Mac `sleep(1/44)` took ~26.8 ms, so the tick ran ~37 Hz; paced,
  43.4-44.0 Hz. Art-Net then gets ~38-39 fps, 0 gaps: the content's change
  rate (`_render_room` skips a byte-identical frame).
- `_wait_in_setup` and `_run_tick_loop` build it on their `sleep` but its
  own `time.monotonic`, not `clock=` (tests script that clock with fixed
  iterators); `gs.tick` keeps `1/44`. `render_bench.measure()` passes its own
  `clock=` and drives `_loop_once()`, so never times luxaeterna's own loop.
- **Jitter is not fixed**: ~4 ms per macOS sleep fails `render_bench`'s p95
  <= 25 ms (27.07 ms), and the Dec 4 Terrarium is a Mac (sleep-then-spin is
  the follow-up). luxaeterna#23 paced its own output loops. Dev-box figures.

#### Benches and venue tools

- `harness/render_bench.py`: mean, min, p95, worst (a stall hides in a
  mean); pass is mean >= 43, p95 <= 25 ms, worst <= 50 ms; venue box only.
- `harness/array_smoke.py`: 864 RGBW px, 7 Art-Net universes; 21.6 A at
  full white on a 12.5 A supply, so `TERRARIUM_MAX_AMPS` (10) is forced.
- `harness/artnet_listen.py`: strict fake WLED (fps, gaps, `--websim`).
- `harness/sync_bench.py`: p99 and worst of absolute deltas (*Timed cues
  and `cue_horizon`*).
- `tools/trace_stats.py`: gesture features per trace and label over a
  capture directory (`--csv`); thresholds are a ladder, not truth.
- `harness/local_sample.py`: `last_latency_ms` is dispatch, not sound.

### `console/`: the Terrarium Console

A Bit-agnostic local admin panel, the inbound sibling of `uplink/` ([design](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-21-terrarium-console-design.md)).
Off unless `--console-port` (*Running it*). **Trusted LAN, no auth**, bound to
`127.0.0.1` (`terrarium_boot --host 0.0.0.0` opts in); facing an untrusted
network makes auth a prerequisite.

#### Server, protocol and agent

- **`ConsoleServer`** (`console/server.py`, the only socket code): one port,
  `GET /` and assets over HTTP (`process_request`), websocket only at `/ws`.
  Handler threads touch only locked queues; `GameServer` is touched on the
  tick thread (`_pump` calls `ConsoleAgent.poll()`). A failed send drops the
  client. Assets: `console/static/` files with an allowlisted extension
  (`.html`, `.css`, `.js`, `.ttf`, readable in one line for an
  unauthenticated server), read once, **keyed by basename**: a request path
  is cut to its last segment, so traversal 404s and no two files may share
  a basename (`fonts/` included).
- **`console/protocol.py`**: pure builders re-exporting `uplink.protocol`'s
  `parse_command` and shared events. Shared commands: `load_bit` (`name`,
  `overrides`, `room`), `run`, `abort`, `restart`, `list_bits`, `load_room`,
  `unload_room`. **Admin commands** (`parse_admin_command`, never from the
  uplink): `arm_room`, `release_room`, `fire_function`, `get_design`/
  `save_design`/`publish_design`/`clone_design` (with `kind` `instrument`
  or `room`), `bench_start`/`stop`/`fire`/`lane`, `list_captures`,
  `capture_stats`, `replay_trace`. Refusals go to the requester only.
- A client gets `snapshot`, then `bits_listed`; the rest is change-driven
  (`roles_changed` on LOADED and IDLE, so a tab opened before a Bit loaded
  still classes scored/jam), including the bench's five:
  `bench_started`, `bench_frame`, `captures_listed`, `capture_stats`,
  `replay_result`.
- **`ConsoleAgent`** (`console/agent.py`) observes `GameServer` and
  `Terrarium`. Its kwargs (`registry`, `terrarium`, `room_controllers`,
  `canvas_urls`, `catalog_root`, `rooms_root`, `bench_session_factory`,
  `captures_root`, `join_info`, client hooks) are optional: `None` yields
  an `error_event` or empty view. `terrarium_boot` wires all
  (`captures_root=Path("captures")`, cwd-relative).
- Room frames go out at ~10 Hz (`ROOM_FRAME_INTERVAL`), latest per fixture,
  dropped, never queued (boundary rule 2); bench frames too
  (`BENCH_FRAME_INTERVAL`, 0.1 s). Other views rebroadcast only on change.
- The `room` payload (*Shown by addition*, *Rooms and fixtures*): `fixtures[]`
  (`dev`, `color_order`, `muted`, `artnet`), `controllers` (flat, first
  fixture wins), `fixture_controllers` (no Console module reads it).
  `surface_instruments` maps `@fixture:` tokens, bound devs (not a covered
  fixture's stale one) and devices; `builtins` add carried instruments.
- `run` is `request_start(None, TERRARIUM_ADMIN, "console")`; `restart` is
  `abort()` plus `load_bit` of the same name and config (ABORT, Unload,
  `room_load_failed` and the other-Room refusal: *Terrarium lifecycle*). A
  failed load's `NO_ROOM` looks like an unload's, so `_load_room`
  broadcasts `room_load_failed` itself.
- The Console's `bit_completed` goes out at UNLOADING when `result()` is
  non-`None`, aborts included; the uplink sends its own at COMPLETING only
  (*`uplink/`*). `log` carries warnings, start/prepare verdicts, lobby
  events, `round ended: <bit> (<reason>)`.
- **Design commands** load via `_load_design_catalog`: an unparseable
  published entry fails a whole catalog, so it becomes one unclickable
  `catalog_error_row`, never a raise out of `poll()`. A mutation replies
  `designs_changed` and broadcasts it (the sender gets two).
- **Bench**: one `DesignBench` (*Design bench and gesture eval*) over
  `harness/design_session.py`'s `LuxBenchSession`, which renders into a
  default 512-channel `Universe` and returns the capability's slice; closed
  when the last client leaves. `bench_start`/`replay_trace` are
  instrument-only. `capture_stats`/`replay_trace` check session and label
  against `CATALOG_NAME_RE` (path components) and name an unreadable trace.

#### Front end (`console/static/`)

- `index.html` loads `shell.js` (inits every panel; `VIEWS` Live, Room,
  Design). `wire.js` is the only WebSocket (`on`, `send`, `flashRefusal`,
  `connect`, `confirmTap`, `reserveConfirmWidth`); `dom.js` has the shared
  `mk`/`clear`. `rail.js`: the rollup (Fixtures, Scored, Shared if
  declared, Jam, Devices) and the event log.
- **Bit panel** (`bit.js`): Run/Restart/Abort need `ROOM_READY`, Load a
  settled Terrarium. The picker omits disabled Bits, dims `[console]
  hidden` ones, takes `table.key` overrides and a loadable Room, disabling
  any but the active one (*Terrarium lifecycle*).
- **Live view**: the Room card (`surface.js`: dot rows per block, zone bar,
  Release/Arm, an `Art-Net` chip (no Arm) when covered, `Muted`, canvas
  links `http(s)` only), Triggers (`functions.js`), Live values, status,
  Join, log. `_decodePixels` decodes by `color_order`, W added onto RGB;
  `design.js`'s bench paints GRB on purpose (a Shroom capability).
- **Pickers**: All, each fixture as `@fixture:<name>` (`(unbound)`,
  `(muted)`), then unbound devices; DEVICE pickers never offer a fixture
  ("no device joined" disables Fire). A refill maps a device that just bound
  to its fixture row, not to All (one Stop would mute everything).
  `functions.js` holds each row's picker by reference
  (`currentDeviceTargets`), so a refill mutates the node the row owns. A
  failed optimistic Arm rolls back (`pendingArm`).
- **Join card** (`join.js`, from `control/join_info.py`, hidden when `null`):
  guest URL, then per node a segno QR, URL and Tuneshroom command. With a
  `[start] key`, a **Start (admin)** row adds the start URL and QR, the key,
  the `/game/start "ss" <dev> <key>` line and the Prepare URL.
- `rooms.js`: a card per Room (Unload shows `unload_blocked`). `busy.js`
  overlays `ROOM_LOADING`/`ROOM_UNLOADING` from any tab (a failure stays up
  with Dismiss) and shares `#overlayMount` with `bit.js`'s `closeOverlay()`:
  safe only because `shell.js` inits `bit.js` first.
- **Design view** (`design.js`): instrument and Room rows share one wire,
  split by `kind`. Save on a published name writes its draft; Publish;
  Clone. Bench and Calibrate are instrument-only. **`applyProposal`**
  writes proposed thresholds into the textarea under `# calibrated from
  <session> on <date>`, only on a draft; the operator reviews before Save.
- **`design_forms.js`** is a second view over `#designText`, the single
  source of truth: controls re-parse it, edits run `applyEdit(fn, opts)`,
  raw typing rebuilds after 300 ms (`guard` stops the echo). Rebuilds
  recreate controls; a `data-form-key` restores focus and caret, but
  **destructive edits pass `{restoreFocus: false}`** (rows shift, so the key
  names the next row). A Room design gets Fixtures: reorder, instrument pick.
- **`toml_edit.js` is line-based and comment-preserving, not a serializer**
  (a round trip would drop `# calibrated from` provenance): `splitBlocks`,
  `getScalar`/`setScalar`, thresholds, script steps, ambient rows (matched
  by header suffix), `listFixtures`, `moveFixture`, `setFixtureInstrument`;
  `fixtureBlocks` moves unindented `[[fixtures.*]]` children with their
  fixture. A missing target is a no-op. Raw TOML only: a new function, a
  generator's lane, a new ambient block or fixture.

#### Rendering discipline and front-end gotchas

- **No frequent event may rebuild a subtree whose declaration did not
  change**: `confirmTap` keeps its arm state on the button node. Panels
  gate on signatures: `bit.js` (loaded Bit identity), `rooms.js`,
  `functions.js` (pickers refill only on a fixture `(name, dev, muted)`
  change: a refill closes an open `<select>`), `surface.js`
  (`fixtureShapeMatches`, `bindStateKey`; Muted sits outside it).
- `confirmTap` arms 4 s, flashing "not confirmed" on expiry; call
  `reserveConfirmWidth` at creation, or arming reflows the row and the
  second click misses. **An author `display` rule beats `hidden`**: keep
  `terrarium.css`'s `[hidden] { display: none !important; }`.
- One throwing `wire.on` listener stops the rest for that event, so
  `functions.js` returns early with no `#functionsMount` (`NO_ROOM`) and
  clears `fnSignature`. `tests/js/_dom_stub.js` auto-vivifies ids and reads
  `hidden` as a property, so neither case shows offline.

### `uplink/`: outbound remote control

`UplinkAgent` (`uplink/link.py`) drives `GameServer` over an outbound
websocket to a future mm-fairyring broker; nothing depends on it being up,
and only lifecycle and registration counts cross it ([design](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md),
[MycoQuest handoff](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-13-mycoquest-handoff-terrarium-design.md)).

- Built only with an `[uplink]` table: `tenant_slug`, `secret` (64
  lowercase hex), `url` (empty: log-only, non-durable `LogTransport`). A
  connect sends the identity frame (`event: "identity"`, `tenant_slug`,
  `terrarium_name`, `secret`), the resync (`state_changed` with `lan_ip`,
  `room_loaded`, `registration_changed`), then the journal. Backoff doubles
  1 s to 30 s; a send failing just after connect is a failed attempt.
- `load_bit` needs `ROOM_READY` and ignores `room` (unlike the Console);
  `run` is `request_start` as the Terrarium; `abort` is Bit-only; `restart`
  is the soft cycle. `unload_room` gets the Console's live-clients refusal
  (the shared `unload_room_refusal`; `terrarium_boot` passes both agents the
  same `clients_live`), so a broker unload with live Arco clients is an
  `error` event and the Room stays up; before 2026-09-28 it went straight to
  `terrarium.unload_room` and stranded the process until restart
  (`load_room` is still refused outside `NO_ROOM` by the `Terrarium`).
  Errors become `error` events.
- **`bit_completed` fires at COMPLETING only, never on `abort()`**: an
  aborted round credits nobody. It carries `bit {name, version}`,
  `room_name`, `terrarium_config_version`, `result` (`null` if absent or
  raising) and `players [{dev, role, class}]` (`jam`/`scored`, the reserved
  `terrarium` id filtered as a second guard).
- **A replay journal, not a buffer**: each `bit_completed` is appended to
  `<runs_dir>/uplink_journal.jsonl` before any send (cap 500, newest kept);
  after the resync, over a durable transport only, it replays in order and
  clears, and a mid-replay failure keeps it. `--no-run-records`: no journal.
- **Deviations from MycoQuest spec 7.2** (its section 8; mm-renquest
  mirrors them): no abort credit; `result` may be `null`; the identity
  `event` field; `GET /prepare` may answer 503 (all but 202/409 mean
  "cannot reach the room"); an unknown Bit is a silent 202; `lan_ip`.

### `capture/`: labelled sensor telemetry

Records a phone's accelerometer, gyroscope and mic in a labelled gesture,
so thresholds come from data ([design](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-07-sensor-telemetry-capture-design.md)).

- Wire (*Message vocabulary*): `/game/capture "ssb"` (`open` declares the
  label and device `t0` first; `close`; `abandon`) and `/game/telemetry
  "sfb"` (~100 ms structure-of-arrays batches, optional 16 kHz PCM); a
  `label` must match `[A-Za-z0-9_-]+`, as it becomes a path.
- `capture/trace.py`'s pure `Trace` keeps a skipped `seq` as a gap and
  refuses a stale or duplicate one. `capture/store.py`, the only file I/O,
  writes once at close, expiry or truncation (never per batch):
  `<session>/<label>/<series>.json`, a `.wav` sidecar, `index.jsonl`. An
  open label/series is refused, no file overwritten, a failed write only
  bumps `failures`; a capture past `window_ms` plus 5 s is truncated.
- `docs/telemetry-trace-schema.md` is the cross-repo contract;
  `tools/trace_stats.py` the offline CLI. No capture client exists yet.

### `bits/`: the in-repo Bits

`--list-bits` marks `enabled = false` packages `DISABLED` (kept out of the
Console and launchers). **`__init__` must take `config` first**: `load_bit`
calls `bit_cls(config)`, so an earlier parameter silently gets the
`BitConfig` (it once crashed a stack; `tests/test_capture_bit.py` pins it).

- **TestBit** (`bits/test/`, TEST/DEMO/VENUE, `[console] hidden`): the
  regression fixture, done after 2 s. A scored `unique` capacity-1
  `player` (`TEST_PLAYER_NODE`, slot `light.pixels` + `gesture.tilt`), so
  the second handshake is denied `scored full`, and the unscored jam
  `jammer` (`TEST_JAM_NODE`) that every other device gets at start. The player is
  `aurora` (`cc:74` hue, `cc:11` level, so the breath), no note lane, with
  welcome `glow` (`bloom` strobed and rendered a dark welcome); the jammer
  glows dim green on its own `cc:1`/`cc:2` and has no `ugen_manifest`,
  exercising the no-audio path. The Room `rainbow` moves with
  the `drift` generator and `tilt_hue`; three full tilts fire `play_aurora`.
- **SoloTestBit** (`bits/solotest/`, TEST, `[console] hidden`, kind
  `tool`): the solo-fallback fixture. One scored `unique` capacity-1
  `player` (`SOLOTEST_PLAYER_NODE`) and **no** unscored role, so every
  other device gets `solo:<instrument>`; starts `immediate`, completes
  after 20 s.
- **ChaseBit** (`bits/chase/`): TestBit plus `chase`, stepping
  `@fixture:main` then `@fixture:accent`, so TEST only.
- **CaptureBit** (`bits/capture/`): a tool Bit, unscored `recorder` on
  `CAPTURE_NODE` (everyone's jam role at start), no light or audio, never
  completing; a capture silent 10 s
  expires, unload truncates the rest; `status()` makes the Console a live
  capture dashboard.
- **MetronomeBit** (`bits/metronome/`, DEMO/VENUE, [design](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-20-metronome-bit-design.md),
  [on o2lite](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-08-metronome-bit-on-o2lite-design.md)):
  8-beat call and response x4 over up to 2 `UNIQUE` players in turn; a
  phrase needs every answer beat tapped within 50 ms and none off-grid (success:
  fireworks; fail: red and bass; any success: a 10 s finale). `[rhythm]`
  sets BPM (100; `profiles/dev-metronome.toml` 80), window, offset, and
  `beats_per_cycle` (8), split evenly into call then answer beats
  (`CALL_BEATS = beats_per_cycle // 2`); grading, clicks and the judgment
  deadline all follow it (hard-coded 8 until 2026-09-28). Each
  consequence is a Function fired at its beat's `at`, audio Room-side only;
  the grid starts at the first `fires(at)` plus `LEAD_IN_S`, clearing the
  1.5 s role-opening signature.
  - **Taps grade at `at - cue_horizon - INPUT_OFFSET_S`**: the horizon
    cancels for the Bit's cues, not for input (else a perfect tap is
    +60 ms); so does the judgment deadline. `status()` shows the last 8.
  - `player` sets `breath=False` (the breath overwrote the cc:11 pulse);
    `metro_recovery` fires only for a failed dev, before the pulse.
    `BeatTapper` locks on shown pulses; a device that misses beat 0 is dephased.
- **Rev1Bit** (`bits/rev1/`, TEST/DEMO): the Rev 1 board bench check,
  never completing. `REV1_PLAYER_NODE` gates on the five Rev 1 capabilities
  (spelled here: venue code never imports `contract_kit/`;
  `tests/test_rev1_bit.py` pins them); `REV1_SIM_NODE` (default) needs
  pixels and tap. Both are unscored, so at start each device gets the
  first its carried instrument satisfies (a Rev 1 board `player`, else
  `sim`). Tap: `tick`, hue step; hold: `hold`, white 1 s; swing:
  red (negative g) or blue 0.5 s; each a DEVICE trigger the Console can fire.

- **MinigameBit** (`bits/minigame/`, TEST, `MINIGAME_PLAYER_NODE`): a hold
  starts 10 white `blink`s 2 s apart, a tap resets. Its `player` is
  unscored `unique` capacity 1, so the first pooled device gets it at start
  (before `on_run_start`, which keeps it) and the rest get solo. Hold needs a Rev 1 board or the sim's long press.


### `www/` and `arcoserver/`

- Arco runs with `arcoserver/` as cwd (prefs come from the cwd; no
  `http_enable` key); `arcoserver/arco_server_prefs.json` sets `http_root`
  `"www"`, port 8080. `arcoserver/www` symlinks `../www` because O2's HTTP
  server rejects any path containing `..` (`o2/src/websock.cpp:905`), root
  included. `o2debug.log` lands there. `www/` also holds `index.htm` (not
  `index.html`), `o2wsclocksync.htm` and a gitignored `app/` guest build.
- **`www/o2ws.js` carries three patches**, each marked `// mm-terrarium
  patch` and listed in `www/README.md` to re-apply on refresh: the delay is
  scaled to ms before rounding (upstream sent anything under 500 ms at
  once); a deferred handler snapshots `o2ws_message_fields` before
  `setTimeout` (the getters shift one global every message reassigns);
  `onerror` calls `o2ws_on_error`, not the undefined `o2ws_error`. Only the
  first is reported upstream; mm-tuneshroom's `web/o2ws.js` must stay
  byte-identical below its header.

## Boundary rules (the load-bearing invariants)

Code, tests and other repos cite these by number, so a new rule is
appended, never inserted.

1. **Single writer to `/arco`.** Only Control builds ugen graphs and owns the
   ugen id space. Interactive Elements express intent to `/game`; Control
   decides the audio consequence. A device never touches `/arco`.
2. **Uplink and console are monitor/control shells, never the hot loop.**
   Both attach through the engine's observer list and are pumped from the
   engine's tick loop, but an observer exception is logged, never raised
   into the tick (`GameServer._notify`), and neither carries per-device
   `handshake`/`tick` traffic. Gameplay never depends on either link's health.
3. **Lux Aeterna is the lighting renderer, downstream of Bit cue logic.**
   [Lux Aeterna](https://github.com/Musical-Mycology/luxaeterna) is MM's
   Python DMX512 / Art-Net to WLED library, the visual analog of Arco,
   rendering at 44 Hz (MM-internal hardware and installation design docs).
   The Console **monitors,
   never drives**, as with Arco: it displays each role's `light_manifest`,
   never instantiates ugens and never pushes frames into the Room's render
   loop (the Design bench renders its own offline session). A Lux Aeterna
   health read-out through `Bit.status()` is anticipated, not built.
4. **An in-process consumer is reached by a Python method call, not by O2.**
   o2lite `send()` has **no local short-circuit**: addressing a service from
   the process that offers it round-trips through Arco. So Control drives
   its own light sessions and Room voices by direct calls
   (`session.feed_midi(...)`, `.swap(...)`, zero hops), and `game` and
   `actl` stay **inbound-only** (devices to `game`, Arco to `actl`): Control
   never messages itself (design doc, *Message Routing*). **One deliberate
   exception:** `verify_service_ownership` (`devicelink/o2_transport.py`)
   sends `game`, then `actl`, one self-addressed message at startup, before
   the tick loop, using that very property as a measurement: it comes back
   only if the hub routes the service here (*Service refusal*).
5. **A test double must never be more permissive than the library it stands
   for.** A `FakeO2Lite` once called handlers directly while real o2litepy
   dispatches only inside `poll()`, so the suite and every review agreed a
   transport worked that had never delivered a message. Encode a double's
   *strictness* as well as its shape: what the real thing refuses, when it
   dispatches, and what it requires you to call.

## Host platform (gotcha)

- **The Dec 4 Terrarium is a Mac** (its line-out feeds the PA) and the
  Instruments are ESP32s on o2lite firmware (student hardware track spec,
  section 4.5). The later venue target, bare-metal Linux on a Raspberry Pi 5
  with an I2S DAC HAT, is deferred past the show (design doc, *Host
  Platform*).
- **No NAT'd hosts for real devices.** O2 discovery and Art-Net to WLED are
  UDP on the LAN; a NAT'd VM or **WSL2 in its default NAT mode** sits on its
  own subnet and gets neither. WSL2 is still the default *dev* host
  (simulated devices need no LAN); mirrored (Windows 11) or bridged
  (Windows 10 Pro) networking may lift the restriction but is unverified
  with a real dev shroom (*Linux / WSL host setup*, step 9). The show
  machine stays a Mac.
- **Develop without hardware** on luxaeterna's `WebSimBackend` (browser
  canvas; `serve=False` records frames headless); `harness/o2_shroom.py`'s
  `build()` is the worked example.
- **Measure timing on the show machine**, which relays every hop through the
  process doing all synthesis while feeding the 44 Hz render loop. The
  M1a-era "round trip under 50 ms" had Control out of the path. Use
  `harness/render_bench.py` (*Benches and venue tools*; the `_loop_once()`
  rationale is in *Tick pacing*). No show-machine figures are recorded yet.
- **Pace to deadlines, never sleep after the work**: macOS oversleeps. New
  fixed-rate loops use `TickPacer`; figures and the open jitter in *Tick
  pacing*.
- **Arco needs a controlling terminal.** Curses opens `/dev/tty`, so a plain
  `Popen` on a pipe fails (then reads as a readiness timeout); `script` does
  not help. `pty_popen` (`control/arco_process.py`, `--arco-pty`) forks onto
  a pty the child adopts. Two silent failures: `TERM` must be set, and the
  pty needs a non-zero size (`TIOCSWINSZ`). An exec failure raises
  `ArcoExecFailed` via a close-on-exec pipe (macOS reports EIO on the pty
  master once the slave closes, so pty text never reaches the log);
  `wait_ready` raises `ArcoExited` once the child dies. The readiness probe passes
  `wait_ready`'s remaining time to pyarco's connect phase (its 15 s reset wait is
  hard-coded, so overrun is bounded at ~15 s), reports ready only once the reset
  completed, and re-issues the reset on a connected-but-unreset Arco; the
  timeout message names the stage (never connected / reset never completed). The pty master is
  also Arco's only control surface (`_PtyProcess.write_console`). `_PtyProcess`'s
  own thread drains it continuously (*Running it*): the pty holds ~19.6 KB
  and Arco writes ~40 KB on WSL at startup.

## Relationships to other repos

- **arco / o2** (rbdannenberg upstream, Musical-Mycology forks): the engine
  and transport; the Arco server *is* the room's O2 hub and sole
  synthesizer. Two defects went to Roger Dannenberg
  ([report](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/upstream/2026-08-14-o2-service-and-discovery-report.md)):
  the silent service refusal is O2 as designed (*Service refusal*), and
  o2litepy's missing ensemble filter is fixed upstream (`rbdannenberg/arco`
  `379424e`; canonically `rbdannenberg/o2` `f21499e`), confirmed live.
- **pyarco**: the Python layer Control builds ugen graphs through; dev/test
  only, reached by `PYTHONPATH`, never vendored, never imported by
  `control/audio.py`, so the suite runs offline. Source of truth: the
  sibling `arco` checkout's `pyarco/` (Roger's `rbdannenberg/arco`, mirrored
  to `Musical-Mycology/arco`), found by `harness/arco_paths.py` (*Running
  it*). The standalone `Musical-Mycology/pyarco` repo is archived.
- **o2litepy** (`arco/o2litepy/`): what pyarco and `O2LiteTransport` ride.
  Nothing under `control/` or `devicelink/` imports it at module level (the
  caller injects a connected object). `harness/arco_paths.ensure_o2litepy()`
  is the one fallback, called by `run_stack`, `terrarium_boot` and
  `o2_shroom`: if o2litepy is not importable it appends `ARCO_PYTHONPATH` to
  `sys.path` and the children's `PYTHONPATH` (an explicit `PYTHONPATH`
  wins). The canonical, pip-installable package is `rbdannenberg/o2`'s
  `o2litepy/`; if Roger removes the identical arco copy, `ARCO_PYTHONPATH`
  and every `PYTHONPATH=` recipe must repoint. Phase 3 of the connectivity
  migration spec pins the package and retires the fallback.
- **mm-tuneshroom**: the instrument app and browser simulator. Its web build
  deploys into the Terrarium's `www/` as an artifact; the *application*
  (Dart app, web build, native harness) never contains Terrarium-side logic.
  Its `bits/` holds Terrarium-side Bit packages that ship with the
  instrument they target (its GlowBit is the reference), consumed only through
  `bit_paths`, never imported by the app. Shared contracts, synced by hand:
  `devicelink/protocol.py` with `lib/link/envelope.dart`, the exported
  device contract, `www/o2ws.js` with its `web/o2ws.js`, and
  `docs/telemetry-trace-schema.md` (its capture client, derived thresholds
  and simulator presets are unbuilt). The legacy M1a / Sensor-Check harness
  stays there as a reference; nothing was ported. Its solo mode is the
  light lexicon's Solo signal (`docs/light-lexicon.md`).
- **mm-devshroom**: Rev 1 ESP32 Tuneshroom firmware (Victor's), consuming
  the exported device contract. What a v3 device must do is the firmware
  checklist in `docs/device-contract-guide.md` section 5.3 (spec section
  8): hello on link-up and every 5 s, answer `/<dev>/handshake` on the
  accept gesture, send gestures only after a received `/role`, never send
  `/game/join`. Its `origin/main` firmware still sends `/game/join` once
  per link, with no resend; until it adopts the handshake it gets the
  retirement `/error`, still hellos, and gets a jam role at every start.
  Before Control talks to it, the firmware draws its own light: the light
  lexicon's Solo (aurora) and Looking (slow white pulse) signals, neither
  built yet (`docs/light-lexicon.md` gap G4).
- **mm-fairyring**: the cloud broker, Terrarium `uplink/` to fairyring to
  MycoQuest. `uplink/` is written against a protocol fairyring implements;
  the broker is built in its own repo, not yet deployed. Its cross-repo
  follow-ups doc lists the Terrarium-side items (*Not yet built*).
- **mm-renquest (MycoQuest)**: `GET /prepare` and the uplink events, per the
  [MycoQuest handoff spec](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-13-mycoquest-handoff-terrarium-design.md);
  the deviations are under `uplink/`.
- **Lux Aeterna** (boundary rule 3): role blobs carry `light_manifest` in
  its v2 shape (`LightManifest.from_dict`;
  `docs/superpowers/specs/2026-07-22-light-manifest-v2-adoption-design.md`).
  This repo relies on: the WebSim canvas fitting a LINEAR surface of any
  length in one row, and replaying its last frame to a late client (so a
  one-shot `--identify-blocks` shows); `SurfaceCapability` refusing zones
  that under-cover `pixel_count` at construction (`harness/room_surface.py`
  conforms); and `LightSession.render_into` passing the injected clock's
  reading as `t`, so fixture sessions sharing `DeviceLinkAgent`'s clock
  scroll a `primary` rainbow as one gradient. Its `sys:*` status
  signatures (`synth/status.py`: `sys:loaded` on a role grant,
  `sys:closing` on release, `sys:error` on a manifest that fails to
  resolve) are part of the light lexicon, and their patterns are reserved.

## Not yet built / deferred

Kept explicit so the doc does not over-claim.

- **Native iOS/Android and Radxa apps cannot connect** until mm-tuneshroom's
  FFI o2lite link lands (connectivity migration Phase 3, which also pins
  o2litepy; `control/join_info.py`'s `NATIVE_NOTE`). o2ws timing on a real,
  OS-focused phone on the venue LAN is unmeasured, and two of the three
  `www/o2ws.js` patches are not reported upstream.
- **The Tuneshroom LED wire cannot reach the white die.** The Rev 1 board's
  12 pixels are RGBW, but `protocol.leds_event` ships 36 ints (12 px GRB)
  and luxaeterna's `shroom_capability` says `color_order: "GRB"`, so a
  driver must hardcode `w=0`. Widening to 48 means changing
  `devicelink/protocol.py`, `shroom_capability`, the WebSim backend,
  mm-tuneshroom's `lib/link/envelope.dart` and the contract together: **an
  open decision, not a bug to quietly fix.**
- **Contract kit:** mm-devshroom's native replay environment, the live bench
  replay spike (section 8 of the device contract kit spec, *The device
  contract and `contract_kit/`*), and an executable Mushica capability-gate
  test (waits on the Mushica Bit).
- **Device clients on contract v3**: mm-tuneshroom is on v3 (its PR #35,
  merged 2026-10-01: device session `acceptHandshake`, simulator Accept
  control, v3 replay); mm-devshroom's firmware still joins (*Relationships
  to other repos*), so until mm-devshroom#7 lands a Rev 1 board is
  jam-only.
- **The light lexicon's G4** ([`docs/light-lexicon.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/light-lexicon.md)
  section 6): Rev 1 firmware does not yet render Solo or the white Looking
  pulse, and link-loss fallback to Looking is undecided.
- **Room liveness is undesigned**: Room-bound devices are never reaped
  (liveness spec section 5).
- **A device's clock-sync to Arco after Control has connected is unreliable**
  in one remaining, upstream case. The intermittent half was this repo's
  undrained Arco pty (*Running it*), fixed by the drain thread, and never headless-specific.
  What remains: pyarco's `arco.initialize()` always sends `/host/clear`
  via `reset()`, and a client that synced **before** that keeps a valid
  `time_get()` on a dead socket (measured: 120 joins over 240 s, none
  received). `verify_service_ownership` makes this loud
  (`FATAL: service ... is not routed back to this process`), not fixed.
  Arco's `(S)tart` key restores sync (`--arco-start-audio`, off by
  default: the toggle cannot be read, so it can stop running audio); same
  family as "only the first client after an Arco start gets working audio"
  (that `/host/clear` also tears down Arco's audio stream on macOS, so the
  re-open fails with PortAudio `-9988, Invalid stream pointer`; restarting
  the Arco server before each run is the workaround, upstream in Arco).
  On a failed live run read `o2debug.log`: `dropping message because service
  was not found` means Control was not up yet (or a device's announcement
  was lost; devices re-verify on reconnect); silence means a dead socket.
- **Is the device-side `TimedQueue` redundant on o2lite?** O2 already
  delivers at `when`, so "one gesture, one shared `T`" may be enforced a
  layer lower. Unanswered; evidence in
  [*Timed cues and `cue_horizon`*](#timed-cues-and-cue_horizon).
- **Service refusal stays silent at the O2 layer**, a design constraint
  here, detected by the probe and orphan guards, never fixed
  ([*Service refusal*](#service-refusal)). Two Terrariums (two real Arco
  hubs) on one network are not exercised against the ensemble fix.
- **Arco synthesis** is provisional (one `Flsyn`, `ugen_manifest` v0;
  *Audio*): no per-role synthesis beyond FluidSynth, no real
  Flsyn-parameterizing schema, and no Arco audio to devices (they play local
  samples on `play` cues).
- **Scoring**: no framework; `Bit.on_complete()` is an empty hook and a Bit
  reports only through `result()`. **A Bit is not told a player left**: a
  ROOM role switch releases the player role, but `control/bit.py` has no
  leave hook.
- **No physical LED has been driven.** `[[artnet]]` wires DEMO and VENUE for
  real, tested only against `harness/artnet_listen.py` and loopback; the
  Art-Net spec's section 9 bring-up checklist is pending. Treat venue-array
  and device tooling as unexercised until the student track's hardware
  gates record otherwise. TEST's only backend is the browser simulator.
- **VENUE hardware inputs** (venue Room spec section 2): LEDs per fiber
  engine (N = 1 in `rooms/VENUE.toml` until supplied), fiber current, the
  PSU rating, and whether the fiber shares it. A per-bundle fiber ambient
  (one `aurora` per zone) is a follow-up.
- **`RoomBindingRegistry` save/load does not persist in live runs**: no
  `binding_store_path` is passed (*Room binding*).
- **`deploy/`** (venue provisioning, networking) is planned in the README,
  not created. **Operator control beyond the Console** (physical controls,
  a Registration Node convention) is a later decision.
- **mm-fairyring follow-ups here:** mm-terrarium
  [#100](https://github.com/Musical-Mycology/mm-terrarium/issues/100)
  (a `set_admin_devices` down-command writing `[admin] devices` from a
  MycoQuest manifest, how the admin site gets a GemID onto a box);
  [#127](https://github.com/Musical-Mycology/mm-terrarium/issues/127)
  (a refused identity still replays and clears the journal, then
  hot-loops the reconnect); two boxes
  sharing an identity displace each other forever, each closing the other
  with websocket close code 4409; and
  `bit_completed` needs a run id so a replay cannot credit a later quest.
- **`GET /join.json`** on the LAN static server (mm-tuneshroom's
  join-launcher spec, section 4.1) is unbuilt. It must serve `start: null`
  (`start_key=None`), as it omits `qr_svg`: `start` carries the key and the
  prepare URL.

## Design docs (in-repo, authoritative)

Each spec's Status line records what was live-verified; all specs are in
`docs/superpowers/specs/`, linked from their sections above.

- Canonical architecture: [`docs/control-gameserver-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/control-gameserver-design.md).
- [Bootstrap](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-18-mm-terrarium-bootstrap-design.md); [first slice](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md) (lifecycle engine, TestBit).
- [Uplink](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md); [Console](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-07-21-terrarium-console-design.md); [Tuneshroom audio](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md).
- [Student hardware track, ESP32 revision](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md):
  the Dec 4 build (Tuneshroom firmware, Tower fixture, Mushica Bit, gates,
  acceptance). The 2026-08-06 track spec (Radxa, Pi 5, 864 px array) is kept
  only for its reasoning; none of its tasks started.
- [Room concept and load sequence](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md) and the [Terrarium Visualization Simulator](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md) (TEST Room).
- [Control on o2lite, and timed cues](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-12-control-o2lite-and-timed-cues-design.md); [load-bearing timed cues](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-14-load-bearing-timed-cues-design.md):
  read its section 2 (the timing model) before touching cue timing.
- [Teardown order and the stack runner](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-14-teardown-order-and-stack-runner-design.md).
- Spec A, [Room panel and Room fixtures](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md); Spec B, [Bit-declared triggers, cue scripts and conditions](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-17-bit-declared-triggers-and-cue-scripts-design.md); Spec C, [the N-fixture Room](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-18-n-fixture-room-design.md) (Spec B section 4.2's deferral).
- Room/Instrument/Trigger restructure: [Instruments and Fixtures](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-27-instruments-and-fixtures-design.md) (Spec 2); [Functions and the Trigger rename](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-27-functions-and-trigger-rename-design.md) (Spec 3: acting side is Function, sensing side owns Trigger).
- [Instrument handshake protocol](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-10-01-instrument-handshake-protocol-design.md)
  (contract v3: hello, handshake, validated, one role per device at
  RUNNING); supersedes the registration flow in the canonical architecture
  doc and the lobby double-tap join (*Lobby and the handshake*).
- [Light lexicon](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/light-lexicon.md)
  (2026-10-05): the reserved status-light vocabulary for every instrument,
  fixture and Bit, and where the code does not match it yet.
- Cited under *Not yet built*: [o2lite connectivity migration](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md) (Phase 3 open), [device liveness](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md), [VENUE Room](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-25-venue-room-design.md).

Game-design background (RenQuest integration, Bit scoring and loop rules,
hardware) is in MM-internal docs, not needed to work on this architecture.
