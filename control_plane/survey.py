"""Read-only repository survey: clone, worktrees, branches, and orphan work."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from control_plane.materialization import inspect_git_state_materialization
from control_plane.repository import (
    assert_no_external_git_filters,
    canonical_directory,
    is_oid,
    observed_directory,
    run_bounded_git,
    worktree_fingerprint,
)


class _SurveyLimit(Exception):
    """Internal marker: a declared bound was exceeded."""


_MAX_OUTPUT_BYTES = 1_048_576
_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class WorktreeObservation:
    path: str
    branch: str
    head: str
    detached: bool
    dirty: int
    untracked: int


@dataclass(frozen=True)
class BranchObservation:
    name: str
    head: str
    only_in_branch: int
    content_equivalent_to_base: bool


@dataclass(frozen=True)
class RepositorySurvey:
    root: str
    common_git_dir: str
    branch: str
    head: str
    worktrees: tuple[WorktreeObservation, ...]
    branches: tuple[BranchObservation, ...]
    stashes: int
    untracked_total: int
    status: str
    error_code: str | None


def _git(repository: Path, arguments: tuple[str, ...]) -> bytes | None:
    try:
        with observed_directory(repository):
            completed = run_bounded_git(
                repository,
                arguments,
                output_limit=_MAX_OUTPUT_BYTES,
                timeout=float(_TIMEOUT_SECONDS),
            )
    except (OSError, RuntimeError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _text(repository: Path, arguments: tuple[str, ...]) -> str | None:
    raw = _git(repository, arguments)
    if raw is None:
        return None
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return value[:-1] if value.endswith("\n") else value


def _unknown(root: Path, code: str) -> RepositorySurvey:
    return RepositorySurvey(str(root), "", "", "", (), (), 0, 0, "UNKNOWN", code)


def _filters_are_closed(candidate: Path) -> bool:
    try:
        assert_no_external_git_filters(candidate)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _stable_status(candidate: Path) -> bytes | None:
    arguments = ("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not _filters_are_closed(candidate):
        return None
    first = _text(candidate, arguments)
    if first is None or not _filters_are_closed(candidate):
        return None
    second = _text(candidate, arguments)
    if second is None or first != second or not _filters_are_closed(candidate):
        return None
    try:
        return second.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None


def _status_counts(raw: bytes) -> tuple[int, int] | None:
    if not raw:
        return (0, 0)
    fields = raw.split(b"\0")
    if fields[-1] != b"":
        return None
    fields.pop()
    dirty = untracked = 0
    index = 0
    while index < len(fields):
        item = fields[index]
        if len(item) < 3 or item[2:3] != b" ":
            return None
        code = item[:2]
        if code == b"??":
            untracked += 1
        elif code != b"!!":
            dirty += 1
        index += 1
        if b"R" in code or b"C" in code:
            if index >= len(fields) or not fields[index]:
                return None
            index += 1
    return dirty, untracked


def _worktree_records(raw: bytes) -> tuple[tuple[str, str, str, bool], ...] | None:
    fields = raw.split(b"\0")
    if not fields or fields[-1] != b"":
        return None
    fields.pop()
    records: list[tuple[str, str, str, bool]] = []
    current: list[bytes] = []

    def finish() -> bool:
        if not current:
            return False
        path = head = branch = ""
        detached = False
        try:
            for field in current:
                if field.startswith(b"worktree ") and not path:
                    path = field.removeprefix(b"worktree ").decode(
                        "utf-8", errors="strict"
                    )
                elif field.startswith(b"HEAD ") and not head:
                    head = field.removeprefix(b"HEAD ").decode(
                        "ascii", errors="strict"
                    )
                elif field.startswith(b"branch ") and not branch and not detached:
                    branch = field.removeprefix(b"branch ").decode(
                        "utf-8", errors="strict"
                    ).removeprefix("refs/heads/")
                elif field == b"detached" and not branch and not detached:
                    detached = True
                elif field.startswith((b"locked", b"prunable")):
                    continue
                else:
                    return False
        except UnicodeDecodeError:
            return False
        if not path or not head or (not branch and not detached):
            return False
        records.append((path, head, branch, detached))
        current.clear()
        return True

    for field in fields:
        if field:
            current.append(field)
        elif not finish():
            return None
    if current and not finish():
        return None
    return tuple(records)


def _worktrees(
    root: Path,
    common: Path,
    limit: int,
    listing: bytes,
) -> tuple[WorktreeObservation, ...] | None:
    records = _worktree_records(listing)
    if records is None:
        return None
    entries: list[WorktreeObservation] = []
    for path, head, branch, detached in records:
        if len(entries) >= limit:
            raise _SurveyLimit
        candidate = canonical_directory(path)
        if candidate is None:
            return None
        try:
            with observed_directory(candidate):
                before = worktree_fingerprint(candidate, common)
                expected_branch = "HEAD" if detached else branch
                if (
                    before is None
                    or before.head != head
                    or before.branch != expected_branch
                    or before.has_gitlink
                ):
                    return None
                status = _stable_status(candidate)
                after = worktree_fingerprint(candidate, common)
                if (
                    status is None
                    or after is None
                    or after.has_gitlink
                    or before != after
                ):
                    return None
        except (OSError, RuntimeError, ValueError):
            return None
        counts = None if status is None else _status_counts(status)
        if counts is None:
            return None
        dirty, untracked = counts
        entries.append(
            WorktreeObservation(
                str(candidate), branch, head, detached, dirty, untracked
            )
        )
    return tuple(entries)


def _branches(
    root: Path,
    base_name: str,
    base_oid: str,
    limit: int,
    listing: str,
) -> tuple[BranchObservation, ...] | None:
    entries: list[BranchObservation] = []
    observed_names: set[str] = set()
    for line in listing.splitlines():
        name, _, head = line.partition(" ")
        if (
            not name
            or not head
            or " " in head
            or name in observed_names
            or not is_oid(head)
        ):
            return None
        observed_names.add(name)
        if name == base_name:
            continue
        if len(entries) >= limit:
            raise _SurveyLimit
        added = _text(
            root,
            (
                "diff",
                "--diff-filter=A",
                "--name-only",
                f"{base_oid}..{head}",
                "--",
            ),
        )
        whole = _text(
            root,
            ("diff", "--name-only", f"{base_oid}..{head}", "--"),
        )
        if added is None or whole is None:
            return None
        only = len([item for item in added.splitlines() if item])
        equivalent = not [item for item in whole.splitlines() if item]
        entries.append(BranchObservation(name, head, only, equivalent))
    return tuple(entries)


def survey_repository(
    repository: Path,
    *,
    base: str = "origin/main",
    max_worktrees: int = 64,
    max_branches: int = 64,
) -> RepositorySurvey:
    """Observe one clone. Other clones are never visible from here."""

    for limit in (max_worktrees, max_branches):
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 0 <= limit <= 64
        ):
            raise ValueError("E_SURVEY_LIMIT: invalid survey limit")
    supplied_root = Path(os.path.abspath(repository))
    try:
        git_state = inspect_git_state_materialization(supplied_root)
    except (OSError, RuntimeError, ValueError):
        return _unknown(supplied_root, "E_SURVEY_INVENTORY")
    if not git_state.ok:
        return _unknown(supplied_root, "E_SURVEY_INVENTORY")
    root = canonical_directory(str(supplied_root))
    if root is None:
        return _unknown(supplied_root, "E_SURVEY_INVENTORY")
    common = _text(root, ("rev-parse", "--path-format=absolute", "--git-common-dir"))
    common_directory = None if common is None else canonical_directory(common)
    if common is None or common_directory is None:
        return _unknown(root, "E_SURVEY_INVENTORY")
    try:
        with observed_directory(root), observed_directory(common_directory):
            root_before = worktree_fingerprint(root, common_directory)
            if root_before is None:
                return _unknown(root, "E_SURVEY_INVENTORY")
            base_revision = f"{base}^{{commit}}"
            base_oid = _text(
                root,
                ("rev-parse", "--verify", "--quiet", base_revision),
            )
            if base_oid is None or not is_oid(base_oid):
                return _unknown(root, "E_SURVEY_BASE_UNKNOWN")
            worktree_listing = _git(
                root,
                ("worktree", "list", "--porcelain", "-z"),
            )
            branch_listing = _text(
                root,
                (
                    "for-each-ref",
                    "--format=%(refname:short) %(objectname)",
                    "refs/heads",
                ),
            )
            stash_listing = _text(root, ("stash", "list"))
            if (
                worktree_listing is None
                or branch_listing is None
                or stash_listing is None
            ):
                return _unknown(root, "E_SURVEY_INVENTORY")
            worktrees = _worktrees(
                root,
                common_directory,
                max_worktrees,
                worktree_listing,
            )
            branches = (
                None
                if worktrees is None
                else _branches(
                    root,
                    base,
                    base_oid,
                    max_branches,
                    branch_listing,
                )
            )
            if worktrees is None or branches is None:
                return _unknown(root, "E_SURVEY_INVENTORY")
            if (
                _text(root, ("stash", "list")) != stash_listing
                or _text(
                    root,
                    (
                        "for-each-ref",
                        "--format=%(refname:short) %(objectname)",
                        "refs/heads",
                    ),
                )
                != branch_listing
                or _git(root, ("worktree", "list", "--porcelain", "-z"))
                != worktree_listing
                or _text(
                    root,
                    ("rev-parse", "--verify", "--quiet", base_revision),
                )
                != base_oid
                or worktree_fingerprint(root, common_directory) != root_before
            ):
                return _unknown(root, "E_SURVEY_INVENTORY")
    except _SurveyLimit:
        return _unknown(root, "E_SURVEY_LIMIT")
    except (OSError, RuntimeError, ValueError):
        return _unknown(root, "E_SURVEY_INVENTORY")
    stashes = len([item for item in stash_listing.splitlines() if item])
    untracked_total = sum(item.untracked for item in worktrees)
    orphan = stashes or untracked_total
    return RepositorySurvey(
        str(root),
        str(common_directory),
        root_before.branch,
        root_before.head,
        worktrees,
        branches,
        stashes,
        untracked_total,
        "FAIL" if orphan else "PASS",
        None,
    )


def survey_payload(survey: RepositorySurvey) -> dict:
    """Render the non-authorizing RepositorySurveyV1 mapping."""

    return {
        "schema_version": 1,
        "kind": "RepositorySurveyV1",
        "clone": {
            "root": survey.root,
            "common_git_dir": survey.common_git_dir,
            "branch": survey.branch,
            "head": survey.head,
        },
        "worktrees": [
            {
                "path": item.path,
                "branch": item.branch,
                "head": item.head,
                "detached": item.detached,
                "dirty": item.dirty,
                "untracked": item.untracked,
            }
            for item in survey.worktrees
        ],
        "branches": [
            {
                "name": item.name,
                "head": item.head,
                "only_in_branch": item.only_in_branch,
                "content_equivalent_to_base": item.content_equivalent_to_base,
            }
            for item in survey.branches
        ],
        "orphan_work": {
            "stashes": survey.stashes,
            "untracked_total": survey.untracked_total,
        },
        "other_clones": "UNKNOWN",
        "status": survey.status,
        "error_code": survey.error_code,
        "authorizes": False,
    }
