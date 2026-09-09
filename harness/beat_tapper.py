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
neither.
"""

from __future__ import annotations

RISE_RATIO = 0.25          # a frame this much brighter than the last is a rise
DARK_SUM_FRACTION = 0.05   # below this fraction of full-white, a frame is dark
ACCEPT_FRACTION = 0.2      # a rise within this fraction of a period of a
                           # predicted beat IS that beat
MIN_PERIOD_S = 0.2
MAX_PERIOD_S = 2.0


class BeatTapper:
    def __init__(self, beats_per_cycle: int = 8, call_beats: int = 4) -> None:
        self.beats_per_cycle = beats_per_cycle
        self.call_beats = call_beats
        # Set by the caller once the granted role's `uses` lists `tap`. A
        # role property, not lock state, so reset() leaves it alone.
        self.armed = False
        self.reset()

    def reset(self) -> None:
        self._prev_sum: int | None = None
        self._prev_channels: int = 0
        self._t_first: float | None = None
        self.period: float | None = None
        self._last_index: int = -1
        self.taps = 0

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
            if not (MIN_PERIOD_S <= interval <= MAX_PERIOD_S):
                self._t_first = now  # restart from this rise
                self._last_index = 0
                return None
            self.period = interval
            self._last_index = 1
            return self._tap_if_answer(1)
        k = round((now - self._t_first) / self.period)
        if k <= self._last_index:
            return None              # already handled this beat
        predicted = self._t_first + k * self.period
        if abs(now - predicted) > ACCEPT_FRACTION * self.period:
            return None              # a flash, not a beat
        self.period = (now - self._t_first) / k
        self._last_index = k
        return self._tap_if_answer(k)

    def _tap_if_answer(self, k: int) -> int | None:
        if k % self.beats_per_cycle < self.call_beats:
            return None
        self.taps += 1
        return k
