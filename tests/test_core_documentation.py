from __future__ import annotations

import ast
from hashlib import sha256
import inspect
import json
import os
import errno
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
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
NEW_PROJECT_PACK = ROOT / "templates" / "new-project"
NEW_PROJECT_RUNBOOK = (
    ROOT / "docs" / "engineering" / "23-new-project-audit-bootstrap.md"
)
ADOPTION_READINESS_SPEC = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-22-control-plane-adoption-readiness-v1-design.md"
)
ADOPTION_READINESS_PLAN = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-22-control-plane-adoption-readiness-v1.md"
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
    class NewProjectAuditReadinessTests(unittest.TestCase):
        PACK_PATHS = {
            "AGENTS.md",
            "README.md",
            ".codex/project-policy.toml",
            ".codex/resource-registry.toml",
        }
        AUTHORITY_PATHS = {
            "AGENTS.md",
            ".codex/project-policy.toml",
            ".codex/resource-registry.toml",
        }

        def _pack_files(self) -> dict[str, bytes]:
            return {
                path.relative_to(NEW_PROJECT_PACK).as_posix(): path.read_bytes()
                for path in NEW_PROJECT_PACK.rglob("*")
                if path.is_file()
            }

        def _run(
            self,
            *arguments: str,
            cwd: Path,
            timeout: float = 30.0,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[bytes]:
            process = subprocess.Popen(
                list(arguments),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
            selector = selectors.DefaultSelector()
            payload = bytearray()
            deadline = time.monotonic() + timeout
            try:
                self.assertIsNotNone(process.stdout)
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._terminate_process_group(process)
                        raise AssertionError("E_AUDIT_PROCESS_TIMEOUT")
                    events = selector.select(timeout=min(remaining, 0.05))
                    if not events:
                        continue
                    for key, _ in events:
                        chunk = os.read(key.fileobj.fileno(), _STREAM_CHUNK)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if len(payload) + len(chunk) > 256 * 1_024:
                            self._terminate_process_group(process)
                            raise AssertionError("E_AUDIT_PROCESS_OUTPUT_LIMIT")
                        payload.extend(chunk)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_process_group(process)
                    raise AssertionError("E_AUDIT_PROCESS_TIMEOUT")
                try:
                    return_code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired as error:
                    self._terminate_process_group(process)
                    raise AssertionError("E_AUDIT_PROCESS_TIMEOUT") from error
                return subprocess.CompletedProcess(
                    list(arguments), return_code, bytes(payload), None
                )
            except BaseException:
                self._terminate_process_group(process)
                raise
            finally:
                selector.close()
                if process.stdout is not None:
                    process.stdout.close()

        def _terminate_process_group(self, process: subprocess.Popen[bytes]) -> None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired as error:
                raise AssertionError("E_AUDIT_PROCESS_CLEANUP") from error

        def _closed_environment(self, home: Path) -> dict[str, str]:
            return {
                "HOME": str(home),
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "XDG_CONFIG_HOME": str(home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_REPLACE_OBJECTS": "1",
            }

        def _assert_audit_resources_ready(self, inventory: dict[str, object]) -> None:
            resources = {
                str(item["id"]): item
                for item in inventory["resources"]  # type: ignore[index]
            }
            for resource_id in (
                "instruction.project-agents",
                "document.project-readme",
            ):
                resource = resources[resource_id]
                self.assertTrue(resource["ready"], resource)
                self.assertNotIn("R_NOT_FOUND", resource["reason_codes"])

        def _authority_digest_environment(
            self, target: Path, task_envelope: Path
        ) -> dict[str, str]:
            return {
                "TARGET_REPO": str(target.resolve(strict=True)),
                "TARGET_AGENTS_SHA256": sha256(
                    (target / "AGENTS.md").read_bytes()
                ).hexdigest(),
                "TARGET_POLICY_SHA256": sha256(
                    (target / ".codex" / "project-policy.toml").read_bytes()
                ).hexdigest(),
                "TARGET_REGISTRY_SHA256": sha256(
                    (target / ".codex" / "resource-registry.toml").read_bytes()
                ).hexdigest(),
                "TASK_ENVELOPE": str(task_envelope.resolve(strict=True)),
                "TASK_ENVELOPE_SHA256": sha256(task_envelope.read_bytes()).hexdigest(),
            }

        def _task_envelope(self, root: Path, name: str = "task-envelope.json") -> Path:
            path = root / name
            path.write_text(json.dumps(self._task()), encoding="utf-8")
            return path

        def _object_store_snapshot(self, repo: Path) -> dict[str, tuple[int, str]]:
            git_directory = Path(
                self._git(repo, "rev-parse", "--absolute-git-dir").decode().strip()
            )
            objects = git_directory / "objects"
            snapshot: dict[str, tuple[int, str]] = {}
            for path in sorted(objects.rglob("*")):
                if path.is_dir():
                    continue
                metadata = path.lstat()
                self.assertTrue(stat.S_ISREG(metadata.st_mode))
                snapshot[path.relative_to(objects).as_posix()] = (
                    stat.S_IMODE(metadata.st_mode),
                    sha256(path.read_bytes()).hexdigest(),
                )
            return snapshot

        def _source_binding(self) -> str:
            runbook = read(NEW_PROJECT_RUNBOOK)
            section = runbook.split("<!-- BEGIN SOURCE_BINDING -->", 1)[1].split(
                "<!-- END SOURCE_BINDING -->", 1
            )[0]
            return section.split("```bash", 1)[1].split("```", 1)[0]

        def _embedded_python(self, shell_block: str) -> str:
            return shell_block.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]

        def _materialization_predicate(self, shell_block: str):
            tree = ast.parse(self._embedded_python(shell_block))
            selected = [
                node
                for node in tree.body
                if (
                    isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name)
                        and target.id == "UF_DATALESS"
                        for target in node.targets
                    )
                )
                or (
                    isinstance(node, ast.FunctionDef)
                    and node.name == "fully_materialized"
                )
            ]
            self.assertEqual(len(selected), 2)
            namespace: dict[str, object] = {}
            exec(
                compile(
                    ast.Module(body=selected, type_ignores=[]),
                    "<materialization-predicate>",
                    "exec",
                ),
                namespace,
            )
            return namespace["fully_materialized"]

        def _task_envelope_reader(self):
            tree = ast.parse(self._embedded_python(self._source_binding()))
            selected = [
                node
                for node in tree.body
                if (
                    isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name)
                        and target.id == "UF_DATALESS"
                        for target in node.targets
                    )
                )
                or (
                    isinstance(node, ast.FunctionDef)
                    and node.name
                    in {
                        "fully_materialized",
                        "safe",
                        "identity",
                        "task_envelope_bytes",
                    }
                )
            ]
            namespace: dict[str, object] = {
                "os": os,
                "stat": stat,
                "EUID": os.geteuid(),
                "MAX_TASK": 1024 * 1024,
            }
            exec(
                compile(
                    ast.Module(body=selected, type_ignores=[]),
                    "<task-envelope-reader>",
                    "exec",
                ),
                namespace,
            )
            return namespace["task_envelope_bytes"]

        def _customization_guard(self) -> str:
            runbook = read(NEW_PROJECT_RUNBOOK)
            begin = "<!-- BEGIN CUSTOMIZATION_GUARD -->"
            end = "<!-- END CUSTOMIZATION_GUARD -->"
            self.assertIn(begin, runbook)
            self.assertIn(end, runbook)
            section = runbook.split(begin, 1)[1].split(end, 1)[0]
            return section.split("```bash", 1)[1].split("```", 1)[0]

        def _customization_guard_script(self) -> str:
            return self._source_binding() + "\n" + self._customization_guard()

        def _happy_commands(self) -> tuple[str, ...]:
            runbook = read(NEW_PROJECT_RUNBOOK)
            section = runbook.split("<!-- BEGIN HAPPY_PATH -->", 1)[1].split(
                "<!-- END HAPPY_PATH -->", 1
            )[0]
            lines = tuple(
                line
                for line in section.splitlines()
                if line.startswith(
                    ('"$CONTROL_PLANE"', 'control_plane_audit "$CONTROL_PLANE"')
                )
            )
            self.assertEqual(len(lines), 5)
            return lines

        def _audit_command_script(self, command: str) -> str:
            return (
                self._source_binding()
                + "\n"
                + self._customization_guard()
                + "\n"
                + command
            )

        def _git_result(self, repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
            return self._run(
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
                "-C",
                str(repo),
                *arguments,
                cwd=repo,
                env=self._closed_environment(Path("/var/empty")),
            )

        def _git(self, repo: Path, *arguments: str) -> bytes:
            result = self._git_result(repo, *arguments)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            return result.stdout

        def _project_snapshot(self, repo: Path) -> dict[str, object]:
            root = repo.resolve(strict=True)
            files: dict[str, dict[str, object]] = {}
            stack = [root]
            while stack:
                directory = stack.pop()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        relative = Path(entry.path).relative_to(root)
                        if relative.parts == (".git",):
                            continue
                        metadata = entry.stat(follow_symlinks=False)
                        key = relative.as_posix()
                        mode = stat.S_IMODE(metadata.st_mode)
                        if stat.S_ISLNK(metadata.st_mode):
                            target = os.fsencode(os.readlink(entry.path))
                            self.assertEqual(
                                entry.stat(follow_symlinks=False), metadata
                            )
                            files[key] = {
                                "type": "symlink",
                                "mode": mode,
                                "target_sha256": sha256(target).hexdigest(),
                            }
                        elif stat.S_ISDIR(metadata.st_mode):
                            files[key] = {"type": "directory", "mode": mode}
                            stack.append(Path(entry.path))
                        elif stat.S_ISREG(metadata.st_mode):
                            descriptor = os.open(
                                entry.path,
                                os.O_RDONLY
                                | getattr(os, "O_NOFOLLOW", 0)
                                | getattr(os, "O_CLOEXEC", 0),
                            )
                            try:
                                opened = os.fstat(descriptor)
                                with os.fdopen(
                                    descriptor, "rb", closefd=False
                                ) as stream:
                                    payload = stream.read(_MAX_DOCUMENT_BYTES + 1)
                                after_open = os.fstat(descriptor)
                            finally:
                                os.close(descriptor)
                            self.assertEqual(opened, metadata)
                            self.assertEqual(after_open, metadata)
                            self.assertLessEqual(len(payload), _MAX_DOCUMENT_BYTES)
                            files[key] = {
                                "type": "regular",
                                "mode": mode,
                                "sha256": sha256(payload).hexdigest(),
                            }
                        else:
                            self.fail(f"E_AUDIT_TARGET_SPECIAL: {key}")
            return {
                "head": self._git(repo, "rev-parse", "HEAD"),
                "branch": self._git(repo, "branch", "--show-current"),
                "status": self._git(
                    repo, "status", "--porcelain=v1", "--untracked-files=all"
                ),
                "index": self._git_index_snapshot(repo),
                "files": files,
            }

        def _git_index_snapshot(self, repo: Path) -> dict[str, object]:
            git_directory = Path(
                self._git(repo, "rev-parse", "--absolute-git-dir").decode().strip()
            )
            index_path = Path(
                self._git(
                    repo,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "index",
                ).decode().strip()
            )
            self.assertEqual(index_path.parent.resolve(strict=True), git_directory.resolve(strict=True))
            metadata = index_path.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertFalse(index_path.is_symlink())
            return {
                "mode": stat.S_IMODE(metadata.st_mode),
                "sha256": sha256(index_path.read_bytes()).hexdigest(),
            }

        def _customized_authority(self) -> dict[str, bytes]:
            replacements = {
                b"__PROJECT_NAME__": b"audit-fixture-project",
            }
            customized: dict[str, bytes] = {}
            for relative, payload in self._pack_files().items():
                if relative not in self.AUTHORITY_PATHS:
                    continue
                for old, new in replacements.items():
                    payload = payload.replace(old, new)
                self.assertNotIn(b"__", payload)
                customized[relative] = payload
            return customized

        def _initialize_target(self, root: Path, *, customized: bool = True) -> Path:
            origin = root / "origin.git"
            self._git(root, "init", "-q", "--bare", str(origin))
            target = root / "consumer"
            target.mkdir()
            self._git(target, "init", "-q", "-b", "main")
            self._git(target, "config", "user.name", "Audit Fixture")
            self._git(target, "config", "user.email", "audit@example.invalid")
            consumer_readme = b"# Consumer-owned project\n\nKeep this content.\n"
            (target / "README.md").write_bytes(consumer_readme)
            self._git(target, "add", "README.md")
            self._git(target, "commit", "-qm", "consumer baseline")

            authority = (
                self._customized_authority()
                if customized
                else {
                    relative: payload
                    for relative, payload in self._pack_files().items()
                    if relative in self.AUTHORITY_PATHS
                }
            )
            for relative, payload in authority.items():
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            self.assertEqual((target / "README.md").read_bytes(), consumer_readme)
            self._git(target, "add", "AGENTS.md", ".codex")
            self._git(target, "commit", "-qm", "target-specific governance")
            self._git(target, "remote", "add", "origin", str(origin))
            self._git(target, "push", "-qu", "origin", "main")
            return target

        def _initialize_source(self, root: Path) -> tuple[Path, str]:
            source = root.resolve(strict=True) / "source"
            integrated_sha = self._git(ROOT, "rev-parse", "HEAD").decode().strip()
            clone = self._run(
                "/usr/bin/git",
                "--no-pager",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "clone",
                "-q",
                "--no-checkout",
                str(ROOT),
                str(source),
                cwd=root,
                env=self._closed_environment(Path("/var/empty")),
                timeout=60.0,
            )
            self.assertEqual(clone.returncode, 0, clone.stdout.decode(errors="replace"))
            self._git(source, "checkout", "-q", "--detach", integrated_sha)
            self.assertEqual(
                self._git(source, "rev-parse", "HEAD").decode().strip(),
                integrated_sha,
            )
            self.assertEqual(
                self._git(
                    source, "status", "--porcelain=v1", "--untracked-files=all"
                ),
                b"",
            )
            detached = self._git_result(source, "symbolic-ref", "-q", "HEAD")
            self.assertEqual(detached.returncode, 1, detached.stdout)
            return source, integrated_sha

        def _task(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "task_id": "TASK-NEXT-PROJECT-AUDIT-001",
                "objective": "Audit a structured multi-file change without mutation.",
                "intent": "audit",
                "phase": "research",
                "requested_outcome": "answer",
                "goals": [
                    {
                        "id": "goal-audit",
                        "summary": "Produce bounded local audit evidence.",
                        "domains": ["project"],
                        "depends_on": [],
                    }
                ],
                "domains": ["project"],
                "signals": ["multi_file"],
                "scope_paths": ["AGENTS.md", ".codex/", "README.md"],
                "risk": {
                    "uncertainty": 2,
                    "blast_radius": 1,
                    "irreversibility": 1,
                    "verification_complexity": 2,
                },
                "effects": [{"name": "local_read", "source": "user_explicit"}],
                "explicit_resources": [],
                "excluded_resources": [],
            }

        def test_source_pack_is_exact_minimal_and_schema_valid(self) -> None:
            pack = self._pack_files()
            self.assertEqual(set(pack), self.PACK_PATHS)
            self.assertNotEqual(pack["README.md"], b"")
            policy = tomllib.loads(pack[".codex/project-policy.toml"].decode())
            registry = tomllib.loads(pack[".codex/resource-registry.toml"].decode())
            self.assertEqual(policy["project_name"], "__PROJECT_NAME__")
            self.assertEqual(policy["project_kind"], "generic")
            self.assertEqual(registry["registry_id"], "__PROJECT_NAME__")
            resources = {resource["id"]: resource for resource in registry["resources"]}
            self.assertEqual(
                set(resources),
                {
                    "instruction.project-agents",
                    "document.project-readme",
                    "gate.targeted-validation",
                    "gate.diff-review",
                    "gate.relevant-tests",
                    "gate.pull-request",
                    "gate.written-plan",
                    "gate.independent-review",
                    "gate.security-review",
                    "gate.rollback-plan",
                },
            )
            self.assertEqual(resources["instruction.project-agents"]["selection"], "required")
            self.assertEqual(resources["document.project-readme"]["selection"], "available")
            self.assertTrue(
                all(
                    resource["locator"].startswith(("repo://", "builtin://"))
                    for resource in resources.values()
                )
            )
            self.assertEqual(
                registry["routes"][0]["recommended_resources"],
                ["document.project-readme"],
            )
            serialized = "\n".join(payload.decode() for payload in pack.values())
            for forbidden in (
                "control-plane.lock",
                "hooks.json",
                "CoreTaskState",
                "receipt",
                "mcp://github",
                "release-provider",
                "user-skill://",
            ):
                self.assertNotIn(forbidden, serialized)

        def test_registry_recommends_readme_only_for_first_use_audit_research(self) -> None:
            registry = tomllib.loads(
                (NEW_PROJECT_PACK / ".codex/resource-registry.toml").read_text()
            )
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, _ = self._initialize_source(root)
                target = self._initialize_target(root)
                environment = self._closed_environment(root / "closed-home")
                launcher = source / "scripts" / "control-plane"
                policy = target / ".codex" / "project-policy.toml"
                registry_path = target / ".codex" / "resource-registry.toml"
                cases = (
                    ("audit", "frame"),
                    ("audit", "observe"),
                    ("explain", "research"),
                    ("release", "release"),
                    ("plan", "plan"),
                    ("implement", "implement"),
                )
                for index, (intent, phase) in enumerate(cases):
                    with self.subTest(intent=intent, phase=phase):
                        task = json.loads(json.dumps(self._task()))
                        task["task_id"] = f"TASK-UNSUPPORTED-{index}"
                        task["intent"] = intent
                        task["phase"] = phase
                        task_path = root / f"task-{index}.json"
                        task_path.write_text(json.dumps(task), encoding="utf-8")
                        result = self._run(
                            str(launcher),
                            "route",
                            "--repo",
                            str(target),
                            "--task",
                            str(task_path),
                            "--policy",
                            str(policy),
                            "--registry",
                            str(registry_path),
                            "--mode",
                            "audit",
                            "--json",
                            cwd=target,
                            env=environment,
                        )
                        self.assertEqual(result.returncode, 0, result.stdout)
                        payload = json.loads(result.stdout)
                        self.assertEqual(payload["matched_routes"], [])
                        self.assertNotIn(
                            "document.project-readme",
                            payload["summary"]["recommended"],
                        )
            route = registry["routes"][0]
            self.assertEqual(route["tiers"], ["T2"])
            self.assertEqual(route["phases"], ["research"])
            self.assertEqual(route["intents"], ["audit"])
            self.assertNotIn("T2/T3", (NEW_PROJECT_PACK / "README.md").read_text())

        def test_template_authority_is_restrictive_and_non_authorizing(self) -> None:
            agents = (NEW_PROJECT_PACK / "AGENTS.md").read_text(encoding="utf-8")
            for token in (
                "evidence",
                "TDD",
                "scope",
                "protected base",
                "authorizes=false",
                "commit",
                "push",
                "Pull Request",
                "merge",
                "deploy",
                "release",
                "dependencies",
                "CI",
                "secrets",
            ):
                self.assertIn(token, agents)
            self.assertIn("exact target-specific authorization", agents)
            self.assertNotIn("authorized permanently", agents)
            self.assertNotIn("standing Git authority", agents)

        def test_runbook_and_plan_keep_target_mutation_deferred(self) -> None:
            runbook = read(NEW_PROJECT_RUNBOOK)
            spec = read(ADOPTION_READINESS_SPEC)
            plan = read(ADOPTION_READINESS_PLAN)
            self.assertLessEqual(len(runbook.splitlines()), 350)
            for token in (
                "AUDIT_ONLY",
                "DEFERRED_TARGET_BOOTSTRAP",
                "authorizes=false",
                "clean detached source",
                "exact integrated SHA",
                "outside the target",
                "consumer README",
            ):
                self.assertIn(token, runbook)
            self.assertLess(
                runbook.index("<!-- END CUSTOMIZATION_GUARD -->"),
                runbook.index("<!-- BEGIN HAPPY_PATH -->"),
            )
            for variable in (
                "TARGET_AGENTS_SHA256",
                "TARGET_POLICY_SHA256",
                "TARGET_REGISTRY_SHA256",
                "TASK_ENVELOPE_SHA256",
            ):
                self.assertIn(variable, runbook)
            happy = "\n".join(self._happy_commands())
            for command in (
                "policy-check",
                "registry-check",
                "inventory",
                "preflight --mode read",
                "route",
            ):
                self.assertIn(command, happy)
            for command in ("doctor", "survey"):
                self.assertNotIn(command, happy)
                self.assertIn(command, runbook)
            fenced = "\n".join(re.findall(r"```(?:bash|sh)\n(.*?)```", runbook, re.DOTALL))
            launcher_lines = tuple(
                line.strip()
                for line in fenced.splitlines()
                if any(
                    f" {command} " in f" {line} "
                    for command in (
                        "policy-check",
                        "registry-check",
                        "inventory",
                        "preflight",
                        "route",
                        "doctor",
                        "survey",
                    )
                )
                and "$CONTROL_PLANE" in line
            )
            self.assertEqual(len(launcher_lines), 7)
            self.assertTrue(
                all(
                    line.startswith('control_plane_audit "$CONTROL_PLANE"')
                    for line in launcher_lines
                ),
                launcher_lines,
            )
            self.assertFalse(
                any(
                    re.match(r'^\s*"\$CONTROL_PLANE"\s', line)
                    for line in fenced.splitlines()
                )
            )
            self.assertEqual(runbook.count("control_plane_audit() {"), 1)
            for mutation in (
                "git switch",
                "git add",
                "git commit",
                "git push",
                "mkdir ",
                "cp ",
                "rsync",
                "control-plane-adoption",
            ):
                self.assertNotIn(mutation, fenced)
            for document in (spec, plan):
                self.assertIn("AUDIT_ONLY", document)
                self.assertIn("DEFERRED_TARGET_BOOTSTRAP", document)
                self.assertIn("reusable mutator ADR", document)
                self.assertNotIn("Survey V2", document)
                self.assertNotIn("Adoption preview", document)
            self.assertEqual(plan.count("## Unit "), 4)

        def test_materialization_contract_is_lstat_first_and_fail_closed(self) -> None:
            source = self._source_binding()
            fully_materialized = self._materialization_predicate(source)
            self.assertTrue(fully_materialized(SimpleNamespace(st_flags=0)))
            self.assertFalse(
                fully_materialized(SimpleNamespace(st_flags=0x40000000))
            )
            self.assertIn("E_AUDIT_STABLE_PAUSE_MATERIALIZATION", source)
            self.assertIn(
                "verify_control_plane_target_and_task", self._customization_guard()
            )

            self.assertLess(
                source.index("source_metadata_preflight(source)"),
                source.index("physical = source.resolve"),
            )
            self.assertLess(
                source.index("source_metadata_preflight(source)"),
                source.index("_, raw_gitdir = git("),
            )
            self.assertLess(
                source.index("target_metadata_preflight(literal)"),
                source.index("physical = literal.resolve"),
            )
            self.assertLess(
                source.index("task_metadata_preflight(task_path)"),
                source.index("task_envelope_bytes(task_path)"),
            )
            dataless = SimpleNamespace(st_flags=0x40000000)

            class MetadataOnlyPath:
                def lstat(self) -> SimpleNamespace:
                    return dataless

            task_envelope_reader = self._task_envelope_reader()
            with patch.object(
                os, "open", side_effect=AssertionError("content open reached")
            ) as opened:
                with self.assertRaisesRegex(
                    SystemExit, "E_AUDIT_STABLE_PAUSE_MATERIALIZATION"
                ):
                    task_envelope_reader(MetadataOnlyPath())
                opened.assert_not_called()
            runbook = read(NEW_PROJECT_RUNBOOK)
            self.assertIn("FULLY_MATERIALIZED_LOCAL_ONLY", runbook)
            self.assertIn("File Provider", runbook)
            self.assertIn("before the audit", runbook)

        def test_source_and_target_permissions_fail_closed(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                source_environment = self._closed_environment(root / "closed-home")
                source_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                scripts = source / "scripts"
                scripts_mode = stat.S_IMODE(scripts.stat().st_mode)
                scripts.chmod(0o777)
                unsafe_source = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=source_environment,
                )
                self.assertNotEqual(unsafe_source.returncode, 0, unsafe_source.stdout)
                scripts.chmod(scripts_mode)

                scenario = root / "target-scenario"
                scenario.mkdir()
                target = self._initialize_target(scenario)
                task_path = self._task_envelope(root, "permission-task.json")
                target_environment = self._closed_environment(root / "target-home")
                target_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                target_environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                guard = self._customization_guard_script()

                agents = target / "AGENTS.md"
                agents_mode = stat.S_IMODE(agents.stat().st_mode)
                agents.chmod(0o666)
                unsafe_agents = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=target_environment
                )
                self.assertNotEqual(unsafe_agents.returncode, 0, unsafe_agents.stdout)
                agents.chmod(agents_mode)

                target_mode = stat.S_IMODE(target.stat().st_mode)
                target.chmod(0o777)
                unsafe_root = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=target_environment
                )
                self.assertNotEqual(unsafe_root.returncode, 0, unsafe_root.stdout)
                target.chmod(target_mode)

        def test_writable_source_target_task_and_git_metadata_ancestors_stop(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                source_parent = root / "source-parent"
                source_parent.mkdir()
                source, integrated_sha = self._initialize_source(source_parent)
                source_environment = self._closed_environment(root / "source-home")
                source_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                source_parent.chmod(0o777)
                unsafe_source_parent = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=source_environment,
                )
                self.assertNotEqual(
                    unsafe_source_parent.returncode, 0, unsafe_source_parent.stdout
                )
                source_parent.chmod(0o1777)
                sticky_source_parent = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=source_environment,
                )
                self.assertEqual(
                    sticky_source_parent.returncode,
                    0,
                    sticky_source_parent.stdout,
                )
                source_parent.chmod(0o755)

                target_parent = root / "target-parent"
                target_parent.mkdir()
                target = self._initialize_target(target_parent)
                task_parent = root / "task-parent"
                task_parent.mkdir()
                task_path = self._task_envelope(task_parent)
                target_environment = self._closed_environment(root / "target-home")
                target_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                target_environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                target_parent.chmod(0o777)
                unsafe_target_parent = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target,
                    env=target_environment,
                )
                self.assertNotEqual(
                    unsafe_target_parent.returncode, 0, unsafe_target_parent.stdout
                )
                target_parent.chmod(0o755)

                task_parent.chmod(0o777)
                unsafe_task_parent = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target,
                    env=target_environment,
                )
                self.assertNotEqual(
                    unsafe_task_parent.returncode, 0, unsafe_task_parent.stdout
                )
                task_parent.chmod(0o755)

                target_worktree = root / "safe-target-worktree"
                self._git(
                    target,
                    "worktree",
                    "add",
                    "-q",
                    "--detach",
                    str(target_worktree),
                )
                target_gitdir = Path(
                    self._git(
                        target_worktree, "rev-parse", "--absolute-git-dir"
                    )
                    .decode()
                    .strip()
                )
                self.assertTrue((target_gitdir / "commondir").is_file())
                linked_target_environment = self._closed_environment(
                    root / "linked-target-home"
                )
                linked_target_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                linked_target_environment.update(
                    self._authority_digest_environment(target_worktree, task_path)
                )
                valid_target_common_directory = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target_worktree,
                    env=linked_target_environment,
                )
                self.assertEqual(
                    valid_target_common_directory.returncode,
                    0,
                    valid_target_common_directory.stdout,
                )
                worktree_config = target_gitdir / "config.worktree"
                worktree_config.write_text(
                    '[includeIf "gitdir:/"]\n'
                    f"\tpath = {root / 'outside-worktree.config'}\n",
                    encoding="utf-8",
                )
                rejected_worktree_config = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target_worktree,
                    env=linked_target_environment,
                )
                self.assertNotEqual(
                    rejected_worktree_config.returncode,
                    0,
                    rejected_worktree_config.stdout,
                )
                worktree_config.unlink()
                restored_target_common_directory = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target_worktree,
                    env=linked_target_environment,
                )
                self.assertEqual(
                    restored_target_common_directory.returncode,
                    0,
                    restored_target_common_directory.stdout,
                )

                worktree = root / "safe-worktree"
                self._git(
                    source,
                    "worktree",
                    "add",
                    "-q",
                    "--detach",
                    str(worktree),
                    integrated_sha,
                )
                source_mode = stat.S_IMODE(source.stat().st_mode)
                git_environment = self._closed_environment(root / "git-home")
                git_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(worktree),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                valid_common_directory = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=git_environment,
                )
                self.assertEqual(
                    valid_common_directory.returncode,
                    0,
                    valid_common_directory.stdout,
                )
                source.chmod(0o777)
                unsafe_git_parent = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=git_environment,
                )
                self.assertNotEqual(
                    unsafe_git_parent.returncode, 0, unsafe_git_parent.stdout
                )
                source.chmod(source_mode)

                source_binding = self._source_binding()
                self.assertIn("stat.S_ISVTX", source_binding)
                self.assertIn("child_item.st_uid==EUID", source_binding)
                tree = ast.parse(self._embedded_python(source_binding))
                chain_node = next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "chain_identity"
                )
                namespace: dict[str, object] = {}
                exec(
                    compile(
                        ast.Module(body=[chain_node], type_ignores=[]),
                        "<chain-identity>",
                        "exec",
                    ),
                    namespace,
                )
                chain_identity = namespace["chain_identity"]
                ancestor = SimpleNamespace(
                    st_dev=1,
                    st_ino=2,
                    st_mode=stat.S_IFDIR | 0o755,
                    st_nlink=3,
                    st_uid=os.geteuid(),
                    st_gid=20,
                    st_size=96,
                    st_mtime_ns=1,
                    st_ctime_ns=1,
                )
                churned = SimpleNamespace(**{**vars(ancestor), "st_mtime_ns": 2})
                writable = SimpleNamespace(
                    **{**vars(ancestor), "st_mode": stat.S_IFDIR | 0o777}
                )
                self.assertEqual(chain_identity(ancestor), chain_identity(churned))
                self.assertNotEqual(chain_identity(ancestor), chain_identity(writable))
                sticky_node = next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "sticky_protects"
                )
                sticky_namespace: dict[str, object] = {
                    "stat": stat,
                    "EUID": os.geteuid(),
                }
                exec(
                    compile(
                        ast.Module(body=[sticky_node], type_ignores=[]),
                        "<sticky-protection>",
                        "exec",
                    ),
                    sticky_namespace,
                )
                sticky_protects = sticky_namespace["sticky_protects"]
                owned_sticky = SimpleNamespace(
                    st_mode=stat.S_IFDIR | 0o1777, st_uid=os.geteuid()
                )
                foreign_sticky = SimpleNamespace(
                    st_mode=stat.S_IFDIR | 0o1777, st_uid=os.geteuid() + 1
                )
                owned_child = SimpleNamespace(st_uid=os.geteuid())
                foreign_child = SimpleNamespace(st_uid=os.geteuid() + 1)
                self.assertTrue(sticky_protects(owned_sticky, owned_child))
                self.assertFalse(sticky_protects(foreign_sticky, owned_child))
                self.assertFalse(sticky_protects(owned_sticky, foreign_child))

        def test_each_happy_command_revalidates_the_exact_source(self) -> None:
            binding = self._source_binding()
            self.assertTrue(
                all(
                    command.startswith('control_plane_audit "$CONTROL_PLANE"')
                    for command in self._happy_commands()
                )
            )
            marker = "\nverify_control_plane_source\n"
            self.assertEqual(binding.count(marker), 1)
            self.assertIn("verify_control_plane_source()", binding)
            self.assertIn("control_plane_audit()", binding)
            self.assertIn("verify_control_plane_audit_context()", binding)
            wrapper = binding.split("control_plane_audit() {", 1)[1].split("}", 1)[0]
            self.assertEqual(wrapper.count("verify_control_plane_audit_context"), 2)
            self.assertLess(
                wrapper.index("verify_control_plane_audit_context"),
                wrapper.index('if "$@"'),
            )
            self.assertLess(
                wrapper.index('if "$@"'),
                wrapper.rindex("verify_control_plane_audit_context"),
            )

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                source = root / "source"
                source.mkdir()
                self._git(source, "init", "-q", "-b", "main")
                self._git(source, "config", "user.name", "Audit Fixture")
                self._git(source, "config", "user.email", "audit@example.invalid")
                sentinel = root / "launcher-called"
                launcher = source / "scripts" / "control-plane"
                launcher.parent.mkdir()
                launcher.write_text(
                    "#!/bin/sh\n"
                    f"/usr/bin/touch {str(sentinel)!r}\n"
                    'if [ -n "${AUDIT_MUTATE_PATH-}" ]; then '\
                    "printf '\\n# post-launch drift\\n' >> \"$AUDIT_MUTATE_PATH\"; fi\n"
                    "printf '{\\\"ok\\\":true}\\n'\n",
                    encoding="utf-8",
                )
                launcher.chmod(0o755)
                (source / "README.md").write_text("A\n", encoding="utf-8")
                self._git(source, "add", ".")
                self._git(source, "commit", "-qm", "source A")
                sha_a = self._git(source, "rev-parse", "HEAD").decode().strip()
                (source / "README.md").write_text("B\n", encoding="utf-8")
                self._git(source, "add", "README.md")
                self._git(source, "commit", "-qm", "source B")
                sha_b = self._git(source, "rev-parse", "HEAD").decode().strip()
                self._git(source, "checkout", "-q", "--detach", sha_a)
                scenario = root / "target-scenario"
                scenario.mkdir()
                target = self._initialize_target(scenario)
                task_path = self._task_envelope(root)
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE": str(launcher),
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": sha_a,
                    }
                )
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                invocation = self._audit_command_script(
                    'control_plane_audit "$CONTROL_PLANE"'
                )
                accepted = self._run(
                    "/bin/sh", "-eu", "-c", invocation, cwd=root, env=environment
                )
                self.assertEqual(accepted.returncode, 0, accepted.stdout)
                self.assertTrue(sentinel.exists())
                sentinel.unlink()

                for mutable in (
                    source / "README.md",
                    target / "AGENTS.md",
                    task_path,
                ):
                    original = mutable.read_bytes()
                    mutation_environment = dict(environment)
                    mutation_environment["AUDIT_MUTATE_PATH"] = str(mutable)
                    rejected_after_launcher = self._run(
                        "/bin/sh",
                        "-eu",
                        "-c",
                        invocation,
                        cwd=root,
                        env=mutation_environment,
                    )
                    self.assertNotEqual(
                        rejected_after_launcher.returncode,
                        0,
                        rejected_after_launcher.stdout,
                    )
                    self.assertTrue(sentinel.exists())
                    sentinel.unlink()
                    mutable.write_bytes(original)

                self._git(source, "checkout", "-q", "--detach", sha_b)
                rejected = self._run(
                    "/bin/sh", "-eu", "-c", invocation, cwd=root, env=environment
                )
                self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
                self.assertFalse(sentinel.exists())

                self._git(source, "checkout", "-q", "--detach", sha_a)
                task_path.write_bytes(task_path.read_bytes() + b"\n")
                changed_task = self._run(
                    "/bin/sh", "-eu", "-c", invocation, cwd=root, env=environment
                )
                self.assertNotEqual(changed_task.returncode, 0, changed_task.stdout)
                self.assertFalse(sentinel.exists())

        def test_five_source_commands_are_read_only_and_resource_ready(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                target = self._initialize_target(root)
                controlled_home = root / "closed-home"
                controlled_home.mkdir()
                environment = self._closed_environment(controlled_home)
                task_path = self._task_envelope(root)
                launcher = source / "scripts" / "control-plane"
                policy = target / ".codex" / "project-policy.toml"
                registry = target / ".codex" / "resource-registry.toml"
                before = self._project_snapshot(target)
                environment.update(
                    {
                        "CONTROL_PLANE": str(launcher),
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                guarded = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target,
                    env=environment,
                )
                self.assertEqual(guarded.returncode, 0, guarded.stdout)
                payloads = []
                for command in self._happy_commands():
                    result = self._run(
                        "/bin/sh",
                        "-eu",
                        "-c",
                        self._audit_command_script(command),
                        cwd=target,
                        env=environment,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stdout.decode(errors="replace"),
                    )
                    payloads.append(json.loads(result.stdout))
                self.assertEqual(self._project_snapshot(target), before)
                self._assert_audit_resources_ready(payloads[2])
                preflight_payload = payloads[3]
                self.assertEqual(
                    {check["code"] for check in preflight_payload["checks"]},
                    {
                        "GIT_REPOSITORY",
                        "GIT_COMMITTED_HEAD",
                        "GIT_ATTACHED_BRANCH",
                        "GIT_CLEAN_TREE",
                        "GIT_REMOTE_PRESENT",
                        "GIT_REMOTE_BASE_PRESENT",
                        "GIT_BASE_CONTAINED",
                        "GIT_STATE_MATERIALIZED",
                    },
                )
                self.assertTrue(
                    all(check["ok"] for check in preflight_payload["checks"]),
                    preflight_payload,
                )
                self.assertEqual(
                    {
                        key: preflight_payload["facts"][key]
                        for key in (
                            "remote_present",
                            "remote_base_present",
                            "git_state_materialized",
                        )
                    },
                    {
                        "remote_present": True,
                        "remote_base_present": True,
                        "git_state_materialized": True,
                    },
                )
                self.assertEqual(
                    {
                        key: preflight_payload["facts"][key]
                        for key in ("dirty", "detached", "unborn")
                    },
                    {"dirty": False, "detached": False, "unborn": False},
                )
                self.assertEqual(preflight_payload["facts"]["ahead"], 0)
                self.assertEqual(preflight_payload["facts"]["behind"], 0)
                route = payloads[-1]
                self.assertEqual(route["summary"]["tier"], "T2")
                self.assertTrue(route["decision_ready"], route)
                self.assertFalse(route["authorizes"])
                self.assertIn("instruction.project-agents", route["summary"]["required"])
                self.assertIn("document.project-readme", route["summary"]["recommended"])
                preflight = next(
                    line for line in self._happy_commands() if " preflight " in line
                )
                unsafe = preflight.replace("--offline", "--refresh")
                refreshed = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._audit_command_script(unsafe),
                    cwd=target,
                    env=environment,
                )
                self.assertNotEqual(refreshed.returncode, 0, refreshed.stdout)

        def test_missing_project_resources_stop_even_when_route_exits_zero(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                controlled_home = root / "closed-home"
                controlled_home.mkdir()
                environment = self._closed_environment(controlled_home)
                launcher = source / "scripts" / "control-plane"
                for missing in ("AGENTS.md", "README.md"):
                    with self.subTest(missing=missing):
                        scenario = root / missing.replace(".", "-")
                        scenario.mkdir()
                        target = self._initialize_target(scenario)
                        (target / missing).unlink()
                        policy = target / ".codex" / "project-policy.toml"
                        registry = target / ".codex" / "resource-registry.toml"
                        task_path = root / f"task-{missing}.json"
                        task_path.write_text(json.dumps(self._task()), encoding="utf-8")
                        inventory_result = self._run(
                            str(launcher),
                            "inventory",
                            "--repo",
                            str(target),
                            "--registry",
                            str(registry),
                            "--json",
                            cwd=target,
                            env=environment,
                        )
                        self.assertEqual(inventory_result.returncode, 0)
                        inventory = json.loads(inventory_result.stdout)
                        missing_id = (
                            "instruction.project-agents"
                            if missing == "AGENTS.md"
                            else "document.project-readme"
                        )
                        resource = next(
                            item
                            for item in inventory["resources"]
                            if item["id"] == missing_id
                        )
                        self.assertFalse(resource["ready"])
                        self.assertIn("R_NOT_FOUND", resource["reason_codes"])
                        with self.assertRaises(AssertionError):
                            self._assert_audit_resources_ready(inventory)
                        route_result = self._run(
                            str(launcher),
                            "route",
                            "--repo",
                            str(target),
                            "--task",
                            str(task_path),
                            "--policy",
                            str(policy),
                            "--registry",
                            str(registry),
                            "--mode",
                            "audit",
                            "--json",
                            cwd=target,
                            env=environment,
                        )
                        self.assertEqual(route_result.returncode, 0)
                        route = json.loads(route_result.stdout)
                        self.assertFalse(route["authorizes"])
                        if missing == "README.md":
                            self.assertTrue(route["decision_ready"], route)

        def test_snapshot_detects_index_flags_that_status_hides(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                target = self._initialize_target(Path(directory))
                baseline = self._project_snapshot(target)
                for enable, disable in (
                    ("--assume-unchanged", "--no-assume-unchanged"),
                    ("--skip-worktree", "--no-skip-worktree"),
                ):
                    with self.subTest(flag=enable):
                        self._git(target, "update-index", enable, "README.md")
                        changed = self._project_snapshot(target)
                        self.assertEqual(changed["status"], baseline["status"])
                        self.assertNotEqual(changed["index"], baseline["index"])
                        self._git(target, "update-index", disable, "README.md")
                        restored = self._project_snapshot(target)
                        self.assertEqual(restored["status"], baseline["status"])
                        baseline = restored

        def test_bounded_runner_reaps_overflow_and_timeout_process_groups(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaisesRegex(
                    AssertionError, "E_AUDIT_PROCESS_OUTPUT_LIMIT"
                ):
                    self._run(
                        sys.executable,
                        "-c",
                        "import os; os.write(1, b'x' * (256 * 1024 + 1))",
                        cwd=root,
                    )

                pid_path = root / "child.pid"
                program = (
                    "import pathlib,subprocess,sys,time;"
                    "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid));"
                    "time.sleep(60)"
                )
                with self.assertRaisesRegex(
                    AssertionError, "E_AUDIT_PROCESS_TIMEOUT"
                ):
                    self._run(sys.executable, "-c", program, cwd=root, timeout=1.0)
                child_pid = int(pid_path.read_text())
                child_gone = False
                for _ in range(50):
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        child_gone = True
                        break
                    time.sleep(0.02)
                self.assertTrue(child_gone, f"child process {child_pid} leaked")

        def test_source_binding_is_raw_tree_bound_and_rejects_index_bypasses(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                binding = self._source_binding()
                self.assertIn("observed_paths = set()", binding)
                self.assertIn("if observed_paths != set(expected): fail()", binding)
                clean = self._run("/bin/sh", "-c", binding, cwd=root, env=environment)
                self.assertEqual(clean.returncode, 0, clean.stdout)

                readme = source / "README.md"
                readme_bytes = readme.read_bytes()
                readme.write_bytes(readme_bytes + b"\n# staged alternate\n")
                self._git(source, "add", "README.md")
                readme.write_bytes(readme_bytes)
                staged_only = self._run(
                    "/bin/sh", "-c", binding, cwd=root, env=environment
                )
                self.assertNotEqual(staged_only.returncode, 0, staged_only.stdout)
                self._git(
                    source,
                    "restore",
                    "--staged",
                    "--source=HEAD",
                    "README.md",
                )
                restored_clean = self._run(
                    "/bin/sh", "-c", binding, cwd=root, env=environment
                )
                self.assertEqual(
                    restored_clean.returncode, 0, restored_clean.stdout
                )

                self._git(source, "switch", "-q", "-c", "test-attached")
                rejected = self._run("/bin/sh", "-c", binding, cwd=root, env=environment)
                self.assertNotEqual(rejected.returncode, 0)
                self._git(source, "switch", "-q", "--detach", integrated_sha)

                launcher = source / "scripts" / "control-plane"
                original = launcher.read_bytes()
                self._git(source, "update-index", "--skip-worktree", "scripts/control-plane")
                flag_only = self._run(
                    "/bin/sh", "-c", binding, cwd=root, env=environment
                )
                self.assertNotEqual(flag_only.returncode, 0, flag_only.stdout)
                launcher.write_bytes(original + b"\n# hidden drift\n")
                hidden = self._run("/bin/sh", "-c", binding, cwd=root, env=environment)
                self.assertNotEqual(hidden.returncode, 0)
                launcher.write_bytes(original)
                self._git(source, "update-index", "--no-skip-worktree", "scripts/control-plane")

                redirect = root / "redirect"
                redirect.mkdir()
                self._git(source, "config", "core.worktree", str(redirect))
                launcher.write_bytes(original + b"\n# redirected drift\n")
                redirected = self._run("/bin/sh", "-c", binding, cwd=root, env=environment)
                self.assertNotEqual(redirected.returncode, 0)
                launcher.write_bytes(original)
                self._git(source, "config", "--unset", "core.worktree")

                ignored = source / "control_plane" / "__pycache__"
                ignored.mkdir()
                (ignored / "hidden.pyc").write_bytes(b"ignored-extra")
                extra = self._run("/bin/sh", "-c", binding, cwd=root, env=environment)
                self.assertNotEqual(extra.returncode, 0)
                (ignored / "hidden.pyc").unlink()
                ignored.rmdir()

                readme.unlink()
                missing = self._run("/bin/sh", "-c", binding, cwd=root, env=environment)
                self.assertNotEqual(missing.returncode, 0)
                readme.write_bytes(readme_bytes)

                head_path = source / ".git" / "HEAD"
                head_path.write_text("broken\n", encoding="utf-8")
                error = self._run("/bin/sh", "-c", binding, cwd=root, env=environment)
                self.assertNotEqual(error.returncode, 0)

        def test_target_cleanliness_rejects_hidden_status_and_index_flags(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                target = self._initialize_target(root)
                task_path = self._task_envelope(root)
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                guard = self._customization_guard_script()
                self.assertIn(
                    'index_rc,_=git(bound+["diff-index","--cached","--quiet",head,"--"],256,(0,1)); need(index_rc==0)',
                    guard,
                )
                clean = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertEqual(clean.returncode, 0, clean.stdout)

                readme = target / "README.md"
                readme_bytes = readme.read_bytes()
                readme.write_bytes(readme_bytes + b"\n# staged alternate\n")
                self._git(target, "add", "README.md")
                readme.write_bytes(readme_bytes)
                staged_only = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(staged_only.returncode, 0, staged_only.stdout)
                self._git(
                    target,
                    "restore",
                    "--staged",
                    "--source=HEAD",
                    "README.md",
                )
                restored_index = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertEqual(
                    restored_index.returncode, 0, restored_index.stdout
                )

                self._git(target, "config", "status.showUntrackedFiles", "no")
                hidden = target / "hidden-by-config.txt"
                hidden.write_bytes(b"must be observed\n")
                hidden_status = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(
                    hidden_status.returncode, 0, hidden_status.stdout
                )
                hidden.unlink()
                self._git(target, "config", "--unset", "status.showUntrackedFiles")

                readme_mode = stat.S_IMODE(readme.stat().st_mode)
                self._git(target, "config", "core.fileMode", "false")
                readme.chmod(readme_mode | stat.S_IXUSR)
                hidden_mode = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(hidden_mode.returncode, 0, hidden_mode.stdout)
                readme.chmod(readme_mode)
                self._git(target, "config", "--unset", "core.fileMode")

                for enable, disable in (
                    ("--skip-worktree", "--no-skip-worktree"),
                    ("--assume-unchanged", "--no-assume-unchanged"),
                ):
                    with self.subTest(flag=enable):
                        self._git(target, "update-index", enable, "README.md")
                        readme.write_bytes(readme_bytes + b"hidden drift\n")
                        hidden_index = self._run(
                            "/bin/sh",
                            "-eu",
                            "-c",
                            guard,
                            cwd=target,
                            env=environment,
                        )
                        self.assertNotEqual(
                            hidden_index.returncode, 0, hidden_index.stdout
                        )
                        readme.write_bytes(readme_bytes)
                        self._git(target, "update-index", disable, "README.md")

                restored = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertEqual(restored.returncode, 0, restored.stdout)

        def test_target_filter_config_stops_before_filter_and_preserves_snapshot(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                source, integrated_sha = self._initialize_source(root)
                target = self._initialize_target(root)
                task_path = self._task_envelope(root)
                attributes = target / ".gitattributes"
                payload = target / "payload.probe"
                attributes.write_bytes(b"*.probe filter=audit\n")
                payload.write_bytes(b"filter input\n")
                self._git(target, "add", ".gitattributes", "payload.probe")
                self._git(target, "commit", "-qm", "filter fixture")
                self._git(target, "push", "-q", "origin", "main")
                before = self._project_snapshot(target)

                marker = root / "filter-invoked"
                sentinel = root / "filter-sentinel.py"
                sentinel.write_text(
                    "#!/usr/bin/python3\n"
                    "import pathlib,sys\n"
                    f"pathlib.Path({str(marker)!r}).write_bytes(b'called')\n"
                    "sys.stdout.buffer.write(sys.stdin.buffer.read())\n",
                    encoding="utf-8",
                )
                sentinel.chmod(0o755)
                config = target / ".git" / "config"
                original_config = config.read_bytes()
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                guard = self._customization_guard_script()

                config.write_bytes(
                    original_config
                    + b'\n[filter "audit"]\n\tclean = '
                    + str(sentinel).encode()
                    + b"\n\trequired = true\n"
                )
                rejected = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
                self.assertFalse(marker.exists(), "target clean filter executed")

                for suspicious in (
                    b'\n[FiLtEr "unused"]\r\n\tSmUdGe = /bin/false\r\n',
                    b'\n[core] [FILTER "unused"] process = /bin/false\n',
                    b"\n[filter.unused]\n\trequired = true\n",
                    b"\nfilter.unused.clean = /bin/false\n",
                    b'\xef\xbb\xbf[filter "unused"]\n\tclean = /bin/false\n',
                ):
                    with self.subTest(config=suspicious):
                        config.write_bytes(original_config + suspicious)
                        variant = self._run(
                            "/bin/sh",
                            "-eu",
                            "-c",
                            guard,
                            cwd=target,
                            env=environment,
                        )
                        self.assertNotEqual(
                            variant.returncode, 0, variant.stdout
                        )
                        self.assertFalse(marker.exists())

                config.write_bytes(original_config)
                self.assertEqual(self._project_snapshot(target), before)

        def test_authority_must_be_committed_head_content_not_ignored_files(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                source, integrated_sha = self._initialize_source(root)
                origin = root / "ignored-origin.git"
                self._git(root, "init", "-q", "--bare", str(origin))
                target = root / "ignored-consumer"
                target.mkdir()
                self._git(target, "init", "-q", "-b", "main")
                self._git(target, "config", "user.name", "Audit Fixture")
                self._git(target, "config", "user.email", "audit@example.invalid")
                (target / "README.md").write_bytes(b"# Consumer-owned project\n")
                (target / ".gitignore").write_bytes(b"/AGENTS.md\n/.codex/\n")
                self._git(target, "add", "README.md", ".gitignore")
                self._git(target, "commit", "-qm", "consumer baseline")
                for relative, payload in self._customized_authority().items():
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(payload)
                self._git(target, "remote", "add", "origin", str(origin))
                self._git(target, "push", "-qu", "origin", "main")
                self.assertEqual(
                    self._git(
                        target,
                        "status",
                        "--porcelain=v1",
                        "--untracked-files=all",
                    ),
                    b"",
                )

                task_path = self._task_envelope(root)
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                before = self._project_snapshot(target)
                guard = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target,
                    env=environment,
                )
                self.assertNotEqual(guard.returncode, 0, guard.stdout)
                for command in self._happy_commands():
                    result = self._run(
                        "/bin/sh",
                        "-eu",
                        "-c",
                        self._audit_command_script(command),
                        cwd=target,
                        env=environment,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self._project_snapshot(target), before)

        def test_submodules_and_nested_repositories_stop_before_launcher(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                source, integrated_sha = self._initialize_source(root)
                task_path = self._task_envelope(root)

                def environment_for(target: Path, name: str) -> dict[str, str]:
                    environment = self._closed_environment(root / f"home-{name}")
                    environment.update(
                        {
                            "CONTROL_PLANE_SOURCE": str(source),
                            "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                        }
                    )
                    environment.update(
                        self._authority_digest_environment(target, task_path)
                    )
                    return environment

                def assert_clean_then_rejected(
                    target: Path, environment: dict[str, str], name: str, mutate
                ) -> None:
                    clean = self._run(
                        "/bin/sh",
                        "-eu",
                        "-c",
                        self._customization_guard_script(),
                        cwd=target,
                        env=environment,
                    )
                    self.assertEqual(clean.returncode, 0, clean.stdout)
                    mutate()
                    sentinel = root / f"launcher-{name}"
                    rejected = self._run(
                        "/bin/sh",
                        "-eu",
                        "-c",
                        self._audit_command_script(
                            f"control_plane_audit /usr/bin/touch {str(sentinel)!r}"
                        ),
                        cwd=target,
                        env=environment,
                    )
                    self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
                    self.assertFalse(sentinel.exists())

                gitlink_root = root / "gitlink-case"
                gitlink_root.mkdir()
                gitlink_target = self._initialize_target(gitlink_root)
                gitlink_environment = environment_for(gitlink_target, "gitlink")

                def add_isolated_gitlink() -> None:
                    oid = self._git(gitlink_target, "rev-parse", "HEAD").decode().strip()
                    (gitlink_target / "vendor" / "module").mkdir(parents=True)
                    self._git(
                        gitlink_target,
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        "160000",
                        oid,
                        "vendor/module",
                    )
                    self._git(gitlink_target, "commit", "-qm", "isolated gitlink")
                    self.assertEqual(
                        self._git(
                            gitlink_target,
                            "status",
                            "--porcelain=v1",
                            "-z",
                            "--untracked-files=all",
                        ),
                        b"",
                    )
                    self.assertFalse((gitlink_target / ".git" / "modules").exists())
                    self.assertFalse((gitlink_target / "vendor" / "module" / ".git").exists())

                assert_clean_then_rejected(
                    gitlink_target,
                    gitlink_environment,
                    "gitlink",
                    add_isolated_gitlink,
                )

                modules_root = root / "modules-case"
                modules_root.mkdir()
                modules_target = self._initialize_target(modules_root)
                modules_environment = environment_for(modules_target, "modules")
                assert_clean_then_rejected(
                    modules_target,
                    modules_environment,
                    "modules",
                    lambda: (modules_target / ".git" / "modules").mkdir(),
                )

                nested_root = root / "nested-case"
                nested_root.mkdir()
                nested_target = self._initialize_target(nested_root)
                nested_environment = environment_for(nested_target, "nested")

                def add_ignored_nested_git() -> None:
                    exclude = nested_target / ".git" / "info" / "exclude"
                    exclude.write_bytes(exclude.read_bytes() + b"\n/ignored-nested/\n")
                    nested = nested_target / "ignored-nested"
                    nested.mkdir()
                    (nested / ".git").write_bytes(b"gitdir: /outside\n")
                    self.assertEqual(
                        self._git(
                            nested_target,
                            "status",
                            "--porcelain=v1",
                            "-z",
                            "--untracked-files=all",
                        ),
                        b"",
                    )
                    self.assertFalse((nested_target / ".git" / "modules").exists())

                assert_clean_then_rejected(
                    nested_target,
                    nested_environment,
                    "nested",
                    add_ignored_nested_git,
                )

                combined_root = root / "combined-case"
                combined_root.mkdir()
                target = self._initialize_target(combined_root)
                combined_environment = environment_for(target, "combined")
                clean_combined = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=target,
                    env=combined_environment,
                )
                self.assertEqual(
                    clean_combined.returncode, 0, clean_combined.stdout
                )
                module_source = root / "module-source"
                module_source.mkdir()
                self._git(module_source, "init", "-q", "-b", "main")
                self._git(module_source, "config", "user.name", "Audit Fixture")
                self._git(
                    module_source,
                    "config",
                    "user.email",
                    "audit@example.invalid",
                )
                (module_source / "README.md").write_bytes(b"module\n")
                self._git(module_source, "add", "README.md")
                self._git(module_source, "commit", "-qm", "module")
                self._git(
                    target,
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "add",
                    "-q",
                    str(module_source),
                    "vendor/module",
                )
                self._git(target, "commit", "-qam", "add nested module")
                module = target / "vendor" / "module"
                module_gitdir = Path(
                    self._git(module, "rev-parse", "--absolute-git-dir")
                    .decode()
                    .strip()
                )
                external_worktree = root / "module-external-worktree"
                external_worktree.mkdir()
                self._git(
                    module,
                    "config",
                    "core.worktree",
                    str(external_worktree),
                )
                external_config = root / "module-external.config"
                external_config.write_bytes(b"[alias]\n\tpwn = status\n")
                with (module_gitdir / "config").open("ab") as stream:
                    stream.write(
                        b"\n[include]\n\tpath = "
                        + str(external_config).encode()
                        + b"\n"
                    )
                alternates = module_gitdir / "objects" / "info" / "alternates"
                alternates.parent.mkdir(parents=True, exist_ok=True)
                alternates.write_text(
                    str(module_source / ".git" / "objects") + "\n",
                    encoding="utf-8",
                )
                index_rows = self._git(target, "ls-files", "-s", "-z")
                self.assertIn(b"160000 ", index_rows)
                self.assertTrue((module / ".git").is_file())
                self.assertTrue((target / ".git" / "modules").is_dir())

                sentinel = root / "launcher-called"
                invocation = self._audit_command_script(
                    f"control_plane_audit /usr/bin/touch {str(sentinel)!r}"
                )
                rejected = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    invocation,
                    cwd=target,
                    env=combined_environment,
                )
                self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
                self.assertFalse(sentinel.exists())

        def test_customization_guard_is_required_before_the_five_commands(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                controlled_home = root / "closed-home"
                controlled_home.mkdir()
                environment = self._closed_environment(controlled_home)
                task_path = self._task_envelope(root)
                customized_root = root / "customized"
                customized_root.mkdir()
                target = self._initialize_target(customized_root)
                environment.update(
                    {
                        "CONTROL_PLANE": str(source / "scripts" / "control-plane"),
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                guard = self._customization_guard_script()
                accepted = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertEqual(accepted.returncode, 0, accepted.stdout)

                wrong_digest = dict(environment)
                wrong_digest["TASK_ENVELOPE_SHA256"] = "0" * 64
                rejected_digest = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=wrong_digest
                )
                self.assertNotEqual(
                    rejected_digest.returncode, 0, rejected_digest.stdout
                )

                task_link = root / "task-link.json"
                task_link.symlink_to(task_path)
                linked_task_environment = dict(environment)
                linked_task_environment["TASK_ENVELOPE"] = str(task_link.absolute())
                linked_task = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    guard,
                    cwd=target,
                    env=linked_task_environment,
                )
                self.assertNotEqual(linked_task.returncode, 0, linked_task.stdout)

                raw_root = root / "raw"
                raw_root.mkdir()
                target = self._initialize_target(raw_root, customized=False)
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                raw_guard = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(raw_guard.returncode, 0, raw_guard.stdout)
                payloads = []
                for command in self._happy_commands():
                    result = self._run(
                        "/bin/sh",
                        "-eu",
                        "-c",
                        self._audit_command_script(command),
                        cwd=target,
                        env=environment,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(payloads, [])

        def test_customization_guard_binds_reviewed_bytes_and_physical_ancestors(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                scenario = root / "reviewed"
                scenario.mkdir()
                target = self._initialize_target(scenario)
                task_path = self._task_envelope(root)
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                environment.update(
                    self._authority_digest_environment(target, task_path)
                )
                guard = self._customization_guard_script()
                accepted = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertEqual(accepted.returncode, 0, accepted.stdout)

                agents = target / "AGENTS.md"
                original = agents.read_bytes()
                agents.write_bytes(
                    original.replace(b"audit-fixture-project", b"attacker-project")
                )
                self.assertNotIn(b"__PROJECT_NAME__", agents.read_bytes())
                substituted = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(substituted.returncode, 0, substituted.stdout)
                agents.write_bytes(original)

                external_worktree = root / "external-worktree"
                external_worktree.mkdir()
                external_worktree = external_worktree.resolve(strict=True)
                self._git(
                    target,
                    "config",
                    "core.worktree",
                    str(external_worktree),
                )
                observed_top = Path(
                    self._git(target, "rev-parse", "--show-toplevel")
                    .decode()
                    .strip()
                )
                self.assertEqual(observed_top, external_worktree)
                redirected_worktree = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(
                    redirected_worktree.returncode,
                    0,
                    redirected_worktree.stdout,
                )
                self._git(target, "config", "--unset", "core.worktree")

                target_link = root / "target-link"
                target_link.symlink_to(target, target_is_directory=True)
                environment["TARGET_REPO"] = str(target_link.absolute())
                aliased = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=target, env=environment
                )
                self.assertNotEqual(aliased.returncode, 0, aliased.stdout)

                linked_root = root / "linked-codex"
                linked_root.mkdir()
                linked_target = self._initialize_target(linked_root)
                environment.update(
                    self._authority_digest_environment(linked_target, task_path)
                )
                external = root / "external-codex"
                (linked_target / ".codex").rename(external)
                (linked_target / ".codex").symlink_to(external, target_is_directory=True)
                linked = self._run(
                    "/bin/sh", "-eu", "-c", guard, cwd=linked_target, env=environment
                )
                self.assertNotEqual(linked.returncode, 0, linked.stdout)

        def test_snapshot_records_symlink_leaves_and_directories_without_following(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = self._initialize_target(root)
                outside = root / "outside"
                outside.mkdir()
                (outside / "hidden.txt").write_bytes(b"outside")
                (target / "linked-file").symlink_to(outside / "hidden.txt")
                (target / "linked-directory").symlink_to(
                    outside, target_is_directory=True
                )
                snapshot = self._project_snapshot(target)
                files = snapshot["files"]
                self.assertEqual(files["linked-file"]["type"], "symlink")
                self.assertEqual(files["linked-directory"]["type"], "symlink")
                self.assertNotIn("linked-directory/hidden.txt", files)

        def test_source_binding_disables_lazy_fetch_and_preserves_missing_objects(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                producer = root / "producer"
                producer.mkdir()
                self._git(producer, "init", "-q", "-b", "main")
                self._git(producer, "config", "user.name", "Audit Fixture")
                self._git(producer, "config", "user.email", "audit@example.invalid")
                launcher = producer / "scripts" / "control-plane"
                launcher.parent.mkdir()
                launcher.write_bytes(b"#!/bin/sh\nexit 0\n")
                launcher.chmod(0o755)
                payload = producer / "payload.txt"
                payload.write_bytes(b"promisor-only-payload\n")
                self._git(producer, "add", "scripts/control-plane", "payload.txt")
                self._git(producer, "commit", "-qm", "partial source")
                integrated_sha = self._git(producer, "rev-parse", "HEAD").decode().strip()
                payload_oid = self._git(
                    producer, "rev-parse", f"{integrated_sha}:payload.txt"
                ).decode().strip()

                origin = root / "partial-origin.git"
                self._git(root, "clone", "-q", "--bare", str(producer), str(origin))
                self._git(origin, "config", "uploadpack.allowFilter", "true")
                source = root / "partial-source"
                clone = self._run(
                    "/usr/bin/git",
                    "--no-pager",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "protocol.file.allow=always",
                    "clone",
                    "-q",
                    "--filter=blob:none",
                    "--no-checkout",
                    origin.as_uri(),
                    str(source),
                    cwd=root,
                    env=self._closed_environment(Path("/var/empty")),
                    timeout=60.0,
                )
                self.assertEqual(clone.returncode, 0, clone.stdout)
                self._git(source, "update-ref", "--no-deref", "HEAD", integrated_sha)
                (source / "scripts").mkdir()
                (source / "scripts" / "control-plane").write_bytes(launcher.read_bytes())
                (source / "scripts" / "control-plane").chmod(0o755)
                (source / "payload.txt").write_bytes(payload.read_bytes())
                missing = self._git(
                    source, "rev-list", "--objects", "--missing=print", integrated_sha
                )
                self.assertIn(f"?{payload_oid}".encode(), missing)

                marker = root / "upload-pack-called"
                upload_pack = root / "upload-pack-sentinel.py"
                upload_pack.write_text(
                    "#!/usr/bin/python3\n"
                    "import os,pathlib,sys\n"
                    f"pathlib.Path({str(marker)!r}).write_bytes(b'called')\n"
                    "os.execv('/usr/bin/git', ['/usr/bin/git', 'upload-pack', *sys.argv[1:]])\n",
                    encoding="utf-8",
                )
                upload_pack.chmod(0o755)
                self._git(source, "config", "remote.origin.uploadpack", str(upload_pack))
                before = self._object_store_snapshot(source)
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                result = self._run(
                    "/bin/sh", "-c", self._source_binding(), cwd=root, env=environment
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(marker.exists())
                self.assertEqual(self._object_store_snapshot(source), before)
                self.assertRegex(
                    self._source_binding(), r'"GIT_NO_LAZY_FETCH"\s*:\s*"1"'
                )

        def test_shared_source_and_target_object_stores_fail_without_external_change(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                producer = root / "producer"
                producer.mkdir()
                self._git(producer, "init", "-q", "-b", "main")
                self._git(producer, "config", "user.name", "Audit Fixture")
                self._git(producer, "config", "user.email", "audit@example.invalid")
                launcher = producer / "scripts" / "control-plane"
                launcher.parent.mkdir()
                launcher.write_bytes(b"#!/bin/sh\nexit 0\n")
                launcher.chmod(0o755)
                (producer / "README.md").write_text("shared source\n", encoding="utf-8")
                self._git(producer, "add", ".")
                self._git(producer, "commit", "-qm", "shared source")
                integrated_sha = self._git(
                    producer, "rev-parse", "HEAD"
                ).decode().strip()

                shared_source = root / "shared-source"
                self._git(
                    root,
                    "clone",
                    "-q",
                    "--shared",
                    str(producer),
                    str(shared_source),
                )
                self._git(
                    shared_source, "checkout", "-q", "--detach", integrated_sha
                )
                producer_before = self._object_store_snapshot(producer)
                source_environment = self._closed_environment(root / "source-home")
                source_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(shared_source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                rejected_source = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=source_environment,
                )
                self.assertNotEqual(
                    rejected_source.returncode, 0, rejected_source.stdout
                )
                self.assertEqual(self._object_store_snapshot(producer), producer_before)

                target_root = root / "target-origin"
                target_root.mkdir()
                target_origin = self._initialize_target(target_root)
                shared_target = root / "shared-target"
                self._git(
                    root,
                    "clone",
                    "-q",
                    "--shared",
                    str(target_origin),
                    str(shared_target),
                )
                task_path = self._task_envelope(root)
                target_before = self._object_store_snapshot(target_origin)
                safe_source, safe_source_sha = self._initialize_source(root)
                safe_source_environment = self._closed_environment(
                    root / "safe-source-home"
                )
                safe_source_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(safe_source),
                        "CONTROL_PLANE_SOURCE_SHA": safe_source_sha,
                    }
                )
                accepted_source = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=safe_source_environment,
                )
                self.assertEqual(
                    accepted_source.returncode, 0, accepted_source.stdout
                )

                objects = safe_source / ".git" / "objects"
                info = objects / "info"
                info.mkdir(exist_ok=True)
                http_alternates = info / "http-alternates"
                http_alternates.write_text(
                    "https://example.invalid/objects\n", encoding="utf-8"
                )
                rejected_http_alternates = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=safe_source_environment,
                )
                self.assertNotEqual(
                    rejected_http_alternates.returncode,
                    0,
                    rejected_http_alternates.stdout,
                )
                http_alternates.unlink()

                config = safe_source / ".git" / "config"
                config_bytes = config.read_bytes()
                outside_config = root / "outside.config"
                outside_config.write_bytes(b"[alias]\n\tpwn = status\n")
                for include_header in (
                    b"[include]\n",
                    b'[includeIf "gitdir:/"]\n',
                ):
                    config.write_bytes(
                        config_bytes
                        + include_header
                        + f"\tpath = {outside_config}\n".encode()
                    )
                    rejected_config_include = self._run(
                        "/bin/sh",
                        "-c",
                        self._source_binding(),
                        cwd=root,
                        env=safe_source_environment,
                    )
                    self.assertNotEqual(
                        rejected_config_include.returncode,
                        0,
                        rejected_config_include.stdout,
                    )
                config.write_bytes(
                    b"\xef\xbb\xbf[include]\n"
                    + f"\tpath = {outside_config}\n".encode()
                    + config_bytes
                )
                self.assertEqual(
                    self._git(safe_source, "config", "--get", "alias.pwn").strip(),
                    b"status",
                )
                rejected_bom_include = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=safe_source_environment,
                )
                self.assertNotEqual(
                    rejected_bom_include.returncode,
                    0,
                    rejected_bom_include.stdout,
                )
                for inline_config in (
                    b"[core] [include] path = "
                    + str(outside_config).encode()
                    + b"\n",
                    b"[core]\r[include]\rpath = "
                    + str(outside_config).encode()
                    + b"\r\n",
                ):
                    config.write_bytes(inline_config + config_bytes)
                    self.assertEqual(
                        self._git(
                            safe_source, "config", "--get", "alias.pwn"
                        ).strip(),
                        b"status",
                    )
                    rejected_inline_include = self._run(
                        "/bin/sh",
                        "-c",
                        self._source_binding(),
                        cwd=root,
                        env=safe_source_environment,
                    )
                    self.assertNotEqual(
                        rejected_inline_include.returncode,
                        0,
                        rejected_inline_include.stdout,
                    )
                config.write_bytes(config_bytes)

                object_store_before = self._object_store_snapshot(safe_source)
                external_objects = root / "external-objects"
                objects.rename(external_objects)
                objects.symlink_to(external_objects, target_is_directory=True)
                rejected_object_redirect = self._run(
                    "/bin/sh",
                    "-c",
                    self._source_binding(),
                    cwd=root,
                    env=safe_source_environment,
                )
                self.assertNotEqual(
                    rejected_object_redirect.returncode,
                    0,
                    rejected_object_redirect.stdout,
                )
                self.assertEqual(
                    self._object_store_snapshot(safe_source), object_store_before
                )
                objects.unlink()
                external_objects.rename(objects)

                target_environment = self._closed_environment(root / "target-home")
                target_environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(safe_source),
                        "CONTROL_PLANE_SOURCE_SHA": safe_source_sha,
                    }
                )
                target_environment.update(
                    self._authority_digest_environment(shared_target, task_path)
                )
                rejected_target = self._run(
                    "/bin/sh",
                    "-eu",
                    "-c",
                    self._customization_guard_script(),
                    cwd=shared_target,
                    env=target_environment,
                )
                self.assertNotEqual(
                    rejected_target.returncode, 0, rejected_target.stdout
                )
                self.assertEqual(
                    self._object_store_snapshot(target_origin), target_before
                )

                source_binding = self._source_binding()
                self.assertIn("reject_external_object_store", source_binding)
                self.assertIn('"alternates","http-alternates"', source_binding)
                self.assertIn("stat.S_ISLNK(objects_item.st_mode)", source_binding)

        def test_source_binding_streams_high_fanout_under_a_global_watchdog(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, integrated_sha = self._initialize_source(root)
                fanout = source / "fanout"
                fanout.mkdir()
                for index in range(4_096 * 4 + 1):
                    (fanout / f"entry-{index:05d}").mkdir()
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                binding = self._source_binding()
                started = time.monotonic()
                result = self._run(
                    "/bin/sh", "-c", binding, cwd=root, env=environment, timeout=12.0
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertLess(time.monotonic() - started, 10.0)
                self.assertNotIn("list(os.scandir", binding)
                self.assertIn("signal.setitimer(signal.ITIMER_REAL", binding)

        def test_source_binding_handles_a_batch_request_larger_than_a_pipe(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binding = self._source_binding()
                self.assertIn("process.communicate(input=input_bytes", binding)
                self.assertIn('"cat-file", "--batch-check=', binding)
                source, _ = self._initialize_source(root)
                self._git(source, "config", "user.name", "Audit Fixture")
                self._git(source, "config", "user.email", "audit@example.invalid")
                bulk = source / "bulk"
                bulk.mkdir()
                for index in range(1_800):
                    (bulk / f"item-{index:04d}.txt").write_bytes(
                        f"payload-{index:04d}\n".encode()
                    )
                self._git(source, "add", "bulk")
                self._git(source, "commit", "-qm", "large raw-tree fixture")
                integrated_sha = self._git(source, "rev-parse", "HEAD").decode().strip()
                environment = self._closed_environment(root / "closed-home")
                environment.update(
                    {
                        "CONTROL_PLANE_SOURCE": str(source),
                        "CONTROL_PLANE_SOURCE_SHA": integrated_sha,
                    }
                )
                result = self._run(
                    "/bin/sh",
                    "-c",
                    binding,
                    cwd=root,
                    env=environment,
                    timeout=12.0,
                )
                self.assertEqual(result.returncode, 0, result.stdout)

        def test_fixture_git_ignores_ambient_signing_and_keeps_index_bytes_stable(self) -> None:
            hostile = {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "commit.gpgSign",
                "GIT_CONFIG_VALUE_0": "true",
                "GIT_CONFIG_KEY_1": "gpg.program",
                "GIT_CONFIG_VALUE_1": "/usr/bin/false",
            }
            with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, hostile, clear=False
            ):
                target = self._initialize_target(Path(directory))
                before = self._project_snapshot(target)
                index_before = before["index"]
                readme = target / "README.md"
                metadata = readme.stat()
                os.utime(readme, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1))
                first = self._project_snapshot(target)
                second = self._project_snapshot(target)
                self.assertEqual(first["index"], index_before)
                self.assertEqual(second["index"], index_before)

        def test_target_fixture_never_receives_generic_pack_or_replaces_readme(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                target = self._initialize_target(Path(directory))
                files = {
                    path.relative_to(target).as_posix()
                    for path in target.rglob("*")
                    if path.is_file() and ".git" not in path.relative_to(target).parts
                }
                self.assertEqual(files, self.AUTHORITY_PATHS | {"README.md"})
                self.assertEqual(
                    (target / "README.md").read_bytes(),
                    b"# Consumer-owned project\n\nKeep this content.\n",
                )
                self.assertNotEqual(
                    (target / "README.md").read_bytes(),
                    (NEW_PROJECT_PACK / "README.md").read_bytes(),
                )

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


NewProjectAuditReadinessTests = CoreDocumentationTests.NewProjectAuditReadinessTests


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
