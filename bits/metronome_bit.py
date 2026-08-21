"""MetronomeBit: a call-and-response metronome game for RoomType.DEMO.

See docs/superpowers/specs/2026-08-20-metronome-bit-design.md. This module
carries only the static declarations (roles, manifests, triggers) plus the
lifecycle scaffolding later tasks fill in -- no gameplay logic yet.
"""

from __future__ import annotations

import random

from control.bit import Bit
from control.cues import TARGET
from control.roles import Role, RoleClass, RoleTable
from control.rooms import RoomType, room_role
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTable,
    TriggerTarget,
)


def _fireworks_script():
    """12 flashes over ~1.4 s, seeded so every build is identical."""
    rng = random.Random(2026)
    steps = []
    for i in range(12):
        t = i * 0.12
        pitch = rng.randrange(48, 84)
        steps.append(ScriptStep(t, (TARGET, 0xB0, 70, rng.randrange(0, 128))))
        steps.append(ScriptStep(t, (TARGET, 0x90, pitch, 100)))
        steps.append(ScriptStep(t + 0.08, (TARGET, 0x80, pitch, 0)))
    return tuple(steps)


def _tri(x: float) -> float:
    f = x % 1.0
    return 2 * f if f < 0.5 else 2 * (1 - f)


def _finale_script():
    steps = [
        ScriptStep(0.0, (TARGET, 0xC0, 89, 0)),
        ScriptStep(0.0, (TARGET, 0xB0, 21, 127)),
        ScriptStep(0.0, (TARGET, 0xB0, 11, 0)),
        ScriptStep(0.1, (TARGET, 0x90, 57, 100)),
    ]
    for i in range(20):
        t = 0.5 * (i + 1)
        value = int(127 * _tri(i / 10.0))
        steps.append(ScriptStep(t, (TARGET, 0xB0, 74, value)))
    steps.append(ScriptStep(10.0, (TARGET, 0x80, 57, 0)))
    steps.append(ScriptStep(10.0, (TARGET, 0xB0, 21, 0)))
    steps.append(ScriptStep(10.0, (TARGET, 0xB0, 11, LEVEL_BASE)))
    return tuple(steps)


BEAT_S = 0.6                 # 100 BPM
BEATS_PER_CYCLE = 8          # 4 call + 4 wait
CYCLES = 4
LEAD_IN_S = BEAT_S
TOLERANCE_S = 0.050
INPUT_OFFSET_S = 0.0         # calibration knob, subtracted from tap `at`
JUDGE_SLACK_S = 0.050
FINALE_S = 10.0
CLICK_KEY, HARD_VEL, SOFT_VEL = 76, 120, 65
FAIL_KEY, FAIL_VEL = 33, 110
PROG_CLICK, PROG_FAIL, PROG_PAD = 115, 38, 89
GREEN_CC, RED_CC = 42, 0     # cc:74 hue values (~0.33 green, 0.0 red)
LEVEL_BASE, LEVEL_PULSE = 60, 110   # cc:11 neutral vs on-beat pulse
BLOOM_HUE_CC = 70            # bloom hue lane, separate from aurora's 74
RAINBOW_LEVEL_CC = 21        # room rainbow's level lane (finale only)


class MetronomeBit(Bit):
    version = "0.1"
    room_types = {RoomType.DEMO}

    def __init__(self):
        self._players: list[str] = []
        self._rotation: list[str] = []
        self._t0 = None
        self._next_beat = 0
        self._elapsed = 0.0
        self._done = False
        self._successes: dict[str, int] = {}
        self._phrase = None
        self._judged_cycles = 0
        self._finale_end = None
        self._tap_errors_ms: list[float] = []
        self._pending_fires: list = []

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player",
            role_class=RoleClass.UNIQUE,
            capacity=2,
            scored=True,
            uses=["tap"],
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.33, "level": 0.47},
                     "lanes": [{"source": "cc:74", "dest": "hue"},
                               {"source": "cc:11", "dest": "level"}]},
                    {"instrument": "bloom", "target": "primary",
                     "params": {"hue": 0.33},
                     "lanes": [{"source": "note", "dest": "trigger"},
                               {"source": "cc:70", "dest": "hue"}]},
                ],
            },
        )
        room_light = {
            "instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.33, "level": 0.47},
                 "lanes": [{"source": "cc:74", "dest": "hue"},
                           {"source": "cc:11", "dest": "level"}]},
                {"instrument": "bloom", "target": "primary",
                 "params": {"hue": 0.33},
                 "lanes": [{"source": "note", "dest": "trigger"},
                           {"source": "cc:70", "dest": "hue"}]},
                {"instrument": "rainbow", "target": "primary",
                 "params": {"hue": 0.0, "level": 0.0, "span": 1.0,
                           "speed": 0.05},
                 "lanes": [{"source": "cc:21", "dest": "level"}]},
            ],
        }
        room_ugen = {
            "instruments": [
                {"instrument": "flsyn", "program": 115,
                 "lanes": [{"source": "cc:74", "dest": "cc:74"},
                           {"source": "cc:11", "dest": "cc:11"}]},
            ],
        }
        room_name, room, room_node = room_role(
            RoomType.DEMO, light_manifest=room_light, ugen_manifest=room_ugen)
        roles = {"player": player, room_name: room}
        node_map = {"METRO_PLAYER_NODE": ["player"], room_node: [room_name]}
        return RoleTable(roles=roles, node_map=node_map)

    @property
    def trigger_table(self) -> TriggerTable:
        def _adjudicated(name: str, description: str) -> Condition:
            return Condition(name=name, description=description,
                             source=ConditionSource.BIT_ADJUDICATED)

        return TriggerTable(triggers={
            "fireworks_player": Trigger(
                name="fireworks_player",
                description="Celebratory flashes on the player who nailed it",
                target=TriggerTarget.DEVICE,
                condition=_adjudicated(
                    "phrase_success", "Player matches the call phrase"),
                script=_fireworks_script(),
            ),
            "fireworks_room": Trigger(
                name="fireworks_room",
                description="Celebratory flashes across the Room",
                target=TriggerTarget.ROOM,
                condition=_adjudicated(
                    "phrase_success", "Player matches the call phrase"),
                script=_fireworks_script(),
            ),
            "fail_player": Trigger(
                name="fail_player",
                description="Player's light goes red and dark on a miss",
                target=TriggerTarget.DEVICE,
                condition=_adjudicated(
                    "phrase_fail", "Player misses the call phrase"),
                script=(
                    ScriptStep(0.0, (TARGET, 0xB0, 74, RED_CC)),
                    ScriptStep(1.0, (TARGET, 0xB0, 11, 0)),
                ),
            ),
            "fail_room": Trigger(
                name="fail_room",
                description="Room flashes a fail cue and restores the click",
                target=TriggerTarget.ROOM,
                condition=_adjudicated(
                    "phrase_fail", "Player misses the call phrase"),
                script=(
                    ScriptStep(0.0, (TARGET, 0xC0, PROG_FAIL, 0)),
                    ScriptStep(0.0, (TARGET, 0xB0, 74, RED_CC)),
                    ScriptStep(0.05, (TARGET, 0x90, FAIL_KEY, FAIL_VEL)),
                    ScriptStep(0.9, (TARGET, 0x80, FAIL_KEY, 0)),
                    ScriptStep(1.0, (TARGET, 0xC0, PROG_CLICK, 0)),
                ),
            ),
            "finale": Trigger(
                name="finale",
                description="Closing rainbow sweep and pad after the last cycle",
                target=TriggerTarget.ROOM,
                condition=_adjudicated(
                    "run_complete", "All cycles finished"),
                script=_finale_script(),
            ),
        })

    def on_setup_enter(self) -> None:
        pass

    def on_run_start(self) -> None:
        self._rotation = list(self._players)
        self._t0 = None
        self._next_beat = 0
        self._elapsed = 0.0
        self._done = False
        self._successes = {}
        self._phrase = None
        self._judged_cycles = 0
        self._finale_end = None
        self._tap_errors_ms = []
        self._pending_fires = []

    def on_join(self, dev: str, role_name: str) -> None:
        if role_name == "player":
            self._players.append(dev)

    def update(self, dt: float) -> bool:
        self._elapsed += dt
        return self._done

    def on_complete(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def status(self) -> dict:
        return {}

    def verb_handlers(self) -> dict:
        return {}
