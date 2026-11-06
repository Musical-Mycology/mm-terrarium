"""BeatsBit: a devtower and up to three tuneshrooms connect, a Console button
starts.

Devices are told apart by their dev id, as in MinigameBit: one devtower<N>
takes the `tower` role and three ie<N> take the `shroom` role. Anything else,
or one more than those capacities, falls through to its own solo role.

There are SHROOMS (3) players, and the Console has one button per player
(Bit.actions) choosing which seated shroom it is, labelled with that shroom:
"Player 1: ie3", or "Player 1: -" while none is. A shroom takes the first free
player as it connects and keeps it across a reconnect, so by default the first
to connect is player1. Pressing a player's button moves that player on to the
next seated shroom in connection order, and if that shroom was another player
the two swap. Each tap's printed lines name its player, and status() shows
which device each player is. A seated shroom that is no player (one connecting
while every player is taken) stays seated, so a button can bring it in with no
reconnect, but its taps do nothing and it neither blinks nor scores. The
players are fixed once Start is pressed, since a score is a mean over every
answer beat since Start.

The Console's Music button (Bit.actions) chooses what plays, one of MUSICS:
"Music: music1" (piano.mid) until pressed, and each press moves on to the next,
until Start fixes it. The Start button starts the music's first track, and the
rest come in one at a time, in MUSICS order, as the players' shared progress
grows: of a music's n tracks, track k (from 0) comes in once progress reaches
k/n of PROGRESS_FULL, rounded up (track_threshold), so music2's seven enter at
0, 8, 15, 22, 29, 36 and 43 of 50, and a one-track music plays whole from Start.
A track comes in on the scoring tap's own time, and once in it loops on the
shared grid until the Bit is unloaded, however many of those devices are
connected. Each track repeats at its own length, all on Start's beat 0 whenever
it came in, so music2's 16-beat hi-hat plays twice through each pass of its
32-beat kick. A MusicCue is Room audio only (DeviceLinkAgent._on_music_cue), so the
music sounds from the MIDITEST Room's flsyn, not from the devtower's own
speaker. Its parts are the same whichever music is chosen (PART_PROGRAMS),
since the Room's instruments are fixed when the Bit loads: the files carry no
programs of their own.

While the music plays, beats1.mid is the rhythm for the first measure and
beats2.mid for the third of every CYCLE_BEATS (four measures). Each of those beats
lights one of the tower's RESPONDERS (4), chosen by where the beat falls in
its measure: 1 to 1.99 the first responder, 2 to 2.99 the second, 3 to 3.99
the third and 4 to 4.99 the fourth. A responder starts at RESPONDER_RGB on its
beat and fades to dark over RESPONDER_FADE_S, stepped each tick by fires() as
in MinigameBit; a beat on a responder still fading restarts it from full.
Every responder runs the light delay behind the grid, in seconds whatever the
tempo, so that its light meets the music coming out of the speaker: Arco takes
no timestamps, so the sound cannot be sent early, only the light held back.
The delay is the mean tap delay CalibrationBit last saved in Bit.carried, or
LIGHT_DELAY_S if nothing has been calibrated since the Terrarium started.

The tower's frame pixels are BAR_PIXELS (32) of bar, then the responders'
RESPONDER_PIXELS (24) each, bottom first. The tower role declares no light of
its own, so the frame is dark under the PixelFlashCue paint.

The bar is the players' shared progress, 0 to PROGRESS_FULL (50): a tap earning
more than PROGRESS_MIN_POINTS on its answer beat adds 1, whichever shroom made
it, painted at once. The bar's BAR_LIGHTS (16) two-LED lights fill white from
the bottom, the top one at a partial level, so every point shows. PixelFlashCue
paint lapses, so fires() repaints it every BAR_REFRESH_S, which also picks up a
tower that joins late. Nothing happens at full: the bar stays full and the music
plays on.

A tap on any player's shroom blinks every player's shroom: FLASH_RGB at full on
all FLASH_PIXELS, fading to dark over FLASH_FADE_S, painted with PixelFlashCue
and stepped by fires() the same way. It needs no Start, and a tap mid-fade
restarts the blink from full. A tap from any other device does nothing.

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

The music's timing lives in control.sequencer.Sequencer.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from control.bit import MEAN_TAP_DELAY_S, Bit
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
SHROOMS = 3                     # seats, and players
TOWERS = 1

# The music files carry no tempo, so every music plays at this; as DeuxAraDropBit.
BPM = 130.0

# The Room's flsyn parts, list index = part, for every music. A track's notes
# go out on its part whatever channel the file used. The drum part is the
# General MIDI kit, which takes no program.
PIANO_PART = 0
DRUM_PART = 1
SYNTH_BASS_PART = 2
SAW_PAD_PART = 3
AG_BASS_PART = 4
# General MIDI programs, numbered from 0, or None for the drum kit. Only
# piano's comes from an earlier Bit; the rest are chosen from the files' names.
PART_PROGRAMS = (0,             # acoustic grand
                 None,          # the drum kit
                 38,            # synth bass 1, for "Humonger Bass"
                 90,            # pad 3 (polysynth), for "Basic Saw Pad"
                 32)            # acoustic bass, for "AG Bass"


class Track(NamedTuple):
    """One file of a music: its asset, its part, and how many beats it loops
    on, when its last note-off runs past its final bar line (otherwise the
    Sequencer rounds its end up to a bar)."""
    asset: str
    part: int
    loop_beats: float | None = None


# The Console's music choices, the first until pressed. Each one's tracks come
# in in this order as progress grows (track_threshold), the first at Start. The asset names are bit.toml's [assets].
MUSICS = {
    "music1": (Track("piano", PIANO_PART),),
    "music2": (Track("bass_kick", DRUM_PART),
               Track("hihat", DRUM_PART),
               Track("humonger_bass", SYNTH_BASS_PART, loop_beats=32.0),
               Track("hihat_x2", DRUM_PART),
               Track("saw_pad", SAW_PAD_PART),
               Track("ag_bass", AG_BASS_PART, loop_beats=16.0),
               Track("melt_kit", DRUM_PART)),
}

BEATS_PER_MEASURE = 4
# The call and response repeats every four measures, whatever the music.
CYCLE_BEATS = 4 * BEATS_PER_MEASURE
# (asset, measure of the cycle it plays in, counted from 1). Each asset is
# one measure long, and its own tempo is ignored: it runs on BPM.
BEAT_CLIPS = (("beats1", 1), ("beats2", 3))

BAR_PIXELS = 32                 # the Dev Tower's bar LEDs, ahead of the responders
BAR_LIGHT_PIXELS = 2            # LEDs that light up as one
BAR_LIGHTS = BAR_PIXELS // BAR_LIGHT_PIXELS     # 16
BAR_REFRESH_S = 0.5             # how often the bar's paint is renewed
BAR_HOLD_S = 1.0                # the paint outlasts the gap to its renewal
PROGRESS_FULL = 50              # the bar's full score
PROGRESS_MIN_POINTS = 0.1       # a tap must earn more than this to add 1
RESPONDERS = BEATS_PER_MEASURE  # one per beat of the measure, bottom first
RESPONDER_PIXELS = 24           # LEDs a responder light
RESPONDER_FADE_S = 0.25         # a responder's fade, full to dark
RESPONDER_RGB = (64, 48, 32)    # MinigameBit's half-bright beige
# How far a responder's light is held behind its beat until CalibrationBit has
# measured it. Raise it if the light still leads the sound, lower it if the
# light trails. Not measured: it is JUDGE_DELAY_S's hand-set audio delay less
# the one tick (~23 ms) a light cue already loses between fires() and the next
# render. It depends on the box's audio output, so tune it on the machine that
# plays the Room.
LIGHT_DELAY_S = 0.175

TAP_WINDOW_BEATS = 0.25         # either side of an answer beat
LATE_GRACE_S = 0.020            # how late a tap still counts as dead on
JUDGE_DELAY_S = 0.200           # the judgment's slide behind the grid
JUDGE_SLACK_S = 0.050           # a late tap's trip, before a beat closes

FLASH_PIXELS = 12               # a tuneshroom's LEDs (8 ring + 4 stem)
FLASH_FADE_S = 0.2              # a tap's blink, full to dark
FLASH_RGB = (128, 0, 255)       # MinigameBit's tap purple


def track_threshold(k: int, tracks: int) -> int:
    """The progress at which track k (from 0) of a music of `tracks` comes in:
    k/tracks of PROGRESS_FULL, rounded up, so the first is in from Start."""
    return -(-k * PROGRESS_FULL // tracks)


def responder_pixels(r: int) -> range:
    """Responder r's pixels, after the bar: 32 + 24r..32 + 24r + 23."""
    start = BAR_PIXELS + r * RESPONDER_PIXELS
    return range(start, start + RESPONDER_PIXELS)


def bar_light_value(light: int, progress: int) -> int:
    """Bar light `light`'s W level, 0..255, at `progress`, bottom first."""
    filled = progress * BAR_LIGHTS / PROGRESS_FULL - light
    return round(min(max(filled, 0.0), 1.0) * 255)


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
        for tracks in MUSICS.values():
            for track in tracks:
                self._seq.add_track(
                    track.asset,
                    read_midi_file(self.config.asset_path(track.asset)),
                    part=track.part, loop=True, loop_beats=track.loop_beats)
        self._music = next(iter(MUSICS))    # what Start plays
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
        self._progress = 0              # the players' shared bar score
        self._tracks_in = 0             # the chosen music's tracks playing
        self._bar_refresh_at = 0.0      # when fires() next repaints the bar
        # Responders mid-fade: r -> (its beat, last level sent).
        self._responders: dict[int, tuple[float, int]] = {}
        # The shrooms' blink under way: (its tap, last level sent), or None.
        self._flash: tuple[float, int] | None = None
        self._shrooms: list[str] = []   # seated, in the order they connected
        # Player n's shroom at index n - 1, or None while it has none.
        self._seats: list[str | None] = [None] * SHROOMS
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
        volume = {"source": "cc:7", "dest": "cc:7"}
        ugen = {"instruments": [
            {"instrument": "flsyn", "drums": True, "lanes": [volume]}
            if program is None else
            {"instrument": "flsyn", "program": program, "lanes": [volume]}
            for program in PART_PROGRAMS
        ]}
        return {}, ugen

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def actions(self) -> dict:
        players = {f"player{n}": (f"Player {n}: {dev or '-'}",
                                  lambda at, seat=n - 1: self._on_player(seat))
                   for n, dev in enumerate(self._seats, start=1)}
        return {**players,
                "music": (f"Music: {self._music}", self._on_music),
                "start": ("Start", self._on_start)}

    def admits(self, dev: str, role_name: str) -> bool:
        if role_name == "shroom":
            return SHROOM_DEV_RE.fullmatch(dev) is not None
        if role_name == "tower":
            return TOWER_DEV_RE.fullmatch(dev) is not None
        return True

    def on_join(self, dev: str, role_name: str) -> None:
        self._towers.discard(dev)
        if role_name == "shroom":
            if dev not in self._shrooms:
                self._shrooms.append(dev)
            if dev not in self._seats and None in self._seats:
                self._seats[self._seats.index(None)] = dev
            return
        if dev in self._shrooms:
            self._shrooms.remove(dev)
            self._seats = [None if d == dev else d for d in self._seats]
        if role_name == "tower":
            self._towers.add(dev)

    def _playing(self) -> list[str]:
        """The shrooms that play, player1 first."""
        return [dev for dev in self._seats if dev is not None]

    def _tap_source(self, dev: str) -> str:
        """Who a playing shroom's tap is from, as printed: "player1 (ie3)"."""
        return f"player{self._seats.index(dev) + 1} ({dev})"

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
        return (out + self._seq.advance(at) + self._responder_steps(at)
                + self._bar_refresh(at))

    # -- the buttons ------------------------------------------------------

    def _on_player(self, seat: int) -> list | str:
        """Move player seat + 1 on to the next seated shroom in connection
        order, swapping with whichever player that shroom was, until Start
        fixes the players."""
        if self._started:
            return "already started"
        if not self._shrooms:
            return "no shroom connected"
        current = self._seats[seat]
        if current is None:
            chosen = self._shrooms[0]
        else:
            i = self._shrooms.index(current)
            chosen = self._shrooms[(i + 1) % len(self._shrooms)]
        if chosen in self._seats:
            self._seats[self._seats.index(chosen)] = current
        self._seats[seat] = chosen
        return []

    def _on_music(self, at: float) -> list | str:
        """Move on to the next of MUSICS, until Start fixes it."""
        if self._started:
            return "already started"
        names = list(MUSICS)
        self._music = names[(names.index(self._music) + 1) % len(names)]
        return []

    def _on_start(self, at: float) -> list | str:
        """Grid beat 0 at `at`, the melodic programs restored (the lobby
        reprograms part 0), every track of the chosen music in. Its first
        notes go out here rather than on the next tick, as does any light
        already due; beat 0's light comes the light delay later, from
        fires()."""
        if self._started:
            return "already started"
        self._started = True
        self._seq.start(at)
        out: list = [MusicCue(ROOM, 0xC0 | part, program, 0, when=at)
                     for part, program in enumerate(PART_PROGRAMS)
                     if program is not None]
        out += self._enable_due_tracks(at)
        out += self._seq.advance(at)
        self._strike_due_beats(at)
        out += self._responder_steps(at)
        return out

    def _enable_due_tracks(self, at: float) -> list:
        """Bring in, at `at`, every track of the chosen music whose
        track_threshold the progress has reached, in order."""
        tracks = MUSICS[self._music]
        out: list = []
        while (self._tracks_in < len(tracks)
               and self._progress >= track_threshold(self._tracks_in,
                                                     len(tracks))):
            track = tracks[self._tracks_in]
            self._tracks_in += 1
            print(f"track {self._tracks_in}/{len(tracks)} in: {track.asset}",
                  flush=True)
            out += self._seq.enable(track.asset, at)
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
        """One cycle's beats, in order: (cycle beat, the responder it
        lights). A clip's beats count from 0, so the whole part of one names
        its responder."""
        pattern = []
        for asset, measure in BEAT_CLIPS:
            offset = (measure - 1) * BEATS_PER_MEASURE
            for e in read_midi_file(self.config.asset_path(asset)).events:
                if e.status & 0xF0 == 0x90:
                    pattern.append((offset + e.beat, int(e.beat)))
        return sorted(pattern)

    def _light_delay(self) -> float:
        """Seconds a responder's light is held behind its beat: calibration's
        saved mean, else LIGHT_DELAY_S."""
        return self.carried.get(MEAN_TAP_DELAY_S, LIGHT_DELAY_S)

    def _strike_due_beats(self, at: float) -> None:
        """Start the fade of every beat's responder whose light, the light
        delay behind the beat, is reached by `at`."""
        while self._beats:
            passes, i = divmod(self._beats_done, len(self._beats))
            loop_beat, responder = self._beats[i]
            beat_at = (self._seq.time_at(passes * CYCLE_BEATS + loop_beat)
                       + self._light_delay())
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
        return passes * CYCLE_BEATS + self._answers[i]

    def _judge_tap(self, dev: str, at: float) -> None:
        """Give `dev` the points of the nearest open answer beat it has not
        taken. `at` is presentation time, a horizon ahead of the tap's own
        moment, while the grid is what is presented. `beat` is that moment
        with JUDGE_DELAY_S off, so the grid can be judged as it stands."""
        grid_beat = self._seq.beat_at(at - self.cue_horizon)
        beat = self._seq.beat_at(at - self.cue_horizon - JUDGE_DELAY_S)
        passes = (beat + TAP_WINDOW_BEATS) // CYCLE_BEATS
        closed_to = (self._answer_beat(self._answers_closed - 1)
                     if self._answers_closed else float("-inf"))
        grace = LATE_GRACE_S * self._seq.bpm / 60.0

        def error(target: float) -> float:
            return forgive_late(beat - target, grace)

        candidates = [
            target for target in (passes * CYCLE_BEATS + a
                                  for a in self._answers)
            if target > closed_to
            and abs(error(target)) <= TAP_WINDOW_BEATS
            and dev not in self._open_taps.get(target, {})]
        if candidates:
            target = min(candidates, key=lambda t: abs(error(t)))
            points = tap_points(error(target))
            self._open_taps.setdefault(target, {})[dev] = points
            measure, in_measure = measure_and_beat(target)
            print(f"tap {self._tap_source(dev)}: "
                  f"measure {measure} beat {in_measure:g}, "
                  f"error {beat - target:+.3f} beats, points {points:.3f}",
                  flush=True)
            if (points > PROGRESS_MIN_POINTS
                    and self._progress < PROGRESS_FULL):
                self._progress += 1
                print(f"progress {self._progress}/{PROGRESS_FULL}", flush=True)
        else:
            measure, in_measure = measure_and_beat(grid_beat)
            print(f"tap {self._tap_source(dev)}: "
                  f"at measure {measure} beat {in_measure:.3f}, "
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
        """A player's tap blinks every player, on this tap's own time rather
        than the next tick, and once started is judged against the answer.
        A tap from anything else does nothing."""
        if dev not in self._playing():
            return []
        print(f"tap {self._tap_source(dev)}: "
              f"received at o2 t={at - self.cue_horizon:.3f} s", flush=True)
        progress = self._progress
        if self._started:
            self._judge_tap(dev, at)
        self._flash = (at, 255)
        out = self._flash_cues(255, 0.0, at)
        if self._progress != progress:
            out += self._bar_cues(at)
            out += self._enable_due_tracks(at)
        return out

    # -- the bar ----------------------------------------------------------

    def _bar_cues(self, at: float) -> list:
        """Every lit bar pixel on every tower at its level for the progress."""
        return [PixelFlashCue(tower, px, (0, 0, 0, value), BAR_HOLD_S, when=at)
                for tower in sorted(self._towers)
                for light in range(BAR_LIGHTS)
                if (value := bar_light_value(light, self._progress))
                for px in range(light * BAR_LIGHT_PIXELS,
                                (light + 1) * BAR_LIGHT_PIXELS)]

    def _bar_refresh(self, at: float) -> list:
        """The bar's paint renewed every BAR_REFRESH_S, before it lapses."""
        if at < self._bar_refresh_at:
            return []
        self._bar_refresh_at = at + BAR_REFRESH_S
        return self._bar_cues(at)

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
        """Every LED of every player at `value`, held to the fade's end."""
        rgb = tuple(c * value // 255 for c in FLASH_RGB) + (0,)
        return [PixelFlashCue(dev, px, rgb, FLASH_FADE_S - t, when=at)
                for dev in sorted(self._playing())
                for px in range(FLASH_PIXELS)]

    # -- read-outs --------------------------------------------------------

    def score(self, dev: str) -> float:
        """`dev`'s mean points over the answer beats closed since Start."""
        if not self._answers_closed:
            return 0.0
        return self._earned.get(dev, 0.0) / self._answers_closed

    def status(self) -> dict:
        return {"music": self._music,
                "state": "playing" if self._started else "waiting",
                "tracks": f"{self._tracks_in}/{len(MUSICS[self._music])}",
                "tower": sorted(self._towers),
                "shrooms": sorted(self._shrooms),
                "light delay": f"{self._light_delay() * 1000:+.1f} ms",
                **{f"player{n}": dev
                   for n, dev in enumerate(self._seats, start=1)
                   if dev is not None},
                "progress": f"{self._progress}/{PROGRESS_FULL}",
                **{f"score {dev}": round(self.score(dev), 3)
                   for dev in sorted(self._playing())}}
