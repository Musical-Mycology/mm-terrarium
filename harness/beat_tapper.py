"""BeatTapper: the Testshroom's synthetic player for a call-and-response
Bit (MetronomeBit today).

A human hears the Room's four clicks and taps four back. A headless
Testshroom hears nothing, but it does SEE its own light: MetronomeBit
pulses every joined player's level on every beat (cc:11 60 -> 110, back
150 ms later). This class watches the brightness of the frames the device
actually displays, locks onto those pulses, and reports which beats to
tap on: the answer beats, index modulo `beats_per_cycle` at or past
`call_beats`.

Clock-free and socket-free: the caller passes each displayed frame with
the O2 time it was displayed at (harness/websim_leds.py's on_show hook,
driven from ShroomClient.tick), and sends the /game/tap itself when
observe() returns a beat index. The tap's stamp is therefore the display
tick's own clock reading -- at the source (Design Rule 4) -- which is
what makes a live run prove the whole loop: Control's cue, the device's
display, the tap, and the Bit's judgment against the same gridpoint.

Why a phase-lock and not a counter: fireworks (12 bloom flashes in 1.4 s
on a winning player) are rises too, and a failed player goes dark for a
whole cycle and sees no pulses at all. Indexing each accepted rise by
TIME against the locked grid survives both; counting rises survives
neither. The grid itself is the thing to protect: a flash near enough to
a gridpoint is taken AS that beat, but only a rise landing within
REFINE_FRACTION of its prediction is allowed to redefine the period.
"""

from __future__ import annotations

# A frame this much brighter than the last is a rise. MEASURED against real
# aurora frames (tests/test_metronome_bit_live_frames.py renders them): the
# renderer slews the cc:11 60 -> 110 pulse over ~150 ms, so at the 44 Hz
# frame rate the STEEPEST single-frame step of a real beat is only +11%
# (sum 1500 -> 1668 on a 36-channel frame, then 1.08, 1.07, 1.05, 1.03 as
# it climbs to 2280). Between beats the surface decays, 0.8% to 1.5% per
# frame. 0.05 sits between the two with room on both sides. The old 0.25
# was calibrated on the synthetic step frames of tests/test_beat_tapper.py
# and saw no beat at all in a live run.
RISE_RATIO = 0.05
# Rises closer together than this are the same slewed pulse, not two beats.
# The ramp above spans ~150 ms and produces a rise on every frame of it;
# the shortest beat this class will lock to is MIN_PERIOD_S (0.2 s), so
# anything in between separates the two unambiguously. Measured in TIME,
# not in consecutive frames: the renderer sends nothing while the surface
# is unchanged, so "the previous frame" can be a third of a second ago.
RISE_GAP_S = 0.18
DARK_SUM_FRACTION = 0.05   # below this fraction of full-white, a frame is dark
ACCEPT_FRACTION = 0.2      # a rise within this fraction of a period of a
                           # predicted beat IS that beat
# ...but only a rise within THIS fraction of it may redefine the grid.
# MEASURED (runs/20260908-221609): a real beat lands 1 to 8 ms off its
# prediction, ~1% of a 0.75 s period, while a bloom flash from the fireworks
# a winning player gets lands ~110 ms off (15%) and is still close enough to
# be taken as that beat. Refining from the flash moved the period 0.7515 ->
# 0.7378 -> 0.7227 in two flashes and every later beat fell outside
# ACCEPT_FRACTION: the device tapped beats 4-7 and then nothing until 23.
REFINE_FRACTION = 0.05
MIN_PERIOD_S = 0.2
MAX_PERIOD_S = 2.0


class BeatTapper:
    def __init__(self, beats_per_cycle: int = 8, call_beats: int = 4) -> None:
        self.beats_per_cycle = beats_per_cycle
        self.call_beats = call_beats
        # Set by the caller once the granted role's `uses` lists `tap`. A
        # role property, not lock state, so reset() leaves it alone.
        self.armed = False
        # Cumulative across lobby rounds -- see reset().
        self.taps = 0
        self.reset()

    def reset(self) -> None:
        """Forget this round's beat grid. `taps` deliberately survives: it is
        the process-wide exit diagnostic harness/o2_shroom.py prints once at
        the end (`beat taps sent: N`), across every lobby round, exactly like
        ShroomClient's cumulative `clamped`/`lateness` across the same
        reset_for_lobby() seam."""
        self._prev_sum: int | None = None
        self._prev_channels: int = 0
        self._last_rise_at: float | None = None
        self._t_first: float | None = None
        self.period: float | None = None
        self._last_index: int = -1

    @property
    def locked(self) -> bool:
        return self.period is not None

    def observe(self, frame: bytes, now: float) -> int | None:
        """Record one displayed frame. Returns the beat index to tap on
        right now, or None."""
        total = sum(frame)
        prev, self._prev_sum = self._prev_sum, total
        self._prev_channels = len(frame)
        if prev is None:
            return None
        if total <= prev * (1.0 + RISE_RATIO):
            return None
        # One slewed pulse climbs over several frames and so trips the test
        # above several times; only its ONSET is the beat. Recorded whether
        # or not this rise is acted on, so a from-black rise cannot leave a
        # gap that lets its own continuation read as a fresh beat.
        continuation = (self._last_rise_at is not None
                        and now - self._last_rise_at <= RISE_GAP_S)
        self._last_rise_at = now
        if continuation:
            return None
        from_black = prev < DARK_SUM_FRACTION * 255 * len(frame)
        return self._rise(now, from_black)

    def _rise(self, now: float, from_black: bool) -> int | None:
        if self._t_first is None:
            if from_black:
                return None          # the grant lighting a black canvas
            self._t_first = now
            self._last_index = 0
            return None
        if self.period is None:
            if from_black:
                return None
            interval = now - self._t_first
            if interval < MIN_PERIOD_S:
                # Too soon to be the next beat: a stray flash between beats.
                # Keep the earlier rise as the anchor -- it is still the best
                # candidate for beat 0 -- rather than sliding onto the flash.
                return None
            if interval > MAX_PERIOD_S:
                self._t_first = now  # too long ago; restart from this rise
                self._last_index = 0
                return None
            self.period = interval
            self._last_index = 1
            return self._tap_if_answer(1)
        k = round((now - self._t_first) / self.period)
        if k <= self._last_index:
            return None              # already handled this beat
        predicted = self._t_first + k * self.period
        error = abs(now - predicted)
        if error > ACCEPT_FRACTION * self.period:
            return None              # a flash, not a beat
        if error <= REFINE_FRACTION * self.period:
            # Close enough to be the beat itself, not something riding on
            # it, so it is safe to take the grid from here.
            self.period = (now - self._t_first) / k
        self._last_index = k
        return self._tap_if_answer(k)

    def _tap_if_answer(self, k: int) -> int | None:
        if k % self.beats_per_cycle < self.call_beats:
            return None
        self.taps += 1
        return k
