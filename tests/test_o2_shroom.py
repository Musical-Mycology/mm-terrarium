import pytest

from harness.o2_shroom import _gestures_ready, build, tilt_sweep


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


# --- Gating gestures on the role: harness/shroom_client.py's ShroomClient
# sets .config in _on_role(), only once Control's granted-role reply has
# actually arrived. main()'s join is sent over TCP (o2lite.send_cmd) but
# gestures go out over UDP (o2lite.send), so without this gate the first
# tilt can overtake the join and reach Control before it is registered --
# "tilt: device not registered". A fake client (no socket, no o2lite)
# is enough to drive _gestures_ready() in isolation. ------------------------

class _FakeClient:
    """Stands in for ShroomClient: _gestures_ready() only ever reads
    .config, so nothing else about the real client is needed here."""

    def __init__(self, config=None):
        self.config = config


def test_gestures_not_ready_before_the_role_arrives():
    assert _gestures_ready(_FakeClient(config=None)) is False


def test_gestures_ready_once_the_role_arrives():
    assert _gestures_ready(_FakeClient(config={"bit_name": "TestBit"})) is True


# --- Parent-death guard. The Room simulator is spawned by
# harness/terrarium_boot.py and, with --no-join, never exits on its own:
# main()'s loop waits for a /release that only a live Control sends. An
# orphan therefore runs forever, and o2litepy reconnects it to the NEXT
# Arco (o2lite.py:912 connects whenever _tcp_socket is None; _id_handler
# at :601 re-announces services on connect), where it claims the same dev
# name. O2 then refuses the new run's own simulator with "not from service
# provider" (o2/src/bridge.cpp:231-237). See docs/superpowers/specs/
# 2026-08-14-room-simulator-service-collision-design.md. ------------------

def test_parent_is_gone_is_false_while_the_parent_still_owns_us():
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(4242, getppid=lambda: 4242) is False


def test_parent_is_gone_is_true_once_we_have_been_reparented():
    """A dead parent's children are reparented to init/launchd (pid 1).

    This is also why the check compares against a RECORDED pid rather than
    watching getppid() for a change: if the parent died before this process
    read its argv, getppid() already reads 1 at startup and a change
    detector would wait forever. Comparison catches both orderings with the
    same expression, which is why there is only one case to test here."""
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(4242, getppid=lambda: 1) is True


def test_parent_is_gone_never_fires_without_an_expected_pid():
    """--exit-with-parent is opt-in. A hand-run device passes nothing and
    must never exit because of this guard."""
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(None, getppid=lambda: 1) is False


# --- The service the device just announced may have been refused. O2
# drops a second claimant's /_o2/*/sv with "not from service provider"
# (o2/src/bridge.cpp:231-237) and logs it on the HUB. Measured side by
# side, a refused simulator and an accepted player print the same two
# lines: a watch URL and "clock synced". This gate is what makes them
# distinguishable. -------------------------------------------------------

def test_service_conflict_is_silent_when_the_dev_is_ours():
    from harness.o2_shroom import service_conflict

    assert service_conflict(object(), "sim-room",
                            verify=lambda o2lite, dev: True) is None


def test_service_conflict_names_the_dev_and_the_remedy():
    """The whole cost of this bug was that it was invisible: the refused
    client printed its watch URL and clock-synced exactly like a healthy
    one, and Control saw no error either because the hub routed its frames
    successfully, to the wrong process. The message has to end the
    investigation on the spot."""
    from harness.o2_shroom import service_conflict

    message = service_conflict(object(), "sim-room",
                               verify=lambda o2lite, dev: False)

    assert message is not None
    assert "sim-room" in message
    assert "harness.o2_shroom" in message


def test_service_conflict_asks_about_the_dev_it_was_given():
    """A typo here would check the wrong service and always pass."""
    from harness.o2_shroom import service_conflict

    asked = []

    def _verify(o2lite, dev):
        asked.append(dev)
        return True

    service_conflict(object(), "ie1", verify=_verify)
    assert asked == ["ie1"]
