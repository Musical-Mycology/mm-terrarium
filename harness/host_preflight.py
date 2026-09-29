"""Fail-fast host checks run before anything spawns Arco.

On Linux, O2 (inside Arco) advertises its `_o2proc._tcp` mDNS service through
the Avahi client API, which needs a running avahi-daemon. Without one Arco
logs "Avahi failed to create client: Daemon not running", never advertises,
and the readiness probe times out 60 s later with a message that names
nothing. check_avahi() turns that into an immediate, located refusal. macOS
has Bonjour built in, so it is skipped there.

The daemon's socket is the probe (init-system agnostic: it works with or
without systemd). On WSL, a NAT networking mode is only a warning: simulated
devices work, real LAN devices cannot reach Arco.

Pure stdlib; every host fact is an injectable parameter for tests.
"""
from __future__ import annotations

import os
import subprocess
import sys

AVAHI_SOCKET = "/run/avahi-daemon/socket"
OSRELEASE_PATH = "/proc/sys/kernel/osrelease"
# Set once a parent process has run the checks, so run_stack's child
# (terrarium_boot) does not repeat them or print the warning twice.
DONE_ENV = "MM_HOST_PREFLIGHT_DONE"

AVAHI_MESSAGE = (
    "avahi-daemon is not running: Arco can't advertise itself over mDNS, so "
    "it will never report ready. Fix: sudo apt install avahi-daemon && "
    "sudo systemctl enable --now avahi-daemon "
    "(on WSL, systemd must be enabled in /etc/wsl.conf; see "
    "docs/MM_TERRARIUM.md, Linux / WSL host setup)"
)
NAT_WARNING = (
    "WARNING: WSL is in NAT networking mode. Simulated devices work, but "
    "real devices (ESP32 dev shrooms, phones on the LAN) cannot reach Arco "
    "behind WSL NAT. See docs/MM_TERRARIUM.md, Linux / WSL host setup."
)


class HostPreflightError(RuntimeError):
    """A host precondition Arco needs is missing."""


def check_avahi(*, platform: str = sys.platform,
                socket_path: str = AVAHI_SOCKET) -> None:
    """Raise HostPreflightError on Linux when the Avahi socket is absent."""
    if not platform.startswith("linux"):
        return
    if not os.path.exists(socket_path):
        raise HostPreflightError(AVAHI_MESSAGE)


def _read_osrelease(path: str = OSRELEASE_PATH) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def is_wsl(*, osrelease: str | None = None, env=None) -> bool:
    env = os.environ if env is None else env
    if env.get("WSL_DISTRO_NAME"):
        return True
    return bool(osrelease) and "microsoft" in osrelease.lower()


def wsl_nat_warning(*, wsl: bool, runner=subprocess.run) -> str | None:
    """The NAT warning text, or None (not WSL, not NAT, wslinfo unusable)."""
    if not wsl:
        return None
    try:
        proc = runner(["wslinfo", "--networking-mode"], capture_output=True,
                      text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return NAT_WARNING if (proc.stdout or "").strip().lower() == "nat" else None


def run_preflight(*, platform: str = sys.platform,
                  socket_path: str = AVAHI_SOCKET, wsl: bool | None = None,
                  runner=subprocess.run, env=None) -> None:
    """Entry-point hook: exit 1 with the message if Avahi is missing,
    print the WSL NAT warning to stderr. Runs once per process tree."""
    env = os.environ if env is None else env
    if env.get(DONE_ENV):
        return
    try:
        check_avahi(platform=platform, socket_path=socket_path)
    except HostPreflightError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    if wsl is None:
        wsl = platform.startswith("linux") and is_wsl(
            osrelease=_read_osrelease(), env=env)
    warning = wsl_nat_warning(wsl=wsl, runner=runner)
    if warning:
        print(warning, file=sys.stderr)
    env[DONE_ENV] = "1"
