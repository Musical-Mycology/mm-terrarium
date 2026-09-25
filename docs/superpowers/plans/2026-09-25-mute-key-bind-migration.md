# Mute carry-over on bind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a device muted as a player binds to a Room fixture, the mute moves to the fixture's `@fixture:<name>` token on both the engine and the DeviceLink agent, and every agent-side mute read resolves through the same canonical key.

**Architecture:** `GameServer._bind_room` gains a small `_migrate_mute_on_bind(dev, fixture)` step that rewrites `GameServer.muted` and tells the agent through the existing `on_mute_change` seam (unmute the raw dev, then mute the token). `DeviceLinkAgent._feed_breath` and the lobby `send_play` sink look the mute up by `_fixture_key(dev)`, like `_on_light_cue` already does.

**Tech Stack:** Python 3 stdlib (`control/` stays pure stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-mute-key-bind-migration-design.md`

## Global Constraints

- Mute semantics: a player's mute **carries over** to the fixture it binds to; only a non-mute fire un-latches.
- No new agent API: the engine talks to the agent only through `on_mute_change(dev, muted)`.
- Keep the existing "discard both spellings" code in `GameServer._clear_mutes` and in the agent's `_on_mute_change` unmute branch.
- Out of scope: unbind, `control/terrarium.py`'s fast-path binds, the Console `ArmRoomCommand` `[[artnet]]` refusal.
- Run every test from the worktree with the main checkout's interpreter by absolute path: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest ...` (the worktree has no `.venv` of its own; running from the worktree root makes tests import the worktree's code).
- Suite baseline before this change: **2673 passed, 1 skipped**.
- Never use em dashes in comments, docstrings, docs or commit messages; use a colon, parentheses, or `--` as the surrounding code does.
- Match the surrounding comment density and docstring style (long explanatory docstrings naming the spec and section).

---

### Task 1: Engine migrates a raw player mute to the fixture token on bind

**Files:**
- Modify: `control/engine.py` (`self.muted` comment near line 143; `_bind_room` near line 600; new `_migrate_mute_on_bind` right after it; `_clear_mutes` docstring near line 1087)
- Test: `tests/test_engine_functions.py` (add after `test_clear_mutes_finds_raw_dev_after_it_binds_to_a_fixture`, near line 747)

**Interfaces:**
- Consumes: `GameServer._bind_room(dev: str) -> None` (existing), `GameServer.room_binding: RoomBindingRegistry | None`, `fixture_dev(name: str) -> str` from `control.cues`.
- Produces: `GameServer._migrate_mute_on_bind(dev: str, fixture: str) -> None`. Contract: if raw `dev` is in `self.muted`, then `self.muted` loses `dev` and gains `fixture_dev(fixture)`, and `on_mute_change` (if set) is called exactly as `(dev, False)` then `(fixture_dev(fixture), True)`. Otherwise no state change and no calls. Task 2's agent tests rely on this call order.

- [ ] **Step 1: Write the failing tests**

Add `from control.room_binding import RoomBindingRegistry` to the imports at the top of `tests/test_engine_functions.py` (keep alphabetical order among the `control.` imports). Then add, after `test_clear_mutes_finds_raw_dev_after_it_binds_to_a_fixture`:

```python
def _armed(gs, fixture="main"):
    """Give a _running() GameServer a RoomBindingRegistry armed for
    `fixture`. _running's _Room has no ROOM role, so these tests call
    _bind_room directly -- the real join -> _bind_room path is covered end
    to end in tests/test_devicelink_agent.py."""
    gs.room_binding = RoomBindingRegistry(clock=lambda: 100.0)
    gs.room_binding.arm("TEST", fixture, window_seconds=10.0)


def test_a_player_mute_carries_over_to_the_fixture_on_bind():
    """Spec 2026-09-25-mute-key-bind-migration section 3.1: a mute latched
    on ie1 while it was a plain player (raw "ie1" in self.muted) moves to
    the fixture's @fixture: token when ie1 binds, so is_muted agrees for
    both spellings and the Console shows the fixture muted. The agent is
    told through on_mute_change: unmute the raw dev FIRST, then mute the
    token (the unmute discards both spellings, so the reverse order would
    drop the token again)."""
    gs, _, _ = _running(bound={})
    gs._dispatch_cues([MuteCue("ie1")], at=100.0)
    assert gs.muted == {"ie1"}
    calls = []
    gs.on_mute_change = lambda dev, m: calls.append((dev, m))
    _armed(gs)

    gs._bind_room("ie1")

    assert gs.room.bound["main"] == "ie1"
    assert gs.muted == {fixture_dev("main")}
    assert gs.is_muted("ie1")
    assert gs.is_muted(fixture_dev("main"))
    assert calls == [("ie1", False), (fixture_dev("main"), True)]


def test_a_bind_with_nothing_muted_makes_no_mute_calls():
    gs, _, _ = _running(bound={})
    calls = []
    gs.on_mute_change = lambda dev, m: calls.append((dev, m))
    _armed(gs)

    gs._bind_room("ie1")

    assert gs.room.bound["main"] == "ie1"
    assert gs.muted == set()
    assert calls == []


def test_a_carried_over_mute_lifts_on_the_next_non_mute_fire():
    """The carried-over latch is an ordinary fixture mute: the house rule
    (any non-mute fire at the surface un-latches it) applies unchanged."""
    gs, _, _ = _running(bound={})
    gs._dispatch_cues([MuteCue("ie1")], at=100.0)
    _armed(gs)
    gs._bind_room("ie1")

    assert gs.fire_function("glow", fired_by="admin-manual",
                            dev=fixture_dev("main")) is None

    assert gs.muted == set()
    assert not gs.is_muted("ie1")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_engine_functions.py -k "carries_over or nothing_muted or carried_over" -v`
Expected: `test_a_player_mute_carries_over_to_the_fixture_on_bind` FAILS (`gs.muted` is still `{'ie1'}`). `test_a_carried_over_mute_lifts_on_the_next_non_mute_fire` may already pass (the both-spellings discard in `_clear_mutes` clears the raw entry); that is expected, since it guards the new behavior rather than exposing the bug. `test_a_bind_with_nothing_muted_makes_no_mute_calls` passes (it is a guard).

- [ ] **Step 3: Implement**

In `control/engine.py`, replace `_bind_room` and add the new method directly after it:

```python
    def _bind_room(self, dev: str) -> None:
        fixture = None
        if self.room_binding is not None and self.room is not None:
            fixture = self.room_binding.armed_fixture(self.room.name)
        if fixture is not None:
            if self.room_binding is not None:
                self.room_binding.bind(self.room.name, fixture, dev)
            self.room.bound[fixture] = dev
            self._migrate_mute_on_bind(dev, fixture)
        self._notify("on_devices_change")

    def _migrate_mute_on_bind(self, dev: str, fixture: str) -> None:
        """Carry a mute latched on `dev` while it was a plain player (stored
        under its RAW spelling, since _mute_key(dev) was `dev` then) over to
        the fixture it just bound to, so self.muted and the agent both hold
        the one canonical @fixture:<name> token (spec
        2026-09-25-mute-key-bind-migration section 3.1). Without this the
        raw entry is unreachable by _mute_key once bound: is_muted reads
        False and the Console shows the fixture unmuted while the agent,
        still holding the raw spelling, keeps the device's breath off.

        A bind is not a fire, so it never un-latches: Stop stays the panic
        button until the next non-mute fire at the fixture.

        The agent hears it through on_mute_change, in this order: unmute
        `dev` first (its unmute branch discards BOTH the raw spelling and
        the now-current token, dropping the stale raw blackout), then mute
        the token (latching the fixture's blackout, purging its queued
        cues, silencing its voice). The reverse order would have the
        unmute discard the token just latched. If the fixture was already
        muted by its own token the pair simply re-latches it."""
        if dev not in self.muted:
            return
        token = fixture_dev(fixture)
        self.muted.discard(dev)
        self.muted.add(token)
        sink = self.on_mute_change
        if sink is not None:
            sink(dev, False)
            sink(token, True)
```

`fixture_dev` is already imported in `control/engine.py` (used by `_fixture_target` and `_mute_key`); do not add a second import.

Then update two comments (text only):

1. The `self.muted` field comment (the block ending `never a raw `in`/`add`/`discard` against a bound dev.`): append these lines to the end of that comment block:

```python
        # A mute latched on a plain player (raw spelling) is moved to its
        # fixture's token when that player binds (_migrate_mute_on_bind),
        # so a bind through _bind_room never leaves a raw entry behind.
```

2. The `_clear_mutes` docstring: at the end of the paragraph that ends `(amended 2026-09-23 after the final review).`, add before the closing `"""`:

```
        Since 2026-09-25 _bind_room migrates such a raw entry to the
        fixture token itself (_migrate_mute_on_bind), so this raw-spelling
        discard is now a safety net for binds that bypass _bind_room
        (control/terrarium.py's fast path writes room.bound directly).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_engine_functions.py tests/test_engine.py -q`
Expected: all pass, including the three new tests and the existing `test_clear_mutes_finds_raw_dev_after_it_binds_to_a_fixture` and `test_is_muted_agrees_for_a_bound_dev_and_its_fixture_token`.

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_engine_functions.py
git commit -m "fix(engine): carry a player's mute over to its fixture token on bind"
```

---

### Task 2: Agent reads mutes by canonical key; end-to-end tests; deep-dive update

**Files:**
- Modify: `devicelink/agent.py` (`_muted` field comment near line 142; `send_play` in `_lobby_sinks` near line 473; `_on_mute_change` unmute-branch comment near line 924; `_feed_breath` near line 1053)
- Test: `tests/test_devicelink_agent.py` (add after `test_a_mute_latched_as_a_player_survives_a_bind_then_unload`, near line 2585)
- Modify: `docs/MM_TERRARIUM.md` (the *`devicelink/artnet_sink.py`, `[[artnet]]`, routing by fixture name, native RGBW (2026-09-23)* entry, and its test-baseline lines just before `## Boundary rules`)

**Interfaces:**
- Consumes: Task 1's `GameServer._migrate_mute_on_bind` contract (on bind of a muted player: `on_mute_change(dev, False)` then `on_mute_change(fixture_dev(fixture), True)`). Existing test helpers in `tests/test_devicelink_agent.py`: `FakeServer`, `_hello(server, agent, client="c1", dev="ie1")`, `_Clock`, `TEST_PROFILE`, `BREATH_CC`, `TestBit` (its `room_types` already include `"TEST"`, so it can take a ROOM join), `Room`, `RoomBindingRegistry`, `GameServer`, `DeviceLinkAgent`, `MuteCue`, `fixture_dev`.
- Produces: nothing new for other tasks.

**Background the implementer needs:** a device that joined as a player and then taps in as a Room fixture keeps its old player bridge in `agent.bridges` (the ROOM join carries no role config, so `_on_join` logs "building the LightSession for ie1 failed" and returns without replacing it). That stale bridge is what `_feed_breath` iterates. The logged exception during these tests is expected and pre-existing; do not try to fix it here.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_devicelink_agent.py`, after `test_a_mute_latched_as_a_player_survives_a_bind_then_unload`:

```python
# --- a player's mute carries over on bind (spec 2026-09-25 -----------------
# --- mute-key-bind-migration) -----------------------------------------------

def _player_then_room(monkeypatch, *, mute_as_player):
    """ie1 joins TEST_PLAYER_NODE (so it holds a player bridge), is
    optionally muted as that player, then taps in through the REAL join
    path as the armed `main` fixture (GameServer.join -> _bind_room). The
    ROOM join cannot build a bridge (no role config), so ie1 keeps its
    player bridge: the one _feed_breath iterates."""
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
    assert "ie1" in agent.bridges
    if mute_as_player:
        gs._dispatch_cues([MuteCue("ie1")], at=clk())
        assert gs.muted == {"ie1"} and agent._muted == {"ie1"}
    binding.arm("TEST", "main", window_seconds=10.0)
    server.deliver("c1", "/game/join", "ss", ["ie1", "ROOM_TEST_NODE"])
    agent.poll()
    assert gs.room.bound["main"] == "ie1"
    assert "ie1" in agent.bridges
    return gs, agent, clk


def _breaths_after_advance(agent, clk, dev="ie1"):
    """Advance past the ~47 ms breath quantization step and poll once;
    return the cc:11 feeds `dev`'s bridge session received."""
    seen = []
    agent.bridges[dev].session.feed_midi = lambda s, a, b: seen.append((s, a, b))
    clk.advance(1.0)
    agent.poll()
    return [m for m in seen if m[0] == 0xB0 and m[1] == BREATH_CC]


def test_a_player_mute_carries_over_to_the_fixture_on_both_sides(monkeypatch):
    """The defect: muted as a player, then bound. Engine and agent must
    both hold ONLY the fixture token -- the Console (is_muted) and the
    device (breath, blackout) agree the fixture is muted."""
    gs, agent, clk = _player_then_room(monkeypatch, mute_as_player=True)

    assert gs.muted == {fixture_dev("main")}
    assert gs.is_muted(fixture_dev("main"))
    assert agent._muted == {fixture_dev("main")}
    assert agent._overrides[fixture_dev("main")] == ((0, 0, 0), 0.0, None)
    assert "ie1" not in agent._overrides
    assert _breaths_after_advance(agent, clk) == []


def test_a_carried_over_mute_lifts_and_breath_resumes(monkeypatch):
    gs, agent, clk = _player_then_room(monkeypatch, mute_as_player=True)

    gs._clear_mutes(["ie1"])

    assert gs.muted == set()
    assert agent._muted == set()
    assert fixture_dev("main") not in agent._overrides
    assert _breaths_after_advance(agent, clk)


def test_a_muted_fixtures_bound_device_is_not_fed_breath(monkeypatch):
    """The mirror defect: the fixture is muted by its own token while ie1
    is bound; _feed_breath used to check the raw "ie1", miss the token,
    and keep breathing a muted fixture's device."""
    gs, agent, clk = _player_then_room(monkeypatch, mute_as_player=False)
    assert _breaths_after_advance(agent, clk)      # breathing before the mute

    gs._dispatch_cues([MuteCue(fixture_dev("main"))], at=clk())

    assert agent._muted == {fixture_dev("main")}
    assert _breaths_after_advance(agent, clk) == []


def test_an_unmuted_bound_device_still_breathes(monkeypatch):
    """Guard against over-suppression from reading the mute by
    _fixture_key: nothing muted, the bound device keeps breathing."""
    gs, agent, clk = _player_then_room(monkeypatch, mute_as_player=False)
    assert agent._muted == set()
    assert _breaths_after_advance(agent, clk)
```

- [ ] **Step 2: Run the tests to verify the right ones fail**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py -k "carries_over_to_the_fixture_on_both or carried_over_mute_lifts or muted_fixtures_bound_device or unmuted_bound_device" -v`
Expected, with Task 1 already landed: `test_a_muted_fixtures_bound_device_is_not_fed_breath` FAILS (breath is still fed because `_feed_breath` checks raw `"ie1"`), and `test_a_player_mute_carries_over_to_the_fixture_on_both_sides` FAILS on its final breath assertion (the token is latched but `_feed_breath` checks the raw `"ie1"`, which Task 1's unmute removed, so breath now flows). The other two pass (guards). If `_player_then_room`'s own asserts fail, stop and report: the harness assumption is wrong, not the fix.

- [ ] **Step 3: Implement**

In `devicelink/agent.py`:

1. `_feed_breath`: replace

```python
            if dev in self._muted:
                continue
```

with

```python
            # By fixture key, not raw dev: a bound device's mute is latched
            # under its fixture's @fixture: token (spec 2026-09-25
            # mute-key-bind-migration section 3.2).
            if self._fixture_key(dev) in self._muted:
                continue
```

2. `send_play` inside `_lobby_sinks`: replace

```python
            if dev not in self._muted:
```

with

```python
            if self._fixture_key(dev) not in self._muted:
```

3. The `_muted` field comment (the block starting `# devs currently latched mute-blackout.`): replace the whole comment with

```python
        # Mute-blackout latches, keyed like _overrides: @fixture:<name> for a
        # Room fixture (_fixture_key), the raw dev for a player. Checked by
        # _feed_breath (skip feeding cc:11) and _on_light_cue (drop the cue),
        # both through _fixture_key -- transport-seam suppression; PlayCue is
        # already suppressed engine-side via GameServer.muted.
```

4. The `_on_mute_change` unmute-branch comment (the block starting `# Discard BOTH the dev's CURRENT key and its raw spelling --`): append these lines at the end of that comment block, before the `for k in {key, dev}:` line:

```python
            # GameServer._migrate_mute_on_bind relies on this: on a bind it
            # sends (dev, False) to drop the raw player-era entry, then
            # (token, True) to latch the fixture (spec 2026-09-25
            # mute-key-bind-migration section 3.1).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_lobby_agent.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q`
Expected: **2680 passed, 1 skipped** (baseline 2673 plus 3 engine and 4 agent tests). If the count differs, report the actual numbers rather than editing the expectation.

Also run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m tools.render_diagrams --check`
Expected: reports the deep-dive's generated diagrams current.

- [ ] **Step 6: Update the deep-dive**

In `docs/MM_TERRARIUM.md`, in the *`devicelink/artnet_sink.py`, `[[artnet]]`, routing by fixture name, native RGBW (2026-09-23)* entry, directly after the bullet that begins `- **`devicelink/agent.py` keys overrides, mutes and feeds by fixture, not`, insert this bullet:

```markdown
- **A player's mute carries over when the device binds (2026-09-25).**
  Design:
  [`.../2026-09-25-mute-key-bind-migration-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-25-mute-key-bind-migration-design.md).
  A device muted while still a plain player is latched under its raw dev
  id; `GameServer._bind_room` now moves that entry to the fixture's
  `@fixture:<name>` token (`_migrate_mute_on_bind`) and tells the agent
  through `on_mute_change` (unmute the raw dev, then mute the token), so
  `GameServer.muted` and `DeviceLinkAgent._muted` hold one canonical
  spelling and the Console Room panel shows the fixture muted. A bind is
  not a fire, so it never un-latches. `_feed_breath` and the lobby
  `send_play` sink now read the mute through `_fixture_key`, which also
  fixed a mirror defect: a device that was a player before binding keeps
  its player bridge (the ROOM join builds none), and `_feed_breath` used to
  keep breathing it after its fixture was muted. Binds that bypass
  `_bind_room` (`control/terrarium.py`'s fast path, at Room load when
  nothing is muted) still rely on the both-spellings discard in
  `_clear_mutes` and the agent's unmute branch.
```

Then, after the line `**Test baseline after the 2026-09-25 no-simulator follow-up:** ...`, add a new line (use the actual numbers from Step 5):

```markdown
**Test baseline after the 2026-09-25 mute carry-over fix:** `.venv/bin/python -m pytest tests -q` -> **2680 passed, 1 skipped**.
```

- [ ] **Step 7: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py docs/MM_TERRARIUM.md
git commit -m "fix(devicelink): read mutes by fixture key; carry a player's mute over on bind"
```
