"""The in-memory trace record a capture accumulates into.

Pure: no filesystem, no clock, no transport. capture/store.py owns all I/O.
The serialised shape is the cross-repo contract in
docs/telemetry-trace-schema.md -- change both together.
"""

from __future__ import annotations

from devicelink.protocol import MOTION_AXES, TelemetryBatch

TRACE_SCHEMA = "mm-telemetry-trace/1"

# The motion samples and the audio samples come off two independent clocks.
# Anyone measuring a tap's audio-versus-accelerometer lead needs to know the
# alignment error is about one audio buffer, not zero, so the trace says so
# in words rather than leaving it to be rediscovered.
_AUDIO_CLOCK_NOTE = "audio clock, host-aligned, NOT sample-locked to motion"

DEFAULT_AUDIO_RATE = 16000


class Trace:
    def __init__(self, session: str, capture_id: str, label: str, series: int,
                 dev: str, bit: dict, source: dict, window_ms: float,
                 t0_device: float):
        self.session = session
        self.capture_id = capture_id
        self.label = label
        self.series = series
        self.dev = dev
        self.bit = dict(bit)
        self.source = dict(source)
        self.window_ms = float(window_ms)
        self.t0_device = float(t0_device)

        self.t_ms: list = []
        self.axes: dict = {axis: [] for axis in MOTION_AXES}
        self.pcm = bytearray()
        self.pcm_t0_ms: float | None = None
        self.gaps: list = []
        self.outputs: list = []
        self.truncated = False
        self.notes = ""
        self._next_seq = 0

    @property
    def n(self) -> int:
        return len(self.t_ms)

    def append(self, batch: TelemetryBatch) -> None:
        """Concatenate a decoded batch. A skipped seq is recorded as a gap
        and the batch is still kept; a stale or duplicate seq raises, because
        appending it would silently corrupt the sample order."""
        if batch.seq < self._next_seq:
            raise ValueError(
                f"stale batch seq {batch.seq}, expected {self._next_seq}")
        if batch.seq > self._next_seq:
            self.gaps.append({"expected": self._next_seq, "got": batch.seq})
        self._next_seq = batch.seq + 1

        self.t_ms.extend(batch.t_ms)
        for axis in MOTION_AXES:
            self.axes[axis].extend(batch.axes[axis])
        if batch.pcm:
            if self.pcm_t0_ms is None:
                self.pcm_t0_ms = batch.pcm_t0_ms
            self.pcm.extend(batch.pcm)

    def _audio_dict(self, audio_file: str | None) -> dict | None:
        if audio_file is None or not self.pcm:
            return None
        declared = self.source.get("audio") or {}
        return {"file": audio_file,
                "rate": declared.get("rate", DEFAULT_AUDIO_RATE),
                "channels": declared.get("channels", 1),
                "t0_ms": self.pcm_t0_ms,
                "clock": _AUDIO_CLOCK_NOTE}

    def to_dict(self, audio_file: str | None) -> dict:
        return {
            "schema": TRACE_SCHEMA,
            "session": self.session,
            "capture_id": self.capture_id,
            "label": self.label,
            "series": self.series,
            "dev": self.dev,
            "bit": self.bit,
            "source": self.source,
            "window_ms": self.window_ms,
            "t0_device": self.t0_device,
            "n": self.n,
            "gaps": self.gaps,
            "truncated": self.truncated,
            "samples": {"t_ms": self.t_ms,
                        **{axis: self.axes[axis] for axis in MOTION_AXES}},
            "audio": self._audio_dict(audio_file),
            "outputs": self.outputs,
            "notes": self.notes,
        }
