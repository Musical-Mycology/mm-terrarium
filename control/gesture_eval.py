"""Evaluate recorded gesture traces against EventTrigger thresholds and
propose thresholds from labelled capture rows. Pure stdlib.

The magnitude/edge helpers mirror tools/trace_stats.py deliberately (that
module is an offline CLI, not an importable runtime package)."""
from __future__ import annotations

import json
import math
from pathlib import Path

GRAVITY = 9.80665


def _accel_g(samples: dict) -> list[float]:
    return [math.sqrt(x * x + y * y + z * z) / GRAVITY
            for x, y, z in zip(samples["ax"], samples["ay"], samples["az"])]


def _rising_edges(values: list, threshold: float) -> list[int]:
    edges, above = [], False
    for i, value in enumerate(values):
        now = value >= threshold
        if now and not above:
            edges.append(i)
        above = now
    return edges


def evaluate_trace(trace: dict, thresholds: dict) -> dict:
    samples = trace["samples"]
    t_ms = samples["t_ms"]
    accel_g = _accel_g(samples)
    peak_g = thresholds["peak_g"]
    window_ms = thresholds.get("window_ms", 200)
    edges = _rising_edges(accel_g, peak_g)
    fires: list = []
    for i in edges:
        if fires and t_ms[i] - fires[-1] < window_ms:
            continue
        fires.append(t_ms[i])
    result = {
        "fires": fires,
        "peak_dev_g": max((abs(a - 1.0) for a in accel_g), default=0.0),
        "spikes": len(edges),
        "isi_ms": [t_ms[b] - t_ms[a] for a, b in zip(edges, edges[1:])],
    }
    if "double_ms" in thresholds:
        double_ms = thresholds["double_ms"]
        result["double_fires"] = [
            t for prev, t in zip(fires, fires[1:]) if t - prev <= double_ms]
    return result


def propose_thresholds(rows: list) -> dict | None:
    if not rows:
        return None
    proposal = {
        "peak_g": round(0.8 * min(r["peak_dev_g"] for r in rows), 2),
        "window_ms": int(max(r["span_ms"] for r in rows) + 50),
    }
    isi = [v for r in rows for v in r["isi_ms"]]
    if isi:
        proposal["double_ms"] = int(max(isi) + 100)
    return proposal


def session_rows(session_dir: Path) -> list:
    rows = []
    for path in sorted(Path(session_dir).glob("*/[0-9]*.json")):
        trace = json.loads(path.read_text())
        ev = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
        t_ms = trace["samples"]["t_ms"]
        hits = [i for i, g in enumerate(_accel_g(trace["samples"]))
                if g >= 2.0]
        rows.append({
            "label": trace["label"], "capture_id": trace["capture_id"],
            "series": trace["series"], "peak_dev_g": ev["peak_dev_g"],
            "span_ms": (t_ms[hits[-1]] - t_ms[hits[0]]) if hits else 0.0,
            "spikes": ev["spikes"], "isi_ms": ev["isi_ms"],
            "duration_ms": (t_ms[-1] - t_ms[0]) if t_ms else 0.0,
            "n": len(t_ms),
        })
    return rows
