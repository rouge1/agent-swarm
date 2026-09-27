---
name: agent-swarm
description: Run a software project with a swarm of cheap OpenCode worker agents (several LLMs) managed by Claude. Covers phases, git worktrees, tests written before code, multi-model bake-offs, cross-model code review with consensus, stall triage, time and cost tracking per model and phase, a live dashboard and a shareable replay. Use when the user wants to build something with OpenCode or other CLI coding agents as workers, orchestrate several coding models, run a model bake-off, or cut the cost of agentic coding by delegating to cheap models.
---

# OpenCode swarm

Claude plans and coordinates. Cheap OpenCode models do the generation work: code, tests, reviews, review
consensus and triage. Deterministic checks decide quality: tests, lint, benchmarks and Playwright. Every run is
logged, so time and money are tracked per model and per phase and can be replayed later.

This setup was proven on a real project: a five-phase browser game with 148 tests, costing $1.14 of OpenCode
across 77 runs. Read `references/lessons.md` before a first run. It explains why the process looks the way it does.

## Roles

| Role | Who | Does | Never does |
|---|---|---|---|
| Planner | Claude, the strongest model (the main session) | Project plan, phase specs, acceptance targets, `AGENTS.md`, launching phase managers, reading their reports, talking to the user | Coordinate a phase step by step, write feature code |
| Phase manager | Claude Sonnet subagent, one per phase, fresh context | Everything in `references/runbook.md`: worktrees, launching workers, reviews, fix rounds, merge, play-test, dashboard | Write feature code or tests (tiny fixes only, and logged) |
| Checklist agent | Claude Haiku subagent (optional) | Check that a fix list landed; mechanical verification | Make judgment calls |
| Workers | OpenCode models (2–4 of them) | Implement tasks, write tests, fix their own review findings | Touch files outside their task, or anything outside their worktree |
| Reviewers | OpenCode models that did not write the code | Read-only review in a detached worktree | Edit files |
| Consensus / triage | A third OpenCode run | Check every review claim against the code; diagnose stalled runs | Edit files |

**Cost rule:** Claude's cost comes from how many tool calls it makes multiplied by how large its context is, and
not from the workers. Keep the planner's turns few and short. Give each phase a fresh manager. Managers block on
runs instead of polling. Details are in `references/lessons.md` under "Where the money went".

## Folder map

```
opencode-swarm/
  SKILL.md                     this file
  swarm.toml.example           config template (commented): paths, forbidden dirs, models, phases, prices
  examples/textstats.swarm.toml  a filled-in config from a live test project (paths are placeholders)
  scripts/swarm.py             ops CLI (stdlib only): init, wt, run, task, note, review, tests, score, crew,
                               phase, health, cost, status, claude, push, export, sync, recover
  scripts/screenshot.py        Playwright screenshots, video and console errors from a JSON step list
  assets/dashboard.html        live dashboard and replay page (Artifact with the db capability, or embedded)
  references/runbook.md        the phase manager's step-by-step procedure (give it to every manager)
  references/lessons.md        what worked, what failed, the cost model, model track records
  references/opencode-cli.md   OpenCode CLI usage and gotchas
  references/dashboard.md      publishing the dashboard, pushing events, exporting the replay
  templates/AGENTS.md          worker rules (copy into the project repo root)
  templates/spec.md            phase spec skeleton
  templates/prompts/           manager-launch, test-author, task, review, review-tests, review-ui,
                               consensus, fix, triage
```

## Setup (the planner does this once)

1. **Prerequisites:**
   - the `opencode` CLI with a provider configured (for example OpenRouter), and `opencode run` working without
     the interactive interface
   - git and Python 3.11 or newer
   - optional: Playwright for Python, for UI projects
2. **Pick 2–4 worker models.** Diversity matters more than strength: different models make different mistakes.
   Put their ids in the config.
3. **Create the repo and ops dir:**
   ```bash
   mkdir -p <root>/<project> && git -C <root>/<project> init
   python3 <skill>/scripts/swarm.py init --name "<Project>" --repo <root>/<project>   # run inside <root>/<project>-ops
   ```
   Edit `swarm.toml`:
   - `project.worktrees`: for example `<root>/<project>-wt`
   - `project.forbidden`: every directory that must never host a worker. Include the user's sensitive folders.
   - `project.test_cmd` and `project.lint_cmd`
   - `[[models]]` and `[[phases]]`
4. **Phase 0 is the planner's own work**, because it sets the contracts everyone codes against:
   - the repo skeleton, venv and test harness
   - `AGENTS.md` from `templates/AGENTS.md`
   - `CONTRACTS.md`: the module interfaces
   - the first acceptance tests

   Check your own tests against a private reference implementation kept outside the repo. Commit on `main`.
5. **Optional dashboard:** see `references/dashboard.md`.

## Running a phase

1. **Planner:** write `specs/<phase>.md` from `templates/spec.md`. List the tasks, file ownership (one owner per
   file), the acceptance targets as numbers, and which tasks get a bake-off. Keep it short and exact: every vague
   line costs a review round.
2. **Planner:** launch a Sonnet subagent with `templates/prompts/manager-launch.md`, filled in. It reads
   `references/runbook.md` and runs the whole phase.
3. **Planner:** wait. Don't poll the manager or re-check its work step by step. When the report arrives:
   - check the pushed commit and the test count (one command)
   - run `swarm.py cost` and `swarm.py claude`
   - relay the results to the user in a few lines
4. **Repeat for the next phase.** Measurement tools for later phases (benchmarks, balance scripts) can be built
   early, in parallel, as separate tasks.

## Phase lifecycle (the manager follows it, details in the runbook)

`tests first` (one OpenCode test author) → `test review` (2 other models + consensus run) → `fix` (author's session)
→ `implement` (a bake-off of 2–3 models for core logic, a single author otherwise) → `review` (2 non-authors each)
→ `consensus` (third run; ranks bake-off entries, 0–100) → `fix` (winner's session) → `integrate + play-test`
→ `merge --no-ff` → `sync` records → push (if the user approved) → dashboard.

## Hard rules

- **Workers only ever run inside a worktree under `project.worktrees`.** `swarm.py run` enforces this and refuses
  the main checkout and anything in `project.forbidden`. Never call `opencode` directly, and never bypass the check.
- **Launch every worker through `swarm.py run`.** That is what records time, cost, sessions and events.
- **Every worker prompt names the files the worker may edit and says "Do not list or read anything outside the
  working directory."** A denied read makes some models quit silently.
- **Workers fix their own code** in their own session (`--session`). Anything Claude edits in the product is
  logged with `swarm.py note "ORCHESTRATOR FIX: ..."`.
- **Pushing to a remote, publishing, and other outward actions need the user's approval.** Say in the manager
  launch prompt exactly what is pre-approved.
- **Never `pkill -f` a pattern:** it can kill the agent's own shell. Kill by pid.
- **Never run two `--session` continuations of the same session at once.** They hang.

## Running under OpenCode instead of Claude Code
OpenCode (1.18 and later) loads this skill too. It reads `.opencode/skills/`, `.claude/skills/`, `.agents/skills/`,
`~/.config/opencode/skills/`, and auto-loads `~/.claude/skills/`.
- Map the roles to models: planner and manager = your strongest OpenCode model (as an OpenCode subagent for the
  manager); workers, reviewers, consensus and triage = the cheap models in `[[models]]`.
- Unchanged: `swarm.py`, the templates, the runbook, `screenshot.py` and `swarm.py export` (the replay file).
- Not available:
  - the live Artifact dashboard (claude.ai only)
  - `swarm.py claude`, which reads Claude Code transcripts. Price the orchestrator's session with `opencode export`
    instead.
- The worktree and forbidden-dir guard only protects worker runs. Run the orchestrating OpenCode agent with a
  permission profile that asks before shell commands and before touching folders outside the project.
