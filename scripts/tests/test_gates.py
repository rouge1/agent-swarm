#!/usr/bin/env python3
"""Tests for the opt-in gate check in `swarm.py run` ([project] require_gates = true): a phase runs only when its
spec has a Gates section and that spec is committed on HEAD in the repo's records folder.

Run from the skill folder:
    python3 -m unittest discover -s scripts/tests -v
"""
import importlib.util
import subprocess
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

GATES = "# Phase p1\n\n## Gates (pre-registered: fixed before any worker runs)\n- Pass/fail: 1 test\n"


class GuardPassed(Exception):
    """Raised by the stubbed worktree check: reaching it means the gate check let the call through."""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin"})


def setup(tmp: Path, require: str, spec_text: str | None, committed: bool) -> None:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("x\n")
    git(repo, "add", "README")
    git(repo, "commit", "-q", "-m", "init")
    ops = tmp / "ops"
    (ops / "specs").mkdir(parents=True)
    if spec_text is not None:
        (ops / "specs" / "p1.md").write_text(spec_text)
        records = repo / "records" / "specs"
        records.mkdir(parents=True)
        (records / "p1.md").write_text(spec_text)
        if committed:
            git(repo, "add", "records")
            git(repo, "commit", "-q", "-m", "gates")
    (ops / "swarm.toml").write_text(f'''
[project]
name = "t"
repo = "{repo}"
worktrees = "{tmp / 'wt'}"
records_dir = "records"
require_gates = {require}

[[models]]
key = "safe"
id = "openrouter/safe-model"
name = "Safe"
trains_on_prompts = false
''')
    swarm.load_config(ops / "swarm.toml")


def try_run() -> tuple[str, bool]:
    a = swarm.build_parser().parse_args(["run", "p1", "t1", "safe", "--dir", "/tmp/t-wt/x"])
    stub = unittest.mock.Mock(side_effect=GuardPassed())
    with unittest.mock.patch.object(swarm, "check_worktree_safety", stub):
        try:
            swarm.cmd_run(a)
        except GuardPassed:
            return "", True
        except SystemExit as e:
            return str(e.code), stub.called
    return "", stub.called


class GateCheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_off_by_default_does_not_check(self):
        setup(self.tmp, "false", None, False)
        msg, reached = try_run()
        self.assertEqual(msg, "")
        self.assertTrue(reached)

    def test_refuses_missing_spec(self):
        setup(self.tmp, "true", None, False)
        msg, reached = try_run()
        self.assertIn("no spec", msg)
        self.assertFalse(reached)

    def test_refuses_spec_without_gates(self):
        setup(self.tmp, "true", "# Phase p1\n\nNo gates here.\n", True)
        msg, reached = try_run()
        self.assertIn("## Gates", msg)
        self.assertFalse(reached)

    def test_refuses_gates_not_committed(self):
        setup(self.tmp, "true", GATES, False)
        msg, reached = try_run()
        self.assertIn("not committed", msg)
        self.assertFalse(reached)

    def test_allows_committed_gates(self):
        setup(self.tmp, "true", GATES, True)
        msg, reached = try_run()
        self.assertEqual(msg, "")
        self.assertTrue(reached)

    def test_refuses_gates_changed_after_commit(self):
        setup(self.tmp, "true", GATES, True)
        (self.tmp / "ops" / "specs" / "p1.md").write_text(GATES.replace("1 test", "5 tests"))
        msg, reached = try_run()
        self.assertIn("differ", msg)
        self.assertFalse(reached)

    def test_changes_outside_the_gates_section_are_allowed(self):
        setup(self.tmp, "true", GATES, True)
        (self.tmp / "ops" / "specs" / "p1.md").write_text(GATES + "\n## Tasks\n- new task notes\n")
        msg, reached = try_run()
        self.assertEqual(msg, "")
        self.assertTrue(reached)

    def test_lookalike_heading_is_not_a_gates_section(self):
        setup(self.tmp, "true", "# Phase p1\n\n## Gatesmanship\nnotes\n", True)
        msg, reached = try_run()
        self.assertIn("## Gates", msg)
        self.assertFalse(reached)

    def test_duplicate_gates_sections_are_refused(self):
        setup(self.tmp, "true", GATES + "\n" + GATES, True)
        msg, reached = try_run()
        self.assertIn("more than one", msg)
        self.assertFalse(reached)

    def test_gates_on_another_branch_do_not_count(self):
        setup(self.tmp, "true", GATES, False)
        git(self.tmp / "repo", "checkout", "-q", "-b", "other")
        (self.tmp / "repo" / "records" / "specs").mkdir(parents=True, exist_ok=True)
        (self.tmp / "repo" / "records" / "specs" / "p1.md").write_text(GATES)
        git(self.tmp / "repo", "add", "records")
        git(self.tmp / "repo", "commit", "-q", "-m", "gates on other")
        git(self.tmp / "repo", "checkout", "-q", "main")
        msg, reached = try_run()
        self.assertIn("not committed", msg)
        self.assertFalse(reached)

    def test_default_records_dir_matches_sync(self):
        setup(self.tmp, "true", GATES, True)
        text = (self.tmp / "ops" / "swarm.toml").read_text().replace('records_dir = "records"\n', "")
        (self.tmp / "ops" / "swarm.toml").write_text(text)
        swarm.load_config(self.tmp / "ops" / "swarm.toml")
        (self.tmp / "repo" / "tools" / "swarm-records" / "specs").mkdir(parents=True, exist_ok=True)
        (self.tmp / "repo" / "tools" / "swarm-records" / "specs" / "p1.md").write_text(GATES)
        git(self.tmp / "repo", "add", "tools")
        git(self.tmp / "repo", "commit", "-q", "-m", "default records")
        msg, reached = try_run()
        self.assertEqual(msg, "")
        self.assertTrue(reached)


if __name__ == "__main__":
    unittest.main()
