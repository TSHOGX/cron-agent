import unittest

from runtime.api import app


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

    def test_scheduler_endpoints(self) -> None:
        status = self.client.get("/api/scheduler/status")
        self.assertEqual(status.status_code, 200)
        body = status.get_json() or {}
        self.assertIn("backend", body)
        self.assertEqual(body.get("backend"), "cron")

        sync = self.client.post("/api/scheduler/sync")
        self.assertIn(sync.status_code, (200, 500))
        sync_body = sync.get_json() or {}
        self.assertIn("success", sync_body)
        self.assertIn("scheduler", sync_body)

    def test_removed_endpoints_are_404(self) -> None:
        self.assertIn(self.client.post("/api/tasks/sync").status_code, (404, 405))
        self.assertEqual(self.client.get("/api/backends/status").status_code, 404)
        self.assertEqual(self.client.post("/api/backends/sync").status_code, 404)
        self.assertEqual(self.client.get("/api/runs/some-run/events").status_code, 404)


if __name__ == "__main__":
    unittest.main()
