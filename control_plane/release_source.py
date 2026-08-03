"""Bounded, deterministic integrity helpers for extracted release sources."""

from __future__ import annotations

import base64
import binascii
from hashlib import sha1, sha256
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterable, Mapping
import unicodedata


RELEASE_SOURCE_MARKER = ".codex/release-source.json"
RELEASE_SOURCE_KIND = "control-plane-release-tree"
RELEASE_SOURCE_OBJECT_FORMAT = "sha1"
RELEASE_SOURCE_MODES = frozenset({"100644", "100755"})
RELEASE_SOURCE_MAX_ENTRIES = 4096
RELEASE_SOURCE_MAX_TREE_NODES = 8192
RELEASE_SOURCE_MAX_PATH_BYTES = 1024
RELEASE_SOURCE_MAX_FILE_BYTES = 8 * 1024 * 1024
RELEASE_SOURCE_MAX_TOTAL_BYTES = 32 * 1024 * 1024
RELEASE_SOURCE_MAX_MARKER_BYTES = 2 * 1024 * 1024
RELEASE_SOURCE_MAX_COMMIT_BYTES = 1024 * 1024
RELEASE_SOURCE_HASH_CHUNK_BYTES = 1024 * 1024


class ReleaseSourceError(ValueError):
    """A release source cannot be proven safe and internally consistent."""


def _collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def canonical_release_path(path: str) -> str:
    """Validate one portable Git/archive path and return it unchanged."""

    if not isinstance(path, str) or not path:
        raise ReleaseSourceError("release path is empty")
    try:
        encoded = path.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ReleaseSourceError("release path is not UTF-8") from error
    pure = PurePosixPath(path)
    if (
        len(encoded) > RELEASE_SOURCE_MAX_PATH_BYTES
        or pure.is_absolute()
        or path != pure.as_posix()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in path
        or "\x00" in path
    ):
        raise ReleaseSourceError("release path is not canonical")
    return path


def validate_release_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Reject reserved, non-portable, duplicate and file/directory collisions."""

    canonical: list[str] = []
    file_keys: set[str] = set()
    directory_keys: set[str] = set()
    directory_spellings: dict[str, str] = {}
    marker_key = _collision_key(RELEASE_SOURCE_MARKER)
    marker_parts = PurePosixPath(RELEASE_SOURCE_MARKER).parts
    marker_directory_keys = {
        _collision_key(PurePosixPath(*marker_parts[:index]).as_posix())
        for index in range(1, len(marker_parts))
    }
    for raw_path in paths:
        path = canonical_release_path(raw_path)
        key = _collision_key(path)
        if (
            key == marker_key
            or key.startswith(marker_key + "/")
            or key in marker_directory_keys
        ):
            raise ReleaseSourceError("release path collides with marker namespace")
        if key in file_keys:
            raise ReleaseSourceError("release paths collide after normalization")
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            directory = PurePosixPath(*parts[:index]).as_posix()
            directory_key = _collision_key(directory)
            previous = directory_spellings.setdefault(directory_key, directory)
            if previous != directory:
                raise ReleaseSourceError(
                    "release directories collide after normalization"
                )
            directory_keys.add(directory_key)
        file_keys.add(key)
        canonical.append(path)
        if len(canonical) > RELEASE_SOURCE_MAX_ENTRIES:
            raise ReleaseSourceError("release source has too many entries")
    if file_keys & directory_keys:
        raise ReleaseSourceError("release file and directory paths collide")
    if (
        len(file_keys)
        + len(directory_keys | marker_directory_keys)
        + 1
        > RELEASE_SOURCE_MAX_TREE_NODES
    ):
        raise ReleaseSourceError("release source has too many filesystem nodes")
    return tuple(canonical)


def git_object_oid(kind: str, payload: bytes) -> str:
    """Return the SHA-1 object identity used by this GitHub release line."""

    if kind not in {"blob", "tree", "commit"}:
        raise ReleaseSourceError("unsupported Git object kind")
    header = f"{kind} {len(payload)}\0".encode("ascii")
    return sha1(header + payload, usedforsecurity=False).hexdigest()


def git_tree_oid(entries: Iterable[Mapping[str, Any]]) -> str:
    """Reconstruct the exact Git tree OID represented by release file entries."""

    values = list(entries)
    paths = validate_release_paths(str(entry.get("path", "")) for entry in values)
    root: dict[str, Any] = {}
    for path, entry in zip(paths, values, strict=True):
        mode = entry.get("mode")
        object_id = entry.get("git_oid")
        if (
            mode not in RELEASE_SOURCE_MODES
            or not isinstance(object_id, str)
            or len(object_id) != 40
            or any(character not in "0123456789abcdef" for character in object_id)
        ):
            raise ReleaseSourceError("release entry lacks a valid Git blob identity")
        parts = PurePosixPath(path).parts
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ReleaseSourceError("release path has a tree collision")
            node = child
        leaf = parts[-1]
        if leaf in node:
            raise ReleaseSourceError("release path is duplicated")
        node[leaf] = (mode, object_id)

    def tree_identity(node: Mapping[str, Any]) -> str:
        records: list[tuple[bytes, bytes]] = []
        for name, child in node.items():
            encoded_name = name.encode("utf-8", errors="strict")
            if isinstance(child, dict):
                object_id = tree_identity(child)
                mode = "40000"
                sort_key = encoded_name + b"/"
            else:
                mode, object_id = child
                sort_key = encoded_name + b"\0"
            record = (
                mode.encode("ascii")
                + b" "
                + encoded_name
                + b"\0"
                + bytes.fromhex(object_id)
            )
            records.append((sort_key, record))
        payload = b"".join(record for _, record in sorted(records))
        return git_object_oid("tree", payload)

    return tree_identity(root)


def commit_object_identity(payload: bytes) -> tuple[str, str]:
    """Return and validate a Git commit object's OID and referenced tree."""

    if not payload or len(payload) > RELEASE_SOURCE_MAX_COMMIT_BYTES:
        raise ReleaseSourceError("release commit object exceeds its bound")
    first_line = payload.split(b"\n", 1)[0]
    if not first_line.startswith(b"tree "):
        raise ReleaseSourceError("release commit object has no leading tree")
    raw_tree = first_line.removeprefix(b"tree ")
    try:
        tree = raw_tree.decode("ascii")
    except UnicodeError as error:
        raise ReleaseSourceError("release commit tree is not ASCII") from error
    if (
        len(tree) != 40
        or any(character not in "0123456789abcdef" for character in tree)
    ):
        raise ReleaseSourceError("release commit tree is invalid")
    return git_object_oid("commit", payload), tree


def encode_commit_object(payload: bytes) -> str:
    if len(payload) > RELEASE_SOURCE_MAX_COMMIT_BYTES:
        raise ReleaseSourceError("release commit object exceeds its bound")
    return base64.b64encode(payload).decode("ascii")


def decode_commit_object(value: object) -> bytes:
    if not isinstance(value, str):
        raise ReleaseSourceError("release commit object is not encoded text")
    if len(value) > ((RELEASE_SOURCE_MAX_COMMIT_BYTES + 2) // 3) * 4:
        raise ReleaseSourceError("release commit object exceeds its bound")
    try:
        payload = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ReleaseSourceError("release commit object is not valid base64") from error
    if len(payload) > RELEASE_SOURCE_MAX_COMMIT_BYTES:
        raise ReleaseSourceError("release commit object exceeds its bound")
    return payload


def read_bounded_regular_file(path: Path, *, limit: int) -> tuple[bytes, os.stat_result]:
    """Read one regular file without following its final symlink and with a cap."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseSourceError("release file cannot be opened safely") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 0 or before.st_size > limit:
            raise ReleaseSourceError("release file exceeds its bound or is not regular")
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(RELEASE_SOURCE_HASH_CHUNK_BYTES, limit + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > limit:
                raise ReleaseSourceError("release file exceeds its bound")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or observed != after.st_size:
            raise ReleaseSourceError("release file changed while it was read")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def hash_bounded_regular_file(path: Path) -> dict[str, object]:
    """Stream both release SHA-256 and Git blob identity under the shared cap."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseSourceError("release file cannot be opened safely") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size < 0
            or before.st_size > RELEASE_SOURCE_MAX_FILE_BYTES
        ):
            raise ReleaseSourceError("release file exceeds its bound or is not regular")
        sha256_hasher = sha256()
        sha1_hasher = sha1(usedforsecurity=False)
        sha1_hasher.update(f"blob {before.st_size}\0".encode("ascii"))
        observed = 0
        while True:
            chunk = os.read(descriptor, RELEASE_SOURCE_HASH_CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > RELEASE_SOURCE_MAX_FILE_BYTES:
                raise ReleaseSourceError("release file exceeds its bound")
            sha256_hasher.update(chunk)
            sha1_hasher.update(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or observed != after.st_size:
            raise ReleaseSourceError("release file changed while it was hashed")
        return {
            "git_oid": sha1_hasher.hexdigest(),
            "mode": "100755" if before.st_mode & 0o111 else "100644",
            "sha256": f"sha256:{sha256_hasher.hexdigest()}",
            "size_bytes": observed,
        }
    finally:
        os.close(descriptor)
