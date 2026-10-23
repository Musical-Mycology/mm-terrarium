"""Look-ahead MIDI sequencer: MidiFiles on one beat grid, as timed MusicCues.

A Bit owns one of these and calls `advance(at)` from every engine tick; the
cues it returns carry their own `when`, so the Room speaker plays each note
on the grid regardless of the 44 Hz tick it was computed on. `lookahead`
covers the gap between ticks (plus jitter): every tick emits whatever falls
before `at + lookahead` that has not been emitted yet.

Every track runs on the grid from `start()` whether it is heard or not, so
switching one on or off is a question of *which* of its notes reach the
queue, never of re-aligning it. That is what lets a tap act immediately
rather than on the next bar:

- `enable` chases: a note already under way at `at` with at least
  `_CHASE_MIN_BEATS` still to sound (a pad chord) is re-struck at `at`, and
  the cursor resumes there so the rest of the bar arrives from `advance` as
  usual. A shorter remainder (a kick) is left out: re-struck, it would land
  off the grid.
- `disable` purges the track's still-queued cues (MusicPurge by tag), then
  at `at` releases every key the track has struck since `start()`, and the
  sustain pedal, unconditionally. It cannot release only what it believes
  is still down: the device applies the purge on arrival, ahead of `at`, so
  it also drops cues due in that gap -- including the note-off or pedal-up
  of a note already sounding, and even an earlier disable's own releases
  if the track was switched back on and off within that gap. So the
  struck set is never cleared between disables (at most 128 keys). A
  redundant note-off or pedal-up is a no-op on the synth; a missing one is
  a hung note.

One-shots (`loop=False`) start on the next whole beat via `play_once` and
switch themselves off after their last event.

The file's own tempo is ignored (everything plays at this Sequencer's bpm),
and its CC 7 is scaled by `set_master`, so a file that sends full volume can
never override a ducked master.

Stdlib only, like the rest of control/: it runs in the offline suite and on
the venue box alike.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field

from control.cues import ROOM, MusicCue, MusicPurge
from control.midifile import MidiEvent, MidiFile

# CC 7 assumed for a file that never sends one: the GM power-on default.
_DEFAULT_CC7 = 100

# Beat positions computed from wall-clock times are snapped to this many
# decimal places, so `enable(time_at(4))` lands on beat 4.0 and not on
# 3.9999999999 (which would play the beat-4 note twice: chased, then again).
_SNAP_DIGITS = 9

# `enable` re-strikes a note under way only if this many beats of it remain.
_CHASE_MIN_BEATS = 1.0


def _snap(beat: float) -> float:
    return round(beat, _SNAP_DIGITS)


@dataclass(frozen=True)
class _Note:
    """A note's span in track beats, for chase. `status` keeps the file's
    note-on status byte so the re-strike matches the original."""
    on: float
    off: float
    status: int
    key: int
    velocity: int


@dataclass
class _Track:
    name: str
    part: int
    loop: bool
    events: tuple[MidiEvent, ...]   # beat = track beat (one-shot: lead-in removed)
    beats: list[float]              # events' beats, for bisect
    length: float                   # loop length; one-shot: last event's beat
    notes: tuple[_Note, ...]
    initial_cc7: int
    on: bool = False
    origin: float = 0.0             # grid beat of track beat 0
    cursor: float = 0.0             # next grid beat not yet emitted
    cc7: int = _DEFAULT_CC7         # last CC 7 the file sent (unscaled)
    struck: set[int] = field(default_factory=set)  # keys since start()


def _drop_struck_at_end(events, length: float) -> tuple[MidiEvent, ...]:
    """A loop's events without the notes struck at or past its length (a
    zero-length note on the end bar line, as piano.mid has): played, they
    would sound on the next iteration's downbeat. Each dropped note-on takes
    its paired note-off (the next of the same key, a velocity-0 note-on
    included) with it; a note-off past the length for a note struck inside
    the loop is kept."""
    open_: dict[int, list[bool]] = {}   # key -> per open note-on: dropped?
    out = []
    for e in events:
        kind = e.status & 0xF0
        if kind == 0x90 and e.data2 > 0:
            dropped = e.beat >= length
            open_.setdefault(e.data1, []).append(dropped)
            if dropped:
                continue
        elif kind in (0x80, 0x90) and open_.get(e.data1):
            if open_[e.data1].pop(0):
                continue
        out.append(e)
    return tuple(out)


def _pair_notes(events) -> tuple[_Note, ...]:
    """Pair each note-on with the next note-off of the same key; a note-on
    with no later note-off has no span and is never chased."""
    open_: dict[int, list[MidiEvent]] = {}
    notes = []
    for e in events:
        kind = e.status & 0xF0
        if kind == 0x90:
            open_.setdefault(e.data1, []).append(e)
        elif kind == 0x80 and open_.get(e.data1):
            on = open_[e.data1].pop(0)
            notes.append(_Note(on.beat, e.beat, on.status, on.data1, on.data2))
    return tuple(notes)


class Sequencer:
    def __init__(self, bpm: float, *, dev: str = ROOM,
                 lookahead: float = 0.25) -> None:
        self.bpm = bpm
        self.dev = dev
        self.lookahead = lookahead
        self._master = 1.0
        self._t0: float | None = None
        self._tracks: dict[str, _Track] = {}

    # -- setup ------------------------------------------------------------

    def add_track(self, name: str, midi: MidiFile, *, part: int, loop: bool,
                  skip_beats: float = 0.0) -> None:
        """Looped tracks repeat every `midi.loop_beats()` from grid beat 0,
        without any note struck at or past that length. A one-shot drops events before `skip_beats` (a lead-in) and shifts
        the rest so `skip_beats` lands on the beat `play_once` picks."""
        if loop:
            if skip_beats:
                raise ValueError("skip_beats applies to one-shots only")
            length = midi.loop_beats()
            events = _drop_struck_at_end(midi.events, length)
        else:
            events = tuple(MidiEvent(e.beat - skip_beats, e.status, e.data1,
                                     e.data2)
                           for e in midi.events if e.beat >= skip_beats)
            length = events[-1].beat if events else 0.0
        cc7 = next((e.data2 for e in events
                    if e.status & 0xF0 == 0xB0 and e.data1 == 7), _DEFAULT_CC7)
        self._tracks[name] = _Track(
            name=name, part=part, loop=loop, events=events,
            beats=[e.beat for e in events], length=length,
            notes=_pair_notes(events), initial_cc7=cc7, cc7=cc7)

    def start(self, t0: float) -> None:
        """Grid beat 0 is at `t0`; every track is off."""
        self._t0 = t0
        for tr in self._tracks.values():
            tr.on = False
            tr.struck = set()
            tr.cc7 = tr.initial_cc7

    # -- grid -------------------------------------------------------------

    def beat_at(self, t: float) -> float:
        if self._t0 is None:
            raise RuntimeError("Sequencer.start() has not been called")
        return (t - self._t0) * self.bpm / 60.0

    def time_at(self, beat: float) -> float:
        if self._t0 is None:
            raise RuntimeError("Sequencer.start() has not been called")
        return self._t0 + beat * 60.0 / self.bpm

    # -- control ----------------------------------------------------------

    def is_on(self, name: str) -> bool:
        return self._tracks[name].on

    def enable(self, name: str, at: float) -> list:
        """Bring a looped track in at `at`: its current CC 7, then a note-on
        at `at` for every note struck strictly before `at` and still held
        for at least `_CHASE_MIN_BEATS` after it. An event exactly at `at`
        is left to `advance`."""
        tr = self._tracks[name]
        if not tr.loop:
            raise ValueError(f"{name} is a one-shot; use play_once")
        if tr.on:
            return []
        b = _snap(self.beat_at(at))
        tr.on, tr.cursor = True, b
        out = [self._cc7_cue(tr, at)]
        k = math.floor(b / tr.length)
        chased = set()
        for n in tr.notes:
            for kk in (k, k - 1):
                base = kk * tr.length
                if (kk >= 0 and n.key not in chased
                        and base + n.on < b
                        and base + n.off - b >= _CHASE_MIN_BEATS):
                    chased.add(n.key)
                    out.append(self._cue(tr, n.status, n.key, n.velocity, at))
                    tr.struck.add(n.key)
        return out

    def disable(self, name: str, at: float) -> list:
        """Drop the track at `at`: purge its queued cues, then at `at` a
        note-off for every key it has struck since `start()` and CC 64 = 0
        -- whether or not it believes them released, since the purge may
        have eaten the release (see the module doc)."""
        tr = self._tracks[name]
        if not tr.on:
            return []
        out: list = [MusicPurge(self.dev, name)]
        out += [self._cue(tr, 0x80, key, 0, at) for key in sorted(tr.struck)]
        out.append(self._cue(tr, 0xB0, 64, 0, at))
        tr.on = False
        return out

    def play_once(self, name: str, at: float) -> list:
        """Start a one-shot from the top on the next whole beat (`at`'s own
        beat if exactly on one). Returns its scaled CC 7 at the start time;
        a one-shot still playing is dropped first, as by `disable`."""
        tr = self._tracks[name]
        if tr.loop:
            raise ValueError(f"{name} is a loop; use enable")
        out = self.disable(name, at) if tr.on else []
        start = float(math.ceil(_snap(self.beat_at(at))))
        tr.on, tr.origin, tr.cursor = True, start, start
        tr.cc7 = tr.initial_cc7
        out.append(self._cc7_cue(tr, self.time_at(start)))
        return out

    def set_master(self, level: float, at: float) -> list[MusicCue]:
        """Master volume: CC 7 at `at` on every track's part, scaling the
        last CC 7 its file sent. Later file CC 7s are scaled the same way."""
        self._master = level
        return [self._cc7_cue(tr, at) for tr in self._tracks.values()]

    # -- the tick ---------------------------------------------------------

    def advance(self, at: float) -> list[MusicCue]:
        """Every enabled track's not-yet-emitted events with
        `when < at + lookahead`, in time order."""
        end = _snap(self.beat_at(at + self.lookahead))
        now = _snap(self.beat_at(at))
        out: list[tuple[float, MusicCue]] = []
        for tr in self._tracks.values():
            if not tr.on:
                continue
            if tr.cursor < end:
                for g, ev in self._window(tr, tr.cursor, end):
                    out.append((g, self._event_cue(tr, ev, g)))
                    if ev.status & 0xF0 == 0x90:
                        tr.struck.add(ev.data1)
                tr.cursor = end
            if not tr.loop and tr.cursor > tr.origin + tr.length \
                    and now > tr.origin + tr.length:
                tr.on = False   # its last note-off may still be queued
        out.sort(key=lambda item: item[0])
        return [cue for _, cue in out]

    # -- internals --------------------------------------------------------

    def _window(self, tr: _Track, lo: float, hi: float):
        """(grid beat, event) for every event of `tr` in [lo, hi), in grid
        order. A loop's events past its length (a note-off on the loop's end
        bar line) belong to the iteration that struck them, so they sort
        ahead of the next iteration's events at the same beat."""
        if not tr.loop:
            i = bisect_left(tr.beats, lo - tr.origin)
            j = bisect_left(tr.beats, hi - tr.origin)
            return [(tr.origin + e.beat, e) for e in tr.events[i:j]]
        if not tr.events:
            return []
        last = tr.beats[-1]
        out = []
        k = max(0, math.floor((lo - last) / tr.length))
        while k * tr.length < hi:
            base = k * tr.length
            i = bisect_left(tr.beats, lo - base)
            j = bisect_left(tr.beats, hi - base)
            out += [(base + e.beat, e) for e in tr.events[i:j]]
            k += 1
        out.sort(key=lambda item: item[0])   # stable: iteration, file order
        return out

    def _event_cue(self, tr: _Track, ev: MidiEvent, beat: float) -> MusicCue:
        if ev.status & 0xF0 == 0xB0 and ev.data1 == 7:
            tr.cc7 = ev.data2
            return self._cc7_cue(tr, self.time_at(beat))
        return self._cue(tr, ev.status, ev.data1, ev.data2, self.time_at(beat))

    def _cc7_cue(self, tr: _Track, when: float) -> MusicCue:
        value = max(0, min(127, round(tr.cc7 * self._master)))
        return self._cue(tr, 0xB0, 7, value, when)

    def _cue(self, tr: _Track, status: int, d1: int, d2: int,
             when: float) -> MusicCue:
        return MusicCue(self.dev, (status & 0xF0) | tr.part, d1, d2, when,
                        tag=tr.name)
