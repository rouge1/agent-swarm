# Lessons learned: the Swarm Control case study (2026-09)

We built a working browser game in five phases: FastAPI + WebSocket server, numpy simulation, canvas client,
3 levels, upgrade shop and balance, with 148 tests. Three OpenCode workers did the building: GPT-5.6 Luna,
Muse Spark 1.3 and DeepSeek V4.1 Flash. The repo is `rouge1/skinny-swarm-control`. This skill's scripts and
templates are the generalized version of that project's ops tooling.

## Where the money went

| | OpenCode workers | Claude |
|---|---|---|
| Phases 0–3 (Opus orchestrated and reviewed) | $0.31 | $25.71 |
| Phase 4 (Sonnet manager, OpenCode reviews) | $0.47 | $11.58 |
| Phase 5 (Sonnet manager, leaner) | $0.36 | $6.36 |
| **Total** | **$1.14** (77 runs) | **$43.65** (Opus $29.32, Sonnet $13.99, Haiku $0.34) |

- **The workers are almost free. Coordination is what costs money.** An OpenCode run costs 0.5–3¢. Claude's cost is
  mostly cache reads: every tool call re-reads the whole conversation. Opus read 68M cached tokens, and the
  Sonnet managers read 52M.
- **Claude's cost grows with its tool calls multiplied by its context size.** A long-lived orchestrator gets more
  expensive every turn. The Phase 4 manager made 251 tool calls and cost about $7.30. With a "cost discipline"
  section in its runbook, the Phase 5 manager cost about $6.

## What worked

1. **Acceptance tests before code.** The tests are the spec, and workers who never see each other still fit together.
   In P0–P3 the orchestrator checked its own tests against a private reference implementation kept outside the repo.
2. **Bake-offs.** Three models implement the same spec, and reviewers pick a winner. This cost about 6¢ per
   bake-off and was the best quality-per-dollar step. Different models make different mistakes, and losing entries
   had real bugs that the tests missed or only partly caught (two passed every test):
   - Luna's World burned tokens on a failed purchase.
   - Muse's World paid a level's reward again on every replay.
   - Muse's level balance had a stand-still win.
3. **Cross-model review by OpenCode.** The two models that didn't write the code review it, and a third run checks
   every claim against the code and produces a consensus fix list. The reviewers found bugs the tests missed, and
   the consensus step filtered out misreadings.
4. **Fix rounds in the author's own session** (`-s`). The author keeps its context, so fixes are small and cheap.
5. **Objective gates over opinions.** These tools turned vague goals into pass/fail numbers:
   - tests and ruff
   - `scripts/bench.py`: 4000 units = 2.2 ms/step
   - `scripts/balance.py`: scripted players per level
   - Playwright play-tests with screenshots and video

   For example, "is it balanced?" became "idle and stand-still lose, a sweep wins, and times rise per level".
6. **Starting later work early.** The Phase 5 measurement scripts were written during Phase 4. They found the
   stand-still exploit and "Level 3 is easier than Level 2" before Phase 5 began.
7. **Handing off to a Sonnet phase manager plus a runbook.** Opus's share fell from about 96% of Claude's cost in
   P0–P3 to about 6% in P5.
8. **A watchdog, then OpenCode triage.** A script detects stalls for free. A cheap read-only Flash run then diagnoses
   the stall and recommends resume, restart or reassign. It recovered three stuck runs with no lost work.
9. **An event log driving a dashboard.** Every action appends to `events.jsonl`, which feeds the live dashboard, the
   cost tables and the replay page.

## What didn't work (and the fix)

| Problem | Fix |
|---|---|
| `opencode export` output is cut off at about 64 KB when piped | Export to a file |
| Two `-s` continuations running in parallel hung silently for 25 min | `stdin=DEVNULL`, a watchdog, and never run two continuations of one session at once |
| The watchdog killed Flash while it was silently writing a big file | Two stages: 120 s before the first output, 600 s after |
| Workers quit silently after a denied read outside their worktree | Every prompt says "Do not list or read anything outside the working directory" |
| The test author wrote a wrong assertion, and the implementer followed it | Two reviewers on the tests. Also, when several implementations disagree with a test, suspect the test |
| The balance test sampled 5 stand positions, and an exploit sat between them | Use dense grids or property tests for "must never happen" rules |
| OpenCode reviewers can't see the UI, so visual bugs slipped past them (overlapping text, a ghost overlay) | The manager play-tests with Playwright. Next time, give reviewers a screenshot script |
| Opus orchestrated P0–P3 itself, at 70x the cost of the workers | A Sonnet manager from Phase 2 onward |
| The dashboard showed the orchestrator doing workers' fixes | Task events always carry the worker's model |
| Dashboard database writes drifted out of version | Track `if_version` locally, with a `--resync` option |
| `pkill -f` killed Claude's own shell | Find the pid with `ss -ltnp` or `pgrep`, then `kill <pid>` |
| The Chrome extension sign-in failed | Playwright (headless, scriptable, records video) |

## What I'd do better next time

- **Write the runbook and ops tool on day one.** That means the watchdog, the ledger, task events and the review
  pattern. Then run every phase from Phase 1 with a fresh Sonnet manager.
- **Opus only plans.** It writes the phase plan, the specs and the acceptance-test targets. It launches managers and
  reads their reports. Specs are where a strong model pays off, because every vague line turns into a bug or a
  review round.
- **Use short-lived managers with small contexts.** Run one manager per phase, or even per workstream. They hand
  off through files (specs, review files, the event log), not through conversation history.
- **Make the tests harder to fool:** property and grid tests, plus a small test bake-off (two test authors, keep the
  union) for core logic.
- **Give reviewers eyes:** a `screenshot.py` they can run inside their worktree, so UI review isn't done blind.
- **Set a budget per phase** (for example, stop the manager above N tool calls or $X) and show it on the dashboard.
- **Assign by track record** (the sample is small):
  - Flash was the cheapest and won 3 of 4 bake-offs. Its only loss was the first one (a `clear()` bug).
  - Luna was careful and good at tests and reviews.
  - Muse was slowest and lost every bake-off, but was a sharp reviewer.

## The cheapest way to get quality work

1. **Put all generation on cheap models:** code, tests, reviews, review consensus and triage. At 1–3¢ a run you can
   afford redundancy, and redundancy is where the quality comes from.
2. **Buy quality with diversity, not a bigger model.** A bake-off plus cross-model review catches more than one
   strong model writing alone, and costs less than a single Opus review.
3. **Use deterministic checks first:** tests, lint, benchmarks, balance scripts and Playwright. They're free, exact
   and don't get tired.
4. **Keep Claude to judgment calls at a few checkpoints:** plan and spec (Opus), coordination (Sonnet), and
   checklists (Haiku). Escalate only `[high]` findings to a stronger model.
5. **Minimize manager tool calls,** because each one re-reads the context. Batch shell work, block on runs instead
   of polling, and don't re-read logs.
6. **Parallelism tops out where file ownership does.** About 5–10 concurrent workers was useful here, since two
   workers editing the same file just produce merge conflicts.
