"""The pixel every render buffer and sink carries (contract v5, spec
docs/superpowers/specs/2026-10-14-rgbw-device-wire-design.md section 3):
R, G, B, W, one byte each. A strip's physical order is a driver setting
in firmware or WLED, never on this wire. W is authored, never derived:
an RGB colour has W = 0.

A device's /<dev>/leds blob is the exception: R, G, B only, three bytes a
pixel (BLOB_CHANNELS_PER_PIXEL). devicelink/protocol.py's leds_event
folds each RGBW pixel into it with rgb_blob (the spec's section 3.2 fold),
and a simulated device reads it back with rgbw_from_blob."""

from __future__ import annotations

WIRE_ORDER = "RGBW"
CHANNELS_PER_PIXEL = 4
BLOB_CHANNELS_PER_PIXEL = 3     # a /<dev>/leds blob: R, G, B

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


def rgb_blob(frame) -> bytes:
    """An RGBW frame (R, G, B, W per pixel) as a /<dev>/leds blob, R, G, B
    per pixel, W folded in: min(255, c + w) for each of R, G and B."""
    frame = bytes(int(v) & 0xFF for v in frame)
    if len(frame) % CHANNELS_PER_PIXEL:
        raise ValueError(f"an RGBW frame is a multiple of "
                         f"{CHANNELS_PER_PIXEL} channels, got {len(frame)}")
    out = bytearray()
    for i in range(0, len(frame), CHANNELS_PER_PIXEL):
        r, g, b, w = frame[i:i + CHANNELS_PER_PIXEL]
        out += bytes((min(255, r + w), min(255, g + w), min(255, b + w)))
    return bytes(out)


def rgbw_from_blob(blob) -> bytes:
    """A /<dev>/leds blob (R, G, B per pixel) as an RGBW frame, W = 0."""
    blob = bytes(int(v) & 0xFF for v in blob)
    out = bytearray()
    for i in range(0, len(blob), BLOB_CHANNELS_PER_PIXEL):
        out += blob[i:i + BLOB_CHANNELS_PER_PIXEL] + b"\x00"
    return bytes(out)


def blob_channels(channels: int) -> int:
    """The /<dev>/leds blob width for an RGBW frame of `channels`."""
    return channels // CHANNELS_PER_PIXEL * BLOB_CHANNELS_PER_PIXEL


def solid_frame(color: tuple, level: float, length: int) -> bytes:
    """`length` bytes of one colour at `level`, R, G, B, W per pixel."""
    pixel = bytes(max(0, min(255, round(c * level))) for c in rgbw(color))
    reps = length // CHANNELS_PER_PIXEL + 1
    return (pixel * reps)[:length]
