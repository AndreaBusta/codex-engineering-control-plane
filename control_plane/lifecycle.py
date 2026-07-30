"""Task lifecycle, worktree-scoped leases, and compact resource receipts."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import math
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
    HostContextMetrics,
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
    consume_clarification_entry_bindings,
    consume_clarification_resolution_bindings,
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
LATERAL_STATES = frozenset({"clarification_required", "blocked"})
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "framed": frozenset({"planned", "blocked"}),
    "planned": frozenset({"ready", "blocked"}),
    "ready": frozenset({"implementing", "blocked"}),
    "implementing": frozenset({"verifying", "blocked"}),
    "verifying": frozenset({"review_ready", "blocked"}),
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
    "clarification_required": frozenset(),
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
CLARIFICATION_ACTIVE_FIELDS = frozenset(
    {
        "clarification_resume_state",
        "clarification_request",
        "clarification_request_digest",
        "clarification_context_digest",
        "clarification_question_digest",
        "clarification_repository_status",
        "clarification_repository_observation_digest",
        "clarification_repository_evidence_digest",
        "clarification_task_digest",
        "clarification_decision_digest",
        "clarification_prompt_view_path",
        "clarification_prompt_view_generation",
        "clarification_presentation_digest",
        "clarification_repository_identity",
        "clarification_worktree_identity",
        "clarification_branch",
        "clarification_head",
        "clarification_session_id",
        "clarification_invocation_id",
        "clarification_lease_digest",
        "clarification_lease_owner_session",
        "clarification_lease_scope",
        "clarification_lease_policy_digest",
        "clarification_changed_paths",
        "clarification_changed_paths_digest",
        "clarification_blocked_effects",
    }
)
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


def _prompt_view_relative_path(task_id: str, generation: int) -> Path:
    if not validate_task_id(task_id) or generation < 0:
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: invalid prompt generation"
        )
    return (
        Path("codex-control-plane")
        / "clarification-prompt-views"
        / task_id
        / f"generation-{generation:08d}.json"
    )


def _prompt_path_relative(
    state_dir: Path,
    path: Path,
    *,
    directory: bool,
) -> Path:
    """Validate one closed prompt path without resolving its filesystem target."""

    anchor = Path(state_dir).absolute()
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(anchor)
    except ValueError as error:
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt path escapes the Git directory"
        ) from error
    if (
        len(relative.parts) < 3
        or relative.parts[:2]
        != ("codex-control-plane", "clarification-prompt-views")
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not validate_task_id(relative.parts[2])
    ):
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt path is outside its closed root"
        )
    if directory:
        if len(relative.parts) != 3:
            raise ValueError(
                "C_PRESENTATION_UNAVAILABLE: prompt directory is invalid"
            )
    elif (
        len(relative.parts) != 4
        or re.fullmatch(
            r"generation-[0-9]{8}\.json", relative.parts[3]
        )
        is None
    ):
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt sidecar is invalid"
        )
    return relative


def _prompt_directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: safe directory traversal is unsupported"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


@contextmanager
def _open_prompt_directory(
    state_dir: Path,
    directory: Path,
    *,
    create: bool,
    allow_missing: bool = False,
):
    """Open a prompt directory by anchored, no-follow descriptor traversal."""

    relative = _prompt_path_relative(
        state_dir,
        directory,
        directory=True,
    )
    descriptor = -1
    try:
        descriptor = os.open(
            os.fspath(Path(state_dir).absolute()),
            _prompt_directory_flags(),
        )
    except OSError as error:
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: Git directory is unavailable"
        ) from error
    try:
        for component in relative.parts:
            next_descriptor = -1
            try:
                next_descriptor = os.open(
                    component,
                    _prompt_directory_flags(),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if not create:
                    if allow_missing:
                        os.close(descriptor)
                        descriptor = -1
                        yield None
                        return
                    raise ValueError(
                        "C_PRESENTATION_UNAVAILABLE: prompt directory is unavailable"
                    ) from None
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise ValueError(
                        "C_PRESENTATION_UNAVAILABLE: prompt directory is unavailable"
                    ) from error
                try:
                    next_descriptor = os.open(
                        component,
                        _prompt_directory_flags(),
                        dir_fd=descriptor,
                    )
                except OSError as error:
                    raise ValueError(
                        "C_PRESENTATION_UNAVAILABLE: prompt directory is unsafe"
                    ) from error
            except OSError as error:
                raise ValueError(
                    "C_PRESENTATION_UNAVAILABLE: prompt directory is unsafe"
                ) from error
            if not stat.S_ISDIR(os.fstat(next_descriptor).st_mode):
                os.close(next_descriptor)
                raise ValueError(
                    "C_PRESENTATION_UNAVAILABLE: prompt directory is unsafe"
                )
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("prompt sidecar write made no progress")
        offset += written


def _atomic_bytes(state_dir: Path, path: Path, payload: bytes) -> None:
    """Durably publish bounded bytes without re-resolving prompt ancestors."""

    if not isinstance(payload, bytes) or len(payload) > 1024:
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt view exceeds 1 KiB"
        )
    relative = _prompt_path_relative(state_dir, path, directory=False)
    temporary_name = f".{relative.name}.{uuid4().hex}.tmp"
    descriptor = -1
    with _open_prompt_directory(
        state_dir,
        path.parent,
        create=True,
    ) as directory_descriptor:
        assert directory_descriptor is not None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(
                temporary_name,
                relative.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            os.fsync(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass


def _read_prompt_bytes(
    state_dir: Path,
    path: Path,
) -> tuple[bytes, os.stat_result]:
    relative = _prompt_path_relative(state_dir, path, directory=False)
    with _open_prompt_directory(
        state_dir,
        path.parent,
        create=False,
    ) as directory_descriptor:
        assert directory_descriptor is not None
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(
                relative.name,
                flags,
                dir_fd=directory_descriptor,
            )
            try:
                metadata = os.fstat(descriptor)
                payload = os.read(descriptor, 1025)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise ValueError(
                "C_PRESENTATION_UNAVAILABLE: durable prompt view is unavailable"
            ) from error
    return payload, metadata


def _unlink_prompt_entry(directory_descriptor: int, name: str) -> bool:
    try:
        metadata = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt sidecar is unsafe"
        )
    os.unlink(name, dir_fd=directory_descriptor)
    os.fsync(directory_descriptor)
    return True


def _unlink_prompt_file(state_dir: Path, path: Path) -> bool:
    relative = _prompt_path_relative(state_dir, path, directory=False)
    with _open_prompt_directory(
        state_dir,
        path.parent,
        create=False,
        allow_missing=True,
    ) as directory_descriptor:
        if directory_descriptor is None:
            return False
        return _unlink_prompt_entry(directory_descriptor, relative.name)


def _canonical_prompt_view_bytes(
    prompt_view: Mapping[str, object],
) -> bytes:
    payload = json.dumps(
        prompt_view,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > 1024:
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt view exceeds 1 KiB"
        )
    return payload


def _changed_paths(worktree: Path | str) -> list[str]:
    root = Path(worktree).resolve()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    commands = (
        ["git", "-C", str(root), "diff", "--name-only", "-z", "HEAD"],
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ],
    )
    values: set[str] = set()
    for command in commands:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        if completed.returncode != 0:
            raise ValueError(
                "E_CLARIFICATION_LEASE_DRIFT: changed paths are unavailable"
            )
        try:
            values.update(
                item.decode("utf-8", errors="strict")
                for item in completed.stdout.split(b"\0")
                if item
            )
        except UnicodeDecodeError as error:
            raise ValueError(
                "E_CLARIFICATION_LEASE_DRIFT: changed paths are invalid"
            ) from error
    normalized = sorted(values)
    if any(
        normalize_scope(item) is None or normalize_scope(item) != item
        for item in normalized
    ):
        raise ValueError(
            "E_CLARIFICATION_LEASE_DRIFT: changed paths are unsafe"
        )
    return normalized


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

    def _clarification_sidecar(
        self, task_id: str, state: Mapping[str, Any]
    ) -> Path:
        supplied = state.get("clarification_prompt_view_path")
        if not isinstance(supplied, str):
            raise ValueError(
                "C_PRESENTATION_UNAVAILABLE: prompt view reference is absent"
            )
        relative = Path(supplied)
        expected_root = (
            Path("codex-control-plane")
            / "clarification-prompt-views"
            / task_id
        )
        if (
            relative.is_absolute()
            or relative.parent != expected_root
            or not re.fullmatch(
                r"generation-[0-9]{8}\.json", relative.name
            )
        ):
            raise ValueError(
                "C_PRESENTATION_UNAVAILABLE: prompt view reference is invalid"
            )
        return self.state_dir / relative

    def _unlink_clarification_sidecar(self, sidecar: Path) -> None:
        _unlink_prompt_file(self.state_dir, sidecar)

    @staticmethod
    def _clear_clarification_fields(state: dict[str, Any]) -> None:
        for key in CLARIFICATION_ACTIVE_FIELDS:
            state.pop(key, None)
        state.pop("clarification_resolution_invalidated", None)

    def clarification_status(self, task_id: str) -> dict[str, Any]:
        """Return only the durable request and its exact safe presentation."""

        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            self._assert_runtime_owner(state)
            if state.get("state") != "clarification_required":
                raise ValueError(
                    "C_PRESENTATION_UNAVAILABLE: task is not awaiting clarification"
                )
            request, prompt_view = self._read_clarification_presentation(
                task_id, state
            )
            return {
                "task_id": task_id,
                "state": "clarification_required",
                "generation": state.get("generation"),
                "request": copy.deepcopy(request),
                "prompt_view": copy.deepcopy(prompt_view),
                "presentation_digest": state.get(
                    "clarification_presentation_digest"
                ),
            }

    def _read_clarification_presentation(
        self,
        task_id: str,
        state: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from control_plane.clarification import validate_clarification_request

        sidecar = self._clarification_sidecar(task_id, state)
        payload, metadata = _read_prompt_bytes(self.state_dir, sidecar)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or len(payload) > 1024
        ):
            raise ValueError(
                "C_PRESENTATION_UNAVAILABLE: durable prompt view is unsafe"
            )
        try:
            prompt_view = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "C_PRESENTATION_UNAVAILABLE: durable prompt view is invalid"
            ) from error
        request = state.get("clarification_request")
        if (
            not isinstance(prompt_view, dict)
            or not isinstance(request, dict)
            or _canonical_prompt_view_bytes(prompt_view) != payload
            or contract_digest(prompt_view)
            != state.get("clarification_presentation_digest")
            or request.get("presentation_digest")
            != state.get("clarification_presentation_digest")
            or contract_digest(request)
            != state.get("clarification_request_digest")
            or validate_clarification_request(request)
        ):
            raise ValueError(
                "C_PRESENTATION_UNAVAILABLE: durable prompt binding is invalid"
            )
        return request, prompt_view

    def gc_clarification_prompt_views(
        self,
        task_id: str,
        *,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        """Remove only unreferenced prompt-view generations under task flock."""

        with _task_guard(self.state_dir, task_id):
            state = self._read(task_id)
            self._assert_runtime_owner(state)
            generation = int(state.get("generation", -1))
            if (
                expected_generation is not None
                and generation != expected_generation
            ):
                raise ValueError(
                    "E_STATE_CAS: task generation changed before prompt GC"
                )
            current: Path | None = None
            if state.get("state") == "clarification_required":
                current = self._clarification_sidecar(task_id, state)
            directory = (
                self.state_dir
                / "codex-control-plane"
                / "clarification-prompt-views"
                / task_id
            )
            removed: list[str] = []
            with _open_prompt_directory(
                self.state_dir,
                directory,
                create=False,
                allow_missing=True,
            ) as directory_descriptor:
                if current is not None and directory_descriptor is None:
                    raise ValueError(
                        "C_PRESENTATION_UNAVAILABLE: current prompt view is unavailable"
                    )
                if directory_descriptor is None:
                    names: list[str] = []
                else:
                    names = sorted(os.listdir(directory_descriptor))
                for name in names:
                    if (
                        re.fullmatch(r"generation-[0-9]{8}\.json", name)
                        is None
                    ):
                        continue
                    candidate = directory / name
                    if current is not None and candidate == current:
                        continue
                    assert directory_descriptor is not None
                    if _unlink_prompt_entry(directory_descriptor, name):
                        removed.append(str(candidate))
            return {
                "task_id": task_id,
                "generation": generation,
                "removed": removed,
            }

    def require_clarification(
        self,
        task_id: str,
        *,
        request: object,
        route_context: object,
        expected_generation: int,
        current_branch: str,
        task_digest: str,
        decision_digest: str,
    ) -> dict[str, Any]:
        """Publish one durable lateral gate; generic transitions cannot enter it."""

        pre_state = self._read(task_id)
        self._assert_runtime_owner(pre_state)
        if (
            not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or expected_generation < 0
        ):
            raise ValueError(
                "E_STATE_CAS: expected clarification generation is invalid"
            )
        if int(pre_state.get("generation", -1)) != expected_generation:
            raise ValueError(
                "E_STATE_CAS: task changed before clarification"
            )
        if (
            pre_state.get("state") == "clarification_required"
            and pre_state.get("clarification_resolution_invalidated")
            not in {
                "question_changed",
                "repository_evidence_changed",
                "context_changed",
            }
        ):
            raise ValueError(
                "E_STATE_LATERAL: clarification refresh is not authorized"
            )
        pre_changed_paths: list[str] | None = None
        pre_resume_source = (
            str(pre_state.get("clarification_resume_state", ""))
            if pre_state.get("state") == "clarification_required"
            else str(pre_state.get("state", ""))
        )
        if pre_resume_source == "implementing":
            if pre_state.get("state") == "clarification_required":
                observed_worktree = pre_state.get(
                    "clarification_worktree_identity"
                )
            else:
                pre_lease = self._read_owner_lease(task_id)
                observed_worktree = (
                    pre_lease.get("worktree")
                    if isinstance(pre_lease, Mapping)
                    else None
                )
            if not isinstance(observed_worktree, str):
                raise ValueError(
                    "E_CLARIFICATION_LEASE_DRIFT: writer worktree is unavailable"
                )
            pre_changed_paths = _changed_paths(observed_worktree)
        common_dir = _common_git_dir(self.state_dir)
        with _common_lease_lock(common_dir):
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                self._assert_runtime_owner(state)
                source = str(state.get("state"))
                if int(state.get("generation", -1)) != expected_generation:
                    raise ValueError(
                        "E_STATE_CAS: task changed before clarification"
                    )
                if (
                    source == "clarification_required"
                    and (
                        not isinstance(
                            state.get(
                                "clarification_resolution_invalidated"
                            ),
                            str,
                        )
                        or state.get("clarification_resume_state")
                        not in {
                            "framed",
                            "planned",
                            "ready",
                            "implementing",
                            "verifying",
                            "review_ready",
                        }
                    )
                ) or (
                    source
                    not in {
                        "framed",
                        "planned",
                        "ready",
                        "implementing",
                        "verifying",
                        "review_ready",
                        "clarification_required",
                    }
                    or state.get("resume_forbidden")
                ):
                    raise ValueError(
                        "E_STATE_LATERAL: task cannot require clarification"
                    )
                if (
                    source == "clarification_required"
                    and state.get("clarification_resolution_invalidated")
                    not in {
                        "question_changed",
                        "repository_evidence_changed",
                        "context_changed",
                    }
                ):
                    raise ValueError(
                        "E_STATE_LATERAL: clarification refresh is not authorized"
                    )
                if (
                    current_branch != state.get("branch")
                    or task_digest != state.get("task_digest")
                    or decision_digest != state.get("decision_digest")
                    or SHA256_DIGEST.fullmatch(task_digest) is None
                    or SHA256_DIGEST.fullmatch(decision_digest) is None
                ):
                    raise ValueError(
                        "C_ROUTE_CONTEXT_UNTRUSTED: task binding changed"
                    )
                reissuing = source == "clarification_required"
                resume_source = (
                    str(state["clarification_resume_state"])
                    if reissuing
                    else source
                )
                if resume_source != pre_resume_source:
                    raise ValueError(
                        "E_STATE_CAS: clarification resume state changed"
                    )
                prior_sidecar: Path | None = None
                if reissuing:
                    self._read_clarification_presentation(task_id, state)
                    prior_sidecar = self._clarification_sidecar(
                        task_id, state
                    )
                bindings = consume_clarification_entry_bindings(
                    request=request,
                    route_context=route_context,
                    expected_task_digest=task_digest,
                    expected_decision_digest=decision_digest,
                    expected_branch=current_branch,
                )
                repository_binding = {
                    "status": bindings["repository_status"],
                    "evidence_digest": bindings[
                        "repository_evidence_digest"
                    ],
                }
                next_generation = int(state.get("generation", 0)) + 1
                relative_sidecar = _prompt_view_relative_path(
                    task_id, next_generation
                )
                sidecar = self.state_dir / relative_sidecar
                prompt_view = bindings["prompt_view"]
                if not isinstance(prompt_view, Mapping):
                    raise ValueError(
                        "C_PRESENTATION_UNAVAILABLE: safe prompt is absent"
                    )
                lease_fields: dict[str, Any] = {}
                if resume_source == "implementing":
                    changed_paths = pre_changed_paths
                    lease = self._read_owner_lease(task_id)
                    semantic = (
                        {
                            key: value
                            for key, value in lease.items()
                            if key != "lease_digest"
                        }
                        if isinstance(lease, dict)
                        else {}
                    )
                    owner_changed = (
                        reissuing
                        and isinstance(lease, Mapping)
                        and lease.get("session_id")
                        != bindings["session_id"]
                    )
                    lease_invalid = (
                        lease is None
                        or lease.get("lease_digest")
                        != contract_digest(semantic)
                        or lease.get("worktree")
                        != bindings["worktree_identity"]
                        or lease.get("branch") != current_branch
                        or (
                            not reissuing
                            and lease.get("session_id")
                            != bindings["session_id"]
                        )
                        or not isinstance(lease.get("paths"), list)
                        or changed_paths is None
                        or any(
                            not any(
                                scope_owns(scope, path)
                                for scope in lease["paths"]
                            )
                            for path in changed_paths
                        )
                    )
                    if lease_invalid:
                        if reissuing:
                            return self._publish_clarification_block(
                                task_id=task_id,
                                state=state,
                                expected_generation=expected_generation,
                                sidecar=prior_sidecar,
                                reason=(
                                    "E_CLARIFICATION_OWNER_CHANGED"
                                    if owner_changed
                                    else "E_CLARIFICATION_LEASE_DRIFT"
                                ),
                            )
                        raise ValueError(
                            "E_CLARIFICATION_LEASE_DRIFT: writer lease is invalid"
                        )
                    lease_fields = {
                        "clarification_lease_digest": lease[
                            "lease_digest"
                        ],
                        "clarification_lease_owner_session": lease[
                            "session_id"
                        ],
                        "clarification_lease_scope": copy.deepcopy(
                            lease["paths"]
                        ),
                        "clarification_lease_policy_digest": lease[
                            "policy_digest"
                        ],
                        "clarification_changed_paths": list(changed_paths),
                        "clarification_changed_paths_digest": (
                            contract_digest(changed_paths)
                        ),
                        "clarification_blocked_effects": list(
                            bindings["blocked_effects"]
                        ),
                        "lease_digest": lease["lease_digest"],
                    }
                    if reissuing:
                        owner_changed = owner_changed or (
                            bindings["session_id"]
                            != state.get(
                                "clarification_lease_owner_session"
                            )
                        )
                        refresh_drift = (
                            lease["lease_digest"]
                            != state.get("clarification_lease_digest")
                            or lease["policy_digest"]
                            != state.get(
                                "clarification_lease_policy_digest"
                            )
                            or lease["paths"]
                            != state.get("clarification_lease_scope")
                            or changed_paths
                            != state.get("clarification_changed_paths")
                            or contract_digest(changed_paths)
                            != state.get(
                                "clarification_changed_paths_digest"
                            )
                        )
                        if owner_changed or refresh_drift:
                            return self._publish_clarification_block(
                                task_id=task_id,
                                state=state,
                                expected_generation=expected_generation,
                                sidecar=prior_sidecar,
                                reason=(
                                    "E_CLARIFICATION_OWNER_CHANGED"
                                    if owner_changed
                                    else "E_CLARIFICATION_LEASE_DRIFT"
                                ),
                            )
                _atomic_bytes(
                    self.state_dir,
                    sidecar,
                    _canonical_prompt_view_bytes(prompt_view),
                )
                self._clear_clarification_fields(state)
                state.update(
                    {
                        "state": "clarification_required",
                        "resume_state": None,
                        "clarification_resume_state": resume_source,
                        "clarification_request": copy.deepcopy(
                            bindings["request"]
                        ),
                        "clarification_request_digest": bindings[
                            "request_digest"
                        ],
                        "clarification_context_digest": bindings[
                            "context_digest"
                        ],
                        "clarification_question_digest": bindings[
                            "request"
                        ]["question_digest"],
                        "clarification_repository_status": bindings[
                            "repository_status"
                        ],
                        "clarification_repository_observation_digest": (
                            bindings["repository_evidence_digest"]
                        ),
                        "clarification_repository_evidence_digest": (
                            contract_digest(repository_binding)
                        ),
                        "clarification_task_digest": task_digest,
                        "clarification_decision_digest": decision_digest,
                        "clarification_prompt_view_path": str(
                            relative_sidecar
                        ),
                        "clarification_prompt_view_generation": (
                            next_generation
                        ),
                        "clarification_presentation_digest": bindings[
                            "request"
                        ]["presentation_digest"],
                        "clarification_repository_identity": bindings[
                            "repository_identity"
                        ],
                        "clarification_worktree_identity": bindings[
                            "worktree_identity"
                        ],
                        "clarification_branch": bindings["branch"],
                        "clarification_head": bindings["head"],
                        "clarification_session_id": bindings[
                            "session_id"
                        ],
                        "clarification_invocation_id": bindings[
                            "invocation_id"
                        ],
                        "clarification_blocked_effects": list(
                            bindings["blocked_effects"]
                        ),
                        "generation": next_generation,
                        "block_reason": None,
                        "updated_at": _utc_now(),
                        **lease_fields,
                    }
                )
                state.pop(
                    "clarification_resolution_invalidated", None
                )
                _atomic_json(self._path(task_id), state)
                if prior_sidecar is not None and prior_sidecar != sidecar:
                    self._unlink_clarification_sidecar(prior_sidecar)
                return state

    def _publish_clarification_block(
        self,
        *,
        task_id: str,
        state: dict[str, Any],
        expected_generation: int,
        sidecar: Path | None,
        reason: str,
    ) -> dict[str, Any]:
        self._clear_clarification_fields(state)
        state.update(
            {
                "state": "blocked",
                "resume_state": None,
                "block_reason": reason,
                "resume_forbidden": True,
                "clarification_block_digest": contract_digest(
                    {
                        "reason": reason,
                        "task_id": task_id,
                        "generation": expected_generation,
                    }
                ),
                "generation": expected_generation + 1,
                "updated_at": _utc_now(),
            }
        )
        _atomic_json(self._path(task_id), state)
        if sidecar is not None:
            self._unlink_clarification_sidecar(sidecar)
        return state

    def _publish_clarification_destination(
        self,
        *,
        task_id: str,
        state: dict[str, Any],
        destination: str,
        expected_generation: int,
        resolution_digest: str,
        sidecar: Path,
        block_reason: str | None = None,
        resume_forbidden: bool = False,
    ) -> dict[str, Any]:
        self._clear_clarification_fields(state)
        state.update(
            {
                "state": destination,
                "resume_state": None,
                "block_reason": block_reason,
                "resume_forbidden": resume_forbidden,
                "clarification_resolution_digest": resolution_digest,
                "generation": expected_generation + 1,
                "updated_at": _utc_now(),
            }
        )
        _atomic_json(self._path(task_id), state)
        self._unlink_clarification_sidecar(sidecar)
        return state

    def resolve_and_resume_clarification(
        self,
        task_id: str,
        *,
        interaction: object,
        route_context: object,
        repository_context: object,
        expected_generation: int,
        current_branch: str,
        expected_head: str,
        task_digest: str,
        decision_digest: str,
        context_digest: str,
        question_digest: str,
    ) -> dict[str, Any]:
        """Consume native resolution evidence and publish the exact final state."""

        pre_state = self._read(task_id)
        self._assert_runtime_owner(pre_state)
        current_changed_paths: list[str] | None = None
        if (
            pre_state.get("state") == "clarification_required"
            and pre_state.get("clarification_resume_state")
            == "implementing"
        ):
            current_changed_paths = _changed_paths(
                str(pre_state.get("clarification_worktree_identity", ""))
            )
        common_dir = _common_git_dir(self.state_dir)
        with _common_lease_lock(common_dir) as token:
            dirty_reframe: dict[str, Any] | None = None
            with _task_guard(self.state_dir, task_id):
                state = self._read(task_id)
                self._assert_runtime_owner(state)
                if (
                    state.get("state") != "clarification_required"
                    or state.get("generation") != expected_generation
                    or state.get("branch") != current_branch
                    or state.get("resume_forbidden")
                ):
                    raise ValueError(
                        "E_STATE_CAS: clarification state changed"
                    )
                if state.get("clarification_resolution_invalidated"):
                    raise ValueError(
                        "C_CLARIFICATION_REQUEST_REQUIRED: "
                        "invalidated clarification must be reframed"
                    )
                durable_request = state.get("clarification_request")
                if (
                    not isinstance(durable_request, Mapping)
                    or contract_digest(durable_request)
                    != state.get("clarification_request_digest")
                    or SHA256_DIGEST.fullmatch(task_digest) is None
                    or SHA256_DIGEST.fullmatch(decision_digest) is None
                    or SHA256_DIGEST.fullmatch(context_digest) is None
                    or SHA256_DIGEST.fullmatch(question_digest) is None
                ):
                    raise ValueError(
                        "C_TASK_DIGEST: durable clarification binding is invalid"
                    )
                self._read_clarification_presentation(task_id, state)
                resume_state = str(
                    state.get("clarification_resume_state", "")
                )
                bindings = consume_clarification_resolution_bindings(
                    interaction=interaction,
                    route_context=route_context,
                    repository_context=repository_context,
                    durable_request=durable_request,
                    expected_request_digest=str(
                        state["clarification_request_digest"]
                    ),
                    expected_original_task_digest=str(
                        state["clarification_task_digest"]
                    ),
                    expected_current_task_digest=task_digest,
                    expected_decision_digest=decision_digest,
                    expected_repository_identity=str(
                        state["clarification_repository_identity"]
                    ),
                    expected_worktree_identity=str(
                        state["clarification_worktree_identity"]
                    ),
                    expected_branch=current_branch,
                    expected_head=expected_head,
                    current_question_digest=question_digest,
                )
                if context_digest != bindings["context_digest"]:
                    raise ValueError(
                        "C_CONTEXT_DIGEST: current context digest changed"
                    )
                sidecar = self._clarification_sidecar(task_id, state)
                task_digest_changed = (
                    task_digest != state.get("clarification_task_digest")
                )
                decision_digest_changed = (
                    decision_digest
                    != state.get("clarification_decision_digest")
                )
                current_repository_binding = contract_digest(
                    {
                        "status": bindings["repository_status"],
                        "evidence_digest": bindings[
                            "repository_evidence_digest"
                        ],
                    }
                )
                question_changed = (
                    question_digest
                    != state.get("clarification_question_digest")
                )
                repository_changed = (
                    current_repository_binding
                    != state.get(
                        "clarification_repository_evidence_digest"
                    )
                )
                context_changed = (
                    context_digest
                    != state.get("clarification_context_digest")
                )
                material_frame_changed = task_digest_changed or (
                    decision_digest_changed and context_changed
                )
                resolution_digest = str(
                    bindings["resolution_digest"]
                )
                if resume_state == "implementing":
                    owner_session = state.get(
                        "clarification_lease_owner_session"
                    )
                    if bindings["session_id"] != owner_session:
                        return self._publish_clarification_destination(
                            task_id=task_id,
                            state=state,
                            destination="blocked",
                            expected_generation=expected_generation,
                            resolution_digest=resolution_digest,
                            sidecar=sidecar,
                            block_reason=(
                                "E_CLARIFICATION_OWNER_CHANGED"
                            ),
                            resume_forbidden=True,
                        )
                    lease = self._read_owner_lease(task_id)
                    lease_semantic = (
                        {
                            key: value
                            for key, value in lease.items()
                            if key != "lease_digest"
                        }
                        if isinstance(lease, dict)
                        else {}
                    )
                    lease_drift = (
                        lease is None
                        or lease.get("lease_digest")
                        != contract_digest(lease_semantic)
                        or lease.get("lease_digest")
                        != state.get("clarification_lease_digest")
                        or lease.get("session_id") != owner_session
                        or lease.get("worktree")
                        != state.get(
                            "clarification_worktree_identity"
                        )
                        or lease.get("branch") != current_branch
                        or lease.get("policy_digest")
                        != state.get(
                            "clarification_lease_policy_digest"
                        )
                        or lease.get("paths")
                        != state.get("clarification_lease_scope")
                        or current_changed_paths
                        != state.get("clarification_changed_paths")
                        or contract_digest(
                            current_changed_paths
                            if current_changed_paths is not None
                            else []
                        )
                        != state.get(
                            "clarification_changed_paths_digest"
                        )
                        or expected_head
                        != state.get("clarification_head")
                    )
                    if lease_drift:
                        return self._publish_clarification_destination(
                            task_id=task_id,
                            state=state,
                            destination="blocked",
                            expected_generation=expected_generation,
                            resolution_digest=resolution_digest,
                            sidecar=sidecar,
                            block_reason=(
                                "E_CLARIFICATION_LEASE_DRIFT"
                            ),
                            resume_forbidden=True,
                        )
                    if material_frame_changed:
                        marker = dict(state)
                        marker.update(
                            {
                                "state": "finalizing_suspend",
                                "resume_state": None,
                                "resume_forbidden": True,
                                "finalization": {
                                    "destination": "blocked",
                                    "reason_code": "E_REFRAME_REQUIRED",
                                    "prior_generation": (
                                        expected_generation
                                    ),
                                    "task_id": task_id,
                                    "worktree": lease["worktree"],
                                    "branch": lease["branch"],
                                    "session_id": lease["session_id"],
                                    "policy_digest": lease[
                                        "policy_digest"
                                    ],
                                    "lease_digest": lease[
                                        "lease_digest"
                                    ],
                                    "clarification_reframe": True,
                                    "clarification_sidecar": str(sidecar),
                                    "resolution_digest": (
                                        resolution_digest
                                    ),
                                },
                                "updated_at": _utc_now(),
                            }
                        )
                        _atomic_json(self._path(task_id), marker)
                        dirty_reframe = {
                            "marker": marker,
                            "lease": lease,
                            "sidecar": sidecar,
                            "resolution_digest": resolution_digest,
                        }
                    elif question_changed or repository_changed or context_changed:
                        reason = (
                            "question_changed"
                            if question_changed
                            else (
                                "repository_evidence_changed"
                                if repository_changed
                                else "context_changed"
                            )
                        )
                        state[
                            "clarification_resolution_invalidated"
                        ] = reason
                        state["generation"] = expected_generation + 1
                        state["updated_at"] = _utc_now()
                        _atomic_json(self._path(task_id), state)
                        return state
                    else:
                        state["decision_digest"] = decision_digest
                        return self._publish_clarification_destination(
                            task_id=task_id,
                            state=state,
                            destination="implementing",
                            expected_generation=expected_generation,
                            resolution_digest=resolution_digest,
                            sidecar=sidecar,
                        )
                elif material_frame_changed:
                    invalidated_from = resume_state
                    for evidence_state in (
                        "ready",
                        "verifying",
                        "review_ready",
                    ):
                        state.get("evidence", {}).pop(
                            evidence_state, None
                        )
                    self._clear_clarification_fields(state)
                    state.update(
                        {
                            "state": "planned",
                            "task_digest": task_digest,
                            "decision_digest": decision_digest,
                            "resume_state": None,
                            "block_reason": None,
                            "resume_forbidden": False,
                            "clarification_resolution_digest": (
                                resolution_digest
                            ),
                            "clarification_invalidation": {
                                "invalidated_from": invalidated_from,
                                "prior_task_digest": durable_request[
                                    "task_digest"
                                ],
                                "new_task_digest": task_digest,
                                "new_decision_digest": decision_digest,
                            },
                            "generation": expected_generation + 1,
                            "updated_at": _utc_now(),
                        }
                    )
                    _atomic_json(self._path(task_id), state)
                    self._unlink_clarification_sidecar(sidecar)
                    return state
                elif question_changed or repository_changed or context_changed:
                    reason = (
                        "question_changed"
                        if question_changed
                        else (
                            "repository_evidence_changed"
                            if repository_changed
                            else "context_changed"
                        )
                    )
                    state[
                        "clarification_resolution_invalidated"
                    ] = reason
                    state["generation"] = expected_generation + 1
                    state["updated_at"] = _utc_now()
                    _atomic_json(self._path(task_id), state)
                    return state
                else:
                    state["decision_digest"] = decision_digest
                    return self._publish_clarification_destination(
                        task_id=task_id,
                        state=state,
                        destination=resume_state,
                        expected_generation=expected_generation,
                        resolution_digest=resolution_digest,
                        sidecar=sidecar,
                    )
            if dirty_reframe is None:
                raise ValueError(
                    "E_STATE_CAS: clarification resolution did not publish"
                )
            lease = dirty_reframe["lease"]
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
                marker = dirty_reframe["marker"]
                if (
                    current.get("state") != "finalizing_suspend"
                    or current.get("finalization")
                    != marker.get("finalization")
                ):
                    raise ValueError(
                        "E_STATE_CAS: clarification reframe marker changed"
                    )
                current.pop("finalization", None)
                return self._publish_clarification_destination(
                    task_id=task_id,
                    state=current,
                    destination="blocked",
                    expected_generation=expected_generation,
                    resolution_digest=str(
                        dirty_reframe["resolution_digest"]
                    ),
                    sidecar=dirty_reframe["sidecar"],
                    block_reason="E_REFRAME_REQUIRED",
                    resume_forbidden=True,
                )

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
        host_metrics: object | None,
    ) -> dict[str, Any]:
        """Record deduplicated runtime and host metrics under the task flock."""

        runtime_metric_keys = {
            "router_manifest_bytes",
            "novice_brief_bytes",
            "hook_output_bytes",
            "context_units_selected",
        }
        runtime_context_keys = {"tool_use_id"}
        if (
            not validate_task_id(task_id)
            or SHA256_DIGEST.fullmatch(task_digest) is None
            or not validate_task_id(session_id)
            or not validate_task_id(invocation_id)
            or SHA256_DIGEST.fullmatch(subject_digest) is None
            or not isinstance(runtime_metrics, Mapping)
            or not set(runtime_metrics).issubset(
                runtime_metric_keys | runtime_context_keys
            )
        ):
            raise ValueError("M_METRIC_BINDING: metric identity is invalid")
        for key in runtime_metric_keys.intersection(runtime_metrics):
            value = runtime_metrics[key]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError("M_METRIC_BINDING: runtime metric is invalid")
        for key in runtime_context_keys.intersection(runtime_metrics):
            value = runtime_metrics[key]
            if value is not None and not validate_task_id(value):
                raise ValueError("M_METRIC_BINDING: runtime identity is invalid")

        host_payload: dict[str, Any] | None = None
        host_metrics_consumed = False
        if host_metrics is not None:
            if (
                type(host_metrics) is not HostContextMetrics
                or host_metrics.task_digest != task_digest
                or host_metrics.session_id != session_id
                or host_metrics.invocation_id != invocation_id
                or host_metrics.subject_digest != subject_digest
                or contract_digest(host_metrics.payload)
                != host_metrics.payload_digest
            ):
                raise ValueError(
                    "M_METRIC_UNTRUSTED_CHANNEL: host metrics wrapper required"
                )
            host_metrics_consumed = host_metrics._consumed
            if (
                not host_metrics_consumed
                and not _runtime_host_object_is_live(
                    host_metrics, "host_context_metrics"
                )
            ):
                raise ValueError(
                    "M_METRIC_UNTRUSTED_CHANNEL: host metrics wrapper required"
                )
            host_payload = copy.deepcopy(host_metrics.payload)

        def metric_observation(
            *, source: str, metric: str, tool_use_id: object, value: object
        ) -> dict[str, Any]:
            observation = {
                "schema_version": 1,
                "source": source,
                "metric": metric,
                "task_digest": task_digest,
                "session_id": session_id,
                "invocation_id": invocation_id,
                "subject_digest": subject_digest,
                "tool_use_id": tool_use_id,
                "value": copy.deepcopy(value),
            }
            observation["observation_digest"] = contract_digest(
                observation
            )
            return observation

        runtime_observations = [
            metric_observation(
                source="runtime",
                metric=metric,
                tool_use_id=runtime_metrics.get("tool_use_id"),
                value=runtime_metrics[metric],
            )
            for metric in sorted(
                runtime_metric_keys.intersection(runtime_metrics)
            )
        ]
        host_observations: list[dict[str, Any]] = []
        if host_payload is not None:
            host_values = {
                "required_resource_bytes": host_payload[
                    "required_resource_bytes"
                ],
                "recommended_resource_bytes": host_payload[
                    "recommended_resource_bytes"
                ],
                "worker_id": host_payload["worker_id"],
                "retry_count": host_payload["retry_count"],
                "worker_interval": {
                    "started_at_monotonic": host_payload[
                        "started_at_monotonic"
                    ],
                    "ended_at_monotonic": host_payload[
                        "ended_at_monotonic"
                    ],
                },
            }
            rows: list[dict[str, Any]] = []
            for metric, value in sorted(host_values.items()):
                row = {
                    "source": "host",
                    "metric": metric,
                    "task_digest": task_digest,
                    "session_id": session_id,
                    "invocation_id": invocation_id,
                    "subject_digest": subject_digest,
                    "tool_use_id": host_payload["tool_use_id"],
                    "value": copy.deepcopy(value),
                }
                row["row_digest"] = contract_digest(row)
                rows.append(row)
            host_observations = [
                metric_observation(
                    source="host",
                    metric="host_metric_batch",
                    tool_use_id=host_payload["tool_use_id"],
                    value={
                        "rows": rows,
                        "rows_digest": contract_digest(rows),
                    },
                )
            ]

        with _task_guard(self.state_dir, task_id):
            directory = self._metrics_dir(task_id)
            directory.mkdir(parents=True, exist_ok=True)
            observations = runtime_observations + host_observations
            prepared: list[tuple[dict[str, Any], Path, bool]] = []
            for observation in observations:
                identity = contract_digest(
                    {
                        "source": observation["source"],
                        "invocation_id": invocation_id,
                        "tool_use_id": observation["tool_use_id"],
                        "metric": observation["metric"],
                        "subject_digest": observation["subject_digest"],
                    }
                ).removeprefix("sha256:")
                path = directory / f"{observation['source']}-{identity}.json"
                exists_identically = False
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
                    exists_identically = True
                prepared.append((observation, path, exists_identically))
            if host_metrics is not None:
                prepared_host = [
                    exists_identically
                    for observation, _, exists_identically in prepared
                    if observation["source"] == "host"
                ]
                host_already_recorded = bool(prepared_host) and all(
                    prepared_host
                )
                if host_metrics_consumed and not host_already_recorded:
                    raise ValueError(
                        "M_METRIC_UNTRUSTED_CHANNEL: host metrics were consumed"
                    )
            for observation, path, exists_identically in prepared:
                if not exists_identically:
                    _atomic_json(path, observation)
            if (
                host_metrics is not None
                and not host_metrics_consumed
            ):
                if not _consume_runtime_host_object(
                    host_metrics, "host_context_metrics"
                ):
                    raise ValueError(
                        "M_METRIC_UNTRUSTED_CHANNEL: host metrics were consumed"
                    )
                host_metrics._consumed = True
        return self.context_metrics(task_id)

    def context_metrics(self, task_id: str) -> dict[str, Any]:
        """Aggregate unique metric observations independently of arrival order."""

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
        host_metric_keys = {
            "required_resource_bytes",
            "recommended_resource_bytes",
            "worker_id",
            "retry_count",
            "worker_interval",
        }
        host_row_keys = {
            "source",
            "metric",
            "task_digest",
            "session_id",
            "invocation_id",
            "subject_digest",
            "tool_use_id",
            "value",
            "row_digest",
        }

        def valid_observation(observation: Mapping[str, Any]) -> bool:
            if (
                set(observation) != observation_keys
                or observation.get("schema_version") != 1
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
            ):
                return False
            source = observation.get("source")
            metric = observation.get("metric")
            value = observation.get("value")
            if source == "runtime":
                return bool(
                    metric in runtime_metric_keys
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                )
            if (
                source != "host"
                or metric != "host_metric_batch"
                or not isinstance(value, Mapping)
                or set(value) != {"rows", "rows_digest"}
                or not isinstance(value.get("rows"), list)
                or len(value["rows"]) != len(host_metric_keys)
                or value.get("rows_digest")
                != contract_digest(value["rows"])
            ):
                return False
            seen_metrics: set[str] = set()
            for row in value["rows"]:
                if not isinstance(row, Mapping) or set(row) != host_row_keys:
                    return False
                semantic_row = {
                    key: item
                    for key, item in row.items()
                    if key != "row_digest"
                }
                row_metric = row.get("metric")
                row_value = row.get("value")
                if (
                    row.get("row_digest") != contract_digest(semantic_row)
                    or row.get("source") != "host"
                    or row_metric not in host_metric_keys
                    or row_metric in seen_metrics
                    or row.get("task_digest")
                    != observation.get("task_digest")
                    or row.get("session_id")
                    != observation.get("session_id")
                    or row.get("invocation_id")
                    != observation.get("invocation_id")
                    or row.get("subject_digest")
                    != observation.get("subject_digest")
                    or row.get("tool_use_id")
                    != observation.get("tool_use_id")
                ):
                    return False
                seen_metrics.add(str(row_metric))
                if row_metric in {
                    "required_resource_bytes",
                    "recommended_resource_bytes",
                }:
                    if not (
                        row_value is None
                        or (
                            isinstance(row_value, int)
                            and not isinstance(row_value, bool)
                            and row_value >= 0
                        )
                    ):
                        return False
                elif row_metric == "worker_id":
                    if not validate_task_id(row_value):
                        return False
                elif row_metric == "retry_count":
                    if not (
                        isinstance(row_value, int)
                        and not isinstance(row_value, bool)
                        and row_value >= 0
                    ):
                        return False
                elif not (
                    isinstance(row_value, Mapping)
                    and set(row_value)
                    == {
                        "started_at_monotonic",
                        "ended_at_monotonic",
                    }
                    and isinstance(
                        row_value.get("started_at_monotonic"),
                        (int, float),
                    )
                    and not isinstance(
                        row_value.get("started_at_monotonic"), bool
                    )
                    and math.isfinite(
                        float(row_value["started_at_monotonic"])
                    )
                    and float(row_value["started_at_monotonic"]) >= 0
                    and isinstance(
                        row_value.get("ended_at_monotonic"),
                        (int, float),
                    )
                    and not isinstance(
                        row_value.get("ended_at_monotonic"), bool
                    )
                    and math.isfinite(
                        float(row_value["ended_at_monotonic"])
                    )
                    and float(row_value["ended_at_monotonic"])
                    >= float(row_value["started_at_monotonic"])
                ):
                    return False
            return seen_metrics == host_metric_keys

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
                    if observation.get("observation_digest") != contract_digest(
                        semantic
                    ):
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: metric digest mismatch"
                        )
                    if not valid_observation(observation):
                        raise ValueError(
                            "M_METRIC_REPLAY_CONFLICT: metric schema mismatch"
                        )
                    observations.append(observation)

        runtime = [
            item for item in observations if item.get("source") == "runtime"
        ]
        host: list[dict[str, Any]] = []
        for item in observations:
            if item.get("source") != "host":
                continue
            for row in item["value"]["rows"]:
                host.append(
                    {
                        **item,
                        "metric": row["metric"],
                        "value": row["value"],
                    }
                )

        def metric_values(
            source: list[dict[str, Any]], metric: str
        ) -> list[object]:
            return [
                item.get("value")
                for item in source
                if item.get("metric") == metric
            ]

        def numeric_values(
            source: list[dict[str, Any]], metric: str
        ) -> list[int]:
            return [
                int(value)
                for value in metric_values(source, metric)
                if isinstance(value, int) and not isinstance(value, bool)
            ]

        def metric_values_by_invocation(
            source: list[dict[str, Any]], metric: str
        ) -> list[object]:
            grouped: dict[str, object] = {}
            for item in source:
                if item.get("metric") != metric:
                    continue
                invocation = str(item.get("invocation_id"))
                value = item.get("value")
                if invocation in grouped and grouped[invocation] != value:
                    raise ValueError(
                        "M_METRIC_REPLAY_CONFLICT: "
                        "per-invocation metric changed across tools"
                    )
                grouped[invocation] = value
            return [grouped[key] for key in sorted(grouped)]

        def numeric_values_by_invocation(
            source: list[dict[str, Any]], metric: str
        ) -> list[int]:
            return [
                int(value)
                for value in metric_values_by_invocation(source, metric)
                if isinstance(value, int) and not isinstance(value, bool)
            ]

        def total_and_max(metric: str) -> tuple[int, int]:
            collected = numeric_values(runtime, metric)
            return sum(collected), max(collected, default=0)

        router_total, router_max = total_and_max("router_manifest_bytes")
        brief_total, brief_max = total_and_max("novice_brief_bytes")
        hook_total, hook_max = total_and_max("hook_output_bytes")
        units = numeric_values_by_invocation(
            runtime, "context_units_selected"
        )
        units_total, units_max = sum(units), max(units, default=0)
        required = numeric_values(host, "required_resource_bytes")
        recommended = numeric_values(
            host, "recommended_resource_bytes"
        )
        workers = {
            str(value)
            for value in metric_values(host, "worker_id")
            if validate_task_id(value)
        }
        retries = numeric_values_by_invocation(host, "retry_count")
        intervals = [
            (
                float(value["started_at_monotonic"]),
                float(value["ended_at_monotonic"]),
            )
            for value in metric_values_by_invocation(
                host, "worker_interval"
            )
            if isinstance(value, Mapping)
            and isinstance(
                value.get("started_at_monotonic"), (int, float)
            )
            and not isinstance(value.get("started_at_monotonic"), bool)
            and isinstance(
                value.get("ended_at_monotonic"), (int, float)
            )
            and not isinstance(value.get("ended_at_monotonic"), bool)
            and float(value["ended_at_monotonic"])
            >= float(value["started_at_monotonic"])
        ]
        runtime_identities = {
            (str(item.get("invocation_id")), item.get("tool_use_id"))
            for item in runtime
        }
        host_identities = {
            (str(item.get("invocation_id")), item.get("tool_use_id"))
            for item in host
        }
        host_invocations = {
            invocation_id for invocation_id, _ in host_identities
        }
        required_host_metrics = {
            "required_resource_bytes",
            "recommended_resource_bytes",
            "worker_id",
            "retry_count",
            "worker_interval",
        }
        host_complete = bool(runtime_identities) and (
            runtime_identities == host_identities
            and all(
                sum(
                    1
                    for item in host
                    if (
                        str(item.get("invocation_id")),
                        item.get("tool_use_id"),
                    )
                    == identity
                    and item.get("metric") == metric
                )
                == 1
                for identity in host_identities
                for metric in required_host_metrics
            )
            and len(required) == len(host_identities)
            and len(recommended) == len(host_identities)
            and len(metric_values_by_invocation(host, "worker_id"))
            == len(host_invocations)
            and len(retries) == len(host_invocations)
            and len(intervals) == len(host_invocations)
        )
        invocations = {
            str(item.get("invocation_id")) for item in observations
        }
        hook_invocations = {
            str(item["tool_use_id"])
            for item in observations
            if item.get("tool_use_id") is not None
        }
        return {
            "schema_version": 1,
            "task_id": task_id,
            "metrics_status": "complete" if host_complete else "partial",
            "router_manifest_bytes_total": router_total,
            "router_manifest_bytes_max": router_max,
            "novice_brief_bytes_total": brief_total,
            "novice_brief_bytes_max": brief_max,
            "hook_output_bytes_total": hook_total,
            "hook_output_bytes_max": hook_max,
            "required_resource_bytes_total": (
                sum(required) if host_complete else None
            ),
            "required_resource_bytes_max": (
                max(required, default=0) if host_complete else None
            ),
            "recommended_resource_bytes_total": (
                sum(recommended) if host_complete else None
            ),
            "recommended_resource_bytes_max": (
                max(recommended, default=0) if host_complete else None
            ),
            "invocation_count_unique": len(invocations),
            "hook_invocation_count_unique": len(hook_invocations),
            "context_units_selected_total": units_total,
            "context_units_selected_max": units_max,
            "workers_unique": len(workers) if host_complete else None,
            "retry_count_total": sum(retries) if host_complete else None,
            "worker_time_ms_total": (
                round(sum(end - start for start, end in intervals) * 1000)
                if host_complete
                else None
            ),
            "task_elapsed_ms": (
                round(
                    (
                        max(end for _, end in intervals)
                        - min(start for start, _ in intervals)
                    )
                    * 1000
                )
                if host_complete and intervals
                else None
            ),
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
        if (
            source == "clarification_required"
            or target == "clarification_required"
        ):
            raise ValueError(
                "E_STATE_LATERAL: clarification requires the specialized host API"
            )
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
                clarification_sidecar: Path | None = None
                if finalization.get("clarification_reframe") is True:
                    clarification_sidecar = self._clarification_sidecar(
                        task_id, current
                    )
                    if str(clarification_sidecar) != finalization.get(
                        "clarification_sidecar"
                    ):
                        raise ValueError(
                            "E_STATE_RECOVERY: clarification sidecar binding changed"
                        )
                    self._clear_clarification_fields(current)
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
                if finalization.get("clarification_reframe") is True:
                    current["clarification_resolution_digest"] = finalization.get(
                        "resolution_digest"
                    )
                current.pop("finalization", None)
                current.pop("finalizing_lease_digest", None)
                _atomic_json(state_path, current)
                if clarification_sidecar is not None:
                    self._unlink_clarification_sidecar(
                        clarification_sidecar
                    )
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
