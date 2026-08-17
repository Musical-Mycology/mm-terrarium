# The Room panel: Room fixtures, and a Room read model for the Console

**Date:** 2026-08-17
**Status:** Implemented and live-verified 2026-08-17. Suite 764 to 844 passed,
1 skipped.

*Verified in a real browser*, against a real `GameServer`/`RoomBridge`/
`ConsoleServer`/`ConsoleAgent` with a synthetic frame source fed through
`ConsoleAgent.on_room_frame` (the same entry point `DeviceLinkAgent` calls), so
no Arco was required: 60 swatches in three zones labelled `left (0..19)` /
`center (20..39)` / `right (40..59)`; a LIGHT card for `aurora` and an AUDIO
card for `flsyn` showing `cc:74 -> hue = 58` and `cc:74 -> cc:74 = 58` at the
same instant, which is the shared-MIDI-stream property made visible; the header
`TEST . 60 px . GRB . bound to sim-room`; and the section 3 hiding holding in a
real browser, with Roles and Registration listing only `player` and `jammer`.
GRB decode confirmed by writing a constant B=40 and reading blue=40 back.

*Two Important defects were found by that browser check and fixed* (`0aad46a`,
`e19f11d`), neither visible to 843 passing tests. `renderRoom` replaced the
`#roomStrip` node on every `room_changed`, and `room_changed` fires on every
controller change, so the strip was rebuilt about four times per painted frame
(measured 1726 `room_changed` against 464 `room_frame`) and the live light was
effectively never displayed while the room was active. And `body` had
`color: #111` with no `background-color`, so under `prefers-color-scheme: dark`
the whole console rendered near-black on black. The first fix then introduced a
sibling-order regression, caught by re-review and fixed in a second round.
`room.js` gained a real behavioral test (`tests/js/room_panel_behavior.test.js`
via Node's `vm`, wrapped by `tests/test_room_panel_behavior.py`, skipping
cleanly where node is absent); before this it had only substring greps over its
own source, which is exactly why these reached a live run.

*Verified against a real Arco* via `python -m harness.run_stack --ci
--seconds 25 --devices 0 --console-port 8772`: the console served at the printed
URL, the Room simulator came up, Arco opened audio at 10 ms latency, and
teardown reaped everything with exit 0. **First attempt of two failed** at the
`control-ready` stage with `verify_service_ownership` reporting the `game`
service already claimed; no orphan was present afterwards, so this is the known
intermittency, and the guard failed loud with a named cause rather than running
dark. **Neither run reached RUNNING** (25 s bounded against a 90 s SETUP hold),
so Room animation on the o2lite path was not observed here; that half was
covered by the browser verification above.

This document is a point-in-time design record, not a living doc. For current
behavior, constraints and known issues read `docs/MM_TERRARIUM.md`.
**Repos touched:** `mm-terrarium` only. No luxaeterna change, no mm-tuneshroom
change, no Arco or pyarco change.
**Scope:** Spec A of two. Spec B (triggers, cue scripts, conditions, and
firing) is a separate document and is not designed here. See section 14.
**Amends:** [`2026-08-10-room-concept-and-load-sequence-design.md`](2026-08-10-room-concept-and-load-sequence-design.md)
section 7, which established "the Room is never surfaced on the Console or
uplink". Section 3 below narrows that rule rather than deleting it.

---

## 1. Why this slice exists

An administrator standing at an installation cannot currently see what the Room
is, what it will do, or whether it is doing it. Three separate reasons, each
independently sufficient:

1. **The Console is never constructed.** `ConsoleServer` and `ConsoleAgent`
   appear only under `tests/`. No harness driver builds either one, so during a
   real run there is no admin panel to open at all.
2. **The Room is deliberately filtered out of every Console view.** The 2026-08-10
   spec's section 7 rule is implemented twice, at `console/agent.py:88` (roles)
   and via `control/rooms.py:98`'s `non_room_counts()` (registration).
3. **The Room has no fixtures of its own to show.** `devicelink/agent.py:153`
   builds the Room's `LightSession` from `self._capability or
   shroom_capability()`, so structurally the Room is a 12-LED Tuneshroom with a
   ring and a stem.

Point 3 is also the substantive half of "draw a cleaner distinction between Room
instruments and Tuneshrooms". Today that distinction is a role class and a naming
convention. After this slice it is a different surface with different zones.

### 1.1 Findings from the code that shaped this design

Established by reading the tree before any option was proposed.

**F1. The Room borrows the Tuneshroom's capability, and so does its simulator.**
`devicelink/agent.py:153` and `harness/room_simulator.py:56` both call
`shroom_capability()`. `DeviceLinkAgent` holds a single `self._capability` that
serves the Room path and the per-player path (`devicelink/agent.py:378`) alike.

**F2. The 36-channel frame width is hardcoded in three places, not one.**
`harness/shroom_client.py:52` declares `LED_CHANNELS = 36` and line 160 rejects
any frame of a different length outright; `devicelink/agent.py:249` slices the
Room's rendered universe with a literal `[:36]`. A Room of any other pixel count
fails at all three.

**F3. This is not the open RGBW decision.** `docs/MM_TERRARIUM.md` records
widening the wire from 36 to 48 channels (12 px x RGBW) as "an open decision,
not a bug to quietly fix", because it is coordinated across `devicelink/protocol.py`,
`shroom_capability`, the WebSim backend and `mm-tuneshroom/lib/link/envelope.dart`.
That decision is about the **Tuneshroom's** white die. Making the **Room** a
different surface is a separate, contained change and does not depend on it.

**F4. `leds_event` is already width-agnostic.** `devicelink/protocol.py:90` does
`list(channels)` with no length assertion; only its docstring says 36. So the
wire needs no change, only the two endpoints that assert a length.

**F5. A luxaeterna `Universe` is 512 DMX channels.** A single-surface Room
therefore caps at 170 px RGB. Anything larger needs `PixelSpan` / `UniverseSet` /
`MultiUniverseOutputLoop`, which luxaeterna already has and `harness/array_smoke.py`
already uses for the 864 px venue array. This slice stays single-universe.

**F6. `SurfaceCapability` already carries named zones, and manifests already
target them.** `Zone(name, start, count)` exists, `SurfaceCapability.zone()`
resolves by name with a `primary` fallback, and every `light_manifest` instrument
already declares a `target`. The labelling axis this slice needs is already in
the data model; nothing consumes it yet.

**F7. Room light and Room audio are declared in two separate fields of one
`Role`.** `light_manifest` and `ugen_manifest` both hang off the Room's
`Role` (`bits/test_bit.py:87-100`), fed from one shared MIDI stream through
`RoomBridge.feed_light` / `feed_audio`. Presenting them as two disconnected
tables would hide the property the whole architecture is built around.

**F8. Both existing transport-owned sinks are guarded on the engine side.**
`_unload` wraps `on_release`, `data()` wraps `on_light_cue`, each with its own
regression test, so a failing transport cannot wedge Control. Any new sink must
follow the same pattern to be consistent.

---

## 2. Goals

1. An administrator can open the Terrarium Console during a real run.
2. The Console shows the Room's declared instruments, light and audio together,
   each labelled with the zone it drives and the controller lanes that feed it.
3. The Console shows the Room's live light output as a labelled zone view.
4. The Room stops being shaped like a Tuneshroom.
5. Everything the 2026-08-10 spec's section 7 protects stays protected.

---

## 3. The section 7 amendment

The original rule exists because the Room's Registration Node grants control of
the installation's rendering backend. Revealing that node id invites an attacker,
or a confused guest, to bind themselves as the Room. That concern is real and is
unchanged by this slice.

But the rule was written broader than the concern. The instruments a Bit declares
for the Room are not a credential; they are exactly what an operator needs to see.
The amended rule:

| Stays hidden | Becomes visible |
|---|---|
| The Room's Registration Node id (`control/rooms.py:44`, `ROOM_NODE_IDS`) | The Room's declared light instruments |
| The Room's registration counts and occupancy | The Room's declared audio instruments |
| The Room's role name inside the player role table | The Room's capability, including zone names |
| Everything above, on the uplink, unconditionally | Live controller values, and the live rendered frame |

**The implementation is addition, not removal.** This is the load-bearing
decision of the whole section. `console/agent.py:88` keeps its `RoleClass.ROOM`
filter verbatim, `_non_room_counts()` keeps calling `control/rooms.py`'s
`non_room_counts()`, and `uplink/link.py` is not edited at all. The Room becomes
visible only through a new, separately built `room` key that is scoped to exactly
the right-hand column above.

Two reasons to prefer addition over relaxing the existing filters. A future
reader cannot accidentally widen an exposure that was never narrowed in the first
place; and the regression test in section 13 can assert both halves at once,
which a relaxed filter makes impossible to state.

The uplink stays untouched because this is local-operator information. A remote
fairyring broker has no reason to learn a venue's fixture layout.

---

## 4. The Room's own fixture model

**Correction, 2026-08-17, made during planning.** This section first specified
`control/room_profile.py` exporting `room_capability(room_type) ->
SurfaceCapability`. That is wrong: no module in `control/` imports luxaeterna,
pyarco or o2litepy **at module level**, and returning a luxaeterna type would
have made this slice the first one. That is what lets every `control/` module be
imported, and the whole suite run, with no renderer installed, so the
declaration is split in two, mirroring how `harness/device_bridge.py` already
adapts Control-side declarations to luxaeterna for player devices.

**Module level is the precise boundary, corrected 2026-08-17 during Task 1.**
An earlier draft of this note claimed `control/` referenced those packages zero
times. It does not: `control/arco_process.py:37` carries a deliberate lazy
`from pyarco.arco_engine import arco`, marked `# noqa: PLC0415 (lazy by
design)`, because probing the Arco subprocess for readiness is that module's
whole job. A function-scoped import runs only when the function is called, so it
preserves the property that matters. The repo states the stricter rule
per-module where it applies, in `control/audio.py`'s docstring ("MUST NOT import
pyarco, at module level or anywhere else"), not package-wide.

**`control/room_profile.py`, pure, no third-party imports:**

```python
@dataclass(frozen=True)
class RoomZone:
    name: str
    start: int
    count: int


@dataclass(frozen=True)
class RoomProfile:
    surface_id: str
    pixel_count: int
    color_order: str
    zones: tuple[RoomZone, ...]

    @property
    def channel_count(self) -> int:
        return self.pixel_count * 3
```

TEST room:

```python
RoomProfile(
    surface_id="room_test", pixel_count=60, color_order="GRB",
    zones=(RoomZone("left", 0, 20), RoomZone("center", 20, 20),
           RoomZone("right", 40, 20)),
)
```

**`harness/room_surface.py`, the adapter, imports luxaeterna:**
`to_capability(profile) -> SurfaceCapability`, appending the `primary` zone that
luxaeterna's `SurfaceCapability.zone()` expects to resolve. Both consumers,
`devicelink/agent.py` and `harness/room_simulator.py`, already import from
`harness/`, so this introduces no new dependency direction.

`primary` therefore lives only in the adapter, which also makes section 5's
"omit `primary` from the serialized zones" fall out for free rather than needing
a filter.

A test asserts no `control/` module imports luxaeterna, pyarco or o2litepy at
module level, so the invariant stops being accidental. It deliberately does not
flag indented imports: see the module-level boundary note above.

Linear with three equal zones, because the physical Terrarium array is a single
6 m run rather than a ring and a stem. 60 px is arbitrary but deliberate: large
enough that the Console view reads as a room rather than a Shroom, small enough
to sit well inside F5's 170 px single-universe ceiling. `GRB` matches what the
wire carries today; the RGBW question is F3's separate decision.

`DEMO` raises `NotImplementedError` from `room_profile()`. Failing at boot
matches `resolve_room_type()`'s existing fail-hard-never-downgrade contract, and
DEMO's backend is already a deferred follow-up spec.

### 4.1 The four hardcodes to unpick

| Location | Today | Becomes |
|---|---|---|
| `devicelink/agent.py:153` | `self._capability or shroom_capability()` | `to_capability(self._room_profile)` |
| `devicelink/agent.py:249` | `bytes(universe.get_frame()[:36])` | slice `pixel_count * 3` from the room capability |
| `harness/shroom_client.py:52,160` | module constant `LED_CHANNELS = 36`, equality check | per-instance `expected_channels`, defaulting to 36 |
| `harness/room_simulator.py:56` | `WebSimBackend(capability=shroom_capability())` | `to_capability(room_profile(rt))` |

`DeviceLinkAgent` gains a `room_profile=` constructor parameter alongside the
existing `capability=`. The per-player path at `devicelink/agent.py:378` is not
touched: player devices remain Tuneshrooms and keep `shroom_capability()`.

`ShroomClient`'s `expected_channels` defaults to `LED_CHANNELS` so every existing
caller, including `harness/o2_shroom.py` in its player-device role, is unchanged.
The constant stays exported for those callers. A wrong-width frame is still
**dropped**, never truncated: silently rendering a short frame would turn a
configuration mismatch into a subtly wrong picture instead of a logged drop.

`harness/o2_shroom.py --no-join` serves as the Room simulator on the o2lite path
and takes the same treatment as `room_simulator.py`.

---

## 5. The Room read model

New module `control/room_view.py`: pure dict builders, no engine imports,
mirroring `console/protocol.py`'s style and testability.

It reads **both** manifest fields off the Room's `Role` (F7) and flattens them
into one list discriminated by `kind`:

```json
{
  "room_type": "TEST",
  "bound_dev": "sim-room",
  "capability": {
    "surface_id": "room_test", "pixel_count": 60, "color_order": "GRB",
    "zones": [{"name": "left", "start": 0, "count": 20},
              {"name": "center", "start": 20, "count": 20},
              {"name": "right", "start": 40, "count": 20}]
  },
  "instruments": [
    {"kind": "light", "instrument": "aurora", "target": "primary",
     "params": {"hue": 0.6, "level": 0.55},
     "lanes": [{"source": "cc:74", "dest": "hue"}]},
    {"kind": "audio", "instrument": "flsyn", "program": 89,
     "drone": {"key": 50, "velocity": 80},
     "lanes": [{"source": "cc:74", "dest": "cc:74"}]}
  ],
  "controllers": {"74": 93}
}
```

One list rather than two tables is the point. It makes visible, on one card row,
that `cc:74` drives aurora's hue and FluidSynth's filter cutoff from the same
stream.

`primary` is omitted from the serialized `zones` list: it spans the whole surface
by definition and drawing it would overlay every real zone. `SurfaceCapability.zone()`
already synthesizes it on demand, so nothing downstream needs it present.

`bound_dev` is included. It is a device id, not the node id, and a Room-bound
device already appears in the Console's device list today (without its role), so
this reveals nothing new.

`controllers` is keyed by controller number. It is `dict[int, int]` in Python
(section 6) and JSON stringifies those keys on the wire, so the browser reads
`room.controllers["74"]`. Called out because a lane's `source` is the string
`"cc:74"`, so the UI has to parse the number out of the lane to index this map.

---

## 6. Live controller values

`RoomBridge` gains `controllers: dict[int, int]`, recording the last value seen
per controller number, updated in `feed_light`. Roughly:

```python
def feed_light(self, status: int, d1: int, d2: int) -> None:
    if status & 0xF0 == 0xB0:
        self.controllers[d1] = d2
    if self._light is not None:
        self._light.feed_midi(status, d1, d2)
```

Recorded in `feed_light` rather than `feed_audio` because light is fed on every
cue while audio is released on its own schedule (`control/room_bridge.py:70-87`),
so the light side is the one that sees every value. `release()` clears it.

This does not compromise `RoomBridge`'s backend-agnostic-by-construction
property: it is a plain dict of ints and imports nothing.

---

## 7. Frame relay

`DeviceLinkAgent._render_room()` already renders, slices, and dedupes against
`_last_frames` before sending `/<dev>/leds`. It gains one more call at that same
point:

```python
if frame != self._last_frames.get(self._room_dev):
    ...
    self._emit_room_frame(self._room_dev, frame)   # guarded, best-effort
```

`_emit_room_frame` returns immediately when `self._on_room_frame` is `None`, and
otherwise wraps `self._on_room_frame(dev, frame)` in a try/except that logs and
continues, matching F8's established pattern for the other two sinks. `None` is
the default, so a run without `--console-port` constructs and behaves exactly as
it does today.

**Wired by constructor injection on `DeviceLinkAgent`, not as a `GameServer`
attribute.** `control/engine.py` is not edited by this slice at all. This follows
the precedent the 2026-08-10 simulator spec already set when Room cue routing was
deliberately placed in `DeviceLinkAgent._on_light_cue` rather than in
`GameServer`'s cue dispatch: the frame is computed in the transport, so the sink
belongs to the transport.

`ConsoleAgent` stores the latest frame and broadcasts it from its own `poll()`,
at most once per 100 ms, dropping any intermediate frames. It never queues and
never blocks.

**On boundary rule 2.** The rule forbids the console carrying per-device
`join`/`tick` traffic and requires that gameplay correctness never depend on the
link's health. A decimated, droppable, display-only copy of one device's rendered
frame satisfies both: nothing is retransmitted, nothing is awaited, and dropping
every frame degrades the picture and changes nothing else. This is recorded here
rather than assumed, because it is close enough to the rule's text to deserve an
explicit reading. Rule 2's wording may want a clarifying sentence when
`docs/MM_TERRARIUM.md` is next synced.

At 60 px the payload is 180 ints, roughly 1 KB per message and 10 KB/s at the
decimated rate. Kept as an int list rather than base64 for consistency with
`leds_event`'s existing shape.

---

## 8. Console UI

`console/static/` splits from one file into `index.html`, `console.js`,
`room.js`, `style.css`, served by the same `ConsoleServer`. **No build step**: a
venue box must not need npm, and `ConsoleServer` serving a self-contained static
directory is a property worth keeping.

The Room panel renders:

- **A zone view.** The relayed frame drawn as 60 swatches, with the capability's
  zone boundaries marked and named beneath. This is the "single visualization,
  clearly labelled" deliverable.
- **One card per instrument.** Kind (light or audio), instrument name, target
  zone, declared params, and each lane rendered as `cc:74 -> hue = 93` with the
  live value from `controllers`.
- **A header.** Room type, bound device, and a bound/unbound indicator.

When `room` is `null` the panel reads "No Room configured" and nothing else
changes.

---

## 9. Wiring the Console into the drivers

`harness/terrarium_boot.py` gains `--console-port` (default off). When set, it
constructs `ConsoleServer` and `ConsoleAgent`, registers the server on the
existing `TeardownStack` at the moment it is started, and calls
`ConsoleAgent.poll()` from the same tick loop that already drives
`DeviceLinkAgent.poll()`.

Registration order matters and is free here: the console server is started before
`boot()` for the same reason the devicelink server is, so reverse-of-registration
tears it down last. `harness/run_stack.py` passes the flag through and prints the
console URL alongside the simulator URL it already prints.

Default off, so every existing invocation is byte-identical.

---

## 10. Data flow

```
construction
  boot()                     resolves RoomType, binds Room, sets room.bound_dev
  terrarium_boot.build()     ConsoleServer + ConsoleAgent  (new, --console-port)
                             DeviceLinkAgent(room_profile=..., on_room_frame=...)
  _setup_room()              builds the Room LightSession against the ROOM capability
  SimulatorProcess           spawns the sim with --room-type TEST

per tick
  DeviceLinkAgent.poll()
    _render_room()   render -> slice pixel_count*3 -> dedupe
      |- /<dev>/leds        -> simulator      (unchanged: 44 Hz, timed)
      '- on_room_frame(...) -> ConsoleAgent   (new: guarded, best-effort)

  ConsoleAgent.poll()
    |- _broadcast_room_if_changed()   room view + controllers, on change
    '- _broadcast_room_frame()        latest frame, >= 100 ms apart
```

The simulator learns its surface by CLI argument (`--room-type TEST`, resolved
through `room_profile()` and `to_capability()`), not over the wire. Control spawns the simulator and
therefore already knows the shape, and the Room path has no join and so receives
no `/ie<N>/role` blob. A real-hardware Room backend will need the capability
delivered over the wire; that is part of the already-deferred real-hardware Room
backend, and inventing the message now would be speculative.

---

## 11. Wire protocol

Additions to `console/protocol.py`:

| Message | Shape | Cadence |
|---|---|---|
| `snapshot` | gains a `"room"` key: the room view, or `null` | on connect |
| `room_changed` | `{"event": "room_changed", "room": {...}}` | on change |
| `room_frame` | `{"event": "room_frame", "dev": str, "channels": [int]}` | <= 10 Hz, droppable |

`room_changed` uses the same change-detection shape as the existing
`_broadcast_status_if_changed()`. No existing message changes shape, so an old
browser tab against a new server degrades to exactly today's behavior.

---

## 12. Error handling

- `on_room_frame` is guarded at the call site (F8). A raising console sink must
  not stop the Room rendering, must not stop `/<dev>/leds` going out, and must
  not propagate into the engine tick.
- Frame relay is droppable and never queued. A slow or dead console link loses
  frames; gameplay is unaffected.
- No Room configured, or no `room_binding`, yields `room: null`. A `GameServer`
  built the pre-Room way keeps working, matching how every other Room-aware
  method in `DeviceLinkAgent` already no-ops.
- Room bound but no Bit loaded yields capability and zones with an empty
  `instruments` list.
- `room_profile()` on an unsupported `RoomType` raises at boot, not at render
  time.
- A frame whose width disagrees with the capability is dropped and logged at the
  client, never truncated.

---

## 13. Testing

The whole suite must stay green with no O2, no Arco and no pyarco importable.
Run it through the project venv, never a bare interpreter:

```bash
.venv/bin/python -m pytest tests -v
```

`control/room_profile.py` and `control/room_view.py` are pure and unit-test
directly. Beyond the obvious coverage, three tests are load-bearing:

1. **The section 3 regression.** Assert that the Room role does **not** appear in
   `snapshot()["roles"]` and its occupancy does **not** appear in registration
   counts, **while** `snapshot()["room"]` is populated with its instruments. Both
   halves in one test, because the amendment's entire safety argument is that
   they hold simultaneously. This is the test most likely to catch a future
   accidental widening.
2. **The `on_room_frame` guard.** A sink that raises must not prevent the Room's
   `/<dev>/leds` from being sent and must not escape `poll()`. Mirrors the
   existing guard regressions for `on_release` and `on_light_cue`.
3. **Frame width.** The Room's emitted frame is `pixel_count * 3` bytes and is
   built from the Room's profile, not `shroom_capability()`; `ShroomClient`
   drops a wrong-width frame rather than truncating it; and an existing
   `ShroomClient` built without `expected_channels` still accepts 36.

Also covered: `RoomBridge.controllers` records on `feed_light` and clears on
`release`; frame-relay decimation, driven by an injected clock so no test spends
real time; `room_changed` broadcasts on change only.

**Per boundary rule 5, `FakeConsoleServer` needs an audit before it is extended.**
`ConsoleServer.broadcast` drops slow and dead clients. If the fake assumes
delivery, a relay that never actually reaches a browser will pass its own tests.
That rule was earned expensively on 2026-08-13 and this is precisely the class of
double it applies to.

### 13.1 Live verification

Run `harness/run_stack.py` with `--console-port` set, open the Console, and
confirm the Room panel shows 60 px in three labelled zones animating from
`TestBit.cues(at)`'s hue drift, **with no device joined**, then confirm the panel
still shows no Room role among the player roles.

The no-device-joined property is the point, not a convenience. `docs/MM_TERRARIUM.md`
records device clock sync against a post-`reset()` Arco as intermittent, measured
at 1 of 3 runs succeeding headlessly, and that defect is upstream and unfixed.
A verification path that never needs a device to join is therefore the only part
of this stack that can be checked reliably today, and this slice's acceptance
should not be gated behind a join.

---

## 14. Non-goals

Explicitly out of scope, and compatible with every success criterion in section 15:

- **Triggers, cue scripts, conditions, and firing.** Spec B. Nothing in this
  document adds a `TriggerTable`, a script primitive, a `TriggerFired` event, or
  a Fire button.
- **The DEMO room and its 864 px array**, and with it multi-universe rendering.
  `room_profile(DEMO)` raises.
- **A real-hardware Room backend**, and delivering the capability over the wire.
- **The RGBW / 48-channel wire widening** (F3). Untouched.
- **Uplink changes.** Not edited.
- **`control/engine.py` changes.** Not edited.
- **Authentication.** The Console's trusted-LAN, no-auth, `127.0.0.1`-by-default
  model is unchanged and remains load-bearing.

---

## 15. Success criteria

1. `harness/run_stack.py --console-port N` serves a Console reachable during a
   live run, and the console server is torn down in reverse-of-registration
   order with the rest of the stack.
2. The Room's `LightSession` is built from `room_profile(RoomType.TEST)`, and
   the emitted Room frame is 180 bytes rather than 36.
3. The Console's Room panel draws the live Room frame with `left` / `center` /
   `right` labelled, and one card per declared instrument showing its target
   zone, its lanes, and each lane's live controller value.
4. Light and audio instruments appear in one list, so `cc:74` is visibly the
   same controller driving aurora's hue and FluidSynth's cutoff.
5. The Room's role name, its registration counts, and `ROOM_NODE_IDS` appear
   nowhere in any Console or uplink payload, asserted by section 13's test 1
   simultaneously with `snapshot()["room"]` being populated.
6. A raising console frame sink does not interrupt Room rendering or the Room's
   `/<dev>/leds` stream.
7. The suite passes offline with no O2, no Arco and no pyarco, and does not
   regress from this branch's measured baseline of **764 passed, 1 skipped**.
8. Every existing invocation of `terrarium_boot` and `run_stack` behaves
   identically when `--console-port` is not passed.

---

## 16. Open questions

Recorded rather than resolved, none blocking:

- **TEST room zone names and pixel count.** `left` / `center` / `right` at 60 px
  is a reasonable first shape, not a researched one. It should be revisited when
  a production Bit declares what it actually wants to address.
- **Boundary rule 2's wording.** Section 7 argues the frame relay is compatible
  with it. If that reading is accepted, rule 2 in `docs/MM_TERRARIUM.md` should
  gain a sentence saying display-only, droppable copies are permitted, so the
  next reader does not have to re-derive the argument.
- **Whether the Console should show player devices' frames too.** The same relay
  mechanism would serve it, and player frames already flow through
  `_render_frames()`. Deliberately not in this slice: one Room is one frame
  stream, N players is a fan-out with its own decimation question.
