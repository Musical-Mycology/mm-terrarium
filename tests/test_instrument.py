"""tests/test_instrument.py"""
import pytest
from control.cues import TARGET
from control.functions import (
    Function, FunctionKind, FunctionTarget, GeneratorSpec, ScriptStep,
    StreamOutput, StreamSpec,
)
from control.instrument import (
    CAPABILITY_VOCABULARY, CUE_KINDS, CarriedInstrument, DEFAULTSHROOM,
    Instrument, InstrumentError, InstrumentRequirement, TUNESHROOM,
    fixture_ambient, satisfies, validate_instrument,
    validate_instrument_manifests,
)
from control.room_profile import RoomBlock, RoomFixture
from control.triggers import EventTrigger, StreamTrigger


def _generator_fn(name="glow", waveform="triangle", data1=74):
    return Function(
        name=name, description="ambient glow", kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=TARGET, status=0xB0, data1=data1,
                                waveform=waveform, period=12.0, lo=0, hi=127))


def test_tuneshroom_is_the_standard_carrier_instrument():
    assert TUNESHROOM.name == "tuneshroom"
    assert "gesture.tap" in TUNESHROOM.capabilities
    assert TUNESHROOM.accepted_cues == ("midi", "play", "solid", "mute")
    assert TUNESHROOM.light_manifest == {}
    validate_instrument(TUNESHROOM)  # the shipped standard always validates


def test_tuneshroom_declares_tap_and_shake_event_triggers():
    names = {t.name for t in TUNESHROOM.event_triggers}
    assert names == {"tap", "shake"}
    for trig in TUNESHROOM.event_triggers:
        assert trig.thresholds
        for key, value in trig.thresholds.items():
            assert isinstance(key, str)
            assert isinstance(value, (int, float))
    assert TUNESHROOM.stream_triggers == ()


def test_instrument_validates_its_event_triggers():
    bad = Instrument(
        name="glowstrip",
        event_triggers=(EventTrigger(name="bad name!", description="x",
                                      thresholds={"g": 1.0}),))
    with pytest.raises(InstrumentError, match="glowstrip"):
        validate_instrument(bad)


def test_instrument_validates_its_stream_triggers():
    bad = Instrument(
        name="glowstrip",
        stream_triggers=(StreamTrigger(name="s", description="x", verb="tilt",
                                        arg=0, transform="bogus", params={}),))
    with pytest.raises(InstrumentError, match="glowstrip"):
        validate_instrument(bad)


def test_instrument_generator_functions_validate():
    inst = Instrument(name="glowstrip", functions=(_generator_fn(),))
    validate_instrument(inst)  # must not raise


def test_instrument_generator_function_with_bad_waveform_is_located():
    bad = Function(
        name="glow", description="ambient glow", kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=TARGET, status=0xB0, data1=74,
                                waveform="square", period=12.0))
    inst = Instrument(name="glowstrip", functions=(bad,))
    with pytest.raises(InstrumentError, match="glowstrip") as exc:
        validate_instrument(inst)
    assert "square" in str(exc.value)


def test_instrument_stream_function_is_refused():
    stream = Function(
        name="tap", description="a tap function", kind=FunctionKind.STREAM,
        stream=StreamSpec(verb="tilt", arg=0, in_lo=-1.0, in_hi=1.0,
                          outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),)))
    inst = Instrument(name="glowstrip", functions=(stream,))
    with pytest.raises(InstrumentError, match="glowstrip") as exc:
        validate_instrument(inst)
    assert "only generator and scripted Functions" in str(exc.value)


def test_instrument_scripted_function_is_accepted():
    scripted = Function(
        name="tap", description="a tap function", kind=FunctionKind.SCRIPTED,
        script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),))
    inst = Instrument(name="glowstrip", functions=(scripted,),
                      accepted_cues=("midi",))
    validate_instrument(inst)


def test_unknown_capability_tag_is_a_located_error():
    inst = Instrument(name="bogus", capabilities=frozenset({"light.warp"}))
    with pytest.raises(InstrumentError, match="light.warp"):
        validate_instrument(inst)


def test_unknown_accepted_cue_kind_is_an_error():
    inst = Instrument(name="bogus", accepted_cues=("laser",))
    with pytest.raises(InstrumentError, match="laser"):
        validate_instrument(inst)


def test_satisfies_capability_superset():
    req = InstrumentRequirement(slot="player",
                                capabilities=frozenset({"gesture.tap"}))
    assert satisfies(TUNESHROOM, req) is None


def test_satisfies_names_the_missing_capability():
    req = InstrumentRequirement(slot="room",
                                capabilities=frozenset({"light.surface"}))
    reason = satisfies(TUNESHROOM, req)
    assert reason is not None and "light.surface" in reason


def test_satisfies_min_pixels_checks_supplied_pixel_count():
    req = InstrumentRequirement(slot="room",
                                capabilities=frozenset(), min_pixels=100)
    assert satisfies(TUNESHROOM, req, pixel_count=864) is None
    reason = satisfies(TUNESHROOM, req, pixel_count=60)
    assert reason is not None and "100" in reason


def test_satisfies_min_pixels_without_pixel_count_refuses():
    req = InstrumentRequirement(slot="room",
                                capabilities=frozenset(), min_pixels=1)
    assert satisfies(TUNESHROOM, req) is not None


def test_carried_instrument_pairs_instrument_and_dev():
    c = CarriedInstrument(instrument=TUNESHROOM, dev="ie1")
    assert c.dev == "ie1" and c.instrument is TUNESHROOM


def test_vocabulary_and_cue_kinds_are_the_documented_sets():
    assert CAPABILITY_VOCABULARY == frozenset({
        "light.pixels", "light.surface", "audio.flsyn", "audio.samples",
        "audio.mic", "gesture.tap", "gesture.tilt"})
    assert CUE_KINDS == ("midi", "play", "solid", "mute")


def test_instrument_ambient_light_manifest_is_validated():
    inst = Instrument(name="arr", light_manifest={
        "instruments": [{"target": "primary"}]})  # missing "instrument"
    with pytest.raises(InstrumentError, match="arr"):
        validate_instrument_manifests(inst)


def test_instrument_ambient_ugen_manifest_is_validated():
    inst = Instrument(name="arr", ugen_manifest={
        "instruments": [{"program": 89}]})  # missing "instrument"
    with pytest.raises(InstrumentError, match="arr"):
        validate_instrument_manifests(inst)


def test_fixture_ambient_returns_that_fixtures_own_manifests():
    light = {"instruments": [{"instrument": "aurora", "target": "primary"}]}
    ugen = {"instruments": [{"instrument": "flsyn", "program": 1}]}
    inst = Instrument(name="glow", capabilities=frozenset({"light.surface"}),
                      light_manifest=light, ugen_manifest=ugen)
    fixture = RoomFixture(name="f", color_order="GRB",
                          blocks=(RoomBlock("b", 0, 10),), zones=(), instrument=inst)
    out_light, out_ugen = fixture_ambient(fixture)
    assert (out_light, out_ugen) == (light, ugen)
    out_light["instruments"][0]["target"] = "ring"
    assert inst.light_manifest["instruments"][0]["target"] == "primary"


def test_fixture_ambient_is_empty_dicts_when_nothing_declared():
    inst = Instrument(name="bare", capabilities=frozenset({"light.surface"}))
    fixture = RoomFixture(name="f", color_order="GRB",
                          blocks=(RoomBlock("b", 0, 10),), zones=(), instrument=inst)
    assert fixture_ambient(fixture) == ({}, {})


def test_defaultshroom_is_a_valid_12_led_floor():
    validate_instrument(DEFAULTSHROOM)
    validate_instrument_manifests(DEFAULTSHROOM)
    assert DEFAULTSHROOM.name == "defaultshroom"
    assert DEFAULTSHROOM.pixels == 12
    assert "light.pixels" in DEFAULTSHROOM.capabilities
    assert {t.name for t in DEFAULTSHROOM.event_triggers} == {"tap", "shake"}
    assert DEFAULTSHROOM.light_manifest  # idle glow, not dark


def test_tuneshroom_declares_pixels_and_mic():
    assert TUNESHROOM.pixels == 12
    assert "audio.mic" in TUNESHROOM.capabilities


def test_light_pixels_requires_the_12_led_floor():
    bad = Instrument(name="tiny", capabilities=frozenset({"light.pixels"}),
                     pixels=11)
    with pytest.raises(InstrumentError) as exc:
        validate_instrument(bad)
    assert "pixels >= 12" in str(exc.value)
    validate_instrument(Instrument(
        name="ok", capabilities=frozenset({"light.pixels"}), pixels=12))
    validate_instrument(Instrument(  # non-light.pixels exempt
        name="surface", capabilities=frozenset({"light.surface"})))
