"""tests/test_instrument.py"""
from dataclasses import dataclass

import pytest
from control.cues import ROOM, TARGET
from control.functions import (
    Function, FunctionKind, FunctionTarget, GeneratorSpec, ScriptStep,
    StreamOutput, StreamSpec,
)
from control.instrument import (
    CAPABILITY_VOCABULARY, CUE_KINDS, CarriedInstrument, Instrument,
    InstrumentError, InstrumentRequirement, TUNESHROOM, ambient_manifests,
    satisfies, validate_instrument, validate_instrument_manifests,
)
from control.triggers import EventTrigger, StreamTrigger


def _generator_fn(name="glow", waveform="triangle", data1=74):
    return Function(
        name=name, description="ambient glow", kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=data1,
                                waveform=waveform, period=12.0, lo=0, hi=127))


@dataclass(frozen=True)
class _FakeFixture:
    """A bare stand-in for control.room_profile.RoomFixture: ambient_
    manifests() only ever reads `.instrument` off a fixture, so a full
    RoomFixture (blocks, zones, color_order) would just be noise here."""
    instrument: Instrument


@dataclass(frozen=True)
class _FakeProfile:
    fixtures: tuple


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
        generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
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
        "gesture.tap", "gesture.tilt"})
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


def test_empty_ambient_manifests_validate():
    validate_instrument_manifests(TUNESHROOM)


def test_ambient_manifests_concatenates_in_fixture_order():
    first = Instrument(name="first",
                       light_manifest={"instruments": [{"instrument": "a"}]},
                       ugen_manifest={"instruments": [{"instrument": "flsyn",
                                                        "program": 1}]})
    second = Instrument(name="second",
                        light_manifest={"instruments": [{"instrument": "b"}]},
                        ugen_manifest={"instruments": [{"instrument": "flsyn",
                                                         "program": 2}]})
    profile = _FakeProfile(fixtures=(_FakeFixture(first),
                                     _FakeFixture(second)))

    light, ugen = ambient_manifests(profile)

    assert light == {"instruments": [{"instrument": "a"},
                                     {"instrument": "b"}]}
    assert ugen == {"instruments": [{"instrument": "flsyn", "program": 1},
                                    {"instrument": "flsyn", "program": 2}]}


def test_ambient_manifests_empty_when_nothing_declares_anything():
    profile = _FakeProfile(fixtures=(_FakeFixture(TUNESHROOM),
                                     _FakeFixture(TUNESHROOM)))

    light, ugen = ambient_manifests(profile)

    assert light == {}
    assert ugen == {}


def test_ambient_manifests_deep_copies_entries():
    entry = {"instrument": "a", "params": {"key": 1}}
    inst = Instrument(name="only", light_manifest={"instruments": [entry]})
    profile = _FakeProfile(fixtures=(_FakeFixture(inst),))

    light, _ = ambient_manifests(profile)
    light["instruments"][0]["params"]["key"] = 999

    assert entry["params"]["key"] == 1   # the config-held dict is untouched
