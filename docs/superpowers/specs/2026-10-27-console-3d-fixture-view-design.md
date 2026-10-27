# Console 3D fixture view: design

Date: 2026-10-27. Status: draft, awaiting Chris's approval.
Replaces, for now, mm-tuneshroom's "PR B" (the 3D view in the phone
simulator, `lib/view3d/`, spec
`docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md` in
mm-tuneshroom). That PR is parked, not cancelled.

## 1. Intent

A developer writing a Bit needs to see **where on the physical fixture each
LED they address actually sits**. Today the Terrarium Console's Room card
draws a fixture as a flat row of dots (`console/static/surface.js`): on the
MIDITEST Tower, `tower.middle` is dots 3 and 4, and nothing says whether
those are halfway up the mast, on the far side, or next to the responders.
So a developer cannot tell whether a chase climbs the Tower, whether two
zones sit next to each other, or whether a flash lands somewhere anyone will
see it.

This view puts each fixture's 3D model in the Console, lit live at the true
position of every LED, beside the gesture panel the developer already uses
to drive their Bit, and lets them click an LED to see its address, its RGBW
value and how it has responded to recent input.

## 2. Who and when

A Bit developer at their own machine, running a Bit against a Room through
`./smoke-test.sh` or `./terrarium.sh` with the Console up, driving simulated
devices from the gesture panel (or real devices) and watching how the Room's
fixtures respond. The motivating run is Deux Ara Drop on MIDITEST.

## 3. Goals

1. **Spatial placement.** Every fixture in the loaded Room whose instrument
   declares a model is drawn as that 3D model, with each LED shown as a
   clearly lit point at its marker's true position, coloured live from the
   frames the Console already receives.
2. **Several fixtures.** A Room with several fixtures shows one 3D tile per
   modelled fixture in the Room card. A fixture with no model keeps today's
   flat strip, so mixed Rooms (Towers plus `bars`, say) still load and show.
3. **Inspect an LED.** Clicking an LED (on any tile) selects it; one
   inspector panel for the Room shows:
   - its **address**: fixture name, pixel index, the Room zone(s) covering
     it, and the model zone the marker carries;
   - its **live RGBW** values (the four wire channels, not the blended
     display colour);
   - a **trace of the last 10 s** of its R, G, B and W, with every input
     gesture marked on the time axis (verb and sending device), so the
     developer can see "I tapped, and then this LED went green".
4. **Bump-out.** Each modelled fixture's tile has the ↗ bump-out link, which
   opens that fixture's 3D view full-window in its own browser tab, with the
   same inspector.
5. **Orbit.** The model can be rotated and zoomed with the mouse so LEDs on
   the far side can be seen and picked.

## 4. Non-goals

- **Light glow / show-quality preview.** No baked light maps, no light
  spilling across surfaces. A lit point per LED is enough (Chris,
  2026-10-27). The bake pipeline (`tools/bake_model.py`) stays as is and is
  not a dependency of this view.
- **Room layout.** Fixtures are not placed relative to each other in a room
  scene; each fixture is shown on its own. Room TOMLs carry no position data
  and this work adds none.
- **Cue attribution.** The trace shows inputs and the LED's response, not
  which cue, generator or session produced each change.
- **The phone simulator.** No 3D view in mm-tuneshroom in this work.
- **Model authoring or editing** of any kind.

## 5. Anti-goals (failure even if it works)

- **The Console needing a build step, npm, or internet** on a venue box.
  Today `console/static/` ships as plain ES modules with no build; that must
  stay true.
- **The 3D view feeding back into gameplay.** It is display only: nothing in
  Control may wait on it, and a slow or absent browser must not change
  timing (boundary rule 2, `console/agent.py`'s frame decimation comment).
- **A Room that loaded before failing to load or show** because a model is
  missing, large or broken. Fall back to the flat strip and say why.

## 6. Decided by Chris

- Lives in the **Terrarium Console's Live tab**, inside the Room card
  (mockup "layout A"), with the inspector beside the 3D tiles and the
  gesture panel still below, so the developer can drive and watch on one
  screen.
- One tile per modelled fixture; unmodelled fixtures keep the flat strip.
- Lit points at true LED positions; no glow.
- Inspector content: address, RGBW, the lighter 10 s trace with input
  markers.
- The existing ↗ bump-out is reused for a full-window per-fixture 3D view.
- No room-scale placement at this stage.
- This replaces mm-tuneshroom PR B for now.
- **three.js is committed into the repo as a pinned copy** (under
  `console/static/`, e.g. `console/static/vendor/`), loaded as a plain ES
  module. No npm, no CDN.

## 7. What exists today (facts the builder needs)

- **LED positions are already parsed server-side.**
  `control/model_layout.py`'s `PixelLayout` gives each pixel `index`,
  `x_mm`/`y_mm`/`z_mm`, `size` (`small|medium|large`) and `zone`; a loaded
  `Instrument` carries `layout` (a tuple of them) and `model_sha256`
  (`control/instrument*.py`). The instrument TOML names the model, e.g.
  `instruments/tower.toml` and `instruments/tower_sound.toml`:
  `model = "models/tower.glb"`, resolved relative to `instruments/`, so the
  file is `instruments/models/tower.glb` (the source model, 7 MB, 242k
  triangles; the `.baked.glb` beside it is not needed here). The path is
  resolved in `terrarium_config.py`, which refuses one escaping the
  instruments base; `Instrument` does not keep the resolved path today.
- **A layout whose pixel count disagrees with its fixture cannot load**:
  `room_profile`'s `fixture_instrument_mismatch` and the model parser refuse
  it at Room load.
- **Room frames already reach the Console**, per fixture name, bound or
  not: `ConsoleFrameSink` (`control/fixture_sink.py`) feeds
  `console/agent.py`, which broadcasts `room_frame` events
  (`console/protocol.py:room_frame_event`, `channels` = the RGBW int list)
  **decimated to 10 Hz, latest-wins** (`ROOM_FRAME_INTERVAL = 0.1`).
  `console/static/pixels.js`'s `decodeRgbw` is the one decoder (W added onto
  RGB for display).
- **The Room card's structure** comes from `control/room_view.py`'s `room`
  payload (fixtures, blocks, zones); extend that payload rather than
  loosening any Console filter (deep-dive, *Shown by addition*).
- **Gestures.** Console-simulated gestures enter through
  `ConsoleAgent._handle_gesture_command` (`console/agent.py`), delivered as
  a real device's `/game/<verb>` would be. Real device gestures enter
  `GameServer.data(dev, verb, args, gesture_time)` (`control/engine.py`).
  **There is no observer hook for gestures today**, so the Console does not
  see real devices' inputs; one is needed for the trace markers.
- **The Console's HTTP server** (`console/server.py`) serves only files under
  `console/static/`, **by basename only**, read into memory once at
  construction, with an extension allowlist (`.html .css .js .ttf`). It has
  no route for model files.
- **The current ↗** (`surface.js` `bindingControls`) appears only on a
  bound fixture with a device canvas URL, and opens that device's own
  canvas (e.g. a Testshroom's websim "Watch the Shroom" page).
- JS tests run with `node --test tests/js/*.test.js`; Python with
  `.venv/bin/python -m pytest tests`.

## 8. Calls I made (Claude), for Chris to check

1. **Positions come from the server, not from the browser parsing markers.**
   The browser loads the `.glb` only for the visible mesh. Reason:
   `control/model_layout.py` is already the one marker parser
   (mm-tuneshroom's spec decision D13 says the same), and two parsers would
   drift.
2. **Layout and model travel outside the `room` payload.** The `room`
   payload gains only, per fixture, `model_sha256` (or null). The client
   fetches each modelled fixture's layout once, as JSON (index,
   `x_mm/y_mm/z_mm`, `size`, model zone, Room zones covering it), and its
   `.glb`, from new Console routes keyed by **fixture name in the loaded
   Room**, never by a client-supplied path, and caches both by
   `model_sha256`. Only fixtures of the loaded Room are served; anything
   else is a 404. Reason: `room` is rebroadcast on every state change, and
   the static server's design must not be widened into a file server.
   `Instrument` gains the resolved source model path so the route needs no
   re-resolution. Instruments that come from the design-catalog fallback
   (no Terrarium config) are served only if they carry a layout; otherwise
   the tile falls back.
3. **Vendored three.js is served by exact relative path.** At construction
   the server walks `console/static/vendor/` and records each allowlisted
   file under its path relative to `static/` (e.g.
   `vendor/three/addons/loaders/GLTFLoader.js`); a request is looked up by
   that exact string, never joined onto a directory. Everything else keeps
   today's basename rule. Reason: three's addons import each other by
   relative path and share basenames, so flattening breaks them, and an
   exact-string map keeps traversal impossible. Tests cover a `../`
   request, an encoded one, and a basename collision.
4. **A new client gets a snapshot.** On connect (and on `room` change)
   `ConsoleAgent` sends the latest full frame per fixture, so a reloaded
   Live tab or a fresh bump-out shows the current state, not dark LEDs.
   Reason: `room_frame` is sent only when a fixture's frame changes.
5. **The ↗ on a modelled fixture opens the 3D bump-out; the device canvas
   link moves** to the bound-device chip, which becomes a plain link (Release
   and Arm stay separate buttons). The 3D ↗ shows whether or not a device is
   bound, Art-Net fixtures included. Reason: Chris wants the bump-out reused
   for 3D, and the canvas link is still useful for simulated devices.
6. **The bump-out is a second page served by the Console** (e.g.
   `fixture3d.html?fixture=<name>`) that opens its own websocket and
   listens to the same events. Reason: a separate tab cannot share the Live
   tab's JS state, and the events are already broadcast to every client.
7. **One time axis: Control's clock.** `room_frame` events and the new
   gesture events each carry `t`, Control's clock when the frame was
   rendered or the gesture received. The trace plots on `t`, step-held
   (each value lasts until the next change, since only changes are sent),
   over the last 10 s, at the existing 10 Hz decimation. The inspector
   states the resolution. Reason: one clock means the tap and the LED's
   change line up without guessing at browser-to-server offsets; 10 Hz is
   enough to see "tap, then it changed". A later step can raise the rate
   for the selected fixture if developers need finer timing.
8. **Gesture markers come from a new engine observer hook**, notified from
   `GameServer.data()` once the device is resolved and a Bit is loaded
   (solo-role gestures included), carrying `dev`, `verb`, the receipt time
   and whether the gesture was refused (with the reason). The pre-Bit and
   unregistered-device refusals do not fire it. The marker is drawn at
   receipt; its tooltip also gives the cue time `at` (receipt plus the
   Bit's `cue_horizon`), which is when the response is scheduled to show.
   `ConsoleAgent` buffers these in a bounded deque and broadcasts them as a
   droppable event from its own loop. Reason: one path covers real and
   simulated devices, and the hook does nothing on the calling thread but
   an append, so it can never hold up Control. The builder must confirm
   which thread(s) call `data()` and use a lock if more than one does.
9. **Fallback:** a fixture whose instrument has no model, whose model fails
   to load, or whose source model exceeds the artist budget
   (`docs/instrument-model-guide.md` in mm-tuneshroom: 300k triangles,
   20 MB) shows the flat strip, with a one-line reason on the tile. A
   browser without WebGL2 shows strips everywhere. (A layout-count mismatch
   cannot reach the Console; see section 7.)
8. **LED rendering:** an unlit LED is a dim outlined point (matching the
   strip's look); a lit one is a solid point in its display colour, sized
   from the marker's `size`, drawn so it stays visible through the mesh
   (e.g. a slightly transparent mesh, or points drawn without depth test).
   The selected LED gets a ring.
9. **Camera:** each tile frames its model on load from the model's bounding
   box; orbit and zoom by mouse (three.js `OrbitControls`).

## 9. Left to the builder

- three.js version (current stable at build time, pinned, with its licence
  file committed beside it) and which addons (`GLTFLoader`,
  `OrbitControls`); how bare `three` imports resolve without a bundler (an
  import map in `index.html` is the obvious route).
- Whether the Console asset loader needs to serve the vendor directory by a
  path rather than a basename; keep the allowlist idea.
- Event and route names, the exact payload shape, and where the inspector's
  trace is drawn (canvas is fine).
- Tile sizing and how many tiles fit before the Room card scrolls.
- Model loading should not block the Console's first paint: show the strip
  until the mesh is ready.

## 10. Done means

- Running `./smoke-test.sh --bit DeuxAraDropBit --handshake-devices 1
  --console-port 8772`, the Room card shows the Tower in 3D with its 14
  LEDs at their model positions, lighting in step with the strip; tapping
  in the gesture panel turns the right third of the mast green.
- Clicking an LED shows its fixture, pixel, zones, RGBW and a 10 s trace
  with the tap marked.
- ↗ opens the same Tower full-window with a working inspector.
- A Room with an unmodelled fixture (e.g. DEMO's `array`) still shows that
  fixture's strip.
- Python and JS test suites pass; the deep-dive (`docs/MM_TERRARIUM.md`,
  Console section) describes the view.
- mm-tuneshroom's PR B is marked parked in that repo's CLAUDE.md, pointing
  here (a small follow-up in that repo).
