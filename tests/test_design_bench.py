"""Tests for control/design_bench.py -- the offline design-bench engine.

FakeSession stands in for the real BenchSession Protocol (feed_midi/render/
close): design_bench.py must stay stdlib-only and never touch Arco or a real
device transport.
"""
from control.design_bench import DesignBench
from control.cues import TARGET
from control.functions import (Function, FunctionKind, GeneratorSpec,
                               ScriptStep)
from control.instrument import Instrument
from control.triggers import StreamTrigger


class FakeSession:
    def __init__(self):
        self.midi, self.closed = [], False
        self.frame = [0, 0, 0]

    def feed_midi(self, status, d1, d2):
        self.midi.append((status, d1, d2))
        self.frame = [d2, d2, d2]

    def render(self):
        return list(self.frame)

    def close(self):
        self.closed = True


GEN = Function(name="drift", description="", kind=FunctionKind.GENERATOR,
               generator=GeneratorSpec(dev="room", status=0xB0, data1=74,
                                       waveform="triangle", period=8.0))
SCRIPTED = Function(name="pulse", description="two-step cc pulse",
                    kind=FunctionKind.SCRIPTED,
                    script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                            ScriptStep(1.0, (TARGET, 0xB0, 74, 0))))


def make_bench(functions=(), stream_triggers=(), capabilities=("light.pixels",)):
    t = [0.0]
    inst = Instrument(name="bench", functions=tuple(functions),
                      stream_triggers=tuple(stream_triggers),
                      capabilities=frozenset(capabilities))
    session = FakeSession()
    return DesignBench(inst, session, clock=lambda: t[0]), session, t


def test_tick_runs_generators_and_reports_changed_frames():
    bench, session, t = make_bench([GEN])
    first = bench.tick()
    assert first is not None
    t[0] = 2.0
    assert bench.tick() != first
    assert session.midi


def test_tick_returns_none_when_frame_unchanged():
    bench, session, _ = make_bench([])
    assert bench.tick() is not None
    assert bench.tick() is None


def test_fireable_lists_builtins_then_instrument_functions():
    bench, _, _ = make_bench([SCRIPTED])
    names = [(f["name"], f["source"]) for f in bench.fireable()]
    assert ("flash", "builtin") in names
    assert ("stop", "builtin") in names
    assert ("pulse", "instrument") in names


def test_fire_schedules_light_cues_by_offset():
    bench, session, t = make_bench([SCRIPTED])
    assert bench.fire("pulse") is None
    bench.tick()
    assert (0xB0, 74, 127) in session.midi
    assert (0xB0, 74, 0) not in session.midi
    t[0] = 1.5
    bench.tick()
    assert (0xB0, 74, 0) in session.midi


def test_fire_unknown_name_refuses():
    bench, _, _ = make_bench([])
    assert bench.fire("nope") is not None


def test_flash_overrides_frame_white_then_expires():
    # builtins.py: flash is a solid white (255, 255, 255) SolidCue at
    # level=0.9 for duration=5.0s.
    bench, session, t = make_bench([])
    bench.tick()
    assert bench.fire("flash") is None
    frame = bench.tick()
    assert frame is not None and max(frame) > 200   # 255 * 0.9 ~= 230
    t[0] = 5.1                                       # past the 5 s duration
    late = bench.tick()
    assert late is not None and max(late) < 200


def test_stop_latches_black_until_next_fire():
    bench, session, t = make_bench([SCRIPTED])
    bench.tick()
    assert bench.fire("stop") is None
    assert set(bench.tick()) == {0}
    assert bench.fire("pulse") is None
    t[0] = 0.1
    frame = bench.tick()
    assert frame is not None and max(frame) > 0


def test_lane_applies_smooth_stream_trigger_and_scales():
    # _apply_stream_triggers (control/engine.py) seeds the EMA from the
    # FIRST sample (y = x when y_prev is None), rather than from 0. A
    # constant input therefore converges immediately and would not show two
    # distinct values, so this drives two different raw inputs: -1.0 seeds
    # y=-1.0 (scaled to data2=0), then 1.0 blends with alpha=0.5 to y=0.0
    # (scaled to data2=64 with Python's round-half-to-even).
    trig = StreamTrigger(name="s", description="", verb="tilt", arg=0,
                         transform="smooth", params={"alpha": 0.5})
    bench, session, _ = make_bench([], [trig])
    bench.lane("tilt", -1.0, 0xB0, 74)
    bench.lane("tilt", 1.0, 0xB0, 74)
    values = [d2 for (s, d1, d2) in session.midi if d1 == 74]
    assert len(values) == 2 and values[0] < values[1] <= 127


def test_close_closes_session():
    bench, session, _ = make_bench([])
    bench.close()
    assert session.closed
