# Contract kit: join step and link-loss display Implementation Plan

**Goal:** Close the two gaps mm-tuneshroom's replay of the device contract
export found (mm-tuneshroom [PR #29](https://github.com/Musical-Mycology/mm-tuneshroom/pull/29)):
a late join has no `join` input step to deliver, only an `expect_out` to
infer one from; and no scenario pins a rev1 device's display holding
through a link loss with a role held. Both are additive, both are
observable by a device, so `contract_version` bumps from 1 to 2.

**Architecture:** No new modules. `contract_kit/recorder.py`'s `join_now`
gains one more recorded step (`join`); its `expect_frame` is split into a
shared `_frame_showing_at` lookup plus a new `expect_frame_held`, for a
frame expectation inside a link-down window that must not be computed from
whatever Control keeps sending into a link the device cannot hear.
`contract_kit/scenarios.py` gains a twelfth scenario,
`link_loss_keeps_display`, built from those two primitives.
`tools/export_contract.py`'s `STEP_SCHEMA`/`REPLAY_NOTES` document the new
step kind and the new scenario's hand-authored expectation, and
`CONTRACT_VERSION` becomes 2. The spec
(`docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`) gains
rule 8 and a twelfth scenario row; `docs/MM_TERRARIUM.md` and
`docs/device-contract-guide.md` are brought back in line with the new state
(the guide had already named both gaps as open items 1 and 4).

**Tech Stack:** Python 3 (stdlib + pytest), the existing `contract_kit`/
`devicelink`/`tools` packages, `luxaeterna` (sibling checkout, already a
test-only dependency via `pytest.importorskip`). No new third-party
dependencies.

**Spec:** `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`
sections 4.2, 4.3 (rules, the scenario table) and 5.4 (recorder). The
context that motivated this plan: mm-tuneshroom
`docs/superpowers/specs/2026-09-21-device-session-and-replay-design.md`
section 6 (rule 6, "late join inferred") and section 8 (follow-ups 1 and 4).

## Brainstormed decisions (recorded here since no interactive session was
available to brainstorm live; each is the recommended option, with cost)

1. **`join` step payload shape.** An object, `{"node": str}` (recommended:
   symmetry with `gesture`'s object payload; cost: one more field to
   document versus a bare string). Decided: object.
2. **Is "keeps display" a rev1-only rule?** Yes (recommended: the app's
   full profile already falls back to ambient light once no role is held,
   which is different from holding dead pixels; tagging it `rev1` in the
   spec's rule 8 matches that). The companion rule, "a lost link ends the
   role," is general (every profile).
3. **Outage-window `expect_frame` mechanism.** Hand-authored via a new
   `Recorder.expect_frame_held(t_ms, since_t_ms)`, not a plain
   `expect_frame(t_ms)` call (recommended: `expect_frame`'s "newest
   control_sends with at <= t" search would depend on whatever Control
   keeps sending into a dead link, which is not the fact being pinned, and
   would silently start being wrong if the engine's stale-timeout timing
   ever changed). Verified empirically: Control stays quiet from t=2000 to
   t=15018 in this recording, so a plain call would have happened to agree
   today, but not on that guarantee.
4. **Twelfth scenario name and timings.** `link_loss_keeps_display` (given
   in the task); timings mirror `link_loss_rejoin` exactly (down at 2000,
   up at 17000) so the reap/rejoin behavior is directly comparable, with
   an outage check at t=8000 (comfortably before the t=15018 reap fade
   Control's own engine starts, confirmed by recording it).

## Task 1: `join` input step (`contract_kit/recorder.py`)

**Files:** Modify `contract_kit/recorder.py`, `tools/export_contract.py`;
update `contract_kit/recordings/error_no_state_change.json`,
`contract_kit/recordings/gestures_after_role.json` (re-recorded, not
hand-edited); update `tests/test_contract_recorder.py`,
`tests/test_contract_scenarios.py`, `tests/test_export_contract.py`.

- [x] `Recorder.join_now` appends `{"t": t_ms, "join": {"node": node}}`
  before scripting the send, so it precedes its own `expect_out` at the
  same `t` (the same ordering `_gesture` already gives gesture/expect_out
  pairs).
- [x] `tools/export_contract.py`'s `STEP_SCHEMA["kinds"]["join"]` documents
  the input, with a `fields.node` entry; the `"t"` note gets one clause
  generalizing "an input step (gesture, join) precedes its own expect_out."
  `REPLAY_NOTES` gains a plain-sentence statement that a runner delivers
  `join` as an input and must not infer one from an `expect_out`.
- [x] `.venv/bin/python -m tools.record_scenarios`; diff reviewed --
  exactly the two affected scenarios each gain one `join` step, nothing
  else moves (byte-identical everywhere else, proving determinism held).
- [x] Recorder unit test (`test_join_now_joins_later_and_leaves_device_join_node_alone`)
  updated to assert the new step and its ordering; scenario tests for
  `error_no_state_change` and `gestures_after_role` assert it too; a new
  export test asserts `step_schema.kinds.join`'s shape.

## Task 2: `link_loss_keeps_display` scenario

**Files:** Modify `contract_kit/recorder.py` (`_frame_showing_at`,
`expect_frame_held`), `contract_kit/scenarios.py` (new scenario + registry
entry), `tools/export_contract.py` (`CONTRACT_VERSION`, `REPLAY_NOTES`);
new `contract_kit/recordings/link_loss_keeps_display.json`; update
`tests/test_contract_recorder.py`, `tests/test_contract_scenarios.py`,
`tests/test_export_contract.py`.

- [x] Factor `Recorder.expect_frame`'s selection logic into
  `_frame_showing_at(t_ms)`; add `expect_frame_held(t_ms, since_t_ms)` that
  looks up the frame as of `since_t_ms` but records the expectation at
  `t_ms` -- so an outage-window check cannot pick up a later, undelivered
  send.
- [x] `link_loss_keeps_display`: join explicitly (via `join_node` at
  construction, like `explicit_join_role`), receive role + settled look at
  `SIGNATURE_SETTLED_MS`, `link: down` at the same instant, `expect_quiet`
  for hello/hold/swing across the whole outage, `expect_frame_held` at
  t=8000 (`since_t_ms=SIGNATURE_SETTLED_MS`), `link: up` at t=17000 (past
  `lifecycle.stale_timeout_s`), fresh `expect_out` hello + join, and the
  fresh role's settled frame at t=19000.
- [x] Add to `ALL_SCENARIOS`. `CONTRACT_VERSION` -> 2.
  `REPLAY_NOTES` gains the hand-authoring note naming this scenario.
- [x] `.venv/bin/python -m tools.record_scenarios`; new recording reviewed
  against a throwaway interactive run of the same steps.
- [x] Recorder unit tests for `expect_frame_held` (repeats the pre-outage
  frame, ignores a later undelivered send; raises when nothing had
  reached the device yet). Dedicated scenario test asserting links, hello
  cadence, the rejoin pair, the outage frame, and the quiet window.

## Task 3: Docs

**Files:** `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`,
`docs/MM_TERRARIUM.md`, `docs/device-contract-guide.md`.

- [x] Spec 4.3: `join` input bullet, rule 8, the twelfth scenario row, the
  hand-authoring note, and the `contract_version` 1->2 note with the
  Victor-confirmation caveat (edited in place, following the house style
  already used for this spec's small corrections -- see `bd82e07`).
- [x] `docs/MM_TERRARIUM.md`'s device contract kit deep-dive entry: scenario
  count, a new dated bullet for this fix wave, and the updated suite pass
  count (including the two pre-existing, unrelated `test_terrarium_boot.py`
  failures, stated as such rather than omitted).
- [x] `docs/device-contract-guide.md`: this guide is what named both gaps
  as open items 1 and 4, so it is brought back in line rather than left to
  say a fixed thing is still missing -- `contract_version`, scenario
  counts, the "late join inference" recipe, and section 8's "not pinned by
  any scenario" claims are all updated in place, each marked RESOLVED
  where a firmware author would otherwise read stale guidance.

## Gates (verified before push)

- [x] `.venv/bin/python -m pytest` -- full suite green except two
  pre-existing, unrelated failures in `tests/test_terrarium_boot.py`
  (confirmed present on `origin/main` before any change in this plan).
- [x] `tests/test_contract_scenarios.py`, `tests/test_contract_recorder.py`,
  `tests/test_export_contract.py`, `tests/test_contract_bit.py`,
  `tests/test_devicelink_contract.py` green in isolation.
- [x] Recorder regression test (`test_recording_matches_committed_json`)
  green against the newly committed recordings.
- [x] `tests/test_export_contract.py::test_every_dotted_cross_reference_resolves_in_the_export`
  green (every new backticked reference resolves).
- [x] Sample export generated to a scratch directory and read back: 12
  scenario files, `contract_version` 2, `step_schema.kinds.join` present.

## Follow-up (not done here, by design)

mm-tuneshroom re-exports into its own `test/contract/`, updates its
`contract_version` guard to 2, drops its runner's late-join inference, and
adds delivery of the `join` input step. That is a separate PR in that repo.
