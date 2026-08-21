"""CLI/plumbing tests for the devicelink_smoke demo: the arg->duration
mapping and the SETUP-hold window (_wait_in_setup) that lets a scored role
join before run() closes registration for it. The live server + real-clock
loop in main() is covered by manual acceptance and tests/test_devicelink_smoke.py's
socket-driven test, not here.
"""

from __future__ import annotations

import argparse

import pytest

pytest.importorskip("luxaeterna")

from bits.test.test_bit import RUN_DURATION_SECONDS
from harness.devicelink_smoke import _run_duration, _wait_in_setup


def _args(seconds=None, hold=False):
    return argparse.Namespace(seconds=seconds, hold=hold)


def test_run_duration_hold_is_infinite():
    assert _run_duration(_args(hold=True)) == float("inf")


def test_run_duration_seconds_overrides():
    assert _run_duration(_args(seconds=12.0)) == 12.0


def test_run_duration_default_is_test_bit_natural():
    assert _run_duration(_args()) == RUN_DURATION_SECONDS


class _FakeClock:
    """Deterministic clock: each call advances by `step`. No real time."""

    def __init__(self, step: float = 0.1):
        self._t = 0.0
        self._step = step

    def __call__(self) -> float:
        t = self._t
        self._t += self._step
        return t


class _RecordingAgent:
    def __init__(self):
        self.polls = 0

    def poll(self) -> None:
        self.polls += 1


def _noop_sleep(_seconds: float) -> None:
    pass


def test_wait_in_setup_default_zero_is_a_noop():
    agent = _RecordingAgent()
    _wait_in_setup(agent, 0.0, clock=_FakeClock(), sleep=_noop_sleep)
    assert agent.polls == 0


def test_wait_in_setup_negative_is_a_noop():
    agent = _RecordingAgent()
    _wait_in_setup(agent, -5.0, clock=_FakeClock(), sleep=_noop_sleep)
    assert agent.polls == 0


def test_wait_in_setup_polls_until_the_deadline():
    agent = _RecordingAgent()
    clock = _FakeClock(step=0.1)
    _wait_in_setup(agent, 0.35, clock=clock, sleep=_noop_sleep)
    # deadline = 0.0 + 0.35; loop condition reads 0.1, 0.2, 0.3 (< 0.35,
    # three polls) then 0.4 (>= 0.35, stop).
    assert agent.polls == 3
