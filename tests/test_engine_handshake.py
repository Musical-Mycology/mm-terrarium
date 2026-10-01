"""GameServer's contract v3 entry path (spec 2026-10-01-instrument-
handshake-protocol sections 3.4-3.7): round id, handshake validation,
materialize at run, jam and solo grants, RUNNING walk-ups."""
import pytest

from bits.test.test_bit import TestBit
from control.bit import Bit
from control.bit_config import (BitConfig, BitIdentity, ConsoleBlock,
                                LaunchConfig, StartCondition)
from control.engine import BitLoadError, GameServer
from control.instrument import InstrumentRequirement
from control.lobby import LobbyConfig
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.roles import Role, RoleClass, RoleTable
from control.rooms import Room
from control.state import State
from tests.helpers_admit import admit, admit_running
from tests.instrument_fixtures import GENERIC_SURFACE


class _Bit(Bit):
    def __init__(self, config=None, jam=True, cap=1):
        super().__init__(config)
        self._jam, self._cap = jam, cap
        self.joined = []

    @property
    def role_table(self):
        roles = {"player": Role("player", RoleClass.UNIQUE, self._cap, True)}
        nodes = {"P": ["player"]}
        if self._jam:
            roles["jammer"] = Role("jammer", RoleClass.JAM, None, False)
            nodes["J"] = ["jammer"]
        return RoleTable(roles=roles, node_map=nodes)

    def on_join(self, dev, role_name):
        self.joined.append((dev, role_name))


class _NoJamBit(_Bit):
    def __init__(self, config=None):
        super().__init__(config, jam=False)


class _Cap3Bit(_Bit):
    def __init__(self, config=None):
        super().__init__(config, cap=3)


def _gs(bit=_Bit):
    gs = GameServer({"B": bit}, clock=lambda: 100.0)
    gs.load_bit("B")
    grants = []
    gs.on_grant = lambda dev, result: grants.append((dev, result))
    return gs, grants


def _config(*, start=None, lobby=None):
    return BitConfig(identity=BitIdentity(name="B"), launch=LaunchConfig(),
                     start=start or StartCondition(),
                     console=ConsoleBlock(), lobby=lobby or LobbyConfig())


def test_round_id_minted_and_cleared():
    gs, _ = _gs()
    rid = gs.round_id
    assert rid and rid.startswith("B-")
    gs.abort()
    assert gs.round_id is None


def test_round_id_differs_per_load():
    gs, _ = _gs()
    first = gs.round_id
    gs.abort()
    gs.load_bit("B")
    assert gs.round_id and gs.round_id != first


def test_handshake_requires_hello():
    gs, _ = _gs()
    r = gs.handshake("a", gs.round_id, "")
    assert not r.granted and r.reason == "not connected"


def test_handshake_without_bit_is_registration_closed():
    gs = GameServer({"B": _Bit})
    gs.hello("a", "", "", None)
    r = gs.handshake("a", "x", "")
    assert r.reason == "registration closed" and r.hint == "no Bit loaded"


def test_handshake_validates_default_node_without_role_send():
    gs, grants = _gs()
    gs.hello("a", "", "", None)
    r = gs.handshake("a", gs.round_id, "")
    assert r.granted and r.role == "player"
    assert r.config is None
    assert grants == [] and gs.bit.joined == []
    assert "a" in gs.registration.validated


def test_hello_name_change_fires_devices_change():
    gs, _ = _gs()
    seen = []
    gs.add_observer(type("O", (), {"on_devices_change":
                    lambda self: seen.append(1)})())
    gs.hello("a", "One", "", None)
    gs.hello("a", "One", "", None)
    assert seen == [1]
    gs.hello("a", "Renamed", "", None)
    assert seen == [1, 1]


class _MicBit(_Bit):
    """The scored role requires a slot demanding audio.mic, which
    defaultshroom lacks and tuneshroom has."""

    @property
    def role_table(self):
        t = super().role_table
        t.roles["player"] = Role("player", RoleClass.UNIQUE, self._cap, True,
                                 requires="hand")
        return t

    def instrument_requirements(self):
        return (InstrumentRequirement(
            slot="hand", capabilities=frozenset({"audio.mic",
                                                 "light.pixels"})),)


def test_requires_miss_denies_with_slot_contract_hint():
    gs, _ = _gs(_MicBit)
    gs.hello("a", "", "", None)                 # defaultshroom
    r = gs.handshake("a", gs.round_id, "")
    assert not r.granted and "lacks capability" in r.reason
    assert r.hint == "this role needs: audio.mic, light.pixels"
    assert gs.registration.validated == {}
    gs.hello("b", "", "", "tuneshroom")
    assert gs.handshake("b", gs.round_id, "").granted


def test_solo_refusal_is_logged(caplog, monkeypatch):
    gs, _ = _gs(_NoJamBit)
    gs.hello("c", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    monkeypatch.setattr(gs, "fire_function", lambda *a, **k: "nope")
    with caplog.at_level("INFO", logger="control.engine"):
        assert gs.data("c", "tap", ["c", 1.0, 50.0, 1]) is None
    assert any("refused: nope" in rec.getMessage() for rec in caplog.records)


def test_handshake_names_unknown_node():
    gs, _ = _gs()
    gs.hello("a", "", "", None)
    r = gs.handshake("a", gs.round_id, "NOPE")
    assert r.reason == "no such node"


def test_repeat_handshake_is_idempotent_and_notifies_once():
    gs, _ = _gs()
    changes = []
    gs.add_observer(type("O", (), {"on_registration_change":
                    lambda self: changes.append(1)})())
    gs.hello("a", "", "", None)
    assert gs.handshake("a", gs.round_id, "").granted
    again = gs.handshake("a", gs.round_id, "")
    assert again.granted and again.role == "player"
    assert len(changes) == 1


def test_stale_round_dropped():
    gs, _ = _gs()
    gs.hello("a", "", "", None)
    r = gs.handshake("a", "old-round", "")
    assert not r.granted and r.reason is None   # dropped silently
    assert gs.registration.validated == {}


def test_over_cap_then_jam_at_run():
    gs, grants = _gs()
    for d in ("a", "b", "c"):
        gs.hello(d, "", "", None)
    assert gs.handshake("a", gs.round_id, "").granted
    over = gs.handshake("b", gs.round_id, "")
    assert over.reason == "scored full" and over.hint
    gs.request_start(None, "terrarium", "test")
    assert gs.state is State.RUNNING
    assert [(d, r.role, r.scored) for d, r in grants] == [
        ("a", "player", True), ("b", "jammer", False), ("c", "jammer", False)]
    assert all(r.config is not None for _, r in grants)
    assert gs.bit.joined == [("a", "player"), ("b", "jammer"), ("c", "jammer")]


def test_run_notifies_registration_and_devices_once():
    gs, _ = _gs()
    seen = []
    gs.add_observer(type("O", (), {
        "on_registration_change": lambda self: seen.append("reg"),
        "on_devices_change": lambda self: seen.append("dev")})())
    gs.hello("a", "", "", None)
    gs.hello("b", "", "", None)
    seen.clear()
    gs.request_start(None, "terrarium", "test")
    assert seen == ["reg", "dev"]


def test_handshake_in_running_denied():
    gs, _ = _gs()
    gs.hello("a", "", "", None)
    gs.request_start(None, "terrarium", "test")
    gs.hello("z", "", "", None)
    r = gs.handshake("z", gs.round_id, "")
    assert r.reason == "registration closed"
    assert "jam role" in r.hint


def test_running_walk_up_gets_jam_once():
    gs, grants = _gs()
    gs.request_start(None, "terrarium", "test")
    gs.hello("w", "", "", None)
    gs.hello("w", "", "", None)
    assert [(d, r.role) for d, r in grants] == [("w", "jammer")]


def test_admin_dev_never_gets_a_jam_role():
    gs, grants = _gs()
    gs.hello("terrarium", "", "", None)
    gs.request_start(None, "terrarium", "test")
    assert grants == []


def test_no_jam_role_gets_solo():
    gs, grants = _gs(_NoJamBit)
    gs.hello("c", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    (dev, r), = grants
    assert r.role == "solo:tuneshroom" and r.scored is False
    # compose_role_config stamps role_class.name, which is upper case.
    assert r.config["class"] == "JAM"


def test_reap_frees_validated_slot():
    t = [100.0]
    gs = GameServer({"B": _Bit}, clock=lambda: t[0])
    gs.load_bit("B")
    gs.hello("a", "", "", None)
    gs.handshake("a", gs.round_id, "")
    t[0] += 20
    gs.hello("b", "", "", None)
    gs.reap_stale(15.0)
    assert gs.handshake("b", gs.round_id, "").granted


def test_reap_of_validated_dev_does_not_call_on_release():
    t = [100.0]
    gs = GameServer({"B": _Bit}, clock=lambda: t[0])
    gs.load_bit("B")
    released = []
    gs.on_release = released.append
    changes = []
    gs.add_observer(type("O", (), {"on_registration_change":
                    lambda self: changes.append(1)})())
    gs.hello("a", "", "", None)
    gs.handshake("a", gs.round_id, "")
    changes.clear()
    t[0] += 20
    assert gs.reap_stale(15.0) == ["a"]
    assert released == [] and changes == [1]
    assert gs.registration.validated == {}


def test_default_instrument_is_defaultshroom():
    gs, grants = _gs()
    gs.hello("c", "", "", None)
    gs.request_start(None, "terrarium", "test")
    assert grants[0][1].config["instrument"]["name"] == "defaultshroom"


def test_lobby_full_follows_validated():
    gs, _ = _gs()
    gs.hello("a", "", "", None)
    assert gs.lobby_state() == "WAITING"
    gs.handshake("a", gs.round_id, "")
    assert gs.lobby_state() == "FULL"


def test_solo_binding_fires_instrument_function():
    gs, _ = _gs(_NoJamBit)
    fired = []
    gs.add_observer(type("O", (), {"on_function_fired":
                    lambda self, rec: fired.append(rec)})())
    gs.hello("c", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    assert gs.data("c", "tap", ["c", 1.0, 50.0, 1]) is None
    assert fired and fired[-1].name == "play_aurora"
    assert fired[-1].devs == ("c",)
    assert gs.data("c", "tap", ["c", 1.0, 50.0, 2]) is None
    assert fired[-1].name == "win"
    count = len(fired)
    assert gs.data("c", "tilt", ["c", 10.0]) is None     # unbound: dropped
    assert len(fired) == count


def test_hello_heartbeat_does_not_fire_devices_change():
    gs, _ = _gs()
    seen = []
    gs.add_observer(type("O", (), {"on_devices_change":
                    lambda self: seen.append(1)})())
    gs.hello("a", "", "", None)
    assert seen == [1]
    gs.hello("a", "", "", None)
    gs.hello("a", "", "", "defaultshroom")   # same instrument as before
    assert seen == [1]
    gs.hello("a", "", "", "tuneshroom")      # instrument changed
    assert seen == [1, 1]


def test_players_start_counts_validated_devices():
    cfg = _config(start=StartCondition(when="players", min_scored=1))
    gs = GameServer({"B": _Bit})
    gs.load_bit("B", config=cfg)
    gs.hello("a", "", "", None)
    assert gs.request_start(None, None, "timer") == "minimum not met"
    assert gs.handshake("a", gs.round_id, "").granted
    assert gs.request_start(None, None, "timer") is None
    assert gs.state is State.RUNNING


def test_max_scored_lowers_the_validation_cap():
    gs = GameServer({"B": _Cap3Bit})
    gs.load_bit("B", config=_config(lobby=LobbyConfig(max_scored=1)))
    assert gs.registration.scored_cap() == 1
    gs.hello("a", "", "", None)
    gs.hello("b", "", "", None)
    assert gs.handshake("a", gs.round_id, "").granted
    assert gs.handshake("b", gs.round_id, "").reason == "scored full"


def test_max_scored_above_bounded_capacity_refuses_load():
    gs = GameServer({"B": _Bit})
    with pytest.raises(BitLoadError, match="max_scored"):
        gs.load_bit("B", config=_config(lobby=LobbyConfig(max_scored=2)))
    assert gs.state is State.IDLE and gs.round_id is None


def test_default_scored_node_uses_first_scored_node():
    gs, _ = _gs()
    assert gs.default_scored_node() == "P"


def test_default_scored_node_none_without_bit():
    assert GameServer({"B": _Bit}).default_scored_node() is None


def test_notify_devices_changed_is_public():
    gs, _ = _gs()
    seen = []
    gs.add_observer(type("O", (), {"on_devices_change":
                    lambda self: seen.append(1)})())
    gs.notify_devices_changed()
    assert seen == [1]


ROOM_PROFILE = RoomProfile(surface_id="room_test", fixtures=(
    RoomFixture(name="main", color_order="GRB",
                blocks=(RoomBlock("main", 0, 10),),
                zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),))


class _RoomCapableBit(TestBit):
    room_types = {"TEST"}


def _room_gs():
    binding = RoomBindingRegistry()
    gs = GameServer({"R": _RoomCapableBit}, room_binding=binding)
    gs.room = Room(name="TEST", profile=ROOM_PROFILE, node_id="ROOM_TEST_NODE")
    gs.load_bit("R")
    grants = []
    gs.on_grant = lambda dev, result: grants.append((dev, result))
    return gs, binding, grants


def test_room_node_handshake_denied_while_unarmed():
    gs, _, _ = _room_gs()
    gs.hello("fx", "", "", None)
    r = gs.handshake("fx", "any-round", "ROOM_TEST_NODE")
    assert r.reason == "no such node"


def test_room_node_handshake_without_hello_is_not_connected():
    # Spec 3.4 step 1 precedes the Room-node branch, armed or not.
    gs, binding, _ = _room_gs()
    binding.arm("TEST", "main", window_seconds=10.0)
    r = gs.handshake("fx", "any-round", "ROOM_TEST_NODE")
    assert r.reason == "not connected"
    assert gs.room.bound == {}


def test_bound_fixture_cannot_validate_a_scored_slot():
    gs, binding, grants = _room_gs()
    binding.arm("TEST", "main", window_seconds=10.0)
    gs.hello("fx", "", "", None)
    assert gs.handshake("fx", gs.round_id, "ROOM_TEST_NODE").granted
    r = gs.handshake("fx", gs.round_id, "")
    assert r.reason == "registration closed"
    assert r.hint == "this device is bound to a Room fixture"
    assert gs.registration.validated == {}
    assert gs.registration.assignments["fx"][2] is RoleClass.ROOM


def test_room_node_handshake_binds_and_skips_jam_sweep():
    gs, binding, grants = _room_gs()
    binding.arm("TEST", "main", window_seconds=10.0)
    gs.hello("fx", "", "", None)
    r = gs.handshake("fx", "ignored-round", "ROOM_TEST_NODE")
    assert r.granted and r.role_class == RoleClass.ROOM and r.config is None
    assert gs.room.bound == {"main": "fx"}
    gs.request_start(None, "terrarium", "test")
    assert grants == []


def test_admit_helpers():
    gs, grants = _gs()
    r = admit(gs, "a")
    assert r.granted and gs.state is State.SETUP
    r = admit_running(gs, "b")
    assert r.reason == "scored full"
    assert gs.state is State.RUNNING
    assert [(d, g.role) for d, g in grants] == [("a", "player"),
                                                ("b", "jammer")]


def test_lobby_uncapped_scored_role_never_fills():
    gs = GameServer({"T": TestBit})      # TestBit's player is uncapped
    gs.load_bit("T")
    admit(gs, "a")
    assert gs.lobby_state() == "WAITING"


def test_lobby_without_scored_roles_waits():
    # Cap 0: nothing to fill, so the lobby waits; everyone gets a jam role
    # at start.
    class _JamOnly(Bit):
        @property
        def role_table(self):
            return RoleTable(
                roles={"jammer": Role("jammer", RoleClass.JAM, None, False)},
                node_map={"J": ["jammer"]})
    gs = GameServer({"J": _JamOnly})
    gs.load_bit("J")
    assert gs.lobby_state() == "WAITING"


class _UnscoredBit(_Bit):
    """Only unscored, non-JAM roles (the CaptureBit / MinigameBit shape)."""
    roles_spec = ()

    @property
    def role_table(self):
        roles = {r.name: r for r in self.roles_spec}
        return RoleTable(roles=roles, node_map={})


def _unscored_gs(*roles):
    bit = type("U", (_UnscoredBit,), {"roles_spec": roles})
    return _gs(bit)


def test_shared_unscored_role_is_the_jam_role():
    gs, grants = _unscored_gs(Role("recorder", RoleClass.SHARED, None, False))
    gs.hello("a", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    assert [(d, r.role) for d, r in grants] == [("a", "recorder")]


def test_unique_unscored_role_capacity_consumed_in_pool_order():
    gs, grants = _unscored_gs(Role("player", RoleClass.UNIQUE, 1, False))
    for d in ("a", "b", "c"):
        gs.hello(d, "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    assert [(d, r.role) for d, r in grants] == [
        ("a", "player"), ("b", "solo:tuneshroom"), ("c", "solo:tuneshroom")]


def test_unscored_role_requires_miss_falls_to_next_role():
    class _B(_UnscoredBit):
        roles_spec = (Role("miccer", RoleClass.SHARED, None, False,
                           requires="hand"),
                      Role("plain", RoleClass.SHARED, None, False))

        def instrument_requirements(self):
            return (InstrumentRequirement(
                slot="hand", capabilities=frozenset({"audio.mic"})),)
    gs, grants = _gs(_B)
    gs.hello("a", "", "", None)             # defaultshroom lacks audio.mic
    gs.hello("b", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    assert [(d, r.role) for d, r in grants] == [("a", "plain"),
                                                ("b", "miccer")]


def test_jam_class_preferred_over_earlier_unscored_role():
    gs, grants = _unscored_gs(
        Role("recorder", RoleClass.SHARED, None, False),
        Role("jammer", RoleClass.JAM, None, False))
    gs.hello("a", "", "", None)
    gs.request_start(None, "terrarium", "test")
    assert [(d, r.role) for d, r in grants] == [("a", "jammer")]
