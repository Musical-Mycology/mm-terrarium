"""RoomProfile: the Room's own fixture declaration. Pure -- no luxaeterna,
which is the point (see the design spec section 4's correction note)."""

import pathlib

import pytest

from control.room_profile import ROOM_PROFILES, RoomProfile, RoomZone, room_profile
from control.rooms import RoomType


def test_test_room_is_not_shaped_like_a_tuneshroom():
    profile = room_profile(RoomType.TEST)
    assert profile.surface_id == "room_test"
    assert profile.pixel_count == 60
    assert profile.color_order == "GRB"
    assert [z.name for z in profile.zones] == ["left", "center", "right"]


def test_channel_count_is_three_per_pixel():
    assert room_profile(RoomType.TEST).channel_count == 180


def test_zones_tile_the_surface_without_gaps_or_overlap():
    profile = room_profile(RoomType.TEST)
    cursor = 0
    for zone in profile.zones:
        assert zone.start == cursor, f"zone {zone.name} does not abut its predecessor"
        cursor += zone.count
    assert cursor == profile.pixel_count


def test_primary_is_not_declared_here():
    """luxaeterna's SurfaceCapability.zone() synthesizes `primary` on demand,
    and harness/room_surface.py appends it. Declaring it here would make it a
    real zone that the Console would draw on top of every other one."""
    assert "primary" not in [z.name for z in room_profile(RoomType.TEST).zones]


def test_demo_room_raises_rather_than_downgrading():
    """Matches resolve_room_type()'s existing fail-hard-never-downgrade
    contract. DEMO's backend is a deferred follow-up spec."""
    with pytest.raises(NotImplementedError):
        room_profile(RoomType.DEMO)


def test_profile_is_immutable():
    profile = room_profile(RoomType.TEST)
    with pytest.raises(Exception):
        profile.pixel_count = 99


def test_every_room_type_key_maps_to_a_room_profile():
    for key, value in ROOM_PROFILES.items():
        assert isinstance(key, RoomType)
        assert isinstance(value, RoomProfile)


def test_zone_is_a_plain_value():
    zone = RoomZone("left", 0, 20)
    assert (zone.name, zone.start, zone.count) == ("left", 0, 20)


def test_no_control_module_imports_a_renderer_at_module_level():
    """Every control/ module must import, and the whole suite must run, with
    luxaeterna, pyarco and o2litepy absent. A MODULE-LEVEL import breaks that;
    a function-scoped one does not, because it runs only when called.

    Indented imports are deliberately not flagged. control/arco_process.py:37
    carries a lazy `from pyarco.arco_engine import arco` marked
    `# noqa: PLC0415 (lazy by design)` -- probing the Arco subprocess for
    readiness is that module's whole job. The repo states the stricter
    no-import-anywhere rule per-module where it applies (see control/audio.py's
    docstring), not package-wide. See the design spec section 4.
    """
    control_dir = pathlib.Path(__file__).resolve().parent.parent / "control"
    banned = ("luxaeterna", "pyarco", "o2litepy")
    offenders = []
    for path in sorted(control_dir.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line[:1].isspace():          # indented: function-scoped, allowed
                continue
            if not (line.startswith("import ") or line.startswith("from ")):
                continue
            if any(line.split()[1].split(".")[0] == pkg for pkg in banned):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == [], ("control/ must have no module-level renderer "
                             "imports:\n" + "\n".join(offenders))
