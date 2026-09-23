"""The fake WLED: strict ArtDmx parsing, span reassembly, and a real
sink -> real ArtNet -> localhost receiver run. No network beyond 127.0.0.1."""

import socket
import time

import pytest

pytest.importorskip("luxaeterna")

from luxaeterna.backends.artnet import ArtNet
from luxaeterna.power import PowerBudget

from devicelink.artnet_sink import ArtNetFixtureSink
from harness.artnet_listen import ArtNetParseError, Reassembler, parse_artdmx


def _packet(payload=bytes(512), universe=3):
    return ArtNet(host="127.0.0.1")._build_packet(bytearray(payload), universe)


def test_parse_reads_what_the_real_backend_builds():
    universe, _seq, payload = parse_artdmx(_packet(bytes([7]) * 512, universe=5))
    assert universe == 5 and payload == bytes([7]) * 512


@pytest.mark.parametrize("mutate", [
    lambda p: b"Art-Nex\x00" + p[8:],                 # bad ID
    lambda p: p[:8] + b"\x00\x21" + p[10:],           # wrong opcode
    lambda p: p[:-2],                                 # length field lies
    lambda p: p[:17],                                 # truncated header
])
def test_parse_is_strict(mutate):
    with pytest.raises(ArtNetParseError):
        parse_artdmx(mutate(_packet()))


def test_reassembly_emits_once_every_universe_arrived():
    r = Reassembler(pixel_count=200, start_universe=2)   # 800 ch -> universes 2, 3
    assert r.feed(2, 1, bytes([1]) * 512) is None
    frame = r.feed(3, 2, bytes([2]) * 512)
    assert frame == bytes([1]) * 512 + bytes([2]) * 288
    assert r.feed(9, 3, bytes(512)) is None              # not in the span


def test_sequence_gaps_are_counted():
    r = Reassembler(pixel_count=128)
    r.feed(0, 1, bytes(512))
    r.feed(0, 3, bytes(512))
    assert r.seq_gaps == 1


def test_a_sink_frame_reaches_a_localhost_receiver_intact():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    sink = ArtNetFixtureSink(
        name="e2e", pixel_count=864, start_universe=0,
        budget=PowerBudget(max_amps=10.0),
        backend=ArtNet(host="127.0.0.1", port=rx.getsockname()[1]),
        clock=time.monotonic)
    frame = bytes(i % 100 for i in range(864 * 4))      # under the 117 ceiling
    sink.start()
    try:
        sink.send_frame(frame, when=time.monotonic())
        r = Reassembler(pixel_count=864)
        got = None
        while got is None:
            universe, seq, payload = parse_artdmx(rx.recv(1024))
            got = r.feed(universe, seq, payload)
        assert got == frame
    finally:
        sink.close()
        rx.close()
