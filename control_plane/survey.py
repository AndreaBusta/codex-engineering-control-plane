"""Read-only repository survey: clone, worktrees, branches, and orphan work."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
import subprocess
import time

from control_plane.repository import trusted_git_argv, trusted_git_environment


class _SurveyLimit(Exception):
    pass


class _SurveyInventory(Exception):
    pass


class _SurveyRemoteUnknown(Exception):
    pass


_MAX_OUTPUT_BYTES = 1_048_576
_TIMEOUT_SECONDS = 10.0
_ADDED_PATHS_BUDGET_SECONDS = 10.0
_OID = re.compile(r"[0-9a-f]{40}")
_ZERO_OID = "0" * 40


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
    added_paths: int | None
    content_equivalent_to_base: bool
    has_unique_commits: bool
    remote_tracking_ref_present: bool
    unpublished_unique: bool


@dataclass(frozen=True)
class RepositorySurvey:
    root: str
    common_git_dir: str | None
    branch: str | None
    head: str | None
    base_ref: str
    base_head: str | None
    remote_name: str
    worktrees: tuple[WorktreeObservation, ...] | None
    branches: tuple[BranchObservation, ...] | None
    stashes: int | None
    untracked_total: int | None
    unpublished_unique_branches: int | None
    status: str
    error_code: str | None


def _git(repository: Path, arguments: tuple[str, ...], *, timeout: float = _TIMEOUT_SECONDS) -> bytes | None:
    try:
        completed = subprocess.run(
            trusted_git_argv(repository, arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or len(completed.stdout) > _MAX_OUTPUT_BYTES:
        return None
    return completed.stdout


def _text(repository: Path, arguments: tuple[str, ...], *, timeout: float = _TIMEOUT_SECONDS) -> str | None:
    raw = _git(repository, arguments, timeout=timeout)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return None


def _unknown(
    root: Path, code: str, *, base_ref: str, remote_name: str,
    common_git_dir: str | None = None,
    branch: str | None = None,
    head: str | None = None, base_head: str | None = None,
) -> RepositorySurvey:
    return RepositorySurvey(
        str(root), common_git_dir, branch, head, base_ref, base_head, remote_name,
        None, None, None, None, None, "UNKNOWN", code)


def _survey_status(*, stashes: int, untracked: int, unpublished: int) -> str:
    if unpublished:
        return "FAIL"
    if stashes or untracked:
        return "WARN"
    return "PASS"


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
                raise _SurveyLimit
            dirty = untracked = 0
            status = _text(Path(path), ("status", "--porcelain", "-uall"))
            if status is None:
                return None
            for item in status.splitlines():
                if item.startswith("??"):
                    untracked += 1
                elif item:
                    dirty += 1
            entries.append(
                WorktreeObservation(path, branch, head, detached, dirty, untracked)
            )
            path = head = branch = ""
            detached = False
    return tuple(entries)


def _valid_oid(value: str) -> bool:
    return _OID.fullmatch(value) is not None and value != _ZERO_OID


def _local_branch_inventory(
    root: Path, limit: int, *, merged_into: str | None = None,
) -> dict[str, tuple[str, str, str]]:
    arguments = ["for-each-ref"]
    if merged_into is not None:
        arguments.append(f"--merged={merged_into}")
    arguments.extend(
        (
            "--sort=refname",
            f"--count={limit + 1}",
            "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(tree)",
            "refs/heads/",
        )
    )
    listing = _text(root, tuple(arguments))
    if listing is None:
        raise _SurveyInventory
    rows = listing.splitlines() if listing else []
    if len(rows) > limit:
        raise _SurveyLimit
    inventory: dict[str, tuple[str, str, str]] = {}
    for row in rows:
        fields = row.split("\0")
        if len(fields) != 4:
            raise _SurveyInventory
        ref, head, object_type, tree = fields
        if (
            not ref.startswith("refs/heads/")
            or ref == "refs/heads/"
            or ref != ref.strip()
            or ref in inventory
            or object_type != "commit"
            or not _valid_oid(head)
            or not _valid_oid(tree)
        ):
            raise _SurveyInventory
        inventory[ref] = (head, object_type, tree)
    if tuple(inventory) != tuple(sorted(inventory)):
        raise _SurveyInventory
    return inventory


def _validate_remote_name(root: Path, remote_name: str) -> None:
    if not isinstance(remote_name, str) or not remote_name:
        raise _SurveyRemoteUnknown
    listing = _text(root, ("remote",))
    if listing is None:
        raise _SurveyRemoteUnknown
    names = listing.splitlines() if listing else []
    if names.count(remote_name) != 1 or len(names) != len(set(names)):
        raise _SurveyRemoteUnknown
    valid = _text(root, ("check-ref-format", f"refs/remotes/{remote_name}/sentinel"))
    if valid != "":
        raise _SurveyRemoteUnknown


def _remote_branch_inventory(
    root: Path, local_refs: tuple[str, ...], remote_name: str, limit: int,
) -> frozenset[str]:
    _validate_remote_name(root, remote_name)
    expected = {
        f"refs/remotes/{remote_name}/{ref.removeprefix('refs/heads/')}"
        for ref in local_refs
    }
    if not expected:
        return frozenset()
    listing = _text(
        root,
        (
            "for-each-ref",
            "--sort=refname",
            f"--count={limit + 1}",
            "--format=%(refname)%00%(objectname)%00%(objecttype)",
            *(f"[r]{ref[1:]}" for ref in sorted(expected)),
        ),
    )
    if listing is None:
        raise _SurveyRemoteUnknown
    rows = listing.splitlines() if listing else []
    if len(rows) > limit:
        raise _SurveyRemoteUnknown
    observed: list[str] = []
    for row in rows:
        fields = row.split("\0")
        if len(fields) != 3:
            raise _SurveyRemoteUnknown
        ref, head, object_type = fields
        if (
            ref not in expected
            or ref != ref.strip()
            or ref in observed
            or object_type != "commit"
            or not _valid_oid(head)
        ):
            raise _SurveyRemoteUnknown
        observed.append(ref)
    if observed != sorted(observed):
        raise _SurveyRemoteUnknown
    return frozenset(observed)


def _branches(
    root: Path, base_head: str, base_tree: str, remote_name: str, limit: int,
) -> tuple[BranchObservation, ...]:
    initial = _local_branch_inventory(root, limit)
    local_refs = tuple(initial)
    remote_refs = _remote_branch_inventory(root, local_refs, remote_name, limit)
    merged = _local_branch_inventory(root, limit, merged_into=base_head)
    for ref, identity in merged.items():
        if initial.get(ref) != identity:
            raise _SurveyInventory
    if _local_branch_inventory(root, limit) != initial:
        raise _SurveyInventory
    entries: list[BranchObservation] = []
    for ref, (head, _object_type, tree) in initial.items():
        name = ref.removeprefix("refs/heads/")
        remote_ref = f"refs/remotes/{remote_name}/{name}"
        equivalent = tree == base_tree
        unique = ref not in merged
        remote_present = remote_ref in remote_refs
        unpublished = equivalent is False and unique and not remote_present
        entries.append(BranchObservation(
            name, head, None, equivalent, unique, remote_present, unpublished))
    return tuple(entries)


def _optional_added_paths(
    root: Path, base_head: str, branches: tuple[BranchObservation, ...],
    *, clock=time.monotonic,
) -> tuple[BranchObservation, ...]:
    deadline = clock() + _ADDED_PATHS_BUDGET_SECONDS
    enriched: list[BranchObservation] = []
    exhausted = False
    for branch in branches:
        if exhausted:
            enriched.append(branch)
            continue
        remaining = deadline - clock()
        if remaining <= 0:
            exhausted = True
        else:
            added = _text(
                root,
                ("diff", "--diff-filter=A", "--name-only", f"{base_head}..{branch.head}"),
                timeout=min(_TIMEOUT_SECONDS, remaining),
            )
            if added is None:
                exhausted = True
            else:
                branch = replace(
                    branch,
                    added_paths=len([item for item in added.splitlines() if item]),
                )
        enriched.append(branch)
    return tuple(enriched)


def survey_repository(
    repository: Path,
    *,
    base: str = "origin/main",
    remote_name: str = "origin",
    max_worktrees: int = 64,
    max_branches: int = 64,
) -> RepositorySurvey:
    """Observe one clone. Other clones are never visible from here."""
    for limit in (max_worktrees, max_branches):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("E_SURVEY_LIMIT: invalid survey limit")
    root = repository.resolve()
    common = _text(root, ("rev-parse", "--path-format=absolute", "--git-common-dir"))
    if not common:
        return _unknown(
            root, "E_SURVEY_INVENTORY", base_ref=base, remote_name=remote_name
        )
    base_head = _text(
        root, ("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    )
    if base_head is None or not _valid_oid(base_head):
        return _unknown(
            root, "E_SURVEY_BASE_UNKNOWN", base_ref=base,
            remote_name=remote_name, common_git_dir=common,
        )
    base_tree = _text(
        root, ("rev-parse", "--verify", "--quiet", f"{base_head}^{{tree}}")
    )
    if base_tree is None or not _valid_oid(base_tree):
        return _unknown(
            root, "E_SURVEY_BASE_UNKNOWN", base_ref=base,
            remote_name=remote_name, common_git_dir=common, base_head=base_head,
        )
    head = _text(root, ("rev-parse", "HEAD"))
    branch = _text(root, ("rev-parse", "--abbrev-ref", "HEAD"))
    if head is None or not _valid_oid(head) or not branch:
        return _unknown(
            root, "E_SURVEY_INVENTORY", base_ref=base,
            remote_name=remote_name, common_git_dir=common, base_head=base_head,
        )
    try:
        worktrees = _worktrees(root, max_worktrees)
        if worktrees is None:
            raise _SurveyInventory
        if _text(root, ("rev-parse", "--is-shallow-repository")) != "false":
            raise _SurveyInventory
        branches = _branches(root, base_head, base_tree, remote_name, max_branches)
    except _SurveyLimit:
        code = "E_SURVEY_LIMIT"
    except _SurveyRemoteUnknown:
        code = "E_SURVEY_REMOTE_UNKNOWN"
    except _SurveyInventory:
        code = "E_SURVEY_INVENTORY"
    else:
        code = None
    if code is not None:
        return _unknown(
            root, code, base_ref=base, remote_name=remote_name,
            common_git_dir=common, branch=branch, head=head, base_head=base_head,
        )
    stash_listing = _text(root, ("stash", "list"))
    if stash_listing is None:
        return _unknown(
            root, "E_SURVEY_INVENTORY", base_ref=base, remote_name=remote_name,
            common_git_dir=common, branch=branch, head=head, base_head=base_head,
        )
    stashes = len([item for item in stash_listing.splitlines() if item])
    untracked_total = sum(item.untracked for item in worktrees)
    unpublished = sum(item.unpublished_unique for item in branches)
    status = _survey_status(stashes=stashes, untracked=untracked_total, unpublished=unpublished)
    error_code = None
    branches = _optional_added_paths(root, base_head, branches)
    return RepositorySurvey(
        str(root), common, branch, head, base, base_head, remote_name,
        worktrees, branches, stashes, untracked_total, unpublished,
        status, error_code,
    )


def survey_payload(survey: RepositorySurvey) -> dict:
    """Render the non-authorizing RepositorySurveyV2 mapping."""

    return {
        "schema_version": 2,
        "kind": "RepositorySurveyV2",
        "comparison": {
            "base_ref": survey.base_ref,
            "base_head": survey.base_head,
            "remote_name": survey.remote_name,
        },
        "clone": {
            "root": survey.root,
            "common_git_dir": survey.common_git_dir,
            "branch": survey.branch,
            "head": survey.head,
        },
        "worktrees": None
        if survey.worktrees is None
        else [
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
        "branches": None
        if survey.branches is None
        else [
            {
                "name": item.name,
                "head": item.head,
                "added_paths": item.added_paths,
                "content_equivalent_to_base": item.content_equivalent_to_base,
                "has_unique_commits": item.has_unique_commits,
                "remote_tracking_ref_present": item.remote_tracking_ref_present,
                "unpublished_unique": item.unpublished_unique,
            }
            for item in survey.branches
        ],
        "orphan_work": {
            "stashes": survey.stashes,
            "untracked_total": survey.untracked_total,
            "unpublished_unique_branches": survey.unpublished_unique_branches,
        },
        "other_clones": "UNKNOWN",
        "status": survey.status,
        "error_code": survey.error_code,
        "authorizes": False,
    }
