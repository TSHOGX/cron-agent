import unittest
from unittest import mock

from runtime import cron_manager


class SingleInstanceModeTest(unittest.TestCase):
    def test_fill_defaults_forces_max_concurrency_to_one(self) -> None:
        task = {
            "metadata": {"id": "t1"},
            "spec": {
                "mode": "agent",
                "runBackend": "cron",
                "schedule": {"cron": "* * * * *", "maxConcurrency": 5},
            },
        }
        out = cron_manager._fill_defaults(task)
        self.assertEqual(out["spec"]["schedule"]["maxConcurrency"], 1)

    def test_prepare_run_context_rejects_when_task_already_running(self) -> None:
        task = cron_manager._fill_defaults(
            {
                "metadata": {"id": "t1"},
                "spec": {
                    "mode": "agent",
                    "runBackend": "cron",
                    "schedule": {"cron": "* * * * *", "maxConcurrency": 9},
                    "input": {"prompt": "hello"},
                    "execution": {"timeoutSeconds": 600, "workingDirectory": "."},
                },
            }
        )
        task["_valid"] = True

        mocked_state = {"tasks": {"t1": {"running": True, "started_at": cron_manager._now_iso()}}, "runs": {}}
        with mock.patch("runtime.cron_manager._ensure_dirs"), mock.patch(
            "runtime.cron_manager.get_task", return_value=task
        ), mock.patch("runtime.cron_manager._load_state", return_value=mocked_state), mock.patch(
            "runtime.cron_manager._mark_task_running", return_value=False
        ):
            ctx, err = cron_manager._prepare_run_context("t1")

        self.assertIsNone(ctx)
        self.assertIsNotNone(err)
        self.assertEqual(err.get("error_code"), "task_running")

    def test_validate_rejects_tmux_backend(self) -> None:
        task = {
            "apiVersion": "cron-agent",
            "kind": "CronTask",
            "metadata": {"id": "t1"},
            "spec": {
                "mode": "agent",
                "runBackend": "tmux",
                "schedule": {"cron": "* * * * *"},
            },
        }
        errors = cron_manager.validate_task(task)
        self.assertIn("spec.runBackend must be cron", errors)

    def test_validate_rejects_old_api_version(self) -> None:
        task = {
            "apiVersion": "cron-agent/legacy",
            "kind": "CronTask",
            "metadata": {"id": "t1"},
            "spec": {
                "mode": "agent",
                "runBackend": "cron",
                "schedule": {"cron": "* * * * *"},
            },
        }
        errors = cron_manager.validate_task(task)
        self.assertIn("apiVersion must be cron-agent", errors)


if __name__ == "__main__":
    unittest.main()
