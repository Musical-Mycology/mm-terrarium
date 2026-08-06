"""Arco-backed synth pool. The import-hygiene test runs everywhere; the live
test needs both pyarco on PYTHONPATH and a running Arco server, so it skips.

Start the server by hand (it is a curses app with no headless mode):
    cd /Users/chris/projects/arco/apps/pytest && ./server
then run with:
    PYTHONPATH=/Users/chris/projects/arco python -m pytest tests/test_arco_synth.py
"""

from __future__ import annotations

import os

import pytest


def test_module_imports_without_pyarco():
    # The load-bearing property: importing this module must cost nothing when
    # Arco is absent, so the offline suite stays green.
    import sys

    import harness.arco_synth as mod

    assert hasattr(mod, "ArcoSynthPool")
    assert not [m for m in sys.modules if m.startswith("pyarco")]


def test_channels_are_allocated_and_recycled_without_a_server():
    # Channel bookkeeping is pure, so it is testable with no Arco at all.
    from harness.arco_synth import ArcoSynthPool

    pool = ArcoSynthPool(max_channels=2)
    pool._flsyn = object()                   # pretend start() ran
    a, b = pool.acquire(), pool.acquire()
    assert a.channel != b.channel
    with pytest.raises(RuntimeError):
        pool.acquire()                       # exhausted, and it says so
    pool.release(a)
    c = pool.acquire()
    assert c.channel == a.channel            # recycled


def test_acquire_before_start_is_a_clear_error():
    from harness.arco_synth import ArcoSynthPool

    with pytest.raises(RuntimeError) as ei:
        ArcoSynthPool().acquire()
    assert "start()" in str(ei.value)


def test_start_with_missing_soundfont_names_the_path_without_pyarco():
    # The existence check runs before arco.initialize(), so this must raise
    # with no pyarco installed and no Arco server running.
    from harness.arco_synth import ArcoSynthPool

    missing_path = "/nonexistent/definitely-not-a-soundfont.sf2"
    pool = ArcoSynthPool(soundfont=missing_path)
    with pytest.raises(FileNotFoundError) as ei:
        pool.start()
    assert missing_path in str(ei.value)


@pytest.mark.skipif(not os.environ.get("MM_ARCO_LIVE"),
                    reason="needs a running Arco server; set MM_ARCO_LIVE=1")
def test_live_pool_acquires_sends_and_releases():
    pytest.importorskip("pyarco")
    from harness.arco_synth import ArcoSynthPool

    pool = ArcoSynthPool()
    pool.start()
    try:
        voice = pool.acquire()
        voice.program_change(89)
        voice.note_on(45, 90)
        voice.control_change(74, 100)
        pool.poll()
        voice.note_off(45)
        pool.release(voice)
    finally:
        pool.shutdown()
