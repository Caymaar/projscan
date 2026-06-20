"""projscan — scanner de dépôts git locaux et de tools uv installés.

API publique importable depuis une app Textual ou tout autre code async :

    from projscan import scan_paths, scan_tools, RepoInfo, ToolInfo

    repos = await scan_paths([Path("~/code")], fetch=True)
    tools = await scan_tools(check_remote=True)
"""

from __future__ import annotations

from projscan.git import RepoInfo, scan_paths, scan_repo, find_repos
from projscan.uv import ToolInfo, scan_tools, scan_tool, uv_tool_dir

__all__ = [
    "RepoInfo",
    "scan_paths",
    "scan_repo",
    "find_repos",
    "ToolInfo",
    "scan_tools",
    "scan_tool",
    "uv_tool_dir",
]
__version__ = "0.1.0"
