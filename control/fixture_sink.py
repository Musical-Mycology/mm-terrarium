"""FixtureSink: where one fixture's rendered frames go.

A Room is loaded with all its instruments (one session per fixture) from
Room load; devices are OUTPUTS that attach to a fixture. Each render hands
the fixture's changed frame to every sink it currently has: the Console's
display strip always, the bound devicelink device when there is one, and
later a physical controller. Pure stdlib (control/ discipline): the
devicelink protocol's leds_event is injected, never imported. See
docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md
section 5.3.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class FixtureSink(Protocol):
    def send_frame(self, frame: bytes, when: float) -> None: ...


class ConsoleFrameSink:
    """Display-only: the Console strip for `fixture_name`. Best-effort, per
    boundary rule 2; a failing console never costs the fixture a frame."""

    def __init__(self, fixture_name: str,
                 on_room_frame: Callable[[str, bytes], None]) -> None:
        self.fixture_name = fixture_name
        self._on_room_frame = on_room_frame

    def send_frame(self, frame: bytes, when: float) -> None:
        try:
            self._on_room_frame(self.fixture_name, frame)
        except Exception:
            logger.exception("console frame sink failed for %s; dropping frame",
                             self.fixture_name)


class DeviceLinkSink:
    """The bound devicelink device: a dumb pixel sink that displays `frame`
    at `when` (O2 time)."""

    def __init__(self, dev: str, send: Callable[[str, dict], None],
                 leds_event: Callable[..., dict]) -> None:
        self.dev = dev
        self._send = send
        self._leds_event = leds_event

    def send_frame(self, frame: bytes, when: float) -> None:
        try:
            self._send(self.dev, self._leds_event(self.dev, frame, when=when))
        except Exception:
            logger.exception("leds send failed for %s", self.dev)
