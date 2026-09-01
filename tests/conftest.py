import shutil
from pathlib import Path

import pytest

_REAL_BITS_ROOT = Path(__file__).resolve().parent.parent / "bits"


def _copy_bits_with_metronome_enabled(dest: Path) -> Path:
    """A tmp copy of the real bits/ tree with MetronomeBit's
    [bit] enabled = false flipped back on. MetronomeBit ships disabled
    pending redesign (bits/metronome/bit.toml); tests that need to load
    it through a registry class map or resolve_config scan this private
    copy instead of the real manifest, so the shipped disabled state is
    never weakened."""
    root = dest / "bits"
    shutil.copytree(_REAL_BITS_ROOT, root)
    manifest = root / "metronome" / "bit.toml"
    manifest.write_text(manifest.read_text().replace("enabled = false\n", ""))
    return root


@pytest.fixture
def enabled_bits_root(tmp_path):
    return _copy_bits_with_metronome_enabled(tmp_path)


@pytest.fixture
def metronome_enabled_registry(enabled_bits_root):
    """A BitRegistry scanned from enabled_bits_root, for tests that pass a
    registry directly (e.g. harness.run_stack.config_from_args)."""
    from control.bit_registry import BitRegistry
    return BitRegistry.scan((enabled_bits_root,))


@pytest.fixture
def metronome_enabled_scan(monkeypatch, enabled_bits_root):
    """Patches BitRegistry.scan so any caller that builds its own registry
    internally (e.g. harness.terrarium_boot.main(), which the test cannot
    pass a registry into) scans enabled_bits_root instead of the real
    bits/ tree -- MetronomeBit resolves there without touching the
    shipped disabled manifest."""
    from control.bit_registry import BitRegistry
    real_scan = BitRegistry.scan.__func__

    def _scan(cls, roots=None):
        return real_scan(cls, (enabled_bits_root,))

    monkeypatch.setattr(BitRegistry, "scan", classmethod(_scan))
    return enabled_bits_root
