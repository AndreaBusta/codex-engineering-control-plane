"""Focused repository-contract checks without third-party dependencies."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class GitSubprocessUse:
    relative_path: str
    function: str
    line: int
    command_origin: str
    environment_origin: str
    exception: str | None


_GIT_SUBPROCESS_EXCEPTIONS = {
    ("cli.py", "_refresh_remote_base"): "authenticated remote refresh",
    ("host_bridge.py", "stage_allowlisted_paths"): "authorized index mutation",
    ("host_bridge.py", "commit_staged_change"): "authorized commit mutation",
}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _assigned_expression(
    function: ast.FunctionDef | ast.AsyncFunctionDef | None,
    name: str,
    *,
    before_line: int,
) -> ast.AST | None:
    if function is None:
        return None
    candidates: list[tuple[int, ast.AST]] = []
    for node in ast.walk(function):
        if getattr(node, "lineno", before_line) >= before_line:
            continue
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                candidates.append((node.lineno, node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            candidates.append((node.lineno, node.value))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _expression_origin(
    node: ast.AST | None,
    function: ast.FunctionDef | ast.AsyncFunctionDef | None,
    *,
    before_line: int,
) -> str:
    if node is None:
        return "missing"
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name == "shutil.which" and any(
            isinstance(argument, ast.Constant) and argument.value == "git"
            for argument in node.args
        ):
            return "path-search:shutil.which(git)"
        return f"call:{name}"
    if isinstance(node, ast.Name):
        assigned = _assigned_expression(
            function, node.id, before_line=before_line
        )
        if assigned is not None:
            return _expression_origin(
                assigned,
                function,
                before_line=getattr(assigned, "lineno", before_line),
            )
        return f"name:{node.id}"
    if isinstance(node, (ast.List, ast.Tuple)):
        if not node.elts:
            return "empty-sequence"
        first = _expression_origin(
            node.elts[0], function, before_line=before_line
        )
        if (
            first in {"literal:/usr/bin/env", "literal:/bin/env"}
            and len(node.elts) > 1
            and _expression_origin(
                node.elts[1], function, before_line=before_line
            )
            == "literal:git"
        ):
            return f"path-search:{first.removeprefix('literal:')} git"
        return first
    if isinstance(node, ast.Starred):
        return _expression_origin(node.value, function, before_line=before_line)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return f"literal:{node.value}"
    if isinstance(node, ast.Dict):
        return "literal-dict"
    return f"node:{type(node).__name__}"


def _function_for_node(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
    return None


def _git_command_builder_security(
    tree: ast.AST,
) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        origins = {
            _expression_origin(
                returned.value,
                function,
                before_line=returned.lineno,
            )
            for returned in ast.walk(function)
            if isinstance(returned, ast.Return) and returned.value is not None
        }
        if any("git" in origin.lower() for origin in origins):
            result[function.name] = all(
                origin
                in {
                    "call:trusted_git_executable",
                    "call:_trusted_git_executable",
                    "literal:/usr/bin/git",
                    "literal:/bin/git",
                }
                for origin in origins
            )
    return result


def _looks_like_git_command(origin: str) -> bool:
    lowered = origin.lower()
    return (
        lowered in {"literal:git", "literal:/usr/bin/git", "literal:/bin/git"}
        or "git_argv" in lowered
        or "git_executable" in lowered
        or "closed_git_argv" in lowered
        or lowered == "name:git"
        or lowered.startswith("path-search:")
    )


def _secure_git_command(
    origin: str, builders: dict[str, bool]
) -> bool:
    if origin in {
        "literal:/usr/bin/git",
        "literal:/bin/git",
        "call:trusted_git_argv",
        "call:trusted_git_executable",
        "call:_trusted_git_executable",
    }:
        return True
    if origin.startswith("call:"):
        return builders.get(origin.removeprefix("call:"), False)
    return False


def _authenticated_git_environment_security(tree: ast.AST) -> bool:
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_authenticated_git_environment"
    ]
    if len(functions) != 1:
        return False
    function = functions[0]
    return_origins = {
        _expression_origin(
            returned.value,
            function,
            before_line=returned.lineno,
        )
        for returned in ast.walk(function)
        if isinstance(returned, ast.Return) and returned.value is not None
    }
    if not return_origins or return_origins != {
        "call:trusted_git_environment"
    }:
        return False
    allowed_updates = {
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
    }
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name == "git_environment":
                return False
            if call_name.startswith("environment.") and call_name != (
                "environment.update"
            ):
                return False
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets: tuple[ast.AST, ...]
            if isinstance(node, ast.Assign):
                targets = tuple(node.targets)
            else:
                targets = (node.target,)
            if any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "environment"
                for target in targets
            ):
                return False
        if not (
            isinstance(node, ast.Call)
            and _call_name(node.func) == "environment.update"
        ):
            continue
        if (
            len(node.args) != 1
            or node.keywords
            or not isinstance(node.args[0], ast.Dict)
        ):
            return False
        keys = {
            key.value
            for key in node.args[0].keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if len(keys) != len(node.args[0].keys) or not keys.issubset(
            allowed_updates
        ):
            return False
    return True


def _secure_git_environment(
    relative_path: str,
    function_name: str,
    origin: str,
    exception: str | None,
    authenticated_environment_secure: bool,
) -> bool:
    if origin == "call:trusted_git_environment":
        return True
    if (
        origin == "call:_git_environment"
        and (relative_path, function_name) == ("git_guards.py", "_git")
    ):
        return True
    if (
        origin == "call:_trusted_git_environment"
        and (relative_path, function_name) == ("hooks.py", "_trusted_git_text")
    ):
        return True
    if origin == "call:_sanitized_git_environment" and relative_path == (
        "host_bridge.py"
    ):
        return True
    return (
        origin == "call:_authenticated_git_environment"
        and (relative_path, function_name)
        == ("cli.py", "_refresh_remote_base")
        and exception == "authenticated remote refresh"
        and authenticated_environment_secure
    )


def git_subprocess_contract(
    runtime_root: Path,
) -> tuple[tuple[GitSubprocessUse, ...], tuple[str, ...]]:
    """Inventory productive Git subprocesses and reject ambient execution."""

    inventory: list[GitSubprocessUse] = []
    issues: list[str] = []
    for path in sorted(runtime_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        builders = _git_command_builder_security(tree)
        authenticated_environment_secure = (
            _authenticated_git_environment_security(tree)
        )
        relative_path = path.relative_to(runtime_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node.func) not in {
                "subprocess.run",
                "subprocess.Popen",
                "subprocess.call",
                "subprocess.check_call",
                "subprocess.check_output",
            }:
                continue
            function = _function_for_node(node, parents)
            function_name = function.name if function is not None else "<module>"
            command = node.args[0] if node.args else None
            command_origin = _expression_origin(
                command, function, before_line=node.lineno
            )
            environment_node = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "env"),
                None,
            )
            environment_origin = _expression_origin(
                environment_node, function, before_line=node.lineno
            )
            if not _looks_like_git_command(command_origin):
                continue
            exception = _GIT_SUBPROCESS_EXCEPTIONS.get(
                (relative_path, function_name)
            )
            use = GitSubprocessUse(
                relative_path=relative_path,
                function=function_name,
                line=node.lineno,
                command_origin=command_origin,
                environment_origin=environment_origin,
                exception=exception,
            )
            inventory.append(use)
            label = f"{relative_path}:{function_name}:{node.lineno}"
            if (
                not _secure_git_command(command_origin, builders)
                and exception
                not in {"authorized index mutation", "authorized commit mutation"}
            ):
                issues.append(f"GIT_AMBIENT_EXECUTABLE:{label}")
            if environment_origin == "call:git_environment":
                issues.append(f"GIT_AMBIENT_ENVIRONMENT:{label}")
            elif not _secure_git_environment(
                relative_path,
                function_name,
                environment_origin,
                exception,
                authenticated_environment_secure,
            ):
                issues.append(f"GIT_UNCLOSED_ENVIRONMENT:{label}")
    return tuple(inventory), tuple(issues)


def _literal_arguments(node: ast.AST | None) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return ()
    values: list[str] = []
    for item in node.elts:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            values.append(item.value)
        elif isinstance(item, ast.Starred):
            values.append("*")
        else:
            values.append("?")
    return tuple(values)


def git_diff_contract(
    runtime_root: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Require every productive diff builder to cross a closed normalizer."""

    inventory: list[str] = []
    issues: list[str] = []
    repository_path = runtime_root / "repository.py"
    repository_tree = ast.parse(
        repository_path.read_text(encoding="utf-8"),
        filename=str(repository_path),
    )
    builder = next(
        (
            node
            for node in ast.walk(repository_tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "trusted_git_argv"
        ),
        None,
    )
    if builder is None or not any(
        isinstance(node, ast.Call)
        and _call_name(node.func) == "_normalize_trusted_git_arguments"
        for node in ast.walk(builder)
    ):
        issues.append("GIT_DIFF_NORMALIZER_MISSING:repository.py:trusted_git_argv")

    cached_name_only_exception = (
        "host_bridge.py",
        "stage_allowlisted_paths",
    )
    for path in sorted(runtime_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        relative_path = path.relative_to(runtime_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func)
            function = _function_for_node(node, parents)
            function_name = function.name if function is not None else "<module>"
            label = f"{relative_path}:{function_name}:{node.lineno}"
            if call_name in {
                "subprocess.run",
                "subprocess.Popen",
                "subprocess.call",
                "subprocess.check_call",
                "subprocess.check_output",
            }:
                command_node = node.args[0] if node.args else None
                command = _literal_arguments(command_node)
                first = (
                    command_node.elts[0]
                    if isinstance(command_node, (ast.List, ast.Tuple))
                    and command_node.elts
                    else None
                )
                executable_origin = _expression_origin(
                    first, function, before_line=node.lineno
                )
                if (
                    _looks_like_git_command(executable_origin)
                    and "diff" in command[1:]
                ):
                    inventory.append(label)
                    issues.append(f"GIT_DIFF_BYPASSES_NORMALIZER:{label}")
                continue
            if call_name not in {"trusted_git_argv", "_closed_git_argv"}:
                continue
            arguments = _literal_arguments(
                node.args[1] if len(node.args) > 1 else None
            )
            if arguments[:1] != ("diff",):
                continue
            inventory.append(label)
            if call_name == "trusted_git_argv":
                continue
            if (
                (relative_path, function_name) != cached_name_only_exception
                or arguments != ("diff", "--cached", "--name-only", "-z")
            ):
                issues.append(f"GIT_DIFF_BYPASSES_NORMALIZER:{label}")
    return tuple(inventory), tuple(issues)


def _active_yaml_lines(workflow: str) -> list[str]:
    """Remove blank lines and comments from this repository's simple workflow."""

    active: list[str] = []
    for raw_line in workflow.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if line.strip():
            active.append(line)
    return active


def ci_contract_issues(workflow: str) -> list[str]:
    """Return stable CI safety issues for the workflow conventions we own."""

    lines = _active_yaml_lines(workflow)
    issues: list[str] = []

    def add(code: str) -> None:
        if code not in issues:
            issues.append(code)

    if any(
        re.match(r"\s*(?:-\s*)?[\"'][^\"']+[\"']\s*:", line)
        or "\t" in line
        for line in lines
    ):
        add("CI_UNSUPPORTED_YAML_STYLE")

    permission_headers = [
        (index, len(line) - len(line.lstrip()), line.strip())
        for index, line in enumerate(lines)
        if line.strip().startswith("permissions:")
    ]
    exact_top_level_permissions = [
        (index, indent)
        for index, indent, content in permission_headers
        if indent == 0 and content == "permissions:"
    ]
    if len(exact_top_level_permissions) != 1:
        add("CI_TOP_LEVEL_PERMISSIONS")
    if any(indent > 0 for _, indent, _ in permission_headers):
        add("CI_JOB_PERMISSIONS")

    if exact_top_level_permissions:
        start = exact_top_level_permissions[0][0] + 1
        permission_entries: list[str] = []
        for line in lines[start:]:
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                break
            permission_entries.append(line.strip())
        if permission_entries != ["actions: read", "contents: read"]:
            add("CI_TOP_LEVEL_PERMISSIONS")

    if any(
        re.search(r"\bpermissions\s*:\s*.*\bwrite(?:-all)?\b", line)
        or re.fullmatch(r"\s*[A-Za-z0-9_-]+\s*:\s*write(?:-all)?\s*", line)
        for line in lines
    ):
        add("CI_WRITE_PERMISSION")

    action_references: list[str] = []
    for line in lines:
        match = re.fullmatch(r"\s*(?:-\s+)?uses\s*:\s*(\S+)\s*", line)
        if match:
            action_references.append(match.group(1))
    if not action_references:
        add("CI_NO_ACTIONS")
    for reference in action_references:
        if reference.startswith("./"):
            continue
        if re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference) is None:
            add("CI_UNPINNED_ACTION")

    on_headers = [
        index
        for index, line in enumerate(lines)
        if line == "on:"
    ]
    top_level_triggers: set[str] = set()
    if len(on_headers) == 1:
        for line in lines[on_headers[0] + 1 :]:
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                break
            if indent == 2:
                top_level_triggers.add(line.strip())
    if "pull_request:" not in top_level_triggers:
        add("CI_PULL_REQUEST_TRIGGER")
    if "push:" not in top_level_triggers:
        add("CI_MAIN_PUSH_TRIGGER")
    if "workflow_dispatch:" not in top_level_triggers:
        add("CI_MANUAL_TRIGGER")
    if any(line.strip() == "pull_request_target:" for line in lines):
        add("CI_PULL_REQUEST_TARGET")

    active_text = "\n".join(lines)
    required_fragments = {
        "CI_UBUNTU_RUNNER": "runs-on: ubuntu-24.04",
        "CI_MACOS_RUNNER": "runs-on: macos-15",
        "CI_MACOS_MANUAL_ONLY": "if: github.event_name == 'workflow_dispatch'",
        "CI_TIMEOUT": "timeout-minutes:",
        "CI_TEST_COMMAND": "bash tests/run.sh",
    }
    for code, fragment in required_fragments.items():
        if fragment not in active_text:
            add(code)

    for index, line in enumerate(lines):
        match = re.fullmatch(r"\s*(?:-\s+)?uses\s*:\s*(\S+)\s*", line)
        if not match or not match.group(1).startswith("actions/checkout@"):
            continue
        uses_indent = len(line) - len(line.lstrip())
        step_lines: list[str] = []
        for following in lines[index + 1 :]:
            indent = len(following) - len(following.lstrip())
            if indent < uses_indent or (
                indent == uses_indent and following.lstrip().startswith("- ")
            ):
                break
            step_lines.append(following)

        with_blocks = [
            (position, len(step_line) - len(step_line.lstrip()))
            for position, step_line in enumerate(step_lines)
            if step_line.strip() == "with:"
        ]
        checkout_options: list[str] = []
        if len(with_blocks) == 1:
            with_position, with_indent = with_blocks[0]
            for option_line in step_lines[with_position + 1 :]:
                indent = len(option_line) - len(option_line.lstrip())
                if indent <= with_indent:
                    break
                checkout_options.append(option_line.strip())
        if (
            "persist-credentials: false" not in checkout_options
            or "persist-credentials: true" in checkout_options
        ):
            add("CI_CHECKOUT_CREDENTIALS")

    return issues


def literal_secret_findings(text: str) -> list[str]:
    """Detect common literal credential shapes without returning their values."""

    patterns = (
        (
            "PRIVATE_KEY",
            re.compile(
                r"-----BEGIN (?:ENCRYPTED |RSA |EC |OPENSSH )?PRIVATE KEY-----"
            ),
        ),
        (
            "CREDENTIAL_ASSIGNMENT",
            re.compile(
                r"""(?imx)
                ^\s*(?:export\s+)?["']?(
                    api[_-]?key
                    |access[_-]?token
                    |auth[_-]?token
                    |token
                    |password
                    |passwd
                    |client[_-]?secret
                    |private[_-]?key
                    |service[_-]?account
                )
                ["']?\s*[:=]\s*
                (?:
                    ["'][^"'\r\n]{8,}["']
                    |
                    [^\s#"'$<{][^\s#]{7,}
                )
                \s*,?\s*(?:\#.*)?$
                """
            ),
        ),
        ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
        ("GITHUB_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
        ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
        ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
        ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
        (
            "STRIPE_SECRET",
            re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{12,}\b"),
        ),
    )
    return [label for label, pattern in patterns if pattern.search(text)]
