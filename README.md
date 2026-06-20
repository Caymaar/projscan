# projscan

Scanner for local git repositories and `uv` tools installed from git.  
Answers one simple question: **am I up to date?**

## Quick start

No installation needed — run directly with `uvx`:

```bash
uvx  projscan repos .
uvx  projscan tools
```

## Installation

```bash
uv tool install projscan
```

Or in development mode:

```bash
git clone ...
cd projscan
uv sync
uv run projscan --help
```

## Commands

### `projscan repos [PATHS]...`

Recursively scans one or more directories and displays the status of each git repository.

```
╭──────────────────────┬─────────┬─────────┬─────────────────────┬────────┬────────────────────┬─────────────╮
│ Name                 │ Version │ Branch  │ Status              │ Commit │ Message            │ When        │
├──────────────────────┼─────────┼─────────┼─────────────────────┼────────┼────────────────────┼─────────────┤
│ my-api               │ 2.1.0   │ main    │ behind (↓3)         │ a4f2c1 │ fix: timeout retry │ 2 days ago  │
│ my-dashboard         │ 1.0.0   │ main    │ diverged (↓1 ↑2)    │ b8e3d9 │ feat: dark mode    │ 5 hours ago │
│ projscan             │ 0.1.0   │ main    │ up to date          │ c1a2b3 │ initial commit     │ 1 hour ago  │
╰──────────────────────┴─────────┴─────────┴─────────────────────┴────────┴────────────────────┴─────────────╯
3 repos, 1 up to date, 2 to update
```

**Options:**

| Option | Description |
|--------|-------------|
| `--no-fetch` | Skip `git fetch` — use local cache (fast) |
| `--depth INTEGER` | Max recursion depth (default: 3) |
| `--json` | Machine-readable JSON output |
| `--concurrency INTEGER` | Parallel scans (default: 16) |

```bash
# Scan ~/code and ~/work
projscan repos ~/code ~/work

# Fast, no network
projscan repos --no-fetch ~/code

# Pipe JSON to jq
projscan repos --no-fetch --json ~/code | jq '.[] | select(.status != "up to date")'
```

### `projscan tools`

Scans tools installed via `uv tool install git+https://...` and checks whether updates are available (via `git ls-remote`, without cloning).

```
╭────────────────┬─────────┬───────────────────────────────┬────────┬──────────┬────────────────╮
│ Name           │ Version │ Source                        │ Ref    │ Commit   │ Status         │
├────────────────┼─────────┼───────────────────────────────┼────────┼──────────┼────────────────┤
│ my-tool        │ 1.2.0   │ github.com/org/my-tool.git    │ main   │ f10c2123 │ update avail.  │
│ other-tool     │ 0.9.1   │ github.com/org/other-tool.git │ main   │ fc9e74c5 │ up to date     │
╰────────────────┴─────────┴───────────────────────────────┴────────┴──────────┴────────────────╯
2 tools, 1 up to date, 1 to update
```

**Options:**

| Option | Description |
|--------|-------------|
| `--tool-dir PATH` | uv tools root directory (auto-detected by default) |
| `--no-remote` | List locally without checking remotes |
| `--json` | Machine-readable JSON output |

```bash
projscan tools
projscan tools --no-remote --json
projscan tools --tool-dir ~/.local/share/uv/tools
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Everything is up to date |
| `1`  | At least one item needs updating (or has an error) |
| `2`  | Fatal execution error |
| `130` | Keyboard interrupt (Ctrl-C) |

Useful in scripts or CI:

```bash
projscan repos --no-fetch ~/code || echo "Some repos are behind!"
```

## Status colors

| Status | Color | Meaning |
|--------|-------|---------|
| `up to date` | green | In sync with upstream |
| `behind (↓N)` | red | N remote commits not yet pulled |
| `ahead (↑N)` | cyan | N local commits not yet pushed |
| `diverged (↓N ↑M)` | magenta | Branches have diverged |
| `update avail.` | yellow | New version of the tool available |
| `no upstream` | grey | No tracking branch configured |
| `not git` | grey | Tool installed from PyPI, not git |
| `unknown` | grey | Remote unreachable (offline, auth…) |
| `error` | bold red | Error during scan |

## Usage as a library

`projscan` is designed to be imported in a Textual app or any other async code.

```python
from pathlib import Path
from projscan import scan_paths, scan_tools, RepoInfo, ToolInfo

# Scan git repositories
repos: list[RepoInfo] = await scan_paths(
    [Path("~/code")],
    fetch=True,
    max_depth=3,
    concurrency=16,
)

for repo in repos:
    print(f"{repo.name}: {repo.status}")

# Scan uv tools
tools: list[ToolInfo] = await scan_tools(check_remote=True)

for tool in tools:
    if tool.status == "update avail.":
        print(f"Update available: {tool.name}")
```

Dataclasses expose an `.as_dict()` method for JSON serialization:

```python
import json
print(json.dumps([r.as_dict() for r in repos], ensure_ascii=False))
```

Full public API:

```python
from projscan import (
    # Git
    RepoInfo, scan_paths, scan_repo, find_repos,
    # UV tools
    ToolInfo, scan_tools, scan_tool, uv_tool_dir,
)
```

## Development

```bash
uv sync
uv run pytest           # 52 tests, ~5s
uv run projscan repos . # real-world test
uv run projscan tools   # real-world test
```
