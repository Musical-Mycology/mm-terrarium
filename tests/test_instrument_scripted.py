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


def test_bit_scripted_with_a_script_is_refused():
    table = FunctionTable(functions={"aurora": _scripted(
        "aurora", script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
        target=FunctionTarget.SURFACE,
        condition=Condition(name="c", description="d",
                            source=ConditionSource.ADMIN_MANUAL))})
    with pytest.raises(ValueError, match="name-fire"):
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


@pytest.mark.parametrize("owner", ["bit", "instrument"])
@pytest.mark.parametrize("name", ["flash", "stop", "ping"])
def test_reserved_names_refused_for_both_owners(owner, name):
    fn = (_namefire(name) if owner == "bit"
          else _content(name, (ScriptStep(0.0, (TARGET, 0xB0, 74, 1)),)))
    with pytest.raises(ValueError, match="reserved"):
        validate_function_table(FunctionTable(functions={name: fn}),
                                frozenset(), owner=owner)


def test_stream_refused_on_instruments():
    from control.functions import StreamOutput, StreamSpec
    fn = Function(name="s", description="d", kind=FunctionKind.STREAM,
                  stream=StreamSpec(verb="tilt", arg=1, in_lo=-1.0, in_hi=1.0,
                                    outputs=(StreamOutput(TARGET, 0xB0, 74,
                                                          0.0, 127.0),)))
    with pytest.raises(ValueError, match="STREAM"):
        validate_function_table(FunctionTable(functions={"s": fn}),
                                frozenset(), owner="instrument")
