"""tools/export_models.py against tmp catalogs seeded with the committed
fixture pair: marker_fixture.glb is a real 12-LED source and
marker_fixture.mmbake.glb is a contract-valid bake of it."""
import hashlib
import json
from pathlib import Path

import pytest

from tools.export_models import ExportModelsError, export_models, main
from tools.model_bake_helpers import _read_glb_full, _write_glb

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "models"
SOURCE = (FIXTURES / "marker_fixture.glb").read_bytes()
BAKE = (FIXTURES / "marker_fixture.mmbake.glb").read_bytes()
TOML = '''
description = "a test instrument"
pixels = 12
capabilities = ["light.pixels"]
accepted_cues = ["midi"]
'''


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mm-tuneshroom"
    (repo / "assets" / "models").mkdir(parents=True)
    (repo / "pubspec.yaml").write_text("name: mm_shrooms_app\n")
    return repo


def _seed(root: Path, name: str = "glowcap", *, model: str = "glowcap",
          bake: bytes | None = BAKE) -> None:
    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "models" / f"{model}.glb").write_bytes(SOURCE)
    if bake is not None:
        (root / "models" / f"{model}.baked.glb").write_bytes(bake)
    (root / f"{name}.toml").write_text(TOML + f'\nmodel = "models/{model}.glb"\n')


def _edited_bake(edit) -> bytes:
    gltf, binary = _read_glb_full(BAKE, path="b.glb")
    edit(gltf["extras"]["mm_bake"])
    return _write_glb(gltf, binary)


def test_exports_the_bake_models_json_and_provenance(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    payload = export_models(root, repo, commit="abc123def456")
    sha = hashlib.sha256(BAKE).hexdigest()
    assert payload == {
        "_provenance": {"tool": "export_models/1", "commit": "abc123def456"},
        "models": {"glowcap": {"file": f"glowcap.{sha[:8]}.baked.glb",
                               "model_sha256": hashlib.sha256(SOURCE).hexdigest(),
                               "baked_sha256": sha}}}
    models_dir = repo / "assets" / "models"
    assert (models_dir / f"glowcap.{sha[:8]}.baked.glb").read_bytes() == BAKE
    written = (models_dir / "models.json").read_text()
    assert written == json.dumps(payload, indent=2, sort_keys=True) + "\n"


def test_two_instruments_sharing_one_model_get_one_entry_each(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, "cap_a", model="shared")
    (root / "cap_b.toml").write_text(TOML + '\nmodel = "models/shared.glb"\n')
    payload = export_models(root, repo, commit="c")
    assert sorted(payload["models"]) == ["cap_a", "cap_b"]
    assert payload["models"]["cap_a"]["baked_sha256"] == payload["models"]["cap_b"]["baked_sha256"]


def test_refuses_a_missing_bake(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=None)
    with pytest.raises(ExportModelsError, match="no bake"):
        export_models(root, repo, commit="c")


def test_refuses_a_stale_bake_and_writes_nothing(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=_edited_bake(lambda b: b.update(source_sha256="cd" * 32)))
    before = sorted(p.name for p in (repo / "assets" / "models").iterdir())
    with pytest.raises(ExportModelsError, match="stale"):
        export_models(root, repo, commit="c")
    assert sorted(p.name for p in (repo / "assets" / "models").iterdir()) == before
    assert not (repo / "test").exists()


def test_refuses_a_layout_that_differs_from_the_catalog(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=_edited_bake(lambda b: b["layout"][0].update(x_mm=41)))
    with pytest.raises(ExportModelsError, match="layout"):
        export_models(root, repo, commit="c")


def test_refuses_a_bake_that_breaks_the_contract(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=_edited_bake(lambda b: b.pop("uv")))
    with pytest.raises(ExportModelsError, match="missing required key"):
        export_models(root, repo, commit="c")


def test_instruments_without_a_model_export_an_empty_map(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    root.mkdir()
    (root / "plain.toml").write_text(TOML)
    assert export_models(root, repo, commit="c")["models"] == {}


def test_a_draft_that_declares_a_model_is_not_exported(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    (root / "drafts").mkdir()
    (root / "glowcap.toml").replace(root / "drafts" / "glowcap.toml")
    assert export_models(root, repo, commit="c")["models"] == {}


def test_prunes_stale_exported_bakes(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    stale = repo / "assets" / "models" / "old.deadbeef.baked.glb"
    stale.write_bytes(b"old")
    export_models(root, repo, commit="c")
    assert not stale.exists()


def test_refuses_an_unexpected_file_and_deletes_nothing(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    stray = repo / "assets" / "models" / "notes.txt"
    stray.write_text("hi")
    stale = repo / "assets" / "models" / "old.deadbeef.baked.glb"
    stale.write_bytes(b"old")
    with pytest.raises(ExportModelsError, match="notes.txt"):
        export_models(root, repo, commit="c")
    assert stray.exists() and stale.exists()


def test_refuses_a_target_that_is_not_mm_tuneshroom(tmp_path):
    root = tmp_path / "instruments"
    root.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "pubspec.yaml").write_text("name: something_else\n")
    with pytest.raises(ExportModelsError, match="not an mm-tuneshroom checkout"):
        export_models(root, other, commit="c")


def test_copies_the_mmbake_fixture_pair_byte_identically_and_not_the_source(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    root.mkdir()
    export_models(root, repo, commit="c")
    dest = repo / "test" / "fixtures" / "models"
    assert (dest / "marker_fixture.mmbake.glb").read_bytes() == BAKE
    assert (dest / "expected_layout.json").read_bytes() == (
        FIXTURES / "expected_layout.json").read_bytes()
    assert not (dest / "marker_fixture.glb").exists()


def test_main_without_a_target_prints_usage():
    with pytest.raises(SystemExit, match="usage"):
        main([])
