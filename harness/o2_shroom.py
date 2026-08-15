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
import os

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


def parent_is_gone(expected_ppid, getppid=os.getppid) -> bool:
    """True once this process's parent is no longer the one that spawned it.

    The Room simulator is spawned by harness/terrarium_boot.py and, with
    --no-join, never exits on its own: main()'s loop below waits for a
    /release that only a live Control sends. So a Terrarium that dies
    without running its shutdown leaves this process running forever, and
    o2litepy reconnects it to the NEXT Arco that starts (o2lite.py:912
    connects whenever _tcp_socket is None, and _id_handler at :601
    re-announces every service on connect). There it claims this same dev
    name, and O2 refuses the new run's own simulator with "not from service
    provider" (o2/src/bridge.cpp:231-237) -- silently, since /_o2/*/sv is
    fire-and-forget. See docs/superpowers/specs/
    2026-08-14-room-simulator-service-collision-design.md.

    Compares against the pid the parent stamped in rather than watching
    getppid() for a change: if the parent died before this process read its
    argv, getppid() is ALREADY 1 and a change detector would wait forever.
    Comparison against a recorded value is correct in either order.

    expected_ppid None means the caller did not ask for this guard -- the
    default for a hand-run device -- and it never fires.
    """
    return expected_ppid is not None and getppid() != expected_ppid


def service_conflict(o2lite, dev: str, *, verify=None):
    """Return a diagnostic string if `dev` is not ours, else None.

    Pure apart from the injected `verify`, so the message this prints is
    testable without an O2 hub. `verify` defaults to
    devicelink.o2_transport.verify_service_ownership, imported lazily
    because that module resolves its own o2litepy-free contract and this
    one must stay importable with no o2litepy present.

    Why this exists: a device whose service announcement O2 refused is
    indistinguishable from a healthy one. Both clock-sync, both print a
    watch URL, and Control sees no error because the hub routes its frames
    successfully -- to whoever won the service. See docs/superpowers/specs/
    2026-08-14-room-simulator-service-collision-design.md.
    """
    if verify is None:
        from devicelink.o2_transport import verify_service_ownership
        verify = verify_service_ownership
    if verify(o2lite, dev):
        return None
    return (f"FATAL: service {dev!r} is not routed back to this process. "
            f"Another process on the Arco hub already offers it, and O2 "
            f"refuses a second claimant silently "
            f"(o2/src/bridge.cpp:231-237). Nothing addressed to "
            f"/{dev}/* will ever arrive here. Look for a stale "
            f"'python -m harness.o2_shroom --dev {dev}' and kill it.")


def _gestures_ready(client) -> bool:
    """True once Control's granted-role reply has actually reached this
    client, i.e. once ShroomClient._on_role() has set client.config (see
    harness/shroom_client.py) -- and therefore once the join it responds to
    has been processed by GameServer.join().

    Why this matters: main() sends the join over TCP (o2lite.send_cmd) but
    gestures over UDP (o2lite.send, the default), and UDP can overtake TCP.
    Without gating, the first tilt can reach Control before the join has
    been handled, and GameServer.data() correctly refuses it as "device not
    registered" -- a spurious error on every run, not a real fault. Gating
    gesture emission on this instead of on 'join sent' closes that race:
    there is nothing to overtake once the reply has already arrived.

    --no-join callers (the Room simulator) never send a join and so never
    get a role -- this would return False for them forever. That is
    correct, but it must not be the ONLY thing stopping their gestures:
    main() also short-circuits on args.no_join first, exactly as it did
    before this gate existed, so a --no-join run never even calls this."""
    return client.config is not None


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
                            host=sim_host, port=sim_port, serve=serve,
                            label=dev)
    client = ShroomClient(dev, node, leds=WebSimLeds(backend))
    return client, backend


def main() -> None:
    import argparse
    import sys
    import time

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", default="ie1")
    parser.add_argument("--node", default="TEST_PLAYER_NODE")
    parser.add_argument("--ensemble", default="arco")
    parser.add_argument("--sim-host", default="127.0.0.1")
    parser.add_argument("--sim-port", type=int, default=0)
    parser.add_argument("--tilt-hz", type=float, default=20.0)
    parser.add_argument("--join-retry", type=float, default=0.0,
                        help="Re-send /game/join every N seconds until a "
                             "role, deny or error comes back. 0 (default) "
                             "keeps the original send-once behavior. A join "
                             "sent before Control is listening is simply "
                             "lost -- there is no queue behind it -- so "
                             "without this a device that powers on first "
                             "sits silent forever. Also what lets a device "
                             "sync its clock BEFORE Control resets Arco, "
                             "which is the only reliable ordering while the "
                             "upstream /host/clear defect stands (see "
                             "terrarium_boot's --arco-start-audio).")
    parser.add_argument("--control-horizon", type=float, default=None,
                        help="The horizon Control was run with (its "
                             "--horizon). Used ONLY to turn this device's "
                             "observed lateness into absolute end-to-end "
                             "latency in the exit summary -- the device "
                             "gains no scheduling opinion from it. Omit to "
                             "report signed lateness instead.")
    parser.add_argument("--samples-out", default=None,
                        help="Write the raw per-frame lateness samples to "
                             "this path as JSON, for python -m "
                             "harness.sync_bench.")
    parser.add_argument("--no-join", action="store_true",
                        help="Send /game/hello but never /game/join, and "
                             "emit no gestures. This is what the Room "
                             "simulator needs: Control has already recorded "
                             "this dev as the bound Room before the process "
                             "is spawned, so there is no node to tap "
                             "(harness/room_simulator.py's rule, reused).")
    parser.add_argument("--exit-with-parent", type=int, default=None,
                        metavar="PID",
                        help="Exit as soon as this process's parent is no "
                             "longer PID. harness/terrarium_boot.py passes "
                             "its own pid so a Room simulator cannot outlive "
                             "the Terrarium that spawned it and steal its dev "
                             "name from the next run.")
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
            # Mirrors devicelink/o2_transport.py's _on_message diagnostic,
            # but print rather than logging: this module has no logging
            # setup, and every other operator-facing line here (the watch
            # URL, the clock-synced line, the frames-displayed-late count)
            # is already print, so that is what a person running this tool
            # will actually see.
            print(f"dropping /{address}: unreadable arguments")
            return                          # drop the frame, never raise
        client.handle({"timestamp": o2lite.msg_timestamp,
                       "address": f"/{address}",
                       "typespec": typespec or "", "args": values})

    for kind in ("role", "leds", "release", "deny", "error"):
        o2lite.method_new(f"/{args.dev}/{kind}", None, True, on_down, None)

    while o2lite.time_get() < 0:           # block until clock sync
        if parent_is_gone(args.exit_with_parent):
            print("parent is gone; exiting before clock sync")
            backend.close()
            return
        o2lite.poll()
        time.sleep(0.01)
    print(f"clock synced at {o2lite.time_get():.3f}")

    # The service announcement went out at set_services time and was never
    # acknowledged. Check it actually took before serving a canvas that
    # would otherwise stay dark for the whole run with no explanation.
    problem = service_conflict(o2lite, args.dev)
    if problem is not None:
        print(problem, file=sys.stderr)
        backend.close()
        raise SystemExit(1)

    o2lite.send_cmd("/game/hello", 0, "s", args.dev)
    if not args.no_join:
        o2lite.send_cmd("/game/join", 0, "ss", args.dev, args.node)

    start = o2lite.time_get()
    interval = 1.0 / args.tilt_hz
    # Deferred rather than started at `start`: gestures are held off until
    # _gestures_ready(client) -- see that function's docstring for why --
    # so the first tilt should be scheduled for the moment the role
    # actually arrives, not backdated to loop start (which would fire a
    # burst of "overdue" tilts back-to-back the instant the gate opens).
    next_tilt = None
    # The join reply is asynchronous -- it only arrives once the loop below
    # polls it in -- so noticing a deny/error has to happen inside the loop,
    # not right after send_cmd. Printed once each: without this, a refused
    # join looks identical to a working one that simply has no frames yet
    # -- a blank browser and no explanation.
    deny_printed = False
    error_printed = False
    # Only ever set when --join-retry is on, so the default path still sends
    # exactly one join.
    next_join = (o2lite.time_get() + args.join_retry
                 if args.join_retry > 0 and not args.no_join else None)
    joins_sent = 1
    try:
        while not client.released:
            if parent_is_gone(args.exit_with_parent):
                print("parent is gone; exiting")
                break
            o2lite.poll()
            now = o2lite.time_get()
            if next_join is not None and now >= next_join:
                if client.config is not None or client.last_deny is not None \
                        or client.last_error is not None:
                    next_join = None       # Control answered; stop retrying
                else:
                    # hello as well as join, every time. Both were sent
                    # before Control existed and BOTH were dropped by Arco
                    # ("service was not found"), and /game/hello is what puts
                    # this device in the DevicePool -- a join from a device
                    # Control has never heard of goes nowhere. Retrying only
                    # the join reconnects nothing.
                    o2lite.send_cmd("/game/hello", 0, "s", args.dev)
                    o2lite.send_cmd("/game/join", 0, "ss", args.dev, args.node)
                    joins_sent += 1
                    next_join = now + args.join_retry
                    if joins_sent % 5 == 0:
                        print(f"still waiting on a role after {joins_sent} "
                              f"joins -- is Control up and in SETUP?")
            if not deny_printed and client.last_deny is not None:
                reason, hint = client.last_deny
                print(f"JOIN DENIED: {reason} ({hint})")
                deny_printed = True
                # A denied join never gets a role, so _gestures_ready(client)
                # can never become true and there is nothing left for this
                # loop to do -- stop instead of silently polling forever
                # with no gestures and no further explanation.
                break
            if not error_printed and client.last_error is not None:
                context, message = client.last_error
                print(f"ERROR from Control: {context}: {message}")
                error_printed = True
            if not args.no_join and _gestures_ready(client):
                if next_tilt is None:
                    next_tilt = now       # first tilt fires now the role is in
                    # Say so explicitly. Until this line appears, a silent
                    # browser is indistinguishable from a role that never
                    # arrived, and the two want completely different fixes.
                    print(f"role granted after {joins_sent} join(s); "
                          f"gestures starting at {now:.3f}", flush=True)
                if now >= next_tilt:
                    gamma = tilt_sweep(now - start)
                    # Timestamps at the source (Design Rule 4): the device's
                    # own synced clock reading, not Control's receipt time.
                    o2lite.send("/game/tilt", now, "sf", args.dev, gamma)
                    next_tilt += interval
            client.tick(now)
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"frames displayed late: {client.clamped}")
        _report_latency(client, args.control_horizon, args.samples_out)
        backend.close()


def _report_latency(client, control_horizon, samples_out) -> None:
    """Print the measured distribution, not just the clamp count.

    The clamp count alone cannot size a horizon: 762-of-820 clamped says the
    60 ms default is too small and nothing about what would be big enough.
    """
    import json

    from harness.sync_bench import format_report, summarise

    samples = client.lateness
    if not samples:
        print("no timed frames observed -- nothing to summarise")
        return

    if samples_out:
        with open(samples_out, "w", encoding="utf-8") as handle:
            json.dump(samples, handle)
        print(f"wrote {len(samples)} lateness samples to {samples_out}")

    if control_horizon is None:
        # Signed lateness through summarise() would call a frame arriving
        # early "error", so say plainly that this is the raw spread and that
        # --control-horizon is what turns it into latency.
        print(f"lateness spread (no --control-horizon given): "
              f"{min(samples) * 1000.0:.1f} .. {max(samples) * 1000.0:.1f} ms")
        return

    # Absolute end-to-end latency: Control stamped `when = t + horizon`, so
    # adding the horizon back to (now - when) recovers (now - t).
    latencies = [control_horizon + s for s in samples]
    print(format_report(summarise(latencies),
                        label=f"end-to-end cue latency "
                              f"(horizon {control_horizon * 1000:.0f} ms):"))
    if client.clamped:
        print(f"  WARNING: {client.clamped} frame(s) clamped, so this sample "
              f"is CENSORED at {control_horizon * 1000:.0f} ms -- the real "
              f"tail is longer than 'worst' reports. Re-run with a larger "
              f"--horizon on Control.")


if __name__ == "__main__":
    main()
