"""Where a run came from: the git commit it was produced at."""

from __future__ import annotations

import subprocess


def git_commit() -> str | None:
    """Short HEAD sha, suffixed ``-dirty`` when tracked files have uncommitted changes."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"], capture_output=True, timeout=5, check=False
        ).returncode
    except OSError, subprocess.SubprocessError:
        return None
    if not head:
        return None
    return f"{head}-dirty" if dirty == 1 else head
