"""Drive a spanned SK6812 RGBW array over Art-Net to a WLED controller.

Usage:
    python -m harness.array_smoke --host 10.44.0.50 --pixels 288 --seconds 20
    python -m harness.array_smoke --host 10.44.0.50 --pixels 864   # full array

This is the venue-side sibling of ``led_smoke.py``: same renderer, a real strip
instead of a browser canvas, and a pixel count large enough to span universes.

Needs luxaeterna installed (see requirements-dev.txt).

SAFETY: the 864-pixel Terrarium array draws 21.6 A at full white against a
12.5 A supply. Build it with ``max_amps=TERRARIUM_MAX_AMPS``; the CLI does this
for you and there is no flag to turn it off.
"""

from __future__ import annotations

import argparse
import math
import time

from luxaeterna.backends.artnet import ArtNet
from luxaeterna.pixelspan import PixelSpan
from luxaeterna.power import PowerBudget, PowerLimiter
from luxaeterna.universeset import MultiUniverseOutputLoop, UniverseSet

CHANNELS_PER_PIXEL = 4          # SK6812 RGBW
TERRARIUM_PIXELS = 864          # 6 m at 144 px/m
TERRARIUM_MAX_AMPS = 10.0       # 80% of the LRS-150-12's 12.5 A


def build(pixel_count: int, wled_host: str, start_universe: int = 0,
          max_amps: float | None = None
          ) -> tuple[UniverseSet, MultiUniverseOutputLoop]:
    """Construct the universe set and its output loop. Does not start the loop.

    Passing *max_amps* installs a :class:`PowerLimiter` on the loop. The
    Terrarium array MUST be built with ``max_amps=TERRARIUM_MAX_AMPS``; it draws
    21.6 A at full white against a 12.5 A supply.
    """
    span = PixelSpan(pixel_count,
                     channels_per_pixel=CHANNELS_PER_PIXEL,
                     start_universe=start_universe)
    universe_set = UniverseSet(span)
    loop = MultiUniverseOutputLoop(universe_set, ArtNet(host=wled_host))
    loop.limiter = (
        PowerLimiter(PowerBudget(max_amps=max_amps)) if max_amps else None)
    return universe_set, loop


def limited(paint, loop):
    """Wrap a paint hook so every frame passes through the loop's limiter.

    The limiter runs on each universe's own buffer after the paint, which is
    the last point before ``_loop_once`` snapshots and sends.
    """
    def hook(universe_set):
        paint(universe_set)
        if loop.limiter is not None:
            for universe in universe_set.universes:
                universe.set_range(0, loop.limiter.apply(universe.get_frame()))
    return hook


def travelling_wave(universe_set: UniverseSet, phase: float) -> None:
    """A slow green wave. Green only, so a wrong colour order is obvious."""
    count = universe_set.span.pixel_count
    for px in range(count):
        level = int(90 * (0.5 + 0.5 * math.sin(phase + px * 0.05)))
        universe_set.fill_pixel(px, bytes([0, level, 0, 0]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="WLED controller IP")
    parser.add_argument("--pixels", type=int, default=288)
    parser.add_argument("--start-universe", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    universe_set, loop = build(args.pixels, args.host, args.start_universe,
                               max_amps=TERRARIUM_MAX_AMPS)
    start = time.monotonic()
    loop.on_frame = limited(
        lambda us: travelling_wave(us, (time.monotonic() - start) * 2.0), loop)
    loop.start()
    try:
        while time.monotonic() - start < args.seconds:
            time.sleep(1.0)
            print(f"fps {loop.fps:.1f}")
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()


if __name__ == "__main__":
    main()
