#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TEST_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

python3 -m unittest discover -s tests -v
python3 -m compileall -q control_plane tests
bash -n scripts/control-plane tests/run.sh
scripts/control-plane policy-check --policy .codex/project-policy.toml --json >/dev/null
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml \
  --json >/dev/null
scripts/control-plane inventory --json >/dev/null
scripts/control-plane doctor --json >/dev/null
git diff --check
