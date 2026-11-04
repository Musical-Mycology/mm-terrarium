"""BeatsBit: a devtower and two tuneshrooms connect, a Console button starts.

Devices are told apart by their dev id, as in MinigameBit: one devtower<N>
takes the `tower` role and two ie<N> take the `shroom` role. Anything else,
or one more than those capacities, falls through to its own solo role.

The Console's Start button (Bit.actions) loops piano.mid on the shared grid
until the Bit is unloaded, however many of those devices are connected.
A MusicCue is Room audio only (DeviceLinkAgent._on_music_cue), so the piano
sounds from the MIDITEST Room's flsyn, not from the devtower's own speaker.

While the piano plays, beats1.mid is the rhythm for its first measure and
beats2.mid for its third, on every pass of the loop. Each of those beats
lights one of the tower's RESPONDERS (4), chosen by where the beat falls in
its measure: 1 to 1.99 the first responder, 2 to 2.99 the second, 3 to 3.99
the third and 4 to 4.99 the fourth. A responder starts at RESPONDER_RGB on its
beat and fades to dark over RESPONDER_FADE_S, stepped each tick by fires() as
in MinigameBit; a beat on a responder still fading restarts it from full.

The tower's frame pixels are BAR_PIXELS (32) of bar, left dark here, then the
responders' RESPONDER_PIXELS (24) each, bottom first. The tower role declares
no light of its own, so the frame is dark under the PixelFlashCue paint.

A tap on either shroom blinks every seated shroom: FLASH_RGB at full on all
FLASH_PIXELS, fading to dark over FLASH_FADE_S, painted with PixelFlashCue and
stepped by fires() the same way. It needs no Start, and a tap mid-fade restarts
the blink from full. A tap from any other device does nothing.

Call and response: an odd measure n is the responders' call, and the even
measure n + 1 is the players' answer, the same rhythm one measure later. Each
shroom answers for itself. Its tap takes the nearest beat of the answer it has
not yet taken within TAP_WINDOW_BEATS either side, so the window in seconds
follows the tempo, and earns 1 dead on, falling linearly to 0 at the window's
edge. A late tap is forgiven LATE_GRACE_S first, in seconds whatever the tempo:
one that late counts as dead on, and the window's late edge moves out by as
much. The whole judgment runs JUDGE_DELAY_S behind the grid, in seconds whatever
the tempo: an answer beat's dead on is that long after the beat itself, and the
window, its grace and its closing all move with it. A beat with no tap earns 0,
and a tap that takes no beat earns nothing.
A player's score, 0 to 1, is the mean over every answer beat since Start whose
window has closed.

The piano's timing lives in control.sequencer.Sequencer.
"""

from __future__ import annotations

import re

from control.bit import Bit
from control.cues import ROOM, MusicCue, PixelFlashCue
from control.functions import FunctionTable
from control.midifile import read_midi_file
from control.roles import Role, RoleClass, RoleTable
from control.sequencer import Sequencer

BEATS_SHROOM_NODE = "BEATS_SHROOM_NODE"
BEATS_TOWER_NODE = "BEATS_TOWER_NODE"

# The devices that take part: ie1, ie2, ... and devtower1, devtower2, ...
SHROOM_DEV_RE = re.compile(r"ie\d+")
TOWER_DEV_RE = re.compile(r"devtower\d+")
SHROOMS = 2
TOWERS = 1

BPM = 130.0                     # piano.mid carries no tempo; as DeuxAraDropBit
PIANO = "piano"
PIANO_PART = 0
PIANO_PROGRAM = 0               # General MIDI acoustic grand

BEATS_PER_MEASURE = 4
# (asset, measure of the piano loop it plays in, counted from 1). Each asset
# is one measure long, and its own tempo is ignored: it runs on the piano's.
BEAT_CLIPS = (("beats1", 1), ("beats2", 3))

BAR_PIXELS = 32                 # the Dev Tower's bar LEDs, ahead of the responders
RESPONDERS = BEATS_PER_MEASURE  # one per beat of the measure, bottom first
RESPONDER_PIXELS = 24           # LEDs a responder light
RESPONDER_FADE_S = 0.25         # a responder's fade, full to dark
RESPONDER_RGB = (64, 48, 32)    # MinigameBit's half-bright beige

TAP_WINDOW_BEATS = 0.25         # either side of an answer beat
LATE_GRACE_S = 0.020            # how late a tap still counts as dead on
JUDGE_DELAY_S = 0.200           # the judgment's slide behind the grid
JUDGE_SLACK_S = 0.050           # a late tap's trip, before a beat closes

FLASH_PIXELS = 12               # a tuneshroom's LEDs (8 ring + 4 stem)
FLASH_FADE_S = 0.2              # a tap's blink, full to dark
FLASH_RGB = (128, 0, 255)       # MinigameBit's tap purple


def responder_pixels(r: int) -> range:
    """Responder r's pixels, after the bar: 32 + 24r..32 + 24r + 23."""
    start = BAR_PIXELS + r * RESPONDER_PIXELS
    return range(start, start + RESPONDER_PIXELS)


def tap_points(error_beats: float) -> float:
    """What a tap `error_beats` from its beat earns: 1 dead on, 0 at or past
    the window's edge."""
    return max(1.0 - abs(error_beats) / TAP_WINDOW_BEATS, 0.0)


def forgive_late(error_beats: float, grace_beats: float) -> float:
    """A tap's error with a late tap's grace taken off: a late tap no later
    than the grace is dead on, and an early tap is unchanged."""
    return max(error_beats - grace_beats, 0.0) if error_beats > 0 else error_beats


def measure_and_beat(grid_beat: float) -> tuple[int, float]:
    """A grid beat as (measure since Start, beat within it), both from 1."""
    measure, beat = divmod(grid_beat, BEATS_PER_MEASURE)
    return int(measure) + 1, beat + 1


def fade_value(t: float, fade_s: float) -> int:
    """A fade's level, 255..0, `t` seconds into a fade lasting `fade_s`."""
    return round(min(max(1.0 - t / fade_s, 0.0), 1.0) * 255)


class BeatsBit(Bit):
    version = "0.1"
    room_types = {"MIDITEST"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._seq = Sequencer(BPM)
        piano = read_midi_file(self.config.asset_path(PIANO))
        self._seq.add_track(PIANO, piano, part=PIANO_PART, loop=True)
        self._loop_beats = piano.loop_beats()
        self._beats = self._beat_pattern()
        self._beats_done = 0            # beats struck since Start
        self._measures_begun = 0        # measures announced since Start
        # One pass of the players' answer: the call, a measure later.
        self._answers = [beat + BEATS_PER_MEASURE for beat, _ in self._beats]
        self._answers_announced = 0     # answer beats printed since Start
        self._answers_closed = 0        # answer beats judged since Start
        # Open answer beats' taps: grid beat -> {shroom: points}.
        self._open_taps: dict[float, dict[str, float]] = {}
        self._earned: dict[str, float] = {}     # shroom -> points, closed beats
        # Responders mid-fade: r -> (its beat, last level sent).
        self._responders: dict[int, tuple[float, int]] = {}
        # The shrooms' blink under way: (its tap, last level sent), or None.
        self._flash: tuple[float, int] | None = None
        self._shrooms: set[str] = set()
        self._towers: set[str] = set()
        self._started = False

    # -- declarations -----------------------------------------------------

    @property
    def role_table(self) -> RoleTable:
        shroom = Role(name="shroom", role_class=RoleClass.JAM,
                      capacity=SHROOMS, scored=False, uses=["tap"],
                      light_manifest={})
        tower = Role(name="tower", role_class=RoleClass.JAM,
                     capacity=TOWERS, scored=False, light_manifest={})
        return RoleTable(roles={"shroom": shroom, "tower": tower},
                         node_map={BEATS_SHROOM_NODE: ["shroom"],
                                   BEATS_TOWER_NODE: ["tower"]})

    def room_manifests(self) -> tuple[dict, dict]:
        ugen = {"instruments": [
            {"instrument": "flsyn", "program": PIANO_PROGRAM,
             "lanes": [{"source": "cc:7", "dest": "cc:7"}]},
        ]}
        return {}, ugen

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def actions(self) -> dict:
        return {"start": ("Start", self._on_start)}

    def admits(self, dev: str, role_name: str) -> bool:
        if role_name == "shroom":
            return SHROOM_DEV_RE.fullmatch(dev) is not None
        if role_name == "tower":
            return TOWER_DEV_RE.fullmatch(dev) is not None
        return True

    def on_join(self, dev: str, role_name: str) -> None:
        self._shrooms.discard(dev)
        self._towers.discard(dev)
        if role_name == "shroom":
            self._shrooms.add(dev)
        elif role_name == "tower":
            self._towers.add(dev)

    # -- the tick ---------------------------------------------------------

    def update(self, dt: float) -> bool:
        return False   # plays until unloaded from the Console

    def fires(self, at: float) -> list:
        out = self._flash_step(at)
        if not self._started:
            return out
        self._announce_measures(at)
        self._announce_answers(at)
        self._strike_due_beats(at)
        self._close_answers(at)
        return out + self._seq.advance(at) + self._responder_steps(at)

    # -- the button -------------------------------------------------------

    def _on_start(self, at: float) -> list | str:
        """Grid beat 0 at `at`, the melodic program restored (the lobby
        reprograms part 0), the piano in. Its first notes and any beat on
        grid beat 0 go out here rather than on the next tick."""
        if self._started:
            return "already started"
        self._started = True
        self._seq.start(at)
        out: list = [MusicCue(ROOM, 0xC0 | PIANO_PART, PIANO_PROGRAM, 0,
                              when=at)]
        out += self._seq.enable(PIANO, at)
        out += self._seq.advance(at)
        self._strike_due_beats(at)
        out += self._responder_steps(at)
        return out

    # -- the measures -----------------------------------------------------

    def _announce_measures(self, at: float) -> None:
        """Print each measure as it reaches the real time behind presentation
        time `at`, so the line comes with the sound. An even measure is also
        the players' answer, and so is judged."""
        while True:
            start = self._seq.time_at(self._measures_begun * BEATS_PER_MEASURE)
            if start > at - self.cue_horizon:
                return
            self._measures_begun += 1
            measure = self._measures_begun
            print(f"====== Measure {measure} Playing ======", flush=True)
            if measure % 2 == 0:
                print(f"====== Judging {measure} Start ======", flush=True)

    def _announce_answers(self, at: float) -> None:
        """Print each correct answer's O2 time, the moment a tap is judged
        against (its grid beat plus JUDGE_DELAY_S), as it reaches the real time
        behind presentation time `at`, so the line comes on that moment."""
        while self._answers:
            target = self._answer_beat(self._answers_announced)
            beat_at = self._seq.time_at(target) + JUDGE_DELAY_S
            if beat_at > at - self.cue_horizon:
                return
            self._answers_announced += 1
            measure, in_measure = measure_and_beat(target)
            print(f"answer measure {measure} beat {in_measure:g}: "
                  f"o2 t={beat_at:.3f} s", flush=True)

    # -- the beats --------------------------------------------------------

    def _beat_pattern(self) -> list[tuple[float, int]]:
        """One pass of the piano loop's beats, in order: (loop beat, the
        responder it lights). A clip's beats count from 0, so the whole part
        of one names its responder."""
        pattern = []
        for asset, measure in BEAT_CLIPS:
            offset = (measure - 1) * BEATS_PER_MEASURE
            for e in read_midi_file(self.config.asset_path(asset)).events:
                if e.status & 0xF0 == 0x90:
                    pattern.append((offset + e.beat, int(e.beat)))
        return sorted(pattern)

    def _strike_due_beats(self, at: float) -> None:
        """Start the fade of every beat's responder reached by `at`."""
        while self._beats:
            passes, i = divmod(self._beats_done, len(self._beats))
            loop_beat, responder = self._beats[i]
            beat_at = self._seq.time_at(passes * self._loop_beats + loop_beat)
            if beat_at > at:
                return
            self._responders[responder] = (beat_at, 0)
            self._beats_done += 1

    def _responder_steps(self, at: float) -> list:
        """Each fading responder (R, G, B) at its fade's level `at`, sent
        only when the level moved, and held until that fade's end."""
        out = []
        towers = sorted(self._towers)
        for r, (start, sent) in list(self._responders.items()):
            t = at - start
            value = fade_value(t, RESPONDER_FADE_S)
            if value == 0:
                del self._responders[r]
                continue
            if value == sent:
                continue
            self._responders[r] = (start, value)
            rgb = tuple(c * value // 255 for c in RESPONDER_RGB) + (0,)
            out.extend(PixelFlashCue(tower, px, rgb, RESPONDER_FADE_S - t,
                                     when=at)
                       for tower in towers
                       for px in responder_pixels(r))
        return out

    # -- the answer -------------------------------------------------------

    def _answer_beat(self, index: int) -> float:
        """The grid beat of the index-th answer beat since Start."""
        passes, i = divmod(index, len(self._answers))
        return passes * self._loop_beats + self._answers[i]

    def _judge_tap(self, dev: str, at: float) -> None:
        """Give `dev` the points of the nearest open answer beat it has not
        taken. `at` is presentation time, a horizon ahead of the tap's own
        moment, while the grid is what is presented. `beat` is that moment
        with JUDGE_DELAY_S off, so the grid can be judged as it stands."""
        grid_beat = self._seq.beat_at(at - self.cue_horizon)
        beat = self._seq.beat_at(at - self.cue_horizon - JUDGE_DELAY_S)
        passes = (beat + TAP_WINDOW_BEATS) // self._loop_beats
        closed_to = (self._answer_beat(self._answers_closed - 1)
                     if self._answers_closed else float("-inf"))
        grace = LATE_GRACE_S * self._seq.bpm / 60.0

        def error(target: float) -> float:
            return forgive_late(beat - target, grace)

        candidates = [
            target for target in (passes * self._loop_beats + a
                                  for a in self._answers)
            if target > closed_to
            and abs(error(target)) <= TAP_WINDOW_BEATS
            and dev not in self._open_taps.get(target, {})]
        if candidates:
            target = min(candidates, key=lambda t: abs(error(t)))
            points = tap_points(error(target))
            self._open_taps.setdefault(target, {})[dev] = points
            measure, in_measure = measure_and_beat(target)
            print(f"tap {dev}: measure {measure} beat {in_measure:g}, "
                  f"error {beat - target:+.3f} beats, points {points:.3f}",
                  flush=True)
        else:
            measure, in_measure = measure_and_beat(grid_beat)
            print(f"tap {dev}: at measure {measure} beat {in_measure:.3f}, "
                  f"took no beat", flush=True)

    def _close_answers(self, at: float) -> None:
        """Bank every answer beat whose window, with a late tap's slack, has
        passed by the real time behind presentation time `at`."""
        while self._answers:
            target = self._answer_beat(self._answers_closed)
            deadline = (self._seq.time_at(target + TAP_WINDOW_BEATS)
                        + JUDGE_DELAY_S + LATE_GRACE_S + JUDGE_SLACK_S)
            if deadline > at - self.cue_horizon:
                return
            for dev, points in self._open_taps.pop(target, {}).items():
                self._earned[dev] = self._earned.get(dev, 0.0) + points
            self._answers_closed += 1

    # -- the tap ----------------------------------------------------------

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """A shroom's tap blinks every shroom, on this tap's own time rather
        than the next tick, and once started is judged against the answer.
        A tap from anything else does nothing."""
        if dev not in self._shrooms:
            return []
        print(f"tap {dev}: received at o2 t={at - self.cue_horizon:.3f} s",
              flush=True)
        if self._started:
            self._judge_tap(dev, at)
        self._flash = (at, 255)
        return self._flash_cues(255, 0.0, at)

    def _flash_step(self, at: float) -> list:
        """The blink at its fade's level `at`, sent only when the level moved;
        the last step lapses dark at the fade's end."""
        if self._flash is None:
            return []
        start, sent = self._flash
        t = at - start
        value = fade_value(t, FLASH_FADE_S)
        if value == 0:
            self._flash = None
            return []
        if value == sent:
            return []
        self._flash = (start, value)
        return self._flash_cues(value, t, at)

    def _flash_cues(self, value: int, t: float, at: float) -> list:
        """Every LED of every shroom at `value`, held to the fade's end."""
        rgb = tuple(c * value // 255 for c in FLASH_RGB) + (0,)
        return [PixelFlashCue(dev, px, rgb, FLASH_FADE_S - t, when=at)
                for dev in sorted(self._shrooms)
                for px in range(FLASH_PIXELS)]

    # -- read-outs --------------------------------------------------------

    def score(self, dev: str) -> float:
        """`dev`'s mean points over the answer beats closed since Start."""
        if not self._answers_closed:
            return 0.0
        return self._earned.get(dev, 0.0) / self._answers_closed

    def status(self) -> dict:
        return {"piano": "playing" if self._started else "waiting",
                "tower": sorted(self._towers),
                "shrooms": sorted(self._shrooms),
                **{f"score {dev}": round(self.score(dev), 3)
                   for dev in sorted(self._shrooms)}}
