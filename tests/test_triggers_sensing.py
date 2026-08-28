"""tests/test_triggers_sensing.py

Sensing-side Triggers -- Spec 3 section 6; the acting side lives in
control/functions.py.
"""
import pytest

from control.triggers import (
    TRANSFORMS, EventTrigger, StreamTrigger, TriggerError,
    validate_event_trigger, validate_stream_trigger,
)


def _event(name="tap", thresholds=None):
    return EventTrigger(
        name=name, description="a tap gesture",
        thresholds=thresholds if thresholds is not None else {"peak_g": 2.0, "window_ms": 200})


def _stream(name="tilt_smooth", verb="tilt", arg=0, transform="smooth", params=None):
    return StreamTrigger(
        name=name, description="smoothed tilt", verb=verb, arg=arg,
        transform=transform, params=params if params is not None else {"alpha": 0.3})


def test_valid_event_trigger_validates():
    validate_event_trigger(_event(), "instrument 'tuneshroom'")  # must not raise


def test_event_trigger_name_must_match_vocabulary():
    bad = _event(name="tap gesture!")
    with pytest.raises(TriggerError, match="tap gesture!"):
        validate_event_trigger(bad, "instrument 'tuneshroom'")


def test_event_trigger_thresholds_must_be_numeric():
    bad = _event(thresholds={"peak_g": "high"})
    with pytest.raises(TriggerError, match="peak_g"):
        validate_event_trigger(bad, "instrument 'tuneshroom'")


def test_event_trigger_threshold_keys_must_be_str():
    bad = _event(thresholds={1: 2.0})
    with pytest.raises(TriggerError):
        validate_event_trigger(bad, "instrument 'tuneshroom'")


def test_valid_stream_trigger_validates():
    validate_stream_trigger(_stream(), "instrument 'tuneshroom'")  # must not raise


def test_stream_trigger_name_must_match_vocabulary():
    bad = _stream(name="tilt smooth!")
    with pytest.raises(TriggerError, match="tilt smooth!"):
        validate_stream_trigger(bad, "instrument 'tuneshroom'")


def test_stream_trigger_transform_must_be_known():
    bad = _stream(transform="bogus")
    with pytest.raises(TriggerError, match="bogus"):
        validate_stream_trigger(bad, "instrument 'tuneshroom'")
    assert "smooth" in TRANSFORMS


def test_smooth_transform_requires_alpha_in_unit_range():
    with pytest.raises(TriggerError, match="alpha"):
        validate_stream_trigger(_stream(params={"alpha": 0.0}), "instrument 'tuneshroom'")
    with pytest.raises(TriggerError, match="alpha"):
        validate_stream_trigger(_stream(params={"alpha": 1.5}), "instrument 'tuneshroom'")
    with pytest.raises(TriggerError, match="alpha"):
        validate_stream_trigger(_stream(params={}), "instrument 'tuneshroom'")
    validate_stream_trigger(_stream(params={"alpha": 1.0}), "instrument 'tuneshroom'")  # boundary ok
