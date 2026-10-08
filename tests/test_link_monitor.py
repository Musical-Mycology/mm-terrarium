"""LinkMonitor: Control's per-device view of a beat-capable link (spec
2026-10-08 section 7). Pure, so every test drives time by hand."""

import pytest

from devicelink.link_monitor import LOSS_WINDOW, LinkMonitor, bars


def test_a_device_that_never_beat_has_no_view():
    m = LinkMonitor()
    assert m.beats("ie1") is False
    assert m.view("ie1", last_seen=0.0, now=0.0) is None


def test_live_until_lost_after_then_missing():
    m = LinkMonitor(lost_after=3.0)
    m.on_beat("ie1", 0, 0, now=10.0)
    assert m.beats("ie1") is True
    assert m.view("ie1", last_seen=10.0, now=13.0)["state"] == "live"
    assert m.view("ie1", last_seen=10.0, now=13.01)["state"] == "missing"


def test_loss_from_seq_gaps():
    m = LinkMonitor()
    for seq in (0, 1, 2, 4, 5, 7, 8, 9):     # 3 and 6 lost: 8 of 10
        m.on_beat("ie1", seq, 20, now=float(seq))
    v = m.view("ie1", last_seen=9.0, now=9.0)
    assert v["loss"] == pytest.approx(0.2)
    assert v["rtt_ms"] == 20


def test_a_seq_reset_starts_a_new_window():
    m = LinkMonitor()
    for seq in (0, 2, 4):                     # lossy first link
        m.on_beat("ie1", seq, 0, now=float(seq))
    m.on_beat("ie1", 0, 0, now=10.0)          # relink: seq back to 0
    m.on_beat("ie1", 1, 0, now=11.0)
    assert m.view("ie1", last_seen=11.0, now=11.0)["loss"] == 0.0


def test_a_late_duplicate_is_ignored():
    m = LinkMonitor()
    for seq in (5, 6, 7):
        m.on_beat("ie1", seq, 0, now=float(seq))
    m.on_beat("ie1", 6, 0, now=8.0)           # reordered UDP
    assert m.view("ie1", last_seen=8.0, now=8.0)["loss"] == 0.0


def test_window_is_bounded():
    m = LinkMonitor()
    for seq in range(LOSS_WINDOW + 10):
        m.on_beat("ie1", seq, 0, now=float(seq))
    assert m.view("ie1", last_seen=0.0, now=0.0)["loss"] == 0.0


def test_one_beat_has_no_loss_figure_yet():
    m = LinkMonitor()
    m.on_beat("ie1", 0, 0, now=0.0)
    v = m.view("ie1", last_seen=0.0, now=0.0)
    assert v["loss"] is None and v["bars"] is None


def test_rtt_zero_means_unknown():
    m = LinkMonitor()
    m.on_beat("ie1", 0, 0, now=0.0)
    assert m.view("ie1", last_seen=0.0, now=0.0)["rtt_ms"] is None


def test_forget_and_clear():
    m = LinkMonitor()
    m.on_beat("ie1", 0, 0, now=0.0)
    m.on_beat("ie2", 0, 0, now=0.0)
    m.forget("ie1")
    assert m.beats("ie1") is False and m.beats("ie2") is True
    m.clear()
    assert m.beats("ie2") is False


@pytest.mark.parametrize("loss,rtt,want", [
    (None, 10, None),
    (0.0, 10, 4), (0.02, 49, 4),
    (0.03, 10, 3), (0.0, 99, 3),
    (0.10, 10, 2), (0.0, 249, 2),
    (0.20, 10, 1), (0.0, 400, 1),
    (0.0, None, 4),
])
def test_bars(loss, rtt, want):
    assert bars(loss, rtt) == want
