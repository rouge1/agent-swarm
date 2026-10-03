#!/usr/bin/env python3
"""Tests for the Grok worker driver in scripts/swarm.py.

Never invokes the real `grok` binary. `[grok] bin` is pointed at
scripts/tests/fake_grok.py, a stub that captures the argv/env it received and prints
`--output-format streaming-json` lines whose shape is switchable via $FAKE_GROK_MODE (see that
file's docstring). Each test drives swarm.py's public `cmd_*` functions against a temp ops
directory + a temp git repo/worktree, exactly like a real `swarm.py run` invocation would.

Run from the skill folder:
    python3 -m unittest discover -s scripts/tests -v
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
FAKE_GROK = TESTS_DIR / "fake_grok.py"

_spec = importlib.util.spec_from_file_location("swarm", SCRIPTS_DIR / "swarm.py")
swarm = importlib.util.module_from_spec(_spec)
sys.modules["swarm"] = swarm
_spec.loader.exec_module(swarm)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


FAKE_OPENCODE_SRC = """#!/usr/bin/env python3
import json, os, sys
cap = os.environ.get("FAKE_OPENCODE_CAPTURE")
if cap:
    with open(cap, "w") as f:
        json.dump(sys.argv[1:], f)
print(json.dumps({"part": {"type": "text", "text": "done"}}))
"""


class GrokDriverTestBase(unittest.TestCase):
    """Temp git repo + worktree + ops dir, with [grok] bin pointed at the fake_grok.py stub."""

    def setUp(self):
        os.chmod(FAKE_GROK, os.stat(FAKE_GROK).st_mode | stat.S_IEXEC)

        self.tmp = Path(tempfile.mkdtemp(prefix="swarm-grok-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.repo = self.tmp / "repo"
        self.worktrees = self.tmp / "repo-wt"
        self.ops = self.tmp / "ops"
        self.repo.mkdir()
        self.worktrees.mkdir()
        self.ops.mkdir()

        _git(["init", "-q", "-b", "main"], self.repo)
        _git(["config", "user.email", "test@example.com"], self.repo)
        _git(["config", "user.name", "Swarm Test"], self.repo)
        (self.repo / "README.md").write_text("hi\n")
        _git(["add", "-A"], self.repo)
        _git(["commit", "-q", "-m", "init"], self.repo)

        self.wt = self.worktrees / "b1-t1-grokw"
        _git(["worktree", "add", "-q", "-b", "b1/t1-grokw", str(self.wt), "main"], self.repo)

        self.capture = self.ops / "argv-capture.json"

        # a trivial opencode stub, for the one test that checks the opencode branch is untouched
        self.fake_opencode = self.tmp / "fake_opencode.py"
        self.fake_opencode.write_text(FAKE_OPENCODE_SRC)
        os.chmod(self.fake_opencode, self.fake_opencode.stat().st_mode | stat.S_IEXEC)
        self.opencode_capture = self.ops / "opencode-argv.json"

    def write_config(self, grok_extra="", sonnet_driver='driver = "claude"') -> Path:
        cfg = f"""
[project]
name = "t"
repo = "{self.repo}"
worktrees = "{self.worktrees}"
forbidden = []

[opencode]
bin = "{self.fake_opencode}"

[[models]]
key = "grokw"
id = "grok-test-model"
name = "Grok Test"
driver = "grok"

[[models]]
key = "sonnetw"
id = "claude-sonnet-5"
name = "Sonnet"
{sonnet_driver}

[watchdog]
stall = 1
stall_active = 3
timeout = 30

[grok]
bin = "{FAKE_GROK}"
max_turns = 7
{grok_extra}
"""
        p = self.ops / "swarm.toml"
        p.write_text(cfg)
        return p

    def load(self, grok_extra="", sonnet_driver='driver = "claude"'):
        swarm.load_config(self.write_config(grok_extra=grok_extra, sonnet_driver=sonnet_driver))

    def run_grok(self, model_key="grokw", session=None, kind=None, text_out=None, mode="normal",
                 extra_env=None, prompt="hello world"):
        argv = ["run", "b1", "t1", model_key, "--dir", str(self.wt), "--prompt", prompt]
        if session:
            argv += ["--session", session]
        if kind:
            argv += ["--kind", kind]
        if text_out:
            argv += ["--text-out", str(text_out)]
        a = swarm.build_parser().parse_args(argv)
        env = {"FAKE_GROK_CAPTURE": str(self.capture), "FAKE_GROK_MODE": mode}
        if extra_env:
            env.update(extra_env)
        with mock.patch.dict(os.environ, env):
            with contextlib.redirect_stdout(io.StringIO()):
                swarm.cmd_run(a)
        return a

    def captured_argv(self) -> list[str]:
        return json.loads(self.capture.read_text())["argv"]

    def captured_env(self) -> dict:
        return json.loads(self.capture.read_text())["env"]

    def last_ledger(self) -> dict:
        lines = swarm.LEDGER.read_text().splitlines()
        return json.loads(lines[-1])

    def last_run_event(self) -> dict:
        run_events = [e for e in swarm.load_events() if e["type"] == "run"]
        return run_events[-1]

    def last_task_event(self) -> dict:
        task_events = [e for e in swarm.load_events() if e["type"] == "task"]
        return task_events[-1]

    def prompt_records(self) -> list[Path]:
        """The data/logs/prompt-b1-t1-*.md copies kept for the record (never what grok reads)."""
        return sorted(swarm.LOGS.glob("prompt-b1-t1-*.md"))


class TestArgvShape(GrokDriverTestBase):
    def test_normal_build_argv(self):
        self.load()
        self.run_grok()
        argv = self.captured_argv()

        # a small prompt goes inline as -p <text> -- grok's sandboxed process never has to
        # open a prompt file at all, so a Permission denied under a strict profile is impossible
        self.assertEqual(argv[0], "-p")
        self.assertEqual(argv[1], "hello world")
        self.assertNotIn("--prompt-file", argv)

        # a copy is still kept under data/logs/ for the record, even though it's not read by grok
        records = self.prompt_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].read_text(), "hello world")

        self.assertIn("--cwd", argv)
        self.assertEqual(argv[argv.index("--cwd") + 1], str(self.wt))
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "grok-test-model")
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "streaming-json")
        self.assertIn("--always-approve", argv)
        self.assertIn("--no-subagents", argv)
        self.assertIn("--no-auto-update", argv)
        self.assertIn("--max-turns", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "7")
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "swarm-worker")

        # never -s (that creates a *new* session on this CLI)
        self.assertNotIn("-s", argv)
        # build kind (no session): no --resume, no reviewer flags, no --reasoning-effort
        self.assertNotIn("--resume", argv)
        self.assertNotIn("--disallowed-tools", argv)
        self.assertNotIn("--reasoning-effort", argv)

    def test_reasoning_effort_flag(self):
        self.load(grok_extra='reasoning_effort = "low"')
        self.run_grok()
        argv = self.captured_argv()
        self.assertIn("--reasoning-effort", argv)
        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "low")

    def test_resume_on_fix_round_never_dash_s(self):
        self.load()
        a = self.run_grok(session="ses_prev123")
        argv = self.captured_argv()
        self.assertEqual(a.kind or "fix", "fix")  # kind defaults to fix when --session is given
        self.assertIn("--resume", argv)
        self.assertEqual(argv[argv.index("--resume") + 1], "ses_prev123")
        self.assertNotIn("-s", argv)

    def test_reviewer_tool_restrictions(self):
        self.load()
        self.run_grok(kind="review")
        argv = self.captured_argv()
        self.assertIn("--disallowed-tools", argv)
        self.assertEqual(argv[argv.index("--disallowed-tools") + 1],
                          "search_replace,run_terminal_cmd,Agent")
        self.assertIn("--tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "read_file,grep,list_dir")

    def test_env_vars_on_the_grok_popen(self):
        self.load()
        self.run_grok()
        env = self.captured_env()
        self.assertEqual(env["GROK_MEMORY"], "0")
        self.assertEqual(env["GROK_DISABLE_AUTOUPDATER"], "1")

    def test_large_prompt_uses_tmp_prompt_file_and_cleans_up(self):
        self.load()
        big = "x" * 150_000  # > 100 KB: must go through --prompt-file, not inline -p
        self.run_grok(prompt=big)
        argv = self.captured_argv()

        self.assertIn("--prompt-file", argv)
        self.assertNotIn("-p", argv)
        tmp_path = Path(argv[argv.index("--prompt-file") + 1])

        # under the OS temp dir, never under the ops dir (data/logs/) and never under the worktree
        self.assertEqual(tmp_path.parent, Path(tempfile.gettempdir()))
        self.assertNotIn(swarm.LOGS, tmp_path.parents)
        self.assertNotIn(self.wt, tmp_path.parents)

        # the stub read the file's real content before it could be cleaned up
        captured = json.loads(self.capture.read_text())
        self.assertEqual(captured["prompt_file_content"], big)

        # the record copy under data/logs/ still has the full text
        records = self.prompt_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].read_text(), big)

        # finalize() deletes the /tmp copy once the run is done
        self.assertFalse(tmp_path.exists())


class TestRefusals(GrokDriverTestBase):
    def test_refuses_without_sandbox_or_allow_unsandboxed(self):
        self.load(grok_extra='sandbox = ""')
        with self.assertRaises(SystemExit) as cm:
            self.run_grok()
        msg = str(cm.exception)
        self.assertIn("sandbox", msg)
        self.assertIn("allow_unsandboxed", msg)
        self.assertFalse(self.capture.exists(), "must refuse before ever spawning the process")

    def test_allow_unsandboxed_skips_sandbox_flag(self):
        self.load(grok_extra='sandbox = ""\nallow_unsandboxed = true')
        self.run_grok()
        argv = self.captured_argv()
        self.assertNotIn("--sandbox", argv)

    def test_refuses_claude_driver(self):
        self.load()
        with self.assertRaises(SystemExit) as cm:
            self.run_grok(model_key="sonnetw")
        msg = str(cm.exception)
        self.assertIn("driver=claude", msg)
        self.assertIn("swarm.py log-run", msg)


class TestFinalize(GrokDriverTestBase):
    def test_ledger_and_text_out_normal_run(self):
        self.load()
        text_out = self.ops / "answer.txt"
        self.run_grok(text_out=text_out, mode="normal")

        self.assertEqual(text_out.read_text(), "Here is the answer.")

        rec = self.last_ledger()
        self.assertEqual(rec["session"], "ses_fake0000")
        self.assertEqual(rec["cost"], 0.0123)
        self.assertNotIn("cost_unknown", rec)
        self.assertEqual(rec["tokens_in"], 100)
        self.assertEqual(rec["tokens_out"], 40)
        self.assertEqual(rec["tokens_reasoning"], 10)
        self.assertEqual(rec["cache_read"], 5)
        self.assertEqual(rec["messages"], 3)
        self.assertEqual(rec["model_s"], 0.0)
        self.assertEqual(rec["files_changed"], 0)
        self.assertEqual(rec["exit"], 0)
        self.assertFalse(rec["timed_out"])
        self.assertEqual(rec["outcome"], "ok")

        run_ev = self.last_run_event()
        self.assertEqual(run_ev["outcome"], "ok")
        self.assertNotIn("reason", run_ev)
        task_ev = self.last_task_event()
        self.assertEqual(task_ev["status"], "review")

    def test_files_changed_counts_what_the_run_wrote(self):
        self.load()
        edits = {"README.md": "changed\n", "src/new.py": "x = 1\n", "src/other.py": "y = 2\n"}
        self.run_grok(extra_env={"FAKE_GROK_WRITE": json.dumps(edits)})
        self.assertEqual(self.last_ledger()["files_changed"], 3)

    def test_files_changed_counts_a_deletion(self):
        self.load()
        self.run_grok(extra_env={"FAKE_GROK_WRITE": json.dumps({"README.md": None})})
        self.assertEqual(self.last_ledger()["files_changed"], 1)

    def test_files_changed_ignores_what_an_earlier_run_left_behind(self):
        self.load()
        (self.wt / "earlier.py").write_text("from the first round\n")
        (self.wt / "README.md").write_text("edited by the first round\n")
        self.run_grok(session="ses_fake0000",
                      extra_env={"FAKE_GROK_WRITE": json.dumps({"fix.py": "z = 3\n"})})
        self.assertEqual(self.last_ledger()["files_changed"], 1)

    def test_files_changed_counts_a_fix_that_rewrites_an_earlier_file(self):
        self.load()
        (self.wt / "earlier.py").write_text("from the first round\n")
        self.run_grok(session="ses_fake0000",
                      extra_env={"FAKE_GROK_WRITE": json.dumps({"earlier.py": "rewritten\n"})})
        self.assertEqual(self.last_ledger()["files_changed"], 1)

    def test_files_changed_counts_files_the_worker_committed(self):
        base = swarm._git_out(self.wt, "rev-parse", "HEAD").strip()
        before = swarm._worktree_files(self.wt, base)
        (self.wt / "committed.py").write_text("a = 1\n")
        _git(["add", "-A"], self.wt)
        _git(["-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m", "w"], self.wt)
        after = swarm._worktree_files(self.wt, base)
        self.assertEqual(swarm._files_changed(before, after), 1)

    def test_files_changed_left_out_when_git_cannot_read_the_dir(self):
        self.assertIsNone(swarm._worktree_files(self.tmp, "HEAD"))

    def test_cost_unknown_when_total_cost_usd_missing(self):
        self.load()
        self.run_grok(mode="missing_cost")
        rec = self.last_ledger()
        self.assertIsNone(rec["cost"])
        self.assertTrue(rec["cost_unknown"])
        # a missing cost is not itself a failure -- stopReason was still end_turn
        self.assertEqual(rec["outcome"], "ok")

    def test_max_turn_requests_is_failed_even_with_exit_zero(self):
        self.load()
        self.run_grok(mode="max_turn_requests")
        rec = self.last_ledger()
        self.assertEqual(rec["exit"], 0)  # the stub exits cleanly when it hits the cap
        self.assertEqual(rec["outcome"], "error")
        self.assertEqual(rec["reason"], "hit --max-turns")

        run_ev = self.last_run_event()
        self.assertEqual(run_ev["outcome"], "error")
        self.assertEqual(run_ev["reason"], "hit --max-turns")

        task_ev = self.last_task_event()
        self.assertEqual(task_ev["status"], "failed")

    def test_watchdog_timeout_outcome(self):
        self.load()
        self.run_grok(mode="sleep_forever")
        rec = self.last_ledger()
        self.assertTrue(rec["timed_out"])
        self.assertEqual(rec["exit"], -1)
        self.assertEqual(rec["outcome"], "timeout")

        run_ev = self.last_run_event()
        self.assertEqual(run_ev["outcome"], "timeout")

        task_ev = self.last_task_event()
        self.assertEqual(task_ev["status"], "failed")
        self.assertEqual(task_ev.get("note"), "timed out")


class TestOpencodeBranchUnchanged(GrokDriverTestBase):
    def test_opencode_argv_starts_with_opencode_bin(self):
        self.load()
        argv = ["run", "b1", "t1", "sonnetw2", "--dir", str(self.wt), "--prompt", "hi"]
        # add a driver=opencode model directly via the config so we don't touch the claude one
        cfg_path = self.ops / "swarm.toml"
        cfg_path.write_text(cfg_path.read_text().replace(
            '[[models]]\nkey = "sonnetw"\nid = "claude-sonnet-5"\nname = "Sonnet"\ndriver = "claude"\n',
            '[[models]]\nkey = "sonnetw"\nid = "claude-sonnet-5"\nname = "Sonnet"\ndriver = "claude"\n\n'
            '[[models]]\nkey = "sonnetw2"\nid = "opencode/model"\nname = "OC"\n'
        ))
        swarm.load_config(cfg_path)

        a = swarm.build_parser().parse_args(argv)
        with mock.patch.dict(os.environ, {"FAKE_OPENCODE_CAPTURE": str(self.opencode_capture)}):
            with contextlib.redirect_stdout(io.StringIO()):
                swarm.cmd_run(a)

        oc_argv = json.loads(self.opencode_capture.read_text())
        self.assertEqual(oc_argv[0], "run")
        self.assertEqual(oc_argv[1:4], ["--format", "json", "-m"])
        self.assertNotIn("--prompt-file", oc_argv)
        self.assertNotIn("--sandbox", oc_argv)

        # driver defaults to "opencode" and process actually ran the fake opencode binary
        rec = self.last_ledger()
        self.assertEqual(rec["model"], "sonnetw2")


class TestCostStatusToleratesNullCost(GrokDriverTestBase):
    def test_cost_and_status_do_not_crash_on_null_cost(self):
        self.load()
        self.run_grok(mode="missing_cost")
        self.run_grok(mode="normal")

        a_cost = swarm.build_parser().parse_args(["cost"])
        a_status = swarm.build_parser().parse_args(["status"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            swarm.cmd_cost(a_cost)
            swarm.cmd_status(a_status)
        # got this far without raising -- also sanity check some output was produced
        self.assertTrue(buf.getvalue())


if __name__ == "__main__":
    unittest.main()
