# Per-fixture light sessions, fixture addressing, and the rooms catalog

Status: approved in brainstorm with Chris, 2026-09-01. Successor to the
per-fixture instruments slice (PR #81, spec
2026-09-01-per-fixture-instruments-and-diagnostics-design.md section 9).
Reverses non-goal N1 of the N-fixture Room spec (2026-08-18) by decision.

## 1. Problem

PR #81 made each Room fixture a real instrument for audio and for the
diagnostics built-ins, but kept the Room's light side as ONE shared
LightSession over the whole concatenated profile, sliced per fixture at
send time. Consequences:

- The transport drops the light half of any MIDI cue addressed to a
  non-canonical fixture dev (`_on_light_cue` returns after queueing the
  audio half). A scripted light function fired at the accent has audio
  but no light.
- `fire_function` rung 1 collapses ROOM/@all/PLAYERS fan-out to the
  canonical dev (`_collapse_room_fanout`), and `_suppress_generator_lanes`
  folds only the canonical dev back to the ROOM sentinel. A rung-1 SURFACE
  script fired at a non-canonical fixture while a Room GENERATOR runs the
  same lane is never suppressed. Inert today only because of the transport
  drop above.
- A ROOM-addressed SolidCue paints only the canonical fixture's slice.
- A Bit cannot address a fixture by name at all, and the Room renders
  nothing until a device binds, so the Console cannot show what a loaded
  Room's instruments should be doing without simulators running.

## 2. Decisions (made with Chris, 2026-09-01)

1. A Room is an ordered list of named fixtures and nothing else. It owns
   no light session and no audio channel. Fixture order is a configuration
   property, edited in the Design tab and applied at the next Room load.
   The Room has no knowledge of where fixtures are physically placed.
2. Each fixture is a complete instrument in both halves: its own
   LightSession over its own strip with local zone names, and its own
   Arco voice (already landed in PR #81).
3. A Bit is written against the Room spec. It addresses fixtures by name
   with the string sentinel `@fixture:<name>`, uses `@room` to mean every
   fixture, and `@target` as today. Control validates at `load_bit` that
   every fixture name a Bit's declarations address exists in the loaded
   Room. Non-goal N1 of the N-fixture spec is reversed.
4. All timing is O2 time. Control stamps every cue's `when` off the one
   injected clock, as today. luxaeterna sessions render at that clock's
   reading directly, with no private per-session epoch.
5. Cross-fixture effects are authored, not inferred. A chase is a SCRIPTED
   function stepping fixtures at offsets; a continuous effect spanning
   fixtures is per-fixture manifest declarations with the author's chosen
   params, coherent because they share O2 time. No room-axis geometry
   concept is introduced.
6. Room load loads instruments; devices are outputs. Every declared
   fixture has a session (and a voice, when audio-capable) from Room load,
   bound or not. The Console strip is the primitive visualization and
   needs no simulator. A bound device is one output sink among possibly
   several.
7. Rooms get a file catalog mirroring the instrument catalog. TEST and
   DEMO migrate out of `terrarium.toml` into `rooms/`. The Design tab gains
   a stubbed Room editor (raw TOML plus a fixture-order form). Not a
   real-time control.
8. The device-originated interrupt (flash-on-hit without a Control round
   trip) is pinned as a contract in section 8 and built in a named
   follow-up slice, not here.

## 3. Addressing and validation (control/cues.py, control/functions.py, control/role_config.py, control/engine.py)

### 3.1 Sentinels

`control/cues.py` gains:

```python
FIXTURE_PREFIX = "@fixture:"

def fixture_dev(name: str) -> str: ...      # "@fixture:" + name
def fixture_name(dev: str) -> str | None: ...  # name, or None if not a fixture sentinel
```

These are the only places the prefix is spelled. `ROOM` (`@room`),
`TARGET` (`@target`) and `ALL` (`@all`) are unchanged.

### 3.2 Legal devs by owner

| Owner       | Script steps, SolidCue, MuteCue, PlayCue | Generator lane dev | Stream output dev |
|-------------|------------------------------------------|--------------------|-------------------|
| Bit         | `@target`, `@room`, `@fixture:<name>`    | same               | same              |
| Instrument  | `@target`, `@room`                       | `@target`, `@room` | `@target`, `@room` |

An Instrument is a type and cannot know a Room's fixture names, so
`@fixture:` is refused on instrument-owned functions with a located error
in `validate_function_table`'s `owner == "instrument"` branch. Fixture
names in the sentinel must match `[A-Za-z0-9_-]+` (same as catalog names).

### 3.3 Manifest slicing rule

The Bit ROOM role's `light_manifest` keeps its shape (`{"instruments":
[...]}`, each decl with `instrument`, `target`, `params`, `lanes`). Each
decl binds by `target`:

| target              | binds on                                  | as local target |
|---------------------|-------------------------------------------|-----------------|
| `primary`           | every fixture, its whole strip            | `primary`       |
| `<fixture>`         | that fixture, its whole strip             | `primary`       |
| `<fixture>.<zone>`  | that fixture, that zone                   | `<zone>`        |

Any other target is a `BitLoadError` naming the decl index and the
unknown fixture or zone. `role_config.slice_light_manifest(manifest,
profile, fixture_name) -> dict` performs the slice; it is pure and lives
in `control/`. Ugen manifests are not sliced (audio grants are already per
fixture; the whole ugen manifest is what each audio-capable fixture's
voice receives, as PR #81 left it).

Ambient manifests (`control/instrument.py`) are already per fixture in
shape: fixture F's session receives F's own instrument's
`light_manifest`, with its targets resolved against F's local zones
(`primary` and F's own zone names). `ambient_manifests(profile)` is
replaced by a per-fixture accessor.

### 3.4 Load-time fixture contract

`GameServer.load_bit` derives the set of fixture names the Bit addresses:
every `@fixture:<name>` dev in the FunctionTable (script steps, generator
lanes, stream outputs) plus every fixture-scoped manifest target. Any name
absent from the loaded Room's profile refuses the load with a
`BitLoadError` listing the missing names and the Room's fixture names. The
existing implicit `"room"` slot keeps handling capabilities and
`min_pixels`. No new Bit declaration is added.

### 3.5 Lanes are per fixture

A lane is `(resolved fixture dev, status, data1)` everywhere. Generator
lane uniqueness is checked after expansion against the loaded Room at
`load_bit`: a `@room` generator on cc:74 and a `@fixture:accent`
generator on cc:74 collide (both write the accent's cc:74); two fixtures'
ambient generators on the same cc do not, since each writes its own
fixture's lane. `RoomProfile.__post_init__`'s cross-fixture generator
lane collision rule is deleted. The declaration-time check in
`validate_function_table` (no two GENERATOR functions with identical
declared lane) stays as a cheap first gate.

## 4. Engine data flow (control/engine.py, control/generator_runner.py)

- `_resolve_dev(dev) -> list[str]`: `@fixture:<name>` resolves to that
  fixture's bound dev when bound, else to an empty list with a
  once-per-load warning. `@room` resolves to every bound fixture dev in
  list order. Any other dev passes through as a one-element list.
  `_dispatch_cues` fans one cue per resolved dev. Every caller of the old
  scalar `_resolve_dev` is updated to the list form.
- Deleted: `GameServer._canonical_room_dev`, `_collapse_room_fanout`, the
  `explicit_surface` special case in `fire_function` (there is nothing to
  collapse), and the canonical fold-back inside `_suppress_generator_lanes`.
  Suppression keys are the real fixture lanes the expanded cues write.
- `GeneratorRunner` is constructed with a resolver so `cues()` emits one
  tuple per resolved fixture dev per declared lane, and `suppress()` keys
  per fixture lane. A scripted fire at the accent suppresses only the
  accent's lane; the generator keeps driving main. This closes the parked
  finding from PR #81's review.
- Stream expansion (`stream_cues` / `collect_stream_cues`) resolves output
  devs the same way.
- `_check_cue_kinds` checks each resolved fixture dev against ITS fixture's
  instrument, not "any fixture accepts".
- Ambient generators: an instrument's GENERATOR with dev `@target` resolves
  to the declaring fixture; `@room` resolves to every fixture.
- `_resolve_target` is unchanged in shape. `FunctionFired.devs` continues
  to report resolved devs; for a `@room`-addressed step the record lists
  every bound fixture dev.

## 5. Transport data flow (devicelink/agent.py, control/room_bridge.py, control/fixture_sink.py)

### 5.1 Sessions per fixture, from Room load

`_setup_room` builds, for EVERY fixture in the profile (bound or not):

- a `LightSession` from `to_fixture_capability(profile, fixture.name)`
  and the fixture's manifest slice (Bit ROOM role slice when a ROOM role
  is loaded, else the fixture's ambient manifest, else an empty manifest);
- an Arco voice grant when the fixture's instrument declares any
  `audio.*` capability. Grants are keyed by FIXTURE NAME (the key is
  opaque to `AudioBridge`), so the grant no longer waits for a binding;
  the transport translates a resolved fixture dev to its fixture name at
  the feed seam (`_on_light_cue`'s audio push, `_on_mute_change`'s
  silence, the drone loop). `_room_audio_devs` becomes a set of fixture
  names. The welcome ceremony still plays once, on the first granted
  fixture.

State: `self._fixtures: dict[str, FixtureState]` keyed by fixture name,
each holding session, universe, controllers read-out (`dict[int, int]`,
the per-fixture successor to `RoomBridge.controllers`), pending `when`,
last frame, and sinks. `_room_light`, `_RoomLightSink`, `RoomBridge` and
`FakeRoomLightSink` are deleted; `control/terrarium.py` and
`console/agent.py` stop constructing or reading a room bridge.

### 5.2 Feeding

`_on_light_cue(dev, ...)`: a dev that is a bound fixture dev feeds that
fixture's session (and queues the fixture's audio tuple exactly as
today). The non-canonical drop is gone. `_feed_light_now` and
`_drain_light_cues` operate on the fixture's own session and pending
`when`. Mute (`_on_mute_change`), overrides (`_on_solid_cue`,
`_tick_overrides`, `_apply_override`) are already per fixture dev and do
not move.

Cues can only reach a fixture through its bound dev today (Control's
resolver produces devs). An unbound fixture therefore renders its session
(ambient, generators, breath) and shows in the Console, but receives no
Bit cues until it binds. This is accepted for this slice and recorded as
a limitation: cue routing by fixture name rather than dev is the natural
next step once a hardware sink exists that binds without a device.

### 5.3 Rendering and sinks

`_render_room` renders every fixture's session into its own `Universe`
every tick, applies that fixture's override, and when the frame changed
hands it to each of the fixture's sinks:

```python
class FixtureSink(Protocol):
    def send_frame(self, frame: bytes, when: float) -> None: ...
```

Two implementations in this slice, in `control/fixture_sink.py` (pure,
stdlib) and wired in the agent:

- `ConsoleFrameSink`: emits `room_frame` keyed by FIXTURE NAME (the
  Console's `surface.js` strip map switches from dev to fixture name).
  Present on every fixture from Room load.
- `DeviceLinkSink`: sends `protocol.leds_event(dev, frame, when=when)` to
  the bound dev. Added when the fixture binds, removed when it releases.

A physical controller is a later third implementation (an o2lite client on
a microcontroller, or luxaeterna's sACN / Art-Net / Enttec backend driven
from the Terrarium box) and touches nothing upstream of this protocol.
Multiple simultaneous devices per fixture are not designed here; the sink
list leaves room for it.

`when` per fixture is that fixture's own pending `at`, else clock plus
horizon.

### 5.4 Single-fixture rooms

DEMO's one fixture with six blocks becomes one session over one strip.
Output is identical to today.

## 6. Rooms catalog and the Design tab Room editor

### 6.1 Layout

- `rooms/<NAME>.toml` published, `rooms/drafts/<NAME>.toml` drafts.
- `[terrarium] room_paths`, default `["rooms"]`, beside `instrument_paths`.
- Inline `[rooms.<NAME>]` in `terrarium.toml` still parses. A name present
  both inline and in the catalog is a located `TerrariumConfigError`,
  mirroring the instrument rule.
- The "at least one `[rooms.<NAME>]` required" rule becomes "at least one
  room from either source".
- A room file's top level is the same table shape as today's
  `[rooms.<NAME>]` body: `description`, `backends`, `[[fixtures]]` with
  `name`, `color_order`, `instrument`, `[[blocks]]`, `[[zones]]`. Fixture
  order in the file is the Room's ordered list.

### 6.2 Migration

`rooms/TEST.toml` and `rooms/DEMO.toml` are created with the current
bodies verbatim; the `[rooms.TEST]` and `[rooms.DEMO]` tables are removed
from `terrarium.toml`. A test pins that the loaded TEST and DEMO
`RoomProfile`s equal the pre-migration ones.

### 6.3 Catalog generalization

`control/catalog.py` gains a `kind` ("instrument" | "room") with a parser
per kind (`_parse_instrument` and a new `_parse_room_file`). `CatalogEntry`
carries `kind`. `load_catalog(root, kind)`, `save_draft`, `clone_entry`,
`publish_entry` take the kind. The five design commands (`list_designs`,
`get_design`, `save_design`, `publish_design`, `clone_design`) and the
`designs_listed` / `designs_changed` / `design` events carry `kind`,
defaulting to "instrument" so existing Console code is unchanged in
behaviour.

### 6.4 The stubbed Room editor

In the Design tab: a Rooms list beside Instruments, the existing raw TOML
editor with draft / publish / clone, and ONE structured form: the fixture
list with move-up / move-down buttons and an instrument picker limited to
published instruments. Reordering rewrites the `[[fixtures]]` array order
in the draft text (via `toml_edit.js`). Blocks and zones stay raw TOML.
Changes apply at the next Room load. No live reordering.

## 7. luxaeterna: render at O2 time (cross-repo, lands first)

`LightSession.render_into` drops its private `_start`. `t` is the injected
clock's reading; `dt` is the delta since the previous read (first frame:
a small positive constant, as today). Since Control injects
`o2lite.time_get` in o2lite mode, every session on the box agrees on `t`.

The luxaeterna plan includes an audit of built-in ugens and the
`StatusDirector` for a "t starts at zero" assumption (welcome signatures,
`SegmentLevel` with `loop_from`) so nothing regresses when `t` is large at
first render; anything that needs a local origin captures it on its own
first render rather than relying on the session's. A test asserts two
sessions built at different clock readings return equal `t` for the same
clock value.

Until that PR lands, mm-terrarium's per-fixture sessions still work; they
merely disagree on phase by their construction skew. The Control-side work
does not block on it, but the live checklist's continuity item does.

Generator phase (Control's `_run_elapsed` for Bit generators, clock minus
start for ambient) is Control-owned and deliberately deterministic in
elapsed run time; instrument phase is O2 time. The two are not aligned
and are not meant to be.

## 8. Interrupt contract (pinned; built in a named follow-up slice)

Two origination points on one O2 timeline:

- Scheduled response: Control originates, stamped in O2 time, via the
  Function path (`FireFunction.at` for grid-timed consequences). Unchanged.
- Interrupt response: the device originates at the moment its detector
  fires, stamped with its own O2 reading, and executes a declared local
  response without waiting on Control.

Shape: `EventTrigger` gains an optional `interrupt` of SolidCue shape:

```toml
[[event_triggers]]
name = "tap"
thresholds = { peak_g = 2.0, window_ms = 200 }
interrupt = { rgb = [255, 255, 255], level = 0.9, duration = 0.15 }
```

Composed into the role blob's existing `triggers` key beside the
thresholds (`config["triggers"][name] = {"thresholds": {...},
"interrupt": {...}}`; today's flat thresholds dict becomes nested, a
wire change the mm-tuneshroom client follow-up absorbs). Semantics: the
device paints `rgb * level` over the frame it currently holds for
`duration` seconds from its own clock reading, then resumes the held
frame. The verb still goes to Control with the same timestamp; Control
never re-sends the interrupt. Scored consequences remain
Control-scheduled Functions. A Bit's role or the carried instrument may
declare it; role wins over instrument for the same trigger name.

Owners: mm-terrarium (declaration, validation, blob, Console trigger
card), mm-tuneshroom (client execution), harness Testshroom
(`harness/o2_shroom.py`, reference implementation). Not built here.

## 9. Retirements and reversed decisions

Deleted: `GameServer._canonical_room_dev`, `DeviceLinkAgent._canonical_room_dev`,
`_collapse_room_fanout`, the fold-back in `_suppress_generator_lanes`, the
`explicit_surface` branch in `fire_function`, the non-canonical light drop
in `_on_light_cue`, `RoomBridge`, `FakeRoomLightSink`, `_RoomLightSink`,
`ambient_manifests(profile)` (replaced per fixture), the `RoomProfile`
cross-fixture generator lane rule, and the `room_bridge` plumbing in
`control/terrarium.py` and `console/agent.py`.

Reversed: N-fixture spec non-goal N1 (named-fixture targets). PR #81 spec
section 5's accepted delta (ROOM SolidCue paints only the canonical slice)
is undone: `@room` SolidCue paints every fixture.

Retained: `RoomProfile.zones` (namespaced union, for the Console and for
manifest target validation), `fixture_slices()` (the simulator's
`expected_channels`), `to_capability(profile)` (Console zone list), the
one-`color_order`-per-Room rule (no longer load-bearing for rendering; kept
to avoid a config change this slice).

## 10. Testing

Offline (`.venv/bin/python -m pytest tests -q`; baseline at PR #81 HEAD
1903 passed, 1 skipped):

- cues: `fixture_dev` / `fixture_name` round trip; malformed names refused.
- functions: `@fixture:` legal on Bit owner, refused on instrument owner,
  in script steps, generator lanes, stream outputs.
- role_config: `slice_light_manifest` for `primary`, `<fixture>`,
  `<fixture>.<zone>`; unknown names refused with located messages.
- engine: `@fixture:` resolves to the bound dev, drops when unbound (warned
  once); `@room` fans to every bound fixture in list order; `load_bit`
  refuses a Bit addressing a missing fixture; post-expansion generator
  lane collision refused; per-fixture suppression (fire at accent leaves
  main's generator emitting) closing the parked finding; `_check_cue_kinds`
  per fixture instrument.
- generator_runner: per-fixture emission and suppression.
- devicelink agent: one session per fixture from Room load, bound or not;
  unbound fixture emits Console frames and sends nothing; bound fixture
  gets a `DeviceLinkSink`; `@room` SolidCue paints every fixture;
  per-fixture `when`; light cue at the accent reaches the accent's session
  only; ambient manifests per fixture; single-fixture room byte-identical
  to a whole-profile render.
- catalog / terrarium_config: rooms catalog parse, draft errors, inline
  vs catalog collision, `room_paths` default, "at least one room from
  either source"; TEST and DEMO profiles equal pre-migration.
- console: design commands and events carry `kind`; Rooms list; fixture
  reorder rewrites `[[fixtures]]` order (JS test); `room_frame` keyed by
  fixture name.
- luxaeterna (its own suite): equal `t` across sessions; ugen audit tests.

Live (Arco stack on the dev box; the spec's checklist, run before merge):

1. Load TEST with NO simulators spawned. Both Console strips animate from
   the Bit's ROOM manifest.
2. Spawn the two simulators; both bind and show the same content as the
   strips.
3. Fire `play_aurora` at `sim-room-accent` (SURFACE). Light on the accent
   only; main's generator continues.
4. Fire a chase script (TestBit gains `chase`: `@fixture:main` then
   `@fixture:accent` at 0.0 / 0.5 s). Steps visibly in order.
5. After the luxaeterna PR: rainbow continuity across main and accent
   measured off the canvases as in the 2026-08-19 check (equal hue slope
   per pixel, accent continuing main's ramp when the manifest declares the
   matching hue offset).
6. Load DEMO: unchanged.
7. Stop at `@all`, then ABORT, then Load Room recovery, as PR #81's
   unverified checklist asked (that checklist is folded in here).

## 11. Follow-ups named by this spec

- Interrupt slice (section 8): mm-terrarium declaration and blob,
  mm-tuneshroom client, harness Testshroom reference.
- Hardware `FixtureSink` (luxaeterna backend or microcontroller o2lite
  client) and cue routing by fixture name for fixtures that never bind a
  device.
- Multiple simultaneous devices per fixture.
- Room editor beyond the stub: structured blocks and zones forms.

## 12. Status (2026-09-01)

Plan 1 (sections 2-5, 9: addressing, validation, engine data flow,
transport data flow and sinks, retirements) landed on
`claude/instrument-topology-triggers-6d2031`, commits
`eecf78c..f005d1c`. Offline suite: `.venv/bin/python -m pytest tests -q`
-> 1951 passed, 1 skipped (was 1903 passed, 1 skipped at PR #81 HEAD).
See `docs/MM_TERRARIUM.md`'s "Per-fixture light sessions,
`@fixture:<name>` addressing, FixtureSinks (2026-09-01)" entry for the
landed detail.

A final whole-branch review (2026-09-01) found one gap and one
pre-existing docs/code mismatch in Plan 1, both fixed and re-verified
before merge. Offline suite after the fix wave: `.venv/bin/python -m
pytest tests -q` -> 1955 passed, 1 skipped.

Deviations from this document's plan-facing prose, listed individually
rather than folded into one parenthetical:

- `chase` lives in a new packaged Bit, `bits/chase/` (`ChaseBit`), rather
  than on `TestBit`, because the load-time fixture contract in section
  3.4 refuses `TestBit` naming fixtures on the one-fixture DEMO room.
- The TEST Room's `surface_id` is `room_test` (not `TEST`), and the leds
  wire shape stays as shipped: `harness/shroom_client.py`'s `_on_leds`
  reads the channel array from `args[0]` and the presentation time from
  the separate `env.timestamp` field, not as a combined tuple this
  document's illustrative prose might suggest.
- `when` is stamped per fixture, not shared across fixtures from one
  presentation time: two fixtures rendered in the same tick no longer
  share a presentation time by construction.
- An ambient drone starts on EVERY granted fixture whose own ambient ugen
  declares instruments, each with its own ambient ugen voice, not only
  the first fixture; the welcome ceremony stays a once-per-Room,
  first-fixture-only affair.
- `ConsoleAgent`'s constructor takes `room_controllers` (a callable
  returning `{fixture_name: {cc: value}}`) in place of `room_bridge`;
  `room_view` emits `controllers` (the flat merge, first fixture in
  profile order wins on a collision) alongside `fixture_controllers`
  verbatim; `room_frame` is keyed by `fixture` (a name), not `dev`.
- **(Final-review Important finding 1.) Instrument-owned GENERATOR devs
  are `@target` only, not `@target`/`@room` as this document's section
  3.2 table states.** An instrument is a type and cannot know a Room's
  fixture names or how many fixtures will carry it; a room-wide ambient
  effect is declared per fixture, so an instrument's GENERATOR cannot
  legally be room-wide either. `control/functions.py`'s
  `_validate_generator` refuses any instrument-owned GENERATOR dev but
  `cues.TARGET`, with a located ValueError. This is what makes two
  fixtures' ambient generators on the same cc uncollidable by
  construction (each is `@target`-only, so it can only ever write its own
  declaring fixture's lane) rather than merely uncollided by the shape of
  today's shipped instruments; `devicelink/agent.py`'s ambient resolver's
  `@room`-fans-to-every-fixture branch is unreachable under this rule and
  has been simplified to `[own] if dev == TARGET else []`. No shipped
  instrument (`instruments/*.toml`, `control/instrument.py`'s
  `TUNESHROOM`) declares a GENERATOR at all, so this is a validation-time
  rule with no shipped-content fallout.
- **(Final-review Important finding 2, docs only; no code change.)
  Instrument-owned SCRIPTED (script step) devs are `@target` only, not
  `@target`/`@room` as this document's section 3.2 table states.** This
  was already true of the landed code (`control/functions.py`'s
  instrument SCRIPTED branch has always refused anything but
  `cues.TARGET` on an instrument script step) -- the table was wrong at
  authoring time, not regressed by Plan 1. Instrument STREAM outputs are
  unaffected and still accept `@target` and `@room`, per the table.

Plan 2 (section 6, rooms catalog and the Design tab Room editor) landed on
`claude/rooms-catalog-plan2-8f3a1c`, commits `fc21c1f..05891cf`. Offline
suite: `.venv/bin/python -m pytest tests -q` -> 1986 passed, 1 skipped
after the final-review fix wave (was 1955 passed, 1 skipped at Plan 1's
fix-wave HEAD). See
`docs/MM_TERRARIUM.md`'s "Rooms catalog, TEST/DEMO migration, Design tab
Room editor (2026-09-01)" entry for the landed detail.

Deviations from this document's Plan 2 prose:

- The console-side design-command parser this document's section 6.3
  describes as `parse_command` is `console/protocol.parse_admin_command`
  in the shipped code; it already parsed every other admin command, so
  the `kind` handling was added there rather than in a new function.
- A published room catalog file that fails to parse does not blank the
  Rooms panel: `ConsoleAgent._design_rows` catches the located
  `TerrariumConfigError`, logs it, and appends one synthetic row named
  `<rooms catalog>` carrying the error, so the Instruments half of the
  panel and the fault itself both stay visible.
- The fixture instrument picker in `design_forms.js` offers a fixture's
  own `instrument` value as a leading, preselected option labelled
  `"<name> (unpublished)"` when that name isn't among the published
  instruments, rather than only listing published names -- a browser
  `<select>` with no matching option silently preselects the first one,
  which would have shown the wrong instrument as selected.
- `toml_edit.js`'s `setFixtureInstrument` inserts a missing `instrument =
  "..."` line (directly after the fixture's own `name = "..."` line, or
  after the `[[fixtures]]` header when the fixture has no name) rather
  than being a no-op when the line is absent -- section 6.4's "rewrites
  that block's `instrument = "..."` line" phrasing assumed the line
  always exists, which is not true of every hand-authored room file.
- TEST and DEMO's `surface_id` values (`room_test`, `room_demo`, per
  Plan 1's own deviation from this document) are unchanged by the
  migration into the catalog; the pre-migration profile-equality test
  pins this directly.

Plan 3 (section 7, luxaeterna rendering at O2 time) is not started. The
interrupt contract (section 8) remains a named follow-up slice, not part
of either.

Live checklist (section 10): steps 1-3 and 6-7 have code support as of
Plan 1; step 4's chase now fires through `ChaseBit`, not `TestBit`; step 5
(rainbow continuity across fixtures) waits on Plan 3. Plan 2 adds its own
live verification (Console Design tab Rooms list, fixture reorder,
publish, reload), described in the plan's closing section. None of
Plan 1's or Plan 2's live steps have been run against a real Arco stack
yet -- see
`docs/superpowers/handoffs/2026-09-01-rooms-catalog-and-o2-time-handoff.md`
and this task's brief for the live verification procedure to run before
merge.
