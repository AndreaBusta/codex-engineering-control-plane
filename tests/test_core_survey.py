from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from control_plane import survey as survey_module
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


def _git_output(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
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
        text=True,
        check=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _repository(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    _git(repository, "init", "--quiet", "--initial-branch=main")
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repository, "add", "a.txt")
    _git(repository, "commit", "--quiet", "-m", "first")
    _git(repository, "remote", "add", "origin", "https://example.invalid/repository.git")
    return repository


def _git_path(repository: Path, *arguments: str) -> Path:
    path = Path(_git_output(repository, *arguments))
    return path if path.is_absolute() else (repository / path).resolve()


def _optional_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _tree_bytes(root: Path) -> tuple[tuple[str, bytes], ...]:
    if not root.is_dir():
        return ()
    return tuple(
        (str(path.relative_to(root)), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _repository_snapshot(repository: Path) -> dict[str, object]:
    """Read refs/index/worktree/stash/config without changing the fixture."""

    common = _git_path(repository, "rev-parse", "--git-common-dir")
    git_dir = _git_path(repository, "rev-parse", "--git-dir")
    worktree = tuple(
        (str(path.relative_to(repository)), path.read_bytes())
        for path in sorted(repository.rglob("*"))
        if path.is_file() and path.relative_to(repository).parts[0] != ".git"
    )
    return {
        "refs": (
            _tree_bytes(common / "refs"),
            _optional_bytes(common / "packed-refs"),
            _optional_bytes(git_dir / "HEAD"),
        ),
        "index": _optional_bytes(git_dir / "index"),
        "worktree": worktree,
        "stash": _git_output(
            repository,
            "for-each-ref",
            "--format=%(refname)%00%(objectname)",
            "refs/stash",
        ),
        "config": _optional_bytes(common / "config"),
    }


def _assert_unknown_without_mutation(
    test: unittest.TestCase,
    repository: Path,
    observe,
    error_code: str,
) -> RepositorySurvey:
    before = _repository_snapshot(repository)
    observed = observe()
    after = _repository_snapshot(repository)
    test.assertEqual(observed.status, "UNKNOWN")
    test.assertEqual(observed.error_code, error_code)
    test.assertEqual(before, after)
    payload = survey_payload(observed)
    test.assertIsNone(payload["worktrees"])
    test.assertIsNone(payload["branches"])
    test.assertEqual(
        payload["orphan_work"],
        {
            "stashes": None,
            "untracked_total": None,
            "unpublished_unique_branches": None,
        },
    )
    return observed


def _survey_arguments(argv, repository: Path) -> tuple[str, ...]:
    marker = str(repository.resolve())
    try:
        offset = list(argv).index(marker)
    except ValueError:
        return ()
    return tuple(argv[offset + 1 :])


def _is_local_inventory(arguments: tuple[str, ...]) -> bool:
    return (
        bool(arguments)
        and arguments[0] == "for-each-ref"
        and arguments[-1:] == ("refs/heads/",)
    )


def _is_reachability(arguments: tuple[str, ...]) -> bool:
    return _is_local_inventory(arguments) and any(
        argument.startswith("--merged=") for argument in arguments
    )


def _is_remote_inventory(arguments: tuple[str, ...]) -> bool:
    return bool(arguments) and arguments[0] == "for-each-ref" and any(
        argument.startswith("[r]efs/remotes/") for argument in arguments
    )


def _with_timeout_fault(
    repository: Path,
    predicate,
    *,
    occurrence: int = 1,
    **survey_options,
) -> RepositorySurvey:
    real_run = subprocess.run
    matched = 0

    def timing_out_run(argv, *args, **kwargs):
        nonlocal matched
        arguments = _survey_arguments(argv, repository)
        if predicate(arguments):
            matched += 1
            if matched == occurrence:
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        return real_run(argv, *args, **kwargs)

    with patch.object(survey_module.subprocess, "run", side_effect=timing_out_run):
        return survey_repository(repository, base="main", **survey_options)


def _with_git_bytes_fault(
    repository: Path,
    predicate,
    payload: bytes,
    *,
    occurrence: int = 1,
    **survey_options,
) -> RepositorySurvey:
    real_git = survey_module._git
    matched = 0

    def faulty_git(repo, arguments, *args, **kwargs):
        nonlocal matched
        if predicate(arguments):
            matched += 1
            if matched == occurrence:
                return payload
        return real_git(repo, arguments, *args, **kwargs)

    with patch.object(survey_module, "_git", faulty_git):
        return survey_repository(repository, base="main", **survey_options)


def _with_text_fault(
    repository: Path,
    predicate,
    payload: str | None,
    *,
    occurrence: int = 1,
    **survey_options,
) -> RepositorySurvey:
    real_text = survey_module._text
    matched = 0

    def faulty_text(repo, arguments, *args, **kwargs):
        nonlocal matched
        if predicate(arguments):
            matched += 1
            if matched == occurrence:
                return payload
        return real_text(repo, arguments, *args, **kwargs)

    with patch.object(survey_module, "_text", faulty_text):
        return survey_repository(repository, base="main", **survey_options)


def _unique_branch(repository: Path, name: str = "feature") -> tuple[str, str]:
    _git(repository, "switch", "-c", name)
    (repository / "a.txt").write_text(f"{name}\n", encoding="utf-8")
    _git(repository, "commit", "--quiet", "-am", name)
    head = _git_output(repository, "rev-parse", "HEAD")
    tree = _git_output(repository, "rev-parse", "HEAD^{tree}")
    _git(repository, "switch", "main")
    return head, tree


def _local_row(ref: str, head: str, tree: str, kind: str = "commit") -> str:
    return f"{ref}\0{head}\0{kind}\0{tree}"


def _remote_row(ref: str, head: str, kind: str = "commit") -> str:
    return f"{ref}\0{head}\0{kind}"


class RepositorySurveyTests(unittest.TestCase):
    def test_survey_and_git_guards_do_not_import_each_other(self) -> None:
        runtime = Path(__file__).parents[1] / "control_plane"
        checks = (
            (runtime / "survey.py", "git_guards"),
            (runtime / "git_guards.py", "survey"),
        )

        for path, forbidden in checks:
            with self.subTest(source=path.name, forbidden=forbidden):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imports: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        prefix = "." * node.level + (node.module or "")
                        imports.add(prefix)
                        separator = "" if prefix.endswith(".") else "."
                        imports.update(
                            f"{prefix}{separator}{alias.name}"
                            for alias in node.names
                        )
                forbidden_imports = (
                    forbidden,
                    f".{forbidden}",
                    f"control_plane.{forbidden}",
                )
                self.assertFalse(
                    any(
                        imported == target
                        or imported.startswith(f"{target}.")
                        for imported in imports
                        for target in forbidden_imports
                    ),
                    f"{path.name} imports {forbidden}",
                )

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
            self.assertEqual(payload["kind"], "RepositorySurveyV2")

    def test_default_payload_is_repository_survey_v2(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            payload = survey_payload(survey_repository(repository, base="main"))
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["kind"], "RepositorySurveyV2")
            self.assertEqual(payload["comparison"]["base_ref"], "main")
            self.assertEqual(payload["comparison"]["remote_name"], "origin")
            self.assertNotIn("only_in_branch", str(payload))

    def test_git_and_text_accept_explicit_float_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            timeouts: list[float] = []

            def timing_out_run(argv, *args, **kwargs):
                timeouts.append(kwargs["timeout"])
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

            with patch.object(
                survey_module.subprocess, "run", side_effect=timing_out_run
            ):
                self.assertIsNone(
                    survey_module._git(repository, ("version",), timeout=0.125)
                )
                self.assertIsNone(
                    survey_module._text(repository, ("version",), timeout=0.25)
                )
            self.assertEqual(timeouts, [0.125, 0.25])

    def test_add_only_diff_failure_keeps_normative_unpublished_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            base_head = _git_output(repository, "rev-parse", "main")
            _git(repository, "switch", "-c", "feature")
            (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
            _git(repository, "add", "feature.txt")
            _git(repository, "commit", "--quiet", "-m", "add feature")
            feature_head = _git_output(repository, "rev-parse", "HEAD")
            _git(repository, "switch", "main")
            real_git = survey_module._git
            diff_calls: list[tuple[str, ...]] = []

            def failing_diff(repo, arguments, *args, **kwargs):
                if arguments[:3] == ("diff", "--diff-filter=A", "--name-only"):
                    diff_calls.append(arguments)
                    return None
                return real_git(repo, arguments, *args, **kwargs)

            with patch.object(survey_module, "_git", side_effect=failing_diff):
                observed = survey_repository(repository, base="main")

            feature = next(item for item in observed.branches if item.name == "feature")
            self.assertIsNone(feature.added_paths)
            self.assertTrue(feature.unpublished_unique)
            self.assertEqual(observed.status, "FAIL")
            self.assertIsNone(observed.error_code)
            self.assertEqual(
                diff_calls,
                [
                    (
                        "diff",
                        "--diff-filter=A",
                        "--name-only",
                        f"{base_head}..{feature_head}",
                    )
                ],
            )

    def test_added_paths_share_one_deadline_and_stop_after_optional_failure(
        self,
    ) -> None:
        helper = getattr(survey_module, "_optional_added_paths", None)
        self.assertIsNotNone(helper, "optional added-path enrichment seam must exist")
        assert helper is not None
        base_head = "a" * 40
        branches = (
            survey_module.BranchObservation(
                "first", "b" * 40, None, False, True, False, True
            ),
            survey_module.BranchObservation(
                "second", "c" * 40, None, True, False, True, False
            ),
            survey_module.BranchObservation(
                "third", "d" * 40, None, False, False, False, False
            ),
        )
        ticks = iter((100.0, 100.0, 109.75))
        clock_calls: list[float] = []
        diff_calls: list[tuple[tuple[str, ...], float]] = []

        def clock() -> float:
            value = next(ticks)
            clock_calls.append(value)
            return value

        def optional_text(repo, arguments, *, timeout):
            diff_calls.append((arguments, timeout))
            return "one.txt\n\ntwo.txt" if len(diff_calls) == 1 else None

        with patch.object(survey_module, "_text", side_effect=optional_text):
            enriched = helper(Path("/repo"), base_head, branches, clock=clock)

        self.assertEqual(clock_calls, [100.0, 100.0, 109.75])
        self.assertEqual([item.added_paths for item in enriched], [2, None, None])
        self.assertEqual(
            diff_calls,
            [
                (
                    (
                        "diff",
                        "--diff-filter=A",
                        "--name-only",
                        f"{base_head}..{'b' * 40}",
                    ),
                    10.0,
                ),
                (
                    (
                        "diff",
                        "--diff-filter=A",
                        "--name-only",
                        f"{base_head}..{'c' * 40}",
                    ),
                    0.25,
                ),
            ],
        )
        normative = lambda item: (
            item.name,
            item.head,
            item.content_equivalent_to_base,
            item.has_unique_commits,
            item.remote_tracking_ref_present,
            item.unpublished_unique,
        )
        self.assertEqual(
            [normative(item) for item in enriched],
            [normative(item) for item in branches],
        )

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
            self.assertEqual(feature.added_paths, 0)
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

    def test_modified_unique_branch_without_remote_ref_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "switch", "-c", "feature")
            (repository / "a.txt").write_text("unique\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "unique")
            feature_head = _git_output(repository, "rev-parse", "HEAD")
            _git(repository, "switch", "main")

            observed = survey_repository(repository, base="main")
            branch = next(item for item in observed.branches if item.name == "feature")

            self.assertEqual(branch.head, feature_head)
            self.assertFalse(branch.content_equivalent_to_base)
            self.assertTrue(branch.has_unique_commits)
            self.assertFalse(branch.remote_tracking_ref_present)
            self.assertTrue(branch.unpublished_unique)
            self.assertEqual(observed.unpublished_unique_branches, 1)
            self.assertEqual(observed.status, "FAIL")

    def test_untracked_without_unpublished_branch_is_warn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            (repository / "orphan.md").write_text("local\n", encoding="utf-8")
            observed = survey_repository(repository, base="main")
            self.assertEqual(observed.unpublished_unique_branches, 0)
            self.assertEqual(observed.untracked_total, 1)
            self.assertEqual(observed.status, "WARN")

    def test_unpublished_branch_precedes_local_residue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "switch", "-c", "feature")
            (repository / "a.txt").write_text("unique\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "unique")
            _git(repository, "switch", "main")
            (repository / "orphan.md").write_text("local\n", encoding="utf-8")
            self.assertEqual(survey_repository(repository, base="main").status, "FAIL")

    def test_homonymous_remote_ref_exempts_even_when_behind(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            base_head = _git_output(repository, "rev-parse", "main")
            _git(repository, "switch", "-c", "feature")
            (repository / "a.txt").write_text("unique\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "unique")
            feature_head = _git_output(repository, "rev-parse", "HEAD")
            _git(repository, "update-ref", "refs/remotes/origin/feature", base_head)
            _git(repository, "switch", "main")
            branch = next(
                item for item in survey_repository(repository, base="main").branches
                if item.name == "feature"
            )
            self.assertNotEqual(feature_head, base_head)
            self.assertTrue(branch.has_unique_commits)
            self.assertTrue(branch.remote_tracking_ref_present)
            self.assertFalse(branch.unpublished_unique)

    def test_tree_equivalent_after_squash_is_not_unpublished(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "switch", "-c", "feature")
            (repository / "a.txt").write_text("changed\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "feature")
            _git(repository, "switch", "main")
            _git(repository, "merge", "--quiet", "--squash", "feature")
            _git(repository, "commit", "--quiet", "-m", "squashed")
            branch = next(
                item for item in survey_repository(repository, base="main").branches
                if item.name == "feature"
            )
            self.assertTrue(branch.content_equivalent_to_base)
            self.assertTrue(branch.has_unique_commits)
            self.assertFalse(branch.unpublished_unique)

    def test_behind_branch_with_different_tree_has_no_unique_commits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            _git(repository, "branch", "behind")
            (repository / "a.txt").write_text("main advanced\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "advance main")
            branch = next(
                item for item in survey_repository(repository, base="main").branches
                if item.name == "behind"
            )
            self.assertFalse(branch.content_equivalent_to_base)
            self.assertFalse(branch.has_unique_commits)
            self.assertFalse(branch.unpublished_unique)

    def test_shared_unique_head_counts_only_branch_without_homonymous_remote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            feature_head, _tree = _unique_branch(repository, "published")
            _git(repository, "branch", "unpublished", feature_head)
            _git(
                repository,
                "update-ref",
                "refs/remotes/origin/published",
                feature_head,
            )

            observed = survey_repository(repository, base="main")
            branches = {branch.name: branch for branch in observed.branches}

            self.assertEqual(branches["published"].head, feature_head)
            self.assertEqual(branches["unpublished"].head, feature_head)
            self.assertFalse(branches["published"].unpublished_unique)
            self.assertTrue(branches["unpublished"].unpublished_unique)
            self.assertEqual(observed.unpublished_unique_branches, 1)
            self.assertEqual(observed.status, "FAIL")

    def test_zero_local_refs_without_residue_is_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            base_head = _git_output(repository, "rev-parse", "HEAD")
            _git(repository, "switch", "--detach", base_head)
            _git(repository, "branch", "-D", "main")

            observed = survey_repository(repository, base=base_head)

            self.assertEqual(observed.branches, ())
            self.assertEqual(observed.stashes, 0)
            self.assertEqual(observed.untracked_total, 0)
            self.assertEqual(observed.unpublished_unique_branches, 0)
            self.assertEqual(observed.status, "PASS")

    def test_missing_or_invalid_remote_is_remote_unknown_without_mutation(self) -> None:
        for scenario in ("missing", "invalid"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                if scenario == "missing":
                    _git(repository, "remote", "remove", "origin")

                    def observe():
                        return survey_repository(repository, base="main")

                else:
                    real_text = survey_module._text

                    def invalid_remote_text(repo, arguments, *args, **kwargs):
                        if arguments == ("remote",):
                            return "invalid remote"
                        return real_text(repo, arguments, *args, **kwargs)

                    def observe():
                        with patch.object(
                            survey_module, "_text", invalid_remote_text
                        ):
                            return survey_repository(
                                repository,
                                base="main",
                                remote_name="invalid remote",
                            )

                _assert_unknown_without_mutation(
                    self, repository, observe, "E_SURVEY_REMOTE_UNKNOWN"
                )

    def test_remote_inventory_timeout_decode_or_structure_is_remote_unknown(self) -> None:
        for fault in ("timeout", "decode", "structure"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                _unique_branch(repository)
                if fault == "timeout":
                    observe = lambda: _with_timeout_fault(
                        repository, _is_remote_inventory
                    )
                elif fault == "decode":
                    observe = lambda: _with_git_bytes_fault(
                        repository, _is_remote_inventory, b"\xff"
                    )
                else:
                    observe = lambda: _with_text_fault(
                        repository, _is_remote_inventory, "malformed"
                    )
                _assert_unknown_without_mutation(
                    self, repository, observe, "E_SURVEY_REMOTE_UNKNOWN"
                )

    def test_duplicate_or_unexpected_remote_row_is_remote_unknown(self) -> None:
        for fault in ("duplicate", "unexpected"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                feature_head, _tree = _unique_branch(repository)
                row = _remote_row(
                    "refs/remotes/origin/feature" if fault == "duplicate"
                    else "refs/remotes/origin/unexpected",
                    feature_head,
                )
                listing = f"{row}\n{row}" if fault == "duplicate" else row
                _assert_unknown_without_mutation(
                    self,
                    repository,
                    lambda: _with_text_fault(
                        repository, _is_remote_inventory, listing
                    ),
                    "E_SURVEY_REMOTE_UNKNOWN",
                )

    def test_remote_count_overflow_is_remote_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            feature_head, _tree = _unique_branch(repository)
            row = _remote_row("refs/remotes/origin/feature", feature_head)
            _assert_unknown_without_mutation(
                self,
                repository,
                lambda: _with_text_fault(
                    repository,
                    _is_remote_inventory,
                    "\n".join((row, row, row)),
                    max_branches=2,
                ),
                "E_SURVEY_REMOTE_UNKNOWN",
            )

    def test_remote_non_commit_row_is_remote_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            feature_head, _tree = _unique_branch(repository)
            listing = _remote_row(
                "refs/remotes/origin/feature", feature_head, kind="tag"
            )
            _assert_unknown_without_mutation(
                self,
                repository,
                lambda: _with_text_fault(
                    repository, _is_remote_inventory, listing
                ),
                "E_SURVEY_REMOTE_UNKNOWN",
            )

    def test_local_inventory_reachability_and_postinventory_faults_are_unknown(
        self,
    ) -> None:
        phases = {
            "inventory": (
                lambda arguments: _is_local_inventory(arguments)
                and not _is_reachability(arguments),
                1,
            ),
            "reachability": (_is_reachability, 1),
            "postinventory": (
                lambda arguments: _is_local_inventory(arguments)
                and not _is_reachability(arguments),
                2,
            ),
        }
        for phase, (predicate, occurrence) in phases.items():
            for fault in ("timeout", "decode"):
                with (
                    self.subTest(phase=phase, fault=fault),
                    tempfile.TemporaryDirectory() as raw,
                ):
                    repository = _repository(Path(raw))
                    _unique_branch(repository)
                    if fault == "timeout":
                        observe = lambda: _with_timeout_fault(
                            repository, predicate, occurrence=occurrence
                        )
                    else:
                        observe = lambda: _with_git_bytes_fault(
                            repository,
                            predicate,
                            b"\xff",
                            occurrence=occurrence,
                        )
                    _assert_unknown_without_mutation(
                        self, repository, observe, "E_SURVEY_INVENTORY"
                    )

    def test_invalid_or_duplicate_local_row_is_inventory_unknown(self) -> None:
        for fault in ("invalid", "duplicate"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                head = _git_output(repository, "rev-parse", "main")
                tree = _git_output(repository, "rev-parse", "main^{tree}")
                row = _local_row("refs/heads/main", head, tree)
                listing = "malformed" if fault == "invalid" else f"{row}\n{row}"
                predicate = lambda arguments: (
                    _is_local_inventory(arguments)
                    and not _is_reachability(arguments)
                )
                _assert_unknown_without_mutation(
                    self,
                    repository,
                    lambda: _with_text_fault(
                        repository, predicate, listing, occurrence=1
                    ),
                    "E_SURVEY_INVENTORY",
                )

    def test_local_identity_mismatch_or_postinventory_drift_is_unknown(self) -> None:
        for phase in ("reachability", "postinventory"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                head = _git_output(repository, "rev-parse", "main")
                tree = _git_output(repository, "rev-parse", "main^{tree}")
                changed = _local_row(
                    "refs/heads/main",
                    "1" * 40 if phase == "reachability" else head,
                    "2" * 40 if phase == "postinventory" else tree,
                )
                predicate = (
                    _is_reachability
                    if phase == "reachability"
                    else lambda arguments: (
                        _is_local_inventory(arguments)
                        and not _is_reachability(arguments)
                    )
                )
                occurrence = 1 if phase == "reachability" else 2
                _assert_unknown_without_mutation(
                    self,
                    repository,
                    lambda: _with_text_fault(
                        repository, predicate, changed, occurrence=occurrence
                    ),
                    "E_SURVEY_INVENTORY",
                )

    def test_shallow_or_ambiguous_shallow_observation_is_inventory_unknown(self) -> None:
        for scenario in ("shallow", "ambiguous", "failure"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                shallow = _git_path(repository, "rev-parse", "--git-path", "shallow")
                if scenario == "shallow":
                    shallow.write_text(
                        _git_output(repository, "rev-parse", "HEAD") + "\n",
                        encoding="ascii",
                    )
                    observe = lambda: survey_repository(repository, base="main")
                else:
                    predicate = lambda arguments: arguments == (
                        "rev-parse",
                        "--is-shallow-repository",
                    )
                    payload = "ambiguous" if scenario == "ambiguous" else None
                    observe = lambda: _with_text_fault(
                        repository, predicate, payload
                    )
                _assert_unknown_without_mutation(
                    self, repository, observe, "E_SURVEY_INVENTORY"
                )

    def test_branch_and_worktree_limits_are_survey_limit_unknown(self) -> None:
        for inventory in ("branch", "worktree"):
            with self.subTest(inventory=inventory), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                if inventory == "branch":
                    _git(repository, "branch", "side")
                    observe = lambda: survey_repository(
                        repository, base="main", max_branches=1
                    )
                else:
                    observe = lambda: survey_repository(
                        repository, base="main", max_worktrees=0
                    )
                _assert_unknown_without_mutation(
                    self, repository, observe, "E_SURVEY_LIMIT"
                )

    def test_base_ref_mutation_keeps_fixed_initial_base_oid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            initial_base = _git_output(repository, "rev-parse", "main")
            _git(repository, "branch", "comparison-base", initial_base)
            (repository / "a.txt").write_text("next\n", encoding="utf-8")
            _git(repository, "commit", "--quiet", "-am", "next")
            moved_base = _git_output(repository, "rev-parse", "main")
            calls: list[tuple[str, ...]] = []
            real_text = survey_module._text
            mutated = False

            def mutating_text(repo, arguments, *args, **kwargs):
                nonlocal mutated
                calls.append(arguments)
                result = real_text(repo, arguments, *args, **kwargs)
                if arguments == (
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    "comparison-base^{commit}",
                ):
                    self.assertFalse(mutated)
                    self.assertEqual(result, initial_base)
                    _git(
                        repository,
                        "update-ref",
                        "refs/heads/comparison-base",
                        moved_base,
                    )
                    mutated = True
                return result

            with patch.object(survey_module, "_text", mutating_text):
                observed = survey_repository(repository, base="comparison-base")

            self.assertTrue(mutated)
            self.assertEqual(observed.base_head, initial_base)
            base_resolution = (
                "rev-parse",
                "--verify",
                "--quiet",
                "comparison-base^{commit}",
            )
            fixed_tree = (
                "rev-parse",
                "--verify",
                "--quiet",
                f"{initial_base}^{{tree}}",
            )
            self.assertEqual(calls.count(base_resolution), 1)
            self.assertIn(fixed_tree, calls)
            self.assertLess(calls.index(base_resolution), calls.index(fixed_tree))
            self.assertNotIn(
                ("rev-parse", "--verify", "--quiet", "comparison-base^{tree}"),
                calls,
            )
            merged_arguments = [
                argument
                for call in calls
                for argument in call
                if argument.startswith("--merged=")
            ]
            self.assertEqual(merged_arguments, [f"--merged={initial_base}"])
            self.assertNotIn("--merged=comparison-base", merged_arguments)

    def test_branch_ref_drift_around_merged_observation_is_unknown(self) -> None:
        from unittest.mock import patch

        from control_plane import survey as survey_module

        for phase in ("before_reachability", "before_postvalidation"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as raw:
                repository = _repository(Path(raw))
                _git(repository, "switch", "-c", "feature")
                (repository / "a.txt").write_text("unique\n", encoding="utf-8")
                _git(repository, "commit", "--quiet", "-am", "unique")
                _git(repository, "switch", "main")
                main_head = _git_output(repository, "rev-parse", "main")
                real_text = survey_module._text
                moved = False

                def drifting_text(repo, arguments):
                    nonlocal moved
                    is_merged = (
                        arguments
                        and arguments[0] == "for-each-ref"
                        and any(
                            argument.startswith("--merged=")
                            for argument in arguments
                        )
                    )
                    if not is_merged or moved:
                        return real_text(repo, arguments)
                    if phase == "before_reachability":
                        _git(
                            repository,
                            "update-ref",
                            "refs/heads/feature",
                            main_head,
                        )
                    result = real_text(repo, arguments)
                    if phase == "before_postvalidation":
                        _git(
                            repository,
                            "update-ref",
                            "refs/heads/feature",
                            main_head,
                        )
                    moved = True
                    return result

                with patch.object(survey_module, "_text", drifting_text):
                    observed = survey_repository(repository, base="main")

                self.assertEqual(observed.status, "UNKNOWN")
                self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")

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

    def test_stash_without_unpublished_branch_is_warn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            (repository / "a.txt").write_text("dirty\n", encoding="utf-8")
            _git(repository, "stash", "push", "--quiet", "-m", "kept")
            observed = survey_repository(repository, base="main")
            self.assertEqual(observed.stashes, 1)
            self.assertEqual(observed.untracked_total, 0)
            self.assertEqual(observed.unpublished_unique_branches, 0)
            self.assertEqual(observed.status, "WARN")

    def test_worktree_limit_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            observed = survey_repository(repository, base="main", max_worktrees=0)
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertEqual(observed.error_code, "E_SURVEY_LIMIT")

    def test_survey_does_not_mutate_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            before = _repository_snapshot(repository)
            survey_repository(repository, base="main")
            self.assertEqual(before, _repository_snapshot(repository))

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

    def test_unborn_linked_worktree_head_is_inventory_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            linked = Path(raw) / "unborn"
            _git(repository, "worktree", "add", "--quiet", "--orphan", str(linked))

            _assert_unknown_without_mutation(
                self,
                repository,
                lambda: survey_repository(repository, base="main"),
                "E_SURVEY_INVENTORY",
            )


if __name__ == "__main__":
    unittest.main()
