# Browser guests over o2ws: Phase 2 of the connectivity migration

**Date:** 2026-09-08
**Status:** Approved design, pre-implementation. Refines Phase 2 of
`2026-09-08-o2lite-connectivity-migration-design.md` with what Phase 1 and
probe P2 taught; where the two disagree, this document wins for Phase 2.
**Repos touched:** `mm-terrarium` (wire flavor in the transport, a static
page server, launcher flags, docs), `mm-tuneshroom` (`lib/link/` o2ws link,
join parameters, hello token). Arco and o2 are unchanged.
**Owner:** to be assigned per half (section 10). If mm-terrarium and
mm-tuneshroom halves land in one session, the mm-terrarium half goes first
so the browser has a Control to talk to.

---

## 1. Why

Phase 1 made o2lite the only device wire. A phone that scans a QR poster
must now reach the Arco hub as an O2 participant, and a browser cannot run
the o2lite C library: it speaks o2ws, O2 over a websocket that the hub
terminates. Probe P2 confirmed the hub serves pages, a browser clock-syncs
over o2ws, and `/game/hello` and `/game/join` are granted. It also found the
one gap this spec is built around: **o2ws carries no blob type.** The host
writes a literal `?` for any argument type it does not know
(`o2/src/websock.cpp`, the `default` case of the argument encoder), and
`o2ws.js` has no `o2ws_get_blob`. Control's three blob-carrying messages
(`/<dev>/role`, `/<dev>/room`, `/<dev>/leds`) therefore cannot reach a
browser as sent.

A second finding shapes where the page comes from: O2's file server labels
every file `text/html` (`websock.cpp`, the `Content-Type` it writes) and
refuses any path containing `..`. Plain scripts run under that label, which
is why `o2wsclocksync.htm` works, but a Flutter web build carries `.wasm`
and module scripts that browsers refuse to load as `text/html`.

## 2. End state

```
phone browser --http--> Terrarium static server (www/, port 8788)
phone browser --o2ws--> Arco (port 8080) <--o2lite--> Control ("actl,game")
                                         <--o2lite--> hardware, Testshrooms
```

- The page is the existing Flutter guest app (the sim stack), built for web
  and served by a small static server the Terrarium harness runs. It opens
  its o2ws websocket to Arco's HTTP port.
- The browser is one more device to Control: it offers its own `ie-...`
  service, sends the same `/game/*` messages, and receives the same
  `/<dev>/*` messages, with three of them re-encoded as strings because it
  asked for that flavor in hello.
- Control keeps one transport and one vocabulary. The flavor is a per-device
  encoding decision inside `O2LiteTransport.send`, not a second wire.

Out of scope: the native FFI link (Phase 3), on-device audio for browsers
(`/<dev>/play` is received and ignored as today), any change to Arco or o2,
a QR poster generator (the app's existing `?poster=1` route still renders
one).

## 3. The wire flavor

### 3.1 Announcing it

A browser sends `/game/hello "ssss" dev name protoversion instrument` with
`protoversion = "o2ws/1"`. The sim sends `"1"` today
(`mm-tuneshroom/lib/sim/sim_controller.dart`, `_sendHello`); this is the only
change to that message. Control already stores `protoversion` per device in
the device pool (`control/device_pool.py`, `DeviceInfo.protoversion`).

Rule: a device whose protoversion starts with `o2ws/` gets the string
flavor. Everything else gets blobs. Unknown suffixes after `o2ws/` are
accepted (forward compatible); the transport logs the first one it sees.

### 3.2 What changes on the wire

| Message | blob flavor (hardware, native, Testshroom) | string flavor (`o2ws/*`) |
|---|---|---|
| `/<dev>/role` | `b`, UTF-8 JSON of the composed config | `s`, the identical JSON text |
| `/<dev>/room` | `b`, UTF-8 JSON of the room blob | `s`, the identical JSON text |
| `/<dev>/leds` | `b`, raw channel bytes; message timestamp is the presentation time | `s`, base64 of the identical bytes; same timestamp |
| everything else | unchanged | unchanged |

Constraints the encodings satisfy:

- An o2ws string field ends at byte 0x03 (`ETX`); JSON text and base64 never
  contain it. The transport asserts this rather than trusting it.
- Sizes: a composed role blob measures about 500 bytes (TestBit and
  MetronomeBit `player`), a 36-channel LED frame is 48 base64 characters, a
  full 864-pixel array frame would be 3456 characters; o2ws frames may be
  up to 65535 bytes (`websock.cpp` `len >= 0xffff` refusal). None of this
  is near the o2lite C cap because it never travels over o2lite C.
- The timestamp on `leds` is untouched, so the browser receives the frame
  at its `when` (section 6).

### 3.3 Where it lives in Control

- `DeviceLinkAgent._on_hello` already calls `self.server.bind_dev(dev,
  client)`. It gains `protoversion=` as a keyword argument; the transport
  keeps `self._protoversions[dev]`. `drop_dev` forgets it. The agent stays
  transport-agnostic: it passes a string it already has.
- `O2LiteTransport.send(dev, msg)` selects the encoding per argument: for a
  `b`-typed argument and a string-flavor device, the typespec character
  becomes `s` and the value becomes JSON text (dict or list-of-non-ints)
  or base64 (bytes, or a list of ints). This mirrors `to_o2_arg`'s existing
  two-way blob rule exactly, so the choice of JSON-versus-base64 cannot
  drift between flavors.
- `FakeServer` (`tests/test_devicelink_agent.py`) accepts the keyword;
  `FakeO2Lite` needs no change, the flavor is visible in `fake.sent`.
- Nothing in `control/` changes. `devicelink/protocol.py` stays the
  vocabulary's source of truth and documents the flavor table.

## 4. Serving the page

### 4.1 The static server

`harness/www_server.py`: a `ThreadingHTTPServer` with
`SimpleHTTPRequestHandler` rooted at the repo's `www/` (the same tree Arco
serves), because Python's `mimetypes` already labels `.wasm`
`application/wasm` and `.js` `text/javascript`. It binds `0.0.0.0` (guests
are on the venue LAN; the tree is static files with no state, so the
Console's trust model does not apply) on **port 8788** by default, a new
documented constant `WWW_PORT`, flag `--www-port` on `terrarium_boot` and
`run_stack`, `0` disables it. It starts before Control's transport and is
pushed on the process-level teardown stack.

It prints one line: `WWW_URL: http://<lan-ip>:8788/` where `<lan-ip>` is the
first non-loopback IPv4 address (`netifaces`, already a dependency through
o2litepy; loopback fallback with a warning). `run_stack --open` treats
`WWW_URL` like `BROWSE_URL`. `harness/markers.py` gains `WWW_URL`.

### 4.2 The build

The Flutter web build lives at `www/app/` (gitignored). mm-tuneshroom's
`tool/sim build` produces `build/web/`; `run_stack --web-build DIR` copies
that directory into `www/app/` at startup (replacing what was there), so
the venue box never needs Flutter installed. `www/README.md` documents the
copy.

Arco still serves `www/` on 8080. A browser never needs to fetch from
there; it exists for o2ws and the clock-check page.

## 5. The QR URL and device identity

```
http://<lan-ip>:8788/app/index.html?node=TEST_PLAYER_NODE&ens=arco
```

`lib/sim/join_params.dart` resolves, in this order: query parameter, then
`--dart-define`, then default.

| Parameter | Meaning | Default |
|---|---|---|
| `node` | registration node to join | none; without it the start screen shows, as today |
| `ens` | O2 ensemble name | `arco` |
| `o2ws` | host:port of the o2ws endpoint | page hostname, port 8080 |
| `dev` | device id, pins the service name | minted: `ie-` plus six lowercase base32 characters from `Random.secure()` |
| `link` | the Phase 1 websocket URL | ignored on web; kept for the native build until Phase 3 |

Minting per page load is deliberate: many phones scan the same poster, and
O2 gives the loser of a service-name race no error. After
`o2ws_set_services(dev)` the page proves it owns the name with a
self-addressed round trip on `/<dev>/_svcheck` (the same check
`harness/o2_shroom.py`'s `service_conflict` runs), mints a fresh id and
retries once on failure, and shows an error on a second failure.

## 6. The browser link

### 6.1 Files (mm-tuneshroom)

| File | Responsibility |
|---|---|
| `lib/link/o2ws_codec.dart` (new, pure Dart) | `Envelope` to o2ws call arguments and back; the flavor normalization: `role`/`room` `s` JSON parsed into the `Map` `SimController` already reads, `leds` base64 decoded to the `List<int>` it already reads. Rejects a string containing `ETX`. Testable with plain `flutter test`. |
| `lib/link/o2ws_bridge.dart` (new, web only) | Thin `dart:js_interop` adapter over the `o2ws_*` globals: initialize, set services, register a handler, send, time, sync flag, close. No logic. |
| `lib/link/o2ws_link.dart` (new) | `O2wsLink implements DeviceLink` composed of the two above. Conditional import keeps `dart:js_interop` out of the native build; `lib/sim_main.dart` picks `O2wsLink` on web and keeps `WebSocketLink` elsewhere until Phase 3. |
| `web/index.html` | loads `o2ws.js` before the Flutter bootstrap. The file is copied from the `o2` repo's `test/www/o2WebMonitor/o2ws.js` (commit d4dc921; the newest copy, with the `host` argument and the due-timestamp delivery fix) and its provenance recorded in the file header. |
| `lib/sim/join_params.dart` | `ens`, `o2ws`, minted `dev` (section 5). |
| `lib/sim/sim_controller.dart` | hello's protoversion token becomes `o2ws/1` when the link is `O2wsLink` (the link exposes `wireFlavor`). Nothing else. |

Nothing above `lib/link/` imports a platform library; that rule stands.

### 6.2 Session

1. `o2ws_initialize(ens, host)`; wait for `o2ws_clock_synchronized` (bounded,
   ten seconds, then `SimPhase.lost` with the reason on screen).
2. `o2ws_set_services(dev)`; register one handler per `/<dev>/*` verb the
   app handles (`role`, `deny`, `leds`, `play`, `room`, `release`, `error`,
   and `_svcheck`) with typespec `null`.
3. Ownership round trip (section 5).
4. `/game/hello` immediately and every five seconds, as today; `/game/join`
   when the node is known.
5. Outbound `Envelope`s go through `o2ws_send_cmd` (the `T` flag). Gesture
   envelopes carry `o2ws_time_get()` as the message time.
6. Inbound handlers pull arguments by the message's typespec through the
   bridge and hand the codec an `Envelope` whose `timestamp` is the o2ws
   message timestamp.

### 6.3 Timing

No queue in the browser. `o2ws.js` (this copy) holds a message whose
timestamp is in the future and delivers it at that time, so a `leds` frame
reaches its handler at `when`. The app draws on arrival, as it does today.
Control is unchanged and keeps computing `at = origin + cue_horizon`.

### 6.4 Failure

- Websocket closed: `SimPhase.lost`, existing reload affordance. No
  automatic reconnect in this phase.
- `deny` and `error`: as today.
- A browser that goes silent is reaped by Control's existing stale timeout.

## 7. Testing

**mm-terrarium, offline (the suite stays free of o2litepy, network, Arco):**

- `tests/test_o2_transport.py`: for a device bound with `protoversion="o2ws/1"`,
  `role`/`room` go out as `s` with the identical JSON text, `leds` as `s`
  base64 of the same bytes with the same timestamp, `deny` unchanged; a
  blob-flavor device is byte-identical to today; a string containing `ETX`
  is refused; `drop_dev` forgets the flavor.
- `tests/test_devicelink_agent.py`: hello's protoversion reaches
  `bind_dev`.
- `tests/test_www_server.py`: serves `www/index.htm` and a `.wasm` and
  `.js` fixture with the right `Content-Type`, refuses `..`, binds an
  ephemeral port in tests, prints the `WWW_URL` marker.
- `tests/test_run_stack.py`: `--web-build DIR` copies into `www/app/`;
  `--www-port` is forwarded.

**mm-tuneshroom, `flutter test`:**

- Codec round trips for every verb in both directions, flavor
  normalization for `role`/`room`/`leds`, `ETX` refusal, and the minted `dev`
  shape. `O2wsBridge` is not unit-tested (it is JS glue); a fake bridge
  drives the link tests.

**Probes, first tasks of the plan (RUN ON: MYCOLOGICAL):**

- **P7, cross-origin o2ws.** A page served from port 8788 calls
  `o2ws_initialize("arco", "<lan-ip>:8080")` and clock-syncs. Expected to
  pass: browsers apply no cross-origin rule to websocket opens. If it
  fails, fall back to Arco serving the page and add a probe for `.wasm`
  under `text/html`.
- **P8, timed delivery.** From the same page, register a handler for a
  test address, have Control (or `o2ws_send` from a second tab) send it
  with a timestamp 200 ms ahead, and log `o2ws_time_get()` at delivery
  minus the timestamp over a hundred messages. Expected within a few
  milliseconds. If not, the browser needs its own timed queue, and this
  spec's section 6.3 is revised.

**Live gate (from the migration spec, made concrete):** a phone on the
venue LAN scans the poster, lands joined in a scored role during SETUP,
receives its role, renders frames at their `when` with p50 and p99 lateness
read from the page's console, plays the same Bit alongside a Python
Testshroom on the same hub, and ends with the closing fade on release.

## 8. Constraints carried from Phase 1

- Distinct service names per live process; the browser self-verifies.
- First send after announcing goes over TCP; o2ws is TCP throughout.
- Control resolves o2litepy and starts its transport as Phase 1 left it;
  this phase adds a static server beside it, nothing on the o2lite path.
- `www/` is served by two servers; only files under it are reachable and
  neither follows `..`.

## 9. Open questions settled here

- **Why not upstream blob support in o2ws?** It would need both `o2ws.js`
  and `websock.cpp` changed by Roger and would still carry base64 inside a
  text frame; the per-device flavor gives the same bytes on the wire
  without the dependency. If o2ws grows a blob type later, the flavor rule
  collapses to "everyone gets `b`" in one place.
- **Why not make strings the contract for everyone?** It reopens the frozen
  wire and adds a base64 decode per LED frame on the ESP32 for no benefit.
- **Why a Terrarium static server rather than the Console?** The Console is
  the operator's admin surface, loopback by default with no auth. Guests'
  phones must never be pointed at it.

## 10. Work split

| Half | Repo | Pieces |
|---|---|---|
| Control side | mm-terrarium | flavor in `O2LiteTransport.send` + `bind_dev(protoversion=)`; `harness/www_server.py`; `--www-port`, `--web-build`, `WWW_URL`; `www/README.md`, deep-dive, protocol docstring |
| Browser side | mm-tuneshroom | `o2ws_codec.dart`, `o2ws_bridge.dart`, `o2ws_link.dart`, `web/index.html` + `o2ws.js`, `join_params.dart`, hello token, tests |

Either half can be built and tested offline on its own. The Control side
lands first; probe P7 and P8 need only Phase 1's `run_stack` and a hand
page. Assignee for each half is decided when the plan is written.
