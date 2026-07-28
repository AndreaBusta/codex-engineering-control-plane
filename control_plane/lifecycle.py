"""Task lifecycle, worktree-scoped leases, and compact resource receipts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from control_plane.contracts import (
    RESOURCE_ID,
    SHA256_DIGEST,
    TASK_EFFECTS,
    contract_digest,
    validate_task_id,
)


ORDERED_STATES = (
    "framed",
    "planned",
    "ready",
    "implementing",
    "verifying",
    "review_ready",
    "committed",
    "pushed",
    "pr_draft",
    "pr_ready",
    "merged",
    "base_verified",
    "release_pending",
    "released",
    "observed",
    "closed",
    "blocked",
)
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    state: frozenset(
        {
            *(("blocked",) if state not in {"closed", "blocked"} else ()),
            *(
                (ORDERED_STATES[index + 1],)
                if state not in {"closed", "blocked"}
                and index + 1 < len(ORDERED_STATES) - 1
                else ()
            ),
        }
    )
    for index, state in enumerate(ORDERED_STATES)
}
LEGAL_TRANSITIONS["closed"] = frozenset()
LEGAL_TRANSITIONS["blocked"] = frozenset()
OUTCOME_LIMITS = {
    "answer": "planned",
    "local_change": "review_ready",
    "commit": "committed",
    "pull_request": "pr_ready",
    "integration": "base_verified",
    "release": "observed",
}
TRANSITION_EVIDENCE = {
    "ready": frozenset({"preflight_ok"}),
    "verifying": frozenset({"implementation_complete"}),
    "review_ready": frozenset({"gates_ok", "documentation_decision"}),
    "committed": frozenset({"commit"}),
    "pushed": frozenset({"remote_head"}),
    "pr_draft": frozenset({"pull_request"}),
    "pr_ready": frozenset({"checks_ok"}),
    "merged": frozenset({"merge_commit"}),
    "base_verified": frozenset({"remote_base"}),
    "release_pending": frozenset({"release_manifest"}),
    "released": frozenset({"provider_build"}),
    "observed": frozenset({"observation"}),
}
GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{7,64}$", re.ASCII)
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)


def _valid_branch(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and BRANCH_NAME.fullmatch(value)
        and ".." not in value
        and "//" not in value
        and not value.endswith(("/", ".", ".lock"))
        and "@{" not in value
    )


def _normalize_lease_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return None
    normalized = value.rstrip("/")
    if normalized.endswith("/**"):
        normalized = normalized[:-3]
    if normalized == ".":
        return "."
    if any(character in normalized for character in "*?[]"):
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path.as_posix()


def _path_owned(changed_path: str, owned_paths: list[str]) -> bool:
    changed = PurePosixPath(changed_path).as_posix()
    return any(
        owned == "."
        or changed == owned
        or changed.startswith(owned + "/")
        for owned in owned_paths
    )


def _validate_transition_evidence(
    target: str, evidence: Mapping[str, Any] | None
) -> None:
    supplied = evidence or {}
    required = TRANSITION_EVIDENCE.get(target, frozenset())
    if set(supplied) != required:
        missing = sorted(required.difference(supplied))
        unexpected = sorted(set(supplied).difference(required))
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ValueError("E_STATE_EVIDENCE: " + "; ".join(details))
    if not required:
        return
    if target == "ready" and supplied.get("preflight_ok") is not True:
        raise ValueError("E_STATE_EVIDENCE: preflight must pass")
    if (
        target == "verifying"
        and supplied.get("implementation_complete") is not True
    ):
        raise ValueError("E_STATE_EVIDENCE: implementation is not complete")
    if target == "review_ready":
        if supplied.get("gates_ok") is not True:
            raise ValueError("E_STATE_EVIDENCE: gates must pass")
        documentation = supplied.get("documentation_decision")
        if (
            not isinstance(documentation, str)
            or SHA256_DIGEST.fullmatch(documentation) is None
        ):
            raise ValueError(
                "E_STATE_EVIDENCE: documentation decision digest is required"
            )
    if target in {"committed", "pushed", "merged", "base_verified"}:
        field = next(iter(required))
        value = supplied.get(field)
        if not isinstance(value, str) or GIT_OBJECT_ID.fullmatch(value) is None:
            raise ValueError(f"E_STATE_EVIDENCE: invalid {field}")
    if target == "pr_draft":
        pull_request = supplied.get("pull_request")
        if (
            not isinstance(pull_request, Mapping)
            or set(pull_request) != {"number", "url", "head_commit"}
            or not isinstance(pull_request.get("number"), int)
            or isinstance(pull_request.get("number"), bool)
            or int(pull_request.get("number", 0)) <= 0
            or not isinstance(pull_request.get("url"), str)
            or not str(pull_request.get("url")).startswith("https://")
            or not isinstance(pull_request.get("head_commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(pull_request.get("head_commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: invalid pull request evidence")
    if target == "pr_ready":
        checks = supplied.get("checks_ok")
        if (
            not isinstance(checks, Mapping)
            or set(checks) != {"ok", "head_commit"}
            or checks.get("ok") is not True
            or not isinstance(checks.get("head_commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(checks.get("head_commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: required checks must pass")
    if target == "release_pending":
        manifest = supplied.get("release_manifest")
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != {"digest", "commit"}
            or not isinstance(manifest.get("digest"), str)
            or SHA256_DIGEST.fullmatch(str(manifest.get("digest"))) is None
            or not isinstance(manifest.get("commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(manifest.get("commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: release manifest digest is required")
    if target == "released":
        build = supplied.get("provider_build")
        if (
            not isinstance(build, Mapping)
            or set(build) != {"provider", "build_id", "commit"}
            or not all(
                isinstance(build.get(field), str) and bool(build.get(field))
                for field in ("provider", "build_id")
            )
            or not isinstance(build.get("commit"), str)
            or GIT_OBJECT_ID.fullmatch(str(build.get("commit"))) is None
        ):
            raise ValueError("E_STATE_EVIDENCE: provider build proof is invalid")
    if target == "observed":
        observation = supplied.get("observation")
        if (
            not isinstance(observation, Mapping)
            or set(observation) != {"status", "reference"}
            or observation.get("status") not in {"healthy", "degraded"}
            or not isinstance(observation.get("reference"), str)
            or not observation.get("reference")
        ):
            raise ValueError("E_STATE_EVIDENCE: observation proof is invalid")


def transition_allowed(source: str, target: str) -> bool:
    return target in LEGAL_TRANSITIONS.get(source, frozenset())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


@contextmanager
def _lease_guard(state_dir: Path):
    """Serialize the lease inventory scan and mutation on macOS/Linux."""

    leases_dir = state_dir / "codex-control-plane" / "leases"
    leases_dir.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(leases_dir / ".lease.lock", flags, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield leases_dir
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class TaskStore:
    """Persist compact task state beneath the worktree-specific Git dir."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.root = state_dir / "codex-control-plane" / "tasks"

    def _path(self, task_id: str) -> Path:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        return self.root / f"{task_id}.json"

    def _read(self, task_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._path(task_id).read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError("E_TASK_NOT_FOUND: task state does not exist") from error

    def start(
        self,
        task_id: str,
        *,
        outcome: str,
        branch: str,
        task_digest: str,
        decision_digest: str,
    ) -> dict[str, Any]:
        if outcome not in OUTCOME_LIMITS:
            raise ValueError("E_STATE_OUTCOME: unsupported requested outcome")
        if not _valid_branch(branch):
            raise ValueError("E_STATE_BRANCH: invalid branch")
        if (
            not isinstance(task_digest, str)
            or SHA256_DIGEST.fullmatch(task_digest) is None
            or not isinstance(decision_digest, str)
            or SHA256_DIGEST.fullmatch(decision_digest) is None
        ):
            raise ValueError("E_STATE_DIGEST: task and decision digests are required")
        path = self._path(task_id)
        if path.exists():
            existing = self._read(task_id)
            if (
                existing["outcome"] == outcome
                and existing["branch"] == branch
                and existing.get("task_digest") == task_digest
                and existing.get("decision_digest") == decision_digest
            ):
                return existing
            raise ValueError("E_TASK_EXISTS: task ID already has different state")
        state = {
            "schema_version": 1,
            "task_id": task_id,
            "state": "framed",
            "resume_state": None,
            "outcome": outcome,
            "branch": branch,
            "task_digest": task_digest,
            "decision_digest": decision_digest,
            "block_reason": None,
            "evidence": {},
            "updated_at": _utc_now(),
        }
        _atomic_json(path, state)
        return state

    def status(self, task_id: str) -> dict[str, Any]:
        return self._read(task_id)

    def transition(
        self,
        task_id: str,
        target: str,
        *,
        reason: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        current_branch: str,
    ) -> dict[str, Any]:
        state = self._read(task_id)
        if current_branch != state["branch"]:
            raise ValueError("E_STATE_BRANCH: current branch differs from task branch")
        source = str(state["state"])
        limit = OUTCOME_LIMITS[str(state["outcome"])]
        if target not in {"blocked", "closed"}:
            if ORDERED_STATES.index(target) > ORDERED_STATES.index(limit):
                raise ValueError("E_STATE_OUTCOME: target exceeds requested outcome")
        if not transition_allowed(source, target):
            raise ValueError(f"E_STATE_TRANSITION: {source} -> {target} is illegal")
        _validate_transition_evidence(target, evidence)
        supplied = evidence or {}
        prior = state.get("evidence", {})
        if target == "pushed" and supplied.get("remote_head") != prior.get(
            "committed", {}
        ).get("commit"):
            raise ValueError(
                "E_STATE_EVIDENCE: remote head must equal committed head"
            )
        if target == "pr_draft" and supplied.get("pull_request", {}).get(
            "head_commit"
        ) != prior.get("pushed", {}).get("remote_head"):
            raise ValueError(
                "E_STATE_EVIDENCE: pull request must target the pushed head"
            )
        if target == "pr_ready" and supplied.get("checks_ok", {}).get(
            "head_commit"
        ) != prior.get("pushed", {}).get("remote_head"):
            raise ValueError(
                "E_STATE_EVIDENCE: checks must correspond to pushed head"
            )
        if target == "base_verified" and supplied.get(
            "remote_base"
        ) != prior.get("merged", {}).get("merge_commit"):
            raise ValueError(
                "E_STATE_EVIDENCE: remote base must contain the merge commit"
            )
        if target == "release_pending" and supplied.get(
            "release_manifest", {}
        ).get("commit") != prior.get("base_verified", {}).get("remote_base"):
            raise ValueError(
                "E_STATE_EVIDENCE: release manifest must bind verified base"
            )
        if target == "released" and supplied.get("provider_build", {}).get(
            "commit"
        ) != prior.get("release_pending", {}).get(
            "release_manifest", {}
        ).get("commit"):
            raise ValueError(
                "E_STATE_EVIDENCE: provider build must use manifest commit"
            )
        if target == "blocked":
            state["resume_state"] = source
            state["block_reason"] = reason or "unspecified"
        state["state"] = target
        if evidence:
            state["evidence"][target] = dict(evidence)
        state["updated_at"] = _utc_now()
        _atomic_json(self._path(task_id), state)
        return state

    def resume(self, task_id: str, *, current_branch: str) -> dict[str, Any]:
        state = self._read(task_id)
        if current_branch != state["branch"]:
            raise ValueError("E_STATE_BRANCH: current branch differs from task branch")
        if state["state"] != "blocked" or not state.get("resume_state"):
            raise ValueError("E_STATE_RESUME: task is not resumable")
        state["state"] = state["resume_state"]
        state["resume_state"] = None
        state["block_reason"] = None
        state["updated_at"] = _utc_now()
        _atomic_json(self._path(task_id), state)
        return state

    def close(self, task_id: str, *, current_branch: str) -> dict[str, Any]:
        state = self._read(task_id)
        if current_branch != state["branch"]:
            raise ValueError("E_STATE_BRANCH: current branch differs from task branch")
        terminal = OUTCOME_LIMITS[str(state["outcome"])]
        if state["state"] != terminal:
            raise ValueError(
                f"E_STATE_CLOSE: expected {terminal}, observed {state['state']}"
            )
        state["state"] = "closed"
        state["updated_at"] = _utc_now()
        _atomic_json(self._path(task_id), state)
        with _lease_guard(self.state_dir) as leases_dir:
            (leases_dir / f"{task_id}.json").unlink(missing_ok=True)
        return state


class TaskLease:
    """Bind a dirty-work continuation to one exact task identity."""

    @staticmethod
    def acquire(
        state_dir: Path,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        paths: list[str],
        policy_digest: str,
    ) -> dict[str, Any]:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        normalized_paths = [_normalize_lease_path(path) for path in paths]
        if not paths or any(path is None for path in normalized_paths):
            raise ValueError("E_LEASE_PATH: lease paths must be safe repository paths")
        if not _valid_branch(branch) or not validate_task_id(session_id):
            raise ValueError("E_LEASE_IDENTITY: invalid branch or session")
        if (
            not isinstance(policy_digest, str)
            or SHA256_DIGEST.fullmatch(policy_digest) is None
        ):
            raise ValueError("E_LEASE_DIGEST: invalid policy digest")
        payload = {
            "schema_version": 1,
            "task_id": task_id,
            "worktree": str(Path(worktree).resolve()),
            "branch": branch,
            "session_id": session_id,
            "paths": sorted(set(str(path) for path in normalized_paths)),
            "policy_digest": policy_digest,
        }
        payload["lease_digest"] = contract_digest(payload)
        with _lease_guard(state_dir) as leases_dir:
            path = leases_dir / f"{task_id}.json"
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != payload:
                    raise ValueError(
                        "E_LEASE_MISMATCH: lease belongs to another task identity"
                    )
                return existing
            requested_paths = list(payload["paths"])
            for other_path in leases_dir.glob("*.json"):
                try:
                    other = json.loads(other_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raise ValueError("E_LEASE_INVALID: existing lease is unreadable")
                if other.get("task_id") == task_id:
                    continue
                other_paths = [
                    str(item) for item in other.get("paths", [])
                ]
                overlap = any(
                    left == right
                    or left.startswith(right + "/")
                    or right.startswith(left + "/")
                    for left in requested_paths
                    for right in other_paths
                )
                if overlap:
                    raise ValueError(
                        "E_LEASE_CONFLICT: another task owns an overlapping path"
                    )
            _atomic_json(path, payload)
        return payload

    @staticmethod
    def validate(
        state_dir: Path,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        policy_digest: str,
        changed_paths: list[str],
    ) -> dict[str, Any]:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        if not isinstance(changed_paths, list):
            raise ValueError(
                "E_LEASE_SCOPE: changed paths are required for continuation"
            )
        with _lease_guard(state_dir) as leases_dir:
            path = leases_dir / f"{task_id}.json"
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    "E_LEASE_NOT_FOUND: continuation lease is unavailable"
                ) from error
        expected = {
            "task_id": task_id,
            "worktree": str(Path(worktree).resolve()),
            "branch": branch,
            "session_id": session_id,
            "policy_digest": policy_digest,
        }
        if any(existing.get(key) != value for key, value in expected.items()):
            raise ValueError("E_LEASE_MISMATCH: continuation identity changed")
        lease_digest = existing.get("lease_digest")
        semantic = {
            key: value for key, value in existing.items() if key != "lease_digest"
        }
        if (
            not isinstance(lease_digest, str)
            or SHA256_DIGEST.fullmatch(lease_digest) is None
            or lease_digest != contract_digest(semantic)
        ):
            raise ValueError("E_LEASE_DIGEST: lease content was modified")
        owned_paths = existing.get("paths")
        if not isinstance(owned_paths, list) or not all(
            _normalize_lease_path(path) == path for path in owned_paths
        ):
            raise ValueError("E_LEASE_PATH: lease contains invalid ownership paths")
        unsafe_changed = [
            path
            for path in changed_paths
            if _normalize_lease_path(path) is None
            or not _path_owned(path, owned_paths)
        ]
        if unsafe_changed:
            raise ValueError(
                "E_LEASE_SCOPE: changed files exceed lease ownership: "
                + ", ".join(sorted(unsafe_changed))
            )
        return existing


def create_resource_receipt(
    *,
    task_id: str,
    decision_digest: str,
    digests: Mapping[str, str],
    used: list[str],
    resource_digests: Mapping[str, str],
    omitted: list[str],
    gates: list[Mapping[str, Any]],
    effects: list[str],
) -> dict[str, Any]:
    """Create compact evidence without retaining source text or tool output."""

    if not validate_task_id(task_id):
        raise ValueError("E_TASK_ID: unsafe task ID")
    if (
        not isinstance(decision_digest, str)
        or SHA256_DIGEST.fullmatch(decision_digest) is None
    ):
        raise ValueError("E_RECEIPT_DIGEST: invalid decision digest")
    if set(digests) != {"task", "policy", "registry", "inventory"} or any(
        not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
        for value in digests.values()
    ):
        raise ValueError("E_RECEIPT_DIGEST: four contract digests are required")
    if not all(
        isinstance(item, str) and RESOURCE_ID.fullmatch(item) is not None
        for item in [*used, *omitted]
    ):
        raise ValueError("E_RECEIPT_RESOURCE: invalid resource ID")
    if (
        any(resource_id not in resource_digests for resource_id in used)
        or any(
            not isinstance(digest, str)
            or SHA256_DIGEST.fullmatch(digest) is None
            for digest in resource_digests.values()
        )
    ):
        raise ValueError(
            "E_RECEIPT_RESOURCE: used resources require locator digests"
        )
    normalized_gates: list[dict[str, Any]] = []
    for gate in gates:
        if (
            set(gate) != {"gate_id", "ok", "report_digest"}
            or not isinstance(gate.get("gate_id"), str)
            or RESOURCE_ID.fullmatch(str(gate.get("gate_id"))) is None
            or not isinstance(gate.get("ok"), bool)
            or not isinstance(gate.get("report_digest"), str)
            or SHA256_DIGEST.fullmatch(str(gate.get("report_digest"))) is None
        ):
            raise ValueError("E_RECEIPT_GATE: invalid gate evidence")
        normalized = {
            "gate_id": str(gate["gate_id"]),
            "ok": gate["ok"],
            "report_digest": str(gate["report_digest"]),
            "subject_digest": decision_digest,
        }
        normalized["evidence_digest"] = contract_digest(normalized)
        normalized_gates.append(normalized)
    if not all(effect in TASK_EFFECTS for effect in effects):
        raise ValueError("E_RECEIPT_EFFECT: invalid observed effect")
    receipt = {
        "schema_version": 1,
        "task_id": task_id,
        "decision_digest": decision_digest,
        "task_digest": digests["task"],
        "policy_digest": digests["policy"],
        "registry_digest": digests["registry"],
        "inventory_digest": digests["inventory"],
        "used": [
            {
                "resource_id": resource_id,
                "locator_digest": str(resource_digests[resource_id]),
                "evidence_digest": contract_digest(
                    {
                        "decision_digest": decision_digest,
                        "resource_id": resource_id,
                        "locator_digest": str(resource_digests[resource_id]),
                    }
                ),
            }
            for resource_id in sorted(set(used))
        ],
        "omitted": sorted(set(omitted)),
        "gate_results": sorted(
            normalized_gates, key=lambda item: str(item["gate_id"])
        ),
        "observed_effects": sorted(set(effects)),
    }
    receipt["receipt_digest"] = contract_digest(receipt)
    return receipt
