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

from control_plane.release_source import (
    commit_object_identity,
    decode_commit_object,
    git_tree_oid,
)


ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "scripts" / "build-release-candidate"
WORKFLOW_URL = (
    "https://github.com/AndreaBusta/codex-engineering-control-plane/"
    "actions/runs/123456/attempts/1"
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
            'schema_version = 1\nproduct_version = "2.1.1"\n',
            encoding="utf-8",
        )
        (self.repository / "control_plane" / "__init__.py").write_text(
            '__version__ = "2.1.1"\n', encoding="utf-8"
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

    def build(
        self,
        output: Path,
        *,
        workflow_evidence: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            sys.executable,
            str(BUILDER),
            "--repo",
            str(self.repository),
            "--output-dir",
            str(output),
            "--workflow-url",
            WORKFLOW_URL,
        ]
        if workflow_evidence is not None:
            arguments.extend(["--workflow-evidence", str(workflow_evidence)])
        return _run(*arguments, cwd=self.repository)

    def workflow_evidence(self, path: Path) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "source": {
                "repository": (
                    "https://github.com/AndreaBusta/"
                    "codex-engineering-control-plane"
                ),
                "commit": _git(self.repository, "rev-parse", "HEAD"),
                "tree": _git(self.repository, "rev-parse", "HEAD^{tree}"),
            },
            "workflow": {
                "event": "workflow_dispatch",
                "run_attempt": 1,
                "run_id": 123456,
                "url": WORKFLOW_URL,
            },
            "gates": [
                {"name": name, "result": "success"}
                for name in (
                    "adoption-matrix",
                    "macos-smoke",
                    "release-preflight",
                    "verify",
                )
            ],
            "authorizes": False,
        }
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return payload

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
                "codex-engineering-control-plane-2.1.1.manifest.json",
                "codex-engineering-control-plane-2.1.1.receipt.json",
                "codex-engineering-control-plane-2.1.1.tar.gz",
            ],
        )
        for name in first_files:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

        archive = first / "codex-engineering-control-plane-2.1.1.tar.gz"
        manifest = json.loads(
            (first / "codex-engineering-control-plane-2.1.1.manifest.json").read_text(
                encoding="utf-8"
            )
        )
        receipt = json.loads(
            (first / "codex-engineering-control-plane-2.1.1.receipt.json").read_text(
                encoding="utf-8"
            )
        )
        expected_commit = _git(self.repository, "rev-parse", "HEAD")
        expected_tree = _git(self.repository, "rev-parse", "HEAD^{tree}")
        expected_digest = f"sha256:{sha256(archive.read_bytes()).hexdigest()}"

        self.assertEqual(manifest["release_tag"], "v2.1.1")
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
            marker_member = packaged.getmember(
                "codex-engineering-control-plane-2.1.1/"
                ".codex/release-source.json"
            )
            marker_stream = packaged.extractfile(marker_member)
            self.assertIsNotNone(marker_stream)
            marker = json.loads(marker_stream.read())
        self.assertTrue(names)
        self.assertTrue(
            all(
                name == "codex-engineering-control-plane-2.1.1"
                or name.startswith("codex-engineering-control-plane-2.1.1/")
                for name in names
            )
        )
        self.assertIn(
            "codex-engineering-control-plane-2.1.1/control_plane/__init__.py",
            names,
        )
        self.assertEqual(marker["schema_version"], 1)
        self.assertEqual(marker["source_kind"], "control-plane-release-tree")
        self.assertEqual(marker["product_version"], "2.1.1")
        self.assertEqual(marker["release_tag"], "v2.1.1")
        self.assertEqual(marker["source_commit"], expected_commit)
        self.assertEqual(marker["source_tree"], expected_tree)
        self.assertEqual(marker["source_object_format"], "sha1")
        observed_commit, commit_tree = commit_object_identity(
            decode_commit_object(marker["source_commit_object_base64"])
        )
        self.assertEqual(observed_commit, expected_commit)
        self.assertEqual(commit_tree, expected_tree)
        self.assertEqual(git_tree_oid(marker["entries"]), expected_tree)
        self.assertEqual(
            [entry["path"] for entry in marker["entries"]],
            [
                ".codex/control-plane.lock",
                "README.md",
                "control_plane/__init__.py",
                "nested/.keep",
            ],
        )
        self.assertNotIn(
            ".codex/release-source.json",
            [entry["path"] for entry in marker["entries"]],
        )
        self.assertTrue(
            all(len(entry["git_oid"]) == 40 for entry in marker["entries"])
        )

    def test_workflow_evidence_records_real_gates_without_authorizing_release(
        self,
    ) -> None:
        evidence = self.root / "workflow-evidence.json"
        self.workflow_evidence(evidence)
        output = self.root / "workflow-output"
        namespace = runpy.run_path(
            str(BUILDER),
            run_name="release_candidate_observed_workflow_test_module",
        )
        build_candidate = namespace["build_candidate"]
        observed: list[tuple[str, int, int, str, str, str]] = []

        def observe_workflow(
            slug: str,
            run_id: int,
            run_attempt: int,
            commit: str,
            tree: str,
            workflow_url: str,
        ) -> list[dict[str, str]]:
            observed.append(
                (slug, run_id, run_attempt, commit, tree, workflow_url)
            )
            return [
                {"name": name, "result": "success"}
                for name in (
                    "adoption-matrix",
                    "macos-smoke",
                    "release-preflight",
                    "verify",
                )
            ]

        build_candidate.__globals__["_observe_github_workflow"] = observe_workflow

        result = build_candidate(
            self.repository,
            output,
            WORKFLOW_URL,
            evidence,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            observed,
            [
                (
                    "AndreaBusta/codex-engineering-control-plane",
                    123456,
                    1,
                    _git(self.repository, "rev-parse", "HEAD"),
                    _git(self.repository, "rev-parse", "HEAD^{tree}"),
                    WORKFLOW_URL,
                )
            ],
        )
        manifest = json.loads(
            (
                output
                / "codex-engineering-control-plane-2.1.1.manifest.json"
            ).read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (
                output
                / "codex-engineering-control-plane-2.1.1.receipt.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["gates"],
            [
                {"name": name, "result": "success"}
                for name in (
                    "adoption-matrix",
                    "macos-smoke",
                    "release-preflight",
                    "verify",
                )
            ],
        )
        self.assertEqual(manifest["workflow"]["evidence"], "workflow_api_observed")
        self.assertFalse(manifest["authorizes"])
        self.assertEqual(receipt["status"], "verified_candidate")
        self.assertEqual(
            receipt["verification"]["external_state"], "workflow_api_observed"
        )
        self.assertEqual(receipt["verification"]["smoke_result"], "success")
        self.assertFalse(receipt["approvals"]["release_authorized"])

    def test_local_self_attested_workflow_evidence_is_rejected(self) -> None:
        evidence = self.root / "self-attested-evidence.json"
        self.workflow_evidence(evidence)

        result = self.build(
            self.root / "self-attested-output",
            workflow_evidence=evidence,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_RELEASE_EVIDENCE_OBSERVATION", result.stderr)

    def test_github_api_observation_binds_attempt_source_jobs_and_steps(
        self,
    ) -> None:
        namespace = runpy.run_path(
            str(BUILDER),
            run_name="release_candidate_github_observation_test_module",
        )
        observe = namespace["_observe_github_workflow"]
        head = _git(self.repository, "rev-parse", "HEAD")
        tree = _git(self.repository, "rev-parse", "HEAD^{tree}")
        run = {
            "id": 123456,
            "run_attempt": 1,
            "event": "workflow_dispatch",
            "head_sha": head,
            "path": ".github/workflows/control-plane.yml",
            "repository": {
                "full_name": "AndreaBusta/codex-engineering-control-plane"
            },
            "head_commit": {"id": head, "tree_id": tree},
        }
        jobs = {
            "total_count": 3,
            "jobs": [
                {
                    "name": "verify",
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "macos-smoke",
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "release-candidate",
                    "head_sha": head,
                    "status": "in_progress",
                    "conclusion": None,
                    "steps": [
                        {
                            "name": "Refresh release gate against private main",
                            "status": "completed",
                            "conclusion": "success",
                        },
                        {
                            "name": "Run supported adoption matrix",
                            "status": "completed",
                            "conclusion": "success",
                        },
                    ],
                },
            ],
        }

        def github_json(url: str) -> dict[str, object]:
            return jobs if url.endswith("/jobs?per_page=100") else run

        observe.__globals__["_github_json"] = github_json
        environment = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_JOB": "release-candidate",
            "GITHUB_REPOSITORY": (
                "AndreaBusta/codex-engineering-control-plane"
            ),
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "123456",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_SHA": head,
        }
        with patch.dict(os.environ, environment, clear=False):
            observed = observe(
                "AndreaBusta/codex-engineering-control-plane",
                123456,
                1,
                head,
                tree,
                WORKFLOW_URL,
            )

        self.assertEqual(
            observed,
            [
                {"name": name, "result": "success"}
                for name in (
                    "adoption-matrix",
                    "macos-smoke",
                    "release-preflight",
                    "verify",
                )
            ],
        )

        jobs["jobs"][2]["steps"][1]["conclusion"] = "failure"
        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(
                namespace["ReleaseCandidateError"],
                "E_RELEASE_EVIDENCE_OBSERVATION",
            ):
                observe(
                    "AndreaBusta/codex-engineering-control-plane",
                    123456,
                    1,
                    head,
                    tree,
                    WORKFLOW_URL,
                )

    def test_workflow_evidence_is_closed_and_exactly_source_bound(self) -> None:
        base_path = self.root / "workflow-evidence-base.json"
        base = self.workflow_evidence(base_path)
        cases: list[dict[str, object]] = []
        cases.append({**base, "unexpected": True})
        cases.append(
            {
                **base,
                "source": {**dict(base["source"]), "commit": "0" * 40},
            }
        )
        cases.append(
            {
                **base,
                "gates": [
                    *list(base["gates"])[:-1],
                    {"name": "verify", "result": "failure"},
                ],
            }
        )
        cases.append({**base, "authorizes": True})

        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                evidence = self.root / f"invalid-evidence-{index}.json"
                evidence.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                result = self.build(
                    self.root / f"invalid-output-{index}",
                    workflow_evidence=evidence,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("E_RELEASE_EVIDENCE", result.stderr)

    def test_candidate_packages_install_skill_with_committed_bytes(self) -> None:
        skill = ROOT / "skills" / "install-control-plane" / "SKILL.md"
        fixture_skill = self.repository / "skills" / "install-control-plane" / "SKILL.md"
        fixture_skill.parent.mkdir(parents=True)
        fixture_skill.write_bytes(skill.read_bytes())
        _git(self.repository, "add", fixture_skill.relative_to(self.repository).as_posix())
        _git(self.repository, "commit", "-m", "feat: add install skill (#13)")
        _git(self.repository, "remote", "set-url", "origin", str(self.remote))
        _git(self.repository, "push", "origin", "main")
        _git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )
        output = self.root / "skill-output"

        result = self.build(output)

        self.assertEqual(result.returncode, 0, result.stderr)
        archive = output / "codex-engineering-control-plane-2.1.1.tar.gz"
        with tarfile.open(archive, mode="r:gz") as packaged:
            member = packaged.extractfile(
                "codex-engineering-control-plane-2.1.1/"
                "skills/install-control-plane/SKILL.md"
            )
            self.assertIsNotNone(member)
            assert member is not None
            self.assertEqual(member.read(), skill.read_bytes())

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

    def test_candidate_enforces_the_consumer_entry_limit(self) -> None:
        namespace = runpy.run_path(
            str(BUILDER),
            run_name="release_candidate_entry_limit_test_module",
        )
        release_entries = namespace["_release_source_entries"]
        release_error = namespace["ReleaseCandidateError"]
        release_entries.__globals__["RELEASE_SOURCE_MAX_ENTRIES"] = 2

        with self.assertRaisesRegex(release_error, "E_RELEASE_SOURCE_LIMIT"):
            release_entries(
                self.repository,
                _git(self.repository, "rev-parse", "HEAD"),
            )

    def test_candidate_bounds_version_metadata_before_parsing(self) -> None:
        namespace = runpy.run_path(
            str(BUILDER),
            run_name="release_candidate_metadata_limit_test_module",
        )
        committed_text = namespace["_committed_text"]
        release_error = namespace["ReleaseCandidateError"]
        committed_text.__globals__["RELEASE_SOURCE_MAX_FILE_BYTES"] = 1

        with self.assertRaisesRegex(release_error, "E_RELEASE_SOURCE_LIMIT"):
            committed_text(
                self.repository,
                _git(self.repository, "rev-parse", "HEAD"),
                "README.md",
            )

    def test_candidate_rejects_oversized_release_source_blob(self) -> None:
        oversized = self.repository / "oversized.bin"
        with oversized.open("wb") as handle:
            handle.truncate(8 * 1024 * 1024 + 1)
        _git(self.repository, "add", oversized.name)
        _git(self.repository, "commit", "-m", "test: oversized source")
        _git(self.repository, "remote", "set-url", "origin", str(self.remote))
        _git(self.repository, "push", "origin", "main")
        _git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )

        result = self.build(self.root / "oversized-output")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_RELEASE_SOURCE_LIMIT", result.stderr)

    def test_candidate_rejects_reserved_marker_descendants(self) -> None:
        collision = (
            self.repository
            / ".codex"
            / "release-source.json"
            / "child.txt"
        )
        collision.parent.mkdir()
        collision.write_text("collision\n", encoding="utf-8")
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "test: marker descendant")
        _git(self.repository, "remote", "set-url", "origin", str(self.remote))
        _git(self.repository, "push", "origin", "main")
        _git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )

        result = self.build(self.root / "marker-descendant-output")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_RELEASE_SOURCE_PATH", result.stderr)

    def test_candidate_rejects_casefold_marker_collisions(self) -> None:
        collision = self.repository / ".codex" / "RELEASE-SOURCE.JSON"
        collision.write_text("collision\n", encoding="utf-8")
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "test: marker casefold collision")
        _git(self.repository, "remote", "set-url", "origin", str(self.remote))
        _git(self.repository, "push", "origin", "main")
        _git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )

        result = self.build(self.root / "marker-casefold-output")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_RELEASE_SOURCE_PATH", result.stderr)

    def test_candidate_packages_exact_git_blobs_despite_export_attributes(self) -> None:
        (self.repository / ".gitattributes").write_text(
            "README.md export-ignore\n"
            "nested/.keep export-subst\n",
            encoding="utf-8",
        )
        literal = b"release $Format:%H$\n"
        (self.repository / "nested" / ".keep").write_bytes(literal)
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "test: export attributes")
        _git(self.repository, "remote", "set-url", "origin", str(self.remote))
        _git(self.repository, "push", "origin", "main")
        _git(
            self.repository,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )

        output = self.root / "export-attributes-output"
        result = self.build(output)

        self.assertEqual(result.returncode, 0, result.stderr)
        archive = output / "codex-engineering-control-plane-2.1.1.tar.gz"
        with tarfile.open(archive, mode="r:gz") as packaged:
            readme = packaged.extractfile(
                "codex-engineering-control-plane-2.1.1/README.md"
            )
            substituted = packaged.extractfile(
                "codex-engineering-control-plane-2.1.1/nested/.keep"
            )
            self.assertIsNotNone(readme)
            self.assertIsNotNone(substituted)
            self.assertEqual(readme.read(), b"release fixture\n")
            self.assertEqual(substituted.read(), literal)

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
