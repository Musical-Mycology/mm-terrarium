# Bit-declared triggers, cue scripts, and conditions

**Date:** 2026-08-17
**Status:** Designed, not implemented. Written against the tree at `6c03ede`
(PR #31 merged). Suite baseline to hold: 844 passed, 1 skipped.

**Repos touched:** `mm-terrarium` only. No luxaeterna change, no mm-tuneshroom
change, no Arco or pyarco change.

**Scope:** Spec B of two. Spec A,
[`2026-08-17-room-panel-and-room-fixtures-design.md`](2026-08-17-room-panel-and-room-fixtures-design.md),
landed the Room panel and the Room's own fixtures and named this document as its
section 14 non-goal. A third slice, Spec C (the N-fixture Room), is identified in
section 4.2 below and deliberately deferred.

**Amends:** nothing. This document adds a declaration surface; it narrows or
relaxes no existing rule.

This document is a point-in-time design record, not a living doc. For current
behavior, constraints and known issues read `docs/MM_TERRARIUM.md`.

---

## 1. Why this slice exists

After Spec A an operator can see what a Bit *declares* for the Room and what it
is *doing now*. They still cannot see what will make something happen. There is
no way for a Bit to say "there is a thing called Play Aurora, it fires when a
user wins a round, and here is what it does."

Three structural gaps, each independently sufficient:

1. **`verb_handlers()` carries no metadata.** It is a bare `dict[str, callable]`
   (`control/bit.py:87`). A handler has no description, no condition, no target,
   and nothing the Console can render beyond the verb string itself.
2. **A Bit-adjudicated trigger has no incoming verb at all.** "User wins a round"
   is decided inside the Bit, with no device message behind it, so it
   structurally cannot live in `verb_handlers()` however much metadata that dict
   grew.
3. **There is no way to make something happen on purpose.** The only paths that
   move the Room today are a device gesture (`GameServer.data()`) and the Bit's
   own per-tick `cues(at)`. Neither is available to an operator standing at an
   installation trying to confirm a fixture works.

Gap 3 is not a convenience. `docs/MM_TERRARIUM.md` records headless device clock
sync against a post-`reset()` Arco as intermittent, measured at **1 of 3 runs
succeeding**, upstream and unfixed. A bring-up tool that requires a device to
join is a bring-up tool that works one time in three.

### 1.1 Findings from the code that shaped this design

Established by reading the tree before any option was proposed.

**F1. The cue dispatch path already computes and threads one presentation time.**
`GameServer.data()` computes `at = self._origin(gesture_time) + self._horizon`
once (`control/engine.py:212`) and `_dispatch_bit_cues()` computes
`at = self._clock() + self._horizon` (`control/engine.py:306`). Both hand that
single value to `_dispatch_cues(cues, at)`, which stamps every cue that did not
name a time of its own. Anything riding this path inherits "one gesture, one T"
for free.

**F2. `_dispatch_cues` already tells cue kinds apart by identity and already
resolves a sentinel.** `control/engine.py:261-289` branches on `PlayCue` /
`LightCue` / plain 4-tuple, calls `_resolve_dev` on each, drops a cue whose
resolution returns `None`, and guards the whole per-cue block so an arity-wrong
cue from a buggy Bit cannot break `data()`'s documented never-raises contract. A
fourth cue kind costs one branch.

**F3. `LightCue` honors a Bit-declared `when`, and the transport already holds a
far-future one.** `_dispatch_cues` does `when = at if cue.when is None else
cue.when`. Downstream, `DeviceLinkAgent._on_light_cue` computes
`feed_at = when - self._horizon` and, when that is still in the future, pushes
onto `_light_cues` with the comment "A Bit-declared cue further out than one
horizon. Hold the session feed too, or the future state leaks into whatever
breath frame renders in between" (`devicelink/agent.py:550-557`). **The
scheduling this slice needs is already built and already correct.**

**F4. `on_play_cue` has no queue.** `DeviceLinkAgent._on_play_cue`
(`devicelink/agent.py:560`) sends immediately, and `PlayCue`'s own docstring says
"Untimed by design: the device owns when a local sample fires, so there is
nothing on this path for Control to schedule." A delayed `PlayCue` would
therefore fire at once, silently.

**F5. The engine's observer list is multi-observer, guarded, and already shared.**
`add_observer()` appends; `_notify()` walks every observer, skips a missing
method, and logs a raising one without interrupting the engine or its peers
(`control/engine.py:358-376`). Both `UplinkAgent` and `ConsoleAgent` attach
simultaneously. The transport-owned sinks (`on_release`, `on_light_cue`,
`on_play_cue`) are by contrast single-slot attributes, each guarded by hand at
its call site.

**F6. Room cue routing and the room frame were both placed in the transport for
the same stated reason, and it is not "never touch the engine".** The 2026-08-10
simulator spec put Room cue routing in `DeviceLinkAgent._on_light_cue`, and Spec
A section 7 put the frame sink on `DeviceLinkAgent`, both because the value being
routed **is computed inside the transport**. Spec A states it directly: "the
frame is computed in the transport, so the sink belongs to the transport." The
rule is about where data is produced, not about which file is off-limits.

**F7. `control/role_config.py` is the load-time validation pattern to copy.**
`validate_role_declarations()` is called from `load_bit`
(`control/engine.py:102`) inside the `try` whose `except` re-raises as
`BitLoadError`, and every message locates the offending field
(`role 'player' light_manifest instruments[0] lanes[1]: missing required field
'source'`). A typo'd Bit fails at load, never as a device-side parse error
mid-installation.

**F8. The Room is single-device end to end.** `Room.bound_dev` is one string
(`control/rooms.py:63`), `RoleClass.ROOM` has capacity 1
(`control/rooms.py:87-94`), `RoomBindingRegistry` tracks one dev per `RoomType`,
`RoomProfile` declares one `surface_id`, and `DeviceLinkAgent` holds one
`_room_dev`, one `RoomBridge` and one `_room_light`. `_resolve_dev` returns a
single string.

**F9. Lights are o2lite clients; audio instruments are ugen channels with no dev
id.** The Room's light path is Lux Aeterna in Control's own process
(`LightSession.render_into(universe)` to a 180-byte `/dev/leds` blob), while its
audio path is `RoomBridge.feed_audio` to `AudioBridge` to `ArcoVoice`, which is
**one MIDI channel on one shared `Flsyn` ugen** (`harness/arco_synth.py`).
Arco relays the light blob as the room's O2 hub but never renders it. So a cue's
`dev` field addresses a light client, and there is no dev id on the audio side to
address at all. `RoomBridge` splits `feed_light`/`feed_audio` for **timing**, not
for routing (`control/room_bridge.py:77-96`).

**F10. Console visibility is established as addition, never relaxation.**
(Findings are numbered within this document; Spec A has its own F-numbers, and
where one is cited below it is named as Spec A's.) Spec A
section 3 kept `console/agent.py:88`'s `RoleClass.ROOM` filter byte-identical and
introduced a separately built `room` key, so that
`tests/test_console_agent.py::test_the_room_stays_hidden_from_roles_and_registration_while_visible_as_room`
can assert both halves in one test.

**F11. A grep over browser source is not a test of behavior.** Two Important
defects reached a live browser run past 843 passing tests because `room.js` was
checked only by substring greps over its own text: `renderRoom` replaced the
`#roomStrip` node on every `room_changed`, rebuilding it roughly four times per
painted frame (1726 against 464), and `body` had `color: #111` with no
`background-color`. The pattern that now covers it is
`tests/js/room_panel_behavior.test.js` under a DOM stub via Node's `vm`, wrapped
by `tests/test_room_panel_behavior.py` with a clean skip where node is absent.

---

## 2. Goals

1. A Bit can declare, as data, that a named thing exists, what makes it happen,
   what it does, and where it lands.
2. The Terrarium Console renders each declaration as a card showing the actual
   steps, not a prose summary of them.
3. An operator can fire any declared trigger by hand, and the record of that fire
   is never mistakable for real gameplay.
4. A trigger that is declared but not implemented fails at `load_bit` as a
   `BitLoadError`, not mid-installation.
5. Control never evaluates a win condition.
6. Nothing here requires a device to join.

---

## 3. Decisions taken before this document, restated so the record is one place

These were settled in the brainstorming that produced this spec and are not
re-argued below. They are recorded because the reasoning matters more than the
conclusion.

1. **Surface is the Terrarium Console.** Not the luxaeterna simulator page: that
   process is deliberately a dumb LED client, interchangeable with real hardware,
   and must not learn about Bits. Not a third page either.
2. **A new `TriggerTable`, declared parallel to `RoleTable`.** `verb_handlers()`
   stays exactly as it is and is *annotated* by this, not replaced.
3. **Actions are declarative cue scripts, not callables.** Three reasons a
   callable loses to data here: the Console can render the real steps rather than
   only a description; a test can assert the exact cue sequence with no Arco; and
   manual fire becomes pushing data through the existing dispatch rather than
   calling into Bit code at an arbitrary moment.
4. **The Bit evaluates conditions; Control only observes.** This preserves the
   boundary the architecture rests on, needs no expression language, and still
   answers the operator's "did it actually fire?".
5. **Manual fire is in scope for the first slice**, tagged as an operator action.
   Justified by the 1-in-3 headless clock sync recorded in section 1.

**Ruled out, with the reason.** Triggering a luxaeterna instrument by note-on or
signature. It was tried for `aurora` and rejected: `bloom` froze its colour at
note-on, so sweeping the hue meant retriggering constantly, which read as a
visible strobe. `aurora` deliberately has no note lane, and `bits/test_bit.py`'s
docstring records this.

---

## 4. Targeting, and the boundary this slice does not cross

### 4.1 The target vocabulary

A trigger declares one of three targets:

| Target | Resolves to |
|---|---|
| `ROOM` | every Room fixture |
| `DEVICE` | the device that fired, when there was one |
| `ALL` | Room fixtures plus every registered non-`ROOM` device, deduplicated, in a stable order |

**Resolution returns a list from day one.** Today `ROOM` yields at most one
element, because of F8. That is the single accommodation this slice makes for
section 4.2, and it costs one function signature:

```python
def _resolve_target(self, target, dev) -> list[str]:
    if target is TriggerTarget.ROOM:
        return [d for d in (self.room.bound_dev,) if d] if self.room else []
    if target is TriggerTarget.DEVICE:
        return [dev] if dev else []
    ...
```

A named-fixture target (`target: "room_left_array"`) was considered and rejected
for this slice. It has nothing to validate against: `RoomProfile` declares one
`surface_id` and `RoomBindingRegistry` maps one dev per `RoomType`, so a name
would be authored against a registry that does not exist, and load-time
validation is precisely what goal 4 asks to be strict. Per F9 it also has no
referent on the audio side, where one `Flsyn` channel carries the whole Room and
there is no second thing to select between.

### 4.2 Spec C: the N-fixture Room, deferred

The brainstorming that produced this document established that a real venue Room
is **N light fixtures, each its own o2lite client with its own unique service
name**, not one. That is correct and it is not what the code does (F8).

It is a separate slice because it touches `Room`, `RoomProfile`,
`RoomBindingRegistry`, `RoleClass.ROOM`'s capacity of 1, `DeviceLinkAgent`'s
single `_room_dev` / `_room_bridge` / `_room_light`, the boot binding sequence,
and the `/dev/leds` fan-out. That is a larger change than this one, in code
shaped by hardware nobody has plugged in yet, and bundling the two means neither
gets a clean live verification.

**Nothing a Bit author writes in this slice changes when Spec C lands.**
`_resolve_target` starts returning N instead of 1 and every declaration above
still means what it said.

One correction worth carrying into Spec C, because it was a live misconception
during this design: **lights are not wrapped in Arco ugens.** Per F9, Lux Aeterna
and Arco are siblings, not nested, and boundary rule 3 calls Lux Aeterna "the
visual analog of the Arco audio engine". Arco wears two hats, hub and
synthesizer, and light traffic transits it as a relay without ever entering it as
a ugen. So N fixtures is a **light-side** concept: adding a fixture costs a
client, a unique service name and a binding, while adding an audio instrument
costs a channel from a pool and no client at all.

---

## 5. The declaration

New module `control/triggers.py`. Pure, no engine imports, mirroring how
`control/roles.py` holds the declaration and `control/role_config.py` holds its
validation. Both live here because there is no composed device-side blob to keep
them apart, and because `expand_script` belongs next to the shape it expands.

```python
class TriggerTarget(Enum):
    ROOM = auto()
    DEVICE = auto()
    ALL = auto()


class ConditionSource(Enum):
    GESTURE_VERB = auto()       # a device gesture the Bit adjudicates
    BIT_ADJUDICATED = auto()    # decided inside the Bit, no message behind it
    ADMIN_MANUAL = auto()       # exists only to be fired by an operator


@dataclass(frozen=True)
class Condition:
    name: str
    description: str
    source: ConditionSource
    verb: str | None = None     # required iff source is GESTURE_VERB


@dataclass(frozen=True)
class ScriptStep:
    offset: float               # seconds from the trigger's `at`
    cue: object                 # plain 4-tuple or PlayCue; see section 6


@dataclass(frozen=True)
class Trigger:
    name: str
    description: str
    target: TriggerTarget
    condition: Condition
    script: tuple[ScriptStep, ...] = ()


@dataclass
class TriggerTable:
    triggers: dict[str, Trigger]
```

`control/bit.py` gains one property:

```python
@property
def trigger_table(self) -> TriggerTable:
    """This Bit's declared triggers. Default: none."""
    return TriggerTable(triggers={})
```

A plain property with a default, deliberately not `@abstractmethod` like
`role_table` is, so every existing Bit is unchanged and `CaptureBit` in
particular needs no edit.

`control/cues.py` gains one sentinel and one type:

```python
# Sentinel dev id for a script step addressed at whatever the firing trigger
# declared as its target. Substituted during expansion, before the cue reaches
# GameServer._resolve_dev, so _resolve_dev is not edited by this slice.
TARGET = "@target"


@dataclass(frozen=True)
class FireTrigger:
    """A Bit's report that one of its declared conditions is satisfied.

    Returned in the same list a Bit already returns cues in, from a verb
    handler or from cues(at), so the fire inherits that path's single
    presentation time. `dev` names the device the fire is about, when there
    is one; it is what TriggerTarget.DEVICE resolves to.
    """
    name: str
    dev: str | None = None
```

### 5.1 Why the condition does not auto-fire on its verb

A `GESTURE_VERB` condition names the verb it is reached through, and Control does
**not** fire the trigger when that verb arrives. The Bit's handler returns
`FireTrigger` explicitly, or does not.

This is the load-bearing reading of decision 4, and the obvious alternative is
wrong: a `tap` that misses, a tilt below threshold, or a gesture from a player
who has already won should not fire. Auto-firing on verb dispatch would place
condition evaluation, however trivial, inside Control. The `verb` field is
therefore metadata that tells an operator how a trigger is reached, and gives
load-time validation something real to check.

---

## 6. Load-time validation

`validate_trigger_table(trigger_table, verb_handlers)` is called from `load_bit`
alongside `validate_role_declarations`, inside the same `try` whose `except`
re-raises as `BitLoadError` (F7). Every message locates the offending field in
the same style.

| Refused | Because |
|---|---|
| A dict key that does not equal its `Trigger.name` | The Console indexes by key and the record reports the name; disagreeing is a silent mislabel |
| Empty `Trigger.description` or `Condition.description` | The card is the deliverable |
| `source is GESTURE_VERB` with no `verb`, or a `verb` absent from `verb_handlers()` | **This is goal 4's declared-but-unimplemented check** |
| `verb` set on a `BIT_ADJUDICATED` or `ADMIN_MANUAL` condition | Names a route that does not exist |
| An offset that is negative, non-finite, or out of non-decreasing order | The Console renders steps as a sequence; unordered data renders misleadingly |
| A `LightCue` in a script | The offset is the timing. `LightCue` is what expansion *produces* (section 7), not what a Bit declares |
| A `PlayCue` at a non-zero offset | F4: `on_play_cue` has no queue, so the offset would be silently ignored |
| A literal dev id in a step's cue | Dev ids are assigned at runtime, so a literal in a static declaration is always a bug. Only `cues.TARGET` and `cues.ROOM` are legal |
| A plain cue tuple of wrong arity, or with a status/data byte outside 0-255 | Same shallow-structural discipline `role_config.py` applies |

**An empty script is legal**, and means observe-only: fire, record, emit nothing.
That is exactly what a "did it actually fire?" bring-up check wants, and what a
later fairyring-bound event with no local light consequence wants.

Validation is deliberately shallow in the same place `role_config.py` is: it
checks shape and cross-references only what Control owns. It does not check that
a cue's controller number appears in any manifest lane, because an undeclared cc
is already dropped by `AudioBridge._apply_midi` by design ("what makes the lane a
remap seam and not decoration") and because the light half's instrument registry
belongs to luxaeterna, which Control cannot see.

---

## 7. Firing

### 7.1 One entry point, three sources

```python
def fire_trigger(self, name: str, *, fired_by: str,
                 dev: str | None = None,
                 at: float | None = None) -> str | None:
```

Returns `None` when fired, else a refusal reason, and **never raises**, matching
`data()`'s contract for the same reason: neither a device nor a browser may wedge
Control. Permitted in `SETUP` and `RUNNING`, also matching `data()`, because the
`--setup-seconds` hold is where an operator does bring-up.

Three callers:

```
verb handler returns FireTrigger  ->  _dispatch_cues(..., fired_by="gesture-verb")
Bit.cues(at) returns FireTrigger  ->  _dispatch_cues(..., fired_by="bit-adjudicated")
Console "fire_trigger" command    ->  fire_trigger(..., fired_by="admin-manual")
```

`_dispatch_cues` gains a `fired_by` parameter, supplied by its two existing call
sites (`data()` and `_dispatch_bit_cues()`), and one branch for `FireTrigger` in
the chain it already runs (F2). A `FireTrigger` met there calls `fire_trigger`
with the `at` that dispatch already holds, so a trigger fired from a gesture
lands on the same presentation time as the ordinary cues returned beside it. That
is F1 paying out with no new arithmetic.

### 7.2 Expansion

`expand_script(trigger, at, devs) -> list` in `control/triggers.py`, pure and
unit-testable with no engine:

- one output cue per (step, resolved dev) pair, `cues.TARGET` substituted
- `cues.ROOM` in a step is left **untouched**, and resolved downstream by the
  existing `_resolve_dev`, so a script can address the Room explicitly even when
  its target is `DEVICE`
- a light step becomes `LightCue(dev, status, d1, d2, when=at + offset)`
- a `PlayCue` step becomes a `PlayCue` with its dev substituted

The result is handed to the **existing** `_dispatch_cues`, which already honors a
`LightCue`'s explicit `when` (F3) and already resolves `ROOM`. So expansion adds
no dispatch code, no scheduler, and no second copy of horizon arithmetic.

Downstream, `DeviceLinkAgent._on_light_cue`'s existing far-future branch (F3)
holds each step's session feed until `when - horizon` and stamps the outgoing
frame `when`. **The scheduling for this feature was written on 2026-08-14 and
this slice only supplies it with input.**

An engine-held queue draining steps per tick was considered and rejected: it
would have to release each step at `at + offset - horizon` rather than at
`at + offset`, or every step feeds late and clamps. That is a second copy of
horizon arithmetic in a repo whose record already contains two separate live
failures caused by timing living in two places.

### 7.3 A script cannot outlive its Bit

`DeviceLinkAgent` already handles `State.UNLOADING` in `on_state_change`, where it
stops the Room drone. It gains one more action there: clear `_light_cues` and
`_room_cues`.

Without it, a step scheduled at `at + 5.0` by a Bit that completes at `at + 2.0`
still feeds the Room bridge afterwards, gliding the Room's light after the drone
has stopped and the Bit is gone. Player devices are already safe by accident
(`_feed_light_now` returns early when `bridge is None or bridge.session is None`
after `_finish_release`), but the Room's bridge persists across a Bit lifecycle by
design, so the Room is the case that needs saying.

---

## 8. The fire event

```python
@dataclass(frozen=True)
class TriggerFired:
    name: str
    condition: str              # Condition.name
    fired_by: str               # what actually fired it, THIS time
    declared_source: str        # what the condition declares
    dev: str | None             # the firing device, when there was one
    devs: tuple[str, ...]       # what the target resolved to
    at: float
    steps: int                  # cues actually dispatched
```

**`fired_by` and `declared_source` are separate on purpose.** A manual fire of a
gesture-verb trigger records `fired_by="admin-manual"` against
`declared_source="gesture-verb"`. Collapsing them into one field is what would
make an operator action indistinguishable from real gameplay in the log, which
decision 5 exists to prevent.

`devs` and `steps` report what the fire *resolved to*, not what it declared, so a
`DEVICE`-target trigger fired with no device is visibly a fire that reached
nothing rather than a fire that silently did nothing.

### 8.1 Where it rides, and why

**A new engine observer hook,** `self._notify("on_trigger_fired", record)`, on the
existing multi-observer list. Not a transport-owned sink.

This is the one genuinely open routing question in this slice, and the case turns
on F6. Spec A and the 2026-08-10 simulator spec both put a sink on
`DeviceLinkAgent`, and both said the same thing about why: the value being routed
was computed inside the transport. That reasoning does not transfer here, and
read literally it points the other way.

Three reasons, each independently sufficient:

1. **The record is produced in `control/engine.py`.** Expansion, target
   resolution and dispatch all happen there, off the Bit's own declaration. No
   transport computes any part of it.
2. **A fire has no device destination.** `on_release`, `on_light_cue` and
   `on_play_cue` all exist to hand a transport something to *deliver*. A
   transport receiving `TriggerFired` would deliver it nowhere and forward it to
   the Console, which is a detour with no delivery in it.
3. **The cardinality is wrong for a sink.** Transport sinks are single-slot
   attributes; the observer list is notify-all and already carries two live
   attendees (F5). "User wins a round" is precisely the class of event the
   `Terrarium uplink -> mm-fairyring -> RenQuest trigger` chain exists for, so
   choosing a single-slot sink would foreclose the uplink observing it without a
   rewrite.

`_notify` already guards every observer and logs a raising one without
interrupting the engine or its peers, so the "a failing observer must not wedge
Control" property arrives for free rather than as a fourth hand-written guard of
the kind each transport sink needed (Spec A's own finding F8, not this
document's).

Notification happens **after** cue dispatch, so `devs` and `steps` report what
actually went out.

**`control/engine.py` is edited by this slice, and Spec A's non-goal is not being
violated.** Spec A declined to edit it because the frame sink genuinely belonged
elsewhere. This slice must edit it regardless of where the hook lands: expansion
needs `_resolve_dev` and `_dispatch_cues`, and manual fire needs an engine entry
point. Given that, the hook's placement is a free choice made on merit.

---

## 9. Console

New module `control/trigger_view.py`, pure dict builders with no engine imports,
mirroring `control/room_view.py`.

```json
{
  "name": "play_aurora",
  "description": "A slow aurora sweep across the Room",
  "target": "ROOM",
  "condition": {"name": "round_won", "description": "User wins a round",
                "source": "bit-adjudicated", "verb": null},
  "script": [
    {"offset": 0.0, "kind": "light", "dev": "@room",
     "status": 176, "data1": 74, "data2": 127},
    {"offset": 0.5, "kind": "light", "dev": "@room",
     "status": 176, "data1": 74, "data2": 40},
    {"offset": 2.0, "kind": "light", "dev": "@room",
     "status": 176, "data1": 74, "data2": 0}
  ]
}
```

Steps are serialized field-by-field rather than as raw tuples so the browser
renders them without re-deriving MIDI semantics, and `kind` discriminates light
from play the same way Spec A's instrument list discriminates light from audio.

### 9.1 Wire protocol

Additions to `console/protocol.py`. **Addition, not relaxation** (F10): no
existing message changes shape, and no existing filter is touched, so an old
browser tab against a new server degrades to exactly today's behavior.

| Message | Shape | Cadence |
|---|---|---|
| `snapshot` | gains a `"triggers"` key: a list, empty when no Bit is loaded | on connect |
| `triggers_changed` | `{"event": "triggers_changed", "triggers": [...]}` | on change |
| `trigger_fired` | `{"event": "trigger_fired", "fired": {...}}` | per fire |
| `fire_trigger` (command) | `{"command": "fire_trigger", "name": str, "dev": str?}` | operator action |

`fire_trigger` is parsed in `console/protocol.py`'s `parse_admin_command`, **not**
in `uplink.protocol.parse_command`. Same separation, and the same reason, as
`arm_room`/`release_room`: firing a venue's trigger is a local trusted-operator
action, not something a remote fairyring peer should be able to request.
`ConsoleAgent._handle_command` already routes admin commands by name before
falling through to the shared parser.

`triggers_changed` uses the same change-detection shape as
`_broadcast_room_if_changed()`. A trigger table is static per Bit, so in practice
it changes on load and unload only; detecting rather than special-casing keeps it
consistent with its neighbors and cheap to reason about.

### 9.2 The panel

New `console/static/triggers.js`, served from the existing allowlisted asset map.
No build step: a venue box must never need npm.

```
Play Aurora                                       target: room     [ Fire ]
  User wins a round                               bit-adjudicated
  +0.00s   @room   cc:74 = 127
  +0.50s   @room   cc:74 = 40
  +2.00s   @room   cc:74 = 0
  last fired 12s ago   ADMIN MANUAL

Flash Device                                      target: device   [ Fire ]
  Player taps their Shroom                        gesture-verb (tap)
  +0.00s   @target  play "click"
  +0.00s   @target  cc:74 = 127
  never fired
```

A `DEVICE`-target card carries a device picker beside its Fire button, populated
from the device list the panel already holds, so manual fire remains useful for
exactly the gesture-driven triggers a 1-in-3 clock sync makes hardest to test.
Firing with no device selected is refused by the engine and surfaced as an
`error` event rather than silently reaching nothing.

The `ADMIN MANUAL` tag is styled distinctly from the other two sources. That is
the visible half of decision 5.

---

## 10. TestBit

Two triggers, because one leaves the `GESTURE_VERB` condition source and its
verb-existence validation with no fixture behind it, and because a one-row list is
where off-by-one rendering bugs hide.

| Trigger | Target | Condition | Source |
|---|---|---|---|
| `play_aurora` | `ROOM` | `round_won`, "User wins a round" | `BIT_ADJUDICATED` |
| `flash_device` | `DEVICE` | `tapped`, "Player taps their Shroom" | `GESTURE_VERB` (`tap`) |

`play_aurora` is adjudicated in `update(dt)` from a counter incremented by full
tilt deflection, and reported from `cues(at)` on the tick after it latches. It is
deterministic in `self._elapsed`, which `update(dt)` already accumulates, so a
test asserts the exact fire tick with no Arco. `flash_device` is returned from the
existing `_on_tap` handler alongside the cues it already returns.

`TestBit` remains the durable reference fixture: after this slice it is the lone
exemplar of the `TriggerTable` seam exactly as it is already the lone exemplar of
`ugen_manifest`, `light_manifest` and `status()`.

---

## 11. Data flow

```
declaration
  Bit.trigger_table                validated at load_bit -> BitLoadError

fire, three ways
  verb handler -> [FireTrigger]  --+
  cues(at)     -> [FireTrigger]  --+--> _dispatch_cues(cues, at, fired_by)
  Console "fire_trigger"         --+     |
                                         '-> GameServer.fire_trigger(...)
                                               |
                                               |- _resolve_target -> [dev, ...]
                                               |- expand_script    -> [LightCue(when=at+offset), ...]
                                               |- _dispatch_cues   (existing path, unchanged)
                                               |    '-> on_light_cue / on_play_cue
                                               '- _notify("on_trigger_fired", record)

delivery (all pre-existing)
  DeviceLinkAgent._on_light_cue
    when - horizon still future?  -> hold on _light_cues        (F3)
    else                          -> feed session now
  _render_room / _render_frames   -> /<dev>/leds stamped `when`

observation
  ConsoleAgent.on_trigger_fired   -> broadcast trigger_fired
  (uplink could attach here later; not wired in this slice)
```

---

## 12. Error handling

- `fire_trigger` never raises and returns a located reason: `no Bit running`,
  `unknown trigger 'x'`, `trigger 'x' targets the firing device; no device given`.
- `Bit.trigger_table` raising is caught and treated as an empty table at fire
  time, and as a `BitLoadError` at load, matching how `verb_handlers()` raising is
  already handled in `data()`.
- A raising `on_trigger_fired` observer is logged by `_notify` and never reaches
  the engine or its peers (F5).
- A trigger whose target resolves to nothing dispatches nothing and still emits a
  record, with `devs=()` and `steps=0`. A fire that reached nothing must be
  visible as such, not absent.
- A `ROOM`-addressed step with no Room bound is dropped by the existing
  `_resolve_dev`, which already warns once per Bit load rather than once per cue.
- Pending script cues are cleared at `UNLOADING` (section 7.3).
- A Bit with no `trigger_table` behaves exactly as today: an empty list on the
  wire, and the panel reads "No triggers declared".

---

## 13. Testing

The suite must stay green with no O2, no Arco and no pyarco importable. Run it
through the project venv, never a bare interpreter, and note that a fresh
worktree has no `.venv` at all:

```bash
ln -s /Users/chris/projects/mm-terrarium/.venv .venv
```

```bash
.venv/bin/python -m pytest tests -v
```

`control/triggers.py` and `control/trigger_view.py` are pure and unit-test
directly: one case per refusal in section 6's table, plus expansion against each
target with zero, one and several resolved devs.

Four tests are load-bearing:

1. **The declared-but-unimplemented regression.** A Bit declaring a
   `GESTURE_VERB` condition on a verb its `verb_handlers()` does not implement
   fails `load_bit` as a `BitLoadError`, and Control returns to `IDLE`. This is
   goal 4, and it is the test a future refactor of `load_bit`'s ordering is most
   likely to break.
2. **`fired_by` never inherits `declared_source`.** Fire `flash_device`
   (`declared_source="gesture-verb"`) through the Console path and assert the
   record reads `fired_by="admin-manual"`. Both halves in one assertion, because
   the whole safety argument for manual fire is that they stay distinguishable.
3. **The observer guard.** An observer whose `on_trigger_fired` raises must not
   prevent the script's cues from being dispatched, must not stop a second
   observer being notified, and must not escape `fire_trigger`. Mirrors the
   existing guard regressions for `on_release` and `on_light_cue`.
4. **A script cannot outlive its Bit.** A step scheduled past the Bit's
   completion is not fed to the Room bridge after `UNLOADING` (section 7.3).

Also covered: `_dispatch_cues` still handles the three existing cue kinds
unchanged with a `FireTrigger` in the same list; a `FireTrigger` naming an unknown
trigger is dropped and logged without breaking dispatch of its neighbors;
`triggers_changed` broadcasts on change only; and a `GameServer` built the pre-Room
way fires a `ROOM`-target trigger to nothing without raising.

**The browser half gets behavioral tests, not greps** (F11).
`tests/js/trigger_panel_behavior.test.js`, wrapped by
`tests/test_trigger_panel_behavior.py` with the same clean node skip, drives the
real shipped `triggers.js` and asserts:

- the card list renders one card per trigger with every script step visible
- **the card list DOM node survives a `trigger_fired` re-render with its children
  intact.** This is the exact defect class that reached a live browser in Spec A,
  and `trigger_fired` is the high-frequency event here in the same way
  `room_changed` was there
- the Fire button sends `{"command": "fire_trigger", "name": ...}`, with `dev`
  present for a `DEVICE`-target card and absent otherwise
- `console.js`'s `handle()` routes `triggers_changed` and `trigger_fired` to the
  right renderer with the right payload
- an `admin-manual` fire renders with its distinguishing tag

**Per boundary rule 5, any double added here must not be more permissive than
what it stands for.** The relevant one is the existing `FakeConsoleServer`, whose
real counterpart drops slow and dead clients.

### 13.1 Live verification

Run `harness/run_stack.py --console-port N`, open the Console with **no device
joined**, and fire `play_aurora` by hand. Confirm the Room's three zones sweep
through the declared steps at the declared offsets, that the log line reads
`ADMIN MANUAL`, and that the Room's role name, counts and node id are still
absent from every panel.

The no-device-joined property is the point, not a convenience, for the reason in
section 1: headless device clock sync is 1 of 3 and upstream. This slice's
acceptance must not be gated behind a join, and unlike every previous slice it
does not have to be.

---

## 14. Non-goals

Explicitly out of scope, and compatible with every success criterion below:

- **The N-fixture Room.** Spec C, section 4.2. `Room.bound_dev` stays singular,
  `RoleClass.ROOM` stays capacity 1, and `RoomProfile` stays single-surface.
- **Named-fixture targets.** Section 4.1.
- **Wiring `on_trigger_fired` into the uplink.** The hook is multi-observer
  precisely so this is a later one-liner, but fairyring does not exist and
  inventing its event shape now would be speculative.
- **Any condition expression language.** Decision 4: the Bit evaluates, Control
  observes.
- **Note-on or signature triggering of a luxaeterna instrument.** Section 3.
- **A timed `PlayCue`.** Section 6. If a later slice wants delayed samples, that
  is a decision about `PlayCue`, not about scripts.
- **Scoring.** `on_complete()` remains a stub hook. A trigger is not a score.
- **Authentication.** The Console's trusted-LAN, no-auth,
  `127.0.0.1`-by-default model is unchanged and remains load-bearing. Manual fire
  makes that assumption carry slightly more weight, which is recorded in
  section 15.

---

## 15. Success criteria

1. A Bit declares a `TriggerTable` alongside its `RoleTable`, and a Bit that
   declares none is byte-identical in behavior to today.
2. A trigger whose `GESTURE_VERB` condition names a verb the Bit does not
   implement fails `load_bit` as a `BitLoadError`, with a message naming the
   trigger and the verb.
3. The Console renders one card per trigger showing its description, target,
   condition, and every script step with its offset, controller and value.
4. An operator fires any declared trigger from the Console, and a `DEVICE`-target
   trigger can be fired at a chosen device.
5. Every fire emits a `TriggerFired` whose `fired_by` reports what actually fired
   it, distinct from the `declared_source` the condition declares.
6. Firing `play_aurora` moves the Room's declared `aurora` through its three
   steps at the declared offsets, with no device joined and no Arco required for
   the light half.
7. `control/engine.py` never evaluates a condition. Its only knowledge of a
   trigger is its declaration, its target resolution, and its script.
8. `_resolve_target` returns a list, so Spec C changes what it returns and no Bit
   declaration.
9. The Room's role name, its registration counts, and `ROOM_NODE_IDS` still
   appear nowhere in any Console or uplink payload, asserted alongside the new
   `triggers` key being populated.
10. `triggers.js` has behavioral tests, and the card list survives a
    `trigger_fired` re-render with its children intact.
11. The suite passes offline with no O2, no Arco and no pyarco, and does not
    regress from the baseline of **844 passed, 1 skipped**.

---

## 16. Open questions

Recorded rather than resolved, none blocking:

- **Manual fire raises the stakes on the no-auth trust model.** Reading a panel
  and firing a venue's lighting are different acts, and the Console's model was
  drawn for the first. It is still `127.0.0.1` by default with LAN exposure an
  explicit opt-in, so nothing regresses here, but this is the first write path
  that does something an audience can see. Worth revisiting when the Console
  first faces anything less trusted than a venue laptop.
- **Whether `ALL` should include the Room.** Defined here as including it, on the
  reading that an operator picking "all" means the whole installation. A Bit that
  wants players only can declare `DEVICE` per-device or omit `ALL` entirely, so
  this is reversible; it is called out because the two readings are equally
  defensible and only one can be the default.
- **Whether a trigger should be able to fire another trigger.** Deliberately not
  designed: a script step is a cue, never a `FireTrigger`, so chaining is
  impossible by construction and cannot produce a cycle. If chaining is ever
  wanted, cycle detection at load is the price and should be paid deliberately.
- **`TestBit`'s adjudication is a fixture, not a game.** "Full tilt deflection N
  times" exists to be deterministic and testable, not because it is a good round.
  It should be revisited when a production Bit declares what winning actually
  means.
