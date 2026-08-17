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
    """Owns whichever light/audio sinks are currently bound to the Room.

    Light and sound read the same stream, which is the point (mirroring
    harness/led_smoke.py's feed_shared()), but they are fed through separate
    calls because they are released at different times against one shared
    cue time -- see feed_light.
    """

    def __init__(self) -> None:
        self.dev: str | None = None
        # Last value seen per controller number, for the Console's live
        # read-out. Recorded in feed_light rather than feed_audio because
        # light is fed on every cue while audio is released on its own
        # schedule (see feed_light/feed_audio below), so the light side is the
        # one that sees every value. A plain dict of ints: this class stays
        # backend-agnostic by construction and imports nothing.
        self.controllers: dict[int, int] = {}
        self._light: RoomLightSink | None = None
        self._audio: RoomAudioSink | None = None

    def bind(self, dev: str, light: RoomLightSink | None = None,
             audio: RoomAudioSink | None = None) -> None:
        self.dev = dev
        self._light = light
        self._audio = audio

    def feed_light(self, status: int, d1: int, d2: int) -> None:
        """Feed the light sink only.

        Separate from feed_audio because the two halves of a Room cue are
        released at different times against ONE shared `at`: light is fed as
        early as possible, since the frame it renders still has to cross the
        wire to reach the device by `at`, while audio waits until `at`
        because it reaches Arco from Control with no wire in between. See
        docs/superpowers/specs/
        2026-08-14-load-bearing-timed-cues-design.md section 2.
        """
        if status & 0xF0 == 0xB0:
            self.controllers[d1] = d2
        if self._light is not None:
            self._light.feed_midi(status, d1, d2)

    def feed_audio(self, status: int, d1: int, d2: int) -> None:
        """Feed the audio sink only. See feed_light for why they are split."""
        if self._audio is not None:
            self._audio.feed_midi(status, d1, d2)

    def release(self) -> None:
        if self._light is not None:
            self._light.clear()
        self.controllers.clear()
        self.dev = None
        self._light = None
        self._audio = None

    def shutdown(self) -> None:
        if self._audio is not None:
            self._audio.shutdown()
        self.release()
