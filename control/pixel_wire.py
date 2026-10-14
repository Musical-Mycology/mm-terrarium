"""The one pixel wire every sink carries (contract v5, spec
docs/superpowers/specs/2026-10-14-rgbw-device-wire-design.md section 3):
R, G, B, W, one byte each. A strip's physical order is a driver setting
in firmware or WLED, never on this wire. W is authored, never derived:
an RGB colour has W = 0."""

from __future__ import annotations

WIRE_ORDER = "RGBW"
CHANNELS_PER_PIXEL = 4

# The light lexicon's white: the W die alone (spec section 4). An RGB-only
# device shows it through the fold.
WHITE_W = (0, 0, 0, 255)


def rgbw(color: tuple) -> tuple[int, int, int, int]:
    """(r, g, b) -> (r, g, b, 0); (r, g, b, w) unchanged."""
    if len(color) == 3:
        r, g, b = color
        return (r, g, b, 0)
    if len(color) == 4:
        r, g, b, w = color
        return (r, g, b, w)
    raise ValueError(f"colour must be (r, g, b) or (r, g, b, w), got {color!r}")


def solid_frame(color: tuple, level: float, length: int) -> bytes:
    """`length` bytes of one colour at `level`, R, G, B, W per pixel."""
    pixel = bytes(max(0, min(255, round(c * level))) for c in rgbw(color))
    reps = length // CHANNELS_PER_PIXEL + 1
    return (pixel * reps)[:length]
