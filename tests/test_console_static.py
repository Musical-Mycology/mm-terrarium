"""The console's static assets. Split across files as of the Room panel
slice, still with NO build step: a venue box must never need npm."""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "console" / "static"


def _all_assets() -> str:
    return "\n".join(p.read_text() for p in sorted(STATIC.glob("*"))
                     if p.suffix in (".html", ".js", ".css"))


def test_no_external_asset_fetches_anywhere():
    """Global Constraint: self-contained. A venue box may have no internet."""
    for needle in ("http://", "https://", "//cdn", "src=\"//"):
        assert needle not in _all_assets(), f"external reference found: {needle}"


def test_the_expected_files_exist():
    names = {p.name for p in STATIC.glob("*")}
    assert {"index.html", "style.css", "console.js"} <= names


def test_index_references_its_split_assets():
    html = (STATIC / "index.html").read_text()
    assert "style.css" in html
    assert "console.js" in html


def test_the_lifecycle_controls_survived_the_split():
    assets = _all_assets()
    assert "new WebSocket" in assets
    assert "/ws" in assets
    assert "load_bit" in assets and "\"run\"" in assets and "abort" in assets
    assert "snapshot" in assets


def test_room_panel_renders_zones_instruments_and_live_values():
    room_js = (STATIC / "room.js").read_text()
    # zone view driven by the capability
    assert "capability" in room_js and "zones" in room_js
    # the frame relay
    assert "roomStrip" in room_js
    # instrument cards, both kinds, with live controller values
    assert "instruments" in room_js and "controllers" in room_js
    assert "lanes" in room_js
    # the empty state
    assert "No Room configured" in room_js


def test_room_panel_decodes_grb_not_rgb():
    """The wire is GRB (control/room_profile.py's color_order), so a naive
    rgb(c[0], c[1], c[2]) would render every zone the wrong colour."""
    assert "GRB" in (STATIC / "room.js").read_text()
