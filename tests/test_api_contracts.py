import unittest

from api import app


class ApiContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()

    def test_process_adhoc_endpoints(self) -> None:
        r = self.client.post(
            "/api/process/start",
            json={
                "mode": "agent",
                "prompt": "smoke",
                "timeout_seconds": 5,
                "workdir": ".",
                "agent": {"provider": "definitely_missing_cli", "model": "x"},
            },
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json() or {}
        self.assertTrue(body.get("success"))
        process_id = body.get("process_id")
        self.assertIsInstance(process_id, str)

        poll = self.client.get(f"/api/process/poll/{process_id}")
        self.assertIn(poll.status_code, (200, 404))

        log = self.client.get(f"/api/process/log/{process_id}?offset=0&limit=20")
        self.assertEqual(log.status_code, 200)
        log_body = log.get_json() or {}
        self.assertIsInstance(log_body.get("items"), list)

        write = self.client.post(f"/api/process/write/{process_id}", json={"data": "x"})
        self.assertIn(write.status_code, (200, 400))

        submit = self.client.post(f"/api/process/submit/{process_id}", json={"data": "x"})
        self.assertIn(submit.status_code, (200, 400))

        kill = self.client.post(f"/api/process/kill/{process_id}", json={"signal": "TERM"})
        self.assertIn(kill.status_code, (200, 400))

    def test_task_run_async_contract_not_found(self) -> None:
        r = self.client.post("/api/tasks/task-not-exist/run")
        self.assertEqual(r.status_code, 400)
        body = r.get_json() or {}
        self.assertIn("success", body)
        self.assertIn("task_id", body)
        self.assertIn("status", body)
        self.assertIn("error", body)
        self.assertEqual(body.get("task_id"), "task-not-exist")


if __name__ == "__main__":
    unittest.main()
