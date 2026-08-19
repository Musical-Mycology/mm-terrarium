"""TestBit: the durable reference/test fixture for the Control+GameServer
lifecycle. Exercises both a scored and a jam role. Not throwaway -- this
stays in the repo as the engine's regression fixture. See design spec
section 4.
"""

from control.bit import Bit
from control.cues import ROOM, TARGET, FireTrigger, PlayCue
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

RUN_DURATION_SECONDS = 2.0


class TestBit(Bit):
    version = "0.1"

    # TestBit is the reference fixture for BOTH shipped room types, so the
    # Scored/Jam validation loop works in either. control/boot.py reads
    # this off the class before instantiation.
    room_types = {RoomType.TEST, RoomType.DEMO}

    # Seconds for one full out-and-back sweep of the Room's ambient hue.
    ROOM_DRIFT_PERIOD = 12.0

    # Full-deflection tilts that win a round. A fixture's adjudication, not a
    # game: it exists to be deterministic and assertable at an exact tick.
    ROUND_TILTS = 3

    # How long this Bit stops driving the Room's cc:74 after firing
    # play_aurora. The drift below runs at 44 Hz and shares that lane with the
    # script, so without yielding it the very next tick would overwrite step 0
    # and the declared sweep would never be visible. A Bit yielding a lane it
    # shares with its own script is the general shape here, not a TestBit quirk.
    SCRIPT_QUIET_SECONDS = 2.0

    def __init__(self, run_duration: float = RUN_DURATION_SECONDS):
        self._run_duration = run_duration
        self._elapsed = 0.0
        self._setup_entered = False
        self._run_started = False
        self._completed = False
        self._unloaded = False
        self._full_tilts = 0
        self._round_won = False
        self._rounds_won = 0
        self._quiet_until = 0.0

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
        # A field-rate gesture, like player's aurora -- no note lane, so it
        # renders continuously under cc:74 without the note-triggered strobe
        # TestBit's own docstring already explains. Deliberately no cc:11/
        # level lane (unlike player): breath-feeding the Room is a real,
        # separable enhancement, not needed to prove RoomBridge renders at
        # all. The instrument itself is `rainbow`, not `aurora`: a scrolling
        # hue gradient across the Room's whole concatenated surface, which
        # makes the cross-fixture property -- one declaration, one gradient
        # spanning every fixture -- the thing the reference fixture visibly
        # proves (see design spec section 9).
        room_light = {
            "instruments": [
                {"instrument": "rainbow", "target": "primary",
                 "params": {"hue": 0.6, "level": 0.55,
                           "span": 1.0, "speed": 0.05},
                 "lanes": [{"source": "cc:74", "dest": "hue"}]},
            ],
        }
        room_ugen = {
            "instruments": [
                {"instrument": "flsyn", "program": 89,
                 "drone": {"key": 50, "velocity": 80},
                 "lanes": [{"source": "cc:74", "dest": "cc:74"}]},
            ],
        }
        room_entries = [
            room_role(rt, light_manifest=room_light, ugen_manifest=room_ugen)
            for rt in sorted(self.room_types, key=lambda t: t.name)
        ]
        roles = {"player": player, "jammer": jammer}
        node_map = {"TEST_PLAYER_NODE": ["player"],
                    "TEST_JAM_NODE": ["jammer"]}
        for room_name, room, room_node in room_entries:
            roles[room_name] = room
            node_map[room_node] = [room_name]
        return RoleTable(roles=roles, node_map=node_map)

    def on_setup_enter(self) -> None:
        self._setup_entered = True

    def on_run_start(self) -> None:
        self._run_started = True
        self._elapsed = 0.0
        self._full_tilts = 0
        self._round_won = False
        self._quiet_until = 0.0

    def update(self, dt: float) -> bool:
        self._elapsed += dt
        return self._elapsed >= self._run_duration

    @property
    def trigger_table(self) -> TriggerTable:
        """Two triggers, deliberately: one per fire path.

        play_aurora is bit-adjudicated, so nothing outside this Bit decides
        when a round is won. flash_device is reached through the `tap` verb
        this Bit already implements, and Control does NOT fire it just because
        a tap arrived: _on_tap returns the FireTrigger itself, which is what
        keeps condition evaluation inside the Bit.
        """
        return TriggerTable(triggers={
            "play_aurora": Trigger(
                name="play_aurora",
                description="A slow rainbow sweep across the Room",
                target=TriggerTarget.ROOM,
                condition=Condition(
                    name="round_won",
                    description="User wins a round",
                    source=ConditionSource.BIT_ADJUDICATED),
                script=(
                    ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                    ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
                    ScriptStep(2.0, (TARGET, 0xB0, 74, 0)),
                ),
            ),
            "flash_device": Trigger(
                name="flash_device",
                description="Flash the tapping device and click its speaker",
                target=TriggerTarget.DEVICE,
                condition=Condition(
                    name="tapped",
                    description="Player taps their Shroom",
                    source=ConditionSource.GESTURE_VERB,
                    verb="tap"),
                script=(
                    ScriptStep(0.0, PlayCue(TARGET, "click", "")),
                    ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                ),
            ),
        })

    def cues(self, at: float) -> list:
        """Self-driven Room animation, plus this Bit's own adjudication report.

        verb_handlers() can only ever react to a device, so without the drift
        the Room's rainbow would still scroll on its own -- its `speed` param
        advances the gradient every tick with no input at all -- but its base
        hue would sit fixed at the declared value for a whole run rather than
        sweeping. Deterministic in self._elapsed, which update(dt) already
        accumulates, so a test can assert the exact value at a given elapsed
        time.

        Triangle rather than sawtooth: a sawtooth snaps from 127 back to 0 once
        per period, and rainbow GLIDES to its target, so the snap reads as a
        visible lurch rather than a wrap.

        A won round is reported here rather than from update(dt) because a fire
        is returned in the cue vocabulary, and this is the hook that carries it
        with a presentation time already computed. It latches, so a round fires
        exactly once however many ticks pass before it is drained.
        """
        if self._round_won:
            self._round_won = False
            self._rounds_won += 1
            self._quiet_until = self._elapsed + self.SCRIPT_QUIET_SECONDS
            return [FireTrigger("play_aurora")]
        if self._elapsed < self._quiet_until:
            # play_aurora owns cc:74 until its script finishes. See
            # SCRIPT_QUIET_SECONDS.
            return []
        phase = (self._elapsed % self.ROOM_DRIFT_PERIOD) / self.ROOM_DRIFT_PERIOD
        cc = int(round(254 * (phase if phase < 0.5 else 1.0 - phase)))
        return [(ROOM, 0xB0, 74, cc)]

    def on_complete(self) -> None:
        self._completed = True

    def on_unload(self) -> None:
        self._unloaded = True

    def status(self) -> dict:
        return {"elapsed": round(self._elapsed, 2),
                "run_duration": self._run_duration,
                "full_tilts": self._full_tilts,
                "rounds_won": self._rounds_won}

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

        Two cues, one `at`. The calling device's own hue lane, and the Room's.
        The Room role declares cc:74 on BOTH its light_manifest (rainbow hue)
        and its ugen_manifest (FluidSynth cutoff), so one tilt moves the Room's
        colour and the Room's drone timbre against a single shared time.
        Neither cue names a time: control/engine.py stamps both with `at`,
        which is what makes "one gesture, one T" hold without a Bit having to
        remember to say so.

        Full deflection also counts toward the round. Counted here rather than
        reported here: a round is not a light consequence of this gesture, and
        cues() is where this Bit reports one.
        """
        gamma = float(args[1]) if len(args) > 1 else 0.0
        gamma = max(-90.0, min(90.0, gamma))
        cc = int(round((gamma + 90.0) / 180.0 * 127.0))
        if cc >= 127:
            self._full_tilts += 1
            if self._full_tilts >= self.ROUND_TILTS:
                self._full_tilts = 0
                self._round_won = True
        return [(dev, 0xB0, 74, cc), (ROOM, 0xB0, 74, cc)]

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """args: [dev, peak_g, duration_ms, count]. A single tap clicks, a
        double chimes; both flash the hue lane so the tap is visible as well
        as audible, and both fire this Bit's declared flash_device trigger for
        the tapping device."""
        count = int(args[3]) if len(args) > 3 else 1
        name = "chime" if count >= 2 else "click"
        return [PlayCue(dev, name, ""), (dev, 0xB0, 74, 127),
                FireTrigger("flash_device", dev)]

    def _on_shake(self, dev: str, args: list, at: float) -> list:
        """args: [dev, peak_g, duration_ms, sweep_deg]. Sweep drives the hue
        lane: a wider sweep pushes the colour further."""
        sweep = float(args[3]) if len(args) > 3 else 0.0
        sweep = max(0.0, min(90.0, sweep))
        cc = int(round(sweep / 90.0 * 127.0))
        return [(dev, 0xB0, 74, cc)]
