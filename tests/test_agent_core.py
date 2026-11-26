import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

import agent_core


class AgentCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "proj"
        self.workspace.mkdir()
        os.environ["AGENT_ROOT"] = str(self.root)
        (self.workspace / "add.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.workspace / ".env").write_text("PASSWORD=hidden\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_blocks_escape_and_secrets(self):
        with self.assertRaises(agent_core.AgentError):
            agent_core.read_file(self.workspace, "../proj/.env")
        with self.assertRaises(agent_core.AgentError):
            agent_core.read_file(self.workspace, ".env")
        with self.assertRaises(agent_core.AgentError):
            agent_core.run_command(self.workspace, "python -c 'print(1)' && cat /etc/passwd")

    def test_patch_and_run(self):
        message = agent_core.patch_file(
            self.workspace,
            "add.py",
            "return a - b",
            "return a + b",
        )
        self.assertIn("Patched", message)
        result = agent_core.run_command(self.workspace, "python3 -c 'import add; assert add.add(2, 2) == 4'")
        self.assertTrue(result.startswith("passed"))

    def test_loop_fixes_with_a_fake_model(self):
        replies = [
            json.dumps({"action": "read", "path": "add.py"}),
            json.dumps({
                "action": "patch",
                "path": "add.py",
                "old": "return a - b",
                "new": "return a + b",
            }),
            json.dumps({"action": "run", "command": "python3 -c 'import add; assert add.add(2, 2) == 4'"}),
            json.dumps({"action": "done", "summary": "add() now sums its arguments and the check passed."}),
        ]

        def fake(prompt, session_name):
            self.assertIn("Goal:", prompt)
            return replies.pop(0)

        result = agent_core.run_agent(
            "Fix add.py so add(2, 2) is 4.",
            workspace=str(self.workspace),
            max_steps=6,
            complete_fn=fake,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["stopped"], "done")
        self.assertEqual([step["action"] for step in result["steps"]], ["read", "patch", "run", "done"])
        self.assertIn("return a + b", (self.workspace / "add.py").read_text(encoding="utf-8"))

    def test_step_cap(self):
        def always_list(prompt, session_name):
            return '{"action":"list","path":"."}'

        result = agent_core.run_agent("Look around", str(self.workspace), max_steps=2, complete_fn=always_list)
        self.assertFalse(result["success"])
        self.assertEqual(result["stopped"], "step_cap")
        self.assertEqual(len(result["steps"]), 2)

    def test_hard_cap_is_twelve(self):
        calls = {"n": 0}

        def always_list(prompt, session_name):
            calls["n"] += 1
            return '{"action":"list","path":"."}'

        result = agent_core.run_agent("Look around", str(self.workspace), max_steps=100, complete_fn=always_list)
        self.assertEqual(calls["n"], agent_core.HARD_CAP)
        self.assertEqual(len(result["steps"]), agent_core.HARD_CAP)

    def test_parses_fenced_json(self):
        action = agent_core.parse_action(textwrap.dedent("""\
            Sure.
            ```json
            {"action":"done","summary":"ok"}
            ```
        """))
        self.assertEqual(action["action"], "done")


if __name__ == "__main__":
    unittest.main()
