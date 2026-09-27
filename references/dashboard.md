# Dashboard and replay

`assets/dashboard.html` shows everything that happened in the event log, top to bottom:
- header: project name, the clock, live/replay, and a theme disc that cycles Default (follows the system's
  light or dark setting), Slate, Reading Room and Walnut; the choice is remembered in the browser
- **phase stack**: overlapping cards, `[1 [2 [3] 4] 5]`. Every phase card carries the full detail (eyebrow,
  title, goal, "Done when", a stats row -- elapsed/runs/reruns/failed/spend -- and a **Spend ▸** chevron that
  expands a per-agent spend table for that phase). The focused phase sits in front in the centre; its neighbours
  are tucked behind it, completed to the left and future to the right (up to 2 per side on desktop, 1 on narrow
  screens), each one opaque, a little smaller and more veiled than the one in front of it, and pushed out just
  far enough to peek past it. Only the front card's Spend button is live. Navigate with the ‹ › buttons (hidden
  on narrow screens), `[`/`]` keys, trackpad/shift-wheel scroll, touch swipe, or by clicking (or Enter/Space on)
  a peeking card; a "Back to current phase" control appears once you've moved away. The bake-off scorecard
  appears inside a phase card only while that phase has scored tasks that aren't all merged/dropped yet -- once
  settled it disappears.
- **phase map**: every phase as a tiny numbered cell, coloured by status, with a window box outlining exactly the
  phases currently in the stack; click a cell to focus that phase, or drag the window along the map and
  the stack follows, card by card, with the cards sliding into place
- **task board** for the focused phase: queued, in progress, in review, done
- **worker lanes**: the orchestrator plus one lane per model, with live "doing"/idle status and that lane's
  totals for the focused phase
- **spend**: the all-phases table (non-Claude workers, then Claude orchestrator + subagents); per-phase spend
  lives in the wheel's chevron instead of a scope toggle here
- the activity feed
- a replay player: Space plays or pauses, 1/2/3 set the speed, ←/→ step, Home/End jump, `[`/`]` shift the wheel's
  focused phase, R restarts. During replay the focused phase follows the replayed current phase unless you've
  wheeled away (same "Back to current phase" rule as live mode).

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
Put three files in one folder and serve it on this machine only:
- `index.html`: the page, wrapped in its own `<!doctype html><head>` with `<meta charset="utf-8">` and a
  viewport meta (the page is written for the Artifact skeleton, which normally supplies both; without the
  charset every `·`, `‹` and `✓` turns to mojibake)
- `meta.json`: the same body `push` writes to `project/meta`
- `events.json`: the whole event log as one JSON array

Refresh the two JSON files on a timer (write to a temp file, then rename, so the page never reads half a file)
and serve with `python3 -m http.server <port> --bind 127.0.0.1 --directory <folder>`. The whichhf build does
this with a small scribe loop (activity scan, Claude tally, then those three files every 30 s).

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
