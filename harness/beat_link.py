"""The device side of the beat heartbeat (spec
docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md
section 6.1). Pure: no sockets and no clock of its own. The caller feeds
it the device's LOCAL time in seconds (never O2 time, which goes away
during a relink) and carries out the actions it returns.

harness/o2_shroom.py drives it over real o2lite, and
contract_kit/recorder.py drives it with jitter 0 to record the beat
scenarios. mm-devshroom firmware and the mm-tuneshroom app port this
state machine; this file is the reference they are checked against.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from devicelink.contract import (BEAT_INTERVAL_S, BEAT_JITTER_S, GRACE_S,
                                 HELLO_INTERVAL_S, LINK_LOST_S)

DOWN = "down"          # no transport, never linked: display untouched
LINKING = "linking"    # transport up, no reply yet on this link
LINKED = "linked"      # a reply arrived on this link
LOOKING = "looking"    # lost: show the white Looking pulse
SOLO = "solo"          # lost past the grace window: show Solo

_SEQ_WRAP = 2 ** 31
_RTT_MEMORY = 32       # how many unanswered seqs keep a send time


@dataclass(frozen=True)
class SendHello:
    pass


@dataclass(frozen=True)
class SendBeat:
    seq: int
    rtt_ms: int


@dataclass(frozen=True)
class DropTransport:
    pass


@dataclass(frozen=True)
class DropRole:
    pass


@dataclass(frozen=True)
class StateChanged:
    state: str


class BeatLink:
    def __init__(self, *, interval: float = BEAT_INTERVAL_S,
                 jitter: float = BEAT_JITTER_S,
                 lost_after: float = LINK_LOST_S, grace: float = GRACE_S,
                 legacy_hello: float = HELLO_INTERVAL_S,
                 rng: random.Random | None = None):
        self._interval = interval
        self._jitter = jitter
        self._lost_after = lost_after
        self._grace = grace
        self._legacy_hello = legacy_hello
        self._rng = rng or random.Random()
        self.state = DOWN
        self.armed = False
        self.epoch: str | None = None
        self.rtt_ms = 0
        self._up = False
        self._seq = 0
        self._sent_at: dict[int, float] = {}
        self._next_beat: float | None = None
        self._next_hello: float | None = None
        self._last_heard: float | None = None
        self._lost_since: float | None = None

    # --- helpers -----------------------------------------------------------

    def _gap(self) -> float:
        if self._jitter <= 0:
            return self._interval
        return self._interval + self._rng.uniform(-self._jitter, self._jitter)

    def _set(self, state: str, out: list) -> None:
        if state != self.state:
            self.state = state
            out.append(StateChanged(state))

    def _beat(self, now: float) -> SendBeat:
        seq = self._seq
        self._sent_at[seq] = now
        if len(self._sent_at) > _RTT_MEMORY:
            del self._sent_at[min(self._sent_at)]
        self._seq = (self._seq + 1) % _SEQ_WRAP
        return SendBeat(seq, self.rtt_ms)

    @staticmethod
    def _advance(due: float, step: float, now: float) -> float:
        nxt = due + step
        return nxt if nxt > now else now + step

    # --- inputs ------------------------------------------------------------

    def link_up(self, now: float) -> list:
        """The transport came up: hello, then beat 0. The display stays in
        LOOKING or SOLO until a reply proves Control is there."""
        out: list = [SendHello()]
        self._up = True
        self.armed = False
        self._seq = 0
        self._sent_at.clear()
        out.append(self._beat(now))
        self._next_beat = now + self._gap()
        self._next_hello = now + self._legacy_hello
        self._last_heard = now
        if self.state == DOWN:
            self._set(LINKING, out)
        return out

    def link_down(self, now: float) -> list:
        """The transport dropped from below (a socket error)."""
        out: list = []
        self._up = False
        self._next_beat = self._next_hello = None
        if self.state == LINKED:
            self._lost_since = now
            self._set(LOOKING, out)
        elif self.state == LINKING:
            self._set(DOWN, out)
        self.armed = False
        return out

    def on_control_message(self, now: float) -> None:
        """Any down message counts as proof of life, not only a beat
        reply: a /leds frame or a /role proves Control just as well."""
        if self._up:
            self._last_heard = now

    def on_beat_reply(self, now: float, seq: int, epoch: str) -> list:
        out: list = []
        if not self._up:
            return out
        self.on_control_message(now)
        sent = self._sent_at.pop(seq, None)
        if sent is not None:
            self.rtt_ms = max(0, round((now - sent) * 1000))
        if self.epoch is not None and epoch != self.epoch:
            # Control restarted and holds nothing for this device.
            out += [DropRole(), SendHello()]
        self.epoch = epoch
        self.armed = True
        self._lost_since = None
        self._set(LINKED, out)
        return out

    def tick(self, now: float) -> list:
        out: list = []
        if self._up:
            if self._next_beat is not None and now >= self._next_beat:
                out.append(self._beat(now))
                self._next_beat = self._advance(self._next_beat,
                                                self._gap(), now)
            if (not self.armed and self._next_hello is not None
                    and now >= self._next_hello):
                out.append(SendHello())
                self._next_hello = self._advance(self._next_hello,
                                                 self._legacy_hello, now)
            if (self.armed and self.state == LINKED
                    and now - self._last_heard >= self._lost_after):
                self._lost_since = self._last_heard
                self._up = False
                self.armed = False
                self._next_beat = self._next_hello = None
                out.append(DropTransport())
                self._set(LOOKING, out)
        if (self.state == LOOKING and self._lost_since is not None
                and now - self._lost_since >= self._grace):
            self._set(SOLO, out)
        return out

    def next_due(self) -> float | None:
        """The earliest time tick() has something to do, for a caller
        that steps time (contract_kit/recorder.py)."""
        due = []
        if self._up and self._next_beat is not None:
            due.append(self._next_beat)
        if self._up and not self.armed and self._next_hello is not None:
            due.append(self._next_hello)
        if self._up and self.armed and self.state == LINKED:
            due.append(self._last_heard + self._lost_after)
        if self.state == LOOKING and self._lost_since is not None:
            due.append(self._lost_since + self._grace)
        return min(due) if due else None
