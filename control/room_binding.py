"""RoomBindingRegistry: Control-global record of which device is bound as
each RoomType's Room rendering backend, per fixture. Survives Bit load/unload
cycles the same way DevicePool does. See
docs/superpowers/specs/2026-08-18-n-fixture-room-design.md section 4.
"""

from __future__ import annotations

import json
import logging
import os
import time

from control.rooms import RoomType

logger = logging.getLogger(__name__)


class RoomBindingRegistry:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._bound: dict[RoomType, dict[str, str]] = {}
        self._armed_fixture: dict[RoomType, str] = {}
        self._armed_until: dict[RoomType, float] = {}

    def bound_device(self, room_type: RoomType, fixture: str) -> str | None:
        return self._bound.get(room_type, {}).get(fixture)

    def bind(self, room_type: RoomType, fixture: str, dev: str) -> None:
        self._bound.setdefault(room_type, {})[fixture] = dev
        if self._armed_fixture.get(room_type) == fixture:
            self._armed_fixture.pop(room_type, None)
            self._armed_until.pop(room_type, None)

    def release(self, room_type: RoomType, fixture: str | None = None) -> None:
        """Release one fixture, or every fixture of this room_type when
        fixture is None. Only clears the armed window if it belonged to the
        fixture being released (or fixture is None, releasing everything)."""
        if fixture is None:
            self._bound.pop(room_type, None)
            self._armed_fixture.pop(room_type, None)
            self._armed_until.pop(room_type, None)
            return
        self._bound.get(room_type, {}).pop(fixture, None)
        if self._armed_fixture.get(room_type) == fixture:
            self._armed_fixture.pop(room_type, None)
            self._armed_until.pop(room_type, None)

    def arm(self, room_type: RoomType, fixture: str, window_seconds: float) -> None:
        """Open a registration window for window_seconds, naming which
        fixture the next join against the Room node binds. One fixture armed
        at a time per RoomType; arming a second replaces the first -- see
        design spec section 4."""
        self._armed_fixture[room_type] = fixture
        self._armed_until[room_type] = self._clock() + window_seconds

    def disarm(self, room_type: RoomType) -> None:
        self._armed_fixture.pop(room_type, None)
        self._armed_until.pop(room_type, None)

    def is_armed(self, room_type: RoomType) -> bool:
        deadline = self._armed_until.get(room_type)
        return deadline is not None and self._clock() < deadline

    def armed_fixture(self, room_type: RoomType) -> str | None:
        """Which fixture the next Room-node join binds, or None if nothing
        is currently armed (including an expired window)."""
        if not self.is_armed(room_type):
            return None
        return self._armed_fixture.get(room_type)

    def save(self, path: str) -> None:
        """Persist just the bound device IDs, per fixture -- not armed-window
        state, which never survives a restart. See design spec section 4."""
        data = {room_type.name: dict(fixtures)
               for room_type, fixtures in self._bound.items()}
        with open(path, "w") as f:
            json.dump(data, f)

    def load(self, path: str) -> None:
        """Replace in-memory bindings with whatever's on disk. A missing
        file is a no-op (fresh installation, nothing recorded yet). A file in
        the pre-N-fixture flat format (room_type -> single dev string) is
        ignored with a warning rather than guessed at -- see design spec
        section 4."""
        if not os.path.isfile(path):
            return
        with open(path) as f:
            data = json.load(f)
        loaded: dict[RoomType, dict[str, str]] = {}
        for name, value in data.items():
            if not isinstance(value, dict):
                logger.warning(
                    "ignoring room binding file %r: old flat format "
                    "(room_type -> dev string) is no longer supported, "
                    "fixture-keyed format expected", path)
                return
            loaded[RoomType[name]] = dict(value)
        self._bound = loaded
