"""Transactional, target-specific project adoption of the control plane."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit

from control_plane.contracts import canonical_json, contract_digest
from control_plane.policy import load_policy
from control_plane.project_profiles import detect_project_profile
from control_plane.repository import (
    discover_repository,
    git_common_dir,
    git_environment,
    worktree_git_dir,
)
from control_plane.resource_registry import load_registry


RUNTIME_PACKAGE = "codex_control_plane_runtime_v2"
RUNTIME_MODULES = (
    "__init__.py",
    "adoption.py",
    "clarification.py",
    "cli.py",
    "contracts.py",
    "git_guards.py",
    "git_state.py",
    "graph.py",
    "hooks.py",
    "host_bridge.py",
    "intake.py",
    "lifecycle.py",
    "lockfile.py",
    "policy.py",
    "project_profiles.py",
    "repository.py",
    "resource_registry.py",
    "risk_sentinel.py",
    "routing.py",
    "scopes.py",
)
MANAGED_FILES = (
    (".codex/project-policy.toml", ".codex/project-policy.toml"),
    (".codex/resource-registry.toml", ".codex/resource-registry.toml"),
    (".codex/control-plane.lock", ".codex/control-plane.lock"),
    (".codex/hooks.json", ".codex/hooks.json"),
    (".codex/hooks/control_plane_hook.py", ".codex/hooks/control_plane_hook.py"),
    (".codex/git-hooks/pre-commit", ".codex/git-hooks/pre-commit"),
    (".codex/git-hooks/pre-push", ".codex/git-hooks/pre-push"),
    ("scripts/control-plane", "scripts/control-plane"),
    ("AGENTS.md", "AGENTS.md"),
    ("SECURITY.md", "docs/codex-control-plane/SECURITY.md"),
    (
        "docs/engineering/01-operating-model.md",
        "docs/codex-control-plane/operating-model.md",
    ),
    (
        "docs/engineering/10-resource-routing.md",
        "docs/codex-control-plane/resource-routing.md",
    ),
    (
        "docs/engineering/11-lifecycle-hooks-adoption.md",
        "docs/codex-control-plane/lifecycle-hooks-adoption.md",
    ),
    (
        "docs/engineering/12-multidominio-y-modos.md",
        "docs/codex-control-plane/multidomain-and-modes.md",
    ),
    ("docs/profiles/generic.md", "docs/codex-control-plane/profiles/generic.md"),
    ("docs/profiles/ios.md", "docs/codex-control-plane/profiles/ios.md"),
    ("docs/profiles/android.md", "docs/codex-control-plane/profiles/android.md"),
    ("docs/profiles/web-pwa.md", "docs/codex-control-plane/profiles/web-pwa.md"),
    (
        "docs/profiles/saas-backend.md",
        "docs/codex-control-plane/profiles/saas-backend.md",
    ),
    (
        "docs/profiles/ai-text-pipeline.md",
        "docs/codex-control-plane/profiles/ai-text-pipeline.md",
    ),
    ("templates/TASK_ENVELOPE.json", ".codex/templates/TASK_ENVELOPE.json"),
    (
        "templates/RESOURCE_USE_RECEIPT.json",
        ".codex/templates/RESOURCE_USE_RECEIPT.json",
    ),
    *tuple(
        (
            f"control_plane/{name}",
            f".codex/runtime/{RUNTIME_PACKAGE}/{name}",
        )
        for name in RUNTIME_MODULES
    ),
)
AGENTS_START = "<!-- BEGIN CODEX_CONTROL_PLANE_V2 -->"
AGENTS_END = "<!-- END CODEX_CONTROL_PLANE_V2 -->"


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _digest_bytes(path.read_bytes())


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace_bytes(
    destination: Path,
    payload: bytes,
    *,
    suffix: str,
    expected_digest: str | None,
    mode: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + suffix)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if (
            expected_digest is not None
            and _digest(temporary) != expected_digest
        ):
            raise ValueError(
                f"E_ADOPT_WRITE: staged digest mismatch: {destination}"
            )
        os.replace(temporary, destination)
        destination.chmod(mode)
        _fsync_directory(destination.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
            _fsync_directory(temporary.parent)


def _durable_copy(
    source: Path,
    destination: Path,
    *,
    suffix: str,
    expected_digest: str | None,
) -> None:
    source_stat = source.stat()
    _durable_replace_bytes(
        destination,
        source.read_bytes(),
        suffix=suffix,
        expected_digest=expected_digest,
        mode=source_stat.st_mode & 0o777,
    )


def _unlink_if_present_and_fsync(path: Path) -> None:
    if not path.exists():
        return
    path.unlink()
    _fsync_directory(path.parent)


def _safe_target(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or "\\" in relative
        or "\x00" in relative
    ):
        raise ValueError("E_ADOPT_PATH: unsafe managed path")
    target = root.joinpath(*pure.parts)
    cursor = root.resolve()
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("E_ADOPT_PATH: managed path contains a symlink")
    parent = target.parent.resolve()
    if not parent.is_relative_to(root.resolve()):
        raise ValueError("E_ADOPT_PATH: managed path escapes repository")
    return target


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=git_environment(),
    )
    if completed.returncode != 0:
        raise ValueError(
            "E_ADOPT_GIT: git command failed: " + " ".join(arguments)
        )
    return completed.stdout.strip()


def _git_config_result(
    root: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    environment = git_environment()
    environment["LANG"] = "C"
    environment["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            ["/usr/bin/git", "config", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "E_ADOPT_HOOK_PATH_CONFLICT: Git config is not observable"
        ) from error
    if (
        completed.returncode == 128
        and arguments[:1] == ("--worktree",)
        and "worktreeConfig" in completed.stderr
    ):
        completed.returncode = 1
        completed.stderr = ""
    if (
        completed.returncode not in {0, 1}
        or len(completed.stdout.encode("utf-8")) > 131_072
        or len(completed.stderr.encode("utf-8")) > 131_072
    ):
        raise ValueError(
            "E_ADOPT_HOOK_PATH_CONFLICT: Git config is not observable"
        )
    return completed


def _config_values(
    root: Path, scope: str
) -> list[str]:
    if scope == "worktree":
        enabled = _git_config_result(
            root, "--local", "--get", "extensions.worktreeConfig"
        )
        if (
            enabled.returncode == 1
            or enabled.stdout.strip().lower() != "true"
        ):
            return []
    result = _git_config_result(
        root, f"--{scope}", "--get-all", "core.hooksPath"
    )
    if result.returncode == 1:
        return []
    return result.stdout.splitlines()


def _current_managed_hooks_path(target_root: Path) -> str | None:
    journal = _journal_path(target_root)
    if not journal.is_file() or journal.is_symlink():
        return None
    try:
        state = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    snapshot = state.get("installed_snapshot")
    if (
        state.get("status") == "applied"
        and isinstance(snapshot, Mapping)
        and isinstance(snapshot.get("hooks_path"), str)
    ):
        return str(snapshot["hooks_path"])
    return None


def _record_has_direct_local_origin(
    target_root: Path, record: Mapping[str, str]
) -> bool:
    origin = record.get("origin", "")
    if not origin.startswith("file:"):
        return False
    path = Path(origin.removeprefix("file:"))
    if not path.is_absolute():
        path = target_root / path
    try:
        observed = path.resolve(strict=True)
        expected = (git_common_dir(target_root) / "config").resolve(
            strict=True
        )
    except OSError:
        return False
    return observed == expected


def _observe_git_hook_config(target_root: Path) -> dict[str, Any]:
    result = _git_config_result(
        target_root,
        "--show-origin",
        "--show-scope",
        "--get-all",
        "core.hooksPath",
    )
    records: list[dict[str, str]] = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split(maxsplit=2)
            if len(parts) != 3:
                raise ValueError(
                    "E_ADOPT_HOOK_PATH_CONFLICT: Git config output is ambiguous"
                )
            records.append(
                {
                    "scope": parts[0],
                    "origin": parts[1],
                    "value": parts[2],
                }
            )
    local_values = _config_values(target_root, "local")
    worktree_values = _config_values(target_root, "worktree")
    active_managed = _current_managed_hooks_path(target_root)
    allowed_active = (
        len(records) == 1
        and records[0]["scope"] == "local"
        and _record_has_direct_local_origin(target_root, records[0])
        and records[0]["value"] == active_managed
        and local_values == [active_managed]
        and not worktree_values
    )
    conflict = bool(records or local_values or worktree_values) and not (
        active_managed is not None and allowed_active
    )
    return {
        "records": records,
        "local_values": local_values,
        "worktree_values": worktree_values,
        "active_managed": active_managed,
        "conflict": conflict,
    }


def _default_executable_hooks(target_root: Path) -> list[str]:
    hooks = git_common_dir(target_root) / "hooks"
    if not hooks.exists():
        return []
    if hooks.is_symlink() or not hooks.is_dir():
        raise ValueError(
            "E_ADOPT_EXISTING_HOOKS: default hooks directory is invalid"
        )
    found: list[str] = []
    for path in sorted(hooks.iterdir()):
        if path.name.endswith(".sample"):
            continue
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_mode & 0o111
        ):
            found.append(path.name)
    return found


def _worktree_count(target_root: Path) -> int:
    raw = _git(target_root, "worktree", "list", "--porcelain")
    count = sum(
        1 for line in raw.splitlines() if line.startswith("worktree ")
    )
    if count < 1 or count > 4096:
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: worktree inventory is ambiguous"
        )
    return count


def _remote_repository_identity(remote_url: str, target_root: Path) -> str:
    patterns = (
        r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$",
        r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, remote_url)
        if match is not None:
            return match.group(1)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "-", target_root.name)
    return f"local/{safe_name or 'repository'}"


def _snapshot_remote_url(remote_url: str) -> str:
    try:
        parsed = urlsplit(remote_url)
    except ValueError as error:
        raise ValueError("E_ADOPT_REMOTE: push URL is invalid") from error
    if (
        parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
        or (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.username is not None
        )
    ):
        raise ValueError(
            "E_ADOPT_REMOTE_CREDENTIALS: push URL contains userinfo"
        )
    return remote_url


def _render_installed_launcher() -> bytes:
    python_binary = str(Path(sys.executable).resolve(strict=True))
    return f"""#!/bin/sh
set -eu
install_root=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")/.." && pwd -P)
exec {json.dumps(python_binary)} -I -B -c '
import importlib
import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import stat
import sys

root = Path(sys.argv[1]).resolve(strict=True)
manifest_path = root / "manifest.json"
raw = manifest_path.read_bytes()
digest = "sha256:" + sha256(raw).hexdigest()
if root.name != digest or stat.S_IMODE(manifest_path.stat().st_mode) != 0o600:
    raise SystemExit("GG_INSTALLED_POLICY_INVALID: manifest binding failed")
manifest = json.loads(raw)
if raw != (
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\\n"
).encode("utf-8"):
    raise SystemExit("GG_INSTALLED_POLICY_INVALID: manifest is not canonical")
artifacts = {{item["path"]: item for item in manifest["artifacts"]}}
expected_files = set(artifacts) | {{"manifest.json"}}
expected_directories = set()
for relative in expected_files:
    parent = Path(relative).parent
    while str(parent) not in {{"", "."}}:
        expected_directories.add(parent.as_posix())
        parent = parent.parent
observed_files = set()
for path in root.rglob("*"):
    relative = path.relative_to(root).as_posix()
    metadata = path.lstat()
    if path.is_symlink():
        raise SystemExit(
            "GG_INSTALLED_POLICY_INVALID: unmanifested artifact"
        )
    if stat.S_ISDIR(metadata.st_mode):
        if relative not in expected_directories:
            raise SystemExit(
                "GG_INSTALLED_POLICY_INVALID: unmanifested artifact"
            )
        continue
    if (
        not stat.S_ISREG(metadata.st_mode)
        or relative not in expected_files
    ):
        raise SystemExit(
            "GG_INSTALLED_POLICY_INVALID: unmanifested artifact"
        )
    observed_files.add(relative)
if observed_files != expected_files:
    raise SystemExit("GG_INSTALLED_POLICY_INVALID: snapshot inventory drift")
for relative, record in artifacts.items():
    path = root / relative
    if (
        path.is_symlink()
        or not path.is_file()
        or "sha256:" + sha256(path.read_bytes()).hexdigest()
        != record["digest"]
        or stat.S_IMODE(path.stat().st_mode) != record["mode"]
    ):
        raise SystemExit("GG_INSTALLED_POLICY_INVALID: artifact drift")
runtime = root / "{RUNTIME_PACKAGE}"
spec = importlib.util.spec_from_file_location(
    "{RUNTIME_PACKAGE}",
    runtime / "__init__.py",
    submodule_search_locations=[str(runtime)],
)
if spec is None or spec.loader is None:
    raise SystemExit("GG_INSTALLED_POLICY_INVALID: runtime unavailable")
package = importlib.util.module_from_spec(spec)
sys.modules["{RUNTIME_PACKAGE}"] = package
spec.loader.exec_module(package)
cli = importlib.import_module("{RUNTIME_PACKAGE}.cli")
raise SystemExit(cli.main(sys.argv[2:]))
' "$install_root" "$@"
""".encode("utf-8")


def _render_installed_git_hook(source: bytes, *, action: str) -> bytes:
    if b"__CONTROL_PLANE_ENTRYPOINT__" not in source:
        raise ValueError(
            "E_ADOPT_SOURCE: Git hook template lacks the entrypoint token"
        )
    arguments = (
        'git-guard pre-commit --repo "$repo"'
        if action == "pre-commit"
        else (
            'git-guard pre-push --repo "$repo" '
            '--remote-name "$1" --remote-url "$2"'
        )
    )
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'repo=$(/usr/bin/git rev-parse --show-toplevel)\n'
        'install_root=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")/.." && pwd -P)\n'
        f'exec "$install_root/scripts/control-plane" {arguments}\n'
    ).encode("utf-8")


def _installed_snapshot(
    source_root: Path,
    target_root: Path,
    *,
    source_commit: str,
    git_facts: Mapping[str, Any],
    rendered: Mapping[str, bytes],
) -> tuple[dict[str, Any], dict[str, tuple[bytes, int]]]:
    source_root = discover_repository(source_root)
    target_root = discover_repository(target_root)
    remote_name = str(git_facts["remote"])
    remote_url = _snapshot_remote_url(
        _git(target_root, "remote", "get-url", "--push", remote_name)
    )
    artifacts: dict[str, tuple[bytes, int, str]] = {
        "policy": (
            rendered[".codex/project-policy.toml"],
            0o600,
            "policy/project-policy.toml",
        ),
        "lock": (
            rendered[".codex/control-plane.lock"],
            0o600,
            "control-plane.lock",
        ),
        "runtime_entrypoint": (
            _render_installed_launcher(),
            0o700,
            "scripts/control-plane",
        ),
        "hook_pre_commit": (
            _render_installed_git_hook(
                (source_root / ".codex/git-hooks/pre-commit").read_bytes(),
                action="pre-commit",
            ),
            0o700,
            "git-hooks/pre-commit",
        ),
        "hook_pre_push": (
            _render_installed_git_hook(
                (source_root / ".codex/git-hooks/pre-push").read_bytes(),
                action="pre-push",
            ),
            0o700,
            "git-hooks/pre-push",
        ),
    }
    runtime_prefix = f".codex/runtime/{RUNTIME_PACKAGE}/"
    for relative, payload in rendered.items():
        if relative.startswith(runtime_prefix):
            name = Path(relative).name
            artifacts[f"runtime_module:{name}"] = (
                payload,
                0o600,
                f"{RUNTIME_PACKAGE}/{name}",
            )
    records = [
        {
            "role": role,
            "path": relative,
            "digest": _digest_bytes(payload),
            "mode": mode,
        }
        for role, (payload, mode, relative) in artifacts.items()
    ]
    governing_base = _git(
        target_root,
        "rev-parse",
        f"refs/remotes/{remote_name}/{git_facts['base_branch']}",
    )
    manifest = {
        "schema_version": 1,
        "repository_identity": str(git_common_dir(target_root)),
        "common_git_dir": str(git_common_dir(target_root)),
        "source_commit": source_commit,
        "governing_base_commit": governing_base,
        "install_invocation_id": contract_digest(
            {
                "source_commit": source_commit,
                "target": str(target_root),
                "policy": _digest_bytes(
                    rendered[".codex/project-policy.toml"]
                ),
            }
        ),
        "git": {
            "base_branch": str(git_facts["base_branch"]),
            "remote_name": remote_name,
            "remote_url_digest": _digest_bytes(remote_url.encode("utf-8")),
            "remote_repository": _remote_repository_identity(
                remote_url, target_root
            ),
        },
        "artifacts": sorted(records, key=lambda item: str(item["path"])),
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    manifest_digest = _digest_bytes(manifest_bytes)
    install_root = (
        git_common_dir(target_root)
        / "codex-control-plane"
        / "installs"
        / manifest_digest
    )
    staging_root = install_root.parent / f".{manifest_digest}.staging"
    files = {
        relative: (payload, mode)
        for payload, mode, relative in artifacts.values()
    }
    files["manifest.json"] = (manifest_bytes, 0o600)
    plan = {
        "manifest_digest": manifest_digest,
        "common_git_dir": str(git_common_dir(target_root)),
        "path": str(install_root),
        "staging_path": str(staging_root),
        "hooks_path": str(install_root / "git-hooks"),
        "artifact_digests": {
            relative: _digest_bytes(payload)
            for relative, (payload, _) in sorted(files.items())
        },
    }
    return plan, files


def _snapshot_is_valid(
    target_root: Path, snapshot: Mapping[str, Any]
) -> bool:
    try:
        from control_plane.git_guards import _validate_snapshot

        observed = _validate_snapshot(
            canonical_repo=target_root,
            common_git_dir=Path(str(snapshot["common_git_dir"])),
            manifest_digest=str(snapshot["manifest_digest"]),
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return (
        str(observed["install_root"]) == str(snapshot.get("path"))
        and str(observed["install_root"] / "git-hooks")
        == str(snapshot.get("hooks_path"))
    )


def _control_state_directory(
    common_git_dir: Path,
    child_name: str | None,
    *,
    create: bool,
    missing_ok: bool = False,
    error_code: str,
) -> Path | None:
    common = Path(common_git_dir)
    try:
        common_metadata = common.lstat()
        common_resolved = common.resolve(strict=True)
    except OSError as error:
        raise ValueError(
            f"{error_code}: common Git directory is unavailable"
        ) from error
    if (
        not common.is_absolute()
        or stat.S_ISLNK(common_metadata.st_mode)
        or not stat.S_ISDIR(common_metadata.st_mode)
        or common_resolved != common
    ):
        raise ValueError(
            f"{error_code}: common Git directory is unsafe"
        )
    current = common
    names = (
        ("codex-control-plane", child_name)
        if child_name is not None
        else ("codex-control-plane",)
    )
    for name in names:
        child = current / name
        try:
            metadata = child.lstat()
        except FileNotFoundError:
            if not create:
                if missing_ok:
                    return None
                raise ValueError(
                    f"{error_code}: control state parent is unavailable"
                )
            try:
                child.mkdir(mode=0o700)
                metadata = child.lstat()
            except OSError as error:
                raise ValueError(
                    f"{error_code}: control state parent cannot be created"
                ) from error
        except OSError as error:
            raise ValueError(
                f"{error_code}: control state parent is unavailable"
            ) from error
        try:
            resolved = child.resolve(strict=True)
        except OSError as error:
            raise ValueError(
                f"{error_code}: control state parent is unavailable"
            ) from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or resolved != child
            or resolved.parent != current
        ):
            raise ValueError(
                f"{error_code}: control state parent is unsafe"
            )
        current = child
    return current


def _snapshot_installs_directory(
    common_git_dir: Path, *, create: bool, missing_ok: bool = False
) -> Path | None:
    return _control_state_directory(
        common_git_dir,
        "installs",
        create=create,
        missing_ok=missing_ok,
        error_code="E_ADOPT_SNAPSHOT_DRIFT",
    )


def _remove_snapshot_tree(path: Path, *, common_git_dir: Path) -> None:
    installs = _snapshot_installs_directory(
        common_git_dir, create=False, missing_ok=True
    )
    if installs is None:
        return
    if not path.is_absolute() or path.parent != installs:
        raise ValueError(
            "E_ADOPT_SNAPSHOT_DRIFT: installed snapshot path is unsafe"
        )
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(
            "E_ADOPT_SNAPSHOT_DRIFT: installed snapshot is unavailable"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(
            "E_ADOPT_SNAPSHOT_DRIFT: installed snapshot path is unsafe"
        )
    entries = list(path.rglob("*"))
    for child in entries:
        try:
            child_metadata = child.lstat()
        except OSError as error:
            raise ValueError(
                "E_ADOPT_SNAPSHOT_DRIFT: installed snapshot is unavailable"
            ) from error
        if stat.S_ISLNK(child_metadata.st_mode) or not (
            stat.S_ISDIR(child_metadata.st_mode)
            or stat.S_ISREG(child_metadata.st_mode)
        ):
            raise ValueError(
                "E_ADOPT_SNAPSHOT_DRIFT: installed snapshot entry is invalid"
            )
    for child in sorted(
        entries, key=lambda item: len(item.parts), reverse=True
    ):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink()
    path.rmdir()
    _fsync_directory(installs)


def _publish_install_snapshot(
    target_root: Path,
    snapshot: Mapping[str, Any],
    files: Mapping[str, tuple[bytes, int]],
) -> bool:
    install_root = Path(str(snapshot["path"]))
    common = Path(str(snapshot["common_git_dir"]))
    installs = _snapshot_installs_directory(common, create=True)
    if (
        not install_root.is_absolute()
        or install_root.parent != installs
        or install_root.name != snapshot.get("manifest_digest")
    ):
        raise ValueError(
            "E_ADOPT_SNAPSHOT_DRIFT: planned snapshot path is unsafe"
        )
    if install_root.exists():
        if not _snapshot_is_valid(target_root, snapshot):
            raise ValueError(
                "E_ADOPT_SNAPSHOT_DRIFT: digest path already exists with drift"
            )
        return False
    temporary = Path(str(snapshot.get("staging_path")))
    if (
        not temporary.is_absolute()
        or temporary.parent != installs
        or temporary.name
        != f".{snapshot['manifest_digest']}.staging"
    ):
        raise ValueError(
            "E_ADOPT_SNAPSHOT_DRIFT: staging path is unsafe"
        )
    if temporary.exists():
        raise ValueError("E_ADOPT_SNAPSHOT_BUSY: staging path already exists")
    temporary.mkdir(mode=0o700)
    try:
        for relative, (payload, mode) in sorted(files.items()):
            destination = _safe_target(temporary, relative)
            _durable_replace_bytes(
                destination,
                payload,
                suffix=".snapshot-new",
                expected_digest=_digest_bytes(payload),
                mode=mode,
            )
        os.rename(temporary, install_root)
        _fsync_directory(installs)
        if not _snapshot_is_valid(target_root, snapshot):
            raise ValueError(
                "E_ADOPT_SNAPSHOT_DRIFT: published snapshot failed verification"
            )
    except Exception:
        if temporary.exists():
            _remove_snapshot_tree(temporary, common_git_dir=common)
        if install_root.exists() and not _snapshot_is_valid(
            target_root, snapshot
        ):
            _remove_snapshot_tree(install_root, common_git_dir=common)
        raise
    return True


def _git_config_mutation(
    root: Path, *arguments: str, allowed: set[int]
) -> None:
    environment = git_environment()
    environment["LANG"] = "C"
    environment["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            ["/usr/bin/git", "config", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "E_ADOPT_HOOK_CONFIG: Git config mutation failed"
        ) from error
    if completed.returncode not in allowed:
        raise ValueError(
            "E_ADOPT_HOOK_CONFIG: Git config mutation failed"
        )


def _restore_local_hook_config(root: Path, values: list[str]) -> None:
    if len(values) > 1 or not all(isinstance(item, str) for item in values):
        raise ValueError(
            "E_ADOPT_HOOK_CONFIG: prior local config is ambiguous"
        )
    _git_config_mutation(
        root,
        "--local",
        "--unset-all",
        "core.hooksPath",
        allowed={0, 5},
    )
    if values:
        _git_config_mutation(
            root,
            "--local",
            "--add",
            "core.hooksPath",
            values[0],
            allowed={0},
        )


def _set_local_hooks_path(root: Path, value: str) -> None:
    _restore_local_hook_config(root, [value])


def _git_config_change_is_applied(
    root: Path, change: Mapping[str, Any]
) -> bool:
    planned = change.get("planned_value")
    if not isinstance(planned, str) or not Path(planned).is_absolute():
        return False
    result = _git_config_result(
        root,
        "--show-origin",
        "--show-scope",
        "--get-all",
        "core.hooksPath",
    )
    if result.returncode != 0:
        return False
    records = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) != 3:
            return False
        records.append(parts)
    return (
        len(records) == 1
        and records[0][0] == "local"
        and _record_has_direct_local_origin(
            root,
            {
                "scope": records[0][0],
                "origin": records[0][1],
                "value": records[0][2],
            },
        )
        and records[0][2] == planned
        and _config_values(root, "local") == [planned]
        and not _config_values(root, "worktree")
    )


def _git_config_precondition_matches(
    root: Path, change: Mapping[str, Any]
) -> bool:
    observed = _observe_git_hook_config(root)
    return (
        observed["records"] == change.get("observed_records")
        and observed["local_values"]
        == change.get("previous_local_values")
        and not observed["worktree_values"]
    )


def _restore_external_state(
    target_root: Path, external_state: Mapping[str, Any] | None
) -> None:
    if not external_state:
        return
    change = external_state.get("git_config_change")
    if not isinstance(change, Mapping):
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: config recovery is ambiguous"
        )
    previous = change.get("previous_local_values")
    if not isinstance(previous, list):
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: prior config is ambiguous"
        )
    snapshot = external_state.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: snapshot recovery is ambiguous"
        )
    failures: list[Exception] = []
    try:
        _restore_local_hook_config(target_root, list(previous))
    except Exception as error:
        failures.append(error)
    common_git_dir = Path(str(snapshot.get("common_git_dir")))
    staging_path = Path(str(snapshot.get("staging_path")))
    try:
        _remove_snapshot_tree(
            staging_path,
            common_git_dir=common_git_dir,
        )
    except Exception as error:
        failures.append(error)
    if snapshot.get("created") is True:
        try:
            _remove_snapshot_tree(
                Path(str(snapshot["path"])),
                common_git_dir=common_git_dir,
            )
        except Exception as error:
            failures.append(error)
    if failures:
        grouped = ExceptionGroup(
            "external adoption recovery failures", failures
        )
        raise ValueError(
            "E_ADOPT_RECOVERY_FAILED: external state remains pending"
        ) from grouped


def _source_identity(source_root: Path) -> tuple[str, bool]:
    commit = _git(source_root, "rev-parse", "HEAD")
    dirty = bool(
        _git(source_root, "status", "--porcelain", "--untracked-files=all")
    )
    return commit, dirty


def _target_git_facts(
    target_root: Path,
    *,
    requested_remote: str | None,
    requested_base: str | None,
) -> dict[str, str | bool]:
    remotes = [item for item in _git(target_root, "remote").splitlines() if item]
    remote = requested_remote or ("origin" if "origin" in remotes else None)
    if remote is None and len(remotes) == 1:
        remote = remotes[0]
    if remote is None or remote not in remotes:
        raise ValueError("E_ADOPT_REMOTE: target remote is ambiguous or missing")
    current_branch = _git(target_root, "symbolic-ref", "--short", "HEAD")
    candidates: list[str] = []
    try:
        remote_head = _git(
            target_root,
            "symbolic-ref",
            "--short",
            f"refs/remotes/{remote}/HEAD",
        )
        if remote_head.startswith(remote + "/"):
            candidates.append(remote_head.removeprefix(remote + "/"))
    except ValueError:
        pass
    try:
        upstream = _git(target_root, "rev-parse", "--abbrev-ref", "@{upstream}")
        if upstream.startswith(remote + "/"):
            candidates.append(upstream.removeprefix(remote + "/"))
    except ValueError:
        pass
    remote_refs = [
        item.removeprefix(remote + "/")
        for item in _git(
            target_root,
            "for-each-ref",
            "--format=%(refname:short)",
            f"refs/remotes/{remote}",
        ).splitlines()
        if item and not item.endswith("/HEAD")
    ]
    base_branch = requested_base
    if base_branch is None:
        unique_candidates = sorted(set(candidates))
        if len(unique_candidates) == 1:
            base_branch = unique_candidates[0]
        elif len(remote_refs) == 1:
            base_branch = remote_refs[0]
        else:
            raise ValueError(
                "E_ADOPT_BASE: base branch is ambiguous; pass it explicitly"
            )
    if base_branch not in remote_refs:
        raise ValueError(
            f"E_ADOPT_BASE: {remote}/{base_branch} is not present locally"
        )
    dirty = bool(_git(target_root, "status", "--porcelain", "--untracked-files=all"))
    return {
        "remote": remote,
        "base_branch": base_branch,
        "current_branch": current_branch,
        "dirty": dirty,
    }


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _render_policy(source_root: Path, target_root: Path, facts: Mapping[str, Any]) -> bytes:
    source = load_policy(source_root / ".codex" / "project-policy.toml")
    profile = detect_project_profile(target_root)
    profiles = [str(item) for item in profile.get("profiles", [])]
    project_kind = "hybrid" if len(profiles) > 1 else (profiles[0] if profiles else "generic")
    reasoning = source["reasoning"]
    release = source["release"]
    lines = [
        "schema_version = 1",
        f"project_name = {_toml_string(target_root.name)}",
        f"project_kind = {_toml_string(project_kind)}",
        "",
        "[git]",
        f"remote = {_toml_string(str(facts['remote']))}",
        f"base_branch = {_toml_string(str(facts['base_branch']))}",
        "require_pull_request = true",
        "allow_direct_base_push = false",
        f"integration_strategy = {_toml_string(str(source['git']['integration_strategy']))}",
        "",
        "[reasoning]",
        f"model = {_toml_string(str(reasoning['model']))}",
        f"default = {_toml_string(str(reasoning['default']))}",
        f"plan = {_toml_string(str(reasoning['plan']))}",
        f"subagent = {_toml_string(str(reasoning['subagent']))}",
        f"normal_max_workers = {int(reasoning['normal_max_workers'])}",
        f"sequential_default = {str(bool(reasoning['sequential_default'])).lower()}",
        "",
        "[documentation]",
        "require_impact_assessment = true",
        "",
        "[release]",
        f"official_source = {_toml_string(str(release['official_source']))}",
        f"require_manifest = {str(bool(release['require_manifest'])).lower()}",
        "allow_local_official_release = false",
    ]
    for tier in ("T0", "T1", "T2", "T3"):
        values = ", ".join(
            _toml_string(str(item))
            for item in source["gates"][tier]["required"]
        )
        lines.extend(["", f"[gates.{tier}]", f"required = [{values}]"])
    return ("\n".join(lines) + "\n").encode("utf-8")


def _render_registry(source_root: Path) -> bytes:
    source_path = source_root / ".codex" / "resource-registry.toml"
    load_registry(source_path)
    text = source_path.read_text(encoding="utf-8")
    replacements = {
        "repo://docs/engineering/01-operating-model.md": "repo://docs/codex-control-plane/operating-model.md",
        "repo://docs/profiles/generic.md": "repo://docs/codex-control-plane/profiles/generic.md",
        "repo://docs/profiles/ios.md": "repo://docs/codex-control-plane/profiles/ios.md",
        "repo://docs/profiles/android.md": "repo://docs/codex-control-plane/profiles/android.md",
        "repo://docs/profiles/web-pwa.md": "repo://docs/codex-control-plane/profiles/web-pwa.md",
        "repo://docs/profiles/saas-backend.md": "repo://docs/codex-control-plane/profiles/saas-backend.md",
        "repo://docs/profiles/ai-text-pipeline.md": "repo://docs/codex-control-plane/profiles/ai-text-pipeline.md",
        "repo://SECURITY.md": "repo://docs/codex-control-plane/SECURITY.md",
        "repo://docs/engineering/10-resource-routing.md": "repo://docs/codex-control-plane/resource-routing.md",
        "repo://docs/engineering/11-lifecycle-hooks-adoption.md": "repo://docs/codex-control-plane/lifecycle-hooks-adoption.md",
        "repo://docs/engineering/12-multidominio-y-modos.md": "repo://docs/codex-control-plane/multidomain-and-modes.md",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    return text.encode("utf-8")


def _target_safe_agents_source(source_text: str) -> str:
    headings = list(
        re.finditer(r"(?m)^## Verificación[ \t]*$", source_text)
    )
    if len(headings) != 1:
        raise ValueError(
            "E_ADOPT_AGENTS: source verification section is missing or ambiguous"
        )
    heading = headings[0]
    following = re.search(r"(?m)^## [^\n]+$", source_text[heading.end() :])
    end = (
        len(source_text)
        if following is None
        else heading.end() + following.start()
    )
    replacement = """## Verificación

Ejecuta primero los gates canónicos documentados por el repositorio objetivo.
Después valida la instalación local del Control Plane:

```bash
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \\
  --registry .codex/resource-registry.toml \\
  --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check
git status --short --branch
```

Informa siempre si se tocaron dependencias, secretos o CI/CD y qué límites
externos permanecen sin verificar.

"""
    return source_text[: heading.start()] + replacement + source_text[end:]


def _render_agents(source_root: Path, target_root: Path) -> bytes:
    source_text = _target_safe_agents_source(
        (source_root / "AGENTS.md").read_text(encoding="utf-8")
    ).rstrip()
    target_path = target_root / "AGENTS.md"
    existing = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
    block = f"{AGENTS_START}\n{source_text}\n{AGENTS_END}"
    if AGENTS_START in existing or AGENTS_END in existing:
        if existing.count(AGENTS_START) != 1 or existing.count(AGENTS_END) != 1:
            raise ValueError("E_ADOPT_AGENTS: managed AGENTS block is malformed")
        prefix, remainder = existing.split(AGENTS_START, 1)
        _, suffix = remainder.split(AGENTS_END, 1)
        rendered = prefix + block + suffix
    else:
        separator = (
            ""
            if not existing or existing.endswith("\n\n")
            else ("\n" if existing.endswith("\n") else "\n\n")
        )
        rendered = existing + separator + block + "\n"
    return rendered.encode("utf-8")


def _render_hooks(source_root: Path, target_root: Path) -> bytes:
    source = json.loads(
        (source_root / ".codex" / "hooks.json").read_text(encoding="utf-8")
    )
    target_path = target_root / ".codex" / "hooks.json"
    if not target_path.is_file():
        return (json.dumps(source, indent=2, sort_keys=True) + "\n").encode("utf-8")
    target = json.loads(target_path.read_text(encoding="utf-8"))
    if not isinstance(target, dict) or not isinstance(target.get("hooks"), dict):
        raise ValueError("E_ADOPT_HOOKS: existing hooks config is not mergeable")
    for event, groups in source.get("hooks", {}).items():
        existing_groups = target["hooks"].setdefault(event, [])
        existing_groups[:] = [
            group
            for group in existing_groups
            if "control_plane_hook.py" not in canonical_json(group)
        ]
        existing_groups.extend(groups)
    target["description"] = source.get("description")
    return (json.dumps(target, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _render_runtime(source_path: Path) -> bytes:
    text = source_path.read_text(encoding="utf-8")
    text = text.replace("from control_plane.", f"from {RUNTIME_PACKAGE}.")
    text = text.replace("import control_plane.", f"import {RUNTIME_PACKAGE}.")
    return text.encode("utf-8")


def _render_launcher(source_root: Path) -> bytes:
    del source_root
    return f"""#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$(command -v python3)"

exec "$PYTHON_BIN" -I -B -c '
import importlib
import importlib.util
from hashlib import sha256
from pathlib import Path
import sys
import tomllib

root = Path(sys.argv[1]).resolve()
try:
    lock = tomllib.loads(
        (root / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
    )
except Exception as error:
    raise SystemExit(f"E_RUNTIME_BOOTSTRAP: invalid lock: {{error}}")
if (
    lock.get("runtime_layout") != "isolated"
    or lock.get("runtime_package") != "{RUNTIME_PACKAGE}"
):
    raise SystemExit("E_RUNTIME_LAYOUT: isolated launcher requires isolated runtime")
runtime = root / ".codex" / "runtime" / "{RUNTIME_PACKAGE}"
if runtime.is_symlink() or not runtime.is_dir():
    raise SystemExit("E_RUNTIME_LAYOUT: isolated runtime is unavailable")
modules = sorted(runtime.glob("*.py"))
if not modules:
    raise SystemExit("E_RUNTIME_LAYOUT: isolated runtime is empty")
hasher = sha256()
for path in modules:
    if path.is_symlink() or not path.is_file():
        raise SystemExit("E_RUNTIME_LAYOUT: invalid isolated runtime module")
    hasher.update(path.name.encode())
    hasher.update(bytes((0,)))
    hasher.update(path.read_bytes())
    hasher.update(bytes((0,)))
if lock.get("digests", {{}}).get("runtime") != f"sha256:{{hasher.hexdigest()}}":
    raise SystemExit("E_RUNTIME_DIGEST: isolated runtime does not match lock")
spec = importlib.util.spec_from_file_location(
    "{RUNTIME_PACKAGE}",
    runtime / "__init__.py",
    submodule_search_locations=[str(runtime)],
)
if spec is None or spec.loader is None:
    raise SystemExit("E_RUNTIME_LAYOUT: isolated runtime cannot be loaded")
package = importlib.util.module_from_spec(spec)
sys.modules["{RUNTIME_PACKAGE}"] = package
spec.loader.exec_module(package)
cli = importlib.import_module("{RUNTIME_PACKAGE}.cli")
raise SystemExit(cli.main(sys.argv[2:]))
' "$PROJECT_ROOT" "$@"
""".encode("utf-8")


def _render_hook_entrypoint(source_root: Path) -> bytes:
    del source_root
    return f'''#!/usr/bin/env -S python3 -I -B
"""Isolated project-local entrypoint for bounded Codex audit hooks."""
from __future__ import annotations
import sys

if not sys.flags.isolated or not sys.flags.safe_path:
    raise SystemExit("E_RUNTIME_BOOTSTRAP: hook requires python3 -I -B")

import importlib
import importlib.util
from hashlib import sha256
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".codex" / "runtime"

def validate_runtime() -> None:
    try:
        lock = tomllib.loads(
            (ROOT / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
        )
    except Exception as error:
        raise RuntimeError(f"E_RUNTIME_BOOTSTRAP: invalid lock: {{error}}") from error
    if (
        lock.get("runtime_layout") != "isolated"
        or lock.get("runtime_package") != "{RUNTIME_PACKAGE}"
    ):
        raise RuntimeError(
            "E_RUNTIME_LAYOUT: isolated hook requires isolated runtime"
        )
    package = RUNTIME / "{RUNTIME_PACKAGE}"
    if package.is_symlink() or not package.is_dir():
        raise RuntimeError("E_RUNTIME_LAYOUT: isolated runtime is unavailable")
    modules = sorted(package.glob("*.py"))
    if not modules:
        raise RuntimeError("E_RUNTIME_LAYOUT: isolated runtime is empty")
    hasher = sha256()
    for path in modules:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("E_RUNTIME_LAYOUT: invalid runtime module")
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\\0")
    if lock.get("digests", {{}}).get("runtime") != (
        f"sha256:{{hasher.hexdigest()}}"
    ):
        raise RuntimeError("E_RUNTIME_DIGEST: isolated runtime does not match lock")

validate_runtime()
runtime = RUNTIME / "{RUNTIME_PACKAGE}"
spec = importlib.util.spec_from_file_location(
    "{RUNTIME_PACKAGE}",
    runtime / "__init__.py",
    submodule_search_locations=[str(runtime)],
)
if spec is None or spec.loader is None:
    raise RuntimeError("E_RUNTIME_LAYOUT: isolated runtime cannot be loaded")
package = importlib.util.module_from_spec(spec)
sys.modules["{RUNTIME_PACKAGE}"] = package
spec.loader.exec_module(package)
run_hook = importlib.import_module("{RUNTIME_PACKAGE}.hooks").run_hook

def main() -> int:
    try:
        output = run_hook(sys.stdin.buffer.read())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if output:
        print(output)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''.encode("utf-8")


def _render_lock(rendered: Mapping[str, bytes]) -> bytes:
    runtime_hasher = sha256()
    runtime_prefix = f".codex/runtime/{RUNTIME_PACKAGE}/"
    for relative in sorted(path for path in rendered if path.startswith(runtime_prefix)):
        runtime_hasher.update(Path(relative).name.encode("utf-8"))
        runtime_hasher.update(b"\0")
        runtime_hasher.update(rendered[relative])
        runtime_hasher.update(b"\0")
    digest = lambda relative: _digest_bytes(rendered[relative])
    lines = [
        "schema_version = 1",
        'product_version = "2.1.0"',
        "policy_schema = 1",
        "registry_schema = 1",
        "task_schema = 1",
        "route_schema = 1",
        "receipt_schema = 1",
        "clarification_schema = 1",
        "risk_schema = 1",
        'hook_mode = "audit"',
        'hook_trust = "pending_hook_trust"',
        f'runtime_package = "{RUNTIME_PACKAGE}"',
        'runtime_layout = "isolated"',
        "",
        "[digests]",
        f'project_policy = "{digest(".codex/project-policy.toml")}"',
        f'resource_registry = "{digest(".codex/resource-registry.toml")}"',
        f'hooks = "{digest(".codex/hooks.json")}"',
        f'hook_entrypoint = "{digest(".codex/hooks/control_plane_hook.py")}"',
        f'git_pre_commit = "{digest(".codex/git-hooks/pre-commit")}"',
        f'git_pre_push = "{digest(".codex/git-hooks/pre-push")}"',
        f'entrypoint = "{digest("scripts/control-plane")}"',
        f'runtime = "sha256:{runtime_hasher.hexdigest()}"',
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _render_distribution(
    source_root: Path,
    target_root: Path,
    *,
    git_facts: Mapping[str, Any],
) -> dict[str, bytes]:
    rendered: dict[str, bytes] = {}
    for source_relative, target_relative in MANAGED_FILES:
        source_path = _safe_target(source_root, source_relative)
        if target_relative == ".codex/project-policy.toml":
            value = _render_policy(source_root, target_root, git_facts)
        elif target_relative == ".codex/resource-registry.toml":
            value = _render_registry(source_root)
        elif target_relative == ".codex/control-plane.lock":
            continue
        elif target_relative == ".codex/hooks.json":
            value = _render_hooks(source_root, target_root)
        elif target_relative == ".codex/hooks/control_plane_hook.py":
            value = _render_hook_entrypoint(source_root)
        elif target_relative == "scripts/control-plane":
            value = _render_launcher(source_root)
        elif target_relative == "AGENTS.md":
            value = _render_agents(source_root, target_root)
        elif target_relative.startswith(f".codex/runtime/{RUNTIME_PACKAGE}/"):
            value = _render_runtime(source_path)
        else:
            if not source_path.is_file():
                raise ValueError(f"E_ADOPT_SOURCE: missing {source_relative}")
            value = source_path.read_bytes()
        rendered[target_relative] = value
    rendered[".codex/control-plane.lock"] = _render_lock(rendered)
    return rendered


def adoption_plan(
    source: Path,
    target: Path,
    *,
    base_branch: str | None = None,
    remote: str | None = None,
    allow_dirty_source: bool = False,
) -> dict[str, Any]:
    """Build a read-only, target-specific immutable adoption plan."""

    source_root = discover_repository(source)
    target_root = discover_repository(target)
    source_commit, source_dirty = _source_identity(source_root)
    git_facts = _target_git_facts(
        target_root,
        requested_remote=remote,
        requested_base=base_branch,
    )
    rendered = _render_distribution(
        source_root, target_root, git_facts=git_facts
    )
    installed_snapshot, _ = _installed_snapshot(
        source_root,
        target_root,
        source_commit=source_commit,
        git_facts=git_facts,
        rendered=rendered,
    )
    hook_config = _observe_git_hook_config(target_root)
    git_config_changes = [
        {
            "key": "core.hooksPath",
            "observed_records": hook_config["records"],
            "previous_local_values": hook_config["local_values"],
            "planned_value": installed_snapshot["hooks_path"],
        }
    ]
    changes: list[dict[str, Any]] = []
    for _, target_relative in MANAGED_FILES:
        target_path = _safe_target(target_root, target_relative)
        before = _digest(target_path)
        after = _digest_bytes(rendered[target_relative])
        changes.append(
            {
                "path": target_relative,
                "action": (
                    "unchanged"
                    if before == after
                    else ("create" if before is None else "update")
                ),
                "before_digest": before,
                "after_digest": after,
            }
        )
    source_manifest_digest = contract_digest(
        {
            "source_commit": source_commit,
            "rendered": {
                path: _digest_bytes(value)
                for path, value in sorted(rendered.items())
            },
            "installed_snapshot": installed_snapshot,
        }
    )
    preflight_errors = []
    if source_dirty and not allow_dirty_source:
        preflight_errors.append("E_ADOPT_SOURCE_DIRTY")
    if git_facts["dirty"]:
        preflight_errors.append("E_ADOPT_TARGET_DIRTY")
    if git_facts["current_branch"] == git_facts["base_branch"]:
        preflight_errors.append("E_ADOPT_PROTECTED_BRANCH")
    if hook_config["conflict"]:
        preflight_errors.append("E_ADOPT_HOOK_PATH_CONFLICT")
    if _default_executable_hooks(target_root):
        preflight_errors.append("E_ADOPT_EXISTING_HOOKS")
    warnings = (
        ["W_ADOPT_SHARED_COMMON_HOOK_PATH"]
        if _worktree_count(target_root) > 1
        else []
    )
    plan_core = {
        "schema_version": 2,
        "operation": "adopt",
        "source": str(source_root),
        "target": str(target_root),
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "allow_dirty_source": allow_dirty_source,
        "source_manifest_digest": source_manifest_digest,
        "target_git": git_facts,
        "changes": changes,
        "installed_snapshot": installed_snapshot,
        "git_config_changes": git_config_changes,
        "warnings": warnings,
        "preflight_errors": preflight_errors,
    }
    plan = {
        **plan_core,
        "plan_id": contract_digest(plan_core),
        "command": "adopt-plan",
        "ok": not preflight_errors,
        "manual_actions": [
            {
                "id": "review-hooks",
                "action": "Review exact hook hashes with /hooks before trusting.",
            },
            {
                "id": "run-project-tests",
                "action": "Run the target project test matrix before soft-enforce.",
            },
        ],
    }
    return plan


def upgrade_plan(
    source: Path,
    target: Path,
    *,
    base_branch: str | None = None,
    remote: str | None = None,
    allow_dirty_source: bool = False,
) -> dict[str, Any]:
    """Create a versioned upgrade plan bound to the installed plan identity."""

    status = adoption_status(target)
    if status.get("status") != "applied":
        raise ValueError("E_UPGRADE_NOT_APPLIED: adopt v2 before upgrading it")
    adopt = adoption_plan(
        source,
        target,
        base_branch=base_branch,
        remote=remote,
        allow_dirty_source=allow_dirty_source,
    )
    core = {
        "schema_version": 2,
        "operation": "upgrade",
        "source": adopt["source"],
        "target": adopt["target"],
        "source_commit": adopt["source_commit"],
        "source_dirty": adopt["source_dirty"],
        "allow_dirty_source": adopt["allow_dirty_source"],
        "source_manifest_digest": adopt["source_manifest_digest"],
        "target_git": adopt["target_git"],
        "from_plan_id": status.get("plan_id"),
        "changes": adopt["changes"],
        "installed_snapshot": adopt["installed_snapshot"],
        "git_config_changes": adopt["git_config_changes"],
        "warnings": adopt["warnings"],
        "preflight_errors": adopt["preflight_errors"],
    }
    return {
        **core,
        "plan_id": contract_digest(core),
        "command": "upgrade-plan",
        "ok": not core["preflight_errors"],
        "manual_actions": adopt["manual_actions"],
    }


def _journal_path(target: Path) -> Path:
    owner_git_dir = worktree_git_dir(target)
    control_root = _control_state_directory(
        owner_git_dir,
        None,
        create=False,
        missing_ok=True,
        error_code="E_ADOPT_RECOVERY_UNKNOWN",
    )
    if control_root is None:
        control_root = owner_git_dir / "codex-control-plane"
    return control_root / "adoption.json"


def _lock_path(target: Path) -> Path:
    root = discover_repository(target)
    raw = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    common = Path(raw)
    if not common.is_absolute():
        common = (root / common).absolute()
    locks = _control_state_directory(
        common,
        "locks",
        create=True,
        error_code="E_ADOPT_RECOVERY_UNKNOWN",
    )
    assert locks is not None
    return locks / "adoption.lock"


class _ProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_ProcessLock":
        try:
            parent_metadata = self.path.parent.lstat()
            parent_resolved = self.path.parent.resolve(strict=True)
        except OSError as error:
            raise ValueError(
                "E_ADOPT_RECOVERY_UNKNOWN: lock parent is unavailable"
            ) from error
        if (
            stat.S_ISLNK(parent_metadata.st_mode)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_resolved != self.path.parent
        ):
            raise ValueError(
                "E_ADOPT_RECOVERY_UNKNOWN: lock parent is unsafe"
            )
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self.fd = os.open(self.path, flags, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.fd)
            self.fd = None
            raise ValueError(
                "E_ADOPT_BUSY: another adoption operation is active"
            ) from error
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def _owner_pointer_path(target: Path) -> Path:
    lock_path = _lock_path(target)
    common = lock_path.parent.parent.parent
    transactions = _control_state_directory(
        common,
        "transactions",
        create=True,
        error_code="E_ADOPT_RECOVERY_UNKNOWN",
    )
    assert transactions is not None
    return transactions / "adoption-owner.json"


def _read_owner_pointer(path: Path) -> dict[str, Any] | None:
    """Read one bounded regular pointer without following filesystem links."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: safe owner pointer reads are unsupported"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: owner pointer is unsafe"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65_536:
            raise ValueError(
                "E_ADOPT_RECOVERY_UNKNOWN: owner pointer is unsafe"
            )
        payload = os.read(descriptor, 65_537)
        if len(payload) > 65_536:
            raise ValueError(
                "E_ADOPT_RECOVERY_UNKNOWN: owner pointer exceeds cap"
            )
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: owner pointer is invalid"
        ) from error
    if not isinstance(value, dict):
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: owner pointer is invalid"
        )
    return value


def _registered_worktree_owners(target: Path) -> dict[str, Path]:
    """Resolve every registered worktree to its exact worktree Git directory."""

    root = discover_repository(target)
    raw = _git(root, "worktree", "list", "--porcelain", "-z")
    if len(raw.encode("utf-8")) > 1_048_576:
        raise ValueError("E_ADOPT_RECOVERY_UNKNOWN: worktree inventory exceeds cap")
    worktrees = [
        Path(field.removeprefix("worktree ")).resolve()
        for field in raw.split("\0")
        if field.startswith("worktree ")
    ]
    if not worktrees or len(worktrees) > 4096 or len(set(worktrees)) != len(worktrees):
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: worktree inventory is ambiguous"
        )
    owners: dict[str, Path] = {}
    for worktree in worktrees:
        if not worktree.is_dir() or worktree.is_symlink():
            raise ValueError(
                "E_ADOPT_RECOVERY_UNKNOWN: registered worktree is unavailable"
            )
        git_dir = worktree_git_dir(worktree)
        identity = str(worktree)
        if identity in owners:
            raise ValueError(
                "E_ADOPT_RECOVERY_UNKNOWN: duplicate worktree identity"
            )
        owners[identity] = git_dir
    return owners


def _unlink_and_fsync(path: Path) -> None:
    path.unlink(missing_ok=False)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _transaction_owner_root(target: Path, transaction_id: str) -> Path:
    transactions = _control_state_directory(
        worktree_git_dir(target),
        "transactions",
        create=True,
        error_code="E_ADOPT_RECOVERY_UNKNOWN",
    )
    assert transactions is not None
    return transactions / transaction_id


def _begin_transaction(
    target: Path,
    *,
    operation: str,
    records: list[Mapping[str, Any]],
    previous_state: Mapping[str, Any] | None = None,
    external_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    transaction_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        + f"-{os.getpid()}"
    )
    owner_git_dir = worktree_git_dir(target)
    owner_root = _transaction_owner_root(target, transaction_id)
    manifest_path = owner_root / "transaction-manifest.json"
    wal_root = owner_root / "wal"
    manifest = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "operation": operation,
        "target_identity": str(discover_repository(target)),
        "owner_git_dir": str(owner_git_dir),
        "wal_path": "wal",
        "records": [dict(item) for item in records],
        "previous_state": (
            dict(previous_state) if previous_state is not None else None
        ),
        "external_state": (
            dict(external_state) if external_state is not None else None
        ),
    }
    _atomic_json(manifest_path, manifest)
    manifest_digest = _digest(manifest_path)
    generation = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "generation": 1,
        "previous_generation_digest": None,
        "status": "prepared",
        "state_digest": contract_digest(manifest),
    }
    generation["generation_digest"] = contract_digest(generation)
    _atomic_json(wal_root / "00000001.json", generation)
    pointer = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "target_identity": manifest["target_identity"],
        "owner_git_dir": str(owner_git_dir),
        "manifest_path": str(manifest_path.relative_to(owner_git_dir)),
        "manifest_digest": manifest_digest,
    }
    _atomic_json(_owner_pointer_path(target), pointer)
    return {
        "pointer": pointer,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "wal_root": wal_root,
        "last_generation": generation,
    }


def _advance_transaction(
    transaction: Mapping[str, Any], *, status: str, state: Mapping[str, Any]
) -> dict[str, Any]:
    previous = dict(transaction["last_generation"])
    number = int(previous["generation"]) + 1
    generation = {
        "schema_version": 1,
        "transaction_id": transaction["pointer"]["transaction_id"],
        "generation": number,
        "previous_generation_digest": previous["generation_digest"],
        "status": status,
        "state_digest": contract_digest(state),
    }
    generation["generation_digest"] = contract_digest(generation)
    _atomic_json(
        Path(transaction["wal_root"]) / f"{number:08d}.json",
        generation,
    )
    transaction["last_generation"] = generation
    return generation


def _commit_transaction(
    target: Path,
    transaction: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
) -> None:
    final = _advance_transaction(transaction, status="committed", state=state)
    committed = {
        "schema_version": 1,
        "transaction_id": transaction["pointer"]["transaction_id"],
        "final_generation": final["generation"],
        "final_generation_digest": final["generation_digest"],
        "state_digest": contract_digest(state),
    }
    _atomic_json(Path(transaction["wal_root"]).parent / "COMMITTED", committed)
    pointer = _owner_pointer_path(target)
    _unlink_and_fsync(pointer)


def _recover_owner_transaction(target: Path) -> None:
    pointer_path = _owner_pointer_path(target)
    pointer = _read_owner_pointer(pointer_path)
    if pointer is None:
        return
    try:
        registered_owners = _registered_worktree_owners(target)
        if (
            set(pointer)
            != {
                "schema_version",
                "transaction_id",
                "target_identity",
                "owner_git_dir",
                "manifest_path",
                "manifest_digest",
            }
            or pointer.get("schema_version") != 1
        ):
            raise ValueError
        owner_git_dir = Path(str(pointer["owner_git_dir"]))
        relative = PurePosixPath(str(pointer["manifest_path"]))
        target_identity = str(pointer["target_identity"])
        if (
            not owner_git_dir.is_absolute()
            or relative.is_absolute()
            or ".." in relative.parts
            or owner_git_dir.is_symlink()
            or not owner_git_dir.is_dir()
            or target_identity not in registered_owners
            or registered_owners[target_identity] != owner_git_dir
        ):
            raise ValueError
        owner_transactions = _control_state_directory(
            owner_git_dir,
            "transactions",
            create=False,
            error_code="E_ADOPT_RECOVERY_UNKNOWN",
        )
        if owner_transactions is None:
            raise ValueError
        manifest_path = owner_git_dir.joinpath(*relative.parts)
        if (
            manifest_path.is_symlink()
            or manifest_path.parent.parent != owner_transactions
            or not manifest_path.resolve().is_relative_to(owner_git_dir.resolve())
            or _digest(manifest_path) != pointer["manifest_digest"]
        ):
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            set(manifest)
            != {
                "schema_version",
                "transaction_id",
                "operation",
                "target_identity",
                "owner_git_dir",
                "wal_path",
                "records",
                "previous_state",
                "external_state",
            }
            or manifest.get("schema_version") != 1
            or manifest.get("transaction_id") != pointer["transaction_id"]
            or manifest.get("owner_git_dir") != str(owner_git_dir)
            or manifest.get("target_identity") != target_identity
            or manifest.get("operation") not in {"adopt", "upgrade"}
            or manifest.get("wal_path") != "wal"
            or not isinstance(manifest.get("records"), list)
        ):
            raise ValueError
        wal_root = manifest_path.parent / "wal"
        if wal_root.is_symlink() or not wal_root.is_dir():
            raise ValueError
        generations = sorted(wal_root.glob("*.json"))
        if not generations or len(generations) > 4096:
            raise ValueError
        previous_digest: str | None = None
        last: dict[str, Any] | None = None
        for number, path in enumerate(generations, start=1):
            value = json.loads(path.read_text(encoding="utf-8"))
            supplied_digest = value.get("generation_digest")
            semantic = dict(value)
            semantic.pop("generation_digest", None)
            if (
                path.name != f"{number:08d}.json"
                or path.is_symlink()
                or set(value)
                != {
                    "schema_version",
                    "transaction_id",
                    "generation",
                    "previous_generation_digest",
                    "status",
                    "state_digest",
                    "generation_digest",
                }
                or value.get("schema_version") != 1
                or value.get("transaction_id") != pointer["transaction_id"]
                or value.get("generation") != number
                or value.get("previous_generation_digest") != previous_digest
                or supplied_digest != contract_digest(semantic)
            ):
                raise ValueError
            previous_digest = str(supplied_digest)
            last = value
        committed_path = manifest_path.parent / "COMMITTED"
        if committed_path.exists():
            committed = json.loads(committed_path.read_text(encoding="utf-8"))
            if (
                committed_path.is_symlink()
                or set(committed)
                != {
                    "schema_version",
                    "transaction_id",
                    "final_generation",
                    "final_generation_digest",
                    "state_digest",
                }
                or committed.get("schema_version") != 1
                or last is None
                or committed.get("transaction_id")
                != pointer["transaction_id"]
                or committed.get("final_generation")
                != last.get("generation")
                or committed.get("final_generation_digest")
                != last.get("generation_digest")
                or committed.get("state_digest")
                != last.get("state_digest")
                or last.get("status") != "committed"
            ):
                raise ValueError
            _unlink_and_fsync(pointer_path)
            return
        recovery_target = Path(target_identity)
        records = list(manifest["records"])
        for record in records:
            if (
                not isinstance(record, Mapping)
                or set(record)
                != {"path", "before_digest", "installed_digest", "backup"}
                or not isinstance(record.get("path"), str)
                or not isinstance(record.get("installed_digest"), str)
                or not str(record["installed_digest"]).startswith("sha256:")
                or (
                    record.get("before_digest") is not None
                    and (
                        not isinstance(record.get("before_digest"), str)
                        or not str(record["before_digest"]).startswith("sha256:")
                    )
                )
                or (
                    record.get("before_digest") is None
                    and record.get("backup") is not None
                )
            ):
                raise ValueError
            _safe_target(recovery_target, str(record["path"]))
            backup = record.get("backup")
            if backup is not None:
                if not isinstance(backup, str):
                    raise ValueError
                backup_path = _safe_target(owner_git_dir, backup)
                if (
                    backup_path.is_symlink()
                    or _digest(backup_path) != record["before_digest"]
                ):
                    raise ValueError
        external_state = manifest.get("external_state")
        if external_state is not None and not isinstance(
            external_state, Mapping
        ):
            raise ValueError
        _recover_failed_transaction(
            original_error=ValueError(
                "E_ADOPT_RECOVERY_PENDING: interrupted transaction"
            ),
            target_root=recovery_target,
            external_state=(
                external_state
                if isinstance(external_state, Mapping)
                else {}
            ),
            records=records,
            staging_suffix=(
                ".codex-new"
                if manifest["operation"] == "adopt"
                else ".codex-upgrade"
            ),
        )
        previous_state = manifest.get("previous_state")
        if isinstance(previous_state, Mapping):
            _atomic_json(_journal_path(recovery_target), previous_state)
        elif previous_state is None and manifest["operation"] == "adopt":
            _unlink_if_present_and_fsync(
                _journal_path(recovery_target)
            )
        else:
            raise ValueError
        _unlink_and_fsync(pointer_path)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(
            "E_ADOPT_RECOVERY_UNKNOWN: adoption transaction is ambiguous"
        ) from error


def _validate_approved_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != 2 or plan.get("operation") != "adopt":
        raise ValueError("E_ADOPT_PLAN: unsupported adoption plan")
    plan_core = {
        key: value
        for key, value in plan.items()
        if key not in {"plan_id", "command", "ok", "manual_actions"}
    }
    if plan.get("plan_id") != contract_digest(plan_core):
        raise ValueError("E_ADOPT_PLAN: plan content does not match plan_id")
    if plan.get("ok") is not True or plan.get("preflight_errors"):
        raise ValueError("E_ADOPT_PREFLIGHT: approved plan is not applicable")


def _validate_upgrade_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != 2 or plan.get("operation") != "upgrade":
        raise ValueError("E_UPGRADE_PLAN: unsupported upgrade plan")
    core = {
        key: value
        for key, value in plan.items()
        if key not in {"plan_id", "command", "ok", "manual_actions"}
    }
    if plan.get("plan_id") != contract_digest(core):
        raise ValueError("E_UPGRADE_PLAN: plan content does not match plan_id")
    if plan.get("ok") is not True or plan.get("preflight_errors"):
        raise ValueError("E_UPGRADE_PREFLIGHT: approved plan is not applicable")


def _restore_records(root: Path, records: list[Mapping[str, Any]]) -> None:
    for record in reversed(records):
        path = _safe_target(root, str(record["path"]))
        backup = record.get("backup")
        if backup:
            backup_path = _safe_target(
                worktree_git_dir(root), str(backup)
            )
            _durable_copy(
                backup_path,
                path,
                suffix=".codex-restore",
                expected_digest=str(record["before_digest"]),
            )
        else:
            _unlink_if_present_and_fsync(path)


def _recover_failed_transaction(
    *,
    original_error: Exception,
    target_root: Path,
    external_state: Mapping[str, Any],
    records: list[Mapping[str, Any]],
    staging_suffix: str,
) -> None:
    failures: list[Exception] = []
    for recovery in (
        lambda: _restore_records(target_root, records),
        lambda: _restore_external_state(target_root, external_state),
    ):
        try:
            recovery()
        except Exception as error:
            failures.append(error)
    for record in records:
        try:
            path = _safe_target(target_root, str(record["path"]))
            _unlink_if_present_and_fsync(
                path.with_suffix(path.suffix + staging_suffix)
            )
        except Exception as error:
            failures.append(error)
    if failures:
        grouped = ExceptionGroup(
            "adoption failure and recovery failures",
            [original_error, *failures],
        )
        raise ValueError(
            "E_ADOPT_RECOVERY_FAILED: recovery remains pending"
        ) from grouped


def adoption_apply(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Apply an approved plan transactionally with automatic failure recovery."""

    _validate_approved_plan(plan)
    source_root = discover_repository(Path(str(plan["source"])))
    target_root = discover_repository(Path(str(plan["target"])))
    with _ProcessLock(_lock_path(target_root)):
        _recover_owner_transaction(target_root)
        journal = _journal_path(target_root)
        if journal.exists():
            current = json.loads(journal.read_text(encoding="utf-8"))
            if current.get("status") == "applied":
                if current.get("plan_id") == plan.get("plan_id"):
                    verified = adoption_verify(target_root)
                    if verified["ok"]:
                        return {
                            **verified,
                            "command": "adopt-apply",
                            "idempotent": True,
                        }
                raise ValueError("E_ADOPT_ALREADY_APPLIED: use upgrade for a new plan")
            if current.get("status") == "preparing":
                _restore_records(target_root, current.get("records", []))
                current["status"] = "recovered"
                _atomic_json(journal, current)
        current_plan = adoption_plan(
            source_root,
            target_root,
            base_branch=str(plan["target_git"]["base_branch"]),
            remote=str(plan["target_git"]["remote"]),
            allow_dirty_source=bool(plan.get("allow_dirty_source")),
        )
        if current_plan.get("plan_id") != plan.get("plan_id"):
            raise ValueError("E_ADOPT_PLAN_STALE: source or target changed after plan")
        rendered = _render_distribution(
            source_root, target_root, git_facts=plan["target_git"]
        )
        installed_snapshot, snapshot_files = _installed_snapshot(
            source_root,
            target_root,
            source_commit=str(plan["source_commit"]),
            git_facts=plan["target_git"],
            rendered=rendered,
        )
        if installed_snapshot != plan.get("installed_snapshot"):
            raise ValueError(
                "E_ADOPT_PLAN_STALE: installed snapshot changed after plan"
            )
        _snapshot_installs_directory(
            Path(str(installed_snapshot["common_git_dir"])),
            create=True,
        )
        config_changes = plan.get("git_config_changes")
        if (
            not isinstance(config_changes, list)
            or len(config_changes) != 1
            or not isinstance(config_changes[0], Mapping)
        ):
            raise ValueError("E_ADOPT_PLAN: Git config change is invalid")
        config_change = dict(config_changes[0])
        snapshot_preexisting = Path(
            str(installed_snapshot["path"])
        ).exists()
        if snapshot_preexisting and not _snapshot_is_valid(
            target_root, installed_snapshot
        ):
            raise ValueError(
                "E_ADOPT_SNAPSHOT_DRIFT: planned snapshot path has drift"
            )
        external_state = {
            "git_config_change": config_change,
            "snapshot": {
                **installed_snapshot,
                "created": not snapshot_preexisting,
            },
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        _control_state_directory(
            worktree_git_dir(target_root),
            "backups",
            create=True,
            error_code="E_ADOPT_RECOVERY_UNKNOWN",
        )
        records: list[dict[str, Any]] = []
        for change in plan["changes"]:
            relative = str(change["path"])
            target_path = _safe_target(target_root, relative)
            if _digest(target_path) != change["before_digest"]:
                raise ValueError(f"E_ADOPT_PLAN_STALE: target changed: {relative}")
            backup_relative: str | None = None
            if target_path.is_file():
                backup_relative = f"codex-control-plane/backups/{stamp}/{relative}"
                backup = _safe_target(worktree_git_dir(target_root), backup_relative)
                _durable_copy(
                    target_path,
                    backup,
                    suffix=".codex-backup",
                    expected_digest=str(change["before_digest"]),
                )
                if _digest(backup) != change["before_digest"]:
                    raise ValueError(f"E_ADOPT_BACKUP: backup failed: {relative}")
            records.append(
                {
                    "path": relative,
                    "before_digest": change["before_digest"],
                    "installed_digest": change["after_digest"],
                    "backup": backup_relative,
                }
            )
        state = {
            "schema_version": 2,
            "status": "preparing",
            "plan_id": plan["plan_id"],
            "source_commit": plan["source_commit"],
            "source_manifest_digest": plan["source_manifest_digest"],
            "records": records,
            "git_config_changes": [config_change],
            "installed_snapshot": installed_snapshot,
            "snapshot_records": [
                {
                    **installed_snapshot,
                    "created": not snapshot_preexisting,
                }
            ],
            "initial_git_config_values": list(
                config_change["previous_local_values"]
            ),
            "warnings": list(plan.get("warnings", [])),
        }
        transaction = _begin_transaction(
            target_root,
            operation="adopt",
            records=records,
            external_state=external_state,
        )
        _atomic_json(journal, state)
        _advance_transaction(transaction, status="preparing", state=state)
        try:
            if (
                not _git_config_precondition_matches(
                    target_root, config_change
                )
                or _default_executable_hooks(target_root)
            ):
                raise ValueError(
                    "E_ADOPT_PLAN_STALE: Git hook state changed before apply"
                )
            for record in records:
                path = _safe_target(target_root, str(record["path"]))
                mode = (
                    0o755
                    if path.name
                    in {
                        "control-plane",
                        "control_plane_hook.py",
                        "pre-commit",
                        "pre-push",
                    }
                    else 0o644
                )
                _durable_replace_bytes(
                    path,
                    rendered[str(record["path"])],
                    suffix=".codex-new",
                    expected_digest=str(record["installed_digest"]),
                    mode=mode,
                )
            _publish_install_snapshot(
                target_root, installed_snapshot, snapshot_files
            )
            if (
                not _git_config_precondition_matches(
                    target_root, config_change
                )
                or _default_executable_hooks(target_root)
            ):
                raise ValueError(
                    "E_ADOPT_PLAN_STALE: Git hook state changed during apply"
                )
            _set_local_hooks_path(
                target_root, str(config_change["planned_value"])
            )
            if any(
                _digest(_safe_target(target_root, str(record["path"])))
                != record["installed_digest"]
                for record in records
            ):
                raise ValueError("E_ADOPT_VERIFY: installed files do not match plan")
            if (
                not _snapshot_is_valid(target_root, installed_snapshot)
                or not _git_config_change_is_applied(
                    target_root, config_change
                )
            ):
                raise ValueError(
                    "E_ADOPT_VERIFY: installed guard state does not match plan"
                )
        except Exception as error:
            _recover_failed_transaction(
                original_error=error,
                target_root=target_root,
                external_state=external_state,
                records=records,
                staging_suffix=".codex-new",
            )
            state["status"] = "failed_rolled_back"
            _atomic_json(journal, state)
            _commit_transaction(target_root, transaction, state=state)
            raise
        state["status"] = "applied"
        _atomic_json(journal, state)
        _commit_transaction(target_root, transaction, state=state)
        return {
            "schema_version": 2,
            "command": "adopt-apply",
            "ok": True,
            "idempotent": False,
            "plan_id": plan["plan_id"],
            "records": records,
        }


def upgrade_apply(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Apply an immutable upgrade plan without losing the original rollback."""

    _validate_upgrade_plan(plan)
    source_root = discover_repository(Path(str(plan["source"])))
    target_root = discover_repository(Path(str(plan["target"])))
    journal = _journal_path(target_root)
    with _ProcessLock(_lock_path(target_root)):
        _recover_owner_transaction(target_root)
        status = adoption_status(target_root)
        if status.get("status") == "upgrading":
            _restore_records(target_root, list(status.get("upgrade_records", [])))
            previous = status.get("previous_state")
            if not isinstance(previous, Mapping):
                raise ValueError("E_UPGRADE_RECOVERY: prior state is unavailable")
            _atomic_json(journal, previous)
            status = adoption_status(target_root)
        if status.get("status") != "applied":
            raise ValueError("E_UPGRADE_NOT_APPLIED: no applied installation")
        if status.get("plan_id") != plan.get("from_plan_id"):
            if status.get("plan_id") == plan.get("plan_id"):
                verified = adoption_verify(target_root)
                if verified["ok"]:
                    return {
                        **verified,
                        "command": "upgrade-apply",
                        "idempotent": True,
                    }
            raise ValueError("E_UPGRADE_STALE: installed version changed")
        current = upgrade_plan(
            source_root,
            target_root,
            base_branch=str(plan["target_git"]["base_branch"]),
            remote=str(plan["target_git"]["remote"]),
            allow_dirty_source=bool(plan.get("allow_dirty_source")),
        )
        if current.get("plan_id") != plan.get("plan_id"):
            raise ValueError("E_UPGRADE_STALE: source or target changed after plan")
        rendered = _render_distribution(
            source_root, target_root, git_facts=plan["target_git"]
        )
        installed_snapshot, snapshot_files = _installed_snapshot(
            source_root,
            target_root,
            source_commit=str(plan["source_commit"]),
            git_facts=plan["target_git"],
            rendered=rendered,
        )
        if installed_snapshot != plan.get("installed_snapshot"):
            raise ValueError(
                "E_UPGRADE_STALE: installed snapshot changed after plan"
            )
        _snapshot_installs_directory(
            Path(str(installed_snapshot["common_git_dir"])),
            create=True,
        )
        config_changes = plan.get("git_config_changes")
        if (
            not isinstance(config_changes, list)
            or len(config_changes) != 1
            or not isinstance(config_changes[0], Mapping)
        ):
            raise ValueError("E_UPGRADE_PLAN: Git config change is invalid")
        config_change = dict(config_changes[0])
        snapshot_preexisting = Path(
            str(installed_snapshot["path"])
        ).exists()
        if snapshot_preexisting and not _snapshot_is_valid(
            target_root, installed_snapshot
        ):
            raise ValueError(
                "E_ADOPT_SNAPSHOT_DRIFT: planned snapshot path has drift"
            )
        external_state = {
            "git_config_change": config_change,
            "snapshot": {
                **installed_snapshot,
                "created": not snapshot_preexisting,
            },
        }
        for change in plan["changes"]:
            if _digest(
                _safe_target(target_root, str(change["path"]))
            ) != change["before_digest"]:
                raise ValueError(
                    f"E_UPGRADE_STALE: target changed: {change['path']}"
                )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        _control_state_directory(
            worktree_git_dir(target_root),
            "upgrades",
            create=True,
            error_code="E_ADOPT_RECOVERY_UNKNOWN",
        )
        upgrade_records: list[dict[str, Any]] = []
        for change in plan["changes"]:
            relative = str(change["path"])
            path = _safe_target(target_root, relative)
            backup_relative: str | None = None
            if path.is_file():
                backup_relative = (
                    f"codex-control-plane/upgrades/{stamp}/{relative}"
                )
                backup = _safe_target(
                    worktree_git_dir(target_root), backup_relative
                )
                _durable_copy(
                    path,
                    backup,
                    suffix=".codex-backup",
                    expected_digest=str(change["before_digest"]),
                )
                if _digest(backup) != change["before_digest"]:
                    raise ValueError(f"E_UPGRADE_BACKUP: {relative}")
            upgrade_records.append(
                {
                    "path": relative,
                    "before_digest": change["before_digest"],
                    "installed_digest": change["after_digest"],
                    "backup": backup_relative,
                }
            )
        previous_state = {
            key: value
            for key, value in status.items()
            if key not in {"command", "ok"}
        }
        upgrading = {
            "schema_version": 2,
            "status": "upgrading",
            "plan_id": plan["plan_id"],
            "previous_state": previous_state,
            "upgrade_records": upgrade_records,
            "git_config_changes": [config_change],
            "installed_snapshot": installed_snapshot,
        }
        transaction = _begin_transaction(
            target_root,
            operation="upgrade",
            records=upgrade_records,
            previous_state=previous_state,
            external_state=external_state,
        )
        _atomic_json(journal, upgrading)
        _advance_transaction(
            transaction, status="upgrading", state=upgrading
        )
        try:
            if (
                not _git_config_precondition_matches(
                    target_root, config_change
                )
                or _default_executable_hooks(target_root)
            ):
                raise ValueError(
                    "E_UPGRADE_STALE: Git hook state changed before upgrade"
                )
            for record in upgrade_records:
                path = _safe_target(target_root, str(record["path"]))
                mode = (
                    0o755
                    if path.name
                    in {
                        "control-plane",
                        "control_plane_hook.py",
                        "pre-commit",
                        "pre-push",
                    }
                    else 0o644
                )
                _durable_replace_bytes(
                    path,
                    rendered[str(record["path"])],
                    suffix=".codex-upgrade",
                    expected_digest=str(record["installed_digest"]),
                    mode=mode,
                )
            _publish_install_snapshot(
                target_root, installed_snapshot, snapshot_files
            )
            if (
                not _git_config_precondition_matches(
                    target_root, config_change
                )
                or _default_executable_hooks(target_root)
            ):
                raise ValueError(
                    "E_UPGRADE_STALE: Git hook state changed during upgrade"
                )
            _set_local_hooks_path(
                target_root, str(config_change["planned_value"])
            )
            if (
                any(
                    _digest(
                        _safe_target(
                            target_root, str(record["path"])
                        )
                    )
                    != record["installed_digest"]
                    for record in upgrade_records
                )
                or not _snapshot_is_valid(
                    target_root, installed_snapshot
                )
                or not _git_config_change_is_applied(
                    target_root, config_change
                )
            ):
                raise ValueError(
                    "E_UPGRADE_VERIFY: upgraded guard state does not match plan"
                )
        except Exception as error:
            _recover_failed_transaction(
                original_error=error,
                target_root=target_root,
                external_state=external_state,
                records=upgrade_records,
                staging_suffix=".codex-upgrade",
            )
            _atomic_json(journal, previous_state)
            _commit_transaction(
                target_root, transaction, state=previous_state
            )
            raise
        original_records = {
            str(record["path"]): dict(record)
            for record in previous_state["records"]
        }
        for record in upgrade_records:
            relative = str(record["path"])
            if relative in original_records:
                original_records[relative]["installed_digest"] = record[
                    "installed_digest"
                ]
            else:
                original_records[relative] = {
                    "path": relative,
                    "before_digest": None,
                    "installed_digest": record["installed_digest"],
                    "backup": None,
                }
        snapshot_records = [
            dict(item)
            for item in previous_state.get("snapshot_records", [])
            if isinstance(item, Mapping)
        ]
        if not any(
            item.get("manifest_digest")
            == installed_snapshot["manifest_digest"]
            for item in snapshot_records
        ):
            snapshot_records.append(
                {
                    **installed_snapshot,
                    "created": not snapshot_preexisting,
                }
            )
        final = {
            "schema_version": 2,
            "status": "applied",
            "plan_id": plan["plan_id"],
            "source_commit": plan["source_commit"],
            "source_manifest_digest": plan["source_manifest_digest"],
            "records": [
                original_records[path] for path in sorted(original_records)
            ],
            "git_config_changes": [config_change],
            "installed_snapshot": installed_snapshot,
            "snapshot_records": snapshot_records,
            "initial_git_config_values": list(
                previous_state.get("initial_git_config_values", [])
            ),
            "warnings": list(plan.get("warnings", [])),
            "upgrade_history": [
                *previous_state.get("upgrade_history", []),
                {
                    "from_plan_id": plan["from_plan_id"],
                    "to_plan_id": plan["plan_id"],
                    "backup_stamp": stamp,
                },
            ],
        }
        _atomic_json(journal, final)
        _commit_transaction(target_root, transaction, state=final)
        return {
            "schema_version": 2,
            "command": "upgrade-apply",
            "ok": True,
            "idempotent": False,
            "plan_id": plan["plan_id"],
        }


def adoption_status(target: Path) -> dict[str, Any]:
    root = discover_repository(target)
    journal = _journal_path(root)
    if not journal.exists():
        return {
            "schema_version": 2,
            "command": "adopt-status",
            "ok": True,
            "status": "not_applied",
        }
    state = json.loads(journal.read_text(encoding="utf-8"))
    return {
        "schema_version": 2,
        "command": "adopt-status",
        "ok": True,
        **state,
    }


def adoption_verify(target: Path) -> dict[str, Any]:
    root = discover_repository(target)
    status = adoption_status(root)
    if status.get("status") != "applied":
        return {
            "schema_version": 2,
            "command": "adopt-verify",
            "ok": False,
            "errors": [
                {
                    "code": "E_ADOPT_NOT_APPLIED",
                    "message": "No applied adoption journal.",
                }
            ],
        }
    drift = [
        str(record["path"])
        for record in status["records"]
        if _digest(_safe_target(root, str(record["path"])))
        != record["installed_digest"]
    ]
    snapshot = status.get("installed_snapshot")
    if (
        not isinstance(snapshot, Mapping)
        or not _snapshot_is_valid(root, snapshot)
    ):
        drift.append("<installed-snapshot>")
    config_changes = status.get("git_config_changes")
    if (
        not isinstance(config_changes, list)
        or len(config_changes) != 1
        or not isinstance(config_changes[0], Mapping)
        or not _git_config_change_is_applied(root, config_changes[0])
    ):
        drift.append("<git-config:core.hooksPath>")
    return {
        "schema_version": 2,
        "command": "adopt-verify",
        "ok": not drift,
        "plan_id": status.get("plan_id"),
        "drift": sorted(drift),
        "errors": (
            []
            if not drift
            else [
                {
                    "code": "E_ADOPT_DRIFT",
                    "message": "Managed files changed: " + ", ".join(sorted(drift)),
                }
            ]
        ),
    }


def adoption_rollback(target: Path) -> dict[str, Any]:
    root = discover_repository(target)
    with _ProcessLock(_lock_path(root)):
        _recover_owner_transaction(root)
        status = adoption_status(root)
        if status.get("status") not in {"applied", "rolling_back"}:
            raise ValueError("E_ADOPT_NOT_APPLIED: no adoption to roll back")
        records = list(status["records"])
        if status.get("status") == "applied":
            drift = [
                str(record["path"])
                for record in records
                if _digest(_safe_target(root, str(record["path"])))
                != record["installed_digest"]
            ]
            backup_errors = [
                str(record["path"])
                for record in records
                if record.get("backup")
                and _digest(
                    _safe_target(
                        worktree_git_dir(root), str(record["backup"])
                    )
                )
                != record["before_digest"]
            ]
            snapshot_records = status.get("snapshot_records")
            config_changes = status.get("git_config_changes")
            initial_config = status.get("initial_git_config_values")
            external_errors = []
            if (
                not isinstance(snapshot_records, list)
                or not all(
                    isinstance(item, Mapping)
                    and _snapshot_is_valid(root, item)
                    for item in snapshot_records
                )
                or not isinstance(config_changes, list)
                or len(config_changes) != 1
                or not isinstance(config_changes[0], Mapping)
                or not _git_config_change_is_applied(
                    root, config_changes[0]
                )
                or not isinstance(initial_config, list)
            ):
                external_errors.append("installed guard state")
            if drift or backup_errors or external_errors:
                raise ValueError(
                    "E_ADOPT_DRIFT: rollback preflight failed; no files changed"
                )
            state = {
                key: value
                for key, value in status.items()
                if key not in {"command", "ok"}
            }
            state["status"] = "rolling_back"
            _atomic_json(_journal_path(root), state)
        _restore_records(root, records)
        initial_config = status.get("initial_git_config_values", [])
        _restore_local_hook_config(root, list(initial_config))
        for snapshot_record in reversed(
            list(status.get("snapshot_records", []))
        ):
            if snapshot_record.get("created") is True:
                _remove_snapshot_tree(
                    Path(str(snapshot_record["path"])),
                    common_git_dir=Path(
                        str(snapshot_record["common_git_dir"])
                    ),
                )
        state = {
            key: value
            for key, value in adoption_status(root).items()
            if key not in {"command", "ok"}
        }
        state["status"] = "rolled_back"
        _atomic_json(_journal_path(root), state)
        return {
            "schema_version": 2,
            "command": "adopt-rollback",
            "ok": True,
            "status": "rolled_back",
        }
