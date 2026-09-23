"""ArtNetFixtureSink: one Room fixture's frames, over Art-Net, to a WLED
controller that has no clock.

WLED latches a frame the moment it arrives, so `when` can only be honored
here: send_frame() queues the frame, and a sender thread sends it once it is
due (minus `lead`, the controller's own measured latency). The newest due
frame wins, so a stall never builds a backlog. With nothing new to send, the
last frame is resent every `keepalive` so WLED stays in realtime mode and a
lost UDP packet heals. Every frame, including keepalives and the close
frame, passes this output's PowerLimiter first.

send_frame() runs on the engine tick: a lock, a push and a notify, and no
I/O (boundary rule 2). Everything else runs on the sender thread.

Spec: docs/superpowers/specs/2026-09-23-artnet-fixture-sink-design.md
sections 4.3, 5 and 8.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from luxaeterna.logutil import ThrottledLog
from luxaeterna.pixelspan import PixelSpan
from luxaeterna.power import PowerBudget, PowerLimiter
from luxaeterna.universeset import UniverseSet

from control.timed_queue import TimedQueue

logger = logging.getLogger(__name__)

CHANNELS_PER_PIXEL = 4          # RGBW: 128 px per universe, no straddling


class ArtNetFixtureSink:
    def __init__(self, *, name: str, pixel_count: int, start_universe: int,
                 budget: PowerBudget, backend, clock: Callable[[], float],
                 lead: float = 0.0, keepalive: float = 0.25) -> None:
        self.name = name
        self._width = pixel_count * CHANNELS_PER_PIXEL
        self._set = UniverseSet(PixelSpan(pixel_count, CHANNELS_PER_PIXEL,
                                          start_universe))
        self._limiter = PowerLimiter(budget)
        self._backend = backend
        self._clock = clock
        self._lead = lead
        self._keepalive = keepalive
        self._queue = TimedQueue()
        self._cond = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None
        self._opened = False
        self._last: bytes | None = None
        self._last_sent_at: float | None = None
        self._throttle = ThrottledLog(logger)
        self._warned_width = False
        self._first_ok = False
        self._frames_sent = 0
        self._keepalives = 0
        self._send_errors = 0

    # --- tick thread -------------------------------------------------------
    def send_frame(self, frame: bytes, when: float) -> None:
        if len(frame) != self._width:
            if not self._warned_width:
                self._warned_width = True
                logger.warning("art-net output %s: frame width %d, expected "
                               "%d; dropping (logged once)", self.name,
                               len(frame), self._width)
            return
        with self._cond:
            self._queue.push(when - self._lead, bytes(frame), now=self._clock())
            self._cond.notify()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run,
                                        name=f"artnet-{self.name}", daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._send(bytes(self._width))          # leave the array dark
        try:
            self._backend.close()
        except Exception:
            logger.exception("art-net output %s: backend close failed", self.name)
        self._opened = False

    def stats(self) -> dict:
        with self._cond:
            return {"frames_sent": self._frames_sent,
                    "keepalives": self._keepalives,
                    "send_errors": self._send_errors,
                    "late": self._queue.clamped,
                    "lateness": tuple(self._queue.lateness)}

    # --- sender thread -----------------------------------------------------
    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stop:
                    return
            try:
                wake = self._service_once(self._clock())
            except Exception:
                logger.exception("art-net output %s: sender loop error; "
                                 "continuing", self.name)
                wake = self._clock() + self._keepalive
            with self._cond:
                if self._stop:
                    return
                # Re-read under the lock: a frame pushed after
                # _service_once released it must shorten this wait, or it
                # would sit until the keepalive.
                pending = self._queue.next_due()
                if pending is not None:
                    wake = min(wake, pending)
                timeout = wake - self._clock()
                if timeout > 0:
                    self._cond.wait(timeout)

    def _service_once(self, now: float) -> float:
        """Send whatever is due at `now`; return the next wake time."""
        with self._cond:
            due = self._queue.due(now)
            pending = self._queue.next_due()
        if due:
            frame, keepalive = due[-1], False
        elif (self._last is not None and self._last_sent_at is not None
              and now - self._last_sent_at >= self._keepalive):
            frame, keepalive = self._last, True
        else:
            frame = None
        if frame is not None:
            ok = self._send(frame)
            self._last, self._last_sent_at = frame, now
            if ok:
                with self._cond:
                    if keepalive:
                        self._keepalives += 1
                    else:
                        self._frames_sent += 1
        next_keepalive = (now + self._keepalive if self._last_sent_at is None
                          else self._last_sent_at + self._keepalive)
        return next_keepalive if pending is None else min(pending, next_keepalive)

    def _send(self, frame: bytes) -> bool:
        try:
            if not self._opened:
                self._backend.open()
                self._opened = True
            self._set.set_pixels(self._limiter.apply(frame))
            for universe_id, data in self._set.frames():
                self._backend.send(data, universe_id)
        except Exception as exc:
            with self._cond:
                self._send_errors += 1
            self._throttle.log(f"send:{self.name}", logging.WARNING,
                               "art-net output %s: send failed: %s",
                               self.name, exc)
            return False
        if not self._first_ok:
            self._first_ok = True
            logger.info("art-net output %s: first frame sent", self.name)
        return True
