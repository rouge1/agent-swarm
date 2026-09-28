# Dashboard and replay

`assets/dashboard.html` shows everything that happened in the event log, top to bottom:
- header: project name, the clock, live/replay, and a theme disc that cycles Default (follows the system's
  light or dark setting), Slate, Reading Room and Walnut; the choice is remembered in the browser
- **phase stack**: overlapping cards, `[1 [2 [3] 4] 5]`. Every phase card carries the full detail (eyebrow,
  title, goal, "Done when", a stats row -- elapsed/runs/reruns/failed/spend -- and a **Spend details ▸** button on
  the left that expands a per-agent spend table, the full width of the card, for that phase). The focused phase sits in front in the centre; its neighbours
  are tucked behind it, completed to the left and future to the right (up to 2 per side on desktop, 1 on narrow
  screens), each one opaque, a little smaller and more veiled than the one in front of it, and pushed out just
  far enough to peek past it. Card text is centred; both side edges of every card carry the phase number in a
  circle with the number spelled out below it, one letter per line, spaced out wide for a short word and
  closing up for a long one (filled for the current phase, green when
  done), so a card peeking from either side still says which phase it is (hidden on narrow screens, where
  nothing peeks). Only the front card shows its Spend details. Nothing snaps: cards slide
  into their new places over about 0.6 s (joining cards slide in from outside the stack, leaving ones slide
  out and fade), the Spend details table grows open and fades in, and the stack's height, and so the page
  below it, eases to the front card's height. Navigate with the large ‹ › buttons (hidden on
  narrow screens), `[`/`]` keys, trackpad/shift-wheel scroll, by dragging the cards left or right (mouse,
  pen or finger; they trail the pointer and the next phase slides to the front at each stretch of the drag), or by clicking (or
  Enter/Space on) a peeking card. The current phase's cell in the phase map pulses, so it is easy to find again (L or
  End goes back to live). The bake-off scorecard
  appears inside a phase card only while that phase has scored tasks that aren't all merged/dropped yet -- once
  settled it disappears.
- **phase map**: every phase as a tiny numbered cell, coloured by status (a `deferred` phase, left with its
  exit criterion not met yet, is amber with a dashed outline, and its card says "Deferred"), with a window box outlining exactly the
  phases currently in the stack; click a cell to focus that phase, or drag the window along the map and
  the stack follows, card by card, with the cards sliding into place
- **task board** for the focused phase: queued, in progress, in review, done
- **worker lanes**: the orchestrator plus one lane per model, with live "doing"/idle status and that lane's
  totals for the focused phase. The orchestrator lane is busy while a `crew orchestrator busy` event says so,
  or, for a Claude orchestrator, while its transcript tally changed in the last 5 minutes
- **spend**: the all-phases table (non-Claude workers, then Claude orchestrator + subagents); per-phase spend
  lives in the wheel's chevron instead of a scope toggle here
- the activity feed
- a replay player: Space plays or pauses, 1/2/3 set the speed, ←/→ step, Home/End jump, `[`/`]` shift the wheel's
  focused phase, R restarts. During replay the focused phase follows the replayed current phase unless you've
  moved away (L or End brings the focus back to the current phase).

**What each agent is doing.** A working task's card, and its worker's lane, show two lines: the scribe's
summary in plain words, and underneath it the agent's latest action (`now: bash · pytest -q`, with how long
it has been going once that passes two minutes). Both come from `activity` events: `swarm.py scan` (run by
every `watch` pass) reads the run log of each `swarm.py run` still going (Grok and OpenCode workers) and the
transcripts of Claude subagents whose description starts with `[<phase>:<task>]`, and logs the latest action
when it changes; the scribe adds the summary with `swarm.py activity`. Activity never appears in the feed,
and both lines clear when the task changes status.

Claude lanes (their spend comes from the `claude` transcript tally, not worker `run` events) are any
`meta.models` entry with `driver: "claude"` -- there's no hard-coded model key or price-key map. The orchestrator
lane's role line comes from an optional `[project] orchestrator` string in `swarm.toml` (falls back to a generic
"Orchestrator" label).

## Data model
`swarm.py` appends every action to `data/events.jsonl`. `swarm.py push` groups the events into documents: one
`project/meta` document (name, phases, models and the optional `orchestrator` string from `swarm.toml`, plus
other metadata) and one `timeline/<phase>` document per phase holding that phase's events. The page folds the
events into its view, and replay is the same fold played back in virtual time.

The page picks its data source at load, first one that works: an embedded replay (`window.__SWARM_EMBED__`), the
claude.ai `db` capability, or -- for a standalone deployment with no database -- same-origin polling of
`./meta.json` and `./events.json` every 5 seconds (silently skipped if those 404). Nothing else is fetched.

## Standalone live board (no claude.ai)

    python3 scripts/swarm.py watch --serve 8765     # then open http://127.0.0.1:8765/

`watch` does one pass every 30 seconds (`--every N`): the activity scan, the Claude transcript tally (skipped when
`claude.session_dir` doesn't exist, or with `--no-claude`), then the board folder, `<ops>/out/site/` by default
(`--out DIR`). `--serve PORT` also serves that folder from the same process, bound to 127.0.0.1 (`--bind` to
change it; keep it on this machine unless the user asks otherwise). Each pass re-reads `swarm.toml` if it changed, so new phases, models and roles reach the board
without a restart (an edit that doesn't parse yet keeps the last good config). A phase the events use but
the config doesn't list still gets a card at the end of the stack, titled by its id. `--once` does a single
pass and exits;
`swarm.py site` writes the folder without the tally.

The folder holds three files:
- `index.html`: the page, wrapped in its own `<!doctype html><head>` with `<meta charset="utf-8">` and a
  viewport meta (the page is written for the Artifact skeleton, which normally supplies both; without the
  charset every `·`, `‹` and `✓` turns to mojibake)
- `meta.json`: the same body `push` writes to `project/meta`
- `events.json`: the whole event log as one JSON array

Each file is written to a temp file and renamed, so the page never reads half a file. Without `--serve`, any
static server works: `python3 -m http.server 8765 --bind 127.0.0.1 --directory <ops>/out/site`.

## Live dashboard as a claude.ai Artifact (optional)
1. Publish `assets/dashboard.html` with the Artifact tool, with capabilities
   `{"db": {"rules": [{"path": "", "read": "view", "write": "admin"}]}}` (viewers can read; only the owner writes).
   Put the resulting URL in `swarm.toml` under `[dashboard] url`.
2. At milestones, run `swarm.py push`. It prints a JSON list of batch writes with `if_version` values tracked in
   `out/.versions.json`. Apply the list with the ArtifactData tool (action `batch`, the dashboard URL, and the
   list as `writes`).
3. On a version conflict: `swarm.py push --resync timeline/<phase>=<current version>`, then push again.
   `--all` rewrites every document.

A db-backed artifact is private to its owner's organization, so friends outside it can't open it. For them, use
the replay.

## Shareable replay
`swarm.py export` embeds all events into a copy of the page as `out/<name>-replay.html`. It's a standalone file
that works offline and needs no database. Before sharing it:
- read the embedded notes for anything private (paths, emails, secrets)
- publish it as its own Artifact without the db capability, or just send the file
