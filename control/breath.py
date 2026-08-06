"""The breath: a slow control signal Control generates and both media consume.

luxaeterna's aurora used to breathe on its own private clock. A role that
declares a `level` param opts out of that clock, so the envelope moves here and
travels as cc:11 on the shared MIDI stream. A light renderer binding cc:11 to
`level` and a sound engine binding it to expression then swell together, because
they are reading the same number in the same tick rather than two clocks that
happen to agree.

Consequence worth stating plainly: every renderer of a level-declaring role has
to be fed this, or it renders a static surface. harness/led_smoke.py and
devicelink/agent.py both tick it for that reason.

Pure and dependency-free: no luxaeterna, no pyarco, no clock of its own.
"""

from __future__ import annotations

BREATH_CC = 11        # General MIDI Expression: a direct attenuation in FluidSynth

# luxaeterna's _AURORA_BREATHE, point for point, so the light is unchanged.
BREATHE_POINTS = [(0.0, 0.55), (3.0, 1.0), (6.0, 0.55)]
BREATHE_PERIOD = 6.0


def breath_cc(t: float) -> int:
    """Sample the breath envelope at time t (looping), scaled to 7-bit MIDI."""
    phase = t % BREATHE_PERIOD
    for (x0, y0), (x1, y1) in zip(BREATHE_POINTS, BREATHE_POINTS[1:]):
        if phase <= x1:
            frac = 0.0 if x1 == x0 else (phase - x0) / (x1 - x0)
            return round((y0 + frac * (y1 - y0)) * 127)
    return round(BREATHE_POINTS[-1][1] * 127)
