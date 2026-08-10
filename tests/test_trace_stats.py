"""trace_stats features, checked against hand-built traces whose answers are
known analytically. Pure -- no luxaeterna, core offline suite."""

import math
import struct

import pytest

from tools.trace_stats import (GRAVITY, audio_features, motion_features,
                               rows_for)


def trace(t_ms, ax=None, ay=None, az=None, gx=None, gy=None, gz=None,
          label="shake"):
    n = len(t_ms)
    zeros = [0.0] * n
    return {"label": label, "capture_id": f"{label}-001", "series": 1,
            "n": n, "truncated": False, "gaps": [],
            "samples": {"t_ms": list(t_ms),
                        "ax": ax or list(zeros), "ay": ay or list(zeros),
                        "az": az or list(zeros),
                        "gx": gx or list(zeros), "gy": gy or list(zeros),
                        "gz": gz or list(zeros)}}


# --- motion --------------------------------------------------------------

def test_a_resting_trace_reads_as_one_g_with_no_deviation():
    t = [0.0, 10.0, 20.0, 30.0]
    feats = motion_features(trace(t, az=[GRAVITY] * 4))
    assert feats["peak_a_g"] == pytest.approx(1.0)
    assert feats["peak_dev_g"] == pytest.approx(0.0)
    assert feats["span_ms"] == 0.0
    assert feats["time_above_g"]["2.0"] == 0.0


def test_peak_and_deviation_come_off_the_magnitude():
    """A 3-4-5 triangle: |a| = 5 * GRAVITY exactly."""
    t = [0.0, 10.0]
    feats = motion_features(
        trace(t, ax=[0.0, 3 * GRAVITY], az=[GRAVITY, 4 * GRAVITY]))
    assert feats["peak_a_g"] == pytest.approx(5.0)
    assert feats["peak_dev_g"] == pytest.approx(4.0)


def test_time_above_threshold_counts_sample_intervals():
    """Four samples 10 ms apart, two of them above 2 g -> 20 ms."""
    t = [0.0, 10.0, 20.0, 30.0]
    az = [GRAVITY, 3 * GRAVITY, 3 * GRAVITY, GRAVITY]
    feats = motion_features(trace(t, az=az), thresholds_g=(2.0,))
    assert feats["time_above_g"]["2.0"] == pytest.approx(20.0)
    assert feats["span_ms"] == pytest.approx(10.0)


def test_a_constant_rotation_integrates_to_a_known_swept_angle():
    """1.0 rad/s held for 1.0 s is 1 radian, i.e. 57.29578 degrees."""
    t = [i * 10.0 for i in range(101)]          # 0 .. 1000 ms
    feats = motion_features(trace(t, gz=[1.0] * 101))
    assert feats["peak_omega"] == pytest.approx(1.0)
    assert feats["swept_deg"] == pytest.approx(math.degrees(1.0), rel=1e-6)


def test_inter_spike_intervals_measure_rising_edges():
    """Two separated spikes 40 ms apart -> one interval of 40 ms. This is the
    feature a double-tap window has to be derived from."""
    t = [i * 10.0 for i in range(9)]
    az = [GRAVITY] * 9
    az[2] = 4 * GRAVITY
    az[6] = 4 * GRAVITY
    feats = motion_features(trace(t, az=az), thresholds_g=(2.0,))
    assert feats["isi_ms"] == [pytest.approx(40.0)]
    assert feats["spikes"] == 2


def test_a_single_spike_has_no_interval():
    t = [i * 10.0 for i in range(5)]
    az = [GRAVITY] * 5
    az[2] = 4 * GRAVITY
    feats = motion_features(trace(t, az=az), thresholds_g=(2.0,))
    assert feats["isi_ms"] == []
    assert feats["spikes"] == 1


def test_an_empty_trace_does_not_divide_by_zero():
    feats = motion_features(trace([]))
    assert feats["peak_a_g"] == 0.0
    assert feats["swept_deg"] == 0.0
    assert feats["isi_ms"] == []


# --- audio ---------------------------------------------------------------

def test_full_scale_audio_reads_as_zero_dbfs():
    pcm = struct.pack("<2h", 32767, -32767)
    feats = audio_features(pcm, rate=16000)
    assert feats["peak_dbfs"] == pytest.approx(0.0, abs=0.01)


def test_half_scale_audio_reads_as_minus_six_dbfs():
    pcm = struct.pack("<2h", 16384, 0)
    feats = audio_features(pcm, rate=16000)
    assert feats["peak_dbfs"] == pytest.approx(-6.02, abs=0.05)


def test_attack_time_is_measured_from_ten_percent_of_peak_to_peak():
    """Ramp at 1000 Hz: 10% of peak at sample 1, peak at sample 10, so the
    attack is 9 samples = 9 ms."""
    pcm = struct.pack("<11h", *[i * 1000 for i in range(11)])
    feats = audio_features(pcm, rate=1000)
    assert feats["attack_ms"] == pytest.approx(9.0)


def test_silence_is_reported_rather_than_crashing_on_log_of_zero():
    feats = audio_features(struct.pack("<4h", 0, 0, 0, 0), rate=16000)
    assert feats["peak_dbfs"] == float("-inf")
    assert feats["attack_ms"] == 0.0


def test_no_audio_is_none():
    assert audio_features(b"", rate=16000) is None


# --- directory walk ------------------------------------------------------

def test_rows_for_reads_every_trace_in_a_session(tmp_path):
    import json
    session = tmp_path / "SESSION"
    for label, series in (("tap", 1), ("shake", 2)):
        directory = session / label
        directory.mkdir(parents=True)
        body = trace([0.0, 10.0], az=[GRAVITY, 4 * GRAVITY], label=label)
        body["audio"] = None
        (directory / f"{series:03d}.json").write_text(json.dumps(body))

    rows = rows_for(session)
    assert sorted(r["label"] for r in rows) == ["shake", "tap"]
    assert all(r["peak_a_g"] > 3.0 for r in rows)
