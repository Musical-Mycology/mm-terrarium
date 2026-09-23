# Art-Net FixtureSink: Room fixtures drive WLED ESP32 controllers

**Date:** 2026-09-23
**Status:** Approved design, pre-implementation. Brainstormed and approved
section by section with Chris, 2026-09-23.
**Closes:** the follow-ups named in
[`2026-09-01-per-fixture-light-sessions-design.md`](2026-09-01-per-fixture-light-sessions-design.md)
§11: "Hardware `FixtureSink` (luxaeterna backend ...) and cue routing by
fixture name for fixtures that never bind a device". It also closes the
deep-dive's *Not yet built* entry "A real-hardware Room backend" for DEMO.
**Driver:** Nothing in Musical Mycology has driven a physical light over
Art-Net yet. Lux Aeterna renders every Room fixture inside Control
(`devicelink/agent.py`) and hands each changed frame to that fixture's
`FixtureSink`s (`control/fixture_sink.py`). The venue array and the Booster
are driven by stock WLED ESP32 controllers that take Art-Net
(`MM_HARDWARE_DESIGN.md` §11.5). `harness/array_smoke.py` already drives an
864 px RGBW span over Art-Net standalone, but it is not wired into Room load.
This spec adds the third `FixtureSink`, plus the two changes it cannot work
without: cue routing by fixture name, and native RGBW fixtures.

Related: luxaeterna `docs/deployment.md` *Embedded devices: a pixel sink, not
a port* (luxaeterna PR #20); the light-path diagram in this repo's deep-dive
(PR #139).

---

## 1. What the code does today (verified 2026-09-23, `origin/main@1276cfb`)

| Fact | Where | Consequence for this spec |
|---|---|---|
| `_resolve_devs` resolves `@room` / `@fixture:<name>` only to **bound** device ids, and drops an unbound fixture with a once-per-load warning | `control/engine.py` `_room_devs`, `_resolve_devs` | An Art-Net fixture never binds an o2lite device, so it would get ambient, generators and breath, but no Bit cue, override or mute |
| The agent maps a cue to a fixture through the bound dev (`_fixture_for_dev`). `_muted` and `_overrides` are keyed by dev | `devicelink/agent.py` | Same as above |
| `_sinks_for` builds fresh sink objects on every render, and `_render_room` only calls sinks when the frame changed | `devicelink/agent.py` | A sink with a thread and socket must persist across renders. WLED leaves realtime mode after ~2.5 s without packets, so the sink needs its own keepalive |
| WLED has no O2 clock and latches frames on arrival | WLED | `when` can only be honored by holding the frame in Control |
| Fixture frames are 3 ch/px everywhere (`* 3` in `room_profile.py`, `agent.py`, `o2_shroom.py`, `websim_leds.py`; the GRB decode in `console/static/surface.js` and `design.js`; luxaeterna `websim.py`) | several | The array and fiber engines are SK6812 **RGBW** |
| luxaeterna already renders RGBW: `channels_for(color_order) == len(color_order)`, and ugens pad RGB colors with W = 0 | `luxaeterna/synth/engine.py`, `ugens.py` | Native RGBW is mostly an mm-terrarium sizing change |
| `PixelSpan` requires `channels_per_pixel` to divide 512 evenly (no 3-ch straddling, by decision) | `luxaeterna/pixelspan.py` | 4 ch/px fits unchanged, at 128 px per universe. The 864 px array is 7 universes |
| `rooms/DEMO.toml` declares `backends = ["devicelink", "array"]`. `validate_rooms` gates it on `BootConfig.array_backend_configured`, which only `terrarium_boot --room-type DEMO` sets (to `"simulator"`) | `control/terrarium_config.py`, `control/boot_config.py`, `harness/terrarium_boot.py` | The gate exists, but nothing names a real host |
| One SK6812 RGBW pixel shifts out in 40 µs, so 864 px on one data pin take ≈ 34.6 ms, a ceiling of ≈ 29 Hz | arithmetic | Bring-up must split the array across ≥ 2 WLED outputs (§9) |

## 2. Goals and non-goals

**Goals**

- G1. A Room fixture configured with an Art-Net output drives a WLED
  controller from Room load to unload, with every Bit cue, override and mute
  it would get if a device were bound.
- G2. Each frame is displayed at its `when`, within scheduler jitter plus a
  per-output measured lead.
- G3. Sends never block the engine tick (boundary rule 2).
- G4. Every Art-Net frame passes a per-output power limiter, with no opt-out.
- G5. Fixtures may be RGBW end to end: render, override, Console, simulator
  and wire.
- G6. The test suite stays runnable with no network. A local fake WLED
  receiver gives a full no-hardware run.

**Non-goals** (each is a named follow-up, §12)

- Art-Net ArtSync, and sACN, serial-Enttec or DDP sinks.
- The Tuneshroom *player* wire (36 bytes, 3 ch). It stays open and unchanged.
- White-aware instruments. Existing manifests render W = 0.
- A white SolidCue, i.e. an RGBW override color.
- A Console health panel ("WLED reachable?").
- The venue Room profile, including its fiber fixture. DEMO is the target
  here.
- Multiple controllers per fixture. One fixture maps to one output with
  contiguous universes.

## 3. Decisions

| # | Decision | Why | What would change it |
|---|---|---|---|
| D1 | **One `ArtNetFixtureSink` per configured output, each with its own sender thread** (approach A) | Each output fails and recovers independently; a dead fiber controller never stalls the bars. It tests in isolation with a fake backend and clock. Two controllers means two threads, which is trivial | A need for frame-accurate sync *across* controllers, which would call for one shared output service |
| D2 | **Route every fixture by name.** `@fixture:<name>` is a fixture's canonical target, bound or not | An Art-Net fixture never binds. Doing this for every fixture, not only hardware ones, closes the §5.2 limitation outright and leaves one code path | none expected |
| D3 | **Hold in Control until due**, on the sink's thread | WLED latches on arrival. Sending immediately would put the array ~one horizon (60 ms) ahead of Tuneshrooms showing the same cue | A measured array display latency close to the horizon. `lead_ms` covers this |
| D4 | **Native RGBW**: a fixture's `color_order` may be 4 letters, and channels = `len(color_order)` | The hardware is RGBW. luxaeterna already renders it, and PixelSpan and PowerBudget assume 4 ch | n/a (chosen by Chris over expanding in the sink) |
| D5 | **Wiring lives in `terrarium.toml` `[[artnet]]`**, not in the room TOML | IPs are per box. Rooms are cataloged, edited in the Design tab and shared across boxes: the same DEMO drives the simulator on a dev box and WLED at the venue | Rooms that only ever exist at one site |
| D6 | **The power limit is applied in the sink, per output, after RGBW** | The budget belongs to the PSU the controller's strip hangs on. Every path to the wire is limited, including overrides, keepalives and the close frame. None of the limiter's work runs on the tick | Several outputs on one PSU. Budgets must then sum (§6.3) |
| D7 | **No luxaeterna change for the sink.** It reuses `ArtNet`, `PixelSpan`, `UniverseSet`, `PowerBudget`, `PowerLimiter` and `ThrottledLog` as they are. The only luxaeterna change is WebSim RGBW display (§7) | 4 ch/px fits PixelSpan unchanged | n/a |

## 4. Architecture and data flow

```
Bit cue ─► GameServer._resolve_devs ─► "@fixture:<name>" (every declared fixture, bound or not)
                                             │
DeviceLinkAgent._on_light_cue ─► fixture session feed (by name) ─► _render_room (44 Hz tick)
                                             │ changed frame, when = at or clock()+horizon
                     ┌───────────────────────┼─────────────────────────┐
             ConsoleFrameSink        DeviceLinkSink (if bound)   ArtNetFixtureSink (if configured)
                                                                   lock + schedule push, returns
                                                                        │ sender thread
                                                  due? → PowerLimiter → UniverseSet → ArtNet.send ×N universes
                                                  idle ≥ keepalive → resend last frame
```

### 4.1 Cue routing by fixture name (`control/engine.py`)

*(Amended 2026-09-23 while writing the plan. The first draft made the token
the engine's only spelling, even for bound fixtures. That contradicted §8.3's
promise that bound-fixture tests pass unchanged, and a prototype showed it
would rewrite ~146 `sim-room-*` assertions for no behavioral gain. The
engine now keeps a bound fixture's dev, and the agent canonicalizes (§4.2).)*

- A new `_fixture_target(name)` returns the bound dev when the fixture is
  bound, else `cues.fixture_dev(name)`.
- `_room_devs()` returns `_fixture_target(f.name)` for **every** declared
  fixture, in profile order. It returns `[]` only when no Room is loaded.
- `_resolve_devs("@fixture:x")` returns `[_fixture_target("x")]`. `load_bit`
  already refuses undeclared names (`_bit_fixture_names`), so
  `_warned_unbound` and its drop path are deleted.
- `_instrument_for("@fixture:x")` returns that fixture's instrument.
- As a result, an unbound fixture receives every Room broadcast, `@fixture:`
  cue, SolidCue and mute as `@fixture:<name>`. A bound fixture's cues go to
  its dev exactly as before. The only existing tests whose expectations
  change are the 12 in `tests/test_engine_functions.py` whose `_Room` binds
  only `main`: the unbound `accent` now appears as `@fixture:accent`.
- `cues.py` stays the only module that spells the `@fixture:` prefix.

### 4.2 Agent (`devicelink/agent.py`)

- `_fixture_for_dev(dev)` becomes `_fixture_for(target)`. It accepts a
  `@fixture:` token, or a bound dev translated through `room.bound`. A player
  dev returns `None`, as today.
- Fixture entries in `_muted` and `_overrides` are keyed by the token.
  `_on_mute_change`, `_on_solid_cue`, `_feed_light_now`, `_drain_light_cues`
  and `_on_light_cue` canonicalize before touching them. `_render_room` looks
  up the override by `fixture_dev(fixture.name)`, not `bound.get(name)`.
- `_on_play_cue` for a fixture token maps back to the bound dev and sends
  `/<dev>/play` as today. When nothing is bound it drops, logged once per
  load, which is today's behavior for an unbound fixture. Fixture audio is
  already keyed by fixture name and is unaffected.
- **Persistent outputs.** The constructor takes
  `outputs_for: Callable[[RoomProfile], dict[str, list[FixtureSink]]] | None`.
  `_setup_room` calls it once, `start()`s each sink, and stores them per
  fixture. `_sinks_for` appends them after the Console and DeviceLink sinks.
  `unwire_room` `close()`s them. A Bit swap inside one Room keeps them.
- `FixtureSink` (the protocol) is unchanged. `start()` and `close()` are
  duck-typed on persistent outputs only.

### 4.3 The sink (`devicelink/artnet_sink.py`, new)

`devicelink/` already imports luxaeterna, so this module may too.

```python
class ArtNetFixtureSink:
    def __init__(self, *, name: str, pixel_count: int, start_universe: int,
                 budget: PowerBudget, backend: DMXBackend, clock: Callable[[], float],
                 lead: float = 0.0, keepalive: float = 0.25): ...
    def send_frame(self, frame: bytes, when: float) -> None: ...   # tick thread
    def start(self) -> None: ...
    def close(self) -> None: ...
    def stats(self) -> dict: ...
    def _service_once(self, now: float) -> float: ...   # returns next wake time; tests call it directly
```

- It owns `UniverseSet(PixelSpan(pixel_count, 4, start_universe))`, a
  `PowerLimiter(budget)`, and a `control.timed_queue.TimedQueue` behind a
  `threading.Condition`.
- `clock` is the agent's own clock (O2 time), so `when` and `now` share one
  time base.
- A frame whose length is not `pixel_count * 4` is dropped and logged once.
  It is never truncated (the `ShroomClient` rule).

## 5. Timing

- **`send_frame`** takes the lock, calls `push(when, frame, now)`, notifies,
  and returns.
- **The sender loop** waits on the condition until whichever comes first:
  - the earliest queued `when - lead`,
  - the keepalive deadline,
  - a notify.

  On wake, `_service_once(now)`:
  - takes **every** due frame and keeps the newest (coalescing, so a stall
    never builds a backlog);
  - limits it, calls `set_pixels`, and sends each universe;
  - records lateness for frames that arrived already past due.

  There is no fixed-rate loop, so there is no beat against the 44 Hz render
  tick.
- **Keepalive.** If nothing has been sent for `keepalive` (default 0.25 s),
  the last frame is resent. This keeps WLED in realtime mode and heals a lost
  UDP packet on a static scene.
- **`lead_ms`** (per output, default 0) sends early to absorb the
  controller's own latency. For an RGBW strip that latency is dominated by
  its shift-out time (≈ 35 ms on one pin, ≈ 17 ms on two). It stays 0 until
  bring-up measures it (§9 step 6).
- **`cue_horizon` is unchanged.** The Art-Net hop (LAN, ~1–5 ms) is shorter
  than the o2lite hop the horizon was sized against, and `lead_ms` absorbs
  the strip. ArtSync is out of scope: an output's 7 universes go out back to
  back within ~1 ms.

## 6. Config, RGBW and power

### 6.1 `terrarium.toml`

```toml
[[artnet]]
room = "DEMO"
fixture = "array"
host = "<WLED controller IP>"   # site-specific; no default
start_universe = 0
max_amps = 10.0                 # required; no opt-out
# optional:
port = 6454                     # override for a local fake receiver (§8.3)
amps_per_pixel_full = 0.025     # PowerBudget default, SK6812 RGBW @ 12 V
lead_ms = 0
keepalive_ms = 250
```

- `control/terrarium_config.py` parses entries into a pure, frozen
  `ArtNetOutput` on `TerrariumConfig.artnet_outputs`, with no luxaeterna
  import.
- Validation raises a located `TerrariumConfigError` on each of:
  - unknown room;
  - unknown fixture;
  - a fixture `color_order` other than exactly `RGBW`;
  - `max_amps <= 0`;
  - a duplicate (room, fixture);
  - `start_universe < 0`;
  - overlapping universe ranges on the same (host, port);
  - an unknown key.
- `validate_rooms` treats a room with `"array"` in `backends` as loadable
  when either:
  - `BootConfig.array_backend == "simulator"`, or
  - every fixture in the room has an `[[artnet]]` entry.

  The "any other string = a real host" meaning of `array_backend` is removed
  from `boot_config.py`'s comment and code.
- `harness/terrarium_boot.py` (the composition root) builds the
  `outputs_for` factory from `config.artnet_outputs`, creating each sink
  with `ArtNet(host, port)` and the agent's clock. `control/` stays
  stdlib-only.

### 6.2 Native RGBW

- **`control/room_profile.py`.**
  - `RoomFixture.channels = len(color_order)`.
  - `color_order` must contain R, G and B exactly once each, W at most once,
    and nothing else.
  - `channel_count` and `fixture_slices` use per-fixture channels.
  - The mixed-color-order refusal stays.
  - `_MAX_PROFILE_PIXELS` stays at 170. Its comment is corrected: a block is
    a physical run, and universes are PixelSpan's job per output.
- **`rooms/DEMO.toml`.** The `array` fixture becomes `color_order = "RGBW"`.
  That is the Art-Net *wire* order; WLED's LED settings carry the strip's
  physical GRBW. TEST stays GRB.
- **`devicelink/agent.py`.** Each fixture's `Universe(channel_count=...)` and
  frame slice use `pixel_count * channels`. `_apply_override` builds a
  `channels`-wide pixel in `color_order`, with SolidCue `rgb` stated R, G, B
  and **W = 0**, repeated `len(frame) // channels + 1` times.
- **Console.**
  - `control/room_view.py` fixture entries gain `color_order`.
  - `console/static/surface.js` decodes each fixture by its own order and
    width, replacing the hardcoded `channels[i*3]=g`.
  - `design.js` is unchanged (amended 2026-09-23): the design bench
    renders a Shroom capability, which is always GRB.
  - W is displayed additively as (r+w, g+w, b+w), clipped to 255.
- **Simulator.** `harness/o2_shroom.py` and `harness/websim_leds.py` size by
  `pixel_count * channels`.
- **Unchanged.** The Tuneshroom player wire (`_DEVICE_CHANNELS = 36`) and
  `shroom_capability` stay as they are.

### 6.3 Power

- The sink applies `PowerLimiter(PowerBudget(max_amps, amps_per_pixel_full,
  channels_per_pixel=4))` to every frame it sends, including keepalives and
  the close frame.
- The hard ceiling (117) and the adaptive scale are luxaeterna's defaults.
- **Shared PSU.** Budgets are per output. The config comment and the §9
  checklist state that outputs sharing a PSU must have `max_amps` values
  that **sum** to ≤ 80 % of its rating. The validator cannot know PSU
  topology.
- **WLED's ABL** is set at bring-up as a second, independent defense.
- **The Console strip shows the pre-limit frame**, so it reads brighter than
  the array at high load. That is accepted: the Console is for monitoring
  only.
- **Hardware doc conflict, flagged for the hardware owner.**
  `MM_HARDWARE_DESIGN.md`'s Terrarium bill of materials lists a "12 V 20 A
  PSU" for the LED subsystem, while its §11 parts table lists the Mean Well
  LRS-150-12 (12.5 A). This spec keeps `array_smoke`'s
  `TERRARIUM_MAX_AMPS = 10.0` (80 % of 12.5 A) as the DEMO value.

## 7. Companion luxaeterna change

`luxaeterna/backends/websim.py` hardcodes 3 channels: `f[i*3]` in the page
script, and `self._n = pixel_count * 3`. It learns
`channels_for(cap.color_order)` and the same additive W display as the
Console, so the DEMO simulator still renders once DEMO is RGBW. This is a
separate luxaeterna PR with its own tests, which must merge before the
mm-terrarium RGBW task. Nothing else in luxaeterna changes.

## 8. Threading, lifecycle, errors and testing

### 8.1 Threading and lifecycle

- **Threads.** Each output has one daemon thread,
  `artnet-<room>-<fixture>`. The tick thread only runs `send_frame`, which
  costs microseconds and does no I/O. `ArtNet`'s socket is already
  non-blocking.
- **Tick-side cost.** The limiter is pure Python over 3456 channels and holds
  the GIL. Bring-up measures its effect on the tick with
  `harness/render_bench.py` (worst frame, p95). A numpy limiter in luxaeterna
  is the fallback if it shows up.
- **Lifecycle.**
  - `start()` runs in `_setup_room`.
  - `close()` runs in `unwire_room`: set stop, notify, send one all-black
    (limited) frame, join with a 2 s timeout, close the backend.
  - WLED then holds black until its realtime timeout and falls back to its
    default preset. §9 sets that preset to off, so the array stays dark
    between Rooms.

### 8.2 Errors and observability

- Nothing propagates to the tick, and nothing fails a Room load. UDP cannot
  detect an unreachable controller anyway.
- A `BackendError` from `send` is counted and logged through luxaeterna's
  `ThrottledLog`. The next keepalive retries, so a controller that
  power-cycles recovers within `keepalive_ms`.
- An `open()` failure is logged and retried on each wake.
- An unexpected exception in the loop is logged and the loop continues. The
  thread never dies silently.
- `stats()` returns frames sent, keepalives, send errors, late count, and a
  bounded lateness sample (the `TimedQueue.lateness` shape).
- The first successful send and the first error for each host are logged at
  INFO and WARNING.

### 8.3 Testing (offline, `importorskip("luxaeterna")`)

- **Strict fake backend (boundary rule 5).**
  - It refuses a send before `open()`, and a payload length that is odd or
    outside 2–512, exactly as `ArtNet.send` does.
  - It records `(universe_id, bytes, clock_time)`.
  - A contract test runs the real `ArtNet` against a localhost UDP socket and
    asserts the fake accepts and refuses the same inputs.
- **Sink, deterministic (`_service_once` with a fake clock, no thread).**
  - a frame is held until `when - lead`;
  - due frames coalesce to the newest;
  - a late frame is sent and counted;
  - a keepalive fires at `keepalive` and not before;
  - an all-white frame comes out within budget;
  - 864 px become 7 universes of 512 bytes each, the last zero-padded;
  - a wrong-width frame is dropped;
  - `close()` sends black.
- **Sink, threaded.** One start/stop test. One test that `send_frame`
  returns promptly while the backend's `send` is blocked on an `Event`.
- **Routing.**
  - An unbound fixture receives a LightCue, a SolidCue override and a mute
    by `@fixture:` name.
  - A bound dev canonicalizes to its token in `muted`, `FunctionFired.devs`
    and generator suppression.
  - A `PlayCue` to an unbound fixture drops, logged once.
  - The existing bound-fixture tests pass unchanged, as a regression guard
    (§4.1 names the 12 partially-bound expectations that change).
- **RGBW.**
  - profile `channels`, slices and `color_order` validation;
  - agent universe sizing and override (W = 0);
  - DEMO renders 3456 channels;
  - `room_view` carries `color_order`.
- **Config.** Every validation error in §6.1, and the `validate_rooms` gate
  both ways (simulator and Art-Net).
- **Fake WLED: `harness/artnet_listen.py` (new).**
  - It binds a UDP port and parses ArtDmx strictly: ID, opcode, and the
    length field against the payload.
  - It reassembles a span's universes into a frame and reports fps,
    per-universe sequence gaps and inter-arrival jitter.
  - `--websim` paints the reassembled frame on a luxaeterna WebSim canvas.
  - Its parser is unit-tested against packets from the real
    `ArtNet._build_packet`.
  - With `[[artnet]] host = "127.0.0.1"` and a `port`, this gives a full
    no-hardware run: Bit → Art-Net → fake WLED canvas.

## 9. Hardware bring-up checklist

Tick each item in the deep-dive as it is verified, with measured figures.

1. **Network.** The controller and the Terrarium are on the same wired LAN
   or AP subnet, with no VM or WSL2 (deep-dive *Host platform*). Prefer
   Ethernet WLED boards. Record each controller's IP in `terrarium.toml`.
2. **WLED config.**
   - LED type SK6812 RGBW with color order GRBW, checked against the strip.
   - Realtime set to Art-Net, DMX mode *Multi RGBW*, start universe matching
     the config.
   - **The 864 px split over ≥ 2 data outputs.** One pin tops out at
     ≈ 29 Hz.
   - ABL on.
   - Default/boot preset off.
3. **Smoke without Control.** `python -m harness.array_smoke --host <ip>
   --pixels 144`, then `--pixels 864`: the green wave shows no wrong color
   order and no universe seams.
4. **Through Control.** Load DEMO with `[[artnet]]` configured. Repeat the
   2026-08-20 rainbow seam sweep on real LEDs: no visible step at the
   128-px universe boundaries (128, 256, …). Fire `play_aurora` and a Console
   mute on the unbound `array` fixture.
5. **Power.** Drive all-white through Control and read the PSU current with
   a clamp meter. It must stay ≤ `max_amps`.
6. **Timing.** Film a Testshroom and the array together on a tilt cue with a
   high-speed phone camera, measure the offset, set `lead_ms`, and record
   the figure. Check tick cost with `render_bench.py`.
7. **Soak.** Run 30 min: `stats()` send errors are 0, and a WLED
   power-cycle mid-run recovers within `keepalive_ms`.
8. **Fiber and Booster.** Repeat steps 2–5 per controller. The fiber
   engines are a separate fixture on the second controller, and the Booster
   is 128 px on one universe.

## 10. Implementation order (for writing-plans)

1. luxaeterna WebSim RGBW (§7). Separate repo and PR.
2. Cue routing by fixture name (§4.1, §4.2 minus outputs). This is a pure
   refactor with a regression guard, and it lands on its own merit.
3. Native RGBW (§6.2). Depends on 1.
4. `ArtNetFixtureSink` and the strict fake (§4.3, §5, §8).
5. Config, the `validate_rooms` gate and composition (§6.1), and the agent's
   `outputs_for`.
6. `harness/artnet_listen.py`, and an end-to-end no-hardware run.
7. Deep-dive sync: close the per-fixture §5.2 limitation, update *Not yet
   built*, add a section for this slice.

Bring-up (§9) follows on hardware and is tracked against the student
hardware track's Gate 2 (2026-10-16).

## 11. Risks

- **The limiter's GIL time on the tick.** Measured in §9 step 6. The
  fallback is a numpy limiter.
- **The routing refactor touches many engine call sites.** Mitigated by the
  unchanged bound-fixture tests and by landing it first on its own.
- **WLED Multi RGBW behavior is unverified here** (tearing across
  universes, whether ABL applies in realtime mode). §9 checks both. ArtSync
  is the follow-up if tearing is visible.
- **Wi-Fi jitter.** 7 universes × 44 Hz ≈ 308 packets/s per output. Prefer
  wired Ethernet, and read `artnet_listen.py`'s jitter figures on the real
  network before the show.

## 12. Follow-ups named by this spec

- White-aware `instruments/venue_array.toml`, and an RGBW SolidCue color.
- The venue Room profile with bars and fiber fixtures on two controllers.
- A Console health read-out from `sink.stats()` (boundary rule 3's
  anticipated Lux Aeterna health seam).
- Art-Net ArtSync, and sACN/Enttec/DDP sinks.
- The Tuneshroom player RGBW wire (still open).
- Reconciling the 12 V PSU rating in `MM_HARDWARE_DESIGN.md` (§6.3).
