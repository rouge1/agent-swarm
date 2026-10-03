# Agent tracking: what we know about each agent, and where it comes from

This skill's job is to know **what each agent is doing and where it is**. Whether the work is right is the
orchestrator's job (tests, reviews, reading diffs) and is out of scope here. A status that is up to about 35
seconds old is fine: `watch` rewrites the board every 30 s and the page polls every 5 s.

## What "tracked" means

Every tracked agent is identified by `(phase, task, model)` and has a status. For each one we can answer:

| Question | Answer |
|---|---|
| Who is it? | phase, task, model key, runtime (`opencode`, `grok` or `claude`) |
| Is it running? | `working` or `fixing` is active. A run launched by `swarm.py run` also has a pid we can probe. |
| What is it doing right now? | its latest tool call, or "writing its answer" once answer text follows it |
| Is it moving? | seconds since its log or transcript last grew (`idle_s`); the board calls out an agent that is `quiet` or `crashed` |
| Is it waiting rather than stuck? | a Claude subagent holding for its own background job shows `waiting: <job>` |
| Did it finish? | a `run_end` event (launched runs), or a final answer with nothing pending (Claude subagents) |
| Where is it? | the worktree it was launched in (`dir`) and that worktree's branch, its log or transcript path, its pid |

## Where each answer comes from

Nothing reports to `swarm.py` itself: workers never call it. Everything is either written by `swarm.py run`
around the worker, or read from files the agent's own runtime already writes.

| Runtime | Launched by | Source of "what it's doing" | Source of "is it alive / finished" |
|---|---|---|---|
| OpenCode | `swarm.py run` | the run log `<ops>/data/logs/<phase>-<task>-<model>-<ms>.jsonl` (`tool` and `text` parts) | `run_start` records the pid; a `run_end` event closes the run |
| Grok | `swarm.py run` | the same run log (`streaming-json`: `tool_call` and `text` events) | same |
| Claude subagent | the orchestrator (Agent tool) | its transcript `<session_dir>/<session>/subagents/agent-*.jsonl` | the transcript: last message time, a final `end_turn` answer, pending background jobs |
| Claude orchestrator | the user | not read for actions, only tallied; a task it holds shows as "Building <task>" | `crew` events, or a transcript tally that changed in the last 5 minutes |

Details that decide whether an agent is found:

- **Launched runs** (`opencode`, `grok`) are in flight from their `run_start` event until their `run_end`,
  while the log file exists. The log's last write time is `idle_s`. Only the last 256 KB of a log is read.
- **Claude subagents** are found only if their description starts with `[<phase>:<task>]`, or
  `[<phase>:<task>:<model>]` in a bake-off. A tagged subagent is tracked only while exactly one task matching
  the tag is `working` or `fixing`: if none, or several without a model in the tag, it is skipped.
- **Claude orchestrator: work or orchestration.** What tells them apart is whether it holds a task. A task it
  does itself is `swarm.py task <phase> <task> working --model orchestrator`: its lane reads "Building <task>",
  and what it spends while that task is `working` or `fixing` is counted as its own work (a share of its total,
  never extra). With no such task it is orchestrating: its lane reads "Orchestrating" while the last `crew`
  event says it is busy or its transcript tally changed in the last 5 minutes. Under another orchestrator, post
  `swarm.py crew orchestrator busy|idle "<what>"` yourself; its work tasks are ordinary `task` events, and the
  spend split needs a Claude transcript, so it only applies to a Claude orchestrator.
- **Waiting.** A Claude subagent whose last tool call is a background job (`run_in_background`, or a
  `Monitor` wait) with no completion notification yet is `waiting`, and is not counted as finished.

## How it reaches the board

1. State changes are events appended to `<ops>/data/events.jsonl` (file-locked, so concurrent writers are safe):
   - `swarm.py run` writes `task working` (or `fixing`), `run_start`, `run`, `run_end`, then `task review`
     (or `failed`).
   - The phase manager writes `phase`, `task`, `note` and `review` events through `swarm.py` commands.
   - `swarm.py log-run` records a Claude subagent run `swarm.py` did not launch.
2. Each `watch` pass runs the activity scan. For every in-flight agent whose latest action, or flag, changed
   since its last `activity` event, it logs a new `activity` event. Unchanged agents add nothing. The flag is:
   - `crashed`: the worker's pid is gone, its run was never closed with a `run_end`, and its log has been still for
     at least 60 s (so a run that is just being closed isn't flagged);
   - `quiet`: its log or transcript hasn't grown for 10 minutes and it isn't waiting on a background job
     (Claude subagents can only be `quiet`, as they have no pid);
   - empty, which clears it, when output resumes or the run ends.
3. `watch` rewrites `meta.json` and `events.json`; the page polls them and folds the events into its view.
4. The task card and the worker lane show the flag in red (`crashed · run lost for 5m 15s`) or amber (`quiet
   for 12m 05s`) above the "doing" lines. All of it clears when the task changes status. The board shows how
   long an action has been going once it passes two minutes.

## Asking directly (no lag)

| Command | Shows |
|---|---|
| `swarm.py agents [--json]` | every in-flight agent: runtime, status, `idle_s`, `pid`, `alive`, worktree `dir` and `branch`, `started`, recent actions, `waiting`, `ended`, `run_logged` |
| `swarm.py health` | runs started but not finished, with pid and idle time; `CRASHED` if the pid is gone, `HUNG` if idle over 600 s |
| `swarm.py status` | tasks of the current phase and their status, runs per model |
| `swarm.py scan` | run the activity scan once and say how many agents changed |

## Limits

- **The board's `quiet` and `crashed` are inferred, not reported.** `quiet` means no output for 10 minutes: a
  worker that is thinking, or in a long tool call that prints nothing, looks the same. The run watchdog kills a
  silent run before that (120 s with no output at all, 600 s once it has started), so `quiet` is mostly seen on
  Claude subagents.
- **Pid checks are local.** `alive` uses a signal-0 probe, so it only works for runs on this machine, and
  only for the worker process (not for `swarm.py run`).
- **A launched run whose bookkeeping died stays "working".** Its pid is dead and it has no `run_end`.
  `swarm.py recover <phase> <task> <model> <log>` rebuilds the ledger entry from the log.
- **The branch is recorded for launched runs only.** `run_start` carries the branch checked out in the worktree at
  launch (`<phase>/<task>[-<model>]` when made by `swarm.py wt`), and nothing for a detached review worktree.
  Claude subagents run wherever the orchestrator put them, so only their transcript path is known.
- **Own work is counted by time, not by content.** Every orchestrator message between a task's `working` and its
  next status counts as work, including a quick question it answers meanwhile. A task it forgets to close keeps
  counting, so close it with `review` or `merged`.
- **No file watching.** What an agent has changed is known after it finishes (`files_changed` on the `run`
  event) and not while it runs. During a run only its actions are visible.
- **Untagged Claude subagents are invisible** until someone logs them with `log-run`. The board marks their
  runs "not logged".
