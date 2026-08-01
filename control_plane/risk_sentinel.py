"""Tri-state, read-only engineering risk evaluation.

The sentinel deliberately distinguishes a demonstrated failure from an
unavailable observation.  It never upgrades serialized policy, route, task, or
authorization material into host authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import stat
import subprocess
import tomllib
from typing import Any, Iterable, Mapping

from control_plane.contracts import SHA256_DIGEST, contract_digest
from control_plane.git_state import evaluate_preflight
from control_plane.intake import render_interaction_recommendation
from control_plane.lifecycle import TaskLease, TaskStore
from control_plane.lockfile import validate_lock
from control_plane.policy import (
    GoverningPolicy,
    _governing_policy_is_issued,
    validate_policy,
)
from control_plane.project_profiles import detect_project_profile
from control_plane.repository import (
    RepositoryError,
    discover_repository,
    git_common_dir,
    git_environment,
    worktree_git_dir,
)


PASS = "PASS"
UNKNOWN = "UNKNOWN"
FAIL = "FAIL"
_STATUS_RANK = {PASS: 0, UNKNOWN: 1, FAIL: 2}


@dataclass(frozen=True)
class RiskCheck:
    code: str
    status: str
    message: str
    facts: dict[str, Any]


@dataclass(frozen=True)
class RiskDimension:
    status: str
    checks: tuple[RiskCheck, ...]
    errors: tuple[dict[str, str], ...]

    def __post_init__(self) -> None:
        expected = aggregate_status(check.status for check in self.checks)
        if self.errors and expected == PASS:
            expected = UNKNOWN
        if self.status != expected:
            raise ValueError(
                "RS_DIMENSION_STATUS: dimension status does not match its evidence"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
            "errors": [dict(error) for error in self.errors],
        }


@dataclass(frozen=True)
class RiskStatus:
    command: str
    dimensions: dict[str, RiskDimension]
    facts: dict[str, Any]
    errors: tuple[dict[str, str], ...]

    @property
    def status(self) -> str:
        return aggregate_status(
            self.dimensions[name].status for name in ("local", "remote")
        )

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> dict[str, Any]:
        if set(self.dimensions) != {"local", "remote"}:
            raise ValueError(
                "RS_DIMENSIONS: risk status requires exact local and remote dimensions"
            )
        return {
            "schema_version": 1,
            "command": self.command,
            "ok": self.ok,
            "status": self.status,
            "dimensions": {
                name: self.dimensions[name].to_dict()
                for name in ("local", "remote")
            },
            "facts": dict(self.facts),
            "errors": [dict(error) for error in self.errors],
        }


def aggregate_status(statuses: Iterable[str]) -> str:
    """Return the closed tri-state value with FAIL > UNKNOWN > PASS."""

    result = PASS
    for status in statuses:
        if status not in _STATUS_RANK:
            raise ValueError(f"RS_STATUS: unsupported risk status {status!r}")
        if _STATUS_RANK[status] > _STATUS_RANK[result]:
            result = status
    return result


def _check(
    code: str,
    status: str,
    message: str,
    **facts: Any,
) -> RiskCheck:
    if status not in _STATUS_RANK:
        raise ValueError("RS_STATUS: invalid check status")
    return RiskCheck(code=code, status=status, message=message, facts=facts)


def _dimension(
    checks: Iterable[RiskCheck],
    *,
    errors: Iterable[dict[str, str]] = (),
) -> RiskDimension:
    closed_checks = tuple(checks)
    closed_errors = tuple(dict(error) for error in errors)
    status = aggregate_status(check.status for check in closed_checks)
    if closed_errors and status == PASS:
        status = UNKNOWN
    return RiskDimension(status=status, checks=closed_checks, errors=closed_errors)


def _unknown_local_dimension(
    message: str,
    *,
    task_state: Mapping[str, Any] | None = None,
    authority_not_applicable: bool = False,
) -> RiskDimension:
    checks = (
        _check("RS_LOCAL_POLICY", UNKNOWN, message),
        _check(
            "RS_LOCAL_LOCK",
            UNKNOWN,
            "Governing lock provenance is not observable.",
        ),
        _check(
            "RS_LOCAL_REPOSITORY",
            UNKNOWN,
            "Repository identity is not observable under governing policy.",
        ),
        _check(
            "RS_LOCAL_BASE_BRANCH",
            UNKNOWN,
            "Protected base branch is not observable.",
        ),
        _check("RS_LOCAL_DETACHED", UNKNOWN, "HEAD attachment is not observable."),
        _check(
            "RS_LOCAL_BASE_DIVERGENCE",
            UNKNOWN,
            "Base divergence is not observable.",
        ),
        _check("RS_LOCAL_DIRTY", UNKNOWN, "Working-tree state is not observable."),
        _check(
            "RS_LOCAL_HOOK_PATH",
            UNKNOWN,
            "Managed Git hook path is not observable.",
        ),
        _check(
            "RS_LOCAL_HOOK_DIGEST",
            UNKNOWN,
            "Managed Git hook digests are not observable.",
        ),
        _check("RS_HOOK_TRUST", UNKNOWN, "Hook trust is not observable."),
        _check("RS_HOOK_MODE", UNKNOWN, "Hook mode is not observable."),
        _check(
            "RS_CLARIFICATION_REQUIRED",
            UNKNOWN,
            "Current clarification state requires native host context.",
        ),
        _check(
            "RS_AUTHORITY_REQUIRED",
            PASS if authority_not_applicable else UNKNOWN,
            (
                "No task, route, or protected effect was requested."
                if authority_not_applicable
                else "Current authority requires native host context."
            ),
            **(
                {"reason": "NOT_APPLICABLE"}
                if authority_not_applicable
                else {}
            ),
        ),
        _check("RS_PROFILE", UNKNOWN, "Project profile is not observable."),
        _task_check(task_state),
    )
    return _dimension(
        checks,
        errors=(
            {
                "code": "RS_LOCAL_GOVERNING_POLICY_UNAVAILABLE",
                "message": message,
            },
        ),
    )


def _unanchored_local_dimension(
    repo: Path | str,
    *,
    task_state: Mapping[str, Any] | None,
    message: str,
) -> RiskDimension:
    """Observe anchor-independent facts while base policy remains UNKNOWN."""

    checks = list(
        _unknown_local_dimension(
            message,
            task_state=task_state,
            authority_not_applicable=task_state is None,
        ).checks
    )
    replacements: dict[str, RiskCheck] = {}
    try:
        root = discover_repository(Path(repo))
    except RepositoryError:
        replacements["RS_LOCAL_REPOSITORY"] = _check(
            "RS_LOCAL_REPOSITORY",
            FAIL,
            "The target is not inside a Git repository.",
        )
        return _dimension(
            tuple(replacements.get(check.code, check) for check in checks),
            errors=(
                {
                    "code": "RS_LOCAL_GOVERNING_POLICY_UNAVAILABLE",
                    "message": message,
                },
            ),
        )
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=git_environment(),
        )
        branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=git_environment(),
        )
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=git_environment(),
        )
    except OSError:
        head = branch = status = None
    repository_observed = head is not None and head.returncode == 0
    replacements["RS_LOCAL_REPOSITORY"] = _check(
        "RS_LOCAL_REPOSITORY",
        PASS if repository_observed else UNKNOWN,
        (
            "Repository and worktree were observed."
            if repository_observed
            else "Git repository observation failed."
        ),
        head=(head.stdout.strip() if repository_observed else None),
    )
    detached = (
        None
        if branch is None
        else branch.returncode != 0
    )
    replacements["RS_LOCAL_DETACHED"] = _check(
        "RS_LOCAL_DETACHED",
        UNKNOWN if detached is None else (FAIL if detached else PASS),
        (
            "HEAD is attached to a branch."
            if detached is False
            else (
                "HEAD is detached."
                if detached is True
                else "HEAD attachment is not observable."
            )
        ),
        detached=detached,
    )
    dirty = (
        None
        if status is None or status.returncode != 0
        else bool(status.stdout)
    )
    lease_valid = False
    replacements["RS_LOCAL_DIRTY"] = _check(
        "RS_LOCAL_DIRTY",
        (
            UNKNOWN
            if dirty is None
            else (PASS if not dirty or lease_valid else FAIL)
        ),
        (
            "Working tree is clean or fully covered by a valid lease."
            if dirty is False or lease_valid
            else (
                "Working tree is dirty without a complete valid lease."
                if dirty is True
                else "Working-tree status is not observable."
            )
        ),
        dirty=dirty,
        lease_valid=lease_valid,
    )
    clarification, authority, host_evidence = _local_authority_checks(
        task_state
    )
    replacements[clarification.code] = clarification
    replacements[authority.code] = authority
    try:
        profile = detect_project_profile(root)
        profile_status = UNKNOWN if profile.get("truncated") else PASS
    except (OSError, ValueError):
        profile = {}
        profile_status = UNKNOWN
    replacements["RS_PROFILE"] = _check(
        "RS_PROFILE",
        profile_status,
        (
            "Project profile scan is complete."
            if profile_status == PASS
            else "Project profile scan is incomplete."
        ),
        kind=profile.get("kind"),
        profiles=profile.get("profiles"),
        truncated=profile.get("truncated"),
    )
    replacements["RS_TASK_STATE"] = _task_check(
        task_state,
        repo=root,
        host_evidence=host_evidence,
    )
    return _dimension(
        tuple(replacements.get(check.code, check) for check in checks),
        errors=(
            {
                "code": "RS_LOCAL_GOVERNING_POLICY_UNAVAILABLE",
                "message": message,
            },
        ),
    )
def _git_config_values(repo: Path, key: str) -> tuple[int, tuple[str, ...]]:
    try:
        completed = subprocess.run(
            ["git", "config", "--get-all", key],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            env=git_environment(),
        )
    except OSError:
        return 128, ()
    values = tuple(
        line.strip() for line in completed.stdout.splitlines() if line.strip()
    )
    return completed.returncode, values


def _lock_snapshot(repo: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = repo / ".codex" / "control-plane.lock"
    try:
        raw = path.read_bytes()
        if path.is_symlink() or len(raw) > 131_072:
            return None, None
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, None
    return parsed, f"sha256:{sha256(raw).hexdigest()}"


def _file_matches_digest(
    path: Path, expected: object, *, executable: bool
) -> str:
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or not isinstance(expected, str)
            or SHA256_DIGEST.fullmatch(expected) is None
        ):
            return FAIL
        if executable and not (path.stat().st_mode & stat.S_IXUSR):
            return FAIL
        actual = f"sha256:{sha256(path.read_bytes()).hexdigest()}"
    except OSError:
        return UNKNOWN
    return PASS if actual == expected else FAIL


def _installed_guard_snapshot(
    repo: Path,
    hook_paths: tuple[str, ...],
    policy: GoverningPolicy,
) -> tuple[str | None, str]:
    if len(hook_paths) != 1:
        return None, FAIL
    hooks_path = Path(hook_paths[0])
    if (
        not hooks_path.is_absolute()
        or hooks_path.name != "git-hooks"
        or hooks_path.parent.name.startswith("sha256:") is False
    ):
        return None, FAIL
    try:
        from control_plane.git_guards import (
            _runtime_digest,
            _validate_snapshot,
        )

        observed = _validate_snapshot(
            canonical_repo=repo,
            common_git_dir=git_common_dir(repo),
            manifest_digest=hooks_path.parent.name,
        )
        records = observed["artifacts"]
        if (
            str(observed["install_root"] / "git-hooks")
            != str(hooks_path)
            or records["policy"]["digest"] != policy.policy_digest
            or records["lock"]["digest"] != policy.lock_digest
            or _runtime_digest(records) != policy.runtime_digest
            or observed["manifest"]["governing_base_commit"]
            != policy.governing_base_commit
            or observed["manifest"]["git"]["remote_repository"]
            != policy.remote_repository
        ):
            return None, FAIL
    except (OSError, TypeError, ValueError):
        return None, FAIL
    return str(hooks_path), PASS


def _task_check(
    task_state: Mapping[str, Any] | None,
    *,
    repo: Path | str | None = None,
    host_evidence: Mapping[str, Any] | None = None,
) -> RiskCheck:
    if task_state is None:
        if host_evidence is not None:
            return _check(
                "RS_TASK_STATE",
                FAIL,
                "Native task context was supplied without matching task state.",
            )
        return _check(
            "RS_TASK_STATE",
            PASS,
            "No task state was requested.",
            reason="NOT_APPLICABLE",
        )
    if task_state.get("_unobserved") is True:
        return _check(
            "RS_TASK_STATE",
            UNKNOWN,
            "Requested task state is not observable.",
            task_id=task_state.get("task_id"),
        )
    task_id = task_state.get("task_id")
    task_digest = task_state.get("task_digest")
    state = task_state.get("state")
    generation = task_state.get("generation")
    valid = (
        isinstance(task_id, str)
        and bool(task_id)
        and isinstance(task_digest, str)
        and SHA256_DIGEST.fullmatch(task_digest) is not None
        and isinstance(state, str)
        and bool(state)
        and isinstance(generation, int)
        and not isinstance(generation, bool)
        and generation >= 0
    )
    if not valid:
        return _check(
            "RS_TASK_STATE",
            FAIL,
            "Task state is invalid or drifted.",
            task_id=task_id if isinstance(task_id, str) else None,
        )
    if repo is None:
        return _check(
            "RS_TASK_STATE",
            UNKNOWN,
            "Requested task state cannot be authenticated without a repository.",
            task_id=task_id,
        )
    try:
        root = discover_repository(Path(repo))
        durable = TaskStore(worktree_git_dir(root)).status(str(task_id))
    except (RepositoryError, OSError, ValueError) as error:
        status = (
            UNKNOWN
            if str(error).startswith(
                ("E_TASK_NOT_FOUND:", "E_STATE_NOT_FOUND:")
            )
            else FAIL
        )
        return _check(
            "RS_TASK_STATE",
            status,
            (
                "Requested task state is not observable."
                if status == UNKNOWN
                else "Durable task state is invalid or belongs to another runtime."
            ),
            task_id=task_id,
        )
    supplied_digest = contract_digest(dict(task_state))
    matches = supplied_digest == contract_digest(durable)
    if not matches:
        return _check(
            "RS_TASK_STATE",
            FAIL,
            "Serialized task state differs from the durable record.",
            task_id=task_id,
        )
    if host_evidence is None:
        return _check(
            "RS_TASK_STATE",
            UNKNOWN,
            "Durable task state lacks a current opaque host attestation.",
            task_id=task_id,
        )
    anchored = (
        bool(host_evidence)
        and host_evidence.get("task_id") == task_id
        and host_evidence.get("task_digest") == task_digest
        and host_evidence.get("task_state_digest") == supplied_digest
        and host_evidence.get("decision_digest")
        == task_state.get("decision_digest")
        and host_evidence.get("branch") == task_state.get("branch")
    )
    return _check(
        "RS_TASK_STATE",
        PASS if anchored else FAIL,
        (
            "Task state matches its durable record and opaque host anchor."
            if anchored
            else "Task state differs from the current opaque host anchor."
        ),
        task_id=task_id,
    )


def _git_changed_paths(repo: Path) -> tuple[str, ...] | None:
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "-z", "HEAD"],
            cwd=repo,
            check=False,
            capture_output=True,
            env=git_environment(),
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=repo,
            check=False,
            capture_output=True,
            env=git_environment(),
        )
    except OSError:
        return None
    if changed.returncode != 0 or untracked.returncode != 0:
        return None
    paths: set[str] = set()
    for payload in (changed.stdout, untracked.stdout):
        if not isinstance(payload, bytes):
            return None
        try:
            paths.update(
                item.decode("utf-8", errors="strict")
                for item in payload.split(b"\0")
                if item
            )
        except UnicodeDecodeError:
            return None
    return tuple(sorted(paths))


def _lease_covers_dirty_tree(
    repo: Path,
    *,
    task_state: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
    branch: object,
    host_evidence: Mapping[str, Any] | None,
) -> bool | None:
    if task_state is None or not isinstance(branch, str) or not branch:
        return False
    task_check = _task_check(
        task_state,
        repo=repo,
        host_evidence=host_evidence,
    )
    if task_check.status != PASS:
        return False
    if not host_evidence:
        return False
    expected_lease_digest = host_evidence.get("lease_digest")
    expected_session_id = host_evidence.get("session_id")
    if (
        not isinstance(expected_lease_digest, str)
        or SHA256_DIGEST.fullmatch(expected_lease_digest) is None
        or not isinstance(expected_session_id, str)
        or not expected_session_id
    ):
        return False
    task_id = str(task_state["task_id"])
    state_dir = worktree_git_dir(repo)
    changed_paths = _git_changed_paths(repo)
    if changed_paths is None:
        return None
    try:
        validated = TaskLease.validate(
            state_dir,
            task_id=task_id,
            worktree=str(repo),
            branch=branch,
            session_id=expected_session_id,
            policy_digest=contract_digest(policy),
            changed_paths=list(changed_paths),
        )
    except ValueError:
        return False
    except OSError:
        return None
    return (
        isinstance(validated, Mapping)
        and validated.get("lease_digest") == expected_lease_digest
    )


def _local_authority_checks(
    task_state: Mapping[str, Any] | None,
) -> tuple[RiskCheck, RiskCheck, None]:
    """Return honest local-audit authority state without a host adapter."""

    clarification = _check(
        "RS_CLARIFICATION_REQUIRED",
        UNKNOWN,
        "Clarification cannot be proven without a native host capability.",
    )
    authority = (
        _check(
            "RS_AUTHORITY_REQUIRED",
            PASS,
            "No active task or protected effect was supplied.",
            reason="NOT_APPLICABLE",
        )
        if task_state is None
        else _check(
            "RS_AUTHORITY_REQUIRED",
            UNKNOWN,
            "Protected-effect authority is not observable in local-audit mode.",
        )
    )
    return clarification, authority, None


def evaluate_local_risk(
    repo: Path | str,
    policy: GoverningPolicy,
    *,
    task_state: Mapping[str, Any] | None = None,
    route_decision_hint: Mapping[str, Any] | None = None,
) -> RiskDimension:
    """Evaluate every normative local check without treating read mode as safe."""

    del route_decision_hint
    if type(policy) is not GoverningPolicy:
        if policy is None:
            return _unanchored_local_dimension(
                repo,
                task_state=task_state,
                message=(
                    "A host-bound governing policy observation is unavailable."
                ),
            )
        return _dimension(
            (
                _check(
                    "RS_LOCAL_POLICY",
                    FAIL,
                    "Serialized or candidate policy cannot govern local risk.",
                ),
                *tuple(
                    check
                    for check in _unknown_local_dimension(
                        "Governing policy provenance is invalid.",
                        task_state=task_state,
                    ).checks
                    if check.code != "RS_LOCAL_POLICY"
                ),
            )
        )
    policy_mapping = policy.policy
    if (
        not _governing_policy_is_issued(policy)
        or validate_policy(policy_mapping)
        or contract_digest(policy_mapping) is None
    ):
        return _dimension(
            (
                _check(
                    "RS_LOCAL_POLICY",
                    FAIL,
                    "Governing policy is invalid, drifted, or not host-issued.",
                ),
                *tuple(
                    check
                    for check in _unknown_local_dimension(
                        "Governing policy is invalid.",
                        task_state=task_state,
                    ).checks
                    if check.code != "RS_LOCAL_POLICY"
                ),
            )
        )

    checks: list[RiskCheck] = [
        _check(
            "RS_LOCAL_POLICY",
            PASS,
            "Host-bound governing policy is valid.",
            governing_base_commit=policy.governing_base_commit,
        )
    ]
    try:
        root = discover_repository(Path(repo))
    except RepositoryError:
        checks.extend(
            (
                _check(
                    "RS_LOCAL_LOCK",
                    UNKNOWN,
                    "Control-plane lock cannot be observed outside a repository.",
                ),
                _check(
                    "RS_LOCAL_REPOSITORY",
                    FAIL,
                    "The target is not inside a Git repository.",
                ),
            )
        )
        checks.extend(
            check
            for check in _unknown_local_dimension(
                "Repository-dependent state is unavailable.",
                task_state=task_state,
            ).checks
            if check.code not in {"RS_LOCAL_POLICY", "RS_LOCAL_LOCK", "RS_LOCAL_REPOSITORY"}
        )
        return _dimension(checks)

    lock, lock_digest = _lock_snapshot(root)
    lock_issues = validate_lock(root)
    if lock is None:
        lock_status = FAIL
        lock_message = "Control-plane lock is absent or invalid."
    elif lock_digest != policy.lock_digest or lock_issues:
        lock_status = FAIL
        lock_message = "Control-plane lock or one of its digests drifted."
    else:
        lock_status = PASS
        lock_message = "Control-plane lock and digests match governing policy."
    checks.append(
        _check(
            "RS_LOCAL_LOCK",
            lock_status,
            lock_message,
            lock_digest=lock_digest,
        )
    )

    try:
        preflight = evaluate_preflight(root, policy_mapping, mode="read")
        facts = preflight.facts
    except (KeyError, OSError, TypeError, ValueError):
        facts = {}
    repository_observed = bool(facts.get("repository")) and facts.get("head") is not None
    checks.append(
        _check(
            "RS_LOCAL_REPOSITORY",
            PASS if repository_observed else UNKNOWN,
            (
                "Repository and worktree were observed."
                if repository_observed
                else "Git repository observation failed."
            ),
            repository=facts.get("repository"),
            head=facts.get("head"),
        )
    )
    branch = facts.get("branch")
    base_branch = policy_mapping["git"]["base_branch"]
    clarification, authority, host_evidence = _local_authority_checks(
        task_state
    )
    checks.append(
        _check(
            "RS_LOCAL_BASE_BRANCH",
            UNKNOWN if branch is None else (FAIL if branch == base_branch else PASS),
            (
                "Current branch is a feature branch."
                if branch is not None and branch != base_branch
                else (
                    "Writing directly on the protected base branch is unsafe."
                    if branch == base_branch
                    else "Current branch is not observable."
                )
            ),
            branch=branch,
            base_branch=base_branch,
        )
    )
    detached = facts.get("detached")
    checks.append(
        _check(
            "RS_LOCAL_DETACHED",
            UNKNOWN if detached is None else (FAIL if detached else PASS),
            (
                "HEAD is attached to a branch."
                if detached is False
                else ("HEAD is detached." if detached is True else "HEAD attachment is not observable.")
            ),
            detached=detached,
        )
    )
    behind = facts.get("behind")
    checks.append(
        _check(
            "RS_LOCAL_BASE_DIVERGENCE",
            UNKNOWN if not isinstance(behind, int) else (FAIL if behind > 0 else PASS),
            (
                "HEAD contains the observed remote base."
                if behind == 0
                else (
                    "HEAD is behind or diverged from the observed remote base."
                    if isinstance(behind, int)
                    else "Remote-base divergence is not observable."
                )
            ),
            behind=behind,
        )
    )
    dirty = facts.get("dirty")
    lease_valid = (
        _lease_covers_dirty_tree(
            root,
            task_state=task_state,
            policy=policy_mapping,
            branch=branch,
            host_evidence=host_evidence,
        )
        if dirty is True
        else False
    )
    checks.append(
        _check(
            "RS_LOCAL_DIRTY",
            (
                UNKNOWN
                if dirty is None
                else (
                    PASS
                    if not dirty or lease_valid is True
                    else (UNKNOWN if lease_valid is None else FAIL)
                )
            ),
            (
                "Working tree is clean or fully covered by a valid lease."
                if dirty is False or lease_valid is True
                else (
                    "Working tree is dirty without a complete valid lease."
                    if dirty is True and lease_valid is False
                    else (
                        "Lease coverage is not observable."
                        if dirty is True
                        else "Working-tree status is not observable."
                    )
                )
            ),
            dirty=dirty,
            lease_valid=lease_valid,
        )
    )

    config_rc, hook_paths = _git_config_values(root, "core.hooksPath")
    locked_hook_path = (
        str(lock.get("managed_hooks_path"))
        if isinstance(lock, Mapping)
        and isinstance(lock.get("managed_hooks_path"), str)
        else None
    )
    installed_hook_path, installed_hook_status = _installed_guard_snapshot(
        root, hook_paths, policy
    )
    managed_hook_path = locked_hook_path or installed_hook_path
    if config_rc not in {0, 1}:
        hook_path_status = UNKNOWN
    elif (
        managed_hook_path is not None
        and hook_paths == (managed_hook_path,)
        and Path(managed_hook_path).is_absolute()
    ):
        hook_path_status = PASS
    else:
        hook_path_status = FAIL
    checks.append(
        _check(
            "RS_LOCAL_HOOK_PATH",
            hook_path_status,
            (
                "Git uses the exact managed hook snapshot."
                if hook_path_status == PASS
                else (
                    "Git hook configuration is not observable."
                    if hook_path_status == UNKNOWN
                    else "Git does not use the exact managed hook snapshot."
                )
            ),
            configured=list(hook_paths),
            managed=managed_hook_path,
        )
    )

    digests = lock.get("digests", {}) if isinstance(lock, Mapping) else {}
    hook_statuses = (
        _file_matches_digest(
            root / ".codex" / "hooks.json",
            digests.get("hooks") if isinstance(digests, Mapping) else None,
            executable=False,
        ),
        _file_matches_digest(
            root / ".codex" / "hooks" / "control_plane_hook.py",
            digests.get("hook_entrypoint") if isinstance(digests, Mapping) else None,
            executable=True,
        ),
        installed_hook_status,
    )
    hook_digest_status = aggregate_status(hook_statuses)
    checks.append(
        _check(
            "RS_LOCAL_HOOK_DIGEST",
            hook_digest_status,
            (
                "Hook artifacts match their locked digests."
                if hook_digest_status == PASS
                else (
                    "Hook artifact bytes could not be read."
                    if hook_digest_status == UNKNOWN
                    else "Hook artifacts are absent, non-executable, or drifted."
                )
            ),
        )
    )

    hook_trust = lock.get("hook_trust") if isinstance(lock, Mapping) else None
    trust_status = {
        "trusted": PASS,
        "pending_hook_trust": UNKNOWN,
        "rejected": FAIL,
        "invalid": FAIL,
    }.get(hook_trust, FAIL if hook_trust is not None else UNKNOWN)
    checks.append(
        _check(
            "RS_HOOK_TRUST",
            trust_status,
            {
                PASS: "Hook trust is user-reviewed.",
                UNKNOWN: "Hook trust remains pending or unobservable.",
                FAIL: "Hook trust is rejected or invalid.",
            }[trust_status],
            hook_trust=hook_trust,
        )
    )
    hook_mode = lock.get("hook_mode") if isinstance(lock, Mapping) else None
    mode_status = {
        "soft-enforce": PASS,
        "enforce": PASS,
        "audit": UNKNOWN,
        "disabled": FAIL,
        "invalid": FAIL,
    }.get(hook_mode, FAIL if hook_mode is not None else UNKNOWN)
    checks.append(
        _check(
            "RS_HOOK_MODE",
            mode_status,
            {
                PASS: "Hook mode provides local enforcement.",
                UNKNOWN: "Hook mode is audit-only or unobservable.",
                FAIL: "Hook mode is disabled or invalid.",
            }[mode_status],
            hook_mode=hook_mode,
        )
    )

    checks.extend((clarification, authority))
    try:
        profile = detect_project_profile(root)
        profile_status = UNKNOWN if profile.get("truncated") else PASS
    except (OSError, ValueError):
        profile = {}
        profile_status = UNKNOWN
    checks.append(
        _check(
            "RS_PROFILE",
            profile_status,
            (
                "Project profile scan is complete."
                if profile_status == PASS
                else "Project profile scan is incomplete."
            ),
            kind=profile.get("kind"),
            profiles=profile.get("profiles"),
            truncated=profile.get("truncated"),
        )
    )
    checks.append(
        _task_check(
            task_state,
            repo=root,
            host_evidence=host_evidence,
        )
    )
    return _dimension(checks)


def _interaction_view(
    route_decision_hint: Mapping[str, Any] | None,
) -> dict[str, Any]:
    interaction = (
        route_decision_hint.get("interaction")
        if isinstance(route_decision_hint, Mapping)
        else None
    )
    if not isinstance(interaction, Mapping):
        return render_interaction_recommendation(
            "normal", ["MODE_BOUNDED"]
        ).as_dict()
    mode = interaction.get("recommended_mode", "default")
    normalized_mode = "normal" if mode == "default" else mode
    reasons = interaction.get("reason_codes")
    try:
        return render_interaction_recommendation(
            str(normalized_mode),
            list(reasons) if isinstance(reasons, list) else ["MODE_BOUNDED"],
        ).as_dict()
    except ValueError:
        return render_interaction_recommendation(
            "normal", ["MODE_BOUNDED"]
        ).as_dict()


def evaluate_risk_status(
    repo: Path | str,
    policy: GoverningPolicy,
    *,
    task_state: Mapping[str, Any] | None = None,
    route_decision_hint: Mapping[str, Any] | None = None,
) -> RiskStatus:
    """Return separate local and remote dimensions with a closed aggregate."""

    local = evaluate_local_risk(
        repo,
        policy,
        task_state=task_state,
        route_decision_hint=route_decision_hint,
    )
    remote = RiskDimension(
        status=UNKNOWN,
        checks=(),
        errors=(
            {
                "code": "RS_REMOTE_NOT_OBSERVED",
                "message": (
                    "Remote protection or provenance is deferred in local-audit mode."
                ),
            },
        ),
    )
    profile_facts: dict[str, Any] = {}
    for check in local.checks:
        if check.code == "RS_PROFILE":
            profile_facts = dict(check.facts)
            break
    policy_mapping = (
        policy.policy if type(policy) is GoverningPolicy else {}
    )
    git_policy = (
        policy_mapping.get("git", {})
        if isinstance(policy_mapping, Mapping)
        else {}
    )
    facts = {
        "base_branch": git_policy.get("base_branch"),
        "remote": git_policy.get("remote"),
        "interaction": _interaction_view(route_decision_hint),
        "project_profile": profile_facts,
    }
    return RiskStatus(
        command="risk-status",
        dimensions={"local": local, "remote": remote},
        facts=facts,
        errors=(),
    )
