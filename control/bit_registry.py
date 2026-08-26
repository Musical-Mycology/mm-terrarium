"""Bit package discovery: scan `bits/*/bit.toml` into a registry of
`BitPackage`s without ever importing Bit code at discovery time.

See .superpowers/sdd/2026-08-21-bit-packaging-and-launch/ for the design.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from control.bit_config import BitConfig, ManifestError, merge_overrides, parse_manifest

_DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "bits"


@dataclass
class BitPackage:
    name: str
    config: BitConfig
    path: Path
    import_root: str


@dataclass
class PackageError:
    path: str
    message: str


class _LazyClassMap(Mapping):
    """A Mapping view over a BitRegistry's packages that imports a Bit's
    class only when it is actually accessed (`registry.bit_class(name)`
    on `__getitem__`), not when the map is built or iterated. Unknown
    names raise KeyError (from the underlying dict lookup); a failing
    import raises the registry's existing ManifestError. Both propagate
    unchanged -- the engine's load_bit already wraps whatever bit_class
    raises in BitLoadError, so this class adds no error handling of its
    own."""

    def __init__(self, registry: "BitRegistry"):
        self._registry = registry

    def __getitem__(self, name: str) -> type:
        return self._registry.bit_class(name)

    def __iter__(self):
        return iter(self._registry.packages)

    def __len__(self) -> int:
        return len(self._registry.packages)


class BitRegistry:
    def __init__(self):
        self.packages: dict[str, BitPackage] = {}
        self.errors: list[PackageError] = []

    @classmethod
    def discover(cls, root: Path | None = None) -> "BitRegistry":
        is_default_root = root is None
        root = root if root is not None else _DEFAULT_ROOT
        registry = cls()

        if not root.is_dir():
            return registry

        for pkg_dir in sorted(root.iterdir()):
            manifest_path = pkg_dir / "bit.toml"
            if not pkg_dir.is_dir() or not manifest_path.is_file():
                continue

            source = str(manifest_path)
            try:
                text = manifest_path.read_text()
                config = parse_manifest(text, source=source)
            except ManifestError as exc:
                registry.errors.append(PackageError(path=source, message=str(exc)))
                continue
            except tomllib.TOMLDecodeError as exc:
                registry.errors.append(PackageError(path=source, message=str(exc)))
                continue

            import_root = (
                f"bits.{pkg_dir.name}" if is_default_root else pkg_dir.name
            )
            name = config.identity.name
            if name in registry.packages:
                registry.errors.append(PackageError(
                    path=source,
                    message=f"duplicate bit name {name!r} (already provided by "
                             f"{registry.packages[name].path})",
                ))
                continue

            registry.packages[name] = BitPackage(
                name=name, config=config, path=pkg_dir, import_root=import_root)

        return registry

    def _import_module(self, pkg: BitPackage, module_name: str):
        if pkg.path.parent != _DEFAULT_ROOT:
            parent = str(pkg.path.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)
        return importlib.import_module(f"{pkg.import_root}.{module_name}")

    def bit_class(self, name: str) -> type:
        pkg = self.packages[name]
        entry = pkg.config.identity.entry
        module_name, _, class_name = entry.partition(":")
        source = str(pkg.path / "bit.toml")
        try:
            module = self._import_module(pkg, module_name)
            return getattr(module, class_name)
        except Exception as exc:
            raise ManifestError(
                source=source, key="bit.entry",
                message=f"failed to load entry {entry!r}: {exc}") from exc

    def lazy_class_map(self) -> Mapping[str, type]:
        """A Mapping over every discovered Bit name whose values import
        lazily on access, suitable to hand straight to build()/GameServer
        so the Console can load_bit() any discovered package, not just
        the one named at boot time."""
        return _LazyClassMap(self)

    def resolve_config(self, name: str, overrides: dict | None = None) -> BitConfig:
        pkg = self.packages[name]
        if not overrides:
            return pkg.config
        return merge_overrides(pkg.config, overrides,
                                source=str(pkg.path / "bit.toml"))

    def _role_summary(self, name: str, config) -> dict | None:
        """Best-effort scored/jam summary for the Load picker. Instantiates
        the Bit class (first console connect pays the import); any failure
        yields None rather than breaking discovery -- a broken Bit already
        fails loudly at load_bit. ROOM-class roles are never counted: they
        bind a fixture's rendering backend, not a player."""
        from control.roles import RoleClass
        try:
            bit = self.bit_class(name)(config=config)
            table = bit.role_table
        except Exception:
            return None
        scored = 0
        shared_open = False
        jam_open = False
        for role in table.roles.values():
            if role.role_class == RoleClass.ROOM:
                continue                     # never leak the Room role
            if role.role_class == RoleClass.JAM:
                jam_open = True
                continue
            if not role.scored:
                continue
            if role.capacity is None:
                # Unbounded scored capacity (SHARED): count as one open
                # slot rather than an unknowable total.
                scored += 1
                if role.role_class == RoleClass.SHARED:
                    shared_open = True
            else:
                scored += role.capacity
        return {"scored": scored, "shared_open": shared_open, "jam_open": jam_open}

    def list_view(self, *, include_hidden: bool = True) -> list[dict]:
        rows = []
        for name in sorted(self.packages):
            pkg = self.packages[name]
            config = pkg.config
            if not include_hidden and config.console.hidden:
                continue
            rows.append({
                "name": config.identity.name,
                "version": config.identity.version,
                "kind": config.identity.kind,
                "description": config.identity.description,
                "display_name": config.console.display_name,
                "hidden": config.console.hidden,
                "room_types": list(config.launch.room_types),
                "start": {
                    "when": config.start.when,
                    "min_scored": config.start.min_scored,
                    "timeout_seconds": config.start.timeout_seconds,
                    "on_timeout": config.start.on_timeout,
                },
                "notes": config.console.notes,
                "roles": self._role_summary(name, config),
            })
        return rows

    def errors_view(self) -> list[dict]:
        return [{"path": e.path, "message": e.message} for e in self.errors]
