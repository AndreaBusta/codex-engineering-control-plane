"""Repository discovery and worktree-local state paths."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
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
_DATALESS_FLAG = 0x40000000
_TRUSTED_PYTHON = Path("/usr/bin/python3")
_FCHDIR_GIT_WRAPPER = (
    "import os,sys;"
    "descriptor=int(sys.argv[1]);"
    "arguments=sys.argv[2:];"
    "os.fchdir(descriptor);"
    "os.execv(arguments[0],arguments)"
)
_BOUNDED_GIT_STATIC_ARGUMENTS = frozenset(
    {
        ("check-attr", "--stdin", "-z", "filter", "diff"),
        ("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"),
        ("ls-files", "-z"),
        ("ls-files", "--deleted", "-z"),
        ("ls-files", "--stage", "-z"),
        ("rev-parse", "--absolute-git-dir"),
        ("rev-parse", "--abbrev-ref", "HEAD"),
        ("rev-parse", "--path-format=absolute", "--git-common-dir"),
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "HEAD"),
        ("stash", "list"),
        ("status", "--porcelain", "-uno"),
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        ("worktree", "list", "--porcelain", "-z"),
    }
)


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


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        int(getattr(metadata, "st_flags", 0)),
    )


def _directory_binding_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Bind a path component without treating unrelated child churn as replacement."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        int(getattr(metadata, "st_flags", 0)),
    )


def _directory_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("directory observation deadline expired")


def _open_canonical_directory(
    path: Path | str,
    *,
    deadline: float | None,
) -> tuple[Path, int, os.stat_result]:
    candidate = Path(path)
    normalized = Path(os.path.normpath(os.fspath(candidate)))
    if not candidate.is_absolute() or normalized != candidate:
        raise OSError("directory path is not canonical")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(os.sep, flags)
    metadata = os.fstat(descriptor)
    try:
        for component in candidate.parts[1:]:
            _directory_deadline(deadline)
            before = os.stat(
                component,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            mode = stat.S_IMODE(before.st_mode)
            root_owned_sticky = (
                before.st_uid == 0 and bool(mode & stat.S_ISVTX)
            )
            if (
                not stat.S_ISDIR(before.st_mode)
                or before.st_uid not in {0, os.geteuid()}
                or before.st_nlink < 1
                or (mode & 0o022 and not root_owned_sticky)
                or int(getattr(before, "st_flags", 0)) & _DATALESS_FLAG
            ):
                raise OSError("directory component is unsafe")
            child = os.open(component, flags, dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                named = os.stat(
                    component,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                if (
                    _directory_binding_identity(before)
                    != _directory_binding_identity(opened)
                    or _directory_binding_identity(before)
                    != _directory_binding_identity(named)
                ):
                    raise OSError("directory component changed during open")
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            metadata = opened
        if metadata.st_uid != os.geteuid():
            raise OSError("final directory is not owned by the operator")
        return candidate, descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def observed_directory(
    path: Path | str,
    *,
    deadline: float | None = None,
) -> Iterator[tuple[Path, int, os.stat_result]]:
    """Retain one canonical owned directory opened without following links."""

    try:
        candidate, descriptor, opened = _open_canonical_directory(
            path,
            deadline=deadline,
        )
    except (OSError, TimeoutError, ValueError) as error:
        raise ValueError(
            "E_DIRECTORY_OBSERVATION: directory is not safely observable"
        ) from error
    try:
        yield candidate, descriptor, opened
    except BaseException:
        raise
    else:
        fresh_descriptor = -1
        try:
            _directory_deadline(deadline)
            after = os.fstat(descriptor)
            _, fresh_descriptor, named = _open_canonical_directory(
                candidate,
                deadline=deadline,
            )
            if (
                _directory_identity(opened) != _directory_identity(after)
                or _directory_identity(opened) != _directory_identity(named)
            ):
                raise OSError("directory changed during observation")
        except (OSError, TimeoutError, ValueError) as error:
            raise ValueError(
                "E_DIRECTORY_OBSERVATION: directory changed during observation"
            ) from error
        finally:
            if fresh_descriptor >= 0:
                os.close(fresh_descriptor)
    finally:
        os.close(descriptor)


def read_bounded_regular_file(
    path: Path | str,
    *,
    output_limit: int = 4_096,
) -> bytes:
    """Read one owned regular file without links, blocking, or an unbounded buffer."""

    if (
        not isinstance(output_limit, int)
        or isinstance(output_limit, bool)
        or not 1 <= output_limit <= 1_048_576
    ):
        raise ValueError("E_BOUNDED_FILE: invalid output limit")
    candidate = Path(os.path.abspath(path))
    name = candidate.name
    descriptor = -1
    payload = bytearray()
    try:
        if name in {"", ".", ".."} or "/" in name or "\0" in name:
            raise OSError("unsafe file name")
        with observed_directory(candidate.parent) as parent_observation:
            _, parent, _ = parent_observation
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o022
                or before.st_size > output_limit
                or int(getattr(before, "st_flags", 0)) & _DATALESS_FLAG
            ):
                raise OSError("unsafe regular file")
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent,
            )
            opened = os.fstat(descriptor)
            while len(payload) <= output_limit:
                chunk = os.read(
                    descriptor,
                    min(65_536, output_limit + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                _directory_identity(before) != _directory_identity(opened)
                or _directory_identity(before) != _directory_identity(after)
                or _directory_identity(before) != _directory_identity(named)
                or len(payload) > output_limit
                or len(payload) != opened.st_size
            ):
                raise OSError("regular file changed during read")
    except (OSError, TimeoutError, ValueError) as error:
        raise ValueError("E_BOUNDED_FILE: regular file is not safely readable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return bytes(payload)


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
    pass_fds: Sequence[int] = (),
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
        inherited_descriptors = tuple(pass_fds)
        if any(
            not isinstance(descriptor, int)
            or isinstance(descriptor, bool)
            or descriptor < 0
            for descriptor in inherited_descriptors
        ):
            raise ValueError("invalid inherited descriptor")
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            pass_fds=inherited_descriptors,
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


def _safe_bounded_git_revision(value: str, *, require_range: bool) -> bool:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return (
        bool(encoded)
        and len(encoded) <= 4_096
        and not value.startswith("-")
        and all(byte >= 0x20 and byte != 0x7F for byte in encoded)
        and (not require_range or ".." in value)
    )


def _bounded_git_arguments_are_read_only(arguments: tuple[str, ...]) -> bool:
    if arguments in _BOUNDED_GIT_STATIC_ARGUMENTS:
        return True
    if (
        len(arguments) == 4
        and arguments[:3] == ("rev-parse", "--verify", "--quiet")
    ):
        return _safe_bounded_git_revision(arguments[3], require_range=False)
    revision: str | None = None
    tail: tuple[str, ...] = ()
    if len(arguments) in {3, 4} and arguments[:2] == ("diff", "--name-only"):
        revision = arguments[2]
        tail = arguments[3:]
    elif (
        len(arguments) in {4, 5}
        and arguments[:3] == ("diff", "--diff-filter=A", "--name-only")
    ):
        revision = arguments[3]
        tail = arguments[4:]
    return (
        revision is not None
        and tail in {(), ("--",)}
        and _safe_bounded_git_revision(revision, require_range=True)
    )


def _bounded_git_observation(
    repository: Path | str,
    arguments: Sequence[str],
    *,
    environment: dict[str, str],
    input_data: bytes | None,
    output_limit: int,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    normalized = tuple(arguments)
    if (
        not normalized
        or any(not isinstance(item, str) or "\0" in item for item in normalized)
        or not _bounded_git_arguments_are_read_only(normalized)
    ):
        raise ValueError("E_GIT_OBSERVATION: Git command is not read-only")
    root = Path(os.path.abspath(repository))
    try:
        with observed_directory(root) as root_observation:
            _, descriptor, _ = root_observation
            git_arguments = trusted_git_argv(Path("."), normalized)
            wrapper = [
                str(_TRUSTED_PYTHON),
                "-I",
                "-S",
                "-B",
                "-c",
                _FCHDIR_GIT_WRAPPER,
                str(descriptor),
                *git_arguments,
            ]
            return _bounded_filter_probe(
                wrapper,
                cwd=Path("/"),
                environment=environment,
                input_data=input_data,
                output_limit=output_limit,
                timeout=timeout,
                pass_fds=(descriptor,),
            )
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(
            "E_GIT_OBSERVATION: bounded Git observation failed"
        ) from error


def run_bounded_git(
    repository: Path | str,
    arguments: Sequence[str],
    *,
    input_data: bytes | None = None,
    output_limit: int,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    """Run one exact read-only Git form, bound to an observed directory descriptor."""

    return _bounded_git_observation(
        repository,
        arguments,
        environment=trusted_git_environment(),
        input_data=input_data,
        output_limit=output_limit,
        timeout=timeout,
    )


@dataclass(frozen=True)
class DirectoryObservation:
    path: Path
    identity: tuple[int, ...]


@dataclass(frozen=True)
class GitDirectoryBinding:
    path: str
    identity: tuple[int, ...]
    marker_digest: str
    backlink_digest: str | None


@dataclass(frozen=True)
class WorktreeFingerprint:
    top: str
    top_identity: tuple[int, ...]
    common: str
    common_identity: tuple[int, ...]
    git_directory: GitDirectoryBinding
    head: str
    branch: str
    index_digest: str
    has_gitlink: bool


def _bounded_git_bytes(
    repository: Path,
    arguments: tuple[str, ...],
) -> bytes | None:
    try:
        completed = run_bounded_git(
            repository,
            arguments,
            output_limit=1_048_576,
            timeout=10.0,
        )
    except (OSError, RuntimeError, ValueError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _bounded_git_text(
    repository: Path,
    arguments: tuple[str, ...],
) -> str | None:
    raw = _bounded_git_bytes(repository, arguments)
    if raw is None:
        return None
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return value[:-1] if value.endswith("\n") else value


def canonical_directory_observation(value: str) -> DirectoryObservation | None:
    candidate = Path(value)
    try:
        with observed_directory(candidate) as opened:
            canonical, _, metadata = opened
    except (OSError, RuntimeError, ValueError):
        return None
    return DirectoryObservation(canonical, _directory_identity(metadata))


def canonical_directory(value: str) -> Path | None:
    observation = canonical_directory_observation(value)
    return None if observation is None else observation.path


def _strict_control_line(payload: bytes, *, prefix: str = "") -> str | None:
    try:
        value = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if value.endswith("\n"):
        value = value[:-1]
    if not value or "\0" in value:
        return None
    if prefix:
        if not value.startswith(prefix) or len(value) == len(prefix):
            return None
        value = value.removeprefix(prefix)
    return value


def _absolute_control_path(value: str, *, relative_to: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = relative_to / candidate
    return Path(os.path.abspath(candidate))


def _git_directory_binding(
    candidate: Path,
    common: Path,
) -> GitDirectoryBinding | None:
    marker = candidate / ".git"
    try:
        with observed_directory(candidate) as root_observation:
            _, root_descriptor, _ = root_observation
            marker_metadata = os.stat(
                ".git",
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
    except (OSError, RuntimeError, ValueError):
        return None
    if stat.S_ISDIR(marker_metadata.st_mode):
        marker_observation = canonical_directory_observation(str(marker))
        common_observation = canonical_directory_observation(str(common))
        if (
            marker_observation is None
            or common_observation is None
            or marker_observation != common_observation
        ):
            return None
        return GitDirectoryBinding(
            str(marker_observation.path),
            marker_observation.identity,
            sha256(b"directory").hexdigest(),
            None,
        )
    if not stat.S_ISREG(marker_metadata.st_mode):
        return None
    try:
        marker_payload = read_bounded_regular_file(marker)
    except ValueError:
        return None
    pointer = _strict_control_line(marker_payload, prefix="gitdir: ")
    if pointer is None:
        return None
    git_directory = canonical_directory_observation(
        str(_absolute_control_path(pointer, relative_to=candidate))
    )
    if (
        git_directory is None
        or git_directory.path.parent.name != "worktrees"
        or git_directory.path.parent.parent != common
    ):
        return None
    try:
        backlink_payload = read_bounded_regular_file(git_directory.path / "gitdir")
    except ValueError:
        return None
    backlink = _strict_control_line(backlink_payload)
    if backlink is None:
        return None
    backlink_path = _absolute_control_path(backlink, relative_to=git_directory.path)
    if (
        backlink_path.name != ".git"
        or canonical_directory(str(backlink_path.parent)) != candidate
    ):
        return None
    return GitDirectoryBinding(
        str(git_directory.path),
        git_directory.identity,
        sha256(marker_payload).hexdigest(),
        sha256(backlink_payload).hexdigest(),
    )


def _index_observation(root: Path) -> tuple[bool, str] | None:
    raw = _bounded_git_bytes(root, ("ls-files", "--stage", "-z"))
    if raw is None:
        return None
    fields = raw.split(b"\0")
    if fields[-1] != b"":
        return None
    fields.pop()
    has_gitlink = False
    for field in fields:
        header, separator, path = field.partition(b"\t")
        parts = header.split(b" ")
        if separator != b"\t" or not path or len(parts) != 3:
            return None
        mode, object_name, stage = parts
        if (
            len(mode) != 6
            or any(value not in b"01234567" for value in mode)
            or len(object_name) not in {40, 64}
            or stage not in {b"0", b"1", b"2", b"3"}
        ):
            return None
        if mode == b"160000":
            has_gitlink = True
    return has_gitlink, sha256(raw).hexdigest()


def is_oid(value: str) -> bool:
    if len(value) not in {40, 64}:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def worktree_fingerprint(
    candidate: Path,
    common: Path,
) -> WorktreeFingerprint | None:
    binding = _git_directory_binding(candidate, common)
    if binding is None:
        return None
    top = _bounded_git_text(candidate, ("rev-parse", "--show-toplevel"))
    common_value = _bounded_git_text(
        candidate,
        ("rev-parse", "--path-format=absolute", "--git-common-dir"),
    )
    git_directory_value = _bounded_git_text(
        candidate,
        ("rev-parse", "--absolute-git-dir"),
    )
    head = _bounded_git_text(candidate, ("rev-parse", "HEAD"))
    branch = _bounded_git_text(candidate, ("rev-parse", "--abbrev-ref", "HEAD"))
    if None in {top, common_value, git_directory_value, head, branch}:
        return None
    assert top is not None and common_value is not None
    assert git_directory_value is not None and head is not None and branch is not None
    top_observation = canonical_directory_observation(top)
    common_observation = canonical_directory_observation(common_value)
    git_directory_observation = canonical_directory_observation(git_directory_value)
    index = _index_observation(candidate)
    if (
        top_observation is None
        or common_observation is None
        or git_directory_observation is None
        or index is None
        or top_observation.path != candidate
        or common_observation.path != common
        or git_directory_observation.path != Path(binding.path)
        or git_directory_observation.identity != binding.identity
        or not is_oid(head)
    ):
        return None
    has_gitlink, index_digest = index
    return WorktreeFingerprint(
        str(top_observation.path),
        top_observation.identity,
        str(common_observation.path),
        common_observation.identity,
        binding,
        head,
        branch,
        index_digest,
        has_gitlink,
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
            inventory = _bounded_git_observation(
                repository,
                ("ls-files", "-z"),
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
        completed = _bounded_git_observation(
            repository,
            ("check-attr", "--stdin", "-z", "filter", "diff"),
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
