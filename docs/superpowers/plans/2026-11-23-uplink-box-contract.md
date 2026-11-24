# Uplink box contract: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The box speaks the uplink contract the deployed fairyring broker requires, so a real Terrarium can connect, stay connected, have every completion credited once, and take admin-device manifests.

**Architecture:** All changes sit in `uplink/` plus one small pure-stdlib store in `control/` and the boot wiring. `UplinkAgent` keeps its plain tick-loop style: timers hang off `maintain_connection()` using the injected `time_source`. The journal becomes confirm-to-remove. The applied admin list lives in its own JSON file next to `terrarium.toml`, which the box never rewrites.

**Tech Stack:** Python, `websockets>=13` sync client, pytest, `FakeTransport`.

**Spec:** `mm-fairyring/docs/superpowers/specs/2026-11-23-box-contract-design.md`, sections 4 and 5.3 (local path `~/projects/mm-fairyring/.claude/worktrees/determined-northcutt-dc286e/docs/superpowers/specs/2026-11-23-box-contract-design.md` until it merges). It amends `docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md` and section 6 of `docs/superpowers/specs/2026-09-13-mycoquest-handoff-terrarium-design.md`, and settles issues 100 and 127.

## Global Constraints

Exact values, copied from the spec:

- Handshake headers: `X-Fairyring-Tenant`, `X-Fairyring-Terrarium`, `X-Fairyring-Secret`. No `identity` frame is ever sent.
- Heartbeat: exactly `{"event": "heartbeat"}`, no other keys, every 60 seconds while connected, first one 60 seconds after connect, never journalled.
- Every other up-frame carries `boot_id` (string, random, one per process) and `seq` (integer from 1 within the `boot_id`). `seq` is assigned only after the size check passes and only to a frame that reaches the wire or the journal. A replayed completion keeps its original `boot_id` and `seq`.
- `bit_completed` carries `completion_id` (uuid4, lower-case hyphenated string) and `completed_at` (UTC, RFC 3339, `Z` suffix).
- Size limit: `MAX_FRAME_BYTES = 32000`, measured as `len(wire_json.dumps(frame).encode("utf-8"))` on the stamped frame. Oversize `bit_completed`: `result` becomes `None` and `"result_dropped": True` is added, then re-measured; still oversize means dropped and logged. Any other oversize frame is dropped and logged.
- Resync order: `state_changed` (with `lan_ip`), `room_loaded` if any, `registration_changed` if any, `admin_devices_applied {version}` always (0 when none), then every journal entry.
- Journal: an entry leaves only by `completions_stored` or the 500 cap. Replay happens only over a durable transport and removes nothing.
- `session_replaced`: close, warn, notify, wait 60 seconds; each further one doubles to a cap of 600; back to 60 once a connection has lasted 300 seconds. The ordinary 1 to 30 second back-off is unchanged for every other drop.
- `set_admin_devices`: applied only when `schema == 1`, `tenant_slug` and `terrarium_name` equal the box's own, `version` is an int greater than the applied version, and no device (any status) has `gem_uid == "terrarium"`. Only devices with `status == "active"` become admins. Not-greater version: silent no-op. Any other failure: `error` event, nothing changes.
- `terrarium.toml` is never written by the box.
- Secrets never appear in logs, reprs or markers.
- All work happens in the worktree `~/projects/mm-terrarium/.claude/worktrees/box-contract` on branch `claude/box-contract`. Nothing is pushed or merged without Chris's go-ahead.
- Tests: `python -m pytest tests/<file> -q` per task, and `terrarium-dev test` (see README line 113) before the final commit of each task that touches `harness/`.

## Review Focus

- A `completions_stored` whose `completion_ids` is not a list of strings (a string, `null`, numbers inside): dropped as unparseable, journal untouched. (Task 2, Task 4.)
- A journal file holding a corrupt line or an entry with no `completion_id` (written by an older build): skipped with a warning, never replayed, never blocks the rest. (Task 3.)
- The link drops between `journal.append` and the send, or mid-replay: the entry is still in the journal and is sent on the next connect. (Task 4.)
- The applied admin file is missing, empty or corrupt at boot: the box boots with the `terrarium.toml` list and applied version 0, and logs once. (Task 6.)
- The operator's clock jumps: every timer uses the injected monotonic `time_source`; only `completed_at` reads the wall clock. (Task 4, Task 5.)

---

### Task 1: Transport: headers, close, frame size

**Model:** sonnet.

**Files:**
- Modify: `uplink/transport.py`
- Test: `tests/test_websocket_transport.py`, `tests/test_transport.py`

**Interfaces:**
- Produces:
  - `WebSocketTransport(uri: str, headers: dict[str, str] | None = None)`; `connect()` passes `additional_headers=headers`.
  - `close() -> None` on `WebSocketTransport`, `FakeTransport` and `LogTransport`: sets `connected = False`, closes the socket if any, never raises. `FakeTransport` counts calls in `close_count`.
  - `frame_size(msg: dict) -> int` (module function): `len(control.wire_json.dumps(msg).encode("utf-8"))`.
  - `LogTransport.send` no longer needs the `secret` redaction (no frame carries it); remove it.

- [ ] **Step 1: Write the failing tests**: a local `websockets.sync.server` handler that records `connection.request.headers` proves the three headers arrive; `close()` then `connected is False` and a second `close()` does not raise; `frame_size({"a": "é"})` equals the encoded length; `WebSocketTransport(uri)` with no headers still connects.
- [ ] **Step 2: Run** `python -m pytest tests/test_websocket_transport.py tests/test_transport.py -q`. Expected: new tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(uplink): handshake headers, close and frame size on the transport`.

### Task 2: Protocol: new builders and commands

**Model:** sonnet.

**Files:**
- Modify: `uplink/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Removes: `identity_frame`. `UplinkIdentity` stays and gains `headers() -> dict[str, str]` returning the three `X-Fairyring-*` headers.
- Produces:
  - `heartbeat_event() -> dict` returning `{"event": "heartbeat"}`.
  - `admin_devices_applied_event(version: int) -> dict`.
  - `bit_completed_event(..., completion_id: str | None = None, completed_at: str | None = None)`: two new keyword arguments; each key is on the event only when its value is not `None`, so `console/` callers are untouched. Task 4 makes the uplink always pass both.
  - `@dataclass SetAdminDevicesCommand(manifest: dict)` (the whole message minus `command`), `@dataclass CompletionsStoredCommand(completion_ids: tuple[str, ...])`, `@dataclass SessionReplacedCommand`.
  - `parse_command` handles `set_admin_devices` (any dict passes parsing; content checks are the agent's), `completions_stored` (`completion_ids` must be a list of non-empty strings, else `ValueError`), `session_replaced`.

- [ ] **Step 1: Write the failing tests**: each builder's exact dict; `UplinkIdentity.headers()` exact dict and `repr` still hides the secret; `parse_command` for the three commands; `completions_stored` with `"x"`, `None`, `[1]`, `[""]` each raises `ValueError`; an empty list parses to `()`.
- [ ] **Step 2: Run** `python -m pytest tests/test_protocol.py -q`. Expected: new tests fail; the old `identity_frame` test is deleted in this step.
- [ ] **Step 3: Implement.** Remove the `identity_frame` call in `uplink/link.py` in this task too (delete the line and its test assertions) so the suite stays green.
- [ ] **Step 4: Run** `python -m pytest tests/test_protocol.py tests/test_console_protocol.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(uplink): protocol for heartbeat, completion ids and the three new commands`.

### Task 3: Journal: remove by completion id

**Model:** sonnet.

**Files:**
- Modify: `uplink/journal.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Produces:
  - `Journal.remove(completion_ids: Iterable[str]) -> int`: rewrites the file without entries whose `completion_id` is in the set; returns how many were removed; unknown ids are ignored; a missing file returns 0. Written atomically (temp file in the same directory, then `os.replace`).
  - `Journal.entries()` now also skips, with one warning per line, any parsed entry that is not a dict with a non-empty string `completion_id`.
  - `Journal.purge_unidentified() -> int`: rewrites the file keeping only entries `entries()` would return; returns how many lines were dropped.
- `clear()` and `is_empty()` stay (tests use them); nothing on the replay path calls `clear()` after Task 4.

- [ ] **Step 1: Write the failing tests**: remove one of three keeps the other two in order; remove an unknown id returns 0 and leaves the file byte-identical; remove on a missing file returns 0; an entry without `completion_id` and a corrupt line are skipped by `entries()` and dropped by `purge_unidentified()`; the cap still keeps the newest 500.
- [ ] **Step 2: Run** `python -m pytest tests/test_journal.py -q`. Expected: new tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(uplink): journal entries leave by confirmation`.

### Task 4: Agent core: stamping, size rule, resync, heartbeat, confirmed replay

**Model:** opus. This is the task with the most interacting rules.

**Files:**
- Modify: `uplink/link.py`
- Test: `tests/test_link.py`

**Interfaces:**
- Consumes: Task 1 `frame_size`, `close`; Task 2 builders and commands; Task 3 `remove`, `purge_unidentified`.
- Produces, on `UplinkAgent`:
  - New keyword-only constructor arguments: `boot_id: str | None = None` (default `uuid.uuid4().hex`), `id_source=uuid.uuid4`, `wall_clock=lambda: datetime.now(timezone.utc)`, `admin_version=lambda: 0` (Task 6 replaces the default at boot).
  - Constants: `HEARTBEAT_SECONDS = 60.0`, `MAX_FRAME_BYTES = 32000`.
  - `_stamp(msg: dict) -> dict | None`: returns a copy with `boot_id` and the next `seq`, or `None` when it is oversize after the `bit_completed` strip rule; the counter advances only when it returns a frame.
  - `_send(msg)` stamps then sends; when disconnected it returns without stamping. Heartbeats bypass `_stamp`.
  - `maintain_connection()` no longer returns early when connected: it calls `_tick_connected(now)`, which sends a heartbeat when 60 seconds have passed since the connect or the last heartbeat.
  - `self._connected_at: float | None`, set on each successful connect (Task 5 reads it).

- [ ] **Step 1: Update existing tests** that assert on exact sent dicts: add a helper `strip(frames)` that removes `boot_id` and `seq`, and use it where the old assertions compare whole frames. Delete assertions about the identity frame. `make_agent()` gains fixed `boot_id="boot-1"`, an `id_source` yielding `uuid.UUID(int=n)` and a fixed `wall_clock`.
- [ ] **Step 2: Write the failing tests.**

```python
def test_no_identity_frame_is_sent_on_connect()
def test_resync_order_ends_with_admin_devices_applied_then_journal()
    # sent events == ["state_changed", "room_loaded"?, "registration_changed"?,
    #                 "admin_devices_applied", "bit_completed", ...]; version 0 by default
def test_every_frame_but_heartbeat_carries_boot_id_and_gapless_seq()
def test_heartbeat_is_exact_and_sent_every_60_seconds()
    # time_source 59.9 -> none; 60.0 -> [{"event": "heartbeat"}]; 119.9 -> none; 120.0 -> second
def test_heartbeat_timer_restarts_on_reconnect()
def test_bit_completed_carries_completion_id_and_completed_at()
    # completion_id == str(uuid.UUID(int=1)); completed_at == "2026-11-23T12:00:00Z"
def test_completion_is_journalled_with_its_stamp_before_the_send()
def test_frame_not_sent_while_disconnected_consumes_no_seq()
def test_completion_while_disconnected_is_journalled_and_stamped()
def test_replay_sends_journal_entries_with_original_boot_id_and_seq()
    # journal written by boot "old" seq 7; new agent boot "boot-1" replays it unchanged
def test_replay_does_not_clear_the_journal()
def test_completions_stored_removes_only_those_entries()
def test_completions_stored_with_unknown_id_is_ignored()
def test_malformed_completions_stored_leaves_journal_untouched()
def test_replay_skipped_on_non_durable_transport()
def test_oversize_bit_completed_is_sent_with_result_dropped()
    # result = "x" * 40000 -> sent frame has result None, result_dropped True, size <= 32000
def test_bit_completed_still_oversize_after_strip_is_dropped_and_not_journalled()
    # players list large enough to exceed the limit alone
def test_oversize_other_frame_is_dropped_and_consumes_no_seq()
    # bits_listed with a 40000-byte entry; next frame's seq is contiguous
def test_unidentified_journal_entries_are_purged_at_construction()
def test_send_failure_right_after_connect_still_backs_off()   # existing behaviour, re-pinned
```

- [ ] **Step 3: Run** `python -m pytest tests/test_link.py -q`. Expected: the new tests fail.
- [ ] **Step 4: Implement.** `completed_at` format: `wall_clock().strftime("%Y-%m-%dT%H:%M:%SZ")`. `_emit_bit_completed` stamps first, journals the stamped frame, then sends it without re-stamping. `_replay_journal` sends each entry as stored and never clears. `_dispatch` handles `CompletionsStoredCommand` by `journal.remove(...)` when a journal is set. `SetAdminDevicesCommand` and `SessionReplacedCommand` are parsed but, until Tasks 5 and 6, logged at info and ignored.
- [ ] **Step 5: Run** `python -m pytest tests/test_link.py tests/test_journal.py tests/test_protocol.py -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(uplink): stamped frames, heartbeat, size rule and confirmed journal replay`.

### Task 5: `session_replaced` back-off

**Model:** sonnet.

**Files:**
- Modify: `uplink/link.py`
- Modify: `harness/markers.py` (add `SESSION_REPLACED = "SESSION_REPLACED:"` with a comment in the file's style)
- Test: `tests/test_link.py`

**Interfaces:**
- Consumes: Task 4 `_connected_at`, `_tick_connected`.
- Produces: `UplinkAgent` keyword `notify=None` (a callable taking one string; Task 7 wires it to print the marker); constants `REPLACED_INITIAL_SECONDS = 60.0`, `REPLACED_MAX_SECONDS = 600.0`, `STABLE_SECONDS = 300.0`; method `_on_replaced(now)`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_session_replaced_closes_and_waits_60_seconds()
    # after the command: transport.connected False, close_count 1, no connect at +59.9, connect at +60
def test_repeated_replacement_doubles_to_a_600_second_cap()
    # waits 60, 120, 240, 480, 600, 600
def test_replaced_wait_resets_after_300_seconds_connected()
def test_ordinary_drop_keeps_the_one_second_backoff()
def test_session_replaced_warns_and_notifies_without_the_secret()
    # caplog has one WARNING containing "another box is using this identity";
    # notify called once with a string naming tenant and terrarium, not the secret
```

- [ ] **Step 2: Run.** Expected: new tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4 (conditional): the lost-frame fallback.** Build this step only if `mm-fairyring/docs/followups/platform-proof.md` records that `session_replaced` was delivered in fewer than 20 of 20 tries; the controlling session tells you which. Rule: a connection closed by the server within 60 seconds of `_connected_at`, twice in a row, calls `_on_replaced(now)`. Tests: `test_two_quick_server_closes_count_as_replacement`, `test_one_quick_close_does_not`, `test_a_close_after_60_seconds_resets_the_count`.
- [ ] **Step 5: Run** `python -m pytest tests/test_link.py -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(uplink): long back-off when another box takes this identity`.

### Task 6: Admin devices: applied store, engine setter, command handling

**Model:** opus.

**Files:**
- Create: `control/admin_devices_store.py` (pure stdlib)
- Modify: `control/engine.py` (`GameServer`)
- Modify: `uplink/link.py`
- Test: `tests/test_admin_devices_store.py` (new), `tests/test_link.py`, the existing engine admin test file (find with `grep -ln is_admin tests/`)

**Interfaces:**
- Produces:
  - `APPLIED_FILENAME = "admin_devices.applied.json"`; file content `{"version": <int>, "devices": ["<gem_uid>", ...]}`.
  - `class AdminDevicesStore(path: str)` with `version -> int` (0 when no valid file), `devices -> tuple[str, ...] | None` (`None` when no valid file), `save(devices: Sequence[str], version: int) -> None` (atomic: temp file then `os.replace`; raises `OSError` on failure). A missing, empty or malformed file reads as no file and logs one warning when malformed.
  - `GameServer.set_admin_devices(devices: Iterable[str]) -> None`: replaces `self.admin_devices` with `frozenset(devices) | {TERRARIUM_ADMIN}`.
  - `UplinkAgent` keyword `admin_store=None`. When set, `admin_version` defaults to `lambda: admin_store.version`.

- [ ] **Step 1: Write the failing store tests**: round trip; missing file is version 0 and `devices is None`; `{}`, `[]`, `{"version": "7"}`, truncated JSON each read as no file; `save` leaves no temp file behind; a `save` into a missing directory raises `OSError` and leaves nothing.
- [ ] **Step 2: Write the failing agent tests** (a manifest helper builds the issue 100 payload for tenant `mm`, terrarium `main-stage`).

```python
def test_valid_manifest_applies_active_devices_and_reports_version()
    # devices [A active, B lost] v7 -> gs.is_admin("A"), not gs.is_admin("B"), gs.is_admin("terrarium");
    # store.version == 7, store.devices == ("A",); sent admin_devices_applied {version: 7}
def test_manifest_with_no_devices_leaves_only_the_terrarium_admin()
def test_same_or_older_version_is_a_silent_noop()        # nothing sent, store untouched
@pytest.mark.parametrize(...)   # each sends error(command="set_admin_devices") and changes nothing:
def test_refused_manifest(...)
    # schema 2; wrong tenant_slug; wrong terrarium_name; version "7"; version True;
    # devices not a list; a device with gem_uid "terrarium" and status "lost";
    # a device missing gem_uid; no admin_store configured
def test_failed_save_sends_error_and_keeps_the_old_admins()
def test_resync_reports_the_stored_version()
```

- [ ] **Step 3: Run.** Expected: new tests fail.
- [ ] **Step 4: Implement.** Order inside the apply: validate, `admin_store.save`, then `game_server.set_admin_devices`, then send `admin_devices_applied`. The agent needs its own `tenant_slug` and `terrarium_name`: read them from `self.identity`; with no identity every manifest is refused.
- [ ] **Step 5: Run** `python -m pytest tests/test_admin_devices_store.py tests/test_link.py -q` and the engine admin test file. Expected: PASS.
- [ ] **Step 6: Commit** `feat(uplink): set_admin_devices with an applied store beside terrarium.toml`.

### Task 7: Boot wiring and docs

**Model:** sonnet.

**Files:**
- Modify: `harness/terrarium_boot.py` (`_build_uplink`, the `GameServer(...)` construction near line 405, and the `--config` path it already receives)
- Test: `tests/test_boot_config.py` or the existing boot test that covers `_build_uplink` (find with `grep -ln _build_uplink tests/`)
- Modify: `docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md` and `docs/superpowers/specs/2026-09-13-mycoquest-handoff-terrarium-design.md` (one dated note at the top of each naming the box-contract design and the sections it replaces: identity frame, journal clear, resync order)
- Modify: `docs/MM_TERRARIUM.md` section "`uplink/`: outbound remote control"

**Interfaces:**
- Consumes: everything above.
- Produces: `_build_uplink(..., config_path: str)`; the store path is `os.path.join(os.path.dirname(os.path.abspath(config_path)), APPLIED_FILENAME)`.

- [ ] **Step 1: Write the failing tests**: `_build_uplink` builds `WebSocketTransport` with `identity.headers()`; passes an `AdminDevicesStore` at the path above and a `notify` that prints a line starting `SESSION_REPLACED:`; when the applied file exists, `GameServer` is constructed with its devices instead of `terrarium_config.admin_devices`, and with the config's list when it does not; the `UPLINK:` marker line is unchanged and carries no secret.
- [ ] **Step 2: Run.** Expected: new tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update the docs.** In `MM_TERRARIUM.md` rewrite the uplink section's connect, resync, journal and admin paragraphs to match the spec's section 4; say plainly that `terrarium.toml` `[admin] devices` is used only until the first manifest is applied.
- [ ] **Step 5: Run** `terrarium-dev test`. Expected: PASS (pytest, then node tests).
- [ ] **Step 6: Commit** `feat(boot): wire handshake identity, applied admin devices and the replaced marker`.

### Task 8: Closeout and bench check (gated on Chris)

**Model:** the controlling session; no subagent.

- [ ] **Step 1:** Ask Chris for go-ahead to push and open a PR, and to post the issue 100 and 127 comments drafted in `mm-fairyring/docs/followups/`.
- [ ] **Step 2:** The bench check is the spec's section 7, run by Chris on the real box once mm-fairyring and mm-renquest are deployed and `FAIRYRING_BASE_URL` is set. Write its eight steps into the PR description as a checklist.
- [ ] **Step 3:** `superpowers:finishing-a-development-branch`.
