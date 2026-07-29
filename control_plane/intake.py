"""Bounded, non-authoritative views for validated task intake."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence
import unicodedata

from control_plane.contracts import (
    SHA256_DIGEST,
    canonical_json,
    contract_digest,
    validate_task_envelope,
)


MAX_COMPACT_MANIFEST_BYTES = 4096
MAX_NOVICE_BRIEF_BYTES = 1024
MAX_INTERACTION_VIEW_BYTES = 512

_INTERACTION_CONFIG = {
    "normal": (
        (),
        "MODE_NORMAL_DIRECT",
        "Modo normal: puedo ejecutar esta tarea directamente.",
    ),
    "plan": (
        ("/plan",),
        "MODE_PLAN_FIRST",
        "Te recomiendo /plan: primero conviene cerrar decisiones y pasos.",
    ),
    "goal": (
        ("/goal",),
        "MODE_GOAL_TRACKING",
        "Te recomiendo /goal: esta tarea necesita seguimiento persistente.",
    ),
    "plan_then_goal": (
        ("/plan", "/goal"),
        "MODE_PLAN_THEN_GOAL",
        "Te recomiendo /plan y, tras aprobarlo, /goal para ejecutarlo por hitos.",
    ),
}
_ROUTE_ACTIONS = {
    "default": "Continue in the current task mode.",
    "plan": "Use /plan before implementation.",
    "goal": "Use /goal with outcome, constraints, and verification.",
    "plan_then_goal": (
        "Use /plan to refine measurable completion criteria, then /goal."
    ),
}
_ROUTE_REASON_CODES = frozenset(
    {
        "MODE_BOUNDED",
        "MODE_COMPLEX_OR_UNCERTAIN",
        "MODE_LONG_RUNNING",
        "MODE_REQUIRES_PLAN",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "task_digest",
        "decision_digest",
        "manifest_digest",
        "tier",
        "workflow_mode",
        "required",
        "recommended",
        "unresolved",
        "max_agents",
        "execution_strategy",
        "required_documents",
        "approval_boundaries",
        "required_gates",
        "project_profile",
        "interaction",
    }
)
_INTERACTION_KEYS = frozenset(
    {
        "recommended_mode",
        "reason_codes",
        "user_action",
        "automatic_change",
        "confidence",
    }
)
_PROFILE_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "profiles",
        "evidence",
        "confidence",
        "truncated",
    }
)
_PROFILES = frozenset(
    {
        "generic",
        "ios",
        "android",
        "web_pwa",
        "saas_backend",
        "ai_text_pipeline",
    }
)
_DOCUMENTS = frozenset(
    {
        "plan",
        "adr",
        "issue",
        "architecture",
        "runbook",
        "threat_model",
        "rollback",
        "release_notes",
        "release_receipt",
    }
)
_SAFE_ROUTE_TOKEN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,126}$", re.ASCII
)


@dataclass(frozen=True)
class InteractionRecommendationView:
    """Closed educational rendering of a route interaction recommendation."""

    mode: str
    commands: tuple[str, ...]
    message_code: str
    reason_codes: tuple[str, ...]
    automatic_change: bool
    human_message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "commands": list(self.commands),
            "message_code": self.message_code,
            "reason_codes": list(self.reason_codes),
            "automatic_change": self.automatic_change,
            "human_message": self.human_message,
        }


def render_interaction_recommendation(
    interaction: str, reason_codes: Sequence[str]
) -> InteractionRecommendationView:
    """Return a closed recommendation that never changes the active mode."""

    if interaction not in _INTERACTION_CONFIG:
        raise ValueError("E_INTERACTION_MODE: unsupported interaction mode")
    if (
        isinstance(reason_codes, (str, bytes))
        or not isinstance(reason_codes, Sequence)
        or not reason_codes
        or len(reason_codes) > len(_ROUTE_REASON_CODES)
        or any(
            not isinstance(code, str)
            or code not in _ROUTE_REASON_CODES
            for code in reason_codes
        )
        or len(set(reason_codes)) != len(reason_codes)
    ):
        raise ValueError(
            "E_INTERACTION_REASON: reason codes must use the closed route vocabulary"
        )
    commands, message_code, human_message = _INTERACTION_CONFIG[interaction]
    view = InteractionRecommendationView(
        mode=interaction,
        commands=commands,
        message_code=message_code,
        reason_codes=tuple(reason_codes),
        automatic_change=False,
        human_message=human_message,
    )
    if len(canonical_json(view.as_dict()).encode("utf-8")) > (
        MAX_INTERACTION_VIEW_BYTES
    ):
        raise ValueError(
            "E_INTERACTION_SIZE: interaction view exceeds 512 bytes"
        )
    return view


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value.encode()) > 512:
        return False
    parsed = PurePosixPath(value)
    return (
        not parsed.is_absolute()
        and ".." not in parsed.parts
        and "\\" not in value
        and "\x00" not in value
    )


def _route_tokens(
    value: object, *, max_items: int = 64
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > max_items
        or any(
            not isinstance(item, str)
            or _SAFE_ROUTE_TOKEN.fullmatch(item) is None
            or ".." in item
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: route token list is invalid"
        )
    return tuple(value)


def _validate_project_profile(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != _PROFILE_KEYS:
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: project profile is invalid"
        )
    profiles = value.get("profiles")
    if (
        value.get("schema_version") != 1
        or not isinstance(profiles, list)
        or not profiles
        or len(profiles) > len(_PROFILES)
        or any(item not in _PROFILES for item in profiles)
        or len(set(profiles)) != len(profiles)
        or value.get("kind")
        != (profiles[0] if len(profiles) == 1 else "hybrid")
        or value.get("confidence")
        not in {
            "high",
            "fallback",
            "marker_evidence",
            "bounded_scan_incomplete",
            "not_observed",
        }
        or not isinstance(value.get("truncated"), bool)
    ):
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: project profile values are invalid"
        )
    evidence = value.get("evidence")
    if (
        not isinstance(evidence, list)
        or len(evidence) > 32
        or any(not _safe_relative_path(item) for item in evidence)
        or len(set(evidence)) != len(evidence)
    ):
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: project profile evidence is invalid"
        )


def _parse_compact_manifest(
    payload: object, *, expected_task_digest: str
) -> dict[str, Any]:
    if not isinstance(payload, str):
        raise ValueError(
            "E_INTAKE_MANIFEST_TYPE: compact route manifest must be JSON text"
        )
    if len(payload.encode("utf-8")) > MAX_COMPACT_MANIFEST_BYTES:
        raise ValueError(
            "E_INTAKE_MANIFEST_SIZE: compact route manifest exceeds 4096 bytes"
        )
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(
            "E_INTAKE_MANIFEST_JSON: compact route manifest is invalid JSON"
        ) from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != _MANIFEST_KEYS
        or canonical_json(manifest) != payload
    ):
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: compact route manifest is not canonical"
        )
    if (
        manifest.get("schema_version") != 1
        or not isinstance(manifest.get("task_digest"), str)
        or SHA256_DIGEST.fullmatch(manifest["task_digest"]) is None
        or not isinstance(manifest.get("decision_digest"), str)
        or SHA256_DIGEST.fullmatch(manifest["decision_digest"]) is None
        or not isinstance(manifest.get("manifest_digest"), str)
        or SHA256_DIGEST.fullmatch(manifest["manifest_digest"]) is None
        or manifest.get("tier") not in {"T0", "T1", "T2", "T3"}
        or manifest.get("workflow_mode")
        != {
            "T0": "direct",
            "T1": "direct",
            "T2": "structured",
            "T3": "controlled",
        }[manifest["tier"]]
        or not isinstance(manifest.get("max_agents"), int)
        or isinstance(manifest.get("max_agents"), bool)
        or not 0 <= manifest["max_agents"] <= 2
        or manifest.get("execution_strategy") != "sequential"
    ):
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: compact route facts are invalid"
        )
    manifest_without_digest = dict(manifest)
    supplied_manifest_digest = manifest_without_digest.pop("manifest_digest")
    if contract_digest(manifest_without_digest) != supplied_manifest_digest:
        raise ValueError(
            "E_INTAKE_MANIFEST_DIGEST: compact route manifest was modified"
        )
    if manifest["task_digest"] != expected_task_digest:
        raise ValueError(
            "E_INTAKE_TASK_DIGEST: compact route manifest belongs to another task"
        )
    for field in (
        "required",
        "recommended",
        "unresolved",
        "approval_boundaries",
        "required_gates",
    ):
        _route_tokens(manifest.get(field))
    documents = _route_tokens(
        manifest.get("required_documents"),
        max_items=len(_DOCUMENTS),
    )
    if any(item not in _DOCUMENTS for item in documents):
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: required document is unknown"
        )
    _validate_project_profile(manifest.get("project_profile"))
    interaction = manifest.get("interaction")
    if (
        not isinstance(interaction, Mapping)
        or set(interaction) != _INTERACTION_KEYS
        or interaction.get("recommended_mode") not in _ROUTE_ACTIONS
        or interaction.get("automatic_change") is not False
        or interaction.get("confidence") not in {"medium", "high"}
        or interaction.get("user_action")
        != _ROUTE_ACTIONS[interaction["recommended_mode"]]
    ):
        raise ValueError(
            "E_INTAKE_MANIFEST_SCHEMA: route interaction is invalid"
        )
    view_mode = (
        "normal"
        if interaction["recommended_mode"] == "default"
        else str(interaction["recommended_mode"])
    )
    render_interaction_recommendation(
        view_mode,
        interaction.get("reason_codes"),
    )
    return manifest


def _clip_utf8(value: str, max_bytes: int) -> str:
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    suffix = "..."
    available = max_bytes - len(suffix)
    clipped = ""
    for character in value:
        if len((clipped + character).encode("utf-8")) > available:
            break
        clipped += character
    return clipped + suffix


def _inline_text(value: object, max_bytes: int) -> str:
    without_controls = "".join(
        character
        for character in str(value)
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs"}
    )
    compact = " ".join(without_controls.split()).replace("&", "&amp;")
    compact = compact.translate(
        str.maketrans(
            {
                "\\": "/",
                "`": "'",
                "[": "(",
                "]": ")",
                "<": "‹",
                ">": "›",
                "«": "'",
                "»": "'",
            }
        )
    )
    return _clip_utf8(compact, max_bytes)


def _bounded_join(values: Sequence[str], max_bytes: int) -> str:
    return _clip_utf8(", ".join(values) if values else "ninguno", max_bytes)


def _goals_dependency_first(
    goals: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Return a deterministic dependency-first order for a validated DAG."""

    goals_by_id = {str(goal["id"]): goal for goal in goals}
    remaining = {
        goal_id: len(goal["depends_on"])
        for goal_id, goal in goals_by_id.items()
    }
    dependents = {goal_id: [] for goal_id in goals_by_id}
    for goal_id, goal in goals_by_id.items():
        for dependency in goal["depends_on"]:
            dependents[str(dependency)].append(goal_id)
    ready = [goal_id for goal_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    ordered: list[Mapping[str, Any]] = []
    while ready:
        goal_id = heapq.heappop(ready)
        ordered.append(goals_by_id[goal_id])
        for dependent in sorted(dependents[goal_id]):
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(ordered) != len(goals_by_id):
        raise ValueError("T_GOAL_CYCLE: goals must form an acyclic graph")
    return tuple(ordered)


def render_novice_brief(
    task: Mapping[str, Any],
    compact_route_manifest_json: str,
) -> str:
    """Render a short Markdown explanation after a route has been decided."""

    if not isinstance(task, Mapping):
        raise ValueError(
            "T_TASK_ENVELOPE: brief requires a TaskEnvelope mapping"
        )
    task_issues = validate_task_envelope(task)
    if task_issues:
        issue = task_issues[0]
        raise ValueError(
            f"{issue.code}: {issue.path}: {issue.message}"
        )
    task_digest = contract_digest(task)
    manifest = _parse_compact_manifest(
        compact_route_manifest_json,
        expected_task_digest=task_digest,
    )
    interaction = manifest["interaction"]
    view_mode = (
        "normal"
        if interaction["recommended_mode"] == "default"
        else str(interaction["recommended_mode"])
    )
    view = render_interaction_recommendation(
        view_mode,
        interaction["reason_codes"],
    )
    goals = []
    for goal in _goals_dependency_first(task["goals"]):
        dependencies = goal["depends_on"]
        goals.append(
            str(goal["id"])
            + (
                "<-" + "+".join(str(item) for item in dependencies)
                if dependencies
                else ""
            )
        )
    checks = [
        *[str(item) for item in manifest["required_gates"]],
        *[
            f"doc:{item}"
            for item in manifest["required_documents"]
        ],
    ]
    unresolved = [str(item) for item in manifest["unresolved"]]
    next_step = (
        "aclarar " + _bounded_join(unresolved, 48)
        if unresolved
        else (
            _bounded_join(
                [str(item) for item in manifest["required_gates"]],
                56,
            )
            if manifest["required_gates"]
            else "sin pregunta pendiente"
        )
    )
    boundaries = [
        str(item) for item in manifest["approval_boundaries"]
    ]
    commands = ",".join(view.commands) if view.commands else "ninguno"
    lines = (
        "Qué he entendido: «"
        + _inline_text(task["objective"], 80)
        + "» (solo descripción; no autoriza)",
        "Cómo lo separo y en qué orden: "
        + _bounded_join(goals, 128),
        "Qué comprobaré para darlo por terminado: "
        + _bounded_join(checks, 96),
        "Modo recomendado y por qué: "
        + _clip_utf8(view.human_message, 88)
        + f" {view.message_code}; commands={commands}; "
        + "automatic_change=false; reasons="
        + _bounded_join(list(view.reason_codes), 48),
        "Siguiente gate o pregunta: " + _clip_utf8(next_step, 64),
        "Qué no haré sin autorización: "
        + (
            _bounded_join(boundaries, 80)
            if boundaries
            else "ningún efecto externo ni ampliación de autoridad"
        ),
        "Digests: task="
        + task_digest
        + " route="
        + str(manifest["decision_digest"]),
    )
    rendered = "\n\n".join(lines)
    if len(rendered.encode("utf-8")) > MAX_NOVICE_BRIEF_BYTES:
        raise ValueError(
            "E_INTAKE_BRIEF_SIZE: novice brief exceeds 1024 bytes"
        )
    return rendered
