"""python -m harness.o2_shroom -- a Testshroom over real o2lite.

The acceptance vehicle for docs/superpowers/specs/
2026-08-12-control-o2lite-and-timed-cues-design.md: a clock-synced O2
device that hellos, optionally answers Control's handshake invite
(--handshake), drives one gesture once its role arrives at RUNNING, and
displays its frames at their declared time.

It reuses harness/shroom_client.py's ShroomClient unmodified for the
protocol surface -- that module's docstring already anticipated this, since
its transport half lives in main() precisely because o2lite replaces it.

Trap worth knowing: TestBit's `player` is a SCORED role, and a scored role
is only validated during SETUP (contract v3, spec 2026-10-01). A device
that never handshakes, or handshakes after SETUP, is a jam device at start.
The driver must hold in SETUP long enough for this client to handshake,
exactly as harness/run_stack.py's --setup-seconds already does.

Usage (needs a running Arco and PYTHONPATH=/Users/chris/projects/arco):
    python3 -m harness.o2_shroom --dev ie1 --node TEST_PLAYER_NODE
"""

from __future__ import annotations

import math
import os
import queue
import sys

from devicelink.contract import HELLO_INTERVAL_S
from harness import markers
from harness.beat_link import (BeatLink, DropRole, DropTransport, SendBeat,
                               SendHello, StateChanged)
from harness.arco_paths import ARCO_PYTHONPATH, ensure_o2litepy
from harness.shroom_client import LED_CHANNELS, ShroomClient
from harness.signals import parent_is_gone, sigterm_as_keyboard_interrupt

# Printed once per hub-away transition by reconnect_recheck, so run_stack's
# log watchers -- and a human tailing the console -- can key on one stable
# string instead of parsing the bridge-id numbers around it. Console ABORT
# hard-stops Arco by design (the o2 hub), so this is an expected, not an
# error, condition: the loop below idles and keeps polling until o2lite
# reconnects and stamps a new positive bridge id.
HUB_AWAY_NOTE = "hub connection lost; waiting for it to return"

# Degrees. TestBit._on_tilt clamps gamma to [-90, 90] and maps it onto
# cc:74, which `player` binds to aurora's hue lane.
SWEEP_DEGREES = 90.0
# Seconds for one full there-and-back sweep. Slow enough to watch the hue
# glide rather than strobe.
SWEEP_PERIOD = 8.0


def tilt_sweep(elapsed: float) -> float:
    """A deterministic ping-pong ramp over [-90, 90] degrees.

    A triangle wave rather than a sawtooth: aurora glides its hue under
    cc:74, so a wrap-around discontinuity reads as a visible snap. The
    original LED harness's canned cc:74 ramp had this shape and proved it
    looks right.
    """
    phase = (elapsed % SWEEP_PERIOD) / SWEEP_PERIOD
    triangle = 2.0 * abs(2.0 * (phase - math.floor(phase + 0.5)))
    return SWEEP_DEGREES * (triangle - 1.0)


def next_heartbeat_time(now: float, interval: float) -> float:
    """The next O2 time a heartbeat /game/hello should be resent.

    interval <= 0 disables the heartbeat: returns float('inf') so a
    `now >= next_heartbeat_time(...)` check in main()'s tick loop never
    fires again ("0 disables the resend", like --heartbeat-interval).
    """
    if interval <= 0:
        return float("inf")
    return now + interval


def beat_legacy_hello(interval: float) -> float:
    """The BeatLink legacy hello interval for --heartbeat-interval.

    interval <= 0 means "no resend" (like next_heartbeat_time), so it maps
    to infinity: a literal 0 would make BeatLink re-hello on every lap."""
    return float("inf") if interval <= 0 else interval


def link_transition(was_linked: bool, bridge_id):
    """One lap's link view: returns (event, linked) where event is "up",
    "down" or None. A bridge id that is None or negative means the hub is
    away."""
    linked = isinstance(bridge_id, int) and bridge_id >= 0
    if linked and not was_linked:
        return "up", linked
    if was_linked and not linked:
        return "down", linked
    return None, linked


def beat_reply_args(values) -> tuple[int, str] | None:
    """(seq, epoch) of a /<dev>/beat reply, or None when it is malformed
    (fewer than two values, seq not an int, epoch not a str): the caller
    drops it rather than raising inside the o2lite callback."""
    if len(values) < 2:
        return None
    seq, epoch = values[0], values[1]
    if isinstance(seq, bool) or not isinstance(seq, int):
        return None
    if not isinstance(epoch, str):
        return None
    return seq, epoch


def run_beat_actions(actions, *, send_beat, send_hello, drop_transport,
                     drop_role, say) -> None:
    """Carry out BeatLink's actions (harness/beat_link.py). Each effect is
    injected, so this stays testable without o2lite."""
    for action in actions:
        if isinstance(action, SendHello):
            send_hello()
        elif isinstance(action, SendBeat):
            send_beat(action.seq, action.rtt_ms)
        elif isinstance(action, DropTransport):
            drop_transport()
        elif isinstance(action, DropRole):
            drop_role()
        elif isinstance(action, StateChanged):
            say(f"{markers.DEVICE_LINK_STATE} {action.state}")


# Seconds after the operator's last drag-tilt before the synthetic sweep
# resumes. Long enough that hue does not snap back mid-exploration, short
# enough that an unattended run still animates.
SWEEP_RESUME_SECONDS = 5.0


def wants_verb(config: dict | None, verb: str) -> bool:
    """Whether the granted role wants this synthetic gesture. The role blob
    carries the Bit's `uses` list (control/role_config.py); a role that
    declares one gets only what it lists -- MetronomeBit's player lists
    `tap` alone, and driving it with tilts earned an `unknown verb 'tilt'`
    error on every run. A role that declares nothing keeps the legacy
    tilt sweep, so hand-built Bits are unchanged."""
    uses = (config or {}).get("uses") or []
    return not uses or verb in uses


def lobby_round_over(client, persist: bool) -> str | None:
    """The per-tick round-over decision, factored pure so it is testable
    with no socket (this module's convention -- see next_heartbeat_time).
    Only a release ends the round. A deny is informational (spec
    2026-10-01 section 5.7): a device over the scored cap is denied and
    becomes a jam device at start, so it must neither end a one-shot run
    nor, under --persist, leave the lobby."""
    if client.released:
        return "lobby" if persist else "exit"
    return None


def hello_args(dev: str, instrument: str | None) -> tuple[str, tuple]:
    """(typespec, args) of this device's /game/hello. The ONE builder for
    the initial hello and every heartbeat resend, so the name (and the
    instrument) never differ between them: a changed name makes the engine
    announce a rename, which would fire every heartbeat. Mirrors
    ShroomClient.hello()'s declared/undeclared shape split."""
    if instrument is None:
        return "s", (dev,)
    return "ssss", (dev, "", "", instrument)


def send_handshake_ack(o2lite, dev: str, round_id: str, node: str | None):
    """Answer an invite: /game/handshake <dev> <round_id> <node>, over TCP
    (send_cmd) so it cannot be overtaken by UDP gestures. An empty node
    asks Control for its default scored node."""
    o2lite.send_cmd("/game/handshake", 0, "sss", dev, round_id, node or "")


def handshake_due(client, enabled: bool, sent: set, seen_at: dict,
                  now: float, delay: float) -> str | None:
    """The round id this device should answer with /game/handshake now, or
    None. Answers each round id once, `delay` seconds after the invite was
    first seen, and only while this device has no role: once Control has
    sent /role the round is over for handshaking. Pure so it is testable
    with no socket; `seen_at` is the caller's round_id -> first-seen map."""
    round_id = client.round_id
    if not enabled or round_id is None or client.config is not None:
        return None
    if round_id in sent:
        return None
    first = seen_at.setdefault(round_id, now)
    if now - first < delay:
        return None
    return round_id

# Bound on browser gestures queued between ticks. Generous: the page
# rate-bounds tilts to 20 Hz and the loop drains every ~5 ms.
INPUT_QUEUE_MAX = 64


def enqueue_input(q: "queue.Queue", msg: dict, stamp: float | None) -> None:
    """Queue one browser gesture with its enqueue-time stamp, dropping the
    OLDEST on overflow.

    Runs on WebSimBackend's websocket handler thread, so it must never
    block; drop-oldest keeps the freshest gestures, matching the
    drop-not-queue rule frame relay already follows elsewhere. `stamp` is
    read on THIS thread, at enqueue time, so it does not absorb the
    latency of waiting for the next tick's drain (see drain_gestures)."""
    entry = (stamp, msg)
    while True:
        try:
            q.put_nowait(entry)
            return
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass


def drain_gestures(q: "queue.Queue", send, dev: str, now: float,
                   config: dict | None = None):
    """Translate every queued browser gesture into a /game/* send.

    `send` has o2lite.send's signature: send(address, time, typespec,
    *args). Each gesture carries its own enqueue-time stamp (see
    enqueue_input); that stamp is used as the send time when present,
    with `now` -- the caller's o2lite clock reading -- as the fallback
    for entries stamped None. Either way the stamp is still a reading
    taken somewhere inside this simulator process, because the whole
    simulator process is the device (Design Rule 4); the browser hop
    happened inside the device, and moving the stamp from drain time to
    enqueue time only moves it earlier within that same device, not
    outside it. Returns the stamp of the drained tilt if any tilt went
    out (the caller suspends its synthetic sweep against it), else None.
    Malformed entries are dropped with one diagnostic per drain,
    mirroring the engine's drop-this-frame rule.

    `config` is the granted role blob (client.config). hold and swing are
    Rev 1 verbs that wait for a role (pre_role false in
    devicelink/contract.py), so they go out only when the role's `uses`
    lists them by name -- stricter than wants_verb, because no Bit written
    before Rev 1 handles either. Otherwise a hold (the page's long press)
    goes out as the plain tap it was before hold existed, and a swing is
    dropped. A hold is stamped at touch-down, `held_seconds` before its
    release-time stamp, as the contract row says."""
    tilted = None
    complained = False
    while True:
        try:
            stamp, msg = q.get_nowait()
        except queue.Empty:
            return tilted
        when = stamp if stamp is not None else now
        kind = msg.get("type") if isinstance(msg, dict) else None
        try:
            if kind == "tap":
                count = max(1, int(msg.get("count", 1)))
                send("/game/tap", when, "sffi", dev, 1.0, 50.0, count)
            elif kind == "tilt":
                gamma = max(-90.0, min(90.0, float(msg["gamma"])))
                send("/game/tilt", when, "sf", dev, gamma)
                tilted = when
            elif kind == "hold":
                held = float(msg["held_seconds"])
                if not (math.isfinite(held) and held >= 0.0):
                    raise ValueError(held)
                if config is None:
                    continue
                if "hold" in (config.get("uses") or []):
                    send("/game/hold", when - held, "sfi", dev, held, 1)
                else:
                    send("/game/tap", when, "sffi", dev, 1.0, 50.0, 1)
            elif kind == "swing":
                g = float(msg["signed_peak_g"])
                if not math.isfinite(g):
                    raise ValueError(g)
                if config is not None and "swing" in (config.get("uses") or []):
                    send("/game/swing", when, "sfi", dev, g, 1)
                elif config is not None:
                    print("swing ignored: this role does not use swing",
                          flush=True)
            else:
                raise ValueError(kind)
        except (KeyError, TypeError, ValueError):
            if not complained:
                print(f"dropping operator gesture {msg!r}", flush=True)
                complained = True


def discard_pre_role(q: "queue.Queue", reason: str) -> int:
    """Empty the gesture queue before a role has arrived, and say so.

    Without this, clicks made while the join is pending sat in the queue
    and all went out at once, with stale stamps, the moment the role
    landed; and until then the page looked dead with nothing in the
    terminal to say why. Returns how many were dropped."""
    dropped = 0
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break
        dropped += 1
    if dropped:
        print(f"{dropped} gesture(s) ignored: {reason}", flush=True)
    return dropped


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

    if current is None or (isinstance(current, int) and current < 0):
        # The hub itself went away (Console ABORT takes Arco down by
        # design). Not an error: idle, keep polling, and re-verify when
        # o2lite reconnects and stamps a real bridge id.
        print(f"{HUB_AWAY_NOTE} (bridge id {previous_bridge_id} -> {current})")
        return current, None

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
    #
    # A reconnect can also land mid-verify just as the hub drops again
    # (a second ABORT, or the same one still settling), and o2litepy's
    # send path asserts rather than returning an error in that case --
    # not a real conflict, just the hub not being there yet. Treat it
    # the same as the negative-bridge-id case above: idle at the
    # PREVIOUS bridge id so the next lap re-detects the change and
    # retries the whole re-check once the hub is truly back.
    try:
        verified = verify(o2lite, dev, timeout=10.0, resend_interval=2.0)
    except (AssertionError, OSError):
        print(f"{HUB_AWAY_NOTE} (send failed during the ownership re-check; "
              f"will retry)")
        return previous_bridge_id, None
    if verified:
        return current, None

    problem = (f"{markers.DEVICE_SERVICE_CONFLICT} {dev!r} is not routed "
               f"back to this process after reconnecting to the hub "
               f"(bridge id {previous_bridge_id} -> {current}). Another "
               f"process has likely claimed it, and O2 refuses a second "
               f"claimant silently (o2/src/bridge.cpp:231-237). Nothing "
               f"addressed to /{dev}/* will ever arrive here.")
    return current, problem


def _gestures_ready(client) -> bool:
    """True once Control's /role reply has actually reached this client,
    i.e. once ShroomClient._on_role() has set client.config (see
    harness/shroom_client.py). The role arrives at RUNNING (contract v3),
    and gating gestures on it, not on 'handshake sent', closes the race
    where a UDP gesture overtakes the TCP handshake: there is nothing to
    overtake once the reply has already arrived.

    --no-join callers (the Room simulator) never get a role -- this would
    return False for them forever. That is correct, but it must not be the
    ONLY thing stopping their gestures: main() also short-circuits on
    args.no_join first, so a --no-join run never even calls this."""
    return client.config is not None


def build(dev: str, node: str = "TEST_PLAYER_NODE",
          sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True, room_type: str | None = None,
          fixture: str | None = None,
          input_queue: "queue.Queue | None" = None,
          clock=None, on_play=None, instrument: str | None = None,
          on_show=None):
    """Construct the client and its LED backend WITHOUT opening a socket.

    Returns (client, backend). serve=False gives a record-only backend for
    headless tests (the build()/main() split).

    room_type, when given, renders that ROOM's ONE named fixture instead of
    a Testshroom's surface -- fixture is then required. This is the
    --no-join path, where this module is the Room simulator, once per
    fixture, on the o2lite transport.

    input_queue, when given, receives every gesture the browser page sends
    back; see drain_gestures.

    clock, when given, is called ON THE WEBSOCKET HANDLER THREAD to stamp
    each gesture at enqueue time rather than at the next drain (see
    enqueue_input). Pass o2lite.time_get: verified against o2litepy's
    source (arco checkout, o2litepy/o2lite.py) to be a pure read --
    local_time() (== time.monotonic() - a fixed start offset) plus the
    already-synced global_minus_local float, no socket I/O and no state
    mutation -- so it is safe to call from a thread other than the one
    running the o2lite event loop. If clock is None, gestures are queued
    with stamp=None and drain_gestures falls back to its own `now`.

    on_show, when given, is WebSimLeds' displayed-frame hook, called
    (frame, clock()) -- see harness/websim_leds.py.
    """
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    from harness.websim_leds import WebSimLeds

    if room_type is None:
        capability = shroom_capability(surface_id=dev)
        channels = LED_CHANNELS
    else:
        if fixture is None:
            raise ValueError("room_type requires fixture")
        from control.terrarium_config import load_terrarium_config
        from harness.room_surface import to_fixture_capability

        profile = load_terrarium_config("terrarium.toml").rooms[room_type].profile
        capability = to_fixture_capability(profile, fixture)
        channels = capability.pixel_count * len(capability.color_order)

    on_input = (None if input_queue is None
                else lambda msg: enqueue_input(
                    input_queue, msg,
                    stamp=(clock() if clock is not None else None)))
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

    client = ShroomClient(dev, node,
                          leds=WebSimLeds(backend, channels,
                                          on_show=on_show, clock=clock),
                          on_role=_on_role, on_play=on_play,
                          expected_channels=channels, instrument=instrument)
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
    parser.add_argument("--instrument", default="testshroom",
                        help="Declare this device's carried instrument on "
                             "hello, re-sent on every heartbeat (default "
                             "testshroom, the harness's own catalog "
                             "instrument). Pass an empty string to stay "
                             "undeclared, resolving to defaultshroom.")
    parser.add_argument("--no-beat", action="store_true",
                        help="behave as a legacy client: no /game/beat, "
                             "only the hello every --heartbeat-interval "
                             "seconds (spec 2026-10-08)")
    parser.add_argument("--heartbeat-interval", type=float,
                        default=HELLO_INTERVAL_S,
                        help="Resend /game/hello every N seconds while "
                             "connected, so Control's GameServer.reap_stale "
                             "does not time this device out for going "
                             "quiet between gestures. 0 disables the "
                             "resend (pre-liveness-detection behavior). "
                             "Applies with or without --no-join: a Room "
                             "device needs it too, even though "
                             "reap_stale() never actually reaps a "
                             "Room-bound dev today.")
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
                        help="Send /game/hello only: never a handshake "
                             "ack (/game/handshake), and no gestures. This "
                             "is what the Room simulator needs: Control has "
                             "already recorded this dev as the bound Room "
                             "before the process is spawned, so there is "
                             "nothing to accept.")
    parser.add_argument("--room-type", default=None,
                        help="Render this Room's (a name in terrarium.toml) surface instead of a "
                             "Testshroom's. Only meaningful with --no-join, "
                             "which is how this module serves as the Room "
                             "simulator on the o2lite path.")
    parser.add_argument("--fixture", default=None,
                        help="Which Room fixture to render. Required "
                             "together with --room-type.")
    parser.add_argument("--identify-blocks", action="store_true",
                        help="Debug: skips Control and never connects to "
                             "the hub; paints each of this fixture's "
                             "declared blocks a distinct solid color and "
                             "holds until Ctrl-C, "
                             "so the physical build-out mapping can be "
                             "confirmed visually. Needs --no-join, "
                             "--room-type and --fixture.")
    parser.add_argument("--exit-with-parent", type=int, default=None,
                        metavar="PID",
                        help="Exit as soon as this process's parent is no "
                             "longer PID. harness/terrarium_boot.py passes "
                             "its own pid so a Room simulator cannot outlive "
                             "the Terrarium that spawned it and steal its dev "
                             "name from the next run.")
    parser.add_argument("--persist", action="store_true",
                        help="Lobby mode: on release, return to the "
                             "hello lobby instead of exiting, so this "
                             "device takes part in whatever Bit the Console "
                             "loads next, across room recycles (each Bit "
                             "close replaces Arco; o2lite auto-reconnects "
                             "and reconnect_recheck re-verifies the "
                             "service). A deny never ends a round either "
                             "way. Meaningless with --no-join.")
    parser.add_argument("--handshake", action="store_true",
                        help="Answer Control's /<dev>/handshake invite with "
                             "/game/handshake <dev> <round_id> <node>, once "
                             "per round id (spec 2026-10-01). Without it "
                             "the device only says hello and ends up a jam "
                             "device at start.")
    parser.add_argument("--handshake-delay", type=float, default=0.0,
                        metavar="SECONDS",
                        help="Wait this long after first seeing an invite "
                             "before answering it (default 0). Lets a run "
                             "order several devices' handshakes.")
    args = parser.parse_args()

    # control/simulator_process.py shuts this process down with SIGTERM when
    # it is playing the Room simulator, and finally blocks do not run on a
    # bare SIGTERM -- so without this the exit lateness report and the
    # backend shutdown below are simply lost.
    sigterm_as_keyboard_interrupt()

    # Lazy, exactly like harness/arco_synth.py: this module must import with
    # no o2litepy on the path. When run by hand (outside run_stack, which
    # already ran this same fallback for its children), fall back to the
    # hardcoded arco checkout before giving up.
    if not ensure_o2litepy():
        print(f"o2_shroom needs o2litepy and could not find it, even after "
              f"falling back to {ARCO_PYTHONPATH}. Is the arco checkout "
              f"present there? Otherwise re-run with PYTHONPATH pointing "
              f"at it.", file=sys.stderr)
        raise SystemExit(1)

    from o2litepy import o2lite

    from devicelink.o2_transport import pull_args

    from harness.sim_audio import KeyedChimePlayer, build_sim_player, play_key

    operator_input = queue.Queue(maxsize=INPUT_QUEUE_MAX)
    # /<dev>/play sink: generated tones through afplay (degrades to a
    # printed line off-Mac). Without this every PlayCue died on the wire
    # as an o2lite "no match" drop and the sim was silent by accident.
    player = build_sim_player()
    keyed = KeyedChimePlayer(player.sink) if hasattr(player, "sink") else None

    def _play(name: str, params: str) -> None:
        key = play_key(params)
        if name == "chime" and key is not None and keyed is not None:
            keyed.play(key)
        else:
            player.play(name)

    from harness.beat_tapper import BeatTapper

    # The synthetic player for a call-and-response role (MetronomeBit):
    # taps on the answer beats it sees in its own light. Armed only once
    # the granted role's `uses` says `tap` (see the tick loop below).
    tapper = BeatTapper()

    def _on_frame(frame: bytes, now) -> None:
        if not tapper.armed or now is None:
            return
        beat = tapper.observe(frame, now)
        if beat is None:
            return
        # Stamped at the display tick's own clock reading -- the moment
        # this device showed the beat -- never Control's receipt time.
        o2lite.send("/game/tap", now, "sffi", args.dev, 1.0, 50.0, 1)
        print(f"tap sent: beat {beat} at {now:.3f}", flush=True)

    client, backend = build(args.dev, args.node,
                            args.sim_host, args.sim_port,
                            room_type=args.room_type, fixture=args.fixture,
                            input_queue=operator_input,
                            clock=o2lite.time_get,
                            on_play=_play,
                            instrument=args.instrument or None,
                            on_show=_on_frame)
    backend.open()
    canvas_url = f"http://{args.sim_host}:{backend.port}/"
    url_marker = markers.ROOM_URL if args.no_join else markers.BROWSE_URL
    print(f"{url_marker} Watch the Shroom at "
          f"{canvas_url}", flush=True)

    def send_hello() -> None:
        # Re-sent on every heartbeat (see next_heartbeat_time below), so a
        # declared client's instrument survives GameServer.reap_stale's
        # liveness re-hello exactly as the initial one did. Both go through
        # hello_args, so the name and instrument are identical every time.
        hello_typespec, hello_arguments = hello_args(args.dev,
                                                     client.instrument)
        try:
            o2lite.send_cmd("/game/hello", 0, hello_typespec,
                            *hello_arguments)
            o2lite.send_cmd("/game/canvas", 0, "ss", args.dev, canvas_url)
        except (AssertionError, OSError):
            pass   # hub away; the heartbeat resend tries again

    beat = None if args.no_beat else BeatLink(
        legacy_hello=beat_legacy_hello(args.heartbeat_interval))

    def _send_beat(seq: int, rtt_ms: int) -> None:
        try:
            o2lite.send("/game/beat", 0, "sii", args.dev, seq, rtt_ms)
        except (AssertionError, OSError):
            pass   # hub away; BeatLink's lost timer handles it

    transport_dropped = False

    def _drop_transport() -> None:
        nonlocal transport_dropped
        transport_dropped = True
        o2lite.tcp_close()

    def do_beat(actions) -> None:
        run_beat_actions(
            actions, send_beat=_send_beat, send_hello=send_hello,
            drop_transport=_drop_transport,
            drop_role=client.reset_for_lobby,
            say=lambda line: print(line, flush=True))

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
        if args.identify_blocks:
            if not (args.no_join and args.room_type and args.fixture):
                parser.error("--identify-blocks needs --no-join, "
                             "--room-type and --fixture")
            from control.terrarium_config import load_terrarium_config
            from harness.websim_leds import identify_blocks_frame

            profile = load_terrarium_config(
                "terrarium.toml").rooms[args.room_type].profile
            backend.send(identify_blocks_frame(profile, args.fixture))
            print(f"identify-blocks: {args.fixture} painted; Ctrl-C to "
                  f"exit", flush=True)
            while not parent_is_gone(args.exit_with_parent):
                time.sleep(0.5)
            return

        o2lite.initialize(args.ensemble)
        o2lite.set_services(args.dev)      # the device offers its own ie<N>

        def on_down(address, typespec, info):
            """o2litepy handler: THREE parameters, and `address` has already
            had its leading '/' stripped. Arguments are pulled in typespec
            order, not handed over as a list."""
            try:
                values = pull_args(o2lite, typespec or "", f"/{address}")
            except Exception:
                # Mirrors devicelink/o2_transport.py's _on_message
                # diagnostic, but print rather than logging: this module has
                # no logging setup, and every other operator-facing line here
                # (the watch URL, the clock-synced line, the frames-displayed-
                # late count) is already print, so that is what a person
                # running this tool will actually see.
                print(f"dropping /{address}: unreadable arguments")
                return                      # drop the frame, never raise
            if beat is not None:
                beat.on_control_message(time.monotonic())
                if address.endswith("/beat"):
                    reply = beat_reply_args(values)
                    if reply is None:
                        print(f"dropping /{address}: malformed beat reply")
                        return
                    do_beat(beat.on_beat_reply(time.monotonic(), *reply))
                    return
            client.handle({"timestamp": o2lite.msg_timestamp,
                           "address": f"/{address}",
                           "typespec": typespec or "", "args": values})

        for kind in ("role", "leds", "release", "deny", "error",
                     "room", "play", "handshake", "validated", "beat"):
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

        if beat is None:
            send_hello()
        else:
            do_beat(beat.link_up(time.monotonic()))
        was_linked = True

        start = o2lite.time_get()
        interval = 1.0 / args.tilt_hz
        bridge_id = getattr(o2lite, "bridge_id", None)

        # Round ids already answered, and when each invite was first seen
        # (for --handshake-delay). Kept across rounds: a round id is
        # answered once however many times its invite repeats.
        handshaken: set = set()
        first_invite_at: dict = {}
        round_num = 1
        while True:                     # rounds; one lap in one-shot mode
            round_start = o2lite.time_get()
            next_heartbeat = next_heartbeat_time(round_start, args.heartbeat_interval)
            # Deferred rather than started at `start`: gestures are held off
            # until _gestures_ready(client) -- see that function's docstring
            # for why -- so the first tilt should be scheduled for the
            # moment the role actually arrives, not backdated to loop start
            # (which would fire a burst of "overdue" tilts back-to-back the
            # instant the gate opens).
            next_tilt = None
            last_operator_tilt = None
            # A deny/error is asynchronous -- it only arrives once the
            # loop below polls it in -- so noticing one has to happen
            # inside the loop. Printed once each: without this, a refused
            # handshake looks identical to a working device that simply has
            # no frames yet -- a blank browser and no explanation.
            deny_printed = False
            error_printed = False
            outcome = None
            while outcome is None:
                if parent_is_gone(args.exit_with_parent):
                    print("parent is gone; exiting")
                    outcome = "exit"
                    break
                o2lite.poll()
                bridge_id, problem = reconnect_recheck(o2lite, args.dev, bridge_id)
                if problem is not None:
                    print(problem, file=sys.stderr)
                    raise SystemExit(1)
                if beat is not None:
                    event, was_linked = link_transition(was_linked, bridge_id)
                    if event == "up":
                        do_beat(beat.link_up(time.monotonic()))
                    elif event == "down":
                        do_beat(beat.link_down(time.monotonic()))
                    do_beat(beat.tick(time.monotonic()))
                    if transport_dropped:
                        # We closed the transport ourselves. A reconnect
                        # that lands inside one poll() can reuse the same
                        # bridge id, which would hide the relink. Mark our
                        # own view down so the next positive id is always
                        # a change: reconnect_recheck re-verifies and
                        # link_up fires.
                        transport_dropped = False
                        bridge_id = -1
                        was_linked = False
                now = o2lite.time_get()
                if now < 0:
                    # Across a room recycle the clock goes unsynced until
                    # the new Arco masters it, and every now-based branch
                    # below would misfire on -1.
                    time.sleep(0.05)
                    continue
                if beat is None and now >= next_heartbeat:
                    send_hello()
                    next_heartbeat = next_heartbeat_time(now, args.heartbeat_interval)
                if not deny_printed and client.last_deny is not None:
                    reason, hint = client.last_deny
                    print(f"{markers.DEVICE_JOIN_DENIED} {reason} ({hint})",
                          flush=True)
                    deny_printed = True
                    # Informational only: a denied device gets no scored
                    # role, but it still becomes a jam device at start, so
                    # the loop keeps polling for its /role.
                if not error_printed and client.last_error is not None:
                    context, message = client.last_error
                    print(f"ERROR from Control: {context}: {message}")
                    error_printed = True
                outcome = lobby_round_over(client, args.persist)
                if outcome is not None:
                    break
                due = handshake_due(client, args.handshake and not args.no_join,
                                    handshaken, first_invite_at, now,
                                    args.handshake_delay)
                if due is not None:
                    try:
                        send_handshake_ack(o2lite, args.dev, due, args.node)
                    except (AssertionError, OSError):
                        pass    # hub away; the invite repeats, so retry
                    else:
                        handshaken.add(due)
                        print(f"handshake: answered round {due} at "
                              f"{now:.3f}", flush=True)
                if not args.no_join and not _gestures_ready(client):
                    discard_pre_role(
                        operator_input,
                        "denied; this device is jam at start"
                        if client.last_deny is not None
                        else "waiting for a role")
                if not args.no_join and _gestures_ready(client):
                    if next_tilt is None:
                        next_tilt = now   # first tilt fires now the role is in
                        # The ROLE GRANTED line itself is printed by
                        # ShroomClient when /role lands; say when gestures
                        # begin.
                        print(f"gestures starting at {now:.3f}", flush=True)
                        # The role decides which synthetic gestures run.
                        tapper.armed = wants_verb(client.config, "tap")
                        if tapper.armed:
                            print("role uses tap: beat tapper armed", flush=True)
                    operator = drain_gestures(operator_input, o2lite.send,
                                              args.dev, now, client.config)
                    if operator is not None:
                        last_operator_tilt = operator
                    sweeping = (wants_verb(client.config, "tilt")
                                and (last_operator_tilt is None
                                     or now - last_operator_tilt >= SWEEP_RESUME_SECONDS))
                    if now >= next_tilt:
                        if sweeping:
                            gamma = tilt_sweep(now - start)
                            # Timestamps at the source (Design Rule 4): the
                            # device's own synced clock reading, not
                            # Control's receipt time.
                            o2lite.send("/game/tilt", now, "sf", args.dev, gamma)
                        # Advance even while suspended, so the sweep resumes
                        # on schedule instead of firing a backlog of overdue
                        # tilts.
                        next_tilt += interval
                client.tick(now)
                time.sleep(0.005)

            if outcome == "exit" or args.no_join or not args.persist:
                break
            print(f"round {round_num} released; returning to lobby",
                  flush=True)
            client.reset_for_lobby()
            tapper.reset()
            tapper.armed = False
            round_num += 1
            send_hello()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"frames displayed late: {client.clamped}")
        print(f"beat taps sent: {tapper.taps}")
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
