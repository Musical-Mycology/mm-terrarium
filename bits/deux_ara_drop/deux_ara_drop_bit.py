"""DeuxAraDropBit: MIDI loops dropped in and out from one player device.

One player, on the MIDITEST Room's speaker-equipped Tower:

- a single tap toggles the next loop in ORDER (piano, drums, synth, piano,
  ...): it comes in or drops out at once, on the shared beat grid, and its
  third of the Tower turns green (on) or red (off) -- every tap repaints
  all three thirds, so one lost to a Stop's mute is put right by the next;
- a double tap (a tap gesture with count >= 2) starts the Arabesque from
  the top on the next beat, or stops it if it is playing, without moving
  the loop cycle; while it plays, single Tower pixels flicker white;
- tilting past TIP_DEG ducks the whole mix to DUCK; it comes back only
  once the device is within RIGHT_DEG of upright again (hysteresis).

All the timing lives in control.sequencer.Sequencer: this Bit only decides
which track is on and turns its answers into cues. Every `at` it is handed
(fires and verbs alike) is already a presentation time, passed straight to
the sequencer.

The Room role carries four flsyn parts, one per track (list index = part =
the status byte's channel nibble), and three auroras, one per third of the
Tower, whose hue rides cc 20/21/22.
"""

from __future__ import annotations

import random

from control.bit import Bit
from control.cues import ROOM, LightCue, MusicCue, PixelFlashCue
from control.functions import FunctionTable
from control.midifile import read_midi_file
from control.roles import Role, RoleClass, RoleTable
from control.sequencer import Sequencer

DEUX_PLAYER_NODE = "DEUX_PLAYER_NODE"

BPM = 130.0
ORDER = ("piano", "drums", "synth")          # the single-tap cycle
ARABESQUE = "arabesque"
PARTS = {"piano": 0, "drums": 1, "synth": 2, ARABESQUE: 3}
# General MIDI programs for the melodic parts; drums is the GM kit.
PROGRAMS = {"piano": 0, "synth": 90, ARABESQUE: 0}
ARABESQUE_LEAD_IN = 4.0                      # beats: its first bar is skipped

# Each loop's third of the Tower, and the cc its aurora's hue rides.
THIRDS = {"piano": "tower.bottom", "drums": "tower.middle",
          "synth": "tower.top"}
THIRD_CC = {"piano": 20, "drums": 21, "synth": 22}
GREEN = 42                                   # hue 0.333 x 127
RED = 0

TIP_DEG = 45.0                               # tilt past this ducks
RIGHT_DEG = 20.0                             # back within this restores
DUCK = 0.2

FLICKER_HZ = 12.0                            # mean flashes per second
FLICKER_S = 0.08
WHITE = (0, 0, 0, 255)
TOWER_PIXELS = 14
_MAX_FLICKER_DT = 0.1


def _tap_count(args: list) -> int:
    """The taps in one /game/tap gesture: its `count`, at least 1."""
    try:
        return max(int(args[3]), 1)
    except (IndexError, TypeError, ValueError):
        return 1


class DeuxAraDropBit(Bit):
    version = "0.1"
    room_types = {"MIDITEST"}

    def __init__(self, config=None, rng: random.Random | None = None) -> None:
        super().__init__(config)
        self._rng = rng if rng is not None else random.Random()
        self._seq = Sequencer(BPM)
        for name in ORDER:
            self._seq.add_track(name, self._midi(name), part=PARTS[name],
                                loop=True)
        self._seq.add_track(ARABESQUE, self._midi(ARABESQUE),
                            part=PARTS[ARABESQUE], loop=False,
                            skip_beats=ARABESQUE_LEAD_IN)
        self._started = False
        self._cursor = 0                     # next ORDER index a tap toggles
        self._level = 1.0                    # master volume
        self._last_at: float | None = None

    def _midi(self, key: str):
        return read_midi_file(self.config.asset_path(key))

    # -- declarations -----------------------------------------------------

    @property
    def role_table(self) -> RoleTable:
        player = Role(name="player", role_class=RoleClass.UNIQUE,
                      capacity=1, scored=True, uses=["tap", "tilt"],
                      light_manifest={})
        return RoleTable(roles={"player": player},
                         node_map={DEUX_PLAYER_NODE: ["player"]})

    def room_manifests(self) -> tuple[dict, dict]:
        light = {"instruments": [
            {"instrument": "aurora", "target": THIRDS[name],
             "params": {"hue": 0.0, "level": 0.8},
             "lanes": [{"source": f"cc:{THIRD_CC[name]}", "dest": "hue"}]}
            for name in ORDER
        ]}
        volume = {"source": "cc:7", "dest": "cc:7"}
        pedal = {"source": "cc:64", "dest": "cc:64"}
        ugen = {"instruments": [
            {"instrument": "flsyn", "program": PROGRAMS["piano"],
             "lanes": [volume, pedal]},
            {"instrument": "flsyn", "drums": True, "lanes": [volume]},
            {"instrument": "flsyn", "program": PROGRAMS["synth"],
             "lanes": [volume, pedal]},
            {"instrument": "flsyn", "program": PROGRAMS[ARABESQUE],
             "lanes": [volume, pedal]},
        ]}
        return light, ugen

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "tilt": self._on_tilt}

    # -- the tick ---------------------------------------------------------

    def update(self, dt: float) -> bool:
        return False   # plays until unloaded from the Console

    def fires(self, at: float) -> list:
        out = self._start(at)
        out += self._seq.advance(at)
        dt = 0.0 if self._last_at is None else \
            min(max(at - self._last_at, 0.0), _MAX_FLICKER_DT)
        self._last_at = at
        if self._seq.is_on(ARABESQUE) and \
                self._rng.random() < FLICKER_HZ * dt:
            out.append(PixelFlashCue(ROOM, self._rng.randrange(TOWER_PIXELS),
                                     WHITE, FLICKER_S, when=at))
        return out

    def _start(self, at: float) -> list:
        """Once, at the first `at` this Bit sees: put the grid's beat 0
        there, restore the melodic programs (the lobby reprograms part 0
        during SETUP), paint every third red and set the mix volume."""
        if self._started:
            return []
        self._started = True
        self._seq.start(at)
        out: list = [MusicCue(ROOM, 0xC0 | PARTS[name], program, 0, when=at)
                     for name, program in PROGRAMS.items()]
        out += [LightCue(ROOM, 0xB0, THIRD_CC[name], RED, when=at)
                for name in ORDER]
        out += self._seq.set_master(self._level, at)
        return out

    # -- verbs ------------------------------------------------------------

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """A double tap toggles the Arabesque; a single tap toggles the next
        loop in ORDER and repaints all three thirds, not only its own: a
        tap during a Stop's mute has its LightCue dropped, and the next tap
        must not leave that third's colour stale."""
        out = self._start(at)
        if _tap_count(args) >= 2:
            if self._seq.is_on(ARABESQUE):
                return out + self._seq.disable(ARABESQUE, at)
            return out + self._seq.play_once(ARABESQUE, at)
        name = ORDER[self._cursor % len(ORDER)]
        self._cursor += 1
        if self._seq.is_on(name):
            out += self._seq.disable(name, at)
        else:
            out += self._seq.enable(name, at)
        out += [LightCue(ROOM, 0xB0, THIRD_CC[n],
                         GREEN if self._seq.is_on(n) else RED, when=at)
                for n in ORDER]
        return out

    def _on_tilt(self, dev: str, args: list, at: float) -> list:
        """args: [dev, gamma]. Ducks past TIP_DEG while upright, restores
        within RIGHT_DEG while ducked; anything else is no change."""
        try:
            tilt = abs(float(args[1]))
        except (IndexError, TypeError, ValueError):
            return []
        ducked = self._level < 1.0
        if not ducked and tilt >= TIP_DEG:
            self._level = DUCK
        elif ducked and tilt <= RIGHT_DEG:
            self._level = 1.0
        else:
            return []
        return self._seq.set_master(self._level, at)

    # -- read-outs --------------------------------------------------------

    def status(self) -> dict:
        out = {name: "on" if self._seq.is_on(name) else "off"
               for name in ORDER}
        out[ARABESQUE] = "playing" if self._seq.is_on(ARABESQUE) \
            else "stopped"
        out["volume"] = f"{round(self._level * 100)}%"
        return out
