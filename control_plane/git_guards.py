"""Installed, fail-closed Git guards for protected project policy."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tomllib
from typing import Any, Callable, Iterable, Mapping

from control_plane.contracts import contract_digest
from control_plane.policy import validate_policy


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_OID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$", re.ASCII)
_SAFE_GIT_NAME = re.compile(
    r"^(?!-)(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]+$",
    re.ASCII,
)
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_ARTIFACT_BYTES = 16 * 1_048_576
_REQUIRED_ROLES = frozenset(
    {
        "policy",
        "lock",
        "runtime_entrypoint",
        "hook_pre_commit",
        "hook_pre_push",
    }
)
_ZERO_OIDS = frozenset({"0" * 40, "0" * 64})


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _result(
    event: str,
    errors: Iterable[dict[str, str]],
    *,
    warnings: Iterable[dict[str, str]] = (),
) -> dict[str, Any]:
    collected = list(errors)
    return {
        "schema_version": 1,
        "command": "git-guard",
        "ok": not collected,
        "event": event,
        "errors": collected,
        "warnings": list(warnings),
    }


def _invalid(message: str) -> ValueError:
    return ValueError(f"GG_INSTALLED_POLICY_INVALID: {message}")


def _canonical_existing_directory(value: Path | str, *, label: str) -> Path:
    try:
        path = Path(value)
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except (TypeError, ValueError, OSError) as error:
        raise _invalid(f"{label} is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not resolved.is_dir():
        raise _invalid(f"{label} must be a real directory")
    return resolved


def _git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_GRAFT_FILE": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(
    repo: Path, arguments: list[str], *, allowed: frozenset[int]
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            ["/usr/bin/git", *arguments],
            cwd=repo,
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "GG_GIT_STATE_UNOBSERVABLE: Git state could not be observed."
        ) from error
    if completed.returncode not in allowed:
        raise ValueError(
            "GG_GIT_STATE_UNOBSERVABLE: Git state could not be observed."
        )
    if len(completed.stdout) > 131_072 or len(completed.stderr) > 131_072:
        raise ValueError(
            "GG_GIT_STATE_UNOBSERVABLE: Git output exceeded the limit."
        )
    return completed


def _git_text(repo: Path, arguments: list[str]) -> str:
    completed = _git(repo, arguments, allowed=frozenset({0}))
    try:
        return completed.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise ValueError(
            "GG_GIT_STATE_UNOBSERVABLE: Git output was not UTF-8."
        ) from error


def _is_shallow_repository(repo: Path) -> bool:
    """Return Git's exact shallow state, failing closed on ambiguity."""

    value = _git_text(repo, ["rev-parse", "--is-shallow-repository"])
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("GG_GIT_STATE_UNOBSERVABLE: shallow state is invalid")


def _observed_repository(repo: Path) -> tuple[Path, Path]:
    root = Path(_git_text(repo, ["rev-parse", "--show-toplevel"])).resolve(
        strict=True
    )
    common_value = Path(
        _git_text(repo, ["rev-parse", "--git-common-dir"])
    )
    if not common_value.is_absolute():
        common_value = repo / common_value
    common = common_value.resolve(strict=True)
    return root, common


def _regular_bytes(
    root: Path,
    relative: str,
    *,
    expected_mode: int | None,
    limit: int,
) -> bytes:
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or ".." in pure.parts
        or str(pure) != relative
    ):
        raise _invalid("artifact path is not confined")
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise _invalid("artifact parent is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise _invalid("artifact parent is not a real directory")
    path = root.joinpath(*pure.parts)
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > limit
        ):
            raise _invalid("artifact is not a bounded regular file")
        if (
            expected_mode is not None
            and stat.S_IMODE(metadata.st_mode) != expected_mode
        ):
            raise _invalid("artifact mode does not match the manifest")
        payload = path.read_bytes()
    except OSError as error:
        raise _invalid("artifact could not be read") from error
    if len(payload) != metadata.st_size:
        raise _invalid("artifact changed while it was read")
    return payload


def _canonical_manifest_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _artifact_map(
    manifest: Mapping[str, Any], install_root: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise _invalid("manifest artifacts are invalid")
    records: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    seen_paths: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict) or set(raw) != {
            "role",
            "path",
            "digest",
            "mode",
        }:
            raise _invalid("artifact record is invalid")
        role = raw.get("role")
        relative = raw.get("path")
        digest = raw.get("digest")
        mode = raw.get("mode")
        if (
            not isinstance(role, str)
            or not role
            or role in records
            or not isinstance(relative, str)
            or relative in seen_paths
            or not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
            or not isinstance(mode, int)
            or isinstance(mode, bool)
            or mode not in {0o600, 0o700}
        ):
            raise _invalid("artifact binding is invalid")
        payload = _regular_bytes(
            install_root,
            relative,
            expected_mode=mode,
            limit=_MAX_ARTIFACT_BYTES,
        )
        if f"sha256:{sha256(payload).hexdigest()}" != digest:
            raise _invalid("artifact digest does not match the manifest")
        records[role] = dict(raw)
        payloads[role] = payload
        seen_paths.add(relative)
    if not _REQUIRED_ROLES.issubset(records):
        raise _invalid("required installed artifacts are missing")
    if not any(role == "runtime_module" or role.startswith("runtime_module:")
               for role in records):
        raise _invalid("installed runtime modules are missing")
    return records, payloads


def _validate_snapshot_inventory(
    install_root: Path, records: Mapping[str, Mapping[str, Any]]
) -> None:
    expected_files = {"manifest.json"}
    expected_files.update(str(record["path"]) for record in records.values())
    expected_directories: set[str] = set()
    for relative in expected_files:
        parent = PurePosixPath(relative).parent
        while str(parent) not in {"", "."}:
            expected_directories.add(str(parent))
            parent = parent.parent

    observed_files: set[str] = set()
    try:
        paths = list(install_root.rglob("*"))
        for path in paths:
            relative = path.relative_to(install_root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise _invalid(
                    "installed snapshot contains an unmanifested artifact"
                )
            if stat.S_ISDIR(metadata.st_mode):
                if relative not in expected_directories:
                    raise _invalid(
                        "installed snapshot contains an unmanifested artifact"
                    )
                continue
            if not stat.S_ISREG(metadata.st_mode) or relative not in expected_files:
                raise _invalid(
                    "installed snapshot contains an unmanifested artifact"
                )
            observed_files.add(relative)
    except OSError as error:
        raise _invalid("installed snapshot inventory is unavailable") from error
    if observed_files != expected_files:
        raise _invalid("installed snapshot inventory does not match the manifest")


def _validate_snapshot(
    *,
    canonical_repo: Path | str,
    common_git_dir: Path | str,
    manifest_digest: str,
) -> dict[str, Any]:
    if (
        not isinstance(manifest_digest, str)
        or _DIGEST.fullmatch(manifest_digest) is None
    ):
        raise _invalid("manifest digest is invalid")
    canonical = _canonical_existing_directory(
        canonical_repo, label="canonical repository"
    )
    common = _canonical_existing_directory(
        common_git_dir, label="Git common directory"
    )
    try:
        observed_repo, observed_common = _observed_repository(canonical)
    except ValueError as error:
        raise _invalid("repository identity is not observable") from error
    if observed_repo != canonical or observed_common != common:
        raise _invalid("repository or common-dir identity does not match")
    installs = common / "codex-control-plane" / "installs"
    install_root = installs / manifest_digest
    try:
        control_root_metadata = installs.parent.lstat()
        installs_metadata = installs.lstat()
        installs_resolved = installs.resolve(strict=True)
        install_resolved = install_root.resolve(strict=True)
        install_metadata = install_root.lstat()
    except OSError as error:
        raise _invalid("installed snapshot is unavailable") from error
    if (
        stat.S_ISLNK(control_root_metadata.st_mode)
        or not stat.S_ISDIR(control_root_metadata.st_mode)
        or stat.S_ISLNK(installs_metadata.st_mode)
        or not stat.S_ISDIR(installs_metadata.st_mode)
        or installs_resolved != installs
        or installs_resolved.parent.parent != common
        or stat.S_ISLNK(install_metadata.st_mode)
        or not stat.S_ISDIR(install_metadata.st_mode)
        or install_resolved.parent != installs_resolved
        or install_resolved.name != manifest_digest
    ):
        raise _invalid("installed snapshot path is invalid")
    manifest_bytes = _regular_bytes(
        install_resolved,
        "manifest.json",
        expected_mode=0o600,
        limit=_MAX_MANIFEST_BYTES,
    )
    if f"sha256:{sha256(manifest_bytes).hexdigest()}" != manifest_digest:
        raise _invalid("manifest digest does not match its directory")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid("manifest JSON is invalid") from error
    if not isinstance(manifest, dict):
        raise _invalid("manifest must be an object")
    if manifest_bytes != _canonical_manifest_bytes(manifest):
        raise _invalid("manifest is not canonical JSON")
    if set(manifest) != {
        "schema_version",
        "repository_identity",
        "common_git_dir",
        "source_commit",
        "governing_base_commit",
        "install_invocation_id",
        "git",
        "artifacts",
    } or manifest.get("schema_version") != 1:
        raise _invalid("manifest schema is invalid")
    git_policy = manifest.get("git")
    if (
        manifest.get("repository_identity") != str(common)
        or manifest.get("common_git_dir") != str(common)
        or not isinstance(manifest.get("source_commit"), str)
        or _OID.fullmatch(str(manifest.get("source_commit"))) is None
        or not isinstance(manifest.get("governing_base_commit"), str)
        or _OID.fullmatch(
            str(manifest.get("governing_base_commit"))
        )
        is None
        or not isinstance(manifest.get("install_invocation_id"), str)
        or not manifest.get("install_invocation_id")
        or not isinstance(git_policy, dict)
        or set(git_policy) != {
            "base_branch",
            "remote_name",
            "remote_url_digest",
            "remote_repository",
        }
    ):
        raise _invalid("manifest identity binding is invalid")
    base_branch = git_policy.get("base_branch")
    remote_name = git_policy.get("remote_name")
    remote_url_digest = git_policy.get("remote_url_digest")
    remote_repository = git_policy.get("remote_repository")
    if (
        not isinstance(base_branch, str)
        or _SAFE_GIT_NAME.fullmatch(base_branch) is None
        or not isinstance(remote_name, str)
        or _SAFE_GIT_NAME.fullmatch(remote_name) is None
        or not isinstance(remote_url_digest, str)
        or _DIGEST.fullmatch(remote_url_digest) is None
        or not isinstance(remote_repository, str)
        or not remote_repository
    ):
        raise _invalid("protected Git policy is invalid")
    records, payloads = _artifact_map(manifest, install_resolved)
    _validate_snapshot_inventory(install_resolved, records)
    try:
        policy = tomllib.loads(payloads["policy"].decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise _invalid("installed policy is invalid") from error
    if validate_policy(policy):
        raise _invalid("installed policy failed validation")
    policy_git = policy.get("git")
    if (
        not isinstance(policy_git, dict)
        or policy_git.get("base_branch") != base_branch
        or policy_git.get("remote") != remote_name
    ):
        raise _invalid("manifest and installed policy disagree")
    return {
        "canonical_repo": canonical,
        "common_git_dir": common,
        "install_root": install_resolved,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "artifacts": records,
        "payloads": payloads,
        "policy": policy,
    }


class ProtectedGitPolicy:
    __slots__ = (
        "_consumed",
        "_clock",
        "canonical_repo",
        "common_git_dir",
        "install_root",
        "manifest_digest",
        "policy",
        "policy_digest",
        "lock_digest",
        "runtime_digest",
        "base_branch",
        "remote_name",
        "remote_url_digest",
        "remote_repository",
        "source_commit",
        "governing_base_commit",
        "install_invocation_id",
        "invocation_id",
        "freshness_deadline",
        "binding_digest",
    )

    def __new__(cls, *_: object, **__: object) -> "ProtectedGitPolicy":
        raise TypeError("ProtectedGitPolicy is installed-runtime-bound")


_PROTECTED_BINDINGS = (
    "canonical_repo",
    "common_git_dir",
    "install_root",
    "manifest_digest",
    "policy_digest",
    "lock_digest",
    "runtime_digest",
    "base_branch",
    "remote_name",
    "remote_url_digest",
    "remote_repository",
    "source_commit",
    "governing_base_commit",
    "install_invocation_id",
    "invocation_id",
    "freshness_deadline",
    "binding_digest",
)
_ISSUED_PROTECTED: dict[int, tuple[ProtectedGitPolicy, tuple[Any, ...], str]] = {}


def _protected_binding(policy: ProtectedGitPolicy) -> tuple[Any, ...]:
    return tuple(getattr(policy, name) for name in _PROTECTED_BINDINGS)


def _protected_is_live(policy: object) -> bool:
    if type(policy) is not ProtectedGitPolicy:
        return False
    issued = _ISSUED_PROTECTED.get(id(policy))
    return (
        issued is not None
        and issued[0] is policy
        and issued[1] == _protected_binding(policy)
        and issued[2] == contract_digest(policy.policy)
        and not policy._consumed
        and float(policy._clock()) <= policy.freshness_deadline
    )


def _consume_protected(policy: object) -> bool:
    if not _protected_is_live(policy):
        return False
    assert isinstance(policy, ProtectedGitPolicy)
    _ISSUED_PROTECTED.pop(id(policy), None)
    policy._consumed = True
    return True


def _runtime_digest(records: Mapping[str, Mapping[str, Any]]) -> str:
    runtime = [
        {
            "role": role,
            "path": record["path"],
            "digest": record["digest"],
            "mode": record["mode"],
        }
        for role, record in records.items()
        if role == "runtime_entrypoint"
        or role == "runtime_module"
        or role.startswith("runtime_module:")
    ]
    return contract_digest(sorted(runtime, key=lambda item: item["role"]))


def load_protected_git_policy(
    *,
    canonical_repo: Path | str,
    common_git_dir: Path | str,
    installed_manifest_digest: str,
    invocation_id: str,
    clock: Callable[[], float],
) -> ProtectedGitPolicy:
    """Load one opaque guard policy from an immutable installed snapshot."""

    if not isinstance(invocation_id, str) or not invocation_id:
        raise _invalid("invocation identity is invalid")
    try:
        now = float(clock())
    except (TypeError, ValueError) as error:
        raise _invalid("clock is invalid") from error
    if not math.isfinite(now):
        raise _invalid("clock is invalid")
    snapshot = _validate_snapshot(
        canonical_repo=canonical_repo,
        common_git_dir=common_git_dir,
        manifest_digest=installed_manifest_digest,
    )
    manifest = snapshot["manifest"]
    git_policy = manifest["git"]
    records = snapshot["artifacts"]
    result = object.__new__(ProtectedGitPolicy)
    result._consumed = False
    result._clock = clock
    result.canonical_repo = str(snapshot["canonical_repo"])
    result.common_git_dir = str(snapshot["common_git_dir"])
    result.install_root = str(snapshot["install_root"])
    result.manifest_digest = installed_manifest_digest
    result.policy = deepcopy(snapshot["policy"])
    result.policy_digest = records["policy"]["digest"]
    result.lock_digest = records["lock"]["digest"]
    result.runtime_digest = _runtime_digest(records)
    result.base_branch = git_policy["base_branch"]
    result.remote_name = git_policy["remote_name"]
    result.remote_url_digest = git_policy["remote_url_digest"]
    result.remote_repository = git_policy["remote_repository"]
    result.source_commit = manifest["source_commit"]
    result.governing_base_commit = manifest["governing_base_commit"]
    result.install_invocation_id = manifest["install_invocation_id"]
    result.invocation_id = invocation_id
    result.freshness_deadline = now + 30.0
    result.binding_digest = contract_digest(
        {
            name: getattr(result, name)
            for name in _PROTECTED_BINDINGS
            if name not in {"freshness_deadline", "binding_digest"}
        }
    )
    _ISSUED_PROTECTED[id(result)] = (
        result,
        _protected_binding(result),
        contract_digest(result.policy),
    )
    return result


def _candidate_drift(policy: ProtectedGitPolicy, repo: Path) -> bool:
    try:
        candidate = _regular_bytes(
            repo,
            ".codex/project-policy.toml",
            expected_mode=None,
            limit=131_072,
        )
    except ValueError:
        return True
    return f"sha256:{sha256(candidate).hexdigest()}" != policy.policy_digest


def _prepare_guard(
    repo: Path | str, policy: ProtectedGitPolicy
) -> tuple[Path, list[dict[str, str]]]:
    if not _protected_is_live(policy):
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: protected policy is invalid or replayed"
        )
    try:
        candidate_repo = Path(repo).resolve(strict=True)
        observed_repo, observed_common = _observed_repository(candidate_repo)
    except (OSError, ValueError) as error:
        raise ValueError(
            "GG_GIT_STATE_UNOBSERVABLE: repository state is unavailable"
        ) from error
    if (
        str(observed_repo) != policy.canonical_repo
        or str(observed_common) != policy.common_git_dir
    ):
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: repository identity changed"
        )
    if not _consume_protected(policy):
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: protected policy was replayed"
        )
    warnings: list[dict[str, str]] = []
    if _candidate_drift(policy, observed_repo):
        warnings.append(
            _error(
                "GG_CANDIDATE_POLICY_DRIFT",
                "Candidate policy differs from the installed protected policy.",
            )
        )
    return observed_repo, warnings


def guard_pre_commit(
    repo: Path | str, protected_policy: ProtectedGitPolicy
) -> dict[str, Any]:
    """Block commits on the installed base branch or detached HEAD."""

    try:
        canonical, drift_warnings = _prepare_guard(
            repo, protected_policy
        )
        branch = _git(
            canonical,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            allowed=frozenset({0, 1}),
        )
        errors: list[dict[str, str]] = []
        if branch.returncode == 1:
            errors.append(
                _error(
                    "GG_DETACHED_HEAD",
                    "Commits from detached HEAD are forbidden.",
                )
            )
        else:
            try:
                branch_name = branch.stdout.decode(
                    "utf-8", errors="strict"
                ).strip()
            except UnicodeDecodeError as error:
                raise ValueError(
                    "GG_GIT_STATE_UNOBSERVABLE: branch was not UTF-8"
                ) from error
            if branch_name == protected_policy.base_branch:
                errors.append(
                    _error(
                        "GG_BASE_COMMIT",
                        "Direct commits on the configured base are forbidden.",
                    )
                )
        return _result(
            "pre-commit", errors, warnings=drift_warnings
        )
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        return _result("pre-commit", [_error(code, str(error))])


def _valid_update(update: object) -> bool:
    if (
        not isinstance(update, (tuple, list))
        or len(update) != 4
        or not all(isinstance(item, str) for item in update)
    ):
        return False
    local_ref, local_oid, remote_ref, remote_oid = update
    if (
        _OID.fullmatch(local_oid) is None
        or _OID.fullmatch(remote_oid) is None
        or not remote_ref.startswith("refs/heads/")
    ):
        return False
    if local_oid in _ZERO_OIDS:
        return local_ref == "(delete)"
    return local_ref.startswith("refs/heads/")


def guard_pre_push(
    repo: Path | str,
    protected_policy: ProtectedGitPolicy,
    *,
    remote_name: str,
    remote_url: str,
    updates: Iterable[tuple[str, str, str, str]],
) -> dict[str, Any]:
    """Evaluate every push update against installed base and ancestry."""

    try:
        canonical, drift_warnings = _prepare_guard(
            repo, protected_policy
        )
        errors: list[dict[str, str]] = []
        observed_remote_digest = (
            f"sha256:{sha256(remote_url.encode('utf-8')).hexdigest()}"
            if isinstance(remote_url, str)
            else None
        )
        if (
            remote_name != protected_policy.remote_name
            or observed_remote_digest != protected_policy.remote_url_digest
        ):
            errors.append(
                _error(
                    "GG_REMOTE_UNVERIFIED",
                    "Observed remote does not match the installed policy.",
                )
            )
        try:
            materialized = list(updates)
        except (TypeError, ValueError) as error:
            raise ValueError("GG_INPUT_INVALID: push input is invalid") from error
        for raw in materialized:
            if not _valid_update(raw):
                if not any(
                    item["code"] == "GG_INPUT_INVALID" for item in errors
                ):
                    errors.append(
                        _error(
                            "GG_INPUT_INVALID",
                            "Push input must contain four valid fields per line.",
                        )
                    )
                continue
            local_ref, local_oid, remote_ref, remote_oid = raw
            if remote_ref == f"refs/heads/{protected_policy.base_branch}":
                errors.append(
                    _error(
                        "GG_BASE_PUSH",
                        "Direct updates to the configured base are forbidden.",
                    )
                )
                continue
            if local_oid in _ZERO_OIDS or remote_oid in _ZERO_OIDS:
                continue
            try:
                ancestry = _git(
                    canonical,
                    ["merge-base", "--is-ancestor", remote_oid, local_oid],
                    allowed=frozenset({0, 1}),
                )
            except ValueError:
                errors.append(
                    _error(
                        "GG_GIT_STATE_UNOBSERVABLE",
                        "Git ancestry could not be observed.",
                    )
                )
                continue
            if ancestry.returncode == 1:
                try:
                    shallow = _is_shallow_repository(canonical)
                except ValueError:
                    errors.append(
                        _error(
                            "GG_GIT_STATE_UNOBSERVABLE",
                            "Git shallow state could not be observed.",
                        )
                    )
                    continue
                if shallow:
                    errors.append(
                        _error(
                            "GG_GIT_STATE_UNOBSERVABLE",
                            "Git ancestry is incomplete in a shallow repository.",
                        )
                    )
                    continue
                errors.append(
                    _error(
                        "GG_NON_FAST_FORWARD",
                        "A proven non-fast-forward update is forbidden.",
                    )
                )
        unique: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in errors:
            if item["code"] not in seen:
                seen.add(item["code"])
                unique.append(item)
        return _result(
            "pre-push", unique, warnings=drift_warnings
        )
    except ValueError as error:
        code = str(error).split(":", 1)[0]
        return _result("pre-push", [_error(code, str(error))])


class InstalledPolicyObservation:
    __slots__ = (
        "_consumed",
        "_clock",
        "repository_identity",
        "common_git_dir",
        "install_root",
        "manifest_digest",
        "policy",
        "policy_digest",
        "lock_digest",
        "runtime_digest",
        "source_commit",
        "governing_base_commit",
        "remote_repository",
        "session_id",
        "invocation_id",
        "freshness_deadline",
        "binding_digest",
    )

    def __new__(cls, *_: object, **__: object) -> "InstalledPolicyObservation":
        raise TypeError("InstalledPolicyObservation is host-bound")


class ValidatedInstalledPolicyObservation(InstalledPolicyObservation):
    __slots__ = ()

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedInstalledPolicyObservation":
        raise TypeError("ValidatedInstalledPolicyObservation is host-bound")


_OBSERVATION_BINDINGS = tuple(
    name
    for name in InstalledPolicyObservation.__slots__
    if name not in {"_consumed", "_clock"}
)
_ISSUED_OBSERVATIONS: dict[
    int, tuple[InstalledPolicyObservation, tuple[Any, ...], str]
] = {}
_ISSUED_VALIDATED: dict[
    int, tuple[ValidatedInstalledPolicyObservation, tuple[Any, ...], str]
] = {}


def _observation_binding(value: InstalledPolicyObservation) -> tuple[Any, ...]:
    return tuple(getattr(value, name) for name in _OBSERVATION_BINDINGS)


def _observation_live(
    value: object, *, validated: bool, clock: Callable[[], float]
) -> bool:
    expected_type = (
        ValidatedInstalledPolicyObservation
        if validated
        else InstalledPolicyObservation
    )
    registry = _ISSUED_VALIDATED if validated else _ISSUED_OBSERVATIONS
    if type(value) is not expected_type:
        return False
    issued = registry.get(id(value))
    return (
        issued is not None
        and issued[0] is value
        and issued[1] == _observation_binding(value)
        and issued[2] == contract_digest(value.policy)
        and not value._consumed
        and float(clock()) <= value.freshness_deadline
    )


def _revalidate_observation_snapshot(
    value: InstalledPolicyObservation | ProtectedGitPolicy,
) -> bool:
    repository_identity = getattr(
        value, "repository_identity", getattr(value, "canonical_repo", None)
    )
    try:
        snapshot = _validate_snapshot(
            canonical_repo=repository_identity,
            common_git_dir=value.common_git_dir,
            manifest_digest=value.manifest_digest,
        )
    except ValueError:
        return False
    records = snapshot["artifacts"]
    return (
        str(snapshot["install_root"]) == value.install_root
        and records["policy"]["digest"] == value.policy_digest
        and records["lock"]["digest"] == value.lock_digest
        and _runtime_digest(records) == value.runtime_digest
        and contract_digest(snapshot["policy"]) == contract_digest(value.policy)
    )


def observe_installed_policy_source(
    *,
    protected_policy: ProtectedGitPolicy,
    canonical_repo: Path | str,
    expected_manifest_digest: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> InstalledPolicyObservation:
    """Frame a short-lived observation from one exact installed policy."""

    try:
        canonical = Path(canonical_repo).resolve(strict=True)
        now = float(clock())
    except (TypeError, ValueError, OSError) as error:
        raise ValueError(
            "GG_INSTALLED_POLICY_OBSERVATION: invalid observation binding"
        ) from error
    if (
        not _protected_is_live(protected_policy)
        or str(canonical) != protected_policy.canonical_repo
        or expected_manifest_digest != protected_policy.manifest_digest
        or invocation_id != protected_policy.invocation_id
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
        or not math.isfinite(now)
        or not _revalidate_observation_snapshot(protected_policy)
    ):
        raise ValueError(
            "GG_INSTALLED_POLICY_OBSERVATION: installed policy is invalid"
        )
    if not _consume_protected(protected_policy):
        raise ValueError(
            "GG_INSTALLED_POLICY_OBSERVATION: installed policy was replayed"
        )
    result = object.__new__(InstalledPolicyObservation)
    result._consumed = False
    result._clock = clock
    result.repository_identity = protected_policy.canonical_repo
    result.common_git_dir = protected_policy.common_git_dir
    result.install_root = protected_policy.install_root
    result.manifest_digest = protected_policy.manifest_digest
    result.policy = deepcopy(protected_policy.policy)
    result.policy_digest = protected_policy.policy_digest
    result.lock_digest = protected_policy.lock_digest
    result.runtime_digest = protected_policy.runtime_digest
    result.source_commit = protected_policy.source_commit
    result.governing_base_commit = protected_policy.governing_base_commit
    result.remote_repository = protected_policy.remote_repository
    result.session_id = session_id
    result.invocation_id = invocation_id
    result.freshness_deadline = min(
        protected_policy.freshness_deadline, now + float(ttl_seconds)
    )
    result.binding_digest = contract_digest(
        {
            name: getattr(result, name)
            for name in _OBSERVATION_BINDINGS
            if name not in {"freshness_deadline", "binding_digest"}
        }
    )
    _ISSUED_OBSERVATIONS[id(result)] = (
        result,
        _observation_binding(result),
        contract_digest(result.policy),
    )
    return result


def validate_installed_policy_source(
    observation: InstalledPolicyObservation,
    *,
    expected_repository_identity: Path | str,
    expected_manifest_digest: str,
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedInstalledPolicyObservation:
    """Validate and consume one manifest-bound installed observation."""

    try:
        expected_repo = Path(expected_repository_identity).resolve(strict=True)
    except (TypeError, ValueError, OSError) as error:
        raise ValueError(
            "GG_INSTALLED_POLICY_OBSERVATION: invalid repository identity"
        ) from error
    if (
        not _observation_live(observation, validated=False, clock=clock)
        or observation.repository_identity != str(expected_repo)
        or observation.manifest_digest != expected_manifest_digest
        or observation.session_id != expected_session_id
        or observation.invocation_id != expected_invocation_id
        or not _revalidate_observation_snapshot(observation)
    ):
        raise ValueError(
            "GG_INSTALLED_POLICY_OBSERVATION: observation is invalid or replayed"
        )
    _ISSUED_OBSERVATIONS.pop(id(observation), None)
    observation._consumed = True
    result = object.__new__(ValidatedInstalledPolicyObservation)
    for name in InstalledPolicyObservation.__slots__:
        setattr(result, name, getattr(observation, name))
    result._consumed = False
    _ISSUED_VALIDATED[id(result)] = (
        result,
        _observation_binding(result),
        contract_digest(result.policy),
    )
    return result


def _validated_installed_policy_is_live(
    observation: object, *, clock: Callable[[], float]
) -> bool:
    return _observation_live(observation, validated=True, clock=clock) and (
        _revalidate_observation_snapshot(observation)
    )


def _consume_validated_installed_policy(observation: object) -> bool:
    if type(observation) is not ValidatedInstalledPolicyObservation:
        return False
    issued = _ISSUED_VALIDATED.pop(id(observation), None)
    if (
        issued is None
        or issued[0] is not observation
        or issued[1] != _observation_binding(observation)
        or issued[2] != contract_digest(observation.policy)
        or observation._consumed
    ):
        return False
    observation._consumed = True
    return True
