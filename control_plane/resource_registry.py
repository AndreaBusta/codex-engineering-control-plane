"""Strict resource-registry validation and metadata-only inventory discovery."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import tomllib
from typing import Any, Mapping

from control_plane.contracts import (
    SHA256_DIGEST,
    TASK_EFFECTS,
    TASK_INTENTS,
    TASK_PHASES,
    TASK_SIGNALS,
    contract_digest,
)
from control_plane.project_profiles import detect_project_profile


SUPPORTED_SCHEMA_VERSION = 1
RESOURCE_KINDS = frozenset(
    {
        "instruction",
        "document",
        "skill",
        "plugin",
        "mcp_server",
        "mcp_tool",
        "agent",
        "template",
        "gate",
        "hook",
        "automation",
    }
)
TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "registry_id", "router", "budgets", "resources", "routes"}
)
ROUTER_KEYS = frozenset(
    {"default_mode", "external_effect_default", "max_context_output_bytes"}
)
BUDGET_KEYS = frozenset(
    {"max_recommended", "max_agents", "max_context_units"}
)
RESOURCE_KEYS = frozenset(
    {
        "id",
        "kind",
        "provider",
        "locator",
        "capabilities",
        "scope",
        "authority",
        "trust",
        "selection",
        "effects",
        "egress",
        "data_classes",
        "approval",
        "load_strategy",
        "context_class",
        "canonical",
        "priority",
        "requires",
        "conflicts",
        "supersedes",
        "aliases",
        "output_contract",
    }
)
REQUIRED_RESOURCE_KEYS = RESOURCE_KEYS
ROUTE_KEYS = frozenset(
    {
        "id",
        "priority",
        "tiers",
        "phases",
        "intents",
        "domains_any",
        "signals_any",
        "signals_all",
        "effects_any",
        "required_capabilities",
        "recommended_resources",
        "forbidden_resources",
    }
)
REQUIRED_ROUTE_KEYS = ROUTE_KEYS
RESOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}$", re.ASCII)
SAFE_LOCATOR_SCHEMES = frozenset(
    {"repo", "user-skill", "builtin", "mcp", "plugin", "agent", "template"}
)
RESOURCE_SCOPES = frozenset({"project", "global", "plugin", "host"})
RESOURCE_AUTHORITIES = frozenset({"project", "global", "system"})
RESOURCE_TRUST = frozenset(
    {
        "trusted_project",
        "trusted_global",
        "trusted_host",
        "unverified_duplicate",
        "unverified_external",
    }
)
RESOURCE_SELECTIONS = frozenset(
    {"required", "available", "recommended", "forbidden"}
)
RESOURCE_EGRESS = frozenset({"none", "metadata"})
RESOURCE_APPROVAL = frozenset({"none", "task", "explicit"})
RESOURCE_LOAD_STRATEGIES = frozenset(
    {"native", "progressive", "tool", "metadata"}
)
RESOURCE_CONTEXT_CLASSES = frozenset(
    {"none", "tiny", "small", "medium", "large"}
)
INVENTORY_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "observed_at",
        "project_profile",
        "resources",
        "snapshot_digest",
    }
)
INVENTORY_RESOURCE_KEYS = frozenset(
    {
        "id",
        "availability",
        "discovered",
        "enabled",
        "trusted",
        "authenticated",
        "healthy",
        "authorized_for_task",
        "ready",
        "locator_digest",
        "size_bytes",
        "reason_codes",
    }
)
INVENTORY_AVAILABILITY = frozenset(
    {"available", "unavailable", "invalid", "unknown"}
)
INVENTORY_AUTHENTICATION = frozenset(
    {"authenticated", "unauthenticated", "not_applicable", "unknown"}
)
INVENTORY_HEALTH = frozenset(
    {"healthy", "unhealthy", "available", "unavailable", "invalid", "unknown"}
)


class RegistryError(Exception):
    """Raised when a registry cannot be loaded."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RegistryIssue:
    """One deterministic registry validation issue."""

    code: str
    path: str
    message: str


def load_registry(path: Path) -> dict[str, Any]:
    """Load TOML without applying implicit resources or policy."""

    try:
        with path.open("rb") as registry_file:
            return tomllib.load(registry_file)
    except FileNotFoundError as error:
        raise RegistryError(
            "E_REGISTRY_NOT_FOUND", f"Resource registry not found: {path}"
        ) from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RegistryError(
            "E_REGISTRY_PARSE", f"Resource registry could not be parsed: {path}"
        ) from error


def _issue(code: str, path: str, message: str) -> RegistryIssue:
    return RegistryIssue(code, path, message)


def _unknown_keys(
    value: Any, allowed: frozenset[str], path: str
) -> list[RegistryIssue]:
    if not isinstance(value, Mapping):
        return []
    return [
        _issue(
            "R_UNKNOWN",
            f"{path}.{key}" if path else str(key),
            "Unknown key for resource-registry schema 1.",
        )
        for key in sorted(value)
        if key not in allowed
    ]


def _valid_locator(locator: Any) -> bool:
    if not isinstance(locator, str) or "://" not in locator:
        return False
    scheme, target = locator.split("://", 1)
    if scheme not in SAFE_LOCATOR_SCHEMES or not target:
        return False
    if "\\" in target or "\x00" in target:
        return False
    path = PurePosixPath(target)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        return False
    if (
        scheme == "user-skill"
        and (
            len(path.parts) != 1
            or RESOURCE_ID.fullmatch(path.parts[0]) is None
        )
    ):
        return False
    return True


def _dependency_issues(resources: list[Mapping[str, Any]]) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    identifiers = {
        item.get("id") for item in resources if isinstance(item.get("id"), str)
    }
    graph: dict[str, list[str]] = {}
    for index, resource in enumerate(resources):
        identifier = resource.get("id")
        if not isinstance(identifier, str):
            continue
        dependencies = resource.get("requires", [])
        graph[identifier] = [
            item for item in dependencies if isinstance(item, str)
        ]
        for dependency in graph[identifier]:
            if dependency not in identifiers:
                issues.append(
                    _issue(
                        "R_DEPENDENCY_MISSING",
                        f"resources.{index}.requires",
                        f"Unknown dependency: {dependency}",
                    )
                )

    remaining_dependencies = {
        node: {dependency for dependency in dependencies if dependency in graph}
        for node, dependencies in graph.items()
    }
    dependents: dict[str, set[str]] = {node: set() for node in graph}
    for node, dependencies in remaining_dependencies.items():
        for dependency in dependencies:
            dependents[dependency].add(node)
    ready = sorted(
        node for node, dependencies in remaining_dependencies.items() if not dependencies
    )
    visited: set[str] = set()
    while ready:
        node = ready.pop()
        if node in visited:
            continue
        visited.add(node)
        for dependent in sorted(dependents[node]):
            remaining_dependencies[dependent].discard(node)
            if not remaining_dependencies[dependent]:
                ready.append(dependent)
    cycle_nodes = sorted(set(graph) - visited)
    if cycle_nodes:
        issues.append(
            _issue(
                "R_DEPENDENCY_CYCLE",
                "resources",
                "Resource dependency cycle or dependent chain: "
                + ", ".join(cycle_nodes),
            )
        )
    return issues


def validate_registry(registry: Mapping[str, Any]) -> list[RegistryIssue]:
    """Validate schema, identifiers, locators, and referential integrity."""

    issues = _unknown_keys(registry, TOP_LEVEL_KEYS, "")
    if registry.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        issues.append(
            _issue(
                "R_SCHEMA",
                "schema_version",
                "Only resource-registry schema 1 is supported.",
            )
        )
    router = registry.get("router")
    if not isinstance(router, Mapping):
        issues.append(_issue("R_MISSING", "router", "Router settings are required."))
    else:
        issues.extend(_unknown_keys(router, ROUTER_KEYS, "router"))
        if router.get("default_mode") not in {"audit", "enforce"}:
            issues.append(
                _issue("R_ROUTER", "router.default_mode", "Unsupported router mode.")
            )
        if router.get("external_effect_default") != "deny":
            issues.append(
                _issue(
                    "R_ROUTER",
                    "router.external_effect_default",
                    "External effects must default to deny.",
                )
            )
        context_bytes = router.get("max_context_output_bytes")
        if (
            not isinstance(context_bytes, int)
            or isinstance(context_bytes, bool)
            or not 1 <= context_bytes <= 4096
        ):
            issues.append(
                _issue(
                    "R_ROUTER",
                    "router.max_context_output_bytes",
                    "Router context output must be an integer from 1 to 4096.",
                )
            )

    budgets = registry.get("budgets")
    if not isinstance(budgets, Mapping):
        issues.append(_issue("R_MISSING", "budgets", "Budgets are required."))
    else:
        for tier in ("T0", "T1", "T2", "T3"):
            budget = budgets.get(tier)
            if not isinstance(budget, Mapping):
                issues.append(
                    _issue("R_MISSING", f"budgets.{tier}", "Tier budget is required.")
                )
                continue
            issues.extend(
                _unknown_keys(budget, BUDGET_KEYS, f"budgets.{tier}")
            )
            for key in BUDGET_KEYS:
                value = budget.get(key)
                upper = 2 if key == "max_agents" else 1_000_000
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 0 <= value <= upper
                ):
                    issues.append(
                        _issue(
                            "R_BUDGET",
                            f"budgets.{tier}.{key}",
                            "Budget must be a bounded non-negative integer.",
                        )
                    )

    raw_resources = registry.get("resources")
    if not isinstance(raw_resources, list):
        return issues + [
            _issue("R_MISSING", "resources", "Resource list is required.")
        ]
    resources = [item for item in raw_resources if isinstance(item, Mapping)]
    seen: set[str] = set()
    for index, resource in enumerate(resources):
        path = f"resources.{index}"
        issues.extend(_unknown_keys(resource, RESOURCE_KEYS, path))
        for key in sorted(REQUIRED_RESOURCE_KEYS.difference(resource)):
            issues.append(
                _issue(
                    "R_MISSING",
                    f"{path}.{key}",
                    "Required resource field is missing.",
                )
            )
        identifier = resource.get("id")
        if not isinstance(identifier, str) or not RESOURCE_ID.fullmatch(identifier):
            issues.append(
                _issue(
                    "R_RESOURCE_ID",
                    f"{path}.id",
                    "Resource IDs must use lower-case ASCII identifiers.",
                )
            )
        elif identifier in seen:
            issues.append(
                _issue(
                    "R_DUPLICATE_ID",
                    f"{path}.id",
                    f"Duplicate resource ID: {identifier}",
                )
            )
        else:
            seen.add(identifier)
        if resource.get("kind") not in RESOURCE_KINDS:
            issues.append(
                _issue("R_KIND", f"{path}.kind", "Unsupported resource kind.")
            )
        scalar_enums = (
            ("scope", RESOURCE_SCOPES),
            ("authority", RESOURCE_AUTHORITIES),
            ("trust", RESOURCE_TRUST),
            ("selection", RESOURCE_SELECTIONS),
            ("egress", RESOURCE_EGRESS),
            ("approval", RESOURCE_APPROVAL),
            ("load_strategy", RESOURCE_LOAD_STRATEGIES),
            ("context_class", RESOURCE_CONTEXT_CLASSES),
        )
        for field, allowed in scalar_enums:
            if resource.get(field) not in allowed:
                issues.append(
                    _issue(
                        "R_ENUM",
                        f"{path}.{field}",
                        f"Unsupported {field} value.",
                    )
                )
        for field in ("provider", "output_contract"):
            if not isinstance(resource.get(field), str) or not RESOURCE_ID.fullmatch(
                str(resource.get(field, ""))
            ):
                issues.append(
                    _issue(
                        "R_TYPE",
                        f"{path}.{field}",
                        "Expected a stable lower-case ASCII identifier.",
                    )
                )
        if not isinstance(resource.get("canonical"), bool):
            issues.append(
                _issue("R_TYPE", f"{path}.canonical", "Expected a boolean.")
            )
        priority = resource.get("priority")
        if (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or not 0 <= priority <= 10_000
        ):
            issues.append(
                _issue(
                    "R_TYPE",
                    f"{path}.priority",
                    "Priority must be an integer from 0 to 10000.",
                )
            )
        if not _valid_locator(resource.get("locator")):
            issues.append(
                _issue(
                    "R_LOCATOR",
                    f"{path}.locator",
                    "Locator must use an approved non-executable scheme.",
                )
            )
        for field in (
            "capabilities",
            "effects",
            "data_classes",
            "requires",
            "conflicts",
            "supersedes",
            "aliases",
        ):
            value = resource.get(field)
            if not isinstance(value, list) or not all(
                isinstance(item, str)
                and RESOURCE_ID.fullmatch(item) is not None
                for item in value
            ):
                issues.append(
                    _issue("R_TYPE", f"{path}.{field}", "Expected a string list.")
                )
        if isinstance(resource.get("effects"), list):
            for effect in resource["effects"]:
                if effect not in TASK_EFFECTS:
                    issues.append(
                        _issue(
                            "R_ENUM",
                            f"{path}.effects",
                            f"Unsupported resource effect: {effect}",
                        )
                    )
    issues.extend(_dependency_issues(resources))
    identifiers = {
        str(item.get("id"))
        for item in resources
        if isinstance(item.get("id"), str)
    }
    aliases: dict[str, str] = {}
    for index, resource in enumerate(resources):
        for field in ("conflicts", "supersedes"):
            for reference in resource.get(field, []):
                if reference not in identifiers:
                    issues.append(
                        _issue(
                            "R_REFERENCE_MISSING",
                            f"resources.{index}.{field}",
                            f"Unknown resource reference: {reference}",
                        )
                    )
        for alias in resource.get("aliases", []):
            if not RESOURCE_ID.fullmatch(alias):
                issues.append(
                    _issue(
                        "R_ALIAS",
                        f"resources.{index}.aliases",
                        f"Alias is not stable lower-case ASCII: {alias}",
                    )
                )
            if alias in identifiers and alias != resource.get("id"):
                issues.append(
                    _issue(
                        "R_ALIAS_COLLISION",
                        f"resources.{index}.aliases",
                        f"Alias collides with resource ID: {alias}",
                    )
                )
            owner = aliases.get(alias)
            if owner is not None and owner != resource.get("id"):
                issues.append(
                    _issue(
                        "R_ALIAS_COLLISION",
                        f"resources.{index}.aliases",
                        f"Alias belongs to both {owner} and {resource.get('id')}.",
                    )
                )
            aliases[alias] = str(resource.get("id"))

    routes = registry.get("routes")
    if not isinstance(routes, list):
        issues.append(_issue("R_MISSING", "routes", "Route list is required."))
    else:
        route_ids: set[str] = set()
        for index, route in enumerate(routes):
            if not isinstance(route, Mapping):
                issues.append(
                    _issue("R_TYPE", f"routes.{index}", "Route must be a table.")
                )
                continue
            issues.extend(_unknown_keys(route, ROUTE_KEYS, f"routes.{index}"))
            for key in sorted(REQUIRED_ROUTE_KEYS.difference(route)):
                issues.append(
                    _issue(
                        "R_MISSING",
                        f"routes.{index}.{key}",
                        "Required route field is missing.",
                    )
                )
            route_id = route.get("id")
            if not isinstance(route_id, str) or not RESOURCE_ID.fullmatch(route_id):
                issues.append(
                    _issue("R_ROUTE", f"routes.{index}.id", "Invalid route ID.")
                )
            elif route_id in route_ids:
                issues.append(
                    _issue(
                        "R_ROUTE",
                        f"routes.{index}.id",
                        f"Duplicate route ID: {route_id}",
                    )
                )
            else:
                route_ids.add(route_id)
            priority = route.get("priority")
            if (
                not isinstance(priority, int)
                or isinstance(priority, bool)
                or not 0 <= priority <= 10_000
            ):
                issues.append(
                    _issue(
                        "R_ROUTE",
                        f"routes.{index}.priority",
                        "Route priority must be an integer from 0 to 10000.",
                    )
                )
            route_lists = {
                "tiers": frozenset({"T0", "T1", "T2", "T3"}),
                "phases": TASK_PHASES,
                "intents": TASK_INTENTS,
                "signals_any": TASK_SIGNALS,
                "signals_all": TASK_SIGNALS,
                "effects_any": TASK_EFFECTS,
            }
            for field, allowed in route_lists.items():
                value = route.get(field)
                if not isinstance(value, list) or not all(
                    isinstance(item, str) and item in allowed for item in value
                ):
                    issues.append(
                        _issue(
                            "R_ROUTE",
                            f"routes.{index}.{field}",
                            "Route list contains unsupported values.",
                        )
                    )
            for field in (
                "domains_any",
                "required_capabilities",
                "recommended_resources",
                "forbidden_resources",
            ):
                value = route.get(field)
                if not isinstance(value, list) or not all(
                    isinstance(item, str) and RESOURCE_ID.fullmatch(item)
                    for item in value
                ):
                    issues.append(
                        _issue(
                            "R_ROUTE",
                            f"routes.{index}.{field}",
                            "Route references must be stable lower-case IDs.",
                        )
                    )
            for field in ("recommended_resources", "forbidden_resources"):
                for reference in route.get(field, []):
                    if reference not in identifiers:
                        issues.append(
                            _issue(
                                "R_ROUTE_REFERENCE",
                                f"routes.{index}.{field}",
                                f"Unknown route resource: {reference}",
                            )
                        )
    return sorted(issues, key=lambda item: (item.code, item.path, item.message))


def validate_policy_references(
    policy: Mapping[str, Any], registry: Mapping[str, Any]
) -> list[RegistryIssue]:
    """Require every policy gate name to resolve to a registered gate alias."""

    gate_aliases: set[str] = set()
    for resource in registry.get("resources", []):
        if isinstance(resource, Mapping) and resource.get("kind") == "gate":
            gate_aliases.update(
                alias
                for alias in resource.get("aliases", [])
                if isinstance(alias, str)
            )
    issues: list[RegistryIssue] = []
    for tier in ("T0", "T1", "T2", "T3"):
        for gate in policy.get("gates", {}).get(tier, {}).get("required", []):
            if gate not in gate_aliases:
                issues.append(
                    _issue(
                        "R_GATE_UNRESOLVED",
                        f"gates.{tier}.required",
                        f"Policy gate is not registered: {gate}",
                    )
                )
    return issues


def _inventory_ready(entry: Mapping[str, Any]) -> bool:
    """Compute operational readiness without treating enablement as authority."""

    return bool(
        entry.get("availability") == "available"
        and entry.get("discovered") is True
        and entry.get("enabled") is True
        and entry.get("trusted") is True
        and entry.get("authenticated") in {"authenticated", "not_applicable"}
        and entry.get("healthy") in {"healthy", "available"}
    )


def validate_inventory(
    registry: Mapping[str, Any], inventory: Mapping[str, Any]
) -> list[RegistryIssue]:
    """Validate a closed InventorySnapshot and recompute its evidence digest."""

    issues = _unknown_keys(inventory, INVENTORY_KEYS, "")
    if inventory.get("schema_version") != 1:
        issues.append(
            _issue("I_SCHEMA", "schema_version", "Only inventory schema 1 is supported.")
        )
    if not isinstance(inventory.get("source"), str) or not inventory.get("source"):
        issues.append(_issue("I_SOURCE", "source", "Inventory source is required."))
    if "observed_at" in inventory and not isinstance(inventory.get("observed_at"), str):
        issues.append(
            _issue("I_TYPE", "observed_at", "Observed time must be a string.")
        )
    profile = inventory.get("project_profile")
    if not isinstance(profile, Mapping):
        issues.append(
            _issue(
                "I_PROFILE",
                "project_profile",
                "A detector-produced project profile is required.",
            )
        )
    resources = inventory.get("resources")
    if not isinstance(resources, list):
        issues.append(_issue("I_RESOURCES", "resources", "Resources must be a list."))
        resources = []

    registry_ids = {
        str(resource.get("id"))
        for resource in registry.get("resources", [])
        if isinstance(resource, Mapping)
    }
    seen: set[str] = set()
    for index, entry in enumerate(resources):
        path = f"resources.{index}"
        if not isinstance(entry, Mapping):
            issues.append(_issue("I_TYPE", path, "Inventory entry must be an object."))
            continue
        issues.extend(_unknown_keys(entry, INVENTORY_RESOURCE_KEYS, path))
        for key in sorted(INVENTORY_RESOURCE_KEYS.difference(entry)):
            issues.append(
                _issue("I_MISSING", f"{path}.{key}", "Inventory field is required.")
            )
        identifier = entry.get("id")
        if (
            not isinstance(identifier, str)
            or RESOURCE_ID.fullmatch(identifier) is None
            or identifier not in registry_ids
        ):
            issues.append(
                _issue(
                    "I_RESOURCE_ID",
                    f"{path}.id",
                    "Inventory resource must reference exactly one registry resource.",
                )
            )
        elif identifier in seen:
            issues.append(
                _issue("I_DUPLICATE", f"{path}.id", f"Duplicate resource: {identifier}")
            )
        else:
            seen.add(identifier)
        if entry.get("availability") not in INVENTORY_AVAILABILITY:
            issues.append(
                _issue("I_ENUM", f"{path}.availability", "Invalid availability.")
            )
        if entry.get("authenticated") not in INVENTORY_AUTHENTICATION:
            issues.append(
                _issue("I_ENUM", f"{path}.authenticated", "Invalid authentication state.")
            )
        if entry.get("healthy") not in INVENTORY_HEALTH:
            issues.append(_issue("I_ENUM", f"{path}.healthy", "Invalid health state."))
        for field in (
            "discovered",
            "enabled",
            "trusted",
            "authorized_for_task",
            "ready",
        ):
            if not isinstance(entry.get(field), bool):
                issues.append(
                    _issue("I_TYPE", f"{path}.{field}", "Expected a boolean.")
                )
        if entry.get("ready") is not _inventory_ready(entry):
            issues.append(
                _issue(
                    "I_READY_MISMATCH",
                    f"{path}.ready",
                    "Ready must equal the mechanically derived readiness state.",
                )
            )
        locator_digest = entry.get("locator_digest")
        if (
            not isinstance(locator_digest, str)
            or SHA256_DIGEST.fullmatch(locator_digest) is None
        ):
            issues.append(
                _issue(
                    "I_DIGEST",
                    f"{path}.locator_digest",
                    "Locator digest must be a SHA-256 digest.",
                )
            )
        size_bytes = entry.get("size_bytes")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 0 <= size_bytes <= 1 << 40
        ):
            issues.append(
                _issue("I_TYPE", f"{path}.size_bytes", "Invalid resource size.")
            )
        reason_codes = entry.get("reason_codes")
        if not isinstance(reason_codes, list) or not all(
            isinstance(code, str) and RESOURCE_ID.fullmatch(code.lower())
            for code in reason_codes
        ):
            issues.append(
                _issue(
                    "I_TYPE",
                    f"{path}.reason_codes",
                    "Reason codes must be stable ASCII identifiers.",
                )
            )
    missing = sorted(registry_ids - seen)
    if missing:
        issues.append(
            _issue(
                "I_MISSING_RESOURCE",
                "resources",
                "Inventory is missing registry resources: " + ", ".join(missing),
            )
        )
    supplied_digest = inventory.get("snapshot_digest")
    semantic = {
        key: value for key, value in inventory.items() if key != "snapshot_digest"
    }
    expected_digest = contract_digest(semantic)
    if supplied_digest != expected_digest:
        issues.append(
            _issue(
                "I_SNAPSHOT_DIGEST",
                "snapshot_digest",
                "Inventory snapshot digest does not match its canonical content.",
            )
        )
    return sorted(issues, key=lambda item: (item.code, item.path, item.message))


def _file_digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        while chunk := source.read(65536):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def build_inventory(
    registry: Mapping[str, Any], repo_root: Path
) -> dict[str, Any]:
    """Discover resource metadata without reading content into the snapshot."""

    root = repo_root.resolve()
    entries: list[dict[str, Any]] = []
    for resource in sorted(
        registry.get("resources", []), key=lambda item: str(item.get("id", ""))
    ):
        identifier = str(resource.get("id", ""))
        locator = str(resource.get("locator", ""))
        availability = "available"
        reason_codes: list[str] = []
        size_bytes = 0
        locator_digest = contract_digest({"id": identifier, "locator": locator})
        if locator.startswith("repo://"):
            relative = locator.removeprefix("repo://")
            candidate = root.joinpath(*PurePosixPath(relative).parts)
            try:
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    availability = "invalid"
                    reason_codes.append("R_SYMLINK_ESCAPE")
                elif not resolved.is_file():
                    availability = "unavailable"
                    reason_codes.append("R_NOT_FILE")
                else:
                    size_bytes = resolved.stat().st_size
                    locator_digest = _file_digest(resolved)
            except (FileNotFoundError, OSError, RuntimeError):
                availability = "unavailable"
                reason_codes.append("R_NOT_FOUND")
        elif locator.startswith("user-skill://"):
            name = locator.removeprefix("user-skill://")
            skill_roots = (
                (Path.home() / ".codex" / "skills").resolve(),
                (Path.home() / ".agents" / "skills").resolve(),
            )
            if (
                RESOURCE_ID.fullmatch(name) is None
                or "/" in name
                or "\\" in name
            ):
                availability = "invalid"
                reason_codes.append("R_LOCATOR")
                existing = []
            else:
                candidates = tuple(
                    skill_root / name / "SKILL.md"
                    for skill_root in skill_roots
                )
                existing = []
                for skill_root, candidate in zip(skill_roots, candidates):
                    try:
                        resolved = candidate.resolve(strict=True)
                    except (FileNotFoundError, OSError, RuntimeError):
                        continue
                    if resolved.is_file() and resolved.is_relative_to(skill_root):
                        existing.append(resolved)
                    else:
                        availability = "invalid"
                        reason_codes.append("R_SYMLINK_ESCAPE")
            if len(existing) == 1:
                size_bytes = existing[0].stat().st_size
                locator_digest = _file_digest(existing[0])
            elif len(existing) > 1:
                digests = {_file_digest(path) for path in existing}
                if len(digests) == 1:
                    size_bytes = existing[0].stat().st_size
                    locator_digest = next(iter(digests))
                else:
                    availability = "invalid"
                    reason_codes.append("R_RESOURCE_AMBIGUOUS")
            else:
                if availability == "available":
                    availability = "unavailable"
                    reason_codes.append("R_NOT_FOUND")
        elif locator.startswith("builtin://"):
            availability = "available"
        else:
            availability = "unknown"
            reason_codes.append("R_RUNTIME_EVIDENCE_REQUIRED")
        entry = {
            "id": identifier,
            "availability": availability,
            "discovered": availability == "available",
            "enabled": availability == "available",
            "trusted": str(resource.get("trust", "")).startswith("trusted_"),
            "authenticated": (
                "unknown"
                if resource.get("kind") in {"mcp_server", "mcp_tool", "plugin"}
                else "not_applicable"
            ),
            "healthy": availability,
            "authorized_for_task": False,
            "ready": False,
            "locator_digest": locator_digest,
            "size_bytes": size_bytes,
            "reason_codes": reason_codes,
        }
        entry["ready"] = _inventory_ready(entry)
        entries.append(entry)
    snapshot = {
        "schema_version": 1,
        "source": "local-discovery",
        "project_profile": detect_project_profile(root),
        "resources": entries,
    }
    snapshot["snapshot_digest"] = contract_digest(snapshot)
    return snapshot
