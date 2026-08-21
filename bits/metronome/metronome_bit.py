"""MetronomeBit: a call-and-response metronome game for RoomType.DEMO.

See docs/superpowers/specs/2026-08-20-metronome-bit-design.md. This module
carries only the static declarations (roles, manifests, triggers) plus the
lifecycle scaffolding later tasks fill in -- no gameplay logic yet.
"""

from __future__ import annotations

import random

from control.bit import Bit
from control.cues import ROOM, TARGET, FireTrigger, LightCue
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

    BEAT_S = BEAT_S
    BEATS_PER_CYCLE = BEATS_PER_CYCLE
    CYCLES = CYCLES
    LEAD_IN_S = LEAD_IN_S
    TOLERANCE_S = TOLERANCE_S
    INPUT_OFFSET_S = INPUT_OFFSET_S
    JUDGE_SLACK_S = JUDGE_SLACK_S
    FINALE_S = FINALE_S
    CLICK_KEY, HARD_VEL, SOFT_VEL = CLICK_KEY, HARD_VEL, SOFT_VEL
    FAIL_KEY, FAIL_VEL = FAIL_KEY, FAIL_VEL
    PROG_CLICK, PROG_FAIL, PROG_PAD = PROG_CLICK, PROG_FAIL, PROG_PAD
    GREEN_CC, RED_CC = GREEN_CC, RED_CC
    LEVEL_BASE, LEVEL_PULSE = LEVEL_BASE, LEVEL_PULSE
    BLOOM_HUE_CC = BLOOM_HUE_CC
    RAINBOW_LEVEL_CC = RAINBOW_LEVEL_CC

    def __init__(self, config=None):
        super().__init__(config)
        if config and config.rhythm:
            r = config.rhythm
            self.BEAT_S = 60.0 / r.bpm
            self.LEAD_IN_S = self.BEAT_S
            self.BEATS_PER_CYCLE = r.beats_per_cycle
            self.CYCLES = r.cycles
            self.TOLERANCE_S = r.grading_window_ms / 1000.0
            self.INPUT_OFFSET_S = r.input_offset_ms / 1000.0
        self._players: list[str] = []
        self._rotation: list[str] = []
        self._t0 = None
        self._next_beat = 0
        self._elapsed = 0.0
        self._done = False
        self._successes: dict[str, int] = {}
        self._phrases: dict[int, dict] = {}
        self._judged_cycles = 0
        self._finale_end = None
        self._tap_errors_ms: list[float] = []
        self._pending_fires: list = []
        self._failed_devs: set = set()

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
        self._phrases = {}
        self._judged_cycles = 0
        self._finale_end = None
        self._tap_errors_ms = []
        self._pending_fires = []
        self._failed_devs = set()

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

    def result(self) -> dict:
        return {"phrases": self.CYCLES, "successes": dict(self._successes)}

    def status(self) -> dict:
        cycle = self._judged_cycles if self._judged_cycles < self.CYCLES else self.CYCLES - 1
        return {
            "turn": self._turn_dev(cycle),
            "cycle": cycle,
            "judged_cycles": self._judged_cycles,
            "elapsed": round(self._elapsed, 3),
            "done": self._done,
            "tap_errors_ms": list(self._tap_errors_ms[-8:]),
        }

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def _turn_dev(self, cycle: int) -> str | None:
        if not self._rotation:
            return None
        return self._rotation[cycle % len(self._rotation)]

    def _phrase_for(self, cycle: int) -> dict:
        if cycle not in self._phrases:
            self._phrases[cycle] = {"cycle": cycle, "hits": set(), "spoiled": False}
        return self._phrases[cycle]

    def _current_cycle(self, t: float) -> int | None:
        if self._t0 is None:
            return None
        dt = t - self._t0
        if dt < -self.TOLERANCE_S:
            return None
        last_grid = self._grid(self.CYCLES * self.BEATS_PER_CYCLE - 1)
        if t > last_grid + self.TOLERANCE_S:
            return None
        k = dt / self.BEAT_S
        cycle = int(k // self.BEATS_PER_CYCLE)
        if cycle < 0:
            cycle = 0
        if cycle >= self.CYCLES:
            cycle = self.CYCLES - 1
        # Floor division alone always lands on the right cycle here: cycle
        # spacing (BEAT_S == 0.6s) is far larger than TOLERANCE_S (0.05s),
        # so a tap within tolerance of cycle c's last wait beat can never
        # cross into the beat-index range of cycle c+1. This would only
        # matter if TOLERANCE_S approached BEAT_S.
        return cycle

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        if self._t0 is None or self._done:
            return []
        t = at - self.INPUT_OFFSET_S
        cycle = self._current_cycle(t)
        if cycle is None or dev != self._turn_dev(cycle):
            return []
        phrase = self._phrase_for(cycle)
        # nearest wait-beat gridpoint of this cycle
        best_w, best_err = None, None
        for w in range(4):
            err = t - self._grid(cycle * 8 + 4 + w)
            if best_err is None or abs(err) < abs(best_err):
                best_w, best_err = w, err
        self._tap_errors_ms.append(round(best_err * 1000.0, 1))
        if abs(best_err) <= self.TOLERANCE_S:
            phrase["hits"].add(best_w)
        else:
            phrase["spoiled"] = True
        return []

    def _grid(self, k: int) -> float:
        """Absolute O2 time of global beat index `k` (0..31)."""
        return self._t0 + k * self.BEAT_S

    def _beat_cues(self, k: int) -> list:
        """All absolutely-timed LightCues for global beat `k`."""
        t = self._grid(k)
        pos = k % self.BEATS_PER_CYCLE
        out = []

        # Every beat: level pulse on ROOM and every player, then decay back
        # to the neutral level.
        for dev in [ROOM, *self._rotation]:
            if dev in self._failed_devs:
                continue
            out.append(LightCue(dev, 0xB0, 11, self.LEVEL_PULSE, when=t))
            out.append(LightCue(dev, 0xB0, 11, self.LEVEL_BASE, when=t + 0.15))

        # Call beats (0-3): click note pair on ROOM, hard on the downbeat.
        if pos in (0, 1, 2, 3):
            vel = self.HARD_VEL if pos == 0 else self.SOFT_VEL
            out.append(LightCue(ROOM, 0x90, self.CLICK_KEY, vel, when=t))
            out.append(LightCue(ROOM, 0x80, self.CLICK_KEY, 0, when=t + 0.1))

        if pos == 0:
            out.append(LightCue(ROOM, 0xB0, 74, self.GREEN_CC, when=t))
            dev = self._turn_dev(k // self.BEATS_PER_CYCLE)
            if dev is not None:
                self._failed_devs.discard(dev)
                out.append(LightCue(dev, 0xB0, 74, self.GREEN_CC, when=t))
                out.append(LightCue(dev, 0xB0, 11, self.LEVEL_BASE, when=t))

        return out

    def cues(self, at: float) -> list:
        out = []
        if self._t0 is None:
            self._t0 = at + self.LEAD_IN_S
        # Emit each beat's cues once, when its gridpoint enters the horizon
        # one beat ahead of `at`. TimedQueue holds them until `when`.
        total = self.BEATS_PER_CYCLE * self.CYCLES
        while (self._next_beat < total
               and self._grid(self._next_beat) <= at + self.BEAT_S):
            out.extend(self._beat_cues(self._next_beat))
            self._next_beat += 1

        # Judge every unjudged cycle whose slack window has passed. A loop
        # (not a single check) so a cues(at) call whose `at` jumps past more
        # than one cycle's judge deadline -- a stalled tick, scheduler
        # catch-up, or the final call -- still judges each of them.
        while self._judged_cycles < self.CYCLES:
            c = self._judged_cycles
            deadline = self._grid(c * 8 + 7) + self.TOLERANCE_S + self.JUDGE_SLACK_S
            if at < deadline:
                break
            dev = self._turn_dev(c)
            if dev is not None:
                phrase = self._phrase_for(c)
                success = phrase["hits"] == {0, 1, 2, 3} and not phrase["spoiled"]
                if success:
                    out.append(FireTrigger("fireworks_player", dev))
                    out.append(FireTrigger("fireworks_room"))
                    self._successes[dev] = self._successes.get(dev, 0) + 1
                else:
                    self._failed_devs.add(dev)
                    out.append(FireTrigger("fail_player", dev))
                    out.append(FireTrigger("fail_room"))
            self._judged_cycles += 1
            if self._judged_cycles == self.CYCLES:
                if sum(self._successes.values()) >= 1:
                    if self._finale_end is None:
                        out.append(FireTrigger("finale"))
                        self._finale_end = at + self.FINALE_S
                else:
                    self._done = True

        if self._finale_end is not None and at >= self._finale_end:
            self._done = True

        return out
