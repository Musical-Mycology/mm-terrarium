# o2lite pump fix + silent-deny fix

## Root cause (confirmed)

`devicelink/o2_transport.py` never called `self._o2.poll()` anywhere. Verified
with `grep -n "\.poll()" devicelink/o2_transport.py` before the fix: zero
matches. o2litepy only dispatches inbound messages to registered handlers from
inside `o2lite.poll()`, so `O2LiteTransport._on_message` was never invoked
after `start()`, `self._inbound` stayed empty forever, and `drain_inbound()`
faithfully returned `[]`.

Confirmed nothing else in Control's loop pumps it either:
- `harness/terrarium_boot.py` calls `agent.poll()` (lines 182, 206), never
  `AudioBridge.tick()`.
- `devicelink/agent.py`'s `poll()` calls `drain_new_clients()` /
  `drain_inbound()` / render steps -- none of it reaches o2lite directly (it
  goes through the transport interface, which was the broken part).
- `control/audio.py:192` calls `self._pool.poll()` inside `AudioBridge.tick()`,
  but nothing in the driver loop calls `AudioBridge.tick()`.

## Why no test caught it

`FakeO2Lite.deliver()` called the registered handler directly; `poll()` was a
no-op. Every existing test dispatched without ever pumping -- more forgiving
than real o2litepy in exactly the dimension that broke production.

## The fake fix

`FakeO2Lite` (`devicelink/o2_transport.py`):
- `deliver()` now only enqueues `(address, typespec, args, timestamp)` onto a
  new `self._queue` list.
- `poll()` now drains that queue and dispatches each queued message to its
  registered handler (the old `deliver()` body, unchanged logic), matching
  real o2litepy's "only dispatches from inside poll()" contract.

## The transport fix

Pump lives in `O2LiteTransport.drain_inbound()`, not `drain_new_clients()`.
Reasoning:
- The task's own regression shape ("deliver a message, then assert
  `drain_inbound()` returns it") only holds if `drain_inbound()` itself
  pumps -- tests (and `test_terrarium_boot.py`'s existing
  `test_o2lite_frame_is_released_across_the_shared_clock`) call
  `drain_inbound()` directly/via `agent.poll()` without a separate call to
  `drain_new_clients()` in between deliver and drain.
- `drain_new_clients()` is a documented no-op for this transport (a device is
  anonymous until `/game/hello`), so putting the pump there would tie a
  side-effecting operation to a method whose entire contract today is "return
  `[]`".
- `agent.poll()` still calls `drain_new_clients()` before `drain_inbound()`
  each tick, and `drain_new_clients()` does not itself pump, so exactly one
  pump happens per agent tick, and it happens before the drain -- satisfying
  the stated requirement either way.

```python
def drain_inbound(self) -> list:
    if self._o2 is not None:
        try:
            self._o2.poll()
        except Exception:
            logger.exception("o2lite poll failed")
    drained, self._inbound = self._inbound, []
    return drained
```

Guarded for `self._o2 is None` (pre-`start()`), and a raising `poll()` is
swallowed rather than propagated -- boundary rule 2, same treatment as
`send()`.

## Tests added (`tests/test_o2_transport.py`)

1. `test_a_delivered_message_is_only_seen_after_a_pump` -- the regression
   test the bug's absence should have had. Delivers `/game/hello`, asserts
   the fake's `_queue` actually holds it (proving the fake queues rather than
   dispatches), then asserts `transport.drain_inbound()` returns it -- which
   can only pass if `drain_inbound()` pumped. Verified TDD-red: temporarily
   reverted only the `drain_inbound()` pump call (keeping the faithful fake)
   and reran -- this test failed with `IndexError`/`assert 0 == 1`, along
   with 4 pre-existing tests in the same file (`test_inbound_game_messages_
   are_drained_as_envelopes`, `test_the_inbound_timestamp_is_carried_from_
   the_message`, `test_an_inbound_blob_is_decoded_back_to_a_value`,
   `test_draining_twice_does_not_repeat_a_message`). Restored the fix; all
   18 tests in the file pass green.
2. `test_a_raising_poll_does_not_escape_drain_inbound` -- monkeypatches
   `fake.poll` to raise, asserts `drain_inbound()` returns `[]` rather than
   propagating.
3. `test_drain_inbound_before_start_does_not_raise` -- calls `drain_inbound()`
   on a transport that never had `start()` called (`self._o2 is None`).

## Existing tests needing an explicit `poll()`

Zero. Every existing call site that uses `FakeO2Lite.deliver()` (all in
`tests/test_o2_transport.py` and one in `tests/test_terrarium_boot.py`) goes
through `transport.drain_inbound()` either directly or via `agent.poll()`, so
the pump embedded in `drain_inbound()` covers them all without modification.
The `.deliver(...)` calls in `test_console_agent.py`, `test_capture_smoke.py`,
`test_devicelink_frames.py`, and `test_devicelink_agent.py` belong to an
unrelated local `FakeServer`/`FakeCapture`-style double, not `FakeO2Lite`, and
were untouched.

## Silent-deny fix (`harness/o2_shroom.py`)

`main()`'s serve loop now checks `client.last_deny` / `client.last_error`
once each iteration (after `o2lite.poll()`), and prints a one-shot message
the first time either becomes non-`None`:

```
JOIN DENIED: <reason> (<hint>)
ERROR from Control: <context>: <message>
```

Uses `print`, matching the module's existing style (no logging setup). Two
boolean flags (`deny_printed`, `error_printed`) keep it from repeating.
Placed inside the polling loop rather than right after `send_cmd("/game/join"
...)` because the deny/error reply is asynchronous -- it only exists once a
later `o2lite.poll()` call has pulled it in.

## Constraints respected

- `control/timed_queue.py`: untouched.
- `harness/shroom_client.py` protocol logic: untouched (only
  `harness/o2_shroom.py`'s `main()` loop was touched, per the task).
- Clock wiring from commit `a7943a0`: untouched.
- No `o2litepy` import added anywhere in `devicelink/o2_transport.py` or
  under `control/` -- verified with `grep -n "o2litepy"` (docstrings/comments
  only) and `python -c "import devicelink.o2_transport"` succeeding with no
  o2litepy on the path.

## Verification

`$PY -m pytest tests -v` -- 614 passed, 1 skipped, 0 failed, 0 errors (611
baseline + 3 new tests in `tests/test_o2_transport.py`).
