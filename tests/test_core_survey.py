from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
    return repository.resolve()


class RepositorySurveyTests(unittest.TestCase):
    def test_survey_uses_only_the_public_bounded_git_runner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            with patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("unbounded subprocess.run was used"),
            ):
                observed = survey_repository(repository, base="main")

        self.assertEqual(observed.status, "PASS")

    def test_git_state_guard_stops_before_any_git_observation(self) -> None:
        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            with (
                patch.object(
                    survey_module,
                    "inspect_git_state_materialization",
                    return_value=SimpleNamespace(ok=False),
                ),
                patch.object(
                    survey_module,
                    "run_bounded_git",
                    side_effect=AssertionError("Git ran before its state was safe"),
                ) as bounded_git,
            ):
                observed = survey_repository(repository, base="main")

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")
        bounded_git.assert_not_called()

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

    def test_branch_that_only_modifies_is_not_content_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "checkout", "--quiet", "-b", "only-modifies")
            (repository / "a.txt").write_text("real unmerged work\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "modify without adding")
            _git(repository, "checkout", "--quiet", "main")
            observed = survey_repository(repository, base="main")
            branch = next(b for b in observed.branches if b.name == "only-modifies")
            self.assertFalse(
                branch.content_equivalent_to_base,
                "a branch with unmerged modifications must never read as equivalent",
            )

    def test_branch_that_only_deletes_is_not_content_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "checkout", "--quiet", "-b", "only-deletes")
            (repository / "a.txt").unlink()
            _git(repository, "commit", "--quiet", "-am", "delete without adding")
            _git(repository, "checkout", "--quiet", "main")
            observed = survey_repository(repository, base="main")
            branch = next(b for b in observed.branches if b.name == "only-deletes")
            self.assertFalse(branch.content_equivalent_to_base)

    def test_unobservable_worktree_status_is_unknown_not_clean(self) -> None:
        from unittest.mock import patch

        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            real_text = survey_module._text

            def failing_text(repo, arguments):
                if arguments and arguments[0] == "status":
                    return None
                return real_text(repo, arguments)

            with patch.object(survey_module, "_text", failing_text):
                observed = survey_repository(repository, base="main")
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_unobservable_stash_list_is_unknown_not_zero(self) -> None:
        from unittest.mock import patch

        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            real_text = survey_module._text

            def failing_text(repo, arguments):
                if arguments and arguments[0] == "stash":
                    return None
                return real_text(repo, arguments)

            with patch.object(survey_module, "_text", failing_text):
                observed = survey_repository(repository, base="main")
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

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

    def test_limits_above_governing_maximum_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            for keyword in ("max_worktrees", "max_branches"):
                with self.subTest(keyword=keyword), self.assertRaisesRegex(
                    ValueError,
                    "^E_SURVEY_LIMIT",
                ):
                    survey_repository(
                        repository,
                        base="main",
                        **{keyword: 65},
                    )

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

    def test_linked_worktree_clean_filter_is_rejected_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = _repository(root)
            linked = root / "linked"
            _git(repository, "worktree", "add", "--quiet", str(linked), "-b", "side")
            (linked / ".gitattributes").write_text(
                "victim.txt filter=survey-hostile\n", encoding="utf-8"
            )
            (linked / "victim.txt").write_text("safe\n", encoding="utf-8")
            _git(linked, "add", ".gitattributes", "victim.txt")
            _git(linked, "commit", "--quiet", "-m", "linked fixture")
            marker = root / "clean-filter-executed"
            helper = root / "clean-filter.sh"
            helper.write_text(
                f"#!/bin/sh\n: > {shlex.quote(str(marker))}\ncat\n",
                encoding="utf-8",
            )
            helper.chmod(0o700)
            _git(
                repository,
                "config",
                "filter.survey-hostile.clean",
                str(helper),
            )
            _git(
                repository,
                "config",
                "filter.survey-hostile.required",
                "true",
            )
            (linked / "victim.txt").write_text("evil\n", encoding="utf-8")
            marker.unlink(missing_ok=True)

            observed = survey_repository(repository, base="main")

            self.assertFalse(marker.exists(), "survey executed a linked clean filter")
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_newline_worktree_path_is_parsed_losslessly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = _repository(root)
            linked = root / "linked\nline"
            _git(
                repository,
                "worktree",
                "add",
                "--quiet",
                str(linked),
                "-b",
                "newline-side",
            )

            observed = survey_repository(repository, base="main")
            observed_from_linked = survey_repository(linked.resolve(), base="main")

            self.assertEqual(observed.status, "PASS")
            self.assertEqual(observed_from_linked.status, "PASS")
            self.assertIn(
                str(linked.resolve()),
                {item.path for item in observed.worktrees},
            )

    def test_registered_worktree_substituted_by_another_repo_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = _repository(root)
            linked = root / "linked"
            _git(repository, "worktree", "add", "--quiet", str(linked), "-b", "side")
            displaced = root / "registered-worktree"
            linked.rename(displaced)
            linked.mkdir()
            _git(linked, "init", "--quiet", "--initial-branch=other")
            (linked / "other.txt").write_text("other repository\n", encoding="utf-8")
            _git(linked, "add", "other.txt")
            _git(linked, "commit", "--quiet", "-m", "other")

            observed = survey_repository(repository, base="main")

            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_swapped_worktrees_from_same_clone_are_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = _repository(root)
            first = root / "first"
            second = root / "second"
            _git(repository, "worktree", "add", "--quiet", str(first), "-b", "first")
            _git(repository, "worktree", "add", "--quiet", str(second), "-b", "second")
            holding = root / "holding"
            first.rename(holding)
            second.rename(first)
            holding.rename(second)

            observed = survey_repository(repository, base="main")

            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_swapped_detached_worktrees_at_same_head_are_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = _repository(root)
            first = root / "first-detached"
            second = root / "second-detached"
            _git(repository, "worktree", "add", "--quiet", "--detach", str(first), "HEAD")
            _git(repository, "worktree", "add", "--quiet", "--detach", str(second), "HEAD")
            holding = root / "holding-detached"
            first.rename(holding)
            second.rename(first)
            holding.rename(second)

            observed = survey_repository(repository, base="main")

            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_worktree_binding_drift_after_status_is_unknown(self) -> None:
        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            (repository / "a.txt").write_text("second\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "second")
            real_text = survey_module._text
            drifted = False

            def drifting_text(repo, arguments):
                nonlocal drifted
                value = real_text(repo, arguments)
                if arguments and arguments[0] == "status" and not drifted:
                    drifted = True
                    _git(repo, "checkout", "--quiet", "--detach", "HEAD^")
                return value

            with patch.object(survey_module, "_text", side_effect=drifting_text):
                observed = survey_repository(repository, base="main")

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_status_bytes_must_be_stable_not_only_their_counts(self) -> None:
        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            real_text = survey_module._text
            observations = iter(("?? first\0", "?? second\0"))

            def changing_status(repo, arguments):
                if arguments and arguments[0] == "status":
                    return next(observations, "?? second\0")
                return real_text(repo, arguments)

            with patch.object(survey_module, "_text", side_effect=changing_status):
                observed = survey_repository(repository, base="main")

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_filter_added_after_first_status_is_rejected_before_reobservation(
        self,
    ) -> None:
        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = _repository(root)
            marker = root / "racing-filter-executed"
            helper = root / "racing-filter.sh"
            helper.write_text(
                f"#!/bin/sh\n: > {shlex.quote(str(marker))}\ncat\n",
                encoding="utf-8",
            )
            helper.chmod(0o700)
            _git(repository, "config", "filter.racing.clean", str(helper))
            _git(repository, "config", "filter.racing.required", "true")
            real_text = survey_module._text
            status_seen = False

            def racing_text(repo, arguments):
                nonlocal status_seen
                value = real_text(repo, arguments)
                if arguments and arguments[0] == "status" and not status_seen:
                    status_seen = True
                    (repo / ".gitattributes").write_text(
                        "a.txt filter=racing\n", encoding="utf-8"
                    )
                return value

            with patch.object(survey_module, "_text", side_effect=racing_text):
                observed = survey_repository(repository, base="main")

        self.assertFalse(marker.exists())
        self.assertEqual(observed.status, "UNKNOWN")
        self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_branch_ref_drift_after_initial_snapshot_is_unknown(self) -> None:
        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            real_text = survey_module._text
            drifted = False

            def drifting_text(repo, arguments):
                nonlocal drifted
                value = real_text(repo, arguments)
                if arguments == ("stash", "list") and not drifted:
                    drifted = True
                    _git(repo, "branch", "concurrent-ref", "HEAD")
                return value

            with patch.object(survey_module, "_text", side_effect=drifting_text):
                observed = survey_repository(repository, base="main")

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_stash_drift_after_initial_snapshot_is_unknown(self) -> None:
        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            real_text = survey_module._text
            drifted = False

            def drifting_text(repo, arguments):
                nonlocal drifted
                value = real_text(repo, arguments)
                if arguments == ("stash", "list") and not drifted:
                    drifted = True
                    (repo / "concurrent.txt").write_text(
                        "concurrent\n", encoding="utf-8"
                    )
                    _git(repo, "stash", "push", "--quiet", "--include-untracked")
                return value

            with patch.object(survey_module, "_text", side_effect=drifting_text):
                observed = survey_repository(repository, base="main")

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

    def test_branch_diffs_use_frozen_oids_and_an_explicit_separator(self) -> None:
        from control_plane import survey as survey_module

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "checkout", "--quiet", "-b", "feature")
            (repository / "a.txt").write_text("feature\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "feature")
            _git(repository, "checkout", "--quiet", "main")
            real_text = survey_module._text
            diff_arguments = []

            def recording_text(repo, arguments):
                if arguments and arguments[0] == "diff":
                    diff_arguments.append(arguments)
                return real_text(repo, arguments)

            with patch.object(survey_module, "_text", side_effect=recording_text):
                observed = survey_repository(repository, base="main")

        self.assertEqual(observed.status, "PASS")
        self.assertTrue(diff_arguments)
        for arguments in diff_arguments:
            self.assertEqual(arguments[-1], "--")
            revision = arguments[-2]
            left, separator, right = revision.partition("..")
            self.assertEqual(separator, "..")
            self.assertIn(len(left), {40, 64})
            self.assertIn(len(right), {40, 64})
            int(left, 16)
            int(right, 16)

    def test_gitlink_is_unknown_before_nested_filter_can_execute(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repository = _repository(root)
            nested_parent = root / "nested-parent"
            nested_parent.mkdir()
            nested = _repository(nested_parent)
            _git(
                repository,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "--quiet",
                str(nested),
                "vendor",
            )
            _git(repository, "commit", "--quiet", "-m", "add gitlink")
            vendor = repository / "vendor"
            marker = root / "nested-filter-executed"
            helper = root / "nested-filter.sh"
            helper.write_text(
                f"#!/bin/sh\n: > {shlex.quote(str(marker))}\ncat\n",
                encoding="utf-8",
            )
            helper.chmod(0o700)
            (vendor / ".gitattributes").write_text(
                "victim.txt filter=nested-hostile\n", encoding="utf-8"
            )
            (vendor / "victim.txt").write_text("safe\n", encoding="utf-8")
            _git(vendor, "add", ".gitattributes", "victim.txt")
            _git(vendor, "commit", "--quiet", "-m", "nested filter fixture")
            _git(vendor, "config", "filter.nested-hostile.clean", str(helper))
            _git(vendor, "config", "filter.nested-hostile.required", "true")
            (vendor / "victim.txt").write_text("evil\n", encoding="utf-8")
            marker.unlink(missing_ok=True)

            observed = survey_repository(repository, base="main")

            self.assertFalse(marker.exists())
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")


if __name__ == "__main__":
    unittest.main()
