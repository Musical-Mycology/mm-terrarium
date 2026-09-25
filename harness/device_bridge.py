"""DeviceBridge: Control's per-device light session for /ie<N>/role.

Turns a granted JoinResult's composed light-manifest-v2 blob into a luxaeterna
LightSession, and maps GameServer release onto session.clear() (the CLOSING
fade). Control renders the session and ships its frames to the device over
o2lite (devicelink/agent.py); the device runs no renderer."""

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
