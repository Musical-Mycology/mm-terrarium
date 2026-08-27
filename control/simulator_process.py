"""SimulatorProcess: spawns and owns a Room simulator subprocess
(harness/room_simulator.py) for the Terrarium load sequence. Peer to
control/arco_process.py's ArcoProcess, minus a readiness probe -- the
simulator's own devicelink connection is retried/owned by the caller's
sequencing (see docs/superpowers/specs/2026-08-10-terrarium-visualization-
simulator-design.md section 3), not something boot() blocks on.
"""

from __future__ import annotations

import subprocess

from control.process import stop_process


class SimulatorProcess:
    def __init__(self, command: list[str], *, popen=subprocess.Popen,
                 record=None) -> None:
        self._command = command
        self._popen = popen
        # Called with the spawned pid at spawn time (control/run_record.py's
        # RunRecorder.record, threaded in by Terrarium) -- never imported
        # here, only invoked, so this module stays free of run_record.
        self._record = record
        self._process = None

    def start(self) -> None:
        self._process = self._popen(self._command)
        if self._record is not None:
            pid = getattr(self._process, "pid", None)
            if pid is not None:
                self._record(pid)

    def shutdown(self) -> None:
        """SIGTERM, then SIGKILL if that is ignored, then reap.

        Bounded via control/process.py. The simulator is ALWAYS a plain
        subprocess.Popen, whose wait() has no timeout, so before this the
        one client most likely to be slow on its way out could hang teardown
        forever.
        """
        if self._process is None:
            return
        process, self._process = self._process, None
        stop_process(process)
