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
ADOPTION_ENABLEMENT_SPEC = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-13-control-plane-core-adoption-enablement-design.md"
)
ADOPTION_ENABLEMENT_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-13-control-plane-core-adoption-enablement.md"
)
STABLE_PAUSE_SPEC = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-14-control-plane-stable-pause-v1-design.md"
)
STABLE_PAUSE_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-14-control-plane-stable-pause-v1.md"
)
ALIGNMENT = ROOT / "docs" / "engineering" / "21-repository-alignment-and-branch-decisions.md"
ORIENTATION = ROOT / "docs" / "engineering" / "22-orientation-and-known-traps.md"
SPECPACK_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-18-control-plane-3-2-specpack.md"
)
ORIENTATION_DESIGN = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-18-control-plane-3-3-operator-orientation-design.md"
)
ORIENTATION_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-18-control-plane-3-3-operator-orientation.md"
)
REPOSITORY_SURVEY_V2_ADR = (
    ROOT / "docs" / "adr" / "0008-repository-survey-v2-contract.md"
)
REPOSITORY_SURVEY_V2_SPEC = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-21-repository-survey-v2-design.md"
)
REPOSITORY_SURVEY_V2_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-21-repository-survey-v2.md"
)
ORIENTATION_PLAN_SHA256 = (
    "7a2d275cadeaaa497a8a097da242a6b76f1dfccfbea1dd6f0320f78f27813475"
)
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
_GIT_TIMEOUT_SECONDS = 15.0
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
    "docs/superpowers/plans/2026-08-12-control-plane-core-3-1.md",
    "docs/superpowers/plans/2026-08-18-control-plane-3-3-operator-orientation.md",
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
    "docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md",
    "docs/superpowers/plans/2026-08-14-control-plane-stable-pause-v1.md",
)

LOCAL_ENABLEMENT_DOCUMENTS = (
    "docs/superpowers/specs/2026-08-13-control-plane-core-adoption-enablement-design.md",
    "docs/superpowers/plans/2026-08-13-control-plane-core-adoption-enablement.md",
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
    "docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md",
    "docs/superpowers/plans/2026-08-14-control-plane-stable-pause-v1.md",
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
    def test_final_verification_budget_and_reconciliation_review_are_bounded(self) -> None:
        maintenance = read(MAINTENANCE)
        stable_pause_plan = read(STABLE_PAUSE_PLAN)
        adoption_plan = read(ADOPTION_ENABLEMENT_PLAN)

        _, marker, verification = maintenance.partition("## Final verification budget")
        self.assertEqual(marker, "## Final verification budget")
        verification, _, _ = verification.partition("\n## ")
        verification_flat = " ".join(verification.split())
        for token in (
            "max_gate_runs=6",
            "gate_run_count",
            "same closure lineage",
            "does not reset",
            "verification mutex",
            "exact final bytes",
            "last consumed run",
            "Stable Pause",
            "fresh disposable executor",
            "waits internally",
            "periodic empty polls",
            "does not grant commit, push, PR, merge, deploy, or release authority",
            "both exact merge parents",
            "semantic reconciliation delta",
            "Inherited-parent bytes",
            "0 Critical / 0 Important",
        ):
            self.assertIn(token, verification_flat)

        alignment = read(ALIGNMENT)
        alignment_flat = " ".join(alignment.split())
        stable_pause_spec = read(STABLE_PAUSE_SPEC)
        for governing_gate_document in (
            stable_pause_plan,
            adoption_plan,
            stable_pause_spec,
            alignment,
        ):
            self.assertIn("max_gate_runs=6", governing_gate_document)
        for governing_gate_document in (
            stable_pause_plan,
            adoption_plan,
            stable_pause_spec,
        ):
            self.assertIn("last consumed run", governing_gate_document)
        self.assertIn("última ejecución consumida", alignment_flat)
        self.assertIn("gate_run_count=2", alignment)
        self.assertIn("3/6", alignment)
        self.assertIn("checkpoint previo e inmutable", alignment_flat)
        self.assertIn("resultados posteriores", alignment_flat)
        for stale_rule in (
            "fresh one-shot",
            "one authorized full gate",
            "fresh one-shot authority",
            "one-shot authorization",
            "grant fresco",
            "una sola ejecución local",
            "una sola vez el gate integral",
        ):
            for governing_gate_document in (
                stable_pause_plan,
                adoption_plan,
                stable_pause_spec,
                alignment,
            ):
                self.assertNotIn(stale_rule, governing_gate_document)

        dogfood = read(DOGFOOD)
        dogfood_flat = " ".join(dogfood.split())
        self.assertIn("Do not run a full suite per dogfood task", dogfood_flat)
        self.assertIn("max_gate_runs=6", dogfood_flat)
        self.assertNotIn("at most one authoritative full", dogfood_flat)

        for document in (read(ALIGNMENT), read(ORIENTATION), read(SPECPACK_PLAN)):
            document_flat = " ".join(document.split())
            for branch in (
                "`codex/control-plane-v3`",
                "`codex/control-plane-v2-3`",
                "`codex/control-plane-v2-4`",
                "`codex/taskplaybook-v0-impl`",
            ):
                self.assertIn(branch, document_flat)
            self.assertIn("no se aplica a todas las ramas `codex/*`", document_flat)
            self.assertIn(
                "`codex/control-plane-adoption-enablement-design`",
                document_flat,
            )
            self.assertNotIn("ramas `codex/*` de la línea v2.3–v3", document_flat)

    def test_stable_pause_governing_contract(self) -> None:
        specification = read(STABLE_PAUSE_SPEC)
        plan = read(STABLE_PAUSE_PLAN)
        readme = read(ROOT / "README.md")
        dogfood = read(DOGFOOD)
        index = read(CANONICAL_INDEX)
        combined = "\n".join((specification, plan, readme, dogfood))

        for document in (specification, plan):
            document_flat = " ".join(document.split())
            self.assertIn("GOVERNING_CORE / IMPLEMENTED_LOCAL", document_flat)
            self.assertIn("CLOSES_ON_FINAL_EVIDENCE", document_flat)
            self.assertIn("final frozen-byte evidence", document_flat)
            self.assertIn("authorizes=false", document_flat)
        self.assertIn("## Pre-freeze implementation evidence", plan)
        self.assertIn("E_GIT_DIRTY", plan)
        self.assertIn("Task 8 checkboxes remain intentionally open", plan)
        self.assertIn("native Goal and final handoff", plan)
        self.assertIn(
            "### Task 8 calibration remediation: exact Adoption projection",
            plan,
        )
        self.assertIn(
            "The prior unchanged-Adoption assumption was falsified",
            plan,
        )
        for status in (
            "SAFE_PAUSE_ACTIVE",
            "SAFE_PAUSE_TERMINAL",
            "UNSAFE_PAUSE",
            "UNKNOWN",
        ):
            self.assertIn(status, combined)
        for token in (
            "scripts/control-plane task checkpoint",
            "--mode stable-pause",
            "--task-id EXACT-TASK-ID",
            "--json",
            "exact task ID",
            "create=false",
            "adoption.lifecycle -> verification -> named task -> leases",
            "zero mutation",
            "dirty worktree",
            "failing RED",
            "not automatically unsafe",
            "native host before and after",
            "never upgrades",
            "4096 bytes",
            "checkpoint_digest",
            "exact selected repository root",
            "assume-unchanged",
            "skip-worktree",
            "core.filemode=true",
            "ignored caches stay outside",
            "nested repositories are unsupported",
            "single `cat-file --batch`",
            "exact release receipt",
            "resume",
        ):
            self.assertIn(token, combined)
        for path, purpose in (
            (
                "docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md",
                "WHAT/WHY",
            ),
            (
                "docs/superpowers/plans/2026-08-14-control-plane-stable-pause-v1.md",
                "HOW",
            ),
        ):
            self.assertEqual(index.count(f"| `{path}` | `GOVERNING_CORE` |"), 1)
            row = next(line for line in index.splitlines() if f"| `{path}` |" in line)
            self.assertIn(purpose, row)
            self.assertIn("IMPLEMENTED_LOCAL", row)
        for path in (
            "skills/control-plane-run/SKILL.md",
            "skills/control-plane-run/references/stable-pause-v1.md",
            "plugins/control-plane/skills/control-plane-run/references/stable-pause-v1.md",
        ):
            self.assertIn(path, combined)
        for forbidden_claim in (
            "stable_pause=RELEASED",
            "stable_pause=INSTALLED",
            "stable_pause=CONSUMER_PROVEN",
            "stable_pause=CANARY_PASS",
        ):
            self.assertNotIn(forbidden_claim, combined)

    def test_stable_pause_threats_and_runbook_are_aligned(self) -> None:
        maintenance = read(MAINTENANCE)
        security = read(ROOT / "SECURITY.md")
        threat = read(THREAT_MODEL)
        combined = "\n".join((maintenance, security, threat))

        _, marker, runbook = maintenance.partition("## Stable Pause v1")
        self.assertEqual(marker, "## Stable Pause v1")
        runbook, _, _ = runbook.partition("\n## ")
        runbook_flat = " ".join(runbook.split())
        for token in (
            "scripts/control-plane task checkpoint",
            "--mode stable-pause",
            "--task-id EXACT-TASK-ID",
            "--json",
            "SAFE_PAUSE_ACTIVE",
            "SAFE_PAUSE_TERMINAL",
            "UNSAFE_PAUSE",
            "UNKNOWN",
            "exit 0",
            "exit 1",
            "exit 2",
            "native host before and after",
            "never upgrades",
            "4096 bytes",
            "checkpoint_digest",
            "same task and worktree",
            "authorizes=false",
        ):
            self.assertIn(token, runbook_flat)
        for exclusion in (
            "no cleanup",
            "no lifecycle transition",
            "no Goal",
            "no test or gate",
            "no Git transition",
            "no remote effect",
            "no consumer",
            "no canary",
        ):
            self.assertIn(exclusion, runbook_flat)
        combined_flat = " ".join(combined.split())
        for attacker_story in (
            "repository byte substitution",
            "lock-domain substitution",
            "malicious Git config or filter",
            "residue smuggling",
            "digest-as-authority confusion",
            "host-visibility uncertainty",
            "index-hint hiding",
            "nested repository collapse",
            "terminal receipt deletion",
        ):
            self.assertIn(attacker_story, combined_flat)
        for residual in (
            "same-UID/filesystem compromise after the last descriptor check",
            "non-cooperating external writers",
        ):
            self.assertIn(residual, combined_flat)

    def test_stable_pause_skill_join_is_verify_only(self) -> None:
        skill = read(ROOT / "skills/control-plane-run/SKILL.md")
        reference = read(
            ROOT / "skills/control-plane-run/references/stable-pause-v1.md"
        )
        self.assertIn("only when", skill)
        self.assertIn("Do not load", skill)
        self.assertIn("ordinary Control Plane work", skill)
        for marker in (
            "verify-only",
            "native host",
            "before",
            "after",
            "foreground observer",
            "active host operation",
            "host visibility",
            "UNSAFE_PAUSE",
            "UNKNOWN",
            "never upgrades",
            "same task",
            "same worktree",
            "checkpoint_digest",
            "authorizes=false",
            "does not kill",
            "does not interrupt",
            "does not clean",
            "does not mutate task or lease state",
            "does not create a Goal",
            "does not run tests or gates",
            "does not perform Git or remote transitions",
            "transcript",
            "hidden reasoning",
            "raw output",
            "full diff",
            "secrets",
            "personal data",
        ):
            self.assertIn(marker, reference)
        self.assertIn(
            "Only the bounded foreground observer may be present during the invocation",
            reference,
        )
        self.assertIn(
            "Core `UNSAFE_PAUSE` or `UNKNOWN` is never upgraded by native evidence",
            reference,
        )
        for forbidden in (
            "create_goal(",
            "update_goal(",
            "task transition",
            "task lease-release",
            "bash tests/run.sh",
            "git commit",
            "git push",
            "git clean",
            "rm -rf",
        ):
            self.assertNotIn(forbidden, reference)

    def test_adoption_enablement_plan_has_closed_requirement_traceability(self) -> None:
        specification = ADOPTION_ENABLEMENT_SPEC.read_text(encoding="utf-8")
        plan = ADOPTION_ENABLEMENT_PLAN.read_text(encoding="utf-8")

        self.assertIn("Status: accepted for local implementation.", specification)
        self.assertIn(
            "Preparation state: `IMPLEMENTED_LOCAL / CANARY_PROHIBITED`.",
            specification,
        )
        self.assertIn("external_consumer_adoption=PROHIBITED", specification)
        self.assertIn("The Spec Kit CLI is not installed", plan)
        for document in (specification, plan):
            for token in (
                "managed_parent_directories",
                "managed_repository_scan",
                "managed-repositories-v1",
                "journal-bound-v1",
                "lifecycle_lock",
                "verification_lock",
                "`create=false`",
                "reuse-only",
                "`scripts/control-plane` from the selected source",
                "ROOT_EMPTY",
                "`P2Q`",
                "`P3Q`",
                "durable quarantine",
                "nonblocking",
                "exact-value",
                "lifecycle inode before the task lock",
            ):
                self.assertIn(token, document)
        _, marker, evidence = plan.partition("## Implementation evidence")
        self.assertEqual(marker, "## Implementation evidence")
        evidence, _, _ = evidence.partition("\n## ")
        for index in range(1, 10):
            requirement = f"AE-{index:02d}"
            self.assertGreaterEqual(plan.count(requirement), 2, requirement)
            resolution = "CLOSES_ON_FINAL_EVIDENCE" if index == 9 else "CLOSED"
            self.assertRegex(
                evidence,
                rf"(?m)^\| `{requirement}` \| RED: [^|]+ \| GREEN: [^|]+ \| "
                rf"ROLLBACK: [^|]+ \| `{resolution}` \|$",
            )
        self.assertIn(
            "one passing final-byte focal set, a full gate whose last consumed "
            "run is green within `max_gate_runs=6`, all post-gates and both "
            "independent rereviews on identical bytes",
            evidence,
        )
        for test_name in (
            "test_git_markers_inside_managed_scope_are_rejected_without_mutation",
            "test_gitlink_inside_managed_scope_is_rejected_without_mutation",
            "test_managed_repository_scan_depth_and_count_are_bounded_without_mutation",
            "test_nested_repository_drift_after_apply_blocks_verify_and_rollback_before_mutation",
            "test_target_authority_uses_the_exact_selected_source_entrypoint",
            "test_source_head_drift_after_locked_preview_fails_before_journal",
            "test_unjournaled_mutex_provisioning_is_exactly_recoverable",
            "test_core_owned_verification_mutex_is_not_adoption_provisioning",
            "test_provisioning_recovery_validates_plan_before_cleanup",
            "test_fresh_verification_provisioning_is_exclusive",
            "test_verification_guard_revalidates_common_and_state_after_flock",
            "test_rollback_rejects_a_missing_or_replaced_bound_verification_mutex",
            "test_core_and_runner_require_a_closed_active_adoption_journal",
            "test_invalid_active_adoption_journal_blocks_task_and_lease_mutation",
            "test_invalid_active_adoption_journal_blocks_new_lease_claim",
            "test_core_verifier_retains_the_locked_directory_identity",
            "test_runner_retains_the_locked_directory_identity",
            "test_runner_rejects_a_symlinked_adoption_binding_ancestor",
            "test_active_adoption_journal_counts_the_root_toward_the_item_bound",
            "test_invalid_active_journal_blocks_new_task_before_creating_its_lock",
            "test_each_partial_journalless_provisioning_prefix_is_recoverable",
            "test_each_provisioning_cleanup_boundary_remains_retryable",
            "test_post_cleanup_validation_failure_leaves_a_retryable_prefix",
            "test_core_only_verification_prefixes_are_preserved",
            "test_forged_closed_task_blocks_rollback_without_mutation",
            "test_rollback_preserves_a_record_substituted_after_preflight",
            "test_confined_read_opens_the_leaf_nonblocking",
            "test_root_empty_core_prefix_race_removes_only_the_created_lifecycle_lock",
            "test_p2_p3_cleanup_never_removes_a_substituted_directory",
            "test_p4t_cleanup_opens_and_revalidates_the_observed_temporary",
            "test_new_task_holds_a_lifecycle_domain_even_when_adoption_was_absent",
            "test_rollback_conditionally_removes_only_its_exact_hooks_path",
            "test_rollback_retains_open_managed_and_activation_inodes_in_quarantine",
            "test_rollback_rechecks_managed_quarantine_after_an_open_descriptor_write",
            "test_rollback_rechecks_activation_quarantine_after_an_open_descriptor_write",
        ):
            self.assertIn(test_name, evidence)
        _, addendum_marker, addendum = plan.partition(
            "### Subsequent AE-09 verification-lock remediation"
        )
        self.assertEqual(
            addendum_marker,
            "### Subsequent AE-09 verification-lock remediation",
        )
        addendum, _, _ = addendum.partition("Run focused tests first:")
        for token in (
            "control_plane/contracts.py",
            "control_plane/verification.py",
            "tests/test_core_task_state.py",
            "tests/test_core_verification.py",
            "tests/run.sh",
            ".codex/control-plane.lock",
            ".codex/adoption-enablement.lock",
            "verification_lock",
            "does not grant, replay, or transfer authority",
            "authorizes=false",
        ):
            self.assertIn(token, addendum)
        _, final_addendum_marker, final_addendum = plan.partition(
            "### Subsequent AE-09 final concurrency and quarantine remediation"
        )
        self.assertEqual(
            final_addendum_marker,
            "### Subsequent AE-09 final concurrency and quarantine remediation",
        )
        final_addendum, _, _ = final_addendum.partition("Run focused tests first:")
        for token in (
            "control_plane/leases.py",
            "adoption_enablement/safe_io.py",
            "adoption_enablement/repository.py",
            "adoption_enablement/transaction.py",
            "tests/test_core_task_state.py",
            "tests/test_core_leases.py",
            "tests/test_adoption_enablement_repository.py",
            "tests/test_adoption_enablement_transaction.py",
            "tests/test_adoption_enablement_recovery.py",
            "verification_lock",
            "`P2Q`",
            "`P3Q`",
            "durable quarantine",
            "does not grant, replay, or transfer authority",
            "authorizes=false",
        ):
            self.assertIn(token, final_addendum)
        self.assertNotIn(
            "that bounded closure requires only the `task_state.py` revalidation "
            "and `leases.py` shared adoption barrier",
            plan,
        )
        for token in (
            "O_CREAT|O_EXCL",
            "pre-existing Core-owned verification mutex",
            "validates the reviewed plan before cleanup",
            "closed active journal",
            "common/state/locks/file",
        ):
            self.assertIn(token, specification)
        for boundary in (
            "atomic no-replace rename",
            "verification=UNKNOWN",
            "fixture teardown",
            "bound to `3.1.0-core.2`",
        ):
            self.assertIn(boundary, specification)
        self.assertIn(
            "product rollback must never delete Core-owned task evidence",
            plan,
        )
        self.assertIn("authorized bump to `3.1.0-core.2`", plan)
        self.assertIn(
            "evidence bound to `3.1.0-core.1` remains historical",
            plan,
        )
        for task in range(1, 9):
            self.assertEqual(
                len(re.findall(rf"^## Task {task}:", plan, re.MULTILINE)),
                1,
            )
        self.assertIn("IMPLEMENTED_LOCAL / CANARY_PROHIBITED", plan)
        self.assertNotIn("Decision: `GO_STABLE_ADOPTION`", plan)
        self.assertNotIn("Autopilot = ON", plan)

    def test_adoption_enablement_is_local_only_and_non_authorizing(self) -> None:
        documents = {
            "readme": read(ROOT / "README.md"),
            "security": read(ROOT / "SECURITY.md"),
            "adr": read(ADR),
            "index": read(CANONICAL_INDEX),
            "maintenance": read(MAINTENANCE),
            "threat": read(THREAT_MODEL),
        }
        combined = "\n".join(documents.values())
        for token in (
            "adoption_tool=IMPLEMENTED_LOCAL",
            "temporary_repository_e2e=PASS",
            "external_consumer_adoption=PROHIBITED",
            "canary=NOT_PREPARED",
            "stable_adoption=NOT_DECIDED",
            "Autopilot OFF",
            "authorizes=false",
        ):
            self.assertIn(token, combined)
        for surface in ("readme", "security", "maintenance"):
            self.assertIn("E_CAPABILITY_QUARANTINED", documents[surface])
        for surface in ("security", "threat"):
            for token in (
                "managed-repositories-v1",
                "journal-bound-v1",
                "lifecycle_lock",
                "verification.lock",
                "verification_lock",
                "reuse-only",
                "pre-existing Core-owned verification mutex",
                "closed active journal",
                "P2Q",
                "P3Q",
                "durable quarantine",
                "exact-value",
                "nonblocking",
            ):
                self.assertIn(token, documents[surface])
        self.assertIn("does not supersede the adoption prohibition", documents["adr"])
        self.assertIn("later independently accepted ADR", documents["adr"])
        self.assertIn(
            "before even preparing one disposable canary",
            documents["adr"],
        )
        for path in LOCAL_ENABLEMENT_DOCUMENTS:
            self.assertRegex(
                documents["index"],
                rf"(?m)^\| `{re.escape(path)}` \| `GOVERNING_LOCAL_ENABLEMENT` \|",
            )
        for threat in (
            "source substitution",
            "wrong-target selection",
            "partial publication",
            "journal tampering",
            "rollback deletion",
            "filter execution",
            "hostile environment",
            "lock replay",
            "serialized-authority confusion",
            "nested-repository smuggling",
            "selected-source authority substitution",
            "lifecycle-lock substitution",
            "verification-mutex substitution",
        ):
            self.assertIn(threat, documents["threat"])
        self.assertNotIn("canary=PASS", combined)
        self.assertNotIn("stable_adoption=APPROVED", combined)

    def test_required_core_documents_and_headings_exist(self) -> None:
        contracts = {
            ADR: (
                "# ADR 0006: Control Plane Core and structural quarantine",
                "## Context",
                "## Decision",
                "## Local adoption enablement",
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
                "## Local adoption enablement",
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
                "## Governing local enablement documents",
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
            "3.1.0-core.1 — superseded local prerelease candidate",
            "3.1.0-core.2 — current local prerelease candidate",
            "Fresh ten-task dogfood is required for `3.1.0-core.2`",
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

    def test_completed_orientation_and_pending_specpack_have_current_statuses(self) -> None:
        statuses = index_statuses()
        orientation_path = ORIENTATION_PLAN.relative_to(ROOT).as_posix()
        specpack = read(SPECPACK_PLAN)
        orientation = read(ORIENTATION)
        self.assertEqual(
            statuses.get(orientation_path),
            "HISTORICAL_NON_GOVERNING",
        )
        self.assertIn("IMPLEMENTED_LOCAL", read(ORIENTATION_DESIGN))
        self.assertNotIn("sin implementación", read(ORIENTATION_DESIGN))
        self.assertIn("BLOCKED_ON_R1_FINAL_EVIDENCE", specpack)
        self.assertNotIn("3.1.0-core.1", specpack)
        self.assertIn("3.1.0-core.2", specpack)
        self.assertIn("R1_CLOSED_ON_FINAL_EVIDENCE", specpack)
        self.assertIn("este plan no registra estado vivo", specpack)
        self.assertNotIn(
            "reparaciones, prerevisiones frescas y evidencia final pendientes",
            specpack,
        )
        self.assertNotIn(
            "| 1 | Contrato: plantillas y skill | no | Ninguna, ejecutable ya |",
            specpack,
        )
        self.assertIn("BLOCKED_ON_R1_FINAL_EVIDENCE", orientation)
        self.assertIn("codex/reconcile-core-3-1-core-2", orientation)
        self.assertIn("este documento no registra estado vivo", orientation)
        self.assertNotIn("R1_OPEN / FINAL_EVIDENCE_PENDING", orientation)
        self.assertNotIn("recorte por procedencia y evidencia final pendientes", orientation)
        alignment = read(ALIGNMENT)
        orientation_design = read(ORIENTATION_DESIGN)
        adoption_plan = read(ADOPTION_ENABLEMENT_PLAN)
        maintenance = read(MAINTENANCE)
        maintenance_flat = " ".join(maintenance.split())
        self.assertIn("no registra estado vivo ni autoridad", alignment)
        self.assertIn("no registra estado vivo ni autoridad", orientation)
        self.assertIn("no registra el estado vivo de cierre de R1", orientation_design)
        self.assertIn("outside the 27-module Core runtime", adoption_plan)
        self.assertIn("no registra estado Git vivo ni autoridad", adoption_plan)
        self.assertIn("does not record live Git state or authority", maintenance_flat)
        for governing_document in (alignment, orientation, orientation_design):
            self.assertNotIn("evidencia final de R1 pendiente", governing_document)
            self.assertNotIn("commits locales de R1 sí están autorizados", governing_document)
            self.assertNotIn("commits locales de R1 están autorizados", governing_document)
        r1_decision_governing_documents = (
            ROOT / "README.md",
            CANONICAL_INDEX,
            MAINTENANCE,
            DOGFOOD,
            ALIGNMENT,
            ORIENTATION,
            THREAT_MODEL,
            ADOPTION_ENABLEMENT_PLAN,
            STABLE_PAUSE_PLAN,
            SPECPACK_PLAN,
            STABLE_PAUSE_SPEC,
            ORIENTATION_DESIGN,
        )
        for governing_path in r1_decision_governing_documents:
            governing_document = read(governing_path)
            for stale_contract in (
                "outside the 25-module Core runtime",
                "origin/main@b07418364409f76c900f0595a76c9e3e388ac433",
                "candidato local `3.1.0-core.2` sin commit",
                "The candidate remains isolated in its own worktree",
            ):
                self.assertNotIn(stale_contract, governing_document, governing_path)
        self.assertNotIn("Gate integral `395 OK`", orientation)
        self.assertNotIn("AE-09 pendiente", orientation)
        self.assertNotIn("protección de rama ausente", alignment)

    def test_repository_survey_v2_governing_documentation_contract(self) -> None:
        index = read(CANONICAL_INDEX)
        candidate_status = "IMPLEMENTED_LOCAL_CANDIDATE / FINAL_GATE_PENDING"
        for path in (
            REPOSITORY_SURVEY_V2_ADR,
            REPOSITORY_SURVEY_V2_SPEC,
            REPOSITORY_SURVEY_V2_PLAN,
        ):
            relative = path.relative_to(ROOT).as_posix()
            rows = re.findall(
                rf"(?m)^\| `{re.escape(relative)}` \| `([^`]+)` \|",
                index,
            )
            self.assertEqual(rows, [candidate_status], relative)

        orientation_design = read(ORIENTATION_DESIGN)
        self.assertIn("### Supersesión del Survey V1 — 2026-08-21", orientation_design)
        self.assertIn(
            "[RepositorySurveyV2](2026-08-21-repository-survey-v2-design.md)",
            orientation_design,
        )
        self.assertIn(
            "[ADR 0008](../../adr/0008-repository-survey-v2-contract.md)",
            orientation_design,
        )
        self.assertIn(
            "Solo el bloque de contrato `RepositorySurveyV1` de este diseño "
            "queda sustituido",
            orientation_design,
        )
        self.assertEqual(
            sha256(ORIENTATION_PLAN.read_bytes()).hexdigest(),
            ORIENTATION_PLAN_SHA256,
        )

        specification = read(REPOSITORY_SURVEY_V2_SPEC)
        plan = read(REPOSITORY_SURVEY_V2_PLAN)
        self.assertIn(
            "Estado: `IMPLEMENTED_LOCAL_CANDIDATE / FINAL_GATE_PENDING`",
            specification,
        )
        self.assertIn(
            "**Status:** `EXECUTION_AUTHORIZED / EVIDENCE_PENDING / "
            "SHALLOW_SCOPE_REFRAME_ACCEPTED`",
            plan,
        )

        readme = read(ROOT / "README.md")
        skill = read(ROOT / "skills" / "control-plane-git" / "SKILL.md")
        for document in (readme, skill):
            for token in (
                "RepositorySurveyV2",
                "PASS=0",
                "FAIL=1",
                "UNKNOWN=2",
                "WARN=3",
                "unpublished_unique",
                "added_paths=null",
                "other_clones=UNKNOWN",
            ):
                self.assertIn(token, document)
        self.assertIn(
            "refs remotas locales pueden estar obsoletas",
            readme.lower(),
        )
        self.assertIn(
            "local remote-tracking refs can be stale",
            skill.lower(),
        )

        equivalence_command = (
            "git diff --quiet <fixed-base-oid>..<fixed-branch-oid>"
        )
        add_only_command = (
            "git diff --diff-filter=A --name-only "
            "<fixed-base-oid>..<fixed-branch-oid>"
        )
        operational_documents = (
            ROOT / "AGENTS.md",
            ROOT / "skills" / "control-plane-git" / "SKILL.md",
            ALIGNMENT,
            ORIENTATION,
        )
        for path in operational_documents:
            document = read(path)
            self.assertIn(equivalence_command, document, path)
            self.assertNotIn(
                "git diff --diff-filter=A --name-only origin/main..<rama>",
                document,
                path,
            )
            self.assertNotIn(
                "git diff --diff-filter=A --name-only <base>..<branch>",
                document,
                path,
            )
            for match in re.finditer(re.escape(add_only_command), document):
                context = document[
                    max(0, match.start() - 300) : min(len(document), match.end() + 300)
                ]
                self.assertIn("added_paths", context, path)
                self.assertRegex(context.lower(), r"informati(?:onal|vo)|nullable")

        threat = read(THREAT_MODEL)
        _, marker, residual = threat.partition("## Residual risks")
        self.assertEqual(marker, "## Residual risks")
        residual_flat = " ".join(residual.split())
        for closed_residual in (
            "the top-level `orphan_work` meaning",
            "the add-only `only_in_branch` field",
        ):
            self.assertNotIn(closed_residual, residual_flat)
        for retained_residual in (
            "filter execution",
            "submodule/Gitlink",
            "object-alternate",
            "detached-worktree substitution",
            "discovery-to-walk TOCTOU",
            "newline-bearing paths",
            "APFS case-equivalent Git markers",
            "symlinked `.git/config`",
            "linked-worktree rollback inventory",
        ):
            self.assertIn(retained_residual, residual_flat)

        for attacker_story in (
            "a stale local remote-tracking ref is treated as remote proof",
            "a wrapper collapses `warn` into `pass` or `fail`",
            "optional `added_paths` is treated as mandatory evidence",
            "a mutable base ref is substituted between observations",
        ):
            self.assertIn(attacker_story, threat.lower())
        for mitigation in (
            "authenticated remote observation remains separate",
            "`WARN=3` and `ok=false`",
            "normative status is frozen before optional `added_paths` enrichment",
            "fixed base commit and tree OIDs",
            "Shallow reachability is untrusted",
            "candidate-only shallow check",
            "remaining time in the existing five-second deadline",
        ):
            self.assertIn(mitigation, threat)

        for document in (index, specification, orientation_design, readme, skill, threat):
            for unsupported_claim in (
                "FINAL_GATE_PASSED",
                "RELEASED_CANDIDATE",
                "ADOPTION_APPROVED",
                "INSTALLATION_PROVEN",
                "REMOTE_PROOF_CONFIRMED",
            ):
                self.assertNotIn(unsupported_claim, document)

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

    def test_active_registry_never_routes_historical_non_governing_documents(self) -> None:
        registry = tomllib.loads(read(ROOT / ".codex" / "resource-registry.toml"))
        statuses = index_statuses()
        resources = {item["id"]: item for item in registry["resources"]}
        routed = {
            resource_id
            for route in registry["routes"]
            for field in ("recommended_resources", "forbidden_resources")
            for resource_id in route[field]
        }
        for resource_id, resource in resources.items():
            if resource.get("kind") != "document" or not resource.get("canonical"):
                continue
            locator = resource.get("locator", "")
            if not locator.startswith("repo://"):
                continue
            relative = locator.removeprefix("repo://")
            self.assertNotEqual(
                statuses.get(relative),
                "HISTORICAL_NON_GOVERNING",
                resource_id,
            )
        resource_ids = set(resources)
        self.assertTrue(routed.issubset(resource_ids), sorted(routed - resource_ids))

    def test_maintenance_runbook_is_fail_closed_and_time_bounded(self) -> None:
        content = read(MAINTENANCE)
        content_flat = " ".join(content.split())
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
            "reobserve worktree, branch, HEAD, base and native",
        ):
            self.assertIn(token, content_flat)
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
        _, marker, mutex = content.partition("## Verification mutex")
        self.assertEqual(marker, "## Verification mutex")
        mutex, _, _ = mutex.partition("## Maintenance circuit breaker")
        for token in (
            "locks/verification.lock",
            "verification_lock",
            "persistent",
            "create=false",
            "reuse-only",
            "never unlink",
            "E_VERIFICATION_LOCK",
            "E_TEST_MUTEX",
            "Fresh Adoption apply",
            "pre-existing Core-owned verification mutex",
            "closed active journal",
            "authorizes=false",
        ):
            self.assertIn(token, mutex)
        _, marker, quarantine = content.partition("## Adoption rollback quarantine")
        self.assertEqual(marker, "## Adoption rollback quarantine")
        quarantine, _, _ = quarantine.partition("## Maintenance circuit breaker")
        for token in (
            "adoption.lock",
            "lifecycle inode before the task lock",
            "ROOT_EMPTY",
            "P2Q",
            "P3Q",
            "nonblocking",
            "exact-value",
            "durable quarantine",
            "separate GC",
            "authorizes=false",
        ):
            self.assertIn(token, quarantine)

    def test_core_task_and_lease_state_locations_match_runtime(self) -> None:
        agents = read(ROOT / "AGENTS.md")
        adr = read(ROOT / "docs" / "adr" / "0006-control-plane-core-and-quarantine.md")
        maintenance = read(MAINTENANCE)
        readme = read(ROOT / "README.md")
        for content in (agents, adr, maintenance, readme):
            self.assertRegex(content, r"worktree Git\s+dir")
            self.assertRegex(content, r"Git common\s+dir")
            self.assertRegex(content, r"across\s+worktrees")
        self.assertNotIn(
            "leases bajo el Git dir del worktree",
            agents,
        )

    def test_manual_dogfood_scorecard_is_closed(self) -> None:
        content = read(DOGFOOD)
        self.assertEqual(
            [line for line in content.splitlines() if line.startswith("Status:")],
            [
                "Status: `HISTORICAL_PASS_10_TASK_CORE_1 / "
                "CORE_2_DOGFOOD_PENDING`. `Autopilot=OFF`."
            ],
        )
        entry_marker = "## Entry gate\n\n"
        scorecard_marker = "\n## Scorecard\n"
        _, entry_separator, entry_tail = content.partition(entry_marker)
        entry_gate, scorecard_separator, _ = entry_tail.partition(scorecard_marker)
        self.assertEqual(entry_separator, entry_marker)
        self.assertEqual(scorecard_separator, scorecard_marker)
        self.assertIn(
            "- Use the exact `3.1.0-core.2` source candidate and record its "
            "runtime digest.",
            entry_gate,
        )
        self.assertNotIn("3.1.0-core.1", entry_gate)
        self.assertIsNone(
            re.search(r"(?i)\bAutopilot\s*=\s*ON\b", content),
            "Autopilot must remain OFF everywhere in the scorecard",
        )
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
        expected_rows = [
            ["CORE-DOGFOOD-01", "local", "true", "answer", "local_read", "0", "PASS", "CORE-DOGFOOD-01-E1"],
            ["CORE-DOGFOOD-02", "hybrid", "true", "answer", "local_read", "0", "PASS", "CORE-DOGFOOD-02-E1"],
            ["CORE-DOGFOOD-03", "controlled", "true", "answer", "local_read", "0", "PASS", "CORE-DOGFOOD-03-E1"],
            ["CORE-DOGFOOD-04", "local", "false", "local_change", "local_read+local_write", "1", "PASS", "CORE-DOGFOOD-04-E1"],
            ["CORE-DOGFOOD-05", "local", "false", "local_change", "local_read+local_write", "1", "PASS", "CORE-DOGFOOD-05-E1"],
            ["CORE-DOGFOOD-06", "hybrid", "false", "local_change", "local_read+local_write", "1", "PASS", "CORE-DOGFOOD-06-E1"],
            ["CORE-DOGFOOD-07", "hybrid", "false", "local_change", "local_read+local_write", "1", "PASS", "CORE-DOGFOOD-07-E1"],
            ["CORE-DOGFOOD-08", "controlled", "false", "local_change", "local_read+local_write", "1", "PASS", "CORE-DOGFOOD-08-E1"],
            ["CORE-DOGFOOD-09", "controlled", "false", "local_change", "local_read+local_write", "1", "PASS", "CORE-DOGFOOD-09-E1"],
            ["CORE-DOGFOOD-10", "controlled", "false", "local_change", "local_read+local_write", "1", "PASS", "CORE-DOGFOOD-10-E1"],
        ]
        self.assertEqual(parsed, expected_rows)
        self.assertGreaterEqual(sum(row[2] == "true" for row in parsed), 3)
        self.assertTrue({"local", "hybrid", "controlled"}.issubset({row[1] for row in parsed}))
        self.assertEqual(
            [row[6:8] for row in parsed],
            [["PASS", f"CORE-DOGFOOD-{index:02d}-E1"] for index in range(1, 11)],
        )
        for row in parsed:
            if row[2] == "true":
                self.assertEqual(row[3:6], ["answer", "local_read", "0"])
        for token in (
            "Autopilot=OFF",
            "HISTORICAL_PASS_10_TASK_CORE_1 / CORE_2_DOGFOOD_PENDING",
            "Fresh ten-task dogfood is required for core.2",
            "tasks_completed=10",
            "facts_only_total=3",
            "duplicated_effects=0",
            "fabricated_effects=0",
            "overlapping_writers=0",
            "nuisance_warnings=0",
            "duplicated_full_suites=0",
            "authoritative_full_gate=PENDING",
            "No prompts, transcripts, or telemetry",
        ):
            self.assertIn(token, content)

        expected_digest_by_reference = {
            "CORE-DOGFOOD-01-E1": (
                "sha256:f4c03568ee778872ed35cd5b1ba9397875c68e89e5e830278e755a2da21c0ab7"
            ),
            "CORE-DOGFOOD-02-E1": (
                "sha256:f5b8e541742b434f65610092828d00252341dc8df3d04570dacc022e60553849"
            ),
            "CORE-DOGFOOD-03-E1": (
                "sha256:b3ef611e3fbbea8bd54ba15688425aa94822cc517477533094ab5bf0306c23f8"
            ),
            "CORE-DOGFOOD-04-E1": (
                "sha256:f4c31e70e80df5d51d892d552856c9128c408a548246b8d37f629525ced2ac56"
            ),
            "CORE-DOGFOOD-05-E1": (
                "sha256:347e67cd18fe64538805c0b3ba578a243e50099fe8887bfe9a26c4aaab0ec734"
            ),
            "CORE-DOGFOOD-06-E1": (
                "sha256:fb7c3f0ae0a2ff0a39bfd4927d6aba7d7dca04d3cb7d190f0943dfc13c67ac24"
            ),
            "CORE-DOGFOOD-07-E1": (
                "sha256:ebe5ecf36f0d445e6a3dcd7ec9b53c3f571323205d9a0e3c3c7d766c0cedf074"
            ),
            "CORE-DOGFOOD-08-E1": (
                "sha256:2cd791638d31ed8fadaf5adb29541d8a9e1483c0df41e9035d1b1b0f6dc08b25"
            ),
            "CORE-DOGFOOD-09-E1": (
                "sha256:78ae6111baa5503f411961c974433e4cba3f136b514a17faa9f6ad5a7daed42e"
            ),
            "CORE-DOGFOOD-10-E1": (
                "sha256:ec29389f6afb3492d85f21f4e92ca20e699a2605f66a97c466e12292c408d8e8"
            ),
        }
        self.assertEqual(
            re.findall(r"(?m)^## Evidence registry$", content),
            ["## Evidence registry"],
        )
        self.assertEqual(
            re.findall(r"(?m)^## Continuación$", content),
            ["## Continuación"],
        )
        _, evidence_marker, after_evidence_marker = content.partition(
            "## Evidence registry\n\n"
        )
        self.assertEqual(evidence_marker, "## Evidence registry\n\n")
        evidence_section, continuation_marker, continuation = (
            after_evidence_marker.partition("\n## Continuación\n")
        )
        self.assertEqual(continuation_marker, "\n## Continuación\n")
        evidence_matches = list(
            re.finditer(
                r"(?m)^### `(?P<reference>CORE-DOGFOOD-[0-9]{2}-E[0-9]+)`\n\n"
                r"```json\n(?P<payload>\{[^\n]*\})\n```\n\n"
                r"Evidence digest: `(?P<digest>sha256:[0-9a-f]{64})`$",
                evidence_section,
            )
        )
        self.assertEqual(
            evidence_section.strip(),
            "\n\n".join(match.group(0) for match in evidence_matches),
        )
        self.assertEqual(
            [match.group("reference") for match in evidence_matches],
            list(expected_digest_by_reference),
        )
        rows_by_id = {row[0]: row for row in parsed}

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise AssertionError(f"duplicate evidence key: {key}")
                result[key] = value
            return result

        for match in evidence_matches:
            reference = match.group("reference")
            evidence = json.loads(
                match.group("payload"), object_pairs_hook=reject_duplicate_keys
            )
            self.assertEqual(evidence["schema_version"], 1)
            self.assertEqual(evidence["kind"], "CoreDogfoodEvidenceV1")
            self.assertEqual(evidence["task_id"], reference.removesuffix("-E1"))
            self.assertEqual(evidence["result"], "PASS")
            self.assertEqual(evidence["review"], "APPROVED")
            self.assertIs(evidence["authorizes"], False)
            row = rows_by_id[str(evidence["task_id"])]
            contract = evidence["contract"]
            self.assertIsInstance(contract, dict)
            assert isinstance(contract, dict)
            self.assertEqual(contract["facts_only"], row[2] == "true")
            self.assertEqual(contract["requested_outcome"], row[3])
            self.assertEqual(
                contract.get("effects", contract.get("allowed_effects")),
                row[4].split("+"),
            )
            self.assertEqual(contract["writers"], int(row[5]))
            self.assertEqual(
                contract.get("workload", contract.get("workflow")), row[1]
            )
            self.assertFalse(
                contract.get("durable_task_state", contract.get("durable"))
            )
            def assert_non_authorizing(value: object) -> None:
                if isinstance(value, dict):
                    for key, nested in value.items():
                        if key == "authorizes":
                            self.assertIs(nested, False)
                        assert_non_authorizing(nested)
                elif isinstance(value, list):
                    for nested in value:
                        assert_non_authorizing(nested)

            assert_non_authorizing(evidence)
            canonical = json.dumps(
                evidence,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            self.assertEqual(
                match.group("digest"),
                f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}",
            )
            self.assertEqual(
                match.group("digest"), expected_digest_by_reference[reference]
            )
            for forbidden_key in (
                "objective",
                "prompt",
                "transcript",
                "telemetry",
                "secret",
                "authority",
                "authorization",
            ):
                self.assertNotIn(f'"{forbidden_key}"', canonical)
            self.assertNotIn("tests/run.sh", canonical)
            self.assertNotRegex(canonical, r"(?i)full[_ -]?(gate|suite).*PASS")
        self.assertEqual(
            re.findall(r"(?m)^- \*\*([^*]+):\*\*", continuation),
            [
                "Escribe en",
                "Rol",
                "Para continuar",
                "Mensaje exacto",
                "Estado de partida",
                "No hacer todavía",
                "Autoridad",
            ],
        )
        self.assertEqual(
            continuation.strip(),
            "\n".join(
                (
                    "- **Escribe en:** este hilo.",
                    "- **Rol:** orquestadora del candidato Core y scorecard manual.",
                    "- **Para continuar:** ejecutar un nuevo dogfood manual de diez "
                    "tareas ligado al digest final de `3.1.0-core.2` en una tarea "
                    "separada.",
                    "- **Mensaje exacto:** `Prepara el dogfood manual local de "
                    "3.1.0-core.2; no instales, no uses consumidor ni habilites "
                    "Autopilot.`",
                    "- **Estado de partida:** `3.1.0-core.2` pendiente de dogfood; "
                    "las diez filas `PASS` de `3.1.0-core.1` son evidencia histórica "
                    "y la adopción estable no está autorizada.",
                    "- **No hacer todavía:** instalar, adoptar externamente, "
                    "commit, push, PR, merge, deploy, publicación o release.",
                    "- **Autoridad:** `authorizes=false`",
                )
            ),
        )

    def test_manual_dogfood_scorecard_rejects_resigned_or_structural_drift(self) -> None:
        content = read(DOGFOOD)
        evidence_match = re.search(
            r"(?ms)^### `CORE-DOGFOOD-01-E1`\n\n"
            r"```json\n(?P<payload>\{.*?\})\n```\n\n"
            r"Evidence digest: `(?P<digest>sha256:[0-9a-f]{64})`$",
            content,
        )
        self.assertIsNotNone(evidence_match)
        assert evidence_match is not None

        def resigned(mutator: object) -> str:
            payload = json.loads(evidence_match.group("payload"))
            assert callable(mutator)
            mutator(payload)
            canonical = json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            digest = f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"
            changed = content.replace(evidence_match.group("payload"), canonical, 1)
            return changed.replace(evidence_match.group("digest"), digest, 1)

        mutations = {
            "duplicate_json_key": content.replace(
                evidence_match.group("payload"),
                evidence_match.group("payload").replace(
                    "{", '{"authorizes":true,', 1
                ),
                1,
            ),
            "extra_malformed_evidence_block": content.replace(
                "\n## Continuación\n",
                "\n### `CORE-DOGFOOD-99-E1`\n\n```json\n{}\n```\n\n"
                "Evidence digest: `sha256:"
                + "0" * 64
                + "`\n\n## Continuación\n",
                1,
            ),
            "duplicate_continuation_heading": content.replace(
                "\n## Evidence registry\n",
                "\n## Continuación\n\n- stale\n\n## Evidence registry\n",
                1,
            ),
            "row_payload_workload_mismatch": content.replace(
                "| `CORE-DOGFOOD-01` | `local` |",
                "| `CORE-DOGFOOD-01` | `hybrid` |",
                1,
            ),
            "stray_autopilot_on": content.replace(
                "This is a manual evidence gate",
                "Autopilot=ON\n\nThis is a manual evidence gate",
                1,
            ),
            "resigned_workload": resigned(
                lambda payload: payload["contract"].__setitem__(
                    "workload", "controlled"
                )
            ),
            "resigned_extra_key": resigned(
                lambda payload: payload.__setitem__("prompt_text", "synthetic")
            ),
            "contradictory_autopilot": content.replace(
                "Status: `HISTORICAL_PASS_10_TASK_CORE_1 / "
                "CORE_2_DOGFOOD_PENDING`. `Autopilot=OFF`.",
                "Status: `HISTORICAL_PASS_10_TASK_CORE_1 / "
                "CORE_2_DOGFOOD_PENDING`. `Autopilot=ON`.\n\n"
                "<!-- stale marker: Autopilot=OFF -->",
                1,
            ),
            "wrong_continuation": content.replace(
                "ejecutar un nuevo dogfood manual de diez tareas",
                "reutilizar el dogfood histórico",
                1,
            ).replace(
                "Prepara el dogfood manual local",
                "Reutiliza el dogfood histórico",
                1,
            ),
            "duplicate_status": content.replace(
                "Status: `HISTORICAL_PASS_10_TASK_CORE_1 / "
                "CORE_2_DOGFOOD_PENDING`. `Autopilot=OFF`.",
                "Status: `HISTORICAL_PASS_10_TASK_CORE_1 / "
                "CORE_2_DOGFOOD_PENDING`. `Autopilot=OFF`.\n"
                "Status: `HISTORICAL_PASS_10_TASK_CORE_1 / "
                "CORE_2_DOGFOOD_PENDING`. `Autopilot=ON`.",
                1,
            ),
            "duplicate_continuation": content.replace(
                "- **Para continuar:** ejecutar un nuevo dogfood manual de diez "
                "tareas ligado al digest final de `3.1.0-core.2` en una tarea "
                "separada.",
                "- **Para continuar:** ejecutar un nuevo dogfood manual de diez "
                "tareas ligado al digest final de `3.1.0-core.2` en una tarea "
                "separada.\n"
                "- **Para continuar:** ejecutar un segundo gate.",
                1,
            ),
            "authority_true": content.replace(
                "- **Autoridad:** `authorizes=false`",
                "- **Autoridad:** `authorizes=true`",
                1,
            ),
            "remote_effect_allowed": content.replace(
                "- **No hacer todavía:** instalar, adoptar externamente, commit, "
                "push, PR, merge, deploy, publicación o release.",
                "- **No hacer todavía:** nada; push permitido.",
                1,
            ),
        }
        for label, mutated in mutations.items():
            with self.subTest(label=label):
                case = CoreDocumentationTests(
                    methodName="test_manual_dogfood_scorecard_is_closed"
                )
                with patch(f"{__name__}.read", return_value=mutated):
                    with self.assertRaises(AssertionError):
                        case.test_manual_dogfood_scorecard_is_closed()

    def test_continuation_is_compact_and_non_authorizing(self) -> None:
        content = read(DOGFOOD)
        _, separator, continuation = content.rpartition("## Continuación")
        self.assertEqual(separator, "## Continuación")
        self.assertLessEqual(len(continuation.encode("utf-8")), 2_048)
        self.assertEqual(
            re.findall(r"(?m)^- \*\*([^*]+):\*\*", continuation),
            [
                "Escribe en",
                "Rol",
                "Para continuar",
                "Mensaje exacto",
                "Estado de partida",
                "No hacer todavía",
                "Autoridad",
            ],
        )
        self.assertIn("- **Autoridad:** `authorizes=false`", continuation)
        self.assertNotIn("authorizes=true", continuation)

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
