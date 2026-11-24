"""The admin-device list last applied from a MycoQuest manifest (mm-fairyring
box contract spec 2026-11-23 section 4.10, mm-terrarium issue 100).

A file of the box's own beside terrarium.toml, never terrarium.toml itself:
that file's text hash is the box's config version, so only a person may
change it. Content: {"version": <int>, "devices": ["<gem_uid>", ...],
"tenant_slug": "<str>", "terrarium_name": "<str>"}: the identity the list was
applied for. A file whose identity is not the box's (a changed tenant_slug or
[terrarium] name, or a copied config directory) is treated as absent, so the
box neither keeps another identity's admins nor reports its version.
Written atomically (temp file in the same directory, then os.replace).

The file is read once, at construction, and cached; save() updates the
cache only after the replace succeeds. Pure stdlib.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Sequence

logger = logging.getLogger(__name__)

APPLIED_FILENAME = "admin_devices.applied.json"


def _valid(data) -> bool:
    if not isinstance(data, dict):
        return False
    version, devices = data.get("version"), data.get("devices")
    return (isinstance(version, int) and not isinstance(version, bool)
            and isinstance(devices, list)
            and all(isinstance(d, str) and d for d in devices))


class AdminDevicesStore:
    def __init__(self, path: str, *, tenant_slug: str | None = None,
                 terrarium_name: str | None = None) -> None:
        """tenant_slug and terrarium_name are the identity a file must have
        been applied for; None skips that comparison (a box with no
        [uplink] table knows no tenant)."""
        self.path = path
        self._expected = (("tenant_slug", tenant_slug),
                          ("terrarium_name", terrarium_name))
        self._version = 0
        self._devices: tuple[str, ...] | None = None
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return
        except OSError:
            logger.warning("could not read %s; treating it as absent",
                           self.path)
            return
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not _valid(data):
            logger.warning("%s is malformed; treating it as absent", self.path)
            return
        for key, expected in self._expected:
            if expected is not None and data.get(key) != expected:
                # Names the field only: neither the file's values nor its
                # device list reach the log.
                logger.warning("%s was applied for a different %s (or names "
                               "none); treating it as absent", self.path, key)
                return
        self._version = data["version"]
        self._devices = tuple(data["devices"])

    @property
    def version(self) -> int:
        """The applied manifest's version; 0 when there is no valid file."""
        return self._version

    @property
    def devices(self) -> tuple[str, ...] | None:
        """The applied active gem_uids; None when there is no valid file."""
        return self._devices

    def save(self, devices: Sequence[str], version: int, *, tenant_slug: str,
             terrarium_name: str) -> None:
        """Write atomically, with the identity the list is applied for, then
        update the cache. Raises OSError with the file and the cache
        unchanged and no temp file left behind."""
        devices = tuple(devices)
        text = json.dumps({"version": version, "devices": list(devices),
                           "tenant_slug": tenant_slug,
                           "terrarium_name": terrarium_name})
        directory = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(
            dir=directory, prefix=os.path.basename(self.path) + ".",
            suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._version = version
        self._devices = devices
