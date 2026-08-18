from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from control_plane.survey import RepositorySurvey, survey_payload, survey_repository


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        env={
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=10,
    )


def _repository(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    _git(repository, "init", "--quiet", "--initial-branch=main")
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repository, "add", "a.txt")
    _git(repository, "commit", "--quiet", "-m", "first")
    return repository


class RepositorySurveyTests(unittest.TestCase):
    def test_reports_clone_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            observed = survey_repository(repository, base="main")
            self.assertIsInstance(observed, RepositorySurvey)
            self.assertEqual(observed.branch, "main")
            self.assertEqual(len(observed.head), 40)
            self.assertEqual(observed.status, "PASS")

    def test_other_clones_is_always_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            payload = survey_payload(survey_repository(repository, base="main"))
            self.assertEqual(payload["other_clones"], "UNKNOWN")
            self.assertIs(payload["authorizes"], False)
            self.assertEqual(payload["kind"], "RepositorySurveyV1")

    def test_branch_comparison_is_by_content_not_commit_count(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "checkout", "--quiet", "-b", "feature")
            (repository / "a.txt").write_text("changed\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "second")
            _git(repository, "checkout", "--quiet", "main")
            _git(repository, "merge", "--quiet", "--squash", "feature")
            _git(repository, "commit", "--quiet", "-m", "squashed")
            observed = survey_repository(repository, base="main")
            feature = next(b for b in observed.branches if b.name == "feature")
            self.assertEqual(feature.only_in_branch, 0)
            self.assertTrue(feature.content_equivalent_to_base)

    def test_orphan_work_counts_stashes_and_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            (repository / "a.txt").write_text("dirty\n", encoding="utf-8")
            _git(repository, "stash", "push", "--quiet", "-m", "kept")
            (repository / "orphan.md").write_text("only here\n", encoding="utf-8")
            observed = survey_repository(repository, base="main")
            self.assertEqual(observed.stashes, 1)
            self.assertEqual(observed.untracked_total, 1)
            self.assertEqual(observed.status, "FAIL")

    def test_worktree_limit_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            observed = survey_repository(repository, base="main", max_worktrees=0)
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_LIMIT")

    def test_survey_does_not_mutate_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))

            def snapshot() -> list[tuple[str, int]]:
                return sorted(
                    (str(item.relative_to(repository)), item.lstat().st_mtime_ns)
                    for item in repository.rglob("*")
                    if item.is_file()
                )

            before = snapshot()
            survey_repository(repository, base="main")
            self.assertEqual(before, snapshot())

    def test_missing_base_is_unknown_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            observed = survey_repository(repository, base="origin/does-not-exist")
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_BASE_UNKNOWN")

    def test_linked_worktree_is_inventoried(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            linked = Path(raw) / "linked"
            _git(repository, "worktree", "add", "--quiet", str(linked), "-b", "side")
            observed = survey_repository(repository, base="main")
            names = {item.branch for item in observed.worktrees}
            self.assertIn("main", names)
            self.assertIn("side", names)


if __name__ == "__main__":
    unittest.main()
