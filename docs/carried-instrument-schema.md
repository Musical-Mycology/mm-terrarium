# Carried-instrument wire schema

The cross-repo contract for a device declaring which instrument it carries.
The Python side is `control/instrument.py` (the `Instrument`/`DEFAULTSHROOM`/
`TUNESHROOM` definitions), `control/engine.py` (`GameServer.hello`'s
resolution), `control/device_pool.py` (`DevicePool.hello`'s heartbeat rule)
and `control/role_config.py` (`compose_role_config`'s blob stamp); the wire
encoding is `devicelink/agent.py` (websocket) and `devicelink/o2_transport.py`
/ `harness/o2_shroom.py` (o2lite). **Change them together.**

Design: [`docs/superpowers/specs/2026-08-31-carried-instrument-wire-design.md`](superpowers/specs/2026-08-31-carried-instrument-wire-design.md).

## Wire: `/game/hello` gains an optional 4th argument

```
/game/hello   "s"     dev
/game/hello   "ssss"  dev  name  protoversion  instrument
```

The 3-arg-plus form is the pre-existing shape; `instrument` is new and
optional on both transports:

- **Websocket (`devicelink/agent.py`'s `_on_hello`)** already indexes `args`
  defensively (`args[1]`, `args[2]`, `args[3]`, each falling back when
  absent), so an old 1-arg or 3-arg hello and a new 4-arg hello both parse
  with no dispatch change.
- **o2lite (`devicelink/o2_transport.py` / `harness/o2_shroom.py`)** sends
  either `"s"` (no instrument) or `"ssss"` (instrument declared) --
  `GAME_VERBS`' typespec match-any registration accepts both without a
  protocol version bump.

A client that has nothing to declare keeps sending the old shape; nothing
about the old wire behavior changes for it. A client declaring an instrument
sends the 4-arg form with `name`/`protoversion` in their existing slots and
the instrument name at position 3:

```
["ie7", "testshroom", "1.0", "tuneshroom"]
```

## Resolution: `GameServer.hello`

`GameServer.hello(dev, name, protoversion, instrument=None)` resolves the
declared name against `self.carried_instruments` (code constants
`TUNESHROOM`/`DEFAULTSHROOM` plus any config/catalog instrument threaded to
the engine at boot):

| `instrument` arg | Resolution | Console-visible |
|---|---|---|
| A name found in `carried_instruments` and declaring `light.pixels` | That `Instrument` | No |
| A name found in `carried_instruments` but **not** declaring `light.pixels` (e.g. a Room fixture like `light.surface`, no gestures) | `DEFAULTSHROOM` | Yes -- `on_device_warning` fires, naming the device and instrument and that it is not carriable (no `light.pixels`) |
| A name **not** found (unknown/typo'd) | `DEFAULTSHROOM` | Yes -- `on_device_warning` fires, logged as a `warn` `log_event` naming the device and the unresolved name |
| Absent (arg omitted, 3-arg or 1-arg hello) | `DEFAULTSHROOM` | No -- this is the documented legacy meaning, not an error |

Both warning cases above are deduped per `(dev, declared name)`: a device's
`hello` fires again roughly every 5s on the o2 heartbeat, so warning on
every occurrence would flood the console log. `GameServer` keeps a small
`self._warned_instruments` set and warns only the first time a given
`(dev, name)` pair fails to resolve. An entry is dropped -- so the next
occurrence warns again -- the moment that dev's declaration changes to a
different name, the current name resolves successfully, or the dev is
removed by `reap_stale`. This means a fixed declaration that later
regresses (the config entry disappears again, or the fixture is
re-declared as carried) warns again rather than staying silently
suppressed.

### The heartbeat preservation rule

`DevicePool.hello(dev, name, protoversion, now, carried=None)` receives
`carried=None` from `GameServer.hello` in exactly two cases: an
actually-undeclared hello, and o2lite's periodic liveness re-hello (which
re-sends the client's declaration on its own timer, but any transport that
bypasses that re-send still reaches `DevicePool.hello` with nothing new to
say). The rule that keeps those two cases from colliding:

- **Unknown dev, `carried=None`** -- falls back to `DeviceInfo`'s own
  default, `DEFAULTSHROOM`.
- **Known dev, `carried=None`** -- preserves that entry's existing
  `carried` value. A re-hello with nothing new to say is proof of life, not
  a fresh declaration that resets the device back to the floor.
- **Any dev, `carried=<Instrument>`** -- always wins; re-hello with a
  different declared name re-resolves.

A client that re-hellos without re-declaring its instrument (a bare
heartbeat) therefore never gets silently demoted to `defaultshroom` between
declarations.

## Blob: the `"instrument"` section

The composed `/ie<N>/role` blob (`control/role_config.py`'s
`compose_role_config`) gains an `"instrument"` key for every granted
**non-ROOM** join, next to the existing `"triggers"` key. It ships the
resolved instrument's full definition, not just its name -- a generic host
never needs a second lookup to render its idle look or its fireable
vocabulary:

```jsonc
"instrument": {
  "name": "tuneshroom",
  "capabilities": ["audio.mic", "audio.samples", "gesture.tap",
                    "gesture.tilt", "light.pixels"],
  "pixels": 12,
  "ambient": {
    "light": {},
    "ugen": {}
  },
  "functions": [
    {"kind": "scripted", "name": "play_aurora",
     "description": "Hue bloom on the handheld's ring",
     "target": null, "condition": null,
     "script": [{"offset": 0.0, "kind": "light", "dev": "target",
                 "status": 176, "data1": 74, "data2": 127},
                {"offset": 1.0, "kind": "light", "dev": "target",
                 "status": 176, "data1": 74, "data2": 0}]}
    /* ... one entry per declared Function, function_view's wire shape */
  ]
}
```

| Field | Rule |
|---|---|
| `name` | The resolved instrument's catalog name. |
| `capabilities` | Sorted list of `CAPABILITY_VOCABULARY` tags. |
| `pixels` | Int; 0 only for an instrument that never declared `light.pixels`. |
| `ambient` | `{"light": ..., "ugen": ...}`, each the instrument's own manifest verbatim (deep-copied -- a host may render this but must never mutate the server's copy). Empty dict when the instrument declares no ambient manifest of that kind. |
| `functions` | Every declared `Function`, kind-tagged, `function_view`'s existing wire shape (the same shape the Console's function cards already consume). |

### The published-only rule

Only **published** catalog instruments (code constants, plus
`TerrariumConfig.instruments` and any catalog entry threaded to the engine
at boot) resolve. A draft instrument in `instruments/drafts/` is bench/
browser territory only -- it is never reachable by a real hello, and a
device declaring a draft's name resolves the same as any other unknown
name: `DEFAULTSHROOM`, with a console warning.

### The 12-LED floor

`validate_instrument` refuses any instrument that declares `light.pixels`
with `pixels < 12` -- enforced once, at publish/config-load time, never
discovered on a device at runtime. `DEFAULTSHROOM` itself sits exactly on
the floor (`pixels = 12`), so the worst case a hello can resolve to is
still a 12-LED-capable host. An instrument with no `light.pixels`
capability (e.g. a fixed installation surface like `venue_array`) is
exempt -- the floor is about handheld/carried hosts, not every instrument
in the vocabulary.

## Compatibility

**mm-tuneshroom MUST send `"tuneshroom"` on hello before this deploys to a
real room, or hardware becomes a DefaultShroom.** Before this branch,
`DeviceInfo.carried` defaulted to `TUNESHROOM` unconditionally -- every
hello'ing device was presumed to be a Tuneshroom with no wire vocabulary to
say otherwise. That default has flipped to `DEFAULTSHROOM`. A real
Tuneshroom that has not been updated to declare its name on hello will:

- still join and function (capabilities overlap: both instruments declare
  `light.pixels`/`gesture.tap`/`gesture.tilt` at 12 pixels), but
- lose `audio.mic`, `audio.samples`, and every TUNESHROOM-specific
  scripted Function (`play_aurora`, `win`, `fireworks_player`,
  `fail_player`, `metro_pulse_player`, `metro_recovery`) -- none of those
  are declared on `DEFAULTSHROOM`, so a Bit firing them at a silently-
  demoted Tuneshroom hits the fire-ladder's load-gap warning path instead
  of the real cue.

This is a breaking change gated on a cross-repo follow-up, not a
same-branch fix: the client-side declaration lives in mm-tuneshroom, not
this repo. Do not deploy this branch against a real room, and do not treat
existing hardware as compatible, until mm-tuneshroom's hello sends its
name.
