"""Descriptor-relative bounded filesystem primitives for adoption enablement."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat


DATALESS_FLAG = 0x40000000
READ_CHUNK = 65_536


def metadata_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_uid),
        int(value.st_gid),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
        int(getattr(value, "st_flags", 0)),
    )


def _dataless(value: os.stat_result) -> bool:
    return bool(int(getattr(value, "st_flags", 0)) & DATALESS_FLAG)


def _private_directory(value: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(value.st_mode)
        and value.st_uid == os.geteuid()
        and stat.S_IMODE(value.st_mode) & 0o022 == 0
        and not _dataless(value)
    )


def canonical_root(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        resolved = absolute.resolve(strict=True)
        metadata = absolute.lstat()
    except OSError as error:
        raise ValueError("E_ADOPTION_PATH: root is unavailable") from error
    if absolute != resolved or not _private_directory(metadata):
        raise ValueError("E_ADOPTION_PATH: root is not canonical private content")
    return resolved


def _relative_parts(relative: str | Path) -> tuple[str, ...]:
    raw = os.fspath(relative)
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw.encode("utf-8")) > 4096
        or "\\" in raw
        or "\x00" in raw
    ):
        raise ValueError("E_ADOPTION_FILE: relative path is unsafe")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError("E_ADOPTION_FILE: relative path is unsafe")
    return tuple(path.parts)


def _open_directory_chain(root: Path, parts: tuple[str, ...]) -> list[int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        descriptor = os.open(root, flags)
        descriptors.append(descriptor)
        if not _private_directory(os.fstat(descriptor)):
            raise ValueError("E_ADOPTION_FILE: root directory is unsafe")
        for component in parts:
            descriptor = os.open(component, flags, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            if not _private_directory(os.fstat(descriptor)):
                raise ValueError("E_ADOPTION_FILE: path ancestor is unsafe")
        return descriptors
    except ValueError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    except OSError as error:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise ValueError("E_ADOPTION_FILE: path ancestor cannot be opened safely") from error


def confined_lstat(root: Path, relative: str | Path) -> os.stat_result | None:
    canonical = canonical_root(root)
    parts = _relative_parts(relative)
    try:
        descriptors = _open_directory_chain(canonical, parts[:-1])
    except ValueError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise
    try:
        try:
            return os.stat(
                parts[-1],
                dir_fd=descriptors[-1],
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError("E_ADOPTION_FILE: path cannot be observed safely") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_confined_file(
    root: Path,
    relative: str | Path,
    *,
    maximum: int,
) -> bytes:
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        raise ValueError("E_ADOPTION_FILE: file limit is invalid")
    canonical = canonical_root(root)
    parts = _relative_parts(relative)
    descriptors = _open_directory_chain(canonical, parts[:-1])
    file_descriptor: int | None = None
    try:
        try:
            before = os.stat(
                parts[-1],
                dir_fd=descriptors[-1],
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError("E_ADOPTION_FILE: file is unavailable") from error
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size < 0
            or before.st_size > maximum
            or _dataless(before)
        ):
            raise ValueError("E_ADOPTION_FILE: file is not bounded private regular content")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            file_descriptor = os.open(
                parts[-1],
                flags,
                dir_fd=descriptors[-1],
            )
        except OSError as error:
            raise ValueError("E_ADOPTION_FILE: file cannot be opened safely") from error
        opened = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o022
            or opened.st_size < 0
            or opened.st_size > maximum
            or _dataless(opened)
            or metadata_identity(before) != metadata_identity(opened)
        ):
            raise ValueError("E_ADOPTION_FILE: file identity changed before read")
        chunks: list[bytes] = []
        observed = 0
        while True:
            allowance = maximum + 1 - observed
            chunk = os.read(file_descriptor, min(READ_CHUNK, max(1, allowance)))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > maximum:
                raise ValueError("E_ADOPTION_FILE: file exceeds its byte limit")
        after_open = os.fstat(file_descriptor)
        after_path = os.stat(
            parts[-1],
            dir_fd=descriptors[-1],
            follow_symlinks=False,
        )
        if (
            metadata_identity(before) != metadata_identity(after_open)
            or metadata_identity(before) != metadata_identity(after_path)
        ):
            raise ValueError("E_ADOPTION_FILE: file changed during read")
        return b"".join(chunks)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("E_ADOPTION_FILE: safe file observation failed") from error
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
