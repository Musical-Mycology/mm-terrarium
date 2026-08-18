"""expand_script: a declarative script becomes concrete, timed cues.

Pure and engine-free by construction, which is half the point of scripts being
data: the exact cue sequence a trigger produces is assertable with no Arco, no
renderer and no GameServer.
"""

from control.cues import ROOM, TARGET, LightCue, PlayCue
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTarget,
    expand_script,
)

AT = 100.0


def _trigger(script, target=TriggerTarget.ROOM):
    return Trigger(
        name="t", description="d", target=target,
        condition=Condition(name="c", description="cd",
                            source=ConditionSource.BIT_ADJUDICATED),
        script=script)


def test_an_empty_script_expands_to_nothing():
    assert expand_script(_trigger(()), AT, ["sim-room"]) == []


def test_each_step_carries_its_offset_added_to_at():
    trigger = _trigger((
        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
        ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
        ScriptStep(2.0, (TARGET, 0xB0, 74, 0)),
    ))
    out = expand_script(trigger, AT, ["sim-room"])
    assert [c.when for c in out] == [100.0, 100.5, 102.0]
    assert [c.data2 for c in out] == [127, 40, 0]
    assert all(isinstance(c, LightCue) for c in out)


def test_target_is_substituted_with_the_resolved_dev():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),))
    out = expand_script(trigger, AT, ["sim-room"])
    assert [c.dev for c in out] == ["sim-room"]


def test_target_fans_out_to_every_resolved_dev():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
                       target=TriggerTarget.ALL)
    out = expand_script(trigger, AT, ["sim-room", "ie1", "ie2"])
    assert [c.dev for c in out] == ["sim-room", "ie1", "ie2"]
    assert {c.when for c in out} == {100.0}


def test_target_with_no_resolved_devs_expands_to_nothing():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
                       target=TriggerTarget.DEVICE)
    assert expand_script(trigger, AT, []) == []


def test_a_room_addressed_step_is_left_for_resolve_dev_downstream():
    """ROOM passes through untouched: GameServer._resolve_dev already turns it
    into the Room's bound dev, so this module never needs to know what a Room
    is, and _resolve_dev is not edited by this slice."""
    trigger = _trigger((ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),),
                       target=TriggerTarget.DEVICE)
    out = expand_script(trigger, AT, ["ie1"])
    assert [c.dev for c in out] == [ROOM]


def test_a_room_step_does_not_fan_out_even_when_several_devs_resolved():
    trigger = _trigger((ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),),
                       target=TriggerTarget.ALL)
    out = expand_script(trigger, AT, ["sim-room", "ie1", "ie2"])
    assert [c.dev for c in out] == [ROOM]


def test_a_play_cue_step_keeps_its_name_and_params_and_gains_the_dev():
    trigger = _trigger((ScriptStep(0.0, PlayCue(TARGET, "click", "soft")),),
                       target=TriggerTarget.DEVICE)
    out = expand_script(trigger, AT, ["ie1"])
    assert out == [PlayCue("ie1", "click", "soft")]


def test_a_mixed_script_preserves_declaration_order():
    trigger = _trigger((
        ScriptStep(0.0, PlayCue(TARGET, "click", "")),
        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
    ), target=TriggerTarget.DEVICE)
    out = expand_script(trigger, AT, ["ie1"])
    assert isinstance(out[0], PlayCue)
    assert isinstance(out[1], LightCue)


def test_expansion_never_produces_a_fire_trigger_so_chaining_cannot_cycle():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),))
    out = expand_script(trigger, AT, ["sim-room"])
    assert all(isinstance(c, (LightCue, PlayCue)) for c in out)
