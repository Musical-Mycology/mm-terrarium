"""Offline feature summary over a capture directory.

    python -m tools.trace_stats captures/2026-08-07T14-22-03Z-3f9a
    python -m tools.trace_stats captures/<session> --csv > features.csv

This is what turns "we have data" into "we have definitions": it reports the
features a tap-versus-shake discriminator would be built out of, per trace
and per label, so the separation is visible in one command.

Every feature here is pure over a loaded trace dict, so it is tested against
hand-built traces with known analytic answers rather than against captures.

Deriving actual thresholds from real captures is the NEXT spec. Nothing in
this file encodes a threshold as truth: DEFAULT_THRESHOLDS_G is a ladder to
report against, not an answer.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import sys
import wave
from pathlib import Path

# Matches mm-tuneshroom/lib/sensors/sensor_service.dart.
GRAVITY = 9.80665

DEFAULT_THRESHOLDS_G = (1.5, 2.0, 2.5, 3.0)

_FULL_SCALE = 32768.0


def _magnitudes(samples: dict, axes: tuple) -> list:
    xs, ys, zs = (samples[a] for a in axes)
    return [math.sqrt(x * x + y * y + z * z) for x, y, z in zip(xs, ys, zs)]


def _mean_dt_ms(t_ms: list) -> float:
    if len(t_ms) < 2:
        return 0.0
    return (t_ms[-1] - t_ms[0]) / (len(t_ms) - 1)


def _rising_edges(values: list, threshold: float) -> list:
    """Indices where the signal crosses up through threshold. The count is
    the spike count and the gaps between them are the inter-spike intervals
    a double-tap window has to be derived from."""
    edges, above = [], False
    for i, value in enumerate(values):
        now = value >= threshold
        if now and not above:
            edges.append(i)
        above = now
    return edges


def motion_features(trace: dict, thresholds_g=DEFAULT_THRESHOLDS_G) -> dict:
    samples = trace["samples"]
    t_ms = samples["t_ms"]
    accel = _magnitudes(samples, ("ax", "ay", "az"))
    omega = _magnitudes(samples, ("gx", "gy", "gz"))

    accel_g = [a / GRAVITY for a in accel]
    dt_ms = _mean_dt_ms(t_ms)

    time_above, span_ms, isi_ms, spikes = {}, 0.0, [], 0
    primary = thresholds_g[0] if len(thresholds_g) == 1 else 2.0
    for threshold in thresholds_g:
        hits = [i for i, g in enumerate(accel_g) if g >= threshold]
        time_above[f"{threshold:.1f}"] = len(hits) * dt_ms
        if threshold == primary and hits:
            span_ms = t_ms[hits[-1]] - t_ms[hits[0]]
    edges = _rising_edges(accel_g, primary)
    spikes = len(edges)
    isi_ms = [t_ms[b] - t_ms[a] for a, b in zip(edges, edges[1:])]

    # Trapezoidal integral of |omega| dt, in degrees. Rotation rate is in
    # rad/s and t_ms in milliseconds, hence the 1000.
    swept = 0.0
    for (t0, w0), (t1, w1) in zip(zip(t_ms, omega), zip(t_ms[1:], omega[1:])):
        swept += (w0 + w1) / 2.0 * (t1 - t0) / 1000.0

    return {
        "n": len(t_ms),
        "duration_ms": (t_ms[-1] - t_ms[0]) if t_ms else 0.0,
        "mean_dt_ms": dt_ms,
        "peak_a_g": max(accel_g) if accel_g else 0.0,
        "peak_dev_g": max((abs(a - GRAVITY) / GRAVITY for a in accel),
                          default=0.0),
        "time_above_g": time_above,
        "span_ms": span_ms,
        "spikes": spikes,
        "isi_ms": isi_ms,
        "peak_omega": max(omega) if omega else 0.0,
        "swept_deg": math.degrees(swept),
    }


def audio_features(pcm: bytes, rate: int) -> dict | None:
    """Peak level and attack time off raw int16le PCM. None when the capture
    was motion-only (mic denied, or no mic in the client)."""
    if not pcm:
        return None
    count = len(pcm) // 2
    values = struct.unpack(f"<{count}h", pcm[:count * 2])
    peak = max(abs(v) for v in values)
    if peak == 0:
        return {"peak_dbfs": float("-inf"), "attack_ms": 0.0,
                "duration_ms": count / rate * 1000.0}

    peak_i = next(i for i, v in enumerate(values) if abs(v) == peak)
    onset_i = next((i for i, v in enumerate(values)
                    if abs(v) >= peak * 0.1), peak_i)
    return {
        "peak_dbfs": 20.0 * math.log10(peak / _FULL_SCALE),
        "attack_ms": (peak_i - onset_i) / rate * 1000.0,
        "duration_ms": count / rate * 1000.0,
    }


def read_wav(path: Path):
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


def rows_for(session_dir, thresholds_g=DEFAULT_THRESHOLDS_G) -> list:
    """One flat row per trace in a session directory, features included."""
    session_dir = Path(session_dir)
    rows = []
    for path in sorted(session_dir.glob("*/[0-9]*.json")):
        trace = json.loads(path.read_text())
        row = {"label": trace["label"], "capture_id": trace["capture_id"],
               "series": trace["series"], "truncated": trace["truncated"],
               "gaps": len(trace.get("gaps", [])),
               **motion_features(trace, thresholds_g)}
        audio = trace.get("audio")
        if audio:
            pcm, rate = read_wav(path.parent / audio["file"])
            row.update({f"mic_{k}": v
                        for k, v in (audio_features(pcm, rate) or {}).items()})
        rows.append(row)
    return rows


_FLAT = ("label", "capture_id", "series", "n", "duration_ms", "mean_dt_ms",
         "peak_a_g", "peak_dev_g", "span_ms", "spikes", "peak_omega",
         "swept_deg", "mic_peak_dbfs", "mic_attack_ms", "truncated", "gaps")


def _print_table(rows: list) -> None:
    for row in rows:
        print(f"{row['capture_id']:<14} "
              f"peak {row['peak_a_g']:6.2f}g  dev {row['peak_dev_g']:6.2f}g  "
              f"span {row['span_ms']:7.1f}ms  spikes {row['spikes']:2d}  "
              f"omega {row['peak_omega']:6.2f}  swept {row['swept_deg']:7.1f}deg"
              f"  mic {row.get('mic_peak_dbfs', float('nan')):7.1f}dBFS")

    print()
    labels = sorted({row["label"] for row in rows})
    print(f"{'label':<12}{'n':>4}{'peak_a_g':>22}{'peak_omega':>22}"
          f"{'swept_deg':>22}")
    for label in labels:
        group = [r for r in rows if r["label"] == label]
        cells = []
        for key in ("peak_a_g", "peak_omega", "swept_deg"):
            values = sorted(r[key] for r in group)
            cells.append(f"{values[0]:7.2f}..{values[-1]:7.2f}"
                         f" ({sum(values) / len(values):6.2f})")
        print(f"{label:<12}{len(group):>4}" + "".join(f"{c:>22}" for c in cells))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Summarise captured telemetry traces.")
    ap.add_argument("session_dir",
                    help="A capture session directory, e.g. "
                         "captures/2026-08-07T14-22-03Z-3f9a")
    ap.add_argument("--csv", action="store_true",
                    help="Emit CSV on stdout instead of the table.")
    ap.add_argument("--thresholds", default=None,
                    help="Comma-separated g thresholds to report time-above "
                         f"for. Default {','.join(str(t) for t in DEFAULT_THRESHOLDS_G)}.")
    args = ap.parse_args()

    thresholds = DEFAULT_THRESHOLDS_G
    if args.thresholds:
        thresholds = tuple(float(t) for t in args.thresholds.split(","))

    rows = rows_for(args.session_dir, thresholds)
    if not rows:
        print(f"no traces under {args.session_dir}", file=sys.stderr)
        raise SystemExit(1)

    if args.csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=_FLAT,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    else:
        _print_table(rows)


if __name__ == "__main__":
    main()
