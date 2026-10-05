"""The device contract kit scenarios, contract v3 (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md sections 4.3 and 5.4, as amended by
2026-10-01-instrument-handshake-protocol-design.md section 6). There are
len(ALL_SCENARIOS) of them: eighteen.

Each function takes no arguments, builds its own Recorder, and returns an
EXPORT FORMAT v1 scenario dict recorded from the REAL Control engine.
tools/record_scenarios.py writes them to contract_kit/recordings/;
tests/test_contract_scenarios.py re-runs them and fails on any diff against
the committed files, and also checks that each recording still contains the
behavior its spec table row names.

These files are the contract two other repos learn device behavior from (the
Flutter Tuneshroom app and the Rev 1 ESP32 firmware), so where Control's real
behavior differs from the spec's prose the RECORDING wins and the difference
is stated in the scenario's own docstring. The differences found while
recording:

1. A newly granted role plays a ~1.5 s opening signature before the role's
   own light manifest reaches the pixels, and that signature ignores light
   cues. A timed look sent during it changes nothing a device can see, so
   `timed_frames_hold_last` waits for the signature to settle before it
   taps. See that scenario's docstring.
2. The closing fade's last frame is a dim non-black frame, not black, and
   `/$DEV/release` follows it on the wire in the same millisecond while
   carrying no presentation time of its own. See `release_keeps_display`
   for the spec section 5.5 ordering answer.
3. The round id Control mints per Bit load carries a random token, so it is
   recorded as the `$ROUND` placeholder: "whatever the latest
   `/$DEV/handshake` carried".
4. The role blob's `class` value is the Role class name in upper case
   ("UNIQUE", "JAM"), not the lower-case "jam" the kit brief used.
5. A v3 device sends no gesture before a role, tap included, so the v2
   pre-role tap is gone from `gestures_after_role` and
   `error_no_state_change` now draws its error from a refused jammer hold.
6. A `/game/join` is not a contract verb any more, so `join_retired_error`
   records Control's answer to one without any step for the join itself.

One rule for replay runners, which applies to every scenario and bites in
`link_loss_rejoin`, `link_loss_keeps_display` and `link_blip_keeps_role`:
a runner must not deliver
any `control_sends` step while the scenario's link is down. Those steps
record what Control really sends, and a device with its link down receives
none of them.
"""
from __future__ import annotations

from typing import Callable

from contract_kit.contract_bit import CONTRACT_PLAYER_NODE
from contract_kit.recorder import ROOM_NODE_ID, Recorder

# A node name no Bit's role table declares. GameServer.handshake() refuses
# any node absent from the role table's node_map, so this is how a scenario
# asks for a deny without inventing a second Bit.
NO_SUCH_NODE = "NO_SUCH_NODE"

# The gesture verbs held back before a role besides tap (spec section 4.3,
# rule 2; devicelink/contract.py's pre_role column). Tap is listed beside
# them where a scenario needs it: in contract v3 it waits for a role too.
POST_ROLE_GESTURES = ["/game/hold", "/game/swing"]

# How long ContractBit's player-role opening signature runs before the
# role's own light manifest reaches the pixels. Measured from the
# recordings: the last signature frame is sent about 1518 ms after the role
# arrives. Scenarios that need a light cue to be VISIBLE, or an expect_frame
# that is not within one render tick of a frame change, wait past this.
SIGNATURE_SETTLED_MS = 2000

# The common v3 timeline: the device's device.handshake policy accepts
# ACCEPT_AFTER_MS after its first invite (which arrives with its first
# hello, at t=0), the operator starts at START_T, and the role's look has
# settled by ROLE_SETTLED_T.
ACCEPT_AFTER_MS = 300
ACCEPT_POLICY = {"node": "", "ack_after_ms": ACCEPT_AFTER_MS}
START_T = 1000
ROLE_SETTLED_T = START_T + SIGNATURE_SETTLED_MS
# handshake_validate_then_role starts later, after the validation
# ceremony's chime (about 1.8 s after the accept) has played.
VALIDATE_START_T = 3000
# Ready (light lexicon): the validation ceremony's green flashes end 800 ms
# after the accept, then a 4 s green pulse rises from dark; this check sits
# near its first peak, where the frame barely moves within a tolerance.
READY_PULSE_CHECK_T = 2900
# deny_stays_hellod with the Room's lobby up: red x2 from the deny at
# ACCEPT_AFTER_MS (this check is mid first flash), then the white invite
# pulse resumes 800 ms after the deny and peaks 2 s later.
DENY_FLASH_CHECK_T = 400
DENY_PULSE_CHECK_T = 3100

# handshake_over_cap_deny: the uncaptured rival validates first.
RIVAL_ACCEPT_T = 100
# handshake_stale_round.
STALE_ROUND_ID = "stale"
STALE_ACCEPT_T = 300
GOOD_ACCEPT_T = 1000
# late_hello_gets_jam: the link comes up after the round has started.
WALK_UP_T = 500
# room_node_handshake_binds.
ARM_T = 500
ROOM_ACCEPT_T = 1000
# join_retired_error.
LEGACY_JOIN_T = 200
# link_loss_keeps_display: the link is back past the 15 s stale timeout.
LINK_BACK_T = 18000
# link_blip_keeps_role: the link drops at ROLE_SETTLED_T and is back well
# inside the 15 s stale timeout (the device last spoke at ACCEPT_AFTER_MS),
# then a tap proves the role was kept.
BLIP_BACK_T = 8000
BLIP_TAP_T = 9000

# timed_frames_hold_last. The look's glide (taps at ROLE_SETTLED_T) has
# stopped sending frames by LOOK_SETTLED_T. Then the hand-authored pair:
# two frames on one send time with different presentation times, in colors
# no real frame in any recording carries (pure blue and pure red in GRB
# order), so a device showing the wrong one is unmistakable.
LOOK_SETTLED_T = 6500
AUTHORED_NEWER_GRB = [0, 0, 255] * 12
AUTHORED_OLDER_GRB = [255, 0, 0] * 12
AUTHORED_PAIR_T = 7000
AUTHORED_NEWER_AT = 7200
AUTHORED_OLDER_AT = 7100
AUTHORED_CHECK_T = 7500
HOLD_CHECK_T = 13000


def boot_hello_heartbeat() -> dict:
    """Rule 1: hello goes out once the link is up and repeats every 5 s,
    declaring the carried instrument in its fourth argument, and a device
    that never accepts sends nothing else at all.

    Control answers the FIRST hello with one `/$DEV/room` snapshot and the
    round's `/$DEV/handshake` invite, and repeats the invite every 5 s
    (the invite cycle) while the device stays un-validated. It does NOT
    answer every heartbeat with a `/room` any more (contract v3): the
    snapshot goes out on first contact and on state or registration
    changes only.

    The instrument name itself is recorded as the `*` placeholder: which
    instrument a device carries is its own business, and only the four
    argument `ssss` shape is contract.
    """
    rec = Recorder(name="boot_hello_heartbeat",
                   summary="Hello repeats every 5 s and declares an "
                           "instrument; a device that never accepts sends "
                           "nothing else",
                   handshake=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_quiet(0, ["/game/handshake", "/game/tap", *POST_ROLE_GESTURES],
                     12000)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.advance_to(10000)
    rec.expect_hello(10000)
    rec.advance_to(12000)
    return rec.finish()


def handshake_validate_then_role() -> dict:
    """The v3 entry path, end to end with a Room's lobby loaded: Hello,
    Handshake, Received Handshake, Validated, then the role at RUNNING
    (the spec's player-flow diagram).

    Control invites the device with `/$DEV/handshake [$ROUND]` on its first
    hello, and the lobby's white invite flash starts on `/$DEV/leds`. The
    device's `device.handshake` policy accepts 300 ms later with
    `/game/handshake ["$DEV", "$ROUND", ""]` (an empty node asks for the
    Bit's default scored role), and Control answers `/$DEV/validated
    ["$ROUND", "player"]` plus a fresh `/$DEV/room` whose player count is
    now 1. The validation ceremony follows: green flashes, then `/play
    ["chime", "key=$KEY"]` about 1.8 s later. Validation cancels the
    invite's second white flash, which was already queued, so nothing
    white follows `/validated` (and only `/$DEV/handshake` is ever an
    invite in any case).
    The white invite pulse never shows here: it is held dark until 800 ms
    after the invite's flashes, and the accept at 300 ms comes first. Once
    the ceremony's green flashes are done, a dim green pulse (the light
    lexicon's Ready) holds until `/$DEV/role`; READY_PULSE_CHECK_T checks
    it near its peak.

    Validated is a reservation, not a role: `/$DEV/role` (scored, `class`
    "UNIQUE") arrives only when the operator starts the round at
    VALIDATE_START_T, followed by the role's opening signature.

    The start is at VALIDATE_START_T (3000 ms), not the kit brief's
    1000 ms: the ceremony's chime lands about 2117 ms in, and a start
    before it would record the round going RUNNING mid-ceremony instead of
    the whole player flow (recording difference).

    The chime's key is recorded as `key=$KEY`. It is the validation index
    into a note scale, so a replaying device must reproduce the
    parameter's shape and not its number.
    """
    rec = Recorder(name="handshake_validate_then_role",
                   summary="Invited, the device accepts and is validated "
                           "with the lobby ceremony; the scored role "
                           "arrives at start",
                   handshake=ACCEPT_POLICY, with_room=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(150)
    rec.expect_frame(150)                    # the invite's first white flash
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.advance_to(400)
    rec.expect_frame(400)                    # the ceremony's first green flash
    rec.advance_to(READY_PULSE_CHECK_T)
    rec.expect_frame(READY_PULSE_CHECK_T)    # Ready: the dim green pulse
    rec.advance_to(VALIDATE_START_T)
    rec.expect_play(VALIDATE_START_T, "chime")
    rec.start(VALIDATE_START_T)
    rec.advance_to(VALIDATE_START_T + SIGNATURE_SETTLED_MS)
    rec.expect_frame(VALIDATE_START_T + SIGNATURE_SETTLED_MS)
    return rec.finish()


def handshake_over_cap_deny() -> dict:
    """The scored cap. ContractBit's one scored role is UNIQUE with
    capacity 1, so once another device has validated, this device's accept
    is refused with `/$DEV/deny ["scored full", <hint>]`, and at start it
    gets the Bit's jam role instead (`role` "jammer", `scored` false).

    The other device is real Control traffic from a second dev id that
    this recording never captures: to the device under test it shows up
    only as the `/$DEV/room` snapshot at t=100 whose player count went to
    1. Control's answer to a full lobby is a deny, not silence; the hint
    says a jam role follows at start.

    The role blob's `class` value is the Role class's own name, "JAM", in
    upper case (the brief for this kit said "jam"; the recording wins).
    """
    rec = Recorder(name="handshake_over_cap_deny",
                   summary="An accept after the scored slot is taken is "
                           "denied; the device gets the jam role at start",
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.rival_accept(RIVAL_ACCEPT_T)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)          # the jammer's settled look
    return rec.finish()


def handshake_stale_round() -> dict:
    """A `/game/handshake` whose round id is not the current round's is
    dropped without an answer: no `/validated`, no `/deny`, nothing. The
    device's later accept, echoing the round id its `/$DEV/handshake`
    actually carried, validates normally.

    The stale accept's round id is the literal "stale" in both the
    `accept` input step and its `expect_out`, because a runner has to make
    its device send exactly that; the good accept's is "$ROUND", the
    latest id the device received.
    """
    rec = Recorder(name="handshake_stale_round",
                   summary="An accept with a stale round id gets no answer; "
                           "an accept with the current one validates",
                   handshake=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.accept(STALE_ACCEPT_T, round_id=STALE_ROUND_ID)
    rec.accept(GOOD_ACCEPT_T)
    rec.advance_to(GOOD_ACCEPT_T + 500)
    return rec.finish()


def late_hello_gets_jam() -> dict:
    """A RUNNING walk-up. The round is already running when the device's
    link comes up, so its first hello is answered at once, in the same
    millisecond: a first-contact `/$DEV/room` saying RUNNING, then
    `/$DEV/role` for the jam role, then a second `/room` with the jam
    count raised. No invite and no handshake: scored slots open only in
    SETUP.
    """
    rec = Recorder(name="late_hello_gets_jam",
                   summary="A device that hellos into a running round gets "
                           "the jam role at once",
                   handshake=None)
    rec.start(0)
    rec.link_up(WALK_UP_T)
    rec.expect_hello(WALK_UP_T)
    rec.advance_to(WALK_UP_T + SIGNATURE_SETTLED_MS)
    rec.expect_frame(WALK_UP_T + SIGNATURE_SETTLED_MS)
    return rec.finish()


def jam_solo_fallback() -> dict:
    """A Bit with no jam role (SoloContractBit) still gives a device that
    never validated something alive at start: a solo role synthesized for
    its carried instrument, `role` "solo:tuneshroom_rev1", `class` "JAM",
    `scored` false.

    tuneshroom_rev1 declares no `[solo]` table and no ambient light, so
    the synthesized role carries an empty light manifest and no gesture
    bindings; what reaches the pixels is the generic opening signature
    every granted role plays, which settles dark. The role blob is the
    contract here, not the look.
    """
    rec = Recorder(name="jam_solo_fallback",
                   summary="With no jam role in the Bit, a device that never "
                           "accepted gets a solo role for its instrument",
                   handshake=None, bit="SoloContractBit")
    rec.link_up(0)
    rec.expect_hello(0)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)
    return rec.finish()


def room_node_handshake_binds() -> dict:
    """A handshake naming the Room's node binds the device to the fixture
    the operator armed. No `/validated` and no `/role` come back (a bound
    fixture is not a player); the fixture's own frames start on
    `/$DEV/leds` in the same millisecond.

    With a Room loaded, the lobby's white invite flash shows before the
    accept (the frames at t=0 and t=403), then the dim white invite pulse
    rises (the frames at t=914 and t=983) until the bind clears it,
    alongside the `/$DEV/handshake` invite itself. The node id is Control's own; the device learns it from
    an NFC tag or QR code, never from this wire.

    There is deliberately no `expect_frame` after the accept. Once bound,
    Control sends the device its FIXTURE's slice (the TEST fixture is 60 px
    GRB, 180 values), not a 12 px, 36-value player frame, so no 12-pixel
    device could pass a display check there. That the fixture's `/$DEV/leds`
    frames begin arriving at the accept is pinned by the recorded
    `control_sends` themselves (inputs a runner delivers); the only
    expectation a device can fail here is the `/game/handshake` it sends.
    The one `expect_frame` at t=150 is the pre-bind invite flash, a normal
    36-value frame.
    """
    rec = Recorder(name="room_node_handshake_binds",
                   summary="Accepting with the Room node binds an armed "
                           "fixture: no validated, no role, fixture frames",
                   handshake=None, with_room=True)
    rec.link_up(0)
    rec.expect_hello(0)
    # Near the middle of the invite's first 200 ms white flash.
    rec.advance_to(150)
    rec.expect_frame(150)
    rec.arm_fixture(ARM_T)
    rec.accept(ROOM_ACCEPT_T, node=ROOM_NODE_ID)
    rec.advance_to(ROOM_ACCEPT_T + 500)
    return rec.finish()


def join_retired_error() -> dict:
    """A contract v2 device's `/game/join` is answered with `/$DEV/error
    ["join", "retired in contract v3: use /game/handshake"]` and otherwise
    ignored: the device stays hello'd, keeps being invited, and at start
    gets the jam role like any device that never accepted.

    The join itself is NOT a step in this file. A v3 device never sends
    `/game/join` (firmware checklist item 8), so there is no input a
    runner could deliver and no `expect_out` a compliant device could
    pass. What a v3 device must do with the recorded `/error` is what
    `error_no_state_change` already pins: nothing.
    """
    rec = Recorder(name="join_retired_error",
                   summary="A v2 join draws the retirement /error and "
                           "changes nothing; the device still gets jam at "
                           "start",
                   handshake=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.legacy_join(LEGACY_JOIN_T, CONTRACT_PLAYER_NODE)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)
    rec.advance_to(5000)
    rec.expect_hello(5000)
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
    is that same hand-authored newer frame, still showing almost six
    seconds later.

    The role arrives at START_T (accept, then start), and the taps wait
    for its opening signature to settle on purpose. A newly granted
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
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)         # the settled role look
    rec.tap(ROLE_SETTLED_T, duration_ms=80.0)
    rec.tap(ROLE_SETTLED_T, duration_ms=80.0)
    rec.advance_to(LOOK_SETTLED_T)
    rec.expect_frame(LOOK_SETTLED_T)         # the look, settled and quiet
    rec.advance_to(AUTHORED_PAIR_T)
    # Hand-authored, newest presentation time FIRST (see the docstring).
    rec.control_send_now("/$DEV/leds", "b", [AUTHORED_NEWER_GRB],
                         at=AUTHORED_NEWER_AT)
    rec.control_send_now("/$DEV/leds", "b", [AUTHORED_OLDER_GRB],
                         at=AUTHORED_OLDER_AT)
    rec.advance_to(AUTHORED_CHECK_T)
    rec.expect_frame(AUTHORED_CHECK_T)       # the newer of the two, not the
                                             # one that arrived last
    rec.advance_to(HOLD_CHECK_T)
    rec.expect_frame(HOLD_CHECK_T)           # still holding, long after
    return rec.finish()


def gestures_after_role() -> dict:
    """The three gesture shapes and their stamps, and rule 2's pre-role
    window.

    In contract v3 a device sends no gesture at all before it holds a
    role, tap included (the verb table marks tap pre_role false; taps are
    always gameplay now that the lobby no longer reads them as a join).
    So tap, hold and swing are all held back under an `expect_quiet` from
    link-up until the role arrives at START_T. (The kit brief kept v2's
    pre-role tap here; a device sending it would break rule 2, so it is
    gone.)

    Once the role is held, all three are stamped at their own onset, tap
    carries `sffi [dev, peak_g, duration_ms, count]` with peak_g 0 and
    count 1, and hold and swing carry `sfi`.
    """
    rec = Recorder(name="gestures_after_role",
                   summary="No gesture goes out before a role; tap, hold "
                           "and swing all stamp at onset after it",
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.expect_quiet(0, ["/game/tap", *POST_ROLE_GESTURES], START_T)
    rec.start(START_T)
    rec.tap(START_T + 500, duration_ms=80.0)
    rec.hold(START_T + 1000, held_s=0.65)
    rec.swing(START_T + 1500, signed_g=-2.1)
    rec.advance_to(START_T + 2000)
    return rec.finish()


def deny_stays_hellod() -> dict:
    """An accept naming a node no role table declares is denied with
    `/$DEV/deny ["no such node", <hint>]`: no validation and no role, and
    the hello heartbeat keeps running on its own 5 s cadence. Control
    keeps inviting the device every 5 s; its `device.handshake` policy
    answers only the first invite of a link-up, so it accepts once.

    With a Room's lobby up, the deny is visible (the light lexicon's
    Failure): red x2 on `/$DEV/leds` from the deny, replacing the invite's
    still-queued second white flash, then the dim white invite pulse
    resumes, because the lobby is WAITING and the device is still invited.
    DENY_FLASH_CHECK_T checks the first red flash and DENY_PULSE_CHECK_T
    the pulse near its peak.
    """
    rec = Recorder(name="deny_stays_hellod",
                   summary="An accept naming an unknown node is denied with "
                           "a red flash; the device stays hello'd and "
                           "invited with its heartbeat running",
                   handshake={"node": NO_SUCH_NODE,
                              "ack_after_ms": ACCEPT_AFTER_MS},
                   with_room=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.advance_to(DENY_FLASH_CHECK_T)
    rec.expect_frame(DENY_FLASH_CHECK_T)     # Failure: the first red flash
    rec.advance_to(DENY_PULSE_CHECK_T)
    rec.expect_frame(DENY_PULSE_CHECK_T)     # still invited: the white pulse
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
    the release before the final fade frame is due to show. (`/release`
    travels over TCP and `/leds` over UDP in contract v3, so on a real link
    the two are not even on one stream.)

    Two details a device author should not mistake for rig artifacts: the
    fade's last frame is a dim non-black frame rather than black, and the
    `/$DEV/room` snapshot that follows says IDLE with no Bit, because the
    Bit really has been unloaded.
    """
    rec = Recorder(name="release_keeps_display",
                   summary="Unloading the Bit fades and releases a device "
                           "holding a role; the last fade frame holds and "
                           "the heartbeat continues",
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)         # the settled role look
    rec.unload_bit()
    rec.advance_to(4000)
    rec.expect_frame(4000)                   # the last fade frame
    rec.expect_quiet(4000, ["/game/tap", *POST_ROLE_GESTURES], 5000)
    rec.advance_to(5000)
    rec.expect_hello(5000)                   # the heartbeat, after release
    rec.advance_to(9000)
    rec.expect_frame(9000)                   # still holding, 5 s later
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
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.tap(START_T + 200, duration_ms=80.0)
    rec.expect_play(START_T + 200, "tick")
    rec.hold(START_T + 700, held_s=0.65)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    return rec.finish()


def link_loss_rejoin() -> dict:
    """Guide rule 9 (checklist item 7), in SETUP: the link drops, the
    heartbeat stops with it, and 15 s after the device last spoke Control
    reaps it. The device itself keeps its round id and its validation
    across the loss; it has no way to know it was reaped. When the link
    comes back it hellos, and Control, which no longer knows it, invites
    it again with `/$DEV/handshake`. That invite supersedes whatever the
    device held: it takes the invite's round id (the same `$ROUND`, since
    the Bit was never reloaded), accepts again, and is validated again.
    The round stays in SETUP throughout, so this is the validation half;
    `link_loss_keeps_display` and `link_blip_keeps_role` are the halves
    with a role held.

    The reaped device held a validation but no role, so the reap sends
    nothing to it at all (no fade, no `/release`): there was nothing to
    end on the device. Nothing else is sent while the link is down either:
    a validated device is owed no invite, and once reaped it is not in the
    pool. The rule that a runner must not deliver a `control_sends` step
    inside a down window still holds here; this recording simply has none
    to skip.
    """
    rec = Recorder(name="link_loss_rejoin",
                   summary="After 15 s of silence Control drops the device; "
                           "it hellos, is invited and validates again when "
                           "the link is back",
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.link_down(2000)
    rec.advance_to(17000)                    # 17 s of silence: past the 15 s
    rec.link_up(17000)
    rec.expect_hello(17000)
    rec.advance_to(17000 + ACCEPT_AFTER_MS)
    rec.expect_handshake_out(17000 + ACCEPT_AFTER_MS)
    rec.advance_to(17500)
    return rec.finish()


def error_no_state_change() -> dict:
    """An `/$DEV/error` changes nothing. The device never accepts, so at
    start it holds the jam role, whose handler refuses a hold with
    `/$DEV/error ["hold", "jammer role uses tap only"]`. Nothing else
    follows it: no `/room`, no `/role`, no `/release`. The heartbeat
    keeps its cadence, and a tap afterwards still plays `tick`.

    In contract v2 this error came from a pre-role tap with no lobby to
    receive it. A v3 device sends no gesture before a role (the verb table
    marks tap pre_role false), so the error now comes from a gesture the
    role's own handler refuses (the kit brief kept the pre-role tap; the
    recording wins).
    """
    rec = Recorder(name="error_no_state_change",
                   summary="A refused gesture draws an /error that changes "
                           "nothing; a later tap still plays",
                   handshake=None)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.start(START_T)
    rec.hold(START_T + 500, held_s=0.65)
    rec.advance_to(5000)
    rec.expect_hello(5000)
    rec.tap(6000, duration_ms=80.0)
    rec.expect_play(6000, "tick")
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
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(START_T + 200)
    rec.control_send_now("/$DEV/bogus", "s", ["hello"], malformed=True)
    rec.control_send_now("/$DEV/role", "s", ["not json"], malformed=True)
    rec.control_send_now("/$DEV/leds", "b", ["not a list"], malformed=True)
    rec.tap(START_T + 700, duration_ms=80.0)
    rec.expect_play(START_T + 700, "tick")   # a later gesture round-trips
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)         # a later frame still shows
    rec.advance_to(5000)
    rec.expect_hello(5000)
    return rec.finish()


def link_loss_keeps_display() -> dict:
    """Guide rule 9 (checklist item 7) past the 15 s stale timeout while
    RUNNING: the device KEEPS its held role and round id across the loss,
    in every profile, and a rev1 device (the board, and the app's rev1
    profile) keeps its last frame lit through the loss. The heartbeat
    halts while the link is down (rule 1's own "while the link stays up").

    Control reaps the silent device during the outage, so the fade and
    `/$DEV/release` it sends then are never heard. Once the link is back
    the device hellos (t=LINK_BACK_T); Control no longer knows it, so that
    hello is a RUNNING walk-up and is answered with a fresh `/$DEV/role`,
    the JAM role this time, not the scored role the device still holds (a
    scored slot is only ever won in SETUP). The new `/role` supersedes the
    held one, and its frames replace the held look. A device that dropped
    its role on the loss passes this file too; `link_blip_keeps_role` is
    the scenario that tells the two apart.

    The outage-window `expect_frame` is HAND-AUTHORED
    (`Recorder.expect_frame_held`), like the pair in
    `timed_frames_hold_last`, and for the same structural reason: this
    recorder can only observe what Control sends, and during a link loss
    the device hears none of it. Control itself stays quiet here until its
    own 15 s stale timeout starts the reap fade (well after this
    scenario's own outage check), but a runner replaying this file must
    not depend on that timing either way; what the device is still showing
    has to be told to the recording directly, as whatever had already
    reached it before the link fell.

    Whether a real Rev 1 board keeps its pixels lit through a link loss,
    rather than going dark, is inferred from the firmware plan's A2 code,
    not measured on hardware; Victor confirms it on the bench (spec
    section 7) before mm-devshroom's replay tests adopt this scenario.
    """
    rec = Recorder(name="link_loss_keeps_display",
                   summary="A lost link halts the heartbeat; a rev1 device "
                           "keeps its last frame lit through the outage, "
                           "and once the link is back past the 15 s "
                           "timeout a fresh jam role replaces the held one",
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)                # the settled role look
    rec.link_down(ROLE_SETTLED_T)
    rec.expect_quiet(ROLE_SETTLED_T,
                     ["/game/hello", "/game/tap", *POST_ROLE_GESTURES],
                     LINK_BACK_T - ROLE_SETTLED_T)
    rec.expect_frame_held(9000, since_t_ms=ROLE_SETTLED_T)
    rec.advance_to(LINK_BACK_T)                     # past the 15 s timeout
    rec.link_up(LINK_BACK_T)
    rec.expect_hello(LINK_BACK_T)
    rec.advance_to(LINK_BACK_T + SIGNATURE_SETTLED_MS)
    rec.expect_frame(LINK_BACK_T + SIGNATURE_SETTLED_MS)  # the jam role's look
    return rec.finish()



def link_blip_keeps_role() -> dict:
    """Guide rule 9 (checklist item 7) inside the 15 s stale timeout while
    RUNNING: the link drops at t=ROLE_SETTLED_T and is back at
    t=BLIP_BACK_T, before Control has noticed anything. Control sends
    nothing new: no `/$DEV/role`, no `/$DEV/handshake`, no `/$DEV/release`,
    no `/$DEV/room`. The device must therefore still hold the scored role
    it had: it hellos on link-up and every 5 s from there, and a tap at
    t=BLIP_TAP_T goes out and plays the role's `tick`. A device that
    dropped its role on the loss would hold back that tap (rule 2) and
    fail, and nothing would ever give it a role again this round.
    """
    rec = Recorder(name="link_blip_keeps_role",
                   summary="A link that drops and returns inside 15 s while "
                           "RUNNING changes nothing: no role is re-sent and "
                           "the held role still plays",
                   handshake=ACCEPT_POLICY)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)                # the settled role look
    rec.link_down(ROLE_SETTLED_T)
    rec.expect_quiet(ROLE_SETTLED_T,
                     ["/game/hello", "/game/tap", *POST_ROLE_GESTURES],
                     BLIP_BACK_T - ROLE_SETTLED_T)
    rec.link_up(BLIP_BACK_T)
    rec.expect_hello(BLIP_BACK_T)
    rec.tap(BLIP_TAP_T, duration_ms=80.0)
    rec.expect_play(BLIP_TAP_T, "tick")
    rec.advance_to(BLIP_BACK_T + 5000)
    rec.expect_hello(BLIP_BACK_T + 5000)
    rec.advance_to(BLIP_BACK_T + 5500)
    return rec.finish()

ALL_SCENARIOS: tuple[Callable[[], dict], ...] = (
    boot_hello_heartbeat,
    handshake_validate_then_role,
    handshake_over_cap_deny,
    handshake_stale_round,
    late_hello_gets_jam,
    jam_solo_fallback,
    room_node_handshake_binds,
    join_retired_error,
    timed_frames_hold_last,
    gestures_after_role,
    deny_stays_hellod,
    release_keeps_display,
    play_known_and_unknown,
    link_loss_rejoin,
    error_no_state_change,
    malformed_dropped,
    link_loss_keeps_display,
    link_blip_keeps_role,
)
