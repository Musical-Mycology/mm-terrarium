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
         "params": {"base_hue": 0.33},
         "lanes": [{"source": "note", "dest": "trigger"},
                   {"source": "cc:74", "dest": "base_hue", "curve": "linear"}]},
    ],
}

GOOD_WELCOME = {
    "light": {"instrument": "bloom", "params": {"base_hue": 0.33},
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


def test_validate_rejects_non_dict_welcome():
    role = make_role(welcome="hello")
    with pytest.raises(ValueError, match=r"role 'player' welcome: must be a dict"):
        validate_role_declarations(make_table(role))


def test_validate_names_the_failing_role():
    bad = Role(name="jammer", role_class=RoleClass.JAM, capacity=None,
               scored=False, light_manifest=[])
    with pytest.raises(ValueError, match=r"role 'jammer'"):
        validate_role_declarations(make_table(make_role(), bad))
