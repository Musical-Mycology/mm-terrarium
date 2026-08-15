"""harness/markers.py: the contract harness/run_stack.py watches stdout for.

These constants are the ONLY thing standing between a reworded print and a
runner that hangs forever with no diagnostic. Assert both sides."""
from __future__ import annotations

import inspect

import pytest

from harness import markers


def test_every_ready_marker_is_emitted_by_its_module():
    for name, marker in markers.READY_MARKERS.items():
        module = _module_for(name)
        # The emit sites reference the constant by name (markers.CONST_NAME),
        # not by its literal value -- that symbolic reference is the whole
        # point of this module, so it is what a source check has to look
        # for. inspect.getsource() reads the .py file's own text and does
        # not resolve imports, so checking for the marker's *value* here
        # would fail even on a correct emit site.
        assert f"markers.{name}" in inspect.getsource(module), (
            f"{name}: harness/run_stack.py waits for {marker!r}, and nothing "
            f"in {module.__name__} emits it any more. A reworded print is a "
            f"hang, not a test failure, unless this test catches it.")


def test_every_failure_marker_is_emitted_by_its_module():
    for name, marker in markers.FAILURE_MARKERS.items():
        module = _module_for(name)
        assert f"markers.{name}" in inspect.getsource(module)


def test_markers_are_non_empty_and_distinct():
    """A blank marker matches every line, and a marker that is a prefix of
    another would fire the wrong event."""
    all_markers = list(markers.READY_MARKERS.values()) + \
        list(markers.FAILURE_MARKERS.values())
    assert all(m.strip() for m in all_markers)
    assert len(set(all_markers)) == len(all_markers)
    for a in all_markers:
        for b in all_markers:
            if a is not b:
                assert not a.startswith(b)


def _module_for(name: str):
    import harness.o2_shroom
    import harness.terrarium_boot
    return (harness.terrarium_boot if name.startswith("CONTROL_")
            else harness.o2_shroom)
