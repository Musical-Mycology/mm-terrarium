"""SamplePlayer: preloaded local playback with dispatch-latency accounting."""

from __future__ import annotations

import pytest

from harness.local_sample import SamplePlayer

PATHS = {"tap": "/tmp/tap.wav"}


class FakeSink:
    def __init__(self) -> None:
        self.played: list[tuple[str, int]] = []

    def write(self, name: str, data: bytes) -> None:
        self.played.append((name, len(data)))


def loader(_path: str) -> bytes:
    return b"\x00" * 128


def player(paths=None, **kw) -> SamplePlayer:
    # `paths or PATHS` would be wrong: an empty dict is falsy and is a case
    # these tests deliberately exercise.
    kw.setdefault("loader", loader)
    return SamplePlayer(PATHS if paths is None else paths,
                        sink=FakeSink(), **kw)


# --- preload ---

def test_preload_reads_every_sample_into_memory():
    p = player()
    p.preload()
    assert p.is_preloaded is True


def test_not_preloaded_before_preload_is_called():
    assert player().is_preloaded is False


def test_preload_is_idempotent():
    calls: list[str] = []

    def counting_loader(path: str) -> bytes:
        calls.append(path)
        return b"\x00" * 128

    p = player(loader=counting_loader)
    p.preload()
    p.preload()
    assert len(calls) == 1


def test_preload_loads_every_declared_sample():
    calls: list[str] = []

    def counting_loader(path: str) -> bytes:
        calls.append(path)
        return b"\x00" * 8

    p = player({"tap": "/tmp/a.wav", "shake": "/tmp/b.wav"},
               loader=counting_loader)
    p.preload()
    assert sorted(calls) == ["/tmp/a.wav", "/tmp/b.wav"]


def test_an_empty_sample_set_is_trivially_preloaded():
    p = player({})
    assert p.is_preloaded is True


# --- play ---

def test_play_before_preload_raises():
    p = player()
    with pytest.raises(RuntimeError, match="preload"):
        p.play("tap")


def test_play_dispatches_to_the_sink():
    p = player()
    p.preload()
    p.play("tap")
    assert p.sink.played == [("tap", 128)]


def test_play_returns_a_latency_and_records_it():
    p = player()
    p.preload()
    latency = p.play("tap")
    assert latency >= 0.0
    assert p.last_latency_ms == pytest.approx(latency * 1000.0)


def test_unknown_sample_name_raises():
    p = player()
    p.preload()
    with pytest.raises(KeyError):
        p.play("nope")


def test_play_with_no_sink_still_reports_a_latency():
    p = SamplePlayer(PATHS, sink=None, loader=loader)
    p.preload()
    assert p.play("tap") >= 0.0


def test_repeated_plays_do_not_reload_from_disk():
    """A file read on the tap path is the difference between 3 ms and 30 ms."""
    calls: list[str] = []

    def counting_loader(path: str) -> bytes:
        calls.append(path)
        return b"\x00" * 128

    p = player(loader=counting_loader)
    p.preload()
    for _ in range(10):
        p.play("tap")
    assert len(calls) == 1


def test_the_sink_receives_the_preloaded_bytes():
    p = player(loader=lambda _: b"abcd")
    p.preload()
    p.play("tap")
    assert p.sink.played == [("tap", 4)]
