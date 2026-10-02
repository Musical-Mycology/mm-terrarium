"""Catalog load-side: instruments/*.toml published, instruments/drafts/*.toml drafts."""
from pathlib import Path

import pytest

from control.catalog import clone_entry, load_catalog, publish_entry, save_draft
from control.terrarium_config import TerrariumConfigError

GOOD = '''
description = "a test instrument"
pixels = 12
capabilities = ["light.pixels"]
accepted_cues = ["midi"]
'''


def make_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "instruments"
    (root / "drafts").mkdir(parents=True)
    return root


def test_missing_root_is_an_empty_catalog(tmp_path):
    cat = load_catalog(tmp_path / "nope")
    assert cat.entries == {}
    assert cat.published == {}


def test_published_entry_parses_to_an_instrument(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    cat = load_catalog(root)
    entry = cat.get("published", "glowcap")
    assert entry.state == "published"
    assert entry.instrument.name == "glowcap"
    assert entry.error is None
    assert "glowcap" in cat.published


def test_published_parse_failure_raises_located(tmp_path):
    root = make_catalog(tmp_path)
    (root / "bad.toml").write_text('capabilities = ["no.such.capability"]')
    with pytest.raises(TerrariumConfigError) as exc:
        load_catalog(root)
    assert "bad.toml" in str(exc.value)


def test_draft_parse_failure_is_collected_not_raised(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "wip.toml").write_text('capabilities = ["no.such.capability"]')
    cat = load_catalog(root)
    entry = cat.get("draft", "wip")
    assert entry.state == "draft"
    assert entry.instrument is None
    assert "no.such.capability" in entry.error
    assert cat.published == {}


def test_draft_shadowing_published_keeps_both_reachable(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    (root / "drafts" / "glowcap.toml").write_text(GOOD)
    cat = load_catalog(root)
    # entries key is "<state>:<name>" precisely so a draft edit of a
    # published entry does not hide it.
    assert cat.entries["published:glowcap"].state == "published"
    assert cat.entries["draft:glowcap"].state == "draft"


def test_bad_stem_is_refused_even_as_draft(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "we ird.toml").write_text(GOOD)
    with pytest.raises(TerrariumConfigError):
        load_catalog(root)


from control.instrument import TUNESHROOM
from control.terrarium_config import load_terrarium_config, parse_terrarium_config


def test_shipped_tuneshroom_catalog_file_matches_the_code_constant():
    cat = load_catalog(Path("instruments"))
    assert cat.published["tuneshroom"] == TUNESHROOM


def test_shipped_config_still_resolves_fixture_instruments():
    config = load_terrarium_config("terrarium.toml")
    assert "venue_array" in config.instruments
    assert "dev_strip_main" in config.instruments
    fixture = config.rooms["TEST"].profile.fixtures[0]
    assert fixture.instrument.name == "dev_strip_main"


def test_extra_instrument_collision_with_inline_is_located(tmp_path):
    text = (
        'schema = 1\n[terrarium]\nname = "t"\n'
        '[instruments.dupe]\ncapabilities = []\n'
        '[rooms.T]\ndescription = "d"\nbackends = ["devicelink"]\n')
    from control.instrument import Instrument
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(
            text, source="test",
            extra_instruments={"dupe": Instrument(name="dupe")})
    assert "dupe" in str(exc.value)


def test_save_draft_roundtrips_and_reports_errors(tmp_path):
    root = make_catalog(tmp_path)
    refusal, errors = save_draft(root, "wip", 'capabilities = ["nope"]')
    assert refusal is None
    assert errors and "nope" in errors[0]
    assert (root / "drafts" / "wip.toml").read_text() == 'capabilities = ["nope"]'
    refusal, errors = save_draft(root, "wip", GOOD)
    assert refusal is None and errors == []


def test_save_draft_refuses_bad_name(tmp_path):
    root = make_catalog(tmp_path)
    refusal, _ = save_draft(root, "../evil", GOOD)
    assert refusal is not None
    assert not (tmp_path / "evil.toml").exists()


def test_clone_published_to_new_draft(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    assert clone_entry(root, "published", "glowcap", "glowcap2") is None
    assert (root / "drafts" / "glowcap2.toml").read_text() == GOOD
    # refuses to clobber an existing draft
    assert clone_entry(root, "published", "glowcap", "glowcap2") is not None


def test_publish_moves_a_valid_draft(tmp_path):
    root = make_catalog(tmp_path)
    save_draft(root, "wip", GOOD)
    assert publish_entry(root, "wip") is None
    assert (root / "wip.toml").exists()
    assert not (root / "drafts" / "wip.toml").exists()


def test_publish_refuses_an_invalid_draft_in_place(tmp_path):
    root = make_catalog(tmp_path)
    save_draft(root, "wip", 'capabilities = ["nope"]')
    reason = publish_entry(root, "wip")
    assert reason is not None and "nope" in reason
    assert (root / "drafts" / "wip.toml").exists()
    assert not (root / "wip.toml").exists()


def test_shipped_defaultshroom_catalog_file_matches_the_code_constant():
    from control.instrument import DEFAULTSHROOM
    cat = load_catalog(Path("instruments"))
    assert cat.published["defaultshroom"] == DEFAULTSHROOM


def test_testshroom_catalog_entry_resolves_with_audio_samples():
    from control.terrarium_config import load_terrarium_config
    cfg = load_terrarium_config("terrarium.toml")
    inst = cfg.instruments["testshroom"]
    assert inst.pixels == 12
    assert "audio.samples" in inst.capabilities
    assert "light.pixels" in inst.capabilities      # carriable (engine gate)
    assert "audio.mic" not in inst.capabilities


from control.instrument import Instrument
from control.catalog import Catalog, CatalogEntry, KINDS

STRIP = Instrument(name="strip", capabilities=frozenset({"light.surface"}),
                   accepted_cues=("midi", "play", "solid", "mute"))
INSTRUMENTS = {"strip": STRIP}

ROOM_TOML = '''description = "Two strips"
backends = ["devicelink"]

[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "strip"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 60
  [[fixtures.zones]]
  name = "left"
  start = 0
  count = 30
  [[fixtures.zones]]
  name = "right"
  start = 30
  count = 30

[[fixtures]]
name = "accent"
color_order = "GRB"
instrument = "strip"
  [[fixtures.blocks]]
  name = "accent"
  start = 0
  count = 30
'''


def test_kinds_are_instrument_and_room():
    assert KINDS == ("instrument", "room")


def test_room_catalog_requires_instruments(tmp_path):
    with pytest.raises(ValueError, match="instruments"):
        load_catalog(tmp_path, kind="room")


def test_published_room_parses_to_a_room_spec(tmp_path):
    (tmp_path / "LOFT.toml").write_text(ROOM_TOML)
    cat = load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)
    entry = cat.get("published", "LOFT")
    assert entry.kind == "room" and entry.instrument is None
    spec = entry.room
    assert spec.name == "LOFT"
    assert [f.name for f in spec.profile.fixtures] == ["main", "accent"]
    assert spec.profile.surface_id == "room_loft"
    assert cat.published == {"LOFT": spec}


def test_published_room_with_unknown_instrument_raises_located(tmp_path):
    (tmp_path / "LOFT.toml").write_text(ROOM_TOML.replace('"strip"', '"ghost"'))
    with pytest.raises(TerrariumConfigError, match="ghost"):
        load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)


def test_room_draft_errors_are_collected_not_raised(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "LOFT.toml").write_text("description = 1\n[[fixtures]]\nname = 'x'\n")
    cat = load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)
    entry = cat.get("draft", "LOFT")
    assert entry.room is None and entry.error


def test_room_save_draft_reports_room_errors(tmp_path):
    refusal, errors = save_draft(tmp_path, "LOFT", ROOM_TOML.replace('"strip"', '"ghost"'),
                                 kind="room", instruments=INSTRUMENTS)
    assert refusal is None
    assert any("ghost" in e for e in errors)
    refusal, errors = save_draft(tmp_path, "LOFT", ROOM_TOML,
                                 kind="room", instruments=INSTRUMENTS)
    assert (refusal, errors) == (None, [])


def test_room_publish_moves_a_valid_draft(tmp_path):
    save_draft(tmp_path, "LOFT", ROOM_TOML, kind="room", instruments=INSTRUMENTS)
    assert publish_entry(tmp_path, "LOFT", kind="room", instruments=INSTRUMENTS) is None
    assert (tmp_path / "LOFT.toml").is_file()
    assert not (tmp_path / "drafts" / "LOFT.toml").exists()


def test_room_publish_refuses_an_invalid_draft_in_place(tmp_path):
    save_draft(tmp_path, "LOFT", ROOM_TOML.replace('"strip"', '"ghost"'),
               kind="room", instruments=INSTRUMENTS)
    refusal = publish_entry(tmp_path, "LOFT", kind="room", instruments=INSTRUMENTS)
    assert refusal and "ghost" in refusal
    assert (tmp_path / "drafts" / "LOFT.toml").is_file()


def test_room_clone_names_the_kind_in_its_refusal(tmp_path):
    refusal = clone_entry(tmp_path, "published", "NOPE", "NEW", kind="room")
    assert refusal == "no published room named 'NOPE'"


def test_instrument_kind_is_the_default_and_unchanged(tmp_path):
    (tmp_path / "glow.toml").write_text('description = "g"\ncapabilities = ["light.surface"]\n'
                                        'accepted_cues = ["midi"]\n')
    cat = load_catalog(tmp_path)
    assert cat.kind == "instrument"
    entry = cat.get("published", "glow")
    assert entry.kind == "instrument" and entry.room is None
    assert entry.instrument.name == "glow"


def test_clone_entry_refuses_an_unknown_kind(tmp_path):
    # Every other catalog entry point checks the kind before it touches the
    # filesystem; clone_entry did not, so a typo'd kind quietly cloned an
    # instrument file into a rooms catalog (or vice versa).
    (tmp_path / "glowcap.toml").write_text(GOOD)
    with pytest.raises(ValueError, match="unknown catalog kind"):
        clone_entry(tmp_path, "published", "glowcap", "glowcap2", kind="widget")
    assert not (tmp_path / "drafts").exists()


SOLO = GOOD + '''
[[functions]]
name = "bloom"
kind = "scripted"
description = "a scripted test function"
script = [ { offset = 0.0, midi = [176, 74, 127] } ]

[solo]
  [solo.ambient.light]
  instruments = [ { instrument = "aurora", target = "primary" } ]
  [solo.bindings]
  tap = "bloom"
'''


def test_published_entry_parses_solo_table(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(SOLO)
    inst = load_catalog(root).published["glowcap"]
    assert inst.solo is not None
    assert inst.solo.bindings == {"tap": "bloom"}
    assert inst.solo.light_manifest["instruments"][0]["instrument"] == "aurora"


def test_published_entry_without_solo_has_none(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    assert load_catalog(root).published["glowcap"].solo is None


def test_shipped_tuneshroom_declares_solo():
    root = Path(__file__).resolve().parents[1] / "instruments"
    inst = load_catalog(root).published["tuneshroom"]
    assert inst.solo.bindings == {
        "tap": "play_aurora", "double_tap": "win", "shake": "fireworks_player"}
    assert inst.solo.light_manifest["instruments"] == [
        {"instrument": "aurora", "target": "primary"}]


from tests.glb_builder import GlbBuilder


def _twelve_led_glb() -> bytes:
    """A real, Blender-valid 12-LED document (8 ring, 4 stem) -- the
    same shape as the shared fixture, built directly with GlbBuilder so
    every test in this file that needs "an instrument with a model"
    builds one consistent, genuinely loadable file rather than a
    JSON-only stand-in."""
    builder = GlbBuilder()
    nodes = [
        {"name": "LEDs", "children": [1, 2]},
        {"name": "ring", "children": []},
        {"name": "stem", "children": []},
    ]
    for idx in range(12):
        mesh_idx = builder.add_box_mesh((0.0, 0.0, idx * 0.01), 0.002)
        marker_idx = len(nodes)
        nodes.append({"name": f"LED_{idx:03d}", "mesh": mesh_idx})
        nodes[1 if idx < 8 else 2]["children"].append(marker_idx)
    return builder.build(nodes)


def test_published_instrument_with_a_model_gets_layout_and_sha256(tmp_path):
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    cat = load_catalog(root)
    inst = cat.published["glowcap"]
    assert len(inst.layout) == 12
    assert inst.model_sha256 == __import__("hashlib").sha256(glb_bytes).hexdigest()


def test_published_instrument_with_missing_model_file_fails_to_load(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/nope.glb"\n')
    with pytest.raises(TerrariumConfigError, match="nope.glb"):
        load_catalog(root)


def test_draft_instrument_with_invalid_model_records_the_error_not_raises(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "glowcap.toml").write_text(GOOD + '\nmodel = "models/nope.glb"\n')
    cat = load_catalog(root)
    entry = cat.get("draft", "glowcap")
    assert entry.instrument is None
    assert "nope.glb" in entry.error


def test_instrument_without_a_model_has_no_layout_or_sha256(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    cat = load_catalog(root)
    inst = cat.published["glowcap"]
    assert inst.layout == ()
    assert inst.model_sha256 is None


def test_published_instrument_with_absolute_model_path_is_refused(tmp_path):
    root = make_catalog(tmp_path)
    outside = tmp_path / "outside.glb"
    outside.write_bytes(b"not a real glb but never read")
    (root / "glowcap.toml").write_text(GOOD + f'\nmodel = "{outside}"\n')
    with pytest.raises(TerrariumConfigError) as exc:
        load_catalog(root)
    message = str(exc.value)
    assert "relative" in message or "inside" in message
    assert "not a GLB" not in message  # confinement must be checked before any read


def test_published_instrument_with_escaping_model_path_is_refused(tmp_path):
    root = make_catalog(tmp_path)
    outside = tmp_path / "outside.glb"
    outside.write_bytes(b"not a real glb but never read")
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "../outside.glb"\n')
    with pytest.raises(TerrariumConfigError) as exc:
        load_catalog(root)
    message = str(exc.value)
    assert "relative" in message or "inside" in message
    assert "not a GLB" not in message


def test_draft_instrument_model_resolves_against_catalog_root_not_drafts(tmp_path):
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    (root / "drafts" / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    cat = load_catalog(root)
    entry = cat.get("draft", "glowcap")
    assert entry.error is None
    assert len(entry.instrument.layout) == 12


def test_published_instrument_with_model_but_zero_pixels_fails(tmp_path):
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    text = GOOD.replace("pixels = 12", "pixels = 0") + '\nmodel = "models/glowcap.glb"\n'
    (root / "glowcap.toml").write_text(text)
    with pytest.raises(TerrariumConfigError):
        load_catalog(root)


def test_stale_bake_warns_but_does_not_fail_load(tmp_path, caplog):
    import logging
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    stale_gltf = {"nodes": [], "extras": {"mm_bake": {"source_sha256": "not-the-real-hash"}}}
    from tests.glb_builder import build_glb
    (root / "models" / "glowcap.baked.glb").write_bytes(build_glb(stale_gltf))
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    with caplog.at_level(logging.WARNING):
        cat = load_catalog(root)
    assert cat.published["glowcap"] is not None  # load still succeeds
    assert any("stale" in r.message for r in caplog.records)


def test_fresh_bake_does_not_warn(tmp_path, caplog):
    import hashlib
    import logging
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    fresh_gltf = {"nodes": [], "extras": {"mm_bake": {
        "source_sha256": hashlib.sha256(glb_bytes).hexdigest()}}}
    from tests.glb_builder import build_glb
    (root / "models" / "glowcap.baked.glb").write_bytes(build_glb(fresh_gltf))
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    with caplog.at_level(logging.WARNING):
        load_catalog(root)
    assert not any("stale" in r.message for r in caplog.records)


def test_no_bake_file_does_not_warn(tmp_path, caplog):
    import logging
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    (root / "models" / "glowcap.glb").write_bytes(_twelve_led_glb())
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    with caplog.at_level(logging.WARNING):
        load_catalog(root)
    assert not any("stale" in r.message for r in caplog.records)


def test_bake_with_non_dict_extras_warns_but_does_not_fail_load(tmp_path, caplog):
    import logging
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    garbled_gltf = {"nodes": [], "extras": "not-a-dict"}
    from tests.glb_builder import build_glb
    (root / "models" / "glowcap.baked.glb").write_bytes(build_glb(garbled_gltf))
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    with caplog.at_level(logging.WARNING):
        cat = load_catalog(root)
    assert cat.published["glowcap"] is not None  # load still succeeds
    assert any("glowcap" in r.message for r in caplog.records)


def test_bake_with_non_dict_mm_bake_warns_but_does_not_fail_load(tmp_path, caplog):
    import logging
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    garbled_gltf = {"nodes": [], "extras": {"mm_bake": ["not", "a", "dict"]}}
    from tests.glb_builder import build_glb
    (root / "models" / "glowcap.baked.glb").write_bytes(build_glb(garbled_gltf))
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    with caplog.at_level(logging.WARNING):
        cat = load_catalog(root)
    assert cat.published["glowcap"] is not None  # load still succeeds
    assert any("glowcap" in r.message for r in caplog.records)


def test_stale_check_with_list_json_root_warns_but_does_not_fail_load(tmp_path, caplog):
    """IMPORTANT 2: a .baked.glb whose JSON root is a list must not raise."""
    import logging
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    from tests.glb_builder import build_glb
    (root / "models" / "glowcap.baked.glb").write_bytes(build_glb([1, 2, 3]))
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    with caplog.at_level(logging.WARNING):
        cat = load_catalog(root)
    assert cat.published["glowcap"] is not None  # load still succeeds
    assert any("glowcap" in r.message for r in caplog.records)


def test_stale_check_warns_when_mm_bake_missing_source_sha256(tmp_path, caplog):
    """IMPORTANT 2: mm_bake present as a dict but with no source_sha256 key
    must warn, not silently pass and not raise."""
    import logging
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    garbled_gltf = {"nodes": [], "extras": {"mm_bake": {"maps": []}}}
    from tests.glb_builder import build_glb
    (root / "models" / "glowcap.baked.glb").write_bytes(build_glb(garbled_gltf))
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')
    with caplog.at_level(logging.WARNING):
        cat = load_catalog(root)
    assert cat.published["glowcap"] is not None  # load still succeeds
    assert any("glowcap" in r.message and "source_sha256" in r.message
               for r in caplog.records)


def test_draft_model_with_nul_byte_records_error_not_raises(tmp_path):
    """CRITICAL 1a: draft text is untrusted; a `model` path containing a NUL
    byte must never escape load_catalog as an uncaught ValueError."""
    root = make_catalog(tmp_path)
    text = GOOD + '\nmodel = "models/x\\u0000.glb"\n'
    error, draft_errors = save_draft(root, "evil", text)
    assert error is None
    assert draft_errors  # refused, recorded as a draft error
    cat = load_catalog(root)  # must not raise
    entry = cat.get("draft", "evil")
    assert entry.instrument is None
    assert entry.error


def test_draft_model_pointing_at_a_crafted_toml_disguised_glb_is_refused(tmp_path):
    """CRITICAL 1b: a second draft can name any file under the catalog root
    as its `model`, including another draft's .toml (which save_draft will
    happily write as valid UTF-8 text, e.g. bytes of a crafted GLB). This
    must be refused before parse_model_layout ever sees it, and must never
    raise out of load_catalog."""
    import struct
    root = make_catalog(tmp_path)
    body = __import__("json").dumps({"nodes": [{"name": "LEDs", "children": [9]}]}).encode()
    body += b" " * (100 - len(body))
    glb = (struct.pack("<III", 0x46546C67, 2, 120)
           + struct.pack("<II", 100, 0x4E4F534A) + body)
    text = glb.decode("utf-8")
    error, _ = save_draft(root, "evil", text)
    assert error is None
    error, draft_errors = save_draft(root, "wip", GOOD + '\nmodel = "drafts/evil.toml"\n')
    assert error is None
    assert draft_errors  # refused as a draft error, not raised
    cat = load_catalog(root)  # must not raise (IndexError from a crafted marker index)
    entry = cat.get("draft", "wip")
    assert entry.instrument is None
    assert entry.error


def test_published_model_without_glb_suffix_is_refused(tmp_path):
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    (root / "models" / "glowcap.txt").write_bytes(b"not a glb")
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.txt"\n')
    with pytest.raises(TerrariumConfigError, match=r"\.glb"):
        load_catalog(root)


def test_published_model_referencing_a_drafts_path_is_refused(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "sneaky.glb").write_bytes(_twelve_led_glb())
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "drafts/sneaky.glb"\n')
    with pytest.raises(TerrariumConfigError, match="drafts"):
        load_catalog(root)


def test_unreadable_bake_warns_but_does_not_fail_load(tmp_path, caplog, monkeypatch):
    import logging
    from pathlib import Path as _Path
    root = make_catalog(tmp_path)
    (root / "models").mkdir()
    glb_bytes = _twelve_led_glb()
    (root / "models" / "glowcap.glb").write_bytes(glb_bytes)
    baked_path = root / "models" / "glowcap.baked.glb"
    baked_path.write_bytes(b"irrelevant, read_bytes is patched to fail")
    (root / "glowcap.toml").write_text(GOOD + '\nmodel = "models/glowcap.glb"\n')

    real_read_bytes = _Path.read_bytes

    def fake_read_bytes(self):
        if self.name == "glowcap.baked.glb":
            raise PermissionError(f"denied: {self}")
        return real_read_bytes(self)

    monkeypatch.setattr(_Path, "read_bytes", fake_read_bytes)
    with caplog.at_level(logging.WARNING):
        cat = load_catalog(root)
    assert cat.published["glowcap"] is not None  # load still succeeds
    assert any("glowcap" in r.message for r in caplog.records)


# --- instrument publish checks the published rooms that bind it ---

_PX_ROOM = '''description = "r"
backends = ["devicelink"]
[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "glow"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 14
'''


def _glow(pixels: int) -> str:
    return (f'pixels = {pixels}\ncapabilities = ["light.pixels"]\n'
            'accepted_cues = ["midi"]\n')


def _roomed_catalog(tmp_path, room_text=_PX_ROOM):
    root = make_catalog(tmp_path)
    rooms = tmp_path / "rooms"
    rooms.mkdir()
    (rooms / "TOWER.toml").write_text(room_text)
    return root, rooms


def test_instrument_publish_refused_when_a_published_room_would_break(tmp_path):
    root, rooms = _roomed_catalog(tmp_path)
    save_draft(root, "glow", _glow(15))
    reason = publish_entry(root, "glow", rooms_root=rooms, instruments={})
    assert reason is not None
    assert "TOWER" in reason and "pixels = 15" in reason
    assert (root / "drafts" / "glow.toml").exists()
    assert not (root / "glow.toml").exists()


def test_instrument_publish_succeeds_when_the_room_still_parses(tmp_path):
    root, rooms = _roomed_catalog(tmp_path)
    save_draft(root, "glow", _glow(14))
    assert publish_entry(root, "glow", rooms_root=rooms, instruments={}) is None
    assert (root / "glow.toml").exists()


def test_instrument_publish_without_rooms_root_is_unchanged(tmp_path):
    root, _rooms = _roomed_catalog(tmp_path)
    save_draft(root, "glow", _glow(15))
    assert publish_entry(root, "glow") is None
    assert (root / "glow.toml").exists()


def test_a_room_that_does_not_bind_the_instrument_does_not_block_it(tmp_path):
    root, rooms = _roomed_catalog(tmp_path)
    save_draft(root, "other", _glow(15))
    assert publish_entry(root, "other", rooms_root=rooms, instruments={}) is None
    assert (root / "other.toml").exists()
