"""DeviceLink: Control's device-facing transport, on o2lite.

The inbound sibling of console/ -- the same split (a transport-only
object plus a transport-agnostic agent driven from the tick loop), but
its clients are Testshrooms and real devices speaking /game/* over the
Arco hub rather than operators. devicelink/o2_transport.py is the
transport; devicelink/protocol.py is the wire vocabulary.
"""
