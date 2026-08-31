"""control/generator_runner.py: pure, stdlib-only, deterministic-in-elapsed
waveform evaluation and per-lane overlay suppression. See docs/superpowers/
specs/2026-08-27-functions-and-trigger-rename-design.md section 4."""

from control.cues import ROOM
from control.functions import Function, FunctionKind, GeneratorSpec
from control.generator_runner import GeneratorRunner


def _gen(dev=ROOM, status=0xB0, data1=74, period=12.0, lo=0, hi=254):
    return Function(
        name="drift", description="Ambient drift", kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=dev, status=status, data1=data1,
                                waveform="triangle", period=period, lo=lo, hi=hi))


def test_triangle_value_deterministic():
    spec = GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                         waveform="triangle", period=12.0, lo=0, hi=254)
    assert GeneratorRunner.value(spec, 0.0) == 0
    assert GeneratorRunner.value(spec, 3.0) == 127
    assert GeneratorRunner.value(spec, 6.0) == 254
    assert GeneratorRunner.value(spec, 9.0) == 127


def test_cues_emits_one_tuple_per_declared_generator():
    runner = GeneratorRunner([_gen()])
    assert runner.cues(elapsed=6.0, at=100.0) == [(ROOM, 0xB0, 74, 254)]


def test_cues_is_empty_with_no_generators():
    runner = GeneratorRunner([])
    assert runner.cues(elapsed=6.0, at=100.0) == []


def test_suppression_skips_lane_but_phase_advances():
    runner = GeneratorRunner([_gen()])
    runner.suppress([(ROOM, 0xB0, 74)], until_at=105.0)
    assert runner.cues(elapsed=3.0, at=100.0) == []
    assert runner.cues(elapsed=6.0, at=105.0) == [(ROOM, 0xB0, 74, 254)]


def test_suppression_is_per_lane_not_global():
    gen_a = _gen(data1=74)
    gen_b = _gen(data1=75)
    runner = GeneratorRunner([gen_a, gen_b])
    runner.suppress([(ROOM, 0xB0, 74)], until_at=105.0)
    cues = runner.cues(elapsed=6.0, at=100.0)
    assert cues == [(ROOM, 0xB0, 75, 254)]
