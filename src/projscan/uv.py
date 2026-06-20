"""projscan.uv — scan des outils installés par `uv tool` depuis des sources git.

Objectif : pour chaque tool installé via `uv tool install git+https://...`,
savoir s'il est à jour par rapport à la branche/ref distante, SANS cloner.

Stratégie :
  1. localiser la racine des tools (`uv tool dir`, repli sur les chemins connus)
  2. pour chaque tool, retrouver l'URL git + le commit installé + la ref demandée
       - source fiable : uv-receipt.toml (métadonnées uv)
       - repli PEP 610 : <venv>/.../<pkg>-*.dist-info/direct_url.json
  3. interroger le HEAD distant via `git ls-remote <url> <ref>` (1 requête, 0 objet)
  4. comparer : installed_commit == remote_commit -> à jour, sinon MAJ dispo

Pensé pour Textual : fonctions d'I/O async, scan parallèle borné, dataclass.
"""

from __future__ import annotations

import asyncio
import json
import os
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class ToolInfo:
    name: str
    version: str | None
    url: str | None            # URL git source (None si non-git / PyPI)
    ref: str | None            # branche/tag demandé (ex "main"), peut être None
    installed_commit: str | None
    remote_commit: str | None
    status: str                # à jour / MAJ dispo / inconnu / pas git / erreur
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


# --- localisation de la racine uv tools --------------------------------------

async def uv_tool_dir() -> Path | None:
    """Racine des environnements de tools uv (ex ~/.local/share/uv/tools)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "uv", "tool", "dir",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode == 0:
            p = Path(out.decode().strip())
            if p.is_dir():
                return p
    except (FileNotFoundError, asyncio.TimeoutError):
        pass
    # replis usuels
    for cand in (
        os.environ.get("UV_TOOL_DIR"),
        "~/.local/share/uv/tools",
        "~/Library/Application Support/uv/tools",  # macOS
    ):
        if cand:
            p = Path(cand).expanduser()
            if p.is_dir():
                return p
    return None


# --- extraction des infos d'installation -------------------------------------

def _parse_direct_url(data: dict) -> dict:
    """Extrait url/commit/ref d'un direct_url.json (PEP 610)."""
    url = data.get("url")
    vcs = data.get("vcs_info") or {}
    return {
        "url": url,
        "installed_commit": vcs.get("commit_id"),
        "ref": vcs.get("requested_revision"),  # peut être absent
    }


def _read_dist_info_version(tool_path: Path) -> str | None:
    """Lit la version installée depuis *.dist-info/METADATA (header PEP 566 'Version:').

    Fallback universel : couvre les tools PyPI où le receipt ne stocke pas la version.
    """
    for dist_info in tool_path.rglob("*.dist-info"):
        metadata = dist_info / "METADATA"
        if not metadata.is_file():
            continue
        try:
            for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("version:"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            continue
    return None


def _find_direct_url_json(tool_path: Path) -> dict | None:
    """Cherche un direct_url.json dans le venv du tool (layout uv variable)."""
    # uv place le venv soit directement dans tool_path, soit dans un sous-dossier.
    for dist_info in tool_path.rglob("*.dist-info"):
        du = dist_info / "direct_url.json"
        if du.is_file():
            try:
                return json.loads(du.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _read_receipt(tool_path: Path) -> dict | None:
    """Lit uv-receipt.toml et tente d'en extraire url/commit/ref + version.

    Le schéma du receipt a évolué selon les versions d'uv ; on reste tolérant
    et on récupère ce qu'on peut, peu importe l'emplacement exact des clés.
    """
    receipt = tool_path / "uv-receipt.toml"
    if not receipt.is_file():
        return None
    try:
        data = tomllib.loads(receipt.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None

    found: dict = {"url": None, "installed_commit": None, "ref": None, "version": None}

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            # clés git possibles selon les versions d'uv
            for k, v in obj.items():
                if isinstance(v, str):
                    lk = k.lower()
                    # clé "git" explicite -> on prend la valeur telle quelle
                    # (couvre les URLs http(s)/ssh ET les chemins git locaux).
                    if lk == "git" and v:
                        found["url"] = found["url"] or v
                    elif lk in {"url", "repository"} and (
                        "://" in v or v.startswith("git@") or v.endswith(".git")
                    ):
                        found["url"] = found["url"] or v
                    elif lk in {"rev", "commit", "commit_id", "precise"} and _looks_like_sha(v):
                        found["installed_commit"] = found["installed_commit"] or v
                    elif lk in {"reference", "requested_revision", "branch", "tag", "ref"}:
                        found["ref"] = found["ref"] or v
                    elif lk == "version":
                        found["version"] = found["version"] or v
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return found


def _looks_like_sha(s: str) -> bool:
    return 7 <= len(s) <= 40 and all(c in "0123456789abcdef" for c in s.lower())


def _split_git_url(raw: str) -> tuple[str, str | None]:
    """Sépare une URL git de sa ref encodée.

    uv (et pip) encodent parfois la ref dans l'URL :
      - https://host/repo.git?rev=main      -> (https://host/repo.git, "main")
      - https://host/repo.git@v1.2          -> (https://host/repo.git, "v1.2")
      - https://host/repo.git#main          -> (https://host/repo.git, "main")
      - git+https://host/repo.git           -> (https://host/repo.git, None)
    Renvoie (url_propre, ref|None). ls-remote a besoin de l'URL SANS la ref.
    """
    ref: str | None = None
    url = raw.strip()

    # préfixe pip "git+"
    if url.startswith("git+"):
        url = url[4:]

    # ?rev=... ou &rev=... (paramètre de requête)
    for sep in ("?", "&"):
        if sep in url:
            base, _, query = url.partition(sep)
            for part in query.split("&"):
                k, _, v = part.partition("=")
                if k in {"rev", "ref", "branch", "tag"} and v:
                    ref = ref or v
            # on retire toute la query de l'URL
            url = base

    # fragment #ref
    if "#" in url:
        url, _, frag = url.partition("#")
        # fragments du style "egg=...&subdirectory=..." : ignorer; sinon = ref
        if frag and "=" not in frag:
            ref = ref or frag

    # suffixe @ref (après le .git), mais PAS le @ de git@host (scp-like)
    # on ne traite le @ que s'il suit ".git"
    git_at = url.rfind(".git@")
    if git_at != -1:
        ref = ref or url[git_at + len(".git@"):]
        url = url[: git_at + len(".git")]

    return url, ref


def read_install_info(tool_path: Path) -> dict:
    """Combine receipt (prioritaire) et direct_url.json (repli/complément)."""
    info: dict = {"url": None, "installed_commit": None, "ref": None, "version": None}

    receipt = _read_receipt(tool_path)
    if receipt:
        for k in info:
            info[k] = info[k] or receipt.get(k)

    # complète les trous avec direct_url.json
    if not all((info["url"], info["installed_commit"])):
        du = _find_direct_url_json(tool_path)
        if du:
            parsed = _parse_direct_url(du)
            for k in ("url", "installed_commit", "ref"):
                info[k] = info[k] or parsed.get(k)

    # nettoie l'URL (retire ?rev=/@ref/#frag) et récupère la ref encodée s'il
    # n'y en avait pas. ls-remote a besoin de l'URL nue.
    if info["url"]:
        clean_url, url_ref = _split_git_url(info["url"])
        info["url"] = clean_url
        info["ref"] = info["ref"] or url_ref

    # dernier recours : lire la version depuis dist-info/METADATA
    if not info["version"]:
        info["version"] = _read_dist_info_version(tool_path)
    return info


# --- HEAD distant sans clone -------------------------------------------------

async def remote_head(url: str, ref: str | None = None, *, timeout: float = 30.0) -> str | None:
    """SHA du HEAD distant via ls-remote. ref None -> HEAD par défaut du repo."""
    args = ["git", "ls-remote", url]
    if ref:
        args.append(ref)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # pas de prompt auth
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    text = out.decode(errors="replace").strip()
    if not text:
        return None
    # ls-remote peut renvoyer plusieurs lignes (ex refs/heads/main + refs/tags).
    # On préfère une ligne refs/heads/<ref> si ref donné, sinon la 1re.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if ref:
        for ln in lines:
            sha, _, name = ln.partition("\t")
            if name.endswith(f"refs/heads/{ref}") or name == ref:
                return sha.strip()
    return lines[0].split("\t")[0].strip()


def _same_commit(a: str | None, b: str | None) -> bool:
    """Compare deux SHA possiblement de longueurs différentes (court vs long)."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


# --- scan d'un tool ----------------------------------------------------------

async def scan_tool(name: str, tool_path: Path, *, check_remote: bool = True) -> ToolInfo:
    """Scanne un tool uv installé et détermine si une mise à jour est disponible."""
    try:
        info = read_install_info(tool_path)
        url = info["url"]
        installed = info["installed_commit"]
        ref = info["ref"]
        version = info["version"]

        if not url:
            return ToolInfo(name, version, None, None, installed, None, "not git")

        remote = None
        if check_remote:
            remote = await remote_head(url, ref)

        if remote is None:
            status = "unknown"  # offline, auth, repo privé...
        elif _same_commit(installed, remote):
            status = "up to date"
        else:
            status = "update available"

        return ToolInfo(name, version, url, ref, installed, remote, status)
    except Exception as exc:
        return ToolInfo(name, None, None, None, None, None, "error",
                        error=f"{type(exc).__name__}: {exc}")


def find_tools(tools_root: Path) -> list[tuple[str, Path]]:
    """Liste (nom, chemin) des tools : chaque sous-dossier direct de la racine."""
    out: list[tuple[str, Path]] = []
    try:
        for d in sorted(tools_root.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                out.append((d.name, d))
    except (OSError, PermissionError):
        pass
    return out


async def scan_tools(
    tools_root: Path | None = None, *, check_remote: bool = True, concurrency: int = 16
) -> list[ToolInfo]:
    """Scanne tous les uv tools. Trouve la racine automatiquement si non fournie."""
    root = tools_root or await uv_tool_dir()
    if root is None:
        return []
    tools = find_tools(root)
    sem = asyncio.Semaphore(concurrency)

    async def bounded(item: tuple[str, Path]) -> ToolInfo:
        name, path = item
        async with sem:
            return await scan_tool(name, path, check_remote=check_remote)

    return list(await asyncio.gather(*(bounded(t) for t in tools)))
