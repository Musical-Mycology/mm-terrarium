"""Pure trace evaluation against EventTrigger thresholds."""
import json
from pathlib import Path

from control.gesture_eval import evaluate_trace, propose_thresholds, session_rows

GRAVITY = 9.80665


def make_trace(t_ms, accel_g, label="tap", series=1):
    # z carries the whole magnitude; x/y zero; gyro zero
    return {"label": label, "capture_id": f"{label}-{series}", "series": series,
            "samples": {"t_ms": list(t_ms),
                        "ax": [0.0] * len(t_ms), "ay": [0.0] * len(t_ms),
                        "az": [g * GRAVITY for g in accel_g],
                        "gx": [0.0] * len(t_ms), "gy": [0.0] * len(t_ms),
                        "gz": [0.0] * len(t_ms)}}


def test_single_spike_fires_once():
    trace = make_trace([0, 10, 20, 30, 40], [1.0, 1.0, 3.0, 1.0, 1.0])
    result = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
    assert result["fires"] == [20]
    assert result["spikes"] == 1
    assert result["peak_dev_g"] > 1.5


def test_below_threshold_never_fires():
    trace = make_trace([0, 10, 20], [1.0, 1.4, 1.0])
    result = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
    assert result["fires"] == []


def test_edges_inside_window_collapse():
    trace = make_trace([0, 10, 20, 30, 40, 50],
                       [1.0, 3.0, 1.0, 3.0, 1.0, 1.0])
    result = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
    assert result["fires"] == [10]


def test_double_fire_annotated_when_double_ms_declared():
    trace = make_trace([0, 10, 300, 310, 320],
                       [1.0, 3.0, 1.0, 3.0, 1.0])
    result = evaluate_trace(
        trace, {"peak_g": 2.0, "window_ms": 200, "double_ms": 400})
    assert result["fires"] == [10, 310]
    assert result["double_fires"] == [310]


def test_propose_thresholds_from_rows():
    rows = [{"peak_dev_g": 2.5, "span_ms": 80.0, "isi_ms": [150.0]},
            {"peak_dev_g": 2.0, "span_ms": 120.0, "isi_ms": []}]
    proposal = propose_thresholds(rows)
    assert proposal == {"peak_g": 1.6, "window_ms": 170, "double_ms": 250}
    assert propose_thresholds([]) is None


def test_propose_omits_double_ms_without_isi():
    proposal = propose_thresholds([{"peak_dev_g": 2.0, "span_ms": 100.0,
                                    "isi_ms": []}])
    assert proposal == {"peak_g": 1.6, "window_ms": 150}


def test_session_rows_reads_capture_layout(tmp_path):
    d = tmp_path / "tap"
    d.mkdir()
    trace = make_trace([0, 10, 20], [1.0, 3.0, 1.0])
    (d / "1.json").write_text(json.dumps(trace))
    rows = session_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["label"] == "tap" and rows[0]["n"] == 3
