"""Serialized, proportional local verification for Control Plane Core."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import stat
from typing import Callable, Generic, TypeVar

from control_plane.repository import (
    discover_repository,
    git_common_dir,
    open_private_state_lock,
)


T = TypeVar("T")


@dataclass(frozen=True)
class VerificationResult(Generic[T]):
    status: str
    error_code: str | None
    executed: bool
    value: T | None
    consumes_reframe: bool
    authorizes: bool = False


class VerificationMutex:
    """One nonblocking full verifier for one Git common directory."""

    def __init__(self, repository: Path | str) -> None:
        repo = discover_repository(Path(repository))
        self.common_git_dir = git_common_dir(repo)
        self.root = self.common_git_dir / "codex-control-plane-core" / "locks"
        self.path = self.root / "verification.lock"
        self.descriptor: int | None = None
        self.acquired = False

    def __enter__(self) -> bool:
        self.descriptor = open_private_state_lock(
            self.common_git_dir,
            ("codex-control-plane-core", "locks"),
            "verification.lock",
            code="E_VERIFICATION_LOCK",
        )
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.acquired = False
            return False
        self.acquired = True
        return True

    def __exit__(self, *_: object) -> None:
        if self.descriptor is None:
            return
        if self.acquired:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        os.close(self.descriptor)
        self.descriptor = None
        self.acquired = False


def run_serialized_verification(
    repository: Path | str,
    runner: Callable[[], T],
) -> VerificationResult[T]:
    """Run only after the mutex; contention executes no Git or test command."""

    with VerificationMutex(repository) as acquired:
        if not acquired:
            return VerificationResult(
                status="UNKNOWN",
                error_code="E_VERIFICATION_BUSY",
                executed=False,
                value=None,
                consumes_reframe=False,
                authorizes=False,
            )
        try:
            value = runner()
        except Exception:
            return VerificationResult(
                status="FAIL",
                error_code="E_VERIFICATION_FAILED",
                executed=True,
                value=None,
                consumes_reframe=False,
                authorizes=False,
            )
        return VerificationResult(
            status="PASS",
            error_code=None,
            executed=True,
            value=value,
            consumes_reframe=False,
            authorizes=False,
        )
