"""Tests pour projscan.uv — layouts de tools uv simulés + remote bare local.

Pas de réseau : l'URL "git" pointe vers un dépôt bare local (chemin de fichier),
ce que `git ls-remote` accepte parfaitement. On contrôle le commit distant,
donc à-jour vs MAJ-dispo de façon déterministe.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from projscan.uv import ToolInfo, _same_commit, scan_tool, scan_tools


# --- helpers git -------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {res.stderr.strip()}")
    return res.stdout.strip()


def make_remote_with_commits(tmp: Path, name: str, n: int) -> tuple[Path, list[str]]:
    """Crée un bare remote + un work clone, fait n commits, push. Renvoie (bare, shas)."""
    bare = tmp / f"{name}.git"
    bare.mkdir(parents=True)
    git(bare, "init", "-q", "--bare", "-b", "main")

    work = tmp / f"{name}_work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@t.t")
    git(work, "config", "user.name", "test")
    git(work, "remote", "add", "origin", str(bare))

    shas = []
    for i in range(n):
        (work / "f.txt").write_text(f"commit {i}", encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", f"c{i}")
        shas.append(git(work, "rev-parse", "HEAD"))
    git(work, "push", "-q", "-u", "origin", "main")
    return bare, shas


# --- helpers layout uv tool --------------------------------------------------

def make_tool_with_receipt(root: Path, name: str, url: str, commit: str,
                           ref: str = "main", version: str = "1.0.0") -> Path:
    tp = root / name
    tp.mkdir(parents=True)
    (tp / "uv-receipt.toml").write_text(
        "[tool]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n\n'
        "[tool.source]\n"
        f'git = "{url}"\n'
        f'rev = "{commit}"\n'
        f'reference = "{ref}"\n',
        encoding="utf-8",
    )
    return tp


def make_tool_with_direct_url(root: Path, name: str, url: str, commit: str,
                              ref: str = "main", version: str = "2.0.0") -> Path:
    tp = root / name
    di = tp / "lib" / "python3.12" / "site-packages" / f"{name}-{version}.dist-info"
    di.mkdir(parents=True)
    (di / "direct_url.json").write_text(
        json.dumps({
            "url": url,
            "vcs_info": {"vcs": "git", "commit_id": commit, "requested_revision": ref},
        }),
        encoding="utf-8",
    )
    return tp


# --- tests -------------------------------------------------------------------

async def test_receipt_up_to_date(tmp_path: Path) -> None:
    bare, shas = make_remote_with_commits(tmp_path, "repoA", 3)
    root = tmp_path / "tools"
    tp = make_tool_with_receipt(root, "mytool", str(bare), shas[-1])
    info = await scan_tool("mytool", tp)
    assert info.url == str(bare), f"got {info.url!r}"
    assert info.installed_commit == shas[-1]
    assert info.status == "up to date", f"got {info.status!r}"


async def test_receipt_behind(tmp_path: Path) -> None:
    bare, shas = make_remote_with_commits(tmp_path, "repoB", 3)
    root = tmp_path / "tools"
    tp = make_tool_with_receipt(root, "oldtool", str(bare), shas[0])
    info = await scan_tool("oldtool", tp)
    assert info.installed_commit == shas[0]
    assert _same_commit(info.remote_commit, shas[-1]), \
        f"got {info.remote_commit!r} vs {shas[-1]!r}"
    assert info.status == "update available", f"got {info.status!r}"


async def test_direct_url_fallback(tmp_path: Path) -> None:
    bare, shas = make_remote_with_commits(tmp_path, "repoC", 2)
    root = tmp_path / "tools"
    tp = make_tool_with_direct_url(root, "dutool", str(bare), shas[-1])
    info = await scan_tool("dutool", tp)
    assert info.url == str(bare), f"got {info.url!r}"
    assert info.installed_commit == shas[-1]
    assert info.ref == "main", f"got {info.ref!r}"
    assert info.status == "up to date", f"got {info.status!r}"


async def test_receipt_takes_priority(tmp_path: Path) -> None:
    bare, shas = make_remote_with_commits(tmp_path, "repoD", 3)
    root = tmp_path / "tools"
    tp = make_tool_with_receipt(root, "both", str(bare), shas[-1])
    # direct_url.json incohérent — le receipt doit primer
    di = tp / "lib" / "python3.12" / "site-packages" / "both-9.9.dist-info"
    di.mkdir(parents=True)
    (di / "direct_url.json").write_text(json.dumps({
        "url": "https://example.com/wrong.git",
        "vcs_info": {"vcs": "git", "commit_id": "deadbeef" * 5, "requested_revision": "x"},
    }), encoding="utf-8")
    info = await scan_tool("both", tp)
    assert info.url == str(bare), f"got {info.url!r}"
    assert info.installed_commit == shas[-1]
    assert info.status == "up to date", f"got {info.status!r}"


async def test_short_vs_long_sha(tmp_path: Path) -> None:
    bare, shas = make_remote_with_commits(tmp_path, "repoE", 1)
    root = tmp_path / "tools"
    short = shas[-1][:8]  # uv stocke parfois un hash court
    tp = make_tool_with_receipt(root, "shorttool", str(bare), short)
    info = await scan_tool("shorttool", tp)
    assert info.status == "up to date", \
        f"got {info.status!r} ({short} vs {info.remote_commit})"


async def test_non_git_tool(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    tp = root / "pypitool"
    di = tp / "lib" / "python3.12" / "site-packages" / "pypitool-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "RECORD").write_text("", encoding="utf-8")
    info = await scan_tool("pypitool", tp)
    assert info.status == "not git", f"got {info.status!r}"
    assert info.error is None, f"got {info.error!r}"


async def test_unknown_when_unreachable(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    fake_url = str(tmp_path / "does_not_exist.git")
    tp = make_tool_with_receipt(root, "deadtool", fake_url, "a" * 40)
    info = await scan_tool("deadtool", tp)
    assert info.remote_commit is None, f"got {info.remote_commit!r}"
    assert info.status == "unknown", f"got {info.status!r}"


async def test_scan_tools_root(tmp_path: Path) -> None:
    bare, shas = make_remote_with_commits(tmp_path, "repoF", 2)
    root = tmp_path / "tools_multi"
    make_tool_with_receipt(root, "t1", str(bare), shas[-1])      # à jour
    make_tool_with_receipt(root, "t2", str(bare), shas[0])       # MAJ dispo
    make_tool_with_direct_url(root, "t3", str(bare), shas[-1])   # à jour
    infos = await scan_tools(root)
    by = {i.name: i for i in infos}
    assert len(infos) == 3, f"got {len(infos)}"
    assert by["t1"].status == "up to date", f"got {by['t1'].status!r}"
    assert by["t2"].status == "update available", f"got {by['t2'].status!r}"
    assert by["t3"].status == "up to date", f"got {by['t3'].status!r}"
    errors = [i.error for i in infos if i.error]
    assert not errors, str(errors)


async def test_version_from_dist_info_metadata(tmp_path: Path) -> None:
    """Tool PyPI sans receipt : la version est lue dans dist-info/METADATA."""
    root = tmp_path / "tools"
    tp = root / "memray"
    di = tp / "lib" / "python3.12" / "site-packages" / "memray-1.7.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: memray\nVersion: 1.7.0\nSummary: A memory profiler.\n",
        encoding="utf-8",
    )
    info = await scan_tool("memray", tp, check_remote=False)
    assert info.version == "1.7.0", f"got {info.version!r}"
    assert info.status == "not git"


async def test_version_metadata_does_not_override_receipt(tmp_path: Path) -> None:
    """La version du receipt reste prioritaire sur METADATA."""
    bare, shas = make_remote_with_commits(tmp_path, "repo", 1)
    root = tmp_path / "tools"
    tp = make_tool_with_receipt(root, "mytool", str(bare), shas[-1], version="2.0.0")
    # Ajoute un METADATA avec une version différente
    di = tp / "lib" / "python3.12" / "site-packages" / "mytool-9.9.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: mytool\nVersion: 9.9.0\n", encoding="utf-8")
    info = await scan_tool("mytool", tp, check_remote=False)
    assert info.version == "2.0.0", f"receipt doit primer ; got {info.version!r}"


def test_tool_info_as_dict() -> None:
    info = ToolInfo(
        name="foo", version="1.0", url="https://x.git", ref="main",
        installed_commit="abc1234", remote_commit="abc1234", status="à jour",
    )
    d = info.as_dict()
    for key in ("name", "version", "url", "ref", "installed_commit", "remote_commit", "status", "error"):
        assert key in d, f"clé manquante : {key}"
