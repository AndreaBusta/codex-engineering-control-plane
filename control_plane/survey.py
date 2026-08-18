"""Read-only repository survey: clone, worktrees, branches, and orphan work."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from control_plane.repository import trusted_git_argv, trusted_git_environment


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
        completed = subprocess.run(
            trusted_git_argv(repository, arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or len(completed.stdout) > _MAX_OUTPUT_BYTES:
        return None
    return completed.stdout


def _text(repository: Path, arguments: tuple[str, ...]) -> str | None:
    raw = _git(repository, arguments)
    return None if raw is None else raw.decode("utf-8", errors="replace").strip()


def _unknown(root: Path, code: str) -> RepositorySurvey:
    return RepositorySurvey(str(root), "", "", "", (), (), 0, 0, "UNKNOWN", code)


def _worktrees(root: Path, limit: int) -> tuple[WorktreeObservation, ...] | None:
    listing = _text(root, ("worktree", "list", "--porcelain"))
    if listing is None:
        return None
    entries: list[WorktreeObservation] = []
    path = head = branch = ""
    detached = False
    for line in listing.splitlines() + [""]:
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("HEAD "):
            head = line[len("HEAD "):]
        elif line.startswith("branch "):
            branch = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "detached":
            detached = True
        elif not line and path:
            if len(entries) >= limit:
                return None
            dirty = untracked = 0
            status = _text(Path(path), ("status", "--porcelain", "-uall"))
            if status:
                for item in status.splitlines():
                    if item.startswith("??"):
                        untracked += 1
                    else:
                        dirty += 1
            entries.append(
                WorktreeObservation(path, branch, head, detached, dirty, untracked)
            )
            path = head = branch = ""
            detached = False
    return tuple(entries)


def _branches(
    root: Path,
    base: str,
    limit: int,
) -> tuple[BranchObservation, ...] | None:
    listing = _text(
        root,
        ("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"),
    )
    if listing is None:
        return None
    entries: list[BranchObservation] = []
    for line in listing.splitlines():
        name, _, head = line.partition(" ")
        if not name or name == base:
            continue
        if len(entries) >= limit:
            return None
        added = _text(
            root, ("diff", "--diff-filter=A", "--name-only", f"{base}..{name}")
        )
        if added is None:
            return None
        only = len([item for item in added.splitlines() if item])
        entries.append(BranchObservation(name, head, only, only == 0))
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
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("E_SURVEY_LIMIT: invalid survey limit")
    root = repository.resolve()
    common = _text(root, ("rev-parse", "--path-format=absolute", "--git-common-dir"))
    if common is None:
        return _unknown(root, "E_SURVEY_INVENTORY")
    if _text(root, ("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")) is None:
        return _unknown(root, "E_SURVEY_BASE_UNKNOWN")
    head = _text(root, ("rev-parse", "HEAD"))
    branch = _text(root, ("rev-parse", "--abbrev-ref", "HEAD"))
    if head is None or branch is None:
        return _unknown(root, "E_SURVEY_INVENTORY")
    worktrees = _worktrees(root, max_worktrees)
    if worktrees is None:
        return _unknown(root, "E_SURVEY_LIMIT")
    branches = _branches(root, base, max_branches)
    if branches is None:
        return _unknown(root, "E_SURVEY_LIMIT")
    stash_listing = _text(root, ("stash", "list"))
    stashes = 0 if not stash_listing else len(stash_listing.splitlines())
    untracked_total = sum(item.untracked for item in worktrees)
    orphan = stashes or untracked_total
    return RepositorySurvey(
        str(root),
        common,
        branch,
        head,
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
