"""MinigameBit: a shared progress bar on every Tower, filled by taps.

Two kinds of device take part, told apart by their dev id:

- a player, ie<N> (PLAYER_DEV_RE), takes the `player` role. Every tap adds
  PROGRESS_PER_TAP (1%) to one progress figure shared by all players; a
  gesture of several taps (the tap's `count`) adds that many.
- a Tower, devtower<N> (TOWER_DEV_RE, the instrument instruments/
  devtower.toml), takes the `tower` role. It is a player device, not a
  Room fixture: it hellos like any device and gets its own 12 px player
  frame at start or walk-up, no Arm and no Room node. Its taps do nothing.
  Every Tower shows the same bar.

Progress runs 0..PROGRESS_MAX over TOWER_LIGHTS lights, pixels 0..3 of the
Tower's frame, bottom first, 25% a light, so 1% of progress is a
twenty-fifth of one light's level. At 100% the bar holds full and further taps do nothing;
reload the Bit to start over.

Each light is one zone of the tower role's own surface (Role.zones, p0..p3),
bound to an aurora held at level 0 whose level rides its own cc lane
(LIGHT_CC_BASE + k, a lane value is cc/127). A tap sends one cc to each
light it moved, on every Tower; a Tower that joined since the last tap gets
the whole bar instead, so a late Tower catches up on the next tap.

Any other device falls through to its own solo role, untouched by the game.
No scoring, and the Bit needs nothing from the Room.
"""

from __future__ import annotations

import re

from control.bit import Bit
from control.cues import LightCue
from control.functions import FunctionTable
from control.roles import Role, RoleClass, RoleTable

MINIGAME_PLAYER_NODE = "MINIGAME_PLAYER_NODE"
MINIGAME_TOWER_NODE = "MINIGAME_TOWER_NODE"

# The devices that play: ie1, ie2, ...
PLAYER_DEV_RE = re.compile(r"ie\d+")
# The devices that show the bar: devtower1, devtower2, ...
TOWER_DEV_RE = re.compile(r"devtower\d+")

PROGRESS_MAX = 100             # percent
PROGRESS_PER_TAP = 1           # percent
TOWER_LIGHTS = 4                # the Dev Tower's LEDs, pixels 0..3
LIGHTS = tuple(f"p{k}" for k in range(TOWER_LIGHTS))

# Light k's level rides cc LIGHT_CC_BASE + k, clear of cc:11, which
# Control's breath drives (off for the tower role anyway).
LIGHT_CC_BASE = 20
BAR_HUE = 0.33                 # green


def light_level(progress: int, k: int, lights: int = TOWER_LIGHTS) -> float:
    """Light k's level, 0..1, at `progress` percent on a bar of `lights`."""
    return min(max(progress * lights / PROGRESS_MAX - k, 0.0), 1.0)


def light_value(progress: int, k: int) -> int:
    """Light k's cc value, 0..127, at `progress` percent."""
    return round(light_level(progress, k) * 127)


def tap_count(args: list) -> int:
    """The taps in one /game/tap gesture: its `count`, at least 1."""
    try:
        return max(int(args[3]), 1)
    except (IndexError, TypeError, ValueError):
        return 1


def is_tower(dev: str) -> bool:
    return TOWER_DEV_RE.fullmatch(dev) is not None


def is_player(dev: str) -> bool:
    return PLAYER_DEV_RE.fullmatch(dev) is not None


class MinigameBit(Bit):
    version = "0.3"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.progress = 0
        self._players: set[str] = set()
        self._towers: set[str] = set()
        # Towers granted since the last tap: they get the whole bar on it.
        self._unsynced: set[str] = set()

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player",
            role_class=RoleClass.JAM,      # every ie<N> device, see admits()
            capacity=None,
            scored=False,
            uses=["tap"],
        )
        tower = Role(
            name="tower",
            role_class=RoleClass.JAM,      # every devtower<N> device
            capacity=None,
            scored=False,
            breath=False,                  # the bar is the whole light
            zones=tuple((zone, k, 1) for k, zone in enumerate(LIGHTS)),
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": zone,
                 "params": {"hue": BAR_HUE, "level": 0.0},
                 "lanes": [{"source": f"cc:{LIGHT_CC_BASE + k}",
                            "dest": "level"}]}
                for k, zone in enumerate(LIGHTS)
            ]},
        )
        return RoleTable(roles={"player": player, "tower": tower},
                         node_map={MINIGAME_PLAYER_NODE: ["player"],
                                   MINIGAME_TOWER_NODE: ["tower"]})

    def instrument_requirements(self) -> tuple:
        return ()

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def on_setup_enter(self) -> None:
        pass

    def on_run_start(self) -> None:
        pass

    def admits(self, dev: str, role_name: str) -> bool:
        if role_name == "player":
            return is_player(dev)
        if role_name == "tower":
            return is_tower(dev)
        return True

    def on_join(self, dev: str, role_name: str) -> None:
        self._players.discard(dev)
        self._towers.discard(dev)
        self._unsynced.discard(dev)
        if role_name == "player":
            self._players.add(dev)
        elif role_name == "tower":
            self._towers.add(dev)
            self._unsynced.add(dev)

    def update(self, dt: float) -> bool:
        return False   # this Bit never auto-completes; unload it from the Console

    def on_complete(self) -> None:
        pass

    def result(self) -> dict | None:
        return None

    def status(self) -> dict:
        return {"progress": self.progress, "players": sorted(self._players),
                "towers": sorted(self._towers)}

    def on_unload(self) -> None:
        pass

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """PROGRESS_PER_TAP more per tap in the gesture, shown on every
        Tower: the lights that moved, or the whole bar on a Tower that has
        not seen one yet. A tap from a non-player, or once the bar is full,
        does nothing."""
        if dev not in self._players or self.progress >= PROGRESS_MAX:
            return []
        before = self.progress
        self.progress = min(before + PROGRESS_PER_TAP * tap_count(args),
                            PROGRESS_MAX)
        out = []
        for tower in sorted(self._towers):
            full = tower in self._unsynced
            for k in range(TOWER_LIGHTS):
                value = light_value(self.progress, k)
                if full or value != light_value(before, k):
                    out.append(LightCue(tower, 0xB0, LIGHT_CC_BASE + k,
                                        value, when=at))
        self._unsynced.clear()
        return out
