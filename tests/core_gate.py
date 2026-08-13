"""Verified full-gate body, loaded only from bytes captured under the mutex."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import unittest


_MAX_COMMAND_OUTPUT_BYTES = 262_144
_COMMAND_POLL_SECONDS = 0.05
_COMMAND_REAP_SECONDS = 0.25


def _assert_context(context: Any, repository: Path) -> None:
    context.validate_executables()
    if (
        context.repository != repository
        or context.authorizes is not False
        or context.python.realpath != Path(sys.executable).resolve(strict=True)
        or not sys.flags.isolated
        or not sys.flags.safe_path
        or not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
        or sys.pycache_prefix != os.devnull
    ):
        raise RuntimeError("E_TEST_CONTEXT: closed execution context drifted")


def _activate_context(context: Any, repository: Path) -> None:
    _assert_context(context, repository)
    os.environ.clear()
    os.environ.update(dict(context.environment))


def _run(
    argv: Sequence[str],
    *,
    repository: Path,
    environment: Mapping[str, str],
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[bytes]:
    if timeout <= 0:
        raise RuntimeError("E_TEST_TIMEOUT: command deadline is invalid")
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    returncode: int | None = None
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=repository,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("E_TEST_COMMAND: command pipes are unavailable")
        selector = selectors.DefaultSelector()
        for stream, target in ((process.stdout, stdout), (process.stderr, stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, target)
        deadline = time.monotonic() + timeout
        open_streams = 2
        while open_streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("E_TEST_TIMEOUT: command exceeded its deadline")
            events = selector.select(min(_COMMAND_POLL_SECONDS, remaining))
            for key, _ in events:
                target = key.data
                allowance = _MAX_COMMAND_OUTPUT_BYTES + 1 - len(stdout) - len(stderr)
                chunk = os.read(key.fd, min(65_536, max(1, allowance)))
                if chunk:
                    target.extend(chunk)
                    if len(stdout) + len(stderr) > _MAX_COMMAND_OUTPUT_BYTES:
                        raise RuntimeError(
                            "E_TEST_OUTPUT: command output exceeded its byte limit"
                        )
                    continue
                selector.unregister(key.fileobj)
                key.fileobj.close()
                open_streams -= 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("E_TEST_TIMEOUT: command exceeded its deadline")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "E_TEST_TIMEOUT: command exceeded its deadline"
            ) from error
    except RuntimeError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise RuntimeError("E_TEST_COMMAND: command execution failed") from error
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=_COMMAND_REAP_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=_COMMAND_REAP_SECONDS)
    completed = subprocess.CompletedProcess(
        list(argv), int(returncode), bytes(stdout), bytes(stderr)
    )
    if completed.returncode != 0:
        stderr = completed.stderr[:4_096].decode("utf-8", errors="replace")
        raise RuntimeError(
            f"E_TEST_COMMAND: {argv[0]} exited {completed.returncode}: {stderr}"
        )
    return completed


def _run_cli(arguments: Sequence[str]) -> dict[str, Any]:
    from control_plane import cli

    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        returncode = cli.main(arguments)
    if returncode != 0 or stderr.getvalue():
        raise RuntimeError(
            f"E_TEST_CLI: {' '.join(arguments)} failed: {stderr.getvalue()[:4096]}"
        )
    try:
        value = json.loads(stdout.getvalue())
    except json.JSONDecodeError as error:
        raise RuntimeError("E_TEST_CLI: command output is not JSON") from error
    if (
        not isinstance(value, dict)
        or value.get("ok") is not True
        or value.get("authorizes") is not False
    ):
        raise RuntimeError("E_TEST_CLI: command did not return a closed PASS contract")
    return value


def _git_argv(executable: Path, repository: Path, *arguments: str) -> list[str]:
    normalized = tuple(arguments)
    if normalized[:1] == ("diff",):
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
        normalized = (
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            *options,
            *tail[separator:],
        )
    return [
        str(executable),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.pager=cat",
        "-c",
        "diff.external=",
        "-c",
        "color.ui=false",
        "-C",
        str(repository),
        *normalized,
    ]


def run_gate(
    *,
    repository: Path,
    context: Any,
    test_names: Sequence[str],
    shell_paths: Sequence[str],
    revalidate_sources: Callable[[], None],
    authoritative_git_environment: Callable[[Any], dict[str, str]],
) -> int:
    """Execute every gate phase under one already-acquired mutex and context."""

    revalidate_sources()
    _activate_context(context, repository)

    python_contract = (
        "import os,sys;"
        "bad=any(n=='control_plane' or n.startswith('control_plane.') for n in sys.modules);"
        f"expected={str(context.python.realpath)!r};"
        "ok=(sys.flags.isolated and sys.flags.safe_path and sys.flags.no_site "
        "and sys.flags.dont_write_bytecode and sys.pycache_prefix==os.devnull "
        "and os.path.realpath(sys.executable)==expected and not bad);"
        "raise SystemExit(0 if ok else 9)"
    )
    _run(
        [
            str(context.python.path),
            "-I",
            "-S",
            "-B",
            "-X",
            "pycache_prefix=/dev/null",
            "-c",
            python_contract,
        ],
        repository=repository,
        environment=context.environment,
    )

    revalidate_sources()
    _activate_context(context, repository)
    if context.node is None:
        raise RuntimeError("E_TEST_CONTEXT: attested Node is unavailable")
    node_contract = (
        "const f=require('fs');"
        f"const expected={json.dumps(str(context.node.realpath))};"
        "if(f.realpathSync(process.execPath)!==expected||!process.versions.node)process.exit(9);"
    )
    _run(
        [str(context.node.path), "-e", node_contract],
        repository=repository,
        environment=context.environment,
    )

    revalidate_sources()
    _activate_context(context, repository)
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(test_names))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    revalidate_sources()
    _activate_context(context, repository)
    for relative in shell_paths:
        path = repository / relative
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise RuntimeError("E_TEST_SOURCE: shell source drifted")
    _run(
        ["/bin/sh", "-n", *(str(repository / item) for item in shell_paths)],
        repository=repository,
        environment=context.environment,
    )

    for arguments in (
        (
            "policy-check",
            "--policy",
            str(repository / ".codex" / "project-policy.toml"),
            "--json",
        ),
        (
            "registry-check",
            "--registry",
            str(repository / ".codex" / "resource-registry.toml"),
            "--policy",
            str(repository / ".codex" / "project-policy.toml"),
            "--json",
        ),
        ("inventory", "--repo", str(repository), "--json"),
        ("doctor", "--repo", str(repository), "--json"),
    ):
        revalidate_sources()
        _activate_context(context, repository)
        _run_cli(arguments)

    revalidate_sources()
    _activate_context(context, repository)
    from control_plane.repository import assert_no_external_git_filters

    assert_no_external_git_filters(repository)
    revalidate_sources()
    _activate_context(context, repository)
    git_environment = authoritative_git_environment(context)
    git_environment.update(
        {
            "GIT_CONFIG_COUNT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    _run(
        _git_argv(context.git.path, repository, "diff", "--check"),
        repository=repository,
        environment=git_environment,
    )
    revalidate_sources()
    _activate_context(context, repository)
    assert_no_external_git_filters(repository)
    revalidate_sources()
    _activate_context(context, repository)
    git_environment = authoritative_git_environment(context)
    git_environment.update(
        {
            "GIT_CONFIG_COUNT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    status = _run(
        _git_argv(context.git.path, repository, "status", "--short", "--branch"),
        repository=repository,
        environment=git_environment,
    )
    sys.stdout.buffer.write(status.stdout)
    return 0
