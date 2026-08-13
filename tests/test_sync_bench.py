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
