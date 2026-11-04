"""MinigameBit: the Towers' four-phase white build (bits/minigame/).
Package discovery, the roles, the beat, the blink's fade, the phase cues
and the responders lit in order at unit level, the tap flash, and role routing (ie<N> players,
devtower<N> Towers) through a real GameServer."""
from pathlib import Path

import pytest

from bits.minigame.minigame_bit import (
    BAR_PIXELS,
    BEAT_S,
    BLACK,
    BLINK_ON_S,
    CLEAR_S,
    FADE_S,
    FLASH_FADE_S,
    HOLD_S,
    MINIGAME_PLAYER_NODE,
    RESPONDER_FADE_S,
    RESPONDER_RGB,
    RESPONDERS,
    TOWER_PIXELS,
    WHITE,
    MinigameBit,
    blink_value,
    group_pixels,
    responder_pixels,
    responder_value,
)
from control.bit_registry import BitRegistry
from control.catalog import load_catalog
from control.cues import PixelFlashCue
from control.engine import GameServer
from control.jam_role import is_solo_role
from control.role_config import validate_role_declarations

ROOT = Path(__file__).resolve().parents[1]
TAP = ["ie1", 0.0, 80.0, 1]
TICK = 1.0 / 44.0


def flash(dev, value, when, start):
    """The flash's 4 LEDs at purple `value`, W at 0, held to the fade's
    end."""
    rgb = (128 * value // 255, 0, value, 0)
    return [PixelFlashCue(dev, px, rgb,
                          pytest.approx(start + FLASH_FADE_S - when), when=when)
            for px in range(4)]


def towers(out):
    """The Tower cues among `out`, as {dev: {pixel: (rgb, duration)}}."""
    lit = {}
    for c in out:
        if isinstance(c, PixelFlashCue):
            lit.setdefault(c.dev, {})[c.pixel] = (c.rgb, c.duration)
    return lit


def solid(phase):
    """The groups a beat of `phase` holds solid, on one Tower."""
    return {px: (WHITE, HOLD_S)
            for g in range(phase - 1) for px in group_pixels(g)}


def bar_cues(out):
    """The cues among `out` on a Tower's bar, not its responders."""
    return [c for c in out if c.pixel < BAR_PIXELS]


def beige(v):
    """A responder's cue colour at level `v`, W at 0."""
    return tuple(c * v // 255 for c in RESPONDER_RGB) + (0,)


def beat(phase, r):
    """A beat's first cues on one Tower: the groups `phase` holds solid,
    and responder r full, held its whole fade."""
    out = solid(phase)
    for px in responder_pixels(r):
        out[px] = (beige(255), RESPONDER_FADE_S)
    return out


def group_w(lit, g):
    """Group g's one W level among `lit` (one Tower's cues), or None when
    the group got no cue."""
    ws = {lit[px][0][3] for px in group_pixels(g) if px in lit}
    if not ws:
        return None
    assert len(ws) == 1 and all(px in lit for px in group_pixels(g)), lit
    return ws.pop()


def responder_lit(lit):
    """The responders among `lit` (one Tower's cues) that got a cue, each
    {r: its one (r, g, b, w)}, every LED of it the same."""
    out = {}
    for r in range(RESPONDERS):
        cues = {lit[px][0] for px in responder_pixels(r) if px in lit}
        if cues:
            assert len(cues) == 1 and all(
                px in lit for px in responder_pixels(r)), lit
            out[r] = cues.pop()
    return out


def blink_levels(bit, start, dev="devtower1"):
    """Tick fires() through one blink from `start`; the blinking group's W
    level as each tick leaves it (the last level sent)."""
    levels, level = [], 0
    t = start
    while t < start + BLINK_ON_S + TICK:
        lit = towers(bit.fires(t)).get(dev, {})
        w = group_w(lit, bit.phase - 1)
        if w is not None:
            level = w
        levels.append(level)
        t += TICK
    return levels


# --- the package, as the Console sees it -----------------------------------

def test_package_is_discovered_and_visible_in_the_console():
    reg = BitRegistry.discover()
    assert "MinigameBit" in reg.packages, reg.errors
    row = next(r for r in reg.list_view(include_hidden=False)
               if r["name"] == "MinigameBit")
    assert row["hidden"] is False
    cfg = reg.resolve_config("MinigameBit", {})
    assert cfg.join_node() == MINIGAME_PLAYER_NODE
    assert reg.bit_class("MinigameBit") is MinigameBit


def test_needs_no_room_light():
    assert MinigameBit().room_manifests() == ({}, {})
    assert MinigameBit.room_types == {"TEST"}


# --- the roles -------------------------------------------------------------

def test_the_tower_role_is_one_dark_aurora():
    tower = MinigameBit().role_table.roles["tower"]
    assert tower.breath is False and tower.scored is False
    assert tower.light_manifest["instruments"] == [
        {"instrument": "aurora", "target": "primary",
         "params": {"hue": 0.0, "level": 0.0}, "lanes": []}]


def test_the_role_table_validates():
    validate_role_declarations(MinigameBit().role_table)


def test_the_groups_are_four_lights_of_two_leds_bottom_first():
    assert [list(group_pixels(g)) for g in range(4)] == [
        list(range(8 * g, 8 * g + 8)) for g in range(4)]


def test_the_responders_are_four_lights_of_24_after_the_bar():
    assert [list(responder_pixels(r)) for r in range(RESPONDERS)] == [
        list(range(32 + 24 * r, 32 + 24 * r + 24)) for r in range(4)]
    assert TOWER_PIXELS == 32 + 96


def test_130_bpm_lit_half_of_each_beat():
    assert BEAT_S == pytest.approx(60 / 130)
    assert BLINK_ON_S == pytest.approx(BEAT_S / 2)
    assert HOLD_S > BEAT_S
    assert 2 * FADE_S < BLINK_ON_S


# --- the blink's fade -------------------------------------------------------

def test_the_blink_fades_in_holds_and_fades_out():
    assert blink_value(0.0) == 0
    assert blink_value(FADE_S / 2) == 128
    assert blink_value(FADE_S) == 255
    assert blink_value(BLINK_ON_S / 2) == 255
    assert blink_value(BLINK_ON_S - FADE_S / 2) == 128
    assert blink_value(BLINK_ON_S) == 0
    assert blink_value(BLINK_ON_S + 0.1) == 0


# --- beats and phases, unit level ------------------------------------------

def _joined_bit(*devs, tower_devs=("devtower1",)):
    bit = MinigameBit()
    for dev in devs or ("ie1",):
        bit.on_join(dev, "player")
    for dev in tower_devs:
        bit.on_join(dev, "tower")
    bit.on_run_start()
    return bit


def test_it_starts_in_phase_one_blinking_the_bottom_group():
    bit = _joined_bit()
    assert bit.status()["phase"] == 1
    levels = blink_levels(bit, 5.0)
    assert levels[0] == 0 and max(levels) == 255
    # up over a few ticks, never jumping straight to full
    rise = levels[:levels.index(255) + 1]
    assert rise == sorted(rise) and len(rise) >= 3


def test_the_fade_steps_hold_until_the_blink_ends_and_go_dark_on_time():
    bit = _joined_bit()
    bit.fires(5.0)
    out = bit.fires(5.0 + TICK)
    assert len(out) == 8 + 24                  # the group and a responder
    assert [c.duration for c in out] == pytest.approx(
        [BLINK_ON_S - TICK] * 8 + [RESPONDER_FADE_S - TICK] * 24)
    assert {c.when for c in out} == {5.0 + TICK}


def test_a_held_level_is_not_resent():
    bit = _joined_bit()
    bit.fires(5.0)
    bit.fires(5.0 + 0.1)                       # full
    # the blink still full, nothing new on the bar (the responder fades on)
    out = towers(bit.fires(5.0 + 0.12))["devtower1"]
    assert all(px >= BAR_PIXELS for px in out), out


def test_each_beat_lights_the_next_responder_in_order():
    bit = _joined_bit()
    order = []
    for n in range(9):
        beat = 5.0 + n * BEAT_S
        bit.fires(beat)
        lit = responder_lit(towers(bit.fires(beat + 0.1))["devtower1"])
        assert len(lit) == 1, lit
        order.append(next(iter(lit)))
    assert order == [0, 1, 2, 3, 0, 1, 2, 3, 0]


def test_a_responder_is_beige_full_on_the_beat_fading_out():
    bit = _joined_bit()
    lit = towers(bit.fires(5.0))["devtower1"]
    assert responder_lit(lit) == {0: beige(255)}
    assert [lit[px][1] for px in responder_pixels(0)] == \
        pytest.approx([RESPONDER_FADE_S] * 24)
    levels = [255]
    t = TICK
    while t < RESPONDER_FADE_S + TICK:
        lit = responder_lit(towers(bit.fires(5.0 + t)).get("devtower1", {}))
        if 0 in lit:
            v = responder_value((5.0 + t) - 5.0)
            assert lit[0] == beige(v)
            levels.append(v)
        t += TICK
    assert levels == sorted(levels, reverse=True) and len(levels) >= 15
    # dark, by the last step's hold lapsing, at RESPONDER_FADE_S
    assert responder_value(RESPONDER_FADE_S) == 0


def test_a_responders_tail_overlaps_the_next_one():
    bit = _joined_bit()
    bit.fires(5.0)
    lit = responder_lit(towers(bit.fires(5.0 + BEAT_S))["devtower1"])
    assert lit == {0: beige(responder_value(BEAT_S)), 1: beige(255)}
    assert responder_value(BEAT_S) > 0


@pytest.mark.parametrize("phase", [1, 2, 3, 4])
def test_the_responders_run_in_every_phase(phase):
    bit = _joined_bit()
    bit.fires(5.0)
    bit._on_phase(phase, 5.01)
    lit = responder_lit(towers(bit.fires(5.0 + BEAT_S))["devtower1"])
    assert lit[1] == beige(255)


def test_the_beat_repeats_at_130_bpm_and_not_between():
    bit = _joined_bit()
    blink_levels(bit, 5.0)
    assert bar_cues(bit.fires(5.0 + BEAT_S - 0.01)) == []
    first = blink_levels(bit, 5.0 + BEAT_S + 0.001)
    assert max(first) == 255


def test_a_stalled_tick_restarts_the_grid_instead_of_bursting():
    bit = _joined_bit()
    bit.actions()["phase2"][1](0.0)
    out = bit.fires(10.0)
    assert {c.when for c in out} == {10.0}
    assert towers(out)["devtower1"] == beat(2, 0)


@pytest.mark.parametrize("phase", [1, 2, 3, 4])
def test_each_phase_blinks_its_group_over_the_groups_below_held(phase):
    bit = _joined_bit()
    blink_levels(bit, 0.0)
    # between blinks: the groups below solid, its own group and up dark
    switch = BLINK_ON_S + 0.05
    out = towers(bit.actions()[f"phase{phase}"][1](switch)).get(
        "devtower1", {})
    assert bit.status()["phase"] == phase
    want = solid(phase)
    for g in range(phase - 1, 4):
        for px in group_pixels(g):
            want[px] = (BLACK, CLEAR_S)
    assert out == want
    # the next beat lands on the original grid, holding the same
    assert bar_cues(bit.fires(BEAT_S - 0.01)) == []
    lit = towers(bit.fires(BEAT_S)).get("devtower1", {})
    for px in responder_pixels(0):             # the last beat's fading tail
        del lit[px]
    assert lit == beat(phase, 1)
    assert max(blink_levels(bit, BEAT_S + TICK)) == 255


def test_a_switch_mid_blink_carries_the_blink_to_the_new_group():
    bit = _joined_bit()
    bit.fires(0.0)
    bit.fires(0.1)                              # group 0 at full
    out = towers(bit.actions()["phase2"][1](0.12))["devtower1"]
    assert {out[px] for px in group_pixels(0)} == {(WHITE, HOLD_S)}
    for px in group_pixels(1):
        assert out[px] == ((0, 0, 0, 255), pytest.approx(BLINK_ON_S - 0.12))
    assert {out[px] for g in (2, 3) for px in group_pixels(g)} == \
        {(BLACK, CLEAR_S)}


def test_a_phase_switch_never_moves_the_beat():
    bit = _joined_bit()
    bit.actions()["phase2"][1](0.0)
    switches = {0.3: 3, 0.9: 4, 1.6: 2, 2.0: 3}
    beats = []
    for n in range(round(6 * BEAT_S / TICK)):
        t = n * TICK
        for at in [at for at in switches if at <= t]:
            bit.actions()[f"phase{switches.pop(at)}"][1](t)
        beats += sorted({c.when for c in bit.fires(t)
                         if isinstance(c, PixelFlashCue)
                         and c.duration == HOLD_S})
    assert len(beats) >= 5
    # every beat exactly on the grid laid at t=0
    assert beats == pytest.approx([k * BEAT_S for k in range(len(beats))])


def test_going_back_a_phase_clears_the_groups_above_at_once():
    bit = _joined_bit()
    bit.actions()["phase4"][1](1.0)
    out = towers(bit.actions()["phase2"][1](2.0))["devtower1"]
    assert {out[px] for g in (1, 2, 3) for px in group_pixels(g)} == \
        {(BLACK, CLEAR_S)}
    assert out[0] == (WHITE, HOLD_S)
    assert towers(bit.fires(2.0))["devtower1"] == beat(2, 0)


def test_the_console_gets_four_phase_buttons_in_order():
    assert [(name, label) for name, (label, _h)
            in MinigameBit().actions().items()] == [
        ("phase1", "Phase 1"), ("phase2", "Phase 2"),
        ("phase3", "Phase 3"), ("phase4", "Phase 4")]


def test_every_tower_shows_the_same_phase():
    bit = _joined_bit(tower_devs=("devtower1", "devtower2"))
    bit.actions()["phase3"][1](0.0)
    assert towers(bit.fires(BEAT_S)) == {
        "devtower1": beat(3, 0), "devtower2": beat(3, 0)}
    lit = towers(bit.fires(BEAT_S + FADE_S))
    assert group_w(lit["devtower1"], 2) == group_w(lit["devtower2"], 2) == 255


def test_a_tower_joining_late_is_picked_up_on_the_next_beat():
    bit = _joined_bit()
    bit.actions()["phase2"][1](0.0)
    bit.on_join("devtower2", "tower")
    assert towers(bit.fires(BEAT_S))["devtower2"] == beat(2, 0)


def test_no_tower_no_tower_cues():
    bit = _joined_bit(tower_devs=())
    assert bit.fires(0.0) == [] and bit.fires(FADE_S) == []
    assert bit.actions()["phase4"][1](1.0) == []
    assert bit.status()["phase"] == 4


def test_taps_never_move_the_towers():
    bit = _joined_bit()
    bit.fires(0.0)
    out = bit.verb_handlers()["tap"]("ie1", TAP, at=0.1)
    assert out == flash("ie1", 255, 0.1, 0.1)
    assert bit.status()["phase"] == 1


def test_taps_from_a_tower_or_a_stranger_are_ignored():
    bit = _joined_bit()
    tap = bit.verb_handlers()["tap"]
    assert tap("devtower1", ["devtower1", 0.0, 80.0, 1], at=0.0) == []
    assert tap("other", ["other", 0.0, 80.0, 1], at=0.0) == []

# --- the tap flash ----------------------------------------------------------

def _flash_bit(*devs):
    """Joined, no Tower, so fires() returns only the flash."""
    return _joined_bit(*devs, tower_devs=())


def test_the_player_role_is_one_dark_purple_aurora():
    player = MinigameBit().role_table.roles["player"]
    assert player.breath is False
    assert player.light_manifest["instruments"] == [
        {"instrument": "aurora", "target": "primary",
         "params": {"hue": 0.75, "level": 0.0}, "lanes": []}]


def test_the_flash_is_full_to_dark_in_a_fifth_of_a_second():
    assert FLASH_FADE_S == pytest.approx(0.2)


def test_the_flash_fades_to_dark_and_then_stops():
    bit = _flash_bit()
    bit.verb_handlers()["tap"]("ie1", TAP, at=5.0)
    assert bit.fires(5.0) == []                     # still at full
    assert bit.fires(5.0 + FLASH_FADE_S / 2) == \
        flash("ie1", 128, 5.0 + FLASH_FADE_S / 2, 5.0)
    assert bit.fires(5.0 + FLASH_FADE_S / 2) == []  # unchanged, nothing sent
    # the last step's hold lapses dark at the fade's end; nothing more is sent
    assert bit.fires(5.0 + FLASH_FADE_S) == []
    assert bit.fires(9.0) == []                     # done


def test_a_tap_mid_fade_restarts_it_from_full():
    bit = _flash_bit()
    tap = bit.verb_handlers()["tap"]
    tap("ie1", TAP, at=0.0)
    bit.fires(0.5 * FLASH_FADE_S)
    retap = 0.75 * FLASH_FADE_S
    assert tap("ie1", TAP, at=retap) == flash("ie1", 255, retap, retap)
    assert bit.fires(retap + FLASH_FADE_S / 2) == \
        flash("ie1", 128, retap + FLASH_FADE_S / 2, retap)


def test_each_shroom_flashes_on_its_own():
    bit = _flash_bit("ie1", "ie2")
    tap = bit.verb_handlers()["tap"]
    tap("ie1", TAP, at=0.0)
    tap("ie2", ["ie2", 0.0, 80.0, 1], at=FLASH_FADE_S / 2)
    assert bit.fires(FLASH_FADE_S) == \
        flash("ie2", 128, FLASH_FADE_S, FLASH_FADE_S / 2)  # ie1 has lapsed


def test_a_tower_or_stranger_tap_flashes_nothing():
    bit = _flash_bit()
    bit.on_join("devtower1", "tower")
    bit.verb_handlers()["tap"]("devtower1", ["devtower1", 0.0, 80.0, 1],
                               at=0.0)
    assert [c for c in bit.fires(0.5) if c.dev != "devtower1"] == []


def test_never_completes_on_its_own():
    assert _joined_bit().update(3600.0) is False


# --- role routing through a real GameServer --------------------------------

def test_devices_are_admitted_by_their_id():
    bit = MinigameBit()
    assert bit.admits("ie1", "player") and bit.admits("ie12", "player")
    assert bit.admits("devtower1", "tower") and bit.admits("devtower12", "tower")
    for dev in ("devtower", "devtower1x", "tower1", "ie", "ie2-rev1",
                "other"):
        assert not bit.admits(dev, "tower")
    for dev in ("devtower1", "ie", "ie2-rev1", "other"):
        assert not bit.admits(dev, "player")

def _running_gs():
    now = [100.0]
    catalog = load_catalog(ROOT / "instruments")
    testshroom = catalog.get("published", "testshroom").instrument
    gs = GameServer({"MinigameBit": MinigameBit}, clock=lambda: now[0],
                    carried_instruments={"testshroom": testshroom})
    gs.load_bit("MinigameBit")
    grants = []
    gs.on_grant = lambda dev, result: grants.append((dev, result))
    return gs, grants, now


def test_towers_players_and_strangers_each_get_their_own_role():
    gs, _grants, _now = _running_gs()
    for dev in ("other", "ie1", "ie2", "devtower1"):
        gs.hello(dev, "dev", "1", instrument="testshroom")
    gs.run()
    roles = {dev: a[1] for dev, a in gs.registration.assignments.items()}
    assert is_solo_role(roles.pop("other"))
    assert roles == {"ie1": "player", "ie2": "player", "devtower1": "tower"}
    assert gs.bit.status()["players"] == ["ie1", "ie2"]
    assert gs.bit.status()["towers"] == ["devtower1"]
    # A walk-up after start is sorted the same way.
    gs.hello("devtower2", "dev", "1", instrument="testshroom")
    gs.hello("ie3", "dev", "1", instrument="testshroom")
    assert gs.registration.assignments["devtower2"][1] == "tower"
    assert gs.registration.assignments["ie3"][1] == "player"


def _flashes_rig():
    gs, _, now = _running_gs()
    flashes = []
    gs.on_pixel_flash = lambda dev, px, rgb, dur, when: \
        flashes.append((dev, px, tuple(rgb)))
    for dev in ("ie1", "devtower1", "devtower2"):
        gs.hello(dev, "dev", "1", instrument="testshroom")
    gs.run()
    return gs, flashes, now


def test_the_blink_reaches_every_tower_through_the_engine():
    gs, flashes, now = _flashes_rig()
    gs.tick(TICK)
    now[0] += FADE_S
    gs.tick(TICK)
    assert sorted(flashes) == sorted(
        [(tower, px, WHITE) for tower in ("devtower1", "devtower2")
         for px in group_pixels(0)]
        + [(tower, px, rgb)
           for rgb in (beige(255), beige(responder_value(FADE_S)))
           for tower in ("devtower1", "devtower2")
           for px in responder_pixels(0)])


def test_a_phase_button_reaches_every_tower_through_the_engine():
    gs, flashes, _now = _flashes_rig()
    assert gs.run_action("phase3") is None
    assert gs.bit.status()["phase"] == 3
    white = {(d, px) for d, px, rgb in flashes if rgb == WHITE}
    assert white == {(tower, px) for tower in ("devtower1", "devtower2")
                     for g in range(2) for px in group_pixels(g)}
    assert gs.run_action("nope") == "unknown action 'nope'"
