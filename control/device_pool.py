"""Tracks known devices across Bit lifecycles. See design spec section 4,
and docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md
sections 3-4 for last_seen/touch/stale/remove."""

from dataclasses import dataclass, field

from control.instrument import TUNESHROOM, Instrument


@dataclass
class DeviceInfo:
    dev: str
    name: str
    protoversion: str
    # The last time ANY traffic arrived from this dev -- not just hello.
    # See devicelink/agent.py's _handle(), which touches this on every
    # inbound message, and GameServer.reap_stale(), the only reader.
    last_seen: float = 0.0
    # The Instrument this device physically is. Every hello'd device is a
    # standard Tuneshroom today; this field is the seam for future kinds
    # (a fixed default rather than a per-hello parameter, since nothing yet
    # negotiates instrument kind at hello time).
    carried: Instrument = field(default=TUNESHROOM)


class DevicePool:
    """dev -> DeviceInfo, populated by /game/hello. Global to Control, not
    reset when a Bit unloads -- a released device stays in the joinable pool.

    A device is removed only by GameServer.reap_stale() finding it silent
    past a timeout (see the design spec above) -- there is still no
    graceful-release path that removes a DevicePool entry, exactly as
    before this liveness mechanism existed.
    """

    def __init__(self):
        self._devices: dict[str, DeviceInfo] = {}

    def hello(self, dev: str, name: str, protoversion: str,
             now: float = 0.0) -> DeviceInfo:
        info = DeviceInfo(dev=dev, name=name, protoversion=protoversion,
                          last_seen=now)
        self._devices[dev] = info
        return info

    def touch(self, dev: str, now: float) -> None:
        """Record proof of life for an already-known device. A no-op for an
        unknown dev, same tolerance known()/get() already have -- the first
        hello is what actually creates the entry, not this method."""
        info = self._devices.get(dev)
        if info is not None:
            info.last_seen = now

    def known(self, dev: str) -> bool:
        return dev in self._devices

    def get(self, dev: str) -> DeviceInfo | None:
        return self._devices.get(dev)

    def all(self) -> list[DeviceInfo]:
        """Every known device, insertion order -- the public view for the
        Terrarium Console snapshot. Returns a fresh list; mutating it does
        not affect the pool.
        """
        return list(self._devices.values())

    def stale(self, now: float, timeout: float) -> list[str]:
        """Dev ids whose last_seen is older than `timeout`. Pure query --
        mutates nothing. GameServer.reap_stale is the only caller that acts
        on the result."""
        return [dev for dev, info in self._devices.items()
                if now - info.last_seen > timeout]

    def clear(self) -> None:
        """Drop every known device. Called on unload_room -- every device's
        clock died with the hub (design spec section 6), so the whole pool
        is stale, not just the ones bound to the departed Room."""
        self._devices.clear()

    def remove(self, dev: str) -> None:
        """Drop the entry outright, not a tombstone -- a device that
        reconnects later says hello again and is indistinguishable from a
        first-time connection, which is correct: there is nothing to
        preserve about a device that was never cleanly released."""
        self._devices.pop(dev, None)

    def __len__(self) -> int:
        return len(self._devices)
