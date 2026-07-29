#!/usr/bin/env python3
"""Project-local entrypoint for bounded Codex audit hooks."""

from __future__ import annotations

import importlib
from hashlib import sha256
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def _validate_source_runtime() -> None:
    try:
        lock = tomllib.loads(
            (ROOT / ".codex" / "control-plane.lock").read_text(
                encoding="utf-8"
            )
        )
    except Exception as error:
        raise RuntimeError(f"E_RUNTIME_BOOTSTRAP: invalid lock: {error}") from error
    if (
        lock.get("runtime_layout") != "source"
        or lock.get("runtime_package") != "control_plane"
    ):
        raise RuntimeError(
            "E_RUNTIME_LAYOUT: source hook requires source runtime"
        )
    runtime = ROOT / "control_plane"
    if runtime.is_symlink() or not runtime.is_dir():
        raise RuntimeError("E_RUNTIME_LAYOUT: source runtime is unavailable")
    modules = sorted(runtime.glob("*.py"))
    if not modules:
        raise RuntimeError("E_RUNTIME_LAYOUT: source runtime is empty")
    hasher = sha256()
    for path in modules:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("E_RUNTIME_LAYOUT: invalid runtime module")
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    if lock.get("digests", {}).get("runtime") != (
        f"sha256:{hasher.hexdigest()}"
    ):
        raise RuntimeError("E_RUNTIME_DIGEST: source runtime does not match lock")


_validate_source_runtime()
sys.path[:] = [str(ROOT)] + [
    item
    for item in sys.path
    if item and Path(item).resolve() != ROOT.resolve()
]
run_hook = importlib.import_module("control_plane.hooks").run_hook


def main() -> int:
    try:
        output = run_hook(sys.stdin.buffer.read())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
