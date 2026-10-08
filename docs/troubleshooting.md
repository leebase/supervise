# Troubleshooting

Every entry names what the observation means and the permitted response. "Permitted" matters: editing state or forging a verdict does not work by design, and renaming or copying the PLAN is prohibited by policy because it creates a new invocation and orphans the original accounting (the system does not link the two).

| Observation | Cause | Permitted response |
|---|---|---|
| `PRE_ADMISSION` / `PRECHECK_FAILED`, version 0, zero starts | The candidate did not validate: wrong test argv, a selector not found in the baseline, source root mismatch, an oversized frozen input, a wildcard in the argv | `diagnose` at version 0, correct the PLAN or project, re-admit. Nothing was spent. |
| `MANAGER_ERROR` in the transport receipt | The manager model is rejected by the coding-agent transport (for example a model name the Codex transport does not support under a subscription account) | Fix the global or project model configuration; re-invoke the same PLAN. |
| `MANAGER_STALLED` | The manager stopped acting with the unit open (its nudge budget was exhausted) | Inspect `state.json.next`; re-invoke. If it recurs the PLAN is probably ambiguous. |
| `MANAGER_TIMEOUT` | The host window expired | Re-invoke with a larger finite `--max-seconds` (at most 7200). No native allowance is added. |
| Author `verdict: pass` but `advanced: false` in `decision.json` | The worker ran but produced no source change (in the accepted run, a repair worker went looking for the manager's tool instead of editing) | Nothing; the next packet carries that fact. Starts within one reviewer-directed round share a round number; at most two rounds are admitted. |
| `judge_fail` with findings | A genuine review rejection | Let the supervisor run the bounded repair; read the finding. |
| Review finish with no `judge_pass` / `judge_fail` | Timeout, role violation or invalid output; the review is incomplete, not a verdict | The reserved fallback reviewer may run; unchanged evidence is never re-reviewed without a changed action. |
| `OWNER_REFUSED: No eligible independent same-role fallback remains` | Author and reviewer families collided | A routing fact, not a quality verdict. v2.8.0+ excludes such authors before dispatch. |
| `NOVELTY_INVALID` / contract exceeds bounded snapshot size | Frozen inputs or the observation exceed the Router's file bound | Reduce the admitted inputs; chunked sealed evidence covers the review handoff, not oversized contracts. |
| `Route provenance requires protected immutable regular file` | `pending.json` not protected (releases before v2.8.1) | Upgrade. |
| `Native receipt record attribution/integrity mismatch` at settlement | Route receipt without an attempt id (releases before v2.8.2) | Upgrade; that invocation stays `RECONCILE_REQUIRED` as history. |
| `Installed Router source differs from accepted publication` | A pinned Router file changed after installation (another session installed a release, or a file was edited) | Restore the pinned bytes or install a reviewed release. This gate is correct. |
| `OWNER_OUTCOME_UNKNOWN` | A start's outcome was not observed (crash, kill, lost session) | `reconcile` first; never redispatch an unknown start. |
| `CHECKPOINT_EXHAUSTED` | The internal starts or seconds checkpoint ran out | Blocked. Continuation needs applicable owner authority; the command cannot self-grant starts. |
| `manager_returncode: 143` with `status: ACCEPTED` | Normal success exit: the transport terminates the idle manager after acceptance | Nothing. |
| Claim refused (exit 3) when installing or publishing | Another live instance owns the path | Stop and report; a stale claim may be superseded through the claim command, never by force. |

If none of these matches, read the invocation's `state.json`, the last `finish` record in `STATE.jsonl` and the native stream for the last start (`router-N-*/router.stdout`). The [owner guide](owner-operator-guide.md) explains every file.

---

*Part of the [`/supervise` public documentation](../README.md), Apache License 2.0.*
