"""Measure how closely the audio call and the LED frame land on one cue.

Reports worst and p95 alongside the mean, for the same reason
harness/render_bench.py does: a path that averages 2 ms while missing by
200 ms once a second reads as healthy and is not.

EVERY FIGURE THIS PRODUCES IS A DEV-BOX FIGURE. The venue target is
bare-metal Linux on a Raspberry Pi 5 relaying every hop through the same
process doing all room synthesis while feeding a 44 Hz render loop. No
venue-box measurement exists; the box does not exist. Do not quote these
numbers as venue latency, and do not derive BootConfig.cue_horizon for a
venue from them.

summarise() takes no luxaeterna and no pyarco dependency, so it runs in the
core offline suite.
"""

from __future__ import annotations


def _percentile(sorted_ms: list[float], fraction: float) -> float:
    """Nearest-rank pick off an already-sorted list.

    Extracted from what summarise() already did inline for p95, so p95 and
    p99 cannot drift apart into two different conventions.
    """
    index = min(len(sorted_ms) - 1, int(len(sorted_ms) * fraction))
    return sorted_ms[index]


def summarise(deltas: list[float]) -> dict:
    """Reduce signed second-deltas (audio time minus light time) to stats.

    Absolute values throughout: light landing 10 ms early is as wrong as 10
    ms late, and signed averaging would let the two cancel into a
    flattering zero.

    Callers measuring one-directional LATENCY rather than a two-sided
    agreement error should convert to absolute latency BEFORE calling this
    (see harness/o2_shroom.py). Latency is always positive, so the abs()
    above is then a no-op and every figure reads as a real latency. Handing
    this signed lateness instead would report a frame arriving a healthy
    80 ms EARLY as 80 ms of error.
    """
    if not deltas:
        return {"count": 0, "mean_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
                "worst_ms": 0.0}
    magnitudes = sorted(abs(d) * 1000.0 for d in deltas)
    return {
        "count": len(magnitudes),
        "mean_ms": sum(magnitudes) / len(magnitudes),
        "p95_ms": _percentile(magnitudes, 0.95),
        "p99_ms": _percentile(magnitudes, 0.99),
        "worst_ms": magnitudes[-1],
    }


def format_report(stats: dict, *, label: str = "") -> str:
    """One-line-per-figure rendering, worst last so the tail is the thing
    left on screen rather than the mean."""
    head = f"{label}\n" if label else ""
    return (f"{head}"
            f"  frames : {stats['count']}\n"
            f"  mean   : {stats['mean_ms']:.1f} ms\n"
            f"  p95    : {stats['p95_ms']:.1f} ms\n"
            f"  p99    : {stats['p99_ms']:.1f} ms\n"
            f"  worst  : {stats['worst_ms']:.1f} ms")


def main() -> None:
    """python -m harness.sync_bench SAMPLES.json

    harness/terrarium_boot.py's --horizon help has pointed here since the
    timed-cue slice landed, but the entry point never existed, so following
    that instruction failed. It reads a JSON list of second-deltas -- the
    shape harness/o2_shroom.py --samples-out writes.
    """
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Summarise measured deltas.")
    ap.add_argument("samples", help="JSON file of second-deltas, or - for stdin")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="Seconds added to every sample before summarising. "
                         "Converts signed lateness (now - when) into absolute "
                         "end-to-end latency by adding back the horizon the "
                         "run used. See the o2lite cue-latency design spec.")
    args = ap.parse_args()

    raw = sys.stdin.read() if args.samples == "-" else \
        open(args.samples, encoding="utf-8").read()
    deltas = [float(d) + args.offset for d in json.loads(raw)]
    print(format_report(summarise(deltas)))


if __name__ == "__main__":
    main()
