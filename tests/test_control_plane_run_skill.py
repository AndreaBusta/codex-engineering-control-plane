from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "control-plane-run" / "SKILL.md"
OPENAI_YAML = ROOT / "skills" / "control-plane-run" / "agents" / "openai.yaml"


def _internal_authority_prompt_findings(text: str) -> list[str]:
    """Find user-facing setup/plumbing requests, including common synonyms."""

    normalized = " ".join(text.lower().split())
    patterns = {
        "enable_bridge": (
            r"\b(?:habilita|activa|configura|instala)\b.{0,40}"
            r"\b(?:bridge|adaptador|adapter)\b"
        ),
        "mint_or_reissue_grant": (
            r"\b(?:mint|acuña|genera|emite|reemite|re-emite|vuelve a emitir)\b"
            r".{0,40}\b(?:grant|autorización|authorization)\b"
        ),
        "repeat_bindings": (
            r"\b(?:repite|proporciona|facilita|pega|copia|introduce)\b"
            r".{0,60}\b(?:nonce|sesión|session|head|scope|alcance|mensaje exacto)\b"
        ),
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, normalized)]


class ControlPlaneRunSkillTests(unittest.TestCase):
    def test_skill_exposes_the_bounded_local_run_protocol(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("---\nname: control-plane-run\n"))
        for required in (
            "run prepare",
            "run verify",
            "run status",
            "run block",
            "--task-id <id> --reason <código>",
            "PLANIFICANDO",
            "TRABAJANDO",
            "VERIFICANDO",
            "PR LISTA",
            "BLOCKED",
            "tres ejecuciones totales",
            "UNKNOWN",
            "autorización nativa",
            "gate.rollback-plan",
            "RollbackPlanV1",
            "Sin CLI público",
            "scalar",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("--decision", text)
        self.assertNotIn("force-push", text.lower())

    def test_skill_has_ui_metadata_without_external_dependencies(self) -> None:
        metadata = OPENAI_YAML.read_text(encoding="utf-8")

        self.assertIn('display_name: "Control Plane Run"', metadata)
        self.assertIn("$control-plane-run", metadata)
        self.assertNotIn("dependencies:", metadata)

    def test_skill_keeps_native_authority_internal_and_reauthorizes_only_for_drift(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        for required in (
            "evidencia no es autoridad",
            "host-bound",
            "local_write",
            "remote_write",
            "pull_request",
            "integration",
            "una sola reautorización",
            "efecto nuevo",
            "deriva",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        for forbidden in (
            "OutcomeAuthorizationContext",
            "NativeUserInteractionEvent",
            "HostAdapterCapability",
            "TrustedAuthorization",
            "AuthorizationGrant",
            "mensaje exacto",
            "mint",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        self.assertEqual(_internal_authority_prompt_findings(text), [])

    def test_skill_closes_remote_retry_and_squash_mandate_semantics(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        normalized = " ".join(text.lower().split())

        self.assertLess(len(text.encode("utf-8")), 4096)

        for required in (
            "pr lista es el resultado predeterminado",
            "observar antes de reintentar",
            "cero segunda escritura",
            "cero reparación remota",
            "unknown termina en blocked",
            "«hasta squash merge»",
            "petición nativa actual, fresca y exacta",
            "4 kib",
            "reutilizar el receipt exacto",
            "tres ejecuciones totales",
            "ruta feliz",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)

        for forbidden_command in (
            "run commit",
            "run push",
            "run pull-request",
            "run merge",
            "run retry",
            "run authorize",
        ):
            with self.subTest(forbidden_command=forbidden_command):
                self.assertNotIn(forbidden_command, text)

    def test_skill_does_not_reduce_kernel_to_observation_validation(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        normalized = " ".join(text.lower().split())

        self.assertNotRegex(
            normalized,
            r"kernel (?:solo|únicamente) valida observaciones",
        )
        for required in (
            "observaciones y receipts son no autorizantes",
            "`git ls-remote` read-only",
            "mutaciones push/pr/squash merge son host-native",
            "python no recibe autoridad",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)

    def test_internal_authority_prompt_detector_catches_literal_and_synonyms(self) -> None:
        scenarios = {
            "literal": "Habilita el bridge y mint un grant para continuar.",
            "spanish_synonyms": (
                "Activa el adaptador y vuelve a emitir la autorización."
            ),
            "binding_prompt": (
                "Proporciona otra vez el nonce, la sesión, HEAD y scope."
            ),
        }
        for scenario, sample in scenarios.items():
            with self.subTest(scenario=scenario):
                self.assertTrue(_internal_authority_prompt_findings(sample))


if __name__ == "__main__":
    unittest.main()
