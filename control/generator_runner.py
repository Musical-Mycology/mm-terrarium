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

    Each GENERATOR Function still declares one DECLARED lane (its dev may be
    the ROOM sentinel or a @fixture:<name> dev); `resolve` maps that declared
    dev to the concrete devs it lands on (Room fixture binding, Task 4's
    `_resolve_devs`), and `cues()` emits one tuple per RESOLVED dev per
    declared lane, suppressed and evaluated per resolved lane so one
    fixture's overlay never silences another's.
    """

    def __init__(self, functions: Sequence, resolve=None) -> None:
        # Declared lanes, keyed by the DECLARED (dev, status, data1);
        # validate_function_table guarantees no two share one.
        self._generators: dict[tuple[str, int, int], object] = {}
        for fn in functions:
            spec = fn.generator
            self._generators[(spec.dev, spec.status, spec.data1)] = spec
        # dev sentinel -> concrete devs. None passes the declared dev
        # through unchanged (pure unit tests, and the pre-resolution shape).
        self._resolve = resolve if resolve is not None else (lambda dev: [dev])
        # CONCRETE (dev, status, data1) -> suppression window end, on the
        # absolute `at` timeline. Absent lane == never suppressed.
        self._suppressed_until: dict[tuple[str, int, int], float] = {}

    def suppress(self, lanes: Iterable[tuple[str, int, int]],
                 until_at: float) -> None:
        """Skip emission on `lanes` (CONCRETE (dev, status, data1) tuples)
        while the dispatch `at` is < until_at. The generator's own phase is
        untouched -- suppression only gates `cues()`'s output, never the
        waveform's evolution in elapsed time, so it resumes exactly where
        it would have been."""
        for lane in lanes:
            self._suppressed_until[lane] = until_at

    def cues(self, elapsed: float, at: float) -> list[tuple]:
        """One (resolved dev, status, data1, value) per non-suppressed
        RESOLVED lane, evaluated at `elapsed` seconds into the run. The
        value is computed once per declared lane per tick (not once per
        resolved dev) and shared across every resolved dev that lane
        reaches this tick."""
        out: list[tuple] = []
        for (dev, status, data1), spec in self._generators.items():
            value = None
            for resolved in self._resolve(dev):
                until = self._suppressed_until.get((resolved, status, data1))
                if until is not None and at < until:
                    continue
                if value is None:
                    value = self.value(spec, elapsed)
                out.append((resolved, status, data1, value))
        return out

    @staticmethod
    def value(spec, elapsed: float) -> int:
        """triangle: phase=(elapsed % period)/period; frac = 2*phase if
        phase < 0.5 else 2*(1-phase); int(round(lo + frac * (hi - lo)))."""
        phase = (elapsed % spec.period) / spec.period
        frac = 2 * phase if phase < 0.5 else 2 * (1 - phase)
        return int(round(spec.lo + frac * (spec.hi - spec.lo)))
