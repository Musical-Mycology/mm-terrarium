def test_arco_path_fallback_is_a_noop_when_o2litepy_imports():
    from harness.arco_paths import ensure_o2litepy

    syspath, env = [], {}
    ok = ensure_o2litepy(importer=lambda: None, syspath=syspath, environ=env)

    assert ok is True
    assert syspath == []
    assert env == {}


def test_arco_path_fallback_appends_the_arco_checkout_and_retries():
    """The single-command goal: a bare `run_stack --open` with no
    PYTHONPATH set falls back to the same hardcoded arco checkout
    DEFAULT_ARCO_COMMAND already lives in, for this process (sys.path)
    AND for the children it spawns (PYTHONPATH)."""
    from harness.arco_paths import ARCO_PYTHONPATH, ensure_o2litepy

    calls = []

    def importer():
        calls.append(True)
        if len(calls) == 1:
            raise ImportError("no o2litepy")

    syspath, env = [], {}
    ok = ensure_o2litepy(importer=importer, syspath=syspath, environ=env)

    assert ok is True
    assert ARCO_PYTHONPATH in syspath
    assert env["PYTHONPATH"] == ARCO_PYTHONPATH


def test_arco_path_fallback_preserves_an_existing_pythonpath():
    from harness.arco_paths import ARCO_PYTHONPATH, ensure_o2litepy

    calls = []

    def importer():
        calls.append(True)
        if len(calls) == 1:
            raise ImportError("no o2litepy")

    env = {"PYTHONPATH": "/somewhere/else"}
    ok = ensure_o2litepy(importer=importer, syspath=[], environ=env)

    assert ok is True
    assert env["PYTHONPATH"] == f"/somewhere/else:{ARCO_PYTHONPATH}"


def test_arco_path_fallback_reports_failure_when_the_checkout_lacks_it():
    from harness.arco_paths import ensure_o2litepy

    def importer():
        raise ImportError("no o2litepy anywhere")

    ok = ensure_o2litepy(importer=importer, syspath=[], environ={})

    assert ok is False


# --- sibling-checkout resolution inside a git worktree ------------------
#
# A git worktree under .claude/worktrees/<name> has a repo root whose parent
# is NOT the projects/ directory, so "sibling of the repo root" resolves to
# .../worktrees/arco, which does not exist. The fix reads the worktree's
# `.git` FILE (gitdir: <main>/.git/worktrees/<name>) and resolves siblings
# from the main checkout's parent instead.

def _make_worktree(tmp_path, *, relative_gitdir=False):
    projects = tmp_path / "projects"
    main = projects / "mm-terrarium"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    worktree = main / ".claude" / "worktrees" / "wt"
    worktree.mkdir(parents=True)
    gitdir = main / ".git" / "worktrees" / "wt"
    if relative_gitdir:
        gitdir = "../../../.git/worktrees/wt"
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n")
    (projects / "arco").mkdir()
    (projects / "fluidsynth" / "sf2").mkdir(parents=True)
    return projects, main, worktree


def test_main_checkout_root_is_the_repo_root_for_a_plain_checkout(tmp_path):
    from harness.arco_paths import main_checkout_root

    main = tmp_path / "projects" / "mm-terrarium"
    (main / ".git").mkdir(parents=True)

    assert main_checkout_root(str(main)) == str(main)


def test_main_checkout_root_resolves_a_worktree_to_its_main_checkout(tmp_path):
    from harness.arco_paths import main_checkout_root

    _, main, worktree = _make_worktree(tmp_path)

    assert main_checkout_root(str(worktree)) == str(main)


def test_main_checkout_root_handles_a_relative_gitdir(tmp_path):
    from harness.arco_paths import main_checkout_root

    _, main, worktree = _make_worktree(tmp_path, relative_gitdir=True)

    assert main_checkout_root(str(worktree)) == str(main)


def test_main_checkout_root_falls_back_on_an_unparseable_dotgit_file(tmp_path):
    from harness.arco_paths import main_checkout_root

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").write_text("nonsense\n")

    assert main_checkout_root(str(root)) == str(root)


def test_sibling_path_from_a_worktree_lands_next_to_the_main_checkout(tmp_path):
    from harness.arco_paths import sibling_path

    projects, _, worktree = _make_worktree(tmp_path)

    assert sibling_path("arco", repo_root=str(worktree)) == str(projects / "arco")


def test_default_arco_pythonpath_from_a_worktree_is_the_real_sibling(tmp_path):
    from harness.arco_paths import _default_arco_pythonpath

    projects, _, worktree = _make_worktree(tmp_path)

    path = _default_arco_pythonpath(repo_root=str(worktree), environ={})

    assert path == str(projects / "arco")


def test_default_arco_pythonpath_env_override_still_wins_in_a_worktree(tmp_path):
    from harness.arco_paths import _default_arco_pythonpath

    _, _, worktree = _make_worktree(tmp_path)

    path = _default_arco_pythonpath(repo_root=str(worktree),
                                    environ={"MM_ARCO_PATH": "/elsewhere/arco"})

    assert path == "/elsewhere/arco"


def test_default_soundfont_from_a_worktree_is_the_real_sibling(tmp_path):
    from harness.arco_synth import _default_soundfont

    projects, _, worktree = _make_worktree(tmp_path)
    sf2 = projects / "fluidsynth" / "sf2" / "FluidR3_GM.sf2"
    sf2.write_bytes(b"")

    assert _default_soundfont(repo_root=str(worktree), environ={}) == str(sf2)


def test_default_soundfont_env_override_still_wins_in_a_worktree(tmp_path):
    from harness.arco_synth import _default_soundfont

    _, _, worktree = _make_worktree(tmp_path)

    path = _default_soundfont(repo_root=str(worktree),
                              environ={"MM_SOUNDFONT": "/elsewhere/gm.sf2"})

    assert path == "/elsewhere/gm.sf2"
