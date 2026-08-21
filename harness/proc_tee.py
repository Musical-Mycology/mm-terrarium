"""ProcTee: fan one supervised child's stdout three ways.

To its own log file, so a run leaves evidence behind and a failure summary
has something to quote. To the operator's terminal with a short prefix, so
an interactive run reads as one narrative rather than three silent
processes. And to a set of marker events, so the supervisor can wait on
what actually happened instead of sleeping and hoping.

One daemon thread per child, reading line by line. Children are spawned
with -u and stderr folded into stdout, so there is exactly one stream per
process and it is not block-buffered.

The thread is JOINED, bounded, after its process stops. A device prints its
whole lateness summary on the way out, and reading to EOF is what puts that
in the log rather than cutting it off mid-write.
"""

from __future__ import annotations

import sys
import threading
import time


class ProcTee:
    """Reads `stream` to EOF on a daemon thread."""

    def __init__(self, name: str, stream, log_path: str, *, markers,
                 echo: bool = False, out=None, on_line=None) -> None:
        self.name = name
        self._on_line = on_line
        self._stream = stream
        self._log_path = log_path
        self._echo = echo
        self._out = out if out is not None else sys.stderr
        self._events = {marker: threading.Event() for marker in markers}
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._pump, daemon=True,
                                        name=f"tee-{self.name}")
        self._thread.start()

    def _pump(self) -> None:
        with open(self._log_path, "w", encoding="utf-8", buffering=1) as log:
            for raw in self._stream:
                line = raw.rstrip("\n")
                log.write(line + "\n")
                if self._echo:
                    self._out.write(f"[{self.name}] {line}\n")
                    flush = getattr(self._out, "flush", None)
                    if flush is not None:
                        flush()
                with self._lock:
                    self._lines.append(line)
                for marker, event in self._events.items():
                    if marker in line:
                        event.set()
                if self._on_line is not None:
                    self._on_line(line)

    def seen(self, marker: str) -> bool:
        return self._events[marker].is_set()

    def wait_for(self, marker: str, timeout: float, clock=time.monotonic,
                 sleep=time.sleep) -> bool:
        """True once `marker` has been seen, False once `timeout` elapses.

        Polls rather than using Event.wait(timeout) so a test can inject a
        clock and spend no real time. Bounded by construction: this is the
        function that turns the documented headless clock-sync defect from
        a hang into a named failure.
        """
        deadline = clock() + timeout
        event = self._events[marker]
        while True:
            if event.is_set():
                return True
            if clock() >= deadline:
                return False
            sleep(0.05)

    def join(self, timeout: float = 2.0) -> None:
        """Wait for the reader to reach EOF, so the child's last words land
        in the log. Bounded: a child whose stdout never closes must not hold
        teardown open."""
        if self._thread is not None:
            self._thread.join(timeout)

    def tail(self, lines: int = 20) -> list[str]:
        with self._lock:
            return list(self._lines[-lines:])
