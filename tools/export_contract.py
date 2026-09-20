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
is a defect this tool refuses to paper over -- it is checked, and this
tool exits loudly, before anything is written.

`contract.json` also carries `step_schema`, `scenarios` and
`replay_notes`, because a device author who holds only this export
folder (not this repo) must be able to write a replay runner from it
alone. Nothing in those three keys should ever point outside the export
at a file the folder does not contain.
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
from devicelink.contract import HELLO_INTERVAL_S, VERB_TABLE
from devicelink.o2_transport import MAX_DEV_LEN
from devicelink.protocol import O2_MAX_MSG_LEN

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = REPO_ROOT / "contract_kit" / "recordings"
TOOL_VERSION = "export_contract/1"

# Bumped on any change a device can observe: a new/changed verb, a
# link/limits/lifecycle value, the tuneshroom_rev1 instrument, or a
# scenario's steps (spec section 4.2). A device repo pins this number in
# its own replay tests so an export that changes it cannot be adopted
# silently. Nothing in this export has been published to a device repo
# yet, so this fix wave keeps it at 1 even though it adds keys.
CONTRACT_VERSION = 1

# The live bench replay's timing tolerances (spec section 4.2, "the
# tolerances the live bench replay uses"; section 8). No single constant
# in the code owns these numbers today -- the bench replay itself does
# not exist yet (docs/superpowers/plans/2026-09-16-bench-replay-feasibility.md
# is still a feasibility spike) -- so this is the contract kit's own
# published value, taken from the design spec's lifecycle example, not a
# figure read off running code.
BENCH_TOLERANCE_MS = {"frame": 50, "heartbeat": 1000}

# What every step kind in every committed recording looks like: every
# field, its type, its unit, and what a placeholder value means. Verified
# against contract_kit/recorder.py's step-producing methods and against a
# scan of all eleven contract_kit/recordings/*.json files (no kind or
# field appears in the recordings that isn't described here -- see
# tests/test_export_contract.py's test_step_schema_covers_every_recorded_step_kind_and_field).
# A device author holding only this export must be able to write a replay
# runner from this object plus the scenario files alone.
STEP_SCHEMA = {
    "t": ("int; milliseconds since the scenario's own start. `steps` is "
         "sorted by `t`, ascending, stably (ties keep their original "
         "relative order)."),
    "tolerance": (
        "In-process replay runs on a fake clock and allows an "
        "expect_frame to land up to one render tick (23 ms) late. "
        "expect_out and expect_play are instead checked against their "
        "own within_ms window, which starts at the step's own t. A live "
        "bench replay uses this contract.json's lifecycle.bench_tolerance_ms."
    ),
    "link_down_delivery": (
        "While the most recently seen link step is \"down\", a replay "
        "runner must not deliver any control_sends step to the device. "
        "Those steps record what Control really sent while recording, "
        "and a device whose link is down receives none of them."
    ),
    "placeholders": {
        "$DEV": ("the replaying device's own dev id, substituted for "
                 "the id used while recording"),
        "$KEY": ("in a control_sends string argument, a value whose "
                 "shape must match (for example \"key=$KEY\") but whose "
                 "actual number was never pinned"),
        "*": "in expect_out.args, matches any value",
    },
    "scenario_fields": {
        "name": "str; snake_case, equal to the file's own stem.",
        "summary": "str; one line describing what the scenario covers.",
        "profiles": ("list of str; the device profiles this scenario "
                    "must pass in (\"rev1\" and/or \"any\")."),
        "device": ("object, {\"join_node\": str or null}. When "
                  "join_node is non-null, the device sends /game/join "
                  "[\"$DEV\", join_node] right after its first hello on "
                  "every link-up."),
        "steps": "list of step objects (see \"kinds\" below).",
    },
    "kinds": {
        "link": {
            "role": "input: the O2 link's state changes.",
            "fields": {"link": "str; \"up\" or \"down\"."},
        },
        "control_sends": {
            "role": ("input: a message Control really sent while "
                     "recording; deliver it to the device (subject to "
                     "tolerance.link_down_delivery above)."),
            "fields": {
                "address": "str; \"/$DEV/<verb>\".",
                "typespec": "str; the O2 typespec the message was sent with.",
                "args": ("list; decoded arguments -- role and room "
                        "args[0] is a JSON object, leds args[0] is a "
                        "list of 36 ints (0-255), GRB, 3 per pixel, 12 "
                        "pixels."),
                "at": ("int ms on the same t timeline, or null; the "
                      "presentation time the message carries. Only "
                      "/leds ever carries one -- every other verb's at "
                      "is null, meaning it has no presentation time, "
                      "not that it is due at t=0."),
                "malformed": ("bool; present and true only on a "
                             "hand-authored step a device must drop "
                             "without changing any state. Absent (not "
                             "false) on every message actually captured "
                             "from Control."),
            },
        },
        "gesture": {
            "role": ("input: a classified gesture delivered to the "
                     "device session at t."),
            "fields": {
                "kind": "str; \"tap\", \"hold\" or \"swing\".",
                "onset_t": "int ms; when the gesture began (equals the step's own t).",
                "duration_ms": "float; tap only -- how long the touch lasted.",
                "held_s": ("float; hold only -- seconds the touch was "
                          "held before release."),
                "signed_g": ("float; swing only -- peak acceleration in "
                            "g, negative means left."),
            },
        },
        "expect_out": {
            "role": ("expectation: the device must send this message "
                     "with a send time in [t, t + within_ms]."),
            "fields": {
                "address": "str; \"/game/<verb>\".",
                "typespec": "str.",
                "args": ("list; each element is a literal (numbers "
                        "match within 1e-3), \"$DEV\", or \"*\" (see "
                        "placeholders)."),
                "stamp_t": ("int ms or null; the O2 timestamp the "
                           "outbound message must carry, within 1 ms. "
                           "null means the timestamp is not checked."),
                "within_ms": ("int; width in ms of the send-time "
                             "window, starting at t."),
            },
        },
        "expect_frame": {
            "role": "expectation: the pixels showing at t (see tolerance above).",
            "fields": {
                "grb": ("list of 36 ints, 0-255: 12 pixels x 3 "
                       "channels, green-red-blue order."),
            },
        },
        "expect_play": {
            "role": ("expectation: a play effect with this name and "
                     "params is emitted in [t, t + within_ms]."),
            "fields": {
                "name": ("str; the sample name. An unknown name is a "
                        "device's own business, not checked here."),
                "params": "str; the play verb's params argument.",
                "within_ms": "int.",
            },
        },
        "expect_quiet": {
            "role": ("expectation: none of these addresses may have a "
                     "send time in [t, t + for_ms)."),
            "fields": {
                "addresses": "list of str.",
                "for_ms": "int; width in ms of the window, starting at t.",
            },
        },
    },
}

# Plain-sentence replay rules a runner needs that the scenario files and
# step_schema together don't already state. Every entry here must be
# understandable from this export folder alone -- no entry points at a
# file this folder does not contain.
REPLAY_NOTES = [
    "A replay runner must not deliver a control_sends step while the "
    "scenario's link is down; those steps record what Control really "
    "sent, and a device with its link down receives none of them.",
    "A control_sends step flagged \"malformed\": true is delivered to "
    "the device and must be dropped without changing state.",
    "In timed_frames_hold_last, the two /$DEV/leds control_sends steps "
    "at t=6000 are hand-authored rather than captured from Control: the "
    "one with at=6200 (pure blue, GRB [0, 0, 255] repeated 12 times) is "
    "sent first, and the one with at=6100 (pure red, GRB [255, 0, 0] "
    "repeated 12 times) is sent second, so a runner that shows whichever "
    "arrived last would show the wrong one. Both are due by t=6500; the "
    "expect_frame steps at t=6500 and t=12000 both expect the newer one "
    "(at=6200, blue), because a device must select the frame with the "
    "newest presentation time, not the one that arrived most recently.",
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
    return {
        "address": row.address,
        "direction": row.direction,
        "typespecs": list(row.typespecs),
        "args": list(row.args),
        "transport": row.transport,
        "pre_role": row.pre_role,
        "notes": row.notes,
    }


def _scenario_names() -> list[str]:
    return [fn.__name__ for fn in ALL_SCENARIOS]


def _committed_recording_names() -> list[str]:
    return sorted(p.stem for p in RECORDINGS_DIR.glob("*.json"))


def _validated_scenario_names() -> list[str]:
    """The sorted scenario names, after checking that
    contract_kit.scenarios.ALL_SCENARIOS and contract_kit/recordings/ name
    exactly the same set. Exits loudly on any mismatch. Called from
    export_contract() itself (not just from main()'s file-writing path),
    so a mismatch is caught before this tool's payload is even built, let
    alone written -- no partial export is ever possible.
    """
    expected = set(_scenario_names())
    committed = set(_committed_recording_names())

    missing = sorted(expected - committed)
    if missing:
        sys.exit("contract_kit/recordings/ is missing a recording named "
                 f"by contract_kit.scenarios.ALL_SCENARIOS: "
                 f"{', '.join(missing)}")

    orphaned = sorted(committed - expected)
    if orphaned:
        sys.exit("contract_kit/recordings/ holds a recording no scenario "
                 f"in contract_kit.scenarios.ALL_SCENARIOS produces: "
                 f"{', '.join(orphaned)}")

    return sorted(expected)


def _scenario_index() -> list[dict]:
    """contract.json's "scenarios" key: name/profiles/summary per
    scenario, sorted by name, so a device repo can assert it holds the
    full committed set without globbing its own scenarios/ directory."""
    index = []
    for name in _validated_scenario_names():
        data = json.loads((RECORDINGS_DIR / f"{name}.json").read_text())
        index.append({
            "name": data["name"],
            "profiles": list(data["profiles"]),
            "summary": data["summary"],
        })
    return index


def export_contract(*, commit: str) -> dict:
    rev1 = load_catalog(REPO_ROOT / "instruments").published["tuneshroom_rev1"]
    verbs = sorted(
        (_verb_entry(row) for row in VERB_TABLE),
        key=lambda v: v["address"],
    )
    return {
        "_provenance": {"commit": commit, "tool": TOOL_VERSION},
        "contract_version": CONTRACT_VERSION,
        "verbs": verbs,
        "link": {
            "arg_types": ["b", "f", "i", "s"],
            "max_message_bytes": O2_MAX_MSG_LEN,
            "service_is_dev_id": True,
        },
        "limits": {
            "max_message_bytes": O2_MAX_MSG_LEN,
            "dev_id_max_len": MAX_DEV_LEN,
            "reserved_dev_ids": [TERRARIUM_ADMIN],
        },
        "lifecycle": {
            "hello_interval_s": HELLO_INTERVAL_S,
            "stale_timeout_s": _stale_timeout_s(),
            "lobby_double_tap_window_s": LobbyConfig().double_tap_window_s,
            "cue_horizon_s": CUE_HORIZON_S,
            "bench_tolerance_ms": dict(BENCH_TOLERANCE_MS),
        },
        "instruments": {
            "tuneshroom_rev1": {
                "instrument": carried_instrument_view(rev1),
                "triggers": {t.name: dict(t.thresholds)
                            for t in rev1.event_triggers},
            },
        },
        # _scenario_index() validates ALL_SCENARIOS against
        # contract_kit/recordings/ and exits loudly on a mismatch, so this
        # call (not main()'s writing code) is where an inconsistent
        # scenario set is caught -- before anything is written (Minor 4).
        "scenarios": _scenario_index(),
        "step_schema": STEP_SCHEMA,
        "replay_notes": list(REPLAY_NOTES),
    }


def _copy_scenarios(out_dir: Path) -> int:
    """Copy contract_kit/recordings/<name>.json byte for byte into
    <out_dir>/scenarios/, one file per name in ALL_SCENARIOS."""
    names = _validated_scenario_names()
    scenarios_dir = out_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        data = (RECORDINGS_DIR / f"{name}.json").read_bytes()
        (scenarios_dir / f"{name}.json").write_bytes(data)
    return len(names)


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

    # export_contract() validates the scenario set (via _scenario_index())
    # before returning, so a mismatch exits here, before out_dir has any
    # file written into it at all.
    data = export_contract(commit=commit)
    _write_json(out_dir / "contract.json", data)
    count = _copy_scenarios(out_dir)
    print(f"wrote {out_dir}/contract.json and {count} scenario files")


if __name__ == "__main__":
    main()
