# Muted SolidCue guard and Room-join bridge drop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A muted surface stays dark under every SolidCue (lobby flash, `__flash__`, a Bit's SolidCue) until a non-mute fire lifts the mute, and a ROOM join drops the device's player bridge instead of failing to build one and sending `/error`.

**Architecture:** One mute check at the single SolidCue seam (`DeviceLinkAgent._on_solid_cue`), keyed through `_fixture_key` like every other agent mute read. A ROOM-class join in `_on_join` short-circuits before `DeviceBridge` is built, calling a new `_drop_player_bridge(dev)` helper that clears per-device render state without a fade, a `/release` or a `drop_dev`.

**Tech Stack:** Python 3, pytest, offline fakes (`FakeServer`, `_fake_sessions`, `_Clock`) in `tests/test_devicelink_agent.py`.

**Spec:** `docs/superpowers/specs/2026-09-25-lobby-flash-mute-and-room-bridge-design.md`

## Global Constraints

- Run tests from the worktree: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q`. Baseline before Task 1: **2747 passed, 1 skipped**.
- The suite stays fully offline (no O2, no Arco, no pyarco).
- Boundary rule 2: nothing added may raise into the engine tick.
- Mute lookups in the agent resolve through `self._fixture_key(dev)`.
- A non-mute fire still un-latches a mute (`GameServer.fire_function` → `_clear_mutes` runs before `_dispatch_cues`). Do not change `control/engine.py`.
- The existing player-join `on_grant` failure path (sends `/<dev>/error role "could not build light session"`) is unchanged.
- No em dashes in any prose written (code comments, docs, commit messages).
- This branch stacks on `claude/interesting-bhaskara-62910e` (already merged into it). Do not rebase.

## File map

- `devicelink/agent.py`: `_on_solid_cue` (mute guard), `_drain_light_cues` (`__flash__` comment), `_on_join` (ROOM short-circuit), new `_drop_player_bridge`.
- `tests/test_devicelink_agent.py`: new guard and ROOM-join tests; `_player_then_room` and the four carry-over tests that leaned on the stale bridge are updated; new `_player_bound_directly` helper.
- `tests/test_lobby_agent.py`: lobby `feedback` flash on a muted fixture.
- `docs/MM_TERRARIUM.md`, `docs/superpowers/specs/2026-09-25-mute-key-bind-migration-design.md`: docs.

---

### Task 1: A muted surface ignores every SolidCue

**Files:**
- Modify: `devicelink/agent.py` (`_on_solid_cue`, around line 884; the `__flash__` comment in `_drain_light_cues`, around line 1528)
- Test: `tests/test_devicelink_agent.py` (append after `test_an_rgbw_solid_override_leaves_white_dark`, near the end of the file)
- Test: `tests/test_lobby_agent.py` (append at the end)

**Interfaces:**
- Consumes: `DeviceLinkAgent._fixture_key(dev) -> str`, `DeviceLinkAgent._muted: set[str]`, `DeviceLinkAgent._overrides: dict[str, tuple[rgb, level, expires]]`, test helpers `_room_ready_game_server`, `_agent_with_joined_device`, `_player_then_room`, `FakeServer`, `_fake_sessions`, `_Clock`.
- Produces: `_on_solid_cue` is a no-op for a muted key. Task 2 relies on nothing new from this task.

- [ ] **Step 1: Write the failing tests in `tests/test_devicelink_agent.py`**

Append:

```python
# --- a muted surface ignores every SolidCue (spec 2026-09-25 -------------
# --- lobby-flash-mute-and-room-bridge section 3.1) ---------------------------

_BLACKOUT = ((0, 0, 0), 0.0, None)


def test_a_solid_cue_on_a_muted_fixture_keeps_the_blackout(monkeypatch):
    """The core defect: a short SolidCue used to overwrite the latched
    blackout, then expire, leaving the fixture lit while still muted."""
    gs = _room_ready_game_server()
    _fake_sessions(monkeypatch)
    clk = _Clock(100.0)
    agent = DeviceLinkAgent(gs, FakeServer(), clock=clk)
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=clk())
    assert agent._overrides[fixture_dev("main")] == _BLACKOUT

    agent._on_solid_cue("sim-room-main", (0, 255, 0), 1.0, 0.25, clk())
    assert agent._overrides[fixture_dev("main")] == _BLACKOUT
    clk.advance(1.0)
    agent.poll()
    assert agent._overrides[fixture_dev("main")] == _BLACKOUT
    assert fixture_dev("main") in agent._muted


def test_the_lobby_set_override_sink_respects_a_fixture_mute(monkeypatch):
    gs = _room_ready_game_server()
    _fake_sessions(monkeypatch)
    clk = _Clock(100.0)
    agent = DeviceLinkAgent(gs, FakeServer(), clock=clk)
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=clk())

    agent._lobby_sinks().set_override("sim-room-main", (0, 255, 0), 1.0, 0.25)

    assert agent._overrides[fixture_dev("main")] == _BLACKOUT


def test_the_flash_sentinel_respects_a_fixture_mute(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0)
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=100.0)

    agent._flash_fixtures_now((0, 255, 0), 1)
    agent._drain_light_cues()

    assert agent._overrides[fixture_dev("main")] == _BLACKOUT
    assert agent._overrides[fixture_dev("accent")][0] == (0, 255, 0)


def test_a_bit_solid_cue_is_dropped_for_a_muted_fixture(monkeypatch):
    gs = _room_ready_game_server()
    _fake_sessions(monkeypatch)
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0)
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=100.0)

    gs._dispatch_cues([SolidCue(fixture_dev("main"), (255, 0, 0), 1.0, 0.5)],
                      at=100.0)

    assert agent._overrides[fixture_dev("main")] == _BLACKOUT


def test_a_bit_solid_cue_is_dropped_for_a_muted_player():
    gs, server, agent, dev, clk = _agent_with_joined_device()
    gs._dispatch_cues([MuteCue(dev)], at=clk())

    gs._dispatch_cues([SolidCue(dev, (255, 0, 0), 1.0, 0.5)], at=clk())
    clk.advance(1.0)
    agent.poll()

    assert agent._overrides[dev] == _BLACKOUT
    assert set(_last_leds_payload(server, dev)) == {0}


def test_a_non_mute_fire_still_unlatches_and_applies_its_solid(monkeypatch):
    """The guard must not block the one sanctioned un-latch: the engine
    clears the mute before it dispatches the fire's SolidCue."""
    gs = _room_ready_game_server()
    _fake_sessions(monkeypatch)
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0)
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=100.0)

    assert gs.fire_function("flash", fired_by="admin-manual",
                            dev=fixture_dev("main")) is None

    assert fixture_dev("main") not in agent._muted
    assert agent._overrides[fixture_dev("main")][0] == (255, 255, 255)


def test_a_queued_join_flash_cannot_unblack_a_carried_over_mute(monkeypatch):
    """The end-to-end case: ie1 joins as a scored player (the lobby queues
    its green join flash), is muted, then binds to `main`. The queued flash
    resolves to @fixture:main and used to un-black it for good."""
    gs, agent, clk, server = _player_then_room(monkeypatch, mute_as_player=True)
    assert agent._lobby is not None
    for _ in range(int(3.0 / (1 / 44))):
        clk.advance(1 / 44)
        agent.poll()
        assert agent._overrides.get(fixture_dev("main")) == _BLACKOUT
    assert fixture_dev("main") in agent._muted
```

- [ ] **Step 2: Write the failing test in `tests/test_lobby_agent.py`**

Add `MuteCue` to the `control.cues` import and `FEEDBACK_REFUSED` to the `control.lobby` import, then append:

```python
def test_start_feedback_flash_leaves_a_muted_fixture_dark(monkeypatch):
    """_flash_fixtures (start feedback) goes through the set_override sink;
    a muted fixture must keep its latched blackout, an unmuted one flashes."""
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=clk.t)

    agent._lobby.feedback(FEEDBACK_REFUSED)
    saw_accent_flash = False
    for _ in range(int(2.0 / (1 / 44))):
        clk.advance(1 / 44)
        agent.poll()
        assert agent._overrides[fixture_dev("main")] == ((0, 0, 0), 0.0, None)
        entry = agent._overrides.get(fixture_dev("accent"))
        saw_accent_flash |= entry is not None and entry[0] == RED
    assert saw_accent_flash
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_lobby_agent.py -q -k "muted or mute or unlatches or unblack"`
Expected: FAIL for the fixture/player/lobby/sink/end-to-end cases (override is no longer `_BLACKOUT`). `test_the_flash_sentinel_respects_a_fixture_mute` and `test_a_non_mute_fire_still_unlatches_and_applies_its_solid` may already pass (the sentinel has its own raw check; the fire clears first). That is expected; they pin behavior the change must keep.

- [ ] **Step 4: Implement the guard in `devicelink/agent.py`**

Replace `_on_solid_cue` with:

```python
    def _on_solid_cue(self, dev: str, rgb: tuple[int, int, int],
                      level: float, duration: float | None,
                      when: float | None) -> None:
        """A Bit's SolidCue reached the engine sink. Store the override and
        force a resend this tick (see _apply_override's use at both send
        seams) so it goes out immediately, stamped with the cue's own `when`
        rather than this tick's stream-frame origin.

        Dropped for a muted surface. Every SolidCue source lands here: the
        engine (a Bit's SolidCue), the lobby's set_override sink (join,
        invite and start-feedback flashes) and the __flash__ sentinel. An
        override written over the latched blackout would expire and take
        the blackout with it, leaving a muted surface lit. A non-mute fire
        is unaffected: the engine clears the mute before it dispatches the
        fire's cues (spec 2026-09-25 lobby-flash-mute-and-room-bridge
        section 3.1)."""
        dev = self._fixture_key(dev)
        if dev in self._muted:
            return
        expires = None if duration is None else when + duration
        self._overrides[dev] = (rgb, level, expires)
        self._invalidate_frame(dev)
```

In `_drain_light_cues`, replace the `__flash__` comment block (the lines starting `# _flash_fixtures_now's sentinel:` through `# matches a tagged payload.`) with:

```python
                # _flash_fixtures_now's sentinel: a feedback flash that has
                # to outlive the lobby runtime that asked for it. `dev` is
                # already a fixture token. _on_solid_cue's own mute guard is
                # the one that matters; this check just skips the call.
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_lobby_agent.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q`
Expected: **2755 passed, 1 skipped** (baseline 2747 + 8 new).

- [ ] **Step 7: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py tests/test_lobby_agent.py
git commit -m "fix(devicelink): a muted surface ignores every SolidCue, so a flash cannot undo Stop"
```

---

### Task 2: A ROOM join drops the player bridge instead of building one

**Files:**
- Modify: `devicelink/agent.py` (`_on_join`, around line 1248; add `_drop_player_bridge` right after `_on_join`; add `RoleClass` to the imports if not already imported: check with `grep -n "RoleClass" devicelink/agent.py`, it is already used at lines ~90 and ~626)
- Test: `tests/test_devicelink_agent.py` (`_player_then_room` and the carry-over tests after it, around lines 2592-2730)

**Interfaces:**
- Consumes: `GameServer.join(dev, node) -> JoinResult` (`result.role_class`), `RoleClass.ROOM`, Task 1's guard (only indirectly).
- Produces: `DeviceLinkAgent._drop_player_bridge(self, dev: str) -> None`.

- [ ] **Step 1: Update `_player_then_room` and add `_player_bound_directly`**

In `tests/test_devicelink_agent.py`, change the end of `_player_then_room`'s docstring and its last assertion:

```python
def _player_then_room(monkeypatch, *, mute_as_player):
    """ie1 joins TEST_PLAYER_NODE (so it holds a player bridge), is
    optionally muted as that player, then taps in through the REAL join
    path as the armed `main` fixture (GameServer.join -> _bind_room). The
    ROOM join drops the player bridge (spec 2026-09-25
    lobby-flash-mute-and-room-bridge section 3.2), so ie1 gets only the
    fixture's frames."""
```

and replace the final `assert "ie1" in agent.bridges` with `assert "ie1" not in agent.bridges`.

Add right after `_player_then_room`:

```python
def _player_bound_directly(monkeypatch):
    """ie1 joins TEST_PLAYER_NODE, then `main` is bound to it by writing
    room.bound directly (the fast-path shape, no ROOM join), so ie1 still
    holds its player bridge. Covers the _fixture_key mute reads in
    _feed_breath and _render_frames for any bridge-holding bound dev."""
    _fake_sessions(monkeypatch)
    clk = _Clock()
    gs = GameServer({"TestBit": TestBit}, clock=clk)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("TestBit")
    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.room.bound["main"] = "ie1"
    assert "ie1" in agent.bridges
    return gs, agent, clk, server
```

- [ ] **Step 2: Rewrite the carry-over tests that leaned on the stale bridge**

Replace these five tests (from `test_a_player_mute_carries_over_to_the_fixture_on_both_sides` through `test_a_fixture_muted_by_its_own_token_blacks_its_bound_players_bridge`; keep `_breaths_after_advance` and `_player_frames_sent` helpers as they are) with:

```python
def test_a_player_mute_carries_over_to_the_fixture_on_both_sides(monkeypatch):
    """The defect: muted as a player, then bound. Engine and agent must
    both hold ONLY the fixture token -- the Console (is_muted) and the
    device (blackout) agree the fixture is muted."""
    gs, agent, clk, server = _player_then_room(monkeypatch, mute_as_player=True)

    assert gs.muted == {fixture_dev("main")}
    assert gs.is_muted(fixture_dev("main"))
    assert agent._muted == {fixture_dev("main")}
    assert agent._overrides[fixture_dev("main")] == ((0, 0, 0), 0.0, None)
    assert "ie1" not in agent._overrides
    assert "ie1" not in agent.bridges


def test_a_carried_over_mute_lifts(monkeypatch):
    gs, agent, clk, server = _player_then_room(monkeypatch, mute_as_player=True)

    gs._clear_mutes(["ie1"])

    assert gs.muted == set()
    assert agent._muted == set()
    assert fixture_dev("main") not in agent._overrides


def test_a_muted_fixtures_bound_device_is_not_fed_breath(monkeypatch):
    """_feed_breath reads the mute by _fixture_key: a bridge-holding dev
    bound to a muted fixture is not breathed."""
    gs, agent, clk, server = _player_bound_directly(monkeypatch)
    assert _breaths_after_advance(agent, clk)      # breathing before the mute

    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=clk())

    assert agent._muted == {fixture_dev("main")}
    assert _breaths_after_advance(agent, clk) == []


def test_an_unmuted_bound_device_still_breathes(monkeypatch):
    """Guard against over-suppression from reading the mute by
    _fixture_key: nothing muted, the bound device keeps breathing."""
    gs, agent, clk, server = _player_bound_directly(monkeypatch)
    assert agent._muted == set()
    assert _breaths_after_advance(agent, clk)


def test_a_fixture_muted_by_its_own_token_blacks_its_bound_players_bridge(monkeypatch):
    """_render_frames reads the override by _fixture_key: a bridge-holding
    dev bound to a muted fixture gets black 36-channel frames."""
    gs, agent, clk, server = _player_bound_directly(monkeypatch)
    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=clk())
    # Isolate from the lobby's own scored-join ceremony.
    agent._lobby = None
    server.sent.clear()
    for _ in range(5):
        clk.advance(0.2)
        # Force a resend each tick: the override paints the same colour
        # every time, so without this the render would dedupe against its
        # own last frame and prove nothing either way.
        agent._last_frames.pop("ie1", None)
        agent.poll()
    frames = _player_frames_sent(server)
    assert frames, "no 36-channel /ie1/leds frame was sent after the bind"
    assert all(f == bytes(_DEVICE_CHANNELS) for f in frames)


@pytest.mark.parametrize("mute_as_player", [False, True])
def test_a_room_join_leaves_one_led_stream(monkeypatch, mute_as_player):
    """The double-stream defect: the stale player bridge kept sending
    36-channel frames to /ie1/leds beside the fixture's own frames."""
    gs, agent, clk, server = _player_then_room(monkeypatch,
                                               mute_as_player=mute_as_player)
    server.sent.clear()
    for _ in range(int(2.0 / (1 / 44))):
        clk.advance(1 / 44)
        agent.poll()
    assert _player_frames_sent(server) == []
    assert [m for d, m in server.sent
            if d == "ie1" and m["address"] == "/ie1/leds"], \
        "the fixture's own frames must still reach ie1"


def test_a_room_join_after_a_player_join_sends_no_error_and_no_role(monkeypatch):
    _fake_sessions(monkeypatch)
    clk = _Clock()
    binding = RoomBindingRegistry(clock=clk)
    gs = GameServer({"TestBit": TestBit}, room_binding=binding, clock=clk)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("TestBit")
    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    roles_before = len(server.addressed("/ie1/role"))
    binding.arm("TEST", "main", window_seconds=10.0)

    server.deliver("c1", "/game/join", "ss", ["ie1", "ROOM_TEST_NODE"])
    agent.poll()

    assert gs.room.bound["main"] == "ie1"
    assert server.addressed("/ie1/error") == []
    assert len(server.addressed("/ie1/role")) == roles_before
    assert "ie1" not in agent.bridges
    assert "ie1" not in agent._universes


def test_a_never_a_player_room_join_sends_no_error(monkeypatch, caplog):
    _fake_sessions(monkeypatch)
    clk = _Clock()
    binding = RoomBindingRegistry(clock=clk)
    gs = GameServer({"TestBit": TestBit}, room_binding=binding, clock=clk)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("TestBit")
    _hello(server, agent, client="c2", dev="ie2")
    binding.arm("TEST", "main", window_seconds=10.0)

    server.deliver("c2", "/game/join", "ss", ["ie2", "ROOM_TEST_NODE"])
    agent.poll()

    assert gs.room.bound["main"] == "ie2"
    assert server.addressed("/ie2/error") == []
    assert server.addressed("/ie2/role") == []
    assert "ie2" not in agent.bridges
    assert not any("building the LightSession" in r.message
                   for r in caplog.records)
```

- [ ] **Step 3: Run the updated tests to verify the right ones fail**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py -q -k "room_join or carried_over or carries_over or player_then or bound_device or blacks_its_bound or unblack"`
Expected: FAIL in `_player_then_room` (`assert "ie1" not in agent.bridges`) for every test using it, and in the two ROOM-join error tests (`/ie1/error` and `/ie2/error` are sent). The three `_player_bound_directly` tests PASS already.

- [ ] **Step 4: Implement the ROOM short-circuit and `_drop_player_bridge` in `devicelink/agent.py`**

In `_on_join`, right after the `if not result.granted: ... return` block and before `bridge = DeviceBridge(...)`, insert:

```python
        if result.role_class == RoleClass.ROOM:
            # A ROOM grant binds dev to a Room fixture (GameServer._bind_room)
            # and carries no role config, so there is no bridge to build and
            # nothing to tell the device: its fixture's frames start arriving
            # on /<dev>/leds. Registration already released any player role
            # dev held (a role switch), so drop that role's bridge too, or dev
            # gets two LED streams (spec 2026-09-25
            # lobby-flash-mute-and-room-bridge section 3.2).
            self._drop_player_bridge(dev)
            if self._lobby is not None:
                self._lobby.forget(dev)
            return
```

Add this method right after `_on_join`:

```python
    def _drop_player_bridge(self, dev: str) -> None:
        """Forget dev's player-side render state at once: no closing fade
        (the fixture's frames take over dev's LEDs this tick), no
        /<dev>/release (dev is not leaving) and no server.drop_dev (its
        connection stays live). _canvas_urls stays, and _muted is left to
        the engine's bind migration, which already moves a raw player mute
        onto the fixture token. A no-op for a dev that never held a
        bridge."""
        self.bridges.pop(dev, None)
        self._universes.pop(dev, None)
        self._last_frames.pop(dev, None)
        self._pending_at.pop(dev, None)
        self._last_breath.pop(dev, None)
        self._breathless.discard(dev)
        self._closing.pop(dev, None)
        self._closing_revived.discard(dev)
        self._overrides.pop(dev, None)
        self._override_only.discard(dev)
```

Check `RoleClass` is already imported at the top of `devicelink/agent.py` (`grep -n "^from control.roles\|RoleClass" devicelink/agent.py | head -3`). It is used at module level around line 90, so it should be. Add it to the existing `control.roles` import only if missing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_lobby_agent.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q`
Expected: **2759 passed, 1 skipped** (Task 1's 2755, plus 4 new: two parametrized cases of `test_a_room_join_leaves_one_led_stream` and the two ROOM-join error tests; the five rewritten tests replace five existing ones). If any other test asserted the old `/error` on a ROOM join or the stale bridge, update it to the new behavior and name it in the report.

- [ ] **Step 7: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "fix(devicelink): a Room join drops the player bridge and sends no /error"
```

---

### Task 3: Docs

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (read it first with offset/limit; the file is ~400KB)
- Modify: `docs/superpowers/specs/2026-09-25-mute-key-bind-migration-design.md` (section 1, the "Mirror defect" bullet)

**Interfaces:**
- Consumes: Tasks 1 and 2 landed; the final suite count from Task 2 Step 6.
- Produces: nothing code-facing.

- [ ] **Step 1: Find the carry-over entry and the latest baseline line**

Run: `grep -n "mute carry-over\|keeps its player bridge\|player bridge\|Test baseline after" docs/MM_TERRARIUM.md`

- [ ] **Step 2: Correct the carry-over entry**

Wherever the carry-over entry says a device that joined as a player and bound to a fixture keeps its player bridge, add one sentence after it: `Closed 2026-09-25: a ROOM join now drops that bridge (see *A muted surface ignores every SolidCue; a Room join drops the player bridge* below).` Keep the rest of the text.

- [ ] **Step 3: Add the new entry**

Right after the line `**Test baseline after the 2026-09-25 mute carry-over fix:** ...`, add (fill `<N>` from Task 2 Step 6's actual output):

```markdown
### `devicelink/agent.py` -- A muted surface ignores every SolidCue; a Room join drops the player bridge (2026-09-25)
Design:
[`.../2026-09-25-lobby-flash-mute-and-room-bridge-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-25-lobby-flash-mute-and-room-bridge-design.md).

- **A SolidCue can no longer undo Stop.** `_on_solid_cue` drops the cue
  when `_fixture_key(dev)` is muted. Before, any lobby flash (the scored-join
  flash, the invite flash, `_flash_fixtures` start feedback) or a Bit's own
  SolidCue overwrote the latched blackout, then expired and took the
  blackout with it, so a muted surface rendered lit while `_muted` still
  held it. A non-mute fire still un-latches: the engine clears the mute
  before it dispatches the fire's cues.
- **A ROOM join builds no bridge and sends no `/error`.** `_on_join`
  short-circuits a ROOM-class grant: `_drop_player_bridge(dev)` forgets the
  device's player-era bridge and render state (no fade, no `/release`, no
  `drop_dev`), and nothing is sent. Before, every Room tap logged a
  traceback and sent `/<dev>/error role "could not build light session"`,
  and a device that had joined as a player kept its bridge, so it got two
  LED streams (36-channel player frames and the fixture's frames).
- **Still open:** registration releases the player role on a ROOM role
  switch, but the engine never calls `Bit.on_leave` for it.

**Test baseline after this fix:** `.venv/bin/python -m pytest tests -q` -> **<N> passed, 1 skipped**.
```

- [ ] **Step 4: Point the carry-over spec at this fix**

In `docs/superpowers/specs/2026-09-25-mute-key-bind-migration-design.md`, at the end of the "Mirror defect (found while tracing this one)" bullet in section 1, add: `The stale bridge itself is removed by [`2026-09-25-lobby-flash-mute-and-room-bridge-design.md`](2026-09-25-lobby-flash-mute-and-room-bridge-design.md).`

- [ ] **Step 5: Check diagrams and em dashes**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m tools.render_diagrams --check`
Expected: generated diagrams reported current.
Run: `git diff -U0 | grep "^+" | grep -c "—"`
Expected: `0`.

- [ ] **Step 6: Commit**

```bash
git add docs/MM_TERRARIUM.md docs/superpowers/specs/2026-09-25-mute-key-bind-migration-design.md
git commit -m "docs(terrarium): a muted surface ignores SolidCues; a Room join drops the player bridge"
```
