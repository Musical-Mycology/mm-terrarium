"""Export the device contract kit -- the verb table, the Rev 1 instrument,
and every committed scenario recording -- as the EXPORT FORMAT v1 folder
each device repo (mm-tuneshroom, mm-devshroom) commits at test/contract/
(docs/superpowers/specs/2026-09-16-device-contract-kit-design.md section
4.2, and the plan's "Reference: EXPORT FORMAT v1"). A sibling of
tools/export_solo.py.

    .venv/bin/python -m tools.export_contract /path/to/mm-tuneshroom/test/contract

Every `lifecycle`/`limits` number is read from the constant that owns it
rather than typed as a literal here, with one exception noted at
BENCH_TOLERANCE_MS below. Scenario files are copied byte for byte from
contract_kit/recordings/, never re-recorded: the export's job is to
package what is already committed, not to re-derive it, and a mismatch
between contract_kit.scenarios.ALL_SCENARIOS and the recordings on disk
is a defect this tool refuses to paper over.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from control.boot_config import BootConfig
from control.catalog import load_catalog
from control.lobby import LobbyConfig, TERRARIUM_ADMIN
from control.role_config import carried_instrument_view
from contract_kit.recorder import CUE_HORIZON_S
from contract_kit.scenarios import ALL_SCENARIOS
from devicelink.contract import VERB_TABLE
from devicelink.o2_transport import MAX_DEV_LEN
from devicelink.protocol import O2_MAX_MSG_LEN
from harness.o2_shroom import HELLO_INTERVAL_S

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = REPO_ROOT / "contract_kit" / "recordings"
TOOL_VERSION = "export_contract/1"

# Bumped on any change a device can observe: a new/changed verb, a
# link/limits/lifecycle value, the tuneshroom_rev1 instrument, or a
# scenario's steps (spec section 4.2). A device repo pins this number in
# its own replay tests so an export that changes it cannot be adopted
# silently.
CONTRACT_VERSION = 1

# The live bench replay's timing tolerances (spec section 4.2, "the
# tolerances the live bench replay uses"; section 8). No single constant
# in the code owns these numbers today -- the bench replay itself does
# not exist yet (docs/superpowers/plans/2026-09-16-bench-replay-feasibility.md
# is still a feasibility spike) -- so this is the contract kit's own
# published value, taken from the design spec's lifecycle example, not a
# figure read off running code.
BENCH_TOLERANCE_MS = {"frame": 50, "heartbeat": 1000}

# Plain-sentence replay rules a runner needs that the scenario files alone
# don't state (EXPORT FORMAT v1 defines no field for this, so this key is
# new). Sources: contract_kit/scenarios.py's module docstring and
# contract_kit/recorder.py's Recorder.control_send_now docstring.
REPLAY_NOTES = [
    "A replay runner must not deliver a control_sends step while the "
    "scenario's link is down; those steps record what Control really "
    "sent, and a device with its link down receives none of them.",
    "A control_sends step flagged \"malformed\": true is delivered to the "
    "device and must be dropped without changing state.",
    "Some /$DEV/leds control_sends steps in timed_frames_hold_last are "
    "hand-authored rather than captured from Control, to pin the "
    "newest-of-several-due rule (rule 3); see that scenario's own "
    "docstring.",
]


def _stale_timeout_s() -> float:
    """BootConfig.stale_timeout's shipped default, read off the dataclass.

    BootConfig has two required fields (room_name, bit_name), so it
    cannot simply be constructed here (contract_kit/recorder.py's
    _production_cue_horizon reads cue_horizon the same way, for the same
    reason).
    """
    for field in dataclasses.fields(BootConfig):
        if field.name == "stale_timeout":
            return float(field.default)
    raise RuntimeError("BootConfig no longer declares stale_timeout")


def _head_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _verb_entry(row) -> dict:
    return {"address": row.address, "direction": row.direction,
            "typespecs": list(row.typespecs), "args": list(row.args),
            "transport": row.transport, "pre_role": row.pre_role,
            "notes": row.notes}


def export_contract(*, commit: str) -> dict:
    rev1 = load_catalog(REPO_ROOT / "instruments").published["tuneshroom_rev1"]
    verbs = sorted((_verb_entry(row) for row in VERB_TABLE),
                  key=lambda v: v["address"])
    return {
        "_provenance": {"commit": commit, "tool": TOOL_VERSION},
        "contract_version": CONTRACT_VERSION,
        "verbs": verbs,
        "link": {"arg_types": ["b", "f", "i", "s"],
                "max_message_bytes": O2_MAX_MSG_LEN,
                "service_is_dev_id": True},
        "limits": {"max_message_bytes": O2_MAX_MSG_LEN,
                  "dev_id_max_len": MAX_DEV_LEN,
                  "reserved_dev_ids": [TERRARIUM_ADMIN]},
        "lifecycle": {"hello_interval_s": HELLO_INTERVAL_S,
                     "stale_timeout_s": _stale_timeout_s(),
                     "lobby_double_tap_window_s": LobbyConfig().double_tap_window_s,
                     "cue_horizon_s": CUE_HORIZON_S,
                     "bench_tolerance_ms": dict(BENCH_TOLERANCE_MS)},
        "instruments": {
            "tuneshroom_rev1": {
                "instrument": carried_instrument_view(rev1),
                "triggers": {t.name: dict(t.thresholds)
                            for t in rev1.event_triggers},
            },
        },
        "replay_notes": list(REPLAY_NOTES),
    }


def _scenario_names() -> list[str]:
    return [fn.__name__ for fn in ALL_SCENARIOS]


def _committed_recording_names() -> list[str]:
    return sorted(p.stem for p in RECORDINGS_DIR.glob("*.json"))


def _copy_scenarios(out_dir: Path) -> int:
    """Copy contract_kit/recordings/<name>.json byte for byte into
    <out_dir>/scenarios/, one file per name in ALL_SCENARIOS. Exits loudly
    on any mismatch between ALL_SCENARIOS and what is actually committed,
    rather than silently exporting a stale or incomplete scenario set."""
    expected = set(_scenario_names())
    committed = set(_committed_recording_names())

    missing = sorted(expected - committed)
    if missing:
        sys.exit("contract_kit/recordings/ is missing a recording named by "
                 f"contract_kit.scenarios.ALL_SCENARIOS: {', '.join(missing)}")

    orphaned = sorted(committed - expected)
    if orphaned:
        sys.exit("contract_kit/recordings/ holds a recording no scenario in "
                 f"contract_kit.scenarios.ALL_SCENARIOS produces: "
                 f"{', '.join(orphaned)}")

    scenarios_dir = out_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(expected):
        data = (RECORDINGS_DIR / f"{name}.json").read_bytes()
        (scenarios_dir / f"{name}.json").write_bytes(data)
    return len(expected)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: export_contract.py <out-dir>")
    out_dir = Path(argv[0])
    commit = _head_commit()

    _write_json(out_dir / "contract.json", export_contract(commit=commit))
    count = _copy_scenarios(out_dir)
    print(f"wrote {out_dir}/contract.json and {count} scenario files")


if __name__ == "__main__":
    main()
