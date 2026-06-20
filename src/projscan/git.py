"""projscan.git — scan de dépôts git locaux pour un dashboard "suis-je à jour ?".

Conçu pour être réutilisé dans un worker Textual : toutes les fonctions d'I/O sont
async (asyncio.create_subprocess_exec), et scan_paths() parallélise via un Semaphore.

Pour chaque repo trouvé on remonte :
  - name          : nom du dossier
  - version       : git describe --tags, sinon project.version de pyproject.toml
  - commit        : hash court du HEAD
  - subject / when : message + date relative du dernier commit
  - branch        : branche courante
  - upstream      : nom de l'upstream (ou None s'il n'y en a pas)
  - behind / ahead : nb de commits vs upstream (None si pas d'upstream)
  - status        : libellé synthétique (à jour / en retard / ...)
"""

from __future__ import annotations

import asyncio
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class RepoInfo:
    name: str
    path: str
    version: str | None
    commit: str | None
    subject: str | None
    when: str | None
    branch: str | None
    upstream: str | None
    behind: int | None
    ahead: int | None
    status: str
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


async def _git(repo: Path, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Lance une commande git dans `repo`. Renvoie (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timeout"
    return proc.returncode, out.decode(errors="replace").strip(), err.decode(errors="replace").strip()


def is_git_repo(path: Path) -> bool:
    """Un dépôt git "racine" : présence d'un .git (dossier classique ou worktree)."""
    return (path / ".git").exists()


def _read_pyproject_version(repo: Path) -> str | None:
    pp = repo / "pyproject.toml"
    if not pp.is_file():
        return None
    try:
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    # project.version (PEP 621) puis tool.poetry.version en repli
    v = data.get("project", {}).get("version")
    if v:
        return str(v)
    v = data.get("tool", {}).get("poetry", {}).get("version")
    return str(v) if v else None


async def _version(repo: Path) -> str | None:
    """git describe --tags si possible, sinon la version du pyproject."""
    rc, out, _ = await _git(repo, "describe", "--tags", "--always", "--dirty")
    if rc == 0 and out:
        # describe renvoie le hash court s'il n'y a aucun tag ; dans ce cas on
        # préfère pyproject s'il existe, sinon on garde le hash.
        py = _read_pyproject_version(repo)
        # heuristique : si describe ne ressemble pas à un tag (pas de chiffre.point)
        # et qu'on a une version pyproject, on prend pyproject.
        looks_like_tag = any(c.isdigit() for c in out) and "." in out
        if looks_like_tag:
            return out
        if py:
            return py
        return out
    return _read_pyproject_version(repo)


async def scan_repo(path: Path, *, fetch: bool = True) -> RepoInfo:
    """Scanne un dépôt unique. Si fetch=True, met à jour les refs distantes d'abord."""
    name = path.name
    try:
        if fetch:
            # --quiet, ignore les erreurs (offline, pas de remote, auth...) :
            # on continue avec les refs locales.
            await _git(path, "fetch", "--quiet", "--all", timeout=60.0)

        rc, log, _ = await _git(path, "log", "-1", "--format=%h%x1f%s%x1f%cr")
        commit = subject = when = None
        if rc == 0 and log:
            parts = log.split("\x1f")
            commit = parts[0] if len(parts) > 0 else None
            subject = parts[1] if len(parts) > 1 else None
            when = parts[2] if len(parts) > 2 else None

        _, branch, _ = await _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        branch = branch or None

        rc_up, upstream, _ = await _git(
            path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
        )
        upstream = upstream if rc_up == 0 and upstream else None

        behind = ahead = None
        if upstream:
            rc_c, counts, _ = await _git(
                path, "rev-list", "--left-right", "--count", "HEAD...@{u}"
            )
            if rc_c == 0 and counts:
                # format : "<ahead>\t<behind>" (gauche=HEAD, droite=upstream)
                a, _, b = counts.partition("\t")
                try:
                    ahead, behind = int(a.strip()), int(b.strip())
                except ValueError:
                    pass

        version = await _version(path)

        status = _status_label(upstream, behind, ahead)
        return RepoInfo(
            name=name,
            path=str(path),
            version=version,
            commit=commit,
            subject=subject,
            when=when,
            branch=branch,
            upstream=upstream,
            behind=behind,
            ahead=ahead,
            status=status,
        )
    except Exception as exc:  # robustesse : un repo cassé ne doit pas tuer le scan
        return RepoInfo(
            name=name, path=str(path), version=None, commit=None, subject=None,
            when=None, branch=None, upstream=None, behind=None, ahead=None,
            status="error", error=f"{type(exc).__name__}: {exc}",
        )


def _status_label(upstream: str | None, behind: int | None, ahead: int | None) -> str:
    if upstream is None:
        return "no upstream"
    if behind is None or ahead is None:
        return "unknown"
    if behind and ahead:
        return f"diverged (↓{behind} ↑{ahead})"
    if behind:
        return f"behind (↓{behind})"
    if ahead:
        return f"ahead (↑{ahead})"
    return "up to date"


def find_repos(roots: list[Path], *, max_depth: int = 3) -> list[Path]:
    """Parcourt récursivement les racines et renvoie tous les dépôts git.

    S'arrête de descendre dès qu'un .git est trouvé (on ne scanne pas l'intérieur
    d'un repo). max_depth limite la profondeur pour éviter d'exploser sur un home.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth or d in seen:
            return
        seen.add(d)
        if is_git_repo(d):
            found.append(d)
            return  # ne pas descendre dans un repo
        try:
            children = [c for c in d.iterdir() if c.is_dir() and not c.is_symlink()]
        except (PermissionError, OSError):
            return
        for c in children:
            if c.name in {".git", "node_modules", "__pycache__", ".venv", "venv"}:
                continue
            walk(c, depth + 1)

    for root in roots:
        root = Path(root).expanduser()
        if root.is_dir():
            walk(root, 0)
    return sorted(found)


async def scan_paths(
    roots: list[Path], *, fetch: bool = True, max_depth: int = 3, concurrency: int = 16
) -> list[RepoInfo]:
    """Trouve et scanne tous les repos sous `roots`, en parallèle (borné)."""
    repos = find_repos(roots, max_depth=max_depth)
    sem = asyncio.Semaphore(concurrency)

    async def bounded(p: Path) -> RepoInfo:
        async with sem:
            return await scan_repo(p, fetch=fetch)

    return list(await asyncio.gather(*(bounded(p) for p in repos)))
