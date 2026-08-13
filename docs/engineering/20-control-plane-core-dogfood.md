# Control Plane Core manual dogfood

Status: `PASS_10_TASK_DOGFOOD_PENDING_FINAL_GATE`. `Autopilot=OFF`.

This is a manual evidence gate, not an execution log or an authorization store.
No prompts, transcripts, or telemetry are persisted. A task counts only after
its observable result and bounded evidence have been reviewed. Completed rows
reference a canonical, non-authorizing evidence payload below; the candidate
remains pending until all ten rows satisfy the exit gate together.

## Entry gate

- Use the exact `3.1.0-core.1` source candidate and record its runtime digest.
- Keep one root coordinator, at most two workers, and no overlapping writers.
- Permit only `answer` or `local_change`; defer every external effect.
- Run focal verification while iterating and at most one authoritative full
  suite for the final subject of each task.
- Mark uncertainty `UNKNOWN`; never translate missing evidence into success.

## Scorecard

| ID | Workload | FACTS_ONLY | Outcome | Allowed effects | Writers | Status | Evidence |
|---|---|---|---|---|---:|---|---|
| `CORE-DOGFOOD-01` | `local` | `true` | `answer` | `local_read` | `0` | `PASS` | `CORE-DOGFOOD-01-E1` |
| `CORE-DOGFOOD-02` | `hybrid` | `true` | `answer` | `local_read` | `0` | `PASS` | `CORE-DOGFOOD-02-E1` |
| `CORE-DOGFOOD-03` | `controlled` | `true` | `answer` | `local_read` | `0` | `PASS` | `CORE-DOGFOOD-03-E1` |
| `CORE-DOGFOOD-04` | `local` | `false` | `local_change` | `local_read+local_write` | `1` | `PASS` | `CORE-DOGFOOD-04-E1` |
| `CORE-DOGFOOD-05` | `local` | `false` | `local_change` | `local_read+local_write` | `1` | `PASS` | `CORE-DOGFOOD-05-E1` |
| `CORE-DOGFOOD-06` | `hybrid` | `false` | `local_change` | `local_read+local_write` | `1` | `PASS` | `CORE-DOGFOOD-06-E1` |
| `CORE-DOGFOOD-07` | `hybrid` | `false` | `local_change` | `local_read+local_write` | `1` | `PASS` | `CORE-DOGFOOD-07-E1` |
| `CORE-DOGFOOD-08` | `controlled` | `false` | `local_change` | `local_read+local_write` | `1` | `PASS` | `CORE-DOGFOOD-08-E1` |
| `CORE-DOGFOOD-09` | `controlled` | `false` | `local_change` | `local_read+local_write` | `1` | `PASS` | `CORE-DOGFOOD-09-E1` |
| `CORE-DOGFOOD-10` | `controlled` | `false` | `local_change` | `local_read+local_write` | `1` | `PASS` | `CORE-DOGFOOD-10-E1` |

For `FACTS_ONLY=true`, the outcome must remain `answer`, effects must be exactly
`local_read`, and no writer or durable task state may exist. `hybrid` means more
than one detected project profile. `controlled` covers security, auth, private
data, migration, production, or comparable risk while retaining local effects.

## Exit gate

Autopilot remains OFF. The ten-task dogfood gate is satisfied only when all of
the following are evidenced together; satisfying it does not enable Autopilot,
installation, external adoption, or any remote effect:

```text
tasks_completed=10
facts_only_total=3
workloads_include=local,hybrid,controlled
duplicated_effects=0
fabricated_effects=0
overlapping_writers=0
nuisance_warnings=0
duplicated_full_suites=0
authoritative_full_gate=PENDING
```

The ten rows satisfy these dogfood invariants. The authoritative full gate is a
separate, single execution over the sealed final bytes. Failure leaves the
candidate blocked; success still does not enable Autopilot or stable adoption.
Every evidence reference excludes prompts, transcripts, secrets, and authority.

## Evidence registry

### `CORE-DOGFOOD-01-E1`

```json
{"schema_version":1,"kind":"CoreDogfoodEvidenceV1","task_id":"CORE-DOGFOOD-01","result":"PASS","review":"APPROVED","candidate_version":"3.1.0-core.1","runtime_digest":"sha256:6fac3fd157509985b17b311df9ce40f89bb8ef55828868da0a1270541da6d7f3","repository_local":{"branch":"codex/control-plane-core-3-1","head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","clean":true,"lock_valid":true,"autopilot":"OFF"},"contract":{"workload":"local","facts_only":true,"requested_outcome":"answer","effects":["local_read"],"writers":0,"durable_task_state":false},"routing":{"mode":"audit","tier":"T0","workflow_mode":"direct","project_profiles":["generic"],"matched_routes":[],"required":["gate.diff-review","gate.targeted-validation","instruction.project-agents"],"required_gates":["gate.diff-review","gate.targeted-validation"],"recommended":[],"deferred":[],"unresolved":[],"decision_ready":true,"errors":[],"authorizes":false},"task_digest":"sha256:eac16ef19ed4ec17e550660583cfe877427c14db8dec4173132819a70db4bab1","decision_digest":"sha256:dbabb410d9f43a5d8ccaf418e5536ffac70c2e0bf54188674071bd5a38f6b53c","observed_effects":["local_read"],"external_effects":[],"writers_observed":0,"tests_run":[],"network_used":false,"authorizes":false}
```

Evidence digest: `sha256:f4c03568ee778872ed35cd5b1ba9397875c68e89e5e830278e755a2da21c0ab7`

### `CORE-DOGFOOD-02-E1`

```json
{"authorizes":false,"candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read"],"facts_only":true,"requested_outcome":"answer","workload":"hybrid","writers":0},"decision_digest":"sha256:8c54f5999657d5055a5a140932832a7e2ef66e4be5dc98c1d3b3d32e5719394f","external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read"],"repository_local":{"autopilot":"OFF","branch":"codex/control-plane-core-3-1","clean":false,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","lock_valid":true},"result":"PASS","review":"APPROVED","routing":{"authorizes":false,"decision_ready":true,"deferred":[],"errors":[],"matched_routes":[],"mode":"audit","profile_confidence":"marker_evidence","profile_kind":"hybrid","profile_truncated":false,"project_profiles":["android","ios","saas_backend","web_pwa"],"recommended":[],"required":["gate.diff-review","gate.targeted-validation","instruction.project-agents"],"required_gates":["gate.diff-review","gate.targeted-validation"],"tier":"T0","unresolved":[],"workflow_mode":"direct"},"runtime_digest":"sha256:6fac3fd157509985b17b311df9ce40f89bb8ef55828868da0a1270541da6d7f3","schema_version":1,"state_metadata":{"task_state_present":false,"unchanged":true},"target_local":{"branch":"codex/coach-athlete-portal-strategy","clean":false,"head":"61fd5a9175df8f439f5a737f68b7441a42d8a318"},"task_digest":"sha256:da9a665aea2c4d3246bbe6e71ba95d8c28a0ede97b2c2b2cce8e0b40ea7ae29a","task_id":"CORE-DOGFOOD-02","tests_run":[],"writers_observed":0}
```

Evidence digest: `sha256:f5b8e541742b434f65610092828d00252341dc8df3d04570dacc022e60553849`

### `CORE-DOGFOOD-03-E1`

```json
{"authorizes":false,"contract":{"allowed_effects":["local_read"],"durable":false,"facts_only":true,"requested_outcome":"answer","workflow":"controlled","writers":0},"kind":"CoreDogfoodEvidenceV1","observed_effects":{"external_effects":[],"network":false,"tests_run":[]},"result":"PASS","review":"APPROVED","route":{"decision_digest":"sha256:9591b045d87b3d31c721681c609b598072d153b5417dc2d7cfc5ed12190d4e24","decision_ready":true,"inventory_digest":"sha256:8b90eb7f05c61d1cd6f2fb3956578e726439ba59824db3d03325dd2fe9339a74","matched_routes":["critical-assurance","quality-profile-generic","structured-engineering"],"registry_digest":"sha256:10bbf154e3a09ddebd0c497a5fdf9a1943bbd65f2e4c3c562ef88413573f2862","required_gates":["gate.independent-review","gate.rollback-plan","gate.security-review","gate.written-plan"],"task_digest":"sha256:f8308f6962889a54235f1ea9b30b7613d679549a09a7b46e3406dd43e90bb80a","tier":"T3","workflow_mode":"controlled"},"schema_version":1,"state_metadata":{"scan_complete":true,"task_state_present":false,"unchanged":true},"subject":{"active_python_loc":13876,"active_python_loc_limit":21530,"advanced_imports_present":false,"advanced_sources_present":false,"autopilot_off":true,"external_consumer_adoption_prohibited":true,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","hook_isolated":true,"launcher_isolated":true,"legacy_writer_exclusion":"COOPERATIVE_ONLY","product_version":"3.1.0-core.1","runtime_digest":"sha256:6fac3fd157509985b17b311df9ce40f89bb8ef55828868da0a1270541da6d7f3","runtime_module_count":25,"test_runner_isolated":true},"task_id":"CORE-DOGFOOD-03"}
```

Evidence digest: `sha256:b3ef611e3fbbea8bd54ba15688425aa94822cc517477533094ab5bf0306c23f8`

### `CORE-DOGFOOD-04-E1`

```json
{"authorizes":false,"candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read","local_write"],"facts_only":false,"requested_outcome":"local_change","workload":"local","writers":1},"external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read","local_write"],"repository_local":{"autopilot":"OFF","branch":"codex/control-plane-core-3-1","clean":false,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","lock_valid":true},"result":"PASS","review":"APPROVED","routing":{"authorizes":false,"decision_digest":"sha256:a4f107484c2dd6e5879c27c82a9b155b963c918624e85e23becd86ee3aa664ec","decision_ready":true,"deferred":["skill.decision-stress-test"],"errors":[],"inventory_digest":"sha256:63b5d2f445ff0a7245e777fbacd05ea6eba4403e89991bc8e35dc97ef643249e","matched_routes":["quality-profile-generic","structured-engineering"],"recommended":["document.lifecycle-adoption-guide"],"registry_digest":"sha256:4d2ca4152890b64e46c012ba4a8f8f5116cc206d0e55a34d75570cab7c3fa46a","required_gates":["gate.independent-review","gate.relevant-tests","gate.written-plan"],"task_digest":"sha256:e4a2dcdfd45775dcaf776a3967dae65d51d02c052926c42bb025ea0cd0e2d40b","tier":"T2","unresolved":[],"workflow_mode":"structured"},"runtime_digest":"sha256:6fac3fd157509985b17b311df9ce40f89bb8ef55828868da0a1270541da6d7f3","schema_version":1,"state_metadata":{"task_state_present":false},"task_id":"CORE-DOGFOOD-04","tests_run":["tests.test_core_documentation.CoreDocumentationTests.test_active_registry_never_routes_historical_non_governing_documents","tests.test_core_documentation.CoreDocumentationTests.test_manual_dogfood_scorecard_is_closed","tests.test_core_documentation.CoreDocumentationTests.test_manual_dogfood_scorecard_rejects_resigned_or_structural_drift","tests.test_resource_registry","tests.test_core_routing"],"writers_observed":1}
```

Evidence digest: `sha256:f4c31e70e80df5d51d892d552856c9128c408a548246b8d37f629525ced2ac56`

### `CORE-DOGFOOD-05-E1`

```json
{"authorizes":false,"candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read","local_write"],"facts_only":false,"requested_outcome":"local_change","workload":"local","writers":1},"decision_digest":"sha256:63b79b5bfa31dd76f493ce26573771fd40655ba13229dd68eaaf074b50b36700","external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read","local_write"],"repository_local":{"autopilot":"OFF","branch":"codex/control-plane-core-3-1","clean":false,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","lock_valid":true},"result":"PASS","review":"APPROVED","routing":{"authorizes":false,"decision_ready":true,"deferred":["skill.decision-stress-test"],"errors":[],"inventory_digest":"sha256:63b5d2f445ff0a7245e777fbacd05ea6eba4403e89991bc8e35dc97ef643249e","matched_routes":["quality-profile-generic","structured-engineering"],"mode":"audit","profile_confidence":"fallback","profile_kind":"generic","profile_truncated":false,"project_profiles":["generic"],"recommended":["document.lifecycle-adoption-guide"],"registry_digest":"sha256:4d2ca4152890b64e46c012ba4a8f8f5116cc206d0e55a34d75570cab7c3fa46a","required":["document.profile-generic","gate.independent-review","gate.relevant-tests","gate.written-plan","instruction.project-agents","skill.verified-workflow"],"required_gates":["gate.independent-review","gate.relevant-tests","gate.written-plan"],"tier":"T2","unresolved":[],"workflow_mode":"structured"},"runtime_digest":"sha256:b82aec63e16d5b0f9448a3c39b3b7164995c8e5461416539c51b55526fbd0842","schema_version":1,"state_metadata":{"lease_present":false,"task_state_present":false,"unchanged":true},"task_digest":"sha256:11d6824e1f2657f7fa1887d86eb25cb8ada3042f1f327700ac7024feb35463ff","task_id":"CORE-DOGFOOD-05","tests_run":["status-projection-focal:1/1","adoption-recovery:12/12","runtime-lock:PASS"],"writers_observed":1}
```

Evidence digest: `sha256:347e67cd18fe64538805c0b3ba578a243e50099fe8887bfe9a26c4aaab0ec734`

### `CORE-DOGFOOD-06-E1`

```json
{"authorizes":false,"candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read","local_write"],"facts_only":false,"requested_outcome":"local_change","workload":"hybrid","writers":1},"decision_digest":"sha256:4e38dd9fb876b885f6aa39214a5b15a3918d8758d5252e23e371c6ce2547202a","external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read","local_write"],"profile_fixture":{"confidence":"marker_evidence","kind":"hybrid","profiles":["ios","saas_backend"],"truncated":false},"repository_local":{"autopilot":"OFF","branch":"codex/control-plane-core-3-1","clean":false,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","lock_valid":true},"result":"PASS","review":"APPROVED","routing":{"authorizes":false,"decision_ready":true,"deferred":["skill.decision-stress-test"],"errors":[],"inventory_digest":"sha256:63b5d2f445ff0a7245e777fbacd05ea6eba4403e89991bc8e35dc97ef643249e","matched_routes":["quality-profile-generic","structured-engineering"],"mode":"audit","project_profiles":["generic"],"recommended":["document.lifecycle-adoption-guide"],"registry_digest":"sha256:4d2ca4152890b64e46c012ba4a8f8f5116cc206d0e55a34d75570cab7c3fa46a","required_gates":["gate.independent-review","gate.relevant-tests","gate.written-plan"],"tier":"T2","unresolved":[],"workflow_mode":"structured"},"runtime_digest":"sha256:489c83587e3c043ae9041df392a862ed9f992a3372ed900ee66a4b0b59afc0dd","schema_version":1,"state_metadata":{"lease_present":false,"task_state_present":false,"unchanged":true},"task_digest":"sha256:373d8bdb05001680c2778466bed69de52914e23c044f2db6f8d3ba5c209bd4d2","task_id":"CORE-DOGFOOD-06","tests_run":["structural-quarantine:1/1","hybrid-quiescence:1/1","adoption-recovery-and-state-paths:16/16","runtime-lock:PASS"],"writers_observed":1}
```

Evidence digest: `sha256:fb7c3f0ae0a2ff0a39bfd4927d6aba7d7dca04d3cb7d190f0943dfc13c67ac24`

### `CORE-DOGFOOD-07-E1`

```json
{"authorizes":false,"candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read","local_write"],"facts_only":false,"requested_outcome":"local_change","workload":"hybrid","writers":1},"decision_digest":"sha256:ce9877cc84d587723cc9c08d644aff993fc79e3a17bee29939741dc2451ce4ec","external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read","local_write"],"profile_fixture":{"confidence":"marker_evidence","kind":"hybrid","profiles":["ios","saas_backend"],"truncated":false},"repository_local":{"autopilot":"OFF","branch":"codex/control-plane-core-3-1","clean":false,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","lock_valid":true},"result":"PASS","review":"APPROVED","routing":{"authorizes":false,"decision_ready":true,"deferred":["skill.decision-stress-test"],"errors":[],"inventory_digest":"sha256:63b5d2f445ff0a7245e777fbacd05ea6eba4403e89991bc8e35dc97ef643249e","matched_routes":["quality-profile-generic","structured-engineering"],"mode":"audit","project_profiles":["generic"],"recommended":["document.lifecycle-adoption-guide"],"registry_digest":"sha256:4d2ca4152890b64e46c012ba4a8f8f5116cc206d0e55a34d75570cab7c3fa46a","required_gates":["gate.independent-review","gate.relevant-tests","gate.written-plan"],"tier":"T2","unresolved":[],"workflow_mode":"structured"},"runtime_digest":"sha256:cbaa126e12535a713e2b6d44109713809338dad6051830611bbb92dff3d319ef","schema_version":1,"state_metadata":{"lease_present":false,"task_state_present":false,"unchanged":true},"task_digest":"sha256:2f1b6463f75df89bcb4f19c2e669d42a3d4b8d72f7054d0da3a51cb9ab86e762","task_id":"CORE-DOGFOOD-07","tests_run":["public-lease-cycle-and-replay:1/1","core-cli-and-leases:29/29","runtime-lock:PASS"],"writers_observed":1}
```

Evidence digest: `sha256:ebe5ecf36f0d445e6a3dcd7ec9b53c3f571323205d9a0e3c3c7d766c0cedf074`

### `CORE-DOGFOOD-08-E1`

```json
{"authorizes":false,"autopilot":"OFF","candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read","local_write"],"facts_only":false,"requested_outcome":"local_change","workload":"controlled","writers":1},"external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read","local_write"],"result":"PASS","review":"APPROVED","runtime_digest":"sha256:bb18924e638b5d231d4edaffaf1117c3193ab36d92df7d61467803ba0897d815","runtime_lock_errors":[],"schema_version":1,"source_manifest_digest":"sha256:2b1393338d1dc4bd16745463c836d9e19eae4e9e9691c9d4469be036555a81ce","state_metadata":{"lease_present":false,"task_state_present":false,"unchanged":true},"task_id":"CORE-DOGFOOD-08","tests_run":["owner-binding-corrections:3/3","runtime-lock:PASS"],"writers_observed":1}
```

Evidence digest: `sha256:2cd791638d31ed8fadaf5adb29541d8a9e1483c0df41e9035d1b1b0f6dc08b25`

### `CORE-DOGFOOD-09-E1`

```json
{"authorizes":false,"candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read","local_write"],"facts_only":false,"requested_outcome":"local_change","workload":"controlled","writers":1},"decision_digest":"sha256:284e665b3fafe5167ca7675acd9b5fc22b06a0ca9405b9751d494b9029a582c7","external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read","local_write"],"repository_local":{"autopilot":"OFF","branch":"codex/control-plane-core-3-1","clean":false,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","lock_valid":true},"result":"PASS","review":"APPROVED","routing":{"authorizes":false,"decision_ready":true,"deferred":[],"errors":[],"inventory_digest":"sha256:981ed5f01f4e7e7147760f5d9b648e24d2b03c37b28997c9659e724a23882421","matched_routes":["critical-assurance","quality-profile-generic","structured-engineering"],"mode":"audit","profile_confidence":"fallback","profile_kind":"generic","profile_truncated":false,"project_profiles":["generic"],"recommended":["document.lifecycle-adoption-guide","skill.decision-stress-test"],"registry_digest":"sha256:4d2ca4152890b64e46c012ba4a8f8f5116cc206d0e55a34d75570cab7c3fa46a","required_gates":["gate.independent-review","gate.rollback-plan","gate.security-review","gate.written-plan"],"tier":"T3","unresolved":[],"workflow_mode":"controlled"},"runtime_digest":"sha256:2043db78698cf77e0a18d2e60a8559336a5d2f7f7f158b4af8d45cc0c69cea17","schema_version":1,"state_metadata":{"lease_present":false,"task_state_present":false,"unchanged":true},"task_digest":"sha256:0291a9a5a9f2e75b69136e6fbd8306a983fec25b9685ad33e6278815eb52be3f","task_id":"CORE-DOGFOOD-09","tests_run":["next-revision-adversarials:4/4","task-state-lease-cli:48/48","runtime-lock:PASS"],"writers_observed":1}
```

Evidence digest: `sha256:78ae6111baa5503f411961c974433e4cba3f136b514a17faa9f6ad5a7daed42e`

### `CORE-DOGFOOD-10-E1`

```json
{"authorizes":false,"candidate_version":"3.1.0-core.1","contract":{"durable_task_state":false,"effects":["local_read","local_write"],"facts_only":false,"requested_outcome":"local_change","workload":"controlled","writers":1},"decision_digest":"sha256:d945ed7b8775572c8cb202452aa9d301c7d5da902a517e6d059ce79fd6ee628a","external_effects":[],"kind":"CoreDogfoodEvidenceV1","network_used":false,"observed_effects":["local_read","local_write"],"repository_local":{"autopilot":"OFF","branch":"codex/control-plane-core-3-1","clean":false,"head":"d48cdd7dd2bf5090a55a871c7b722083276c8bff","lock_valid":true},"result":"PASS","review":"APPROVED","routing":{"authorizes":false,"decision_ready":true,"deferred":[],"errors":[],"inventory_digest":"sha256:981ed5f01f4e7e7147760f5d9b648e24d2b03c37b28997c9659e724a23882421","matched_routes":["critical-assurance","quality-profile-generic","structured-engineering"],"mode":"audit","profile_confidence":"fallback","profile_kind":"generic","profile_truncated":false,"project_profiles":["generic"],"recommended":["document.lifecycle-adoption-guide","skill.decision-stress-test"],"registry_digest":"sha256:4d2ca4152890b64e46c012ba4a8f8f5116cc206d0e55a34d75570cab7c3fa46a","required_gates":["gate.independent-review","gate.rollback-plan","gate.security-review","gate.written-plan"],"tier":"T3","unresolved":[],"workflow_mode":"controlled"},"runtime_digest":"sha256:2043db78698cf77e0a18d2e60a8559336a5d2f7f7f158b4af8d45cc0c69cea17","schema_version":1,"state_metadata":{"lease_present":false,"task_state_present":false,"unchanged":true},"task_digest":"sha256:a8d7e90d1a34c67d958bd6225b0985f056a2386ba0ec133765672cdc65fd41bb","task_id":"CORE-DOGFOOD-10","tests_run":["verification-module:13/13","mutex-pre-git-and-docs:2/2","shell-syntax:PASS","runtime-lock:PASS"],"writers_observed":1}
```

Evidence digest: `sha256:ec29389f6afb3492d85f21f4e92ca20e699a2605f66a97c466e12292c408d8e8`

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del candidato Core y scorecard manual.
- **Para continuar:** ejecutar una única vez `bash tests/run.sh` sobre estos bytes finales y detenerse si no es `PASS`.
- **Mensaje exacto:** `Ejecuta el único gate integral de Control Plane Core 3.1; no edites ni realices efectos remotos después.`
- **Estado de partida:** `3.1.0-core.1`, diez filas `PASS`, gate dogfood satisfecho, gate integral aún no observado y adopción estable no autorizada.
- **No hacer todavía:** instalar, adoptar externamente, commit, push, PR, merge, deploy, publicación o release.
- **Autoridad:** `authorizes=false`
