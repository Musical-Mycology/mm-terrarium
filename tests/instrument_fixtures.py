"""Shared test-only Instrument fixtures, so tests don't hand-roll N copies
of the same generic instrument just to satisfy RoomFixture's required field.
"""
from __future__ import annotations

from control.instrument import Instrument

GENERIC_SURFACE = Instrument(
    name="generic_surface",
    capabilities=frozenset({"light.surface", "audio.flsyn"}),
    accepted_cues=("midi", "play", "solid", "mute"),
)
