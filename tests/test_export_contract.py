"""tools/export_contract.py: packages devicelink/contract.py's verb table,
the tuneshroom_rev1 instrument, and the committed contract_kit/recordings/
into the EXPORT FORMAT v1 folder every device repo commits at
test/contract/ (docs/superpowers/specs/2026-09-16-device-contract-kit-design.md
section 4.2, and the plan's "Reference: EXPORT FORMAT v1")."""
import dataclasses
import json
from pathlib import Path

import pytest

pytest.importorskip("luxaeterna")

from control.boot_config import BootConfig
from control.catalog import load_catalog
from control.lobby import LobbyConfig, TERRARIUM_ADMIN
from control.role_config import carried_instrument_view
from contract_kit.scenarios import ALL_SCENARIOS
from devicelink.contract import VERB_TABLE
from devicelink.o2_transport import MAX_DEV_LEN
from devicelink.protocol import O2_MAX_MSG_LEN
from harness.o2_shroom import HELLO_INTERVAL_S
from tools.export_contract import export_contract, main

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = ROOT / "contract_kit" / "recordings"


def _stale_timeout_default() -> float:
    for field in dataclasses.fields(BootConfig):
        if field.name == "stale_timeout":
            return float(field.default)
    raise RuntimeError("BootConfig no longer declares stale_timeout")


def test_verbs_match_the_verb_table_exactly():
    data = export_contract(commit="abc123")
    assert len(data["verbs"]) == len(VERB_TABLE)
    by_address = {v["address"]: v for v in data["verbs"]}
    assert set(by_address) == {row.address for row in VERB_TABLE}
    for row in VERB_TABLE:
        entry = by_address[row.address]
        assert entry == {
            "address": row.address,
            "direction": row.direction,
            "typespecs": list(row.typespecs),
            "args": list(row.args),
            "transport": row.transport,
            "pre_role": row.pre_role,
            "notes": row.notes,
        }


def test_verbs_are_sorted_by_address_and_order_is_deterministic():
    data = export_contract(commit="abc123")
    addresses = [v["address"] for v in data["verbs"]]
    assert addresses == sorted(addresses)
    again = export_contract(commit="abc123")
    assert [v["address"] for v in again["verbs"]] == addresses


def test_link_and_limits_come_from_the_code():
    data = export_contract(commit="abc123")
    assert data["link"] == {"arg_types": ["b", "f", "i", "s"],
                            "max_message_bytes": O2_MAX_MSG_LEN,
                            "service_is_dev_id": True}
    assert data["limits"] == {"max_message_bytes": O2_MAX_MSG_LEN,
                              "dev_id_max_len": MAX_DEV_LEN,
                              "reserved_dev_ids": [TERRARIUM_ADMIN]}


def test_lifecycle_comes_from_the_code():
    data = export_contract(commit="abc123")
    assert data["lifecycle"]["hello_interval_s"] == HELLO_INTERVAL_S
    assert data["lifecycle"]["stale_timeout_s"] == _stale_timeout_default()
    assert (data["lifecycle"]["lobby_double_tap_window_s"]
            == LobbyConfig().double_tap_window_s)
    assert data["lifecycle"]["bench_tolerance_ms"] == {"frame": 50,
                                                       "heartbeat": 1000}


def test_lifecycle_publishes_the_cue_horizon_recordings_assume():
    from contract_kit.recorder import CUE_HORIZON_S
    data = export_contract(commit="abc123")
    assert data["lifecycle"]["cue_horizon_s"] == CUE_HORIZON_S


def test_replay_notes_state_the_undocumented_replay_rules():
    data = export_contract(commit="abc123")
    notes = " ".join(data["replay_notes"]).lower()
    assert "control_sends" in " ".join(data["replay_notes"])
    assert "link is down" in notes
    assert "malformed" in notes
    assert "hand-authored" in notes or "hand authored" in notes


def test_instruments_section_uses_the_carried_instrument_view():
    data = export_contract(commit="abc123")
    rev1 = load_catalog(ROOT / "instruments").published["tuneshroom_rev1"]
    section = data["instruments"]["tuneshroom_rev1"]
    assert section["instrument"] == carried_instrument_view(rev1)
    assert section["triggers"] == {t.name: dict(t.thresholds)
                                   for t in rev1.event_triggers}


def test_instrument_triggers_match_the_toml():
    data = export_contract(commit="abc123")
    triggers = data["instruments"]["tuneshroom_rev1"]["triggers"]
    assert triggers == {
        "tap": {"max_ms": 250},
        "hold": {"min_ms": 400},
        "swing": {"peak_g": 1.5, "window_ms": 80},
    }


def test_contract_version_and_provenance():
    data = export_contract(commit="abc123")
    assert data["contract_version"] == 1
    assert data["_provenance"] == {"commit": "abc123", "tool": "export_contract/1"}


def test_main_writes_contract_and_scenario_files(tmp_path):
    main([str(tmp_path)])
    contract = json.loads((tmp_path / "contract.json").read_text())
    assert contract["contract_version"] == 1
    scenario_files = sorted(p.name for p in (tmp_path / "scenarios").glob("*.json"))
    assert scenario_files == sorted(f"{fn.__name__}.json" for fn in ALL_SCENARIOS)


def test_scenario_files_are_byte_identical_to_the_committed_recordings(tmp_path):
    main([str(tmp_path)])
    for fn in ALL_SCENARIOS:
        name = fn.__name__
        exported = (tmp_path / "scenarios" / f"{name}.json").read_bytes()
        committed = (RECORDINGS_DIR / f"{name}.json").read_bytes()
        assert exported == committed


def test_two_exports_with_the_same_commit_are_byte_identical(tmp_path):
    out1 = tmp_path / "one"
    out2 = tmp_path / "two"
    main([str(out1)])
    main([str(out2)])
    files1 = sorted(p.relative_to(out1) for p in out1.rglob("*") if p.is_file())
    files2 = sorted(p.relative_to(out2) for p in out2.rglob("*") if p.is_file())
    assert files1 == files2
    for rel in files1:
        assert (out1 / rel).read_bytes() == (out2 / rel).read_bytes()


def test_export_fails_loudly_when_a_scenario_recording_is_missing(tmp_path, monkeypatch):
    import tools.export_contract as export_contract_module

    missing = "no_such_scenario"

    def fake_all_scenarios():
        def _missing():
            return {"name": missing}
        _missing.__name__ = missing
        return (*ALL_SCENARIOS, _missing)

    monkeypatch.setattr(export_contract_module, "_scenario_names",
                        lambda: [fn.__name__ for fn in fake_all_scenarios()])
    with pytest.raises(SystemExit, match=missing):
        export_contract_module.main([str(tmp_path)])


def test_export_fails_loudly_when_a_recording_has_no_scenario(tmp_path, monkeypatch):
    import shutil

    import tools.export_contract as export_contract_module

    # A throwaway copy of the recordings directory, not the committed one:
    # writing an orphan file into the real contract_kit/recordings/ would
    # leave it behind if the run were interrupted, or race a parallel run.
    fake_recordings = tmp_path / "recordings"
    shutil.copytree(RECORDINGS_DIR, fake_recordings)
    (fake_recordings / "orphan_scenario.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(export_contract_module, "RECORDINGS_DIR", fake_recordings)

    with pytest.raises(SystemExit, match="orphan_scenario"):
        export_contract_module.main([str(tmp_path / "out")])
