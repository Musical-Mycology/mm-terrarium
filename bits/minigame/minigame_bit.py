"""MinigameBit: a single-Tuneshroom bench toy exercising a three-phase
state machine (PENDING / INGAME / END).

    PENDING --tap--> INGAME --10 blinks, 2s apart--> END
       ^                |                            |
       +------tap-------+-------------tap------------+

A tap in PENDING starts the round; a tap in INGAME or END resets straight
back to PENDING. In INGAME the LED blinks 10 times, 2 seconds apart, then
the round ends on its own (END). No scoring, no instrument gating -- see
Rev1Bit (bits/rev1/rev1_bit.py) for the pattern of gating a role on real
Rev-1 hardware capabilities if this ever needs to run on the board rather
than a plain Tuneshroom/testshroom.
"""

from __future__ import annotations

from enum import Enum, auto

from control.bit import Bit
from control.cues import TARGET, FireFunction, SolidCue
from control.functions import (
    Condition,
    ConditionSource,
    Function,
    FunctionTable,
    FunctionTarget,
    ScriptStep,
)
from control.roles import Role, RoleClass, RoleTable

MINIGAME_PLAYER_NODE = "MINIGAME_PLAYER_NODE"

BLINK_COUNT = 10
BLINK_INTERVAL_S = 2.0
BLINK_FLASH_S = 0.5            # how long each blink stays lit
BLINK_RGB = (255, 255, 255)
BLINK_LEVEL = 0.9


class Phase(Enum):
    PENDING = auto()   # waiting for a tap to start the round
    INGAME = auto()    # blinking; counts up to BLINK_COUNT
    END = auto()       # round finished; a tap resets to PENDING


class MinigameBit(Bit):
    version = "0.1"

    room_types = {"TEST"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._phase = Phase.PENDING
        self._dev: str | None = None
        self._blink_t0: float | None = None
        self._next_blink = 0

    def _enter(self, phase: Phase) -> None:
        self._phase = phase

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player",
            role_class=RoleClass.UNIQUE,   # exactly one device holds this role
            capacity=1,
            scored=False,
            uses=["tap", "swing"],
        )
        return RoleTable(roles={"player": player},
                         node_map={MINIGAME_PLAYER_NODE: ["player"]})

    def instrument_requirements(self) -> tuple:
        return ()

    def room_manifests(self) -> tuple[dict, dict]:
        return ({}, {})

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={
            "blink": Function(
                name="blink",
                description=f"LED flash, {BLINK_FLASH_S:g}s",
                target=FunctionTarget.DEVICE,
                condition=Condition(
                    name="blink", source=ConditionSource.BIT_ADJUDICATED,
                    description="One tick of the INGAME blink schedule"),
                script=(ScriptStep(
                    0.0, SolidCue(TARGET, BLINK_RGB, BLINK_LEVEL,
                                 BLINK_FLASH_S)),),
            ),
        })

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "swing": self._on_swing}

    def on_setup_enter(self) -> None:
        pass

    def on_run_start(self) -> None:
        # Joins land in SETUP, before this runs, so the player is kept:
        # clearing _dev here orphaned the lobby's device (MetronomeBit
        # keeps its _players across run start for the same reason).
        self._enter(Phase.PENDING)
        self._blink_t0 = None
        self._next_blink = 0

    def on_join(self, dev: str, role_name: str) -> None:
        if role_name == "player":
            self._dev = dev

    def update(self, dt: float) -> bool:
        return False   # this Bit never auto-completes; unload it from the Console

    def _grid(self, k: int) -> float:
        """Absolute O2 time of blink `k` (0-indexed)."""
        return self._blink_t0 + k * BLINK_INTERVAL_S

    def fires(self, at: float) -> list:
        out = []
        if self._phase is Phase.INGAME:
            while (self._next_blink < BLINK_COUNT
                   and self._grid(self._next_blink) <= at):
                out.append(FireFunction("blink", dev=self._dev,
                                        at=self._grid(self._next_blink)))
                self._next_blink += 1
            if self._next_blink >= BLINK_COUNT:
                self._enter(Phase.END)
        return out

    def on_complete(self) -> None:
        pass

    def result(self) -> dict | None:
        return None

    def status(self) -> dict:
        return {"phase": self._phase.name, "dev": self._dev,
                "blinks": self._next_blink}

    def on_unload(self) -> None:
        pass

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """PENDING -> INGAME: starts the round, first blink at `at`.
        INGAME or END -> PENDING: resets, so a round in progress is never
        restarted by a tap -- it takes a second tap to start again."""
        if dev != self._dev:
            return []
        if self._phase is Phase.PENDING:
            self._enter(Phase.INGAME)
            self._blink_t0 = at
            self._next_blink = 0
            return []
        self._enter(Phase.PENDING)
        self._blink_t0 = None
        self._next_blink = 0
        return []

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        """Prints the swing's O2 clock time: the device's onset stamp, or
        Control's O2 clock if the stamp was unusable. `at` is that plus
        cue_horizon (the presentation time), so take the horizon back off."""
        o2_time = at - self.cue_horizon
        print(f"minigame: swing from {dev} at O2 {o2_time:.3f}", flush=True)
        return []
