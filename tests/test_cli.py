"""Tests pour projscan.cli — via click.testing.CliRunner.

On crée de vrais repos git locaux dans tmp_path pour tester les codes de sortie
et le format de sortie sans dépendance réseau.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from projscan.cli import _cli


# --- helpers git -------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {res.stderr.strip()}")
    return res.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@t.t")
    git(path, "config", "user.name", "test")
    return path


def commit(repo: Path, msg: str = "init") -> None:
    (repo / "f.txt").write_text(msg, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def make_up_to_date_repo(base: Path) -> Path:
    remote = base / "remote.git"
    remote.mkdir(parents=True)
    git(remote, "init", "-q", "--bare", "-b", "main")

    work = base / "work"
    init_repo(work)
    commit(work, "init")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    return work


def make_behind_repo(base: Path) -> Path:
    remote = base / "remote.git"
    remote.mkdir(parents=True)
    git(remote, "init", "-q", "--bare", "-b", "main")

    seed = base / "seed"
    init_repo(seed)
    commit(seed, "c1")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")

    clone = base / "clone"
    git(base, "clone", "-q", str(remote), str(clone))

    # seed pousse un nouveau commit -> clone est en retard
    commit(seed, "c2")
    git(seed, "push", "-q", "origin", "main")
    return clone


# --- repos -------------------------------------------------------------------

runner = CliRunner()


def test_repos_help() -> None:
    result = runner.invoke(_cli, ["repos", "--help"])
    assert result.exit_code == 0
    assert "PATHS" in result.output or "paths" in result.output.lower()
    assert "--no-fetch" in result.output
    assert "--json" in result.output


def test_repos_json_valid(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "r")
    commit(repo)
    result = runner.invoke(_cli, ["repos", "--no-fetch", "--json", str(tmp_path)])
    assert result.exit_code in (0, 1), f"exit={result.exit_code}\n{result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    for key in ("name", "path", "status", "commit", "branch"):
        assert key in data[0], f"clé JSON manquante : {key}"


def test_repos_table_columns_present(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "r")
    commit(repo)
    result = runner.invoke(_cli, ["repos", "--no-fetch", str(tmp_path)])
    assert result.exit_code in (0, 1)
    for col in ("Name", "Status", "Branch"):
        assert col in result.output, f"colonne manquante dans la sortie : {col}"


def test_repos_exit_0_when_up_to_date(tmp_path: Path) -> None:
    work = make_up_to_date_repo(tmp_path)
    result = runner.invoke(_cli, ["repos", "--no-fetch", "--json", str(tmp_path)])
    data = json.loads(result.output)
    assert data[0]["status"] == "up to date", f"got {data[0]['status']!r}"
    assert result.exit_code == 0


def test_repos_exit_1_when_behind(tmp_path: Path) -> None:
    make_behind_repo(tmp_path)
    result = runner.invoke(_cli, ["repos", "--json", str(tmp_path)])
    data = json.loads(result.output)
    clone_data = next(d for d in data if d["name"] == "clone")
    assert "behind" in clone_data["status"], f"got {clone_data['status']!r}"
    assert result.exit_code == 1


def test_repos_empty_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(_cli, ["repos", "--no-fetch", str(empty)])
    assert result.exit_code == 0


def test_repos_json_multiple(tmp_path: Path) -> None:
    for i in range(3):
        r = init_repo(tmp_path / f"r{i}")
        commit(r)
    result = runner.invoke(_cli, ["repos", "--no-fetch", "--json", str(tmp_path)])
    data = json.loads(result.output)
    assert len(data) == 3


# --- tools -------------------------------------------------------------------

def test_tools_help() -> None:
    result = runner.invoke(_cli, ["tools", "--help"])
    assert result.exit_code == 0
    assert "--no-remote" in result.output
    assert "--json" in result.output


def test_tools_no_remote_json(tmp_path: Path) -> None:
    """Un faux tool directory vide -> liste vide, exit 0."""
    result = runner.invoke(_cli, ["tools", "--no-remote", "--json", "--tool-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == []


def test_tools_json_keys(tmp_path: Path) -> None:
    """Un tool sans source git -> status 'pas git', clés JSON présentes."""
    tool_dir = tmp_path / "tools"
    tp = tool_dir / "mytool"
    (tp / "lib" / "python3.12" / "site-packages" / "mytool-1.0.dist-info").mkdir(parents=True)

    result = runner.invoke(_cli, ["tools", "--no-remote", "--json", "--tool-dir", str(tool_dir)])
    assert result.exit_code in (0, 1)
    data = json.loads(result.output)
    assert len(data) == 1
    for key in ("name", "version", "url", "status", "error"):
        assert key in data[0], f"clé JSON manquante : {key}"
    assert data[0]["status"] == "not git"


# --- git not installed -------------------------------------------------------

def test_repos_no_git_exits_2(tmp_path: Path) -> None:
    with patch("shutil.which", return_value=None):
        result = runner.invoke(_cli, ["repos", str(tmp_path)])
    assert result.exit_code == 2
    assert "git" in result.output.lower()


def test_tools_no_git_with_remote_exits_2(tmp_path: Path) -> None:
    with patch("shutil.which", side_effect=lambda cmd: None if cmd == "git" else "/usr/bin/uv"):
        result = runner.invoke(_cli, ["tools", "--tool-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "git" in result.output.lower()


def test_tools_no_git_no_remote_succeeds(tmp_path: Path) -> None:
    """--no-remote ne fait pas appel à git : doit fonctionner sans git."""
    with patch("shutil.which", side_effect=lambda cmd: None if cmd == "git" else "/usr/bin/uv"):
        result = runner.invoke(_cli, ["tools", "--no-remote", "--json", "--tool-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_tools_no_uv_exits_2(tmp_path: Path) -> None:
    with patch("shutil.which", return_value=None):
        result = runner.invoke(_cli, ["tools", "--tool-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "uv" in result.output.lower()
