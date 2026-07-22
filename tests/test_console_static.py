from pathlib import Path


def test_index_html_is_self_contained():
    html = (Path(__file__).resolve().parent.parent
            / "console" / "static" / "index.html").read_text()
    # no external asset fetches (Global Constraint)
    for needle in ("http://", "https://", "//cdn", "src=\"//"):
        assert needle not in html, f"external reference found: {needle}"
    # the pieces the panel is built from
    assert "new WebSocket" in html
    assert "/ws" in html
    assert "load_bit" in html and "\"run\"" in html and "abort" in html
    assert "snapshot" in html
