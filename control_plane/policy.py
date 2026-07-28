"""Load and validate the versioned project policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import re
import tomllib


SUPPORTED_SCHEMA_VERSION = 1
ALLOWED_REASONING_LEVELS = frozenset(
    {"low", "medium", "high", "xhigh", "max", "ultra"}
)
ALLOWED_INTEGRATION_STRATEGIES = frozenset(
    {"squash", "merge-commit", "rebase-merge"}
)
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
        {
            "official_source",
            "require_manifest",
            "allow_local_official_release",
        }
    ),
    "gates": frozenset({"T0", "T1", "T2", "T3"}),
    "gates.T0": frozenset({"required"}),
    "gates.T1": frozenset({"required"}),
    "gates.T2": frozenset({"required"}),
    "gates.T3": frozenset({"required"}),
}


class PolicyError(Exception):
    """Raised when a policy file cannot be read or parsed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PolicyIssue:
    """One deterministic policy validation failure."""

    code: str
    path: str
    message: str


def load_policy(path: Path) -> dict[str, Any]:
    """Load TOML without silently substituting policy defaults."""

    try:
        with path.open("rb") as policy_file:
            data = tomllib.load(policy_file)
    except FileNotFoundError as error:
        raise PolicyError(
            "E_POLICY_NOT_FOUND", f"Project policy not found: {path}"
        ) from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PolicyError(
            "E_POLICY_PARSE", f"Project policy could not be parsed: {path}"
        ) from error

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
                issues.append(
                    PolicyIssue(
                        "P_UNKNOWN",
                        dotted,
                        f"Unknown policy key for schema 1: {dotted}",
                    )
                )
    return issues


def _is_safe_git_name(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("-"):
        return False
    if re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value):
        return False
    if (
        value.startswith("/")
        or value.endswith(("/", "."))
        or "//" in value
        or ".." in value
        or "@{" in value
    ):
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
    """Return every policy issue so configuration can be fixed in one pass."""

    issues = _missing_issues(policy)
    issues.extend(_unknown_issues(policy))

    schema_version = policy.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SUPPORTED_SCHEMA_VERSION
    ):
        issues.append(
            PolicyIssue(
                "P_SCHEMA",
                "schema_version",
                f"Only schema version {SUPPORTED_SCHEMA_VERSION} is supported.",
            )
        )

    project_name = policy.get("project_name")
    if project_name is not None and not _is_nonempty_text(
        project_name, max_length=120
    ):
        issues.append(
            PolicyIssue(
                "P_PROJECT_NAME",
                "project_name",
                "Project name must be nonempty text without control characters.",
            )
        )

    project_kind = policy.get("project_kind")
    if project_kind is not None and (
        not _is_nonempty_text(project_kind, max_length=64)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project_kind) is None
    ):
        issues.append(
            PolicyIssue(
                "P_PROJECT_KIND",
                "project_kind",
                "Project kind must be a nonempty identifier using letters, digits, dots, underscores, or hyphens.",
            )
        )

    for path in ("reasoning.default", "reasoning.plan", "reasoning.subagent"):
        level = _value_at(policy, path)
        if level is not None and (
            not isinstance(level, str) or level not in ALLOWED_REASONING_LEVELS
        ):
            issues.append(
                PolicyIssue(
                    "P_REASONING",
                    path,
                    f"Unsupported reasoning level at {path}: {level!r}",
                )
            )

    if _value_at(policy, "reasoning.model") not in (None, "gpt-5.6-sol"):
        issues.append(
            PolicyIssue(
                "P_MODEL",
                "reasoning.model",
                "The project policy requires gpt-5.6-sol.",
            )
        )

    workers = _value_at(policy, "reasoning.normal_max_workers")
    if workers is not None and (
        isinstance(workers, bool)
        or not isinstance(workers, int)
        or not 1 <= workers <= 2
    ):
        issues.append(
            PolicyIssue(
                "P_WORKERS",
                "reasoning.normal_max_workers",
                "Normal concurrency must be an integer between 1 and 2.",
            )
        )

    sequential_default = _value_at(policy, "reasoning.sequential_default")
    if sequential_default is not None and sequential_default is not True:
        issues.append(
            PolicyIssue(
                "P_SEQUENTIAL",
                "reasoning.sequential_default",
                "Sequential execution must remain the default.",
            )
        )

    require_pull_request = _value_at(policy, "git.require_pull_request")
    if require_pull_request is not None and require_pull_request is not True:
        issues.append(
            PolicyIssue(
                "P_PR_REQUIRED",
                "git.require_pull_request",
                "Protected-base integration must require a Pull Request.",
            )
        )

    remote = _value_at(policy, "git.remote")
    if remote is not None and not _is_safe_git_name(remote):
        issues.append(
            PolicyIssue(
                "P_REMOTE",
                "git.remote",
                "Remote must be a safe Git name and cannot be an option.",
            )
        )

    base_branch = _value_at(policy, "git.base_branch")
    if base_branch is not None and (
        base_branch == "HEAD" or not _is_safe_git_name(base_branch)
    ):
        issues.append(
            PolicyIssue(
                "P_BASE_BRANCH",
                "git.base_branch",
                "Base branch must be a valid, unambiguous Git branch name.",
            )
        )

    integration_strategy = _value_at(policy, "git.integration_strategy")
    if (
        integration_strategy is not None
        and (
            not isinstance(integration_strategy, str)
            or integration_strategy not in ALLOWED_INTEGRATION_STRATEGIES
        )
    ):
        issues.append(
            PolicyIssue(
                "P_INTEGRATION",
                "git.integration_strategy",
                "Integration strategy must be squash, merge-commit, or rebase-merge.",
            )
        )

    allow_direct_base_push = _value_at(policy, "git.allow_direct_base_push")
    if allow_direct_base_push is not None and allow_direct_base_push is not False:
        issues.append(
            PolicyIssue(
                "P_BASE_PUSH",
                "git.allow_direct_base_push",
                "Direct pushes to the protected base branch are forbidden.",
            )
        )

    if _value_at(policy, "release.official_source") not in (None, "remote_base"):
        issues.append(
            PolicyIssue(
                "P_RELEASE_SOURCE",
                "release.official_source",
                "Official releases must use the protected remote base.",
            )
        )

    require_impact_assessment = _value_at(
        policy, "documentation.require_impact_assessment"
    )
    if (
        require_impact_assessment is not None
        and require_impact_assessment is not True
    ):
        issues.append(
            PolicyIssue(
                "P_DOC_IMPACT",
                "documentation.require_impact_assessment",
                "Documentation impact assessment must remain enabled.",
            )
        )

    require_manifest = _value_at(policy, "release.require_manifest")
    if require_manifest is not None and require_manifest is not True:
        issues.append(
            PolicyIssue(
                "P_RELEASE_MANIFEST",
                "release.require_manifest",
                "Official releases require a release manifest.",
            )
        )

    allow_local_release = _value_at(
        policy, "release.allow_local_official_release"
    )
    if allow_local_release is not None and allow_local_release is not False:
        issues.append(
            PolicyIssue(
                "P_LOCAL_RELEASE",
                "release.allow_local_official_release",
                "A local worktree cannot be the source of an official release.",
            )
        )

    for tier in ("T0", "T1", "T2", "T3"):
        path = f"gates.{tier}.required"
        gates = _value_at(policy, path)
        if gates is not None and (
            not isinstance(gates, list)
            or not gates
            or not all(isinstance(item, str) and item for item in gates)
        ):
            issues.append(
                PolicyIssue(
                    "P_GATES",
                    path,
                    f"{tier} must contain at least one named gate.",
                )
            )

    return issues
