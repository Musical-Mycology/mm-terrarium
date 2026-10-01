"""SoloTestBit: the handshake fixture for the solo jam path. One scored
UNIQUE capacity-1 player and no unscored role at all, so every other
validated device is granted solo:<instrument> (spec 2026-10-01 section
7.3). Completes on its own after RUN_DURATION_SECONDS of RUNNING so a
--ci smoke run exits by itself."""

from control.bit import Bit
from control.roles import Role, RoleClass, RoleTable

RUN_DURATION_SECONDS = 20.0


class SoloTestBit(Bit):
    version = "0.1"
    room_types = {"TEST"}

    def __init__(self, config=None, run_duration: float | None = None):
        super().__init__(config)
        self._run_duration = (
            run_duration if run_duration is not None
            else (config.extras.get("run_duration_seconds") if config else None)
            or RUN_DURATION_SECONDS
        )
        self._elapsed = 0.0

    @property
    def run_duration(self) -> float:
        return self._run_duration

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.UNIQUE, capacity=1,
            scored=True, uses=["tilt"],
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.33, "level": 0.55},
                     "lanes": [{"source": "cc:74", "dest": "hue"},
                               {"source": "cc:11", "dest": "level"}]},
                ],
            },
        )
        return RoleTable(roles={"player": player},
                         node_map={"SOLOTEST_PLAYER_NODE": ["player"]})

    def on_run_start(self) -> None:
        self._elapsed = 0.0

    def update(self, dt: float) -> bool:
        self._elapsed += dt
        return self._elapsed >= self._run_duration

    def status(self) -> dict:
        return {"elapsed": round(self._elapsed, 2),
                "run_duration": self._run_duration}
