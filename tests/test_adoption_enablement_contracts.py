from __future__ import annotations

import copy
import json
import unittest

from adoption_enablement.contracts import (
    JOURNAL_KEYS,
    PLAN_KEYS,
    RECEIPT_KEYS,
    REQUIREMENT_IDS,
    contract_digest,
    load_closed_json,
    validate_journal,
    validate_plan,
    validate_receipt,
)


SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def sealed_plan() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreAdoptionPlanV1",
        "source": {
            "head": COMMIT_A,
            "tree": COMMIT_B,
            "product_version": "3.1.0-core.2",
            "runtime_digest": SHA_A,
            "lock_digest": SHA_B,
            "manifest_digest": SHA_C,
        },
        "target": {
            "repository_id": [1, 2],
            "common_dir_id": [1, 3],
            "worktree_id": [1, 4],
            "branch": "codex/adoption-target",
            "head": COMMIT_B,
            "policy_digest": SHA_A,
            "registry_digest": SHA_B,
            "before_snapshot_digest": SHA_C,
            "core_hooks_path_before": None,
            "adoption_lifecycle": "journal-bound-v1",
            "managed_parent_directories": [
                {
                    "path": ".codex",
                    "state": "present",
                    "identity": [1, 5],
                    "mode": 0o755,
                },
                {"path": "control_plane", "state": "absent"},
                {"path": "scripts", "state": "absent"},
                {"path": ".codex/git-hooks", "state": "absent"},
                {"path": ".codex/hooks", "state": "absent"},
            ],
            "managed_repository_scan": {
                "contract": "managed-repositories-v1",
                "nested_repositories_absent": True,
                "gitlinks_absent": True,
            },
        },
        "managed_records": [
            {
                "path": "scripts/control-plane",
                "role": "entrypoint",
                "sha256": SHA_A,
                "git_mode": "100755",
                "size_bytes": 128,
            }
        ],
        "before_snapshot_digest": SHA_C,
        "requirement_ids": list(REQUIREMENT_IDS),
        "result": "PASS",
        "applicable": True,
        "mutation": False,
        "error_codes": [],
        "authorizes": False,
    }
    value["plan_digest"] = contract_digest(value)
    return value


def sealed_journal() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreAdoptionJournalV1",
        "plan_digest": SHA_A,
        "install_digest": SHA_B,
        "state": "prepared",
        "source_manifest_digest": SHA_C,
        "target_binding": {
            "repository_id": [1, 2],
            "common_dir_id": [1, 3],
            "worktree_id": [1, 4],
            "branch": "codex/adoption-target",
            "head": COMMIT_B,
            "policy_digest": SHA_A,
            "registry_digest": SHA_B,
            "adoption_lifecycle": "journal-bound-v1",
        },
        "before_snapshot_digest": SHA_A,
        "managed_parent_directories": [
            {
                "path": ".codex",
                "state": "present",
                "identity": [1, 5],
                "mode": 0o755,
            },
            {"path": "control_plane", "state": "absent"},
            {"path": "scripts", "state": "absent"},
            {"path": ".codex/git-hooks", "state": "absent"},
            {"path": ".codex/hooks", "state": "absent"},
        ],
        "managed_repository_scan": {
            "contract": "managed-repositories-v1",
            "nested_repositories_absent": True,
            "gitlinks_absent": True,
        },
        "lifecycle_lock": {
            "path": "codex-control-plane-core/adoption.lock",
            "device": 1,
            "inode": 6,
            "mode": 0o600,
            "links": 1,
            "uid": 501,
            "gid": 20,
            "size": 0,
            "mtime_ns": 7,
            "ctime_ns": 8,
            "flags": 0,
        },
        "verification_lock": {
            "directory": {
                "path": "codex-control-plane-core/locks",
                "device": 1,
                "inode": 9,
                "mode": 0o700,
                "uid": 501,
                "gid": 20,
                "flags": 0,
            },
            "file": {
                "path": "codex-control-plane-core/locks/verification.lock",
                "device": 1,
                "inode": 10,
                "mode": 0o600,
                "links": 1,
                "uid": 501,
                "gid": 20,
                "size": 0,
                "mtime_ns": 11,
                "ctime_ns": 12,
                "flags": 0,
            },
        },
        "created_directories": [],
        "published_records": [],
        "target_lock_record": {
            "path": ".codex/control-plane.lock",
            "role": "activation_pointer",
            "sha256": SHA_B,
            "git_mode": "100644",
            "size_bytes": 256,
        },
        "prior_git_config": {"core.hooksPath": None},
        "rollback_records": [
            {
                "path": ".codex/control-plane.lock",
                "role": "activation_pointer",
                "sha256": SHA_B,
                "git_mode": "100644",
                "size_bytes": 256,
                "before": "absent",
            }
        ],
        "authorizes": False,
    }
    value["state_digest"] = contract_digest(value)
    return value


def sealed_receipt() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreAdoptionReceiptV1",
        "operation": "apply",
        "plan_digest": SHA_A,
        "install_digest": SHA_B,
        "before_snapshot_digest": SHA_C,
        "after_snapshot_digest": SHA_A,
        "result": "PASS",
        "error_codes": [],
        "lifecycle_lock": {
            "path": "codex-control-plane-core/adoption.lock",
            "device": 1,
            "inode": 6,
            "mode": 0o600,
            "links": 1,
            "uid": 501,
            "gid": 20,
            "size": 0,
            "mtime_ns": 7,
            "ctime_ns": 8,
            "flags": 0,
        },
        "authorizes": False,
    }
    value["receipt_digest"] = contract_digest(value)
    return value


class AdoptionContractTests(unittest.TestCase):
    def test_closed_contracts_accept_only_the_exact_schema(self) -> None:
        plan = sealed_plan()
        journal = sealed_journal()
        receipt = sealed_receipt()

        self.assertEqual(set(plan), PLAN_KEYS)
        self.assertEqual(set(journal), JOURNAL_KEYS)
        self.assertEqual(set(receipt), RECEIPT_KEYS)
        self.assertEqual(validate_plan(plan), ())
        self.assertEqual(validate_journal(journal), ())
        self.assertEqual(validate_receipt(receipt), ())

        for validator, value in (
            (validate_plan, plan),
            (validate_journal, journal),
            (validate_receipt, receipt),
        ):
            with self.subTest(validator=validator.__name__):
                mutated = copy.deepcopy(value)
                mutated["unexpected"] = "field"
                self.assertIn(
                    "E_ADOPTION_SCHEMA",
                    {issue.code for issue in validator(mutated)},
                )

    def test_authorizes_must_be_the_false_boolean_everywhere(self) -> None:
        for invalid in (True, 0, "false", None):
            with self.subTest(invalid=invalid):
                value = sealed_plan()
                value["authorizes"] = invalid
                value["plan_digest"] = contract_digest(
                    {key: item for key, item in value.items() if key != "plan_digest"}
                )
                self.assertIn(
                    "E_ADOPTION_AUTHORITY",
                    {issue.code for issue in validate_plan(value)},
                )

        nested = sealed_plan()
        nested["source"]["authorizes"] = True  # type: ignore[index]
        nested["plan_digest"] = contract_digest(
            {key: item for key, item in nested.items() if key != "plan_digest"}
        )
        self.assertIn(
            "E_ADOPTION_AUTHORITY",
            {issue.code for issue in validate_plan(nested)},
        )

    def test_separate_expected_digest_rejects_a_resigned_mutation(self) -> None:
        original = sealed_plan()
        expected = original["plan_digest"]
        mutated = copy.deepcopy(original)
        mutated["target"]["head"] = COMMIT_A  # type: ignore[index]
        mutated["plan_digest"] = contract_digest(
            {key: item for key, item in mutated.items() if key != "plan_digest"}
        )

        self.assertEqual(validate_plan(mutated), ())
        self.assertIn(
            "E_ADOPTION_PLAN_DIGEST",
            {
                issue.code
                for issue in validate_plan(mutated, expected_digest=expected)
            },
        )

    def test_closed_json_rejects_duplicates_constants_depth_and_size(self) -> None:
        bad_payloads = (
            b'{"schema_version":1,"schema_version":1}',
            b'{"value":NaN}',
            b"\xff",
            (b'{"nested":' + b"[" * 40 + b"0" + b"]" * 40 + b"}"),
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload[:32]):
                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_JSON"):
                    load_closed_json(payload, limit=4096)

        with self.assertRaisesRegex(ValueError, "^E_ADOPTION_JSON_SIZE"):
            load_closed_json(b'{"value":"0123456789"}', limit=8)

    def test_journal_nested_records_and_target_authority_are_closed(self) -> None:
        for mutation in ("target", "directory", "published", "lock", "rollback"):
            with self.subTest(mutation=mutation):
                value = sealed_journal()
                if mutation == "target":
                    value["target_binding"]["unexpected"] = True  # type: ignore[index]
                elif mutation == "directory":
                    value["created_directories"] = [
                        {"path": "scripts", "mode": 0o755, "identity": [1, 2], "extra": 1}
                    ]
                elif mutation == "published":
                    value["published_records"] = [{"path": "scripts/control-plane"}]
                elif mutation == "lock":
                    value["target_lock_record"]["size_bytes"] = "256"  # type: ignore[index]
                else:
                    value["rollback_records"][0]["before"] = "present"  # type: ignore[index]
                value["state_digest"] = contract_digest(
                    {key: item for key, item in value.items() if key != "state_digest"}
                )
                self.assertTrue(
                    {"E_ADOPTION_SCHEMA", "E_ADOPTION_JOURNAL"}
                    & {issue.code for issue in validate_journal(value)},
                )

    def test_lifecycle_and_parent_bindings_are_closed(self) -> None:
        cases = (
            "target_marker",
            "parents",
            "scan",
            "journal_lock",
            "verification_directory",
            "verification_file",
            "receipt_lock",
        )
        for case in cases:
            with self.subTest(case=case):
                if case in {"target_marker", "parents", "scan"}:
                    value = sealed_plan()
                    validator = validate_plan
                    if case == "target_marker":
                        value["target"]["adoption_lifecycle"] = "optional"  # type: ignore[index]
                    elif case == "parents":
                        value["target"]["managed_parent_directories"][0]["mode"] = 0o777  # type: ignore[index]
                    else:
                        value["target"]["managed_repository_scan"]["gitlinks_absent"] = False  # type: ignore[index]
                    value["plan_digest"] = contract_digest(
                        {key: item for key, item in value.items() if key != "plan_digest"}
                    )
                elif case in {
                    "journal_lock",
                    "verification_directory",
                    "verification_file",
                }:
                    value = sealed_journal()
                    validator = validate_journal
                    if case == "journal_lock":
                        value["lifecycle_lock"]["size"] = 1  # type: ignore[index]
                    elif case == "verification_directory":
                        value["verification_lock"]["directory"]["mode"] = 0o755  # type: ignore[index]
                    else:
                        value["verification_lock"]["file"]["inode"] = -1  # type: ignore[index]
                    value["state_digest"] = contract_digest(
                        {key: item for key, item in value.items() if key != "state_digest"}
                    )
                else:
                    value = sealed_receipt()
                    validator = validate_receipt
                    value["lifecycle_lock"]["links"] = 2  # type: ignore[index]
                    value["receipt_digest"] = contract_digest(
                        {key: item for key, item in value.items() if key != "receipt_digest"}
                    )
                self.assertTrue(validator(value))

    def test_requirement_ids_are_exact_and_canonical_json_is_stable(self) -> None:
        self.assertEqual(
            REQUIREMENT_IDS,
            tuple(f"AE-{index:02d}" for index in range(1, 10)),
        )
        first = sealed_plan()
        second = json.loads(json.dumps(first, sort_keys=False))
        self.assertEqual(contract_digest(first), contract_digest(second))


if __name__ == "__main__":
    unittest.main()
