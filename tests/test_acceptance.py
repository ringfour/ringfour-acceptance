import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ringfour_acceptance.model import Store
from ringfour_acceptance.server import Server
from ringfour_acceptance.worker import command, execute, request, scrub

IDENTITY = {
    "user": {"id": "user-a", "email": "a@example.test"},
    "selected_org": "org-a",
    "organizations": [
        {
            "id": "org-a",
            "name": "Test org",
            "robots": [{"id": "robot-a", "name": "Test robot"}],
        }
    ],
}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.registration = self.store.register(
            self.store.pairing(), "account-a", IDENTITY
        )
        self.worker = self.store.authenticate(self.registration["token"])
        self.target = {
            "worker_id": self.worker["id"],
            "user_id": "user-a",
            "org_id": "org-a",
            "robot_id": "robot-a",
        }

    def configure(self):
        self.store.configure(
            {"repository": "localhost:5000/test", "targets": [self.target]}
        )

    def test_live_requires_explicit_valid_target_and_current_identity(self):
        with self.assertRaises(ValueError):
            self.store.enqueue("live")
        for replacement in (
            {"user_id": "other"},
            {"org_id": "other"},
            {"robot_id": "other"},
        ):
            with self.assertRaises(ValueError):
                self.store.configure({"targets": [self.target | replacement]})
        self.configure()
        self.worker["identity"] = IDENTITY | {"user": {"id": "changed-login"}}
        with self.assertRaises(ValueError):
            self.store.enqueue("live")

    def test_pairing_is_one_use_and_secrets_are_not_exposed(self):
        code = self.store.pairing()
        self.store.register(code, "B", IDENTITY)
        with self.assertRaises(ValueError):
            self.store.register(code, "C", IDENTITY)
        self.assertNotIn(self.registration["token"], json.dumps(self.store.public()))

    def test_queue_is_globally_sequential_and_owned(self):
        job = self.store.enqueue("quick", self.worker["id"])[0]
        b = self.store.register(self.store.pairing(), "B", IDENTITY)
        worker_b = self.store.authenticate(b["token"])
        self.store.enqueue("quick", worker_b["id"])
        claimed = self.store.claim(self.worker)
        self.assertEqual(claimed["id"], job["id"])
        self.assertIsNone(self.store.claim(worker_b))
        with self.assertRaises(PermissionError):
            self.store.update(worker_b, job["id"], {"status": "passed"})
        self.store.update(self.worker, job["id"], {"status": "failed"})
        self.assertIsNotNone(self.store.claim(worker_b))

    def test_cancel_waits_for_worker_cleanup_and_restart_never_replays(self):
        job = self.store.enqueue("quick", self.worker["id"])[0]
        self.store.claim(self.worker)
        self.store.cancel(job["id"])
        self.assertEqual(job["status"], "stopping")
        self.assertTrue(self.store.update(self.worker, job["id"], {})["cancel"])
        restarted = Store(self.temp.name)
        self.assertEqual(restarted.jobs[job["id"]]["status"], "interrupted")
        self.assertEqual(restarted.workers, {})
        self.assertIsNone(restarted.claim(self.worker))

    def test_saved_targets_rebind_only_to_the_same_revalidated_user(self):
        self.configure()
        restarted = Store(self.temp.name)
        other = restarted.register(
            restarted.pairing(), "other", IDENTITY | {"user": {"id": "foreign"}}
        )
        self.assertEqual(restarted.setup["targets"][0]["worker_id"], self.worker["id"])
        restored = restarted.register(restarted.pairing(), "restored", IDENTITY)
        self.assertNotEqual(restored["worker_id"], other["worker_id"])
        self.assertEqual(
            restarted.setup["targets"][0]["worker_id"], restored["worker_id"]
        )
        self.assertEqual(
            json.loads((restarted.root / "setup.json").read_text())["targets"][0][
                "worker_id"
            ],
            restored["worker_id"],
        )

    def test_preflight_cannot_accidentally_become_mutating(self):
        self.configure()
        job = self.store.enqueue("preflight")[0]
        args = command(Path("/platform"), job, Path("/evidence"))
        self.assertIn("--live-preflight", args)
        self.assertNotIn("--live-repository", args)
        self.assertNotIn("shell", args)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = Server(("127.0.0.1", 0), self.temp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.registration = request(
            self.url,
            "/worker/register",
            {
                "pairing": self.server.store.pairing(),
                "label": "test",
                "identity": IDENTITY,
            },
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_browser_cookie_origin_and_worker_authorization(self):
        with self.assertRaises(HTTPError) as denied:
            urlopen(self.url + "/api/state")
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        with urlopen(self.url) as response:
            cookie = response.headers["Set-Cookie"].split(";")[0]
            self.assertIn(b"Ringfour Development", response.read())
        with urlopen(
            Request(self.url + "/api/state", headers={"Cookie": cookie})
        ) as response:
            self.assertEqual(len(json.load(response)["workers"]), 1)
        with self.assertRaises(HTTPError) as origin:
            urlopen(
                Request(
                    self.url + "/api/pair",
                    data=b"{}",
                    headers={
                        "Cookie": cookie,
                        "Content-Type": "application/json",
                        "Origin": "https://foreign.example",
                    },
                )
            )
        self.assertEqual(origin.exception.code, 403)
        origin.exception.close()
        with self.assertRaises(HTTPError):
            request(self.url, "/worker/poll", {"identity": IDENTITY}, "wrong-token")

    def test_expired_pairing_cli_explains_recovery_without_traceback(self):
        platform = Path(self.temp.name) / "pairing-platform"
        (platform / "scripts").mkdir(parents=True)
        (platform / "scripts/apps-acceptance.py").touch()
        code = self.server.store.pairing()
        self.server.store.pairings[code] = time.time() - 1
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ringfour_acceptance",
                "worker",
                "--server",
                self.url,
                "--platform",
                str(platform),
                "--pair",
                code,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Pairing code expired or already used", result.stderr)
        self.assertIn("create another in Setup", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(code, result.stderr)
        self.assertEqual(len(self.server.store.workers), 1)

    def test_real_process_progress_results_and_cancellation(self):
        # A real disposable subprocess tests the worker protocol; no Platform/robot calls.
        platform = Path(self.temp.name) / "platform"
        scripts = platform / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "apps-acceptance.py").write_text("""import argparse,json,signal,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--profile');p.add_argument('--output');a=p.parse_args()
out=Path(a.output)
def stop(*_):
 (out/'cleanup.log').write_text('cleanup confirmed')
 raise SystemExit(130)
signal.signal(signal.SIGINT,stop)
print('Running test (component)…',flush=True)
(out/'report.json').write_text(json.dumps({'selected':['test'],'suites':[{'id':'test','status':'passed'}]}))
if a.profile == 'integration':
 while True: time.sleep(.1)
print('finished',flush=True)
""")
        worker = self.server.store.authenticate(self.registration["token"])
        job = self.server.store.enqueue("quick", worker["id"])[0]
        self.server.store.claim(worker)
        execute(self.url, self.registration["token"], platform, job, threading.Event())
        self.assertEqual(job["status"], "passed")
        self.assertEqual(job["progress"]["report"]["suites"][0]["status"], "passed")
        self.assertIn("finished", job["log"])
        job = self.server.store.enqueue("integration", worker["id"])[0]
        self.server.store.claim(worker)
        thread = threading.Thread(
            target=execute,
            args=(
                self.url,
                self.registration["token"],
                platform,
                job,
                threading.Event(),
            ),
        )
        thread.start()
        deadline = time.monotonic() + 10
        while not job["progress"] and time.monotonic() < deadline:
            time.sleep(0.1)
        self.server.store.cancel(job["id"])
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(job["files"]["cleanup.log"], "cleanup confirmed")

    def test_identity_mismatch_stops_before_spawning_live_work(self):
        worker = self.server.store.authenticate(self.registration["token"])
        self.server.store.configure(
            {
                "targets": [
                    {
                        "worker_id": worker["id"],
                        "user_id": "user-a",
                        "org_id": "org-a",
                        "robot_id": "robot-a",
                    }
                ],
                "repository": "localhost/test",
            }
        )
        job = self.server.store.enqueue("live")[0]
        self.server.store.claim(worker)
        execute(
            self.url,
            self.registration["token"],
            Path(self.temp.name) / "missing-platform",
            job,
            threading.Event(),
        )
        self.assertEqual(job["status"], "failed")
        self.assertIn("authenticated identity", job["reason"])

    def test_credential_redaction(self):
        self.assertEqual(
            scrub("Authorization: Bearer abc.def.ghi"),
            "Authorization: Bearer [redacted]",
        )
        self.assertEqual(scrub("eyJabc.eyJdef.signature"), "[redacted JWT]")


if __name__ == "__main__":
    unittest.main()
