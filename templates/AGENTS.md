# Rules for worker agents

You are a worker on **<PROJECT>**, <one line: what it is and the stack>.
A manager gives you one task at a time, and a reviewer from a different model reads every line you write.

## Hard rules
1. **Edit only the files your task names.** Create new files only where your task says so.
2. **Never edit** tests, `CONTRACTS.md`, `AGENTS.md`, `<config / protocol files owned by the planner>` or
   packaging files, unless your task explicitly says so. If you think one of them is wrong, say so in your final
   message instead.
3. **Stay inside the current working directory.** Do not list, read or write anything outside it, except running
   the tools listed below.
4. **Do not run git commands that change state** (no commit, branch, checkout, reset, stash).
5. **No new dependencies.** Available: <runtime + libraries>. <Front-end rule, for example "plain JS, no build
   step, no CDN">.
6. Do not start long-running servers. Do not use `sudo`.

## Checking your work
    <TEST_CMD>
    <LINT_CMD>
Your task is done when the tests named in the task pass and lint reports no errors.

## Style
- Match the surrounding code. <Language conventions: type hints, docstrings, …>
- <Performance rule, if any: "vectorise bulk work; no Python loops over N items".>
- Keep public interfaces exactly as documented in the module docstrings and CONTRACTS.md.

## Finish
End with a short summary: files changed, what you did, the test result, and anything you could not do.
