import unittest
from unittest import mock

from runtime import process_manager


class ProcessRecoveryTest(unittest.TestCase):
    def test_mark_lost_running_processes(self) -> None:
        state = {
            "tasks": {
                "t1": {
                    "running": True,
                    "current_run_id": "r1",
                    "current_process_id": "p1",
                }
            },
            "runs": {},
            "processes": {
                "p1": {
                    "process_id": "p1",
                    "task_id": "t1",
                    "run_id": "r1",
                    "status": "running",
                    "log_path": "/tmp/p1.jsonl",
                }
            },
            "run_to_process": {"r1": "p1"},
            "task_to_active_process": {"t1": "p1"},
        }

        saved: dict = {}

        def fake_save(data: dict) -> None:
            saved.clear()
            saved.update(data)

        with mock.patch("runtime.process_manager._load_state", return_value=state), mock.patch(
            "runtime.process_manager._save_state", side_effect=fake_save
        ):
            process_manager._RECOVERED = False
            process_manager._mark_lost_running_processes_once()

        self.assertIn("p1", saved.get("processes", {}))
        self.assertEqual(saved["processes"]["p1"]["status"], "failed")
        self.assertIn("restart", saved["processes"]["p1"]["error"])
        self.assertFalse(saved["tasks"]["t1"]["running"])
        self.assertIsNone(saved["tasks"]["t1"]["current_process_id"])


if __name__ == "__main__":
    unittest.main()
