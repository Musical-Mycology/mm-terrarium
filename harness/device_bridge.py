"""DeviceBridge: Control's per-device light session for /ie<N>/role.

Turns a granted JoinResult's composed light-manifest-v2 blob into a luxaeterna
LightSession, and maps GameServer release onto session.clear() (the CLOSING
fade). Control renders the session and ships its frames to the device over
o2lite (devicelink/agent.py); the device runs no renderer."""

from __future__ import annotations

import time

from control.pixel_wire import WIRE_ORDER
from control.role_config import PLAYER_PIXELS
from luxaeterna.synth.capability import SurfaceCapability, Zone, shroom_capability
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session


def zoned_capability(dev: str, zones,
                     pixel_count: int = PLAYER_PIXELS) -> SurfaceCapability:
    """A player device's surface of `pixel_count` pixels (control/
    role_config.py's player_pixels: 12, or a devtower's 32) carrying a
    role's own zones (Role.zones, (name, start, count) each) plus `primary`
    over the whole surface, which an untargeted manifest instrument
    resolves to -- the same shape harness/room_surface.py gives a Room
    fixture. A role may name only some pixels (a bar on part of the frame):
    the rest get `unzoned<start>` filler zones, since luxaeterna wants a
    surface's zones to tile it whole. ValueError when a zone runs past the
    surface: the role wants a bigger device than this one."""
    named = [Zone(name, start, count) for name, start, count in zones]
    for z in named:
        if z.start + z.count > pixel_count:
            raise ValueError(f"{dev}: zone {z.name!r} (pixels {z.start}.."
                             f"{z.start + z.count - 1}) runs past the "
                             f"device's {pixel_count} pixels")
    return SurfaceCapability(
        surface_id=dev,
        pixel_count=pixel_count,
        color_order=WIRE_ORDER,
        zones=named + _unzoned(named, pixel_count)
        + [Zone("primary", 0, pixel_count)],
    )


def _unzoned(zones, pixel_count: int = PLAYER_PIXELS) -> list[Zone]:
    """One zone per run of the surface's pixels that `zones` leave
    uncovered."""
    covered = {p for z in zones for p in range(z.start, z.start + z.count)}
    out, start = [], None
    for p in range(pixel_count + 1):
        if p < pixel_count and p not in covered:
            if start is None:
                start = p
        elif start is not None:
            out.append(Zone(f"unzoned{start}", start, p - start))
            start = None
    return out


class DeviceBridge:
    def __init__(self, capability=None, clock=time.monotonic) -> None:
        self._cap = capability or shroom_capability()
        self._clock = clock
        self.session = None

    def on_grant(self, join_result):
        """Build the device's LightSession from the composed /ie<N>/role blob."""
        if not join_result.granted:
            raise ValueError(
                f"cannot build a session for a denied join: {join_result.reason}")
        blob = join_result.config["light_manifest"]
        manifest = LightManifest.from_dict(blob)
        self.session = build_session(manifest, self._cap, clock=self._clock)
        return self.session

    def on_release(self, dev) -> None:
        """GameServer released this device -> ask the session to close/fade."""
        if self.session is not None:
            self.session.clear()
