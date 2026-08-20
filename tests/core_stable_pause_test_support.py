from __future__ import annotations

import copy
from hashlib import sha256
import os
from pathlib import Path
import stat
import subprocess
from typing import Any

from control_plane.contracts import contract_digest, stable_pause_checkpoint_digest
from control_plane.leases import LeaseStore
from control_plane.task_state import CoreTaskStore
from control_plane.verification import VerificationMutex


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def stable_pause_observation(
    *,
    status: str = "SAFE_PAUSE_ACTIVE",
    task_state: str = "implementing",
    lease_state: str = "active",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "StablePauseObservationV1",
        "scope": "core-owned-local-state",
        "status": status,
        "repository": {
            "root": "/private/tmp/stable-pause-repo",
            "common_git_dir": "/private/tmp/stable-pause-repo/.git",
            "branch": "codex/stable-pause-v1",
            "head": "1" * 40,
            "status_digest": DIGEST_A,
            "worktree_digest": DIGEST_B,
            "staged_count": 0,
            "unstaged_count": 1,
            "untracked_count": 0,
            "diff_check": "PASS",
        },
        "lifecycle": {
            "task_id": "TASK-STABLE-PAUSE-V1",
            "task_state": task_state,
            "task_state_digest": DIGEST_C,
            "lease_state": lease_state,
            "lease_digest": DIGEST_D if lease_state == "active" else None,
            "owner_runtime_digest": DIGEST_A,
        },
        "control_plane_state": {
            "adoption_mutex": "free",
            "verification_mutex": "free",
            "task_mutex": "free",
            "lease_mutex": "free",
            "residue_count": 0,
            "residue_digest": DIGEST_B,
        },
        "checks": {
            "repository_identity": "PASS",
            "snapshot_stability": "PASS",
            "lifecycle_binding": "PASS",
            "mutex_quiescence": "PASS",
            "owned_residue": "PASS",
        },
        "issues": [],
        "checkpoint_digest": DIGEST_A,
        "authorizes": False,
    }
    value["checkpoint_digest"] = stable_pause_checkpoint_digest(value)
    return value


def resigned(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["checkpoint_digest"] = stable_pause_checkpoint_digest(result)
    return result


def git(repository: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            *arguments,
        ],
        cwd=repository,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/var/empty",
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        },
        timeout=10,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"git fixture command failed: {arguments!r}")
    return completed.stdout.strip()


def make_repository(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Stable Pause Fixture")
    git(path, "config", "user.email", "stable-pause@example.invalid")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    codex = path / ".codex"
    codex.mkdir(mode=0o700)
    (codex / "control-plane.lock").write_text(
        f'schema_version = 2\n[digests]\nruntime = "{DIGEST_A}"\n',
        encoding="utf-8",
    )
    (codex / "project-policy.toml").write_bytes(
        (Path(__file__).parent / "fixtures" / "valid-policy.toml").read_bytes()
    )
    git(
        path,
        "add",
        "tracked.txt",
        ".codex/control-plane.lock",
        ".codex/project-policy.toml",
    )
    git(path, "commit", "-m", "fixture")
    git(path, "switch", "-c", "codex/stable-pause-v1")
    return path.resolve()


def install_lifecycle_fixture(
    repository: Path,
    *,
    task_id: str = "TASK-STABLE-PAUSE-V1",
    terminal: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Create ordinary Core state using only production writer APIs in a temp repo."""

    branch = git(repository, "branch", "--show-current")
    tasks = CoreTaskStore(repository)
    leases = LeaseStore(repository)
    state = tasks.start(
        task_id,
        outcome="local_change",
        branch=branch,
        head=git(repository, "rev-parse", "HEAD"),
        task_digest=contract_digest({"stable-pause-task": task_id}),
        decision_digest=contract_digest({"stable-pause-decision": task_id}),
        scope_paths=["control_plane"],
    )
    lease: dict[str, Any] | None = None
    if terminal:
        for target in (
            "planned",
            "ready",
            "implementing",
            "verifying",
            "review_ready",
            "closed",
        ):
            state = tasks.transition(
                task_id,
                target,
                current_branch=branch,
            )
    else:
        lease = leases.acquire(
            state,
            session_id="SESSION-STABLE-PAUSE-V1",
            policy_digest=contract_digest({"stable-pause-policy": task_id}),
        )
        state = tasks.bind_lease_generation(
            task_id,
            revision_id=str(lease["revision_id"]),
            generation=int(lease["lease_generation"]),
            expected_state_digest=str(state["state_digest"]),
            session_id=str(lease["session_id"]),
        )
        for target in ("planned", "ready", "implementing"):
            state = tasks.transition(
                task_id,
                target,
                current_branch=branch,
                session_id=str(lease["session_id"]),
            )
    with VerificationMutex(repository) as acquired:
        if not acquired:
            raise AssertionError("fixture verification mutex was unexpectedly held")
    return state, lease


def private_state_identity_snapshot(
    repository: Path,
) -> tuple[tuple[object, ...], ...]:
    """Bind every Core-owned state entry in a temporary single-worktree repo."""

    git_dir = Path(
        git(repository, "rev-parse", "--path-format=absolute", "--git-dir")
    ).resolve()
    state = git_dir / "codex-control-plane-core"
    if not state.exists():
        return ()
    records: list[tuple[object, ...]] = []
    for path in (state, *sorted(state.rglob("*"))):
        metadata = path.lstat()
        relative = "." if path == state else path.relative_to(state).as_posix()
        if stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            payload = b""
        elif stat.S_ISREG(metadata.st_mode):
            kind = "regular"
            payload = path.read_bytes()
        elif stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            payload = os.readlink(path).encode("utf-8")
        else:
            kind = "other"
            payload = b""
        records.append(
            (
                relative,
                kind,
                int(metadata.st_dev),
                int(metadata.st_ino),
                int(metadata.st_mode),
                int(metadata.st_nlink),
                int(metadata.st_uid),
                int(metadata.st_gid),
                int(metadata.st_size),
                int(metadata.st_mtime_ns),
                int(metadata.st_ctime_ns),
                payload,
            )
        )
    return tuple(records)


def repository_surface_snapshot(repository: Path) -> tuple[tuple[object, ...], ...]:
    git_dir = Path(
        git(repository, "rev-parse", "--path-format=absolute", "--git-dir")
    ).resolve()
    records: list[tuple[object, ...]] = []
    for current, directories, files in os.walk(git_dir, followlinks=False):
        directories[:] = sorted(directories)
        for name in sorted(files):
            path = Path(current) / name
            metadata = path.lstat()
            relative = path.relative_to(git_dir).as_posix()
            if stat.S_ISREG(metadata.st_mode):
                payload = path.read_bytes()
                records.append(
                    (
                        relative,
                        "regular",
                        metadata.st_dev,
                        metadata.st_ino,
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_nlink,
                        metadata.st_size,
                        metadata.st_mtime_ns,
                        metadata.st_ctime_ns,
                        sha256(payload).hexdigest(),
                    )
                )
            elif stat.S_ISLNK(metadata.st_mode):
                records.append((relative, "symlink", os.readlink(path)))
            else:
                records.append((relative, "other", metadata.st_mode))
    return tuple(records)
