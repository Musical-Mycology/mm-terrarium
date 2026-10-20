"""MinigameBit: a shared progress bar on the Tower, filled by taps.

Every tap from an ie<N> device adds PROGRESS_PER_TAP (1%) to one progress
figure shared by all players; a gesture of several taps (the tap's `count`)
adds that many. Progress runs 0..PROGRESS_MAX whatever the bar's length:
the Tower fixture shows it on however many lights its Room declares, bottom
first, each light covering PROGRESS_MAX / lights. On TOWER_BAR's 10 lights
that is 10% a light, so 1% of progress is a tenth of one light's level.
At 100% the bar holds full and further taps do nothing; reload the Bit to
start over.

Each light is one zone of the Tower fixture (p0..p9 in
rooms/TOWER_BAR.toml), in pixel order, bound to an aurora held at level 0
whose level rides its own cc lane (LIGHT_CC_BASE + k, a lane value is
cc/127). A tap sends one cc to each light it moved.

Any device may connect, but only an ie<N> device (PLAYER_DEV_RE) is
admitted to the player role; every other device falls through to its own
solo role, untouched by the game. No scoring.
"""

from __future__ import annotations

import re

from control.bit import Bit
from control.cues import LightCue, fixture_dev
from control.functions import FunctionTable
from control.roles import Role, RoleClass, RoleTable

MINIGAME_PLAYER_NODE = "MINIGAME_PLAYER_NODE"

# The devices that play: ie1, ie2, ...
PLAYER_DEV_RE = re.compile(r"ie\d+")

TOWER_FIXTURE = "tower"
PROGRESS_MAX = 100             # percent
PROGRESS_PER_TAP = 1           # percent
# The bar's light zones when no Room is loaded (a hand-built Bit).
DEFAULT_LIGHTS = tuple(f"p{k}" for k in range(10))

# Light k's level rides cc LIGHT_CC_BASE + k, clear of cc:11, which
# Control's breath drives.
LIGHT_CC_BASE = 20
BAR_HUE = 0.33                 # green


def light_level(progress: int, k: int, lights: int) -> float:
    """Light k's level, 0..1, at `progress` percent on a bar of `lights`."""
    return min(max(progress * lights / PROGRESS_MAX - k, 0.0), 1.0)


def tap_count(args: list) -> int:
    """The taps in one /game/tap gesture: its `count`, at least 1."""
    try:
        return max(int(args[3]), 1)
    except (IndexError, TypeError, ValueError):
        return 1


class MinigameBit(Bit):
    version = "0.2"

    room_types = {"TOWER_BAR"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.progress = 0
        self._players: set[str] = set()

    def _lights(self) -> tuple[str, ...]:
        """The Tower fixture's zones, bottom (pixel 0) first: one per light."""
        if self.room is None:
            return DEFAULT_LIGHTS
        for fixture in self.room.profile.fixtures:
            if fixture.name == TOWER_FIXTURE:
                zones = sorted(fixture.zones, key=lambda z: z.start)
                if not zones:
                    raise ValueError(f"fixture {TOWER_FIXTURE!r} declares "
                                     "no zones to light as a bar")
                if LIGHT_CC_BASE + len(zones) > 128:
                    raise ValueError(f"{len(zones)} lights need more cc "
                                     f"lanes than {LIGHT_CC_BASE}..127")
                return tuple(z.name for z in zones)
        raise ValueError(f"Room {self.room.name!r} has no fixture "
                         f"{TOWER_FIXTURE!r}")

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player",
            role_class=RoleClass.JAM,      # every ie<N> device, see admits()
            capacity=None,
            scored=False,
            uses=["tap"],
        )
        return RoleTable(roles={"player": player},
                         node_map={MINIGAME_PLAYER_NODE: ["player"]})

    def instrument_requirements(self) -> tuple:
        return ()

    def room_manifests(self) -> tuple[dict, dict]:
        light = {"instruments": [
            {"instrument": "aurora", "target": f"{TOWER_FIXTURE}.{zone}",
             "params": {"hue": BAR_HUE, "level": 0.0},
             "lanes": [{"source": f"cc:{LIGHT_CC_BASE + k}",
                        "dest": "level"}]}
            for k, zone in enumerate(self._lights())
        ]}
        return (light, {})

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
        return role_name != "player" or PLAYER_DEV_RE.fullmatch(dev) is not None

    def on_join(self, dev: str, role_name: str) -> None:
        if role_name == "player":
            self._players.add(dev)
        else:
            self._players.discard(dev)

    def update(self, dt: float) -> bool:
        return False   # this Bit never auto-completes; unload it from the Console

    def on_complete(self) -> None:
        pass

    def result(self) -> dict | None:
        return None

    def status(self) -> dict:
        return {"progress": self.progress, "players": sorted(self._players)}

    def on_unload(self) -> None:
        pass

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """PROGRESS_PER_TAP more per tap in the gesture, shown on every
        light that moved. A tap from a non-player, or once the bar is full,
        does nothing."""
        if dev not in self._players or self.progress >= PROGRESS_MAX:
            return []
        before = self.progress
        self.progress = min(before + PROGRESS_PER_TAP * tap_count(args),
                            PROGRESS_MAX)
        n = len(self._lights())
        out = []
        for k in range(n):
            value = round(light_level(self.progress, k, n) * 127)
            if value != round(light_level(before, k, n) * 127):
                out.append(LightCue(fixture_dev(TOWER_FIXTURE), 0xB0,
                                    LIGHT_CC_BASE + k, value, when=at))
        return out
