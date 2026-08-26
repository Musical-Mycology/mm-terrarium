"""Declaration shape and load-time validation for Bit-declared triggers.

Mirrors tests/test_role_config.py's discipline: one case per refusal, each
asserting the message locates the offending field, because a Bit author reads
the message and nothing else.
"""

import pytest

from control.cues import ROOM, TARGET, FireTrigger, LightCue, MuteCue, PlayCue, SolidCue
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTable,
    TriggerTarget,
    expand_script,
    validate_trigger_table,
)

VERBS = {"tap", "tilt"}


def _condition(**overrides):
    base = dict(name="round_won", description="User wins a round",
                source=ConditionSource.BIT_ADJUDICATED)
    base.update(overrides)
    return Condition(**base)


def _trigger(**overrides):
    base = dict(name="play_aurora", description="A slow aurora sweep",
                target=TriggerTarget.ROOM, condition=_condition(),
                script=(ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),))
    base.update(overrides)
    return Trigger(**base)


def _table(trigger):
    return TriggerTable(triggers={trigger.name: trigger})


def test_a_well_formed_table_validates():
    validate_trigger_table(_table(_trigger()), VERBS)


def test_an_empty_table_validates():
    validate_trigger_table(TriggerTable(triggers={}), VERBS)


def test_an_empty_script_is_legal_and_means_observe_only():
    validate_trigger_table(_table(_trigger(script=())), VERBS)


def test_a_key_disagreeing_with_its_trigger_name_is_refused():
    table = TriggerTable(triggers={"mislabelled": _trigger()})
    with pytest.raises(ValueError, match="does not match its key"):
        validate_trigger_table(table, VERBS)


def test_a_name_with_characters_illegal_in_a_dom_id_is_refused():
    with pytest.raises(ValueError, match="play aurora"):
        validate_trigger_table(_table(_trigger(name="play aurora")), VERBS)


def test_an_empty_trigger_description_is_refused():
    with pytest.raises(ValueError, match="description must be non-empty"):
        validate_trigger_table(_table(_trigger(description="")), VERBS)


def test_an_empty_condition_description_is_refused():
    bad = _condition(description="")
    with pytest.raises(ValueError, match="description must be non-empty"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_gesture_verb_condition_naming_an_unimplemented_verb_is_refused():
    """Goal 4: declared-but-unimplemented fails at load, not mid-installation."""
    bad = _condition(source=ConditionSource.GESTURE_VERB, verb="wiggle")
    with pytest.raises(ValueError, match="'wiggle' is not implemented"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_gesture_verb_condition_with_no_verb_is_refused():
    bad = _condition(source=ConditionSource.GESTURE_VERB, verb=None)
    with pytest.raises(ValueError, match="must name a verb"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_verb_on_a_non_gesture_condition_is_refused():
    bad = _condition(source=ConditionSource.BIT_ADJUDICATED, verb="tap")
    with pytest.raises(ValueError, match="only meaningful on a gesture-verb"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_negative_offset_is_refused():
    bad = (ScriptStep(-0.5, (ROOM, 0xB0, 74, 127)),)
    with pytest.raises(ValueError, match=r"script\[0\]: offset must be >= 0"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_out_of_order_offsets_are_refused():
    bad = (ScriptStep(1.0, (ROOM, 0xB0, 74, 127)),
           ScriptStep(0.5, (ROOM, 0xB0, 74, 0)))
    with pytest.raises(ValueError, match="non-decreasing order"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_light_cue_in_a_script_is_refused():
    """The offset IS the timing; LightCue is what expansion produces."""
    bad = (ScriptStep(0.0, LightCue(ROOM, 0xB0, 74, 127, when=1.0)),)
    with pytest.raises(ValueError, match="names its own absolute time"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_play_cue_at_a_non_zero_offset_is_refused():
    bad = (ScriptStep(1.5, PlayCue(TARGET, "click", "")),)
    with pytest.raises(ValueError, match="must sit at offset 0"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_play_cue_at_offset_zero_is_accepted():
    good = (ScriptStep(0.0, PlayCue(TARGET, "click", "")),)
    validate_trigger_table(_table(_trigger(script=good)), VERBS)


def test_a_literal_dev_id_in_a_step_is_refused():
    bad = (ScriptStep(0.0, ("ie1", 0xB0, 74, 127)),)
    with pytest.raises(ValueError, match="assigned at runtime"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_wrong_arity_cue_tuple_is_refused():
    bad = (ScriptStep(0.0, (ROOM, 0xB0, 74)),)
    with pytest.raises(ValueError, match="4-tuple"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_data_byte_outside_0_255_is_refused():
    bad = (ScriptStep(0.0, (ROOM, 0xB0, 74, 300)),)
    with pytest.raises(ValueError, match="data2 300 is outside 0-255"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_fire_trigger_in_a_script_is_refused_so_chaining_cannot_cycle():
    bad = (ScriptStep(0.0, FireTrigger("play_aurora")),)
    with pytest.raises(ValueError, match="4-tuple"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_target_is_used_by_name_not_by_value():
    assert {t.name for t in TriggerTarget} == {"ROOM", "DEVICE", "ALL"}


def test_fire_trigger_defaults_its_dev_to_none():
    assert FireTrigger("x").dev is None
    assert FireTrigger("x", "ie1").dev == "ie1"


def test_solid_cue_step_validates():
    good = (ScriptStep(1.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)),)
    validate_trigger_table(_table(_trigger(script=good)), VERBS)


def test_solid_cue_bad_level_refused():
    bad = (ScriptStep(0.0, SolidCue(TARGET, (255, 255, 255), 1.5, 5.0)),)
    with pytest.raises(ValueError, match="level"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_mute_cue_nonzero_offset_refused():
    bad = (ScriptStep(0.5, MuteCue(TARGET)),)
    with pytest.raises(ValueError, match="offset 0"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_expand_solid_cue_fans_out_with_when():
    trig = _trigger(script=(
        ScriptStep(2.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)),))
    out = expand_script(trig, at=100.0, devs=["d1", "d2"])
    assert [c.dev for c in out] == ["d1", "d2"]
    assert all(isinstance(c, SolidCue) and c.when == 102.0 for c in out)


def test_expand_mute_cue_fans_out():
    trig = _trigger(script=(ScriptStep(0.0, MuteCue(TARGET)),))
    out = expand_script(trig, at=100.0, devs=["d1"])
    assert out == [MuteCue("d1")]
