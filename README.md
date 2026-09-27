# opencode-swarm (Claude Code skill)

> **Status (2026-09-27):** imported from the `opencode-swarm` skill used to build whichhf in `trythismodel`.
> It now drives Grok as a sandboxed worker next to OpenCode and Claude subagents. The plan for making it
> vendor-neutral (Claude Code, Grok or OpenCode as orchestrator), with a standalone local dashboard kept current
> by a separate scribe agent, is in [`docs/requirements.md`](docs/requirements.md).

Run a software project with a swarm of cheap OpenCode worker models, managed by Claude.
Start with `SKILL.md`.

Install (once per machine; available in every project):

    ln -s "$PWD" ~/.claude/skills/opencode-swarm      # or: cp -r . ~/.claude/skills/opencode-swarm

Or install per project: put the folder at `<project>/.claude/skills/opencode-swarm/` and commit it.
