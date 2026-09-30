"""Fake `docker` and `wslinfo` for driving docker/terrarium-dev offline.

The fake docker appends each argv as one JSON line to $FAKE_DOCKER_LOG.
Behaviour knobs (env):
  FAKE_DOCKER_INFO_FAIL=1       `docker info` exits 1
  FAKE_DOCKER_PS=<ids>          `docker ps -q ...` prints this
  FAKE_DOCKER_IMAGE_ID=<id>     `docker image inspect` prints this (default sha256:cur)
  FAKE_DOCKER_VOLUMES=a=l1,b=l2 volumes and their mm.terrarium-dev.image labels:
                                `volume ls -q` prints names; `volume inspect NAME`
                                prints the label, or exits 1 if NAME is absent
  FAKE_DOCKER_RM_FAIL=1         `volume rm` exits 1 without removing
  FAKE_DOCKER_PULL_FAIL=1       `pull` exits 1
Everything else exits 0. Volume removals are tracked in FAKE_DOCKER_RM_LOG.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

_DOCKER = '''\
import json, os, sys, pathlib
argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
vols = dict(p.split("=", 1) for p in
            os.environ.get("FAKE_DOCKER_VOLUMES", "").split(",") if p)
# Track volume removals in a state file
rm_log_path = pathlib.Path(os.environ.get("FAKE_DOCKER_RM_LOG", "/dev/null"))
try:
    removed = set(rm_log_path.read_text().splitlines())
except:
    removed = set()
if argv[:1] == ["info"]:
    sys.exit(1 if os.environ.get("FAKE_DOCKER_INFO_FAIL") else 0)
if argv[:1] == ["pull"]:
    sys.exit(1 if os.environ.get("FAKE_DOCKER_PULL_FAIL") else 0)
elif argv[:1] == ["ps"]:
    print(os.environ.get("FAKE_DOCKER_PS", ""))
elif argv[:2] == ["image", "inspect"]:
    print(os.environ.get("FAKE_DOCKER_IMAGE_ID", "sha256:cur"))
elif argv[:2] == ["volume", "ls"]:
    print("\\n".join(vols))
elif argv[:2] == ["volume", "inspect"]:
    name = argv[-1]
    if name not in vols or name in removed:
        sys.exit(1)
    print(vols[name])
elif argv[:2] == ["volume", "rm"]:
    if os.environ.get("FAKE_DOCKER_RM_FAIL"):
        sys.exit(1)
    name = argv[-1]
    if name in vols:
        with open(rm_log_path, "a") as f:
            f.write(name + "\\n")
'''


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_fake_docker(bindir: Path) -> None:
    _exe(bindir / "docker", f"#!{sys.executable}\n{_DOCKER}")


def install_fake_wslinfo(bindir: Path, mode: str) -> None:
    _exe(bindir / "wslinfo", f"#!/bin/sh\necho {mode}\n")
