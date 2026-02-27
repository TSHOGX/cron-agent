import unittest
from unittest import mock

import cron_manager


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
        with mock.patch("cron_manager._ensure_dirs"), mock.patch("cron_manager.get_task", return_value=task), mock.patch(
            "cron_manager._load_state", return_value=mocked_state
        ), mock.patch("cron_manager._mark_task_running", return_value=False):
            ctx, err = cron_manager._prepare_run_context("t1")

        self.assertIsNone(ctx)
        self.assertIsNotNone(err)
        self.assertEqual(err.get("error_code"), "task_running")


if __name__ == "__main__":
    unittest.main()
