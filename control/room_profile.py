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

from control.instrument import (Instrument, InstrumentError,
                                 validate_instrument,
                                 validate_instrument_manifests)

# A single luxaeterna Universe is 512 DMX channels, so one BLOCK -- one
# physical LED device / one controller's worth -- caps at 170 px RGB (see
# RoomBlock). A whole profile may exceed this by declaring more blocks;
# anything larger than one universe per block needs PixelSpan/UniverseSet
# (luxaeterna has them; harness/array_smoke.py uses them for the 864 px venue
# array) and is out of scope for this slice (non-goal N5).
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
class RoomBlock:
    """A physical LED device's own pixel range within a Fixture -- the
    build-out unit ("which literal LED device drives this pixel range").
    Individually capped at _MAX_PROFILE_PIXELS (one DMX universe / one
    controller's worth). Purely declarative this slice: no simulator or
    backend consumes block boundaries for output routing yet -- a future
    real per-controller adapter reads them off the profile with no further
    data-model change needed. Only harness/room_simulator.py's
    --identify-blocks debug tool reads them today. A different axis from
    RoomZone, which is gameplay/Console targeting; blocks are hardware
    composition. See
    docs/superpowers/specs/2026-08-19-demo-room-and-block-profile-design.md
    section 2."""
    name: str
    start: int
    count: int


@dataclass(frozen=True)
class RoomFixture:
    """One physical (or simulated) light fixture -- its own o2lite client,
    its own unique service name, once bound. One continuous physical run,
    decomposed into blocks (see RoomBlock). See design spec section 3 and
    the 2026-08-19 block spec section 2."""
    name: str
    color_order: str
    blocks: tuple[RoomBlock, ...]
    zones: tuple[RoomZone, ...]
    instrument: Instrument

    @property
    def pixel_count(self) -> int:
        """Derived, not stored: the sum of this fixture's blocks, which
        must tile the fixture exactly (validated in RoomProfile)."""
        return sum(b.count for b in self.blocks)


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
            if not fixture.blocks:
                raise ValueError(
                    f"fixture {fixture.name!r} declares no blocks")
            block_names = [b.name for b in fixture.blocks]
            if len(block_names) != len(set(block_names)):
                raise ValueError(
                    f"fixture {fixture.name!r} has duplicate block names: "
                    f"{block_names}")
            for block in fixture.blocks:
                if block.count <= 0:
                    raise ValueError(
                        f"block {fixture.name!r}.{block.name!r} must have a "
                        f"positive count, got {block.count}")
                if block.count > _MAX_PROFILE_PIXELS:
                    raise ValueError(
                        f"block {fixture.name!r}.{block.name!r} is "
                        f"{block.count} px, over the {_MAX_PROFILE_PIXELS} px "
                        f"single-universe cap")
            spans = sorted((b.start, b.start + b.count) for b in fixture.blocks)
            for i in range(1, len(spans)):
                if spans[i][0] < spans[i - 1][1]:
                    raise ValueError(
                        f"fixture {fixture.name!r} has overlapping blocks")
            expected = 0
            for start, end in spans:
                if start != expected:
                    raise ValueError(
                        f"fixture {fixture.name!r}'s blocks do not tile the "
                        f"fixture: gap before pixel {start}")
                expected = end
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
        for fixture in self.fixtures:
            try:
                validate_instrument(fixture.instrument)
                validate_instrument_manifests(fixture.instrument)
            except InstrumentError as exc:
                raise ValueError(
                    f"fixture {fixture.name!r}: {exc}") from exc

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
