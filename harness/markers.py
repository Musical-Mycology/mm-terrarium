"""The stdout contract harness/run_stack.py supervises processes through.

The runner has to know when Control is ready for devices to join, and when
a device has actually been granted its role, because both are the
difference between a working run and a silent one. Waiting a fixed number
of seconds for either was tried and is not good enough: the SETUP window is
short, and a device that joins outside it is refused
(control/registration.py refuses a SCORED role once RUNNING).

Promoting these strings from incidental print() calls to named constants
matched on both sides is what makes stdout-watching honest. A reworded
print then breaks tests/test_markers.py rather than hanging the runner
forever with nothing to show for it.

FAILURE markers matter as much as ready ones. Waiting out a full timeout on
a failure the child has already diagnosed turns a 30-second answer into a
five-minute one, and both of the ones here are conditions the child knows
about precisely and the runner cannot infer.
"""

from __future__ import annotations

# --- Control (harness/terrarium_boot.py) -------------------------------

# The o2lite transport has claimed `game` on the hub and is serving.
CONTROL_TRANSPORT_READY = "DeviceLink running on o2lite ensemble"

# Registration is open. Devices must join scored roles inside this window.
CONTROL_SETUP_HOLD = "Holding in SETUP"

# The Bit signalled done from update(dt) and terrarium_boot is unwinding on
# purpose. A control child that exits ZERO after this line is the run
# ending on its own -- run_stack treats it as success, not child-exited.
# (TestBit under --hold never emits this; self-completing Bits like
# MetronomeBit always do.)
CONTROL_BIT_COMPLETED = "Bit completed; tearing down"

# A Bit is loaded and about to run -- round 1's CLI-selected Bit (printed
# once by main() before the round machinery starts) and, under --serve,
# every later round's Console-loaded Bit (printed by _serve_rounds only for
# a round it watched _wait_for_load actually observe leave IDLE, never for
# the immediate-return case on entry). One line per round, always.
CONTROL_ROUND_LOADED = "round loaded:"

# --- Device (harness/o2_shroom.py) -------------------------------------

# o2lite.time_get() went non-negative. Until this, the device has no clock
# and cannot stamp a gesture. This is the step the documented headless
# clock-sync defect stalls at, so it is the one the runner names when a CI
# run fails.
DEVICE_CLOCK_SYNCED = "clock synced at"

# Control answered the join with a role. Gestures start here.
DEVICE_ROLE_GRANTED = "role granted after"

# Control refused the join. Never recovers; fail the run now.
DEVICE_JOIN_DENIED = "JOIN DENIED:"

# The hub refused this device's service announcement because another
# process already offers that name (o2/src/bridge.cpp:231-237). The device
# clock-syncs, prints a watch URL and then receives nothing at all, which
# is the single most confusing live failure in this stack. See
# docs/superpowers/specs/2026-08-14-room-simulator-service-collision-design.md.
DEVICE_SERVICE_CONFLICT = "FATAL: service"

# --- Browser surfaces (all three harness entry points) -----------------

# A line carrying a URL worth a browser tab: the Terrarium Console, a Room
# fixture canvas, or a Testshroom canvas. run_stack collects
# every such URL and, under --open, opens each in the default browser.
# Unlike the ready/failure markers this one is not waited on -- there is a
# variable number of them per run -- so it lives outside both dicts.
BROWSE_URL = "BROWSE_URL:"

# A line carrying a URL worth knowing but NOT worth an automatic browser
# tab: a Room fixture canvas, opened on demand from the Console's Room
# card instead. run_stack collects and echoes these, never opens them.
ROOM_URL = "ROOM_URL:"

READY_MARKERS = {
    "CONTROL_TRANSPORT_READY": CONTROL_TRANSPORT_READY,
    "CONTROL_SETUP_HOLD": CONTROL_SETUP_HOLD,
    "CONTROL_BIT_COMPLETED": CONTROL_BIT_COMPLETED,
    "CONTROL_ROUND_LOADED": CONTROL_ROUND_LOADED,
    "DEVICE_CLOCK_SYNCED": DEVICE_CLOCK_SYNCED,
    "DEVICE_ROLE_GRANTED": DEVICE_ROLE_GRANTED,
}

FAILURE_MARKERS = {
    "DEVICE_JOIN_DENIED": DEVICE_JOIN_DENIED,
    "DEVICE_SERVICE_CONFLICT": DEVICE_SERVICE_CONFLICT,
}
