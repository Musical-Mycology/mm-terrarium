"""RoomProfile: the Room's own fixture declaration, so a Room stops being
shaped like a Tuneshroom.

Deliberately pure. This module imports nothing outside the standard library
and control/, which is what lets the engine be reasoned about and tested with
no renderer present. The luxaeterna adapter lives in harness/room_surface.py,
mirroring how harness/device_bridge.py already adapts Control-side role
declarations for player devices. See
docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md
section 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.rooms import RoomType


@dataclass(frozen=True)
class RoomZone:
    """A named, contiguous run of pixels a light instrument can target.

    Mirrors luxaeterna's Zone field-for-field so the adapter is a rename and
    nothing more. `primary` is deliberately NOT declared in any profile:
    SurfaceCapability.zone() synthesizes it on demand for the whole surface,
    and a real `primary` zone would overlay every other zone in the Console's
    view.
    """
    name: str
    start: int
    count: int


@dataclass(frozen=True)
class RoomProfile:
    """One Room's physical (or simulated) light surface."""
    surface_id: str
    pixel_count: int
    color_order: str
    zones: tuple[RoomZone, ...]

    @property
    def channel_count(self) -> int:
        """Wire width of one rendered frame. Three channels per pixel, matching
        the GRB wire devicelink/protocol.py's leds_event carries today. The
        RGBW question (widening to four) is a separate open decision about the
        Tuneshroom's white die and does not belong to the Room."""
        return self.pixel_count * 3


# A single luxaeterna Universe is 512 DMX channels, so a one-surface Room caps
# at 170 px RGB. 60 px sits well inside that. Anything larger needs
# PixelSpan/UniverseSet (luxaeterna has them; harness/array_smoke.py uses them
# for the 864 px venue array) and is out of scope for this slice.
#
# Linear with three equal zones because the physical Terrarium array is a
# single 6 m run, not a ring and a stem.
ROOM_PROFILES: dict[RoomType, RoomProfile] = {
    RoomType.TEST: RoomProfile(
        surface_id="room_test",
        pixel_count=60,
        color_order="GRB",
        zones=(RoomZone("left", 0, 20),
               RoomZone("center", 20, 20),
               RoomZone("right", 40, 20)),
    ),
}


def room_profile(room_type: RoomType) -> RoomProfile:
    """This Room type's fixture declaration.

    Raises rather than substituting a default, matching
    control/rooms.py's resolve_room_type(): a Terrarium that cannot render the
    Room it was configured for must fail at boot, not render the wrong thing
    all night.
    """
    try:
        return ROOM_PROFILES[room_type]
    except KeyError:
        raise NotImplementedError(
            f"{room_type.name} has no room profile; only "
            f"{', '.join(t.name for t in ROOM_PROFILES)} is implemented"
        ) from None
