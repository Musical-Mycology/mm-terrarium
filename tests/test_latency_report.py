"""The lateness -> absolute-latency conversion behind the measured
cue_horizon. This is the arithmetic that would silently produce a wrong
number, so it is pinned rather than eyeballed off one run's output.
"""
from __future__ import annotations

import json

from harness.o2_shroom import _report_latency
from harness.sync_bench import format_report, summarise


class _FakeClient:
    def __init__(self, lateness, clamped=0):
        self.lateness = list(lateness)
        self.clamped = clamped


def test_latency_is_the_horizon_plus_the_observed_lateness(capsys):
    """Control stamps when = t + horizon, so (now - when) + horizon recovers
    (now - t): the end-to-end figure cue_horizon has to cover."""
    # Ran at a 150 ms horizon; every frame landed 80 ms before its deadline.
    _report_latency(_FakeClient([-0.080] * 10), 0.150, None)
    out = capsys.readouterr().out
    assert "70.0 ms" in out          # 150 - 80
    assert "frames : 10" in out


def test_a_frame_arriving_early_is_not_reported_as_error(capsys):
    """summarise() absolutises, which is right for two-sided agreement error
    and wrong for one-sided latency. Converting first is what keeps a
    healthy early arrival from reading as 80 ms of error."""
    _report_latency(_FakeClient([-0.080]), 0.150, None)
    out = capsys.readouterr().out
    assert "80.0 ms" not in out


def test_a_clamped_sample_is_flagged_as_censored(capsys):
    """A clamp means the frame was already past its deadline, so the real
    tail is longer than 'worst' can show. Reporting the number without
    saying so would understate the horizon needed."""
    _report_latency(_FakeClient([0.010], clamped=1), 0.150, None)
    out = capsys.readouterr().out
    assert "CENSORED" in out


def test_no_clamps_means_no_censoring_warning(capsys):
    _report_latency(_FakeClient([-0.05, -0.06]), 0.150, None)
    assert "CENSORED" not in capsys.readouterr().out


def test_without_a_horizon_the_raw_spread_is_reported_not_a_latency(capsys):
    """The device does not know Control's horizon unless told, and guessing
    one would invent a latency figure."""
    _report_latency(_FakeClient([-0.080, -0.020]), None, None)
    out = capsys.readouterr().out
    assert "lateness spread" in out
    assert "-80.0 .. -20.0 ms" in out


def test_no_timed_frames_says_so_rather_than_reporting_zeros(capsys):
    """An empty sample and a genuinely 0 ms path must not look identical."""
    _report_latency(_FakeClient([]), 0.150, None)
    assert "no timed frames observed" in capsys.readouterr().out


def test_samples_are_written_for_sync_bench_to_reread(tmp_path):
    path = tmp_path / "samples.json"
    _report_latency(_FakeClient([-0.08, -0.07]), 0.150, str(path))
    assert json.loads(path.read_text()) == [-0.08, -0.07]


def test_format_report_shows_the_tail_alongside_the_mean():
    """render_bench's lesson: a mean-only report hides the frame that
    actually breaks the illusion."""
    text = format_report(summarise([0.001] * 99 + [0.2]))
    for label in ("mean", "p95", "p99", "worst"):
        assert label in text
