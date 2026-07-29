"""Canonical repository-scope normalization and ownership semantics."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def normalize_scope(value: Any) -> str | None:
    """Return the canonical root owned by a repository-relative scope."""

    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return None
    normalized = value.rstrip("/")
    if normalized.endswith("/**"):
        normalized = normalized[:-3].rstrip("/")
    if normalized in {"", "."}:
        return "."
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in {"", "."}:
        return "."
    if any(character in normalized for character in "*?[]"):
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    canonical = path.as_posix()
    return "." if canonical == "." else canonical


def scope_owns(owner: Any, candidate: Any) -> bool:
    """Return whether *owner* contains the candidate root or path."""

    normalized_owner = normalize_scope(owner)
    normalized_candidate = normalize_scope(candidate)
    if normalized_owner is None or normalized_candidate is None:
        return False
    return (
        normalized_owner == "."
        or normalized_candidate == normalized_owner
        or normalized_candidate.startswith(normalized_owner + "/")
    )


def scopes_overlap(left: Any, right: Any) -> bool:
    """Return whether either normalized scope owns the other's root."""

    return scope_owns(left, right) or scope_owns(right, left)
