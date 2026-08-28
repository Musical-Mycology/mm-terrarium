import json
import zipfile
from pathlib import Path

import pytest

from tools.bundle_bit import BUNDLE_MANIFEST, bundle, verify

MANIFEST = """
[bit]
name = "GlowBit"
version = "1.0.0"
entry = "glow_bit:GlowBit"
kind = "ambient"
requires_terrarium_api = 1

[assets]
palette = "assets/palette.json"
"""


@pytest.fixture
def pkg(tmp_path):
    pkg = tmp_path / "GlowBit"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "bit.toml").write_text(MANIFEST)
    (pkg / "glow_bit.py").write_text("class GlowBit:\n    pass\n")
    (pkg / "assets" / "palette.json").write_text('{"hue": 0.6}')
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "junk.pyc").write_bytes(b"x")
    (pkg / ".DS_Store").write_bytes(b"x")
    return pkg


def test_bundle_names_archive_and_excludes_junk(pkg):
    archive = bundle(pkg)
    assert archive.name == "GlowBit-1.0.0.mmbit"
    names = set(zipfile.ZipFile(archive).namelist())
    assert names == {"bit.toml", "glow_bit.py", "assets/palette.json",
                     BUNDLE_MANIFEST}


def test_bundle_manifest_carries_hashes_and_provenance(pkg):
    archive = bundle(pkg)
    meta = json.loads(zipfile.ZipFile(archive).read(BUNDLE_MANIFEST))
    assert meta["name"] == "GlowBit"
    assert meta["version"] == "1.0.0"
    assert meta["requires_terrarium_api"] == 1
    assert set(meta["files"]) == {"bit.toml", "glow_bit.py",
                                  "assets/palette.json"}
    assert all(len(h) == 64 for h in meta["files"].values())
    assert "created" in meta and "bundler" in meta


def test_bundle_refuses_broken_package(pkg):
    (pkg / "assets" / "palette.json").unlink()
    with pytest.raises(SystemExit):
        bundle(pkg)


def test_verify_clean(pkg):
    assert verify(bundle(pkg)) == []


def test_verify_refuses_tampered_member(pkg):
    archive = bundle(pkg)
    # rewrite one member with different bytes
    tampered = archive.with_suffix(".tampered.mmbit")
    with zipfile.ZipFile(archive) as zin, \
         zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "glow_bit.py":
                data = b"class GlowBit:\n    EVIL = True\n"
            zout.writestr(item, data)
    problems = verify(tampered)
    assert problems and any("glow_bit.py" in p for p in problems)


def test_verify_refuses_unlisted_member(pkg):
    archive = bundle(pkg)
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("extra.py", "x = 1\n")
    problems = verify(archive)
    assert any("extra.py" in p for p in problems)


def test_verify_warns_not_fails_on_api_mismatch(pkg):
    archive = bundle(pkg)
    problems = verify(archive, terrarium_api=2)
    assert problems and all(p.startswith("warning:") for p in problems)
