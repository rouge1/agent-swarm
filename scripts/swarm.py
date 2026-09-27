#!/usr/bin/env python3
"""Swarm ops: run OpenCode workers, track time/tokens/cost, keep the event log.

Generalized, config-driven version of the project-specific swarm.py this skill
packages. Everything project-specific (repo paths, models, phases, prices)
lives in a TOML config (see swarm.toml.example); this script is stdlib-only
(Python 3.11+, tomllib) so it runs anywhere without a venv.

The event log (<ops dir>/data/events.jsonl) is the single source of truth.
The live dashboard and its replay are both folds over it; `push` exports it
as one timeline document per phase for the dashboard's database.

Config lookup: --config PATH (anywhere on the command line), else
$SWARM_CONFIG, else a file named `swarm.toml` in the current directory or any
parent. The ops dir (data/, out/, prompts/, specs/) defaults to the directory
that holds the resolved config file. `swarm.py init` scaffolds a fresh one.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent   # .../opencode-swarm/scripts -- fixed, part of the skill
SKILL_DIR = SCRIPT_DIR.parent                  # .../opencode-swarm -- assets/, templates/, swarm.toml.example

# Populated by load_config() before any command but `init` runs; predeclared
# here (with harmless defaults) so `init` and `--help` work without a config.
CFG: dict = {}
OPS = DATA = OUT = LOGS = EVENTS = LEDGER = None
MODELS: dict = {}
PHASES: list = []
WATCHDOG = {"stall": 120, "stall_active": 600, "timeout": 1800}
OPENCODE_BIN = "opencode"
OPENCODE_AGENT = None
GROK_BIN = "grok"
GROK_MAX_TURNS = 40
GROK_REASONING_EFFORT = None
GROK_SANDBOX = "swarm-worker"
GROK_ALLOW_UNSANDBOXED = False
SESSION_DIR = Path.home() / ".claude" / "projects"
CLAUDE_SESSION = ""
CLAUDE_PRICES: dict = {}
CLAUDE_NAMES: dict = {}
DASHBOARD_URL = ""


# ---------------------------------------------------------------- config


def _patch_toml_scalar(text: str, section: str, key: str, value: str) -> str:
    """Replace `key = "..."` inside `[section]` of a TOML source, keeping comments/formatting intact.

    tomllib is read-only (stdlib has no TOML writer), so `init` fills the
    example file in with a couple of textual substitutions instead of a
    parse/mutate/serialize round-trip that would drop every comment.
    """
    m = re.search(rf'(?ms)^\[{re.escape(section)}\](.*?)(?=^\[|\Z)', text)
    if not m:
        return text
    block = re.sub(rf'(?m)^{re.escape(key)} = ".*"$', f'{key} = "{value}"', m.group(0), count=1)
    return text[: m.start()] + block + text[m.end() :]


def extract_config_arg(argv: list[str]) -> tuple[str | None, list[str]]:
    """Pull `--config PATH` / `--config=PATH` out of argv wherever it appears.

    It's documented as a global flag usable before or after the subcommand,
    which argparse subparsers can't do on their own, so it's handled here.
    """
    cfg, out, i = None, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--config" and i + 1 < len(argv):
            cfg = argv[i + 1]
            i += 2
        elif a.startswith("--config="):
            cfg = a.split("=", 1)[1]
            i += 1
        else:
            out.append(a)
            i += 1
    return cfg, out


def find_config(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            sys.exit(f"--config {explicit}: no such file")
        return p
    env = os.environ.get("SWARM_CONFIG")
    if env:
        p = Path(env).expanduser().resolve()
        if not p.is_file():
            sys.exit(f"$SWARM_CONFIG={env}: no such file")
        return p
    here = Path.cwd()
    for cand in (here, *here.parents):
        p = cand / "swarm.toml"
        if p.is_file():
            return p
    sys.exit("no swarm.toml found: pass --config PATH, set $SWARM_CONFIG, or run `swarm.py init` here")


def load_config(path: Path) -> None:
    global CFG, OPS, DATA, OUT, LOGS, EVENTS, LEDGER, MODELS, PHASES, WATCHDOG
    global OPENCODE_BIN, OPENCODE_AGENT, SESSION_DIR, CLAUDE_SESSION, CLAUDE_PRICES, CLAUDE_NAMES, DASHBOARD_URL
    global GROK_BIN, GROK_MAX_TURNS, GROK_REASONING_EFFORT, GROK_SANDBOX, GROK_ALLOW_UNSANDBOXED

    CFG = tomllib.loads(path.read_text())
    OPS = path.parent
    DATA, OUT = OPS / "data", OPS / "out"
    LOGS, EVENTS, LEDGER = DATA / "logs", DATA / "events.jsonl", DATA / "ledger.jsonl"

    MODELS = {m["key"]: {"id": m["id"], "name": m["name"], "driver": m.get("driver", "opencode"),
                          "role": m.get("role", "")}
              for m in CFG.get("models", [])}
    PHASES = CFG.get("phases", [])

    wd = CFG.get("watchdog", {})
    WATCHDOG = {"stall": wd.get("stall", 120), "stall_active": wd.get("stall_active", 600),
                "timeout": wd.get("timeout", 1800)}

    oc = CFG.get("opencode", {})
    OPENCODE_BIN = oc.get("bin", "opencode")
    OPENCODE_AGENT = oc.get("agent")

    gk = CFG.get("grok", {})
    GROK_BIN = gk.get("bin", "grok")
    GROK_MAX_TURNS = gk.get("max_turns", 40)
    GROK_REASONING_EFFORT = gk.get("reasoning_effort")
    GROK_SANDBOX = gk.get("sandbox", "swarm-worker")
    GROK_ALLOW_UNSANDBOXED = gk.get("allow_unsandboxed", False)

    claude = CFG.get("claude", {})
    SESSION_DIR = Path(claude.get("session_dir", "~/.claude/projects")).expanduser()
    CLAUDE_SESSION = claude.get("session", "")
    CLAUDE_PRICES, CLAUDE_NAMES = {}, {}
    for key, pr in claude.get("prices", {}).items():
        CLAUDE_PRICES[key] = (pr["in"], pr["out"], pr["cache_read"], pr["cache_write_5m"], pr["cache_write_1h"])
        CLAUDE_NAMES[key] = pr.get("name", key)

    DASHBOARD_URL = CFG.get("dashboard", {}).get("url", "")


def build_meta() -> dict:
    """The one document swarm.py, push and export all agree describes the project."""
    meta = {"name": CFG["project"]["name"], "repo": CFG["project"].get("repo", ""),
            "phases": PHASES, "models": [{"key": k, **v} for k, v in MODELS.items()]}
    orchestrator = CFG["project"].get("orchestrator")
    if orchestrator:
        meta["orchestrator"] = orchestrator
    return meta


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "swarm"


# ---------------------------------------------------------------- safety


def _resolve_dir(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def check_worktree_safety(path: Path) -> Path:
    """Refuse anywhere OpenCode (full filesystem permissions) or a new worktree could land except a
    configured worktree: strictly inside project.worktrees, never project.repo or project.forbidden."""
    worktrees = Path(CFG["project"]["worktrees"]).expanduser().resolve()
    repo = Path(CFG["project"]["repo"]).expanduser().resolve()
    forbidden = [Path(f).expanduser().resolve() for f in CFG["project"].get("forbidden", [])]

    if path == worktrees or worktrees not in path.parents:
        sys.exit(f"refusing --dir {path}: must be strictly inside project.worktrees ({worktrees})")
    if path == repo:
        sys.exit(f"refusing --dir {path}: that is project.repo, the main checkout")
    for f in forbidden:
        if path == f or f in path.parents:
            sys.exit(f"refusing --dir {path}: inside forbidden path {f} (project.forbidden)")
    return path


def _git(args: list[str]) -> None:
    try:
        subprocess.run(["git", *args], check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"git {' '.join(args)} failed (exit {e.returncode})")


# ---------------------------------------------------------------- events


def now_ms() -> int:
    return int(time.time() * 1000)


def emit(ev: dict) -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    ev = {"t": now_ms(), **ev}
    with open(EVENTS, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(ev) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    return ev


def load_events() -> list[dict]:
    if not EVENTS.exists():
        return []
    return [json.loads(line) for line in EVENTS.read_text().splitlines() if line.strip()]


def load_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def current_phase(events: list[dict]) -> str:
    cur = PHASES[0]["id"] if PHASES else "p0"
    for e in events:
        if e["type"] == "phase" and e["status"] == "active":
            cur = e["phase"]
    return cur


# ---------------------------------------------------------------- run


def session_metrics(session_id: str, since_ms: int) -> dict:
    """Cost, tokens and model time for assistant messages created since `since_ms`."""
    LOGS.mkdir(parents=True, exist_ok=True)
    # stdout to a pipe is truncated at ~64 KB by opencode; a file is not
    tmp = LOGS / f"export-{session_id}.json"
    with open(tmp, "w") as f:
        subprocess.run([OPENCODE_BIN, "export", session_id], stdout=f, stderr=subprocess.DEVNULL,
                        cwd=tempfile.gettempdir())
    raw = tmp.read_text()
    tmp.unlink()
    start = raw.find("{")
    data = json.loads(raw[start:]) if start >= 0 else {"messages": []}
    m = {"cost": 0.0, "tokens_in": 0, "tokens_out": 0, "tokens_reasoning": 0, "cache_read": 0,
         "model_s": 0.0, "messages": 0, "files_changed": 0}
    for msg in data.get("messages", []):
        info = msg.get("info", {})
        if info.get("role") != "assistant" or info.get("time", {}).get("created", 0) < since_ms:
            continue
        t = info.get("tokens", {})
        m["cost"] += info.get("cost", 0.0) or 0.0
        m["tokens_in"] += t.get("input", 0)
        m["tokens_out"] += t.get("output", 0)
        m["tokens_reasoning"] += t.get("reasoning", 0)
        m["cache_read"] += t.get("cache", {}).get("read", 0)
        tm = info.get("time", {})
        if tm.get("completed"):
            m["model_s"] += (tm["completed"] - tm["created"]) / 1000
        m["messages"] += 1
    m["files_changed"] = data.get("info", {}).get("summary", {}).get("files", 0)
    m["cost"] = round(m["cost"], 6)
    m["model_s"] = round(m["model_s"], 1)
    return m


GROK_PROMPT_INLINE_LIMIT = 100_000  # bytes; at/above this, use --prompt-file instead of -p


def _grok_prompt_args(a) -> tuple[list[str], Path | None]:
    """Read the prompt text (from --prompt or --prompt-file -- swarm.py itself is never
    sandboxed; only the grok process it launches is) and return the argv fragment grok should
    read the prompt from, plus a temp file for the caller to delete once the run finishes (or
    None if nothing was created).

    grok's own process runs inside the `[grok] sandbox` profile, which only grants *it* read
    access to its own CWD (the worktree), system paths, ~/.grok, and -- for the built-in
    profiles -- /tmp. The ops dir (where data/logs/ lives) is none of those, so a sandboxed grok
    can't open a prompt file written there (`Permission denied`). A prompt short enough goes
    inline as `-p <text>` instead, so grok never has to open a prompt file at all. A longer one
    is written to a fresh file under `tempfile.gettempdir()` (a path every built-in profile can
    read) and passed with `--prompt-file`. Either way a copy is kept under data/logs/ for the
    record -- that copy is never what grok itself reads."""
    prompt_text = Path(a.prompt_file).read_text() if a.prompt_file else (a.prompt or "")

    LOGS.mkdir(parents=True, exist_ok=True)
    record = LOGS / f"prompt-{a.phase}-{a.task}-{now_ms()}.md"
    record.write_text(prompt_text)

    if len(prompt_text.encode()) < GROK_PROMPT_INLINE_LIMIT:
        return ["-p", prompt_text], None

    # never inside the worktree: tempfile.gettempdir() is independent of --cwd, and every
    # built-in sandbox profile (workspace/devbox/read-only/strict) can read it
    fd, tmp_name = tempfile.mkstemp(prefix="swarm-grok-prompt-", suffix=".md",
                                     dir=tempfile.gettempdir())
    with os.fdopen(fd, "w") as f:
        f.write(prompt_text)
    tmp = Path(tmp_name)
    return ["--prompt-file", str(tmp)], tmp


def _grok_argv(a, model: dict, dir_path: Path, kind: str) -> tuple[list[str], Path | None]:
    prompt_args, prompt_tmp = _grok_prompt_args(a)
    cmd = [GROK_BIN, *prompt_args, "--cwd", str(dir_path),
           "-m", model["id"], "--output-format", "streaming-json", "--always-approve",
           "--no-subagents", "--no-auto-update", "--max-turns", str(GROK_MAX_TURNS)]
    if GROK_REASONING_EFFORT:
        cmd += ["--reasoning-effort", GROK_REASONING_EFFORT]
    if a.session:
        cmd += ["--resume", a.session]  # never -s: that creates a *new* session
    if kind == "review":
        cmd += ["--disallowed-tools", "search_replace,run_terminal_cmd,Agent",
                "--tools", "read_file,grep,list_dir"]
    if GROK_SANDBOX:
        cmd += ["--sandbox", GROK_SANDBOX]
    return cmd, prompt_tmp


def cmd_run(a):
    dir_path = check_worktree_safety(_resolve_dir(a.dir))
    model = MODELS[a.model]
    driver = model.get("driver", "opencode")
    if driver == "claude":
        sys.exit(f"refusing to run '{a.model}': driver=claude workers are subagents the orchestrator runs "
                 "itself; log their runs with `swarm.py log-run`")
    if driver == "grok" and not GROK_SANDBOX and not GROK_ALLOW_UNSANDBOXED:
        sys.exit("grok driver needs a sandbox profile; set [grok] sandbox or allow_unsandboxed = true")
    kind = a.kind or ("fix" if a.session else "build")
    emit({"type": "task", "phase": a.phase, "task": a.task, "model": a.model,
          "status": "fixing" if kind == "fix" else "working"})
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"{a.phase}-{a.task}-{a.model}-{now_ms()}.jsonl"

    env_extra = None
    if driver == "grok":
        cmd, prompt_tmp = _grok_argv(a, model, dir_path, kind)
        a._grok_prompt_tmp = prompt_tmp  # finalize() deletes this /tmp copy once the run is done
        # GROK_MEMORY=0 keeps a worker run from reading/writing cross-session memory;
        # GROK_DISABLE_AUTOUPDATER belt-and-suspenders alongside --no-auto-update
        env_extra = {"GROK_MEMORY": "0", "GROK_DISABLE_AUTOUPDATER": "1"}
    else:
        prompt = Path(a.prompt_file).read_text() if a.prompt_file else a.prompt
        cmd = [OPENCODE_BIN, "run", "--format", "json", "-m", model["id"], "--dir", str(dir_path),
               "--title", f"{a.phase}/{a.task}/{a.model}"]
        if OPENCODE_AGENT:
            cmd += ["--agent", OPENCODE_AGENT]
        if a.session:
            cmd += ["-s", a.session]
        cmd.append(prompt)

    start = now_ms()
    t0 = time.monotonic()
    timed_out = False
    with open(log, "w") as lf:
        popen_kwargs = dict(stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             cwd=str(dir_path))
        if env_extra:
            popen_kwargs["env"] = {**os.environ, **env_extra}
        proc = subprocess.Popen(cmd, **popen_kwargs)
        emit({"type": "run_start", "phase": a.phase, "task": a.task, "model": a.model,
              "dir": str(dir_path), "pid": proc.pid, "log": str(log)})
        # watchdog: kill on the overall timeout, or if the worker prints nothing for a.stall seconds
        while proc.poll() is None:
            time.sleep(2)
            st = log.stat()
            idle = time.time() - st.st_mtime
            # a run that never prints is hung; one that has started may be quietly writing a big file
            limit = a.stall if st.st_size == 0 else a.stall_active
            if time.monotonic() - t0 > a.timeout or idle > limit:
                proc.kill()
                proc.wait()
                timed_out = True
                break
        code = -1 if timed_out else proc.returncode
    wall = round(time.monotonic() - t0, 1)
    finalize(a, kind, start, wall, code, timed_out, log)


def grok_log(log: Path) -> dict:
    """Parse a Grok `--output-format streaming-json` run log: one JSON object per line, stderr
    (update checks, warnings) mixed in as non-JSON lines and skipped. Concatenates `text` chunks
    (`{"type":"text","data":...}`) into the worker's final answer, and pulls session/spend/outcome
    fields from the terminal `end` event -- see references/grok-cli.md for the event shapes."""
    texts = []
    end: dict = {}
    for line in log.read_text().splitlines():
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type")
        if t == "text" and ev.get("data"):
            texts.append(ev["data"])
        elif t == "end":
            end = ev
    usage = end.get("usage") or {}
    return {
        "text": "".join(texts),
        "sessionId": end.get("sessionId"),
        "stopReason": end.get("stopReason"),
        "total_cost_usd": end.get("total_cost_usd"),  # None when the server never reported one
        "num_turns": end.get("num_turns", 0),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "reasoning_tokens": usage.get("reasoning_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
    }


def finalize(a, kind, start, wall, code, timed_out, log):
    driver = MODELS.get(a.model, {}).get("driver", "opencode")
    hit_max_turns = False
    gl = None
    if driver == "grok":
        gl = grok_log(log)
        sid = gl["sessionId"]
        cost = gl["total_cost_usd"]
        u = gl["usage"]
        metrics = {"cost": cost, "tokens_in": u["input_tokens"], "tokens_out": u["output_tokens"],
                   "tokens_reasoning": u["reasoning_tokens"], "cache_read": u["cache_read_input_tokens"],
                   "model_s": 0.0, "messages": gl["num_turns"], "files_changed": 0}
        if cost is None:
            metrics["cost_unknown"] = True  # never silently 0 -- the server just didn't report one
        hit_max_turns = gl["stopReason"] == "max_turn_requests"
    else:
        sid = a.session
        if not sid:
            found = re.findall(r'"sessionID"\s*:\s*"(ses_[A-Za-z0-9]+)"', log.read_text())
            sid = found[0] if found else None
        metrics = session_metrics(sid, start) if sid else {}

    failed = (code != 0) or hit_max_turns
    outcome = "timeout" if timed_out else ("error" if failed else "ok")

    rec = {"phase": a.phase, "task": a.task, "model": a.model, "kind": kind, "session": sid,
           "wall_s": wall, "exit": code, "timed_out": timed_out, "outcome": outcome,
           "log": str(log), **metrics}
    if hit_max_turns:
        rec["reason"] = "hit --max-turns"
    with open(LEDGER, "a") as f:
        f.write(json.dumps({"t": now_ms(), **rec}) + "\n")
    emit({"type": "run", **{k: v for k, v in rec.items() if k != "log"}})
    emit({"type": "run_end", "log": str(log), "exit": code, "timed_out": timed_out})
    emit({"type": "task", "phase": a.phase, "task": a.task, "model": a.model,
          "status": "review" if not failed else "failed",
          **({"note": "timed out"} if timed_out else {})})
    # written after the ledger so a bad --text-out path can never lose the run's cost
    if getattr(a, "text_out", None):
        if driver == "grok":
            text = gl["text"]
        else:
            # keep the worker's final written answer (used for OpenCode reviews)
            texts = []
            for line in log.read_text().splitlines():
                try:
                    part = json.loads(line).get("part", {})
                except ValueError:
                    continue
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
            text = texts[-1] if texts else ""
        out = Path(a.text_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    # remove the /tmp prompt copy an oversized Grok prompt needed for --prompt-file, if any
    tmp = getattr(a, "_grok_prompt_tmp", None)
    if tmp:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    print(json.dumps(rec, indent=1))


# ---------------------------------------------------------------- simple events


def cmd_recover(a):
    """Rebuild the ledger entry for a finished run whose bookkeeping failed, from its log."""
    log = Path(a.log).resolve()
    start = int(re.search(r"-(\d{13})\.jsonl$", log.name).group(1))
    wall = round(log.stat().st_mtime - start / 1000, 1)
    a.session = None
    finalize(a, a.kind, start, wall, 0, False, log)


def cmd_phase(a):
    emit({"type": "phase", "phase": a.phase, "status": a.status})


def cmd_task(a):
    if not a.model:
        # fill in the worker from earlier events so a status change never creates an ownerless card
        owners = {e.get("model") for e in load_events()
                  if e["type"] == "task" and e["phase"] == a.phase and e["task"] == a.task and e.get("model")}
        if len(owners) > 1:
            sys.exit(f"task {a.phase}/{a.task} has several workers {sorted(owners)}; pass --model")
        a.model = owners.pop() if owners else None
    ev = {"type": "task", "phase": a.phase, "task": a.task, "status": a.status}
    for k in ("model", "title", "note", "files"):
        if getattr(a, k):
            ev[k] = getattr(a, k)
    emit(ev)


def cmd_note(a):
    emit({"type": "note", "phase": a.phase or current_phase(load_events()), "who": a.who, "text": a.text})


def cmd_review(a):
    emit({"type": "review", "phase": a.phase, "task": a.task, "model": a.model, "verdict": a.verdict,
          "findings": a.findings, "wall_s": a.wall, "summary": a.summary or ""})


def cmd_tests(a):
    emit({"type": "tests", "phase": a.phase, "task": a.task, "model": a.model,
          "passed": a.passed, "failed": a.failed})


def cmd_crew(a):
    emit({"type": "crew", "phase": a.phase or current_phase(load_events()), "who": a.who,
          "state": a.state, "doing": a.doing or ""})


def cmd_score(a):
    emit({"type": "score", "phase": a.phase, "task": a.task, "model": a.model, "score": a.score,
          "summary": a.summary or ""})


# ---------------------------------------------------------------- init / worktrees


def cmd_init(a):
    """Scaffold a fresh ops directory: swarm.toml (from the packaged example) + data/out/prompts/specs."""
    target = Path(a.dir).resolve() if a.dir else Path.cwd()
    cfg_path = target / "swarm.toml"
    if cfg_path.exists():
        sys.exit(f"refusing to overwrite existing {cfg_path}")
    example = SKILL_DIR / "swarm.toml.example"
    if not example.is_file():
        sys.exit(f"can't find the packaged swarm.toml.example (looked in {example})")

    text = example.read_text()
    text = _patch_toml_scalar(text, "project", "name", a.name)
    text = _patch_toml_scalar(text, "project", "repo", a.repo)
    target.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text)

    made = []
    for sub in ("data", "data/logs", "data/reviews", "out", "prompts", "specs"):
        (target / sub).mkdir(parents=True, exist_ok=True)
        made.append(f"{sub}/")

    copied, skipped = [], []
    tmpl_dir = SKILL_DIR / "templates" / "prompts"
    if tmpl_dir.is_dir():
        for f in sorted(tmpl_dir.glob("*.md")):
            dest = target / "prompts" / f.name
            if dest.exists():
                skipped.append(dest.name)
                continue
            dest.write_text(f.read_text())
            copied.append(dest.name)

    print(f"wrote {cfg_path}")
    for sub in made:
        print(f"  {target / sub}")
    if copied:
        print(f"copied prompts: {', '.join(copied)}")
    if skipped:
        print(f"skipped existing prompts (already present): {', '.join(skipped)}")
    print("edit swarm.toml (worktrees, forbidden, models, claude.session_dir) before running anything.")


def cmd_wt(a):
    """Create a task or detached-review worktree under project.worktrees; print its path."""
    worktrees = Path(CFG["project"]["worktrees"]).expanduser().resolve()
    repo = Path(CFG["project"]["repo"]).expanduser().resolve()
    worktrees.mkdir(parents=True, exist_ok=True)

    if a.detach:
        name = f"rv-{a.task}"
        path = check_worktree_safety(worktrees / name)
        _git(["-C", str(repo), "worktree", "add", "--detach", str(path), a.detach])
    else:
        suffix = f"-{a.model}" if a.model else ""
        name = f"{a.phase}-{a.task}{suffix}"
        branch = f"{a.phase}/{a.task}{suffix}"
        path = check_worktree_safety(worktrees / name)
        _git(["-C", str(repo), "worktree", "add", "-b", branch, str(path), a.from_])
    print(path)


# ---------------------------------------------------------------- health


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True


def cmd_health(a):
    """List every run() that has started but not finished: model, task, dir, pid, seconds since its log
    last grew. A run idle for more than 600s is flagged HUNG. Always exits 0 (this is a status report)."""
    starts, ended = {}, set()
    for e in load_events():
        if e["type"] == "run_start":
            starts[str(Path(e["log"]).resolve())] = e
        elif e["type"] == "run_end":
            ended.add(str(Path(e["log"]).resolve()))
    open_runs = sorted((e for log, e in starts.items() if log not in ended), key=lambda e: e["t"])
    if not open_runs:
        print("no runs in flight")
        return
    print(f"{'model':<10}{'task':<20}{'pid':>8}{'idle':>9}  dir")
    for e in open_runs:
        pid = e.get("pid")
        alive = bool(pid) and _pid_alive(pid)
        log = Path(e["log"])
        idle = time.time() - log.stat().st_mtime if log.exists() else float("inf")
        idle_s = "?" if idle == float("inf") else f"{int(idle)}s"
        flag = " CRASHED (bookkeeping lost; run `swarm.py recover`)" if pid and not alive else \
            " HUNG" if idle > 600 else ""
        pid_s = str(pid) if alive else f"{pid}(dead)" if pid else "-"
        print(f"{e['model']:<10}{e['task']:<20}{pid_s:>8}{idle_s:>9}{flag}  {e['dir']}")


# ---------------------------------------------------------------- cost


def cmd_cost(a):
    """OpenCode cost/time/runs per phase and model from the ledger, plus Claude totals from the last
    `claude` tallies logged for each (phase, role, model)."""
    ledger = [r for r in load_ledger() if not a.phase or r.get("phase") == a.phase]
    agg = defaultdict(lambda: defaultdict(float))
    for r in ledger:
        k = agg[(r["phase"], r["model"])]
        k["runs"] += 1
        k["wall"] += r.get("wall_s", 0)
        k["model_s"] += r.get("model_s", 0)
        k["cost"] += r.get("cost") or 0  # cost may be None (cost_unknown): never let it poison the sum

    print(f"{'phase':<6}{'model':<28}{'runs':>5}{'wall':>10}{'model':>10}{'cost':>10}")
    oc_total = 0.0
    for (ph, m), r in sorted(agg.items()):
        name = MODELS.get(m, {}).get("name", m)
        print(f"{ph:<6}{name:<28}{int(r['runs']):>5}{fmt_s(r['wall']):>10}{fmt_s(r['model_s']):>10}"
              f"{r['cost']:>10.4f}")
        oc_total += r["cost"]
    print(f"{'':<6}{'opencode total':<28}{'':>5}{'':>10}{'':>10}{oc_total:>10.4f}")

    last = {}
    for e in load_events():
        if e["type"] == "claude" and (not a.phase or e["phase"] == a.phase):
            last[(e["phase"], e["role"], e["model"])] = e
    if last:
        claude_total = sum(e["cost"] for e in last.values())
        print(f"{'':<6}{'claude total (list price)':<28}{'':>5}{'':>10}{'':>10}{claude_total:>10.4f}")
        print(f"{'':<6}{'grand total':<28}{'':>5}{'':>10}{'':>10}{oc_total + claude_total:>10.4f}")


# ---------------------------------------------------------------- status


def fmt_s(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def cmd_status(a):
    events = load_events()
    cur = current_phase(events)
    tasks = {}
    for e in events:
        if e["type"] == "task":
            key = (e["phase"], e["task"], e.get("model"))
            tasks.setdefault(key, {}).update(e)
    title = next((p["title"] for p in PHASES if p["id"] == cur), cur)
    print(f"Current phase: {cur} · {title}\n")
    for (ph, task, model), t in tasks.items():
        if ph == cur:
            name = MODELS.get(model, {}).get("name", model or "orchestrator")
            print(f"  {task:<18} {name:<28} {t['status']}")
    agg = defaultdict(lambda: defaultdict(float))
    for e in events:
        if e["type"] == "run":
            r = agg[(e["phase"], e["model"])]
            r["runs"] += 1
            r["fix"] += e["kind"] == "fix"
            r["wall"] += e["wall_s"]
            r["model_s"] += e.get("model_s", 0)
            r["tokens"] += e.get("tokens_in", 0) + e.get("tokens_out", 0)
            r["cost"] += e.get("cost") or 0  # cost may be None (cost_unknown): never let it poison the sum
    if agg:
        print(f"\n{'phase':<6}{'model':<28}{'runs':>5}{'fixes':>6}{'wall':>10}{'model':>10}{'tokens':>10}{'cost':>9}")
        tot = defaultdict(float)
        for (ph, m), r in sorted(agg.items()):
            name = MODELS.get(m, {}).get("name", m)
            print(f"{ph:<6}{name:<28}{int(r['runs']):>5}{int(r['fix']):>6}{fmt_s(r['wall']):>10}"
                  f"{fmt_s(r['model_s']):>10}{int(r['tokens']):>10,}{r['cost']:>9.4f}")
            for k, v in r.items():
                tot[k] += v
        print(f"{'':<6}{'TOTAL':<28}{int(tot['runs']):>5}{int(tot['fix']):>6}{fmt_s(tot['wall']):>10}"
              f"{fmt_s(tot['model_s']):>10}{int(tot['tokens']):>10,}{tot['cost']:>9.4f}")


# ---------------------------------------------------------------- push / export


def cmd_push(a):
    """Write dashboard documents to out/; print the ones that changed since the last push."""
    OUT.mkdir(parents=True, exist_ok=True)
    events = load_events()
    docs = {("project", "meta"): build_meta()}
    by_phase = defaultdict(list)
    for e in events:
        by_phase[e.get("phase") or (PHASES[0]["id"] if PHASES else "p0")].append(e)
    for ph, evs in by_phase.items():
        docs[("timeline", ph)] = {"phase": ph, "events": evs}
    cache_f = OUT / ".hashes.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    ver_f = OUT / ".versions.json"
    vers = json.loads(ver_f.read_text()) if ver_f.exists() else {}
    for kv in a.resync or []:
        k, v = kv.split("=")
        vers[k] = int(v)
    changed = []
    for (coll, doc_id), body in docs.items():
        path = OUT / f"{coll}__{doc_id}.json"
        text = json.dumps(body, separators=(",", ":"))
        h = hashlib.sha1(text.encode()).hexdigest()
        path.write_text(text)
        if a.all or cache.get(path.name) != h:
            w = {"op": "set", "collection": coll, "doc_id": doc_id, "file_path": str(path)}
            key = f"{coll}/{doc_id}"
            if key in vers:
                w["if_version"] = vers[key]
            vers[key] = vers.get(key, 0) + 1  # assumes the write lands; --resync fixes drift
            changed.append(w)
        cache[path.name] = h
    cache_f.write_text(json.dumps(cache))
    ver_f.write_text(json.dumps(vers))
    print(json.dumps(changed))
    if DASHBOARD_URL:
        print(f"# apply with ArtifactData (action batch, url {DASHBOARD_URL}, writes = the list above)",
              file=sys.stderr)


def cmd_export(a):
    """Bundle the event log into a standalone replay page (no database needed)."""
    page = (SKILL_DIR / "assets" / "dashboard.html").read_text()
    payload = {"meta": build_meta(), "events": load_events()}
    inject = f"<script>window.__SWARM_EMBED__ = {json.dumps(payload)};</script>\n"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(inject + page)
    print(out)


# ---------------------------------------------------------------- activity scan
#
# What each in-flight agent is doing, read from what its runtime already writes: the run log of every
# `swarm.py run` still going (Grok and OpenCode workers), and the transcripts of Claude subagents whose
# description starts with a [<phase>:<task>] or [<phase>:<task>:<model>] tag. `scan` logs a change of an
# agent's latest action as an `activity` event (the board shows it on the task card and the worker lane,
# never in the feed); `agents` lists the in-flight agents with their recent actions for the scribe.

TAG = re.compile(r"^\[(?P<phase>[\w.-]+):(?P<task>[\w.-]+)(?::(?P<model>[\w.-]+))?\]")
ACTIVE = ("working", "fixing")
WRITING = "writing its answer"


def _tail_lines(path: Path, max_bytes: int = 256_000) -> list[str]:
    """The last lines of a (possibly large, still growing) log; a cut first line is dropped."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    return lines[1:] if size > max_bytes else lines


def _json_lines(path: Path) -> list[dict]:
    out = []
    for line in _tail_lines(path):
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _trim_paths(text: str, roots: list[str]) -> str:
    for root in sorted((r for r in roots if r), key=len, reverse=True):
        text = text.replace(root.rstrip("/") + "/", "").replace(root, ".")
    return text


def describe_tool(name: str, inp: dict | None, roots: list[str]) -> str:
    """`Bash · Run the tests`: the tool, then the most telling input, with the worktree paths trimmed."""
    inp = inp if isinstance(inp, dict) else {}
    detail = next((inp[k] for k in ("description", "file_path", "target_file", "path", "pattern", "command",
                                     "query", "url", "prompt") if inp.get(k)), "")
    detail = _trim_paths(str(detail).strip(), roots)
    detail = detail.splitlines()[0][:90] if detail else ""
    return f"{name} · {detail}" if detail else name


def _said(text: str, roots: list[str]) -> tuple[str, str]:
    return ("text", " ".join(_trim_paths(text, roots).split())[:160])


def claude_actions(transcript: Path, roots: list[str]) -> list[tuple[str, str]]:
    """(kind, text) per action in a Claude transcript, oldest first: kind is "tool" or "text"."""
    out = []
    for d in _json_lines(transcript):
        content = (d.get("message") or {}).get("content")
        if d.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                out.append(("tool", describe_tool(block.get("name", "?"), block.get("input"), roots)))
            elif block.get("type") == "text" and block.get("text", "").strip():
                out.append(_said(block["text"], roots))
    return out


def grok_actions(log: Path, roots: list[str]) -> list[tuple[str, str]]:
    """From a streaming-json run log: tool calls, and each stretch of streamed answer text as one entry."""
    out, text = [], []
    for ev in _json_lines(log):
        t = ev.get("type")
        if t == "text" and ev.get("data"):
            text.append(ev["data"])
            continue
        if t in ("tool_call", "end") and "".join(text).strip():
            out.append(_said("".join(text), roots))
            text = []
        if t == "tool_call":
            out.append(("tool", describe_tool(ev.get("toolName") or ev.get("title") or "?", ev.get("rawInput"), roots)))
    if "".join(text).strip():
        out.append(_said("".join(text), roots))
    return out


def opencode_actions(log: Path, roots: list[str]) -> list[tuple[str, str]]:
    """From an `opencode run --format json` log: `tool_use` lines carry the finished tool part."""
    out = []
    for ev in _json_lines(log):
        part = ev.get("part") or {}
        if part.get("type") == "tool":
            out.append(("tool", describe_tool(part.get("tool", "?"), (part.get("state") or {}).get("input"), roots)))
        elif part.get("type") == "text" and str(part.get("text", "")).strip():
            out.append(_said(part["text"], roots))
    return out


def latest(actions: list[tuple[str, str]]) -> str | None:
    """The line the board shows: the latest tool call, or WRITING once answer text follows it."""
    if not actions:
        return None
    kind, text = actions[-1]
    return text if kind == "tool" else WRITING


def _claude_subagent_dirs(events: list[dict]) -> list[Path]:
    if CLAUDE_SESSION:
        return [SESSION_DIR / CLAUDE_SESSION / "subagents"]
    if not events or not SESSION_DIR.is_dir():
        return []
    since = events[0]["t"] / 1000.0
    return sorted(d for d in SESSION_DIR.glob("*/subagents") if d.stat().st_mtime >= since)


def _claude_span(transcript: Path) -> tuple[int | None, int | None, bool]:
    """First and last message times (ms) of a transcript, and whether its last turn ended the agent's work
    (a final answer with no tool call pending)."""
    from datetime import datetime

    times, ended = [], False
    for d in _json_lines(transcript):
        ts = d.get("timestamp")
        if ts:
            times.append(int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000))
        m = d.get("message") or {}
        if d.get("type") == "assistant" and isinstance(m.get("content"), list):
            ended = m.get("stop_reason") == "end_turn" and not any(b.get("type") == "tool_use" for b in m["content"])
        elif d.get("type") == "user":
            ended = False
    return (times[0] if times else None), (times[-1] if times else None), ended


def in_flight(events: list[dict] | None = None) -> list[dict]:
    """Every agent working now: {phase, task, model, runtime, status, log, idle_s, actions, doing, ...}.
    Runs from `swarm.py run` also carry pid/alive; Claude subagents carry started/last/ended and whether a
    `run` event has been logged for this stretch of work yet."""
    events = load_events() if events is None else events
    repo = CFG.get("project", {}).get("repo", "")
    tasks: dict[tuple, dict] = {}
    for e in events:
        if e["type"] == "task":
            tasks.setdefault((e["phase"], e["task"], e.get("model")), {}).update(e)
    agents = []

    starts, ended = {}, set()
    for e in events:
        if e["type"] == "run_start":
            starts[e["log"]] = e
        elif e["type"] == "run_end":
            ended.add(e["log"])
    for log_s, e in starts.items():
        log = Path(log_s)
        if log_s in ended or not log.exists():
            continue
        driver = MODELS.get(e["model"], {}).get("driver", "opencode")
        read = grok_actions if driver == "grok" else opencode_actions
        acts = read(log, [e.get("dir", ""), repo])
        pid = e.get("pid")
        agents.append({"phase": e["phase"], "task": e["task"], "model": e["model"], "runtime": driver,
                       "status": tasks.get((e["phase"], e["task"], e["model"]), {}).get("status", "working"),
                       "log": log_s, "idle_s": int(time.time() - log.stat().st_mtime), "pid": pid,
                       "alive": bool(pid) and _pid_alive(pid), "actions": acts, "doing": latest(acts)})

    for sub in _claude_subagent_dirs(events):
        for meta_f in sorted(sub.glob("agent-*.meta.json")):
            try:
                m = TAG.match(json.loads(meta_f.read_text()).get("description", ""))
            except (OSError, ValueError):
                continue
            transcript = meta_f.with_name(meta_f.name.replace(".meta.json", ".jsonl"))
            if not m or not transcript.exists():
                continue
            owners = [k for k, t in tasks.items() if k[:2] == (m["phase"], m["task"])
                      and t["status"] in ACTIVE and (not m["model"] or k[2] == m["model"])]
            if len(owners) != 1:  # not working now, or several workers and the tag doesn't say which
                continue
            key = owners[0]
            acts = claude_actions(transcript, [repo])
            started, last, done = _claude_span(transcript)
            since = tasks[key].get("t", 0)
            logged = any(e["type"] == "run" and (e["phase"], e["task"], e.get("model")) == key and e["t"] >= since
                         for e in events)
            agents.append({"phase": key[0], "task": key[1], "model": key[2], "runtime": "claude",
                           "status": tasks[key]["status"], "log": str(transcript),
                           "idle_s": int(time.time() - transcript.stat().st_mtime), "actions": acts,
                           "doing": latest(acts), "started": started, "last": last, "ended": done,
                           "run_logged": logged})
    return agents


def scan_activity(events: list[dict] | None = None) -> int:
    """Log an `activity` event for every in-flight agent whose latest action changed; return how many."""
    events = load_events() if events is None else events
    last = {}
    for e in events:
        if e["type"] == "activity" and e.get("doing"):
            last[(e["phase"], e["task"], e.get("model"))] = e["doing"]
    changed = 0
    for ag in in_flight(events):
        key = (ag["phase"], ag["task"], ag["model"])
        if ag["doing"] and ag["doing"] != last.get(key):
            emit({"type": "activity", "phase": ag["phase"], "task": ag["task"], "model": ag["model"],
                  "runtime": ag["runtime"], "doing": ag["doing"]})
            changed += 1
    return changed


def cmd_scan(a):
    print(f"activity events logged: {scan_activity()}")


def cmd_agents(a):
    """In-flight agents with their recent actions: the scribe's reading, or a quick look for a person."""
    agents = in_flight()
    for ag in agents:
        ag["actions"] = [f"{'said: ' if k == 'text' else ''}{t}" for k, t in ag["actions"][-a.tail:]]
    if a.json:
        print(json.dumps(agents, indent=1))
        return
    if not agents:
        print("no agents working")
        return
    for ag in agents:
        flags = []
        if ag.get("pid") and not ag.get("alive"):
            flags.append("CRASHED")
        if ag["idle_s"] > 600:
            flags.append(f"quiet {ag['idle_s'] // 60}m")
        if ag.get("ended"):
            flags.append("finished" + ("" if ag.get("run_logged") else ", run not logged"))
        print(f"{ag['phase']}/{ag['task']} {ag['model']} ({ag['runtime']}, {ag['status']})"
              + (f"  [{'; '.join(flags)}]" if flags else ""))
        for act in ag["actions"]:
            print(f"    {act}")


def cmd_activity(a):
    """The scribe's plain-words summary of what an agent is doing; the board shows it on the card and lane."""
    emit({"type": "activity", "phase": a.phase, "task": a.task, "model": a.model, "summary": a.summary,
          "who": a.who})


def cmd_log_run(a):
    """Log a run swarm.py didn't launch (a Claude subagent, say), so the board counts it: runs, reruns,
    failures. Its tokens and cost come from `swarm.py claude`, so they default to 0 here."""
    outcome = a.outcome or ("ok" if a.exit == 0 else "error")
    ev = {"type": "run", "phase": a.phase, "task": a.task, "model": a.model, "kind": a.kind, "wall_s": a.wall,
          "model_s": a.wall, "tokens_in": a.tokens, "tokens_out": 0, "cost": a.cost, "exit": a.exit,
          "outcome": outcome}
    if a.reason:
        ev["reason"] = a.reason
    print(json.dumps(emit(ev)))


# ---------------------------------------------------------------- standalone board


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file and rename, so a reader (the polling page) never sees half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# The page is written for the claude.ai Artifact skeleton, which supplies the document head. Served on
# its own it needs one, or the browser guesses the encoding and every non-ASCII glyph breaks.
SITE_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
             '<link rel="icon" href="data:,">'
             '<style>[hidden]{display:none!important}img{max-width:100%}</style>'
             '</head><body style="margin:0">')


def write_site(site: Path) -> None:
    """The standalone board: index.html plus the meta.json / events.json it polls every 5 s."""
    site.mkdir(parents=True, exist_ok=True)
    _write_atomic(site / "meta.json", json.dumps(build_meta(), separators=(",", ":")))
    _write_atomic(site / "events.json", json.dumps(load_events(), separators=(",", ":")))
    page = (SKILL_DIR / "assets" / "dashboard.html").read_text(encoding="utf-8")
    title = re.match(r"\s*(<title>.*?</title>)", page, re.S)  # the page's title belongs in the head
    head = SITE_HEAD.replace("</head>", (title.group(1) if title else "") + "</head>")
    _write_atomic(site / "index.html", head + page[title.end():] if title else head + page)


def _site_dir(a) -> Path:
    return Path(a.out).expanduser().resolve() if a.out else OUT / "site"


def cmd_site(a):
    site = _site_dir(a)
    write_site(site)
    print(f"site written to {site}")


def _serve(site: Path, port: int, bind: str) -> None:
    """Serve the board folder from a daemon thread (stdlib only, no request logging)."""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer((bind, port), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"serving {site} at http://{bind}:{httpd.server_port}/", flush=True)


def watch_pass(site: Path, tally: bool) -> None:
    """One refresh of the board: activity scan, Claude tally (when there is a session to read), site files."""
    scan_activity()
    if tally and SESSION_DIR.is_dir():
        claude_tally(CLAUDE_SESSION)
    write_site(site)


def cmd_watch(a):
    """Keep the standalone board current: one pass every --every seconds, optionally serving it."""
    site = _site_dir(a)
    if a.once:
        watch_pass(site, not a.no_claude)
        print(f"site written to {site}")
        return
    site.mkdir(parents=True, exist_ok=True)
    if a.serve is not None:
        _serve(site, a.serve, a.bind)
    while True:
        try:
            watch_pass(site, not a.no_claude)
        except Exception as exc:  # keep watching; one bad pass must not stop the board
            print(f"watch: {exc}", file=sys.stderr, flush=True)
        time.sleep(a.every)


# ---------------------------------------------------------------- sync


def cmd_sync(a):
    """Copy events.jsonl, ledger.jsonl, prompts/, specs/ and shots/*.png into <repo>/<records_dir>."""
    repo = Path(CFG["project"]["repo"]).expanduser().resolve()
    dest = repo / CFG["project"].get("records_dir", "tools/swarm-records")
    dest.mkdir(parents=True, exist_ok=True)
    copied = []

    for f in (EVENTS, LEDGER):
        if f.exists():
            shutil.copy2(f, dest / f.name)
            copied.append(f.name)

    for sub in ("prompts", "specs"):
        src = OPS / sub
        if src.is_dir() and any(src.iterdir()):
            shutil.copytree(src, dest / sub, dirs_exist_ok=True)
            copied.append(f"{sub}/")

    shots = OPS / "shots"
    n = 0
    if shots.is_dir():
        for png in shots.rglob("*.png"):
            rel = png.relative_to(shots)
            target = dest / "shots" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(png, target)
            n += 1
    if n:
        copied.append(f"shots/ ({n} png)")

    print(f"synced to {dest}")
    for c in copied:
        print(f"  {c}")


# ---------------------------------------------------------------- claude usage


def _price_key(model: str) -> str:
    for k in CLAUDE_PRICES:
        if model and model.startswith(k):
            return k
    return model or "unknown"


def _read_transcript(path: Path) -> list[dict]:
    """One entry per API message (streamed blocks repeat the same message id; keep the last)."""
    msgs = {}
    for line in path.open():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        m = e.get("message") or {}
        if e.get("type") == "assistant" and m.get("usage") and m.get("id"):
            msgs[m["id"]] = {"t": e.get("timestamp"), "model": m.get("model"), "u": m["usage"]}
    return list(msgs.values())


def claude_tally(session: str) -> tuple[dict, int] | None:
    """Tally Claude (orchestrator + subagent) tokens and list-price cost per phase and model; log the
    tallies that changed. Returns (per (phase, role, model) totals, number logged), or None when there
    is no session to read and no event yet to anchor an unscoped tally."""
    from datetime import datetime

    if session:
        sources = [("orchestrator", SESSION_DIR / f"{session}.jsonl")]
        sources += [("reviewer", f) for f in sorted((SESSION_DIR / session / "subagents").glob("agent-*.jsonl"))]
    else:
        # no session configured: tally every transcript under session_dir touched since our first event
        events0 = load_events()
        if not events0:
            return None
        since = events0[0]["t"] / 1000.0
        top = [f for f in SESSION_DIR.glob("*.jsonl") if f.stat().st_mtime >= since]
        subs = [f for f in SESSION_DIR.glob("*/subagents/agent-*.jsonl") if f.stat().st_mtime >= since]
        sources = [("orchestrator", f) for f in sorted(top)] + [("reviewer", f) for f in sorted(subs)]
    sources = [(role, path) for role, path in sources if path.exists()]

    events = load_events()
    starts = [(e["t"], e["phase"]) for e in events if e["type"] == "phase" and e["status"] == "active"]

    def phase_at(ms):
        ph = PHASES[0]["id"] if PHASES else "p0"
        for t, p in starts:
            if t <= ms:
                ph = p
        return ph

    agg = defaultdict(lambda: defaultdict(float))
    for role, path in sources:
        for m in _read_transcript(path):
            ms = int(datetime.fromisoformat(m["t"].replace("Z", "+00:00")).timestamp() * 1000)
            key = _price_key(m["model"])
            u = m["u"]
            cc = u.get("cache_creation") or {}
            w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
            w5m = (u.get("cache_creation_input_tokens", 0) or 0) - w1h
            r = agg[(phase_at(ms), role, key)]
            r["messages"] += 1
            r["input"] += u.get("input_tokens", 0) or 0
            r["output"] += u.get("output_tokens", 0) or 0
            r["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
            r["cache_write"] += w5m + w1h
            pi, po, pr, pw5, pw1 = CLAUDE_PRICES.get(key, (0, 0, 0, 0, 0))
            r["cost"] += (u.get("input_tokens", 0) or 0) * pi / 1e6 + (u.get("output_tokens", 0) or 0) * po / 1e6
            r["cost"] += (u.get("cache_read_input_tokens", 0) or 0) * pr / 1e6 + w5m * pw5 / 1e6 + w1h * pw1 / 1e6

    # log only what changed since the last tally
    last = {}
    for e in events:
        if e["type"] == "claude":
            last[(e["phase"], e["role"], e["model"])] = e
    changed = 0
    for (ph, role, model), r in sorted(agg.items()):
        ev = {"type": "claude", "phase": ph, "role": role, "model": model, "name": CLAUDE_NAMES.get(model, model),
              "messages": int(r["messages"]), "input": int(r["input"]), "output": int(r["output"]),
              "cache_read": int(r["cache_read"]), "cache_write": int(r["cache_write"]), "cost": round(r["cost"], 4)}
        prev = last.get((ph, role, model))
        if not prev or prev["messages"] != ev["messages"] or prev["output"] != ev["output"]:
            emit(ev)
            changed += 1
    return agg, changed


def cmd_claude(a):
    """Tally Claude usage (see claude_tally) and print totals per role and model."""
    res = claude_tally(a.session if a.session is not None else CLAUDE_SESSION)
    if res is None:
        sys.exit("claude: no events yet in this ops dir to anchor an unscoped tally; "
                 "pass --session, or set claude.session in swarm.toml")
    agg, changed = res
    tot = defaultdict(lambda: defaultdict(float))
    for (ph, role, model), r in agg.items():
        for k, v in r.items():
            tot[(role, model)][k] += v
    print(f"{'role':<13}{'model':<11}{'msgs':>6}{'output':>10}{'cache rd':>12}{'cache wr':>11}{'cost':>10}")
    for (role, model), r in sorted(tot.items()):
        print(f"{role:<13}{CLAUDE_NAMES.get(model, model):<11}{int(r['messages']):>6}{int(r['output']):>10,}"
              f"{int(r['cache_read']):>12,}{int(r['cache_write']):>11,}{r['cost']:>10.2f}")
    print(f"logged {changed} changed tallies")


# ---------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swarm",
        epilog="Config lookup: --config PATH (anywhere on the command line), else $SWARM_CONFIG, "
               "else swarm.toml in the current directory or any parent. Run `swarm.py init` to create one.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="scaffold a fresh ops directory")
    s.add_argument("--dir", help="where to scaffold (default: current directory)")
    s.add_argument("--name", required=True, help="project.name")
    s.add_argument("--repo", required=True, help="project.repo (absolute path to the main checkout)")
    s.set_defaults(fn=cmd_init)

    r = sub.add_parser("run")
    r.add_argument("phase"); r.add_argument("task"); r.add_argument("model", choices=MODELS)
    r.add_argument("--dir", required=True)
    r.add_argument("--prompt"); r.add_argument("--prompt-file")
    r.add_argument("--session"); r.add_argument("--timeout", type=int, default=WATCHDOG["timeout"])
    r.add_argument("--stall", type=int, default=WATCHDOG["stall"],
                    help="kill if the run prints nothing at all for this long")
    r.add_argument("--stall-active", type=int, default=WATCHDOG["stall_active"],
                    help="kill if a started run goes quiet this long")
    r.add_argument("--text-out", help="save the worker's final text answer to this file")
    r.add_argument("--kind", help="override run kind (e.g. review)")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("recover"); s.add_argument("phase"); s.add_argument("task")
    s.add_argument("model", choices=MODELS); s.add_argument("log"); s.add_argument("--kind", default="build")
    s.add_argument("--text-out", help="also save the final text answer (for reviews)")
    s.set_defaults(fn=cmd_recover)

    s = sub.add_parser("wt", help="create a task or detached-review worktree")
    s.add_argument("phase"); s.add_argument("task")
    s.add_argument("--model", help="suffix the worktree/branch name, e.g. p2-server-luna")
    s.add_argument("--from", dest="from_", default="main", help="base ref for a new branch")
    s.add_argument("--detach", metavar="BRANCH", help="make a detached review worktree rv-<task> of BRANCH")
    s.set_defaults(fn=cmd_wt)

    s = sub.add_parser("phase"); s.add_argument("phase"); s.add_argument("status", choices=["active", "done"])
    s.set_defaults(fn=cmd_phase)

    s = sub.add_parser("task"); s.add_argument("phase"); s.add_argument("task")
    s.add_argument("status", choices=["queued", "working", "review", "fixing", "merged", "failed", "dropped"])
    s.add_argument("--model"); s.add_argument("--title"); s.add_argument("--note"); s.add_argument("--files")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("note"); s.add_argument("text"); s.add_argument("--phase")
    s.add_argument("--who", default="orchestrator"); s.set_defaults(fn=cmd_note)

    s = sub.add_parser("review"); s.add_argument("phase"); s.add_argument("task"); s.add_argument("model")
    s.add_argument("verdict", choices=["pass", "changes"]); s.add_argument("--findings", type=int, default=0)
    s.add_argument("--wall", type=float, default=0); s.add_argument("--summary"); s.set_defaults(fn=cmd_review)

    s = sub.add_parser("tests"); s.add_argument("phase"); s.add_argument("task"); s.add_argument("model")
    s.add_argument("--passed", type=int, required=True); s.add_argument("--failed", type=int, required=True)
    s.set_defaults(fn=cmd_tests)

    s = sub.add_parser("score"); s.add_argument("phase"); s.add_argument("task"); s.add_argument("model")
    s.add_argument("score", type=float); s.add_argument("--summary"); s.set_defaults(fn=cmd_score)

    s = sub.add_parser("crew"); s.add_argument("who", choices=["orchestrator", "reviewer"])
    s.add_argument("state", choices=["busy", "idle"]); s.add_argument("doing", nargs="?")
    s.add_argument("--phase"); s.set_defaults(fn=cmd_crew)

    s = sub.add_parser("health", help="runs started but not finished; flags anything idle >600s as HUNG")
    s.set_defaults(fn=cmd_health)

    s = sub.add_parser("cost", help="opencode + claude cost/time/runs per phase and model")
    s.add_argument("--phase")
    s.set_defaults(fn=cmd_cost)

    s = sub.add_parser("claude")
    s.add_argument("--session", default=None,
                    help="override claude.session from config; pass --session \"\" to force the unscoped tally")
    s.set_defaults(fn=cmd_claude)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    s = sub.add_parser("push"); s.add_argument("--all", action="store_true")
    s.add_argument("--resync", nargs="*", help="coll/doc=version pairs from the database")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("export")
    default_out = str(OUT / f"{_slugify(CFG.get('project', {}).get('name', 'swarm'))}-replay.html") if OUT else None
    s.add_argument("--out", default=default_out, required=default_out is None)
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("scan", help="log what each in-flight agent is doing now (activity events)")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("agents", help="in-flight agents with their recent actions")
    s.add_argument("--tail", type=int, default=8, help="recent actions per agent (default 8)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_agents)

    s = sub.add_parser("activity", help="log a plain-words summary of what an agent is doing")
    s.add_argument("phase"); s.add_argument("task"); s.add_argument("model"); s.add_argument("summary")
    s.add_argument("--who", default="scribe"); s.set_defaults(fn=cmd_activity)

    s = sub.add_parser("log-run", help="log a run swarm.py didn't launch (e.g. a Claude subagent)")
    s.add_argument("phase"); s.add_argument("task"); s.add_argument("model")
    s.add_argument("--kind", default="build"); s.add_argument("--wall", type=float, required=True)
    s.add_argument("--tokens", type=int, default=0); s.add_argument("--cost", type=float, default=0.0)
    s.add_argument("--exit", type=int, default=0)
    s.add_argument("--outcome", choices=["ok", "rejected", "timeout", "error"])
    s.add_argument("--reason", default="")
    s.set_defaults(fn=cmd_log_run)

    s = sub.add_parser("site", help="write the standalone board (index.html, meta.json, events.json)")
    s.add_argument("--out", help="board folder (default: <ops>/out/site)")
    s.set_defaults(fn=cmd_site)

    s = sub.add_parser("watch", help="refresh the standalone board on a timer, optionally serving it")
    s.add_argument("--every", type=float, default=30, help="seconds between passes (default 30)")
    s.add_argument("--out", help="board folder (default: <ops>/out/site)")
    s.add_argument("--serve", type=int, metavar="PORT", help="also serve the folder on this port")
    s.add_argument("--bind", default="127.0.0.1", help="address for --serve (default 127.0.0.1, this machine only)")
    s.add_argument("--no-claude", action="store_true", help="skip the Claude transcript tally")
    s.add_argument("--once", action="store_true", help="one pass, then exit")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("sync", help="copy events/ledger/prompts/specs/shots into <repo>/<records_dir>")
    s.set_defaults(fn=cmd_sync)

    return p


def main():
    argv = sys.argv[1:]
    cfg_arg, argv = extract_config_arg(argv)
    # peek at the subcommand so `init`, `--help` and a bare invocation work without an existing config
    sub = next((a for a in argv if not a.startswith("-")), None)
    if sub not in (None, "init"):
        load_config(find_config(cfg_arg))
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
