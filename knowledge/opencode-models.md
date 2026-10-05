# Using OpenCode models in the swarm

How to add an OpenCode model to the roster, run it, and pick the right one for the job. The CLI details are in
`references/opencode-cli.md`, the phase workflow is in `references/runbook.md`, and the bake-off numbers behind the
picks are in `references/lessons.md`. This page ties them together.

## The short version

1. Add the model to `[[models]]` in `swarm.toml`.
2. Make a worktree: `swarm.py wt <phase> <task> --model <key>`.
3. Run it: `swarm.py run <phase> <task> <key> --dir <worktree> --prompt-file prompts/<f>.md`.
4. Read the JSON on the last line of the output (exit, cost, wall_s, session), then review the diff.

Never call `opencode` directly. `swarm.py run` wraps it and adds the path checks, the watchdog, the ledger entry and
the cost record.

## 1. Put a model in the roster

Each model is one `[[models]]` entry in `swarm.toml`. The roster holds 1 to 6.

```toml
[[models]]
key  = "sol"                              # what you type: swarm.py run p1 task sol ...
id   = "openrouter/openai/gpt-6.1-sol"    # provider/model, passed to opencode -m
name = "GPT-6.1 Sol"                      # display name on the board and in reports
# driver = "opencode"                     # default; "grok" and "claude" are the other drivers
# role = "free tier: no sensitive data"   # optional text under the name on the dashboard lane
```

- **`id` is `provider/model`.** Two providers are in use: `openrouter/...` (OpenRouter, needs your OpenRouter key
  configured in OpenCode) and `opencode/...` (OpenCode's own hosted models, such as the free Muse).
- **Check the id before a phase** with a one-line run. A wrong id wastes a worktree and a watchdog timeout.
- **Optional `[opencode]` settings:** `bin` (the executable, or a stub for tests) and `agent` (passes `--agent <name>`
  on every run; unset uses OpenCode's default agent).
- **Only `driver = "opencode"` models are launched through OpenCode.** `grok` models use the Grok CLI
  (`references/grok-cli.md`), and `claude` is documentation only: those workers are Agent-tool subagents, and `run`
  refuses them.

## 2. Run a worker

```
swarm.py wt <phase> <task> --model <key>          # worktree + branch <phase>/<task>-<key>
swarm.py run <phase> <task> <key> --dir <wt> --prompt-file prompts/<f>.md
```

- **Reviewers and researchers** save their final answer to a file: add `--text-out data/reviews/<f>.md --kind review`.
- **Fix rounds** continue the same session with its context: add `--session <id>`. Find the id in the ledger:
  `grep '"<phase>"' data/ledger.jsonl | grep '"task": "<task>"' | grep -oE '"session": "[^"]*"' | head -1`.
  Never run two continuations of one session at once: it is the most common cause of a silent hang.
- **Parallel runs:** `nohup swarm.py run ... > data/logs/<name>.out 2>&1 &`, staggered by about 15 s. Wait with one
  blocking `until grep -q '"exit"' ...; do sleep 15; done` loop, not repeated polls.
- **Bake-off:** the same spec and tests to every model, each in its own worktree (`wt ... --model <key>`), then
  `swarm.py score <phase> <task> <key> <0-100> --summary ".."`.

Every worker prompt must name the files it may edit and contain: "Do not list or read anything outside the working
directory." Workers never commit; you commit on their branches. Workers can't message you: you start every exchange
and read the output or the `--text-out` file.

## 3. What keeps a run safe

`--dir` only sets where the agent works. It is not a sandbox, and OpenCode has full filesystem permissions inside it.
The guards are:

- `swarm.py` refuses a `--dir` outside `project.worktrees`, inside anything in `project.forbidden`, or equal to
  `project.repo` (the main checkout).
- The prompt rule above.
- `git status` on main after runs.

The watchdog (`[watchdog]` in `swarm.toml`) kills a run that prints nothing for 120 s (`stall`), or that goes quiet
for 600 s once it has started (`stall_active`), or that passes 1800 s overall (`timeout`). `swarm.py health` flags a
run as HUNG after 600 s without log growth.

## 4. Which model for which job

Results of the 2026-10 bake-offs (two small implementation tasks and one seeded-bug code review, one run each; see
`references/lessons.md` for the full tables and caveats):

| Model | `id` | Use it for | Notes |
|---|---|---|---|
| GPT-6.1 Sol | `openrouter/openai/gpt-6.1-sol` | The default worker | Clean on both tasks at about $0.10 and 80 s. Its `max` reasoning variant cost 2.4x for the same result |
| Muse Spark 1.3 Contributor (free) | `opencode/muse-spark-1.3-contributor-free` | Routine work and review on **public or throwaway code only** | Matched Sol at $0, but the provider may train on prompts and code. **Never** give it credentials, personal data, or private or client code |
| DeepSeek V4.1 Flash | `openrouter/deepseek/deepseek-v4.1-flash` | Cheapest worker (about $0.05), triage, tasks with a tight test net | Missed one narrow semver feature. Slowest range: 76-300 s |
| Claude Sonnet 5.5 (via OpenRouter) | | Review | 9 of 9 seeded bugs at $0.07 and 22 s. It labels nearly everything `[high]`, so ignore its severity |
| Claude Opus 5.5 (via OpenRouter) | | Planning, not generation | Correct but about 3x Sol's cost |
| Muse Spark 1.3 (paid) | | Avoid | About 6x Sol's cost and 8x its time. At `xhigh` it spent its whole output budget reasoning and wrote no code |

Rules of thumb:

- **Put generation on cheap models.** A run costs cents, so redundancy is affordable, and redundancy is where the
  quality comes from.
- **Review with models that did not write the code**, ideally from a different family, and take a consensus.
- **Passing the visible tests is not enough.** Every model passed them in the bake-offs, and fuzzing against a
  reference implementation still found real bugs.
- **Don't trust severity labels across models.**
- **Assign by track record, but the sample is small.** Test-writing and multi-file work are untested.

## 5. When a run goes wrong

Symptoms and first moves:

| Symptom | Likely cause | Move |
|---|---|---|
| Exit 0, no output or no useful change | A denied read outside the worktree: `run` mode auto-rejects the `external_directory` prompt and some models then quit silently | Triage run (below) |
| `timed_out` | A silent hang or a stall | Triage run |
| Empty or truncated `opencode export` | Piped stdout is cut off at about 64 KB | Redirect to a file: `opencode export <sid> > file.json` |
| Run hangs at start | Inherited terminal stdin | `swarm.py run` already uses `/dev/null`. Don't launch workers by hand |
| `reason: length` and no code | The reasoning effort used the whole output budget | Lower the effort, or pick another model |

**Triage:** run the cheapest model read-only in the same worktree with `templates/prompts/triage.md` filled in (task
summary, the last 60 or so log lines, `git status --short`), in a fresh session, with `--kind review --text-out`.
Then follow its verdict: `RESUME` (same `--session`, with a corrective note), `RESTART-KEEP`, `RESTART-DISCARD`, or
`REASSIGN` (another model). Log it: `swarm.py note "triage <task>: <cause> -> <action>"`.

Kill a stuck run by pid (`pgrep -af "opencode run"`), never with `pkill -f`.

## 6. Cost

Take cost from `swarm.py cost [--phase <p>]`, which reads `opencode export` (per-message tokens and cost), not the
streamed JSON. An OpenCode run costs 0.5-3¢ in a real phase. Coordination by the orchestrating Claude session costs
far more than the workers, so batch your own shell calls.

## Keeping this page current

The model table here is a snapshot of the `lessons.md` bake-offs and the roster in `swarm.toml.example`. When a
new bake-off changes a pick, update `references/lessons.md` first and then this page.
