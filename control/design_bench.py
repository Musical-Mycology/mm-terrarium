"""DesignBench: an offline, single-instrument bench engine for the design
panel (see docs/superpowers/sdd/2026-08-31-design-bench-and-calibrate).

Drives one Instrument against a `BenchSession` (feed_midi/render/close) with
no Arco, no transport, and no Bit -- a designer previews an instrument's
generators, fire ladder, and stream triggers standalone. Pure stdlib
(control/ discipline).

Two deliberate simplifications from the full engine (control/engine.py),
both because a bench has exactly one surface and no audio path:

  - PlayCue is skipped entirely. Audio playback is out of scope for a
    bench session; expand_script still produces PlayCues for a fired
    function that declares one, DesignBench just drops them on the floor.
  - Firing any function other than "stop" clears the mute latch. The real
    engine's rule (control/engine.py's _clear_mutes) is "any non-mute
    fire clears the mute", evaluated per resolved dev across possibly many
    surfaces; a bench has exactly one surface, so "not stop" and
    "not a MuteCue-only fire" collapse to the same test.

Fire ladder (mirrors GameServer._resolve_script_for, control/engine.py:701):
builtins first (reserved names make shadowing impossible), then the
instrument's own SCRIPTED function of that name.

Stream smoothing (mirrors GameServer._apply_stream_triggers,
control/engine.py:484): the "smooth" transform is an EMA seeded from the
FIRST sample (y = x when there is no prior value), not from 0, and the
bench keeps its own per-trigger state independent of any real engine.
"""
from __future__ import annotations

import heapq
import time
from typing import Protocol

from control.builtins import builtin_functions
from control.cues import LightCue, MuteCue, PlayCue, SolidCue
from control.functions import FunctionKind, expand_script


class BenchSession(Protocol):
    def feed_midi(self, status: int, d1: int, d2: int) -> None: ...
    def render(self) -> list[int]: ...
    def close(self) -> None: ...


def _lane_of(cue) -> tuple[str, int, int] | None:
    if isinstance(cue, LightCue):
        return (cue.dev, cue.status, cue.data1)
    return None


class DesignBench:
    """Bench engine for one Instrument, driving one BenchSession."""

    def __init__(self, instrument, session: BenchSession,
                 clock=time.monotonic) -> None:
        self._instrument = instrument
        self._session = session
        self._clock = clock
        self._start = clock()
        from control.generator_runner import GeneratorRunner
        generators = [fn for fn in instrument.functions
                     if fn.kind is FunctionKind.GENERATOR]
        self._runner = GeneratorRunner(generators)
        self._heap: list[tuple[float, int, int, int]] = []
        self._solid: tuple[tuple[int, int, int], float, float] | None = None
        self._muted = False
        self._ema_state: dict[str, float] = {}
        self._last_frame: list[int] | None = None
        # A fire() changes latch/override/schedule state even when that
        # doesn't (yet) change the rendered pixel values -- e.g. latching
        # an already-black frame. Force the next tick() to report such a
        # state change rather than silently swallowing it as "unchanged".
        self._dirty = False

    def _elapsed(self) -> float:
        return self._clock() - self._start

    def tick(self) -> list[int] | None:
        elapsed = self._elapsed()
        while self._heap and self._heap[0][0] <= elapsed:
            _, status, data1, data2 = heapq.heappop(self._heap)
            self._session.feed_midi(status, data1, data2)
        for _dev, status, data1, value in self._runner.cues(elapsed, elapsed):
            self._session.feed_midi(status, data1, value)
        frame = self._session.render()
        if self._solid is not None:
            rgb, level, expires_at = self._solid
            if expires_at is None or elapsed < expires_at:
                frame = [max(0, min(255, int(round(rgb[i % 3] * level))))
                          for i in range(len(frame))]
            else:
                self._solid = None
        if self._muted:
            frame = [0] * len(frame)
        unchanged = (self._last_frame is not None and frame == self._last_frame
                    and not self._dirty)
        self._dirty = False
        if unchanged:
            return None
        self._last_frame = frame
        return list(frame)

    def _resolve(self, name: str):
        builtin = builtin_functions(self._instrument).get(name)
        if builtin is not None:
            return builtin
        for fn in self._instrument.functions:
            if fn.name == name and fn.kind is FunctionKind.SCRIPTED:
                return fn
        return None

    def fire(self, name: str) -> str | None:
        fn = self._resolve(name)
        if fn is None:
            return f"no function {name!r} on this instrument"
        self._dirty = True
        if name != "stop":
            self._muted = False
        elapsed = self._elapsed()
        expanded = expand_script(fn, at=elapsed, devs=("bench",))
        lanes: set[tuple[str, int, int]] = set()
        last_when = elapsed
        for cue in expanded:
            if isinstance(cue, PlayCue):
                continue
            if isinstance(cue, SolidCue):
                duration = cue.duration
                expires_at = None if duration is None else cue.when + duration
                self._solid = (cue.rgb, cue.level, expires_at)
                last_when = max(last_when, cue.when)
                continue
            if isinstance(cue, MuteCue):
                self._muted = True
                continue
            lane = _lane_of(cue)
            if lane is not None:
                lanes.add(lane)
            when = cue.when if cue.when is not None else elapsed
            last_when = max(last_when, when)
            heapq.heappush(self._heap, (when, cue.status, cue.data1, cue.data2))
        if lanes:
            self._runner.suppress(lanes, until_at=last_when)
        return None

    def fireable(self) -> list[dict]:
        out: list[dict] = []
        builtins = builtin_functions(self._instrument)
        for name, fn in builtins.items():
            out.append({"name": name, "description": fn.description,
                       "source": "builtin"})
        for fn in self._instrument.functions:
            if fn.kind is not FunctionKind.SCRIPTED:
                continue
            if fn.name in builtins:
                continue
            out.append({"name": fn.name, "description": fn.description,
                       "source": "instrument"})
        return out

    def lane(self, verb: str, value: float, status: int, data1: int) -> None:
        triggers = [t for t in self._instrument.stream_triggers
                    if t.verb == verb]
        y = value
        for trig in triggers:
            if trig.transform != "smooth":
                continue
            alpha = trig.params.get("alpha", 1.0)
            y_prev = self._ema_state.get(trig.name)
            y = value if y_prev is None else alpha * value + (1 - alpha) * y_prev
            self._ema_state[trig.name] = y
        scaled = max(0, min(127, int(round((y + 1.0) / 2.0 * 127))))
        self._session.feed_midi(status, data1, scaled)

    def close(self) -> None:
        self._ema_state.clear()
        self._solid = None
        self._muted = False
        self._session.close()
