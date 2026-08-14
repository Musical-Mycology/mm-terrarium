from harness.sync_bench import summarise


def test_summarise_reports_worst_and_p95_not_just_mean():
    """render_bench.py's lesson, applied to sync: a path that averages well
    while missing badly once a second reads as healthy and is not."""
    deltas = [0.001] * 99 + [0.200]
    stats = summarise(deltas)
    assert stats["count"] == 100
    assert stats["worst_ms"] == 200.0
    assert stats["mean_ms"] < 5.0
    assert stats["p95_ms"] < stats["worst_ms"]


def test_summarise_uses_absolute_deltas():
    """Light landing 10 ms EARLY is as wrong as 10 ms late."""
    stats = summarise([-0.010, 0.010])
    assert stats["worst_ms"] == 10.0


def test_summarise_of_nothing_is_empty_not_an_error():
    stats = summarise([])
    assert stats["count"] == 0
    assert stats["worst_ms"] == 0.0


def test_summarise_reports_p99_between_p95_and_worst():
    """p99 is the design point for cue_horizon: worst-case would make every
    cue in the room pay for the single worst hiccup."""
    deltas = [0.001] * 990 + [0.050] * 9 + [0.200]
    stats = summarise(deltas)
    assert stats["p95_ms"] <= stats["p99_ms"] <= stats["worst_ms"]
    assert stats["worst_ms"] == 200.0


def test_p95_and_p99_share_one_convention():
    """Both go through _percentile, so they cannot drift into two different
    rank conventions."""
    from harness.sync_bench import _percentile
    magnitudes = [float(i) for i in range(100)]
    assert _percentile(magnitudes, 0.95) == 95.0
    assert _percentile(magnitudes, 0.99) == 99.0


def test_summarise_of_nothing_still_carries_every_key():
    """format_report indexes all of them; a missing key would be a KeyError
    on the empty run rather than a zero."""
    stats = summarise([])
    for key in ("count", "mean_ms", "p95_ms", "p99_ms", "worst_ms"):
        assert key in stats
