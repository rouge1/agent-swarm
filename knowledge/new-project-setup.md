# Loading the skill into a new project

How to move this skill between projects without dragging one project's settings into the next. The step-by-step
setup the orchestrator follows is "Setup" in `SKILL.md`; this page explains where each piece lives and the
checklist for a new project.

## Three layers: what is shared and what is not

| Layer | Lives in | Shared across projects? | Holds |
|---|---|---|---|
| **The skill** | one clone of this repo | Yes, never edited per project | `SKILL.md`, `scripts/swarm.py`, `references/`, `templates/`, `agents/`, `assets/` |
| **The machine** | your home directory | Yes, so treat it as shared state | OpenCode and Grok installs and logins, `~/.grok/sandbox.toml`, `~/.config/opencode/` |
| **The project** | the repo, its worktree folder and its ops folder | No | `swarm.toml`, `data/`, `specs/`, `prompts/`, the copied agent files, `AGENTS.md`, the Grok sandbox profile |

The rule: **nothing project-specific goes in the skill, and nothing in the machine layer is named after one
project's paths.** A shared name that hides one project's paths is how bluey's runs ended up on a sandbox profile
listing the `trythismodel` env: the profile was called `swarm-worker`, written for one project, and every later
project that used the name inherited it.

## Install the skill once

```
git clone https://github.com/rouge1/agent-swarm.git
ln -s "$PWD/agent-swarm" ~/.claude/skills/agent-swarm
```

- **Claude Code:** the symlink above. OpenCode also auto-loads `~/.claude/skills/`.
- **A pinned copy per project:** copy the folder to `<project>/.claude/skills/agent-swarm/` and commit it. Do this
  when a project must not change under you; the cost is that updates reach it only when you copy them in.
- **Any other orchestrator:** point it at `SKILL.md` and `references/runbook.md`.
- **Update:** `git pull` in the clone. Every symlinked project gets it at once, so run the offline tests first:
  `python3 -m unittest discover -s scripts/tests`.

## Checklist for a new project

1. **Repo:** `git init -b main` (or use the existing one) and make at least one commit, so worktrees can branch.
2. **Folders** next to it, outside `/tmp`: `<name>` (repo), `<name>-wt` (worktrees), `<name>-ops` (ops).
3. **Ops folder.** One command does steps 3, 5 and 6; add only the flags you need:

   ```
   python3 <skill>/scripts/swarm.py init --dir <name>-ops --name "<Name>" --repo <abs path to repo> \
     --grok-env <abs path to the project's Python env> \
     --agents claude --agents opencode --opencode-model <provider/model>
   ```

   - `--worktrees` is optional; it defaults to `<repo>-wt`.
   - `--grok-env` (repeatable) creates the per-project Grok profile, see step 5.
   - `--agents` copies the manager, scribe and advisor into the repo, see step 6.
   - It prints a `note:` line for anything it did to files outside the ops folder, or could not check.
4. **Edit `swarm.toml`:**
   - `project.worktrees`: the `-wt` folder. Workers may only run under it.
   - `project.forbidden`: every folder that must never host a worker, including `~/.ssh` and other projects' main
     checkouts.
   - `project.test_cmd` and `project.lint_cmd`: absolute paths to this project's own environment.
   - `[[models]]`: 2-4 models with their `driver`. Check each id first (`opencode models`, `grok models`). Set
     `trains_on_prompts` and, for private code, `[project] sensitive = true`. See `opencode-with-swarm.md` and
     `grok-with-swarm.md`.
5. **Grok sandbox profile, one per project** (skip if you use no Grok model). `init --grok-env` does this: it adds
   `[profiles.<project-slug>-worker]` to `~/.grok/sandbox.toml`, never touches an existing profile, and sets
   `[grok] sandbox` in `swarm.toml`. By hand, in `~/.grok/sandbox.toml`, add a
   profile named for the project (`<name>-worker`, and `<name>-review` if reviewers need different reads):
   extend `strict`, `restrict_network = true`, and under `read_only` list this project's Python env and the main
   repo's `.git`. Then set `[grok] sandbox = "<name>-worker"` in the project's `swarm.toml`. Never reuse another
   project's profile, and never edit a profile another swarm is using.
6. **Agent files** (Claude Code and OpenCode). `init --agents` copies them (never overwriting) and fills in the
   OpenCode model; the `external_directory` allowances stay manual. By hand: copy `agents/claude/*.md` to `<repo>/.claude/agents/`, and
   `agents/opencode/*.md` to `<repo>/.opencode/agents/`. In the OpenCode copies, replace
   `REPLACE_WITH_PROVIDER/MODEL` and fill in the `external_directory` allowances with this project's ops, skill and
   worktree paths. These copies are project files: commit them to the project, not to the skill.
7. **Phase 0:** `AGENTS.md` from `templates/AGENTS.md`, `CONTRACTS.md`, first acceptance tests, committed on `main`.
8. **Smoke test each worker CLI** using the "After an update" steps in the matching `*-with-swarm.md`, in a scratch
   repo, before the first real phase.
9. **Start the board:** `swarm.py --config <ops>/swarm.toml watch --serve 8765`.

## Moving to another machine

Clone the skill, reinstall and log in to OpenCode and Grok, recreate `~/.grok/sandbox.toml` profiles with that
machine's paths, and re-run step 8. The ops folder is not versioned, so copy it across if you want the history.
`swarm.py sync` already copies events, ledger, prompts and specs into the repo under `records_dir`, which is the
durable record.

## Known gaps

- `init` fills in the model for the OpenCode agents but not their `external_directory` allowances for the ops dir,
  the skill folder and `project.worktrees`; it prints a reminder.
- `swarm.py run` now warns on stderr when a Grok sandbox profile is not defined or lists a path that does not exist.
  It does not check that the listed paths are the *right* ones for the project, so a profile copied from another
  project with paths that happen to exist still passes.
- `swarm.toml.example` still names the profile `swarm-worker`. `init --grok-env` replaces it; without that flag,
  set it yourself.
