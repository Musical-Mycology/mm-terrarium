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
import queue

from harness import markers
from harness.shroom_client import LED_CHANNELS, ShroomClient
from harness.signals import sigterm_as_keyboard_interrupt

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


# Seconds after the operator's last drag-tilt before the synthetic sweep
# resumes. Long enough that hue does not snap back mid-exploration, short
# enough that an unattended run still animates.
SWEEP_RESUME_SECONDS = 5.0

# Bound on browser gestures queued between ticks. Generous: the page
# rate-bounds tilts to 20 Hz and the loop drains every ~5 ms.
INPUT_QUEUE_MAX = 64


def enqueue_input(q: "queue.Queue", msg: dict) -> None:
    """Queue one browser gesture, dropping the OLDEST on overflow.

    Runs on WebSimBackend's websocket handler thread, so it must never
    block; drop-oldest keeps the freshest gestures, matching the
    drop-not-queue rule frame relay already follows elsewhere."""
    while True:
        try:
            q.put_nowait(msg)
            return
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass


def drain_gestures(q: "queue.Queue", send, dev: str, now: float):
    """Translate every queued browser gesture into a /game/* send.

    `send` has o2lite.send's signature: send(address, time, typespec,
    *args). Gestures are stamped `now` -- the caller's o2lite clock
    reading -- because the whole simulator process is the device, so
    this IS the source stamp (Design Rule 4); the browser hop happened
    inside the device. Returns `now` if any tilt went out (the caller
    suspends its synthetic sweep against it), else None. Malformed
    entries are dropped with one diagnostic per drain, mirroring the
    engine's drop-this-frame rule."""
    tilted = None
    complained = False
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            return tilted
        kind = msg.get("type") if isinstance(msg, dict) else None
        try:
            if kind == "tap":
                count = max(1, int(msg.get("count", 1)))
                send("/game/tap", now, "sffi", dev, 1.0, 50.0, count)
            elif kind == "tilt":
                gamma = max(-90.0, min(90.0, float(msg["gamma"])))
                send("/game/tilt", now, "sf", dev, gamma)
                tilted = now
            else:
                raise ValueError(kind)
        except (KeyError, TypeError, ValueError):
            if not complained:
                print(f"dropping operator gesture {msg!r}")
                complained = True


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
    return (f"{markers.DEVICE_SERVICE_CONFLICT} {dev!r} is not routed back "
            f"to this process. Another process on the Arco hub already "
            f"offers it, and O2 refuses a second claimant silently "
            f"(o2/src/bridge.cpp:231-237). Nothing addressed to "
            f"/{dev}/* will ever arrive here. Look for a stale "
            f"'python -m harness.o2_shroom --dev {dev}' and kill it.")


def reconnect_recheck(o2lite, dev: str, previous_bridge_id, *, verify=None):
    """If o2lite's bridge id has changed since the last check, re-run the
    service-ownership check and return (current_bridge_id, problem).

    o2litepy auto-reconnects silently and stamps a new bridge_id on
    reconnect; a reconnect that lands after this device's own service
    announcement was lost leaves it clock-synced against the OLD hub
    forever, with the hub dropping every reply as "service was not
    found" (measured 2026-08-20: fifteen dropped Control replies while
    the device saw pure silence). The one-shot startup check
    (service_conflict) cannot catch this because it only runs once,
    before any reconnect has happened.

    `problem` is None when the bridge id is unchanged (nothing to do) or
    when the re-check passes. `verify` defaults to
    devicelink.o2_transport.verify_service_ownership, imported lazily for
    the same reason as service_conflict's `verify`.
    """
    current = getattr(o2lite, "bridge_id", previous_bridge_id)
    if current == previous_bridge_id:
        return previous_bridge_id, None

    print(f"reconnected to the hub (bridge id {previous_bridge_id} -> "
          f"{current}); re-verifying service")

    if verify is None:
        from devicelink.o2_transport import verify_service_ownership
        verify = verify_service_ownership

    # A reconnect can land on a hub that is busy (e.g. a cold audio
    # open), and Task 2 established that a blocked hub needs the resend
    # window to be distinguished from a genuine conflict -- so this call
    # passes timeout/resend_interval explicitly rather than relying on
    # verify_service_ownership's tight defaults. The STARTUP check (in
    # service_conflict) keeps those tight defaults: it runs after clock
    # sync, when the hub is provably alive.
    if verify(o2lite, dev, timeout=10.0, resend_interval=2.0):
        return current, None

    problem = (f"{markers.DEVICE_SERVICE_CONFLICT} {dev!r} is not routed "
               f"back to this process after reconnecting to the hub "
               f"(bridge id {previous_bridge_id} -> {current}). Another "
               f"process has likely claimed it, and O2 refuses a second "
               f"claimant silently (o2/src/bridge.cpp:231-237). Nothing "
               f"addressed to /{dev}/* will ever arrive here.")
    return current, problem


def join_stall_hint(dev: str) -> str:
    """The tail of the message printed every 5 unanswered joins (the
    caller prepends "N joins unanswered. ").

    The old wording ("is Control up and in SETUP?") pointed at Control
    even on a run where Control was perfectly healthy: the actual cause,
    measured 2026-08-20, was this device's own service announcement
    being lost, which the hub logs as "service was not found" and never
    tells the device about. Naming that -- and where to look for it --
    turns a guess into an instruction.
    """
    return (f"Either Control is not up yet, or this device's service "
            f"announcement was lost (check o2debug.log on the hub for "
            f'"/{dev}/... service was not found").')


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
          serve: bool = True, room_type: str | None = None,
          fixture: str | None = None,
          input_queue: "queue.Queue | None" = None):
    """Construct the client and its LED backend WITHOUT opening a socket.

    Returns (client, backend). serve=False gives a record-only backend for
    headless tests, matching led_smoke.py's and room_simulator.py's
    build()/main() split.

    room_type, when given, renders that ROOM's ONE named fixture instead of
    a Tuneshroom's surface -- fixture is then required. This is the
    --no-join path, where this module stands in for
    harness/room_simulator.py, once per fixture, on the o2lite transport.

    input_queue, when given, receives every gesture the browser page sends
    back; see drain_gestures.
    """
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    from harness.room_simulator import WebSimLeds

    if room_type is None:
        capability = shroom_capability(surface_id=dev)
        channels = LED_CHANNELS
    else:
        if fixture is None:
            raise ValueError("room_type requires fixture")
        from control.room_profile import room_profile
        from control.rooms import RoomType
        from harness.room_surface import to_fixture_capability

        profile = room_profile(RoomType[room_type])
        capability = to_fixture_capability(profile, fixture)
        channels = capability.pixel_count * 3

    on_input = (None if input_queue is None
                else lambda msg: enqueue_input(input_queue, msg))
    backend = WebSimBackend(capability=capability,
                            host=sim_host, port=sim_port, serve=serve,
                            label=dev, on_input=on_input)

    def _on_role(config: dict) -> None:
        # Where client.config is first set. A granted role whose
        # light_manifest declares no instruments (TestBit's `jammer`, on
        # purpose) renders a black canvas that is otherwise
        # indistinguishable from a broken one -- reported as a failure
        # once already because nothing said this was expected.
        if not (config.get("light_manifest") or {}).get("instruments"):
            print("role has no light declaration -- canvas stays dark "
                  "by design")

    client = ShroomClient(dev, node, leds=WebSimLeds(backend, channels),
                          on_role=_on_role, expected_channels=channels)
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
    parser.add_argument("--room-type", default=None,
                        help="Render this RoomType's surface instead of a "
                             "Tuneshroom's. Only meaningful with --no-join, "
                             "which is how this module serves as the Room "
                             "simulator on the o2lite path.")
    parser.add_argument("--fixture", default=None,
                        help="Which Room fixture to render. Required "
                             "together with --room-type.")
    parser.add_argument("--exit-with-parent", type=int, default=None,
                        metavar="PID",
                        help="Exit as soon as this process's parent is no "
                             "longer PID. harness/terrarium_boot.py passes "
                             "its own pid so a Room simulator cannot outlive "
                             "the Terrarium that spawned it and steal its dev "
                             "name from the next run.")
    args = parser.parse_args()

    # control/simulator_process.py shuts this process down with SIGTERM when
    # it is playing the Room simulator, and finally blocks do not run on a
    # bare SIGTERM -- so without this the exit lateness report and the
    # backend shutdown below are simply lost.
    sigterm_as_keyboard_interrupt()

    # Lazy, exactly like harness/arco_synth.py: this module must import with
    # no o2litepy on the path.
    from o2litepy import o2lite

    from devicelink.o2_transport import pull_args

    operator_input = queue.Queue(maxsize=INPUT_QUEUE_MAX)
    client, backend = build(args.dev, args.node,
                            args.sim_host, args.sim_port,
                            room_type=args.room_type, fixture=args.fixture,
                            input_queue=operator_input)
    backend.open()
    print(f"Watch the Shroom at http://{args.sim_host}:{backend.port}/")

    # ONE cleanup path, covering everything after backend.open(). The guard
    # starts here and not at the tick loop because every step between is
    # interruptible: o2lite.initialize() blocks on mDNS discovery,
    # set_services() rides the same socket, the clock-sync wait below spins
    # until the hub answers, and service_conflict() polls a self-addressed
    # nonce against a timeout. A SIGTERM in any of them used to raise
    # KeyboardInterrupt with no handler in scope, printing a traceback and
    # leaving the WebSim backend open.
    #
    # build() and backend.open() above are deliberately left uncovered:
    # WebSimBackend.open() only binds a local socket and starts a
    # daemon=True thread, so a signal landing there is self-cleaning on
    # process exit rather than a real leak -- unlike the multi-second
    # o2lite.initialize() and clock-sync window this guard does cover.
    #
    # That is not a hypothetical. The clock-sync wait is exactly where a
    # device sits when the upstream /host/clear defect bites (see
    # docs/MM_TERRARIUM.md, "A device's clock-sync to Arco after Control
    # has connected is unreliable"), so it is the likeliest place in this
    # program to be signalled -- and it was the one path the SIGTERM
    # handler did not protect. Measured live on 2026-08-14.
    try:
        o2lite.initialize(args.ensemble)
        o2lite.set_services(args.dev)      # the device offers its own ie<N>

        def on_down(address, typespec, info):
            """o2litepy handler: THREE parameters, and `address` has already
            had its leading '/' stripped. Arguments are pulled in typespec
            order, not handed over as a list."""
            try:
                values = pull_args(o2lite, typespec or "")
            except Exception:
                # Mirrors devicelink/o2_transport.py's _on_message
                # diagnostic, but print rather than logging: this module has
                # no logging setup, and every other operator-facing line here
                # (the watch URL, the clock-synced line, the frames-displayed-
                # late count) is already print, so that is what a person
                # running this tool will actually see.
                print(f"dropping /{address}: unreadable arguments")
                return                      # drop the frame, never raise
            client.handle({"timestamp": o2lite.msg_timestamp,
                           "address": f"/{address}",
                           "typespec": typespec or "", "args": values})

        for kind in ("role", "leds", "release", "deny", "error"):
            o2lite.method_new(f"/{args.dev}/{kind}", None, True, on_down, None)

        while o2lite.time_get() < 0:       # block until clock sync
            if parent_is_gone(args.exit_with_parent):
                print("parent is gone; exiting before clock sync")
                return                     # the finally below still runs
            o2lite.poll()
            time.sleep(0.01)
        print(f"{markers.DEVICE_CLOCK_SYNCED} {o2lite.time_get():.3f}",
              flush=True)

        # The service announcement went out at set_services time and was
        # never acknowledged. Check it actually took before serving a canvas
        # that would otherwise stay dark for the whole run with no
        # explanation.
        problem = service_conflict(o2lite, args.dev)
        if problem is not None:
            print(problem, file=sys.stderr)
            # SystemExit is a BaseException, so it passes through the
            # except below untouched and still exits 1 -- it just gets its
            # cleanup from the finally now instead of by hand.
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
        last_operator_tilt = None
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
        bridge_id = getattr(o2lite, "bridge_id", None)
        while not client.released:
            if parent_is_gone(args.exit_with_parent):
                print("parent is gone; exiting")
                break
            o2lite.poll()
            bridge_id, problem = reconnect_recheck(o2lite, args.dev, bridge_id)
            if problem is not None:
                print(problem, file=sys.stderr)
                raise SystemExit(1)
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
                        print(f"{joins_sent} joins unanswered. "
                              f"{join_stall_hint(args.dev)}")
            if not deny_printed and client.last_deny is not None:
                reason, hint = client.last_deny
                print(f"{markers.DEVICE_JOIN_DENIED} {reason} ({hint})",
                      flush=True)
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
                    print(f"{markers.DEVICE_ROLE_GRANTED} {joins_sent} "
                          f"join(s); gestures starting at {now:.3f}", flush=True)
                operator = drain_gestures(operator_input, o2lite.send,
                                          args.dev, now)
                if operator is not None:
                    last_operator_tilt = operator
                sweeping = (last_operator_tilt is None
                            or now - last_operator_tilt >= SWEEP_RESUME_SECONDS)
                if now >= next_tilt:
                    if sweeping:
                        gamma = tilt_sweep(now - start)
                        # Timestamps at the source (Design Rule 4): the
                        # device's own synced clock reading, not Control's
                        # receipt time.
                        o2lite.send("/game/tilt", now, "sf", args.dev, gamma)
                    # Advance even while suspended, so the sweep resumes on
                    # schedule instead of firing a backlog of overdue tilts.
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
