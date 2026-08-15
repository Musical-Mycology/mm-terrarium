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
    "harness.room_simulator",
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
