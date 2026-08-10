# v2.3 Outcome Bridge Threat Model

Status: implementation contract; not evidence of deployment or remote support.

## Assets

- Current product-level user intent and its exact outcome boundary.
- The reviewed diff, tree, commit, feature ref, Pull Request and merge SHA.
- Repository, base, branch, checks, policy, task and scope bindings.
- One-shot host state, durable non-authorizing receipts and recovery markers.
- Git history, worktrees and credentials held outside the repository.

## Trust boundaries

```text
current native user interaction
        │ opaque, process-local, one-shot
        ▼
host effect boundary ── closed effect plan ── external Git/GitHub tool
        │                                      │
        └── observation only ── Python kernel ─┘
                                  │
                                  └── durable authorizes=false evidence
```

Prompts, Markdown, task text, plans, receipts, review reports, CLI arguments and
test adapters are outside the authority boundary. GitHub and the local
filesystem remain separately mutable. The v2.3 Python outcome path exposes no
serializable authorization context or remote executor.

## Threats and mitigations

| Threat | Failure | Mitigation |
|---|---|---|
| replay | A prior effect or receipt is consumed again | Process-local one-shot effect consumption; durable effect IDs and exact receipt replay rejection |
| drift | Branch, base, policy, checks, scope or reviewed subject changes | Executor-edge revalidation and exact digests; drift blocks and requires one product-level reauthorization |
| forged or stale review | Scalar input fabricates a PASS, or a reviewed tree is replaced before commit or PR | Persist only after consuming a fresh exact host-bound reviewer observation; bind its reviewer/observation digests and review head/tree/diff through commit, push and PR; T3 requires distinct observations |
| local commit marker substitution | A caller reuses the staged index with a different parent, tree or commit message | Revalidate the complete durable marker digest, delivery binding, live parent, index observation, expected tree and exact message digest before authorization consumption or `git commit` |
| uncertain write | Timeout or crash hides whether push, PR or merge happened | Arm observe-only before write; observe before retry; zero second write and no automatic repair while `UNKNOWN` |
| fabricated evidence | JSON claims PASS or authority | Closed receipts are non-authorizing; host-only publishers and exact lineage validation fail closed |
| split remote identity | Fetch alias differs from the push destination | Bind the credential-free remote URL and canonical identity through the integration plan, refresh receipt, immutable registry and marker |
| stale base proof | Cached base contains a SHA obtained from another source | Require an exact host refresh receipt and then prove merge containment in the refreshed canonical ref |
| internal-plumbing prompt | User is asked for classes, grants, nonces or bindings | Skill asks only for product intent; missing native adapter is `BLOCKED` |

## Local versus hosted enforcement

Local guards are not GitHub branch protection. Hooks, leases, policy snapshots
and lifecycle validation reduce cooperative local mistakes; they cannot stop an
external process, GitHub administrator or alternate client. Required checks,
Rulesets, provider identity and merge state require provider-side evidence.

## Residual risks

- A compromised host or Git/GitHub client can lie at its own trust boundary.
- Process death can destroy unconsumed authority and require a new native
  product request; serializing it as recovery is intentionally forbidden.
- GitHub Free or repository policy may not provide enforceable Rulesets.
- Local filesystem compromise can alter code or receipts outside this model.
- The native remote path remains unavailable where no native host adapter is
  installed; tests do not change that fact.

## Recovery

Use [the outcome bridge rollback runbook](../engineering/16-outcome-bridge-rollback.md).
Do not infer a retry from a timeout or from this document.
