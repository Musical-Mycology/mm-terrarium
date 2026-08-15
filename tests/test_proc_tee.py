"""harness/proc_tee.py: one child's stdout, fanned to a log file, the
operator's terminal, and a set of marker events."""
from __future__ import annotations

import io

from harness.proc_tee import ProcTee


def _tee(text, tmp_path, **kwargs):
    log = tmp_path / "child.log"
    tee = ProcTee("ie1", io.StringIO(text), str(log),
                  markers=["role granted after"], **kwargs)
    tee.start()
    tee.join(timeout=2.0)
    return tee, log


def test_every_line_reaches_the_log(tmp_path):
    tee, log = _tee("first\nsecond\n", tmp_path)
    assert log.read_text().splitlines() == ["first", "second"]


def test_a_marker_line_sets_its_event(tmp_path):
    tee, _log = _tee("noise\nrole granted after 3 join(s)\n", tmp_path)
    assert tee.seen("role granted after")


def test_an_absent_marker_stays_unseen(tmp_path):
    tee, _log = _tee("noise\n", tmp_path)
    assert not tee.seen("role granted after")


def test_wait_for_returns_true_once_the_marker_arrives(tmp_path):
    tee, _log = _tee("role granted after 1 join(s)\n", tmp_path)
    assert tee.wait_for("role granted after", timeout=1.0) is True


def test_wait_for_returns_false_on_timeout_rather_than_hanging(tmp_path):
    """CI mode's whole value: a failure is bounded and named, not a hang."""
    tee, _log = _tee("noise\n", tmp_path)
    ticks = iter([0.0, 5.0, 10.0])
    assert tee.wait_for("role granted after", timeout=1.0,
                        clock=lambda: next(ticks),
                        sleep=lambda _s: None) is False


def test_echo_writes_a_prefixed_copy(tmp_path):
    """Interactive mode: the operator watches the run unfold and the WebSim
    URLs are readable as they appear."""
    out = io.StringIO()
    tee, _log = _tee("hello\n", tmp_path, echo=True, out=out)
    assert out.getvalue() == "[ie1] hello\n"


def test_echo_is_off_by_default(tmp_path):
    out = io.StringIO()
    tee, _log = _tee("hello\n", tmp_path, out=out)
    assert out.getvalue() == ""


def test_tail_returns_the_last_lines_for_a_failure_summary(tmp_path):
    tee, _log = _tee("".join(f"line{i}\n" for i in range(50)), tmp_path)
    assert tee.tail(3) == ["line47", "line48", "line49"]
