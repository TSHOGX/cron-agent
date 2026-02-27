import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import process_manager


class ProcessRecoveryTest(unittest.TestCase):
    def test_mark_lost_running_processes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "state.json"
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
                        "log_path": str(Path(td) / "p1.jsonl"),
                    }
                },
                "run_to_process": {"r1": "p1"},
                "task_to_active_process": {"t1": "p1"},
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")

            with mock.patch("process_manager._state_path", return_value=state_path):
                process_manager._RECOVERED = False
                process_manager._mark_lost_running_processes_once()

            updated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["processes"]["p1"]["status"], "failed")
            self.assertIn("restart", updated["processes"]["p1"]["error"])
            self.assertFalse(updated["tasks"]["t1"]["running"])
            self.assertIsNone(updated["tasks"]["t1"]["current_process_id"])


if __name__ == "__main__":
    unittest.main()
