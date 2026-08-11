"""SimulatorProcess: spawns and owns a Room simulator subprocess
(harness/room_simulator.py) for the Terrarium load sequence. Peer to
control/arco_process.py's ArcoProcess, minus a readiness probe -- the
simulator's own devicelink connection is retried/owned by the caller's
sequencing (see docs/superpowers/specs/2026-08-10-terrarium-visualization-
simulator-design.md section 3), not something boot() blocks on.
"""

from __future__ import annotations

import signal
import subprocess


class SimulatorProcess:
    def __init__(self, command: list[str], *, popen=subprocess.Popen) -> None:
        self._command = command
        self._popen = popen
        self._process = None

    def start(self) -> None:
        self._process = self._popen(self._command)

    def shutdown(self) -> None:
        if self._process is None:
            return
        self._process.send_signal(signal.SIGTERM)
        self._process.wait()
        self._process = None
