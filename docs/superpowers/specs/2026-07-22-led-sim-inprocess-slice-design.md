# Slice 1 — In-process Bit → Lux Aeterna → Web LED Simulator

**Date:** 2026-07-22
**Status:** Design approved (brainstorm), pending spec review → implementation plan
**Repos touched:** `luxaeterna` (new `WebSimBackend` + tiny MIDI-inject seam), `mm-terrarium` (new integration harness + luxaeterna dev-dependency). Spec lives in `mm-terrarium` as the driving repo; the luxaeterna half lands via its own PR in that repo.

---

## 1. Why this slice exists

Today the Musical Mycology "full connection stack" is really **three pieces that do not touch each other**:

1. **mm-terrarium Control+GameServer** — the real Bit lifecycle engine. `TestBit` already declares a real light-manifest **v2** block plus a `welcome` pair; `compose_role_config` builds the `/ie<N>/role` config blob at grant time and surfaces it on `JoinResult.config`. **But there is no device wire at all** — no o2lite/pyarco dependency, no `control/o2lite_transport.py`, no `dev→N` id assignment, no inbound `/game/*` parser, `on_release` is an unset slot, and nothing calls `hello`/`join`/`tick` outside tests. There is not even a *fake* device transport (unlike uplink/console, which each have one).

2. **mm-tuneshroom `www/` + `harness/`** — a working o2lite-over-websocket round-trip (browser sim `www/o2ws.js` + a canvas LED renderer `www/leds.js`, driven by hand-rolled per-Bit Python harnesses over o2litepy). These are **not** the Control+GameServer engine, they use the **older `/te` + `/sh<pid>` vocabulary** (not `/game` + `/ie<N>` + `/light/midi`), and `www/leds.js` is a dumb per-message painter that **does not understand light-manifest v2**. Its `CLAUDE.md` is stale (documents the dormant Flutter/native path, ignores `www/`+`harness/`).

3. **luxaeterna** — the real renderer: `LightManifest.from_dict` (v2), `StatusDirector` lifecycle, `LightEngine` → `Universe` → `DMXBackend` (Art-Net/sACN/ENTTEC) at 44 Hz, `shroom_capability()` (12px ring+stem, GRB), plus `FakeBackend`/`FakeO2Lite` test doubles. **But there is no *visual* LED simulator** (only numeric `Universe.get_frame()` byte assertions), and luxaeterna is **Python that runs on the device** (the real Radxa Tuneshroom runs it locally).

The freshly-frozen seam — the light-manifest v2 config blob that mm-terrarium composes and luxaeterna parses — has **never actually been run end-to-end between the two repos.** It is a contract asserted independently on each side.

**This slice makes that seam real and watchable, in one process, with the least new infrastructure — and delivers a reusable LED simulator as a first-class luxaeterna feature.**

## 2. Goal & success criteria

Run one command; a browser tab shows a 12-pixel Shroom (8-LED ring + 4-LED stem). Loading `TestBit` produces this visible sequence:

- idle breathing →
- **welcome** bloom flash (from the manifest's `welcome` half) →
- RUNNING; a harness-injected **note-on + `cc:74` sweep** makes bloom visibly trigger and its hue sweep →
- completion → **closing fade** → idle breathing.

Success is met when:

1. A **headless** integration test (no browser, no hardware) drives the whole in-process pipeline and asserts, off the recording backend: welcome frames present → bloom-trigger frames after the note-on → hue sweep across the `cc:74` ramp → closing fade → idle. This becomes the **in-process full-stack regression**.
2. The luxaeterna `WebSimBackend` has its own headless unit tests (frame recording, capability handshake, GRB decode).
3. Watching in a browser shows the sequence above, live at ~44 Hz.
4. The `light_manifest` consumed by luxaeterna is **byte-for-byte the blob mm-terrarium composes** (`JoinResult.config["light_manifest"]`), not a hand-copied fixture.

## 3. Non-goals (explicit scope boundary)

Not in this slice — named in §9 as later work:

- No o2lite, no o2host, no O2 network, no `WebSocketTransport`-style device wire, no `/ie<N>` addressing over a socket.
- No real device (browser Tuneshroom or Radxa), no sensor→MIDI input path, no vocab convergence.
- No Arco audio, no ugen graph, no welcome **audio** half (Control-side only; never ships to a device).
- No scoring, no uplink/console changes.
- No change to the `o2`/`o2litepy` absolute-path dependencies in mm-tuneshroom (only relevant once we build the wire).

"Full connection stack" in *this* slice means the **render + contract** path end-to-end, in one process. The wire comes next.

## 4. Architecture

```
┌─ mm-terrarium (harness/) ───────────────────────────────────────────┐
│ GameServer(TestBit)                                                  │
│   load_bit → join(TEST_PLAYER_NODE) → compose_role_config           │
│      → JoinResult.config["light_manifest"]  (v2 + welcome + prov.)   │
│                                                                      │
│ DeviceBridge (new):                                                  │
│   on grant  → LightManifest.from_dict(config) → build_session()      │
│              (initial swap already enqueued → welcome→RUNNING)       │
│   on release→ session.clear()                                        │
│                                                                      │
│ CannedMidi injector → session.feed_midi(...)  during RUNNING         │
│ main loop: GameServer.tick(dt) @ ~44 Hz                              │
└───────────────┬──────────────────────────────────────────────────────┘
                │  (in-process Python calls — NO wire)
┌─ luxaeterna ──▼──────────────────────────────────────────────────────┐
│ LightSession (StatusDirector + LightEngine)                          │
│   OutputLoop(universe, WebSimBackend, on_frame=session.render_into)  │
│      render_into(Universe) @ 44 Hz → DMX bytes                       │
│         → WebSimBackend.send(frame)                                   │
│              ├─ record frame (.frames — assertion seam)              │
│              └─ stream frame over ws → browser <canvas>              │
└──────────────────────────────────────────────────────────────────────┘
```

**Cross-repo dependency direction:** mm-terrarium (dev/test) → luxaeterna. This is the **first code coupling** between the two, in the sane direction: the venue server depends on the renderer, never the reverse.

**Branch/housekeeping (load-bearing):** the luxaeterna working tree is checked out at stale **v1** (`lightarco-engine` @ `cea56e9`); all v2 work is on `origin/main`. The `WebSimBackend` work **must be based on `origin/main`** (or its fast-follow `claude/bounded-midi-drain`). Confirm the checkout before starting.

## 5. Component design

### 5.1 luxaeterna — `WebSimBackend(DMXBackend)` *(new: `luxaeterna/backends/websim.py`)*

A `DMXBackend` subclass that is both a **recorder** and a **streamer**:

- `send(frame, universe_id)`:
  - appends `bytes(frame)` to `self.frames` (the assertion seam, mirroring `tests/test_output_hook.py::FakeBackend`);
  - if a browser is connected, pushes the raw DMX bytes to all clients over websocket. A dead/slow client is dropped without blocking others (same fan-out discipline as `console/server.py`).
- `open()`: starts a `websockets` server (configurable host/port; default `127.0.0.1`, LAN opt-in). Serves a **self-contained** canvas page on `GET /` and upgrades `/ws` (same single-port `process_request` pattern the mm-terrarium console uses). On each client connect it first sends a one-shot **capability** message — `{pixel_count, zones: {ring, stem}, color_order}` derived from the `SurfaceCapability` it was constructed with (default `shroom_capability()`) — so the page knows the geometry before frames arrive.
- `close()`: stops the server; `is_open` reflects state.
- **Record-only mode:** constructing with `serve=False` (or port `None`) skips the server entirely — the backend still records frames. This is the mode CI/tests use (no port binding, no browser).
- Dependency: gated behind an optional `websim` extra in `pyproject.toml` (pulls `websockets`); luxaeterna core stays dependency-light. luxaeterna does not currently depend on `websockets`.
- Exceptions in `send`/server callbacks are isolated from the render loop — luxaeterna's `OutputLoop` already isolates `on_frame`/backend faults, but `WebSimBackend` must not let a browser hiccup raise into `send`.

### 5.2 luxaeterna — the wire page *(embedded static asset served by `WebSimBackend`)*

A single self-contained HTML/JS/CSS page (inline; no external deps), porting the **`www/leds.js`** shroom geometry (8-dot ring + 4-dot stem, radial-gradient glow). It:

- opens a websocket to `/ws`;
- reads the capability message → lays out `pixel_count` dots per the ring/stem zones;
- per incoming DMX frame, decodes each pixel's bytes per `color_order` (e.g. GRB) → css color → paints; redraws at frame cadence.

Reusing the tuneshroom aesthetic keeps the sim visually consistent with the real instrument UI and is the on-ramp to the browser-device slice.

### 5.3 luxaeterna — MIDI-inject seam *(small addition)*

The harness needs to inject MIDI without a live o2lite client. `FakeO2Lite` lives in luxaeterna's **tests**, not its package, so it is not importable by the harness. Add a minimal **public** seam so callers (this harness, and the future Python device sim) can feed MIDI cleanly:

- **Recommended:** `LightSession.feed_midi(status, data1, data2)` — a few lines that enqueue onto the same path `_bridge.on_midi` uses (packed int32 → decode → dispatch, gated to RUNNING like all MIDI). Smallest clean seam, directly serves the sim use case.
- Alternative considered: promote a `FakeO2Lite`-style client to a public `luxaeterna.testing`/`luxaeterna.sim` module. Broader, more scope; deferred unless the plan finds `feed_midi` insufficient.

### 5.4 mm-terrarium — the integration harness *(new: `harness/led_smoke.py` + `harness/device_bridge.py`)*

- **`DeviceBridge`** — the in-process stand-in for "what the device does with `/ie<N>/role`." It registers as a `GameServer` observer (or is called by the harness on the grant result) and:
  - on a **granted join**: `LightManifest.from_dict(join_result.config["light_manifest"])` → `build_session(manifest, shroom_capability())` → hold the session. `build_session` **already enqueues the initial swap**, so luxaeterna plays welcome → RUNNING on its own — the bridge does *not* call `swap` again. (A role *switch* — a later re-grant with a different manifest — is what calls `session.swap(new_manifest)`; not exercised in this slice.)
  - on **release** (bit complete/unload → `on_release`): `session.clear()` → luxaeterna CLOSING fade → IDLE.
- **`led_smoke.py`** — the runnable demo. Constructs `GameServer(TestBit())`, a `Universe`, a `WebSimBackend(serve=True)`, and an `OutputLoop(universe, backend, on_frame=session.render_into)`; starts the loop; then runs the scripted scenario (§6); prints where to point a browser.
- **Canned-MIDI injector** — a tiny timed script `[(t_seconds, (status, d1, d2)), …]` played via `session.feed_midi(...)` during RUNNING: one note-on, then a `cc:74` ramp 0→127, then note-off.

The harness lives under `harness/` for cross-repo idiom consistency with mm-tuneshroom; the Slice-2 Python device sim will grow alongside it. (Adjustable — `sim/` is an alternative package name.)

## 6. Data flow / the scripted scenario

```
TestBit.role_table
  → GameServer.load_bit()            IDLE→…→SETUP
  → GameServer.join(dev, TEST_PLAYER_NODE)   grant "player"
       → compose_role_config → JoinResult.config["light_manifest"]  (v2)
  → DeviceBridge: LightManifest.from_dict → build_session(+shroom_capability)
       (initial swap enqueued by build_session)  luxaeterna: IDLE→LOADING(welcome flash)→RUNNING
  → GameServer.run()                 SETUP→RUNNING
  → main loop @44 Hz: GameServer.tick(dt)
       t=0.5s  feed_midi(note-on)    → bloom triggers
       t=0.6–1.6s  cc:74 sweep 0→127 → hue sweeps
       t=1.7s  feed_midi(note-off)
  → TestBit auto-completes (fixed duration)  RUNNING→COMPLETING→UNLOADING
       → on_release → DeviceBridge: session.clear()  luxaeterna: CLOSING fade→IDLE
  → idle breathing continues; render thread keeps painting
                (render each tick) → WebSimBackend.send → record + ws → browser
```

Two clocks run concurrently and independently, matching luxaeterna's threading model: the harness main loop drives `GameServer.tick` + MIDI injection (caller thread → thread-safe enqueue), while `OutputLoop`'s daemon thread drains the queue and renders at 44 Hz.

## 7. A contract risk this slice is designed to surface (a feature, not a bug)

`TestBit`'s manifest wires `cc:74 → base_hue` and its welcome uses `params: {base_hue: 0.33}`. luxaeterna's `bloom` preset exposes a hue-ish param, and **an unknown lane `dest` raises at resolve time** (`binding.resolve` validates `dest` against `instrument.param_names()`). If bloom's param is actually `hue` (not `base_hue`), `build_session` on TestBit's manifest will **raise** — which is exactly the cross-repo drift this harness exists to catch. Reconciling the param name (in `TestBit` or in luxaeterna's bloom preset) is **expected in-slice work**; the plan should verify the exact param name on `origin/main` and align one side.

## 8. Error handling & testing

**Error handling**
- Backend/server exceptions stay isolated from the render loop; a browser hiccup never raises into `send` or the tick.
- The harness runs fully **headless** even with no browser connected (record-only path still works; the browser is an optional viewer).
- Manifest parse/resolve errors **fail fast and loud** at harness start (consistent with mm-terrarium's load-time validation ethos: a bad contract must surface immediately, not mid-run).

**Testing**
- **luxaeterna** (headless, no browser): unit tests for `WebSimBackend` — frames recorded in order; capability message shape/content; GRB byte→color decode correctness; `serve=False` record-only path. Extends existing `FakeBackend`/e2e patterns. Plus a test that `feed_midi` reaches an instrument only in RUNNING.
- **mm-terrarium**: `tests/test_led_smoke.py` — runs the §6 pipeline with `WebSimBackend(serve=False)`, no port, no browser, and asserts: (a) welcome frames present in the LOADING window; (b) bloom-trigger frames after the note-on; (c) hue progression across the `cc:74` ramp (e.g. GRB byte ordering shifts green→red as documented in luxaeterna's own e2e test); (d) closing fade toward zero after release; (e) return to idle. This is the durable **in-process full-stack regression**.
- Both preserve the "no hardware, no browser needed in CI" property that both repos already hold.

## 9. Docs & tools to update

**In this slice**
- **luxaeterna:** backends doc / README — document `WebSimBackend` + `pip install luxaeterna[websim]`; optional `python -m luxaeterna.websim_demo` convenience entry point (a canned-manifest viewer, independent of mm-terrarium).
- **mm-terrarium:** `docs/MM_TERRARIUM.md` deep-dive — add the LED-sim harness + the luxaeterna dev-dependency to the picture; keep the "still in-process, no wire" line honest (update *Not yet built / deferred*). Run `mm-deepdive-sync` at closeout.
- The spec + plan under `docs/superpowers/specs/` and `docs/superpowers/plans/`.

**Flagged, out of scope here (queue as follow-ups)**
- **mm-tuneshroom `CLAUDE.md`** is stale (documents the dormant Flutter/native stack, omits `www/`+`harness/` where all recent work lives). Needs a refresh — but not part of this slice.
- The `o2`/`o2litepy` absolute-path dependencies in mm-tuneshroom's harness/build — de-hardcode when we build the wire (Slice 2).

## 10. Roadmap — OUT OF SCOPE for this spec (context only)

This spec covers **Slice 1 only.** Recorded here so the plan does not accidentally pull it in.

- **Slice 2 — the wire + both devices in parallel.** Build `control/o2lite_transport.py` (o2litepy dep, `dev→N` id assignment, inbound `/game/hello|join|tick` → engine calls, outbound `/ie<N>/role|release`, a `main.py` tick loop wiring GameServer + observers together); de-hardcode the `o2`/`o2litepy` paths; stand up o2host/Arco. Then bring up **both** a **headless Python device sim** (runs the same Python luxaeterna + `WebSimBackend` the real Radxa runs — the CI-automatable device that proves the wire without a human phone) **and** the **`www/` browser Tuneshroom** (human testing) against that same wire, converging the `/te`+`/sh<pid>` vocabulary onto canonical `/game`+`/ie<N>`+`/light/midi`. The browser needs **no JS port of luxaeterna** — it remains a luxaeterna-driven display (the Slice-1 `WebSimBackend` view) plus a sensor→MIDI input path.
- **Slice 3 — real hardware bring-up.** A real Radxa Tuneshroom on the wire, luxaeterna driving physical SK6812 LEDs via a real `DMXBackend`.

## 11. Decisions locked (from brainstorm)

- Slice 1 fidelity: **in-process, no O2 wire.**
- LED sim viewer: **web canvas** (`WebSimBackend` in luxaeterna, reusing `www/leds.js` aesthetic).
- Driving Bit: **reuse `TestBit`** + harness-injected canned MIDI (no new Bit).
- LED sim home: **luxaeterna** (a proper backend). Integration harness home: **mm-terrarium** (`harness/`).
- Cross-repo dep: **mm-terrarium → luxaeterna** (dev/test).
- Wired-slice sequencing: **Python sim + browser Tuneshroom both in Slice 2**, in parallel, against one wire.
