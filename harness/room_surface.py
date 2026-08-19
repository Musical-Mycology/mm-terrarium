"""Adapt a Control-side RoomProfile into the luxaeterna SurfaceCapability the
renderer needs.

This module exists so control/room_profile.py can stay import-free. It is the
Room-scoped peer of harness/device_bridge.py, which already does the same job
for a player device's role declaration. Both consumers (devicelink/agent.py
and harness/room_simulator.py) already import from harness/, so this
introduces no new dependency direction. See
docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md
section 4.
"""

from __future__ import annotations

from control.room_profile import RoomProfile
from luxaeterna.synth.capability import SurfaceCapability, Zone


def to_capability(profile: RoomProfile) -> SurfaceCapability:
    """Build the renderer's view of this Room's surface.

    `primary` is appended here rather than declared in the profile. A
    light_manifest instrument that names no target resolves to it, so it has
    to exist for the renderer; but it spans the whole surface, so it must not
    appear in the Console's zone list where it would be drawn over every real
    zone. Appending it in the adapter gives both halves what they need from
    one declaration.
    """
    zones = [Zone(z.name, z.start, z.count) for z in profile.zones]
    zones.append(Zone("primary", 0, profile.pixel_count))
    return SurfaceCapability(
        surface_id=profile.surface_id,
        pixel_count=profile.pixel_count,
        color_order=profile.color_order,
        zones=zones,
    )


def to_fixture_capability(profile: RoomProfile, fixture_name: str):
    """Build ONE fixture's own standalone capability -- for a simulator
    process that displays only that fixture's own physical strip, with
    LOCAL (unprefixed) zone names, not the profile's global namespaced
    union. Distinct from to_capability(), which builds the WHOLE
    concatenated surface for DeviceLinkAgent's one shared session. See
    design spec section 7."""
    fixture = next(f for f in profile.fixtures if f.name == fixture_name)
    zones = [Zone(z.name, z.start, z.count) for z in fixture.zones]
    zones.append(Zone("primary", 0, fixture.pixel_count))
    return SurfaceCapability(
        surface_id=f"{profile.surface_id}_{fixture_name}",
        pixel_count=fixture.pixel_count,
        color_order=fixture.color_order,
        zones=zones,
    )
