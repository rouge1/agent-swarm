#!/usr/bin/env python3
"""Tests for the sensitive-repo guard in `swarm.py run`: with [project] sensitive = true, a model that does not
explicitly set trains_on_prompts = false is refused before any worktree check or run is started.

Run from the skill folder:
    python3 -m unittest discover -s scripts/tests -v
"""
import importlib.util
import io
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("swarm", SCRIPTS_DIR / "swarm.py")
swarm = importlib.util.module_from_spec(_spec)
sys.modules["swarm"] = swarm
_spec.loader.exec_module(swarm)

CONFIG = """
[project]
name = "t"
repo = "/tmp/t-repo"
worktrees = "/tmp/t-wt"
sensitive = {sensitive}

[[models]]
key = "free"
id = "opencode/free-model"
name = "Free"
trains_on_prompts = true

[[models]]
key = "safe"
id = "openrouter/safe-model"
name = "Safe"
trains_on_prompts = false

[[models]]
key = "unknown"
id = "openrouter/unknown-model"
name = "Unknown"
"""


def load(sensitive: str, tmp: Path) -> None:
    path = tmp / "swarm.toml"
    path.write_text(CONFIG.format(sensitive=sensitive))
    swarm.load_config(path)


def try_run(model: str) -> str:
    """Return the refusal message, or '' when the guard let the call through to the worktree check."""
    a = swarm.build_parser().parse_args(["run", "p1", "t1", model, "--dir", "/tmp/t-wt/x"])
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            swarm.cmd_run(a)
    except SystemExit as e:
        return str(e.code)
    except Exception:
        return ""
    return ""


class SensitiveGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sensitive_refuses_free_and_unset_models(self):
        load("true", self.tmp)
        self.assertIn("trains_on_prompts = false", try_run("free"))
        self.assertIn("trains_on_prompts = false", try_run("unknown"))

    def test_sensitive_allows_explicit_no_training_model(self):
        load("true", self.tmp)
        self.assertNotIn("trains_on_prompts", try_run("safe"))

    def test_not_sensitive_does_not_refuse(self):
        load("false", self.tmp)
        self.assertNotIn("trains_on_prompts", try_run("free"))
        self.assertNotIn("trains_on_prompts", try_run("unknown"))


if __name__ == "__main__":
    unittest.main()
