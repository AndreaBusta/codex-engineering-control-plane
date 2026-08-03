from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "scripts" / "build-release-candidate"
WORKFLOW_URL = (
    "https://github.com/AndreaBusta/codex-engineering-control-plane/"
    "actions/runs/123456"
)


def _run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*arguments],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )


def _git(repository: Path, *arguments: str) -> str:
    completed = _run("git", *arguments, cwd=repository)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


class ReleaseCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.remote = self.root / "remote.git"
        self.repository.mkdir()
        _git(self.repository, "init", "-b", "main")
        _git(self.repository, "config", "user.name", "Release Tests")
        _git(
            self.repository,
            "config",
            "user.email",
            "release-tests@example.invalid",
        )
        (self.repository / ".codex").mkdir()
        (self.repository / "control_plane").mkdir()
        (self.repository / ".codex" / "control-plane.lock").write_text(
            'schema_version = 1\nproduct_version = "2.1.0"\n',
            encoding="utf-8",
        )
        (self.repository / "control_plane" / "__init__.py").write_text(
            '__version__ = "2.1.0"\n', encoding="utf-8"
        )
        (self.repository / "README.md").write_text(
            "release fixture\n", encoding="utf-8"
        )
        (self.repository / "nested").mkdir()
        (self.repository / "nested" / ".keep").write_text("keep\n", encoding="utf-8")
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "feat: fixture (#12)")
        _run("git", "init", "--bare", "--initial-branch=main", str(self.remote), cwd=self.root)
        _git(self.repository, "remote", "add", "origin", str(self.remote))
        _git(self.repository, "push", "-u", "origin", "main")
        _git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )

    def build(self, output: Path) -> subprocess.CompletedProcess[str]:
        return _run(
            sys.executable,
            str(BUILDER),
            "--repo",
            str(self.repository),
            "--output-dir",
            str(output),
            "--workflow-url",
            WORKFLOW_URL,
            cwd=self.repository,
        )

    def test_candidate_is_deterministic_and_binds_source_and_gates(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        head = _git(self.repository, "rev-parse", "HEAD")
        tree = _git(self.repository, "rev-parse", "HEAD^{tree}")
        replacement = _git(
            self.repository,
            "commit-tree",
            tree,
            "-m",
            "fake local replacement (#999)",
        )
        _git(self.repository, "replace", head, replacement)

        _git(self.repository, "config", "tar.umask", "0002")
        first_run = self.build(first)
        _git(self.repository, "config", "tar.umask", "0077")
        second_run = self.build(second)

        self.assertEqual(first_run.returncode, 0, first_run.stderr)
        self.assertEqual(second_run.returncode, 0, second_run.stderr)
        self.assertEqual(json.loads(first_run.stdout), json.loads(second_run.stdout))
        first_files = sorted(path.name for path in first.iterdir())
        self.assertEqual(
            first_files,
            [
                "SHA256SUMS",
                "codex-engineering-control-plane-2.1.0.manifest.json",
                "codex-engineering-control-plane-2.1.0.receipt.json",
                "codex-engineering-control-plane-2.1.0.tar.gz",
            ],
        )
        for name in first_files:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

        archive = first / "codex-engineering-control-plane-2.1.0.tar.gz"
        manifest = json.loads(
            (first / "codex-engineering-control-plane-2.1.0.manifest.json").read_text(
                encoding="utf-8"
            )
        )
        receipt = json.loads(
            (first / "codex-engineering-control-plane-2.1.0.receipt.json").read_text(
                encoding="utf-8"
            )
        )
        expected_commit = _git(self.repository, "rev-parse", "HEAD")
        expected_tree = _git(self.repository, "rev-parse", "HEAD^{tree}")
        expected_digest = f"sha256:{sha256(archive.read_bytes()).hexdigest()}"

        self.assertEqual(manifest["release_tag"], "v2.1.0")
        self.assertEqual(manifest["source"]["commit"], expected_commit)
        self.assertEqual(manifest["source"]["tree"], expected_tree)
        self.assertEqual(manifest["source"]["pull_requests"], [
            {
                "number": 12,
                "url": "https://github.com/AndreaBusta/codex-engineering-control-plane/pull/12",
            }
        ])
        self.assertEqual(manifest["workflow"]["url"], WORKFLOW_URL)
        self.assertEqual(
            manifest["gates"],
            [
                {
                    "name": "adoption-matrix",
                    "result": "pending_external_evidence",
                },
                {"name": "macos-smoke", "result": "pending_external_evidence"},
                {
                    "name": "release-preflight",
                    "result": "pending_external_evidence",
                },
                {"name": "verify", "result": "pending_external_evidence"},
            ],
        )
        self.assertFalse(manifest["authorizes"])
        self.assertEqual(manifest["artifacts"][0]["sha256"], expected_digest)
        self.assertEqual(receipt["status"], "candidate")
        self.assertEqual(receipt["artifact"]["archive_sha256"], expected_digest)
        self.assertEqual(receipt["verification"]["external_state"], "pending_external_evidence")
        self.assertEqual(
            receipt["verification"]["smoke_result"],
            "pending_external_evidence",
        )
        self.assertFalse(receipt["approvals"]["release_authorized"])

        checksum_line = (first / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertEqual(checksum_line, f"{expected_digest.removeprefix('sha256:')}  {archive.name}\n")
        with tarfile.open(archive, mode="r:gz") as packaged:
            names = packaged.getnames()
        self.assertTrue(names)
        self.assertTrue(
            all(
                name == "codex-engineering-control-plane-2.1.0"
                or name.startswith("codex-engineering-control-plane-2.1.0/")
                for name in names
            )
        )
        self.assertIn(
            "codex-engineering-control-plane-2.1.0/control_plane/__init__.py",
            names,
        )

    def test_candidate_fails_closed_on_version_or_source_drift(self) -> None:
        (self.repository / "control_plane" / "__init__.py").write_text(
            '__version__ = "1.0.0"\n', encoding="utf-8"
        )
        _git(self.repository, "add", "control_plane/__init__.py")
        _git(self.repository, "commit", "-m", "test: mismatched version")
        _git(self.repository, "remote", "set-url", "origin", str(self.remote))
        _git(self.repository, "push", "origin", "main")
        _git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )
        mismatch = self.build(self.root / "mismatch")
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("E_RELEASE_VERSION", mismatch.stderr)

        _git(self.repository, "switch", "-c", "codex/not-main")
        wrong_branch = self.build(self.root / "wrong-branch")
        self.assertNotEqual(wrong_branch.returncode, 0)
        self.assertIn("E_RELEASE_SOURCE", wrong_branch.stderr)

    def test_candidate_refuses_nonempty_or_symlink_output(self) -> None:
        nonempty = self.root / "nonempty"
        nonempty.mkdir()
        (nonempty / "owned.txt").write_text("keep\n", encoding="utf-8")
        refused = self.build(nonempty)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("E_RELEASE_OUTPUT", refused.stderr)
        self.assertEqual((nonempty / "owned.txt").read_text(encoding="utf-8"), "keep\n")

        linked = self.root / "linked"
        linked.symlink_to(self.root / "outside", target_is_directory=True)
        refused_link = self.build(linked)
        self.assertNotEqual(refused_link.returncode, 0)
        self.assertIn("E_RELEASE_OUTPUT", refused_link.stderr)

        ancestor_link = self.root / "repository-link"
        ancestor_link.symlink_to(self.repository, target_is_directory=True)
        escaped = self.build(ancestor_link / "nested" / "generated")
        self.assertNotEqual(escaped.returncode, 0)
        self.assertIn("E_RELEASE_OUTPUT", escaped.stderr)
        self.assertFalse((self.repository / "nested" / "generated").exists())

        shared_parent = self.root / "shared-parent"
        shared_parent.mkdir()
        shared_parent.chmod(0o777)
        refused_shared = self.build(shared_parent / "candidate")
        self.assertNotEqual(refused_shared.returncode, 0)
        self.assertIn("E_RELEASE_OUTPUT", refused_shared.stderr)
        self.assertFalse((shared_parent / "candidate").exists())

    def test_output_inode_swap_fails_without_deleting_replacement(self) -> None:
        namespace = runpy.run_path(
            str(BUILDER),
            run_name="release_candidate_test_module",
        )
        write_candidate = namespace["_write_candidate"]
        release_error = namespace["ReleaseCandidateError"]
        output = self.root / "raced-output"
        real_open = os.open
        raced = False

        def racing_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal raced
            if (
                not raced
                and path == output.name
                and dir_fd is not None
                and flags & getattr(os, "O_DIRECTORY", 0)
            ):
                raced = True
                os.rename(
                    output.name,
                    f"{output.name}.original",
                    src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd,
                )
                os.mkdir(output.name, 0o700, dir_fd=dir_fd)
                replacement = real_open(
                    output.name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    dir_fd=dir_fd,
                )
                try:
                    attacker = real_open(
                        "attacker-owned.txt",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=replacement,
                    )
                    os.close(attacker)
                finally:
                    os.close(replacement)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with patch.object(os, "open", side_effect=racing_open):
            with self.assertRaisesRegex(release_error, "E_RELEASE_OUTPUT"):
                write_candidate(output, {"artifact.bin": b"candidate"})

        self.assertTrue(raced)
        self.assertTrue((output / "attacker-owned.txt").is_file())
        self.assertFalse((output / "artifact.bin").exists())


if __name__ == "__main__":
    unittest.main()
