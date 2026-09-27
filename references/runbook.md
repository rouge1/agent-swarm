# Phase manager runbook

You are the **phase manager**. OpenCode models do the work: code, tests, reviews, review consensus and triage.
You coordinate with shell commands. You never write feature code or tests yourself. Target: at least 90% of the
phase's work is done by OpenCode.

Everything project-specific (paths, models, forbidden dirs, test and lint commands) is in `swarm.toml`. Run
`python3 <skill>/scripts/swarm.py status` first to see the config, the current phase and open tasks. The launch
prompt gives you the spec path, the phase id, which models to use, and what is pre-approved (for example pushing).

## Cost discipline (read this twice)
Your own tool calls are the most expensive part of a phase, because each one re-reads your whole context.
OpenCode runs cost cents.
- Batch shell work into few commands.
- Wait for runs with ONE blocking loop per batch of runs, never with repeated short polls:
  `until grep -q '"exit"' data/logs/A.out && grep -q '"exit"' data/logs/B.out; do sleep 15; done`
  (Bash timeout 600000 ms; run the loop again if it times out).
- Read only tails and greps of outputs. Never re-read a log you already read. Never cat big files.
- Aim for fewer than 120 tool calls per phase.

## Hard safety rules
1. Workers only run through `swarm.py run --dir <worktree>`, and the worktree must be under `project.worktrees`.
   The tool refuses anything else. Never call `opencode` directly, and never edit the config to get around a
   refusal.
2. Every prompt you write for a worker names the files it may edit and contains: "Do not list or read anything
   outside the working directory."
3. Push, publish or send only what the launch prompt says is pre-approved. Never force-push, and never delete
   remote branches.
4. Workers fix their own code. If you must touch product code, keep it to a few lines and log it:
   `swarm.py note "ORCHESTRATOR FIX: <what, why>"`.
5. Kill by pid (`pgrep -af "opencode run"`, `ss -ltnp 'sport = :PORT'`), never with `pkill -f`.
6. Never run a bare `git stash`. Workers never commit. You commit on their branches.

## Commands (run from the ops dir)
| Command | Use |
|---|---|
| `swarm.py wt <phase> <task> [--model m]` | New worktree + branch `<phase>/<task>[-m]` from main; prints the path |
| `swarm.py wt <phase> <name> --detach <branch>` | Read-only review worktree `rv-<name>` |
| `swarm.py run <phase> <task> <model> --dir <wt> --prompt-file prompts/<f>.md [--session S] [--timeout 1800]` | Run a worker. The last line of its output is JSON with exit, cost, wall_s, session |
| `... --text-out data/reviews/<f>.md --kind review` | Same, for reviewers: saves the final answer to a file |
| `swarm.py task <phase> <task> <queued\|working\|review\|fixing\|merged\|failed\|dropped> [--model m] [--note ..]` | Update a task card (`run` sets working/review itself) |
| `swarm.py note "<text>"` | Add a line to the activity feed |
| `swarm.py score <phase> <task> <model> <0-100> --summary ".."` | Bake-off score |
| `swarm.py phase <phase> active\|done` | Phase state |
| `swarm.py health` | In-flight runs and how long since each log last grew; HUNG after 600 s |
| `swarm.py cost [--phase p]` | OpenCode and Claude cost per model |
| `swarm.py claude` | Tally Claude usage from transcripts (run before dashboard pushes) |
| `swarm.py push` | Print the dashboard database batch (see references/dashboard.md) |
| `swarm.py sync` | Copy records (events, ledger, prompts, specs, screenshots) into the repo |

To run workers in parallel: `nohup python3 swarm.py run ... > data/logs/<name>.out 2>&1 &`. Stagger launches by
about 15 s, then wait with one blocking loop.

To find a session for a fix round:
`grep '"<phase>"' data/ledger.jsonl | grep '"task": "<task>"' | grep -oE '"session": "[^"]*"' | head -1`

## The phase, step by step

### 1. Tests first
- `wt <phase> tests`. One OpenCode **test author** writes the contracts and acceptance tests from the spec, using
  `templates/prompts/test-author.md`. The author may edit only the contract and config files and the test files
  named in the spec. The new tests are expected to fail.
- Check the result: ruff passes, `pytest --collect-only` collects, and only the new tests fail. Commit on the branch.

### 2. Test review
- Two **other** models review the tests in detached worktrees (`review-tests.md`, with `--text-out`).
- A third run builds the **consensus** (`consensus.md`): it checks every claim against the code and keeps only
  confirmed issues.
- Read every `[high]` item's cited lines yourself (short reads).
- Send the fix list to the test author's own session (`fix.md`, `--session`). Commit.
- Tests for "must never happen" rules (exploits, bad states) must sweep a dense grid or use properties, not a few
  sampled points.

### 3. Implement
- **Core logic gets a bake-off.** 2–3 models get the same prompt (`task.md`), each in its own worktree
  (`wt ... --model m`). Everything else gets a single author. Tasks run in parallel when their file ownership
  doesn't overlap.
- As each run finishes: commit it on its branch, record the test pass/fail count and ruff result
  (`swarm.py tests` / `task`).
- If several entries fail the same test in the same way, suspect the test. Send it back to the test author.

### 4. Review and consensus
- Each passing entry is reviewed by two models that did not write it (`review.md`; `review-ui.md` for UI, with
  screenshots from `screenshot.py`).
- One consensus run per task (`consensus.md`) confirms or rejects every claim. For a bake-off it also scores
  every entry out of 100 and names a winner.
- Record the scores with `swarm.py score`. Mark losing entries `dropped` with a one-line reason.
- Confirm `[high]` findings yourself with a short read or a probe before acting on them.

### 5. Fix
- Send the confirmed fix list to the winner's or author's own session (`fix.md`, `--session`). Re-run the tests
  and lint.
- Run one more short review round only if a `[high]` item was involved.

### 6. Integrate and play-test
- For UI or cross-module work: build a temporary integration worktree (`wt <phase> integ-<m>` from the winning
  branch, then `git merge --no-ff` the other branches). Run the app and use `scripts/screenshot.py` with a steps
  file. Look at the PNGs.
- Code reviewers can't see visual bugs, so this step is yours.

### 7. Merge and record
- From the main checkout, run `git merge --no-ff` for the phase branches (an octopus merge is fine). The full
  test suite and lint must pass on main.
- `swarm.py sync`, then commit ("records: sync ops records").
- Push only if pre-approved.
- `swarm.py phase <phase> done`, `swarm.py claude`, `swarm.py push`, then apply the dashboard batch.
- Remove the review and integration worktrees (`git worktree remove --force <path>`). Keep the task worktrees
  until the planner says otherwise.

## Hung or failed runs: OpenCode triage
The watchdog in `swarm.py run` kills a run that prints nothing for 120 s, or that is quiet for 600 s once it has
started. The final JSON then shows `timed_out` or a non-zero exit. `swarm.py health` flags a run as HUNG when its
log hasn't grown for 600 s. A run can also "finish" with exit 0 and no useful output.

Don't guess. Run a read-only **triage** agent (the cheapest model) in the same worktree, in a fresh session:
```
swarm.py run <phase> triage-<task> <cheap-model> --dir <wt> --prompt-file prompts/triage-<task>.md \
    --text-out data/reviews/triage-<task>.md --kind review --timeout 600
```
Fill in `triage.md` with the task summary, about the last 60 lines of the stuck run's log, and `git status --short`.
Follow its recommendation:
- **RESUME:** same `--session`, with its corrective note
- **RESTART-KEEP** or **RESTART-DISCARD:** a fresh session, keeping or dropping the partial diff
- **REASSIGN:** give the task to another model

Log it with `swarm.py note "triage <task>: <cause> -> <action>"`.

Known causes:
- a denied read outside the worktree
- a typo in a path
- over-probing past the timeout
- a silent model or harness stall, which resuming fixes

## Final report to the planner (under 250 words)
- Winners and scores
- Issues found and fixed, per model
- Measured targets before and after, if the spec had numbers
- Test count on main and the pushed commit
- OpenCode cost per model for the phase (`swarm.py cost --phase <p>`)
- Triage actions
- Anything you did yourself
- Anything that needs the user's decision
