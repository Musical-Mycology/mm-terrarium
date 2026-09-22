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

`contract.json` also carries `step_schema`, `scenarios`, `lifecycle_notes`
and `replay_notes`, because a device author who holds only this export
folder (not this repo) must be able to write a replay runner from it
alone. Nothing in those keys should ever point outside the export at a
file the folder does not contain, and every cross-reference between keys
must resolve inside the exported document itself (see
tests/test_export_contract.py's
test_every_dotted_cross_reference_resolves_in_the_export).
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
from devicelink.contract import HELLO_INTERVAL_S, VERB_TABLE, row_for
from devicelink.o2_transport import MAX_DEV_LEN
from devicelink.protocol import O2_MAX_MSG_LEN

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDINGS_DIR = REPO_ROOT / "contract_kit" / "recordings"
TOOL_VERSION = "export_contract/1"

# Bumped on any change a device can observe: a new/changed verb, a
# link/limits/lifecycle value, the tuneshroom_rev1 instrument, or a
# scenario's steps (spec section 4.2). A device repo pins this number in
# its own replay tests so an export that changes it cannot be adopted
# silently. This export has now been published (mm-tuneshroom committed
# it at test/contract/ and found two gaps replaying it -- mm-tuneshroom
# PR #29), so this fix wave's new "join" step kind and
# link_loss_keeps_display scenario bump it from 1 to 2; mm-tuneshroom
# updates its own guard and runner in a follow-up, not here.
CONTRACT_VERSION = 2

# The live bench replay's timing tolerances (spec section 4.2, "the
# tolerances the live bench replay uses"; section 8). No single constant
# in the code owns these numbers today -- the bench replay itself does
# not exist yet (docs/superpowers/plans/2026-09-16-bench-replay-feasibility.md
# is still a feasibility spike) -- so this is the contract kit's own
# published value, taken from the design spec's lifecycle example, not a
# figure read off running code.
BENCH_TOLERANCE_MS = {"frame": 50, "heartbeat": 1000}


def _hello_semantics_sentence() -> str:
    """A prose sentence for step_schema.kinds.link.semantics and
    lifecycle_notes.hello_interval_s, built from devicelink/contract.py's
    own hello row so it cannot drift from the verb table."""
    hello = row_for("up", "hello")
    bare = hello.typespecs[0]
    longest = hello.typespecs[-1]
    args = ", ".join(hello.args)
    return (f"On \"up\", the device sends /game/hello over "
            f"{hello.transport} (typespec \"{longest}\": {args}) once "
            f"the link is up, then repeats it every "
            f"`lifecycle.hello_interval_s` seconds while the link stays "
            f"up (spec section 4.3, rule 1). The verb table also allows "
            f"the bare \"{bare}\" form, but a device declares its "
            f"instrument in the fourth argument (\"{hello.args[-1]}\"), "
            f"and Rev 1 devices always send \"{longest}\". On \"down\", "
            f"the device sends nothing and receives nothing. There is "
            f"no session resume: after `lifecycle.stale_timeout_s` "
            f"seconds of silence, Control has dropped the device, which "
            f"must join again (spec section 4.3, rule 7).")


# What every step kind in every committed recording looks like: every
# field, its type, its unit, and what a placeholder value means. Verified
# against contract_kit/recorder.py's step-producing methods and against a
# scan of all eleven contract_kit/recordings/*.json files -- see
# tests/test_export_contract.py's
# test_step_schema_matches_the_recordings_exactly, which checks both
# directions: no recorded kind/field is undescribed, and no described
# kind/field is unused.
#
# Two shapes of "kinds" entry: one with a "fields" object describes a step
# whose payload is itself a JSON object, keyed the way "fields" lists; one
# with a "payload" string instead describes a step whose payload is a bare
# scalar (currently only "link", whose value is the string "up" or "down",
# not an object).
STEP_SCHEMA = {
    "notation": (
        "Each entry under \"kinds\" describes one step kind, the key "
        "other than \"t\" that a step object carries. A kind with a "
        "\"fields\" object has a JSON object as its payload, one entry "
        "described per key. A kind with a \"payload\" string instead has "
        "a bare scalar as its payload, not an object."
    ),
    "t": (
        "int; milliseconds since the scenario's own start. `steps` is "
        "sorted by `t`, ascending, stably: steps sharing one `t` are "
        "delivered and checked in file order. Within one `t`, a "
        "captured control_sends step keeps its real relative order "
        "among other captured control_sends steps; a runner should "
        "otherwise use the file order across kinds -- an input step "
        "(gesture, join) always precedes its own expect_out at the same "
        "t, the same way a gesture does."
    ),
    "tolerance": (
        "In-process replay runs on a fake clock and allows an "
        "expect_frame to land up to one render tick (23 ms) late. "
        "expect_out and expect_play are instead checked against their "
        "own within_ms window, which starts at the step's own t. A live "
        "bench replay uses this export's `lifecycle.bench_tolerance_ms` "
        "instead of either."
    ),
    "link_down_delivery": (
        "While the most recently seen link step is \"down\", a replay "
        "runner must not deliver any control_sends step to the device. "
        "Those steps record what Control really sent while recording, "
        "and a device whose link is down receives none of them."
    ),
    "placeholders": {
        "$DEV": {
            "meaning": (
                "the replaying device's own dev id, substituted for "
                "the id used while recording."
            ),
            "appears_in": ["control_sends.address", "expect_out.args"],
        },
        "$KEY": {
            "meaning": (
                "a join chime's key number, substituted because the "
                "contract does not pin the number. Always embedded in "
                "a larger string as \"key=$KEY\" (for example replacing "
                "\"key=60\"). A runner matches the shape key=<integer> "
                "and does not compare the integer itself."
            ),
            "appears_in": ["control_sends.args", "expect_play.params"],
        },
        "*": {
            "meaning": "matches any value.",
            "appears_in": ["expect_out.args"],
        },
    },
    "scenario_fields": {
        "name": "str; snake_case, equal to the file's own stem.",
        "summary": "str; one line describing what the scenario covers.",
        "profiles": (
            "list of str; the device profiles this scenario must pass "
            "in (\"rev1\" and/or \"any\")."
        ),
        "device": (
            "object, {\"join_node\": str or null}. When join_node is "
            "non-null, the device sends /game/join [\"$DEV\", "
            "join_node] right after its first hello on every link-up."
        ),
        "steps": "list of step objects (see \"kinds\" below).",
    },
    "kinds": {
        "link": {
            "role": "input: the O2 link's state changes.",
            "payload": "string, one of \"up\" or \"down\".",
            "semantics": _hello_semantics_sentence(),
        },
        "control_sends": {
            "role": (
                "input: a message Control really sent while "
                "recording; deliver it to the device (subject to "
                "`step_schema.link_down_delivery` above)."
            ),
            "fields": {
                "address": "str; \"/$DEV/<verb>\".",
                "typespec": "str; the O2 typespec the message was sent with.",
                "args": (
                    "list; decoded arguments -- role and room args[0] "
                    "is a JSON object, leds args[0] is a list of 36 "
                    "ints (0-255), GRB, 3 per pixel, 12 pixels. A "
                    "string argument may carry the $KEY placeholder "
                    "(see placeholders)."
                ),
                "at": (
                    "int ms on the same t timeline, or null; the "
                    "presentation time the message carries. Only "
                    "/leds ever carries one -- every other verb's at "
                    "is null, meaning it has no presentation time, "
                    "not that it is due at t=0."
                ),
                "malformed": (
                    "bool; present and true only on a hand-authored "
                    "step a device must drop without changing any "
                    "state. Absent (not false) on every message "
                    "actually captured from Control."
                ),
            },
        },
        "gesture": {
            "role": (
                "input: a classified gesture delivered to the device "
                "session at t."
            ),
            "fields": {
                "kind": "str; \"tap\", \"hold\" or \"swing\".",
                "onset_t": (
                    "int ms; when the gesture began (equals the "
                    "step's own t)."
                ),
                "duration_ms": "float; tap only -- how long the touch lasted.",
                "held_s": (
                    "float; hold only -- seconds the touch was held "
                    "before release."
                ),
                "signed_g": (
                    "float; swing only -- peak acceleration in g, "
                    "negative means left."
                ),
            },
        },
        "join": {
            "role": (
                "input: the device deciding to join \"node\", LATER than "
                "its link coming up (a join AT link-up is instead the "
                "scenario's own device.join_node field -- see "
                "step_schema.scenario_fields -- delivered when the link "
                "comes up). A runner delivers this as an input at t and "
                "must not infer a join from the expect_out that follows "
                "it -- an expect_out checks what the device under test "
                "sends, and cannot also be the runner's own cue to send "
                "it."
            ),
            "fields": {
                "node": "str; the node name this join names.",
            },
        },
        "expect_out": {
            "role": (
                "expectation: the device must send this message with "
                "a send time in [t, t + within_ms]."
            ),
            "fields": {
                "address": "str; \"/game/<verb>\".",
                "typespec": "str.",
                "args": (
                    "list; each element is a literal (numbers match "
                    "within 1e-3), \"$DEV\", or \"*\" (see "
                    "placeholders)."
                ),
                "stamp_t": (
                    "int ms or null; the O2 timestamp the outbound "
                    "message must carry, within 1 ms. null means the "
                    "timestamp is not checked."
                ),
                "within_ms": (
                    "int; width in ms of the send-time window, "
                    "starting at t."
                ),
            },
        },
        "expect_frame": {
            "role": (
                "expectation: the pixels showing at t (see tolerance "
                "above)."
            ),
            "fields": {
                "grb": (
                    "list of 36 ints, 0-255: 12 pixels x 3 channels, "
                    "green-red-blue order."
                ),
            },
        },
        "expect_play": {
            "role": (
                "expectation: a play effect with this name and "
                "params is emitted in [t, t + within_ms]."
            ),
            "fields": {
                "name": (
                    "str; the sample name. An unknown name is a "
                    "device's own business, not checked here."
                ),
                "params": (
                    "str; the play verb's params argument. May carry "
                    "the $KEY placeholder (see placeholders)."
                ),
                "within_ms": "int.",
            },
        },
        "expect_quiet": {
            "role": (
                "expectation: none of these addresses may have a "
                "send time in [t, t + for_ms)."
            ),
            "fields": {
                "addresses": "list of str.",
                "for_ms": "int; width in ms of the window, starting at t.",
            },
        },
    },
}

# One-sentence prose companions to contract.json's "lifecycle" values,
# keyed by the same names, each stating what the number means and its
# unit. Checked against the code or the design spec, not guessed:
# hello_interval_s and stale_timeout_s against devicelink/contract.py and
# control/boot_config.py; lobby_double_tap_window_s against
# control/lobby.py's DoubleTapDetector; cue_horizon_s against
# control/boot_config.py's own BootConfig.cue_horizon comment and
# contract_kit/recorder.py's CUE_HORIZON_S docstring; bench_tolerance_ms
# against BENCH_TOLERANCE_MS's own comment above.
LIFECYCLE_NOTES = {
    "hello_interval_s": (
        "Seconds between a device's /game/hello resends while its link "
        "stays up (see `step_schema.kinds.link.semantics`)."
    ),
    "stale_timeout_s": (
        "Seconds of silence after which Control has dropped a device, "
        "which must join again on its next hello (see "
        "`step_schema.kinds.link.semantics`)."
    ),
    "lobby_double_tap_window_s": (
        "Seconds within which two count-1 taps from the same device in "
        "the lobby are paired into a join; a single tap already carrying "
        "count >= 2 joins immediately regardless of this window."
    ),
    "cue_horizon_s": (
        "Seconds of lead time Control adds when it schedules a cue, "
        "becoming the presentation time (`at`) on the /leds message the "
        "device receives. A captured /leds control_sends step is stamped "
        "about one cue horizon after it is sent; the hand-authored steps "
        "named in `replay_notes` carry other leads. A runner must always "
        "use each step's own `at` and never compute it."
    ),
    "bench_tolerance_ms": (
        "Milliseconds of slack a live bench replay uses instead of "
        "`step_schema.tolerance`: frame for how late an expect_frame's "
        "pixels may still appear, heartbeat for how late a /game/hello "
        "resend may still arrive."
    ),
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
    "A join step is the device deciding to join, later than link-up; a "
    "runner delivers it as an input to the device under test and must "
    "not instead infer a join from the expect_out that follows it -- see "
    "`step_schema.kinds.join`.",
    "In link_loss_keeps_display, the expect_frame step at t=8000 (inside "
    "the link-down window that runs from t=2000 to t=17000) is "
    "hand-authored, like the pair in timed_frames_hold_last: it repeats "
    "the same pixels as the expect_frame at t=2000, the moment just "
    "before the link fell, because during a link loss the device hears "
    "none of whatever Control goes on sending (see "
    "`step_schema.link_down_delivery`) and a runner must not compute "
    "this one from a later control_sends step the way an ordinary "
    "expect_frame would.",
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
    exactly the same set. Exits loudly on any mismatch. Callers that
    already hold a validated name list (main()) pass it straight to
    export_contract() and _copy_scenarios() instead of calling this
    again, so one export run validates exactly once.
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


def _scenario_index(names: list[str]) -> list[dict]:
    """contract.json's "scenarios" key: name/profiles/summary per
    scenario, sorted by name, so a device repo can assert it holds the
    full committed set without globbing its own scenarios/ directory."""
    index = []
    for name in names:
        data = json.loads((RECORDINGS_DIR / f"{name}.json").read_text())
        index.append({
            "name": data["name"],
            "profiles": list(data["profiles"]),
            "summary": data["summary"],
        })
    return index


def export_contract(*, commit: str,
                    scenario_names: list[str] | None = None) -> dict:
    """The contract.json payload. `scenario_names`, if given, must already
    be validated (see _validated_scenario_names) -- passing it lets a
    caller that already validated once (main()) skip doing so again.
    Called with no `scenario_names` (as every test does), this function
    validates the scenario set itself before returning, so a mismatch is
    caught before any payload is built, let alone written.
    """
    names = (scenario_names if scenario_names is not None
             else _validated_scenario_names())
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
        "lifecycle_notes": dict(LIFECYCLE_NOTES),
        "instruments": {
            "tuneshroom_rev1": {
                "instrument": carried_instrument_view(rev1),
                "triggers": {t.name: dict(t.thresholds)
                            for t in rev1.event_triggers},
            },
        },
        "scenarios": _scenario_index(names),
        "step_schema": STEP_SCHEMA,
        "replay_notes": list(REPLAY_NOTES),
    }


def _copy_scenarios(out_dir: Path, names: list[str]) -> int:
    """Copy contract_kit/recordings/<name>.json byte for byte into
    <out_dir>/scenarios/, one file per name in `names` (already
    validated by the caller)."""
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

    # Validate exactly once here, before out_dir has any file written
    # into it. export_contract() and _copy_scenarios() both take the
    # already-validated list instead of re-checking it themselves.
    names = _validated_scenario_names()
    data = export_contract(commit=commit, scenario_names=names)
    _write_json(out_dir / "contract.json", data)
    count = _copy_scenarios(out_dir, names)
    print(f"wrote {out_dir}/contract.json and {count} scenario files")


if __name__ == "__main__":
    main()
