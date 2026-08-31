import hashlib
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


def test_verify_reports_manifest_entry_missing_from_archive(pkg):
    archive = bundle(pkg)
    tampered = archive.with_suffix(".missing.mmbit")
    with zipfile.ZipFile(archive) as zin, \
         zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.infolist():
            if item.filename == "assets/palette.json":
                continue
            zout.writestr(item, zin.read(item.filename))
    problems = verify(tampered)
    assert any("manifest entry missing from archive: assets/palette.json"
               in p for p in problems)


def test_verify_hard_problem_suppresses_api_warning(pkg):
    archive = bundle(pkg)
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("extra.py", "x = 1\n")
    problems = verify(archive, terrarium_api=2)
    assert any("extra.py" in p for p in problems)
    assert not any(p.startswith("warning:") for p in problems)


from control.bit_registry import BitRegistry
from tools.bundle_bit import install


def test_install_roundtrip_discovers_and_loads(pkg, tmp_path):
    archive = bundle(pkg)
    root = tmp_path / "installed"
    root.mkdir()
    dest = install(archive, root)
    assert dest == root / "GlowBit"
    assert (dest / "BUNDLE.json").is_file()
    reg = BitRegistry.scan((root,))
    assert "GlowBit" in reg.packages and not reg.errors
    cls = reg.bit_class("GlowBit")
    assert cls.__name__ == "GlowBit"


def test_install_refuses_existing_without_force(pkg, tmp_path):
    archive = bundle(pkg)
    root = tmp_path / "installed"
    (root / "GlowBit").mkdir(parents=True)
    with pytest.raises(SystemExit):
        install(archive, root)
    install(archive, root, force=True)          # replaces
    assert (root / "GlowBit" / "glow_bit.py").is_file()


def test_install_refuses_tampered(pkg, tmp_path):
    archive = bundle(pkg)
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("extra.py", "x = 1\n")
    root = tmp_path / "installed"
    root.mkdir()
    with pytest.raises(SystemExit):
        install(archive, root)


def test_install_refuses_zip_slip(pkg, tmp_path):
    archive = bundle(pkg)
    evil = archive.with_suffix(".evil.mmbit")
    with zipfile.ZipFile(archive) as zin, \
         zipfile.ZipFile(evil, "w") as zout:
        meta = json.loads(zin.read(BUNDLE_MANIFEST))
        payload = b"pwn"
        meta["files"]["../pwn.py"] = hashlib.sha256(payload).hexdigest()
        for item in zin.infolist():
            if item.filename != BUNDLE_MANIFEST:
                zout.writestr(item, zin.read(item.filename))
        zout.writestr("../pwn.py", payload)
        zout.writestr(BUNDLE_MANIFEST, json.dumps(meta))
    root = tmp_path / "installed"
    root.mkdir()
    with pytest.raises(SystemExit):
        install(evil, root)
    assert not (tmp_path / "pwn.py").exists()
