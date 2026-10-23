"""Tests for control.sequencer -- the look-ahead MIDI sequencer a Bit drives.

Small MidiFiles are built inline so each test states the exact notes it is
about; the loop, master and one-shot tests use the Deux Ara Drop assets where
the brief names them. The stuck-note tests replay every returned cue through
a model of the real Room queue: cues carry presentation times `at`, which the
engine dispatches one cue horizon early, and a MusicPurge drops every queued
cue of its tag that has not played by the time it arrives -- including cues
due between its arrival and its `at`.
"""

import random
from collections import Counter
from pathlib import Path

import pytest

from control.cues import ROOM, MusicCue, MusicPurge
from control.midifile import MidiEvent, MidiFile, read_midi_file
from control.sequencer import Sequencer

ASSETS = Path(__file__).resolve().parent.parent / "bits" / "deux_ara_drop" / "assets"
BPM = 130
T0 = 100.0
TICK = 1 / 44
HORIZON = 0.060  # cue horizon: a cue for `at` reaches the device at at - 0.060


def mf(*events, end=None):
    """A MidiFile from (beat, status, d1, d2) tuples, sorted by beat."""
    evs = tuple(MidiEvent(*e) for e in sorted(events, key=lambda e: e[0]))
    if end is None:
        end = max(e.beat for e in evs) if evs else 0.0
    return MidiFile(ticks_per_beat=96, tempo_bpm=None, events=evs,
                    end_beat=end, name=None)


def note(key, on, off, vel=100):
    return [(on, 0x90, key, vel), (off, 0x80, key, 0)]


def asset(name):
    return read_midi_file(ASSETS / f"{name}.mid")


def started(**tracks):
    """Sequencer at 130 bpm, started at T0, with `tracks` = name -> kwargs."""
    seq = Sequencer(BPM)
    for name, kw in tracks.items():
        seq.add_track(name, **kw)
    seq.start(T0)
    return seq


def run(seq, t_from, t_to):
    """advance() at 44 Hz over [t_from, t_to]; every cue returned."""
    out = []
    t = t_from
    while t <= t_to:
        out += seq.advance(t)
        t += TICK
    return out


class RoomQueue:
    """The device's tagged cue queue. `dispatch(cues, at)` models the engine
    sending cues for presentation time `at`, which arrive at device time
    `at - HORIZON`: a MusicPurge then drops every queued cue of its tag due
    after that arrival. `play(now)` releases what is due, in time order."""

    def __init__(self):
        self.queue, self.delivered = [], []

    def dispatch(self, cues, at):
        arrival = at - HORIZON
        for c in cues:
            if isinstance(c, MusicPurge):
                self.queue = [q for q in self.queue
                              if not (q.tag == c.tag and q.when > arrival)]
            else:
                self.queue.append(c)

    def play(self, now):
        due = sorted((q for q in self.queue if q.when <= now),
                     key=lambda q: q.when)
        self.queue = [q for q in self.queue if q.when > now]
        self.delivered.extend(due)

    def hung(self):
        """(part, key)s whose last delivered note event is a note-on, and
        parts whose last delivered CC 64 is down."""
        last_note, last_pedal = {}, {}
        for c in self.delivered:
            kind, part = c.status & 0xF0, c.status & 0x0F
            if kind in (0x80, 0x90):
                last_note[(part, c.data1)] = kind
            elif kind == 0xB0 and c.data1 == 64:
                last_pedal[part] = c.data2
        return ({k for k, kind in last_note.items() if kind == 0x90},
                {p for p, v in last_pedal.items() if v != 0})


def note_ons(cues, key=None):
    return [c for c in cues if isinstance(c, MusicCue)
            and c.status & 0xF0 == 0x90 and (key is None or c.data1 == key)]


def test_grid_at_130_bpm():
    seq = started()
    assert seq.time_at(1) - seq.time_at(0) == pytest.approx(60 / 130)
    assert seq.time_at(0) == T0
    assert seq.beat_at(seq.time_at(7.5)) == pytest.approx(7.5)


def test_advance_emits_at_grid_times():
    seq = started(a=dict(midi=mf(*note(60, 1, 2)), part=3, loop=True))
    seq.enable("a", T0)
    cues = seq.advance(T0 + 0.3)
    ons = note_ons(cues, 60)
    assert len(ons) == 1
    assert ons[0].when == pytest.approx(T0 + 60 / 130)
    assert ons[0].status == 0x90 | 3
    assert ons[0].dev == ROOM and ons[0].tag == "a"


def test_disabled_track_emits_nothing():
    seq = started(a=dict(midi=mf(*note(60, 0, 1), *note(62, 2, 3)),
                         part=0, loop=True))
    assert not seq.is_on("a")
    assert run(seq, T0, T0 + 10) == []


def test_loop_repeats_at_loop_length():
    piano = asset("piano")
    assert piano.loop_beats() == 16.0
    first = next(e.data1 for e in piano.events if e.status & 0xF0 == 0x90)
    seq = started(piano=dict(midi=piano, part=0, loop=True))
    seq.enable("piano", T0)
    cues = run(seq, T0, seq.time_at(16) + 0.1)
    whens = [c.when for c in note_ons(cues, first)]
    assert whens[0] == pytest.approx(seq.time_at(0))
    assert any(w == pytest.approx(seq.time_at(16)) for w in whens)


def test_loop_drops_notes_struck_on_its_end_bar_line():
    """piano.mid strikes keys 56 and 72 at beat 16.0 == its loop length,
    with zero-length note-offs. Kept, they would land on the next
    iteration's downbeat as ghost notes; a loop drops them (and their
    paired note-offs) instead."""
    piano = asset("piano")
    seq = started(piano=dict(midi=piano, part=0, loop=True))
    seq.enable("piano", T0)
    cues = run(seq, T0, seq.time_at(32) + 0.1)
    for key in (56, 72):
        for c in [c for c in cues if c.data1 == key]:
            assert c.when != pytest.approx(seq.time_at(16))
            assert c.when != pytest.approx(seq.time_at(32))
    ons = note_ons(cues)
    first = [c for c in ons if c.when < seq.time_at(16) - 1e-9]
    second = [c for c in ons if seq.time_at(16) - 1e-9 <= c.when
              < seq.time_at(32) - 1e-9]
    assert len(first) == len(second) == 53


def test_loop_keeps_note_off_on_its_end_bar_line():
    """A note struck inside the loop whose note-off lands on the loop's
    end is kept; only note-ons at or past the end are dropped."""
    seq = started(a=dict(midi=mf(*note(60, 3, 4), *note(62, 4, 4), end=4),
                         part=0, loop=True))
    seq.enable("a", T0)
    cues = run(seq, T0, seq.time_at(4))
    assert [c.data1 for c in note_ons(cues)] == [60]
    assert any(c.status & 0xF0 == 0x80 and c.data1 == 60
               and c.when == pytest.approx(seq.time_at(4)) for c in cues)
    assert not any(c.data1 == 62 for c in cues)


def test_enable_chases_held_note():
    seq = started(pad=dict(midi=mf(*note(60, 0, 8), end=8), part=2, loop=True))
    at = seq.time_at(2)
    run(seq, T0, at - TICK)
    cues = seq.enable("pad", at)
    assert cues[0] == MusicCue(ROOM, 0xB0 | 2, 7, 100, at, tag="pad")
    assert MusicCue(ROOM, 0x90 | 2, 60, 100, at, tag="pad") in cues
    assert seq.is_on("pad")


def test_enable_does_not_chase_a_short_note():
    """A kick (0.21-0.59 beats) re-struck mid-note lands off the grid; it
    is left out, and the next kick arrives on time from advance."""
    seq = started(drums=dict(midi=asset("drums"), part=9, loop=True))
    cues = seq.enable("drums", seq.time_at(0.1))
    assert note_ons(cues) == []


def test_enable_chases_only_a_note_with_a_beat_left():
    seq = started(pad=dict(midi=mf(*note(60, 0, 8), end=8), part=2, loop=True))
    assert note_ons(seq.enable("pad", seq.time_at(7))) != []    # 1.0 left
    seq = started(pad=dict(midi=mf(*note(60, 0, 8), end=8), part=2, loop=True))
    assert note_ons(seq.enable("pad", seq.time_at(7.5))) == []  # 0.5 left


def test_enable_does_not_double_emit_boundary_note():
    seq = started(a=dict(midi=mf(*note(60, 4, 5), end=8), part=0, loop=True))
    at = seq.time_at(4)
    cues = seq.enable("a", at)
    cues += run(seq, at, seq.time_at(6))
    ons = note_ons(cues, 60)
    assert len(ons) == 1
    assert ons[0].when == pytest.approx(at)


def test_disable_purges_and_releases():
    """Every key struck since enable is released, plus the pedal -- even the
    key whose own note-off already went out."""
    synth = mf((0, 0xB0, 64, 127), *note(60, 0, 8), *note(62, 0, 0.5),
               *note(64, 4, 5), end=8)
    seq = started(synth=dict(midi=synth, part=1, loop=True))
    seq.enable("synth", T0)
    run(seq, T0, seq.time_at(1))
    at = seq.time_at(2)
    cues = seq.disable("synth", at)
    assert cues == [
        MusicPurge(ROOM, "synth"),
        MusicCue(ROOM, 0x80 | 1, 60, 0, at, tag="synth"),
        MusicCue(ROOM, 0x80 | 1, 62, 0, at, tag="synth"),
        MusicCue(ROOM, 0xB0 | 1, 64, 0, at, tag="synth"),
    ]
    assert not seq.is_on("synth")
    assert run(seq, at, at + 5) == []


def test_disable_releases_note_off_purged_in_horizon_window():
    """A note-off due in (at - HORIZON, at] is still queued when the purge
    arrives, so the purge eats it; disable must release the key anyway."""
    seq = started(synth=dict(midi=mf(*note(60, 0, 1), end=4), part=1,
                             loop=True))
    room = RoomQueue()
    room.dispatch(seq.enable("synth", T0), T0)
    t = T0
    while t < seq.time_at(1) - HORIZON:
        room.dispatch(seq.advance(t + HORIZON), t + HORIZON)
        room.play(t)
        t += TICK
    off = seq.time_at(1)
    assert any(q.status == 0x81 and q.when == pytest.approx(off)
               for q in room.queue)
    at = off + HORIZON / 2   # the note-off falls in (at - HORIZON, at]
    room.play(at - HORIZON)
    room.dispatch(seq.disable("synth", at), at)
    room.play(float("inf"))
    assert room.hung() == (set(), set())
    assert room.delivered[-2:] == [
        MusicCue(ROOM, 0x81, 60, 0, at, tag="synth"),
        MusicCue(ROOM, 0xB1, 64, 0, at, tag="synth"),
    ]


def test_disable_twice_within_horizon_keeps_first_release():
    """disable, enable, disable inside one horizon: the second purge eats the
    first disable's releases, so the second must release those keys too."""
    seq = started(synth=dict(midi=mf(*note(62, 0, 2), end=4), part=1,
                             loop=True))
    room = RoomQueue()
    room.dispatch(seq.enable("synth", T0), T0)
    at = T0
    while at < seq.time_at(2):
        room.play(at - HORIZON)
        room.dispatch(seq.advance(at), at)
        at += TICK
    # the note-off at beat 2 is still queued when the purge arrives
    for step in (seq.disable, seq.enable, seq.disable):
        room.play(at - HORIZON)
        room.dispatch(step("synth", at), at)
        at += TICK
    room.play(float("inf"))
    assert room.hung() == (set(), set())


def test_one_shot_restart_and_disable_within_horizon_releases_old_notes():
    """A one-shot that ended (by presentation time) may still have its last
    note-off queued; a restart then a disable inside the horizon purges it."""
    seq = started(one=dict(midi=mf(*note(60, 0, 2)), part=1, loop=False))
    room = RoomQueue()
    room.dispatch(seq.play_once("one", T0), T0)
    at = T0
    while seq.is_on("one"):
        room.play(at - HORIZON)
        room.dispatch(seq.advance(at), at)
        at += TICK
    assert any(q.status == 0x81 and q.data1 == 60 for q in room.queue)
    for step in (seq.play_once, seq.disable):
        room.play(at - HORIZON)
        room.dispatch(step("one", at), at)
        at += TICK
    room.play(float("inf"))
    assert room.hung() == (set(), set())


@pytest.mark.parametrize("seed", range(5))
def test_no_stuck_notes_over_random_toggles(seed):
    # Several seeds: a hang needs a disable to land within one horizon of a
    # note-off, which a single 60 s run can miss by chance.
    rng = random.Random(seed)
    names = {"piano": 0, "drums": 9, "synth": 1}
    seq = started(**{n: dict(midi=asset(n), part=p, loop=True)
                     for n, p in names.items()})
    room = RoomQueue()
    toggles = 0
    now = T0   # device time; the engine works one horizon ahead of it
    while now < T0 + 60:
        at = now + HORIZON
        room.play(now)
        if rng.random() < 0.03:
            name = rng.choice(list(names))
            if seq.is_on(name):
                room.dispatch(seq.disable(name, at), at)
            else:
                room.dispatch(seq.enable(name, at), at)
            toggles += 1
        room.dispatch(seq.advance(at), at)
        now += TICK
    at = now + HORIZON
    room.play(now)
    for name in names:
        if seq.is_on(name):
            room.dispatch(seq.disable(name, at), at)
    room.play(float("inf"))

    assert toggles > 40
    assert len(note_ons(room.delivered)) > 100
    assert room.hung() == (set(), set())


def test_master_scales_file_cc7():
    seq = started(
        arabesque=dict(midi=asset("arabesque"), part=4, loop=False,
                       skip_beats=4.0),
        piano=dict(midi=asset("piano"), part=0, loop=True),
    )
    cues = seq.set_master(0.2, T0)
    cc7 = {c.tag: c.data2 for c in cues if c.data1 == 7}
    assert cc7 == {"arabesque": 25, "piano": 20}
    assert all(c.when == T0 and c.status & 0xF0 == 0xB0 for c in cues)

    started_cues = seq.play_once("arabesque", T0)
    assert MusicCue(ROOM, 0xB4, 7, 25, T0, tag="arabesque") in started_cues
    file_cc7 = [c for c in seq.advance(T0) if c.status == 0xB4 and c.data1 == 7]
    assert [c.data2 for c in file_cc7] == [25]
    assert seq.enable("piano", T0)[0] == MusicCue(ROOM, 0xB0, 7, 20, T0,
                                                   tag="piano")


def test_play_once_on_next_beat_skipping_lead_in():
    seq = started(arabesque=dict(midi=asset("arabesque"), part=4, loop=False,
                                 skip_beats=4.0))
    at = T0 + 0.1
    cues = seq.play_once("arabesque", at)
    assert MusicCue(ROOM, 0xB4, 7, 127, seq.time_at(1), tag="arabesque") in cues
    cues += run(seq, at, seq.time_at(2))
    first = note_ons(cues)[0]
    assert first.when == pytest.approx(seq.time_at(1))
    assert first.data1 == 61


def test_play_once_exactly_on_a_beat_starts_there():
    seq = started(one=dict(midi=mf(*note(60, 0, 1)), part=0, loop=False))
    at = seq.time_at(3)
    seq.play_once("one", at)
    assert note_ons(seq.advance(at))[0].when == pytest.approx(at)


def test_one_shot_ends_and_can_replay():
    seq = started(one=dict(midi=mf(*note(60, 0, 2)), part=0, loop=False))
    seq.play_once("one", T0)
    first = run(seq, T0, seq.time_at(3))
    assert len(note_ons(first, 60)) == 1
    assert not seq.is_on("one")

    at = seq.time_at(4) + 0.1
    seq.play_once("one", at)
    assert seq.is_on("one")
    again = run(seq, at, seq.time_at(6))
    ons = note_ons(again, 60)
    assert len(ons) == 1 and ons[0].when == pytest.approx(seq.time_at(5))
