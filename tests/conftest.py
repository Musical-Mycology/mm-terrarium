import shutil
from pathlib import Path

import pytest

_REAL_BITS_ROOT = Path(__file__).resolve().parent.parent / "bits"


@pytest.fixture(autouse=True)
def _reset_terrarium_boot_logging():
    """harness/terrarium_boot.py's configure_logging() installs one handler
    on the ROOT logger, tagged .terrarium_boot, and is idempotent -- once
    installed it hands the same handler back rather than installing a fresh
    one. Any test that calls main() (directly, or through a helper like
    tests/test_run_profile.py's _run_main_capturing_build) leaves that
    handler on the real root logger for the rest of the pytest session, so a
    later test's own configure_logging() call gets the earlier test's stale
    handler -- pointed at a stream nobody is reading anymore -- instead of
    one against its own stream. This is suite-wide, not confined to one
    test file: whichever test runs first depends on file order and -k
    filters. This fixture strips every root-logger handler tagged
    terrarium_boot after each test, unconditionally, so the leak cannot
    survive regardless of which tests ran, in what order, or under what
    filter."""
    import logging
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "terrarium_boot", False):
            root.removeHandler(handler)


def _copy_bits_with_metronome_enabled(dest: Path) -> Path:
    """A tmp copy of the real bits/ tree with MetronomeBit's
    [bit] enabled = false flipped back on. MetronomeBit has shipped enabled
    since 2026-09-08 -- its `enabled = false` was removed from
    bits/metronome/bit.toml, and no redesign was ever specified that would
    bring it back. This copy, and the replace("enabled = false\n", "") below,
    are a no-op against today's manifest by design: they exist so that if
    the bit is ever disabled again, tests that load it through a registry
    class map or resolve_config keep working instead of breaking on the
    disabled state."""
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
