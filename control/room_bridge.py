"""RoomBridge: fans a Room's MIDI stream out to whatever light sink is
currently bound -- the Room-scoped sibling of harness/device_bridge.py.
Backend-agnostic by construction: it never imports luxaeterna or pyarco, so
the offline suite stays green. See docs/superpowers/specs/
2026-08-10-room-concept-and-load-sequence-design.md section 6.

The Room's audio channel moved off this bridge (per-fixture instruments
slice): each audio-capable bound fixture now gets its own AudioBridge voice,
granted/fed directly by devicelink/agent.py against a real fixture dev, not
through this class. This bridge is light-only.
"""

from __future__ import annotations

from typing import Protocol


class RoomLightSink(Protocol):
    def feed_midi(self, status: int, d1: int, d2: int) -> None: ...
    def clear(self) -> None: ...


class FakeRoomLightSink:
    """In-process test double, sibling of control/audio.py's FakeVoice."""

    def __init__(self) -> None:
        self.fed: list[tuple[int, int, int]] = []
        self.cleared = False

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self.fed.append((status, d1, d2))

    def clear(self) -> None:
        self.cleared = True


class RoomBridge:
    """Owns whichever light sink is currently bound to the Room.

    Backend-agnostic by construction (mirrors harness/led_smoke.py's
    feed_shared()), and light-only: see the module docstring for where the
    audio half went.
    """

    def __init__(self) -> None:
        self.dev: str | None = None
        # Last value seen per controller number, for the Console's live
        # read-out. A plain dict of ints: this class stays backend-agnostic
        # by construction and imports nothing.
        self.controllers: dict[int, int] = {}
        self._light: RoomLightSink | None = None

    def bind(self, dev: str, light: RoomLightSink | None = None) -> None:
        self.dev = dev
        self._light = light

    def feed_light(self, status: int, d1: int, d2: int) -> None:
        """Feed the light sink.

        Fed as early as possible, since the frame it renders still has to
        cross the wire to reach the device by `at`. See docs/superpowers/
        specs/2026-08-14-load-bearing-timed-cues-design.md section 2.
        """
        if status & 0xF0 == 0xB0:
            self.controllers[d1] = d2
        if self._light is not None:
            self._light.feed_midi(status, d1, d2)

    def release(self) -> None:
        if self._light is not None:
            self._light.clear()
        self.controllers.clear()
        self.dev = None
        self._light = None

    def shutdown(self) -> None:
        """Light-release only now (the audio half moved off this bridge --
        see the module docstring). Kept as an alias for release() so
        existing teardown-stack callers (control/terrarium.py) need no
        changes."""
        self.release()
