"""Shared test-only Instrument fixtures, so tests don't hand-roll N copies
of the same generic instrument just to satisfy RoomFixture's required field.
"""
from __future__ import annotations

from control.instrument import Instrument
from control.triggers import StreamTrigger

GENERIC_SURFACE = Instrument(
    name="generic_surface",
    capabilities=frozenset({"light.surface", "audio.flsyn"}),
    accepted_cues=("midi", "play", "solid", "mute"),
)

# Task 10 coverage fixture: no shipped instrument declares a StreamTrigger
# (a smoothed reference would hide responsiveness regressions, spec section
# 6), so this test-only widget exercises data()'s smoothing path.
SMOOTHING_WIDGET = Instrument(
    name="smoothing_widget",
    capabilities=frozenset({"light.pixels", "gesture.tilt"}),
    accepted_cues=("midi", "play", "solid", "mute"),
    stream_triggers=(
        StreamTrigger(name="tilt_smooth", description="smooths tilt arg 0",
                      verb="tilt", arg=0, transform="smooth",
                      params={"alpha": 0.5}),
    ),
)
