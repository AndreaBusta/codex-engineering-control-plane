"""Focused repository-contract checks without third-party dependencies."""

from __future__ import annotations

import re


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
