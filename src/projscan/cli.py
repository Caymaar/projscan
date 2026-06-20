"""projscan.cli — command-line interface for projscan.

Two commands:
  projscan repos [PATHS]...   — scan folders for git repositories
  projscan tools              — scan uv tools installed from git

Exit codes:
  0   everything up to date
  1   at least one item needs updating (or has an error)
  2   fatal execution error
  130 keyboard interrupt (Ctrl-C)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from projscan.git import scan_paths
from projscan.render import repos_table, tools_table
from projscan.uv import scan_tools

console = Console()
err_console = Console(stderr=True)

# Statuses that indicate an item needs attention
_ACTIONABLE = frozenset(["behind", "update available", "diverged", "error"])


def _require_git() -> None:
    """Exit with a clear message if git is not found in PATH."""
    if shutil.which("git") is None:
        err_console.print("[bold red]Error:[/bold red] git is not installed or not found in PATH.")
        err_console.print("Install git at [link]https://git-scm.com/downloads[/link] and make sure it is in your PATH.")
        sys.exit(2)


def _require_uv() -> None:
    """Exit with a clear message if uv is not found in PATH."""
    if shutil.which("uv") is None:
        err_console.print("[bold red]Error:[/bold red] uv is not installed or not found in PATH.")
        err_console.print("Install uv at [link]https://docs.astral.sh/uv/getting-started/installation/[/link] and make sure it is in your PATH.")
        sys.exit(2)


def _needs_action(status: str) -> bool:
    return any(status == s or status.startswith(s) for s in _ACTIONABLE)


def _exit_code(statuses: list[str]) -> int:
    return 1 if any(_needs_action(s) for s in statuses) else 0


@click.group()
def _cli() -> None:
    """projscan — git repository and uv tool scanner."""


@_cli.command("repos")
@click.argument("paths", nargs=-1, type=click.Path(file_okay=False))
@click.option("--no-fetch", is_flag=True, help="Skip git fetch (use local cache).")
@click.option("--depth", default=3, show_default=True, metavar="INTEGER", help="Max recursion depth.")
@click.option("--json", "as_json", is_flag=True, help="JSON output (machine-readable).")
@click.option("--concurrency", default=16, show_default=True, metavar="INTEGER", help="Max parallel scans.")
def repos_cmd(
    paths: tuple[str, ...],
    no_fetch: bool,
    depth: int,
    as_json: bool,
    concurrency: int,
) -> None:
    """Scan one or more directories and show the status of each git repository.

    Defaults to the current directory if no PATH is given.

    \b
    Examples:
      projscan repos ~/code
      projscan repos --no-fetch --json ~/projects | jq '.[] | select(.status != "à jour")'
    """
    _require_git()
    roots = [Path(p) for p in paths] if paths else [Path.cwd()]
    try:
        if as_json:
            infos = asyncio.run(
                scan_paths(roots, fetch=not no_fetch, max_depth=depth, concurrency=concurrency)
            )
            click.echo(json.dumps([i.as_dict() for i in infos], ensure_ascii=False, indent=2))
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                progress.add_task(
                    f"Scanning{'  (network fetch)' if not no_fetch else ''}…",
                    total=None,
                )
                infos = asyncio.run(
                    scan_paths(roots, fetch=not no_fetch, max_depth=depth, concurrency=concurrency)
                )

            if not infos:
                console.print("[dim]No git repositories found.[/dim]")
                sys.exit(0)

            console.print(repos_table(infos))

            total = len(infos)
            ok = sum(1 for i in infos if i.status == "up to date")
            nok = total - ok
            console.print(
                f"[dim]{total} repo{'s' if total > 1 else ''}, "
                f"{ok} up to date, {nok} to update[/dim]"
            )

        sys.exit(_exit_code([i.status for i in infos]))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)


@_cli.command("tools")
@click.option("--tool-dir", "tool_dir", default=None, type=click.Path(file_okay=False), help="uv tools root (auto-detected by default).")
@click.option("--no-remote", is_flag=True, help="Skip remote checks (list local installs only).")
@click.option("--json", "as_json", is_flag=True, help="JSON output (machine-readable).")
def tools_cmd(tool_dir: str | None, no_remote: bool, as_json: bool) -> None:
    """Scan uv tools installed from git and check for updates.

    \b
    Examples:
      projscan tools
      projscan tools --no-remote --json
      projscan tools --tool-dir ~/.local/share/uv/tools
    """
    _require_uv()
    if not no_remote:
        _require_git()
    root = Path(tool_dir) if tool_dir else None
    try:
        if as_json:
            infos = asyncio.run(
                scan_tools(root, check_remote=not no_remote)
            )
            click.echo(json.dumps([i.as_dict() for i in infos], ensure_ascii=False, indent=2))
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                progress.add_task(
                    f"Scanning tools{'  (checking remotes)' if not no_remote else ''}…",
                    total=None,
                )
                infos = asyncio.run(
                    scan_tools(root, check_remote=not no_remote)
                )

            if not infos:
                console.print("[dim]No uv tools found (or uv not installed).[/dim]")
                sys.exit(0)

            console.print(tools_table(infos))

            total = len(infos)
            ok = sum(1 for i in infos if i.status == "up to date")
            nok = total - ok
            console.print(
                f"[dim]{total} tool{'s' if total > 1 else ''}, "
                f"{ok} up to date, {nok} to update[/dim]"
            )

        sys.exit(_exit_code([i.status for i in infos]))
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(2)


def main() -> None:
    """Entry point for the projscan script."""
    try:
        _cli()
    except KeyboardInterrupt:
        sys.exit(130)
