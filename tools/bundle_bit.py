"""Bundle, verify, and install Bit packages as single .mmbit archives.

Offline CLI, peer of tools/trace_stats.py; never imported by the runtime.
Format and rules: docs/superpowers/specs/
2026-08-28-external-and-bundled-bits-design.md section 5. sha256 gives
integrity, not authenticity; install bundles only from sources you trust.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import socket
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from control.api_version import TERRARIUM_API
from control.bit_config import ManifestError, parse_manifest

BUNDLE_MANIFEST = "BUNDLE.json"
EXCLUDE_DIRS = {"__pycache__", ".git"}


def _package_files(pkg_dir: Path) -> list[Path]:
    out = []
    for path in sorted(pkg_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pkg_dir)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name.startswith(".") or rel.suffix == ".pyc":
            continue
        out.append(rel)
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit(pkg_dir: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(pkg_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10)
    except OSError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def bundle(pkg_dir: Path, out: Path | None = None) -> Path:
    pkg_dir = pkg_dir.resolve()
    manifest_path = pkg_dir / "bit.toml"
    if not manifest_path.is_file():
        sys.exit(f"refusing to bundle: no bit.toml in {pkg_dir}")
    try:
        config = parse_manifest(manifest_path.read_text(),
                                 source=str(manifest_path))
    except ManifestError as exc:
        sys.exit(f"refusing to bundle a package discovery would refuse: {exc}")
    if config.identity.requires_terrarium_api is None:
        sys.exit("refusing to bundle: [bit.requires_terrarium_api] missing")
    for akey, rel in config.assets:
        target = pkg_dir / rel
        if not target.is_file():
            sys.exit(f"refusing to bundle: declared asset {akey!r} "
                     f"missing: {rel}")
        if not target.resolve().is_relative_to(pkg_dir):
            sys.exit(f"refusing to bundle: declared asset {akey!r} escapes "
                     f"the package directory: {rel}")

    name = config.identity.name
    version = config.identity.version
    default_name = f"{name}-{version}.mmbit" if version else f"{name}.mmbit"
    archive = (out or pkg_dir.parent / default_name).resolve()

    files = _package_files(pkg_dir)
    meta = {
        "name": name,
        "version": version,
        "requires_terrarium_api": config.identity.requires_terrarium_api,
        "created": datetime.now(timezone.utc).isoformat(),
        "bundler": f"{getpass.getuser()}@{socket.gethostname()}",
        "files": {str(rel): _sha256(pkg_dir / rel) for rel in files},
    }
    commit = _source_commit(pkg_dir)
    if commit:
        meta["source_commit"] = commit

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(pkg_dir / rel, str(rel))
        z.writestr(BUNDLE_MANIFEST, json.dumps(meta, indent=2, sort_keys=True))
    return archive


def verify(archive: Path, *, terrarium_api: int | None = None) -> list[str]:
    api = TERRARIUM_API if terrarium_api is None else terrarium_api
    problems: list[str] = []
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        if BUNDLE_MANIFEST not in names:
            return [f"no {BUNDLE_MANIFEST} in archive"]
        meta = json.loads(z.read(BUNDLE_MANIFEST))
        listed = dict(meta.get("files", {}))
        for member in sorted(names - {BUNDLE_MANIFEST}):
            if member.endswith("/"):
                continue
            if member not in listed:
                problems.append(f"member not in manifest: {member}")
                continue
            digest = hashlib.sha256(z.read(member)).hexdigest()
            if digest != listed.pop(member):
                problems.append(f"hash mismatch: {member}")
        for missing in sorted(listed):
            problems.append(f"manifest entry missing from archive: {missing}")
        declared = meta.get("requires_terrarium_api")
        if not problems and declared != api:
            problems.append(
                f"warning: bundle requires Terrarium API {declared}, "
                f"this checkout provides {api}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_bundle = sub.add_parser("bundle")
    p_bundle.add_argument("pkg_dir", type=Path)
    p_bundle.add_argument("-o", "--out", type=Path, default=None)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("archive", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "bundle":
        archive = bundle(args.pkg_dir, args.out)
        print(archive)
        return 0
    problems = verify(args.archive)
    for p in problems:
        print(p, file=sys.stderr)
    hard = [p for p in problems if not p.startswith("warning:")]
    if not hard:
        print(f"{args.archive}: OK")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
