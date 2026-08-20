from __future__ import annotations

import os
from pathlib import Path
import shlex
import stat
import tempfile
import unittest
from unittest.mock import patch

import adoption_enablement.repository as repository
import adoption_enablement.safe_io as safe_io
from adoption_enablement.repository import (
    _run_git,
    observe_source,
    observe_target,
)
from adoption_enablement.safe_io import read_confined_file
from tests.adoption_enablement_test_support import (
    git,
    initialize_fresh_target,
    initialize_full_source,
    initialize_source,
    metadata_snapshot,
    write_file,
)


class AdoptionRepositoryTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_confined_read_rejects_symlink_hardlink_fifo_and_unsafe_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(strict=True)
            regular = write_file(root, "regular.txt", b"safe")

            self.assertEqual(
                read_confined_file(root, "regular.txt", maximum=16),
                b"safe",
            )

            (root / "link.txt").symlink_to(regular)
            os.link(regular, root / "hardlink.txt")
            os.mkfifo(root / "fifo")
            unsafe = write_file(root, "unsafe.txt", b"unsafe")
            unsafe.chmod(0o666)

            for relative in ("link.txt", "regular.txt", "hardlink.txt", "fifo", "unsafe.txt"):
                with self.subTest(relative=relative):
                    with self.assertRaisesRegex(ValueError, "^E_ADOPTION_FILE"):
                        read_confined_file(root, relative, maximum=16)

    def test_confined_read_opens_the_leaf_nonblocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(strict=True)
            write_file(root, "regular.txt", b"safe")
            real_open = safe_io.os.open
            observed: list[int] = []

            def require_nonblocking_leaf(
                path: object,
                flags: int,
                *arguments: object,
                **keywords: object,
            ) -> int:
                if path == "regular.txt" and keywords.get("dir_fd") is not None:
                    observed.append(flags)
                    self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0))
                return real_open(path, flags, *arguments, **keywords)

            with patch.object(safe_io.os, "open", side_effect=require_nonblocking_leaf):
                self.assertEqual(
                    read_confined_file(root, "regular.txt", maximum=16),
                    b"safe",
                )

            self.assertEqual(len(observed), 1)

    def test_source_observation_is_closed_and_dirty_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = initialize_source(Path(directory) / "source")
            before = metadata_snapshot(source)
            hostile = {
                "GIT_DIR": str(Path(directory) / "attacker"),
                "GIT_WORK_TREE": str(Path(directory) / "attacker-worktree"),
                "GIT_CONFIG_GLOBAL": str(Path(directory) / "attacker-config"),
                "HOME": str(Path(directory) / "attacker-home"),
                "PATH": str(Path(directory) / "attacker-bin"),
            }
            with patch.dict(os.environ, hostile, clear=False):
                observation = observe_source(source)
            self.assertEqual(observation.product_version, "3.1.0-core.2")
            self.assertEqual(metadata_snapshot(source), before)

            write_file(source, "untracked.txt", "dirty")
            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_SOURCE_DIRTY"):
                observe_source(source)

    def test_fresh_target_is_observed_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", self.ROOT)
            target = initialize_fresh_target(container / "target")
            before = metadata_snapshot(target)

            observation = observe_target(target, authority_source=source)

            self.assertEqual(observation.branch, "codex/adoption-target")
            self.assertIsNone(observation.core_hooks_path_before)
            self.assertEqual(metadata_snapshot(target), before)

    def test_target_requires_canonical_policy_and_registry_validation(self) -> None:
        cases = (
            ("policy", ".codex/project-policy.toml", "\nunknown_authority = true\n"),
            ("registry", ".codex/resource-registry.toml", "\nunknown_authority = true\n"),
        )
        for name, relative, suffix in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                target = initialize_fresh_target(Path(directory) / "target")
                source = initialize_full_source(Path(directory) / "source", self.ROOT)
                path = target / relative
                path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
                git(target, "add", relative)
                git(target, "commit", "-m", f"invalid {name}")
                before = metadata_snapshot(target)

                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_POLICY"):
                    observe_target(target, authority_source=source)

                self.assertEqual(metadata_snapshot(target), before)

    def test_target_git_filter_is_rejected_before_status_can_execute_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            target = initialize_fresh_target(container / "target")
            source = initialize_full_source(container / "source", self.ROOT)
            write_file(target, ".gitattributes", "*.toml filter=reviewevil\n")
            git(target, "add", ".gitattributes")
            git(target, "commit", "-m", "declare filter attribute")

            marker = container / "filter-marker"
            executable = write_file(
                container,
                "filter-command",
                "#!/bin/sh\n"
                f": > {shlex.quote(str(marker))}\n"
                "cat\n",
                mode=0o755,
            )
            git(
                target,
                "config",
                "--local",
                "filter.reviewevil.clean",
                str(executable),
            )
            os.utime(target / ".codex" / "project-policy.toml", None)
            before = metadata_snapshot(target)

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_GIT_FILTER"):
                observe_target(target, authority_source=source)

            self.assertFalse(marker.exists())
            self.assertEqual(metadata_snapshot(target), before)

    def test_target_rejects_managed_path_hooks_state_and_second_worktree(self) -> None:
        cases = ("managed", "hooks", "state", "worktree")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                target = initialize_fresh_target(container / "target")
                source = initialize_full_source(container / "source", self.ROOT)
                if case == "managed":
                    write_file(target, ".codex/control-plane.lock", "installed")
                elif case == "hooks":
                    git(target, "config", "--local", "core.hooksPath", ".codex/git-hooks")
                elif case == "state":
                    (target / ".git" / "codex-control-plane-core").mkdir()
                else:
                    git(
                        target,
                        "worktree",
                        "add",
                        str(container / "second"),
                        "-b",
                        "codex/second",
                    )
                before = metadata_snapshot(target)
                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_(?:NOT_FRESH|TARGET_WORKTREES)"):
                    observe_target(target, authority_source=source)
                self.assertEqual(metadata_snapshot(target), before)

    def test_target_root_symlink_and_bare_repository_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            target = initialize_fresh_target(container / "target")
            source = initialize_full_source(container / "source", self.ROOT)
            alias = container / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_PATH"):
                observe_target(alias, authority_source=source)

            bare = container / "bare.git"
            bare.mkdir()
            git(bare, "init", "--bare")
            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_REPOSITORY"):
                observe_target(bare.resolve(strict=True), authority_source=source)

    def test_git_output_is_bounded_before_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = initialize_source(Path(directory) / "source")
            write_file(source, "large.bin", b"x" * 131_072)
            git(source, "add", "large.bin")
            git(source, "commit", "-m", "large")

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_GIT_OUTPUT"):
                _run_git(source, "show", "HEAD:large.bin", maximum=1024)

    def test_bare_repository_inside_managed_scope_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", self.ROOT)
            target = initialize_fresh_target(container / "target")
            nested = target / "scripts" / "nested.git"
            nested.mkdir(parents=True)
            git(nested, "init", "--bare")
            git(target, "add", "--all")
            git(target, "commit", "-m", "nested bare repository markers")
            before = metadata_snapshot(target)

            from adoption_enablement.manifest import preview

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_NESTED_REPOSITORY"):
                preview(source, target)

            self.assertEqual(before, metadata_snapshot(target))

    def test_git_markers_inside_managed_scope_are_rejected_without_mutation(self) -> None:
        cases = ("directory", "gitfile")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", self.ROOT)
                target = initialize_fresh_target(container / "target")
                marker = target / "scripts" / "work" / ".git"
                marker.parent.mkdir(parents=True)
                if case == "directory":
                    marker.mkdir()
                else:
                    marker.write_text("gitdir: /untrusted/nested.git\n", encoding="utf-8")
                before = metadata_snapshot(target)

                from adoption_enablement.manifest import preview

                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_NESTED_REPOSITORY"):
                    preview(source, target)

                self.assertEqual(before, metadata_snapshot(target))

    def test_gitlink_inside_managed_scope_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", self.ROOT)
            target = initialize_fresh_target(container / "target")
            head = git(target, "rev-parse", "HEAD").stdout.decode("ascii").strip()
            git(
                target,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{head},scripts/nested",
            )
            git(target, "commit", "-m", "managed gitlink")
            (target / "scripts" / "nested").mkdir(parents=True)
            self.assertEqual(git(target, "status", "--porcelain").stdout, b"")
            before = metadata_snapshot(target)

            from adoption_enablement.manifest import preview

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_NESTED_REPOSITORY"):
                preview(source, target)

            self.assertEqual(before, metadata_snapshot(target))

    def test_managed_repository_scan_depth_and_count_are_bounded_without_mutation(self) -> None:
        cases = ("depth", "count")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", self.ROOT)
                target = initialize_fresh_target(container / "target")
                scripts = target / "scripts"
                scripts.mkdir()
                if case == "depth":
                    current = scripts
                    for index in range(repository.MANAGED_SCAN_DEPTH_MAX):
                        current /= f"d{index:02d}"
                        current.mkdir()
                else:
                    for index in range(repository.MANAGED_SCAN_ENTRY_MAX + 1):
                        (scripts / f"e{index:04d}").mkdir()
                before = metadata_snapshot(target)

                from adoption_enablement.manifest import preview

                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_BOUNDS"):
                    preview(source, target)

                self.assertEqual(before, metadata_snapshot(target))


if __name__ == "__main__":
    unittest.main()
