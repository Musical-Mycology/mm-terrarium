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


@pytest.mark.parametrize("timestamp", [
    float("nan"), float("inf"), float("-inf"),
])
def test_decode_rejects_a_non_finite_timestamp(timestamp):
    """Python's json module accepts the literal tokens NaN, Infinity, and
    -Infinity as valid floats by default, so a malformed device payload
    could otherwise pass decode()'s isinstance check here. Of the three,
    only NaN actually slips past GameServer._origin() (control/engine.py)
    unguarded -- +inf is caught by its too-far-future check and -inf by its
    <=0 fallback, both falling back to Control's clock. A NaN timestamp
    would produce a garbage presentation time that a TimedQueue would then
    hold forever, since `NaN <= now` is never true. Rejecting all three
    here, at the wire boundary, is still correct defense in depth."""
    with pytest.raises(ValueError):
        protocol.decode({"address": "/game/hello", "typespec": "",
                         "args": [], "timestamp": timestamp})


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


def test_leds_event_carries_a_display_time():
    event = protocol.leds_event("ie1", [0] * 36, when=42.5)
    assert event["timestamp"] == 42.5
    assert event["address"] == "/ie1/leds"


def test_leds_event_defaults_to_no_declared_time():
    """Zero keeps the pre-timing behavior: display on arrival."""
    event = protocol.leds_event("ie1", [0] * 36)
    assert event["timestamp"] == 0.0


def test_release_and_error_events():
    assert protocol.release_event("ie1")["address"] == "/ie1/release"
    err = protocol.error_event("ie1", "join", "no such node")
    assert err["args"] == ["join", "no such node"]


def test_play_event_shape():
    from devicelink.protocol import play_event
    msg = play_event("ie1", "click", "hard")
    assert msg["address"] == "/ie1/play"
    assert msg["typespec"] == "ss"
    assert msg["args"] == ["click", "hard"]


def test_play_event_params_default_empty():
    from devicelink.protocol import play_event
    assert play_event("ie3", "chime")["args"] == ["chime", ""]


def test_play_event_typespec_matches_arg_count():
    """The invariant decode() enforces on every inbound frame."""
    from devicelink.protocol import play_event
    msg = play_event("ie1", "click", "soft")
    assert len(msg["typespec"]) == len(msg["args"])


def test_room_event_wraps_blob_as_single_b_arg():
    blob = {"state": "IDLE", "bit": None, "version": None, "nodes": []}
    msg = protocol.room_event("ie1", blob)
    assert msg["address"] == "/ie1/room"
    assert msg["typespec"] == "b"
    assert msg["args"] == [blob]


def test_parse_canvas_url_accepts_http_and_https():
    assert protocol.parse_canvas_url(["ie1", "http://127.0.0.1:8123/"]) == \
        "http://127.0.0.1:8123/"
    assert protocol.parse_canvas_url(["ie1", "https://host/"]) == "https://host/"


def test_parse_canvas_url_refuses_javascript_scheme():
    with pytest.raises(ValueError):
        protocol.parse_canvas_url(["ie1", "javascript:alert(1)"])


def test_parse_canvas_url_refuses_data_scheme_relative_path_and_non_string():
    for bad in ["data:text/html,x", "/relative", "", None, 7]:
        with pytest.raises(ValueError):
            protocol.parse_canvas_url(["ie1", bad])


def test_parse_canvas_url_refuses_missing_url_arg():
    with pytest.raises(ValueError):
        protocol.parse_canvas_url(["ie1"])
