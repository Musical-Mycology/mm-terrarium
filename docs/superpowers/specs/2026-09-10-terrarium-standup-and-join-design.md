# Terrarium standup script, room-aware Bit load, and the Join card

**Date:** 2026-09-10
**Status:** Approved design, pre-implementation
**Driver:** Bringing up a Terrarium and connecting a device (a Tuneshroom
web sim, a phone) is a multi-terminal, flag-heavy workflow. There is no
one-command "clean Terrarium, no Bit" standup, the Console does not ask
which Room a Bit should load into, and the guest URL exists only as one
stdout line. This spec adds a clean standup script, a room prompt on Bit
load, and a Join card that shows the URL, a QR code, and a pre-populated
Tuneshroom launch command.

## What exists today (for the record)

- `./smoke-test.sh` wraps `harness/run_stack.py`, which always resolves a
  Bit (`TestBit` by default) and boots that Bit's `default_room_type`.
  `run_stack` owns the hard-won Arco pty / settle / PYTHONPATH / log-tee /
  teardown knowledge; nothing else should re-implement it.
- `harness/terrarium_boot.py` can boot to `NO_ROOM` and wait for the
  Console (`--console-port` with no `--room`), but it still requires a Bit
  and `run_stack` never exposes the no-room path.
- The Console has a separate Load Room flow (`console/static/rooms.js`) and
  a Load Bit picker (`console/static/bit.js`). Load Bit is gated on
  `ROOM_READY`; the picker prints a Bit's room types as text and never
  asks for one. `load_bit` is refused with "no room loaded" otherwise.
- The guest page URL is printed once at boot as a `WWW_URL:` line
  (`harness/www_server.py`, port 8788). No QR exists anywhere in this repo.
  The Tuneshroom web app has a `?poster=1` QR route, but only when a Flutter
  build has been staged with `--web-build`.
- The Tuneshroom web sim (`lib/sim_main.dart` in mm-tuneshroom) resolves
  `dev`, `link`, `node`, `nodes`, `ens`, `o2ws` from URL query params, then
  `--dart-define` (`DEV`, `LINK`, `NODE`, `NODES`, `ENS`, `O2WS`), then
  defaults. It mints its own `dev` when none is given.

**Constraint carried into the design.** Since the o2lite cutover
(2026-09-08) the Terrarium speaks only o2lite and o2ws. Native
`flutter run -d ios` / Android and the Radxa app still use the old
websocket wire and cannot connect. The only Tuneshroom path that works
today is the browser sim over o2ws, so that is the only launch command
this spec pre-populates. The native form waits on mm-tuneshroom's FFI
o2lite link and is out of scope here.

## Decisions

All confirmed with the operator (Chris) during brainstorm, 2026-09-10:

- Standup is `./terrarium.sh` on top of a new `run_stack --no-bit` path.
  Not a standalone launcher, not a shell-only wrapper: the Arco/teardown
  knowledge stays in one place.
- The Bit picker always prompts for a room, preselected to the active room
  when compatible, else the Bit's default. A different choice unloads and
  reloads the Room (about 15 s of Arco spawn, with progress).
- The join URL and QR live in a Console **Join card**, rendered
  server-side with `segno` (pure Python, BSD), and the same URLs print on
  stdout as `JOIN_URL:` marker lines for a headless box.
- The pre-populated device command is the Chrome sim form only, with a
  note that native and Radxa builds are pending the FFI link.

## Design

### 1. `./terrarium.sh`

A sibling of `smoke-test.sh`, same shape:

```bash
#!/usr/bin/env bash
# Clean Terrarium standup: Console + guest page, no Bit, no Room unless
# --room NAME is given. Any run_stack flag may follow and overrides these
# defaults (argparse: last occurrence wins).
#   ./terrarium.sh
#   ./terrarium.sh --room TEST
#   ./terrarium.sh --room DEMO --console-port 9000
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PYTHONPATH=/Users/chris/projects/arco
exec .venv/bin/python -m harness.run_stack --no-bit --serve --devices 0 \
  --console-port 8772 "$@"
```

Port 8772 is the README's documented Console port. It does not collide
with Arco's HTTP on 8080 or the guest page on 8788. Devices default to 0:
this script stands up a Terrarium for real or browser devices to join, it
does not spawn Testshrooms.

### 2. `run_stack --no-bit`

- `StackConfig.bit` becomes `str | None`, `StackConfig.node` becomes
  `str | None`, and a `no_bit: bool` field is added.
- `config_from_args` under `--no-bit`: `--bit`, `--profile`, `--node` and
  `--devices N>0` are refused at parse time with a located message (there
  is no Bit to derive a node from, so a spawned device would have nothing
  to join). `room_type` is `args.room` verbatim, possibly `None`. `--ci`
  stays allowed: a bounded no-Bit run (default 45 s) is a useful smoke
  check of the standup path itself.
- `control_command` omits `--bit`, adds `--no-bit`, and adds `--room` only
  when a room was given.
- `run()`'s Control stage table depends on the mode:
  - `--no-bit` with `--room`: `control-room-loaded`, `control-ready`. The
    `control-setup` gate is skipped (no Bit, no SETUP).
  - `--no-bit` without `--room`: one gate, `control-no-room-wait`, on a
    new marker `CONTROL_NO_ROOM_WAIT` (section 3). No Arco exists yet in
    this mode, so nothing else can be waited on.
  - Existing Bit runs: unchanged.
- No devices are spawned when `devices == 0` (already the case). The hold
  is the existing serve hold (until Ctrl-C or a child exit). The success
  summary prints the Console and guest-page URLs as it does today.

### 3. `terrarium_boot --no-bit`

- New flag `--no-bit`, mutually exclusive with `--bit` and `--profile`.
  Refused without `--console-port`, mirroring the existing rule for a
  missing `--room`: nothing would ever load a Bit.
- `BootConfig.bit_name` becomes `str | None`. `build()` skips the whole
  `load_bit` block when it is `None`; a `--room` build then returns with
  the Room in `ROOM_READY` and the engine in `IDLE`.
- `main()` under `--no-bit` forces serve mode and enters the existing
  loops directly: `_serve_roomless` when there is no room, `_serve_rounds`
  (which begins in `_wait_for_load`, sitting in IDLE until the Console
  loads a Bit) when there is one. No round-1 `CONTROL_ROUND_LOADED` is
  printed; the Console's first load prints it from `_serve_rounds` as it
  does for every later round today.
- New marker `markers.CONTROL_NO_ROOM_WAIT = "NO_ROOM: waiting for the
  Console to load a Room"`, printed once on entry to the `NO_ROOM` wait
  (`_wait_for_room`/`_serve_roomless` entry). Pinned by
  `tests/test_markers.py` like every other marker.

### 4. Room-aware `load_bit`

Superseded in part by Addendum 2 (D7) of the plan and the Status below: a
Room switch in a running Terrarium is refused; only the NO_ROOM-to-Room
load and same-Room loads are supported.

**Wire.** `LoadBitCommand` gains `room: str | None`; `parse_command`
accepts an optional string `room`. `bits_listed` rows gain
`default_room_type` (string) and `nodes` (the manifest's role-to-node
map, `{role: node}`), both read off `BitConfig.launch`.

**Agent** (`console/agent.py`, `LoadBitCommand` branch), in this order:

1. Resolve the Bit's config exactly as today (`registry.resolve_config`);
   a manifest refusal is an `error_event` before anything else happens.
2. Determine the target room: `command.room` when given, else the active
   room. With a Terrarium wired and no target room at all, refuse with
   the existing "no room loaded".
3. If the target room is not in the resolved config's
   `launch.room_types`, refuse with `Bit <name> does not support <room>`
   **before touching the Room**. (Today this check lives only in
   `terrarium_boot.build()`; the engine does not perform it.)
4. If the target room differs from the active one: if a Bit is loaded,
   `gs.abort()` first; if a Room is active, `terrarium.unload_room(force=
   True)`; then `self._load_room(target)`. Each step's refusal is returned
   as an `error_event` and stops the sequence. `_load_room` already
   broadcasts progress and `room_load_failed` events, so the picker sees
   the Arco spawn stages exactly as the Rooms panel does.
5. `gs.load_bit(name, config=cfg)` as today.

`load_bit` without `room` behaves exactly as before. `load_room` and
`unload_room` are untouched.

**Picker** (`console/static/bit.js`):

- Every Bit card gets a room `<select>` built from the Bit's `room_types`
  intersected with the rooms in `snapshot.rooms` whose `status` is null
  (configured and loadable). It preselects the active room when it is in
  that set, else the Bit's `default_room_type`. An empty intersection
  disables Load with the note "no configured room supports this Bit".
- When the selection differs from the active room the card shows
  "switches Room: Arco restarts (about 15 s)".
- Load sends `{name, overrides, room}`.
- The sidebar Load button and the picker enable whenever the Terrarium is
  settled, `NO_ROOM` or `ROOM_READY`, not mid-transition. `bit.js` reads
  `snapshot.rooms` and follows `room_loaded` / `room_unloaded` so its room
  list stays current; it does not reach into `rooms.js` state.

### 5. Join info and the Join card

**Pure module** `control/join_info.py`:

```python
def build_join_info(*, lan_ip, www_port, arco_http_port, ensemble,
                    bit_name, nodes, app_present, qr_svg=None) -> dict
```

returns

```
{
  "www_url":      "http://<ip>:8788/app/",
  "o2ws_host":    "<ip>:8080",
  "ensemble":     "arco",
  "app_present":  bool,          # www/app/index.html exists
  "bit":          "<name>" | None,
  "nodes": [ {"role": "player", "node": "METRO_PLAYER_NODE",
              "url": "http://<ip>:8788/app/?node=METRO_PLAYER_NODE&o2ws=<ip>:8080&ens=arco",
              "qr_svg": "<svg ...>" | None,
              "tuneshroom_cmd": "flutter run -d chrome -t lib/sim_main.dart --dart-define=NODE=METRO_PLAYER_NODE --dart-define=O2WS=<ip>:8080 --dart-define=ENS=arco"} ],
  "native_note":  "Native iOS/Android and the Radxa app cannot connect until mm-tuneshroom's FFI o2lite link lands; use the Chrome sim."
}
```

- `dev` is deliberately absent from the URL and the command: the app mints
  one per page load, and many phones scan one poster.
- The command is written to be run from the mm-tuneshroom checkout root;
  the card says so. No path is guessed or embedded.
- `qr_svg` is produced by an injected callable (default: `segno.make(url,
  error="m").svg_inline(scale=4)`); `None` when the callable is absent or
  raises, so the card degrades to URL-only rather than failing.
- `segno` is added to `requirements.txt`. It is pure Python with no
  transitive dependencies, so the offline test property is unchanged.

**Plumbing.** `ConsoleAgent` gains an optional `join_info` provider
callable (same pattern as `canvas_urls`). `terrarium_boot` builds it from
`lan_ip()`, `WWW_PORT`, `ARCO_HTTP_PORT`, the ensemble, and a closure
reading the loaded Bit's `config.launch.nodes`. The snapshot gains a
`join` field; a `join_changed` event is broadcast from the agent's
`on_state_change` whenever the engine enters `LOADED` or returns to
`IDLE`. When no Bit is loaded, `nodes` is empty and the card shows the
bare guest URL.

**stdout.** New marker `markers.JOIN_URL = "JOIN_URL:"`. `terrarium_boot`'s
lifecycle logger prints one `JOIN_URL: <role> <node> <url>` line per node
when a Bit reaches `LOADED` (round 1 and every Console load). `run_stack`
echoes them as it echoes everything else; it does not gate on them.

**Card** (`console/static/join.js`, mounted below the Loaded-Bit panel):
guest URL (with a "no web build staged under /app/, pass --web-build"
warning when `app_present` is false), then one row per node with the QR,
the URL, and the Tuneshroom command, each with a Copy button
(`navigator.clipboard`, with a select-and-`execCommand` fallback on plain
HTTP origins where the clipboard API is unavailable). The native note
renders once at the bottom. No external fetches; the SVG is inline, which
`tests/test_console_static.py`'s no-external-asset test keeps honest.

### 6. Tests (all offline)

- `tests/test_run_stack.py`: `--no-bit` parsing and refusals (`--bit`,
  `--profile`, `--node`, `--devices 1`); `control_command` shape with and
  without `--room`; the stage table per mode; a no-Bit run with the fake
  Popen reaching the hold.
- `tests/test_terrarium_boot.py`: `--no-bit` refused without a Console;
  `build()` with `bit_name=None` and a room loads the Room and no Bit;
  `CONTROL_NO_ROOM_WAIT` printed on the no-room path.
- `tests/test_console_protocol.py`: `load_bit` with and without `room`;
  `bits_listed` carries `default_room_type` and `nodes`.
- `tests/test_console_agent.py`: unsupported room refused before the Room
  is touched; same-room load touches no Room; different-room load runs
  abort, unload, load, `load_bit` in order against a fake Terrarium; a
  `load_room` refusal stops the sequence with no `load_bit`; `join` in
  the snapshot and `join_changed` on LOADED and IDLE.
- `tests/test_join_info.py`: URL shape, command shape, `dev` absent,
  `qr_svg` None when the encoder is absent, `app_present` threading.
- `tests/test_markers.py`: the two new markers.
- `tests/test_console_static.py`: `join.js` present and an ES module.

### 7. Live verification (on MYCOLOGICAL, after the plan lands)

1. `./terrarium.sh`: Console at `http://127.0.0.1:8772/` in `NO_ROOM`,
   `WWW_URL` printed, no Arco process.
2. Load Bit picker shows a room select for TestBit; choose TEST: Rooms
   panel shows the spawn stages, Bit loads, Join card shows QR + URL +
   command.
3. `./terrarium.sh --room DEMO`: Arco up, `ROOM_READY`, engine `IDLE`;
   pick a Bit whose default is TEST and confirm the select preselects
   DEMO when compatible, and that choosing TEST restarts Arco.
4. Open the Join URL in desktop Chrome (with `--web-build` staged) and
   confirm a join; then run the copied `flutter run` line from the
   mm-tuneshroom checkout and confirm a second join.
5. Scan the QR with a phone on the same LAN: stays with Chris.

## Out of scope

- Native / Radxa Tuneshroom connectivity (mm-tuneshroom FFI o2lite link).
- Console authentication (the trusted-LAN model is unchanged; the Join
  card exposes only what `WWW_URL` already prints).
- Printed posters and the Tuneshroom app's own `?poster=1` route.
- `run_stack`'s unforwarded `--sim-host` on the o2lite simulator path
  (tracked separately in the deep-dive).

## Risks and open points

- `_serve_rounds` has only ever been entered with a Bit already loaded
  for round 1. Entering it cold in `IDLE` relies on `_wait_for_load`'s
  documented "sit in IDLE until the Console loads a Bit" behavior; the
  plan pins that with a test before wiring `main()`.
- A room switch inside the `load_bit` handler blocks the Console
  websocket handler for the Arco spawn (~15 s). `load_room` already has
  exactly this shape from the Rooms panel, so this is accepted, not new.
- `segno.svg_inline` exists from segno 1.3; the plan pins `segno>=1.5`.

## Status

**Implemented 2026-09-10/11.** Suite: 2142 passed, 1 skipped (the
pre-existing skip; unchanged by Task 14). Task 15 added the D7 refusal
tests; Task 16's re-run (before and after its live phase, 2026-09-11):
2143 passed, 1 skipped both times, stable. Live on MYCOLOGICAL:

- **Step 2, `./terrarium.sh` with no Room.** Stdout printed, in order,
  `BROWSE_URL: Terrarium Console at http://127.0.0.1:8772/`, `WWW_URL:
  http://192.168.1.136:8788/ (guest page; o2ws goes to Arco on 8080)`,
  `NO_ROOM: waiting for the Console to load a Room`; no `Traceback`, no
  spawned Arco process. Client snapshot: `terrarium_state == "NO_ROOM"`,
  `join` non-null with `nodes == []`, `bits_listed` rows carried
  `default_room_type` and `nodes`. Matches spec.
- **Step 3, load a Bit with a Room, then switch.** Booted via
  `./terrarium.sh --room TEST` (round 1's TestBit already loaded at boot,
  not a true NO_ROOM start -- corrected label; the original note below
  said "from NO_ROOM"), so `load_bit TestBit room: TEST` reloads the
  already-loaded Room and Bit: `room loaded: TEST`, `join_changed` with
  two node rows (jammer, player), each `qr_svg` starting `<svg`, each URL
  of the form `http://192.168.1.136:8788/app/?node=...&o2ws=192.168.1.136%3A8080&ens=arco`,
  `state_changed` to SETUP, stdout `DeviceLink running on o2lite ensemble
  'arco' (restarted)` and two `JOIN_URL:` lines, `round loaded: TestBit`.
  A true NO_ROOM boot's first combined `load_bit ... room:` does not
  print `round loaded:` -- the engine is already out of IDLE by the time
  `_serve_rounds` is entered for round 1, so that marker (only printed on
  a later Console reload once rounds are already looping) never fires the
  first time around; see Run 1 of the Task 16 live re-verification below,
  which boots truly NO_ROOM and reaches SETUP with no `round loaded:`
  line at all.
  **A room switch (`load_bit TestBit room: DEMO`, then back to `room:
  TEST`) was refused, not attempted -- D7, ruled and fixed in Task 15,
  live-verified in Task 16 (2026-09-11).** Root cause, read from pyarco
  rather than a debugger: `arco.initialize()`'s first line is `if o2lite
  and o2lite.time_get() > 0: return None  # already started`
  (`/Users/chris/projects/arco/pyarco/arco_engine.py`), so a second
  `pool.start()` in the same process is a no-op against the dead o2lite
  singleton, and `finish()` does not prepare a restart. Control can
  therefore talk to exactly one Arco per process until pyarco/o2litepy
  support re-initialization. Task 15 fixed this rather than merely
  documenting it: `ConsoleAgent._ensure_room_for_bit` refuses a switch
  away from the active Room before touching it (unknown/unloadable
  checks still run first), naming the restart command in the refusal;
  and `_RoomWiring` now restarts Control's own clients BEFORE
  `rewire_room()` grants Room audio, closing the ordering race Task 14
  found (the observer used to call `_grant_room_audio` ->
  `pool.acquire()` synchronously inside `terrarium.load_room()`, ahead
  of the caller's own `restart_room_clients()`).

  Live re-verified (Task 16, 2026-09-11, `r16-1.log`/`r16-2.log`,
  scratchpad `console_client.py`). **Run 1** (`./terrarium.sh`, no
  Room): booted to NO_ROOM in order (`BROWSE_URL:`, `WWW_URL:`,
  `NO_ROOM: waiting for the Console to load a Room`), `load_bit TestBit
  room: TEST` reached SETUP with `DeviceLink running on o2lite ensemble
  'arco' (restarted)` printed before the two `JOIN_URL:` lines and no
  `_RoomWiring` traceback; `load_bit TestBit room: DEMO` (the switch)
  returned `{"event": "error", "command": "load_bit", "message":
  "switching Rooms in a running Terrarium is not supported yet: pyarco
  cannot reconnect to a new Arco in one process; stop and run
  ./terrarium.sh --room DEMO"}`, no `room_unloaded` event was ever sent,
  and a fresh snapshot afterward still showed `terrarium_state:
  "ROOM_READY"` with `room.room_type: "TEST"`. **Run 2**
  (`./terrarium.sh --room DEMO`): booted straight to `room loaded:
  DEMO` then `DeviceLink running on o2lite ensemble 'arco' (Ctrl-C to
  stop)`, and `load_bit TestBit room: DEMO` (same room, no switch)
  reached SETUP printing `round loaded: TestBit` and two `JOIN_URL:`
  lines. Grep counts across both control logs: `_RoomWiring` 0, `must
  run before acquire` 0, `room audio tick failed` 0, `Network is
  unreachable` 0, `StopIteration` 0, `Traceback` 0 -- D2 through D7 all
  hold live. SIGINT again did not stop either run within 20 s (SIGTERM
  both times, as in Task 14); `pgrep -fl 'apps/pytest/server
  |terrarium_boot|o2_shroom|run_stack'` was empty after each teardown
  (zero orphans).

  One observational note, not a defect: TestBit's own round
  (`start.when: "immediate"`) auto-completes in under two seconds once
  loaded, so in Run 1 the round had already advanced past SETUP (to
  RUNNING, then on through its own completion) by the time the refused
  switch's `error` event came back -- a race against TestBit's own
  timing, unrelated to the Room-switch code path. The invariants D7
  actually depends on -- no `room_unloaded`, `terrarium_state` staying
  `ROOM_READY` with the original Room active, the refusal message
  itself -- held regardless of the round's own state in every attempt.
- **Step 4, `./terrarium.sh --room DEMO --web-build
  .../mm-tuneshroom/build/web`.** Stdout: `room loaded: DEMO`,
  `DeviceLink running on o2lite ensemble 'arco' (Ctrl-C to stop)`, no
  `round loaded:` before a Console load. Snapshot: `state == "IDLE"`,
  `terrarium_state == "ROOM_READY"`, `join.app_present == true`. `curl
  -sI http://127.0.0.1:8788/app/` returned `200`, `Content-type:
  text/html`. Loading TestBit into DEMO (no Room switch, so the D7
  ordering defect above did not trigger) printed two `JOIN_URL:` lines
  and `round loaded: TestBit`, reached SETUP cleanly, no `Network is
  unreachable` or `StopIteration` (0 matches). SIGINT again needed
  SIGTERM after 20 s (recorded); zero orphans afterward. `git status
  --short` was clean afterward (`www/app/` is git-ignored). The only
  tracebacks in this run's log were `BrokenPipeError: [Errno 32] Broken
  pipe` / `OSError: [Errno 9] Bad file descriptor` from `o2litepy` during
  the SIGTERM teardown, matching D6 (deferred, pre-existing).
- **Step 5, real browser guest join and the `flutter run` line.** Not
  run headlessly; with the controller / Chris.

**D1 to D6 (first live pass, 2026-09-10), fixes landed in Tasks 12 and
13:**

- **D1** (`./terrarium.sh` with no Room died in `build()`: `pool.start()`
  and `transport.start()` ran unconditionally against a hub that does
  not exist yet). Fixed: a NO_ROOM boot leaves both Arco clients stopped
  and `restart_clients()` (now unconditionally defined) starts them on
  the first Console `load_room`, printing `TRANSPORT_READY ...
  (restarted)`. Re-verified live in Step 2/3 above (one-shot NO_ROOM
  runs now boot and load cleanly).
- **D2** (a Console room switch swapped Arco under Control's own
  clients: 13,693 `Network is unreachable` lines in one run). Fixed:
  `ConsoleAgent` takes `stop_room_clients` / `restart_room_clients`
  hooks and calls them around the switch (stop, unload, load, restart).
  Re-verified live: 0 `Network is unreachable` lines in either Step 3 or
  Step 4's control log.
- **D3** (the serve loops kept polling the old Arco handle after a
  switch, never draining the new Arco's pty). Fixed: `_live_arco`
  resolves `terrarium.arco` every loop iteration in `_serve_rounds`,
  `_wait_for_load`, `_wait_in_setup`, `_serve_until_done`.
- **D4** (Room simulators spawned with the boot room's type, raising
  `StopIteration` on a switch to a different room type). Fixed:
  `Terrarium.loading_room` is set for the duration of `load_room`; the
  simulator factory resolves the Room type through a callable at spawn
  time. Re-verified live: 0 `StopIteration` lines in either run's log.
- **D5** (`load_bit` unloaded the active Room before checking the
  target was loadable, stranding the operator in NO_ROOM on a refusal).
  Fixed: `_ensure_room_for_bit` checks `validate_rooms` for the target
  before touching anything; `terrarium_boot` always passes
  `array_backend="simulator"` so every configured room is loadable from
  any boot.
- **D6 (fixed 2026-09-11).** `BrokenPipeError`/`EBADF` tracebacks from
  pyarco's `Ugen.__del__` on every teardown, also present in 2026-09-08
  logs. Root cause: nothing shut the `ArcoSynthPool` down at process
  exit, so Arco was SIGTERMed with the pool's ugens alive and
  `arco.finished` False, and each ugen's destructor sent `/arco/free` on
  the dead socket at interpreter exit. Fix: `main()` registers the
  `AudioBridge`'s shutdown on `pre_room_teardown` ahead of the transport
  (`_register_room_audio`), and both `ArcoSynthPool.shutdown()` and
  `AudioBridge.shutdown()` finish in a `finally`. Live: `./terrarium.sh
  --room TEST`, SIGINT, `Traceback` count 0, exit 1 s, zero orphans
  (runs/20260911-120253).
- **The SIGINT-needs-SIGTERM note in Tasks 14 and 16 was the test
  harness, not the product.** `bounded.sh` backgrounded the command from
  a non-interactive bash without job control, which starts it with
  SIGINT ignored; Python then never installs `KeyboardInterrupt`, so
  `kill -INT` was a no-op. Under `set -m` the same `kill -INT` tears the
  stack down in 1 s. Recorded with the recipe in `docs/MM_TERRARIUM.md`'s
  2026-09-10 entry.

Deviation: the Join card lives in the Live view's content column (after
the Bit status card) rather than the sidebar, because the URL and
command lines need the width. Phone-on-LAN QR scan and the `flutter run`
line: with Chris.
