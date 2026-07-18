# mm-terrarium Bootstrap Design

**Date:** 2026-07-18
**Status:** Approved (compressed brainstorm, autonomous session; decisions
below were made with explicit recommendations and stand unless Chris revises)
**Canonical architecture:** `mm-documents/mm-shrooms-app/control-gameserver-design.md`

## 1. Purpose

Stand up mm-terrarium as its own repo, separate from mm-tuneshroom, as the
home of the Terrarium Server: Arco server hosting plus the Control+GameServer
and its Bits. This spec covers the repo bootstrap only; the first
implementation slice gets its own spec and plan.

## 2. Decisions

1. **Separate repo, created now, before any Control code exists.** The new
   architecture makes Bits Control-side Python modules, shrinking the
   cross-repo surface to the stable `/game` lifecycle vocabulary, so the
   M1a-era reason to co-locate app and harness is gone. Matches the fleet's
   one-service-one-repo convention.
2. **Name `mm-terrarium`, private, default branch `main`.** Matches fleet
   slugs and mm-tuneshroom's visibility.
3. **Docs-first scaffold.** README (identity, canonical-design pointer,
   planned layout), mm-meta.yml (schema 2, tier app, status pending),
   .gitignore, this spec. No code, no empty directories: the layout in the
   README is a plan, not a promise, and lands with the first implementation
   spec.
4. **Legacy stays put.** The M1a and Sensor Check harness (Python + o2host +
   web pages) remains in mm-tuneshroom as a working reference until the new
   stack reproduces its behavior. Nothing is ported.
5. **Roger Dannenberg (rbdannenberg) invited as collaborator with write
   access**, so the Arco-server-extension work and the Control design can be
   discussed and iterated where the code will live.

## 3. Open questions (for the first implementation spec)

1. Arco and o2 as git submodules versus sibling checkouts with pinned SHAs
   (current arco/o2 build coupling suggests pinned siblings; decide with
   Roger).
2. Packaging for `control/`: plain requirements.txt (as mm-tuneshroom's
   harness does) versus pyproject; follow pyarco's convention when code lands.
3. How the mm-tuneshroom simulator web build ships into `www/` (artifact copy
   at deploy time versus CI artifact pull).
4. Whether the o2host successor role (reference clock, ws bridge, static web)
   is fully absorbed by the extended Arco server or needs a thin wrapper here.

## 4. Out of scope

- Any Control/Bit implementation, CI, deployment tooling.
- services/MM_TERRARIUM.md deep-dive in mm-documents (create when the repo
  has behavior worth documenting; mm-meta.yml carries identity until then).
- Migration of legacy harness code.
