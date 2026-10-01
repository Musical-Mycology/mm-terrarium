"""Test helpers for the contract v3 entry path (spec 2026-10-01). Most
older tests only need "this device holds role X"; these give it the
real way: hello, handshake, and (for gameplay) start."""
from control.lobby import TERRARIUM_ADMIN
from control.state import State


def admit(gs, dev, node="", instrument=None):
    if gs.devices.get(dev) is None:
        gs.hello(dev, "", "", instrument)
    return gs.handshake(dev, gs.round_id, node)


def admit_running(gs, dev, node="", instrument=None):
    result = admit(gs, dev, node, instrument)
    if gs.state is State.SETUP:
        gs.request_start(None, TERRARIUM_ADMIN, "test")
    return result
