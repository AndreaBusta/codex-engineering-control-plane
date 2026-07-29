"""Task lifecycle, worktree-scoped leases, and compact resource receipts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping
from uuid import uuid4

from control_plane.contracts import (
    RESOURCE_ID,
    SHA256_DIGEST,
    TASK_EFFECTS,
    contract_digest,
    validate_task_id,
    validate_task_envelope,
)
from control_plane.scopes import normalize_scope, scope_owns, scopes_overlap
from control_plane.host_bridge import (
    GoverningRuntimeObservation,
    ValidatedCandidateWorktreeObservation,
    ValidatedGitHubObservation,
    ValidatedGoverningBaseWorktreeObservation,
    ValidatedLocalGitObservation,
    ValidatedReleaseProviderObservation,
    ValidatedWorktreeInventoryObservation,
    _consume_governing_runtime_observation,
    _consume_runtime_host_object,
    _consume_worktree_inventory,
    _governing_runtime_observation_is_live,
    _register_runtime_host_object,
    _runtime_host_object_is_live,
    consume_lease_recovery_authorization,
    consume_lifecycle_observation,
    observe_worktree_inventory,
    validate_worktree_inventory_observation,
)


ORDERED_STATES = (
    "framed",
    "planned",
    "ready",
    "implementing",
    "verifying",
    "review_ready",
    "committed",
    "pushed",
    "pr_draft",
    "pr_ready",
    "merged",
    "base_verified",
    "release_pending",
    "released",
    "observed",
    "closed",
    "blocked",
)
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    state: frozenset(
        {
            *(("blocked",) if state not in {"closed", "blocked"} else ()),
            *(
                (ORDERED_STATES[index + 1],)
                if state not in {"closed", "blocked"}
                and index + 1 < len(ORDERED_STATES) - 1
                else ()
            ),
        }
    )
    for index, state in enumerate(ORDERED_STATES)
}
LEGAL_TRANSITIONS["closed"] = frozenset()
LEGAL_TRANSITIONS["blocked"] = frozenset()
OUTCOME_LIMITS = {
    "answer": "planned",
    "local_change": "review_ready",
    "commit": "committed",
    "pull_request": "pr_ready",
    "integration": "base_verified",
    "release": "observed",
}
TRANSITION_EVIDENCE = {
    "ready": frozenset({"preflight_ok"}),
    "verifying": frozenset({"implementation_complete"}),
    "review_ready": frozenset({"gates_ok", "documentation_decision"}),
    "committed": frozenset({"commit"}),
    "pushed": frozenset({"remote_head"}),
    "pr_draft": frozenset({"pull_request"}),
    "pr_ready": frozenset({"checks_ok"}),
    "merged": frozenset({"merge_commit"}),
    "base_verified": frozenset({"remote_base"}),
    "release_pending": frozenset({"release_manifest"}),
    "released": frozenset({"provider_build"}),
    "observed": frozenset({"observation"}),
}
GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{7,64}$", re.ASCII)
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)
VERIFICATION_PROFILES = frozenset(
    {"control_plane_assurance", "governing_base_verification"}
)
VERIFICATION_COMMAND_IDS = {
    "control_plane_assurance": (
        "normal_budget",
        "assurance_budget",
        "policy_check",
        "registry_check",
        "doctor",
        "risk_integration_smoke",
        "security_regression",
        "candidate_diff_check",
    ),
    "governing_base_verification": (
        "normal_budget",
        "policy_check",
        "registry_check",
        "doctor",
        "governing_tree_clean",
    ),
}


def build_verification_task_envelope(
    *, task_id: str, profile: str
) -> dict[str, Any]:
    """Build one of the two complete, immutable verification task templates."""

    if not validate_task_id(task_id) or profile not in VERIFICATION_PROFILES:
        raise ValueError(
            "E_VERIFICATION_PROFILE: closed verification profile required"
        )
    candidate = profile == "control_plane_assurance"
    task = {
        "schema_version": 1,
        "task_id": task_id,
        "objective": (
            "Verify the bound control-plane candidate without changing "
            "repository content."
            if candidate
            else "Verify the bound governing base without changing repository "
            "content."
        ),
        "intent": "operate",
        "phase": "verify",
        "requested_outcome": "local_change",
        "goals": [
            {
                "id": (
                    "verify-candidate"
                    if candidate
                    else "verify-governing-base"
                ),
                "summary": (
                    "Run the closed verification profile and publish bounded "
                    "receipts."
                    if candidate
                    else "Run the closed governing base profile and publish "
                    "bounded receipts."
                ),
                "domains": ["generic"],
                "depends_on": [],
            }
        ],
        "domains": ["generic"],
        "signals": ["regression_risk"],
        "scope_paths": ["."],
        "risk": {
            "uncertainty": 1,
            "blast_radius": 2,
            "irreversibility": 0,
            "verification_complexity": 2,
        },
        "risk_provenance": "project_policy",
        "effects": [
            {"name": "local_read", "source": "project_policy"},
            {"name": "local_write", "source": "project_policy"},
        ],
        "explicit_resources": [],
        "excluded_resources": [],
    }
    issues = validate_task_envelope(task)
    if issues:
        raise ValueError(
            "E_VERIFICATION_PROFILE: internal task template is invalid"
        )
    return task


class _VerificationBootstrapAuthority:
    __slots__ = (
        "_consumed",
        "profile",
        "runtime_digest",
        "target_digest",
        "content_trust",
        "authority_digest",
        "session_id",
        "invocation_id",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "_VerificationBootstrapAuthority":
        raise TypeError("verification bootstrap authority is host-bound")


class CandidateAssuranceBootstrapAuthority(_VerificationBootstrapAuthority):
    pass


class GoverningBaseBootstrapAuthority(_VerificationBootstrapAuthority):
    pass


def _mint_verification_bootstrap_authority(
    authority_type: type[_VerificationBootstrapAuthority],
    *,
    profile: str,
    runtime: GoverningRuntimeObservation,
    target: object,
) -> _VerificationBootstrapAuthority:
    authority = object.__new__(authority_type)
    authority._consumed = False
    authority.profile = profile
    authority.runtime_digest = runtime.runtime_digest
    authority.target_digest = target.target_digest
    authority.content_trust = target.content_trust
    authority.session_id = runtime.session_id
    authority.invocation_id = runtime.invocation_id
    authority.authority_digest = contract_digest(
        {
            "profile": profile,
            "runtime_digest": authority.runtime_digest,
            "target_digest": authority.target_digest,
            "content_trust": authority.content_trust,
            "session_id": authority.session_id,
            "invocation_id": authority.invocation_id,
        }
    )
    kind = (
        "candidate_assurance_bootstrap_authority"
        if authority_type is CandidateAssuranceBootstrapAuthority
        else "governing_base_bootstrap_authority"
    )
    _register_runtime_host_object(authority, kind)
    return authority


def bind_candidate_assurance_bootstrap_authority(
    *,
    governing_runtime: object,
    candidate_target: object,
    expected_head: str,
    session_id: str,
    invocation_id: str,
    clock: object,
) -> CandidateAssuranceBootstrapAuthority:
    now = float(clock())
    if (
        not isinstance(governing_runtime, GoverningRuntimeObservation)
        or not _runtime_host_object_is_live(
            governing_runtime, "governing_runtime_observation"
        )
        or not isinstance(
            candidate_target, ValidatedCandidateWorktreeObservation
        )
        or not _runtime_host_object_is_live(
            candidate_target, "candidate_verification_target"
        )
        or governing_runtime._consumed
        or candidate_target._consumed
        or candidate_target.head != expected_head
        or candidate_target.content_trust
        not in {"project_owned", "external_untrusted"}
        or governing_runtime.target_worktree
        != candidate_target.worktree_identity
        or governing_runtime.session_id != session_id
        or candidate_target.session_id != session_id
        or governing_runtime.invocation_id != invocation_id
        or candidate_target.invocation_id != invocation_id
        or now > governing_runtime.freshness_deadline
        or now > candidate_target.freshness_deadline
    ):
        raise ValueError(
            "E_VERIFICATION_BOOTSTRAP: candidate authority binding is invalid"
        )
    if not _consume_runtime_host_object(
        governing_runtime, "governing_runtime_observation"
    ) or not _consume_runtime_host_object(
        candidate_target, "candidate_verification_target"
    ):
        raise ValueError(
            "E_VERIFICATION_BOOTSTRAP: candidate bindings are not host-issued"
        )
    governing_runtime._consumed = True
    candidate_target._consumed = True
    authority = _mint_verification_bootstrap_authority(
        CandidateAssuranceBootstrapAuthority,
        profile="control_plane_assurance",
        runtime=governing_runtime,
        target=candidate_target,
    )
    assert isinstance(authority, CandidateAssuranceBootstrapAuthority)
    return authority


def bind_governing_base_bootstrap_authority(
    *,
    governing_runtime: object,
    verifier_target: object,
    expected_governing_base_commit: str,
    session_id: str,
    invocation_id: str,
    clock: object,
) -> GoverningBaseBootstrapAuthority:
    now = float(clock())
    if (
        not isinstance(governing_runtime, GoverningRuntimeObservation)
        or not _runtime_host_object_is_live(
            governing_runtime, "governing_runtime_observation"
        )
        or not isinstance(
            verifier_target, ValidatedGoverningBaseWorktreeObservation
        )
        or not _runtime_host_object_is_live(
            verifier_target, "governing_base_verification_target"
        )
        or governing_runtime._consumed
        or verifier_target._consumed
        or verifier_target.content_trust != "governing_base"
        or governing_runtime.governing_base_commit
        != expected_governing_base_commit
        or verifier_target.head != expected_governing_base_commit
        or governing_runtime.target_worktree
        != verifier_target.worktree_identity
        or governing_runtime.session_id != session_id
        or verifier_target.session_id != session_id
        or governing_runtime.invocation_id != invocation_id
        or verifier_target.invocation_id != invocation_id
        or now > governing_runtime.freshness_deadline
        or now > verifier_target.freshness_deadline
    ):
        raise ValueError(
            "E_VERIFICATION_BOOTSTRAP: base authority binding is invalid"
        )
    if not _consume_runtime_host_object(
        governing_runtime, "governing_runtime_observation"
    ) or not _consume_runtime_host_object(
        verifier_target, "governing_base_verification_target"
    ):
        raise ValueError(
            "E_VERIFICATION_BOOTSTRAP: base bindings are not host-issued"
        )
    governing_runtime._consumed = True
    verifier_target._consumed = True
    authority = _mint_verification_bootstrap_authority(
        GoverningBaseBootstrapAuthority,
        profile="governing_base_verification",
        runtime=governing_runtime,
        target=verifier_target,
    )
    assert isinstance(authority, GoverningBaseBootstrapAuthority)
    return authority


class VerificationTaskBootstrap:
    __slots__ = (
        "_consumed",
        "task",
        "task_digest",
        "profile",
        "profile_digest",
        "runtime_digest",
        "target_digest",
        "content_trust",
        "authority_digest",
        "bootstrap_digest",
        "session_id",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "VerificationTaskBootstrap":
        raise TypeError("verification task bootstrap is host-bound")


def create_verification_task_bootstrap(
    *, task_id: str, authority: object
) -> VerificationTaskBootstrap:
    if not isinstance(
        authority,
        (
            CandidateAssuranceBootstrapAuthority,
            GoverningBaseBootstrapAuthority,
        ),
    ):
        raise ValueError(
            "E_VERIFICATION_BOOTSTRAP: typed authority is required"
        )
    if authority._consumed:
        raise ValueError(
            "E_VERIFICATION_BOOTSTRAP: authority was consumed"
        )
    authority_kind = (
        "candidate_assurance_bootstrap_authority"
        if type(authority) is CandidateAssuranceBootstrapAuthority
        else "governing_base_bootstrap_authority"
    )
    if not _consume_runtime_host_object(authority, authority_kind):
        raise ValueError(
            "E_VERIFICATION_BOOTSTRAP: authority is not host-issued"
        )
    authority._consumed = True
    bootstrap = object.__new__(VerificationTaskBootstrap)
    bootstrap._consumed = False
    bootstrap.task = build_verification_task_envelope(
        task_id=task_id, profile=authority.profile
    )
    bootstrap.task_digest = contract_digest(bootstrap.task)
    bootstrap.profile = authority.profile
    bootstrap.profile_digest = contract_digest(
        {
            "profile": bootstrap.profile,
            "commands": VERIFICATION_COMMAND_IDS[bootstrap.profile],
        }
    )
    bootstrap.runtime_digest = authority.runtime_digest
    bootstrap.target_digest = authority.target_digest
    bootstrap.content_trust = authority.content_trust
    bootstrap.authority_digest = authority.authority_digest
    bootstrap.session_id = authority.session_id
    bootstrap.bootstrap_digest = contract_digest(
        {
            "task_digest": bootstrap.task_digest,
            "profile": bootstrap.profile,
            "profile_digest": bootstrap.profile_digest,
            "runtime_digest": bootstrap.runtime_digest,
            "target_digest": bootstrap.target_digest,
            "content_trust": bootstrap.content_trust,
            "authority_digest": bootstrap.authority_digest,
            "session_id": bootstrap.session_id,
        }
    )
    _register_runtime_host_object(bootstrap, "verification_task_bootstrap")
    return bootstrap


class VerificationExecutionContext:
    __slots__ = (
        "_consumed",
        "task_id",
        "task_digest",
        "profile",
        "profile_digest",
        "runtime_digest",
        "target_digest",
        "content_trust",
        "repository",
        "worktree",
        "expected_head",
        "session_id",
        "lease_digest",
        "dedicated_temp_root",
        "executables",
        "executables_digest",
        "context_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "VerificationExecutionContext":
        raise TypeError("verification execution context is host-bound")


def _git_head(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def create_verification_execution_context(
    *,
    task_context: Mapping[str, Any],
    lease: Mapping[str, Any],
    canonical_repo: Path | str,
    expected_head: str,
    session_id: str,
    dedicated_temp_root: Path | str,
    clock: object,
) -> VerificationExecutionContext:
    del clock
    repo = Path(canonical_repo).resolve()
    worktree = Path(str(lease.get("worktree", ""))).resolve()
    temp_root = Path(dedicated_temp_root).resolve()
    profile = str(task_context.get("verification_profile", ""))
    content_trust = str(
        task_context.get("verification_content_trust", "")
    )
    expected_profile_digest = contract_digest(
        {
            "profile": profile,
            "commands": VERIFICATION_COMMAND_IDS.get(profile, ()),
        }
    )
    if (
        profile not in VERIFICATION_PROFILES
        or content_trust
        not in {"project_owned", "governing_base", "external_untrusted"}
        or task_context.get("verification_profile_digest")
        != expected_profile_digest
        or task_context.get("session_id") != session_id
        or lease.get("session_id") != session_id
        or lease.get("task_id") != task_context.get("task_id")
        or (
            task_context.get("lease_digest") is not None
            and lease.get("lease_digest") != task_context.get("lease_digest")
        )
        or worktree != repo
        or repo == temp_root
        or repo in temp_root.parents
        or temp_root in repo.parents
        or _git_head(repo) != expected_head
    ):
        raise ValueError(
            "E_VERIFICATION_CONTEXT: task, lease, HEAD, or profile drifted"
        )
    python_executable = Path(sys.executable).resolve()
    git_executable = shutil.which("git")
    if (
        not python_executable.is_file()
        or git_executable is None
        or not Path(git_executable).resolve().is_file()
    ):
        raise ValueError(
            "E_VERIFICATION_CONTEXT: required executable is unavailable"
        )
    executables = {
        "python": str(python_executable),
        "git": str(Path(git_executable).resolve()),
        "control_plane": str((repo / "scripts" / "control-plane").resolve()),
    }
    context = object.__new__(VerificationExecutionContext)
    context._consumed = False
    values = {
        "task_id": str(task_context["task_id"]),
        "task_digest": str(task_context["task_digest"]),
        "profile": profile,
        "profile_digest": str(task_context["verification_profile_digest"]),
        "runtime_digest": str(task_context["verification_runtime_digest"]),
        "target_digest": str(task_context["verification_target_digest"]),
        "content_trust": content_trust,
        "repository": str(repo),
        "worktree": str(worktree),
        "expected_head": expected_head,
        "session_id": session_id,
        "lease_digest": str(lease["lease_digest"]),
        "dedicated_temp_root": str(temp_root),
        "executables": executables,
        "executables_digest": contract_digest(executables),
    }
    for name, value in values.items():
        setattr(context, name, value)
    context.context_digest = contract_digest(
        {
            name: getattr(context, name)
            for name in (
                "task_id",
                "task_digest",
                "profile",
                "profile_digest",
                "runtime_digest",
                "target_digest",
                "content_trust",
                "repository",
                "worktree",
                "expected_head",
                "session_id",
                "lease_digest",
                "executables_digest",
            )
        }
    )
    return context


@dataclass(frozen=True)
class CompletedVerificationCommand:
    command_id: str
    returncode: int
    status: str
    output_digest: str
    output_truncated: bool
    before_snapshot_digest: str
    after_snapshot_digest: str
    context_digest: str


def _verification_argv(
    context: VerificationExecutionContext, command_id: str
) -> tuple[str, ...]:
    repo = Path(context.repository)
    python = context.executables["python"]
    git = context.executables["git"]
    scripts = context.executables["control_plane"]
    commands: dict[str, tuple[str, ...]] = {
        "normal_budget": (
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-q",
        ),
        "assurance_budget": (
            python,
            "-m",
            "unittest",
            "tests.test_assurance",
            "-q",
        ),
        "policy_check": (
            scripts,
            "policy-check",
            "--policy",
            str(repo / ".codex" / "project-policy.toml"),
            "--json",
        ),
        "registry_check": (
            scripts,
            "registry-check",
            "--registry",
            str(repo / ".codex" / "resource-registry.toml"),
            "--policy",
            str(repo / ".codex" / "project-policy.toml"),
            "--json",
        ),
        "doctor": (scripts, "doctor", "--repo", str(repo), "--json"),
        "risk_integration_smoke": (
            python,
            "-m",
            "unittest",
            "tests.test_routing",
            "-q",
        ),
        "security_regression": (
            python,
            "-m",
            "unittest",
            "tests.test_repository_contract",
            "-q",
        ),
        "candidate_diff_check": (git, "-C", str(repo), "diff", "--check"),
        "governing_tree_clean": (
            git,
            "-C",
            str(repo),
            "status",
            "--porcelain=v2",
        ),
    }
    if command_id not in VERIFICATION_COMMAND_IDS[context.profile]:
        raise ValueError(
            "E_VERIFICATION_COMMAND: command is not in the bound profile"
        )
    return commands[command_id]


def _verification_snapshot(repo: Path) -> str:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ValueError("E_VERIFICATION_UNKNOWN: Git snapshot failed")
    residue: list[dict[str, object]] = []
    total_bytes = 0
    entries = 0
    for path in sorted(repo.rglob("*")):
        relative = path.relative_to(repo)
        if relative.parts and relative.parts[0] == ".git":
            continue
        entries += 1
        if entries > 20_000 or path.is_symlink():
            raise ValueError(
                "E_VERIFICATION_UNKNOWN: residue inventory is unsafe or too large"
            )
        stat = path.lstat()
        item: dict[str, object] = {
            "path": relative.as_posix(),
            "mode": stat.st_mode,
            "size": stat.st_size,
            "kind": "directory" if path.is_dir() else "file",
        }
        if path.is_file():
            total_bytes += stat.st_size
            if total_bytes > 67_108_864:
                raise ValueError(
                    "E_VERIFICATION_UNKNOWN: residue bytes exceed cap"
                )
            from hashlib import sha256

            item["digest"] = f"sha256:{sha256(path.read_bytes()).hexdigest()}"
        residue.append(item)
    index = subprocess.run(
        ["git", "-C", str(repo), "write-tree"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if index.returncode != 0:
        raise ValueError("E_VERIFICATION_UNKNOWN: index snapshot failed")
    return contract_digest(
        {
            "head": _git_head(repo),
            "index_tree": index.stdout.strip(),
            "status_hex": completed.stdout.hex(),
            "residue": residue,
        }
    )


def _sanitized_verification_environment(
    context: VerificationExecutionContext,
) -> dict[str, str]:
    temp_root = Path(context.dedicated_temp_root)
    executable_dirs = sorted(
        {
            str(Path(value).parent)
            for value in context.executables.values()
        }
    )
    return {
        "PATH": os.pathsep.join(executable_dirs),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(temp_root / "home"),
        "TMPDIR": str(temp_root / "tmp"),
        "XDG_CACHE_HOME": str(temp_root / "cache"),
        "PYTHONPYCACHEPREFIX": str(temp_root / "pycache"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "SSH_AUTH_SOCK": "",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }


def _run_verification_command(
    *,
    context: VerificationExecutionContext,
    command_id: str,
    clock: object,
) -> CompletedVerificationCommand:
    del clock
    if type(context) is not VerificationExecutionContext:
        raise ValueError("E_VERIFICATION_CONTEXT: typed context is required")
    if context.content_trust == "external_untrusted":
        raise ValueError(
            "E_VERIFICATION_HOST_ISOLATION: external untrusted code requires "
            "native read-root and no-network isolation"
        )
    argv = _verification_argv(context, command_id)
    repo = Path(context.repository)
    before = _verification_snapshot(repo)
    temp_root = Path(context.dedicated_temp_root)
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("home", "tmp", "cache", "pycache"):
        (temp_root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = _sanitized_verification_environment(context)
    timeout_seconds = (
        300 if context.profile == "control_plane_assurance" else 90
    )
    process = subprocess.Popen(
        argv,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=environment,
        shell=False,
        start_new_session=True,
    )
    if process.stdout is None:
        raise ValueError("E_VERIFICATION_UNKNOWN: child output is unavailable")
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    output_truncated = False
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    stream_open = True
    try:
        while stream_open or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            events = selector.select(
                timeout=0.05 if process.poll() is None else 0
            )
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(process.stdout)
                    stream_open = False
                    continue
                available = max(0, 1_048_576 - len(output))
                output.extend(chunk[:available])
                if len(chunk) > available:
                    output_truncated = True
            if timed_out and process.poll() is not None and not stream_open:
                break
        returncode = process.wait(timeout=1)
    finally:
        selector.close()
        process.stdout.close()
    after = _verification_snapshot(repo)
    status = (
        "PASS"
        if returncode == 0
        and not timed_out
        and not output_truncated
        and before == after
        and _git_head(repo) == context.expected_head
        else "FAIL"
    )
    return CompletedVerificationCommand(
        command_id=command_id,
        returncode=returncode,
        status=status,
        output_digest=contract_digest(
            {
                "output": bytes(output).hex(),
                "truncated": output_truncated,
                "timed_out": timed_out,
            }
        ),
        output_truncated=output_truncated,
        before_snapshot_digest=before,
        after_snapshot_digest=after,
        context_digest=context.context_digest,
    )


@dataclass(frozen=True)
class VerificationExecutionReceipt:
    task_id: str
    task_digest: str
    profile: str
    profile_digest: str
    head: str
    session_id: str
    lease_digest: str
    generation: int
    context_digest: str
    command_digests: tuple[str, ...]
    supplemental_receipt_digests: tuple[str, ...]
    host_isolation: str
    receipt_digest: str


VERIFICATION_SUPPLEMENTAL_RECEIPTS = {
    "control_plane_assurance": (
        "MacOSHookSmokeReceipt",
        "SkillPressureEvaluationReceipt",
        "IndependentReviewReceipt",
    ),
    "governing_base_verification": (),
}


class HostBoundVerificationEvidence:
    __slots__ = (
        "_consumed",
        "observation_id",
        "kind",
        "receipt_digest",
        "status",
        "subject_digest",
        "task_id",
        "task_digest",
        "head",
        "profile",
        "profile_digest",
        "generation",
        "session_id",
        "lease_digest",
        "context_digest",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "HostBoundVerificationEvidence":
        raise TypeError("verification evidence is host-bound")


def frame_verification_supplemental_evidence_set(
    *,
    governing_runtime: object,
    context: object,
    expected_generation: int,
    specifications: Mapping[str, Mapping[str, object]],
    clock: object,
) -> tuple[HostBoundVerificationEvidence, ...]:
    if (
        type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or type(context) is not VerificationExecutionContext
        or context._consumed
        or not isinstance(expected_generation, int)
        or isinstance(expected_generation, bool)
        or expected_generation < 1
        or not isinstance(specifications, Mapping)
        or set(specifications)
        != set(VERIFICATION_SUPPLEMENTAL_RECEIPTS[context.profile])
        or governing_runtime.runtime_digest != context.runtime_digest
        or governing_runtime.target_worktree != context.worktree
        or governing_runtime.session_id != context.session_id
        or float(clock()) > governing_runtime.freshness_deadline
    ):
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: governing runtime binding is required"
        )
    prepared: list[HostBoundVerificationEvidence] = []
    for kind in sorted(specifications):
        specification = specifications[kind]
        status = (
            specification.get("status")
            if isinstance(specification, Mapping)
            else None
        )
        subject_digest = (
            specification.get("subject_digest")
            if isinstance(specification, Mapping)
            else None
        )
        if (
            not isinstance(specification, Mapping)
            or set(specification) != {"status", "subject_digest"}
            or status not in {"PASS", "AUDIT", "PENDING"}
            or not isinstance(subject_digest, str)
            or SHA256_DIGEST.fullmatch(subject_digest) is None
        ):
            raise ValueError(
                "E_VERIFICATION_EVIDENCE: supplemental specification "
                "is invalid"
            )
        observation_id = f"verification-evidence-{uuid4().hex}"
        semantic = {
            "schema_version": 2,
            "observation_id": observation_id,
            "kind": kind,
            "task_id": context.task_id,
            "task_digest": context.task_digest,
            "head": context.expected_head,
            "profile": context.profile,
            "profile_digest": context.profile_digest,
            "generation": expected_generation,
            "session_id": context.session_id,
            "lease_digest": context.lease_digest,
            "status": status,
            "subject_digest": subject_digest,
            "context_digest": context.context_digest,
            "governing_runtime_digest": (
                governing_runtime.observation_digest
            ),
        }
        item = object.__new__(HostBoundVerificationEvidence)
        item._consumed = False
        values = {
            "observation_id": observation_id,
            "kind": kind,
            "receipt_digest": contract_digest(semantic),
            "status": status,
            "subject_digest": subject_digest,
            "task_id": context.task_id,
            "task_digest": context.task_digest,
            "head": context.expected_head,
            "profile": context.profile,
            "profile_digest": context.profile_digest,
            "generation": expected_generation,
            "session_id": context.session_id,
            "lease_digest": context.lease_digest,
            "context_digest": context.context_digest,
            "freshness_deadline": governing_runtime.freshness_deadline,
        }
        for name, value in values.items():
            setattr(item, name, value)
        prepared.append(item)
    if not _consume_governing_runtime_observation(governing_runtime):
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: governing runtime is not host-issued"
        )
    governing_runtime._consumed = True
    for item in prepared:
        _register_runtime_host_object(
            item, "verification_supplemental_evidence"
        )
    return tuple(prepared)


def publish_verification_supplemental_evidence(
    *,
    task_store: object,
    context: object,
    evidence: tuple[HostBoundVerificationEvidence, ...],
    expected_generation: int,
    clock: object,
) -> dict[str, Any]:
    if (
        type(task_store) is not TaskStore
        or type(context) is not VerificationExecutionContext
        or context._consumed
        or not isinstance(evidence, tuple)
        or not isinstance(expected_generation, int)
        or isinstance(expected_generation, bool)
    ):
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: typed publication inputs are required"
        )
    required = tuple(
        sorted(VERIFICATION_SUPPLEMENTAL_RECEIPTS[context.profile])
    )
    if tuple(item.kind for item in evidence) != required:
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: supplemental evidence set is not exact"
        )
    target_generation = expected_generation + 1
    registrations: dict[str, dict[str, object]] = {}
    for item in evidence:
        if (
            type(item) is not HostBoundVerificationEvidence
            or item._consumed
            or not _runtime_host_object_is_live(
                item, "verification_supplemental_evidence"
            )
            or float(clock()) > item.freshness_deadline
            or item.task_id != context.task_id
            or item.task_digest != context.task_digest
            or item.head != context.expected_head
            or item.profile != context.profile
            or item.profile_digest != context.profile_digest
            or item.generation != target_generation
            or item.session_id != context.session_id
            or item.lease_digest != context.lease_digest
            or item.context_digest != context.context_digest
        ):
            raise ValueError(
                "E_VERIFICATION_EVIDENCE: host evidence binding drifted"
            )
        registrations[item.kind] = {
            "observation_id": item.observation_id,
            "receipt_digest": item.receipt_digest,
            "status": item.status,
            "subject_digest": item.subject_digest,
        }
    receipt_root = (
        task_store.state_dir
        / "codex-control-plane"
        / "verification-receipts"
        / context.task_id
    )
    if receipt_root.exists() and (
        receipt_root.is_symlink()
        or not receipt_root.is_dir()
        or any(receipt_root.iterdir())
    ):
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: candidate receipt files are forbidden"
        )
    common_dir = _common_git_dir(task_store.state_dir)
    with _common_lease_lock(common_dir):
        with _task_guard(task_store.state_dir, context.task_id):
            state = task_store._read(context.task_id)
            lease = task_store._read_owner_lease(context.task_id)
            if (
                state.get("state") != "verifying"
                or state.get("generation") != expected_generation
                or state.get("task_digest") != context.task_digest
                or state.get("verification_profile") != context.profile
                or state.get("verification_profile_digest")
                != context.profile_digest
                or state.get("session_id") != context.session_id
                or lease is None
                or lease.get("lease_digest") != context.lease_digest
                or lease.get("session_id") != context.session_id
                or _git_head(Path(context.repository))
                != context.expected_head
            ):
                raise ValueError(
                    "E_STATE_CAS: verification evidence publication drifted"
                )
            for item in evidence:
                if not _consume_runtime_host_object(
                    item, "verification_supplemental_evidence"
                ):
                    raise ValueError(
                        "E_VERIFICATION_EVIDENCE: evidence claim failed"
                    )
            state["generation"] = target_generation
            state["verification_supplemental_evidence"] = registrations
            state["updated_at"] = _utc_now()
            _atomic_json(task_store._path(context.task_id), state)
    return state


def _load_verification_supplemental_evidence(
    *,
    task_store: "TaskStore",
    context: VerificationExecutionContext,
    expected_generation: int,
    evidence: tuple[HostBoundVerificationEvidence, ...],
    clock: object,
) -> tuple[HostBoundVerificationEvidence, ...]:
    required = VERIFICATION_SUPPLEMENTAL_RECEIPTS[context.profile]
    root = (
        task_store.state_dir
        / "codex-control-plane"
        / "verification-receipts"
        / context.task_id
    )
    if not required:
        if evidence or (root.exists() and any(root.iterdir())):
            raise ValueError(
                "E_VERIFICATION_EVIDENCE: governing profile forbids "
                "supplemental evidence"
            )
        return ()
    if root.exists() and (
        root.is_symlink()
        or not root.is_dir()
        or any(root.iterdir())
    ):
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: candidate receipt files are forbidden"
        )
    if (
        not isinstance(evidence, tuple)
        or tuple(item.kind for item in evidence) != tuple(sorted(required))
    ):
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: supplemental receipt set is not exact"
        )
    state = task_store.status(context.task_id)
    registered = state.get("verification_supplemental_evidence")
    if not isinstance(registered, Mapping) or set(registered) != set(required):
        raise ValueError(
            "E_VERIFICATION_EVIDENCE: supplemental state registration "
            "is not exact"
        )
    validated: list[HostBoundVerificationEvidence] = []
    for item in evidence:
        registration = registered.get(item.kind)
        if (
            type(item) is not HostBoundVerificationEvidence
            or item._consumed
            or not isinstance(registration, Mapping)
            or item.task_id != context.task_id
            or item.task_digest != context.task_digest
            or item.head != context.expected_head
            or item.profile != context.profile
            or item.profile_digest != context.profile_digest
            or item.generation != expected_generation
            or item.session_id != context.session_id
            or item.lease_digest != context.lease_digest
            or item.context_digest != context.context_digest
            or float(clock()) > item.freshness_deadline
            or registration
            != {
                "observation_id": item.observation_id,
                "receipt_digest": item.receipt_digest,
                "status": item.status,
                "subject_digest": item.subject_digest,
            }
        ):
            raise ValueError(
                "E_VERIFICATION_EVIDENCE: supplemental receipt binding drifted"
            )
        validated.append(item)
    return tuple(validated)


def run_verification_profile(
    *,
    context: VerificationExecutionContext,
    task_store: object,
    expected_generation: int,
    clock: object,
    supplemental_evidence: tuple[
        HostBoundVerificationEvidence, ...
    ] = (),
) -> VerificationExecutionReceipt:
    if (
        type(context) is not VerificationExecutionContext
        or type(task_store) is not TaskStore
        or not isinstance(expected_generation, int)
        or isinstance(expected_generation, bool)
        or context._consumed
    ):
        raise ValueError("E_VERIFICATION_REPLAY: context was consumed")
    state = task_store.status(context.task_id)
    lease = task_store._read_owner_lease(context.task_id)
    if (
        state.get("state") != "verifying"
        or state.get("generation") != expected_generation
        or state.get("task_digest") != context.task_digest
        or state.get("verification_profile") != context.profile
        or state.get("verification_profile_digest") != context.profile_digest
        or state.get("verification_runtime_digest") != context.runtime_digest
        or state.get("verification_target_digest") != context.target_digest
        or state.get("verification_content_trust") != context.content_trust
        or state.get("session_id") != context.session_id
        or lease is None
        or lease.get("task_id") != context.task_id
        or lease.get("session_id") != context.session_id
        or lease.get("lease_digest") != context.lease_digest
        or str(Path(str(lease.get("worktree", ""))).resolve())
        != context.worktree
        or lease.get("branch") != state.get("branch")
        or _git_head(Path(context.repository)) != context.expected_head
    ):
        raise ValueError(
            "E_VERIFICATION_CONTEXT: task, generation, lease, or HEAD drifted"
        )
    profile_commands = VERIFICATION_COMMAND_IDS[context.profile]
    if len(set(profile_commands)) != len(profile_commands):
        raise ValueError(
            "E_VERIFICATION_PROFILE: repeated command ID in closed profile"
        )
    started = float(clock())
    aggregate_snapshot = _verification_snapshot(Path(context.repository))
    completed: list[CompletedVerificationCommand] = []
    failure_reason: str | None = None
    try:
        for command_id in profile_commands:
            command = _run_verification_command(
                context=context, command_id=command_id, clock=clock
            )
            completed.append(command)
            if command.status != "PASS":
                failure_reason = "E_VERIFICATION_FAIL"
                break
            deadline = (
                300
                if context.profile == "control_plane_assurance"
                else 90
            )
            if float(clock()) - started > deadline:
                failure_reason = "E_VERIFICATION_FAIL"
                break
        supplemental = _load_verification_supplemental_evidence(
            task_store=task_store,
            context=context,
            expected_generation=expected_generation,
            evidence=supplemental_evidence,
            clock=clock,
        )
        if (
            failure_reason is None
            and (
                len(completed) != len(profile_commands)
                or _verification_snapshot(Path(context.repository))
                != aggregate_snapshot
                or _git_head(Path(context.repository))
                != context.expected_head
            )
        ):
            failure_reason = "E_VERIFICATION_MUTATION"
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        failure_reason = (
            code
            if code
            in {
                "E_VERIFICATION_FAIL",
                "E_VERIFICATION_UNKNOWN",
                "E_VERIFICATION_MUTATION",
                "E_VERIFICATION_PROFILE",
                "E_VERIFICATION_EVIDENCE",
                "E_VERIFICATION_HOST_ISOLATION",
            }
            else "E_VERIFICATION_UNKNOWN"
        )
        supplemental = ()
    if failure_reason is not None:
        context._consumed = True
        task_store.abort_verification(
            task_id=context.task_id,
            expected_generation=expected_generation,
            task_digest=context.task_digest,
            repo=context.repository,
            worktree=context.worktree,
            branch=str(state["branch"]),
            session_id=context.session_id,
            lease_digest=context.lease_digest,
            reason_code=failure_reason,
            clock=clock,
        )
        raise ValueError(
            f"{failure_reason}: closed verification profile did not pass"
        )
    command_digests = tuple(
        contract_digest(item.__dict__) for item in completed
    )
    supplemental_digests = tuple(
        item.receipt_digest for item in supplemental
    )
    receipt_core = {
        "task_id": context.task_id,
        "task_digest": context.task_digest,
        "profile": context.profile,
        "profile_digest": context.profile_digest,
        "head": context.expected_head,
        "session_id": context.session_id,
        "lease_digest": context.lease_digest,
        "generation": expected_generation,
        "context_digest": context.context_digest,
        "command_digests": command_digests,
        "supplemental_receipt_digests": supplemental_digests,
        "host_isolation": "pending_verification_host_isolation",
    }
    receipt = VerificationExecutionReceipt(
        **receipt_core,
        receipt_digest=contract_digest(receipt_core),
    )
    task_store._complete_verification(
        receipt=receipt,
        expected_generation=expected_generation,
    )
    for item in supplemental:
        item._consumed = True
    context._consumed = True
    return receipt


def _valid_branch(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and BRANCH_NAME.fullmatch(value)
        and ".." not in value
        and "//" not in value
        and not value.endswith(("/", ".", ".lock"))
        and "@{" not in value
    )


def _normalize_lease_path(value: Any) -> str | None:
    return normalize_scope(value)


def _path_owned(changed_path: str, owned_paths: list[str]) -> bool:
    return any(scope_owns(owned, changed_path) for owned in owned_paths)


def _validate_transition_evidence(
    target: str, evidence: Mapping[str, Any] | None
) -> None:
    supplied = evidence or {}
    required = TRANSITION_EVIDENCE.get(target, frozenset())
    if set(supplied) != required:
        missing = sorted(required.difference(supplied))
        unexpected = sorted(set(supplied).difference(required))
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ValueError("E_STATE_EVIDENCE: " + "; ".join(details))
    if not required:
        return
    if target == "ready" and supplied.get("preflight_ok") is not True:
        raise ValueError("E_STATE_EVIDENCE: preflight must pass")
    if (
        target == "verifying"
        and supplied.get("implementation_complete") is not True
    ):
        raise ValueError("E_STATE_EVIDENCE: implementation is not complete")
    if target == "review_ready":
        if supplied.get("gates_ok") is not True:
            raise ValueError("E_STATE_EVIDENCE: gates must pass")
        documentation = supplied.get("documentation_decision")
        if (
            not isinstance(documentation, str)
            or SHA256_DIGEST.fullmatch(documentation) is None
        ):
            raise ValueError(
                "E_STATE_EVIDENCE: documentation decision digest is required"
            )
    if target in {"committed", "pushed", "merged", "base_verified"}:
        field = next(iter(required))
        value = supplied.get(field)
        if not isinstance(value, str) or GIT_OBJECT_ID.fullmatch(value) is None:
            raise ValueError(f"E_STATE_EVIDENCE: invalid {field}")
    if target == "pr_draft":
        pull_request = supplied.get("pull_request")
        if (
            not isinstance(pull_request, Mapping)
            or set(pull_request) != {"number", "url", "head_commit"}
            or not isinstance(pull_request.get("number"), int)
            or isinstance(pull_request.get("number"), bool)
            or int(pull_request.get("number", 0)) <= 0
            or not isinstance(pull_request.get("url"), str)
            or not str(pull_request.get("url")).startswith("https://")
            or not isinstance(pull_request.get("head_commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(pull_request.get("head_commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: invalid pull request evidence")
    if target == "pr_ready":
        checks = supplied.get("checks_ok")
        if (
            not isinstance(checks, Mapping)
            or set(checks) != {"ok", "head_commit"}
            or checks.get("ok") is not True
            or not isinstance(checks.get("head_commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(checks.get("head_commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: required checks must pass")
    if target == "release_pending":
        manifest = supplied.get("release_manifest")
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != {"digest", "commit"}
            or not isinstance(manifest.get("digest"), str)
            or SHA256_DIGEST.fullmatch(str(manifest.get("digest"))) is None
            or not isinstance(manifest.get("commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(manifest.get("commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: release manifest digest is required")
    if target == "released":
        build = supplied.get("provider_build")
        if (
            not isinstance(build, Mapping)
            or set(build) != {"provider", "build_id", "commit"}
            or not all(
                isinstance(build.get(field), str) and bool(build.get(field))
                for field in ("provider", "build_id")
            )
            or not isinstance(build.get("commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(build.get("commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: provider build proof is invalid")
    if target == "observed":
        observation = supplied.get("observation")
        if (
            not isinstance(observation, Mapping)
            or set(observation) != {"status", "reference"}
            or observation.get("status") not in {"healthy", "degraded"}
            or not isinstance(observation.get("reference"), str)
            or not observation.get("reference")
        ):
            raise ValueError("E_STATE_EVIDENCE: observation proof is invalid")


def transition_allowed(source: str, target: str) -> bool:
    return target in LEGAL_TRANSITIONS.get(source, frozenset())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


@contextmanager
def _common_lease_lock(common_dir: Path):
    """Serialize lease scans across every worktree sharing one common Git dir."""

    lock_dir = common_dir / "codex-control-plane" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_dir / "leases.lock", flags, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        lock_proof = object.__new__(LeaseLockToken)
        lock_proof._active = True
        lock_proof.common_dir = common_dir.resolve()
        lock_proof.descriptor = handle.fileno()
        try:
            yield lock_proof
        finally:
            lock_proof._active = False
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _common_git_dir(state_dir: Path) -> Path:
    marker = state_dir / "commondir"
    if marker.is_symlink():
        raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: commondir is a symlink")
    if marker.is_file():
        if marker.stat().st_size > 4096:
            raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: commondir exceeds cap")
        raw = Path(marker.read_text(encoding="utf-8").strip())
        common = (state_dir / raw).resolve() if not raw.is_absolute() else raw.resolve()
        if not common.is_dir() or common.is_symlink():
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: common Git dir is unavailable"
            )
        return common
    return state_dir.resolve()


def _registered_git_dir(state_dir: Path) -> bool:
    return (state_dir / "commondir").is_file() or (
        (state_dir / "config").is_file() and (state_dir / "HEAD").is_file()
    )


@contextmanager
def _lease_guard(state_dir: Path):
    """Lock the common registry while mutating this worktree's lease directory."""

    common_dir = _common_git_dir(state_dir)
    with _common_lease_lock(common_dir):
        leases_dir = state_dir / "codex-control-plane" / "leases"
        leases_dir.mkdir(parents=True, exist_ok=True)
        yield leases_dir


@contextmanager
def _task_guard(state_dir: Path, task_id: str):
    """Serialize one task state without taking the common lease flock."""

    if not validate_task_id(task_id):
        raise ValueError("E_TASK_ID: unsafe task ID")
    lock_dir = state_dir / "codex-control-plane" / "locks" / "tasks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_dir / f"{task_id}.lock", flags, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LeaseLockToken:
    """Opaque proof that the common-dir lease flock is currently held."""

    __slots__ = ("_active", "common_dir", "descriptor")

    def __new__(cls, *_: object, **__: object) -> "LeaseLockToken":
        raise TypeError("LeaseLockToken is internal")


def _valid_lease_lock_token(token: object, common_dir: Path) -> bool:
    descriptor = getattr(token, "descriptor", None)
    if (
        type(token) is not LeaseLockToken
        or getattr(token, "_active", None) is not True
        or getattr(token, "common_dir", None) != common_dir.resolve()
        or not isinstance(descriptor, int)
        or descriptor < 0
    ):
        return False
    lock_path = (
        common_dir.resolve()
        / "codex-control-plane"
        / "locks"
        / "leases.lock"
    )
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        token_stat = os.fstat(descriptor)
        probe_descriptor = os.open(lock_path, flags)
    except (AttributeError, OSError):
        return False
    try:
        probe_stat = os.fstat(probe_descriptor)
        if (
            not stat.S_ISREG(token_stat.st_mode)
            or (token_stat.st_dev, token_stat.st_ino)
            != (probe_stat.st_dev, probe_stat.st_ino)
        ):
            return False
        try:
            fcntl.flock(
                probe_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError:
            pass
        else:
            fcntl.flock(probe_descriptor, fcntl.LOCK_UN)
            return False
        try:
            fcntl.flock(
                descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError:
            return False
        return True
    except OSError:
        return False
    finally:
        os.close(probe_descriptor)


def _loaded_runtime_digest() -> str:
    """Bind state ownership to the exact imported runtime package bytes."""

    package = Path(__file__).resolve().parent
    hasher = sha256()
    modules = sorted(package.glob("*.py"))
    if not modules:
        raise ValueError("E_FOREIGN_RUNTIME_STATE: runtime package is empty")
    for module in modules:
        if module.is_symlink() or not module.is_file():
            raise ValueError(
                "E_FOREIGN_RUNTIME_STATE: runtime package is unsafe"
            )
        hasher.update(module.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(module.read_bytes())
        hasher.update(b"\0")
    return f"sha256:{hasher.hexdigest()}"


class TaskStore:
    """Persist compact task state beneath the worktree-specific Git dir."""

    def __init__(
        self, state_dir: Path, *, runtime_digest: str | None = None
    ) -> None:
        self.state_dir = state_dir
        self.root = state_dir / "codex-control-plane" / "tasks"
        self.runtime_digest = runtime_digest or _loaded_runtime_digest()
        if SHA256_DIGEST.fullmatch(self.runtime_digest) is None:
            raise ValueError("E_FOREIGN_RUNTIME_STATE: runtime digest is invalid")

    def _path(self, task_id: str) -> Path:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        return self.root / f"{task_id}.json"

    def _read(self, task_id: str) -> dict[str, Any]:
        try:
            state = json.loads(
                self._path(task_id).read_text(encoding="utf-8")
            )
        except FileNotFoundError as error:
            raise ValueError("E_TASK_NOT_FOUND: task state does not exist") from error
        if not isinstance(state, dict):
            raise ValueError("E_FOREIGN_RUNTIME_STATE: task state is invalid")
        self._assert_runtime_owner(state)
        return state

    def _assert_runtime_owner(self, state: Mapping[str, Any]) -> None:
        owner = state.get("owner_runtime_digest")
        verification_owner = state.get("verification_runtime_digest")
        if (
            not isinstance(owner, str)
            or SHA256_DIGEST.fullmatch(owner) is None
            or owner != self.runtime_digest
            or (
                verification_owner is not None
                and verification_owner != self.runtime_digest
            )
        ):
            raise ValueError(
                "E_FOREIGN_RUNTIME_STATE: task state belongs to another "
                "immutable runtime"
            )

    def start(
        self,
        task_id: str,
        *,
        outcome: str,
        branch: str,
        task_digest: str,
        decision_digest: str,
        verification_bootstrap: object | None = None,
    ) -> dict[str, Any]:
        with _task_guard(self.state_dir, task_id):
            return self._start_locked(
                task_id,
                outcome=outcome,
                branch=branch,
                task_digest=task_digest,
                decision_digest=decision_digest,
                verification_bootstrap=verification_bootstrap,
            )

    def _start_locked(
        self,
        task_id: str,
        *,
        outcome: str,
        branch: str,
        task_digest: str,
        decision_digest: str,
        verification_bootstrap: object | None = None,
    ) -> dict[str, Any]:
        if outcome not in OUTCOME_LIMITS:
            raise ValueError("E_STATE_OUTCOME: unsupported requested outcome")
        if not _valid_branch(branch):
            raise ValueError("E_STATE_BRANCH: invalid branch")
        if (
            not isinstance(task_digest, str)
            or SHA256_DIGEST.fullmatch(task_digest) is None
            or not isinstance(decision_digest, str)
            or SHA256_DIGEST.fullmatch(decision_digest) is None
        ):
            raise ValueError("E_STATE_DIGEST: task and decision digests are required")
        path = self._path(task_id)
        if path.exists():
            existing = self._read(task_id)
            self._assert_runtime_owner(existing)
            if (
                existing["outcome"] == outcome
                and existing["branch"] == branch
                and existing.get("task_digest") == task_digest
                and existing.get("decision_digest") == decision_digest
            ):
                return existing
            raise ValueError("E_TASK_EXISTS: task ID already has different state")
        state = {
            "schema_version": 1,
            "task_id": task_id,
            "state": "framed",
            "resume_state": None,
            "outcome": outcome,
            "branch": branch,
            "task_digest": task_digest,
            "decision_digest": decision_digest,
            "owner_runtime_digest": self.runtime_digest,
            "block_reason": None,
            "evidence": {},
            "generation": 0,
            "revision": 0,
            "updated_at": _utc_now(),
        }
        if verification_bootstrap is not None:
            if (
                type(verification_bootstrap) is not VerificationTaskBootstrap
                or not _runtime_host_object_is_live(
                    verification_bootstrap, "verification_task_bootstrap"
                )
                or verification_bootstrap._consumed
                or verification_bootstrap.task["task_id"] != task_id
                or verification_bootstrap.task_digest != task_digest
                or verification_bootstrap.runtime_digest
                != self.runtime_digest
            ):
                raise ValueError(
                    "E_VERIFICATION_BOOTSTRAP: bootstrap binding is invalid"
                )
            if not _consume_runtime_host_object(
                verification_bootstrap, "verification_task_bootstrap"
            ):
                raise ValueError(
                    "E_VERIFICATION_BOOTSTRAP: bootstrap is not host-issued"
                )
            verification_bootstrap._consumed = True
            state.update(
                {
                    "verification_profile": verification_bootstrap.profile,
                    "verification_profile_digest": (
                        verification_bootstrap.profile_digest
                    ),
                    "verification_runtime_digest": (
                        verification_bootstrap.runtime_digest
                    ),
                    "verification_target_digest": (
                        verification_bootstrap.target_digest
                    ),
                    "verification_content_trust": (
                        verification_bootstrap.content_trust
                    ),
                    "verification_authority_digest": (
                        verification_bootstrap.authority_digest
                    ),
                    "verification_bootstrap_digest": (
                        verification_bootstrap.bootstrap_digest
                    ),
                    "session_id": verification_bootstrap.session_id,
                }
            )
        _atomic_json(path, state)
        return state

    def status(self, task_id: str) -> dict[str, Any]:
        return self._read(task_id)

    def transition(
        self,
        task_id: str,
        target: str,
        *,
        reason: str | None = None,
        evidence: object | None = None,
        current_branch: str,
    ) -> dict[str, Any]:
        with _task_guard(self.state_dir, task_id):
            return self._transition_locked(
                task_id,
                target,
                reason=reason,
                evidence=evidence,
                current_branch=current_branch,
            )

    def _transition_locked(
        self,
        task_id: str,
        target: str,
        *,
        reason: str | None = None,
        evidence: object | None = None,
        current_branch: str,
    ) -> dict[str, Any]:
        state = self._read(task_id)
        self._assert_runtime_owner(state)
        if state.get("verification_profile") is not None and target in {
            "review_ready",
            "blocked",
        }:
            raise ValueError(
                "E_VERIFICATION_EVIDENCE: verifier completion and failure "
                "require specialized APIs"
            )
        if state.get("resume_forbidden") or str(state.get("state", "")).startswith(
            "finalizing_"
        ):
            raise ValueError(
                "E_STATE_FINALIZING: task cannot transition or resume"
            )
        if current_branch != state["branch"]:
            raise ValueError("E_STATE_BRANCH: current branch differs from task branch")
        source = str(state["state"])
        limit = OUTCOME_LIMITS[str(state["outcome"])]
        if target not in {"blocked", "closed"}:
            if ORDERED_STATES.index(target) > ORDERED_STATES.index(limit):
                raise ValueError("E_STATE_OUTCOME: target exceeds requested outcome")
        if not transition_allowed(source, target):
            raise ValueError(f"E_STATE_TRANSITION: {source} -> {target} is illegal")
        if target == "committed":
            if (
                not isinstance(evidence, ValidatedLocalGitObservation)
                or evidence.task_digest != state["task_digest"]
                or evidence.branch != state["branch"]
                or evidence.target_state != target
            ):
                raise ValueError(
                    "E_LIFECYCLE_OBSERVATION_REQUIRED: committed requires "
                    "ValidatedLocalGitObservation"
                )
            supplied = consume_lifecycle_observation(evidence)
        elif target in {
            "pushed",
            "pr_draft",
            "pr_ready",
            "merged",
            "base_verified",
        }:
            if (
                not isinstance(evidence, ValidatedGitHubObservation)
                or evidence.task_digest != state["task_digest"]
                or evidence.branch != state["branch"]
                or evidence.target_state != target
            ):
                raise ValueError(
                    "E_LIFECYCLE_OBSERVATION_REQUIRED: remote transition "
                    "requires ValidatedGitHubObservation"
                )
            supplied = consume_lifecycle_observation(evidence)
        elif target in {"release_pending", "released", "observed"}:
            if (
                not isinstance(evidence, ValidatedReleaseProviderObservation)
                or evidence.task_digest != state["task_digest"]
                or evidence.branch != state["branch"]
                or evidence.target_state != target
            ):
                raise ValueError(
                    "E_LIFECYCLE_OBSERVATION_REQUIRED: release transition "
                    "requires ValidatedReleaseProviderObservation"
                )
            supplied = consume_lifecycle_observation(evidence)
        else:
            if evidence is not None and not isinstance(evidence, Mapping):
                raise ValueError(
                    "E_STATE_EVIDENCE: evidence must use the expected contract"
                )
            supplied = evidence or {}
        _validate_transition_evidence(target, supplied)
        prior = state.get("evidence", {})
        if target == "pushed" and supplied.get("remote_head") != prior.get(
            "committed", {}
        ).get("commit"):
            raise ValueError(
                "E_STATE_EVIDENCE: remote head must equal committed head"
            )
        if target == "pr_draft" and supplied.get("pull_request", {}).get(
            "head_commit"
        ) != prior.get("pushed", {}).get("remote_head"):
            raise ValueError(
                "E_STATE_EVIDENCE: pull request must target the pushed head"
            )
        if target == "pr_ready" and supplied.get("checks_ok", {}).get(
            "head_commit"
        ) != prior.get("pushed", {}).get("remote_head"):
            raise ValueError(
                "E_STATE_EVIDENCE: checks must correspond to pushed head"
            )
        if target == "base_verified" and supplied.get(
            "remote_base"
        ) != prior.get("merged", {}).get("merge_commit"):
            raise ValueError(
                "E_STATE_EVIDENCE: remote base must contain the merge commit"
            )
        if target == "release_pending" and supplied.get(
            "release_manifest", {}
        ).get("commit") != prior.get("base_verified", {}).get("remote_base"):
            raise ValueError(
                "E_STATE_EVIDENCE: release manifest must bind verified base"
            )
        if target == "released" and supplied.get("provider_build", {}).get(
            "commit"
        ) != prior.get("release_pending", {}).get(
            "release_manifest", {}
        ).get("commit"):
            raise ValueError(
                "E_STATE_EVIDENCE: provider build must use manifest commit"
            )
        if target == "blocked":
            state["resume_state"] = source
            state["block_reason"] = reason or "unspecified"
        state["state"] = target
        if supplied:
            state["evidence"][target] = dict(supplied)
        state["generation"] = int(state.get("generation", 0)) + 1
        state["updated_at"] = _utc_now()
        _atomic_json(self._path(task_id), state)
        return state

    def resume(self, task_id: str, *, current_branch: str) -> dict[str, Any]:
        with _task_guard(self.state_dir, task_id):
            return self._resume_locked(
                task_id, current_branch=current_branch
            )

    def _resume_locked(
        self, task_id: str, *, current_branch: str
    ) -> dict[str, Any]:
        state = self._read(task_id)
        self._assert_runtime_owner(state)
        if current_branch != state["branch"]:
            raise ValueError("E_STATE_BRANCH: current branch differs from task branch")
        if (
            state.get("resume_forbidden")
            or state["state"] != "blocked"
            or not state.get("resume_state")
        ):
            raise ValueError("E_STATE_RESUME: task is not resumable")
        state["state"] = state["resume_state"]
        state["resume_state"] = None
        state["block_reason"] = None
        state["generation"] = int(state.get("generation", 0)) + 1
        state["updated_at"] = _utc_now()
        _atomic_json(self._path(task_id), state)
        return state

    def _complete_verification(
        self,
        *,
        receipt: VerificationExecutionReceipt,
        expected_generation: int,
    ) -> dict[str, Any]:
        """CAS one in-memory closed verifier aggregate into review_ready."""

        if (
            type(receipt) is not VerificationExecutionReceipt
            or receipt.generation != expected_generation
        ):
            raise ValueError(
                "E_VERIFICATION_EVIDENCE: typed aggregate is required"
            )
        common_dir = _common_git_dir(self.state_dir)
        with _common_lease_lock(common_dir):
            with _task_guard(self.state_dir, receipt.task_id):
                state = self._read(receipt.task_id)
                self._assert_runtime_owner(state)
                lease = self._read_owner_lease(receipt.task_id)
                core = {
                    key: value
                    for key, value in receipt.__dict__.items()
                    if key != "receipt_digest"
                }
                if (
                    state.get("state") != "verifying"
                    or state.get("generation") != expected_generation
                    or state.get("task_digest") != receipt.task_digest
                    or state.get("verification_profile") != receipt.profile
                    or state.get("verification_profile_digest")
                    != receipt.profile_digest
                    or state.get("session_id") != receipt.session_id
                    or lease is None
                    or lease.get("lease_digest") != receipt.lease_digest
                    or receipt.receipt_digest != contract_digest(core)
                    or _git_head(Path(str(lease.get("worktree", ""))))
                    != receipt.head
                ):
                    raise ValueError(
                        "E_STATE_CAS: verification task changed before publish"
                    )
                state["state"] = "review_ready"
                state["generation"] = expected_generation + 1
                state["evidence"]["review_ready"] = {
                    "gates_ok": True,
                    "documentation_decision": receipt.receipt_digest,
                }
                state["verification_receipt_digest"] = (
                    receipt.receipt_digest
                )
                state["updated_at"] = _utc_now()
                _atomic_json(self._path(receipt.task_id), state)
                return state

    def close(self, task_id: str, *, current_branch: str) -> dict[str, Any]:
        state = self._read(task_id)
        self._assert_runtime_owner(state)
        if current_branch != state["branch"]:
            raise ValueError("E_STATE_BRANCH: current branch differs from task branch")
        terminal = OUTCOME_LIMITS[str(state["outcome"])]
        if state["state"] != terminal:
            raise ValueError(
                f"E_STATE_CLOSE: expected {terminal}, observed {state['state']}"
            )
        return self._finalize_writer(
            task_id,
            expected_generation=int(state.get("generation", 0)),
            marker_state="finalizing_close",
            destination="closed",
            reason_code=None,
        )

    def suspend_for_reframe(
        self,
        task_id: str,
        *,
        expected_generation: int,
        current_branch: str,
    ) -> dict[str, Any]:
        state = self._read(task_id)
        self._assert_runtime_owner(state)
        if (
            state.get("branch") != current_branch
            or state.get("generation") != expected_generation
            or state.get("state") in {"closed", "merged", "base_verified"}
        ):
            raise ValueError("E_REFRAME_REQUIRED: task cannot be suspended")
        return self._finalize_writer(
            task_id,
            expected_generation=expected_generation,
            marker_state="finalizing_suspend",
            destination="blocked",
            reason_code="E_REFRAME_REQUIRED",
        )

    def abort_verification(
        self,
        *,
        task_id: str,
        expected_generation: int,
        task_digest: str,
        repo: Path | str,
        worktree: Path | str,
        branch: str,
        session_id: str,
        lease_digest: str,
        reason_code: str,
        clock: object | None = None,
    ) -> dict[str, Any]:
        del repo, clock
        closed_reasons = {
            "E_VERIFICATION_FAIL",
            "E_VERIFICATION_UNKNOWN",
            "E_VERIFICATION_MUTATION",
            "E_VERIFICATION_PROFILE",
            "E_VERIFICATION_EVIDENCE",
            "E_VERIFICATION_HOST_ISOLATION",
        }
        state = self._read(task_id)
        self._assert_runtime_owner(state)
        if (
            state.get("state") not in {"implementing", "verifying"}
            or state.get("generation") != expected_generation
            or state.get("task_digest") != task_digest
            or state.get("branch") != branch
            or reason_code not in closed_reasons
        ):
            raise ValueError(
                "E_VERIFICATION_ABORT_BINDING: verifier binding is invalid"
            )
        lease = self._read_owner_lease(task_id)
        if (
            lease is None
            or lease.get("worktree") != str(Path(worktree).resolve())
            or lease.get("branch") != branch
            or lease.get("session_id") != session_id
            or lease.get("lease_digest") != lease_digest
        ):
            raise ValueError(
                "E_VERIFICATION_ABORT_BINDING: verifier lease is invalid"
            )
        result = self._finalize_writer(
            task_id,
            expected_generation=expected_generation,
            marker_state="finalizing_verification_abort",
            destination="blocked",
            reason_code=reason_code,
        )
        result["verification_aborted"] = True
        _atomic_json(self._path(task_id), result)
        return result

    def _read_owner_lease(self, task_id: str) -> dict[str, Any] | None:
        path = (
            self.state_dir
            / "codex-control-plane"
            / "leases"
            / f"{task_id}.json"
        )
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: owner lease is unreadable"
            ) from error
        if not isinstance(value, dict):
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: owner lease is invalid"
            )
        return value

    def _finalize_writer(
        self,
        task_id: str,
        *,
        expected_generation: int,
        marker_state: str,
        destination: str,
        reason_code: str | None,
    ) -> dict[str, Any]:
        """Publish marker, release exact lease, then publish terminal state."""

        common_dir = _common_git_dir(self.state_dir)
        state_path = self._path(task_id)
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                self._assert_runtime_owner(state)
                if state.get("generation") != expected_generation:
                    raise ValueError("E_STATE_CAS: task generation changed")
                if state.get("resume_forbidden"):
                    raise ValueError(
                        "E_STATE_FINALIZING: task is already finalizing"
                    )
                lease = self._read_owner_lease(task_id)
                if lease is None:
                    if marker_state != "finalizing_close":
                        raise ValueError(
                            "E_LEASE_NOT_FOUND: writer finalization requires a lease"
                        )
                    state.update(
                        {
                            "state": destination,
                            "resume_state": None,
                            "resume_forbidden": destination == "blocked",
                            "block_reason": reason_code,
                            "generation": expected_generation + 1,
                            "updated_at": _utc_now(),
                        }
                    )
                    _atomic_json(state_path, state)
                    return state
                marker = dict(state)
                marker.update(
                    {
                        "state": marker_state,
                        "resume_state": None,
                        "resume_forbidden": True,
                        "finalization": {
                            "destination": destination,
                            "reason_code": reason_code,
                            "prior_generation": expected_generation,
                            "task_id": task_id,
                            "worktree": lease.get("worktree"),
                            "branch": lease.get("branch"),
                            "session_id": lease.get("session_id"),
                            "policy_digest": lease.get("policy_digest"),
                            "lease_digest": lease.get("lease_digest"),
                        },
                        "updated_at": _utc_now(),
                    }
                )
                _atomic_json(state_path, marker)
            TaskLease._release_locked(
                token,
                state_dir=self.state_dir,
                task_id=task_id,
                worktree=str(lease["worktree"]),
                branch=str(lease["branch"]),
                session_id=str(lease["session_id"]),
                policy_digest=str(lease["policy_digest"]),
                lease_digest=str(lease["lease_digest"]),
            )
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                self._assert_runtime_owner(current)
                if (
                    current.get("state") != marker_state
                    or current.get("finalization") != marker["finalization"]
                ):
                    raise ValueError(
                        "E_STATE_CAS: finalization marker changed"
                    )
                current.update(
                    {
                        "state": destination,
                        "resume_state": None,
                        "resume_forbidden": destination == "blocked",
                        "block_reason": reason_code,
                        "generation": expected_generation + 1,
                        "verification_aborted": (
                            marker_state == "finalizing_verification_abort"
                        ),
                        "updated_at": _utc_now(),
                    }
                )
                current.pop("finalization", None)
                _atomic_json(state_path, current)
                return current

    def recover_writer_finalization(self, task_id: str) -> dict[str, Any]:
        """Complete one durable writer finalization without opaque wrappers."""

        common_dir = _common_git_dir(self.state_dir)
        state_path = self._path(task_id)
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                self._assert_runtime_owner(state)
                marker_state = str(state.get("state"))
                if marker_state not in {
                    "finalizing_close",
                    "finalizing_suspend",
                    "finalizing_verification_abort",
                    "finalizing_abandon",
                }:
                    raise ValueError(
                        "E_STATE_RECOVERY: no writer finalization is pending"
                    )
                finalization = state.get("finalization")
                if not isinstance(finalization, Mapping):
                    if marker_state == "finalizing_abandon":
                        lease = self._read_owner_lease(task_id)
                        if lease is None:
                            raise ValueError(
                                "E_STATE_RECOVERY: abandoned owner binding is absent"
                            )
                        finalization = {
                            "destination": "blocked",
                            "reason_code": "E_LEASE_OWNER_ABANDONED",
                            "prior_generation": int(state.get("generation", 0)),
                            **{
                                key: lease.get(key)
                                for key in (
                                    "task_id",
                                    "worktree",
                                    "branch",
                                    "session_id",
                                    "policy_digest",
                                    "lease_digest",
                                )
                            },
                        }
                    else:
                        raise ValueError(
                            "E_STATE_RECOVERY: finalization binding is absent"
                        )
            TaskLease._release_locked(
                token,
                state_dir=self.state_dir,
                task_id=str(finalization["task_id"]),
                worktree=str(finalization["worktree"]),
                branch=str(finalization["branch"]),
                session_id=str(finalization["session_id"]),
                policy_digest=str(finalization["policy_digest"]),
                lease_digest=str(finalization["lease_digest"]),
            )
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                self._assert_runtime_owner(current)
                if current.get("state") != marker_state:
                    raise ValueError("E_STATE_CAS: recovery marker changed")
                destination = str(finalization["destination"])
                current.update(
                    {
                        "state": destination,
                        "resume_state": None,
                        "resume_forbidden": destination == "blocked",
                        "block_reason": finalization.get("reason_code"),
                        "generation": int(
                            finalization.get(
                                "prior_generation",
                                current.get("generation", 0),
                            )
                        )
                        + 1,
                        "verification_aborted": (
                            marker_state == "finalizing_verification_abort"
                        ),
                        "updated_at": _utc_now(),
                    }
                )
                current.pop("finalization", None)
                current.pop("finalizing_lease_digest", None)
                _atomic_json(state_path, current)
                return current

    @staticmethod
    def recover_verification_abort(
        *,
        task_id: str,
        state_dir: Path,
        common_dir: Path,
        clock: object | None = None,
    ) -> dict[str, Any]:
        del clock
        if _common_git_dir(state_dir) != Path(common_dir).resolve():
            raise ValueError(
                "E_STATE_RECOVERY: common Git directory binding changed"
            )
        state = TaskStore(state_dir).recover_writer_finalization(task_id)
        if not state.get("verification_aborted"):
            raise ValueError(
                "E_STATE_RECOVERY: marker was not a verification abort"
            )
        return state

    def start_revision(
        self,
        task_id: str,
        *,
        expected_generation: int,
        reason: str,
        observation: object,
        worktree_inventory: object,
        worktree: str,
        session_id: str,
        policy_digest: str,
        scope_paths: list[str],
        current_branch: str,
    ) -> dict[str, Any]:
        """Start one review round only after reacquiring an exact writer lease."""

        if reason == "base_advanced":
            raise ValueError(
                "E_REFRAME_REQUIRED: base advance requires a new task and lease"
            )
        if reason not in {"review_feedback", "checks_failed"}:
            raise ValueError("E_REVISION_REASON: unsupported review reason")
        state = self._read(task_id)
        self._assert_runtime_owner(state)
        if (
            state.get("state") not in {"pr_draft", "pr_ready"}
            or state.get("branch") != current_branch
            or state.get("generation") != expected_generation
            or not isinstance(observation, ValidatedGitHubObservation)
            or observation.task_digest != state.get("task_digest")
            or observation.branch != current_branch
            or observation.target_state != "implementing"
            or not isinstance(
                worktree_inventory, ValidatedWorktreeInventoryObservation
            )
        ):
            raise ValueError(
                "E_REVISION_BINDING: task, observation, or generation mismatch"
            )
        revision_evidence = consume_lifecycle_observation(observation)
        if set(revision_evidence) != {
            "pull_request_number",
            "prior_head",
            "reason",
            "observation_digest",
        }:
            raise ValueError("E_REVISION_EVIDENCE: closed revision evidence required")
        prior_pr = (
            state.get("evidence", {})
            .get("pr_draft", {})
            .get("pull_request", {})
        )
        prior_head = (
            state.get("evidence", {}).get("pushed", {}).get("remote_head")
        )
        if (
            revision_evidence.get("reason") != reason
            or revision_evidence.get("prior_head") != prior_head
            or revision_evidence.get("pull_request_number")
            != prior_pr.get("number")
            or not isinstance(
                revision_evidence.get("observation_digest"), str
            )
            or SHA256_DIGEST.fullmatch(
                str(revision_evidence.get("observation_digest"))
            )
            is None
        ):
            raise ValueError(
                "E_REVISION_EVIDENCE: revision does not match current PR"
            )
        state_path = self._path(task_id)
        common_dir = _common_git_dir(self.state_dir)
        prior_state = json.loads(json.dumps(state))
        history = list(state.get("pull_request_history", []))
        history.append(
            {
                "revision": int(state.get("revision", 0)),
                "number": prior_pr["number"],
                "head": prior_head,
                "reason": reason,
                "observation_digest": revision_evidence[
                    "observation_digest"
                ],
            }
        )
        next_evidence = dict(state.get("evidence", {}))
        for key in ("committed", "pushed", "pr_draft", "pr_ready"):
            next_evidence.pop(key, None)
        next_state = {
            "state": "implementing",
            "resume_state": None,
            "resume_forbidden": False,
            "block_reason": None,
            "generation": expected_generation + 1,
            "revision": int(state.get("revision", 0)) + 1,
            "revision_reason": reason,
            "pull_request": dict(prior_pr),
            "pull_request_history": history,
            "evidence": next_evidence,
        }
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                self._assert_runtime_owner(current)
                if (
                    current.get("generation") != expected_generation
                    or current.get("state") != state.get("state")
                ):
                    raise ValueError(
                        "E_STATE_CAS: task changed before revision"
                    )
                marker = json.loads(json.dumps(current))
                marker.update(
                    {
                        "state": "finalizing_revision",
                        "resume_forbidden": True,
                        "revision_reason": reason,
                        "prior_state": current["state"],
                        "prior_generation": expected_generation,
                        "revision_finalization": {
                            "prior_state": current["state"],
                            "prior_generation": expected_generation,
                            "lease": {
                                "task_id": task_id,
                                "worktree": str(Path(worktree).resolve()),
                                "branch": current_branch,
                                "session_id": session_id,
                                "policy_digest": policy_digest,
                            },
                            "next_state": next_state,
                        },
                        "updated_at": _utc_now(),
                    },
                )
                _atomic_json(state_path, marker)
                try:
                    lease = TaskLease._acquire_locked(
                        token,
                        task_id=task_id,
                        worktree=worktree,
                        branch=current_branch,
                        session_id=session_id,
                        policy_digest=policy_digest,
                        scopes=scope_paths,
                        inventory=worktree_inventory,
                    )
                except Exception:
                    _atomic_json(state_path, prior_state)
                    raise
                current.update(
                    {
                        **next_state,
                        "lease_digest": lease["lease_digest"],
                        "updated_at": _utc_now(),
                    }
                )
                current.pop("prior_state", None)
                current.pop("prior_generation", None)
                current.pop("revision_finalization", None)
                _atomic_json(state_path, current)
                return current

    def recover_revision_start(self, task_id: str) -> dict[str, Any]:
        """Complete or revert a durable revision marker without re-observing."""

        common_dir = _common_git_dir(self.state_dir)
        state_path = self._path(task_id)
        with _common_lease_lock(common_dir):
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                self._assert_runtime_owner(state)
                finalization = state.get("revision_finalization")
                if (
                    state.get("state") != "finalizing_revision"
                    or not isinstance(finalization, Mapping)
                    or not isinstance(finalization.get("lease"), Mapping)
                    or not isinstance(finalization.get("next_state"), Mapping)
                ):
                    raise ValueError(
                        "E_REVISION_RECOVERY: revision marker is invalid"
                    )
                lease_binding = dict(finalization["lease"])
                lease = self._read_owner_lease(task_id)
                if lease is None:
                    state.update(
                        {
                            "state": finalization.get("prior_state"),
                            "generation": finalization.get(
                                "prior_generation"
                            ),
                            "resume_forbidden": False,
                            "updated_at": _utc_now(),
                        }
                    )
                    for key in (
                        "prior_state",
                        "prior_generation",
                        "revision_finalization",
                        "revision_reason",
                    ):
                        state.pop(key, None)
                    _atomic_json(state_path, state)
                    return state
                if any(
                    lease.get(key) != value
                    for key, value in lease_binding.items()
                ):
                    raise ValueError(
                        "E_REVISION_RECOVERY: revision lease is inconsistent"
                    )
                next_state = dict(finalization["next_state"])
                if not next_state:
                    raise ValueError(
                        "E_REVISION_RECOVERY: next revision state is absent"
                    )
                state.update(next_state)
                state["lease_digest"] = lease.get("lease_digest")
                state["updated_at"] = _utc_now()
                for key in (
                    "prior_state",
                    "prior_generation",
                    "revision_finalization",
                ):
                    state.pop(key, None)
                _atomic_json(state_path, state)
                return state


class TaskLease:
    """Bind a dirty-work continuation to one exact task identity."""

    @staticmethod
    def acquire(
        state_dir: Path,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        paths: list[str],
        policy_digest: str,
    ) -> dict[str, Any]:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        normalized_paths = [_normalize_lease_path(path) for path in paths]
        if not paths or any(path is None for path in normalized_paths):
            raise ValueError("E_LEASE_PATH: lease paths must be safe repository paths")
        if not _valid_branch(branch) or not validate_task_id(session_id):
            raise ValueError("E_LEASE_IDENTITY: invalid branch or session")
        if (
            not isinstance(policy_digest, str)
            or SHA256_DIGEST.fullmatch(policy_digest) is None
        ):
            raise ValueError("E_LEASE_DIGEST: invalid policy digest")
        payload = {
            "schema_version": 1,
            "task_id": task_id,
            "worktree": str(Path(worktree).resolve()),
            "branch": branch,
            "session_id": session_id,
            "paths": sorted(set(str(path) for path in normalized_paths)),
            "policy_digest": policy_digest,
        }
        payload["lease_digest"] = contract_digest(payload)
        if _registered_git_dir(state_dir):
            common_dir = _common_git_dir(state_dir)
            invocation_id = f"lease-acquire-{uuid4().hex}"
            observation = observe_worktree_inventory(
                canonical_common_git_dir=common_dir,
                invocation_id=invocation_id,
                clock=time.monotonic,
                ttl_seconds=30,
                max_output_bytes=1_048_576,
            )
            inventory = validate_worktree_inventory_observation(
                observation,
                expected_common_git_dir=common_dir,
                expected_invocation_id=invocation_id,
                clock=time.monotonic,
            )
            with _common_lease_lock(common_dir) as token:
                return TaskLease._acquire_locked(
                    token,
                    task_id=task_id,
                    worktree=worktree,
                    branch=branch,
                    session_id=session_id,
                    policy_digest=policy_digest,
                    scopes=paths,
                    inventory=inventory,
                )
        with _lease_guard(state_dir) as leases_dir:
            prior_task_path = (
                state_dir
                / "codex-control-plane"
                / "tasks"
                / f"{task_id}.json"
            )
            if prior_task_path.exists():
                try:
                    prior_task = json.loads(
                        prior_task_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: prior task state "
                        "is unreadable"
                    ) from error
                if (
                    not isinstance(prior_task, Mapping)
                    or prior_task.get("task_id") != task_id
                ):
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: prior task state "
                        "is invalid"
                    )
                if prior_task.get("state") == "closed" or (
                    prior_task.get("resume_forbidden") is True
                    and prior_task.get("state") != "finalizing_revision"
                ):
                    raise ValueError(
                        "E_LEASE_RECOVERY_UNAUTHORIZED: closed or "
                        "finalized task requires a new task identity"
                    )
            path = leases_dir / f"{task_id}.json"
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != payload:
                    raise ValueError(
                        "E_LEASE_MISMATCH: lease belongs to another task identity"
                    )
                return existing
            requested_paths = list(payload["paths"])
            for other_path in leases_dir.glob("*.json"):
                try:
                    other = json.loads(other_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raise ValueError("E_LEASE_INVALID: existing lease is unreadable")
                if other.get("task_id") == task_id:
                    continue
                other_paths = [
                    str(item) for item in other.get("paths", [])
                ]
                overlap = any(
                    scopes_overlap(left, right)
                    for left in requested_paths
                    for right in other_paths
                )
                if overlap:
                    raise ValueError(
                        "E_LEASE_CONFLICT: another task owns an overlapping path"
                    )
            _atomic_json(path, payload)
        return payload

    @staticmethod
    def _acquire_locked(
        lease_lock_token: LeaseLockToken,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        policy_digest: str,
        scopes: list[str],
        inventory: ValidatedWorktreeInventoryObservation,
    ) -> dict[str, Any]:
        """Acquire under an already-held common lock without Git or relocking."""

        if type(lease_lock_token) is not LeaseLockToken:
            raise ValueError("E_LEASE_LOCK: invalid common-dir lease lock token")
        common_dir = lease_lock_token.common_dir.resolve()
        if (
            not _valid_lease_lock_token(lease_lock_token, common_dir)
            or inventory.common_git_dir != str(common_dir)
        ):
            raise ValueError("E_LEASE_LOCK: invalid common-dir lease lock token")
        normalized_scopes = [normalize_scope(item) for item in scopes]
        if (
            not validate_task_id(task_id)
            or not scopes
            or any(item is None for item in normalized_scopes)
            or not _valid_branch(branch)
            or not validate_task_id(session_id)
            or not isinstance(policy_digest, str)
            or SHA256_DIGEST.fullmatch(policy_digest) is None
        ):
            raise ValueError("E_LEASE_IDENTITY: invalid lease proposal")
        records = _consume_worktree_inventory(
            inventory, expected_common_git_dir=common_dir
        )
        canonical_worktree = str(Path(worktree).resolve())
        owner = next(
            (item for item in records if item.worktree == canonical_worktree),
            None,
        )
        if owner is None or owner.branch != branch:
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: owner worktree is not registered"
            )
        payload = {
            "schema_version": 1,
            "task_id": task_id,
            "worktree": canonical_worktree,
            "branch": branch,
            "session_id": session_id,
            "paths": sorted(set(str(item) for item in normalized_scopes)),
            "policy_digest": policy_digest,
        }
        payload["lease_digest"] = contract_digest(payload)
        owner_lease_dir = Path(owner.git_dir) / "codex-control-plane" / "leases"
        owner_path = owner_lease_dir / f"{task_id}.json"
        for record in records:
            prior_task_path = (
                Path(record.git_dir)
                / "codex-control-plane"
                / "tasks"
                / f"{task_id}.json"
            )
            if prior_task_path.exists():
                try:
                    prior_task = json.loads(
                        prior_task_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: prior task state is unreadable"
                    ) from error
                if (
                    not isinstance(prior_task, Mapping)
                    or prior_task.get("task_id") != task_id
                ):
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: prior task state is invalid"
                    )
                if prior_task.get("state") == "closed" or (
                    prior_task.get("resume_forbidden") is True
                    and prior_task.get("state") != "finalizing_revision"
                ):
                    raise ValueError(
                        "E_LEASE_RECOVERY_UNAUTHORIZED: closed or "
                        "finalized task requires a new task identity"
                    )
            lease_dir = Path(record.git_dir) / "codex-control-plane" / "leases"
            if lease_dir.is_symlink():
                raise ValueError(
                    "E_LEASE_OBSERVATION_UNKNOWN: lease directory is a symlink"
                )
            if not lease_dir.exists():
                continue
            try:
                candidates = sorted(lease_dir.glob("*.json"))
            except OSError as error:
                raise ValueError(
                    "E_LEASE_OBSERVATION_UNKNOWN: lease directory is unreadable"
                ) from error
            for candidate in candidates:
                try:
                    other = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: lease is unreadable"
                    ) from error
                semantic = {
                    key: value
                    for key, value in other.items()
                    if key != "lease_digest"
                }
                if (
                    set(other)
                    != {
                        "schema_version",
                        "task_id",
                        "worktree",
                        "branch",
                        "session_id",
                        "paths",
                        "policy_digest",
                        "lease_digest",
                    }
                    or other.get("schema_version") != 1
                    or other.get("lease_digest") != contract_digest(semantic)
                ):
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: lease contract is invalid"
                    )
                if candidate == owner_path and other.get("task_id") == task_id:
                    if other != payload:
                        raise ValueError(
                            "E_LEASE_MISMATCH: lease belongs to another task identity"
                        )
                    return other
                if other.get("task_id") == task_id:
                    raise ValueError(
                        "E_LEASE_MISMATCH: task lease exists in another worktree"
                    )
                other_scopes = other.get("paths")
                if not isinstance(other_scopes, list) or any(
                    normalize_scope(item) != item for item in other_scopes
                ):
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: lease scopes are invalid"
                    )
                if any(
                    scopes_overlap(left, right)
                    for left in payload["paths"]
                    for right in other_scopes
                ):
                    raise ValueError(
                        "E_LEASE_CONFLICT: another task owns an overlapping path"
                    )
        owner_lease_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(owner_path, payload)
        return payload

    @staticmethod
    def release(
        common_dir: Path,
        state_dir: Path,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        policy_digest: str,
        lease_digest: str,
    ) -> dict[str, Any]:
        """Release only the exact owner lease and make retries idempotent."""

        canonical_common = _common_git_dir(state_dir)
        if Path(common_dir).resolve() != canonical_common:
            raise ValueError("E_LEASE_LOCK: common Git dir does not match owner")
        with _common_lease_lock(canonical_common) as token:
            return TaskLease._release_locked(
                token,
                state_dir=state_dir,
                task_id=task_id,
                worktree=worktree,
                branch=branch,
                session_id=session_id,
                policy_digest=policy_digest,
                lease_digest=lease_digest,
            )

    @staticmethod
    def _release_locked(
        lease_lock_token: LeaseLockToken,
        *,
        state_dir: Path,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        policy_digest: str,
        lease_digest: str,
    ) -> dict[str, Any]:
        """Release under an existing common lock without reacquiring it."""

        common_dir = _common_git_dir(state_dir)
        if (
            not _valid_lease_lock_token(lease_lock_token, common_dir)
        ):
            raise ValueError("E_LEASE_LOCK: invalid release lock token")
        if (
            not validate_task_id(task_id)
            or not _valid_branch(branch)
            or not validate_task_id(session_id)
            or SHA256_DIGEST.fullmatch(policy_digest) is None
            or SHA256_DIGEST.fullmatch(lease_digest) is None
        ):
            raise ValueError("E_LEASE_MISMATCH: invalid release identity")
        expected = {
            "task_id": task_id,
            "worktree": str(Path(worktree).resolve()),
            "branch": branch,
            "session_id": session_id,
            "policy_digest": policy_digest,
            "lease_digest": lease_digest,
        }
        lease_path = (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{task_id}.json"
        )
        tombstone_path = (
            state_dir
            / "codex-control-plane"
            / "lease-tombstones"
            / f"{task_id}.json"
        )
        tombstone: dict[str, Any] | None = None
        if tombstone_path.exists():
            try:
                tombstone = json.loads(
                    tombstone_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    "E_LEASE_OBSERVATION_UNKNOWN: release tombstone is unreadable"
                ) from error
            if any(tombstone.get(key) != value for key, value in expected.items()):
                raise ValueError(
                    "E_LEASE_MISMATCH: tombstone belongs to another owner"
                )
        if not lease_path.exists():
            if tombstone is None:
                raise ValueError("E_LEASE_NOT_FOUND: owner lease is unavailable")
            return {
                "schema_version": 1,
                "task_id": task_id,
                "lease_digest": lease_digest,
                "released": True,
                "idempotent": True,
                "tombstone_digest": contract_digest(tombstone),
            }
        try:
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: owner lease is unreadable"
            ) from error
        if any(lease.get(key) != value for key, value in expected.items()):
            raise ValueError("E_LEASE_MISMATCH: release identity does not match")
        semantic = {key: value for key, value in lease.items() if key != "lease_digest"}
        if lease.get("lease_digest") != contract_digest(semantic):
            raise ValueError("E_LEASE_DIGEST: owner lease was modified")
        if tombstone is None:
            tombstone = {
                "schema_version": 1,
                **expected,
                "status": "released",
            }
            _atomic_json(tombstone_path, tombstone)
        lease_path.unlink()
        directory = os.open(lease_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {
            "schema_version": 1,
            "task_id": task_id,
            "lease_digest": lease_digest,
            "released": True,
            "idempotent": False,
            "tombstone_digest": contract_digest(tombstone),
        }

    @staticmethod
    def recover_abandoned(
        common_dir: Path,
        state_dir: Path,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        owner_session_id: str,
        policy_digest: str,
        lease_digest: str,
        recovery_authorization: object,
        worktree_inventory: object,
    ) -> dict[str, Any]:
        """Release an explicitly abandoned owner and permanently block its task."""

        if not validate_task_id(task_id):
            raise ValueError("E_LEASE_RECOVERY_UNAUTHORIZED: invalid task ID")
        canonical_common = _common_git_dir(state_dir)
        if Path(common_dir).resolve() != canonical_common:
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: common Git dir mismatch"
            )
        if not hasattr(recovery_authorization, "authorization_id"):
            raise ValueError(
                "E_LEASE_RECOVERY_UNAUTHORIZED: trusted authorization required"
            )
        state_path = (
            state_dir / "codex-control-plane" / "tasks" / f"{task_id}.json"
        )
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
            raise ValueError(
                "E_LEASE_RECOVERY_UNAUTHORIZED: task state is unavailable"
            ) from error
        TaskStore(state_dir)._assert_runtime_owner(state)
        with _common_lease_lock(canonical_common) as token:
            with _task_guard(state_dir, task_id):
                state = json.loads(state_path.read_text(encoding="utf-8"))
                TaskStore(state_dir)._assert_runtime_owner(state)
                lease = (
                    state_dir
                    / "codex-control-plane"
                    / "leases"
                    / f"{task_id}.json"
                )
                if not lease.is_file():
                    raise ValueError(
                        "E_LEASE_RECOVERY_UNAUTHORIZED: owner lease is unavailable"
                    )
            consume_lease_recovery_authorization(
                recovery_authorization,
                task_id=task_id,
                worktree=worktree,
                branch=branch,
                owner_session_id=owner_session_id,
                policy_digest=policy_digest,
                lease_digest=lease_digest,
                inventory=worktree_inventory,
                expected_common_git_dir=canonical_common,
            )
            with _task_guard(state_dir, task_id):
                state = json.loads(state_path.read_text(encoding="utf-8"))
                prior_generation = int(state.get("generation", 0))
                state.update(
                    {
                        "state": "finalizing_abandon",
                        "resume_state": None,
                        "resume_forbidden": True,
                        "block_reason": "E_LEASE_OWNER_ABANDONED",
                        "finalization": {
                            "destination": "blocked",
                            "reason_code": "E_LEASE_OWNER_ABANDONED",
                            "prior_generation": prior_generation,
                            "task_id": task_id,
                            "worktree": str(Path(worktree).resolve()),
                            "branch": branch,
                            "session_id": owner_session_id,
                            "policy_digest": policy_digest,
                            "lease_digest": lease_digest,
                        },
                        "updated_at": _utc_now(),
                    }
                )
                _atomic_json(state_path, state)
            released = TaskLease._release_locked(
                token,
                state_dir=state_dir,
                task_id=task_id,
                worktree=worktree,
                branch=branch,
                session_id=owner_session_id,
                policy_digest=policy_digest,
                lease_digest=lease_digest,
            )
            with _task_guard(state_dir, task_id):
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if (
                    state.get("state") != "finalizing_abandon"
                    or state.get("finalization", {}).get("lease_digest")
                    != lease_digest
                ):
                    raise ValueError(
                        "E_STATE_CAS: abandonment marker changed"
                    )
                state.update(
                    {
                        "state": "blocked",
                        "resume_state": None,
                        "resume_forbidden": True,
                        "block_reason": "E_LEASE_OWNER_ABANDONED",
                        "verification_aborted": False,
                        "generation": prior_generation + 1,
                        "updated_at": _utc_now(),
                    }
                )
                state.pop("finalization", None)
                _atomic_json(state_path, state)
            return {
                **released,
                "recovery_authorization_id": recovery_authorization.authorization_id,
                "task_state": "blocked",
                "resume_forbidden": True,
            }

    @staticmethod
    def validate(
        state_dir: Path,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        policy_digest: str,
        changed_paths: list[str],
    ) -> dict[str, Any]:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        if not isinstance(changed_paths, list):
            raise ValueError(
                "E_LEASE_SCOPE: changed paths are required for continuation"
            )
        with _lease_guard(state_dir) as leases_dir:
            path = leases_dir / f"{task_id}.json"
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    "E_LEASE_NOT_FOUND: continuation lease is unavailable"
                ) from error
        expected = {
            "task_id": task_id,
            "worktree": str(Path(worktree).resolve()),
            "branch": branch,
            "session_id": session_id,
            "policy_digest": policy_digest,
        }
        if any(existing.get(key) != value for key, value in expected.items()):
            raise ValueError("E_LEASE_MISMATCH: continuation identity changed")
        lease_digest = existing.get("lease_digest")
        semantic = {
            key: value for key, value in existing.items() if key != "lease_digest"
        }
        if (
            not isinstance(lease_digest, str)
            or SHA256_DIGEST.fullmatch(lease_digest) is None
            or lease_digest != contract_digest(semantic)
        ):
            raise ValueError("E_LEASE_DIGEST: lease content was modified")
        owned_paths = existing.get("paths")
        if not isinstance(owned_paths, list) or not all(
            _normalize_lease_path(path) == path for path in owned_paths
        ):
            raise ValueError("E_LEASE_PATH: lease contains invalid ownership paths")
        unsafe_changed = [
            path
            for path in changed_paths
            if _normalize_lease_path(path) is None
            or not _path_owned(path, owned_paths)
        ]
        if unsafe_changed:
            raise ValueError(
                "E_LEASE_SCOPE: changed files exceed lease ownership: "
                + ", ".join(sorted(unsafe_changed))
            )
        return existing


def create_resource_receipt(
    *,
    task_id: str,
    decision_digest: str,
    digests: Mapping[str, str],
    used: list[str],
    resource_digests: Mapping[str, str],
    omitted: list[str],
    gates: list[Mapping[str, Any]],
    effects: list[str],
) -> dict[str, Any]:
    """Create compact evidence without retaining source text or tool output."""

    if not validate_task_id(task_id):
        raise ValueError("E_TASK_ID: unsafe task ID")
    if (
        not isinstance(decision_digest, str)
        or SHA256_DIGEST.fullmatch(decision_digest) is None
    ):
        raise ValueError("E_RECEIPT_DIGEST: invalid decision digest")
    if set(digests) != {"task", "policy", "registry", "inventory"} or any(
        not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
        for value in digests.values()
    ):
        raise ValueError("E_RECEIPT_DIGEST: four contract digests are required")
    if not all(
        isinstance(item, str) and RESOURCE_ID.fullmatch(item) is not None
        for item in [*used, *omitted]
    ):
        raise ValueError("E_RECEIPT_RESOURCE: invalid resource ID")
    if (
        any(resource_id not in resource_digests for resource_id in used)
        or any(
            not isinstance(digest, str)
            or SHA256_DIGEST.fullmatch(digest) is None
            for digest in resource_digests.values()
        )
    ):
        raise ValueError(
            "E_RECEIPT_RESOURCE: used resources require locator digests"
        )
    normalized_gates: list[dict[str, Any]] = []
    for gate in gates:
        if (
            set(gate) != {"gate_id", "ok", "report_digest"}
            or not isinstance(gate.get("gate_id"), str)
            or RESOURCE_ID.fullmatch(str(gate.get("gate_id"))) is None
            or not isinstance(gate.get("ok"), bool)
            or not isinstance(gate.get("report_digest"), str)
            or SHA256_DIGEST.fullmatch(str(gate.get("report_digest"))) is None
        ):
            raise ValueError("E_RECEIPT_GATE: invalid gate evidence")
        normalized = {
            "gate_id": str(gate["gate_id"]),
            "ok": gate["ok"],
            "report_digest": str(gate["report_digest"]),
            "subject_digest": decision_digest,
        }
        normalized["evidence_digest"] = contract_digest(normalized)
        normalized_gates.append(normalized)
    if not all(effect in TASK_EFFECTS for effect in effects):
        raise ValueError("E_RECEIPT_EFFECT: invalid observed effect")
    receipt = {
        "schema_version": 1,
        "task_id": task_id,
        "decision_digest": decision_digest,
        "task_digest": digests["task"],
        "policy_digest": digests["policy"],
        "registry_digest": digests["registry"],
        "inventory_digest": digests["inventory"],
        "used": [
            {
                "resource_id": resource_id,
                "locator_digest": str(resource_digests[resource_id]),
                "evidence_digest": contract_digest(
                    {
                        "decision_digest": decision_digest,
                        "resource_id": resource_id,
                        "locator_digest": str(resource_digests[resource_id]),
                    }
                ),
            }
            for resource_id in sorted(set(used))
        ],
        "omitted": sorted(set(omitted)),
        "gate_results": sorted(
            normalized_gates, key=lambda item: str(item["gate_id"])
        ),
        "observed_effects": sorted(set(effects)),
    }
    receipt["receipt_digest"] = contract_digest(receipt)
    return receipt
