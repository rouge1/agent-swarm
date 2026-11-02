# OpenCode with the swarm: setup, check and version log

The quick path from "OpenCode is installed" to "a worker ran through `swarm.py`", plus a checklist to repeat after
every OpenCode update. This page is meant to change often. The deeper material lives elsewhere and is not repeated:
roster entries, picking a model and failure triage are in `opencode-models.md`; CLI flags and gotchas are in
`references/opencode-cli.md`; the phase workflow is in `references/runbook.md`.

## Set up

1. **Install OpenCode** and configure a provider. The swarm uses `openrouter/...` ids (needs an OpenRouter key in
   OpenCode) and, if you have them, `opencode/...` ids.
2. **List what is available:** `opencode --version` and `opencode models | grep <name>`. The id you put in
   `swarm.toml` must appear in that list exactly.
3. **Add the model** to `[[models]]` in `swarm.toml` (see `swarm.toml.example`), with `[opencode] bin = "opencode"`:

   ```toml
   [[models]]
   key  = "muse"
   id   = "openrouter/meta/muse-spark-1.3-contributor"
   name = "Muse Spark 1.3 Contributor"
   trains_on_prompts = true   # until the provider's terms say otherwise
   ```

4. **Run workers only through `swarm.py`:**

   ```
   swarm.py wt <phase> <task> --model <key>
   swarm.py run <phase> <task> <key> --dir <worktree> --prompt-file prompts/<f>.md
   swarm.py task <phase> <task> working --model <key>
   ```

   The last output line is JSON: `exit`, `outcome`, `cost`, `wall_s`, `session`. Never call `opencode run` for a
   worker by hand: you lose the path checks, the watchdog, the ledger entry and the cost record.

## After an OpenCode update

Run these in order and stop at the first failure.

1. **Version:** `opencode --version`. Note it in the log below.
2. **Ids still exist:** `opencode models` lists every `id` in your `swarm.toml`. Ids move between updates (the Muse
   id changed twice in October 2026), and a wrong id fails only when a run starts.
3. **Direct smoke test**, from any scratch folder, with stdin closed:
   `opencode run -m <id> "Reply with the single word: ok" < /dev/null`
4. **Smoke test through `swarm.py`** in a throwaway repo and ops folder, never your real project. Make a folder with
   a one-commit git repo as the worktree, a minimal `swarm.toml` (one model, one phase, `worktrees` pointing at its
   parent) and run:

   ```
   swarm.py --config <scratch>/ops/swarm.toml run p0 smoke <key> --dir <scratch>/wt/t1 \
     --prompt "Create hello.txt containing exactly: muse ok. Do nothing else." --timeout 180
   ```

   Pass if: exit 0, `outcome: ok`, the file exists with that text, and `session`, `cost` and the token counts are
   filled in. A cost of 0 on a paid route, or empty tokens, means `opencode export` changed shape: read
   `references/opencode-cli.md` and `scripts/swarm.py`'s `session_metrics()`.
5. **Agent files:** `opencode agent list` shows `swarm-manager`, `swarm-scribe` and `swarm-research` if you use them.
   The `permission` and `mode` fields follow the agent format of the OpenCode version in use; recheck them after a
   major version.
6. **Update this page:** add a row to the version log, and fix any command or id above that changed.

## Version log

Add a row for every OpenCode version you check. Keep failures: they are the useful rows.

| Date | OpenCode | Check | Result |
|---|---|---|---|
| 2026-10 | 1.18 | Docs in `references/opencode-cli.md` | Verified with the OpenRouter provider |
| 2026-10-22 | 2.0 | Web research (`:online` ids, `webfetch`) | See `references/opencode-cli.md` |
| 2026-11-02 | 2.2.5 | Steps 1-4 with `openrouter/meta/muse-spark-1.3-contributor` | Pass. Listed by `opencode models`, replied "ok", `swarm.py run` exit 0 in 12 s, about $0.001 (not free), tokens and session recorded. `files_changed` read 0 although a new untracked file appeared; not investigated |

## Known changes between versions

- **Muse Contributor id:** `opencode/muse-spark-1.3-contributor-free` became
  `openrouter/meta/muse-spark-1.3-contributor`. Its price and training terms on the new route are unverified, so the
  "(free)" label in older docs may be out of date.
