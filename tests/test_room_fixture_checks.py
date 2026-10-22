"""A Room fixture's instrument must agree with the fixture: its declared
pixel count, and its model's marker zones. Spec
docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md section 5.3."""

import pytest

from control.instrument import Instrument
from control.model_layout import PixelLayout
from control.terrarium_config import TerrariumConfigError, _parse_room


def _room_raw(count=4, zones=(("a", 0, 2), ("b", 2, 2))):
    return {
        "backends": ["devicelink"],
        "fixtures": [{
            "name": "f", "instrument": "strip",
            "blocks": [{"name": "all", "start": 0, "count": count}],
            "zones": [{"name": n, "start": s, "count": c} for n, s, c in zones],
        }],
    }


def _layout(zones):
    return tuple(PixelLayout(index=i, x_mm=0, y_mm=0, z_mm=i * 10,
                             size="medium", zone=z)
                 for i, z in enumerate(zones))


def _parse(instrument, **room_kwargs):
    return _parse_room("R", _room_raw(**room_kwargs), source="t.toml",
                       instruments={"strip": instrument})


def test_undeclared_pixels_and_no_model_loads():
    spec = _parse(Instrument(name="strip"))
    assert spec.profile.fixtures[0].pixel_count == 4


def test_matching_pixels_loads():
    _parse(Instrument(name="strip", pixels=4))


def test_mismatched_pixels_is_refused_and_names_both_counts():
    with pytest.raises(TerrariumConfigError) as exc:
        _parse(Instrument(name="strip", pixels=5))
    message = str(exc.value)
    assert "room 'R'" in message
    assert "fixture 'f'" in message
    assert "pixels = 5" in message
    assert "total 4" in message


def test_model_zones_matching_the_room_load():
    _parse(Instrument(name="strip", pixels=4,
                      layout=_layout(["a", "a", "b", "b"])))


def test_marker_with_no_zone_loads():
    _parse(Instrument(name="strip", pixels=4,
                      layout=_layout(["a", None, "b", None])))


def test_marker_in_the_wrong_zone_is_refused_and_named():
    with pytest.raises(TerrariumConfigError) as exc:
        _parse(Instrument(name="strip", pixels=4,
                          layout=_layout(["a", "a", "a", "b"])))
    message = str(exc.value)
    assert "LED_002" in message
    assert "'a'" in message
    assert "['b']" in message


def test_marker_on_a_pixel_in_no_room_zone_is_refused():
    with pytest.raises(TerrariumConfigError) as exc:
        _parse(Instrument(name="strip", pixels=4,
                          layout=_layout(["a", "a", "b", None])),
               zones=(("a", 0, 2),))
    message = str(exc.value)
    assert "LED_002" in message
    assert "[]" in message


def test_marker_count_must_match_the_fixture():
    with pytest.raises(TerrariumConfigError, match="3 markers"):
        _parse(Instrument(name="strip", layout=_layout(["a", "a", "b"])))


def test_room_may_subdivide_a_model_zone():
    # One model zone "progress" (px 0-7) refined into bottom/middle/top.
    _parse(Instrument(name="strip", pixels=8,
                      layout=_layout(["progress"] * 8)),
           count=8, zones=(("bottom", 0, 3), ("middle", 3, 2), ("top", 5, 3)))


def test_room_zone_straddling_two_model_zones_is_a_mismatch():
    # The room zone "mid" (px 2-5) spans progress (0-3) and responder (4-7).
    with pytest.raises(TerrariumConfigError) as exc:
        _parse(Instrument(name="strip", pixels=8,
                          layout=_layout(["progress"] * 4 + ["responder"] * 4)),
               count=8, zones=(("low", 0, 2), ("mid", 2, 4), ("high", 6, 2)))
    message = str(exc.value)
    assert "LED_002" in message
    assert "'progress'" in message
    assert "['mid']" in message
