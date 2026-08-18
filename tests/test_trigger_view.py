"""The Console's trigger read model: pure dict builders, no engine, no socket.

Mirrors tests/test_room_view.py. The steps are serialized field by field
rather than as raw cue tuples so the browser renders them without re-deriving
MIDI semantics.
"""

import json

from control.cues import ROOM, TARGET, PlayCue
from control.trigger_view import (
    trigger_fired_view,
    trigger_view,
    triggers_view,
)
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerFired,
    TriggerTable,
    TriggerTarget,
)

SWEEP = Trigger(
    name="play_aurora", description="A slow aurora sweep across the Room",
    target=TriggerTarget.ROOM,
    condition=Condition(name="round_won", description="User wins a round",
                        source=ConditionSource.BIT_ADJUDICATED),
    script=(ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),
            ScriptStep(2.0, (ROOM, 0xB0, 74, 0))))

FLASH = Trigger(
    name="flash_device", description="Flash the tapping device",
    target=TriggerTarget.DEVICE,
    condition=Condition(name="tapped", description="Player taps their Shroom",
                        source=ConditionSource.GESTURE_VERB, verb="tap"),
    script=(ScriptStep(0.0, PlayCue(TARGET, "click", "")),))


def test_a_trigger_serializes_its_declaration():
    view = trigger_view(SWEEP)
    assert view["name"] == "play_aurora"
    assert view["description"] == "A slow aurora sweep across the Room"
    assert view["target"] == "ROOM"
    assert view["condition"] == {
        "name": "round_won", "description": "User wins a round",
        "source": "bit-adjudicated", "verb": None}


def test_a_light_step_is_serialized_field_by_field():
    step = trigger_view(SWEEP)["script"][0]
    assert step == {"offset": 0.0, "kind": "light", "dev": ROOM,
                    "status": 176, "data1": 74, "data2": 127}


def test_a_play_step_carries_its_name_and_params():
    step = trigger_view(FLASH)["script"][0]
    assert step == {"offset": 0.0, "kind": "play", "dev": TARGET,
                    "name": "click", "params": ""}


def test_a_gesture_condition_reports_its_verb():
    assert trigger_view(FLASH)["condition"]["verb"] == "tap"


def test_triggers_view_preserves_declaration_order():
    table = TriggerTable(triggers={"play_aurora": SWEEP, "flash_device": FLASH})
    assert [t["name"] for t in triggers_view(table)] == ["play_aurora",
                                                          "flash_device"]


def test_triggers_view_of_none_is_empty():
    assert triggers_view(None) == []


def test_triggers_view_of_an_empty_table_is_empty():
    assert triggers_view(TriggerTable(triggers={})) == []


def test_the_whole_view_is_json_serializable():
    """It crosses a websocket, so an enum leaking through would fail there
    rather than here."""
    table = TriggerTable(triggers={"play_aurora": SWEEP, "flash_device": FLASH})
    json.dumps(triggers_view(table))


def test_a_fired_record_keeps_fired_by_and_declared_source_apart():
    record = TriggerFired(
        name="flash_device", condition="tapped", fired_by="admin-manual",
        declared_source="gesture-verb", dev="ie1", devs=("ie1",),
        at=100.0, steps=2)
    view = trigger_fired_view(record)
    assert view["fired_by"] == "admin-manual"
    assert view["declared_source"] == "gesture-verb"
    assert view["devs"] == ["ie1"]
    json.dumps(view)
