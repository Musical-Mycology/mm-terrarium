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
