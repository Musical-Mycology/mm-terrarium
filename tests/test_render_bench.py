"""render_bench: frame-timing statistics for the multi-universe output loop.

No luxaeterna import here: ``summarise`` and ``measure`` are deliberately
independent of the renderer so the core suite exercises them. Only ``main()``
reaches for ``harness.array_smoke``, and it imports it lazily.
"""

from __future__ import annotations

import dataclasses

import pytest

from harness.render_bench import FrameStats, measure, summarise

NOMINAL = 1.0 / 44.0


# --- summarise ---

def test_a_perfectly_regular_loop_reports_its_nominal_rate():
    stats = summarise([NOMINAL] * 44)
    assert stats.frames == 44
    assert stats.mean_fps == pytest.approx(44.0, abs=0.01)
    assert stats.worst_frame_ms == pytest.approx(1000.0 / 44.0, abs=0.01)


def test_a_single_stall_is_visible_in_the_worst_frame_not_the_mean():
    stats = summarise([NOMINAL] * 43 + [0.2])
    assert stats.mean_fps > 20.0                 # mean still looks acceptable
    assert stats.worst_frame_ms == pytest.approx(200.0, abs=0.1)


def test_p95_catches_a_sustained_outlier():
    stats = summarise([NOMINAL] * 95 + [0.1] * 5)
    assert stats.p95_frame_ms >= 100.0


def test_p95_is_not_dragged_up_by_one_lone_outlier():
    stats = summarise([NOMINAL] * 999 + [0.5])
    assert stats.p95_frame_ms == pytest.approx(1000.0 * NOMINAL, abs=0.01)
    assert stats.worst_frame_ms == pytest.approx(500.0, abs=0.1)


def test_min_fps_reflects_the_worst_frame():
    stats = summarise([NOMINAL] * 43 + [0.5])
    assert stats.min_fps == pytest.approx(2.0, abs=0.01)


def test_seconds_is_the_total_elapsed():
    stats = summarise([0.1] * 10)
    assert stats.seconds == pytest.approx(1.0, abs=1e-6)


def test_a_single_frame_is_summarisable():
    stats = summarise([0.02])
    assert stats.frames == 1
    assert stats.mean_fps == pytest.approx(50.0)


def test_empty_sample_is_rejected():
    with pytest.raises(ValueError):
        summarise([])


def test_stats_are_immutable():
    stats = summarise([NOMINAL] * 10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        stats.frames = 99


def test_stats_is_a_frozen_dataclass_with_the_documented_fields():
    names = {f.name for f in dataclasses.fields(FrameStats)}
    assert names == {"frames", "seconds", "mean_fps", "min_fps",
                     "p95_frame_ms", "worst_frame_ms"}


# --- measure ---

class FakeLoop:
    """Minimal stand-in for MultiUniverseOutputLoop: a backend and a tick."""

    def __init__(self, frame_interval: float = NOMINAL) -> None:
        self.frame_interval = frame_interval
        self.ticks = 0
        self.opened = 0
        self.closed = 0
        self.backend = self

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def _loop_once(self) -> int:
        self.ticks += 1
        return 7


def test_measure_opens_and_closes_the_backend():
    loop = FakeLoop()
    measure(loop, 0.05)
    assert loop.opened == 1
    assert loop.closed == 1


def test_measure_drives_the_loop_and_returns_stats():
    loop = FakeLoop()
    stats = measure(loop, 0.05)
    assert loop.ticks >= 1
    assert stats.frames == loop.ticks
    assert stats.seconds > 0.0


def test_measure_closes_the_backend_even_when_a_tick_raises():
    loop = FakeLoop()

    def boom() -> int:
        raise RuntimeError("tick failed")

    loop._loop_once = boom
    with pytest.raises(RuntimeError):
        measure(loop, 0.05)
    assert loop.closed == 1
