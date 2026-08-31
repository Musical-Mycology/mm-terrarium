import pytest

luxaeterna = pytest.importorskip("luxaeterna")

from harness.design_session import LuxBenchSession, bench_session_factory


def test_render_returns_nonempty_int_list():
    session = LuxBenchSession({})
    frame = session.render()
    assert isinstance(frame, list)
    assert len(frame) > 0
    assert all(isinstance(v, int) for v in frame)


def test_feed_midi_does_not_raise():
    session = LuxBenchSession({})
    session.feed_midi(0xB0, 74, 64)


def test_close_does_not_raise():
    session = LuxBenchSession({})
    session.close()


def test_bench_session_factory_builds_a_session():
    session = bench_session_factory({})
    frame = session.render()
    assert isinstance(frame, list)
    assert len(frame) > 0
