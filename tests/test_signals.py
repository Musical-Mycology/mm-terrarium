"""harness/signals.py: one copy of the SIGTERM gotcha, and proof that the
four modules a supervisor signals actually install it."""
from __future__ import annotations

import signal

import pytest

from harness.signals import sigterm_as_keyboard_interrupt


def test_it_installs_a_handler_that_raises_keyboard_interrupt():
    previous = signal.getsignal(signal.SIGTERM)
    try:
        sigterm_as_keyboard_interrupt()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.parametrize("module_name", [
    "harness.led_smoke",
    "harness.o2_shroom",
    "harness.terrarium_boot",
])
def test_every_supervised_module_installs_the_handler(module_name, monkeypatch):
    """Python finally blocks do NOT run on a bare SIGTERM, only on
    KeyboardInterrupt. control/simulator_process.py signals its children
    with SIGTERM and harness/run_stack.py signals terrarium_boot the same
    way, so a module without this loses its exit report: o2_shroom's whole
    lateness summary and its backend.close() live in a finally.

    Asserted by source inspection rather than by running main(), because
    main() needs argv, sockets and in two cases a live Arco."""
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    assert "sigterm_as_keyboard_interrupt()" in source, (
        f"{module_name} is sent SIGTERM by a supervisor and would lose its "
        f"finally block without the handler")


def test_it_also_maps_sighup_so_a_closed_terminal_unwinds():
    """2026-09-12: a hangup on run_stack used to hit Python's default
    disposition and kill it without running TeardownStack, leaving Arco
    (in its own session, never hung up) playing."""
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_hup = signal.getsignal(signal.SIGHUP)
    try:
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        sigterm_as_keyboard_interrupt()
        handler = signal.getsignal(signal.SIGHUP)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGHUP, None)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGHUP, previous_hup)


def test_it_leaves_an_ignored_sighup_alone_so_nohup_keeps_working():
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_hup = signal.getsignal(signal.SIGHUP)
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        sigterm_as_keyboard_interrupt()
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGHUP, previous_hup)
