"""The Console's function read model: pure dict builders, no engine, no socket.

Mirrors tests/test_room_view.py. The steps are serialized field by field
rather than as raw cue tuples so the browser renders them without re-deriving
MIDI semantics.
"""

import json

from control.cues import ROOM, TARGET, MuteCue, PlayCue, SolidCue
from control.function_view import (
    _step_view,
    function_fired_view,
    function_view,
    functions_view,
)
from control.functions import (
    Condition,
    ConditionSource,
    FunctionKind,
    GeneratorSpec,
    ScriptStep,
    Function,
    FunctionFired,
    FunctionTable,
    FunctionTarget,
    StreamOutput,
    StreamSpec,
)

SWEEP = Function(
    name="play_aurora", description="A slow aurora sweep across the Room",
    target=FunctionTarget.ROOM,
    condition=Condition(name="round_won", description="User wins a round",
                        source=ConditionSource.BIT_ADJUDICATED),
    script=(ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),
            ScriptStep(2.0, (ROOM, 0xB0, 74, 0))))

FLASH = Function(
    name="flash_device", description="Flash the tapping device",
    target=FunctionTarget.DEVICE,
    condition=Condition(name="tapped", description="Player taps their Shroom",
                        source=ConditionSource.GESTURE_VERB, verb="tap"),
    script=(ScriptStep(0.0, PlayCue(TARGET, "click", "")),))


GLOW = Function(
    name="glow", description="ambient breathing glow",
    kind=FunctionKind.GENERATOR,
    generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                            waveform="triangle", period=12.0, lo=0, hi=127))

TILT = Function(
    name="tilt_wash", description="tilt drives the wash brightness",
    kind=FunctionKind.STREAM,
    stream=StreamSpec(verb="tilt", arg=0, in_lo=-90.0, in_hi=90.0,
                      outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),
                               StreamOutput(ROOM, 0xB0, 11, 127.0, 0.0,
                                          mode="abs"))))


def test_a_function_serializes_its_declaration():
    view = function_view(SWEEP)
    assert view["kind"] == "scripted"
    assert view["name"] == "play_aurora"
    assert view["description"] == "A slow aurora sweep across the Room"
    assert view["target"] == "ROOM"
    assert view["condition"] == {
        "name": "round_won", "description": "User wins a round",
        "source": "bit-adjudicated", "verb": None}


def test_a_generator_function_serializes_its_declaration():
    assert function_view(GLOW) == {
        "kind": "generator",
        "name": "glow",
        "description": "ambient breathing glow",
        "lane": {"dev": ROOM, "status": 0xB0, "data1": 74},
        "waveform": "triangle",
        "period": 12.0,
        "lo": 0,
        "hi": 127,
    }


def test_a_stream_function_serializes_its_declaration():
    assert function_view(TILT) == {
        "kind": "stream",
        "name": "tilt_wash",
        "description": "tilt drives the wash brightness",
        "verb": "tilt",
        "arg": 0,
        "in_lo": -90.0,
        "in_hi": 90.0,
        "outputs": [
            {"dev": TARGET, "status": 0xB0, "data1": 74,
             "out_lo": 0.0, "out_hi": 127.0, "mode": "linear"},
            {"dev": ROOM, "status": 0xB0, "data1": 11,
             "out_lo": 127.0, "out_hi": 0.0, "mode": "abs"},
        ],
    }


def test_functions_view_returns_all_kinds_in_declaration_order():
    table = FunctionTable(functions={
        "play_aurora": SWEEP, "glow": GLOW, "tilt_wash": TILT})
    view = functions_view(table)
    assert [v["name"] for v in view] == ["play_aurora", "glow", "tilt_wash"]
    assert [v["kind"] for v in view] == ["scripted", "generator", "stream"]


def test_a_light_step_is_serialized_field_by_field():
    step = function_view(SWEEP)["script"][0]
    assert step == {"offset": 0.0, "kind": "light", "dev": ROOM,
                    "status": 176, "data1": 74, "data2": 127}


def test_a_play_step_carries_its_name_and_params():
    step = function_view(FLASH)["script"][0]
    assert step == {"offset": 0.0, "kind": "play", "dev": TARGET,
                    "name": "click", "params": ""}


def test_a_gesture_condition_reports_its_verb():
    assert function_view(FLASH)["condition"]["verb"] == "tap"


def test_functions_view_preserves_declaration_order():
    table = FunctionTable(functions={"play_aurora": SWEEP, "flash_device": FLASH})
    assert [t["name"] for t in functions_view(table)] == ["play_aurora",
                                                          "flash_device"]


def test_functions_view_of_none_is_empty():
    assert functions_view(None) == []


def test_functions_view_of_an_empty_table_is_empty():
    assert functions_view(FunctionTable(functions={})) == []


def test_the_whole_view_is_json_serializable():
    """It crosses a websocket, so an enum leaking through would fail there
    rather than here."""
    table = FunctionTable(functions={"play_aurora": SWEEP, "flash_device": FLASH})
    json.dumps(functions_view(table))


def test_a_fired_record_keeps_fired_by_and_declared_source_apart():
    record = FunctionFired(
        name="flash_device", condition="tapped", fired_by="admin-manual",
        declared_source="gesture-verb", dev="ie1", devs=("ie1",),
        at=100.0, steps=2)
    view = function_fired_view(record)
    assert view["fired_by"] == "admin-manual"
    assert view["declared_source"] == "gesture-verb"
    assert view["devs"] == ["ie1"]
    json.dumps(view)


def test_a_fired_record_view_carries_room_name_when_the_record_has_one():
    record = FunctionFired(
        name="flash_device", condition="tapped", fired_by="admin-manual",
        declared_source="gesture-verb", dev="ie1", devs=("ie1",),
        at=100.0, steps=2, room_name="atrium")
    assert function_fired_view(record)["room_name"] == "atrium"


def test_a_fired_record_view_room_name_is_none_without_a_room():
    record = FunctionFired(
        name="flash_device", condition="tapped", fired_by="admin-manual",
        declared_source="gesture-verb", dev="ie1", devs=("ie1",),
        at=100.0, steps=2)
    assert function_fired_view(record)["room_name"] is None


def test_step_view_solid_and_mute():
    solid = _step_view(ScriptStep(1.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)))
    assert solid == {"offset": 1.0, "kind": "solid", "dev": TARGET,
                     "rgb": [255, 255, 255], "level": 0.9, "duration": 5.0}
    mute = _step_view(ScriptStep(0.0, MuteCue(TARGET)))
    assert mute == {"offset": 0.0, "kind": "mute", "dev": TARGET}
