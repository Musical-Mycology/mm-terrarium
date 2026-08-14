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
    control/audio.py's FakeVoice/FakePool.

    BOUNDARY RULE 5 applies here with force: this must never be more
    permissive than Popen, because control/process.py's stop_process exists
    precisely to handle the case where a child does NOT do as it is told.
    What that means concretely:

      * poll() returns None while the child runs and its exit code after.
        A double whose poll() always answered would let stop_process's wait
        loop terminate instantly in every test, so the bounded-wait path
        would never be exercised at all.
      * wait(timeout=...) RAISES subprocess.TimeoutExpired while the child
        is alive, exactly as Popen does. A double that returned instead
        would let a caller believe it had reaped a process that never died.
      * send_signal on an exited child is a no-op, as Popen.send_signal is
        (it checks returncode first).
      * `ignores` models a child that does not die on a signal. Without it,
        the SIGKILL escalation has no coverage and this double would agree
        with a test that never runs the real risk. SIGKILL may be listed
        too: that models a child in uninterruptible sleep, the one real way
        SIGKILL fails to take effect promptly.
    """

    def __init__(self, *, ignores=()) -> None:
        self.commands: list[list[str]] = []
        self.kwargs: dict = {}
        self.signals: list[int] = []
        self.waited = False
        self.returncode = None
        self._ignores = set(ignores)

    def __call__(self, command: list[str], **kwargs):
        self.commands.append(command)
        self.kwargs = kwargs
        return self

    def poll(self):
        return self.returncode

    def send_signal(self, sig: int) -> None:
        if self.returncode is not None:
            return                       # Popen.send_signal no-ops after exit
        self.signals.append(sig)
        if sig not in self._ignores:
            self.returncode = -sig

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            raise subprocess.TimeoutExpired(
                self.commands[-1] if self.commands else "fake", timeout)
        return self.returncode


def pty_popen(command: list[str]):
    """A subprocess.Popen work-alike that gives the child a real CONTROLLING
    TERMINAL, so Arco's curses init can open /dev/tty.

    Why this exists: a plain Popen whose stdio is a pipe or a socket fails
    with "Could not open /dev/tty. Initialization Failed!", after which
    wait_ready times out into a clean BootFailure. That makes the whole
    stack unbootable from CI, cron, or any agent-driven run, and `script`
    does not rescue a process whose stdio is a socket.

    pty.fork() differs from Popen in exactly the way that matters: the child
    calls setsid() and makes the pty slave its controlling terminal, which
    is what makes /dev/tty resolvable at all.

    Two details are load-bearing, and each one costs a SILENT failure when
    omitted -- curses exits without writing a diagnostic, so the process
    just dies looking like a hard incompatibility:

      * TERM must be set, or curses has no terminal description.
      * The pty needs a non-zero window size. A fresh pty is 0x0 and curses
        bails against it. Verified 2026-08-14: without the TIOCSWINSZ below
        this returns zero bytes and looks like a total failure; with it Arco
        reaches its main menu normally.

    Opt-in. ArcoProcess still defaults to subprocess.Popen, so an
    interactive venue run is completely unaffected.
    """
    import fcntl                             # noqa: PLC0415 (lazy: POSIX-only)
    import os
    import pty
    import struct
    import termios

    pid, fd = pty.fork()
    if pid == 0:                             # child: never returns
        os.environ.setdefault("TERM", "xterm-256color")
        try:
            os.execv(command[0], list(command))
        except Exception:                    # noqa: BLE001 (about to _exit)
            os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    return _PtyProcess(pid, fd)


class _PtyProcess:
    """The three-method slice of subprocess.Popen that ArcoProcess actually
    uses (poll / send_signal / wait), over a pty.fork()ed child.

    Drains the master fd on poll() and wait(): Arco is a curses app
    redrawing continuously, so an undrained pty buffer fills and blocks the
    server on its own screen writes. Draining is not optional bookkeeping
    here -- it is what keeps the process alive.
    """

    def __init__(self, pid: int, fd: int) -> None:
        self.pid = pid
        self._fd = fd
        self.returncode = None
        self.output = bytearray()

    def _drain(self) -> None:
        import os
        import select
        while True:
            ready, _, _ = select.select([self._fd], [], [], 0)
            if not ready:
                return
            try:
                chunk = os.read(self._fd, 65536)
            except OSError:                  # slave closed: child is gone
                return
            if not chunk:
                return
            self.output += chunk

    def poll(self):
        import os
        if self.returncode is not None:
            return self.returncode
        self._drain()
        pid, status = os.waitpid(self.pid, os.WNOHANG)
        if pid == 0:
            return None
        self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def send_signal(self, sig: int) -> None:
        import os
        if self.returncode is None:
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:       # already gone; nothing to signal
                pass

    def write_console(self, keys: str) -> None:
        """Type into Arco's curses console.

        Arco's console keypresses are its ONLY control surface -- its own
        doc/server.md records no message-based equivalent, which is why
        ArcoProcess.shutdown() resorts to SIGTERM. Owning the pty master is
        what makes them reachable at all, so this is the one capability the
        pty spawn adds beyond headless startup.

        Callers beware: (S)tart/Stop is a TOGGLE and Arco exposes no way to
        read which state it is in, so sending it blindly is as likely to
        stop audio as to start it. Nothing calls this by default.
        """
        import os
        if self.returncode is None:
            os.write(self._fd, keys.encode())

    def wait(self, timeout: float = 5.0):
        import os
        import time as _time
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if self.poll() is not None:
                break
            _time.sleep(0.05)
        else:
            # SIGTERM was ignored or the app hung on its way out. A venue box
            # rebooting into a still-running Arco would fail to bind its
            # ports, so escalate rather than return with the child alive.
            self.send_signal(9)
            # SIGKILL is delivered asynchronously: polling immediately can
            # still see the child unreaped and would return None, i.e. claim
            # "still running" for a process that is already dying.
            kill_deadline = _time.monotonic() + 5.0
            while _time.monotonic() < kill_deadline:
                if self.poll() is not None:
                    break
                _time.sleep(0.02)
        try:
            os.close(self._fd)
        except OSError:
            pass
        return self.returncode


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
