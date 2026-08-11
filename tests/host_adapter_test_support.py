"""Test-only native host adapter.

This module is deliberately outside ``control_plane`` and is not included in
``RUNTIME_MODULES``. Production has no constructor or factory for native host
events; the real Codex host supplies them in memory.
"""

from __future__ import annotations

import control_plane.host_bridge as bridge
import copy
import itertools
from typing import Any, Mapping
from control_plane.contracts import contract_digest


_TEST_NATIVE_OBJECTS: dict[int, tuple[object, str]] = {}
_REVIEW_INVOCATIONS = itertools.count(1)


def _test_native_object_validator(value: object, kind: str) -> bool:
    registered = _TEST_NATIVE_OBJECTS.get(id(value))
    return bool(
        registered is not None
        and registered[0] is value
        and registered[1] == kind
    )


def _register_native_object(value: object, kind: str) -> None:
    _TEST_NATIVE_OBJECTS[id(value)] = (value, kind)


bridge._native_host_object_validator = _test_native_object_validator


def _test_native_remote_executor(
    operation: str,
    arguments: tuple[str, ...],
    max_output_bytes: int,
) -> tuple[int, bytes]:
    del operation
    completed = bridge.subprocess.run(
        list(arguments),
        check=False,
        stdout=bridge.subprocess.PIPE,
        stderr=bridge.subprocess.DEVNULL,
        stdin=bridge.subprocess.DEVNULL,
        env=bridge._sanitized_git_environment(),
    )
    output = completed.stdout
    if output is None:
        payload = b""
    elif isinstance(output, bytes):
        payload = output
    else:
        payload = str(output).encode("utf-8")
    if len(payload) > max_output_bytes:
        return 1, b""
    return int(completed.returncode), payload


bridge._native_host_remote_executor = _test_native_remote_executor


def rollback_plan_observation(
    *,
    run_plan: Mapping[str, object],
    run_revision: Mapping[str, object],
    attempt: int,
    trigger_conditions: tuple[tuple[str, str], ...],
    rollback_steps: tuple[tuple[int, str, str, str], ...],
    post_rollback_checks: tuple[tuple[str, str], ...],
    irreversible_boundaries: tuple[tuple[str, str], ...],
    status: str,
    session_id: str = "native-rollback-session",
    invocation_id: str = "native-rollback-invocation",
    observed_at: str = "2026-08-08T10:00:30Z",
    now: float = 100.0,
    ttl_seconds: float = 30.0,
) -> bridge.ValidatedRollbackPlanObservation:
    observation = object.__new__(bridge.RollbackPlanObservation)
    observation._consumed = False
    values = {
        "task_id": run_plan["task_id"],
        "task_digest": run_plan["task_digest"],
        "run_plan_digest": run_plan["plan_digest"],
        "run_revision_digest": run_revision["revision_digest"],
        "attempt": attempt,
        "repository": run_plan["repository"],
        "branch": run_plan["branch"],
        "head": run_revision["head"],
        "scope_paths_digest": contract_digest(
            {"scope_paths": run_plan["scope_paths"]}
        ),
        "trigger_conditions": trigger_conditions,
        "rollback_steps": rollback_steps,
        "post_rollback_checks": post_rollback_checks,
        "irreversible_boundaries": irreversible_boundaries,
        "status": status,
        "session_id": session_id,
        "invocation_id": invocation_id,
        "observed_at": observed_at,
        "observed_at_monotonic": now,
        "freshness_deadline": now + ttl_seconds,
    }
    for name, value in values.items():
        setattr(observation, name, value)
    observation.observation_digest = contract_digest({
        name: values[name]
        for name in bridge._ROLLBACK_PLAN_OBSERVATION_DIGEST_FIELDS
    })
    bridge._register_runtime_host_object(
        observation, "rollback_plan_observation"
    )
    return bridge.validate_rollback_plan_observation(
        observation,
        run_plan=run_plan,
        run_revision=run_revision,
        expected_attempt=attempt,
        expected_session_id=session_id,
        expected_invocation_id=invocation_id,
        clock=lambda: now,
    )


def independent_review_observation(
    *,
    review_packet: Mapping[str, object],
    findings_digest: str,
    critical: int,
    important: int,
    status: str,
    reviewer_identity: str,
    session_id: str,
    invocation_id: str,
    observed_at: str,
    now: float = 100.0,
    ttl_seconds: float = 30.0,
) -> bridge.ValidatedIndependentReviewObservation:
    observation = object.__new__(bridge.IndependentReviewObservation)
    observation._consumed = False
    values = {
        "task_id": review_packet["task_id"],
        "task_digest": review_packet["task_digest"],
        "review_packet_digest": review_packet["packet_digest"],
        "review_kind": review_packet["review_kind"],
        "criteria_digest": review_packet["criteria_digest"],
        "findings_digest": findings_digest,
        "critical": critical,
        "important": important,
        "status": status,
        "reviewer_identity": reviewer_identity,
        "reviewer_identity_digest": contract_digest(
            {"reviewer_identity": reviewer_identity}
        ),
        "session_id": session_id,
        "invocation_id": invocation_id,
        "observed_at": observed_at,
        "observed_at_monotonic": now,
        "freshness_deadline": now + ttl_seconds,
    }
    for name, value in values.items():
        setattr(observation, name, value)
    observation.observation_digest = contract_digest(
        {
            name: values[name]
            for name in bridge._INDEPENDENT_REVIEW_OBSERVATION_DIGEST_FIELDS
        }
    )
    bridge._register_runtime_host_object(
        observation, "independent_review_observation"
    )
    return bridge.validate_independent_review_observation(
        observation,
        review_packet=review_packet,
        expected_session_id=session_id,
        expected_invocation_id=invocation_id,
        clock=lambda: now,
    )


def independent_review_receipt(
    *,
    run_store: Any,
    review_packet: Mapping[str, object],
    findings_digest: str,
    critical: int,
    important: int,
    status: str,
    observed_at: str,
    reviewer_identity: str | None = None,
    invocation_id: str | None = None,
    native_session_id: str | None = None,
    now: float = 100.0,
    ttl_seconds: float = 30.0,
) -> tuple[dict[str, object], bridge.ValidatedIndependentReviewObservation]:
    from control_plane.run_workflow import build_independent_review_receipt

    plan = run_store.load_plan(str(review_packet["task_id"]))
    reviewer = reviewer_identity or (
        f"test-reviewer:{review_packet['review_kind']}"
    )
    invocation = invocation_id or (
        f"test-review-invocation-{next(_REVIEW_INVOCATIONS)}"
    )
    proof = independent_review_observation(
        review_packet=review_packet,
        findings_digest=findings_digest,
        critical=critical,
        important=important,
        status=status,
        reviewer_identity=reviewer,
        session_id=native_session_id or str(plan["session_id"]),
        invocation_id=invocation,
        observed_at=observed_at,
        now=now,
        ttl_seconds=ttl_seconds,
    )
    receipt = build_independent_review_receipt(
        review_packet=review_packet,
        findings_digest=findings_digest,
        critical=critical,
        important=important,
        status=status,
        observed_at=observed_at,
        observation=proof,
    )
    return receipt, proof


def native_session_event(
    *,
    event_id: str,
    session_id: str,
    invocation_id: str,
    observed_at_monotonic: float,
) -> bridge.NativeSessionEvent:
    event = object.__new__(bridge.NativeSessionEvent)
    event._consumed = False
    event.event_id = event_id
    event.session_id = session_id
    event.invocation_id = invocation_id
    event.observed_at_monotonic = observed_at_monotonic
    _register_native_object(event, "session")
    return event


def native_user_interaction_event(
    *,
    event_id: str,
    session_id: str,
    invocation_id: str,
    task_digest: str,
    subject_digest: str,
    observed_at_monotonic: float,
) -> bridge.NativeUserInteractionEvent:
    event = object.__new__(bridge.NativeUserInteractionEvent)
    event._consumed = False
    event.event_id = event_id
    event.session_id = session_id
    event.invocation_id = invocation_id
    event.task_digest = task_digest
    event.subject_digest = subject_digest
    event.observed_at_monotonic = observed_at_monotonic
    _register_native_object(event, "user_interaction")
    return event


def lifecycle_observation(
    observation_type: type[bridge.LocalGitObservation],
    *,
    observation_id: str,
    invocation_id: str,
    task_digest: str,
    repository_identity: str,
    worktree_identity: str,
    branch: str,
    prior_head: str,
    target_state: str,
    session_id: str,
    provider: str,
    subject_digest: str,
    evidence: Mapping[str, object],
    observed_at_monotonic: float,
    freshness_deadline: float,
) -> bridge.LocalGitObservation:
    if observation_type not in {
        bridge.LocalGitObservation,
        bridge.GitHubObservation,
        bridge.ReleaseProviderObservation,
    }:
        raise TypeError("closed test observation type required")
    observation = object.__new__(observation_type)
    observation.observation_id = observation_id
    observation.invocation_id = invocation_id
    observation.task_digest = task_digest
    observation.repository_identity = repository_identity
    observation.worktree_identity = worktree_identity
    observation.branch = branch
    observation.prior_head = prior_head
    observation.target_state = target_state
    observation.session_id = session_id
    observation.provider = provider
    observation.subject_digest = subject_digest
    observation.evidence = copy.deepcopy(dict(evidence))
    observation.observed_at_monotonic = observed_at_monotonic
    observation.freshness_deadline = freshness_deadline
    kind = {
        bridge.LocalGitObservation: "local_git_observation",
        bridge.GitHubObservation: "github_observation",
        bridge.ReleaseProviderObservation: "release_provider_observation",
    }[observation_type]
    bridge._register_runtime_host_object(observation, kind)
    return observation


def governing_runtime_observation(
    *,
    runtime_digest: str,
    lock_digest: str,
    policy_digest: str,
    attestor_worktree: str,
    target_worktree: str,
    governing_base_commit: str,
    runtime_layout: str,
    session_id: str,
    invocation_id: str,
    freshness_deadline: float,
) -> bridge.GoverningRuntimeObservation:
    observation = object.__new__(bridge.GoverningRuntimeObservation)
    observation._consumed = False
    values = {
        "runtime_digest": runtime_digest,
        "lock_digest": lock_digest,
        "policy_digest": policy_digest,
        "attestor_worktree": attestor_worktree,
        "target_worktree": target_worktree,
        "governing_base_commit": governing_base_commit,
        "runtime_layout": runtime_layout,
        "session_id": session_id,
        "invocation_id": invocation_id,
        "freshness_deadline": freshness_deadline,
    }
    for name, value in values.items():
        setattr(observation, name, value)
    observation.observation_digest = contract_digest(
        {
            key: value
            for key, value in values.items()
            if key != "freshness_deadline"
        }
    )
    bridge._register_governing_runtime_observation(observation)
    return observation


def native_github_provider_event(
    *,
    event_id: str,
    repository: str,
    session_id: str,
    invocation_id: str,
) -> bridge.NativeGitHubProviderEvent:
    event = object.__new__(bridge.NativeGitHubProviderEvent)
    event._consumed = False
    event.event_id = event_id
    event.repository = repository
    event.session_id = session_id
    event.invocation_id = invocation_id
    _register_native_object(event, "github_provider")
    return event


def governing_policy(
    *,
    policy: Mapping[str, object],
    policy_digest: str,
    runtime_digest: str,
    lock_digest: str,
    governing_base_commit: str,
    remote_repository: str = "example/control-plane",
    session_id: str,
    invocation_id: str,
    freshness_deadline: float,
):
    from control_plane.policy import GoverningPolicy

    result = object.__new__(GoverningPolicy)
    result._consumed = False
    result.policy = copy.deepcopy(dict(policy))
    result.policy_digest = policy_digest
    result.runtime_digest = runtime_digest
    result.lock_digest = lock_digest
    result.governing_base_commit = governing_base_commit
    result.remote_repository = remote_repository
    result.session_id = session_id
    result.invocation_id = invocation_id
    result.freshness_deadline = freshness_deadline
    result.binding_digest = contract_digest(
        {
            "policy_digest": policy_digest,
            "runtime_digest": runtime_digest,
            "lock_digest": lock_digest,
            "governing_base_commit": governing_base_commit,
            "remote_repository": remote_repository,
            "session_id": session_id,
            "invocation_id": invocation_id,
        }
    )
    from control_plane.policy import _register_governing_policy

    _register_governing_policy(result)
    return result
