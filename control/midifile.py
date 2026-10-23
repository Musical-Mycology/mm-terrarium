"""Minimal Standard MIDI File reader, stdlib only.

Bits and control/ may not depend on third-party packages, so this does the
small slice of SMF parsing a sequencer needs and nothing more: channel-voice
events on a beat timeline, the first tempo and track name, and where the
file ends. Meta and SysEx events are skipped (their lengths are honoured so
the stream stays in sync). Timing is returned in beats rather than ticks so
the consumer never needs the file's resolution; SMPTE-division files are
rejected because they have no beat grid.

Anything malformed -- bad chunk ids, reads past a buffer, stray data bytes --
raises ValueError, so a caller can treat "can't be played" as one case.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MidiEvent:
    """One channel-voice message at `beat`. A note-on with velocity 0 is
    stored as the equivalent note-off (0x80 | channel, data2 0); 1-byte
    messages (program change, channel pressure) carry data2 = 0."""

    beat: float
    status: int
    data1: int
    data2: int


@dataclass(frozen=True)
class MidiFile:
    ticks_per_beat: int
    tempo_bpm: float | None
    events: tuple[MidiEvent, ...]
    end_beat: float
    name: str | None

    def loop_beats(self, beats_per_bar: int = 4) -> float:
        """end_beat rounded up to a whole bar (minimum one bar), so a looped
        clip repeats on a bar line."""
        bars = max(1, math.ceil(self.end_beat / beats_per_bar))
        return float(bars * beats_per_bar)

    def first_note_beat(self) -> float | None:
        """Beat of the first note-on, or None if the file has none."""
        for e in self.events:
            if e.status & 0xF0 == 0x90:
                return e.beat
        return None


def _vlq(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= end:
            raise ValueError("truncated variable-length quantity")
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos
    raise ValueError("variable-length quantity longer than 4 bytes")


def _parse_track(buf: bytes, pos: int, end: int):
    """Parse one MTrk body. Returns (events as (tick, status, d1, d2),
    end_tick, tempo_us_per_beat | None, name | None)."""
    events: list[tuple[int, int, int, int]] = []
    tick = 0
    running = 0
    tempo = None
    name = None
    end_tick = None
    while pos < end:
        delta, pos = _vlq(buf, pos, end)
        tick += delta
        if pos >= end:
            raise ValueError("track ends after a delta time")
        status = buf[pos]
        if status < 0x80:
            if not running:
                raise ValueError("data byte with no running status")
            status = running
        else:
            pos += 1
        if status == 0xFF:
            running = 0
            if pos >= end:
                raise ValueError("truncated meta event")
            mtype = buf[pos]
            length, pos = _vlq(buf, pos + 1, end)
            if pos + length > end:
                raise ValueError("meta event runs past the track")
            body = buf[pos : pos + length]
            pos += length
            if mtype == 0x2F:
                end_tick = tick
            elif mtype == 0x51 and tempo is None and length == 3:
                tempo = int.from_bytes(body, "big")
                if tempo == 0:
                    raise ValueError("zero tempo")
            elif mtype == 0x03 and name is None:
                name = body.decode("latin-1")
        elif status in (0xF0, 0xF7):
            running = 0
            length, pos = _vlq(buf, pos, end)
            if pos + length > end:
                raise ValueError("SysEx runs past the track")
            pos += length
        elif status >= 0xF0:
            raise ValueError(f"unsupported status byte 0x{status:02X}")
        else:
            running = status
            need = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
            if pos + need > end:
                raise ValueError("truncated channel message")
            d1 = buf[pos]
            d2 = buf[pos + 1] if need == 2 else 0
            pos += need
            if d1 > 0x7F or d2 > 0x7F:
                raise ValueError("data byte out of range")
            if status & 0xF0 == 0x90 and d2 == 0:
                status = 0x80 | (status & 0x0F)
            events.append((tick, status, d1, d2))
    if end_tick is None:
        end_tick = tick
    return events, end_tick, tempo, name


def parse_midi(data: bytes) -> MidiFile:
    """Parse SMF bytes. Raises ValueError on anything malformed."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hdr_len, _fmt, ntrks, division = struct.unpack(">IHHH", data[4:14])
    if hdr_len < 6:
        raise ValueError("bad MThd length")
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported")
    if division == 0:
        raise ValueError("zero ticks per beat")
    pos = 8 + hdr_len
    tpb = division

    all_events: list[tuple[int, int, int, int]] = []
    end_tick = 0
    tempo = None
    name = None
    for _ in range(ntrks):
        if pos + 8 > len(data):
            raise ValueError("missing track chunk")
        cid = data[pos : pos + 4]
        (clen,) = struct.unpack(">I", data[pos + 4 : pos + 8])
        body_start = pos + 8
        body_end = body_start + clen
        if body_end > len(data):
            raise ValueError("chunk runs past end of file")
        pos = body_end
        if cid != b"MTrk":
            raise ValueError(f"unexpected chunk id {cid!r}")
        events, t_end, t_tempo, t_name = _parse_track(data, body_start, body_end)
        all_events.extend(events)
        end_tick = max(end_tick, t_end)
        if tempo is None:
            tempo = t_tempo
        if name is None:
            name = t_name

    all_events.sort(key=lambda e: e[0])  # stable: track order, then file order
    return MidiFile(
        ticks_per_beat=tpb,
        tempo_bpm=None if tempo is None else 60_000_000 / tempo,
        events=tuple(MidiEvent(t / tpb, s, d1, d2) for t, s, d1, d2 in all_events),
        end_beat=end_tick / tpb,
        name=name,
    )


def read_midi_file(path) -> MidiFile:
    return parse_midi(Path(path).read_bytes())
