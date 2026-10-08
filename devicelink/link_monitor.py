"""Control's per-device view of a beat-capable link (spec
docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md
section 7). Pure: no transport, no clock of its own.

Only a device that has sent /game/beat has an entry; a legacy client
(plain 5 s hello) never appears here and the Console shows no link state
for it. "missing" is display only: the 15 s reap in GameServer.reap_stale
still decides when a slot is freed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from devicelink.contract import LINK_LOST_S

# How many recent beats the loss figure covers.
LOSS_WINDOW = 30


def bars(loss: float | None, rtt_ms: int | None) -> int | None:
    """Signal bars, 1 to 4, from loss and round trip. None until there is
    a loss figure. An unknown rtt counts as fast: loss alone decides."""
    if loss is None:
        return None
    rtt = rtt_ms or 0
    if loss <= 0.02 and rtt < 50:
        return 4
    if loss <= 0.05 and rtt < 100:
        return 3
    if loss <= 0.15 and rtt < 250:
        return 2
    return 1


@dataclass
class _Link:
    seqs: deque = field(default_factory=lambda: deque(maxlen=LOSS_WINDOW))
    rtt_ms: int | None = None


class LinkMonitor:
    def __init__(self, lost_after: float = LINK_LOST_S):
        self._lost_after = lost_after
        self._links: dict[str, _Link] = {}

    def on_beat(self, dev: str, seq: int, rtt_ms: int, now: float) -> None:
        """Record one beat. seq 0, or a seq older than the window, starts a
        new window (the device relinked); a seq at or below the newest one
        is a duplicate or reordered packet and is ignored."""
        link = self._links.setdefault(dev, _Link())
        if rtt_ms > 0:
            link.rtt_ms = rtt_ms
        seqs = link.seqs
        if seq == 0 or not seqs or seq < seqs[0]:
            seqs.clear()
            seqs.append(seq)
        elif seq > seqs[-1]:
            seqs.append(seq)

    def __len__(self) -> int:
        return len(self._links)

    def beats(self, dev: str) -> bool:
        return dev in self._links

    def _loss(self, link: _Link) -> float | None:
        seqs = link.seqs
        if len(seqs) < 2:
            return None
        span = seqs[-1] - seqs[0] + 1
        return max(0.0, 1.0 - len(seqs) / span)

    def view(self, dev: str, last_seen: float, now: float) -> dict | None:
        """The Console's link read-out for dev, or None for a device that
        never beat. last_seen is DevicePool's (any traffic counts)."""
        link = self._links.get(dev)
        if link is None:
            return None
        loss = self._loss(link)
        state = "missing" if now - last_seen > self._lost_after else "live"
        return {"state": state, "bars": bars(loss, link.rtt_ms),
                "rtt_ms": link.rtt_ms,
                "loss": None if loss is None else round(loss, 3)}

    def forget(self, dev: str) -> None:
        self._links.pop(dev, None)

    def clear(self) -> None:
        self._links.clear()
