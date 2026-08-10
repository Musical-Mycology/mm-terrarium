import pytest

from control.role_config import validate_role_declarations, validate_ugen_manifest
from control.roles import Role, RoleClass, RoleTable


def make_role(name="player", **kwargs):
    return Role(name=name, role_class=RoleClass.SHARED, capacity=None,
                scored=True, **kwargs)


def make_table(*roles):
    return RoleTable(roles={r.name: r for r in roles}, node_map={})


GOOD_MANIFEST = {
    "instruments": [
        {"instrument": "bloom", "target": "primary",
         "params": {"hue": 0.33},
         "lanes": [{"source": "note", "dest": "trigger"},
                   {"source": "cc:74", "dest": "hue", "curve": "linear"}]},
    ],
}

GOOD_WELCOME = {
    "light": {"instrument": "bloom", "params": {"hue": 0.33},
              "duration": 1.5},
    "audio": {"instrument": "chime", "duration": 1.5},
}


def test_validate_accepts_empty_defaults():
    validate_role_declarations(make_table(make_role()))


def test_validate_accepts_full_declaration():
    role = make_role(light_manifest=GOOD_MANIFEST, welcome=GOOD_WELCOME)
    validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_manifest():
    role = make_role(light_manifest=[])
    with pytest.raises(ValueError, match=r"role 'player' light_manifest: must be a dict"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("key", ["welcome", "bit_name", "bit_version", "role"])
def test_validate_rejects_composed_keys_in_authored_manifest(key):
    role = make_role(light_manifest={"instruments": [], key: "x"})
    with pytest.raises(ValueError,
                       match=rf"role 'player' light_manifest: field '{key}' is "
                             r"composed by Control at adoption time"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_list_instruments():
    role = make_role(light_manifest={"instruments": {}})
    with pytest.raises(ValueError,
                       match=r"role 'player' light_manifest: 'instruments' must be a list"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("missing", ["instrument", "target"])
def test_validate_rejects_instrument_decl_missing_required_field(missing):
    decl = {"instrument": "bloom", "target": "primary"}
    del decl[missing]
    role = make_role(light_manifest={"instruments": [decl]})
    with pytest.raises(ValueError,
                       match=rf"role 'player' light_manifest instruments\[0\]: "
                             rf"missing required field '{missing}'"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_instrument_decl():
    role = make_role(light_manifest={"instruments": ["not-a-dict"]})
    with pytest.raises(ValueError,
                       match=r"role 'player' light_manifest instruments\[0\]: must be a dict"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_instrument_params():
    role = make_role(light_manifest={"instruments": [
        {"instrument": "bloom", "target": "primary", "params": ["not", "a", "dict"]}]})
    with pytest.raises(ValueError,
                       match=r"role 'player' light_manifest instruments\[0\]: "
                             r"'params' must be a dict"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("missing", ["source", "dest"])
def test_validate_rejects_lane_missing_required_field(missing):
    lane = {"source": "note", "dest": "trigger"}
    del lane[missing]
    role = make_role(light_manifest={
        "instruments": [{"instrument": "bloom", "target": "primary",
                         "lanes": [lane]}]})
    with pytest.raises(ValueError,
                       match=rf"role 'player' light_manifest instruments\[0\] "
                             rf"lanes\[0\]: missing required field '{missing}'"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_lane():
    role = make_role(light_manifest={"instruments": [
        {"instrument": "bloom", "target": "primary", "lanes": ["not-a-dict"]}]})
    with pytest.raises(ValueError,
                       match=r"role 'player' light_manifest instruments\[0\] "
                             r"lanes\[0\]: must be a dict"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_welcome_without_halves():
    role = make_role(welcome={})
    with pytest.raises(ValueError,
                       match=r"role 'player' welcome: must declare at least one "
                             r"of 'light'/'audio'"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("half", ["light", "audio"])
def test_validate_rejects_welcome_half_without_instrument(half):
    role = make_role(welcome={half: {"duration": 1.0}})
    with pytest.raises(ValueError,
                       match=rf"role 'player' welcome {half!r}: missing required "
                             r"field 'instrument'"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_unknown_welcome_audio_instrument():
    # spec section 7: an unknown instrument name is a validation failure,
    # never a silent no-sound discovered later at role-grant time.
    role = make_role(welcome={"audio": {"instrument": "gong"}})
    with pytest.raises(ValueError,
                       match=r"role 'player' welcome 'audio': unknown "
                             r"instrument 'gong' \(known: \['chime'\]\)"):
        validate_role_declarations(make_table(role))


def test_validate_accepts_known_welcome_audio_instrument():
    # A welcome-audio instrument name light validation would leave alone
    # (unlike light instrument names, which are opaque device-side names).
    role = make_role(welcome={"audio": {"instrument": "chime"}})
    validate_role_declarations(make_table(role))


@pytest.mark.parametrize("half", ["light", "audio"])
def test_validate_rejects_non_dict_welcome_params(half):
    # A known instrument name for each half, so the params check under test
    # is what fires rather than the audio-half unknown-instrument check.
    instrument = "chime" if half == "audio" else "bloom"
    role = make_role(welcome={half: {"instrument": instrument, "params": [1, 2]}})
    with pytest.raises(ValueError,
                       match=rf"role 'player' welcome {half!r}: 'params' must be a dict"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_welcome():
    role = make_role(welcome="hello")
    with pytest.raises(ValueError, match=r"role 'player' welcome: must be a dict"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_welcome_half_value():
    role = make_role(welcome={"light": "bloom"})
    with pytest.raises(ValueError,
                       match=r"role 'player' welcome 'light': must be a dict"):
        validate_role_declarations(make_table(role))


def test_validate_names_the_failing_role():
    bad = Role(name="jammer", role_class=RoleClass.JAM, capacity=None,
               scored=False, light_manifest=[])
    with pytest.raises(ValueError, match=r"role 'jammer'"):
        validate_role_declarations(make_table(make_role(), bad))


from control.role_config import compose_role_config


def test_compose_stamps_provenance_and_folds_welcome_light_half():
    role = make_role(light_manifest=GOOD_MANIFEST, welcome=GOOD_WELCOME)
    config = compose_role_config("test_bit", "0.9", role)
    assert config == {
        "role": "player",
        "class": "SHARED",
        "scored": True,
        "light_manifest": {
            "instruments": GOOD_MANIFEST["instruments"],
            "bit_name": "test_bit",
            "bit_version": "0.9",
            "role": "player",
            "welcome": GOOD_WELCOME["light"],
        },
        "uses": [],
        "samples": [],
    }


def test_compose_with_empty_defaults_ships_bare_provenance():
    config = compose_role_config("test_bit", "", make_role())
    assert config == {
        "role": "player",
        "class": "SHARED",
        "scored": True,
        "light_manifest": {"bit_name": "test_bit", "bit_version": "",
                           "role": "player"},
        "uses": [],
        "samples": [],
    }
    # No welcome declared -> no welcome key; the device falls back to
    # sys:loaded (luxaeterna lifecycle spec section 5).
    assert "welcome" not in config["light_manifest"]


def test_compose_audio_only_welcome_ships_no_welcome_key():
    role = make_role(welcome={"audio": {"instrument": "chime"}})
    config = compose_role_config("test_bit", "", role)
    assert "welcome" not in config["light_manifest"]


def test_compose_light_only_welcome_still_folds():
    role = make_role(welcome={"light": {"instrument": "bloom"}})
    config = compose_role_config("test_bit", "", role)
    assert config["light_manifest"]["welcome"] == {"instrument": "bloom"}


def test_compose_unique_role_class_and_scored_flag():
    role = Role(name="conductor", role_class=RoleClass.UNIQUE, capacity=1,
                scored=False)
    config = compose_role_config("test_bit", "", role)
    assert config["class"] == "UNIQUE"
    assert config["scored"] is False
    assert config["role"] == "conductor"


def test_compose_never_aliases_the_authored_declaration():
    role = make_role(light_manifest={"instruments": [
        {"instrument": "bloom", "target": "primary", "params": {"a": 1}}]},
        welcome={"light": {"instrument": "bloom", "params": {"b": 2}}})
    config = compose_role_config("test_bit", "", role)
    config["light_manifest"]["instruments"][0]["params"]["a"] = 99
    config["light_manifest"]["welcome"]["params"]["b"] = 99
    assert role.light_manifest["instruments"][0]["params"]["a"] == 1
    assert role.welcome["light"]["params"]["b"] == 2


def _role(**kw):
    base = dict(name="player", role_class=RoleClass.SHARED, capacity=None,
                scored=True)
    base.update(kw)
    return Role(**base)


def test_empty_ugen_manifest_is_valid():
    validate_ugen_manifest(_role())                      # {} means "no audio"


def test_ugen_manifest_must_be_a_dict():
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=[]))
    assert "ugen_manifest" in str(ei.value) and "list" in str(ei.value)


def test_ugen_manifest_instruments_must_be_a_list():
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest={"instruments": {}}))
    assert "'instruments' must be a list" in str(ei.value)


def test_ugen_manifest_instrument_field_is_required():
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest={"instruments": [{}]}))
    assert "instruments[0]" in str(ei.value) and "instrument" in str(ei.value)


def test_ugen_manifest_lane_source_must_be_a_cc_reference():
    bad = {"instruments": [{"instrument": "flsyn",
                            "lanes": [{"source": "note", "dest": "cc:74"}]}]}
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=bad))
    assert "lanes[0]" in str(ei.value) and "cc:" in str(ei.value)


def test_ugen_manifest_lane_dest_must_be_a_cc_reference():
    bad = {"instruments": [{"instrument": "flsyn",
                            "lanes": [{"source": "cc:74", "dest": "brightness"}]}]}
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=bad))
    assert "lanes[0]" in str(ei.value) and "brightness" in str(ei.value)


def test_ugen_manifest_drone_requires_key_and_velocity():
    bad = {"instruments": [{"instrument": "flsyn", "drone": {"key": 45}}]}
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=bad))
    assert "drone" in str(ei.value) and "velocity" in str(ei.value)


def test_bad_ugen_manifest_fails_the_bit_at_load():
    # The whole point of load-time validation: a typo'd Bit fails as a
    # BitLoadError, never as a mid-installation surprise.
    from control.engine import BitLoadError, GameServer
    from control.bit import Bit

    class BadBit(Bit):
        version = "0.1"

        @property
        def role_table(self):
            return RoleTable(
                roles={"player": _role(
                    ugen_manifest={"instruments": [{"program": 1}]})},
                node_map={"N": ["player"]})

    gs = GameServer({"bad": BadBit})
    with pytest.raises(BitLoadError):
        gs.load_bit("bad")


def test_bad_welcome_audio_instrument_fails_the_bit_at_load():
    # Same discipline as test_bad_ugen_manifest_fails_the_bit_at_load, but
    # for the welcome-audio instrument name: a typo'd Bit fails as a
    # BitLoadError, never a bare KeyError raised much later from
    # AudioBridge._play_welcome at role-grant time.
    from control.engine import BitLoadError, GameServer
    from control.bit import Bit

    class BadWelcomeBit(Bit):
        version = "0.1"

        @property
        def role_table(self):
            return RoleTable(
                roles={"player": _role(
                    welcome={"audio": {"instrument": "gong"}})},
                node_map={"N": ["player"]})

    gs = GameServer({"bad": BadWelcomeBit})
    with pytest.raises(BitLoadError, match=r"unknown instrument 'gong'"):
        gs.load_bit("bad")


def _plain_role(**kw):
    """A minimal valid Role; kw overrides the fields under test."""
    from control.roles import Role, RoleClass
    return Role(name="player", role_class=RoleClass.SHARED, capacity=None,
                scored=True, **kw)


def test_compose_includes_uses_and_samples():
    from control.role_config import compose_role_config
    role = _plain_role(uses=["tilt", "speaker"], samples=["click", "chime"])
    blob = compose_role_config("test_bit", "0.1", role)
    assert blob["uses"] == ["tilt", "speaker"]
    assert blob["samples"] == ["click", "chime"]


def test_compose_defaults_uses_and_samples_to_empty():
    from control.role_config import compose_role_config
    blob = compose_role_config("test_bit", "0.1", _plain_role())
    assert blob["uses"] == []
    assert blob["samples"] == []


def test_compose_deep_copies_uses_and_samples():
    """A Console or transport consumer must never alias the Bit's lists."""
    from control.role_config import compose_role_config
    role = _plain_role(uses=["tilt"], samples=["click"])
    blob = compose_role_config("test_bit", "0.1", role)
    blob["uses"].append("shake")
    blob["samples"].append("boom")
    assert role.uses == ["tilt"]
    assert role.samples == ["click"]


def test_validate_rejects_non_list_uses():
    import pytest
    from control.role_config import validate_role_declarations
    from control.roles import RoleTable
    table = RoleTable(roles={"player": _plain_role(uses="tilt")}, node_map={})
    with pytest.raises(ValueError, match="uses"):
        validate_role_declarations(table)


def test_validate_rejects_non_string_sample():
    import pytest
    from control.role_config import validate_role_declarations
    from control.roles import RoleTable
    table = RoleTable(roles={"player": _plain_role(samples=["click", 7])},
                      node_map={})
    with pytest.raises(ValueError, match="samples"):
        validate_role_declarations(table)


def test_validate_rejects_empty_string_entry():
    import pytest
    from control.role_config import validate_role_declarations
    from control.roles import RoleTable
    table = RoleTable(roles={"player": _plain_role(uses=[""])}, node_map={})
    with pytest.raises(ValueError, match="uses"):
        validate_role_declarations(table)
