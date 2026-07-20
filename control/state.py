"""Lifecycle states for the Control+GameServer's Bit-launching engine.

See docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md
section 3 for the full state diagram and transition rules.
"""

from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    LOADING = auto()
    LOADED = auto()
    SETUP = auto()
    RUNNING = auto()
    COMPLETING = auto()
    UNLOADING = auto()
