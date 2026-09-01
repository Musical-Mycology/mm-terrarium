"""A disabled bit is refused with a located message, not a traceback,
from both CLI launchers."""
import pytest

from control.bit_registry import BitRegistry


def _registry_with_disabled(tmp_path):
    pkg = tmp_path / "off"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(
        '[bit]\nname = "OffBit"\nentry = "m:C"\n'
        "requires_terrarium_api = 1\nenabled = false\n")
    return BitRegistry.scan([tmp_path])


def test_run_stack_refuses_disabled_bit(tmp_path, capsys):
    from harness.run_stack import config_from_args, parse_args
    reg = _registry_with_disabled(tmp_path)
    args = parse_args(["--bit", "OffBit"])
    with pytest.raises(SystemExit):
        config_from_args(args, registry=reg)
    assert "disabled" in capsys.readouterr().err
