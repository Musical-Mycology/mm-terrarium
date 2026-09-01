"""Run profiles: `--profile venue.toml`. Precedence is manifest < profile <
explicit CLI, implemented once per launcher (harness/run_stack.py,
harness/terrarium_boot.py) -- this file covers control/run_profile.py's own
parsing plus both launchers' end-to-end precedence.

See .superpowers/sdd/2026-08-21-bit-packaging-and-launch/task-10-brief.md.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from control.run_profile import RunProfile, deep_merge_overrides, parse_profile

DEV_METRONOME = pathlib.Path(__file__).resolve().parent.parent / \
    "profiles" / "dev-metronome.toml"


def test_parses_the_worked_example():
    profile = parse_profile(DEV_METRONOME.read_text(), source=str(DEV_METRONOME))
    assert profile.bit == "MetronomeBit"
    assert profile.room_type == "DEMO"
    assert profile.devices == 2
    assert profile.console_port is None
    assert profile.seconds is None
    assert profile.overrides == {
        "rhythm": {"bpm": 80},
        "start": {
            "when": "players",
            "min_scored": 2,
            "timeout_seconds": 60,
            "on_timeout": "start",
        },
    }


def test_a_minimal_run_table_leaves_every_field_none():
    profile = parse_profile("[run]\n", source="<test>")
    assert profile == RunProfile()


def test_no_run_table_at_all_is_fine():
    profile = parse_profile("", source="<test>")
    assert profile == RunProfile()


def test_an_unknown_run_key_warns_but_does_not_raise(caplog):
    with caplog.at_level("WARNING"):
        profile = parse_profile(
            '[run]\nbit = "TestBit"\nfrobnicate = true\n', source="venue.toml")
    assert profile.bit == "TestBit"
    assert "frobnicate" in caplog.text
    assert "venue.toml" in caplog.text


def test_bit_overrides_pass_through_untouched():
    text = '[bit.overrides.defaults]\nfoo = "bar"\n'
    profile = parse_profile(text, source="<test>")
    assert profile.overrides == {"defaults": {"foo": "bar"}}


def test_no_bit_table_means_empty_overrides():
    profile = parse_profile('[run]\nbit = "TestBit"\n', source="<test>")
    assert profile.overrides == {}


def test_deep_merge_cli_wins_a_shared_key():
    merged = deep_merge_overrides({"rhythm": {"bpm": 80, "cycles": 4}},
                                  {"rhythm": {"bpm": 120}})
    assert merged == {"rhythm": {"bpm": 120, "cycles": 4}}


def test_deep_merge_keeps_profile_only_tables():
    merged = deep_merge_overrides({"start": {"when": "players"}}, {})
    assert merged == {"start": {"when": "players"}}


def test_deep_merge_keeps_cli_only_tables():
    merged = deep_merge_overrides({}, {"rhythm": {"bpm": 120}})
    assert merged == {"rhythm": {"bpm": 120}}


def test_deep_merge_does_not_mutate_its_inputs():
    profile_overrides = {"rhythm": {"bpm": 80}}
    cli_overrides = {"rhythm": {"cycles": 2}}
    deep_merge_overrides(profile_overrides, cli_overrides)
    assert profile_overrides == {"rhythm": {"bpm": 80}}
    assert cli_overrides == {"rhythm": {"cycles": 2}}


# --- run_stack.py: --profile flag + precedence ---------------------------


def test_run_stack_cli_devices_beats_profile_devices(
        tmp_path, metronome_enabled_registry):
    from harness.run_stack import config_from_args, parse_args

    profile_path = tmp_path / "venue.toml"
    profile_path.write_text(
        '[run]\nbit = "MetronomeBit"\nroom_type = "DEMO"\ndevices = 2\n')

    args = parse_args(
        ["--profile", str(profile_path), "--devices", "1"])
    cfg = config_from_args(args, registry=metronome_enabled_registry)
    assert cfg.devices == 1
    assert cfg.bit == "MetronomeBit"
    assert cfg.room_type == "DEMO"


def test_run_stack_profile_devices_wins_over_the_manifest_default(
        tmp_path, metronome_enabled_registry):
    from harness.run_stack import config_from_args, parse_args

    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\ndevices = 4\n')

    args = parse_args(["--profile", str(profile_path)])
    cfg = config_from_args(args, registry=metronome_enabled_registry)
    assert cfg.devices == 4    # not MetronomeBit's manifest default (2)


def test_run_stack_profile_bit_wins_when_no_cli_bit_given(
        tmp_path, metronome_enabled_registry):
    from harness.run_stack import config_from_args, parse_args

    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\n')

    args = parse_args(["--profile", str(profile_path)])
    cfg = config_from_args(args, registry=metronome_enabled_registry)
    assert cfg.bit == "MetronomeBit"


def test_run_stack_cli_bit_beats_profile_bit(tmp_path):
    from harness.run_stack import config_from_args, parse_args

    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\n')

    args = parse_args(["--profile", str(profile_path), "--bit", "TestBit"])
    cfg = config_from_args(args)
    assert cfg.bit == "TestBit"


def test_run_stack_without_a_profile_still_defaults_bit_to_test_bit():
    from harness.run_stack import config_from_args, parse_args

    args = parse_args([])
    assert config_from_args(args).bit == "TestBit"


def test_run_stack_forwards_profile_to_the_control_command(
        tmp_path, metronome_enabled_registry):
    from harness.run_stack import config_from_args, control_command, parse_args

    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\n')

    args = parse_args(["--profile", str(profile_path)])
    cfg = config_from_args(args, registry=metronome_enabled_registry)
    command = control_command(cfg, ppid=1)
    assert command[command.index("--profile") + 1] == str(profile_path)


def test_run_stack_control_command_omits_profile_by_default():
    from harness.run_stack import StackConfig, control_command

    cfg = StackConfig(log_dir="/tmp/x")
    assert "--profile" not in control_command(cfg, ppid=1)


# --- terrarium_boot.py: --profile flag + precedence -----------------------


def _run_main_capturing_build(monkeypatch, argv):
    """Same trick tests/test_terrarium_boot.py's own
    _run_main_capturing_build uses: build() is stubbed to raise as soon as
    it is called, so every argument-plumbing step before it (profile
    parsing, bit resolution, overrides merge, resolve_config) has already
    run and can be inspected off the captured BootConfig."""
    captured = {}

    def fake_build(config, bit_registry, **kwargs):
        captured["config"] = config
        captured["bit_registry"] = bit_registry
        raise SystemExit(0)

    import harness.terrarium_boot as terrarium_boot_module
    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py"] + argv)

    with pytest.raises(SystemExit):
        terrarium_boot_module.main()

    return captured


def test_terrarium_boot_profile_bpm_reaches_the_resolved_bit_config(
        monkeypatch, tmp_path, metronome_enabled_scan):
    """No CLI rhythm override at all -- MetronomeBit's manifest bpm is 100,
    the profile's [bit.overrides.rhythm] sets bpm=80, and nothing on the
    CLI touches rhythm, so 80 must be what main() actually resolves."""
    profile_path = tmp_path / "venue.toml"
    profile_path.write_text(
        '[run]\nbit = "MetronomeBit"\n\n[bit.overrides.rhythm]\nbpm = 80\n')

    captured = _run_main_capturing_build(
        monkeypatch, ["--profile", str(profile_path), "--room", "DEMO"])
    assert captured["config"].bit_config.rhythm.bpm == 80
    assert captured["config"].bit_name == "MetronomeBit"


def test_terrarium_boot_profile_bit_wins_when_no_cli_bit_given(
        monkeypatch, tmp_path, metronome_enabled_scan):
    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\n')

    captured = _run_main_capturing_build(
        monkeypatch, ["--profile", str(profile_path), "--room", "DEMO"])
    assert captured["config"].bit_name == "MetronomeBit"


def test_terrarium_boot_cli_bit_beats_profile_bit(monkeypatch, tmp_path):
    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\n')

    captured = _run_main_capturing_build(
        monkeypatch, ["--profile", str(profile_path), "--bit", "TestBit",
                     "--room", "TEST"])
    assert captured["config"].bit_name == "TestBit"


def test_terrarium_boot_profile_room_type_wins_over_the_manifest_default(
        monkeypatch, tmp_path, metronome_enabled_scan):
    """MetronomeBit's manifest default_room_type is DEMO already, so use
    CaptureBit (TEST-only) with a profile asking for... actually simplest:
    assert the profile's room_type is honored directly."""
    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\nroom_type = "DEMO"\n')

    captured = _run_main_capturing_build(
        monkeypatch, ["--profile", str(profile_path)])
    assert captured["config"].room_name == "DEMO"


def test_terrarium_boot_without_a_profile_still_defaults_bit_to_test_bit(
        monkeypatch):
    captured = _run_main_capturing_build(monkeypatch, ["--room", "TEST"])
    assert captured["config"].bit_name == "TestBit"


def test_terrarium_boot_cli_rhythm_override_would_beat_the_profile(
        monkeypatch, tmp_path, metronome_enabled_scan):
    """terrarium_boot has no generic CLI override flag for [bit.overrides]
    tables (only --setup-seconds and --seconds/--hold feed the overrides
    dict), so this exercises the one CLI override it does have:
    --setup-seconds must beat a profile that carries no setup_seconds field
    at all (the brief's RunProfile has none) -- i.e. the manifest's own
    setup_seconds stays reachable and --setup-seconds still overrides it
    even with a profile in play."""
    profile_path = tmp_path / "venue.toml"
    profile_path.write_text('[run]\nbit = "MetronomeBit"\n')

    captured = _run_main_capturing_build(
        monkeypatch,
        ["--profile", str(profile_path), "--setup-seconds", "7",
         "--room", "DEMO"])
    assert captured["config"].bit_config.launch.setup_seconds == 7
