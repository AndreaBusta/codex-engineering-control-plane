"""Repository discovery and worktree-local state paths."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import stat
import subprocess
import time
from typing import Iterator, Sequence


_REPOSITORY_REDIRECT_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)

_TRUSTED_GIT = Path("/usr/bin/git")
_CLOSED_GIT_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.filemode=true",
    "-c",
    "core.excludesFile=/dev/null",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.external=",
    "-c",
    "color.ui=false",
)
_MAX_GIT_FILTER_PATH_BYTES = 1_048_576
_MAX_GIT_FILTER_OUTPUT_BYTES = 4_194_304
_MAX_GIT_FILTER_PATHS = 20_000
_GIT_FILTER_POLL_SECONDS = 0.05
_GIT_FILTER_REAP_SECONDS = 0.25
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600


class RepositoryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _state_error(code: str, message: str, error: OSError | None = None) -> ValueError:
    failure = ValueError(f"{code}: {message}")
    if error is not None:
        failure.__cause__ = error
    return failure


def _safe_state_component(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\0" in value
    ):
        raise ValueError("E_STATE_PATH_COMPONENT: state path component is unsafe")
    return value


def _validate_state_directory(
    metadata: os.stat_result,
    *,
    code: str,
    anchor: bool,
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    unsafe_mode = mode & (0o022 if anchor else 0o077)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink < 1
        or unsafe_mode
    ):
        raise _state_error(code, "state directory ownership or mode is unsafe")


@contextmanager
def private_state_directory(
    anchor: Path | str,
    components: Sequence[str],
    *,
    create: bool,
    missing_ok: bool = False,
    code: str,
) -> Iterator[tuple[Path, int] | None]:
    """Open a private state directory by descriptor-relative no-follow descent."""

    root = Path(anchor)
    try:
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise _state_error(code, "state anchor is not canonical")
    except OSError as error:
        raise _state_error(code, "state anchor is unavailable", error) from error
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        current = os.open(root, flags)
    except OSError as error:
        raise _state_error(code, "state anchor cannot be opened safely", error) from error
    path = root
    try:
        _validate_state_directory(os.fstat(current), code=code, anchor=True)
        for raw_component in components:
            component = _safe_state_component(raw_component)
            try:
                child = os.open(component, flags, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    if missing_ok:
                        yield None
                        return
                    raise _state_error(code, "state directory is unavailable") from None
                try:
                    os.mkdir(component, _PRIVATE_DIRECTORY_MODE, dir_fd=current)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise _state_error(
                        code, "state directory cannot be created safely", error
                    ) from error
                try:
                    child = os.open(component, flags, dir_fd=current)
                except OSError as error:
                    raise _state_error(
                        code, "state directory cannot be opened safely", error
                    ) from error
            except OSError as error:
                raise _state_error(
                    code, "state directory cannot be opened safely", error
                ) from error
            try:
                _validate_state_directory(os.fstat(child), code=code, anchor=False)
            except Exception:
                os.close(child)
                raise
            os.close(current)
            current = child
            path = path / component
        yield path, current
    finally:
        os.close(current)


def ensure_private_state_directory(
    anchor: Path | str,
    components: Sequence[str],
    *,
    create: bool,
    missing_ok: bool = False,
    code: str,
) -> Path | None:
    with private_state_directory(
        anchor,
        components,
        create=create,
        missing_ok=missing_ok,
        code=code,
    ) as opened:
        return None if opened is None else opened[0]


def open_private_state_lock(
    anchor: Path | str,
    directory_components: Sequence[str],
    filename: str,
    *,
    code: str,
) -> int:
    """Create or open one private regular lock without following any link."""

    name = _safe_state_component(filename)
    with private_state_directory(
        anchor,
        directory_components,
        create=True,
        code=code,
    ) as opened:
        assert opened is not None
        _, directory = opened
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(
                name, flags, _PRIVATE_FILE_MODE, dir_fd=directory
            )
        except OSError as error:
            raise _state_error(code, "lock cannot be opened safely", error) from error
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
            ):
                raise _state_error(code, "lock ownership, links, or mode are unsafe")
        except Exception:
            os.close(descriptor)
            raise
        return descriptor


def git_environment() -> dict[str, str]:
    """Preserve user authentication while removing repository redirection."""

    return {
        key: value
        for key, value in os.environ.items()
        if key not in _REPOSITORY_REDIRECT_ENV
    }


def trusted_git_executable() -> str:
    """Return the immutable system Git used for subject observations."""

    if (
        not _TRUSTED_GIT.is_absolute()
        or _TRUSTED_GIT.is_symlink()
        or not _TRUSTED_GIT.is_file()
        or not os.access(_TRUSTED_GIT, os.X_OK)
    ):
        raise OSError("trusted Git executable is unavailable")
    return str(_TRUSTED_GIT)


def _normalize_trusted_git_arguments(
    arguments: Sequence[str],
) -> tuple[str, ...]:
    normalized = tuple(arguments)
    if normalized[:1] != ("diff",):
        return normalized
    tail = normalized[1:]
    try:
        separator = tail.index("--")
    except ValueError:
        separator = len(tail)
    options = tuple(
        argument
        for argument in tail[:separator]
        if argument not in {"--no-ext-diff", "--no-textconv"}
    )
    suffix = tail[separator:]
    return (
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        *options,
        *suffix,
    )


def trusted_git_argv(
    repository: Path | str, arguments: Sequence[str]
) -> list[str]:
    """Build a closed read-only Git observation command."""

    normalized_arguments = _normalize_trusted_git_arguments(arguments)
    return [
        trusted_git_executable(),
        *_CLOSED_GIT_CONFIG,
        "-C",
        str(repository),
        *normalized_arguments,
    ]


def trusted_git_environment(
    *, index_file: Path | str | None = None
) -> dict[str, str]:
    """Close Git redirects/config while allowing one explicit index binding."""

    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_GRAFT_FILE": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GCM_INTERACTIVE": "never",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }
    if index_file is not None:
        candidate = Path(index_file)
        try:
            parent = candidate.parent.resolve(strict=True)
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                metadata = None
        except OSError as error:
            raise ValueError(
                "E_GIT_INDEX_FILE: explicit index path is unavailable"
            ) from error
        if (
            not candidate.is_absolute()
            or not parent.is_dir()
            or (
                metadata is not None
                and (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISREG(metadata.st_mode)
                )
            )
        ):
            raise ValueError(
                "E_GIT_INDEX_FILE: explicit index path is unsafe"
            )
        environment["GIT_INDEX_FILE"] = str(candidate)
    return environment


def _terminate_filter_probe(process: subprocess.Popen[bytes]) -> None:
    """Kill the whole probe session and synchronously reap its leader."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_GIT_FILTER_REAP_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=_GIT_FILTER_REAP_SECONDS)


def _bounded_filter_probe(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    input_data: bytes | None,
    output_limit: int,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    """Run one closed probe without ever retaining output beyond its cap."""

    if output_limit <= 0 or timeout <= 0:
        raise ValueError("E_GIT_FILTER: clean-filter observation failed")
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    returncode: int | None = None
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("probe output pipes are unavailable")
        selector = selectors.DefaultSelector()
        for stream, target in ((process.stdout, stdout), (process.stderr, stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, ("read", target))
        payload = memoryview(input_data or b"")
        payload_offset = 0
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, ("write", None))
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(list(argv), timeout)
            for key, _ in selector.select(min(_GIT_FILTER_POLL_SECONDS, remaining)):
                operation, target = key.data
                if operation == "write":
                    try:
                        written = os.write(
                            key.fd,
                            payload[payload_offset : payload_offset + 65_536],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        written = len(payload) - payload_offset
                    payload_offset += written
                    if payload_offset >= len(payload):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                allowance = output_limit + 1 - len(stdout) - len(stderr)
                if allowance <= 0:
                    raise RuntimeError("probe output exceeded its byte limit")
                try:
                    chunk = os.read(key.fd, min(65_536, allowance))
                except BlockingIOError:
                    continue
                if chunk:
                    target.extend(chunk)
                    if len(stdout) + len(stderr) > output_limit:
                        raise RuntimeError("probe output exceeded its byte limit")
                    continue
                selector.unregister(key.fileobj)
                key.fileobj.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        returncode = process.wait(timeout=remaining)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError("E_GIT_FILTER: clean-filter observation failed") from error
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            try:
                _terminate_filter_probe(process)
            except (OSError, subprocess.SubprocessError):
                pass
    return subprocess.CompletedProcess(
        list(argv), int(returncode), bytes(stdout), bytes(stderr)
    )


def assert_no_external_git_filters(
    repository: Path | str,
    paths: Sequence[str] | None = None,
    *,
    index_file: Path | str | None = None,
) -> None:
    """Fail before a worktree observation can invoke an attribute filter."""

    environment = trusted_git_environment(index_file=index_file)
    if paths is None:
        try:
            inventory = _bounded_filter_probe(
                trusted_git_argv(repository, ("ls-files", "-z")),
                cwd=Path(repository),
                environment=environment,
                input_data=None,
                output_limit=_MAX_GIT_FILTER_PATH_BYTES,
                timeout=10.0,
            )
        except ValueError as error:
            raise ValueError(
                "E_GIT_FILTER: clean-filter inventory is incomplete"
            ) from error
        raw_paths = tuple(
            item for item in inventory.stdout.split(b"\0") if item
        )
        if (
            inventory.returncode != 0
            or len(inventory.stdout) > _MAX_GIT_FILTER_PATH_BYTES
            or len(raw_paths) > _MAX_GIT_FILTER_PATHS
        ):
            raise ValueError(
                "E_GIT_FILTER: clean-filter inventory is incomplete"
            )
    else:
        if len(paths) > _MAX_GIT_FILTER_PATHS:
            raise ValueError(
                "E_GIT_FILTER: clean-filter inventory is incomplete"
            )
        encoded: list[bytes] = []
        for value in paths:
            if not isinstance(value, str):
                raise ValueError(
                    "E_GIT_FILTER: clean-filter inventory is incomplete"
                )
            pure = PurePosixPath(value)
            if (
                not value
                or pure.is_absolute()
                or ".." in pure.parts
                or pure.as_posix() != value
                or "\0" in value
            ):
                raise ValueError(
                    "E_GIT_FILTER: clean-filter inventory is incomplete"
                )
            try:
                encoded.append(value.encode("utf-8", errors="strict"))
            except UnicodeEncodeError as error:
                raise ValueError(
                    "E_GIT_FILTER: clean-filter inventory is incomplete"
                ) from error
        raw_paths = tuple(encoded)
    if not raw_paths:
        return
    payload = b"\0".join(raw_paths) + b"\0"
    if len(payload) > _MAX_GIT_FILTER_PATH_BYTES:
        raise ValueError(
            "E_GIT_FILTER: clean-filter inventory is incomplete"
        )
    try:
        completed = _bounded_filter_probe(
            trusted_git_argv(
                repository,
                ("check-attr", "--stdin", "-z", "filter", "diff"),
            ),
            cwd=Path(repository),
            environment=environment,
            input_data=payload,
            output_limit=_MAX_GIT_FILTER_OUTPUT_BYTES,
            timeout=10.0,
        )
    except ValueError as error:
        raise ValueError(
            "E_GIT_FILTER: clean-filter inventory is incomplete"
        ) from error
    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if (
        completed.returncode != 0
        or len(fields) != len(raw_paths) * 6
        or tuple(fields[0::3])
        != tuple(path for path in raw_paths for _ in range(2))
        or tuple(fields[1::3]) != (b"filter", b"diff") * len(raw_paths)
    ):
        raise ValueError(
            "E_GIT_FILTER: clean-filter inventory is incomplete"
        )
    filter_values = fields[2::6]
    diff_values = fields[5::6]
    if any(value not in {b"unspecified", b"unset"} for value in filter_values) or any(
        value not in {b"unspecified", b"unset", b"set"} for value in diff_values
    ):
        raise ValueError(
            "E_GIT_FILTER: external Git filters or diff drivers are not permitted"
        )


def _git(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [trusted_git_executable(), *_CLOSED_GIT_CONFIG, *arguments],
            cwd=path,
            check=False,
            capture_output=True,
            text=True,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(
            args=[str(_TRUSTED_GIT), *arguments],
            returncode=128,
            stdout="",
            stderr="",
        )


def discover_repository(path: Path) -> Path:
    """Find the Git worktree root before resolving project-owned files."""

    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not result.stdout.strip():
        raise RepositoryError(
            "E_GIT_NOT_REPOSITORY", "The target is not inside a Git worktree."
        )
    return Path(result.stdout.strip()).resolve()


def worktree_git_dir(path: Path) -> Path:
    """Return the Git dir unique to this worktree, not the shared common dir."""

    root = discover_repository(path)
    result = _git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    if result.returncode != 0 or not result.stdout.strip():
        raise RepositoryError(
            "E_GIT_DIR_UNKNOWN", "The worktree-specific Git dir is unavailable."
        )
    return Path(result.stdout.strip()).resolve()


def git_common_dir(path: Path) -> Path:
    """Return the canonical common Git dir shared by all registered worktrees."""

    root = discover_repository(path)
    result = _git(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RepositoryError(
            "E_GIT_COMMON_DIR_UNKNOWN",
            "The common Git dir is unavailable.",
        )
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    common = common.resolve()
    if common.is_symlink() or not common.is_dir():
        raise RepositoryError(
            "E_GIT_COMMON_DIR_UNKNOWN",
            "The common Git dir is invalid.",
        )
    return common
