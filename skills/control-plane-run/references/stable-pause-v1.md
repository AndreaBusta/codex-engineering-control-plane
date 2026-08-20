# Stable Pause v1

Use this verify-only procedure only for an explicit stable stop, resumable checkpoint, or resume from that checkpoint. It observes Core-owned local state and joins it with native host visibility; it creates no pause lifecycle state and every result remains `authorizes=false`.

## Checkpoint

1. Resolve the exact current task ID and same worktree from trusted native context. If either is unavailable, report `UNKNOWN`.
2. Check the native host before Core. An active host operation, yielded command, test, tool session, or writing worker makes the result `UNSAFE_PAUSE`; unavailable host visibility makes it `UNKNOWN`.
3. Only the bounded foreground observer may be present during the invocation. Run exactly once, from the worktree root:

   `scripts/control-plane task checkpoint --mode stable-pause --task-id EXACT-TASK-ID --json`

4. Check the native host after Core exits. Confirm that the foreground observer is gone and no command, test, tool session, or writer remains yielded.
5. Join native and Core evidence without promotion. Core `UNSAFE_PAUSE` or `UNKNOWN` is never upgraded by native evidence; native evidence never upgrades either result. A native active host operation downgrades to `UNSAFE_PAUSE`; unknown host visibility downgrades to `UNKNOWN`.

Core exit semantics are closed: `SAFE_PAUSE_ACTIVE` and `SAFE_PAUSE_TERMINAL` use exit 0, `UNSAFE_PAUSE` uses exit 1, and `UNKNOWN` uses exit 2.

## Boundaries

This procedure does not kill, does not interrupt, does not clean, does not mutate task or lease state, does not create a Goal, does not run tests or gates, and does not perform Git or remote transitions. It never waits unboundedly. If already-authorized work is still progressing, use only a bounded native wait that remains inside the user's request; otherwise stop unsafe or unknown.

Do not scan a global process table, temporary directories, caches, browser state, or unrelated files. Do not persist Core JSON as a capability or authority.

## Continuation capsule

Record only the effective status, exact task and same worktree, branch and HEAD, `checkpoint_digest`, concise dirty/RED evidence, remaining work, next safe action, and `authorizes=false`. Exclude transcript, hidden reasoning, raw output, full diff, secrets, and personal data.

## Resume

Resume only in the same task and same worktree. Re-run the native before check, the Core checkpoint once, and the native after check. Compare `checkpoint_digest`; explain any drift, then re-enter normal routing, preflight, authority, lifecycle, and verification gates. A prior checkpoint never authorizes continuation or any effect.
