"""tools/export_contract.py: packages devicelink/contract.py's verb table,
the tuneshroom_rev1 instrument, and the committed contract_kit/recordings/
into the EXPORT FORMAT v1 folder every device repo commits at
test/contract/ (docs/superpowers/specs/2026-09-16-device-contract-kit-design.md
section 4.2, and the plan's "Reference: EXPORT FORMAT v1")."""
import dataclasses
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

pytest.importorskip("luxaeterna")

from control.boot_config import BootConfig
from control.catalog import load_catalog
from control.lobby import LobbyConfig, TERRARIUM_ADMIN
from control.role_config import carried_instrument_view
from contract_kit.scenarios import ALL_SCENARIOS
from devicelink.contract import HELLO_INTERVAL_S, VERB_TABLE, row_for
from devicelink.o2_transport import MAX_DEV_LEN
from devicelink.protocol import O2_MAX_MSG_LEN
from tools.export_contract import export_contract, main

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = ROOT / "contract_kit" / "recordings"

# Schema entries this fix wave knows are not yet demonstrated in any
# recording, with the reason it is still correct to publish them. Kept
# empty on purpose: every "kinds"/"fields" entry in STEP_SCHEMA today was
# derived FROM a scan of the recordings, so nothing should need to be
# listed here. A future entry would go here as
# {("kind", "field_or_None"): "reason"} (field_or_None=None allow-lists
# the whole kind).
SCHEMA_ENTRIES_NOT_YET_RECORDED: dict[tuple[str, str | None], str] = {}

# Backticked dotted paths (`a.b.c`) inside step_schema/replay_notes text,
# where the first segment names one of these top-level contract.json
# keys, must resolve inside the emitted document. A dotted mention like
# `control_sends.address` does NOT start with a top-level key (there is
# no top-level "control_sends" in contract.json -- it names a step kind
# and field instead), so it is correctly skipped by this filter.
_DOTTED_REF_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`")


def _stale_timeout_default() -> float:
    for field in dataclasses.fields(BootConfig):
        if field.name == "stale_timeout":
            return float(field.default)
    raise RuntimeError("BootConfig no longer declares stale_timeout")


def _every_recorded_step() -> list[dict]:
    """Every step dict, from every committed scenario recording, with
    "t" stripped (only the kind key(s) matter for schema coverage)."""
    steps = []
    for path in sorted(RECORDINGS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        for step in data["steps"]:
            steps.append({k: v for k, v in step.items() if k != "t"})
    return steps


def _recorded_step_shapes() -> tuple[set, dict]:
    """(scalar_kinds, dict_fields): scalar_kinds is the set of step kinds
    whose payload is a bare value (e.g. "link"); dict_fields maps every
    other kind to the set of field names its recorded payloads use."""
    scalar_kinds = set()
    dict_fields: dict[str, set] = defaultdict(set)
    for step in _every_recorded_step():
        for kind, payload in step.items():
            if isinstance(payload, dict):
                dict_fields[kind] |= set(payload.keys())
            else:
                scalar_kinds.add(kind)
    return scalar_kinds, dict_fields


def _recorded_placeholder_usage() -> dict[str, set]:
    """{"$DEV": {"kind.field", ...}, ...}: every "kind.field" location
    (top-level field name, not a deep path) where a placeholder string
    actually appears in a committed recording."""
    usage: dict[str, set] = defaultdict(set)
    placeholders = ("$DEV", "$KEY", "*")
    for step in _every_recorded_step():
        for kind, payload in step.items():
            if not isinstance(payload, dict):
                continue
            for field_name, value in payload.items():
                text = json.dumps(value)
                for ph in placeholders:
                    if ph in text:
                        usage[ph].add(f"{kind}.{field_name}")
    return usage


def _all_strings(obj):
    """Every string leaf under obj (a JSON-shaped nested dict/list/str)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_strings(v)


def _resolve_dotted_path(root: dict, path: str):
    """Walk `path` (dot-separated) from `root`. Raises KeyError/TypeError
    on any segment that doesn't resolve, naming the full path."""
    node = root
    for segment in path.split("."):
        node = node[segment]
    return node


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


def test_hello_interval_is_owned_by_the_contract_not_the_harness():
    # Minor 2 (first fix wave): the constant lives in
    # devicelink/contract.py; harness/o2_shroom.py and
    # contract_kit/recorder.py both import it from there.
    from contract_kit.recorder import HELLO_INTERVAL_MS
    from harness.o2_shroom import HELLO_INTERVAL_S as harness_value
    assert harness_value is HELLO_INTERVAL_S
    assert HELLO_INTERVAL_MS == round(HELLO_INTERVAL_S * 1000)


def test_lifecycle_notes_cover_every_lifecycle_key_with_units():
    data = export_contract(commit="abc123")
    notes = data["lifecycle_notes"]
    assert set(notes) == set(data["lifecycle"])
    assert "second" in notes["hello_interval_s"].lower()
    assert "second" in notes["stale_timeout_s"].lower()
    assert "second" in notes["lobby_double_tap_window_s"].lower()
    assert "second" in notes["cue_horizon_s"].lower()
    assert "millisecond" in notes["bench_tolerance_ms"].lower()


def test_link_semantics_state_the_heartbeat_and_no_resume_rules():
    data = export_contract(commit="abc123")
    semantics = data["step_schema"]["kinds"]["link"]["semantics"]
    hello = row_for("up", "hello")
    # The hello typespec/args are pulled from the verb table itself, not
    # retyped, so this text cannot drift from devicelink/contract.py.
    assert hello.typespecs[-1] in semantics
    for arg in hello.args:
        assert arg in semantics
    assert "hello_interval_s" in semantics
    assert "stale_timeout_s" in semantics
    assert "join again" in semantics or "must join" in semantics
    assert "nothing" in semantics  # link-down: sends/receives nothing


def test_replay_notes_state_the_undocumented_replay_rules():
    data = export_contract(commit="abc123")
    joined = " ".join(data["replay_notes"])
    lowered = joined.lower()
    assert "control_sends" in joined
    assert "link is down" in lowered
    assert "malformed" in lowered
    assert "hand-authored" in lowered
    # Important 1: no replay note may point outside the export at a file
    # this folder does not contain.
    assert "docstring" not in lowered
    assert "scenarios.py" not in lowered
    # The timed_frames_hold_last facts are inlined, not just referenced.
    assert "timed_frames_hold_last" in joined
    assert "6200" in joined and "6100" in joined
    assert "6000" in joined
    assert "0, 0, 255" in joined and "255, 0, 0" in joined


def test_step_schema_matches_the_recordings_exactly():
    """Two-directional: every step kind/field the recordings actually use
    is described (forward), and every kind/field step_schema describes is
    actually used somewhere, unless allow-listed with a reason
    (backward). Covers scalar-payload kinds (currently just "link") as
    well as object-payload kinds."""
    data = export_contract(commit="abc123")
    schema_kinds = data["step_schema"]["kinds"]
    scalar_kinds, dict_fields = _recorded_step_shapes()
    assert scalar_kinds or dict_fields, "no recordings found -- test fixture is broken"

    # Forward: every recorded shape is described.
    for kind in scalar_kinds:
        assert kind in schema_kinds, (
            f"scalar step kind {kind!r} appears in a recording but is "
            f"not described in step_schema")
        assert "payload" in schema_kinds[kind], (
            f"{kind!r} is recorded as a scalar payload but step_schema "
            f"describes it with \"fields\" instead of \"payload\"")
    for kind, fields in dict_fields.items():
        assert kind in schema_kinds, (
            f"step kind {kind!r} appears in a recording but is not "
            f"described in step_schema")
        assert "fields" in schema_kinds[kind], (
            f"{kind!r} is recorded as an object payload but step_schema "
            f"describes it with \"payload\" instead of \"fields\"")
        described_fields = set(schema_kinds[kind]["fields"])
        for field_name in fields:
            assert field_name in described_fields, (
                f"{kind}.{field_name} appears in a recording but is not "
                f"described in step_schema")

    # Backward: every described shape is actually used, unless
    # allow-listed.
    for kind, spec in schema_kinds.items():
        if "payload" in spec:
            allowed = (kind, None) in SCHEMA_ENTRIES_NOT_YET_RECORDED
            assert kind in scalar_kinds or allowed, (
                f"step_schema describes scalar kind {kind!r}, which no "
                f"recording contains and which is not allow-listed")
        elif "fields" in spec:
            allowed_kind = (kind, None) in SCHEMA_ENTRIES_NOT_YET_RECORDED
            assert kind in dict_fields or allowed_kind, (
                f"step_schema describes kind {kind!r}, which no "
                f"recording contains and which is not allow-listed")
            for field_name in spec["fields"]:
                allowed_field = ((kind, field_name)
                                in SCHEMA_ENTRIES_NOT_YET_RECORDED)
                assert (field_name in dict_fields.get(kind, set())
                       or allowed_field), (
                    f"step_schema describes {kind}.{field_name}, which "
                    f"no recording contains and which is not "
                    f"allow-listed")


def test_placeholders_name_every_field_they_actually_appear_in():
    data = export_contract(commit="abc123")
    described = data["step_schema"]["placeholders"]
    actual = _recorded_placeholder_usage()
    assert set(actual), "no placeholder usage found -- test fixture is broken"
    for ph, locations in actual.items():
        assert ph in described, f"{ph!r} appears in recordings but step_schema.placeholders omits it"
        named = set(described[ph]["appears_in"])
        undocumented = locations - named
        assert not undocumented, (
            f"{ph!r} appears in {sorted(undocumented)} but "
            f"step_schema.placeholders[{ph!r}]['appears_in'] only names "
            f"{sorted(named)}")
    # And the reverse: nothing is claimed that isn't real.
    for ph, spec in described.items():
        claimed = set(spec["appears_in"])
        real = actual.get(ph, set())
        overclaimed = claimed - real
        assert not overclaimed, (
            f"step_schema.placeholders[{ph!r}] claims {sorted(overclaimed)} "
            f"but no recording puts {ph!r} there")


def test_every_dotted_cross_reference_resolves_in_the_export():
    data = export_contract(commit="abc123")
    texts = list(_all_strings(data["step_schema"])) + list(data["replay_notes"])
    checked = 0
    for text in texts:
        for path in _DOTTED_REF_RE.findall(text):
            top = path.split(".", 1)[0]
            if top not in data:
                continue  # not a reference into contract.json (e.g. a
                          # "kind.field" mention like control_sends.address)
            checked += 1
            try:
                _resolve_dotted_path(data, path)
            except (KeyError, TypeError) as exc:
                pytest.fail(f"backticked reference `{path}` does not "
                           f"resolve in the export: {exc!r}")
    assert checked > 0, "no dotted cross-references found -- test is not exercising anything"


def test_scenario_index_matches_the_files_written(tmp_path):
    main([str(tmp_path)])
    data = json.loads((tmp_path / "contract.json").read_text())
    index = data["scenarios"]
    assert [e["name"] for e in index] == sorted(e["name"] for e in index)
    names_in_index = {e["name"] for e in index}
    names_on_disk = {p.stem for p in (tmp_path / "scenarios").glob("*.json")}
    assert names_in_index == names_on_disk
    for entry in index:
        recorded = json.loads(
            (tmp_path / "scenarios" / f"{entry['name']}.json").read_text())
        assert entry["profiles"] == recorded["profiles"]
        assert entry["summary"] == recorded["summary"]


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
    # Nothing has been published to a device repo yet, so contract_version
    # stays 1 even though this and earlier fix waves added keys.
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
    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit, match=missing):
        export_contract_module.main([str(out_dir)])
    # Nothing is written when validation fails, not even a partial
    # contract.json.
    assert not (out_dir / "contract.json").exists()
    assert not out_dir.exists() or not any(out_dir.iterdir())


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

    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit, match="orphan_scenario"):
        export_contract_module.main([str(out_dir)])
    assert not (out_dir / "contract.json").exists()
    assert not out_dir.exists() or not any(out_dir.iterdir())
