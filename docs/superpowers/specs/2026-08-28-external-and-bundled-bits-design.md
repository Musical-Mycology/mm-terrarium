# External and bundled Bits (Spec 4 of the Room/Instrument/Trigger restructure)

Date: 2026-08-28
Status: draft, awaiting review
Authority: `docs/superpowers/specs/2026-08-26-terrarium-lifecycle-and-config-rooms-design.md`
section 12 ("Spec 4 -- External and bundled Bits") records the agreed
direction this spec instantiates, with one deliberate revision to it
(section 4 below: the dedicated Bits repo is dropped in favor of
instrument-repo-hosted Bits).

Builds on, in order:

- Spec 1 -- Terrarium lifecycle and config-defined rooms (landed
  2026-08-27): `bit_paths` in `terrarium.toml`, `resolve_bit_roots()`,
  multi-root `BitRegistry.scan()`.
- Spec 2 -- Instruments and Fixtures (landed 2026-08-27): capability
  contracts; a Bit targets instruments by declared requirements, not by
  location.
- Spec 3 -- Functions and the Trigger rename (landed 2026-08-27, PR #62):
  the Bit interface this spec's API version freezes. Its Status section
  records execution deviations (`FireFunction.at`, engine edge-clamp,
  raw-domain stream matching, verb-scoped overlap, and more) that are now
  permanent engine behavior -- the API this spec versions is the as-built
  one, not the Spec 3 prose.

**Recorded dependency, not a blocker:** the Spec 1, Spec 2, and Spec 3
live-Arco checklists are all still unrun (see each spec's Status
section). Spec 4 adds its own live checklist (section 8) to that queue;
nothing here waits on them.

## 1. What this spec adds, in one paragraph

An external Bit -- one living outside mm-terrarium's own `bits/` tree --
becomes a supported, safe thing rather than an accident of
`BitRegistry.scan()` accepting any root. Three mechanisms make it so: a
**versioned Bit API contract** (`requires_terrarium_api` in `bit.toml`,
checked at discovery, so an incompatible package is a located refusal
instead of a runtime surprise), the **`[assets]` manifest section made
real** (package files resolved only through config, so a Bit behaves
identically in-repo, in an external checkout, or unpacked from a
bundle), and a **bundling tool** (`tools/bundle_bit.py`: one archive,
per-file integrity hashes, provenance stamp -- because an external
bundle is code the venue box executes). External Bits live in the repo
of the instrument they target, starting with `mm-tuneshroom/bits/`.

## 2. The versioned Bit API contract

### 2.1 `TERRARIUM_API`

New module `control/api_version.py`:

```python
TERRARIUM_API = 1
```

One integer, defined exactly once, bumped only on a breaking change to
the Bit-facing contract. The contract it names is the union of:

- the `Bit` interface (`control/bit.py`) as of Spec 3: `role_table`,
  lifecycle hooks, `verb_handlers()` with `(dev, args, at)`,
  `fires(at)`, `room_manifests()`, `instrument_requirements()`,
  `on_join`, `result()`, `status()`; `Bit(config: BitConfig)` as the
  one constructor signature;
- manifest schema v1 (`bit.toml` as parsed by `control/bit_config.py`),
  including this spec's own additions;
- the cue vocabulary a Bit may emit (`LightCue`, `SolidCue`, `PlayCue`,
  `MuteCue`, `FireFunction`) and the Function/Trigger declaration
  shapes.

Additive changes (a new optional hook with a base-class default, a new
optional manifest table) do NOT bump the integer -- that is the same
growth path manifest schema v1 already established with its
unknown-keys-warn rule. A bump means "a Bit written against the old
number can misbehave", nothing weaker.

### 2.2 `requires_terrarium_api` in `bit.toml`

`[bit]` gains a **required** integer key:

```toml
[bit]
name = "GlowBit"
requires_terrarium_api = 1
```

Checked at **discovery**, in `BitRegistry.scan()`, before any Bit code
imports (discovery still executes no Bit code):

- missing key -> located `PackageError`
  (`.../bit.toml: [bit.requires_terrarium_api] required as of Terrarium API v1`);
- wrong type (bool included -- TOML `true` must not pass as `1`) ->
  located `PackageError`;
- value != `TERRARIUM_API` -> located `PackageError` naming both
  numbers (`requires Terrarium API 2, this engine provides 1`).

A failing package is disabled exactly like a broken manifest today: its
error is collected, `--list-bits` and the Console surface it, every
other package still discovers. Equality, not `<=`: the engine provides
exactly one API version at a time, and "older Bit on a newer engine"
compatibility is a claim the engine makes by NOT bumping the integer,
never by range arithmetic in the registry.

All three in-repo packages (`bits/test`, `bits/metronome`,
`bits/capture`) gain the key this slice. There is no grace path for the
in-repo default root -- one rule for every root, or the external gate is
the untested one.

### 2.3 `min_terrarium` retired

`BitIdentity.min_terrarium` was parsed and never enforced (landed with
manifest schema v1, 2026-08-21, as a reserved key). It is removed from
the schema and the dataclass. A manifest still carrying it falls into
the existing unknown-key **warn** path -- loud, harmless, and
self-explaining. Nothing in the tree or in any known manifest reads it.

## 3. `[assets]`: resolved only through config

The `[assets]` table (reserved since manifest schema v1) becomes real.

### 3.1 Declaration and validation

```toml
[assets]
chime = "assets/chime.wav"
```

Keys are asset names; values are **package-relative** paths. Validated
at discovery (still no Bit code executed; these are `stat()` calls):

- an absolute path, or any path whose resolution escapes the package
  directory (`..`, symlink out), is a located `PackageError` -- a
  bundle must never address files outside its own tree;
- a declared file that does not exist is a located `PackageError` --
  a package missing its assets is broken *now*, at discovery, not
  mid-installation when the Bit first plays the file.

### 3.2 Resolution

`BitConfig.assets` stays a tuple of `(key, relpath)` pairs as parsed
(the frozen dataclass remains location-independent and serializable);
resolution happens where the package location is known. `BitPackage`
gains:

```python
def asset_path(self, key: str) -> Path   # absolute, inside pkg dir
```

and `BitConfig` gains the same accessor, populated at
`resolve_config()` time via a non-serialized resolution root
(`BitConfig.assets_root: Path | None`, default `None`; `asset_path()`
raises a located error when called with no root or an unknown key --
never returns a guess).

**The contract this section exists for:** Bit code obtains asset paths
ONLY via `config.asset_path(...)`. Never `__file__`-relative, never
CWD-relative. That is what makes a Bit behave identically in
mm-terrarium's `bits/`, in an external checkout named by `bit_paths`,
and unpacked from a bundle -- and it is the rule the reference external
Bit (section 4) demonstrates.

No shipped in-repo Bit declares an asset today and none is forced to;
the exemplar lives in the external package.

## 4. Where external Bits live (deliberate revision of section 12)

Section 12 called for a dedicated Bits repo, explicitly not
mm-tuneshroom "whose boundary excludes Terrarium-side logic". **That
direction is revised here, deliberately and with eyes open:** external
Bits live in the repo of the instrument they target, in a top-level
`bits/` tree, one directory per package:

```
mm-tuneshroom/
  bits/
    GlowBit/
      bit.toml
      glow_bit.py
      assets/...
```

- **The boundary rule changes shape rather than dying.** The old rule
  ("mm-tuneshroom never contains Terrarium-side logic") becomes:
  mm-tuneshroom's *application* (Dart app, web build, native harness)
  never contains Terrarium-side logic; its `bits/` tree is a distinct,
  co-located artifact set -- Terrarium-side Bit packages that ship WITH
  the instrument they are written for, consumed only through
  `terrarium.toml`'s `bit_paths` and never imported by the Dart app.
  Keeping a Bit next to the instrument it choreographs keeps the two
  halves of one experience (device behavior, game logic) in one
  reviewable place, which is the reason for the revision.
- **mm-terrarium keeps its own `bits/`.** The packages the Terrarium
  suite depends on -- `test` (the reference/regression fixture),
  `metronome`, `capture` -- stay in-repo, unmoved. The rule of thumb:
  a Bit the Terrarium repo's own tests or reference docs depend on
  lives in mm-terrarium; a gameplay Bit written for an instrument
  lives with that instrument.
- **Multi-instrument Bits:** live with their *primary* instrument (the
  one whose players the Bit exists for); the capability-contract
  system (Spec 2) already makes targeting declarative, so location
  carries no semantics -- it is organization only.
- **Wiring:** a `terrarium.toml` on a box with the checkout present
  adds `bit_paths = ["bits", "/Users/chris/projects/mm-tuneshroom/bits"]`
  (absolute, or relative to the config file's own directory --
  `resolve_bit_roots()` semantics, unchanged). The shipped
  mm-terrarium `terrarium.toml` keeps `["bits"]` only: a default
  checkout must not error on a neighbor repo that is not there
  (a missing root is already a collected `PackageError`, but the
  default config should be silent-clean).
- **Seeding:** this slice creates `mm-tuneshroom/bits/GlowBit` -- a
  minimal `ambient`-kind Bit (a declared GENERATOR Function drifting
  the room hue; no scored roles) with one small `[assets]` file and
  `requires_terrarium_api = 1`, plus `mm-tuneshroom/bits/README.md`
  stating the contract (API version, layout, the
  resolved-only-through-config asset rule, how to bundle). It is the
  living exemplar of every mechanism in this spec, discovered across a
  real repo boundary. Landed as its own small PR in mm-tuneshroom.
- **Cross-repo doc follow-ups** (flagged at closeout, not silently
  drifted): mm-terrarium's `docs/MM_TERRARIUM.md` "Relationships"
  entry for mm-tuneshroom, and mm-tuneshroom's own deep-dive, both
  currently state the old absolute boundary and need the revised
  wording above.

## 5. Bundling: `tools/bundle_bit.py` and the archive format

An external bundle is code the venue box executes; the tool exists so
that what arrives is verifiably what was built, and so "install a Bit"
is one command rather than folklore.

### 5.1 Format: `<name>-<version>.mmbit`

A plain zip (stdlib `zipfile`, deterministic member order) containing:

- the package directory's files at archive root (`bit.toml` at root --
  the same shape `BitRegistry.scan()` reads on disk);
- `BUNDLE.json`, generated at bundle time, never present in the source
  tree: per-file sha256 (path -> hash, every file except itself),
  `name`, `version`, `requires_terrarium_api` (copied from the
  manifest), `created` (ISO 8601 UTC), `bundler` (user@host), and
  `source_commit` (git HEAD of the package's repo when available, else
  absent).

Exclusions at bundle time: `__pycache__/`, `*.pyc`, `.git/`, dotfiles.

### 5.2 The tool

`tools/bundle_bit.py`, an offline CLI (peer of `tools/trace_stats.py`,
not part of the runtime), three subcommands:

- `bundle <pkg-dir> [-o out.mmbit]` -- validates the manifest parses
  and the asset rules of section 3 hold (refuses to bundle a package
  discovery would refuse), then writes the archive.
- `verify <archive>` -- re-hashes every member against `BUNDLE.json`;
  any mismatch, any member absent from the manifest, or any manifest
  entry absent from the archive is a refusal (exit nonzero, every
  discrepancy listed). Also re-checks `requires_terrarium_api` against
  this checkout's `TERRARIUM_API` and warns on mismatch (warn, not
  refuse: verify may legitimately run on a box other than the target).
- `install <archive> <root>` -- runs the full `verify` (refusal
  aborts), then unpacks into `<root>/<pkg-name>/`. An existing
  directory of that name is a refusal without `--force`; with
  `--force` the old directory is replaced atomically (unpack beside,
  swap, remove). Member paths are validated against zip-slip
  (no absolute members, no `..` escape) before any byte is written.
  `BUNDLE.json` is installed too -- it is the on-disk provenance
  record an operator can audit later.

`BitRegistry.scan()` ignores a `BUNDLE.json` sitting in a package
directory (it only reads `bit.toml`), so an installed bundle discovers
exactly like a checkout.

### 5.3 sys.path vs venv-install: answered, sys.path

The question section 12 left open is settled in favor of what
`BitRegistry.scan()` already does: an external root is inserted on
`sys.path` and the package imports as a plain package directory.

- A venv-install path (wheel per Bit, `pip install` into the runtime
  venv) buys dependency resolution -- and **Bits are not allowed
  third-party dependencies**: a Bit may import the stdlib and
  mm-terrarium's own modules, nothing else. That constraint is now
  explicit contract (recorded in the mm-tuneshroom `bits/` README and
  this spec) rather than accident; it is what keeps a bundle a
  self-contained folder of files and keeps `install` trivially
  reversible (delete the directory).
- Revisit trigger, recorded so nobody rediscovers it: the day a real
  Bit genuinely needs a third-party package, this decision reopens as
  its own spec -- the answer will involve venvs or vendoring, and it
  is deliberately NOT pre-designed here.

### 5.4 Integrity is not authenticity (honest limit)

sha256 proves the archive is intact, not who made it. There is no
signing this slice: signature infrastructure (keys, distribution,
rotation) is a project of its own and is explicitly deferred. Until
then the operational rule is the Console's own trust model extended:
**install bundles only from sources you trust** -- the provenance
stamp is for audit and debugging, not for authentication. A future
signing slice can add a detached signature over `BUNDLE.json` without
changing the format.

## 6. Engine changes, collected

- `control/api_version.py` -- new, `TERRARIUM_API = 1`.
- `control/bit_config.py` -- `requires_terrarium_api` required int in
  `[bit]`; `min_terrarium` removed; `[assets]` values validated as
  relative, non-escaping strings (existence is checked in the
  registry, where the package dir is known); `BitConfig.assets_root` +
  `asset_path()`.
- `control/bit_registry.py` -- discovery-time API gate; asset
  existence/escape check per package; `BitPackage.asset_path()`;
  `resolve_config()` threads `assets_root`.
- `bits/*/bit.toml` (all three) -- gain `requires_terrarium_api = 1`.
- `tools/bundle_bit.py` -- new.
- `terrarium.toml` -- unchanged (`bit_paths = ["bits"]` stays).
- mm-tuneshroom: `bits/GlowBit/` + `bits/README.md` -- new, own PR.
- No changes to `control/engine.py`, transports, Console, or wire
  protocols: discovery-time refusals ride the existing `PackageError`
  surfaces end to end.

## 7. Testing (offline, as always)

- API gate: fixture packages under `tests/` -- missing key, wrong
  type (`true`), wrong value, correct value; each refusal located and
  package-scoped (siblings still discover).
- `min_terrarium` in a manifest -> warn path, not error.
- Assets: declared+present resolves via `asset_path()`; missing file,
  absolute path, `..` escape each a located `PackageError`; unknown
  key / no root raises, never guesses.
- Bundle round-trip in tmp dirs: `bundle` -> `verify` green ->
  tamper one byte -> `verify` refuses -> `install` -> `scan()` over
  the install root discovers the package -> `load_bit` runs it.
  Zip-slip member refused at install. `--force` replace works;
  refusal without it.
- Existing suite stays green; the full-cycle pins are untouched
  (baseline 1634 passed, 1 skipped).

## 8. Live verification checklist (dev box, real Arco -- queued behind
the Spec 1/2/3 checklists, all still unrun)

- [ ] 1. `terrarium.toml` with `bit_paths = ["bits",
      "/Users/chris/projects/mm-tuneshroom/bits"]`; `--list-bits`
      shows GlowBit alongside the in-repo three, no errors.
- [ ] 2. Load TEST from the Console; `load_bit` GlowBit; ambient hue
      drift visibly running from the external package.
- [ ] 3. Edit GlowBit's `requires_terrarium_api` to 2; relaunch;
      `--list-bits` shows the located refusal; the other packages
      unaffected.
- [ ] 4. `bundle_bit.py bundle` GlowBit; `verify`; `install` into a
      scratch root; boot with `bit_paths` at the scratch root; GlowBit
      loads from the installed bundle.

## 9. Out of scope (named so nobody rediscovers them)

- Bundle signing / authenticity (section 5.4; deferred until key
  infrastructure exists).
- Third-party dependencies for Bits (section 5.3; revisit trigger
  recorded).
- A Bit marketplace/registry service, remote fetch, or auto-update --
  install is a local, operator-run command.
- Moving `metronome`/`capture`/`test` out of mm-terrarium (they stay;
  section 4).
- Any Console UI for installing bundles (the Console lists what
  discovery found; installing is a shell operation).

## Status

Spec written 2026-08-28. Not yet implemented.
