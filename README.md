# agent-swarm

Build a software project with a swarm of cheap coding agents. One strong model plans and coordinates, cheap
worker models write the code, tests and reviews in their own git worktrees, and deterministic checks (tests,
lint, benchmarks, browser screenshots) decide what gets merged. Every run is logged, so you can see time,
money, reruns and failures per model and per phase, live or as a replay.

It began as a Claude Code skill and has been used on two real builds:
- a five-phase browser game: 148 tests, 77 worker runs, $1.14 of worker cost
- a seven-phase Python CLI built by a crew of Claude subagents and Grok

## How it works

- **Phases.** The project is split into phases, each with a spec, acceptance tests written before the code,
  and a "done when" line.
- **Workers in worktrees.** Each task runs in its own git worktree, so workers can't touch each other's
  files or the main checkout.
- **Bake-offs.** Two models can build the same task against the same spec and tests; you keep the better one.
- **Cross-model review.** A model that didn't write the code reviews it read-only; a third run checks
  every review claim against the code before anyone acts on it.
- **Event log.** Every action is appended to `data/events.jsonl`, the single source of truth for the
  dashboard, cost reports and replay.

## Workers

`swarm.py run` launches a worker through a driver, chosen per model in `swarm.toml`:

| Driver | CLI | Notes |
|---|---|---|
| `opencode` (default) | [OpenCode](https://opencode.ai) | Any OpenCode `provider/model`; cost read from `opencode export` |
| `grok` | Grok CLI, headless | Refuses to run without a kernel sandbox profile; see `references/grok-cli.md` |
| `claude` | Claude Code subagents | Run by the orchestrator itself, not by `swarm.py run`; cost tallied from session transcripts |

## Requirements

- Python 3.11+ (the scripts use only the standard library)
- git
- At least one worker CLI: OpenCode and/or the Grok CLI
- An orchestrating agent that can run shell commands and read files. Claude Code is the tested one.
- Optional: Playwright, for `scripts/screenshot.py`

## Install

As a Claude Code skill, once per machine:

    git clone https://github.com/rouge1/agent-swarm.git
    ln -s "$PWD/agent-swarm" ~/.claude/skills/agent-swarm

Or per project: copy the folder to `<project>/.claude/skills/agent-swarm/` and commit it.

With another orchestrator, point it at `SKILL.md` and `references/runbook.md`. They are plain markdown
instructions, and everything else is `scripts/swarm.py`.

## Quick start

    # scaffold an ops folder (swarm.toml, data/, prompts/, specs/, out/) next to your project
    python3 scripts/swarm.py init --dir ../myapp-ops --name "My App" --repo /path/to/myapp
    # edit ../myapp-ops/swarm.toml: models, phases, forbidden dirs, prices
    python3 scripts/swarm.py --config ../myapp-ops/swarm.toml phase p0 active
    python3 scripts/swarm.py --config ../myapp-ops/swarm.toml status

The orchestrator does the rest by following `SKILL.md`. Start there, and read `references/lessons.md` before
a first run: it explains why the process looks the way it does and where the money goes.

## Dashboard

`assets/dashboard.html` is one self-contained page. It shows:
- the phases as an overlapping card stack
- a task board for the focused phase
- worker lanes, spend per model and phase, and the activity feed
- a replay player

It reads its data from whichever of these it finds first:
- **Replay:** `swarm.py export` writes a single offline HTML file with every event embedded, ready to share.
- **claude.ai Artifact** (optional), backed by the Artifact database.
- **Local:** `meta.json` and `events.json` in the same folder, polled every 5 seconds. Serve the folder on
  this machine only, for example `python3 -m http.server 8765 --bind 127.0.0.1`.

`references/dashboard.md` has the details.

## Layout

```
SKILL.md               the orchestrator's instructions (start here)
swarm.toml.example     config template: paths, forbidden dirs, models and drivers, phases, prices
examples/              a filled-in config (paths are placeholders)
scripts/swarm.py       ops CLI: init, wt, run, task, note, review, tests, score, crew, phase,
                       health, cost, status, claude, push, export, sync, recover
scripts/screenshot.py  Playwright screenshots, video and console errors from a JSON step list
scripts/tests/         driver tests (a fake Grok CLI stands in for the real one)
assets/dashboard.html  live dashboard and replay page
references/            runbook, lessons, CLI notes (OpenCode, Grok), dashboard
templates/             AGENTS.md worker rules, phase spec, prompt templates
docs/requirements.md   where the project is heading
```

## Status

Working, and still being generalised. The goal is to run under any orchestrator (Claude Code, Grok or
OpenCode), with the local dashboard kept current by a separate "scribe" agent. See
[`docs/requirements.md`](docs/requirements.md).

Run the tests with:

    python3 -m pytest -q scripts/tests
