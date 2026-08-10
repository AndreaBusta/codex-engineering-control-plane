"""Read-only Git state inspection and deterministic preflight gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
import subprocess

from control_plane.repository import (
    assert_no_external_git_filters,
    trusted_git_argv,
    trusted_git_environment,
)


@dataclass(frozen=True)
class GateError:
    """One blocking preflight error."""

    code: str
    message: str


@dataclass(frozen=True)
class GateCheck:
    """One observed preflight condition."""

    code: str
    ok: bool
    message: str


@dataclass
class GateResult:
    """Stable result returned by the preflight API and CLI."""

    ok: bool
    mode: str
    facts: dict[str, Any]
    checks: list[GateCheck]
    errors: list[GateError]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "command": "preflight",
            "ok": self.ok,
            "mode": self.mode,
            "facts": self.facts,
            "checks": [asdict(check) for check in self.checks],
            "issues": [],
            "errors": [asdict(error) for error in self.errors],
        }


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            trusted_git_argv(repo, arguments),
            check=False,
            capture_output=True,
            text=True,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(
            args=["/usr/bin/git", *arguments],
            returncode=128,
            stdout="",
            stderr="",
        )


def _is_shallow_repository(repo: Path) -> bool | None:
    """Return shallow state, or None when the Git fact is unavailable."""

    result = _git(repo, "rev-parse", "--is-shallow-repository")
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _check(
    checks: list[GateCheck],
    code: str,
    condition: bool,
    success: str,
    failure: str,
) -> None:
    checks.append(GateCheck(code, condition, success if condition else failure))


def _error(errors: list[GateError], code: str, message: str) -> None:
    if not any(error.code == code for error in errors):
        errors.append(GateError(code, message))


def evaluate_preflight(
    repo: Path, policy: Mapping[str, Any], mode: str
) -> GateResult:
    """Observe a repository and evaluate read, write, or release gates.

    This function never fetches, checks out, stages, commits, resets, pushes, or
    otherwise changes repository state.
    """

    if mode not in {"read", "write", "release"}:
        raise ValueError(f"Unsupported preflight mode: {mode}")

    remote = str(policy["git"]["remote"])
    base_branch = str(policy["git"]["base_branch"])
    remote_base = f"{remote}/{base_branch}"
    facts: dict[str, Any] = {
        "repository": str(repo.resolve()),
        "branch": None,
        "head": None,
        "remote": remote,
        "base_branch": base_branch,
        "remote_base": remote_base,
        "dirty": None,
        "detached": None,
        "unborn": None,
        "remote_present": None,
        "remote_base_present": None,
        "ahead": None,
        "behind": None,
    }
    checks: list[GateCheck] = []
    errors: list[GateError] = []

    repository_result = _git(repo, "rev-parse", "--show-toplevel")
    is_repository = repository_result.returncode == 0
    _check(
        checks,
        "GIT_REPOSITORY",
        is_repository,
        "Git repository detected.",
        "The target is not inside a Git repository.",
    )
    if not is_repository:
        _error(
            errors,
            "E_GIT_NOT_REPOSITORY",
            "The target is not inside a Git repository.",
        )
        return GateResult(False, mode, facts, checks, errors)

    root = Path(repository_result.stdout.strip()).resolve()
    facts["repository"] = str(root)

    head_result = _git(root, "rev-parse", "--verify", "HEAD")
    unborn = head_result.returncode != 0
    facts["unborn"] = unborn
    if not unborn:
        facts["head"] = head_result.stdout.strip()
    _check(
        checks,
        "GIT_COMMITTED_HEAD",
        not unborn,
        "HEAD resolves to a commit.",
        "The repository has no committed HEAD yet.",
    )

    branch_result = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    detached = branch is None
    facts["branch"] = branch
    facts["detached"] = detached
    _check(
        checks,
        "GIT_ATTACHED_BRANCH",
        not detached,
        f"Attached branch detected: {branch}.",
        "HEAD is detached.",
    )

    try:
        assert_no_external_git_filters(root)
    except ValueError:
        status_result = subprocess.CompletedProcess(
            args=["/usr/bin/git", "status"],
            returncode=128,
            stdout="",
            stderr="",
        )
    else:
        status_result = _git(root, "status", "--porcelain=v1", "-z")
    status_observed = status_result.returncode == 0
    dirty = bool(status_result.stdout) if status_observed else None
    facts["dirty"] = dirty
    _check(
        checks,
        "GIT_CLEAN_TREE",
        status_observed and not dirty,
        "Working tree is clean.",
        (
            "Working tree has tracked or untracked changes."
            if status_observed
            else "Working tree status could not be observed."
        ),
    )
    if not status_observed:
        _error(
            errors,
            "E_GIT_STATUS_FAILED",
            "Working tree status could not be observed safely.",
        )

    remotes_result = _git(root, "remote")
    remotes = set(remotes_result.stdout.split())
    remote_present = remote in remotes
    facts["remote_present"] = remote_present
    _check(
        checks,
        "GIT_REMOTE_PRESENT",
        remote_present,
        f"Configured remote is present: {remote}.",
        f"Configured remote is missing: {remote}.",
    )

    remote_ref = f"refs/remotes/{remote}/{base_branch}"
    remote_base_result = _git(root, "show-ref", "--verify", "--quiet", remote_ref)
    remote_base_present = remote_base_result.returncode == 0
    facts["remote_base_present"] = remote_base_present
    _check(
        checks,
        "GIT_REMOTE_BASE_PRESENT",
        remote_base_present,
        f"Remote base reference is present: {remote_base}.",
        f"Remote base reference is missing: {remote_base}.",
    )

    if not unborn and remote_base_present:
        divergence_result = _git(
            root, "rev-list", "--left-right", "--count", f"{remote_base}...HEAD"
        )
        try:
            if divergence_result.returncode != 0:
                raise ValueError
            left, right = divergence_result.stdout.split()
            facts["behind"] = int(left)
            facts["ahead"] = int(right)
            _check(
                checks,
                "GIT_BASE_CONTAINED",
                facts["behind"] == 0,
                f"HEAD contains the current {remote_base}.",
                f"HEAD is behind or diverged from {remote_base}.",
            )
        except (TypeError, ValueError):
            _check(
                checks,
                "GIT_BASE_CONTAINED",
                False,
                f"HEAD contains the current {remote_base}.",
                f"Divergence from {remote_base} could not be observed.",
            )
            _error(
                errors,
                "E_GIT_DIVERGENCE_UNKNOWN",
                f"Divergence from {remote_base} could not be observed safely.",
            )

    if mode == "read":
        return GateResult(not errors, mode, facts, checks, errors)

    if unborn:
        _error(
            errors,
            "E_GIT_UNBORN",
            "A committed baseline is required before write or release work.",
        )
    if not remote_present:
        _error(
            errors,
            "E_GIT_NO_REMOTE",
            f"Configured remote is missing: {remote}.",
        )
    if remote_present and not remote_base_present:
        _error(
            errors,
            "E_GIT_NO_REMOTE_BASE",
            f"Remote base reference is missing: {remote_base}.",
        )
    if detached:
        _error(
            errors,
            "E_GIT_DETACHED",
            "A real branch is required before write or release work.",
        )
    if dirty:
        _error(
            errors,
            "E_GIT_DIRTY",
            "The working tree must be clean before starting this transition.",
        )

    if mode == "write":
        if branch == base_branch:
            _error(
                errors,
                "E_GIT_BASE_BRANCH",
                "Writing directly on the protected base branch is forbidden.",
            )
        if facts["behind"] not in (None, 0):
            _error(
                errors,
                "E_GIT_BEHIND_BASE",
                f"The branch does not contain the current {remote_base}.",
            )

    if mode == "release":
        if branch != base_branch:
            _error(
                errors,
                "E_RELEASE_WRONG_BRANCH",
                f"Release preflight requires the protected base branch: {base_branch}.",
            )
        if facts["ahead"] not in (0,) or facts["behind"] not in (0,):
            _error(
                errors,
                "E_RELEASE_NOT_SYNCED",
                f"Local {base_branch} must exactly match {remote_base}.",
            )

    return GateResult(not errors, mode, facts, checks, errors)


def verify_refreshed_base_containment(
    *,
    effect_plan: object,
    integration_receipt: object,
    refresh_receipt: object,
) -> object:
    """Read only the exact refreshed remote ref and prove merge containment."""

    from control_plane.host_bridge import (
        BaseRefreshReceiptV1,
        IntegrationEffectPlanV1,
        IntegrationReceiptV1,
        build_base_verification_receipt,
    )

    if (
        type(effect_plan) is not IntegrationEffectPlanV1
        or type(integration_receipt) is not IntegrationReceiptV1
        or type(refresh_receipt) is not BaseRefreshReceiptV1
    ):
        raise ValueError("E_BASE_VERIFICATION: exact contracts are required")
    effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
    integration_receipt = IntegrationReceiptV1.from_dict(
        integration_receipt.to_dict()
    )
    refresh_receipt = BaseRefreshReceiptV1.from_dict(
        refresh_receipt.to_dict()
    )

    def blocked(
        reason_code: str,
        *,
        observed_base_sha: str | None = None,
        contained: bool | None = None,
    ) -> object:
        return build_base_verification_receipt(
            effect_plan=effect_plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh_receipt,
            status="BLOCKED",
            reason_code=reason_code,
            observed_base_sha=observed_base_sha,
            contained=contained,
        )

    expected_base_ref = (
        f"refs/remotes/{effect_plan.remote}/{effect_plan.base}"
    )
    if (
        refresh_receipt.task_id != effect_plan.task_id
        or refresh_receipt.task_digest != effect_plan.task_digest
        or refresh_receipt.run_plan_digest != effect_plan.run_plan_digest
        or refresh_receipt.repository != effect_plan.repository
        or refresh_receipt.remote != effect_plan.remote
        or refresh_receipt.remote_url != effect_plan.remote_url
        or refresh_receipt.remote_url_digest != effect_plan.remote_url_digest
        or refresh_receipt.remote_identity != effect_plan.remote_identity
        or refresh_receipt.remote_identity_digest
        != effect_plan.remote_identity_digest
        or refresh_receipt.base != effect_plan.base
        or refresh_receipt.base_ref != expected_base_ref
        or refresh_receipt.policy_digest != effect_plan.policy_digest
        or refresh_receipt.effect_plan_digest != effect_plan.plan_digest
        or refresh_receipt.integration_receipt_digest
        != integration_receipt.receipt_digest
        or refresh_receipt.merge_sha != integration_receipt.observed_merge_sha
    ):
        raise ValueError("E_BASE_VERIFICATION: receipt binding drifted")
    if refresh_receipt.status != "PASS":
        return blocked("BASE_REFRESH_UNKNOWN")
    repository = Path(effect_plan.repository)
    ref = _git(repository, "rev-parse", "--verify", f"{refresh_receipt.base_ref}^{{commit}}")
    if ref.returncode != 0:
        return blocked("BASE_REF_MISSING")
    observed_base_sha = ref.stdout.strip()
    if observed_base_sha != refresh_receipt.observed_sha:
        return blocked(
            "BASE_REF_MISMATCH", observed_base_sha=observed_base_sha
        )
    merge = _git(
        repository,
        "cat-file",
        "-e",
        f"{integration_receipt.observed_merge_sha}^{{commit}}",
    )
    if merge.returncode != 0:
        return blocked(
            "BASE_CONTAINMENT_UNKNOWN", observed_base_sha=observed_base_sha
        )
    containment = _git(
        repository,
        "merge-base",
        "--is-ancestor",
        str(integration_receipt.observed_merge_sha),
        refresh_receipt.base_ref,
    )
    if containment.returncode == 1:
        if _is_shallow_repository(repository) is not False:
            return blocked(
                "BASE_CONTAINMENT_UNKNOWN",
                observed_base_sha=observed_base_sha,
            )
        return blocked(
            "BASE_MERGE_NOT_CONTAINED",
            observed_base_sha=observed_base_sha,
            contained=False,
        )
    if containment.returncode != 0:
        return blocked(
            "BASE_CONTAINMENT_UNKNOWN", observed_base_sha=observed_base_sha
        )
    return build_base_verification_receipt(
        effect_plan=effect_plan,
        integration_receipt=integration_receipt,
        refresh_receipt=refresh_receipt,
        status="PASS",
        reason_code="BASE_CONTAINED",
        observed_base_sha=observed_base_sha,
        contained=True,
    )


def revalidate_base_verification_receipt(
    receipt: object,
    *,
    refresh_receipt: object,
) -> bool:
    """Re-read one exact PASS refresh and its proof; never mutate or fetch."""

    from control_plane.host_bridge import (
        BaseRefreshReceiptV1,
        BaseVerificationReceiptV1,
    )

    if (
        type(receipt) is not BaseVerificationReceiptV1
        or type(refresh_receipt) is not BaseRefreshReceiptV1
    ):
        return False
    try:
        receipt = BaseVerificationReceiptV1.from_dict(receipt.to_dict())
        refresh_receipt = BaseRefreshReceiptV1.from_dict(
            refresh_receipt.to_dict()
        )
    except ValueError:
        return False
    if (
        receipt.status != "PASS"
        or receipt.contained is not True
        or refresh_receipt.status != "PASS"
        or receipt.refresh_receipt_digest != refresh_receipt.receipt_digest
        or receipt.task_id != refresh_receipt.task_id
        or receipt.task_digest != refresh_receipt.task_digest
        or receipt.run_plan_digest != refresh_receipt.run_plan_digest
        or receipt.repository != refresh_receipt.repository
        or receipt.remote != refresh_receipt.remote
        or receipt.base != refresh_receipt.base
        or receipt.base_ref != refresh_receipt.base_ref
        or receipt.policy_digest != refresh_receipt.policy_digest
        or receipt.effect_plan_digest != refresh_receipt.effect_plan_digest
        or receipt.integration_receipt_digest
        != refresh_receipt.integration_receipt_digest
        or receipt.merge_sha != refresh_receipt.merge_sha
        or receipt.observed_at != refresh_receipt.observed_at
        or refresh_receipt.observed_ref != receipt.base_ref
        or refresh_receipt.observed_sha != receipt.observed_base_sha
    ):
        return False
    repository = Path(receipt.repository)
    ref = _git(
        repository,
        "rev-parse",
        "--verify",
        f"{receipt.base_ref}^{{commit}}",
    )
    if ref.returncode != 0 or ref.stdout.strip() != receipt.observed_base_sha:
        return False
    merge = _git(repository, "cat-file", "-e", f"{receipt.merge_sha}^{{commit}}")
    if merge.returncode != 0:
        return False
    containment = _git(
        repository,
        "merge-base",
        "--is-ancestor",
        receipt.merge_sha,
        receipt.base_ref,
    )
    return containment.returncode == 0
