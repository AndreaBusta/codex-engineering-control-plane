"""Closed Git and repository observations for adoption enablement."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
import tomllib
from typing import Mapping, Sequence

from .contracts import (
    ADOPTION_LIFECYCLE,
    MANAGED_PARENT_PATHS,
    MANAGED_REPOSITORY_SCAN,
    contract_digest,
)
from .safe_io import canonical_root, confined_lstat, metadata_identity, read_confined_file


GIT = Path("/usr/bin/git")
GIT_TIMEOUT = 5.0
GIT_OUTPUT_MAX = 1024 * 1024
PROCESS_REAP_SECONDS = 0.25
AUTHORITY_FILE_MAX = 1024 * 1024
FILTER_CONFIG_MAX = 64 * 1024
MANAGED_PATHS = (
    ".codex/control-plane.lock",
    ".codex/hooks.json",
    ".codex/hooks/control_plane_hook.py",
    ".codex/git-hooks/pre-commit",
    ".codex/git-hooks/pre-push",
    "scripts/control-plane",
    "control_plane",
)
MANAGED_SCAN_ROOTS = (".codex", "control_plane", "scripts")
MANAGED_SCAN_ENTRY_MAX = 4_096
MANAGED_SCAN_DEPTH_MAX = 32
MANAGED_SCAN_PATH_MAX = 4_096
STATE_ROOTS = (
    "codex-control-plane-core",
    "codex-control-plane",
)
PROVISIONING_PREFIXES = frozenset(
    {"P1", "P2", "P2Q", "P3", "P3Q", "P4", "P4T"}
)
_PROVISIONING_TEMP = re.compile(r"^\.journal\.json\.[0-9a-f]{32}\.tmp$", re.ASCII)
_PROVISIONING_ADOPTION_QUARANTINE = ".provisioning-adoption"
_PROVISIONING_LOCKS_QUARANTINE = ".provisioning-locks"
GIT_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/var/empty",
    "XDG_CONFIG_HOME": "/var/empty",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
}
GIT_PREFIX = (
    str(GIT),
    "--no-pager",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.pager=cat",
    "-c",
    "color.ui=false",
    "-c",
    "diff.external=",
)


@dataclass(frozen=True)
class SourceObservation:
    repository_id: tuple[int, int]
    head: str
    tree: str
    product_version: str
    runtime_digest: str
    source_lock_digest: str


@dataclass(frozen=True)
class TargetObservation:
    repository_id: tuple[int, int]
    common_dir_id: tuple[int, int]
    worktree_id: tuple[int, int]
    branch: str
    head: str
    policy_digest: str
    registry_digest: str
    before_snapshot_digest: str
    core_hooks_path_before: None
    managed_parent_directories: tuple[Mapping[str, object], ...]
    managed_repository_scan: Mapping[str, object]


def _managed_parent_directories(repository: Path) -> tuple[dict[str, object], ...]:
    root = canonical_root(repository)
    root_before = metadata_identity(root.lstat())
    observed: list[dict[str, object]] = []
    for relative in MANAGED_PARENT_PATHS:
        metadata = confined_lstat(root, relative)
        if metadata is None:
            observed.append({"path": relative, "state": "absent"})
            continue
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or mode & 0o022
            or bool(int(getattr(metadata, "st_flags", 0)) & 0x40000000)
        ):
            raise ValueError("E_ADOPTION_TARGET_DRIFT: managed parent is unsafe")
        observed.append(
            {
                "path": relative,
                "state": "present",
                "identity": [int(metadata.st_dev), int(metadata.st_ino)],
                "mode": mode,
            }
        )
    if metadata_identity(root.lstat()) != root_before:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: target changed during parent observation")
    return tuple(observed)


def _scan_directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _safe_scanned_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
        and not bool(int(getattr(metadata, "st_flags", 0)) & 0x40000000)
    )


def _assert_no_nested_repositories(repository: Path) -> dict[str, object]:
    """Reject repository markers in the bounded managed projection only."""

    root = canonical_root(repository)
    root_fd = os.open(root, _scan_directory_flags())
    observed_entries = 0

    def scan(descriptor: int, relative: str, depth: int) -> None:
        nonlocal observed_entries
        if depth > MANAGED_SCAN_DEPTH_MAX:
            raise ValueError("E_ADOPTION_TARGET_BOUNDS: managed scan depth exceeded")
        before = os.fstat(descriptor)
        if not _safe_scanned_directory(before):
            raise ValueError("E_ADOPTION_TARGET_DRIFT: managed scan directory is unsafe")
        entries: list[tuple[str, os.stat_result]] = []
        try:
            with os.scandir(descriptor) as iterator:
                for entry in iterator:
                    observed_entries += 1
                    if observed_entries > MANAGED_SCAN_ENTRY_MAX:
                        raise ValueError(
                            "E_ADOPTION_TARGET_BOUNDS: managed scan entry limit exceeded"
                        )
                    name = entry.name
                    try:
                        encoded = f"{relative}/{name}".encode("utf-8", errors="strict")
                    except UnicodeEncodeError as error:
                        raise ValueError(
                            "E_ADOPTION_TARGET_BOUNDS: managed scan path is not UTF-8"
                        ) from error
                    if len(encoded) > MANAGED_SCAN_PATH_MAX:
                        raise ValueError(
                            "E_ADOPTION_TARGET_BOUNDS: managed scan path is too long"
                        )
                    if name.casefold() == ".git":
                        raise ValueError(
                            "E_ADOPTION_NESTED_REPOSITORY: nested Git marker is unsupported"
                        )
                    metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    entries.append((name, metadata))
        except ValueError:
            raise
        except OSError as error:
            raise ValueError(
                "E_ADOPTION_TARGET_DRIFT: managed scan is unavailable"
            ) from error

        names = {name.casefold() for name, _ in entries}
        if {"head", "config", "objects"}.issubset(names) and (
            "refs" in names or "packed-refs" in names
        ):
            raise ValueError(
                "E_ADOPTION_NESTED_REPOSITORY: nested bare repository is unsupported"
            )

        for name, metadata in sorted(entries, key=lambda item: item[0]):
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                continue
            try:
                child = os.open(name, _scan_directory_flags(), dir_fd=descriptor)
            except OSError as error:
                raise ValueError(
                    "E_ADOPTION_TARGET_DRIFT: managed directory cannot be opened"
                ) from error
            try:
                if metadata_identity(metadata) != metadata_identity(os.fstat(child)):
                    raise ValueError(
                        "E_ADOPTION_TARGET_DRIFT: managed directory identity changed"
                    )
                scan(child, f"{relative}/{name}", depth + 1)
                after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if metadata_identity(metadata) != metadata_identity(after):
                    raise ValueError(
                        "E_ADOPTION_TARGET_DRIFT: managed directory identity changed"
                    )
            finally:
                os.close(child)
        if metadata_identity(before) != metadata_identity(os.fstat(descriptor)):
            raise ValueError("E_ADOPTION_TARGET_DRIFT: managed scan directory changed")

    try:
        for relative in MANAGED_SCAN_ROOTS:
            try:
                metadata = os.stat(relative, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not _safe_scanned_directory(metadata):
                raise ValueError("E_ADOPTION_TARGET_DRIFT: managed scan root is unsafe")
            descriptor = os.open(relative, _scan_directory_flags(), dir_fd=root_fd)
            try:
                if metadata_identity(metadata) != metadata_identity(os.fstat(descriptor)):
                    raise ValueError(
                        "E_ADOPTION_TARGET_DRIFT: managed scan root identity changed"
                    )
                scan(descriptor, relative, 1)
                after = os.stat(relative, dir_fd=root_fd, follow_symlinks=False)
                if metadata_identity(metadata) != metadata_identity(after):
                    raise ValueError(
                        "E_ADOPTION_TARGET_DRIFT: managed scan root identity changed"
                    )
            finally:
                os.close(descriptor)
    finally:
        os.close(root_fd)

    staged = _run_git(
        root,
        "ls-files",
        "--stage",
        "-z",
        "--",
        *MANAGED_SCAN_ROOTS,
    )
    records = staged.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    if len(records) > MANAGED_SCAN_ENTRY_MAX:
        raise ValueError("E_ADOPTION_TARGET_BOUNDS: managed Git index exceeds its bound")
    for record in records:
        try:
            header, raw_path = record.split(b"\t", 1)
            mode = header.split(b" ", 1)[0]
            path = raw_path.decode("utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("E_ADOPTION_TARGET_DRIFT: managed Git index is malformed") from error
        if len(path.encode("utf-8")) > MANAGED_SCAN_PATH_MAX:
            raise ValueError("E_ADOPTION_TARGET_BOUNDS: managed Git path is too long")
        if mode == b"160000":
            raise ValueError(
                "E_ADOPTION_NESTED_REPOSITORY: managed Gitlink is unsupported"
            )
    return dict(MANAGED_REPOSITORY_SCAN)


def target_surface_digest(
    binding: Mapping[str, object],
    *,
    managed_parent_directories: Sequence[Mapping[str, object]],
    managed_repository_scan: Mapping[str, object],
) -> str:
    expected = {
        "repository_id",
        "common_dir_id",
        "worktree_id",
        "branch",
        "head",
        "policy_digest",
        "registry_digest",
        "adoption_lifecycle",
    }
    if set(binding) != expected:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: target surface binding is invalid")
    if binding.get("adoption_lifecycle") != ADOPTION_LIFECYCLE:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: lifecycle policy binding is invalid")
    parent_paths = [item.get("path") for item in managed_parent_directories]
    if parent_paths != list(MANAGED_PARENT_PATHS):
        raise ValueError("E_ADOPTION_TARGET_DRIFT: managed parent binding is invalid")
    for item in managed_parent_directories:
        state = item.get("state")
        if state == "absent":
            valid = set(item) == {"path", "state"}
        else:
            identity = item.get("identity")
            mode = item.get("mode")
            valid = (
                state == "present"
                and set(item) == {"path", "state", "identity", "mode"}
                and isinstance(identity, list)
                and len(identity) == 2
                and all(type(value) is int and value >= 0 for value in identity)
                and type(mode) is int
                and 0 <= mode <= 0o7777
                and mode & 0o022 == 0
            )
        if not valid:
            raise ValueError("E_ADOPTION_TARGET_DRIFT: managed parent binding is invalid")
    if dict(managed_repository_scan) != MANAGED_REPOSITORY_SCAN:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: managed repository scan is invalid")
    return contract_digest(
        {
            **dict(binding),
            "managed_paths_absent": list(MANAGED_PATHS),
            "managed_parent_directories": [
                dict(item) for item in managed_parent_directories
            ],
            "managed_repository_scan": dict(managed_repository_scan),
            "core_hooks_path_before": None,
        }
    )


def _cleanup_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=PROCESS_REAP_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=PROCESS_REAP_SECONDS)
        else:
            process.wait(timeout=0)
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=PROCESS_REAP_SECONDS)
        except (OSError, subprocess.SubprocessError):
            return
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=PROCESS_REAP_SECONDS)
        except (OSError, subprocess.SubprocessError):
            return


def _run_closed_command(
    arguments: Sequence[str],
    *,
    cwd: Path,
    code: str,
    maximum: int = GIT_OUTPUT_MAX,
    timeout: float = GIT_TIMEOUT,
    allowed_returncodes: Sequence[int] = (0,),
) -> bytes:
    if maximum < 0 or timeout <= 0:
        raise ValueError("E_ADOPTION_GIT: command bounds are invalid")
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    returncode: int | None = None
    try:
        process = subprocess.Popen(
            list(arguments),
            cwd=cwd,
            env=GIT_ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise ValueError("E_ADOPTION_GIT: command pipes are unavailable")
        selector = selectors.DefaultSelector()
        for stream, target in ((process.stdout, stdout), (process.stderr, stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, target)
        deadline = time.monotonic() + timeout
        open_streams = 2
        while open_streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError(f"{code}_TIMEOUT: command exceeded its deadline")
            events = selector.select(timeout=min(0.05, remaining))
            for key, _ in events:
                target = key.data
                allowance = maximum + 1 - len(stdout) - len(stderr)
                chunk = os.read(key.fd, min(65_536, max(1, allowance)))
                if chunk:
                    target.extend(chunk)
                    if len(stdout) + len(stderr) > maximum:
                        raise ValueError(f"{code}_OUTPUT: command output exceeded its limit")
                    continue
                selector.unregister(key.fileobj)
                key.fileobj.close()
                open_streams -= 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError(f"{code}_TIMEOUT: command exceeded its deadline")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"{code}_TIMEOUT: command exceeded its deadline") from error
        if returncode not in tuple(allowed_returncodes):
            raise ValueError(f"{code}: closed command failed")
        return bytes(stdout)
    except ValueError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"{code}: closed command failed") from error
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        _cleanup_process(process)


def _run_git(
    repository: Path,
    *arguments: str,
    maximum: int = GIT_OUTPUT_MAX,
    timeout: float = GIT_TIMEOUT,
    allowed_returncodes: Sequence[int] = (0,),
) -> bytes:
    return _run_closed_command(
        [*GIT_PREFIX, "-C", str(repository), *arguments],
        cwd=repository,
        code="E_ADOPTION_GIT",
        maximum=maximum,
        timeout=timeout,
        allowed_returncodes=allowed_returncodes,
    )


def _validate_target_authority(root: Path, authority_source: Path) -> None:
    source = canonical_root(authority_source)
    entrypoint = source / "scripts" / "control-plane"
    entrypoint_payload = read_confined_file(
        source,
        "scripts/control-plane",
        maximum=AUTHORITY_FILE_MAX,
    )
    entrypoint_metadata = confined_lstat(source, "scripts/control-plane")
    if (
        not entrypoint_payload
        or entrypoint_metadata is None
        or not stat.S_ISREG(entrypoint_metadata.st_mode)
        or not entrypoint_metadata.st_mode & stat.S_IXUSR
    ):
        raise ValueError("E_ADOPTION_TARGET_POLICY: selected Core entrypoint is unsafe")
    policy = root / ".codex" / "project-policy.toml"
    registry = root / ".codex" / "resource-registry.toml"
    commands = (
        (str(entrypoint), "policy-check", "--policy", str(policy), "--json"),
        (
            str(entrypoint),
            "registry-check",
            "--registry",
            str(registry),
            "--policy",
            str(policy),
            "--json",
        ),
    )
    try:
        for command in commands:
            _run_closed_command(
                command,
                cwd=source,
                code="E_ADOPTION_TARGET_POLICY",
            )
    except ValueError as error:
        raise ValueError(
            "E_ADOPTION_TARGET_POLICY: canonical policy or registry validation failed"
        ) from error


def _text(payload: bytes, *, code: str, maximum: int = 4096) -> str:
    if len(payload) > maximum:
        raise ValueError(f"{code}: output exceeds its bound")
    try:
        value = payload.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"{code}: output is not UTF-8") from error
    if not value or "\x00" in value or "\n" in value:
        raise ValueError(f"{code}: output is malformed")
    return value


def _repo_identity(root: Path) -> tuple[int, int]:
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("E_ADOPTION_PATH: repository root is not a directory")
    return int(metadata.st_dev), int(metadata.st_ino)


def _inside_worktree(root: Path, *, code: str) -> None:
    inside = _text(_run_git(root, "rev-parse", "--is-inside-work-tree"), code=code)
    bare = _text(_run_git(root, "rev-parse", "--is-bare-repository"), code=code)
    if inside != "true" or bare != "false":
        raise ValueError(f"{code}: repository must be a non-bare worktree")


def _head(root: Path, *, code: str) -> str:
    value = _text(_run_git(root, "rev-parse", "--verify", "HEAD^{commit}"), code=code)
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{code}: HEAD is invalid")
    return value


def _clean(root: Path, *, code: str) -> None:
    status = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=normal",
    )
    if status:
        raise ValueError(f"{code}: worktree is not clean")


def _reject_content_filters(root: Path) -> None:
    configured = _run_git(
        root,
        "config",
        "--includes",
        "--get-regexp",
        r"^filter\..*\.(clean|smudge|process)$",
        maximum=FILTER_CONFIG_MAX,
        allowed_returncodes=(0, 1),
    )
    if configured:
        raise ValueError("E_ADOPTION_GIT_FILTER: executable Git filters are unsupported")


def _lock_payload(root: Path) -> tuple[bytes, dict[str, object]]:
    payload = read_confined_file(
        root,
        ".codex/control-plane.lock",
        maximum=AUTHORITY_FILE_MAX,
    )
    try:
        value = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("E_ADOPTION_SOURCE_LOCK: source lock is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("E_ADOPTION_SOURCE_LOCK: source lock is invalid")
    return payload, value


def observe_source(repository: Path) -> SourceObservation:
    root = canonical_root(repository)
    before_identity = metadata_identity(root.lstat())
    _inside_worktree(root, code="E_ADOPTION_SOURCE_REPOSITORY")
    _reject_content_filters(root)
    head = _head(root, code="E_ADOPTION_SOURCE_HEAD")
    tree = _text(
        _run_git(root, "rev-parse", "--verify", "HEAD^{tree}"),
        code="E_ADOPTION_SOURCE_TREE",
    )
    _clean(root, code="E_ADOPTION_SOURCE_DIRTY")
    lock_payload, lock = _lock_payload(root)
    digests = lock.get("digests")
    product_version = lock.get("product_version")
    runtime_digest = digests.get("runtime") if isinstance(digests, dict) else None
    if (
        product_version != "3.1.0-core.2"
        or not isinstance(runtime_digest, str)
        or not runtime_digest.startswith("sha256:")
        or len(runtime_digest) != 71
    ):
        raise ValueError("E_ADOPTION_SOURCE_LOCK: source lock binding is unsupported")
    _clean(root, code="E_ADOPTION_SOURCE_DIRTY")
    if metadata_identity(root.lstat()) != before_identity:
        raise ValueError("E_ADOPTION_SOURCE_DRIFT: source identity changed")
    return SourceObservation(
        repository_id=_repo_identity(root),
        head=head,
        tree=tree,
        product_version=product_version,
        runtime_digest=runtime_digest,
        source_lock_digest=f"sha256:{sha256(lock_payload).hexdigest()}",
    )


def _canonical_git_directory(root: Path, *arguments: str) -> Path:
    raw = _text(_run_git(root, *arguments), code="E_ADOPTION_TARGET_REPOSITORY")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    return canonical_root(candidate)


def _local_git_config_identity(root: Path) -> tuple[int, ...]:
    """Bind the exact private regular config leaf used by local Git writes."""

    git_directory = _canonical_git_directory(
        root,
        "rev-parse",
        "--absolute-git-dir",
    )
    metadata = confined_lstat(git_directory, "config")
    if (
        metadata is None
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not 0 <= metadata.st_size <= AUTHORITY_FILE_MAX
        or int(getattr(metadata, "st_flags", 0)) != 0
    ):
        raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config is unsafe")
    return metadata_identity(metadata)


def _assert_single_worktree(root: Path) -> None:
    listing = _run_git(root, "worktree", "list", "--porcelain", "-z")
    fields = listing.split(b"\0")
    if (
        not fields
        or fields[-1] != b""
        or sum(field.startswith(b"worktree ") for field in fields) != 1
    ):
        raise ValueError("E_ADOPTION_TARGET_WORKTREES: target must have one worktree")


def _reject_existing(root: Path, relatives: Sequence[str]) -> None:
    for relative in relatives:
        if confined_lstat(root, relative) is not None:
            raise ValueError("E_ADOPTION_NOT_FRESH: target contains managed or runtime state")


def _private_provisioning_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o700
        and not bool(int(getattr(metadata, "st_flags", 0)) & 0x40000000)
    )


def _private_provisioning_file(
    metadata: os.stat_result,
    *,
    maximum: int,
) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and 0 <= metadata.st_size <= maximum
        and not bool(int(getattr(metadata, "st_flags", 0)) & 0x40000000)
    )


def _provisioning_state(common_directory: Path) -> str:
    relative = "codex-control-plane-core"
    metadata = confined_lstat(common_directory, relative)
    if metadata is None:
        return "ABSENT"
    if not _private_provisioning_directory(metadata):
        raise ValueError("E_ADOPTION_LOCK: adoption lock root is unsafe")
    root = common_directory / relative
    try:
        canonical = canonical_root(root)
        with os.scandir(canonical) as entries:
            observed = sorted(entry.name for entry in entries)
    except (OSError, ValueError) as error:
        raise ValueError("E_ADOPTION_LOCK: adoption lock root is unsafe") from error
    if not observed:
        return "ROOT_EMPTY"
    if "adoption.lock" not in observed:
        return "BLOCKED"
    lock = confined_lstat(common_directory, f"{relative}/adoption.lock")
    if lock is None or not _private_provisioning_file(lock, maximum=0):
        raise ValueError("E_ADOPTION_LOCK: adoption lock file is unsafe")
    if observed == ["adoption.lock"]:
        return "P1"
    if set(observed) == {"adoption.lock", _PROVISIONING_ADOPTION_QUARANTINE}:
        quarantine = canonical_root(root / _PROVISIONING_ADOPTION_QUARANTINE)
        quarantine_metadata = confined_lstat(
            common_directory,
            f"{relative}/{_PROVISIONING_ADOPTION_QUARANTINE}",
        )
        if quarantine_metadata is None or not _private_provisioning_directory(
            quarantine_metadata
        ):
            raise ValueError("E_ADOPTION_LOCK: provisioning quarantine is unsafe")
        try:
            with os.scandir(quarantine) as entries:
                if any(True for _ in entries):
                    return "BLOCKED"
        except OSError as error:
            raise ValueError("E_ADOPTION_LOCK: provisioning quarantine is unavailable") from error
        return "P2Q"
    if "adoption" not in observed:
        return "BLOCKED"
    adoption_metadata = confined_lstat(common_directory, f"{relative}/adoption")
    if adoption_metadata is None or not _private_provisioning_directory(
        adoption_metadata
    ):
        raise ValueError("E_ADOPTION_LOCK: provisioning directory is unsafe")
    adoption = canonical_root(root / "adoption")
    try:
        with os.scandir(adoption) as entries:
            adoption_names = sorted(entry.name for entry in entries)
    except OSError as error:
        raise ValueError("E_ADOPTION_LOCK: provisioning inventory is unavailable") from error
    if observed == ["adoption", "adoption.lock"]:
        return "P2" if not adoption_names else "BLOCKED"
    if set(observed) == {
        "adoption",
        "adoption.lock",
        _PROVISIONING_LOCKS_QUARANTINE,
    }:
        quarantine = canonical_root(root / _PROVISIONING_LOCKS_QUARANTINE)
        quarantine_metadata = confined_lstat(
            common_directory,
            f"{relative}/{_PROVISIONING_LOCKS_QUARANTINE}",
        )
        if quarantine_metadata is None or not _private_provisioning_directory(
            quarantine_metadata
        ):
            raise ValueError("E_ADOPTION_LOCK: provisioning quarantine is unsafe")
        try:
            with os.scandir(quarantine) as entries:
                if any(True for _ in entries):
                    return "BLOCKED"
        except OSError as error:
            raise ValueError("E_ADOPTION_LOCK: provisioning quarantine is unavailable") from error
        return "P3Q" if not adoption_names else "BLOCKED"
    if observed != ["adoption", "adoption.lock", "locks"]:
        return "BLOCKED"
    locks_metadata = confined_lstat(common_directory, f"{relative}/locks")
    if locks_metadata is None or not _private_provisioning_directory(locks_metadata):
        raise ValueError("E_ADOPTION_LOCK: provisioning directory is unsafe")
    locks = canonical_root(root / "locks")
    try:
        with os.scandir(locks) as entries:
            lock_names = sorted(entry.name for entry in entries)
    except OSError as error:
        raise ValueError("E_ADOPTION_LOCK: provisioning inventory is unavailable") from error
    if not lock_names:
        return "P3" if not adoption_names else "BLOCKED"
    if lock_names != ["verification.lock"]:
        return "BLOCKED"
    verification = confined_lstat(
        common_directory,
        f"{relative}/locks/verification.lock",
    )
    if verification is None or not _private_provisioning_file(
        verification,
        maximum=0,
    ):
        raise ValueError("E_ADOPTION_LOCK: provisioning mutex is unsafe")
    if not adoption_names:
        return "P4"
    if len(adoption_names) != 1 or _PROVISIONING_TEMP.fullmatch(adoption_names[0]) is None:
        return "BLOCKED"
    temporary = confined_lstat(
        common_directory,
        f"{relative}/adoption/{adoption_names[0]}",
    )
    if temporary is None or not _private_provisioning_file(
        temporary,
        maximum=AUTHORITY_FILE_MAX,
    ):
        raise ValueError("E_ADOPTION_LOCK: provisioning journal temporary is unsafe")
    return "P4T"


def _validate_locked_adoption_root(
    common_directory: Path,
    *,
    provisioning_recovery: bool,
) -> None:
    observed = _provisioning_state(common_directory)
    expected = PROVISIONING_PREFIXES if provisioning_recovery else frozenset({"P1"})
    if observed not in expected:
        raise ValueError("E_ADOPTION_LOCK: adoption lock root is not exact")


def observe_target(
    repository: Path,
    *,
    authority_source: Path,
    adoption_lock_held: bool = False,
    provisioning_recovery: bool = False,
) -> TargetObservation:
    if provisioning_recovery and not adoption_lock_held:
        raise ValueError("E_ADOPTION_LOCK: provisioning recovery requires the lifecycle lock")
    root = canonical_root(repository)
    root_before = metadata_identity(root.lstat())
    _inside_worktree(root, code="E_ADOPTION_TARGET_REPOSITORY")
    _reject_content_filters(root)
    head = _head(root, code="E_ADOPTION_TARGET_HEAD")
    branch = _text(
        _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD"),
        code="E_ADOPTION_TARGET_BRANCH",
    )
    _reject_existing(root, MANAGED_PATHS)
    managed_repository_scan = _assert_no_nested_repositories(root)
    config_identity = _local_git_config_identity(root)
    hooks = _run_git(
        root,
        "config",
        "--local",
        "--get-all",
        "core.hooksPath",
        allowed_returncodes=(0, 1),
    )
    if hooks:
        raise ValueError("E_ADOPTION_NOT_FRESH: target already configures core.hooksPath")

    _assert_single_worktree(root)
    if _run_git(root, "submodule", "status", "--recursive"):
        raise ValueError("E_ADOPTION_NOT_FRESH: submodules are unsupported")

    git_directory = _canonical_git_directory(root, "rev-parse", "--absolute-git-dir")
    common_directory = _canonical_git_directory(root, "rev-parse", "--git-common-dir")
    if adoption_lock_held:
        _reject_existing(git_directory, ("codex-control-plane",))
        if common_directory != git_directory:
            _reject_existing(common_directory, ("codex-control-plane",))
            if confined_lstat(git_directory, "codex-control-plane-core") is not None:
                raise ValueError("E_ADOPTION_NOT_FRESH: worktree contains Core state")
        _validate_locked_adoption_root(
            common_directory,
            provisioning_recovery=provisioning_recovery,
        )
    else:
        _reject_existing(git_directory, STATE_ROOTS)
        if common_directory != git_directory:
            _reject_existing(common_directory, STATE_ROOTS)

    _clean(root, code="E_ADOPTION_TARGET_DIRTY")
    policy = read_confined_file(root, ".codex/project-policy.toml", maximum=AUTHORITY_FILE_MAX)
    registry = read_confined_file(root, ".codex/resource-registry.toml", maximum=AUTHORITY_FILE_MAX)
    for relative in (".codex/project-policy.toml", ".codex/resource-registry.toml"):
        _run_git(root, "ls-files", "--error-unmatch", "--", relative)
    try:
        policy_value = tomllib.loads(policy.decode("utf-8", errors="strict"))
        registry_value = tomllib.loads(registry.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("E_ADOPTION_TARGET_POLICY: target policy or registry is invalid") from error
    _validate_target_authority(root, authority_source)
    base_branch = policy_value.get("git", {}).get("base_branch") if isinstance(policy_value, dict) else None
    if (
        policy_value.get("schema_version") != 1
        or registry_value.get("schema_version") != 1
        or not isinstance(base_branch, str)
        or branch == base_branch
    ):
        raise ValueError("E_ADOPTION_TARGET_POLICY: target policy binding is unsupported")

    repository_id = _repo_identity(root)
    worktree_id = _repo_identity(git_directory)
    common_dir_id = _repo_identity(common_directory)
    policy_digest = f"sha256:{sha256(policy).hexdigest()}"
    registry_digest = f"sha256:{sha256(registry).hexdigest()}"
    managed_parent_directories = _managed_parent_directories(root)
    before_snapshot_digest = target_surface_digest(
        {
            "repository_id": list(repository_id),
            "common_dir_id": list(common_dir_id),
            "worktree_id": list(worktree_id),
            "branch": branch,
            "head": head,
            "policy_digest": policy_digest,
            "registry_digest": registry_digest,
            "adoption_lifecycle": ADOPTION_LIFECYCLE,
        },
        managed_parent_directories=managed_parent_directories,
        managed_repository_scan=managed_repository_scan,
    )
    _clean(root, code="E_ADOPTION_TARGET_DIRTY")
    if (
        metadata_identity(root.lstat()) != root_before
        or _repo_identity(git_directory) != worktree_id
        or _repo_identity(common_directory) != common_dir_id
        or _local_git_config_identity(root) != config_identity
        or _assert_no_nested_repositories(root) != managed_repository_scan
    ):
        raise ValueError("E_ADOPTION_TARGET_DRIFT: target identity changed")
    return TargetObservation(
        repository_id=repository_id,
        common_dir_id=common_dir_id,
        worktree_id=worktree_id,
        branch=branch,
        head=head,
        policy_digest=policy_digest,
        registry_digest=registry_digest,
        before_snapshot_digest=before_snapshot_digest,
        core_hooks_path_before=None,
        managed_parent_directories=tuple(
            dict(item) for item in managed_parent_directories
        ),
        managed_repository_scan=dict(managed_repository_scan),
    )
