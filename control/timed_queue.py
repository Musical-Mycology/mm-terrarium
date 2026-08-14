"""TimedQueue: hold a payload until its moment on the O2 clock.

Payload-generic on purpose. The two consumers hold different things:
Control holds (when, midi) and feeds a luxaeterna LightSession; a device
holds (when, frame) and lights its LEDs. Keeping the payload opaque is
what lets one module serve both, and what makes the later move to
device-side rendering a change of payload rather than a change of
scheduling. See docs/superpowers/specs/
2026-08-12-control-o2lite-and-timed-cues-design.md section 5.3.

Pure and stdlib-only: it runs in the offline suite and on a Radxa alike.
"""

from __future__ import annotations

from collections import deque

# Bound on retained lateness samples. This class runs on a Radxa in a
# long-lived installation, so an unbounded list of floats is a leak: at the
# 44 Hz render rate it would grow without limit for as long as the room is
# up. 20000 is ~7.5 minutes of frames -- far more than any measurement run
# needs, and small enough to ignore. Same reasoning as
# harness/shroom_client.py's _MAX_PENDING_FRAMES.
_MAX_LATENESS_SAMPLES = 20000


class TimedQueue:
    def __init__(self) -> None:
        self._items: list[tuple[float, int, object]] = []
        # Payloads released late because their time had already passed.
        # A rising count is the signal that BootConfig.cue_horizon is too
        # small -- see the design spec section 6.
        self.clamped = 0
        # The MAGNITUDE behind that counter, which push() otherwise computes
        # and throws away. `clamped` answers "is the horizon wrong?"; this
        # answers "by how much, and how often?" -- which is what picking a
        # horizon actually needs. See
        # docs/superpowers/specs/2026-08-14-cue-horizon-measurement-design.md.
        self.lateness: deque[float] = deque(maxlen=_MAX_LATENESS_SAMPLES)
        self._seq = 0

    def push(self, when: float | None, payload, now: float) -> None:
        """Queue `payload` for release at `when`.

        `when=None` means no time was declared: release at the next drain,
        and do NOT count it as a clamp. A `when` already in the past IS a
        clamp: it releases at the next drain and increments the counter.
        """
        if when is None:
            # No declared time means there is no deadline to be late for, so
            # this contributes no lateness sample either -- consistent with
            # it not counting as a clamp.
            due_at = now
        else:
            # SIGNED, deliberately: negative means the payload arrived before
            # its deadline, which is the healthy case and has to stay
            # distinguishable from an equally-sized overshoot. Recorded for
            # every timed payload, not just clamped ones -- sampling only the
            # late ones would measure the tail and call it the distribution.
            self.lateness.append(now - when)
            if when < now:
                self.clamped += 1
                due_at = now
            else:
                due_at = when
        # The sequence number keeps equal times in insertion order and, more
        # importantly, stops sort() from ever comparing two payloads -- they
        # are MIDI tuples on one side and dicts/frames on the other.
        self._items.append((due_at, self._seq, payload))
        self._seq += 1

    def due(self, now: float) -> list:
        """Release every payload whose time has arrived, in time order."""
        ready = [item for item in self._items if item[0] <= now]
        if not ready:
            return []
        self._items = [item for item in self._items if item[0] > now]
        ready.sort(key=lambda item: (item[0], item[1]))
        return [payload for (_, _, payload) in ready]

    def pending(self) -> int:
        """How many payloads are still waiting. Used by sync_bench and by
        teardown, which must not drop work still in flight."""
        return len(self._items)
