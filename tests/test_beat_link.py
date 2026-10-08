"""BeatLink: the device side of the beat heartbeat (spec 2026-10-08
section 6.1). Jitter 0 throughout, so every time is exact."""

from harness.beat_link import (DOWN, LINKED, LINKING, LOOKING, SOLO, BeatLink,
                               DropRole, DropTransport, SendBeat, SendHello,
                               StateChanged)


def _bl():
    return BeatLink(jitter=0.0)


def _sends(actions, kind):
    return [a for a in actions if isinstance(a, kind)]


def test_link_up_sends_hello_and_beat_zero():
    bl = _bl()
    acts = bl.link_up(0.0)
    assert acts[:2] == [SendHello(), SendBeat(0, 0)]
    assert StateChanged(LINKING) in acts
    assert bl.state == LINKING and bl.armed is False


def test_beats_every_interval_with_increasing_seq():
    bl = _bl()
    bl.link_up(0.0)
    assert _sends(bl.tick(0.5), SendBeat) == []
    assert _sends(bl.tick(1.0), SendBeat) == [SendBeat(1, 0)]
    assert _sends(bl.tick(2.0), SendBeat) == [SendBeat(2, 0)]


def test_first_reply_arms_and_links_and_measures_rtt():
    bl = _bl()
    bl.link_up(0.0)
    acts = bl.on_beat_reply(0.025, 0, "abc123")
    assert StateChanged(LINKED) in acts
    assert bl.armed and bl.state == LINKED
    assert bl.epoch == "abc123" and bl.rtt_ms == 25
    assert _sends(bl.tick(1.0), SendBeat) == [SendBeat(1, 25)]


def test_unarmed_keeps_the_legacy_hello_and_never_looks():
    bl = _bl()
    bl.link_up(0.0)
    hellos = []
    for t in range(1, 21):
        hellos += _sends(bl.tick(float(t)), SendHello)
    assert len(hellos) == 4                   # t = 5, 10, 15, 20
    assert bl.state == LINKING


def test_three_seconds_of_silence_means_looking_and_drop_transport():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    assert StateChanged(LOOKING) not in bl.tick(2.99)
    acts = bl.tick(3.0)
    assert DropTransport() in acts and StateChanged(LOOKING) in acts
    assert bl.state == LOOKING
    assert _sends(bl.tick(4.0), SendBeat) == []        # stops beating


def test_any_control_message_resets_the_lost_timer():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.on_control_message(2.5)               # e.g. a /leds frame
    bl.tick(5.0)
    assert bl.state == LINKED
    assert StateChanged(LOOKING) in bl.tick(5.5)


def test_solo_at_grace_after_the_last_message():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(3.0)                             # LOOKING
    assert bl.tick(14.9) == []
    assert StateChanged(SOLO) in bl.tick(15.0)


def test_relink_keeps_looking_until_the_first_reply():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(3.0)                             # LOOKING, transport dropped
    acts = bl.link_up(5.0)
    assert acts[:2] == [SendHello(), SendBeat(0, 0)]   # seq restarts
    assert bl.state == LOOKING and bl.armed is False
    assert StateChanged(LINKED) in bl.on_beat_reply(5.01, 0, "abc123")


def test_a_transport_drop_from_below_while_linked_is_looking_at_once():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    assert StateChanged(LOOKING) in bl.link_down(1.0)
    # Solo is timed from the last message (0.0), not from the drop (1.0).
    assert StateChanged(SOLO) not in bl.tick(14.9)
    assert StateChanged(SOLO) in bl.tick(15.0)


def test_the_tick_that_declares_looking_sends_no_beat():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(1.0)
    bl.tick(2.0)
    acts = bl.tick(3.0)                      # last message at 0.0 is 3 s old
    assert _sends(acts, SendBeat) == []
    assert DropTransport() in acts and StateChanged(LOOKING) in acts


def test_a_transport_drop_before_ever_linking_is_just_down():
    bl = _bl()
    bl.link_up(0.0)
    bl.link_down(1.0)
    assert bl.state == DOWN
    assert bl.tick(30.0) == []


def test_epoch_change_drops_role_and_rehellos():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(1.0)
    acts = bl.on_beat_reply(1.0, 1, "ffffff")
    assert acts[:2] == [DropRole(), SendHello()]
    assert bl.epoch == "ffffff" and bl.state == LINKED


def test_next_due():
    bl = _bl()
    assert bl.next_due() is None
    bl.link_up(0.0)
    assert bl.next_due() == 1.0
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(2.0)
    assert bl.next_due() == 3.0              # lost check and beat coincide


def test_jitter_stays_inside_the_band():
    import random
    bl = BeatLink(rng=random.Random(7))
    bl.link_up(0.0)
    t, last = 0.0, 0.0
    gaps = []
    while len(gaps) < 50:
        t += 0.01
        if _sends(bl.tick(t), SendBeat):
            gaps.append(t - last)
            last = t
    assert all(0.88 <= g <= 1.12 for g in gaps)
