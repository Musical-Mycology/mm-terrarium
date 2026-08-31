"""GeneratorRunner: engine-run evaluation of a Bit's declared GENERATOR
Functions. See docs/superpowers/specs/2026-08-27-functions-and-trigger-rename-
design.md section 4.

Pure and stdlib-only, deliberately: it owns no clock of its own and every
method is a pure function of the arguments it's given, so a test can assert
the exact value at a given elapsed time with no Arco and no wall clock.

GameServer owns the one instance for the loaded Bit's declared generators
(built at load_bit from the validated FunctionTable's GENERATOR functions)
and drives it every RUNNING tick from its own elapsed-run clock.
"""

from __future__ import annotations

from typing import Iterable, Sequence


class GeneratorRunner:
    """Evaluates each declared GENERATOR Function's waveform every tick and
    applies per-lane overlay suppression when a scripted Function fires on
    the same lane (spec section 4: "scripted overlays rather than kills").
    """

    def __init__(self, functions: Sequence) -> None:
        # One entry per declared generator, keyed by its element lane
        # (resolved dev, status, data1) -- validate_function_table already
        # guarantees no two GENERATOR functions share a lane, so this dict
        # never silently drops one.
        self._generators: dict[tuple[str, int, int], object] = {}
        for fn in functions:
            spec = fn.generator
            lane = (spec.dev, spec.status, spec.data1)
            self._generators[lane] = spec
        # Per-lane suppression window end, on the absolute `at` timeline.
        # Absent lane == never suppressed.
        self._suppressed_until: dict[tuple[str, int, int], float] = {}

    def suppress(self, lanes: Iterable[tuple[str, int, int]],
                 until_at: float) -> None:
        """Skip emission on `lanes` while the dispatch `at` is < until_at.
        The generator's own phase is untouched -- suppression only gates
        `cues()`'s output, never the waveform's evolution in elapsed time,
        so it resumes exactly where it would have been."""
        for lane in lanes:
            self._suppressed_until[lane] = until_at

    def cues(self, elapsed: float, at: float) -> list[tuple]:
        """One (dev, status, data1, value) per non-suppressed declared
        lane, evaluated at `elapsed` seconds into the run."""
        out: list[tuple] = []
        for lane, spec in self._generators.items():
            until = self._suppressed_until.get(lane)
            if until is not None and at < until:
                continue
            dev, status, data1 = lane
            out.append((dev, status, data1, self.value(spec, elapsed)))
        return out

    @staticmethod
    def value(spec, elapsed: float) -> int:
        """triangle: phase=(elapsed % period)/period; frac = 2*phase if
        phase < 0.5 else 2*(1-phase); int(round(lo + frac * (hi - lo)))."""
        phase = (elapsed % spec.period) / spec.period
        frac = 2 * phase if phase < 0.5 else 2 * (1 - phase)
        return int(round(spec.lo + frac * (spec.hi - spec.lo)))
