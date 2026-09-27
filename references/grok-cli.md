# Grok CLI: usage and gotchas

Verified against `~/.grok/docs/user-guide/14-headless-mode.md`, `15-agent-mode.md`,
`18-sandbox.md` and `26-config-reference.md`. `swarm.py run` uses headless mode
(`grok -p ...` / `grok --prompt-file ...`), not `grok agent` (ACP) -- headless
is a single non-interactive turn that prints and exits, which is what a
worker run is.

## Launch command

For `driver = "grok"`, `swarm.py run` builds:

```
<grok.bin> [-p <text> | --prompt-file <tmp path>] --cwd <worktree> -m <model.id> \
  --output-format streaming-json --always-approve --no-subagents \
  --no-auto-update --max-turns <grok.max_turns> \
  [--reasoning-effort <grok.reasoning_effort>] \
  [--resume <session>] \
  [--disallowed-tools search_replace,run_terminal_cmd,Agent --tools read_file,grep,list_dir]  # kind == review only
  [--sandbox <grok.sandbox>]
```

Notes:
- **Prompt delivery has to survive the sandbox on grok's own process, not
  just swarm.py's.** `swarm.py` itself is never sandboxed -- it always reads
  the prompt text (from `--prompt` or `--prompt-file`) directly. What it
  hands to the *grok* process depends on size, because grok's process is the
  one running inside `[grok] sandbox`:
  - **Under 100 KB** (`GROK_PROMPT_INLINE_LIMIT`): passed inline as `-p
    <text>`. grok never opens a prompt file at all, so there is nothing for
    the sandbox to deny.
  - **100 KB or more**: written to a fresh file under `tempfile.gettempdir()`
    (`/tmp` on Linux -- a path every built-in profile, including `strict`,
    grants read access to) and passed as `--prompt-file <path>`. That temp
    copy is deleted in `finalize()` once the run ends, success or not.
  - **Either way**, a copy is also written to
    `<ops>/data/logs/prompt-<phase>-<task>-<ms>.md` for the historical
    record. That copy is *never* what grok reads -- early builds of this
    driver passed that ops-dir path straight to `--prompt-file` and it broke
    the first real run: `[grok] sandbox = "swarm-worker"` (`strict`) only
    grants grok's process read access to its own CWD (the worktree), system
    paths, and `~/.grok` -- not the ops dir, wherever that happens to live
    relative to the worktree -- so grok failed immediately with `Permission
    denied` trying to open its own prompt file. See "Sandbox" below.
  - The `--prompt-file` temp copy is never written inside the worktree
    either (`tempfile.gettempdir()` is independent of `--cwd`), so a
    profile's write grants for CWD are irrelevant to it.
- **`--resume`, never `-s`.** `-s/--session-id` *creates a new session* (it
  errors if the id already exists); it does not resume one. Fix rounds must
  use `-r/--resume` (`swarm.py` passes it as `--resume`, the long form, since
  `-s` on this CLI means something else entirely and using it by mistake
  silently starts a fresh, contextless session instead of continuing the
  worker's prior one).
- **`--always-approve`** (alias `--yolo`) is required for a non-interactive
  run: without it, tool calls block on an interactive permission prompt that
  never comes. Deny rules and hooks still apply on top of it.
- **`--no-subagents`** keeps a worker from spawning its own subagents mid-run
  (matches the swarm's own orchestration model: Claude fans workers out,
  workers don't fan out further).
- **`--output-format streaming-json` matters for the watchdog.** It emits one
  JSON line per event (`thought`, `tool_call`, `text`, `usage`, `end`, ...) as
  the run progresses, so the run log's mtime keeps advancing and the
  stall watchdog (`swarm.py run --stall`/`--stall-active`) sees real activity
  instead of going quiet for the whole run and firing a false stall-kill.
  `--output-format json` (single object at the end) would look identical to a
  hang until the process exits.
- **Reviewer tool restrictions** (`kind == "review"`): `--disallowed-tools
  search_replace,run_terminal_cmd,Agent --tools read_file,grep,list_dir`
  makes a review run read-only -- it can read and search files but cannot
  edit them, run shell commands, or spawn subagents. This mirrors the
  OpenCode review runs' intent (a reviewer should not be able to "fix" what
  it's reviewing) using Grok's own flag names (`read_file`/`grep`/`list_dir`
  are Grok's internal tool ids, not shell command names).
- **Env on this `Popen` only:** `GROK_MEMORY=0` (disable cross-session
  memory -- a worker run shouldn't read or write memory from unrelated
  sessions) and `GROK_DISABLE_AUTOUPDATER=1` (belt-and-suspenders alongside
  `--no-auto-update`, since the SDKs already inject this for non-leader
  agents they spawn).

## Fields read back

`finalize()` parses the run log (one JSON object per line; stderr text mixed
into the same stream is skipped as non-JSON) with `grok_log()`:

- **`text`**: every `{"type":"text","data":...}` chunk concatenated in
  order -- the worker's streamed final answer. Used for `--text-out`.
- **`sessionId`**, **`stopReason`**, **`total_cost_usd`**, **`num_turns`**,
  **`usage`** (`input_tokens`, `output_tokens`, `reasoning_tokens`,
  `cache_read_input_tokens`) all come from the terminal `{"type":"end",...}`
  event, which is always the last line when the run completes a turn.
- A run the watchdog killed (timeout) has no `end` event at all (see the
  real captured log in this repo's test fixtures) -- `grok_log()` returns
  `None`/`0` defaults for everything, and `finalize()` reports it as
  `outcome: "timeout"` regardless.
- **`total_cost_usd` is not always present.** The docs are explicit that it
  "appears only when the server reported a complete cost" and its "absence
  means unreported or incomplete, never free." `swarm.py` treats a missing
  cost as `cost: null` plus `cost_unknown: true` on the ledger/run record --
  never a silent `0`, which would under-report spend.
- **`stopReason: "max_turn_requests"`** means the run hit `--max-turns`
  without finishing. `swarm.py` records the task as `failed` even when the
  process exit code is `0` (Grok exits cleanly when it hits the cap; a clean
  exit does not mean the task finished), and stamps the run event with
  `"outcome": "error", "reason": "hit --max-turns"` so the dashboard board
  and feed show it as a failure with why, not a plain success.
- `session_metrics()`/the `ses_...` sessionID regex (both OpenCode-specific)
  are not used for this driver -- `sessionId` comes straight off the `end`
  event.

## Sandbox: required, and why `--cwd` is not one

`--cwd` only sets the working directory Grok starts in and prints paths
relative to. It is **not a filesystem boundary** -- nothing stops a tool
call from reading or writing outside it, exactly like OpenCode's `--dir`
(see `opencode-cli.md`'s "`--dir` is not a sandbox" note). The only real
confinement is `--sandbox <profile>`, a kernel-enforced filesystem/network
restriction (Landlock on Linux, Seatbelt on macOS) applied to the whole
process for its lifetime.

`swarm.py run` therefore **refuses to launch a Grok worker with no sandbox
profile configured**: if `[grok] sandbox` is empty and `[grok]
allow_unsandboxed` is not `true`, it exits with `grok driver needs a sandbox
profile; set [grok] sandbox or allow_unsandboxed = true` before ever
spawning the process. `[grok] sandbox` defaults to `"swarm-worker"`; that
profile name has to actually exist as `[profiles.swarm-worker]` in
`~/.grok/sandbox.toml` or `.grok/sandbox.toml` (Grok's built-in profiles are
`workspace`, `devbox`, `read-only`, `strict` -- anything else must be a
custom profile you define; `swarm.py` does not write `sandbox.toml` itself).

### The sandbox confines grok's own process -- not swarm.py's

`swarm.py` is the parent process; it is never sandboxed, and can read and
write anywhere it's allowed to on the host. The sandbox profile applies only
to the *grok* process `swarm.py` spawns, for that process's whole lifetime.
That means **every file grok itself has to open -- not just write, open at
all, including for reading -- has to fall under a path the profile grants
it**: its own CWD (the worktree it was launched with `--cwd`), certain
system paths, `~/.grok`, and, for the built-in profiles, `/tmp`/`/var/tmp`.
Nothing else, including the swarm ops directory (`data/`, `out/`,
`prompts/`, `specs/`), which typically lives next to the repo rather than
inside any worktree.

This is exactly why `--prompt-file` can't simply point at
`<ops>/data/logs/prompt-....md` the way OpenCode's `--dir` scheme might
suggest: `swarm.py` can write that file fine (it isn't sandboxed), but the
sandboxed grok process then fails immediately trying to *read* it --
`Failed to read '.../data/logs/prompt-....md': Permission denied (os error
13)` -- because the ops dir isn't under the profile's granted read paths.
The fix (see "Launch command" above) is to never ask grok to read anything
outside its granted paths in the first place: pass small prompts inline
(`-p`, no file to open at all) and put any prompt-file fallback under
`tempfile.gettempdir()` (`/tmp`), which every built-in profile can read.
The same rule applies to anything else you might be tempted to add to a
Grok worker's argv later -- an extra `--rules` file, a `--agent-profile`
path, and so on all need to resolve under CWD, `/tmp`, or `~/.grok`, or the
sandbox will refuse the read the same way.

Once a session is started under a profile, that profile is fixed for the
life of the session -- resuming it (`--resume`, which `swarm.py` uses for
fix rounds) restores the same profile automatically and refuses a
`--sandbox` value that differs from the one the session started with. This
is a safety feature, not a `swarm.py` limitation: keep `[grok] sandbox`
stable across a phase.

### Ubuntu + bubblewrap + AppArmor

On Linux, a **custom** sandbox profile (anything other than the four
built-ins) that carries a `deny` list needs `bubblewrap` (`bwrap`) to
kernel-enforce those denials; without it, Grok refuses to start rather than
run with the denied paths silently exposed. On Ubuntu 23.10+ (and any distro
that ships the same hardening), the sysctl
`kernel.apparmor_restrict_unprivileged_userns = 1` blocks bubblewrap's use
of unprivileged user namespaces *unless* an AppArmor profile explicitly
permits it for the `bwrap` binary. Concretely: `bwrap` can be installed and
on `PATH` and still fail to create its sandbox namespace on a stock Ubuntu
box, with an error that looks like a permissions problem rather than a
missing dependency. Before relying on `--sandbox <profile>` in CI or on a
fresh Ubuntu worker box, confirm one of:

- an AppArmor profile allowing `bwrap` unprivileged user namespaces is
  installed (this is what desktop Ubuntu ships for Flatpak/`bwrap`-based
  sandboxes; a minimal server image usually does not), or
- `kernel.apparmor_restrict_unprivileged_userns` is `0` (loosening a kernel
  hardening control -- a deliberate, host-level decision, not something
  `swarm.py` should do for you), or
- the profile in use has no `deny` list (built-in profiles and custom
  profiles without `deny` fall back to Landlock alone on Linux, which does
  not need bubblewrap).

`grok inspect` on the target machine shows which sandbox mechanism actually
applied; a silent fall-through to "no enforcement" is exactly the failure
mode the sandbox requirement above exists to catch, so treat a sandboxed
Grok run that reports no enforcement as a configuration bug, not a pass.

## Stub for tests

Never invoke the real `grok` binary from a test. Point `[grok] bin` at a
stub script (see `scripts/tests/fake_grok.py`) that:

- parses the argv it receives and writes it somewhere the test can read it
  back (this repo's stub uses an env var naming a capture file, since argv
  inspection is exactly what the driver's tests need to assert on); when a
  `--prompt-file` was passed, it also reads that file's content into the
  capture immediately, since `finalize()` deletes the real temp copy before
  a test gets to look at it,
- prints a few `streaming-json` lines (`thought`, `text` chunks, then an
  `end` event with `sessionId`/`stopReason`/`usage`/`total_cost_usd`), and
- exits -- with its behavior (normal completion, missing cost, hit
  `--max-turns`, or hang forever for the watchdog) switchable through an
  environment variable, so one stub script covers every finalize path
  without touching the network or a real model.
