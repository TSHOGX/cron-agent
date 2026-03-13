import unittest

from runtime import cron_manager, process_manager


class SandboxModeTest(unittest.TestCase):
    def test_fill_defaults_uses_danger_full_access(self) -> None:
        task = cron_manager._fill_defaults(
            {
                "metadata": {"id": "t1"},
                "spec": {
                    "mode": "agent",
                    "runBackend": "cron",
                    "schedule": {"cron": "* * * * *"},
                },
            }
        )
        self.assertEqual(task["spec"]["modeConfig"]["agent"]["sandboxMode"], "danger-full-access")

    def test_validate_rejects_non_string_sandbox_mode(self) -> None:
        task = {
            "apiVersion": "cron-agent",
            "kind": "CronTask",
            "metadata": {"id": "t1"},
            "spec": {
                "mode": "agent",
                "runBackend": "cron",
                "schedule": {"cron": "* * * * *"},
                "modeConfig": {"agent": {"provider": "codex", "sandboxMode": True}},
            },
        }
        errors = cron_manager.validate_task(task)
        self.assertIn("spec.modeConfig.agent.sandboxMode must be a string when provided", errors)

    def test_build_agent_cmd_honors_sandbox_mode(self) -> None:
        self.assertEqual(
            process_manager._build_agent_cmd(
                {"provider": "codex", "sandboxMode": "danger-full-access"},
                "hello",
            ),
            ["codex", "exec", "--yolo", "hello"],
        )
        self.assertEqual(
            process_manager._build_agent_cmd(
                {"provider": "codex", "sandboxMode": "workspace-write"},
                "hello",
            ),
            ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "hello"],
        )
        self.assertEqual(
            process_manager._build_agent_cmd(
                {"provider": "claude", "sandboxMode": "danger-full-access"},
                "hello",
            ),
            ["claude", "-p", "--dangerously-skip-permissions", "hello"],
        )
        self.assertEqual(
            process_manager._build_agent_cmd(
                {"provider": "claude", "sandboxMode": "workspace-write"},
                "hello",
            ),
            ["claude", "-p", "--permission-mode", "acceptEdits", "hello"],
        )
        self.assertEqual(
            process_manager._build_agent_cmd(
                {"provider": "gemini", "sandboxMode": "danger-full-access"},
                "hello",
            ),
            ["gemini", "--approval-mode", "yolo", "-p", "hello"],
        )
        self.assertEqual(
            process_manager._build_agent_cmd(
                {"provider": "gemini", "sandboxMode": "workspace-write"},
                "hello",
            ),
            ["gemini", "-p", "hello"],
        )


if __name__ == "__main__":
    unittest.main()
