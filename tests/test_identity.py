from hyeseongkit.core.identity import name_of, normalize_remote, project_id_of


def test_https_with_git_suffix():
    assert normalize_remote("https://github.com/Owner/Repo.git") == "github.com/owner/repo"


def test_scp_form():
    assert normalize_remote("git@github.com:Owner/Repo.git") == "github.com/owner/repo"


def test_ssh_nonstandard_port_kept():
    # 비표준 포트는 host_port 형태로 유지 (§7-1)
    assert normalize_remote("ssh://git@host.example:2222/o/r.git") == "host.example_2222/o/r"


def test_default_port_removed():
    assert normalize_remote("https://host.example:443/o/r") == "host.example/o/r"


def test_credentials_stripped():
    assert normalize_remote("https://user:pass@host.example/o/r") == "host.example/o/r"


def test_local_paths_rejected():
    # 절대경로는 어떤 경우에도 식별자에 넣지 않는다 (D19)
    assert normalize_remote("C:\\repos\\proj") is None
    assert normalize_remote("/srv/git/proj") is None
    assert normalize_remote("../relative/path") is None


def test_project_id_deterministic():
    a = project_id_of("github.com/owner/repo")
    b = project_id_of("github.com/owner/repo")
    assert a == b
    assert a.startswith("p-")
    assert len(a) == 14  # "p-" + sha256[:12] (§7-1)


def test_name_of():
    assert name_of("github.com/owner/repo") == "repo"
    assert name_of("named:my-project") == "named:my-project".rsplit("/", 1)[-1]
