"""python -m harness.run_websocket_stack -- host Control+GameServer+Console
over the websocket devicelink, with the Terrarium Console on for monitoring
and controlling whatever devices connect.

WHAT IT SUPERVISES. Nothing new: harness/terrarium_boot.py already does the
whole job in one process (--transport websocket is its own default, and
--console-port turns the Console on). This script is a thin default-setting
wrapper around it -- TEST room, TestBit, a fixed Console port -- so a run
means one short command instead of memorizing terrarium_boot's full flag
set. All real sequencing logic stays in terrarium_boot.py; this delegates
to its main() so it stays correct as that module evolves.

STILL NEEDS ARCO. --transport only picks the *device* connection (websocket
vs o2lite); build()'s room_audio is unconditionally on and backed by a real
ArcoSynthPool regardless of transport (see terrarium_boot.build()'s own
docstring -- "there is no --audio-style opt-out"), so this script spawns
Arco the same way harness/run_stack.py's o2lite path does: --arco-pty (a
plain Popen's piped stdio fails Arco's curses /dev/tty init), a settle
pause, and a raised ready-timeout for the slow first probe. Only the device
path differs from run_stack.py -- websocket here, o2lite there.

Binds 0.0.0.0 by default -- reachable from this machine's LAN, not just
localhost -- since the whole point of hosting devices is usually to let
something other than this same process connect. The Console is
UNAUTHENTICATED (trusted-LAN only, per terrarium_boot's own --console-port
help), so that is a deliberate trade of convenience for exposure: anything
on the LAN can open and control it. Pass --host 127.0.0.1 to go back to
loopback-only.

On startup terrarium_boot itself prints:
  Terrarium Console at http://<host>:<console-port>/   -- open this to
    monitor and control connected devices (load/run/abort Bits, load
    Rooms, fire triggers).
  DeviceLink listening on ws://<host>:<port>/ws          -- what real
    hardware or browser device clients connect to.
These are printed verbatim, so a 0.0.0.0 bind shows "ws://0.0.0.0:.../ws"
-- not an address anything else can dial. This script detects and prints
this machine's actual outbound LAN address separately so there is always
one line you can actually type into another device.

Runs until Ctrl-C.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time


def _detect_lan_ip() -> str | None:
    """This machine's outbound-facing address, i.e. the one another device
    on the LAN would use to reach it. The UDP "connect" here sends no
    packets (UDP has no handshake) -- it only asks the kernel to pick the
    local address it would route 8.8.8.8 through, which is a well-known
    trick for finding the non-loopback interface without enumerating and
    guessing among possibly several."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        return None


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0",
                    help="Default 0.0.0.0: reachable from this machine's "
                         "LAN. Pass 127.0.0.1 for loopback-only.")
    ap.add_argument("--console-port", type=int, default=8080,
                    help="Terrarium Console port.")
    ap.add_argument("--port", type=int, default=8771,
                    help="DeviceLink websocket port.")
    ap.add_argument("--room", default="TEST", metavar="NAME",
                    help="Room to load (see --config's rooms catalog).")
    ap.add_argument("--bit", default="TestBit",
                    help="Bit to run (see --list-bits on terrarium_boot).")
    ap.add_argument("--config", default="terrarium.toml", metavar="PATH")
    ap.add_argument("--log-dir", default=None, metavar="PATH",
                    help="Where Arco's console output is captured. "
                         "Default: runs/<timestamp>.")
    return ap.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)

    from harness.arco_paths import ARCO_PYTHONPATH, ensure_o2litepy

    if not ensure_o2litepy():
        print(f"run_websocket_stack needs o2litepy and could not find it, "
              f"even after falling back to {ARCO_PYTHONPATH}. Is the arco "
              f"checkout present there? Otherwise re-run with PYTHONPATH "
              f"pointing at it.", file=sys.stderr)
        raise SystemExit(1)

    log_dir = args.log_dir or os.path.join("runs",
                                           time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(log_dir, exist_ok=True)
    print(f"logs: {log_dir}")

    if args.host in ("0.0.0.0", "::"):
        lan_ip = _detect_lan_ip()
        if lan_ip is not None:
            print(f"Reachable on this LAN at: ws://{lan_ip}:{args.port}/ws "
                  f"(Console: http://{lan_ip}:{args.console_port}/)")
        else:
            print("Could not detect a LAN address (no route out) -- "
                  "devices on other machines may not reach this host.",
                  file=sys.stderr)

    from harness import terrarium_boot

    # terrarium_boot.main() reads sys.argv directly (no argv parameter),
    # so this is how run_stack.py's own subprocess invocation is mirrored
    # in-process: fixed --transport websocket (the whole point of this
    # script) plus this wrapper's flags, then delegate.
    sys.argv = [
        "terrarium_boot",
        "--transport", "websocket",
        "--host", args.host,
        "--port", str(args.port),
        "--console-port", str(args.console_port),
        "--room", args.room,
        "--bit", args.bit,
        "--config", args.config,
        "--hold",
        "--arco-pty",
        "--arco-log", os.path.join(log_dir, "arco.log"),
        "--arco-settle-seconds", "5.0",
        "--arco-ready-timeout", "60.0",
    ]
    terrarium_boot.main()


if __name__ == "__main__":
    main()
