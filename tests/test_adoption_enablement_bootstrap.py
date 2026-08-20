from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from adoption_enablement.lockfile import (
    ADOPTION_MODULES,
    runtime_digest,
    validate_lock,
)
from tests.adoption_enablement_test_support import (
    git,
    initialize_fresh_target,
    initialize_full_source,
    metadata_snapshot,
    write_file,
)


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "control-plane-adoption"
EXPECTED_MODULES = (
    "__init__.py",
    "cli.py",
    "contracts.py",
    "lockfile.py",
    "manifest.py",
    "repository.py",
    "safe_io.py",
    "transaction.py",
)


def _domain_runtime_digest(root: Path) -> str:
    hasher = sha256(b"control-plane-adoption-enablement-v1\0")
    for name in EXPECTED_MODULES:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((root / "adoption_enablement" / name).read_bytes())
        hasher.update(b"\0")
    return "sha256:" + hasher.hexdigest()


class AdoptionBootstrapTests(unittest.TestCase):
    def _write_lock(self, root: Path) -> None:
        lock = root / ".codex" / "adoption-enablement.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        modules = "\n".join(f'  "{name}",' for name in EXPECTED_MODULES)
        lock.write_text(
            "schema_version = 1\n"
            'tool_version = "0.1.0"\n'
            'runtime_package = "adoption_enablement"\n'
            'runtime_layout = "source"\n'
            "runtime_modules = [\n"
            f"{modules}\n"
            "]\n\n"
            "[digests]\n"
            f'entrypoint = "sha256:{sha256((root / "scripts" / "control-plane-adoption").read_bytes()).hexdigest()}"\n'
            f'runtime = "{_domain_runtime_digest(root)}"\n',
            encoding="utf-8",
        )

    def _fixture(
        self,
        root: Path,
        marker: Path,
        *,
        package_source: str = "# captured package\n",
    ) -> Path:
        package = root / "adoption_enablement"
        package.mkdir(parents=True)
        (root / "scripts").mkdir()
        shutil.copy2(ENTRYPOINT, root / "scripts" / "control-plane-adoption")
        for name in EXPECTED_MODULES:
            (package / name).write_text(f"# {name}\n", encoding="utf-8")
        (package / "__init__.py").write_text(package_source, encoding="utf-8")
        (package / "cli.py").write_text(
            "from pathlib import Path\n"
            "def main(argv):\n"
            f"    Path({str(marker)!r}).write_text('captured', encoding='utf-8')\n"
            "    print('{\"authorizes\":false}')\n"
            "    return 0\n",
            encoding="utf-8",
        )
        self._write_lock(root)
        return package / "cli.py"

    def _run(
        self,
        root: Path,
        *,
        environment: dict[str, str] | None = None,
        input_payload: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(root / "scripts" / "control-plane-adoption"), "status"],
            cwd=root,
            env=environment,
            input=input_payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )

    def test_lock_declares_exact_isolated_adoption_runtime(self) -> None:
        self.assertEqual(ADOPTION_MODULES, EXPECTED_MODULES)
        self.assertEqual(validate_lock(ROOT), ())
        self.assertEqual(runtime_digest(ROOT), _domain_runtime_digest(ROOT))
        core_lock = (ROOT / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
        self.assertNotIn("adoption_enablement", core_lock)

    def test_stage0_launcher_opens_every_runtime_leaf_nonblocking(self) -> None:
        source = ENTRYPOINT.read_text(encoding="utf-8")
        read_private = source.split("def read_private(path, maximum, code):", 1)[1]
        read_private = read_private.split("\ndef inventory(package):", 1)[0]

        self.assertIn('getattr(os, "O_NONBLOCK", 0)', read_private)
        self.assertLess(read_private.index("flags = ("), read_private.index("os.open(path, flags)"))
        self.assertLess(read_private.index("os.open(path, flags)"), read_private.index("os.fstat(descriptor)"))

    def test_launcher_isolated_commands_and_errors_are_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            plan = scratch / "plan.json"
            plan.write_text("{}\n", encoding="utf-8")
            cases = (
                ("unknown",),
                ("adopt", "plan"),
                ("upgrade", "apply"),
            )
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [str(ENTRYPOINT), *arguments],
                        cwd=ROOT,
                        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=10,
                    )
                    self.assertEqual(completed.returncode, 2)
                    payload = json.loads(completed.stdout)
                    self.assertEqual(payload["error_code"], "E_ADOPTION_USAGE")
                    self.assertIs(payload["authorizes"], False)
                    self.assertNotIn("Traceback", completed.stdout + completed.stderr)
            core = subprocess.run(
                [str(ROOT / "scripts" / "control-plane"), "adoption", "status"],
                cwd=ROOT,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
            )
            self.assertNotEqual(core.returncode, 0)

    def test_preview_uncertainty_is_explicitly_inapplicable_and_zero_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory).resolve(strict=True)
            source = initialize_full_source(scratch / "source", ROOT)
            target = initialize_fresh_target(scratch / "target")
            write_file(target, "scripts/control-plane", "occupied\n", mode=0o755)
            git(target, "add", "--all")
            git(target, "commit", "-m", "ineligible managed destination")
            before = metadata_snapshot(target)

            completed = subprocess.run(
                [
                    str(ENTRYPOINT),
                    "preview",
                    "--source",
                    str(source),
                    "--target",
                    str(target),
                    "--json",
                ],
                cwd=ROOT,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["result"], "UNKNOWN")
            self.assertIs(payload["applicable"], False)
            self.assertIs(payload["mutation"], False)
            self.assertIs(payload["authorizes"], False)
            self.assertEqual(before, metadata_snapshot(target))

    def test_apply_rejects_fifo_plan_without_opening_or_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory).resolve(strict=True)
            fifo = scratch / "reviewed-plan.json"
            os.mkfifo(fifo, 0o600)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "adoption_enablement.cli",
                    "apply",
                    "--source",
                    str(scratch),
                    "--target",
                    str(scratch),
                    "--plan",
                    str(fifo),
                    "--plan-digest",
                    "sha256:" + "0" * 64,
                    "--json",
                ],
                cwd=ROOT,
                env={
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.fail("adoption CLI opened and blocked on a FIFO plan")

            self.assertEqual(process.returncode, 2, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["error_code"], "E_ADOPTION_PLAN")
            self.assertIs(payload["authorizes"], False)
            self.assertNotIn("Traceback", stdout + stderr)

    def test_bootstrap_rejects_pyc_shadow_extra_symlink_hardlink_and_mode_attacks(self) -> None:
        for attack in ("pyc", "shadow", "extra", "symlink", "hardlink", "mode"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                marker = root / "marker"
                target = self._fixture(root, marker)
                outside = root / "outside.py"
                outside.write_text("# outside\n", encoding="utf-8")
                if attack == "pyc":
                    malicious = target.read_text(encoding="utf-8").replace("captured", "reopened")
                    self.assertEqual(len(malicious), len(target.read_text(encoding="utf-8")))
                    target.write_text(malicious, encoding="utf-8")
                    fixed = 1_700_000_000
                    os.utime(target, (fixed, fixed))
                    cache = target.parent / "__pycache__" / (
                        f"cli.{sys.implementation.cache_tag}.pyc"
                    )
                    cache.parent.mkdir()
                    py_compile.compile(str(target), cfile=str(cache), doraise=True)
                    target.write_text(
                        malicious.replace("reopened", "captured"),
                        encoding="utf-8",
                    )
                elif attack == "shadow":
                    shadow = target.parent / "cli"
                    shadow.mkdir()
                    (shadow / "__init__.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
                elif attack == "extra":
                    (target.parent / "extra.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
                elif attack == "symlink":
                    target.unlink()
                    target.symlink_to(outside)
                elif attack == "hardlink":
                    target.unlink()
                    os.link(outside, target)
                else:
                    target.chmod(0o666)

                completed = self._run(root, environment={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})

                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(marker.exists())

    def test_bootstrap_uses_captured_bytes_after_source_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(strict=True)
            marker = root / "marker"
            cli = root / "adoption_enablement" / "cli.py"
            malicious = (
                "from pathlib import Path\n"
                "def main(argv):\n"
                f"    Path({str(marker)!r}).write_text('reopened', encoding='utf-8')\n"
                "    return 0\n"
            )
            package_source = (
                "from pathlib import Path\n"
                f"Path({str(cli)!r}).write_text({malicious!r}, encoding='utf-8')\n"
            )
            self._fixture(root, marker, package_source=package_source)

            completed = self._run(root, environment={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "captured")

    def test_hostile_environment_site_and_startup_files_never_execute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(strict=True)
            marker = root / "verified-marker"
            hostile = root / "hostile-marker"
            self._fixture(root, marker)
            startup = root / "startup.py"
            startup.write_text(
                f"from pathlib import Path; Path({str(hostile)!r}).write_text('bad')\n",
                encoding="utf-8",
            )
            environment = {
                "PATH": str(root),
                "HOME": str(root),
                "PYTHONPATH": str(root),
                "PYTHONHOME": str(root),
                "BASH_ENV": str(startup),
                "ENV": str(startup),
                "LD_PRELOAD": str(startup),
                "DYLD_INSERT_LIBRARIES": str(startup),
                "LC_ALL": "C",
            }
            (root / "sitecustomize.py").write_text(startup.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "usercustomize.py").write_text(startup.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "hostile.pth").write_text(startup.read_text(encoding="utf-8"), encoding="utf-8")

            completed = self._run(root, environment=environment, input_payload="preserved-stdin")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(marker.exists())
            self.assertFalse(hostile.exists())

    def test_private_file_checks_bind_owner_mode_links_and_dataless(self) -> None:
        import adoption_enablement.lockfile as lockfile

        baseline = os.stat_result((stat.S_IFREG | 0o644, 1, os.geteuid(), 1, os.geteuid(), 0, 1, 0, 0, 0))
        self.assertTrue(lockfile._private_regular(baseline, 10))
        with patch.object(lockfile.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertFalse(lockfile._private_regular(baseline, 10))
        unsafe_mode = list(baseline)
        unsafe_mode[0] = stat.S_IFREG | 0o666
        self.assertFalse(lockfile._private_regular(os.stat_result(unsafe_mode), 10))
        hardlink = list(baseline)
        hardlink[3] = 2
        self.assertFalse(lockfile._private_regular(os.stat_result(hardlink), 10))
        with patch.object(lockfile, "_dataless", return_value=True):
            self.assertFalse(lockfile._private_regular(baseline, 10))


if __name__ == "__main__":
    unittest.main()
