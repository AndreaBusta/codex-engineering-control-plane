from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
SCENARIOS = (
    "warning_once",
    "sessionstart_compact_fallback",
    "safe_read_explicit_repo",
    "feature_commit_push",
    "base_detached_force_denied",
    "stop_receipt",
    "rollback_byte_exact",
    "source_isolated_parity",
)
ARTIFACT_PATHS = {
    "policy": ".codex/project-policy.toml",
    "registry": ".codex/resource-registry.toml",
    "lock": ".codex/control-plane.lock",
    "launcher": "scripts/control-plane",
    "hooks": ".codex/hooks.json",
}


def _digest(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def artifact_digests(root: Path = ROOT) -> dict[str, str]:
    return {
        name: _digest(root / relative)
        for name, relative in ARTIFACT_PATHS.items()
    }


def _completed_case(
    case_id: str,
    status: str,
    completed: subprocess.CompletedProcess[bytes] | None = None,
) -> dict[str, object]:
    stdout = completed.stdout if completed is not None else b""
    stderr = completed.stderr if completed is not None else b""
    return {
        "id": case_id,
        "status": status,
        "exit_code": completed.returncode if completed is not None else None,
        "stdout_digest": f"sha256:{sha256(stdout).hexdigest()}",
        "stderr_digest": f"sha256:{sha256(stderr).hexdigest()}",
    }


def _run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
    timeout: float = 20.0,
    task_id: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        "PATH": (
            f"{Path(sys.executable).resolve().parent}:"
            "/usr/bin:/bin:/usr/sbin:/sbin"
        ),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if task_id is not None:
        environment["CODEX_CONTROL_PLANE_TASK_ID"] = task_id
    return subprocess.run(
        argv,
        cwd=cwd,
        input=input_bytes,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=environment,
    )


def _invoke_hook(
    root: Path,
    payload: dict[str, object],
    *,
    task_id: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return _run_bounded(
        [
            sys.executable,
            "-I",
            "-B",
            str(root / ".codex" / "hooks" / "control_plane_hook.py"),
        ],
        cwd=root,
        input_bytes=json.dumps(payload).encode("utf-8"),
        task_id=task_id,
    )


def _initialize_target(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    remote = root / "origin.git"
    repo = root / "work"
    _run_bounded(["git", "init", "--bare", str(remote)], cwd=root)
    initialized = _run_bounded(
        ["git", "init", "-b", "main", str(repo)], cwd=root
    )
    if initialized.returncode != 0:
        raise RuntimeError("target init failed")
    for key, value in (
        ("user.name", "Control Plane Smoke"),
        ("user.email", "control-plane-smoke@example.invalid"),
    ):
        if (
            _run_bounded(
                ["git", "config", key, value], cwd=repo
            ).returncode
            != 0
        ):
            raise RuntimeError("target config failed")
    (repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    for argv in (
        ["git", "add", "baseline.txt"],
        ["git", "commit", "-m", "smoke: baseline"],
        ["git", "remote", "add", "origin", str(remote)],
        ["git", "push", "-u", "origin", "main"],
        ["git", "switch", "-c", "codex/macos-smoke"],
    ):
        if _run_bounded(argv, cwd=repo).returncode != 0:
            raise RuntimeError("target bootstrap failed")
    return repo, remote


def _adopt(
    source: Path, target: Path, scratch: Path
) -> subprocess.CompletedProcess[bytes]:
    launcher = source / "scripts" / "control-plane"
    planned = _run_bounded(
        [
            str(launcher),
            "adopt",
            "plan",
            "--source",
            str(source),
            "--target",
            str(target),
            "--base-branch",
            "main",
            "--remote",
            "origin",
            "--json",
        ],
        cwd=source,
    )
    if planned.returncode != 0:
        return planned
    plan_path = scratch / "adoption-plan.json"
    plan_path.write_bytes(planned.stdout)
    return _run_bounded(
        [
            str(launcher),
            "adopt",
            "apply",
            "--plan",
            str(plan_path),
            "--json",
        ],
        cwd=source,
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root).as_posix(): (
            sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mode & 0o777,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


def _repository_snapshot(root: Path) -> dict[str, object]:
    index = _run_bounded(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
    )
    if index.returncode != 0:
        raise RuntimeError("repository index snapshot failed")
    return {
        "tree": _tree_snapshot(root),
        "index": f"sha256:{sha256(index.stdout).hexdigest()}",
    }


def _compact_matches_current(
    raw_output: bytes,
    expected_payload: object,
) -> bool:
    try:
        outer = json.loads(raw_output.decode("utf-8"))
        specific = outer["hookSpecificOutput"]
        actual = json.loads(specific["additionalContext"])
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return False
    return (
        isinstance(expected_payload, dict)
        and set(specific) == {"hookEventName", "additionalContext"}
        and specific.get("hookEventName") == "SessionStart"
        and actual == expected_payload
    )


def _stop_receipt_and_no_loop_are_exact(
    receipt_output: bytes,
    no_loop_output: bytes,
) -> bool:
    try:
        receipt = json.loads(receipt_output.decode("utf-8"))
        no_loop = json.loads(no_loop_output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    expected = {"continue": True}
    return receipt == expected and no_loop == expected


def _expected_unknown_warning(
    *, trigger: str, framing_status: str
) -> dict[str, object]:
    return {
        "title": "CONTROL PLANE RISK",
        "local": "UNKNOWN",
        "remote": "UNKNOWN",
        "action": "PAUSE_AND_VERIFY",
        "reason_code": "RS_WARNING_STATE_UNKNOWN",
        "safe_path": "feature→commit→push-feature→PR→checks→authorized-merge",
        "interaction": "pending_framing",
        "automatic_change": False,
        "trigger": trigger,
        "framing_status": framing_status,
    }


def _setup_local_task_state(
    root: Path,
    *,
    session_id: str,
    task_id: str,
) -> dict[str, object]:
    """Create only the local task and receipt consumed by installed hooks."""

    del session_id
    git_dir = Path(
        _git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    ).resolve()
    state_root = git_dir / "codex-control-plane"
    task_digest = f"sha256:{sha256(task_id.encode('utf-8')).hexdigest()}"
    decision_digest = (
        f"sha256:{sha256(('route:' + task_id).encode('utf-8')).hexdigest()}"
    )
    task = {
        "schema_version": 1,
        "task_id": task_id,
        "task_digest": task_digest,
        "decision_digest": decision_digest,
        "state": "framed",
        "generation": 1,
    }
    receipt_core = {
        "schema_version": 1,
        "task_id": task_id,
        "decision_digest": decision_digest,
    }
    receipt = {
        **receipt_core,
        "receipt_digest": (
            "sha256:"
            + sha256(
                json.dumps(
                    receipt_core,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        ),
    }
    for directory, name, value in (
        ("tasks", f"{task_id}.json", task),
        ("receipts", f"{task_id}.json", receipt),
    ):
        destination = state_root / directory / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    return {
        "task_id": task_id,
        "receipt_digest": receipt["receipt_digest"],
    }


def _child_payload(root: Path) -> dict[str, object]:
    """Execute only real launchers; unavailable later-task mechanics stay UNKNOWN."""

    cases = {
        case_id: _completed_case(case_id, "UNKNOWN")
        for case_id in SCENARIOS
    }
    session = f"smoke-{os.getpid()}-{id(cases)}"
    task_id = f"TASK-MACOS-SMOKE-{os.getpid()}"
    try:
        with tempfile.TemporaryDirectory(
            prefix="control-plane-macos-smoke-"
        ) as temporary:
            scratch = Path(temporary).resolve()
            target, _ = _initialize_target(scratch)
            applied = _adopt(root, target, scratch)
            if applied.returncode == 0:
                setup = _run_bounded(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        str(Path(__file__).resolve()),
                        "--setup-local-task",
                        "--repo",
                        str(target.resolve()),
                        "--session-id",
                        session,
                        "--task-id",
                        task_id,
                    ],
                    cwd=target,
                )
                try:
                    setup_payload = json.loads(
                        setup.stdout.decode("utf-8")
                    )
                    expected_compact = _expected_unknown_warning(
                        trigger="post_compact",
                        framing_status="pending_framing",
                    )
                    setup_exact = (
                        setup.returncode == 0
                        and set(setup_payload)
                        == {"task_id", "receipt_digest"}
                        and setup_payload.get("task_id") == task_id
                        and str(
                            setup_payload.get("receipt_digest", "")
                        ).startswith("sha256:")
                    )
                except (
                    KeyError,
                    TypeError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ):
                    expected_compact = None
                    setup_exact = False
                if setup_exact:
                    prompt_payload = {
                        "hook_event_name": "UserPromptSubmit",
                        "cwd": str(target.resolve()),
                        "session_id": session,
                    }
                    first = _invoke_hook(
                        target, prompt_payload, task_id=task_id
                    )
                    second = _invoke_hook(
                        target, prompt_payload, task_id=task_id
                    )
                    warning_pass = (
                        first.returncode == 0
                        and bool(first.stdout)
                        and second.returncode == 0
                        and second.stdout == b""
                    )
                    cases["warning_once"] = _completed_case(
                        "warning_once",
                        "PASS" if warning_pass else "FAIL",
                        first,
                    )
                    compact = _invoke_hook(
                        target,
                        {
                            "hook_event_name": "SessionStart",
                            "source": "compact",
                            "cwd": str(target.resolve()),
                            "session_id": session,
                        },
                        task_id=task_id,
                    )
                    compact_pass = (
                        compact.returncode == 0
                        and _compact_matches_current(
                            compact.stdout, expected_compact
                        )
                    )
                    cases["sessionstart_compact_fallback"] = _completed_case(
                        "sessionstart_compact_fallback",
                        "PASS" if compact_pass else "FAIL",
                        compact,
                    )
                    stop = _invoke_hook(
                        target,
                        {
                            "hook_event_name": "Stop",
                            "cwd": str(target.resolve()),
                            "session_id": session,
                            "stop_hook_active": False,
                        },
                        task_id=task_id,
                    )
                    no_loop = _invoke_hook(
                        target,
                        {
                            "hook_event_name": "Stop",
                            "cwd": str(target.resolve()),
                            "session_id": session,
                            "stop_hook_active": True,
                        },
                        task_id=task_id,
                    )
                    stop_pass = (
                        stop.returncode == 0
                        and no_loop.returncode == 0
                        and _stop_receipt_and_no_loop_are_exact(
                            stop.stdout, no_loop.stdout
                        )
                    )
                    cases["stop_receipt"] = _completed_case(
                        "stop_receipt",
                        "PASS" if stop_pass else "FAIL",
                        stop,
                    )
                    safe_command = (
                        f"{target / 'scripts' / 'control-plane'} "
                        f"safe-read --repo {target.resolve()} -- "
                        "git diff --check"
                    )
                    safe_pretool = _invoke_hook(
                        target,
                        {
                            "hook_event_name": "PreToolUse",
                            "cwd": str(target.resolve()),
                            "session_id": session,
                            "tool_name": "Bash",
                            "tool_input": {"command": safe_command},
                        },
                        task_id=task_id,
                    )
                    safe_read = _run_bounded(
                        [
                            str(target / "scripts" / "control-plane"),
                            "safe-read",
                            "--repo",
                            str(target.resolve()),
                            "--",
                            "git",
                            "diff",
                            "--check",
                        ],
                        cwd=target,
                        task_id=task_id,
                    )
                    cases["safe_read_explicit_repo"] = _completed_case(
                        "safe_read_explicit_repo",
                        (
                            "PASS"
                            if (
                                safe_pretool.returncode == 0
                                and safe_pretool.stdout == b""
                                and safe_read.returncode == 0
                            )
                            else "FAIL"
                        ),
                        safe_read,
                    )
                else:
                    for case_id in (
                        "warning_once",
                        "sessionstart_compact_fallback",
                        "safe_read_explicit_repo",
                        "stop_receipt",
                    ):
                        cases[case_id] = _completed_case(
                            case_id, "FAIL", setup
                        )

                feature_file = target / "feature-smoke.txt"
                feature_file.write_text("feature\n", encoding="utf-8")
                feature_add = _run_bounded(
                    ["git", "add", feature_file.name], cwd=target
                )
                feature_commit = _run_bounded(
                    ["git", "commit", "-m", "smoke: feature"], cwd=target
                )
                feature_push = _run_bounded(
                    ["git", "push", "-u", "origin", "codex/macos-smoke"],
                    cwd=target,
                )
                feature_pass = (
                    feature_add.returncode == 0
                    and feature_commit.returncode == 0
                    and feature_push.returncode == 0
                )
                cases["feature_commit_push"] = _completed_case(
                    "feature_commit_push",
                    "PASS" if feature_pass else "FAIL",
                    feature_push,
                )

                switch_base = _run_bounded(
                    ["git", "switch", "main"], cwd=target
                )
                base_file = target / "base-denied.txt"
                base_file.write_text("deny\n", encoding="utf-8")
                _run_bounded(["git", "add", base_file.name], cwd=target)
                base_commit = _run_bounded(
                    ["git", "commit", "-m", "smoke: base denied"],
                    cwd=target,
                )
                _run_bounded(
                    ["git", "rm", "--cached", "--force", base_file.name],
                    cwd=target,
                )
                base_file.unlink(missing_ok=True)
                detach = _run_bounded(
                    ["git", "switch", "--detach", "HEAD"], cwd=target
                )
                detached_file = target / "detached-denied.txt"
                detached_file.write_text("deny\n", encoding="utf-8")
                _run_bounded(
                    ["git", "add", detached_file.name], cwd=target
                )
                detached_commit = _run_bounded(
                    ["git", "commit", "-m", "smoke: detached denied"],
                    cwd=target,
                )
                _run_bounded(
                    [
                        "git",
                        "rm",
                        "--cached",
                        "--force",
                        detached_file.name,
                    ],
                    cwd=target,
                )
                detached_file.unlink(missing_ok=True)
                _run_bounded(["git", "switch", "main"], cwd=target)
                bypass_file = target / "base-bypass-fixture.txt"
                bypass_file.write_text("fixture\n", encoding="utf-8")
                _run_bounded(["git", "add", bypass_file.name], cwd=target)
                bypass = _run_bounded(
                    [
                        "git",
                        "commit",
                        "--no-verify",
                        "-m",
                        "smoke: base push fixture",
                    ],
                    cwd=target,
                )
                force_push = _run_bounded(
                    ["git", "push", "--force", "origin", "main"],
                    cwd=target,
                )
                denied_pass = (
                    switch_base.returncode == 0
                    and base_commit.returncode != 0
                    and detach.returncode == 0
                    and detached_commit.returncode != 0
                    and bypass.returncode == 0
                    and force_push.returncode != 0
                )
                cases["base_detached_force_denied"] = _completed_case(
                    "base_detached_force_denied",
                    "PASS" if denied_pass else "FAIL",
                    force_push,
                )
                git_guards_ready = all(
                    path.is_file() and not path.is_symlink()
                    for path in (
                        target / ".codex" / "git-hooks" / "pre-commit",
                        target / ".codex" / "git-hooks" / "pre-push",
                    )
                )
                if not git_guards_ready:
                    cases["feature_commit_push"] = _completed_case(
                        "feature_commit_push", "UNKNOWN"
                    )
                    cases[
                        "base_detached_force_denied"
                    ] = _completed_case(
                        "base_detached_force_denied", "UNKNOWN"
                    )

            parity_root, _ = _initialize_target(scratch / "parity")
            parity_before = _repository_snapshot(parity_root)
            before_hooks_path = _run_bounded(
                [
                    "git",
                    "config",
                    "--local",
                    "--get-all",
                    "core.hooksPath",
                ],
                cwd=parity_root,
            )
            parity_applied = _adopt(root, parity_root, scratch / "parity")
            if parity_applied.returncode == 0:
                source_read = _run_bounded(
                    [
                        str(root / "scripts" / "control-plane"),
                        "safe-read",
                        "--repo",
                        str(parity_root.resolve()),
                        "--",
                        "git",
                        "diff",
                        "--check",
                    ],
                    cwd=root,
                )
                isolated_read = _run_bounded(
                    [
                        str(parity_root / "scripts" / "control-plane"),
                        "safe-read",
                        "--repo",
                        str(parity_root.resolve()),
                        "--",
                        "git",
                        "diff",
                        "--check",
                    ],
                    cwd=parity_root,
                )
                parity_pass = (
                    source_read.returncode == isolated_read.returncode
                    and source_read.stdout == isolated_read.stdout
                    and source_read.stderr == isolated_read.stderr
                )
                cases["source_isolated_parity"] = _completed_case(
                    "source_isolated_parity",
                    "PASS" if parity_pass else "FAIL",
                    isolated_read,
                )
                rollback = _run_bounded(
                    [
                        str(root / "scripts" / "control-plane"),
                        "adopt",
                        "rollback",
                        "--target",
                        str(parity_root),
                        "--json",
                    ],
                    cwd=root,
                )
                after_hooks_path = _run_bounded(
                    [
                        "git",
                        "config",
                        "--local",
                        "--get-all",
                        "core.hooksPath",
                    ],
                    cwd=parity_root,
                )
                rollback_pass = (
                    rollback.returncode == 0
                    and _repository_snapshot(parity_root)
                    == parity_before
                    and before_hooks_path.returncode
                    == after_hooks_path.returncode
                    and before_hooks_path.stdout == after_hooks_path.stdout
                )
                cases["rollback_byte_exact"] = _completed_case(
                    "rollback_byte_exact",
                    "PASS" if rollback_pass else "FAIL",
                    rollback,
                )
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError):
        pass

    return {
        "schema_version": 1,
        "scenarios": [cases[scenario] for scenario in SCENARIOS],
        "native_adapter": "absent",
    }


def _run_child(arguments: argparse.Namespace) -> int:
    root = Path(arguments.repo).resolve()
    payload = _child_payload(root)
    sys.stdout.write(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


class MacOSHookSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_root = Path(self.temp.name)
        self.head = _git(ROOT, "rev-parse", "HEAD")

    def tracked_smoke_repository(self, label: str) -> tuple[Path, str]:
        repository = (self.temp_root / label).resolve()
        initialized = _run_bounded(
            ["git", "init", "-b", "main", str(repository)],
            cwd=self.temp_root,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        for key, value in (
            ("user.name", "Control Plane Smoke Tests"),
            ("user.email", "control-plane-smoke@example.invalid"),
        ):
            configured = _run_bounded(
                ["git", "config", key, value],
                cwd=repository,
            )
            self.assertEqual(configured.returncode, 0, configured.stderr)
        for relative in (*ARTIFACT_PATHS.values(), "tests/macos_hook_smoke.py"):
            destination = repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        added = _run_bounded(["git", "add", "."], cwd=repository)
        committed = _run_bounded(
            ["git", "commit", "-m", "test: tracked smoke runtime"],
            cwd=repository,
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        return repository, _git(repository, "rev-parse", "HEAD")

    def test_smoke_semantic_validators_reject_fallback_and_missing_receipt(
        self,
    ) -> None:
        expected = {
            "title": "CONTROL PLANE RISK",
            "trigger": "post_compact",
            "framing_status": "framed",
        }
        exact = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": json.dumps(
                    expected, sort_keys=True, separators=(",", ":")
                ),
            }
        }
        fallback = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": json.dumps(
                    {
                        "title": "CONTROL PLANE RISK",
                        "trigger": "post_compact",
                        "framing_status": "pending_framing",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        }
        self.assertTrue(
            _compact_matches_current(
                json.dumps(exact).encode("utf-8"), expected
            )
        )
        self.assertFalse(
            _compact_matches_current(
                json.dumps(fallback).encode("utf-8"), expected
            )
        )
        self.assertTrue(
            _stop_receipt_and_no_loop_are_exact(
                b'{"continue":true}', b'{"continue":true}'
            )
        )
        self.assertFalse(
            _stop_receipt_and_no_loop_are_exact(
                (
                    b'{"continue":true,"systemMessage":'
                    b'"active task has no receipt"}'
                ),
                b'{"continue":true}',
            )
        )

    def test_repository_snapshot_binds_index_state(self) -> None:
        repository, _ = _initialize_target(
            (self.temp_root / "snapshot-index").resolve()
        )
        before = _repository_snapshot(repository)
        changed = _run_bounded(
            ["git", "update-index", "--chmod=+x", "baseline.txt"],
            cwd=repository,
        )
        self.assertEqual(changed.returncode, 0, changed.stderr)
        after = _repository_snapshot(repository)

        self.assertEqual(before["tree"], after["tree"])
        self.assertNotEqual(before["index"], after["index"])

    def test_adopted_runtime_emits_exact_minimal_compact_fallback(
        self,
    ) -> None:
        source = (self.temp_root / "exact-compact-source").resolve()
        shutil.copytree(
            ROOT,
            source,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc"
            ),
        )
        self.assertEqual(
            _run_bounded(["git", "init", "-b", "main"], cwd=source).returncode,
            0,
        )
        for key, value in (
            ("user.name", "Control Plane Smoke Tests"),
            ("user.email", "control-plane-smoke@example.invalid"),
        ):
            self.assertEqual(
                _run_bounded(
                    ["git", "config", key, value], cwd=source
                ).returncode,
                0,
            )
        self.assertEqual(
            _run_bounded(["git", "add", "."], cwd=source).returncode,
            0,
        )
        self.assertEqual(
            _run_bounded(
                ["git", "commit", "-m", "test: exact compact source"],
                cwd=source,
            ).returncode,
            0,
        )
        scratch = (self.temp_root / "exact-compact").resolve()
        target, _ = _initialize_target(scratch)
        applied = _adopt(source, target, scratch)
        self.assertEqual(
            applied.returncode, 0, (applied.stdout, applied.stderr)
        )
        session_id = "session-smoke-exact-compact"
        task_id = "TASK-SMOKE-EXACT-COMPACT"
        setup = _run_bounded(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--setup-local-task",
                "--repo",
                str(target.resolve()),
                "--session-id",
                session_id,
                "--task-id",
                task_id,
            ],
            cwd=target,
        )
        self.assertEqual(setup.returncode, 0, setup.stderr)
        setup_payload = json.loads(setup.stdout.decode("utf-8"))
        self.assertEqual(
            set(setup_payload), {"task_id", "receipt_digest"}
        )
        expected = _expected_unknown_warning(
            trigger="post_compact",
            framing_status="pending_framing",
        )
        from control_plane.hooks import _task_warning_bindings

        with patch.dict(
            os.environ,
            {"CODEX_CONTROL_PLANE_TASK_ID": task_id},
            clear=False,
        ):
            bindings = _task_warning_bindings(target)
        self.assertIsNotNone(bindings)
        compact = _invoke_hook(
            target,
            {
                "hook_event_name": "SessionStart",
                "source": "compact",
                "cwd": str(target.resolve()),
                "session_id": session_id,
            },
            task_id=task_id,
        )

        self.assertEqual(compact.returncode, 0, compact.stderr)
        self.assertTrue(
            _compact_matches_current(compact.stdout, expected),
            compact.stdout.decode("utf-8", "replace"),
        )

    def test_completed_smoke_is_host_bound_and_non_darwin_is_unknown(self) -> None:
        from control_plane.host_bridge import (
            CompletedMacOSHookSmoke,
            run_macos_hook_smoke,
        )

        with self.assertRaisesRegex(TypeError, "host-bound"):
            CompletedMacOSHookSmoke()
        with patch(
            "control_plane.host_bridge.platform.system",
            return_value="Linux",
        ), patch(
            "control_plane.host_bridge._run_macos_smoke_process"
        ) as child:
            completed = run_macos_hook_smoke(
                canonical_repo=ROOT,
                expected_head=self.head,
                expected_artifact_digests=artifact_digests(),
                session_id="session-task7-smoke",
                invocation_id="invocation-task7-smoke",
                dedicated_temp_root=(
                    self.temp_root / "darwin-smoke"
                ).resolve(),
                clock=lambda: 100.0,
                timeout_seconds=30.0,
            )

        child.assert_not_called()
        self.assertEqual(completed.mechanical_result, "UNKNOWN")
        self.assertEqual(
            tuple(item.case_id for item in completed.cases), SCENARIOS
        )
        self.assertTrue(
            all(item.status == "UNKNOWN" for item in completed.cases)
        )
        self.assertFalse(completed.authorizes)

    def test_darwin_runner_accepts_only_exact_closed_scenario_set(self) -> None:
        from control_plane.host_bridge import run_macos_hook_smoke

        cases = tuple(
            {
                "id": scenario,
                "status": "PASS",
                "exit_code": 0,
                "stdout_digest": f"sha256:{sha256(b'out').hexdigest()}",
                "stderr_digest": f"sha256:{sha256(b'').hexdigest()}",
            }
            for scenario in SCENARIOS
        )
        repository, head = self.tracked_smoke_repository(
            "closed-scenario-set"
        )
        with patch(
            "control_plane.host_bridge.platform.system",
            return_value="Darwin",
        ), patch(
            "control_plane.host_bridge._run_macos_smoke_process",
            return_value=(cases, "absent"),
        ):
            completed = run_macos_hook_smoke(
                canonical_repo=repository,
                expected_head=head,
                expected_artifact_digests=artifact_digests(repository),
                session_id="session-task7-smoke-pass",
                invocation_id="invocation-task7-smoke-pass",
                dedicated_temp_root=(
                    self.temp_root / "darwin-smoke-pass"
                ).resolve(),
                clock=lambda: 100.0,
                timeout_seconds=30.0,
            )
        case_summary = [
            (item.case_id, item.status, item.exit_code)
            for item in completed.cases
        ]
        self.assertEqual(
            completed.mechanical_result, "PASS", case_summary
        )
        self.assertEqual(completed.native_adapter, "absent")
        self.assertFalse(completed.authorizes)

        duplicate = cases[:-1] + (cases[0],)
        with patch(
            "control_plane.host_bridge.platform.system",
            return_value="Darwin",
        ), patch(
            "control_plane.host_bridge._run_macos_smoke_process",
            return_value=(duplicate, "ready"),
        ):
            with self.assertRaisesRegex(ValueError, "E_MACOS_SMOKE_RESULT"):
                run_macos_hook_smoke(
                    canonical_repo=repository,
                    expected_head=head,
                    expected_artifact_digests=artifact_digests(repository),
                    session_id="session-task7-smoke-duplicate",
                    invocation_id="invocation-task7-smoke-duplicate",
                    dedicated_temp_root=(
                        self.temp_root / "darwin-smoke-duplicate"
                    ).resolve(),
                    clock=lambda: 100.0,
                    timeout_seconds=30.0,
                )

    def test_darwin_runner_requires_exact_head_tracked_harness(self) -> None:
        from control_plane.host_bridge import run_macos_hook_smoke

        cases = tuple(
            {
                "id": scenario,
                "status": "PASS",
                "exit_code": 0,
                "stdout_digest": f"sha256:{sha256(b'out').hexdigest()}",
                "stderr_digest": f"sha256:{sha256(b'').hexdigest()}",
            }
            for scenario in SCENARIOS
        )
        repository, head = self.tracked_smoke_repository(
            "tracked-harness"
        )
        expected_harness_digest = _digest(
            repository / "tests" / "macos_hook_smoke.py"
        )
        with patch(
            "control_plane.host_bridge.platform.system",
            return_value="Darwin",
        ), patch(
            "control_plane.host_bridge._run_macos_smoke_process",
            return_value=(cases, "absent"),
        ) as child:
            completed = run_macos_hook_smoke(
                canonical_repo=repository,
                expected_head=head,
                expected_artifact_digests=artifact_digests(repository),
                session_id="session-task7-tracked-harness",
                invocation_id="invocation-task7-tracked-harness",
                dedicated_temp_root=(
                    self.temp_root / "tracked-harness-temp"
                ).resolve(),
                clock=lambda: 100.0,
                timeout_seconds=30.0,
            )

        child.assert_called_once()
        case_summary = [
            (item.case_id, item.status, item.exit_code)
            for item in completed.cases
        ]
        self.assertEqual(
            completed.mechanical_result, "PASS", case_summary
        )
        self.assertEqual(completed.harness_digest, expected_harness_digest)
        self.assertRegex(completed.harness_binding_digest, r"^sha256:[0-9a-f]{64}$")

        harness = repository / "tests" / "macos_hook_smoke.py"
        harness.write_bytes(harness.read_bytes() + b"\n# dirty drift\n")
        with patch(
            "control_plane.host_bridge.platform.system",
            return_value="Darwin",
        ), patch(
            "control_plane.host_bridge._run_macos_smoke_process",
            return_value=(cases, "absent"),
        ) as drifted_child:
            drifted = run_macos_hook_smoke(
                canonical_repo=repository,
                expected_head=head,
                expected_artifact_digests=artifact_digests(repository),
                session_id="session-task7-drifted-harness",
                invocation_id="invocation-task7-drifted-harness",
                dedicated_temp_root=(
                    self.temp_root / "drifted-harness-temp"
                ).resolve(),
                clock=lambda: 101.0,
                timeout_seconds=30.0,
            )

        drifted_child.assert_not_called()
        self.assertEqual(drifted.mechanical_result, "UNKNOWN")
        self.assertNotEqual(
            drifted.harness_binding_digest,
            completed.harness_binding_digest,
        )

    @unittest.skipUnless(
        platform.system() == "Darwin", "native smoke process requires Darwin"
    )
    def test_real_darwin_child_emits_closed_audit_only_contract(self) -> None:
        from control_plane.host_bridge import run_macos_hook_smoke

        repository = (self.temp_root / "real-darwin-runtime").resolve()
        shutil.copytree(
            ROOT,
            repository,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc"
            ),
        )
        initialized = _run_bounded(
            ["git", "init", "-b", "main"],
            cwd=repository,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        for key, value in (
            ("user.name", "Control Plane Smoke Tests"),
            ("user.email", "control-plane-smoke@example.invalid"),
        ):
            configured = _run_bounded(
                ["git", "config", key, value],
                cwd=repository,
            )
            self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertEqual(
            _run_bounded(["git", "add", "."], cwd=repository).returncode,
            0,
        )
        committed = _run_bounded(
            ["git", "commit", "-m", "test: exact smoke candidate"],
            cwd=repository,
        )
        self.assertEqual(committed.returncode, 0, committed.stderr)
        head = _git(repository, "rev-parse", "HEAD")
        completed = run_macos_hook_smoke(
            canonical_repo=repository,
            expected_head=head,
            expected_artifact_digests=artifact_digests(repository),
            session_id="session-task7-native-child",
            invocation_id="invocation-task7-native-child",
            dedicated_temp_root=(
                self.temp_root / "native-child"
            ).resolve(),
            clock=lambda: 100.0,
            timeout_seconds=120.0,
        )

        case_summary = [
            (item.case_id, item.status, item.exit_code)
            for item in completed.cases
        ]
        self.assertEqual(completed.mechanical_result, "PASS", case_summary)
        self.assertEqual(
            tuple(item.case_id for item in completed.cases), SCENARIOS
        )
        self.assertEqual(
            {
                item.case_id: item.status
                for item in completed.cases
            },
            {
                "warning_once": "PASS",
                "sessionstart_compact_fallback": "PASS",
                "safe_read_explicit_repo": "PASS",
                "feature_commit_push": "PASS",
                "base_detached_force_denied": "PASS",
                "stop_receipt": "PASS",
                "rollback_byte_exact": "PASS",
                "source_isolated_parity": "PASS",
            },
        )
        self.assertFalse(completed.authorizes)

    def test_runner_rejects_head_and_artifact_drift_before_child(self) -> None:
        from control_plane.host_bridge import run_macos_hook_smoke

        drifted = artifact_digests()
        drifted["lock"] = f"sha256:{'0' * 64}"
        with patch(
            "control_plane.host_bridge._run_macos_smoke_process"
        ) as child:
            with self.assertRaisesRegex(ValueError, "E_MACOS_SMOKE_BINDING"):
                run_macos_hook_smoke(
                    canonical_repo=ROOT,
                    expected_head="0" * 40,
                    expected_artifact_digests=drifted,
                    session_id="session-task7-smoke-drift",
                    invocation_id="invocation-task7-smoke-drift",
                    dedicated_temp_root=(
                        self.temp_root / "drift"
                    ).resolve(),
                    clock=lambda: 100.0,
                    timeout_seconds=30.0,
                )
        child.assert_not_called()

    def test_smoke_child_timeout_and_output_overflow_kill_the_process(
        self,
    ) -> None:
        from control_plane.host_bridge import _run_macos_smoke_process

        repository = (self.temp_root / "bounded-child").resolve()
        harness = repository / "tests" / "macos_hook_smoke.py"
        harness.parent.mkdir(parents=True)
        temp_root = (self.temp_root / "bounded-child-temp").resolve()
        temp_root.mkdir()

        harness.write_text(
            "import sys\nsys.stdout.buffer.write(b'x' * 262145)\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "exceeded cap"):
            _run_macos_smoke_process(repository, temp_root, 2.0)

        harness.write_text(
            "import time\ntime.sleep(2)\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "timed out"):
            _run_macos_smoke_process(repository, temp_root, 0.05)

        harness.write_text(
            "import os, time\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "time.sleep(0.3)\n",
            encoding="utf-8",
        )
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "timed out"):
            _run_macos_smoke_process(repository, temp_root, 0.05)
        self.assertLess(time.monotonic() - started, 0.2)

        descendant_marker = self.temp_root / "smoke-descendant-finished"
        harness.write_text(
            "import subprocess, sys\n"
            "subprocess.Popen(\n"
            "    [sys.executable, '-c', "
            f"\"import time; time.sleep(0.3); "
            f"open({str(descendant_marker)!r}, 'w').write('done')\"],\n"
            "    stdout=sys.stdout,\n"
            "    stderr=sys.stderr,\n"
            ")\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "timed out"):
            _run_macos_smoke_process(repository, temp_root, 0.05)
        time.sleep(0.4)
        self.assertFalse(descendant_marker.exists())

    def test_smoke_publisher_uses_generation_cas_and_refreshed_context(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import (
            _atomic_receipt_json,
            frame_verification_task_context,
            publish_macos_hook_smoke_receipt,
            run_macos_hook_smoke,
        )
        from control_plane.lifecycle import (
            TaskLease,
            TaskStore,
            VERIFICATION_COMMAND_IDS,
            VerificationExecutionContext,
            _atomic_json,
        )
        from tests.git_test_support import GitScenario

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/task7-smoke")
        common_dir = scenario.repo / ".git"
        runtime_digest = contract_digest({"runtime": "task7-smoke"})
        store = TaskStore(common_dir, runtime_digest=runtime_digest)
        task_id = "TASK-TASK7-MACOS-SMOKE"
        task_digest = contract_digest({"task": task_id})
        store.start(
            task_id,
            outcome="local_change",
            branch="codex/task7-smoke",
            task_digest=task_digest,
            decision_digest=contract_digest({"route": task_id}),
        )
        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
        ):
            state = store.transition(
                task_id,
                target,
                evidence=evidence,
                current_branch="codex/task7-smoke",
            )
        lease = TaskLease.acquire(
            common_dir,
            task_id=task_id,
            worktree=str(scenario.repo.resolve()),
            branch="codex/task7-smoke",
            session_id="session-task7-publish",
            paths=["."],
            policy_digest=runtime_digest,
        )
        copied_artifacts = {}
        for name, relative in ARTIFACT_PATHS.items():
            target = scenario.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
            copied_artifacts[name] = _digest(target)
        harness = scenario.repo / "tests" / "macos_hook_smoke.py"
        harness.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "tests" / "macos_hook_smoke.py", harness)
        _git(scenario.repo, "add", ".")
        _git(scenario.repo, "commit", "-m", "test: bind smoke harness")
        head = _git(scenario.repo, "rev-parse", "HEAD")
        context = object.__new__(VerificationExecutionContext)
        context._consumed = False
        values = {
            "task_id": task_id,
            "task_digest": task_digest,
            "profile": "control_plane_assurance",
            "profile_digest": contract_digest(
                {
                    "profile": "control_plane_assurance",
                    "commands": VERIFICATION_COMMAND_IDS[
                        "control_plane_assurance"
                    ],
                }
            ),
            "runtime_digest": runtime_digest,
            "target_digest": runtime_digest,
            "content_trust": "project_owned",
            "repository": str(scenario.repo.resolve()),
            "worktree": str(scenario.repo.resolve()),
            "expected_head": head,
            "session_id": "session-task7-publish",
            "lease_digest": lease["lease_digest"],
            "dedicated_temp_root": str(
                (self.temp_root / "verification-temp").resolve()
            ),
            "executables": {
                "python": str(Path(sys.executable).resolve()),
                "git": str(Path(shutil.which("git") or "/usr/bin/git").resolve()),
                "control_plane": str((ROOT / "scripts/control-plane").resolve()),
            },
        }
        values["executables_digest"] = contract_digest(values["executables"])
        for key, value in values.items():
            setattr(context, key, value)
        context.context_digest = contract_digest(
            {
                key: getattr(context, key)
                for key in (
                    "task_id",
                    "task_digest",
                    "profile",
                    "profile_digest",
                    "runtime_digest",
                    "target_digest",
                    "content_trust",
                    "repository",
                    "worktree",
                    "expected_head",
                    "session_id",
                    "lease_digest",
                    "executables_digest",
                )
            }
        )
        state.update(
            {
                "verification_profile": context.profile,
                "verification_profile_digest": context.profile_digest,
                "verification_runtime_digest": context.runtime_digest,
                "verification_target_digest": context.target_digest,
                "verification_content_trust": context.content_trust,
                "session_id": context.session_id,
            }
        )
        _atomic_json(store._path(task_id), state)
        task_context = frame_verification_task_context(
            task_store=store,
            execution_context=context,
            expected_generation=state["generation"],
        )
        passing_cases = tuple(
            {
                "id": scenario_id,
                "status": "PASS",
                "exit_code": 0,
                "stdout_digest": f"sha256:{sha256(b'out').hexdigest()}",
                "stderr_digest": f"sha256:{sha256(b'').hexdigest()}",
            }
            for scenario_id in SCENARIOS
        )
        with patch(
            "control_plane.host_bridge.platform.system",
            return_value="Darwin",
        ), patch(
            "control_plane.host_bridge._run_macos_smoke_process",
            return_value=(passing_cases, "ready"),
        ):
            completed = run_macos_hook_smoke(
                canonical_repo=scenario.repo.resolve(),
                expected_head=head,
                expected_artifact_digests=copied_artifacts,
                session_id=context.session_id,
                invocation_id="invocation-task7-publish",
                dedicated_temp_root=(
                    self.temp_root / "publisher-smoke"
                ).resolve(),
                clock=lambda: 100.0,
                timeout_seconds=30.0,
            )
        receipt_path = (
            common_dir
            / "codex-control-plane"
            / "verification-receipts"
            / task_id
            / "MacOSHookSmokeReceipt.json"
        )
        real_atomic_json = _atomic_json
        real_receipt_atomic_json = _atomic_receipt_json

        def fail_receipt_write(path: Path, payload: object) -> None:
            if Path(path) == receipt_path:
                raise OSError("injected receipt write failure")
            real_receipt_atomic_json(path, payload)

        with patch(
            "control_plane.host_bridge._atomic_receipt_json",
            side_effect=fail_receipt_write,
        ):
            with self.assertRaisesRegex(
                OSError, "injected receipt write failure"
            ):
                publish_macos_hook_smoke_receipt(
                    completed,
                    task_store=store,
                    task_context=task_context,
                    expected_generation=state["generation"],
                )
        self.assertFalse(completed._consumed)
        self.assertFalse(task_context._consumed)
        self.assertFalse(receipt_path.exists())

        def fail_state_write(path: Path, payload: object) -> None:
            if Path(path) == store._path(task_id):
                raise OSError("injected task state write failure")
            real_atomic_json(path, payload)

        with patch(
            "control_plane.lifecycle._atomic_json",
            side_effect=fail_state_write,
        ):
            with self.assertRaisesRegex(
                OSError, "injected task state write failure"
            ):
                publish_macos_hook_smoke_receipt(
                    completed,
                    task_store=store,
                    task_context=task_context,
                    expected_generation=state["generation"],
                )
        self.assertFalse(completed._consumed)
        self.assertFalse(task_context._consumed)
        self.assertTrue(receipt_path.is_file())
        self.assertEqual(
            store.status(task_id)["generation"], state["generation"]
        )

        result = publish_macos_hook_smoke_receipt(
            completed,
            task_store=store,
            task_context=task_context,
            expected_generation=state["generation"],
        )

        self.assertEqual(result.receipt.mechanical_result, "PASS")
        self.assertFalse(result.receipt.authorizes)
        self.assertEqual(
            result.task_context.generation, state["generation"] + 1
        )
        self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, "E_MACOS_SMOKE_REPLAY"):
            publish_macos_hook_smoke_receipt(
                completed,
                task_store=store,
                task_context=task_context,
                expected_generation=state["generation"],
            )
        with self.assertRaisesRegex(ValueError, "E_MACOS_SMOKE"):
            publish_macos_hook_smoke_receipt(
                {"mechanical_result": "PASS"},
                task_store=store,
                task_context=result.task_context,
                expected_generation=result.task_context.generation,
            )

    def test_receipt_writer_rejects_ancestor_symlink_escape(self) -> None:
        from control_plane.host_bridge import _atomic_receipt_json

        state_dir = (self.temp_root / "receipt-state").resolve()
        outside = (self.temp_root / "receipt-outside").resolve()
        control_plane_dir = state_dir / "codex-control-plane"
        control_plane_dir.mkdir(parents=True)
        outside.mkdir()
        (control_plane_dir / "verification-receipts").symlink_to(
            outside,
            target_is_directory=True,
        )
        target = (
            control_plane_dir
            / "verification-receipts"
            / "TASK-SYMLINK"
            / "MacOSHookSmokeReceipt.json"
        )

        with self.assertRaises(OSError):
            _atomic_receipt_json(
                target,
                {
                    "schema_version": 1,
                    "receipt_digest": f"sha256:{'0' * 64}",
                },
            )
        self.assertEqual(list(outside.iterdir()), [])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-macos-hook-smoke", action="store_true")
    parser.add_argument("--setup-local-task", action="store_true")
    parser.add_argument("--repo")
    parser.add_argument("--session-id")
    parser.add_argument("--task-id")
    return parser


if __name__ == "__main__":
    parsed = _parser().parse_args()
    if parsed.setup_local_task:
        if not parsed.repo or not parsed.session_id or not parsed.task_id:
            raise SystemExit(2)
        setup_payload = _setup_local_task_state(
            Path(parsed.repo).resolve(),
            session_id=parsed.session_id,
            task_id=parsed.task_id,
        )
        sys.stdout.write(
            json.dumps(
                setup_payload, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        )
        raise SystemExit(0)
    if parsed.run_macos_hook_smoke:
        if not parsed.repo:
            raise SystemExit(2)
        raise SystemExit(_run_child(parsed))
    unittest.main(argv=[sys.argv[0]])
