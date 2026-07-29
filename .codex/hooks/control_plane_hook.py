#!/usr/bin/env -S python3 -I -B
"""Project-local entrypoint for bounded Codex audit hooks."""

from __future__ import annotations

import sys

if not sys.flags.isolated or not sys.flags.safe_path:
    raise SystemExit("E_RUNTIME_BOOTSTRAP: hook requires python3 -I -B")

import importlib
import importlib.util
from hashlib import sha256
from pathlib import Path
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
runtime = ROOT / "control_plane"
spec = importlib.util.spec_from_file_location(
    "control_plane",
    runtime / "__init__.py",
    submodule_search_locations=[str(runtime)],
)
if spec is None or spec.loader is None:
    raise RuntimeError("E_RUNTIME_LAYOUT: source runtime cannot be loaded")
package = importlib.util.module_from_spec(spec)
sys.modules["control_plane"] = package
spec.loader.exec_module(package)
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
