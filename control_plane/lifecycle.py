"""Task lifecycle, worktree-scoped leases, and compact resource receipts."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from control_plane.repository import (
    assert_no_external_git_filters,
    trusted_git_argv,
    trusted_git_environment,
    trusted_git_executable,
    worktree_git_dir,
)
from control_plane.host_bridge import (
    BaseRefreshReceiptV1,
    BaseVerificationReceiptV1,
    GoverningRuntimeObservation,
    IntegrationEffectPlanV1,
    IntegrationReceiptV1,
    OutcomeEffectPlanV1,
    RemoteOutcomeReceiptV1,
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
    _INTEGRATION_EFFECT_PLAN_MAX_TTL_SECONDS,
    _INTEGRATION_READY_MAX_AGE_SECONDS,
    _issue_integration_execution_ticket,
    _outcome_remote_url_and_identity,
    _register_runtime_host_object,
    _runtime_host_object_is_live,
    apply_remote_write_receipt,
    apply_integration_receipt,
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
)
WRITER_LEASE_STATES = frozenset(
    {"framed", "planned", "ready", "implementing", "verifying"}
)
DELIVERY_OUTCOMES = frozenset({"commit", "pull_request", "integration"})
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "framed": frozenset({"planned", "blocked"}),
    "planned": frozenset({"ready", "blocked"}),
    "ready": frozenset({"implementing", "blocked"}),
    "implementing": frozenset({"verifying", "blocked"}),
    "verifying": frozenset({"implementing", "review_ready", "blocked"}),
    "review_ready": frozenset({"committed", "blocked"}),
    "committed": frozenset({"pushed", "blocked"}),
    "pushed": frozenset({"pr_draft", "blocked"}),
    "pr_draft": frozenset({"pr_ready", "blocked"}),
    "pr_ready": frozenset({"merged", "blocked"}),
    "merged": frozenset({"base_verified", "blocked"}),
    "base_verified": frozenset({"release_pending", "blocked"}),
    "release_pending": frozenset({"released", "blocked"}),
    "released": frozenset({"observed", "blocked"}),
    "observed": frozenset({"closed", "blocked"}),
    "closed": frozenset(),
    "blocked": frozenset(),
}
OUTCOME_LIMITS = {
    "answer": "planned",
    "local_change": "review_ready",
    "commit": "committed",
    "pull_request": "pr_ready",
    "integration": "base_verified",
    "release": "observed",
}
WRITER_OUTCOMES = frozenset(OUTCOME_LIMITS) - {"answer"}
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


def task_allows_writer_lease(state: Mapping[str, Any]) -> bool:
    """Return whether durable lifecycle state may continue local writes."""

    return (
        state.get("state") in WRITER_LEASE_STATES
        and state.get("outcome") in WRITER_OUTCOMES
        and state.get("verification_profile") is None
        and state.get("resume_forbidden") is not True
    )


def task_allows_delivery_lease(state: Mapping[str, Any]) -> bool:
    """Return whether exactly-reviewed work may enter local delivery."""

    return (
        state.get("state") == "review_ready"
        and state.get("outcome") in DELIVERY_OUTCOMES
        and state.get("resume_forbidden") is not True
    )


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


def _git_observation_text(
    repo: Path,
    arguments: tuple[str, ...],
    *,
    index_file: Path | str | None = None,
) -> str:
    try:
        completed = subprocess.run(
            trusted_git_argv(repo, arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=trusted_git_environment(index_file=index_file),
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "E_GIT_OBSERVATION_UNKNOWN: Git fact is unavailable"
        ) from error
    if completed.returncode != 0:
        raise ValueError(
            "E_GIT_OBSERVATION_UNKNOWN: Git fact is unavailable"
        )
    return completed.stdout.rstrip("\n")


def _git_head(repo: Path) -> str:
    try:
        return _git_observation_text(repo, ("rev-parse", "HEAD"))
    except ValueError:
        return ""


def _git_branch(repo: Path) -> str:
    try:
        return _git_observation_text(repo, ("branch", "--show-current"))
    except ValueError:
        return ""


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
    try:
        git_executable = trusted_git_executable()
    except OSError:
        git_executable = None
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
        "candidate_diff_check": tuple(
            trusted_git_argv(repo, ("diff", "--check"))
        ),
        "governing_tree_clean": tuple(
            trusted_git_argv(repo, ("status", "--porcelain=v2"))
        ),
    }
    if command_id not in VERIFICATION_COMMAND_IDS[context.profile]:
        raise ValueError(
            "E_VERIFICATION_COMMAND: command is not in the bound profile"
        )
    return commands[command_id]


def _verification_git(
    repo: Path,
    arguments: tuple[str, ...],
    *,
    index_file: Path | str | None = None,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            trusted_git_argv(repo, arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            env=trusted_git_environment(index_file=index_file),
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "E_VERIFICATION_UNKNOWN: Git snapshot failed"
        ) from error


def _verification_snapshot(
    repo: Path, *, index_file: Path | str | None = None
) -> str:
    assert_no_external_git_filters(repo, index_file=index_file)
    status = _verification_git(
        repo,
        (
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
        ),
        index_file=index_file,
    )
    if status.returncode != 0:
        raise ValueError("E_VERIFICATION_UNKNOWN: Git snapshot failed")
    changed_paths: set[str] = set()
    for arguments in (
        ("diff", "--name-only", "-z", "HEAD", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        completed = _verification_git(
            repo, arguments, index_file=index_file
        )
        if completed.returncode != 0:
            raise ValueError("E_VERIFICATION_UNKNOWN: changed paths unavailable")
        try:
            values = completed.stdout.decode("utf-8").split("\0")
        except UnicodeDecodeError as error:
            raise ValueError(
                "E_VERIFICATION_UNKNOWN: changed path is not UTF-8"
            ) from error
        for value in values:
            if not value:
                continue
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(
                    "E_VERIFICATION_UNKNOWN: changed path is unsafe"
                )
            changed_paths.add(value)
    residue: list[dict[str, object]] = []
    total_bytes = 0
    if len(changed_paths) > 20_000:
        raise ValueError(
            "E_VERIFICATION_UNKNOWN: residue inventory is too large"
        )
    for relative_value in sorted(changed_paths):
        path = repo / relative_value
        if path.is_symlink():
            target = os.readlink(path)
            target_bytes = os.fsencode(target)
            total_bytes += len(target_bytes)
            if total_bytes > 67_108_864:
                raise ValueError(
                    "E_VERIFICATION_UNKNOWN: residue bytes exceed cap"
                )
            residue.append(
                {
                    "path": relative_value,
                    "kind": "symlink",
                    "target_digest": f"sha256:{sha256(target_bytes).hexdigest()}",
                }
            )
            continue
        if not path.exists():
            residue.append({"path": relative_value, "kind": "missing"})
            continue
        if not path.is_file():
            raise ValueError(
                "E_VERIFICATION_UNKNOWN: changed path is not a regular file"
            )
        stat = path.lstat()
        total_bytes += stat.st_size
        if total_bytes > 67_108_864:
            raise ValueError(
                "E_VERIFICATION_UNKNOWN: residue bytes exceed cap"
            )
        residue.append(
            {
                "path": relative_value,
                "mode": stat.st_mode,
                "size": stat.st_size,
                "kind": "file",
                "digest": f"sha256:{sha256(path.read_bytes()).hexdigest()}",
            }
        )
    index = _verification_git(
        repo, ("write-tree",), index_file=index_file, text=True
    )
    if index.returncode != 0:
        raise ValueError("E_VERIFICATION_UNKNOWN: index snapshot failed")
    return contract_digest(
        {
            "head": _git_head(repo),
            "index_tree": index.stdout.strip(),
            "status_hex": status.stdout.hex(),
            "residue": residue,
        }
    )


def _sanitized_verification_environment(
    context: VerificationExecutionContext,
) -> dict[str, str]:
    git_observation = trusted_git_environment()
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
        "GIT_CONFIG_NOSYSTEM": git_observation["GIT_CONFIG_NOSYSTEM"],
        "GIT_CONFIG_SYSTEM": git_observation["GIT_CONFIG_SYSTEM"],
        "GIT_CONFIG_GLOBAL": git_observation["GIT_CONFIG_GLOBAL"],
        "GIT_GRAFT_FILE": git_observation["GIT_GRAFT_FILE"],
        "GIT_NO_LAZY_FETCH": git_observation["GIT_NO_LAZY_FETCH"],
        "GIT_NO_REPLACE_OBJECTS": git_observation[
            "GIT_NO_REPLACE_OBJECTS"
        ],
        "GIT_OPTIONAL_LOCKS": git_observation["GIT_OPTIONAL_LOCKS"],
        "GIT_LITERAL_PATHSPECS": git_observation[
            "GIT_LITERAL_PATHSPECS"
        ],
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
    specifications: object,
    clock: object,
) -> tuple[HostBoundVerificationEvidence, ...]:
    del (
        governing_runtime,
        context,
        expected_generation,
        specifications,
        clock,
    )
    raise ValueError(
        "E_VERIFICATION_EVIDENCE: Task 1 has no typed supplemental "
        "publisher; mapping and JSON specifications are never host evidence"
    )


def publish_verification_supplemental_evidence(
    *,
    task_store: object,
    context: object,
    evidence: tuple[HostBoundVerificationEvidence, ...],
    expected_generation: int,
    clock: object,
) -> dict[str, Any]:
    del task_store, context, evidence, expected_generation, clock
    raise ValueError(
        "E_VERIFICATION_EVIDENCE: Task 1 cannot publish supplemental "
        "receipts before their typed publishers exist"
    )


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


def _parse_outcome_time(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{code}: UTC timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{code}: UTC timestamp is invalid") from error
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{code}: UTC timestamp is invalid")
    return parsed


def _integration_current_time(
    *, now: str | None, clock: object | None, code: str
) -> datetime:
    """Read exactly one UTC wall-clock sample for an integration boundary."""

    if (now is None) == (clock is None):
        raise ValueError(f"{code}: exactly one current-time source is required")
    if clock is not None:
        if not callable(clock):
            raise ValueError(f"{code}: current time is UNKNOWN")
        try:
            observed = clock()
        except Exception as error:
            raise ValueError(f"{code}: current time is UNKNOWN") from error
    else:
        observed = now
    if isinstance(observed, datetime):
        if observed.tzinfo != timezone.utc:
            raise ValueError(f"{code}: current time is UNKNOWN")
        return observed
    try:
        return _parse_outcome_time(observed, code=code)
    except ValueError as error:
        raise ValueError(f"{code}: current time is UNKNOWN") from error


def _integration_plan_is_current(
    effect_plan: IntegrationEffectPlanV1, *, current: datetime, code: str
) -> bool:
    prepared = _parse_outcome_time(effect_plan.prepared_at, code=code)
    expires = _parse_outcome_time(effect_plan.expires_at, code=code)
    return (
        prepared <= current < expires
        and (expires - prepared).total_seconds()
        <= _INTEGRATION_EFFECT_PLAN_MAX_TTL_SECONDS
    )


def _integration_ready_is_fresh(
    effect_plan: IntegrationEffectPlanV1,
    receipt: IntegrationReceiptV1,
    *,
    current: datetime,
    code: str,
) -> bool:
    prepared = _parse_outcome_time(effect_plan.prepared_at, code=code)
    observed = _parse_outcome_time(receipt.observed_at, code=code)
    return (
        _integration_plan_is_current(effect_plan, current=current, code=code)
        and prepared <= observed <= current
        and (current - observed).total_seconds()
        <= _INTEGRATION_READY_MAX_AGE_SECONDS
    )


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
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError(
                "E_DURABILITY: directory is unsafe"
            )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
        self._armed_remote_write_plans: dict[str, str] = {}
        self._armed_pull_request_plans: dict[str, str] = {}
        self._armed_pull_request_ready_plans: dict[str, str] = {}
        self._armed_integration_plans: dict[str, str] = {}
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

    def acquire_delivery_lease(
        self,
        task_id: str,
        *,
        worktree: str,
        branch: str,
        session_id: str,
        paths: list[str],
        policy_digest: str,
        expected_head: str,
        diff_digest: str,
        expected_generation: int,
    ) -> dict[str, Any]:
        """Acquire the distinct, review-bound lease used only for delivery."""

        common_dir = _common_git_dir(self.state_dir)
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                if (
                    not task_allows_delivery_lease(state)
                    or state.get("branch") != branch
                    or state.get("generation") != expected_generation
                ):
                    raise ValueError(
                        "E_DELIVERY_LEASE_STATE: delivery requires fresh review_ready"
                    )
                implementation_lease = (
                    self.state_dir / "codex-control-plane" / "leases" / f"{task_id}.json"
                )
                if implementation_lease.exists():
                    raise ValueError(
                        "E_DELIVERY_LEASE_CONFLICT: implementation lease is active"
                    )
                self._validate_delivery_review_binding(
                    state,
                    expected_head=expected_head,
                    diff_digest=diff_digest,
                    paths=paths,
                    worktree=worktree,
                    branch=branch,
                    policy_digest=policy_digest,
                )
                return DeliveryLease.acquire(
                    self.state_dir,
                    task_id=task_id,
                    worktree=worktree,
                    branch=branch,
                    session_id=session_id,
                    paths=paths,
                    policy_digest=policy_digest,
                    generation=expected_generation,
                    review_head=expected_head,
                    base_head=str(state["delivery_review_binding"]["base_head"]),
                    diff_digest=diff_digest,
                    _lease_lock_token=token,
                )

    def _validate_delivery_review_binding(
        self,
        state: Mapping[str, Any],
        *,
        expected_head: str,
        diff_digest: str,
        paths: list[str],
        worktree: str,
        branch: str,
        policy_digest: str,
    ) -> None:
        """Rebind delivery to the immutable, post-promotion review proof."""

        from control_plane.run_workflow import (
            MAX_REVIEW_PACKET_BYTES,
            ReviewArtifactStore,
            RunStore,
            _changed_paths,
            _executable_gate_ids,
            _required_review_kinds,
            _review_summary,
            validate_independent_review_receipt,
            validate_review_packet,
        )
        from control_plane.policy import load_policy

        run_plan_digest = state.get("run_plan_digest")
        revision_digest = state.get("active_run_revision_digest")
        attempt_digest = state.get("review_attempt_digest")
        promotion_digest = state.get("review_promotion_digest")
        binding = state.get("delivery_review_binding")
        required = {
            "schema_version", "kind", "run_plan_digest", "run_revision_digest",
            "attempt_digest", "promotion_digest", "base_head", "reviewed_head", "diff_digest", "untracked_modes",
            "scope_paths", "receipt_digests", "authorizes", "binding_digest",
        }
        if (
            not isinstance(binding, Mapping)
            or set(binding) != required
            or binding.get("schema_version") != 1
            or binding.get("kind") != "DeliveryReviewBindingV1"
            or binding.get("authorizes") is not False
            or binding.get("binding_digest") != contract_digest(
                {key: value for key, value in binding.items() if key != "binding_digest"}
            )
            or not all(
                isinstance(value, str) and SHA256_DIGEST.fullmatch(value)
                for value in (
                    run_plan_digest, revision_digest, attempt_digest, promotion_digest,
                )
            )
            or binding.get("run_plan_digest") != run_plan_digest
            or binding.get("run_revision_digest") != revision_digest
            or binding.get("attempt_digest") != attempt_digest
            or binding.get("promotion_digest") != promotion_digest
            or GIT_OBJECT_ID.fullmatch(str(binding.get("base_head"))) is None
            or binding.get("reviewed_head") != expected_head
            or binding.get("diff_digest") != diff_digest
            or binding.get("scope_paths") != paths
            or not isinstance(binding.get("untracked_modes"), list)
            or not isinstance(binding.get("receipt_digests"), list)
            or not binding["receipt_digests"]
        ):
            raise ValueError("E_DELIVERY_REVIEW: review binding drifted")
        try:
            store = RunStore(self.state_dir)
            plan = store.load_plan(str(state["task_id"]))
            revision = store.load_active(str(state["task_id"]))
            attempts = store.attempts(str(state["task_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("E_DELIVERY_REVIEW: durable review evidence is unavailable") from error
        if not attempts:
            raise ValueError("E_DELIVERY_REVIEW: reviewed attempt is unavailable")
        latest = attempts[-1]
        required_kinds = _required_review_kinds(plan)
        if (
            plan.get("plan_digest") != run_plan_digest
            or revision.get("revision_digest") != revision_digest
            or latest.get("status") != "PASS"
            or latest.get("run_revision_digest") != revision.get("revision_digest")
            or latest.get("attempt_digest") != attempt_digest
            or expected_head != revision.get("head")
            or tuple(paths) != tuple(latest.get("changed_paths", ()))
        ):
            raise ValueError("E_DELIVERY_REVIEW: review binding drifted")
        gate_digests = latest.get("gate_receipt_digests")
        try:
            gates = [
                store.load_gate_receipt(str(state["task_id"]), str(item))
                for item in gate_digests
            ] if isinstance(gate_digests, list) else []
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("E_DELIVERY_REVIEW: durable gate evidence is unavailable") from error
        if (
            not isinstance(gate_digests, list)
            or {item.get("gate_id") for item in gates} != set(_executable_gate_ids(plan))
            or len({item.get("gate_id") for item in gates}) != len(gates)
            or any(item.get("status") != "PASS" or item.get("attempt") != latest["attempt"] for item in gates)
        ):
            raise ValueError("E_DELIVERY_REVIEW: required gate evidence drifted")
        repository = Path(str(plan["repository"])).resolve()
        try:
            current_branch = _delivery_git_text(repository, ("branch", "--show-current"))
            current_head = _delivery_git_text(repository, ("rev-parse", "HEAD"))
            policy = load_policy(repository / ".codex" / "project-policy.toml")
            durable_policy_digest = contract_digest(policy)
            remote_base = _delivery_git_text(
                repository,
                ("rev-parse", f"refs/remotes/{policy['git']['remote']}/{policy['git']['base_branch']}"),
            )
            changed_paths = _changed_paths(repository)
            live_diff = ReviewArtifactStore._diff_digest(
                ReviewArtifactStore(repository)._capture_diff(expected_head, changed_paths)
            )
        except (OSError, ValueError) as error:
            raise ValueError("E_DELIVERY_REVIEW: delivery worktree is unavailable") from error
        if (
            Path(worktree).resolve() != repository
            or worktree_git_dir(repository).resolve() != self.state_dir.resolve()
            or branch != plan.get("branch")
            or current_branch != branch
            or current_head != expected_head
            or policy_digest != durable_policy_digest
            or remote_base != binding.get("base_head")
            or tuple(changed_paths) != tuple(paths)
            or live_diff != diff_digest
            or ReviewArtifactStore(repository)._untracked_modes(tuple(paths)) != binding.get("untracked_modes")
            or _delivery_index_paths(repository)
        ):
            raise ValueError("E_DELIVERY_REVIEW: delivery worktree binding drifted")
        durable_digests: list[str] = []
        for review_kind in required_kinds:
            try:
                receipt = store._load_closed_json(
                    store._review_receipt_path(
                        str(state["task_id"]), int(latest["attempt"]), review_kind
                    ),
                    maximum=MAX_REVIEW_PACKET_BYTES,
                    code="E_INDEPENDENT_REVIEW",
                )
                packet = store.load_review_packet(
                    str(state["task_id"]), int(latest["attempt"]), review_kind
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("E_DELIVERY_REVIEW: durable review evidence is unavailable") from error
            if (
                validate_independent_review_receipt(receipt)
                or validate_review_packet(packet)
                or receipt.get("status") != "PASS"
                or receipt.get("review_kind") != review_kind
                or packet.get("review_kind") != review_kind
                or receipt.get("receipt_digest") not in binding["receipt_digests"]
                or receipt.get("review_packet_digest") != packet.get("packet_digest")
                or any(receipt.get(key) != expected for key, expected in (
                    ("run_plan_digest", run_plan_digest),
                    ("run_revision_digest", revision_digest),
                    ("attempt_digest", attempt_digest),
                    ("reviewed_head", expected_head),
                    ("diff_digest", diff_digest),
                ))
                or packet.get("reviewed_head") != expected_head
                or packet.get("diff_digest") != diff_digest
                or packet.get("scope_paths") != paths
                or packet.get("evidence_summaries")
                != sorted((_review_summary(gate) for gate in gates), key=lambda item: (item["check_kind"], item["check_id"]))
                or receipt.get("scope_paths_digest") != contract_digest({"scope_paths": paths})
            ):
                raise ValueError("E_DELIVERY_REVIEW: review binding drifted")
            durable_digests.append(str(receipt["receipt_digest"]))
        promotion_core = {
            "run_plan_digest": run_plan_digest,
            "review_receipt_digests": sorted(durable_digests),
            "review_kinds": list(required_kinds),
            "authorizes": False,
        }
        if (
            sorted(durable_digests) != binding["receipt_digests"]
            or promotion_digest != contract_digest(promotion_core)
        ):
            raise ValueError("E_DELIVERY_REVIEW: review binding drifted")

    def prepare_delivery_commit(
        self,
        task_id: str,
        *,
        lease: Mapping[str, Any],
        snapshot_digest: str,
        allowlist: tuple[str, ...],
        expected_index_tree: str,
        parent_head: str,
        expected_tree: str,
        message: str,
    ) -> dict[str, Any]:
        """Durably bind the exact local commit before the host stages files."""

        if (
            not isinstance(snapshot_digest, str)
            or SHA256_DIGEST.fullmatch(snapshot_digest) is None
            or not allowlist
            or tuple(sorted(set(allowlist))) != allowlist
            or not all(_normalize_lease_path(path) == path for path in allowlist)
            or any(
                GIT_OBJECT_ID.fullmatch(value) is None
                for value in (expected_index_tree, parent_head, expected_tree)
            )
            or not isinstance(message, str)
            or not 1 <= len(message) <= 200
            or any(ord(character) < 32 for character in message)
        ):
            raise ValueError("E_DELIVERY_MARKER: commit proposal is invalid")
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            bound_lease = DeliveryLease.validate(
                self.state_dir, task_id=task_id, lease=lease
            )
            if (
                not task_allows_delivery_lease(state)
                or state.get("generation") != bound_lease["generation"]
                or state.get("branch") != bound_lease["branch"]
                or parent_head != bound_lease["review_head"]
                or not _delivery_remote_base_matches(Path(bound_lease["worktree"]), bound_lease["base_head"])
                or tuple(allowlist) != tuple(bound_lease["paths"])
                or not isinstance(state.get("delivery_review_binding"), Mapping)
                or snapshot_digest != state["delivery_review_binding"].get("binding_digest")
            ):
                raise ValueError("E_DELIVERY_MARKER: lease no longer matches review")
            marker_core = {
                "schema_version": 1,
                "task_id": task_id,
                "generation": state["generation"],
                "lease_digest": bound_lease["lease_digest"],
                "snapshot_digest": snapshot_digest,
                "allowlist": list(allowlist),
                "expected_index_tree": expected_index_tree,
                "parent_head": parent_head,
                "base_head": bound_lease["base_head"],
                "expected_tree": expected_tree,
                "message_digest": f"sha256:{sha256(message.encode('utf-8')).hexdigest()}",
                "phase": "prepared",
            }
            marker = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            existing = state.get("finalizing_delivery_commit")
            if existing is not None:
                if existing != marker:
                    raise ValueError("E_DELIVERY_MARKER: another delivery marker is active")
                return state
            state["finalizing_delivery_commit"] = marker
            state["resume_forbidden"] = True
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            return state

    def observe_delivery_index(
        self,
        task_id: str,
        *,
        lease: Mapping[str, Any],
        expected_index_tree: str,
    ) -> dict[str, Any]:
        """Record the host-observed stage result without a lifecycle state."""

        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_delivery_marker(state, task_id=task_id)
            bound_lease = DeliveryLease.validate(
                self.state_dir, task_id=task_id, lease=lease
            )
            if (
                marker["phase"] != "prepared"
                or marker["lease_digest"] != bound_lease["lease_digest"]
                or marker["base_head"] != bound_lease["base_head"]
                or not _delivery_remote_base_matches(Path(bound_lease["worktree"]), bound_lease["base_head"])
                or marker["expected_index_tree"] != expected_index_tree
                or _delivery_index_tree(Path(bound_lease["worktree"]))
                != expected_index_tree
                or _delivery_index_paths(Path(bound_lease["worktree"]))
                != tuple(marker["allowlist"])
                or not _delivery_review_diff_matches(
                    Path(bound_lease["worktree"]),
                    state.get("delivery_review_binding"),
                    tuple(marker["allowlist"]),
                )
            ):
                raise ValueError("E_DELIVERY_INDEX: staged index is not the bound allowlist")
            marker = dict(marker)
            marker["phase"] = "index_observed"
            marker["marker_digest"] = contract_digest(
                {key: value for key, value in marker.items() if key != "marker_digest"}
            )
            state["finalizing_delivery_commit"] = marker
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            return state

    def publish_delivery_commit(
        self,
        task_id: str,
        *,
        lease: Mapping[str, Any],
        observation: object,
        current_branch: str,
    ) -> dict[str, Any]:
        """Publish one observed local commit, then release only its owner lease."""

        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_delivery_marker(state, task_id=task_id)
            bound_lease = DeliveryLease.validate(
                self.state_dir, task_id=task_id, lease=lease
            )
            if (
                marker["phase"] not in {"index_observed", "git_committed"}
                or marker["lease_digest"] != bound_lease["lease_digest"]
                or marker["base_head"] != bound_lease["base_head"]
                or not _delivery_remote_base_matches(Path(bound_lease["worktree"]), bound_lease["base_head"])
                or current_branch != state.get("branch")
                or not isinstance(observation, ValidatedLocalGitObservation)
                or observation.task_digest != state.get("task_digest")
                or observation.branch != state.get("branch")
                or observation.target_state != "committed"
            ):
                raise ValueError("E_DELIVERY_COMMIT: local commit observation is invalid")
            commit = str(observation.evidence.get("commit", ""))
            worktree = Path(bound_lease["worktree"])
            if not _delivery_commit_matches(
                worktree, marker, commit
            ) or (
                _delivery_git_text(worktree, ("branch", "--show-current"))
                != state.get("branch")
                or _delivery_git_text(worktree, ("rev-parse", "HEAD")) != commit
                or not _delivery_worktree_clean(worktree)
            ):
                raise ValueError("E_DELIVERY_COMMIT: observed commit drifted")
            if marker["phase"] == "index_observed":
                marker = dict(marker)
                marker.update({"phase": "git_committed", "observed_sha": commit})
                marker["marker_digest"] = contract_digest(
                    {key: value for key, value in marker.items() if key != "marker_digest"}
                )
                state["finalizing_delivery_commit"] = marker
                _atomic_json(self._path(task_id), state)
            evidence = consume_lifecycle_observation(observation)
            try:
                outcome_binding = _canonical_delivery_outcome_binding(
                    self.state_dir,
                    task_id=task_id,
                    state=state,
                    marker=marker,
                    committed_head=commit,
                )
            except ValueError as error:
                raise ValueError(
                    "E_DELIVERY_COMMIT: canonical outcome lineage is invalid"
                ) from error
            state.update(
                {
                    "state": "committed",
                    "generation": int(state["generation"]) + 1,
                    "resume_forbidden": True,
                    "outcome_binding": outcome_binding,
                    "updated_at": _utc_now(),
                }
            )
            state.setdefault("evidence", {})["committed"] = evidence
            marker = dict(state["finalizing_delivery_commit"])
            marker["phase"] = "state_committed"
            marker["marker_digest"] = contract_digest(
                {key: value for key, value in marker.items() if key != "marker_digest"}
            )
            state["finalizing_delivery_commit"] = marker
            _atomic_json(self._path(task_id), state)
        released = DeliveryLease.release(self.state_dir, task_id=task_id, lease=lease)
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_delivery_marker(state, task_id=task_id)
            if marker["phase"] != "state_committed":
                raise ValueError("E_DELIVERY_COMMIT: marker changed before lease release")
            marker = dict(marker)
            marker["phase"] = "lease_released"
            marker["release_digest"] = released["tombstone_digest"]
            marker["marker_digest"] = contract_digest(
                {key: value for key, value in marker.items() if key != "marker_digest"}
            )
            state["finalizing_delivery_commit"] = marker
            state["resume_forbidden"] = False
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            return state

    def prepare_remote_write(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Persist exact bindings and reobserve Git before any remote write."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_REMOTE_WRITE_PREPARE: exact plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        run_plan, policy = _canonical_remote_write_inputs(
            self.state_dir, task_id=task_id, effect_plan=effect_plan
        )
        policy_git = policy.get("git", {})
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            try:
                delivery_marker = self._validated_delivery_marker(
                    state, task_id=task_id
                )
                outcome_binding = _canonical_delivery_outcome_binding(
                    self.state_dir,
                    task_id=task_id,
                    state=state,
                    marker=delivery_marker,
                    committed_head=str(
                        state.get("evidence", {})
                        .get("committed", {})
                        .get("commit", "")
                    ),
                )
            except ValueError as error:
                raise ValueError(
                    "E_REMOTE_WRITE_PREPARE: canonical committed lineage is unavailable"
                ) from error
            if (
                state.get("state") != "committed"
                or delivery_marker.get("phase") != "lease_released"
                or state.get("outcome_binding") != outcome_binding
                or state.get("task_digest") != effect_plan.task_digest
                or state.get("outcome") != effect_plan.requested_outcome
                or state.get("branch") != current_branch
                or state.get("branch") != effect_plan.branch
                or state.get("evidence", {})
                .get("committed", {})
                .get("commit")
                != effect_plan.head_sha
                or state.get("pending_remote_effect") is not None
                or effect_plan.task_id != task_id
                or effect_plan.task_digest != run_plan.get("task_digest")
                or effect_plan.run_plan_digest != run_plan.get("plan_digest")
                or effect_plan.requested_outcome
                != run_plan.get("requested_outcome")
                or effect_plan.repository != run_plan.get("repository")
                or effect_plan.branch != run_plan.get("branch")
                or effect_plan.subject_digest
                != outcome_binding.get("binding_digest")
                or effect_plan.head_sha != outcome_binding.get("committed_head")
                or effect_plan.policy_digest != contract_digest(policy)
                or effect_plan.remote != policy_git.get("remote")
                or effect_plan.base != policy_git.get("base_branch")
                or policy_git.get("allow_direct_base_push") is not False
                or effect_plan.branch == effect_plan.base
            ):
                raise ValueError(
                    "E_REMOTE_WRITE_PREPARE: committed lifecycle binding drifted"
                )
            live = _observe_remote_write_bindings(effect_plan)
            marker_core = {
                "schema_version": 1,
                "task_id": task_id,
                "phase": "prepared",
                "effect_plan": effect_plan.to_dict(),
                "outcome_binding": copy.deepcopy(dict(outcome_binding)),
                "run_plan": copy.deepcopy(dict(run_plan)),
                "policy": copy.deepcopy(dict(policy)),
                "base_head": live["base_head"],
                "remote_url_digest": live["remote_url_digest"],
                "remote_identity_digest": live["remote_identity_digest"],
                "status": "PENDING",
                "retry_policy": "observe_before_write",
                "authorizes": False,
            }
            marker = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            if len(
                json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ) > 32_768:
                raise ValueError("E_REMOTE_WRITE_PREPARE: marker exceeds byte cap")
            state["pending_remote_effect"] = marker
            state["resume_forbidden"] = True
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            return state

    def arm_remote_write_observation(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Revalidate immediately, then durably force observation-only mode."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_REMOTE_WRITE_PREPARE: exact plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_remote_write_marker(state, task_id=task_id)
            if marker["phase"] != "prepared":
                if marker["phase"] == "observe_only":
                    raise ValueError(
                        "E_REMOTE_WRITE_OBSERVE_ONLY: observe exact ref; never push again"
                    )
                raise ValueError(
                    "E_REMOTE_WRITE_PREPARE: bounded repair is required"
                )
            if (
                state.get("state") != "committed"
                or state.get("branch") != current_branch
                or marker["effect_plan"] != effect_plan.to_dict()
            ):
                raise ValueError("E_REMOTE_WRITE_PREPARE: prepared binding drifted")
            live = _observe_remote_write_bindings(effect_plan)
            if any(
                live[name] != marker[name]
                for name in (
                    "base_head",
                    "remote_url_digest",
                    "remote_identity_digest",
                )
            ):
                raise ValueError("E_REMOTE_WRITE_PREPARE: live Git binding drifted")
            marker_core = {
                **{
                    key: value
                    for key, value in marker.items()
                    if key != "marker_digest"
                },
                "phase": "observe_only",
                "status": "UNKNOWN",
                "retry_policy": "observe_only",
            }
            state["pending_remote_effect"] = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            self._armed_remote_write_plans[task_id] = effect_plan.plan_digest
            return state

    def revalidate_remote_write_before_execution(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> OutcomeEffectPlanV1:
        """Read-only, one-shot validation immediately before the host write."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_REMOTE_WRITE_EXECUTION: exact plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        armed_digest = self._armed_remote_write_plans.pop(task_id, None)
        if armed_digest != effect_plan.plan_digest:
            raise ValueError(
                "E_REMOTE_WRITE_EXECUTION: current-process arm is unavailable"
            )
        try:
            run_plan, policy = _canonical_remote_write_inputs(
                self.state_dir, task_id=task_id, effect_plan=effect_plan
            )
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                pending = self._validated_remote_write_marker(
                    state, task_id=task_id
                )
                delivery_marker = self._validated_delivery_marker(
                    state, task_id=task_id
                )
                canonical_binding = _canonical_delivery_outcome_binding(
                    self.state_dir,
                    task_id=task_id,
                    state=state,
                    marker=delivery_marker,
                    committed_head=effect_plan.head_sha,
                )
                live = _observe_remote_write_bindings(effect_plan)
                if (
                    state.get("state") != "committed"
                    or state.get("branch") != current_branch
                    or state.get("branch") != effect_plan.branch
                    or delivery_marker.get("phase") != "lease_released"
                    or pending.get("phase") != "observe_only"
                    or pending.get("status") != "UNKNOWN"
                    or pending.get("retry_policy") != "observe_only"
                    or pending.get("effect_plan") != effect_plan.to_dict()
                    or pending.get("run_plan") != run_plan
                    or pending.get("policy") != policy
                    or state.get("outcome_binding") != canonical_binding
                    or pending.get("outcome_binding") != canonical_binding
                    or effect_plan.subject_digest
                    != canonical_binding["binding_digest"]
                    or any(
                        pending.get(name) != live[name]
                        for name in (
                            "base_head",
                            "remote_url_digest",
                            "remote_identity_digest",
                        )
                    )
                ):
                    raise ValueError("executor-edge binding drifted")
        except ValueError as error:
            raise ValueError(
                "E_REMOTE_WRITE_EXECUTION: executor-edge revalidation failed"
            ) from error
        return effect_plan

    def publish_remote_write(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        receipt: RemoteOutcomeReceiptV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Publish one exact remote-write observation without executing Git.

        An inconclusive or failed observation persists a bounded block marker.
        Only an exact PASS for a prior UNKNOWN marker may recover the task;
        this method never retries the write.
        """

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_OUTCOME_EFFECT_PLAN: exact durable plan is required")
        if type(receipt) is not RemoteOutcomeReceiptV1:
            raise ValueError(
                "E_REMOTE_OUTCOME_RECEIPT: exact durable contracts are required"
            )
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        receipt = RemoteOutcomeReceiptV1.from_dict(receipt.to_dict())
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            source = state.get("state")
            try:
                pending = self._validated_remote_write_marker(
                    state, task_id=task_id
                )
            except ValueError as error:
                raise ValueError(
                    "E_REMOTE_WRITE_PREPARE: prepared marker is required"
                ) from error
            recovering_unknown = (
                source == "blocked"
                and pending.get("status") == "UNKNOWN"
                and state.get("resume_state") == "committed"
            )
            if (
                (source != "committed" and not recovering_unknown)
                or pending.get("phase") != "observe_only"
                or pending.get("effect_plan") != effect_plan.to_dict()
                or pending.get("outcome_binding") != state.get("outcome_binding")
                or state.get("task_id") != task_id
                or state.get("task_digest") != effect_plan.task_digest
                or state.get("outcome") != effect_plan.requested_outcome
                or state.get("branch") != current_branch
                or state.get("branch") != effect_plan.branch
                or state.get("evidence", {})
                .get("committed", {})
                .get("commit")
                != effect_plan.head_sha
            ):
                raise ValueError(
                    "E_REMOTE_OUTCOME_BINDING: committed lifecycle binding drifted"
                )
            try:
                delivery_marker = self._validated_delivery_marker(
                    state, task_id=task_id
                )
                canonical_binding = _canonical_delivery_outcome_binding(
                    self.state_dir,
                    task_id=task_id,
                    state=state,
                    marker=delivery_marker,
                    committed_head=effect_plan.head_sha,
                )
            except ValueError as error:
                raise ValueError(
                    "E_REMOTE_OUTCOME_BINDING: canonical committed lineage drifted"
                ) from error
            if (
                delivery_marker.get("phase") != "lease_released"
                or state.get("outcome_binding") != canonical_binding
                or pending.get("outcome_binding") != canonical_binding
                or effect_plan.subject_digest != canonical_binding["binding_digest"]
            ):
                raise ValueError(
                    "E_REMOTE_OUTCOME_BINDING: canonical committed lineage drifted"
                )
            receipt_digests = list(state.get("remote_outcome_receipt_digests", []))
            if receipt.receipt_digest in receipt_digests:
                raise ValueError(
                    "E_REMOTE_OUTCOME_REPLAY: receipt has already been published"
                )
            if receipt.status in {"UNKNOWN", "FAIL"}:
                retry_policy = (
                    "observe_only"
                    if receipt.status == "UNKNOWN"
                    else "bounded_repair_only"
                )
                marker_core = {
                    **{
                        key: value
                        for key, value in pending.items()
                        if key != "marker_digest"
                    },
                    "receipt_digest": receipt.receipt_digest,
                    "phase": (
                        "observe_only"
                        if receipt.status == "UNKNOWN"
                        else "repair_required"
                    ),
                    "status": receipt.status,
                    "observed_at": receipt.observed_at,
                    "retry_policy": retry_policy,
                }
                state.update(
                    {
                        "state": "blocked",
                        "resume_state": "committed",
                        "resume_forbidden": True,
                        "block_reason": f"E_REMOTE_OUTCOME_{receipt.status}",
                        "pending_remote_effect": {
                            **marker_core,
                            "marker_digest": contract_digest(marker_core),
                        },
                        "generation": int(state.get("generation", 0)) + 1,
                        "updated_at": _utc_now(),
                    }
                )
                state["remote_outcome_receipt_digests"] = [
                    *receipt_digests,
                    receipt.receipt_digest,
                ]
                _atomic_json(self._path(task_id), state)
                return state
            try:
                canonical_run_plan, canonical_policy = (
                    _canonical_remote_write_inputs(
                        self.state_dir,
                        task_id=task_id,
                        effect_plan=effect_plan,
                    )
                )
                live = _observe_remote_write_bindings(effect_plan)
                if (
                    pending.get("run_plan") != canonical_run_plan
                    or pending.get("policy") != canonical_policy
                    or any(
                        pending.get(name) != live[name]
                        for name in (
                            "base_head",
                            "remote_url_digest",
                            "remote_identity_digest",
                        )
                    )
                ):
                    raise ValueError("canonical remote-write subject drifted")
            except ValueError:
                marker_core = {
                    **{
                        key: value
                        for key, value in pending.items()
                        if key != "marker_digest"
                    },
                    "phase": "observe_only",
                    "status": "UNKNOWN",
                    "observed_at": receipt.observed_at,
                    "receipt_digest": receipt.receipt_digest,
                    "retry_policy": "observe_only",
                }
                state.update(
                    {
                        "state": "blocked",
                        "resume_state": "committed",
                        "resume_forbidden": True,
                        "block_reason": "E_REMOTE_WRITE_PUBLISH_DRIFT",
                        "pending_remote_effect": {
                            **marker_core,
                            "marker_digest": contract_digest(marker_core),
                        },
                        "generation": int(state.get("generation", 0)) + 1,
                        "updated_at": _utc_now(),
                    }
                )
                state["remote_outcome_receipt_digests"] = [
                    *receipt_digests,
                    receipt.receipt_digest,
                ]
                _atomic_json(self._path(task_id), state)
                return state
            successor = apply_remote_write_receipt(
                outcome_binding=pending["outcome_binding"],
                effect_plan=effect_plan,
                receipt=receipt,
            )
            state.update(
                {
                    "state": "pushed",
                    "resume_state": None,
                    "resume_forbidden": False,
                    "block_reason": None,
                    "outcome_binding": successor,
                    "generation": int(state.get("generation", 0)) + 1,
                    "updated_at": _utc_now(),
                }
            )
            state.pop("pending_remote_effect", None)
            state.setdefault("evidence", {})["pushed"] = {
                "remote_head": effect_plan.head_sha,
                "receipt_digest": receipt.receipt_digest,
            }
            state["remote_outcome_receipt_digests"] = [
                *receipt_digests,
                receipt.receipt_digest,
            ]
            _atomic_json(self._path(task_id), state)
            return state

    def _pull_request_context_locked(
        self,
        state: Mapping[str, Any],
        *,
        task_id: str,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
        try:
            run_plan, policy = _canonical_remote_write_inputs(
                self.state_dir, task_id=task_id, effect_plan=effect_plan
            )
            delivery_marker = self._validated_delivery_marker(
                state, task_id=task_id
            )
            pushed_head = str(
                state.get("evidence", {})
                .get("pushed", {})
                .get("remote_head", "")
            )
            canonical_binding = _canonical_pushed_outcome_binding(
                self.state_dir,
                task_id=task_id,
                state=state,
                delivery_marker=delivery_marker,
                pushed_head=pushed_head,
            )
            live = _observe_pull_request_bindings(effect_plan)
        except ValueError as error:
            raise ValueError(
                "E_PULL_REQUEST_PREPARE: canonical pushed context drifted"
            ) from error
        policy_git = policy.get("git", {})
        if (
            state.get("task_id") != task_id
            or state.get("task_digest") != effect_plan.task_digest
            or state.get("outcome") != effect_plan.requested_outcome
            or state.get("branch") != current_branch
            or state.get("branch") != effect_plan.branch
            or state.get("outcome_binding") != canonical_binding
            or delivery_marker.get("phase") != "lease_released"
            or pushed_head != effect_plan.head_sha
            or effect_plan.subject_digest
            != canonical_binding.get("binding_digest")
            or effect_plan.run_plan_digest != run_plan.get("plan_digest")
            or effect_plan.repository != run_plan.get("repository")
            or effect_plan.branch != run_plan.get("branch")
            or effect_plan.policy_digest != contract_digest(policy)
            or effect_plan.remote != policy_git.get("remote")
            or effect_plan.base != policy_git.get("base_branch")
            or policy_git.get("allow_direct_base_push") is not False
            or live.get("remote_url_digest") != effect_plan.remote_url_digest
            or live.get("remote_identity_digest")
            != effect_plan.remote_identity_digest
            or live.get("feature_head") != effect_plan.head_sha
        ):
            raise ValueError("E_PULL_REQUEST_PREPARE: canonical binding drifted")
        return run_plan, policy, canonical_binding, live

    def arm_pull_request_draft_creation(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Durably enter observation-only mode before a host PR mutation."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_PULL_REQUEST_PREPARE: exact plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        if effect_plan.effect != "pull_request":
            raise ValueError("E_PULL_REQUEST_PREPARE: PR plan is required")
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_pull_request_marker(state, task_id=task_id)
            if marker["phase"] != "prepared":
                raise ValueError(
                    "E_PULL_REQUEST_OBSERVE_ONLY: observe exact PR; never create again"
                )
            run_plan, policy, binding, live = self._pull_request_context_locked(
                state,
                task_id=task_id,
                effect_plan=effect_plan,
                current_branch=current_branch,
            )
            if (
                state.get("state") != "pushed"
                or marker.get("effect_plan") != effect_plan.to_dict()
                or marker.get("run_plan") != run_plan
                or marker.get("policy") != policy
                or marker.get("outcome_binding") != binding
                or any(
                    marker.get(field) != live[field]
                    for field in (
                        "base_head",
                        "feature_head",
                        "remote_url_digest",
                        "remote_identity_digest",
                    )
                )
            ):
                raise ValueError("E_PULL_REQUEST_PREPARE: marker binding drifted")
            marker_core = {
                **{key: value for key, value in marker.items() if key != "marker_digest"},
                "phase": "observe_only",
                "status": "UNKNOWN",
                "retry_policy": "observe_only",
            }
            state["pending_pull_request_effect"] = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            self._armed_pull_request_plans[task_id] = effect_plan.plan_digest
            return state

    def revalidate_pull_request_draft_before_execution(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> OutcomeEffectPlanV1:
        """Consume a process-local ticket after exact executor-edge revalidation."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_PULL_REQUEST_EXECUTION: exact plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        if self._armed_pull_request_plans.pop(task_id, None) != effect_plan.plan_digest:
            raise ValueError(
                "E_PULL_REQUEST_EXECUTION: current-process arm is unavailable"
            )
        try:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                marker = self._validated_pull_request_marker(
                    state, task_id=task_id
                )
                run_plan, policy, binding, live = self._pull_request_context_locked(
                    state,
                    task_id=task_id,
                    effect_plan=effect_plan,
                    current_branch=current_branch,
                )
                if (
                    state.get("state") != "pushed"
                    or marker.get("phase") != "observe_only"
                    or marker.get("status") != "UNKNOWN"
                    or marker.get("retry_policy") != "observe_only"
                    or marker.get("effect_plan") != effect_plan.to_dict()
                    or marker.get("run_plan") != run_plan
                    or marker.get("policy") != policy
                    or marker.get("outcome_binding") != binding
                    or any(
                        marker.get(field) != live[field]
                        for field in (
                            "base_head",
                            "feature_head",
                            "remote_url_digest",
                            "remote_identity_digest",
                        )
                    )
                ):
                    raise ValueError("executor-edge binding drifted")
        except ValueError as error:
            raise ValueError(
                "E_PULL_REQUEST_EXECUTION: executor-edge revalidation failed"
            ) from error
        return effect_plan

    def publish_pull_request_draft(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        receipt: RemoteOutcomeReceiptV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Publish one read-before-write PR observation without provider access."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_PULL_REQUEST_PREPARE: exact plan is required")
        if type(receipt) is not RemoteOutcomeReceiptV1:
            raise ValueError(
                "E_PULL_REQUEST_OUTCOME_RECEIPT: exact receipt is required"
            )
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        receipt = RemoteOutcomeReceiptV1.from_dict(receipt.to_dict())
        if effect_plan.effect != "pull_request" or receipt.effect != "pull_request":
            raise ValueError("E_PULL_REQUEST_PREPARE: PR contracts are required")
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            try:
                pending = (
                    self._validated_pull_request_marker(state, task_id=task_id)
                    if state.get("pending_pull_request_effect") is not None
                    else None
                )
                run_plan, policy, canonical_binding, live = (
                    self._pull_request_context_locked(
                        state,
                        task_id=task_id,
                        effect_plan=effect_plan,
                        current_branch=current_branch,
                    )
                )
            except ValueError as error:
                raise ValueError(
                    "E_PULL_REQUEST_PREPARE: canonical pushed lineage drifted"
                ) from error
            recovering = (
                state.get("state") == "blocked"
                and state.get("resume_state") == "pushed"
                and pending is not None
                and pending.get("phase") == "observe_only"
            )
            receipt_plan_fields = (
                "task_id", "task_digest", "run_plan_digest", "requested_outcome",
                "repository", "remote", "remote_url", "remote_url_digest",
                "remote_identity_digest", "base", "branch", "head_sha",
                "scope_paths", "subject_digest", "policy_digest", "effect",
                "title_digest", "body_digest", "draft",
            )
            if (
                (state.get("state") != "pushed" and not recovering)
                or receipt.effect_plan_digest != effect_plan.plan_digest
                or any(
                    getattr(receipt, field) != getattr(effect_plan, field)
                    for field in receipt_plan_fields
                )
                or (
                    pending is not None
                    and (
                        pending.get("effect_plan") != effect_plan.to_dict()
                        or pending.get("run_plan") != run_plan
                        or pending.get("policy") != policy
                        or pending.get("outcome_binding") != canonical_binding
                        or any(
                            pending.get(field) != live[field]
                            for field in (
                                "base_head", "feature_head",
                                "remote_url_digest", "remote_identity_digest",
                            )
                        )
                    )
                )
            ):
                raise ValueError(
                    "E_PULL_REQUEST_OUTCOME_BINDING: plan or receipt drifted"
                )
            receipt_digests = list(
                state.get("pull_request_outcome_receipt_digests", [])
            )
            if receipt.receipt_digest in receipt_digests:
                raise ValueError(
                    "E_PULL_REQUEST_OUTCOME_REPLAY: receipt was already published"
                )
            receipt_digests.append(receipt.receipt_digest)
            if receipt.status == "ABSENT" and pending is None:
                marker_core = {
                    "schema_version": 1,
                    "task_id": task_id,
                    "phase": "prepared",
                    "effect_plan": effect_plan.to_dict(),
                    "outcome_binding": copy.deepcopy(canonical_binding),
                    "run_plan": copy.deepcopy(run_plan),
                    "policy": copy.deepcopy(policy),
                    "base_head": live["base_head"],
                    "feature_head": live["feature_head"],
                    "remote_url_digest": live["remote_url_digest"],
                    "remote_identity_digest": live["remote_identity_digest"],
                    "absence_receipt": receipt.to_dict(),
                    "absence_receipt_digest": receipt.receipt_digest,
                    "observed_at": receipt.observed_at,
                    "status": "ABSENT",
                    "retry_policy": "observe_before_write",
                    "authorizes": False,
                }
                marker = {
                    **marker_core,
                    "marker_digest": contract_digest(marker_core),
                }
                if len(json.dumps(marker, sort_keys=True).encode("utf-8")) > 131_072:
                    raise ValueError("E_PULL_REQUEST_PREPARE: marker exceeds byte cap")
                state["pending_pull_request_effect"] = marker
                state["resume_forbidden"] = True
                state["generation"] = int(state.get("generation", 0)) + 1
                state["updated_at"] = _utc_now()
                state["pull_request_outcome_receipt_digests"] = receipt_digests
                _atomic_json(self._path(task_id), state)
                return state
            if receipt.status in {"FAIL", "UNKNOWN", "ABSENT"}:
                if pending is not None:
                    marker_core = {
                        **{
                            key: value
                            for key, value in pending.items()
                            if key != "marker_digest"
                        },
                        "phase": "observe_only",
                        "status": receipt.status,
                        "retry_policy": "observe_only",
                        "receipt_digest": receipt.receipt_digest,
                        "latest_observed_at": receipt.observed_at,
                    }
                    state["pending_pull_request_effect"] = {
                        **marker_core,
                        "marker_digest": contract_digest(marker_core),
                    }
                state.update(
                    {
                        "state": "blocked",
                        "resume_state": "pushed",
                        "resume_forbidden": True,
                        "block_reason": f"E_PULL_REQUEST_OUTCOME_{receipt.status}",
                        "generation": int(state.get("generation", 0)) + 1,
                        "updated_at": _utc_now(),
                    }
                )
                state.setdefault("evidence", {})["pull_request_observation"] = {
                    "status": receipt.status,
                    "receipt_digest": receipt.receipt_digest,
                    "observed_at": receipt.observed_at,
                    "authorizes": False,
                }
                state["pull_request_outcome_receipt_digests"] = receipt_digests
                _atomic_json(self._path(task_id), state)
                return state
            allowed_dispositions = (
                {"created", "observed_existing"}
                if pending is not None and pending.get("phase") == "observe_only"
                else {"observed_existing"}
            )
            if receipt.status != "PASS" or receipt.disposition not in allowed_dispositions:
                raise ValueError(
                    "E_PULL_REQUEST_OUTCOME_BINDING: observation phase is invalid"
                )
            evidence = {
                "pull_request": {
                    "number": receipt.observed_pr_number,
                    "url": receipt.observed_pr_url,
                    "head_commit": receipt.observed_head_sha,
                },
                "draft": True,
                "disposition": receipt.disposition,
                "receipt_digest": receipt.receipt_digest,
            }
            _validate_transition_evidence(
                "pr_draft", {"pull_request": evidence["pull_request"]}
            )
            state.update(
                {
                    "state": "pr_draft",
                    "resume_state": None,
                    "resume_forbidden": False,
                    "block_reason": None,
                    "generation": int(state.get("generation", 0)) + 1,
                    "updated_at": _utc_now(),
                }
            )
            state.setdefault("evidence", {})["pr_draft"] = evidence
            state["pull_request_effect_plan"] = effect_plan.to_dict()
            state["pull_request_outcome_receipt_digests"] = receipt_digests
            state.pop("pending_pull_request_effect", None)
            _atomic_json(self._path(task_id), state)
            return state

    def publish_pull_request_readiness(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        receipts: tuple[RemoteOutcomeReceiptV1, ...],
        current_branch: str,
    ) -> dict[str, Any]:
        """Persist an exact draft-to-ready proposal after bounded observations."""

        from control_plane.host_bridge import build_pull_request_ready_effect_plan

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_PR_READINESS_PROOF: exact PR plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        if (
            effect_plan.effect != "pull_request"
            or not isinstance(receipts, tuple)
            or len(receipts) != 3
            or any(type(receipt) is not RemoteOutcomeReceiptV1 for receipt in receipts)
        ):
            raise ValueError("E_PR_READINESS_RECEIPTS: exact receipt set is required")
        parsed = tuple(RemoteOutcomeReceiptV1.from_dict(item.to_dict()) for item in receipts)
        by_kind = {receipt.observation_kind: receipt for receipt in parsed}
        if set(by_kind) != {"checks", "review_threads", "comments"} or len(by_kind) != 3:
            raise ValueError("E_PR_READINESS_RECEIPTS: receipt kinds must be exact")
        parsed = (
            by_kind["checks"], by_kind["review_threads"], by_kind["comments"]
        )
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            if state.get("revision_required") is not None:
                self._validated_pull_request_revision_required(
                    state, task_id=task_id
                )
                raise ValueError(
                    "E_PR_READINESS_REVISION_REQUIRED: revision marker is pending"
                )
            published = list(state.get("pr_readiness_receipt_digests", []))
            if (
                any(SHA256_DIGEST.fullmatch(str(item)) is None for item in published)
                or len(set(published)) != len(published)
                or any(receipt.receipt_digest in published for receipt in parsed)
            ):
                raise ValueError("E_PR_READINESS_REPLAY: receipt was already published")
            if state.get("pending_pull_request_ready_effect") is not None:
                self._validated_pull_request_ready_marker(
                    state, task_id=task_id
                )
                raise ValueError(
                    "E_PR_READY_OBSERVE_ONLY: observe exact PR; never mark ready again"
                )
            try:
                run_plan, policy, canonical_binding, live = self._pull_request_context_locked(
                    state, task_id=task_id, effect_plan=effect_plan,
                    current_branch=current_branch,
                )
            except ValueError as error:
                raise ValueError("E_PR_READINESS_PROOF: canonical draft lineage drifted") from error
            prior_pr = state.get("evidence", {}).get("pr_draft", {}).get("pull_request")
            if (
                state.get("state") != "pr_draft"
                or state.get("pull_request_effect_plan") != effect_plan.to_dict()
                or state.get("outcome_binding") != canonical_binding
                or effect_plan.run_plan_digest != run_plan.get("plan_digest")
                or effect_plan.policy_digest != contract_digest(policy)
                or live.get("feature_head") != effect_plan.head_sha
                or not isinstance(prior_pr, Mapping)
                or set(prior_pr) != {"number", "url", "head_commit"}
            ):
                raise ValueError("E_PR_READINESS_PROOF: persisted draft binding drifted")
            required = tuple(item[3] for item in effect_plan.required_checks)
            checks, threads, comments = (
                by_kind["checks"], by_kind["review_threads"], by_kind["comments"]
            )
            plan_fields = (
                "task_id", "task_digest", "run_plan_digest", "requested_outcome",
                "repository", "remote", "remote_url", "remote_url_digest",
                "remote_identity_digest", "base", "branch", "head_sha",
                "scope_paths", "subject_digest", "policy_digest", "effect",
                "title_digest", "body_digest", "draft",
            )
            if (
                any(receipt.effect_plan_digest != effect_plan.plan_digest for receipt in parsed)
                or any(
                    getattr(receipt, field) != getattr(effect_plan, field)
                    for receipt in parsed for field in plan_fields
                )
                or any(
                    (receipt.observed_pr_number, receipt.observed_pr_url, receipt.observed_head_sha)
                    != (prior_pr["number"], prior_pr["url"], prior_pr["head_commit"])
                    for receipt in parsed
                )
                or checks.required_check_digests != required
                or tuple(item[0] for item in checks.check_results) != required
            ):
                raise ValueError("E_PR_READINESS_CHECKS: receipt bindings are not exact")
            receipt_digests = tuple(receipt.receipt_digest for receipt in parsed)
            unknown = next((receipt.observation_kind for receipt in parsed if receipt.status == "UNKNOWN"), None)
            check_statuses = tuple(status for _, status in checks.check_results)
            has_unknown_check = "UNKNOWN" in check_statuses
            has_failed_check = "FAIL" in check_statuses
            unresolved = tuple(
                row for receipt in (threads, comments) for row in receipt.feedback
                if row[3] == "unresolved"
            )
            state["pr_readiness_receipt_digests"] = [*published, *receipt_digests]
            if unknown is not None or has_unknown_check:
                reason = (
                    f"E_PR_READINESS_{unknown.upper()}_UNKNOWN"
                    if unknown is not None else "E_PR_READINESS_CHECKS_UNKNOWN"
                )
                state.update({"state": "blocked", "resume_state": "pr_draft",
                              "resume_forbidden": True, "block_reason": reason,
                              "generation": int(state.get("generation", 0)) + 1,
                              "updated_at": _utc_now()})
                state.setdefault("evidence", {})["pr_readiness"] = {
                    "status": "UNKNOWN", "receipt_digests": list(receipt_digests),
                    "authorizes": False,
                }
                _atomic_json(self._path(task_id), state)
                return state
            critical_or_important = any(
                row[2] in {"Critical", "Important"} for row in unresolved
            )
            if has_failed_check or critical_or_important:
                reason = "checks_failed" if has_failed_check else "review_feedback"
                next_generation = int(state.get("generation", 0)) + 1
                marker_core = {
                    "schema_version": 1,
                    "kind": "PullRequestRevisionRequiredV1",
                    "task_id": task_id,
                    "generation": next_generation,
                    "pull_request": dict(prior_pr),
                    "head_sha": effect_plan.head_sha,
                    "effect_plan_digest": effect_plan.plan_digest,
                    "policy_digest": effect_plan.policy_digest,
                    "outcome_binding_digest": canonical_binding["binding_digest"],
                    "receipts": [receipt.to_dict() for receipt in parsed],
                    "receipt_digests": list(receipt_digests),
                    "reason": reason,
                    "authorizes": False,
                }
                marker = {
                    **marker_core,
                    "marker_digest": contract_digest(marker_core),
                }
                state.update({"state": "pr_draft", "resume_state": None,
                              "resume_forbidden": True,
                              "block_reason": "E_PR_READINESS_REVISION_REQUIRED",
                              "revision_required": marker,
                              "generation": next_generation,
                              "updated_at": _utc_now()})
                state.setdefault("evidence", {})["pr_readiness"] = {
                    "status": "FAIL", "receipt_digests": list(receipt_digests),
                    "authorizes": False,
                }
                _atomic_json(self._path(task_id), state)
                return state
            if unresolved:
                state.update({"state": "blocked", "resume_state": "pr_draft",
                              "resume_forbidden": True,
                              "block_reason": "E_PR_READINESS_UNRESOLVED_MINOR",
                              "generation": int(state.get("generation", 0)) + 1,
                              "updated_at": _utc_now()})
                state.setdefault("evidence", {})["pr_readiness"] = {
                    "status": "BLOCKED", "receipt_digests": list(receipt_digests),
                    "authorizes": False,
                }
                _atomic_json(self._path(task_id), state)
                return state
            if any(receipt.status != "PASS" for receipt in parsed):
                raise ValueError("E_PR_READINESS_RECEIPTS: status is invalid")
            pull_request_digest = contract_digest({
                "number": prior_pr["number"], "url": prior_pr["url"],
                "head": prior_pr["head_commit"], "draft": True,
            })
            checks_digest = contract_digest(list(receipt_digests))
            ready_plan = build_pull_request_ready_effect_plan(
                draft_effect_plan=effect_plan,
                outcome_binding=canonical_binding,
                pull_request_number=prior_pr["number"],
                pull_request_url=prior_pr["url"],
                readiness_receipts=parsed,
            )
            marker_core = {
                "schema_version": 1,
                "kind": "PendingPullRequestReadyEffectV1",
                "task_id": task_id,
                "phase": "prepared",
                "effect_plan": ready_plan.to_dict(),
                "draft_effect_plan": effect_plan.to_dict(),
                "outcome_binding": copy.deepcopy(canonical_binding),
                "run_plan": copy.deepcopy(run_plan),
                "policy": copy.deepcopy(policy),
                "pr_draft": copy.deepcopy(dict(prior_pr)),
                "readiness_receipts": [receipt.to_dict() for receipt in parsed],
                "readiness_receipt_digests": list(receipt_digests),
                "pull_request_digest": pull_request_digest,
                "checks_digest": checks_digest,
                "status": "PENDING",
                "retry_policy": "observe_before_write",
                "authorizes": False,
            }
            marker = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            if len(json.dumps(marker, sort_keys=True).encode("utf-8")) > 131_072:
                raise ValueError("E_PR_READY_PREPARE: marker exceeds byte cap")
            state.update({"state": "pr_draft", "resume_state": None,
                          "resume_forbidden": False, "block_reason": None,
                          "generation": int(state.get("generation", 0)) + 1,
                          "updated_at": _utc_now()})
            state.pop("revision_required", None)
            state["pending_pull_request_ready_effect"] = marker
            state.setdefault("evidence", {})["pr_readiness"] = {
                "status": "PASS",
                "receipt_digests": list(receipt_digests),
                "pull_request_digest": pull_request_digest,
                "checks_digest": checks_digest, "authorizes": False,
            }
            _atomic_json(self._path(task_id), state)
            return state

    @staticmethod
    def _validated_pull_request_ready_marker(
        state: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        """Validate the durable marker that precedes draft-to-ready mutation."""

        from control_plane.host_bridge import build_pull_request_ready_effect_plan
        from control_plane.policy import validate_policy
        from control_plane.run_workflow import (
            validate_outcome_binding,
            validate_run_plan,
        )

        marker = state.get("pending_pull_request_ready_effect")
        required = {
            "schema_version", "kind", "task_id", "phase", "effect_plan",
            "draft_effect_plan", "outcome_binding", "run_plan", "policy",
            "pr_draft", "readiness_receipts", "readiness_receipt_digests",
            "pull_request_digest", "checks_digest", "status", "retry_policy",
            "authorizes", "marker_digest",
        }
        optional = {"latest_receipt", "latest_receipt_digest"}
        if (
            not isinstance(marker, Mapping)
            or not required.issubset(marker)
            or not set(marker).issubset(required | optional)
            or marker.get("schema_version") != 1
            or marker.get("kind") != "PendingPullRequestReadyEffectV1"
            or marker.get("task_id") != task_id
            or marker.get("phase") not in {"prepared", "observe_only"}
            or marker.get("authorizes") is not False
            or not isinstance(marker.get("effect_plan"), Mapping)
            or not isinstance(marker.get("draft_effect_plan"), Mapping)
            or not isinstance(marker.get("outcome_binding"), Mapping)
            or not isinstance(marker.get("run_plan"), Mapping)
            or not isinstance(marker.get("policy"), Mapping)
            or not isinstance(marker.get("pr_draft"), Mapping)
            or not isinstance(marker.get("readiness_receipts"), list)
            or not isinstance(marker.get("readiness_receipt_digests"), list)
            or validate_outcome_binding(marker["outcome_binding"])
            or validate_run_plan(marker["run_plan"])
            or validate_policy(marker["policy"])
            or marker.get("marker_digest")
            != contract_digest({
                key: value for key, value in marker.items()
                if key != "marker_digest"
            })
        ):
            raise ValueError("E_PR_READY_PREPARE: marker is invalid")
        try:
            plan = OutcomeEffectPlanV1.from_dict(marker["effect_plan"])
            draft_plan = OutcomeEffectPlanV1.from_dict(
                marker["draft_effect_plan"]
            )
            receipts = tuple(
                RemoteOutcomeReceiptV1.from_dict(item)
                for item in marker["readiness_receipts"]
            )
            rebuilt = build_pull_request_ready_effect_plan(
                draft_effect_plan=draft_plan,
                outcome_binding=marker["outcome_binding"],
                pull_request_number=marker["pr_draft"]["number"],
                pull_request_url=marker["pr_draft"]["url"],
                readiness_receipts=receipts,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("E_PR_READY_PREPARE: marker contracts are invalid") from error
        receipt_digests = [receipt.receipt_digest for receipt in receipts]
        persisted_pr = (
            state.get("evidence", {})
            .get("pr_draft", {})
            .get("pull_request")
        )
        readiness_evidence = state.get("evidence", {}).get("pr_readiness")
        published_digests = state.get("pr_readiness_receipt_digests")
        if (
            rebuilt.to_dict() != plan.to_dict()
            or state.get("pull_request_effect_plan") != draft_plan.to_dict()
            or persisted_pr != marker["pr_draft"]
            or state.get("outcome_binding") != marker["outcome_binding"]
            or marker["readiness_receipt_digests"] != receipt_digests
            or not isinstance(published_digests, list)
            or published_digests[-len(receipt_digests):] != receipt_digests
            or marker["checks_digest"] != contract_digest(receipt_digests)
            or marker["pull_request_digest"] != contract_digest({
                "number": marker["pr_draft"].get("number"),
                "url": marker["pr_draft"].get("url"),
                "head": marker["pr_draft"].get("head_commit"),
                "draft": True,
            })
            or plan.run_plan_digest != marker["run_plan"].get("plan_digest")
            or plan.policy_digest != contract_digest(marker["policy"])
            or plan.subject_digest
            != marker["outcome_binding"].get("binding_digest")
            or readiness_evidence != {
                "status": "PASS",
                "receipt_digests": receipt_digests,
                "pull_request_digest": marker["pull_request_digest"],
                "checks_digest": marker["checks_digest"],
                "authorizes": False,
            }
        ):
            raise ValueError("E_PR_READY_PREPARE: marker binding drifted")
        if marker["phase"] == "prepared":
            if (
                set(marker) != required
                or marker.get("status") != "PENDING"
                or marker.get("retry_policy") != "observe_before_write"
            ):
                raise ValueError("E_PR_READY_PREPARE: prepared marker is invalid")
        else:
            allowed = (
                required | optional
                if state.get("state") == "blocked"
                else required
            )
            if (
                set(marker) != allowed
                or marker.get("status") != "UNKNOWN"
                or marker.get("retry_policy") != "observe_only"
            ):
                raise ValueError("E_PR_READY_PREPARE: observe-only marker is invalid")
        if "latest_receipt" in marker:
            try:
                latest = RemoteOutcomeReceiptV1.from_dict(marker["latest_receipt"])
            except (TypeError, ValueError) as error:
                raise ValueError("E_PR_READY_PREPARE: latest receipt is invalid") from error
            if (
                marker.get("latest_receipt_digest") != latest.receipt_digest
                or latest.effect_plan_digest != plan.plan_digest
                or latest.status != "UNKNOWN"
                or state.get("evidence", {}).get("pr_ready_observation")
                != {
                    "status": "UNKNOWN",
                    "receipt_digest": latest.receipt_digest,
                    "observed_at": latest.observed_at,
                    "authorizes": False,
                }
            ):
                raise ValueError("E_PR_READY_PREPARE: latest receipt drifted")
        return copy.deepcopy(dict(marker))

    def arm_pull_request_ready(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Durably enter observe-only mode before marking a draft ready."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_PR_READY_PREPARE: exact plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        if effect_plan.operation != "mark_pull_request_ready":
            raise ValueError("E_PR_READY_PREPARE: ready plan is required")
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_pull_request_ready_marker(
                state, task_id=task_id
            )
            if marker["phase"] != "prepared":
                raise ValueError(
                    "E_PR_READY_OBSERVE_ONLY: observe exact PR; never mark ready again"
                )
            run_plan, policy, binding, live = self._pull_request_context_locked(
                state, task_id=task_id, effect_plan=effect_plan,
                current_branch=current_branch,
            )
            if (
                state.get("state") != "pr_draft"
                or marker.get("effect_plan") != effect_plan.to_dict()
                or marker.get("run_plan") != run_plan
                or marker.get("policy") != policy
                or marker.get("outcome_binding") != binding
                or live.get("feature_head") != effect_plan.head_sha
            ):
                raise ValueError("E_PR_READY_PREPARE: marker binding drifted")
            marker_core = {
                **{key: value for key, value in marker.items()
                   if key != "marker_digest"},
                "phase": "observe_only",
                "status": "UNKNOWN",
                "retry_policy": "observe_only",
            }
            state["pending_pull_request_ready_effect"] = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            self._armed_pull_request_ready_plans[task_id] = effect_plan.plan_digest
            return state

    def revalidate_pull_request_ready_before_execution(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        current_branch: str,
    ) -> OutcomeEffectPlanV1:
        """Consume one process-local ticket after exact ready-edge revalidation."""

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_PR_READY_EXECUTION: exact plan is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        if (
            self._armed_pull_request_ready_plans.pop(task_id, None)
            != effect_plan.plan_digest
        ):
            raise ValueError(
                "E_PR_READY_EXECUTION: current-process arm is unavailable"
            )
        try:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                marker = self._validated_pull_request_ready_marker(
                    state, task_id=task_id
                )
                run_plan, policy, binding, live = self._pull_request_context_locked(
                    state, task_id=task_id, effect_plan=effect_plan,
                    current_branch=current_branch,
                )
                if (
                    state.get("state") != "pr_draft"
                    or marker.get("phase") != "observe_only"
                    or marker.get("status") != "UNKNOWN"
                    or marker.get("retry_policy") != "observe_only"
                    or marker.get("effect_plan") != effect_plan.to_dict()
                    or marker.get("run_plan") != run_plan
                    or marker.get("policy") != policy
                    or marker.get("outcome_binding") != binding
                    or live.get("feature_head") != effect_plan.head_sha
                ):
                    raise ValueError("executor-edge binding drifted")
        except ValueError as error:
            raise ValueError(
                "E_PR_READY_EXECUTION: executor-edge revalidation failed"
            ) from error
        return effect_plan

    def publish_pull_request_ready(
        self,
        task_id: str,
        *,
        effect_plan: OutcomeEffectPlanV1,
        receipt: RemoteOutcomeReceiptV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Publish one observed ready result; UNKNOWN is permanently observe-only."""

        from control_plane.run_workflow import advance_outcome_binding

        if type(effect_plan) is not OutcomeEffectPlanV1:
            raise ValueError("E_PR_READY_PREPARE: exact plan is required")
        if type(receipt) is not RemoteOutcomeReceiptV1:
            raise ValueError("E_PR_READY_OUTCOME_RECEIPT: exact receipt is required")
        effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
        receipt = RemoteOutcomeReceiptV1.from_dict(receipt.to_dict())
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_pull_request_ready_marker(
                state, task_id=task_id
            )
            try:
                run_plan, policy, binding, live = self._pull_request_context_locked(
                    state, task_id=task_id, effect_plan=effect_plan,
                    current_branch=current_branch,
                )
            except ValueError as error:
                raise ValueError("E_PR_READY_PREPARE: canonical draft lineage drifted") from error
            recovering = (
                state.get("state") == "blocked"
                and state.get("resume_state") == "pr_draft"
                and marker.get("phase") == "observe_only"
            )
            receipt_fields = (
                "task_id", "task_digest", "run_plan_digest", "requested_outcome",
                "repository", "remote", "remote_url", "remote_url_digest",
                "remote_identity_digest", "base", "branch", "head_sha",
                "scope_paths", "subject_digest", "policy_digest", "effect",
                "title_digest", "body_digest", "draft",
            )
            if (
                (state.get("state") != "pr_draft" and not recovering)
                or marker.get("phase") != "observe_only"
                or marker.get("effect_plan") != effect_plan.to_dict()
                or marker.get("run_plan") != run_plan
                or marker.get("policy") != policy
                or marker.get("outcome_binding") != binding
                or live.get("feature_head") != effect_plan.head_sha
                or receipt.effect_plan_digest != effect_plan.plan_digest
                or any(
                    getattr(receipt, field) != getattr(effect_plan, field)
                    for field in receipt_fields
                )
            ):
                raise ValueError("E_PR_READY_OUTCOME_BINDING: plan or receipt drifted")
            published = list(
                state.get("pull_request_ready_outcome_receipt_digests", [])
            )
            if (
                any(SHA256_DIGEST.fullmatch(str(item)) is None for item in published)
                or len(set(published)) != len(published)
                or receipt.receipt_digest in published
            ):
                raise ValueError("E_PR_READY_OUTCOME_REPLAY: receipt was already published")
            published.append(receipt.receipt_digest)
            if receipt.status == "UNKNOWN":
                marker_core = {
                    **{key: value for key, value in marker.items()
                       if key != "marker_digest"},
                    "phase": "observe_only",
                    "status": "UNKNOWN",
                    "retry_policy": "observe_only",
                    "latest_receipt": receipt.to_dict(),
                    "latest_receipt_digest": receipt.receipt_digest,
                }
                state["pending_pull_request_ready_effect"] = {
                    **marker_core,
                    "marker_digest": contract_digest(marker_core),
                }
                state.update({
                    "state": "blocked", "resume_state": "pr_draft",
                    "resume_forbidden": True,
                    "block_reason": "E_PR_READY_OUTCOME_UNKNOWN",
                    "generation": int(state.get("generation", 0)) + 1,
                    "updated_at": _utc_now(),
                })
                state.setdefault("evidence", {})["pr_ready_observation"] = {
                    "status": "UNKNOWN",
                    "receipt_digest": receipt.receipt_digest,
                    "observed_at": receipt.observed_at,
                    "authorizes": False,
                }
                state["pull_request_ready_outcome_receipt_digests"] = published
                _atomic_json(self._path(task_id), state)
                return state
            if receipt.status != "PASS":
                raise ValueError("E_PR_READY_OUTCOME_BINDING: PASS receipt is required")
            prior_pr = marker["pr_draft"]
            if (
                receipt.observed_pr_number != prior_pr.get("number")
                or receipt.observed_pr_url != prior_pr.get("url")
                or receipt.observed_head_sha != prior_pr.get("head_commit")
            ):
                raise ValueError("E_PR_READY_OUTCOME_BINDING: PR identity drifted")
            pull_request_digest = contract_digest({
                "number": prior_pr["number"], "url": prior_pr["url"],
                "head": prior_pr["head_commit"], "draft": False,
            })
            successor = advance_outcome_binding(
                binding, effect_id="pull_request", observation={
                    "pull_request_digest": pull_request_digest,
                    "checks_digest": marker["checks_digest"],
                    "head": effect_plan.head_sha,
                },
            )
            checks_ok = {"ok": True, "head_commit": effect_plan.head_sha}
            _validate_transition_evidence("pr_ready", {"checks_ok": checks_ok})
            state.update({
                "state": "pr_ready", "resume_state": None,
                "resume_forbidden": False, "block_reason": None,
                "outcome_binding": successor,
                "generation": int(state.get("generation", 0)) + 1,
                "updated_at": _utc_now(),
            })
            state.setdefault("evidence", {})["pr_ready"] = {
                "checks_ok": checks_ok,
                "receipt_digests": marker["readiness_receipt_digests"],
                "ready_receipt_digest": receipt.receipt_digest,
                "pull_request_digest": pull_request_digest,
                "checks_digest": marker["checks_digest"],
                "authorizes": False,
            }
            state["pull_request_ready_outcome_receipt_digests"] = published
            state.pop("pending_pull_request_ready_effect", None)
            _atomic_json(self._path(task_id), state)
            return state

    @staticmethod
    def _validated_integration_marker(
        state: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        """Validate the durable read-before-write squash marker."""

        from control_plane.policy import validate_policy
        from control_plane.run_workflow import (
            validate_outcome_binding,
            validate_run_plan,
        )

        marker = state.get("pending_integration_effect")
        required = {
            "schema_version",
            "kind",
            "task_id",
            "phase",
            "effect_plan",
            "outcome_binding",
            "run_plan",
            "policy",
            "pr_ready",
            "status",
            "retry_policy",
            "authorizes",
            "marker_digest",
        }
        optional = {
            "ready_receipt",
            "ready_receipt_digest",
            "latest_receipt_digest",
        }
        if (
            not isinstance(marker, Mapping)
            or not required.issubset(marker)
            or not set(marker).issubset(required | optional)
            or marker.get("schema_version") != 1
            or marker.get("kind") != "PendingIntegrationEffectV1"
            or marker.get("task_id") != task_id
            or marker.get("phase") not in {"prepared", "observe_only"}
            or marker.get("authorizes") is not False
            or not isinstance(marker.get("effect_plan"), Mapping)
            or not isinstance(marker.get("outcome_binding"), Mapping)
            or not isinstance(marker.get("run_plan"), Mapping)
            or not isinstance(marker.get("policy"), Mapping)
            or not isinstance(marker.get("pr_ready"), Mapping)
            or validate_outcome_binding(marker["outcome_binding"])
            or validate_run_plan(marker["run_plan"])
            or validate_policy(marker["policy"])
            or marker.get("marker_digest")
            != contract_digest(
                {
                    key: value
                    for key, value in marker.items()
                    if key != "marker_digest"
                }
            )
        ):
            raise ValueError("E_INTEGRATION_PREPARE: marker is invalid")
        try:
            plan = IntegrationEffectPlanV1.from_dict(marker["effect_plan"])
        except ValueError as error:
            raise ValueError(
                "E_INTEGRATION_PREPARE: effect plan is invalid"
            ) from error
        if marker["phase"] == "prepared":
            if (
                set(marker) != required
                or marker.get("status") != "PENDING"
                or marker.get("retry_policy") != "read_before_write"
            ):
                raise ValueError("E_INTEGRATION_PREPARE: prepared marker is invalid")
        else:
            try:
                ready = IntegrationReceiptV1.from_dict(marker["ready_receipt"])
            except (KeyError, ValueError) as error:
                raise ValueError(
                    "E_INTEGRATION_PREPARE: READY receipt is invalid"
                ) from error
            if (
                marker.get("retry_policy") != "observe_only"
                or marker.get("status") not in {"UNKNOWN", "FAIL", "PASS"}
                or marker.get("ready_receipt_digest") != ready.receipt_digest
                or ready.status != "READY"
                or ready.effect_plan_digest != plan.plan_digest
            ):
                raise ValueError(
                    "E_INTEGRATION_PREPARE: observe-only marker is invalid"
                )
        if (
            plan.subject_digest != marker["outcome_binding"].get("binding_digest")
            or plan.run_plan_digest != marker["run_plan"].get("plan_digest")
            or plan.policy_digest != contract_digest(marker["policy"])
            or marker["policy"].get("git", {}).get("integration_strategy")
            != "squash"
        ):
            raise ValueError("E_INTEGRATION_PREPARE: marker binding drifted")
        return copy.deepcopy(dict(marker))

    def prepare_integration(
        self,
        task_id: str,
        *,
        effect_plan: IntegrationEffectPlanV1,
        current_branch: str,
        now: str | None = None,
        clock: object | None = None,
    ) -> dict[str, Any]:
        """Persist the exact squash subject before any provider observation."""

        if type(effect_plan) is not IntegrationEffectPlanV1:
            raise ValueError("E_INTEGRATION_PREPARE: exact plan is required")
        effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
        current = _integration_current_time(
            now=now, clock=clock, code="E_INTEGRATION_PREPARE"
        )
        if not _integration_plan_is_current(
            effect_plan, current=current, code="E_INTEGRATION_PREPARE"
        ):
            raise ValueError("E_INTEGRATION_PREPARE: plan expired")
        try:
            run_plan, policy = _canonical_remote_write_inputs(
                self.state_dir, task_id=task_id, effect_plan=effect_plan
            )
        except ValueError as error:
            raise ValueError(
                "E_INTEGRATION_PREPARE: canonical plan or policy drifted"
            ) from error
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            binding = state.get("outcome_binding")
            pr_ready = state.get("evidence", {}).get("pr_ready")
            if (
                state.get("state") != "pr_ready"
                or state.get("outcome") != "integration"
                or state.get("branch") != current_branch
                or current_branch != effect_plan.branch
                or state.get("task_digest") != effect_plan.task_digest
                or state.get("run_plan_digest") != effect_plan.run_plan_digest
                or state.get("pending_integration_effect") is not None
                or binding is None
                or binding != dict(binding)
                or effect_plan.subject_digest != binding.get("binding_digest")
                or binding.get("consumed_effect_ids")
                != ["local_write", "commit", "remote_write", "pull_request"]
                or binding.get("pull_request_digest")
                != effect_plan.pull_request_digest
                or binding.get("checks_digest") != effect_plan.checks_digest
                or not isinstance(pr_ready, Mapping)
                or pr_ready.get("pull_request_digest")
                != effect_plan.pull_request_digest
                or pr_ready.get("checks_digest") != effect_plan.checks_digest
                or run_plan.get("plan_digest") != effect_plan.run_plan_digest
                or policy.get("git", {}).get("integration_strategy") != "squash"
            ):
                raise ValueError(
                    "E_INTEGRATION_PREPARE: pr_ready binding drifted"
                )
            marker_core = {
                "schema_version": 1,
                "kind": "PendingIntegrationEffectV1",
                "task_id": task_id,
                "phase": "prepared",
                "effect_plan": effect_plan.to_dict(),
                "outcome_binding": copy.deepcopy(dict(binding)),
                "run_plan": copy.deepcopy(run_plan),
                "policy": copy.deepcopy(policy),
                "pr_ready": copy.deepcopy(dict(pr_ready)),
                "status": "PENDING",
                "retry_policy": "read_before_write",
                "authorizes": False,
            }
            marker = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            if len(json.dumps(marker, sort_keys=True).encode("utf-8")) > 131_072:
                raise ValueError("E_INTEGRATION_PREPARE: marker exceeds byte cap")
            state["pending_integration_effect"] = marker
            state["resume_forbidden"] = True
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            return state

    def arm_integration_observe_only(
        self,
        task_id: str,
        *,
        effect_plan: IntegrationEffectPlanV1,
        receipt: IntegrationReceiptV1,
        current_branch: str,
        now: str | None = None,
        clock: object | None = None,
    ) -> dict[str, Any]:
        """Bind the READY read, then durably prohibit a blind second write."""

        if (
            type(effect_plan) is not IntegrationEffectPlanV1
            or type(receipt) is not IntegrationReceiptV1
        ):
            raise ValueError("E_INTEGRATION_PREPARE: exact contracts are required")
        effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
        receipt = IntegrationReceiptV1.from_dict(receipt.to_dict())
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_integration_marker(state, task_id=task_id)
            if marker["phase"] != "prepared":
                raise ValueError(
                    "E_INTEGRATION_OBSERVE_ONLY: observe exact PR; never merge again"
                )
            if (
                state.get("state") != "pr_ready"
                or state.get("branch") != current_branch
                or marker["effect_plan"] != effect_plan.to_dict()
                or marker["outcome_binding"] != state.get("outcome_binding")
                or receipt.status != "READY"
                or receipt.effect_plan_digest != effect_plan.plan_digest
                or receipt.subject_digest != effect_plan.subject_digest
            ):
                raise ValueError("E_INTEGRATION_PREPARE: READY binding drifted")
            current = _integration_current_time(
                now=now, clock=clock, code="E_INTEGRATION_PREPARE"
            )
            if not _integration_ready_is_fresh(
                effect_plan,
                receipt,
                current=current,
                code="E_INTEGRATION_PREPARE",
            ):
                raise ValueError("E_INTEGRATION_PREPARE: READY binding drifted")
            marker_core = {
                **{
                    key: value
                    for key, value in marker.items()
                    if key != "marker_digest"
                },
                "phase": "observe_only",
                "status": "UNKNOWN",
                "retry_policy": "observe_only",
                "ready_receipt": receipt.to_dict(),
                "ready_receipt_digest": receipt.receipt_digest,
            }
            state["pending_integration_effect"] = {
                **marker_core,
                "marker_digest": contract_digest(marker_core),
            }
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            self._armed_integration_plans[task_id] = effect_plan.plan_digest
            return state

    def revalidate_integration_before_execution(
        self,
        task_id: str,
        *,
        effect_plan: IntegrationEffectPlanV1,
        current_branch: str,
        now: str | None = None,
        clock: object | None = None,
    ) -> object:
        """Revalidate the exact durable subject at the executor edge once."""

        if type(effect_plan) is not IntegrationEffectPlanV1:
            raise ValueError("E_INTEGRATION_EXECUTION: exact plan is required")
        effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
        if self._armed_integration_plans.pop(task_id, None) != effect_plan.plan_digest:
            raise ValueError(
                "E_INTEGRATION_EXECUTION: current-process arm is unavailable"
            )
        try:
            if now is not None or not callable(clock):
                raise ValueError("live executor clock is required")
            run_plan, policy = _canonical_remote_write_inputs(
                self.state_dir, task_id=task_id, effect_plan=effect_plan
            )
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                marker = self._validated_integration_marker(
                    state, task_id=task_id
                )
                if (
                    state.get("state") != "pr_ready"
                    or state.get("branch") != current_branch
                    or marker.get("phase") != "observe_only"
                    or marker.get("status") != "UNKNOWN"
                    or marker.get("retry_policy") != "observe_only"
                    or marker.get("effect_plan") != effect_plan.to_dict()
                    or marker.get("outcome_binding") != state.get("outcome_binding")
                    or marker.get("run_plan") != run_plan
                    or marker.get("policy") != policy
                    or effect_plan.subject_digest
                    != state.get("outcome_binding", {}).get("binding_digest")
                ):
                    raise ValueError("executor-edge binding drifted")
                ready_receipt = IntegrationReceiptV1.from_dict(
                    marker["ready_receipt"]
                )
                current = _integration_current_time(
                    now=None, clock=clock, code="E_INTEGRATION_EXECUTION"
                )
                if not _integration_ready_is_fresh(
                    effect_plan,
                    ready_receipt,
                    current=current,
                    code="E_INTEGRATION_EXECUTION",
                ):
                    raise ValueError("executor-edge binding drifted")
                ready_deadline = _parse_outcome_time(
                    ready_receipt.observed_at,
                    code="E_INTEGRATION_EXECUTION",
                ) + timedelta(seconds=_INTEGRATION_READY_MAX_AGE_SECONDS)
                plan_expires = _parse_outcome_time(
                    effect_plan.expires_at,
                    code="E_INTEGRATION_EXECUTION",
                )
        except ValueError as error:
            raise ValueError(
                "E_INTEGRATION_EXECUTION: executor-edge revalidation failed"
            ) from error
        return _issue_integration_execution_ticket(
            effect_plan,
            clock=clock,
            issued_at=current,
            ready_deadline=ready_deadline,
            plan_expires=plan_expires,
        )

    def publish_integration(
        self,
        task_id: str,
        *,
        effect_plan: IntegrationEffectPlanV1,
        receipt: IntegrationReceiptV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Publish only an exact observation; never execute or retry merge."""

        if (
            type(effect_plan) is not IntegrationEffectPlanV1
            or type(receipt) is not IntegrationReceiptV1
        ):
            raise ValueError("E_INTEGRATION_RECEIPT: exact contracts are required")
        effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
        receipt = IntegrationReceiptV1.from_dict(receipt.to_dict())
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_integration_marker(state, task_id=task_id)
            recovering = (
                state.get("state") == "blocked"
                and state.get("resume_state") == "pr_ready"
            )
            published = list(state.get("integration_receipt_digests", []))
            if receipt.receipt_digest in published:
                raise ValueError("E_INTEGRATION_REPLAY: receipt was already published")
            if (
                (state.get("state") != "pr_ready" and not recovering)
                or state.get("branch") != current_branch
                or marker.get("phase") != "observe_only"
                or marker.get("retry_policy") != "observe_only"
                or marker.get("effect_plan") != effect_plan.to_dict()
                or marker.get("outcome_binding") != state.get("outcome_binding")
                or receipt.effect_plan_digest != effect_plan.plan_digest
                or receipt.subject_digest != effect_plan.subject_digest
            ):
                raise ValueError("E_INTEGRATION_BINDING: durable binding drifted")
            published.append(receipt.receipt_digest)
            if receipt.status in {"UNKNOWN", "FAIL"}:
                marker_core = {
                    **{
                        key: value
                        for key, value in marker.items()
                        if key != "marker_digest"
                    },
                    "status": receipt.status,
                    "latest_receipt_digest": receipt.receipt_digest,
                }
                state["pending_integration_effect"] = {
                    **marker_core,
                    "marker_digest": contract_digest(marker_core),
                }
                state.update(
                    {
                        "state": "blocked",
                        "resume_state": "pr_ready",
                        "resume_forbidden": True,
                        "block_reason": f"E_INTEGRATION_{receipt.status}",
                        "generation": int(state.get("generation", 0)) + 1,
                        "updated_at": _utc_now(),
                    }
                )
                state.setdefault("evidence", {})["integration_recovery"] = {
                    "status": receipt.status,
                    "receipt_digest": receipt.receipt_digest,
                    "next_action": "observe_exact_pull_request",
                    "authorizes": False,
                }
                state["integration_receipt_digests"] = published
                _atomic_json(self._path(task_id), state)
                return state
            if receipt.status != "PASS":
                raise ValueError("E_INTEGRATION_RECEIPT: PASS observation required")
            successor = apply_integration_receipt(
                outcome_binding=state["outcome_binding"],
                effect_plan=effect_plan,
                receipt=receipt,
            )
            state.update(
                {
                    "state": "merged",
                    "resume_state": None,
                    "resume_forbidden": False,
                    "block_reason": None,
                    "outcome_binding": successor,
                    "integration_effect_plan": effect_plan.to_dict(),
                    "integration_receipt": receipt.to_dict(),
                    "integration_receipt_digests": published,
                    "generation": int(state.get("generation", 0)) + 1,
                    "updated_at": _utc_now(),
                }
            )
            state.setdefault("evidence", {})["merged"] = {
                "merge_commit": receipt.observed_merge_sha,
                "strategy": "squash",
                "receipt_digest": receipt.receipt_digest,
                "authorizes": False,
            }
            state.pop("pending_integration_effect", None)
            _atomic_json(self._path(task_id), state)
            return state

    def _base_refresh_observation_path(self, task_id: str) -> Path:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        return (
            self.state_dir
            / "codex-control-plane"
            / "base-refresh-observations"
            / f"{task_id}.json"
        )

    @staticmethod
    def _base_refresh_directory_identity(
        descriptor: int,
        *,
        exact_mode: int | None,
    ) -> tuple[int, int, int, int, int]:
        try:
            current = os.fstat(descriptor)
        except OSError as error:
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: directory stat failed"
            ) from error
        mode = stat.S_IMODE(current.st_mode)
        if (
            not stat.S_ISDIR(current.st_mode)
            or current.st_uid != os.getuid()
            or current.st_nlink < 1
            or mode & 0o022
            or (exact_mode is not None and mode != exact_mode)
        ):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: directory identity is unsafe"
            )
        return (
            current.st_dev,
            current.st_ino,
            current.st_uid,
            mode,
            current.st_nlink,
        )

    def _open_base_refresh_registry_directory(
        self,
        *,
        create: bool,
    ) -> tuple[tuple[int, int, int], tuple[tuple[int, int, int, int, int], ...]]:
        """Open trusted registry ancestors without following symlinks."""

        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        opened: list[int] = []
        try:
            state_descriptor = os.open(self.state_dir, directory_flags)
            opened.append(state_descriptor)
            state_identity = self._base_refresh_directory_identity(
                state_descriptor, exact_mode=None
            )
            control_descriptor = os.open(
                "codex-control-plane",
                directory_flags,
                dir_fd=state_descriptor,
            )
            opened.append(control_descriptor)
            control_identity = self._base_refresh_directory_identity(
                control_descriptor, exact_mode=None
            )
            if create:
                try:
                    os.mkdir(
                        "base-refresh-observations",
                        0o700,
                        dir_fd=control_descriptor,
                    )
                except FileExistsError:
                    pass
                try:
                    registry_stat = os.stat(
                        "base-refresh-observations",
                        dir_fd=control_descriptor,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise ValueError(
                        "E_BASE_REFRESH_OBSERVATION: registry directory stat failed"
                    ) from error
                registry_mode = stat.S_IMODE(registry_stat.st_mode)
                if (
                    not stat.S_ISDIR(registry_stat.st_mode)
                    or registry_stat.st_uid != os.getuid()
                    or registry_stat.st_nlink < 1
                    or registry_mode & 0o022
                ):
                    raise ValueError(
                        "E_BASE_REFRESH_OBSERVATION: registry directory is unsafe"
                    )
                os.chmod(
                    "base-refresh-observations",
                    0o700,
                    dir_fd=control_descriptor,
                    follow_symlinks=False,
                )
            registry_descriptor = os.open(
                "base-refresh-observations",
                directory_flags,
                dir_fd=control_descriptor,
            )
            opened.append(registry_descriptor)
            registry_before = self._base_refresh_directory_identity(
                registry_descriptor,
                exact_mode=None if create else 0o700,
            )
            if create:
                os.fchmod(registry_descriptor, 0o700)
            registry_identity = self._base_refresh_directory_identity(
                registry_descriptor, exact_mode=0o700
            )
            if registry_identity[:3] != registry_before[:3]:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: registry directory changed"
                )
            return (
                (state_descriptor, control_descriptor, registry_descriptor),
                (state_identity, control_identity, registry_identity),
            )
        except (OSError, ValueError) as error:
            for descriptor in reversed(opened):
                os.close(descriptor)
            if isinstance(error, ValueError) and str(error).startswith(
                "E_BASE_REFRESH_OBSERVATION"
            ):
                raise
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry directory is unavailable"
            ) from error

    def _revalidate_base_refresh_registry_directories(
        self,
        descriptors: tuple[int, int, int],
        identities: tuple[tuple[int, int, int, int, int], ...],
    ) -> None:
        for index, descriptor in enumerate(descriptors):
            current = self._base_refresh_directory_identity(
                descriptor,
                exact_mode=0o700 if index == 2 else None,
            )
            if current[:4] != identities[index][:4]:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: registry ancestry changed"
                )

    @staticmethod
    def _base_refresh_leaf_identity(
        descriptor: int,
        *,
        expected_size: int | None,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        try:
            current = os.fstat(descriptor)
        except OSError as error:
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry leaf stat failed"
            ) from error
        mode = stat.S_IMODE(current.st_mode)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.getuid()
            or mode != 0o600
            or current.st_nlink != 1
            or current.st_size < 0
            or current.st_size > 65_536
            or (expected_size is not None and current.st_size != expected_size)
        ):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry leaf is unsafe"
            )
        return (
            current.st_dev,
            current.st_ino,
            current.st_uid,
            mode,
            current.st_nlink,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        )

    def _load_base_refresh_observation_registry(
        self,
        state: Mapping[str, Any],
        *,
        task_id: str,
        effect_plan: IntegrationEffectPlanV1,
        integration_receipt: IntegrationReceiptV1,
        refresh_receipt: BaseRefreshReceiptV1,
    ) -> dict[str, Any]:
        """Load the closed O_EXCL record and match the exact first receipt."""

        descriptors, identities = self._open_base_refresh_registry_directory(
            create=False
        )
        registry_descriptor = descriptors[-1]
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        leaf_descriptor = -1
        try:
            leaf_descriptor = os.open(
                f"{task_id}.json",
                flags,
                dir_fd=registry_descriptor,
            )
            before = self._base_refresh_leaf_identity(
                leaf_descriptor, expected_size=None
            )
            if before[5] == 0:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: registry leaf is empty"
                )
            chunks: list[bytes] = []
            remaining = 65_537
            while remaining:
                chunk = os.read(leaf_descriptor, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            encoded = b"".join(chunks)
            if len(encoded) > 65_536:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: registry exceeds byte cap"
                )
            after = self._base_refresh_leaf_identity(
                leaf_descriptor, expected_size=len(encoded)
            )
            if after != before:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: registry changed during read"
                )
            self._revalidate_base_refresh_registry_directories(
                descriptors, identities
            )
            registry = json.loads(encoded.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry is unreadable"
            ) from error
        finally:
            if leaf_descriptor >= 0:
                os.close(leaf_descriptor)
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        required = {
            "schema_version", "kind", "task_id", "task_digest",
            "run_plan_digest", "generation", "repository", "remote",
            "remote_url", "remote_url_digest", "remote_identity",
            "remote_identity_digest",
            "base", "base_ref", "policy_digest", "effect_plan_digest",
            "integration_receipt_digest", "merge_sha", "refresh_receipt",
            "refresh_receipt_digest", "authorizes", "registry_digest",
        }
        if not isinstance(registry, Mapping) or set(registry) != required:
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry schema is not closed"
            )
        try:
            durable_refresh = BaseRefreshReceiptV1.from_dict(
                registry["refresh_receipt"]
            )
        except (TypeError, ValueError):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry receipt is invalid"
            ) from None
        core = {
            key: value
            for key, value in registry.items()
            if key != "registry_digest"
        }
        generation = registry.get("generation")
        state_generation = state.get("generation")
        if (
            registry.get("schema_version") != 1
            or registry.get("kind") != "BaseRefreshReceiptRegistryV1"
            or registry.get("task_id") != task_id
            or registry.get("task_digest") != effect_plan.task_digest
            or registry.get("run_plan_digest") != effect_plan.run_plan_digest
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
            or not isinstance(state_generation, int)
            or isinstance(state_generation, bool)
            or state_generation not in {generation, generation + 1}
            or registry.get("repository") != effect_plan.repository
            or registry.get("remote") != effect_plan.remote
            or registry.get("remote_url") != effect_plan.remote_url
            or registry.get("remote_url_digest")
            != effect_plan.remote_url_digest
            or registry.get("remote_identity") != effect_plan.remote_identity
            or registry.get("remote_identity_digest")
            != effect_plan.remote_identity_digest
            or registry.get("base") != effect_plan.base
            or registry.get("base_ref") != refresh_receipt.base_ref
            or registry.get("policy_digest") != effect_plan.policy_digest
            or registry.get("effect_plan_digest") != effect_plan.plan_digest
            or registry.get("integration_receipt_digest")
            != integration_receipt.receipt_digest
            or registry.get("merge_sha")
            != integration_receipt.observed_merge_sha
            or registry.get("refresh_receipt") != durable_refresh.to_dict()
            or registry.get("refresh_receipt_digest")
            != durable_refresh.receipt_digest
            or registry.get("refresh_receipt") != refresh_receipt.to_dict()
            or registry.get("refresh_receipt_digest")
            != refresh_receipt.receipt_digest
            or registry.get("authorizes") is not False
            or registry.get("registry_digest") != contract_digest(core)
        ):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: first registry receipt is immutable"
            )
        return copy.deepcopy(dict(registry))

    def _register_base_refresh_observation(
        self,
        state: Mapping[str, Any],
        *,
        task_id: str,
        effect_plan: IntegrationEffectPlanV1,
        integration_receipt: IntegrationReceiptV1,
        refresh_receipt: BaseRefreshReceiptV1,
    ) -> dict[str, Any]:
        """Create the first exact refresh record once; exact replay is safe."""

        generation = state.get("generation")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: task generation is invalid"
            )
        core = {
            "schema_version": 1,
            "kind": "BaseRefreshReceiptRegistryV1",
            "task_id": task_id,
            "task_digest": effect_plan.task_digest,
            "run_plan_digest": effect_plan.run_plan_digest,
            "generation": generation,
            "repository": effect_plan.repository,
            "remote": effect_plan.remote,
            "remote_url": effect_plan.remote_url,
            "remote_url_digest": effect_plan.remote_url_digest,
            "remote_identity": effect_plan.remote_identity,
            "remote_identity_digest": effect_plan.remote_identity_digest,
            "base": effect_plan.base,
            "base_ref": refresh_receipt.base_ref,
            "policy_digest": effect_plan.policy_digest,
            "effect_plan_digest": effect_plan.plan_digest,
            "integration_receipt_digest": integration_receipt.receipt_digest,
            "merge_sha": integration_receipt.observed_merge_sha,
            "refresh_receipt": refresh_receipt.to_dict(),
            "refresh_receipt_digest": refresh_receipt.receipt_digest,
            "authorizes": False,
        }
        registry = {**core, "registry_digest": contract_digest(core)}
        encoded = (json.dumps(registry, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > 65_536:
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry exceeds byte cap"
            )
        descriptors, identities = self._open_base_refresh_registry_directory(
            create=True
        )
        registry_descriptor = descriptors[-1]
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        leaf_descriptor = -1
        try:
            leaf_descriptor = os.open(
                f"{task_id}.json",
                flags,
                0o600,
                dir_fd=registry_descriptor,
            )
        except FileExistsError:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            return self._load_base_refresh_observation_registry(
                state,
                task_id=task_id,
                effect_plan=effect_plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh_receipt,
            )
        except OSError as error:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: registry leaf is unavailable"
            ) from error
        try:
            os.fchmod(leaf_descriptor, 0o600)
            before = self._base_refresh_leaf_identity(
                leaf_descriptor, expected_size=0
            )
            written = 0
            while written < len(encoded):
                count = os.write(leaf_descriptor, encoded[written:])
                if count <= 0:
                    raise OSError("registry write made no progress")
                written += count
            os.fsync(leaf_descriptor)
            after = self._base_refresh_leaf_identity(
                leaf_descriptor, expected_size=len(encoded)
            )
            if after[:5] != before[:5]:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: registry leaf changed during write"
                )
            self._revalidate_base_refresh_registry_directories(
                descriptors, identities
            )
            os.fsync(registry_descriptor)
            final = self._base_refresh_leaf_identity(
                leaf_descriptor, expected_size=len(encoded)
            )
            if final[:6] != after[:6]:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: registry leaf changed after fsync"
                )
            self._revalidate_base_refresh_registry_directories(
                descriptors, identities
            )
        finally:
            if leaf_descriptor >= 0:
                os.close(leaf_descriptor)
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        return registry

    @staticmethod
    def _new_base_refresh_observation(
        state: Mapping[str, Any],
        *,
        task_id: str,
        effect_plan: IntegrationEffectPlanV1,
        integration_receipt: IntegrationReceiptV1,
        refresh_receipt: BaseRefreshReceiptV1,
        registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind the first refresh observation without granting authority."""

        generation = state.get("generation")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: task generation is invalid"
            )
        marker_core = {
            "schema_version": 1,
            "kind": "BaseRefreshObservationMarkerV1",
            "task_id": task_id,
            "task_digest": effect_plan.task_digest,
            "run_plan_digest": effect_plan.run_plan_digest,
            "generation": generation,
            "repository": effect_plan.repository,
            "remote": effect_plan.remote,
            "remote_url": effect_plan.remote_url,
            "remote_url_digest": effect_plan.remote_url_digest,
            "remote_identity": effect_plan.remote_identity,
            "remote_identity_digest": effect_plan.remote_identity_digest,
            "base": effect_plan.base,
            "base_ref": refresh_receipt.base_ref,
            "policy_digest": effect_plan.policy_digest,
            "effect_plan_digest": effect_plan.plan_digest,
            "integration_receipt_digest": integration_receipt.receipt_digest,
            "merge_sha": integration_receipt.observed_merge_sha,
            "refresh_receipt": refresh_receipt.to_dict(),
            "refresh_receipt_digest": refresh_receipt.receipt_digest,
            "registry_digest": registry["registry_digest"],
            "authorizes": False,
        }
        marker = {
            **marker_core,
            "marker_digest": contract_digest(marker_core),
        }
        if len(json.dumps(marker, sort_keys=True).encode("utf-8")) > 65_536:
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: marker exceeds byte cap"
            )
        return marker

    @staticmethod
    def _validated_base_refresh_observation(
        state: Mapping[str, Any],
        *,
        task_id: str,
        effect_plan: IntegrationEffectPlanV1,
        integration_receipt: IntegrationReceiptV1,
        refresh_receipt: BaseRefreshReceiptV1,
        registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Load and compare the immutable first refresh during recovery."""

        marker = state.get("base_refresh_observation")
        required = {
            "schema_version", "kind", "task_id", "task_digest",
            "run_plan_digest", "generation", "repository", "remote",
            "remote_url", "remote_url_digest", "remote_identity",
            "remote_identity_digest",
            "base", "base_ref", "policy_digest", "effect_plan_digest",
            "integration_receipt_digest", "merge_sha", "refresh_receipt",
            "refresh_receipt_digest", "registry_digest", "authorizes",
            "marker_digest",
        }
        if not isinstance(marker, Mapping) or set(marker) != required:
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: durable marker is missing or open"
            )
        try:
            durable_refresh = BaseRefreshReceiptV1.from_dict(
                marker["refresh_receipt"]
            )
        except (TypeError, ValueError):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: durable receipt is invalid"
            ) from None
        marker_core = {
            key: value for key, value in marker.items() if key != "marker_digest"
        }
        generation = marker.get("generation")
        if (
            marker.get("schema_version") != 1
            or marker.get("kind") != "BaseRefreshObservationMarkerV1"
            or marker.get("task_id") != task_id
            or marker.get("task_digest") != effect_plan.task_digest
            or marker.get("run_plan_digest") != effect_plan.run_plan_digest
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or state.get("generation") != generation + 1
            or marker.get("repository") != effect_plan.repository
            or marker.get("remote") != effect_plan.remote
            or marker.get("remote_url") != effect_plan.remote_url
            or marker.get("remote_url_digest")
            != effect_plan.remote_url_digest
            or marker.get("remote_identity") != effect_plan.remote_identity
            or marker.get("remote_identity_digest")
            != effect_plan.remote_identity_digest
            or marker.get("base") != effect_plan.base
            or marker.get("base_ref") != refresh_receipt.base_ref
            or marker.get("policy_digest") != effect_plan.policy_digest
            or marker.get("effect_plan_digest") != effect_plan.plan_digest
            or marker.get("integration_receipt_digest")
            != integration_receipt.receipt_digest
            or marker.get("merge_sha")
            != integration_receipt.observed_merge_sha
            or marker.get("refresh_receipt") != durable_refresh.to_dict()
            or marker.get("refresh_receipt_digest")
            != durable_refresh.receipt_digest
            or marker.get("refresh_receipt") != refresh_receipt.to_dict()
            or marker.get("refresh_receipt_digest")
            != refresh_receipt.receipt_digest
            or marker.get("registry_digest") != registry.get("registry_digest")
            or marker.get("remote_url") != registry.get("remote_url")
            or marker.get("remote_url_digest")
            != registry.get("remote_url_digest")
            or marker.get("remote_identity")
            != registry.get("remote_identity")
            or marker.get("remote_identity_digest")
            != registry.get("remote_identity_digest")
            or marker.get("refresh_receipt")
            != registry.get("refresh_receipt")
            or marker.get("refresh_receipt_digest")
            != registry.get("refresh_receipt_digest")
            or marker.get("authorizes") is not False
            or marker.get("marker_digest") != contract_digest(marker_core)
        ):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: first receipt is immutable"
            )
        return copy.deepcopy(dict(marker))

    @staticmethod
    def _reconcile_base_refresh_evidence(
        state: Mapping[str, Any],
        *,
        registry: Mapping[str, Any],
    ) -> None:
        """Require state evidence to repeat the immutable registry exactly."""

        evidence = state.get("evidence")
        if not isinstance(evidence, Mapping):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: task evidence is unavailable"
            )
        receipt_digests = state.get("base_verification_receipt_digests")
        if not isinstance(receipt_digests, list):
            raise ValueError(
                "E_BASE_REFRESH_OBSERVATION: receipt ledger is unavailable"
            )
        if state.get("state") == "blocked":
            recovery = evidence.get("base_recovery")
            required = {
                "status", "reason_code", "base_ref", "observed_base_sha",
                "merge_sha", "refresh_receipt", "refresh_receipt_digest",
                "refresh_registry_digest", "receipt_digest", "next_action",
                "authorizes",
            }
            durable_refresh = registry.get("refresh_receipt", {})
            expected_reason = (
                "BASE_REFRESH_UNKNOWN"
                if isinstance(durable_refresh, Mapping)
                and durable_refresh.get("status") != "PASS"
                else None
            )
            if (
                not isinstance(recovery, Mapping)
                or set(recovery) != required
                or recovery.get("status") != "BLOCKED"
                or (
                    expected_reason is not None
                    and recovery.get("reason_code") != expected_reason
                )
                or recovery.get("base_ref") != registry.get("base_ref")
                or recovery.get("merge_sha") != registry.get("merge_sha")
                or recovery.get("refresh_receipt")
                != registry.get("refresh_receipt")
                or recovery.get("refresh_receipt_digest")
                != registry.get("refresh_receipt_digest")
                or recovery.get("refresh_registry_digest")
                != registry.get("registry_digest")
                or recovery.get("receipt_digest") not in receipt_digests
                or recovery.get("next_action")
                != "refresh_exact_base_and_observe"
                or recovery.get("authorizes") is not False
            ):
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: recovery evidence contradicts registry"
                )
            return
        if state.get("state") == "base_verified":
            verified = evidence.get("base_verified")
            required = {
                "remote_base", "base_ref", "observed_base_sha",
                "refresh_receipt_digest", "refresh_registry_digest",
                "receipt_digest", "authorizes",
            }
            if (
                not isinstance(verified, Mapping)
                or set(verified) != required
                or verified.get("remote_base") != registry.get("merge_sha")
                or verified.get("base_ref") != registry.get("base_ref")
                or verified.get("observed_base_sha")
                != registry.get("refresh_receipt", {}).get("observed_sha")
                or verified.get("refresh_receipt_digest")
                != registry.get("refresh_receipt_digest")
                or verified.get("refresh_registry_digest")
                != registry.get("registry_digest")
                or verified.get("receipt_digest") not in receipt_digests
                or verified.get("authorizes") is not False
            ):
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: verified evidence contradicts registry"
                )
            return
        raise ValueError(
            "E_BASE_REFRESH_OBSERVATION: registry has no reconciliable state"
        )

    def publish_base_verification(
        self,
        task_id: str,
        *,
        effect_plan: IntegrationEffectPlanV1,
        integration_receipt: IntegrationReceiptV1,
        refresh_receipt: BaseRefreshReceiptV1,
        receipt: BaseVerificationReceiptV1,
        current_branch: str,
    ) -> dict[str, Any]:
        """Advance merged -> base_verified only from exact local containment."""

        if (
            type(effect_plan) is not IntegrationEffectPlanV1
            or type(integration_receipt) is not IntegrationReceiptV1
            or type(refresh_receipt) is not BaseRefreshReceiptV1
            or type(receipt) is not BaseVerificationReceiptV1
        ):
            raise ValueError("E_BASE_VERIFICATION: exact contracts are required")
        effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
        integration_receipt = IntegrationReceiptV1.from_dict(
            integration_receipt.to_dict()
        )
        refresh_receipt = BaseRefreshReceiptV1.from_dict(
            refresh_receipt.to_dict()
        )
        receipt = BaseVerificationReceiptV1.from_dict(receipt.to_dict())
        try:
            canonical_run_plan, canonical_policy = (
                _canonical_remote_write_inputs(
                    self.state_dir,
                    task_id=task_id,
                    effect_plan=effect_plan,
                )
            )
        except ValueError as error:
            raise ValueError(
                "E_BASE_VERIFICATION: canonical plan or policy drifted"
            ) from error
        from control_plane.run_workflow import validate_outcome_binding

        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            recovering = (
                state.get("state") == "blocked"
                and state.get("resume_state") == "merged"
            )
            verified_replay = state.get("state") == "base_verified"
            binding = state.get("outcome_binding", {})
            published = list(state.get("base_verification_receipt_digests", []))
            if (
                (
                    state.get("state") != "merged"
                    and not recovering
                    and not verified_replay
                )
                or state.get("outcome") != "integration"
                or state.get("branch") != current_branch
                or state.get("task_digest") != effect_plan.task_digest
                or state.get("run_plan_digest") != effect_plan.run_plan_digest
                or state.get("integration_effect_plan") != effect_plan.to_dict()
                or state.get("integration_receipt")
                != integration_receipt.to_dict()
                or canonical_run_plan.get("task_id") != task_id
                or canonical_run_plan.get("task_digest")
                != effect_plan.task_digest
                or canonical_run_plan.get("plan_digest")
                != effect_plan.run_plan_digest
                or canonical_run_plan.get("requested_outcome") != "integration"
                or canonical_run_plan.get("repository")
                != effect_plan.repository
                or canonical_run_plan.get("branch") != effect_plan.branch
                or contract_digest(canonical_policy)
                != effect_plan.policy_digest
                or canonical_policy.get("git", {}).get(
                    "integration_strategy"
                )
                != "squash"
                or integration_receipt.status != "PASS"
                or integration_receipt.effect_plan_digest != effect_plan.plan_digest
                or not isinstance(binding, Mapping)
                or validate_outcome_binding(binding)
                or binding.get("task_id") != task_id
                or binding.get("run_plan_digest")
                != effect_plan.run_plan_digest
                or binding.get("requested_outcome") != "integration"
                or binding.get("repository") != effect_plan.repository
                or binding.get("branch") != effect_plan.branch
                or binding.get("committed_head") != effect_plan.head_sha
                or binding.get("pushed_head") != effect_plan.head_sha
                or binding.get("pull_request_digest")
                != effect_plan.pull_request_digest
                or binding.get("checks_digest") != effect_plan.checks_digest
                or binding.get("merge_sha") != integration_receipt.observed_merge_sha
                or binding.get("consumed_effect_ids")
                != [
                    "local_write",
                    "commit",
                    "remote_write",
                    "pull_request",
                    "integration",
                ]
                or receipt.task_id != task_id
                or receipt.task_digest != effect_plan.task_digest
                or receipt.run_plan_digest != effect_plan.run_plan_digest
                or receipt.repository != effect_plan.repository
                or receipt.remote != effect_plan.remote
                or receipt.base != effect_plan.base
                or receipt.policy_digest != effect_plan.policy_digest
                or receipt.effect_plan_digest != effect_plan.plan_digest
                or receipt.integration_receipt_digest
                != integration_receipt.receipt_digest
                or receipt.merge_sha != integration_receipt.observed_merge_sha
                or refresh_receipt.task_id != task_id
                or refresh_receipt.task_digest != effect_plan.task_digest
                or refresh_receipt.run_plan_digest != effect_plan.run_plan_digest
                or refresh_receipt.repository != effect_plan.repository
                or refresh_receipt.remote != effect_plan.remote
                or refresh_receipt.remote_url != effect_plan.remote_url
                or refresh_receipt.remote_url_digest
                != effect_plan.remote_url_digest
                or refresh_receipt.remote_identity
                != effect_plan.remote_identity
                or refresh_receipt.remote_identity_digest
                != effect_plan.remote_identity_digest
                or refresh_receipt.base != effect_plan.base
                or refresh_receipt.base_ref != receipt.base_ref
                or refresh_receipt.policy_digest != effect_plan.policy_digest
                or refresh_receipt.effect_plan_digest != effect_plan.plan_digest
                or refresh_receipt.integration_receipt_digest
                != integration_receipt.receipt_digest
                or refresh_receipt.merge_sha
                != integration_receipt.observed_merge_sha
                or receipt.refresh_receipt_digest
                != refresh_receipt.receipt_digest
                or receipt.observed_at != refresh_receipt.observed_at
                or (
                    receipt.status == "PASS"
                    and refresh_receipt.status != "PASS"
                )
                or (
                    receipt.status == "BLOCKED"
                    and receipt.reason_code == "BASE_REFRESH_UNKNOWN"
                    and refresh_receipt.status == "PASS"
                )
                or (
                    receipt.status == "BLOCKED"
                    and receipt.reason_code != "BASE_REFRESH_UNKNOWN"
                    and refresh_receipt.status != "PASS"
                )
            ):
                raise ValueError("E_BASE_VERIFICATION: durable binding drifted")
            if state.get("state") == "merged":
                if state.get("base_refresh_observation") is not None:
                    raise ValueError(
                        "E_BASE_REFRESH_OBSERVATION: unexpected durable marker"
                    )
                registry = self._register_base_refresh_observation(
                    state,
                    task_id=task_id,
                    effect_plan=effect_plan,
                    integration_receipt=integration_receipt,
                    refresh_receipt=refresh_receipt,
                )
                refresh_marker = self._new_base_refresh_observation(
                    state,
                    task_id=task_id,
                    effect_plan=effect_plan,
                    integration_receipt=integration_receipt,
                    refresh_receipt=refresh_receipt,
                    registry=registry,
                )
            else:
                registry = self._load_base_refresh_observation_registry(
                    state,
                    task_id=task_id,
                    effect_plan=effect_plan,
                    integration_receipt=integration_receipt,
                    refresh_receipt=refresh_receipt,
                )
                refresh_marker = self._validated_base_refresh_observation(
                    state,
                    task_id=task_id,
                    effect_plan=effect_plan,
                    integration_receipt=integration_receipt,
                    refresh_receipt=refresh_receipt,
                    registry=registry,
                )
                self._reconcile_base_refresh_evidence(
                    state,
                    registry=registry,
                )
            if receipt.receipt_digest in published:
                if recovering or verified_replay:
                    return state
                raise ValueError(
                    "E_BASE_VERIFICATION_REPLAY: receipt was already published"
                )
            if verified_replay:
                raise ValueError(
                    "E_BASE_REFRESH_OBSERVATION: verified replay receipt drifted"
                )
            if receipt.status == "PASS":
                from control_plane.git_state import (
                    revalidate_base_verification_receipt,
                )

                if not revalidate_base_verification_receipt(
                    receipt,
                    refresh_receipt=refresh_receipt,
                ):
                    raise ValueError(
                        "E_BASE_VERIFICATION: lifecycle-edge containment failed"
                    )
            state["base_refresh_observation"] = refresh_marker
            published.append(receipt.receipt_digest)
            state["base_verification_receipt_digests"] = published
            if receipt.status != "PASS":
                state.update(
                    {
                        "state": "blocked",
                        "resume_state": "merged",
                        "resume_forbidden": True,
                        "block_reason": f"E_BASE_VERIFICATION_{receipt.reason_code}",
                        "generation": int(state.get("generation", 0)) + 1,
                        "updated_at": _utc_now(),
                    }
                )
                state.setdefault("evidence", {})["base_recovery"] = {
                    "status": "BLOCKED",
                    "reason_code": receipt.reason_code,
                    "base_ref": receipt.base_ref,
                    "observed_base_sha": receipt.observed_base_sha,
                    "merge_sha": receipt.merge_sha,
                    "refresh_receipt": refresh_receipt.to_dict(),
                    "refresh_receipt_digest": refresh_receipt.receipt_digest,
                    "refresh_registry_digest": registry["registry_digest"],
                    "receipt_digest": receipt.receipt_digest,
                    "next_action": "refresh_exact_base_and_observe",
                    "authorizes": False,
                }
                _atomic_json(self._path(task_id), state)
                return state
            if receipt.contained is not True:
                raise ValueError("E_BASE_VERIFICATION: containment PASS is required")
            state.update(
                {
                    "state": "base_verified",
                    "resume_state": None,
                    "resume_forbidden": False,
                    "block_reason": None,
                    "generation": int(state.get("generation", 0)) + 1,
                    "updated_at": _utc_now(),
                }
            )
            state.setdefault("evidence", {})["base_verified"] = {
                "remote_base": receipt.merge_sha,
                "base_ref": receipt.base_ref,
                "observed_base_sha": receipt.observed_base_sha,
                "refresh_receipt_digest": refresh_receipt.receipt_digest,
                "refresh_registry_digest": registry["registry_digest"],
                "receipt_digest": receipt.receipt_digest,
                "authorizes": False,
            }
            state.setdefault("evidence", {}).pop("base_recovery", None)
            _atomic_json(self._path(task_id), state)
            return state

    @staticmethod
    def _validated_pull_request_revision_required(
        state: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        """Validate the durable, non-authorizing Task5A revision marker."""

        marker = state.get("revision_required")
        required = {
            "schema_version", "kind", "task_id", "generation", "pull_request",
            "head_sha", "effect_plan_digest", "policy_digest",
            "outcome_binding_digest", "receipts", "receipt_digests", "reason", "authorizes",
            "marker_digest",
        }
        pull_request = (
            state.get("evidence", {}).get("pr_draft", {}).get("pull_request")
        )
        if not isinstance(pull_request, Mapping):
            pull_request = {}
        try:
            effect_plan = OutcomeEffectPlanV1.from_dict(
                state.get("pull_request_effect_plan", {})
            )
            receipts = tuple(
                RemoteOutcomeReceiptV1.from_dict(item)
                for item in marker.get("receipts", [])
            ) if isinstance(marker, Mapping) and isinstance(marker.get("receipts"), list) else ()
        except ValueError as error:
            raise ValueError(
                "E_PR_READINESS_REVISION_REQUIRED: marker is invalid"
            ) from error
        canonical_receipts = (
            receipts[0].observation_kind if len(receipts) == 3 else None,
            receipts[1].observation_kind if len(receipts) == 3 else None,
            receipts[2].observation_kind if len(receipts) == 3 else None,
        )
        receipt_digests = [receipt.receipt_digest for receipt in receipts]
        check_statuses = (
            tuple(status for _, status in receipts[0].check_results)
            if len(receipts) == 3 else ()
        )
        has_unknown = (
            any(receipt.status == "UNKNOWN" for receipt in receipts)
            or "UNKNOWN" in check_statuses
        )
        has_failed_check = "FAIL" in check_statuses
        unresolved_important = any(
            row[3] == "unresolved" and row[2] in {"Critical", "Important"}
            for receipt in receipts[1:] for row in receipt.feedback
        ) if len(receipts) == 3 else False
        expected_reason = (
            None if has_unknown else "checks_failed" if has_failed_check
            else "review_feedback" if unresolved_important else None
        )
        expected_evidence = {
            "status": "FAIL",
            "receipt_digests": receipt_digests,
            "authorizes": False,
        }
        if (
            not isinstance(marker, Mapping)
            or set(marker) != required
            or marker.get("schema_version") != 1
            or marker.get("kind") != "PullRequestRevisionRequiredV1"
            or marker.get("task_id") != task_id
            or marker.get("generation") != state.get("generation")
            or marker.get("pull_request") != pull_request
            or marker.get("head_sha") != state.get("evidence", {}).get("pushed", {}).get("remote_head")
            or marker.get("effect_plan_digest")
            != effect_plan.plan_digest
            or marker.get("policy_digest")
            != effect_plan.policy_digest
            or marker.get("outcome_binding_digest")
            != state.get("outcome_binding", {}).get("binding_digest")
            or not all(
                SHA256_DIGEST.fullmatch(str(marker.get(field))) is not None
                for field in (
                    "effect_plan_digest", "policy_digest", "outcome_binding_digest",
                    "marker_digest",
                )
            )
            or not isinstance(marker.get("receipt_digests"), list)
            or marker.get("receipt_digests") != receipt_digests
            or canonical_receipts != ("checks", "review_threads", "comments")
            or any(
                receipt.effect_plan_digest != effect_plan.plan_digest
                or receipt.policy_digest != effect_plan.policy_digest
                or receipt.subject_digest != state.get("outcome_binding", {}).get("binding_digest")
                or (receipt.observed_pr_number, receipt.observed_pr_url, receipt.observed_head_sha)
                != (pull_request.get("number"), pull_request.get("url"), pull_request.get("head_commit"))
                for receipt in receipts
            )
            or state.get("pr_readiness_receipt_digests") != receipt_digests
            or state.get("evidence", {}).get("pr_readiness") != expected_evidence
            or marker.get("reason") != expected_reason
            or marker.get("authorizes") is not False
            or marker.get("marker_digest") != contract_digest(
                {key: value for key, value in marker.items() if key != "marker_digest"}
            )
            or len(json.dumps(marker, sort_keys=True).encode("utf-8")) > 65_536
        ):
            raise ValueError("E_PR_READINESS_REVISION_REQUIRED: marker is invalid")
        return dict(marker)

    @staticmethod
    def _validated_pull_request_marker(
        state: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        from control_plane.policy import validate_policy
        from control_plane.run_workflow import (
            validate_outcome_binding,
            validate_run_plan,
        )

        marker = state.get("pending_pull_request_effect")
        required = {
            "schema_version", "task_id", "phase", "effect_plan",
            "outcome_binding", "run_plan", "policy", "base_head",
            "feature_head", "remote_url_digest", "remote_identity_digest",
            "absence_receipt", "absence_receipt_digest", "observed_at", "status",
            "retry_policy", "authorizes", "marker_digest",
        }
        optional = {"receipt_digest", "latest_observed_at"}
        if (
            not isinstance(marker, Mapping)
            or not required.issubset(marker)
            or not set(marker).issubset(required | optional)
            or marker.get("schema_version") != 1
            or marker.get("task_id") != task_id
            or marker.get("authorizes") is not False
            or not isinstance(marker.get("effect_plan"), Mapping)
            or not isinstance(marker.get("outcome_binding"), Mapping)
            or not isinstance(marker.get("run_plan"), Mapping)
            or not isinstance(marker.get("policy"), Mapping)
            or not isinstance(marker.get("absence_receipt"), Mapping)
            or validate_outcome_binding(marker["outcome_binding"])
            or validate_run_plan(marker["run_plan"])
            or validate_policy(marker["policy"])
            or GIT_OBJECT_ID.fullmatch(str(marker.get("base_head"))) is None
            or GIT_OBJECT_ID.fullmatch(str(marker.get("feature_head"))) is None
            or any(
                SHA256_DIGEST.fullmatch(str(marker.get(field))) is None
                for field in (
                    "remote_url_digest", "remote_identity_digest",
                    "absence_receipt_digest", "marker_digest",
                )
            )
            or (
                marker.get("receipt_digest") is not None
                and SHA256_DIGEST.fullmatch(str(marker.get("receipt_digest")))
                is None
            )
            or not isinstance(marker.get("observed_at"), str)
            or not marker.get("observed_at")
            or (
                marker.get("latest_observed_at") is not None
                and (
                    not isinstance(marker.get("latest_observed_at"), str)
                    or not marker.get("latest_observed_at")
                )
            )
            or (
                marker.get("phase") == "prepared"
                and (
                    marker.get("status") != "ABSENT"
                    or marker.get("retry_policy") != "observe_before_write"
                )
            )
            or (
                marker.get("phase") == "observe_only"
                and (
                    marker.get("status") not in {"UNKNOWN", "FAIL", "ABSENT"}
                    or marker.get("retry_policy") != "observe_only"
                )
            )
            or marker.get("phase") not in {"prepared", "observe_only"}
            or marker.get("marker_digest")
            != contract_digest(
                {
                    key: value
                    for key, value in marker.items()
                    if key != "marker_digest"
                }
            )
            or len(json.dumps(marker, sort_keys=True).encode("utf-8")) > 131_072
        ):
            raise ValueError("E_PULL_REQUEST_PREPARE: marker is invalid")
        try:
            plan = OutcomeEffectPlanV1.from_dict(marker["effect_plan"])
            absence_receipt = RemoteOutcomeReceiptV1.from_dict(
                marker["absence_receipt"]
            )
        except ValueError as error:
            raise ValueError(
                "E_PULL_REQUEST_PREPARE: marker receipt is invalid"
            ) from error
        receipt_plan_fields = (
            "task_id", "task_digest", "run_plan_digest", "requested_outcome",
            "repository", "remote", "remote_url", "remote_url_digest",
            "remote_identity_digest", "base", "branch", "head_sha",
            "scope_paths", "subject_digest", "policy_digest", "effect",
            "title_digest", "body_digest", "draft",
        )
        registered = state.get("pull_request_outcome_receipt_digests")
        if (
            plan.effect != "pull_request"
            or plan.subject_digest
            != marker["outcome_binding"].get("binding_digest")
            or plan.run_plan_digest != marker["run_plan"].get("plan_digest")
            or plan.policy_digest != contract_digest(marker["policy"])
            or plan.head_sha != marker.get("feature_head")
            or absence_receipt.status != "ABSENT"
            or absence_receipt.effect != "pull_request"
            or absence_receipt.authorizes is not False
            or absence_receipt.effect_plan_digest != plan.plan_digest
            or absence_receipt.receipt_digest
            != marker.get("absence_receipt_digest")
            or absence_receipt.observed_at != marker.get("observed_at")
            or any(
                getattr(absence_receipt, field) != getattr(plan, field)
                for field in receipt_plan_fields
            )
            or not isinstance(registered, list)
            or registered.count(absence_receipt.receipt_digest) != 1
        ):
            raise ValueError("E_PULL_REQUEST_PREPARE: marker binding drifted")
        return copy.deepcopy(dict(marker))

    @staticmethod
    def _validated_remote_write_marker(
        state: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        from control_plane.policy import validate_policy
        from control_plane.run_workflow import (
            validate_outcome_binding,
            validate_run_plan,
        )

        marker = state.get("pending_remote_effect")
        required = {
            "schema_version",
            "task_id",
            "phase",
            "effect_plan",
            "outcome_binding",
            "run_plan",
            "policy",
            "base_head",
            "remote_url_digest",
            "remote_identity_digest",
            "status",
            "retry_policy",
            "authorizes",
            "marker_digest",
        }
        optional = {"receipt_digest", "observed_at"}
        if (
            not isinstance(marker, Mapping)
            or set(marker) - optional != required
            or marker.get("schema_version") != 1
            or marker.get("task_id") != task_id
            or marker.get("phase")
            not in {"prepared", "observe_only", "repair_required"}
            or marker.get("authorizes") is not False
            or not isinstance(marker.get("effect_plan"), Mapping)
            or not isinstance(marker.get("outcome_binding"), Mapping)
            or not isinstance(marker.get("run_plan"), Mapping)
            or not isinstance(marker.get("policy"), Mapping)
            or validate_outcome_binding(marker["outcome_binding"])
            or validate_run_plan(marker["run_plan"])
            or validate_policy(marker["policy"])
            or GIT_OBJECT_ID.fullmatch(str(marker.get("base_head"))) is None
            or SHA256_DIGEST.fullmatch(str(marker.get("remote_url_digest")))
            is None
            or SHA256_DIGEST.fullmatch(
                str(marker.get("remote_identity_digest"))
            )
            is None
            or marker.get("marker_digest")
            != contract_digest(
                {
                    key: value
                    for key, value in marker.items()
                    if key != "marker_digest"
                }
            )
        ):
            raise ValueError("E_REMOTE_WRITE_PREPARE: marker is invalid")
        effect_plan = OutcomeEffectPlanV1.from_dict(marker["effect_plan"])
        if (
            effect_plan.subject_digest
            != marker["outcome_binding"].get("binding_digest")
            or effect_plan.run_plan_digest != marker["run_plan"].get("plan_digest")
            or effect_plan.policy_digest != contract_digest(marker["policy"])
        ):
            raise ValueError("E_REMOTE_WRITE_PREPARE: marker binding drifted")
        return copy.deepcopy(dict(marker))

    @staticmethod
    def _validated_delivery_marker(
        state: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        marker = state.get("finalizing_delivery_commit")
        required = {
            "schema_version", "task_id", "generation", "lease_digest",
            "snapshot_digest", "allowlist", "expected_index_tree", "parent_head",
            "base_head", "expected_tree", "message_digest", "phase", "marker_digest",
        }
        optional = {"observed_sha", "release_digest"}
        if (
            not isinstance(marker, Mapping)
            or not set(marker).issubset(required | optional)
            or not required.issubset(marker)
            or marker.get("schema_version") != 1
            or marker.get("task_id") != task_id
            or not isinstance(marker.get("generation"), int)
            or marker.get("phase") not in {
                "prepared", "index_observed", "git_committed", "state_committed", "lease_released"
            }
            or not isinstance(marker.get("allowlist"), list)
            or not marker["allowlist"]
            or not all(_normalize_lease_path(path) == path for path in marker["allowlist"])
            or any(
                not isinstance(marker.get(key), str)
                or SHA256_DIGEST.fullmatch(marker[key]) is None
                for key in ("lease_digest", "snapshot_digest", "message_digest")
            )
            or any(GIT_OBJECT_ID.fullmatch(str(marker.get(key))) is None for key in ("expected_index_tree", "parent_head", "expected_tree"))
            or GIT_OBJECT_ID.fullmatch(str(marker.get("base_head"))) is None
            or marker.get("marker_digest") != contract_digest(
                {key: value for key, value in marker.items() if key != "marker_digest"}
            )
            or not isinstance(state.get("delivery_review_binding"), Mapping)
            or marker.get("snapshot_digest")
            != state["delivery_review_binding"].get("binding_digest")
        ):
            raise ValueError("E_DELIVERY_RECOVERY_UNKNOWN: delivery marker is invalid")
        return dict(marker)

    def bind_active_run_revision(
        self,
        task_id: str,
        *,
        run_plan_digest: str,
        revision_digest: str,
        current_branch: str,
        expected_active_revision_digest: str | None = None,
    ) -> dict[str, Any]:
        """CAS-bind the immutable active run revision to its task state."""

        if (
            SHA256_DIGEST.fullmatch(run_plan_digest) is None
            or SHA256_DIGEST.fullmatch(revision_digest) is None
            or (
                expected_active_revision_digest is not None
                and SHA256_DIGEST.fullmatch(expected_active_revision_digest) is None
            )
        ):
            raise ValueError("E_STATE_CAS: run revision digests are invalid")
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            if (
                state.get("branch") != current_branch
                or state.get("task_digest") is None
            ):
                raise ValueError("E_STATE_CAS: task binding changed")
            existing = state.get("active_run_revision_digest")
            if existing is not None:
                if existing == revision_digest:
                    return state
                raise ValueError(
                    "E_STATE_CAS: review revisions require the specialized "
                    "local-review boundary"
                )
            state["active_run_revision_digest"] = revision_digest
            state["run_plan_digest"] = run_plan_digest
            state["generation"] = int(state.get("generation", 0)) + 1
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            return state

    def finalize_review_ready(
        self,
        task_id: str,
        *,
        expected_generation: int,
        run_plan_digest: str,
        run_revision_digest: str,
        attempt_digest: str,
        promotion_digest: str,
        receipt_digests: tuple[str, ...],
        artifact: Mapping[str, Any],
        current_branch: str,
    ) -> dict[str, Any]:
        """Durably delete an exact review artifact before publishing readiness."""

        from control_plane.run_workflow import (
            ReviewArtifactStore,
            RunStore,
            promote_review_ready,
            validate_stable_review_diff_artifact,
        )

        if (
            validate_stable_review_diff_artifact(artifact)
            or not all(
                isinstance(value, str) and SHA256_DIGEST.fullmatch(value)
                for value in (run_plan_digest, run_revision_digest, attempt_digest, promotion_digest, *receipt_digests)
            )
            or not receipt_digests
        ):
            raise ValueError("E_INDEPENDENT_REVIEW: promotion proof is invalid")
        try:
            persisted_plan = RunStore(self.state_dir).load_plan(task_id)
            persisted_proof = promote_review_ready(
                state_dir=self.state_dir,
                run_plan=persisted_plan,
                receipt_digests=receipt_digests,
            )
        except ValueError as error:
            raise ValueError(
                "E_INDEPENDENT_REVIEW: durable promotion proof is invalid"
            ) from error
        if (
            persisted_plan.get("plan_digest") != run_plan_digest
            or persisted_proof.get("promotion_digest") != promotion_digest
        ):
            raise ValueError(
                "E_INDEPENDENT_REVIEW: durable promotion proof drifted"
            )
        common_dir, state_path = _common_git_dir(self.state_dir), self._path(task_id)
        with _common_lease_lock(common_dir):
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                if (
                    state.get("state") != "verifying"
                    or state.get("generation") != expected_generation
                    or state.get("branch") != current_branch
                    or state.get("run_plan_digest") != run_plan_digest
                    or state.get("active_run_revision_digest") != run_revision_digest
                    or self._read_owner_lease(task_id) is not None
                ):
                    raise ValueError("E_INDEPENDENT_REVIEW: task is not awaiting review")
                try:
                    self._observe_review_handoff_subject(
                        task_id=task_id,
                        worktree=str(artifact["repository"]),
                        branch=current_branch,
                        active_revision_digest=run_revision_digest,
                        attempt_digest=attempt_digest,
                        artifact_digest=str(artifact["artifact_digest"]),
                    )
                except ValueError:
                    self._terminally_block_review_handoff_locked(
                        task_id=task_id,
                        current_branch=current_branch,
                        state_path=state_path,
                    )
                    raise
                final_core = {
                    "prior_generation": expected_generation,
                    "task_id": task_id,
                    "run_plan_digest": run_plan_digest,
                    "run_revision_digest": run_revision_digest,
                    "attempt_digest": attempt_digest,
                    "promotion_digest": promotion_digest,
                    "receipt_digests": sorted(receipt_digests),
                    "artifact": dict(artifact),
                    "artifact_delete_started": True,
                    "branch": current_branch,
                }
                final = {
                    **final_core,
                    "finalization_digest": contract_digest(final_core),
                }
                marker = dict(state)
                marker.update({"state": "finalizing_review_ready", "resume_state": None,
                               "resume_forbidden": True, "review_ready_finalization": final,
                               "updated_at": _utc_now()})
                _atomic_json(state_path, marker)
            ReviewArtifactStore(Path(str(artifact["repository"]))).delete_exact(artifact)
            try:
                self._observe_review_finalization_subject(
                    task_id=task_id,
                    worktree=str(artifact["repository"]),
                    branch=current_branch,
                    reviewed_revision_digest=run_revision_digest,
                    allowed_active_revision_digests=(run_revision_digest,),
                    attempt_digest=attempt_digest,
                    artifact=artifact,
                )
            except ValueError as error:
                raise ValueError(
                    "E_REVIEW_READY_RECOVERY_UNKNOWN: live subject drifted"
                ) from error
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                if current.get("review_ready_finalization") != final:
                    raise ValueError("E_REVIEW_READY_RECOVERY_UNKNOWN: marker changed")
                current.update({"state": "review_ready", "resume_forbidden": False,
                                "generation": expected_generation + 1,
                                "review_attempt_digest": attempt_digest,
                                "review_promotion_digest": promotion_digest,
                                "updated_at": _utc_now()})
                current["delivery_review_binding"] = self._delivery_review_binding(final)
                current.setdefault("evidence", {})["review_ready"] = {
                    "gates_ok": True, "documentation_decision": promotion_digest,
                }
                current.pop("review_ready_finalization", None)
                _atomic_json(state_path, current)
                return current

    def _recover_review_ready(self, task_id: str) -> dict[str, Any]:
        """Finish a review-ready marker whether deletion already happened or not."""

        from control_plane.run_workflow import ReviewArtifactStore, validate_stable_review_diff_artifact

        common_dir, state_path = _common_git_dir(self.state_dir), self._path(task_id)
        with _common_lease_lock(common_dir):
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                final = state.get("review_ready_finalization")
                if (
                    state.get("state") != "finalizing_review_ready"
                    or not isinstance(final, Mapping)
                    or not isinstance(final.get("prior_generation"), int)
                    or final.get("task_id") != task_id
                    or final.get("artifact_delete_started") is not True
                    or validate_stable_review_diff_artifact(final.get("artifact", {}))
                    or self._read_owner_lease(task_id) is not None
                    or not self._review_ready_finalization_is_bound(state, final)
                ):
                    raise ValueError("E_REVIEW_READY_RECOVERY_UNKNOWN: marker is invalid")
                final = copy.deepcopy(final)
            artifact = dict(final["artifact"])
            store = ReviewArtifactStore(Path(str(artifact["repository"])))
            try:
                self._observe_review_finalization_subject(
                    task_id=task_id,
                    worktree=str(artifact["repository"]),
                    branch=str(final["branch"]),
                    reviewed_revision_digest=str(
                        final["run_revision_digest"]
                    ),
                    allowed_active_revision_digests=(
                        str(final["run_revision_digest"]),
                    ),
                    attempt_digest=str(final["attempt_digest"]),
                    artifact=artifact,
                )
            except ValueError as error:
                raise ValueError(
                    "E_REVIEW_READY_RECOVERY_UNKNOWN: live subject drifted"
                ) from error
            deletion = store.artifact_state(artifact)
            if deletion == "drift":
                raise ValueError("E_REVIEW_READY_RECOVERY_UNKNOWN: artifact is inconsistent")
            if deletion in {"present", "partial"}:
                store.delete_exact(artifact)
            try:
                self._observe_review_finalization_subject(
                    task_id=task_id,
                    worktree=str(artifact["repository"]),
                    branch=str(final["branch"]),
                    reviewed_revision_digest=str(
                        final["run_revision_digest"]
                    ),
                    allowed_active_revision_digests=(
                        str(final["run_revision_digest"]),
                    ),
                    attempt_digest=str(final["attempt_digest"]),
                    artifact=artifact,
                )
            except ValueError as error:
                raise ValueError(
                    "E_REVIEW_READY_RECOVERY_UNKNOWN: live subject drifted"
                ) from error
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                if current.get("review_ready_finalization") != final:
                    raise ValueError("E_REVIEW_READY_RECOVERY_UNKNOWN: marker changed")
                current.update({"state": "review_ready", "resume_forbidden": False,
                                "generation": int(final["prior_generation"]) + 1,
                                "review_attempt_digest": final["attempt_digest"],
                                "review_promotion_digest": final["promotion_digest"],
                                "updated_at": _utc_now()})
                current["delivery_review_binding"] = self._delivery_review_binding(final)
                current.setdefault("evidence", {})["review_ready"] = {
                    "gates_ok": True, "documentation_decision": final["promotion_digest"],
                }
                current.pop("review_ready_finalization", None)
                _atomic_json(state_path, current)
                return current

    @staticmethod
    def _delivery_review_binding(final: Mapping[str, Any]) -> dict[str, Any]:
        artifact = final["artifact"]
        core = {
            "schema_version": 1,
            "kind": "DeliveryReviewBindingV1",
            "run_plan_digest": final["run_plan_digest"],
            "run_revision_digest": final["run_revision_digest"],
            "attempt_digest": final["attempt_digest"],
            "promotion_digest": final["promotion_digest"],
            "base_head": artifact["base_head"],
            "reviewed_head": artifact["reviewed_head"],
            "diff_digest": artifact["diff_digest"],
            "untracked_modes": artifact["untracked_modes"],
            "scope_paths": list(artifact["scope_paths"]),
            "receipt_digests": list(final["receipt_digests"]),
            "authorizes": False,
        }
        return {**core, "binding_digest": contract_digest(core)}

    def _review_ready_finalization_is_bound(
        self, state: Mapping[str, Any], final: Mapping[str, Any]
    ) -> bool:
        """Rebind a recovery marker to immutable run and review evidence."""

        from control_plane.run_workflow import (
            MAX_REVIEW_PACKET_BYTES,
            RunStore,
            _required_review_kinds,
            validate_independent_review_receipt,
            validate_review_packet,
        )

        expected_keys = {
            "prior_generation", "task_id", "run_plan_digest",
            "run_revision_digest", "attempt_digest", "promotion_digest",
            "receipt_digests", "artifact", "artifact_delete_started",
            "branch", "finalization_digest",
        }
        if set(final) != expected_keys:
            return False
        core = {
            key: value
            for key, value in final.items()
            if key != "finalization_digest"
        }
        if final.get("finalization_digest") != contract_digest(core):
            return False
        try:
            runs = RunStore(self.state_dir)
            task_id = str(final["task_id"])
            plan = runs.load_plan(task_id)
            revision = runs.load_active(task_id)
            attempts = runs.attempts(task_id)
            required_kinds = _required_review_kinds(plan)
        except (KeyError, TypeError, ValueError):
            return False
        if not attempts:
            return False
        latest = attempts[-1]
        artifact = final.get("artifact")
        receipt_digests = final.get("receipt_digests")
        handoff = state.get("evidence", {}).get("review_handoff")
        if (
            not isinstance(artifact, Mapping)
            or not isinstance(receipt_digests, list)
            or receipt_digests != sorted(receipt_digests)
            or len(receipt_digests) != len(required_kinds)
            or len(set(receipt_digests)) != len(receipt_digests)
            or state.get("generation") != final.get("prior_generation")
            or state.get("branch") != final.get("branch")
            or state.get("run_plan_digest") != final.get("run_plan_digest")
            or state.get("active_run_revision_digest")
            != final.get("run_revision_digest")
            or plan.get("plan_digest") != final.get("run_plan_digest")
            or plan.get("branch") != final.get("branch")
            or revision.get("revision_digest")
            != final.get("run_revision_digest")
            or latest.get("status") != "PASS"
            or latest.get("run_revision_digest")
            != revision.get("revision_digest")
            or latest.get("attempt_digest") != final.get("attempt_digest")
            or artifact.get("task_id") != task_id
            or artifact.get("attempt") != latest.get("attempt")
            or artifact.get("repository") != plan.get("repository")
            or artifact.get("reviewed_head") != revision.get("head")
            or tuple(artifact.get("scope_paths", ()))
            != tuple(latest.get("changed_paths", ()))
            or handoff
            != {
                "revision_digest": revision.get("revision_digest"),
                "attempt_digest": latest.get("attempt_digest"),
                "artifact_digest": artifact.get("artifact_digest"),
            }
        ):
            return False
        durable_digests: list[str] = []
        for review_kind in required_kinds:
            try:
                receipt = runs._load_closed_json(
                    runs._review_receipt_path(
                        task_id, int(latest["attempt"]), review_kind
                    ),
                    maximum=MAX_REVIEW_PACKET_BYTES,
                    code="E_INDEPENDENT_REVIEW",
                )
                packet = runs.load_review_packet(
                    task_id, int(latest["attempt"]), review_kind
                )
            except (KeyError, TypeError, ValueError):
                return False
            if (
                validate_independent_review_receipt(receipt)
                or validate_review_packet(packet)
                or receipt.get("status") != "PASS"
                or receipt.get("review_kind") != review_kind
                or packet.get("review_kind") != review_kind
                or receipt.get("review_packet_digest")
                != packet.get("packet_digest")
                or receipt.get("run_plan_digest") != plan.get("plan_digest")
                or receipt.get("run_revision_digest")
                != revision.get("revision_digest")
                or receipt.get("attempt_digest")
                != latest.get("attempt_digest")
                or receipt.get("artifact_digest")
                != artifact.get("artifact_digest")
                or receipt.get("diff_digest") != artifact.get("diff_digest")
                or packet.get("artifact_digest")
                != artifact.get("artifact_digest")
                or packet.get("diff_digest") != artifact.get("diff_digest")
            ):
                return False
            durable_digests.append(str(receipt["receipt_digest"]))
        if sorted(durable_digests) != receipt_digests:
            return False
        promotion_core = {
            "run_plan_digest": plan["plan_digest"],
            "review_receipt_digests": receipt_digests,
            "review_kinds": list(required_kinds),
            "authorizes": False,
        }
        return final.get("promotion_digest") == contract_digest(promotion_core)

    def finalize_exhausted_review(
        self,
        task_id: str,
        *,
        expected_generation: int,
        run_plan_digest: str,
        run_revision_digest: str,
        attempt_digest: str,
        review_kind: str,
        review_receipt_digest: str,
        artifact: Mapping[str, Any],
        current_branch: str,
    ) -> dict[str, Any]:
        """Block an exhausted review and remove its exact artifact durably."""

        from control_plane.run_workflow import (
            ReviewArtifactStore,
            validate_stable_review_diff_artifact,
        )

        if (
            validate_stable_review_diff_artifact(artifact)
            or not all(
                isinstance(value, str) and SHA256_DIGEST.fullmatch(value)
                for value in (
                    run_plan_digest,
                    run_revision_digest,
                    attempt_digest,
                    review_receipt_digest,
                )
            )
        ):
            raise ValueError(
                "E_RUN_EXHAUSTED: exhausted review proof is invalid"
            )
        common_dir, state_path = _common_git_dir(self.state_dir), self._path(task_id)
        with _common_lease_lock(common_dir):
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                if (
                    state.get("state") != "verifying"
                    or state.get("generation") != expected_generation
                    or state.get("branch") != current_branch
                    or state.get("run_plan_digest") != run_plan_digest
                    or state.get("active_run_revision_digest")
                    != run_revision_digest
                    or self._read_owner_lease(task_id) is not None
                ):
                    raise ValueError(
                        "E_RUN_EXHAUSTED: task is not awaiting final review"
                    )
                final_core = {
                    "prior_generation": expected_generation,
                    "task_id": task_id,
                    "run_plan_digest": run_plan_digest,
                    "run_revision_digest": run_revision_digest,
                    "attempt_digest": attempt_digest,
                    "review_kind": review_kind,
                    "review_receipt_digest": review_receipt_digest,
                    "artifact": dict(artifact),
                    "artifact_delete_started": True,
                    "branch": current_branch,
                }
                final = {
                    **final_core,
                    "finalization_digest": contract_digest(final_core),
                }
                marker = dict(state)
                marker.update(
                    {
                        "state": "finalizing_review_exhausted",
                        "resume_state": None,
                        "resume_forbidden": True,
                        "review_exhausted_finalization": final,
                        "updated_at": _utc_now(),
                    }
                )
                if not self._review_exhausted_finalization_is_bound(
                    marker, final
                ):
                    raise ValueError(
                        "E_RUN_EXHAUSTED: durable review proof is invalid"
                    )
                try:
                    self._observe_review_finalization_subject(
                        task_id=task_id,
                        worktree=str(artifact["repository"]),
                        branch=current_branch,
                        reviewed_revision_digest=run_revision_digest,
                        allowed_active_revision_digests=(
                            run_revision_digest,
                        ),
                        attempt_digest=attempt_digest,
                        artifact=artifact,
                    )
                except ValueError as error:
                    raise ValueError(
                        "E_RUN_EXHAUSTED: live review subject drifted"
                    ) from error
                _atomic_json(state_path, marker)
            ReviewArtifactStore(
                Path(str(artifact["repository"]))
            ).delete_exact(artifact)
            try:
                self._observe_review_finalization_subject(
                    task_id=task_id,
                    worktree=str(artifact["repository"]),
                    branch=current_branch,
                    reviewed_revision_digest=run_revision_digest,
                    allowed_active_revision_digests=(run_revision_digest,),
                    attempt_digest=attempt_digest,
                    artifact=artifact,
                )
            except ValueError as error:
                raise ValueError(
                    "E_REVIEW_EXHAUSTED_RECOVERY_UNKNOWN: live subject drifted"
                ) from error
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                if (
                    current.get("review_exhausted_finalization") != final
                    or not self._review_exhausted_finalization_is_bound(
                        current, final
                    )
                ):
                    raise ValueError(
                        "E_REVIEW_EXHAUSTED_RECOVERY_UNKNOWN: marker changed"
                    )
                return self._complete_exhausted_review_locked(
                    current=current,
                    state_path=state_path,
                    final=final,
                )

    def _recover_review_exhausted(self, task_id: str) -> dict[str, Any]:
        """Complete an interrupted third-review terminal block."""

        from control_plane.run_workflow import ReviewArtifactStore

        common_dir, state_path = _common_git_dir(self.state_dir), self._path(task_id)
        with _common_lease_lock(common_dir):
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                final = state.get("review_exhausted_finalization")
                if (
                    state.get("state") != "finalizing_review_exhausted"
                    or not isinstance(final, Mapping)
                    or not self._review_exhausted_finalization_is_bound(
                        state, final
                    )
                ):
                    raise ValueError(
                        "E_REVIEW_EXHAUSTED_RECOVERY_UNKNOWN: marker is invalid"
                    )
                final = copy.deepcopy(final)
            artifact = dict(final["artifact"])
            try:
                self._observe_review_finalization_subject(
                    task_id=task_id,
                    worktree=str(artifact["repository"]),
                    branch=str(final["branch"]),
                    reviewed_revision_digest=str(
                        final["run_revision_digest"]
                    ),
                    allowed_active_revision_digests=(
                        str(final["run_revision_digest"]),
                    ),
                    attempt_digest=str(final["attempt_digest"]),
                    artifact=artifact,
                )
            except ValueError as error:
                raise ValueError(
                    "E_REVIEW_EXHAUSTED_RECOVERY_UNKNOWN: live subject drifted"
                ) from error
            store = ReviewArtifactStore(Path(str(artifact["repository"])))
            deletion = store.artifact_state(artifact)
            if deletion == "drift":
                raise ValueError(
                    "E_REVIEW_EXHAUSTED_RECOVERY_UNKNOWN: artifact is inconsistent"
                )
            if deletion in {"present", "partial"}:
                store.delete_exact(artifact)
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                if (
                    current.get("review_exhausted_finalization") != final
                    or not self._review_exhausted_finalization_is_bound(
                        current, final
                    )
                ):
                    raise ValueError(
                        "E_REVIEW_EXHAUSTED_RECOVERY_UNKNOWN: marker changed"
                    )
                return self._complete_exhausted_review_locked(
                    current=current,
                    state_path=state_path,
                    final=final,
                )

    @staticmethod
    def _complete_exhausted_review_locked(
        *, current: dict[str, Any], state_path: Path, final: Mapping[str, Any]
    ) -> dict[str, Any]:
        evidence = dict(current.get("evidence", {}))
        evidence.pop("review_handoff", None)
        current.update(
            {
                "state": "blocked",
                "resume_state": None,
                "resume_forbidden": True,
                "block_reason": "E_RUN_EXHAUSTED",
                "generation": int(final["prior_generation"]) + 1,
                "evidence": evidence,
                "updated_at": _utc_now(),
            }
        )
        current.pop("review_exhausted_finalization", None)
        _atomic_json(state_path, current)
        return current

    def _review_exhausted_finalization_is_bound(
        self, state: Mapping[str, Any], final: Mapping[str, Any]
    ) -> bool:
        """Rebind an exhausted-review marker to the exact failed receipt."""

        from control_plane.run_workflow import (
            MAX_EXECUTIONS,
            MAX_REVIEW_PACKET_BYTES,
            RunStore,
            validate_independent_review_receipt,
            validate_review_packet,
            validate_stable_review_diff_artifact,
        )

        expected_keys = {
            "prior_generation", "task_id", "run_plan_digest",
            "run_revision_digest", "attempt_digest", "review_kind",
            "review_receipt_digest", "artifact", "artifact_delete_started",
            "branch", "finalization_digest",
        }
        if set(final) != expected_keys:
            return False
        core = {
            key: value
            for key, value in final.items()
            if key != "finalization_digest"
        }
        if final.get("finalization_digest") != contract_digest(core):
            return False
        try:
            runs = RunStore(self.state_dir)
            task_id = str(final["task_id"])
            plan = runs.load_plan(task_id)
            revision = runs.load_active(task_id)
            attempts = runs.attempts(task_id)
            if not attempts:
                return False
            latest = attempts[-1]
            review_kind = str(final["review_kind"])
            receipt = runs._load_closed_json(
                runs._review_receipt_path(
                    task_id, int(latest["attempt"]), review_kind
                ),
                maximum=MAX_REVIEW_PACKET_BYTES,
                code="E_INDEPENDENT_REVIEW",
            )
            packet = runs.load_review_packet(
                task_id, int(latest["attempt"]), review_kind
            )
        except (KeyError, OSError, TypeError, ValueError):
            return False
        artifact = final.get("artifact")
        handoff = state.get("evidence", {}).get("review_handoff")
        if (
            not isinstance(artifact, Mapping)
            or validate_stable_review_diff_artifact(artifact)
            or state.get("state") != "finalizing_review_exhausted"
            or state.get("resume_forbidden") is not True
            or state.get("generation") != final.get("prior_generation")
            or state.get("branch") != final.get("branch")
            or state.get("run_plan_digest") != final.get("run_plan_digest")
            or state.get("active_run_revision_digest")
            != final.get("run_revision_digest")
            or self._read_owner_lease(task_id) is not None
            or plan.get("plan_digest") != final.get("run_plan_digest")
            or plan.get("branch") != final.get("branch")
            or plan.get("tier") not in {"T2", "T3"}
            or revision.get("revision_digest")
            != final.get("run_revision_digest")
            or latest.get("attempt") != MAX_EXECUTIONS
            or latest.get("status") != "PASS"
            or latest.get("run_revision_digest")
            != revision.get("revision_digest")
            or latest.get("attempt_digest") != final.get("attempt_digest")
            or artifact.get("task_id") != task_id
            or artifact.get("attempt") != latest.get("attempt")
            or artifact.get("repository") != plan.get("repository")
            or artifact.get("reviewed_head") != revision.get("head")
            or tuple(artifact.get("scope_paths", ()))
            != tuple(latest.get("changed_paths", ()))
            or validate_independent_review_receipt(receipt)
            or receipt.get("receipt_digest")
            != final.get("review_receipt_digest")
            or receipt.get("status") != "FAIL"
            or int(receipt.get("critical", 0))
            + int(receipt.get("important", 0))
            <= 0
            or receipt.get("review_kind") != review_kind
            or receipt.get("run_plan_digest") != plan.get("plan_digest")
            or receipt.get("run_revision_digest")
            != revision.get("revision_digest")
            or receipt.get("attempt_digest") != latest.get("attempt_digest")
            or receipt.get("artifact_digest")
            != artifact.get("artifact_digest")
            or receipt.get("diff_digest") != artifact.get("diff_digest")
            or validate_review_packet(packet)
            or receipt.get("review_packet_digest")
            != packet.get("packet_digest")
            or packet.get("run_plan_digest") != plan.get("plan_digest")
            or packet.get("run_revision_digest")
            != revision.get("revision_digest")
            or packet.get("attempt_digest") != latest.get("attempt_digest")
            or packet.get("artifact_digest")
            != artifact.get("artifact_digest")
            or packet.get("diff_digest") != artifact.get("diff_digest")
            or handoff
            != {
                "revision_digest": revision.get("revision_digest"),
                "attempt_digest": latest.get("attempt_digest"),
                "artifact_digest": artifact.get("artifact_digest"),
            }
        ):
            return False
        return True

    @staticmethod
    def _observe_review_handoff_subject(
        *, task_id: str, worktree: str, branch: str,
        active_revision_digest: str, attempt_digest: str,
        artifact_digest: str,
    ) -> dict[str, Any]:
        """Recapture the exact review subject from the repository, never a caller."""

        from control_plane.run_workflow import (
            ReviewArtifactStore, RunStore, _changed_paths,
        )

        root = Path(worktree).resolve()
        run_store = RunStore(worktree_git_dir(root))
        active = run_store.load_active(task_id)
        attempts = run_store.attempts(task_id)
        actual_branch = _git_branch(root)
        actual_head = _git_head(root)
        if not attempts or not actual_branch or not actual_head:
            raise ValueError("E_REVIEW_HANDOFF_UNKNOWN: live subject is unavailable")
        latest = attempts[-1]
        try:
            artifact = ReviewArtifactStore(root).load_manifest(
                task_id, int(latest["attempt"]),
            )
            paths = _changed_paths(root)
            diff = ReviewArtifactStore(root)._capture_diff(actual_head, paths)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise ValueError("E_REVIEW_HANDOFF_UNKNOWN: live subject is unavailable") from error
        subject = {
            "branch": actual_branch,
            "head": actual_head,
            "scope_paths": list(paths),
            "diff_digest": ReviewArtifactStore._diff_digest(diff),
            "diff_size": len(diff),
            "revision_digest": active.get("revision_digest"),
            "attempt_digest": latest.get("attempt_digest"),
            "artifact_digest": artifact.get("artifact_digest"),
        }
        if (
            actual_branch != branch
            or active.get("revision_digest") != active_revision_digest
            or active.get("head") != actual_head
            or latest.get("status") != "PASS"
            or latest.get("attempt_digest") != attempt_digest
            or latest.get("head") != actual_head
            or artifact.get("artifact_digest") != artifact_digest
            or artifact.get("reviewed_head") != actual_head
            or tuple(artifact.get("scope_paths", ())) != paths
            or artifact.get("diff_digest") != subject["diff_digest"]
            or artifact.get("diff_size") != subject["diff_size"]
        ):
            raise ValueError("E_REVIEW_HANDOFF_DRIFT: live subject changed")
        return subject

    @staticmethod
    def _observe_review_finalization_subject(
        *,
        task_id: str,
        worktree: str,
        branch: str,
        reviewed_revision_digest: str,
        allowed_active_revision_digests: tuple[str, ...],
        attempt_digest: str,
        artifact: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Recapture a marker-bound diff even after its stored copy was deleted."""

        from control_plane.run_workflow import (
            ReviewArtifactStore,
            RunStore,
            _changed_paths,
            validate_stable_review_diff_artifact,
        )

        root = Path(worktree).resolve()
        if (
            validate_stable_review_diff_artifact(artifact)
            or Path(str(artifact.get("repository", ""))).resolve() != root
            or not allowed_active_revision_digests
        ):
            raise ValueError(
                "E_REVIEW_FINALIZATION_DRIFT: artifact binding is invalid"
            )
        runs = RunStore(worktree_git_dir(root))
        active = runs.load_active(task_id)
        attempts = runs.attempts(task_id)
        actual_branch = _git_branch(root)
        actual_head = _git_head(root)
        if not attempts or not actual_branch or not actual_head:
            raise ValueError(
                "E_REVIEW_FINALIZATION_DRIFT: live subject is unavailable"
            )
        latest = attempts[-1]
        paths = _changed_paths(root)
        diff = ReviewArtifactStore(root)._capture_diff(actual_head, paths)
        subject = {
            "branch": actual_branch,
            "head": actual_head,
            "scope_paths": list(paths),
            "diff_digest": ReviewArtifactStore._diff_digest(diff),
            "diff_size": len(diff),
            "active_revision_digest": active.get("revision_digest"),
            "attempt_digest": latest.get("attempt_digest"),
        }
        if (
            actual_branch != branch
            or active.get("revision_digest")
            not in set(allowed_active_revision_digests)
            or active.get("head") != actual_head
            or latest.get("status") != "PASS"
            or latest.get("run_revision_digest")
            != reviewed_revision_digest
            or latest.get("attempt_digest") != attempt_digest
            or latest.get("head") != actual_head
            or artifact.get("task_id") != task_id
            or artifact.get("reviewed_head") != actual_head
            or tuple(artifact.get("scope_paths", ())) != paths
            or artifact.get("diff_digest") != subject["diff_digest"]
            or artifact.get("diff_size") != subject["diff_size"]
        ):
            raise ValueError(
                "E_REVIEW_FINALIZATION_DRIFT: live subject changed"
            )
        return subject

    def _terminally_block_review_handoff_locked(
        self, *, task_id: str, current_branch: str, state_path: Path
    ) -> dict[str, Any]:
        """Invalidate a stale packet so generic resume cannot reactivate it."""

        blocked = self._transition_locked(
            task_id,
            "blocked",
            reason="E_REVIEW_HANDOFF_REPLAY_INVALID",
            evidence=None,
            current_branch=current_branch,
        )
        blocked["resume_forbidden"] = True
        blocked["resume_state"] = None
        blocked.setdefault("evidence", {}).pop("review_handoff", None)
        _atomic_json(state_path, blocked)
        return blocked

    def handoff_to_local_review(
        self,
        task_id: str,
        *,
        expected_generation: int,
        active_revision_digest: str,
        attempt_digest: str,
        artifact_digest: str,
        worktree: str,
        branch: str,
        session: str,
        policy_digest: str,
    ) -> dict[str, Any]:
        """Release the implementation lease only after durable review proof.

        The visible state deliberately remains ``verifying``: a local reviewer
        is not an implementation owner and must never inherit its writer lease.
        """

        if (
            not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or not all(
                isinstance(value, str) and SHA256_DIGEST.fullmatch(value)
                for value in (
                    active_revision_digest, attempt_digest, artifact_digest,
                    policy_digest,
                )
            )
            or not _valid_branch(branch)
            or not validate_task_id(session)
        ):
            raise ValueError("E_REVIEW_HANDOFF: request binding is invalid")
        canonical_worktree = str(Path(worktree).resolve())
        evidence = {
            "revision_digest": active_revision_digest,
            "attempt_digest": attempt_digest,
            "artifact_digest": artifact_digest,
        }
        common_dir = _common_git_dir(self.state_dir)
        state_path = self._path(task_id)
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                if (
                    state.get("state") == "verifying"
                    and state.get("evidence", {}).get("review_handoff") == evidence
                    and self._read_owner_lease(task_id) is None
                ):
                    if (
                        state.get("branch") != branch
                        or state.get("active_run_revision_digest") != active_revision_digest
                    ):
                        raise ValueError("E_REVIEW_HANDOFF: completed binding drifted")
                    try:
                        self._observe_review_handoff_subject(
                            task_id=task_id,
                            worktree=canonical_worktree,
                            branch=branch,
                            active_revision_digest=active_revision_digest,
                            attempt_digest=attempt_digest,
                            artifact_digest=artifact_digest,
                        )
                    except ValueError:
                        self._terminally_block_review_handoff_locked(
                            task_id=task_id,
                            current_branch=branch,
                            state_path=state_path,
                        )
                        raise
                    return state
                if (
                    state.get("state") != "verifying"
                    or state.get("generation") != expected_generation
                    or state.get("branch") != branch
                    or state.get("active_run_revision_digest") != active_revision_digest
                    or state.get("resume_forbidden")
                ):
                    raise ValueError("E_REVIEW_HANDOFF: task binding changed")
                # Read durable proof under the state lock, before publishing the
                # marker.  This prevents a packet from releasing a lease for an
                # unproven or stale verification result.
                from control_plane.run_workflow import RunStore, ReviewArtifactStore

                run_store = RunStore(self.state_dir)
                active = run_store.load_active(task_id)
                attempts = run_store.attempts(task_id)
                if (
                    active.get("revision_digest") != active_revision_digest
                    or not attempts
                    or attempts[-1].get("status") != "PASS"
                    or attempts[-1].get("attempt_digest") != attempt_digest
                ):
                    raise ValueError("E_REVIEW_HANDOFF: attempt evidence is invalid")
                artifact = ReviewArtifactStore(Path(canonical_worktree)).load_manifest(
                    task_id, int(attempts[-1]["attempt"])
                )
                if artifact.get("artifact_digest") != artifact_digest:
                    raise ValueError("E_REVIEW_HANDOFF: artifact evidence is invalid")
                lease = self._read_owner_lease(task_id)
                if (
                    lease is None
                    or lease.get("task_id") != task_id
                    or lease.get("worktree") != canonical_worktree
                    or lease.get("branch") != branch
                    or lease.get("session_id") != session
                    or lease.get("policy_digest") != policy_digest
                    or not isinstance(lease.get("lease_digest"), str)
                ):
                    raise ValueError("E_REVIEW_HANDOFF: owner lease is invalid")
                # Capture after proving the exact owner lease and before the
                # marker.  This is the last point at which pre-release drift
                # can leave the implementation lease safely in place.
                subject = self._observe_review_handoff_subject(
                    task_id=task_id, worktree=canonical_worktree, branch=branch,
                    active_revision_digest=active_revision_digest,
                    attempt_digest=attempt_digest, artifact_digest=artifact_digest,
                )
                marker = dict(state)
                marker.update(
                    {
                        "state": "finalizing_review_handoff",
                        "resume_state": None,
                        "resume_forbidden": True,
                        "review_handoff_finalization": {
                            "prior_generation": expected_generation,
                            "evidence": evidence,
                            "task_id": task_id,
                            "worktree": canonical_worktree,
                            "branch": branch,
                            "session_id": session,
                            "policy_digest": policy_digest,
                            "lease_digest": lease["lease_digest"],
                            "subject": subject,
                        },
                        "updated_at": _utc_now(),
                    }
                )
                _atomic_json(state_path, marker)
            TaskLease._release_locked(
                token,
                state_dir=self.state_dir,
                task_id=task_id,
                worktree=canonical_worktree,
                branch=branch,
                session_id=session,
                policy_digest=policy_digest,
                lease_digest=str(lease["lease_digest"]),
            )
            # Re-read after the release boundary.  A drift here must remain
            # finalizing: no stale packet becomes active and recovery repeats
            # the same repository-bound observation.
            self._observe_review_handoff_subject(
                task_id=task_id, worktree=canonical_worktree, branch=branch,
                active_revision_digest=active_revision_digest,
                attempt_digest=attempt_digest, artifact_digest=artifact_digest,
            )
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                if (
                    current.get("state") != "finalizing_review_handoff"
                    or current.get("review_handoff_finalization")
                    != marker["review_handoff_finalization"]
                ):
                    raise ValueError("E_STATE_CAS: review handoff marker changed")
                current.update(
                    {
                        "state": "verifying",
                        "resume_state": None,
                        "resume_forbidden": False,
                        "generation": expected_generation + 1,
                        "updated_at": _utc_now(),
                    }
                )
                current.pop("lease_digest", None)
                current.pop("review_handoff_finalization", None)
                current.setdefault("evidence", {})["review_handoff"] = evidence
                _atomic_json(state_path, current)
                return current

    def _recover_review_handoff(self, task_id: str) -> dict[str, Any]:
        """Finish the marker/release boundary without granting a new lease."""

        common_dir = _common_git_dir(self.state_dir)
        state_path = self._path(task_id)
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                finalization = state.get("review_handoff_finalization")
                if (
                    state.get("state") != "finalizing_review_handoff"
                    or not isinstance(finalization, Mapping)
                    or not isinstance(finalization.get("evidence"), Mapping)
                    or not isinstance(finalization.get("prior_generation"), int)
                ):
                    raise ValueError("E_REVIEW_HANDOFF_RECOVERY_UNKNOWN: marker is invalid")
                binding = {
                    key: finalization.get(key)
                    for key in ("task_id", "worktree", "branch", "session_id", "policy_digest", "lease_digest")
                }
                if (
                    binding.get("task_id") != task_id
                    or not isinstance(binding["worktree"], str)
                    or not _valid_branch(binding["branch"])
                    or not validate_task_id(binding["session_id"])
                    or not isinstance(binding["policy_digest"], str)
                    or SHA256_DIGEST.fullmatch(binding["policy_digest"]) is None
                    or not isinstance(binding["lease_digest"], str)
                    or SHA256_DIGEST.fullmatch(binding["lease_digest"]) is None
                ):
                    raise ValueError("E_REVIEW_HANDOFF_RECOVERY_UNKNOWN: marker binding is invalid")
                subject = finalization.get("subject")
                evidence = finalization.get("evidence")
                if (
                    not isinstance(subject, Mapping)
                    or not isinstance(evidence, Mapping)
                    or subject.get("revision_digest") != evidence.get("revision_digest")
                    or subject.get("attempt_digest") != evidence.get("attempt_digest")
                    or subject.get("artifact_digest") != evidence.get("artifact_digest")
                ):
                    raise ValueError("E_REVIEW_HANDOFF_RECOVERY_UNKNOWN: marker subject is invalid")
                lease = self._read_owner_lease(task_id)
                if lease is not None and any(lease.get(key) != value for key, value in binding.items()):
                    raise ValueError("E_REVIEW_HANDOFF_RECOVERY_UNKNOWN: owner lease mismatched")
                marker = copy.deepcopy(finalization)
                generation = int(state["generation"])
            observed = self._observe_review_handoff_subject(
                task_id=task_id, worktree=str(binding["worktree"]),
                branch=str(binding["branch"]),
                active_revision_digest=str(evidence["revision_digest"]),
                attempt_digest=str(evidence["attempt_digest"]),
                artifact_digest=str(evidence["artifact_digest"]),
            )
            if observed != dict(subject):
                raise ValueError("E_REVIEW_HANDOFF_DRIFT: live subject changed")
            if lease is not None:
                try:
                    TaskLease._release_locked(
                        token, state_dir=self.state_dir, task_id=task_id,
                        worktree=str(binding["worktree"]), branch=str(binding["branch"]),
                        session_id=str(binding["session_id"]), policy_digest=str(binding["policy_digest"]),
                        lease_digest=str(binding["lease_digest"]),
                    )
                except ValueError as error:
                    raise ValueError("E_REVIEW_HANDOFF_RECOVERY_UNKNOWN: release is ambiguous") from error
            observed = self._observe_review_handoff_subject(
                task_id=task_id, worktree=str(binding["worktree"]),
                branch=str(binding["branch"]),
                active_revision_digest=str(evidence["revision_digest"]),
                attempt_digest=str(evidence["attempt_digest"]),
                artifact_digest=str(evidence["artifact_digest"]),
            )
            if observed != dict(subject):
                raise ValueError("E_REVIEW_HANDOFF_DRIFT: live subject changed")
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                if (
                    current.get("state") != "finalizing_review_handoff"
                    or current.get("review_handoff_finalization") != marker
                    or int(current.get("generation", -1)) != generation
                ):
                    raise ValueError("E_REVIEW_HANDOFF_RECOVERY_UNKNOWN: marker changed")
                current.update({
                    "state": "verifying", "resume_state": None,
                    "resume_forbidden": False,
                    "generation": int(marker["prior_generation"]) + 1,
                    "updated_at": _utc_now(),
                })
                current.pop("lease_digest", None)
                current.pop("review_handoff_finalization", None)
                current.setdefault("evidence", {})["review_handoff"] = dict(marker["evidence"])
                _atomic_json(state_path, current)
                return current

    @staticmethod
    def _local_review_lease_binding(
        *, task_id: str, worktree: str, branch: str, session_id: str,
        policy_digest: str, scope_paths: list[str],
    ) -> dict[str, Any]:
        """Compute the exact fresh lease expected by local review recovery."""

        normalized = [normalize_scope(path) for path in scope_paths]
        if (
            not validate_task_id(task_id)
            or not _valid_branch(branch)
            or not validate_task_id(session_id)
            or not scope_paths
            or any(path is None for path in normalized)
            or SHA256_DIGEST.fullmatch(policy_digest) is None
        ):
            raise ValueError("E_LOCAL_REVIEW: lease binding is invalid")
        payload = {
            "schema_version": 1,
            "task_id": task_id,
            "worktree": str(Path(worktree).resolve()),
            "branch": branch,
            "session_id": session_id,
            "paths": sorted(set(str(path) for path in normalized)),
            "policy_digest": policy_digest,
        }
        return {**payload, "lease_digest": contract_digest(payload)}

    def start_local_review_revision(
        self,
        task_id: str,
        *,
        expected_generation: int,
        run_plan: Mapping[str, Any],
        parent_revision: Mapping[str, Any],
        latest_attempt: Mapping[str, Any],
        review_receipt: Mapping[str, Any],
        artifact: Mapping[str, Any],
        revision: Mapping[str, Any],
        worktree: str,
        policy_digest: str,
        new_session_id: str,
    ) -> dict[str, Any]:
        """Atomically resume implementation from a blocking local review.

        The marker deliberately precedes acquisition.  A failed acquisition
        rolls back to verifying, while every later boundary is recoverable from
        the exact marker and the fresh lease it names.
        """

        from control_plane.run_workflow import (
            ReviewArtifactStore, RunStore, validate_independent_review_receipt,
            validate_run_plan, validate_run_revision,
            validate_stable_review_diff_artifact,
        )

        canonical_worktree = str(Path(worktree).resolve())
        if (
            not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or validate_run_plan(run_plan)
            or validate_run_revision(parent_revision)
            or validate_run_revision(revision)
            or validate_independent_review_receipt(review_receipt)
            or validate_stable_review_diff_artifact(artifact)
            or Path(str(run_plan.get("repository", ""))).resolve()
            != Path(canonical_worktree)
            or revision.get("parent_revision_digest")
            != parent_revision.get("revision_digest")
            or revision.get("first_attempt")
            != int(latest_attempt.get("attempt", 0)) + 1
            or revision.get("head") != parent_revision.get("head")
            or revision.get("source_attempt_digest")
            != latest_attempt.get("attempt_digest")
            or revision.get("source_review_receipt_digest")
            != review_receipt.get("receipt_digest")
            or revision.get("source_diff_digest") != artifact.get("diff_digest")
            or review_receipt.get("diff_digest") != artifact.get("diff_digest")
        ):
            raise ValueError("E_LOCAL_REVIEW: revision binding is invalid")
        lease_binding = self._local_review_lease_binding(
            task_id=task_id, worktree=canonical_worktree,
            branch=str(run_plan["branch"]), session_id=new_session_id,
            policy_digest=policy_digest,
            scope_paths=[str(path) for path in run_plan["scope_paths"]],
        )
        common_dir = _common_git_dir(self.state_dir)
        invocation_id = f"local-review-{uuid4().hex}"
        observation = observe_worktree_inventory(
            canonical_common_git_dir=common_dir, invocation_id=invocation_id,
            clock=time.monotonic, ttl_seconds=30, max_output_bytes=1_048_576,
        )
        inventory = validate_worktree_inventory_observation(
            observation, expected_common_git_dir=common_dir,
            expected_invocation_id=invocation_id, clock=time.monotonic,
        )
        state_path = self._path(task_id)
        marker: dict[str, Any]
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                if (
                    state.get("state") != "verifying"
                    or state.get("generation") != expected_generation
                    or state.get("branch") != run_plan.get("branch")
                    or state.get("active_run_revision_digest")
                    != parent_revision.get("revision_digest")
                    or self._read_owner_lease(task_id) is not None
                    or _git_head(Path(canonical_worktree)) != parent_revision.get("head")
                    or int(latest_attempt.get("attempt", 0)) >= 3
                ):
                    raise ValueError("E_LOCAL_REVIEW: task is not eligible for local revision")
                branch = _git_branch(Path(canonical_worktree))
                if branch != run_plan.get("branch"):
                    raise ValueError("E_LOCAL_REVIEW: current branch drifted")
                try:
                    self._observe_review_handoff_subject(
                        task_id=task_id,
                        worktree=canonical_worktree,
                        branch=str(run_plan["branch"]),
                        active_revision_digest=str(
                            parent_revision["revision_digest"]
                        ),
                        attempt_digest=str(latest_attempt["attempt_digest"]),
                        artifact_digest=str(artifact["artifact_digest"]),
                    )
                except ValueError:
                    self._terminally_block_review_handoff_locked(
                        task_id=task_id,
                        current_branch=str(run_plan["branch"]),
                        state_path=state_path,
                    )
                    raise
                final_core = {
                    "prior_state": "verifying",
                    "prior_generation": expected_generation,
                    "task_id": task_id,
                    "lease": lease_binding,
                    "parent_revision_digest": parent_revision["revision_digest"],
                    "revision": dict(revision),
                    "review_kind": review_receipt["review_kind"],
                    "review_receipt_digest": review_receipt["receipt_digest"],
                    "artifact": dict(artifact),
                    "artifact_delete_started": True,
                }
                finalization = {
                    **final_core,
                    "finalization_digest": contract_digest(final_core),
                }
                marker = dict(state)
                marker.update({
                    "state": "finalizing_local_review_revision",
                    "resume_state": None,
                    "resume_forbidden": True,
                    "local_review_revision_finalization": finalization,
                    "updated_at": _utc_now(),
                })
                if not self._local_review_finalization_is_bound(
                    marker, finalization
                ):
                    raise ValueError(
                        "E_LOCAL_REVIEW: durable review proof is invalid"
                    )
                _atomic_json(state_path, marker)
            try:
                lease = TaskLease._acquire_locked(
                    token, task_id=task_id, worktree=canonical_worktree,
                    branch=str(run_plan["branch"]), session_id=new_session_id,
                    policy_digest=policy_digest,
                    scopes=[str(path) for path in run_plan["scope_paths"]],
                    inventory=inventory,
                )
            except Exception:
                with _task_guard(self.state_dir, task_id):
                    current = self._read(task_id)
                    if current.get("local_review_revision_finalization") != marker["local_review_revision_finalization"]:
                        raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: marker changed")
                    current.update({"state": "verifying", "resume_forbidden": False,
                                    "generation": expected_generation, "updated_at": _utc_now()})
                    current.pop("local_review_revision_finalization", None)
                    _atomic_json(state_path, current)
                raise
            if lease != lease_binding:
                raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: fresh lease drifted")
            with _task_guard(self.state_dir, task_id):
                RunStore(self.state_dir).write_review_revision(revision)
                ReviewArtifactStore(Path(canonical_worktree)).delete_exact(artifact)
                try:
                    self._observe_review_finalization_subject(
                        task_id=task_id,
                        worktree=canonical_worktree,
                        branch=str(run_plan["branch"]),
                        reviewed_revision_digest=str(
                            parent_revision["revision_digest"]
                        ),
                        allowed_active_revision_digests=(
                            str(parent_revision["revision_digest"]),
                            str(revision["revision_digest"]),
                        ),
                        attempt_digest=str(latest_attempt["attempt_digest"]),
                        artifact=artifact,
                    )
                except ValueError as error:
                    raise ValueError(
                        "E_LOCAL_REVIEW_RECOVERY_UNKNOWN: live subject drifted"
                    ) from error
                current = self._read(task_id)
                if (
                    current.get("state") != "finalizing_local_review_revision"
                    or current.get("local_review_revision_finalization")
                    != marker["local_review_revision_finalization"]
                    or self._read_owner_lease(task_id) != lease_binding
                    or not self._local_review_finalization_is_bound(
                        current, marker["local_review_revision_finalization"]
                    )
                ):
                    raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: marker changed")
                evidence = dict(current.get("evidence", {}))
                for key in tuple(evidence):
                    if "review" in key:
                        evidence.pop(key, None)
                current.update({
                    "state": "implementing", "resume_state": None,
                    "resume_forbidden": False, "generation": expected_generation + 1,
                    "lease_digest": lease["lease_digest"],
                    "implementation_session_id": new_session_id,
                    "active_run_revision_digest": revision["revision_digest"],
                    "evidence": evidence, "updated_at": _utc_now(),
                })
                for key in ("local_review_revision_finalization", "review_attempt_digest",
                            "review_promotion_digest", "review_receipt_digest"):
                    current.pop(key, None)
                _atomic_json(state_path, current)
                return current

    def _recover_local_review_revision(self, task_id: str) -> dict[str, Any]:
        """Recover a local-review marker without activating an orphan revision."""

        from control_plane.run_workflow import ReviewArtifactStore, RunStore, validate_run_revision, validate_stable_review_diff_artifact

        common_dir, state_path = _common_git_dir(self.state_dir), self._path(task_id)
        with _common_lease_lock(common_dir) as token:
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                final = state.get("local_review_revision_finalization")
                if (
                    state.get("state") != "finalizing_local_review_revision"
                    or not isinstance(final, Mapping)
                    or not isinstance(final.get("prior_generation"), int)
                    or not isinstance(final.get("lease"), Mapping)
                    or not isinstance(final.get("revision"), Mapping)
                    or not isinstance(final.get("artifact"), Mapping)
                    or final.get("artifact_delete_started") is not True
                    or validate_run_revision(final["revision"])
                    or validate_stable_review_diff_artifact(final["artifact"])
                    or not self._local_review_finalization_is_bound(state, final)
                ):
                    raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: marker is invalid")
                final = copy.deepcopy(final)
                lease = self._read_owner_lease(task_id)
                binding = dict(final["lease"])
                expected = {key: binding.get(key) for key in ("task_id", "worktree", "branch", "session_id", "policy_digest", "lease_digest")}
                if expected.get("task_id") != task_id or not all(isinstance(value, str) for value in expected.values()):
                    raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: lease binding is invalid")
                if lease is not None and any(lease.get(key) != value for key, value in expected.items()):
                    raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: owner lease mismatched")
            runs = RunStore(self.state_dir)
            artifact_store = ReviewArtifactStore(Path(str(binding["worktree"])))
            revision = dict(final["revision"])
            artifact = dict(final["artifact"])
            if lease is None:
                revision_path = runs._revision_path(task_id, int(revision["revision"]))
                if revision_path.exists() or revision_path.is_symlink():
                    runs.delete_review_revision_exact(revision)
                if artifact_store.artifact_state(artifact) == "drift":
                    raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: artifact is inconsistent")
                with _task_guard(self.state_dir, task_id):
                    current = self._read(task_id)
                    if current.get("local_review_revision_finalization") != final:
                        raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: marker changed")
                    current.update({"state": final["prior_state"], "generation": final["prior_generation"],
                                    "resume_forbidden": False, "updated_at": _utc_now()})
                    current.pop("local_review_revision_finalization", None)
                    _atomic_json(state_path, current)
                    return current
            try:
                self._observe_review_finalization_subject(
                    task_id=task_id,
                    worktree=str(binding["worktree"]),
                    branch=str(binding["branch"]),
                    reviewed_revision_digest=str(
                        final["parent_revision_digest"]
                    ),
                    allowed_active_revision_digests=(
                        str(final["parent_revision_digest"]),
                        str(revision["revision_digest"]),
                    ),
                    attempt_digest=str(revision["source_attempt_digest"]),
                    artifact=artifact,
                )
            except ValueError as error:
                raise ValueError(
                    "E_LOCAL_REVIEW_RECOVERY_UNKNOWN: live subject drifted"
                ) from error
            runs.write_review_revision(revision)
            deletion = artifact_store.artifact_state(artifact)
            if deletion == "drift":
                raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: artifact is inconsistent")
            if deletion in {"present", "partial"}:
                artifact_store.delete_exact(artifact)
            try:
                self._observe_review_finalization_subject(
                    task_id=task_id,
                    worktree=str(binding["worktree"]),
                    branch=str(binding["branch"]),
                    reviewed_revision_digest=str(
                        final["parent_revision_digest"]
                    ),
                    allowed_active_revision_digests=(
                        str(final["parent_revision_digest"]),
                        str(revision["revision_digest"]),
                    ),
                    attempt_digest=str(revision["source_attempt_digest"]),
                    artifact=artifact,
                )
            except ValueError as error:
                raise ValueError(
                    "E_LOCAL_REVIEW_RECOVERY_UNKNOWN: live subject drifted"
                ) from error
            with _task_guard(self.state_dir, task_id):
                current = self._read(task_id)
                if current.get("local_review_revision_finalization") != final or self._read_owner_lease(task_id) != binding:
                    raise ValueError("E_LOCAL_REVIEW_RECOVERY_UNKNOWN: marker changed")
                evidence = dict(current.get("evidence", {}))
                for key in tuple(evidence):
                    if "review" in key:
                        evidence.pop(key, None)
                current.update({"state": "implementing", "resume_forbidden": False,
                                "generation": int(final["prior_generation"]) + 1,
                                "lease_digest": binding["lease_digest"],
                                "implementation_session_id": binding["session_id"],
                                "active_run_revision_digest": revision["revision_digest"],
                                "evidence": evidence, "updated_at": _utc_now()})
                current.pop("local_review_revision_finalization", None)
                _atomic_json(state_path, current)
                return current

    def _local_review_finalization_is_bound(
        self, state: Mapping[str, Any], final: Mapping[str, Any]
    ) -> bool:
        """Rebind a local-review recovery marker to durable review evidence."""

        from control_plane.policy import load_policy
        from control_plane.run_workflow import (
            MAX_REVIEW_PACKET_BYTES,
            RunStore,
            validate_independent_review_receipt,
            validate_review_packet,
            validate_run_revision,
        )

        expected_keys = {
            "prior_state", "prior_generation", "task_id", "lease",
            "parent_revision_digest", "revision", "review_kind",
            "review_receipt_digest", "artifact", "artifact_delete_started",
            "finalization_digest",
        }
        if set(final) != expected_keys:
            return False
        core = {
            key: value
            for key, value in final.items()
            if key != "finalization_digest"
        }
        if final.get("finalization_digest") != contract_digest(core):
            return False
        try:
            task_id = str(final["task_id"])
            binding = dict(final["lease"])
            revision = dict(final["revision"])
            artifact = dict(final["artifact"])
            review_kind = str(final["review_kind"])
            runs = RunStore(self.state_dir)
            plan = runs.load_plan(task_id)
            attempts = runs.attempts(task_id)
            if not attempts:
                return False
            latest = attempts[-1]
            parent_number = int(revision["revision"]) - 1
            if parent_number < 0:
                return False
            parent = runs._read_revision(task_id, parent_number)
            active = runs.load_active(task_id)
            receipt = runs._load_closed_json(
                runs._review_receipt_path(
                    task_id, int(latest["attempt"]), review_kind
                ),
                maximum=MAX_REVIEW_PACKET_BYTES,
                code="E_INDEPENDENT_REVIEW",
            )
            packet = runs.load_review_packet(
                task_id, int(latest["attempt"]), review_kind
            )
            current_policy_digest = contract_digest(
                load_policy(
                    Path(str(binding["worktree"]))
                    / ".codex"
                    / "project-policy.toml"
                )
            )
            expected_lease = self._local_review_lease_binding(
                task_id=task_id,
                worktree=str(binding["worktree"]),
                branch=str(binding["branch"]),
                session_id=str(binding["session_id"]),
                policy_digest=str(binding["policy_digest"]),
                scope_paths=[str(path) for path in binding["paths"]],
            )
        except (KeyError, OSError, TypeError, ValueError):
            return False
        handoff = state.get("evidence", {}).get("review_handoff")
        scope_paths = tuple(str(path) for path in plan.get("scope_paths", ()))
        changed_paths = tuple(str(path) for path in latest.get("changed_paths", ()))
        if (
            final.get("prior_state") != "verifying"
            or not isinstance(final.get("prior_generation"), int)
            or isinstance(final.get("prior_generation"), bool)
            or state.get("state") != "finalizing_local_review_revision"
            or state.get("resume_forbidden") is not True
            or state.get("generation") != final.get("prior_generation")
            or state.get("branch") != plan.get("branch")
            or state.get("run_plan_digest") != plan.get("plan_digest")
            or state.get("active_run_revision_digest")
            != parent.get("revision_digest")
            or final.get("parent_revision_digest")
            != parent.get("revision_digest")
            or validate_run_revision(parent)
            or validate_run_revision(revision)
            or active not in (parent, revision)
            or revision.get("task_id") != task_id
            or revision.get("task_digest") != plan.get("task_digest")
            or revision.get("run_plan_digest") != plan.get("plan_digest")
            or revision.get("repository") != plan.get("repository")
            or revision.get("branch") != plan.get("branch")
            or tuple(revision.get("scope_paths", ())) != scope_paths
            or revision.get("parent_revision_digest")
            != parent.get("revision_digest")
            or revision.get("head") != parent.get("head")
            or revision.get("first_attempt")
            != int(latest.get("attempt", 0)) + 1
            or revision.get("source_attempt_digest")
            != latest.get("attempt_digest")
            or revision.get("source_review_receipt_digest")
            != final.get("review_receipt_digest")
            or revision.get("source_diff_digest") != artifact.get("diff_digest")
            or latest.get("status") != "PASS"
            or latest.get("run_revision_digest")
            != parent.get("revision_digest")
            or latest.get("attempt_digest")
            != revision.get("source_attempt_digest")
            or latest.get("head") != parent.get("head")
            or artifact.get("task_id") != task_id
            or artifact.get("attempt") != latest.get("attempt")
            or artifact.get("repository") != plan.get("repository")
            or artifact.get("reviewed_head") != parent.get("head")
            or tuple(artifact.get("scope_paths", ())) != changed_paths
            or receipt.get("receipt_digest")
            != final.get("review_receipt_digest")
            or validate_independent_review_receipt(receipt)
            or receipt.get("status") != "FAIL"
            or int(receipt.get("critical", 0))
            + int(receipt.get("important", 0))
            <= 0
            or receipt.get("review_kind") != review_kind
            or receipt.get("task_id") != task_id
            or receipt.get("task_digest") != plan.get("task_digest")
            or receipt.get("run_plan_digest") != plan.get("plan_digest")
            or receipt.get("run_revision_digest")
            != parent.get("revision_digest")
            or receipt.get("attempt") != latest.get("attempt")
            or receipt.get("attempt_digest") != latest.get("attempt_digest")
            or receipt.get("review_packet_digest")
            != packet.get("packet_digest")
            or receipt.get("base_head") != artifact.get("base_head")
            or receipt.get("artifact_digest")
            != artifact.get("artifact_digest")
            or receipt.get("diff_digest") != artifact.get("diff_digest")
            or receipt.get("scope_paths_digest")
            != contract_digest({"scope_paths": list(changed_paths)})
            or validate_review_packet(packet)
            or packet.get("task_id") != task_id
            or packet.get("task_digest") != plan.get("task_digest")
            or packet.get("run_plan_digest") != plan.get("plan_digest")
            or packet.get("run_revision_digest")
            != parent.get("revision_digest")
            or packet.get("attempt") != latest.get("attempt")
            or packet.get("attempt_digest") != latest.get("attempt_digest")
            or packet.get("repository") != plan.get("repository")
            or packet.get("base_head") != artifact.get("base_head")
            or packet.get("branch") != plan.get("branch")
            or packet.get("reviewed_head") != parent.get("head")
            or packet.get("review_kind") != review_kind
            or packet.get("artifact_digest")
            != artifact.get("artifact_digest")
            or packet.get("diff_digest") != artifact.get("diff_digest")
            or tuple(packet.get("scope_paths", ())) != changed_paths
            or handoff
            != {
                "revision_digest": parent.get("revision_digest"),
                "attempt_digest": latest.get("attempt_digest"),
                "artifact_digest": artifact.get("artifact_digest"),
            }
            or expected_lease != binding
            or binding.get("worktree") != plan.get("repository")
            or binding.get("branch") != plan.get("branch")
            or tuple(binding.get("paths", ())) != tuple(sorted(set(scope_paths)))
            or binding.get("policy_digest") != current_policy_digest
        ):
            return False
        return True


    def _metrics_dir(self, task_id: str) -> Path:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        return (
            self.state_dir
            / "codex-control-plane"
            / "metrics"
            / task_id
        )

    def record_context_metrics(
        self,
        task_id: str,
        *,
        task_digest: str,
        session_id: str,
        invocation_id: str,
        subject_digest: str,
        runtime_metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Record deduplicated local runtime metrics under the task flock."""

        metric_keys = {
            "router_manifest_bytes",
            "novice_brief_bytes",
            "hook_output_bytes",
            "context_units_selected",
        }
        if (
            not validate_task_id(task_id)
            or SHA256_DIGEST.fullmatch(task_digest) is None
            or not validate_task_id(session_id)
            or not validate_task_id(invocation_id)
            or SHA256_DIGEST.fullmatch(subject_digest) is None
            or not isinstance(runtime_metrics, Mapping)
            or not set(runtime_metrics).issubset(metric_keys | {"tool_use_id"})
        ):
            raise ValueError("M_METRIC_BINDING: metric identity is invalid")
        tool_use_id = runtime_metrics.get("tool_use_id")
        if tool_use_id is not None and not validate_task_id(tool_use_id):
            raise ValueError("M_METRIC_BINDING: runtime identity is invalid")
        for metric in metric_keys.intersection(runtime_metrics):
            value = runtime_metrics[metric]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError("M_METRIC_BINDING: runtime metric is invalid")

        observations: list[dict[str, Any]] = []
        for metric in sorted(metric_keys.intersection(runtime_metrics)):
            observation = {
                "schema_version": 1,
                "source": "runtime",
                "metric": metric,
                "task_digest": task_digest,
                "session_id": session_id,
                "invocation_id": invocation_id,
                "subject_digest": subject_digest,
                "tool_use_id": tool_use_id,
                "value": runtime_metrics[metric],
            }
            observation["observation_digest"] = contract_digest(observation)
            observations.append(observation)

        with _task_guard(self.state_dir, task_id):
            directory = self._metrics_dir(task_id)
            directory.mkdir(parents=True, exist_ok=True)
            for observation in observations:
                identity = contract_digest(
                    {
                        "source": "runtime",
                        "invocation_id": invocation_id,
                        "tool_use_id": tool_use_id,
                        "metric": observation["metric"],
                        "subject_digest": subject_digest,
                    }
                ).removeprefix("sha256:")
                path = directory / f"runtime-{identity}.json"
                if path.exists():
                    if path.is_symlink():
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: metric path is unsafe"
                        )
                    try:
                        existing = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as error:
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: metric record is invalid"
                        ) from error
                    if existing != observation:
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: observation identity changed"
                        )
                    continue
                _atomic_json(path, observation)
        return self.context_metrics(task_id)

    def context_metrics(self, task_id: str) -> dict[str, Any]:
        """Aggregate only observed local runtime metrics."""

        observation_keys = {
            "schema_version",
            "source",
            "metric",
            "task_digest",
            "session_id",
            "invocation_id",
            "subject_digest",
            "tool_use_id",
            "value",
            "observation_digest",
        }
        runtime_metric_keys = {
            "router_manifest_bytes",
            "novice_brief_bytes",
            "hook_output_bytes",
            "context_units_selected",
        }
        with _task_guard(self.state_dir, task_id):
            directory = self._metrics_dir(task_id)
            observations: list[dict[str, Any]] = []
            if directory.exists():
                if directory.is_symlink():
                    raise ValueError(
                        "M_METRIC_REPLAY_CONFLICT: metric directory is unsafe"
                    )
                paths = sorted(directory.glob("*.json"))
                if len(paths) > 10000:
                    raise ValueError(
                        "M_METRIC_REPLAY_CONFLICT: metric record cap exceeded"
                    )
                for path in paths:
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: metric record is unsafe"
                        )
                    try:
                        observation = json.loads(
                            path.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError) as error:
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: metric record is invalid"
                        ) from error
                    semantic = {
                        key: value
                        for key, value in observation.items()
                        if key != "observation_digest"
                    }
                    if (
                        set(observation) != observation_keys
                        or observation.get("schema_version") != 1
                        or observation.get("source") != "runtime"
                        or observation.get("metric") not in runtime_metric_keys
                        or not isinstance(observation.get("value"), int)
                        or isinstance(observation.get("value"), bool)
                        or int(observation["value"]) < 0
                        or SHA256_DIGEST.fullmatch(
                            str(observation.get("task_digest"))
                        )
                        is None
                        or not validate_task_id(observation.get("session_id"))
                        or not validate_task_id(observation.get("invocation_id"))
                        or SHA256_DIGEST.fullmatch(
                            str(observation.get("subject_digest"))
                        )
                        is None
                        or (
                            observation.get("tool_use_id") is not None
                            and not validate_task_id(
                                observation.get("tool_use_id")
                            )
                        )
                        or observation.get("observation_digest")
                        != contract_digest(semantic)
                    ):
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: metric schema mismatch"
                        )
                    observations.append(observation)

        def numeric(metric: str) -> list[int]:
            return [
                int(item["value"])
                for item in observations
                if item.get("metric") == metric
            ]

        def per_invocation(metric: str) -> list[int]:
            grouped: dict[str, int] = {}
            for item in observations:
                if item.get("metric") != metric:
                    continue
                invocation = str(item["invocation_id"])
                value = int(item["value"])
                if invocation in grouped and grouped[invocation] != value:
                    raise ValueError(
                        "M_METRIC_REPLAY_CONFLICT: "
                        "per-invocation metric changed across tools"
                    )
                grouped[invocation] = value
            return [grouped[key] for key in sorted(grouped)]

        def total_and_max(metric: str) -> tuple[int, int]:
            values = numeric(metric)
            return sum(values), max(values, default=0)

        router_total, router_max = total_and_max("router_manifest_bytes")
        brief_total, brief_max = total_and_max("novice_brief_bytes")
        hook_total, hook_max = total_and_max("hook_output_bytes")
        units = per_invocation("context_units_selected")
        invocations = {str(item["invocation_id"]) for item in observations}
        hook_invocations = {
            str(item["tool_use_id"])
            for item in observations
            if item.get("tool_use_id") is not None
        }
        return {
            "schema_version": 1,
            "task_id": task_id,
            "metrics_status": "local",
            "router_manifest_bytes_total": router_total,
            "router_manifest_bytes_max": router_max,
            "novice_brief_bytes_total": brief_total,
            "novice_brief_bytes_max": brief_max,
            "hook_output_bytes_total": hook_total,
            "hook_output_bytes_max": hook_max,
            "context_units_selected_total": sum(units),
            "context_units_selected_max": max(units, default=0),
            "invocation_count_unique": len(invocations),
            "hook_invocation_count_unique": len(hook_invocations),
        }

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
        if target == "pr_ready":
            raise ValueError(
                "E_PR_READINESS_PROOF: use publish_pull_request_readiness"
            )
        if state.get("verification_profile") is not None and target in {
            "review_ready",
            "blocked",
        }:
            raise ValueError(
                "E_VERIFICATION_EVIDENCE: verifier completion and failure "
                "require specialized APIs"
            )
        if target == "review_ready":
            from control_plane.run_workflow import RunStore

            if state.get("run_plan_digest") is not None:
                try:
                    run_plan = RunStore(self.state_dir).load_plan(task_id)
                except ValueError as error:
                    raise ValueError(
                        "E_INDEPENDENT_REVIEW: run plan is unavailable"
                    ) from error
                if (
                    run_plan.get("plan_digest")
                    != state.get("run_plan_digest")
                    or run_plan.get("tier") in {"T2", "T3"}
                ):
                    raise ValueError(
                        "E_INDEPENDENT_REVIEW: T2/T3 promotion requires "
                        "the proven review-ready boundary"
                    )
        if target in {"merged", "base_verified"}:
            raise ValueError(
                "E_INTEGRATION_PROOF: merge and base verification require "
                "the specialized squash-only protocol"
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
        if target not in ORDERED_STATES and target != "blocked":
            raise ValueError(
                f"E_STATE_TRANSITION: {source} -> {target} is illegal"
            )
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

    def _recover_delivery_commit(self, task_id: str) -> dict[str, Any]:
        """Observe and complete a delivery marker without replaying Git writes."""

        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_delivery_marker(state, task_id=task_id)
            try:
                lease_payload = _load_delivery_lease(self.state_dir, task_id)
                lease = DeliveryLease.validate(
                    self.state_dir, task_id=task_id, lease=lease_payload
                )
            except ValueError:
                lease = None
            if lease is not None and lease["lease_digest"] != marker["lease_digest"]:
                return self._block_delivery_recovery(task_id, state)
            if lease is not None and (
                lease.get("base_head") != marker.get("base_head")
                or not _delivery_remote_base_matches(
                    Path(str(lease["worktree"])), str(lease["base_head"])
                )
            ):
                return self._block_delivery_recovery(task_id, state)
            worktree = Path(lease["worktree"]) if lease is not None else Path("")
            phase = marker["phase"]
            if phase == "state_committed" and lease is None:
                try:
                    expected_binding = _canonical_delivery_outcome_binding(
                        self.state_dir,
                        task_id=task_id,
                        state=state,
                        marker=marker,
                        committed_head=str(marker.get("observed_sha", "")),
                    )
                except ValueError:
                    return self._block_delivery_recovery(task_id, state)
                if state.get("outcome_binding") != expected_binding:
                    return self._block_delivery_recovery(task_id, state)
                tombstone_path = self.state_dir / "codex-control-plane" / "delivery-lease-tombstones" / f"{task_id}.json"
                try:
                    tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return self._block_delivery_recovery(task_id, state)
                if tombstone.get("lease_digest") != marker["lease_digest"]:
                    return self._block_delivery_recovery(task_id, state)
                state["finalizing_delivery_commit"] = self._advance_delivery_marker(
                    marker, "lease_released", release_digest=contract_digest(tombstone)
                )
                state["resume_forbidden"] = False
                state["updated_at"] = _utc_now()
                _atomic_json(self._path(task_id), state)
                return state
            if phase == "prepared":
                try:
                    staged = (
                        _delivery_index_tree(worktree) == marker["expected_index_tree"]
                        and _delivery_index_paths(worktree) == tuple(marker["allowlist"])
                    )
                except ValueError:
                    staged = False
                if not staged:
                    return self._block_delivery_recovery(task_id, state)
                marker = self._advance_delivery_marker(marker, "index_observed")
                state["finalizing_delivery_commit"] = marker
                _atomic_json(self._path(task_id), state)
                phase = "index_observed"
            if phase == "index_observed":
                try:
                    observed_sha = _delivery_git_text(worktree, ("rev-parse", "HEAD"))
                    observed_branch = _delivery_git_text(worktree, ("branch", "--show-current"))
                except ValueError:
                    observed_sha = ""
                    observed_branch = ""
                if (
                    observed_branch != lease.get("branch")
                    or not _delivery_commit_matches(worktree, marker, observed_sha)
                    or not _delivery_worktree_clean(worktree)
                ):
                    return self._block_delivery_recovery(task_id, state)
                marker = self._advance_delivery_marker(
                    marker, "git_committed", observed_sha=observed_sha
                )
                state["finalizing_delivery_commit"] = marker
                _atomic_json(self._path(task_id), state)
                phase = "git_committed"
            if phase == "git_committed":
                observed_sha = str(marker.get("observed_sha", ""))
                try:
                    observed_branch = _delivery_git_text(worktree, ("branch", "--show-current"))
                    observed_head = _delivery_git_text(worktree, ("rev-parse", "HEAD"))
                except ValueError:
                    observed_branch = observed_head = ""
                if (
                    observed_branch != lease.get("branch")
                    or observed_head != observed_sha
                    or not _delivery_commit_matches(worktree, marker, observed_sha)
                    or not _delivery_worktree_clean(worktree)
                ):
                    return self._block_delivery_recovery(task_id, state)
                try:
                    outcome_binding = _canonical_delivery_outcome_binding(
                        self.state_dir,
                        task_id=task_id,
                        state=state,
                        marker=marker,
                        committed_head=observed_sha,
                    )
                except ValueError:
                    return self._block_delivery_recovery(task_id, state)
                state.update(
                    {
                        "state": "committed",
                        "generation": int(marker["generation"]) + 1,
                        "resume_forbidden": True,
                        "outcome_binding": outcome_binding,
                        "updated_at": _utc_now(),
                    }
                )
                state.setdefault("evidence", {})["committed"] = {"commit": observed_sha}
                marker = self._advance_delivery_marker(marker, "state_committed")
                state["finalizing_delivery_commit"] = marker
                _atomic_json(self._path(task_id), state)
                phase = "state_committed"
            if phase == "lease_released":
                return state
            if phase != "state_committed" or lease is None:
                return self._block_delivery_recovery(task_id, state)
            try:
                expected_binding = _canonical_delivery_outcome_binding(
                    self.state_dir,
                    task_id=task_id,
                    state=state,
                    marker=marker,
                    committed_head=str(marker.get("observed_sha", "")),
                )
            except ValueError:
                return self._block_delivery_recovery(task_id, state)
            if state.get("outcome_binding") != expected_binding:
                return self._block_delivery_recovery(task_id, state)
        released = DeliveryLease.release(
            self.state_dir, task_id=task_id, lease=lease
        )
        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            marker = self._validated_delivery_marker(state, task_id=task_id)
            if marker["phase"] != "state_committed":
                raise ValueError("E_DELIVERY_RECOVERY_UNKNOWN: delivery marker changed")
            state["finalizing_delivery_commit"] = self._advance_delivery_marker(
                marker, "lease_released", release_digest=released["tombstone_digest"]
            )
            state["resume_forbidden"] = False
            state["updated_at"] = _utc_now()
            _atomic_json(self._path(task_id), state)
            return state

    def _block_delivery_recovery(
        self, task_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        state.update(
            {
                "state": "blocked",
                "resume_state": "review_ready",
                "resume_forbidden": True,
                "block_reason": "E_DELIVERY_RECOVERY_UNKNOWN",
                "updated_at": _utc_now(),
            }
        )
        _atomic_json(self._path(task_id), state)
        return state

    @staticmethod
    def _advance_delivery_marker(
        marker: Mapping[str, Any], phase: str, **extra: str
    ) -> dict[str, Any]:
        result = {**marker, "phase": phase, **extra}
        result["marker_digest"] = contract_digest(
            {key: value for key, value in result.items() if key != "marker_digest"}
        )
        return result

    def recover_writer_finalization(self, task_id: str) -> dict[str, Any]:
        """Complete one durable writer finalization without opaque wrappers."""

        if self.status(task_id).get("finalizing_delivery_commit") is not None:
            return self._recover_delivery_commit(task_id)
        if self.status(task_id).get("state") == "finalizing_review_handoff":
            return self._recover_review_handoff(task_id)
        if self.status(task_id).get("state") == "finalizing_local_review_revision":
            return self._recover_local_review_revision(task_id)
        if self.status(task_id).get("state") == "finalizing_review_ready":
            return self._recover_review_ready(task_id)
        if self.status(task_id).get("state") == "finalizing_review_exhausted":
            return self._recover_review_exhausted(task_id)

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
                marker_finalization = copy.deepcopy(finalization)
                marker_generation = int(state.get("generation", -1))
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
                if (
                    current.get("state") != marker_state
                    or int(current.get("generation", -1))
                    != marker_generation
                    or current.get("finalization") != marker_finalization
                ):
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
        if state.get("state") != "pr_draft" or state.get("resume_forbidden") is not True:
            raise ValueError("E_REVISION_MARKER: task is not awaiting PR repair")
        marker = self._validated_pull_request_revision_required(
            state, task_id=task_id
        )
        from control_plane.policy import load_policy
        from control_plane.run_workflow import RunStore, build_run_revision

        run_plan = RunStore(self.state_dir).load_plan(task_id)
        canonical_policy = load_policy(
            Path(str(run_plan["repository"])) / ".codex" / "project-policy.toml"
        )
        canonical_policy_digest = contract_digest(canonical_policy)
        canonical_scope = list(run_plan["scope_paths"])
        runs = RunStore(self.state_dir)
        active_revision = runs.load_active(task_id)
        delivery_review = state.get("delivery_review_binding", {})
        attempts = runs.attempts(task_id)
        next_attempt = int(attempts[-1]["attempt"]) + 1 if attempts else int(active_revision["first_attempt"]) + 1
        if next_attempt > 3:
            state.update({"state": "blocked", "resume_state": "pr_draft",
                          "resume_forbidden": True, "block_reason": "E_REVISION_EXHAUSTED",
                          "updated_at": _utc_now()})
            _atomic_json(self._path(task_id), state)
            return state
        if (
            state.get("branch") != current_branch
            or state.get("generation") != expected_generation
            or marker.get("reason") != reason
            or marker.get("policy_digest") != canonical_policy_digest
            or policy_digest != canonical_policy_digest
            or scope_paths != canonical_scope
            or str(Path(worktree).resolve()) != run_plan.get("repository")
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
            or revision_evidence.get("observation_digest")
            != marker.get("marker_digest")
        ):
            raise ValueError(
                "E_REVISION_EVIDENCE: revision does not match current PR"
            )
        try:
            delivery_marker = self._validated_delivery_marker(
                state, task_id=task_id
            )
            if delivery_marker.get("phase") != "lease_released":
                raise ValueError("delivery lease was not released")
            canonical_pushed = _canonical_pushed_outcome_binding(
                self.state_dir,
                task_id=task_id,
                state=state,
                delivery_marker=delivery_marker,
                pushed_head=str(prior_head),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("E_REVISION_LINEAGE: delivery lineage drifted") from error
        if state.get("outcome_binding") != canonical_pushed:
            raise ValueError("E_REVISION_LINEAGE: pushed lineage drifted")
        if not _revision_worktree_is_current(
            Path(str(run_plan["repository"])), current_branch, str(prior_head)
        ):
            raise ValueError("E_REVISION_BINDING: local revision worktree drifted")
        state_path = self._path(task_id)
        try:
            revision = build_run_revision(
                run_plan=run_plan, revision=int(active_revision["revision"]) + 1,
                first_attempt=next_attempt,
                head=prior_head, reason="pull_request_feedback",
                parent_revision_digest=str(active_revision["revision_digest"]),
                source_attempt_digest=str(attempts[-1]["attempt_digest"]),
                source_review_receipt_digest=str(marker["marker_digest"]),
                source_diff_digest=str(delivery_review["diff_digest"]),
            )
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError("E_REVISION_LINEAGE: durable review lineage drifted") from error
        common_dir = _common_git_dir(self.state_dir)
        prior_state = json.loads(json.dumps(state))
        next_state = _canonical_revision_next_state(
            state,
            expected_generation=expected_generation,
            reason=reason,
            marker=marker,
            run_plan=run_plan,
            active_revision=active_revision,
            latest_attempt=attempts[-1],
            delivery_review=delivery_review,
            revision=revision,
            prior_pr=prior_pr,
            prior_head=str(prior_head),
            observation_digest=str(revision_evidence["observation_digest"]),
        )
        finalization_core = {
            "schema_version": 1,
            "kind": "RevisionFinalizationV1",
            "task_id": task_id,
            "task_digest": state["task_digest"],
            "run_plan_digest": run_plan["plan_digest"],
            "policy_digest": canonical_policy_digest,
            "task5a_marker_digest": marker["marker_digest"],
            "latest_attempt_digest": attempts[-1]["attempt_digest"],
            "delivery_review_binding_digest": delivery_review["binding_digest"],
            "prior_state": state["state"],
            "prior_generation": expected_generation,
            "lease": {
                "task_id": task_id,
                "worktree": str(Path(worktree).resolve()),
                "branch": current_branch,
                "session_id": session_id,
                "policy_digest": policy_digest,
            },
            "next_state": next_state,
            "run_revision": revision,
        }
        finalization = {
            **finalization_core,
            "finalization_digest": contract_digest(finalization_core),
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
                        "revision_finalization": finalization,
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
                try:
                    runs.write_review_revision(revision)
                except Exception:
                    revision_path = runs._revision_path(
                        task_id, int(revision["revision"])
                    )
                    if revision_path.exists() or revision_path.is_symlink():
                        try:
                            if runs._read_revision(
                                task_id, int(revision["revision"])
                            ) != revision:
                                raise ValueError("foreign revision")
                        except (OSError, ValueError) as error:
                            raise ValueError(
                                "E_REVISION_RECOVERY_UNKNOWN: revision write is ambiguous"
                            ) from error
                        # The durable write landed before the host fault.  The
                        # finalizing marker and exact lease are now the only
                        # safe recovery input; never roll them back blindly.
                        raise
                    TaskLease._release_locked(
                        token, state_dir=self.state_dir, task_id=task_id,
                        worktree=worktree, branch=current_branch,
                        session_id=session_id, policy_digest=policy_digest,
                        lease_digest=lease["lease_digest"],
                    )
                    _atomic_json(state_path, prior_state)
                    raise
                current.update(
                    {
                        **next_state,
                        "lease_digest": lease["lease_digest"],
                        "updated_at": _utc_now(),
                    }
                )
                for key in (
                    "revision_required", "pull_request_effect_plan",
                    "pr_readiness_receipt_digests",
                    "pull_request_outcome_receipt_digests",
                    "remote_outcome_receipt_digests",
                    "outcome_binding",
                    "delivery_review_binding",
                    "review_attempt_digest",
                    "review_promotion_digest",
                    "review_packet_digest",
                    "review_receipt_digests",
                    "finalizing_delivery_commit",
                    "pull_request",
                ):
                    current.pop(key, None)
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
                    or not isinstance(finalization.get("run_revision"), Mapping)
                ):
                    raise ValueError(
                        "E_REVISION_RECOVERY_UNKNOWN: revision marker is invalid"
                    )
                required_finalization = {
                    "schema_version", "kind", "task_id", "task_digest",
                    "run_plan_digest", "policy_digest", "task5a_marker_digest",
                    "latest_attempt_digest", "delivery_review_binding_digest",
                    "prior_state", "prior_generation", "lease", "next_state",
                    "run_revision", "finalization_digest",
                }
                finalization_core = {
                    key: value
                    for key, value in finalization.items()
                    if key != "finalization_digest"
                }
                marker = state.get("revision_required")
                delivery_review = state.get("delivery_review_binding")
                if (
                    set(finalization) != required_finalization
                    or finalization.get("schema_version") != 1
                    or finalization.get("kind") != "RevisionFinalizationV1"
                    or finalization.get("finalization_digest")
                    != contract_digest(finalization_core)
                    or finalization.get("task_id") != task_id
                    or finalization.get("task_digest") != state.get("task_digest")
                    or finalization.get("run_plan_digest") != state.get("run_plan_digest")
                    or not isinstance(marker, Mapping)
                    or finalization.get("task5a_marker_digest")
                    != marker.get("marker_digest")
                    or not isinstance(delivery_review, Mapping)
                    or finalization.get("delivery_review_binding_digest")
                    != delivery_review.get("binding_digest")
                ):
                    raise ValueError("E_REVISION_RECOVERY_UNKNOWN: revision marker is invalid")
                lease_binding = dict(finalization["lease"])
                revision = dict(finalization["run_revision"])
                from control_plane.run_workflow import (
                    RunStore,
                    build_run_revision,
                    validate_run_revision,
                )
                from control_plane.policy import load_policy
                if validate_run_revision(revision):
                    raise ValueError("E_REVISION_RECOVERY_UNKNOWN: revision is invalid")
                runs = RunStore(self.state_dir)
                try:
                    plan = runs.load_plan(task_id)
                    attempts = runs.attempts(task_id)
                    task5a_marker = self._validated_pull_request_revision_required(
                        state, task_id=task_id
                    )
                    delivery_marker = self._validated_delivery_marker(
                        state, task_id=task_id
                    )
                    if delivery_marker.get("phase") != "lease_released":
                        raise ValueError("delivery lease was not released")
                    canonical_policy = contract_digest(load_policy(
                        Path(str(plan["repository"])) / ".codex" / "project-policy.toml"
                    ))
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("E_REVISION_RECOVERY_UNKNOWN: revision lineage is unavailable") from error
                prior_pr = state.get("evidence", {}).get("pr_draft", {}).get("pull_request")
                prior_head = state.get("evidence", {}).get("pushed", {}).get("remote_head")
                if (
                    not attempts
                    or finalization.get("run_plan_digest") != plan.get("plan_digest")
                    or finalization.get("policy_digest") != canonical_policy
                    or finalization.get("latest_attempt_digest")
                    != attempts[-1].get("attempt_digest")
                    or revision.get("run_plan_digest") != plan.get("plan_digest")
                    or revision.get("source_attempt_digest")
                    != attempts[-1].get("attempt_digest")
                    or revision.get("source_review_receipt_digest")
                    != marker.get("marker_digest")
                    or revision.get("source_diff_digest")
                    != delivery_review.get("diff_digest")
                    or task5a_marker != marker
                    or not isinstance(prior_pr, Mapping)
                    or prior_pr.get("head_commit") != prior_head
                ):
                    raise ValueError("E_REVISION_RECOVERY_UNKNOWN: revision lineage drifted")
                try:
                    active_revision = runs.load_revision(
                        task_id, str(revision["parent_revision_digest"])
                    )
                    expected_revision = build_run_revision(
                        run_plan=plan,
                        revision=int(active_revision["revision"]) + 1,
                        first_attempt=int(attempts[-1]["attempt"]) + 1,
                        head=str(prior_head),
                        reason="pull_request_feedback",
                        parent_revision_digest=str(active_revision["revision_digest"]),
                        source_attempt_digest=str(attempts[-1]["attempt_digest"]),
                        source_review_receipt_digest=str(task5a_marker["marker_digest"]),
                        source_diff_digest=str(delivery_review["diff_digest"]),
                    )
                    if revision != expected_revision:
                        raise ValueError("run revision drifted")
                    canonical_pushed = _canonical_pushed_outcome_binding(
                        self.state_dir,
                        task_id=task_id,
                        state=state,
                        delivery_marker=delivery_marker,
                        pushed_head=str(prior_head),
                    )
                    expected_next_state = _canonical_revision_next_state(
                        state,
                        expected_generation=int(finalization["prior_generation"]),
                        reason=str(task5a_marker["reason"]),
                        marker=task5a_marker,
                        run_plan=plan,
                        active_revision=active_revision,
                        latest_attempt=attempts[-1],
                        delivery_review=delivery_review,
                        revision=expected_revision,
                        prior_pr=prior_pr,
                        prior_head=str(prior_head),
                        observation_digest=str(task5a_marker["marker_digest"]),
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("E_REVISION_RECOVERY_UNKNOWN: revision successor is unavailable") from error
                if (
                    state.get("outcome_binding") != canonical_pushed
                    or not _revision_worktree_is_current(
                        Path(str(plan["repository"])), str(state.get("branch")), str(prior_head)
                    )
                    or finalization.get("next_state") != expected_next_state
                ):
                    raise ValueError("E_REVISION_RECOVERY_UNKNOWN: revision successor drifted")
                lease = self._read_owner_lease(task_id)
                if lease is None:
                    orphan_path = runs._revision_path(task_id, int(revision["revision"]))
                    if orphan_path.exists() or orphan_path.is_symlink():
                        try:
                            runs.delete_review_revision_exact(revision)
                        except ValueError as error:
                            raise ValueError(
                                "E_REVISION_RECOVERY_UNKNOWN: revision orphan is inconsistent"
                            ) from error
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
                        "E_REVISION_RECOVERY_UNKNOWN: revision lease is inconsistent"
                    )
                try:
                    runs.write_review_revision(revision)
                except ValueError as error:
                    raise ValueError(
                        "E_REVISION_RECOVERY_UNKNOWN: revision is inconsistent"
                    ) from error
                state.update(expected_next_state)
                state["lease_digest"] = lease.get("lease_digest")
                state["updated_at"] = _utc_now()
                for key in (
                    "prior_state",
                    "prior_generation",
                    "revision_finalization",
                    "revision_reason",
                ):
                    state.pop(key, None)
                for key in (
                    "revision_required", "pull_request_effect_plan",
                    "pr_readiness_receipt_digests",
                    "pull_request_outcome_receipt_digests",
                    "remote_outcome_receipt_digests",
                    "outcome_binding",
                    "delivery_review_binding",
                    "review_attempt_digest",
                    "review_promotion_digest",
                    "review_packet_digest",
                    "review_receipt_digests",
                    "finalizing_delivery_commit",
                    "pull_request",
                ):
                    state.pop(key, None)
                _atomic_json(state_path, state)
                return state


def _delivery_git_text(
    worktree: Path,
    arguments: tuple[str, ...],
    *,
    index_file: Path | str | None = None,
) -> str:
    try:
        return _git_observation_text(
            worktree, arguments, index_file=index_file
        )
    except ValueError as error:
        raise ValueError(
            "E_DELIVERY_RECOVERY_UNKNOWN: Git observation failed"
        ) from error


def _observe_remote_write_bindings(
    effect_plan: OutcomeEffectPlanV1,
) -> dict[str, str]:
    """Read every mutable Git/policy binding used immediately before push."""

    try:
        worktree = Path(effect_plan.repository).resolve(strict=True)
        branch = _delivery_git_text(worktree, ("branch", "--show-current"))
        head = _delivery_git_text(worktree, ("rev-parse", "HEAD"))
        branch_head = _delivery_git_text(
            worktree, ("rev-parse", f"refs/heads/{effect_plan.branch}")
        )
        cached_base = _delivery_git_text(
            worktree,
            (
                "rev-parse",
                f"refs/remotes/{effect_plan.remote}/{effect_plan.base}",
            ),
        )
        remote_base_raw = _delivery_git_text(
            worktree,
            (
                "ls-remote",
                "--heads",
                effect_plan.remote_url,
                f"refs/heads/{effect_plan.base}",
            ),
        )
        remote_url, remote_url_digest, remote_identity_digest = (
            _outcome_remote_url_and_identity(worktree, effect_plan.remote)
        )
    except (OSError, ValueError) as error:
        raise ValueError(
            "E_REMOTE_WRITE_PREPARE: live Git bindings are unavailable"
        ) from error
    remote_fields = remote_base_raw.split()
    if (
        str(worktree) != effect_plan.repository
        or branch != effect_plan.branch
        or head != effect_plan.head_sha
        or branch_head != effect_plan.head_sha
        or not _delivery_worktree_clean(worktree)
        or len(remote_fields) != 2
        or remote_fields[0] != cached_base
        or remote_fields[1] != f"refs/heads/{effect_plan.base}"
        or remote_url != effect_plan.remote_url
        or remote_url_digest != effect_plan.remote_url_digest
        or remote_identity_digest != effect_plan.remote_identity_digest
    ):
        raise ValueError("E_REMOTE_WRITE_PREPARE: live Git binding drifted")
    return {
        "base_head": cached_base,
        "remote_url_digest": remote_url_digest,
        "remote_identity_digest": remote_identity_digest,
    }


def _observe_pull_request_bindings(
    effect_plan: OutcomeEffectPlanV1,
) -> dict[str, str]:
    """Reobserve the pushed feature ref plus every mutable local binding."""

    live = _observe_remote_write_bindings(effect_plan)
    try:
        remote_head_raw = _delivery_git_text(
            Path(effect_plan.repository),
            (
                "ls-remote",
                "--heads",
                effect_plan.remote_url,
                f"refs/heads/{effect_plan.branch}",
            ),
        )
    except ValueError as error:
        raise ValueError(
            "E_PULL_REQUEST_PREPARE: pushed ref is unavailable"
        ) from error
    fields = remote_head_raw.split()
    if fields != [effect_plan.head_sha, f"refs/heads/{effect_plan.branch}"]:
        raise ValueError("E_PULL_REQUEST_PREPARE: pushed ref drifted")
    return {**live, "feature_head": effect_plan.head_sha}


def _canonical_remote_write_inputs(
    state_dir: Path,
    *,
    task_id: str,
    effect_plan: OutcomeEffectPlanV1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the plan and policy from their canonical worktree locations."""

    from control_plane.policy import PolicyError, load_policy, validate_policy
    from control_plane.run_workflow import RunStore, validate_run_plan

    try:
        repository = Path(effect_plan.repository).resolve(strict=True)
        canonical_state_dir = worktree_git_dir(repository).resolve(strict=True)
        if canonical_state_dir != state_dir.resolve(strict=True):
            raise ValueError("state directory differs from worktree Git dir")
        run_store = RunStore(canonical_state_dir)
        run_plan = run_store.load_plan(task_id)
        plan_path = run_store._plan_path(task_id)
        canonical_plan_bytes = (
            json.dumps(run_plan, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if plan_path.is_symlink() or plan_path.read_bytes() != canonical_plan_bytes:
            raise ValueError("persisted RunPlan bytes drifted")
        policy_path = repository / ".codex" / "project-policy.toml"
        if policy_path.is_symlink() or not policy_path.is_file():
            raise ValueError("canonical policy path is unsafe")
        policy = load_policy(policy_path)
    except (OSError, ValueError, PolicyError) as error:
        # PolicyError is intentionally collapsed into the stable bridge code.
        raise ValueError(
            "E_REMOTE_WRITE_PREPARE: canonical plan or policy is unavailable"
        ) from error
    policy_git = policy.get("git", {})
    if (
        validate_run_plan(run_plan)
        or validate_policy(policy)
        or run_plan.get("task_id") != task_id
        or run_plan.get("plan_digest") != effect_plan.run_plan_digest
        or run_plan.get("task_digest") != effect_plan.task_digest
        or run_plan.get("requested_outcome") != effect_plan.requested_outcome
        or run_plan.get("repository") != effect_plan.repository
        or run_plan.get("branch") != effect_plan.branch
        or contract_digest(policy) != effect_plan.policy_digest
        or policy_git.get("remote") != effect_plan.remote
        or policy_git.get("base_branch") != effect_plan.base
        or effect_plan.branch == policy_git.get("base_branch")
        or policy_git.get("allow_direct_base_push") is not False
    ):
        raise ValueError(
            "E_REMOTE_WRITE_PREPARE: canonical plan or policy binding drifted"
        )
    return dict(run_plan), dict(policy)


def _delivery_reviewed_tree_digest(expected_tree: str) -> str:
    """Bind OutcomeBinding's tree digest to the exact committed Git tree."""

    if GIT_OBJECT_ID.fullmatch(expected_tree) is None:
        raise ValueError("E_DELIVERY_COMMIT: expected tree is invalid")
    return contract_digest({"git_tree_oid": expected_tree})


def _canonical_delivery_outcome_binding(
    state_dir: Path,
    *,
    task_id: str,
    state: Mapping[str, Any],
    marker: Mapping[str, Any],
    committed_head: str,
) -> dict[str, Any]:
    """Derive committed lineage only from durable review and delivery proof."""

    from control_plane.run_workflow import (
        RunStore,
        advance_outcome_binding,
        build_outcome_binding,
        validate_run_plan,
    )

    review = state.get("delivery_review_binding")
    required_review = {
        "schema_version",
        "kind",
        "run_plan_digest",
        "run_revision_digest",
        "attempt_digest",
        "promotion_digest",
        "base_head",
        "reviewed_head",
        "diff_digest",
        "untracked_modes",
        "scope_paths",
        "receipt_digests",
        "authorizes",
        "binding_digest",
    }
    try:
        plan = RunStore(state_dir).load_plan(task_id)
    except ValueError as error:
        raise ValueError("E_DELIVERY_COMMIT: durable RunPlan is unavailable") from error
    if (
        validate_run_plan(plan)
        or not isinstance(review, Mapping)
        or set(review) != required_review
        or review.get("schema_version") != 1
        or review.get("kind") != "DeliveryReviewBindingV1"
        or review.get("authorizes") is not False
        or review.get("binding_digest")
        != contract_digest(
            {key: value for key, value in review.items() if key != "binding_digest"}
        )
        or not isinstance(review.get("scope_paths"), list)
        or not isinstance(marker.get("allowlist"), list)
        or plan.get("task_id") != task_id
        or plan.get("task_digest") != state.get("task_digest")
        or plan.get("plan_digest") != state.get("run_plan_digest")
        or plan.get("plan_digest") != review.get("run_plan_digest")
        or plan.get("requested_outcome") != state.get("outcome")
        or plan.get("repository") != str(Path(plan.get("repository", "")).resolve())
        or plan.get("branch") != state.get("branch")
        or review.get("reviewed_head") != marker.get("parent_head")
        or review.get("binding_digest") != marker.get("snapshot_digest")
        or review.get("scope_paths") != plan.get("scope_paths")
        or review.get("scope_paths") != marker.get("allowlist")
        or marker.get("expected_index_tree") != marker.get("expected_tree")
        or GIT_OBJECT_ID.fullmatch(str(committed_head)) is None
        or committed_head == marker.get("parent_head")
        or marker.get("observed_sha") not in {None, committed_head}
    ):
        raise ValueError("E_DELIVERY_COMMIT: durable lineage drifted")
    tree_digest = _delivery_reviewed_tree_digest(str(marker["expected_tree"]))
    binding = build_outcome_binding(
        run_plan=plan,
        review_head=str(review["reviewed_head"]),
        reviewed_tree_digest=tree_digest,
        reviewed_diff_digest=str(review["diff_digest"]),
    )
    binding = advance_outcome_binding(
        binding,
        effect_id="local_write",
        observation={
            "head": str(review["reviewed_head"]),
            "tree_digest": tree_digest,
            "diff_digest": str(review["diff_digest"]),
        },
    )
    return advance_outcome_binding(
        binding,
        effect_id="commit",
        observation={
            "parent_head": str(marker["parent_head"]),
            "tree_digest": tree_digest,
            "committed_head": committed_head,
        },
    )


def _canonical_pushed_outcome_binding(
    state_dir: Path,
    *,
    task_id: str,
    state: Mapping[str, Any],
    delivery_marker: Mapping[str, Any],
    pushed_head: str,
) -> dict[str, Any]:
    """Reconstruct pushed lineage from delivery proof and remote evidence."""

    from control_plane.run_workflow import advance_outcome_binding

    committed = _canonical_delivery_outcome_binding(
        state_dir,
        task_id=task_id,
        state=state,
        marker=delivery_marker,
        committed_head=pushed_head,
    )
    return advance_outcome_binding(
        committed,
        effect_id="remote_write",
        observation={"pushed_head": pushed_head},
    )


def _delivery_remote_base_matches(worktree: Path, base_head: str) -> bool:
    """Observe the cached policy remote base without fetching or mutating it."""

    try:
        from control_plane.policy import load_policy

        policy = load_policy(worktree / ".codex" / "project-policy.toml")
        observed = _delivery_git_text(
            worktree,
            ("rev-parse", f"refs/remotes/{policy['git']['remote']}/{policy['git']['base_branch']}"),
        )
    except (OSError, ValueError):
        return False
    return observed == base_head


def _delivery_worktree_clean(worktree: Path) -> bool:
    try:
        assert_no_external_git_filters(worktree)
        return not _git_observation_text(
            worktree, ("status", "--porcelain=v1", "-z")
        )
    except ValueError:
        return False


def _revision_worktree_is_current(
    worktree: Path, branch: str, head: str
) -> bool:
    """Reobserve the exact clean PR subject before state publication."""

    try:
        return (
            _delivery_git_text(worktree, ("branch", "--show-current")) == branch
            and _delivery_git_text(worktree, ("rev-parse", "HEAD")) == head
            and _delivery_worktree_clean(worktree)
        )
    except (OSError, ValueError):
        return False


def _canonical_revision_next_state(
    state: Mapping[str, Any],
    *,
    expected_generation: int,
    reason: str,
    marker: Mapping[str, Any],
    run_plan: Mapping[str, Any],
    active_revision: Mapping[str, Any],
    latest_attempt: Mapping[str, Any],
    delivery_review: Mapping[str, Any],
    revision: Mapping[str, Any],
    prior_pr: Mapping[str, Any],
    prior_head: str,
    observation_digest: str,
) -> dict[str, Any]:
    """Derive the sole recoverable PR-repair successor from durable inputs."""

    history = state.get("pull_request_history", [])
    if not isinstance(history, list) or any(not isinstance(item, Mapping) for item in history):
        raise ValueError("E_REVISION_LINEAGE: pull-request history is invalid")
    return {
        "state": "implementing",
        "resume_state": None,
        "resume_forbidden": False,
        "block_reason": None,
        "generation": expected_generation + 1,
        "revision": int(state.get("revision", 0)) + 1,
        "revision_reason": reason,
        "pull_request_history": [
            *[dict(item) for item in history],
            {
                "revision": int(state.get("revision", 0)),
                "attempt": int(latest_attempt["attempt"]),
                "number": prior_pr["number"],
                "head": prior_head,
                "reason": reason,
                "observation_digest": observation_digest,
                "marker_digest": marker["marker_digest"],
                "receipt_digests": list(marker["receipt_digests"]),
                "source_attempt_digest": latest_attempt["attempt_digest"],
                "source_revision_digest": active_revision["revision_digest"],
                "source_diff_digest": delivery_review["diff_digest"],
            },
        ],
        "active_run_revision_digest": revision["revision_digest"],
        "run_plan_digest": run_plan["plan_digest"],
        "pr_revision_marker_digest": marker["marker_digest"],
        "evidence": {},
    }


def _delivery_index_tree(
    worktree: Path, *, index_file: Path | str | None = None
) -> str:
    tree = _delivery_git_text(
        worktree, ("write-tree",), index_file=index_file
    )
    if GIT_OBJECT_ID.fullmatch(tree) is None:
        raise ValueError("E_DELIVERY_INDEX: index tree is invalid")
    return tree


def _delivery_index_paths(
    worktree: Path, *, index_file: Path | str | None = None
) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            trusted_git_argv(
                worktree,
                ("diff", "--cached", "--name-only", "-z"),
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            env=trusted_git_environment(index_file=index_file),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "E_DELIVERY_INDEX: staged paths are unavailable"
        ) from error
    if completed.returncode != 0:
        raise ValueError("E_DELIVERY_INDEX: staged paths are unavailable")
    try:
        paths = tuple(sorted(item for item in completed.stdout.decode("utf-8").split("\0") if item))
    except UnicodeDecodeError as error:
        raise ValueError("E_DELIVERY_INDEX: staged paths are not UTF-8") from error
    if not all(_normalize_lease_path(path) == path for path in paths):
        raise ValueError("E_DELIVERY_INDEX: staged path is unsafe")
    return paths


def _delivery_review_diff_matches(
    worktree: Path,
    binding: object,
    allowlist: tuple[str, ...],
    *,
    index_file: Path | str | None = None,
) -> bool:
    """Recompute the staged binary diff when a durable review binding exists."""

    if binding is None:
        return True
    if not isinstance(binding, Mapping):
        return False
    review_head = binding.get("reviewed_head")
    diff_digest = binding.get("diff_digest")
    if (
        GIT_OBJECT_ID.fullmatch(str(review_head)) is None
        or SHA256_DIGEST.fullmatch(str(diff_digest)) is None
        or binding.get("scope_paths") != list(allowlist)
        or not isinstance(binding.get("untracked_modes"), list)
    ):
        return False
    try:
        rendered = _delivery_review_diff(
            worktree,
            str(review_head),
            allowlist,
            index_file=index_file,
        )
    except ValueError:
        return False
    return (
        len(rendered) <= 1_048_576
        and contract_digest({"diff": rendered.hex()}) == diff_digest
        and _delivery_index_untracked_modes(
            worktree, binding["untracked_modes"], index_file=index_file
        )
    )


def _delivery_index_untracked_modes(
    worktree: Path,
    expected: list[object],
    *,
    index_file: Path | str | None = None,
) -> bool:
    for item in expected:
        if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str) or not isinstance(item[1], int):
            return False
        try:
            completed = subprocess.run(
                trusted_git_argv(
                    worktree, ("ls-files", "-s", "--", item[0])
                ),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                env=trusted_git_environment(index_file=index_file),
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return False
        try:
            mode = completed.stdout.decode("utf-8").split(" ", 1)[0]
        except UnicodeDecodeError:
            return False
        if completed.returncode != 0 or mode != f"{0o100000 | item[1]:06o}":
            return False
    return True


def _delivery_review_diff(
    worktree: Path,
    reviewed_head: str,
    allowlist: tuple[str, ...],
    *,
    index_file: Path | str | None = None,
) -> bytes:
    """Render the candidate index with ReviewArtifactStore's byte contract.

    A review artifact renders untracked files through ``diff --no-index``.  By
    delivery time those files have been staged, so obtain their exact index
    blob and re-render it through the same normalizing form instead of trusting
    Git's ordinary cached-add output.
    """

    from control_plane.run_workflow import MAX_REVIEW_DIFF_BYTES, ReviewArtifactStore

    capture = ReviewArtifactStore(worktree)._capture_git
    tracked: list[str] = []
    untracked: list[str] = []
    for path in allowlist:
        try:
            present = subprocess.run(
                trusted_git_argv(
                    worktree,
                    ("ls-tree", "-z", reviewed_head, "--", path),
                ),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                env=trusted_git_environment(index_file=index_file),
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ValueError(
                "E_DELIVERY_INDEX: review tree is unavailable"
            ) from error
        if present.returncode == 0 and present.stdout:
            tracked.append(path)
        elif present.returncode == 0:
            untracked.append(path)
        else:
            raise ValueError("E_DELIVERY_INDEX: review tree is unavailable")
    try:
        rendered = bytearray(capture(
            worktree,
            ("diff", "--cached", "--binary", "--full-index", "--no-ext-diff",
             "--no-renames", "--no-textconv", reviewed_head, "--", *tracked),
            maximum=MAX_REVIEW_DIFF_BYTES,
            index_file=index_file,
        ))
    except ValueError as error:
        raise ValueError("E_DELIVERY_INDEX: staged diff is unavailable") from error
    for path in untracked:
        try:
            blob = capture(
                worktree, ("show", f":{path}"),
                maximum=MAX_REVIEW_DIFF_BYTES - len(rendered),
                index_file=index_file,
            )
        except ValueError as error:
            raise ValueError("E_DELIVERY_INDEX: staged untracked blob is unavailable") from error
        with tempfile.TemporaryDirectory(prefix="codex-delivery-") as temporary:
            candidate = Path(temporary) / "content"
            candidate.write_bytes(blob)
            try:
                piece = capture(
                    worktree,
                    ("diff", "--no-index", "--binary", "--full-index", "--no-ext-diff",
                     "--", "/dev/null", str(candidate)),
                    maximum=MAX_REVIEW_DIFF_BYTES - len(rendered),
                    index_file=index_file,
                )
            except ValueError as error:
                raise ValueError("E_DELIVERY_INDEX: staged untracked diff is unavailable") from error
            rendered.extend(piece.replace(str(candidate).encode("utf-8"), path.encode("utf-8")))
        if len(rendered) > MAX_REVIEW_DIFF_BYTES:
            raise ValueError("E_DELIVERY_INDEX: staged diff exceeds byte cap")
    return bytes(rendered)


def _delivery_commit_matches(
    worktree: Path, marker: Mapping[str, Any], commit: str
) -> bool:
    if GIT_OBJECT_ID.fullmatch(commit) is None:
        return False
    try:
        subject = _delivery_git_text(
            worktree, ("log", "-1", "--format=%s", commit)
        )
        message_digest = f"sha256:{sha256(subject.encode('utf-8')).hexdigest()}"
        return (
            _delivery_git_text(worktree, ("rev-parse", f"{commit}^"))
            == marker["parent_head"]
            and _delivery_git_text(worktree, ("rev-parse", f"{commit}^{{tree}}"))
            == marker["expected_tree"]
            and message_digest == marker["message_digest"]
        )
    except ValueError:
        return False


class DeliveryLease:
    """Owner-bound delivery lease, distinct from the implementation lease."""

    @staticmethod
    def _path(state_dir: Path, task_id: str) -> Path:
        return state_dir / "codex-control-plane" / "delivery-leases" / f"{task_id}.json"

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
        generation: int,
        review_head: str,
        base_head: str,
        diff_digest: str,
        _lease_lock_token: LeaseLockToken | None = None,
    ) -> dict[str, Any]:
        normalized = [_normalize_lease_path(path) for path in paths]
        if (
            not validate_task_id(task_id)
            or not _valid_branch(branch)
            or not validate_task_id(session_id)
            or not paths
            or any(path is None for path in normalized)
            or not isinstance(generation, int)
            or generation < 0
            or SHA256_DIGEST.fullmatch(policy_digest) is None
            or SHA256_DIGEST.fullmatch(diff_digest) is None
            or GIT_OBJECT_ID.fullmatch(review_head) is None
            or GIT_OBJECT_ID.fullmatch(base_head) is None
        ):
            raise ValueError("E_DELIVERY_LEASE: delivery lease binding is invalid")
        core = {
            "schema_version": 1,
            "kind": "DeliveryLeaseV1",
            "task_id": task_id,
            "worktree": str(Path(worktree).resolve()),
            "branch": branch,
            "session_id": session_id,
            "paths": sorted(set(str(path) for path in normalized)),
            "policy_digest": policy_digest,
            "generation": generation,
            "review_head": review_head,
            "base_head": base_head,
            "diff_digest": diff_digest,
        }
        payload = {**core, "lease_digest": contract_digest(core)}
        common_dir = _common_git_dir(state_dir)
        if _lease_lock_token is not None:
            return DeliveryLease._acquire_locked(
                _lease_lock_token, state_dir=state_dir, payload=payload,
            )
        with _common_lease_lock(common_dir) as token:
            return DeliveryLease._acquire_locked(token, state_dir=state_dir, payload=payload)

    @staticmethod
    def _acquire_locked(
        lease_lock_token: LeaseLockToken, *, state_dir: Path, payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Write after scanning both lease kinds in every registered worktree."""

        common_dir = _common_git_dir(state_dir)
        if not _valid_lease_lock_token(lease_lock_token, common_dir):
            raise ValueError("E_LEASE_LOCK: invalid common-dir lease lock token")
        task_id = str(payload["task_id"])
        requested_paths = [str(item) for item in payload["paths"]]
        git_dirs = [common_dir]
        worktrees = common_dir / "worktrees"
        if worktrees.exists():
            if worktrees.is_symlink() or not worktrees.is_dir():
                raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: worktree registry is unsafe")
            git_dirs.extend(sorted(path for path in worktrees.iterdir() if path.is_dir() and not path.is_symlink()))
        owner_path = DeliveryLease._path(state_dir, task_id)
        for git_dir in git_dirs:
            for directory, kind in (("leases", "implementation"), ("delivery-leases", "delivery")):
                lease_dir = git_dir / "codex-control-plane" / directory
                if lease_dir.is_symlink():
                    raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: lease directory is unsafe")
                if not lease_dir.exists():
                    continue
                for candidate in sorted(lease_dir.glob("*.json")):
                    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 65_536:
                        raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: lease candidate is unsafe")
                    try:
                        other = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as error:
                        raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: lease is unreadable") from error
                    semantic = {key: value for key, value in other.items() if key != "lease_digest"}
                    if (
                        not isinstance(other, Mapping)
                        or other.get("lease_digest") != contract_digest(semantic)
                        or not isinstance(other.get("task_id"), str)
                        or not isinstance(other.get("paths"), list)
                        or any(normalize_scope(item) != item for item in other["paths"])
                    ):
                        raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: lease contract is invalid")
                    if candidate == owner_path and kind == "delivery" and other.get("task_id") == task_id:
                        if other != dict(payload):
                            raise ValueError("E_DELIVERY_LEASE_MISMATCH: delivery lease identity changed")
                        return dict(other)
                    if other.get("task_id") == task_id:
                        raise ValueError("E_DELIVERY_LEASE_CONFLICT: task lease exists in another worktree")
                    if any(scopes_overlap(left, right) for left in requested_paths for right in other["paths"]):
                        raise ValueError("E_DELIVERY_LEASE_CONFLICT: another task owns an overlapping path")
        _atomic_json(owner_path, payload)
        return dict(payload)

    @staticmethod
    def validate(
        state_dir: Path, *, task_id: str, lease: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(lease, Mapping) or not validate_task_id(task_id):
            raise ValueError("E_DELIVERY_LEASE_MISMATCH: delivery lease is invalid")
        path = DeliveryLease._path(state_dir, task_id)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("E_DELIVERY_LEASE_NOT_FOUND: delivery lease is unavailable") from error
        required = {
            "schema_version", "kind", "task_id", "worktree", "branch", "session_id",
            "paths", "policy_digest", "generation", "review_head", "diff_digest", "lease_digest",
            "base_head",
        }
        core = {key: value for key, value in stored.items() if key != "lease_digest"}
        if (
            set(stored) != required
            or stored.get("schema_version") != 1
            or stored.get("kind") != "DeliveryLeaseV1"
            or stored.get("task_id") != task_id
            or stored.get("lease_digest") != contract_digest(core)
            or stored != dict(lease)
        ):
            raise ValueError("E_DELIVERY_LEASE_MISMATCH: delivery lease binding drifted")
        return stored

    @staticmethod
    def release(
        state_dir: Path, *, task_id: str, lease: Mapping[str, Any]
    ) -> dict[str, Any]:
        if (
            not isinstance(lease, Mapping)
            or set(lease) != {
                "schema_version", "kind", "task_id", "worktree", "branch", "session_id",
                "paths", "policy_digest", "generation", "review_head", "diff_digest", "lease_digest",
                "base_head",
            }
            or lease.get("task_id") != task_id
            or not isinstance(lease.get("lease_digest"), str)
            or SHA256_DIGEST.fullmatch(str(lease.get("lease_digest"))) is None
            or lease.get("lease_digest") != contract_digest(
                {key: value for key, value in lease.items() if key != "lease_digest"}
            )
        ):
            raise ValueError("E_DELIVERY_LEASE_MISMATCH: delivery release is invalid")
        common_dir = _common_git_dir(state_dir)
        with _common_lease_lock(common_dir):
            path = DeliveryLease._path(state_dir, task_id)
            tombstone_path = state_dir / "codex-control-plane" / "delivery-lease-tombstones" / f"{task_id}.json"
            if not path.exists():
                try:
                    tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError("E_DELIVERY_LEASE_NOT_FOUND: delivery release is unavailable") from error
                if tombstone.get("lease_digest") != lease["lease_digest"]:
                    raise ValueError("E_DELIVERY_LEASE_MISMATCH: delivery release changed owner")
                return {"released": True, "idempotent": True, "tombstone_digest": contract_digest(tombstone)}
            current = DeliveryLease.validate(state_dir, task_id=task_id, lease=lease)
            tombstone = {
                "schema_version": 1,
                "kind": "DeliveryLeaseTombstoneV1",
                "task_id": task_id,
                "lease_digest": current["lease_digest"],
                "status": "released",
            }
            _atomic_json(tombstone_path, tombstone)
            path.unlink()
            _fsync_directory(path.parent)
            return {"released": True, "idempotent": False, "tombstone_digest": contract_digest(tombstone)}


def _load_delivery_lease(state_dir: Path, task_id: str) -> dict[str, Any]:
    path = DeliveryLease._path(state_dir, task_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("E_DELIVERY_LEASE_NOT_FOUND: delivery lease is unavailable") from error
    if not isinstance(value, dict):
        raise ValueError("E_DELIVERY_LEASE_UNKNOWN: delivery lease is invalid")
    return value


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
        task_path = state_dir / "codex-control-plane" / "tasks" / f"{task_id}.json"
        if task_path.exists():
            try:
                task_state = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: task state is unreadable") from error
            if not isinstance(task_state, Mapping) or task_state.get("task_id") != task_id:
                raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: task state is invalid")
            if task_state.get("state") == "review_ready":
                raise ValueError("E_LEASE_DELIVERY_REQUIRED: review_ready requires a delivery lease")
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
                    and prior_task.get("state") not in {
                        "finalizing_revision", "finalizing_local_review_revision",
                    }
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
                    and prior_task.get("state") not in {
                        "finalizing_revision", "finalizing_local_review_revision",
                    }
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
        # Delivery is another writer kind: it cannot overlap implementation.
        for record in records:
            delivery_dir = Path(record.git_dir) / "codex-control-plane" / "delivery-leases"
            if delivery_dir.is_symlink():
                raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: delivery lease directory is unsafe")
            if not delivery_dir.exists():
                continue
            for candidate in sorted(delivery_dir.glob("*.json")):
                if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 65_536:
                    raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: delivery lease candidate is unsafe")
                try:
                    other = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: delivery lease is unreadable") from error
                semantic = {key: value for key, value in other.items() if key != "lease_digest"}
                if (
                    other.get("kind") != "DeliveryLeaseV1"
                    or other.get("lease_digest") != contract_digest(semantic)
                    or not isinstance(other.get("task_id"), str)
                    or not isinstance(other.get("paths"), list)
                    or any(normalize_scope(item) != item for item in other["paths"])
                ):
                    raise ValueError("E_LEASE_OBSERVATION_UNKNOWN: delivery lease contract is invalid")
                if other["task_id"] == task_id:
                    raise ValueError("E_LEASE_MISMATCH: task delivery lease exists")
                if any(scopes_overlap(left, right) for left in payload["paths"] for right in other["paths"]):
                    raise ValueError("E_LEASE_CONFLICT: delivery owns an overlapping path")
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
        tombstone_matches = False
        if tombstone_path.exists():
            try:
                tombstone = json.loads(
                    tombstone_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    "E_LEASE_OBSERVATION_UNKNOWN: release tombstone is unreadable"
                ) from error
            tombstone_matches = all(
                tombstone.get(key) == value for key, value in expected.items()
            )
        if not lease_path.exists():
            if tombstone is None or not tombstone_matches:
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
        if tombstone is None or not tombstone_matches:
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
