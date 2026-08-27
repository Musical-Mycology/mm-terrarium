"""tests/test_instrument.py"""
import pytest
from control.instrument import (
    CAPABILITY_VOCABULARY, CUE_KINDS, CarriedInstrument, Instrument,
    InstrumentError, InstrumentRequirement, TUNESHROOM, satisfies,
    validate_instrument, validate_instrument_manifests,
)


def test_tuneshroom_is_the_standard_carrier_instrument():
    assert TUNESHROOM.name == "tuneshroom"
    assert "gesture.tap" in TUNESHROOM.capabilities
    assert TUNESHROOM.accepted_triggers == ("midi", "play", "solid", "mute")
    assert TUNESHROOM.light_manifest == {}
    validate_instrument(TUNESHROOM)  # the shipped standard always validates


def test_unknown_capability_tag_is_a_located_error():
    inst = Instrument(name="bogus", capabilities=frozenset({"light.warp"}))
    with pytest.raises(InstrumentError, match="light.warp"):
        validate_instrument(inst)


def test_unknown_accepted_trigger_kind_is_an_error():
    inst = Instrument(name="bogus", accepted_triggers=("laser",))
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
