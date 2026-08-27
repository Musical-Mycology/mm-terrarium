"""DeviceLink: the device-facing websocket transport for Control.

The inbound sibling of console/ -- same split (a socket-only server plus a
transport-agnostic agent driven from the tick loop), but its clients are
Testshrooms (and eventually real devices) speaking /game/* rather than
operators.
"""
