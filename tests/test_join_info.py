"""control/join_info.py: what a guest needs to reach a loaded Bit."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from control.join_info import (NATIVE_NOTE, build_join_info, guest_url,
                               tuneshroom_command)


def _info(**overrides):
    kwargs = dict(lan_ip="10.0.0.7", www_port=8788, arco_http_port=8080,
                  ensemble="arco", bit_name="MetronomeBit",
                  nodes=(("player", "METRO_PLAYER_NODE"),),
                  app_present=True, qr_svg=lambda url: f"<svg>{url}</svg>")
    kwargs.update(overrides)
    return build_join_info(**kwargs)


def test_guest_url_carries_node_o2ws_and_ensemble_and_no_dev():
    url = guest_url(lan_ip="10.0.0.7", www_port=8788, arco_http_port=8080,
                    ensemble="arco", node="METRO_PLAYER_NODE")
    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.netloc == "10.0.0.7:8788"
    assert parsed.path == "/app/"
    query = parse_qs(parsed.query)
    assert query == {"node": ["METRO_PLAYER_NODE"], "o2ws": ["10.0.0.7:8080"],
                     "ens": ["arco"]}
    assert "dev" not in query


def test_tuneshroom_command_is_the_chrome_sim_form_with_defines():
    cmd = tuneshroom_command(lan_ip="10.0.0.7", arco_http_port=8080,
                             ensemble="arco", node="METRO_PLAYER_NODE")
    assert cmd.startswith("flutter run -d chrome -t lib/sim_main.dart ")
    assert "--dart-define=NODE=METRO_PLAYER_NODE" in cmd
    assert "--dart-define=O2WS=10.0.0.7:8080" in cmd
    assert "--dart-define=ENS=arco" in cmd
    assert "DEV=" not in cmd
    assert "/Users/" not in cmd


def test_build_join_info_shape():
    info = _info()
    assert info["www_url"] == "http://10.0.0.7:8788/app/"
    assert info["o2ws_host"] == "10.0.0.7:8080"
    assert info["ensemble"] == "arco"
    assert info["app_present"] is True
    assert info["bit"] == "MetronomeBit"
    assert info["native_note"] == NATIVE_NOTE
    assert len(info["nodes"]) == 1
    row = info["nodes"][0]
    assert row["role"] == "player"
    assert row["node"] == "METRO_PLAYER_NODE"
    assert row["url"] == guest_url(lan_ip="10.0.0.7", www_port=8788,
                                   arco_http_port=8080, ensemble="arco",
                                   node="METRO_PLAYER_NODE")
    assert row["qr_svg"] == f"<svg>{row['url']}</svg>"
    expected = tuneshroom_command(lan_ip="10.0.0.7", arco_http_port=8080,
                                  ensemble="arco", node="METRO_PLAYER_NODE")
    assert row["tuneshroom_cmd"] == expected


def test_no_bit_yields_no_node_rows_but_still_the_guest_url():
    info = _info(bit_name=None, nodes=())
    assert info["bit"] is None
    assert info["nodes"] == []
    assert info["www_url"] == "http://10.0.0.7:8788/app/"


def test_app_present_is_threaded_verbatim():
    assert _info(app_present=False)["app_present"] is False


def test_qr_is_none_when_no_encoder_is_given():
    info = _info(qr_svg=None)
    assert info["nodes"][0]["qr_svg"] is None


def test_qr_is_none_when_the_encoder_raises():
    def boom(url):
        raise RuntimeError("no encoder")
    info = _info(qr_svg=boom)
    assert info["nodes"][0]["qr_svg"] is None
    assert info["nodes"][0]["url"]   # the row survives


def test_default_encoder_produces_inline_svg():
    pytest.importorskip("segno")
    info = build_join_info(lan_ip="10.0.0.7", www_port=8788,
                           arco_http_port=8080, ensemble="arco",
                           bit_name="TestBit",
                           nodes=(("player", "TEST_PLAYER_NODE"),),
                           app_present=True)
    svg = info["nodes"][0]["qr_svg"]
    assert svg.startswith("<svg")
    assert "<?xml" not in svg
    assert "http://" not in svg    # the URL is encoded, never inlined as text


from control.join_info import start_url


def test_start_url_carries_the_key():
    assert start_url(lan_ip="10.0.0.7", www_port=8788, key="a b") == \
        "http://10.0.0.7:8788/start?key=a+b"


def test_build_join_info_adds_a_start_row_only_for_an_admin_bit():
    assert _info()["start"] is None
    info = _info(start_key="metro-dev")
    assert info["start"]["url"] == "http://10.0.0.7:8788/start?key=metro-dev"
    assert info["start"]["key"] == "metro-dev"
    assert info["start"]["qr_svg"].startswith("<svg>")
    assert info["start"]["wire"] == '/game/start "ss" <dev> metro-dev'
