# Slice 2 — DeviceLink + the Flutter Tuneshroom Simulator

**Date:** 2026-07-27
**Status:** Design approved (brainstorm), pending spec review → implementation plan
**Repos touched:** `mm-terrarium` (new `devicelink/` package — the device-facing websocket shim), `mm-tuneshroom` (new `web/` platform target, `lib/link/` transport seam, `lib/sim/` simulator). Spec lives in `mm-terrarium` as the driving repo; the mm-tuneshroom half lands via its own PR in that repo.

---

## 1. Why this slice exists

Slice 1 ([`2026-07-22-led-sim-inprocess-slice-design.md`](2026-07-22-led-sim-inprocess-slice-design.md)) proved the light-manifest-v2 seam **in one process**: `harness/device_bridge.py` turns a granted `JoinResult.config` into a real luxaeterna `LightSession`, and `harness/led_smoke.py` watches it render. It deliberately stopped short of a wire.

So today the Control+GameServer still has **no device transport of any kind**. `GameServer.hello()` / `join()` / `tick()` are called only by tests and by `led_smoke.py`, which hardcodes one device (`sim-dev`, `TEST_PLAYER_NODE`). `JoinResult.config` — the composed `/ie<N>/role` blob, complete with provenance and the folded welcome light half — has never once crossed a socket. Neither has a `/game/*` message.

Meanwhile mm-tuneshroom holds two working stacks, **neither of which speaks this architecture**:

- `www/` — a real O2-over-websocket client (`o2ws.js`, 684 lines, with clock sync) plus sensors and a canvas LED painter, all on the **legacy M1a vocabulary** (`/te/join`, `/te/tilt`, `/sh/beat`). `www/leds.js` is a dumb per-message painter with no idea what a light manifest is.
- `lib/` — a Flutter app whose o2lite client is **`dart:ffi` over vendored C** (`native/o2lite/`), iOS-only (`ios/` is the sole platform directory), also on the M1a vocabulary.

This slice gives Control its first device wire, and gives the project a Tuneshroom simulator that speaks the real `/game/*` + `/ie<N>/*` vocabulary.

### The constraint that shaped the design

The goal is **one Flutter codebase targeting all platforms, web first** — native apps only if latency later demands them. That collides with a hard fact: **`dart:ffi` does not exist on Flutter web.** The existing FFI o2lite client cannot run in a browser at all.

This is not a blocker, because the architecture already anticipated it. `docs/control-gameserver-design.md` § *Topology* has browsers reaching Arco over **websockets**, and states outright: *"A phone simulating a Tuneshroom offers `ie<N>` semantics through the same websocket instead."* Web-on-websockets is the sanctioned path.

The consequence for this slice: the Dart side gets a **transport interface** with exactly one implementation today (websocket). The FFI o2lite implementation slots in behind the same interface later, on native, without touching anything above it. That seam is what makes "Flutter everywhere" real rather than aspirational.

## 2. Goal & success criteria

Run the Terrarium with the new shim, open the simulator in a browser, and drive a full player lifecycle over a wire.

Success is met when:

1. **Registration + role grant crosses a socket.** The simulator sends `/game/hello` then `/game/join`; a granted join returns `/ie<N>/role` whose `light_manifest` is **byte-for-byte `JoinResult.config["light_manifest"]`** — not a hand-copied fixture. A denied join returns `/ie<N>/deny` with the engine's own `reason`/`hint`.
2. **The device renders light-manifest v2 from that blob.** The shim builds a per-device `LightSession` from the same blob it sent, and the simulator paints the resulting 12-pixel Shroom (8 ring + 4 stem, GRB).
3. **Sensor input produces real `/game/*` traffic.** Browser orientation/motion becomes `/game/tilt` / `/game/shake` / `/game/tap`, each carrying `dev` **in the arguments** (Design Rule 2).
4. **The loop closes visibly.** Tilting the phone/browser drives `cc:74`, which TestBit's `player` role already binds to `aurora`'s hue lane — so a gesture glides the Shroom's colour. This single gesture exercises criteria 1–3 at once and needs no new Bit.
5. A **headless** integration test drives the whole path over a real local socket and asserts the above without a browser.

## 3. Non-goals (explicit scope boundary)

Named in §12 as later work:

- **No Arco, no o2lite, no o2ws framing.** The shim is a direct Control-side websocket server; Arco is not in the loop. Hop counts are therefore *not* real (see §4).
- **No clock sync, no timing or latency measurement.** Structurally out of reach for this transport, and explicitly not this slice's job.
- **No multi-device contention** — capacity limits on `unique` roles, the scored-denied-but-jam-open RUNNING rule, `DevicePool` survival across Bit lifecycles. The shim must not *prevent* N devices, but proving contention is a later slice.
- **No changes to `www/` or `lib/ffi/`.** Both stay as working references until this stack reproduces their behavior, per the standing policy in `docs/MM_TERRARIUM.md`.
- **No new Bit.** TestBit's existing `player`/`jammer` roles are the fixture.
- **No Arco audio, no ugen graph, no welcome audio half** (Control-side only; never ships to a device).
- **No authentication.** See the trust model in §9.

## 4. Architecture

Two new units, one per repo, plus a platform scaffold.

```
Browser (Flutter web)                mm-terrarium process
+---------------------+              +--------------------------------+
| lib/sim/            |              | devicelink/                    |
|  node picker        |   ws         |  DeviceLinkServer  (transport) |
|  12-LED display     |<------------>|  DeviceLinkAgent   (brains)    |
|  sensor capture     |  envelopes   |    dev -> DeviceBridge         |
+---------------------+              |             -> LightSession    |
| lib/link/           |              +--------------------------------+
|  DeviceLink (iface) |                        | observer list
|  WebSocketLink      |                        v
|  [FfiLink — later]  |              +--------------------------------+
+---------------------+              | GameServer  (unchanged)        |
                                     +--------------------------------+
                                              ^ same observer list
                                     console/ and uplink/ attach here
```

**`devicelink/` is the device-facing sibling of `console/`.** It deliberately mirrors that package's proven structure — a `DeviceLinkServer` (single port, fan-out to *N* clients, drain-based) and a `DeviceLinkAgent` (transport-agnostic brains). It attaches through the **same `GameServer` observer list** that `console/` and `uplink/` use.

**Boundary rule 2 governs it.** DeviceLink is a transport shell, never the hot loop: an exception inside it must never propagate into the engine tick, exactly as already required of its two siblings.

### What is real here and what is not

The **vocabulary** is real: `/game/hello`, `/game/join`, `/ie<N>/role`, the composed config blob, `dev` in arguments. That is what this slice validates.

The **framing** is not: messages are JSON envelopes mirroring o2ws field-for-field — `{timestamp, address, typespec, args}` — rather than Roger's o2ws wire protocol. This was a deliberate choice over an o2ws-faithful shim, because reproducing Arco's server side **with no Arco to validate against** risks building a confidently wrong reference. The envelope carries the right fields, so the later swap to o2ws is mechanical and isolated behind one interface on each side.

Hop counts are consequently **not** the real ones (§ *Message Routing* says device→`/game/*` is 2 hops through Arco; here it is 1). Nothing in this slice may quote a hop count or a latency figure.

## 5. Component design

### `devicelink/server.py` — `DeviceLinkServer`

Modelled directly on `console/server.py`: single-port websocket server with `start()` / `stop()` / `port`, `drain_new_clients()`, `drain_inbound()`, `send(client, msg)`, `broadcast(msg)`. Drained from the engine's tick loop, so no threading discipline is introduced beyond what already exists. A dead or slow client is dropped without blocking others.

### `devicelink/agent.py` — `DeviceLinkAgent`

The brains, local sibling of `ConsoleAgent`:

- `poll()` — drains inbound envelopes, dispatches by address, wrapped so nothing escapes into the tick.
- Inbound: `/game/hello` → `GameServer.hello()`; `/game/join` → `GameServer.join()`; `/game/<verb>` → the engine's new input entry point, which dispatches to `bit.verb_handlers()` (**that route does not exist today — see §7**).
- Outbound: `/ie<N>/role`, `/ie<N>/deny`, `/ie<N>/leds`, `/ie<N>/release`, `/ie<N>/error`.
- Holds `dev → DeviceBridge`, the per-device renderer map.
- Owns the two transport-owned sinks — `on_release` (existing) and the Bit's light-cue sink (§7) — dispatching each per-device.

### `devicelink/protocol.py`

Envelope encode/decode plus address dispatch. Mirrors `uplink/protocol.py`'s dataclass style. Single source of truth for the wire shape, importable by tests on both sides.

### `harness/device_bridge.py` — extended, not rewritten

Today `DeviceBridge` holds one `session`. It already has the right shape (`on_grant(join_result)` → `LightSession`, `on_release(dev)` → `clear()`); the agent simply holds one instance per device. No interface change.

### Frame streaming

The session's `OutputLoop` renders at 44 Hz on its own thread while the engine tick and socket drain run on the main loop. Frames cross that boundary by **enqueue-and-drain, never by blocking the render thread** — the discipline luxaeterna already uses internally for o2lite receives.

Frames go out **on change**, not `always_send=True`. At 44 fps × N devices, a JSON envelope per frame is the one place this design could get silly; LED payloads are a compact array of 12 RGB triples.

### `mm-tuneshroom` — `lib/link/`

`DeviceLink`, an abstract transport: connect, send envelope, stream of inbound envelopes. One implementation now — `WebSocketLink`. The future `FfiLink` (native o2lite) implements the same interface; **nothing above `lib/link/` may import `dart:ffi` or `dart:html`.** That rule is what keeps the codebase buildable for every target.

### `mm-tuneshroom` — `lib/sim/`

The simulator: a node picker (which Registration Node am I tapping?), a 12-pixel Shroom display (8 ring + 4 stem, GRB decode), a sensor capture layer, and a panel showing the parsed manifest with its `bit_name`/`bit_version`/`role` provenance — so a human can see *which* Bit and role produced what is on screen.

### `mm-tuneshroom` — `web/`

Scaffolded via `flutter create --platforms=web .`. Additive; touches no existing source.

## 6. Data flow

1. **Connect** — simulator opens a websocket. The device is anonymous until it says hello.
2. **hello** — `/game/hello (dev, name, protoversion)` → `GameServer.hello()`. Device enters the `DevicePool`, which survives Bit lifecycles.
3. **Operator loads and runs a Bit** through the existing Console, unchanged. State → SETUP, registration open.
4. **join** — `/game/join (dev, node)` → `GameServer.join()` → `JoinResult`.
   - Denied → `/ie<N>/deny (reason, hint)`. Denials are normal traffic, not errors; the simulator shows *why*.
   - Granted → `/ie<N>/role` carrying `JoinResult.config` verbatim. The agent builds this device's `DeviceBridge` → `LightSession` from that same blob.
5. **Render** — the session's 44 Hz loop produces DMX frames; the agent emits `/ie<N>/leds` on change. The simulator paints ring + stem.
6. **Sensor input** — orientation/motion → `/game/tilt`, `/game/shake`, `/game/tap`, `dev` in the arguments. Tilt maps to `cc:74`, which TestBit's `player` role binds to `aurora`'s hue lane.
7. **Release** — Bit completes or Control aborts → `on_release` → per-device `session.clear()` (the closing fade) → `/ie<N>/release`.
8. **Disconnect** — socket drops, device leaves the pool, its session is cleared.

### The demo, end to end

Load TestBit from the Console. Open two browser tabs. Each taps a *different* Registration Node — one gets `player` (scored, `shared`), one gets `jammer` (unscored `jam`, empty light manifest, so the no-light path stays exercised). Run. Tilting the `player` tab glides its Shroom's hue through `aurora`; the `jammer` tab stays dark by design. TestBit auto-completes after ~2 s (or `--hold`), both sessions fade, both tabs return to idle.

Two tabs here is **fan-out, not contention**: they take different roles from different nodes, so no capacity limit is under test. Contention — two devices racing for the last slot of a `unique` role — remains deferred per §3.

## 7. Pre-verified gap: `verb_handlers` is declared but unrouted (confirmed before planning)

Success criterion 3 needs device input to reach a Bit. **Today it cannot.**

`Bit.verb_handlers()` exists in `control/bit.py:60` with a docstring — *"Extra `/game/*` verb handlers this Bit adds, beyond the fixed lifecycle verbs Control always handles"* — and a default `{}`. `tests/test_bit.py:26` asserts that default. But **`GameServer` never calls it.** Grepping `control/`, `bits/`, and `tests/` finds no other reference. There is no dispatch path from a `/game/<verb>` message to a Bit, and `GameServer`'s public API (`hello`, `load_bit`, `run`, `join`, `tick`, `abort`, `add_observer`) has no input entry point at all.

This is a **declared-but-unwired seam**, not a missing design. Wiring it is in scope for this slice, and is small:

1. **`GameServer` gains an input entry point** — a `data(dev, verb, args)`-shaped method that validates the device is registered, looks up `bit.verb_handlers()`, and dispatches. Unknown verbs and unregistered devices are refused with a reason, never raised.
2. **A Bit's verb handler may emit a light cue.** Boundary rule 3 puts Lux Aeterna *downstream of Bit cue logic*, so the tilt → `cc:74` mapping belongs to the Bit, not to DeviceLink. But the Bit has no access to a `LightSession` — DeviceLink owns those. The cue therefore crosses through a **transport-owned sink**, exactly mirroring the existing `on_release` slot that `led_smoke.py` already assigns (`gs.on_release = bridge.on_release`). DeviceLink registers the sink and applies the cue to that device's session via `feed_midi`.
3. **TestBit gains a `tilt` verb handler** mapping tilt to `cc:74`. TestBit is the durable reference fixture, so this also makes verb dispatch a *tested* behavior rather than an assumption — the same reason its scored/jam role pair exists.

Nothing here changes an existing interface: `verb_handlers()` keeps its signature, `on_release` keeps its shape, and Bits that declare no verbs are unaffected.

## 8. Error handling

- **Nothing escapes into the engine tick.** Agent `poll()` is wrapped; a failing client is logged and dropped. Boundary rule 2.
- **Engine errors become envelopes, never exceptions across the wire.** `InvalidTransition` and `BitLoadError` go out as `/ie<N>/error`, the discipline `uplink/` already uses.
- **Protocol violations get a reasoned denial, not a disconnect** — `/game/*` before hello, or a double join.
- **A misbehaving device must never wedge Control**, mirroring the existing rule that a misbehaving Bit cannot.

## 9. Trust model

Identical to the Console, and load-bearing: **trusted-LAN operator, no authentication**, default bind **`127.0.0.1`**, with `0.0.0.0` LAN exposure an explicit opt-in. The moment this faces an untrusted network, auth becomes a prerequisite, not an enhancement. This is the first surface that *devices* connect to rather than operators, so the assumption deserves re-stating rather than inheriting silently.

## 10. Testing

- **Python** — headless tests against a fake websocket client, mirroring the existing console tests: grant and deny paths, byte-identity of the config blob, release → `clear()`, exception isolation from the tick, and N-client fan-out.
- **Verb dispatch (§7)** — its own tests, since the route is new: a declared verb reaches its Bit handler; an unknown verb and an unregistered device are each refused with a reason rather than raising; a Bit declaring no verbs is unaffected. TestBit's `tilt` handler makes this a regression fixture, not a one-off.
- **Dart** — unit tests for the envelope codec and LED frame decode. Plain `flutter test`; no browser, no platform channels.
- **End-to-end** — `harness/devicelink_smoke.py`, sibling to `led_smoke.py`: a real local socket, a real `LightSession`, headless frame assertions, and `--hold` to watch it live.
- **Not tested, by construction:** contention, timing, latency, hop counts.

## 11. Docs to update

- `docs/MM_TERRARIUM.md` — new `devicelink/` subsystem; the *Not yet built* entry for "Real O2lite/pyarco transport wiring" narrows (a device wire now exists; the *o2lite* wire still does not); the `Bit` interface hook list gains `verb_handlers` as **routed** rather than merely declared (§7).
- `mm-tuneshroom/CLAUDE.md` — the new `web/` target, the `lib/link/` transport rule, and the standing note that `www/` and `lib/ffi/` remain legacy references.
- `README.md` planned-layout block — `devicelink/` joins the list.

## 12. Roadmap — OUT OF SCOPE (context only)

1. Swap the JSON envelope for real o2ws framing once an Arco server exists; repoint the simulator at Arco's `/o2ws`.
2. `FfiLink` — the native o2lite implementation behind `DeviceLink`, if latency demands native apps.
3. Multi-device contention as a tested behavior.
4. `arcoserver/` (Arco build config) and the real 2-hop path.
5. The Arco cue path for the welcome **audio** half, which this slice still never ships to a device.

## 13. Decisions locked (from brainstorm)

| Decision | Choice | Why |
|---|---|---|
| What the simulator connects to | Control-side websocket shim | Only option needing nothing that doesn't exist yet; exercises the real seams today |
| Wire framing | JSON envelope mirroring o2ws fields | Vocabulary is what this slice validates; reproducing o2ws with no Arco to check against risks a wrong reference |
| Who renders the light manifest | Shim, with real luxaeterna, streaming frames | Tests the actual renderer and contract rather than a lookalike; a Dart reimplementation of a weeks-old schema would drift silently |
| Fate of `www/` and `lib/ffi/` | Untouched, kept as references | Standing policy in `MM_TERRARIUM.md`; they are the only working sensor/LED reference |
| Flutter web vs. FFI | Transport interface, websocket impl only | `dart:ffi` is unavailable on web; the architecture already routes browsers over websockets |
| Success criteria | Registration + grant, v2 rendering, sensor → `/game/*` | Contention explicitly deferred |
