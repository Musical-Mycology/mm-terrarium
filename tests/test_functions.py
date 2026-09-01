"""Declaration shape and load-time validation for Bit-declared functions.

Mirrors tests/test_role_config.py's discipline: one case per refusal, each
asserting the message locates the offending field, because a Bit author reads
the message and nothing else.
"""

import pytest

from control.cues import ROOM, TARGET, FireFunction, LightCue, MuteCue, PlayCue, SolidCue
from control.functions import (
    Condition,
    ConditionSource,
    FunctionKind,
    GeneratorSpec,
    ScriptStep,
    StreamOutput,
    StreamSpec,
    Function,
    FunctionTable,
    FunctionTarget,
    collect_stream_cues,
    expand_script,
    stream_cues,
    validate_function_table,
)

VERBS = {"tap", "tilt"}


def _condition(**overrides):
    base = dict(name="round_won", description="User wins a round",
                source=ConditionSource.BIT_ADJUDICATED)
    base.update(overrides)
    return Condition(**base)


def _function(**overrides):
    base = dict(name="play_aurora", description="A slow aurora sweep",
                target=FunctionTarget.ROOM, condition=_condition(),
                script=(ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),))
    base.update(overrides)
    return Function(**base)


def _table(function_decl):
    return FunctionTable(functions={function_decl.name: function_decl})


def test_a_well_formed_table_validates():
    validate_function_table(_table(_function()), VERBS)


def test_an_empty_table_validates():
    validate_function_table(FunctionTable(functions={}), VERBS)


def test_an_empty_script_is_legal_and_means_observe_only():
    validate_function_table(_table(_function(script=())), VERBS)


def test_a_key_disagreeing_with_its_function_name_is_refused():
    table = FunctionTable(functions={"mislabelled": _function()})
    with pytest.raises(ValueError, match="does not match its key"):
        validate_function_table(table, VERBS)


def test_a_name_with_characters_illegal_in_a_dom_id_is_refused():
    with pytest.raises(ValueError, match="play aurora"):
        validate_function_table(_table(_function(name="play aurora")), VERBS)


def test_an_empty_function_description_is_refused():
    with pytest.raises(ValueError, match="description must be non-empty"):
        validate_function_table(_table(_function(description="")), VERBS)


def test_bit_declared_reserved_name_is_refused():
    table = _table(_function(name="stop"))
    with pytest.raises(ValueError, match="reserved built-in"):
        validate_function_table(table, VERBS, owner="bit")


def test_an_empty_condition_description_is_refused():
    bad = _condition(description="")
    with pytest.raises(ValueError, match="description must be non-empty"):
        validate_function_table(_table(_function(condition=bad)), VERBS)


def test_a_gesture_verb_condition_naming_an_unimplemented_verb_is_refused():
    """Goal 4: declared-but-unimplemented fails at load, not mid-installation."""
    bad = _condition(source=ConditionSource.GESTURE_VERB, verb="wiggle")
    with pytest.raises(ValueError, match="'wiggle' is not implemented"):
        validate_function_table(_table(_function(condition=bad)), VERBS)


def test_a_gesture_verb_condition_with_no_verb_is_refused():
    bad = _condition(source=ConditionSource.GESTURE_VERB, verb=None)
    with pytest.raises(ValueError, match="must name a verb"):
        validate_function_table(_table(_function(condition=bad)), VERBS)


def test_a_verb_on_a_non_gesture_condition_is_refused():
    bad = _condition(source=ConditionSource.BIT_ADJUDICATED, verb="tap")
    with pytest.raises(ValueError, match="only meaningful on a gesture-verb"):
        validate_function_table(_table(_function(condition=bad)), VERBS)


def test_a_negative_offset_is_refused():
    bad = (ScriptStep(-0.5, (ROOM, 0xB0, 74, 127)),)
    with pytest.raises(ValueError, match=r"script\[0\]: offset must be >= 0"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_out_of_order_offsets_are_refused():
    bad = (ScriptStep(1.0, (ROOM, 0xB0, 74, 127)),
           ScriptStep(0.5, (ROOM, 0xB0, 74, 0)))
    with pytest.raises(ValueError, match="non-decreasing order"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_a_light_cue_in_a_script_is_refused():
    """The offset IS the timing; LightCue is what expansion produces."""
    bad = (ScriptStep(0.0, LightCue(ROOM, 0xB0, 74, 127, when=1.0)),)
    with pytest.raises(ValueError, match="names its own absolute time"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_a_play_cue_at_a_non_zero_offset_is_refused():
    bad = (ScriptStep(1.5, PlayCue(TARGET, "click", "")),)
    with pytest.raises(ValueError, match="must sit at offset 0"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_a_play_cue_at_offset_zero_is_accepted():
    good = (ScriptStep(0.0, PlayCue(TARGET, "click", "")),)
    validate_function_table(_table(_function(script=good)), VERBS)


def test_a_literal_dev_id_in_a_step_is_refused():
    bad = (ScriptStep(0.0, ("ie1", 0xB0, 74, 127)),)
    with pytest.raises(ValueError, match="assigned at runtime"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_a_wrong_arity_cue_tuple_is_refused():
    bad = (ScriptStep(0.0, (ROOM, 0xB0, 74)),)
    with pytest.raises(ValueError, match="4-tuple"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_a_data_byte_outside_0_255_is_refused():
    bad = (ScriptStep(0.0, (ROOM, 0xB0, 74, 300)),)
    with pytest.raises(ValueError, match="data2 300 is outside 0-255"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_a_fire_function_in_a_script_is_refused_so_chaining_cannot_cycle():
    bad = (ScriptStep(0.0, FireFunction("play_aurora")),)
    with pytest.raises(ValueError, match="4-tuple"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_target_is_used_by_name_not_by_value():
    assert {t.name for t in FunctionTarget} == {"ROOM", "DEVICE", "ALL", "SURFACE"}


def test_fire_function_defaults_its_dev_to_none():
    assert FireFunction("x").dev is None
    assert FireFunction("x", "ie1").dev == "ie1"


def test_solid_cue_step_validates():
    good = (ScriptStep(1.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)),)
    validate_function_table(_table(_function(script=good)), VERBS)


def test_solid_cue_bad_level_refused():
    bad = (ScriptStep(0.0, SolidCue(TARGET, (255, 255, 255), 1.5, 5.0)),)
    with pytest.raises(ValueError, match="level"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_mute_cue_nonzero_offset_refused():
    bad = (ScriptStep(0.5, MuteCue(TARGET)),)
    with pytest.raises(ValueError, match="offset 0"):
        validate_function_table(_table(_function(script=bad)), VERBS)


def test_expand_solid_cue_fans_out_with_when():
    fn = _function(script=(
        ScriptStep(2.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)),))
    out = expand_script(fn, at=100.0, devs=["d1", "d2"])
    assert [c.dev for c in out] == ["d1", "d2"]
    assert all(isinstance(c, SolidCue) and c.when == 102.0 for c in out)


def test_expand_mute_cue_fans_out():
    fn = _function(script=(ScriptStep(0.0, MuteCue(TARGET)),))
    out = expand_script(fn, at=100.0, devs=["d1"])
    assert out == [MuteCue("d1")]


def _gen(name="drift", dev=ROOM, data1=74, period=12.0, lo=0, hi=254):
    return Function(name=name, description="d", kind=FunctionKind.GENERATOR,
                    generator=GeneratorSpec(dev=dev, status=0xB0, data1=data1,
                                            waveform="triangle",
                                            period=period, lo=lo, hi=hi))


def _stream(name="tilt_hue", verb="tilt", in_lo=-90.0, in_hi=90.0,
            outputs=None):
    outputs = outputs or (StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),)
    return Function(name=name, description="d", kind=FunctionKind.STREAM,
                    stream=StreamSpec(verb=verb, arg=1, in_lo=in_lo,
                                      in_hi=in_hi, outputs=outputs))


def test_generator_function_validates():
    validate_function_table(FunctionTable(functions={"drift": _gen()}), set())


def test_scripted_function_refuses_generator_field():
    bad = _function(generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                            waveform="triangle", period=1.0))
    with pytest.raises(ValueError, match="generator"):
        validate_function_table(_table(bad), VERBS)


def test_generator_refuses_script_and_unknown_waveform():
    bad_script = _gen()
    bad_script = Function(name=bad_script.name, description=bad_script.description,
                          kind=FunctionKind.GENERATOR, generator=bad_script.generator,
                          script=(ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),))
    with pytest.raises(ValueError, match="script"):
        validate_function_table(_table(bad_script), set())

    bad_waveform = Function(
        name="drift", description="d", kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                waveform="sine", period=1.0))
    with pytest.raises(ValueError, match="waveform"):
        validate_function_table(_table(bad_waveform), set())


def test_two_generators_same_lane_refused():
    table = FunctionTable(functions={
        "a": _gen("a"), "b": _gen("b")})
    with pytest.raises(ValueError, match="lane"):
        validate_function_table(table, set())


def test_streams_same_lane_overlapping_domains_refused():
    overlapping = FunctionTable(functions={
        "a": _stream("a", in_lo=-90.0, in_hi=10.0),
        "b": _stream("b", in_lo=0.0, in_hi=90.0),
    })
    with pytest.raises(ValueError, match="overlap"):
        validate_function_table(overlapping, {"tilt"})

    adjacent = FunctionTable(functions={
        "a": _stream("a", in_lo=-90.0, in_hi=0.0),
        "b": _stream("b", in_lo=0.0, in_hi=90.0),
    })
    validate_function_table(adjacent, {"tilt"})


def test_stream_bad_mode_and_inverted_domain_refused():
    bad_mode = _stream()
    bad_mode = Function(
        name=bad_mode.name, description=bad_mode.description,
        kind=FunctionKind.STREAM,
        stream=StreamSpec(verb="tilt", arg=1, in_lo=-90.0, in_hi=90.0,
                          outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0,
                                                mode="bogus"),)))
    with pytest.raises(ValueError, match="mode"):
        validate_function_table(_table(bad_mode), {"tilt"})

    inverted = Function(
        name="tilt_hue", description="d", kind=FunctionKind.STREAM,
        stream=StreamSpec(verb="tilt", arg=1, in_lo=90.0, in_hi=-90.0,
                          outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),)))
    with pytest.raises(ValueError, match="in_lo"):
        validate_function_table(_table(inverted), {"tilt"})


def test_stream_cues_clamps_and_maps_linearly():
    fn = _stream(in_lo=-90.0, in_hi=90.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),))
    assert stream_cues(fn, "d1", [None, -90.0]) == [("d1", 0xB0, 74, 0)]
    assert stream_cues(fn, "d1", [None, 90.0]) == [("d1", 0xB0, 74, 127)]
    assert stream_cues(fn, "d1", [None, 0.0]) == [("d1", 0xB0, 74, 64)]
    # Out-of-domain input clamps to the nearer bound rather than raising.
    assert stream_cues(fn, "d1", [None, 200.0]) == [("d1", 0xB0, 74, 127)]
    assert stream_cues(fn, "d1", [None, -200.0]) == [("d1", 0xB0, 74, 0)]


def test_stream_cues_inverted_output_range():
    fn = _stream(in_lo=0.0, in_hi=90.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 127.0, 0.0),))
    assert stream_cues(fn, "d1", [None, 0.0]) == [("d1", 0xB0, 74, 127)]
    assert stream_cues(fn, "d1", [None, 90.0]) == [("d1", 0xB0, 74, 0)]


def test_stream_cues_abs_mode_folds_negative_domain():
    # A tilt argument of +/-90 both map to the same output magnitude when
    # mode="abs" takes abs(x) before clamping to the output's own [0, 90]
    # domain -- a negative raw value is not out-of-domain here, it folds.
    fn = _stream(in_lo=0.0, in_hi=90.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0,
                                       mode="abs"),))
    assert stream_cues(fn, "d1", [None, -90.0]) == [("d1", 0xB0, 74, 127)]
    assert stream_cues(fn, "d1", [None, 90.0]) == [("d1", 0xB0, 74, 127)]
    assert stream_cues(fn, "d1", [None, 0.0]) == [("d1", 0xB0, 74, 0)]


def test_stream_cues_rounding():
    fn = _stream(in_lo=0.0, in_hi=1.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 255.0),))
    # 0.5 * 255 = 127.5 -> rounds to 128 (banker's rounding on .5 ties to
    # even, but this exercises the general round-then-clamp path).
    assert stream_cues(fn, "d1", [None, 0.5]) == [("d1", 0xB0, 74, 128)]


def test_stream_cues_room_dev_passes_through():
    fn = _stream(outputs=(StreamOutput(ROOM, 0xB0, 74, 0.0, 127.0),))
    assert stream_cues(fn, "d1", [None, 0.0]) == [(ROOM, 0xB0, 74, 64)]


def test_stream_cues_missing_or_nonnumeric_arg_returns_empty():
    fn = _stream()
    assert stream_cues(fn, "d1", [None]) == []
    assert stream_cues(fn, "d1", [None, "not a number"]) == []
    assert stream_cues(fn, "d1", [None, True]) == []
    assert stream_cues(fn, "d1", []) == []


# --- collect_stream_cues: the edge-clamp rule ---------------------------
#
# A raw arg outside every declared domain on a lane is not simply dropped
# by GameServer.data's stream dispatch. Beyond the lane's whole domain
# hull, the function owning the nearest edge still applies (clamped to
# that edge); a value inside a GAP between two disjoint, non-touching
# domains is unaffected and still drops. See collect_stream_cues's
# docstring in control/functions.py.

def test_collect_stream_cues_clamps_below_the_lowest_domain_to_its_owner():
    lo = _stream("lo", in_lo=-90.0, in_hi=0.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 50.0),))
    hi = _stream("hi", in_lo=0.0, in_hi=90.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 200.0, 255.0),))
    # -200 is below every declared domain on this lane; "lo" owns the
    # lowest in_lo (-90), so it applies, clamped to -90 -- same cue as
    # stream_cues(lo, ..., -90) would produce on its own.
    assert collect_stream_cues([lo, hi], "d1", [None, -200.0]) == \
        stream_cues(lo, "d1", [None, -90.0])


def test_collect_stream_cues_clamps_above_the_highest_domain_to_its_owner():
    lo = _stream("lo", in_lo=-90.0, in_hi=0.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 50.0),))
    hi = _stream("hi", in_lo=0.0, in_hi=90.0,
                 outputs=(StreamOutput(TARGET, 0xB0, 74, 200.0, 255.0),))
    assert collect_stream_cues([lo, hi], "d1", [None, 999.0]) == \
        stream_cues(hi, "d1", [None, 90.0])


def test_collect_stream_cues_drops_a_value_in_a_gap_between_domains():
    """A gap BETWEEN two disjoint (non-touching) domains is not an edge:
    it stays dropped even though it sits inside the lane's overall hull."""
    low = _stream("low", in_lo=-90.0, in_hi=-10.0,
                  outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 50.0),))
    high = _stream("high", in_lo=10.0, in_hi=90.0,
                   outputs=(StreamOutput(TARGET, 0xB0, 74, 200.0, 255.0),))
    assert collect_stream_cues([low, high], "d1", [None, 0.0]) == []


def test_collect_stream_cues_edge_clamp_is_per_lane():
    """A second, independent lane with its own (disjoint) hull clamps
    against its OWN edges, not the other lane's."""
    tilt_a = _stream("tilt_a", in_lo=-90.0, in_hi=90.0,
                     outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),))
    tilt_b = _stream("tilt_b", in_lo=-45.0, in_hi=45.0,
                     outputs=(StreamOutput(TARGET, 0xB0, 1, 0.0, 127.0),))
    cues = collect_stream_cues([tilt_a, tilt_b], "d1", [None, 999.0])
    assert set(cues) == {("d1", 0xB0, 74, 127), ("d1", 0xB0, 1, 127)}


def test_collect_stream_cues_interior_value_is_unaffected_by_edge_clamp():
    fn = _stream(in_lo=-90.0, in_hi=90.0,
                outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),))
    assert collect_stream_cues([fn], "d1", [None, 0.0]) == [("d1", 0xB0, 74, 64)]


def test_stream_cues_multi_output():
    fn = _stream(outputs=(
        StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),
        StreamOutput(ROOM, 0xB0, 20, 127.0, 0.0),
    ))
    assert stream_cues(fn, "d1", [None, 90.0]) == [
        ("d1", 0xB0, 74, 127), (ROOM, 0xB0, 20, 0)]


def test_stream_touching_domain_boundary_gesture_verb_condition_validates():
    """A gesture-verb condition may name a verb that is only ever declared
    by STREAM functions, with no verb_handlers() entry at all."""
    table = FunctionTable(functions={
        "glow": _function(name="glow", condition=Condition(
            name="tilted", description="d",
            source=ConditionSource.GESTURE_VERB, verb="tilt")),
        "lo": _stream("lo", verb="tilt", in_lo=-90.0, in_hi=0.0),
        "hi": _stream("hi", verb="tilt", in_lo=0.0, in_hi=90.0),
    })
    validate_function_table(table, set())
