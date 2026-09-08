# o2lite as the only device wire: the connectivity migration plan

**Date:** 2026-09-08
**Status:** Approved overall design. Each phase gets its own refined spec and
implementation plan when it starts; this document is the shared map.
**Repos touched:** `mm-terrarium` (Phase 1, Phase 3 packaging),
`mm-tuneshroom` (Phase 2, Phase 3 links), Arco serving config (Phase 1
prep, Phase 2).
**Owners:** Chris (mm-terrarium, Arco serving config), Victor
(mm-tuneshroom `lib/link/` and the join flow). Roger owns o2, o2lite,
o2ws.js and Arco; nothing here changes those.

---

## 1. Why

Control today has two device wires and one of them is the default. The
websocket wire (`devicelink/server.py`, JSON envelopes to a socket Control
owns) is what `harness/terrarium_boot.py` starts unless told otherwise, and
it is the only wire mm-tuneshroom's sim stack speaks. The o2lite wire
(`devicelink/o2_transport.py`, Control offering `game` on the Arco hub) is
opt-in, and it is the one every live measurement, every timed-cue result,
and `harness/run_stack.py` already use.

Two wires cost more than their code. Every device-facing change lands
twice, the fakes and the real libraries drift in different directions, and
the design doc's architecture (one hub, every device an O2 participant)
stays a diagram while the default path bypasses it. This plan makes o2lite
the only device wire and gives each client platform its own O2 link to
Arco.

## 2. End state, in one picture

```
Phone browser  --o2ws-->  +--------------+
Native app     --o2lite-> | Arco server  |  one o2lite conn   Control+GameServer
ESP32 Tuneshroom -o2lite> | "arco"       | <----------------> offers "actl,game"
                          +--------------+                    (pyarco + transport)
  each device offers "ie<N>"
```

- **Arco** is the only full-O2 process in the room and the only hub. It
  also serves the browser page over HTTP.
- **Control** is one o2lite client with **one** connection offering both
  `actl` (pyarco's Arco control) and `game` (the device transport). One
  connection per process is o2lite's model: the C library keeps its
  connection in process-wide statics, and `set_services` takes a
  comma-separated list precisely so a process announces everything it
  offers over its single link. Control has no device-facing socket of its
  own.
- **Devices** reach Arco over three physical links: the o2lite C library on
  hardware, the same C library behind `dart:ffi` in the native app, and
  o2ws (O2 over websocket, `o2ws.js`) in a browser page Arco serves.
- **Two things stay websocket, deliberately.** The Terrarium Console is a
  LAN admin panel for a browser; the Uplink is a WAN link to the future
  fairyring broker. Neither is in the real-time loop, and o2lite is a
  single-hub LAN protocol with mDNS discovery, so it fits neither.

Production client targets are all three: hardware Tuneshroom, native phone
app, and phone browser via QR. The browser path is a must-build, not a dev
convenience, because it is the design doc's guest path.

## 3. The wire contract does not change

The vocabulary is already implemented on both sides:
`devicelink/protocol.py` (Python, source of truth) and
`mm-tuneshroom/lib/link/envelope.dart` (Dart counterpart). What changes is
**framing**: a JSON envelope `{timestamp, address, typespec, args}` on a
websocket becomes an O2 message with the same address, typespec and
arguments. The envelope was designed to mirror o2ws field-for-field so this
swap would be mechanical.

| Direction | Address | Typespec | Notes |
|---|---|---|---|
| device to Control | `/game/hello` | `ssss` | `dev`, name, protoversion, instrument; `dev` is always the first argument (Design Rule 2) |
| device to Control | `/game/join` | `ss` | `dev`, node; node id carried in the QR URL |
| device to Control | `/game/tilt` | `sf` | `dev`, gamma; message timestamp is the gesture's O2 time (Design Rule 4) |
| device to Control | `/game/tap` | `sffi` | `dev`, peak g, duration ms, count |
| device to Control | `/game/shake` | `sfff` | `dev`, peak g, duration ms, sweep degrees |
| device to Control | `/game/canvas` | `ss` | `dev`, canvas URL; Testshroom only |
| device to Control | `/game/capture` | `ssb` | |
| device to Control | `/game/telemetry` | `sfb` | batches re-chunked so no message exceeds 4096 bytes |
| Control to device | `/<dev>/role`, `/<dev>/room` | `b` | UTF-8 JSON blob |
| Control to device | `/<dev>/leds` | `b` | raw bytes; the message timestamp is the presentation time |
| Control to device | `/<dev>/deny`, `/<dev>/error`, `/<dev>/play` | `ss` | |
| Control to device | `/<dev>/release` | empty | arrives after the closing fade, not at Bit end |

**Blob framing rule** (already encoded in `to_o2_arg` /
`from_o2_arg`): a list of ints is raw bytes; anything else is UTF-8 JSON.
A receiver tries JSON first and falls back to bytes.

**Message size.** o2lite's C library caps a message at 4096 bytes
(`o2/src/o2lite.h:135`). o2litepy allows 8192, but hardware and the FFI
link use the C library, so **4096 is the contract**. Today only telemetry
batches (about 3200 bytes of PCM per 100 ms plus motion samples) can
exceed it.

**Service naming.** Each device offers exactly one service, its own
`ie<N>`. O2 forwards a service to one provider and gives the loser of a
race no error, so every live process must claim a distinct name and
self-verify its claim (`verify_service_ownership` is the sanctioned
pattern).

## 4. Phases

### Phase 1: Terrarium cutover (mm-terrarium, Chris)

Goal: o2lite is the only device wire in this repo, and Arco is ready to
serve a page.

1. **Control owns the process's services string.** One o2lite connection
   per process, as today, but the ownership is explicit: after
   `arco.initialize()` returns (pyarco has announced `actl`), Control
   applies the full string `"actl,game"` once, from one constant, and a
   test pins that pyarco's service name is in it. This closes the
   set_services-replaces trap (`o2lite.py:707`) without a second
   connection, which the C library cannot do and which a dead hub would
   not benefit from anyway, since `_recycle_room` restarts pyarco and the
   transport together.
2. **`terrarium_boot` defaults to o2lite** and the websocket branch is
   deleted: `devicelink/server.py`, `harness/room_simulator.py`,
   `harness/shroom_client.py`, `harness/devicelink_smoke.py`, and the
   `--transport` flag itself. `harness/o2_shroom.py` is the only
   Testshroom, covering both the Room simulator (`--no-join`) and player
   devices. `run_stack`'s `--flutter-sim` websocket launch is removed with
   it and returns in Phase 2 as a URL served by Arco.
3. **Capture moves to o2lite.** `harness/capture_smoke.py` and the
   `/game/telemetry` path re-chunk batches under 4096 bytes; the decoder
   reassembles by `seq`. `docs/telemetry-trace-schema.md` records the chunk
   rule for the mm-tuneshroom capture client.
4. **One clock.** `DeviceLinkAgent` takes O2 time as its only clock instead
   of a per-path injection. `time.monotonic` leaves the device path.
5. **`protocol.py` stays the vocabulary's source of truth.** Its JSON
   encode/decode becomes the internal representation `FakeO2Lite` and the
   tests use; nothing on a wire is JSON-framed any more.
6. **Tests.** The offline-suite invariant holds: `FakeO2Lite` already
   dispatches only on `poll()`, and the rule that a double is never more
   permissive than o2litepy stands. `test_devicelink_server`,
   `test_shroom_client`, `test_devicelink_smoke` and the websocket branches
   of `test_terrarium_boot` and `test_run_stack` are deleted or reworked,
   not skipped.
7. **Arco serves.** `control/arco_process.py` starts Arco with
   `http_enable` on, an `http_port`, and an `http_root` (pref keys in
   `arco/server/src/prefs.cpp`), pointing at a new top-level `www/`
   directory carrying `o2ws.js`. This is the `arcoserver/` and `www/` work
   the README's planned layout lists as unbuilt. Depends on probe P2.
8. **Docs.** `docs/MM_TERRARIUM.md` loses its "both transports are
   maintained" line and the stale claim that the o2lite transport shipping
   `JoinResult.config` is unbuilt (it ships via `devicelink/agent.py`).

**Live gate.** `run_stack --ci` green with the services string pinned;
a Testshroom joined, rendering at its declared `when`; a capture round
trip with PCM reassembled from chunks; `o2wsclocksync.htm` loaded from
Arco's `www/` reaching clock sync in a browser; and the o2litepy ensemble
filter re-verified live with a second O2 host on the LAN.

### Phase 2: browser over o2ws (mm-tuneshroom, Victor; Arco serving, Chris)

Goal: a phone that scans a QR lands in the instrument, joined over o2ws to
Arco, with no Control-side socket anywhere.

1. **`O2wsLink implements DeviceLink`** inside `lib/link/`, wrapping
   `o2ws.js` through JS interop. It is the web counterpart of the
   `FfiLink` the seam's docstring anticipated. Nothing above `lib/link/`
   imports a platform library; `SimController` does not change.
2. **The page is the Flutter web build** (`flutter build web -t
   lib/sim_main.dart`) copied into Arco's `www/` root beside `o2ws.js`.
   The browser finds the hub from the URL it loaded, so no discovery.
3. **The QR URL points at Arco's HTTP port**, not Control.
   `lib/sim/join_params.dart` already resolves `dev`, `node` and `link`
   from the URL; `link` becomes the o2ws endpoint.
4. **Session shape.** `o2ws_initialize(ensemble)`, `o2ws_set_services("ie<N>")`,
   `o2ws_method_new` for each `/ie<N>/*` verb, wait for
   `o2ws_clock_synchronized`, then `/game/hello`. Gesture stamps use
   `o2ws_time_get()`; `/ie<N>/leds` frames are held to their timestamp
   the way `harness/o2_shroom.py` holds them.
5. **Envelope stays.** `Envelope` remains the in-app representation;
   `O2wsLink` converts it to and from o2ws calls using the blob rule in
   section 3.
6. **`run_stack --flutter-sim`** returns as "serve the web build from
   `www/` and print the QR URLs", replacing the deleted websocket launch.

**Live gate.** A phone scans a QR, joins a scored role during SETUP,
receives `/ie<N>/role`, renders frames at their `when`, and plays the same
Bit alongside a Python Testshroom on the same hub. Release ends with the
closing fade.

### Phase 3: native FFI link and packaging (mm-tuneshroom, Victor; deploy, Chris)

Goal: the native app is an o2lite client like hardware, and the Terrarium
depends on a pinned o2litepy rather than a sibling checkout.

1. **`FfiLink implements DeviceLink`** over the vendored o2lite C library
   (`native/o2lite/src/`), reusing `lib/ffi/o2_client.dart` as the binding
   and discarding the legacy stack around it. Same session shape as
   `O2wsLink` with o2lite C calls in place of o2ws ones.
2. **Discovery and ensemble.** mDNS on the venue LAN, filtered by the
   room's ensemble name, which the QR deep link carries so two Terrariums
   on one LAN cannot cross-connect. Depends on probe P4 (Victor's spike).
3. **Reconnect discipline** mirrors `harness/o2_shroom.py`: re-verify
   service ownership after any reconnect, and send the first message after
   `set_services` over TCP, never UDP.
4. **o2litepy is pinned and installed.** The `o2` repo's `o2litepy/` is a
   setuptools package (`pyproject.toml`, depends on `netifaces` and
   `zeroconf`), byte-identical in source to the `arco/o2litepy/` copy this
   repo reaches by `PYTHONPATH` today. It goes into `requirements.txt` at
   a pinned commit, `harness/arco_paths.ensure_o2litepy` becomes a
   fallback rather than the path, and the install step lands in the
   unbuilt `deploy/` directory. Depends on probe P3.
5. **Legacy stack deleted** in mm-tuneshroom once `FfiLink` passes the
   gate.

**Live gate.** The native app joins over FFI with the Phase 2 criteria,
on a phone on the venue LAN, with a Terrarium running from a fresh install
that has no `arco` checkout beside it.

## 5. Constraints carried through every phase

All are documented in `docs/MM_TERRARIUM.md`; they are upstream behavior or
O2 working as designed, and every phase designs around them:

- **pyarco's reset kills sockets of clients connected before it.**
  Control shares pyarco's connection, so it is never one of those
  clients; devices that attached before Control came up re-verify their
  service after the reconnect o2lite performs for them.
- **One o2lite connection per process.** Never a second `O2lite()`
  instance in Control, even though o2litepy would allow it: the C library
  on hardware and behind FFI cannot, and every client should share one
  connection model.
- **A refused service announcement is silent on the client.** Every
  client self-verifies with a self-addressed round trip after announcing.
- **A UDP send right after `set_services` can be dropped.** First send
  goes over TCP.
- **Distinct service names per live process.** Control `game`, pyarco
  `actl`, fixtures `sim-room-<fixture>`, devices `ie<N>`.
- **Orphans re-claim their old name on auto-reconnect.** `--exit-with-parent`,
  `TeardownStack`, and ownership verification stay.
- **4096-byte messages.** No wire shape may assume more.

## 6. Assumptions validated by experiment, not by asking

Each of these was a question for Roger in the first draft. Each is instead
a probe we run ourselves before the phase that depends on it, so any
question that does go upstream carries a measurement. Every probe is
throwaway code in the scratchpad or a sibling checkout, never committed.

### P1: withdrawn

The first draft probed whether Control could open a second o2lite
connection beside pyarco's. Withdrawn on review (2026-09-08): one
connection per process offering several services is o2lite's model, the
C library cannot do otherwise, and a second connection buys nothing on a
dead hub. Phase 1 step 1 is the ownership fix instead. The number is kept
so P2 to P5 keep their names.

### P2: Arco serves a page and o2ws reaches Control (gates Phase 1 step 7, Phase 2)

**Hypothesis.** With `http_enable`, `http_port` and `http_root` set in
Arco's prefs, Arco calls `o2_http_initialize`
(`arco/server/src/arco.cpp:1308`) and serves files from the root, and a
page loaded from it can clock-sync over o2ws and exchange messages with
Control's `game` service.

**Procedure.** **RUN ON: MYCOLOGICAL.** Step one is locating the prefs
file Arco actually reads (`prefs.cpp` names `prefs.arco` as the example;
confirm by running Arco and checking its startup output). Copy
`o2/test/www/o2ws.js` and `o2/test/www/o2wsclocksync.htm` into the root,
set the ensemble in the page to `arco`, load it in a browser, and confirm
clock sync. Then a 30-line page that offers `ie99`, sends `/game/hello`
over o2ws, and prints the `/ie99/room` blob Control pushes back, with
Control up via `terrarium_boot --transport o2lite`.

**Pass.** Clock sync in the browser, `/ie99/room` received, and a
`/game/join` that yields `/ie99/role` with the composed blob.

**If it fails at serving.** Terrarium serves the page from its own static
file server and the page's o2ws endpoint points at Arco; the probe then
checks `o2ws.js` accepts a host different from the page's origin. **If it
fails at o2ws itself,** that is the one finding that goes to Roger, with
the page and Arco's log attached.

### P3: the `o2` repo's o2litepy installs and runs the suite (gates Phase 3 step 4)

**Hypothesis.** `pip install` of `/Users/chris/projects/o2/o2litepy` into
the project venv replaces the `PYTHONPATH` reach into `arco/o2litepy`
with no behavior change.

**Procedure.** **RUN ON: MYCOLOGICAL.** In a fresh venv: install the
package editable, do not set `MM_ARCO_PATH` or `PYTHONPATH`, run the
offline suite, then `run_stack --ci`. Record the `o2` commit installed.

**Pass.** Offline suite green, `run_stack --ci` green, and
`ensure_o2litepy` never had to add a path.

**If it fails.** Phase 3 pins a commit of the `arco` checkout instead and
records it in `requirements-dev.txt`; the `deploy/` step clones that
commit.

### P4: o2lite C discovery from a phone (gates Phase 3 step 2, Victor)

**Hypothesis.** The vendored o2lite C library's mDNS discovery finds Arco
from iOS and Android on the venue LAN and filters by ensemble.

**Procedure.** **RUN ON: a phone on the same LAN as MYCOLOGICAL**, with
`run_stack` up. The legacy stack's `O2Client` already binds
`o2l_initialize` and `set_services`; a debug screen that initializes with
ensemble `arco`, reports `time_get()` once synced, and offers `ie98`.
Run it once with a second O2 host on the LAN using a different ensemble.

**Pass.** Sync within a few seconds on both platforms, and the client never
attaches to the wrong ensemble.

**If it fails on discovery.** `FfiLink` takes the hub address from the
QR deep link, the same way the browser takes it from the URL, and
discovery is bypassed. That is a smaller change than it sounds because the
join link already carries `link`.

### P5: telemetry chunking fits 4096 (Phase 1 step 3, offline)

Not a live probe. A unit test that encodes the largest batch the capture
client emits, asserts every chunk is under 4096 bytes on the wire, and
round-trips through `decode_telemetry_batch` with the same `seq` gap
detection the unchunked path has.

## 7. Sequencing and parallelism

```
Chris   P2 P3 ─────> Phase 1 ─────────────> www/ serving ──> Phase 3 packaging
Victor  P4 ──────> Phase 2 (O2wsLink) ────────────────────> Phase 3 (FfiLink)
```

- Victor starts on `O2wsLink` immediately. Control's `game` service is
  reachable over o2lite today through `run_stack`, and P2's 30-line page
  is the same thing `O2wsLink` does with Flutter around it. Until Phase 1
  step 7 lands, Victor serves the page from any static server and points
  o2ws at Arco, which P2 validates.
- Phase 1 and Phase 2 merge independently. The only handoff is the `www/`
  root layout, which Phase 1 step 7 fixes as "`o2ws.js` beside the web
  build's `index.html`".
- Phase 3's two halves are also independent: `FfiLink` needs P4,
  packaging needs P3.
- Each phase gets its own spec and plan at its start, revised by what the
  previous phase measured. This document is updated only when the end
  state or the ownership changes.

## 8. Out of scope

- The ESP32 firmware port. It is the declared design basis for
  mm-tuneshroom's `lib/sense/` and instrument modules and uses the same
  o2lite C library, but it has no hardware to test on before Gate 2
  (2026-10-16). It gets its own plan.
- Console and Uplink transports (section 2).
- The RGBW 48-channel LED wire question. Unchanged by framing; still an
  open decision.
- Scoring, fairyring, and anything else in the deep-dive's "not yet
  built" list that does not touch the device wire.
