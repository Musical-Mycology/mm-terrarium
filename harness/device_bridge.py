"""DeviceBridge: in-process stand-in for a device consuming /ie<N>/role.

Turns a granted JoinResult's composed light-manifest-v2 blob into a luxaeterna
LightSession (the device's local renderer), and maps GameServer release onto
session.clear() (the device-side CLOSING fade). This is the seam the real
o2lite transport will replace in Slice 2."""

from __future__ import annotations

import time

from luxaeterna.synth.capability import shroom_capability
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session


class DeviceBridge:
    def __init__(self, capability=None, clock=time.monotonic) -> None:
        self._cap = capability or shroom_capability()
        self._clock = clock
        self.session = None

    def on_grant(self, join_result):
        """Build the device's LightSession from the composed /ie<N>/role blob."""
        blob = join_result.config["light_manifest"]
        manifest = LightManifest.from_dict(blob)
        self.session = build_session(manifest, self._cap, clock=self._clock)
        return self.session

    def on_release(self, dev) -> None:
        """GameServer released this device -> ask the session to close/fade."""
        if self.session is not None:
            self.session.clear()
