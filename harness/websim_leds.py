"""WebSimLeds: adapt ShroomClient's leds.show(bytes)/leds.clear() to a
luxaeterna WebSimBackend, plus the block-identification frame the
--identify-blocks build-out tool paints.

Lived in harness/room_simulator.py until the o2lite cutover deleted that
websocket simulator; harness/o2_shroom.py is the one consumer now.
"""

from __future__ import annotations

import logging

# Fixed identification palette, assigned to blocks in declaration order
# (red, orange, yellow, green, blue, violet). RGB triples; laid out per the
# fixture's color_order when painted. Repeats past six blocks.
BLOCK_PALETTE: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0), (255, 128, 0), (255, 255, 0),
    (0, 255, 0), (0, 0, 255), (148, 0, 211),
)

logger = logging.getLogger(__name__)


def identify_blocks_frame(profile, fixture_name: str) -> bytes:
    """One static frame painting each of the fixture's blocks a distinct
    solid color, so a human can visually confirm the physical build-out
    mapping on the canvas. Harness-only: the one consumer of block
    boundaries this slice (blocks are otherwise declarative -- see
    control/room_profile.py's RoomBlock)."""
    fixture = next(f for f in profile.fixtures if f.name == fixture_name)
    order = fixture.color_order.upper()
    frame = bytearray(fixture.pixel_count * 3)
    for i, block in enumerate(fixture.blocks):
        rgb = dict(zip("RGB", BLOCK_PALETTE[i % len(BLOCK_PALETTE)]))
        px = bytes(rgb[ch] for ch in order)
        frame[block.start * 3:(block.start + block.count) * 3] = \
            px * block.count
    return bytes(frame)


class WebSimLeds:
    """Adapts ShroomClient's leds.show(bytes)/leds.clear() to
    WebSimBackend's send(frame).

    `channels` is the frame width this surface expects. It is a parameter
    rather than the LED_CHANNELS constant because a Room is not a Testshroom:
    the Room's width comes from its RoomProfile (60 px x 3 = 180), while a
    player device is still 12 px x GRB = 36.
    """

    def __init__(self, backend, channels: int, on_show=None,
                 clock=None) -> None:
        self._backend = backend
        self._channels = channels
        # on_show(frame, now): called after every DISPLAYED frame (not
        # clear()), with `now` from `clock()` when a clock is given, else
        # None. harness/beat_tapper.py listens here; the Room simulator
        # passes neither. A raising hook is logged and never stops the
        # frame -- a broken tapper must not blank the canvas.
        self._on_show = on_show
        self._clock = clock

    def show(self, frame: bytes) -> None:
        self._backend.send(frame)
        if self._on_show is None:
            return
        try:
            self._on_show(frame, self._clock() if self._clock else None)
        except Exception:
            logger.exception("on_show hook raised; frame already displayed")

    def clear(self) -> None:
        self._backend.send(bytes(self._channels))
