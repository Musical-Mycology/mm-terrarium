"""TestBit: the durable reference/test fixture for the Control+GameServer
lifecycle. Exercises both a scored and a jam role. Not throwaway -- this
stays in the repo as the engine's regression fixture. See design spec
section 4.
"""

from control.bit import Bit
from control.cues import ROOM, PlayCue
from control.roles import Role, RoleClass, RoleTable
from control.rooms import RoomType, room_role

RUN_DURATION_SECONDS = 2.0


class TestBit(Bit):
    version = "0.1"

    # Seconds for one full out-and-back sweep of the Room's ambient hue.
    ROOM_DRIFT_PERIOD = 12.0

    def __init__(self, run_duration: float = RUN_DURATION_SECONDS):
        self._run_duration = run_duration
        self._elapsed = 0.0
        self._setup_entered = False
        self._run_started = False
        self._completed = False
        self._unloaded = False

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.SHARED, capacity=None,
            scored=True,
            # What this role asks the device for. The simulator draws exactly
            # these as active; jammer below deliberately asks for less, so
            # switching nodes visibly changes the device's control panel.
            uses=["tilt", "tap", "shake", "speaker"],
            samples=["click", "chime"],
            # First real light-lane declaration: the act that freezes the
            # light-manifest v2 authored shape (see control/roles.py).
            # Instrument names are opaque to Control; these are luxaeterna
            # registry names. Declaring `level` opts aurora out of its private
            # breathing clock and onto cc:11, which is what lets the audio
            # swell in step with the visible pulse rather than near it.
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.33, "level": 0.55},
                     "lanes": [{"source": "cc:74", "dest": "hue"},
                               {"source": "cc:11", "dest": "level"}]},
                ],
            },
            # The audio half of the SAME two controllers. cc:74 is General
            # MIDI Brightness (FluidSynth reads it as filter cutoff) and cc:11
            # is Expression (a direct attenuation, so the swell is audible on
            # any soundfont). Both lanes forward the controller unchanged; the
            # lane exists so a role CAN remap a gesture, not because it must.
            # v0 and provisional, not a frozen wire contract: see
            # docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md.
            ugen_manifest={
                "instruments": [
                    {"instrument": "flsyn", "program": 89,
                     "drone": {"key": 45, "velocity": 90},
                     "lanes": [{"source": "cc:74", "dest": "cc:74"},
                               {"source": "cc:11", "dest": "cc:11"}]},
                ],
            },
            welcome={
                "light": {"instrument": "glow",
                          "params": {"hue": 0.33}, "duration": 1.5},
                "audio": {"instrument": "chime", "duration": 1.5},
            },
        )
        jammer = Role(name="jammer", role_class=RoleClass.JAM,
                      capacity=None, scored=False, uses=["tilt"])
        # The Room's own role. Its cc:74 lane is driven two ways now: by any
        # player's tilt (see _on_tilt) and by this Bit's own cues() drift, so
        # the Room animates whether or not anyone has joined.
        room_name, room, room_node = room_role(
            RoomType.TEST,
            # A field-rate gesture, like player's aurora -- no note lane,
            # so it renders continuously under cc:74 without the note-
            # triggered strobe TestBit's own docstring already explains.
            # Deliberately no cc:11/level lane (unlike player): breath-
            # feeding the Room is a real, separable enhancement, not
            # needed to prove RoomBridge renders at all.
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.6, "level": 0.55},
                     "lanes": [{"source": "cc:74", "dest": "hue"}]},
                ],
            },
            ugen_manifest={
                "instruments": [
                    {"instrument": "flsyn", "program": 89,
                     "drone": {"key": 50, "velocity": 80},
                     "lanes": [{"source": "cc:74", "dest": "cc:74"}]},
                ],
            },
        )
        return RoleTable(
            roles={"player": player, "jammer": jammer, room_name: room},
            node_map={"TEST_PLAYER_NODE": ["player"],
                      "TEST_JAM_NODE": ["jammer"],
                      room_node: [room_name]},
        )

    def on_setup_enter(self) -> None:
        self._setup_entered = True

    def on_run_start(self) -> None:
        self._run_started = True
        self._elapsed = 0.0

    def update(self, dt: float) -> bool:
        self._elapsed += dt
        return self._elapsed >= self._run_duration

    def cues(self, at: float) -> list:
        """Self-driven Room animation: a slow hue drift so the Room breathes
        with nobody joined.

        verb_handlers() can only ever react to a device, so without this the
        Room's aurora reached its declared static hue once and held it,
        unanimated, for a whole run. Deterministic in self._elapsed, which
        update(dt) already accumulates, so a test can assert the exact value
        at a given elapsed time.

        Triangle rather than sawtooth: a sawtooth snaps from 127 back to 0
        once per period, and aurora GLIDES to its target, so the snap reads
        as a visible lurch rather than a wrap.
        """
        phase = (self._elapsed % self.ROOM_DRIFT_PERIOD) / self.ROOM_DRIFT_PERIOD
        cc = int(round(254 * (phase if phase < 0.5 else 1.0 - phase)))
        return [(ROOM, 0xB0, 74, cc)]

    def on_complete(self) -> None:
        self._completed = True

    def on_unload(self) -> None:
        self._unloaded = True

    def status(self) -> dict:
        return {"elapsed": round(self._elapsed, 2),
                "run_duration": self._run_duration}

    def verb_handlers(self) -> dict:
        """Gameplay verbs beyond the fixed lifecycle set. `tilt` maps device
        tilt onto cc:74, which this Bit's `player` role binds to aurora's hue
        lane -- so tilting a device glides its Shroom's colour. `tap` and
        `shake` exercise the local-sample path and the same hue lane.
        Boundary rule 3: the Bit decides the light consequence, not the
        transport."""
        return {"tilt": self._on_tilt,
                "tap": self._on_tap,
                "shake": self._on_shake}

    def _on_tilt(self, dev: str, args: list, at: float) -> list:
        """args: [dev, gamma]. gamma is degrees in [-90, 90].

        Two cues, one `at`. The calling device's own hue lane, and the
        Room's. The Room role declares cc:74 on BOTH its light_manifest
        (aurora hue) and its ugen_manifest (FluidSynth cutoff), so one tilt
        moves the Room's colour and the Room's drone timbre against a single
        shared time. Neither cue names a time: control/engine.py stamps both
        with `at`, which is what makes "one gesture, one T" hold without a
        Bit having to remember to say so.
        """
        gamma = float(args[1]) if len(args) > 1 else 0.0
        gamma = max(-90.0, min(90.0, gamma))
        cc = int(round((gamma + 90.0) / 180.0 * 127.0))
        return [(dev, 0xB0, 74, cc), (ROOM, 0xB0, 74, cc)]

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """args: [dev, peak_g, duration_ms, count]. A single tap clicks, a
        double chimes; both flash the hue lane so the tap is visible as well
        as audible."""
        count = int(args[3]) if len(args) > 3 else 1
        name = "chime" if count >= 2 else "click"
        return [PlayCue(dev, name, ""), (dev, 0xB0, 74, 127)]

    def _on_shake(self, dev: str, args: list, at: float) -> list:
        """args: [dev, peak_g, duration_ms, sweep_deg]. Sweep drives the hue
        lane: a wider sweep pushes the colour further."""
        sweep = float(args[3]) if len(args) > 3 else 0.0
        sweep = max(0.0, min(90.0, sweep))
        cc = int(round(sweep / 90.0 * 127.0))
        return [(dev, 0xB0, 74, cc)]
