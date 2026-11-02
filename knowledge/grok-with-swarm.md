# Grok with the swarm: setup, check and version log

The quick path from "the Grok CLI is installed" to "a worker ran through `swarm.py`", plus a checklist to repeat after
every Grok update. This page is meant to change often. The deep material lives in `references/grok-cli.md` (launch
command, fields read back, sandbox and AppArmor details) and is not repeated; the phase workflow is in
`references/runbook.md`. The OpenCode twin of this page is `opencode-with-swarm.md`.

## Set up

1. **Install the Grok CLI** and check it: `grok --version` and `grok models`. `grok models` may print "You are not
   authenticated" while headless runs work fine, so judge by the smoke run, not by that message.
2. **Add the model and a `[grok]` section** to `swarm.toml`. `driver = "grok"` is what routes the model to Grok; the
   default driver is `opencode`:

   ```toml
   [[models]]
   key    = "grokw"
   id     = "grok-4.7-build-fast"     # an id from `grok models`
   name   = "Grok 4.7 Build Fast"
   driver = "grok"

   [grok]
   bin = "grok"
   max_turns = 40
   reasoning_effort = "low"           # the default effort can time out on big prompts
   sandbox = "swarm-worker"           # must exist, see the next step
   ```

3. **Define the sandbox profile** named in `[grok] sandbox`, in `~/.grok/sandbox.toml` or `.grok/sandbox.toml` in the
   project. `swarm.py` does not write it, and `swarm-worker` is not a built-in: until it exists Grok refuses to
   start. Extend `strict`, set `restrict_network = true`, and list under `read_only` the Python env the tests need
   and the main repo's `.git` (a worktree's `.git` is a file pointing into it). The full example is in
   `references/grok-cli.md`.
4. **Run workers only through `swarm.py`:**

   ```
   swarm.py wt <phase> <task> --model <key>
   swarm.py run <phase> <task> <key> --dir <worktree> --prompt-file prompts/<f>.md
   ```

   The last output line is JSON: `exit`, `outcome`, `cost`, `wall_s`, `session`, `files_changed`. Put `--config`
   after the subcommand when you pass it.

Differences from OpenCode that matter in practice:

- **Resume is `--resume`, never `-s`.** `swarm.py` handles this for `--session`; calling `grok -s` by hand starts a
  fresh, contextless session.
- **Cost comes from Grok's own `usage` events**, not from an export command. A `null` cost means unreported, never
  free.
- **Read-only is enforced for Grok only:** `--kind review` and `--kind research` restrict its tools. On an OpenCode
  model they are just labels.
- **Keep ops dirs and worktrees out of `/tmp`** in a real swarm: the `strict` profile grants write to `/tmp`, so
  sandbox tests there prove nothing. (Throwaway smoke tests below may live there.)

## After a Grok update

Run these in order and stop at the first failure.

1. **Version:** `grok --version`. Note it in the log below.
2. **Ids still exist:** every Grok `id` in `swarm.toml` appears in `grok models`.
3. **Sandbox profile still parses:** the first smoke run fails fast with a clear error if `swarm-worker` is missing
   or its keys changed. On Ubuntu with `kernel.apparmor_restrict_unprivileged_userns = 1`, see the bubblewrap
   section of `references/grok-cli.md` before adding a `deny` list.
4. **Smoke test through `swarm.py`** in a throwaway repo and ops folder, never your real project. Use a worktree with
   one commit, a minimal `swarm.toml` (one Grok model, one phase, `worktrees` at its parent) and run:

   ```
   swarm.py run p0 smoke <key> --dir <scratch>/wt/t1 --config <scratch>/ops/swarm.toml \
     --prompt "Create hello.txt containing exactly: grok ok. Do nothing else." --timeout 240
   ```

   Pass if: exit 0, `outcome: ok`, the file has that text, `files_changed` is 1, and `session`, `cost` and the token
   counts are filled in. A `null` cost or empty tokens means the `usage` or `end` event changed shape: read the
   "Fields read back" section of `references/grok-cli.md` and the Grok driver in `scripts/swarm.py`.
5. **Resume keeps context:** re-run with `--session <session from step 4>` and ask what file it created.
6. **The sandbox still confines it:** in the resumed session, ask it to `echo hi > $HOME/some-file` and
   `cat $HOME/.ssh/config`. Both must fail with `Permission denied` and leave no file. Use targets outside `/tmp`.
7. **Offline tests:** `python3 -m pytest scripts/tests/test_grok_driver.py -q` runs against the fake Grok stub and
   catches driver regressions without spending money. It does not prove the real CLI still behaves the same.
8. **Update this page:** add a row to the version log, and fix any command, flag or id above that changed.

## Version log

Add a row for every Grok version you check. Keep failures: they are the useful rows.

| Date | Grok CLI | Check | Result |
|---|---|---|---|
| 2026-10 | 1.0.46 | Setup and sandbox, run end to end (`references/grok-cli.md`) | Verified on Ubuntu with the AppArmor userns restriction |
| 2026-11-02 | 1.0.70 | Steps 1 and 4 with `grok-4.7-build-fast` | Pass. `swarm.py run` exit 0 in 16 s, `files_changed` 1, `hello.txt` correct, session and tokens recorded, cost $0.038. `grok models` printed "not authenticated" as expected. Steps 5-7 not run |

## Known changes between versions

- None recorded yet. Note here anything an update renamed, removed or made stricter (flags, event names, sandbox
  keys, model ids).
