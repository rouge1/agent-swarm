---
name: agent-swarm
description: Run a software project with a swarm of cheap coding agents (OpenCode models, Grok, Claude subagents) managed by one strong orchestrating model. Covers phases, git worktrees, tests written before code, multi-model bake-offs, cross-model code review with consensus, stall triage, time and cost tracking per model and phase, a live local dashboard and a shareable replay. Use when the user wants to build something with CLI coding agents as workers, orchestrate several coding models, run a model bake-off, or cut the cost of agentic coding by delegating to cheap models.
---

# Agent swarm

One strong model plans and coordinates. Cheap worker models do the generation work: code, tests, reviews, review
consensus and triage. Deterministic checks decide quality: tests, lint, benchmarks and Playwright. Every run is
logged, so time and money are tracked per model and per phase and can be replayed later.

It has run two real builds:
- a five-phase browser game: 148 tests, $1.14 of OpenCode cost across 77 runs
- a seven-phase Python CLI built by Claude subagents and Grok

Read `references/lessons.md` before a first run. It explains why the process looks the way it does.

## Roles

| Role | Who | Does | Never does |
|---|---|---|---|
| Planner | The strongest model (the main session) | Project plan, phase specs, acceptance targets, `AGENTS.md`, launching phase managers, reading their reports, talking to the user | Coordinate a phase step by step, write feature code |
| Phase manager | A cheap-but-careful subagent, one per phase, fresh context (`agents/`) | Everything in `references/runbook.md`: gates, worktrees, launching workers, reviews, fix rounds, merge, play-test, report | Write feature code or tests (tiny fixes only, and logged); change a gate after seeing results |
| Advisor | A stronger model the phase manager consults at a few decision points (optional) | Reviews a plan, a score, a finding, a merge or a triage question; read only | Make changes; run commands |
| Checklist agent | A small, cheap subagent (optional) | Check that a fix list landed; mechanical verification | Make judgment calls |
| Workers | 2–4 models from `[[models]]` | Implement tasks, write tests, fix their own review findings | Touch files outside their task, or anything outside their worktree |
| Reviewers | Models that did not write the code | Read-only review in a detached worktree | Edit files |
| Consensus / triage | A third model's run | Check every review claim against the code; diagnose stalled runs | Edit files |
| Scribe | A cheap agent (any model, any CLI) on top of the `watch` timer | Reads what every other agent is doing and writes it to the board: summaries, trouble notes, unlogged Claude runs (`templates/prompts/scribe.md`) | Change code, files or task state |

Under Claude Code the usual mapping is Opus as planner, Haiku 5.5 as phase manager with a Sonnet 5.5 advisor, and
Haiku 5.5 as scribe. Under OpenCode, use a strong model as planner, a mid-tier model as manager, a strong model as
advisor (not a free tier, since it reads the whole question) and a cheap model as scribe. Setup below installs the
agents.

**Sensitive data:** set `[project] sensitive = true` in `swarm.toml` when the repo holds credentials, personal data
or private code. Then `swarm.py run` refuses every model that does not set `trains_on_prompts = false`, reviewers
included. Free tiers may train on prompts and code. Keep them off sensitive projects. The guard does not cover
`driver = "claude"` subagents, which `run` never launches, and the manager, advisor and scribe agents (under
Claude Code or OpenCode), which read the code outside `run`. Keep these off sensitive repos unless the planner accepts
that those models see the code. The flag restricts which providers may see the data; it does not keep data
on this machine.

**Cost rule:** the orchestrator's cost comes from how many tool calls it makes multiplied by how large its context
is, and not from the workers. Keep the planner's turns few and short. Give each phase a fresh manager. Managers
block on runs instead of polling. Details are in `references/lessons.md` under "Where the money went".

## Worker drivers

Each `[[models]]` entry in `swarm.toml` has a `driver` that decides how `swarm.py run` launches it:

| Driver | Launches | Cost from | Fix rounds (`--session`) |
|---|---|---|---|
| `opencode` (default) | `opencode run` | `opencode export` | the same OpenCode session |
| `grok` | Grok CLI, headless, inside its kernel sandbox | the run's own JSON result | `--resume` of the same Grok session |
| `claude` | not launched by `swarm.py`: the orchestrator runs these as its own subagents | `swarm.py claude` (session transcripts) | resume the subagent |

The `grok` driver refuses to run without a sandbox profile (`[grok] sandbox`); see `references/grok-cli.md` for
the profile, the Linux AppArmor fix and the permission rule it needs under Claude Code.

## Folder map

```
agent-swarm/
  SKILL.md                     this file
  swarm.toml.example           config template (commented): paths, forbidden dirs, models and drivers, phases, prices
  examples/textstats.swarm.toml  a filled-in config from a live test project (paths are placeholders)
  scripts/swarm.py             ops CLI (stdlib only): init, wt, run, task, note, review, tests, score, crew,
                               phase, health, cost, status, claude, scan, agents, activity, log-run, site,
                               watch, push, export, sync, recover
  scripts/screenshot.py        Playwright screenshots, video and console errors from a JSON step list
  scripts/tests/               driver, board and sensitive-repo tests (a fake Grok CLI stands in for the real one)
  agents/claude/               swarm-manager (Haiku 5.5), swarm-scribe (Haiku 5.5), swarm-advisor (Sonnet 5.5)
  agents/opencode/             the same three for OpenCode; the model is a placeholder to fill in
  assets/dashboard.html        live dashboard and replay page (local, embedded replay, or claude.ai Artifact)
  references/runbook.md        the phase manager's step-by-step procedure (give it to every manager)
  references/lessons.md        what worked, what failed, the cost model, model track records
  references/opencode-cli.md   OpenCode CLI usage and gotchas
  references/grok-cli.md       Grok CLI headless usage, sandbox profile, gotchas
  references/dashboard.md      running the dashboard, pushing events, exporting the replay
  templates/AGENTS.md          worker rules (copy into the project repo root)
  templates/spec.md            phase spec skeleton (with pre-registered gates and amendments)
  templates/report.md          the phase manager's final report
  templates/prompts/           manager-launch, test-author, task, review, review-tests, review-ui,
                               consensus, fix, triage, scribe
  docs/requirements.md         where the project is heading
```

## Setup (the planner does this once)

1. **Prerequisites:**
   - git and Python 3.11 or newer
   - at least one worker CLI working headless: `opencode run` with a provider configured (for example
     OpenRouter), and/or the Grok CLI with a sandbox profile
   - optional: Playwright for Python, for UI projects
2. **Pick 2–4 worker models.** Diversity matters more than strength: different models make different mistakes.
   Put their ids and drivers in the config.
3. **Create the repo and ops dir:**
   ```bash
   mkdir -p <root>/<project> && git -C <root>/<project> init -b main

   python3 <skill>/scripts/swarm.py init --dir <root>/<project>-ops --name "<Project>" --repo <root>/<project>
   ```
   Edit `swarm.toml`:
   - `project.worktrees`: for example `<root>/<project>-wt`
   - `project.forbidden`: every directory that must never host a worker. Include the user's sensitive folders.
   - `project.test_cmd` and `project.lint_cmd`
   - `[[models]]` (with `driver`, and `trains_on_prompts` if `sensitive` is on) and `[[phases]]`
4. **Phase 0 is the planner's own work**, because it sets the contracts everyone codes against:
   - the repo skeleton, venv and test harness
   - `AGENTS.md` from `templates/AGENTS.md`
   - `CONTRACTS.md`: the module interfaces
   - the first acceptance tests

   Check your own tests against a private reference implementation kept outside the repo. Commit on `main`.

   **Record work of your own as a task**, so the board and the spend can tell it from orchestrating:
   `swarm.py task p0 skeleton working --model orchestrator --title "Repo skeleton"`, then `review` or `merged`
   when it is done. Anything you do while such a task is `working` or `fixing` counts as your own work; the
   rest (writing specs, launching managers, waiting, reading reports) is orchestration and needs no task.
   With `[claude] session` set, the task card also shows your latest action, read from your transcript.
5. **Agents (Claude Code and OpenCode):** copy the manager, scribe and advisor agents into the project so the
   planner can launch them by name:
   - Claude Code: `mkdir -p <root>/<project>/.claude/agents && cp <skill>/agents/claude/*.md <root>/<project>/.claude/agents/`. The model is pinned to
     `claude-haiku-5-5` (manager, scribe) and `claude-sonnet-5-5` (advisor). If Claude Code rejects a full model id in
     the frontmatter, change it to the alias `haiku` or `sonnet`. New agents load when the session restarts.
   - OpenCode: `mkdir -p <root>/<project>/.opencode/agents && cp <skill>/agents/opencode/*.md <root>/<project>/.opencode/agents/`, then replace
     `REPLACE_WITH_PROVIDER/MODEL` in each file with a model from your roster (`provider/model`). Check with
     `opencode agent list`. The `permission` and `mode` fields follow the 1.18 agent format and need a check on the
     first run.
6. **Dashboard:** start the local board (`swarm.py watch --serve 8765`, see below) so the user can watch from
   the first phase.

## Running a phase

1. **Planner:** write `specs/<phase>.md` from `templates/spec.md`. List the tasks, file ownership (one owner per
   file), the acceptance targets as numbers, the gates (pass/fail thresholds, the bake-off rubric, the winner rule),
   and which tasks get a bake-off. Keep it short and exact: every vague line costs a review round. Commit the gates
   before any run.
2. **Planner:** launch a phase manager with `templates/prompts/manager-launch.md`, filled in (its Merge, Advisor and
   Sensitive lines say what the manager may do). Under Claude Code, start the `swarm-manager` agent; under OpenCode,
   run it with `--agent swarm-manager`. It reads `references/runbook.md` and runs the whole phase.
3. **Planner:** wait. Don't poll the manager or re-check its work step by step. When the report arrives:
   - check the pushed commit and the test count (one command)
   - run `swarm.py cost`, plus `swarm.py claude` if Claude agents took part
   - read the report (`templates/report.md`): measured and inferred are listed apart, and the limits are named
   - relay the results to the user in a few lines
4. **Repeat for the next phase.** Measurement tools for later phases (benchmarks, balance scripts) can be built
   early, in parallel, as separate tasks.

## Phase lifecycle (the manager follows it, details in the runbook)

`tests first` (one test author) → `test review` (2 other models + consensus run) → `fix` (author's session)
→ `implement` (a bake-off of 2–3 models for core logic, a single author otherwise) → `review` (2 non-authors each)
→ `consensus` (third run; ranks bake-off entries, 0–100) → `fix` (winner's session) → `integrate + play-test`
→ `merge --no-ff` → `sync` records → push (if the user approved) → dashboard.

In a bake-off, read both diffs before picking a winner: hidden acceptance tests can pass wrong code.

## Dashboard

`assets/dashboard.html` shows the phases as an overlapping card stack, the task board, worker lanes, spend and
the activity feed, live or as a replay. It needs no service: `swarm.py watch --serve 8765` rewrites the page,
`meta.json` and `events.json` in `out/site/` every 30 seconds and serves that folder on this machine only
(127.0.0.1); the page polls the two files every 5 seconds. `swarm.py site` writes the folder once. `swarm.py
export` writes a single offline replay file to share, and a claude.ai Artifact board is also supported. See
`references/dashboard.md`.

Task cards and worker lanes also show what each working agent is doing. `watch` scans every in-flight
agent's own log each pass (Grok and OpenCode run logs, Claude subagent transcripts) and logs its latest
action. For Claude subagents to be found, start each one's description with `[<phase>:<task>]`.

## Scribe

The scribe keeps the board telling the story, not just the numbers. Its scripted half is the `watch` timer.
Its judgement half is a cheap agent that runs one pass of `templates/prompts/scribe.md` (filled in) every
few minutes while a phase runs. It reads `swarm.py agents`, writes a one-line summary per agent
(`swarm.py activity`), flags crashed, quiet or looping agents as feed notes, and logs finished Claude
subagent runs nobody logged (`swarm.py log-run`). It never touches code, files or task status.

- **Claude Code:** the `swarm-scribe` agent (Haiku 5.5) in the background with the filled-in prompt, relaunched
  every ~5 minutes by the phase manager between its own steps (or `/loop 5m`).
- **OpenCode or Grok:** a headless run of a cheap model with the prompt, from a timer (cron, a shell loop),
  launched from the project repo root (where `.opencode/agents/` lives) with `--agent swarm-scribe` and
  `--config <ops>/swarm.toml`. Its agent definition allows only the read and logging `swarm.py` subcommands.
- **No scribe agent:** `watch` alone still keeps the board live; cards show the latest action without a
  summary.

## Hard rules

- **Workers only ever run inside a worktree under `project.worktrees`.** `swarm.py run` enforces this and refuses
  the main checkout and anything in `project.forbidden`. Never call a worker CLI directly, and never bypass the
  check.
- **Launch every worker through `swarm.py run`.** That is what records time, cost, sessions and events.
- **Never run a worker with auto-approve outside its sandbox.** `--cwd` or `--dir` is not a sandbox. Keep
  `allow_unsandboxed = false`, and check `git status` on `main` after every run.
- **Every worker prompt names the files the worker may edit and says "Do not list or read anything outside the
  working directory."** A denied read makes some models quit silently.
- **Workers start from the current `main`.** A fresh worktree can start from an old commit: have each worker run
  `git merge --ff-only main` first, and run tests from the worktree root (an editable install points at the
  main checkout).
- **Workers fix their own code** in their own session (`--session`). Anything the orchestrator edits in the
  product is logged with `swarm.py note "ORCHESTRATOR FIX: ..."`.
- **Gates are fixed before a run.** Thresholds, the bake-off rubric and the winner rule are committed in the spec
  before any worker starts. A later change is a dated entry under "Amendments", with its direction (conservative or
  relaxed). A relaxed gate needs the user's approval.
- **Sensitive repos run only no-training models.** With `[project] sensitive = true`, `run` refuses any model that
  does not set `trains_on_prompts = false`. Don't work around it.
- **Pushing to a remote, publishing, and other outward actions need the user's approval.** Say in the manager
  launch prompt exactly what is pre-approved.
- **Never `pkill -f` a pattern:** it can kill the agent's own shell. Kill by pid.
- **Never run two `--session` continuations of the same session at once.** They hang.

## Running under another orchestrator

The skill is plain markdown plus `scripts/swarm.py`, so any agent that can run shell commands and read files can
orchestrate it.
- **OpenCode** (1.18 and later) loads it from `.opencode/skills/`, `.claude/skills/`, `.agents/skills/`,
  `~/.config/opencode/skills/`, and auto-loads `~/.claude/skills/`.
- **Grok or another CLI:** point it at this file and `references/runbook.md`.
- Map the roles to models: planner and manager = your strongest model (the manager as a subagent where the CLI
  has them); workers, reviewers, consensus and triage = the cheap models in `[[models]]`.
- Unchanged: `swarm.py`, the templates, the runbook, `screenshot.py`, the local dashboard and `swarm.py export`.
- Not available: `swarm.py claude`, which reads Claude Code transcripts, and the claude.ai Artifact board. Price
  the orchestrator's own session with its CLI (for example `opencode export`).
- The worktree and forbidden-dir guard only protects worker runs. Run the orchestrating agent with a permission
  profile that asks before shell commands and before touching folders outside the project.
