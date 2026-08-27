"""CaptureStore: the only filesystem-touching code in the capture package.

Layout, one directory per session:

    <root>/<session-id>/
        index.jsonl                one line appended per closed capture
        <label>/<series>.json      the trace
        <label>/<series>.wav       its mic audio, when the capture had any

Writes happen at capture close, never per batch: append() only accumulates
into the open Trace, so filesystem contact is roughly once per gesture and
never touches the hot path. A crash loses at most the capture in flight.

Boundary rule 2: no method here may raise into the engine tick. Refusals a
Bit should surface to its device raise CaptureError, which the Bit converts
into a refusal string; anything else (a full disk, a read-only mount) is
logged, counted, and swallowed.
"""

from __future__ import annotations

import datetime
import io
import logging
import secrets
import time
import wave
from pathlib import Path

from capture.trace import DEFAULT_AUDIO_RATE, Trace
from control.wire_json import dumps as _json_dumps
from devicelink.protocol import CaptureCommand, TelemetryBatch

logger = logging.getLogger(__name__)

# Grace beyond a capture's declared window before the server force-closes
# it -- generous headroom for network jitter, not a tight deadline. Bounds
# per-capture memory growth for a client that never sends `close`.
_WINDOW_GRACE_MS = 5000.0


class CaptureError(Exception):
    """A refusal the Bit surfaces to the device as /<dev>/error. Its str()
    is the reason, so keep the message device-facing and actionable."""


def new_session_id(now=None, suffix=None) -> str:
    """A sortable, filesystem-safe session id: no colons, UTC, plus a short
    random suffix so two sessions started in the same second cannot collide."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    suffix = suffix or secrets.token_hex(2)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ") + "-" + suffix


def wav_bytes(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Frame raw int16le PCM as a WAV file. A sidecar rather than base64
    inside the JSON so the audio is openable in Audacity and listenable."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


class CaptureStore:
    def __init__(self, root, session_id: str, bit: dict,
                 clock=time.monotonic, provenance: dict | None = None):
        self.root = Path(root)
        self.session_id = session_id
        self.bit = dict(bit)
        # Room provenance (room_name/terrarium_config_version), stamped into
        # every trace this store writes when non-empty. Threaded in from
        # whoever constructs the store -- see bits/capture/capture_bit.py's
        # CaptureBit.__init__ for the (currently gs-less) seam.
        self.provenance = dict(provenance) if provenance else {}
        self._clock = clock
        self._open: dict = {}          # dev -> Trace
        self._last_seen: dict = {}     # dev -> clock reading
        self._counts: dict = {}        # label -> closed captures
        self.failures = 0
        self.bytes_written = 0

    # --- read-out for Bit.status() ---------------------------------------
    def open_ids(self) -> dict:
        return {dev: trace.capture_id for dev, trace in self._open.items()}

    def counts(self) -> dict:
        return dict(self._counts)

    @property
    def session_dir(self) -> Path:
        return self.root / self.session_id

    # --- capture lifecycle -----------------------------------------------
    def open_capture(self, dev: str, cmd: CaptureCommand) -> None:
        if dev in self._open:
            raise CaptureError(
                f"{dev} already open on {self._open[dev].capture_id}")
        meta = cmd.meta
        label, series = meta["label"], meta["series"]
        for other_dev, other in self._open.items():
            if other.label == label and other.series == series:
                raise CaptureError(
                    f"{label}/{series} already open on {other_dev}")
        self._open[dev] = Trace(
            session=self.session_id, capture_id=cmd.capture_id,
            label=label, series=series, dev=dev,
            bit=self.bit, source=meta["source"],
            window_ms=meta["window_ms"], t0_device=float(meta["t0"]))
        self._last_seen[dev] = self._clock()

    def append(self, dev: str, batch: TelemetryBatch) -> None:
        trace = self._require_open(dev)
        if batch.capture_id != trace.capture_id:
            raise CaptureError(
                f"batch is for {batch.capture_id}, {dev} has "
                f"{trace.capture_id} open")
        if batch.t_ms and batch.t_ms[-1] > trace.window_ms + _WINDOW_GRACE_MS:
            self._truncate(
                dev, f"exceeded window_ms ({trace.window_ms:g}ms + "
                     f"{_WINDOW_GRACE_MS:g}ms grace)")
            raise CaptureError(
                f"capture window ({trace.window_ms:g}ms) exceeded; closed")
        try:
            trace.append(batch)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        self._last_seen[dev] = self._clock()

    def close_capture(self, dev: str, meta: dict) -> None:
        trace = self._require_open(dev)
        trace.outputs = meta.get("outputs") or []
        self._release(dev)
        self._write(trace)

    def abandon(self, dev: str, reason: str) -> None:
        """Drop an in-flight capture without writing it. Used when the client
        cancels, or when the mic was denied partway through."""
        if dev not in self._open:
            return
        logger.info("abandoning %s on %s: %s",
                    self._open[dev].capture_id, dev, reason)
        self._release(dev)

    def expire(self, idle_s: float) -> list:
        """Close any capture whose device has gone quiet, marking it
        truncated. Returns the devices closed. Without this, a phone that
        leaves WiFi mid-window leaves a capture open forever."""
        now = self._clock()
        stale = [dev for dev, seen in self._last_seen.items()
                 if now - seen >= idle_s]
        for dev in stale:
            self._truncate(dev, f"no telemetry for {idle_s:g}s")
        return stale

    def truncate_all(self, reason: str) -> list:
        """Close every capture still open, marking each truncated. Called
        from the Bit's on_unload so a session teardown never strands data."""
        devs = list(self._open)
        for dev in devs:
            self._truncate(dev, reason)
        return devs

    # --- internals --------------------------------------------------------
    def _require_open(self, dev: str) -> Trace:
        trace = self._open.get(dev)
        if trace is None:
            raise CaptureError(f"no open capture for {dev}")
        return trace

    def _release(self, dev: str) -> None:
        self._open.pop(dev, None)
        self._last_seen.pop(dev, None)

    def _truncate(self, dev: str, reason: str) -> None:
        trace = self._open[dev]
        trace.truncated = True
        trace.notes = reason
        self._release(dev)
        self._write(trace)

    def _write(self, trace: Trace) -> None:
        """The single write per capture. Everything here is best-effort: a
        write failure is logged and counted, never raised."""
        stem = f"{trace.series:03d}"
        audio_file = f"{stem}.wav" if trace.pcm else None
        directory = self.session_dir / trace.label
        if (directory / f"{stem}.json").exists():
            self.failures += 1
            logger.error(
                "capture %s would overwrite existing %s/%s.json; skipping "
                "write to avoid silent data loss", trace.capture_id,
                trace.label, stem)
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            trace_dict = trace.to_dict(audio_file)
            if self.provenance:
                trace_dict.update(self.provenance)
            body = _json_dumps(trace_dict, separators=(",", ":"))
            (directory / f"{stem}.json").write_text(body)
            self.bytes_written += len(body)
            if audio_file is not None:
                rate = (trace.source.get("audio") or {}).get("rate", DEFAULT_AUDIO_RATE)
                data = wav_bytes(bytes(trace.pcm), rate=rate)
                (directory / audio_file).write_bytes(data)
                self.bytes_written += len(data)
        except Exception:
            self.failures += 1
            logger.exception("writing capture %s failed; continuing",
                             trace.capture_id)
            return
        self._counts[trace.label] = self._counts.get(trace.label, 0) + 1
        self._append_index(trace, stem)

    def _append_index(self, trace: Trace, stem: str) -> None:
        line = _json_dumps({"capture_id": trace.capture_id,
                           "label": trace.label, "series": trace.series,
                           "dev": trace.dev, "n": trace.n,
                           "truncated": trace.truncated,
                           "gaps": len(trace.gaps),
                           "path": f"{trace.label}/{stem}.json"},
                          separators=(",", ":"))
        try:
            with (self.session_dir / "index.jsonl").open("a") as fh:
                fh.write(line + "\n")
        except Exception:
            self.failures += 1
            logger.exception("index append for %s failed; continuing",
                             trace.capture_id)
