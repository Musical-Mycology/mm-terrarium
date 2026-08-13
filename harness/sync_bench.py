"""Measure how closely the audio call and the LED frame land on one cue.

Reports worst and p95 alongside the mean, for the same reason
harness/render_bench.py does: a path that averages 2 ms while missing by
200 ms once a second reads as healthy and is not.

EVERY FIGURE THIS PRODUCES IS A DEV-BOX FIGURE. The venue target is
bare-metal Linux on a Raspberry Pi 5 relaying every hop through the same
process doing all room synthesis while feeding a 44 Hz render loop. No
venue-box measurement exists; the box does not exist. Do not quote these
numbers as venue latency, and do not derive BootConfig.cue_horizon for a
venue from them.

summarise() takes no luxaeterna and no pyarco dependency, so it runs in the
core offline suite.
"""

from __future__ import annotations


def summarise(deltas: list[float]) -> dict:
    """Reduce signed second-deltas (audio time minus light time) to stats.

    Absolute values throughout: light landing 10 ms early is as wrong as 10
    ms late, and signed averaging would let the two cancel into a
    flattering zero.
    """
    if not deltas:
        return {"count": 0, "mean_ms": 0.0, "p95_ms": 0.0, "worst_ms": 0.0}
    magnitudes = sorted(abs(d) * 1000.0 for d in deltas)
    p95_index = min(len(magnitudes) - 1, int(len(magnitudes) * 0.95))
    return {
        "count": len(magnitudes),
        "mean_ms": sum(magnitudes) / len(magnitudes),
        "p95_ms": magnitudes[p95_index],
        "worst_ms": magnitudes[-1],
    }
