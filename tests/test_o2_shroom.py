import pytest

from harness.o2_shroom import _gestures_ready, build, tilt_sweep


def test_tilt_sweep_stays_in_range():
    """gamma is degrees in [-90, 90]: TestBit._on_tilt clamps to that, and a
    sweep that relied on the clamp would silently flatten at both ends."""
    for step in range(0, 400):
        value = tilt_sweep(step * 0.05)
        assert -90.0 <= value <= 90.0


def test_tilt_sweep_reverses_rather_than_jumping():
    """A ping-pong ramp, not a sawtooth: aurora glides its hue under cc:74,
    and a wrap-around discontinuity reads as a visible snap."""
    samples = [tilt_sweep(step * 0.05) for step in range(0, 200)]
    biggest_step = max(abs(b - a) for a, b in zip(samples, samples[1:]))
    assert biggest_step < 20.0


def test_tilt_sweep_is_periodic():
    """One full period returns to where it started, so the sweep closes its
    loop cleanly rather than drifting. Also pins it as a deterministic
    function of elapsed time: a random walk would make every acceptance run
    a judgement call."""
    from harness.o2_shroom import SWEEP_PERIOD

    assert tilt_sweep(0.0) == tilt_sweep(SWEEP_PERIOD)
    assert tilt_sweep(1.25) == tilt_sweep(1.25 + SWEEP_PERIOD)


def test_build_wires_the_client_and_backend():
    """Mirrors tests/test_room_simulator.py's test_build_wires_the_client_
    and_backend for the same socket-free build() seam: dev id and node
    reach the client, an LED adapter is wired, and serve=False means no
    socket was opened."""
    pytest.importorskip("luxaeterna.backends.websim")

    client, backend = build("ie1", "TEST_PLAYER_NODE", serve=False)

    assert client.dev == "ie1"
    assert client.node == "TEST_PLAYER_NODE"
    assert client.leds is not None
    assert backend.is_open is False
    assert backend.label == "ie1"


def test_no_join_build_uses_the_room_surface():
    client, backend = build("sim-room", serve=False, room_type="TEST", fixture="main")
    assert client.expected_channels == 180


def test_a_player_build_is_unchanged():
    pytest.importorskip("luxaeterna")
    from harness.o2_shroom import build
    client, backend = build("ie1", serve=False)
    assert backend._cap.pixel_count == 12
    assert client.expected_channels == 36


def test_build_stamps_the_capability_with_the_devs_own_id():
    """shroom_capability() used to be called with no surface_id, so every
    device's canvas header read ie0 regardless of who it actually was.
    build() must pass its own `dev` through."""
    pytest.importorskip("luxaeterna")
    from harness.o2_shroom import build
    client, backend = build("ie2", serve=False)
    assert backend._cap.surface_id == "ie2"


# --- Gating gestures on the role: harness/shroom_client.py's ShroomClient
# sets .config in _on_role(), only once Control's granted-role reply has
# actually arrived. main()'s join is sent over TCP (o2lite.send_cmd) but
# gestures go out over UDP (o2lite.send), so without this gate the first
# tilt can overtake the join and reach Control before it is registered --
# "tilt: device not registered". A fake client (no socket, no o2lite)
# is enough to drive _gestures_ready() in isolation. ------------------------

class _FakeClient:
    """Stands in for ShroomClient: _gestures_ready() only ever reads
    .config, so nothing else about the real client is needed here."""

    def __init__(self, config=None):
        self.config = config


def test_gestures_not_ready_before_the_role_arrives():
    assert _gestures_ready(_FakeClient(config=None)) is False


def test_gestures_ready_once_the_role_arrives():
    assert _gestures_ready(_FakeClient(config={"bit_name": "TestBit"})) is True


# --- Parent-death guard. The Room simulator is spawned by
# harness/terrarium_boot.py and, with --no-join, never exits on its own:
# main()'s loop waits for a /release that only a live Control sends. An
# orphan therefore runs forever, and o2litepy reconnects it to the NEXT
# Arco (o2lite.py:912 connects whenever _tcp_socket is None; _id_handler
# at :601 re-announces services on connect), where it claims the same dev
# name. O2 then refuses the new run's own simulator with "not from service
# provider" (o2/src/bridge.cpp:231-237). See docs/superpowers/specs/
# 2026-08-14-room-simulator-service-collision-design.md. ------------------

def test_parent_is_gone_is_false_while_the_parent_still_owns_us():
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(4242, getppid=lambda: 4242) is False


def test_parent_is_gone_is_true_once_we_have_been_reparented():
    """A dead parent's children are reparented to init/launchd (pid 1).

    This is also why the check compares against a RECORDED pid rather than
    watching getppid() for a change: if the parent died before this process
    read its argv, getppid() already reads 1 at startup and a change
    detector would wait forever. Comparison catches both orderings with the
    same expression, which is why there is only one case to test here."""
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(4242, getppid=lambda: 1) is True


def test_parent_is_gone_never_fires_without_an_expected_pid():
    """--exit-with-parent is opt-in. A hand-run device passes nothing and
    must never exit because of this guard."""
    from harness.o2_shroom import parent_is_gone

    assert parent_is_gone(None, getppid=lambda: 1) is False


# --- The service the device just announced may have been refused. O2
# drops a second claimant's /_o2/*/sv with "not from service provider"
# (o2/src/bridge.cpp:231-237) and logs it on the HUB. Measured side by
# side, a refused simulator and an accepted player print the same two
# lines: a watch URL and "clock synced". This gate is what makes them
# distinguishable. -------------------------------------------------------

def test_service_conflict_is_silent_when_the_dev_is_ours():
    from harness.o2_shroom import service_conflict

    assert service_conflict(object(), "sim-room",
                            verify=lambda o2lite, dev: True) is None


def test_service_conflict_names_the_dev_and_the_remedy():
    """The whole cost of this bug was that it was invisible: the refused
    client printed its watch URL and clock-synced exactly like a healthy
    one, and Control saw no error either because the hub routed its frames
    successfully, to the wrong process. The message has to end the
    investigation on the spot."""
    from harness.o2_shroom import service_conflict

    message = service_conflict(object(), "sim-room",
                               verify=lambda o2lite, dev: False)

    assert message is not None
    assert "sim-room" in message
    assert "harness.o2_shroom" in message


def test_service_conflict_asks_about_the_dev_it_was_given():
    """A typo here would check the wrong service and always pass."""
    from harness.o2_shroom import service_conflict

    asked = []

    def _verify(o2lite, dev):
        asked.append(dev)
        return True

    service_conflict(object(), "ie1", verify=_verify)
    assert asked == ["ie1"]


# --- Reconnect re-verification. o2litepy auto-reconnects a device to a
# new hub and stamps a new o2lite.bridge_id, but a service lost in that
# reconnect (its /ie1 announcement never re-arrives) leaves the device
# clock-synced and silent forever -- the STARTUP check already passed
# against the PREVIOUS hub. See harness/o2_shroom.py's reconnect_recheck().
# -----------------------------------------------------------------------

class _FakeO2Bridge:
    def __init__(self, bridge_id):
        self.bridge_id = bridge_id


def test_reconnect_recheck_is_silent_when_the_bridge_id_is_unchanged():
    from harness.o2_shroom import reconnect_recheck

    o2 = _FakeO2Bridge(bridge_id="A")
    calls = []

    def verify(o2lite, dev, timeout=None, resend_interval=None):
        calls.append((dev, timeout, resend_interval))
        return True

    bridge_id, problem = reconnect_recheck(o2, "ie1", "A", verify=verify)

    assert bridge_id == "A"
    assert problem is None
    assert calls == []          # no re-verification when nothing changed


def test_reconnect_recheck_reverifies_exactly_once_on_a_bridge_id_change():
    """The decision from Task 2's review: the reconnect check must pass
    timeout=10.0, resend_interval=2.0 explicitly, not the tight startup
    defaults -- a reconnect can land on a hub that is busy (cold audio
    open), and only the resend window can tell that apart from a real
    conflict."""
    from harness.o2_shroom import reconnect_recheck

    o2 = _FakeO2Bridge(bridge_id="B")
    calls = []

    def verify(o2lite, dev, timeout=None, resend_interval=None):
        calls.append((dev, timeout, resend_interval))
        return True

    bridge_id, problem = reconnect_recheck(o2, "ie1", "A", verify=verify)

    assert bridge_id == "B"
    assert problem is None
    assert calls == [("ie1", 10.0, 2.0)]


def test_reconnect_recheck_reports_a_failed_reverification():
    from harness.o2_shroom import reconnect_recheck

    o2 = _FakeO2Bridge(bridge_id="B")

    bridge_id, problem = reconnect_recheck(
        o2, "ie1", "A", verify=lambda o2lite, dev, timeout=None,
        resend_interval=None: False)

    assert bridge_id == "B"
    assert problem is not None
    assert "ie1" in problem


# --- Unanswered-join hinting. The old message pointed at Control even
# though Control was healthy; the real cause fifteen dropped Control
# replies traced to was a lost service announcement on the HUB side. -----

def test_join_stall_hint_names_the_devs_own_service_and_the_hub_log():
    from harness.o2_shroom import join_stall_hint

    hint = join_stall_hint("ie1")

    assert "ie1" in hint
    assert "service was not found" in hint
    assert "o2debug.log" in hint


# --- Dark-by-design notice. TestBit's jammer is deliberately light-less;
# its black canvas was reported as a failure because a silent role grant
# looks identical to a broken one. -----------------------------------------

def test_role_with_no_light_manifest_prints_the_dark_by_design_notice(capsys):
    pytest.importorskip("luxaeterna.backends.websim")
    from harness.o2_shroom import build
    from devicelink import protocol

    client, _backend = build("ie1", serve=False)
    msg = protocol.encode(protocol.Envelope(
        timestamp=0.0, address="/ie1/role", typespec="b",
        args=[{"bit_name": "jammer", "light_manifest": {}}]))
    client.handle(msg)

    out = capsys.readouterr().out
    assert "role has no light declaration -- canvas stays dark by design" in out


def test_role_with_instruments_stays_silent(capsys):
    pytest.importorskip("luxaeterna.backends.websim")
    from harness.o2_shroom import build
    from devicelink import protocol

    client, _backend = build("ie1", serve=False)
    msg = protocol.encode(protocol.Envelope(
        timestamp=0.0, address="/ie1/role", typespec="b",
        args=[{"bit_name": "player",
              "light_manifest": {"instruments": ["aurora"]}}]))
    client.handle(msg)

    out = capsys.readouterr().out
    assert "canvas stays dark by design" not in out


def test_main_has_exactly_one_backend_close():
    """main() used to close the backend by hand at each exit path -- the
    parent-gone return, the service-conflict SystemExit, and the tick loop's
    finally -- with SIGTERM as a forgotten fourth. A KeyboardInterrupt raised
    anywhere between backend.open() and the tick loop's try: therefore left
    the WebSim backend open and printed an unhandled traceback, which is
    exactly what a live run produced on 2026-08-14.

    Asserted by source inspection rather than by running main(), because
    main() imports o2litepy, which is absent from this offline suite by
    design. The same technique and the same reason as tests/test_signals.py.

    Walks the AST for real backend.close() Call nodes inside main(), rather
    than counting the substring "backend.close()" across the module's raw
    text. A substring count is blind to context: this file's own pre-fix
    comment above sigterm_as_keyboard_interrupt() read "...the exit
    lateness report and backend.close() below are simply lost", and that
    prose alone pushed the count to 4 when only 3 real calls existed,
    colliding with this exact assertion. A Call node cannot appear inside a
    comment or docstring, so this version stays immune to that collision no
    matter what a future contributor writes in prose anywhere in this
    module.

    One close call means one cleanup path. A second appearing means
    per-exit-path cleanup has crept back into main()."""
    import ast
    import inspect

    import harness.o2_shroom

    source = inspect.getsource(harness.o2_shroom)
    tree = ast.parse(source)
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")

    close_calls = [
        node for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "backend"
    ]

    assert len(close_calls) == 1, (
        f"harness/o2_shroom.py's main() calls backend.close() at "
        f"{len(close_calls)} real call site(s) (AST Call nodes, not text "
        f"matches). Exactly one is expected, in the finally block. More "
        f"than one means per-exit-path cleanup has crept back into "
        f"main() -- the same defect that let the SIGTERM path go "
        f"uncovered.")


def test_main_consults_ensure_o2litepy_before_importing_o2litepy():
    """When run by hand (outside run_stack, which already ran this same
    fallback for its children), main() must fall back to the hardcoded
    arco checkout before giving up -- exactly like run_stack.main() does.

    Asserted by source inspection rather than by running main(), for the
    same reason as test_main_has_exactly_one_backend_close: main() imports
    o2litepy, which is absent from this offline suite by design.

    Walks main()'s top-level statements for the ensure_o2litepy() Call and
    the `from o2litepy import o2lite` ImportFrom, and asserts the former
    comes first -- so a caller can never reach the production import
    without having given the fallback a chance to run first.
    """
    import ast
    import inspect

    import harness.o2_shroom

    source = inspect.getsource(harness.o2_shroom)
    tree = ast.parse(source)
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")

    # Compared by lineno, not by walk() order: walk() is breadth-first, so
    # a Call nested inside an `if` (one level deeper than a top-level
    # ImportFrom) can surface after it in walk() even when it appears
    # earlier in the source.
    ensure_call_linenos = [
        node.lineno for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ensure_o2litepy"
    ]
    import_linenos = [
        node.lineno for node in ast.walk(main)
        if isinstance(node, ast.ImportFrom)
        and node.module == "o2litepy"
    ]

    assert ensure_call_linenos, (
        "main() never calls ensure_o2litepy() -- the fallback to the "
        "hardcoded arco checkout is only consulted by run_stack's "
        "children, not by a hand-run o2_shroom.")
    assert import_linenos, "main() no longer imports o2litepy directly."
    assert min(ensure_call_linenos) < min(import_linenos), (
        "main() imports o2litepy before consulting ensure_o2litepy() -- "
        "the fallback must run first so a hand-run o2_shroom without "
        "PYTHONPATH set still finds o2litepy in the arco checkout.")


def test_next_heartbeat_time_advances_by_the_interval():
    from harness.o2_shroom import next_heartbeat_time
    assert next_heartbeat_time(now=10.0, interval=5.0) == 15.0


def test_next_heartbeat_time_disabled_by_a_non_positive_interval():
    from harness.o2_shroom import next_heartbeat_time
    assert next_heartbeat_time(now=10.0, interval=0.0) == float("inf")
    assert next_heartbeat_time(now=10.0, interval=-1.0) == float("inf")


def test_main_resends_hello_inside_a_while_loop():
    """Source-inspection, same technique and reason as
    test_main_has_exactly_one_backend_close: main() imports o2litepy,
    absent from this offline suite by design. main() has TWO while loops
    (the clock-sync wait, then the tick loop), and ast.walk's traversal
    order across sibling subtrees is not a documented guarantee -- so
    this deliberately does not index into "the first While found".
    Instead it walks EVERY While node's own subtree for a
    send_cmd("/game/hello", ...) Call and sums across all of them: since
    the two loops' subtrees are disjoint, this is equivalent to "how many
    hello sends live inside some while loop" without needing to identify
    which loop is which. There must be at least 2 (the join-retry block's
    existing one, inside the tick loop; plus the heartbeat's own) -- the
    clock-sync loop has none. That proves the resend is wired into a loop
    body rather than only sent once at startup.

    Both hello resend sites now go through the local send_hello() helper
    (so the matching /game/canvas always follows), so this also counts
    bare send_hello() Call nodes -- a raw send_cmd("/game/hello", ...)
    inside a while loop no longer exists once both sites are converted."""
    import ast
    import inspect

    import harness.o2_shroom

    source = inspect.getsource(harness.o2_shroom)
    tree = ast.parse(source)
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    while_nodes = [node for node in ast.walk(main)
                  if isinstance(node, ast.While)]
    assert while_nodes, "main() must have at least one while loop"

    def _is_hello_send_cmd(node):
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_cmd"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "/game/hello")

    def _is_send_hello_helper(node):
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "send_hello")

    hello_call_count = sum(
        1 for w in while_nodes for node in ast.walk(w)
        if _is_hello_send_cmd(node) or _is_send_hello_helper(node))
    assert hello_call_count >= 2, (
        f"expected at least 2 hello resends (send_cmd(\"/game/hello\", ...) "
        f"or send_hello()) inside some while loop in main() (join-retry's "
        f"existing one plus the heartbeat's own), found {hello_call_count}")


def test_main_follows_every_hello_send_with_a_canvas_send():
    """The rule under test for this task: wherever /game/hello goes out,
    /game/canvas must follow immediately after with the device's own
    watch URL -- so a Control restart (which re-hellos via the heartbeat)
    re-learns every device's canvas URL, not just the one sent at
    startup. Source-inspection, same reason as the sibling tests in this
    file: main() imports o2litepy, absent from this offline suite.

    Checks two things:
    1. The local send_hello() helper sends /game/hello then /game/canvas,
       in that order.
    2. No bare send_cmd("/game/hello", ...) call remains in main() outside
       the helper's own body -- proving all three hello sites (initial,
       join-retry resend, heartbeat resend) were converted to go through
       the helper rather than just some of them.
    """
    import ast
    import inspect

    import harness.o2_shroom

    source = inspect.getsource(harness.o2_shroom)
    tree = ast.parse(source)
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")

    send_hello_def = next(
        (node for node in ast.walk(main)
         if isinstance(node, ast.FunctionDef) and node.name == "send_hello"),
        None)
    assert send_hello_def is not None, (
        "main() must define a local send_hello() helper")

    def _send_cmd_address(node):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_cmd"
                and node.args
                and isinstance(node.args[0], ast.Constant)):
            return None
        return node.args[0].value

    helper_send_cmd_calls = [
        node for node in ast.walk(send_hello_def)
        if _send_cmd_address(node) is not None]
    helper_addresses = [
        _send_cmd_address(node) for node in helper_send_cmd_calls]
    assert helper_addresses == ["/game/hello", "/game/canvas"], (
        f"send_hello() must send /game/hello then /game/canvas, found "
        f"{helper_addresses}")

    # Pin the canvas call's own arguments, not just its address: a
    # regression sending /game/canvas with the wrong typespec, the wrong
    # dev, or a hardcoded string instead of the computed canvas_url would
    # still pass the address-only check above.
    canvas_call = helper_send_cmd_calls[1]

    def _is_args_dev(node):
        return (isinstance(node, ast.Attribute) and node.attr == "dev"
                and isinstance(node.value, ast.Name)
                and node.value.id == "args")

    def _is_canvas_url_name(node):
        return isinstance(node, ast.Name) and node.id == "canvas_url"

    assert len(canvas_call.args) == 5, (
        f"expected send_cmd(\"/game/canvas\", 0, \"ss\", args.dev, "
        f"canvas_url), found {len(canvas_call.args)} positional args")
    address_arg, seq_arg, typespec_arg, dev_arg, url_arg = canvas_call.args
    assert isinstance(seq_arg, ast.Constant) and seq_arg.value == 0, (
        "the canvas send_cmd's sequence-number arg must be the literal 0")
    assert isinstance(typespec_arg, ast.Constant) and \
        typespec_arg.value == "ss", (
        "the canvas send_cmd's typespec must be the literal \"ss\"")
    assert _is_args_dev(dev_arg), (
        "the canvas send_cmd's dev arg must be args.dev, not a different "
        "expression")
    assert _is_canvas_url_name(url_arg), (
        "the canvas send_cmd's url arg must be the computed canvas_url "
        "name, not a literal or a different expression")

    helper_node_ids = {id(n) for n in ast.walk(send_hello_def)}
    bare_hello_calls = [
        node for node in ast.walk(main)
        if id(node) not in helper_node_ids
        and _send_cmd_address(node) == "/game/hello"]
    assert bare_hello_calls == [], (
        "found a send_cmd(\"/game/hello\", ...) call in main() outside "
        "the send_hello() helper -- every hello site must go through "
        "send_hello() so the canvas send always follows")

    send_hello_call_count = sum(
        1 for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "send_hello")
    assert send_hello_call_count >= 3, (
        f"expected send_hello() to be called at all three hello sites "
        f"(initial, join-retry resend, heartbeat resend), found "
        f"{send_hello_call_count} call(s)")
