from __future__ import annotations

import copy
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from control_plane.policy import load_policy


FIXTURE_POLICY = Path(__file__).parent / "fixtures" / "valid-policy.toml"
GIT = "/usr/bin/git"
GIT_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git_command(*arguments: str) -> list[str]:
    return [
        GIT,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        *arguments,
    ]


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        _git_command(*arguments),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=GIT_ENV,
        timeout=10,
    )
    return completed.stdout.strip()


def install_external_diff_driver(
    repository: Path,
    fixture_root: Path,
    *,
    tracked_path: str,
    driver_name: str,
) -> Path:
    """Install a real external diff driver and leave one tracked modification."""

    marker = fixture_root / f"{driver_name}-executed"
    driver = fixture_root / f"{driver_name}.sh"
    driver.write_text(
        "#!/bin/sh\n"
        f": > {shlex.quote(str(marker))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    driver.chmod(0o700)
    (repository / ".gitattributes").write_text(
        f"{tracked_path} diff={driver_name}\n", encoding="utf-8"
    )
    git(repository, "add", ".gitattributes")
    git(repository, "commit", "-m", f"test: {driver_name} attributes")
    git(repository, "config", f"diff.{driver_name}.command", str(driver))
    (repository / tracked_path).write_text(
        "changed through builtin diff\n", encoding="utf-8"
    )
    return marker


class GitScenario:
    def __init__(self, base_branch: str = "main") -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.remote = self.root / "origin.git"
        self.repo = self.root / "work"
        self.base_branch = base_branch

        subprocess.run(
            _git_command("init", "--bare", str(self.remote)),
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=GIT_ENV,
            timeout=10,
        )
        subprocess.run(
            _git_command("init", "-b", base_branch, str(self.repo)),
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=GIT_ENV,
            timeout=10,
        )
        git(self.repo, "config", "user.name", "Control Plane Tests")
        git(self.repo, "config", "user.email", "control-plane@example.invalid")
        (self.repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
        git(self.repo, "add", "baseline.txt")
        git(self.repo, "commit", "-m", "test: baseline")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-u", "origin", base_branch)
        git(self.remote, "symbolic-ref", "HEAD", f"refs/heads/{base_branch}")

    def close(self) -> None:
        self._temp.cleanup()

    def policy(self) -> dict[str, Any]:
        policy = copy.deepcopy(load_policy(FIXTURE_POLICY))
        policy["git"]["base_branch"] = self.base_branch
        return policy

    def checkout_feature(self, name: str = "feature/test") -> None:
        git(self.repo, "switch", "-c", name)

    def advance_remote_base(self) -> None:
        secondary = self.root / "secondary"
        subprocess.run(
            _git_command("clone", str(self.remote), str(secondary)),
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=GIT_ENV,
            timeout=10,
        )
        git(secondary, "config", "user.name", "Control Plane Tests")
        git(secondary, "config", "user.email", "control-plane@example.invalid")
        (secondary / "remote-change.txt").write_text("new base\n", encoding="utf-8")
        git(secondary, "add", "remote-change.txt")
        git(secondary, "commit", "-m", "test: advance remote base")
        git(secondary, "push", "origin", self.base_branch)
        git(self.repo, "fetch", "origin")


def create_unborn_repository() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name) / "unborn"
    subprocess.run(
        _git_command("init", "-b", "main", str(repo)),
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=GIT_ENV,
        timeout=10,
    )
    return temp, repo
