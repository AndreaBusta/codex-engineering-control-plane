from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tomllib
from typing import Sequence


GIT = Path("/usr/bin/git")
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
SHA_A = "sha256:" + "a" * 64
_CONTENT_SNAPSHOT_MAX_PATHS = 8_192
_CONTENT_SNAPSHOT_MAX_DIRECTORY_ENTRIES = 2_048
_CONTENT_SNAPSHOT_MAX_DEPTH = 128
_CONTENT_SNAPSHOT_MAX_PATH_BYTES = 4_096
_CONTENT_SNAPSHOT_MAX_FILE_BYTES = 4 * 1_048_576
_CONTENT_SNAPSHOT_MAX_TOTAL_BYTES = 32 * 1_048_576
_CONTENT_SNAPSHOT_READ_BYTES = 65_536


def git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        [
            str(GIT),
            "--no-pager",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "color.ui=false",
            "-c",
            "core.pager=cat",
            "-C",
            str(repository),
            *arguments,
        ],
        env=GIT_ENVIRONMENT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"fixture Git failed: {arguments!r}, rc={completed.returncode}, "
            f"stderr={completed.stderr[:1024]!r}"
        )
    return completed


def write_file(
    repository: Path,
    relative: str,
    payload: str | bytes,
    *,
    mode: int = 0o644,
) -> Path:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_bytes(payload)
    path.chmod(mode)
    return path


def initialize_repository(
    repository: Path,
    *,
    branch: str = "codex/adoption-target",
    files: Sequence[tuple[str, str | bytes, int]] = (),
) -> Path:
    repository.mkdir(parents=True, mode=0o700)
    git(repository, "init", "-b", branch)
    git(repository, "config", "user.name", "Control Plane Test")
    git(repository, "config", "user.email", "control-plane-test@example.invalid")
    for relative, payload, mode in files:
        write_file(repository, relative, payload, mode=mode)
    git(repository, "add", "--all")
    git(repository, "commit", "-m", "fixture")
    return repository.resolve(strict=True)


def initialize_fresh_target(repository: Path) -> Path:
    fixtures = Path(__file__).parent / "fixtures"
    return initialize_repository(
        repository,
        files=(
            (
                ".codex/project-policy.toml",
                (fixtures / "valid-policy.toml").read_bytes(),
                0o644,
            ),
            (
                ".codex/resource-registry.toml",
                (fixtures / "valid-registry.toml").read_bytes(),
                0o644,
            ),
            ("AGENTS.md", "# Temporary adoption target\n", 0o644),
        ),
    )


def initialize_governed_target(repository: Path, project_root: Path) -> Path:
    return initialize_repository(
        repository,
        files=(
            (
                ".codex/project-policy.toml",
                (project_root / ".codex" / "project-policy.toml").read_bytes(),
                0o644,
            ),
            (
                ".codex/resource-registry.toml",
                (project_root / ".codex" / "resource-registry.toml").read_bytes(),
                0o644,
            ),
            ("AGENTS.md", "# Temporary adoption target\n", 0o644),
        ),
    )


def initialize_source(repository: Path) -> Path:
    return initialize_repository(
        repository,
        branch="codex/source",
        files=(
            (
                ".codex/control-plane.lock",
                "schema_version = 2\n"
                "product_version = \"3.1.0-core.2\"\n"
                "runtime_package = \"control_plane\"\n"
                "runtime_layout = \"source\"\n"
                "runtime_modules = []\n"
                "[digests]\n"
                f"runtime = \"{SHA_A}\"\n",
                0o644,
            ),
            ("README.md", "# Temporary Core source\n", 0o644),
        ),
    )


def initialize_full_source(repository: Path, project_root: Path) -> Path:
    repository.mkdir(parents=True, mode=0o700)
    git(repository, "init", "-b", "codex/source")
    git(repository, "config", "user.name", "Control Plane Test")
    git(repository, "config", "user.email", "control-plane-test@example.invalid")
    lock = tomllib.loads(
        (project_root / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
    )
    modules = lock["runtime_modules"]
    paths = (
        ".codex/control-plane.lock",
        ".codex/hooks.json",
        ".codex/hooks/control_plane_hook.py",
        ".codex/git-hooks/pre-commit",
        ".codex/git-hooks/pre-push",
        "scripts/control-plane",
        "templates/new-project/AGENTS.md",
        "templates/new-project/README.md",
        "templates/new-project/.codex/project-policy.toml",
        "templates/new-project/.codex/resource-registry.toml",
        *(f"control_plane/{name}" for name in modules),
    )
    for relative in paths:
        source = project_root / relative
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    git(repository, "add", "--all")
    git(repository, "commit", "-m", "full Core source fixture")
    return repository.resolve(strict=True)


def metadata_snapshot(root: Path) -> tuple[tuple[str, int, int, int], ...]:
    records: list[tuple[str, int, int, int]] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in (*directories, *files):
            path = current_path / name
            metadata = path.lstat()
            records.append(
                (
                    path.relative_to(root).as_posix(),
                    metadata.st_mode,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                )
            )
    return tuple(records)


def _content_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(metadata.st_uid),
        int(metadata.st_gid),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        int(getattr(metadata, "st_flags", 0)),
    )


def _content_kind(metadata: os.stat_result) -> str:
    mode = metadata.st_mode
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    return "other"


def content_snapshot(
    root: Path,
) -> tuple[tuple[str, str, tuple[int, ...], str], ...]:
    """Capture bounded content and metadata without following descendants."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory_flag:
        raise AssertionError("content snapshot requires no-follow directory opens")
    try:
        root_before = root.lstat()
    except OSError as error:
        raise AssertionError("content snapshot root is unavailable") from error
    if not stat.S_ISDIR(root_before.st_mode) or stat.S_ISLNK(root_before.st_mode):
        raise AssertionError("content snapshot root must be a real directory")
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | nofollow | directory_flag
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as error:
        raise AssertionError("content snapshot root cannot be opened safely") from error

    records: list[tuple[str, str, tuple[int, ...], str]] = [
        (".", "directory", _content_identity(root_before), "")
    ]
    state = {"paths": 1, "total_bytes": 0}

    def relative_name(parts: tuple[str, ...]) -> str:
        relative = Path(*parts).as_posix()
        encoded = os.fsencode(relative)
        if (
            not relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or len(encoded) > _CONTENT_SNAPSHOT_MAX_PATH_BYTES
        ):
            raise AssertionError("content snapshot path is invalid or oversized")
        return relative

    def account_payload(size: int) -> None:
        if size < 0 or size > _CONTENT_SNAPSHOT_MAX_FILE_BYTES:
            raise AssertionError("content snapshot entry exceeds its byte limit")
        state["total_bytes"] += size
        if state["total_bytes"] > _CONTENT_SNAPSHOT_MAX_TOTAL_BYTES:
            raise AssertionError("content snapshot exceeds its total byte limit")

    def stable_stat(
        parent_descriptor: int,
        name: str,
        expected: os.stat_result,
    ) -> os.stat_result:
        try:
            observed = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise AssertionError("content snapshot entry changed or disappeared") from error
        if _content_identity(observed) != _content_identity(expected):
            raise AssertionError("content snapshot entry identity changed")
        return observed

    def read_regular(
        parent_descriptor: int,
        name: str,
        before: os.stat_result,
    ) -> str:
        account_payload(int(before.st_size))
        file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | nofollow
        try:
            descriptor = os.open(name, file_flags, dir_fd=parent_descriptor)
        except OSError as error:
            raise AssertionError("content snapshot file cannot be opened safely") from error
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _content_identity(opened) != _content_identity(before)
            ):
                raise AssertionError("content snapshot file identity changed before read")
            hasher = sha256()
            observed_size = 0
            while True:
                allowance = _CONTENT_SNAPSHOT_MAX_FILE_BYTES - observed_size
                chunk = os.read(
                    descriptor,
                    min(_CONTENT_SNAPSHOT_READ_BYTES, allowance + 1),
                )
                if not chunk:
                    break
                observed_size += len(chunk)
                if observed_size > _CONTENT_SNAPSHOT_MAX_FILE_BYTES:
                    raise AssertionError("content snapshot file exceeds its byte limit")
                hasher.update(chunk)
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = stable_stat(parent_descriptor, name, before)
        if (
            observed_size != int(before.st_size)
            or _content_identity(after_open) != _content_identity(before)
            or _content_identity(after_path) != _content_identity(before)
        ):
            raise AssertionError("content snapshot file changed during read")
        return "sha256:" + hasher.hexdigest()

    def scan_directory(
        directory_descriptor: int,
        parts: tuple[str, ...],
        depth: int,
    ) -> None:
        if depth > _CONTENT_SNAPSHOT_MAX_DEPTH:
            raise AssertionError("content snapshot exceeds its depth limit")
        try:
            with os.scandir(directory_descriptor) as entries:
                names: list[str] = []
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > _CONTENT_SNAPSHOT_MAX_DIRECTORY_ENTRIES:
                        raise AssertionError(
                            "content snapshot directory exceeds its entry limit"
                        )
        except OSError as error:
            raise AssertionError("content snapshot directory cannot be enumerated") from error
        if len(set(names)) != len(names):
            raise AssertionError("content snapshot directory contains duplicate names")

        for name in sorted(names):
            current_parts = (*parts, name)
            relative = relative_name(current_parts)
            state["paths"] += 1
            if state["paths"] > _CONTENT_SNAPSHOT_MAX_PATHS:
                raise AssertionError("content snapshot exceeds its path limit")
            try:
                before = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise AssertionError("content snapshot entry is unavailable") from error
            kind = _content_kind(before)
            payload = ""
            if kind == "regular":
                payload = read_regular(directory_descriptor, name, before)
            elif kind == "symlink":
                try:
                    target_before = os.readlink(name, dir_fd=directory_descriptor)
                except OSError as error:
                    raise AssertionError(
                        "content snapshot symlink target is unavailable"
                    ) from error
                target_size = len(os.fsencode(target_before))
                account_payload(target_size)
                stable_stat(directory_descriptor, name, before)
                try:
                    target_after = os.readlink(name, dir_fd=directory_descriptor)
                except OSError as error:
                    raise AssertionError(
                        "content snapshot symlink changed during read"
                    ) from error
                stable_stat(directory_descriptor, name, before)
                if target_after != target_before:
                    raise AssertionError("content snapshot symlink target changed")
                payload = target_before
            elif kind == "directory":
                try:
                    child_descriptor = os.open(
                        name,
                        directory_flags,
                        dir_fd=directory_descriptor,
                    )
                except OSError as error:
                    raise AssertionError(
                        "content snapshot directory cannot be opened safely"
                    ) from error
                try:
                    opened = os.fstat(child_descriptor)
                    if _content_identity(opened) != _content_identity(before):
                        raise AssertionError(
                            "content snapshot directory identity changed before open"
                        )
                    scan_directory(child_descriptor, current_parts, depth + 1)
                    after_open = os.fstat(child_descriptor)
                    after_path = stable_stat(directory_descriptor, name, before)
                    if (
                        _content_identity(after_open) != _content_identity(before)
                        or _content_identity(after_path) != _content_identity(before)
                    ):
                        raise AssertionError(
                            "content snapshot directory changed during traversal"
                        )
                finally:
                    os.close(child_descriptor)
            else:
                stable_stat(directory_descriptor, name, before)
            records.append((relative, kind, _content_identity(before), payload))

    try:
        root_opened = os.fstat(root_descriptor)
        if _content_identity(root_opened) != _content_identity(root_before):
            raise AssertionError("content snapshot root identity changed before open")
        scan_directory(root_descriptor, (), 0)
        root_after_open = os.fstat(root_descriptor)
    finally:
        os.close(root_descriptor)
    try:
        root_after_path = root.lstat()
    except OSError as error:
        raise AssertionError("content snapshot root disappeared") from error
    if (
        _content_identity(root_after_open) != _content_identity(root_before)
        or _content_identity(root_after_path) != _content_identity(root_before)
    ):
        raise AssertionError("content snapshot root changed during traversal")
    return tuple(sorted(records, key=lambda record: record[0]))
