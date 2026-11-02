#!/usr/bin/env python3
"""Tests for `swarm.py init`: per-project Grok sandbox profile, agent copies, and the missing-path warning.

Run from the skill folder:
    python3 -m unittest discover -s scripts/tests -v
"""
import argparse
import importlib.util
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

SWARM = Path(__file__).resolve().parents[1] / "swarm.py"
spec = importlib.util.spec_from_file_location("swarm", SWARM)
swarm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(swarm)


def args(**kw):
    base = dict(dir=None, name="My App", repo=None, worktrees=None, grok_env=[], grok_profile=None,
                agents=[], opencode_model=None)
    base.update(kw)
    return argparse.Namespace(**base)


class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "app"
        self.repo.mkdir()
        self.env = self.tmp / "env"
        self.env.mkdir()
        self.sandbox = self.tmp / "sandbox.toml"
        self.sandbox.write_text('[profiles.other]\nextends = "strict"\nread_only = ["/nope"]\n')
        os.environ["GROK_SANDBOX_FILE"] = str(self.sandbox)
        self.addCleanup(os.environ.pop, "GROK_SANDBOX_FILE", None)

    def init(self, **kw):
        swarm.cmd_init(args(dir=str(self.tmp / "ops"), repo=str(self.repo), **kw))
        return tomllib.loads((self.tmp / "ops" / "swarm.toml").read_text())

    def test_worktrees_default_next_to_repo(self):
        self.assertEqual(self.init()["project"]["worktrees"], f"{self.repo}-wt")

    def test_grok_profile_is_per_project_and_leaves_others_alone(self):
        cfg = self.init(grok_env=[str(self.env)])
        self.assertEqual(cfg["grok"]["sandbox"], "my-app-worker")
        profiles = tomllib.loads(self.sandbox.read_text())["profiles"]
        self.assertEqual(profiles["my-app-worker"]["read_only"], [str(self.env), f"{self.repo}/.git"])
        self.assertEqual(profiles["other"]["read_only"], ["/nope"])

    def test_existing_profile_is_not_overwritten(self):
        self.init(grok_env=[str(self.env)])
        before = self.sandbox.read_text()
        (self.tmp / "ops" / "swarm.toml").unlink()
        self.init(grok_env=["/somewhere/else"])
        self.assertEqual(self.sandbox.read_text(), before)

    def test_missing_path_is_reported(self):
        self.assertTrue(any("/nope" in p for p in swarm.sandbox_profile_problems("other")))
        self.assertTrue(any("not defined" in p for p in swarm.sandbox_profile_problems("ghost")))
        self.assertEqual(swarm.sandbox_profile_problems("strict"), [])

    def test_agents_copied_with_model_and_never_overwritten(self):
        self.init(agents=["claude", "opencode"], opencode_model="openrouter/x/y")
        oc = self.repo / ".opencode" / "agents"
        self.assertNotIn("REPLACE_WITH_PROVIDER/MODEL", (oc / "swarm-manager.md").read_text())
        self.assertFalse((oc / "swarm-research.md").exists())
        self.assertTrue((self.repo / ".claude" / "agents" / "swarm-manager.md").is_file())
        (oc / "swarm-manager.md").write_text("mine")
        (self.tmp / "ops" / "swarm.toml").unlink()
        self.init(agents=["opencode"])
        self.assertEqual((oc / "swarm-manager.md").read_text(), "mine")


if __name__ == "__main__":
    unittest.main()
