"""StrictFakeArtNet: luxaeterna's ArtNet backend, minus the socket, WITH its
strictness (boundary rule 5). It refuses exactly what ArtNet.send refuses:
a send before open(), and a payload that is odd or outside 2-512 bytes.
tests/test_artnet_sink.py's contract test holds it to the real class."""

from __future__ import annotations

from luxaeterna.exceptions import BackendError


class StrictFakeArtNet:
    def __init__(self, host: str = "255.255.255.255", port: int = 6454) -> None:
        self.host = host
        self.port = port
        self.is_open = False
        self.opens = 0
        self.closes = 0
        self.sent: list[tuple[int, bytes]] = []
        self.fail: Exception | None = None
        self.gate = None          # threading.Event: send() blocks until set

    def open(self) -> None:
        self.is_open = True
        self.opens += 1

    def close(self) -> None:
        self.is_open = False
        self.closes += 1

    def send(self, frame, universe_id: int = 0) -> None:
        if self.gate is not None:
            self.gate.wait(5.0)
        if not self.is_open:
            raise BackendError("Art-Net socket not open")
        n = len(frame)
        if n < 2 or n > 512:
            raise BackendError(f"Art-Net frame length {n} outside 2-512")
        if n % 2 != 0:
            raise BackendError(f"Art-Net frame length {n} must be even")
        if self.fail is not None:
            raise self.fail
        self.sent.append((universe_id, bytes(frame)))
