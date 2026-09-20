"""instruments/tuneshroom_rev1.toml: the Rev 1 hardware/hardware-profile
instrument (docs/superpowers/specs/2026-09-16-device-contract-kit-design.md
section 5.2)."""
from pathlib import Path

from control.catalog import load_catalog
from control.instrument import TUNESHROOM, InstrumentRequirement, satisfies

ROOT = Path(__file__).resolve().parents[1]
REV1_CAPABILITIES = frozenset({"light.pixels", "gesture.tap", "gesture.hold",
                               "gesture.swing", "audio.samples"})


def _catalog():
    return load_catalog(ROOT / "instruments")


def test_tuneshroom_rev1_loads_and_validates_as_published():
    entry = _catalog().get("published", "tuneshroom_rev1")
    assert entry is not None
    assert entry.state == "published"
    assert entry.error is None
    assert entry.instrument.name == "tuneshroom_rev1"
    assert entry.instrument.capabilities == REV1_CAPABILITIES
    assert entry.instrument.pixels == 12


def test_satisfies_admits_tuneshroom_rev1_for_the_rev1_requirement():
    rev1 = _catalog().published["tuneshroom_rev1"]
    req = InstrumentRequirement(slot="player", capabilities=REV1_CAPABILITIES)
    assert satisfies(rev1, req) is None


def test_satisfies_refuses_tuneshroom_for_the_rev1_requirement():
    req = InstrumentRequirement(slot="player", capabilities=REV1_CAPABILITIES)
    assert satisfies(TUNESHROOM, req) is not None
