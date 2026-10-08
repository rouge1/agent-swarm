# Phase <PHASE> report: <title>

Under 250 words. Measured and inferred are separate lines: do not present a causal story as a fact.

**Outcome:** <done | deferred | blocked>. Main is at <commit> with <n> tests passing (<lint result>).

**Gates (from the pre-registered spec):**
| gate | threshold | measured | pass |
|---|---|---|---|
| <target> | <threshold> | <value, denominator> | yes / no |

**Winners and scores:** <task: model, score, one-line reason>. Dropped entries and why.

**Review findings:** <per model: issues found, fixed, left open with reason>. Consensus rejected: <n>.

**Measured:** <what the scripts and tests showed, with numbers>.

**Inferred (not measured):** <what you think is true but did not check, and why you think so>.

**Not shown / limits:** <what this phase did not test, including UI or play-test gaps>.

**Triage actions:** <task: cause -> RESUME | RESTART-KEEP | RESTART-DISCARD | REASSIGN>. Or "none".

**Cost:** <OpenCode and Claude cost for the phase, per model>. Advisor calls: <n, model>.

**Done by you, not a worker:** <list, or "nothing">.

**Needs the planner or user:** <decisions, approvals, amendments needing approval>.

**Merge:** <done on main at <commit> | handed to the planner as a merge list: branch, tip>.

**Next command:** `<exact command>`
