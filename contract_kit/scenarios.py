"""The eleven device contract kit scenarios (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md sections 4.3 and 5.4).

Each function takes no arguments, builds its own Recorder, and returns an
EXPORT FORMAT v1 scenario dict recorded from the REAL Control engine.
tools/record_scenarios.py writes them to contract_kit/recordings/;
tests/test_contract_scenarios.py re-runs them and fails on any diff against
the committed files, and also checks that each recording still contains the
behavior its spec table row names.

These files are the contract two other repos learn device behavior from (the
Flutter Tuneshroom app and the Rev 1 ESP32 firmware), so where Control's real
behavior differs from the spec's prose the RECORDING wins and the difference
is stated in the scenario's own docstring. The three differences found while
recording:

1. A pre-role tap outside a lobby is answered with `/$DEV/error tap "device
   not registered"`. Rule 2 permits a device to SEND a tap before a role,
   and Control accepts it as a lobby join tap when a Room is loaded
   (`lobby_tap_join`), but with no Room there is no lobby to receive it
   (`DeviceLinkAgent._enter_lobby` returns early when `gs.room is None`).
   See `gestures_after_role` and `error_no_state_change`.
2. A newly granted role plays a ~1.5 s opening signature before the role's
   own light manifest reaches the pixels, and that signature ignores light
   cues. A timed look sent during it changes nothing a device can see, so
   `timed_frames_hold_last` waits for the signature to settle before it
   taps. See that scenario's docstring.
3. The closing fade's last frame is a dim non-black frame, not black, and
   `/$DEV/release` follows it on the wire in the same millisecond while
   carrying no presentation time of its own. See `release_keeps_display`
   for the spec section 5.5 ordering answer.

One rule for replay runners, which applies to every scenario and bites in
`link_loss_rejoin`: a runner must not deliver any `control_sends` step while
the scenario's link is down. Those steps record what Control really sends,
and a device with its link down receives none of them.
"""
from __future__ import annotations

from typing import Callable

from contract_kit.contract_bit import CONTRACT_PLAYER_NODE
from contract_kit.recorder import Recorder

# A node name no Bit's role table declares. GameServer.join() refuses any
# node absent from the role table's node_map, so this is how a scenario asks
# for a deny without inventing a second Bit.
NO_SUCH_NODE = "NO_SUCH_NODE"

# The gesture verbs a device may not send before it holds a role (spec
# section 4.3, rule 2; devicelink/contract.py's pre_role column).
POST_ROLE_GESTURES = ["/game/hold", "/game/swing"]

# How long ContractBit's player-role opening signature runs before the
# role's own light manifest reaches the pixels. Measured from the
# recordings: the last signature frame is sent at t=1518 in
# explicit_join_role, release_keeps_display and timed_frames_hold_last, and
# at t=1503 in play_known_and_unknown, whose early tap shifts the tick phase
# by one partial tick. Scenarios that need a light cue to be VISIBLE, or an
# expect_frame that is not within one render tick of a frame change, wait
# past this.
SIGNATURE_SETTLED_MS = 2000

# The hand-authored pair in timed_frames_hold_last. Two frames on one send
# time with different presentation times, in colors no real frame in any
# recording carries (pure blue and pure green in GRB order), so a device
# showing the wrong one is unmistakable.
AUTHORED_NEWER_GRB = [0, 0, 255] * 12
AUTHORED_OLDER_GRB = [255, 0, 0] * 12
AUTHORED_PAIR_T = 6000
AUTHORED_NEWER_AT = 6200
AUTHORED_OLDER_AT = 6100


def boot_hello_heartbeat() -> dict:
    """Rule 1: hello goes out once the link is up and repeats every 5 s,
    declaring the carried instrument in its fourth argument, and the device
    sends nothing else at all before it joins.

    The instrument name itself is recorded as the `*` placeholder: which
    instrument a device carries is its own business, and only the four
    argument `ssss` shape is contract.
    """
    rec = Recorder(name="boot_hello_heartbeat",
                   summary="Hello repeats every 5 s and declares an "
                           "instrument; nothing else goes out before a join",
                   join_node=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_quiet(0, ["/game/join", "/game/tap", *POST_ROLE_GESTURES],
                     12000)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.advance_to(10000)
    rec.expect_hello(10000)
    rec.advance_to(12000)
    return rec.finish()


def explicit_join_role() -> dict:
    """Joining a node by name and receiving the role blob for it.

    The join goes out with the first hello, and Control answers with a fresh
    `/$DEV/room` snapshot (the node's count is now 1), the `/$DEV/role` blob,
    and then the granted session's opening signature on `/$DEV/leds`.
    """
    rec = Recorder(name="explicit_join_role",
                   summary="Joining a node by name and receiving its role",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_join(0, CONTRACT_PLAYER_NODE)
    rec.advance_to(SIGNATURE_SETTLED_MS)
    rec.expect_frame(SIGNATURE_SETTLED_MS)
    return rec.finish()


def lobby_tap_join() -> dict:
    """The lobby handshake: invite flashes show while the device is hello'd
    but un-joined, two count-1 taps inside the 1.5 s double-tap window join
    it, the role arrives, and the join ceremony plays `chime` with a `key=`
    parameter 1.8 s later (control/lobby.py's CHIME_OFFSET_S).

    Unlike `gestures_after_role`, these pre-role taps draw no `/$DEV/error`:
    a Room is loaded here, so the lobby is the thing that receives them.

    The chime's key is recorded as `key=$KEY`. It is the join index into a
    note scale, so a replaying device must reproduce the parameter's shape
    and not its number.
    """
    rec = Recorder(name="lobby_tap_join",
                   summary="Invite frames show before a role; two count-1 "
                           "taps join; the role and the chime follow",
                   join_node=None, with_room=True)
    rec.link_up(0)
    rec.expect_hello(0)
    # Both frame checks sit near the middle of a 200 ms flash, clear of the
    # render tick either side of it.
    rec.advance_to(150)
    rec.expect_frame(150)                  # the invite's first white flash
    rec.tap(1000, duration_ms=80.0)
    rec.tap(1600, duration_ms=80.0)        # 600 ms later, inside the window
    rec.advance_to(1750)
    rec.expect_frame(1750)                 # the accept flash, after the role
    rec.advance_to(3500)
    rec.expect_play(3500, "chime")
    return rec.finish()


def timed_frames_hold_last() -> dict:
    """Rule 3, all three clauses.

    **A frame shows at its own presentation time.** The two taps share one
    onset, so ContractBit stamps both light cues for the same moment.
    Control collapses them into ONE `/$DEV/leds` frame carrying the SECOND
    cue's hue, and stamps that frame `at` the cue's own presentation time
    rather than at the tick it was sent on: it is the only frame in the file
    whose `at` is earlier than its send time plus the cue horizon.

    **When several are due, only the newest shows.** Control's own stream
    never does this: it sends frames 23 ms apart, each stamped one cue
    horizon ahead, so a device is never asked to choose. The pair at
    t=AUTHORED_PAIR_T is therefore HAND-AUTHORED, like the three broken
    inputs in `malformed_dropped`: two `/$DEV/leds` messages on one send
    time, the NEWER presentation time sent first, so a device that simply
    shows whatever arrived last fails the `expect_frame` that follows. Both
    are due by then, and the expectation is the newer one. They sit well
    after the look has settled and gone quiet, so they cannot be confused
    with the glide.

    **The last frame holds through silence.** The closing `expect_frame`
    at 12000 is that same hand-authored newer frame, still showing almost
    six seconds later.

    The taps wait for SIGNATURE_SETTLED_MS on purpose. A newly granted
    session plays a ~1.5 s opening signature that ignores light cues
    entirely, so a look sent inside that window is recorded but invisible,
    which would have made this scenario pass without testing anything.
    Every `expect_frame` here is also placed well clear of the nearest frame
    change, so a runner's one-render-tick of slack cannot decide any of them.
    """
    rec = Recorder(name="timed_frames_hold_last",
                   summary="A future-stamped frame shows at its time; of "
                           "two frames due together only the newest shows; "
                           "the last frame holds through silence",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(SIGNATURE_SETTLED_MS)
    rec.expect_frame(SIGNATURE_SETTLED_MS)   # the settled role look
    rec.tap(SIGNATURE_SETTLED_MS, duration_ms=80.0)
    rec.tap(SIGNATURE_SETTLED_MS, duration_ms=80.0)
    rec.advance_to(5500)
    rec.expect_frame(5500)                   # the look, settled and quiet
    rec.advance_to(AUTHORED_PAIR_T)
    # Hand-authored, newest presentation time FIRST (see the docstring).
    rec.control_send_now("/$DEV/leds", "b", [AUTHORED_NEWER_GRB],
                         at=AUTHORED_NEWER_AT)
    rec.control_send_now("/$DEV/leds", "b", [AUTHORED_OLDER_GRB],
                         at=AUTHORED_OLDER_AT)
    rec.advance_to(6500)
    rec.expect_frame(6500)                   # the newer of the two, not the
                                             # one that arrived last
    rec.advance_to(12000)
    rec.expect_frame(12000)                  # still holding, long after
    return rec.finish()


def gestures_after_role() -> dict:
    """The three gesture shapes and their stamps, and rule 2's pre-role
    window.

    Tap is the one gesture a device may send before a role, so it is the
    only one scripted before the join and hold and swing are held back
    under an `expect_quiet`. With no Room loaded there is no lobby to
    receive that tap, and Control answers it with `/$DEV/error tap "device
    not registered"`: allowed to send is not the same as acted upon.

    Once the role is held, all three are stamped at their own onset, tap
    carries `sffi [dev, peak_g, duration_ms, count]` with peak_g 0 and
    count 1, and hold and swing carry `sfi`.
    """
    rec = Recorder(name="gestures_after_role",
                   summary="Tap may go out before a role, hold and swing "
                           "wait for one; all three stamp at onset",
                   join_node=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.tap(500, duration_ms=80.0)
    rec.expect_quiet(500, POST_ROLE_GESTURES, 500)
    rec.join_now(1000, CONTRACT_PLAYER_NODE)
    rec.tap(1500, duration_ms=80.0)
    rec.hold(2000, held_s=0.65)
    rec.swing(2500, signed_g=-2.1)
    rec.advance_to(3000)
    return rec.finish()


def deny_stays_hellod() -> dict:
    """A join to a node no role table declares is denied with `/$DEV/deny
    ss ["no such node", ""]`, and nothing else changes: no role, no frame,
    and the hello heartbeat keeps running on its own 5 s cadence.
    """
    rec = Recorder(name="deny_stays_hellod",
                   summary="A join to an unknown node is denied; the device "
                           "stays hello'd with its heartbeat running",
                   join_node=NO_SUCH_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_join(0, NO_SUCH_NODE)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.advance_to(10000)
    rec.expect_hello(10000)
    rec.advance_to(11000)
    return rec.finish()


def release_keeps_display() -> dict:
    """Rule 4 and decision D5: unloading the Bit fades the device's look
    out, releases it, and leaves whatever was last shown on the pixels; the
    hello heartbeat keeps running afterwards.

    Spec section 5.5's ordering question, answered from this recording:
    Control sends `/$DEV/release` in the same millisecond as the closing
    fade's last `/$DEV/leds` frame and immediately after it on the wire, but
    the release carries no presentation time while that last frame is
    stamped one cue horizon (60 ms) into the future, so a device does see
    the release before the final fade frame is due to show.

    Two details a device author should not mistake for rig artifacts: the
    fade's last frame is a dim non-black frame rather than black, and the
    `/$DEV/room` snapshot that follows says IDLE with no Bit, because the
    Bit really has been unloaded.
    """
    rec = Recorder(name="release_keeps_display",
                   summary="Unloading the Bit fades and releases a joined "
                           "device; the last fade frame holds and the "
                           "heartbeat continues",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(SIGNATURE_SETTLED_MS)
    rec.expect_frame(SIGNATURE_SETTLED_MS)   # the settled role look
    rec.unload_bit()
    rec.advance_to(3000)
    rec.expect_frame(3000)                   # the last fade frame
    rec.expect_quiet(3000, POST_ROLE_GESTURES, 5000)
    rec.advance_to(5000)
    rec.expect_hello(5000)                   # the heartbeat, after release
    rec.advance_to(8000)
    rec.expect_frame(8000)                   # still holding, 5 s later
    return rec.finish()


def play_known_and_unknown() -> dict:
    """Rule 6's sample half: a tap plays the role's declared `tick` sample,
    a hold plays `not_a_real_sample`, a name the role never declares, and
    the device is expected to ignore the unknown one and carry on. The
    hello at 5000 is the proof that it carried on.

    Both gestures land inside the opening signature deliberately: this
    scenario is about `/$DEV/play`, and taps placed later would record a
    second light glide that teaches nothing new here.
    """
    rec = Recorder(name="play_known_and_unknown",
                   summary="A known sample plays; an unknown name is "
                           "ignored and later steps still pass",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.tap(200, duration_ms=80.0)
    rec.expect_play(200, "tick")
    rec.hold(700, held_s=0.65)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    return rec.finish()


def link_loss_rejoin() -> dict:
    """Rule 7: there is no session resume. The link drops, the heartbeat
    stops with it, and 15 s after the last hello Control reaps the device:
    it fades the look out and sends `/$DEV/release` into a link that is not
    there to hear it. When the link comes back the device hellos and joins
    again from scratch, and the join is granted again.

    A replay runner must NOT deliver any `control_sends` step while the
    scenario's link is down. The 27 `/$DEV/leds` frames and the
    `/$DEV/release` recorded between `link: down` at 2000 and `link: up` at
    17000 are what Control really sends; the device never receives any of
    them, which is the whole point of the scenario.
    """
    rec = Recorder(name="link_loss_rejoin",
                   summary="After 15 s of silence Control drops the device; "
                           "it hellos and joins again when the link is back",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_join(0, CONTRACT_PLAYER_NODE)
    rec.link_down(2000)
    rec.advance_to(17000)                    # 17 s of silence: past the 15 s
    rec.link_up(17000)
    rec.expect_hello(17000)
    rec.expect_join(17000, CONTRACT_PLAYER_NODE)
    rec.advance_to(17500)
    return rec.finish()


def error_no_state_change() -> dict:
    """An `/$DEV/error` changes nothing. The tap at 200 arrives with no
    role and no lobby to receive it and is refused with `error ss ["tap",
    "device not registered"]`; the `/$DEV/room` snapshot after it is
    identical to the one before it, the heartbeat keeps its cadence, and a
    join 6 s later is still granted.
    """
    rec = Recorder(name="error_no_state_change",
                   summary="A tap with no lobby and no role draws an /error "
                           "that changes nothing; a later join still works",
                   join_node=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.tap(200, duration_ms=80.0)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.join_now(6000, CONTRACT_PLAYER_NODE)
    rec.advance_to(6300)
    return rec.finish()


def malformed_dropped() -> dict:
    """Rule 6's message half: an unknown verb, a `/role` carrying a string
    where the contract says blob, and a `/leds` blob that is not a list are
    all dropped without changing anything, and the device keeps working.

    Those three steps are hand-authored and flagged `malformed`, not
    recorded: nothing real ever sends them, there is no hub in this rig to
    route a broken message from, and a replay runner's own transport is
    what actually has to drop them. Everything else in the file is real.
    """
    rec = Recorder(name="malformed_dropped",
                   summary="An unknown verb, a bad typespec and a non-list "
                           "leds arg are dropped; the device carries on",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(200)
    rec.control_send_now("/$DEV/bogus", "s", ["hello"], malformed=True)
    rec.control_send_now("/$DEV/role", "s", ["not json"], malformed=True)
    rec.control_send_now("/$DEV/leds", "b", ["not a list"], malformed=True)
    rec.tap(700, duration_ms=80.0)
    rec.expect_play(700, "tick")             # a later gesture round-trips
    rec.advance_to(SIGNATURE_SETTLED_MS)
    rec.expect_frame(SIGNATURE_SETTLED_MS)   # a later frame still shows
    rec.advance_to(5000)
    rec.expect_hello(5000)
    return rec.finish()


def link_loss_keeps_display() -> dict:
    """Rule 8's two device-side halves, and the fresh join a compliant
    device sends once the link is back: a lost link ends the device's
    held role, in every profile, and a rev1 device (the board, and the
    app's rev1 profile) keeps its last frame lit through the loss until a
    fresh role's own frames replace it. The heartbeat halts while the
    link is down (rule 1's own "while the link stays up").

    "Role ends" is not itself something a device sends over the wire, so
    nothing here checks it directly. What IS wire-observable, and what
    this scenario actually pins, is its consequence: a device that
    correctly cleared its role when the link fell re-joins from scratch
    once the link is back (the `expect_out` pair at t=17000), exactly as
    `link_loss_rejoin` already pins for Control's own 15 s reap. A device
    that wrongly went on believing it still held a role across the outage
    would have no reason to send that join again.

    The outage-window `expect_frame` is HAND-AUTHORED
    (`Recorder.expect_frame_held`), like the pair in
    `timed_frames_hold_last`, and for the same structural reason: this
    recorder can only observe what Control sends, and during a link loss
    the device hears none of it. Control itself stays quiet here until its
    own 15 s stale timeout starts the reap fade (at t=15018 -- well after
    this scenario's own outage check at t=8000), but a runner replaying
    this file must not depend on that timing either way; what the device
    is still showing has to be told to the recording directly, as whatever
    had already reached it before the link fell.

    Whether a real Rev 1 board keeps its pixels lit through a link loss,
    rather than going dark, is inferred from the firmware plan's A2 code,
    not measured on hardware; Victor confirms it on the bench (spec
    section 7) before mm-devshroom's replay tests adopt this scenario.
    """
    rec = Recorder(name="link_loss_keeps_display",
                   summary="A lost link ends the role and halts the "
                           "heartbeat; a rev1 device keeps its last frame "
                           "lit through the outage and rejoins once the "
                           "link is back",
                   join_node=CONTRACT_PLAYER_NODE)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_join(0, CONTRACT_PLAYER_NODE)
    rec.advance_to(SIGNATURE_SETTLED_MS)
    rec.expect_frame(SIGNATURE_SETTLED_MS)          # the settled role look
    rec.link_down(SIGNATURE_SETTLED_MS)
    rec.expect_quiet(SIGNATURE_SETTLED_MS,
                     ["/game/hello", *POST_ROLE_GESTURES], 15000)
    rec.expect_frame_held(8000, since_t_ms=SIGNATURE_SETTLED_MS)
    rec.advance_to(17000)                           # 17 s of silence: past the 15 s
    rec.link_up(17000)
    rec.expect_hello(17000)
    rec.expect_join(17000, CONTRACT_PLAYER_NODE)
    rec.advance_to(17000 + SIGNATURE_SETTLED_MS)
    rec.expect_frame(17000 + SIGNATURE_SETTLED_MS)  # the fresh role's settled look
    return rec.finish()


ALL_SCENARIOS: tuple[Callable[[], dict], ...] = (
    boot_hello_heartbeat,
    explicit_join_role,
    lobby_tap_join,
    timed_frames_hold_last,
    gestures_after_role,
    deny_stays_hellod,
    release_keeps_display,
    play_known_and_unknown,
    link_loss_rejoin,
    error_no_state_change,
    malformed_dropped,
    link_loss_keeps_display,
)
