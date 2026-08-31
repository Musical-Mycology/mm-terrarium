"""The console's static assets. NO build step: a venue box never needs npm.
As of the 2026-08-25 redesign the front end is ES modules with one entry."""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "console" / "static"

MODULES = {"wire.js", "shell.js", "bit.js", "surface.js", "functions.js",
           "rail.js"}


def _text_assets() -> str:
    return "\n".join(p.read_text() for p in sorted(STATIC.glob("*"))
                     if p.suffix in (".html", ".js", ".css"))


def test_no_external_asset_fetches_anywhere():
    for needle in ("http://", "https://", "//cdn", "src=\"//",
                   "fonts.googleapis", "@import url"):
        assert needle not in _text_assets(), f"external reference: {needle}"


def test_the_expected_files_exist():
    names = {p.name for p in STATIC.glob("*") if p.is_file()}
    assert {"index.html", "terrarium.css"} | MODULES <= names
    # the old plain-script front end is gone
    assert not {"style.css", "console.js", "room.js"} & names


def test_fonts_are_self_hosted_and_servable():
    fonts = {p.name for p in (STATIC / "fonts").glob("*")}
    assert any("LondrinaSolid" in f for f in fonts)
    assert any("AtkinsonHyperlegible" in f for f in fonts)
    assert any("JetBrainsMono" in f for f in fonts)
    from console.server import _CONTENT_TYPES
    for f in fonts:
        assert Path(f).suffix in _CONTENT_TYPES, f"{f} not servable"


def test_index_loads_exactly_one_module_entry():
    html = (STATIC / "index.html").read_text()
    import re
    tags = re.findall(r"<script[^>]*>", html)
    assert len(tags) == 1
    assert 'type="module"' in tags[0] and 'src="shell.js"' in tags[0]


def test_every_js_file_is_an_es_module():
    """Module isolation by construction: no shared global scope exists, so
    the 2026-08-19 buildCard collision class is structurally impossible."""
    for name in MODULES:
        text = (STATIC / name).read_text()
        assert ("export " in text) or ("import " in text), f"{name} is not a module"


def test_css_guards_the_hidden_attribute():
    """Author display rules (.maincol flex, .chip inline-flex) override the
    UA stylesheet's [hidden] { display: none }, which made view switching a
    silent no-op: every view rendered stacked. The guard must outrank them."""
    css = (STATIC / "terrarium.css").read_text()
    assert "[hidden]" in css
    assert "display: none !important" in css


def test_css_defines_the_status_palette_and_faces():
    css = (STATIC / "terrarium.css").read_text()
    for token in ("#7a9e6e", "#d96680", "#c07850",   # sage/rose/terracotta
                  "Londrina Solid", "Atkinson Hyperlegible", "JetBrains Mono"):
        assert token in css
