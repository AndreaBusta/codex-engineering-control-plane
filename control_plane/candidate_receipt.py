"""Closed, non-authorizing receipt for one exact local v2.3 candidate."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from .contracts import ContractIssue, canonical_json, contract_digest


MAX_CANDIDATE_RECEIPT_BYTES = 8 * 1024
_CANDIDATE_ID = "v2-3-local-candidate"
_CANDIDATE_FILE = f"{_CANDIDATE_ID}.json"
_PENDING_PREFIX = f".{_CANDIDATE_FILE}.pending-"
_PENDING_NAME = re.compile(
    rf"^{re.escape(_PENDING_PREFIX)}[0-9a-f]{{64}}$", re.ASCII
)
_MAX_CANDIDATE_DIRECTORY_ENTRIES = 64
_MAX_CANDIDATE_DIRECTORY_NAME_BYTES = 4096
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$", re.ASCII)
_GATE_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$", re.ASCII)
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)
_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN"})

_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "candidate_id",
        "repository",
        "branch",
        "head_sha",
        "product_version",
        "runtime_digest",
        "worktree_subject",
        "security_snapshot",
        "index_digest",
        "index_empty",
        "tracked_modified_count",
        "untracked_count",
        "suite",
        "gates",
        "independent_review",
        "security_review",
        "sandbox_status",
        "observed_at",
        "authorizes",
        "receipt_digest",
    }
)
_ALGORITHM_KEYS = frozenset({"algorithm", "digest"})
_SUITE_KEYS = frozenset({"command", "count", "status"})
_GATE_KEYS = frozenset({"name", "command", "status", "result_digest"})
_REVIEW_KEYS = frozenset({"result_digest", "status"})


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code, path, message)


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _bounded_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.encode("utf-8")) <= maximum
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value
    )


def _valid_command(value: object) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        return False
    if any(not _bounded_text(argument, 512) for argument in value):
        return False
    return len(canonical_json(value).encode("utf-8")) <= 2048


def _valid_observed_at(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc


def _pending_name_for_digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("E_CANDIDATE_RECEIPT: receipt digest is invalid")
    return _PENDING_PREFIX + value.removeprefix("sha256:")


def _valid_repository(value: object) -> bool:
    if not _bounded_text(value, 4096):
        return False
    path = Path(value)
    return path.is_absolute() and ".." not in path.parts


def _valid_branch(value: object) -> bool:
    return (
        isinstance(value, str)
        and _BRANCH.fullmatch(value) is not None
        and ".." not in value
        and "@{" not in value
        and not value.endswith((".", "/"))
        and "//" not in value
    )


def _valid_algorithm_binding(
    value: object, *, expected_algorithm: str
) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == _ALGORITHM_KEYS
        and value.get("algorithm") == expected_algorithm
        and _is_digest(value.get("digest"))
    )


def _valid_review(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == _REVIEW_KEYS
        and _is_digest(value.get("result_digest"))
        and isinstance(value.get("status"), str)
        and value.get("status") in _STATUSES
    )


def _valid_suite(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == _SUITE_KEYS
        and _valid_command(value.get("command"))
        and isinstance(value.get("count"), int)
        and not isinstance(value.get("count"), bool)
        and 0 <= int(value["count"]) <= 1_000_000
        and isinstance(value.get("status"), str)
        and value.get("status") in _STATUSES
    )


def _valid_gates(value: object) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        return False
    names: list[str] = []
    for gate in value:
        if (
            not isinstance(gate, Mapping)
            or set(gate) != _GATE_KEYS
            or not isinstance(gate.get("name"), str)
            or _GATE_NAME.fullmatch(str(gate["name"])) is None
            or not _valid_command(gate.get("command"))
            or not isinstance(gate.get("status"), str)
            or gate.get("status") not in _STATUSES
            or not _is_digest(gate.get("result_digest"))
        ):
            return False
        names.append(str(gate["name"]))
    return names == sorted(set(names))


def validate_local_candidate_receipt(
    value: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate the closed LocalCandidateReceiptV1 schema and digest."""

    if not isinstance(value, Mapping) or set(value) != _RECEIPT_KEYS:
        return [
            _issue(
                "CANDIDATE_SCHEMA",
                "",
                "LocalCandidateReceiptV1 must use the closed schema-1 fields.",
            )
        ]
    if value.get("schema_version") != 1 or value.get("kind") != "LocalCandidateReceiptV1":
        return [_issue("CANDIDATE_SCHEMA", "schema_version", "Candidate schema is invalid.")]
    if (
        value.get("candidate_id") != _CANDIDATE_ID
        or not _valid_repository(value.get("repository"))
        or not _valid_branch(value.get("branch"))
        or not isinstance(value.get("head_sha"), str)
        or _GIT_SHA.fullmatch(str(value["head_sha"])) is None
        or not isinstance(value.get("product_version"), str)
        or _VERSION.fullmatch(str(value["product_version"])) is None
        or not _is_digest(value.get("runtime_digest"))
        or not _valid_algorithm_binding(
            value.get("worktree_subject"),
            expected_algorithm="ControlPlaneReviewSubjectV1",
        )
        or not _valid_algorithm_binding(
            value.get("security_snapshot"),
            expected_algorithm="codex-security-snapshot/v1",
        )
        or not _is_digest(value.get("index_digest"))
        or not isinstance(value.get("index_empty"), bool)
    ):
        return [_issue("CANDIDATE_BINDING", "", "Candidate identity binding is invalid.")]
    for name in ("tracked_modified_count", "untracked_count"):
        count = value.get(name)
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 0 <= count <= 1_000_000
        ):
            return [_issue("CANDIDATE_COUNT", name, "Candidate count is invalid.")]
    if (
        not _valid_suite(value.get("suite"))
        or not _valid_gates(value.get("gates"))
        or not _valid_review(value.get("independent_review"))
        or not _valid_review(value.get("security_review"))
        or value.get("sandbox_status") != "PENDING_SANDBOX_TARGET"
        or not _valid_observed_at(value.get("observed_at"))
        or value.get("authorizes") is not False
    ):
        return [_issue("CANDIDATE_EVIDENCE", "", "Candidate evidence is invalid.")]
    core = {key: item for key, item in value.items() if key != "receipt_digest"}
    if not _is_digest(value.get("receipt_digest")) or value.get(
        "receipt_digest"
    ) != contract_digest(core):
        return [_issue("CANDIDATE_DIGEST", "receipt_digest", "Candidate digest is invalid.")]
    try:
        encoded = (canonical_json(dict(value)) + "\n").encode("utf-8")
    except (TypeError, ValueError):
        return [_issue("CANDIDATE_JSON", "", "Candidate JSON is invalid.")]
    if len(encoded) > MAX_CANDIDATE_RECEIPT_BYTES:
        return [_issue("CANDIDATE_SIZE", "", "Candidate exceeds the byte cap.")]
    return []


def _json_copy(value: object) -> Any:
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("E_CANDIDATE_RECEIPT: candidate is not JSON-safe") from error


def build_local_candidate_receipt(
    *,
    candidate_id: str,
    repository: str,
    branch: str,
    head_sha: str,
    product_version: str,
    runtime_digest: str,
    worktree_subject: Mapping[str, object],
    security_snapshot: Mapping[str, object],
    index_digest: str,
    index_empty: bool,
    tracked_modified_count: int,
    untracked_count: int,
    suite: Mapping[str, object],
    gates: Sequence[Mapping[str, object]],
    independent_review: Mapping[str, object],
    security_review: Mapping[str, object],
    sandbox_status: str,
    observed_at: str,
) -> dict[str, Any]:
    """Build one exact non-authorizing local candidate receipt."""

    core: dict[str, Any] = {
        "schema_version": 1,
        "kind": "LocalCandidateReceiptV1",
        "candidate_id": candidate_id,
        "repository": repository,
        "branch": branch,
        "head_sha": head_sha,
        "product_version": product_version,
        "runtime_digest": runtime_digest,
        "worktree_subject": _json_copy(worktree_subject),
        "security_snapshot": _json_copy(security_snapshot),
        "index_digest": index_digest,
        "index_empty": index_empty,
        "tracked_modified_count": tracked_modified_count,
        "untracked_count": untracked_count,
        "suite": _json_copy(suite),
        "gates": _json_copy(list(gates)),
        "independent_review": _json_copy(independent_review),
        "security_review": _json_copy(security_review),
        "sandbox_status": sandbox_status,
        "observed_at": observed_at,
        "authorizes": False,
    }
    receipt = {**core, "receipt_digest": contract_digest(core)}
    issues = validate_local_candidate_receipt(receipt)
    if issues:
        raise ValueError(
            f"E_CANDIDATE_RECEIPT: {issues[0].code}: {issues[0].message}"
        )
    return receipt


class LocalCandidateReceiptStore:
    """Persist one immutable candidate under an exact worktree Git directory."""

    def __init__(self, worktree_git_dir: Path) -> None:
        raw = Path(worktree_git_dir)
        self.worktree_git_dir = raw if raw.is_absolute() else Path.cwd() / raw
        self.path = (
            self.worktree_git_dir
            / "codex-control-plane"
            / "candidates"
            / _CANDIDATE_FILE
        )

    @staticmethod
    def _directory_flags() -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return flags

    @staticmethod
    def _leaf_flags() -> int:
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return flags

    @staticmethod
    def _directory_identity(descriptor: int, *, exact_mode: int | None) -> tuple[int, ...]:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink < 1
            or mode & 0o022
            or (exact_mode is not None and mode != exact_mode)
        ):
            raise ValueError("E_CANDIDATE_RECEIPT: candidate directory is unsafe")
        return (info.st_dev, info.st_ino, info.st_uid, mode, info.st_nlink)

    @staticmethod
    def _leaf_identity(
        descriptor: int,
        *,
        expected_size: int | None,
        expected_links: frozenset[int] = frozenset({1}),
    ) -> tuple[int, ...]:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or mode != 0o600
            or info.st_nlink not in expected_links
            or info.st_size <= 0
            or info.st_size > MAX_CANDIDATE_RECEIPT_BYTES
            or (expected_size is not None and info.st_size != expected_size)
        ):
            raise ValueError("E_CANDIDATE_RECEIPT: candidate leaf is unsafe")
        return (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def _open_directories(self, *, create: bool) -> tuple[list[int], list[tuple[int, ...]]]:
        opened: list[int] = []
        identities: list[tuple[int, ...]] = []
        try:
            root = os.open(self.worktree_git_dir, self._directory_flags())
            opened.append(root)
            identities.append(self._directory_identity(root, exact_mode=None))
            for index, component in enumerate(("codex-control-plane", "candidates")):
                parent = opened[-1]
                created = False
                try:
                    descriptor = os.open(component, self._directory_flags(), dir_fd=parent)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(component, 0o700, dir_fd=parent)
                    os.fsync(parent)
                    descriptor = os.open(component, self._directory_flags(), dir_fd=parent)
                    created = True
                opened.append(descriptor)
                exact_mode = 0o700 if created or index == 1 else None
                identities.append(
                    self._directory_identity(descriptor, exact_mode=exact_mode)
                )
            return opened, identities
        except FileNotFoundError:
            for descriptor in reversed(opened):
                os.close(descriptor)
            raise
        except (OSError, ValueError) as error:
            for descriptor in reversed(opened):
                os.close(descriptor)
            if isinstance(error, ValueError):
                raise
            raise ValueError("E_CANDIDATE_RECEIPT: candidate ancestry is unsafe") from error

    def _revalidate_directories(
        self, descriptors: list[int], identities: list[tuple[int, ...]]
    ) -> None:
        for index, descriptor in enumerate(descriptors):
            current = self._directory_identity(
                descriptor, exact_mode=0o700 if index == 2 else None
            )
            if current[:4] != identities[index][:4]:
                raise ValueError("E_CANDIDATE_RECEIPT: candidate ancestry changed")

    @staticmethod
    def _close(descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    @staticmethod
    def _inventory(directory: int) -> tuple[bool, tuple[str, ...]]:
        """Return one bounded no-content inventory of candidate state."""

        entry_count = 0
        total_name_bytes = 0
        pending: list[str] = []
        canonical = False
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > _MAX_CANDIDATE_DIRECTORY_ENTRIES:
                        raise ValueError(
                            "E_CANDIDATE_RECEIPT: candidate inventory exceeds entry cap"
                        )
                    name = entry.name
                    if not isinstance(name, str):
                        raise ValueError(
                            "E_CANDIDATE_RECEIPT: candidate inventory name is invalid"
                        )
                    total_name_bytes += len(name.encode("utf-8"))
                    if total_name_bytes > _MAX_CANDIDATE_DIRECTORY_NAME_BYTES:
                        raise ValueError(
                            "E_CANDIDATE_RECEIPT: candidate inventory exceeds name cap"
                        )
                    if name == _CANDIDATE_FILE:
                        canonical = True
                    elif name.startswith(_PENDING_PREFIX):
                        if _PENDING_NAME.fullmatch(name) is None:
                            raise ValueError(
                                "E_CANDIDATE_RECEIPT: foreign pending candidate exists"
                            )
                        pending.append(name)
        except OSError as error:
            raise ValueError(
                "E_CANDIDATE_RECEIPT: candidate inventory is unavailable"
            ) from error
        if len(pending) > 1:
            raise ValueError(
                "E_CANDIDATE_RECEIPT: multiple pending candidates exist"
            )
        return canonical, tuple(sorted(pending))

    def _read_named_leaf(
        self,
        directory: int,
        name: str,
        descriptors: list[int],
        identities: list[tuple[int, ...]],
        *,
        expected_links: frozenset[int],
    ) -> tuple[dict[str, Any], bytes, tuple[int, ...]]:
        leaf = -1
        try:
            leaf = os.open(name, self._leaf_flags(), dir_fd=directory)
            before = self._leaf_identity(
                leaf, expected_size=None, expected_links=expected_links
            )
            chunks: list[bytes] = []
            remaining = MAX_CANDIDATE_RECEIPT_BYTES + 1
            while remaining:
                chunk = os.read(leaf, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_CANDIDATE_RECEIPT_BYTES:
                raise ValueError(
                    "E_CANDIDATE_RECEIPT: candidate exceeds byte cap"
                )
            after = self._leaf_identity(
                leaf,
                expected_size=len(payload),
                expected_links=expected_links,
            )
            if after != before:
                raise ValueError(
                    "E_CANDIDATE_RECEIPT: candidate changed during read"
                )
            self._revalidate_directories(descriptors, identities)
            value = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "E_CANDIDATE_RECEIPT: candidate is unreadable"
            ) from error
        finally:
            if leaf >= 0:
                os.close(leaf)
        if not isinstance(value, dict) or validate_local_candidate_receipt(value):
            raise ValueError("E_CANDIDATE_RECEIPT: candidate is malformed")
        if payload != (canonical_json(value) + "\n").encode("utf-8"):
            raise ValueError("E_CANDIDATE_RECEIPT: candidate is not canonical")
        return value, payload, before

    def _load_from_directory(
        self,
        directory: int,
        descriptors: list[int],
        identities: list[tuple[int, ...]],
        *,
        pending_name: str | None = None,
        expected_identity: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        expected_links = frozenset({2}) if pending_name is not None else frozenset({1})
        value, payload, identity = self._read_named_leaf(
            directory,
            _CANDIDATE_FILE,
            descriptors,
            identities,
            expected_links=expected_links,
        )
        if pending_name is not None:
            if pending_name != _pending_name_for_digest(value.get("receipt_digest")):
                raise ValueError(
                    "E_CANDIDATE_RECEIPT_DRIFT: pending name differs from receipt"
                )
            pending_value, pending_payload, pending_identity = self._read_named_leaf(
                directory,
                pending_name,
                descriptors,
                identities,
                expected_links=frozenset({2}),
            )
            if (
                pending_value != value
                or pending_payload != payload
                or pending_identity != identity
            ):
                raise ValueError(
                    "E_CANDIDATE_RECEIPT_DRIFT: published candidate pair differs"
                )
        if expected_identity is not None and identity[:2] != expected_identity:
            raise ValueError(
                "E_CANDIDATE_RECEIPT_DRIFT: published candidate inode changed"
            )
        return value

    def load(self) -> dict[str, Any]:
        """Load and revalidate the exact immutable candidate receipt."""

        try:
            descriptors, identities = self._open_directories(create=False)
        except FileNotFoundError as error:
            raise ValueError("E_CANDIDATE_RECEIPT: candidate is unavailable") from error
        try:
            canonical, pending = self._inventory(descriptors[-1])
            if not canonical:
                raise ValueError(
                    "E_CANDIDATE_RECEIPT: candidate is unavailable"
                )
            return self._load_from_directory(
                descriptors[-1],
                descriptors,
                identities,
                pending_name=pending[0] if pending else None,
            )
        finally:
            self._close(descriptors)

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ValueError("E_CANDIDATE_RECEIPT: candidate write failed")
            offset += written

    def _recover_pending(
        self,
        *,
        directory: int,
        descriptors: list[int],
        identities: list[tuple[int, ...]],
        pending_name: str,
        canonical_exists: bool,
        normalized: dict[str, Any],
        payload: bytes,
    ) -> dict[str, Any]:
        """Recover only one exact reserved pending inode, never foreign state."""

        if canonical_exists:
            stored = self._load_from_directory(
                directory,
                descriptors,
                identities,
                pending_name=pending_name,
            )
            if stored != normalized:
                raise ValueError(
                    "E_CANDIDATE_RECEIPT_DRIFT: linked recovery state differs"
                )
            return stored

        pending_value, pending_payload, pending_identity = self._read_named_leaf(
            directory,
            pending_name,
            descriptors,
            identities,
            expected_links=frozenset({1}),
        )
        if pending_value != normalized or pending_payload != payload:
            raise ValueError(
                "E_CANDIDATE_RECEIPT_DRIFT: pending candidate differs"
            )
        if pending_name != _pending_name_for_digest(
            pending_value.get("receipt_digest")
        ):
            raise ValueError(
                "E_CANDIDATE_RECEIPT_DRIFT: pending name differs from receipt"
            )
        try:
            os.link(
                pending_name,
                _CANDIDATE_FILE,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            os.fsync(directory)
        except OSError as error:
            raise ValueError(
                "E_CANDIDATE_RECEIPT: orphan candidate publication failed"
            ) from error
        canonical, pending = self._inventory(directory)
        if not canonical or pending != (pending_name,):
            raise ValueError(
                "E_CANDIDATE_RECEIPT: candidate recovery is incomplete"
            )
        stored = self._load_from_directory(
            directory,
            descriptors,
            identities,
            pending_name=pending_name,
            expected_identity=(pending_identity[0], pending_identity[1]),
        )
        if stored != normalized:
            raise ValueError(
                "E_CANDIDATE_RECEIPT_DRIFT: recovered candidate differs"
            )
        return stored

    def store(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Create once; permit only byte-equivalent idempotent replay."""

        if validate_local_candidate_receipt(receipt):
            raise ValueError("E_CANDIDATE_RECEIPT: candidate is invalid")
        normalized = _json_copy(receipt)
        payload = (canonical_json(normalized) + "\n").encode("utf-8")
        if len(payload) > MAX_CANDIDATE_RECEIPT_BYTES:
            raise ValueError("E_CANDIDATE_RECEIPT: candidate exceeds byte cap")
        descriptors, identities = self._open_directories(create=True)
        directory = descriptors[-1]
        try:
            canonical_exists, pending = self._inventory(directory)
            if pending:
                return self._recover_pending(
                    directory=directory,
                    descriptors=descriptors,
                    identities=identities,
                    pending_name=pending[0],
                    canonical_exists=canonical_exists,
                    normalized=normalized,
                    payload=payload,
                )
            if canonical_exists:
                existing = self._load_from_directory(
                    directory, descriptors, identities
                )
                if existing == normalized:
                    return existing
                raise ValueError("E_CANDIDATE_RECEIPT_DRIFT: candidate already differs")

            temporary = _pending_name_for_digest(normalized.get("receipt_digest"))
            leaf = -1
            temporary_identity: tuple[int, int] | None = None
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                leaf = os.open(temporary, flags, 0o600, dir_fd=directory)
                opened_info = os.fstat(leaf)
                temporary_identity = (opened_info.st_dev, opened_info.st_ino)
                os.fchmod(leaf, 0o600)
                self._write_all(leaf, payload)
                os.fsync(leaf)
                temp_info = os.fstat(leaf)
                if (
                    not stat.S_ISREG(temp_info.st_mode)
                    or temp_info.st_uid != os.getuid()
                    or stat.S_IMODE(temp_info.st_mode) != 0o600
                    or temp_info.st_nlink != 1
                    or temp_info.st_size != len(payload)
                ):
                    raise ValueError("E_CANDIDATE_RECEIPT: temporary candidate is unsafe")
                self._revalidate_directories(descriptors, identities)
                observed_canonical, observed_pending = self._inventory(directory)
                if observed_canonical or observed_pending != (temporary,):
                    raise ValueError(
                        "E_CANDIDATE_RECEIPT: candidate inventory changed before publication"
                    )
                os.link(
                    temporary,
                    _CANDIDATE_FILE,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
            except FileExistsError as error:
                raise ValueError(
                    "E_CANDIDATE_RECEIPT: candidate publication collided"
                ) from error
            except OSError as error:
                raise ValueError("E_CANDIDATE_RECEIPT: candidate write failed") from error
            finally:
                if leaf >= 0:
                    os.close(leaf)
            if temporary_identity is None:
                raise ValueError("E_CANDIDATE_RECEIPT: candidate inode is unavailable")
            observed_canonical, observed_pending = self._inventory(directory)
            if not observed_canonical or observed_pending != (temporary,):
                raise ValueError(
                    "E_CANDIDATE_RECEIPT: candidate publication is incomplete"
                )
            stored = self._load_from_directory(
                directory,
                descriptors,
                identities,
                pending_name=temporary,
                expected_identity=temporary_identity,
            )
            if stored != normalized:
                raise ValueError("E_CANDIDATE_RECEIPT_DRIFT: stored candidate differs")
            return stored
        finally:
            self._close(descriptors)


__all__ = [
    "LocalCandidateReceiptStore",
    "MAX_CANDIDATE_RECEIPT_BYTES",
    "build_local_candidate_receipt",
    "validate_local_candidate_receipt",
]
