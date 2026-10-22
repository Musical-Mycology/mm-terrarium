"""Tests for control.midifile -- the stdlib-only Standard MIDI File reader.

Asset tests pin the four files shipped with the Deux Ara Drop Bit; the
inline-bytes tests exercise the parser edge cases (running status, velocity-0
note-off, multi-track merge, malformed input) without depending on any asset.
"""

import struct
from pathlib import Path

import pytest

from control.midifile import MidiEvent, parse_midi, read_midi_file

ASSETS = Path(__file__).resolve().parent.parent / "bits" / "deux_ara_drop" / "assets"


def note_ons(mf):
    return [e for e in mf.events if e.status & 0xF0 == 0x90]


def smf(fmt, tpq, *tracks):
    """Build a tiny SMF from raw track-body byte strings."""
    out = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), tpq)
    for body in tracks:
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    return out


EOT = b"\xff\x2f\x00"


def test_piano_loop():
    mf = read_midi_file(ASSETS / "piano.mid")
    assert (mf.ticks_per_beat, mf.tempo_bpm) == (96, None)
    assert len(note_ons(mf)) == 55
    assert mf.end_beat == 16.0 and mf.loop_beats() == 16.0
    assert mf.name == "3-Grand Piano Single Sample 3"


def test_synth_loop_rounds_up_to_the_bar():
    mf = read_midi_file(ASSETS / "synth.mid")
    assert len(note_ons(mf)) == 19
    assert mf.end_beat == 3015 / 96 and mf.loop_beats() == 32.0


def test_drums_are_all_kick():
    mf = read_midi_file(ASSETS / "drums.mid")
    assert len(note_ons(mf)) == 32 and {e.data1 for e in note_ons(mf)} == {36}
    assert mf.loop_beats() == 32.0


def test_arabesque():
    mf = read_midi_file(ASSETS / "arabesque.mid")
    assert (mf.ticks_per_beat, mf.tempo_bpm) == (384, 120.0)
    assert len(note_ons(mf)) == 1458
    assert mf.first_note_beat() == 4.0 and mf.end_beat == 484.5
    assert any(e.status & 0xF0 == 0xB0 and e.data1 == 64 for e in mf.events)
    assert MidiEvent(4.0, 0xC0, 0, 0) in mf.events


def test_running_status_and_velocity_zero_note_off():
    track = (
        b"\x00\x90\x3c\x64"  # note-on 60 vel 100 @0
        b"\x60\x3c\x00"  # running status: note-on 60 vel 0 @96 -> note-off
        b"\x00" + EOT  # EOT @96
    )
    mf = parse_midi(smf(0, 96, track))
    assert mf.events == (MidiEvent(0.0, 0x90, 60, 100), MidiEvent(1.0, 0x80, 60, 0))
    assert mf.end_beat == 1.0
    assert mf.loop_beats() == 4.0  # minimum one bar


def test_events_sorted_across_tracks():
    t1 = b"\x00\x90\x3c\x64" + b"\x81\x40\x80\x3c\x00" + b"\x00" + EOT  # 0, 192
    t2 = b"\x60\x91\x40\x50" + b"\x60\x81\x40\x00" + b"\x00" + EOT  # 96, 192
    mf = parse_midi(smf(1, 96, t1, t2))
    assert [(e.beat, e.status, e.data1) for e in mf.events] == [
        (0.0, 0x90, 60),
        (1.0, 0x91, 64),
        (2.0, 0x80, 60),  # same beat: track order wins (stable)
        (2.0, 0x81, 64),
    ]


def test_one_byte_messages_have_zero_data2():
    track = b"\x00\xc0\x05" + b"\x00\xd1\x10" + b"\x00" + EOT
    mf = parse_midi(smf(0, 96, track))
    assert mf.events == (MidiEvent(0.0, 0xC0, 5, 0), MidiEvent(0.0, 0xD1, 16, 0))


def test_meta_and_sysex_skipped_but_tempo_and_name_read():
    track = (
        b"\x00\xff\x03\x04Name"
        b"\x00\xff\x03\x05Other"  # only the first track name counts
        b"\x00\xff\x51\x03\x07\xa1\x20"  # 500000 us/beat = 120 bpm
        b"\x00\xff\x51\x03\x03\xd0\x90"  # later tempo ignored
        b"\x00\xf0\x03\x01\x02\xf7"  # sysex
        b"\x00\x90\x3c\x40"
        b"\x00" + EOT
    )
    mf = parse_midi(smf(0, 96, track))
    assert (mf.name, mf.tempo_bpm) == ("Name", 120.0)
    assert mf.events == (MidiEvent(0.0, 0x90, 60, 64),)


def test_first_note_beat_none_without_notes():
    mf = parse_midi(smf(0, 96, b"\x00\xc0\x01" + b"\x00" + EOT))
    assert mf.first_note_beat() is None


def test_loop_beats_rounds_up_to_whole_bars():
    track = b"\x83\x60" + EOT  # VLQ 0x83 0x60 = 480 ticks = 5 beats at 96 tpq
    mf = parse_midi(smf(0, 96, track))
    assert mf.end_beat == 5.0
    assert mf.loop_beats() == 8.0
    assert mf.loop_beats(beats_per_bar=3) == 6.0


def test_malformed_raises():
    for bad in (
        b"",
        b"junk",
        b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x09",
        smf(0, 96, b"\x00\x90\x3c"),  # truncated channel message
        smf(0, 96, b"\x00\xff\x03\x09ab"),  # meta length past the chunk
        smf(0, 96, b"\x00\x3c\x40"),  # data byte with no running status
        smf(0, 0, EOT),  # zero ticks-per-beat
        smf(0, 96, b"\x00\xff\x51\x03\x00\x00\x00" + EOT),  # zero tempo
    ):
        with pytest.raises(ValueError):
            parse_midi(bad)
