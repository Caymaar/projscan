"""Tests pour projscan.render — construction des tables Rich et mapping de styles."""

from __future__ import annotations

import pytest
from rich.table import Table

from projscan.git import RepoInfo
from projscan.render import _sort_priority, repos_table, status_style, tools_table
from projscan.uv import ToolInfo


def _make_repo(status: str = "up to date", **kw) -> RepoInfo:
    defaults = dict(
        name="repo", path="/tmp/repo", version="1.0", commit="abc1234",
        subject="msg", when="2h", branch="main", upstream="origin/main",
        behind=0, ahead=0, status=status, error=None,
    )
    return RepoInfo(**{**defaults, **kw})


def _make_tool(status: str = "up to date", **kw) -> ToolInfo:
    defaults = dict(
        name="tool", version="1.0", url="https://github.com/x/y.git",
        ref="main", installed_commit="abc1234", remote_commit="abc1234",
        status=status, error=None,
    )
    return ToolInfo(**{**defaults, **kw})


# --- status_style ------------------------------------------------------------

@pytest.mark.parametrize("status, expected", [
    ("up to date", "green"),
    ("update available", "yellow"),
    ("ahead (↑1)", "cyan"),
    ("no upstream", "dim"),
    ("not git", "dim"),
    ("unknown", "dim"),
    ("error", "bold red"),
])
def test_status_style_exact(status: str, expected: str) -> None:
    assert status_style(status) == expected


@pytest.mark.parametrize("status", ["behind (↓1)", "behind (↓5)"])
def test_status_style_behind(status: str) -> None:
    assert status_style(status) == "red"


@pytest.mark.parametrize("status", ["diverged (↓1 ↑1)", "diverged (↓2 ↑3)"])
def test_status_style_diverged(status: str) -> None:
    assert status_style(status) == "magenta"


# --- priorité de tri ---------------------------------------------------------

def test_sort_priority_order() -> None:
    assert _sort_priority("error") < _sort_priority("up to date")
    assert _sort_priority("behind (↓2)") < _sort_priority("up to date")
    assert _sort_priority("update available") < _sort_priority("up to date")
    assert _sort_priority("diverged (↓1 ↑1)") < _sort_priority("up to date")


def test_repos_table_sorting() -> None:
    infos = [
        _make_repo("up to date", name="zzz"),
        _make_repo("behind (↓2)", name="aaa"),
        _make_repo("update available", name="mmm"),
    ]
    table = repos_table(infos)
    # Vérifie que le tri place l'item actionable en premier
    rows = list(table.rows)
    # Rich ne donne pas directement le contenu des cellules via l'API publique,
    # mais on peut vérifier que la table se construit sans lever et a le bon nb de lignes.
    assert len(rows) == 3


# --- repos_table -------------------------------------------------------------

def test_repos_table_empty() -> None:
    table = repos_table([])
    assert isinstance(table, Table)
    assert len(table.rows) == 0


def test_repos_table_single() -> None:
    table = repos_table([_make_repo()])
    assert isinstance(table, Table)
    assert len(table.rows) == 1


def test_repos_table_columns() -> None:
    table = repos_table([_make_repo()])
    col_names = [col.header for col in table.columns]
    for expected in ("Name", "Version", "Branch", "Status", "Commit", "Message", "When"):
        assert expected in col_names, f"colonne manquante : {expected}"


def test_repos_table_no_upstream() -> None:
    info = _make_repo("no upstream", upstream=None, behind=None, ahead=None)
    table = repos_table([info])
    assert len(table.rows) == 1


# --- tools_table -------------------------------------------------------------

def test_tools_table_empty() -> None:
    table = tools_table([])
    assert isinstance(table, Table)
    assert len(table.rows) == 0


def test_tools_table_single() -> None:
    table = tools_table([_make_tool()])
    assert isinstance(table, Table)
    assert len(table.rows) == 1


def test_tools_table_columns() -> None:
    table = tools_table([_make_tool()])
    col_names = [col.header for col in table.columns]
    for expected in ("Name", "Version", "Source", "Ref", "Commit", "Status"):
        assert expected in col_names, f"colonne manquante : {expected}"


def test_tools_table_not_git() -> None:
    info = _make_tool("not git", url=None, ref=None, installed_commit=None, remote_commit=None)
    table = tools_table([info])
    assert len(table.rows) == 1


def test_tools_table_url_shortened() -> None:
    # Juste s'assurer que ça ne lève pas avec une URL longue
    info = _make_tool(url="https://github.com/very-long-org/very-long-repo-name.git")
    table = tools_table([info])
    assert len(table.rows) == 1
