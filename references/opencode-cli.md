# OpenCode CLI: usage and gotchas

Verified with OpenCode 1.18 using the OpenRouter provider.

| Command | Purpose |
|---|---|
| `opencode run --format json -m <provider/model> --dir <wt> --title <t> "<prompt>"` | One non-interactive run. `swarm.py run` wraps this |
| `opencode run -s <session-id> ...` | Continue a session with its context (fix rounds) |
| `opencode export <session-id> > file.json` | Full session with token counts and cost. **Redirect to a file**: piped stdout is cut off at about 64 KB |
| `opencode session list` | Sessions and their ids |
| `opencode db path` | Location of the session database |
| `opencode serve` + `run --attach <url>` | Keep a server warm to avoid a cold start on every run |

## Gotchas
- **Permissions:** the default `build` agent may have `"*": allow`, so it runs any shell command without asking.
  Folders outside the project trigger an `external_directory` prompt. In `run` mode that prompt is auto-rejected,
  and some models then quit silently with exit 0. Hence the prompt rule "Do not list or read anything outside the
  working directory", plus a triage run when output is empty.
- **`--dir` is not a sandbox.** It sets where the agent works; it doesn't lock it in. The guards are:
  - `swarm.py`'s path checks (`project.worktrees` and `project.forbidden`)
  - the prompt rule above
  - running `git status` on main after runs
- **Stdin:** start runs with stdin set to `/dev/null`. An inherited terminal can make a run hang.
- **Silent hangs:** runs sometimes print nothing for many minutes and never finish. This is especially likely
  when two `-s` continuations of one session run at once, so never do that. Use a two-stage watchdog: 120 s before
  the first output, and 600 s of silence after that. The long second limit exists because models can write a big
  file without printing anything.
- **Cost data:** take it from `opencode export` (per-message tokens and cost), not from the streamed JSON.
- **Model ids** are `provider/model`, for example `openrouter/deepseek/deepseek-v4.1-flash`. Check them with a
  one-line run before a phase.
- **Workers can't message Claude.** Claude starts every exchange and reads the output or the `--text-out` file.
- **OpenCode doesn't create worktrees.** `swarm.py wt` does.
