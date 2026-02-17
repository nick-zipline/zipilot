"""Tests for zipilot.worktree module."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from zipilot.worktree import is_git_repo, setup_worktree, slugify_branch


# ---------------------------------------------------------------------------
# slugify_branch
# ---------------------------------------------------------------------------

class TestSlugifyBranch:
    def test_basic_goal(self):
        assert slugify_branch("Add health endpoint") == "zipilot/add-health-endpoint"

    def test_special_characters(self):
        assert slugify_branch("Fix bug #42 (urgent!)") == "zipilot/fix-bug-42-urgent"

    def test_truncation(self):
        long_goal = "a" * 100
        branch = slugify_branch(long_goal)
        # "zipilot/" prefix + max 50 chars
        assert len(branch) <= len("zipilot/") + 50

    def test_empty_after_strip(self):
        assert slugify_branch("!!!") == "zipilot/unnamed"

    def test_already_clean(self):
        assert slugify_branch("simple") == "zipilot/simple"


# ---------------------------------------------------------------------------
# is_git_repo
# ---------------------------------------------------------------------------

class TestIsGitRepo:
    def test_returns_true_for_git_repo(self):
        mock_result = MagicMock(returncode=0)
        with patch("zipilot.worktree._run_git", return_value=mock_result):
            assert is_git_repo("/some/repo") is True

    def test_returns_false_for_non_repo(self):
        mock_result = MagicMock(returncode=128)
        with patch("zipilot.worktree._run_git", return_value=mock_result):
            assert is_git_repo("/not/a/repo") is False

    def test_returns_false_on_exception(self):
        with patch("zipilot.worktree._run_git", side_effect=FileNotFoundError):
            assert is_git_repo("/no/git") is False

    def test_tilde_is_expanded(self):
        mock_result = MagicMock(returncode=0)
        with patch("zipilot.worktree._run_git", return_value=mock_result) as mock_git:
            is_git_repo("~/somepath")
            # The cwd kwarg should be the expanded home path, not ~/somepath
            cwd_used = mock_git.call_args[1]["cwd"]
            assert "~" not in cwd_used


# ---------------------------------------------------------------------------
# setup_worktree
# ---------------------------------------------------------------------------

def _make_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestSetupWorktree:
    @patch("zipilot.worktree.print_success")
    @patch("zipilot.worktree.print_phase")
    @patch("zipilot.worktree._run_git")
    def test_successful_creation(self, mock_git, _phase, _success):
        # fetch, verify origin/main, check branch doesn't exist, worktree add
        mock_git.side_effect = [
            _make_result(0),   # fetch origin main
            _make_result(0),   # rev-parse --verify origin/main
            _make_result(128), # rev-parse --verify zipilot/add-endpoint (doesn't exist)
            _make_result(0),   # worktree add
        ]

        path, err = setup_worktree("/tmp/repo", "Add endpoint")

        assert err == ""
        assert "repo--add-endpoint" in path
        # Verify worktree add was called with correct branch
        wt_call = mock_git.call_args_list[3]
        assert wt_call[0][0][0] == "worktree"
        assert "zipilot/add-endpoint" in wt_call[0][0]

    @patch("zipilot.worktree.print_warning")
    @patch("zipilot.worktree.print_phase")
    @patch("zipilot.worktree._run_git")
    def test_fetch_fails_but_continues(self, mock_git, _phase, _warn):
        mock_git.side_effect = [
            _make_result(1, stderr="fetch failed"),  # fetch fails
            _make_result(0),   # verify origin/main OK
            _make_result(128), # branch doesn't exist
            _make_result(0),   # worktree add
        ]

        path, err = setup_worktree("/tmp/repo", "Fix bug")

        assert err == ""
        assert path  # still succeeds

    @patch("zipilot.worktree.print_phase")
    @patch("zipilot.worktree._run_git")
    def test_origin_main_missing_returns_error(self, mock_git, _phase):
        mock_git.side_effect = [
            _make_result(0),   # fetch
            _make_result(128), # origin/main doesn't exist
        ]

        path, err = setup_worktree("/tmp/repo", "Do thing")

        assert path == ""
        assert "origin/main" in err

    @patch("zipilot.worktree.print_success")
    @patch("zipilot.worktree.print_phase")
    @patch("zipilot.worktree._run_git")
    def test_branch_exists_adds_suffix(self, mock_git, _phase, _success):
        mock_git.side_effect = [
            _make_result(0),   # fetch
            _make_result(0),   # verify origin/main
            _make_result(0),   # zipilot/fix-bug exists
            _make_result(128), # zipilot/fix-bug-2 doesn't exist
            _make_result(0),   # worktree add
        ]

        path, err = setup_worktree("/tmp/repo", "Fix bug")

        assert err == ""
        assert "fix-bug-2" in path
        wt_call = mock_git.call_args_list[4]
        assert "zipilot/fix-bug-2" in wt_call[0][0]

    @patch("zipilot.worktree.print_phase")
    @patch("zipilot.worktree._run_git")
    def test_all_suffixes_exhausted(self, mock_git, _phase):
        # fetch, verify, then all branch checks return 0 (exists)
        mock_git.side_effect = [
            _make_result(0),  # fetch
            _make_result(0),  # verify origin/main
        ] + [
            _make_result(0)   # branch exists — base, -2, -3, ..., -9
            for _ in range(9)
        ] + [
            _make_result(0),  # final check after loop also exists
        ]

        path, err = setup_worktree("/tmp/repo", "Fix bug")

        assert path == ""
        assert "taken" in err

    @patch("zipilot.worktree.print_phase")
    @patch("zipilot.worktree._run_git")
    def test_worktree_add_fails(self, mock_git, _phase):
        mock_git.side_effect = [
            _make_result(0),   # fetch
            _make_result(0),   # verify origin/main
            _make_result(128), # branch doesn't exist
            _make_result(1, stderr="lock exists"),  # worktree add fails
        ]

        path, err = setup_worktree("/tmp/repo", "Do thing")

        assert path == ""
        assert "worktree add failed" in err

    @patch("zipilot.worktree.print_success")
    @patch("zipilot.worktree.print_phase")
    @patch("zipilot.worktree._run_git")
    def test_path_is_sibling_to_repo(self, mock_git, _phase, _success):
        mock_git.side_effect = [
            _make_result(0),   # fetch
            _make_result(0),   # verify origin/main
            _make_result(128), # branch doesn't exist
            _make_result(0),   # worktree add
        ]

        path, err = setup_worktree("/home/user/github/cloud", "Add endpoint")

        assert err == ""
        # Worktree path should be sibling: <primary>--<slug>
        assert path.endswith("/cloud--add-endpoint")
