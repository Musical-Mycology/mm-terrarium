"""RoomBridge: fans a Room's MIDI stream out to whatever light/audio sinks
are currently bound -- the Room-scoped sibling of harness/device_bridge.py
and control/audio.py's AudioBridge. Backend-agnostic by construction: it
never imports luxaeterna or pyarco, so the offline suite stays green. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 6.
"""

from __future__ import annotations

from typing import Protocol


class RoomLightSink(Protocol):
    def feed_midi(self, status: int, d1: int, d2: int) -> None: ...
    def clear(self) -> None: ...


class RoomAudioSink(Protocol):
    def feed_midi(self, status: int, d1: int, d2: int) -> None: ...
    def shutdown(self) -> None: ...


class FakeRoomLightSink:
    """In-process test double, sibling of control/audio.py's FakeVoice."""

    def __init__(self) -> None:
        self.fed: list[tuple[int, int, int]] = []
        self.cleared = False

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self.fed.append((status, d1, d2))

    def clear(self) -> None:
        self.cleared = True


class FakeRoomAudioSink:
    def __init__(self) -> None:
        self.fed: list[tuple[int, int, int]] = []
        self.shut = False

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self.fed.append((status, d1, d2))

    def shutdown(self) -> None:
        self.shut = True


class RoomBridge:
    """Owns whichever light/audio sinks are currently bound to the Room and
    forwards the same MIDI bytes to both, mirroring harness/led_smoke.py's
    feed_shared() -- light and sound reading the same stream is the point."""

    def __init__(self) -> None:
        self.dev: str | None = None
        self._light: RoomLightSink | None = None
        self._audio: RoomAudioSink | None = None

    def bind(self, dev: str, light: RoomLightSink | None = None,
             audio: RoomAudioSink | None = None) -> None:
        self.dev = dev
        self._light = light
        self._audio = audio

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        if self._light is not None:
            self._light.feed_midi(status, d1, d2)
        if self._audio is not None:
            self._audio.feed_midi(status, d1, d2)

    def release(self) -> None:
        if self._light is not None:
            self._light.clear()
        self.dev = None
        self._light = None
        self._audio = None

    def shutdown(self) -> None:
        if self._audio is not None:
            self._audio.shutdown()
        self.release()
