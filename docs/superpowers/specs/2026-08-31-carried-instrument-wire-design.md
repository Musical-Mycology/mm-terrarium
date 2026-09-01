# Carried-instrument wire support: devices declare what they are

**Date:** 2026-08-31
**Status:** Approved design, pre-plan. Brainstormed with Chris; slice 3 of
the Design panel arc (spec `2026-08-31-design-panel-and-instrument-catalog-design.md`
section 2 named this the trailing slice).

## Problem

Every hello'ing device is presumed to carry `TUNESHROOM`
(`DeviceInfo.carried` defaults to it; there is no wire vocabulary for a
device to say otherwise). The instrument catalog, the Design panel, and
the bench all treat instruments as first-class data, but no real device
can *become* one: the definition never crosses the wire. A generic
instrument host (Testshroom, phone, future hardware) should declare which
published catalog instrument it carries and receive that instrument's
definition from the server, which stays the single authority.

## Decisions (from brainstorm)

| Question | Decision |
|---|---|
| Blob scope | Full definition ships down: capabilities (including audio record/play), pixels, ambient manifests, functions, triggers |
| Audio I/O | Expressed as capability tags; `audio.mic` added for recording, `audio.samples`/`audio.flsyn` already cover playback. No format negotiation beyond tags |
| Fallback | EVERYTHING unresolved falls to a new `DEFAULTSHROOM` -- unknown names warn on the console, absent names are the documented legacy meaning and stay silent. `TUNESHROOM` applies only when explicitly declared |
| Breaking change | A legacy device sending no name stops being presumed a Tuneshroom. mm-tuneshroom must ship its name declaration before this reaches a real room; recorded as a cross-repo follow-up |
| 12-LED floor | "Any instrument in the ecosystem has at least 12 LEDs" is enforced: `Instrument.pixels` required with `light.pixels`, `pixels >= 12` validated at publish/load |
| Drafts | Published catalog entries only; drafts stay bench/browser territory |
| Cross-repo scope | This slice ships the mm-terrarium side, the contract doc, and Testshroom harness support. The mm-tuneshroom client change is a spawned follow-up |

## 1. Model and vocabulary

- **`DEFAULTSHROOM`** (`control/instrument.py`, sibling constant to
  `TUNESHROOM`): the ecosystem floor, modeled on the Testshroom -- name
  `"defaultshroom"`, 12 pixels, capabilities
  `{light.pixels, gesture.tap, gesture.tilt}`, `accepted_cues` all four
  kinds, TUNESHROOM's tap/shake event triggers (same provenance caveat:
  guessed values awaiting a capture pass), and a minimal ambient light
  manifest (a dim aurora-style glow) so an idle unknown device is visibly
  alive rather than dark. Shipped as `instruments/defaultshroom.toml`,
  pinned equal to the constant by test, exactly the TUNESHROOM pattern.
- **`audio.mic`** joins `CAPABILITY_VOCABULARY`: the device can record
  audio. Playback stays `audio.samples` (local sample playback) and
  `audio.flsyn` (an Arco FluidSynth voice reachable). The capability set
  in the blob is how a Bit or operator learns what a device can record
  and play; richer audio descriptors are out of scope.
- **`Instrument.pixels`** (int, default 0 meaning undeclared): required
  whenever `light.pixels` is declared, and `validate_instrument` refuses
  `pixels < 12` for such instruments -- the floor is enforced at
  publish/config-load, never discovered on a device. Config vocabulary:
  a top-level `pixels = <int>` key on the instrument table, parsed by
  `_parse_instrument`. Instruments without `light.pixels`
  (e.g. `venue_array`, which declares `light.surface`) are untouched.
- **`TUNESHROOM`** gains `pixels = 12` and `audio.mic` (its mic is what
  the capture path already records). `instruments/tuneshroom.toml`
  updated to stay pinned-equal.

## 2. Wire: hello declares the carried instrument

- `/game/hello` gains an optional 4th argument: the carried instrument's
  catalog name (string). Both transports tolerate its absence -- the
  websocket agent already indexes args defensively, and the o2lite
  transport accepts both the old `"sss"` and new `"ssss"` typespecs.
- `GameServer.hello(dev, name, protoversion, instrument=None)` resolves:
  - a name matching a published carried-instrument definition (the
    code-defined constants plus the config/catalog instruments threaded
    to the engine at boot) -> that `Instrument` becomes
    `DeviceInfo.carried`;
  - an unknown name -> `DEFAULTSHROOM`, with a console-visible warning
    line naming the device and the unresolved name (the fire-ladder
    load-gap warning convention);
  - absent -> `DEFAULTSHROOM`, silent. This is the documented legacy
    meaning from now on.
- `DeviceInfo.carried` default flips from `TUNESHROOM` to
  `DEFAULTSHROOM`. Everything downstream that already reads `carried`
  (join-time `requires` slot checks, stream-trigger transforms, the
  role blob's `triggers` key) picks the declared instrument up with no
  further changes.
- Re-hello with a different name re-resolves; a joined device's carried
  instrument still only takes effect for checks made at their usual
  times (join-time slots at join, stream transforms per gesture), same
  as today.

## 3. Blob: the definition ships down

The composed `/ie<N>/role` blob gains an `"instrument"` section next to
the existing `"triggers"` key, for every granted non-ROOM join:

```json
"instrument": {
  "name": "...",
  "capabilities": ["..."],          // sorted
  "pixels": 12,
  "ambient": {"light": {...}, "ugen": {...}},
  "functions": [ ... ]              // existing function wire-view shape
}
```

A generic host can render the instrument's idle look from `ambient`,
expose its fireable vocabulary from `functions`, detect with the
`triggers` it already receives, and size its surface from `pixels`.
ROOM-class joins ship nothing new, matching the triggers key's existing
rule. Composition happens in the same `compose_role_config` path the
blob already uses; the section reflects `DeviceInfo.carried` at grant
time.

## 4. Engine plumbing

The engine already builds a carried-instruments dict at load
(`{TUNESHROOM.name: TUNESHROOM}` today, control/engine.py:336). It
becomes: code constants (`TUNESHROOM`, `DEFAULTSHROOM`) plus the
`TerrariumConfig.instruments` union, threaded to `GameServer` at
construction the same way other config products already are. The console
snapshot's existing device view gains the carried-instrument name per
device so an operator can see what each device declared (the fixtures
view already shows instruments; this is the carried-side sibling).

## 5. Harness and cross-repo contract

- Testshroom (`harness/shroom_client.py` and `harness/o2_shroom.py`)
  gains an `--instrument <name>` flag that sends the declaration on
  hello; the devicelink and o2 smoke paths assert the blob's
  `"instrument"` section round-trips (name, pixels, capabilities) for a
  declared name, and that an undeclared Testshroom lands on
  `defaultshroom`.
- **`docs/carried-instrument-schema.md`**: the cross-repo wire and blob
  contract mm-tuneshroom implements against -- hello argument, fallback
  semantics, the instrument section's shape, and the 12-LED floor --
  following the `docs/telemetry-trace-schema.md` pattern.
- **mm-tuneshroom follow-up (required before a real room)**: the client
  must send `"tuneshroom"` on hello or real hardware silently becomes a
  DefaultShroom. Spawned as its own task at closeout and named in the
  contract doc's compatibility section.

## 6. Testing

- Constant/file equality tests for `DEFAULTSHROOM`; floor-validation
  tests (12+ passes, 11 refused, non-light.pixels exempt).
- Hello resolution tests on all three branches (declared/unknown/absent)
  over both transports, including the console warning on unknown.
- Blob composition tests for the instrument section, including the
  ROOM-join exclusion.
- Harness round-trip smoke (websocket path in the offline suite; o2lite
  path joins the live-Arco checklist).
- The suite stays fully offline; no luxaeterna or pyarco touched.

## Status

Implemented 2026-08-31, on this branch (`claude/carried-instrument-wire`).
Deep-dive: `docs/MM_TERRARIUM.md`'s *Carried-instrument wire* entry.
Contract doc: `docs/carried-instrument-schema.md`. Full suite:
`.venv/bin/python -m pytest tests -q` -> **1871 passed, 1 skipped**.

Outstanding: the mm-tuneshroom follow-up named in section 5 (the client
must send `"tuneshroom"` on hello) is not part of this repo and has not
landed. This branch's breaking change (the carried default flipping from
`TUNESHROOM` to `DEFAULTSHROOM`) must not reach a real room or real
hardware until that follow-up ships -- to be spawned as its own task at
closeout, not fixed here.

## 7. Out of scope (recorded)

- Draft instruments on the wire.
- mm-tuneshroom client changes (spawned follow-up).
- Device-side rendering of the shipped ambient manifest in existing
  clients (the contract doc defines it; hosts adopt at their own pace).
- Audio format negotiation beyond capability tags.
- Per-device pixel-count overrides (the instrument declares its shape;
  hardware variance is a future concern).
