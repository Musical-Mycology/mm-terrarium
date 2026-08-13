"""ArcoProcess: spawns and owns the Arco server subprocess for the
Terrarium load sequence. Real pyarco imports stay lazy (inside
_default_probe, never at module level) so the offline suite runs with
neither Arco nor pyarco present. Arco has no message-based quit
(arco/doc/server.md: the only documented shutdown is a console keypress),
so shutdown() sends SIGTERM, mirroring harness/led_smoke.py's own
_sigterm_as_keyboard_interrupt handling of itself. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5.
"""

from __future__ import annotations

import signal
import subprocess
import time


class ArcoReadyTimeout(Exception):
    """Raised when Arco doesn't report ready within the configured timeout."""


def _default_probe() -> bool:
    """Real readiness probe: lazy pyarco import, mirroring
    harness/arco_synth.py's ArcoSynthPool.start(). A bare connect attempt --
    callers needing the full ensemble use ArcoSynthPool afterward, once this
    has already confirmed a server is listening."""
    from pyarco.arco_engine import arco  # noqa: PLC0415 (lazy by design)
    try:
        arco.initialize()
        return True
    except TimeoutError:
        return False


class FakePopen:
    """In-process test double for subprocess.Popen, sibling of
    control/audio.py's FakeVoice/FakePool."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.signals: list[int] = []
        self.waited = False

    def __call__(self, command: list[str]):
        self.commands.append(command)
        return self

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def wait(self) -> None:
        self.waited = True


class ArcoProcess:
    def __init__(self, command: list[str], *, popen=subprocess.Popen,
                 probe=_default_probe, clock=time.monotonic,
                 sleep=time.sleep) -> None:
        self._command = command
        self._popen = popen
        self._probe = probe
        self._clock = clock
        self._sleep = sleep
        self._process = None

    def start(self) -> None:
        self._process = self._popen(self._command)

    def wait_ready(self, timeout: float) -> None:
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if self._probe():
                return
            self._sleep(0.2)
        raise ArcoReadyTimeout(
            f"Arco did not report ready within {timeout}s")

    def poll(self):
        """None while the server is still running, else its exit code."""
        return None if self._process is None else self._process.poll()

    def shutdown(self) -> None:
        if self._process is None:
            return
        self._process.send_signal(signal.SIGTERM)
        self._process.wait()
        self._process = None
