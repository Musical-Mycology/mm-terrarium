"""MinigameBit: a single-Tuneshroom bench toy exercising a three-phase
state machine (PENDING / INGAME / END).

    PENDING --tap--> INGAME --10 blinks, 2s apart--> END
       ^                |                            |
       +------tap-------+-------------tap------------+

A tap in PENDING starts the round; a tap in INGAME or END resets straight
back to PENDING. In INGAME the ring shows a scrolling rainbow and blinks
white 10 times over it, 2 seconds apart; the rainbow goes dark at the 10th
blink and the round ends on its own (END). The rainbow is the role's own
light manifest held at level 0: rainbow_on/rainbow_off move its level lane,
so outside INGAME the ring is dark. The three phases are State classes run
by state_machine.py's StateMachine: INGAME's on_enter lights the rainbow,
its on_tick schedules the blinks and its on_exit darkens it. No scoring, no instrument gating -- see
Rev1Bit (bits/rev1/rev1_bit.py) for the pattern of gating a role on real
Rev-1 hardware capabilities if this ever needs to run on the board rather
than a plain Tuneshroom/testshroom.
"""

from __future__ import annotations

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

from .state_machine import State, StateMachine

MINIGAME_PLAYER_NODE = "MINIGAME_PLAYER_NODE"

BLINK_COUNT = 10
BLINK_INTERVAL_S = 2.0
BLINK_FLASH_S = 0.5            # how long each blink stays lit
BLINK_RGB = (255, 255, 255)
BLINK_LEVEL = 0.9

# The player's rainbow. Its level rides RAINBOW_LEVEL_CC (a lane value is
# cc/127), never cc:11, which Control's breath drives (breath=False below).
RAINBOW_LEVEL_CC = 7
RAINBOW_SPEED = 0.25           # hue cycles per second around the ring


class Pending(State):
    """Waiting for a tap to start the round."""
    name = "PENDING"


class InGame(State):
    """The round: rainbow lit, BLINK_COUNT blinks on a BLINK_INTERVAL_S grid
    from the starting tap's `at`, then END."""
    name = "INGAME"

    def __init__(self, bit: "MinigameBit") -> None:
        super().__init__()
        self.bit = bit
        self.t0: float | None = None
        self.next_blink = 0

    def grid(self, k: int) -> float:
        """Absolute O2 time of blink `k` (0-indexed)."""
        return self.t0 + k * BLINK_INTERVAL_S

    def on_enter(self, at):
        self.t0 = at
        self.next_blink = 0
        return [FireFunction("rainbow_on", dev=self.bit.dev, at=at)]

    def on_tick(self, at):
        out = []
        while self.next_blink < BLINK_COUNT and self.grid(self.next_blink) <= at:
            out.append(FireFunction("blink", dev=self.bit.dev,
                                    at=self.grid(self.next_blink)))
            self.next_blink += 1
        if self.next_blink >= BLINK_COUNT:
            # Leave at the last blink's own time, so the rainbow goes dark
            # under its white flash.
            self.request(End.name, at=self.grid(BLINK_COUNT - 1))
        return out

    def on_exit(self, at):
        return [FireFunction("rainbow_off", dev=self.bit.dev, at=at)]


class End(State):
    """Round finished; only a tap does anything here."""
    name = "END"


class MinigameBit(Bit):
    version = "0.1"

    room_types = {"TEST"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.dev: str | None = None
        self._ingame = InGame(self)
        self._sm = StateMachine([Pending(), self._ingame, End()],
                                initial=Pending.name)
        self._sm.start()

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player",
            role_class=RoleClass.UNIQUE,   # exactly one device holds this role
            capacity=1,
            scored=False,
            uses=["tap", "swing"],
            breath=False,
            light_manifest={"instruments": [
                {"instrument": "rainbow", "target": "primary",
                 "params": {"hue": 0.0, "level": 0.0, "span": 1.0,
                            "speed": RAINBOW_SPEED},
                 "lanes": [{"source": f"cc:{RAINBOW_LEVEL_CC}",
                            "dest": "level"}]},
            ]},
        )
        return RoleTable(roles={"player": player},
                         node_map={MINIGAME_PLAYER_NODE: ["player"]})

    def instrument_requirements(self) -> tuple:
        return ()

    def room_manifests(self) -> tuple[dict, dict]:
        return ({}, {})

    @property
    def function_table(self) -> FunctionTable:
        def rainbow(name, value, description):
            return Function(
                name=name, description=description,
                target=FunctionTarget.DEVICE,
                condition=Condition(
                    name=name, source=ConditionSource.BIT_ADJUDICATED,
                    description=description),
                script=(ScriptStep(
                    0.0, (TARGET, 0xB0, RAINBOW_LEVEL_CC, value)),),
            )

        return FunctionTable(functions={
            "rainbow_on": rainbow("rainbow_on", 127,
                                  "Rainbow lit: the round started"),
            "rainbow_off": rainbow("rainbow_off", 0,
                                   "Rainbow dark: the round reset or ended"),
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
        # clearing dev here orphaned the lobby's device (MetronomeBit
        # keeps its _players across run start for the same reason).
        self._sm.start()
        # on_join fires inside run(), when validations materialize into
        # roles, before this runs, so the player is kept: clearing _dev
        # here orphaned the lobby's device (MetronomeBit keeps its _players
        # across run start for the same reason).
        self._enter(Phase.PENDING)
        self._blink_t0 = None
        self._next_blink = 0

    def on_join(self, dev: str, role_name: str) -> None:
        if role_name == "player":
            self.dev = dev

    def update(self, dt: float) -> bool:
        return False   # this Bit never auto-completes; unload it from the Console

    def fires(self, at: float) -> list:
        return self._sm.tick(at)

    def on_complete(self) -> None:
        pass

    def result(self) -> dict | None:
        return None

    def status(self) -> dict:
        phase = self._sm.state
        return {"phase": phase, "dev": self.dev,
                "blinks": 0 if phase == Pending.name
                else self._ingame.next_blink}

    def on_unload(self) -> None:
        pass

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """PENDING -> INGAME: starts the round, first blink at `at`.
        INGAME or END -> PENDING: resets, so a round in progress is never
        restarted by a tap -- it takes a second tap to start again."""
        if dev != self.dev:
            return []
        target = InGame.name if self._sm.state == Pending.name else Pending.name
        return self._sm.transition(target, at)

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        """Prints the swing's O2 clock time: the device's onset stamp, or
        Control's O2 clock if the stamp was unusable. `at` is that plus
        cue_horizon (the presentation time), so take the horizon back off."""
        o2_time = at - self.cue_horizon
        print(f"minigame: swing from {dev} at O2 {o2_time:.3f}", flush=True)
        return []
