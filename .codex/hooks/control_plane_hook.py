#!/usr/bin/env python3
"""Project-local entrypoint for bounded Codex audit hooks."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".codex" / "runtime"
IS_ADOPTED = (
    RUNTIME / "codex_control_plane_runtime_v2" / "hooks.py"
).is_file()
IMPORT_ROOT = RUNTIME if IS_ADOPTED else ROOT
PACKAGE = "codex_control_plane_runtime_v2" if IS_ADOPTED else "control_plane"
sys.path[:] = [str(IMPORT_ROOT)] + [
    item
    for item in sys.path
    if item
    and Path(item).resolve() not in {ROOT.resolve(), RUNTIME.resolve()}
]
run_hook = importlib.import_module(f"{PACKAGE}.hooks").run_hook


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
