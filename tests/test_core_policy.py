from __future__ import annotations

import copy
import tempfile
from pathlib import Path
import unittest

from control_plane.contracts import contract_digest
from tests.core_router_test_support import VALID_POLICY


class CorePolicyTests(unittest.TestCase):
    def test_schema_rejects_unknown_and_unsafe_git_or_worker_values(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = load_policy(VALID_POLICY)
        policy["surprise"] = True
        policy["git"]["base_branch"] = "../main"
        policy["reasoning"]["normal_max_workers"] = 3
        codes = {issue.code for issue in validate_policy(policy)}
        self.assertTrue({"P_UNKNOWN", "P_BASE_BRANCH", "P_WORKERS"}.issubset(codes))

    def test_policy_load_parse_failure_and_opaque_seal_are_fail_closed(self) -> None:
        from control_plane.policy import (
            GoverningPolicy,
            PolicyError,
            _governing_policy_snapshot,
            load_policy,
            seal_governing_policy,
        )

        with tempfile.TemporaryDirectory() as temporary:
            malformed = Path(temporary) / "policy.toml"
            malformed.write_text("[git\n", encoding="utf-8")
            with self.assertRaisesRegex(PolicyError, "could not be parsed"):
                load_policy(malformed)

        source = load_policy(VALID_POLICY)
        sealed = seal_governing_policy(
            source,
            runtime_digest=contract_digest({"runtime": "core"}),
            lock_digest=contract_digest({"lock": "core"}),
            governing_base_commit="a" * 40,
            remote_repository="example/control-plane",
        )
        self.assertIs(type(sealed), GoverningPolicy)
        source["git"]["base_branch"] = "mutated"
        snapshot = _governing_policy_snapshot(sealed)
        self.assertEqual(snapshot["policy"]["git"]["base_branch"], "main")

        forged = object.__new__(GoverningPolicy)
        for name, value in snapshot.items():
            setattr(forged, name, copy.deepcopy(value))
        self.assertIsNone(_governing_policy_snapshot(forged))


if __name__ == "__main__":
    unittest.main()
