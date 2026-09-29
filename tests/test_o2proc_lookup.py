"""harness/o2proc_lookup.py: Arco's O2 process name for GET /o2proc
(spec docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md)."""
from harness.o2proc_lookup import O2Record, ProcName, parse_proc_name, select_local

REAL = "@00000000:ac17f983:afb9:9f48"  # measured from a live Arco, 2026-09-29


def test_parse_real_arco_name():
    assert parse_proc_name(REAL) == ProcName("172.23.249.131", 44985, 40776)


def test_parse_rejects_malformed():
    for bad in ["", REAL[:-1], REAL + "0", "#" + REAL[1:],
                REAL.replace(":", ";", 1), "@0000000g:ac17f983:afb9:9f48",
                "<html>nope</html>", None]:
        assert parse_proc_name(bad) is None, bad


def _rec(name=REAL, instance="arco", port=44985):
    return O2Record(instance, port, name)


def test_select_one_local_match():
    assert select_local([_rec()], "arco", {"172.23.249.131"}) == (REAL, "")


def test_select_ignores_other_ensemble():
    name, reason = select_local([_rec(instance="other")], "arco",
                                {"172.23.249.131"})
    assert name is None and "no arco" in reason


def test_select_ignores_remote_arco():
    remote = "@00000000:c0a80105:afb9:9f48"  # 192.168.1.5, not this host
    name, reason = select_local([_rec(remote)], "arco", {"172.23.249.131"})
    assert name is None and "no arco" in reason


def test_select_ignores_port_mismatch_and_bad_txt():
    recs = [_rec(port=1234), _rec(name=None), _rec(name="garbage")]
    name, _ = select_local(recs, "arco", {"172.23.249.131"})
    assert name is None


def test_select_refuses_two_local_matches():
    other = "@00000000:ac17f983:1f90:1f91"
    name, reason = select_local([_rec(), _rec(other, port=0x1F90)], "arco",
                                {"172.23.249.131"})
    assert name is None and "2 local" in reason
