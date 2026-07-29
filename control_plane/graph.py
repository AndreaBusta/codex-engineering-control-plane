"""Validation for bounded fork-join execution graphs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from control_plane.scopes import normalize_scope, scopes_overlap


@dataclass(frozen=True)
class GraphIssue:
    code: str
    path: str
    message: str


GRAPH_KEYS = frozenset({"schema_version", "nodes"})
NODE_KEYS = frozenset({"id", "role", "allowed_paths", "depends_on"})
NODE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}$", re.ASCII)


def _path_safe(value: str) -> bool:
    return normalize_scope(value) is not None


def _overlap(left: str, right: str) -> bool:
    return scopes_overlap(left, right)


def validate_graph(
    graph: Mapping[str, Any], *, max_workers: int
) -> list[GraphIssue]:
    """Reject cycles, unsafe paths, excess workers, and overlapping writers."""

    issues: list[GraphIssue] = []
    if set(graph).difference(GRAPH_KEYS) or graph.get("schema_version") != 1:
        issues.append(
            GraphIssue("G_SCHEMA", "", "Graph must use the closed schema 1.")
        )
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or not 1 <= max_workers <= 2:
        issues.append(
            GraphIssue("G_WORKERS", "max_workers", "Worker limit must be one or two.")
        )
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return [GraphIssue("G_NODES", "nodes", "Graph nodes must be a list.")]
    identifiers: set[str] = set()
    writers: list[tuple[str, str]] = []
    dependencies: dict[str, list[str]] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            issues.append(GraphIssue("G_NODE", f"nodes.{index}", "Node must be an object."))
            continue
        if set(node) != NODE_KEYS:
            issues.append(
                GraphIssue(
                    "G_NODE_SCHEMA",
                    f"nodes.{index}",
                    "Node must use the closed schema.",
                )
            )
        identifier = node.get("id")
        if not isinstance(identifier, str) or NODE_ID.fullmatch(identifier) is None:
            issues.append(GraphIssue("G_ID", f"nodes.{index}.id", "Node ID is required."))
            continue
        if identifier in identifiers:
            issues.append(GraphIssue("G_DUPLICATE", f"nodes.{index}.id", "Duplicate node ID."))
        identifiers.add(identifier)
        role = node.get("role")
        if role not in {"reader", "writer", "join"}:
            issues.append(GraphIssue("G_ROLE", f"nodes.{index}.role", "Unsupported node role."))
        paths = node.get("allowed_paths", [])
        if not isinstance(paths, list) or not all(isinstance(item, str) and _path_safe(item) for item in paths):
            issues.append(GraphIssue("G_PATH", f"nodes.{index}.allowed_paths", "Graph paths must stay within the repository."))
        if role == "writer":
            writers.extend((identifier, path) for path in paths if isinstance(path, str))
        raw_dependencies = node.get("depends_on", [])
        if not isinstance(raw_dependencies, list) or not all(
            isinstance(item, str) and NODE_ID.fullmatch(item) is not None
            for item in raw_dependencies
        ):
            issues.append(
                GraphIssue(
                    "G_DEPENDENCY",
                    f"nodes.{index}.depends_on",
                    "Dependencies must be stable node IDs.",
                )
            )
            raw_dependencies = []
        dependencies[identifier] = list(raw_dependencies)
    for identifier, required in dependencies.items():
        for dependency in required:
            if dependency not in identifiers:
                issues.append(GraphIssue("G_DEPENDENCY", identifier, f"Unknown dependency: {dependency}"))
    def depends_transitively(node: str, possible_ancestor: str) -> bool:
        pending = list(dependencies.get(node, []))
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == possible_ancestor:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(dependencies.get(current, []))
        return False

    for index, (left_id, left_path) in enumerate(writers):
        for right_id, right_path in writers[index + 1 :]:
            sequential = depends_transitively(
                left_id, right_id
            ) or depends_transitively(right_id, left_id)
            if (
                left_id != right_id
                and not sequential
                and _overlap(left_path, right_path)
            ):
                issues.append(
                    GraphIssue(
                        "G_WRITER_OVERLAP",
                        f"{left_id},{right_id}",
                        f"Writers overlap on {left_path} and {right_path}.",
                    )
                )
    indegree = {
        identifier: sum(
            dependency in dependencies for dependency in required
        )
        for identifier, required in dependencies.items()
    }
    dependents: dict[str, list[str]] = {identifier: [] for identifier in dependencies}
    for identifier, required in dependencies.items():
        for dependency in required:
            if dependency in dependents:
                dependents[dependency].append(identifier)
    frontier = sorted(
        identifier for identifier, degree in indegree.items() if degree == 0
    )
    visited_count = 0
    roles = {
        str(node.get("id")): node.get("role")
        for node in nodes
        if isinstance(node, Mapping)
    }
    while frontier:
        active = sum(roles.get(identifier) in {"reader", "writer"} for identifier in frontier)
        if active > max_workers:
            if not any(issue.code == "G_WORKERS" for issue in issues):
                issues.append(
                    GraphIssue(
                        "G_WORKERS",
                        "nodes",
                        f"Execution wave exceeds worker limit {max_workers}.",
                    )
                )
        next_frontier: list[str] = []
        for identifier in frontier:
            visited_count += 1
            for dependent in dependents.get(identifier, []):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    next_frontier.append(dependent)
        frontier = sorted(next_frontier)
    if visited_count != len(dependencies):
        issues.append(GraphIssue("G_CYCLE", "nodes", "Graph dependencies contain a cycle."))
    return sorted(issues, key=lambda item: (item.code, item.path))
