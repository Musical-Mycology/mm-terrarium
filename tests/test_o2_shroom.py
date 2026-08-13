import pytest

from harness.o2_shroom import build, tilt_sweep


def test_tilt_sweep_stays_in_range():
    """gamma is degrees in [-90, 90]: TestBit._on_tilt clamps to that, and a
    sweep that relied on the clamp would silently flatten at both ends."""
    for step in range(0, 400):
        value = tilt_sweep(step * 0.05)
        assert -90.0 <= value <= 90.0


def test_tilt_sweep_reverses_rather_than_jumping():
    """A ping-pong ramp, not a sawtooth: aurora glides its hue under cc:74,
    and a wrap-around discontinuity reads as a visible snap."""
    samples = [tilt_sweep(step * 0.05) for step in range(0, 200)]
    biggest_step = max(abs(b - a) for a, b in zip(samples, samples[1:]))
    assert biggest_step < 20.0


def test_tilt_sweep_is_periodic():
    """One full period returns to where it started, so the sweep closes its
    loop cleanly rather than drifting. Also pins it as a deterministic
    function of elapsed time: a random walk would make every acceptance run
    a judgement call."""
    from harness.o2_shroom import SWEEP_PERIOD

    assert tilt_sweep(0.0) == tilt_sweep(SWEEP_PERIOD)
    assert tilt_sweep(1.25) == tilt_sweep(1.25 + SWEEP_PERIOD)


def test_build_wires_the_client_and_backend():
    """Mirrors tests/test_room_simulator.py's test_build_wires_the_client_
    and_backend for the same socket-free build() seam: dev id and node
    reach the client, an LED adapter is wired, and serve=False means no
    socket was opened."""
    pytest.importorskip("luxaeterna.backends.websim")

    client, backend = build("ie1", "TEST_PLAYER_NODE", serve=False)

    assert client.dev == "ie1"
    assert client.node == "TEST_PLAYER_NODE"
    assert client.leds is not None
    assert backend.is_open is False
