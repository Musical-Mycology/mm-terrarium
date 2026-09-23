"""A fake WLED: receive Art-Net ArtDmx on a UDP port, parse it strictly, and
reassemble one fixture's universes into frames -- for a full no-hardware run
of an [[artnet]] output (spec 2026-09-23 section 8.3).

Usage:
    python -m harness.artnet_listen --port 16454 --pixels 864
    python -m harness.artnet_listen --port 16454 --pixels 864 --websim

Point a terrarium.toml [[artnet]] entry at host = "127.0.0.1" and the same
port. Reports fps, sequence gaps and frame inter-arrival jitter once a
second; --websim also paints each frame on a luxaeterna WebSim canvas.
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

ARTNET_ID = b"Art-Net\x00"
OPCODE_DMX = 0x5000
_HEADER = 18
_DMX_CHANNELS = 512


class ArtNetParseError(ValueError):
    pass


def parse_artdmx(packet: bytes) -> tuple[int, int, bytes]:
    """(universe, sequence, payload). Strict: the ID, opcode, protocol
    version and the length field must all agree with the packet."""
    if len(packet) < _HEADER:
        raise ArtNetParseError(f"packet is {len(packet)} bytes, under the 18-byte header")
    if packet[:8] != ARTNET_ID:
        raise ArtNetParseError("not an Art-Net packet (bad ID)")
    (opcode,) = struct.unpack_from("<H", packet, 8)
    if opcode != OPCODE_DMX:
        raise ArtNetParseError(f"opcode 0x{opcode:04x} is not ArtDmx")
    (version,) = struct.unpack_from(">H", packet, 10)
    if version < 14:
        raise ArtNetParseError(f"protocol version {version} < 14")
    sequence = packet[12]
    (universe,) = struct.unpack_from("<H", packet, 14)
    (length,) = struct.unpack_from(">H", packet, 16)
    payload = packet[_HEADER:]
    if length != len(payload):
        raise ArtNetParseError(f"length field {length} != payload {len(payload)}")
    if length < 2 or length > _DMX_CHANNELS or length % 2:
        raise ArtNetParseError(f"payload length {length} invalid")
    return universe, sequence, bytes(payload)


class Reassembler:
    """Collect one span's universes and emit a frame once every universe has
    arrived since the last emit. Sequence gaps are counted across all
    packets: luxaeterna's ArtNet numbers every send(), not each universe."""

    def __init__(self, pixel_count: int, start_universe: int = 0,
                 channels_per_pixel: int = 4) -> None:
        self._channels = pixel_count * channels_per_pixel
        count = -(-self._channels // _DMX_CHANNELS)
        self.universes = list(range(start_universe, start_universe + count))
        self._parts: dict[int, bytes] = {}
        self._last_seq: int | None = None
        self.seq_gaps = 0

    def feed(self, universe: int, sequence: int, payload: bytes) -> bytes | None:
        if self._last_seq is not None and sequence != (self._last_seq + 1) % 256:
            self.seq_gaps += 1
        self._last_seq = sequence
        if universe not in self.universes:
            return None
        self._parts[universe] = payload.ljust(_DMX_CHANNELS, b"\0")
        if len(self._parts) < len(self.universes):
            return None
        frame = b"".join(self._parts[u] for u in self.universes)[:self._channels]
        self._parts = {}
        return frame


def _websim(pixel_count: int):
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import SurfaceCapability, Zone
    cap = SurfaceCapability(surface_id="artnet_listen", pixel_count=pixel_count,
                            color_order="RGBW",
                            zones=[Zone("primary", 0, pixel_count)])
    backend = WebSimBackend(capability=cap, serve=True, label="artnet_listen")
    backend.open()
    print(f"websim: http://127.0.0.1:{backend.port}/")
    return backend


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6454)
    parser.add_argument("--pixels", type=int, required=True)
    parser.add_argument("--start-universe", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="stop after this long (0 = until Ctrl-C)")
    parser.add_argument("--websim", action="store_true")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.5)
    r = Reassembler(args.pixels, args.start_universe)
    sim = _websim(args.pixels) if args.websim else None
    start = report = time.monotonic()
    arrivals: list[float] = []
    errors = 0
    try:
        while not args.seconds or time.monotonic() - start < args.seconds:
            try:
                packet = sock.recv(2048)
            except socket.timeout:
                packet = None
            if packet is not None:
                try:
                    frame = r.feed(*parse_artdmx(packet))
                except ArtNetParseError as exc:
                    errors += 1
                    print(f"bad packet: {exc}")
                    frame = None
                if frame is not None:
                    arrivals.append(time.monotonic())
                    if sim is not None:
                        sim.send(frame)
            now = time.monotonic()
            if now - report >= 1.0:
                gaps = sorted(b - a for a, b in zip(arrivals, arrivals[1:]))
                p50 = gaps[len(gaps) // 2] * 1000 if gaps else 0.0
                p99 = gaps[int(len(gaps) * 0.99)] * 1000 if gaps else 0.0
                print(f"fps {len(arrivals) / (now - report):.1f}  "
                      f"interval p50 {p50:.1f} ms p99 {p99:.1f} ms  "
                      f"seq gaps {r.seq_gaps}  bad packets {errors}")
                arrivals, report = arrivals[-1:], now
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        if sim is not None:
            sim.close()


if __name__ == "__main__":
    main()
