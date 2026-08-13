"""Pure schema-1 project policy loading and validation for Core."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import re
import tomllib

from control_plane.contracts import SHA256_DIGEST, contract_digest
from control_plane.core_types import (
    _copy_live_runtime_host_object,
    _register_runtime_host_object,
)


SUPPORTED_SCHEMA_VERSION = 1
ALLOWED_REASONING_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
ALLOWED_INTEGRATION_STRATEGIES = frozenset({"squash", "merge-commit", "rebase-merge"})
ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "": frozenset(
        {
            "schema_version",
            "project_name",
            "project_kind",
            "git",
            "reasoning",
            "documentation",
            "release",
            "gates",
        }
    ),
    "git": frozenset(
        {
            "remote",
            "base_branch",
            "require_pull_request",
            "allow_direct_base_push",
            "integration_strategy",
        }
    ),
    "reasoning": frozenset(
        {
            "model",
            "default",
            "plan",
            "subagent",
            "normal_max_workers",
            "sequential_default",
        }
    ),
    "documentation": frozenset({"require_impact_assessment"}),
    "release": frozenset(
        {"official_source", "require_manifest", "allow_local_official_release"}
    ),
    "gates": frozenset({"T0", "T1", "T2", "T3"}),
    "gates.T0": frozenset({"required"}),
    "gates.T1": frozenset({"required"}),
    "gates.T2": frozenset({"required"}),
    "gates.T3": frozenset({"required"}),
}


class GoverningPolicy:
    """Opaque installed-policy observation, not serializable authority."""

    __slots__ = (
        "policy",
        "policy_digest",
        "runtime_digest",
        "lock_digest",
        "governing_base_commit",
        "remote_repository",
        "__weakref__",
    )

    def __new__(cls, *_: object, **__: object) -> "GoverningPolicy":
        raise TypeError("GoverningPolicy is emitted only by local attestation")


def seal_governing_policy(
    policy: Mapping[str, Any],
    *,
    runtime_digest: str,
    lock_digest: str,
    governing_base_commit: str,
    remote_repository: str,
) -> GoverningPolicy:
    issues = validate_policy(policy)
    if issues:
        raise ValueError("E_GOVERNING_POLICY: policy is invalid")
    if any(
        not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
        for value in (runtime_digest, lock_digest)
    ) or re.fullmatch(r"[0-9a-f]{40}", governing_base_commit) is None:
        raise ValueError("E_GOVERNING_POLICY: installed binding is invalid")
    result = object.__new__(GoverningPolicy)
    result.policy = copy.deepcopy(dict(policy))
    result.policy_digest = contract_digest(policy)
    result.runtime_digest = runtime_digest
    result.lock_digest = lock_digest
    result.governing_base_commit = governing_base_commit
    result.remote_repository = remote_repository
    _register_runtime_host_object(result, "governing_policy")
    return result


def _governing_policy_is_issued(value: object) -> bool:
    return _governing_policy_snapshot(value) is not None


def _governing_policy_snapshot(value: object) -> dict[str, object] | None:
    if type(value) is not GoverningPolicy:
        return None
    return _copy_live_runtime_host_object(value, "governing_policy")


class PolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PolicyIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class RequiredCheckCandidate:
    name: str
    app_id: int | None


def parse_required_check_selector(value: str) -> RequiredCheckCandidate:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ValueError("P_REQUIRED_CHECK: selector is invalid")
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9 ._:/-]{0,127})(?:@([1-9][0-9]{0,9}))?", value)
    if match is None:
        raise ValueError("P_REQUIRED_CHECK: selector is invalid")
    return RequiredCheckCandidate(
        name=match.group(1),
        app_id=int(match.group(2)) if match.group(2) else None,
    )


def load_policy(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as policy_file:
            data = tomllib.load(policy_file)
    except FileNotFoundError as error:
        raise PolicyError("E_POLICY_NOT_FOUND", f"Project policy not found: {path}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PolicyError("E_POLICY_PARSE", f"Project policy could not be parsed: {path}") from error
    return data


def _value_at(policy: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = policy
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _missing_issues(policy: Mapping[str, Any]) -> list[PolicyIssue]:
    required_paths = (
        "schema_version",
        "project_name",
        "project_kind",
        "git.remote",
        "git.base_branch",
        "git.require_pull_request",
        "git.allow_direct_base_push",
        "git.integration_strategy",
        "reasoning.model",
        "reasoning.default",
        "reasoning.plan",
        "reasoning.subagent",
        "reasoning.normal_max_workers",
        "reasoning.sequential_default",
        "documentation.require_impact_assessment",
        "release.official_source",
        "release.require_manifest",
        "release.allow_local_official_release",
        "gates.T0.required",
        "gates.T1.required",
        "gates.T2.required",
        "gates.T3.required",
    )
    return [
        PolicyIssue("P_MISSING", path, f"Required policy key is missing: {path}")
        for path in required_paths
        if _value_at(policy, path) is None
    ]


def _unknown_issues(policy: Mapping[str, Any]) -> list[PolicyIssue]:
    issues: list[PolicyIssue] = []
    for path, allowed in ALLOWED_KEYS.items():
        section = policy if path == "" else _value_at(policy, path)
        if not isinstance(section, Mapping):
            continue
        for key in section:
            if key not in allowed:
                dotted = f"{path}.{key}" if path else str(key)
                issues.append(PolicyIssue("P_UNKNOWN", dotted, f"Unknown policy key for schema 1: {dotted}"))
    return issues


def _is_safe_git_name(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("-"):
        return False
    if re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value):
        return False
    if value.startswith("/") or value.endswith(("/", ".")) or "//" in value or ".." in value or "@{" in value:
        return False
    return all(
        component not in {"", ".", ".."}
        and not component.startswith(".")
        and not component.endswith(".lock")
        for component in value.split("/")
    )


def _is_nonempty_text(value: Any, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= max_length
        and not re.search(r"[\x00-\x1f\x7f]", value)
    )


def validate_policy(policy: Mapping[str, Any]) -> list[PolicyIssue]:
    issues = [*_missing_issues(policy), *_unknown_issues(policy)]
    schema = policy.get("schema_version")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != SUPPORTED_SCHEMA_VERSION:
        issues.append(PolicyIssue("P_SCHEMA", "schema_version", "Only schema version 1 is supported."))
    if policy.get("project_name") is not None and not _is_nonempty_text(policy.get("project_name"), max_length=120):
        issues.append(PolicyIssue("P_PROJECT_NAME", "project_name", "Project name must be nonempty text without control characters."))
    kind = policy.get("project_kind")
    if kind is not None and (
        not _is_nonempty_text(kind, max_length=64)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(kind)) is None
    ):
        issues.append(PolicyIssue("P_PROJECT_KIND", "project_kind", "Project kind must be a safe identifier."))
    for path in ("reasoning.default", "reasoning.plan", "reasoning.subagent"):
        level = _value_at(policy, path)
        if level is not None and (
            not isinstance(level, str) or level not in ALLOWED_REASONING_LEVELS
        ):
            issues.append(PolicyIssue("P_REASONING", path, f"Unsupported reasoning level at {path}: {level!r}"))
    if _value_at(policy, "reasoning.model") not in (None, "gpt-5.6-sol"):
        issues.append(PolicyIssue("P_MODEL", "reasoning.model", "The project policy requires gpt-5.6-sol."))
    workers = _value_at(policy, "reasoning.normal_max_workers")
    if workers is not None and (isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 2):
        issues.append(PolicyIssue("P_WORKERS", "reasoning.normal_max_workers", "Normal concurrency must be between 1 and 2."))
    booleans = (
        ("reasoning.sequential_default", True, "P_SEQUENTIAL"),
        ("git.require_pull_request", True, "P_PR_REQUIRED"),
        ("git.allow_direct_base_push", False, "P_BASE_PUSH"),
        ("documentation.require_impact_assessment", True, "P_DOC_IMPACT"),
        ("release.require_manifest", True, "P_RELEASE_MANIFEST"),
        ("release.allow_local_official_release", False, "P_LOCAL_RELEASE"),
    )
    for path, expected, code in booleans:
        observed = _value_at(policy, path)
        if observed is not None and observed is not expected:
            issues.append(PolicyIssue(code, path, f"{path} must remain {str(expected).lower()}."))
    remote = _value_at(policy, "git.remote")
    if remote is not None and not _is_safe_git_name(remote):
        issues.append(PolicyIssue("P_REMOTE", "git.remote", "Remote must be a safe Git name."))
    base = _value_at(policy, "git.base_branch")
    if base is not None and (base == "HEAD" or not _is_safe_git_name(base)):
        issues.append(PolicyIssue("P_BASE_BRANCH", "git.base_branch", "Base branch must be valid and unambiguous."))
    strategy = _value_at(policy, "git.integration_strategy")
    if strategy is not None and (
        not isinstance(strategy, str)
        or strategy not in ALLOWED_INTEGRATION_STRATEGIES
    ):
        issues.append(PolicyIssue("P_INTEGRATION", "git.integration_strategy", "Integration strategy is unsupported."))
    if _value_at(policy, "release.official_source") not in (None, "remote_base"):
        issues.append(PolicyIssue("P_RELEASE_SOURCE", "release.official_source", "Official releases must use remote_base."))
    for tier in ("T0", "T1", "T2", "T3"):
        path = f"gates.{tier}.required"
        gates = _value_at(policy, path)
        if gates is not None and (
            not isinstance(gates, list)
            or not gates
            or not all(isinstance(item, str) and item for item in gates)
        ):
            issues.append(PolicyIssue("P_GATES", path, f"{tier} must contain named gates."))
    return issues
