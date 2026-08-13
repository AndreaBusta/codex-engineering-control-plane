"""One attested Python/Git/Node context for all Core verification."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import MappingProxyType
from typing import Mapping

from control_plane.contracts import contract_digest
from control_plane.repository import discover_repository, trusted_git_executable


_NODE_CANDIDATES = (
    Path("/opt/homebrew/bin/node"),
    Path("/usr/local/bin/node"),
    Path("/usr/bin/node"),
)
_MAX_VERSION_BYTES = 4_096
_PROBE_TIMEOUT_SECONDS = 5.0
_PROBE_POLL_SECONDS = 0.05
_PROBE_REAP_SECONDS = 0.25


def _terminate_probe_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the probe session, including descendants, and reap its leader."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_PROBE_REAP_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=_PROBE_REAP_SECONDS)


@dataclass(frozen=True)
class ExecutableAttestation:
    name: str
    path: Path
    realpath: Path
    device: int
    inode: int
    mode: int
    version: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "realpath": str(self.realpath),
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "version": self.version,
        }


@dataclass(frozen=True)
class ClosedExecutionContextV1:
    repository: Path
    temp_root: Path
    python: ExecutableAttestation
    git: ExecutableAttestation
    node: ExecutableAttestation | None
    environment: Mapping[str, str]
    probes: Mapping[str, bool]
    context_digest: str
    authorizes: bool = False

    def validate_executables(self) -> None:
        try:
            root = self.temp_root.lstat()
            config = (self.temp_root / "gitconfig").lstat()
        except OSError as error:
            raise ValueError("E_TOOLCHAIN_DRIFT: closed environment is unavailable") from error
        if (
            not stat.S_ISDIR(root.st_mode)
            or root.st_uid != os.geteuid()
            or root.st_nlink < 1
            or stat.S_IMODE(root.st_mode) != 0o700
            or not stat.S_ISREG(config.st_mode)
            or config.st_uid != os.geteuid()
            or config.st_nlink != 1
            or stat.S_IMODE(config.st_mode) != 0o600
            or config.st_size != 0
        ):
            raise ValueError("E_TOOLCHAIN_DRIFT: closed environment identity changed")
        for executable in (self.python, self.git, self.node):
            if executable is None:
                continue
            try:
                metadata = executable.path.stat()
                realpath = executable.path.resolve(strict=True)
            except OSError as error:
                raise ValueError(
                    f"E_TOOLCHAIN_DRIFT: {executable.name} became unavailable"
                ) from error
            if (
                realpath != executable.realpath
                or metadata.st_dev != executable.device
                or metadata.st_ino != executable.inode
                or stat.S_IMODE(metadata.st_mode) != executable.mode
            ):
                raise ValueError(
                    f"E_TOOLCHAIN_DRIFT: {executable.name} identity changed"
                )


def _regular_executable(path: Path, *, name: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"E_TOOLCHAIN_{name.upper()}: path is not absolute")
    try:
        realpath = path.resolve(strict=True)
        metadata = realpath.stat()
    except OSError as error:
        raise ValueError(f"E_TOOLCHAIN_{name.upper()}: executable is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode) or not os.access(realpath, os.X_OK):
        raise ValueError(f"E_TOOLCHAIN_{name.upper()}: path is not executable")
    return realpath


def _probe(
    path: Path,
    arguments: tuple[str, ...],
    *,
    environment: Mapping[str, str],
    name: str,
) -> str:
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    output = bytearray()
    returncode: int | None = None
    failure: BaseException | None = None
    try:
        process = subprocess.Popen(
            [str(path), *arguments],
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        if process.stdout is None:
            raise RuntimeError("probe stdout is unavailable")
        os.set_blocking(process.stdout.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + _PROBE_TIMEOUT_SECONDS
        pipe_open = True
        while True:
            if len(output) > _MAX_VERSION_BYTES:
                raise RuntimeError("probe output exceeded its byte limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    [str(path), *arguments], _PROBE_TIMEOUT_SECONDS
                )
            if selector.get_map():
                events = selector.select(min(_PROBE_POLL_SECONDS, remaining))
            else:
                time.sleep(min(_PROBE_POLL_SECONDS, remaining))
                events = []
            read_payload = False
            for key, _ in events:
                capacity = _MAX_VERSION_BYTES + 1 - len(output)
                if capacity <= 0:
                    raise RuntimeError("probe output exceeded its byte limit")
                try:
                    chunk = os.read(key.fd, min(65_536, capacity))
                except BlockingIOError:
                    continue
                if chunk:
                    output.extend(chunk)
                    read_payload = True
                else:
                    selector.unregister(key.fileobj)
                    pipe_open = False
            if len(output) > _MAX_VERSION_BYTES:
                raise RuntimeError("probe output exceeded its byte limit")
            if process.poll() is not None:
                if not pipe_open or not read_payload:
                    break
        returncode = process.wait(
            timeout=max(0.001, deadline - time.monotonic())
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        failure = error
    finally:
        if process is not None:
            try:
                _terminate_probe_group(process)
            except (OSError, subprocess.SubprocessError) as error:
                if failure is None:
                    failure = error
            if process.stdout is not None:
                process.stdout.close()
        if selector is not None:
            selector.close()
    if failure is not None:
        raise ValueError(f"E_TOOLCHAIN_{name.upper()}: probe failed") from failure
    if returncode != 0 or len(output) > _MAX_VERSION_BYTES:
        raise ValueError(f"E_TOOLCHAIN_{name.upper()}: probe failed")
    try:
        version = bytes(output).decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"E_TOOLCHAIN_{name.upper()}: version is invalid") from error
    if not version or "\x00" in version or "\n" in version:
        raise ValueError(f"E_TOOLCHAIN_{name.upper()}: version is invalid")
    return version


def _attest(
    name: str,
    path: Path,
    arguments: tuple[str, ...],
    environment: Mapping[str, str],
) -> ExecutableAttestation:
    realpath = _regular_executable(path, name=name)
    metadata = realpath.stat()
    version = _probe(realpath, arguments, environment=environment, name=name)
    return ExecutableAttestation(
        name=name,
        path=realpath,
        realpath=realpath,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=stat.S_IMODE(metadata.st_mode),
        version=version,
    )


def _node_path() -> Path:
    for candidate in _NODE_CANDIDATES:
        try:
            return _regular_executable(candidate, name="node")
        except ValueError:
            continue
    raise ValueError("E_TOOLCHAIN_NODE: trusted Node executable is unavailable")


def _private_temp_root(path: Path) -> Path:
    resolved = path.absolute()
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = resolved.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink < 1
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("E_TOOLCHAIN_TEMP: closed temp root is unsafe")
    config = resolved / "gitconfig"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(config, flags, 0o600)
    except OSError as error:
        raise ValueError(
            "E_TOOLCHAIN_TEMP: closed Git config must be freshly created"
        ) from error
    try:
        os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size != 0
        ):
            raise ValueError("E_TOOLCHAIN_TEMP: closed Git config is unsafe")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(
        resolved,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return resolved


def build_closed_execution_context(
    repository: Path | str,
    *,
    temp_root: Path,
) -> ClosedExecutionContextV1:
    """Resolve, bind, and probe every tool inside the final environment."""

    repo = discover_repository(Path(repository))
    closed_temp = _private_temp_root(Path(temp_root))
    python_path = _regular_executable(Path(sys.executable), name="python")
    git_path = _regular_executable(Path(trusted_git_executable()), name="git")
    node_path = _node_path()
    bin_directories: list[str] = []
    for path in (python_path.parent, git_path.parent, node_path.parent):
        value = str(path)
        if value not in bin_directories:
            bin_directories.append(value)
    environment = {
        "PATH": os.pathsep.join(bin_directories),
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(closed_temp / "gitconfig"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false",
        "SSH_ASKPASS": "/usr/bin/false",
        "TMPDIR": str(closed_temp),
    }
    python_probe = (
        "import sys;"
        "bad=any(n=='control_plane' or n.startswith('control_plane.') for n in sys.modules);"
        "print('.'.join(map(str,sys.version_info[:3])));"
        "raise SystemExit(3 if not sys.flags.no_site or bad else 0)"
    )
    python = _attest(
        "python",
        python_path,
        (
            "-I",
            "-S",
            "-B",
            "-X",
            "pycache_prefix=/dev/null",
            "-c",
            python_probe,
        ),
        environment,
    )
    git = _attest("git", git_path, ("--version",), environment)
    node = _attest("node", node_path, ("--version",), environment)
    nested_script = (
        "const c=require('child_process');"
        "const o=c.execFileSync(process.execPath,['--version'],{env:process.env});"
        "if(!o.toString().trim())process.exit(9);process.stdout.write(o);"
    )
    nested = _probe(
        node.path,
        ("-e", nested_script),
        environment=environment,
        name="node_nested",
    )
    probes = {
        "python": True,
        "python_no_site": True,
        "python_no_preloaded_core": True,
        "git": True,
        "node": True,
        "node_nested": bool(nested),
    }
    payload = {
        "schema_version": 1,
        "kind": "ClosedExecutionContextV1",
        "repository": str(repo),
        "temp_root": str(closed_temp),
        "python": python.as_dict(),
        "git": git.as_dict(),
        "node": node.as_dict(),
        "environment": environment,
        "probes": probes,
        "authorizes": False,
    }
    return ClosedExecutionContextV1(
        repository=repo,
        temp_root=closed_temp,
        python=python,
        git=git,
        node=node,
        environment=MappingProxyType(dict(environment)),
        probes=MappingProxyType(dict(probes)),
        context_digest=contract_digest(payload),
        authorizes=False,
    )


def authoritative_git_environment(
    context: ClosedExecutionContextV1,
) -> dict[str, str]:
    """Add replace-ref isolation only at an authoritative Git observation."""

    context.validate_executables()
    return {**context.environment, "GIT_NO_REPLACE_OBJECTS": "1"}
