"""Shared fallback for locating the arco checkout's o2litepy package.

harness/run_stack.py and harness/o2_shroom.py both need o2litepy importable
-- run_stack for the processes it spawns, o2_shroom when it's run by hand
outside run_stack's control. Both fall back to the same hardcoded arco
checkout when no PYTHONPATH was set, rather than requiring every caller to
remember to export one.
"""

from __future__ import annotations

import os
import sys


def _default_arco_pythonpath() -> str:
    """MM_ARCO_PATH wins if set; otherwise assume arco is a sibling checkout
    of this repo (both under the same projects/ directory), which holds on
    every machine we've onboarded regardless of OS or username."""
    override = os.environ.get("MM_ARCO_PATH")
    if override:
        return override
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(os.path.dirname(repo_root), "arco")


ARCO_PYTHONPATH = _default_arco_pythonpath()


def _import_o2litepy() -> None:
    from o2litepy import o2lite      # noqa: F401, PLC0415 (import is the check)


def ensure_o2litepy(*, importer=_import_o2litepy, syspath=sys.path,
                    environ=os.environ) -> bool:
    """True once o2litepy is importable, falling back to the hardcoded
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
