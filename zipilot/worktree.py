"""Git worktree helpers for isolating spec work on a fresh branch."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from zipilot.console import print_phase, print_success, print_warning


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a git command with a 30-second timeout."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def is_git_repo(directory: str) -> bool:
    """Return True if *directory* is inside a git repository."""
    try:
        expanded = str(Path(directory).expanduser())
        result = _run_git(["rev-parse", "--git-dir"], cwd=expanded)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def slugify_branch(goal: str) -> str:
    """Turn a goal string into a branch name like ``zipilot/add-health-endpoint``."""
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")
    slug = slug[:50].rstrip("-")
    return f"zipilot/{slug or 'unnamed'}"


def setup_worktree(primary_dir: str, goal: str) -> tuple[str, str]:
    """Create a git worktree off ``origin/main`` for *goal*.

    Returns ``(worktree_path, error)``.  On success *error* is empty.
    On failure *worktree_path* is empty.
    """
    primary = str(Path(primary_dir).resolve())

    # Fetch latest main (warn but continue on failure)
    print_phase("Setting up git worktree...")
    try:
        fetch = _run_git(["fetch", "origin", "main"], cwd=primary)
        if fetch.returncode != 0:
            print_warning(f"git fetch origin main failed: {fetch.stderr.strip()}")
            print_warning("Trying local origin/main.")
    except subprocess.TimeoutExpired:
        print_warning("git fetch timed out. Trying local origin/main.")

    # Verify origin/main exists
    verify = _run_git(["rev-parse", "--verify", "origin/main"], cwd=primary)
    if verify.returncode != 0:
        return "", "origin/main not found. Is this repo cloned from a remote?"

    # Compute branch name, appending suffix if needed
    base_branch = slugify_branch(goal)
    branch = base_branch
    for suffix in range(2, 10):
        check = _run_git(["rev-parse", "--verify", branch], cwd=primary)
        if check.returncode != 0:
            break  # branch doesn't exist — use it
        branch = f"{base_branch}-{suffix}"
    else:
        # All suffixes 2-9 were taken (and the loop exhausted without break)
        check = _run_git(["rev-parse", "--verify", branch], cwd=primary)
        if check.returncode == 0:
            return "", f"All branch names {base_branch}(-2..-9) are taken."

    # Compute worktree path as sibling directory
    slug_part = branch.removeprefix("zipilot/")
    worktree_path = f"{primary}--{slug_part}"

    # Create worktree
    result = _run_git(
        ["worktree", "add", "-b", branch, worktree_path, "origin/main"],
        cwd=primary,
    )
    if result.returncode != 0:
        return "", f"git worktree add failed: {result.stderr.strip()}"

    print_success(f"Worktree: {worktree_path}")
    print_success(f"Branch:   {branch}")
    return worktree_path, ""
