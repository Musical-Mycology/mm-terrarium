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


class TimedQueue:
    def __init__(self) -> None:
        self._items: list[tuple[float, int, object]] = []
        # Payloads released late because their time had already passed.
        # A rising count is the signal that BootConfig.cue_horizon is too
        # small -- see the design spec section 6.
        self.clamped = 0
        self._seq = 0

    def push(self, when: float | None, payload, now: float) -> None:
        """Queue `payload` for release at `when`.

        `when=None` means no time was declared: release at the next drain,
        and do NOT count it as a clamp. A `when` already in the past IS a
        clamp: it releases at the next drain and increments the counter.
        """
        if when is None:
            due_at = now
        elif when < now:
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
