"""Frame-timing statistics for the Terrarium's multi-universe render loop.

Mean FPS alone hides stalls: a loop that averages 44 Hz while pausing 200 ms
once a second reads as healthy and is not. This reports the worst frame and the
95th percentile alongside the mean, so a stall cannot hide behind an average.

MM_TERRARIUM.md is explicit that any timing figure must be measured on the venue
box, because it relays every hop through the same process doing all room
synthesis while feeding a 44 Hz render loop. Numbers from a laptop do not carry
over, and the M1a-era "under 50 ms" figure was measured with Control not in the
path.

Usage (ON THE VENUE BOX, never a laptop):
    python -m harness.render_bench --host 10.44.0.50 --pixels 864 --seconds 120

``summarise`` and ``measure`` do not import luxaeterna, so they stay testable in
the core offline suite; only ``main()`` reaches for the renderer.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class FrameStats:
    frames: int
    seconds: float
    mean_fps: float
    min_fps: float
    p95_frame_ms: float
    worst_frame_ms: float


def summarise(intervals: Sequence[float]) -> FrameStats:
    """Reduce a sequence of per-frame intervals (seconds) to statistics."""
    if not intervals:
        raise ValueError("need at least one frame interval")
    total = sum(intervals)
    ordered = sorted(intervals)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    worst = ordered[-1]
    return FrameStats(
        frames=len(intervals),
        seconds=total,
        mean_fps=len(intervals) / total,
        min_fps=1.0 / worst,
        p95_frame_ms=ordered[p95_index] * 1000.0,
        worst_frame_ms=worst * 1000.0,
    )


def measure(loop, seconds: float) -> FrameStats:
    """Drive *loop* synchronously for *seconds*, timing every tick.

    Synchronous on purpose: the loop's own background thread reports a smoothed
    once-per-second FPS, which is exactly the averaging that hides a stall.
    """
    intervals: list[float] = []
    loop.backend.open()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            tick = time.monotonic()
            loop._loop_once()
            elapsed = time.monotonic() - tick
            sleep = loop.frame_interval - elapsed
            if sleep > 0:
                time.sleep(sleep)
            intervals.append(time.monotonic() - tick)
    finally:
        loop.backend.close()
    return summarise(intervals)


def main() -> None:
    from harness.array_smoke import TERRARIUM_MAX_AMPS, build, limited, travelling_wave

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="WLED controller IP")
    parser.add_argument("--pixels", type=int, default=864)
    parser.add_argument("--seconds", type=float, default=120.0)
    args = parser.parse_args()

    _, loop = build(args.pixels, args.host, max_amps=TERRARIUM_MAX_AMPS)
    start = time.monotonic()
    loop.on_frame = limited(
        lambda us: travelling_wave(us, (time.monotonic() - start) * 2.0), loop)

    stats = measure(loop, args.seconds)
    print(f"pixels      {args.pixels}")
    print(f"frames      {stats.frames}")
    print(f"duration    {stats.seconds:.1f} s")
    print(f"mean fps    {stats.mean_fps:.2f}")
    print(f"min fps     {stats.min_fps:.2f}")
    print(f"p95 frame   {stats.p95_frame_ms:.2f} ms")
    print(f"worst frame {stats.worst_frame_ms:.2f} ms")
    print()
    print("Pass criteria: mean >= 43.0, p95 <= 25 ms, worst <= 50 ms.")


if __name__ == "__main__":
    main()
