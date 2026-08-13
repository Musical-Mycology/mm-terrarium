"""Control-side audio: turning a role's declared MIDI intent into synth calls.

Pure by construction. This module MUST NOT import pyarco, at module level or
anywhere else, so the offline test suite runs with no Arco server and no
pyarco checkout. The concrete pyarco-backed pool lives in harness/arco_synth.py
and is injected.

Boundary rule 1 (docs/MM_TERRARIUM.md): only Control builds ugen graphs and
owns the ugen id space, which includes freeing it at unload. Audio declarations
never ship to the device.

The channel question is OPEN and out with Roger Dannenberg: his written notes
argue against a channel parameter (allocate up to 16 Synths sharing one Flsyn),
his shipped MidiSender takes chan on every method. Those disagree about the API,
not the implementation. So the channel here is real but INTERNAL to the backend:
nothing in this file names one. The type is DeviceVoice, not Synth, so this is
not read as that abstraction having landed. See
docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md section 9.1.
"""

from __future__ import annotations

import time
from typing import Protocol

from control.roles import Role

_CC_PREFIX = "cc:"

# Welcome-cue instrument names, provisional v0. Names are opaque to Control
# exactly the way light_manifest instrument names are opaque luxaeterna
# registry names; this table is the audio-side equivalent of that registry.
# (program, key, velocity). Numbers picked by listening on the venue soundfont.
# These are General MIDI program numbers, and they only mean what they say if
# the loaded soundfont actually follows the GM map (see harness/arco_synth.py's
# DEFAULT_SOUNDFONT comment; a non-GM soundfont cost real debugging time here).
WELCOME_INSTRUMENTS: dict[str, tuple[int, int, int]] = {
    "chime": (9, 84, 88),        # 9 = Glockenspiel (General MIDI)
}

_DEFAULT_WELCOME_DURATION = 1.5


class DeviceVoice(Protocol):
    """One device's slice of the room synth. No channel in this API."""

    def note_on(self, key: int, vel: int) -> None: ...
    def note_off(self, key: int) -> None: ...
    def control_change(self, num: int, val: int) -> None: ...
    def program_change(self, prog: int) -> None: ...
    def all_off(self) -> None: ...


class SynthPool(Protocol):
    def acquire(self) -> DeviceVoice: ...
    def release(self, voice: DeviceVoice) -> None: ...
    def poll(self) -> None: ...
    def shutdown(self) -> None: ...
    def schedule_at(self, when: float, fn) -> None: ...


class FakeVoice:
    """In-process test double, sibling of uplink.transport.FakeTransport."""

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def note_on(self, key: int, vel: int) -> None:
        self.sent.append(("note_on", key, vel))

    def note_off(self, key: int) -> None:
        self.sent.append(("note_off", key))

    def control_change(self, num: int, val: int) -> None:
        self.sent.append(("cc", num, val))

    def program_change(self, prog: int) -> None:
        self.sent.append(("program", prog))

    def all_off(self) -> None:
        self.sent.append(("all_off",))


class FakePool:
    def __init__(self) -> None:
        self.acquired: list[FakeVoice] = []
        self.released: list[FakeVoice] = []
        self.polls = 0
        self.shut = False
        self.scheduled: list[tuple[float, object]] = []

    def acquire(self) -> FakeVoice:
        voice = FakeVoice()
        self.acquired.append(voice)
        return voice

    def release(self, voice) -> None:
        self.released.append(voice)

    def poll(self) -> None:
        self.polls += 1

    def shutdown(self) -> None:
        self.shut = True

    def schedule_at(self, when: float, fn) -> None:
        """Record rather than run. A test fires the callable itself, which
        is what makes 'scheduled for T' assertable with no scheduler."""
        self.scheduled.append((when, fn))


def _cc_number(ref: str) -> int:
    return int(ref[len(_CC_PREFIX):])


class _DeviceAudio:
    """Per-device state: the voice, its lane map, and its drone."""

    __slots__ = ("voice", "lanes", "drone", "drone_key")

    def __init__(self, voice, lanes: dict[int, int], drone: dict | None) -> None:
        self.voice = voice
        self.lanes = lanes
        self.drone = drone
        self.drone_key: int | None = None       # set while the drone sounds


class AudioBridge:
    """Fans a device's MIDI stream out to its synth voice, per the role's
    declared lanes. The light-side sibling is harness/device_bridge.py; both
    consume the SAME stream, which is the point (spec section 3)."""

    def __init__(self, pool, clock=time.monotonic, welcome_instruments=None) -> None:
        self._pool = pool
        self._clock = clock
        self._welcome = (WELCOME_INSTRUMENTS if welcome_instruments is None
                         else welcome_instruments)
        self._devices: dict[str, _DeviceAudio] = {}
        # (due_time, voice, key) for welcome cues still sounding. A cue plays on
        # its own transient voice so it never disturbs the sustained drone, and
        # that voice is released the moment its declared duration expires.
        self._pending_offs: list[tuple[float, object, int]] = []

    def on_grant(self, dev: str, role: Role) -> None:
        """Role adopted: acquire a voice, wire its lanes, sound the welcome.
        A role declaring no instruments is silent and must not consume a voice,
        but it may still have a welcome cue."""
        instruments = role.ugen_manifest.get("instruments", [])
        if instruments:
            decl = instruments[0]        # v0: one instrument per role
            voice = self._pool.acquire()
            program = decl.get("program")
            if program is not None:
                voice.program_change(int(program))
            lanes = {_cc_number(lane["source"]): _cc_number(lane["dest"])
                     for lane in decl.get("lanes", [])}
            self._devices[dev] = _DeviceAudio(voice, lanes, decl.get("drone"))
        self._play_welcome(role)

    def _play_welcome(self, role: Role) -> None:
        """The audio half of the adoption ceremony. Declared alongside the light
        half in Role.welcome since PR #5; this is its first consumer."""
        decl = (role.welcome or {}).get("audio")
        if not decl:
            return
        name = decl["instrument"]
        if name not in self._welcome:
            raise KeyError(
                f"role {role.name!r} welcome audio: unknown instrument {name!r} "
                f"(known: {sorted(self._welcome)})")
        program, key, vel = self._welcome[name]
        duration = float(decl.get("duration", _DEFAULT_WELCOME_DURATION))
        voice = self._pool.acquire()
        voice.program_change(program)
        voice.note_on(key, vel)
        self._pending_offs.append((self._clock() + duration, voice, key))

    def tick(self, now: float | None = None) -> None:
        """Called once per driver-loop iteration: expire welcome cues, then let
        the backend pump its transport. The single place the audio side ticks."""
        if now is None:
            now = self._clock()
        still_sounding = []
        for due, voice, key in self._pending_offs:
            if now >= due:
                voice.note_off(key)
                voice.all_off()
                self._pool.release(voice)
            else:
                still_sounding.append((due, voice, key))
        self._pending_offs = still_sounding
        self._pool.poll()

    def feed_midi(self, dev: str, status: int, d1: int, d2: int,
                  when: float | None = None) -> None:
        """Apply one MIDI event to `dev`'s voice through its declared lanes.

        `when` is an absolute time on the O2 clock. None means apply now,
        which is the pre-timing behavior and stays the default. A time is
        handed to the pool's scheduler rather than slept on: this module
        must never block the tick, and must never import pyarco to find a
        clock (boundary: see the module docstring).
        """
        def apply() -> None:
            self._apply_midi(dev, status, d1, d2)

        if when is None:
            apply()
        else:
            self._pool.schedule_at(when, apply)

    def _apply_midi(self, dev: str, status: int, d1: int, d2: int) -> None:
        """The one path from a MIDI byte to a synth call. An undeclared cc is
        dropped, which is what makes the lane a remap seam and not decoration."""
        entry = self._devices.get(dev)
        if entry is None:
            return
        kind = status & 0xF0
        if kind == 0x90 and d2 > 0:
            entry.voice.note_on(d1, d2)
        elif kind == 0x80 or (kind == 0x90 and d2 == 0):
            entry.voice.note_off(d1)
        elif kind == 0xB0:
            dest = entry.lanes.get(d1)
            if dest is not None:
                entry.voice.control_change(dest, d2)
        elif kind == 0xC0:
            entry.voice.program_change(d1)

    def start_drone(self, dev: str) -> None:
        """FluidSynth is silent without a note, so the role's declared drone is
        the substrate its lanes modulate. Light ignores it: the running light
        declaration has no note lane."""
        entry = self._devices.get(dev)
        if entry is None or entry.drone is None or entry.drone_key is not None:
            return
        key, vel = int(entry.drone["key"]), int(entry.drone["velocity"])
        self.feed_midi(dev, 0x90, key, vel)
        entry.drone_key = key

    def stop_drone(self, dev: str) -> None:
        entry = self._devices.get(dev)
        if entry is None or entry.drone_key is None:
            return
        self.feed_midi(dev, 0x80, entry.drone_key, 0)
        entry.drone_key = None

    def on_release(self, dev: str) -> None:
        entry = self._devices.pop(dev, None)
        if entry is None:
            return
        if entry.drone_key is not None:
            entry.voice.note_off(entry.drone_key)
        entry.voice.all_off()
        self._pool.release(entry.voice)

    def shutdown(self) -> None:
        """Free every voice, then the pool. Boundary rule 1: owning the ugen id
        space means freeing it at Bit unload."""
        for _due, voice, key in self._pending_offs:
            voice.note_off(key)
            voice.all_off()
            self._pool.release(voice)
        self._pending_offs = []
        for dev in list(self._devices):
            self.on_release(dev)
        self._pool.shutdown()
