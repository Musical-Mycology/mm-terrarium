from control.timed_queue import TimedQueue


def test_a_future_payload_is_withheld_until_its_time():
    q = TimedQueue()
    q.push(10.0, "a", now=0.0)
    assert q.due(9.9) == []
    assert q.due(10.0) == ["a"]


def test_a_released_payload_is_not_released_twice():
    q = TimedQueue()
    q.push(1.0, "a", now=0.0)
    assert q.due(1.0) == ["a"]
    assert q.due(2.0) == []


def test_none_means_now_and_is_not_a_clamp():
    """when=None is 'no time declared', not 'late'. It must not inflate the
    clamp counter, which exists to report a too-small horizon."""
    q = TimedQueue()
    q.push(None, "a", now=5.0)
    assert q.due(5.0) == ["a"]
    assert q.clamped == 0


def test_a_past_time_releases_immediately_and_counts_as_clamped():
    q = TimedQueue()
    q.push(3.0, "late", now=5.0)
    assert q.due(5.0) == ["late"]
    assert q.clamped == 1


def test_payloads_are_released_in_time_order():
    q = TimedQueue()
    q.push(3.0, "third", now=0.0)
    q.push(1.0, "first", now=0.0)
    q.push(2.0, "second", now=0.0)
    assert q.due(10.0) == ["first", "second", "third"]


def test_equal_times_keep_insertion_order():
    """Two cues from one gesture share a time; the Bit's ordering is the
    only ordering available, so it must survive."""
    q = TimedQueue()
    q.push(1.0, "a", now=0.0)
    q.push(1.0, "b", now=0.0)
    assert q.due(1.0) == ["a", "b"]


def test_payloads_need_not_be_comparable():
    """Payloads are MIDI tuples on one side and frames on the other. Sorting
    must never fall through to comparing them."""
    q = TimedQueue()
    q.push(1.0, {"not": "comparable"}, now=0.0)
    q.push(1.0, {"also": "not"}, now=0.0)
    assert len(q.due(1.0)) == 2


def test_lateness_records_the_magnitude_behind_the_clamp_counter():
    """`clamped` says the horizon is wrong; `lateness` says by how much,
    which is what actually sizes BootConfig.cue_horizon."""
    q = TimedQueue()
    q.push(3.0, "late", now=5.0)
    assert q.clamped == 1
    assert list(q.lateness) == [2.0]


def test_lateness_is_signed_so_early_stays_distinguishable_from_late():
    """A frame arriving 2 s before its deadline is the healthy case. Storing
    magnitudes would make it indistinguishable from a 2 s overshoot."""
    q = TimedQueue()
    q.push(10.0, "early", now=8.0)
    assert list(q.lateness) == [-2.0]
    assert q.clamped == 0


def test_lateness_samples_every_timed_payload_not_only_the_clamped_ones():
    """Sampling only clamped payloads would measure the tail and report it
    as the distribution."""
    q = TimedQueue()
    q.push(10.0, "early", now=8.0)
    q.push(3.0, "late", now=5.0)
    q.push(20.0, "early-too", now=19.5)
    assert len(q.lateness) == 3
    assert q.clamped == 1


def test_an_undeclared_time_contributes_no_lateness_sample():
    """when=None has no deadline to be late for -- consistent with it not
    counting as a clamp either."""
    q = TimedQueue()
    q.push(None, "a", now=5.0)
    assert list(q.lateness) == []


def test_lateness_is_bounded_so_a_long_installation_cannot_leak():
    """This runs on a Radxa for as long as the room is up."""
    from control.timed_queue import _MAX_LATENESS_SAMPLES
    q = TimedQueue()
    for i in range(_MAX_LATENESS_SAMPLES + 50):
        q.push(float(i), "p", now=float(i))
        q.due(float(i))
    assert len(q.lateness) == _MAX_LATENESS_SAMPLES
