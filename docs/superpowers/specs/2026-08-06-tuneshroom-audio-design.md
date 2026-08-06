# Tuneshroom demo: audio that tracks the light, off one shared MIDI stream

**Date:** 2026-08-06
**Status:** approved design, ready for an implementation plan
**Repos touched:** `luxaeterna` (small, lands first), `mm-terrarium` (this repo)

## 1. Goal

`harness/led_smoke.py` currently renders TestBit's `player` role to a browser
Shroom and injects a canned `cc:74` ping-pong ramp. It makes light and no sound.

This slice makes the demo **audible**, with the audio modulating in time with
the visible pulsing, driven from the *same* control stream that drives the
light. Not a parallel audio timeline that happens to look synchronized: the
same numbers, read in the same tick, by both consumers.

This is the first time anything in `mm-terrarium` builds an Arco ugen graph. It
is deliberately a small, provisional step, and section 9 says exactly which
parts are provisional and why.

## 2. Why this is possible now

The Arco server on Mycological can make sound for the first time (2026-08-05).
Verified state as of this spec:

- `~/projects/arco` is on `main`, and `main`'s HEAD is `95cd215 Fix Sum/Sumb
  action_rem signature`. The `pyarco/ugens/sum.py` fix is **already on main**;
  the `test/roger-pytest-sound` branch is no longer needed.
- `flsyn` is listed in `apps/pytest/dspmanifest.txt`, so the built server at
  `~/projects/arco/apps/pytest/server` has FluidSynth compiled in.
- `arco.initialize()` **blocks** until connected and reset, then the client only
  needs `sched.poll()` on a timer. It does **not** require `sched.run()` to own
  the main loop. Our existing tick loop can call `sched.poll()` each tick. No
  threads, no inversion of control.
- The server is a curses application (`fopen("/dev/tty")`, no headless mode), so
  it must be started by a human in a real terminal. See section 10.

Two gaps closed by this design rather than assumed away:

- There is no `apps/pytest/soundfont.py`, so Roger's `miditest.py` has never run
  on this box. A soundfont does exist at
  `/Users/chris/projects/fluidsynth/sf2/VintageDreamsWaves-v2.sf2`. This demo
  takes the path as a CLI flag / env var and never imports his untracked file.
- Venvs are split. `mm-terrarium/.venv` has luxaeterna but not
  `zeroconf`/`netifaces`; `arco/.venv` has the reverse. Both are Python 3.14.4,
  and `netifaces` builds cleanly in the arco venv, so adding those two packages
  to `mm-terrarium/.venv` plus `PYTHONPATH=/Users/chris/projects/arco` unifies
  it. No second interpreter, no vendoring.

## 3. The control idea: two controllers, each the analog of itself

One MIDI stream per device. Control fans it out to the light renderer and to the
synth. Two controllers carry the whole demo:

| Controller | Light consequence | Audio consequence |
|---|---|---|
| `cc:74` (Brightness) | aurora `hue` glide | FluidSynth filter cutoff |
| `cc:11` (Expression) | aurora `level`, i.e. the breath | channel expression, i.e. loudness |

`cc:11` is the load-bearing addition. Today aurora's visible pulsing is
`_AURORA_BREATHE`, a fixed piecewise-linear envelope baked into the luxaeterna
preset and looping on its own clock. It is not on the MIDI stream and cannot be
bound as a lane destination, so no audio could possibly track it without either
guessing its period (a second timeline, forbidden by design rule 2) or moving it
onto the stream.

This design moves it onto the stream. Control generates the breath and sends it
as `cc:11`; light binds it to aurora's `level`, audio binds it to channel
expression. The visible swell and the audible swell become the same value. They
cannot drift, because there is nothing to drift from.

`cc:11` is also the acoustically safe choice: FluidSynth honors Expression as a
direct attenuation, so the swell is audible on any soundfont. `cc:74` is a
FluidSynth default modulator onto filter cutoff, but how strongly it reads
depends on the preset, so the demo does not stake its audibility on it.

FluidSynth is silent without a note, so the shared stream also carries a
**sustained drone**: one note-on at run start, one note-off at release. Light
ignores it, because aurora declares no note lane. The PR #9 strobe fix (see
`docs/MM_TERRARIUM.md`, `bits/` section) is untouched, and the running light
declaration still has no note lane.

### 3.1 Preserving the current look exactly

Control generates the breath as **exactly `_AURORA_BREATHE`'s shape**:
`0.55 -> 1.0 -> 0.55` over 6.0 s, looping, scaled to 7-bit as `round(v * 127)`
(so 70 -> 127 -> 70). luxaeterna's `dispatch_midi` normalizes cc back with
`d2 / 127.0`, and aurora's `level` is wrapped in a `Smooth`, so the glide across
the coarse 7-bit steps is handled the same way `hue` already handles them.

Net effect: **the demo looks identical to today**. Same breath, same period, same
0.55 floor, same "never dark" property. The only change is that the number now
originates in Control, which is what makes it shareable.

## 4. luxaeterna change: `level` as an additive param on `aurora`

The 2026-07-23 aurora spec set the precedent "a new instrument, leaving `bloom`
and `glow` untouched". This change is additive rather than a new preset, because
it can be made with **zero** behavior change when the param is absent.

In `luxaeterna/synth/presets.py`:

```python
_AURORA_PARAMS = frozenset({"hue", "level"})

_AURORA_BREATHE = [(0.0, 0.55), (3.0, 1.0), (6.0, 0.55)]   # unchanged
_AURORA_HUE_GLIDE_TAU = 0.4
_AURORA_LEVEL_GLIDE_TAU = 0.15                             # new


def _make_aurora(**params) -> LightInstrument:
    unknown = set(params) - _AURORA_PARAMS
    if unknown:
        raise KeyError(f"unknown aurora param(s) {sorted(unknown)} "
                       f"(known: {sorted(_AURORA_PARAMS)})")
    hue = Smooth(Const(float(params.get("hue", 0.0))), _AURORA_HUE_GLIDE_TAU)
    exposed = {"hue": Param("hue", hue)}
    if "level" in params:                    # externally driven
        level = Smooth(Const(float(params["level"])), _AURORA_LEVEL_GLIDE_TAU)
        exposed["level"] = Param("level", level)
    else:                                    # unchanged: self-breathing
        level = SegmentLevel(_AURORA_BREATHE, loop_from=0.0)
    return LightInstrument(Fill(level, HueColor(hue)), exposed)
```

**Declaring `level` is what opts into external drive**, exactly the way declaring
`hue` already supplies a cc-drivable starting value. Omit it and you get today's
graph object for object, so all four existing aurora tests
(`tests/synth/test_presets.py`) and the `cc:74 -> hue` binding test pass
unmodified, and nothing else in luxaeterna moves.

`Smooth(Const(...), tau)` is the settable idiom `hue` already uses:
`Param.set` calls `ugen.set_target`, and `Smooth.set_target` forwards to its
`Const` source. `SegmentLevel` has no `set_target`, which is precisely why the
two modes cannot be collapsed into one graph.

A `cc:11 -> level` lane against a self-breathing aurora fails at `resolve()` with
luxaeterna's existing located error ("routes to unknown param 'level'; known
params: ['hue']"). That is the correct failure: a manifest that wants the breath
driven must say so by declaring the param.

### 4.1 luxaeterna tests

- `level` absent: aurora still breathes, still never dark, `param_names() ==
  {"hue"}` (the existing tests already assert the first two; add the third).
- `level` present: `param_names() == {"hue", "level"}`; rendered field level
  tracks the set value rather than the breath envelope; the level glides toward
  a new target rather than snapping (mirrors the existing hue-glide test).
- A manifest decl of aurora with `params={"hue":…, "level":…}` and lanes
  `cc:74 -> hue` plus `cc:11 -> level` resolves, and dispatching `cc:11` moves
  the rendered level.
- A decl with a `cc:11 -> level` lane but **no** `level` param raises at
  `resolve()` with the unknown-param message.
- `aurora(huue=…)` still raises `KeyError` (unknown-param strictness preserved).

## 5. mm-terrarium module layout

Split along the line the repo already uses for `DeviceLinkServer` /
`DeviceLinkAgent` and `WebSocketTransport` / `FakeTransport`: transport-agnostic
brains in one module, the concrete backend in another.

### `control/audio.py` (new, pure, never imports pyarco)

The Control-side fan-out. Boundary rule 1 ("single writer to `/arco`") puts the
decision-making here, and keeping it pyarco-free is what keeps the offline suite
green.

Contents:

- **`DeviceVoice` protocol** (duck-typed, no ABC): `note_on(key, vel)`,
  `note_off(key)`, `control_change(num, val)`, `program_change(prog)`,
  `all_off()`. **No channel parameter anywhere in this API.** See section 9.1.
- **`SynthPool` protocol**: `acquire() -> DeviceVoice`, `release(voice)`,
  `poll()`, `shutdown()`. The pool is what a concrete backend implements.
  `poll()` is the backend's chance to pump whatever transport it has, and is a
  no-op for the fake.
- **`AudioBridge`**: holds `dev -> voice`. Its surface mirrors
  `harness/device_bridge.py`, which is the light-side sibling:
  - `on_grant(join_result)`: reads the role's `ugen_manifest`, acquires a voice,
    sends `program_change`, plays the welcome audio cue (section 7), and records
    the role's declared cc lanes for this device.
  - `start_drone(dev)` / `stop_drone(dev)`: the sustained note, tied to the Bit's
    RUNNING window.
  - `feed_midi(dev, status, d1, d2)`: applies the role's declared lanes. A
    `cc:<n>` source with no matching lane is dropped, not forwarded.
  - `on_release(dev)`: note-off the drone, `all_off()`, release the voice.
  - `tick(now)`: fires any due timed note-offs (the welcome cue's `duration`)
    and calls `pool.poll()`. This is the single call the driver loop makes per
    iteration, so there is exactly one place that pumps the audio side.
  - `shutdown()`: release every voice. Boundary rule 1's "owning the id space
    also means freeing it at Bit unload".

`AudioBridge` is fully offline-testable against a `FakeVoice` that records the
MIDI it was handed.

### `harness/arco_synth.py` (new, pyarco-backed, dev/test-only)

The concrete `SynthPool`. `from pyarco...` imports happen **lazily inside a
factory function**, never at module import, so importing the module costs
nothing when Arco is absent.

Owns:

- `arco.initialize(...)` (blocking, with the existing 30 s timeout surfaced as a
  clear error if the server is not running),
- one shared `Flsyn` instance built from the soundfont path, `.play()`ed once,
- **channel allocation** from a 0..15 free list, one channel per voice,
- `poll()`, reached from `AudioBridge.tick`, delegating to `sched.poll()`
  (which in turn pumps o2lite via the poll function `arco.initialize` registers),
- teardown: `all_off()` per channel, drop the `Flsyn` reference so pyarco frees
  the Arco ugen id, then `arco.finish()`.

Putting the pyarco-touching half in `harness/` rather than `control/` is a
**deliberate holding position, not where it belongs long-term**. It matches how
luxaeterna is currently carried (dev/test-only, `requirements-dev.txt`,
`importorskip` in tests) and it keeps `control/` importable with nothing
installed. It moves into `control/` once pyarco's source-of-truth is settled,
which is bootstrap open question #1 and Roger Dannenberg's call. This spec does
not pre-empt that decision.

### `harness/led_smoke.py` (modified)

New flags, all opt-in:

- `--audio` (default off). Without it, `python -m harness.led_smoke` behaves
  **exactly** as it does today, including in CI and on a box with no Arco.
- `--soundfont PATH` (default `$MM_SOUNDFONT`, then
  `/Users/chris/projects/fluidsynth/sf2/VintageDreamsWaves-v2.sf2`).
- `--program N` (default a sustained pad; the concrete number is chosen during
  implementation by listening, see section 10).

The driver loop gains the `cc:11` breath generator alongside the existing
`cc:74` ping-pong, and calls `audio.tick(now)` each iteration when `--audio` is
on. Both controllers go to `session.feed_midi(...)` **and**
`audio.feed_midi(...)` from the same statement, so there is one obvious place a
reader can confirm the stream is shared.

## 6. TestBit: `ugen_manifest` v0

`bits/test_bit.py`'s `player` role gains:

```python
ugen_manifest={
    "instruments": [
        {"instrument": "flsyn", "program": 89,
         "drone": {"key": 45, "velocity": 90},
         "lanes": [{"source": "cc:74", "dest": "cc:74"},
                   {"source": "cc:11", "dest": "cc:11"}]},
    ],
}
```

and its `light_manifest` gains the level param and lane:

```python
light_manifest={
    "instruments": [
        {"instrument": "aurora", "target": "primary",
         "params": {"hue": 0.33, "level": 0.55},
         "lanes": [{"source": "cc:74", "dest": "hue"},
                   {"source": "cc:11", "dest": "level"}]},
    ],
}
```

`jammer` keeps empty defaults, so the no-audio and no-light paths both stay
exercised.

Notes on the shape:

- `dest` stays in `cc:<n>` form, symmetric with `source`. There is no named
  destination vocabulary ("brightness", "expression") to invent and then regret;
  the mapping to FluidSynth is already a cc number. The lanes read as identity
  forwarding because today they are. The lane exists so a role **can** remap a
  gesture to a different synth parameter, which is the seam worth having. The
  "audio consequence" column in section 3's table is FluidSynth's own reading of
  those cc numbers, not something this manifest names.
- `Role.ugen_manifest` changes from `list` to `dict`, matching `light_manifest`,
  with `field(default_factory=dict)`. Blast radius: three tests assert `== []`
  (`tests/test_test_bit.py` x2, `tests/test_roles.py` x1), and
  `console/protocol.py` plus `console/static/index.html` pass it straight through
  as JSON, so the console needs no change.
- Validation is **shallow and separate** from `control/role_config.py`'s v2
  validator: a small `validate_ugen_manifest` that checks the top-level shape and
  locates errors the same way, called at `load_bit` so a typo'd Bit fails as a
  `BitLoadError` rather than mid-installation. It does **not** get folded into
  the composed `/ie<N>/role` blob: audio is Control's business, never the
  device's (boundary rule 1).

## 7. The welcome audio cue

`Role.welcome["audio"]` has been declared since PR #5
(`{"instrument": "chime", "duration": 1.5}`) with no consumer, and the deep-dive
lists "the Arco cue path that plays the welcome audio half" as unbuilt. This
slice builds the first version of it.

`AudioBridge.on_grant` plays the cue on a **second channel**, so the sustained
drone is never disturbed by it. Instrument names map through a small provisional
table in `control/audio.py` (`{"chime": (program, key, velocity)}`), the same way
`light_manifest` instrument names are opaque luxaeterna registry names. Its
concrete numbers are picked during implementation by listening, the same as
`--program` in section 10; an unknown instrument name is a `BitLoadError` at
validation, never a silent no-sound. The `duration` field schedules a note-off,
tracked by `AudioBridge` and fired from `tick(now)`, so no scheduler dependency
leaks into `control/`.

This lands the welcome ceremony as audible and visible together, which is the
reason both halves were declared in one place.

## 8. Testing

**The offline suite must still pass with no Arco server and no pyarco.** This is
a hard requirement, not a nice-to-have.

- `tests/test_audio.py` (new, **no** importorskip): every `AudioBridge` decision
  against a `FakeVoice`/`FakePool`. Lane application and remapping; a cc with no
  declared lane is dropped; drone note-on at start and note-off at release;
  welcome cue on a separate channel and its timed note-off; `on_release` frees
  the voice; `shutdown()` frees every voice; a role with an empty
  `ugen_manifest` (jammer) acquires nothing.
- `tests/test_roles.py`, `tests/test_test_bit.py`: updated for the `dict`
  default and the new declarations.
- `tests/test_role_config.py`: cases for `validate_ugen_manifest`, including a
  typo'd Bit failing as `BitLoadError` with a located message.
- `tests/test_led_smoke_cli.py`: `--audio` defaults off; the breath generator
  produces the `_AURORA_BREATHE` shape at the right period and floor; `build()`
  without `--audio` constructs no audio objects at all.
- `tests/test_arco_synth.py` (new, `importorskip("pyarco")` **and** skip unless
  an Arco server is reachable): one thin integration test that acquires a voice,
  sends a cc, and releases it.

Guard against regression on the shared-stream property specifically: a test that
feeds one cc event and asserts **both** the light session and the audio bridge
saw the same value. That is the property this whole slice exists to establish,
so it deserves a test that fails loudly if someone later splits the stream.

## 9. What is provisional, and why it is marked so

### 9.1 The `Synth` abstraction is NOT frozen here

The channel question is open and out with Roger Dannenberg. His written notes
argue against a channel parameter (allocate up to 16 `Synth`s sharing one
`Flsyn`); his shipped `MidiSender` in `apps/pytest/miditest.py` takes `chan` on
every method. Those two disagree about the **API**, not the implementation:
16 `Synth`s sharing one `Flsyn` still means each one holds a channel internally.

So this design makes the channel **real but internal**. Callers write
`voice.control_change(74, val)`, never `voice.control_change(chan, 74, val)`.
That is his written direction at the surface, and if the channel-parameter
version wins instead, the change is confined to `harness/arco_synth.py`.

The type is named `DeviceVoice`, not `Synth`, so nobody reads this repo as
having landed the abstraction. A comment in `control/audio.py` states the open
question and points here.

### 9.2 `ugen_manifest` v0 is not the audio-manifest freeze

`light_manifest` v2 was frozen by a dedicated spec and a shared wire contract
with luxaeterna. This is not that. `ugen_manifest` v0 is the smallest thing that
lets a role declare its own audio, validated shallowly, with no cross-repo
contract and no device-side parser. The real Flsyn-parameterizing schema is a
separate spec, and this one must not be cited as precedent for its shape.

### 9.3 New dependency, stated plainly

mm-terrarium gains a **dev/test-only** dependency on pyarco, reached by
`PYTHONPATH` rather than by pip, plus `zeroconf` and `netifaces` in
`requirements-dev.txt` under an optional heading. This follows the precedent
already set for luxaeterna and is called out here rather than decided silently,
because "real ugen graph-building on Arco" is listed under *Not yet built* in
`docs/MM_TERRARIUM.md` and this slice moves that line.

Nothing is vendored and nothing is submoduled. pyarco's source-of-truth stays
Roger's open decision.

## 10. Verification

Verification is running it, not asserting it should work.

**Sequencing.** luxaeterna lands first (the same order the aurora spec used).
The mm-terrarium work is blocked on `aurora`'s `level` param existing in the
sibling checkout at `/Users/chris/projects/luxaeterna`.

**Manual run.** Chris starts the Arco server in a real terminal (it is a curses
app and cannot be launched from a tool call):

**RUN ON: MYCOLOGICAL**

```bash
cd /Users/chris/projects/arco/apps/pytest && ./server
```

then the demo:

**RUN ON: MYCOLOGICAL**

```bash
cd /Users/chris/projects/mm-terrarium && PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.led_smoke --audio --hold
```

**Acceptance, judged by watching and listening at once:**

1. The Shroom breathes in the browser exactly as it does today, and the sound
   swells and fades **with** that breath, not near it.
2. As the hue glides through the `cc:74` ping-pong, the timbre moves with it.
3. The welcome chime is heard at grant, at the same moment the glow welcome is
   seen.
4. On completion the drone stops and the light fades.
5. `python -m harness.led_smoke` with no `--audio`, and with the Arco server
   stopped, behaves exactly as before.
6. `python -m pytest tests` passes with the Arco server stopped.

**Open risk with a named fallback.** `--program 89` is a guess at a sustained
pad in VintageDreamsWaves-v2. If it turns out percussive (no sustain, so nothing
for the breath to swell) or unresponsive to `cc:74`, the fix is to pick a
different program by listening during implementation, not to change the design.
The `cc:11` swell is program-independent, so acceptance criterion 1 holds
regardless.

## 11. Explicitly out of scope

- `harness/devicelink_smoke.py`. The fan-out is built as a reusable
  `control/audio.py`, so wiring `DeviceLinkAgent` to it is a small follow-on
  slice, not part of this one. Deferred so the first audible run needs only the
  Arco server, with no browser simulator driving tilt.
- Real o2lite transport, an Arco-side `/ie<N>` path, and multi-device audio
  beyond what the 16-channel pool gives for free.
- The audio-manifest v1 schema (section 9.2).
- Scoring, `on_complete()` consequences, and any production Bit.

## 12. Documentation updates required

- `docs/MM_TERRARIUM.md`: the `harness/` section (led_smoke now optionally makes
  sound); the `bits/` section (the running declaration gains a `cc:11 -> level`
  lane, and the "nothing feeds note-ons" sentence becomes "the light declaration
  still has no note lane, and the drone note-on the audio path adds is ignored by
  light"); *Relationships to other repos* (pyarco is no longer "no dependency
  yet"); *Not yet built* (real ugen graph-building moves from unbuilt to a first
  provisional slice, `ugen_manifest` is no longer a bare placeholder, and the
  welcome audio half now has a consumer).
- `docs/control-gameserver-design.md`: only if a documented contract or
  constraint actually changed. Boundary rule 1 is honored, not amended, so the
  likely edit is a short note under the instrument discussion recording that a
  provisional Control-side voice abstraction exists and that the channel question
  remains open with Roger. Do not restate this spec there.
- luxaeterna's own deep-dive and its aurora spec: note the additive `level` param
  and that omitting it preserves the self-breathing behavior.
