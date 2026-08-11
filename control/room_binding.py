"""RoomBindingRegistry: Control-global record of which device is bound as
each RoomType's Room rendering backend. Survives Bit load/unload cycles the
same way DevicePool does. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 4.
"""

from __future__ import annotations

import json
import os
import time

from control.rooms import RoomType


class RoomBindingRegistry:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._bound: dict[RoomType, str] = {}
        self._armed_until: dict[RoomType, float] = {}

    def bound_device(self, room_type: RoomType) -> str | None:
        return self._bound.get(room_type)

    def bind(self, room_type: RoomType, dev: str) -> None:
        self._bound[room_type] = dev
        self._armed_until.pop(room_type, None)

    def release(self, room_type: RoomType) -> None:
        self._bound.pop(room_type, None)
        self._armed_until.pop(room_type, None)

    def arm(self, room_type: RoomType, window_seconds: float) -> None:
        """Open a registration window for window_seconds. Only a join
        against the Room node while armed may bind a device -- see design
        spec section 4."""
        self._armed_until[room_type] = self._clock() + window_seconds

    def disarm(self, room_type: RoomType) -> None:
        self._armed_until.pop(room_type, None)

    def is_armed(self, room_type: RoomType) -> bool:
        deadline = self._armed_until.get(room_type)
        return deadline is not None and self._clock() < deadline

    def save(self, path: str) -> None:
        """Persist just the bound device IDs -- not armed-window state,
        which never survives a restart. See design spec section 4."""
        data = {room_type.name: dev for room_type, dev in self._bound.items()}
        with open(path, "w") as f:
            json.dump(data, f)

    def load(self, path: str) -> None:
        """Replace in-memory bindings with whatever's on disk. A missing
        file is a no-op (fresh installation, nothing recorded yet)."""
        if not os.path.isfile(path):
            return
        with open(path) as f:
            data = json.load(f)
        self._bound = {RoomType[name]: dev for name, dev in data.items()}
