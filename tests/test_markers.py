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


def test_browse_url_marker_is_emitted_by_every_browser_surface():
    """The Console and a simulated Tuneshroom canvas print their URL
    behind markers.BROWSE_URL, so harness/run_stack.py can collect and
    open them under --open. Matching the incidental wording ('Watch the
    Shroom at ...') instead would be the silent-hang trap this module
    exists to prevent, one URL at a time.

    A Room fixture canvas is deliberately NOT one of these surfaces any
    more: it prints markers.ROOM_URL instead (see
    test_room_url_marker_is_emitted_by_room_simulator below), so
    run_stack --open stops auto-opening it."""
    import harness.o2_shroom
    import harness.terrarium_boot

    for module in (harness.terrarium_boot, harness.o2_shroom):
        assert "markers.BROWSE_URL" in inspect.getsource(module), (
            f"{module.__name__} no longer emits markers.BROWSE_URL; "
            f"run_stack --open would silently stop opening its tab.")


def test_browse_url_marker_is_distinct_from_every_other_marker():
    others = list(markers.READY_MARKERS.values()) + \
        list(markers.FAILURE_MARKERS.values())
    assert markers.BROWSE_URL.strip()
    for other in others:
        assert not markers.BROWSE_URL.startswith(other)
        assert not other.startswith(markers.BROWSE_URL)


def test_room_url_marker_value():
    assert markers.ROOM_URL == "ROOM_URL:"


def test_room_url_marker_is_emitted_by_room_simulator():
    """The Room fixture canvas is a pop-out reached from the Console's
    Room card, not an automatic browser tab, so it prints behind
    markers.ROOM_URL rather than markers.BROWSE_URL."""
    import harness.room_simulator

    assert "markers.ROOM_URL" in inspect.getsource(harness.room_simulator)


def test_o2_shroom_emits_room_url_under_no_join_and_browse_url_otherwise():
    """o2_shroom.py stands in for a Room fixture under --no-join and for a
    real Tuneshroom device otherwise -- the marker it prints has to track
    which one it is being right now, not just always emit BROWSE_URL."""
    import harness.o2_shroom

    source = inspect.getsource(harness.o2_shroom)
    assert "markers.ROOM_URL if args.no_join else markers.BROWSE_URL" in source


def test_room_url_marker_is_distinct_from_every_other_marker():
    others = list(markers.READY_MARKERS.values()) + \
        list(markers.FAILURE_MARKERS.values()) + [markers.BROWSE_URL]
    assert markers.ROOM_URL.strip()
    for other in others:
        assert not markers.ROOM_URL.startswith(other)
        assert not other.startswith(markers.ROOM_URL)


def _module_for(name: str):
    import harness.o2_shroom
    import harness.terrarium_boot
    return (harness.terrarium_boot if name.startswith("CONTROL_")
            else harness.o2_shroom)
