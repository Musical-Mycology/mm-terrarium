"""Boot-time configuration for control.boot's load sequence. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.rooms import RoomType


@dataclass
class BootConfig:
    room_type: RoomType
    bit_name: str
    arco_soundfont: str | None = None
    # None = no array backend configured; "simulator" = Terrarium spawns
    # one (Spec 2's job); any other string = a real ArtNet/WLED host.
    array_backend: str | None = None
    arco_ready_timeout: float = 15.0
    room_setup_timeout: float = 30.0

    @property
    def array_backend_configured(self) -> bool:
        return self.array_backend is not None
