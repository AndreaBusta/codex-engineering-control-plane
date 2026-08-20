from __future__ import annotations

import os
from pathlib import Path
import shutil
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
