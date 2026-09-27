# agent-swarm — requirements for the extracted skill

After whichhf ships, the `opencode-swarm` skill used here becomes its own project:

- **Name:** `agent-swarm`
- **Location:** its own git repo, `agent-swarm`
- **Source:** `.claude/skills/opencode-swarm/` plus what this build added in `.swarm/` (scribe, outcome tracking, board changes)

## Requirements (from you, 2026-09-27)

1. **Runs under any orchestrator.** The skill must work when the orchestrating agent is Claude Code, Grok, or OpenCode. Nothing may depend on one vendor’s tools.
2. **The website is standalone.** No claude.ai Artifact, no claude.ai database. A static page served locally (stdlib HTTP server or a plain file) that reads the event log and refreshes itself on a timer. The shareable replay stays a single self-contained HTML file.
3. **A separate agent keeps the website current.** A cheap “scribe” agent (any model, any CLI) reads the status of every other agent and writes events; the page only reads. The scribe’s scripted parts (activity scan, cost tally) run without an LLM so they can also run on a timer.

## Design notes from this build

### Workers: one driver per CLI
`swarm.py run` only knows OpenCode today (it execs `opencode run`, then prices the run with `opencode export`). Add a driver per CLI behind the same `run` command, so the ledger, watchdog, dashboard and cost log stay the same:

| Driver | Launch | Result / cost | Fix round |
|---|---|---|---|
| `opencode` | `opencode run …` (as today) | `opencode export <ses_…>` | as today |
| `grok` | `grok --prompt-file <task.md> --cwd <worktree> --output-format json --always-approve --no-subagents --no-auto-update --max-turns 40 --sandbox <profile> < /dev/null`, env `GROK_MEMORY=0` | stdout JSON: `text`, `sessionId`, `stopReason` (`end_turn` / `max_turn_requests`), `usage`, `total_cost_usd` (missing = not reported); exit 0 / 1 / 130 / 143 | same command with `--resume <sessionId>` (not `--session-id`, which creates a new session); one process per session at a time |
| `claude` | `claude -p` headless, or Agent-tool subagents when Claude orchestrates | transcript tally (`swarm.py claude`) | resume / SendMessage |

Grok reviewers: keep read tools only, e.g. `--tools "read_file,grep,list_dir" --disallowed-tools "search_replace,run_terminal_cmd,Agent"`.

### Safety
- `--cwd` is not a sandbox. Use the CLI’s own kernel sandbox where it has one (Grok: `--sandbox` with a custom profile extending `strict`, `restrict_network = true`, read-only grants for the Python env and the repo’s `.git`), plus the worktree path check in `swarm.py`, plus `git status` on main after every run.
- Under Claude Code, `grok --always-approve` is blocked by the auto-mode check until the user adds a permission rule. Allow a single wrapper script, not `grok` in general.

### Board
- Every run carries an `outcome` (`ok` / `rejected` / `timeout` / `error`) and a `reason`; the board shows runs, reruns and failures per agent, on task cards and in the feed. Reruns are a data point, not noise.
- Spend: external workers in the top table; Claude (orchestrator + subagents) in its own section, never both.
- Bake-off scorecard: list only scored tasks (the entries), not every task in the phase; log each entry’s review verdict as a `review` event so the Review column fills in.
- Hidden acceptance tests can pass wrong code (Grok’s entry passed 39/39 with a tracker bug): always read both diffs before picking a winner, and have the test author check behaviour, not just the presence of words.
- Activity scan per runtime: Claude subagent transcripts (`subagents/agent-*.jsonl` + `.meta.json` descriptions tagged `[phase:task]`), Grok sessions (`grok sessions list`, `grok export`), OpenCode sessions.

### Lessons (things that bit us)
- Agent-tool worktrees started from an old commit; every worker had to fast-forward to `main` itself. The orchestrator should check the base commit, or tell workers to `git merge --ff-only main` first.
- An editable install points at the main checkout, so in a worktree always run `python -m pytest` from the worktree root.
- With `--json-schema` plus `--permission-mode plan`, Grok may answer without reading the files it was told to read: inline the source material.
- Large prompts with default reasoning effort made Grok write the whole answer inside its reasoning and time out (15 min on `grok-4.7`, 10 min on `grok-4.7-build-fast`). Use `--reasoning-effort low` for bulk-generation tasks, and stream (`--output-format streaming-json`) to diagnose.
- After a database version conflict, a plain resync prints nothing because the hash cache assumes the failed write landed: resync with `--all`.
- Tests should assert behaviour (rejected families), not curator-owned strings (labels), or parallel workers break each other on merge.
