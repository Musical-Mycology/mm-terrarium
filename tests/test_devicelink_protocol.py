"""DeviceLink wire protocol: envelope round-trip and event builders."""

import pytest

from devicelink import protocol


def test_envelope_round_trip():
    env = protocol.Envelope(timestamp=1.5, address="/game/join",
                            typespec="ss", args=["ie1", "NODE_A"])
    assert protocol.decode(protocol.encode(env)) == env


def test_decode_rejects_missing_address():
    with pytest.raises(ValueError):
        protocol.decode({"typespec": "ss", "args": []})


def test_decode_rejects_non_list_args():
    with pytest.raises(ValueError):
        protocol.decode({"address": "/game/join", "typespec": "ss",
                         "args": "nope"})


def test_decode_rejects_typespec_arity_mismatch():
    with pytest.raises(ValueError):
        protocol.decode({"address": "/game/join", "typespec": "ss",
                         "args": ["only-one"]})


def test_decode_defaults_missing_timestamp_to_zero():
    env = protocol.decode({"address": "/game/hello", "typespec": "",
                           "args": []})
    assert env.timestamp == 0.0


@pytest.mark.parametrize("address,expected", [
    ("/game/join", "join"),
    ("/game/hello", "hello"),
    ("/game/tilt", "tilt"),
    ("/ie1/role", None),
    ("/game", None),
    ("/game/", None),
    ("nonsense", None),
])
def test_parse_game_address(address, expected):
    assert protocol.parse_game_address(address) == expected


def test_role_event_carries_blob_verbatim():
    blob = {"light_manifest": {"instruments": []}, "role": "player"}
    msg = protocol.role_event("ie1", blob)
    assert msg["address"] == "/ie1/role"
    assert msg["typespec"] == "b"
    assert msg["args"][0] is blob


def test_deny_event_normalises_missing_hint():
    msg = protocol.deny_event("ie1", "player at capacity", None)
    assert msg["address"] == "/ie1/deny"
    assert msg["args"] == ["player at capacity", ""]


def test_leds_event_shape():
    msg = protocol.leds_event("ie1", list(range(36)))
    assert msg["address"] == "/ie1/leds"
    assert msg["typespec"] == "b"
    assert msg["args"] == [list(range(36))]


def test_release_and_error_events():
    assert protocol.release_event("ie1")["address"] == "/ie1/release"
    err = protocol.error_event("ie1", "join", "no such node")
    assert err["args"] == ["join", "no such node"]
