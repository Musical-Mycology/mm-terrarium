"""python -m harness.o2_shroom -- a simulated Tuneshroom over real o2lite.

The acceptance vehicle for docs/superpowers/specs/
2026-08-12-control-o2lite-and-timed-cues-design.md: a clock-synced O2
device that joins TEST_PLAYER_NODE, drives one gesture, and displays its
frames at their declared time.

It reuses harness/shroom_client.py's ShroomClient unmodified for the
protocol surface -- that module's docstring already anticipated this, since
its transport half lives in main() precisely because o2lite replaces it.

Trap worth knowing: TestBit's `player` is a SCORED role, and
RegistrationState.join() refuses a scored role once the Bit is RUNNING. The
driver must hold in SETUP long enough for this client to join, exactly as
harness/devicelink_smoke.py's --setup-seconds already does.

Usage (needs a running Arco and PYTHONPATH=/Users/chris/projects/arco):
    python3 -m harness.o2_shroom --dev ie1 --node TEST_PLAYER_NODE
"""

from __future__ import annotations

import math

from harness.shroom_client import ShroomClient

# Degrees. TestBit._on_tilt clamps gamma to [-90, 90] and maps it onto
# cc:74, which `player` binds to aurora's hue lane.
SWEEP_DEGREES = 90.0
# Seconds for one full there-and-back sweep. Slow enough to watch the hue
# glide rather than strobe.
SWEEP_PERIOD = 8.0


def tilt_sweep(elapsed: float) -> float:
    """A deterministic ping-pong ramp over [-90, 90] degrees.

    A triangle wave rather than a sawtooth: aurora glides its hue under
    cc:74, so a wrap-around discontinuity reads as a visible snap. Same
    shape as led_smoke.py's canned cc:74 ramp, which is what proved this
    looks right.
    """
    phase = (elapsed % SWEEP_PERIOD) / SWEEP_PERIOD
    triangle = 2.0 * abs(2.0 * (phase - math.floor(phase + 0.5)))
    return SWEEP_DEGREES * (triangle - 1.0)


def build(dev: str, node: str = "TEST_PLAYER_NODE",
          sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True):
    """Construct the client and its LED backend WITHOUT opening a socket.

    Returns (client, backend). serve=False gives a record-only backend for
    headless tests, matching led_smoke.py's and room_simulator.py's
    build()/main() split.
    """
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    from harness.room_simulator import WebSimLeds

    backend = WebSimBackend(capability=shroom_capability(),
                            host=sim_host, port=sim_port, serve=serve)
    client = ShroomClient(dev, node, leds=WebSimLeds(backend))
    return client, backend


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", default="ie1")
    parser.add_argument("--node", default="TEST_PLAYER_NODE")
    parser.add_argument("--ensemble", default="arco")
    parser.add_argument("--sim-host", default="127.0.0.1")
    parser.add_argument("--sim-port", type=int, default=0)
    parser.add_argument("--tilt-hz", type=float, default=20.0)
    parser.add_argument("--no-join", action="store_true",
                        help="Send /game/hello but never /game/join, and "
                             "emit no gestures. This is what the Room "
                             "simulator needs: Control has already recorded "
                             "this dev as the bound Room before the process "
                             "is spawned, so there is no node to tap "
                             "(harness/room_simulator.py's rule, reused).")
    args = parser.parse_args()

    # Lazy, exactly like harness/arco_synth.py: this module must import with
    # no o2litepy on the path.
    from o2litepy import o2lite

    from devicelink.o2_transport import pull_args

    client, backend = build(args.dev, args.node,
                            args.sim_host, args.sim_port)
    backend.open()
    print(f"Watch the Shroom at http://{args.sim_host}:{backend.port}/")

    o2lite.initialize(args.ensemble)
    o2lite.set_services(args.dev)          # the device offers its own ie<N>

    def on_down(address, typespec, info):
        """o2litepy handler: THREE parameters, and `address` has already had
        its leading '/' stripped. Arguments are pulled in typespec order,
        not handed over as a list."""
        try:
            values = pull_args(o2lite, typespec or "")
        except Exception:
            return                          # drop the frame, never raise
        client.handle({"timestamp": o2lite.msg_timestamp,
                       "address": f"/{address}",
                       "typespec": typespec or "", "args": values})

    for kind in ("role", "leds", "release", "deny", "error"):
        o2lite.method_new(f"/{args.dev}/{kind}", None, True, on_down, None)

    while o2lite.time_get() < 0:           # block until clock sync
        o2lite.poll()
        time.sleep(0.01)
    print(f"clock synced at {o2lite.time_get():.3f}")

    o2lite.send_cmd("/game/hello", 0, "s", args.dev)
    if not args.no_join:
        o2lite.send_cmd("/game/join", 0, "ss", args.dev, args.node)

    start = o2lite.time_get()
    interval = 1.0 / args.tilt_hz
    next_tilt = start
    try:
        while not client.released:
            o2lite.poll()
            now = o2lite.time_get()
            if not args.no_join and now >= next_tilt:
                gamma = tilt_sweep(now - start)
                # Timestamps at the source (Design Rule 4): the device's own
                # synced clock reading, not Control's receipt time.
                o2lite.send("/game/tilt", now, "sf", args.dev, gamma)
                next_tilt += interval
            client.tick(now)
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"frames displayed late: {client.clamped}")
        backend.close()


if __name__ == "__main__":
    main()
