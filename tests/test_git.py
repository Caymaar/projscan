"""Tests pour projscan.git — vrais dépôts git locaux dans tmp_path.

Aucune dépendance réseau : l'"upstream" est un autre repo local (file://),
ce qui permet de tester behind/ahead/diverged de façon déterministe.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from projscan.git import RepoInfo, find_repos, scan_paths, scan_repo


# --- helpers de mise en place ------------------------------------------------

def git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {res.stderr.strip()}")
    return res.stdout.strip()


def init_repo(path: Path, *, bare: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", *(["--bare"] if bare else []), "-b", "main")
    if not bare:
        git(path, "config", "user.email", "t@t.t")
        git(path, "config", "user.name", "test")
    return path


def commit(repo: Path, msg: str, *, fname: str = "f.txt", content: str | None = None) -> None:
    (repo / fname).write_text(content if content is not None else msg, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def write_pyproject(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "{version}"\n', encoding="utf-8"
    )


# --- tests -------------------------------------------------------------------

async def test_clean_up_to_date(tmp_path: Path) -> None:
    remote = init_repo(tmp_path / "remote.git", bare=True)
    work = init_repo(tmp_path / "seed")
    commit(work, "init")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(remote), str(clone))

    info = await scan_repo(clone, fetch=True)
    assert info.status == "up to date", f"got {info.status!r}"
    assert info.behind == 0, f"got {info.behind}"
    assert info.ahead == 0, f"got {info.ahead}"
    assert info.upstream == "origin/main", f"got {info.upstream!r}"
    assert bool(info.commit)


async def test_behind(tmp_path: Path) -> None:
    remote = init_repo(tmp_path / "r2.git", bare=True)
    seed = init_repo(tmp_path / "seed2")
    commit(seed, "c1")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")

    clone = tmp_path / "clone2"
    git(tmp_path, "clone", "-q", str(remote), str(clone))

    # le seed pousse 2 nouveaux commits -> le clone est 2 en retard
    commit(seed, "c2")
    commit(seed, "c3")
    git(seed, "push", "-q", "origin", "main")

    info = await scan_repo(clone, fetch=True)
    assert info.behind == 2, f"got {info.behind}"
    assert info.ahead == 0, f"got {info.ahead}"
    assert "behind" in info.status, f"got {info.status!r}"


async def test_ahead(tmp_path: Path) -> None:
    remote = init_repo(tmp_path / "r3.git", bare=True)
    seed = init_repo(tmp_path / "seed3")
    commit(seed, "c1")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")

    clone = tmp_path / "clone3"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    git(clone, "config", "user.email", "t@t.t")
    git(clone, "config", "user.name", "test")
    commit(clone, "local1")  # avance de 1, pas poussé

    info = await scan_repo(clone, fetch=True)
    assert info.ahead == 1, f"got {info.ahead}"
    assert info.behind == 0, f"got {info.behind}"
    assert "ahead" in info.status, f"got {info.status!r}"


async def test_diverged(tmp_path: Path) -> None:
    remote = init_repo(tmp_path / "r4.git", bare=True)
    seed = init_repo(tmp_path / "seed4")
    commit(seed, "c1")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")

    clone = tmp_path / "clone4"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    git(clone, "config", "user.email", "t@t.t")
    git(clone, "config", "user.name", "test")
    commit(clone, "local-only")   # +1 local

    commit(seed, "remote-only")
    git(seed, "push", "-q", "origin", "main")  # +1 distant

    info = await scan_repo(clone, fetch=True)
    assert info.behind == 1, f"got {info.behind}"
    assert info.ahead == 1, f"got {info.ahead}"
    assert "diverged" in info.status, f"got {info.status!r}"


async def test_no_upstream(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "local-only")
    commit(repo, "solo")

    info = await scan_repo(repo, fetch=True)
    assert info.upstream is None, f"got {info.upstream!r}"
    assert info.behind is None, f"got {info.behind}"
    assert info.status == "no upstream", f"got {info.status!r}"
    assert bool(info.commit)


async def test_version_from_tag(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "tagged")
    commit(repo, "c1")
    git(repo, "tag", "v1.4.2")

    info = await scan_repo(repo, fetch=False)
    assert info.version == "v1.4.2", f"got {info.version!r}"


async def test_version_from_pyproject(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "pyproj")
    write_pyproject(repo, "0.9.0")
    commit(repo, "c1")  # aucun tag

    info = await scan_repo(repo, fetch=False)
    assert info.version == "0.9.0", f"got {info.version!r}"


async def test_find_repos_recursive(tmp_path: Path) -> None:
    base = tmp_path / "projets"
    a = init_repo(base / "a"); commit(a, "x")
    b = init_repo(base / "sous" / "b"); commit(b, "x")
    # repo imbriqué DANS a : ne doit pas être listé séparément
    inner = base / "a" / "vendored"; init_repo(inner); commit(inner, "x")
    # node_modules contenant un .git : doit être ignoré
    nm = base / "c"; init_repo(nm); commit(nm, "x")
    deep = nm / "node_modules" / "pkg"; init_repo(deep); commit(deep, "x")

    found = find_repos([base], max_depth=5)
    names = {p.name for p in found}
    assert "a" in names
    assert "b" in names
    assert "c" in names
    assert "vendored" not in names, f"ne doit pas entrer dans un repo ; got {names}"
    assert "pkg" not in names, f"node_modules doit être ignoré ; got {names}"


async def test_scan_paths_parallel(tmp_path: Path) -> None:
    base = tmp_path / "multi"
    for i in range(5):
        r = init_repo(base / f"proj{i}")
        commit(r, "c1")
    infos = await scan_paths([base], fetch=False)
    assert len(infos) == 5, f"got {len(infos)}"
    errors = [i.error for i in infos if i.error]
    assert not errors, str(errors)


async def test_as_dict_keys(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "dictrepo")
    commit(repo, "c1")
    info = await scan_repo(repo, fetch=False)
    d = info.as_dict()
    for key in ("name", "path", "version", "commit", "subject", "when",
                "branch", "upstream", "behind", "ahead", "status", "error"):
        assert key in d, f"clé manquante : {key}"


@pytest.mark.parametrize("cls", [RepoInfo])
def test_repo_info_is_dataclass(cls: type) -> None:
    import dataclasses
    assert dataclasses.is_dataclass(cls)
