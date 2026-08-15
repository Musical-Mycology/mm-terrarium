import signal

from control.arco_process import FakePopen
from control.simulator_process import SimulatorProcess


def test_start_launches_the_configured_command():
    popen = FakePopen()
    process = SimulatorProcess(["room-simulator", "--dev", "sim-room"], popen=popen)
    process.start()
    assert popen.commands == [["room-simulator", "--dev", "sim-room"]]


def test_shutdown_sends_sigterm_and_reaps():
    popen = FakePopen()
    process = SimulatorProcess(["room-simulator"], popen=popen)
    process.start()

    process.shutdown()

    assert popen.signals == [signal.SIGTERM]
    assert popen.returncode is not None      # signalled AND reaped


def test_shutdown_before_start_is_a_noop():
    process = SimulatorProcess(["room-simulator"], popen=FakePopen())
    process.shutdown()   # must not raise


def test_shutdown_twice_only_signals_once():
    popen = FakePopen()
    process = SimulatorProcess(["room-simulator"], popen=popen)
    process.start()
    process.shutdown()
    process.shutdown()
    assert popen.signals == [signal.SIGTERM]
