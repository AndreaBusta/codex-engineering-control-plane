from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from adoption_enablement.contracts import validate_journal, validate_plan, validate_receipt
from adoption_enablement.transaction import status
from control_plane.contracts import load_active_adoption_journal
from tests.adoption_enablement_test_support import (
    git,
    initialize_full_source,
    initialize_governed_target,
)


ROOT = Path(__file__).resolve().parents[1]
ADOPTION = ROOT / "scripts" / "control-plane-adoption"


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    records: list[tuple[str, str, int, str]] = []
    for current, directories, files in os.walk(root, followlinks=False):
        if Path(current) == root:
            directories[:] = [name for name in directories if name != ".git"]
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            metadata = path.lstat()
            records.append((path.relative_to(root).as_posix(), "directory", stat.S_IMODE(metadata.st_mode), ""))
        for name in files:
            path = current_path / name
            metadata = path.lstat()
            records.append(
                (
                    path.relative_to(root).as_posix(),
                    "file",
                    stat.S_IMODE(metadata.st_mode),
                    "sha256:" + sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(records)


def _run(
    executable: Path,
    *arguments: str,
    cwd: Path,
    input_payload: str | None = None,
) -> tuple[int, dict[str, object], str]:
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=cwd,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        input=input_payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"command returned non-JSON rc={completed.returncode}: "
            f"stdout={completed.stdout[:2048]!r}, stderr={completed.stderr[:2048]!r}"
        ) from error
    return completed.returncode, payload, completed.stderr


def _task_records(target: Path) -> tuple[Path, ...]:
    state = target / ".git" / "codex-control-plane-core"
    paths: list[Path] = []
    for relative in ("tasks", "leases", "lease-release-receipts", "locks"):
        root = state / relative
        if root.exists():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    return tuple(sorted(paths))


class AdoptionEndToEndTests(unittest.TestCase):
    def test_full_temporary_repository_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_governed_target(container / "target", ROOT)
            before = _tree_snapshot(target)
            plan_file = container / "reviewed-plan.json"

            preview_code, plan, preview_error = _run(
                ADOPTION,
                "preview",
                "--source",
                str(source),
                "--target",
                str(target),
                "--json",
                cwd=ROOT,
            )
            self.assertEqual(preview_code, 0, preview_error)
            self.assertEqual(validate_plan(plan), ())
            self.assertEqual(before, _tree_snapshot(target))
            plan_file.write_text(
                json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            plan_file.chmod(0o600)

            apply_code, apply_receipt, apply_error = _run(
                ADOPTION,
                "apply",
                "--source",
                str(source),
                "--target",
                str(target),
                "--plan",
                str(plan_file),
                "--plan-digest",
                str(plan["plan_digest"]),
                "--json",
                cwd=ROOT,
            )
            self.assertEqual(apply_code, 0, apply_error)
            self.assertEqual(validate_receipt(apply_receipt), ())
            state = target / ".git" / "codex-control-plane-core"
            verification_lock = state / "locks" / "verification.lock"
            verification_metadata = verification_lock.lstat()
            verification_identity = (
                verification_metadata.st_dev,
                verification_metadata.st_ino,
            )
            journal_path = state / "adoption" / "journal.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual(validate_journal(journal), ())
            self.assertEqual(
                load_active_adoption_journal(journal_path.read_bytes()),
                journal,
            )
            locks_metadata = (state / "locks").lstat()
            self.assertEqual(
                journal["verification_lock"],
                {
                    "directory": {
                        "path": "codex-control-plane-core/locks",
                        "device": int(locks_metadata.st_dev),
                        "inode": int(locks_metadata.st_ino),
                        "mode": stat.S_IMODE(locks_metadata.st_mode),
                        "uid": int(locks_metadata.st_uid),
                        "gid": int(locks_metadata.st_gid),
                        "flags": int(getattr(locks_metadata, "st_flags", 0)),
                    },
                    "file": {
                        "path": "codex-control-plane-core/locks/verification.lock",
                        "device": int(verification_metadata.st_dev),
                        "inode": int(verification_metadata.st_ino),
                        "mode": stat.S_IMODE(verification_metadata.st_mode),
                        "links": int(verification_metadata.st_nlink),
                        "uid": int(verification_metadata.st_uid),
                        "gid": int(verification_metadata.st_gid),
                        "size": int(verification_metadata.st_size),
                        "mtime_ns": int(verification_metadata.st_mtime_ns),
                        "ctime_ns": int(verification_metadata.st_ctime_ns),
                        "flags": int(getattr(verification_metadata, "st_flags", 0)),
                    },
                },
            )
            core = target / "scripts" / "control-plane"

            for arguments in (
                (
                    "policy-check",
                    "--policy",
                    str(target / ".codex" / "project-policy.toml"),
                    "--json",
                ),
                (
                    "registry-check",
                    "--registry",
                    str(target / ".codex" / "resource-registry.toml"),
                    "--policy",
                    str(target / ".codex" / "project-policy.toml"),
                    "--json",
                ),
                ("doctor", "--repo", str(target), "--json"),
            ):
                code, payload, error = _run(core, *arguments, cwd=target)
                self.assertEqual(code, 0, (arguments, payload, error))
                self.assertIs(payload["authorizes"], False)

            hook_code, hook_payload, hook_error = _run(
                core,
                "__hook__",
                cwd=target,
                input_payload='{"hook_event_name":"UserPromptSubmit"}',
            )
            self.assertEqual(hook_code, 0, hook_error)
            self.assertTrue(hook_payload["continue"])

            task_digest = "sha256:" + sha256(b"temporary-adoption-task").hexdigest()
            decision_digest = "sha256:" + sha256(b"temporary-adoption-decision").hexdigest()
            session = "SESSION-ADOPTION-E2E"
            task_id = "TASK-ADOPTION-E2E"
            branch = git(target, "branch", "--show-current").stdout.decode().strip()
            start_code, started, start_error = _run(
                core,
                "task",
                "start",
                "--repo",
                str(target),
                "--task-id",
                task_id,
                "--outcome",
                "local_change",
                "--branch",
                branch,
                "--task-digest",
                task_digest,
                "--decision-digest",
                decision_digest,
                "--session-id",
                session,
                "--scope-path",
                "AGENTS.md",
                "--policy",
                str(target / ".codex" / "project-policy.toml"),
                "--json",
                cwd=target,
            )
            self.assertEqual(start_code, 0, (started, start_error))
            for task_state in ("planned", "ready", "implementing", "verifying", "review_ready"):
                code, payload, error = _run(
                    core,
                    "task",
                    "transition",
                    "--repo",
                    str(target),
                    "--task-id",
                    task_id,
                    "--state",
                    task_state,
                    "--session-id",
                    session,
                    "--json",
                    cwd=target,
                )
                self.assertEqual(code, 0, (task_state, payload, error))
            close_code, closed, close_error = _run(
                core,
                "task",
                "close",
                "--repo",
                str(target),
                "--task-id",
                task_id,
                "--session-id",
                session,
                "--json",
                cwd=target,
            )
            self.assertEqual(close_code, 0, (closed, close_error))
            binding = started["lease"]
            release_arguments = (
                "task",
                "lease-release",
                "--repo",
                str(target),
                "--task-id",
                str(binding["task_id"]),
                "--revision-id",
                str(binding["revision_id"]),
                "--lease-generation",
                str(binding["lease_generation"]),
                "--worktree",
                str(binding["worktree"]),
                "--branch",
                str(binding["branch"]),
                "--session-id",
                str(binding["session_id"]),
                "--policy-digest",
                str(binding["policy_digest"]),
                "--lease-digest",
                str(binding["lease_digest"]),
                "--json",
            )
            release_code, released, release_error = _run(
                core, *release_arguments, cwd=target
            )
            self.assertEqual(release_code, 0, (released, release_error))
            status_code, task_status, status_error = _run(
                core,
                "task",
                "status",
                "--repo",
                str(target),
                "--task-id",
                task_id,
                "--json",
                cwd=target,
            )
            self.assertEqual(status_code, 0, status_error)
            self.assertEqual(task_status["task"]["state"], "closed")
            self.assertIsNone(task_status["lease"])

            verify_code, verified, verify_error = _run(
                ADOPTION,
                "verify",
                "--target",
                str(target),
                "--json",
                cwd=ROOT,
            )
            self.assertEqual(verify_code, 0, (verified, verify_error))

            # Terminal Core evidence belongs only to this temporary harness. Remove
            # those exact records after observing the closed/no-lease state so the
            # adoption rollback can prove it never deletes runtime-owned data.
            for path in _task_records(target):
                if path == verification_lock:
                    continue
                if path.name in {"adoption.lock", "journal.json"} or "adoption/evidence" in path.as_posix():
                    continue
                path.unlink()
            for relative in ("tasks", "leases", "lease-release-receipts", "locks"):
                path = state / relative
                if path.exists() and not any(path.iterdir()):
                    path.rmdir()

            rollback_code, rollback_receipt, rollback_error = _run(
                ADOPTION,
                "rollback",
                "--target",
                str(target),
                "--install-digest",
                str(apply_receipt["install_digest"]),
                "--json",
                cwd=ROOT,
            )
            self.assertEqual(rollback_code, 0, (rollback_receipt, rollback_error))
            self.assertEqual(validate_receipt(rollback_receipt), ())
            self.assertEqual(before, _tree_snapshot(target))
            self.assertEqual(
                git(
                    target,
                    "config",
                    "--local",
                    "--get-all",
                    "core.hooksPath",
                    check=False,
                ).stdout,
                b"",
            )
            self.assertEqual(status(target)["state"], "ROLLED_BACK")
            current_verification_metadata = verification_lock.lstat()
            self.assertEqual(
                (
                    current_verification_metadata.st_dev,
                    current_verification_metadata.st_ino,
                ),
                verification_identity,
            )
            evidence = tuple(
                path.relative_to(state).as_posix()
                for path in state.rglob("*")
                if path.is_file()
            )
            install_hex = str(apply_receipt["install_digest"]).removeprefix("sha256:")
            expected_evidence = {
                "adoption.lock",
                "locks/verification.lock",
                f"adoption/evidence/{install_hex}.json",
                f"adoption/.recovery-{install_hex}/control-plane.lock",
                *(
                    f"adoption/.staging-{install_hex}/{index:04d}"
                    for index, record in enumerate(journal["rollback_records"])
                    if record["path"] != ".codex/control-plane.lock"
                ),
            }
            self.assertEqual(
                set(evidence),
                expected_evidence,
            )
            self.assertEqual(len(evidence), len(expected_evidence))


if __name__ == "__main__":
    unittest.main()
