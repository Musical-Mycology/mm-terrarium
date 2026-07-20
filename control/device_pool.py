"""Tracks known devices across Bit lifecycles. See design spec section 4."""

from dataclasses import dataclass


@dataclass
class DeviceInfo:
    dev: str
    name: str
    protoversion: str


class DevicePool:
    """dev -> DeviceInfo, populated by /game/hello. Global to Control, not
    reset when a Bit unloads -- a released device stays in the joinable pool.
    """

    def __init__(self):
        self._devices: dict[str, DeviceInfo] = {}

    def hello(self, dev: str, name: str, protoversion: str) -> DeviceInfo:
        info = DeviceInfo(dev=dev, name=name, protoversion=protoversion)
        self._devices[dev] = info
        return info

    def known(self, dev: str) -> bool:
        return dev in self._devices

    def get(self, dev: str) -> DeviceInfo | None:
        return self._devices.get(dev)

    def __len__(self) -> int:
        return len(self._devices)
