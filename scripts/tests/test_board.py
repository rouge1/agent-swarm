#!/usr/bin/env python3
"""Tests for the standalone board and the scribe's commands in scripts/swarm.py: `site`, `watch --once`,
the activity scan over Grok / OpenCode run logs and Claude subagent transcripts, `agents`, `activity` and
`log-run`. Nothing real is launched: run logs and transcripts are written by the tests.

Run from the skill folder:
    python3 -m unittest discover -s scripts/tests -v
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("swarm", SCRIPTS_DIR / "swarm.py")
swarm = importlib.util.module_from_spec(_spec)
sys.modules["swarm"] = swarm
_spec.loader.exec_module(swarm)


def run_cli(*argv) -> str:
    a = swarm.build_parser().parse_args(list(argv))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        a.fn(a)
    return out.getvalue()


def jl(path: Path, *objs, extra: str = "") -> None:
    path.write_text("".join(json.dumps(o) + "\n" for o in objs) + extra)


class BoardTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="swarm-board-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ops, self.repo, self.wt = self.tmp / "ops", self.tmp / "repo", self.tmp / "repo-wt"
        self.sessions = self.tmp / "sessions"
        for d in (self.ops, self.repo, self.wt / "p1-a-grokw", self.sessions / "sess1" / "subagents"):
            d.mkdir(parents=True)
        cfg = self.ops / "swarm.toml"
        cfg.write_text(f"""
[project]
name = "Board · Test"
repo = "{self.repo}"
worktrees = "{self.wt}"

[[models]]
key = "grokw"
id = "grok-x"
name = "Grok"
driver = "grok"

[[models]]
key = "ocw"
id = "openrouter/x"
name = "OC"

[[models]]
key = "sonnet"
id = "claude-sonnet-5"
name = "Sonnet"
driver = "claude"

[[phases]]
id = "p1"
title = "One"
goal = "g"
exit = "e"

[claude]
session_dir = "{self.sessions}"
session = "sess1"
""")
        swarm.load_config(cfg)

    def events(self, type_=None):
        return [e for e in swarm.load_events() if type_ is None or e["type"] == type_]

    def start_run(self, task, model, log_objs, extra=""):
        """A run `swarm.py run` started and hasn't finished: run_start event + its log so far."""
        swarm.LOGS.mkdir(parents=True, exist_ok=True)
        log = swarm.LOGS / f"p1-{task}-{model}-1790000000000.jsonl"
        jl(log, *log_objs, extra=extra)
        swarm.emit({"type": "task", "phase": "p1", "task": task, "model": model, "status": "working"})
        swarm.emit({"type": "run_start", "phase": "p1", "task": task, "model": model,
                    "dir": str(self.wt / f"p1-{task}-{model}"), "pid": os.getpid(), "log": str(log)})
        return log


class SiteTest(BoardTestBase):
    def test_site_writes_page_meta_and_events(self):
        swarm.emit({"type": "phase", "phase": "p1", "status": "active"})
        run_cli("site")
        site = swarm.OUT / "site"
        page = (site / "index.html").read_text(encoding="utf-8")
        self.assertTrue(page.startswith('<!doctype html><html lang="en"><head><meta charset="utf-8">'))
        head = page[: page.index("</head>")]
        self.assertIn("<title>", head)  # the page's own title moved into the head
        self.assertEqual(json.loads((site / "meta.json").read_text())["name"], "Board · Test")
        self.assertEqual(json.loads((site / "events.json").read_text())[0]["type"], "phase")
        self.assertEqual(sorted(p.name for p in site.iterdir()), ["events.json", "index.html", "meta.json"])

    def test_site_out_dir(self):
        out = self.tmp / "elsewhere"
        run_cli("site", "--out", str(out))
        self.assertTrue((out / "index.html").exists())

    def test_watch_once_scans_and_writes(self):
        self.start_run("a", "grokw", [{"type": "tool_call", "toolName": "read_file", "rawInput": {"target_file": "x.py"}}])
        run_cli("watch", "--once")
        self.assertTrue((swarm.OUT / "site" / "events.json").exists())
        shipped = json.loads((swarm.OUT / "site" / "events.json").read_text())
        self.assertIn("activity", [e["type"] for e in shipped])


class WatchReloadTest(BoardTestBase):
    def test_reload_picks_up_new_phases_and_survives_a_bad_edit(self):
        cfg = swarm.CONFIG_PATH
        seen = cfg.stat().st_mtime
        cfg.write_text(cfg.read_text() + '\n[[phases]]\nid = "p2"\ntitle = "Two"\ngoal = "g"\nexit = "e"\n')
        os.utime(cfg, (seen + 5, seen + 5))
        with contextlib.redirect_stdout(io.StringIO()):
            seen = swarm.reload_if_changed(seen)
        self.assertEqual([p["id"] for p in swarm.PHASES], ["p1", "p2"])
        cfg.write_text(cfg.read_text() + "\n[[phases]\n")  # half-typed edit
        os.utime(cfg, (seen + 5, seen + 5))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            swarm.reload_if_changed(seen)
        self.assertIn("keeping the last good one", err.getvalue())
        self.assertEqual([p["id"] for p in swarm.PHASES], ["p1", "p2"])
        run_cli("site")
        self.assertEqual(len(json.loads((swarm.OUT / "site" / "meta.json").read_text())["phases"]), 2)


class ConfigLookupTest(unittest.TestCase):
    def test_finds_dot_swarm_from_inside_the_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix="swarm-lookup-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = tmp / "repo" / ".swarm" / "swarm.toml"
        (tmp / "repo" / "src" / "pkg").mkdir(parents=True)
        cfg.parent.mkdir()
        cfg.write_text('[project]\nname = "x"\n')
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(tmp / "repo" / "src" / "pkg")
        env = {k: v for k, v in os.environ.items() if k != "SWARM_CONFIG"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(swarm.find_config(None), cfg.resolve())


class ParseTest(BoardTestBase):
    def test_grok_actions(self):
        root = str(self.wt / "p1-a-grokw")
        log = self.tmp / "g.jsonl"
        jl(log, {"type": "thought", "data": "hmm"},
           {"type": "tool_call", "toolName": "read_file", "rawInput": {"target_file": f"{root}/src/app.py"}},
           {"type": "tool_call_update", "toolCallId": "c1"},
           {"type": "text", "data": "All "}, {"type": "text", "data": "done."}, extra="update check: not json\n")
        acts = swarm.grok_actions(log, [root])
        self.assertEqual(acts, [("tool", "read_file · src/app.py"), ("text", "All done.")])
        self.assertEqual(swarm.latest(acts), swarm.WRITING)

    def test_opencode_actions(self):
        log = self.tmp / "o.jsonl"
        jl(log, {"type": "step_start", "part": {"type": "step-start"}},
           {"type": "tool_use", "part": {"type": "tool", "tool": "bash",
                                         "state": {"status": "completed", "input": {"command": "pytest -q\nmore"}}}})
        acts = swarm.opencode_actions(log, [])
        self.assertEqual(acts, [("tool", "bash · pytest -q")])
        self.assertEqual(swarm.latest(acts), "bash · pytest -q")

    def test_claude_actions_prefers_description(self):
        t = self.tmp / "c.jsonl"
        jl(t, {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest", "description": "Run the tests"}}]}})
        self.assertEqual(swarm.latest(swarm.claude_actions(t, [])), "Bash · Run the tests")


class ScanTest(BoardTestBase):
    def test_scan_logs_changes_only(self):
        log = self.start_run("a", "grokw", [{"type": "tool_call", "toolName": "grep", "rawInput": {"pattern": "def "}}])
        self.assertEqual(swarm.scan_activity(), 1)
        self.assertEqual(swarm.scan_activity(), 0)  # nothing new
        with open(log, "a") as f:
            f.write(json.dumps({"type": "tool_call", "toolName": "run_terminal_command",
                                "rawInput": {"command": "pytest"}}) + "\n")
        self.assertEqual(swarm.scan_activity(), 1)
        acts = self.events("activity")
        self.assertEqual([e["doing"] for e in acts], ["grep · def", "run_terminal_command · pytest"])
        self.assertEqual(acts[-1]["model"], "grokw")

    def test_finished_run_is_not_scanned(self):
        log = self.start_run("a", "ocw", [{"type": "tool_use", "part": {"type": "tool", "tool": "read", "state": {}}}])
        swarm.emit({"type": "run_end", "log": str(log), "exit": 0, "timed_out": False})
        self.assertEqual(swarm.scan_activity(), 0)

    def claude_subagent(self, desc, content, stop="tool_use"):
        sub = self.sessions / "sess1" / "subagents"
        (sub / "agent-1.meta.json").write_text(json.dumps({"description": desc}))
        jl(sub / "agent-1.jsonl",
           {"type": "user", "timestamp": "2026-09-27T10:00:00Z", "message": {"content": "go"}},
           {"type": "assistant", "timestamp": "2026-09-27T10:05:00Z",
            "message": {"stop_reason": stop, "content": content}})

    def test_claude_subagent_tagged(self):
        swarm.emit({"type": "task", "phase": "p1", "task": "b", "model": "sonnet", "status": "working"})
        self.claude_subagent("[p1:b] Build b", [{"type": "tool_use", "name": "Edit",
                                                 "input": {"file_path": f"{self.repo}/b.py"}}])
        self.assertEqual(swarm.scan_activity(), 1)
        self.assertEqual(self.events("activity")[0]["doing"], "Edit · b.py")
        ag = swarm.in_flight()[0]
        self.assertEqual((ag["runtime"], ag["ended"], ag["run_logged"]), ("claude", False, False))

    def test_claude_subagent_finished_and_untagged(self):
        swarm.emit({"type": "task", "phase": "p1", "task": "b", "model": "sonnet", "status": "working"})
        self.claude_subagent("[p1:b] Build b", [{"type": "text", "text": "Done: b.py written."}], stop="end_turn")
        ag = swarm.in_flight()[0]
        self.assertTrue(ag["ended"])
        self.assertEqual(ag["last"] - ag["started"], 300_000)
        self.claude_subagent("Build b", [{"type": "text", "text": "x"}])  # no tag: not matched to a task
        self.assertEqual(swarm.in_flight(), [])

    def test_claude_subagent_not_working(self):
        swarm.emit({"type": "task", "phase": "p1", "task": "b", "model": "sonnet", "status": "review"})
        self.claude_subagent("[p1:b] Build b", [{"type": "tool_use", "name": "Read", "input": {}}])
        self.assertEqual(swarm.in_flight(), [])


class ScribeCommandsTest(BoardTestBase):
    def test_agents_json_and_text(self):
        self.start_run("a", "grokw", [{"type": "tool_call", "toolName": "list_dir", "rawInput": {"path": "."}}])
        data = json.loads(run_cli("agents", "--json"))
        self.assertEqual((data[0]["task"], data[0]["actions"]), ("a", ["list_dir · ."]))
        self.assertIn("p1/a grokw (grok, working)", run_cli("agents"))

    def test_phase_deferred(self):
        run_cli("phase", "p1", "deferred")
        self.assertEqual(self.events("phase")[0]["status"], "deferred")

    def test_activity_summary_and_log_run(self):
        run_cli("activity", "p1", "b", "sonnet", "Writing the parser; tests not run yet")
        ev = self.events("activity")[0]
        self.assertEqual((ev["summary"], ev["who"]), ("Writing the parser; tests not run yet", "scribe"))
        # a scribe summary is not the scan's latest action, so the scan still logs its own line
        self.assertNotIn("doing", ev)
        run_cli("log-run", "p1", "b", "sonnet", "--wall", "300", "--exit", "1", "--reason", "tests failed")
        run = self.events("run")[0]
        self.assertEqual((run["outcome"], run["wall_s"], run["cost"], run["reason"]), ("error", 300.0, 0.0, "tests failed"))


if __name__ == "__main__":
    unittest.main()
