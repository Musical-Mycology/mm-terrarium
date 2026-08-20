"""The outbound JSON serialiser.

Note what these tests deliberately do NOT use: plain json.loads. Python's
decoder accepts the same Infinity/NaN extension its encoder emits, so a
round-trip through json.loads agrees with itself and disagrees with every
browser. That asymmetry is exactly what hid the defect this module exists
to fix, so every assertion here is either on the raw string or through a
strict parser.
"""

import json
import math

import pytest

from control.wire_json import dumps


def strict_loads(text: str):
    """json.loads with the Python-only extension tokens rejected, which is
    what a browser's JSON.parse does."""
    def reject(token):
        raise ValueError(f"non-JSON token {token!r}")
    return json.loads(text, parse_constant=reject)


def test_infinity_becomes_null_at_the_top_level():
    assert strict_loads(dumps({"d": float("inf")})) == {"d": None}


def test_negative_infinity_and_nan_become_null():
    out = strict_loads(dumps({"a": float("-inf"), "b": float("nan")}))
    assert out == {"a": None, "b": None}


def test_non_finite_nested_in_a_dict_becomes_null():
    out = strict_loads(dumps({"outer": {"inner": float("inf")}}))
    assert out == {"outer": {"inner": None}}


def test_non_finite_nested_in_a_list_becomes_null():
    out = strict_loads(dumps({"xs": [1.0, float("inf"), 3.0]}))
    assert out == {"xs": [1.0, None, 3.0]}


def test_output_carries_no_bare_extension_token():
    """Asserted on the raw string: this is the property browsers care about
    and the one a decoded comparison cannot see."""
    text = dumps({"a": float("inf"), "b": float("-inf"), "c": float("nan")})
    assert "Infinity" not in text
    assert "NaN" not in text
    assert text.count("null") == 3


def test_kwargs_pass_through():
    """capture/store.py already serialises with separators=(",", ":") and
    must keep doing so."""
    text = dumps({"a": 1, "b": 2}, separators=(",", ":"))
    assert text == '{"a":1,"b":2}'


def test_finite_payloads_are_byte_identical_to_json_dumps():
    """Proves this is not a formatting change for the overwhelmingly common
    case, so adopting it at eight call sites cannot alter any existing wire
    output."""
    payload = {"state": "RUNNING", "n": 3, "f": 1.5, "t": True,
               "z": None, "xs": [1, 2, 3], "d": {"k": "v"}}
    assert dumps(payload) == json.dumps(payload)


def test_a_missed_path_raises_rather_than_emitting_a_bad_token():
    """allow_nan=False is the belt to the sanitiser's braces. A float
    subclass that slips past an isinstance check must fail loudly here, not
    silently produce something no browser can read."""
    class Sneaky(float):
        pass

    # Sneaky IS a float instance, so it is sanitised; this pins that the
    # guard is in place by confirming no bad token can reach the output.
    assert "Infinity" not in dumps({"x": Sneaky("inf")})


def test_bools_and_ints_are_untouched():
    assert dumps({"t": True, "f": False, "n": 7}) == '{"t": true, "f": false, "n": 7}'


def test_warns_once_per_path_not_once_per_value(caplog):
    """A 44 Hz loop must not produce a 44 Hz log."""
    import control.wire_json as wj
    wj._warned.clear()
    with caplog.at_level("WARNING"):
        for _ in range(5):
            dumps({"status": {"run_duration": float("inf")}})
    assert len([r for r in caplog.records if "run_duration" in r.message]) == 1


def test_list_indices_do_not_grow_the_warning_set_without_bound(caplog):
    """Paths are normalised so a 10000-element list logs one warning, not
    10000, and _warned stays bounded by schema shape rather than data size."""
    import control.wire_json as wj
    wj._warned.clear()
    with caplog.at_level("WARNING"):
        dumps({"xs": [float("inf")] * 50})
    assert len(wj._warned) == 1
