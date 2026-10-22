"""Tests for control.sequencer -- the look-ahead MIDI sequencer a Bit drives.

Small MidiFiles are built inline so each test states the exact notes it is
about; the loop, master and one-shot tests use the Deux Ara Drop assets where
the brief names them. The stuck-note test replays every returned cue through
a model of the Room queue (a MusicPurge drops still-queued cues of its tag
whose `when` is after the purge's `at`).
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


def test_enable_chases_held_note():
    seq = started(pad=dict(midi=mf(*note(60, 0, 8), end=8), part=2, loop=True))
    at = seq.time_at(2)
    run(seq, T0, at - TICK)
    cues = seq.enable("pad", at)
    assert cues[0] == MusicCue(ROOM, 0xB0 | 2, 7, 100, at, tag="pad")
    assert MusicCue(ROOM, 0x90 | 2, 60, 100, at, tag="pad") in cues
    assert seq.is_on("pad")


def test_enable_does_not_double_emit_boundary_note():
    seq = started(a=dict(midi=mf(*note(60, 4, 5), end=8), part=0, loop=True))
    at = seq.time_at(4)
    cues = seq.enable("a", at)
    cues += run(seq, at, seq.time_at(6))
    ons = note_ons(cues, 60)
    assert len(ons) == 1
    assert ons[0].when == pytest.approx(at)


def test_disable_purges_and_releases():
    synth = mf((0, 0xB0, 64, 127), *note(60, 0, 8), end=8)
    seq = started(synth=dict(midi=synth, part=1, loop=True))
    seq.enable("synth", T0)
    run(seq, T0, seq.time_at(1))
    at = seq.time_at(2)
    cues = seq.disable("synth", at)
    assert cues == [
        MusicPurge(ROOM, "synth"),
        MusicCue(ROOM, 0x80 | 1, 60, 0, at, tag="synth"),
        MusicCue(ROOM, 0xB0 | 1, 64, 0, at, tag="synth"),
    ]
    assert not seq.is_on("synth")
    assert run(seq, at, at + 5) == []


def test_disable_ignores_events_after_at():
    """A note-on queued past `at` is purged, so it needs no release; a pedal
    lifted before `at` needs no CC 64 = 0."""
    synth = mf((0, 0xB0, 64, 127), (0.5, 0xB0, 64, 0), *note(60, 1, 2),
               *note(62, 3, 4), end=4)
    seq = started(synth=dict(midi=synth, part=1, loop=True))
    seq.enable("synth", T0)
    run(seq, T0, seq.time_at(3))
    at = seq.time_at(2.5)
    assert seq.disable("synth", at) == [MusicPurge(ROOM, "synth")]


def test_no_stuck_notes_over_random_toggles():
    rng = random.Random(20261022)
    names = {"piano": 0, "drums": 9, "synth": 1}
    seq = started(**{n: dict(midi=asset(n), part=p, loop=True)
                     for n, p in names.items()})
    queue, delivered = [], []

    def dispatch(cues, at):
        for c in cues:
            if isinstance(c, MusicPurge):
                queue[:] = [q for q in queue
                            if not (q.tag == c.tag and q.when > at)]
            else:
                queue.append(c)

    def deliver(now):
        due = sorted((q for q in queue if q.when <= now), key=lambda q: q.when)
        queue[:] = [q for q in queue if q.when > now]
        delivered.extend(due)

    toggles = 0
    t = T0
    while t < T0 + 60:
        if rng.random() < 0.03:
            name = rng.choice(list(names))
            if seq.is_on(name):
                dispatch(seq.disable(name, t), t)
            else:
                dispatch(seq.enable(name, t), t)
            toggles += 1
        dispatch(seq.advance(t), t)
        deliver(t)
        t += TICK
    for name in names:
        if seq.is_on(name):
            dispatch(seq.disable(name, t), t)
    deliver(float("inf"))

    assert toggles > 40
    ons = Counter((c.status & 0x0F, c.data1) for c in delivered
                  if c.status & 0xF0 == 0x90)
    offs = Counter((c.status & 0x0F, c.data1) for c in delivered
                   if c.status & 0xF0 == 0x80)
    assert sum(ons.values()) > 100
    assert ons == offs
    pedal = [c for c in delivered if c.status & 0xF0 == 0xB0 and c.data1 == 64]
    assert not pedal or pedal[-1].data2 < 64


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
