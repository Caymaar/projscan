"""projscan.render — rendu Rich partagé entre les deux commandes CLI.

Toutes les fonctions retournent des objets Rich (Table, Text) sans écrire
directement dans la console, pour pouvoir être réutilisées dans Textual.
"""

from __future__ import annotations

from rich import box
from rich.table import Table
from rich.text import Text

from projscan.git import RepoInfo
from projscan.uv import ToolInfo

# Mapping status -> Rich style. Variable-prefix statuses (behind / diverged)
# are handled by status_style() below.
_STATUS_STYLES: dict[str, str] = {
    "up to date": "green",
    "ahead": "cyan",
    "update available": "yellow",
    "no upstream": "dim",
    "not git": "dim",
    "unknown": "dim",
    "error": "bold red",
}

# Display priority: actionable items bubble to the top.
_STATUS_PRIORITY: dict[str, int] = {
    "error": 0,
    "behind": 1,
    "update available": 2,
    "diverged": 3,
    "unknown": 4,
    "ahead": 5,
    "no upstream": 6,
    "not git": 7,
    "up to date": 8,
}


def status_style(status: str) -> str:
    """Return the Rich style corresponding to the given status string."""
    if status.startswith("behind"):
        return "red"
    if status.startswith("diverged"):
        return "magenta"
    if status.startswith("ahead"):
        return "cyan"
    return _STATUS_STYLES.get(status, "dim")


def _sort_priority(status: str) -> int:
    for prefix, priority in _STATUS_PRIORITY.items():
        if status == prefix or status.startswith(prefix):
            return priority
    return 9


def _shorten_url(url: str) -> str:
    """Retire le schéma d'une URL pour économiser de la place."""
    if "://" in url:
        _, _, rest = url.partition("://")
        return rest
    return url


def repos_table(infos: list[RepoInfo]) -> Table:
    """Construit une Table Rich pour les RepoInfo, triée par priorité d'action."""
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=False,
        highlight=False,
    )
    table.add_column("Name", style="bold", no_wrap=True, min_width=12)
    table.add_column("Version", no_wrap=True)
    table.add_column("Branch", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Commit", no_wrap=True, style="dim")
    table.add_column("Message", max_width=48)
    table.add_column("When", no_wrap=True, style="dim")

    sorted_infos = sorted(infos, key=lambda r: (_sort_priority(r.status), r.name.lower()))

    for r in sorted_infos:
        table.add_row(
            r.name,
            r.version or "",
            r.branch or "",
            Text(r.status, style=status_style(r.status)),
            r.commit or "",
            r.subject or "",
            r.when or "",
        )
    return table


def tools_table(infos: list[ToolInfo]) -> Table:
    """Construit une Table Rich pour les ToolInfo, triée par priorité d'action."""
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=False,
        highlight=False,
    )
    table.add_column("Name", style="bold", no_wrap=True, min_width=12)
    table.add_column("Version", no_wrap=True)
    table.add_column("Source", max_width=40)
    table.add_column("Ref", no_wrap=True)
    table.add_column("Commit", no_wrap=True, style="dim")
    table.add_column("Status", no_wrap=True)

    sorted_infos = sorted(infos, key=lambda t: (_sort_priority(t.status), t.name.lower()))

    for t in sorted_infos:
        url_short = _shorten_url(t.url) if t.url else ""
        commit_short = (t.installed_commit or "")[:8]
        table.add_row(
            t.name,
            t.version or "",
            url_short,
            t.ref or "",
            commit_short,
            Text(t.status, style=status_style(t.status)),
        )
    return table
