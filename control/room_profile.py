"""RoomProfile: the Room's own N-fixture declaration, so a Room stops being
shaped like a Tuneshroom and stops being shaped like exactly one device.

Deliberately pure. This module imports nothing outside the standard library
and control/, which is what lets the engine be reasoned about and tested with
no renderer present. The luxaeterna adapter lives in harness/room_surface.py,
mirroring how harness/device_bridge.py already adapts Control-side role
declarations for player devices. See
docs/superpowers/specs/2026-08-18-n-fixture-room-design.md sections 2-3.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.rooms import RoomType

# A single luxaeterna Universe is 512 DMX channels, so a Room caps at 170 px
# RGB in total across every fixture. Anything larger needs PixelSpan/
# UniverseSet (luxaeterna has them; harness/array_smoke.py uses them for the
# 864 px venue array) and is out of scope for this slice (non-goal N5).
_MAX_PROFILE_PIXELS = 170


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
class RoomFixture:
    """One physical (or simulated) light fixture -- its own o2lite client,
    its own unique service name, once bound. See design spec section 3."""
    name: str
    pixel_count: int
    color_order: str
    zones: tuple[RoomZone, ...]


@dataclass(frozen=True)
class RoomProfile:
    """One Room's physical (or simulated) light surface: N fixtures laid end
    to end. Declaration order IS physical order -- a spatial instrument's
    position 0.0 is the first pixel of the first declared fixture, so a
    bottom-to-top effect means the fixtures are declared bottom-to-top. See
    design spec section 2."""
    surface_id: str
    fixtures: tuple[RoomFixture, ...]

    def __post_init__(self) -> None:
        if not self.fixtures:
            raise ValueError(f"profile {self.surface_id!r} declares no fixtures")
        names = [f.name for f in self.fixtures]
        if len(names) != len(set(names)):
            raise ValueError(
                f"profile {self.surface_id!r} has duplicate fixture names: {names}")
        orders = {f.color_order for f in self.fixtures}
        if len(orders) > 1:
            raise ValueError(
                f"profile {self.surface_id!r} mixes color_order {sorted(orders)}; "
                f"one Room renders through one shared session this slice "
                f"(non-goal N4)")
        for fixture in self.fixtures:
            # Overlap and overrun are real configuration bugs; declaration
            # order and full coverage are not requirements -- a fixture may
            # leave pixels undeclared, and harness/room_surface.py's adapter
            # has always preserved whatever zone order a profile gives it
            # (see tests/test_room_surface.py's
            # test_zone_order_is_preserved_for_an_unsorted_profile).
            intervals = sorted((z.start, z.start + z.count) for z in fixture.zones)
            for start, end in intervals:
                if start < 0 or end > fixture.pixel_count:
                    raise ValueError(
                        f"fixture {fixture.name!r} has a zone overrunning "
                        f"its {fixture.pixel_count} px (extends to {end})")
            for i in range(1, len(intervals)):
                if intervals[i][0] < intervals[i - 1][1]:
                    raise ValueError(
                        f"fixture {fixture.name!r} has overlapping zones")
        if self.pixel_count > _MAX_PROFILE_PIXELS:
            raise ValueError(
                f"profile {self.surface_id!r} has {self.pixel_count} px "
                f"total, over the {_MAX_PROFILE_PIXELS} px single-universe "
                f"cap (non-goal N5)")

    @property
    def pixel_count(self) -> int:
        """Total pixels across every fixture, i.e. the concatenated virtual
        surface's width."""
        return sum(f.pixel_count for f in self.fixtures)

    @property
    def channel_count(self) -> int:
        """Wire width of one rendered frame, whole-profile. Three channels
        per pixel, matching the GRB wire devicelink/protocol.py's leds_event
        carries today. The RGBW question (widening to four) is a separate
        open decision about the Tuneshroom's white die and does not belong
        to the Room."""
        return self.pixel_count * 3

    @property
    def color_order(self) -> str:
        """The shared color order every fixture in this profile declares
        (validated equal in __post_init__)."""
        return self.fixtures[0].color_order

    @property
    def zones(self) -> tuple[RoomZone, ...]:
        """Every fixture's zones, renamed `<fixture>.<zone>` and offset into
        the concatenated surface, in fixture declaration order. This is the
        namespaced union the Console draws and a spatial instrument's
        `primary` target spans."""
        out: list[RoomZone] = []
        offset = 0
        for fixture in self.fixtures:
            for zone in fixture.zones:
                out.append(RoomZone(f"{fixture.name}.{zone.name}",
                                    offset + zone.start, zone.count))
            offset += fixture.pixel_count
        return tuple(out)

    def fixture_slices(self) -> tuple[tuple[str, int, int], ...]:
        """Per fixture: (name, channel_start, channel_count) into one
        rendered whole-profile frame, in declaration order. What
        devicelink/agent.py's _render_room() slices a frame with, and what
        each simulator's own client passes as expected_channels."""
        out: list[tuple[str, int, int]] = []
        offset = 0
        for fixture in self.fixtures:
            out.append((fixture.name, offset * 3, fixture.pixel_count * 3))
            offset += fixture.pixel_count
        return tuple(out)


# Linear because the real Terrarium array is a single 6 m run, not a ring and
# a stem. Two fixtures, deliberately asymmetric: the smallest N that
# exercises fan-out, distinct service names, distinct frame widths and
# namespaced zones, with asymmetry so same-shape assumptions cannot hide.
# `main` is the original single-fixture TEST surface, unchanged in shape;
# `accent` is new.
ROOM_PROFILES: dict[RoomType, RoomProfile] = {
    RoomType.TEST: RoomProfile(
        surface_id="room_test",
        fixtures=(
            RoomFixture(
                name="main", pixel_count=60, color_order="GRB",
                zones=(RoomZone("left", 0, 20),
                      RoomZone("center", 20, 20),
                      RoomZone("right", 40, 20))),
            RoomFixture(
                name="accent", pixel_count=30, color_order="GRB",
                zones=(RoomZone("low", 0, 15),
                      RoomZone("high", 15, 15))),
        ),
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
