# mm-terrarium — Light-Manifest v2 Adoption & Per-Role Welcome Plumbing

**Status:** Approved in brainstorm, pending spec review
**Date:** 2026-07-22
**Scope:** Adopt the luxaeterna light-manifest v2 wire contract for
`Role.light_manifest`, add the per-role welcome pair (light + audio halves),
compose and surface the per-role config blob through the role-adoption flow,
and two targeted fixes the plumbing touches (`load_bit` exception wedge,
Console loaded-bit name scan).

---

## 1. Motivation

Luxaeterna has ratified light-manifest v2 (luxaeterna repo,
`docs/superpowers/specs/2026-07-22-synth-session-lifecycle-design.md` §9, on
branch `claude/luxaeterna-latency-memory-leaks-b367c5`): `LightManifest` gains
`bit_name`, `bit_version`, `role`, and `welcome` — a `SignatureDecl`
(`instrument`, `params`, `duration=1.5`) played at role adoption in place of
the generic green load flash. The placeholder comment on
`control/roles.py::Role.light_manifest` promised the schema would freeze once
a real Bit declares light lanes; that promise is now due.

The v2 spec's §9 also settles the split of responsibilities: **Control stamps
provenance** (`bit_name`, `bit_version`, `role`) at adoption time and ships a
per-role config blob in `/ie<N>/role "sssib" bit role class channel config`
(per `docs/control-gameserver-design.md`); the Bit author declares only
instruments and the welcome. And the audio half of a welcome lives on the
Arco side, triggered off the same role-adoption message — it never ships to
the device.

## 2. Decisions (settled in brainstorm)

1. **Dict in wire shape.** `Role.light_manifest` is a plain dict shaped
   exactly as luxaeterna's `LightManifest.from_dict` parses. No mirrored
   dataclasses (drift risk across a single cross-repo contract), no package
   dependency on luxaeterna (couples Control to the device library's release
   cadence; v2 isn't even merged there yet).
2. **`Role.welcome` pair.** One declaration block holds both halves of the
   welcome ceremony — light and audio — in symmetric `SignatureDecl` shape.
   Bit authors declare both in one place; Control routes each half to its
   consumer.
3. **Blob on `JoinResult`.** The composed config blob rides the `join()`
   return value. The future o2lite transport is the caller of `join()`, so
   the return value is exactly where it will pick up the blob to build
   `/ie<N>/role`. No speculative `on_role` hook (`on_release` exists only
   because releases originate inside the engine).
4. **Shallow validation at load, in its own module.** Structural checks
   (required keys, types, forbidden keys) at `load_bit` time so a typo'd Bit
   fails as `BitLoadError` instead of a device-side parse error
   mid-installation. No instrument-name or param validation — that vocabulary
   lives in luxaeterna's installation-overridable registry, which Control
   cannot see.

## 3. Role schema (`control/roles.py`)

```python
@dataclass
class Role:
    name: str
    role_class: RoleClass
    capacity: int | None
    scored: bool
    ugen_manifest: list = field(default_factory=list)   # unchanged placeholder
    # Authored subset of the light-manifest v2 wire shape (see §1).
    # {"instruments": [{instrument, target, params?, lanes?: [{source, dest, curve?}]}]}
    # welcome/bit_name/bit_version/role are FORBIDDEN here: Control composes
    # them into the outgoing blob at adoption time (§5).
    light_manifest: dict = field(default_factory=dict)
    # The role's welcome ceremony, both halves in one place:
    # {"light": {instrument, params?, duration?}, "audio": {instrument, params?, duration?}}
    # light folds into the outgoing light_manifest blob; audio stays
    # Control-side for the future Arco cue path.
    welcome: dict | None = None
```

- An empty `light_manifest` (`{}`) parses device-side as an empty manifest —
  today's "declares no light" default is preserved.
- A present `welcome` must have at least one half; a present half must name
  an `instrument`. `duration` defaults are the consumer's business
  (1.5 s for light, per `SignatureDecl`).
- The audio half has no consumer yet. Declaring its shape now freezes the
  authoring contract so Bit authors write both halves together from day one;
  the Arco cue path picks it up when gameserver verbs land.

## 4. Bit identity (`control/bit.py`, `control/engine.py`)

- `Bit` gains `version: str = ""` (class attribute, overridable).
- The bit *name* is not a new attribute. `GameServer.load_bit(name)` already
  receives the registry name; it stores it as `self.bit_name: str | None`,
  cleared in `_unload`. Single source of truth, nothing for a Bit author to
  keep in sync.
- Targeted cleanup: `ConsoleAgent._loaded_bit_name()` currently
  isinstance-scans the registry to recover the name; it collapses to reading
  `game_server.bit_name`.

## 5. New module: `control/role_config.py`

Two pure functions, no engine imports (mirroring the repo's protocol-module
discipline):

- `validate_role_declarations(role_table)` — walks every role, shallow
  structural validation per §3. Raises `ValueError` with located messages in
  luxaeterna's style:
  `"role 'player' light_manifest instruments[0]: missing required field 'instrument'"`.
  Rejects `welcome`/`bit_name`/`bit_version`/`role` keys inside an authored
  `light_manifest` (those are Control's to stamp).
- `compose_role_config(bit_name, bit_version, role)` — the per-role config
  blob for `/ie<N>/role`:

  ```python
  {
      "role": role.name,
      "class": role.role_class.name,
      "scored": role.scored,
      "light_manifest": {
          **deepcopy(role.light_manifest),
          "bit_name": bit_name,
          "bit_version": bit_version,
          "role": role.name,
          # only when role.welcome has a light half:
          "welcome": deepcopy(role.welcome["light"]),
      },
  }
  ```

  Deep-copied so transport/Console consumers can never alias a Bit's
  declaration. The audio half is deliberately absent — it never ships to the
  device; the future cue path reads it off `role.welcome`.

## 6. Engine plumbing (`control/engine.py`, `control/registration.py`)

- `JoinResult` gains `config: dict | None = None`. Denied results keep
  `None`.
- `GameServer.join`: on a granted result, compose
  `compose_role_config(self.bit_name, self.bit.version, role)` where `role`
  comes from the **registration's role-table snapshot**
  (`self.registration.role_table.roles[result.role]`), not a fresh
  `bit.role_table` call — Bits build tables per property access.
- `load_bit` fix: today `bit.role_table` is accessed *outside* the guarded
  try, so a raising `role_table` property escapes the handler and wedges the
  engine in LOADING with `self.bit` set. The role-table access,
  `RegistrationState` construction, and `validate_role_declarations` all move
  inside the guarded region; any failure becomes `BitLoadError` with state
  restored to IDLE.

## 7. Console & TestBit

- `console/protocol.py::role_view` passes the dict-shaped `light_manifest`
  through and adds `"welcome": role.welcome`.
- `bits/test_bit.py`: the `player` role gains a real v2 manifest (one
  instrument with a note lane) and a welcome pair (light + audio); `jammer`
  keeps empty defaults. The regression fixture then exercises both the
  composition path and the empty-default path — and a real Bit now declares
  light lanes, formally freezing the schema per the placeholder's promise.

## 8. Testing strategy

1. **`tests/test_role_config.py` (new).** Validation: happy path; each
   missing required key (instrument/target/source/dest); forbidden keys in
   authored `light_manifest`; welcome with no halves; half without
   `instrument`; non-dict manifest — every failure message carries its
   location. Composition: provenance stamped; welcome light half folded;
   light-only and audio-only welcomes; no welcome; authored dicts never
   aliased or mutated.
2. **Engine/registration.** Granted `JoinResult.config` carries the composed
   blob (exact-dict assertion); denies carry `None`; a role switch composes
   the new role's blob; scored/class/capacity behavior unchanged.
3. **`load_bit` wedge regression.** A Bit whose `role_table` raises, and one
   whose manifest fails validation: both → `BitLoadError`, state IDLE,
   `bit`/`registration`/`bit_name` all cleared.
4. **Console.** `role_view` renders the v2 dict + welcome;
   `_loaded_bit_name` equivalence via `game_server.bit_name`; snapshot shape
   updated.
5. **Updated defaults.** `test_roles.py`: `light_manifest == {}`,
   `welcome is None`, `ugen_manifest` untouched.

## 9. Docs & cross-repo coordination

- `docs/control-gameserver-design.md`: one-line touch to the config-blob
  sentence naming light-manifest v2 + welcome (canonical doc; light touch
  only).
- **Nothing changes in luxaeterna in this task.** Flags for that repo's
  follow-up:
  1. The v2 branch's `from_dict` predates main's `_require` validation
     helper — the merge needs reconciling.
  2. `sys:role-adopted`, `sys:role-denied`, `sys:goodbye` stay reserved
     names awaiting gameserver verbs; this design's welcome blob is the
     `sys:role-adopted` predecessor, played as LOADING's welcome rather than
     a distinct verb.
- `docs/MM_TERRARIUM.md` deep-dive syncs at closeout per house process.

## 10. Sources

- Light-manifest v2 — luxaeterna
  `docs/superpowers/specs/2026-07-22-synth-session-lifecycle-design.md` §9
  (commit `a559210`, branch `claude/luxaeterna-latency-memory-leaks-b367c5`).
- Role-adoption flow — `docs/control-gameserver-design.md` (player flow
  step 3, `/ie<N>/role` config blob).
- Placeholder contract — `control/roles.py` comments (this repo, `main`).
