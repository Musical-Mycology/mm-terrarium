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
