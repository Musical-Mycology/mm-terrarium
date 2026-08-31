import pytest
from control.cues import ROOM, TARGET
from control.functions import (
    Condition, ConditionSource, Function, FunctionKind, FunctionTable,
    FunctionTarget, ScriptStep, validate_function_table,
)


def _scripted(name, script=(), target=None, condition=None):
    return Function(name=name, description="d", kind=FunctionKind.SCRIPTED,
                    script=script, target=target, condition=condition)


def _namefire(name):
    return _scripted(name, script=(), target=FunctionTarget.SURFACE,
                     condition=Condition(name="c", description="d",
                                         source=ConditionSource.ADMIN_MANUAL))


def _content(name, script):
    return _scripted(name, script)


def test_bit_scripted_with_a_script_is_accepted():
    table = FunctionTable(functions={"aurora": _scripted(
        "aurora", script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
        target=FunctionTarget.SURFACE,
        condition=Condition(name="c", description="d",
                            source=ConditionSource.ADMIN_MANUAL))})
    validate_function_table(table, frozenset(), owner="bit")


def test_bit_namefire_passes():
    table = FunctionTable(functions={"aurora": _namefire("aurora")})
    validate_function_table(table, frozenset(), owner="bit")


def test_instrument_scripted_requires_content_and_no_condition_or_target():
    ok = FunctionTable(functions={"aurora": _content(
        "aurora", (ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),))})
    validate_function_table(ok, frozenset(), owner="instrument")
    empty = FunctionTable(functions={"aurora": _content("aurora", ())})
    with pytest.raises(ValueError, match="non-empty script"):
        validate_function_table(empty, frozenset(), owner="instrument")
    with_target = FunctionTable(functions={"aurora": _scripted(
        "aurora", (ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
        target=FunctionTarget.SURFACE)})
    with pytest.raises(ValueError, match="target"):
        validate_function_table(with_target, frozenset(), owner="instrument")


def test_instrument_script_may_not_address_the_room_sentinel():
    table = FunctionTable(functions={"aurora": _content(
        "aurora", (ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),))})
    with pytest.raises(ValueError, match="TARGET"):
        validate_function_table(table, frozenset(), owner="instrument")


@pytest.mark.parametrize("name", ["flash", "stop", "ping"])
def test_reserved_names_refused_on_instruments(name):
    fn = _content(name, (ScriptStep(0.0, (TARGET, 0xB0, 74, 1)),))
    with pytest.raises(ValueError, match="reserved"):
        validate_function_table(FunctionTable(functions={name: fn}),
                                frozenset(), owner="instrument")


def test_stream_refused_on_instruments():
    from control.functions import StreamOutput, StreamSpec
    fn = Function(name="s", description="d", kind=FunctionKind.STREAM,
                  stream=StreamSpec(verb="tilt", arg=1, in_lo=-1.0, in_hi=1.0,
                                    outputs=(StreamOutput(TARGET, 0xB0, 74,
                                                          0.0, 127.0),)))
    with pytest.raises(ValueError, match="STREAM"):
        validate_function_table(FunctionTable(functions={"s": fn}),
                                frozenset(), owner="instrument")


def test_tuneshroom_declares_its_scripted_vocabulary():
    from control.instrument import TUNESHROOM, validate_instrument
    from control.functions import FunctionKind
    names = {fn.name for fn in TUNESHROOM.functions
             if fn.kind is FunctionKind.SCRIPTED}
    assert names == {"play_aurora", "win", "fireworks_player",
                     "fail_player", "metro_pulse_player", "metro_recovery"}
    validate_instrument(TUNESHROOM)   # v1 rules hold


def test_tuneshroom_fireworks_matches_the_bits_seeded_script():
    # Deterministic (random.Random(2026)) -- 12 flashes, 36 steps.
    from control.instrument import TUNESHROOM
    fw = next(fn for fn in TUNESHROOM.functions
              if fn.name == "fireworks_player")
    assert len(fw.script) == 36
    assert fw.script[0].offset == 0.0


def test_scripted_step_cue_kind_not_in_accepted_cues_is_refused_at_load():
    from control.cues import SolidCue
    from control.instrument import Instrument, InstrumentError, validate_instrument
    fn = _content("glow", (ScriptStep(0.0, SolidCue(TARGET, (255, 0, 0), 1.0, None)),))
    instrument = Instrument(name="dev_strip", capabilities=frozenset(),
                            functions=(fn,), accepted_cues=("midi",))
    with pytest.raises(InstrumentError) as exc:
        validate_instrument(instrument)
    message = str(exc.value)
    assert "dev_strip" in message
    assert "glow" in message
    assert "script[0]" in message


def test_function_view_tolerates_an_instrument_scripted_functions_targetless_condition():
    # Instrument SCRIPTED functions declare neither target nor condition
    # (validate_function_table's owner="instrument" rules forbid both) --
    # unlike a Bit name-fire, which always has both. function_view must
    # render None for each rather than raising.
    from control.function_view import function_view
    from control.instrument import TUNESHROOM
    fn = next(f for f in TUNESHROOM.functions if f.name == "play_aurora")
    view = function_view(fn)
    assert view["target"] is None
    assert view["condition"] is None
