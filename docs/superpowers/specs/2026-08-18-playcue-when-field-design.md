# `PlayCue` gains a `when` field

Give `PlayCue` the same optional timing `LightCue` already has, so a Bit can
schedule a device-local sample to fire at a specific presentation time
instead of only on arrival.

Motivated by a hypothetical scoring flow worked through interactively: a
per-player stinger that should land on a musical beat, not whenever the
message happens to arrive. `PlayCue` today is explicitly untimed by design
("the device owns when a local sample fires, so there is nothing on this
path for Control to schedule" — `control/cues.py:29-31`), and that design
predates this need.

Baseline at the time of writing: `ad3ff9a`, **830 passed, 1 skipped**, fully
offline.

## 1. What's already there

Traced directly against the code, not from memory:

- `control/cues.py` — `PlayCue(dev, name, params)`, no `when`. `LightCue`
  has `when: float | None = None`.
- `control/engine.py:244-289` (`GameServer._dispatch_cues`) — resolves
  `ROOM`/dev targeting for every cue kind, computes `at = origin +
  cue_horizon` once per gesture, and for `LightCue` resolves
  `when = at if cue.when is None else cue.when` before calling the sink.
  `PlayCue`'s branch (`:263-267`) unpacks only `(dev, cue.name,
  cue.params)` — `at` is computed but discarded on this path.
- `control/timed_queue.py` — fully generic: `push(when, payload, now)`,
  `due(now)`. `when=None` releases at the next drain and is not a clamp;
  a `when` already past releases at the next drain and **is** a clamp.
  Nothing here needs to change for this feature — it already does exactly
  what a timed `PlayCue` needs.
- `devicelink/agent.py:492-516` (`_on_light_cue`) — the two-pattern
  precedent. Room-targeted: pushed onto `self._room_cues`, released
  exactly at `when` — pure Control-side scheduling. Device-targeted:
  fed *early* (`when - cue_horizon`) so the frame crosses the wire in
  time, and **the device holds it and displays at `when`** — network
  jitter is kept out of the timing budget by not needing the device to
  wait at all.
- `devicelink/agent.py:518-522` (`_on_play_cue`) — untimed, confirmed:
  calls `self._send(dev, protocol.play_event(dev, name, params))`
  synchronously, no queue involved.
- `devicelink/protocol.py:110-126` (`play_event`) — no `when` parameter,
  and its call to `_event(...)` omits `timestamp=`, so it always defaults
  to `0.0`. The wire envelope already has a `timestamp` field on every
  `/ie<N>/play` message sent today — it is simply always `0.0`. No wire
  format change is needed, only plumbing to populate it.
- `devicelink/o2_transport.py`, `devicelink/server.py` — neither
  implements `on_light_cue`/`on_play_cue`. `DeviceLinkAgent` is the single
  dispatch point for both the websocket and o2lite transports; a change
  here covers both automatically.
- Clock basis (`docs/MM_TERRARIUM.md:801-812`, `:1238-1248`): on the
  o2lite transport, `DeviceLinkAgent` is stamped off `o2lite.time_get()` —
  genuinely Arco-synced, holds over a real network. On the websocket
  transport it's `time.monotonic`, valid only because the simulator is
  always a local subprocess of Control. This is the exact clock
  `LightCue.when` already runs on.

## 2. The clock invariant

**`PlayCue.when` lives on the identical timeline `LightCue.when` already
uses — `GameServer._clock()`/`_horizon` for computing `at`, and
`DeviceLinkAgent._clock` for `TimedQueue` scheduling.** No new clock
source, no new sync path, and no change to which transport is considered
network-safe for timing (o2lite: yes; websocket: only because the
simulator is local). This spec adds no timing infrastructure — it reuses
the single Arco-anchored timeline the cue system already depends on.

## 3. Design: one queue, a fast path, no room/device split

Two things ruled this shape in, deliberately different from `LightCue`'s:

- **`LightCue` needs two patterns because it pre-feeds.** A frame has to
  arrive before `when` so the device can hold and display it exactly
  then. `PlayCue.when` does not pre-feed — Control holds the cue and
  fires the `/ie<N>/play` message *at* `when`. There is nothing for a
  device to hold, so there is no room/device asymmetry to encode. One
  `TimedQueue` serves both targets.
- **Untimed and already-past `PlayCue` must stay exactly as fast as
  today.** `test_play_cue_is_sent_to_the_device` (`tests/test_devicelink_
  agent.py:374-404`) asserts a synchronous send with `timestamp: 0.0`, no
  intervening tick. Routing every `PlayCue` through the queue — even
  `when=None` — would add up to one tick of latency to every existing
  call for no benefit, and would break that test's premise. So
  `_on_play_cue` gets `LightCue`'s own fast-path shape: only a
  genuinely-future `when` enters the queue.

`control/cues.py`:
```python
@dataclass(frozen=True)
class PlayCue:
    dev: str
    name: str
    params: str = ""
    when: float | None = None
```
Default preserves every existing 3-positional-arg `PlayCue(dev, name,
params)` construction unchanged.

`control/engine.py`, `_dispatch_cues`'s `PlayCue` branch — mirror the
`LightCue` branch exactly:
```python
if isinstance(cue, PlayCue):
    dev = self._resolve_dev(cue.dev)
    if dev is None:
        continue
    when = at if cue.when is None else cue.when
    sink, args = self.on_play_cue, (dev, cue.name, cue.params, when)
```
`on_play_cue`'s type hint (declared alongside `on_light_cue` near
`control/engine.py:60-67`) gains the fourth parameter.

`devicelink/agent.py`, `_on_play_cue`:
```python
def _on_play_cue(self, dev: str, name: str, params: str,
                 when: float | None = None) -> None:
    now = self._clock()
    if when is not None and when > now:
        self._play_cues.push(when, (dev, name, params, when), now=now)
        return
    self._send(dev, protocol.play_event(
        dev, name, params, 0.0 if when is None else when))
```
`when` is stored inside the payload as well as used as the push key,
matching `_on_light_cue`'s own precedent (`_light_cues.push(feed_at, (dev,
status, data1, data2, when), now=now)`) — `TimedQueue.due()` yields only
the payload, not the push key, so a caller needing the original `when` on
the wire has to carry it there itself; `TimedQueue` makes no promise that
the release-time equals the push-time.

A `_drain_play_cues` sibling to the existing `_drain_light_cues`, called
from wherever `_room_cues`/`_light_cues` already drain each tick (exact
call site is a planning-time detail, not a design one — this spec commits
to the pattern, not the line number):
```python
def _drain_play_cues(self) -> None:
    for dev, name, params, when in self._play_cues.due(self._clock()):
        self._send(dev, protocol.play_event(dev, name, params, when))
```

`devicelink/protocol.py`, `play_event`:
```python
def play_event(dev: str, name: str, params: str = "",
               when: float = 0.0) -> dict:
    return _event(f"/{dev}/play", "ss", [name, params], timestamp=when)
```

`self._play_cues = TimedQueue()` added alongside the existing
`self._room_cues`/`self._light_cues` in `DeviceLinkAgent.__init__`.

## 4. Semantics, stated plainly

- `when=None`: send immediately, wire `timestamp=0.0` — byte-identical to
  today's behavior. Not a clamp.
- `when` given but already at-or-before now: send immediately, wire
  `timestamp=when` (the real, past value — not rewritten to `0.0`). Not
  counted as a clamp, because it never reaches `TimedQueue.push()` — it
  takes the same fast path as `None`. This is a deliberate asymmetry with
  `LightCue`'s Room-targeted path, which pushes unconditionally and so
  *does* clamp-count a late Room cue; `PlayCue` has no equivalent
  unconditional push, so there is nothing to clamp-count on the fast
  path. Recorded here so it doesn't read as an oversight later.
- `when` genuinely in the future: queued, released and sent at that time
  on `DeviceLinkAgent`'s clock, wire `timestamp=when`.
- Room-targeted `PlayCue` (`dev == ROOM` resolved to the bound dev before
  `_on_play_cue` ever sees it, same as today) behaves identically to a
  device target — no branch needed, confirmed already covered by
  `test_play_cue_can_target_the_room_too`.

## 5. Testing plan

Mirrors patterns already established for `LightCue`, cited so the
implementer transcribes rather than invents:

**Engine-level** (`tests/test_engine_data.py`), mirroring
`test_light_cue_carries_its_time` / `test_explicit_light_cue_time_wins_
over_at` / `test_untimed_cue_is_stamped_with_at`:
- `PlayCue(dev, name, params, when=X)` → `on_play_cue` called with
  `when=X` unchanged.
- Explicit `when` wins over the computed `at`.
- `when=None` → `on_play_cue` called with the computed `at`.
- Regression: existing 3-positional-arg `PlayCue` construction still
  passes through `verb_handlers()` unchanged (`when` defaults `None`,
  dispatch resolves it to `at`, matching current behavior byte-for-byte
  at the sink boundary — the CURRENT tests already assert the 3-tuple
  sink signature; add one asserting the 4th arg equals `at`).

**Agent-level** (`tests/test_devicelink_agent.py`), mirroring
`test_a_timed_room_cue_is_withheld_until_its_time` / `test_an_untimed_
room_cue_still_applies_on_arrival` / `test_a_late_room_cue_applies_and_
counts_as_clamped` / `test_room_frame_carries_a_time`:
- A future `when` is withheld until `_drain_play_cues` releases it at
  that time, not before.
- `when=None` still sends synchronously, in the same call — this is the
  regression test for `test_play_cue_is_sent_to_the_device`'s existing
  assertion; it must keep passing unmodified.
- A past `when` sends immediately, wire `timestamp` equals the real past
  value, and does **not** increment any clamp counter (per section 4 —
  write the test to prove this explicitly, since it's the one place this
  design diverges from `LightCue`'s Room path).
- A future `when`, once released, carries that same `when` as the wire
  `/ie<N>/play` message's `timestamp`.
- Room-targeted future `when` behaves identically to a device target.

No test may need `d2`, node, or the network — this is pure Python engine
and transport-adjacent logic, already covered by the existing offline
suite's patterns.

## 6. Non-goals

- **Device-side hold-and-delay is out of scope.** The alternative
  considered and rejected: feed the message early and have the device
  itself hold it until `when`, matching `LightCue`'s device-targeted
  precision model exactly. That needs new logic in `shroom_client.py` /
  `harness/o2_shroom.py` (and eventually real Tuneshroom firmware) that
  doesn't exist anywhere in this repo today — a materially larger,
  device-spanning change. If sample-accurate audio timing later proves
  necessary (LAN network latency insufficient for the musical precision
  wanted), that is a follow-up spec, not this one.
- **No change to `LightCue`, `TimedQueue`, or either transport module.**
  `TimedQueue` is already generic enough to serve `PlayCue` unmodified.
- **No new wire message type.** The `timestamp` field on `/ie<N>/play`
  already exists in every envelope sent today; this only populates it
  with something other than a hardcoded `0.0`.
- **No change to `Role.samples` or how a Bit declares which sample names
  a role may trigger.** Orthogonal to timing.
