"""Read-only inspection and fail-closed recovery diagnostics for legacy installs."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any, Mapping

from control_plane.repository import (
    discover_repository,
    git_common_dir,
    private_state_directory,
    trusted_git_environment,
    trusted_git_executable,
    worktree_git_dir,
)


_MAX_JOURNAL_BYTES = 1_048_576
_MAX_RECORDS = 1_024
_MAX_MANAGED_BYTES = 16 * 1_048_576
_MAX_CREATED_DESCENDANTS = 4_096
_MAX_CREATED_DEPTH = 32
_READ_CHUNK_BYTES = 65_536
_DATALESS_FLAG = 0x40000000
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_OID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$", re.ASCII)
_EXECUTABLE_NAMES = {"control-plane", "control_plane_hook.py", "pre-commit", "pre-push"}
_JOURNAL_REQUIRED = frozenset(
    {
        "schema_version",
        "status",
        "plan_id",
        "source_commit",
        "source_manifest_digest",
        "records",
        "installed_snapshot",
        "snapshot_records",
        "git_config_changes",
        "initial_git_config_values",
        "created_directories",
    }
)
_JOURNAL_OPTIONAL = frozenset({"warnings", "upgrade_history"})
_SNAPSHOT_FIELDS = frozenset(
    {
        "manifest_digest",
        "common_git_dir",
        "path",
        "staging_path",
        "hooks_path",
        "artifact_digests",
    }
)


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _is_dataless(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_flags", 0)) & _DATALESS_FLAG)


def _validate_regular(
    metadata: os.stat_result,
    *,
    limit: int,
    code: str,
    exact_mode: int | None = None,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size > limit
        or _is_dataless(metadata)
        or (
            exact_mode is not None
            and stat.S_IMODE(metadata.st_mode) != exact_mode
        )
    ):
        raise ValueError(f"{code}: file is not bounded private regular content")


def _read_descriptor(
    descriptor: int,
    *,
    before: os.stat_result,
    limit: int,
    code: str,
    exact_mode: int | None = None,
) -> tuple[bytes, os.stat_result]:
    opened = os.fstat(descriptor)
    _validate_regular(opened, limit=limit, code=code, exact_mode=exact_mode)
    if _identity(opened) != _identity(before):
        raise ValueError(f"{code}: file identity changed before read")
    chunks: list[bytes] = []
    observed = 0
    while True:
        chunk = os.read(
            descriptor,
            min(_READ_CHUNK_BYTES, limit + 1 - observed),
        )
        if not chunk:
            break
        chunks.append(chunk)
        observed += len(chunk)
        if observed > limit:
            raise ValueError(f"{code}: file exceeds its byte limit")
    after = os.fstat(descriptor)
    if _identity(after) != _identity(opened) or observed != opened.st_size:
        raise ValueError(f"{code}: file changed while read")
    return b"".join(chunks), opened


def _read_regular(
    path: Path,
    *,
    limit: int = _MAX_MANAGED_BYTES,
    code: str = "E_ADOPT_DRIFT",
    exact_mode: int | None = None,
) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
        _validate_regular(before, limit=limit, code=code, exact_mode=exact_mode)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError):
            raise
        raise ValueError(f"{code}: file cannot be opened safely") from error
    try:
        return _read_descriptor(
            descriptor,
            before=before,
            limit=limit,
            code=code,
            exact_mode=exact_mode,
        )
    finally:
        os.close(descriptor)


def _digest(path: Path) -> str:
    payload, _ = _read_regular(path)
    return "sha256:" + sha256(payload).hexdigest()


def _journal_path(repository: Path) -> Path:
    return worktree_git_dir(repository) / "codex-control-plane" / "adoption.json"


def _safe_target(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError("E_ADOPT_RECOVERY_UNKNOWN: managed path is unsafe")
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        if not current.exists():
            continue
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("E_ADOPT_RECOVERY_UNKNOWN: managed parent is unsafe")
    if candidate.is_symlink():
        raise ValueError("E_ADOPT_RECOVERY_UNKNOWN: managed leaf is unsafe")
    return candidate


def _read_journal(repository: Path) -> dict[str, Any] | None:
    with private_state_directory(
        worktree_git_dir(repository),
        ("codex-control-plane",),
        create=False,
        missing_ok=True,
        code="E_ADOPT_RECOVERY_UNKNOWN",
    ) as opened:
        if opened is None:
            return None
        _, parent_descriptor = opened
        try:
            before = os.stat("adoption.json", dir_fd=parent_descriptor, follow_symlinks=False)
            descriptor = os.open(
                "adoption.json",
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError("E_ADOPT_RECOVERY_UNKNOWN: journal is unavailable") from error
        try:
            payload, _ = _read_descriptor(
                descriptor,
                before=before,
                limit=_MAX_JOURNAL_BYTES,
                code="E_ADOPT_RECOVERY_UNKNOWN",
                exact_mode=0o600,
            )
        finally:
            os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("E_ADOPT_RECOVERY_UNKNOWN: journal is invalid") from error
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: installed journal is unsupported")
    return value


def _records(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = state.get("records")
    if (
        not isinstance(values, list)
        or len(values) > _MAX_RECORDS
        or not all(isinstance(item, Mapping) for item in values)
    ):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: record inventory is invalid")
    for item in values:
        if set(item) != {"path", "before_digest", "installed_digest", "backup"}:
            raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: managed record is invalid")
        if (
            not isinstance(item.get("path"), str)
            or not isinstance(item.get("installed_digest"), str)
            or _DIGEST.fullmatch(str(item["installed_digest"])) is None
            or (
                item.get("before_digest") is not None
                and (
                    not isinstance(item.get("before_digest"), str)
                    or _DIGEST.fullmatch(str(item["before_digest"])) is None
                )
            )
            or (
                item.get("backup") is not None
                and not isinstance(item.get("backup"), str)
            )
            or (item.get("backup") is not None and item.get("before_digest") is None)
        ):
            raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: managed record binding is invalid")
    paths = [str(item["path"]) for item in values]
    if len(paths) != len(set(paths)):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: managed record paths are duplicated")
    return list(values)


def _git_config_values(repository: Path) -> list[str]:
    try:
        completed = subprocess.run(
            [
                trusted_git_executable(),
                "-C",
                str(repository),
                "config",
                "--local",
                "--get-all",
                "core.hooksPath",
            ],
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError("E_ADOPT_HOOK_CONFIG: Git config is unavailable") from error
    if completed.returncode not in {0, 1}:
        raise ValueError("E_ADOPT_HOOK_CONFIG: Git config is unavailable")
    return completed.stdout.splitlines() if completed.returncode == 0 else []


def _expected_installed_config(state: Mapping[str, Any]) -> list[str]:
    changes = state.get("git_config_changes", [])
    if not isinstance(changes, list) or len(changes) > 1:
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: Git config record is invalid")
    if not changes:
        return []
    change = changes[0]
    if (
        not isinstance(change, Mapping)
        or change.get("key") not in {None, "core.hooksPath"}
        or not isinstance(change.get("planned_value"), str)
        or not Path(str(change["planned_value"])).is_absolute()
    ):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: Git config record is invalid")
    return [str(change["planned_value"])]


def _initial_config(state: Mapping[str, Any]) -> list[str]:
    values = state.get("initial_git_config_values")
    if (
        not isinstance(values, list)
        or len(values) > 1
        or not all(isinstance(item, str) for item in values)
    ):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: prior Git config is invalid")
    return list(values)


def _journal_contract(state: Mapping[str, Any]) -> None:
    keys = set(state)
    if (
        not _JOURNAL_REQUIRED.issubset(keys)
        or not keys.issubset(_JOURNAL_REQUIRED | _JOURNAL_OPTIONAL)
        or state.get("schema_version") != 2
        or state.get("status") not in {"applied", "rolling_back", "rolled_back"}
        or not isinstance(state.get("plan_id"), str)
        or _DIGEST.fullmatch(str(state.get("plan_id"))) is None
        or not isinstance(state.get("source_commit"), str)
        or _OID.fullmatch(str(state.get("source_commit"))) is None
        or not isinstance(state.get("source_manifest_digest"), str)
        or _DIGEST.fullmatch(str(state.get("source_manifest_digest"))) is None
        or (
            "warnings" in state
            and not isinstance(state.get("warnings"), list)
        )
        or (
            "upgrade_history" in state
            and not isinstance(state.get("upgrade_history"), list)
        )
    ):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: installed journal shape is unsupported")
    _records(state)
    created = state.get("created_directories")
    if (
        not isinstance(created, list)
        or len(created) > _MAX_RECORDS
        or not all(isinstance(item, str) for item in created)
        or len(created) != len(set(created))
    ):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: created directory inventory is invalid")


def _snapshot_structure(
    repository: Path,
    raw: object,
    *,
    record: bool,
) -> tuple[dict[str, Any], Path]:
    expected_fields = _SNAPSHOT_FIELDS | ({"created"} if record else set())
    if not isinstance(raw, Mapping) or set(raw) != expected_fields:
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: installed snapshot record is invalid")
    manifest_digest = raw.get("manifest_digest")
    common = git_common_dir(repository)
    installs = common / "codex-control-plane" / "installs"
    expected_path = installs / str(manifest_digest)
    expected_staging = installs / f".{manifest_digest}.staging"
    artifact_digests = raw.get("artifact_digests")
    if (
        not isinstance(manifest_digest, str)
        or _DIGEST.fullmatch(manifest_digest) is None
        or raw.get("common_git_dir") != str(common)
        or raw.get("path") != str(expected_path)
        or raw.get("staging_path") != str(expected_staging)
        or raw.get("hooks_path") != str(expected_path / "git-hooks")
        or not isinstance(artifact_digests, Mapping)
        or not artifact_digests
        or len(artifact_digests) > _MAX_RECORDS
        or not all(
            isinstance(relative, str)
            and not PurePosixPath(relative).is_absolute()
            and ".." not in PurePosixPath(relative).parts
            and str(PurePosixPath(relative)) == relative
            and isinstance(value, str)
            and _DIGEST.fullmatch(value) is not None
            for relative, value in artifact_digests.items()
        )
        or (record and not isinstance(raw.get("created"), bool))
    ):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: installed snapshot binding is invalid")
    return dict(raw), expected_path


def _validate_live_snapshot(repository: Path, snapshot: Mapping[str, Any]) -> None:
    try:
        from control_plane.git_guards import _validate_snapshot

        observed = _validate_snapshot(
            canonical_repo=repository,
            common_git_dir=str(snapshot["common_git_dir"]),
            manifest_digest=str(snapshot["manifest_digest"]),
        )
    except (KeyError, OSError, TypeError, ValueError, RecursionError) as error:
        raise ValueError("E_ADOPT_DRIFT: installed snapshot is invalid") from error
    expected_digests = {"manifest.json": str(snapshot["manifest_digest"])}
    expected_digests.update(
        {
            str(value["path"]): str(value["digest"])
            for value in observed["artifacts"].values()
        }
    )
    if (
        str(observed["install_root"]) != str(snapshot["path"])
        or dict(snapshot["artifact_digests"]) != expected_digests
    ):
        raise ValueError("E_ADOPT_DRIFT: installed snapshot binding drifted")
    staging = Path(str(snapshot["staging_path"]))
    try:
        staging.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ValueError("E_ADOPT_DRIFT: snapshot staging path is unavailable") from error
    else:
        raise ValueError("E_ADOPT_DRIFT: snapshot staging path still exists")
    install_root = Path(str(observed["install_root"]))
    manifest_payload, _ = _read_regular(
        install_root / "manifest.json",
        limit=_MAX_JOURNAL_BYTES,
        exact_mode=0o600,
    )
    if "sha256:" + sha256(manifest_payload).hexdigest() != snapshot["manifest_digest"]:
        raise ValueError("E_ADOPT_DRIFT: snapshot manifest digest drifted")
    for artifact in observed["artifacts"].values():
        payload, _ = _read_regular(
            install_root / str(artifact["path"]),
            exact_mode=int(artifact["mode"]),
        )
        if "sha256:" + sha256(payload).hexdigest() != artifact["digest"]:
            raise ValueError("E_ADOPT_DRIFT: snapshot artifact digest drifted")


def _snapshot_records(
    repository: Path,
    state: Mapping[str, Any],
    *,
    recovering: bool,
) -> list[dict[str, Any]]:
    installed, _ = _snapshot_structure(
        repository, state.get("installed_snapshot"), record=False
    )
    raw_records = state.get("snapshot_records")
    if (
        not isinstance(raw_records, list)
        or not raw_records
        or len(raw_records) > _MAX_RECORDS
    ):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: snapshot inventory is invalid")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    installed_matches = 0
    for raw in raw_records:
        value, path = _snapshot_structure(repository, raw, record=True)
        digest = str(value["manifest_digest"])
        if digest in seen:
            raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: snapshot inventory is duplicated")
        seen.add(digest)
        if {key: value[key] for key in _SNAPSHOT_FIELDS} == installed:
            installed_matches += 1
        try:
            path.lstat()
        except FileNotFoundError:
            if not (recovering and value["created"] is True):
                raise ValueError("E_ADOPT_DRIFT: installed snapshot is unavailable") from None
        except OSError as error:
            raise ValueError("E_ADOPT_DRIFT: installed snapshot is unavailable") from error
        else:
            _validate_live_snapshot(repository, value)
        records.append(value)
    if installed_matches != 1:
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: active snapshot is not uniquely bound")
    planned = _expected_installed_config(state)
    if planned != [str(installed["hooks_path"])]:
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: hooksPath is not bound to the active snapshot")
    return records


def _preflight(repository: Path, state: Mapping[str, Any], *, recovering: bool) -> None:
    _journal_contract(state)
    _snapshot_records(repository, state, recovering=recovering)
    records = _records(state)
    for record in records:
        target = _safe_target(repository, str(record["path"]))
        installed = str(record["installed_digest"])
        before = record.get("before_digest")
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            observed = None
            observed_mode = None
        except OSError as error:
            raise ValueError("E_ADOPT_DRIFT: managed file is unavailable") from error
        else:
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("E_ADOPT_DRIFT: managed file is not regular")
            observed = _digest(target)
            observed_mode = stat.S_IMODE(metadata.st_mode)
        allowed = {installed}
        if recovering:
            allowed.add(before)
        if observed not in allowed:
            raise ValueError("E_ADOPT_DRIFT: rollback preflight failed; no files changed")
        if observed == installed:
            expected_mode = (
                0o755
                if PurePosixPath(str(record["path"])).name in _EXECUTABLE_NAMES
                else 0o644
            )
            if observed_mode != expected_mode:
                raise ValueError("E_ADOPT_DRIFT: installed managed mode drifted")
        backup = record.get("backup")
        if backup is not None:
            backup_path = _safe_target(worktree_git_dir(repository), str(backup))
            try:
                backup_payload, backup_metadata = _read_regular(backup_path)
            except ValueError as error:
                raise ValueError("E_ADOPT_DRIFT: rollback backup is unsafe") from error
            backup_digest = "sha256:" + sha256(backup_payload).hexdigest()
            if backup_digest != before:
                raise ValueError("E_ADOPT_DRIFT: rollback backup drifted; no files changed")
            if observed == before and observed_mode != stat.S_IMODE(backup_metadata.st_mode):
                raise ValueError("E_ADOPT_DRIFT: restored managed mode drifted")
    installed_config = _expected_installed_config(state)
    initial_config = _initial_config(state)
    observed_config = _git_config_values(repository)
    if observed_config not in ([installed_config, initial_config] if recovering else [installed_config]):
        raise ValueError("E_ADOPT_DRIFT: rollback Git config drifted; no files changed")
    created = state.get("created_directories")
    if not isinstance(created, list) or not all(isinstance(item, str) for item in created):
        raise ValueError("E_ADOPT_ROLLBACK_SCHEMA: created directory inventory is invalid")
    allowed_paths = {_safe_target(repository, str(record["path"])) for record in records}
    for relative in created:
        directory = _safe_target(repository, relative)
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("E_ADOPT_DRIFT: created directory is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("E_ADOPT_DRIFT: created directory drifted; no files changed")
        pending: list[tuple[Path, int]] = [(directory, 0)]
        observed = 0
        while pending:
            current, depth = pending.pop()
            if depth > _MAX_CREATED_DEPTH:
                raise ValueError("E_ADOPT_BOUNDS: created directory is too deep")
            try:
                with os.scandir(current) as scan:
                    for entry in scan:
                        observed += 1
                        if observed > _MAX_CREATED_DESCENDANTS:
                            raise ValueError(
                                "E_ADOPT_BOUNDS: created directory inventory is too large"
                            )
                        try:
                            child_metadata = entry.stat(follow_symlinks=False)
                        except OSError as error:
                            raise ValueError(
                                "E_ADOPT_DRIFT: created directory entry is unavailable"
                            ) from error
                        child = Path(entry.path)
                        if stat.S_ISLNK(child_metadata.st_mode):
                            raise ValueError(
                                "E_ADOPT_DRIFT: created directory is not prunable"
                            )
                        if stat.S_ISDIR(child_metadata.st_mode):
                            pending.append((child, depth + 1))
                        elif (
                            not stat.S_ISREG(child_metadata.st_mode)
                            or child not in allowed_paths
                        ):
                            raise ValueError(
                                "E_ADOPT_DRIFT: created directory is not prunable"
                            )
            except OSError as error:
                raise ValueError(
                    "E_ADOPT_DRIFT: created directory is unavailable"
                ) from error


def adoption_status(target: Path | str) -> dict[str, Any]:
    repository = discover_repository(Path(target))
    try:
        state = _read_journal(repository)
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        return {
            "schema_version": 2,
            "command": "adopt-status",
            "ok": False,
            "status": "UNKNOWN",
            "errors": [{"code": code, "message": str(error)}],
            "authorizes": False,
        }
    if state is None:
        return {
            "schema_version": 2,
            "command": "adopt-status",
            "ok": True,
            "status": "not_applied",
            "authorizes": False,
        }
    try:
        _journal_contract(state)
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        return {
            "schema_version": 2,
            "command": "adopt-status",
            "ok": False,
            "status": "UNKNOWN",
            "errors": [{"code": code, "message": str(error)}],
            "authorizes": False,
        }
    return {
        "schema_version": 2,
        "command": "adopt-status",
        "ok": True,
        "status": state["status"],
        "plan_id": state["plan_id"],
        "errors": [],
        "authorizes": False,
    }


def adoption_verify(target: Path | str) -> dict[str, Any]:
    repository = discover_repository(Path(target))
    state = _read_journal(repository)
    if state is None or state.get("status") != "applied":
        return {
            "schema_version": 2,
            "command": "adopt-verify",
            "ok": False,
            "status": "not_applied",
            "errors": [{"code": "E_ADOPT_NOT_APPLIED", "message": "No applied adoption journal."}],
            "authorizes": False,
        }
    try:
        _preflight(repository, state, recovering=False)
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        return {
            "schema_version": 2,
            "command": "adopt-verify",
            "ok": False,
            "status": "UNKNOWN" if code == "E_ADOPT_ROLLBACK_SCHEMA" else "drifted",
            "errors": [{"code": code, "message": str(error)}],
            "authorizes": False,
        }
    return {
        "schema_version": 2,
        "command": "adopt-verify",
        "ok": True,
        "status": "applied",
        "plan_id": state.get("plan_id"),
        "errors": [],
        "authorizes": False,
    }


def adoption_rollback(target: Path | str) -> dict[str, Any]:
    """Validate the legacy journal, then fail closed before any mutation."""

    repository = discover_repository(Path(target))
    initial = _read_journal(repository)
    if initial is None or initial.get("status") not in {"applied", "rolling_back"}:
        raise ValueError("E_ADOPT_NOT_APPLIED: no adoption to roll back")
    # The complete parser/preflight remains available and deliberately creates no
    # lock. Legacy v2.1 writers have no shared global lock that can close starts
    # for previously unseen task IDs, so caller-provided quiescence is insufficient.
    _preflight(repository, initial, recovering=initial.get("status") == "rolling_back")
    raise ValueError(
        "E_ADOPT_QUIESCENCE_UNKNOWN: the legacy runtime has no shared global "
        "writer barrier; rollback is disabled until quiescence is mechanically proven"
    )
