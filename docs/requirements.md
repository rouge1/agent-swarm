# agent-swarm — requirements for the extracted skill

After whichhf ships, the `opencode-swarm` skill used here becomes its own project:

- **Name:** `agent-swarm`
- **Location:** its own git repo, `agent-swarm`
- **Source:** `.claude/skills/opencode-swarm/` plus what this build added in `.swarm/` (scribe, outcome tracking, board changes)

## Requirements (from you, 2026-09-27)

1. **Runs under any orchestrator.** The skill must work when the orchestrating agent is Claude Code, Grok, or OpenCode. Nothing may depend on one vendor’s tools.
2. **The website is standalone.** No claude.ai Artifact, no claude.ai database. A static page served locally (stdlib HTTP server or a plain file) that reads the event log and refreshes itself on a timer. The shareable replay stays a single self-contained HTML file.
3. **A separate agent keeps the website current.** A cheap “scribe” agent (any model, any CLI) reads the status of every other agent and writes events; the page only reads. The scribe’s scripted parts (activity scan, cost tally) run without an LLM so they can also run on a timer.

## Status

- 2 (standalone website): done. `swarm.py watch --serve 8765` writes and serves the board; `export` still
  writes the single-file replay.
- 3 (scribe): done. The scripted parts (`scan`, the Claude tally, `site`) run on the `watch` timer; the
  scribe agent (`templates/prompts/scribe.md`) adds summaries, trouble notes and unlogged Claude runs
  through `agents`, `activity`, `note` and `log-run`.
- Activity per runtime: Grok and OpenCode are read from the run logs of `swarm.py run` (every worker goes
  through it), so `grok sessions list` / `grok export` aren't needed; Claude subagents from their
  transcripts.

## Design notes from this build

### Workers: one driver per CLI
`swarm.py run` has one driver per CLI behind the same command (`driver` on each `[[models]]` entry; default `opencode`), so the ledger, watchdog, dashboard and cost log stay the same. OpenCode is run with `opencode run` and priced with `opencode export`; Grok is below:

| Driver | Launch | Result / cost | Fix round |
|---|---|---|---|
| `opencode` | `opencode run …` (as today) | `opencode export <ses_…>` | as today |
| `grok` | `grok -p <text> --cwd <worktree> --output-format streaming-json --always-approve --no-subagents --no-auto-update --max-turns 40 --sandbox <profile> < /dev/null` (`--prompt-file` instead of `-p` from 100 KB up), env `GROK_MEMORY=0` | stdout: one JSON event per line; the closing `end` event carries `sessionId`, `stopReason` (`end_turn` / `max_turn_requests`), `usage`, `total_cost_usd` (missing = not reported); exit 0 / 1 / 130 / 143 | same command with `--resume <sessionId>` (not `--session-id`, which creates a new session); one process per session at a time |
| `claude` | `claude -p` headless, or Agent-tool subagents when Claude orchestrates | transcript tally (`swarm.py claude`) | resume / SendMessage |

Grok reviewers: keep read tools only, e.g. `--tools "read_file,grep,list_dir" --disallowed-tools "search_replace,run_terminal_cmd,Agent"` (`--kind review`).

Grok research: `--kind research` is the reviewer's read-only set plus `web_search,web_fetch` and `GROK_WEB_FETCH=1`: it can search and read the web but cannot edit, run commands or spawn subagents. Read-only is enforced for Grok only; on an OpenCode model the kind is just a label.

Grok's `end` event has no file count (OpenCode's export does), so `files_changed` is the worktree's changed files after the run compared with a snapshot taken before it. A fix round doesn't re-count an earlier round's uncommitted work, and a committed change still counts. The field is left out when git can't read the directory.

### Safety
- `--cwd` is not a sandbox. Use the CLI’s own kernel sandbox where it has one (Grok: `--sandbox` with a custom profile extending `strict`, `restrict_network = true`, read-only grants for the Python env and the repo’s `.git`), plus the worktree path check in `swarm.py`, plus `git status` on main after every run.
- Web access needs no sandbox change. Grok's `web_search` and `web_fetch` run inside the Grok process, so `restrict_network = true` (which blocks only the commands a worker runs, e.g. `curl`) leaves them working. Don't loosen the profile to give a worker the web; use a research run for lookups with nothing to write. Page content is untrusted text.
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
- A headless Grok worker can't ask for access. A sandbox denial or a removed tool comes back to the model as a tool error (`Permission denied`, no such tool) and the model carries on; when asked to report, it said so plainly. `swarm.py` marks a run failed only on a non-zero exit, a timeout or `--max-turns`, so a worker that was blocked and finished cleanly is logged `ok`. Judge by the answer text, `files_changed` and the tests, not the outcome alone. Not yet tested: what a worker does when a denial surprises it (stops, works around it, or hands back partial work unmentioned).
- After a database version conflict, a plain resync prints nothing because the hash cache assumes the failed write landed: resync with `--all`.
- Tests should assert behaviour (rejected families), not curator-owned strings (labels), or parallel workers break each other on merge.
