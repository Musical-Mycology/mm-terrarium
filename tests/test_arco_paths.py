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
