"""Shared fallback for locating the arco checkout's o2litepy package.

harness/run_stack.py and harness/o2_shroom.py both need o2litepy importable
-- run_stack for the processes it spawns, o2_shroom when it's run by hand
outside run_stack's control. Both fall back to the same sibling arco
checkout when no PYTHONPATH was set, rather than requiring every caller to
remember to export one.

"Sibling" means next to the MAIN checkout of this repo, which is not the
same thing as next to the repo root once this repo is being run from a git
worktree (Claude Code puts them under .claude/worktrees/<name>). See
main_checkout_root() below; harness/arco_synth.py resolves its soundfont
through the same helper so both defaults agree.
"""

from __future__ import annotations

import os
import sys


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main_checkout_root(repo_root: str) -> str:
    """The main checkout's root for `repo_root`, which is `repo_root`
    itself unless it is a linked git worktree.

    A linked worktree carries a `.git` FILE (not directory) reading
    `gitdir: <main>/.git/worktrees/<name>`. Resolving that pointer back to
    the directory that owns the `.git` directory gives the main checkout,
    which is what "sibling checkout" was always meant relative to. Any
    shape this does not recognise falls back to `repo_root` unchanged, so
    a plain checkout behaves exactly as before. Pure filesystem reads, no
    git subprocess: this runs at import time in every harness process.
    """
    dotgit = os.path.join(repo_root, ".git")
    if not os.path.isfile(dotgit):
        return repo_root
    try:
        with open(dotgit, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return repo_root
    gitdir = None
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            gitdir = line[len("gitdir:"):].strip()
            break
    if not gitdir:
        return repo_root
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(repo_root, gitdir)
    gitdir = os.path.normpath(gitdir)
    parts = gitdir.split(os.sep)
    try:
        index = len(parts) - 1 - parts[::-1].index(".git")
    except ValueError:
        return repo_root
    main = os.sep.join(parts[:index]) or os.sep
    return main


def sibling_path(name: str, *, repo_root: str | None = None) -> str:
    """`name` as a sibling of this repo's MAIN checkout (both under the
    same projects/ directory), which holds on every machine we've
    onboarded regardless of OS or username, and now also from inside a
    git worktree of this repo."""
    root = _repo_root() if repo_root is None else repo_root
    return os.path.join(os.path.dirname(main_checkout_root(root)), name)


def _default_arco_pythonpath(*, repo_root: str | None = None,
                             environ=os.environ) -> str:
    """MM_ARCO_PATH wins if set; otherwise assume arco is a sibling checkout
    of this repo (see sibling_path)."""
    override = environ.get("MM_ARCO_PATH")
    if override:
        return override
    return sibling_path("arco", repo_root=repo_root)


ARCO_PYTHONPATH = _default_arco_pythonpath()


def _import_o2litepy() -> None:
    from o2litepy import o2lite      # noqa: F401, PLC0415 (import is the check)


def ensure_o2litepy(*, importer=_import_o2litepy, syspath=sys.path,
                    environ=os.environ) -> bool:
    """True once o2litepy is importable, falling back to the sibling
    arco checkout when no PYTHONPATH was set.

    The fallback covers both halves of the stack: sys.path for this
    process, and PYTHONPATH for every child it spawns (terrarium_boot and
    the devices all need o2litepy too, and they inherit the environment).
    An explicit PYTHONPATH still wins -- the fallback only runs when the
    import already failed, and it appends rather than replaces.
    """
    try:
        importer()
        return True
    except ImportError:
        pass
    syspath.append(ARCO_PYTHONPATH)
    existing = environ.get("PYTHONPATH")
    environ["PYTHONPATH"] = (f"{existing}:{ARCO_PYTHONPATH}" if existing
                             else ARCO_PYTHONPATH)
    try:
        importer()
        return True
    except ImportError:
        return False
