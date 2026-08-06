"""Local sample playback on the Tuneshroom.

Synthesis lives on the Terrarium, but "tap -> local sound under 20 ms" cannot
survive a network round trip, so the immediate hit sounds from the device's own
speaker. Arco still owns the room mix and everything the hit implies beyond the
player's own ear. See MM_HARDWARE_DESIGN.md §4.4.

Samples are read into memory at preload, never at play: a file read on the tap
path is the difference between 3 ms and 30 ms.

``last_latency_ms`` measures *dispatch*, not sound. It tells you how long this
code took to hand bytes to the sink, and nothing about ALSA buffering or the
speaker. The real tap-to-sound figure has to be measured acoustically -- record
the tap and the resulting sound on one audio track and read the gap off the
waveform. Do not report the dispatch number as the latency.
"""

from __future__ import annotations

import time


class SamplePlayer:
    """Preloaded PCM samples dispatched to an audio sink with latency accounting."""

    def __init__(self, sample_paths: dict[str, str], sink=None, loader=None) -> None:
        self.sample_paths = dict(sample_paths)
        self.sink = sink
        self._loader = loader or self._default_loader
        self._data: dict[str, bytes] = {}
        self.last_latency_ms: float = 0.0

    @staticmethod
    def _default_loader(path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    @property
    def is_preloaded(self) -> bool:
        return len(self._data) == len(self.sample_paths)

    def preload(self) -> None:
        """Read every sample into memory. Idempotent."""
        if self.is_preloaded:
            return
        for name, path in self.sample_paths.items():
            self._data[name] = self._loader(path)

    def play(self, name: str) -> float:
        """Dispatch *name* to the sink. Returns dispatch latency in seconds."""
        if not self.is_preloaded:
            raise RuntimeError("call preload() before play()")
        if name not in self._data:
            raise KeyError(name)
        started = time.perf_counter()
        if self.sink is not None:
            self.sink.write(name, self._data[name])
        latency = time.perf_counter() - started
        self.last_latency_ms = latency * 1000.0
        return latency

    def __repr__(self) -> str:
        return (f"SamplePlayer(samples={sorted(self.sample_paths)}, "
                f"preloaded={self.is_preloaded})")
