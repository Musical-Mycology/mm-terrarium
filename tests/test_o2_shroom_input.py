"""The operator-input bridge: browser gestures -> /game/* sends.

Everything here is socket-free: drain_gestures takes an injected send
and an injected now, so the o2lite stamp-at-source rule (Design Rule 4)
is asserted without o2litepy present."""

from __future__ import annotations

import queue

from harness.o2_shroom import (INPUT_QUEUE_MAX, SWEEP_RESUME_SECONDS,
                               discard_pre_role, drain_gestures,
                               enqueue_input)


def _q(*msgs, stamp=None):
    q = queue.Queue(maxsize=INPUT_QUEUE_MAX)
    for m in msgs:
        enqueue_input(q, m, stamp=stamp)
    return q


def test_tap_maps_to_the_documented_wire_row_stamped_at_now():
    sent = []
    got = drain_gestures(_q({"type": "tap", "count": 2}),
                         lambda *a: sent.append(a), "ie1", now=12.5)
    assert sent == [("/game/tap", 12.5, "sffi", "ie1", 1.0, 50.0, 2)]
    assert got is None                    # a tap is not a tilt


def test_tilt_maps_clamped_and_reports_operator_tilt_time():
    sent = []
    got = drain_gestures(_q({"type": "tilt", "gamma": 120.0}),
                         lambda *a: sent.append(a), "ie1", now=3.0)
    assert sent == [("/game/tilt", 3.0, "sf", "ie1", 90.0)]
    assert got == 3.0


def test_tap_count_defaults_to_1_and_is_clamped_to_at_least_1():
    sent = []
    drain_gestures(_q({"type": "tap"}, {"type": "tap", "count": 0}),
                   lambda *a: sent.append(a), "ie1", now=1.0)
    assert [s[6] for s in sent] == [1, 1]


def test_unknown_type_and_bad_fields_are_dropped():
    sent = []
    got = drain_gestures(
        _q({"type": "shake"}, {"type": "tilt", "gamma": "sideways"},
           {"no_type": True}, {"type": "tilt"}),
        lambda *a: sent.append(a), "ie1", now=1.0)
    assert sent == []
    assert got is None


# The granted role blob, as far as drain_gestures reads it: RoleTable's
# `uses` list (bits/rev1/rev1_bit.py lists all three Rev 1 verbs).
REV1_ROLE = {"uses": ["tap", "hold", "swing"]}


def test_hold_maps_to_the_documented_wire_row_stamped_at_touch_down():
    """/game/hold sfi [dev, held_seconds, count], stamped at touch-down
    (devicelink/contract.py): the page sends on release, so the stamp is
    the release stamp minus the time held."""
    sent = []
    got = drain_gestures(_q({"type": "hold", "held_seconds": 1.5}, stamp=10.0),
                         lambda *a: sent.append(a), "ie1", now=99.0,
                         config=REV1_ROLE)
    assert sent == [("/game/hold", 8.5, "sfi", "ie1", 1.5, 1)]
    assert got is None                    # a hold is not a tilt


def test_swing_maps_to_the_documented_wire_row_stamped_at_onset():
    sent = []
    got = drain_gestures(_q({"type": "swing", "signed_peak_g": -2.0},
                            {"type": "swing", "signed_peak_g": 2.0}),
                         lambda *a: sent.append(a), "ie1", now=4.0,
                         config=REV1_ROLE)
    assert sent == [("/game/swing", 4.0, "sfi", "ie1", -2.0, 1),
                    ("/game/swing", 4.0, "sfi", "ie1", 2.0, 1)]
    assert got is None


def test_hold_and_swing_wait_for_a_role():
    """pre_role is false for both verbs: with no role blob in hand
    (config None) neither goes out."""
    sent = []
    drain_gestures(_q({"type": "hold", "held_seconds": 1.0},
                      {"type": "swing", "signed_peak_g": 2.0}),
                   lambda *a: sent.append(a), "ie1", now=1.0)
    assert sent == []


def test_a_role_without_hold_gets_a_long_press_as_a_tap():
    """TestBit and MetronomeBit roles do not list hold. A long press on
    the page was a tap before hold existed, and stays one for them rather
    than earning an `unknown verb 'hold'` error from Control."""
    sent = []
    drain_gestures(_q({"type": "hold", "held_seconds": 1.0}),
                   lambda *a: sent.append(a), "ie1", now=2.0,
                   config={"uses": ["tap"]})
    assert sent == [("/game/tap", 2.0, "sffi", "ie1", 1.0, 50.0, 1)]


def test_a_role_without_swing_drops_it(capsys):
    sent = []
    drain_gestures(_q({"type": "swing", "signed_peak_g": 2.0}),
                   lambda *a: sent.append(a), "ie1", now=2.0,
                   config={"uses": ["tap", "tilt"]})
    assert sent == []
    # Said out loud: a silent drop reads as a broken page.
    assert "swing ignored: this role does not use swing" in capsys.readouterr().out


def test_pre_role_gestures_are_discarded_and_reported(capsys):
    """Before a role, a click must not wait in the queue and then burst out
    with stale stamps the moment the role lands; it is dropped, and the
    operator is told why."""
    q = _q({"type": "tap", "count": 1}, {"type": "swing", "signed_peak_g": 2.0})
    assert discard_pre_role(q, "waiting for a role from Control") == 2
    assert q.empty()
    out = capsys.readouterr().out
    assert "2 gesture(s) ignored: waiting for a role from Control" in out
    assert discard_pre_role(q, "x") == 0
    assert capsys.readouterr().out == ""


def test_a_role_declaring_no_uses_gets_neither_rev1_verb():
    """wants_verb is permissive for a role with no `uses`, which is right
    for the legacy tilt sweep but not here: no Bit written before Rev 1
    handles hold or swing, so only an explicit listing opts in."""
    sent = []
    drain_gestures(_q({"type": "hold", "held_seconds": 1.0},
                      {"type": "swing", "signed_peak_g": 2.0}),
                   lambda *a: sent.append(a), "ie1", now=2.0, config={})
    assert sent == [("/game/tap", 2.0, "sffi", "ie1", 1.0, 50.0, 1)]


def test_bad_hold_and_swing_fields_are_dropped():
    sent = []
    drain_gestures(_q({"type": "hold"}, {"type": "hold", "held_seconds": "x"},
                      {"type": "hold", "held_seconds": -1.0},
                      {"type": "hold", "held_seconds": float("inf")},
                      {"type": "swing"}, {"type": "swing", "signed_peak_g": None},
                      {"type": "swing", "signed_peak_g": float("nan")}),
                   lambda *a: sent.append(a), "ie1", now=1.0, config=REV1_ROLE)
    assert sent == []


def test_drain_empties_the_queue():
    q = _q({"type": "tap", "count": 1})
    drain_gestures(q, lambda *a: None, "ie1", now=1.0)
    assert q.empty()


def test_enqueue_drops_oldest_on_overflow():
    q = queue.Queue(maxsize=2)
    enqueue_input(q, {"type": "tap", "count": 1}, stamp=None)
    enqueue_input(q, {"type": "tap", "count": 2}, stamp=None)
    enqueue_input(q, {"type": "tap", "count": 3}, stamp=None)
    sent = []
    drain_gestures(q, lambda *a: sent.append(a), "ie1", now=1.0)
    assert [s[6] for s in sent] == [2, 3]


def test_gesture_carries_enqueue_stamp_not_drain_time():
    q = queue.Queue(maxsize=8)
    enqueue_input(q, {"type": "tap", "count": 1}, stamp=12.345)
    sent = []
    drain_gestures(q, lambda *a: sent.append(a), "ie1", now=99.0)
    address, when, typespec, dev, peak, dur, count = sent[0]
    assert address == "/game/tap"
    assert when == 12.345          # the enqueue-time stamp, not 99.0


def test_build_wires_the_queue_into_the_backend():
    from harness.o2_shroom import build
    q = queue.Queue(maxsize=INPUT_QUEUE_MAX)
    client, backend = build("ie1", serve=False, input_queue=q)
    try:
        assert backend.on_input is not None
        backend.on_input({"type": "tap", "count": 1})
        stamp, msg = q.get_nowait()
        assert msg == {"type": "tap", "count": 1}
        assert stamp is None  # build() defaults clock=None -> no clock wired
    finally:
        backend.close()


def test_build_without_queue_leaves_the_backend_input_free():
    from harness.o2_shroom import build
    client, backend = build("ie1", serve=False)
    try:
        assert backend.on_input is None
    finally:
        backend.close()


def test_sweep_resume_window_is_five_seconds():
    assert SWEEP_RESUME_SECONDS == 5.0
