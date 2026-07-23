import pytest

from control.role_config import validate_role_declarations
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


@pytest.mark.parametrize("half", ["light", "audio"])
def test_validate_rejects_non_dict_welcome_params(half):
    role = make_role(welcome={half: {"instrument": "bloom", "params": [1, 2]}})
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
    }


def test_compose_with_empty_defaults_ships_bare_provenance():
    config = compose_role_config("test_bit", "", make_role())
    assert config == {
        "role": "player",
        "class": "SHARED",
        "scored": True,
        "light_manifest": {"bit_name": "test_bit", "bit_version": "",
                           "role": "player"},
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
