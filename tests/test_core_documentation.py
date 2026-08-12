from __future__ import annotations

from hashlib import sha256
import inspect
import json
import os
import errno
from pathlib import Path
import re
import selectors
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_INDEX = ROOT / "docs" / "engineering" / "00-canonical-index.md"
ADR = ROOT / "docs" / "adr" / "0006-control-plane-core-and-quarantine.md"
MAINTENANCE = ROOT / "docs" / "engineering" / "19-control-plane-core-maintenance.md"
DOGFOOD = ROOT / "docs" / "engineering" / "20-control-plane-core-dogfood.md"
THREAT_PATH = Path("docs/security/2026-08-12-control-plane-core-threat-model.md")
THREAT_MODEL = ROOT / THREAT_PATH
REPOSITORY_ID = "sha256:31d48f56964b98247664973b33d474c0f79ce6e9ac191996c9c6ad4307fe8959"
BASE_REVISION = "929d3f8a0656fed190bb65ceb3a29deef8de07d6"
_STREAM_CHUNK = 65_536
_MAX_TRACKED_LIST_BYTES = 1_048_576
_MAX_TRACKED_ENTRIES = 4_096
_MAX_UNTRACKED_LIST_BYTES = 256 * 1_024
_MAX_UNTRACKED_ENTRIES = 256
_MAX_SNAPSHOT_PATH_BYTES = 4_096
_MAX_SNAPSHOT_FILE_BYTES = 2 * 1_048_576
_MAX_SNAPSHOT_TOTAL_BYTES = 8 * 1_048_576
_MAX_DOCUMENT_BYTES = 1_048_576
_FILE_PROVIDER_DATALESS = 0x40000000
_GIT_TIMEOUT_SECONDS = 5.0
_PROCESS_CLEANUP_SECONDS = 0.25
_GIT_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/var/empty",
    "XDG_CONFIG_HOME": "/var/empty",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_TERMINAL_PROMPT": "0",
}
_GIT_PREFIX = (
    "/usr/bin/git",
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
)
_GIT_REVISION_ARGUMENTS = ("rev-parse", "--verify", "HEAD")
_GIT_ANCHOR_ARGUMENTS = (
    "ls-tree",
    "-r",
    "-z",
    "--full-tree",
    BASE_REVISION,
)
_GIT_INDEX_ARGUMENTS = ("ls-files", "--stage", "-z")
_GIT_UNTRACKED_ARGUMENTS = (
    "ls-files",
    "-z",
    "--others",
    "--exclude-standard",
)
_ALLOWED_GIT_ARGUMENTS = {
    _GIT_REVISION_ARGUMENTS,
    _GIT_ANCHOR_ARGUMENTS,
    _GIT_INDEX_ARGUMENTS,
    _GIT_UNTRACKED_ARGUMENTS,
}
_SENSITIVE_PATH = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|\.npmrc|\.pypirc)(?:$|/)|"
    r"(?:^|/)[^/]*(?:credentials?|service[-_]?account)[^/]*(?:$|/)|"
    r"\.(?:pem|key|p12|crt|cert)$",
    re.IGNORECASE,
)

HISTORICAL_DOCUMENTS = (
    "docs/adr/0001-router-hibrido-y-resolver-puro.md",
    "docs/adr/0002-distribucion-hooks-leases-y-enforcement.md",
    "docs/adr/0003-local-audit-kernel-v2-1.md",
    "docs/adr/0004-skill-led-local-run-loop.md",
    "docs/adr/0005-host-bound-outcome-authorization.md",
    "docs/engineering/01-operating-model.md",
    "docs/engineering/02-git-pr-merge.md",
    "docs/engineering/05-release-and-observation.md",
    "docs/engineering/06-recovery.md",
    "docs/engineering/07-adoption.md",
    "docs/engineering/11-lifecycle-hooks-adoption.md",
    "docs/engineering/13-clarification-and-risk-local-audit.md",
    "docs/engineering/14-bustafit-dogfood-pilot.md",
    "docs/engineering/16-outcome-bridge-rollback.md",
    "docs/engineering/17-v2-3-native-sandbox-promotion.md",
    "docs/engineering/18-native-governor-plugin.md",
    "docs/security/2026-08-08-v2-3-outcome-bridge-threat-model.md",
    "docs/superpowers/plans/2026-07-28-codex-engineering-control-plane-v1.md",
    "docs/superpowers/plans/2026-07-29-clarification-gate-risk-sentinel-v2-1.md",
    "docs/superpowers/plans/2026-07-31-control-plane-v2-1-local-audit-consolidation.md",
    "docs/superpowers/plans/2026-08-01-control-plane-v2-1-pilot-fixes.md",
    "docs/superpowers/plans/2026-08-02-bustafit-dogfood-pilot.md",
    "docs/superpowers/plans/2026-08-03-continuation-pointer-v1.md",
    "docs/superpowers/plans/2026-08-03-cross-thread-host-lookup-v1.md",
    "docs/superpowers/plans/2026-08-03-release-v2-1-prep.md",
    "docs/superpowers/plans/2026-08-03-supported-adoption-v2-1.md",
    "docs/superpowers/plans/2026-08-04-control-plane-v2-1-1-alignment.md",
    "docs/superpowers/plans/2026-08-08-control-plane-v2-3-outcome-bridge.md",
    "docs/superpowers/plans/2026-08-08-personal-control-plane-v3.md",
    "docs/superpowers/specs/2026-08-08-control-plane-v2-3-outcome-bridge-design.md",
    "docs/superpowers/plans/2026-08-10-control-plane-v2-4-native-governor.md",
    "docs/superpowers/plans/2026-08-11-control-plane-taskplaybook-v0-progressive-disclosure.md",
    "docs/superpowers/specs/2026-07-28-codex-engineering-control-plane-design.md",
    "docs/superpowers/specs/2026-07-29-clarification-gate-risk-sentinel-design.md",
    "docs/superpowers/specs/2026-08-10-control-plane-taskplaybook-v0-design.md",
    "docs/superpowers/specs/2026-08-10-control-plane-v2-4-native-governor-design.md",
)

RELEASE_EVIDENCE_DOCUMENTS = (
    "docs/releases/v2.1.0.md",
    "docs/releases/v2.1.1.md",
)

GOVERNING_DOCUMENTS = (
    "README.md",
    "AGENTS.md",
    "SECURITY.md",
    "docs/adr/0006-control-plane-core-and-quarantine.md",
    "docs/engineering/00-canonical-index.md",
    "docs/engineering/03-reasoning-context-agents.md",
    "docs/engineering/04-documentation-policy.md",
    "docs/engineering/08-global-codex-configuration.md",
    "docs/engineering/09-audit-dafo-and-risk-register.md",
    "docs/engineering/10-resource-routing.md",
    "docs/engineering/12-multidominio-y-modos.md",
    "docs/engineering/19-control-plane-core-maintenance.md",
    "docs/engineering/20-control-plane-core-dogfood.md",
    "docs/security/2026-08-12-control-plane-core-threat-model.md",
    "docs/superpowers/plans/2026-08-12-control-plane-core-3-1.md",
)

ADVANCED_OPERATION_PATTERN = re.compile(
    r"control-plane\s+(?:run\s+(?:prepare|verify|status|block)|"
    r"adopt\s+(?:plan|apply)|upgrade\s+(?:plan|apply))|"
    r"\bPR LISTA\b|\bTrustedAuthorization\b|\bOutcomeAuthorization(?:Context)?\b|"
    r"\bOutcomeBindingV1\b|\bRemoteOutcomeReceiptV1\b|"
    r"\bLocalCandidateReceiptV1\b|\bDeliveryLease\b|\bdelivery lease\b|"
    r"\bverification-run\b|control_plane\."
    r"(?:adoption|candidate_receipt|host_bridge|lifecycle|release_source|run_workflow)",
    re.IGNORECASE,
)
ADVANCED_MARKER_CORE_ALLOWLIST = {
    "docs/engineering/19-control-plane-core-maintenance.md",
    "docs/superpowers/plans/2026-08-12-control-plane-core-3-1.md",
}


class SnapshotError(RuntimeError):
    """Stable path-free failure for the local security snapshot."""


def _metadata_is_dataless(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_flags", 0) & _FILE_PROVIDER_DATALESS)


def _metadata_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(metadata.st_uid),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        int(getattr(metadata, "st_flags", 0)),
    )


def _validate_regular_metadata(
    metadata: os.stat_result,
    *,
    maximum: int,
    remaining_total: int | None,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SnapshotError("E_SNAPSHOT_FILE_TYPE")
    if _metadata_is_dataless(metadata):
        raise SnapshotError("E_SNAPSHOT_FILE_DATALESS")
    if metadata.st_nlink != 1:
        raise SnapshotError("E_SNAPSHOT_FILE_LINKS")
    if metadata.st_uid != os.geteuid():
        raise SnapshotError("E_SNAPSHOT_FILE_OWNER")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise SnapshotError("E_SNAPSHOT_FILE_MODE")
    if metadata.st_size < 0 or metadata.st_size > maximum:
        raise SnapshotError("E_SNAPSHOT_FILE_SIZE")
    if remaining_total is not None and metadata.st_size > remaining_total:
        raise SnapshotError("E_SNAPSHOT_UNTRACKED_TOTAL")


def _confined_parts(relative: Path) -> tuple[str, ...]:
    encoded = os.fsencode(relative.as_posix())
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or b"\\" in encoded
        or b"\0" in encoded
        or len(encoded) > _MAX_SNAPSHOT_PATH_BYTES
    ):
        raise SnapshotError("E_SNAPSHOT_PATH")
    return tuple(relative.parts)


def _safe_regular_bytes(
    root: Path,
    relative: Path,
    *,
    maximum: int,
    remaining_total: int | None = None,
    reject_sensitive: bool = False,
) -> tuple[bytes, os.stat_result]:
    encoded_relative = os.fsencode(relative.as_posix())
    if reject_sensitive and _SENSITIVE_PATH.search(os.fsdecode(encoded_relative)):
        raise SnapshotError("E_SNAPSHOT_SENSITIVE_PATH")
    parts = _confined_parts(relative)
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_descriptors: list[int] = []
    try:
        root_descriptor = os.open(root, directory_flags)
        directory_descriptors.append(root_descriptor)
        if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
            raise SnapshotError("E_SNAPSHOT_PATH")
        for component in parts[:-1]:
            descriptor = os.open(
                component,
                directory_flags,
                dir_fd=directory_descriptors[-1],
            )
            directory_descriptors.append(descriptor)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise SnapshotError("E_SNAPSHOT_PATH")
    except SnapshotError:
        for directory_descriptor in reversed(directory_descriptors):
            os.close(directory_descriptor)
        raise
    except OSError as error:
        for directory_descriptor in reversed(directory_descriptors):
            os.close(directory_descriptor)
        if error.errno == errno.ENOENT:
            raise SnapshotError("E_SNAPSHOT_FILE_MISSING") from error
        raise SnapshotError("E_SNAPSHOT_PATH") from error
    try:
        parent_descriptor = directory_descriptors[-1]
        try:
            initial = os.stat(
                parts[-1],
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise SnapshotError("E_SNAPSHOT_FILE_MISSING") from error
            raise SnapshotError("E_SNAPSHOT_PATH") from error
        _validate_regular_metadata(
            initial,
            maximum=maximum,
            remaining_total=remaining_total,
        )
        file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(
                parts[-1],
                file_flags,
                dir_fd=parent_descriptor,
            )
        except OSError as error:
            raise SnapshotError("E_SNAPSHOT_FILE_UNAVAILABLE") from error
        try:
            before = os.fstat(file_descriptor)
            _validate_regular_metadata(
                before,
                maximum=maximum,
                remaining_total=remaining_total,
            )
            if _metadata_fingerprint(before) != _metadata_fingerprint(initial):
                raise SnapshotError("E_SNAPSHOT_FILE_DRIFT")
            payload = bytearray()
            expected = int(before.st_size)
            while True:
                allowance = expected - len(payload)
                size = min(_STREAM_CHUNK, max(1, allowance + 1))
                try:
                    chunk = os.read(file_descriptor, size)
                except OSError as error:
                    raise SnapshotError("E_SNAPSHOT_FILE_UNAVAILABLE") from error
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > expected or len(payload) > maximum:
                    raise SnapshotError("E_SNAPSHOT_FILE_DRIFT")
            after = os.fstat(file_descriptor)
            try:
                final = os.stat(
                    parts[-1],
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise SnapshotError("E_SNAPSHOT_FILE_DRIFT") from error
            fingerprint = _metadata_fingerprint(initial)
            if (
                len(payload) != expected
                or _metadata_fingerprint(before) != fingerprint
                or _metadata_fingerprint(after) != fingerprint
                or _metadata_fingerprint(final) != fingerprint
            ):
                raise SnapshotError("E_SNAPSHOT_FILE_DRIFT")
            return bytes(payload), initial
        finally:
            os.close(file_descriptor)
    finally:
        for directory_descriptor in reversed(directory_descriptors):
            os.close(directory_descriptor)


def read(path: Path) -> str:
    try:
        relative = path.relative_to(ROOT)
    except ValueError as error:
        raise SnapshotError("E_SNAPSHOT_PATH") from error
    payload, _ = _safe_regular_bytes(ROOT, relative, maximum=_MAX_DOCUMENT_BYTES)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SnapshotError("E_SNAPSHOT_TEXT") from error


def index_statuses() -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in read(CANONICAL_INDEX).splitlines():
        match = re.match(r"^\| `([^`]+)` \| `([^`]+)` \|", line)
        if match:
            path, status = match.groups()
            if path in statuses:
                raise AssertionError(f"duplicate canonical index path: {path}")
            statuses[path] = status
    return statuses


class _BoundedAccumulator:
    def __init__(self, maximum: int, *, digest_only: bool) -> None:
        if maximum < 1:
            raise SnapshotError("E_SNAPSHOT_GIT_OUTPUT_LIMIT")
        self.maximum = maximum
        self.digest_only = digest_only
        self.total = 0
        self.payload = bytearray()
        self.hasher = sha256()

    def push(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if self.total > self.maximum:
            raise SnapshotError("E_SNAPSHOT_GIT_OUTPUT_LIMIT")
        if self.digest_only:
            self.hasher.update(chunk)
        else:
            self.payload.extend(chunk)

    def finish(self) -> bytes | str:
        return self.hasher.hexdigest() if self.digest_only else bytes(self.payload)


def _cleanup_process(process: object) -> None:
    try:
        if process.poll() is not None:  # type: ignore[attr-defined]
            return
        process.terminate()  # type: ignore[attr-defined]
        try:
            process.wait(timeout=_PROCESS_CLEANUP_SECONDS)  # type: ignore[attr-defined]
            return
        except subprocess.TimeoutExpired:
            process.kill()  # type: ignore[attr-defined]
            try:
                process.wait(timeout=_PROCESS_CLEANUP_SECONDS)  # type: ignore[attr-defined]
                return
            except subprocess.TimeoutExpired as error:
                raise SnapshotError("E_SNAPSHOT_PROCESS_CLEANUP") from error
    except SnapshotError:
        raise
    except (OSError, AttributeError) as error:
        raise SnapshotError("E_SNAPSHOT_PROCESS_CLEANUP") from error


def _run_git(
    arguments: tuple[str, ...],
    *,
    maximum: int,
    digest_only: bool,
) -> bytes | str:
    if arguments not in _ALLOWED_GIT_ARGUMENTS:
        raise SnapshotError("E_SNAPSHOT_GIT_COMMAND")
    try:
        process = subprocess.Popen(
            [*_GIT_PREFIX, *arguments],
            cwd=ROOT,
            env=dict(_GIT_ENVIRONMENT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
    except (OSError, ValueError) as error:
        raise SnapshotError("E_SNAPSHOT_GIT_SPAWN") from error
    selector = selectors.DefaultSelector()
    accumulator = _BoundedAccumulator(maximum, digest_only=digest_only)
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    try:
        if process.stdout is None:
            raise SnapshotError("E_SNAPSHOT_GIT_STREAM")
        selector.register(process.stdout, selectors.EVENT_READ)
        open_stream = True
        while open_stream:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SnapshotError("E_SNAPSHOT_GIT_TIMEOUT")
            events = selector.select(timeout=min(remaining, 0.1))
            if not events:
                continue
            for key, _ in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), _STREAM_CHUNK)
                except OSError as error:
                    raise SnapshotError("E_SNAPSHOT_GIT_STREAM") from error
                if chunk:
                    accumulator.push(chunk)
                else:
                    selector.unregister(key.fileobj)
                    open_stream = False
        remaining = max(0.001, deadline - time.monotonic())
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise SnapshotError("E_SNAPSHOT_GIT_TIMEOUT") from error
        if return_code != 0:
            raise SnapshotError("E_SNAPSHOT_GIT_FAILED")
        return accumulator.finish()
    except SnapshotError as error:
        try:
            _cleanup_process(process)
        except SnapshotError as cleanup_error:
            raise cleanup_error from error
        raise
    finally:
        selector.close()
        if process.stdout is not None:
            process.stdout.close()


def git_bytes(*arguments: str) -> bytes:
    closed = tuple(arguments)
    maximum = {
        _GIT_REVISION_ARGUMENTS: 256,
        _GIT_ANCHOR_ARGUMENTS: _MAX_TRACKED_LIST_BYTES,
        _GIT_INDEX_ARGUMENTS: _MAX_TRACKED_LIST_BYTES,
        _GIT_UNTRACKED_ARGUMENTS: _MAX_UNTRACKED_LIST_BYTES,
    }.get(closed)
    if maximum is None:
        raise SnapshotError("E_SNAPSHOT_GIT_COMMAND")
    result = _run_git(closed, maximum=maximum, digest_only=False)
    if not isinstance(result, bytes):
        raise SnapshotError("E_SNAPSHOT_GIT_STREAM")
    return result


def _git_revision() -> str:
    try:
        revision = git_bytes(*_GIT_REVISION_ARGUMENTS).decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise SnapshotError("E_SNAPSHOT_GIT_REVISION") from error
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SnapshotError("E_SNAPSHOT_GIT_REVISION")
    return revision


def _parse_anchor_tree(raw: bytes) -> tuple[dict[str, str], str]:
    if len(raw) > _MAX_TRACKED_LIST_BYTES:
        raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_LIMIT")
    if raw and not raw.endswith(b"\0"):
        raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_FORMAT")
    entries = [entry for entry in raw.split(b"\0") if entry]
    if len(entries) > _MAX_TRACKED_ENTRIES:
        raise SnapshotError("E_SNAPSHOT_TRACKED_COUNT")
    paths: dict[str, str] = {}
    hasher = sha256()
    for entry in entries:
        metadata, separator, path_raw = entry.partition(b"\t")
        match = re.fullmatch(rb"(100644|100755) blob ([0-9a-f]{40})", metadata)
        if not separator or not path_raw or match is None:
            raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_FORMAT")
        if len(path_raw) > _MAX_SNAPSHOT_PATH_BYTES:
            raise SnapshotError("E_SNAPSHOT_TRACKED_PATH")
        relative = Path(os.fsdecode(path_raw))
        _confined_parts(relative)
        key = relative.as_posix()
        if key in paths:
            raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_FORMAT")
        paths[key] = match.group(1).decode("ascii")
        if relative != THREAT_PATH:
            hasher.update(entry)
            hasher.update(b"\0")
    return paths, hasher.hexdigest()


def _parse_index_paths(raw: bytes) -> dict[str, str]:
    if len(raw) > _MAX_TRACKED_LIST_BYTES:
        raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_LIMIT")
    if raw and not raw.endswith(b"\0"):
        raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_FORMAT")
    entries = [entry for entry in raw.split(b"\0") if entry]
    if len(entries) > _MAX_TRACKED_ENTRIES:
        raise SnapshotError("E_SNAPSHOT_TRACKED_COUNT")
    paths: dict[str, str] = {}
    for entry in entries:
        metadata, separator, path_raw = entry.partition(b"\t")
        match = re.fullmatch(
            rb"(100644|100755) [0-9a-f]{40} 0",
            metadata,
        )
        if not separator or not path_raw or match is None:
            raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_FORMAT")
        if len(path_raw) > _MAX_SNAPSHOT_PATH_BYTES:
            raise SnapshotError("E_SNAPSHOT_TRACKED_PATH")
        relative = Path(os.fsdecode(path_raw))
        _confined_parts(relative)
        key = relative.as_posix()
        if key in paths:
            raise SnapshotError("E_SNAPSHOT_TRACKED_LIST_FORMAT")
        paths[key] = match.group(1).decode("ascii")
    return paths


def _tracked_inventory() -> tuple[dict[str, str], dict[str, str], str]:
    anchor_raw = git_bytes(*_GIT_ANCHOR_ARGUMENTS)
    index_raw = git_bytes(*_GIT_INDEX_ARGUMENTS)
    anchor, anchor_digest = _parse_anchor_tree(anchor_raw)
    index = _parse_index_paths(index_raw)
    return anchor, index, anchor_digest


def _parse_untracked_list(raw: bytes) -> tuple[Path, ...]:
    if len(raw) > _MAX_UNTRACKED_LIST_BYTES:
        raise SnapshotError("E_SNAPSHOT_UNTRACKED_LIST_LIMIT")
    if raw and not raw.endswith(b"\0"):
        raise SnapshotError("E_SNAPSHOT_UNTRACKED_LIST_FORMAT")
    encoded_paths = [item for item in raw.split(b"\0") if item]
    if len(encoded_paths) > _MAX_UNTRACKED_ENTRIES:
        raise SnapshotError("E_SNAPSHOT_UNTRACKED_COUNT")
    if any(len(item) > _MAX_SNAPSHOT_PATH_BYTES for item in encoded_paths):
        raise SnapshotError("E_SNAPSHOT_UNTRACKED_PATH")
    paths = tuple(Path(os.fsdecode(item)) for item in sorted(encoded_paths))
    if len({path.as_posix() for path in paths}) != len(paths):
        raise SnapshotError("E_SNAPSHOT_UNTRACKED_LIST_FORMAT")
    return paths


def _safe_untracked_record(
    root: Path,
    relative: Path,
    *,
    remaining_total: int,
) -> tuple[dict[str, object], int]:
    payload, metadata = _safe_regular_bytes(
        root,
        relative,
        maximum=_MAX_SNAPSHOT_FILE_BYTES,
        remaining_total=remaining_total,
        reject_sensitive=True,
    )
    return (
        {
            "path": relative.as_posix(),
            "state": "present",
            "mode": "100755" if metadata.st_mode & stat.S_IXUSR else "100644",
            "sha256": sha256(payload).hexdigest(),
        },
        len(payload),
    )


def normalized_snapshot_version() -> str:
    """Bind the canonical final overlay relative to one immutable anchor."""

    threat_payload, _ = _safe_regular_bytes(
        ROOT,
        THREAT_PATH,
        maximum=_MAX_DOCUMENT_BYTES,
    )
    try:
        threat_model = threat_payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SnapshotError("E_SNAPSHOT_TEXT") from error
    lines = threat_model.splitlines()
    if (
        len(lines) < 3
        or not lines[-2].startswith("Repository: sha256:")
        or not lines[-1].startswith("Version: codex-security-snapshot/v1:sha256:")
    ):
        raise SnapshotError("E_SNAPSHOT_THREAT_FOOTER")
    body = ("\n".join(lines[:-2]) + "\n").encode("utf-8")
    anchor, index, anchor_tree_digest = _tracked_inventory()
    untracked_raw = git_bytes(*_GIT_UNTRACKED_ARGUMENTS)
    untracked_paths = _parse_untracked_list(untracked_raw)
    untracked_keys = {relative.as_posix() for relative in untracked_paths}
    tracked_keys = set(anchor) | set(index)
    if tracked_keys & untracked_keys:
        raise SnapshotError("E_SNAPSHOT_OVERLAY_DUPLICATE")
    if len(tracked_keys) + len(untracked_keys) > _MAX_TRACKED_ENTRIES + _MAX_UNTRACKED_ENTRIES:
        raise SnapshotError("E_SNAPSHOT_OVERLAY_COUNT")
    overlay: dict[str, dict[str, object]] = {}
    remaining_total = _MAX_SNAPSHOT_TOTAL_BYTES
    for key in sorted(tracked_keys):
        relative = Path(key)
        if relative == THREAT_PATH:
            continue
        if _SENSITIVE_PATH.search(key):
            raise SnapshotError("E_SNAPSHOT_SENSITIVE_PATH")
        try:
            record, consumed = _safe_untracked_record(
                ROOT,
                relative,
                remaining_total=remaining_total,
            )
        except SnapshotError as error:
            if str(error) != "E_SNAPSHOT_FILE_MISSING":
                raise
            if key in anchor:
                overlay[key] = {"path": key, "state": "absent"}
            continue
        overlay[key] = record
        remaining_total -= consumed
    for relative in untracked_paths:
        if relative == THREAT_PATH:
            continue
        key = relative.as_posix()
        record, consumed = _safe_untracked_record(
            ROOT,
            relative,
            remaining_total=remaining_total,
        )
        overlay[key] = record
        remaining_total -= consumed
    manifest = {
        "schema": "codex-security-snapshot/v1",
        "target_kind": "git_worktree",
        "base_revision": BASE_REVISION,
        "base_tree_sha256": anchor_tree_digest,
        "overlay": [overlay[path] for path in sorted(overlay)],
        "normalized_outputs": [
            {
                "path": THREAT_PATH.as_posix(),
                "normalization": "exclude_cache_footer_only",
                "sha256": sha256(body).hexdigest(),
            }
        ],
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return "codex-security-snapshot/v1:sha256:" + sha256(encoded).hexdigest()


class CoreDocumentationTests(unittest.TestCase):
    def test_required_core_documents_and_headings_exist(self) -> None:
        contracts = {
            ADR: (
                "# ADR 0006: Control Plane Core and structural quarantine",
                "## Context",
                "## Decision",
                "## Alternatives",
                "## Consequences",
                "## Compatibility and rollback",
            ),
            MAINTENANCE: (
                "# Control Plane Core maintenance",
                "## Runtime boundary",
                "## Legacy recovery",
                "## Verification mutex",
                "## Maintenance circuit breaker",
                "## Compatibility window",
                "## Rollback",
                "## External adoption",
            ),
            DOGFOOD: (
                "# Control Plane Core manual dogfood",
                "## Entry gate",
                "## Scorecard",
                "## Exit gate",
                "## Continuación",
            ),
            THREAT_MODEL: (
                "# Control Plane Core threat model",
                "## Overview",
                "## Threat Model, Trust Boundaries, and Assumptions",
                "## Attack Surface, Mitigations, and Attacker Stories",
                "## Severity Calibration",
            ),
            CANONICAL_INDEX: (
                "# Canonical documentation index",
                "## Version truth",
                "## Governing Core documents",
                "## Historical non-governing documents",
            ),
        }
        for path, headings in contracts.items():
            self.assertTrue(path.is_file(), path)
            content = read(path)
            for heading in headings:
                self.assertIn(heading, content, f"{path}: {heading}")

    def test_canonical_index_owns_version_truth_and_advanced_history(self) -> None:
        index = read(CANONICAL_INDEX)
        for truth in (
            "2.1.1 — last official release",
            "3.0.0 — unpublished plugin candidate; not a product release",
            "3.1.0-core.1 — local prerelease candidate",
            "GREEN_LOCAL / PENDING_STABLE_ADOPTION",
        ):
            self.assertIn(truth, index)
        for path in HISTORICAL_DOCUMENTS:
            self.assertRegex(
                index,
                rf"(?m)^\| `{re.escape(path)}` \| `HISTORICAL_NON_GOVERNING` \|",
            )
        for path in GOVERNING_DOCUMENTS:
            self.assertRegex(
                index,
                rf"(?m)^\| `{re.escape(path)}` \| `GOVERNING_CORE` \|",
            )
        for path in RELEASE_EVIDENCE_DOCUMENTS:
            self.assertRegex(
                index,
                rf"(?m)^\| `{re.escape(path)}` \| `HISTORICAL_RELEASE_EVIDENCE_NON_GOVERNING` \|",
            )

        readme = read(ROOT / "README.md")
        agents = read(ROOT / "AGENTS.md")
        security = read(ROOT / "SECURITY.md")
        for link in (
            "docs/engineering/00-canonical-index.md",
            "docs/engineering/19-control-plane-core-maintenance.md",
            "docs/engineering/20-control-plane-core-dogfood.md",
            THREAT_PATH.as_posix(),
            "docs/adr/0006-control-plane-core-and-quarantine.md",
        ):
            self.assertIn(link, readme)
        for stale in (
            "scripts/control-plane run prepare",
            "scripts/control-plane adopt plan",
            "scripts/control-plane adopt apply",
            "scripts/control-plane upgrade plan",
            "`PR LISTA` es el default",
        ):
            self.assertNotIn(stale, readme)
        for stale in ("TrustedAuthorization", "planes shadow", "tareas shadow"):
            self.assertNotIn(stale, agents)
        self.assertIn("- Autoridad: `authorizes=false`.", agents)
        self.assertIn(THREAT_PATH.as_posix(), security)
        self.assertNotIn("`PR LISTA` es el default", security)
        self.assertNotIn("outcome bridge v2.3 permanece", security)

    def test_readme_recommends_only_governing_core_documents_and_local_preflight(self) -> None:
        readme = read(ROOT / "README.md")
        self.assertNotIn("--refresh", readme)
        self.assertNotIn("preflight --mode release", readme)
        _, marker, tail = readme.partition("## Dónde leer")
        self.assertEqual(marker, "## Dónde leer")
        section, _, _ = tail.partition("\n## ")
        links = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", section)
        self.assertGreaterEqual(len(links), 8)
        statuses = index_statuses()
        for link in links:
            self.assertEqual(statuses.get(link), "GOVERNING_CORE", link)

    def test_every_advanced_operational_document_is_explicitly_historical(self) -> None:
        statuses = index_statuses()
        historical = {
            "HISTORICAL_NON_GOVERNING",
            "HISTORICAL_RELEASE_EVIDENCE_NON_GOVERNING",
        }
        for path in HISTORICAL_DOCUMENTS:
            self.assertEqual(statuses.get(path), "HISTORICAL_NON_GOVERNING", path)
        for path in RELEASE_EVIDENCE_DOCUMENTS:
            self.assertEqual(
                statuses.get(path),
                "HISTORICAL_RELEASE_EVIDENCE_NON_GOVERNING",
                path,
            )
        for path in sorted((ROOT / "docs").rglob("*.md")):
            relative = path.relative_to(ROOT).as_posix()
            if relative in ADVANCED_MARKER_CORE_ALLOWLIST:
                self.assertEqual(statuses.get(relative), "GOVERNING_CORE", relative)
                continue
            if ADVANCED_OPERATION_PATTERN.search(read(path)):
                self.assertIn(statuses.get(relative), historical, relative)

    def test_registry_routes_stable_lifecycle_id_to_core_maintenance(self) -> None:
        registry = tomllib.loads(read(ROOT / ".codex" / "resource-registry.toml"))
        resources = [
            item
            for item in registry["resources"]
            if item["id"] == "document.lifecycle-adoption-guide"
        ]
        self.assertEqual(len(resources), 1)
        self.assertEqual(
            resources[0]["locator"],
            "repo://docs/engineering/19-control-plane-core-maintenance.md",
        )
        self.assertTrue(resources[0]["canonical"])
        self.assertFalse(
            any(
                item.get("locator")
                == "repo://docs/engineering/11-lifecycle-hooks-adoption.md"
                for item in registry["resources"]
            )
        )
        route = next(item for item in registry["routes"] if item["id"] == "structured-engineering")
        self.assertIn("document.lifecycle-adoption-guide", route["recommended_resources"])

    def test_maintenance_runbook_is_fail_closed_and_time_bounded(self) -> None:
        content = read(MAINTENANCE)
        for token in (
            "CoreTaskStateV1",
            "`answer`",
            "`local_change`",
            "origin=legacy",
            "resumable=false",
            "E_ACTIVE_LEGACY_STATE",
            "legacy_writer_exclusion=COOPERATIVE_ONLY",
            "`adopt status`",
            "`adopt verify`",
            "`adopt rollback`",
            "E_VERIFICATION_BUSY",
            "executed=false",
            "consumes_reframe=false",
            "MaintenanceLineageV1",
            "E_BOOTSTRAP_REFRAME_LIMIT",
            "E_CAPABILITY_QUARANTINED",
            "authorizes=false",
            "compatibility_window=3.1_line_only",
            "removal_boundary=first_3.2_prerelease",
            "self_certified=false",
            "external_consumer_adoption=PROHIBITED",
            "GREEN_LOCAL / PENDING_STABLE_ADOPTION",
            "origin/main@929d3f8a0656fed190bb65ceb3a29deef8de07d6",
        ):
            self.assertIn(token, content)
        for command in (
            "run prepare",
            "run verify",
            "run status",
            "run block",
            "report",
            "verification-run",
            "adopt plan",
            "adopt apply",
            "upgrade plan",
            "upgrade apply",
        ):
            self.assertRegex(
                content,
                rf"(?m)^\| `{re.escape(command)}` \| `E_CAPABILITY_QUARANTINED` \| `2` \|",
            )

    def test_manual_dogfood_scorecard_is_closed(self) -> None:
        content = read(DOGFOOD)
        rows = [
            line
            for line in content.splitlines()
            if re.match(r"^\| `CORE-DOGFOOD-[0-9]{2}` \|", line)
        ]
        self.assertEqual(len(rows), 10)
        parsed = [
            [cell.strip().strip("`") for cell in row.strip("|").split("|")]
            for row in rows
        ]
        self.assertEqual(
            [row[0] for row in parsed],
            [f"CORE-DOGFOOD-{index:02d}" for index in range(1, 11)],
        )
        self.assertGreaterEqual(sum(row[2] == "true" for row in parsed), 3)
        self.assertTrue({"local", "hybrid", "controlled"}.issubset({row[1] for row in parsed}))
        self.assertTrue(all(row[6] == "PENDING" and row[7] == "NONE" for row in parsed))
        for row in parsed:
            if row[2] == "true":
                self.assertEqual(row[3:6], ["answer", "local_read", "0"])
        for token in (
            "Autopilot=OFF",
            "PENDING_10_TASK_DOGFOOD",
            "tasks_completed=10",
            "facts_only_total>=3",
            "duplicated_effects=0",
            "fabricated_effects=0",
            "overlapping_writers=0",
            "nuisance_warnings<=1",
            "duplicated_full_suites=0",
            "No prompts, transcripts, or telemetry",
        ):
            self.assertIn(token, content)

    def test_continuation_is_compact_and_non_authorizing(self) -> None:
        content = read(DOGFOOD)
        _, separator, continuation = content.rpartition("## Continuación")
        self.assertEqual(separator, "## Continuación")
        self.assertLessEqual(len(continuation.encode("utf-8")), 2_048)
        for field in (
            "Escribe en:",
            "Rol:",
            "Para continuar:",
            "Mensaje exacto:",
            "Estado de partida:",
            "No hacer todavía:",
            "Autoridad:",
        ):
            self.assertIn(field, continuation)
        self.assertIn("authorizes=false", continuation)

    def test_threat_model_is_repository_scoped_and_snapshot_bound(self) -> None:
        content = read(THREAT_MODEL)
        for heading in (
            "## Overview",
            "## Threat Model, Trust Boundaries, and Assumptions",
            "## Attack Surface, Mitigations, and Attacker Stories",
            "## Severity Calibration",
            "### Critical",
            "### High",
            "### Medium",
            "### Low",
            "## Residual risks",
        ):
            self.assertIn(heading, content)
        for token in (
            "attacker-controlled",
            "operator-controlled",
            "developer-controlled",
            "exact runtime allowlist",
            "authorizes=false",
            "generational lease",
            "E_VERIFICATION_BUSY",
            "E_BOOTSTRAP_REFRAME_LIMIT",
            "origin=legacy",
            "external_consumer_adoption=PROHIBITED",
            "legacy_writer_exclusion=COOPERATIVE_ONLY",
            "snapshot_normalization=exclude_cache_footer_only",
        ):
            self.assertIn(token, content)
        lines = content.splitlines()
        self.assertGreaterEqual(len(lines), 2)
        self.assertEqual(lines[-2], f"Repository: {REPOSITORY_ID}")
        self.assertRegex(
            lines[-1],
            r"^Version: codex-security-snapshot/v1:sha256:[0-9a-f]{64}$",
        )
        self.assertEqual(lines[-1], f"Version: {normalized_snapshot_version()}")


class CoreSnapshotSafetyTests(unittest.TestCase):
    def assert_snapshot_error(self, code: str, function) -> None:
        with self.assertRaises(SnapshotError) as observed:
            function()
        self.assertEqual(str(observed.exception), code)
        self.assertRegex(str(observed.exception), r"^E_[A-Z_]+$")

    def test_snapshot_implementation_has_no_unbounded_or_following_io(self) -> None:
        sources = "\n".join(
            inspect.getsource(function)
            for function in (
                read,
                _safe_regular_bytes,
                git_bytes,
                _tracked_inventory,
                _run_git,
                normalized_snapshot_version,
            )
        )
        self.assertNotIn("capture_output", sources)
        self.assertNotIn("read_bytes", sources)
        self.assertNotIn("read_text", sources)
        self.assertIn("shell=False", sources)
        self.assertIn("stdin=subprocess.DEVNULL", sources)
        self.assertIn("env=", sources)
        self.assertIn("dir_fd=", sources)
        self.assertIn("follow_symlinks=False", sources)
        self.assertIn("O_NOFOLLOW", sources)
        self.assertLessEqual(_STREAM_CHUNK, 65_536)

    def test_snapshot_digest_does_not_observe_its_own_commit_id(self) -> None:
        with patch(
            f"{__name__}._git_revision",
            side_effect=AssertionError("snapshot consulted mutable HEAD"),
        ) as revision:
            normalized_snapshot_version()
        revision.assert_not_called()

    def test_same_final_content_is_stable_across_checkpoint_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*arguments: str) -> bytes:
                return subprocess.check_output(
                    ["/usr/bin/git", "-C", str(root), *arguments],
                    env=dict(_GIT_ENVIRONMENT),
                    stderr=subprocess.DEVNULL,
                )

            git("init", "-q")
            git("config", "user.name", "Core Snapshot Test")
            git("config", "user.email", "core-snapshot@example.invalid")
            (root / "base.txt").write_bytes(b"base\n")
            (root / "removed.txt").write_bytes(b"remove me\n")
            (root / "rename-me.txt").write_bytes(b"rename me\n")
            (root / "mode.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
            (root / ".gitignore").write_bytes(b"ignored.tmp\n")
            threat_model = root / THREAT_PATH
            threat_model.parent.mkdir(parents=True)
            threat_model.write_text(
                "# Test threat model\n\n"
                f"Repository: {REPOSITORY_ID}\n"
                "Version: codex-security-snapshot/v1:sha256:"
                + "0" * 64
                + "\n",
                encoding="utf-8",
            )
            git("add", ".")
            git("commit", "-qm", "anchor")

            anchor = git("rev-parse", "HEAD").decode("ascii").strip()
            anchor_arguments = (
                "ls-tree",
                "-r",
                "-z",
                "--full-tree",
                anchor,
            )
            allowed = {
                _GIT_REVISION_ARGUMENTS,
                anchor_arguments,
                _GIT_INDEX_ARGUMENTS,
                _GIT_UNTRACKED_ARGUMENTS,
            }
            with (
                patch(f"{__name__}.ROOT", root),
                patch(f"{__name__}.BASE_REVISION", anchor),
                patch(f"{__name__}._GIT_ANCHOR_ARGUMENTS", anchor_arguments),
                patch(f"{__name__}._ALLOWED_GIT_ARGUMENTS", allowed),
            ):
                baseline = normalized_snapshot_version()
                git("update-index", "--assume-unchanged", "base.txt")
                git("update-index", "--skip-worktree", "mode.sh")
                (root / "base.txt").write_bytes(b"changed\n")
                (root / "removed.txt").unlink()
                (root / "added.txt").write_bytes(b"added\n")
                (root / "rename-me.txt").rename(root / "renamed.txt")
                (root / "mode.sh").chmod(0o755)
                (root / "ignored.tmp").write_bytes(b"ignored one\n")
                dirty = normalized_snapshot_version()
                (root / "ignored.tmp").write_bytes(b"ignored two\n")
                self.assertEqual(normalized_snapshot_version(), dirty)
                self.assertNotEqual(baseline, dirty)
                git("update-index", "--no-assume-unchanged", "base.txt")
                git("update-index", "--no-skip-worktree", "mode.sh")
                git("add", ".")
                git("commit", "-qm", "checkpoint")
                clean = normalized_snapshot_version()
                threat_model.write_text(
                    "# Test threat model\n\n"
                    f"Repository: {REPOSITORY_ID}\n"
                    "Version: codex-security-snapshot/v1:sha256:"
                    + "1" * 64
                    + "\n",
                    encoding="utf-8",
                )
                git("add", THREAT_PATH.as_posix())
                git("commit", "--amend", "--no-edit", "-q")
                after_footer_amend = normalized_snapshot_version()
            self.assertEqual(dirty, clean)
            self.assertEqual(clean, after_footer_amend)

    def test_hostile_git_redirect_environment_is_ignored(self) -> None:
        expected = _git_revision()
        hostile = {
            "GIT_DIR": "/definitely/not/the/repository",
            "GIT_WORK_TREE": "/definitely/not/the/worktree",
            "GIT_INDEX_FILE": "/definitely/not/the/index",
            "GIT_OBJECT_DIRECTORY": "/definitely/not/the/objects",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/definitely/not/alternates",
        }
        with patch.dict(os.environ, hostile, clear=False):
            self.assertEqual(_git_revision(), expected)

    def test_git_and_untracked_limits_are_exact(self) -> None:
        self.assertEqual(_MAX_TRACKED_LIST_BYTES, 1_048_576)
        self.assertEqual(_MAX_TRACKED_ENTRIES, 4_096)
        self.assertEqual(_GIT_ANCHOR_ARGUMENTS[-1], BASE_REVISION)
        self.assertEqual(_MAX_UNTRACKED_LIST_BYTES, 256 * 1_024)
        self.assertEqual(_MAX_UNTRACKED_ENTRIES, 256)
        self.assertEqual(_MAX_SNAPSHOT_PATH_BYTES, 4_096)
        self.assertEqual(_MAX_SNAPSHOT_FILE_BYTES, 2 * 1_048_576)
        self.assertEqual(_MAX_SNAPSHOT_TOTAL_BYTES, 8 * 1_048_576)

        accumulator = _BoundedAccumulator(4, digest_only=True)
        accumulator.push(b"1234")
        self.assert_snapshot_error(
            "E_SNAPSHOT_GIT_OUTPUT_LIMIT",
            lambda: accumulator.push(b"5"),
        )
        self.assert_snapshot_error(
            "E_SNAPSHOT_UNTRACKED_LIST_LIMIT",
            lambda: _parse_untracked_list(b"x" * (_MAX_UNTRACKED_LIST_BYTES + 1)),
        )
        too_many = b"\0".join(f"f{index}".encode() for index in range(257)) + b"\0"
        self.assert_snapshot_error(
            "E_SNAPSHOT_UNTRACKED_COUNT",
            lambda: _parse_untracked_list(too_many),
        )
        too_long = b"x" * (_MAX_SNAPSHOT_PATH_BYTES + 1) + b"\0"
        self.assert_snapshot_error(
            "E_SNAPSHOT_UNTRACKED_PATH",
            lambda: _parse_untracked_list(too_long),
        )
        self.assert_snapshot_error(
            "E_SNAPSHOT_TRACKED_LIST_FORMAT",
            lambda: _parse_index_paths(b"160000 " + b"a" * 40 + b" 0\tmodule\0"),
        )
        self.assert_snapshot_error(
            "E_SNAPSHOT_TRACKED_LIST_FORMAT",
            lambda: _parse_index_paths(
                b"100644 " + b"a" * 40 + b" 0\tduplicate\0"
                b"100644 " + b"b" * 40 + b" 0\tduplicate\0"
            ),
        )

    def test_untracked_faults_fail_closed_without_paths_or_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            safe = root / "safe.txt"
            safe.write_bytes(b"safe")

            link = root / "link.txt"
            link.symlink_to(safe)
            fifo = root / "pipe"
            os.mkfifo(fifo)
            oversized = root / "oversized.bin"
            with oversized.open("wb") as handle:
                handle.truncate(_MAX_SNAPSHOT_FILE_BYTES + 1)
            sensitive = root / ".env"
            sensitive.write_bytes(b"not-a-real-secret")
            hardlink = root / "hardlink.txt"
            os.link(safe, hardlink)
            writable = root / "writable.txt"
            writable.write_bytes(b"unsafe")
            writable.chmod(0o666)
            target_parent = root / "target-parent"
            target_parent.mkdir()
            (target_parent / "child.txt").write_bytes(b"child")
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(target_parent, target_is_directory=True)

            cases = (
                ("E_SNAPSHOT_FILE_TYPE", Path("link.txt")),
                ("E_SNAPSHOT_FILE_TYPE", Path("pipe")),
                ("E_SNAPSHOT_FILE_SIZE", Path("oversized.bin")),
                ("E_SNAPSHOT_SENSITIVE_PATH", Path(".env")),
                ("E_SNAPSHOT_FILE_LINKS", Path("safe.txt")),
                ("E_SNAPSHOT_FILE_MODE", Path("writable.txt")),
                ("E_SNAPSHOT_PATH", Path("linked-parent/child.txt")),
            )
            for code, relative in cases:
                with self.subTest(code=code, relative=relative.as_posix()):
                    self.assert_snapshot_error(
                        code,
                        lambda relative=relative: _safe_untracked_record(
                            root,
                            relative,
                            remaining_total=_MAX_SNAPSHOT_TOTAL_BYTES,
                        ),
                    )

            dataless = root / "dataless.txt"
            dataless.write_bytes(b"placeholder")
            with patch(
                f"{__name__}._metadata_is_dataless",
                return_value=True,
            ):
                self.assert_snapshot_error(
                    "E_SNAPSHOT_FILE_DATALESS",
                    lambda: _safe_untracked_record(
                        root,
                        Path("dataless.txt"),
                        remaining_total=_MAX_SNAPSHOT_TOTAL_BYTES,
                    ),
                )

            total = root / "total.bin"
            total.write_bytes(b"12345")
            self.assert_snapshot_error(
                "E_SNAPSHOT_UNTRACKED_TOTAL",
                lambda: _safe_untracked_record(root, Path("total.bin"), remaining_total=4),
            )

    def test_sensitive_path_is_rejected_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "credentials.json").write_bytes(b"not-a-real-secret")
            with patch("os.open") as opened:
                self.assert_snapshot_error(
                    "E_SNAPSHOT_SENSITIVE_PATH",
                    lambda: _safe_untracked_record(
                        root,
                        Path("credentials.json"),
                        remaining_total=_MAX_SNAPSHOT_TOTAL_BYTES,
                    ),
                )
            opened.assert_not_called()

    def test_file_metadata_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "drift.txt"
            path.write_bytes(b"stable")
            observed = path.stat()
            drifted = SimpleNamespace(
                **{
                    name: getattr(observed, name)
                    for name in (
                        "st_mode",
                        "st_ino",
                        "st_dev",
                        "st_nlink",
                        "st_uid",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                }
            )
            drifted.st_mtime_ns += 1
            real_fstat = os.fstat
            calls = 0

            def drifting_fstat(descriptor: int):
                nonlocal calls
                calls += 1
                return real_fstat(descriptor) if calls == 1 else drifted

            with patch("os.fstat", side_effect=drifting_fstat):
                self.assert_snapshot_error(
                    "E_SNAPSHOT_FILE_DRIFT",
                    lambda: _safe_untracked_record(
                        root,
                        Path("drift.txt"),
                        remaining_total=_MAX_SNAPSHOT_TOTAL_BYTES,
                    ),
                )

    def test_process_cleanup_terminates_then_kills_with_bounded_waits(self) -> None:
        class Process:
            def __init__(self, *, stubborn: bool = False) -> None:
                self.stubborn = stubborn
                self.terminated = False
                self.killed = False
                self.waits = 0

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def wait(self, *, timeout: float):
                self.waits += 1
                if self.waits == 1 or self.stubborn:
                    raise subprocess.TimeoutExpired("git", timeout)
                return -9

        recoverable = Process()
        _cleanup_process(recoverable)
        self.assertTrue(recoverable.terminated)
        self.assertTrue(recoverable.killed)
        self.assertEqual(recoverable.waits, 2)

        stubborn = Process(stubborn=True)
        self.assert_snapshot_error(
            "E_SNAPSHOT_PROCESS_CLEANUP",
            lambda: _cleanup_process(stubborn),
        )

    def test_oversized_git_stream_is_terminated_and_reaped(self) -> None:
        class Stream:
            def fileno(self) -> int:
                return 123

            def close(self) -> None:
                return None

        class Process:
            def __init__(self) -> None:
                self.stdout = Stream()
                self.terminated = False
                self.waited = False

            def poll(self):
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, *, timeout: float) -> int:
                self.waited = True
                return -15

        class Selector:
            def __init__(self, stream: Stream) -> None:
                self.stream = stream
                self.closed = False

            def register(self, stream: Stream, event: int) -> None:
                self.stream = stream

            def select(self, *, timeout: float):
                return [(SimpleNamespace(fileobj=self.stream), selectors.EVENT_READ)]

            def unregister(self, stream: Stream) -> None:
                return None

            def close(self) -> None:
                self.closed = True

        process = Process()
        selector = Selector(process.stdout)
        with (
            patch("subprocess.Popen", return_value=process),
            patch("selectors.DefaultSelector", return_value=selector),
            patch("os.read", return_value=b"12345"),
        ):
            self.assert_snapshot_error(
                "E_SNAPSHOT_GIT_OUTPUT_LIMIT",
                lambda: _run_git(
                    _GIT_REVISION_ARGUMENTS,
                    maximum=4,
                    digest_only=True,
                ),
            )
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertTrue(selector.closed)


if __name__ == "__main__":
    unittest.main()
