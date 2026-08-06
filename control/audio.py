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

    def __init__(self, pool, clock=time.monotonic) -> None:
        self._pool = pool
        self._clock = clock
        self._devices: dict[str, _DeviceAudio] = {}

    def on_grant(self, dev: str, role: Role) -> None:
        """Role adopted: acquire a voice and wire its lanes. A role declaring
        no instruments is silent and must not consume a voice."""
        instruments = role.ugen_manifest.get("instruments", [])
        if not instruments:
            return
        decl = instruments[0]        # v0: one instrument per role
        voice = self._pool.acquire()
        program = decl.get("program")
        if program is not None:
            voice.program_change(int(program))
        lanes = {_cc_number(lane["source"]): _cc_number(lane["dest"])
                 for lane in decl.get("lanes", [])}
        self._devices[dev] = _DeviceAudio(voice, lanes, decl.get("drone"))

    def feed_midi(self, dev: str, status: int, d1: int, d2: int) -> None:
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
        for dev in list(self._devices):
            self.on_release(dev)
        self._pool.shutdown()
