"""Real luxaeterna-backed BenchSession for the Design bench.

Renders a draft instrument's ambient light manifest on a standard
Testshroom-sized surface (shroom_capability). Dev/test dependency on
luxaeterna, mirroring harness/device_bridge.py.

Deviations from the task-4 sketch, both forced by reading the real
luxaeterna API rather than guessing it:

- ``LightSession.render_into`` takes a ``luxaeterna.universe.Universe``,
  not a plain buffer -- ``harness/led_smoke.py`` hands it a bare
  ``Universe()`` (the DMX512 default, 512 channels) rather than sizing one
  off the capability, so this module follows that same precedent instead
  of computing a bespoke width. ``_channel_count()`` reports the number of
  channels the capability itself actually occupies (pixel_count *
  channels-per-pixel, from ``luxaeterna.synth.engine.channels_for``) purely
  so ``render()`` can hand back a right-sized slice of the Universe's
  frame -- the Universe object underneath is still the standard 512-wide
  one.
- ``clear()`` is confirmed (by reading ``luxaeterna.synth.session``) as the
  correct teardown: it enqueues a ``ClearEvent`` the director applies on
  the next frame boundary, exactly what ``harness/device_bridge.py``'s
  ``DeviceBridge.on_release`` already relies on for the same purpose.
"""
from __future__ import annotations

import time

from luxaeterna.synth.capability import shroom_capability
from luxaeterna.synth.engine import channels_for
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session
from luxaeterna.universe import Universe


class LuxBenchSession:
    def __init__(self, light_manifest: dict, clock=time.monotonic):
        self._cap = shroom_capability()
        manifest = LightManifest.from_dict(light_manifest or {})
        self._session = build_session(manifest, self._cap, clock=clock)
        self._universe = Universe()

    def _channel_count(self) -> int:
        return self._cap.pixel_count * channels_for(self._cap.color_order)

    def feed_midi(self, status, d1, d2):
        self._session.feed_midi(status, d1, d2)

    def render(self) -> list[int]:
        self._session.render_into(self._universe)
        frame = self._universe.get_frame()
        return list(frame[:self._channel_count()])

    def close(self):
        self._session.clear()


def bench_session_factory(light_manifest: dict):
    return LuxBenchSession(light_manifest)
