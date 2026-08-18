from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

from control_plane.lockfile import ACTIVE_RUNTIME_MODULES


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_STEMS = frozenset(
    {
        "adoption",
        "candidate_receipt",
        "host_bridge",
        "lifecycle",
        "release_source",
        "run_workflow",
    }
)
FORBIDDEN_TEST_HELPERS = frozenset(
    {
        "tests.host_adapter_test_support",
        "tests.router_test_support",
    }
)

GOVERNING_TEST_MATRIX = {
    "__init__.py": ("tests.test_core_contract",),
    "adoption_recovery.py": (
        "tests.test_core_adoption_recovery",
        "tests.test_core_state_paths",
    ),
    "clarification.py": ("tests.test_core_clarification",),
    "cli.py": ("tests.test_core_cli",),
    "contracts.py": (
        "tests.test_contracts_v2",
        "tests.test_core_contract",
        "tests.test_core_task_state",
        "tests.test_core_verification",
    ),
    "core_types.py": ("tests.test_core_types",),
    "git_guards.py": ("tests.test_core_git_guards",),
    "git_state.py": ("tests.test_core_git_state",),
    "graph.py": ("tests.test_graph",),
    "hooks.py": ("tests.test_core_hooks",),
    "intake.py": ("tests.test_core_intake",),
    "leases.py": (
        "tests.test_core_leases",
        "tests.test_core_state_paths",
        "tests.test_core_task_state",
    ),
    "lockfile.py": ("tests.test_core_lockfile",),
    "maintenance.py": (
        "tests.test_core_maintenance",
        "tests.test_core_state_paths",
    ),
    "materialization.py": ("tests.test_materialization",),
    "policy.py": ("tests.test_core_policy",),
    "project_profiles.py": ("tests.test_core_project_profiles",),
    "repository.py": (
        "tests.test_core_repository",
        "tests.test_core_state_paths",
    ),
    "resource_registry.py": ("tests.test_resource_registry",),
    "risk_sentinel.py": ("tests.test_core_risk_sentinel",),
    "routing.py": ("tests.test_core_routing",),
    "scopes.py": ("tests.test_graph",),
    "stable_pause.py": ("tests.test_core_stable_pause",),
    "task_state.py": (
        "tests.test_core_task_state",
        "tests.test_core_state_paths",
    ),
    "toolchain.py": ("tests.test_core_toolchain",),
    "verification.py": (
        "tests.test_core_verification",
        "tests.test_core_state_paths",
    ),
}

GOVERNING_TESTS = frozenset(
    {
        "tests.test_contracts_v2",
        "tests.test_graph",
        "tests.test_materialization",
        "tests.test_resource_registry",
        "tests.test_core_adoption_recovery",
        "tests.test_core_clarification",
        "tests.test_core_cli",
        "tests.test_core_contract",
        "tests.test_core_documentation",
        "tests.test_core_git_guards",
        "tests.test_core_git_state",
        "tests.test_core_governing_manifest",
        "tests.test_core_hooks",
        "tests.test_core_intake",
        "tests.test_core_leases",
        "tests.test_core_lockfile",
        "tests.test_core_maintenance",
        "tests.test_core_plugin",
        "tests.test_core_policy",
        "tests.test_core_project_profiles",
        "tests.test_core_quarantine",
        "tests.test_core_repository",
        "tests.test_core_risk_sentinel",
        "tests.test_core_routing",
        "tests.test_core_state_paths",
        "tests.test_core_stable_pause",
        "tests.test_core_task_state",
        "tests.test_core_toolchain",
        "tests.test_core_types",
        "tests.test_core_verification",
    }
)
GOVERNING_HELPERS = frozenset(
    {
        "tests.core_gate",
        "tests.core_router_test_support",
        "tests.core_stable_pause_test_support",
        "tests.git_test_support",
    }
)
ADOPTION_MODULE_FILES = frozenset(
    {
        "adoption_enablement/__init__.py",
        "adoption_enablement/cli.py",
        "adoption_enablement/contracts.py",
        "adoption_enablement/lockfile.py",
        "adoption_enablement/manifest.py",
        "adoption_enablement/repository.py",
        "adoption_enablement/safe_io.py",
        "adoption_enablement/transaction.py",
    }
)
ADOPTION_TESTS = frozenset(
    {
        "tests.test_adoption_enablement_contracts",
        "tests.test_adoption_enablement_repository",
        "tests.test_adoption_enablement_preview",
        "tests.test_adoption_enablement_transaction",
        "tests.test_adoption_enablement_recovery",
        "tests.test_adoption_enablement_bootstrap",
        "tests.test_adoption_enablement_e2e",
    }
)
ADOPTION_HELPERS = frozenset({"tests.adoption_enablement_test_support"})
ADOPTION_GATE_FILES = frozenset(
    {
        "scripts/control-plane-adoption",
        ".codex/adoption-enablement.lock",
    }
)


def _shell_words(variable: str) -> tuple[str, ...]:
    source = (ROOT / "tests" / "run.sh").read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(variable)}='(?P<body>.*?)'$",
        source,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"{variable} is missing from tests/run.sh")
    words = tuple(match.group("body").split())
    if len(words) != len(set(words)):
        raise AssertionError(f"{variable} contains duplicates")
    return words


def _module_path(module: str) -> Path:
    return ROOT / (module.replace(".", "/") + ".py")


def _forbidden_runtime_import(module: str) -> bool:
    parts = module.split(".")
    return (
        len(parts) >= 2
        and parts[0] == "control_plane"
        and parts[1] in FORBIDDEN_RUNTIME_STEMS
    )


def _imported_modules(tree: ast.AST) -> tuple[set[str], tuple[str, ...]]:
    imported: set[str] = set()
    dynamic_targets: list[str] = []
    module_aliases: dict[str, str] = {}
    dynamic_callables: dict[str, str] = {
        "__import__": "import",
        "eval": "eval",
        "exec": "exec",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
                if alias.name in {"builtins", "importlib"}:
                    module_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                if node.module in {"control_plane", "tests"}:
                    imported.add(f"{node.module}.{alias.name}")
                if node.module == "importlib" and alias.name == "import_module":
                    dynamic_callables[alias.asname or alias.name] = "import"
                if node.module == "builtins" and alias.name in {
                    "__import__",
                    "eval",
                    "exec",
                }:
                    dynamic_callables[alias.asname or alias.name] = (
                        "import" if alias.name == "__import__" else alias.name
                    )

    def callable_kind(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return dynamic_callables.get(value.id)
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            module = module_aliases.get(value.value.id)
            if module == "importlib" and value.attr == "import_module":
                return "import"
            if module == "builtins" and value.attr in {"__import__", "eval", "exec"}:
                return "import" if value.attr == "__import__" else value.attr
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "getattr"
            and len(value.args) >= 2
            and isinstance(value.args[0], ast.Name)
            and isinstance(value.args[1], ast.Constant)
            and isinstance(value.args[1].value, str)
        ):
            module = module_aliases.get(value.args[0].id)
            attribute = value.args[1].value
            if module == "importlib" and attribute == "import_module":
                return "import"
            if module == "builtins" and attribute in {"__import__", "eval", "exec"}:
                return "import" if attribute == "__import__" else attribute
        return None

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue
            kind = callable_kind(value)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and isinstance(value, ast.Name)
                    and value.id in module_aliases
                    and module_aliases.get(target.id) != module_aliases[value.id]
                ):
                    module_aliases[target.id] = module_aliases[value.id]
                    changed = True
                if isinstance(target, ast.Name) and dynamic_callables.get(target.id) != kind:
                    if kind is not None:
                        dynamic_callables[target.id] = kind
                        changed = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kind = callable_kind(node.func)
            if kind == "import":
                target = (
                    node.args[0].value
                    if node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    else "<nonliteral>"
                )
                dynamic_targets.append(target)
            elif kind in {"eval", "exec"}:
                dynamic_targets.append(f"<{kind}>")
    return imported, tuple(dynamic_targets)


class CoreGoverningManifestTests(unittest.TestCase):
    def test_ast_scanner_closes_alias_and_from_import_bypasses(self) -> None:
        tree = ast.parse(
            "import importlib as loader\n"
            "from importlib import import_module as load\n"
            "from control_plane import host_bridge\n"
            "loader.import_module('control_plane.lifecycle')\n"
            "load('control_plane.run_workflow')\n"
        )
        imported, dynamic_targets = _imported_modules(tree)
        self.assertIn("control_plane.host_bridge", imported)
        self.assertEqual(
            dynamic_targets,
            ("control_plane.lifecycle", "control_plane.run_workflow"),
        )

    def test_ast_scanner_closes_callable_getattr_and_builtins_bypasses(self) -> None:
        tree = ast.parse(
            "import builtins as bi\n"
            "import importlib as loader\n"
            "from importlib import import_module as load\n"
            "first = load\n"
            "second = getattr(loader, 'import_module')\n"
            "third = bi.__import__\n"
            "first('control_plane.lifecycle')\n"
            "second('control_plane.host_bridge')\n"
            "third('control_plane.run_workflow')\n"
            "eval('1 + 1')\n"
        )
        _, dynamic_targets = _imported_modules(tree)
        self.assertEqual(
            dynamic_targets,
            (
                "control_plane.lifecycle",
                "control_plane.host_bridge",
                "control_plane.run_workflow",
                "<eval>",
            ),
        )

    def test_ast_scanner_closes_module_assignment_and_root_from_import(self) -> None:
        tree = ast.parse(
            "import importlib\n"
            "module_alias = importlib\n"
            "loader = getattr(module_alias, 'import_module')\n"
            "from control_plane import undeclared_origin\n"
            "loader('control_plane.undeclared_origin')\n"
        )

        imported, dynamic_targets = _imported_modules(tree)

        self.assertIn("control_plane.undeclared_origin", imported)
        self.assertEqual(dynamic_targets, ("control_plane.undeclared_origin",))

    def test_every_active_runtime_module_has_explicit_governing_behavior_tests(
        self,
    ) -> None:
        self.assertEqual(
            set(GOVERNING_TEST_MATRIX),
            set(ACTIVE_RUNTIME_MODULES),
        )
        for module, tests in GOVERNING_TEST_MATRIX.items():
            with self.subTest(module=module):
                self.assertTrue(tests)
                self.assertTrue(set(tests).issubset(GOVERNING_TESTS))
                for test in tests:
                    self.assertTrue(_module_path(test).is_file(), test)

    def test_runner_manifest_is_exact_and_compiles_all_governing_sources(
        self,
    ) -> None:
        declared_tests = frozenset(_shell_words("CORE_TESTS"))
        declared_test_files = frozenset(_shell_words("CORE_TEST_FILES"))
        declared_helpers = frozenset(_shell_words("CORE_TEST_HELPERS"))
        declared_package = tuple(_shell_words("CORE_TEST_PACKAGE"))
        declared_gate_files = frozenset(_shell_words("CORE_GATE_FILES"))
        adoption_modules = frozenset(_shell_words("ADOPTION_MODULES"))
        adoption_tests = frozenset(_shell_words("ADOPTION_TESTS"))
        adoption_test_files = frozenset(_shell_words("ADOPTION_TEST_FILES"))
        adoption_helpers = frozenset(_shell_words("ADOPTION_TEST_HELPERS"))
        adoption_gate_files = frozenset(_shell_words("ADOPTION_GATE_FILES"))

        self.assertEqual(declared_tests, GOVERNING_TESTS)
        self.assertEqual(
            declared_test_files,
            frozenset(
                str(_module_path(module).relative_to(ROOT))
                for module in GOVERNING_TESTS
            ),
        )
        self.assertEqual(
            declared_helpers,
            frozenset(
                str(_module_path(module).relative_to(ROOT))
                for module in GOVERNING_HELPERS
            ),
        )
        self.assertEqual(declared_package, ("tests/__init__.py",))
        self.assertEqual(
            declared_gate_files,
            frozenset(
                {
                    "scripts/control-plane",
                    "scripts/build-release-candidate",
                    "tests/run.sh",
                }
            ),
        )
        self.assertEqual(adoption_modules, ADOPTION_MODULE_FILES)
        self.assertEqual(adoption_tests, ADOPTION_TESTS)
        self.assertEqual(
            adoption_test_files,
            frozenset(
                str(_module_path(module).relative_to(ROOT))
                for module in ADOPTION_TESTS
            ),
        )
        self.assertEqual(
            adoption_helpers,
            frozenset(
                str(_module_path(module).relative_to(ROOT))
                for module in ADOPTION_HELPERS
            ),
        )
        self.assertEqual(adoption_gate_files, ADOPTION_GATE_FILES)

        observed_adoption_sources = frozenset(
            str(path.relative_to(ROOT))
            for path in (ROOT / "adoption_enablement").iterdir()
            if path.is_file()
        )
        observed_adoption_tests = frozenset(
            str(path.relative_to(ROOT))
            for path in (ROOT / "tests").glob("test_adoption_enablement_*.py")
        )
        self.assertEqual(observed_adoption_sources, ADOPTION_MODULE_FILES)
        self.assertEqual(observed_adoption_tests, adoption_test_files)

    def test_core_and_adoption_runtime_import_boundaries_are_bidirectionally_closed(self) -> None:
        for relative in sorted(ADOPTION_MODULE_FILES):
            tree = ast.parse((ROOT / relative).read_bytes(), filename=relative)
            imported, dynamic_targets = _imported_modules(tree)
            self.assertFalse(
                any(name == "control_plane" or name.startswith("control_plane.") for name in imported),
                relative,
            )
            self.assertFalse(
                any(target == "control_plane" or target.startswith("control_plane.") for target in dynamic_targets),
                relative,
            )
        for name in ACTIVE_RUNTIME_MODULES:
            relative = ROOT / "control_plane" / name
            tree = ast.parse(relative.read_bytes(), filename=str(relative))
            imported, dynamic_targets = _imported_modules(tree)
            self.assertFalse(
                any(name == "adoption_enablement" or name.startswith("adoption_enablement.") for name in imported),
                str(relative),
            )
            self.assertFalse(
                any(target == "adoption_enablement" or target.startswith("adoption_enablement.") for target in dynamic_targets),
                str(relative),
            )

    def test_governing_tests_and_helpers_have_no_advanced_import_path(self) -> None:
        sources = GOVERNING_TESTS | GOVERNING_HELPERS
        for module in sorted(sources):
            path = _module_path(module)
            with self.subTest(module=module):
                self.assertTrue(path.is_file(), module)
                tree = ast.parse(path.read_bytes(), filename=str(path))
                imported, dynamic_targets = _imported_modules(tree)
                forbidden = {
                    name
                    for name in imported
                    if _forbidden_runtime_import(name)
                    or name in FORBIDDEN_TEST_HELPERS
                    or name == "adoption_enablement"
                    or name.startswith("adoption_enablement.")
                    or name.startswith("tests.test_adoption_enablement_")
                }
                self.assertEqual(forbidden, set())
                forbidden_dynamic = tuple(
                    target
                    for target in dynamic_targets
                    if target == "<nonliteral>"
                    or _forbidden_runtime_import(target)
                )
                self.assertEqual(
                    forbidden_dynamic,
                    (),
                    f"{module} dynamically imports outside the Core boundary",
                )
                local_helpers = {
                    name
                    for name in imported
                    if name.startswith("tests.")
                }
                self.assertTrue(
                    local_helpers.issubset(sources),
                    f"undeclared governing helpers: {sorted(local_helpers - sources)}",
                )


if __name__ == "__main__":
    unittest.main()
