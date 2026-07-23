"""CLI/plumbing tests for the led_smoke demo: the arg->duration mapping and a
headless pipeline build. The live server + real-clock loop in main() is covered
by manual acceptance, not here."""

from __future__ import annotations

import argparse

import pytest

pytest.importorskip("luxaeterna.backends.websim")

from bits.test_bit import RUN_DURATION_SECONDS
from control.state import State
from harness.led_smoke import _run_duration, build


def _args(seconds=None, hold=False):
    return argparse.Namespace(seconds=seconds, hold=hold)


def test_run_duration_hold_is_infinite():
    assert _run_duration(_args(hold=True)) == float("inf")


def test_run_duration_seconds_overrides():
    assert _run_duration(_args(seconds=12.0)) == 12.0


def test_run_duration_default_is_test_bit_natural():
    assert _run_duration(_args()) == RUN_DURATION_SECONDS


def test_build_constructs_headless_pipeline():
    loop, session, gs = build(run_duration=float("inf"), serve=False)
    assert isinstance(gs.state, State)           # a real GameServer wired up
    assert callable(session.render_into)         # luxaeterna session ready to render
    assert loop is not None
